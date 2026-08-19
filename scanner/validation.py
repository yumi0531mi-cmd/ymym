from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Regime, TradePlan
from .persistence import EventStore, PersistenceError


@dataclass
class HorizonResult:
    minutes: int
    predicted_low: float
    predicted_base: float
    predicted_high: float
    predicted_direction: str
    actual: float | None = None
    range_pass: bool | None = None
    direction_pass: bool | None = None
    pass_all: bool | None = None
    price_error_pct: float | None = None
    representative_reached: bool | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None


@dataclass
class ValidationCase:
    case_id: str
    version: str
    symbol: str
    market: str
    session: str
    signal_time: str
    signal: str
    quote_price: float
    latest_trade_price: float | None
    quote_age_seconds: float | None
    quote_pass: bool | None
    entry: float | None
    predicted_regime: str
    actual_regime: str | None
    regime_pass: bool | None
    target: float | None
    target_basis: str
    stop: float | None
    soft_stop: float | None = None
    hard_stop: float | None = None
    target_first: bool | None = None
    soft_stop_first: bool | None = None
    hard_stop_first: bool | None = None
    stopped_first: bool | None = None
    target_pass: bool | None = None
    horizons: list[HorizonResult] = field(default_factory=list)
    full_path_pass: bool | None = None
    complete_four_area_pass: bool | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    net_return_pct: float | None = None
    missing: list[str] = field(default_factory=list)
    strategy: str = ""
    target2: float | None = None
    target2_basis: str = ""
    invalidation: float | None = None
    score: int = 0
    risk_state: str = ""
    persistence_score: int | None = None
    latest_trade_time: str | None = None
    orderbook_available: bool | None = None
    spread_pct: float | None = None
    entry_executable: bool | None = None
    structural_target_confirmed: bool | None = None
    data_completeness: str = "PENDING"
    target_outcome: str | None = None
    validation_kind: str = "ACTIONABLE"
    forecast_path_direction: str = "MIXED"
    price_source: str = "KIS 체결"
    price_snapshots: list[dict[str, Any]] = field(default_factory=list)
    capture_failures: dict[str, dict[str, str]] = field(default_factory=dict)
    exchange: str = ""
    batch_id: str = ""

    @classmethod
    def from_plan(
        cls,
        plan: TradePlan,
        latest_trade_price: float | None,
        session: str,
        version: str = "1.0.0",
        latest_trade_time: datetime | None = None,
        validation_kind: str = "ACTIONABLE",
        price_source: str = "KIS 체결",
        exchange: str = "",
        batch_id: str = "",
    ):
        directions = {point.direction.value for point in plan.forecasts}
        forecast_path_direction = directions.pop() if len(directions) == 1 else "MIXED"
        case_id = f"{plan.market.value}-{plan.symbol}-{plan.created_at.strftime('%Y%m%dT%H%M%S%f')}-{validation_kind.lower()}"
        tick_tolerance = max(plan.current_price * 0.0002, 0.01 if plan.market.value == "US" else 1)
        quote_pass = None if latest_trade_price is None else abs(plan.current_price - latest_trade_price) <= tick_tolerance
        quote_age = None if latest_trade_time is None else max(0.0, (plan.created_at - latest_trade_time).total_seconds())
        orderbook_available = bool(plan.diagnostics.get("bid") or plan.diagnostics.get("ask"))
        if not orderbook_available:
            orderbook_available = bool(plan.diagnostics.get("spread_pct") is not None)
        spread_pct = plan.diagnostics.get("spread_pct")
        structural_target_confirmed = any(token in (plan.target_basis or "") for token in ("스윙", "반복박스", "저항"))
        entry_executable = bool(quote_pass is True and orderbook_available and plan.entry and plan.stop)
        horizons = [HorizonResult(point.minutes, point.low, point.base, point.high, point.direction.value) for point in plan.forecasts]
        price_snapshots = (
            [{"timestamp": latest_trade_time.isoformat(), "price": float(latest_trade_price), "source": price_source}]
            if latest_trade_time is not None and latest_trade_price is not None
            else []
        )
        return cls(
            case_id=case_id,
            version=version,
            symbol=plan.symbol,
            market=plan.market.value,
            session=session,
            signal_time=plan.created_at.isoformat(),
            signal=plan.signal.value,
            quote_price=plan.current_price,
            latest_trade_price=latest_trade_price,
            quote_age_seconds=quote_age,
            quote_pass=quote_pass,
            entry=plan.entry,
            predicted_regime=plan.regime.value,
            actual_regime=None,
            regime_pass=None,
            target=plan.target,
            target_basis=plan.target_basis,
            stop=plan.hard_stop or plan.stop,
            soft_stop=plan.soft_stop,
            hard_stop=plan.hard_stop or plan.stop,
            horizons=horizons,
            missing=list(plan.missing),
            strategy=plan.strategy,
            target2=plan.target2,
            target2_basis=plan.target2_basis,
            invalidation=plan.invalidation,
            score=plan.score,
            risk_state=plan.risk_state,
            persistence_score=plan.persistence_score,
            latest_trade_time=latest_trade_time.isoformat() if latest_trade_time else None,
            orderbook_available=orderbook_available,
            spread_pct=float(spread_pct) if isinstance(spread_pct, (int, float)) else None,
            entry_executable=entry_executable,
            structural_target_confirmed=structural_target_confirmed,
            validation_kind=validation_kind,
            forecast_path_direction=forecast_path_direction,
            price_source=price_source,
            price_snapshots=price_snapshots,
            exchange=exchange,
            batch_id=batch_id,
        )

    def score_path(self, actual_prices: dict[int, float], actual_regime: Regime | None = None) -> None:
        origin = self.quote_price if self.validation_kind == "FORECAST_AUDIT" else self.entry or self.quote_price
        for horizon in self.horizons:
            actual = actual_prices.get(horizon.minutes)
            horizon.actual = actual
            if actual is None:
                continue
            horizon.range_pass = horizon.predicted_low <= actual <= horizon.predicted_high
            horizon.price_error_pct = (actual / horizon.predicted_base - 1.0) * 100 if horizon.predicted_base > 0 else None
            actual_direction = (
                Regime.UP.value if actual > origin * 1.0005
                else Regime.DOWN.value if actual < origin * 0.9995
                else Regime.RANGE.value
            )
            horizon.direction_pass = actual_direction == horizon.predicted_direction
            horizon.pass_all = bool(horizon.range_pass and horizon.direction_pass)
            horizon.representative_reached = (
                actual >= horizon.predicted_base if horizon.predicted_direction == Regime.UP.value
                else actual <= horizon.predicted_base if horizon.predicted_direction == Regime.DOWN.value
                else horizon.predicted_low <= actual <= horizon.predicted_high
            )
        self.full_path_pass = bool(self.horizons) and all(horizon.pass_all is True for horizon in self.horizons)
        self.data_completeness = "COMPLETE" if self.horizons and all(horizon.actual is not None for horizon in self.horizons) else "PARTIAL"
        if actual_regime is not None:
            self.actual_regime = actual_regime.value
            self.regime_pass = self.actual_regime == self.predicted_regime
        self.complete_four_area_pass = all(
            value is True
            for value in (
                self.quote_pass,
                self.full_path_pass,
                self.regime_pass,
                self.target_pass,
                self.entry_executable,
                self.structural_target_confirmed,
            )
        ) and self.data_completeness == "COMPLETE"

    def score_price_snapshots(self) -> bool:
        """Score a forecast audit from timestamped KIS REST snapshots after 30 minutes."""
        if self.validation_kind != "FORECAST_AUDIT" or not self.horizons or not self.price_snapshots:
            return False
        signal_at = pd.Timestamp(self.signal_time)
        observations: list[tuple[pd.Timestamp, float]] = []
        for snapshot in self.price_snapshots:
            try:
                observed_at = pd.Timestamp(snapshot["timestamp"])
                if observed_at.tzinfo is None and signal_at.tzinfo is not None:
                    observed_at = observed_at.tz_localize(signal_at.tzinfo)
                elif observed_at.tzinfo is not None and signal_at.tzinfo is None:
                    observed_at = observed_at.tz_localize(None)
                observations.append((observed_at, float(snapshot["price"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not observations:
            return False
        observations.sort(key=lambda item: item[0])
        actual_prices: dict[int, float] = {}
        for horizon in self.horizons:
            cutoff = signal_at + pd.Timedelta(horizon.minutes, unit="min")
            # A browser fragment fires on a minute cadence and can be a few seconds
            # after its scheduled boundary. Select the closest real KIS snapshot in
            # a narrow, disclosed window instead of silently treating a +1-second
            # tick as missing.
            eligible = [
                item for item in observations
                if signal_at <= item[0] and abs((item[0] - cutoff).total_seconds()) <= 75
            ]
            if eligible:
                actual_prices[horizon.minutes] = min(
                    eligible, key=lambda item: abs((item[0] - cutoff).total_seconds())
                )[1]
        if len(actual_prices) != len(self.horizons):
            return False
        final_price = actual_prices[max(actual_prices)]
        actual_regime = (
            Regime.UP if final_price > self.quote_price * 1.0005
            else Regime.DOWN if final_price < self.quote_price * 0.9995
            else Regime.RANGE
        )
        self.score_path(actual_prices, actual_regime)
        return self.data_completeness == "COMPLETE"

    def mark_data_missing(self, minutes: list[int], observed_at: datetime, reason: str) -> None:
        """Persist a boundary-capture failure without misclassifying it as a forecast miss."""
        captured_at = observed_at.isoformat()
        for minute in minutes:
            self.capture_failures[str(int(minute))] = {
                "timestamp": captured_at,
                "reason": str(reason)[:240],
            }
        self.data_completeness = "DATA_MISSING"
        self.full_path_pass = False
        self.missing.append("실제 5·15·30분 경계 시세 수집 실패: " + ", ".join(f"{minute}분" for minute in minutes))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def score_future_bars(self, future: pd.DataFrame, cost_pct: float = 0.10) -> None:
        """Score one signal chronologically; same-bar target and stop is always a failure."""
        if future.empty:
            return
        frame = future.sort_index().copy()
        origin = self.entry or self.quote_price
        actual_prices: dict[int, float] = {}
        signal_at = datetime.fromisoformat(self.signal_time)
        if signal_at.tzinfo is not None and getattr(frame.index, "tz", None) is None:
            signal_at = signal_at.replace(tzinfo=None)
        for horizon in (5, 15, 30):
            cutoff = signal_at + pd.Timedelta(horizon, unit="min")
            eligible = frame.loc[frame.index <= cutoff]
            if not eligible.empty:
                actual_prices[horizon] = float(eligible.close.iloc[-1])
                point = next((item for item in self.horizons if item.minutes == horizon), None)
                if point is not None:
                    horizon_origin = self.quote_price if self.validation_kind == "FORECAST_AUDIT" else origin
                    point.mfe_pct = (float(eligible.high.max()) / horizon_origin - 1) * 100
                    point.mae_pct = (float(eligible.low.min()) / horizon_origin - 1) * 100

        end = frame.loc[frame.index <= signal_at + pd.Timedelta(30, unit="min")]
        if end.empty:
            return
        high = float(end.high.max())
        low = float(end.low.min())
        self.mfe_pct = (high / origin - 1) * 100
        self.mae_pct = (low / origin - 1) * 100
        target_window = frame.loc[frame.index <= signal_at + pd.Timedelta(5, unit="min")]
        target_time = None
        soft_stop_time = None
        hard_stop_time = None
        if not target_window.empty:
            if self.target is not None:
                touched = target_window.index[target_window.high >= self.target]
                target_time = touched[0] if len(touched) else None
            if self.soft_stop is not None:
                touched = target_window.index[target_window.low <= self.soft_stop]
                soft_stop_time = touched[0] if len(touched) else None
            hard_stop = self.hard_stop if self.hard_stop is not None else self.stop
            if hard_stop is not None:
                touched = target_window.index[target_window.low <= hard_stop]
                hard_stop_time = touched[0] if len(touched) else None
        self.soft_stop_first = bool(
            soft_stop_time is not None
            and (target_time is None or soft_stop_time < target_time)
            and (hard_stop_time is None or soft_stop_time <= hard_stop_time)
        )
        if target_time is not None and hard_stop_time is not None and target_time == hard_stop_time:
            self.target_first = False
            self.stopped_first = True
            self.hard_stop_first = True
            self.target_pass = False
            self.target_outcome = "AMBIGUOUS_SAME_BAR"
            self.missing.append("1차 목표 5분 창에서 목표·손절 동시 접촉: 보수적으로 실패 처리")
        else:
            self.target_first = target_time is not None and (hard_stop_time is None or target_time < hard_stop_time)
            self.stopped_first = hard_stop_time is not None and (target_time is None or hard_stop_time < target_time)
            self.hard_stop_first = self.stopped_first
            self.target_pass = self.target_first
            self.target_outcome = "TARGET_FIRST" if self.target_first else "STOP_FIRST" if self.stopped_first else "TIME_EXPIRED"
        hard_stop = self.hard_stop if self.hard_stop is not None else self.stop
        exit_price = self.target if self.target_first else hard_stop if self.stopped_first else float(target_window.close.iloc[-1]) if not target_window.empty else float(end.close.iloc[-1])
        self.net_return_pct = (float(exit_price) / origin - 1) * 100 - cost_pct

        first = float(end.close.iloc[0])
        last = float(end.close.iloc[-1])
        path_range = (float(end.high.max()) - float(end.low.min())) / max(first, 1e-9)
        net = (last - first) / max(first, 1e-9)
        realized = Regime.RANGE if abs(net) < max(0.001, path_range * 0.25) else Regime.UP if net > 0 else Regime.DOWN
        self.score_path(actual_prices, realized)


class ValidationStore:
    def __init__(self, root: str | Path = ".scanner_data/validation", event_store: EventStore | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.event_store = event_store
        self.last_persistence_error: str | None = None

    @property
    def storage_status(self) -> str:
        return self.event_store.status if self.event_store else "로컬 임시 저장"

    def _local_path(self, case: ValidationCase) -> Path:
        day = datetime.fromisoformat(case.signal_time).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{case.case_id}.json"

    def save(self, case: ValidationCase) -> Path:
        path = self._local_path(case)
        payload = case.to_dict()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.event_store and self.event_store.configured:
            try:
                self.event_store.upsert(case.case_id, "validation_case", case.signal_time, payload)
                self.last_persistence_error = None
            except PersistenceError as exc:
                self.last_persistence_error = str(exc)
        return path

    def save_once(self, case: ValidationCase, cooldown_seconds: int = 180) -> tuple[Path, bool]:
        """Persist one raw signal event; UI reruns inside the cooldown reuse it."""
        day = datetime.fromisoformat(case.signal_time).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        candidates = sorted(folder.glob(f"{case.market}-{case.symbol}-*.json"), reverse=True)
        for existing in candidates[:10]:
            try:
                row = json.loads(existing.read_text(encoding="utf-8"))
                old_time = datetime.fromisoformat(row["signal_time"])
                new_time = datetime.fromisoformat(case.signal_time)
                same_event = (
                    row.get("version") == case.version
                    and row.get("predicted_regime") == case.predicted_regime
                    and row.get("validation_kind", "ACTIONABLE") == case.validation_kind
                )
                if same_event and abs((new_time - old_time).total_seconds()) < cooldown_seconds:
                    return existing, False
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                continue
        return self.save(case), True

    def update(self, case: ValidationCase) -> Path:
        return self.save(case)

    def _local_rows(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("*/*.json"):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return rows

    def load_all(self) -> list[dict[str, Any]]:
        by_id = {str(row.get("case_id")): row for row in self._local_rows() if row.get("case_id")}
        if self.event_store and self.event_store.configured:
            try:
                for row in self.event_store.list("validation_case"):
                    if row.get("case_id"):
                        by_id[str(row["case_id"])] = row
                self.last_persistence_error = None
            except PersistenceError as exc:
                self.last_persistence_error = str(exc)
        return sorted(by_id.values(), key=lambda row: str(row.get("signal_time", "")))

    def cases(self) -> list[ValidationCase]:
        result: list[ValidationCase] = []
        for row in self.load_all():
            try:
                row["horizons"] = [HorizonResult(**item) for item in row.get("horizons", [])]
                result.append(ValidationCase(**row))
            except (TypeError, ValueError):
                continue
        return result

    def pending(self, market: str | None = None) -> list[ValidationCase]:
        return [
            case for case in self.cases()
            if case.full_path_pass is None
            and case.data_completeness not in {"DATA_MISSING", "EXPIRED"}
            and (market is None or case.market == market)
        ]

    @staticmethod
    def due_horizon_minutes(case: ValidationCase, observed_at: datetime, grace_seconds: int = 75) -> list[int]:
        """Return only uncaptured 5·15·30-minute boundaries due at ``observed_at``."""
        now = pd.Timestamp(observed_at)
        signal_at = pd.Timestamp(case.signal_time)
        if now.tzinfo is None and signal_at.tzinfo is not None:
            now = now.tz_localize(signal_at.tzinfo)
        elif now.tzinfo is not None and signal_at.tzinfo is None:
            now = now.tz_localize(None)
        captured_times: list[pd.Timestamp] = []
        for snapshot in case.price_snapshots:
            try:
                captured_at = pd.Timestamp(snapshot["timestamp"])
                if captured_at.tzinfo is None and signal_at.tzinfo is not None:
                    captured_at = captured_at.tz_localize(signal_at.tzinfo)
                elif captured_at.tzinfo is not None and signal_at.tzinfo is None:
                    captured_at = captured_at.tz_localize(None)
                captured_times.append(captured_at)
            except (KeyError, TypeError, ValueError):
                continue
        due: list[int] = []
        for horizon in case.horizons:
            cutoff = signal_at + pd.Timedelta(horizon.minutes, unit="min")
            captured = any(abs((captured_at - cutoff).total_seconds()) <= grace_seconds for captured_at in captured_times)
            if cutoff <= now <= cutoff + pd.Timedelta(grace_seconds, unit="s") and not captured:
                due.append(int(horizon.minutes))
        return due

    def pending_forecast_audits(
        self, market: str, version: str | None = None, limit: int = 5, batch_id: str | None = None,
    ) -> list[ValidationCase]:
        """Return incomplete full-path audits for the active rule version."""
        pending = [
            case for case in self.pending(market)
            if (
                case.validation_kind == "FORECAST_AUDIT"
                and (version is None or case.version == version)
                and (batch_id is None or case.batch_id == batch_id)
            )
        ]
        return sorted(pending, key=lambda case: case.signal_time)[:max(1, min(int(limit), 5))]

    def has_pending_forecast_audit(self, market: str, version: str, batch_id: str | None = None) -> bool:
        return bool(self.pending_forecast_audits(market, version=version, limit=1, batch_id=batch_id))

    def due_forecast_audits(
        self,
        market: str,
        observed_at: datetime,
        version: str | None = None,
        limit: int = 8,
        grace_seconds: int = 75,
        batch_id: str | None = None,
    ) -> list[ValidationCase]:
        """Return only audits needing their next 5·15·30-minute price snapshot.

        This keeps a browser-open batch from refreshing every pending symbol each
        minute. One symbol is fetched only when one of its three real observation
        boundaries is due, which preserves the shared KIS request budget.
        """
        now = pd.Timestamp(observed_at)
        due: list[ValidationCase] = []
        for case in self.pending_forecast_audits(market, version=version, limit=100, batch_id=batch_id):
            if self.due_horizon_minutes(case, observed_at, grace_seconds):
                due.append(case)
        return due[:max(1, int(limit))]

    def score_ready(self, symbol: str, market: str, bars: pd.DataFrame, cost_pct: float) -> int:
        """Score pending same-symbol cases after a complete 30-minute future path exists."""
        if bars.empty:
            return 0
        latest = bars.index.max()
        scored = 0
        for case in self.pending(market):
            if case.symbol != symbol:
                continue
            signal_at = pd.Timestamp(case.signal_time)
            if latest.tz is None and signal_at.tzinfo is not None:
                signal_at = signal_at.tz_localize(None)
            elif latest.tz is not None and signal_at.tzinfo is None:
                signal_at = signal_at.tz_localize(latest.tz)
            if latest < signal_at + pd.Timedelta(30, unit="min"):
                continue
            future = bars.loc[(bars.index >= signal_at) & (bars.index <= signal_at + pd.Timedelta(30, unit="min"))]
            if len(future) < 4:
                continue
            case.score_future_bars(future, cost_pct)
            self.update(case)
            scored += 1
        return scored

    def capture_rest_snapshot_and_score(
        self,
        symbol: str,
        market: str,
        observed_at: datetime,
        price: float,
        source: str = "KIS REST",
        version: str | None = None,
    ) -> int:
        """Append one KIS REST snapshot to pending forecast audits and score mature paths."""
        if price <= 0:
            return 0
        observed = pd.Timestamp(observed_at)
        scored = 0
        for case in self.pending(market):
            if (
                case.symbol != symbol
                or case.validation_kind != "FORECAST_AUDIT"
                or (version is not None and case.version != version)
            ):
                continue
            signal_at = pd.Timestamp(case.signal_time)
            comparable_observed = observed
            if comparable_observed.tzinfo is None and signal_at.tzinfo is not None:
                comparable_observed = comparable_observed.tz_localize(signal_at.tzinfo)
            elif comparable_observed.tzinfo is not None and signal_at.tzinfo is None:
                comparable_observed = comparable_observed.tz_localize(None)
            if comparable_observed < signal_at:
                continue
            timestamp_text = observed_at.isoformat()
            snapshots = list(case.price_snapshots)
            if not any(str(snapshot.get("timestamp")) == timestamp_text for snapshot in snapshots):
                snapshots.append({"timestamp": timestamp_text, "price": float(price), "source": source})
                case.price_snapshots = snapshots
            if comparable_observed >= signal_at + pd.Timedelta(30, unit="min") and case.score_price_snapshots():
                scored += 1
            elif comparable_observed >= signal_at + pd.Timedelta(32, unit="min"):
                # A late snapshot cannot reconstruct earlier boundaries. Preserve the
                # cause as DATA_MISSING instead of treating it as a forecast miss.
                missing_minutes = [
                    horizon.minutes for horizon in case.horizons
                    if horizon.actual is None
                ]
                case.mark_data_missing(
                    missing_minutes or [5, 15, 30], observed_at,
                    "경계 시각 ±75초 안에 KIS 실제가를 확보하지 못함",
                )
            self.update(case)
        return scored

    @staticmethod
    def _forecast_audit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Summarize direction and price-path accuracy without mixing it with trade outcomes."""
        complete = [row for row in rows if row.get("data_completeness") == "COMPLETE"]
        strict_passes = [row for row in complete if row.get("full_path_pass") is True]
        direction_passes = []
        by_direction: dict[str, list[dict[str, Any]]] = {}
        by_source: dict[str, list[dict[str, Any]]] = {}
        horizon_stats = {
            str(minutes): {"complete": 0, "range_pass": 0, "direction_pass": 0, "strict_pass": 0}
            for minutes in (5, 15, 30)
        }
        for row in complete:
            horizons = [item for item in row.get("horizons", []) if item.get("actual") is not None]
            if horizons and all(item.get("direction_pass") is True for item in horizons):
                direction_passes.append(row)
            direction = str(row.get("forecast_path_direction") or "MIXED")
            by_direction.setdefault(direction, []).append(row)
            source = str(row.get("price_source") or "KIS 체결")
            by_source.setdefault(source, []).append(row)
            for horizon in horizons:
                minutes = str(horizon.get("minutes"))
                if minutes not in horizon_stats:
                    continue
                stats = horizon_stats[minutes]
                stats["complete"] += 1
                stats["range_pass"] += int(horizon.get("range_pass") is True)
                stats["direction_pass"] += int(horizon.get("direction_pass") is True)
                stats["strict_pass"] += int(horizon.get("pass_all") is True)
        for stats in horizon_stats.values():
            denominator = stats["complete"]
            stats["range_rate"] = stats["range_pass"] / denominator * 100 if denominator else None
            stats["direction_rate"] = stats["direction_pass"] / denominator * 100 if denominator else None
            stats["strict_rate"] = stats["strict_pass"] / denominator * 100 if denominator else None
        by_direction_summary = {
            direction: {
                "complete": len(group),
                "strict_full_path_pass": sum(row.get("full_path_pass") is True for row in group),
                "strict_full_path_rate": (
                    sum(row.get("full_path_pass") is True for row in group) / len(group) * 100 if group else None
                ),
            }
            for direction, group in by_direction.items()
        }
        by_source_summary = {
            source: {
                "complete": len(group),
                "strict_full_path_pass": sum(row.get("full_path_pass") is True for row in group),
                "strict_full_path_rate": (
                    sum(row.get("full_path_pass") is True for row in group) / len(group) * 100 if group else None
                ),
            }
            for source, group in by_source.items()
        }
        return {
            "records": len(rows),
            "complete_paths": len(complete),
            "data_missing_paths": sum(row.get("data_completeness") == "DATA_MISSING" for row in rows),
            "expired_paths": sum(row.get("data_completeness") == "EXPIRED" for row in rows),
            "strict_full_path_pass": len(strict_passes),
            "strict_full_path_rate": len(strict_passes) / len(complete) * 100 if complete else None,
            "direction_full_path_pass": len(direction_passes),
            "direction_full_path_rate": len(direction_passes) / len(complete) * 100 if complete else None,
            "horizons": horizon_stats,
            "by_prediction_direction": by_direction_summary,
            "by_price_source": by_source_summary,
        }

    def summary(self) -> dict[str, Any]:
        all_rows = self.load_all()
        rows = [row for row in all_rows if row.get("validation_kind", "ACTIONABLE") == "ACTIONABLE"]
        forecast_audit_rows = [row for row in all_rows if row.get("validation_kind") == "FORECAST_AUDIT"]
        complete = [row for row in rows if row.get("complete_four_area_pass") is not None]
        passed = [row for row in complete if row.get("complete_four_area_pass") is True]
        net = [float(row["net_return_pct"]) for row in complete if row.get("net_return_pct") is not None]
        groups: dict[str, list[float]] = {}
        for row in rows:
            if row.get("net_return_pct") is None:
                continue
            key = f"{row.get('market', '')}:{row.get('session', '')}:{row.get('strategy', '')}"
            groups.setdefault(key, []).append(float(row["net_return_pct"]))
        by_strategy_session = {}
        for key, values in groups.items():
            wins = [value for value in values if value > 0]
            losses = [value for value in values if value <= 0]
            win_rate = len(wins) / len(values) * 100
            average_win = sum(wins) / len(wins) if wins else None
            average_loss = sum(losses) / len(losses) if losses else None
            expectancy = sum(values) / len(values)
            profit_factor = (
                sum(wins) / abs(sum(losses))
                if wins and losses and abs(sum(losses)) > 1e-12
                else None
            )
            by_strategy_session[key] = {
                "samples": len(values),
                "target_first_rate": sum(1 for row in rows if f"{row.get('market', '')}:{row.get('session', '')}:{row.get('strategy', '')}" == key and row.get("target_pass") is True) / len(values) * 100,
                "net_win_rate": win_rate,
                "average_net_return_pct": expectancy,
                "average_win_pct": average_win,
                "average_loss_pct": average_loss,
                "expectancy_pct": expectancy,
                "profit_factor": profit_factor,
                "eligible_for_80pct_review": len(values) >= 30,
                "meets_80pct_goal": len(values) >= 30
                and (sum(1 for row in rows if f"{row.get('market', '')}:{row.get('session', '')}:{row.get('strategy', '')}" == key and row.get("target_pass") is True) / len(values) * 100) >= 80.0
                and expectancy > 0,
            }
        quote_verified = [row for row in rows if row.get("quote_pass") is True]
        complete_data = [row for row in rows if row.get("data_completeness") == "COMPLETE"]
        executable = [row for row in rows if row.get("entry_executable") is True]
        full_path = [row for row in rows if row.get("full_path_pass") is True]
        target_first = [row for row in rows if row.get("target_pass") is True]
        structural_target = [row for row in rows if row.get("structural_target_confirmed") is True]
        return {
            "storage": self.storage_status,
            "signals": len(rows),
            "quote_verified": len(quote_verified),
            "complete_data": len(complete_data),
            "entry_executable": len(executable),
            "full_path_pass": len(full_path),
            "structural_target_confirmed": len(structural_target),
            "target_first": len(target_first),
            "cost_positive": sum(value > 0 for value in net),
            "fully_scored": len(complete),
            "four_area_pass": len(passed),
            "four_area_rate": len(passed) / len(complete) * 100 if complete else None,
            "net_win_rate": sum(value > 0 for value in net) / len(net) * 100 if net else None,
            "average_net_return_pct": sum(net) / len(net) if net else None,
            "worst_net_return_pct": min(net) if net else None,
            "by_strategy_session": by_strategy_session,
            "forecast_audit": self._forecast_audit_summary(forecast_audit_rows),
        }

    def export_csv(self, output: str | Path) -> Path:
        rows = self.load_all()
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
                "case_id", "version", "symbol", "market", "session", "signal_time", "signal", "score", "risk_state", "validation_kind", "price_source", "price_snapshots",
            "quote_price", "latest_trade_price", "latest_trade_time", "quote_age_seconds", "quote_pass",
            "entry", "entry_executable", "orderbook_available", "spread_pct",
            "predicted_regime", "actual_regime", "regime_pass", "structural_target_confirmed",
                "target", "target_basis", "soft_stop", "hard_stop", "stop", "target_outcome", "target_pass", "soft_stop_first", "hard_stop_first", "full_path_pass",
            "data_completeness", "complete_four_area_pass", "mfe_pct", "mae_pct", "net_return_pct",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def export_html(self, output: str | Path) -> Path:
        rows = self.load_all()
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary()
        body = []
        for row in sorted(rows, key=lambda value: value.get("signal_time", "")):
            horizons = row.get("horizons", [])
            path_text = ", ".join(
                f"{horizon.get('minutes')}분 실제 {horizon.get('actual')} / 통과 {horizon.get('pass_all')}"
                for horizon in horizons
            )
            body.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('signal_time', '')))}</td>"
                f"<td>{html.escape(str(row.get('market', '')))}</td>"
                f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
                f"<td>{html.escape(str(row.get('signal', '')))}</td>"
                f"<td>{html.escape(path_text)}</td>"
                f"<td>{row.get('mfe_pct')}</td><td>{row.get('mae_pct')}</td>"
                f"<td>{row.get('target_pass')}</td><td>{row.get('complete_four_area_pass')}</td>"
                "</tr>"
            )
        document = f"""<!doctype html><meta charset='utf-8'><title>스캐너 검증 보고서</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:7px;text-align:left}}th{{background:#f3f5f8}}</style>
<h1>스캐너 원본 신호 검증 보고서</h1>
<p>저장소 {html.escape(str(summary['storage']))} · 관찰 신호 {summary['signals']}건 · 현재가 검증 {summary['quote_verified']}건 · 완전 데이터 {summary['complete_data']}건 · 체결 가능 {summary['entry_executable']}건 · 전체 경로 적중 {summary['full_path_pass']}건 · 구조 목표 확인 {summary['structural_target_confirmed']}건 · 목표 선도달 {summary['target_first']}건 · 비용 차감 양수 {summary['cost_positive']}건 · 엄격 통과 {summary['four_area_pass']}건</p>
<table><thead><tr><th>신호시각</th><th>시장</th><th>종목</th><th>신호</th><th>5~30분 경로</th><th>MFE%</th><th>MAE%</th><th>목표 우선</th><th>엄격 통과</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>"""
        path.write_text(document, encoding="utf-8")
        return path
