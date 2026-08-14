# -*- coding: utf-8 -*-
"""KIS 15-strategy intraday scanner - Streamlit app.

Version 0.2.0
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

APP_VERSION = "0.2.0"
KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"
TOKEN_FILE = Path(".kis_token_cache.json")
VALIDATION_LOG = Path("validation_signals.jsonl")

DOMESTIC_TRADE_COLUMNS = [
    "symbol", "time", "price", "sign", "diff", "change_pct", "wavg", "open", "high", "low",
    "ask1", "bid1", "trade_volume", "volume", "amount", "sell_count", "buy_count", "net_count",
    "trade_strength", "sell_volume", "buy_volume", "trade_div", "buy_rate", "prev_volume_rate",
]
DOMESTIC_BOOK_COLUMNS = [
    "symbol", "time", "hour_cls",
    *[f"ask{i}" for i in range(1, 11)], *[f"bid{i}" for i in range(1, 11)],
    *[f"ask_qty{i}" for i in range(1, 11)], *[f"bid_qty{i}" for i in range(1, 11)],
    "total_ask_qty", "total_bid_qty",
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
                return
            self._signature = sig
            self._subscriptions = list(normalized)
            self._snapshots = {}
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
            return {
                "connected": self.connected, "error": self.last_error, "symbols": len(self._subscriptions),
                "market": self.active_market, "session": self.active_session,
            }

    def _merge(self, symbol: str, values: dict[str, Any]) -> None:
        if not symbol:
            return
        with self._lock:
            cur = self._snapshots.setdefault(symbol, {})
            cur.update(values)
            cur["updated_at"] = time.time()

    @staticmethod
    def _row(columns: list[str], payload: str) -> dict[str, str]:
        vals = payload.split("^")
        return {columns[i]: vals[i] for i in range(min(len(columns), len(vals)))}

    def _handle_data(self, tr_id: str, payload: str) -> None:
        if tr_id == "H0STCNT0":
            d = self._row(DOMESTIC_TRADE_COLUMNS, payload)
            self._merge(d.get("symbol", ""), {
                "current": _num(d.get("price")), "bid": _num(d.get("bid1")), "ask": _num(d.get("ask1")),
                "trade_strength": _num(d.get("trade_strength"), 100.0), "trade_volume": _num(d.get("trade_volume")),
                "volume": _num(d.get("volume")), "amount": _num(d.get("amount")), "source": "KIS_WS_TRADE",
            })
        elif tr_id == "H0STASP0":
            d = self._row(DOMESTIC_BOOK_COLUMNS, payload)
            aq, bq = _num(d.get("total_ask_qty")), _num(d.get("total_bid_qty"))
            imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
            self._merge(d.get("symbol", ""), {
                "bid": _num(d.get("bid1")), "ask": _num(d.get("ask1")), "bid_ask_imbalance": imb,
                "total_bid_qty": bq, "total_ask_qty": aq, "source_book": "KIS_WS_BOOK",
            })
        elif tr_id == "HDFSCNT0":
            d = self._row(US_TRADE_COLUMNS, payload)
            raw_symbol = d.get("symbol", "")
            symbol = raw_symbol[4:] if len(raw_symbol) > 4 and raw_symbol[0] in "DR" else raw_symbol
            bq, aq = _num(d.get("bid_qty1")), _num(d.get("ask_qty1"))
            imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
            self._merge(symbol, {
                "current": _num(d.get("price")), "bid": _num(d.get("bid1")), "ask": _num(d.get("ask1")),
                "trade_strength": _num(d.get("trade_strength"), 100.0), "bid_ask_imbalance": imb,
                "trade_volume": _num(d.get("trade_volume")), "volume": _num(d.get("volume")),
                "amount": _num(d.get("amount")), "source": "KIS_WS_TRADE",
            })
        elif tr_id == "HDFSASP0":
            d = self._row(US_BOOK_COLUMNS, payload)
            raw_symbol = d.get("symbol", "")
            symbol = raw_symbol[4:] if len(raw_symbol) > 4 and raw_symbol[0] in "DR" else raw_symbol
            bq, aq = _num(d.get("bid_qty1")), _num(d.get("ask_qty1"))
            imb = (bq - aq) / (bq + aq) if bq + aq > 0 else 0.0
            self._merge(symbol, {
                "bid": _num(d.get("bid1")), "ask": _num(d.get("ask1")), "bid_ask_imbalance": imb,
                "total_bid_qty": bq, "total_ask_qty": aq, "source_book": "KIS_WS_BOOK",
            })

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
                    pairs = [("H0STCNT0", item["symbol"]), ("H0STASP0", item["symbol"])]
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
                        self._handle_data(parts[1], parts[3])
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
    return merge_candidates(rows, 200), errors


def shortlist(candidates: list[dict], n: int = 18) -> list[dict]:
    liquid = [x for x in candidates if _num(x.get("price")) > 0 and (_num(x.get("spread_pct")) <= 0.8 or _num(x.get("spread_pct")) == 0)]
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


def run_scan(client: KISClient, market: str, session_name: str, detail_count: int = 18):
    candidates, errors = discover(client, market)
    results: list[dict] = []
    models: dict[str, dict[str, Any]] = {}
    for c in shortlist(candidates, detail_count):
        try:
            bars = client.domestic_minute_bars(c["symbol"], 60) if market == "KR" else client.overseas_minute_bars(c["exchange"], c["symbol"], 60)
            if len(bars) < 8:
                errors.append(f"{c['symbol']}: 분봉 부족({len(bars)})")
                continue
            result = analyze(c["symbol"], c["name"], market, session_name, bars, quote_context(c), "OK")
            d = result.to_dict()
            d["exchange"] = c["exchange"]
            results.append(d)
            models[c["symbol"]] = {"candidate": c, "bars": bars, "bars_updated_at": time.time()}
            save_signal(d)
        except Exception as e:
            errors.append(f"{c.get('symbol')}: {e}")
        time.sleep(0.06)
    results.sort(key=lambda x: (x["decision"] == "🟢 진입", x["uncalibrated_score"]), reverse=True)
    return candidates, results, errors, models


def _fmt_price(v: float, market: str) -> str:
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
        with st.container(border=True):
            st.metric(label, value)
            if caption:
                st.caption(caption)


def render():
    if st is None:
        raise RuntimeError("Streamlit이 설치되어 있지 않습니다. Streamlit Community Cloud에서 실행하세요.")
    st.set_page_config(page_title="KIS 15전략 단타 스캐너", layout="wide")
    st.title("KIS 15전략 단타 스캐너")
    st.caption(f"APP {APP_VERSION} · ENGINE {ENGINE_VERSION} · 실시간 현재가는 KIS WebSocket · 점수는 보정 전 규칙점수")

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

    def live_panel(state_key: str, market: str, sess: str):
        payload = st.session_state.get(state_key)
        if not payload:
            st.info("`지금 스캔`을 누르면 후보를 계산하고 상위 종목의 KIS 실시간 시세를 연결합니다.")
            return
        status = feed.status()
        live_context_ok = status.get("market") == market and status.get("session") == sess
        if status["connected"] and live_context_ok:
            st.success(f"● KIS WebSocket 실시간 연결 · {status['symbols']}종목 감시")
        elif status["connected"]:
            st.info("다른 시장/세션이 현재 실시간 감시 중입니다. 이 탭에서 `지금 스캔`을 누르면 이 세션으로 전환됩니다.")
        else:
            st.warning(f"○ 실시간 연결 대기/끊김 · {status['error'] or '접속 중'}")

        _refresh_one_structure(client, payload, market)
        displayed = []
        for base in payload["results"][:10]:
            symbol = base["symbol"]
            model = payload["models"].get(symbol)
            live = feed.snapshot(symbol) if live_context_ok else {}
            if model:
                try:
                    fresh = bool(live) and time.time() - _num(live.get("updated_at"), 0.0) <= 10
                    q = quote_context(model["candidate"], live if fresh else {})
                    rr = analyze(symbol, base["name"], market, sess, model["bars"], q, "OK").to_dict()
                    rr["exchange"] = base.get("exchange", model["candidate"].get("exchange", ""))
                    rr["live"] = live if fresh else {}
                    # Lock the trade plan once the first fresh WebSocket price arrives.
                    # Forecast/decision may refresh, but stop/targets must not chase price tick-by-tick.
                    plans = payload.setdefault("plans", {})
                    if fresh and symbol not in plans:
                        plans[symbol] = {k: rr[k] for k in ("entry_low", "entry_high", "target1", "target2", "soft_stop", "hard_stop")}
                    if symbol in plans:
                        rr.update(plans[symbol])
                    displayed.append(rr)
                except Exception:
                    displayed.append(dict(base, live=live))
            else:
                displayed.append(dict(base, live=live))

        buys = [r for r in displayed if r["decision"] == "🟢 진입"]
        st.subheader(f"🟢 지금 진입 {len(buys)}개 · 상위 후보 {len(displayed)}개")
        for r in displayed:
            live = r.get("live", {})
            age = time.time() - _num(live.get("updated_at"), 0.0) if live else 999
            live_ok = age <= 10 and _num(live.get("current")) > 0
            current = _num(live.get("current"), _num(r["current"]))
            hard_breach = current > 0 and current <= _num(r["hard_stop"])
            with st.container(border=True):
                h1, h2, h3, h4 = st.columns([3.2, 1.2, 1.3, 1.3])
                with h1:
                    st.markdown(f"### {r['decision']} · {r['name']} ({r['symbol']})")
                    st.caption(f"{r['primary_strategy']} · {r['regime']} · 보정 전 {r['uncalibrated_score']:.1f}")
                _metric_cell(h2, "실시간 현재가" if live_ok else "현재가(스캔)", _fmt_price(current, market))
                _metric_cell(h3, "매수1호가", _fmt_price(_num(live.get("bid")), market) if _num(live.get("bid")) else "-")
                _metric_cell(h4, "매도1호가", _fmt_price(_num(live.get("ask")), market) if _num(live.get("ask")) else "-")
                if hard_breach:
                    st.error("🚨 HARD STOP 이탈")
                elif live_ok:
                    st.caption(f"실시간 갱신 {age:.1f}초 전 · 체결강도 {_num(live.get('trade_strength'),100):.0f} · 호가불균형 {_num(live.get('bid_ask_imbalance')):+.2f}")
                else:
                    st.caption("실시간 틱 수신 대기 — 현재 표시가는 마지막 스캔값일 수 있습니다.")

                p1, p2, p3, p4, p5 = st.columns(5)
                _metric_cell(p1, "진입 구간", f"{_fmt_price(r['entry_low'], market)} ~ {_fmt_price(r['entry_high'], market)}")
                _metric_cell(p2, "1차 목표", _fmt_price(r["target1"], market))
                _metric_cell(p3, "2차 목표", _fmt_price(r["target2"], market))
                _metric_cell(p4, "Soft Stop", _fmt_price(r["soft_stop"], market))
                _metric_cell(p5, "Hard Stop", _fmt_price(r["hard_stop"], market))

                fc = {x["minutes"]: x for x in r["forecasts"]}
                c5, c10, c15, c30 = st.columns(4)
                for col, m in ((c5, 5), (c10, 10), (c15, 15), (c30, 30)):
                    main, sub = _forecast_text(fc[m], market)
                    _metric_cell(col, f"{m}분 계산", main, sub)

                s1, s2, s3 = st.columns(3)
                s1.caption("보조전략: " + (", ".join(r["supporting_strategies"]) or "-"))
                s2.caption("충돌전략: " + (", ".join(r["conflicting_strategies"]) or "-"))
                s3.caption(f"VWAP {_fmt_price(_num(r['features'].get('vwap')), market)} · EMA20 {_fmt_price(_num(r['features'].get('ema20')), market)}")

    # Wrap live panel with Streamlit fragment so current price/flow refreshes without rerunning REST scans.
    live_fragment = st.fragment(run_every=1.0)(live_panel)

    tab_kr, tab_day, tab_pre, tab_regular, tab_after = st.tabs(["🇰🇷 국내", "🇺🇸 데이", "🇺🇸 프리", "🇺🇸 정규", "🇺🇸 애프터"])

    def one_tab(container, market: str, sess: str):
        state_key = f"scan_state_{market}_{sess}"
        with container:
            st.caption("거래량 TOP + 거래대금 TOP 합집합 최대 200개 → 유동성 우선 정밀분석 → 15개 전략 → 5/10/15/30분 독립 계산")
            detail = st.slider("정밀 분석 종목 수", 8, 24, 18, key=f"n_{market}_{sess}")
            if st.button("지금 스캔", type="primary", key=f"scan_{market}_{sess}"):
                with st.spinner("KIS 후보검색 + 1분봉 전략 계산 중..."):
                    cand, res, errs, models = run_scan(client, market, sess, detail)
                st.session_state[state_key] = {"candidates": cand, "results": res, "errors": errs, "models": models, "plans": {}}
                subscriptions = [
                    {"market": market, "session": sess, "exchange": r.get("exchange", "KRX"), "symbol": r["symbol"]}
                    for r in res[:10]
                ]
                feed.replace(subscriptions)
                st.toast(f"후보 {len(cand)} · 정밀분석 {len(res)} · 실시간 상위 {min(10, len(res))}종목 연결")
            live_fragment(state_key, market, sess)
            payload = st.session_state.get(state_key)
            if payload and payload["errors"]:
                with st.expander(f"데이터/API 경고 {len(payload['errors'])}건"):
                    st.write("\n".join(payload["errors"][:30]))

    one_tab(tab_kr, "KR", "KR_REGULAR")
    one_tab(tab_day, "US", "US_DAY")
    one_tab(tab_pre, "US", "US_PRE")
    one_tab(tab_regular, "US", "US_REGULAR")
    one_tab(tab_after, "US", "US_AFTER")


if __name__ == "__main__":
    render()
