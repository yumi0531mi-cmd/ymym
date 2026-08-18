from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

try:
    import websockets
except ImportError:  # pragma: no cover - surfaced as a safe UI fallback
    websockets = None

from .kis_client import KISClient, KISError
from .models import Market
from .sessions import ET, KST


LOGGER = logging.getLogger(__name__)

KIS_WS_PROD = "ws://ops.koreainvestment.com:21000"
KR_TRADE_TR_ID = "H0STCNT0"
US_TRADE_TR_ID = "HDFSCNT0"
KR_TRADE_FIELD_COUNT = 46
US_TRADE_FIELD_COUNT = 26


_PROCESS_HUB_LOCK = threading.RLock()
_PROCESS_HUBS: dict[str, "KISRealtimeHub"] = {}


@dataclass(frozen=True)
class RealtimeTick:
    symbol: str
    market: Market
    price: float
    change_pct: float | None
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    source: str = "KIS_WEBSOCKET"


class KISRealtimeHub:
    """Read-only KIS market-data stream for the symbols currently on screen.

    The hub owns one daemon thread while the Streamlit process is alive.  It only
    subscribes to trade data; order, balance, and account endpoints are absent.
    REST remains the safe fallback while the socket is establishing or reconnecting.
    """

    def __init__(self, client: KISClient, websocket_url: str = KIS_WS_PROD):
        self.client = client
        self.websocket_url = websocket_url
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._desired: dict[tuple[Market, str], str] = {}
        self._ticks: dict[tuple[Market, str], RealtimeTick] = {}
        self._forming_bars: dict[tuple[Market, str], dict[str, object]] = {}
        self._completed_bars: dict[tuple[Market, str], deque[dict[str, object]]] = defaultdict(lambda: deque(maxlen=360))
        self._connected = False
        self._socket_open = False
        self._last_error = ""
        self._last_message_at: datetime | None = None
        self._approval_key = ""
        self._approval_expires_monotonic = 0.0

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
        if self.connected:
            return "KIS 실시간 체결 연결됨 · 1초 화면 갱신"
        if self._socket_open:
            return "KIS 실시간 체결 수신 대기 중"
        if self.last_error:
            return "KIS 실시간 연결 재시도 중 · REST 현재가 임시 사용"
        return "KIS 실시간 체결 연결 준비 중 · REST 현재가 임시 사용"

    @staticmethod
    def reconnect_delay_after_error(current_delay: float, error: Exception) -> float:
        """Avoid hammering KIS while the same APPKEY is already used elsewhere."""
        message = str(error).upper()
        if "ALREADY IN USE" in message or "APPKEY" in message and "USE" in message:
            return max(current_delay, 30.0)
        # An approval-key 403 cannot be cured by retrying every second.  Leave the
        # REST card path enough shared request budget and retry only periodically.
        if "HTTP 403" in message or "APPROVAL" in message and "403" in message:
            return max(current_delay, 120.0)
        return current_delay

    def configure(self, symbols: Iterable[tuple[Market, str, str]]) -> None:
        """Set the visible symbols and launch the worker without blocking a UI run.

        `exchange` is kept in the signature for future exchange-specific US key
        mappings; domestic tickers are subscribed as-is.  Unsupported US symbols
        safely remain on the REST fallback until their KIS market key is resolved.
        """
        desired: dict[tuple[Market, str], str] = {}
        for market, symbol, exchange in symbols:
            clean = str(symbol).strip().upper()
            if not clean:
                continue
            desired[(market, clean)] = str(exchange).strip().upper()
        with self._lock:
            if desired == self._desired:
                return
            self._desired = desired
            self._wake.set()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._thread_main, name="kis-realtime", daemon=True)
                self._thread.start()

    def tick(self, market: Market, symbol: str) -> RealtimeTick | None:
        with self._lock:
            return self._ticks.get((market, str(symbol).strip().upper()))

    def completed_bar_rows(self, market: Market, symbol: str) -> list[dict[str, object]]:
        """Return locally completed one-minute bars derived from live KIS trades.

        The still-forming minute is deliberately excluded so entry and target logic
        never treats an unfinished candle as confirmed structure.
        """
        with self._lock:
            return list(self._completed_bars.get((market, str(symbol).strip().upper()), ()))

    def _accumulate_bar(self, tick: RealtimeTick) -> None:
        key = (tick.market, tick.symbol)
        minute = tick.timestamp.replace(second=0, microsecond=0)
        cumulative_volume = float(tick.volume or 0.0)
        current = self._forming_bars.get(key)
        if current is None or current["minute"] != minute:
            if current is not None:
                self._completed_bars[key].append(
                    {
                        "timestamp": current["minute"],
                        "open": current["open"],
                        "high": current["high"],
                        "low": current["low"],
                        "close": current["close"],
                        "volume": max(float(current["last_volume"]) - float(current["start_volume"]), 0.0),
                    }
                )
            self._forming_bars[key] = {
                "minute": minute,
                "open": tick.price,
                "high": tick.price,
                "low": tick.price,
                "close": tick.price,
                "start_volume": cumulative_volume,
                "last_volume": cumulative_volume,
            }
            return
        current["high"] = max(float(current["high"]), tick.price)
        current["low"] = min(float(current["low"]), tick.price)
        current["close"] = tick.price
        current["last_volume"] = max(float(current["last_volume"]), cumulative_volume)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _snapshot_desired(self) -> dict[tuple[Market, str], str]:
        with self._lock:
            return dict(self._desired)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # pragma: no cover - last-resort safety net
            with self._lock:
                self._connected = False
                self._last_error = type(exc).__name__

    def _approval(self) -> str:
        if self._approval_key and time.monotonic() < self._approval_expires_monotonic:
            return self._approval_key
        key = self.client.websocket_approval_key()
        self._approval_key = key
        # The official approval key is valid for the live connection.  Refresh it
        # conservatively only after 23 hours, never on every UI rerun.
        self._approval_expires_monotonic = time.monotonic() + 23 * 60 * 60
        return key

    @staticmethod
    def _us_tr_key(exchange: str, symbol: str) -> str | None:
        prefixes = {
            "NAS": "DNAS", "NASDAQ": "DNAS",
            "NYS": "DNYS", "NYSE": "DNYS",
            "AMS": "DAMS", "AMEX": "DAMS",
        }
        prefix = prefixes.get(exchange.upper())
        return f"{prefix}{symbol.upper()}" if prefix else None

    @staticmethod
    def _request(approval_key: str, tr_id: str, tr_key: str) -> str:
        return json.dumps(
            {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
            }
        )

    async def _run(self) -> None:
        if websockets is None:
            raise KISError("실시간 시세 모듈이 설치되지 않았습니다.")
        reconnect_delay = 1.0
        while not self._stop.is_set():
            desired = self._snapshot_desired()
            if not desired:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            try:
                approval = self._approval()
                # In Streamlit Cloud, websocket-client's default network route is
                # required for KIS's raw production endpoint.  Forcing a direct
                # connection produces an invalid HTTP handshake in that environment.
                async with websockets.connect(
                    self.websocket_url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=8,
                ) as ws:
                    for (market, symbol), exchange in desired.items():
                        if market == Market.KR:
                            await ws.send(self._request(approval, KR_TRADE_TR_ID, symbol))
                        elif market == Market.US:
                            tr_key = self._us_tr_key(exchange, symbol)
                            if tr_key:
                                await ws.send(self._request(approval, US_TRADE_TR_ID, tr_key))
                    with self._lock:
                        # A TCP/WebSocket open is not a live market-data connection.
                        # Confirm the status only after the first official trade tick.
                        self._connected = False
                        self._socket_open = True
                        self._last_error = ""
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if self._wake.is_set():
                                self._wake.clear()
                                break
                            continue
                        raw_text = str(raw)
                        if await self._handle_control_message(ws, raw_text):
                            continue
                        self._consume(raw_text)
            except Exception as exc:
                detail = str(exc).replace("\n", " ")
                wait_delay = self.reconnect_delay_after_error(reconnect_delay, exc)
                with self._lock:
                    self._connected = False
                    self._socket_open = False
                    self._last_error = f"{type(exc).__name__}: {detail[:120]}" if detail else type(exc).__name__
                LOGGER.warning("KIS WebSocket reconnect in %.0fs: %s", wait_delay, self._last_error)
                await asyncio.sleep(wait_delay)
                reconnect_delay = min(wait_delay * 2, 60.0)
            finally:
                with self._lock:
                    self._connected = False
                    self._socket_open = False

    async def _handle_control_message(self, ws, raw: str) -> bool:
        """Handle KIS subscription replies and its application-level PINGPONG heartbeat."""
        if raw.startswith("0|"):
            return False
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        header = payload.get("header") if isinstance(payload, dict) else {}
        body = payload.get("body") if isinstance(payload, dict) else {}
        tr_id = str((header or {}).get("tr_id") or "").upper()
        if tr_id == "PINGPONG":
            # KIS sends a JSON PINGPONG message in addition to WebSocket protocol
            # ping frames. The official sample answers it with the original payload.
            await ws.pong(raw)
            return True
        result_code = str((body or {}).get("rt_cd") or "")
        if result_code and result_code != "0":
            message = str((body or {}).get("msg1") or "KIS 구독 응답 오류")
            if "approval" in message.lower() or "인증" in message:
                self._approval_key = ""
                self._approval_expires_monotonic = 0.0
            raise KISError(f"KIS 실시간 구독 실패: {message[:120]}")
        return bool(header or body)

    def _consume(self, raw: str) -> None:
        # KIS market-data messages use 0|TR_ID|COUNT|caret-separated-fields.
        if not raw.startswith("0|"):
            return
        parts = raw.split("|", 3)
        if len(parts) != 4:
            return
        tr_id = parts[1]
        values = parts[3].split("^")
        try:
            record_count = max(int(parts[2]), 1)
        except ValueError:
            record_count = 1
        field_count = KR_TRADE_FIELD_COUNT if tr_id == KR_TRADE_TR_ID else US_TRADE_FIELD_COUNT
        # KIS may batch multiple trades in one frame. Parsing only the first
        # record leaves the dashboard stale during busy periods.
        records = [
            values[offset:offset + field_count]
            for offset in range(0, min(len(values), record_count * field_count), field_count)
            if len(values[offset:offset + field_count]) >= (14 if tr_id == KR_TRADE_TR_ID else 16)
        ]
        for record in records:
            self._consume_record(tr_id, record)

    def _consume_record(self, tr_id: str, values: list[str]) -> None:
        try:
            if tr_id == KR_TRADE_TR_ID and len(values) >= 14:
                symbol = values[0].strip().upper()
                price = float(values[2])
                change_pct = float(values[5])
                bid = float(values[11]) if values[11] else None
                ask = float(values[10]) if values[10] else None
                volume = float(values[13]) if values[13] else None
                market = Market.KR
                timestamp = datetime.now(KST)
            elif tr_id == US_TRADE_TR_ID and len(values) >= 16:
                raw_symbol = values[0].strip().upper()
                symbol = values[1].strip().upper() if values[1].strip() else (
                    raw_symbol[4:] if raw_symbol.startswith(("DNAS", "DNYS", "DAMS")) else raw_symbol
                )
                price = float(values[10])
                change_pct = float(values[13])
                # HDFSCNT0 is a trade channel; fields 14/15 are cumulative
                # volume/turnover and must never be presented as bid/ask.
                bid = None
                ask = None
                volume = float(values[14]) if values[14] else None
                market = Market.US
                timestamp = datetime.now(ET)
            else:
                return
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        tick = RealtimeTick(
            symbol=symbol,
            market=market,
            price=price,
            change_pct=change_pct,
            timestamp=timestamp,
            bid=bid,
            ask=ask,
            volume=volume,
        )
        with self._lock:
            self._ticks[(market, symbol)] = tick
            self._accumulate_bar(tick)
            self._last_message_at = tick.timestamp
            self._connected = True
            self._last_error = ""


def process_realtime_hub(client: KISClient, secret_fingerprint: str) -> KISRealtimeHub:
    """Return exactly one live hub for one Streamlit process and one KIS key set.

    Streamlit reruns the script for browser interactions and may retain resources
    across hot deploys.  A module-level registry prevents those reruns from
    opening another KIS socket for the same credentials.
    """
    identity = str(secret_fingerprint)
    with _PROCESS_HUB_LOCK:
        existing = _PROCESS_HUBS.get(identity)
        if existing is not None:
            return existing
        for old_identity, old_hub in list(_PROCESS_HUBS.items()):
            if old_identity != identity:
                old_hub.stop()
                _PROCESS_HUBS.pop(old_identity, None)
        hub = KISRealtimeHub(client)
        _PROCESS_HUBS[identity] = hub
        return hub


def reset_process_realtime_hubs() -> None:
    """Stop in-process hubs for deterministic tests and controlled resets."""
    with _PROCESS_HUB_LOCK:
        for hub in _PROCESS_HUBS.values():
            hub.stop()
        _PROCESS_HUBS.clear()
