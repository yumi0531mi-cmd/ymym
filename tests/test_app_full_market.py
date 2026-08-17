from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from scanner.kis_client import KISClient, KISError
from scanner.models import ForecastPoint, Market, Quote, Regime, Signal, TradePlan


APP_PATH = "/home/ubuntu/ymym_review/app.py"
MOCK_RANKINGS = {
    "거래대금·거래량 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "100000"},
    ],
    "상승률 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "prdy_ctrt": "2.0"},
    ],
}


def _quote(*_args, **_kwargs) -> Quote:
    return Quote(
        symbol="005930", market=Market.KR, price=70000, previous_close=69000,
        timestamp=datetime(2026, 8, 17, 10, 0), volume=1_200_000,
        turnover=84_000_000_000, session="KR_REGULAR",
    )


def _bars(*_args, **_kwargs) -> pd.DataFrame:
    index = pd.date_range("2026-08-17 09:00", periods=40, freq="min")
    return pd.DataFrame(
        {"open": [69000] * 40, "high": [70100] * 40, "low": [68900] * 40, "close": [70000] * 40, "volume": [1000] * 40},
        index=index,
    )


def _plan(*_args, **_kwargs) -> TradePlan:
    return TradePlan(
        symbol="005930", market=Market.KR, created_at=datetime(2026, 8, 17, 10, 0),
        signal=Signal.BUY, strategy="TREND_SWING", regime=Regime.UP, current_price=70000,
        entry=69900, target=71000, target2=72000, stop=69500, soft_stop=69600,
        hard_stop=69400, target_basis="완료 5분봉 스윙 저항", target2_basis="다음 완료 5분봉 저항",
        stop_basis="1분봉 구조 무효화", entry_basis="완료봉 EMA9 지지", score=87, reasons=["완료봉 확인"], risk_state="NORMAL",
        forecasts=[
            ForecastPoint(5, 70000, 71000, 71500, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
            ForecastPoint(15, 70500, 72000, 73000, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
            ForecastPoint(30, 71000, 73500, 75000, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
        ],
        persistence_score=78, diagnostics={"rvol": 2.4, "spread_pct": 0.14, "reward_risk_net": 1.8, "false_signal_flags": []},
    )


def test_missing_kis_credentials_shows_safe_waiting_screen_without_buttons():
    st.cache_resource.clear()
    st.cache_data.clear()
    app = AppTest.from_file(APP_PATH)
    app.run(timeout=30)

    assert not app.exception
    assert not app.button
    assert any("한국투자증권 연결을 기다리고 있습니다" in str(item.value) for item in app.markdown)


def _quote_for_symbol(symbol, market, *_args, **_kwargs) -> Quote:
    return Quote(
        symbol=str(symbol), market=market, price=70000, previous_close=69000,
        timestamp=datetime(2026, 8, 17, 10, 0), volume=1_200_000,
        turnover=84_000_000_000, session="KR_REGULAR",
    )


def test_connected_app_automatically_renders_live_candidate_card_without_user_selection():
    original_init = KISClient.__init__

    def init_with_mock_token(self, secrets=None, cache_dir=".scanner_cache"):
        original_init(
            self,
            {"KIS_APP_KEY": "test-key", "KIS_APP_SECRET": "test-secret", "KIS_ACCESS_TOKEN": "test-token"},
            cache_dir=cache_dir,
        )

    st.cache_resource.clear()
    st.cache_data.clear()
    with (
        patch.object(KISClient, "__init__", init_with_mock_token),
        patch.object(KISClient, "market_rankings", return_value=MOCK_RANKINGS),
        patch.object(KISClient, "quote", side_effect=_quote),
        patch.object(KISClient, "orderbook", return_value=(69900, 70000)),
        patch.object(KISClient, "intraday", side_effect=_bars),
        patch("scanner.engine.analyze", side_effect=_plan),
        patch("scanner.calibration.calibration_for", return_value=SimpleNamespace(probability_pct=None, samples=0)),
    ):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)

    assert not app.exception
    assert not app.button
    assert any("실시간 상승·반복단타 혼합 스캐너" in str(item.value) for item in app.markdown)
    assert any("005930" in str(item.value) for item in app.subheader)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}
    assert {metric.label for metric in app.metric} >= {"5분 예상", "10분 예상", "15분 예상", "30분 예상", "현재 차트 지지"}
    assert any("상세 카드 1개" in str(item.value) for item in app.caption)


def test_ranking_error_falls_back_to_liquid_candidates_and_still_renders_cards():
    original_init = KISClient.__init__

    def init_with_mock_token(self, secrets=None, cache_dir=".scanner_cache"):
        original_init(
            self,
            {"KIS_APP_KEY": "test-key", "KIS_APP_SECRET": "test-secret", "KIS_ACCESS_TOKEN": "test-token"},
            cache_dir=cache_dir,
        )

    st.cache_resource.clear()
    st.cache_data.clear()
    with (
        patch.object(KISClient, "__init__", init_with_mock_token),
        patch.object(KISClient, "market_rankings", side_effect=KISError("rank unavailable")),
        patch.object(KISClient, "quote", side_effect=_quote_for_symbol),
        patch.object(KISClient, "orderbook", return_value=(69900, 70000)),
        patch.object(KISClient, "intraday", side_effect=_bars),
        patch("scanner.engine.analyze", side_effect=_plan),
        patch("scanner.calibration.calibration_for", return_value=SimpleNamespace(probability_pct=None, samples=0)),
    ):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)

    assert not app.exception
    assert any("005930" in str(item.value) for item in app.subheader)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}


def test_korean_name_direct_search_adds_requested_stock_card():
    original_init = KISClient.__init__

    def init_with_mock_token(self, secrets=None, cache_dir=".scanner_cache"):
        original_init(
            self,
            {"KIS_APP_KEY": "test-key", "KIS_APP_SECRET": "test-secret", "KIS_ACCESS_TOKEN": "test-token"},
            cache_dir=cache_dir,
        )

    st.cache_resource.clear()
    st.cache_data.clear()
    with (
        patch.object(KISClient, "__init__", init_with_mock_token),
        patch.object(KISClient, "market_rankings", return_value=MOCK_RANKINGS),
        patch.object(KISClient, "quote", side_effect=_quote_for_symbol),
        patch.object(KISClient, "orderbook", return_value=(69900, 70000)),
        patch.object(KISClient, "intraday", side_effect=_bars),
        patch("scanner.engine.analyze", side_effect=_plan),
        patch("scanner.calibration.calibration_for", return_value=SimpleNamespace(probability_pct=None, samples=0)),
    ):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)
        next(widget for widget in app.text_input if widget.label == "관심 종목 바로 보기").set_value("현대차")
        app.run(timeout=30)

    assert not app.exception
    assert any("005380" in str(item.value) and "현대차" in str(item.value) for item in app.subheader)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}
