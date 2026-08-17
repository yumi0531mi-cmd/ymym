from __future__ import annotations

import pandas as pd

from scanner.models import Regime
from scanner.persistence_engine import (
    FinalDecision,
    PersistenceResult,
    RiskResult,
    SwingStatistics,
    final_buy_decision,
    horizon_state,
    risk_state,
    swing_statistics,
)


def swing_bars() -> pd.DataFrame:
    prices = [
        100.0, 100.3, 101.1, 102.0, 101.2, 100.2,
        100.5, 101.4, 102.3, 101.4, 100.4,
        100.7, 101.6, 102.5, 101.6, 100.6,
        100.9, 101.8, 102.7, 101.8, 101.0, 101.2,
    ]
    index = pd.date_range("2026-01-02 09:00", periods=len(prices), freq="min")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [price + 0.12 for price in prices],
            "low": [price - 0.12 for price in prices],
            "close": prices,
            "volume": [1000] * len(prices),
        },
        index=index,
    )


def test_swing_statistics_requires_real_repeated_low_to_high_moves():
    stats = swing_statistics(swing_bars())
    assert stats.valid_count >= 3
    assert stats.representative_width_pct is not None
    assert 0.5 <= stats.representative_width_pct <= 5.0
    assert stats.representative_cycle_minutes is not None


def test_horizon_state_transitions_are_explicit():
    assert horizon_state(20, 300)[0] == "EARLY_FORMING"
    assert horizon_state(90, 300)[0] == "PROJECTED_90"
    assert horizon_state(180, 300)[0] == "PROJECTED_180"
    assert horizon_state(300, 300)[0] == "OBSERVED_300"


def test_risk_state_hard_exit_requires_price_beyond_noise_buffer():
    result = risk_state(swing_bars(), current_price=95.0, support=100.0, fake_breakdown=False)
    assert result.state == "HARD_EXIT"
    assert result.hard_stop is not None and result.hard_stop < result.soft_stop


def test_final_buy_blocks_hard_kill_even_when_other_gates_pass():
    persistence = PersistenceResult(
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
        remaining_minutes=100,
        new_entry_allowed=True,
    )
    persistence.swing.up_swings = [object(), object(), object()]  # valid_count only needs count in decision
    persistence.swing.representative_width_pct = 1.2
    risk = RiskResult("NORMAL_SWING", 100.0, 99.0, 2)
    decision = final_buy_decision(
        persistence=persistence,
        risk=risk,
        session_ok=True,
        data_fresh=True,
        execution_ok=True,
        entry_zone_ok=True,
        reward_risk_ok=True,
        cooldown_active=False,
        hard_kill=True,
        calibration_probability=None,
        calibration_samples=0,
    )
    assert decision.final_buy is False
    assert decision.gates["Hard Kill"] is False
