from __future__ import annotations

from datetime import datetime

import pandas as pd

from .forecast import forecast_path
from .indicators import enrich
from .models import Market, Quote, Regime, Signal, TradePlan
from .persistence_engine import final_buy_decision, persistence_score, risk_state
from .sessions import remaining_session_minutes
from .strategy import confirmed_levels, fake_signal_flags, multi_timeframe, price_zone_in_box, repeat_box, trade_levels
from .strategy_ensemble import evaluate_ensemble


ACTIVE_SESSIONS = {"KR_REGULAR", "US_PRE", "US_REGULAR", "US_AFTER"}


def _max_spread(quote: Quote) -> float:
    if quote.market == Market.KR:
        return 0.15
    return 0.25 if quote.session == "US_REGULAR" else 0.15


def _minimum_rvol(quote: Quote, regime: Regime) -> float:
    if quote.session in {"US_PRE", "US_AFTER"}:
        return 1.35
    return 0.80 if regime == Regime.RANGE else 1.00


def _minimum_notional_rvol(quote: Quote) -> float:
    return 1.20 if quote.session in {"US_PRE", "US_AFTER"} else 0.85


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
    entry = quote.ask or quote.price
    target1, target2, support, target1_basis, target2_basis, support_basis = trade_levels(df, entry, box)
    entry_resistance_1m, _, _, _ = confirmed_levels(df, entry)
    flags = fake_signal_flags(completed, support, entry_resistance_1m)
    risk = risk_state(df, current_price=quote.price, support=support, fake_breakdown=flags["fake_breakdown"])
    stop_basis = f"{support_basis} 기반 Hard Stop" if risk.hard_stop else "구조 무효화 기준 미확인"
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
    structure_ok = bool(target1 and target2 and risk.hard_stop and risk.hard_stop < entry < target1 < target2)
    if structure_ok:
        reward = float(target1) - entry - cost_amount
        risk_amount = entry - float(risk.hard_stop) + cost_amount
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
    )
    gates = dict(decision.gates)
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
    if calibration_samples < 30:
        reasons.append(f"보정확률 미표시: 동일 조건 실측 표본 {calibration_samples}건 / 30건")

    hard_block = regime == Regime.DOWN or not session_ok or risk.state in {"REAL_BREAKDOWN", "HARD_EXIT"} or hard_kill
    signal = Signal.BLOCK if hard_block else Signal.BUY if final_buy else Signal.WAIT
    diagnostics: dict[str, object] = {
        "timeframes": {minutes: state.regime.value for minutes, state in states.items()},
        "final_buy_gates": gates,
        "persistence": persistence.to_dict(),
        "risk": risk.to_dict(),
        "spread_pct": spread,
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
    }
    strategy_label = f"{ensemble.calibration_key} · {strategy}"
    return _plan(
        quote, now, signal, strategy_label, regime,
        entry=entry if structure_ok else None,
        target1=target1 if structure_ok else None,
        target2=target2 if structure_ok else None,
        stop=risk.hard_stop if structure_ok else None,
        target1_basis=target1_basis,
        target2_basis=target2_basis,
        stop_basis=stop_basis,
        score=score,
        reasons=reasons,
        missing=missing,
        repeat=box,
        verified=data_verified,
        diagnostics=diagnostics,
        forecasts=forecast_path(completed, regime),
        soft_stop=risk.soft_stop,
        hard_stop=risk.hard_stop,
        risk_status=risk.state,
        persistence=persistence,
        calibration_probability=calibration_probability if calibration_samples >= 30 else None,
        calibration_samples=calibration_samples,
    )
