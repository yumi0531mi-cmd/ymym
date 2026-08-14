# -*- coding: utf-8 -*-
"""15-strategy intraday scanner engine.

Version 0.2.0
- 15 independent strategy families
- conflict separation
- broker-agnostic 1-minute structure engine
- 5/10/15/30 minute forecasts are calculated independently
- live quote/order-flow fields can overlay the latest completed candle

IMPORTANT:
- strategy score is an uncalibrated rule score, NOT a win rate.
- horizon forecast is a rule-based expected move estimate, NOT a guaranteed future price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable, Sequence

ENGINE_VERSION = "0.2.0"
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
    direction: str


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


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


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


def _ret(closes: Sequence[float], n: int) -> float:
    if len(closes) < 2:
        return 0.0
    idx = max(0, len(closes) - 1 - n)
    return _pct(closes[-1], closes[idx])


def _slope(values: Sequence[float], n: int) -> float:
    xs = list(values[-min(n, len(values)):])
    if len(xs) < 2 or xs[0] == 0:
        return 0.0
    return _pct(xs[-1], xs[0]) / max(len(xs) - 1, 1)


def _realized_vol(closes: Sequence[float], n: int) -> float:
    xs = list(closes[-min(n + 1, len(closes)):])
    if len(xs) < 3:
        return 0.0
    rs = [_pct(xs[i], xs[i - 1]) for i in range(1, len(xs)) if xs[i - 1] != 0]
    return pstdev(rs) if len(rs) >= 2 else 0.0


def compute_features(candles: Sequence[Candle], quote: dict | None = None) -> dict[str, float | str | bool]:
    if len(candles) < 8:
        raise ValueError("최소 8개의 1분봉이 필요합니다.")
    q = quote or {}
    closes = [c.close for c in candles]
    vols = [max(c.volume, 0.0) for c in candles]
    candle_close = closes[-1]
    live_current = _f(q.get("current"), 0.0)
    cur = live_current if live_current > 0 else candle_close
    e9, e20, e50 = ema(closes, 9), ema(closes, 20), ema(closes, 50)
    vw = vwap(candles)
    rs = rsi(closes)
    a = atr(candles)
    a_pct = (a / cur * 100.0) if cur > 0 else 0.0
    recent20 = candles[-min(20, len(candles)):]
    recent5 = candles[-min(5, len(candles)):]
    hi20, lo20 = max(c.high for c in recent20), min(c.low for c in recent20)
    hi5, lo5 = max(c.high for c in recent5), min(c.low for c in recent5)
    width20 = _pct(hi20, lo20) if lo20 else 0.0
    v5 = mean(vols[-5:]) if vols else 0.0
    v20 = mean(vols[-20:]) if vols else 0.0
    rvol = _f(q.get("rvol"), v5 / v20 if v20 > 0 else 1.0)
    spread_pct = _f(q.get("spread_pct"), 0.0)
    strength = _f(q.get("relative_strength"), 0.0)
    gap_pct = _f(q.get("gap_pct"), 0.0)
    event_score = _clip(_f(q.get("event_score"), 0.0), 0.0, 100.0)
    opening_high = _f(q.get("opening_high"), hi5)
    opening_low = _f(q.get("opening_low"), lo5)
    prev_high = _f(q.get("prev_high"), hi20)
    prev_close = _f(q.get("prev_close"), closes[0])
    turnover = _f(q.get("turnover"), 0.0)
    bid_ask_imbalance = _clip(_f(q.get("bid_ask_imbalance"), 0.0), -1.0, 1.0)
    trade_strength = _clip(_f(q.get("trade_strength"), 100.0), 0.0, 300.0)
    live_ret = _pct(cur, candle_close) if candle_close else 0.0
    return {
        "current": cur,
        "candle_close": candle_close,
        "live_ret": live_ret,
        "ema9": e9, "ema20": e20, "ema50": e50, "vwap": vw,
        "rsi": rs, "atr": a, "atr_pct": a_pct, "rvol": rvol,
        "ret1": _ret(closes, 1), "ret3": _ret(closes, 3), "ret5": _ret(closes, 5),
        "ret10": _ret(closes, 10), "ret15": _ret(closes, 15), "ret20": _ret(closes, 20),
        "slope3": _slope(closes, 3), "slope5": _slope(closes, 5), "slope10": _slope(closes, 10),
        "rv5": _realized_vol(closes, 5), "rv10": _realized_vol(closes, 10), "rv20": _realized_vol(closes, 20),
        "high20": hi20, "low20": lo20, "range20_pct": width20,
        "high5": hi5, "low5": lo5, "spread_pct": spread_pct,
        "relative_strength": strength, "gap_pct": gap_pct, "event_score": event_score,
        "opening_high": opening_high, "opening_low": opening_low, "prev_high": prev_high,
        "prev_close": prev_close, "turnover": turnover, "bid_ask_imbalance": bid_ask_imbalance,
        "trade_strength": trade_strength,
        "above_vwap": cur >= vw, "ema_bull": e9 >= e20 >= e50, "ema_bear": e9 <= e20 <= e50,
    }


def _sig(name: str, direction: str, score: float, reasons: Iterable[str]) -> StrategySignal:
    score = _clip(score, 0.0, 100.0)
    return StrategySignal(name, score >= 55.0, direction if score >= 55.0 else "NEUTRAL", score, list(reasons))


def evaluate_strategies(f: dict[str, float | str | bool]) -> list[StrategySignal]:
    c = float(f["current"]); e20 = float(f["ema20"]); vw = float(f["vwap"])
    rsi_v = float(f["rsi"]); rvol = float(f["rvol"]); atrp = float(f["atr_pct"])
    ret1 = float(f["ret1"]); ret5 = float(f["ret5"]); ret10 = float(f["ret10"]); slope5 = float(f["slope5"])
    hi20 = float(f["high20"]); lo20 = float(f["low20"]); width = float(f["range20_pct"])
    rel = float(f["relative_strength"]); gap = float(f["gap_pct"]); event = float(f["event_score"])
    oh = float(f["opening_high"]); ph = float(f["prev_high"])
    imb = float(f["bid_ask_imbalance"]); strength = float(f["trade_strength"])
    near_e20 = abs(_pct(c, e20)) <= max(0.25, atrp * 0.8) if e20 else False
    near_vw = abs(_pct(c, vw)) <= max(0.25, atrp * 0.8) if vw else False
    breakout = c >= max(ph, hi20 * 0.997)
    compression = width <= max(1.0, atrp * 5.0)
    overextended = _pct(c, vw) >= max(1.0, atrp * 2.5) if vw else False
    underextended = _pct(c, vw) <= -max(1.0, atrp * 2.5) if vw else False
    flow_buy = imb > 0.08 or strength >= 108
    flow_sell = imb < -0.08 or strength <= 92

    return [
        _sig("Trend Continuation", "LONG", 38 + 18*bool(f["ema_bull"]) + 14*bool(f["above_vwap"]) + 10*(ret10>0) + 8*(rvol>1.0) + 7*(rel>0) + 5*flow_buy, ["EMA 정배열", "VWAP 위", "중기 방향"]),
        _sig("Trend Pullback", "LONG", 34 + 18*bool(f["ema_bull"]) + 18*(near_e20 or near_vw) + 10*(ret10>0) + 10*(-0.7 <= ret5 <= 0.4) + 7*(rvol>=0.7) + 5*(not flow_sell), ["상승추세", "EMA/VWAP 눌림"]),
        _sig("Resistance Breakout", "LONG", 28 + 26*breakout + 16*(rvol>1.2) + 12*(c>vw) + 10*flow_buy + 8*(ret1>=0), ["저항/전고점 돌파", "거래량/체결 확인"]),
        _sig("Breakout-Retest", "LONG", 28 + 18*(c>=ph*0.995 if ph else False) + 18*(c>=vw) + 14*(ret10>0) + 10*(ret5>=-0.5) + 7*(rvol>=0.8) + 5*(not flow_sell), ["돌파가격 재지지", "VWAP 유지"]),
        _sig("Volatility Compression Breakout", "LONG", 28 + 24*compression + 20*(c>=hi20*0.995) + 14*(rvol>1.2) + 8*(slope5>0) + 6*flow_buy, ["변동성 압축", "상단 이탈"]),
        _sig("Failed Breakout Reversal", "SHORT", 24 + 24*(c<ph and max(ph, hi20)>0) + 18*(ret5<0) + 14*(rsi_v>62) + 10*(c<vw) + 8*flow_sell, ["돌파 실패", "VWAP 이탈"]),
        _sig("Range Swing", "LONG", 28 + 20*(width<=5.0) + 18*(c <= lo20 + (hi20-lo20)*0.35 if hi20>lo20 else False) + 12*(35<=rsi_v<=55) + 10*(rvol<1.5) + 6*(not flow_sell), ["박스 하단", "과도한 추세 아님"]),
        _sig("Mean Reversion", "LONG", 24 + 28*underextended + 18*(rsi_v<35) + 14*(ret5<0) + 10*(slope5>=-0.3) + 6*(not flow_sell), ["평균 과대이격", "과매도"]),
        _sig("Momentum Continuation", "LONG", 28 + 20*(ret5>0.3) + 18*(ret10>0.5) + 16*(rvol>1.3) + 10*(c>vw) + 6*(rel>0) + 8*flow_buy, ["가격 모멘텀", "거래량/체결 확장"]),
        _sig("Momentum Exhaustion Reversal", "SHORT", 24 + 24*overextended + 18*(rsi_v>72) + 14*(ret1<0) + 10*(rvol>1.5) + 8*flow_sell, ["과열", "모멘텀 소진"]),
        _sig("Gap Continuation", "LONG", 28 + 22*(gap>0.5) + 18*(c>vw) + 14*(ret5>0) + 10*(rvol>1.2) + 8*flow_buy, ["상승 갭", "갭 방향 지속"]),
        _sig("Gap Reversion", "SHORT", 28 + 22*(gap>0.8) + 18*(c<vw) + 14*(ret5<0) + 10*(rsi_v>60) + 8*flow_sell, ["갭 상승 후 약화", "갭 메움 가능"]),
        _sig("Opening Range Breakout", "LONG", 28 + 25*(c>oh) + 16*(rvol>1.2) + 12*(c>vw) + 8*(ret5>0) + 8*flow_buy, ["시초 범위 상단 돌파"]),
        _sig("Event Momentum", "LONG", 24 + 0.45*event + 14*(rvol>1.4) + 10*(ret5>0) + 6*flow_buy, ["이벤트 점수", "이벤트 후 거래 확장"]),
        _sig("Relative Strength / Leader-Laggard", "LONG", 34 + _clip(rel, -2.0, 2.0)*12 + 14*(ret10>0) + 10*(c>vw) + 8*(rvol>1.0) + 5*flow_buy, ["시장/섹터 대비 상대강도"]),
    ]


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


# Strategy relevance changes by forecast horizon. These are rule weights, not learned probabilities.
HORIZON_STRATEGY_WEIGHTS: dict[int, dict[str, float]] = {
    5: {
        "Resistance Breakout": 1.00, "Opening Range Breakout": 1.00, "Momentum Continuation": 1.00,
        "Breakout-Retest": 0.90, "Trend Pullback": 0.75, "Trend Continuation": 0.70,
        "Volatility Compression Breakout": 0.90, "Failed Breakout Reversal": 1.00,
        "Momentum Exhaustion Reversal": 1.00, "Gap Continuation": 0.90, "Gap Reversion": 0.90,
        "Range Swing": 0.65, "Mean Reversion": 0.70, "Event Momentum": 0.90,
        "Relative Strength / Leader-Laggard": 0.55,
    },
    10: {
        "Resistance Breakout": 0.95, "Opening Range Breakout": 0.90, "Momentum Continuation": 1.00,
        "Breakout-Retest": 1.00, "Trend Pullback": 0.95, "Trend Continuation": 0.90,
        "Volatility Compression Breakout": 1.00, "Failed Breakout Reversal": 0.95,
        "Momentum Exhaustion Reversal": 0.95, "Gap Continuation": 0.90, "Gap Reversion": 0.90,
        "Range Swing": 0.80, "Mean Reversion": 0.85, "Event Momentum": 0.90,
        "Relative Strength / Leader-Laggard": 0.70,
    },
    15: {
        "Resistance Breakout": 0.85, "Opening Range Breakout": 0.75, "Momentum Continuation": 0.90,
        "Breakout-Retest": 1.00, "Trend Pullback": 1.00, "Trend Continuation": 1.00,
        "Volatility Compression Breakout": 0.95, "Failed Breakout Reversal": 0.90,
        "Momentum Exhaustion Reversal": 0.90, "Gap Continuation": 0.85, "Gap Reversion": 0.90,
        "Range Swing": 0.90, "Mean Reversion": 0.90, "Event Momentum": 0.80,
        "Relative Strength / Leader-Laggard": 0.85,
    },
    30: {
        "Resistance Breakout": 0.70, "Opening Range Breakout": 0.55, "Momentum Continuation": 0.75,
        "Breakout-Retest": 0.90, "Trend Pullback": 0.95, "Trend Continuation": 1.00,
        "Volatility Compression Breakout": 0.80, "Failed Breakout Reversal": 0.90,
        "Momentum Exhaustion Reversal": 0.95, "Gap Continuation": 0.75, "Gap Reversion": 0.95,
        "Range Swing": 1.00, "Mean Reversion": 1.00, "Event Momentum": 0.70,
        "Relative Strength / Leader-Laggard": 1.00,
    },
}


def _strategy_edge(signals: Sequence[StrategySignal], minutes: int) -> float:
    weights = HORIZON_STRATEGY_WEIGHTS[minutes]
    total_w = 0.0
    signed = 0.0
    for s in signals:
        if not s.active:
            continue
        w = weights.get(s.name, 0.5) * max(0.15, (s.score - 50.0) / 50.0)
        if s.direction == "LONG":
            signed += w
        elif s.direction == "SHORT":
            signed -= w
        total_w += abs(w)
    return _clip(signed / total_w if total_w > 0 else 0.0, -1.0, 1.0)


def forecast_horizons(
    f: dict[str, float | str | bool], current: float, signals: Sequence[StrategySignal]
) -> list[Forecast]:
    """Calculate each horizon independently.

    No horizon is derived by multiplying another horizon. Each uses a different
    mixture of short/medium price action, strategy edge, VWAP/EMA structure,
    order-flow, relative strength and volatility.
    """
    atrp = max(0.03, float(f["atr_pct"]))
    flow = _clip(float(f["bid_ask_imbalance"]), -1.0, 1.0)
    strength = _clip((float(f["trade_strength"]) - 100.0) / 70.0, -1.0, 1.0)
    rel = _clip(float(f["relative_strength"]) / 2.0, -1.0, 1.0)
    vwap_bias = 1.0 if bool(f["above_vwap"]) else -1.0
    ema_bias = 1.0 if bool(f["ema_bull"]) else (-1.0 if bool(f["ema_bear"]) else 0.0)
    rsi_v = float(f["rsi"])
    exhaustion = _clip((rsi_v - 70.0) / 20.0, 0.0, 1.0) - _clip((30.0 - rsi_v) / 20.0, 0.0, 1.0)
    rvol_impulse = _clip((float(f["rvol"]) - 1.0) / 1.5, -0.5, 1.0)
    live_ret_norm = _clip(float(f.get("live_ret", 0.0)) / max(atrp, 0.08), -2.0, 2.0) / 2.0

    # Per-horizon independent price-action inputs.
    params = {
        5:  (0.25, 0.26, 0.12, 0.04, 0.13, 0.08, 0.04, 0.08, 0.02, 0.05),
        10: (0.12, 0.22, 0.20, 0.08, 0.12, 0.08, 0.06, 0.07, 0.03, 0.06),
        15: (0.07, 0.16, 0.22, 0.14, 0.10, 0.08, 0.08, 0.07, 0.05, 0.08),
        30: (0.03, 0.08, 0.18, 0.24, 0.06, 0.06, 0.12, 0.09, 0.07, 0.10),
    }
    # The first four components are normalized momentum readings for 1/3/5/10+ minute windows.
    raw_mom = {
        5:  (float(f["ret1"]), float(f["ret3"]), float(f["ret5"]), float(f["ret10"])),
        10: (float(f["ret1"]), float(f["ret3"]), float(f["ret5"]), float(f["ret10"])),
        15: (float(f["ret1"]), float(f["ret5"]), float(f["ret10"]), float(f["ret15"])),
        30: (float(f["ret3"]), float(f["ret10"]), float(f["ret15"]), float(f["ret20"])),
    }

    out: list[Forecast] = []
    for m in HORIZONS:
        p = params[m]
        mom = [_clip(x / max(atrp, 0.08), -2.5, 2.5) / 2.5 for x in raw_mom[m]]
        edge = _strategy_edge(signals, m)
        score = (
            p[0]*mom[0] + p[1]*mom[1] + p[2]*mom[2] + p[3]*mom[3]
            + p[4]*flow + p[5]*strength + p[6]*vwap_bias + p[7]*ema_bias
            + p[8]*rel + p[9]*edge
        )
        # Live tick displacement only affects short horizons strongly; completed 1m structure remains shared.
        score += live_ret_norm * {5: 0.10, 10: 0.06, 15: 0.03, 30: 0.01}[m]
        # Exhaustion opposes continuation; effect grows with horizon.
        score -= exhaustion * {5: 0.02, 10: 0.04, 15: 0.07, 30: 0.11}[m]
        # Volume expansion magnifies a direction only when there is already directional evidence.
        score *= 1.0 + 0.12 * max(0.0, rvol_impulse)
        score = _clip(score, -1.0, 1.0)

        # Expected move magnitude is volatility-bounded, not time-linear.
        vol_scale = {5: 0.55, 10: 0.78, 15: 0.96, 30: 1.35}[m]
        center = score * atrp * vol_scale
        # Keep rule estimate within a realistic multiple of recent 1-minute ATR.
        cap = max(0.12, atrp * {5: 0.90, 10: 1.20, 15: 1.55, 30: 2.20}[m])
        center = _clip(center, -cap, cap)
        rv = max(float(f[{5:"rv5",10:"rv10",15:"rv10",30:"rv20"}[m]]), atrp * 0.22)
        band = max(atrp * {5:0.35,10:0.50,15:0.62,30:0.85}[m], rv * sqrt(m/5.0) * 0.35)
        low, high = center - band, center + band
        direction = "UP" if center > 0.03 else ("DOWN" if center < -0.03 else "FLAT")
        out.append(Forecast(
            m, center, low, high,
            current*(1+low/100), current*(1+center/100), current*(1+high/100), direction,
        ))
    return out


def analyze(
    symbol: str, name: str, market: str, session: str, candles: Sequence[Candle],
    quote: dict | None = None, data_quality: str = "OK"
) -> ScanResult:
    f = compute_features(candles, quote)
    signals = evaluate_strategies(f)
    kept, conflicts = resolve_compatible(signals)
    regime = regime_from_features(f)
    current = float(f["current"])
    long_kept = [s for s in kept if s.direction == "LONG"]
    short_kept = [s for s in kept if s.direction == "SHORT"]
    primary = long_kept[0] if long_kept else (kept[0] if kept else StrategySignal("None", False, "NEUTRAL", 0.0, []))
    independent_scores = [s.score for s in long_kept[:4]]
    score = mean(independent_scores) if independent_scores else 0.0
    spread = float(f["spread_pct"])
    if spread > 0.35:
        score -= min(25.0, spread * 25.0)
    if short_kept and short_kept[0].score >= primary.score - 5:
        score -= 12.0
    score = _clip(score, 0.0, 100.0)

    forecasts = forecast_horizons(f, current, signals)
    f5 = next(x for x in forecasts if x.minutes == 5)
    f10 = next(x for x in forecasts if x.minutes == 10)
    f15 = next(x for x in forecasts if x.minutes == 15)
    atrp = max(float(f["atr_pct"]), 0.10)
    ext = _pct(current, float(f["vwap"])) if float(f["vwap"]) else 0.0
    chase = ext > max(0.9, atrp*2.2) or float(f["rsi"]) > 78
    near_term_positive = sum(x.center_pct > 0.05 for x in (f5, f10, f15)) >= 2
    near_term_negative = sum(x.center_pct < -0.05 for x in (f5, f10, f15)) >= 2

    if score >= 72 and near_term_positive and not chase and not near_term_negative and data_quality == "OK":
        decision = "🟢 진입"
    elif score >= 55 and max(f5.high_pct, f10.high_pct, f15.high_pct) > 0.12 and not near_term_negative:
        decision = "🟡 대기"
    else:
        decision = "🔴 금지"

    entry_pad = min(atrp*0.16, 0.22)
    entry_low = current*(1-entry_pad/100)
    entry_high = current*(1+entry_pad/100)
    positive_forecasts = [x.center_pct for x in forecasts if x.center_pct > 0]
    t1_pct = _clip(max(positive_forecasts[:3] or [0.12]), 0.12, max(0.22, atrp*0.75))
    t2_pct = _clip(max(positive_forecasts or [t1_pct+0.10]), t1_pct+0.08, max(0.38, atrp*1.35))
    soft = current*(1-max(0.18, atrp*0.70)/100)
    hard = current*(1-max(0.32, atrp*1.20)/100)

    return ScanResult(
        symbol=symbol, name=name, market=market, session=session, current=current, regime=regime,
        decision=decision, uncalibrated_score=round(score, 1), primary_strategy=primary.name,
        supporting_strategies=[s.name for s in long_kept[1:4]], conflicting_strategies=[s.name for s in conflicts],
        entry_low=entry_low, entry_high=entry_high, target1=current*(1+t1_pct/100), target2=current*(1+t2_pct/100),
        soft_stop=soft, hard_stop=hard, forecasts=forecasts, features=f, strategy_signals=signals,
        data_quality=data_quality,
    )
