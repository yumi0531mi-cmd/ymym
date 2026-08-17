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
from .rate_limit import BudgetSnapshot, RequestBudget
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


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(float(value), minimum)
    except (TypeError, ValueError):
        return default


def _as_int(value: str, default: int, minimum: int = 0) -> int:
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)
    except ValueError:
        return None


class KISClient:
    """Read-only KIS REST client with conservative token and request controls.

    Community Cloud deployments should provide a daily-issued KIS_ACCESS_TOKEN through
    Streamlit Secrets. Automatic token issuance is deliberately disabled by default.
    """

    def __init__(self, secrets: Any | None = None, cache_dir: str | Path = ".scanner_cache"):
        self.app_key = _secret(("KIS_APP_KEY", "APP_KEY", "app_key"), secrets)
        self.app_secret = _secret(("KIS_APP_SECRET", "APP_SECRET", "app_secret"), secrets)
        self.access_token = _secret(("KIS_ACCESS_TOKEN", "ACCESS_TOKEN"), secrets)
        self.access_token_expires_at = _secret(("KIS_ACCESS_TOKEN_EXPIRES_AT",), secrets)
        self.allow_token_issue = _as_bool(_secret(("KIS_ALLOW_TOKEN_ISSUE",), secrets, "false"))
        self.base = _secret(("KIS_BASE_URL", "BASE_URL"), secrets, "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.min_request_interval = _as_float(
            _secret(("KIS_MIN_REQUEST_INTERVAL_SECONDS",), secrets, "1.0"), default=1.0, minimum=0.2
        )
        self.max_retries = _as_int(_secret(("KIS_MAX_RETRIES",), secrets, "1"), default=1, minimum=0)
        self.request_budget = RequestBudget(
            minute_limit=_as_int(_secret(("KIS_MAX_REQUESTS_PER_MINUTE",), secrets, "30"), default=30, minimum=1),
            five_hour_limit=_as_int(_secret(("KIS_MAX_REQUESTS_PER_FIVE_HOURS",), secrets, "1100"), default=1100, minimum=1),
        )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"content-type": "application/json; charset=utf-8", "custtype": "P"})
        self._mutex = threading.Lock()
        self._last_call = 0.0
        self._blocked_until = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    @property
    def token_mode(self) -> str:
        if self.access_token:
            return "수동 토큰"
        if self.allow_token_issue:
            return "로컬 개발용 자동 발급"
        return "토큰 미설정"

    @property
    def token_is_expiring(self) -> bool:
        expires = _parse_time(self.access_token_expires_at)
        return bool(expires and expires <= datetime.now().astimezone() + timedelta(minutes=10))

    @property
    def budget_status(self) -> BudgetSnapshot:
        return self.request_budget.snapshot()

    def _wait_for_slot(self) -> None:
        budget_wait = self.request_budget.acquire()
        if budget_wait > 0:
            raise KISError(f"KIS 호출 예산 보호 중입니다. 최소 {budget_wait:.0f}초 뒤 다시 시도하세요.")
        with self._mutex:
            now = time.monotonic()
            delay = max(self._last_call + self.min_request_interval - now, self._blocked_until - now, 0.0)
            if delay > 0:
                time.sleep(delay)
            self._last_call = time.monotonic()

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None,
                 params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> requests.Response:
        response: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                response = self.session.request(
                    method,
                    f"{self.base}{path}",
                    headers=headers,
                    params=params,
                    json=payload,
                    timeout=12,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise KISError(f"KIS 네트워크 오류: {type(exc).__name__}") from exc
                time.sleep(min(2 ** attempt, 4))
                continue

            if response.status_code != 429:
                return response

            retry_after = _as_float(response.headers.get("Retry-After", ""), default=2.0, minimum=0.5)
            self.request_budget.block_for(retry_after)
            with self._mutex:
                self._blocked_until = max(self._blocked_until, time.monotonic() + retry_after)
            if attempt >= self.max_retries:
                raise KISError(f"KIS 요청 제한(HTTP 429)입니다. 최소 {retry_after:g}초 뒤 다시 시도하세요.")

        raise KISError("KIS 요청을 완료하지 못했습니다.")

    def _cached_token(self) -> str | None:
        path = self.cache_dir / "kis_token.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            expires = _parse_time(str(data.get("expires_at", "")))
            if expires and expires > datetime.now().astimezone() + timedelta(minutes=10):
                return str(data["access_token"])
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return None
        return None

    def _token(self) -> str:
        if self.access_token:
            if self.token_is_expiring:
                raise KISError("KIS_ACCESS_TOKEN이 만료 임박했습니다. 새 일일 토큰을 Secrets에 입력한 뒤 재배포하세요.")
            return self.access_token

        if not self.allow_token_issue:
            raise KISError(
                "KIS_ACCESS_TOKEN이 없습니다. Community Cloud에서는 일일 토큰을 Secrets에 입력하세요. "
                "자동 발급은 KIS_ALLOW_TOKEN_ISSUE=true인 로컬 개발 환경에서만 허용됩니다."
            )
        if not self.configured:
            raise KISError("KIS_APP_KEY/KIS_APP_SECRET이 없습니다.")

        path = self.cache_dir / "kis_token.json"
        lock = FileLock(str(path) + ".lock")
        with lock:
            cached = self._cached_token()
            if cached:
                return cached
            response = self._request(
                "POST",
                "/oauth2/tokenP",
                payload={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            )
            if not response.ok:
                raise KISError(f"KIS 토큰 발급 실패(HTTP {response.status_code}): {response.text[:200]}")
            try:
                data = response.json()
                token = str(data["access_token"])
            except (KeyError, TypeError, ValueError) as exc:
                raise KISError("KIS 토큰 응답에 access_token이 없습니다.") from exc
            expires_in = _as_int(str(data.get("expires_in", 23 * 60 * 60)), default=23 * 60 * 60, minimum=600)
            expires = datetime.now().astimezone() + timedelta(seconds=max(expires_in - 300, 600))
            path.write_text(
                json.dumps({"access_token": token, "expires_at": expires.isoformat()}), encoding="utf-8"
            )
            return token

    def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "authorization": f"Bearer {self._token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }
        response = self._request("GET", path, headers=headers, params=params)
        if not response.ok:
            raise KISError(f"KIS 시세 요청 실패(HTTP {response.status_code}): {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise KISError("KIS 시세 응답이 JSON 형식이 아닙니다.") from exc
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
            out = self._first_mapping(payload.get("output1"), payload.get("output2"), payload.get("output3"), payload.get("output"))
            bid = self._number(out, "pbid1", "bidp1", "bid")
            ask = self._number(out, "pask1", "askp1", "ask")
        return (bid or None, ask or None)

    def quote(self, symbol: str, market: Market, exchange: str = "NAS", include_orderbook: bool = True) -> Quote:
        symbol = symbol.strip().upper()
        now = datetime.now(tz=KST)
        if market == Market.KR:
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            )
            out = payload.get("output") or {}
            price = self._number(out, "stck_prpr")
            previous = self._number(out, "stck_sdpr", "stck_prdy_clpr")
            bid, ask = self._orderbook(symbol, market, exchange) if include_orderbook else (None, None)
            if price <= 0:
                raise KISError(f"{symbol} 국내 현재가가 수신되지 않았습니다.")
            return Quote(
                symbol, market, price, previous, now, bid or None, ask or None,
                self._number(out, "acml_vol"), self._number(out, "acml_tr_pbmn"), market_session(market, now),
            )

        payload = self._get(
            "/uapi/overseas-price/v1/quotations/price-detail",
            "HHDFS76200200",
            {"AUTH": "", "EXCD": exchange, "SYMB": symbol},
        )
        out = payload.get("output") or {}
        price = self._number(out, "last", "ovrs_nmix_prpr")
        previous = self._number(out, "base", "prev", "ovrs_nmix_prdy_clpr")
        bid, ask = self._orderbook(symbol, market, exchange) if include_orderbook else (None, None)
        if price <= 0:
            raise KISError(f"{symbol} 미국 현재가가 수신되지 않았습니다.")
        return Quote(
            symbol, market, price, previous, now, bid or None, ask or None,
            self._number(out, "tvol", "acml_vol"), self._number(out, "tamt"), market_session(market, now),
        )

    def intraday(self, symbol: str, market: Market, exchange: str = "NAS") -> pd.DataFrame:
        symbol = symbol.strip().upper()
        now = datetime.now(tz=KST)
        if market == Market.KR:
            payload = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                "FHKST03010200",
                {
                    "FID_ETC_CLS_CODE": "",
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": now.strftime("%H%M%S"),
                    "FID_PW_DATA_INCU_YN": "Y",
                },
            )
            rows = payload.get("output2") or []
            mapping = {
                "date": "stck_bsop_date", "time": "stck_cntg_hour", "open": "stck_oprc", "high": "stck_hgpr",
                "low": "stck_lwpr", "close": "stck_prpr", "volume": "cntg_vol",
            }
        else:
            session_name = market_session(market, now)
            day_exchange = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
            request_exchange = day_exchange.get(exchange, exchange) if session_name == "US_REGULAR" else exchange
            payload = self._get(
                "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
                "HHDFS76950200",
                {"AUTH": "", "EXCD": request_exchange, "SYMB": symbol, "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": "120", "FILL": "", "KEYB": ""},
            )
            rows = payload.get("output2") or []
            mapping = {"date": "xymd", "time": "xhms", "open": "open", "high": "high", "low": "low", "close": "last", "volume": "evol"}

        records = []
        for row in rows:
            try:
                records.append({key: row.get(value) for key, value in mapping.items()})
            except AttributeError:
                continue
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        for column in ("open", "high", "low", "close", "volume"):
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])
        date_values = df.get("date", pd.Series([now.strftime("%Y%m%d")] * len(df))).astype(str).str.replace(r"\D", "", regex=True)
        date_values = date_values.where(date_values.str.len() == 8, now.strftime("%Y%m%d"))
        time_values = df.get("time", pd.Series([""] * len(df))).astype(str).str.replace(r"\D", "", regex=True).str.zfill(6).str[:6]
        timestamps = pd.to_datetime(date_values + time_values, format="%Y%m%d%H%M%S", errors="coerce")
        try:
            timestamps = timestamps.dt.tz_localize(
                KST if market == Market.KR else ET, ambiguous="NaT", nonexistent="shift_forward"
            ).dt.tz_convert(KST)
        except (TypeError, ValueError):
            pass
        df.index = timestamps
        df = df.loc[df.index.notna(), ["open", "high", "low", "close", "volume"]].sort_index()
        return df[~df.index.duplicated(keep="last")]
