from __future__ import annotations

from datetime import datetime

import pandas as pd

from .calibration import MIN_COMPLETE_PATH_SAMPLES
from .forecast import forecast_path
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
    trend_confirmed = states[15].regime == Regime.UP and states[5].regime == Regime.UP
    box = repeat_box(df, quote.price)
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
    forecast_points = forecast_path(completed, regime, reference_price=quote.price)
    forecast_by_minutes = {point.minutes: point for point in forecast_points}
    # A card may present an upward entry/target ladder only when every displayed
    # 5/10/15/30-minute forecast is upward and its base estimate is above live price.
    # A single downward horizon turns the card into observation-only rather than
    # mixing a bearish forecast with an upward price recommendation.
    has_downward_forecast = any(point.direction == Regime.DOWN for point in forecast_points)
    forecast_path_ready = {point.minutes for point in forecast_points} == {5, 10, 15, 30}
    long_price_path_confirmed = forecast_path_ready and all(
        point.direction == Regime.UP and float(point.base) > quote.price
        for point in forecast_points
    )
    # A forecast is never allowed to become an upside target below the intended entry.
    # If completed five-minute resistance is unavailable, display no target instead of
    # presenting a directionally invalid price level.
    forecast_target1 = forecast_by_minutes.get(5)
    if target1 is None and forecast_target1 is not None and float(forecast_target1.base) > entry:
        target1 = float(forecast_target1.base)
        target1_basis = "5분 완료봉 모멘텀·VWAP·EMA·거래량·거래대금·ATR 계산"
    forecast_target2 = forecast_by_minutes.get(15)
    minimum_target2 = float(target1) if target1 is not None else entry
    if target2 is None and forecast_target2 is not None and float(forecast_target2.base) > minimum_target2:
        target2 = float(forecast_target2.base)
        target2_basis = "15분 완료봉 모멘텀·VWAP·EMA·거래량·거래대금·ATR 계산"
    # A structural target that has already been passed by the live quote cannot guide
    # a fresh entry. Likewise, any downward forecast disables upward price guidance.
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
            target1_basis, target2_basis = "현재가 위 1차 목표 재확인 중", "현재가 위 2차 목표 재확인 중"
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
    zone = price_zone_in_box(box, quote.price)
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
    )
    gates = dict(decision.gates)
    gates["5·10·15·30분 상방 경로"] = long_price_path_confirmed
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
        reasons.append("5·10·15·30분 예상에 하방 경로가 있어 상승 가격 추천을 표시하지 않습니다.")
    elif not forecast_path_ready:
        reasons.append("5·10·15·30분 방향을 계산할 완료 분봉이 아직 충분하지 않습니다.")
    elif not long_price_path_confirmed:
        reasons.append("5·10·15·30분 상방 경로가 모두 확인되기 전에는 상승 가격 추천을 표시하지 않습니다.")
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
        "reward_risk_net": reward_risk,
        "price_structure_valid": structure_ok,
        "long_price_path_confirmed": long_price_path_confirmed,
        "has_downward_forecast": has_downward_forecast,
        "forecast_path_ready": forecast_path_ready,
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
        "strategy_ensemble": ensemble.to_dict(),
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
