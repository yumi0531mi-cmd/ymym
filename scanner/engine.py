from __future__ import annotations

from datetime import datetime

import pandas as pd

from .forecast import forecast_path
from .indicators import enrich
from .models import Market, Quote, Regime, Signal, TradePlan
from .strategy import fake_signal_flags, multi_timeframe, price_zone_in_box, repeat_box, trade_levels


ACTIVE_SESSIONS = {"KR_REGULAR", "US_PRE", "US_REGULAR", "US_AFTER"}


def _max_spread(quote: Quote) -> float:
    if quote.market == Market.KR:
        return 0.15
    # Extended hours need a tighter ceiling because less liquid quotes can look tradable but be costly.
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
) -> TradePlan:
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
    )


def analyze(
    quote: Quote | None,
    bars: pd.DataFrame | None,
    orderbook_required: bool = True,
    round_trip_cost_pct: float | None = None,
    minimum_score: int = 80,
) -> TradePlan:
    """Create a read-only, explainable repeated-scalping plan from completed bars.

    The function never submits orders or treats score as a probability. A BUY label means
    only that the current completed-bar, liquidity and cost gates all passed.
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
            reasons=["완료 1분봉이 부족하여 진입·목표·손절 기준을 숨깁니다."], missing=missing,
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
        regime, strategy = Regime.UP, "상승 추세 눌림 반복단타"
    elif box:
        regime, strategy = Regime.RANGE, "박스 하단 평균회귀 반복단타"
    elif states[15].regime == Regime.DOWN and states[5].regime == Regime.DOWN:
        regime, strategy = Regime.DOWN, "하락 구조 관찰"
    else:
        regime, strategy = Regime.TRANSITION, "장세 전환 대기"

    latest = completed.iloc[-1]
    entry = quote.ask or quote.price
    target1, target2, support, target1_basis, target2_basis, support_basis = trade_levels(bars, entry, box)
    atr_buffer = max(float(latest.atr) * 0.25, entry * 0.0005)
    invalidation = support - atr_buffer if support else None
    stop_basis = f"{support_basis} - ATR 완충" if invalidation else "구조 무효화 기준 미확인"
    flags = fake_signal_flags(completed, support, target1)
    zone = price_zone_in_box(box, quote.price)
    spread = quote.spread_pct
    max_spread = _max_spread(quote)
    cost_pct = _round_trip_cost_pct(quote.market) if round_trip_cost_pct is None else max(round_trip_cost_pct, 0.0)
    cost_amount = entry * cost_pct / 100

    reward_risk: float | None = None
    structure_ok = bool(target1 and target2 and invalidation and invalidation < entry < target1 < target2)
    if structure_ok:
        reward = float(target1) - entry - cost_amount
        risk = entry - float(invalidation) + cost_amount
        reward_risk = reward / risk if risk > 0 else None

    session_ok = quote.session in ACTIVE_SESSIONS
    spread_ok = spread is not None and spread <= max_spread
    vwap_ok = quote.price >= float(latest.vwap)
    ema_ok = quote.price >= float(latest.ema9)
    rvol_threshold = _minimum_rvol(quote, regime)
    rvol_ok = float(latest.rvol) >= rvol_threshold
    notional_threshold = _minimum_notional_rvol(quote)
    notional_ok = float(latest.notional_rvol) >= notional_threshold
    rr_ok = reward_risk is not None and reward_risk >= 1.20
    range_entry_ok = bool(box and zone == "하단 진입 구간")
    trend_entry_ok = trend_confirmed and vwap_ok and ema_ok
    entry_zone_ok = trend_entry_ok or range_entry_ok
    false_signal_ok = not flags["fake_breakout"] and not flags["upper_rejection"] and not flags["two_close_breakdown"]
    data_verified = not missing

    score_parts = {
        "추세·박스 구조": 20 if regime in {Regime.UP, Regime.RANGE} else 0,
        "진입 위치": 15 if entry_zone_ok else 0,
        "VWAP·EMA": 10 if (vwap_ok and ema_ok) else 0,
        "상대거래량": 10 if rvol_ok else 0,
        "거래대금 상대강도": 10 if notional_ok else 0,
        "스프레드": 10 if spread_ok else 0,
        "순손익비": 15 if rr_ok else 0,
        "가짜신호 필터": 10 if false_signal_ok else 0,
    }
    score = int(sum(score_parts.values()))

    reasons: list[str] = []
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
        reasons.append("1차 목표 기준 비용 반영 순손익비가 1.20 미만입니다.")
    if flags["fake_breakout"]:
        reasons.append("가짜 돌파 경고: 저항 위 고가 뒤 종가가 저항 아래로 복귀했습니다.")
    if flags["upper_rejection"]:
        reasons.append("매도 압력 경고: 완료 1분봉의 윗꼬리가 길어 추격을 피합니다.")
    if flags["fake_breakdown"]:
        reasons.append("지지 이탈 후 회복: 즉시 손절 대신 다음 완료봉을 확인합니다.")
    if flags["two_close_breakdown"]:
        reasons.append("구조 무효화: 지지 아래에서 2개 완료 1분봉 종가가 연속 확인됐습니다.")
    if len(completed) < 300:
        reasons.append(f"5시간 검증 준비 중: 완료 1분봉 {len(completed)}개 / 300개")

    gates = {
        "세션": session_ok,
        "데이터": data_verified,
        "구조": structure_ok,
        "진입 위치": entry_zone_ok,
        "상대거래량": rvol_ok,
        "거래대금": notional_ok,
        "스프레드": spread_ok,
        "순손익비": rr_ok,
        "가짜신호": false_signal_ok,
        "최소점수": score >= minimum_score,
    }
    hard_block = regime == Regime.DOWN or not session_ok or flags["two_close_breakdown"]
    signal = Signal.BLOCK if hard_block else Signal.BUY if all(gates.values()) else Signal.WAIT

    diagnostics: dict[str, object] = {
        "timeframes": {minutes: state.regime.value for minutes, state in states.items()},
        "quality_score_parts": score_parts,
        "gates": gates,
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
        "box_zone": zone,
        "repeat_entry_ready": range_entry_ok,
        "false_signal_flags": flags,
        "completed_bars": len(completed),
        "five_hour_data_ready": len(completed) >= 300,
        "completed_bar_at": str(completed.index[-1]),
    }
    return _plan(
        quote, now, signal, strategy, regime,
        entry=entry if structure_ok else None,
        target1=target1 if structure_ok else None,
        target2=target2 if structure_ok else None,
        stop=invalidation if structure_ok else None,
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
    )
