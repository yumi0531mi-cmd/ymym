from __future__ import annotations

from datetime import datetime

from scanner.engine import _max_spread, _minimum_notional_rvol, _minimum_rvol
from scanner.models import Market, Quote, Regime


def _quote(session: str) -> Quote:
    return Quote("TEST", Market.US, 100.0, 99.0, datetime.now().astimezone(), 99.9, 100.1, 1_000, 100_000, session)


def test_us_session_liquidity_thresholds_are_distinct():
    day = _quote("US_DAY")
    pre = _quote("US_PRE")
    regular = _quote("US_REGULAR")
    after = _quote("US_AFTER")

    assert _minimum_rvol(day, Regime.UP) > _minimum_rvol(pre, Regime.UP) > _minimum_rvol(regular, Regime.UP)
    assert _minimum_rvol(after, Regime.UP) > _minimum_rvol(day, Regime.UP)
    assert _minimum_notional_rvol(after) > _minimum_notional_rvol(day) > _minimum_notional_rvol(regular)
    assert _max_spread(after) < _max_spread(day) < _max_spread(regular)


def test_us_range_strategy_keeps_a_lower_but_bounded_relative_volume_gate():
    pre = _quote("US_PRE")
    assert _minimum_rvol(pre, Regime.RANGE) < _minimum_rvol(pre, Regime.UP)
    assert _minimum_rvol(pre, Regime.RANGE) >= 0.80
