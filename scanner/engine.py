from __future__ import annotations

from datetime import datetime

import pandas as pd

from .forecast import forecast_path
from .indicators import enrich
from .models import Market, Quote, Regime, Signal, TradePlan
from .strategy import confirmed_levels, multi_timeframe, repeat_box


ACTIVE_SESSIONS = {"KR_REGULAR", "US_PRE", "US_REGULAR", "US_AFTER"}


def _max_spread(quote: Quote) -> float:
    if quote.market == Market.KR:
        return 0.15
    return 0.25 if quote.session == "US_REGULAR" else 0.15


def _round_trip_cost_pct(market: Market) -> float:
    # Conservative editable defaults. Users can override the cost assumption in the app.
    return 0.05 if market == Market.KR else 0.10


def analyze(
    quote: Quote | None,
    bars: pd.DataFrame | None,
    orderbook_required: bool = True,
    round_trip_cost_pct: float | None = None,
    minimum_score: int = 85,
) -> TradePlan:
    """Create a conservative manual-trading plan from verified live data.

    A BUY signal requires every risk gate to pass. The result is research support,
    never an order instruction or a profit guarantee.
    """
    now = datetime.now().astimezone()
    if quote is None or quote.price <= 0:
        return TradePlan(
            "", Market.KR, now, Signal.UNVERIFIED, "없음", Regime.UNKNOWN, 0, None, None, None,
            "현재가 없음", "현재가 없음", missing=["실시간 현재가"],
            reasons=["현재가 미수신: 매매계획을 만들지 않음"],
        )

    missing: list[str] = []
    if bars is None or len(bars) < 31:
        missing.append("충분한 완료 1분봉")
    if orderbook_required and (not quote.bid or not quote.ask):
        missing.append("실시간 1호가")
    if quote.session not in ACTIVE_SESSIONS:
        missing.append("거래 가능 세션")
    if bars is None or len(bars) < 31:
        return TradePlan(
            quote.symbol, quote.market, now, Signal.UNVERIFIED, "관찰", Regime.UNKNOWN, quote.price,
            None, None, None, "분봉 부족", "분봉 부족", missing=missing,
            reasons=["현재가만 표시 가능; 완료 1분봉이 부족해 진입·목표·손절은 숨김"],
        )

    df = enrich(bars)
    completed = df.iloc[:-1].copy()
    if len(completed) < 30:
        return TradePlan(
            quote.symbol, quote.market, now, Signal.UNVERIFIED, "관찰", Regime.UNKNOWN, quote.price,
            None, None, None, "분봉 부족", "분봉 부족", missing=missing + ["완료 1분봉"],
            reasons=["진행 중인 마지막 1분봉을 제외하면 분석 데이터가 부족합니다."],
        )

    states = multi_timeframe(completed)
    major = [states[minutes].regime for minutes in (60, 15, 5)]
    if major.count(Regime.UP) >= 2:
        regime, strategy = Regime.UP, "추세 눌림/돌파 후 재지지"
    elif major.count(Regime.DOWN) >= 2:
        regime, strategy = Regime.DOWN, "하락 추세 관찰"
    elif states[5].regime == Regime.RANGE:
        regime, strategy = Regime.RANGE, "완료 5분봉 박스 평균회귀"
    else:
        regime, strategy = Regime.TRANSITION, "전환 대기"

    entry = quote.ask if quote.ask else quote.price
    target, stop, target_basis, stop_basis = confirmed_levels(df, entry)
    box = repeat_box(df, quote.price)
    latest = completed.iloc[-1]
    spread = quote.spread_pct
    max_spread = _max_spread(quote)
    cost_pct = _round_trip_cost_pct(quote.market) if round_trip_cost_pct is None else max(round_trip_cost_pct, 0.0)
    cost_amount = entry * cost_pct / 100

    score = 0
    reasons: list[str] = []
    trend_ok = regime in (Regime.UP, Regime.RANGE)
    if trend_ok:
        score += 25
    else:
        reasons.append("5·15·60분 방향 합의 부족")

    vwap_ok = quote.price >= float(latest.vwap)
    if vwap_ok:
        score += 15
    else:
        reasons.append("완료 1분봉 VWAP 아래")

    ema_ok = quote.price >= float(latest.ema9)
    if ema_ok:
        score += 10
    else:
        reasons.append("완료 1분봉 EMA9 아래")

    rvol_ok = float(latest.rvol) >= 1.0
    if rvol_ok:
        score += 15
    else:
        reasons.append("상대거래량 부족")

    reward_risk: float | None = None
    structure_ok = bool(target and stop and stop < entry < target)
    if structure_ok:
        reward = float(target) - entry - cost_amount
        risk = entry - float(stop) + cost_amount
        reward_risk = reward / risk if risk > 0 else None
        if reward_risk is not None and reward_risk >= 1.4:
            score += 20
        else:
            reasons.append("실제 진입가·왕복비용 기준 손익비 부족")
    else:
        reasons.append("실제 진입가 기준 지지·저항 구조 부족")

    spread_ok = spread is not None and spread <= max_spread
    if spread_ok:
        score += 15
    elif spread is None:
        reasons.append("호가 스프레드 미확인")
    else:
        reasons.append(f"스프레드 과다({spread:.3f}% > {max_spread:.3f}%)")

    session_ok = quote.session in ACTIVE_SESSIONS
    if not session_ok:
        reasons.append(f"거래 불가 세션: {quote.session}")

    data_verified = not missing
    gates = {
        "세션": session_ok,
        "추세": trend_ok,
        "VWAP": vwap_ok,
        "EMA9": ema_ok,
        "상대거래량": rvol_ok,
        "지지·저항": structure_ok,
        "손익비": reward_risk is not None and reward_risk >= 1.4,
        "스프레드": spread_ok,
        "최소점수": score >= minimum_score,
        "데이터": data_verified,
    }
    buy_trigger = all(gates.values())
    signal = Signal.BUY if buy_trigger else Signal.BLOCK if regime == Regime.DOWN or not session_ok else Signal.WAIT

    repeat_ready = False
    if box:
        box_low, box_high = box
        repeat_ready = quote.price <= box_low + (box_high - box_low) * 0.35
        if not repeat_ready:
            reasons.append("반복박스는 확인됐지만 현재가는 하단 진입 구간이 아님")

    return TradePlan(
        quote.symbol, quote.market, now, signal, strategy, regime, quote.price,
        entry if signal == Signal.BUY else None,
        target if signal == Signal.BUY else None,
        stop if signal == Signal.BUY else None,
        target_basis, stop_basis, forecast_path(completed, regime), score, reasons, missing, box, data_verified,
        {
            "timeframes": {minutes: state.regime.value for minutes, state in states.items()},
            "spread_pct": spread,
            "max_spread_pct": max_spread,
            "reward_risk_net": reward_risk,
            "vwap": float(latest.vwap),
            "ema9": float(latest.ema9),
            "rvol": float(latest.rvol),
            "round_trip_cost_pct": cost_pct,
            "minimum_score": minimum_score,
            "repeat_entry_ready": repeat_ready,
            "gates": gates,
            "completed_bar_at": str(completed.index[-1]),
        },
    )
