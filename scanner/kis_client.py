from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from filelock import FileLock

from .models import Market, Quote
from .sessions import ET, KST, market_session


class KISError(RuntimeError):
    pass


def _secret(names: tuple[str, ...], secrets: Any | None = None, default: str = "") -> str:
    for name in names:
        if secrets is not None:
            try:
                value = secrets[name]
                if value:
                    return str(value)
            except Exception:
                pass
        if os.getenv(name):
            return str(os.getenv(name))
    return default


class KISClient:
    """Read-only KIS REST client. It never submits orders."""

    def __init__(self, secrets: Any | None = None, cache_dir: str | Path = ".scanner_cache"):
        self.app_key = _secret(("KIS_APP_KEY", "APP_KEY", "app_key"), secrets)
        self.app_secret = _secret(("KIS_APP_SECRET", "APP_SECRET", "app_secret"), secrets)
        self.base = _secret(("KIS_BASE_URL", "BASE_URL"), secrets, "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"content-type": "application/json; charset=utf-8", "custtype": "P"})
        self._mutex = threading.Lock()
        self._last_call = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _token(self) -> str:
        if not self.configured:
            raise KISError("Streamlit Secrets에 KIS_APP_KEY/KIS_APP_SECRET이 없습니다.")
        path = self.cache_dir / "kis_token.json"
        lock = FileLock(str(path) + ".lock")
        with lock:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    expires = datetime.fromisoformat(data["expires_at"])
                    if expires > datetime.now().astimezone() + timedelta(minutes=10):
                        return str(data["access_token"])
                except Exception:
                    pass
            response = self.session.post(
                f"{self.base}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=12,
            )
            response.raise_for_status()
            data = response.json()
            token = str(data["access_token"])
            expires = datetime.now().astimezone() + timedelta(hours=23)
            path.write_text(json.dumps({"access_token": token, "expires_at": expires.isoformat()}), encoding="utf-8")
            return token

    def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._mutex:
            delay = .08 - (time.monotonic() - self._last_call)
            if delay > 0:
                time.sleep(delay)
            headers = {"authorization": f"Bearer {self._token()}", "appkey": self.app_key,
                       "appsecret": self.app_secret, "tr_id": tr_id}
            response = self.session.get(f"{self.base}{path}", headers=headers, params=params, timeout=8)
            self._last_call = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(str(payload.get("msg1") or payload))
        return payload

    @staticmethod
    def _number(data: dict[str, Any], *keys: str) -> float:
        for key in keys:
            try:
                raw = data.get(key)
                if raw not in (None, ""):
                    return float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _first_mapping(*values: Any) -> dict[str, Any]:
        """Return the first record from KIS outputs that may be dicts or lists."""
        for value in values:
            if isinstance(value, dict) and value:
                return value
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item:
                        return item
        return {}

    def _orderbook(self, symbol: str, market: Market, exchange: str) -> tuple[float | None, float | None]:
        if market == Market.KR:
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                "FHKST01010200",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            )
            out = self._first_mapping(payload.get("output1"), payload.get("output"))
            bid = self._number(out, "bidp1")
            ask = self._number(out, "askp1")
        else:
            payload = self._get(
                "/uapi/overseas-price/v1/quotations/inquire-asking-price",
                "HHDFS76200100",
                {"AUTH": "", "EXCD": exchange, "SYMB": symbol},
            )
            out = self._first_mapping(
                payload.get("output1"), payload.get("output2"),
                payload.get("output3"), payload.get("output"),
            )
            bid = self._number(out, "pbid1", "bidp1", "bid")
            ask = self._number(out, "pask1", "askp1", "ask")
        return (bid or None, ask or None)

    def quote(self, symbol: str, market: Market, exchange: str = "NAS", include_orderbook: bool = True) -> Quote:
        symbol = symbol.strip().upper()
        now = datetime.now(tz=KST)
        if market == Market.KR:
            payload = self._get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
            out = payload.get("output") or {}
            price = self._number(out, "stck_prpr")
            previous = self._number(out, "stck_sdpr", "stck_prdy_clpr")
            bid, ask = self._orderbook(symbol, market, exchange) if include_orderbook else (None, None)
            if price <= 0:
                raise KISError(f"{symbol} 국내 현재가가 수신되지 않았습니다.")
            return Quote(symbol, market, price, previous, now, bid or None, ask or None,
                         self._number(out, "acml_vol"), self._number(out, "acml_tr_pbmn"), market_session(market, now))
        payload = self._get("/uapi/overseas-price/v1/quotations/price-detail", "HHDFS76200200",
                            {"AUTH": "", "EXCD": exchange, "SYMB": symbol})
        out = payload.get("output") or {}
        price = self._number(out, "last", "ovrs_nmix_prpr")
        previous = self._number(out, "base", "prev", "ovrs_nmix_prdy_clpr")
        bid, ask = self._orderbook(symbol, market, exchange) if include_orderbook else (None, None)
        if price <= 0:
            raise KISError(f"{symbol} 미국 현재가가 수신되지 않았습니다.")
        return Quote(symbol, market, price, previous, now, bid or None, ask or None,
                     self._number(out, "tvol", "acml_vol"), self._number(out, "tamt"), market_session(market, now))

    def intraday(self, symbol: str, market: Market, exchange: str = "NAS") -> pd.DataFrame:
        symbol = symbol.strip().upper()
        now = datetime.now(tz=KST)
        if market == Market.KR:
            payload = self._get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200", {
                "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": now.strftime("%H%M%S"), "FID_PW_DATA_INCU_YN": "Y"})
            rows = payload.get("output2") or []
            mapping = {"date": "stck_bsop_date", "time": "stck_cntg_hour", "open": "stck_oprc", "high": "stck_hgpr", "low": "stck_lwpr",
                       "close": "stck_prpr", "volume": "cntg_vol"}
        else:
            session_name = market_session(market, now)
            day_exchange = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
            request_exchange = day_exchange.get(exchange, exchange) if session_name == "US_DAY" else exchange
            payload = self._get("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", "HHDFS76950200", {
                "AUTH": "", "EXCD": request_exchange, "SYMB": symbol, "NMIN": "1", "PINC": "1",
                "NEXT": "", "NREC": "120", "FILL": "", "KEYB": ""})
            rows = payload.get("output2") or []
            mapping = {"date": "xymd", "time": "xhms", "open": "open", "high": "high", "low": "low", "close": "last", "volume": "evol"}
        records = []
        for row in rows:
            try:
                records.append({k: row.get(v) for k, v in mapping.items()})
            except AttributeError:
                continue
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])
        date_values = df.get("date", pd.Series([now.strftime("%Y%m%d")] * len(df))).astype(str).str.replace(r"\D", "", regex=True)
        date_values = date_values.where(date_values.str.len() == 8, now.strftime("%Y%m%d"))
        time_values = df.get("time", pd.Series([""] * len(df))).astype(str).str.replace(r"\D", "", regex=True).str.zfill(6).str[:6]
        timestamps = pd.to_datetime(date_values + time_values, format="%Y%m%d%H%M%S", errors="coerce")
        try:
            timestamps = timestamps.dt.tz_localize(KST if market == Market.KR else ET, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert(KST)
        except (TypeError, ValueError):
            pass
        df.index = timestamps
        df = df.loc[df.index.notna(), ["open", "high", "low", "close", "volume"]].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df
