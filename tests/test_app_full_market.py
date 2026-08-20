from __future__ import annotations

from contextlib import nullcontext
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
    "거래량 TOP100": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "100000"},
    ],
    "거래대금 TOP100": [
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


def test_dashboard_card_skips_forecast_audit_when_background_batch_owns_fixed_samples():
    from app import analyze_card

    plan = _plan()
    calibration = SimpleNamespace(
        samples=0, probability_pct=None, recent_samples=0,
        recent_probability_pct=None, recent_average_net_return_pct=None,
        to_dict=lambda: {"samples": 0, "probability_pct": None},
    )
    cycle = SimpleNamespace(cooldown_active=False, hard_kill=False)
    forecast_audit = Mock()

    with (
        patch("app.load_rest_dashboard_quote", return_value=_quote()),
        patch("app.quote_with_live_tick", return_value=_quote()),
        patch("app.load_bars", return_value=_bars()),
        patch("app.merge_live_completed_bars", return_value=_bars()),
        patch("app.completed_bar_alignment", return_value=(True, "")),
        patch("app.cycle_store.get", return_value=cycle),
        patch("app.analyze", return_value=plan) as analyze_engine,
        patch("app.calibration_for", return_value=calibration),
        patch("app.record_and_score_live_validation", return_value=(0, False)),
        patch("app.record_forecast_accuracy_audit", forecast_audit),
    ):
        result = analyze_card("005930", Market.KR, "", 0.05, 80, SimpleNamespace(), record_forecast_audit=False)

    assert analyze_engine.call_count == 1
    assert result["forecast_validation_recorded"] is False
    forecast_audit.assert_not_called()


def test_background_validation_rendering_follows_observation_cards_in_normal_scanner():
    source = APP_PATH.read_text(encoding="utf-8")

    observation_cards = source.index('if observation_cards:')
    validation_status = source.index('st.caption("백그라운드 검증 상태')
    assert observation_cards < validation_status


def test_live_card_snapshot_uses_one_page_refresh_when_realtime_tick_is_missing():
    from app import live_card_snapshot

    assert "load_recent_completed_bars" in live_card_snapshot.__doc__ or "load_recent_completed_bars" in APP_PATH.read_text(encoding="utf-8").split("def live_card_snapshot", 1)[1].split("def render_plan_fields", 1)[0]


def test_cold_candidate_analysis_renders_prepared_cards_before_all_eight_complete():
    source = APP_PATH.read_text(encoding="utf-8")

    candidate_loop = source.index("for index, candidate in enumerate(visible_requests, start=1):")
    incremental_preview = source.index("with analysis_preview.container():")
    final_preview_clear = source.index("analysis_preview.empty()")
    assert candidate_loop < incremental_preview < final_preview_clear


def test_observation_summary_rows_use_card_price_levels_and_forecast_ranges():
    from app import observation_rows

    plan = _plan()
    plan.signal = Signal.WAIT
    item = {
        "quote": _quote(), "plan": plan, "name": "삼성전자", "chart_aligned": True,
        "bars": _bars(), "fast_rank": 1,
    }

    rows = observation_rows([item], display_limit=5)

    assert len(rows) == 1
    assert rows[0]["현재가"] == "70,000"
    assert rows[0]["매수가 / 1·2차 목표"] == "69,900 / 71,000 / 72,000"
    assert rows[0]["Soft / Hard Stop"] == "69,600 / 69,400"
    assert rows[0]["5·15·30분"] == "5분 + · 15분 + · 30분 +"
    assert "5분 70,000~71,500" in rows[0]["예상 범위"]


def test_observation_price_summary_precedes_individual_observation_cards():
    source = APP_PATH.read_text(encoding="utf-8")

    section = source.index('if observation_cards:')
    summary = source.index('observation_summary = observation_rows(observation_cards, display_limit=5)', section)
    card_loop = source.index('for card_item in observation_cards:', section)
    background = source.index('st.caption("백그라운드 검증 상태', section)
    assert section < summary < card_loop < background


def test_observation_reason_rows_keep_data_and_strategy_gates_as_recorded():
    from app import observation_reason_rows

    rows = observation_reason_rows({"reasons": {"RR_FAIL": 2, "MINUTE_DATA_WAIT": 1, "PRICE_LEVEL_WAIT": 1}})

    assert rows == [
        {"주요 탈락 관문": "RR_FAIL", "종목 수": 2},
        {"주요 탈락 관문": "MINUTE_DATA_WAIT", "종목 수": 1},
        {"주요 탈락 관문": "PRICE_LEVEL_WAIT", "종목 수": 1},
    ]


def test_candidate_stage_summary_uses_card_diagnostic_gate_code_for_observations():
    from app import candidate_stage_summary

    plan = _plan()
    plan.signal = Signal.WAIT
    plan.diagnostics.update({"forecast_path_ready": False, "data_quality": {"completed_minute_bars": 599}})
    item = {"quote": _quote(), "plan": plan, "name": "삼성전자", "chart_aligned": True, "bars": _bars()}

    summary = candidate_stage_summary([item], candidate_pool=1)

    assert summary["reasons"] == {"MINUTE_DATA_WAIT · KIS 완료 1분봉·방향 경로 재수신 대기": 1}


def test_price_level_wait_exposes_raw_ordering_and_entry_location_without_recommending_invalid_levels():
    from app import observation_diagnostic, observation_rows

    plan = _plan()
    plan.signal = Signal.WAIT
    plan.diagnostics["long_price_path_confirmed"] = False
    item = {"quote": _quote(), "plan": plan, "name": "삼성전자", "chart_aligned": True, "bars": _bars()}

    diagnostic = observation_diagnostic(item)
    rows = observation_rows([item])

    assert diagnostic["stage"] == "PRICE_LEVEL_WAIT"
    assert "상방 경로 미확인" in diagnostic["actual"]
    assert "Hard 69,400 < Soft 69,600 < 진입 69,900 < 1차 71,000 < 2차 72,000" in diagnostic["actual"]
    assert diagnostic["required"] == "상방 경로 확인 · Hard < Soft < 진입 < 1차 < 2차 · 진입은 현재가 +0.2% 이내"
    assert "상방 경로 미확인" in rows[0]["관문 실제값 / 요구"]
    assert rows[0]["매수가 / 1·2차 목표"] == "구조 재확인 / 구조 재확인 / 구조 재확인"


def test_empty_us_rankings_expose_cached_fallback_funnel_without_extra_candidate_call():
    from app import candidate_pool_diagnostic_text, load_dashboard_candidate_snapshot

    liquid = [
        SimpleNamespace(symbol="GOOD", name="Good", exchange="NAS"),
        SimpleNamespace(symbol="FLAT", name="Flat", exchange="NAS"),
    ]
    def quote(symbol, *_args, **_kwargs):
        previous_close = 49 if symbol == "GOOD" else 51
        return Quote(symbol, Market.US, 50, previous_close, datetime(2026, 8, 20, 16, 0), volume=10_000, turnover=500_000)
    client = SimpleNamespace(
        request_scope=lambda _purpose: nullcontext(),
        market_rankings=lambda _market: {"거래량 TOP100": [], "거래대금 TOP100": []},
        quote=quote,
    )

    st.cache_data.clear()
    with patch("app.get_client", return_value=client), patch("app.US_LIQUID", liquid):
        candidates, diagnostics = load_dashboard_candidate_snapshot("US", "candidate-diagnostics-test")

    assert [candidate["symbol"] for candidate in candidates] == ["GOOD"]
    assert diagnostics == {
        "순위 행": 0, "병합 후보": 0, "가격 상한 통과": 0,
        "대체 시세 성공": 2, "상승·상한 통과": 1, "순위 오류": "없음",
    }
    assert "순위 행 0" in candidate_pool_diagnostic_text(diagnostics)
    assert "상승·상한 통과 1" in candidate_pool_diagnostic_text(diagnostics)


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


def test_closed_market_session_reopens_only_when_the_selected_market_becomes_active():
    from app import market_session_reopened

    with patch("app.market_session", return_value="US_PRE"):
        assert market_session_reopened(Market.US) is True

    with patch("app.market_session", return_value="US_CLOSED"):
        assert market_session_reopened(Market.US) is False


def test_capture_integrity_flag_requires_explicit_enabled_value():
    from app import capture_integrity_enabled

    assert capture_integrity_enabled("5") is True
    assert capture_integrity_enabled("true") is True
    assert capture_integrity_enabled("0") is False
    assert capture_integrity_enabled("") is False


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


def test_forecast_audit_records_complete_direction_path_even_when_price_structure_is_unaligned():
    from app import record_forecast_accuracy_audit

    store = SimpleNamespace(
        score_ready=Mock(return_value=0),
        has_pending_forecast_audit=Mock(return_value=False),
        save_once=Mock(return_value=(Path("case.json"), True)),
    )
    tick = RealtimeTick("005930", Market.KR, 70000, 1.2, datetime(2026, 8, 17, 10, 0))
    hub = SimpleNamespace(tick=Mock(return_value=tick))

    with patch("app.current_realtime_hub", return_value=hub):
        _, recorded = record_forecast_accuracy_audit(store, _plan(), _quote(), _bars(), False, 0.05)

    assert recorded is True
    assert store.save_once.call_args.args[0].validation_kind == "FORECAST_AUDIT"


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
    from app import APP_VERSION, record_forecast_accuracy_audit
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
    assert len(store.pending_forecast_audits("KR", version=APP_VERSION)) == 1


def test_pending_forecast_progress_uses_persisted_snapshots_and_remaining_time():
    from app import pending_forecast_progress_rows
    from scanner.validation import HorizonResult, ValidationCase

    case = ValidationCase(
        case_id="KR-005930-pending", version="6.6-holdout-calibration-validation", symbol="005930",
        market="KR", session="KR_REGULAR", signal_time="2026-08-19T10:00:00", signal="대기",
        quote_price=70000.0, latest_trade_price=70000.0, quote_age_seconds=0.0, quote_pass=True,
        entry=None, predicted_regime="박스권", actual_regime=None, regime_pass=None,
        target=None, target_basis="", stop=None, validation_kind="FORECAST_AUDIT",
        horizons=[HorizonResult(minutes, 69900.0, 70000.0, 70100.0, "박스권") for minutes in (5, 15, 30)],
        price_snapshots=[{"timestamp": "2026-08-19T10:05:00", "price": 70010.0, "source": "KIS REST"}],
    )

    class PendingStore:
        def pending_forecast_audits(self, *_args, **_kwargs):
            return [case]

    rows = pending_forecast_progress_rows(PendingStore(), "KR", datetime(2026, 8, 19, 10, 16))

    assert rows == [{
        "종목": "005930", "신호 시각": "10:00:00", "5분 실제가": "수집 완료",
        "15분 실제가": "수집 시각 대기", "30분 실제가": "대기 · 14분", "30분 완료까지": "약 14분",
    }]


def test_boundary_rest_fallback_failure_marks_data_missing_and_releases_reservation():
    from app import capture_forecast_boundary_case

    case = Mock(symbol="005930", exchange="")
    store = SimpleNamespace(
        due_forecast_audits=Mock(return_value=[case]),
        due_horizon_minutes=Mock(return_value=[5]),
        overdue_horizon_minutes=Mock(return_value=[]),
        update=Mock(),
    )
    budget = SimpleNamespace(release=Mock())
    client = SimpleNamespace(request_budget=budget)

    with (
        patch("app.display_tick", return_value=None),
        patch("app.current_client", return_value=client),
        patch("app.load_boundary_quote", side_effect=KISError("KIS 호출 예산 보호 중")),
    ):
        capture_forecast_boundary_case(store, Market.KR, case, datetime(2026, 8, 19, 10, 5))

    case.mark_data_missing.assert_called_once()
    assert "REST fallback 실패" in case.mark_data_missing.call_args.args[2]
    store.update.assert_called_once_with(case)
    budget.release.assert_called_once_with("경계 수집", 1)


def test_boundary_capture_uses_rest_fallback_when_websocket_tick_is_stale():
    from app import capture_forecast_boundary_case

    observed_at = datetime(2026, 8, 19, 10, 5, 20)
    case = SimpleNamespace(
        symbol="005930", exchange="", signal_time="2026-08-19T10:00:00",
        horizons=[SimpleNamespace(minutes=5)],
    )
    store = SimpleNamespace(
        capture_rest_snapshot_and_score=Mock(),
        overdue_horizon_minutes=Mock(return_value=[]),
        due_horizon_minutes=Mock(return_value=[5]),
    )
    budget = SimpleNamespace(release=Mock())
    client = SimpleNamespace(request_budget=budget)
    stale_tick = RealtimeTick("005930", Market.KR, 69900, 1.2, datetime(2026, 8, 19, 10, 3, 0))
    rest_quote = _quote()
    rest_quote.timestamp = observed_at

    with (
        patch("app.display_tick", return_value=stale_tick),
        patch("app.current_client", return_value=client),
        patch("app.load_boundary_quote", return_value=rest_quote) as load_quote,
    ):
        capture_forecast_boundary_case(store, Market.KR, case, observed_at)

    load_quote.assert_called_once_with("005930", "KR", "")
    store.capture_rest_snapshot_and_score.assert_called_once_with(
        "005930", "KR", observed_at, 70000, "KIS REST", "6.23-background-validation-scanner"
    )
    budget.release.assert_not_called()


def test_elapsed_boundary_attempts_rest_before_marking_data_missing():
    from app import capture_forecast_boundary_case

    observed_at = datetime(2026, 8, 19, 10, 6, 20)
    case = Mock(symbol="005930", exchange="")
    store = SimpleNamespace(
        overdue_horizon_minutes=Mock(return_value=[5]),
        due_horizon_minutes=Mock(return_value=[]),
        update=Mock(),
        capture_rest_snapshot_and_score=Mock(),
    )
    budget = SimpleNamespace(release=Mock())
    client = SimpleNamespace(request_budget=budget)
    rest_quote = _quote()
    rest_quote.timestamp = observed_at

    with (
        patch("app.display_tick", return_value=None),
        patch("app.current_client", return_value=client),
        patch("app.load_boundary_quote", return_value=rest_quote) as load_quote,
    ):
        capture_forecast_boundary_case(store, Market.KR, case, observed_at)

    load_quote.assert_called_once_with("005930", "KR", "")
    case.mark_data_missing.assert_called_once()
    assert "REST fallback 1회 실행" in case.mark_data_missing.call_args.args[2]
    store.capture_rest_snapshot_and_score.assert_not_called()
    store.update.assert_called_once_with(case)
    budget.release.assert_called_once_with("경계 수집", 1)


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


def test_visible_trade_cards_do_not_fill_with_downward_observations_when_recommendations_are_scarce():
    from app import visible_trade_cards

    upside = {"quote": _quote(), "plan": _plan(), "chart_aligned": True}
    observations = []
    for index in range(3):
        plan = deepcopy(_plan())
        plan.diagnostics["has_downward_forecast"] = True
        quote = _quote()
        quote.symbol = f"D{index}"
        plan.symbol = quote.symbol
        observations.append({"quote": quote, "plan": plan, "chart_aligned": True})

    visible = visible_trade_cards([upside, *observations])

    assert visible == [upside]


def test_live_card_hides_candidate_that_turns_into_daily_overheat_after_selection():
    from app import card_ready_for_display, card_trade_status

    plan = _plan()
    quote = _quote()
    quote.previous_close = 60000
    item = {"quote": quote, "plan": plan, "chart_aligned": True}

    assert quote.change_pct > 12.0
    assert card_trade_status(item) == "급등 과열 제외"
    assert card_ready_for_display(item) is False


def test_daily_overheat_guard_uses_refreshed_current_price_against_previous_close():
    from app import is_daily_overheated

    assert is_daily_overheated(112.01, 100.0) is True
    assert is_daily_overheated(112.00, 100.0) is False
    assert is_daily_overheated(0.0, 100.0) is False


def test_latest_completed_forecast_case_uses_only_active_rule_version_and_complete_paths():
    from app import latest_completed_forecast_case

    store = SimpleNamespace(cases=lambda: [
        SimpleNamespace(validation_kind="FORECAST_AUDIT", version="6.2-old", data_completeness="COMPLETE", full_path_pass=True, signal_time="2026-08-18T10:30:00"),
        SimpleNamespace(validation_kind="FORECAST_AUDIT", version="6.5.1-free-100-path-validation-retry", data_completeness="PENDING", full_path_pass=None, signal_time="2026-08-18T10:31:00"),
        SimpleNamespace(validation_kind="FORECAST_AUDIT", version="6.5.1-free-100-path-validation-retry", data_completeness="COMPLETE", full_path_pass=False, signal_time="2026-08-18T10:32:00"),
    ])

    latest = latest_completed_forecast_case(store, "6.5.1-free-100-path-validation-retry")

    assert latest is not None
    assert latest.signal_time == "2026-08-18T10:32:00"


def test_card_trade_status_displays_only_buy_ready_recommendations():
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
    assert card_trade_status(item) == "추천 조건 미충족"
    assert card_ready_for_display(item) is False

    plan.diagnostics["has_downward_forecast"] = True
    assert card_trade_status(item) == "하방 제외"
    assert visible_trade_cards([item], 10) == []

    plan.diagnostics["has_downward_forecast"] = False
    plan.diagnostics["forecast_path_ready"] = False
    assert card_ready_for_display(item) is False


def test_card_ready_for_display_rejects_too_small_net_target_or_too_distant_stop():
    from app import card_ready_for_display

    plan = _plan()
    quote = _quote()
    item = {"quote": quote, "plan": plan, "chart_aligned": True}
    assert card_ready_for_display(item) is True

    plan.target = 70200
    assert card_ready_for_display(item) is False

    plan.target = 71000
    plan.hard_stop = 68000
    assert card_ready_for_display(item) is False


def test_first_target_reachable_stays_visible_when_current_entry_timing_is_wait():
    from app import card_stage, first_target_reachable, visible_target1_wait_cards

    plan = _plan()
    plan.signal = Signal.WAIT
    plan.diagnostics["target_reachability"] = {
        "5": {"target1": True, "target2": False},
        "15": {"target1": True, "target2": False},
        "30": {"target1": False, "target2": False},
    }
    item = {"quote": _quote(), "plan": plan, "chart_aligned": True}

    assert first_target_reachable(item) is True
    assert card_stage(item) == "TARGET1_WAIT"
    assert visible_target1_wait_cards([item]) == [item]


def test_pullback_wait_keeps_intact_trend_with_five_minute_downside_visible():
    from app import card_ready_for_display, card_ready_for_pullback_wait, card_stage, card_trade_status

    plan = _plan()
    plan.signal = Signal.WAIT
    plan.forecasts[0] = ForecastPoint(5, 69500, 69800, 70000, Regime.DOWN, "5m pullback")
    plan.diagnostics["strategy_path"] = {
        "kind": "TREND_SWING",
        "structure_confirmed": True,
        "entry_timing_confirmed": False,
        "pullback_reentry_wait": True,
        "reentry_trigger": "VWAP·EMA9 재회복 뒤 5분 반전 확인",
    }
    item = {"quote": _quote(), "plan": plan, "chart_aligned": True}

    assert card_ready_for_display(item) is False
    assert card_ready_for_pullback_wait(item) is True
    assert card_trade_status(item) == "눌림·재매수 대기"
    assert card_stage(item) == "PULLBACK_WAIT"


def test_candidate_stage_summary_reports_observation_reason_counts():
    from app import candidate_stage_summary

    final_item = {"quote": _quote(), "plan": _plan(), "chart_aligned": True}
    observation_plan = _plan()
    observation_plan.signal = Signal.WAIT
    observation_plan.repeat_box = (69000, 71000)
    observation_plan.diagnostics["reward_risk_net"] = 0.8
    observation_item = {"quote": _quote(), "plan": observation_plan, "chart_aligned": True}

    summary = candidate_stage_summary([final_item, observation_item], candidate_pool=12)

    assert summary["funnel"]["후보풀"] == 12
    assert summary["stages"]["FINAL_BUY"] == 1
    assert summary["stages"]["OBSERVATION"] == 1
    assert summary["reasons"]["RR_FAIL · 비용 반영 손익비 부족"] == 1


def test_observation_card_exposes_actual_rr_blocker_and_stays_visible():
    from app import observation_diagnostic, visible_observation_cards

    plan = _plan()
    plan.signal = Signal.WAIT
    plan.diagnostics["reward_risk_net"] = 0.8
    plan.diagnostics["final_buy_gates"] = {"비용 반영 손익비": False}
    item = {"quote": _quote(), "plan": plan, "chart_aligned": True}

    diagnostic = observation_diagnostic(item)

    assert diagnostic["stage"] == "RR_FAIL"
    assert "0.80" in diagnostic["actual"]
    assert visible_observation_cards([item]) == [item]


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
    assert any("실시간 상승 차트 스캐너" in str(item.value) for item in app.markdown)
    assert any("005930" in str(item.value) for item in app.markdown)
    assert {metric.label for metric in app.metric} >= {"현재가", "추천 매수가", "추천 매도가 1차", "추천 매도가 2차", "손절가"}
    assert {metric.label for metric in app.metric} >= {"5분 대표 예상", "15분 대표 예상", "30분 대표 예상", "현재 차트 지지"}
    assert any("매매유형:" in str(item.value) for item in app.caption)
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
