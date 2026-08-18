from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from .calibration import MIN_COMPLETE_PATH_SAMPLES
from .indicators import enrich
from .models import Regime


@dataclass(frozen=True)
class Pivot:
    at: pd.Timestamp
    kind: str  # LOW or HIGH
    price: float


@dataclass(frozen=True)
class Swing:
    start_at: pd.Timestamp
    end_at: pd.Timestamp
    start_price: float
    end_price: float
    direction: str  # UP or DOWN

    @property
    def width_pct(self) -> float:
        return abs(self.end_price / max(self.start_price, 1e-9) - 1) * 100

    @property
    def minutes(self) -> float:
        return max(1.0, (self.end_at - self.start_at).total_seconds() / 60)


@dataclass
class SwingStatistics:
    up_swings: list[Swing] = field(default_factory=list)
    down_swings: list[Swing] = field(default_factory=list)
    representative_width_pct: float | None = None
    representative_cycle_minutes: float | None = None
    representative_down_minutes: float | None = None
    consistency: float = 0.0
    fatigue: int = 0
    fatigue_reasons: list[str] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.up_swings)


@dataclass
class PersistenceResult:
    score: int
    band: str
    horizon_state: str
    confidence_pct: float
    horizon_minutes: int
    swing: SwingStatistics
    vwap_occupancy_pct: float
    structure_ok: bool
    liquidity_ok: bool
    spread_ok: bool
    remaining_minutes: int
    new_entry_allowed: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["swing"]["up_swings"] = [
            {
                "start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat(),
                "start_price": item.start_price, "end_price": item.end_price,
                "direction": item.direction, "width_pct": item.width_pct, "minutes": item.minutes,
            }
            for item in self.swing.up_swings
        ]
        payload["swing"]["down_swings"] = [
            {
                "start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat(),
                "start_price": item.start_price, "end_price": item.end_price,
                "direction": item.direction, "width_pct": item.width_pct, "minutes": item.minutes,
            }
            for item in self.swing.down_swings
        ]
        return payload


@dataclass
class RiskResult:
    state: str
    soft_stop: float | None
    hard_stop: float | None
    recovery_window_minutes: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalDecision:
    final_buy: bool
    gates: dict[str, bool]
    reasons: list[str] = field(default_factory=list)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _completed(frame: pd.DataFrame) -> pd.DataFrame:
    df = enrich(frame)
    return df.iloc[:-1].copy() if len(df) > 1 else df.copy()


def extract_pivots(frame: pd.DataFrame, order: int = 2) -> list[Pivot]:
    """Extract local completed-bar pivots with deterministic, no-look-ahead-safe rules.

    A pivot uses bars on both sides and is therefore only confirmed after `order` later
    completed bars. The latest possible pivot is deliberately excluded.
    """
    df = _completed(frame)
    if len(df) < order * 2 + 3:
        return []
    raw: list[Pivot] = []
    for pos in range(order, len(df) - order):
        window = df.iloc[pos - order : pos + order + 1]
        row = df.iloc[pos]
        low = float(row.low)
        high = float(row.high)
        if low <= float(window.low.min()) and int((window.low == low).sum()) == 1:
            raw.append(Pivot(pd.Timestamp(df.index[pos]), "LOW", low))
        if high >= float(window.high.max()) and int((window.high == high).sum()) == 1:
            raw.append(Pivot(pd.Timestamp(df.index[pos]), "HIGH", high))
    raw.sort(key=lambda item: (item.at, 0 if item.kind == "LOW" else 1))

    # Keep only the stronger candidate when two consecutive pivots have the same type.
    result: list[Pivot] = []
    for pivot in raw:
        if not result or result[-1].kind != pivot.kind:
            result.append(pivot)
        elif pivot.kind == "LOW" and pivot.price < result[-1].price:
            result[-1] = pivot
        elif pivot.kind == "HIGH" and pivot.price > result[-1].price:
            result[-1] = pivot
    return result


def extract_swings(frame: pd.DataFrame) -> tuple[list[Swing], list[Swing]]:
    pivots = extract_pivots(frame)
    up: list[Swing] = []
    down: list[Swing] = []
    for left, right in zip(pivots, pivots[1:]):
        if right.at <= left.at:
            continue
        if left.kind == "LOW" and right.kind == "HIGH" and right.price > left.price:
            swing = Swing(left.at, right.at, left.price, right.price, "UP")
            if 0.5 <= swing.width_pct <= 5.0:
                up.append(swing)
        if left.kind == "HIGH" and right.kind == "LOW" and right.price < left.price:
            down.append(Swing(left.at, right.at, left.price, right.price, "DOWN"))
    return up, down


def swing_statistics(frame: pd.DataFrame) -> SwingStatistics:
    up, down = extract_swings(frame)
    stats = SwingStatistics(up_swings=up, down_swings=down)
    if not up:
        return stats
    widths = [item.width_pct for item in up]
    reps = float(median(widths))
    stats.representative_width_pct = reps
    relative_deviation = median(abs(value - reps) / max(reps, 1e-9) for value in widths)
    stats.consistency = round(_clamp(1.0 - relative_deviation, 0.0, 1.0) * 100, 1)
    if down:
        stats.representative_down_minutes = round(float(median(item.minutes for item in down)), 1)
    cycle_parts = []
    for rise in up:
        later_down = next((fall for fall in down if fall.start_at >= rise.end_at), None)
        cycle_parts.append(rise.minutes + (later_down.minutes if later_down else 0.0))
    stats.representative_cycle_minutes = round(float(median(cycle_parts)), 1) if cycle_parts else None

    recent = widths[-3:]
    if len(recent) == 3 and recent[0] > recent[1] > recent[2]:
        stats.fatigue += 20
        stats.fatigue_reasons.append("최근 3개 상승 Swing 폭이 연속 축소")
    if recent and recent[-1] < reps * 0.70:
        stats.fatigue += 20
        stats.fatigue_reasons.append("최근 상승 Swing 폭이 대표폭보다 30% 이상 작음")
    if stats.representative_cycle_minutes and up[-1].minutes > stats.representative_cycle_minutes * 1.5:
        stats.fatigue += 15
        stats.fatigue_reasons.append("현재 상승 파동 시간이 대표 주기의 1.5배 초과")
    return stats


def horizon_state(completed_bars: int, remaining_minutes: int) -> tuple[str, float, int]:
    horizon = min(300, remaining_minutes) if remaining_minutes > 0 else 300
    observed = min(completed_bars, horizon)
    if observed < 30:
        return "EARLY_FORMING", 20.0, horizon
    if observed < 90:
        return "EARLY_PROJECTED", 40.0, horizon
    if observed < 180:
        return "PROJECTED_90", 60.0, horizon
    if observed < 300:
        return "PROJECTED_180", 75.0, horizon
    return "OBSERVED_300", 90.0, horizon


def persistence_score(
    frame: pd.DataFrame,
    *,
    regime: Regime,
    box_valid: bool,
    rvol: float,
    notional_rvol: float,
    spread_ok: bool,
    remaining_minutes: int,
) -> PersistenceResult:
    completed = _completed(frame)
    stats = swing_statistics(frame)
    state, confidence, horizon = horizon_state(len(completed), remaining_minutes)
    reasons: list[str] = []
    latest = completed.iloc[-1] if not completed.empty else None
    vwap_occupancy = 0.0
    if latest is not None:
        recent = completed.tail(min(60, len(completed)))
        vwap_occupancy = float((recent.close >= recent.vwap).mean() * 100)

    valid_width = bool(stats.representative_width_pct and 0.5 <= stats.representative_width_pct <= 5.0)
    structure_ok = regime == Regime.UP or box_valid
    liquidity_ok = rvol >= (0.8 if regime == Regime.RANGE else 1.0) and notional_rvol >= 0.85
    swing_consistency_points = stats.consistency * 0.20 if stats.valid_count >= 3 else 0.0
    width_points = 10.0 if valid_width and stats.valid_count >= 3 else 0.0
    vwap_points = (vwap_occupancy / 100) * 15.0 if regime == Regime.UP else (12.0 if box_valid else 0.0)
    structure_points = 15.0 if structure_ok else 0.0
    liquidity_points = 10.0 if liquidity_ok else 0.0
    cycle_points = min(10.0, stats.consistency * 0.10) if stats.representative_cycle_minutes else 0.0
    resilience_points = 10.0 if stats.fatigue < 20 else 5.0 if stats.fatigue < 40 else 0.0
    spread_points = 10.0 if spread_ok else 0.0
    score = int(round(_clamp(
        swing_consistency_points + width_points + vwap_points + structure_points + liquidity_points
        + cycle_points + resilience_points + spread_points - stats.fatigue * 0.10,
        0.0, 100.0,
    )))
    band = "PERSISTENT_A" if score >= 80 else "PERSISTENT_B" if score >= 70 else "WATCH" if score >= 60 else "UNSTABLE"
    if stats.valid_count < 3:
        reasons.append(f"유효 0.5~5.0% 상승 Swing 부족: {stats.valid_count}회 / 3회")
    if not valid_width:
        reasons.append("대표 반복폭이 아직 유효 범위로 확인되지 않았습니다.")
    if not liquidity_ok:
        reasons.append("3중 유동성 중 거래량 또는 거래대금 조건이 부족합니다.")
    if not spread_ok:
        reasons.append("호가 스프레드가 허용 기준을 넘거나 미수신입니다.")
    if not structure_ok:
        reasons.append("상승 구조 또는 유효 반복박스가 확인되지 않았습니다.")
    reasons.extend(stats.fatigue_reasons)
    if remaining_minutes and remaining_minutes < 45:
        reasons.append(f"남은 세션 {remaining_minutes}분: 신규 진입은 차단하고 청산·관리 중심으로 전환")
    return PersistenceResult(
        score=score,
        band=band,
        horizon_state=state,
        confidence_pct=confidence,
        horizon_minutes=horizon,
        swing=stats,
        vwap_occupancy_pct=round(vwap_occupancy, 1),
        structure_ok=structure_ok,
        liquidity_ok=liquidity_ok,
        spread_ok=spread_ok,
        remaining_minutes=remaining_minutes,
        new_entry_allowed=remaining_minutes >= 45,
        reasons=reasons,
    )


def risk_state(
    frame: pd.DataFrame,
    *,
    current_price: float,
    support: float | None,
    fake_breakdown: bool,
) -> RiskResult:
    if support is None or current_price <= 0:
        return RiskResult("미확인", None, None, 1, ["완료된 Swing 지지가 없어 Soft/Hard Stop을 계산하지 않습니다."])
    completed = _completed(frame)
    if completed.empty:
        return RiskResult("미확인", support, None, 1, ["완료 1분봉이 부족합니다."])
    stats = swing_statistics(frame)
    latest = completed.iloc[-1]
    median_range = float((completed.high - completed.low).tail(min(20, len(completed))).median())
    down_noise = 0.0
    if stats.down_swings:
        down_noise = float(median(item.width_pct for item in stats.down_swings)) / 100 * support
    noise_buffer = max(float(latest.atr) * 0.80, median_range * 1.20, down_noise)
    hard = max(0.0, support - noise_buffer)
    recovery = int(round(_clamp((stats.representative_down_minutes or 4.0) * 0.25, 1.0, 4.0)))
    sell_pressure = bool(latest.close < latest.open and latest.rvol >= 1.5)
    two_closes_below = bool(len(completed) >= 2 and completed.close.iloc[-1] < support and completed.close.iloc[-2] < support)
    if current_price <= hard:
        return RiskResult("HARD_EXIT", support, hard, recovery, ["Hard Stop 도달: 정상 노이즈 완충 범위 아래"])
    if two_closes_below and sell_pressure:
        return RiskResult("REAL_BREAKDOWN", support, hard, recovery, ["지지 아래 연속 완료봉과 매도 거래량 증가"])
    if current_price < support:
        return RiskResult("WARNING", support, hard, recovery, [f"Soft Stop 아래: 최대 {recovery}분 회복 여부 확인"])
    if fake_breakdown:
        return RiskResult("SHAKEOUT", support, hard, recovery, ["지지 이탈 뒤 완료봉 회복: 즉시 손절 대신 회복 상태 관찰"])
    if current_price < float(latest.ema9):
        return RiskResult("NORMAL_PULLBACK", support, hard, recovery, ["EMA9 아래 정상 눌림 구간"])
    return RiskResult("NORMAL_SWING", support, hard, recovery, ["지지와 구조가 유지되는 정상 상승 파동"])


def final_buy_decision(
    *,
    persistence: PersistenceResult,
    risk: RiskResult,
    session_ok: bool,
    data_fresh: bool,
    execution_ok: bool,
    entry_zone_ok: bool,
    reward_risk_ok: bool,
    cooldown_active: bool,
    hard_kill: bool,
    calibration_probability: float | None,
    calibration_samples: int,
    calibration_expectancy_pct: float | None = None,
    require_repeat_swing: bool = True,
    minimum_persistence_score: int = 70,
) -> FinalDecision:
    repeat_swing_ok = (
        persistence.swing.valid_count >= 3
        and bool(persistence.swing.representative_width_pct and 0.5 <= persistence.swing.representative_width_pct <= 5.0)
    )
    gates = {
        "세션": session_ok,
        "남은 장시간": persistence.new_entry_allowed,
        "데이터 최신": data_fresh,
        "반복 Swing 구조": repeat_swing_ok if require_repeat_swing else True,
        "지속성": persistence.score >= minimum_persistence_score,
        "전략 구조": persistence.structure_ok,
        "진입 위치": entry_zone_ok,
        "실행 안전": execution_ok,
        "손익비": reward_risk_ok,
        "위험 상태": risk.state not in {"REAL_BREAKDOWN", "HARD_EXIT"},
        "쿨다운": not cooldown_active,
        "Hard Kill": not hard_kill,
        # 동일 시장·세션·전략·점수 구간의 실제 전체 경로 검증 100건을 채운 뒤에만
        # 80% 적중과 비용 반영 기대값을 신호 관문에 적용한다.
        "80% 전체 경로 실측": calibration_samples < MIN_COMPLETE_PATH_SAMPLES or calibration_probability is None or calibration_probability >= 80.0,
        "비용 반영 기대값": calibration_samples < MIN_COMPLETE_PATH_SAMPLES or calibration_expectancy_pct is None or calibration_expectancy_pct > 0,
    }
    reasons = [name for name, passed in gates.items() if not passed]
    return FinalDecision(all(gates.values()), gates, reasons)
