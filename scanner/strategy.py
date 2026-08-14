from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import enrich, resample
from .models import Regime


@dataclass(slots=True)
class TimeframeState:
    minutes: int
    regime: Regime
    strength: float
    close: float
    ema9: float
    ema20: float


def classify(frame: pd.DataFrame, minutes: int) -> TimeframeState:
    df = enrich(resample(frame, minutes))
    if len(df) < 8:
        return TimeframeState(minutes, Regime.UNKNOWN, 0.0, float(df.close.iloc[-1]) if len(df) else 0.0, 0.0, 0.0)
    recent = df.tail(min(12, len(df)))
    close = float(recent.close.iloc[-1])
    x = np.arange(len(recent), dtype=float)
    slope = float(np.polyfit(x, recent.close.to_numpy(), 1)[0]) / max(close, 1e-9)
    price_range = float(recent.high.max() - recent.low.min()) / max(close, 1e-9)
    net = float(recent.close.iloc[-1] - recent.close.iloc[0]) / max(close, 1e-9)
    efficiency = abs(net) / max(price_range, 1e-9)
    ema9, ema20 = float(df.ema9.iloc[-1]), float(df.ema20.iloc[-1])
    if price_range > 0 and efficiency < 0.28:
        regime = Regime.RANGE
    elif slope > 0.00025 / max(minutes, 1) and close >= ema9 >= ema20:
        regime = Regime.UP
    elif slope < -0.00025 / max(minutes, 1) and close <= ema9 <= ema20:
        regime = Regime.DOWN
    else:
        regime = Regime.TRANSITION
    return TimeframeState(minutes, regime, min(1.0, abs(slope) * 1000 + efficiency), close, ema9, ema20)


def multi_timeframe(frame: pd.DataFrame) -> dict[int, TimeframeState]:
    return {m: classify(frame, m) for m in (1, 5, 15, 60)}


def confirmed_levels(frame: pd.DataFrame, current: float) -> tuple[float | None, float | None, str, str]:
    """Return only swing levels observed in completed bars; never invent percentage targets."""
    df = enrich(frame)
    if len(df) < 20:
        return None, None, "저항 미확인", "지지 미확인"
    completed = df.iloc[:-1].tail(120)
    highs = completed.high[(completed.high.shift(1) < completed.high) & (completed.high.shift(-1) < completed.high)]
    lows = completed.low[(completed.low.shift(1) > completed.low) & (completed.low.shift(-1) > completed.low)]
    resistance = sorted(float(v) for v in highs if v > current)
    support = sorted((float(v) for v in lows if v < current), reverse=True)
    return (
        resistance[0] if resistance else None,
        support[0] if support else None,
        "완료된 1분봉 스윙 고점" if resistance else "저항 미확인",
        "완료된 1분봉 스윙 저점" if support else "지지 미확인",
    )


def repeat_box(frame: pd.DataFrame, current: float) -> tuple[float, float] | None:
    df = enrich(resample(frame, 5))
    if len(df) < 8:
        return None
    recent = df.tail(12)
    low, high = float(recent.low.quantile(.2)), float(recent.high.quantile(.8))
    width = (high / low - 1) * 100 if low > 0 else 0
    touches_low = int((recent.low <= low * 1.002).sum())
    touches_high = int((recent.high >= high * .998).sum())
    if 0.5 <= width <= 3.0 and touches_low >= 2 and touches_high >= 2:
        return low, high
    return None
