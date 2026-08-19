from __future__ import annotations

from datetime import datetime

from scanner.calibration import calibration_for
from scanner.cycle import CycleStore
from scanner.models import Market, Regime
from scanner.persistence import EventStore
from scanner.validation import HorizonResult, ValidationCase, ValidationStore


class RowsOnlyStore:
    def __init__(self, rows):
        self.rows = rows

    def load_all(self):
        return self.rows


def test_cycle_real_breakdown_is_counted_once_per_completed_bar():
    cycle_store = CycleStore(EventStore({}))
    state = cycle_store.get("005930", Market.KR, datetime(2026, 1, 2, 10, 0))
    cycle_store.apply_risk_state(state, "REAL_BREAKDOWN", "2026-01-02T10:00:00")
    cycle_store.apply_risk_state(state, "REAL_BREAKDOWN", "2026-01-02T10:00:00")
    assert state.real_breakdowns == 1
    assert state.hard_kill is False
    cycle_store.apply_risk_state(state, "REAL_BREAKDOWN", "2026-01-02T10:01:00")
    assert state.hard_kill is True


def test_calibration_hides_probability_before_one_hundred_complete_samples():
    rows = [
        {
            "market": "KR", "session": "KR_REGULAR", "strategy": "TREND_SWING · 상승 추세 눌림",
            "score": 82, "complete_four_area_pass": True, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True, "net_return_pct": 0.2,
        }
        for _ in range(99)
    ]
    result = calibration_for(
        RowsOnlyStore(rows), market="KR", session="KR_REGULAR", strategy="TREND_SWING · 상승 추세 눌림", score=83,
    )
    assert result.samples == 99
    assert result.probability_pct is None


def test_calibration_uses_complete_path_rate_after_one_hundred_samples():
    rows = [
        {
            "market": "US", "session": "US_REGULAR", "strategy": "RANGE_SWING · 박스 하단 평균회귀",
            "score": 76, "complete_four_area_pass": index < 80, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True, "net_return_pct": 0.3 if index < 80 else -0.2,
        }
        for index in range(100)
    ]
    result = calibration_for(
        RowsOnlyStore(rows), market="US", session="US_REGULAR", strategy="RANGE_SWING · 박스 하단 평균회귀", score=78,
    )
    assert result.samples == 100
    assert result.probability_pct == 80.0


def test_calibration_can_scope_samples_to_target_rule_version():
    rows = [
        {
            "market": "US", "session": "US_REGULAR", "strategy": "TREND_SWING · 상승 추세 눌림",
            "score": 84, "complete_four_area_pass": True, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True, "net_return_pct": 0.3,
            "version": "5.1.1-five-minute-target",
        }
        for _ in range(100)
    ]
    rows.append(
        {
            "market": "US", "session": "US_REGULAR", "strategy": "TREND_SWING · 상승 추세 눌림",
            "score": 84, "complete_four_area_pass": False, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True, "net_return_pct": -0.2,
            "version": "5.1-persistence-cycle",
        }
    )

    result = calibration_for(
        RowsOnlyStore(rows), market="US", session="US_REGULAR", strategy="TREND_SWING · 상승 추세 눌림", score=84,
        version="5.1.1-five-minute-target",
    )

    assert result.samples == 100
    assert result.probability_pct == 100.0


def test_calibration_keeps_time_forward_holdout_separate_from_fit_cohort():
    strategy = "TREND_SWING · 상승 추세 눌림"
    rows = [
        {
            "market": "KR", "session": "KR_REGULAR", "strategy": strategy,
            "score": 83, "complete_four_area_pass": index < 80, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True,
            "net_return_pct": 0.3 if index < 80 else -0.2,
            "signal_time": f"2026-08-18T09:{index:02d}:00",
        }
        for index in range(100)
    ]
    rows.extend(
        {
            "market": "KR", "session": "KR_REGULAR", "strategy": strategy,
            "score": 83, "complete_four_area_pass": index < 90, "data_completeness": "COMPLETE",
            "entry_executable": True, "structural_target_confirmed": True,
            "net_return_pct": 0.3 if index < 90 else -0.2,
            "signal_time": f"2026-08-19T09:{index:02d}:00",
        }
        for index in range(100)
    )

    result = calibration_for(
        RowsOnlyStore(rows), market="KR", session="KR_REGULAR", strategy=strategy, score=83,
    )

    assert result.samples == 100
    assert result.probability_pct == 80.0
    assert result.recent_samples == 100
    assert result.recent_probability_pct == 90.0
    assert result.target_80_verified is True


def test_strategy_summary_includes_expectancy_and_profit_factor(tmp_path):
    strategy = "BREAKOUT_CONTINUATION · TREND_SWING"
    rows = [
        {"market": "KR", "session": "KR_REGULAR", "strategy": strategy, "target_pass": True, "net_return_pct": 0.60},
        {"market": "KR", "session": "KR_REGULAR", "strategy": strategy, "target_pass": True, "net_return_pct": 0.40},
        {"market": "KR", "session": "KR_REGULAR", "strategy": strategy, "target_pass": False, "net_return_pct": -0.20},
    ]
    store = ValidationStore(tmp_path)
    store.load_all = lambda: rows  # type: ignore[method-assign]

    report = store.summary()["by_strategy_session"][f"KR:KR_REGULAR:{strategy}"]

    assert round(report["target_first_rate"], 1) == 66.7
    assert round(report["expectancy_pct"], 4) == 0.2667
    assert report["average_win_pct"] == 0.5
    assert report["average_loss_pct"] == -0.2
    assert report["profit_factor"] == 5.0


def test_forecast_audit_summary_separates_all_direction_paths_from_trade_metrics(tmp_path):
    complete_down = [
        {"minutes": minutes, "actual": 99.0, "range_pass": True, "direction_pass": True, "pass_all": True}
        for minutes in (5, 15, 30)
    ]
    complete_up_miss = [
        {"minutes": minutes, "actual": 101.0, "range_pass": minutes != 30, "direction_pass": True, "pass_all": minutes != 30}
        for minutes in (5, 15, 30)
    ]
    rows = [
        {
            "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "DOWN", "price_source": "KIS 체결", "full_path_pass": True, "horizons": complete_down,
        },
        {
            "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "UP", "price_source": "KIS REST", "full_path_pass": False, "horizons": complete_up_miss,
        },
    ]
    store = ValidationStore(tmp_path)
    store.load_all = lambda: rows  # type: ignore[method-assign]

    report = store.summary()
    audit = report["forecast_audit"]

    assert report["signals"] == 0
    assert audit["records"] == 2
    assert audit["complete_paths"] == 2
    assert audit["strict_full_path_pass"] == 1
    assert audit["direction_full_path_pass"] == 2
    assert audit["horizons"]["30"]["strict_rate"] == 50.0
    assert audit["by_prediction_direction"]["DOWN"]["strict_full_path_rate"] == 100.0
    assert audit["by_price_source"]["KIS REST"]["strict_full_path_rate"] == 0.0


def test_versioned_forecast_summary_keeps_price_error_and_path_metrics_separate(tmp_path):
    rows = [
        {
            "version": "sample", "market": "US", "validation_kind": "FORECAST_AUDIT",
            "data_completeness": "COMPLETE", "full_path_pass": True,
            "horizons": [
                {"minutes": 5, "actual": 101.0, "range_pass": True, "direction_pass": True, "pass_all": True, "price_error_pct": 1.0, "mfe_pct": 1.5, "mae_pct": -0.3},
                {"minutes": 15, "actual": 102.0, "range_pass": True, "direction_pass": True, "pass_all": True, "price_error_pct": -0.5},
                {"minutes": 30, "actual": 103.0, "range_pass": True, "direction_pass": True, "pass_all": True, "price_error_pct": 0.25},
            ],
        },
        {
            "version": "prior", "market": "US", "validation_kind": "FORECAST_AUDIT",
            "data_completeness": "COMPLETE", "full_path_pass": False, "horizons": [],
        },
        {
            "version": "sample", "market": "US", "validation_kind": "ACTIONABLE", "data_completeness": "COMPLETE",
            "target_first": True, "target2": 104.0, "hard_stop_first": False, "net_return_pct": 0.8,
        },
    ]
    store = ValidationStore(tmp_path)
    store.load_all = lambda: rows  # type: ignore[method-assign]

    summary = store.versioned_validation_summary("sample", "US")

    assert summary["forecast"]["records"] == 1
    assert summary["forecast"]["horizons"]["5"]["direction_rate"] == 100.0
    assert summary["forecast"]["horizons"]["5"]["mean_price_error_pct"] == 1.0
    assert summary["forecast"]["horizons"]["5"]["mean_mfe_pct"] == 1.5
    assert summary["final_buy"] == {
        "records": 1, "complete": 1, "target1_first": 1, "target2_recorded": 1,
        "hard_stop_first": 0, "average_net_return_pct": 0.8, "net_return_samples": 1,
    }


def test_versioned_forecast_breakdown_groups_only_matching_completed_forecasts(tmp_path):
    rows = [
        {
            "version": "sample", "market": "US", "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "UP", "predicted_regime": "상승", "strategy": "TREND_SWING", "signal_time": "2026-08-19T09:10:00", "quote_price": 100.0,
            "horizons": [{"minutes": minute, "predicted_direction": "UP", "actual": 101.0, "direction_pass": True, "range_pass": True, "pass_all": True, "price_error_pct": 0.2} for minute in (5, 15, 30)],
        },
        {
            "version": "sample", "market": "US", "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "UP", "predicted_regime": "상승", "strategy": "TREND_SWING", "signal_time": "2026-08-19T09:20:00", "quote_price": 100.0,
            "horizons": [{"minutes": minute, "predicted_direction": "UP", "actual": 99.0, "direction_pass": False, "range_pass": False, "pass_all": False, "price_error_pct": -0.4} for minute in (5, 15, 30)],
        },
        {
            "version": "prior", "market": "US", "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "DOWN", "horizons": [],
        },
    ]
    store = ValidationStore(tmp_path)
    store.load_all = lambda: rows  # type: ignore[method-assign]

    breakdown = store.versioned_forecast_breakdown("sample", "US")

    assert breakdown["예측 방향"] == [{
        "구분": "UP", "완료": 2, "5분 방향 적중률": 50.0, "15분 방향 적중률": 50.0,
        "30분 방향 적중률": 50.0, "30분 평균 가격 오차": -0.1, "30분 평균 절대 오차": 0.30000000000000004,
    }]
    assert breakdown["매매 구조"][0]["구분"] == "TREND_SWING"
    assert breakdown["매매 구조·30분 방향"][0]["구분"] == "TREND_SWING · 30분 UP"

    insight = store.versioned_repeated_failure_insight("sample", "US")
    assert insight is not None
    assert insight["30분 방향 적중률"] == 50.0

    trace = store.versioned_forecast_trace("sample", "US")
    assert len(trace) == 2
    assert {row["분류"] for row in trace} == {"3시간대 방향 적중", "1개 이상 방향 실패"}
    assert all(row["입력 진단"] == "v6.11 미저장" for row in trace)

    confusion = store.versioned_direction_confusion("sample", "US")
    assert sum(row["건수"] for row in confusion if row["구간"] == "5분") == 2
    assert any(row["예측 방향"] == "UP" and row["실제 방향"] == Regime.UP.value for row in confusion)


def test_versioned_input_diagnostics_summary_keeps_fixed_horizon_history(tmp_path):
    rows = [
        {
            "version": "sample", "market": "US", "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "analysis_snapshot": {"direction_engines": {
                "5": {"completed_timeframe_bars": 23, "input_ready": True, "state": "RANGE"},
                "15": {"completed_timeframe_bars": 7, "input_ready": True, "state": "TRANSITION"},
                "30": {"completed_timeframe_bars": 3, "input_ready": True, "state": "TRANSITION"},
            }},
        },
        {
            "version": "sample", "market": "US", "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "analysis_snapshot": {"direction_engines": {
                "5": {"completed_timeframe_bars": 24, "input_ready": True, "state": "RANGE"},
                "15": {"completed_timeframe_bars": 8, "input_ready": True, "state": "RANGE"},
                "30": {"completed_timeframe_bars": 4, "input_ready": True, "state": "TRANSITION"},
            }},
        },
    ]
    store = ValidationStore(tmp_path)
    store.load_all = lambda: rows  # type: ignore[method-assign]

    summary = store.versioned_input_diagnostics_summary("sample", "US")

    assert summary[0]["입력 준비"] == "2/2"
    assert summary[1]["완료 고차 봉 중앙"] == 7.5
    assert summary[2]["완료 고차 봉 최소"] == 3
    assert summary[2]["엔진 상태 분포"] == "TRANSITION 2"


def test_rest_snapshot_store_scores_mature_forecast_audit_once(tmp_path):
    case = ValidationCase(
        case_id="US-TEST-audit", version="test", symbol="TEST", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT", price_source="KIS REST",
        horizons=[HorizonResult(minutes, 100.5, 101.0, 101.5, "상승") for minutes in (5, 15, 30)],
    )
    store = ValidationStore(tmp_path)
    store.save(case)
    assert store.capture_rest_snapshot_and_score("TEST", "US", datetime(2026, 8, 18, 10, 5), 101.0) == 0
    assert store.capture_rest_snapshot_and_score("TEST", "US", datetime(2026, 8, 18, 10, 10), 101.0) == 0
    assert store.capture_rest_snapshot_and_score("TEST", "US", datetime(2026, 8, 18, 10, 15), 101.0) == 0
    assert store.capture_rest_snapshot_and_score("TEST", "US", datetime(2026, 8, 18, 10, 30), 101.0) == 1
    stored = store.cases()[0]
    assert stored.price_snapshots[-1]["source"] == "KIS REST"


def test_pending_forecast_audits_keep_oldest_five_across_card_rotation(tmp_path):
    store = ValidationStore(tmp_path)
    for index in range(6):
        case = ValidationCase(
            case_id=f"US-{index}", version="test", symbol=f"T{index}", market="US", session="US_PRE",
            signal_time=f"2026-08-18T10:0{index}:00", signal="대기", quote_price=100.0,
            latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
            predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
            stop=None, validation_kind="FORECAST_AUDIT", exchange="NAS",
        )
        store.save(case)

    pending = store.pending_forecast_audits("US", limit=5)

    assert [case.symbol for case in pending] == ["T0", "T1", "T2", "T3", "T4"]


def test_late_first_rest_snapshot_marks_data_missing_without_blocking_queue(tmp_path):
    case = ValidationCase(
        case_id="US-late", version="test", symbol="LATE", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT", exchange="NAS",
        horizons=[HorizonResult(minutes, 100.5, 101.0, 101.5, "상승") for minutes in (5, 15, 30)],
    )
    store = ValidationStore(tmp_path)
    store.save(case)

    assert store.capture_rest_snapshot_and_score("LATE", "US", datetime(2026, 8, 18, 10, 32), 101.0) == 0
    assert store.cases()[0].data_completeness == "DATA_MISSING"
    assert store.pending_forecast_audits("US") == []


def test_boundary_capture_failure_is_saved_as_data_missing_not_forecast_miss(tmp_path):
    case = ValidationCase(
        case_id="US-missing", version="test", symbol="MISS", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT",
        horizons=[HorizonResult(minutes, 100.5, 101.0, 101.5, "상승") for minutes in (5, 15, 30)],
    )
    store = ValidationStore(tmp_path)
    case.mark_data_missing([5, 15], datetime(2026, 8, 18, 10, 15), "KIS 호출 예산 보호 중")
    store.save(case)

    stored = store.cases()[0]
    assert stored.data_completeness == "DATA_MISSING"
    assert stored.full_path_pass is False
    assert stored.capture_failures["5"]["reason"] == "KIS 호출 예산 보호 중"
    assert store.pending_forecast_audits("US") == []


def test_elapsed_boundary_window_remains_due_for_one_rest_fallback_attempt(tmp_path):
    case = ValidationCase(
        case_id="US-window", version="test", symbol="WINDOW", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT",
        horizons=[HorizonResult(minutes, 100.5, 101.0, 101.5, "상승") for minutes in (5, 15, 30)],
    )
    store = ValidationStore(tmp_path)
    store.save(case)

    due = store.due_forecast_audits("US", datetime(2026, 8, 18, 10, 6, 16), version="test")
    assert [item.symbol for item in due] == ["WINDOW"]
    stored = store.cases()[0]
    assert stored.data_completeness == "PENDING"
    assert stored.capture_failures == {}


def test_save_once_rejects_same_symbol_audit_even_if_a_fast_rerun_changes_regime(tmp_path):
    store = ValidationStore(tmp_path)
    first = ValidationCase(
        case_id="US-DUPE-first", version="dedupe", symbol="DUPE", market="US", session="US_PRE",
        signal_time="2026-08-19T12:30:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="박스", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT",
    )
    rerun = ValidationCase(
        case_id="US-DUPE-rerun", version="dedupe", symbol="DUPE", market="US", session="US_PRE",
        signal_time="2026-08-19T12:30:30", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT",
    )

    _, first_saved = store.save_once(first, cooldown_seconds=300)
    _, rerun_saved = store.save_once(rerun, cooldown_seconds=300)

    assert first_saved is True
    assert rerun_saved is False
    assert len(store.cases()) == 1


def test_due_forecast_audits_selects_only_a_missing_horizon_boundary(tmp_path):
    case = ValidationCase(
        case_id="US-due", version="test", symbol="DUE", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT", batch_id="free-us-test",
        horizons=[HorizonResult(minutes, 100.5, 101.0, 101.5, "상승") for minutes in (5, 15, 30)],
        price_snapshots=[{"timestamp": "2026-08-18T10:05:10", "price": 101.0, "source": "KIS REST"}],
    )
    store = ValidationStore(tmp_path)
    store.save(case)

    assert store.due_forecast_audits("US", datetime(2026, 8, 18, 10, 5, 30), version="test", batch_id="free-us-test") == []
    due = store.due_forecast_audits("US", datetime(2026, 8, 18, 10, 15, 20), version="test", batch_id="free-us-test")
    assert [item.symbol for item in due] == ["DUE"]


def test_horizon_score_keeps_price_error_and_representative_reachability(tmp_path):
    case = ValidationCase(
        case_id="US-score", version="test", symbol="SCORE", market="US", session="US_PRE",
        signal_time="2026-08-18T10:00:00", signal="대기", quote_price=100.0,
        latest_trade_price=100.0, quote_age_seconds=0.0, quote_pass=True, entry=None,
        predicted_regime="상승", actual_regime=None, regime_pass=None, target=None, target_basis="",
        stop=None, validation_kind="FORECAST_AUDIT",
        horizons=[HorizonResult(5, 100.0, 101.0, 102.0, "상승")],
    )

    case.score_path({5: 101.5}, Regime.UP)

    result = case.horizons[0]
    assert round(float(result.price_error_pct), 4) == round((101.5 / 101.0 - 1) * 100, 4)
    assert result.representative_reached is True
