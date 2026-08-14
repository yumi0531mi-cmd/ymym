from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import enrich
from .models import ForecastPoint, Regime


def forecast_path(frame: pd.DataFrame, regime: Regime) -> list[ForecastPoint]:
    """Transparent scenario range, not a guaranteed exact-price prediction."""
    df = enrich(frame)
    if len(df) < 30:
        return []
    recent = df.tail(30)
    price = float(recent.close.iloc[-1])
    returns = recent.close.pct_change().dropna()
    drift = float(returns.ewm(span=10, adjust=False).mean().iloc[-1])
    if regime == Regime.RANGE:
        drift *= .25
    elif regime == Regime.DOWN:
        drift = min(drift, 0)
    elif regime == Regime.UP:
        drift = max(drift, 0)
    sigma = max(float(returns.std(ddof=0)), 0.0003)
    result: list[ForecastPoint] = []
    for horizon in (5, 10, 15, 30):
        base = price * (1 + drift) ** horizon
        band = price * sigma * np.sqrt(horizon) * 1.15
        direction = Regime.UP if base > price * 1.0005 else Regime.DOWN if base < price * .9995 else Regime.RANGE
        result.append(ForecastPoint(horizon, max(0, base - band), base, base + band, direction))
    return result

