from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError
from scanner.models import Market, Signal
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationCase, ValidationStore

APP_VERSION = "2.1.0"
VALIDATION_ROOT = Path(".scanner_data/validation")

st.set_page_config(page_title="한투 혼합형 주식 스캐너", page_icon="📡", layout="wide")
st.markdown(
    """
<style>
.block-container{padding-top:.8rem;max-width:1280px}.stMetric{background:#f7f8fb;border:1px solid #e6e8ee;padding:.65rem;border-radius:12px}
[data-testid="stMetricValue"]{font-size:clamp(1.05rem,2.8vw,1.8rem)!important}.signal{padding:.85rem 1rem;border-radius:13px;font-size:1.22rem;font-weight:800;margin:.4rem 0}
.buy{background:#e4f6eb;color:#126b39}.wait{background:#fff5d8;color:#775400}.block{background:#fde9e8;color:#9e2923}.unknown{background:#edf2fc;color:#315caa}
@media(max-width:700px){.block-container{padding:.4rem}.signal{font-size:1rem}[data-testid="stMetricValue"]{font-size:1.08rem!important}}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def get_client() -> KISClient:
    return KISClient(st.secrets)


@st.cache_data(ttl=2, show_spinner=False)
def load_quote(symbol: str, market_value: str, exchange: str):
    return get_client().quote(symbol, Market(market_value), exchange, include_orderbook=True)


@st.cache_data(ttl=15, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    return get_client().intraday(symbol, Market(market_value), exchange)


@st.cache_data(ttl=30, show_spinner=False)
def scan_starter_universe(market_value: str):
    scan_market = Market(market_value)
    items = KR_LIQUID if scan_market == Market.KR else US_LIQUID
    quotes, errors = [], []
    for item in items:
        try:
            quotes.append(get_client().quote(item.symbol, scan_market, item.exchange, include_orderbook=False))
        except Exception as exc:
            errors.append(f"{item.symbol}: {type(exc).__name__}")
    return rank_quotes(quotes, scan_market), errors


def render_chart(bars: pd.DataFrame, plan) -> None:
    fig = go.Figure(
        go.Candlestick(x=bars.index, open=bars.open, high=bars.high, low=bars.low, close=bars.close, name="1분봉")
    )
    for value, name, color in (
        (plan.entry, "진입", "#1976d2"),
        (plan.target, "확인된 저항", "#2e7d32"),
        (plan.stop, "확인된 지지 이탈", "#c62828"),
    ):
        if value is not None:
            fig.add_hline(y=value, line_color=color, annotation_text=f"{name} {value:g}")
    fig.update_layout(height=420, margin=dict(l=4, r=4, t=15, b=4), xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)


with st.sidebar:
    st.title("📡 스캐너 설정")
    market_label = st.radio("시장", ["국내 정규장", "미국 전 세션"], horizontal=True)
    market = Market.KR if market_label.startswith("국내") else Market.US
    symbol = st.text_input("종목코드/티커 직접 검색", placeholder="005930 또는 SOXL").strip().upper()
    exchange = st.selectbox("미국 거래소", ["NAS", "NYS", "AMS"], disabled=market == Market.KR)
    live = st.toggle("실시간 집중 분석", True)
    save_validation = st.toggle(
        "검증 원본 저장",
        True,
        help="주문은 실행하지 않습니다. 동일 신호는 3분 안에 중복 저장하지 않고, 30분 뒤 같은 경로로 채점합니다.",
    )
    scan_now = st.button("유동성 시작목록 빠른검색", use_container_width=True)
    st.caption(f"현재 세션: {market_session(market)}")
    st.caption("직접 검색은 가격 제한 없이 분석합니다. 실시간 현재가가 없으면 매매계획을 표시하지 않습니다.")

if live:
    st_autorefresh(interval=2500, key="live_refresh")

st.title("시간별 강한 종목 + 반복박스 혼합형 스캐너")
st.caption("기본은 1회 매매 신호이며, 실제 5분 구조에서 0.5~3.0%의 반복 가능한 박스가 확인될 때만 반복단타 가능을 표시합니다.")

if scan_now:
    with st.spinner("현재가 1차 필터 확인 중… 선택 종목 정밀분석과는 분리되어 있습니다."):
        ranked, scan_errors = scan_starter_universe(market.value)
    st.session_state["ranked_market"] = market.value
    st.session_state["ranked_candidates"] = [(q.symbol, q.change_pct) for q in ranked[:10]]
    st.session_state["scan_errors"] = scan_errors

if st.session_state.get("ranked_market") == market.value and st.session_state.get("ranked_candidates"):
    st.subheader("유동성 시작목록 1차 결과")
    st.caption("전체 시장 스캔 결과가 아닙니다. 현재 공개된 시작목록의 가격·상승률 1차 결과이며, 분봉·호가 정밀검문 통과 전에는 매수 후보가 아닙니다.")
    st.dataframe(
        pd.DataFrame(st.session_state["ranked_candidates"], columns=["종목", "등락률(%)"]),
        hide_index=True,
        use_container_width=True,
    )
if st.session_state.get("ranked_market") == market.value and st.session_state.get("scan_errors"):
    st.caption(f"현재가 미수신: {len(st.session_state['scan_errors'])}건")

if not symbol:
    st.info("왼쪽에 국내 종목코드 또는 미국 티커를 입력하세요. 자동 후보 수집은 직접 분석과 분리해 화면 반응을 막지 않습니다.")
    st.stop()

try:
    quote = load_quote(symbol, market.value, exchange)
    bars = load_bars(symbol, market.value, exchange)
    plan = analyze(quote, bars, orderbook_required=True)
except (KISError, ValueError, KeyError, OSError) as exc:
    st.error(f"KIS 데이터 수신 실패: {exc}")
    st.caption("실시간 현재가를 확인하지 못했으므로 진입가·목표가·손절가를 표시하지 않습니다.")
    st.stop()
except Exception as exc:
    st.error(f"예상하지 못한 오류: {type(exc).__name__}: {exc}")
    st.stop()

css = (
    "buy" if plan.signal == Signal.BUY else "block" if plan.signal in (Signal.BLOCK, Signal.SELL)
    else "unknown" if plan.signal == Signal.UNVERIFIED else "wait"
)
st.markdown(
    f'<div class="signal {css}">{plan.signal.value} · {plan.strategy} · {plan.regime.value}</div>',
    unsafe_allow_html=True,
)

metrics = [
    ("현재가", f"{quote.price:g}"),
    ("매도 1호가", f"{quote.ask:g}" if quote.ask else "미수신"),
    ("매수 1호가", f"{quote.bid:g}" if quote.bid else "미수신"),
    ("당일 등락", f"{quote.change_pct:+.2f}%"),
    ("조건점수", f"{plan.score}점"),
]
for col, (label, value) in zip(st.columns(5), metrics):
    col.metric(label, value)

st.subheader("기계적 매매 계획")
if plan.entry is None:
    st.info("현재는 진입하지 않음. 데이터 또는 구조적 진입 조건이 완성되기 전에는 가격을 임의 계산하지 않습니다.")
else:
    values = [("진입 기준가", plan.entry), ("확인된 저항 청산", plan.target), ("지지 이탈 손절", plan.stop)]
    for col, (label, value) in zip(st.columns(3), values):
        col.metric(label, f"{value:g}" if value is not None else "미확인")
    st.caption(f"목표 근거: {plan.target_basis} · 손절 근거: {plan.stop_basis}")

if plan.repeat_box:
    low, high = plan.repeat_box
    st.success(
        f"반복박스 확인: {low:g}~{high:g} (폭 {(high / low - 1) * 100:.2f}%). "
        "하단 반등 확인 후 진입, 상단 분할청산, 하단 종가 이탈 시 반복 종료."
    )
else:
    st.caption("반복박스 미확인: 현재는 1회 매매 관점만 사용합니다.")

if plan.forecasts:
    st.subheader("5·10·15·30분 경로 시나리오")
    st.caption("확정가격이 아닌 사전 고정 범위입니다. 같은 신호의 네 구간이 모두 맞아야 전체 경로 성공으로 채점합니다.")
    for col, point in zip(st.columns(4), plan.forecasts):
        col.metric(f"{point.minutes}분 · {point.direction.value}", f"{point.base:g}")
        col.caption(f"{point.low:g}~{point.high:g}")

if not bars.empty:
    render_chart(bars.tail(120), plan)

with st.expander("판정 근거와 데이터 상태"):
    st.json(
        {
            "세션": quote.session,
            "데이터검문": plan.data_verified,
            "미수신": plan.missing,
            "가감점 근거": plan.reasons,
            "진단": plan.diagnostics,
        },
        expanded=True,
    )

if save_validation and plan.forecasts:
    store = ValidationStore(VALIDATION_ROOT)
    cost_pct = 0.05 if market == Market.KR else 0.10
    scored = store.score_ready(symbol, market.value, bars, cost_pct)
    latest = float(bars.close.iloc[-1]) if not bars.empty else None
    case = ValidationCase.from_plan(plan, latest, quote.session, APP_VERSION)
    path, created = store.save_once(case)
    if scored:
        st.caption(f"30분 경로 사후채점 완료: {scored}건")
    st.caption(("검증 원본 저장: " if created else "동일 신호 원본 재사용: ") + path.name)
    with st.expander("검증 현황·보고서"):
        st.json(store.summary())
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if st.button("CSV·HTML 보고서 만들기"):
            csv_path = store.export_csv(Path("reports") / f"validation_{stamp}.csv")
            html_path = store.export_html(Path("reports") / f"validation_{stamp}.html")
            st.success(f"보고서 생성: {csv_path} / {html_path}")
