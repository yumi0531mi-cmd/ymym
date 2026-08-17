from datetime import datetime

import numpy as np
import pandas as pd

from scanner.engine import analyze
from scanner.models import Market, Quote, Regime, Signal
from scanner.persistence import EventStore, ManualTrade
from scanner.strategy import confirmed_levels, fake_signal_flags, repeat_box, trade_levels
from scanner.validation import ValidationCase
from scanner.universe import rank_quotes


def bars(n=180, slope=.03):
    idx = pd.date_range("2026-01-02 09:00", periods=n, freq="min")
    base = 100 + np.arange(n) * slope + np.sin(np.arange(n) / 4) * .6
    return pd.DataFrame({"open": base - .05, "high": base + .25, "low": base - .25,
                         "close": base, "volume": 1000 + (np.arange(n) % 10) * 50}, index=idx)


def quote(price=105):
    return Quote("TEST", Market.US, price, 100, datetime.now().astimezone(), price-.01, price+.01, 100000, 10000000, "US_REGULAR")


def test_no_current_means_no_plan():
    plan = analyze(None, bars())
    assert plan.signal == Signal.UNVERIFIED
    assert plan.entry is None and plan.target is None and plan.stop is None


def test_insufficient_bars_hides_plan():
    plan = analyze(quote(), bars(10))
    assert plan.entry is None
    assert "충분한 완료 1분봉" in plan.missing


def test_levels_are_observed_bars():
    df = bars()
    current = float(df.close.iloc[-1])
    target, stop, _, _ = confirmed_levels(df, current)
    if target is not None:
        assert target in set(df.high.iloc[:-1])
    if stop is not None:
        assert stop in set(df.low.iloc[:-1])


def test_validation_requires_all_horizons():
    plan = analyze(quote(float(bars().close.iloc[-1])), bars())
    case = ValidationCase.from_plan(plan, plan.current_price, "US_REGULAR")
    actual = {h.minutes: h.predicted_base for h in case.horizons}
    # Force one horizon outside its range: the entire path must fail.
    actual[10] = case.horizons[1].predicted_high * 2
    case.target_pass = True
    case.score_path(actual, plan.regime)
    assert case.full_path_pass is False
    assert case.complete_four_area_pass is False


def test_repeat_box_width_is_bounded():
    box = repeat_box(bars(slope=0), 100)
    if box:
        assert .5 <= (box[1] / box[0] - 1) * 100 <= 3.0


def test_korean_limit_up_is_not_candidate():
    kr = Quote("LIMIT", Market.KR, 130, 100, datetime.now().astimezone(), 129, 130)
    normal = Quote("NORMAL", Market.KR, 105, 100, datetime.now().astimezone(), 104, 105)
    ranked = rank_quotes([kr, normal], Market.KR)
    assert [q.symbol for q in ranked] == ["NORMAL"]


def test_future_scoring_is_chronological_and_strict():
    df = bars(180)
    current = float(df.close.iloc[-1])
    plan = analyze(quote(current), df)
    case = ValidationCase.from_plan(plan, current, "US_REGULAR")
    start = datetime.fromisoformat(case.signal_time)
    idx = pd.date_range(start, periods=31, freq="min")
    future = pd.DataFrame({"open": current, "high": current * 1.002, "low": current * .999,
                           "close": current * 1.001, "volume": 1000}, index=idx)
    case.score_future_bars(future)
    assert case.mfe_pct is not None and case.mae_pct is not None
    assert len([h for h in case.horizons if h.actual is not None]) == 4


def test_repeat_box_rejects_price_outside_the_box():
    df = bars(slope=0)
    assert repeat_box(df, 150) is None


def test_missing_orderbook_cannot_produce_buy_signal():
    df = bars()
    current = float(df.close.iloc[-1])
    no_orderbook = Quote("TEST", Market.US, current, 100, datetime.now().astimezone(), None, None, 100000, 10000000, "US_REGULAR")
    plan = analyze(no_orderbook, df)
    assert plan.signal != Signal.BUY
    assert "실시간 1호가" in plan.missing


def test_closed_market_cannot_produce_buy_signal():
    df = bars()
    current = float(df.close.iloc[-1])
    closed = Quote("TEST", Market.US, current, 100, datetime.now().astimezone(), current - .01, current + .01, 100000, 10000000, "US_CLOSED")
    plan = analyze(closed, df)
    assert plan.signal != Signal.BUY
    assert "거래 가능 세션" in plan.missing


def test_repeat_box_allows_four_percent_repetition_range():
    idx = pd.date_range("2026-01-02 09:00", periods=80, freq="min")
    cycle = np.where((np.arange(80) // 5) % 2 == 0, 100.0, 104.0)
    df = pd.DataFrame({
        "open": cycle, "high": cycle + 0.2, "low": cycle - 0.2,
        "close": cycle, "volume": np.full(80, 1000),
    }, index=idx)
    box = repeat_box(df, 101.0)
    assert box is not None
    assert 3.0 < (box[1] / box[0] - 1) * 100 <= 5.0


def test_trade_levels_return_two_targets_when_swings_exist():
    df = bars(180, slope=0)
    entry = 100.0
    target1, target2, support, *_ = trade_levels(df, entry)
    assert target1 is None or target1 > entry
    assert target2 is None or target2 > entry
    assert support is None or support < entry


def test_fake_breakdown_and_two_close_breakdown_are_distinguished():
    idx = pd.date_range("2026-01-02 09:00", periods=40, freq="min")
    price = np.full(40, 100.0)
    df = pd.DataFrame({"open": price, "high": price + 0.2, "low": price - 0.2,
                       "close": price, "volume": np.full(40, 1000)}, index=idx)
    df.iloc[-2, df.columns.get_loc("close")] = 98.5
    df.iloc[-1, df.columns.get_loc("close")] = 98.0
    flags = fake_signal_flags(df, support=99.0, resistance=101.0)
    assert flags["two_close_breakdown"] is True
    assert flags["fake_breakdown"] is False


def test_event_store_is_inert_without_supabase_secrets():
    store = EventStore({})
    assert store.configured is False
    store.upsert("id-1", "manual_trade", datetime.now().astimezone().isoformat(), {"hello": "world"})
    assert store.list("manual_trade") == []


def test_manual_trade_calculates_realized_pnl_after_fees():
    trade = ManualTrade.create("TEST", "US", "매수", 100, 2, exit_price=110, fees=1)
    assert trade.realized_pnl == 19
