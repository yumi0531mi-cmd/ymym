from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.models import Regime
from scanner.strategy_ensemble import evaluate_ensemble


def _frame(upward: bool = True) -> pd.DataFrame:
    index = pd.date_range("2026-08-17 09:00", periods=40, freq="min")
    base = np.linspace(100.0, 106.0 if upward else 100.4, 40)
    return pd.DataFrame(
        {
            "open": base - 0.15,
            "high": base + 0.35,
            "low": base - 0.35,
            "close": base,
            "vwap": base - 0.20,
            "ema9": base - 0.10,
            "atr_pct": [1.2] * 32 + [0.7] * 8,
        },
        index=index,
    )


def test_breakout_cluster_uses_only_compatible_trend_strategies():
    frame = _frame(True)
    result = evaluate_ensemble(
        frame,
        regime=Regime.UP,
        box_valid=False,
        price=float(frame.close.iloc[-1]),
        vwap_ok=True,
        ema_ok=True,
        rvol=1.5,
        notional_rvol=1.4,
        fake_breakout=False,
        upper_rejection=False,
        opening_range_breakout=True,
    )

    assert result.cluster == "BREAKOUT_CONTINUATION"
    assert {3, 9, 13, 15}.issubset(result.active_ids)
    assert not result.conflicts


def test_range_reversal_cluster_is_not_scored_as_trend_continuation():
    frame = _frame(False)
    frame["vwap"] = frame["close"] - 0.05
    result = evaluate_ensemble(
        frame,
        regime=Regime.RANGE,
        box_valid=True,
        price=float(frame.close.iloc[-1]),
        vwap_ok=True,
        ema_ok=False,
        rvol=0.9,
        notional_rvol=0.9,
        fake_breakout=True,
        upper_rejection=False,
    )

    assert result.cluster == "REVERSAL_MEAN_REVERSION"
    assert {6, 7, 8}.issubset(result.active_ids)
    assert not set(result.active_ids).intersection({1, 2, 3, 4, 5, 9, 15})


def test_trend_mode_does_not_activate_box_reversal_strategies_when_box_is_present():
    frame = _frame(True)
    result = evaluate_ensemble(
        frame,
        regime=Regime.UP,
        box_valid=True,
        price=float(frame.close.iloc[-1]),
        vwap_ok=True,
        ema_ok=True,
        rvol=1.4,
        notional_rvol=1.3,
        fake_breakout=False,
        upper_rejection=False,
    )

    assert result.cluster != "CONFLICT"
    assert 7 not in result.active_ids
    assert not result.conflicts


def test_gap_uses_previous_session_close_not_first_intraday_close():
    frame = _frame(True)
    frame.loc[frame.index[0], "open"] = 101.0
    result = evaluate_ensemble(
        frame,
        regime=Regime.UP,
        box_valid=False,
        price=float(frame.close.iloc[-1]),
        vwap_ok=True,
        ema_ok=True,
        rvol=1.5,
        notional_rvol=1.4,
        fake_breakout=False,
        upper_rejection=False,
        previous_close=100.0,
    )

    assert 11 in result.active_ids
