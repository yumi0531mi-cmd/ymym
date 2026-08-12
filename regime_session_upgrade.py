# -*- coding: utf-8 -*-
"""scalp_app.py와 run_live_validation.py가 함께 쓰는 공통 초단타 엔진.

중복 방지 원칙:
- 세션 판정은 이 파일에서만 정의한다.
- 반복단타 1분봉 지지/저항·ATR 계산은 이 파일에서만 정의한다.
- 1차/2차 목표 도달확률·ETA·손절 선도달 위험도 이 파일에서만 정의한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

KST = timezone(timedelta(hours=9), name="KST")
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionState:
    tradable: bool
    session_name: str
    local_time: str


def kr_session(now_utc: datetime | None = None) -> SessionState:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(KST)
    minute = now.hour * 60 + now.minute
    tradable = now.weekday() < 5 and 9 * 60 <= minute < 15 * 60 + 30
    return SessionState(
        tradable,
        "국내 정규장" if tradable else "국내 장외",
        now.strftime("%H:%M:%S KST"),
    )


def us_session(now_utc: datetime | None = None) -> SessionState:
    """미국 프리/정규/애프터를 ET 기준으로 판정한다.

    브로커의 별도 주간거래 지원 여부는 KIS 공지/계좌 조건에 따라 달라질 수 있으므로
    이 공통 함수에서는 표준 프리·정규·애프터만 거래 가능 세션으로 잡는다.
    """
    now = (now_utc or datetime.now(timezone.utc)).astimezone(ET)
    minute = now.hour * 60 + now.minute
    weekday = now.weekday() < 5
    if weekday and 4 * 60 <= minute < 9 * 60 + 30:
        name, tradable = "미국 프리마켓", True
    elif weekday and 9 * 60 + 30 <= minute < 16 * 60:
        name, tradable = "미국 정규장", True
    elif weekday and 16 * 60 <= minute < 20 * 60:
        name, tradable = "미국 애프터마켓", True
    else:
        name, tradable = "미국 장외시간", False
    kst = now.astimezone(KST)
    return SessionState(
        tradable,
        name,
        f"{now.strftime('%H:%M:%S')} ET / {kst.strftime('%H:%M:%S')} KST",
    )


def session_for(market_code: str, now_utc: datetime | None = None) -> SessionState:
    return kr_session(now_utc) if str(market_code).upper() == "KR" else us_session(now_utc)


def _num(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default

def _numeric_list(item, key):
    values = item.get(key, []) or []
    result = []
    for value in values:
        number = _num(value, float("nan"))
        if math.isfinite(number):
            result.append(number)
    return result

def _dedupe_levels(levels, tolerance_pct=0.06):
    clean = sorted(x for x in (_num(v) for v in levels) if x > 0)
    result = []
    for level in clean:
        if not result:
            result.append(level)
            continue
        if abs(level / result[-1] - 1.0) * 100 <= tolerance_pct:
            result[-1] = max(result[-1], level)
        else:
            result.append(level)
    return result

def _intraday_ohlcv(item):
    opens = _numeric_list(item, "chart_open_1m")
    highs = _numeric_list(item, "chart_high_1m")
    lows = _numeric_list(item, "chart_low_1m")
    closes = _numeric_list(item, "chart_close_1m")
    raw_volumes = item.get("chart_volume_1m", []) or []
    n = min(len(opens), len(highs), len(lows), len(closes))
    if n < 12:
        return pd.DataFrame()

    opens, highs, lows, closes = opens[-n:], highs[-n:], lows[-n:], closes[-n:]
    volumes = [_num(v) for v in raw_volumes[-n:]]
    if len(volumes) < n:
        volumes = [0.0] * (n - len(volumes)) + volumes

    times = list(item.get("chart_time_1m", []) or [])
    if len(times) >= n:
        times = times[-n:]
    else:
        times = list(range(n))

    frame = pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    frame = frame[
        (frame["open"] > 0)
        & (frame["high"] > 0)
        & (frame["low"] > 0)
        & (frame["close"] > 0)
        & (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
    ].copy()
    if frame.empty:
        return frame

    # 장/세션 사이의 큰 공백이 있으면 마지막 연속 분봉 구간만 사용해
    # 전일 저항이 당일 초단타 목표에 섞이는 것을 줄인다.
    try:
        parsed = pd.to_datetime(frame["time"], errors="coerce")
        if parsed.notna().sum() >= max(12, len(frame) // 2):
            frame["_parsed_time"] = parsed
            frame = frame.sort_values("_parsed_time").reset_index(drop=True)
            gaps = frame["_parsed_time"].diff().dt.total_seconds().fillna(0)
            gap_rows = frame.index[gaps > 45 * 60].tolist()
            if gap_rows:
                frame = frame.iloc[gap_rows[-1]:].copy()
    except Exception:
        pass

    return frame.tail(180).reset_index(drop=True)

def _atr_and_range(frame):
    if frame.empty:
        return 0.0, 0.0
    prev = frame["close"].shift(1)
    tr = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev).abs(),
            (frame["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = _num(tr.tail(14).mean())
    median_range = _num((frame["high"] - frame["low"]).tail(20).median())
    return atr14, median_range

def _swing_levels(values, kind="high"):
    levels = []
    if len(values) < 7:
        return levels
    for i in range(2, len(values) - 2):
        window = values[i - 2:i + 3]
        value = values[i]
        if kind == "high" and value >= max(window):
            levels.append(value)
        elif kind == "low" and value <= min(window):
            levels.append(value)
    return _dedupe_levels(levels)

def apply_repeat_scalp_overlay(item, market_code):
    """기존 엔진 결과를 보존하면서 반복단타 전용 차트 레벨만 추가한다."""
    if not isinstance(item, dict):
        return item
    item = dict(item)
    price = _num(item.get("price"))
    frame = _intraday_ohlcv(item)
    if price <= 0 or len(frame) < 20:
        item.update(
            repeat_chart_valid=False,
            repeat_chart_reason="현재가 또는 연속 1분봉 20개 미만",
            repeat_candidate=False,
        )
        return item

    highs = frame["high"].tolist()
    lows = frame["low"].tolist()
    closes = frame["close"].tolist()
    volumes = frame["volume"].tolist()
    atr14, median_range = _atr_and_range(frame)
    if atr14 <= 0:
        atr14 = median_range
    if median_range <= 0:
        median_range = atr14

    vwap = _num(item.get("vwap"))
    ema9 = _num(item.get("ema9"))
    ema20 = _num(item.get("ema20"))
    rsi = _num(item.get("rsi"), 50.0)
    macd = _num(item.get("macd_histogram"))
    rvol = _num(item.get("rvol"))
    f10 = _num(item.get("forecast_10m"))
    f30 = _num(item.get("forecast_30m"))

    bid = _num(item.get("best_bid"))
    ask = _num(item.get("best_ask"))
    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if ask >= bid > 0 else None
    spread_limit = 0.35 if "레버리지" in str(item.get("asset_type", "")) else 0.25
    quality_checks = {
        "분봉 실데이터": not bool(item.get("intraday_fallback")),
        "VWAP 확인": vwap > 0,
        "EMA9·20 확인": ema9 > 0 and ema20 > 0,
        "호가 스프레드": spread is None or spread <= spread_limit,
    }
    quality_pass = all(quality_checks.values())

    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] > 0 else 0.0
    ret15 = (closes[-1] / closes[-16] - 1) * 100 if len(closes) >= 16 and closes[-16] > 0 else 0.0
    ret30 = (closes[-1] / closes[-31] - 1) * 100 if len(closes) >= 31 and closes[-31] > 0 else 0.0

    recent_hi = max(highs[-6:])
    previous_hi = max(highs[-12:-6]) if len(highs) >= 12 else recent_hi
    recent_lo = min(lows[-6:])
    previous_lo = min(lows[-12:-6]) if len(lows) >= 12 else recent_lo

    up_volume = down_volume = 0.0
    start = max(1, len(closes) - 20)
    for i in range(start, len(closes)):
        vol = volumes[i] if i < len(volumes) else 0.0
        if closes[i] > closes[i - 1]:
            up_volume += vol
        elif closes[i] < closes[i - 1]:
            down_volume += vol
    volume_ratio = up_volume / down_volume if down_volume > 0 else (2.0 if up_volume > 0 else 0.0)

    trend_checks = {
        "VWAP 위": price > vwap > 0,
        "EMA 정배열": price >= ema9 >= ema20 > 0,
        "5분 상승": ret5 > 0,
        "15분 상승": ret15 >= 0,
        "30분 상승": ret30 >= -0.15,
        "고점 상승": recent_hi >= previous_hi,
        "저점 상승": recent_lo >= previous_lo,
        "상승봉 거래량 우세": volume_ratio >= 1.05,
        "RSI 과열 아님": rsi < (82 if market_code == "US" else 78),
        "10분 전망 급락 아님": f10 > -0.40,
    }
    trend_score = sum(bool(v) for v in trend_checks.values())

    swing_lows = _swing_levels(lows, "low")
    support_candidates = [(x, "1분봉 스윙 저점") for x in swing_lows if 0 < x < price]
    recent_low = min(lows[-20:])
    if 0 < recent_low < price:
        support_candidates.append((recent_low, "최근 20분 저점"))
    if 0 < vwap < price:
        support_candidates.append((vwap, "VWAP"))
    if 0 < ema9 < price:
        support_candidates.append((ema9, "EMA9"))
    if 0 < ema20 < price:
        support_candidates.append((ema20, "EMA20"))

    if not support_candidates:
        item.update(
            repeat_chart_valid=False,
            repeat_chart_reason="현재가 아래 차트 지지선 미확인",
            repeat_candidate=False,
            repeat_trend_score=trend_score,
            repeat_trend_checks=trend_checks,
        )
        return item

    support, support_basis = max(support_candidates, key=lambda x: x[0])

    swing_highs = _swing_levels(highs, "high")
    # 반복단타 1차 목표는 지지선 대비 최소 +0.5% 공간이 있는 첫 의미 있는 저항만 사용한다.
    min_repeat_target = support * 1.005
    resistance_candidates = [x for x in swing_highs if x > price and x >= min_repeat_target]
    box_high = max(highs[-30:])
    box_low = min(lows[-30:])
    box_width = max(0.0, box_high - box_low)
    prior_high = max(highs[-30:-1]) if len(highs) >= 31 else max(highs[:-1])
    for level in (box_high, prior_high):
        if level > price and level >= min_repeat_target:
            resistance_candidates.append(level)
    resistance_candidates = _dedupe_levels(resistance_candidates)

    target1 = 0.0
    target2 = 0.0
    target1_basis = ""
    target2_basis = ""
    if resistance_candidates:
        target1 = resistance_candidates[0]
        target1_basis = "현재가 위 가장 가까운 실제 1분봉 저항"
        higher = [x for x in resistance_candidates[1:] if x > target1 * 1.0005]
        if higher:
            target2 = higher[0]
            target2_basis = "1차 위 다음 실제 1분봉 저항"
    elif trend_score >= 7:
        # 신저가/신고가 돌파로 실제 저항이 아직 없을 때만 최근 ATR·봉폭을 투영한다.
        projection1 = max(atr14 * 1.25, median_range * 1.50)
        if box_width > 0:
            projection1 = min(projection1, max(atr14 * 2.20, box_width * 0.50))
        target1 = price + projection1
        target1_basis = "신고가 구간 · 최근 1분봉 ATR/박스폭 투영"

    if target1 > price and target2 <= target1 and trend_score >= 6:
        projection2 = max(atr14 * 1.35, median_range * 1.75, (target1 - price) * 0.80)
        if box_width > 0:
            projection2 = min(projection2, max(atr14 * 2.40, box_width * 0.60))
        target2 = target1 + projection2
        target2_basis = "다음 저항 미형성 · ATR/최근 박스폭 보수 투영"

    if not (0 < support < price < target1):
        item.update(
            repeat_chart_valid=False,
            repeat_chart_reason="지지 < 현재가 < 1차 목표 가격순서 불충족",
            repeat_candidate=False,
            repeat_support=support,
            repeat_trend_score=trend_score,
            repeat_trend_checks=trend_checks,
        )
        return item

    stop_buffer = max(atr14 * 0.35, median_range * 0.45)
    if stop_buffer <= 0:
        stop_buffer = support * 0.0025
    stop_buffer = min(stop_buffer, support * 0.0060)
    stop = max(0.0, support - stop_buffer)
    entry = support

    repeat_width = (target1 / entry - 1) * 100 if target1 > entry > 0 else 0.0
    t1_from_current = (target1 / price - 1) * 100
    t2_from_current = (target2 / price - 1) * 100 if target2 > price else 0.0
    extra_after_t1 = (target2 / target1 - 1) * 100 if target2 > target1 > 0 else 0.0
    risk = entry - stop
    reward = target1 - entry
    repeat_rr = reward / risk if risk > 0 else 0.0

    continuation_checks = {
        "2차 차트 목표 존재": target2 > target1,
        "VWAP 위 유지": price > vwap > 0,
        "EMA 정배열": ema9 >= ema20 > 0,
        "15분 실제 상승": ret15 > 0,
        "30분 약세 아님": ret30 >= -0.10,
        "상승봉 거래량 우세": volume_ratio >= 1.05,
        "MACD 비약세": macd >= 0,
        "RVOL 확보": rvol >= 0.80,
        "10분 전망 약세 아님": f10 > -0.25,
        "30분 전망 약세 아님": f30 > -0.35,
    }
    continuation_score = sum(bool(v) for v in continuation_checks.values())
    if target2 <= target1:
        continuation_state = "NONE"
        continuation_label = "⚪ 2차 목표 미확인"
    elif continuation_score >= 8:
        continuation_state = "HIGH"
        continuation_label = "🟢 추가상승 가능성 높음"
    elif continuation_score >= 6:
        continuation_state = "MID"
        continuation_label = "🟡 추가상승 가능·1차 후 확인"
    else:
        continuation_state = "LOW"
        continuation_label = "🔴 1차 부근 상승 제한 가능"

    near = max(median_range * 0.75, atr14 * 0.35)
    if price <= support:
        repeat_state = "BREAKDOWN"
        repeat_label = "🔴 지지 이탈"
    elif price >= target1 - near:
        repeat_state = "TAKE_PROFIT"
        repeat_label = "🟠 1차 매도구간 근접"
    elif price <= support + near and trend_score >= 6:
        repeat_state = "BUY_ZONE"
        repeat_label = "🟢 반복 매수구간 근접"
    elif trend_score >= 6:
        repeat_state = "WAIT_PULLBACK"
        repeat_label = "🟡 지지 눌림 대기"
    else:
        repeat_state = "WAIT_TREND"
        repeat_label = "⚪ 추세 재확인"

    preferred = 0.50 <= repeat_width <= 1.50
    candidate = bool(
        preferred
        and trend_score >= 6
        and repeat_state not in {"BREAKDOWN", "TAKE_PROFIT"}
        and repeat_rr >= 1.20
        and quality_pass
    )

    item.update(
        repeat_chart_valid=True,
        repeat_chart_reason="연속 1분봉 지지·저항/ATR 계산 완료",
        repeat_candidate=candidate,
        repeat_entry=entry,
        repeat_support=support,
        repeat_stop=stop,
        repeat_target1=target1,
        repeat_target2=target2,
        repeat_width_percent=repeat_width,
        repeat_target1_current_upside=t1_from_current,
        repeat_target2_current_upside=t2_from_current,
        repeat_extra_after_target1=extra_after_t1,
        repeat_risk_reward=repeat_rr,
        repeat_state=repeat_state,
        repeat_label=repeat_label,
        repeat_trend_score=trend_score,
        repeat_trend_checks=trend_checks,
        repeat_support_basis=support_basis,
        repeat_target1_basis=target1_basis,
        repeat_target2_basis=target2_basis,
        repeat_atr14=atr14,
        repeat_median_range=median_range,
        repeat_volume_ratio=volume_ratio,
        repeat_continuation_state=continuation_state,
        repeat_continuation_label=continuation_label,
        repeat_continuation_score=continuation_score,
        repeat_continuation_checks=continuation_checks,
        repeat_preferred_range=preferred,
        repeat_quality_pass=quality_pass,
        repeat_quality_checks=quality_checks,
        repeat_spread_percent=spread,
        repeat_spread_limit=spread_limit,
        repeat_chart_box_low=box_low,
        repeat_chart_box_high=box_high,
    )
    return item


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def target_reach_probability_plan(item: dict) -> dict:
    """현재 차트 조건이 유지된다는 전제에서 목표 도달확률과 ETA를 계산한다.

    확률은 확정값이 아니라 모델 추정치다. forward_forecasts의 중심/범위,
    60분봉 구조, VWAP 체류, 반복추세, RVOL, 스프레드, 목표 거리와
    손절거리의 비대칭을 합쳐 계산한다.
    """
    if not isinstance(item, dict):
        return item
    item = dict(item)
    price = _num(item.get("price"))
    t1 = _num(item.get("structural_target1", item.get("repeat_target1")))
    t2 = _num(item.get("structural_target2", item.get("repeat_target2")))
    stop = _num(item.get("stop_loss", item.get("repeat_stop")))
    forecasts = item.get("forward_forecasts") or {}

    if price <= 0 or t1 <= price or not forecasts:
        item.update(
            target1_reach_probability=0.0,
            target2_reach_probability=0.0,
            target1_eta_minutes=0,
            target2_eta_minutes=0,
            stop_first_risk_probability=0.0,
            target_probability_label="자료 부족",
        )
        return item

    hourly_state = str(item.get("hourly_structure_state", "FORMING"))
    regime = str(item.get("intraday_regime_state", "UNKNOWN"))
    vwap_hold = _num(item.get("intraday_vwap_hold_ratio"))
    trend_score = _num(item.get("repeat_trend_score", item.get("intraday_uptrend_score")))
    rvol = _num(item.get("rvol"))
    spread = item.get("verified_spread_percent", item.get("repeat_spread_percent"))
    spread = _num(spread, 0.0) if spread is not None else 0.0

    structure_bonus = 0.0
    if hourly_state == "STRONG_BULL":
        structure_bonus += 8.0
    elif hourly_state == "BULL":
        structure_bonus += 4.0
    elif hourly_state == "STRONG_BEAR":
        structure_bonus -= 12.0
    elif hourly_state == "BEAR":
        structure_bonus -= 7.0

    if regime == "STRONG_UPTREND":
        structure_bonus += 7.0
    elif regime == "UPTREND_PULLBACK":
        structure_bonus += 5.0
    elif regime in {"DOWNTREND", "DOWNTREND_REVERSAL"}:
        structure_bonus -= 12.0

    structure_bonus += _clamp((vwap_hold - 0.55) * 24.0, -6.0, 7.0)
    structure_bonus += _clamp((trend_score - 6.0) * 1.8, -5.0, 7.0)
    structure_bonus += _clamp((rvol - 0.8) * 2.0, -3.0, 4.0)
    if spread > 0.25:
        structure_bonus -= min(8.0, (spread - 0.25) * 20.0)

    def horizon_stats(target: float):
        distance = (target / price - 1.0) * 100.0
        rows = []
        for h in (5, 15, 30, 60):
            f = forecasts.get(h) or forecasts.get(str(h)) or {}
            center = _num(f.get("center_pct"))
            lo = _num(f.get("low_pct"))
            hi = _num(f.get("high_pct"))
            up_prob = _num(f.get("up_probability"), 50.0)
            # 표시 범위를 약 80% 구간으로 보고 표준편차를 근사
            sigma = max(0.05, abs(hi - lo) / 2.56)
            endpoint_prob = 1.0 - _normal_cdf((distance - center) / sigma)
            # 장중에는 종가가 목표 아래여도 중간에 터치할 수 있으므로 touch 보정
            touch_prob = endpoint_prob * 1.16 + max(0.0, up_prob - 50.0) / 350.0
            touch_prob = _clamp(touch_prob * 100.0 + structure_bonus, 2.0, 97.0)
            rows.append((h, touch_prob, center, lo, hi))
        best_prob = max(r[1] for r in rows)
        eta = 0
        for h, p, center, lo, hi in rows:
            if hi >= distance and p >= 50.0:
                eta = h
                break
        if eta == 0 and best_prob >= 45:
            eta = 60
        return distance, rows, best_prob, eta

    t1_distance, t1_rows, p1, eta1 = horizon_stats(t1)

    projected1 = "투영" in str(item.get("target1_basis", ""))
    if projected1:
        p1 -= 5.0

    p2, eta2 = 0.0, 0
    if t2 > t1:
        _, t2_rows, p2, eta2 = horizon_stats(t2)
        if "투영" in str(item.get("target2_basis", "")):
            p2 -= 7.0
        p2 = min(p2, p1 * 0.92)

    # 손절 선도달 위험: 예측 하단이 손절까지 닿을 가능성을 같은 방식으로 근사
    stop_risk = 0.0
    if 0 < stop < price:
        down_distance = (stop / price - 1.0) * 100.0  # 음수
        risks = []
        for h in (5, 15, 30, 60):
            f = forecasts.get(h) or forecasts.get(str(h)) or {}
            center = _num(f.get("center_pct"))
            lo = _num(f.get("low_pct"))
            hi = _num(f.get("high_pct"))
            sigma = max(0.05, abs(hi - lo) / 2.56)
            endpoint_down = _normal_cdf((down_distance - center) / sigma)
            touch_down = _clamp(endpoint_down * 1.18 * 100.0, 1.0, 96.0)
            risks.append(touch_down)
        stop_risk = max(risks)
        if hourly_state in {"STRONG_BEAR", "BEAR"}:
            stop_risk += 6.0
        if regime in {"DOWNTREND", "DOWNTREND_REVERSAL"}:
            stop_risk += 8.0

    p1 = _clamp(p1, 1.0, 97.0)
    p2 = _clamp(p2, 0.0, min(95.0, p1))
    stop_risk = _clamp(stop_risk, 1.0 if stop > 0 else 0.0, 96.0)

    # 실제 매매 관점의 핵심 값: 1차가 손절보다 먼저 올 조건부 확률
    first_hit = _clamp(p1 * (1.0 - stop_risk / 125.0), 1.0, 96.0)
    if first_hit >= 78:
        label = "🟢 1차 선도달 가능성 높음"
    elif first_hit >= 68:
        label = "🟡 1차 선도달 우세"
    elif first_hit >= 55:
        label = "⚪ 우위 약함"
    else:
        label = "🔴 목표 선도달 신뢰 낮음"

    item.update(
        target1_reach_probability=round(p1, 1),
        target2_reach_probability=round(p2, 1),
        target1_eta_minutes=int(eta1),
        target2_eta_minutes=int(eta2),
        stop_first_risk_probability=round(stop_risk, 1),
        target1_before_stop_probability=round(first_hit, 1),
        target_probability_label=label,
        target_probability_model="조건부 차트확률: 예측범위+60분구조+VWAP+추세+RVOL+스프레드+목표/손절거리",
    )
    return item
