from __future__ import annotations

from datetime import datetime

import pandas as pd

from .calibration import MIN_COMPLETE_PATH_SAMPLES
from .forecast import apply_risk_persistence_to_forecast, cap_downside_forecast_path, cap_upside_forecast_path, forecast_path
from .indicators import enrich
from .models import Market, Quote, Regime, Signal, TradePlan
from .persistence_engine import final_buy_decision, persistence_score, risk_state
from .sessions import remaining_session_minutes
from .strategy import chart_entry_level, confirmed_levels, fake_signal_flags, multi_timeframe, price_zone_in_box, repeat_box, trade_levels
from .strategy_ensemble import evaluate_ensemble


ACTIVE_SESSIONS = {"KR_REGULAR", "US_DAY", "US_PRE", "US_REGULAR", "US_AFTER"}

# Relative-volume and notional thresholds are screening gates, not predicted
# outcomes. U.S. sessions have materially different depth, so the same 1-minute
# activity score must not be treated as equally liquid outside regular trading.
US_SESSION_LIQUIDITY = {
    "US_DAY": {"rvol": 1.55, "notional_rvol": 1.45, "max_spread": 0.18},
    "US_PRE": {"rvol": 1.40, "notional_rvol": 1.30, "max_spread": 0.18},
    "US_REGULAR": {"rvol": 1.00, "notional_rvol": 0.85, "max_spread": 0.25},
    "US_AFTER": {"rvol": 1.60, "notional_rvol": 1.50, "max_spread": 0.15},
}


def _breakout_retest_confirmed(completed: pd.DataFrame, resistance: float | None) -> bool:
    """Require completed-bar breakout, retest, and hold; an intrabar spike is insufficient."""
    if resistance is None or resistance <= 0 or len(completed) < 3:
        return False
    recent = completed.tail(3)
    closes_hold = bool((recent.close >= resistance * 0.998).all())
    retest_seen = bool((recent.low <= resistance * 1.004).any())
    return closes_hold and retest_seen and float(recent.close.iloc[-1]) >= resistance


def _max_spread(quote: Quote) -> float:
    if quote.market == Market.KR:
        return 0.15
    return float(US_SESSION_LIQUIDITY.get(quote.session, US_SESSION_LIQUIDITY["US_AFTER"])["max_spread"])


def _minimum_rvol(quote: Quote, regime: Regime) -> float:
    if quote.market == Market.US:
        base = float(US_SESSION_LIQUIDITY.get(quote.session, US_SESSION_LIQUIDITY["US_AFTER"])["rvol"])
        return max(0.80, base - 0.20) if regime == Regime.RANGE else base
    return 0.80 if regime == Regime.RANGE else 1.00


def _minimum_notional_rvol(quote: Quote) -> float:
    if quote.market == Market.US:
        return float(US_SESSION_LIQUIDITY.get(quote.session, US_SESSION_LIQUIDITY["US_AFTER"])["notional_rvol"])
    return 0.85


def _round_trip_cost_pct(market: Market) -> float:
    return 0.05 if market == Market.KR else 0.10


def _classify_trade_type(
    *,
    market_state: str,
    trend_strategy: bool,
    range_strategy: bool,
    point_5: object | None,
    point_15: object | None,
    point_30: object | None,
    pullback_wait: bool,
    repeat_swing_available: bool,
    hard_block: bool,
) -> str:
    """Select one current chart behaviour; repeated swing is evidence, not a universal gate."""
    directions = [getattr(point, "direction", None) for point in (point_5, point_15, point_30)]
    if hard_block or any(direction == Regime.DOWN for direction in directions[1:]):
        return "구조 하방 회피"
    if pullback_wait:
        return "눌림 후 상승 대기"
    if market_state == "BREAKOUT" and all(direction == Regime.UP for direction in directions):
        return "돌파 추세"
    if trend_strategy and all(direction == Regime.UP for direction in directions):
        return "상승 추세 보유"
    if range_strategy and directions[0] == Regime.UP:
        return "반복단타 가능" if repeat_swing_available else "박스 하단 반등"
    if trend_strategy:
        return "단발 상승 기회"
    return "관찰 대기"


def _final_buy_evidence(
    *,
    points: list[object],
    target1: float | None,
    entry: float,
    hard_stop: float,
    reward_risk: float | None,
    structure_confirmed: bool,
    persistence_score_value: int | None,
    repeat_swing_available: bool,
) -> tuple[list[str], float | None]:
    """Return compact, auditable evidence shared by all mixed trade types."""
    directions = " / ".join(
        f"{getattr(point, 'minutes', '?')}분 {getattr(getattr(point, 'direction', None), 'value', '미확인')}"
        for point in points
    )
    confidences = [float(value) for point in points if isinstance((value := getattr(point, "direction_confidence_pct", None)), (int, float))]
    evidence = [directions]
    if structure_confirmed:
        evidence.append("15·30분 구조 확인")
    if target1 is not None and entry > 0:
        evidence.append(f"1차 목표 여유 {(float(target1) / entry - 1) * 100:+.2f}%")
    if hard_stop > 0 and entry > hard_stop:
        evidence.append(f"손절거리 {(entry / hard_stop - 1) * 100:.2f}%")
    if reward_risk is not None:
        evidence.append(f"비용 반영 손익비 {reward_risk:.2f}")
    if repeat_swing_available:
        evidence.append("반복 Swing 확인")
    structural_confidence = float(persistence_score_value or 0)
    confidence = (sum(confidences) / len(confidences) * 0.60 + structural_confidence * 0.40) if confidences else None
    return evidence[:5], round(confidence, 1) if confidence is not None else None


def _plan(
    quote: Quote,
    now: datetime,
    signal: Signal,
    strategy: str,
    regime: Regime,
    *,
    entry: float | None = None,
    entry_basis: str = "진입 기준 미확인",
    target1: float | None = None,
    target2: float | None = None,
    stop: float | None = None,
    target1_basis: str = "1차 목표 미확인",
    target2_basis: str = "2차 목표 미확인",
    stop_basis: str = "손절 기준 미확인",
    score: int = 0,
    reasons: list[str] | None = None,
    missing: list[str] | None = None,
    repeat: tuple[float, float] | None = None,
    verified: bool = False,
    diagnostics: dict[str, object] | None = None,
    forecasts=None,
    soft_stop: float | None = None,
    hard_stop: float | None = None,
    risk_status: str = "미확인",
    persistence: object | None = None,
    calibration_probability: float | None = None,
    calibration_samples: int = 0,
) -> TradePlan:
    persistence_score_value = getattr(persistence, "score", None)
    persistence_band = getattr(persistence, "band", "미산출")
    persistence_confidence = getattr(persistence, "confidence_pct", None)
    return TradePlan(
        symbol=quote.symbol,
        market=quote.market,
        created_at=now,
        signal=signal,
        strategy=strategy,
        regime=regime,
        current_price=quote.price,
        entry=entry,
        entry_basis=entry_basis,
        target=target1,
        stop=stop,
        target_basis=target1_basis,
        stop_basis=stop_basis,
        forecasts=forecasts or [],
        score=score,
        reasons=reasons or [],
        missing=missing or [],
        repeat_box=repeat,
        data_verified=verified,
        diagnostics=diagnostics or {},
        target2=target2,
        target2_basis=target2_basis,
        invalidation=stop,
        soft_stop=soft_stop,
        hard_stop=hard_stop,
        risk_state=risk_status,
        persistence_score=persistence_score_value,
        persistence_band=persistence_band,
        persistence_confidence=persistence_confidence,
        calibration_probability=calibration_probability,
        calibration_samples=calibration_samples,
    )


def analyze(
    quote: Quote | None,
    bars: pd.DataFrame | None,
    orderbook_required: bool = True,
    round_trip_cost_pct: float | None = None,
    minimum_score: int = 80,
    *,
    cooldown_active: bool = False,
    hard_kill: bool = False,
    calibration_probability: float | None = None,
    calibration_samples: int = 0,
    calibration_expectancy_pct: float | None = None,
) -> TradePlan:
    """Create one explainable v5.1 manual repeated-scalping evaluation.

    The result is never an order. `진입 고려` is shown only when the common FINAL_BUY
    gates pass using completed bars, live quote execution safety and persisted-cycle
    state supplied by the caller.
    """
    now = datetime.now().astimezone()
    if quote is None or quote.price <= 0:
        empty = Quote("", Market.KR, 0, 0, now)
        return _plan(
            empty, now, Signal.UNVERIFIED, "데이터 대기", Regime.UNKNOWN,
            reasons=["실시간 현재가가 없어 분석하지 않습니다."], missing=["실시간 현재가"],
        )

    missing: list[str] = []
    if bars is None or len(bars) < 31:
        missing.append("충분한 완료 1분봉")
    if orderbook_required and (not quote.bid or not quote.ask):
        missing.append("실시간 1호가")
    if quote.session not in ACTIVE_SESSIONS:
        missing.append("거래 가능 세션")
    if bars is None or len(bars) < 31:
        return _plan(
            quote, now, Signal.UNVERIFIED, "데이터 대기", Regime.UNKNOWN,
            reasons=["완료 1분봉이 부족하여 Swing·지속성·진입 기준을 계산하지 않습니다."], missing=missing,
        )

    df = enrich(bars)
    completed = df.iloc[:-1].copy()
    if len(completed) < 30:
        return _plan(
            quote, now, Signal.UNVERIFIED, "데이터 대기", Regime.UNKNOWN,
            reasons=["진행 중인 마지막 1분봉을 제외하면 데이터가 부족합니다."],
            missing=missing + ["완료 1분봉"],
        )

    states = multi_timeframe(completed)
    # 15분 구조가 상승이면 5분 하락은 즉시 하락 추세가 아니라 정상 눌림일 수 있다.
    # 5분은 전략의 진입 타이밍으로 별도 판정한다.
    trend_confirmed = states[15].regime == Regime.UP
    box = repeat_box(df, quote.price)
    zone = price_zone_in_box(box, quote.price)
    if trend_confirmed:
        regime, strategy = Regime.UP, "TREND_SWING · 상승 추세 눌림"
    elif box:
        regime, strategy = Regime.RANGE, "RANGE_SWING · 박스 하단 평균회귀"
    elif states[15].regime == Regime.DOWN and states[5].regime == Regime.DOWN:
        regime, strategy = Regime.DOWN, "NONE · 하락 구조"
    else:
        regime, strategy = Regime.TRANSITION, "NONE · 장세 전환 대기"

    latest = completed.iloc[-1]
    entry, entry_basis = chart_entry_level(df, quote.price, regime, box)
    entry = entry or quote.price
    target1, target2, support, target1_basis, target2_basis, support_basis = trade_levels(df, entry, box)
    raw_forecast_points = forecast_path(completed, regime, reference_price=quote.price)
    forecast_engine_diagnostics = dict(getattr(raw_forecast_points, "diagnostics", {}))
    entry_resistance_1m, _, _, _ = confirmed_levels(df, entry)
    flags = fake_signal_flags(completed, support, entry_resistance_1m)
    risk = risk_state(df, current_price=quote.price, support=support, fake_breakdown=flags["fake_breakdown"])
    fallback_stop = max(0.0, entry - max(float(latest.atr) * 1.20, 0.01))
    raw_hard_stop = risk.hard_stop
    # A long-entry stop must always be below the entry. If a stale or inconsistent
    # structural candidate violates that relationship, use the completed-bar ATR
    # fallback rather than displaying a stop above the suggested buy level.
    displayed_stop = float(raw_hard_stop) if raw_hard_stop is not None and 0 < float(raw_hard_stop) < entry else fallback_stop
    soft_stop = float(risk.soft_stop) if risk.soft_stop is not None and 0 < float(risk.soft_stop) < entry else displayed_stop
    stop_basis = f"{support_basis} 기반 Hard Stop" if raw_hard_stop is not None and 0 < float(raw_hard_stop) < entry else "완료봉 ATR 기반 구조 손절"
    spread = quote.spread_pct
    max_spread = _max_spread(quote)
    spread_ok = spread is not None and spread <= max_spread
    rvol_threshold = _minimum_rvol(quote, regime)
    rvol_ok = float(latest.rvol) >= rvol_threshold
    notional_threshold = _minimum_notional_rvol(quote)
    notional_ok = float(latest.notional_rvol) >= notional_threshold
    breakout_flow_confirmed = rvol_ok and notional_ok
    breakout_trade_confirmed = quote.source == "KIS_WEBSOCKET" and spread_ok and quote.price >= float(target1 or float("inf"))
    breakout_retest_confirmed = _breakout_retest_confirmed(completed, target1)
    breakout_extension_confirmed = bool(
        target1 is not None and target2 is not None
        and breakout_flow_confirmed and breakout_trade_confirmed and breakout_retest_confirmed
    )
    remaining = remaining_session_minutes(quote.market, quote.timestamp)
    persistence = persistence_score(
        df,
        regime=regime,
        box_valid=bool(box),
        rvol=float(latest.rvol),
        notional_rvol=float(latest.notional_rvol),
        spread_ok=spread_ok,
        remaining_minutes=remaining,
    )
    forecast_points = cap_upside_forecast_path(
        raw_forecast_points, quote.price, target1, target2, breakout_extension_confirmed
    )
    forecast_points = cap_downside_forecast_path(forecast_points, quote.price, support)
    forecast_points = apply_risk_persistence_to_forecast(
        forecast_points,
        quote.price,
        risk.state,
        persistence.score,
        persistence.swing.fatigue,
    )
    forecast_by_minutes = {point.minutes: point for point in forecast_points}
    forecast_path_ready = {point.minutes for point in forecast_points} == {5, 15, 30}
    point_5 = forecast_by_minutes.get(5)
    point_15 = forecast_by_minutes.get(15)
    point_30 = forecast_by_minutes.get(30)
    is_trend_strategy = strategy.startswith("TREND_SWING")
    is_range_strategy = strategy.startswith("RANGE_SWING")
    trend_structure_confirmed = bool(
        forecast_path_ready
        and point_15 is not None and point_30 is not None
        and point_15.direction == Regime.UP and point_30.direction == Regime.UP
    )
    range_structure_confirmed = bool(
        forecast_path_ready and box
        and point_15 is not None and point_30 is not None
        and point_15.direction in {Regime.UP, Regime.RANGE}
        and point_30.direction in {Regime.UP, Regime.RANGE}
    )
    # A 5-minute downside in an otherwise intact trend or range is a timing state,
    # not a structural breakdown. Structural downside is reserved for 15/30 minutes.
    has_downward_forecast = bool(
        forecast_path_ready
        and ((point_15 is not None and point_15.direction == Regime.DOWN)
             or (point_30 is not None and point_30.direction == Regime.DOWN))
    )
    long_price_path_confirmed = (
        trend_structure_confirmed if is_trend_strategy
        else range_structure_confirmed if is_range_strategy
        else False
    )
    trend_entry_timing_confirmed = bool(
        trend_structure_confirmed and point_5 is not None
        and point_5.direction == Regime.UP and float(point_5.base) > quote.price
    )
    range_entry_timing_confirmed = bool(
        range_structure_confirmed and point_5 is not None
        and point_5.direction == Regime.UP and float(point_5.base) > quote.price
    )
    strategy_entry_timing_confirmed = (
        trend_entry_timing_confirmed if is_trend_strategy
        else range_entry_timing_confirmed if is_range_strategy
        else False
    )
    trend_pullback_reentry_wait = bool(
        trend_structure_confirmed and point_5 is not None
        and point_5.direction in {Regime.DOWN, Regime.RANGE}
        and risk.state in {"NORMAL_PULLBACK", "SHAKEOUT", "NORMAL_SWING"}
    )
    range_pullback_reentry_wait = bool(
        range_structure_confirmed and point_5 is not None
        and point_5.direction in {Regime.DOWN, Regime.RANGE}
        and zone == "하단 진입 구간"
        and risk.state not in {"REAL_BREAKDOWN", "HARD_EXIT"}
    )
    pullback_reentry_wait = trend_pullback_reentry_wait or range_pullback_reentry_wait
    targets_ahead_of_quote = bool(
        long_price_path_confirmed and target1 is not None and target2 is not None
        and float(target1) > quote.price and float(target2) > float(target1)
    )
    if not targets_ahead_of_quote:
        target1, target2 = None, None
        if has_downward_forecast:
            target1_basis, target2_basis = "하방 경로 관찰 중", "하방 경로 관찰 중"
        elif not forecast_path_ready:
            target1_basis, target2_basis = "방향 재계산 중", "방향 재계산 중"
        else:
            target1_basis, target2_basis = "현재가 위 구조 목표 재확인 중", "다음 구조 목표 재확인 중"

    cost_pct = _round_trip_cost_pct(quote.market) if round_trip_cost_pct is None else max(round_trip_cost_pct, 0.0)
    cost_amount = entry * cost_pct / 100
    reward_risk: float | None = None
    structure_ok = bool(
        target1 and target2 and displayed_stop
        and displayed_stop < entry < target1 < target2
        and quote.price < target1
    )
    if structure_ok:
        reward = float(target1) - entry - cost_amount
        risk_amount = entry - displayed_stop + cost_amount
        reward_risk = reward / risk_amount if risk_amount > 0 else None
    rr_ok = reward_risk is not None and reward_risk >= 1.10

    session_ok = quote.session in ACTIVE_SESSIONS
    vwap_ok = quote.price >= float(latest.vwap)
    ema_ok = quote.price >= float(latest.ema9)
    range_entry_ok = bool(box and zone == "하단 진입 구간")
    trend_entry_ok = trend_confirmed and vwap_ok and ema_ok
    entry_zone_ok = trend_entry_ok or range_entry_ok
    opening_window = completed.head(30)
    opening_range_breakout = bool(
        len(opening_window) == 30
        and len(completed) > 30
        and float(latest.close) >= float(opening_window.high.max()) * 0.998
        and float(latest.rvol) >= 1.0
    )
    ensemble = evaluate_ensemble(
        completed,
        regime=regime,
        box_valid=bool(box),
        price=quote.price,
        vwap_ok=vwap_ok,
        ema_ok=ema_ok,
        rvol=float(latest.rvol),
        notional_rvol=float(latest.notional_rvol),
        fake_breakout=bool(flags["fake_breakout"]),
        upper_rejection=bool(flags["upper_rejection"]),
        opening_range_breakout=opening_range_breakout,
        previous_close=quote.previous_close,
    )
    execution_ok = (
        spread_ok and rvol_ok and notional_ok and not flags["fake_breakout"]
        and not flags["upper_rejection"] and not ensemble.conflicts
    )
    data_verified = not missing

    execution_score = (
        15 if entry_zone_ok else 0
    ) + (10 if vwap_ok and ema_ok else 0) + (10 if rvol_ok else 0) + (10 if notional_ok else 0) + (10 if spread_ok else 0) + (15 if rr_ok else 0) + (10 if execution_ok else 0)
    score = int(round(min(100, persistence.score * 0.40 + execution_score * 0.45 + ensemble.score * 0.15)))

    decision = final_buy_decision(
        persistence=persistence,
        risk=risk,
        session_ok=session_ok,
        data_fresh=data_verified,
        execution_ok=execution_ok,
        entry_zone_ok=entry_zone_ok,
        reward_risk_ok=rr_ok,
        cooldown_active=cooldown_active,
        hard_kill=hard_kill,
        calibration_probability=calibration_probability,
        calibration_samples=calibration_samples,
        calibration_expectancy_pct=calibration_expectancy_pct,
        # Confirmed repeated swing remains a valuable clue for re-entry, but a
        # one-time trend, breakout, pullback or box rebound must not be rejected
        # solely because the same pattern has not repeated three times yet.
        require_repeat_swing=False,
        minimum_persistence_score=55 if is_trend_strategy else 70,
    )
    gates = dict(decision.gates)
    gates["15·30분 구조 경로"] = long_price_path_confirmed
    gates["전략별 5분 진입 타이밍"] = strategy_entry_timing_confirmed
    gates["호환 전략 조합"] = ensemble.cluster not in {"CONFLICT", "DATA_WAIT"} and ensemble.score >= 30
    gates["사용자 최소점수"] = score >= minimum_score
    final_buy = all(gates.values())

    reasons: list[str] = list(persistence.reasons) + list(ensemble.reasons)
    if not session_ok:
        reasons.append(f"거래 가능 세션이 아닙니다: {quote.session}")
    if not entry_zone_ok:
        reasons.append("추세 눌림 또는 박스 하단 진입 위치가 아직 확인되지 않았습니다.")
    if not rvol_ok:
        reasons.append(f"상대거래량 부족: {float(latest.rvol):.2f}배 < {rvol_threshold:.2f}배")
    if not notional_ok:
        reasons.append(f"5분 거래대금 상대강도 부족: {float(latest.notional_rvol):.2f}배 < {notional_threshold:.2f}배")
    if not spread_ok:
        reasons.append("호가 스프레드가 확인되지 않았거나 세션 허용 한도를 넘었습니다.")
    if not rr_ok:
        reasons.append("1차 목표 기준 비용 반영 순손익비가 1.10 미만입니다.")
    if has_downward_forecast:
        reasons.append("15분 또는 30분 예상이 하방이어서 구조 상승 후보에서 제외합니다.")
    elif not forecast_path_ready:
        reasons.append("5·15·30분 방향을 계산할 완료 분봉이 아직 충분하지 않습니다.")
    elif not long_price_path_confirmed:
        reasons.append("전략별 15·30분 구조 경로가 아직 확인되지 않았습니다.")
    elif not strategy_entry_timing_confirmed:
        reasons.append("5분은 구조가 아니라 현재 진입 타이밍입니다. 눌림·재반전 조건을 확인합니다.")
    if flags["fake_breakout"]:
        reasons.append("가짜 돌파 경고: 저항 위 고가 뒤 종가가 저항 아래로 복귀했습니다.")
    if flags["upper_rejection"]:
        reasons.append("매도 압력 경고: 완료 1분봉의 윗꼬리가 길어 추격을 피합니다.")
    reasons.extend(f"전략 충돌: {item}" for item in ensemble.conflicts)
    reasons.extend(risk.reasons)
    if cooldown_active:
        reasons.append("이전 구조붕괴 뒤 쿨다운 중입니다.")
    if hard_kill:
        reasons.append("당일 Hard Kill 상태입니다. 신규 진입을 차단합니다.")
    if calibration_samples < MIN_COMPLETE_PATH_SAMPLES:
        reasons.append(f"전체 경로 검증 누적 중: 동일 조건 실측 표본 {calibration_samples}건 / {MIN_COMPLETE_PATH_SAMPLES}건")
    hard_block = regime == Regime.DOWN or not session_ok or risk.state in {"REAL_BREAKDOWN", "HARD_EXIT"} or hard_kill
    signal = Signal.BLOCK if hard_block else Signal.BUY if final_buy else Signal.WAIT
    repeat_swing_available = bool(
        persistence.swing.valid_count >= 3
        and persistence.swing.representative_width_pct
        and 0.5 <= persistence.swing.representative_width_pct <= 5.0
    )
    hard_block = regime == Regime.DOWN or not session_ok or risk.state in {"REAL_BREAKDOWN", "HARD_EXIT"} or hard_kill
    trade_type = _classify_trade_type(
        market_state=str(forecast_engine_diagnostics.get("market_state", "TRANSITION")),
        trend_strategy=is_trend_strategy, range_strategy=is_range_strategy,
        point_5=point_5, point_15=point_15, point_30=point_30,
        pullback_wait=pullback_reentry_wait, repeat_swing_available=repeat_swing_available, hard_block=hard_block,
    )
    final_buy_evidence, evidence_confidence = _final_buy_evidence(
        points=list(forecast_points), target1=target1, entry=entry, hard_stop=displayed_stop,
        reward_risk=reward_risk, structure_confirmed=long_price_path_confirmed,
        persistence_score_value=persistence.score, repeat_swing_available=repeat_swing_available,
    )
    diagnostics: dict[str, object] = {
        "timeframes": {minutes: state.regime.value for minutes, state in states.items()},
        "final_buy_gates": gates,
        "persistence": persistence.to_dict(),
        "risk": risk.to_dict(),
        "spread_pct": spread,
        "bid": quote.bid,
        "ask": quote.ask,
        "max_spread_pct": max_spread,
        "vwap": float(latest.vwap),
        "ema9": float(latest.ema9),
        "atr": float(latest.atr),
        "atr_pct": float(latest.atr_pct),
        "rvol": float(latest.rvol),
        "rvol_threshold": rvol_threshold,
        "notional_rvol": float(latest.notional_rvol),
        "notional_rvol_threshold": notional_threshold,
        "breakout_flow_confirmed": breakout_flow_confirmed,
        "breakout_trade_confirmed": breakout_trade_confirmed,
        "breakout_retest_confirmed": breakout_retest_confirmed,
        "breakout_extension_confirmed": breakout_extension_confirmed,
        "reward_risk_net": reward_risk,
        "price_structure_valid": structure_ok,
        "long_price_path_confirmed": long_price_path_confirmed,
        "has_downward_forecast": has_downward_forecast,
        "forecast_path_ready": forecast_path_ready,
        "strategy_path": {
            "kind": "TREND_SWING" if is_trend_strategy else "RANGE_SWING" if is_range_strategy else "NONE",
            "structure_confirmed": long_price_path_confirmed,
            "entry_timing_confirmed": strategy_entry_timing_confirmed,
            "pullback_reentry_wait": pullback_reentry_wait,
            "repeat_swing_required_for_entry": is_range_strategy,
            "repeat_swing_available": repeat_swing_available,
            "reentry_trigger": (
                "VWAP·EMA9 재회복 뒤 5분 반전 확인" if trend_pullback_reentry_wait
                else "박스 하단 지지 재확인 뒤 5분 반전 확인" if range_pullback_reentry_wait
                else "현재 5분 진입 타이밍 확인"
            ),
            "directions": {
                str(minutes): forecast_by_minutes[minutes].direction.value
                for minutes in (5, 15, 30) if minutes in forecast_by_minutes
            },
        },
        "raw_hard_stop": raw_hard_stop,
        "round_trip_cost_pct": cost_pct,
        "target1_window_minutes": 5,
        "entry_resistance_1m": entry_resistance_1m,
        "box_zone": zone,
        "false_signal_flags": flags,
        "completed_bars": len(completed),
        "five_hour_data_ready": persistence.horizon_state == "OBSERVED_300",
        "remaining_session_minutes": remaining,
        "completed_bar_at": str(completed.index[-1]),
        "trade_type": trade_type,
        "final_buy_evidence": final_buy_evidence,
        "evidence_confidence_pct": evidence_confidence,
        "strategy_ensemble": ensemble.to_dict(),
        "data_quality": {
            "status": "READY" if data_verified and len(completed) >= 60 else "LIMITED",
            "completed_minute_bars": len(completed),
            "indicator_warmup_ready": len(completed) >= 60,
            "orderbook_available": bool(quote.bid and quote.ask),
            "quote_source": quote.source,
        },
        "market_state": forecast_engine_diagnostics.get("market_state", "TRANSITION"),
        "direction_engines": forecast_engine_diagnostics.get("direction_engines", {}),
        "indicator_components": forecast_engine_diagnostics.get("components", {}),
        "direction_invalidation": {
            "risk_state": risk.state,
            "pattern_fatigue": persistence.swing.fatigue,
            "persistence_score": persistence.score,
        },
        "target_reachability": {
            str(minutes): {
                "target1": bool(target1 and forecast_by_minutes.get(minutes) and forecast_by_minutes[minutes].high >= target1),
                "target2": bool(target2 and forecast_by_minutes.get(minutes) and forecast_by_minutes[minutes].high >= target2),
            }
            for minutes in (5, 15, 30)
        },
        "calibration_probability_pct": calibration_probability if calibration_samples >= MIN_COMPLETE_PATH_SAMPLES else None,
        "calibration_expectancy_pct": calibration_expectancy_pct if calibration_samples >= MIN_COMPLETE_PATH_SAMPLES else None,
    }
    strategy_label = f"{ensemble.calibration_key} · {strategy}"
    return _plan(
        quote, now, signal, strategy_label, regime,
        entry=entry,
        entry_basis=entry_basis,
        target1=target1,
        target2=target2,
        stop=displayed_stop,
        target1_basis=target1_basis,
        target2_basis=target2_basis,
        stop_basis=stop_basis,
        score=score,
        reasons=reasons,
        missing=missing,
        repeat=box,
        verified=data_verified,
        diagnostics=diagnostics,
        forecasts=forecast_points,
        soft_stop=soft_stop,
        hard_stop=displayed_stop,
        risk_status=risk.state,
        persistence=persistence,
        calibration_probability=calibration_probability if calibration_samples >= MIN_COMPLETE_PATH_SAMPLES else None,
        calibration_samples=calibration_samples,
    )
