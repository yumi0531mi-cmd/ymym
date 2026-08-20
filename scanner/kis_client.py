from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
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


US_INTRADAY_HISTORY_PAGES = 6
KR_INTRADAY_HISTORY_PAGES = 6


def _secret_with_source(
    names: tuple[str, ...], secrets: Any | None = None, default: str = ""
) -> tuple[str, str]:
    """Read a secret without ever returning its value to a diagnostic view.

    The second result is only an origin label. It lets the UI explain whether
    a saved key was found at the top level, in any TOML section, or in an
    environment variable. Secret text is never logged or rendered.
    """
    for name in names:
        if secrets is not None:
            try:
                value = secrets[name]
                if value:
                    return str(value), "최상위 Secrets"
            except Exception:
                pass
            try:
                section_names = list(secrets.keys())
            except Exception:
                section_names = ("kis", "KIS")
            for section_name in section_names:
                try:
                    section = secrets[section_name]
                    value = section[name]
                    if value:
                        return str(value), "하위 Secrets 설정"
                except Exception:
                    pass
        if os.getenv(name):
            return str(os.getenv(name)), "환경 변수"
    return default, "미확인"


def _secret(names: tuple[str, ...], secrets: Any | None = None, default: str = "") -> str:
    return _secret_with_source(names, secrets, default)[0]


def secrets_fingerprint(secrets: Any | None = None) -> str:
    """Return an opaque fingerprint to invalidate cached clients after a secret edit."""
    fields = (
        _secret(("KIS_APP_KEY", "APP_KEY", "app_key"), secrets),
        _secret(("KIS_APP_SECRET", "APP_SECRET", "app_secret"), secrets),
        _secret(("KIS_ACCESS_TOKEN", "KIS_TOKEN", "ACCESS_TOKEN", "TOKEN", "access_token"), secrets),
        _secret(("KIS_BASE_URL", "BASE_URL"), secrets),
        _secret(("KIS_ALLOW_TOKEN_ISSUE", "KIS_AUTO_TOKEN_ISSUE"), secrets, "true"),
    )
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


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

    Community Cloud deployments can use a daily KIS_ACCESS_TOKEN when supplied, or
    issue one only on the first protected KIS request when no valid token is cached.
    The live dashboard's initial candidate scan is a protected request, so it may
    issue the daily token once when the user opens the app.
    """

    def __init__(self, secrets: Any | None = None, cache_dir: str | Path = ".scanner_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.app_key, app_key_source = _secret_with_source(("KIS_APP_KEY", "APP_KEY", "app_key"), secrets)
        self.app_secret, app_secret_source = _secret_with_source(("KIS_APP_SECRET", "APP_SECRET", "app_secret"), secrets)
        self.access_token, token_source = _secret_with_source(("KIS_ACCESS_TOKEN", "KIS_TOKEN", "ACCESS_TOKEN", "TOKEN", "access_token"), secrets)
        self.access_token_expires_at = _secret(("KIS_ACCESS_TOKEN_EXPIRES_AT",), secrets)
        # The user selected the simple automatic mode: issue only when a KIS
        # request needs a token, then reuse the private local cache until expiry.
        self.allow_token_issue = _as_bool(_secret(("KIS_ALLOW_TOKEN_ISSUE", "KIS_AUTO_TOKEN_ISSUE"), secrets, "true"))
        cached_token = self._cached_token()
        has_auto_path = bool(self.app_key and self.app_secret and self.allow_token_issue)
        self.connection_diagnostics = {
            "앱 키": "확인됨" if self.app_key else "미확인",
            "앱 시크릿": "확인됨" if self.app_secret else "미확인",
            "당일 토큰": "확인됨" if (self.access_token or cached_token) else ("자동 발급 대기" if has_auto_path else "미확인"),
            "저장 위치": token_source if self.access_token else ("앱 임시 보관" if cached_token else (app_key_source if self.app_key else app_secret_source)),
        }
        self.base = _secret(("KIS_BASE_URL", "BASE_URL"), secrets, "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.min_request_interval = _as_float(
            _secret(("KIS_MIN_REQUEST_INTERVAL_SECONDS",), secrets, "1.0"), default=1.0, minimum=0.2
        )
        self.max_retries = _as_int(_secret(("KIS_MAX_RETRIES",), secrets, "1"), default=1, minimum=0)
        self.request_budget = RequestBudget(
            minute_limit=_as_int(_secret(("KIS_MAX_REQUESTS_PER_MINUTE",), secrets, "30"), default=30, minimum=1),
            five_hour_limit=_as_int(_secret(("KIS_MAX_REQUESTS_PER_FIVE_HOURS",), secrets, "1100"), default=1100, minimum=1),
        )
        self.session = requests.Session()
        self.session.headers.update({"content-type": "application/json; charset=utf-8", "custtype": "P"})
        self._mutex = threading.Lock()
        self._request_context = threading.local()
        self._last_call = 0.0
        self._blocked_until = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    @property
    def ready(self) -> bool:
        """True when a manual/cached token exists or safe on-demand issuance is available."""
        return bool(self.configured and (self.access_token or self._cached_token() or self.allow_token_issue))

    @property
    def token_mode(self) -> str:
        if self.access_token:
            return "수동 토큰"
        if self._cached_token():
            return "자동 발급 토큰 재사용"
        if self.allow_token_issue and self.configured:
            return "필요 시 1회 자동 발급"
        return "토큰 미설정"

    @property
    def token_is_expiring(self) -> bool:
        expires = _parse_time(self.access_token_expires_at)
        return bool(expires and expires <= datetime.now().astimezone() + timedelta(minutes=10))

    @property
    def budget_status(self) -> BudgetSnapshot:
        return self.request_budget.snapshot()

    def _wait_for_slot(self) -> None:
        purpose = str(getattr(self._request_context, "purpose", "기타"))
        budget_wait = self.request_budget.acquire(purpose)
        if budget_wait > 0:
            raise KISError(f"KIS 호출 예산 보호 중입니다. 최소 {budget_wait:.0f}초 뒤 다시 시도하세요.")
        with self._mutex:
            now = time.monotonic()
            delay = max(self._last_call + self.min_request_interval - now, self._blocked_until - now, 0.0)
            if delay > 0:
                time.sleep(delay)
            self._last_call = time.monotonic()

    @contextmanager
    def request_scope(self, purpose: str):
        """Attribute every REST request in this thread to one user-visible workflow."""
        previous = getattr(self._request_context, "purpose", "기타")
        self._request_context.purpose = str(purpose)
        try:
            yield
        finally:
            self._request_context.purpose = previous

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

    def websocket_approval_key(self) -> str:
        """Issue a KIS WebSocket approval key for read-only market-data subscriptions.

        This is distinct from an OAuth access token and is never exposed in the UI,
        logs, or cached price records.  It uses the same app key/secret already
        configured for this scanner and does not call any order endpoint.
        """
        if not self.configured:
            raise KISError("KIS_APP_KEY/KIS_APP_SECRET이 없습니다.")
        with self.request_scope("WebSocket 승인/재연결"):
            response = self._request(
                "POST",
                "/oauth2/Approval",
                payload={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "secretkey": self.app_secret,
                },
            )
        if not response.ok:
            raise KISError(f"KIS 실시간 접속키 발급 실패(HTTP {response.status_code})")
        try:
            key = str(response.json()["approval_key"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KISError("KIS 실시간 접속키 응답에 approval_key가 없습니다.") from exc
        if not key:
            raise KISError("KIS 실시간 접속키가 비어 있습니다.")
        return key

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
                "KIS_ACCESS_TOKEN이 없고 자동 발급도 꺼져 있습니다. "
                "KIS_ALLOW_TOKEN_ISSUE=true로 설정하거나 수동 토큰을 입력하세요."
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
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return token

    def _get_page(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any],
        tr_cont: str = "",
    ) -> tuple[dict[str, Any], str]:
        """Fetch one KIS page and return its continuation marker without exposing headers."""
        headers = {
            "authorization": f"Bearer {self._token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont
        response = self._request("GET", path, headers=headers, params=params)
        if not response.ok:
            raise KISError(f"KIS 시세 요청 실패(HTTP {response.status_code}): {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise KISError("KIS 시세 응답이 JSON 형식이 아닙니다.") from exc
        if str(payload.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(str(payload.get("msg1") or payload))
        return payload, str(response.headers.get("tr_cont", "")).strip().upper()

    def _get(self, path: str, tr_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return one KIS JSON response for ordinary non-paginated requests."""
        return self._get_page(path, tr_id, params)[0]

    def _ranking_pages(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any],
        output_key: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Collect only the shallow rank pages needed for up to 100 candidates."""
        rows: list[dict[str, Any]] = []
        continuation = ""
        while len(rows) < max(1, int(limit)):
            payload, next_marker = self._get_page(path, tr_id, params, continuation)
            page_rows = [row for row in list(payload.get(output_key) or []) if isinstance(row, dict)]
            rows.extend(page_rows)
            if next_marker not in {"M", "F"} or not page_rows:
                break
            continuation = "N"
        return rows[:max(1, int(limit))]

    def _us_ranking_pages(
        self,
        path: str,
        tr_id: str,
        base_params: dict[str, Any],
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Interleave U.S. exchange rank pages and retain the KIS continuation key."""
        maximum = max(1, int(limit))
        states = [
            {
                "exchange": exchange,
                "params": {**base_params, "EXCD": exchange},
                "tr_cont": "",
                "active": True,
            }
            for exchange in ("NAS", "NYS", "AMS")
        ]
        rows: list[dict[str, Any]] = []
        while len(rows) < maximum:
            progressed = False
            for state in states:
                if not state["active"]:
                    continue
                payload, next_marker = self._get_page(
                    path,
                    tr_id,
                    dict(state["params"]),
                    str(state["tr_cont"]),
                )
                page_rows = [row for row in list(payload.get("output2") or []) if isinstance(row, dict)]
                rows.extend([{**row, "_exchange": str(state["exchange"])} for row in page_rows])
                progressed = True
                if next_marker in {"M", "F"} and page_rows:
                    state["tr_cont"] = "N"
                    continuation_key = str(payload.get("keyb") or "")
                    state["params"] = {**dict(state["params"]), "KEYB": continuation_key}
                else:
                    state["active"] = False
                if len(rows) >= maximum:
                    break
            if not progressed:
                break
        return rows[:maximum]

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

    def orderbook(self, symbol: str, market: Market, exchange: str = "NAS") -> tuple[float | None, float | None]:
        """Return best bid/ask for a cached execution-safety check.

        The dashboard refreshes last price more frequently than this method so it
        can stay inside the five-hour KIS request budget.
        """
        return self._orderbook(symbol.strip().upper(), market, exchange)

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
            path = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": now.strftime("%H%M%S"),
                "FID_INPUT_DATE_1": now.strftime("%Y%m%d"),
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "",
            }
            rows: list[dict[str, Any]] = []
            seen_page_keys: set[datetime] = set()
            # The official domestic daily-minute endpoint returns at most 120 rows
            # but accepts a prior date and time on every call.  Continue from the
            # oldest returned completed minute to warm the 15/30-minute engines;
            # this intentionally consumes one budgeted REST call per page.
            for _page in range(KR_INTRADAY_HISTORY_PAGES):
                try:
                    payload = self._get(path, "FHKST03010230", params)
                except KISError:
                    if rows:
                        break
                    raise
                page_rows = [row for row in list(payload.get("output2") or []) if isinstance(row, dict)]
                rows.extend(page_rows)
                if not page_rows:
                    break
                keys: list[datetime] = []
                for row in page_rows:
                    try:
                        keys.append(datetime.strptime(
                            f"{str(row.get('stck_bsop_date') or '')}{str(row.get('stck_cntg_hour') or '').zfill(6)[:6]}",
                            "%Y%m%d%H%M%S",
                        ))
                    except ValueError:
                        continue
                if not keys:
                    break
                # A pre-open same-day cursor can be ignored by the domestic
                # endpoint, returning the opening rows again.  Stop a duplicate
                # page and move an opening-boundary cursor to the preceding
                # calendar date's close so the official past-data flag can find
                # the previous available trading session.
                if set(keys).issubset(seen_page_keys):
                    break
                seen_page_keys.update(keys)
                cursor = min(keys) - timedelta(minutes=1)
                if cursor.hour < 9:
                    cursor = (cursor - timedelta(days=1)).replace(hour=15, minute=30, second=0, microsecond=0)
                params = {
                    **params,
                    "FID_INPUT_DATE_1": cursor.strftime("%Y%m%d"),
                    "FID_INPUT_HOUR_1": cursor.strftime("%H%M%S"),
                    "FID_PW_DATA_INCU_YN": "Y",
                }
            mapping = {
                "date": "stck_bsop_date", "time": "stck_cntg_hour", "open": "stck_oprc", "high": "stck_hgpr",
                "low": "stck_lwpr", "close": "stck_prpr", "volume": "cntg_vol",
            }
        else:
            session_name = market_session(market, now)
            day_exchange = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
            # KIS uses separate exchange codes only for the US daytime market.
            # Regular/pre/after-hours minute charts retain NAS/NYS/AMS.
            request_exchange = day_exchange.get(exchange, exchange) if session_name == "US_DAY" else exchange
            path = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
            params = {"AUTH": "", "EXCD": request_exchange, "SYMB": symbol, "NMIN": "1", "PINC": "1", "NEXT": "", "NREC": "120", "FILL": "", "KEYB": ""}
            rows: list[dict[str, Any]] = []
            continuation = ""
            # KIS permits up to 120 records on one response.  The 15/30-minute
            # direction engines need stable 20-period flow baselines, so collect
            # up to six official pages (720 one-minute bars) before classifying a
            # horizon.  This is history acquisition, not a relaxation of a signal
            # gate: underwarmed horizons are withheld by forecast_path().
            for _page in range(US_INTRADAY_HISTORY_PAGES):
                try:
                    payload, next_marker = self._get_page(path, "HHDFS76950200", params, continuation)
                except KISError:
                    if rows:
                        break
                    raise
                page_rows = [row for row in list(payload.get("output2") or []) if isinstance(row, dict)]
                rows.extend(page_rows)
                if not page_rows:
                    break
                keys: list[datetime] = []
                for row in page_rows:
                    try:
                        parsed = datetime.strptime(
                            f"{str(row.get('xymd') or '')}{str(row.get('xhms') or '').zfill(6)[:6]}",
                            "%Y%m%d%H%M%S",
                        )
                        keys.append(parsed)
                    except ValueError:
                        continue
                if not keys:
                    break
                params = {
                    **params,
                    "PINC": "1",
                    "NEXT": "1",
                    "KEYB": (min(keys) - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S"),
                }
                # The documented continuation key is sufficient for this endpoint.
                # KIS sometimes omits tr_cont even while older minute rows remain;
                # preserve its header when provided but do not let an empty header
                # silently collapse the 30-minute indicator history to one page.
                continuation = "N" if next_marker in {"M", "F"} else ""
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


    def market_rankings(self, market: Market, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        """Fetch up to ``limit`` shallow rank rows without per-symbol details."""
        if market == Market.KR:
            # Domestic rank endpoints can have different availability by session or
            # account entitlement. Keep a successful rank source rather than
            # discarding it because the paired source is temporarily unavailable.
            volume_rows: list[dict[str, Any]] = []
            turnover_rows: list[dict[str, Any]] = []
            failures: list[KISError] = []
            try:
                volume_rows = self._ranking_pages(
                "/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_COND_SCR_DIV_CODE": "20171",
                        "FID_INPUT_ISCD": "0000",
                        "FID_DIV_CLS_CODE": "0",
                        "FID_BLNG_CLS_CODE": "0",
                        "FID_TRGT_CLS_CODE": "111111111",
                        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                        "FID_INPUT_PRICE_1": "0",
                        "FID_INPUT_PRICE_2": "10000000",
                    "FID_VOL_CNT": "0",
                    "FID_INPUT_DATE_1": "",
                },
                "output",
                limit=limit,
                )
            except KISError as exc:
                failures.append(exc)
            try:
                turnover_rows = self._ranking_pages(
                "/uapi/domestic-stock/v1/quotations/volume-rank",
                "FHPST01710000",
                {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_COND_SCR_DIV_CODE": "20171",
                        "FID_INPUT_ISCD": "0000",
                        "FID_DIV_CLS_CODE": "0",
                        "FID_BLNG_CLS_CODE": "3",
                        "FID_TRGT_CLS_CODE": "111111111",
                        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                        "FID_INPUT_PRICE_1": "0",
                        "FID_INPUT_PRICE_2": "10000000",
                        "FID_VOL_CNT": "0",
                        "FID_INPUT_DATE_1": "",
                },
                "output",
                limit=limit,
                )
            except KISError as exc:
                failures.append(exc)
            rows = {
                "거래량 TOP100": volume_rows,
                "거래대금 TOP100": turnover_rows,
            }
            if not any(rows.values()) and failures:
                raise failures[0]
            return rows

        return {
            "거래대금 TOP100": self._us_ranking_pages(
                "/uapi/overseas-stock/v1/ranking/trade-pbmn",
                "HHDFS76320010",
                {"NDAY": "0", "VOL_RANG": "0", "AUTH": "", "KEYB": "", "PRC1": "", "PRC2": ""},
                limit=limit,
            ),
            "거래량 TOP100": self._us_ranking_pages(
                "/uapi/overseas-stock/v1/ranking/trade-vol",
                "HHDFS76310010",
                {"NDAY": "0", "VOL_RANG": "0", "AUTH": "", "KEYB": "", "PRC1": "", "PRC2": ""},
                limit=limit,
            ),
        }
