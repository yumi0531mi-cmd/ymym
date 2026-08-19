from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import enrich
from .models import ForecastPoint, Regime


def _bound(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ForecastPath(list[ForecastPoint]):
    """Forecast points with inspectable state and per-horizon direction-engine details."""

    def __init__(self, points: list[ForecastPoint], diagnostics: dict[str, object]):
        super().__init__(points)
        self.diagnostics = diagnostics


HORIZON_WEIGHTS: dict[int, dict[str, dict[str, float]]] = {
    5: {
        "TREND": {"structure": 0.25, "trend": 0.20, "flow": 0.30, "momentum": 0.25},
        "RANGE": {"structure": 0.25, "trend": 0.10, "flow": 0.20, "momentum": 0.45},
        "BREAKOUT": {"structure": 0.25, "trend": 0.20, "flow": 0.35, "momentum": 0.20},
        "TRANSITION": {"structure": 0.30, "trend": 0.25, "flow": 0.20, "momentum": 0.25},
    },
    15: {
        "TREND": {"structure": 0.35, "trend": 0.30, "flow": 0.20, "momentum": 0.15},
        "RANGE": {"structure": 0.35, "trend": 0.15, "flow": 0.20, "momentum": 0.30},
        "BREAKOUT": {"structure": 0.30, "trend": 0.30, "flow": 0.25, "momentum": 0.15},
        "TRANSITION": {"structure": 0.35, "trend": 0.30, "flow": 0.20, "momentum": 0.15},
    },
    30: {
        "TREND": {"structure": 0.40, "trend": 0.35, "flow": 0.15, "momentum": 0.10},
        "RANGE": {"structure": 0.40, "trend": 0.20, "flow": 0.15, "momentum": 0.25},
        "BREAKOUT": {"structure": 0.35, "trend": 0.35, "flow": 0.20, "momentum": 0.10},
        "TRANSITION": {"structure": 0.40, "trend": 0.30, "flow": 0.15, "momentum": 0.15},
    },
}


def _signed(value: float, scale: float = 1.0) -> float:
    return _bound(value / max(scale, 1e-9), -1.0, 1.0)


def _market_state(df: pd.DataFrame, regime: Regime, flow_score: float) -> str:
    latest = df.iloc[-1]
    previous_high = float(df.high.tail(21).iloc[:-1].max()) if len(df) > 1 else float(latest.high)
    breakout = bool(
        len(df) >= 20
        and float(latest.close) >= previous_high * 0.998
        and flow_score >= 0.10
        and float(latest.close) >= float(latest.vwap)
    )
    if breakout:
        return "BREAKOUT"
    if regime == Regime.RANGE:
        return "RANGE"
    if regime in {Regime.UP, Regime.DOWN}:
        return "TREND"
    return "TRANSITION"


def _components(latest: pd.Series, regime: Regime, state: str, flow_score: float) -> dict[str, float]:
    price = float(latest.close)
    vwap_component = 0.35 if price >= float(latest.vwap) else -0.35
    ema_component = 0.35 if float(latest.ema9) >= float(latest.ema20) else -0.35
    slope_component = _signed(float(latest.ema9_slope) * 600.0 + float(latest.regression_slope) * 700.0)
    di_component = _signed(float(latest.plus_di) - float(latest.minus_di), 25.0)
    trend = _bound(vwap_component + ema_component + slope_component * 0.20 + di_component * 0.10, -1.0, 1.0)
    structure = 0.65 if regime == Regime.UP else -0.65 if regime == Regime.DOWN else 0.0
    if state == "RANGE":
        percent_b = float(latest.boll_pct_b)
        structure = _bound((0.5 - percent_b) * 1.6, -0.65, 0.65)
    flow = _bound(flow_score * 0.65 + _signed(float(latest.cmf), 0.20) * 0.20 + _signed(float(latest.roc10), 1.0) * 0.15, -1.0, 1.0)
    stoch_cross = _signed(float(latest.stoch_k) - float(latest.stoch_d), 20.0)
    stoch_slope = _signed(float(latest.stoch_k) - 50.0, 45.0)
    rsi = _signed(float(latest.rsi) - 50.0, 32.0)
    macd = _signed(float(latest.macd_hist), max(float(latest.atr) * 0.30, 1e-9))
    momentum = _bound(stoch_cross * 0.45 + stoch_slope * 0.20 + rsi * 0.20 + macd * 0.15, -1.0, 1.0)
    if state == "RANGE":
        momentum = _bound(stoch_cross * 0.50 + _signed(50.0 - float(latest.stoch_k), 45.0) * 0.25 + rsi * 0.25, -1.0, 1.0)
    return {"structure": structure, "trend": trend, "flow": flow, "momentum": momentum}


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


def _common_range_context(recent: pd.DataFrame, price: float) -> dict[str, object]:
    """Compute the shared chart inputs once before horizon-specific weighting.

    The 5·15·30-minute engines use different weights, but must start from one
    completed-bar volatility, VWAP/EMA, flow and structural context. This avoids
    incompatible price paths caused by three unrelated percentage formulas.
    """
    latest = recent.iloc[-1]
    returns = recent.close.pct_change().dropna()
    atr_fraction = max(float(latest.atr) / max(float(latest.close), 1e-9), 0.0003)
    realized_sigma = max(
        float(returns.std(ddof=0)), atr_fraction * 0.45,
        float(latest.boll_width_pct) / 100 * 0.20, 0.0003,
    )
    rvol_component = _bound(float(latest.rvol) - 1.0, -1.0, 1.5)
    notional_component = _bound(float(latest.notional_rvol) - 1.0, -1.0, 1.5)
    return {
        "latest": latest,
        "price": price,
        "atr_fraction": atr_fraction,
        "realized_sigma": realized_sigma,
        "flow_score": (rvol_component + notional_component) / 2.0,
    }


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
    progress = {5: 0.35, 15: 0.75, 30: 1.00}
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
        result.append(ForecastPoint(
            point.minutes, max(0.0, low), base, high, direction, f"{point.basis} · {label}",
            point.direction_confidence_pct, ceiling,
        ))
    return result


def cap_downside_forecast_path(
    points: list[ForecastPoint], reference_price: float, primary_support: float | None,
) -> list[ForecastPoint]:
    """Keep downside representative prices above the nearest observed support.

    A statistical downside range may extend farther, but a nearby confirmed support
    is the first realistic landing area unless a later risk engine confirms a
    structural breakdown.
    """
    if reference_price <= 0 or primary_support is None or not 0 < primary_support < reference_price:
        return points
    progress = {5: 0.35, 15: 0.75, 30: 1.00}
    result: list[ForecastPoint] = []
    for point in points:
        if point.direction != Regime.DOWN:
            result.append(point)
            continue
        horizon_floor = reference_price + (float(primary_support) - reference_price) * progress.get(point.minutes, 1.0)
        base = max(float(point.base), horizon_floor)
        low = max(min(base, float(point.low)), float(primary_support))
        high = max(float(point.high), base)
        direction = Regime.DOWN if base < reference_price * 0.9995 else Regime.RANGE
        result.append(ForecastPoint(
            point.minutes, low, base, high, direction, f"{point.basis} · 기본 구조 지지 하한",
            point.direction_confidence_pct, float(primary_support),
        ))
    return result


def apply_risk_persistence_to_forecast(
    points: list[ForecastPoint],
    reference_price: float,
    risk_state: str,
    persistence_score: int | None,
    fatigue: int,
) -> list[ForecastPoint]:
    """Invalidate breakdowns and damp longer paths when the repeated pattern is tired.

    A confirmed real breakdown is not allowed to retain a stale upside forecast. A
    shakeout is handled as near-term uncertainty rather than an automatic breakdown.
    Pattern fatigue and low persistence pull farther-horizon price paths back toward
    the reference more strongly than the five-minute path.
    """
    if reference_price <= 0:
        return points
    if risk_state in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        return [
            ForecastPoint(
                point.minutes,
                max(0.0, min(point.low, reference_price * 0.985)),
                min(point.base, reference_price * (0.996 if point.minutes == 5 else 0.990)),
                min(point.high, reference_price * 0.999),
                Regime.DOWN,
                f"{point.basis} · {risk_state} 방향 무효화",
                point.direction_confidence_pct,
                point.structure_level,
            )
            for point in points
        ]

    horizon_damping = {5: 0.96, 15: 0.72, 30: 0.55}
    fatigue_factor = max(0.0, min(float(fatigue) / 55.0, 0.75))
    persistence_factor = max(0.0, min((65.0 - float(persistence_score or 65)) / 65.0, 0.45))
    result: list[ForecastPoint] = []
    for point in points:
        damping = 1.0 - (fatigue_factor + persistence_factor) * (1.0 - horizon_damping.get(point.minutes, 0.55))
        if risk_state == "SHAKEOUT" and point.minutes == 5:
            damping *= 0.35
        base = reference_price + (float(point.base) - reference_price) * damping
        high = reference_price + (float(point.high) - reference_price) * damping
        low = reference_price + (float(point.low) - reference_price) * damping
        direction = point.direction
        if risk_state == "SHAKEOUT" and point.minutes == 5:
            direction = Regime.RANGE
        elif abs(base / reference_price - 1.0) < 0.0005:
            direction = Regime.RANGE
        result.append(ForecastPoint(
            point.minutes, max(0.0, low), base, max(base, high), direction,
            f"{point.basis} · {risk_state}·지속성 보정", point.direction_confidence_pct, point.structure_level,
        ))
    return result


def forecast_path(
    frame: pd.DataFrame,
    regime: Regime,
    reference_price: float | None = None,
) -> list[ForecastPoint]:
    """Return horizon-specific direction scenarios from completed-chart conditions.

    Each horizon has a separate structure·trend·flow·momentum weighting. This avoids
    turning one short-lived minute-bar drift into a mechanically compounded 30-minute
    target. The result remains a scenario, not a promise.
    """
    df = enrich(frame)
    if len(df) < 30:
        return []
    recent = df.tail(30)
    latest = recent.iloc[-1]
    price = float(reference_price or latest.close)
    context = _common_range_context(recent, price)
    atr_fraction = float(context["atr_fraction"])
    sigma = float(context["realized_sigma"])
    flow_score = float(context["flow_score"])

    state = _market_state(recent, regime, flow_score)
    components = _components(latest, regime, state, flow_score)
    basis = _direction_basis(latest, regime, flow_score)
    result: list[ForecastPoint] = []
    diagnostics: dict[str, object] = {"market_state": state, "components": components, "direction_engines": {}}
    horizon_scale = {5: 0.55, 15: 1.05, 30: 1.35}
    for horizon in (5, 15, 30):
        weights = HORIZON_WEIGHTS[horizon][state]
        score = sum(components[key] * weight for key, weight in weights.items())
        if state == "TRANSITION":
            score *= 0.55
        expected_move = score * max(sigma, atr_fraction * 0.55) * horizon_scale[horizon]
        band = price * max(sigma * np.sqrt(horizon) * 0.72, atr_fraction * np.sqrt(horizon) * 0.42)
        base = price * (1 + expected_move)
        direction = Regime.UP if score >= 0.16 else Regime.DOWN if score <= -0.16 else Regime.RANGE
        confidence = _bound(50.0 + abs(score) * 27.0 + max(flow_score, 0.0) * 6.0, 50.0, 82.0)
        result.append(ForecastPoint(
            horizon, max(0, base - band), base, base + band, direction,
            f"{horizon}분 {state} Direction Engine · {basis}", round(confidence, 1), None,
        ))
        diagnostics["direction_engines"][str(horizon)] = {
            "state": state,
            "score": round(float(score), 4),
            "weights": weights,
            "components": {key: round(float(value), 4) for key, value in components.items()},
            "expected_move_pct": round(float(expected_move) * 100, 4),
            "move_range_pct": round(float(band / max(price, 1e-9)) * 100, 4),
            "direction_confidence_pct": round(float(confidence), 1),
            "common_context": {
                "realized_sigma_pct": round(float(sigma) * 100, 4),
                "atr_pct": round(float(atr_fraction) * 100, 4),
                "flow_score": round(float(flow_score), 4),
            },
        }
    return ForecastPath(result, diagnostics)
