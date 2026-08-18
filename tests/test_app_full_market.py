from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from scanner.kis_client import KISClient, KISError
from scanner.models import ForecastPoint, Market, Quote, Regime, Signal, TradePlan
from scanner.realtime import RealtimeTick


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
MOCK_RANKINGS = {
    "거래대금·거래량 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "100000"},
    ],
    "상승률 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "prdy_ctrt": "2.0"},
    ],
}


@pytest.fixture(autouse=True)
def regular_market_session():
    with patch("scanner.sessions.market_session", return_value="KR_REGULAR"):
        yield


def test_quote_survives_orderbook_endpoint_failure():
    from app import load_rest_dashboard_quote
    quote = _quote("005930", Market.KR)
    with (
        patch("app._load_quote_record", return_value={
            "symbol": quote.symbol, "market": quote.market.value, "price": quote.price,
            "previous_close": quote.previous_close, "timestamp": quote.timestamp.isoformat(),
            "bid": None, "ask": None, "volume": quote.volume, "turnover": quote.turnover,
            "session": quote.session, "source": quote.source,
        }),
        patch("app._load_orderbook", side_effect=KISError("orderbook unavailable")),
    ):
        result = load_rest_dashboard_quote("005930", "KR", "")
    assert result.price == 70000
    assert result.bid is None and result.ask is None


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
            ForecastPoint(10, 70200, 71500, 72200, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
            ForecastPoint(15, 70500, 72000, 73000, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
            ForecastPoint(30, 71000, 73500, 75000, Regime.UP, "EMA9·VWAP 위 · 거래량 강화"),
        ],
        persistence_score=78, diagnostics={
            "rvol": 2.4, "spread_pct": 0.14, "reward_risk_net": 1.8,
            "false_signal_flags": [], "forecast_path_ready": True,
            "long_price_path_confirmed": True, "has_downward_forecast": False,
        },
    )


def test_actionable_levels_hide_card_prices_without_completed_chart_stop_structure():
    from app import actionable_display_levels

    plan = _plan()
    plan.entry = 69000
    plan.target = 69500
    plan.target2 = 69900
    plan.stop = None
    plan.soft_stop = None
    plan.hard_stop = None
    plan.diagnostics.update({"long_price_path_confirmed": True, "atr": 500})

    levels = actionable_display_levels(plan, _quote())

    assert levels["available"] is False
    assert "완료 차트" in str(levels["basis"])


def test_default_market_uses_active_us_session_after_korean_close():
    from app import default_market_label

    with patch("app.market_session", side_effect=lambda market: "US_PRE" if market == Market.US else "KR_CLOSED"):
        assert default_market_label() == "미국"

    with patch("app.market_session", return_value="KR_REGULAR"):
        assert default_market_label() == "국내"


def test_live_validation_records_only_actionable_buy_signal():
    from app import record_and_score_live_validation

    store = SimpleNamespace(score_ready=Mock(return_value=0), save_once=Mock(return_value=(Path("case.json"), True)))
    tick = RealtimeTick("005930", Market.KR, 70000, 1.2, datetime(2026, 8, 17, 10, 0))
    hub = SimpleNamespace(tick=Mock(return_value=tick))
    wait_plan = _plan()
    wait_plan.signal = Signal.WAIT

    with patch("app.current_realtime_hub", return_value=hub):
        scored, recorded = record_and_score_live_validation(store, wait_plan, _quote(), _bars(), True, 0.05)

    assert scored == 0
    assert recorded is False
    store.save_once.assert_not_called()

    buy_plan = _plan()
    with patch("app.current_realtime_hub", return_value=hub):
        _, recorded = record_and_score_live_validation(store, buy_plan, _quote(), _bars(), True, 0.05)

    assert recorded is True
    store.save_once.assert_called_once()


def test_forecast_audit_records_complete_watch_path_separately():
    from app import record_forecast_accuracy_audit

    store = SimpleNamespace(
        score_ready=Mock(return_value=0),
        has_pending_forecast_audit=Mock(return_value=False),
        save_once=Mock(return_value=(Path("case.json"), True)),
    )
    tick = RealtimeTick("005930", Market.KR, 70000, 1.2, datetime(2026, 8, 17, 10, 0))
    hub = SimpleNamespace(tick=Mock(return_value=tick))
    watch_plan = _plan()
    watch_plan.signal = Signal.WAIT

    with patch("app.current_realtime_hub", return_value=hub):
        scored, recorded = record_forecast_accuracy_audit(store, watch_plan, _quote(), _bars(), True, 0.05)

    assert scored == 0
    assert recorded is True
    saved_case = store.save_once.call_args.args[0]
    assert saved_case.signal == Signal.WAIT.value
    assert saved_case.validation_kind == "FORECAST_AUDIT"
    assert saved_case.forecast_path_direction == Regime.UP.value
    assert saved_case.price_source == "KIS 체결"


def test_forecast_audit_uses_labeled_kis_rest_snapshot_while_trade_tick_reconnects():
    from app import record_forecast_accuracy_audit

    store = SimpleNamespace(
        score_ready=Mock(return_value=0),
        capture_rest_snapshot_and_score=Mock(return_value=0),
        has_pending_forecast_audit=Mock(return_value=False),
        save_once=Mock(return_value=(Path("case.json"), True)),
    )
    hub = SimpleNamespace(tick=Mock(return_value=None))
    watch_plan = _plan()
    watch_plan.signal = Signal.WAIT

    with patch("app.current_realtime_hub", return_value=hub):
        _, recorded = record_forecast_accuracy_audit(store, watch_plan, _quote(), _bars(), True, 0.05)

    assert recorded is True
    saved_case = store.save_once.call_args.args[0]
    assert saved_case.latest_trade_price == 70000
    assert saved_case.price_source == "KIS REST"


def test_forecast_audit_starts_only_one_pending_path_per_market(tmp_path):
    from app import record_forecast_accuracy_audit
    from scanner.validation import ValidationStore

    store = ValidationStore(tmp_path)
    tick = RealtimeTick("005930", Market.KR, 70000, 1.2, datetime(2026, 8, 17, 10, 0))
    hub = SimpleNamespace(tick=Mock(return_value=tick))
    watch_plan = _plan()
    watch_plan.signal = Signal.WAIT

    with patch("app.current_realtime_hub", return_value=hub):
        _, first_recorded = record_forecast_accuracy_audit(store, watch_plan, _quote(), _bars(), True, 0.05)
        _, second_recorded = record_forecast_accuracy_audit(store, watch_plan, _quote(), _bars(), True, 0.05)

    assert first_recorded is True
    assert second_recorded is False
    assert len(store.pending_forecast_audits("KR", version="6.3-structural-cap")) == 1


def test_actionable_levels_do_not_create_buy_levels_for_unconfirmed_path():
    from app import actionable_display_levels

    plan = _plan()
    plan.diagnostics["long_price_path_confirmed"] = False

    levels = actionable_display_levels(plan, _quote())

    assert levels["available"] is False


def test_visible_trade_cards_keep_up_to_five_ready_candidates():
    from app import visible_trade_cards

    items = []
    for index in range(6):
        plan = deepcopy(_plan())
        quote = _quote()
        quote.symbol = f"T{index}"
        plan.symbol = quote.symbol
        items.append({"quote": quote, "plan": plan, "chart_aligned": True})

    visible = visible_trade_cards(items)

    assert len(visible) == 5
    assert {item["quote"].symbol for item in visible}.issubset({f"T{index}" for index in range(6)})


def test_card_trade_status_separates_buy_wait_and_downward_candidates():
    from app import card_ready_for_display, card_trade_status, visible_trade_cards

    plan = _plan()
    plan.diagnostics.update({
        "forecast_path_ready": True,
        "long_price_path_confirmed": True,
        "has_downward_forecast": False,
    })
    item = {"quote": _quote(), "plan": plan, "chart_aligned": True}
    assert card_trade_status(item) == "매수 조건 충족"
    assert card_ready_for_display(item) is True

    plan.signal = Signal.WAIT
    assert card_trade_status(item) == "눌림목 대기"
    assert card_ready_for_display(item) is True

    plan.diagnostics["has_downward_forecast"] = True
    assert card_trade_status(item) == "하방 제외"
    assert visible_trade_cards([item], 10) == []

    plan.diagnostics["has_downward_forecast"] = False
    plan.diagnostics["forecast_path_ready"] = False
    assert card_ready_for_display(item) is False


def test_missing_kis_credentials_shows_safe_waiting_screen_without_buttons():
    st.cache_resource.clear()
    st.cache_data.clear()
    with patch.dict(os.environ, {"KIS_APP_KEY": "", "KIS_APP_SECRET": "", "KIS_ACCESS_TOKEN": ""}, clear=False):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)

    assert not app.exception
    assert not app.button
    assert any("한국투자증권 연결을 기다리고 있습니다" in str(item.value) for item in app.markdown)


def test_closed_korean_session_hides_stale_candidate_cards():
    st.cache_resource.clear()
    st.cache_data.clear()
    with patch("scanner.sessions.market_session", return_value="KR_CLOSED"):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)

    assert not app.exception
    assert any("국내 정규장이 종료되었습니다" in str(item.value) for item in app.info)
    assert not any("추천 매수가" == metric.label for metric in app.metric)


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
    assert any("005930" in str(item.value) for item in app.markdown)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}
    assert {metric.label for metric in app.metric} >= {"5분 예상", "10분 예상", "15분 예상", "30분 예상", "현재 차트 지지"}
    assert any("정밀 분석 1개" in str(item.value) for item in app.caption)


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
    assert any("005930" in str(item.value) for item in app.markdown)
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
    assert any("005380" in str(item.value) and "현대차" in str(item.value) for item in app.markdown)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}
