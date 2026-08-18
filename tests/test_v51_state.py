from __future__ import annotations

from datetime import datetime

from scanner.calibration import calibration_for
from scanner.cycle import CycleStore
from scanner.models import Market
from scanner.persistence import EventStore
from scanner.validation import ValidationStore


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
        for minutes in (5, 10, 15, 30)
    ]
    complete_up_miss = [
        {"minutes": minutes, "actual": 101.0, "range_pass": minutes != 30, "direction_pass": True, "pass_all": minutes != 30}
        for minutes in (5, 10, 15, 30)
    ]
    rows = [
        {
            "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "DOWN", "full_path_pass": True, "horizons": complete_down,
        },
        {
            "validation_kind": "FORECAST_AUDIT", "data_completeness": "COMPLETE",
            "forecast_path_direction": "UP", "full_path_pass": False, "horizons": complete_up_miss,
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
