from __future__ import annotations

from scanner.calibration import CalibrationResult
from scanner.persistence_engine import FinalDecision, final_buy_decision
from scanner.persistence_engine import PersistenceResult, RiskResult, SwingStatistics


def _persistence() -> PersistenceResult:
    return PersistenceResult(
        score=85,
        band="PERSISTENT_A",
        horizon_state="OBSERVED_300",
        confidence_pct=90.0,
        horizon_minutes=300,
        swing=SwingStatistics(),
        vwap_occupancy_pct=80.0,
        structure_ok=True,
        liquidity_ok=True,
        spread_ok=True,
        remaining_minutes=120,
        new_entry_allowed=True,
    )


def _risk() -> RiskResult:
    return RiskResult("NORMAL_SWING", soft_stop=99.0, hard_stop=98.0, recovery_window_minutes=2)


def test_target_80_flag_requires_real_calibrated_strategy_outcomes():
    assert not CalibrationResult("KR", "KR_REGULAR", "TREND_PULLBACK", "80-89", 99, None, None).target_80_verified
    assert not CalibrationResult("KR", "KR_REGULAR", "TREND_PULLBACK", "80-89", 100, 79.9, 0.2).target_80_verified
    assert CalibrationResult(
        "KR", "KR_REGULAR", "TREND_PULLBACK", "80-89", 100, 80.0, 0.2,
        recent_samples=100, recent_probability_pct=80.0, recent_average_net_return_pct=0.1,
    ).target_80_verified
    assert not CalibrationResult(
        "KR", "KR_REGULAR", "TREND_PULLBACK", "80-89", 100, 80.0, -0.2,
        recent_samples=100, recent_probability_pct=80.0, recent_average_net_return_pct=-0.1,
    ).target_80_verified


def test_80_percent_gate_blocks_calibrated_underperforming_strategy():
    decision: FinalDecision = final_buy_decision(
        persistence=_persistence(), risk=_risk(), session_ok=True, data_fresh=True,
        execution_ok=True, entry_zone_ok=True, reward_risk_ok=True,
        cooldown_active=False, hard_kill=False, calibration_probability=79.9,
        calibration_samples=100, calibration_expectancy_pct=0.1,
    )
    assert not decision.final_buy
    assert not decision.gates["80% 전체 경로 실측"]


def test_unverified_small_sample_is_not_mislabelled_as_80_percent_verified():
    decision: FinalDecision = final_buy_decision(
        persistence=_persistence(), risk=_risk(), session_ok=True, data_fresh=True,
        execution_ok=True, entry_zone_ok=True, reward_risk_ok=True,
        cooldown_active=False, hard_kill=False, calibration_probability=None,
        calibration_samples=12, calibration_expectancy_pct=None,
    )
    assert decision.gates["80% 전체 경로 실측"]


def test_80_percent_gate_blocks_negative_cost_adjusted_expectancy():
    decision: FinalDecision = final_buy_decision(
        persistence=_persistence(), risk=_risk(), session_ok=True, data_fresh=True,
        execution_ok=True, entry_zone_ok=True, reward_risk_ok=True,
        cooldown_active=False, hard_kill=False, calibration_probability=85.0,
        calibration_samples=100, calibration_expectancy_pct=-0.01,
    )
    assert not decision.final_buy
    assert not decision.gates["비용 반영 기대값"]
