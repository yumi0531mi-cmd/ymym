from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .persistence import EventStore, PersistenceError
from .validation import ValidationStore


@dataclass(slots=True)
class CalibrationResult:
    market: str
    session: str
    strategy: str
    score_bucket: str
    samples: int
    probability_pct: float | None
    average_net_return_pct: float | None

    @property
    def calibrated(self) -> bool:
        return self.samples >= 30 and self.probability_pct is not None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["calibrated"] = self.calibrated
        return payload


def bucket(score: int) -> str:
    low = max(0, min(90, int(score // 10) * 10))
    return f"{low:02d}-{low + 9:02d}"


def calibration_for(
    validation: ValidationStore,
    *,
    market: str,
    session: str,
    strategy: str,
    score: int,
    version: str | None = None,
) -> CalibrationResult:
    current_bucket = bucket(score)
    rows = validation.load_all()
    matches = [
        row for row in rows
        if row.get("market") == market
        and row.get("session") == session
        and row.get("strategy") == strategy
        and bucket(int(row.get("score", 0))) == current_bucket
        and row.get("target_pass") is not None
        and (version is None or row.get("version") == version)
    ]
    samples = len(matches)
    wins = sum(row.get("target_pass") is True for row in matches)
    net = [float(row["net_return_pct"]) for row in matches if row.get("net_return_pct") is not None]
    return CalibrationResult(
        market=market,
        session=session,
        strategy=strategy,
        score_bucket=current_bucket,
        samples=samples,
        probability_pct=round(wins / samples * 100, 1) if samples >= 30 else None,
        average_net_return_pct=round(sum(net) / len(net), 4) if net else None,
    )


def save_snapshot(events: EventStore, result: CalibrationResult, created_at: str) -> None:
    if not events.configured:
        return
    identifier = f"calibration-v51-{result.market}-{result.session}-{result.score_bucket}-{created_at[:10]}"
    try:
        events.upsert(identifier, "calibration_snapshot_v51", created_at, result.to_dict())
    except PersistenceError:
        return
