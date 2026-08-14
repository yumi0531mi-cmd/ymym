# -*- coding: utf-8 -*-
"""15-strategy intraday scanner engine.

This module is deliberately broker-agnostic. It consumes 1-minute candles and
returns uncalibrated rule scores, not win probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable, Sequence

ENGINE_VERSION = "0.1.0"
HORIZONS = (5, 10, 15, 30)


@dataclass(frozen=True)
class Candle:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class StrategySignal:
    name: str
    active: bool
    direction: str  # LONG / SHORT / NEUTRAL
    score: float    # 0..100, uncalibrated rule score
    reasons: list[str] = field(default_factory=list)


@dataclass
class Forecast:
    minutes: int
    center_pct: float
    low_pct: float
    high_pct: float
    low_price: float
    center_price: float
    high_price: float


@dataclass
class ScanResult:
    symbol: str
    name: str
    market: str
    session: str
    current: float
    regime: str
    decision: str
    uncalibrated_score: float
    primary_strategy: str
    supporting_strategies: list[str]
    conflicting_strategies: list[str]
    entry_low: float
    entry_high: float
    target1: float
    target2: float
    soft_stop: float
    hard_stop: float
    forecasts: list[Forecast]
    features: dict[str, float | str | bool]
    strategy_signals: list[StrategySignal]
    data_quality: str
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def _f(v: float | int | str | None, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    out = float(values[0])
    for value in values[1:]:
        out = alpha * float(value) + (1.0 - alpha) * out
    return out


def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = diffs[-period:]
    gains = sum(max(d, 0.0) for d in recent) / max(len(recent), 1)
    losses = sum(max(-d, 0.0) for d in recent) / max(len(recent), 1)
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if not candles:
        return 0.0
    trs: list[float] = []
    prev_close = candles[0].close
    for c in candles:
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
        prev_close = c.close
    return mean(trs[-period:]) if trs else 0.0


def vwap(candles: Sequence[Candle]) -> float:
    vol = sum(max(c.volume, 0.0) for c in candles)
    if vol <= 0:
        return candles[-1].close if candles else 0.0
    return sum(((c.high + c.low + c.close) / 3.0) * max(c.volume, 0.0) for c in candles) / vol


def _pct(a: float, b: float) -> float:
    return ((a / b) - 1.0) * 100.0 if b else 0.0


def _slope(values: Sequence[float], n: int) -> float:
    if len(values) < 2:
        return 0.0
    xs = list(values[-min(n, len(values)):])
    if len(xs) < 2 or xs[0] == 0:
        return 0.0
    return _pct(xs[-1], xs[0]) / max(len(xs) - 1, 1)


def _recent_swing(candles: Sequence[Candle], lookback: int = 20) -> tuple[float, float, float]:
    xs = candles[-min(lookback, len(candles)):]
    if not xs:
        return 0.0, 0.0, 0.0
    hi = max(c.high for c in xs)
    lo = min(c.low for c in xs)
    cur = xs[-1].close
    width = _pct(hi, lo) if lo else 0.0
    pos = (cur - lo) / (hi - lo) if hi > lo else 0.5
    return hi, lo, max(0.0, width)


def compute_features(candles: Sequence[Candle], quote: dict | None = None) -> dict[str, float | str | bool]:
    if len(candles) < 8:
        raise ValueError("최소 8개의 1분봉이 필요합니다.")
    q = quote or {}
    closes = [c.close for c in candles]
    vols = [max(c.volume, 0.0) for c in candles]
    cur = closes[-1]
    e9, e20, e50 = ema(closes, 9), ema(closes, 20), ema(closes, 50)
    vw = vwap(candles)
    a = atr(candles, 14)
    a_pct = (a / cur * 100.0) if cur else 0.0
    rs = rsi(closes, 14)
    hi20, lo20, width20 = _recent_swing(candles, 20)
    hi5 = max(c.high for c in candles[-5:])
    lo5 = min(c.low for c in candles[-5:])
    v5 = mean(vols[-5:]) if vols else 0.0
    v20 = mean(vols[-20:]) if vols else 0.0
    rvol = v5 / v20 if v20 > 0 else 1.0
    ret1 = _pct(closes[-1], closes[-2]) if len(closes) >= 2 else 0.0
    ret5 = _pct(closes[-1], closes[-6]) if len(closes) >= 6 else _pct(closes[-1], closes[0])
    ret10 = _pct(closes[-1], closes[-11]) if len(closes) >= 11 else _pct(closes[-1], closes[0])
    spread_pct = _f(q.get("spread_pct"), 0.0)
    strength = _f(q.get("relative_strength"), 0.0)
    gap_pct = _f(q.get("gap_pct"), 0.0)
    event_score = max(0.0, min(100.0, _f(q.get("event_score"), 0.0)))
    opening_high = _f(q.get("opening_high"), hi5)
    opening_low = _f(q.get("opening_low"), lo5)
    prev_high = _f(q.get("prev_high"), hi20)
    prev_close = _f(q.get("prev_close"), closes[0])
    turnover = _f(q.get("turnover"), 0.0)
    bid_ask_imbalance = _f(q.get("bid_ask_imbalance"), 0.0)
    return {
        "current": cur, "ema9": e9, "ema20": e20, "ema50": e50, "vwap": vw,
        "rsi": rs, "atr": a, "atr_pct": a_pct, "rvol": rvol,
        "ret1": ret1, "ret5": ret5, "ret10": ret10,
        "slope5": _slope(closes, 5), "slope10": _slope(closes, 10),
        "high20": hi20, "low20": lo20, "range20_pct": width20,
        "high5": hi5, "low5": lo5, "spread_pct": spread_pct,
        "relative_strength": strength, "gap_pct": gap_pct, "event_score": event_score,
        "opening_high": opening_high, "opening_low": opening_low, "prev_high": prev_high,
        "prev_close": prev_close, "turnover": turnover, "bid_ask_imbalance": bid_ask_imbalance,
        "above_vwap": cur >= vw, "ema_bull": e9 >= e20 >= e50,
        "ema_bear": e9 <= e20 <= e50,
    }


def _sig(name: str, direction: str, score: float, reasons: Iterable[str]) -> StrategySignal:
    score = max(0.0, min(100.0, float(score)))
    return StrategySignal(name, score >= 55.0, direction if score >= 55.0 else "NEUTRAL", score, list(reasons))


def evaluate_strategies(f: dict[str, float | str | bool]) -> list[StrategySignal]:
    c = float(f["current"]); e9 = float(f["ema9"]); e20 = float(f["ema20"]); vw = float(f["vwap"])
    rsi_v = float(f["rsi"]); rvol = float(f["rvol"]); atrp = float(f["atr_pct"])
    ret1 = float(f["ret1"]); ret5 = float(f["ret5"]); ret10 = float(f["ret10"]); slope5 = float(f["slope5"])
    hi20 = float(f["high20"]); lo20 = float(f["low20"]); width = float(f["range20_pct"])
    rel = float(f["relative_strength"]); gap = float(f["gap_pct"]); event = float(f["event_score"])
    oh = float(f["opening_high"]); ol = float(f["opening_low"]); ph = float(f["prev_high"])
    imb = float(f["bid_ask_imbalance"])
    near_e20 = abs(_pct(c, e20)) <= max(0.25, atrp * 0.8) if e20 else False
    near_vw = abs(_pct(c, vw)) <= max(0.25, atrp * 0.8) if vw else False
    breakout = c >= max(ph, hi20 * 0.997)
    compression = width <= max(1.0, atrp * 5.0)
    overextended = _pct(c, vw) >= max(1.0, atrp * 2.5) if vw else False
    underextended = _pct(c, vw) <= -max(1.0, atrp * 2.5) if vw else False

    signals = [
        _sig("Trend Continuation", "LONG", 40 + 18*bool(f["ema_bull"]) + 14*bool(f["above_vwap"]) + 10*(ret10>0) + 8*(rvol>1.0) + 8*(rel>0), ["EMA 정배열", "VWAP 위", "중기 방향"]),
        _sig("Trend Pullback", "LONG", 35 + 18*bool(f["ema_bull"]) + 18*(near_e20 or near_vw) + 10*(ret10>0) + 10*(-0.6 <= ret5 <= 0.4) + 9*(rvol>=0.7), ["상승추세", "EMA/VWAP 눌림"]),
        _sig("Resistance Breakout", "LONG", 30 + 26*breakout + 16*(rvol>1.2) + 12*(c>vw) + 8*(imb>=0), ["저항/전고점 돌파", "거래량 확인"]),
        _sig("Breakout-Retest", "LONG", 30 + 18*(c>=ph*0.995 if ph else False) + 18*(c>=vw) + 14*(ret10>0) + 10*(ret5>=-0.5) + 10*(rvol>=0.8), ["돌파가격 재지지", "VWAP 유지"]),
        _sig("Volatility Compression Breakout", "LONG", 30 + 24*compression + 20*(c>=hi20*0.995) + 14*(rvol>1.2) + 8*(slope5>0), ["변동성 압축", "상단 이탈"]),
        _sig("Failed Breakout Reversal", "SHORT", 25 + 24*(c<ph and max(x for x in [ph, hi20])>0) + 18*(ret5<0) + 14*(rsi_v>62) + 10*(c<vw), ["돌파 실패", "VWAP 이탈"]),
        _sig("Range Swing", "LONG", 30 + 20*(width<=5.0) + 18*(c <= lo20 + (hi20-lo20)*0.35 if hi20>lo20 else False) + 12*(35<=rsi_v<=55) + 10*(rvol<1.5), ["박스 하단", "과도한 추세 아님"]),
        _sig("Mean Reversion", "LONG", 25 + 28*underextended + 18*(rsi_v<35) + 14*(ret5<0) + 10*(slope5>=-0.3), ["평균 과대이격", "과매도"]),
        _sig("Momentum Continuation", "LONG", 30 + 20*(ret5>0.3) + 18*(ret10>0.5) + 16*(rvol>1.3) + 10*(c>vw) + 6*(rel>0), ["가격 모멘텀", "거래량 확장"]),
        _sig("Momentum Exhaustion Reversal", "SHORT", 25 + 24*overextended + 18*(rsi_v>72) + 14*(ret1<0) + 10*(rvol>1.5), ["과열", "모멘텀 소진"]),
        _sig("Gap Continuation", "LONG", 30 + 22*(gap>0.5) + 18*(c>vw) + 14*(ret5>0) + 10*(rvol>1.2), ["상승 갭", "갭 방향 지속"]),
        _sig("Gap Reversion", "SHORT", 30 + 22*(gap>0.8) + 18*(c<vw) + 14*(ret5<0) + 10*(rsi_v>60), ["갭 상승 후 약화", "갭 메움 가능"]),
        _sig("Opening Range Breakout", "LONG", 30 + 25*(c>oh) + 16*(rvol>1.2) + 12*(c>vw) + 8*(ret5>0), ["시초 범위 상단 돌파"]),
        _sig("Event Momentum", "LONG", 25 + 0.45*event + 14*(rvol>1.4) + 10*(ret5>0), ["이벤트 점수", "이벤트 후 거래 확장"]),
        _sig("Relative Strength / Leader-Laggard", "LONG", 35 + min(max(rel, -2.0), 2.0)*12 + 14*(ret10>0) + 10*(c>vw) + 8*(rvol>1.0), ["시장/섹터 대비 상대강도"]),
    ]
    return signals


CONFLICT_PAIRS = {
    frozenset(("Resistance Breakout", "Failed Breakout Reversal")),
    frozenset(("Momentum Continuation", "Momentum Exhaustion Reversal")),
    frozenset(("Gap Continuation", "Gap Reversion")),
    frozenset(("Trend Continuation", "Range Swing")),
    frozenset(("Trend Pullback", "Mean Reversion")),
}


def regime_from_features(f: dict[str, float | str | bool]) -> str:
    width = float(f["range20_pct"]); ret10 = float(f["ret10"]); rvol = float(f["rvol"])
    if bool(f["ema_bull"]) and ret10 > 0.25:
        return "UP_TREND"
    if bool(f["ema_bear"]) and ret10 < -0.25:
        return "DOWN_TREND"
    if width <= max(1.0, float(f["atr_pct"]) * 5.0):
        return "COMPRESSION"
    if width <= 4.5 and rvol < 1.5:
        return "RANGE"
    return "MIXED"


def resolve_compatible(signals: Sequence[StrategySignal]) -> tuple[list[StrategySignal], list[StrategySignal]]:
    active = sorted([s for s in signals if s.active], key=lambda s: s.score, reverse=True)
    kept: list[StrategySignal] = []
    conflicts: list[StrategySignal] = []
    for sig in active:
        if any(frozenset((sig.name, k.name)) in CONFLICT_PAIRS for k in kept):
            conflicts.append(sig)
        else:
            kept.append(sig)
    return kept, conflicts


def forecast_horizons(f: dict[str, float | str | bool], current: float) -> list[Forecast]:
    # Rule-based directional range, explicitly NOT a calibrated probability forecast.
    drift = 0.40*float(f["slope5"]) + 0.25*(float(f["ret10"]) / 10.0) + 0.08*float(f["relative_strength"])
    if bool(f["above_vwap"]): drift += 0.025
    else: drift -= 0.025
    drift += max(-0.06, min(0.06, (float(f["rvol"]) - 1.0) * 0.03))
    atrp = max(0.05, float(f["atr_pct"]))
    out: list[Forecast] = []
    for m in HORIZONS:
        scale = sqrt(m / 5.0)
        center = drift * m
        band = atrp * 0.75 * scale
        low, high = center - band, center + band
        out.append(Forecast(m, center, low, high,
                            current*(1+low/100), current*(1+center/100), current*(1+high/100)))
    return out


def analyze(symbol: str, name: str, market: str, session: str, candles: Sequence[Candle], quote: dict | None = None, data_quality: str = "OK") -> ScanResult:
    f = compute_features(candles, quote)
    signals = evaluate_strategies(f)
    kept, conflicts = resolve_compatible(signals)
    regime = regime_from_features(f)
    current = float(f["current"])
    long_kept = [s for s in kept if s.direction == "LONG"]
    primary = long_kept[0] if long_kept else (kept[0] if kept else StrategySignal("None", False, "NEUTRAL", 0.0, []))
    independent_scores = [s.score for s in long_kept[:4]]
    score = mean(independent_scores) if independent_scores else 0.0
    # Penalize poor liquidity / contradictory evidence; score remains uncalibrated.
    spread = float(f["spread_pct"])
    if spread > 0.35: score -= min(25.0, spread * 25.0)
    if any(c.direction == "SHORT" and c.score >= primary.score - 5 for c in conflicts): score -= 12.0
    score = max(0.0, min(100.0, score))
    forecasts = forecast_horizons(f, current)
    f15 = next(x for x in forecasts if x.minutes == 15)
    atrp = max(float(f["atr_pct"]), 0.15)
    # Entry remains near current price; do not chase if excessively extended above VWAP.
    ext = _pct(current, float(f["vwap"])) if float(f["vwap"]) else 0.0
    chase = ext > max(0.9, atrp*2.2) or float(f["rsi"]) > 78
    if score >= 72 and f15.center_pct > 0.10 and not chase and data_quality == "OK":
        decision = "🟢 진입"
    elif score >= 55 and f15.high_pct > 0.15:
        decision = "🟡 대기"
    else:
        decision = "🔴 금지"
    entry_pad = min(atrp*0.18, 0.25)
    entry_low = current*(1-entry_pad/100)
    entry_high = current*(1+entry_pad/100)
    t1_pct = max(0.15, min(max(f15.center_pct, 0.0), max(0.25, atrp*0.8)))
    f30 = next(x for x in forecasts if x.minutes == 30)
    t2_pct = max(t1_pct+0.10, min(max(f30.center_pct, t1_pct+0.10), max(0.45, atrp*1.5)))
    soft = current*(1-max(0.20, atrp*0.75)/100)
    hard = current*(1-max(0.35, atrp*1.25)/100)
    return ScanResult(
        symbol=symbol, name=name, market=market, session=session, current=current, regime=regime,
        decision=decision, uncalibrated_score=round(score, 1), primary_strategy=primary.name,
        supporting_strategies=[s.name for s in long_kept[1:4]], conflicting_strategies=[s.name for s in conflicts],
        entry_low=entry_low, entry_high=entry_high, target1=current*(1+t1_pct/100), target2=current*(1+t2_pct/100),
        soft_stop=soft, hard_stop=hard, forecasts=forecasts, features=f, strategy_signals=signals,
        data_quality=data_quality,
    )
