from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from scanner.calibration import calibration_for
from scanner.cycle import CycleStore
from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError, secrets_fingerprint
from scanner.market_screener import merge_rankings
from scanner.models import Market, Quote, Regime, Signal
from scanner.persistence import EventStore
from scanner.realtime import KISRealtimeHub
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationCase, ValidationStore

APP_VERSION = "5.6-kis-realtime"
# Bump this whenever the cached KISClient interface changes. Streamlit can retain a
# resource through a hot code update, so a new contract must never reuse an old client.
CLIENT_CACHE_VERSION = "client-contract-v7-kis-realtime"
VALIDATION_ROOT = Path(".scanner_data/validation")
MAX_LIVE_CARDS = 5
MAX_CANDIDATE_LIST = 20
KR_PRICE_CEILING = 300_000.0
US_PRICE_CEILING = 170.0
KR_SEARCH_INDEX_PATH = Path("data/kr_stock_index.json")

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


@st.cache_resource
def get_realtime_hub(cache_version: str, secret_fingerprint: str) -> KISRealtimeHub:
    return KISRealtimeHub(get_client(cache_version, secret_fingerprint))


@st.cache_resource
def get_event_store() -> EventStore:
    return EventStore(st.secrets)


@st.cache_resource
def get_cycle_store() -> CycleStore:
    return CycleStore(get_event_store())


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


@st.cache_data(ttl=120, show_spinner=False)
def _load_quote_record(symbol: str, market_value: str, exchange: str) -> dict[str, object]:
    # Last price is the only per-card request made every minute.
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


def load_dashboard_quote(symbol: str, market_value: str, exchange: str) -> Quote:
    quote = _quote_from_cache_record(_load_quote_record(symbol, market_value, exchange))
    bid, ask = _load_orderbook(symbol, market_value, exchange)
    tick = get_realtime_hub(CLIENT_CACHE_VERSION, current_secret_fingerprint()).tick(
        Market(market_value), symbol
    )
    if tick is None:
        return Quote(
            symbol=quote.symbol, market=quote.market, price=quote.price,
            previous_close=quote.previous_close, timestamp=quote.timestamp,
            bid=bid, ask=ask, volume=quote.volume, turnover=quote.turnover,
            session=quote.session, source=quote.source,
        )
    return Quote(
        symbol=quote.symbol, market=quote.market, price=tick.price,
        previous_close=quote.previous_close, timestamp=tick.timestamp,
        bid=tick.bid if tick.bid is not None else bid,
        ask=tick.ask if tick.ask is not None else ask,
        volume=tick.volume if tick.volume is not None else quote.volume,
        turnover=quote.turnover, session=quote.session, source=tick.source,
    )


@st.cache_data(ttl=900, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    # Completed 1-minute bars are refreshed every 15 minutes. Current price above
    # remains separate so the card stays responsive without rebuilding structure on every refresh.
    return get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).intraday(
        symbol, Market(market_value), exchange
    )


def price_ceiling(market: Market) -> float:
    return KR_PRICE_CEILING if market == Market.KR else US_PRICE_CEILING


def eligible_price(candidate: dict[str, Any], market: Market) -> bool:
    try:
        price = float(candidate.get("price") or 0.0)
        change_pct = float(candidate.get("change_pct") or 0.0)
    except (TypeError, ValueError):
        return False
    return 0 < price < price_ceiling(market) and change_pct > 0


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


@st.cache_data(ttl=1800, show_spinner=False)
def load_dashboard_candidates(market_value: str) -> list[dict[str, Any]]:
    """Return up to 20 rising price-eligible candidates without detailed analysis yet."""
    market = Market(market_value)
    client = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint())
    try:
        rankings = client.market_rankings(market)
        candidates = [candidate.to_dict() for candidate in merge_rankings(market, rankings, limit=MAX_CANDIDATE_LIST)]
        for candidate in candidates:
            candidate["candidate_source"] = "시장 실시간 순위"
        ranked = sort_rising_candidates(candidates, market)
        if ranked:
            return ranked[:MAX_CANDIDATE_LIST]
    except KISError:
        # Ranking availability differs by market session and account entitlement.
        # A transparent liquid-list fallback avoids a blank first screen.
        pass

    items = KR_LIQUID if market == Market.KR else US_LIQUID
    fallback: list[dict[str, Any]] = []
    for item in items:
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
        ]


def search_kr_stock(query: str, limit: int = 6) -> list[dict[str, str]]:
    needle = query.strip().replace(" ", "")
    if not needle:
        return []
    items = load_kr_search_index()
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


def strategy_signal_text(plan: Any) -> str:
    """Present strategy status without labelling an untested condition as 80% verified."""
    if plan.signal == Signal.BUY and plan.calibration_probability is not None and plan.calibration_probability >= 80.0:
        return "강한 매수 검토 · 실측 80% 이상"
    if plan.signal == Signal.BUY:
        return "매수 검토 신호 · 진입 조건 충족"
    if plan.signal == Signal.WAIT and plan.calibration_samples >= 30 and plan.calibration_probability is not None and plan.calibration_probability < 80.0:
        return "진입 대기 · 실측 80% 미만"
    mapping = {
        Signal.WAIT: "진입 대기",
        Signal.BLOCK: "진입 차단",
        Signal.SELL: "청산 검토",
        Signal.UNVERIFIED: "데이터 확인 대기",
    }
    return mapping.get(plan.signal, str(plan.signal.value))


def analyze_card(symbol: str, market: Market, exchange: str, cost_pct: float, min_score: int, store: ValidationStore) -> dict[str, Any]:
    """Fetch and analyze one automatic dashboard card (maximum three)."""
    quote = load_dashboard_quote(symbol, market.value, exchange)
    bars = load_bars(symbol, market.value, exchange)
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
        calibration_probability=calibration.probability_pct, calibration_samples=calibration.samples,
    )
    # Complete earlier same-symbol signals first, then record a new fully specified
    # entry/target/stop signal. This supplies the real target-before-stop outcomes
    # used by the strategy-specific 80% calibration; no synthetic history is created.
    scored_cases = store.score_ready(symbol, market.value, bars, float(cost_pct))
    recorded_case = False
    if plan.signal == Signal.BUY and plan.entry and plan.target and plan.hard_stop:
        case = ValidationCase.from_plan(plan, quote.price, quote.session, version=APP_VERSION)
        _, recorded_case = store.save_once(case, cooldown_seconds=300)
    if event_store.configured:
        marker = str(plan.diagnostics.get("completed_bar_at") or quote.timestamp.isoformat())
        cycle_store.apply_risk_state(cycle, plan.risk_state, marker)
    return {
        "quote": quote, "bars": bars, "plan": plan, "exchange": exchange,
        "validation_recorded": recorded_case, "validation_scored": scored_cases,
    }


def trade_card_html(item: dict[str, Any], cost_pct: float) -> str:
    quote: Quote = item["quote"]
    plan = item["plan"]
    change = quote.change_pct
    change_class = "up" if change > 0 else "down" if change < 0 else "flat"
    diagnostics = plan.diagnostics
    reasons = list(plan.reasons or [])[:2]
    flags = diagnostics.get("false_signal_flags") or []
    warnings = reasons + [str(flag) for flag in flags[:1]]
    warning_html = "".join(f"<div>⚠ {html.escape(str(reason))}</div>" for reason in warnings)
    if not warning_html:
        warning_html = "<div>현재 특별 경고는 확인되지 않았습니다.</div>"
    spread = diagnostics.get("spread_pct")
    rvol = diagnostics.get("rvol")
    rr_value = diagnostics.get("reward_risk_net")
    volume = quote.volume
    turnover = quote.turnover
    hard_stop = plan.hard_stop or plan.invalidation or plan.stop
    persistence = plan.persistence_score
    repeat_width = repeat_band_pct(plan)
    regime_badge = "상승 추세" if plan.regime == Regime.UP else ("박스권" if plan.regime == Regime.RANGE else "전환·관망")
    repeat_badge = (
        f"<span class='badge repeat'>반복단타 가능 {repeat_width:.2f}%</span>"
        if repeat_width is not None
        else "<span class='badge'>추세 진입 관찰</span>"
    )
    card = f"""
<section class="trade-card">
      <div class="card-top">
    <div><div class="ticker">{html.escape(quote.symbol)}</div><div class="name">{html.escape(str(item.get('name') or quote.market.value))} · {html.escape(quote.session)} · {html.escape(plan.strategy)}</div></div>

    <div><div class="price">{price_text(quote.price)}</div><div class="change {change_class}">{change:+.2f}%</div></div>
  </div>
  <div class="badges">
    <span class="badge {signal_class(plan.signal)}">{html.escape(plan.signal.value)}</span>
    <span class="badge trend">{regime_badge}</span>
    {repeat_badge}
    <span class="badge risk">위험: {html.escape(plan.risk_state)}</span>
    <span class="badge">점수 {plan.score}/100</span>
    <span class="badge">지속성 {persistence if persistence is not None else '미확인'}</span>
  </div>
  <div class="warn-box">{warning_html}</div>
  <div class="data-grid">
    <div class="data-item"><div class="data-label">거래량</div><div class="data-value">{money(volume)}</div></div>
    <div class="data-item"><div class="data-label">거래대금</div><div class="data-value">{money(turnover)}</div></div>
    <div class="data-item"><div class="data-label">상대거래량</div><div class="data-value">{number_text(rvol, 'x')}</div></div>
    <div class="data-item"><div class="data-label">호가 스프레드</div><div class="data-value">{number_text(spread, '%')}</div></div>
  </div>
  <div class="plan-title">매매 레벨 <span style="font-size:.72rem;color:#64748b">수동매매 참고값</span></div>
  <div class="plan-grid">
    <div class="plan-item"><div class="data-label">진입 기준가</div><div class="data-value entry">{price_text(plan.entry)}</div></div>
    <div class="plan-item"><div class="data-label">1차 목표 · 5분</div><div class="data-value target">{price_text(plan.target)}</div></div>
    <div class="plan-item"><div class="data-label">2차 목표 · 5분</div><div class="data-value target">{price_text(plan.target2)}</div></div>
    <div class="plan-item"><div class="data-label">Soft Stop</div><div class="data-value stop">{price_text(plan.soft_stop)}</div></div>
    <div class="plan-item"><div class="data-label">Hard Stop</div><div class="data-value stop">{price_text(hard_stop)}</div></div>
    <div class="plan-item"><div class="data-label">비용 반영 손익비</div><div class="data-value">{number_text(rr_value)}</div></div>
  </div>
  <div class="card-foot">1차·2차 목표는 완료된 5분봉 구조를 우선 사용합니다. 왕복비용 가정 {cost_pct:.2f}% · 주문 기능 없음</div>
</section>
"""
    return card


def mixed_card_priority(item: dict[str, Any]) -> tuple[int, int, int]:
    plan = item["plan"]
    trend_rank = 2 if plan.regime == Regime.UP else (1 if plan.regime == Regime.RANGE else 0)
    repeat_rank = 1 if repeat_band_pct(plan) is not None else 0
    return (trend_rank, repeat_rank, plan.score)


def render_live_card(item: dict[str, Any], cost_pct: float) -> None:
    """Render a safe native Streamlit card so live values never appear as HTML text."""
    quote: Quote = item["quote"]
    plan = item["plan"]
    hard_stop = plan.hard_stop or plan.invalidation or plan.stop
    repeat_width = repeat_band_pct(plan)
    state = "상승 추세" if plan.regime == Regime.UP else ("박스권" if plan.regime == Regime.RANGE else "전환·관망")
    repeat_text = f"반복단타 가능 · 폭 {repeat_width:.2f}%" if repeat_width is not None else "추세 진입 관찰"
    title = f"{quote.symbol} · {item.get('name') or quote.market.value}"

    with st.container(border=True):
        st.subheader(title)
        st.caption(f"{item.get('candidate_source') or '시장 실시간 순위'} · {quote.session} · {plan.strategy}")
        top = st.columns(2)
        top[0].metric("현재가", price_text(quote.price), f"{quote.change_pct:+.2f}%")
        top[1].metric("전략 신호", strategy_signal_text(plan), f"{state} · 점수 {plan.score}/100")
        st.caption(f"구조: {repeat_text} · 위험 상태: {plan.risk_state}")
        ensemble = plan.diagnostics.get("strategy_ensemble") or {}
        active_strategies = ", ".join(ensemble.get("active_names") or [])
        st.caption(f"전략 조합: {ensemble.get('calibration_key') or '계산 대기'} · {active_strategies or '완료봉 확인 대기'}")
        levels = st.columns(3)
        levels[0].metric("진입 기준가", price_text(plan.entry))
        levels[1].metric("1차 목표 · 5분", price_text(plan.target))
        levels[2].metric("2차 목표 · 5분", price_text(plan.target2))
        stops = st.columns(3)
        stops[0].metric("Soft Stop", price_text(plan.soft_stop))
        stops[1].metric("Hard Stop", price_text(hard_stop))
        stops[2].metric("비용 반영 손익비", number_text(plan.diagnostics.get("reward_risk_net")))
        reasons = " · ".join(str(reason) for reason in (plan.reasons or [])[:2]) or "특별 경고 없음"
        st.caption(f"판단 근거: {reasons}")
        basis = st.columns(3)
        basis[0].caption(f"진입 기준: {plan.strategy}")
        basis[1].caption(f"목표 근거: {plan.target_basis or '5분 구조 확인 대기'}")
        basis[2].caption(f"손절 근거: {plan.stop_basis or '1분 구조 확인 대기'}")
        if plan.calibration_samples >= 30 and plan.calibration_probability is not None:
            status = "80% 목표 검증 통과" if plan.calibration_probability >= 80.0 else "80% 목표 검증 미통과 · 강한 신호 제외"
            st.caption(f"동일 전략 실측: {plan.calibration_probability:.1f}% · 표본 {plan.calibration_samples}건 · {status}")
        else:
            st.caption(f"동일 전략 실측 표본 누적 중: {plan.calibration_samples}/30건 · 80% 검증 수치를 아직 표시하지 않습니다.")
        st.caption(f"정밀 현재가 120초 갱신 · 구조 15분 · 호가 15분 · 왕복비용 가정 {cost_pct:.2f}% · 자동 주문 없음")
        render_card_detail(item)


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
        st.caption(f"보정 확률: {plan.calibration_probability:.1f}%" if plan.calibration_probability is not None else f"보정 표본 수: {plan.calibration_samples}/30")
        if not item["bars"].empty:
            render_chart(item["bars"].tail(120), plan, key=f"chart_{quote.market.value}_{quote.symbol}")


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
    st.plotly_chart(fig, use_container_width=True, key=key)


client = current_client()
realtime_hub = get_realtime_hub(CLIENT_CACHE_VERSION, current_secret_fingerprint())
event_store = get_event_store()
cycle_store = get_cycle_store()
store = ValidationStore(VALIDATION_ROOT, event_store=event_store)

with st.sidebar:
    st.title("실시간 설정")
    market_label = st.radio("시장", ["국내", "미국"], horizontal=True)
    market = Market.KR if market_label == "국내" else Market.US
    search_query = st.text_input(
        "관심 종목 바로 보기",
        placeholder="국내: 현대차 또는 005380 · 미국: NVDA",
    ).strip()
    cost_default = 0.05 if market == Market.KR else 0.10
    cost_pct = st.number_input("왕복비용 가정(%)", min_value=0.0, max_value=5.0, value=cost_default, step=0.01)
    min_score = st.slider("최소 신호 점수", min_value=60, max_value=100, value=80, step=5)
    if client.ready:
        st.success("실시간 후보 자동 분석 중")
    else:
        st.warning("한국투자증권 연결 대기 중")
        st.caption(" · ".join(f"{name}: {value}" for name, value in client.connection_diagnostics.items()))
    budget = client.budget_status
    st.caption(f"호출 보호: 1분 {budget.minute_used}/{budget.minute_limit} · 5시간 {budget.five_hour_used}/{budget.five_hour_limit}")
    st.caption(realtime_hub.status_label())
    st.caption("상승 후보 목록 30분 · KIS 체결 현재가 1초 · 구조/호가 10~15분 갱신 · 자동 주문 없음")

kis_connected = client.ready
if kis_connected:
    # Price ticks arrive in a background KIS WebSocket. The 1-second rerun only
    # redraws in-memory ticks; completed-bar analysis remains cached.
    st_autorefresh(interval=1_000, key=f"live_dashboard_{market.value}")

st.markdown("<div class='mobile-head'><h1>실시간 상승·반복단타 혼합 스캐너</h1><p>상승 추세 후보의 현재가 · 진입 기준가 · 5분 1차/2차 목표 · 구조 손절가를 바로 비교하고, 반복단타 구조는 별도로 구분합니다.</p></div>", unsafe_allow_html=True)

cards: list[dict[str, Any]] = []
errors: list[str] = []
candidates: list[dict[str, Any]] = []
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
if not kis_connected:
    st.markdown("<div class='connection wait'>한국투자증권 연결을 기다리고 있습니다. 실제 가격과 매매 레벨은 연결된 데이터가 있을 때만 표시합니다.</div>", unsafe_allow_html=True)
    st.info("연결 확인 — " + " · ".join(f"{name}: {value}" for name, value in client.connection_diagnostics.items()))
else:
    try:
        with st.spinner("시장 전체에서 가격 조건을 통과한 상승 후보를 자동으로 찾는 중…"):
            candidates = load_dashboard_candidates(market.value)
            requests: list[dict[str, Any]] = []
            if direct_request is not None:
                requests.append({**direct_request, "candidate_source": "관심 종목 직접 검색"})
            for candidate in candidates[:MAX_LIVE_CARDS]:
                if direct_request is not None and str(candidate["symbol"]) == direct_request["symbol"]:
                    continue
                requests.append(candidate)
            visible_requests = requests[:MAX_LIVE_CARDS + 1]
            realtime_hub.configure(
                (
                    market,
                    str(candidate["symbol"]),
                    str(candidate.get("exchange") or ("NAS" if market == Market.US else "")),
                )
                for candidate in visible_requests
            )
            for candidate in visible_requests:
                symbol = str(candidate["symbol"])
                exchange = str(candidate.get("exchange") or ("NAS" if market == Market.US else ""))
                try:
                    card = analyze_card(symbol, market, exchange, float(cost_pct), int(min_score), store)
                    card["name"] = str(candidate.get("name") or symbol)
                    card["candidate_source"] = str(candidate.get("candidate_source") or "시장 실시간 순위")
                    cards.append(card)
                except (KISError, ValueError, KeyError, OSError) as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}")
                except Exception:
                    errors.append(f"{symbol}: 분석 준비 오류")
    except KISError:
        st.error("실시간 후보를 가져오지 못했습니다. 잠시 뒤 화면이 자동으로 다시 확인합니다.")

if candidates:
    list_rows = []
    for candidate in candidates[:MAX_CANDIDATE_LIST]:
        price = candidate.get("price")
        change = candidate.get("change_pct")
        list_rows.append({
            "종목": f"{candidate.get('symbol')} · {candidate.get('name') or ''}",
            "현재가": price_text(float(price)) if isinstance(price, (int, float)) else "미확인",
            "등락률": f"{float(change):+.2f}%" if isinstance(change, (int, float)) else "미확인",
            "거래대금": money(float(candidate.get('turnover') or 0.0)),
            "출처": str(candidate.get("candidate_source") or "시장 실시간 순위"),
        })
    limit_text = "30만 원 미만" if market == Market.KR else "170달러 미만"
    st.subheader(f"가격 조건 통과 상승 후보 · {len(candidates)}개")
    st.caption(f"{limit_text} · 상승률·거래대금·거래량 순위를 바탕으로 넓게 선별한 목록입니다. 아래 정밀 카드는 상위 {MAX_LIVE_CARDS}개를 계산합니다.")
    st.dataframe(pd.DataFrame(list_rows), hide_index=True, use_container_width=True)
elif kis_connected:
    limit_text = "30만 원 미만" if market == Market.KR else "170달러 미만"
    st.info(f"현재 {limit_text} 가격 조건과 상승 후보 기준을 함께 통과한 종목이 없습니다. 다음 30분 후보 목록 갱신 때 자동으로 다시 확인합니다.")

if cards:
    cards.sort(key=mixed_card_priority, reverse=True)
    updated_at = max(card["quote"].timestamp for card in cards)
    source_labels = {str(card.get("candidate_source") or "시장 실시간 순위") for card in cards}
    source_text = " · ".join(sorted(source_labels))
    st.markdown(f"<div class='connection ok'>실시간 현재가 기준 {updated_at.strftime('%H:%M:%S')} · {html.escape(realtime_hub.status_label())} · {source_text} · 상승 추세를 우선으로 {len(cards)}개를 분석했습니다. `반복단타 가능`은 추가 구조 표시이며, 초록색 목표가와 빨간색 손절가는 사용자 전략의 목표·손절 레벨입니다.</div>", unsafe_allow_html=True)
    for card_item in cards:
        render_live_card(card_item, float(cost_pct))
elif kis_connected and not errors and not candidates:
    st.info("현재 분석할 상승 추세 후보가 없습니다. 관심 종목은 왼쪽 검색칸에 바로 입력할 수 있습니다.")

for error in errors:
    st.warning(f"일부 후보는 분석 데이터를 만들지 못했습니다: {error}")

with st.expander("숫자와 전략 신호 읽는 법"):
    st.markdown("**현재가**는 KIS 공식 실시간 체결가 연결이 유지되는 동안 1초마다 화면에 반영되고, 재연결 중에는 REST 현재가를 임시 표시합니다. **진입 기준가·1차/2차 목표가·손절가**는 완료된 1분봉과 5분봉 구조, 거래량·거래대금·호가 상태에 따라 계산합니다. `강한 매수 검토 · 실측 80% 이상`은 동일 전략·세션·점수 구간의 사후 표본이 30건 이상이고 1차 목표가가 Hard Stop보다 먼저 도달한 비율이 80% 이상일 때만 표시합니다. 표본 부족 구간은 80%라고 표시하지 않고 누적 상태로 남깁니다. 자동 주문 기능은 없습니다.")
