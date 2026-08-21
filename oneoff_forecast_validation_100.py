from __future__ import annotations

"""One-off live audit for the scanner's 5/15/30-minute forecast path.

Purpose
-------
Run once against the current KIS-backed scanner engine, freeze 100 forecast cases,
then compare each forecast with later 1-minute KIS bars.  This file does not change
or replace any strategy logic and never sends orders.

The audit intentionally uses the existing scanner engine:
- candidate source: KIS volume TOP100 + turnover TOP100 union
- forecast source: scanner.engine.analyze -> plan.forecasts
- market data source: scanner.kis_client.KISClient only

Outputs are written under ``validation_data/oneoff_forecast_100``.
"""

import argparse
import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError
from scanner.market_screener import MarketCandidate, merge_rankings
from scanner.models import Market, Regime

APP_AUDIT_VERSION = "oneoff-forecast-100-v1"
HORIZONS = (5, 15, 30)
OUT_ROOT = Path("validation_data") / "oneoff_forecast_100"
PRICE_CEILING = {Market.KR: 300_000.0, Market.US: 200.0}
MAX_DAILY_RISE_PCT = 12.0
DIRECTION_EPSILON = 0.0005


@dataclass
class FrozenForecast:
    symbol: str
    name: str
    market: str
    exchange: str
    signal_time: str
    origin_price: float
    session: str
    strategy: str
    regime: str
    score: int
    risk_state: str
    persistence_score: int | None
    predictions: dict[str, dict[str, Any]]


@dataclass
class ScoredHorizon:
    symbol: str
    market: str
    exchange: str
    signal_time: str
    horizon_minutes: int
    origin_price: float
    predicted_low: float
    predicted_base: float
    predicted_high: float
    predicted_direction: str
    actual_price: float | None
    actual_bar_time: str | None
    actual_direction: str | None
    direction_hit: bool | None
    range_hit: bool | None
    abs_error_pct: float | None
    baseline_abs_error_pct: float | None
    valid: bool
    invalid_reason: str


def _direction(origin: float, actual: float) -> str:
    if actual > origin * (1.0 + DIRECTION_EPSILON):
        return Regime.UP.value
    if actual < origin * (1.0 - DIRECTION_EPSILON):
        return Regime.DOWN.value
    return Regime.RANGE.value


def _safe_error_pct(predicted: float, actual: float) -> float | None:
    if actual <= 0 or not math.isfinite(predicted) or not math.isfinite(actual):
        return None
    return abs(predicted / actual - 1.0) * 100.0


def _candidate_allowed(candidate: MarketCandidate) -> bool:
    if candidate.price is not None and candidate.price > PRICE_CEILING[candidate.market]:
        return False
    if candidate.change_pct is not None and candidate.change_pct > MAX_DAILY_RISE_PCT:
        return False
    return True


def _forecast_dict(plan: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for point in getattr(plan, "forecasts", []):
        if point.minutes not in HORIZONS:
            continue
        result[str(point.minutes)] = {
            "low": float(point.low),
            "base": float(point.base),
            "high": float(point.high),
            "direction": point.direction.value,
            "basis": str(point.basis),
        }
    return result


def freeze_one(client: KISClient, candidate: MarketCandidate) -> FrozenForecast | None:
    exchange = candidate.exchange or ("NAS" if candidate.market == Market.US else "")
    quote = client.quote(candidate.symbol, candidate.market, exchange, include_orderbook=False)
    if quote.price <= 0 or quote.price > PRICE_CEILING[candidate.market]:
        return None
    if quote.change_pct > MAX_DAILY_RISE_PCT:
        return None
    bars = client.intraday(candidate.symbol, candidate.market, exchange)
    if bars.empty or len(bars) < 31:
        return None
    plan = analyze(quote, bars, orderbook_required=False)
    predictions = _forecast_dict(plan)
    if set(predictions) != {"5", "15", "30"}:
        return None
    if not bool(plan.diagnostics.get("forecast_path_ready")):
        return None
    return FrozenForecast(
        symbol=candidate.symbol,
        name=candidate.name,
        market=candidate.market.value,
        exchange=exchange,
        signal_time=plan.created_at.isoformat(),
        origin_price=float(quote.price),
        session=quote.session,
        strategy=plan.strategy,
        regime=plan.regime.value,
        score=int(plan.score),
        risk_state=plan.risk_state,
        persistence_score=plan.persistence_score,
        predictions=predictions,
    )


def _actual_from_future_bars(
    bars: pd.DataFrame,
    signal_time: str,
    horizon_minutes: int,
) -> tuple[float | None, str | None, str]:
    if bars.empty:
        return None, None, "future bars empty"
    frame = bars.sort_index().copy()
    signal_at = pd.Timestamp(signal_time)
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is None and signal_at.tzinfo is not None:
        signal_at = signal_at.tz_localize(None)
    elif getattr(index, "tz", None) is not None and signal_at.tzinfo is None:
        signal_at = signal_at.tz_localize(index.tz)
    target = signal_at + pd.Timedelta(horizon_minutes, unit="min")
    eligible = frame.loc[(index > signal_at) & (index <= target)]
    if eligible.empty:
        return None, None, "no future bar before cutoff"
    at = pd.Timestamp(eligible.index[-1])
    lateness = target - at
    if lateness > pd.Timedelta(minutes=2):
        return None, at.isoformat(), f"nearest bar too old by {lateness.total_seconds():.0f}s"
    price = float(eligible.close.iloc[-1])
    if price <= 0 or not math.isfinite(price):
        return None, at.isoformat(), "invalid actual price"
    return price, at.isoformat(), ""


def score_case(case: FrozenForecast, future_bars: pd.DataFrame) -> list[ScoredHorizon]:
    rows: list[ScoredHorizon] = []
    for horizon in HORIZONS:
        prediction = case.predictions[str(horizon)]
        actual, actual_at, reason = _actual_from_future_bars(future_bars, case.signal_time, horizon)
        if actual is None:
            rows.append(
                ScoredHorizon(
                    symbol=case.symbol, market=case.market, exchange=case.exchange,
                    signal_time=case.signal_time, horizon_minutes=horizon,
                    origin_price=case.origin_price,
                    predicted_low=float(prediction["low"]), predicted_base=float(prediction["base"]),
                    predicted_high=float(prediction["high"]), predicted_direction=str(prediction["direction"]),
                    actual_price=None, actual_bar_time=actual_at, actual_direction=None,
                    direction_hit=None, range_hit=None, abs_error_pct=None,
                    baseline_abs_error_pct=None, valid=False, invalid_reason=reason,
                )
            )
            continue
        actual_direction = _direction(case.origin_price, actual)
        rows.append(
            ScoredHorizon(
                symbol=case.symbol, market=case.market, exchange=case.exchange,
                signal_time=case.signal_time, horizon_minutes=horizon,
                origin_price=case.origin_price,
                predicted_low=float(prediction["low"]), predicted_base=float(prediction["base"]),
                predicted_high=float(prediction["high"]), predicted_direction=str(prediction["direction"]),
                actual_price=actual, actual_bar_time=actual_at, actual_direction=actual_direction,
                direction_hit=actual_direction == str(prediction["direction"]),
                range_hit=float(prediction["low"]) <= actual <= float(prediction["high"]),
                abs_error_pct=_safe_error_pct(float(prediction["base"]), actual),
                baseline_abs_error_pct=_safe_error_pct(case.origin_price, actual),
                valid=True, invalid_reason="",
            )
        )
    return rows


def summarize(rows: list[ScoredHorizon], requested_cases: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "version": APP_AUDIT_VERSION,
        "requested_cases": requested_cases,
        "scored_rows": len(rows),
        "horizons": {},
    }
    for horizon in HORIZONS:
        subset = [row for row in rows if row.horizon_minutes == horizon and row.valid]
        errors = [row.abs_error_pct for row in subset if row.abs_error_pct is not None]
        baselines = [row.baseline_abs_error_pct for row in subset if row.baseline_abs_error_pct is not None]
        direction_hits = [row.direction_hit for row in subset if row.direction_hit is not None]
        range_hits = [row.range_hit for row in subset if row.range_hit is not None]
        mean_error = sum(errors) / len(errors) if errors else None
        mean_baseline = sum(baselines) / len(baselines) if baselines else None
        summary["horizons"][str(horizon)] = {
            "valid_samples": len(subset),
            "direction_accuracy_pct": (sum(bool(v) for v in direction_hits) / len(direction_hits) * 100.0) if direction_hits else None,
            "forecast_range_hit_pct": (sum(bool(v) for v in range_hits) / len(range_hits) * 100.0) if range_hits else None,
            "mean_abs_price_error_pct": mean_error,
            "median_abs_price_error_pct": float(pd.Series(errors).median()) if errors else None,
            "within_0_5pct_pct": (sum(v <= 0.5 for v in errors) / len(errors) * 100.0) if errors else None,
            "within_1_0pct_pct": (sum(v <= 1.0 for v in errors) / len(errors) * 100.0) if errors else None,
            "no_change_baseline_mean_abs_error_pct": mean_baseline,
            "forecast_beats_no_change_baseline": (
                mean_error < mean_baseline if mean_error is not None and mean_baseline is not None else None
            ),
        }
    return summary


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[ScoredHorizon]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(ScoredHorizon.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def collect_cases(client: KISClient, market: Market, sample_count: int) -> list[FrozenForecast]:
    rankings = client.market_rankings(market, limit=100)
    candidates = [item for item in merge_rankings(market, rankings, limit=200) if _candidate_allowed(item)]
    cases: list[FrozenForecast] = []
    failures: list[str] = []
    for candidate in candidates:
        if len(cases) >= sample_count:
            break
        try:
            case = freeze_one(client, candidate)
        except (KISError, ValueError, KeyError) as exc:
            failures.append(f"{candidate.symbol}: {type(exc).__name__}: {exc}")
            continue
        if case is None:
            failures.append(f"{candidate.symbol}: forecast path unavailable")
            continue
        cases.append(case)
        print(f"freeze {len(cases):03d}/{sample_count} {case.market} {case.symbol} {case.signal_time}")
    if failures:
        _write_json(OUT_ROOT / "collection_failures.json", failures)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="One-off 100-symbol 5/15/30m KIS forecast audit")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    requested = max(1, min(int(args.samples), 100))
    market = Market(args.market)
    client = KISClient()
    if not client.ready:
        raise SystemExit("KIS_APP_KEY/KIS_APP_SECRET 및 유효 토큰 또는 자동 토큰 발급 설정이 필요합니다.")

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    cases = collect_cases(client, market, requested)
    _write_json(OUT_ROOT / "frozen_forecasts.json", [asdict(case) for case in cases])
    if len(cases) < requested:
        raise SystemExit(f"유효 예측 사례가 {len(cases)}/{requested}개뿐입니다. 결과를 채점하지 않습니다.")

    latest_signal = max(datetime.fromisoformat(case.signal_time) for case in cases)
    score_after = latest_signal + timedelta(minutes=31)
    while datetime.now().astimezone() < score_after:
        remaining = (score_after - datetime.now().astimezone()).total_seconds()
        print(f"future-bar wait: {max(0, int(remaining))}s")
        time.sleep(min(max(5, int(args.poll_seconds)), max(5, remaining)))

    scored: list[ScoredHorizon] = []
    for index, case in enumerate(cases, start=1):
        try:
            bars = client.intraday(case.symbol, Market(case.market), case.exchange)
            scored.extend(score_case(case, bars))
            print(f"score {index:03d}/{len(cases)} {case.market} {case.symbol}")
        except (KISError, ValueError, KeyError) as exc:
            for horizon in HORIZONS:
                prediction = case.predictions[str(horizon)]
                scored.append(
                    ScoredHorizon(
                        symbol=case.symbol, market=case.market, exchange=case.exchange,
                        signal_time=case.signal_time, horizon_minutes=horizon,
                        origin_price=case.origin_price,
                        predicted_low=float(prediction["low"]), predicted_base=float(prediction["base"]),
                        predicted_high=float(prediction["high"]), predicted_direction=str(prediction["direction"]),
                        actual_price=None, actual_bar_time=None, actual_direction=None,
                        direction_hit=None, range_hit=None, abs_error_pct=None,
                        baseline_abs_error_pct=None, valid=False,
                        invalid_reason=f"{type(exc).__name__}: {exc}",
                    )
                )

    _write_csv(OUT_ROOT / "results.csv", scored)
    summary = summarize(scored, requested)
    summary["market"] = market.value
    summary["frozen_cases"] = len(cases)
    summary["completed_at"] = datetime.now().astimezone().isoformat()
    _write_json(OUT_ROOT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
