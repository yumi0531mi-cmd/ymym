from __future__ import annotations

from datetime import datetime

import pandas as pd

from .forecast import forecast_path
from .indicators import enrich
from .models import Market, Quote, Regime, Signal, TradePlan
from .strategy import confirmed_levels, multi_timeframe, repeat_box


def analyze(quote: Quote | None, bars: pd.DataFrame | None, orderbook_required: bool = True) -> TradePlan:
    now = datetime.now().astimezone()
    if quote is None or quote.price <= 0:
        return TradePlan("", Market.KR, now, Signal.UNVERIFIED, "없음", Regime.UNKNOWN, 0, None, None, None,
                         "현재가 없음", "현재가 없음", missing=["실시간 현재가"],
                         reasons=["현재가 미수신: 매매계획을 만들지 않음"])
    missing: list[str] = []
    if bars is None or len(bars) < 30:
        missing.append("충분한 1분봉")
    if orderbook_required and (not quote.bid or not quote.ask):
        missing.append("실시간 1호가")
    if bars is None or len(bars) < 30:
        return TradePlan(quote.symbol, quote.market, now, Signal.UNVERIFIED, "관찰", Regime.UNKNOWN, quote.price,
                         None, None, None, "분봉 부족", "분봉 부족", missing=missing,
                         reasons=["현재가만 표시 가능; 진입·목표·손절은 숨김"])

    df = enrich(bars)
    states = multi_timeframe(df)
    major = [states[m].regime for m in (60, 15, 5)]
    if major.count(Regime.UP) >= 2:
        regime, strategy = Regime.UP, "추세 눌림/돌파 후 재지지"
    elif major.count(Regime.DOWN) >= 2:
        regime, strategy = Regime.DOWN, "하락 추세 관찰"
    elif states[5].regime == Regime.RANGE:
        regime, strategy = Regime.RANGE, "박스 평균회귀"
    else:
        regime, strategy = Regime.TRANSITION, "전환 대기"

    target, stop, target_basis, stop_basis = confirmed_levels(df, quote.price)
    box = repeat_box(df, quote.price)
    latest = df.iloc[-1]
    spread = quote.spread_pct
    score = 0
    reasons: list[str] = []
    if regime in (Regime.UP, Regime.RANGE):
        score += 25
    else:
        reasons.append("5·15·60분 방향 합의 부족")
    if quote.price >= latest.vwap:
        score += 15
    else:
        reasons.append("VWAP 아래")
    if quote.price >= latest.ema9:
        score += 10
    else:
        reasons.append("EMA9 아래")
    if latest.rvol >= 1:
        score += 15
    else:
        reasons.append("상대거래량 부족")
    if target and stop and stop < quote.price < target:
        reward_risk = (target - quote.price) / (quote.price - stop)
        if reward_risk >= 1.4:
            score += 20
        else:
            reasons.append("구조적 손익비 부족")
    else:
        reward_risk = None
        reasons.append("확인된 지지·저항 부족")
    if spread is not None and spread <= (0.25 if quote.market == Market.US else 0.15):
        score += 15
    elif spread is None:
        reasons.append("호가 스프레드 미확인")
    else:
        reasons.append("스프레드 과다")

    data_verified = not missing
    buy_trigger = bool(regime in (Regime.UP, Regime.RANGE) and quote.price >= latest.vwap and
                       target and stop and reward_risk and reward_risk >= 1.4)
    signal = Signal.BUY if buy_trigger and data_verified else Signal.WAIT if data_verified else Signal.UNVERIFIED
    if regime == Regime.DOWN:
        signal = Signal.BLOCK
    entry = quote.ask if signal == Signal.BUY and quote.ask else quote.price if signal == Signal.BUY else None
    return TradePlan(quote.symbol, quote.market, now, signal, strategy, regime, quote.price, entry,
                     target, stop, target_basis, stop_basis, forecast_path(df, regime), score, reasons,
                     missing, box, data_verified,
                     {"timeframes": {m: s.regime.value for m, s in states.items()}, "spread_pct": spread,
                      "reward_risk": reward_risk, "vwap": float(latest.vwap), "ema9": float(latest.ema9),
                      "rvol": float(latest.rvol)})
