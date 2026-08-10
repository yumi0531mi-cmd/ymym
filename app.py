
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

try:
    from scanner.prediction_memory import (
        record_prediction,
        score_due_predictions,
        recent_summary,
    )
except Exception:
    def record_prediction(*args, **kwargs):
        return None

    def score_due_predictions():
        return 0

    def recent_summary():
        return {
            "total": 0,
            "hit_5m": 0.0,
            "n5": 0,
            "hit_10m": 0.0,
            "n10": 0,
            "hit_30m": 0.0,
            "n30": 0,
        }
from streamlit_autorefresh import st_autorefresh

from scanner.board import StableBoard
from scanner.kis_engine import KISUnifiedScanner


BOARD_CACHE_DIR = Path(".scanner_cache")
BOARD_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def board_cache_path(mode: str) -> Path:
    safe_mode = (
        mode.replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )
    return BOARD_CACHE_DIR / f"board_{safe_mode}.json"


def load_persistent_board(mode: str) -> list[dict]:
    path = board_cache_path(mode)

    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        board = payload.get("board", [])

        if not isinstance(board, list):
            return []

        return [
            item
            for item in board
            if isinstance(item, dict) and item.get("ticker")
        ]
    except Exception:
        return []


def save_persistent_board(mode: str, board: list[dict]) -> None:
    path = board_cache_path(mode)
    temporary = path.with_suffix(".tmp")

    payload = {
        "saved_at": datetime.now().isoformat(),
        "mode": mode,
        "board": board,
    }

    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


st.set_page_config(
    page_title="AI 단타 스캐너 V9",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.35rem;
        padding-bottom: 1.5rem;
        max-width: 1500px;
    }
    h1 {font-size: 2rem !important; margin-bottom: .15rem !important;}
    h2 {font-size: 1.35rem !important;}
    h3 {font-size: 1.02rem !important; margin-bottom: .05rem !important;}
    [data-testid="stMetricLabel"] {font-size: .73rem !important;}
    [data-testid="stMetricValue"] {font-size: 1.52rem !important;}
    [data-testid="stMetricDelta"] {font-size: .78rem !important;}
    [data-testid="stVerticalBlockBorderWrapper"] {
        padding: .72rem .78rem !important;
    }
    [data-testid="stSidebar"] * {
        font-size: .84rem;
    }
    .stCaption {font-size: .72rem !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

MODES = [
    "국내 우량주",
    "국내 거래대금 급증",
    "국내 돌파",
    "국내 눌림목",
    "미국 우량주",
    "미국 급등주",
    "미국 소형주 급등",
    "미국 프리마켓",
    "미국 런업",
]


@st.cache_resource
def engine() -> KISUnifiedScanner:
    return KISUnifiedScanner()


def fmt_price(value) -> str:
    try:
        value = float(value)
        if value >= 1000:
            return f"{value:,.0f}"
        if value >= 10:
            return f"{value:,.2f}"
        if value >= 1:
            return f"{value:,.3f}"
        return f"{value:,.4f}"
    except Exception:
        return "-"


def render_card(item: dict, rank: int) -> None:
    with st.container(border=True):
        top1, top2, top3 = st.columns([4, 2, 2])

        top1.markdown(f"### {rank}위 · {item.get('name', item['ticker'])}")
        top1.caption(
            f"{item['ticker']} · {item.get('exchange', item.get('market', ''))} · "
            f"{item.get('board_status', '유지')} · "
            f"{item.get('price_source', '한국투자증권 API')}"
        )

        if item.get("market") == "US" and item.get("gainer_stage"):
            top1.caption(
                f"🔥 {item.get('gainer_stage')} · "
                f"당일 {float(item.get('change_percent', 0)):+.2f}% · "
                f"거래량 {int(item.get('volume', 0)):,}주"
            )

        top2.metric(
            "현재가",
            fmt_price(item.get("price")),
            f"{float(item.get('change_percent', 0)):+.2f}%",
        )
        top3.metric("AI 조건점수", f"{int(item.get('score', 0))}점")

        decision = str(item.get("decision", "관찰"))
        if "진입" in decision and "금지" not in decision:
            st.success(decision)
        elif "대기" in decision or "눌림" in decision:
            st.warning(decision)
        elif "금지" in decision or "제외" in decision:
            st.error(decision)
        else:
            st.info(decision)

        forecast = st.columns(3)
        forecast[0].metric("5분 전망", f"{float(item.get('forecast_5m', 0)):+.2f}%")
        forecast[1].metric("10분 전망", f"{float(item.get('forecast_10m', 0)):+.2f}%")
        forecast[2].metric("30분 전망", f"{float(item.get('forecast_30m', 0)):+.2f}%")

        entry = st.columns(5)
        entry[0].metric("1차 눌림", fmt_price(item.get("pullback_entry")))
        entry[1].metric(
            "2차 눌림",
            fmt_price(
                item.get(
                    "deep_pullback_entry",
                    item.get("pullback_entry"),
                )
            ),
        )
        entry[2].metric("돌파 진입", fmt_price(item.get("breakout_entry")))
        entry[3].metric("손절", fmt_price(item.get("stop_loss")))
        entry[4].metric("손익비", f"{float(item.get('risk_reward', 0)):.2f}")

        targets = st.columns(3)
        if item.get("show_upside_targets", True):
            for index, column in enumerate(targets, start=1):
                column.metric(
                    f"{index}차 목표",
                    fmt_price(item.get(f"target{index}")),
                )
                status = item.get(
                    f"target{index}_status",
                    "진행중",
                )

                if status == "도달":
                    column.caption(
                        f"목표 도달 · 확률 "
                        f"{item.get(f'target{index}_probability', 0)}%"
                    )
                elif status == "시간초과":
                    column.caption(
                        "ETA 시간초과 · 재평가 필요"
                    )
                else:
                    column.caption(
                        f"남은 ETA "
                        f"{item.get(f'eta{index}_low', 0)}~"
                        f"{item.get(f'eta{index}_high', 0)}분 · "
                        f"확률 "
                        f"{item.get(f'target{index}_probability', 0)}%"
                    )
        else:
            targets[0].metric("예상 하단", fmt_price(item.get("expected_low")))
            targets[1].metric("눌림 관찰가", fmt_price(item.get("pullback_entry")))
            targets[2].metric("상승 목표", "없음")

        micro = st.columns(4)
        rvol_value = float(item.get("rvol", 0))
        rvol_label = (
            "💥 폭발"
            if rvol_value >= 20
            else "🚀 매우 강함"
            if rvol_value >= 10
            else "🔥 강함"
            if rvol_value >= 5
            else "🟢 증가"
            if rvol_value >= 2
            else "보통"
        )
        micro[0].metric(
            "RVOL",
            f"{rvol_value:.1f}배",
            rvol_label,
        )
        micro[1].metric(
            "공격매수",
            f"{float(item.get('aggressive_buy_pct', 0)):.1f}%",
            item.get("flow_signal", "미확인"),
        )
        micro[2].metric(
            "공격매도",
            f"{float(item.get('aggressive_sell_pct', 0)):.1f}%",
            f"Delta {float(item.get('delta_pct', 0)):+.1f}%",
        )
        micro[3].metric(
            "호가우위",
            (
                f"{float(item.get('bid_ask_ratio', 0)):.2f}배"
                if float(item.get("bid_ask_ratio", 0)) > 0
                else "미확인"
            ),
            item.get("book_signal", "미확인"),
        )

        flow_detail = st.columns(4)
        flow_detail[0].metric(
            "RSI",
            f"{float(item.get('rsi', 0)):.1f}",
        )
        flow_detail[1].metric(
            "체결강도",
            (
                f"{float(item.get('trade_strength', 0)):.1f}"
                if float(item.get("trade_strength", 0)) > 0
                else "미수신"
            ),
        )
        flow_detail[2].metric(
            "지속가능성",
            f"{int(item.get('continuation_score', 0))}점",
            item.get("fomo_stage", "계산중"),
        )
        flow_detail[3].metric(
            "추격위험",
            f"{int(item.get('chase_risk', 0))}점",
            item.get("whale_signal", "미수신"),
        )

        st.caption(
            f"체결 판정 기준: {item.get('flow_source', '미확인')} · "
            f"매수호가 {float(item.get('bid_total', 0)):,.0f}주 · "
            f"매도호가 {float(item.get('ask_total', 0)):,.0f}주"
        )

        if item.get("ws_connected"):
            st.caption("🟢 미국 실시간 WebSocket 수신 중")
        elif item.get("market") == "US":
            if "후보탐색 임시가" in str(item.get("price_source", "")):
                st.caption(
                    "🟠 KIS 분봉 대기 · 외부 급등률/거래량으로 "
                    "5·10·30분 전망과 임시 타점 계산"
                )
            else:
                st.caption("🟡 WebSocket 연결 대기 · REST 현재가 임시 사용")

        st.caption(item.get("reason", ""))

        st.caption(
            "목표가 근거: "
            f"{item.get('target_basis', '분봉 저항 · ATR · 변동성')} · "
            f"지지 {fmt_price(item.get('support_level'))} · "
            f"저항 {fmt_price(item.get('resistance_level'))}"
        )

        if item.get("levels_validated"):
            st.caption(
                "✓ 가격 순서 검증 완료: 손절 < 2차 눌림 < "
                "1차 눌림 < 현재가 < 돌파 < 목표가"
            )

        reliability = int(item.get("model_reliability", 0))
        st.caption(
            f"모델 신뢰도 {reliability}% "
            f"({item.get('reliability_grade', '낮음')}) · "
            f"근거: {item.get('reliability_evidence', '데이터 부족')}"
        )

        elapsed = float(item.get("forecast_elapsed_minutes", 0))
        st.caption(
            f"전망 생성 후 {elapsed:.1f}분 경과 · "
            f"{item.get('eta_state', 'ETA 계산 중')}"
        )

        sample5 = int(item.get("pattern_sample_5m", 0) or 0)
        if sample5 > 0:
            st.caption(
                f"같은 패턴 최근 {sample5}회 · "
                f"5분 적중 {float(item.get('pattern_hit_5m', 0)):.1f}% · "
                f"10분 적중 {float(item.get('pattern_hit_10m', 0)):.1f}% · "
                f"1차 목표 도달 {float(item.get('pattern_target1_hit', 0)):.1f}% · "
                f"ETA 평균오차 {float(item.get('pattern_eta1_mae', 0)):.1f}분 · "
                f"등급 {item.get('decision_grade', '신규')}"
            )

        warnings = item.get("warnings", [])
        if warnings:
            st.warning(" · ".join(warnings))


with st.sidebar:
    st.title("📡 V9 설정")

    mode = st.selectbox("스캐너 종류", MODES)
    minimum = st.slider("최소 점수", 30, 90, 40, 5)
    limit = st.selectbox("화면 후보 수", [5, 7, 10], index=2)

    light_refresh = st.selectbox(
        "화면·현재가 새로고침",
        [3, 5, 10],
        index=1,
        format_func=lambda value: f"{value}초",
    )
    deep_refresh = st.selectbox(
        "전체 정밀 재검색",
        [30, 60, 120, 300],
        index=1,
        format_func=lambda value: f"{value}초",
    )

    auto = st.toggle("자동 새로고침", False)
    display_filter = st.selectbox(
        "표시할 종목",
        ["전체 후보", "진입 가능·눌림만", "진입 가능만"],
    )
    pinned_text = st.text_input(
        "📌 고정 티커",
        placeholder="예: 005930, AAPL",
    )
    run = st.button("🔄 지금 스캔", type="primary", use_container_width=True)

    if mode == "미국 소형주 급등":
        st.caption(
            "미국 급등주를 여러 소스에서 먼저 포착한 뒤 KIS 현재가·분봉으로 분석합니다. "
            "$0.05~$10 · +5% 이상 또는 RVOL 2배 이상 · 거래량 2만주 이상."
        )
    elif mode == "미국 런업":
        st.caption(
            "런업은 가격 제한보다 FDA·임상·계약·실적 등 이벤트를 우선합니다."
        )
    else:
        st.caption(
            "가격 범위는 선택한 스캐너 모드별 기본 조건을 사용합니다."
        )

    st.caption(
        "가격·분봉·차트·지표는 한국투자증권 API를 사용합니다. "
        "Yahoo는 미국 후보 탐색과 뉴스에만 사용합니다."
    )


if auto:
    st_autorefresh(
        interval=light_refresh * 1000,
        key=f"v9_light_refresh::{mode}",
    )


market = "US" if mode.startswith("미국") else "KR"

st.title("📡 AI 단타 스캐너 V9 · 한투 통합")
st.caption(
    "한국투자증권 REST + WebSocket 통합 · 미국 소형주 $0.20~$10 · "
    "5/10/30분 전망 · 매수/매도 체결우위 · 실시간 호가 · 목표가별 ETA"
)
st.info(
    f"시장 {market} · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
    "매매 가격 계산 데이터: 한국투자증권 API"
)


board_key = f"v9_board::{mode}"
if board_key not in st.session_state:
    st.session_state[board_key] = load_persistent_board(mode)

if "v9_last_deep" not in st.session_state:
    st.session_state.v9_last_deep = {}

if "v9_errors" not in st.session_state:
    st.session_state.v9_errors = []


last_deep = float(st.session_state.v9_last_deep.get(mode, 0.0))
deep_due = time.time() - last_deep >= deep_refresh

should_scan = (
    run
    or not st.session_state[board_key]
    or (auto and deep_due)
)

if auto and st.session_state[board_key] and not should_scan:
    st.session_state[board_key] = engine().refresh_quotes(
        st.session_state[board_key],
        mode,
    )
    save_persistent_board(
        mode,
        st.session_state[board_key],
    )

if should_scan:
    with st.spinner("한투 시세와 분봉으로 후보를 정밀 분석 중입니다..."):
        try:
            incoming = engine().scan(
                mode=mode,
                minimum_score=minimum,
                limit=max(limit * 2, 10),
            )

            pinned = {
                token.strip().upper()
                for token in pinned_text.split(",")
                if token.strip()
            }

            previous_board = list(
                st.session_state[board_key]
            )

            pool_limit = 50

            if incoming:
                board = StableBoard(
                    minimum_size=5,
                    maximum_size=pool_limit,
                    minimum_hold_minutes=60,
                ).update(
                    previous=previous_board,
                    incoming=incoming,
                    pinned_tickers=pinned,
                )
            else:
                board = previous_board

                if previous_board:
                    st.session_state.v9_errors = list(
                        engine().errors
                    ) + [
                        "이번 재검색 결과가 비어 기존 후보 풀을 유지했습니다."
                    ]

            st.session_state[board_key] = board
            save_persistent_board(mode, board)
            st.session_state.v9_last_deep[mode] = time.time()

            if incoming:
                st.session_state.v9_errors = list(engine().errors)
        except Exception as error:
            st.session_state.v9_errors = [
                f"{type(error).__name__}: {error}"
            ]


board = [
    engine().sanitize_cached_item(item)
    for item in st.session_state[board_key]
]

st.session_state[board_key] = board

if board:
    save_persistent_board(mode, board)

    for prediction_item in board:
        try:
            record_prediction(mode, prediction_item)
        except Exception:
            pass

    try:
        score_due_predictions()
    except Exception:
        pass

display_board = board[:limit]

if display_filter == "진입 가능·눌림만":
    visible = [
        item for item in display_board
        if (
            "진입" in str(item.get("decision", ""))
            or "눌림" in str(item.get("decision", ""))
            or "대기" in str(item.get("decision", ""))
        )
    ]
elif display_filter == "진입 가능만":
    visible = [
        item for item in display_board
        if (
            "진입" in str(item.get("decision", ""))
            and "금지" not in str(item.get("decision", ""))
        )
    ]
else:
    visible = display_board


scan_tab, diagnostic_tab = st.tabs(["추천 보드", "진단"])

with scan_tab:
    if not board:
        st.warning("현재 표시할 후보가 없습니다.")

    if board and not visible:
        st.info("선택한 필터에 맞는 종목은 없지만 후보 보드는 유지 중입니다.")

    for rank, item in enumerate(visible, start=1):
        render_card(item, rank)

with diagnostic_tab:
    learning = recent_summary()
    st.subheader("예측 자동채점 현황")
    learn_cols = st.columns(4)
    learn_cols[0].metric("누적 예측", learning.get("total", 0))
    learn_cols[1].metric(
        "5분 적중",
        f"{learning.get('hit_5m', 0):.1f}%",
        f"표본 {learning.get('n5', 0)}",
    )
    learn_cols[2].metric(
        "10분 적중",
        f"{learning.get('hit_10m', 0):.1f}%",
        f"표본 {learning.get('n10', 0)}",
    )
    learn_cols[3].metric(
        "30분 적중",
        f"{learning.get('hit_30m', 0):.1f}%",
        f"표본 {learning.get('n30', 0)}",
    )

    st.write(
        {
            "모드": mode,
            "시장": market,
            "보드 후보 수": len(board),
            "화면 새로고침": f"{light_refresh}초",
            "정밀 재검색": f"{deep_refresh}초",
            "가격·차트 데이터": "한국투자증권 REST",
            "실시간 체결·호가": "한국투자증권 WebSocket",
            "후보 유지": "모드별 최대 50개 누적·디스크 캐시 복원",
            "후보 보호시간": "최소 60분",
            "미국 후보 탐색": "Yahoo 상승·거래량 스크리너",
        }
    )

    if st.session_state.v9_errors:
        for error in st.session_state.v9_errors:
            st.code(error)
    else:
        st.success("현재 기록된 오류가 없습니다.")
