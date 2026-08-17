from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from scanner.calibration import calibration_for
from scanner.cycle import CycleStore
from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError
from scanner.market_screener import merge_rankings
from scanner.models import Market, Quote, Signal
from scanner.persistence import EventStore, ManualTrade, PersistenceError, save_manual_trade
from scanner.sessions import market_session
from scanner.universe import KR_LIQUID, US_LIQUID, rank_quotes
from scanner.validation import ValidationCase, ValidationStore

APP_VERSION = "5.1.1-five-minute-target"
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


@st.cache_resource
def get_cycle_store() -> CycleStore:
    return CycleStore(get_event_store())


def _quote_to_cache_record(quote: Quote) -> dict[str, object]:
    """Convert a slots dataclass to basic types before using st.cache_data."""
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
    quote = get_client().quote(symbol, Market(market_value), exchange, include_orderbook=True)
    return _quote_to_cache_record(quote)


def load_quote(symbol: str, market_value: str, exchange: str) -> Quote:
    return _quote_from_cache_record(_load_quote_record(symbol, market_value, exchange))


@st.cache_data(ttl=60, show_spinner=False)
def load_bars(symbol: str, market_value: str, exchange: str) -> pd.DataFrame:
    return get_client().intraday(symbol, Market(market_value), exchange)


@st.cache_data(ttl=60, show_spinner=False)
def scan_starter_universe(market_value: str) -> tuple[list[tuple[str, float]], list[str]]:
    """Cache only strings and numbers; Quote uses slots and is not cache-pickleable."""
    scan_market = Market(market_value)
    items = KR_LIQUID if scan_market == Market.KR else US_LIQUID
    quotes, errors = [], []
    for item in items:
        try:
            quotes.append(get_client().quote(item.symbol, scan_market, item.exchange, include_orderbook=False))
        except Exception as exc:
            errors.append(f"{item.symbol}: {type(exc).__name__}")
    ranked = rank_quotes(quotes, scan_market)
    return [(quote.symbol, float(quote.change_pct)) for quote in ranked], errors


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
        go.Candlestick(x=bars.index, open=bars.open, high=bars.high, low=bars.low, close=bars.close, name="완료·진행 1분봉")
    )
    for value, name, color, dash in (
        (plan.entry, "진입 기준", "#1565c0", "solid"),
        (plan.target, "1차 목표 · 완료 5분봉", "#2e7d32", "solid"),
        (plan.target2, "2차 목표 · 완료 5분봉", "#00897b", "dash"),
        (plan.soft_stop, "Soft Stop", "#ef6c00", "dash"),
        (plan.hard_stop or plan.invalidation or plan.stop, "Hard Stop", "#c62828", "solid"),
    ):
        if value is not None:
            fig.add_hline(y=value, line_color=color, line_dash=dash, annotation_text=f"{name} {value:g}")
    if plan.repeat_box:
        low, high = plan.repeat_box
        fig.add_hrect(y0=low, y1=high, fillcolor="#90caf9", opacity=0.10, line_width=0, annotation_text="반복박스")
    fig.update_layout(
        height=460, margin=dict(l=4, r=4, t=24, b=4), xaxis_rangeslider_visible=False,
        legend=dict(orientation="h"),
    )
    st.plotly_chart(fig, use_container_width=True)


client = get_client()
event_store = get_event_store()
cycle_store = get_cycle_store()

with st.sidebar:
    st.title("📡 스캐너 설정")
    market_label = st.radio("시장", ["국내 정규장", "미국 전 세션"], horizontal=True)
    market = Market.KR if market_label.startswith("국내") else Market.US
    symbol = st.text_input("종목코드/티커 직접 검색", placeholder="005930 또는 SOXL").strip().upper()
    exchange = st.selectbox("미국 거래소", ["NAS", "NYS", "AMS"], disabled=market == Market.KR)
    scan_now = st.button("고정 유동성 시작목록 검색", use_container_width=True)
    full_market_scan_now = st.button("전종목 후보 검색", help="KIS 시장 순위 API로 1차 후보를 찾습니다. 개별 전종목 분봉 조회는 하지 않습니다.", use_container_width=True)
    analyze_now = st.button("선택 종목 분석", type="primary", use_container_width=True)
    live = st.toggle("자동 새로고침", False, help="기본은 꺼져 있습니다. 5시간 호출 예산 안에서 저빈도 갱신만 사용하세요.")
    refresh_seconds = st.select_slider("자동 새로고침 간격(초)", options=[60, 120, 300], value=60, disabled=not live)
    save_validation = st.toggle("매수 신호 검증 저장", False, help="진입 고려 신호만 저장합니다. 주문은 실행하지 않습니다.")
    cost_default = 0.05 if market == Market.KR else 0.10
    cost_pct = st.number_input("왕복비용 가정(%)", min_value=0.0, max_value=5.0, value=cost_default, step=0.01)
    min_score = st.slider("최소 신호 점수", min_value=60, max_value=100, value=80, step=5, help="점수는 승률이 아니라 현재 데이터·유동성·구조의 조건 충족도입니다.")
    st.caption(f"현재 세션: {market_session(market)}")
    token_mode = getattr(client, "token_mode", "재시작 후 인증 상태 확인")
    st.caption(f"KIS 인증: {token_mode}")
    budget = client.budget_status
    st.caption(
        f"KIS 호출 보호(현재 서버): 1분 {budget.minute_used}/{budget.minute_limit} · "
        f"5시간 {budget.five_hour_used}/{budget.five_hour_limit}"
    )
    st.caption("선택 종목 분석은 현재가·1호가·1분봉으로 최대 3건을 사용합니다. 자동 새로고침 60초는 5시간 최대 약 900건입니다.")
    st.caption(f"검증 저장: {event_store.status}")
    admin = admin_unlocked()

if live and symbol:
    st_autorefresh(interval=int(refresh_seconds * 1000), key=f"live_refresh_{refresh_seconds}")

st.title("실시간 반복단타 후보")
st.caption(
    "수동매매 판단 보조 도구입니다. 진입·손절은 완료 1분봉 구조를, 1차·2차 목표는 완료 5분봉 구조를 우선 반영합니다. 화면 가격은 호가, 거래량·거래대금, 비용과 유동성 조건을 함께 계산한 참고 구간이며 수익을 보장하지 않습니다."
)
st.caption("API 보호: 화면을 열기만 해서는 KIS 시세를 요청하지 않습니다. 종목 분석은 최대 3건, 고정 시작목록 검색은 국내 14건·미국 21건의 현재가 요청을 사용합니다. 전종목 후보 검색은 국내 2건·미국 6건의 시장 순위 요청만 사용하며, 개별 분봉·호가 조회는 선택한 종목에만 실행합니다.")

if scan_now:
    with st.spinner("시작목록의 현재가 1차 필터 확인 중…"):
        ranked, scan_errors = scan_starter_universe(market.value)
    st.session_state["ranked_market"] = market.value
    st.session_state["ranked_candidates"] = ranked[:10]
    st.session_state["scan_errors"] = scan_errors

if st.session_state.get("ranked_market") == market.value and st.session_state.get("ranked_candidates"):
    st.subheader("고정 유동성 시작목록 1차 결과")
    st.caption("전체 시장 스캔이 아닙니다. 공개된 시작목록의 가격·상승률 결과이며, 정밀 조건을 통과하기 전에는 매수 후보가 아닙니다.")
    st.dataframe(
        pd.DataFrame(st.session_state["ranked_candidates"], columns=["종목", "등락률(%)"]),
        hide_index=True,
        use_container_width=True,
    )
if st.session_state.get("ranked_market") == market.value and st.session_state.get("scan_errors"):
    st.caption(f"현재가 미수신: {len(st.session_state['scan_errors'])}건")

if full_market_scan_now:
    try:
        with st.spinner("시장 전체 순위에서 반복단타 후보를 1차 선별 중…"):
            full_rankings = client.market_rankings(market)
            full_candidates = merge_rankings(market, full_rankings, limit=20)
        st.session_state["full_market"] = market.value
        st.session_state["full_candidates"] = [candidate.to_dict() for candidate in full_candidates]
        st.session_state["full_scan_sources"] = {source: len(rows) for source, rows in full_rankings.items()}
    except KISError as exc:
        st.error(f"전종목 후보 검색을 지금 실행할 수 없습니다: {exc}")

analyze_candidate = False
if st.session_state.get("full_market") == market.value and st.session_state.get("full_candidates"):
    st.subheader("전종목 1차 후보 결과")
    st.caption("시장 전체 순위 응답에서 거래대금·거래량과 상승률이 겹치는 종목을 우선 정렬했습니다. 이 점수는 매수 신호나 승률이 아닙니다.")
    candidates = pd.DataFrame(st.session_state["full_candidates"])
    display_columns = [column for column in ["symbol", "name", "exchange", "screen_score", "sources", "price", "change_pct", "volume", "turnover"] if column in candidates]
    st.dataframe(candidates[display_columns], hide_index=True, use_container_width=True)
    selected = st.selectbox(
        "전종목 후보를 골라 정밀 분석",
        options=[""] + list(range(len(st.session_state["full_candidates"]))),
        format_func=lambda index: "후보 선택" if index == "" else f"{st.session_state['full_candidates'][index]['symbol']} · {st.session_state['full_candidates'][index]['name']}",
    )
    if selected != "":
        selected_candidate = st.session_state["full_candidates"][selected]
        symbol = str(selected_candidate["symbol"])
        if market == Market.US and selected_candidate.get("exchange"):
            exchange = str(selected_candidate["exchange"])
        analyze_candidate = st.button(f"{symbol} 정밀 분석", type="primary")

if not symbol:
    st.info("왼쪽에 종목코드 또는 티커를 입력하고 **선택 종목 분석**을 누르세요. 자동 새로고침은 기본 해제되어 API 호출을 줄입니다.")
    st.stop()
if not (analyze_now or analyze_candidate or live):
    st.info("준비되었습니다. **선택 종목 분석**을 눌러 KIS 시세를 요청하세요.")
    st.stop()

store = ValidationStore(VALIDATION_ROOT, event_store=event_store)
try:
    quote = load_quote(symbol, market.value, exchange)
    bars = load_bars(symbol, market.value, exchange)
    cycle = cycle_store.get(symbol, market, quote.timestamp)
    preliminary = analyze(
        quote, bars, orderbook_required=True, round_trip_cost_pct=float(cost_pct),
        minimum_score=int(min_score), cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
    )
    calibration = calibration_for(
        store, market=market.value, session=quote.session, strategy=preliminary.strategy, score=preliminary.score,
        version=APP_VERSION,
    )
    plan = analyze(
        quote, bars, orderbook_required=True, round_trip_cost_pct=float(cost_pct), minimum_score=int(min_score),
        cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
        calibration_probability=calibration.probability_pct, calibration_samples=calibration.samples,
    )
    if event_store.configured:
        marker = str(plan.diagnostics.get("completed_bar_at") or quote.timestamp.isoformat())
        cycle = cycle_store.apply_risk_state(cycle, plan.risk_state, marker)
except (KISError, ValueError, KeyError, OSError) as exc:
    st.error(f"KIS 데이터 수신 실패: {exc}")
    st.caption("실시간 현재가와 호가를 확인하지 못했으므로 진입·목표·손절가는 표시하지 않습니다.")
    st.stop()
except Exception as exc:
    st.error(f"예상하지 못한 오류: {type(exc).__name__}: {exc}")
    st.stop()

css = "buy" if plan.signal == Signal.BUY else "block" if plan.signal in (Signal.BLOCK, Signal.SELL) else "unknown" if plan.signal == Signal.UNVERIFIED else "wait"
st.markdown(
    f'<div class="signal {css}">{html.escape(plan.signal.value)} · {html.escape(plan.strategy)}</div>',
    unsafe_allow_html=True,
)

if plan.signal == Signal.BUY:
    st.success("현재 조건에서는 진입 기준가와 구조 무효화 가격이 계산되었습니다. 완료 5분봉 구조의 1차 목표 도달 시 일부 청산 여부를 직접 판단하세요.")
elif plan.signal == Signal.WAIT:
    st.info("가격 기준은 보여 드리지만, 현재는 진입 조건이 완성되지 않았습니다. 아래 ‘대기 이유’를 먼저 확인하세요.")
else:
    st.warning("현재 장세·유동성·데이터 조건에서 신규 진입을 피합니다. 가격 카드가 비어 있으면 필요한 구조가 아직 확인되지 않은 것입니다.")

summary = [
    ("현재가", f"{quote.price:g}"),
    ("모델 점수", f"{plan.score}/100"),
    ("지속성", f"{plan.persistence_score if plan.persistence_score is not None else '-'}점 · {plan.persistence_band}"),
    ("위험 상태", plan.risk_state),
    ("Horizon", str(plan.diagnostics.get("persistence", {}).get("horizon_state", "미확인"))),
    ("보정 확률", f"{plan.calibration_probability:.1f}%" if plan.calibration_probability is not None else f"보정 전 ({plan.calibration_samples}/30)"),
]
for column, (label, value) in zip(st.columns(6), summary):
    column.metric(label, value)

st.subheader("반복단타 가격 계획")
price_cards = [
    ("진입 기준가", plan.entry, "실제 매수 참고가"),
    ("1차 목표가 (5분)", plan.target, plan.target_basis),
    ("2차 목표가 (5분)", plan.target2, plan.target2_basis),
    ("Soft Stop", plan.soft_stop, "지지 훼손 확인 시작선"),
    ("Hard Stop", plan.hard_stop or plan.invalidation or plan.stop, plan.stop_basis),
]
for column, (label, value, basis) in zip(st.columns(5), price_cards):
    column.metric(label, f"{value:g}" if value is not None else "조건 미확인")
    column.caption(basis)

rr_value = plan.diagnostics.get("reward_risk_net")
spread_value = plan.diagnostics.get("spread_pct")
st.caption(
    f"비용 반영 1차 목표 손익비: {rr_value:.2f}" if isinstance(rr_value, (int, float)) else "비용 반영 손익비: 구조 미확인"
)
st.caption(
    f"호가 스프레드: {spread_value:.3f}% · 왕복비용 가정: {cost_pct:.2f}%" if isinstance(spread_value, (int, float)) else f"호가 스프레드: 미확인 · 왕복비용 가정: {cost_pct:.2f}%"
)

if plan.repeat_box:
    low, high = plan.repeat_box
    zone = plan.diagnostics.get("box_zone", "박스 위치 미확인")
    st.success(f"반복박스 확인: {low:g}~{high:g} · 폭 {(high / low - 1) * 100:.2f}% · 현재 위치: {zone}")
    st.caption("박스 전략은 하단 구간에서만 진입을 검토하고, 중단·상단에서는 추격하지 않습니다. 하단 아래 2개 완료 1분봉 종가가 확인되면 구조 무효화로 봅니다.")
else:
    st.caption("0.5~5.0%의 반복박스가 확인되지 않았습니다. 현재는 상승 추세 눌림 또는 장세 전환 관점으로만 평가합니다.")

if plan.reasons:
    st.subheader("지금 대기하거나 조심해야 하는 이유")
    for reason in plan.reasons:
        st.write(f"- {reason}")

st.subheader("실시간 차트 · 진입 / 1차 / 2차 목표 / 구조 무효화")
if not bars.empty:
    render_chart(bars.tail(180), plan)

with st.expander("계산 근거와 상세 지표"):
    diagnostics = plan.diagnostics
    plain = {
        "전략": plan.strategy,
        "장세": plan.regime.value,
        "완료 1분봉 수": diagnostics.get("completed_bars"),
        "1차 목표 평가 창(분)": diagnostics.get("target1_window_minutes"),
        "진입용 1분봉 저항": diagnostics.get("entry_resistance_1m"),
        "VWAP": diagnostics.get("vwap"),
        "EMA9": diagnostics.get("ema9"),
        "ATR": diagnostics.get("atr"),
        "상대거래량": diagnostics.get("rvol"),
        "거래대금 상대강도": diagnostics.get("notional_rvol"),
        "가짜신호 경고": diagnostics.get("false_signal_flags"),
        "지속성 진단": diagnostics.get("persistence"),
        "위험 상태": diagnostics.get("risk"),
        "FINAL_BUY 조건": diagnostics.get("final_buy_gates"),
        "대기 이유": plan.reasons,
        "미수신 데이터": plan.missing,
    }
    st.json(plain, expanded=True)

with st.expander("상승 여력 시나리오 (보조 참고)"):
    st.caption("아래 범위는 확정 목표가가 아닙니다. 1차 목표는 위의 완료 5분봉 구조를 사용하고, 아래는 최근 완료 1분봉 변동성으로 계산한 5~30분 보조 참고 범위입니다.")
    for column, point in zip(st.columns(4), plan.forecasts):
        column.metric(f"{point.minutes}분 · {point.direction.value}", f"{point.base:g}")
        column.caption(f"범위 {point.low:g}~{point.high:g}")

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
