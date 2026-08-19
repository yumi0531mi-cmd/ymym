from datetime import datetime

import numpy as np
import pandas as pd

from scanner.engine import _classify_trade_type, analyze
from scanner.forecast import apply_risk_persistence_to_forecast, cap_downside_forecast_path, cap_upside_forecast_path, forecast_path
from scanner.indicators import enrich, resample
from scanner.models import Market, Quote, Regime, Signal
from scanner.models import ForecastPoint
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
    actual[15] = case.horizons[1].predicted_high * 2
    case.target_pass = True
    case.score_path(actual, plan.regime)
    assert case.full_path_pass is False
    assert case.complete_four_area_pass is False


def test_repeat_box_width_is_bounded():
    box = repeat_box(bars(slope=0), 100)
    if box:
        assert .5 <= (box[1] / box[0] - 1) * 100 <= 3.0


def test_upside_forecast_is_capped_by_primary_structure_before_breakout_confirmation():
    raw = [ForecastPoint(minutes, 100.0, 120.0, 125.0, Regime.UP, "raw") for minutes in (5, 15, 30)]

    capped = cap_upside_forecast_path(raw, 100.0, 103.0, 108.0, False)

    assert capped[-1].base <= 103.0
    assert capped[-1].high <= 103.0
    assert capped[-1].direction == Regime.UP


def test_upside_forecast_uses_next_resistance_only_after_all_breakout_conditions_are_confirmed():
    raw = [ForecastPoint(minutes, 100.0, 120.0, 125.0, Regime.UP, "raw") for minutes in (5, 15, 30)]

    extended = cap_upside_forecast_path(raw, 100.0, 103.0, 104.5, True)

    assert extended[-1].base == 104.5
    assert extended[-1].high <= 104.5


def test_upside_forecast_without_resistance_still_stops_at_five_percent_total_path_ceiling():
    raw = [ForecastPoint(minutes, 100.0, 150.0, 160.0, Regime.UP, "raw") for minutes in (5, 15, 30)]

    capped = cap_upside_forecast_path(raw, 100.0, None, None, False)

    assert capped[-1].base == 105.0
    assert capped[-1].high == 105.0


def test_mixed_trade_type_uses_repeated_swing_as_evidence_not_universal_entry_gate():
    up = ForecastPoint(5, 100.0, 101.0, 102.0, Regime.UP)
    assert _classify_trade_type(
        market_state="TREND", trend_strategy=True, range_strategy=False,
        point_5=up, point_15=ForecastPoint(15, 100, 102, 103, Regime.UP),
        point_30=ForecastPoint(30, 100, 103, 104, Regime.UP),
        pullback_wait=False, repeat_swing_available=False, hard_block=False,
    ) == "상승 추세 보유"


def test_downside_forecast_is_snapped_to_confirmed_support_and_keeps_confidence():
    raw = [ForecastPoint(minutes, 90.0, 92.0, 96.0, Regime.DOWN, "raw", 67.0) for minutes in (5, 15, 30)]

    snapped = cap_downside_forecast_path(raw, 100.0, 95.0)

    assert snapped[-1].base == 95.0
    assert snapped[-1].low >= 95.0
    assert snapped[-1].direction_confidence_pct == 67.0
    assert snapped[-1].structure_level == 95.0


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
    assert len([h for h in case.horizons if h.actual is not None]) == 3


def test_validation_snapshot_preserves_soft_and_hard_stops_separately():
    df = bars(180)
    current = float(df.close.iloc[-1])
    plan = analyze(quote(current), df)
    plan.soft_stop = current * 0.99
    plan.hard_stop = current * 0.98
    plan.stop = plan.hard_stop
    plan.target = current * 1.01
    case = ValidationCase.from_plan(plan, current, "US_REGULAR")
    start = datetime.fromisoformat(case.signal_time)
    idx = pd.date_range(start, periods=31, freq="min")
    future = pd.DataFrame({
        "open": current,
        "high": [current, current, current * 1.01] + [current] * 28,
        "low": [current, current * 0.985, current * 0.995] + [current] * 28,
        "close": current,
        "volume": 1000,
    }, index=idx)

    case.score_future_bars(future)

    assert case.soft_stop == current * 0.99
    assert case.hard_stop == current * 0.98
    assert case.soft_stop_first is True
    assert case.hard_stop_first is False
    assert case.target_first is True


def test_forecast_audit_scores_full_path_from_kis_rest_snapshots():
    df = bars(180)
    current = float(df.close.iloc[-1])
    plan = analyze(quote(current), df)
    case = ValidationCase.from_plan(
        plan, current, "US_REGULAR", validation_kind="FORECAST_AUDIT", latest_trade_time=plan.created_at,
        price_source="KIS REST",
    )
    case.price_snapshots = [
        {
            "timestamp": (pd.Timestamp(case.signal_time) + pd.Timedelta(horizon.minutes, unit="min")).isoformat(),
            "price": horizon.predicted_base,
            "source": "KIS REST",
        }
        for horizon in case.horizons
    ]

    assert case.score_price_snapshots() is True
    assert case.data_completeness == "COMPLETE"
    assert case.full_path_pass is True


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


def test_trade_levels_use_completed_five_minute_swing_highs():
    df = bars(180, slope=0)
    entry = 100.0
    target1, target2, support, target1_basis, target2_basis, _ = trade_levels(df, entry)
    completed_5m = resample(df, 5).iloc[:-1]
    highs = completed_5m.high[
        (completed_5m.high.shift(1) < completed_5m.high)
        & (completed_5m.high.shift(-1) < completed_5m.high)
    ]
    expected = sorted(float(value) for value in highs if value > entry)

    assert target1 == (expected[0] if expected else None)
    assert target2 == (expected[1] if len(expected) > 1 else None)
    assert target1_basis == ("완료 5분봉 스윙 저항" if expected else "1차 목표 미확인")
    assert target2_basis == ("다음 완료 5분봉 스윙 저항" if len(expected) > 1 else "2차 목표 미확인")
    assert support is None or support < entry


def test_target_pass_requires_touch_within_five_minutes():
    df = bars(180)
    current = float(df.close.iloc[-1])
    plan = analyze(quote(current), df)
    case = ValidationCase.from_plan(plan, current, "US_REGULAR")
    case.target = current * 1.01
    case.stop = current * 0.98
    start = datetime.fromisoformat(case.signal_time)
    idx = pd.date_range(start, periods=31, freq="min")
    future = pd.DataFrame({"open": current, "high": current * 1.002, "low": current * 0.999,
                           "close": current * 1.001, "volume": 1000}, index=idx)
    future.iloc[6, future.columns.get_loc("high")] = current * 1.02

    case.score_future_bars(future)

    assert case.target_pass is False
    assert case.target_first is False


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


def test_targets_below_live_quote_are_hidden(monkeypatch):
    from scanner import engine as engine_module

    frame = bars()
    monkeypatch.setattr(engine_module, "chart_entry_level", lambda *_args, **_kwargs: (100.0, "test entry"))
    monkeypatch.setattr(
        engine_module,
        "trade_levels",
        lambda *_args, **_kwargs: (110.0, 115.0, 90.0, "test target 1", "test target 2", "test support"),
    )

    plan = engine_module.analyze(quote(120.0), frame)

    assert plan.target is None
    assert plan.target2 is None
    assert plan.diagnostics["price_structure_valid"] is False


def test_invalid_structural_stop_falls_back_below_entry(monkeypatch):
    from scanner import engine as engine_module
    from scanner.persistence_engine import RiskResult

    frame = bars()
    monkeypatch.setattr(engine_module, "chart_entry_level", lambda *_args, **_kwargs: (100.0, "test entry"))
    monkeypatch.setattr(
        engine_module,
        "trade_levels",
        lambda *_args, **_kwargs: (130.0, 140.0, 95.0, "test target 1", "test target 2", "test support"),
    )
    monkeypatch.setattr(
        engine_module,
        "risk_state",
        lambda *_args, **_kwargs: RiskResult("NORMAL_SWING", 102.0, 105.0, 1, []),
    )

    plan = engine_module.analyze(quote(120.0), frame)

    assert plan.entry == 100.0
    assert plan.hard_stop is not None and plan.hard_stop < plan.entry
    assert plan.soft_stop is not None and plan.soft_stop < plan.entry
    assert plan.diagnostics["raw_hard_stop"] == 105.0


def test_downward_forecast_hides_upward_price_plan(monkeypatch):
    from scanner import engine as engine_module
    from scanner.models import ForecastPoint

    frame = bars()
    monkeypatch.setattr(engine_module, "chart_entry_level", lambda *_args, **_kwargs: (100.0, "test entry"))
    monkeypatch.setattr(
        engine_module,
        "trade_levels",
        lambda *_args, **_kwargs: (130.0, 140.0, 95.0, "test target 1", "test target 2", "test support"),
    )
    monkeypatch.setattr(
        engine_module,
        "forecast_path",
        lambda *_args, **_kwargs: [
            ForecastPoint(minutes, 118.0, 119.0, 120.0, Regime.DOWN, "test down")
            for minutes in (5, 15, 30)
        ],
    )

    plan = engine_module.analyze(quote(120.0), frame)

    assert plan.target is None
    assert plan.target2 is None
    assert plan.diagnostics["long_price_path_confirmed"] is False
    assert plan.diagnostics["final_buy_gates"]["15·30분 구조 경로"] is False
    assert plan.signal != Signal.BUY


def test_trend_pullback_keeps_fifteen_and_thirty_minute_up_structure_for_reentry_wait(monkeypatch):
    from scanner import engine as engine_module
    from scanner.persistence_engine import RiskResult
    from scanner.strategy import TimeframeState

    frame = bars()
    monkeypatch.setattr(
        engine_module,
        "multi_timeframe",
        lambda *_args, **_kwargs: {
            1: TimeframeState(1, Regime.DOWN, .5, 100.0, 100.0, 101.0),
            5: TimeframeState(5, Regime.DOWN, .5, 100.0, 100.0, 101.0),
            15: TimeframeState(15, Regime.UP, .8, 101.0, 100.0, 99.0),
            30: TimeframeState(30, Regime.UP, .8, 101.0, 100.0, 99.0),
        },
    )
    monkeypatch.setattr(engine_module, "chart_entry_level", lambda *_args, **_kwargs: (100.0, "test entry"))
    monkeypatch.setattr(
        engine_module,
        "trade_levels",
        lambda *_args, **_kwargs: (130.0, 140.0, 95.0, "test target 1", "test target 2", "test support"),
    )
    monkeypatch.setattr(
        engine_module,
        "forecast_path",
        lambda *_args, **_kwargs: [
            ForecastPoint(5, 118.0, 119.0, 120.0, Regime.DOWN, "5m pullback"),
            ForecastPoint(15, 120.0, 125.0, 130.0, Regime.UP, "15m trend"),
            ForecastPoint(30, 122.0, 130.0, 135.0, Regime.UP, "30m trend"),
        ],
    )
    monkeypatch.setattr(
        engine_module,
        "risk_state",
        lambda *_args, **_kwargs: RiskResult("NORMAL_PULLBACK", 98.0, 95.0, 2, ["normal pullback"]),
    )

    plan = engine_module.analyze(quote(120.0), frame)

    assert plan.regime == Regime.UP
    assert plan.diagnostics["long_price_path_confirmed"] is True
    assert plan.diagnostics["has_downward_forecast"] is False
    assert plan.diagnostics["final_buy_gates"]["전략별 5분 진입 타이밍"] is False
    assert plan.diagnostics["strategy_path"]["pullback_reentry_wait"] is True
    assert plan.signal == Signal.WAIT


def test_indicator_enrichment_adds_role_separated_direction_inputs():
    enriched = enrich(bars(80))

    for column in ("stoch_k", "stoch_d", "macd_hist", "adx", "plus_di", "minus_di", "boll_width_pct", "obv", "cmf", "mfi", "roc10", "regression_slope"):
        assert column in enriched.columns
        assert pd.notna(enriched[column].iloc[-1])


def test_forecast_path_keeps_horizon_specific_direction_engine_details():
    forecast = forecast_path(bars(100, slope=.08), Regime.UP, reference_price=108.0)

    assert [point.minutes for point in forecast] == [5, 15, 30]
    assert getattr(forecast, "diagnostics")["market_state"] in {"TREND", "BREAKOUT", "TRANSITION", "RANGE"}
    engines = getattr(forecast, "diagnostics")["direction_engines"]
    assert set(engines) == {"5", "15", "30"}
    assert engines["5"]["weights"] != engines["30"]["weights"]


def test_engine_exposes_data_quality_direction_engines_and_target_reachability():
    plan = analyze(quote(float(bars().close.iloc[-1])), bars())

    assert plan.diagnostics["data_quality"]["completed_minute_bars"] >= 30
    assert set(plan.diagnostics["direction_engines"]) == {"5", "15", "30"}
    assert set(plan.diagnostics["target_reachability"]) == {"5", "15", "30"}
    assert {"risk_state", "pattern_fatigue", "persistence_score"} <= set(plan.diagnostics["direction_invalidation"])


def test_real_breakdown_invalidates_stale_upside_forecasts_at_every_horizon():
    points = [ForecastPoint(minutes, 100.0, 102.0, 104.0, Regime.UP, "test") for minutes in (5, 15, 30)]

    adjusted = apply_risk_persistence_to_forecast(points, 100.0, "REAL_BREAKDOWN", 85, 0)

    assert all(point.direction == Regime.DOWN for point in adjusted)
    assert all(point.base < 100.0 for point in adjusted)


def test_pattern_fatigue_damps_the_thirty_minute_path_more_than_five_minutes():
    points = [ForecastPoint(minutes, 100.0, 104.0, 105.0, Regime.UP, "test") for minutes in (5, 15, 30)]

    adjusted = apply_risk_persistence_to_forecast(points, 100.0, "NORMAL_SWING", 50, 40)

    assert adjusted[-1].base - 100.0 < adjusted[0].base - 100.0
