from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

import pandas as pd

from .models import Regime


TREND = {1, 2, 3, 4, 5, 9, 11, 13, 15}
REVERSAL = {6, 7, 8, 10, 12}

NAMES = {
    1: "추세 지속",
    2: "추세 눌림",
    3: "저항 돌파",
    4: "돌파 후 재지지",
    5: "변동성 압축 돌파",
    6: "가짜돌파 반전",
    7: "박스권 Swing",
    8: "평균회귀",
    9: "모멘텀 지속",
    10: "모멘텀 소진 반전",
    11: "갭 지속",
    12: "갭 복귀",
    13: "시초가 범위 돌파",
    14: "이벤트 모멘텀",
    15: "상대강도",
}

# User-specified incompatibilities. Strategy 14 is deliberately omitted because
# verified event direction must be supplied by a separate data source.
INCOMPATIBLE = {
    1: REVERSAL,
    2: REVERSAL,
    3: REVERSAL,
    4: REVERSAL,
    5: REVERSAL,
    6: {1, 2, 3, 4, 5, 9},
    7: {1, 2, 3, 4, 5, 9},
    8: {1, 2, 3, 4, 5, 9},
    9: REVERSAL,
    10: {1, 2, 3, 4, 5, 9},
    11: {12},
    12: {11},
    13: REVERSAL,
    15: {6, 7, 8, 10, 12},
}

COMBOS = {
    "TREND_PULLBACK": (1, 2, 4, 9, 15),
    "BREAKOUT_CONTINUATION": (3, 4, 5, 9, 13, 15),
    "GAP_CONTINUATION": (11, 3, 9, 13, 15),
    "REVERSAL_MEAN_REVERSION": (6, 7, 8, 10, 12),
}


@dataclass
class EnsembleResult:
    active_ids: list[int]
    cluster: str
    score: int
    conflicts: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def calibration_key(self) -> str:
        if self.conflicts:
            return "CONFLICT"
        return self.cluster

    @property
    def active_names(self) -> list[str]:
        return [NAMES[item] for item in self.active_ids]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["active_names"] = self.active_names
        payload["calibration_key"] = self.calibration_key
        return payload


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _recent_slope(frame: pd.DataFrame, length: int = 12) -> float:
    recent = frame.tail(min(length, len(frame)))
    if len(recent) < 4:
        return 0.0
    start = float(recent.close.iloc[0])
    end = float(recent.close.iloc[-1])
    return (end / max(start, 1e-9) - 1.0) * 100


def _compression(frame: pd.DataFrame) -> bool:
    if len(frame) < 30 or "atr_pct" not in frame:
        return False
    recent = float(frame.atr_pct.tail(8).median())
    prior = float(frame.atr_pct.tail(30).head(20).median())
    return prior > 0 and recent <= prior * 0.72


def _gap_pct(frame: pd.DataFrame) -> float:
    if len(frame) < 2:
        return 0.0
    prior_close = float(frame.close.iloc[0])
    opening = float(frame.open.iloc[0])
    return (opening / max(prior_close, 1e-9) - 1.0) * 100


def _has_conflict(ids: Iterable[int]) -> list[str]:
    active = set(ids)
    conflicts: list[str] = []
    for left in sorted(active):
        for right in sorted(active):
            if right <= left:
                continue
            if right in INCOMPATIBLE.get(left, set()) or left in INCOMPATIBLE.get(right, set()):
                conflicts.append(f"{NAMES[left]} ↔ {NAMES[right]}")
    return conflicts


def evaluate_ensemble(
    frame: pd.DataFrame,
    *,
    regime: Regime,
    box_valid: bool,
    price: float,
    vwap_ok: bool,
    ema_ok: bool,
    rvol: float,
    notional_rvol: float,
    fake_breakout: bool,
    upper_rejection: bool,
    opening_range_breakout: bool = False,
    event_direction: str | None = None,
) -> EnsembleResult:
    """Classify one mutually compatible intraday strategy cluster.

    All inputs must use completed bars. No event credit is added without an explicitly
    verified directional event supplied by a separate source.
    """
    if frame.empty or price <= 0:
        return EnsembleResult([], "DATA_WAIT", 0, reasons=["완료봉이 부족해 전략 조합을 계산하지 않습니다."])

    latest = frame.iloc[-1]
    slope = _recent_slope(frame)
    momentum = slope > 0.35 and rvol >= 1.0 and notional_rvol >= 0.85
    trend_ready = regime == Regime.UP and vwap_ok and ema_ok
    prior_highs = frame.high.iloc[:-1].tail(min(20, max(0, len(frame) - 1)))
    resistance = float(prior_highs.max()) if not prior_highs.empty else float(latest.high)
    breakout = bool(float(latest.close) >= resistance * 0.998 and momentum)
    pullback = bool(trend_ready and float(latest.close) <= float(frame.close.tail(min(8, len(frame))).max()) * 0.998 and float(latest.close) >= float(latest.vwap))
    retest = bool(trend_ready and float(latest.low) <= float(latest.ema9) <= float(latest.close))
    compression = _compression(frame) and breakout
    gap = _gap_pct(frame)
    gap_continue = gap >= 0.5 and trend_ready and momentum
    gap_revert = gap <= -0.5 and regime in {Regime.RANGE, Regime.TRANSITION} and float(latest.close) >= float(latest.vwap)
    mean_revert = box_valid and regime == Regime.RANGE and float(latest.close) >= float(latest.vwap)
    exhaustion = bool(upper_rejection and slope < 0.15)
    false_break_reversal = bool(fake_breakout and regime in {Regime.RANGE, Regime.TRANSITION})
    relative_strength = bool(rvol >= 1.25 and notional_rvol >= 1.10)

    active: set[int] = set()
    if trend_ready:
        active.add(1)
    if pullback:
        active.add(2)
    if breakout:
        active.add(3)
    if retest:
        active.add(4)
    if compression:
        active.add(5)
    if false_break_reversal:
        active.add(6)
    if box_valid:
        active.add(7)
    if mean_revert:
        active.add(8)
    if momentum:
        active.add(9)
    if exhaustion:
        active.add(10)
    if gap_continue:
        active.add(11)
    if gap_revert:
        active.add(12)
    if opening_range_breakout and breakout:
        active.add(13)
    if event_direction in {"UP", "DOWN"}:
        active.add(14)
    if relative_strength:
        active.add(15)

    conflicts = _has_conflict(active)
    if conflicts:
        return EnsembleResult(
            sorted(active),
            "CONFLICT",
            0,
            conflicts=conflicts,
            reasons=["상승·돌파 계열과 반전 계열이 동시에 감지되어 점수를 합산하지 않습니다."],
        )

    if any(item in active for item in (11, 3, 9, 13, 15)) and gap_continue:
        cluster = "GAP_CONTINUATION"
    elif len(active.intersection({3, 4, 5, 9, 13, 15})) >= 3:
        cluster = "BREAKOUT_CONTINUATION"
    elif len(active.intersection({1, 2, 4, 9, 15})) >= 3:
        cluster = "TREND_PULLBACK"
    elif len(active.intersection(REVERSAL)) >= 2:
        cluster = "REVERSAL_MEAN_REVERSION"
    else:
        cluster = "WATCH"

    compatible = set(COMBOS.get(cluster, ()))
    points = sum(14 for item in active if item in compatible)
    if event_direction == "UP" and cluster in {"TREND_PULLBACK", "BREAKOUT_CONTINUATION", "GAP_CONTINUATION"}:
        points += 8
    if event_direction == "DOWN" and cluster == "REVERSAL_MEAN_REVERSION":
        points += 5
    score = int(round(_clamp(points + (12 if relative_strength else 0), 0, 100)))
    reasons = [f"전략 조합: {', '.join(NAMES[item] for item in sorted(active)) or '대기'}"]
    if cluster == "WATCH":
        reasons.append("호환된 전략 조합이 충분하지 않아 관찰 단계로 유지합니다.")
    return EnsembleResult(sorted(active), cluster, score, reasons=reasons)
