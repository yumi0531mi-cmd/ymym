from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import enrich
from .models import ForecastPoint, Regime


def _bound(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _direction_basis(latest: pd.Series, regime: Regime, flow_score: float) -> str:
    price = float(latest.close)
    ema_up = price >= float(latest.ema9) >= float(latest.ema20)
    ema_down = price <= float(latest.ema9) <= float(latest.ema20)
    vwap_up = price >= float(latest.vwap)
    vwap_down = price < float(latest.vwap)
    flow = "거래량·거래대금 강화" if flow_score >= 0.15 else "거래량·거래대금 보통" if flow_score >= -0.10 else "거래량·거래대금 약화"
    if regime == Regime.UP or ema_up and vwap_up:
        return f"완료봉 EMA9·EMA20 상승 정렬 · VWAP 위 · {flow}"
    if regime == Regime.DOWN or ema_down and vwap_down:
        return f"완료봉 EMA9·EMA20 하락 정렬 · VWAP 아래 · {flow}"
    return f"완료봉 박스·전환 구조 · {flow}"


def cap_upside_forecast_path(
    points: list[ForecastPoint],
    reference_price: float,
    primary_resistance: float | None,
    next_resistance: float | None,
    extension_confirmed: bool,
) -> list[ForecastPoint]:
    """Bound upside scenarios by observed structure, extending only after confirmation.

    The five-percent ceiling is a safety ceiling for the whole 30-minute path, not a
    required return. A smaller observed resistance always wins.
    """
    if reference_price <= 0:
        return points
    chosen_cap = next_resistance if extension_confirmed and next_resistance else primary_resistance
    ceiling = reference_price * 1.05
    if chosen_cap is not None and chosen_cap > reference_price:
        ceiling = min(float(chosen_cap), ceiling)
    progress = {5: 0.35, 10: 0.55, 15: 0.75, 30: 1.00}
    label = (
        "다음 저항 확장" if extension_confirmed and next_resistance
        else "기본 구조 저항 상한" if chosen_cap is not None and chosen_cap > reference_price
        else "30분 단타 상한"
    )
    result: list[ForecastPoint] = []
    for point in points:
        if point.direction != Regime.UP:
            result.append(point)
            continue
        horizon_cap = reference_price + (ceiling - reference_price) * progress.get(point.minutes, 1.0)
        base = min(float(point.base), horizon_cap)
        high = min(max(base, float(point.high)), ceiling)
        low = min(float(point.low), base)
        direction = Regime.UP if base > reference_price * 1.0005 else Regime.RANGE
        result.append(ForecastPoint(point.minutes, max(0.0, low), base, high, direction, f"{point.basis} · {label}"))
    return result


def forecast_path(
    frame: pd.DataFrame,
    regime: Regime,
    reference_price: float | None = None,
) -> list[ForecastPoint]:
    """Return transparent 5·15·30 minute price scenarios from completed-chart conditions.

    The base is a scenario price, not a promise.  It combines completed-bar momentum,
    EMA/VWAP trend alignment, relative volume, relative 5-minute turnover and ATR.
    """
    df = enrich(frame)
    if len(df) < 30:
        return []
    recent = df.tail(30)
    latest = recent.iloc[-1]
    price = float(reference_price or latest.close)
    returns = recent.close.pct_change().dropna()
    fast_drift = float(returns.tail(5).mean())
    slow_drift = float(returns.ewm(span=10, adjust=False).mean().iloc[-1])
    momentum = fast_drift * 0.55 + slow_drift * 0.45
    atr_fraction = max(float(latest.atr) / max(float(latest.close), 1e-9), 0.0003)
    rvol_component = _bound(float(latest.rvol) - 1.0, -1.0, 1.5)
    notional_component = _bound(float(latest.notional_rvol) - 1.0, -1.0, 1.5)
    flow_score = (rvol_component + notional_component) / 2.0

    ema_up = float(latest.close) >= float(latest.ema9) >= float(latest.ema20)
    ema_down = float(latest.close) <= float(latest.ema9) <= float(latest.ema20)
    vwap_up = float(latest.close) >= float(latest.vwap)
    vwap_down = float(latest.close) < float(latest.vwap)
    if regime == Regime.UP or ema_up and vwap_up:
        alignment = 1.0
    elif regime == Regime.DOWN or ema_down and vwap_down:
        alignment = -1.0
    else:
        alignment = 0.0

    # ATR constrains the chart-based directional adjustment. Stronger confirmed flow
    # increases the adjustment, while weak flow narrows it rather than inventing trend.
    directional_adjustment = alignment * atr_fraction * 0.18 * (1.0 + _bound(flow_score, -0.5, 1.0))
    drift = momentum + directional_adjustment
    if alignment > 0:
        drift = max(drift, 0.0)
    elif alignment < 0:
        drift = min(drift, 0.0)
    else:
        drift *= 0.35

    sigma = max(float(returns.std(ddof=0)), atr_fraction * 0.45, 0.0003)
    basis = _direction_basis(latest, regime, flow_score)
    result: list[ForecastPoint] = []
    # 10분 값은 사후 경로 검증에만 유지한다. 화면의 목표 카드는 5·15·30분만 표시한다.
    for horizon in (5, 10, 15, 30):
        base = price * (1 + drift) ** horizon
        band = price * sigma * np.sqrt(horizon) * 1.15
        direction = Regime.UP if base > price * 1.0005 else Regime.DOWN if base < price * 0.9995 else Regime.RANGE
        result.append(ForecastPoint(horizon, max(0, base - band), base, base + band, direction, basis))
    return result
