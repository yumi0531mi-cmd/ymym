# -*- coding: utf-8 -*-
"""초단타 전용 Streamlit 앱.

GitHub 저장소 루트의 기존 app.py 안에 번들된 KIS 엔진만 읽어 사용한다.
기존 통합 UI는 실행하지 않는다.
"""
import ast
import base64
import importlib.abc
import importlib.util
import re
import sys
import time
import types
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

KST = timezone(timedelta(hours=9), name="KST")

KR_UNIVERSE = [
    {"ticker":"005930","name":"삼성전자","exchange":"KR","asset_type":"우량주"},
    {"ticker":"000660","name":"SK하이닉스","exchange":"KR","asset_type":"우량주"},
    {"ticker":"035420","name":"NAVER","exchange":"KR","asset_type":"우량주"},
    {"ticker":"005380","name":"현대차","exchange":"KR","asset_type":"우량주"},
    {"ticker":"069500","name":"KODEX 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"102110","name":"TIGER 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"396500","name":"TIGER 반도체TOP10","exchange":"KR","asset_type":"ETF"},
    {"ticker":"122630","name":"KODEX 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"123320","name":"TIGER 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"488080","name":"TIGER 반도체TOP10레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"423920","name":"TIGER 미국필라델피아반도체레버리지(합성)","exchange":"KR","asset_type":"레버리지 ETF"},
]
US_UNIVERSE = [
    {"ticker":"NVDA","name":"엔비디아","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"GOOGL","name":"알파벳","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AMD","name":"AMD","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"INTC","name":"인텔","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"SMH","name":"반도체 ETF","exchange":"NASDAQ","asset_type":"ETF"},
    {"ticker":"SOXL","name":"반도체 3배 레버리지 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"SOXS","name":"반도체 3배 인버스 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"TQQQ","name":"나스닥100 3배 레버리지 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
    {"ticker":"SQQQ","name":"나스닥100 3배 인버스 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
]


@st.cache_data(ttl=86400, show_spinner=False)
def kr_name_map() -> dict:
    mapping = {row["name"].replace(" ", ""): (row["ticker"], row["name"]) for row in KR_UNIVERSE}
    try:
        from pykrx import stock as krx_stock
        for market_name in ("KOSPI", "KOSDAQ"):
            for code in krx_stock.get_market_ticker_list(market=market_name):
                name = krx_stock.get_market_ticker_name(code)
                if name:
                    mapping[str(name).replace(" ", "")] = (str(code), str(name))
    except Exception:
        pass
    return mapping


def resolve_manual(value: str, market: str) -> dict | None:
    value = value.strip()
    if not value:
        return None
    if market == "국내":
        if value.isdigit():
            return {"ticker": value.zfill(6), "name": value.zfill(6), "exchange": "KR", "asset_type": "직접 검색"}
        normalized = value.replace(" ", "")
        mapping = kr_name_map()
        exact = mapping.get(normalized)
        if exact:
            return {"ticker": exact[0], "name": exact[1], "exchange": "KR", "asset_type": "직접 검색"}
        partial = next(((code, name) for key, (code, name) in mapping.items() if normalized in key), None)
        if partial:
            return {"ticker": partial[0], "name": partial[1], "exchange": "KR", "asset_type": "직접 검색"}
        return None
    ticker = re.sub(r"[^A-Z0-9.\-]", "", value.upper())
    if not ticker:
        return None
    known = next((dict(row) for row in US_UNIVERSE if row["ticker"] == ticker), None)
    return known or {"ticker": ticker, "name": ticker, "exchange": "NASDAQ", "asset_type": "직접 검색"}


def _load_bundled_sources() -> dict:
    source_path = Path(__file__).with_name("app.py")
    if not source_path.exists():
        st.error("같은 폴더에 기존 app.py가 필요합니다.")
        st.stop()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "_BUNDLED" for target in node.targets):
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    return value
    raise RuntimeError("app.py에서 번들 엔진을 찾지 못했습니다.")


_BUNDLED = _load_bundled_sources()


class _Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    PACKAGES = {"scanner", "utils", "config", "engine", "data", "ui"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in _BUNDLED:
            return importlib.util.spec_from_loader(fullname, self, is_package=False)
        if fullname in self.PACKAGES:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        if name in self.PACKAGES:
            module.__path__ = []
            module.__package__ = name
            module.__file__ = str(Path.cwd() / name / "__init__.py")
            return
        code = zlib.decompress(base64.b64decode(_BUNDLED[name])).decode("utf-8")
        module.__file__ = str(Path.cwd().joinpath(*name.split(".")).with_suffix(".py"))
        module.__package__ = name.rpartition(".")[0]
        exec(compile(code, module.__file__, "exec"), module.__dict__)


sys.meta_path.insert(0, _Loader())

from scanner.kis_engine import (  # noqa: E402
    KISUnifiedScanner,
    apply_mode_policy,
    finalize_trade_item,
)

st.set_page_config(page_title="초단타 VWAP 타점", page_icon="⚡", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1rem; max-width: 1500px;}
  [data-testid="stMetric"] {background:#f7f8fb;border:1px solid #e6e8ee;border-radius:12px;padding:8px;}
  @media(max-width:700px){.block-container{padding:.55rem}.stMetric{font-size:.8rem}}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def scanner() -> KISUnifiedScanner:
    return KISUnifiedScanner()


def fmt(value) -> str:
    try:
        number = float(value)
        if number >= 1000:
            return f"{number:,.0f}"
        if number >= 10:
            return f"{number:,.2f}"
        return f"{number:,.4f}"
    except Exception:
        return "-"


def verdict_text(item: dict) -> tuple[str, str]:
    verdict = str(item.get("chart_verdict", "WAIT"))
    if verdict == "BUY_READY" and item.get("entry_checks_passed"):
        return "🟢 매수 검토", "success"
    if verdict == "BUY_READY":
        return "🟡 차트 매수 준비·위험확인 필요", "warning"
    if verdict == "NO_ENTRY":
        return "🔴 매수 금지·매도 검토", "error"
    return "🟡 대기", "warning"


def forecast_label(percent: float) -> str:
    if percent >= 0.35:
        return "상승 우세"
    if percent <= -0.35:
        return "하락 위험"
    return "횡보·불확실"


def strategy_consensus(item: dict) -> tuple[list[dict], int, int, int]:
    """Major intraday methods vote independently; contradictory methods stay visible."""
    price = float(item.get("price", 0) or 0)
    vwap = float(item.get("vwap", 0) or 0)
    ema9 = float(item.get("ema9", 0) or 0)
    ema20 = float(item.get("ema20", 0) or 0)
    rsi = float(item.get("rsi", 50) or 50)
    macd = float(item.get("macd_histogram", 0) or 0)
    stoch = float(item.get("stochastic_k", 50) or 50)
    rvol = float(item.get("rvol", 0) or 0)
    f5 = float(item.get("forecast_5m", 0) or 0)
    f10 = float(item.get("forecast_10m", 0) or 0)
    f30 = float(item.get("forecast_30m", 0) or 0)
    orderbook = str(item.get("orderbook_signal", ""))
    obv = str(item.get("obv_trend", ""))
    patterns = " ".join(map(str, item.get("pattern_signals", []) or []))
    trend_text = " ".join(str(item.get(key, "")) for key in ("trend_5m", "trend_15m", "trend_30m"))

    votes = []
    def add(name: str, buy: bool, sell: bool, reason: str):
        signal = "매수" if buy and not sell else "매도" if sell and not buy else "대기"
        votes.append({"기법": name, "판정": signal, "근거": reason})

    add("VWAP 추세", price > vwap > 0, 0 < price < vwap, f"현재가 {('위' if price > vwap else '아래')}·VWAP {fmt(vwap)}")
    add("EMA 추세", ema9 > ema20 > 0 and price >= ema9, 0 < ema9 < ema20 and price <= ema9, f"EMA9 {fmt(ema9)} / EMA20 {fmt(ema20)}")
    add("MACD 모멘텀", macd > 0, macd < 0, f"히스토그램 {macd:+.4f}")
    add("RSI·스토캐스틱", 42 <= rsi <= 68 and stoch < 80, rsi >= 75 or stoch >= 90, f"RSI {rsi:.1f} / %K {stoch:.1f}")
    add("거래량·RVOL", rvol >= 1.5 and f5 > 0, rvol < 0.7 or (rvol >= 1.5 and f5 < 0), f"RVOL {rvol:.1f}배")
    add("OBV 수급", "상승" in obv, "하락" in obv, obv or "미확인")
    add("호가·체결", "매수" in orderbook, "매도" in orderbook, orderbook or "미확인")
    add("캔들 패턴", any(x in patterns for x in ("상승", "망치", "돌파", "장악")), any(x in patterns for x in ("하락", "유성", "윗꼬리")), patterns or "뚜렷한 패턴 없음")
    add("다중 시간봉", all(x > 0 for x in (f5, f10, f30)), any(x < 0 for x in (f5, f10, f30)), f"5분 {f5:+.2f}% / 10분 {f10:+.2f}% / 30분 {f30:+.2f}%")
    add("추세 정렬", trend_text.count("상승") >= 2, trend_text.count("하락") >= 2, trend_text or "추세 계산 중")
    buys = sum(row["판정"] == "매수" for row in votes)
    sells = sum(row["판정"] == "매도" for row in votes)
    waits = len(votes) - buys - sells
    return votes, buys, sells, waits


def render_chart(item: dict) -> None:
    times = item.get("chart_time_1m", [])
    closes = item.get("chart_close_1m", [])
    if not times or len(times) != len(closes):
        st.info("1분봉을 수집 중입니다.")
        return
    frame = pd.DataFrame({
        "시간": pd.to_datetime(times, errors="coerce"),
        "시가": item.get("chart_open_1m", []),
        "고가": item.get("chart_high_1m", []),
        "저가": item.get("chart_low_1m", []),
        "종가": closes,
        "VWAP": item.get("chart_vwap_1m", []),
        "EMA9": item.get("chart_ema9_1m", []),
        "EMA20": item.get("chart_ema20_1m", []),
        "신호": item.get("chart_signal_1m", []),
    }).dropna(subset=["시간"])
    frame["색상"] = frame.apply(lambda row: "상승" if row["종가"] >= row["시가"] else "하락", axis=1)
    color_scale = alt.Scale(domain=["상승", "하락"], range=["#ef5350", "#2962ff"])
    base = alt.Chart(frame).encode(x=alt.X("시간:T", title=None))
    wick = base.mark_rule().encode(
        y=alt.Y("저가:Q", scale=alt.Scale(zero=False), title="가격"), y2="고가:Q",
        color=alt.Color("색상:N", scale=color_scale, legend=None))
    body = base.mark_bar(size=7).encode(
        y="시가:Q", y2="종가:Q", color=alt.Color("색상:N", scale=color_scale, legend=None))
    lines = alt.Chart(frame).transform_fold(
        ["VWAP", "EMA9", "EMA20"], as_=["지표", "값"]
    ).mark_line(strokeWidth=2).encode(
        x="시간:T", y=alt.Y("값:Q", scale=alt.Scale(zero=False)),
        color=alt.Color("지표:N", scale=alt.Scale(
            domain=["VWAP", "EMA9", "EMA20"], range=["#f9a825", "#00a86b", "#8e44ad"]), title=None))
    buy = alt.Chart(frame[frame["신호"] == "BUY"]).mark_point(
        shape="triangle-up", filled=True, size=180, color="#00a86b").encode(x="시간:T", y="저가:Q")
    sell = alt.Chart(frame[frame["신호"] == "SELL"]).mark_point(
        shape="triangle-down", filled=True, size=180, color="#d32f2f").encode(x="시간:T", y="고가:Q")
    st.altair_chart((wick + body + lines + buy + sell).properties(height=430), use_container_width=True)


def precise_analysis(row: dict, mode: str) -> dict:
    raw = scanner().analyze(dict(row), mode)
    return apply_mode_policy(finalize_trade_item(raw), mode)


st_autorefresh(interval=1000, key="scalp_tick")
st.title("⚡ 초단타 VWAP 매수타점")
st.caption("선택 종목은 약 1초마다 현재가를 확인하고, 20초마다 1분봉·VWAP·EMA·거래량·호가를 정밀 재분석합니다.")

with st.sidebar:
    st.header("초단타 설정")
    market = st.radio("시장", ["국내", "미국"], horizontal=True)
    mode = "국내 30분 1% 타점" if market == "국내" else "미국 30분 1% 타점"
    exchange = "KR" if market == "국내" else "자동 판별"
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input("종목명 또는 종목코드 검색", placeholder="현대차, 005380, SOXL").strip()
    focus_only = st.toggle("선택 종목 1초 집중", True)
    st.caption("분석 대상: 우량주·ETF·레버리지 ETF")

now = time.time()
options = [dict(row) for row in (KR_UNIVERSE if market == "국내" else US_UNIVERSE)]
if manual_ticker:
    resolved = resolve_manual(manual_ticker, market)
    if resolved:
        options.insert(0, resolved)
    else:
        st.sidebar.error("종목을 찾지 못했습니다. 이름을 조금 더 정확히 입력해 주세요.")
dedup = {}
for row in options:
    ticker = str(row.get("ticker", "")).upper()
    if ticker:
        dedup[ticker] = row
options = list(dedup.values())

selected_ticker = st.selectbox(
    "집중 분석할 종목",
    [str(row.get("ticker", "")) for row in options],
    format_func=lambda ticker: next(
        (f"{ticker} · {row.get('name', ticker)} · {row.get('asset_type', '')}"
         for row in options if str(row.get("ticker", "")) == ticker), ticker),
)
selected_row = next(row for row in options if str(row.get("ticker", "")) == selected_ticker)
selected_row.setdefault("exchange", "KR" if market == "국내" else "NASDAQ")

if st.session_state.get("scalp_selected") != selected_ticker:
    st.session_state["scalp_selected"] = selected_ticker
    st.session_state["scalp_last_precise"] = 0.0
    st.session_state.pop("scalp_latest", None)
    st.session_state["scalp_live_history"] = []

latest = dict(st.session_state.get("scalp_latest", {}))
precise_due = now - float(st.session_state.get("scalp_last_precise", 0)) >= 20
if precise_due or not latest:
    with st.spinner(f"{selected_ticker} 1분봉 정밀분석 중..."):
        try:
            latest = precise_analysis(selected_row, mode)
            st.session_state["scalp_latest"] = latest
            st.session_state["scalp_last_precise"] = now
            st.session_state.pop("scalp_error", None)
        except Exception as error:
            st.session_state["scalp_error"] = str(error)
elif now - float(st.session_state.get("scalp_last_quote", 0)) >= 1:
    try:
        refreshed = scanner().refresh_quotes([latest], mode)
        if refreshed:
            latest.update(refreshed[0])
            st.session_state["scalp_latest"] = latest
        st.session_state["scalp_last_quote"] = now
    except Exception as error:
        st.session_state["scalp_quote_error"] = str(error)

if st.session_state.get("scalp_error"):
    st.error(f"분석 대기: {st.session_state['scalp_error']}")
if not latest:
    st.stop()

if latest.get("intraday_fallback"):
    st.warning("KIS 미국 분봉이 부족하여 Yahoo 보조 분봉을 표시합니다. 이때는 실시간 매수 신호를 내지 않고 관찰만 합니다.")

price = float(latest.get("price", 0) or 0)
change = float(latest.get("change_percent", 0) or 0)
label, level = verdict_text(latest)
strategy_rows, buy_votes, sell_votes, wait_votes = strategy_consensus(latest)
price_limit = 300000 if market == "국내" else 200
if price <= 0:
    st.error("⛔ 현재가가 확인되지 않아 분석과 매수 판정을 중단했습니다.")
    st.stop()
if price > price_limit:
    level, label = "error", f"🔴 설정 금액 초과 · {fmt(price_limit)} 이하만 분석 대상"
    latest["entry_checks_passed"] = False
elif sell_votes >= 3:
    level, label = "error", f"🔴 진입 금지 · 매도/약세 기법 {sell_votes}개 감지"
    latest["entry_checks_passed"] = False
elif buy_votes < 6 or sell_votes > 0:
    level, label = "warning", f"🟡 대기 · 매수 합의 {buy_votes}/10 · 매도 경고 {sell_votes}/10"
    latest["entry_checks_passed"] = False
elif level == "success":
    label = f"🟢 매수 검토 · 주요 기법 합의 {buy_votes}/10"
top = st.columns([1.4, 1, 1, 1, 1])
top[0].metric(f"{latest.get('ticker')} · {latest.get('name')}", fmt(price), f"{change:+.2f}%")
top[1].metric("VWAP", fmt(latest.get("vwap")))
top[2].metric("EMA9", fmt(latest.get("ema9")))
top[3].metric("RVOL", f"{float(latest.get('rvol', 0) or 0):.1f}배")
top[4].metric("하락위험", f"{int(latest.get('five_min_risk_score', 0) or 0)}점")

getattr(st, level)(label)
if level == "success":
    st.write(f"진입: **{fmt(latest.get('pullback_entry'))}** · 손절: **{fmt(latest.get('stop_loss'))}** · +1%: **{fmt(price * 1.01)}**")
elif level == "error":
    st.write(latest.get("invalidation_reason", "VWAP·EMA·호가 조건 이탈"))
else:
    st.write(latest.get("entry_trigger", "VWAP 지지와 거래량 재증가를 기다리세요."))

consensus_cols = st.columns(3)
consensus_cols[0].metric("매수 기법", f"{buy_votes}/10")
consensus_cols[1].metric("매도 기법", f"{sell_votes}/10")
consensus_cols[2].metric("대기 기법", f"{wait_votes}/10")
with st.expander("매수·매도 기법별 판정 근거", expanded=False):
    st.dataframe(pd.DataFrame(strategy_rows), hide_index=True, use_container_width=True)

f5 = float(latest.get("forecast_5m", 0) or 0)
f10 = float(latest.get("forecast_10m", 0) or 0)
f30 = float(latest.get("forecast_30m", 0) or 0)
forecast_cols = st.columns(3)
for column, minutes, forecast in zip(forecast_cols, (5, 10, 30), (f5, f10, f30)):
    expected = price * (1 + forecast / 100)
    uncertainty = max(0.20, abs(forecast) * 0.50)
    low = price * (1 + (forecast - uncertainty) / 100)
    high = price * (1 + (forecast + uncertainty) / 100)
    column.metric(
        f"{minutes}분 예상 도달가 · {forecast_label(forecast)}",
        fmt(expected),
        f"{forecast:+.2f}%",
    )
    column.caption(f"예상 범위 {fmt(low)}~{fmt(high)}")

target_probability = int(latest.get("target1_probability", 0) or 0)
if level == "success" and f5 > 0 and f10 > 0 and f30 > 0:
    st.success(
        f"🚦 매수 전 조건 통과 · 5·10·30분 방향 일치 · "
        f"1차 목표 도달 추정 {target_probability}%"
    )
elif f5 < 0 or f10 < 0 or f30 < 0:
    st.error("⛔ 단기 예상이 약세입니다. 신규 진입하지 마세요.")
else:
    st.warning("⏳ 시간대별 방향이 일치하지 않습니다. 진입을 기다리세요.")

st.subheader("현재 차트가 가리키는 예상 도달가격")
path_cols = st.columns(3)
for column, minutes, forecast in zip(path_cols, (5, 10, 30), (f5, f10, f30)):
    expected = price * (1 + forecast / 100)
    direction = "상승" if forecast > 0 else "하락" if forecast < 0 else "횡보"
    column.metric(f"{minutes}분 뒤 {direction}", fmt(expected), f"현재가 대비 {forecast:+.2f}%")

st.subheader("+1% · +2% · +3% 도달 여부 점검")
goal_cols = st.columns(3)
upside = max(f5, f10, f30)
for column, goal in zip(goal_cols, (1, 2, 3)):
    goal_price = price * (1 + goal / 100)
    if level == "success" and min(f5, f10, f30) > 0 and upside >= goal:
        status = "도달 가능 구간"
    elif min(f5, f10, f30) < 0:
        status = "진입 금지"
    else:
        status = "아직 부족·대기"
    column.metric(f"+{goal}% 목표", fmt(goal_price), status)
st.caption("표시 가격은 현재 데이터에서 가장 가능성이 높은 추정 경로입니다. 실제 체결·뉴스·호가 변화로 달라질 수 있습니다.")

render_chart(latest)

with st.expander("진입 전 뉴스·공시 위험을 지금 한 번 확인"):
    if st.button("뉴스·SEC·거래정지 확인", use_container_width=True):
        with st.spinner("위험자료 확인 중..."):
            try:
                checked = scanner().analyze_candidate(selected_row, mode)
                st.session_state["scalp_risk_check"] = checked
                st.session_state["scalp_latest"] = checked
            except Exception as error:
                st.error(str(error))
    checked = st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker")) == selected_ticker:
        st.write(checked.get("news_summary", "뉴스 확인 완료"))
        st.write("규제검증:", checked.get("regulatory_checked", False), "· 거래정지:", checked.get("halt_active", False))

st.caption(f"마지막 정밀분석: {latest.get('updated_at', '-')} · 화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
# -*- coding: utf-8 -*-
"""초단타 전용 Streamlit 앱.

GitHub 저장소 루트의 기존 app.py 안에 번들된 KIS 엔진만 읽어 사용한다.
기존 통합 UI는 실행하지 않는다.
"""
import ast
import base64
import importlib.abc
import importlib.util
import re
import sys
import time
import types
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

KST = timezone(timedelta(hours=9), name="KST")

KR_UNIVERSE = [
    {"ticker":"005930","name":"삼성전자","exchange":"KR","asset_type":"우량주"},
    {"ticker":"000660","name":"SK하이닉스","exchange":"KR","asset_type":"우량주"},
    {"ticker":"035420","name":"NAVER","exchange":"KR","asset_type":"우량주"},
    {"ticker":"005380","name":"현대차","exchange":"KR","asset_type":"우량주"},
    {"ticker":"069500","name":"KODEX 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"102110","name":"TIGER 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"396500","name":"TIGER 반도체TOP10","exchange":"KR","asset_type":"ETF"},
    {"ticker":"122630","name":"KODEX 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"123320","name":"TIGER 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"488080","name":"TIGER 반도체TOP10레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"423920","name":"TIGER 미국필라델피아반도체레버리지(합성)","exchange":"KR","asset_type":"레버리지 ETF"},
]
US_UNIVERSE = [
    {"ticker":"NVDA","name":"엔비디아","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"GOOGL","name":"알파벳","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AMD","name":"AMD","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"INTC","name":"인텔","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"SMH","name":"반도체 ETF","exchange":"NASDAQ","asset_type":"ETF"},
    {"ticker":"SOXL","name":"반도체 3배 레버리지 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"SOXS","name":"반도체 3배 인버스 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"TQQQ","name":"나스닥100 3배 레버리지 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
    {"ticker":"SQQQ","name":"나스닥100 3배 인버스 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
]


@st.cache_data(ttl=86400, show_spinner=False)
def kr_name_map() -> dict:
    mapping = {row["name"].replace(" ", ""): (row["ticker"], row["name"]) for row in KR_UNIVERSE}
    try:
        from pykrx import stock as krx_stock
        for market_name in ("KOSPI", "KOSDAQ"):
            for code in krx_stock.get_market_ticker_list(market=market_name):
                name = krx_stock.get_market_ticker_name(code)
                if name:
                    mapping[str(name).replace(" ", "")] = (str(code), str(name))
    except Exception:
        pass
    return mapping


def resolve_manual(value: str, market: str) -> dict | None:
    value = value.strip()
    if not value:
        return None
    if market == "국내":
        if value.isdigit():
            return {"ticker": value.zfill(6), "name": value.zfill(6), "exchange": "KR", "asset_type": "직접 검색"}
        normalized = value.replace(" ", "")
        mapping = kr_name_map()
        exact = mapping.get(normalized)
        if exact:
            return {"ticker": exact[0], "name": exact[1], "exchange": "KR", "asset_type": "직접 검색"}
        partial = next(((code, name) for key, (code, name) in mapping.items() if normalized in key), None)
        if partial:
            return {"ticker": partial[0], "name": partial[1], "exchange": "KR", "asset_type": "직접 검색"}
        return None
    ticker = re.sub(r"[^A-Z0-9.\-]", "", value.upper())
    if not ticker:
        return None
    known = next((dict(row) for row in US_UNIVERSE if row["ticker"] == ticker), None)
    return known or {"ticker": ticker, "name": ticker, "exchange": "NASDAQ", "asset_type": "직접 검색"}


def _load_bundled_sources() -> dict:
    source_path = Path(__file__).with_name("app.py")
    if not source_path.exists():
        st.error("같은 폴더에 기존 app.py가 필요합니다.")
        st.stop()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "_BUNDLED" for target in node.targets):
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    return value
    raise RuntimeError("app.py에서 번들 엔진을 찾지 못했습니다.")


_BUNDLED = _load_bundled_sources()


class _Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    PACKAGES = {"scanner", "utils", "config", "engine", "data", "ui"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in _BUNDLED:
            return importlib.util.spec_from_loader(fullname, self, is_package=False)
        if fullname in self.PACKAGES:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__
        if name in self.PACKAGES:
            module.__path__ = []
            module.__package__ = name
            module.__file__ = str(Path.cwd() / name / "__init__.py")
            return
        code = zlib.decompress(base64.b64decode(_BUNDLED[name])).decode("utf-8")
        module.__file__ = str(Path.cwd().joinpath(*name.split(".")).with_suffix(".py"))
        module.__package__ = name.rpartition(".")[0]
        exec(compile(code, module.__file__, "exec"), module.__dict__)


sys.meta_path.insert(0, _Loader())

from scanner.kis_engine import (  # noqa: E402
    KISUnifiedScanner,
    apply_mode_policy,
    finalize_trade_item,
)

st.set_page_config(page_title="초단타 VWAP 타점", page_icon="⚡", layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1rem; max-width: 1500px;}
  [data-testid="stMetric"] {background:#f7f8fb;border:1px solid #e6e8ee;border-radius:12px;padding:8px;}
  @media(max-width:700px){.block-container{padding:.55rem}.stMetric{font-size:.8rem}}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def scanner() -> KISUnifiedScanner:
    return KISUnifiedScanner()


def fmt(value) -> str:
    try:
        number = float(value)
        if number >= 1000:
            return f"{number:,.0f}"
        if number >= 10:
            return f"{number:,.2f}"
        return f"{number:,.4f}"
    except Exception:
        return "-"


def verdict_text(item: dict) -> tuple[str, str]:
    verdict = str(item.get("chart_verdict", "WAIT"))
    if verdict == "BUY_READY" and item.get("entry_checks_passed"):
        return "🟢 매수 검토", "success"
    if verdict == "BUY_READY":
        return "🟡 차트 매수 준비·위험확인 필요", "warning"
    if verdict == "NO_ENTRY":
        return "🔴 매수 금지·매도 검토", "error"
    return "🟡 대기", "warning"


def forecast_label(percent: float) -> str:
    if percent >= 0.35:
        return "상승 우세"
    if percent <= -0.35:
        return "하락 위험"
    return "횡보·불확실"


def strategy_consensus(item: dict) -> tuple[list[dict], int, int, int]:
    """Major intraday methods vote independently; contradictory methods stay visible."""
    price = float(item.get("price", 0) or 0)
    vwap = float(item.get("vwap", 0) or 0)
    ema9 = float(item.get("ema9", 0) or 0)
    ema20 = float(item.get("ema20", 0) or 0)
    rsi = float(item.get("rsi", 50) or 50)
    macd = float(item.get("macd_histogram", 0) or 0)
    stoch = float(item.get("stochastic_k", 50) or 50)
    rvol = float(item.get("rvol", 0) or 0)
    f5 = float(item.get("forecast_5m", 0) or 0)
    f10 = float(item.get("forecast_10m", 0) or 0)
    f30 = float(item.get("forecast_30m", 0) or 0)
    orderbook = str(item.get("orderbook_signal", ""))
    obv = str(item.get("obv_trend", ""))
    patterns = " ".join(map(str, item.get("pattern_signals", []) or []))
    trend_text = " ".join(str(item.get(key, "")) for key in ("trend_5m", "trend_15m", "trend_30m"))

    votes = []
    def add(name: str, buy: bool, sell: bool, reason: str):
        signal = "매수" if buy and not sell else "매도" if sell and not buy else "대기"
        votes.append({"기법": name, "판정": signal, "근거": reason})

    add("VWAP 추세", price > vwap > 0, 0 < price < vwap, f"현재가 {('위' if price > vwap else '아래')}·VWAP {fmt(vwap)}")
    add("EMA 추세", ema9 > ema20 > 0 and price >= ema9, 0 < ema9 < ema20 and price <= ema9, f"EMA9 {fmt(ema9)} / EMA20 {fmt(ema20)}")
    add("MACD 모멘텀", macd > 0, macd < 0, f"히스토그램 {macd:+.4f}")
    add("RSI·스토캐스틱", 42 <= rsi <= 68 and stoch < 80, rsi >= 75 or stoch >= 90, f"RSI {rsi:.1f} / %K {stoch:.1f}")
    add("거래량·RVOL", rvol >= 1.5 and f5 > 0, rvol < 0.7 or (rvol >= 1.5 and f5 < 0), f"RVOL {rvol:.1f}배")
    add("OBV 수급", "상승" in obv, "하락" in obv, obv or "미확인")
    add("호가·체결", "매수" in orderbook, "매도" in orderbook, orderbook or "미확인")
    add("캔들 패턴", any(x in patterns for x in ("상승", "망치", "돌파", "장악")), any(x in patterns for x in ("하락", "유성", "윗꼬리")), patterns or "뚜렷한 패턴 없음")
    add("다중 시간봉", all(x > 0 for x in (f5, f10, f30)), any(x < 0 for x in (f5, f10, f30)), f"5분 {f5:+.2f}% / 10분 {f10:+.2f}% / 30분 {f30:+.2f}%")
    add("추세 정렬", trend_text.count("상승") >= 2, trend_text.count("하락") >= 2, trend_text or "추세 계산 중")
    buys = sum(row["판정"] == "매수" for row in votes)
    sells = sum(row["판정"] == "매도" for row in votes)
    waits = len(votes) - buys - sells
    return votes, buys, sells, waits


def render_chart(item: dict) -> None:
    times = item.get("chart_time_1m", [])
    closes = item.get("chart_close_1m", [])
    if not times or len(times) != len(closes):
        st.info("1분봉을 수집 중입니다.")
        return
    frame = pd.DataFrame({
        "시간": pd.to_datetime(times, errors="coerce"),
        "시가": item.get("chart_open_1m", []),
        "고가": item.get("chart_high_1m", []),
        "저가": item.get("chart_low_1m", []),
        "종가": closes,
        "VWAP": item.get("chart_vwap_1m", []),
        "EMA9": item.get("chart_ema9_1m", []),
        "EMA20": item.get("chart_ema20_1m", []),
        "신호": item.get("chart_signal_1m", []),
    }).dropna(subset=["시간"])
    frame["색상"] = frame.apply(lambda row: "상승" if row["종가"] >= row["시가"] else "하락", axis=1)
    color_scale = alt.Scale(domain=["상승", "하락"], range=["#ef5350", "#2962ff"])
    base = alt.Chart(frame).encode(x=alt.X("시간:T", title=None))
    wick = base.mark_rule().encode(
        y=alt.Y("저가:Q", scale=alt.Scale(zero=False), title="가격"), y2="고가:Q",
        color=alt.Color("색상:N", scale=color_scale, legend=None))
    body = base.mark_bar(size=7).encode(
        y="시가:Q", y2="종가:Q", color=alt.Color("색상:N", scale=color_scale, legend=None))
    lines = alt.Chart(frame).transform_fold(
        ["VWAP", "EMA9", "EMA20"], as_=["지표", "값"]
    ).mark_line(strokeWidth=2).encode(
        x="시간:T", y=alt.Y("값:Q", scale=alt.Scale(zero=False)),
        color=alt.Color("지표:N", scale=alt.Scale(
            domain=["VWAP", "EMA9", "EMA20"], range=["#f9a825", "#00a86b", "#8e44ad"]), title=None))
    buy = alt.Chart(frame[frame["신호"] == "BUY"]).mark_point(
        shape="triangle-up", filled=True, size=180, color="#00a86b").encode(x="시간:T", y="저가:Q")
    sell = alt.Chart(frame[frame["신호"] == "SELL"]).mark_point(
        shape="triangle-down", filled=True, size=180, color="#d32f2f").encode(x="시간:T", y="고가:Q")
    st.altair_chart((wick + body + lines + buy + sell).properties(height=430), use_container_width=True)


def precise_analysis(row: dict, mode: str) -> dict:
    raw = scanner().analyze(dict(row), mode)
    return apply_mode_policy(finalize_trade_item(raw), mode)


st_autorefresh(interval=1000, key="scalp_tick")
st.title("⚡ 초단타 VWAP 매수타점")
st.caption("선택 종목은 약 1초마다 현재가를 확인하고, 20초마다 1분봉·VWAP·EMA·거래량·호가를 정밀 재분석합니다.")

with st.sidebar:
    st.header("초단타 설정")
    market = st.radio("시장", ["국내", "미국"], horizontal=True)
    mode = "국내 30분 1% 타점" if market == "국내" else "미국 30분 1% 타점"
    exchange = "KR" if market == "국내" else "자동 판별"
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input("종목명 또는 종목코드 검색", placeholder="현대차, 005380, SOXL").strip()
    focus_only = st.toggle("선택 종목 1초 집중", True)
    st.caption("분석 대상: 우량주·ETF·레버리지 ETF")

now = time.time()
options = [dict(row) for row in (KR_UNIVERSE if market == "국내" else US_UNIVERSE)]
if manual_ticker:
    resolved = resolve_manual(manual_ticker, market)
    if resolved:
        options.insert(0, resolved)
    else:
        st.sidebar.error("종목을 찾지 못했습니다. 이름을 조금 더 정확히 입력해 주세요.")
dedup = {}
for row in options:
    ticker = str(row.get("ticker", "")).upper()
    if ticker:
        dedup[ticker] = row
options = list(dedup.values())

selected_ticker = st.selectbox(
    "집중 분석할 종목",
    [str(row.get("ticker", "")) for row in options],
    format_func=lambda ticker: next(
        (f"{ticker} · {row.get('name', ticker)} · {row.get('asset_type', '')}"
         for row in options if str(row.get("ticker", "")) == ticker), ticker),
)
selected_row = next(row for row in options if str(row.get("ticker", "")) == selected_ticker)
selected_row.setdefault("exchange", "KR" if market == "국내" else "NASDAQ")

if st.session_state.get("scalp_selected") != selected_ticker:
    st.session_state["scalp_selected"] = selected_ticker
    st.session_state["scalp_last_precise"] = 0.0
    st.session_state.pop("scalp_latest", None)
    st.session_state["scalp_live_history"] = []

latest = dict(st.session_state.get("scalp_latest", {}))
precise_due = now - float(st.session_state.get("scalp_last_precise", 0)) >= 20
if precise_due or not latest:
    with st.spinner(f"{selected_ticker} 1분봉 정밀분석 중..."):
        try:
            latest = precise_analysis(selected_row, mode)
            st.session_state["scalp_latest"] = latest
            st.session_state["scalp_last_precise"] = now
            st.session_state.pop("scalp_error", None)
        except Exception as error:
            st.session_state["scalp_error"] = str(error)
elif now - float(st.session_state.get("scalp_last_quote", 0)) >= 1:
    try:
        refreshed = scanner().refresh_quotes([latest], mode)
        if refreshed:
            latest.update(refreshed[0])
            st.session_state["scalp_latest"] = latest
        st.session_state["scalp_last_quote"] = now
    except Exception as error:
        st.session_state["scalp_quote_error"] = str(error)

if st.session_state.get("scalp_error"):
    st.error(f"분석 대기: {st.session_state['scalp_error']}")
if not latest:
    st.stop()

if latest.get("intraday_fallback"):
    st.warning("KIS 미국 분봉이 부족하여 Yahoo 보조 분봉을 표시합니다. 이때는 실시간 매수 신호를 내지 않고 관찰만 합니다.")

price = float(latest.get("price", 0) or 0)
change = float(latest.get("change_percent", 0) or 0)
label, level = verdict_text(latest)
strategy_rows, buy_votes, sell_votes, wait_votes = strategy_consensus(latest)
price_limit = 300000 if market == "국내" else 200
if price <= 0:
    st.error("⛔ 현재가가 확인되지 않아 분석과 매수 판정을 중단했습니다.")
    st.stop()
if price > price_limit:
    level, label = "error", f"🔴 설정 금액 초과 · {fmt(price_limit)} 이하만 분석 대상"
    latest["entry_checks_passed"] = False
elif sell_votes >= 3:
    level, label = "error", f"🔴 진입 금지 · 매도/약세 기법 {sell_votes}개 감지"
    latest["entry_checks_passed"] = False
elif buy_votes < 6 or sell_votes > 0:
    level, label = "warning", f"🟡 대기 · 매수 합의 {buy_votes}/10 · 매도 경고 {sell_votes}/10"
    latest["entry_checks_passed"] = False
elif level == "success":
    label = f"🟢 매수 검토 · 주요 기법 합의 {buy_votes}/10"
top = st.columns([1.4, 1, 1, 1, 1])
top[0].metric(f"{latest.get('ticker')} · {latest.get('name')}", fmt(price), f"{change:+.2f}%")
top[1].metric("VWAP", fmt(latest.get("vwap")))
top[2].metric("EMA9", fmt(latest.get("ema9")))
top[3].metric("RVOL", f"{float(latest.get('rvol', 0) or 0):.1f}배")
top[4].metric("하락위험", f"{int(latest.get('five_min_risk_score', 0) or 0)}점")

getattr(st, level)(label)
if level == "success":
    st.write(f"진입: **{fmt(latest.get('pullback_entry'))}** · 손절: **{fmt(latest.get('stop_loss'))}** · +1%: **{fmt(price * 1.01)}**")
elif level == "error":
    st.write(latest.get("invalidation_reason", "VWAP·EMA·호가 조건 이탈"))
else:
    st.write(latest.get("entry_trigger", "VWAP 지지와 거래량 재증가를 기다리세요."))

consensus_cols = st.columns(3)
consensus_cols[0].metric("매수 기법", f"{buy_votes}/10")
consensus_cols[1].metric("매도 기법", f"{sell_votes}/10")
consensus_cols[2].metric("대기 기법", f"{wait_votes}/10")
with st.expander("매수·매도 기법별 판정 근거", expanded=False):
    st.dataframe(pd.DataFrame(strategy_rows), hide_index=True, use_container_width=True)

f5 = float(latest.get("forecast_5m", 0) or 0)
f10 = float(latest.get("forecast_10m", 0) or 0)
f30 = float(latest.get("forecast_30m", 0) or 0)
forecast_cols = st.columns(3)
for column, minutes, forecast in zip(forecast_cols, (5, 10, 30), (f5, f10, f30)):
    expected = price * (1 + forecast / 100)
    uncertainty = max(0.20, abs(forecast) * 0.50)
    low = price * (1 + (forecast - uncertainty) / 100)
    high = price * (1 + (forecast + uncertainty) / 100)
    column.metric(
        f"{minutes}분 예상 도달가 · {forecast_label(forecast)}",
        fmt(expected),
        f"{forecast:+.2f}%",
    )
    column.caption(f"예상 범위 {fmt(low)}~{fmt(high)}")

target_probability = int(latest.get("target1_probability", 0) or 0)
if level == "success" and f5 > 0 and f10 > 0 and f30 > 0:
    st.success(
        f"🚦 매수 전 조건 통과 · 5·10·30분 방향 일치 · "
        f"1차 목표 도달 추정 {target_probability}%"
    )
elif f5 < 0 or f10 < 0 or f30 < 0:
    st.error("⛔ 단기 예상이 약세입니다. 신규 진입하지 마세요.")
else:
    st.warning("⏳ 시간대별 방향이 일치하지 않습니다. 진입을 기다리세요.")

st.subheader("현재 차트가 가리키는 예상 도달가격")
path_cols = st.columns(3)
for column, minutes, forecast in zip(path_cols, (5, 10, 30), (f5, f10, f30)):
    expected = price * (1 + forecast / 100)
    direction = "상승" if forecast > 0 else "하락" if forecast < 0 else "횡보"
    column.metric(f"{minutes}분 뒤 {direction}", fmt(expected), f"현재가 대비 {forecast:+.2f}%")

st.subheader("+1% · +2% · +3% 도달 여부 점검")
goal_cols = st.columns(3)
upside = max(f5, f10, f30)
for column, goal in zip(goal_cols, (1, 2, 3)):
    goal_price = price * (1 + goal / 100)
    if level == "success" and min(f5, f10, f30) > 0 and upside >= goal:
        status = "도달 가능 구간"
    elif min(f5, f10, f30) < 0:
        status = "진입 금지"
    else:
        status = "아직 부족·대기"
    column.metric(f"+{goal}% 목표", fmt(goal_price), status)
st.caption("표시 가격은 현재 데이터에서 가장 가능성이 높은 추정 경로입니다. 실제 체결·뉴스·호가 변화로 달라질 수 있습니다.")

render_chart(latest)

with st.expander("진입 전 뉴스·공시 위험을 지금 한 번 확인"):
    if st.button("뉴스·SEC·거래정지 확인", use_container_width=True):
        with st.spinner("위험자료 확인 중..."):
            try:
                checked = scanner().analyze_candidate(selected_row, mode)
                st.session_state["scalp_risk_check"] = checked
                st.session_state["scalp_latest"] = checked
            except Exception as error:
                st.error(str(error))
    checked = st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker")) == selected_ticker:
        st.write(checked.get("news_summary", "뉴스 확인 완료"))
        st.write("규제검증:", checked.get("regulatory_checked", False), "· 거래정지:", checked.get("halt_active", False))

st.caption(f"마지막 정밀분석: {latest.get('updated_at', '-')} · 화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
