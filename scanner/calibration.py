from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .persistence import EventStore, PersistenceError
from .validation import ValidationStore


MIN_COMPLETE_PATH_SAMPLES = 100
RECENT_COMPLETE_PATH_WINDOW = 100


@dataclass
class CalibrationResult:
    market: str
    session: str
    strategy: str
    score_bucket: str
    samples: int
    probability_pct: float | None
    average_net_return_pct: float | None
    recent_samples: int = 0
    recent_probability_pct: float | None = None
    recent_average_net_return_pct: float | None = None

    @property
    def calibrated(self) -> bool:
        return self.samples >= MIN_COMPLETE_PATH_SAMPLES and self.probability_pct is not None

    @property
    def positive_expectancy(self) -> bool:
        return bool(
            self.average_net_return_pct is not None
            and self.recent_average_net_return_pct is not None
            and self.average_net_return_pct > 0
            and self.recent_average_net_return_pct > 0
        )

    @property
    def target_80_verified(self) -> bool:
        """Require 100 real complete-path outcomes and positive cost-adjusted expectancy."""
        return bool(
            self.calibrated
            and self.recent_samples >= MIN_COMPLETE_PATH_SAMPLES
            and self.probability_pct is not None and self.probability_pct >= 80.0
            and self.recent_probability_pct is not None and self.recent_probability_pct >= 80.0
            and self.positive_expectancy
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["calibrated"] = self.calibrated
        payload["target_80_verified"] = self.target_80_verified
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
        and row.get("data_completeness") == "COMPLETE"
        and row.get("entry_executable") is True
        and row.get("structural_target_confirmed") is True
        and row.get("complete_four_area_pass") is not None
        and (version is None or row.get("version") == version)
    ]
    matches = sorted(matches, key=lambda row: str(row.get("signal_time", "")))
    samples = len(matches)
    wins = sum(row.get("complete_four_area_pass") is True for row in matches)
    net = [float(row["net_return_pct"]) for row in matches if row.get("net_return_pct") is not None]
    recent = matches[-RECENT_COMPLETE_PATH_WINDOW:]
    recent_samples = len(recent)
    recent_wins = sum(row.get("complete_four_area_pass") is True for row in recent)
    recent_net = [float(row["net_return_pct"]) for row in recent if row.get("net_return_pct") is not None]
    return CalibrationResult(
        market=market,
        session=session,
        strategy=strategy,
        score_bucket=current_bucket,
        samples=samples,
        probability_pct=round(wins / samples * 100, 1) if samples >= MIN_COMPLETE_PATH_SAMPLES else None,
        average_net_return_pct=round(sum(net) / len(net), 4) if net else None,
        recent_samples=recent_samples,
        recent_probability_pct=round(recent_wins / recent_samples * 100, 1) if recent_samples >= MIN_COMPLETE_PATH_SAMPLES else None,
        recent_average_net_return_pct=round(sum(recent_net) / len(recent_net), 4) if recent_net else None,
    )


def save_snapshot(events: EventStore, result: CalibrationResult, created_at: str) -> None:
    if not events.configured:
        return
    identifier = f"calibration-v51-{result.market}-{result.session}-{result.score_bucket}-{created_at[:10]}"
    try:
        events.upsert(identifier, "calibration_snapshot_v51", created_at, result.to_dict())
    except PersistenceError:
        return
