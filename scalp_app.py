# -*- coding: utf-8 -*-
"""초단타 전용 Streamlit 앱.

GitHub 저장소 루트의 기존 app.py 안에 번들된 KIS 엔진만 읽어 사용한다.
기존 통합 UI는 실행하지 않는다.
"""
import ast
import base64
import importlib.abc
import importlib.util
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
    exchange = "KR" if market == "국내" else st.selectbox("미국 거래소", ["NASDAQ", "NYSE", "AMEX"])
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input("종목코드 직접 입력", placeholder="005930 또는 AAPL").strip().upper()
    focus_only = st.toggle("선택 종목 1초 집중", True)
    refresh_candidates = st.button("🔄 후보 즉시 검색", use_container_width=True)
    st.caption("뉴스·FDA·SEC 전체 검색은 이 앱에서 자동 실행하지 않습니다.")

now = time.time()
candidate_key = f"scalp_candidates::{mode}"
candidate_time_key = f"scalp_candidates_at::{mode}"
candidate_due = (not focus_only) and now - float(st.session_state.get(candidate_time_key, 0)) >= 30
if refresh_candidates or candidate_key not in st.session_state or candidate_due:
    try:
        rows = scanner().candidates(mode)
        # Cheap first-stage ranking only. Detailed minute analysis waits until selection.
        rows = [dict(row) for row in rows[:20]]
        st.session_state[candidate_key] = rows
        st.session_state[candidate_time_key] = now
    except Exception as error:
        st.session_state["candidate_error"] = str(error)

candidates = list(st.session_state.get(candidate_key, []))
options = []
if manual_ticker:
    options.append({"ticker": manual_ticker, "name": manual_ticker, "exchange": exchange})
options.extend(candidates)
dedup = {}
for row in options:
    ticker = str(row.get("ticker", "")).upper()
    if ticker:
        dedup[ticker] = row
options = list(dedup.values())

if not options:
    st.warning("현재 후보가 없습니다. 후보 즉시 검색을 눌러주세요.")
    st.stop()

selected_ticker = st.selectbox(
    "집중 분석할 종목",
    [str(row.get("ticker", "")) for row in options],
    format_func=lambda ticker: next(
        (f"{ticker} · {row.get('name', ticker)} · {float(row.get('screen_change', 0) or 0):+.2f}%"
         for row in options if str(row.get("ticker", "")) == ticker), ticker),
)
selected_row = next(row for row in options if str(row.get("ticker", "")) == selected_ticker)
selected_row.setdefault("exchange", exchange)

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

f10 = float(latest.get("forecast_10m", 0) or 0)
f30 = float(latest.get("forecast_30m", 0) or 0)
f20 = (f10 + f30) / 2
forecast_cols = st.columns(3)
for column, minutes, forecast in zip(forecast_cols, (10, 20, 30), (f10, f20, f30)):
    expected = price * (1 + forecast / 100)
    uncertainty = max(0.20, abs(forecast) * 0.50)
    low = price * (1 + (forecast - uncertainty) / 100)
    high = price * (1 + (forecast + uncertainty) / 100)
    column.metric(
        f"{minutes}분 예상 · {forecast_label(forecast)}",
        fmt(expected),
        f"{forecast:+.2f}%",
    )
    column.caption(f"예상 범위 {fmt(low)}~{fmt(high)}")

target_probability = int(latest.get("target1_probability", 0) or 0)
if level == "success" and f10 > 0 and f20 > 0 and f30 > 0:
    st.success(
        f"🚦 진입 조건 통과 · 10·20·30분 방향 일치 · "
        f"1차 목표 도달 추정 {target_probability}%"
    )
elif f10 < 0 or f20 < 0:
    st.error("⛔ 단기 예상이 약세입니다. 신규 진입하지 마세요.")
else:
    st.warning("⏳ 시간대별 방향이 일치하지 않습니다. 진입을 기다리세요.")

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

st.subheader("30분 보유 추적")
entry = st.number_input("실제 매수가", min_value=0.0, value=price, format="%.4f" if price < 100 else "%.0f")
left, right = st.columns(2)
if left.button("▶️ 추적 시작", type="primary", use_container_width=True):
    st.session_state["scalp_position"] = {
        "ticker": selected_ticker, "entry": float(entry), "start": now,
        "snapshots": [], "last_snapshot": 0.0,
    }
if right.button("⏹️ 추적 종료", use_container_width=True):
    st.session_state.pop("scalp_position", None)

position = st.session_state.get("scalp_position")
if position and position.get("ticker") == selected_ticker:
    elapsed = (now - float(position["start"])) / 60
    pnl = (price / float(position["entry"]) - 1) * 100 if float(position["entry"]) > 0 else 0
    if not position["snapshots"] or now - float(position.get("last_snapshot", 0)) >= 600:
        position["snapshots"].append({
            "경과(분)": round(elapsed, 1), "시각": datetime.now(KST).strftime("%H:%M:%S"),
            "가격": price, "수익률(%)": round(pnl, 3), "판정": label,
        })
        position["last_snapshot"] = now
        st.session_state["scalp_position"] = position
    track = st.columns(4)
    track[0].metric("경과", f"{elapsed:.1f}분")
    track[1].metric("실시간 수익률", f"{pnl:+.2f}%")
    track[2].metric("+1% 목표", fmt(float(position["entry"]) * 1.01))
    track[3].metric("손절", fmt(latest.get("stop_loss")))
    if pnl >= 1:
        st.success("✅ +1% 목표 도달 · 익절 또는 분할매도 검토")
    elif elapsed >= 30:
        st.error("⏰ 30분 종료 · 시간손절/청산 검토")
    elif level == "error":
        st.error("🔴 보유 신호 훼손 · 매도·손절 검토")
    else:
        st.info("보유 추적 중 · 다음 10분 구간을 계속 기록합니다.")
    st.dataframe(pd.DataFrame(position["snapshots"]), hide_index=True, use_container_width=True)

st.caption(f"마지막 정밀분석: {latest.get('updated_at', '-')} · 화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
