# -*- coding: utf-8 -*-
"""KIS 15-strategy intraday scanner - Streamlit app.

Version 0.5.0
- Broad candidate discovery: volume rank + trading-amount rank union
- Domestic + US DAY/PRE/REGULAR/AFTER tabs
- 15 independent strategy families with conflict separation
- 5/10/15/30 minute signed forecasts calculated independently
- KIS WebSocket live last price / bid / ask / order-book imbalance / trade strength
- Structure data remains 1-minute based; live fields are overlaid without extra REST calls

IMPORTANT:
- Rule score is NOT a win rate.
- Forecast is NOT a guaranteed future price.
- Live WebSocket execution must be verified with the user's real KIS account/session.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

try:
    import streamlit as st
except ImportError:  # allows import/static tests outside Streamlit
    st = None

try:
    import websocket
except ImportError:  # dependency is declared in requirements.txt
    websocket = None

from strategy_engine import Candle, ENGINE_VERSION, analyze

APP_VERSION = "0.5.0"
KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"
TOKEN_FILE = Path(".kis_token_cache.json")
VALIDATION_LOG = Path("validation_signals.jsonl")
KR_MAX_PRICE = 150_000.0
US_MAX_PRICE = 150.0
LIVE_FRESH_SECONDS = 5.0
BOOK_FRESH_SECONDS = 5.0
MAX_KR_SPREAD_PCT = 0.8
MAX_US_SPREAD_PCT = 1.0
UI_REFRESH_SECONDS = 0.4
AUTO_RESCAN_SECONDS = 90.0

DOMESTIC_TRADE_COLUMNS = [
    "symbol", "time", "price", "sign", "diff", "change_pct", "wavg", "open", "high", "low",
    "ask1", "bid1", "trade_volume", "volume", "amount", "sell_count", "buy_count", "net_count",
    "trade_strength", "sell_volume", "buy_volume", "trade_div", "buy_rate", "prev_volume_rate",
    "open_time", "open_sign", "open_diff", "high_time", "high_sign", "high_diff",
    "low_time", "low_sign", "low_diff", "business_date", "new_market_code", "halt_yn",
    "ask_qty1", "bid_qty1", "total_ask_qty", "total_bid_qty", "turnover",
    "prev_same_time_volume", "prev_same_time_volume_rate", "hour_cls", "market_trade_time_cls", "vi_price",
]
DOMESTIC_BOOK_COLUMNS = [
    "symbol", "time", "hour_cls",
    *[f"ask{i}" for i in range(1, 11)], *[f"bid{i}" for i in range(1, 11)],
    *[f"ask_qty{i}" for i in range(1, 11)], *[f"bid_qty{i}" for i in range(1, 11)],
    "total_ask_qty", "total_bid_qty", "overtime_total_ask_qty", "overtime_total_bid_qty",
    "anticipated_price", "anticipated_qty", "anticipated_volume", "anticipated_diff", "anticipated_sign",
    "anticipated_change_pct", "volume", "total_ask_qty_change", "total_bid_qty_change",
    "overtime_ask_change", "overtime_bid_change", "deal_cls",
    "kmid_price", "kmid_total_qty", "kmid_cls", "nmid_price", "nmid_total_qty", "nmid_cls",
]
US_TRADE_COLUMNS = [
    "symbol", "zdiv", "tymd", "xymd", "xhms", "kymd", "khms", "open", "high", "low", "price",
    "sign", "diff", "change_pct", "bid1", "ask1", "bid_qty1", "ask_qty1", "trade_volume", "volume",
    "amount", "buy_volume", "sell_volume", "trade_strength", "market_type",
]
US_BOOK_COLUMNS = [
    "symbol", "zdiv", "xymd", "xhms", "kymd", "khms", "buy_volume", "sell_volume", "buy_delta",
    "sell_delta", "bid1", "ask1", "bid_qty1", "ask_qty1", "bid_delta1", "ask_delta1",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _secret(*names: str, default: str = "") -> str:
    if st is not None:
        for name in names:
            try:
                v = st.secrets.get(name)
                if v:
                    return str(v)
            except Exception:
                pass
    for name in names:
        v = os.getenv(name)
        if v:
            return v
    return default


class KISError(RuntimeError):
    pass


class KISClient:
    def __init__(self, app_key: str, app_secret: str, timeout: float = 6.0):
        if not app_key or not app_secret:
            raise KISError("Streamlit Secrets에서 KIS APP KEY / APP SECRET을 찾지 못했습니다.")
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.session = requests.Session()
        self._token: str | None = None
        self._token_expiry = 0.0
        self._ws_key: str | None = None
        self._ws_key_expiry = 0.0
        self._request_lock = threading.RLock()
        self._last_request_at = 0.0

    def _load_cached_token(self) -> bool:
        try:
            d = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if d.get("app_key_tail") == self.app_key[-6:] and float(d.get("expiry", 0)) > time.time() + 120:
                self._token = str(d["token"])
                self._token_expiry = float(d["expiry"])
                return True
        except Exception:
            return False
        return False

    def token(self) -> str:
        if self._token and self._token_expiry > time.time() + 120:
            return self._token
        if self._load_cached_token():
            return str(self._token)
        r = self.session.post(
            BASE_URL + "/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise KISError(f"KIS 토큰 발급 실패 HTTP {r.status_code}: {r.text[:180]}")
        d = r.json()
        token = d.get("access_token")
        if not token:
            raise KISError(f"KIS 토큰 응답 오류: {d.get('msg1') or d}")
        expires = max(600, int(d.get("expires_in", 86400)))
        self._token, self._token_expiry = str(token), time.time() + expires
        try:
            TOKEN_FILE.write_text(
                json.dumps({"token": token, "expiry": self._token_expiry, "app_key_tail": self.app_key[-6:]}),
                encoding="utf-8",
            )
        except Exception:
            pass
        return str(token)

    def ws_approval_key(self) -> str:
        if self._ws_key and self._ws_key_expiry > time.time() + 120:
            return self._ws_key
        r = self.session.post(
            BASE_URL + "/oauth2/Approval",
            headers={"Content-Type": "application/json", "Accept": "text/plain", "charset": "UTF-8"},
            data=json.dumps({"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret}),
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise KISError(f"KIS WebSocket 접속키 발급 실패 HTTP {r.status_code}: {r.text[:180]}")
        d = r.json()
        key = d.get("approval_key")
        if not key:
            raise KISError(f"KIS WebSocket 접속키 응답 오류: {d}")
        self._ws_key = str(key)
        self._ws_key_expiry = time.time() + 23 * 3600
        return self._ws_key

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict:
        # Keep REST discovery/structure calls below burst limits without touching the WebSocket path.
        with self._request_lock:
            wait = 0.07 - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()
        headers = {
            "content-type": "application/json; charset=utf-8", "authorization": "Bearer " + self.token(),
            "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P",
        }
        r = self.session.get(BASE_URL + path, headers=headers, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise KISError(f"{path} HTTP {r.status_code}: {r.text[:180]}")
        d = r.json()
        if str(d.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(str(d.get("msg1") or d.get("msg_cd") or "KIS API 오류"))
        return d

    def domestic_current_price(self, symbol: str) -> float:
        d = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "UN", "FID_INPUT_ISCD": symbol},
        )
        return _num((d.get("output") or {}).get("stck_prpr"))

    def overseas_current_price(self, exchange: str, symbol: str, session: str) -> float:
        excd = exchange
        if session == "US_DAY":
            excd = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}.get(exchange, exchange)
        d = self.get(
            "/uapi/overseas-price/v1/quotations/price",
            "HHDFS00000300",
            {"AUTH": "", "EXCD": excd, "SYMB": symbol},
        )
        out = d.get("output") or {}
        return _num(out.get("last"))

    def domestic_rank(self, market_code: str, amount_mode: bool) -> list[dict]:
        p = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": market_code,
            "FID_DIV_CLS_CODE": "1", "FID_BLNG_CLS_CODE": "3" if amount_mode else "0",
            "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "111111",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
        }
        d = self.get("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", p)
        out = []
        for x in d.get("output", []) or []:
            out.append({
                "symbol": str(x.get("mksc_shrn_iscd", "")), "name": str(x.get("hts_kor_isnm", "")), "exchange": "KRX",
                "price": _num(x.get("stck_prpr")), "change_pct": _num(x.get("prdy_ctrt")), "volume": _num(x.get("acml_vol")),
                "amount": _num(x.get("acml_tr_pbmn")), "rank": int(_num(x.get("data_rank"), 9999)),
                "avg_volume": _num(x.get("avrg_vol")), "source": "amount" if amount_mode else "volume",
            })
        return out

    def overseas_rank(self, exchange: str, amount_mode: bool) -> list[dict]:
        path = "/uapi/overseas-stock/v1/ranking/trade-pbmn" if amount_mode else "/uapi/overseas-stock/v1/ranking/trade-vol"
        tr = "HHDFS76320010" if amount_mode else "HHDFS76310010"
        p = {"KEYB": "", "AUTH": "", "EXCD": exchange, "NDAY": "0", "VOL_RANG": "0", "PRC1": "", "PRC2": ""}
        d = self.get(path, tr, p)
        out = []
        for x in d.get("output2", []) or []:
            out.append({
                "symbol": str(x.get("symb", "")), "name": str(x.get("name") or x.get("ename") or x.get("symb") or ""),
                "exchange": str(x.get("excd", exchange)), "price": _num(x.get("last")), "change_pct": _num(x.get("rate")),
                "volume": _num(x.get("tvol")), "amount": _num(x.get("tamt")), "rank": int(_num(x.get("rank"), 9999)),
                "avg_volume": _num(x.get("a_tvol")), "ask": _num(x.get("pask")), "bid": _num(x.get("pbid")),
                "source": "amount" if amount_mode else "volume",
            })
        return out

    def domestic_minute_bars(self, symbol: str, count: int = 60) -> list[Candle]:
        now = datetime.now(KST).strftime("%H%M%S")
        p = {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
             "FID_INPUT_HOUR_1": now, "FID_PW_DATA_INCU_YN": "Y"}
        d = self.get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200", p)
        rows = d.get("output2", []) or []
        bars = []
        for x in reversed(rows[:count]):
            bars.append(Candle(
                str(x.get("stck_cntg_hour", "")), _num(x.get("stck_oprc")), _num(x.get("stck_hgpr")),
                _num(x.get("stck_lwpr")), _num(x.get("stck_prpr")), _num(x.get("cntg_vol") or x.get("acml_vol")),
            ))
        return [b for b in bars if b.close > 0]

    def overseas_minute_bars(self, exchange: str, symbol: str, count: int = 60) -> list[Candle]:
        p = {"AUTH": "", "EXCD": exchange, "SYMB": symbol, "NMIN": "1", "PINC": "1", "NEXT": "",
             "NREC": str(min(count, 120)), "FILL": "", "KEYB": ""}
        d = self.get("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", "HHDFS76950200", p)
        rows = d.get("output2", []) or d.get("output1", []) or d.get("output", []) or []
        bars = []
        for x in reversed(rows[:count]):
            close = _num(x.get("last") or x.get("clos") or x.get("ovrs_nmix_prpr"))
            opn = _num(x.get("open") or x.get("oprc") or close, close)
            high = _num(x.get("high") or x.get("hgpr") or close, close)
            low = _num(x.get("low") or x.get("lwpr") or close, close)
            vol = _num(x.get("tvol") or x.get("evol") or x.get("vol") or x.get("acml_vol"))
            bars.append(Candle(str(x.get("xymd") or x.get("khms") or x.get("time") or ""), opn, high, low, close, vol))
        return [b for b in bars if b.close > 0]


class KISLiveFeed:
    """One background WebSocket connection for the currently displayed top candidates."""

    def __init__(self, client: KISClient):
        self.client = client
        self._lock = threading.RLock()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._subscriptions: list[dict[str, str]] = []
        self._signature: tuple = ()
        self._ws = None
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""
        self.active_market = ""
        self.active_session = ""
        self._raw_key_to_symbol: dict[str, str] = {}
        self._reference_prices: dict[str, float] = {}

    @staticmethod
    def _us_key(exchange: str, session: str, symbol: str) -> str:
        exchange = exchange.upper()
        if session == "US_DAY":
            return "R" + {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}.get(exchange, "BAQ") + symbol
        return "D" + {"NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}.get(exchange, "NAS") + symbol

    @staticmethod
    def _packet(approval_key: str, tr_id: str, tr_key: str) -> str:
        return json.dumps({
            "header": {"approval_key": approval_key, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
            "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
        })

    def replace(self, subscriptions: list[dict[str, str]]) -> None:
        normalized = subscriptions[:10]  # 10 symbols x trade/book = 20 subscriptions
        sig = tuple(sorted((x["market"], x["session"], x["exchange"], x["symbol"]) for x in normalized))
        with self._lock:
            if sig == self._signature and self._thread and self._thread.is_alive():
                for item in normalized:
                    ref = _num(item.get("reference_price"))
                    if ref > 0:
                        self._reference_prices[item["symbol"]] = ref
                return
            self._signature = sig
            self._subscriptions = list(normalized)
            self._snapshots = {}
            self._raw_key_to_symbol = {}
            self._reference_prices = {}
            for item in normalized:
                symbol = item["symbol"]
                ref = _num(item.get("reference_price"))
                if ref > 0:
                    self._reference_prices[symbol] = ref
                if item["market"] == "US":
                    raw_key = self._us_key(item["exchange"], item["session"], symbol)
                    self._raw_key_to_symbol[raw_key] = symbol
                    self._raw_key_to_symbol[symbol] = symbol
            self.active_market = normalized[0]["market"] if normalized else ""
            self.active_session = normalized[0]["session"] if normalized else ""
            old_ws = self._ws
        if old_ws is not None:
            try:
                old_ws.close()
            except Exception:
                pass
        if not normalized:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="kis-live-feed")
        self._thread.start()

    def snapshot(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshots.get(symbol, {}))

    def status(self) -> dict[str, Any]:
        with self._lock:
            trade_times = [_num(v.get("trade_updated_at")) for v in self._snapshots.values() if _num(v.get("trade_updated_at")) > 0]
            last_trade_at = max(trade_times) if trade_times else 0.0
            return {
                "connected": self.connected, "error": self.last_error, "symbols": len(self._subscriptions),
                "market": self.active_market, "session": self.active_session,
                "last_trade_age": (time.time() - last_trade_at) if last_trade_at else None,
                "trade_symbols": sum(1 for v in self._snapshots.values() if _num(v.get("trade_updated_at")) > 0),
            }

    def _merge(self, symbol: str, values: dict[str, Any]) -> None:
        if not symbol:
            return
        clean = dict(values)
        # Never let malformed/negative order-book fields overwrite a valid quote.
        for key in ("current", "bid", "ask"):
            if key in clean and _num(clean.get(key)) <= 0:
                clean.pop(key, None)
        if "bid" in clean and "ask" in clean:
            bid, ask = _num(clean.get("bid")), _num(clean.get("ask"))
            if bid <= 0 or ask <= 0 or ask < bid:
                clean.pop("bid", None)
                clean.pop("ask", None)
        with self._lock:
            cur = self._snapshots.setdefault(symbol, {})
            cur.update(clean)
            now = time.time()
            # Keep trade freshness separate from book freshness. A fast-moving book must never
            # make an old last-trade price look fresh.
            if "current" in clean and clean.get("source") == "KIS_WS_TRADE":
                cur["trade_updated_at"] = now
                cur["trade_count"] = int(cur.get("trade_count", 0)) + 1
            if any(k in clean for k in ("bid", "ask")):
                cur["book_updated_at"] = now
                cur["book_count"] = int(cur.get("book_count", 0)) + 1
            cur["updated_at"] = max(_num(cur.get("trade_updated_at")), _num(cur.get("book_updated_at")))

    @staticmethod
    def _row(columns: list[str], payload: str) -> dict[str, str]:
        vals = payload.split("^")
        return {columns[i]: vals[i] for i in range(min(len(columns), len(vals)))}

    @staticmethod
    def _records(columns: list[str], payload: str, count: int) -> list[dict[str, str]]:
        vals = payload.split("^")
        width = len(columns)
        if width <= 0:
            return []
        n = max(1, count)
        rows = []
        for i in range(n):
            chunk = vals[i * width:(i + 1) * width]
            if not chunk:
                break
            rows.append({columns[j]: chunk[j] for j in range(min(width, len(chunk)))})
        return rows

    def _resolve_us_symbol(self, raw_symbol: str) -> str:
        raw = str(raw_symbol or "").strip().upper()
        with self._lock:
            if raw in self._raw_key_to_symbol:
                return self._raw_key_to_symbol[raw]
            # KIS normally echoes the requested DNAS/RBAQ-style key, but accept an exact ticker only
            # when it is one of the currently subscribed symbols. Never strip arbitrary characters.
            subscribed = {x["symbol"].upper() for x in self._subscriptions if x.get("market") == "US"}
        return raw if raw in subscribed else ""

    def _valid_live_price(self, symbol: str, price: float) -> bool:
        if price <= 0:
            return False
        with self._lock:
            ref = _num(self._reference_prices.get(symbol))
            prev = _num(self._snapshots.get(symbol, {}).get("current"))
        # Reference prices come from ranking/REST snapshots and can become stale.
        # They are only an absurd-frame guard; they must never freeze a valid live stream.
        if ref > 0 and abs(price / ref - 1.0) > 0.60:
            return False
        if prev > 0 and abs(price / prev - 1.0) > 0.35:
            return False
        return True

    def _handle_data(self, tr_id: str, payload: str, count: int = 1) -> None:
        if tr_id in {"H0STCNT0", "H0UNCNT0"}:
            for d in self._records(DOMESTIC_TRADE_COLUMNS, payload, count):
                symbol = str(d.get("symbol", "")).strip()
                price = _num(d.get("price"))
                if not self._valid_live_price(symbol, price):
                    continue
                self._merge(symbol, {
                    "current": price, "bid": _num(d.get("bid1")), "ask": _num(d.get("ask1")),
                    "trade_strength": _num(d.get("trade_strength"), 100.0), "trade_volume": _num(d.get("trade_volume")),
                    "volume": _num(d.get("volume")), "amount": _num(d.get("amount")), "source": "KIS_WS_TRADE",
                    "trade_time": str(d.get("time", "")), "tr_id": tr_id,
                })
        elif tr_id in {"H0STASP0", "H0UNASP0"}:
            for d in self._records(DOMESTIC_BOOK_COLUMNS, payload, count):
                symbol = d.get("symbol", "")
                aq, bq = _num(d.get("total_ask_qty")), _num(d.get("total_bid_qty"))
                bid, ask = _num(d.get("bid1")), _num(d.get("ask1"))
                imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
                snap = self.snapshot(symbol)
                current = _num(snap.get("current"))
                spread_pct = ((ask - bid) / current * 100.0) if current > 0 and ask >= bid > 0 else 999.0
                book_ok = bid > 0 and ask > 0 and ask >= bid and current > 0 and spread_pct <= MAX_KR_SPREAD_PCT
                if book_ok:
                    self._merge(symbol, {
                        "bid": bid, "ask": ask, "bid_ask_imbalance": imb,
                        "total_bid_qty": bq, "total_ask_qty": aq, "source_book": "KIS_WS_BOOK",
                        "book_time": str(d.get("time", "")), "book_tr_id": tr_id,
                        "book_valid": True, "book_spread_pct": spread_pct,
                    })
                else:
                    with self._lock:
                        cur = self._snapshots.setdefault(symbol, {})
                        cur["book_valid"] = False
                        cur["book_reject_reason"] = f"bid={bid} ask={ask} current={current} spread={spread_pct:.3f}%"
                        cur["book_rejected_at"] = time.time()
        elif tr_id == "HDFSCNT0":
            for d in self._records(US_TRADE_COLUMNS, payload, count):
                raw_symbol = str(d.get("symbol", "")).strip().upper()
                symbol = self._resolve_us_symbol(raw_symbol)
                price = _num(d.get("price"))
                if not symbol or not self._valid_live_price(symbol, price):
                    continue
                buy_vol = _num(d.get("buy_volume"))
                sell_vol = _num(d.get("sell_volume"))
                # KIS HDFSCNT0 PBID/PASK are present in the trade frame, but we deliberately
                # do NOT use them as our displayed top-of-book. Only HDFSASP0 may update bid/ask.
                # This prevents a malformed/misaligned trade frame from contaminating the book.
                derived_strength = 100.0
                if buy_vol > 0 and sell_vol > 0:
                    derived_strength = max(20.0, min(300.0, 100.0 * buy_vol / sell_vol))
                self._merge(symbol, {
                    "current": price,
                    "trade_strength": derived_strength,
                    "trade_volume": _num(d.get("trade_volume")), "volume": _num(d.get("volume")),
                    "amount": _num(d.get("amount")), "buy_volume": buy_vol, "sell_volume": sell_vol,
                    "source": "KIS_WS_TRADE",
                    "trade_time": str(d.get("xhms", "")), "tr_id": tr_id, "raw_symbol": raw_symbol,
                })
        elif tr_id == "HDFSASP0":
            for d in self._records(US_BOOK_COLUMNS, payload, count):
                raw_symbol = str(d.get("symbol", "")).strip().upper()
                symbol = self._resolve_us_symbol(raw_symbol)
                if not symbol:
                    continue
                bid = _num(d.get("bid1"))
                ask = _num(d.get("ask1"))
                bq, aq = _num(d.get("bid_qty1")), _num(d.get("ask_qty1"))
                imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
                snap = self.snapshot(symbol)
                current = _num(snap.get("current"))
                spread_pct = ((ask - bid) / current * 100.0) if current > 0 and ask >= bid > 0 else 999.0
                # Reject impossible, crossed, stale-session-looking or too-wide US books.
                # A rejected book is not allowed to affect strategy calculations or UI.
                book_ok = (
                    bid > 0 and ask > 0 and ask >= bid and current > 0
                    and spread_pct <= MAX_US_SPREAD_PCT
                    and bid >= current * 0.97 and ask <= current * 1.03
                )
                if book_ok:
                    self._merge(symbol, {
                        "bid": bid, "ask": ask, "bid_ask_imbalance": imb,
                        "total_bid_qty": bq, "total_ask_qty": aq, "source_book": "KIS_WS_BOOK",
                        "book_time": str(d.get("xhms", "")), "book_tr_id": tr_id,
                        "raw_symbol_book": raw_symbol, "book_valid": True, "book_spread_pct": spread_pct,
                    })
                else:
                    with self._lock:
                        cur = self._snapshots.setdefault(symbol, {})
                        cur["book_valid"] = False
                        cur["book_reject_reason"] = f"bid={bid} ask={ask} current={current} spread={spread_pct:.3f}%"
                        cur["book_rejected_at"] = time.time()

    def _run(self) -> None:
        if websocket is None:
            with self._lock:
                self.last_error = "websocket-client 미설치"
                self.connected = False
            return
        try:
            approval = self.client.ws_approval_key()
        except Exception as e:
            with self._lock:
                self.last_error = str(e)
                self.connected = False
            return

        def on_open(ws):
            with self._lock:
                self.connected = True
                self.last_error = ""
                subs = list(self._subscriptions)
            for item in subs:
                if item["market"] == "KR":
                    pairs = [("H0UNCNT0", item["symbol"]), ("H0UNASP0", item["symbol"])]
                else:
                    key = self._us_key(item["exchange"], item["session"], item["symbol"])
                    pairs = [("HDFSCNT0", key), ("HDFSASP0", key)]
                for tr_id, tr_key in pairs:
                    ws.send(self._packet(approval, tr_id, tr_key))
                    time.sleep(0.08)

        def on_message(ws, message):
            try:
                if not message:
                    return
                if message[0] in ("0", "1"):
                    parts = message.split("|", 3)
                    if len(parts) >= 4:
                        try:
                            count = int(parts[2])
                        except Exception:
                            count = 1
                        self._handle_data(parts[1], parts[3], count)
                    return
                d = json.loads(message)
                if d.get("header", {}).get("tr_id") == "PINGPONG":
                    ws.send(message, opcode=websocket.ABNF.OPCODE_PONG)
            except Exception as e:
                with self._lock:
                    self.last_error = f"WS 파싱: {e}"

        def on_error(_ws, error):
            with self._lock:
                self.last_error = str(error)

        def on_close(_ws, _code, _msg):
            with self._lock:
                self.connected = False

        app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        with self._lock:
            self._ws = app
        try:
            app.run_forever(ping_interval=25, ping_timeout=10)
        finally:
            with self._lock:
                self.connected = False
                if self._ws is app:
                    self._ws = None




def _valid_candidate_for_market(r: dict, market: str) -> bool:
    symbol = str(r.get("symbol", "")).strip().upper()
    exchange = str(r.get("exchange", "")).strip().upper()
    price = _num(r.get("price"))
    if market == "KR":
        return len(symbol) == 6 and symbol.isdigit() and exchange == "KRX" and 0 < price <= KR_MAX_PRICE
    if exchange not in {"NAS", "NYS", "AMS"}:
        return False
    if not symbol or symbol.isdigit():
        return False
    # User's execution constraint: US candidates above $150 are excluded before strategy analysis.
    return 0 < price <= US_MAX_PRICE

def merge_candidates(rows: list[dict], max_candidates: int = 200) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.get("exchange", ""), r.get("symbol", ""))
        if not key[1]:
            continue
        if key not in merged:
            merged[key] = dict(r)
            merged[key]["sources"] = set()
        cur = merged[key]
        cur["sources"].add(r.get("source", ""))
        for k in ("volume", "amount", "avg_volume", "ask", "bid", "price"):
            if _num(r.get(k)) > _num(cur.get(k)):
                cur[k] = r.get(k)
        cur["rank"] = min(int(cur.get("rank", 9999)), int(r.get("rank", 9999)))
    out = []
    for r in merged.values():
        r["sources"] = "+".join(sorted(x for x in r["sources"] if x))
        price, bid, ask = _num(r.get("price")), _num(r.get("bid")), _num(r.get("ask"))
        r["spread_pct"] = ((ask - bid) / price * 100) if price > 0 and ask > 0 and bid > 0 and ask >= bid else 0.0
        avg, vol = _num(r.get("avg_volume")), _num(r.get("volume"))
        r["rvol_hint"] = vol / avg if avg > 0 else 1.0
        # Discovery only; not strategy confidence.
        r["discovery_score"] = (
            min(45.0, max(0.0, 50.0 - min(int(r.get("rank", 9999)), 50)))
            + min(25.0, max(0.0, r["rvol_hint"] - 1.0) * 12.0)
            + (8.0 if "amount" in r["sources"] and "volume" in r["sources"] else 0.0)
            - min(20.0, r["spread_pct"] * 25.0)
        )
        out.append(r)
    out.sort(key=lambda x: (x["discovery_score"], _num(x.get("amount")), _num(x.get("volume"))), reverse=True)
    return out[:max_candidates]


def discover(client: KISClient, market: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    if market == "KR":
        for code in ("0001", "1001"):
            for amount in (False, True):
                try:
                    rows.extend(client.domestic_rank(code, amount))
                except Exception as e:
                    errors.append(f"KR {code} {'대금' if amount else '거래량'}: {e}")
    else:
        for excd in ("NAS", "NYS", "AMS"):
            for amount in (False, True):
                try:
                    rows.extend(client.overseas_rank(excd, amount))
                except Exception as e:
                    errors.append(f"US {excd} {'대금' if amount else '거래량'}: {e}")
    merged = merge_candidates(rows, 200)
    filtered = [x for x in merged if _valid_candidate_for_market(x, market)]
    return filtered[:200], errors


def shortlist(candidates: list[dict], n: int = 18, market: str = "KR") -> list[dict]:
    liquid = [
        x for x in candidates
        if _valid_candidate_for_market(x, market)
        and (_num(x.get("spread_pct")) <= 0.8 or _num(x.get("spread_pct")) == 0)
    ]
    return liquid[:n]


def quote_context(c: dict, live: dict | None = None) -> dict:
    live = live or {}
    price = _num(live.get("current"), _num(c.get("price")))
    bid = _num(live.get("bid"), _num(c.get("bid")))
    ask = _num(live.get("ask"), _num(c.get("ask")))
    spread = ((ask - bid) / price * 100) if price > 0 and ask > 0 and bid > 0 and ask >= bid else _num(c.get("spread_pct"))
    return {
        "current": price,
        "spread_pct": spread,
        "relative_strength": max(-2.0, min(2.0, _num(c.get("change_pct")) / 2.0)),
        "rvol": _num(c.get("rvol_hint"), 1.0),
        "bid_ask_imbalance": _num(live.get("bid_ask_imbalance"), 0.0),
        "trade_strength": _num(live.get("trade_strength"), 100.0),
        "event_score": 0.0,
    }


def save_signal(result: dict) -> None:
    payload = {"logged_at": datetime.now(timezone.utc).isoformat(), **result}
    try:
        with VALIDATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _verify_reference_price(client: KISClient, c: dict, market: str, session_name: str) -> float:
    """One REST cross-check used only as a sanity reference for the live WebSocket parser."""
    try:
        if market == "KR":
            p = client.domestic_current_price(c["symbol"])
        else:
            p = client.overseas_current_price(c["exchange"], c["symbol"], session_name)
        return p if p > 0 else _num(c.get("price"))
    except Exception:
        return _num(c.get("price"))

def run_scan(client: KISClient, market: str, session_name: str, detail_count: int = 18):
    candidates, errors = discover(client, market)
    results: list[dict] = []
    models: dict[str, dict[str, Any]] = {}
    for c in shortlist(candidates, detail_count, market):
        try:
            c = dict(c)
            c["reference_price"] = _verify_reference_price(client, c, market, session_name)
            bars = client.domestic_minute_bars(c["symbol"], 60) if market == "KR" else client.overseas_minute_bars(c["exchange"], c["symbol"], 60)
            if len(bars) < 8:
                errors.append(f"{c['symbol']}: 분봉 부족({len(bars)})")
                continue
            result = analyze(c["symbol"], c["name"], market, session_name, bars, quote_context(c), "OK")
            d = result.to_dict()
            d["exchange"] = c["exchange"]
            results.append(d)
            models[c["symbol"]] = {"candidate": c, "bars": bars, "bars_updated_at": time.time()}
        except Exception as e:
            errors.append(f"{c.get('symbol')}: {e}")
        time.sleep(0.06)
    results.sort(key=lambda x: (x["decision"] == "🟢 진입", x["uncalibrated_score"]), reverse=True)
    return candidates, results, errors, models


def _fmt_price(v: float, market: str) -> str:
    if _num(v) <= 0:
        return "계산 대기"
    return f"{v:,.0f}" if market == "KR" else f"${v:,.2f}"


def _forecast_text(f: dict, market: str) -> tuple[str, str]:
    arrow = "↑" if f["direction"] == "UP" else ("↓" if f["direction"] == "DOWN" else "→")
    price = _fmt_price(f["center_price"], market)
    return f"{f['center_pct']:+.2f}% {arrow}", f"{price} · 범위 {f['low_pct']:+.2f}~{f['high_pct']:+.2f}%"


def _refresh_one_structure(client: KISClient, payload: dict, market: str) -> None:
    """Refresh at most one candidate's 1m structure per fragment run.

    This separates the fast WebSocket path from slower structural REST reads and
    avoids recomputing every symbol on every tick.
    """
    models = payload.get("models", {})
    if not models:
        return
    now = time.time()
    due = sorted(
        ((sym, model) for sym, model in models.items() if now - _num(model.get("bars_updated_at"), 0.0) >= 55.0),
        key=lambda item: _num(item[1].get("bars_updated_at"), 0.0),
    )
    if not due:
        return
    symbol, model = due[0]
    c = model.get("candidate", {})
    try:
        bars = client.domestic_minute_bars(symbol, 60) if market == "KR" else client.overseas_minute_bars(c.get("exchange", "NAS"), symbol, 60)
        if len(bars) >= 8:
            model["bars"] = bars
        model["bars_updated_at"] = now
    except Exception as e:
        model["bars_updated_at"] = now
        payload.setdefault("errors", []).append(f"{symbol} 1분 구조 갱신: {e}")


def _metric_cell(col, label: str, value: str, caption: str = "") -> None:
    with col:
        st.metric(label, value)
        if caption:
            st.caption(caption)


def render():
    if st is None:
        raise RuntimeError("Streamlit이 설치되어 있지 않습니다.")

    st.set_page_config(page_title="KIS 15전략 단타 스캐너", layout="wide")
    st.markdown("""<style>
    .block-container{padding-top:.7rem;max-width:1600px}
    div[data-testid="stMetric"]{border:1px solid rgba(128,128,128,.22);border-radius:10px;padding:.45rem .55rem;background:rgba(128,128,128,.035)}
    div[data-testid="stMetricLabel"]{font-size:.76rem}
    div[data-testid="stMetricValue"]{font-size:1.12rem}
    .scanner-title{font-size:1.45rem;font-weight:800;margin-bottom:.1rem}
    .scanner-sub{font-size:.78rem;opacity:.72;margin-bottom:.45rem}
    .section-title{font-size:.78rem;font-weight:800;opacity:.72;margin:.28rem 0 .18rem}
    </style>""", unsafe_allow_html=True)

    st.markdown('<div class="scanner-title">KIS 15전략 단타 스캐너</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="scanner-sub">APP {APP_VERSION} · ENGINE {ENGINE_VERSION} · 국내 ≤ 150,000원 · 미국 ≤ $150 · 현재가=KIS WebSocket 체결가</div>',
        unsafe_allow_html=True,
    )

    key = _secret("KIS_APP_KEY", "APP_KEY", "appkey", "KIS_APPKEY")
    secret = _secret("KIS_APP_SECRET", "APP_SECRET", "appsecret", "KIS_APPSECRET")
    if not key or not secret:
        st.error("Streamlit Secrets에서 KIS APP KEY / APP SECRET을 찾지 못했습니다.")
        st.stop()

    @st.cache_resource
    def make_client(app_key: str, app_secret: str) -> KISClient:
        return KISClient(app_key, app_secret)

    @st.cache_resource
    def make_feed(app_key: str, app_secret: str) -> KISLiveFeed:
        return KISLiveFeed(KISClient(app_key, app_secret))

    client = make_client(key, secret)
    feed = make_feed(key, secret)

    session_label = st.radio(
        "시장/세션",
        ["🇰🇷 국내", "🇺🇸 데이", "🇺🇸 프리", "🇺🇸 정규", "🇺🇸 애프터"],
        horizontal=True,
        label_visibility="collapsed",
    )
    mapping = {
        "🇰🇷 국내": ("KR", "KR_REGULAR"),
        "🇺🇸 데이": ("US", "US_DAY"),
        "🇺🇸 프리": ("US", "US_PRE"),
        "🇺🇸 정규": ("US", "US_REGULAR"),
        "🇺🇸 애프터": ("US", "US_AFTER"),
    }
    market, sess = mapping[session_label]
    state_key = f"scan_state_{market}_{sess}"

    top1, top2, top3 = st.columns([1.25, 1.0, 3.0])
    detail = top1.select_slider("정밀분석", options=[8, 10, 12, 15, 18, 20, 24], value=18)
    scan_clicked = top2.button("지금 스캔", type="primary", use_container_width=True)
    top3.caption("거래량 TOP ∪ 거래대금 TOP → 가격/유동성 필터 → 15전략 → 구조가격/시간별 전망 · 후보풀 90초 자동 재검색")

    if scan_clicked:
        with st.spinner("KIS 후보검색 + 1분봉 전략 계산 중..."):
            cand, res, errs, models = run_scan(client, market, sess, detail)
        st.session_state[state_key] = {
            "candidates": cand, "results": res, "errors": errs, "models": models,
            "last_scan_at": time.time(), "detail_count": detail, "live_logged": set(),
        }
        subscriptions = [
            {
                "market": market, "session": sess,
                "exchange": r.get("exchange", "KRX"), "symbol": r["symbol"],
                "reference_price": _num(models.get(r["symbol"], {}).get("candidate", {}).get("reference_price"), _num(r.get("current"))),
            }
            for r in res[:10]
        ]
        feed.replace(subscriptions)
        st.toast(f"후보 {len(cand)} · 정밀분석 {len(res)} · 실시간 감시 {min(10, len(res))}")

    def live_panel():
        payload = st.session_state.get(state_key)
        if not payload:
            st.info("`지금 스캔`을 누르면 후보를 고른 뒤 상위 종목의 현재가를 WebSocket으로 계속 갱신합니다.")
            return

        status = feed.status()
        live_context_ok = status.get("market") == market and status.get("session") == sess
        if not live_context_ok:
            st.warning("이 세션은 아직 실시간 구독되지 않았습니다. `지금 스캔`을 눌러 연결하세요.")
            return

        if time.time() - _num(payload.get("last_scan_at"), 0.0) >= AUTO_RESCAN_SECONDS:
            try:
                cand, res, errs, models = run_scan(client, market, sess, int(payload.get("detail_count", detail)))
                payload.update({"candidates": cand, "results": res, "errors": errs, "models": models, "last_scan_at": time.time()})
                feed.replace([
                    {
                        "market": market, "session": sess,
                        "exchange": r.get("exchange", "KRX"), "symbol": r["symbol"],
                        "reference_price": _num(models.get(r["symbol"], {}).get("candidate", {}).get("reference_price"), _num(r.get("current"))),
                    }
                    for r in res[:10]
                ])
                status = feed.status()
            except Exception as exc:
                payload.setdefault("errors", []).append(f"자동 후보 재검색: {exc}")
                payload["last_scan_at"] = time.time()

        status_cols = st.columns([1.2, 1.0, 1.0, 3.2])
        status_cols[0].metric("WebSocket", "연결" if status.get("connected") else "대기")
        status_cols[1].metric("실시간 종목", f"{status.get('trade_symbols', 0)}/{status.get('symbols', 0)}")
        age = status.get("last_trade_age")
        status_cols[2].metric("최근 체결", f"{age:.1f}초 전" if isinstance(age, (int, float)) else "수신 대기")
        status_cols[3].caption(status.get("error") or "후보 확정 후 현재가는 스캔과 분리되어 WebSocket 체결 때마다 갱신됩니다.")

        _refresh_one_structure(client, payload, market)
        displayed: list[dict] = []
        now_ts = time.time()
        for base in payload.get("results", [])[:10]:
            symbol = base["symbol"]
            model = payload.get("models", {}).get(symbol)
            live = feed.snapshot(symbol)
            if not model:
                continue
            trade_age = now_ts - _num(live.get("trade_updated_at"), 0.0) if _num(live.get("trade_updated_at")) else 999.0
            book_age = now_ts - _num(live.get("book_updated_at"), 0.0) if _num(live.get("book_updated_at")) else 999.0
            live_ok = _num(live.get("current")) > 0 and trade_age <= LIVE_FRESH_SECONDS and live.get("source") == "KIS_WS_TRADE"
            book_ok = bool(live.get("book_valid") and _num(live.get("bid")) > 0 and _num(live.get("ask")) > 0 and book_age <= BOOK_FRESH_SECONDS)
            q = quote_context(model["candidate"], live if live_ok else {})
            if not book_ok:
                q["bid_ask_imbalance"] = 0.0
                q["spread_pct"] = 0.0
            try:
                rr = analyze(symbol, base["name"], market, sess, model["bars"], q, "OK" if live_ok else "STALE_LIVE").to_dict()
            except Exception as exc:
                payload.setdefault("errors", []).append(f"{symbol} 실시간 재계산: {exc}")
                continue
            rr["exchange"] = base.get("exchange", model["candidate"].get("exchange", ""))
            rr["live"] = live
            rr["live_verified"] = live_ok
            rr["book_verified"] = book_ok
            if not live_ok:
                rr["decision"] = "🟡 실시간 대기"
            elif (market == "KR" and _num(live.get("current")) > KR_MAX_PRICE) or (market == "US" and _num(live.get("current")) > US_MAX_PRICE):
                rr["decision"] = "🔴 가격제외"
            displayed.append(rr)

        buys = [r for r in displayed if r["decision"] == "🟢 진입" and r.get("live_verified")]
        st.markdown(f"**🟢 지금 진입 {len(buys)}개 · 후보 {len(displayed)}개**")

        for r in displayed:
            live = r["live"]
            trade_age = now_ts - _num(live.get("trade_updated_at"), 0.0) if _num(live.get("trade_updated_at")) else 999.0
            book_age = now_ts - _num(live.get("book_updated_at"), 0.0) if _num(live.get("book_updated_at")) else 999.0
            live_ok = bool(r.get("live_verified"))
            book_ok = bool(r.get("book_verified"))
            current = _num(live.get("current")) if live_ok else 0.0

            with st.container(border=True):
                head_a, head_b, head_c = st.columns([4.4, 1.5, 2.1])
                head_a.markdown(f"### {r['decision']}  {r['name']}  `{r['symbol']}`")
                head_a.caption(f"{r['primary_strategy']} · {r['regime']} · 보정 전 점수 {r['uncalibrated_score']:.1f}")
                head_b.metric("실시간 현재가", _fmt_price(current, market) if live_ok else "수신 대기")
                head_c.caption(
                    f"체결 {trade_age:.1f}초 전 · {live.get('trade_time','-')} · 수신 {int(live.get('trade_count',0))}회"
                    if live_ok else "WebSocket 체결가가 들어오기 전에는 진입 확정 안 함"
                )

                st.markdown('<div class="section-title">가격 · 호가 · 매매계획</div>', unsafe_allow_html=True)
                cols = st.columns(7)
                values = [
                    ("매수1", _fmt_price(_num(live.get("bid")), market) if book_ok else "확인 중"),
                    ("매도1", _fmt_price(_num(live.get("ask")), market) if book_ok else "확인 중"),
                    ("진입", f"{_fmt_price(r['entry_low'], market)}~{_fmt_price(r['entry_high'], market)}"),
                    ("1차 목표", _fmt_price(r["target1"], market)),
                    ("2차 목표", _fmt_price(r["target2"], market)),
                    ("Soft Stop", _fmt_price(r["soft_stop"], market)),
                    ("Hard Stop", _fmt_price(r["hard_stop"], market)),
                ]
                for col, (label, value) in zip(cols, values):
                    col.metric(label, value)

                st.markdown('<div class="section-title">5 · 10 · 15 · 30분 독립 계산</div>', unsafe_allow_html=True)
                fc = {x["minutes"]: x for x in r["forecasts"]}
                fcols = st.columns(4)
                for col, m in zip(fcols, (5, 10, 15, 30)):
                    main, sub = _forecast_text(fc[m], market)
                    col.metric(f"{m}분", main)
                    col.caption(sub)

                tech = r.get("features", {})
                t1, t2, t3, t4, t5 = st.columns(5)
                t1.caption(f"VWAP {_fmt_price(_num(tech.get('vwap')), market)}")
                t2.caption(f"EMA20 {_fmt_price(_num(tech.get('ema20')), market)}")
                t3.caption(f"RVOL {_num(tech.get('rvol'),1):.2f}")
                t4.caption(f"체결강도 {_num(live.get('trade_strength'),100):.0f}")
                t5.caption("근거: " + str(tech.get("plan_basis", "계산 대기")))

        if payload.get("errors"):
            with st.expander(f"데이터/API 경고 {len(payload['errors'])}건"):
                st.write("\n".join(payload["errors"][-20:]))

    st.fragment(run_every=UI_REFRESH_SECONDS)(live_panel)()


if __name__ == "__main__":
    render()
