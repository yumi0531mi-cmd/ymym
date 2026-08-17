from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError
from scanner.models import Market, Signal
from scanner.persistence import EventStore, ManualTrade, PersistenceError, save_manual_trade
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationCase, ValidationStore

APP_VERSION = "3.0.0"
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


@st.cache_resource
def get_event_store() -> EventStore:
    return EventStore(st.secrets)


@st.cache_data(ttl=10, show_spinner=False)
def load_quote(symbol: str, market_value: str, exchange: str):
    return get_client().quote(symbol, Market(market_value), exchange, include_orderbook=True)


@st.cache_data(ttl=60, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    return get_client().intraday(symbol, Market(market_value), exchange)


@st.cache_data(ttl=60, show_spinner=False)
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


def secret_value(name: str) -> str:
    try:
        value = st.secrets[name]
        return str(value) if value else ""
    except Exception:
        return ""


def admin_unlocked() -> bool:
    password = secret_value("APP_ADMIN_PASSWORD")
    if not password:
        return False
    if st.session_state.get("admin_unlocked"):
        return True
    candidate = st.sidebar.text_input("관리자 비밀번호", type="password", help="검증 보고서와 수동 매매 기록은 관리자만 볼 수 있습니다.")
    if candidate:
        if candidate == password:
            st.session_state["admin_unlocked"] = True
            st.rerun()
        else:
            st.sidebar.error("관리자 비밀번호가 일치하지 않습니다.")
    return False


def render_chart(bars: pd.DataFrame, plan) -> None:
    fig = go.Figure(
        go.Candlestick(x=bars.index, open=bars.open, high=bars.high, low=bars.low, close=bars.close, name="1분봉")
    )
    for value, name, color in (
        (plan.entry, "실제 진입 참고", "#1976d2"),
        (plan.target, "확인된 저항", "#2e7d32"),
        (plan.stop, "지지 이탈 손절", "#c62828"),
    ):
        if value is not None:
            fig.add_hline(y=value, line_color=color, annotation_text=f"{name} {value:g}")
    fig.update_layout(height=420, margin=dict(l=4, r=4, t=15, b=4), xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)


client = get_client()
event_store = get_event_store()

with st.sidebar:
    st.title("📡 스캐너 설정")
    market_label = st.radio("시장", ["국내 정규장", "미국 전 세션"], horizontal=True)
    market = Market.KR if market_label.startswith("국내") else Market.US
    symbol = st.text_input("종목코드/티커 직접 검색", placeholder="005930 또는 SOXL").strip().upper()
    exchange = st.selectbox("미국 거래소", ["NAS", "NYS", "AMS"], disabled=market == Market.KR)
    scan_now = st.button("유동성 시작목록 빠른검색", use_container_width=True)
    analyze_now = st.button("선택 종목 분석", type="primary", use_container_width=True)
    live = st.toggle("자동 새로고침", False, help="기본은 꺼져 있습니다. 필요할 때만 저빈도 갱신을 사용하세요.")
    refresh_seconds = st.select_slider("자동 새로고침 간격(초)", options=[10, 15, 30, 60], value=15, disabled=not live)
    save_validation = st.toggle("매수 신호 검증 저장", False, help="진입 고려 신호만 저장합니다. 주문은 실행하지 않습니다.")
    cost_default = 0.05 if market == Market.KR else 0.10
    cost_pct = st.number_input("왕복비용 가정(%)", min_value=0.0, max_value=5.0, value=cost_default, step=0.01)
    min_score = st.slider("최소 신호 점수", min_value=70, max_value=100, value=85, step=5)
    st.caption(f"현재 세션: {market_session(market)}")
    st.caption(f"KIS 인증: {client.token_mode}")
    st.caption(f"검증 저장: {event_store.status}")
    admin = admin_unlocked()

if live and symbol:
    st_autorefresh(interval=int(refresh_seconds * 1000), key=f"live_refresh_{refresh_seconds}")

st.title("시간별 강한 종목 + 반복박스 혼합형 스캐너")
st.caption(
    "수동매매 판단 보조 도구입니다. 수익은 보장되지 않으며, 매수 신호는 완료된 분봉·실제 진입 참고가·비용·유동성·세션 게이트를 모두 통과할 때만 표시합니다."
)

if scan_now:
    with st.spinner("시작목록의 현재가 1차 필터 확인 중…"):
        ranked, scan_errors = scan_starter_universe(market.value)
    st.session_state["ranked_market"] = market.value
    st.session_state["ranked_candidates"] = [(quote.symbol, quote.change_pct) for quote in ranked[:10]]
    st.session_state["scan_errors"] = scan_errors

if st.session_state.get("ranked_market") == market.value and st.session_state.get("ranked_candidates"):
    st.subheader("유동성 시작목록 1차 결과")
    st.caption("전체 시장 스캔이 아닙니다. 공개된 시작목록의 가격·상승률 결과이며, 정밀 조건을 통과하기 전에는 매수 후보가 아닙니다.")
    st.dataframe(
        pd.DataFrame(st.session_state["ranked_candidates"], columns=["종목", "등락률(%)"]),
        hide_index=True,
        use_container_width=True,
    )
if st.session_state.get("ranked_market") == market.value and st.session_state.get("scan_errors"):
    st.caption(f"현재가 미수신: {len(st.session_state['scan_errors'])}건")

if not symbol:
    st.info("왼쪽에 종목코드 또는 티커를 입력하고 **선택 종목 분석**을 누르세요. 자동 새로고침은 기본 해제되어 API 호출을 줄입니다.")
    st.stop()
if not (analyze_now or live):
    st.info("준비되었습니다. **선택 종목 분석**을 눌러 KIS 시세를 요청하세요.")
    st.stop()

try:
    quote = load_quote(symbol, market.value, exchange)
    bars = load_bars(symbol, market.value, exchange)
    plan = analyze(
        quote,
        bars,
        orderbook_required=True,
        round_trip_cost_pct=float(cost_pct),
        minimum_score=int(min_score),
    )
except (KISError, ValueError, KeyError, OSError) as exc:
    st.error(f"KIS 데이터 수신 실패: {exc}")
    st.caption("실시간 현재가와 호가를 확인하지 못했으므로 진입·목표·손절가는 표시하지 않습니다.")
    st.stop()
except Exception as exc:
    st.error(f"예상하지 못한 오류: {type(exc).__name__}: {exc}")
    st.stop()

css = "buy" if plan.signal == Signal.BUY else "block" if plan.signal in (Signal.BLOCK, Signal.SELL) else "unknown" if plan.signal == Signal.UNVERIFIED else "wait"
st.markdown(
    f'<div class="signal {css}">{html.escape(plan.signal.value)} · {html.escape(plan.strategy)} · {html.escape(plan.regime.value)}</div>',
    unsafe_allow_html=True,
)

metrics = [
    ("현재가", f"{quote.price:g}"),
    ("매도 1호가", f"{quote.ask:g}" if quote.ask else "미수신"),
    ("매수 1호가", f"{quote.bid:g}" if quote.bid else "미수신"),
    ("당일 등락", f"{quote.change_pct:+.2f}%"),
    ("조건점수", f"{plan.score}점"),
]
for column, (label, value) in zip(st.columns(5), metrics):
    column.metric(label, value)

st.subheader("기계적 매매 계획")
if plan.entry is None:
    st.info("현재는 진입하지 않음. 모든 데이터·세션·유동성·손익비·점수 조건이 충족되기 전에는 가격을 제시하지 않습니다.")
else:
    values = [("실제 진입 참고가", plan.entry), ("확인된 저항 청산", plan.target), ("지지 이탈 손절", plan.stop)]
    for column, (label, value) in zip(st.columns(3), values):
        column.metric(label, f"{value:g}" if value is not None else "미확인")
    st.caption(f"목표 근거: {plan.target_basis} · 손절 근거: {plan.stop_basis} · 왕복비용 가정: {cost_pct:.2f}%")

if plan.repeat_box:
    low, high = plan.repeat_box
    ready = bool(plan.diagnostics.get("repeat_entry_ready"))
    message = "하단 진입 구간에 근접" if ready else "현재 위치는 하단 진입 구간이 아님"
    st.success(f"완료 5분봉 반복박스 확인: {low:g}~{high:g} (폭 {(high / low - 1) * 100:.2f}%) · {message}")
    st.caption("상단 돌파 또는 하단 이탈 시 박스 반복 전략을 중단하고 신호를 다시 확인하세요.")
else:
    st.caption("반복박스 미확인 또는 현재가 이탈: 현재는 1회 매매 관점만 사용합니다.")

if plan.forecasts:
    st.subheader("5·10·15·30분 경로 시나리오")
    st.caption("확정가격이 아닌 사전 고정 범위입니다. 네 구간이 모두 맞아야 내부 경로 검증에서 통과로 처리합니다.")
    for column, point in zip(st.columns(4), plan.forecasts):
        column.metric(f"{point.minutes}분 · {point.direction.value}", f"{point.base:g}")
        column.caption(f"{point.low:g}~{point.high:g}")

if not bars.empty:
    render_chart(bars.tail(120), plan)

with st.expander("판정 근거와 데이터 상태"):
    st.json(
        {
            "세션": quote.session,
            "데이터검문": plan.data_verified,
            "미수신": plan.missing,
            "가감점·차단 근거": plan.reasons,
            "진단": plan.diagnostics,
        },
        expanded=True,
    )

store = ValidationStore(VALIDATION_ROOT, event_store=event_store)
if save_validation and plan.signal == Signal.BUY and plan.forecasts:
    scored = store.score_ready(symbol, market.value, bars, float(cost_pct))
    latest = float(bars.close.iloc[-2]) if len(bars) >= 2 else None
    case = ValidationCase.from_plan(plan, latest, quote.session, APP_VERSION)
    path, created = store.save_once(case)
    if scored:
        st.caption(f"30분 경로 사후채점 완료: {scored}건")
    st.caption(("검증 원본 저장: " if created else "동일 신호 원본 재사용: ") + path.name + f" · {store.storage_status}")
    if store.last_persistence_error:
        st.warning(store.last_persistence_error)

if admin:
    with st.expander("관리자: 검증 현황·보고서"):
        summary = store.summary()
        st.json(summary)
        if store.last_persistence_error:
            st.warning(store.last_persistence_error)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if st.button("CSV·HTML 보고서 만들기"):
            csv_path = store.export_csv(Path("reports") / f"validation_{stamp}.csv")
            html_path = store.export_html(Path("reports") / f"validation_{stamp}.html")
            st.success(f"보고서 생성: {csv_path} / {html_path}")

    with st.expander("관리자: 직접 매매 기록"):
        if not event_store.configured:
            st.warning("수동 매매 기록은 영속 저장소가 필요합니다. `SUPABASE_URL`과 `SUPABASE_KEY`를 Secrets에 설정한 뒤 사용하세요.")
        else:
            with st.form("manual_trade_form", clear_on_submit=True):
                left, middle, right = st.columns(3)
                journal_symbol = left.text_input("종목", value=symbol)
                side = middle.selectbox("방향", ["매수", "매도"])
                quantity = right.number_input("수량", min_value=0.0, value=1.0, step=1.0)
                entry_price = left.number_input("진입가", min_value=0.0, value=float(quote.ask or quote.price), step=0.01)
                exit_price = middle.number_input("청산가(미청산 시 0)", min_value=0.0, value=0.0, step=0.01)
                fees = right.number_input("총비용", min_value=0.0, value=0.0, step=0.01)
                note = st.text_area("메모", max_chars=500)
                save_trade = st.form_submit_button("수동 매매 기록 저장")
            if save_trade:
                try:
                    trade = ManualTrade.create(
                        journal_symbol, market.value, side, entry_price, quantity,
                        exit_price if exit_price > 0 else None, fees, note,
                    )
                    save_manual_trade(event_store, trade)
                    st.success("수동 매매 기록을 영속 저장했습니다.")
                except (PersistenceError, ValueError) as exc:
                    st.error(f"수동 매매 기록 저장 실패: {exc}")
