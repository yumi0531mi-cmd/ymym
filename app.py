from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scanner.calibration import calibration_for
from scanner.cycle import CycleStore
from scanner.engine import analyze
from scanner.indicators import resample
from scanner.kis_client import KISClient, KISError, secrets_fingerprint
from scanner.market_screener import is_kr_directional_product, merge_rankings
from scanner.models import Market, Quote, Regime, Signal
from scanner.persistence import EventStore
from scanner.realtime import KISRealtimeHub, process_realtime_hub
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationCase, ValidationStore

APP_VERSION = "6.5-free-100-path-validation"
# Bump this whenever the cached KISClient interface changes. Streamlit can retain a
# resource through a hot code update, so a new contract must never reuse an old client.
CLIENT_CACHE_VERSION = "client-contract-v12-ranked-100-pages"
# The market-data connection has its own lifecycle. Bump this only when the
# WebSocket protocol or recovery contract changes, without issuing a new REST token.
REALTIME_HUB_CACHE_VERSION = "realtime-hub-v4-ranked-five"
VALIDATION_ROOT = Path(".scanner_data/validation")
MAX_LIVE_CARDS = 5
MAX_ANALYSIS_CANDIDATES = 8
MAX_CANDIDATE_LIST = 200
MAX_FAST_SHORTLIST = 15
# 무료 Streamlit 검증 배치는 4개 표본을 매분 시작한다. 각 시작은 시세·분봉·호가
# 확인을 포함하고, 이전 표본의 5·15·30분 실제 가격도 별도 수집하므로 KIS 분당
# 30건 호출 제한 안에서 운용한다.
MAX_PENDING_FORECAST_WATCHES = 8
FREE_VALIDATION_BATCH_SIZE = 100
FREE_VALIDATION_STARTS_PER_MINUTE = 4
MAX_DAILY_RISE_PCT = 12.0
KR_PRICE_CEILING = 300_000.0
US_PRICE_CEILING = 200.0
MIN_NET_TARGET_UPSIDE_PCT = 0.50
MIN_NET_REWARD_RISK = 1.10
MAX_STOP_DISTANCE_PCT = 2.00
KR_SEARCH_INDEX_PATH = Path("data/kr_stock_index.json")
ACTIVE_CARD_SESSIONS = {"KR_REGULAR", "US_DAY", "US_PRE", "US_REGULAR", "US_AFTER"}

st.set_page_config(
    page_title="상승·반복단타 혼합 스캐너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)
st.markdown(
    """
<style>
:root{--bg:#ffffff;--panel:#ffffff;--panel2:#f7f9fc;--line:#dbe2ec;--text:#172033;--muted:#64748b;--green:#17834a;--red:#d54444;--yellow:#a66c00;--blue:#2563eb}
	.stApp{background:var(--bg);color:var(--text)}
	.block-container{max-width:1440px;padding:1.1rem 1.25rem 3rem}
	[data-testid="stSidebar"]{background:#f8fafc;border-right:1px solid var(--line)}
	[data-testid="stSidebar"] *{color:var(--text)}
	h1,h2,h3,p,label{color:var(--text)!important}
	[data-testid="stCaptionContainer"] p{color:var(--muted)!important}
	[data-testid="stMetric"]{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:.55rem}
	[data-testid="stMetricLabel"]{color:var(--muted)}
	[data-testid="stMetricValue"]{color:var(--text);font-size:1.1rem!important}
	.mobile-head{margin:.2rem 0 1rem}.mobile-head h1{margin:0;font-size:clamp(1.6rem,5vw,2.5rem);letter-spacing:-.05em}.mobile-head p{margin:.35rem 0 0;color:var(--muted)!important;font-size:.92rem}
	.connection{border-radius:14px;padding:.85rem 1rem;margin:.35rem 0 1rem;border:1px solid}.connection.ok{background:#effbf3;border-color:#8ed3aa;color:#176238}.connection.wait{background:#fff9e8;border-color:#e8cc72;color:#785000}
	.candidate-row{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:.6rem .75rem;margin:.4rem 0;color:var(--text);display:flex;justify-content:space-between;gap:.5rem;box-shadow:0 2px 8px rgba(15,23,42,.04)}.candidate-row small{color:var(--muted)}
	.cards-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:1rem;align-items:start}.trade-card{background:var(--panel);border:1px solid #d6dfeb;border-radius:18px;padding:1rem;margin:0;box-shadow:0 7px 22px rgba(15,23,42,.08);color:var(--text);height:100%;box-sizing:border-box}
	.card-top{display:flex;justify-content:space-between;gap:.6rem;align-items:flex-start}.ticker{font-size:1.25rem;font-weight:850;letter-spacing:-.02em}.name{font-size:.78rem;color:var(--muted);margin-top:.16rem}.price{font-size:1.35rem;font-weight:850;text-align:right}.change.up{color:var(--green)}.change.down{color:var(--red)}.change.flat{color:var(--muted)}
	.badges{display:flex;flex-wrap:wrap;gap:.35rem;margin:.7rem 0}.badge{border-radius:999px;padding:.24rem .55rem;font-size:.73rem;font-weight:750;background:#edf2f8;color:#34445d}.badge.buy{background:#dff7e7;color:#176238}.badge.wait{background:#fff3cc;color:#885b00}.badge.block{background:#ffe4e5;color:#a32f37}.badge.risk{background:#f8e7ec;color:#a32f57}
	.warn-box{border:1px solid #f0b7b7;background:#fff4f4;color:#9f3131;border-radius:11px;padding:.55rem .65rem;margin:.55rem 0;font-size:.82rem}.note-box{background:#edf4ff;color:#274f85;border-radius:11px;padding:.55rem .65rem;margin:.55rem 0;font-size:.82rem}
	.data-grid{display:grid;grid-template-columns:1fr 1fr;gap:.35rem .75rem;margin:.75rem 0}.data-item{border-bottom:1px solid #e3e9f1;padding:.35rem 0}.data-label{color:var(--muted);font-size:.72rem}.data-value{font-size:.9rem;font-weight:700;margin-top:.1rem}
			.plan-title{font-weight:800;font-size:.95rem;margin:.85rem 0 .35rem}.plan-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.4rem}.plan-item{background:#f5f8fc;border-radius:10px;padding:.48rem}.plan-item .data-label{font-size:.7rem}.entry{color:#a66c00}.target{color:var(--green)}.stop{color:var(--red)}
		.structure-strip{display:grid;grid-template-columns:repeat(6,minmax(100px,1fr));overflow-x:auto;border:1px solid var(--line);border-radius:12px;margin:.35rem 0 .65rem;background:#fbfcfe}.structure-cell{padding:.48rem .55rem;border-right:1px solid var(--line);min-width:100px}.structure-cell:last-child{border-right:0}.structure-label{color:var(--muted);font-size:.7rem}.structure-value{font-size:.86rem;font-weight:750;margin-top:.15rem;white-space:nowrap}@media(max-width:700px){.structure-strip{grid-template-columns:repeat(6,minmax(112px,1fr))}}

	.card-foot{color:var(--muted);font-size:.72rem;margin-top:.65rem}.card-detail{margin:.15rem 0 1rem}
	@media(max-width:700px){.block-container{padding:.65rem .55rem 2rem}.cards-grid{grid-template-columns:1fr;gap:.75rem}.trade-card{padding:.8rem;border-radius:15px}.ticker{font-size:1.1rem}.price{font-size:1.18rem}.data-grid{gap:.25rem .55rem}.plan-grid{gap:.3rem}.plan-item{padding:.42rem}.stButton>button{min-height:2.5rem;font-size:.92rem}}

</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_client(cache_version: str, secret_fingerprint: str) -> KISClient:
    """Cache one read-only client for the currently stored secret values."""
    return KISClient(st.secrets)


def current_secret_fingerprint() -> str:
    return secrets_fingerprint(st.secrets)


def current_client() -> KISClient:
    """Return a client compatible with the active code after a hot deployment.

    This only constructs a local read-only client. It does not issue a KIS token,
    request prices, or submit an order.
    """
    fingerprint = current_secret_fingerprint()
    client = get_client(CLIENT_CACHE_VERSION, fingerprint)
    # Streamlit may retain an object created before a class interface update.
    # A type check avoids touching properties that old instances do not have.
    if not isinstance(client, KISClient):
        get_client.clear()
        client = get_client(CLIENT_CACHE_VERSION, fingerprint)
    return client


def get_realtime_hub(cache_version: str, secret_fingerprint: str) -> KISRealtimeHub:
    """Use the process singleton instead of one hub per Streamlit resource key."""
    del cache_version
    return process_realtime_hub(get_client(CLIENT_CACHE_VERSION, secret_fingerprint), secret_fingerprint)


def current_realtime_hub() -> KISRealtimeHub:
    """Return a current live hub even if Streamlit retained an older resource."""
    fingerprint = current_secret_fingerprint()
    hub = get_realtime_hub(REALTIME_HUB_CACHE_VERSION, fingerprint)
    if not isinstance(hub, KISRealtimeHub) or not callable(getattr(hub, "completed_bar_rows", None)):
        hub = get_realtime_hub(REALTIME_HUB_CACHE_VERSION, fingerprint)
    return hub


@st.cache_resource
def get_event_store(secret_fingerprint: str) -> EventStore:
    """Recreate the persistent event store whenever Streamlit Secrets change."""
    return EventStore(st.secrets)


@st.cache_resource
def get_cycle_store(secret_fingerprint: str) -> CycleStore:
    return CycleStore(get_event_store(secret_fingerprint))


def _quote_to_cache_record(quote: Quote) -> dict[str, object]:
    return {
        "symbol": quote.symbol,
        "market": quote.market.value,
        "price": quote.price,
        "previous_close": quote.previous_close,
        "timestamp": quote.timestamp.isoformat(),
        "bid": quote.bid,
        "ask": quote.ask,
        "volume": quote.volume,
        "turnover": quote.turnover,
        "session": quote.session,
        "source": quote.source,
    }


def _quote_from_cache_record(record: dict[str, object]) -> Quote:
    return Quote(
        symbol=str(record["symbol"]),
        market=Market(str(record["market"])),
        price=float(record["price"]),
        previous_close=float(record["previous_close"]),
        timestamp=datetime.fromisoformat(str(record["timestamp"])),
        bid=float(record["bid"]) if record.get("bid") is not None else None,
        ask=float(record["ask"]) if record.get("ask") is not None else None,
        volume=float(record["volume"]) if record.get("volume") is not None else None,
        turnover=float(record["turnover"]) if record.get("turnover") is not None else None,
        session=str(record.get("session") or "UNKNOWN"),
        source=str(record.get("source") or "KIS"),
    )


@st.cache_data(ttl=12, show_spinner=False)
def _load_quote_record(symbol: str, market_value: str, exchange: str) -> dict[str, object]:
    # KIS REST is the only fallback when the official WebSocket is unavailable.
    # Five visible cards at this interval remain below the 30-call/minute ceiling.
    quote = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).quote(
        symbol, Market(market_value), exchange, include_orderbook=False
    )
    return _quote_to_cache_record(quote)


@st.cache_data(ttl=900, show_spinner=False)
def _load_orderbook(symbol: str, market_value: str, exchange: str) -> tuple[float | None, float | None]:
    # Execution safety is refreshed every 15 minutes; it is not falsely presented
    # as tick-by-tick orderbook data.
    return get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).orderbook(
        symbol, Market(market_value), exchange
    )


def load_rest_dashboard_quote(symbol: str, market_value: str, exchange: str) -> Quote:
    quote = _quote_from_cache_record(_load_quote_record(symbol, market_value, exchange))
    try:
        bid, ask = _load_orderbook(symbol, market_value, exchange)
    except KISError:
        # Preserve price visibility when an entitlement/session-specific
        # orderbook endpoint is unavailable. Missing bid/ask safely keeps the
        # engine's execution gate in WAIT.
        bid, ask = None, None
    return Quote(
        symbol=quote.symbol, market=quote.market, price=quote.price,
        previous_close=quote.previous_close, timestamp=quote.timestamp,
        bid=bid, ask=ask, volume=quote.volume, turnover=quote.turnover,
        session=quote.session, source=quote.source,
    )


@st.cache_data(ttl=900, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str, cache_version: str) -> pd.DataFrame:
    # Historical completed bars seed the analysis. Live KIS trades are merged below
    # so the trailing structure does not wait for another REST refresh.
    return get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).intraday(
        symbol, Market(market_value), exchange
    )


def merge_live_completed_bars(base: pd.DataFrame, symbol: str, market: Market) -> pd.DataFrame:
    """Overlay locally completed one-minute KIS trade bars onto cached REST history."""
    rows = current_realtime_hub().completed_bar_rows(market, symbol)
    if not rows:
        return base
    live = pd.DataFrame(rows).set_index("timestamp")
    live.index = pd.to_datetime(live.index)
    historical = base.copy()
    historical.index = pd.to_datetime(historical.index)
    if getattr(historical.index, "tz", None) is not None and getattr(live.index, "tz", None) is not None:
        live.index = live.index.tz_convert(historical.index.tz)
    elif getattr(historical.index, "tz", None) is not None:
        live.index = live.index.tz_localize(historical.index.tz)
    merged = pd.concat([historical, live], axis=0)
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def quote_with_live_tick(base: Quote) -> Quote:
    tick = current_realtime_hub().tick(base.market, base.symbol)
    if tick is None:
        return base
    return Quote(
        symbol=base.symbol, market=base.market, price=tick.price,
        previous_close=base.previous_close, timestamp=tick.timestamp,
        bid=tick.bid if tick.bid is not None else base.bid,
        ask=tick.ask if tick.ask is not None else base.ask,
        volume=tick.volume if tick.volume is not None else base.volume,
        turnover=base.turnover, session=base.session, source=tick.source,
    )


def display_tick(market: Market, symbol: str):
    """Return only an official KIS trade tick; never substitute another provider."""
    return current_realtime_hub().tick(market, symbol)


def price_ceiling(market: Market) -> float:
    return KR_PRICE_CEILING if market == Market.KR else US_PRICE_CEILING


def default_market_label() -> str:
    """Open the currently active market first while preserving a user's later choice."""
    kr_session = market_session(Market.KR)
    us_session = market_session(Market.US)
    if kr_session not in ACTIVE_CARD_SESSIONS and us_session in ACTIVE_CARD_SESSIONS:
        return "미국"
    return "국내"


def eligible_price(candidate: dict[str, Any], market: Market) -> bool:
    try:
        price = float(candidate.get("price") or 0.0)
        change_pct = float(candidate.get("change_pct") or 0.0)
    except (TypeError, ValueError):
        return False
    # A scalp screen must not promote an already vertical move as a fresh entry.
    # Larger gainers can still be inspected by direct symbol search.
    return 0 < price < price_ceiling(market) and 0 < change_pct <= MAX_DAILY_RISE_PCT


def candidate_pool_price_eligible(candidate: dict[str, Any], market: Market) -> bool:
    """Keep the TOP100 volume/turnover union intact except for the user's price ceiling."""
    try:
        price = float(candidate.get("price") or 0.0)
    except (TypeError, ValueError):
        return False
    return 0 < price < price_ceiling(market)


def is_daily_overheated(price: float, previous_close: float) -> bool:
    """Return whether a refreshed quote must be removed from final candidate cards."""
    if price <= 0 or previous_close <= 0:
        return False
    return (price / previous_close - 1.0) * 100.0 > MAX_DAILY_RISE_PCT + 1e-9


def sort_rising_candidates(candidates: list[dict[str, Any]], market: Market) -> list[dict[str, Any]]:
    """Keep the user's price range and rank rising candidates by screen strength."""
    eligible = [candidate for candidate in candidates if eligible_price(candidate, market)]
    return sorted(
        eligible,
        key=lambda candidate: (
            float(candidate.get("screen_score") or 0.0),
            float(candidate.get("change_pct") or -999.0),
            float(candidate.get("turnover") or 0.0),
        ),
        reverse=True,
    )


def fast_shortlist_candidates(candidates: list[dict[str, Any]], market: Market) -> list[dict[str, Any]]:
    """Narrow the preserved ranking union before any symbol-level REST calls."""
    shortlisted = sort_rising_candidates(candidates, market)[:MAX_FAST_SHORTLIST]
    return [{**candidate, "candidate_stage": "순위·유동성 빠른 선별"} for candidate in shortlisted]


@st.cache_data(ttl=300, show_spinner=False)
def load_dashboard_candidates(market_value: str, cache_version: str) -> list[dict[str, Any]]:
    """Return the price-eligible TOP100 volume/turnover union without signal filtering."""
    market = Market(market_value)
    client = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint())
    try:
        # The v12 KIS client defaults to 100 rank rows. Calling without the
        # optional keyword also keeps a live Streamlit worker safe if it is
        # briefly finishing a rerun that still holds the prior method shape.
        rankings = client.market_rankings(market)
        candidates = [candidate.to_dict() for candidate in merge_rankings(market, rankings, limit=MAX_CANDIDATE_LIST)]
        for candidate in candidates:
            candidate["candidate_source"] = "거래량 TOP100 ∪ 거래대금 TOP100"
        pool = [candidate for candidate in candidates if candidate_pool_price_eligible(candidate, market)]
        if pool:
            return sorted(
                pool,
                key=lambda candidate: (
                    float(candidate.get("screen_score") or 0.0),
                    float(candidate.get("turnover") or 0.0),
                    float(candidate.get("volume") or 0.0),
                ),
                reverse=True,
            )
    except KISError:
        # Ranking availability differs by market session and account entitlement.
        # A transparent liquid-list fallback avoids a blank first screen.
        pass

    items = KR_LIQUID if market == Market.KR else US_LIQUID
    fallback: list[dict[str, Any]] = []
    for item in items:
        if market == Market.KR and is_kr_directional_product(item.name):
            continue
        try:
            quote = client.quote(item.symbol, market, item.exchange, include_orderbook=False)
        except KISError:
            continue
        fallback.append({
            "symbol": quote.symbol,
            "name": item.name,
            "market": market.value,
            "exchange": item.exchange,
            "price": quote.price,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "turnover": quote.turnover,
            "candidate_source": "유동성 시작목록 자동 대체",
        })
    return sort_rising_candidates(fallback, market)[:MAX_CANDIDATE_LIST]


@st.cache_data(ttl=60, show_spinner=False)
def scan_starter_universe(market_value: str) -> tuple[list[tuple[str, float]], list[str]]:
    scan_market = Market(market_value)
    items = KR_LIQUID if scan_market == Market.KR else US_LIQUID
    quotes: list[Quote] = []
    errors: list[str] = []
    for item in items:
        if scan_market == Market.KR and is_kr_directional_product(item.name):
            continue
        try:
            quotes.append(
                get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).quote(
                    item.symbol, scan_market, item.exchange, include_orderbook=False
                )
            )
        except Exception as exc:
            errors.append(f"{item.symbol}: {type(exc).__name__}")
    ranked = rank_quotes(quotes, scan_market)
    return [(quote.symbol, float(quote.change_pct)) for quote in ranked], errors


@st.cache_data(show_spinner=False)
def load_kr_search_index() -> list[dict[str, str]]:
    """Load the bundled KRX name/code index without making a KIS request."""
    try:
        payload = json.loads(KR_SEARCH_INDEX_PATH.read_text(encoding="utf-8"))
        return [dict(item) for item in payload.get("items", []) if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        return [
            {"symbol": item.symbol, "name": item.name, "market": ""}
            for item in KR_LIQUID
            if not is_kr_directional_product(item.name)
        ]


def search_kr_stock(query: str, limit: int = 6) -> list[dict[str, str]]:
    needle = query.strip().replace(" ", "")
    if not needle:
        return []
    items = [item for item in load_kr_search_index() if not is_kr_directional_product(str(item.get("name") or ""))]
    if needle.isdigit():
        padded = needle.zfill(6)
        return [item for item in items if item.get("symbol") == padded][:limit]
    exact = [item for item in items if str(item.get("name") or "").replace(" ", "") == needle]
    partial = [item for item in items if needle in str(item.get("name") or "").replace(" ", "")]
    return (exact + [item for item in partial if item not in exact])[:limit]


def us_exchange_for(symbol: str) -> str:
    normalized = symbol.strip().upper()
    for item in US_LIQUID:
        if item.symbol == normalized:
            return item.exchange
    return "NAS"


def repeat_band_pct(plan: Any) -> float | None:
    if not plan.repeat_box or plan.current_price <= 0:
        return None
    low, high = plan.repeat_box
    width = (float(high) - float(low)) / float(plan.current_price) * 100
    return width if 0.5 <= width <= 5.0 else None


def money(value: float | None) -> str:
    if value is None:
        return "미확인"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def price_text(value: float | None) -> str:
    return money(value) if value is not None else "조건 미확인"


def number_text(value: Any, suffix: str = "") -> str:
    if isinstance(value, (float, int)):
        return f"{value:.2f}{suffix}"
    return "미확인"


def signal_class(signal: Signal) -> str:
    if signal == Signal.BUY:
        return "buy"
    if signal in (Signal.BLOCK, Signal.SELL):
        return "block"
    return "wait"


def forecast_point_for(plan: Any, minutes: int) -> Any | None:
    return next((point for point in plan.forecasts if point.minutes == minutes), None)


def forecast_direction_text(point: Any | None) -> str:
    if point is None:
        return "예상 계산 대기"
    mapping = {Regime.UP: "상승 예상", Regime.DOWN: "하방 예상", Regime.RANGE: "박스권 예상"}
    return mapping.get(point.direction, "방향 확인 중")


def forecast_range_text(point: Any | None) -> str:
    if point is None:
        return "완료봉 추가 확인 필요"
    return f"{forecast_direction_text(point)} · 예상 범위 {price_text(point.low)} ~ {price_text(point.high)}"


def buy_range_text(plan: Any, quote: Quote) -> str:
    """Format only an executable buy zone whose stop and targets have valid ordering."""
    entry = plan.entry
    stop = plan.hard_stop or plan.invalidation or plan.stop
    target1 = plan.target
    if not (
        bool(plan.diagnostics.get("price_structure_valid"))
        and isinstance(entry, (int, float)) and isinstance(stop, (int, float))
        and isinstance(target1, (int, float)) and 0 < float(stop) < float(entry) < float(target1)
    ):
        return "현재가 위 목표 구조 재확인 중"
    candidates = [float(entry)]
    for key in ("vwap", "ema9"):
        value = plan.diagnostics.get(key)
        if (
            isinstance(value, (int, float))
            and float(stop) < float(value) < float(target1)
            and float(value) <= quote.price * 1.002
        ):
            candidates.append(float(value))
    low, high = min(candidates), max(candidates)
    return price_text(low) if abs(high - low) < 0.01 else f"{price_text(low)} ~ {price_text(high)}"


def actionable_display_levels(plan: Any, quote: Quote) -> dict[str, float | str | bool]:
    """Return only completed-chart structural reference levels; never invent percentage targets."""
    upward = bool(plan.diagnostics.get("long_price_path_confirmed"))
    if not upward or quote.price <= 0:
        return {"available": False, "basis": "하방·혼조 경로 · 신규 진입 금지"}

    entry = float(plan.entry) if isinstance(plan.entry, (int, float)) and 0 < float(plan.entry) <= quote.price * 1.002 else quote.price
    stop = float(plan.hard_stop or plan.stop or 0.0)
    target1 = float(plan.target or 0.0)
    target2 = float(plan.target2 or 0.0)
    if not 0 < stop < entry < target1 < target2:
        return {"available": False, "basis": "완료 차트의 지지·저항 목표 구조 재확인 중"}

    support = float(plan.soft_stop or 0.0)
    if not stop < support < entry:
        return {"available": False, "basis": "완료 차트의 지지 구조 재확인 중"}
    return {
        "available": True,
        "entry": entry,
        "target1": target1,
        "target2": target2,
        "support": support,
        "stop": stop,
        "basis": "완료 차트 지지·저항 구조 · 세 조건 충족 때만 다음 저항 확장",
    }


def regime_text(regime: Regime) -> str:
    return {
        Regime.UP: "상승 추세",
        Regime.RANGE: "박스권",
        Regime.DOWN: "하락 추세",
        Regime.TRANSITION: "전환 구간",
    }.get(regime, "구조 확인 중")


def dashboard_structure(item: dict[str, Any]) -> dict[str, str]:
    """Build the short structure row shown above the price levels."""
    plan = item["plan"]
    bars: pd.DataFrame = item["bars"]
    timeframes = plan.diagnostics.get("timeframes") or {}
    trend_30 = str(timeframes.get(30) or timeframes.get("30") or plan.regime.value)
    trend_label = regime_text(Regime(trend_30)) if trend_30 in {item.value for item in Regime} else regime_text(plan.regime)
    completed_30 = max(0, len(resample(bars, 30)) - 1) if not bars.empty else 0
    box = plan.repeat_box
    if plan.regime == Regime.UP and box:
        kind = "우상향 반복단타"
    elif plan.regime == Regime.UP:
        kind = "상승 추세"
    elif plan.regime == Regime.RANGE:
        kind = "박스 반복단타"
    elif plan.regime == Regime.DOWN:
        kind = "하락 추세"
    else:
        kind = "전환 관찰"
    box_text = "박스 확인" if box else "박스 형성 중"
    current_state = {
        "NORMAL_SWING": "정상 상승",
        "NORMAL_PULLBACK": "정상 눌림",
        "SHAKEOUT": "이탈 후 회복",
        "WARNING": "지지 확인",
        "REAL_BREAKDOWN": "구조 이탈",
        "HARD_EXIT": "손절 구조",
    }.get(plan.risk_state, "구조 확인 중")
    if not item.get("chart_aligned", True):
        current_state = str(item.get("chart_alignment_reason") or "완료 분봉 확인 중")
    elif bool(plan.diagnostics.get("has_downward_forecast")):
        current_state = "하방 경로 관찰"
    elif not bool(plan.diagnostics.get("forecast_path_ready")):
        current_state = "방향 재계산 중"
    return {
        "유형": kind,
        "큰 추세": trend_label,
        "30분봉 구조": trend_label,
        "30분봉 수": str(completed_30),
        "박스 판정": box_text,
        "현재 상태": current_state,
    }


def structure_strip_html(structure: dict[str, str]) -> str:
    cells = "".join(
        f'<div class="structure-cell"><div class="structure-label">{html.escape(label)}</div>'
        f'<div class="structure-value">{html.escape(value)}</div></div>'
        for label, value in structure.items()
    )
    return f'<div class="structure-strip">{cells}</div>'


def compact_directions(plan: Any) -> str:
    signs = {Regime.UP: "+", Regime.DOWN: "-", Regime.RANGE: "0"}
    values = []
    for minutes in (5, 15, 30):
        point = forecast_point_for(plan, minutes)
        values.append(f"{minutes}분 {signs.get(point.direction, '?') if point else '?'}")
    return " · ".join(values)


def strategy_signal_text(plan: Any) -> str:
    """Present strategy status without labelling an untested condition as 80% verified."""
    if plan.signal == Signal.BUY and plan.calibration_probability is not None and plan.calibration_probability >= 80.0:
        return "강한 매수 검토 · 실측 80% 이상"
    if plan.signal == Signal.BUY:
        return "매수 검토 신호 · 진입 조건 충족"
    if plan.signal == Signal.WAIT and plan.calibration_samples >= 100 and plan.calibration_probability is not None and plan.calibration_probability < 80.0:
        return "진입 대기 · 실측 80% 미만"
    mapping = {
        Signal.WAIT: "진입 대기",
        Signal.BLOCK: "진입 차단",
        Signal.SELL: "청산 검토",
        Signal.UNVERIFIED: "데이터 확인 대기",
    }
    return mapping.get(plan.signal, str(plan.signal.value))


def completed_bar_alignment(quote: Quote, bars: pd.DataFrame) -> tuple[bool, str]:
    """Allow structural prices only when a recent completed bar agrees with current price."""
    if len(bars) < 2:
        return False, "완료 분봉 수집 중"
    completed = bars.iloc[-2]
    try:
        completed_at = pd.Timestamp(bars.index[-2]).to_pydatetime()
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=quote.timestamp.tzinfo)
        age_minutes = abs((quote.timestamp - completed_at).total_seconds()) / 60.0
        close = float(completed.close)
    except (AttributeError, TypeError, ValueError):
        return False, "완료 분봉 확인 중"
    if close <= 0 or age_minutes > 30.0:
        return False, "완료 분봉 재구성 중"
    gap_pct = abs(quote.price / close - 1.0) * 100.0 if quote.price > 0 else 100.0
    if gap_pct > 12.0:
        return False, "현재가·완료 분봉 불일치"
    return True, "일치"


def record_and_score_live_validation(
    store: ValidationStore,
    plan: Any,
    quote: Quote,
    bars: pd.DataFrame,
    chart_aligned: bool,
    cost_pct: float,
) -> tuple[int, bool]:
    """Persist only real KIS-trade-backed plans and score mature cases from completed bars."""
    scored_cases = store.score_ready(quote.symbol, quote.market.value, bars, float(cost_pct))
    live_tick = current_realtime_hub().tick(quote.market, quote.symbol)
    if not (
        plan.signal == Signal.BUY
        and live_tick
        and chart_aligned
        and plan.entry
        and plan.target
        and plan.hard_stop
    ):
        return scored_cases, False
    case = ValidationCase.from_plan(
        plan,
        live_tick.price,
        quote.session,
        version=APP_VERSION,
        latest_trade_time=live_tick.timestamp,
    )
    _, recorded_case = store.save_once(case, cooldown_seconds=300)
    return scored_cases, recorded_case


def record_forecast_accuracy_audit(
    store: ValidationStore,
    plan: Any,
    quote: Quote,
    bars: pd.DataFrame,
    chart_aligned: bool,
    cost_pct: float,
    exchange: str = "",
    batch_id: str = "",
) -> tuple[int, bool]:
    """Record every complete 5·15·30분 forecast path for prediction auditing."""
    scored_cases = store.score_ready(quote.symbol, quote.market.value, bars, float(cost_pct))
    live_tick = current_realtime_hub().tick(quote.market, quote.symbol)
    forecast_minutes = {point.minutes for point in getattr(plan, "forecasts", [])}
    if not (
        chart_aligned
        and bool(plan.diagnostics.get("forecast_path_ready"))
        and forecast_minutes == {5, 15, 30}
    ):
        return scored_cases, False
    observed_price = live_tick.price if live_tick is not None else quote.price
    observed_time = live_tick.timestamp if live_tick is not None else quote.timestamp
    price_source = "KIS 체결" if live_tick is not None else "KIS REST"
    if live_tick is None:
        scored_cases += store.capture_rest_snapshot_and_score(
            quote.symbol, quote.market.value, observed_time, observed_price, price_source, APP_VERSION
        )
    if not batch_id and store.has_pending_forecast_audit(quote.market.value, APP_VERSION):
        return scored_cases, False
    case = ValidationCase.from_plan(
        plan,
        observed_price,
        quote.session,
        version=APP_VERSION,
        latest_trade_time=observed_time,
        validation_kind="FORECAST_AUDIT",
        price_source=price_source,
        exchange=exchange,
        batch_id=batch_id,
    )
    _, recorded_case = store.save_once(case, cooldown_seconds=300)
    return scored_cases, recorded_case


def analyze_card(
    symbol: str, market: Market, exchange: str, cost_pct: float, min_score: int, store: ValidationStore,
    validation_batch_id: str = "",
) -> dict[str, Any]:
    """Fetch and analyze one automatic dashboard card from REST history plus live completed bars."""
    rest_quote = load_rest_dashboard_quote(symbol, market.value, exchange)
    quote = quote_with_live_tick(rest_quote)
    bars = merge_live_completed_bars(
        load_bars(symbol, market.value, exchange, CLIENT_CACHE_VERSION), symbol, market
    )
    chart_aligned, chart_alignment_reason = completed_bar_alignment(quote, bars)
    cycle = cycle_store.get(symbol, market, quote.timestamp)
    preliminary = analyze(
        quote, bars, orderbook_required=True, round_trip_cost_pct=cost_pct,
        minimum_score=min_score, cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
    )
    calibration = calibration_for(
        store, market=market.value, session=quote.session, strategy=preliminary.strategy,
        score=preliminary.score, version=APP_VERSION,
    )
    plan = analyze(
        quote, bars, orderbook_required=True, round_trip_cost_pct=cost_pct,
        minimum_score=min_score, cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
        calibration_probability=getattr(calibration, "recent_probability_pct", calibration.probability_pct),
        calibration_samples=getattr(calibration, "recent_samples", calibration.samples),
        calibration_expectancy_pct=getattr(calibration, "recent_average_net_return_pct", calibration.average_net_return_pct if hasattr(calibration, "average_net_return_pct") else None),
    )
    # Complete earlier same-symbol signals first, then persist a real KIS-trade-backed
    # plan. The same path also runs from the one-minute card structure refresh.
    actionable_scored, actionable_recorded = record_and_score_live_validation(
        store, plan, quote, bars, chart_aligned, float(cost_pct)
    )
    forecast_scored, forecast_recorded = record_forecast_accuracy_audit(
        store, plan, quote, bars, chart_aligned, float(cost_pct), exchange, validation_batch_id
    )
    if event_store.configured:
        marker = str(plan.diagnostics.get("completed_bar_at") or quote.timestamp.isoformat())
        cycle_store.apply_risk_state(cycle, plan.risk_state, marker)
    return {
        "quote": quote, "bars": bars, "plan": plan, "exchange": exchange,
        "calibration": calibration.to_dict() if hasattr(calibration, "to_dict") else {
            "samples": calibration.samples, "probability_pct": calibration.probability_pct,
        },
        "validation_recorded": actionable_recorded,
        "validation_scored": actionable_scored + forecast_scored,
        "forecast_validation_recorded": forecast_recorded,
        "chart_aligned": chart_aligned, "chart_alignment_reason": chart_alignment_reason,
    }


def _render_realtime_price_content(symbol: str, market_value: str, exchange: str, base_price: float, previous_close: float, initial_timestamp: str) -> None:
    """Redraw only a card's live price area once per second.

    This fragment reads only the official KIS WebSocket tick. If that connection is
    unavailable, a KIS REST snapshot is refreshed at a rate-limit-safe interval.
    """
    market = Market(market_value)
    tick = display_tick(market, symbol)
    if tick is not None:
        price = tick.price
        timestamp = tick.timestamp
        source = "KIS 체결"
    else:
        try:
            rest_quote = _quote_from_cache_record(_load_quote_record(symbol, market.value, exchange))
            price = rest_quote.price
            timestamp = rest_quote.timestamp
            source = "KIS REST 기준"
        except (KISError, OSError, ValueError):
            price = base_price
            timestamp = datetime.fromisoformat(initial_timestamp)
            source = "KIS 현재가 재수신 대기"
    change_pct = ((price / previous_close) - 1.0) * 100.0 if previous_close > 0 else 0.0
    if is_daily_overheated(price, previous_close):
        # A 1-second tick may cross the guard after the parent card list was made.
        # Re-run the parent once so the final-card filter removes this symbol instead
        # of leaving a stale overheated card on screen for the next minute refresh.
        st.rerun(scope="app")
    st.metric("현재가", price_text(price), f"{change_pct:+.2f}%")
    refresh_at = datetime.now(timestamp.tzinfo).strftime("%H:%M:%S")
    st.caption(f"{source} · KIS 시각 {timestamp.strftime('%H:%M:%S')} · 화면 확인 {refresh_at}")


@st.fragment(run_every=1.0)
def render_realtime_price_1s(*args) -> None:
    _render_realtime_price_content(*args)


@st.fragment(run_every=3.0)
def render_realtime_price_3s(*args) -> None:
    _render_realtime_price_content(*args)


@st.fragment(run_every=5.0)
def render_realtime_price_5s(*args) -> None:
    _render_realtime_price_content(*args)


def render_realtime_price(refresh_seconds: int, *args) -> None:
    {1: render_realtime_price_1s, 3: render_realtime_price_3s, 5: render_realtime_price_5s}[refresh_seconds](*args)


def mixed_card_priority(item: dict[str, Any]) -> tuple[int, int, int]:
    plan = item["plan"]
    decision_rank = {"FINAL_BUY": 3, "PULLBACK_WAIT": 2, "OBSERVATION": 1}.get(card_stage(item), 0)
    trend_rank = 2 if plan.regime == Regime.UP else (1 if plan.regime == Regime.RANGE else 0)
    repeat_rank = 1 if repeat_band_pct(plan) is not None else 0
    return (decision_rank, trend_rank + repeat_rank, plan.score)


def card_trade_status(item: dict[str, Any]) -> str:
    """Map analysis output to one unambiguous live-trading state."""
    plan = item["plan"]
    quote: Quote = item["quote"]
    if quote.change_pct > MAX_DAILY_RISE_PCT:
        return "급등 과열 제외"
    diagnostics = plan.diagnostics
    if bool(diagnostics.get("has_downward_forecast")) or plan.regime == Regime.DOWN:
        return "하방 제외"
    if plan.signal == Signal.BUY and bool(diagnostics.get("long_price_path_confirmed")):
        return "매수 조건 충족"
    if bool((diagnostics.get("strategy_path") or {}).get("pullback_reentry_wait")):
        return "눌림·재매수 대기"
    return "추천 조건 미충족"


def recommendation_quality_passes(plan: Any, levels: dict[str, float | str | bool]) -> bool:
    """Require a usable net target, bounded structural risk, and cost-aware reward/risk."""
    if not bool(levels.get("available")):
        return False
    try:
        entry = float(levels["entry"])
        target1 = float(levels["target1"])
        stop = float(levels["stop"])
        cost_pct = max(0.0, float(plan.diagnostics.get("round_trip_cost_pct") or 0.0))
        reward_risk = float(plan.diagnostics.get("reward_risk_net"))
    except (KeyError, TypeError, ValueError):
        return False
    if entry <= 0 or not 0 < stop < entry < target1:
        return False
    net_upside_pct = (target1 / entry - 1.0) * 100.0 - cost_pct
    stop_distance_pct = (entry / stop - 1.0) * 100.0
    return (
        net_upside_pct >= MIN_NET_TARGET_UPSIDE_PCT
        and reward_risk >= MIN_NET_REWARD_RISK
        and stop_distance_pct <= MAX_STOP_DISTANCE_PCT
    )


def card_ready_for_display(item: dict[str, Any]) -> bool:
    """Return whether this item is an executable FINAL_BUY card."""
    quote = item.get("quote")
    if not isinstance(quote, Quote) or quote.session not in ACTIVE_CARD_SESSIONS:
        return False
    if quote.change_pct > MAX_DAILY_RISE_PCT:
        return False
    if not bool(item.get("chart_aligned")):
        return False
    plan = item["plan"]
    diagnostics = plan.diagnostics
    if not bool(diagnostics.get("forecast_path_ready")):
        return False
    if bool(diagnostics.get("has_downward_forecast")):
        return False
    levels = actionable_display_levels(plan, quote)
    return card_trade_status(item) == "매수 조건 충족" and recommendation_quality_passes(plan, levels)


def card_ready_for_pullback_wait(item: dict[str, Any]) -> bool:
    """Keep an intact trend/range pullback visible without marking it as executable now."""
    quote = item.get("quote")
    if not isinstance(quote, Quote) or quote.session not in ACTIVE_CARD_SESSIONS:
        return False
    if quote.change_pct > MAX_DAILY_RISE_PCT or not bool(item.get("chart_aligned")):
        return False
    plan = item["plan"]
    diagnostics = plan.diagnostics
    strategy_path = diagnostics.get("strategy_path") or {}
    if not bool(diagnostics.get("forecast_path_ready")) or bool(diagnostics.get("has_downward_forecast")):
        return False
    if plan.risk_state in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        return False
    levels = actionable_display_levels(plan, quote)
    return bool(strategy_path.get("pullback_reentry_wait")) and recommendation_quality_passes(plan, levels)


def first_target_reachable(item: dict[str, Any]) -> bool:
    """Identify a structurally reachable first target even when current entry timing is not ready."""
    quote = item.get("quote")
    if not isinstance(quote, Quote) or quote.session not in ACTIVE_CARD_SESSIONS:
        return False
    if quote.change_pct > MAX_DAILY_RISE_PCT or not bool(item.get("chart_aligned")):
        return False
    plan = item["plan"]
    diagnostics = plan.diagnostics
    if not bool(diagnostics.get("forecast_path_ready")) or bool(diagnostics.get("has_downward_forecast")):
        return False
    if plan.risk_state in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        return False
    levels = actionable_display_levels(plan, quote)
    if not recommendation_quality_passes(plan, levels):
        return False
    reachability = diagnostics.get("target_reachability") or {}
    return any(bool((reachability.get(str(minutes)) or {}).get("target1")) for minutes in (5, 15))


def card_stage(item: dict[str, Any]) -> str:
    """Assign one stage without hiding a reachable first-target candidate."""
    if card_ready_for_display(item):
        return "FINAL_BUY"
    if card_ready_for_pullback_wait(item):
        return "PULLBACK_WAIT"
    if first_target_reachable(item):
        return "TARGET1_WAIT"
    return "OBSERVATION"


def observation_reason(item: dict[str, Any]) -> str:
    """Give one concise, prioritized reason why a candidate is not currently executable."""
    quote = item.get("quote")
    if not isinstance(quote, Quote):
        return "시세 데이터 확인 중"
    plan = item["plan"]
    diagnostics = plan.diagnostics
    if quote.change_pct > MAX_DAILY_RISE_PCT:
        return "당일 급등 과열"
    if plan.risk_state in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        return plan.risk_state
    if not bool(item.get("chart_aligned")) or not bool(diagnostics.get("forecast_path_ready")):
        return "데이터·방향 계산 대기"
    if bool(diagnostics.get("has_downward_forecast")):
        return "15·30분 구조 하방"
    if plan.regime == Regime.RANGE and repeat_band_pct(plan) is None:
        return "Swing 반복폭 부족"
    levels = actionable_display_levels(plan, quote)
    if not bool(levels.get("available")):
        return "구조 가격대 재확인"
    if not recommendation_quality_passes(plan, levels):
        try:
            if float(plan.diagnostics.get("reward_risk_net")) < MIN_NET_REWARD_RISK:
                return "손익비 부족"
        except (TypeError, ValueError):
            pass
        return "기대폭·손절거리 재확인"
    point_5 = forecast_point_for(plan, 5)
    if point_5 is not None and point_5.direction == Regime.DOWN:
        return "5분 눌림 중"
    return "재진입 구간 대기"


def candidate_stage_summary(items: list[dict[str, Any]], candidate_pool: int) -> dict[str, Any]:
    """Summarize funnel counts and prioritized observation reasons without fabricated data."""
    reason_counts: dict[str, int] = {}
    data_ready = 0
    swing_ready = 0
    strategy_ready = 0
    reward_ready = 0
    stages = {"FINAL_BUY": 0, "PULLBACK_WAIT": 0, "TARGET1_WAIT": 0, "OBSERVATION": 0}
    for item in items:
        plan = item["plan"]
        quote = item.get("quote")
        stage = card_stage(item)
        stages[stage] += 1
        if bool(item.get("chart_aligned")) and bool(plan.diagnostics.get("forecast_path_ready")):
            data_ready += 1
        if repeat_band_pct(plan) is not None:
            swing_ready += 1
        if plan.regime in {Regime.UP, Regime.RANGE} and bool(plan.diagnostics.get("long_price_path_confirmed")):
            strategy_ready += 1
        if isinstance(quote, Quote) and recommendation_quality_passes(plan, actionable_display_levels(plan, quote)):
            reward_ready += 1
        if stage == "OBSERVATION":
            reason = observation_reason(item)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "funnel": {
            "후보풀": candidate_pool,
            "정밀 분석": len(items),
            "데이터 정상": data_ready,
            "Swing 확인": swing_ready,
            "TREND/RANGE 적합": strategy_ready,
            "손익비·손절 적합": reward_ready,
            "1차 목표 가능": stages["FINAL_BUY"] + stages["PULLBACK_WAIT"] + stages["TARGET1_WAIT"],
            "진입대기": stages["PULLBACK_WAIT"],
            "FINAL_BUY": stages["FINAL_BUY"],
        },
        "stages": stages,
        "reasons": reason_counts,
    }


def observation_rows(items: list[dict[str, Any]], display_limit: int = 10) -> list[dict[str, str]]:
    """Return compact, data-backed observation rows for candidates not ready to trade."""
    rows: list[dict[str, str]] = []
    for item in sorted(items, key=mixed_card_priority, reverse=True):
        if card_stage(item) != "OBSERVATION":
            continue
        quote: Quote = item["quote"]
        plan = item["plan"]
        rows.append({
            "종목": f"{quote.symbol} · {item.get('name') or quote.market.value}",
            "구조": regime_text(plan.regime),
            "5·15·30분": compact_directions(plan),
            "주요 사유": observation_reason(item),
        })
        if len(rows) >= max(1, int(display_limit)):
            break
    return rows


def visible_trade_cards(items: list[dict[str, Any]], display_limit: int = MAX_LIVE_CARDS) -> list[dict[str, Any]]:
    """Show at most five executable FINAL_BUY cards."""
    limit = max(1, min(int(display_limit), MAX_LIVE_CARDS))
    recommendations = [item for item in items if card_ready_for_display(item)]
    recommendations.sort(key=mixed_card_priority, reverse=True)
    return recommendations[:limit]


def visible_pullback_cards(items: list[dict[str, Any]], display_limit: int = MAX_LIVE_CARDS) -> list[dict[str, Any]]:
    """Show at most five structurally intact pullback/re-entry waits."""
    limit = max(1, min(int(display_limit), MAX_LIVE_CARDS))
    waits = [item for item in items if card_ready_for_pullback_wait(item)]
    waits.sort(key=mixed_card_priority, reverse=True)
    return waits[:limit]


def visible_target1_wait_cards(items: list[dict[str, Any]], display_limit: int = MAX_LIVE_CARDS) -> list[dict[str, Any]]:
    """Show first-target-reachable candidates whose exact entry timing is still pending."""
    limit = max(1, min(int(display_limit), MAX_LIVE_CARDS))
    waits = [item for item in items if card_stage(item) == "TARGET1_WAIT"]
    waits.sort(key=mixed_card_priority, reverse=True)
    return waits[:limit]


def live_card_snapshot(item: dict[str, Any], cost_pct: float, min_score: int, store: ValidationStore) -> dict[str, Any]:
    """Recalculate from the selected bounded 1-minute source without KIS polling."""
    base_quote: Quote = item["quote"]
    quote = quote_with_live_tick(base_quote)
    if display_tick(base_quote.market, base_quote.symbol) is None:
        try:
            quote = _quote_from_cache_record(
                _load_quote_record(base_quote.symbol, base_quote.market.value, str(item.get("exchange") or ""))
            )
        except (KISError, OSError, ValueError):
            pass
    bars = merge_live_completed_bars(item["bars"], quote.symbol, quote.market)
    chart_aligned, chart_alignment_reason = completed_bar_alignment(quote, bars)
    cycle = cycle_store.get(quote.symbol, quote.market, quote.timestamp)
    previous_plan = item["plan"]
    plan = analyze(
        quote, bars, orderbook_required=True, round_trip_cost_pct=cost_pct,
        minimum_score=min_score, cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
        calibration_probability=previous_plan.calibration_probability,
        calibration_samples=previous_plan.calibration_samples,
        calibration_expectancy_pct=previous_plan.diagnostics.get("calibration_expectancy_pct"),
    )
    actionable_scored, actionable_recorded = record_and_score_live_validation(
        store, plan, quote, bars, chart_aligned, float(cost_pct)
    )
    forecast_scored, forecast_recorded = record_forecast_accuracy_audit(
        store, plan, quote, bars, chart_aligned, float(cost_pct), str(item.get("exchange") or "")
    )
    return {
        **item, "quote": quote, "bars": bars, "plan": plan,
        "validation_scored": actionable_scored + forecast_scored,
        "validation_recorded": actionable_recorded,
        "forecast_validation_recorded": forecast_recorded,
        "chart_aligned": chart_aligned, "chart_alignment_reason": chart_alignment_reason,
    }


def render_plan_fields(item: dict[str, Any]) -> None:
    """Draw the non-tick fields after each completed live minute bar."""
    quote: Quote = item["quote"]
    plan = item["plan"]
    structure = dashboard_structure(item)
    chart_aligned = bool(item.get("chart_aligned", True))
    levels = actionable_display_levels(plan, quote)
    show_price_structure = chart_aligned and bool(levels.get("available"))
    target_1 = float(levels["target1"]) if show_price_structure else None
    target_2 = float(levels["target2"]) if show_price_structure else None
    support = float(levels["support"]) if show_price_structure else None
    stop = float(levels["stop"]) if show_price_structure else None
    has_downward_forecast = bool(plan.diagnostics.get("has_downward_forecast"))
    forecast_path_ready = bool(plan.diagnostics.get("forecast_path_ready"))
    if chart_aligned and has_downward_forecast:
        observation_text = "하방 예상 · 상방 가격 추천 없음"
    elif chart_aligned and not forecast_path_ready:
        observation_text = "방향 재계산 중"
    else:
        observation_text = "현재가 위 목표 구조 재확인 중"
    st.markdown(structure_strip_html(structure), unsafe_allow_html=True)
    forecast_columns = st.columns(3)
    for column, minutes in zip(forecast_columns, (5, 15, 30)):
        point = forecast_point_for(plan, minutes) if chart_aligned else None
        column.metric(f"{minutes}분 예상", price_text(point.base if point else None))
        direction = forecast_direction_text(point)
        if point is not None and point.direction == Regime.DOWN:
            direction = f"▼ {direction}"
        elif point is not None and point.direction == Regime.UP:
            direction = f"▲ {direction}"
        column.caption(direction)
    prices = st.columns(2)
    prices[0].metric(
        "추천 매수가",
        price_text(float(levels["entry"])) if show_price_structure else (observation_text if chart_aligned else "완료 분봉 확인 중"),
    )
    prices[1].metric("추천 매도가 1차", price_text(target_1) if show_price_structure else observation_text)
    exits = st.columns(3)
    exits[0].metric("추천 매도가 2차", price_text(target_2) if show_price_structure else observation_text)
    exits[1].metric("현재 차트 지지", price_text(support) if show_price_structure else observation_text)
    exits[2].metric("손절가", price_text(stop) if show_price_structure else observation_text)
    if show_price_structure:
        st.caption(str(levels["basis"]))


@st.fragment(run_every=60.0)
def render_live_plan_fields(item: dict[str, Any], cost_pct: float, min_score: int, store: ValidationStore) -> None:
    render_plan_fields(live_card_snapshot(item, cost_pct, min_score, store))


@st.fragment(run_every=60.0)
def run_hidden_forecast_validation(items: list[dict[str, Any]], cost_pct: float, min_score: int, store: ValidationStore) -> None:
    """Keep visible FINAL_BUY and pullback/re-entry candidates in the forecast audit."""
    for item in items:
        live_card_snapshot(item, cost_pct, min_score, store)


@st.fragment(run_every=60.0)
def capture_pending_forecast_paths(store: ValidationStore, market_value: str) -> None:
    """Capture only 5·15·30-minute boundaries due in this minute."""
    market = Market(market_value)
    for case in store.due_forecast_audits(
        market.value, datetime.now().astimezone(), version=APP_VERSION, limit=MAX_PENDING_FORECAST_WATCHES
    ):
        tick = display_tick(market, case.symbol)
        if tick is not None:
            store.capture_rest_snapshot_and_score(
                case.symbol, market.value, tick.timestamp, tick.price, "KIS 체결", APP_VERSION
            )
            continue
        try:
            quote = _quote_from_cache_record(_load_quote_record(case.symbol, market.value, case.exchange))
        except (KISError, OSError, ValueError, KeyError):
            continue
        store.capture_rest_snapshot_and_score(
            case.symbol, market.value, quote.timestamp, quote.price, "KIS REST", APP_VERSION
        )


def free_validation_batch_state(market: Market, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Create one browser-open, persistent-session batch from the market candidate pool."""
    key = f"free_validation_batch_{market.value}"
    current_symbols = [str(item.get("symbol") or "").upper() for item in candidates[:FREE_VALIDATION_BATCH_SIZE]]
    state = st.session_state.get(key)
    if not isinstance(state, dict) or state.get("symbols") != current_symbols:
        state = {
            "batch_id": f"free-{market.value.lower()}-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')}",
            "symbols": current_symbols,
            "next_index": 0,
        }
        st.session_state[key] = state
    return state


@st.fragment(run_every=60.0)
def run_free_validation_batch(
    market_value: str, candidates: list[dict[str, Any]], cost_pct: float, min_score: int, store: ValidationStore,
) -> None:
    """Start a small new cohort each minute while the free Streamlit screen stays open."""
    market = Market(market_value)
    state = free_validation_batch_state(market, candidates)
    start = int(state.get("next_index", 0))
    batch_candidates = candidates[:FREE_VALIDATION_BATCH_SIZE]
    selected = batch_candidates[start:start + FREE_VALIDATION_STARTS_PER_MINUTE]
    started = 0
    for candidate in selected:
        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol:
            continue
        exchange = str(candidate.get("exchange") or ("NAS" if market == Market.US else ""))
        try:
            before = len(store.pending_forecast_audits(market.value, version=APP_VERSION, limit=100, batch_id=str(state["batch_id"])))
            analyze_card(symbol, market, exchange, cost_pct, min_score, store, validation_batch_id=str(state["batch_id"]))
            after = len(store.pending_forecast_audits(market.value, version=APP_VERSION, limit=100, batch_id=str(state["batch_id"])))
            started += int(after > before)
        except (KISError, OSError, ValueError, KeyError):
            continue
    state["next_index"] = min(len(batch_candidates), start + FREE_VALIDATION_STARTS_PER_MINUTE)
    st.session_state[f"free_validation_batch_{market.value}"] = state
    cases = [
        case for case in store.cases()
        if case.validation_kind == "FORECAST_AUDIT" and case.batch_id == str(state["batch_id"])
    ]
    complete = sum(case.data_completeness == "COMPLETE" for case in cases)
    st.caption(
        f"100개 예측 검증 진행 · 시작 {len(cases)}/{len(batch_candidates)} · 30분 완료 {complete}/{len(batch_candidates)} · "
        f"이번 분 시작 {started}개 · 화면을 열어 두면 다음 표본을 이어 기록합니다."
    )


def latest_completed_forecast_case(store: ValidationStore, version: str) -> Any | None:
    """Return the newest fully scored forecast audit for the active rule version only."""
    completed = [
        case for case in store.cases()
        if case.validation_kind == "FORECAST_AUDIT"
        and case.version == version
        and case.data_completeness == "COMPLETE"
        and case.full_path_pass is not None
    ]
    return max(completed, key=lambda case: case.signal_time) if completed else None


@st.fragment(run_every=60.0)
def render_latest_forecast_result(store: ValidationStore) -> None:
    """Keep the latest completed 30-minute path visible without a full dashboard rerun."""
    case = latest_completed_forecast_case(store, APP_VERSION)
    if case is None:
        return
    outcome = "통과" if case.full_path_pass else "실패"
    st.subheader(f"최근 30분 실측 결과 · {case.symbol} · {outcome}")
    st.caption(f"신호 시각 {case.signal_time[11:19]} · 가격 원천 {case.price_source} · {case.forecast_path_direction} 예측")
    rows = [
        {
            "구간": f"{horizon.minutes}분",
            "예측가": price_text(horizon.predicted_base),
            "실제가": price_text(horizon.actual),
            "방향": "맞음" if horizon.direction_pass else "틀림",
            "예측 범위": "통과" if horizon.range_pass else "범위 이탈",
        }
        for horizon in case.horizons
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_live_card(
    item: dict[str, Any], cost_pct: float, min_score: int, store: ValidationStore,
    refresh_seconds: int, stage: str = "FINAL_BUY",
) -> None:
    """Render a price-first card with 1-second tick and 1-minute structure refreshes."""
    quote: Quote = item["quote"]
    title = f"{quote.symbol} · {item.get('name') or quote.market.value}"
    with st.container(border=True, key=f"card_{quote.market.value}_{quote.symbol}"):
        # Streamlit heading anchors may be recycled when switching markets,
        # producing links to a previous card. A keyed plain title has no anchor
        # state and remains bound to the correct symbol.
        st.markdown(f"<div class='ticker'>{html.escape(title)}</div>", unsafe_allow_html=True)
        if stage == "PULLBACK_WAIT":
            trigger = str((item["plan"].diagnostics.get("strategy_path") or {}).get("reentry_trigger") or "5분 반전 확인")
            st.warning(f"1차 목표 가능 · 눌림 진입 대기 · {trigger}")
        elif stage == "TARGET1_WAIT":
            st.info("1차 목표 도달 가능 · 현재 추격 진입은 대기하고 표시 재매수가까지 되돌림을 확인")
        else:
            st.success("현재 1회 진입 조건 통과 · 1차·2차 목표가와 손절가를 함께 확인")
        render_realtime_price(
            refresh_seconds,
            quote.symbol,
            quote.market.value,
            str(item.get("exchange") or ""),
            quote.price,
            quote.previous_close,
            quote.timestamp.isoformat(),
        )
        render_live_plan_fields(item, cost_pct, min_score, store)


def render_card_detail(item: dict[str, Any]) -> None:
    quote: Quote = item["quote"]
    plan = item["plan"]
    with st.expander(f"{quote.symbol} 카드 자세히 보기"):
        repeat_box = plan.repeat_box
        if repeat_box:
            low, high = repeat_box
            st.caption(f"반복박스: {price_text(low)} ~ {price_text(high)}")
        st.write("대기·경고 이유")
        for reason in plan.reasons or ["특별 경고 없음"]:
            st.write(f"- {reason}")
        st.caption(f"1차 목표 근거: {plan.target_basis or '구조 미확인'} · 2차 목표 근거: {plan.target2_basis or '구조 미확인'}")
        st.caption(f"전체 경로 검증: {plan.calibration_probability:.1f}%" if plan.calibration_probability is not None else f"전체 경로 검증 표본: {plan.calibration_samples}/100")
        diagnostics = plan.diagnostics
        data_quality = diagnostics.get("data_quality", {})
        st.caption(
            f"데이터 품질: {data_quality.get('status', '미확인')} · 완료 1분봉 {data_quality.get('completed_minute_bars', 0)}개 · "
            f"시장 상태: {diagnostics.get('market_state', 'TRANSITION')} · 위험 상태: {plan.risk_state}"
        )
        direction_engines = diagnostics.get("direction_engines", {})
        if isinstance(direction_engines, dict) and direction_engines:
            rows = [
                {
                    "시간": f"{minutes}분",
                    "상태": detail.get("state", ""),
                    "방향 점수": f"{float(detail.get('score', 0)):+.2f}",
                    "예상 이동폭": f"{float(detail.get('expected_move_pct', 0)):+.2f}%",
                    "변동 범위": f"±{float(detail.get('move_range_pct', 0)):.2f}%",
                }
                for minutes, detail in sorted(direction_engines.items(), key=lambda pair: int(pair[0]))
                if isinstance(detail, dict)
            ]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        invalidation = diagnostics.get("direction_invalidation", {})
        if isinstance(invalidation, dict):
            st.caption(
                f"방향 무효화·감쇠: {invalidation.get('risk_state', plan.risk_state)} · "
                f"Pattern Fatigue {invalidation.get('pattern_fatigue', 0)} · 지속성 {invalidation.get('persistence_score', '미산출')}"
            )
        if not item["bars"].empty:
            render_chart(item["bars"].tail(120), plan, key=f"chart_{quote.market.value}_{quote.symbol}")


@st.fragment(run_every=300)
def render_new_candidate_watchlist(market_value: str, fixed_symbols: tuple[str, ...]) -> None:
    """Refresh only replacement candidates; open cards and direct searches remain fixed."""
    market = Market(market_value)
    try:
        fresh_candidates = load_dashboard_candidates(market.value, APP_VERSION)
    except KISError:
        st.caption("새 후보 감시 목록은 다음 갱신 때 다시 확인합니다.")
        return
    fixed = {symbol.upper() for symbol in fixed_symbols}
    replacements = [candidate for candidate in fresh_candidates if str(candidate.get("symbol", "")).upper() not in fixed]
    rows = [
        {
            "종목": f"{candidate.get('symbol')} · {candidate.get('name') or ''}",
            "순위 조회가": price_text(float(candidate["price"])) if isinstance(candidate.get("price"), (int, float)) else "미확인",
            "등락률": f"{float(candidate['change_pct']):+.2f}%" if isinstance(candidate.get("change_pct"), (int, float)) else "미확인",
            "거래대금": money(float(candidate.get("turnover") or 0.0)),
        }
        for candidate in replacements[:MAX_CANDIDATE_LIST]
    ]
    st.subheader(f"1차 순위 교체 후보(상세분석 전) · {len(rows)}개")
    st.caption("5분마다 순위·가격만 확인합니다. 매수 후보가 아니며 상세 차트 분석 통과 전에는 진입하지 않습니다.")
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("새로 교체할 후보가 아직 없습니다.")


def render_chart(bars: pd.DataFrame, plan: Any, key: str) -> None:
    fig = go.Figure(go.Candlestick(x=bars.index, open=bars.open, high=bars.high, low=bars.low, close=bars.close, name="1분봉"))
    for value, name, color, dash in (
        (plan.entry, "진입", "#6ba7ff", "solid"),
        (plan.target, "1차", "#55d580", "solid"),
        (plan.target2, "2차", "#55d580", "dash"),
        (plan.soft_stop, "Soft", "#ffd66b", "dash"),
        (plan.hard_stop or plan.invalidation or plan.stop, "Hard", "#ff7d7d", "solid"),
    ):
        if value is not None:
            fig.add_hline(y=value, line_color=color, line_dash=dash, annotation_text=f"{name} {price_text(value)}")
    fig.update_layout(height=320, margin=dict(l=4, r=4, t=24, b=4), xaxis_rangeslider_visible=False, paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", font_color="#172033")
    fig.update_xaxes(gridcolor="#edf1f6")
    fig.update_yaxes(gridcolor="#edf1f6")
    st.plotly_chart(fig, width="stretch", key=key)


client = current_client()
realtime_hub = current_realtime_hub()
active_secret_fingerprint = current_secret_fingerprint()
event_store = get_event_store(active_secret_fingerprint)
cycle_store = get_cycle_store(active_secret_fingerprint)
store = ValidationStore(VALIDATION_ROOT, event_store=event_store)

with st.sidebar:
    st.title("실시간 설정")
    initial_market_label = default_market_label()
    market_label = st.radio(
        "시장",
        ["국내", "미국"],
        index=1 if initial_market_label == "미국" else 0,
        horizontal=True,
        key="market_selector",
    )
    market = Market.KR if market_label == "국내" else Market.US
    search_query = st.text_input(
        "관심 종목 바로 보기",
        placeholder="국내: 현대차 또는 005380 · 미국: NVDA",
    ).strip()
    cost_default = 0.05 if market == Market.KR else 0.10
    cost_pct = st.number_input("왕복비용 가정(%)", min_value=0.0, max_value=5.0, value=cost_default, step=0.01)
    min_score = st.slider("최소 신호 점수", min_value=60, max_value=100, value=80, step=5)
    refresh_seconds = int(st.radio("현재가 화면 갱신", [1, 3, 5], horizontal=True, format_func=lambda value: f"{value}초"))
    candidate_card_count = MAX_LIVE_CARDS
    st.caption("거래량 TOP100 ∪ 거래대금 TOP100 후보풀 → 빠른 선별 15개 → 정밀 분석 최대 8개 → 실시간 핵심 카드 최대 5개")
    sidebar_session = market_session(market)
    if sidebar_session not in ACTIVE_CARD_SESSIONS:
        st.info("현재 거래 시간이 아닙니다")
    elif client.ready:
        st.success("실시간 후보 자동 분석 중")
    else:
        st.warning("한국투자증권 연결 대기 중")
        st.caption(" · ".join(f"{name}: {value}" for name, value in client.connection_diagnostics.items()))
    budget = client.budget_status
    st.caption(f"호출 보호: 1분 {budget.minute_used}/{budget.minute_limit} · 5시간 {budget.five_hour_used}/{budget.five_hour_limit}")
    st.caption(realtime_hub.status_label())
    if realtime_hub.last_error:
        st.caption(f"실시간 연결 원인: {realtime_hub.last_error[:160]}")
    if event_store.configured:
        st.caption("검증 성과 저장: 영구 보관 연결됨")
    else:
        st.caption("검증 성과 저장: 앱 재시작 시 초기화될 수 있음 · [한 번만 설정하는 안내](https://github.com/yumi0531mi-cmd/ymym/blob/main/docs/supabase_persistent_validation_setup.md)")
    st.caption(f"화면 현재가 {refresh_seconds}초 확인 · KIS WebSocket 체결 우선 · 미연결 시 REST 12초 안전 대체 · 완료봉 구조 1분")

kis_connected = client.ready
# KIS ticks are redrawn by each card's independent Streamlit fragment.  Do not
# rerun the entire dashboard here: a full rerun causes visible white flashing.

st.markdown("<div class='mobile-head'><h1>실시간 상승 차트 스캐너</h1><p>현재 1회 진입 · 1차·2차 목표가 · 차트 지지 · 손절가 · 5·15·30분 구조</p></div>", unsafe_allow_html=True)

cards: list[dict[str, Any]] = []
errors: list[str] = []
candidates: list[dict[str, Any]] = []
visible_requests: list[dict[str, Any]] = []
fixed_symbols: tuple[str, ...] = ()
direct_request: dict[str, str] | None = None
if search_query:
    if market == Market.KR:
        matches = search_kr_stock(search_query)
        if len(matches) == 1:
            direct_request = {"symbol": matches[0]["symbol"], "name": matches[0]["name"], "exchange": ""}
        elif len(matches) > 1:
            labels = [f"{match['name']} · {match['symbol']}" for match in matches]
            chosen = st.selectbox("국내 검색 결과", labels)
            chosen_match = matches[labels.index(chosen)]
            direct_request = {"symbol": chosen_match["symbol"], "name": chosen_match["name"], "exchange": ""}
        else:
            st.warning("국내 종목명을 찾지 못했습니다. 예: 현대차, 삼성전자, 005380")
    else:
        ticker = search_query.upper().replace(" ", "")
        if ticker.replace(".", "").replace("-", "").isalnum():
            direct_request = {"symbol": ticker, "name": ticker, "exchange": us_exchange_for(ticker)}
        else:
            st.warning("미국 종목은 티커로 입력해 주세요. 예: NVDA, AAPL, SOXL")
current_session = market_session(market)
if current_session not in ACTIVE_CARD_SESSIONS:
    closed_message = (
        "국내 정규장이 종료되었습니다. 다음 정규장에는 분석이 완료된 후보 카드만 표시합니다."
        if market == Market.KR
        else "현재는 지원하는 미국 거래 세션이 아닙니다. 주간·프리·정규·애프터 시간에 분석 카드가 표시됩니다."
    )
    st.info(closed_message)
elif not kis_connected:
    st.markdown("<div class='connection wait'>한국투자증권 연결을 기다리고 있습니다. 실제 가격과 매매 레벨은 연결된 데이터가 있을 때만 표시합니다.</div>", unsafe_allow_html=True)
    st.info("연결 확인 — " + " · ".join(f"{name}: {value}" for name, value in client.connection_diagnostics.items()))
else:
    try:
        with st.spinner("거래량 TOP100 ∪ 거래대금 TOP100 후보풀에서 상승 차트를 자동으로 찾는 중…"):
            candidates = load_dashboard_candidates(market.value, APP_VERSION)
            requests: list[dict[str, Any]] = []
            if direct_request is not None:
                requests.append({**direct_request, "candidate_source": "관심 종목 직접 검색"})
            fast_shortlist = fast_shortlist_candidates(candidates, market)
            auto_slots = max(0, MAX_ANALYSIS_CANDIDATES - (1 if direct_request is not None else 0))
            for candidate in fast_shortlist[:auto_slots]:
                if direct_request is not None and str(candidate["symbol"]) == direct_request["symbol"]:
                    continue
                requests.append(candidate)
            visible_requests = requests[:MAX_ANALYSIS_CANDIDATES]
            for candidate in visible_requests:
                symbol = str(candidate["symbol"])
                exchange = str(candidate.get("exchange") or ("NAS" if market == Market.US else ""))
                try:
                    card = analyze_card(symbol, market, exchange, float(cost_pct), int(min_score), store)
                    card["name"] = str(candidate.get("name") or symbol)
                    card["candidate_source"] = str(candidate.get("candidate_source") or "거래량 TOP100 ∪ 거래대금 TOP100")
                    cards.append(card)
                except KISError as exc:
                    errors.append(f"{symbol}: {str(exc)[:180]}")
                except (ValueError, KeyError, OSError) as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}")
                except Exception as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}")
    except KISError:
        st.error("실시간 후보를 가져오지 못했습니다. 잠시 뒤 화면이 자동으로 다시 확인합니다.")

if candidates:
    fixed_symbols = tuple(
        dict.fromkeys(
            [str(request.get("symbol", "")) for request in visible_requests]
            + [str(card["quote"].symbol) for card in cards]
        )
    )

if cards:
    analyzed_count = len(cards)
    blocked_cards = [card for card in cards if card_trade_status(card) == "하방 제외"]
    all_analyzed_cards = list(cards)
    cards = visible_trade_cards(all_analyzed_cards, candidate_card_count)
    pullback_cards = visible_pullback_cards(all_analyzed_cards, candidate_card_count)
    target1_wait_cards = visible_target1_wait_cards(all_analyzed_cards, candidate_card_count)
    entry_wait_cards = list({id(card): card for card in [*pullback_cards, *target1_wait_cards]}.values())
    analysis_cards = list({id(card): card for card in [*cards, *entry_wait_cards]}.values())
    stage_summary = candidate_stage_summary(all_analyzed_cards, len(candidates))
else:
    analysis_cards = []
    pullback_cards = []
    target1_wait_cards = []
    entry_wait_cards = []
    stage_summary = candidate_stage_summary([], len(candidates))

realtime_hub.configure(
    (
        card["quote"].market,
        str(card["quote"].symbol),
        str(card.get("exchange") or ("NAS" if card["quote"].market == Market.US else "")),
    )
    for card in analysis_cards
)

if analysis_cards:
    run_hidden_forecast_validation(analysis_cards, float(cost_pct), int(min_score), store)

capture_pending_forecast_paths(store, market.value)

if candidates and event_store.configured:
    run_free_validation_batch(market.value, candidates, float(cost_pct), int(min_score), store)
elif candidates:
    st.caption("100개 예측 검증은 영구 저장 연결이 필요합니다. 현재는 앱 재시작 시 기록이 사라질 수 있어 시작하지 않습니다.")

if cards:
    updated_at = max(card["quote"].timestamp for card in cards)
    source_labels = {str(card.get("candidate_source") or "거래량 TOP100 ∪ 거래대금 TOP100") for card in cards}
    source_text = " · ".join(sorted(source_labels))
    st.subheader(f"상승 차트 · 현재 1회 진입 가능 {len(cards)}종목")
    st.caption(f"정밀 분석 {analyzed_count}개 중 현재 진입 조건 통과 {len(cards)}개 · {updated_at.strftime('%H:%M:%S')} · {realtime_hub.status_label()} · {source_text}")
    for card_item in cards:
        render_live_card(card_item, float(cost_pct), int(min_score), store, refresh_seconds, "FINAL_BUY")
elif kis_connected and not errors and not candidates:
    limit_text = "30만 원 미만" if market == Market.KR else "200달러 미만"
    st.info(f"현재 {limit_text} 가격 상한 안의 거래량·거래대금 후보풀을 받지 못했습니다. 순위 API를 다음 갱신 때 다시 확인합니다.")

if candidates and not cards:
    st.info("현재 상승 차트에서 지금 1회 진입 조건을 모두 통과한 종목은 없습니다.")

if candidates:
    st.caption(
        f"후보풀 {len(candidates)}종목 · 빠른 선별 {len(fast_shortlist) if 'fast_shortlist' in locals() else 0}종목 · "
        f"정밀 분석 {stage_summary['funnel']['정밀 분석']}종목 · 1차 목표 가능 {stage_summary['funnel']['1차 목표 가능']}종목 · "
        f"현재 1회 진입 {stage_summary['stages']['FINAL_BUY']}종목"
    )

if entry_wait_cards:
    st.subheader(f"1차 익절 가능 · 현재 진입 대기 {len(entry_wait_cards)}종목")
    st.caption("목표 구조는 남아 있으나 현재 위치가 추격 구간이거나 5분 눌림 진행 중입니다. 각 카드의 추천 매수가가 재진입 참고 가격입니다.")
    for card_item in entry_wait_cards:
        render_live_card(card_item, float(cost_pct), int(min_score), store, refresh_seconds, card_stage(card_item))

with st.expander("검증·관찰 상세", expanded=False):
    render_latest_forecast_result(store)
    if stage_summary["funnel"]:
        funnel = " → ".join(f"{label} {count}" for label, count in stage_summary["funnel"].items())
        st.caption(f"단계 통과 현황 · {funnel}")
        reason_rows = [
            {"주요 탈락 이유": reason, "종목 수": count}
            for reason, count in sorted(stage_summary["reasons"].items(), key=lambda item: (-item[1], item[0]))[:5]
        ]
        if reason_rows:
            st.subheader(f"관찰 후보 · {stage_summary['stages']['OBSERVATION']}종목")
            st.dataframe(pd.DataFrame(reason_rows), hide_index=True, width="stretch")
            rows = observation_rows(all_analyzed_cards)
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

for error in errors:
    st.warning(f"일부 후보는 분석 데이터를 만들지 못했습니다: {error}")
