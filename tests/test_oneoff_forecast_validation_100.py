from __future__ import annotations

import pandas as pd

from oneoff_forecast_validation_100 import (
    FrozenForecast,
    _actual_from_future_bars,
    _direction,
    score_case,
    summarize,
)
from scanner.models import Regime


def _bars(start: str, prices: list[float]) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=len(prices), freq="1min", tz="Asia/Seoul")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [price + 0.2 for price in prices],
            "low": [price - 0.2 for price in prices],
            "close": prices,
            "volume": [1000] * len(prices),
        },
        index=index,
    )


def _case() -> FrozenForecast:
    return FrozenForecast(
        symbol="000001",
        name="TEST",
        market="KR",
        exchange="",
        signal_time="2026-08-19T09:00:00+09:00",
        origin_price=100.0,
        session="KR_REGULAR",
        strategy="TREND_SWING",
        regime=Regime.UP.value,
        score=80,
        risk_state="NORMAL_SWING",
        persistence_score=70,
        predictions={
            "5": {"low": 100.1, "base": 100.5, "high": 101.0, "direction": Regime.UP.value, "basis": "test"},
            "15": {"low": 100.5, "base": 101.0, "high": 101.6, "direction": Regime.UP.value, "basis": "test"},
            "30": {"low": 101.0, "base": 102.0, "high": 103.0, "direction": Regime.UP.value, "basis": "test"},
        },
    )


def test_direction_uses_same_half_bp_neutral_band_as_validation_engine() -> None:
    assert _direction(100.0, 100.04) == Regime.RANGE.value
    assert _direction(100.0, 100.06) == Regime.UP.value
    assert _direction(100.0, 99.94) == Regime.DOWN.value


def test_actual_from_future_bars_uses_latest_close_not_after_cutoff() -> None:
    frame = _bars("2026-08-19 09:01:00", [100.1, 100.2, 100.3, 100.4, 100.5, 100.6])
    actual, at, reason = _actual_from_future_bars(frame, "2026-08-19T09:00:00+09:00", 5)
    assert reason == ""
    assert actual == 100.5
    assert at is not None and at.endswith("+09:00")


def test_score_case_scores_all_three_horizons() -> None:
    prices = [100.1] * 4 + [100.5] + [100.6] * 9 + [101.0] + [101.2] * 14 + [102.0]
    rows = score_case(_case(), _bars("2026-08-19 09:01:00", prices))
    assert len(rows) == 3
    assert all(row.valid for row in rows)
    assert all(row.direction_hit is True for row in rows)
    assert all(row.range_hit is True for row in rows)


def test_summary_compares_forecast_error_with_no_change_baseline() -> None:
    prices = [100.1] * 4 + [100.5] + [100.6] * 9 + [101.0] + [101.2] * 14 + [102.0]
    rows = score_case(_case(), _bars("2026-08-19 09:01:00", prices))
    result = summarize(rows, 1)
    for horizon in (5, 15, 30):
        item = result["horizons"][str(horizon)]
        assert item["valid_samples"] == 1
        assert item["direction_accuracy_pct"] == 100.0
        assert item["forecast_range_hit_pct"] == 100.0
        assert item["mean_abs_price_error_pct"] == 0.0
        assert item["forecast_beats_no_change_baseline"] is True
