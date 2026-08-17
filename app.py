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

APP_VERSION = "5.2-mobile-cards"
CLIENT_CACHE_VERSION = "mobile-card-flow-v1"
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


@st.cache_data(ttl=10, show_spinner=False)
def _load_quote_record(symbol: str, market_value: str, exchange: str) -> dict[str, object]:
    quote = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).quote(
        symbol, Market(market_value), exchange, include_orderbook=True
    )
    return _quote_to_cache_record(quote)


def load_quote(symbol: str, market_value: str, exchange: str) -> Quote:
    return _quote_from_cache_record(_load_quote_record(symbol, market_value, exchange))


@st.cache_data(ttl=60, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    return get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint()).intraday(
        symbol, Market(market_value), exchange
    )


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
    """Fetch and analyze one explicitly selected card candidate (max three per run)."""
    quote = load_quote(symbol, market.value, exchange)
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
    <div><div class="ticker">{html.escape(quote.symbol)}</div><div class="name">{html.escape(quote.market.value)} · {html.escape(quote.session)} · {html.escape(plan.strategy)}</div></div>
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
            render_chart(item["bars"].tail(120), plan)


def render_chart(bars: pd.DataFrame, plan: Any) -> None:
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
    st.plotly_chart(fig, use_container_width=True)


client = get_client(CLIENT_CACHE_VERSION, current_secret_fingerprint())
event_store = get_event_store()
cycle_store = get_cycle_store()
store = ValidationStore(VALIDATION_ROOT, event_store=event_store)

with st.sidebar:
    st.title("📈 카드 설정")
    market_label = st.radio("시장", ["국내 정규장", "미국 전 세션"], horizontal=True)
    market = Market.KR if market_label.startswith("국내") else Market.US
    symbol = st.text_input("직접 볼 종목", placeholder="005930 또는 SOXL").strip().upper()
    exchange = st.selectbox("미국 거래소", ["NAS", "NYS", "AMS"], disabled=market == Market.KR)
    kis_connected = client.ready
    if kis_connected:
        st.success("한국투자증권 연결됨")
    else:
        st.warning("한국투자증권 연결 대기 중\n\n연결되면 아래 검색 버튼이 켜집니다.")
        status = client.connection_diagnostics
        st.caption(" · ".join(f"{name}: {value}" for name, value in status.items()))
    full_market_scan_now = st.button("전종목 후보 찾기", use_container_width=True, disabled=not kis_connected)
    direct_card_now = st.button("입력 종목 카드 만들기", type="primary", use_container_width=True, disabled=not kis_connected or not symbol)
    live = st.toggle("60초 카드 새로고침", False, disabled=not kis_connected)
    cost_default = 0.05 if market == Market.KR else 0.10
    cost_pct = st.number_input("왕복비용 가정(%)", min_value=0.0, max_value=5.0, value=cost_default, step=0.01)
    min_score = st.slider("최소 신호 점수", min_value=60, max_value=100, value=80, step=5)
    budget = client.budget_status
    st.caption(f"호출 보호: 1분 {budget.minute_used}/{budget.minute_limit} · 5시간 {budget.five_hour_used}/{budget.five_hour_limit}")
    st.caption("카드는 최대 3개만 정밀 분석합니다. 자동 주문은 없습니다.")

if live and symbol and kis_connected:
    st_autorefresh(interval=60_000, key="mobile_card_refresh")

st.markdown("<div class='mobile-head'><h1>반복단타 후보 카드</h1><p>여러 후보의 진입 기준가 · 5분 목표가 · 구조 손절가를 휴대전화에서 비교합니다.</p></div>", unsafe_allow_html=True)
if kis_connected:
    st.markdown("<div class='connection ok'>한국투자증권 연결이 준비되었습니다. 전종목 후보를 찾거나 종목코드를 입력해 카드로 확인하세요.</div>", unsafe_allow_html=True)
else:
    status = client.connection_diagnostics
    st.markdown("<div class='connection wait'>한국투자증권 연결을 기다리고 있습니다. 연결되기 전에는 가격을 임의로 보여 주지 않으며, 검색 버튼도 자동으로 막습니다.</div>", unsafe_allow_html=True)
    st.info("연결 확인 — " + " · ".join(f"{name}: {value}" for name, value in status.items()))

if full_market_scan_now:
    try:
        with st.spinner("시장 전체 순위에서 1차 후보를 찾는 중…"):
            full_rankings = client.market_rankings(market)
            full_candidates = merge_rankings(market, full_rankings, limit=20)
        st.session_state["full_market"] = market.value
        st.session_state["full_candidates"] = [candidate.to_dict() for candidate in full_candidates]
        st.session_state["mobile_cards"] = []
    except KISError as exc:
        st.error("후보를 가져오지 못했습니다. 잠시 뒤 다시 시도해 주세요." if "KIS_ACCESS_TOKEN" not in str(exc) else "한국투자증권 연결이 아직 준비되지 않았습니다.")

selected_requests: list[dict[str, str]] = []
if st.session_state.get("full_market") == market.value and st.session_state.get("full_candidates"):
    candidates = list(st.session_state["full_candidates"])
    st.subheader("전종목 1차 후보")
    st.caption("거래대금·거래량·상승률 순위를 먼저 걸러 낸 후보입니다. 아래에서 최대 3개만 골라 카드로 정밀 분석하세요.")
    labels = [f"{candidate['symbol']} · {candidate.get('name') or '종목명 미확인'}" for candidate in candidates]
    label_map = {label: candidate for label, candidate in zip(labels, candidates)}
    for candidate in candidates[:8]:
        change = candidate.get("change_pct")
        change_text = f"{float(change):+.2f}%" if isinstance(change, (int, float)) else "등락률 미확인"
        st.markdown(f"<div class='candidate-row'><span><b>{html.escape(str(candidate['symbol']))}</b> <small>{html.escape(str(candidate.get('name') or ''))}</small></span><span>{change_text} · 점수 {candidate.get('screen_score', '-')}</span></div>", unsafe_allow_html=True)
    chosen_labels = st.multiselect("카드로 자세히 볼 후보 (최대 3개)", labels, max_selections=MAX_CARD_CANDIDATES)
    if st.button("선택 후보 카드 만들기", type="primary", use_container_width=True, disabled=not kis_connected or not chosen_labels):
        selected_requests = [
            {"symbol": str(label_map[label]["symbol"]), "exchange": str(label_map[label].get("exchange") or exchange)}
            for label in chosen_labels
        ]

if direct_card_now and symbol:
    selected_requests = [{"symbol": symbol, "exchange": exchange}]

if selected_requests or (live and symbol and kis_connected):
    requests = selected_requests or [{"symbol": symbol, "exchange": exchange}]
    cards: list[dict[str, Any]] = []
    errors: list[str] = []
    with st.spinner("선택 종목의 현재가·호가·1분봉을 확인해 카드를 만드는 중…"):
        for request in requests[:MAX_CARD_CANDIDATES]:
            try:
                cards.append(analyze_card(request["symbol"], market, request["exchange"], float(cost_pct), int(min_score), store))
            except (KISError, ValueError, KeyError, OSError) as exc:
                errors.append(f"{request['symbol']}: {type(exc).__name__}")
            except Exception:
                errors.append(f"{request['symbol']}: 분석 준비 오류")
    st.session_state["mobile_cards"] = cards
    st.session_state["mobile_card_errors"] = errors

cards = st.session_state.get("mobile_cards") or []
if cards:
    st.subheader(f"정밀 분석 카드 · {len(cards)}개")
    st.markdown(
        "<div class='cards-grid'>" + "".join(trade_card_html(card_item, float(cost_pct)) for card_item in cards) + "</div>",
        unsafe_allow_html=True,
    )
    for card_item in cards:
        render_card_detail(card_item)
elif kis_connected:
    st.info("왼쪽에서 **전종목 후보 찾기**를 누른 뒤 최대 3개를 고르거나, 종목코드를 입력해 카드로 확인하세요.")

for error in st.session_state.get("mobile_card_errors") or []:
    st.warning(f"일부 카드를 만들지 못했습니다: {error}")

with st.expander("이 카드가 보여 주는 것"):
    st.markdown("**초록색 목표가**는 완료된 5분봉 구조를 기준으로 계산합니다. **빨간색 손절가**는 구조가 무효가 되는 참고선입니다. 신호가 `대기` 또는 `차단`이면 가격이 보이더라도 매수 권유가 아닙니다. 화면을 여는 것만으로는 KIS 시세를 요청하지 않습니다.")

with st.expander("연결이 계속 안 될 때"):
    st.markdown("앱의 노란 안내가 계속 보이면, 저장된 한국투자증권 연결 정보가 새 앱에 전달되지 않은 상태입니다. 종목 검색 버튼이 회색일 때는 버튼을 반복해서 누르지 마세요. 앱 관리 화면의 Settings에서 연결 정보를 다시 저장한 뒤 앱을 재시작하면 됩니다. 실제 토큰·앱키 값은 누구에게도 보내지 마세요.")
