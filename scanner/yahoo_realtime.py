from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - displayed as a safe fallback in the app
    yf = None

from .models import Market
from .realtime import RealtimeTick
from .sessions import ET, KST


LOGGER = logging.getLogger(__name__)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def choose_display_tick(kis_tick: RealtimeTick | None, yahoo_tick: RealtimeTick | None) -> RealtimeTick | None:
    """Prefer the official KIS trade tick; use Yahoo only for display fallback."""
    return kis_tick or yahoo_tick


def normalize_yahoo_bars(raw, market: Market):
    """Normalize Yahoo one-minute history into the scanner's OHLCV contract."""
    import pandas as pd

    if raw is None or getattr(raw, "empty", True):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    names = {str(column).lower(): column for column in frame.columns}
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(names):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = frame.rename(columns={names[name]: name for name in required})
    frame = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").dropna()
    index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(index, "tz", None) is None:
        index = index.tz_localize(KST if market == Market.KR else ET, ambiguous="NaT", nonexistent="shift_forward")
    frame.index = index
    return frame.loc[frame.index.notna()].sort_index()[~frame.index.duplicated(keep="last")]


def yahoo_intraday_bars(market: Market, symbol: str, exchange: str = ""):
    """Fetch a bounded 1-minute display/analysis fallback only when KIS bars are absent."""
    import pandas as pd

    if yf is None:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    for yahoo_symbol in YahooRealtimeHub.yahoo_symbols(market, symbol, exchange):
        try:
            raw = yf.Ticker(yahoo_symbol).history(period="1d", interval="1m", prepost=market == Market.US, auto_adjust=False)
            bars = normalize_yahoo_bars(raw, market)
            if len(bars) >= 31:
                return bars.tail(360)
        except Exception as exc:  # pragma: no cover - live provider route
            LOGGER.warning("Yahoo 1-minute history failed for %s: %s", yahoo_symbol, type(exc).__name__)
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


class YahooRealtimeHub:
    """Free Yahoo Finance streaming quote backup for the visible card prices.

    The hub is intentionally display-only. KIS remains the single source for
    intraday bars, orderbook checks, structural prices, and validation records.
    This separation prevents a Yahoo price from being mixed into KIS-derived
    VWAP, spread, targets, stops, or measured validation outcomes.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._desired: dict[tuple[Market, str], tuple[tuple[str, ...], str]] = {}
        self._ticks: dict[tuple[Market, str], RealtimeTick] = {}
        self._connected = False
        self._last_error = ""
        self._last_message_at: datetime | None = None
        self._enabled = os.getenv("SCANNER_ENABLE_YAHOO_STREAM", "true").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def yahoo_symbol(market: Market, symbol: str, exchange: str = "") -> str:
        clean = str(symbol).strip().upper()
        if market == Market.US:
            return clean
        # KIS candidate feeds do not always expose the KRX board. Use the explicit
        # KOSDAQ tag when available; KOSPI is the safe default for six-digit KR codes.
        suffix = ".KQ" if str(exchange).strip().upper() in {"KOSDAQ", "KQ"} else ".KS"
        return f"{clean}{suffix}"

    @classmethod
    def yahoo_symbols(cls, market: Market, symbol: str, exchange: str = "") -> tuple[str, ...]:
        """Return every Yahoo symbol that can represent a candidate safely.

        KIS ranking feeds can omit the KRX board for domestic candidates. In that
        case we subscribe to both Korean Yahoo suffixes rather than silently leaving
        the current-price card unchanged. At most five cards are visible, so this
        bounded fallback adds no broad-market polling.
        """
        if market == Market.US or str(exchange).strip():
            return (cls.yahoo_symbol(market, symbol, exchange),)
        clean = str(symbol).strip().upper()
        return (f"{clean}.KS", f"{clean}.KQ")

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def last_message_at(self) -> datetime | None:
        with self._lock:
            return self._last_message_at

    def status_label(self) -> str:
        if not self._enabled:
            return "Yahoo 보조 시세 비활성화됨"
        if self.connected:
            return "Yahoo 보조 시세 수신 중 · 카드 현재가 1초 확인"
        if yf is None:
            return "Yahoo 보조 시세 모듈 준비 중"
        return "Yahoo 보조 시세 연결 준비 중"

    def configure(self, symbols: Iterable[tuple[Market, str, str]]) -> None:
        if not self._enabled:
            return
        desired: dict[tuple[Market, str], tuple[tuple[str, ...], str]] = {}
        for market, symbol, exchange in symbols:
            clean = str(symbol).strip().upper()
            if clean:
                desired[(market, clean)] = (self.yahoo_symbols(market, clean, exchange), str(exchange).strip().upper())
        with self._lock:
            if desired == self._desired:
                return
            self._desired = desired
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._thread_main, name="yahoo-realtime", daemon=True)
                self._thread.start()

    def tick(self, market: Market, symbol: str) -> RealtimeTick | None:
        with self._lock:
            return self._ticks.get((market, str(symbol).strip().upper()))

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _snapshot(self) -> dict[tuple[Market, str], tuple[tuple[str, ...], str]]:
        with self._lock:
            return dict(self._desired)

    def _thread_main(self) -> None:
        retry_seconds = 1.0
        while not self._stop.is_set():
            desired = self._snapshot()
            if not desired:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            if yf is None:
                with self._lock:
                    self._connected = False
                    self._last_error = "yfinance 미설치"
                return
            try:
                asyncio.run(self._listen(desired))
                retry_seconds = 1.0
            except Exception as exc:  # pragma: no cover - depends on live Yahoo route
                with self._lock:
                    self._connected = False
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:100]}"
                LOGGER.warning("Yahoo WebSocket reconnect in %.0fs: %s", retry_seconds, self._last_error)
                self._stop.wait(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 20.0)

    async def _listen(self, desired: dict[tuple[Market, str], tuple[tuple[str, ...], str]]) -> None:
        assert yf is not None
        reverse = {
            yahoo_symbol: key
            for key, (yahoo_symbols, _) in desired.items()
            for yahoo_symbol in yahoo_symbols
        }

        def receive(payload: dict[str, Any]) -> None:
            self._consume(payload, reverse)

        async with yf.AsyncWebSocket(verbose=False) as websocket:
            await websocket.subscribe(list(reverse))
            with self._lock:
                self._connected = True
                self._last_error = ""
            listener = asyncio.create_task(websocket.listen(receive))
            try:
                while not self._stop.is_set():
                    await asyncio.sleep(0.25)
                    if self._wake.is_set():
                        self._wake.clear()
                        break
            finally:
                listener.cancel()
                try:
                    await listener
                except asyncio.CancelledError:
                    pass
                await websocket.close()
                with self._lock:
                    self._connected = False

    def _consume(self, payload: dict[str, Any], reverse: dict[str, tuple[Market, str]]) -> None:
        yahoo_symbol = str(payload.get("id") or "").upper()
        key = reverse.get(yahoo_symbol)
        price = _number(payload.get("price"))
        if key is None or price is None or price <= 0:
            return
        raw_time = _number(payload.get("time"))
        if raw_time is None:
            received_at = datetime.now(KST if key[0] == Market.KR else ET)
        else:
            seconds = raw_time / 1000 if raw_time > 10_000_000_000 else raw_time
            received_at = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(KST if key[0] == Market.KR else ET)
        change = _number(payload.get("change_percent") or payload.get("changePercent"))
        tick = RealtimeTick(
            symbol=key[1], market=key[0], price=price, change_pct=change,
            timestamp=received_at, bid=_number(payload.get("bid")), ask=_number(payload.get("ask")),
            volume=_number(payload.get("day_volume") or payload.get("dayVolume")), source="YAHOO_WEBSOCKET",
        )
        with self._lock:
            self._ticks[key] = tick
            self._last_message_at = received_at
