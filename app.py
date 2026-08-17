from __future__ import annotations

import html
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
from scanner.models import Market, Quote, Signal
from scanner.persistence import EventStore
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationStore

APP_VERSION = "5.3-live-dashboard"
# Bump this whenever the cached KISClient interface changes. Streamlit can retain a
# resource through a hot code update, so a new contract must never reuse an old client.
CLIENT_CACHE_VERSION = "client-contract-v4-live-dashboard"
VALIDATION_ROOT = Path(".scanner_data/validation")
MAX_CARD_CANDIDATES = 3

st.set_page_config(
    page_title="반복단타 후보 카드",
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


@st.cache_data(ttl=60, show_spinner=False)
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
    return Quote(
        symbol=quote.symbol, market=quote.market, price=quote.price,
        previous_close=quote.previous_close, timestamp=quote.timestamp,
        bid=bid, ask=ask, volume=quote.volume, turnover=quote.turnover,
        session=quote.session, source=quote.source,
    )


@st.cache_data(ttl=600, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    # Completed 1-minute bars are refreshed every 10 minutes. Current price above
    # remains live every minute, while 5-minute structure stays stable and explainable.
    return get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).intraday(
        symbol, Market(market_value), exchange
    )


@st.cache_data(ttl=7200, show_spinner=False)
def load_dashboard_candidates(market_value: str) -> list[dict[str, Any]]:
    """Return automatic candidates, falling back safely when ranking is unavailable."""
    market = Market(market_value)
    client = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint())
    try:
        rankings = client.market_rankings(market)
        candidates = [candidate.to_dict() for candidate in merge_rankings(market, rankings, limit=MAX_CARD_CANDIDATES)]
        for candidate in candidates:
            candidate["candidate_source"] = "시장 실시간 순위"
        if candidates:
            return candidates
    except KISError:
        # Some ranking endpoints can be unavailable by account or session. A known
        # liquid list keeps the first screen useful without pretending it is a full-market rank.
        pass

    items = KR_LIQUID if market == Market.KR else US_LIQUID
    quotes: list[tuple[Quote, Any]] = []
    for item in items:
        try:
            quote = client.quote(item.symbol, market, item.exchange, include_orderbook=False)
            if quote.price > 0:
                quotes.append((quote, item))
        except KISError:
            continue
    quotes.sort(key=lambda pair: (pair[0].change_pct, pair[0].turnover or 0.0), reverse=True)
    return [
        {
            "symbol": quote.symbol,
            "name": item.name,
            "market": market.value,
            "exchange": item.exchange,
            "price": quote.price,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "turnover": quote.turnover,
            "candidate_source": "유동성 시작목록 자동 대체",
        }
        for quote, item in quotes[:MAX_CARD_CANDIDATES]
    ]


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
    if event_store.configured:
        marker = str(plan.diagnostics.get("completed_bar_at") or quote.timestamp.isoformat())
        cycle_store.apply_risk_state(cycle, plan.risk_state, marker)
    return {"quote": quote, "bars": bars, "plan": plan, "exchange": exchange}


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
    card = f"""
<section class="trade-card">
      <div class="card-top">
    <div><div class="ticker">{html.escape(quote.symbol)}</div><div class="name">{html.escape(str(item.get('name') or quote.market.value))} · {html.escape(quote.session)} · {html.escape(plan.strategy)}</div></div>

    <div><div class="price">{price_text(quote.price)}</div><div class="change {change_class}">{change:+.2f}%</div></div>
  </div>
  <div class="badges">
    <span class="badge {signal_class(plan.signal)}">{html.escape(plan.signal.value)}</span>
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
event_store = get_event_store()
cycle_store = get_cycle_store()
store = ValidationStore(VALIDATION_ROOT, event_store=event_store)

with st.sidebar:
    st.title("실시간 설정")
    market_label = st.radio("시장", ["국내", "미국"], horizontal=True)
    market = Market.KR if market_label == "국내" else Market.US
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
    st.caption("현재가 60초 · 구조/호가 10~15분 갱신 · 자동 주문 없음")

kis_connected = client.ready
if kis_connected:
    # This is a dashboard, not a button-driven analysis form. It reruns at the
    # permitted minimum cadence and refreshes all visible card prices automatically.
    st_autorefresh(interval=60_000, key=f"live_dashboard_{market.value}")

st.markdown("<div class='mobile-head'><h1>실시간 반복단타 후보</h1><p>현재가 · 진입 기준가 · 5분 1차/2차 목표 · 구조 손절가를 열자마자 비교합니다.</p></div>", unsafe_allow_html=True)

cards: list[dict[str, Any]] = []
errors: list[str] = []
if not kis_connected:
    st.markdown("<div class='connection wait'>한국투자증권 연결을 기다리고 있습니다. 실제 가격과 매매 레벨은 연결된 데이터가 있을 때만 표시합니다.</div>", unsafe_allow_html=True)
    st.info("연결 확인 — " + " · ".join(f"{name}: {value}" for name, value in client.connection_diagnostics.items()))
else:
    try:
        with st.spinner("시장 전체에서 실시간 반복단타 후보를 자동으로 찾는 중…"):
            candidates = load_dashboard_candidates(market.value)
            for candidate in candidates[:MAX_CARD_CANDIDATES]:
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

if cards:
    updated_at = max(card["quote"].timestamp for card in cards)
    source_labels = {str(card.get("candidate_source") or "시장 실시간 순위") for card in cards}
    source_text = " · ".join(sorted(source_labels))
    st.markdown(f"<div class='connection ok'>실시간 현재가 기준 {updated_at.strftime('%H:%M:%S')} · {source_text} · 상위 {len(cards)}개 후보를 자동 분석했습니다. 초록색 목표가와 빨간색 손절가는 수동매매 참고선입니다.</div>", unsafe_allow_html=True)
    st.markdown("<div class='cards-grid'>" + "".join(trade_card_html(card_item, float(cost_pct)) for card_item in cards) + "</div>", unsafe_allow_html=True)
    for card_item in cards:
        render_card_detail(card_item)
elif kis_connected and not errors:
    st.info("현재 0.5~5% 반복폭과 필수 조건을 함께 통과한 후보가 없습니다. 화면은 60초마다 자동으로 다시 확인합니다.")

for error in errors:
    st.warning(f"일부 후보는 분석 데이터를 만들지 못했습니다: {error}")

with st.expander("숫자 읽는 법"):
    st.markdown("**현재가**는 60초마다 갱신합니다. **진입 기준가·1차/2차 목표가·손절가**는 완료된 1분봉과 5분봉 구조, 거래량·거래대금·호가 안전성에 따라 계산합니다. 신호가 `대기` 또는 `진입 금지`이면 가격이 표시돼도 매수 권유가 아닙니다. 자동 주문 기능은 없습니다.")
