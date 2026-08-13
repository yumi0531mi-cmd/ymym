# -*- coding: utf-8 -*-
"""반복단타 스캐너 v5.1 공통 지속성 엔진.

목표:
- 0.5~5.0% 실제 Swing 반복
- TREND_SWING / RANGE_SWING
- 장초반=추정, 장중=실측 강화, 장후반=남은 세션 기준
- 5시간(300분) Persistence Score + Confidence
- 3중 유동성
- 패턴 피로도
- Cooldown / Hard-Kill 상태관리
- 후보판/집중분석/검증기에서 같은 FINAL_BUY 규칙 사용

주의:
calibrated_probability는 DB 표본 30건 이상일 때만 별도로 채운다.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except Exception:
    pd = None

KST = timezone(timedelta(hours=9), name="KST")
ET = ZoneInfo("America/New_York")

VERSION = "v5.1-persistence-cycle"
SWING_MIN = 0.50
SWING_MAX = 5.00
MIN_SWINGS = 3
TARGET_HORIZON_MIN = 300


def _num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _series(item: dict, key: str) -> list[float]:
    out = []
    for v in item.get(key, []) or []:
        x = _num(v, float("nan"))
        if math.isfinite(x):
            out.append(x)
    return out


def _median(values: list[float], default: float = 0.0) -> float:
    vals = sorted(x for x in values if math.isfinite(x))
    if not vals:
        return default
    n = len(vals)
    m = n // 2
    return vals[m] if n % 2 else (vals[m - 1] + vals[m]) / 2.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _market_code(market: str) -> str:
    s = str(market or "").upper()
    return "KR" if s in {"KR", "국내"} or s.startswith("국내") else "US"


def _session_bounds(market: str, now: datetime) -> tuple[datetime | None, datetime | None, str]:
    code = _market_code(market)
    if code == "KR":
        local = now.astimezone(KST)
        if local.weekday() >= 5:
            return None, None, "국내 휴장"
        op = local.replace(hour=9, minute=0, second=0, microsecond=0)
        cl = local.replace(hour=15, minute=30, second=0, microsecond=0)
        return op, cl, "국내 정규장"

    local = now.astimezone(ET)
    if local.weekday() >= 5:
        return None, None, "미국 휴장"
    minute = local.hour * 60 + local.minute
    if 4 * 60 <= minute < 9 * 60 + 30:
        op = local.replace(hour=4, minute=0, second=0, microsecond=0)
        cl = local.replace(hour=9, minute=30, second=0, microsecond=0)
        return op, cl, "미국 프리마켓"
    if 9 * 60 + 30 <= minute < 16 * 60:
        op = local.replace(hour=9, minute=30, second=0, microsecond=0)
        cl = local.replace(hour=16, minute=0, second=0, microsecond=0)
        return op, cl, "미국 정규장"
    if 16 * 60 <= minute < 20 * 60:
        op = local.replace(hour=16, minute=0, second=0, microsecond=0)
        cl = local.replace(hour=20, minute=0, second=0, microsecond=0)
        return op, cl, "미국 애프터마켓"
    return None, None, "미국 장외시간"


def session_horizon(market: str, now_ts: float | None = None) -> dict:
    now = datetime.fromtimestamp(now_ts or time.time(), tz=KST)
    op, cl, label = _session_bounds(market, now)
    if op is None or cl is None:
        return {
            "session_label": label,
            "tradable": False,
            "elapsed_minutes": 0,
            "remaining_minutes": 0,
            "persistence_horizon_minutes": 0,
            "late_session": True,
        }
    local = now.astimezone(op.tzinfo)
    elapsed = max(0, int((local - op).total_seconds() // 60))
    remaining = max(0, int((cl - local).total_seconds() // 60))
    return {
        "session_label": label,
        "tradable": op <= local < cl,
        "elapsed_minutes": elapsed,
        "remaining_minutes": remaining,
        "persistence_horizon_minutes": min(TARGET_HORIZON_MIN, remaining),
        "late_session": remaining < 45,
    }


def _bar_age_seconds(item: dict, market: str, now_ts: float) -> float | None:
    times = item.get("chart_time_1m", []) or []
    if not times or pd is None:
        return None
    try:
        t = pd.Timestamp(pd.to_datetime(times[-1]))
        if t.tzinfo is None:
            t = t.tz_localize(KST if _market_code(market) == "KR" else ET)
        now = pd.Timestamp(datetime.fromtimestamp(now_ts, tz=timezone.utc))
        return max(0.0, (now - t.tz_convert("UTC")).total_seconds())
    except Exception:
        return None


def _turnover(item: dict, bars: int | None = None) -> float:
    closes = _series(item, "chart_close_1m")
    vols = _series(item, "chart_volume_1m")
    n = min(len(closes), len(vols))
    if n <= 0:
        return 0.0
    if bars is not None:
        n = min(n, bars)
    return sum(closes[-n + i] * vols[-n + i] for i in range(n))


def _liquidity_thresholds(market: str, session_label: str) -> tuple[float, float]:
    code = _market_code(market)
    if code == "KR":
        return 30_000_000_000.0, 3_000_000_000.0  # 300억 / 30억
    if "프리" in session_label:
        return 3_000_000.0, 500_000.0
    if "정규" in session_label:
        return 50_000_000.0, 5_000_000.0
    if "애프터" in session_label:
        return 2_000_000.0, 300_000.0
    return 5_000_000.0, 500_000.0


def tier_liquidity(item: dict, market: str, horizon: dict) -> dict:
    observed = min(
        len(item.get("chart_close_1m", []) or []),
        len(item.get("chart_volume_1m", []) or []),
    )
    day_value = _turnover(item, None)
    hour_value = _turnover(item, 60)

    day_base, hour_base = _liquidity_thresholds(market, horizon["session_label"])
    elapsed = max(1, horizon["elapsed_minutes"])
    # 장초반에는 하루 기준을 경과시간에 비례해 적용한다.
    if _market_code(market) == "KR":
        session_len = 390
    elif "프리" in horizon["session_label"]:
        session_len = 330
    elif "정규" in horizon["session_label"]:
        session_len = 390
    else:
        session_len = 240
    day_req = day_base * _clamp(elapsed / session_len, 0.08, 1.0)
    hour_req = hour_base * _clamp(min(observed, 60) / 60.0, 0.15, 1.0)

    rvol = _num(item.get("rvol"), 0.0)
    phase = str(item.get("swing_current_phase", "FORMING"))
    risk_state = str(item.get("post_entry_risk_state", "FORMING"))
    if risk_state == "UPSIDE_BREAKOUT":
        tier2_req = 1.50
    elif phase == "FALLING":
        tier2_req = 0.50
    else:
        tier2_req = 0.80

    spread = _num(item.get("verified_spread_percent", item.get("repeat_spread_percent")), -1.0)
    spread_limit = 0.35 if "레버리지" in str(item.get("asset_type", "")) else 0.25
    tier3_known = spread >= 0
    tier3_pass = (spread <= spread_limit) if tier3_known else True

    t1 = day_value >= day_req and hour_value >= hour_req
    t2 = rvol >= tier2_req if rvol > 0 else False
    return {
        "liquidity_tier1_pass": bool(t1),
        "liquidity_tier2_pass": bool(t2),
        "liquidity_tier3_pass": bool(tier3_pass),
        "liquidity_tier3_known": bool(tier3_known),
        "liquidity_day_value": day_value,
        "liquidity_hour_value": hour_value,
        "liquidity_day_required": day_req,
        "liquidity_hour_required": hour_req,
        "liquidity_rvol_required": tier2_req,
        "liquidity_spread_limit": spread_limit,
        "liquidity_score": (
            (40 if t1 else min(40, 40 * day_value / max(day_req, 1)))
            + (35 if t2 else min(35, 35 * rvol / max(tier2_req, 0.01)))
            + (25 if tier3_pass else 0)
        ),
    }


def _pattern_fatigue(item: dict) -> dict:
    widths = [_num(x) for x in (item.get("swing_width_samples") or []) if _num(x) > 0]
    representative = _num(item.get("swing_up_width_percent"))
    fatigue = 0.0
    reasons = []
    if len(widths) >= 3:
        last3 = widths[-3:]
        if last3[0] > last3[1] > last3[2]:
            fatigue += 25
            reasons.append("최근 3개 상승스윙 연속 축소")
        if representative > 0 and last3[-1] < representative * 0.70:
            fatigue += 20
            reasons.append("최근 스윙폭 대표값 대비 30% 이상 축소")
    cycle = _num(item.get("swing_cycle_duration_minutes"))
    elapsed = _num(item.get("swing_current_elapsed_minutes"))
    if cycle > 0 and elapsed > cycle * 1.5:
        fatigue += 15
        reasons.append("현재 파동시간 장기화")
    if str(item.get("intraday_regime_state", "")) in {"UPTREND_WEAKENING", "DOWNTREND"}:
        fatigue += 20
        reasons.append("큰 추세 약화")
    sell_share = _num(item.get("post_entry_sell_volume_share"), 0.5)
    if sell_share >= 0.62:
        fatigue += 15
        reasons.append("최근 매도거래량 비중 증가")
    vol_burst = _num(item.get("swing_volume_burst_ratio"), 1.0)
    if str(item.get("swing_current_phase", "")) == "RISING" and vol_burst < 0.60:
        fatigue += 10
        reasons.append("상승파동 거래량 약화")
    fatigue = _clamp(fatigue, 0, 100)
    return {"pattern_fatigue": fatigue, "pattern_fatigue_reasons": reasons}


def _strategy_type(item: dict) -> str:
    box_valid = bool(item.get("repeat_box_valid"))
    regime = str(item.get("intraday_regime_state", ""))
    hourly = str(item.get("hourly_structure_state", ""))
    if box_valid and regime not in {"DOWNTREND"} and hourly not in {"BEAR", "STRONG_BEAR"}:
        return "RANGE_SWING"
    if regime in {"STRONG_UPTREND", "UPTREND_PULLBACK", "UPTREND_WEAKENING"} or hourly in {"BULL", "STRONG_BULL"}:
        return "TREND_SWING"
    return "NONE"


def persistence_plan(item: dict, market: str, now_ts: float | None = None) -> dict:
    now_ts = now_ts or time.time()
    horizon = session_horizon(market, now_ts)
    out = dict(horizon)

    closes = _series(item, "chart_close_1m")
    observed = min(len(closes), 360)
    swing_count = int(_num(item.get("repeat_oscillation_count")))
    width = _num(item.get("swing_up_width_percent", item.get("repeat_width_percent")))
    consistency = _num(item.get("swing_width_consistency"))
    cycle = _num(item.get("swing_cycle_duration_minutes"))
    fatigue = _pattern_fatigue(item)
    liq = tier_liquidity(item, market, horizon)

    # 90/180/300분 가격흐름 안정성: 절대 수익률이 아니라 극단 붕괴 여부를 본다.
    def ret(minutes: int) -> float:
        if len(closes) > minutes and closes[-1 - minutes] > 0:
            return (closes[-1] / closes[-1 - minutes] - 1.0) * 100
        return 0.0
    r90, r180, r300 = ret(90), ret(180), ret(300)

    swing_score = 100 * _clamp(consistency, 0, 1)
    if swing_count < MIN_SWINGS:
        swing_score *= swing_count / MIN_SWINGS
    width_score = 100 if SWING_MIN <= width <= SWING_MAX else 0

    # VWAP 체류는 배열이 있으면 직접 계산.
    c = _series(item, "chart_close_1m")
    v = _series(item, "chart_vwap_1m")
    n = min(len(c), len(v), 180)
    if n >= 20:
        vwap_hold = sum(1 for a, b in zip(c[-n:], v[-n:]) if b > 0 and a >= b) / n
    else:
        vwap_hold = 1.0 if _num(item.get("price")) >= _num(item.get("vwap")) > 0 else 0.45

    hourly = str(item.get("hourly_structure_state", "FORMING"))
    regime = str(item.get("intraday_regime_state", "UNKNOWN"))
    box_valid = bool(item.get("repeat_box_valid"))
    structure_score = 50.0
    if hourly in {"BULL", "STRONG_BULL"}:
        structure_score += 25
    elif hourly in {"BEAR", "STRONG_BEAR"}:
        structure_score -= 35
    if regime in {"STRONG_UPTREND", "UPTREND_PULLBACK"}:
        structure_score += 20
    elif regime == "DOWNTREND":
        structure_score -= 35
    if box_valid:
        structure_score = max(structure_score, 75)
    structure_score = _clamp(structure_score, 0, 100)

    # 주기 안정성은 현재 파동이 대표주기에서 너무 멀어지는지로 보수 평가.
    elapsed = _num(item.get("swing_current_elapsed_minutes"))
    if cycle > 0:
        cycle_ratio = elapsed / cycle
        cycle_score = 100 if cycle_ratio <= 1.2 else _clamp(100 - (cycle_ratio - 1.2) * 70, 20, 100)
    else:
        cycle_score = 35

    # 회복력: shakeout은 회복으로 간주, warning/breakdown은 감점.
    risk_state = str(item.get("post_entry_risk_state", "FORMING"))
    recovery_score = {
        "NORMAL_SWING": 90, "NORMAL_PULLBACK": 85, "SHAKEOUT": 80,
        "WARNING": 45, "REAL_BREAKDOWN": 0, "HARD_EXIT": 0,
        "UPSIDE_BREAKOUT": 92, "FORMING": 45
    }.get(risk_state, 55)

    spread_score = 100 if liq["liquidity_tier3_pass"] else 0

    # 과거 90/180/300분이 심한 음수일수록 지속성 감점.
    long_penalty = 0.0
    for r, weight in ((r90, 5), (r180, 7), (r300, 8)):
        if r < -2.0:
            long_penalty += min(weight, abs(r) * 1.5)

    score = (
        0.20 * swing_score
        + 0.10 * width_score
        + 0.15 * (vwap_hold * 100)
        + 0.15 * structure_score
        + 0.10 * liq["liquidity_score"]
        + 0.10 * cycle_score
        + 0.10 * recovery_score
        + 0.10 * spread_score
        - 0.25 * fatigue["pattern_fatigue"]
        - long_penalty
    )
    score = _clamp(score, 0, 100)

    if observed >= 300:
        mode = "OBSERVED_300"
        confidence = 75 + min(20, (observed - 300) / 3)
    elif observed >= 180:
        mode = "PROJECTED_180"
        confidence = 58 + min(17, (observed - 180) / 7)
    elif observed >= 90:
        mode = "PROJECTED_90"
        confidence = 45 + min(13, (observed - 90) / 7)
    elif observed >= 30:
        mode = "EARLY_PROJECTED"
        confidence = 30 + min(15, (observed - 30) / 4)
    else:
        mode = "EARLY_FORMING"
        confidence = 15 + observed * 0.5

    confidence += min(8, swing_count * 1.5)
    confidence += max(0, consistency - 0.5) * 10
    confidence = _clamp(confidence, 10, 95)

    if score >= 80:
        grade = "PERSISTENT_A"
    elif score >= 70:
        grade = "PERSISTENT_B"
    elif score >= 60:
        grade = "WATCH"
    else:
        grade = "UNSTABLE"

    # 장후반에는 남은 시간에 한 번의 온전한 파동을 완료할 수 있어야 신규진입 가능.
    minimum_trade_time = max(20, int(cycle * 1.2)) if cycle > 0 else 30
    new_entry_time_ok = horizon["remaining_minutes"] >= minimum_trade_time
    if horizon["remaining_minutes"] < 45:
        new_entry_time_ok = False

    out.update(
        observed_minutes=observed,
        persistence_mode=mode,
        persistence_score=round(score, 2),
        persistence_confidence=round(confidence, 1),
        persistence_grade=grade,
        persistence_projected=mode != "OBSERVED_300",
        persistence_new_entry_time_ok=bool(new_entry_time_ok),
        persistence_min_trade_time=minimum_trade_time,
        persistence_return_90m=r90,
        persistence_return_180m=r180,
        persistence_return_300m=r300,
        persistence_vwap_hold_ratio=vwap_hold,
        persistence_structure_score=structure_score,
        persistence_cycle_score=cycle_score,
        **fatigue,
        **liq,
    )
    return out


def dynamic_recovery_window(item: dict) -> int:
    down_duration = _num(item.get("swing_down_duration_minutes"), 0)
    if down_duration <= 0:
        return 2
    return int(round(_clamp(down_duration * 0.25, 1, 4)))


def data_freshness_plan(item: dict, market: str, now_ts: float | None = None) -> dict:
    now_ts = now_ts or time.time()
    age = _bar_age_seconds(item, market, now_ts)
    quote_age = _num(item.get("quote_age_seconds"), 0)
    bar_ok = age is not None and age <= 180
    quote_ok = quote_age <= 15 if quote_age > 0 else True
    return {
        "data_freshness_pass": bool(bar_ok and quote_ok),
        "data_bar_age_seconds": age,
        "data_quote_age_seconds": quote_age if quote_age > 0 else None,
    }


def evaluate_strategy(
    item: dict,
    market: str,
    now_ts: float | None = None,
    cycle_state: dict | None = None,
    calibrated_probability: float | None = None,
    calibration_samples: int = 0,
) -> dict:
    """v5.1 단일 FINAL_BUY 판정."""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    now_ts = now_ts or time.time()
    persistence = persistence_plan(out, market, now_ts)
    fresh = data_freshness_plan(out, market, now_ts)
    out.update(persistence)
    out.update(fresh)

    strategy_type = _strategy_type(out)
    out["strategy_type_v51"] = strategy_type
    out["recovery_window_minutes"] = dynamic_recovery_window(out)

    state = cycle_state or {}
    cooldown_until = _num(state.get("cooldown_until"), 0)
    hard_kill = bool(state.get("hard_kill", False))
    cooldown = now_ts < cooldown_until
    out.update(
        cycle_cooldown_active=cooldown,
        cycle_cooldown_until=cooldown_until,
        cycle_hard_kill=hard_kill,
        cycle_breakdown_count=int(_num(state.get("breakdown_count"))),
        cycle_hard_exit_count=int(_num(state.get("hard_exit_count"))),
    )

    risk_state = str(out.get("post_entry_risk_state", "FORMING"))
    swing_valid = bool(out.get("swing_cycle_valid"))
    swing_count = int(_num(out.get("repeat_oscillation_count")))
    width = _num(out.get("swing_up_width_percent", out.get("repeat_width_percent")))
    buy_zone = str(out.get("repeat_state", out.get("repeat_scalp_state", ""))) in {
        "BUY_ZONE", "BUY_PULLBACK"
    }
    safety = bool(out.get("execution_safety_passed"))
    rr = _num(out.get("execution_effective_rr", out.get("risk_reward")))
    t1_score = _num(out.get("target1_before_stop_probability"), 0)

    # 초기 30분 미만은 관찰만. 90분 미만은 강한 조건에서만 허용할 수 있게 confidence gate를 둔다.
    observed = int(_num(out.get("observed_minutes")))
    if observed < 30:
        min_conf = 999
    elif observed < 90:
        min_conf = 42
    elif observed < 180:
        min_conf = 50
    else:
        min_conf = 55

    probability_gate = (
        calibrated_probability >= 60 if calibrated_probability is not None
        else t1_score >= 60
    )
    reasons = []
    checks = {
        "세션 거래가능": bool(out.get("tradable")),
        "남은시간 충분": bool(out.get("persistence_new_entry_time_ok")),
        "데이터 최신": bool(out.get("data_freshness_pass")),
        "Tier1 유동성": bool(out.get("liquidity_tier1_pass")),
        "Tier2 순간유동성": bool(out.get("liquidity_tier2_pass")),
        "Tier3 스프레드": bool(out.get("liquidity_tier3_pass")),
        "Swing 3회+": swing_valid and swing_count >= MIN_SWINGS,
        "대표폭 0.5~5%": SWING_MIN <= width <= SWING_MAX,
        "Persistence 70+": _num(out.get("persistence_score")) >= 70,
        "Persistence 신뢰": _num(out.get("persistence_confidence")) >= min_conf,
        "Trend/Range 분류": strategy_type in {"TREND_SWING", "RANGE_SWING"},
        "현재 매수구간": buy_zone,
        "실행 안전": safety,
        "RR 1.1+": rr >= 1.10,
        "진짜 붕괴 아님": risk_state not in {"REAL_BREAKDOWN", "HARD_EXIT"},
        "Cooldown 아님": not cooldown,
        "Hard Kill 아님": not hard_kill,
        "확률게이트": probability_gate,
    }
    for k, ok in checks.items():
        if not ok:
            reasons.append(k)

    final_buy = all(checks.values())

    # raw_score는 calibration 전용. UI에서는 '%'가 아니라 모델점수로 표시.
    raw = (
        0.50 * _num(out.get("persistence_score"))
        + 0.20 * _clamp(t1_score, 0, 100)
        + 0.10 * _num(out.get("liquidity_score"))
        + 0.10 * (100 - _num(out.get("pattern_fatigue")))
        + 0.10 * (100 if safety else 0)
    )
    if risk_state == "WARNING":
        raw -= 12
    elif risk_state in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        raw = min(raw, 20)
    raw = _clamp(raw, 0, 100)

    out.update(
        strategy_engine_version=VERSION,
        final_buy=bool(final_buy),
        final_buy_checks=checks,
        final_buy_reasons=reasons,
        model_raw_score=round(raw, 2),
        calibrated_probability=calibrated_probability,
        calibration_samples=int(calibration_samples),
        calibration_state=("보정완료" if calibrated_probability is not None and calibration_samples >= 30 else "보정전"),
        probability_gate_pass=bool(probability_gate),
    )
    return out


def update_cycle_state(item: dict, state: dict | None, now_ts: float | None = None) -> tuple[dict, dict]:
    """종목별 세션 상태. 실제 체결횟수는 추정하지 않고 붕괴/쿨다운/하드킬만 자동 관리한다."""
    now_ts = now_ts or time.time()
    s = dict(state or {})
    s.setdefault("cooldown_until", 0.0)
    s.setdefault("breakdown_count", 0)
    s.setdefault("hard_exit_count", 0)
    s.setdefault("hard_kill", False)
    s.setdefault("last_risk_event", "")

    risk = str(item.get("post_entry_risk_state", "FORMING"))
    times = item.get("chart_time_1m", []) or []
    bar_id = str(times[-1]) if times else str(int(now_ts // 60))
    event_id = f"{risk}:{bar_id}"

    if risk in {"REAL_BREAKDOWN", "HARD_EXIT"} and event_id != s.get("last_risk_event"):
        if risk == "REAL_BREAKDOWN":
            s["breakdown_count"] += 1
            s["cooldown_until"] = max(_num(s.get("cooldown_until")), now_ts + 10 * 60)
        else:
            s["hard_exit_count"] += 1
            s["cooldown_until"] = max(_num(s.get("cooldown_until")), now_ts + 15 * 60)
        s["last_risk_event"] = event_id

    if s["breakdown_count"] >= 2 or s["hard_exit_count"] >= 2:
        s["hard_kill"] = True

    out = evaluate_strategy(item, "KR" if str(item.get("exchange","")).upper()=="KR" else "US", now_ts, s,
                            item.get("calibrated_probability"), int(_num(item.get("calibration_samples"))))
    return s, out


def calibrated_from_db(
    db_path: str | Path,
    raw_score: float,
    strategy_type: str,
    min_samples: int = 30,
    bucket_width: int = 10,
) -> tuple[float | None, int]:
    """signals.detail_json의 model_raw_score를 사용한 단순 구간 보정.
    표본 30건 미만이면 None을 반환한다.
    """
    path = Path(db_path)
    if not path.exists():
        return None, 0
    bucket_lo = int(raw_score // bucket_width) * bucket_width
    bucket_hi = bucket_lo + bucket_width
    successes = []
    try:
        con = sqlite3.connect(path, timeout=3)
        rows = con.execute(
            """SELECT target1_before_stop,detail_json FROM signals
               WHERE result_done=1 AND target1_before_stop IS NOT NULL
               ORDER BY issued DESC LIMIT 2000"""
        ).fetchall()
        con.close()
        for result, detail in rows:
            try:
                d = json.loads(detail or "{}")
            except Exception:
                continue
            rs = _num(d.get("model_raw_score"), -1)
            st = str(d.get("strategy_type_v51", d.get("strategy_type", "")))
            if bucket_lo <= rs < bucket_hi and (not strategy_type or st == strategy_type):
                successes.append(int(bool(result)))
        if len(successes) < min_samples:
            return None, len(successes)
        return round(sum(successes) / len(successes) * 100, 1), len(successes)
    except Exception:
        return None, 0


def evaluate_live_quote_risk(item: dict, price: float) -> dict:
    """2~5초 quote 전용 위험판정.

    무거운 Swing/Persistence를 다시 계산하지 않고, 마지막 확정 구조의
    Soft/Hard Stop과 현재가만 비교한다. 구조 붕괴 확정은 1분봉 엔진이 담당한다.
    """
    out = dict(item or {})
    try:
        px = float(price or 0)
    except Exception:
        px = 0.0
    if px <= 0:
        out["live_risk_quote_valid"] = False
        return out

    out["price"] = px
    soft = _num(out.get("post_entry_soft_stop", out.get("soft_stop_price")))
    hard = _num(out.get("post_entry_hard_stop", out.get("hard_stop_price", out.get("stop_loss"))))
    state = str(out.get("post_entry_risk_state") or "FORMING")

    if hard > 0 and px <= hard:
        out.update(
            post_entry_risk_state="HARD_EXIT",
            post_entry_risk_label="🚨 Hard Stop 실시간 이탈",
            post_entry_action="즉시손절",
        )
    elif soft > 0 and px < soft and state not in {"REAL_BREAKDOWN", "HARD_EXIT"}:
        out.update(
            post_entry_risk_state="WARNING",
            post_entry_risk_label="🟠 Soft Stop 아래 · 1분봉 회복/붕괴 확인 중",
            post_entry_action="신규진입중지·회복확인",
        )

    out["live_risk_quote_valid"] = True
    out["live_risk_price"] = px
    return out
