# -*- coding: utf-8 -*-
"""KIS 초단타 신호 무인 수집·사후검증기.

scalp_app.py의 0.5~1.5% 반복폭, 실제 1차/2차 목표, 추가상승 판정을
signals.detail_json에 보존한다. +1/+2/+3%는 검증 통계 전용이며 매매 목표가와 무관하다.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
import html
import importlib.abc
import importlib.util
import json
import logging
import math
import os
import signal
import sqlite3
import sys
import time
import zlib

import pandas as pd
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # regime_session_upgrade.py를 같은 폴더에서 임포트하기 위함
from regime_session_upgrade import session_for  # noqa: E402

KST = timezone(timedelta(hours=9), name="KST")
DB_PATH = ROOT / "validation_data" / "live_validation.sqlite3"
LOG_PATH = ROOT / "validation_data" / "collector.log"
CSV_PATH = ROOT / "validation_data" / "validation_summary.csv"
STOP = False

KR_UNIVERSE = [
    ("005930", "삼성전자", "KR"), ("000660", "SK하이닉스", "KR"),
    ("035420", "NAVER", "KR"), ("005380", "현대차", "KR"),
    ("069500", "KODEX 200", "KR"), ("102110", "TIGER 200", "KR"),
    ("396500", "TIGER 반도체TOP10", "KR"), ("122630", "KODEX 레버리지", "KR"),
    ("123320", "TIGER 레버리지", "KR"), ("488080", "TIGER 반도체TOP10레버리지", "KR"),
]
US_UNIVERSE = [
    ("GOOGL", "알파벳", "NASDAQ"), ("AMD", "AMD", "NASDAQ"),
    ("INTC", "인텔", "NASDAQ"), ("SMH", "반도체 ETF", "NASDAQ"),
    ("SOXL", "반도체 3배 레버리지 ETF", "AMEX"), ("SOXS", "반도체 3배 인버스 ETF", "AMEX"),
    ("TQQQ", "나스닥100 3배 레버리지 ETF", "NASDAQ"), ("SQQQ", "나스닥100 3배 인버스 ETF", "NASDAQ"),
]


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


def load_engine():
    source_path = ROOT / "app.py"
    if not source_path.exists():
        development_copy = ROOT.parent / "ymym_stock_scanner_fixed" / "app.py"
        if development_copy.exists():
            source_path = development_copy
    if not source_path.exists():
        raise FileNotFoundError("run_live_validation.py와 같은 폴더에 app.py가 필요합니다.")

    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    bundled = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_BUNDLED" for target in node.targets):
            bundled = ast.literal_eval(node.value)
            break
    if not isinstance(bundled, dict):
        raise RuntimeError("app.py에서 번들 KIS 엔진을 찾지 못했습니다.")

    class Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        packages = {"scanner", "utils", "config", "engine", "data", "ui"}

        def find_spec(self, fullname, path=None, target=None):
            if fullname in bundled:
                return importlib.util.spec_from_loader(fullname, self, is_package=False)
            if fullname in self.packages:
                return importlib.util.spec_from_loader(fullname, self, is_package=True)
            return None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            name = module.__name__
            if name in self.packages:
                module.__path__ = []
                module.__package__ = name
                module.__file__ = str(ROOT / name / "__init__.py")
                return
            code = zlib.decompress(base64.b64decode(bundled[name])).decode("utf-8")
            module.__file__ = str(ROOT.joinpath(*name.split(".")).with_suffix(".py"))
            module.__package__ = name.rpartition(".")[0]
            exec(compile(code, module.__file__, "exec"), module.__dict__)

    sys.meta_path.insert(0, Loader())
    from scanner.kis_engine import KISUnifiedScanner, apply_mode_policy, finalize_trade_item
    return KISUnifiedScanner(), apply_mode_policy, finalize_trade_item


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT,
            issued INTEGER NOT NULL, base_price REAL NOT NULL,
            verdict TEXT, score REAL, entry_ok INTEGER NOT NULL DEFAULT 0,
            forecast5 REAL, forecast10 REAL, forecast20 REAL, forecast30 REAL,
            actual5 REAL, actual10 REAL, actual20 REAL, actual30 REAL,
            max_up30 REAL, max_down30 REAL, result_done INTEGER NOT NULL DEFAULT 0,
            stop_price REAL, data_valid INTEGER NOT NULL DEFAULT 0,
            hit1_before_stop INTEGER, hit2_before_stop INTEGER, hit3_before_stop INTEGER,
            stop_first INTEGER, detail_json TEXT,
            repeat_entry_price REAL, target1_price REAL, target2_price REAL,
            repeat_range_percent REAL, repeat_candidate INTEGER NOT NULL DEFAULT 0,
            entry_touched INTEGER, target1_before_stop INTEGER, target2_before_stop INTEGER,
            continuation_score REAL, continuation_label TEXT, extra_after_target1 REAL,
            UNIQUE(market,ticker,issued)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            market TEXT NOT NULL, ticker TEXT NOT NULL, captured INTEGER NOT NULL,
            price REAL NOT NULL, PRIMARY KEY(market,ticker,captured)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_signals_pending ON signals(result_done,issued)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_quotes_lookup ON quotes(market,ticker,captured)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS collector_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), pid INTEGER, heartbeat INTEGER
        )
    """)

    # 기존 검증 DB는 삭제하지 않고 필요한 열만 추가한다.
    existing = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
    migrations = {
        "stop_price": "REAL", "data_valid": "INTEGER NOT NULL DEFAULT 0",
        "hit1_before_stop": "INTEGER", "hit2_before_stop": "INTEGER",
        "hit3_before_stop": "INTEGER", "stop_first": "INTEGER",
        "repeat_entry_price": "REAL", "target1_price": "REAL", "target2_price": "REAL",
        "repeat_range_percent": "REAL", "repeat_candidate": "INTEGER NOT NULL DEFAULT 0",
        "entry_touched": "INTEGER", "target1_before_stop": "INTEGER",
        "target2_before_stop": "INTEGER", "continuation_score": "REAL",
        "continuation_label": "TEXT", "extra_after_target1": "REAL",
    }
    for column, definition in migrations.items():
        if column not in existing:
            db.execute(f"ALTER TABLE signals ADD COLUMN {column} {definition}")
    return db


def f(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def market_is_open(market: str, now: datetime) -> bool:
    """국내=정규장만 / 미국=주간거래+프리+정규+애프터 전부를 tradable로 본다.
    (regime_session_upgrade.session_for()에 위임 - 시간대 정의는 그 파일 한 곳에서만 관리)
    """
    return session_for(market, now).tradable


def signal_window_open(market: str, now: datetime) -> bool:
    # 신호 발생 창은 거래 가능 시간과 동일하게 맞춘다 (국내=정규장만, 미국=전 세션).
    return session_for(market, now).tradable


def row_for(ticker: str, name: str, exchange: str) -> dict:
    return {"ticker": ticker, "name": name, "exchange": exchange, "asset_type": "검증대상"}


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



def _adapt_repeat_overlay_for_audit(item: dict) -> dict:
    """새 반복단타 필드를 기존 검증 DB/초단타 UI 필드명과 동기화한다."""
    if not isinstance(item, dict):
        return item
    width = f(item.get("repeat_width_percent"))
    state = str(item.get("repeat_state") or "WAIT_TREND")
    if width < 0.50 and item.get("repeat_chart_valid"):
        old_state = "RANGE_TOO_NARROW"
    elif width > 1.50 and item.get("repeat_chart_valid"):
        old_state = "RANGE_TOO_WIDE"
    else:
        old_state = {
            "BUY_ZONE": "BUY_PULLBACK",
            "WAIT_PULLBACK": "WAIT_PULLBACK",
            "TAKE_PROFIT": "TAKE_PROFIT",
            "BREAKDOWN": "EXIT",
            "WAIT_TREND": "WAIT_TREND",
        }.get(state, "WAIT_TREND")

    cont = str(item.get("repeat_continuation_state") or "NONE")
    old_cont = {
        "HIGH": "STRONG",
        "MID": "WATCH",
        "LOW": "LIMITED",
        "NONE": "NO_TARGET2",
    }.get(cont, "NO_TARGET2")

    item.update(
        structural_entry=f(item.get("price")),
        structural_support=f(item.get("repeat_support")),
        structural_target=f(item.get("repeat_target1")),
        structural_target1=f(item.get("repeat_target1")),
        structural_target2=f(item.get("repeat_target2")),
        stop_loss=f(item.get("repeat_stop")),
        target1_upside_percent=f(item.get("repeat_target1_current_upside")),
        target2_upside_percent=f(item.get("repeat_target2_current_upside")),
        risk_reward=f(item.get("repeat_risk_reward")),
        risk_reward_target1=f(item.get("repeat_risk_reward")),
        level_plan_valid=bool(item.get("repeat_chart_valid")),
        level_plan_reason=str(item.get("repeat_chart_reason") or ""),
        target_basis=str(item.get("repeat_target1_basis") or ""),
        target1_basis=str(item.get("repeat_target1_basis") or ""),
        target2_basis=str(item.get("repeat_target2_basis") or ""),
        stop_basis=f"{item.get('repeat_support_basis', '차트 지지')} 이탈 + ATR 완충",
        chart_box_high=f(item.get("repeat_chart_box_high")),
        chart_box_low=f(item.get("repeat_chart_box_low")),
        chart_box_width=max(0.0, f(item.get("repeat_chart_box_high")) - f(item.get("repeat_chart_box_low"))),
        continuous_rise=bool(f(item.get("repeat_trend_score")) >= 7),
        continuous_rise_score=int(f(item.get("repeat_trend_score"))),
        continuous_rise_checks=item.get("repeat_trend_checks") or {},
        repeat_scalp_state=old_state,
        repeat_scalp_label=str(item.get("repeat_label") or ""),
        repeat_scalp_reason=str(item.get("repeat_chart_reason") or ""),
        repeat_scalp_buy_level=f(item.get("repeat_entry")),
        repeat_scalp_sell_level=f(item.get("repeat_target1")),
        repeat_scalp_invalidation=f(item.get("repeat_stop")),
        repeat_scalp_median_bar_range=f(item.get("repeat_median_range")),
        repeat_scalp_range_percent=width,
        repeat_scalp_preferred_range=bool(item.get("repeat_preferred_range")),
        repeat_scalp_can_extend=cont == "HIGH",
        repeat_scalp_extension_label=str(item.get("repeat_continuation_label") or ""),
        repeat_scalp_extension_reason=f"추가상승 근거 {int(f(item.get('repeat_continuation_score')))}/10",
        repeat_scalp_extension_percent=f(item.get("repeat_extra_after_target1")),
        upside_continuation_state=old_cont,
        upside_continuation_label=str(item.get("repeat_continuation_label") or ""),
        upside_continuation_score=int(f(item.get("repeat_continuation_score"))),
        upside_continuation_checks=item.get("repeat_continuation_checks") or {},
        additional_upside_after_target1=f(item.get("repeat_extra_after_target1")),
        target2_total_upside=f(item.get("repeat_target2_current_upside")),
    )
    return item

def analyze_one(engine, policy, finalize, market: str, member: tuple[str, str, str]) -> dict | None:
    ticker, name, exchange = member
    mode = "국내 30분 1% 타점" if market == "KR" else "미국 30분 1% 타점"
    try:
        result = engine.analyze(row_for(ticker, name, exchange), mode)
        result = policy(finalize(result), mode)
        # 검증기에서도 앱과 동일한 1분봉 지지/저항·ATR 후처리를 반드시 실행한다.
        result = apply_repeat_scalp_overlay(result, market)
        result = _adapt_repeat_overlay_for_audit(result)
        if f(result.get("price")) <= 0:
            raise ValueError("현재가 0")
        return result
    except Exception as exc:
        logging.warning("분석 실패 %s %s: %s", market, ticker, exc)
        return None


DETAIL_KEYS = (
    "chart_verdict", "entry_checks_passed",
    "risk_reward", "risk_reward_target1", "risk_reward_target2",
    "rvol", "vwap", "ema9", "ema20", "rsi", "five_min_risk_score",
    "change_percent", "screen_change", "change", "data_completeness",
    "pullback_entry", "breakout_entry", "stop_loss",
    "structural_entry", "structural_support", "structural_target",
    "structural_target1", "structural_target2",
    "target1_upside_percent", "target2_upside_percent",
    "target_basis", "target1_basis", "target2_basis", "stop_basis",
    "level_plan_valid", "level_plan_reason",
    "chart_resistance_levels", "chart_box_high", "chart_box_low", "chart_box_width",
    "breakout_active",
    "continuous_rise", "continuous_rise_score", "continuous_rise_checks",
    "trend_return_5m", "trend_return_15m", "trend_return_30m",
    "up_down_volume_ratio",
    "mtf_alignment", "mtf_exit", "mtf_higher_trend", "mtf_short_pullback",
    "mtf_checks", "mtf_status", "mtf_detail",
    "repeat_scalp_state", "repeat_scalp_label", "repeat_scalp_reason",
    "repeat_scalp_buy_level", "repeat_scalp_sell_level", "repeat_scalp_invalidation",
    "repeat_scalp_median_bar_range", "repeat_scalp_range_percent", "repeat_scalp_preferred_range",
    "repeat_box_valid", "repeat_box_low", "repeat_box_high", "repeat_box_range_percent",
    "repeat_rsi_recovery",
    "repeat_scalp_can_extend", "repeat_scalp_extension_label",
    "repeat_scalp_extension_reason", "repeat_scalp_extension_percent",
    "upside_continuation_state", "upside_continuation_label",
    "upside_continuation_score", "upside_continuation_checks",
    "additional_upside_after_target1", "target2_total_upside",
    "repeat_scalp_reversal_score", "repeat_scalp_reversal_checks",
    "trailing_stop_enabled", "trailing_stop_percent", "trailing_stop_price",
    "data_gate_passed", "verified_spread_percent",
    "repeat_chart_valid", "repeat_chart_reason", "repeat_candidate",
    "repeat_entry", "repeat_support", "repeat_stop", "repeat_target1", "repeat_target2",
    "repeat_width_percent", "repeat_target1_current_upside", "repeat_target2_current_upside",
    "repeat_extra_after_target1", "repeat_risk_reward", "repeat_state", "repeat_label",
    "repeat_trend_score", "repeat_trend_checks", "repeat_support_basis",
    "repeat_target1_basis", "repeat_target2_basis", "repeat_atr14", "repeat_median_range",
    "repeat_volume_ratio", "repeat_continuation_state", "repeat_continuation_label",
    "repeat_continuation_score", "repeat_continuation_checks", "repeat_preferred_range",
    "repeat_chart_box_low", "repeat_chart_box_high",
)
# 참고: intraday_regime_plan()/RegimeConfirmer는 현재 scalp_app.py의 precise_analysis()
# 경로에서만 실행됩니다. 이 수집기(analyze_one)는 apply_repeat_scalp_overlay까지만 쓰므로
# regime_state_display 계열 필드는 여기서 생성되지 않습니다. 검증 DB에도 추세 확정 이력을
# 남기고 싶다면 scalp_app.py의 intraday_regime_plan/box_regime_plan/apply_regime_confirmation을
# 이 파일 analyze_one()에도 동일하게 연결해야 합니다(현재는 범위 밖).


def store_result(db: sqlite3.Connection, market: str, item: dict, now_ts: int, bucket_seconds: int) -> None:
    ticker = str(item.get("ticker") or "").upper()
    price = f(item.get("price"))
    if not ticker or price <= 0:
        return

    captured = now_ts - now_ts % 60
    bucket = now_ts - now_ts % bucket_seconds
    db.execute(
        "INSERT OR REPLACE INTO quotes(market,ticker,captured,price) VALUES(?,?,?,?)",
        (market, ticker, captured, price),
    )

    detail = {key: item.get(key) for key in DETAIL_KEYS}

    if "data_gate_passed" in item:
        data_valid = int(bool(item.get("data_gate_passed")))
    else:
        data_valid = int(
            price > 0 and f(item.get("vwap")) > 0 and f(item.get("ema9")) > 0
            and not bool(item.get("intraday_fallback"))
            and f(item.get("data_completeness"), 100.0) >= 60.0
        )

    repeat_entry = f(item.get("repeat_entry", item.get("repeat_scalp_buy_level")))
    target1 = f(item.get("repeat_target1", item.get("structural_target1", item.get("structural_target"))))
    target2 = f(item.get("repeat_target2", item.get("structural_target2")))
    repeat_range = f(item.get("repeat_width_percent", item.get("repeat_scalp_range_percent")))
    repeat_candidate = int(bool(item.get("repeat_candidate")))
    continuation_score = f(item.get("repeat_continuation_score", item.get("upside_continuation_score")))
    continuation_label = str(item.get("repeat_continuation_label") or item.get("upside_continuation_label") or "")
    extra_after_target1 = f(item.get("repeat_extra_after_target1", item.get("additional_upside_after_target1")))
    stop_price = f(item.get("repeat_stop", item.get("stop_loss")))

    duplicate = db.execute(
        "SELECT 1 FROM signals WHERE market=? AND ticker=? AND issued BETWEEN ? AND ? LIMIT 1",
        (market, ticker, bucket, bucket + bucket_seconds - 1),
    ).fetchone()
    if duplicate:
        return

    db.execute("""
        INSERT INTO signals(
            market,ticker,name,issued,base_price,verdict,score,entry_ok,
            forecast5,forecast10,forecast20,forecast30,stop_price,data_valid,detail_json,
            repeat_entry_price,target1_price,target2_price,repeat_range_percent,repeat_candidate,
            continuation_score,continuation_label,extra_after_target1
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        market, ticker, str(item.get("name") or ticker), now_ts, price,
        str(item.get("chart_verdict") or "WAIT"), f(item.get("score")),
        int(bool(item.get("entry_checks_passed"))),
        f(item.get("forecast_5m")), f(item.get("forecast_10m")),
        f(item.get("forecast_20m")), f(item.get("forecast_30m")),
        stop_price, data_valid, json.dumps(detail, ensure_ascii=False, default=str),
        repeat_entry, target1, target2, repeat_range, repeat_candidate,
        continuation_score, continuation_label, extra_after_target1,
    ))


def store_quote(db: sqlite3.Connection, market: str, ticker: str, price: float, now_ts: int) -> None:
    if price <= 0:
        return
    captured = now_ts - now_ts % 60
    db.execute(
        "INSERT OR REPLACE INTO quotes(market,ticker,captured,price) VALUES(?,?,?,?)",
        (market, ticker, captured, price),
    )


def nearest_quote(db: sqlite3.Connection, market: str, ticker: str, target: int, tolerance=150):
    return db.execute("""
        SELECT price,captured FROM quotes
        WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
        ORDER BY ABS(captured-?) LIMIT 1
    """, (market, ticker, target - tolerance, target + tolerance, target)).fetchone()


def grade_pending(db: sqlite3.Connection, now_ts: int) -> None:
    rows = db.execute("""
        SELECT id,market,ticker,issued,base_price,actual5,actual10,actual20,actual30,
               stop_price,repeat_entry_price,target1_price,target2_price,repeat_candidate
        FROM signals WHERE result_done=0 AND issued>=?
    """, (now_ts - 3 * 86400,)).fetchall()

    for (signal_id, market, ticker, issued, base, a5, a10, a20, a30, stop_price,
         repeat_entry_price, target1_price, target2_price, repeat_candidate) in rows:
        updates = {}
        for minutes, existing in ((5, a5), (10, a10), (20, a20), (30, a30)):
            if existing is None and now_ts >= issued + minutes * 60:
                quote = nearest_quote(db, market, ticker, issued + minutes * 60)
                if quote and base > 0:
                    updates[f"actual{minutes}"] = (f(quote[0]) / base - 1) * 100

        if now_ts >= issued + 30 * 60:
            extrema = db.execute("""
                SELECT MAX(price),MIN(price) FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
            """, (market, ticker, issued, issued + 30 * 60)).fetchone()
            if extrema and extrema[0] is not None:
                updates["max_up30"] = (f(extrema[0]) / base - 1) * 100
                updates["max_down30"] = (f(extrema[1]) / base - 1) * 100

            path = db.execute("""
                SELECT captured,price FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ? ORDER BY captured
            """, (market, ticker, issued, issued + 30 * 60)).fetchall()

            stop = f(stop_price)
            repeat_entry = f(repeat_entry_price)
            target1 = f(target1_price)
            target2 = f(target2_price)

            # 반복단타는 실제 매수가에 먼저 닿아야 체결된 것으로 본다.
            entry_index = None
            if path and repeat_entry > 0:
                if f(path[0][1]) <= repeat_entry:
                    entry_index = 0
                else:
                    entry_index = next(
                        (i for i, (_, p) in enumerate(path) if f(p) <= repeat_entry),
                        None,
                    )
                updates["entry_touched"] = int(entry_index is not None)

            if entry_index is not None:
                trade_path = path[entry_index:]
                stop_rel = next(
                    (i for i, (_, p) in enumerate(trade_path) if stop > 0 and f(p) <= stop),
                    None,
                )
                t1_rel = next(
                    (i for i, (_, p) in enumerate(trade_path) if target1 > 0 and f(p) >= target1),
                    None,
                )
                t2_rel = next(
                    (i for i, (_, p) in enumerate(trade_path) if target2 > target1 > 0 and f(p) >= target2),
                    None,
                )
                updates["target1_before_stop"] = int(
                    t1_rel is not None and (stop_rel is None or t1_rel < stop_rel)
                )
                updates["target2_before_stop"] = int(
                    t2_rel is not None and (stop_rel is None or t2_rel < stop_rel)
                ) if target2 > target1 > 0 else None
                updates["stop_first"] = int(
                    stop_rel is not None and (t1_rel is None or stop_rel < t1_rel)
                )
            elif repeat_entry > 0:
                # 매수가 미체결은 손실도 성공도 아닌 '미체결'로 분리한다.
                updates["target1_before_stop"] = None
                updates["target2_before_stop"] = None
                updates["stop_first"] = 0
            else:
                # 레거시 신호에는 반복 매수가가 없으므로 기존 방식으로만 stop_first를 유지한다.
                stop_index = next(
                    (i for i, (_, p) in enumerate(path) if stop > 0 and f(p) <= stop),
                    None,
                )
                first_target_index = next(
                    (i for i, (_, p) in enumerate(path) if f(p) >= base * 1.01),
                    None,
                )
                updates["stop_first"] = int(
                    stop_index is not None
                    and (first_target_index is None or stop_index < first_target_index)
                )

            # +1/+2/+3%는 전략 목표가가 아니라 비교 통계 전용으로 계속 보존한다.
            legacy_stop_index = next(
                (i for i, (_, p) in enumerate(path) if stop > 0 and f(p) <= stop),
                None,
            )
            for goal in (1, 2, 3):
                target = base * (1 + goal / 100)
                target_index = next(
                    (i for i, (_, p) in enumerate(path) if f(p) >= target),
                    None,
                )
                hit = target_index is not None and (
                    legacy_stop_index is None or target_index < legacy_stop_index
                )
                updates[f"hit{goal}_before_stop"] = int(hit)

            projected = {
                5: updates.get("actual5", a5),
                10: updates.get("actual10", a10),
                20: updates.get("actual20", a20),
                30: updates.get("actual30", a30),
            }
            if all(value is not None for value in projected.values()) or now_ts >= issued + 45 * 60:
                updates["result_done"] = 1

        if updates:
            assignments = ",".join(f"{key}=?" for key in updates)
            db.execute(f"UPDATE signals SET {assignments} WHERE id=?", (*updates.values(), signal_id))


def export_summary(db: sqlite3.Connection) -> None:
    rows = db.execute("""
        SELECT market,ticker,name,datetime(issued,'unixepoch','+9 hours'),base_price,
               verdict,score,entry_ok,forecast5,actual5,forecast10,actual10,
               forecast20,actual20,forecast30,actual30,max_up30,max_down30,
               stop_price,data_valid,
               repeat_entry_price,target1_price,target2_price,repeat_range_percent,repeat_candidate,
               entry_touched,target1_before_stop,target2_before_stop,
               continuation_score,continuation_label,extra_after_target1,
               hit1_before_stop,hit2_before_stop,hit3_before_stop,stop_first
        FROM signals ORDER BY issued DESC
    """).fetchall()
    headers = [
        "시장","티커","종목명","신호시각(KST)","기준가","판정","점수","기존진입통과",
        "예상5분","실제5분","예상10분","실제10분","예상20분","실제20분",
        "예상30분","실제30분","30분최대상승","30분최대하락","손절가","데이터유효",
        "반복매수가","차트1차목표","차트2차목표","반복폭%","반복후보",
        "매수가체결","1차선도달","2차선도달","추가상승점수","추가상승판정","1차후추가폭%",
        "+1%비교통계","+2%비교통계","+3%비교통계","손절먼저",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    export_html_report(db)


def ratio(values: list[bool]) -> str:
    return f"{sum(values) / len(values) * 100:.1f}%" if values else "표본 없음"


def wilson_interval(values: list[bool], z: float = 1.96) -> tuple[float, float] | None:
    if not values:
        return None
    n = len(values)
    p = sum(values) / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def export_html_report(db: sqlite3.Connection) -> None:
    report_path = DB_PATH.parent / "validation_report.html"
    completed = db.execute("""
        SELECT market,ticker,name,repeat_candidate,data_valid,repeat_range_percent,
               entry_touched,target1_before_stop,target2_before_stop,stop_first,
               continuation_label,extra_after_target1,max_up30,max_down30,
               forecast5,actual5,forecast10,actual10,forecast20,actual20,forecast30,actual30,
               repeat_entry_price,target1_price,target2_price
        FROM signals WHERE result_done=1 ORDER BY issued DESC
    """).fetchall()
    valid = [row for row in completed if row[4] == 1]
    repeat_candidates = [row for row in valid if row[3] == 1]
    filled = [row for row in repeat_candidates if row[6] == 1]

    cards = []
    entry_values = [bool(row[6]) for row in repeat_candidates if row[6] is not None]
    cards.append(("반복매수가 체결", ratio(entry_values), len(entry_values), wilson_interval(entry_values)))
    t1_values = [bool(row[7]) for row in filled if row[7] is not None]
    cards.append(("차트 1차 선도달", ratio(t1_values), len(t1_values), wilson_interval(t1_values)))
    t2_values = [bool(row[8]) for row in filled if row[8] is not None]
    cards.append(("차트 2차 선도달", ratio(t2_values), len(t2_values), wilson_interval(t2_values)))
    avoid_values = [not bool(row[9]) for row in filled if row[9] is not None]
    cards.append(("1차 전 손절회피", ratio(avoid_values), len(avoid_values), wilson_interval(avoid_values)))

    for label, expected_index, actual_index in (
        ("5분 방향",14,15),("10분 방향",16,17),("20분 방향",18,19),("30분 방향",20,21)
    ):
        judged = [
            ((f(row[expected_index]) >= 0) == (f(row[actual_index]) >= 0))
            for row in valid if row[actual_index] is not None
        ]
        cards.append((label, ratio(judged), len(judged), wilson_interval(judged)))

    card_html = "".join(
        f'<div class="card"><small>{html.escape(label)}</small><b>{value}</b>'
        f'<span>표본 {samples}건</span>'
        + (f'<span>95% 범위 {interval[0]*100:.1f}~{interval[1]*100:.1f}%</span>' if interval else '')
        + '</div>'
        for label, value, samples, interval in cards
    )

    recent_rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(str(value if value is not None else '-'))}</td>"
            for value in (
                row[0], row[1], row[2],
                "후보" if row[3] else "관찰",
                f"{f(row[5]):.2f}%",
                "체결" if row[6] else "미체결",
                row[22], row[23], row[24],
                row[7], row[8], row[9],
                row[10] or "-", f"{f(row[11]):.2f}%",
                round(f(row[12]),3), round(f(row[13]),3),
            )
        ) + "</tr>" for row in completed[:100]
    )

    warning = (
        "반복단타 성공률은 '반복 매수가가 실제로 체결된 표본'에서 차트 1차·2차 목표와 "
        "손절의 선후관계를 계산합니다. +1/+2/+3% 고정 통계는 CSV 비교용으로만 유지합니다."
    )
    document = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>초단타 자동검증 결과</title>
    <style>body{{font-family:Arial,sans-serif;max-width:1350px;margin:30px auto;padding:0 16px;color:#20242c}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
    .card{{background:#f4f6fa;border-radius:12px;padding:16px}}.card b,.card span{{display:block}}
    .card b{{font-size:26px;margin:8px 0}}.warn{{background:#fff4dc;padding:15px;border-radius:10px;margin:18px 0}}
    table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #ddd;padding:7px;text-align:left}}</style>
    <h1>초단타 자동검증 결과</h1>
    <p>완료 {len(completed)}건 · 유효 {len(valid)}건 · 반복후보 {len(repeat_candidates)}건 · 실제 매수가 체결 {len(filled)}건</p>
    <div class='warn'>{warning}</div><div class='cards'>{card_html}</div>
    <h2>최근 완료 신호</h2>
    <table><thead><tr><th>시장</th><th>티커</th><th>종목</th><th>구분</th><th>반복폭</th><th>매수</th><th>반복매수가</th><th>1차</th><th>2차</th><th>1차성공</th><th>2차성공</th><th>손절먼저</th><th>추가상승</th><th>추가폭</th><th>최대상승%</th><th>최대하락%</th></tr></thead>
    <tbody>{recent_rows}</tbody></table></html>"""
    report_path.write_text(document, encoding="utf-8")


def stop_handler(*_):
    global STOP
    STOP = True


def claim_single_instance(db: sqlite3.Connection, now_ts: int) -> bool:
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT pid,heartbeat FROM collector_state WHERE singleton=1").fetchone()
    if row and now_ts - int(row[1] or 0) < 180 and int(row[0] or 0) != os.getpid():
        db.rollback()
        return False
    db.execute("INSERT OR REPLACE INTO collector_state(singleton,pid,heartbeat) VALUES(1,?,?)", (os.getpid(), now_ts))
    db.commit()
    return True


def heartbeat(db: sqlite3.Connection, now_ts: int) -> None:
    db.execute("UPDATE collector_state SET heartbeat=? WHERE singleton=1 AND pid=?", (now_ts, os.getpid()))


def keep_windows_awake(enable: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        continuous, system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(continuous | system_required if enable else continuous)
    except Exception as exc:
        logging.warning("절전 방지 설정 실패: %s", exc)


def refresh_quote_only(engine, market: str, member: tuple[str, str, str]) -> float:
    ticker, _, exchange = member
    try:
        quote = engine.client.kr_quote(ticker) if market == "KR" else engine.client.us_quote(ticker, exchange)
        return f(quote.get("price"))
    except Exception as exc:
        logging.warning("채점 시세 실패 %s %s: %s", market, ticker, exc)
        return 0.0



def discover_market_members(engine, market: str, limit: int = 18) -> list[tuple[str, str, str]]:
    """고정 유니버스 대신 KIS 엔진의 실시간 후보 순위를 사용한다.

    호출 실패 시에만 기존 고정 목록으로 되돌아간다. 후보 검색은 한 루프에 1회만 수행하고
    실제 정밀분석 종목 수는 main의 rotation/batch로 제한해 API 폭증을 막는다.
    """
    modes = ("국내 돌파", "국내 거래대금 급증") if market == "KR" else ("미국 30분 1% 타점", "미국 급등주")
    ranked: dict[str, dict] = {}
    for mode in modes:
        try:
            candidates = engine.candidates(mode) or []
            for row in candidates:
                c = dict(row or {})
                ticker = str(c.get("ticker") or c.get("code") or "").upper().strip()
                if not ticker:
                    continue
                old = ranked.get(ticker, {})
                price = f(c.get("screen_price") or c.get("price") or old.get("screen_price"))
                volume = int(f(c.get("screen_volume") or c.get("volume") or c.get("accumulated_volume") or old.get("screen_volume")))
                change = f(c.get("screen_change") if c.get("screen_change") is not None else c.get("change_percent", c.get("change", old.get("screen_change", 0))))
                # 큰 차트/원본 payload는 후보 탐색 단계에서 보존하지 않는다.
                ranked[ticker] = {
                    "ticker": ticker,
                    "name": str(c.get("name") or old.get("name") or ticker),
                    "exchange": str(c.get("exchange") or old.get("exchange") or ("KR" if market == "KR" else "NASDAQ")),
                    "screen_price": price,
                    "screen_volume": max(volume, int(f(old.get("screen_volume")))),
                    "screen_change": change,
                }
            del candidates
        except Exception as exc:
            logging.debug("동적 후보 검색 실패 %s %s: %s", market, mode, exc)

    rows = []
    for c in ranked.values():
        price = f(c.get("screen_price")); volume = int(f(c.get("screen_volume"))); change = f(c.get("screen_change")); value = price * volume
        if market == "KR":
            valid = 1000 <= price <= 500000 and -1.0 <= change < 18 and volume >= 50000 and value >= 8_000_000_000
            score = min(value / 50_000_000_000, 4.0) + min(volume / 500_000, 3.0) + max(0.0, change) * 0.12
        else:
            valid = 0.5 <= price <= 500 and -1.5 <= change < 40 and volume >= 50000 and value >= 5_000_000
            score = min(value / 50_000_000, 4.0) + min(volume / 1_000_000, 3.0) + max(0.0, change) * 0.10
        if valid:
            rows.append((score, value, (str(c["ticker"]), str(c.get("name") or c["ticker"]), str(c.get("exchange") or ("KR" if market == "KR" else "NASDAQ")))))
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    members = [member for _, _, member in rows[:limit]]
    if members:
        logging.info("%s 동적 후보 %d종목 발견 (상위 %d 사용)", market, len(ranked), len(members))
        return members
    fallback = KR_UNIVERSE if market == "KR" else US_UNIVERSE
    logging.warning("%s 동적 후보 없음 - 고정 안전망 %d종목 사용", market, len(fallback))
    return list(fallback)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="KR", choices=("KR", "US", "BOTH"))
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--signal-bucket", type=int, default=300)
    parser.add_argument("--dynamic-pool", type=int, default=24, help="실시간 1차 후보군 최대 종목 수")
    parser.add_argument("--batch-size", type=int, default=6, help="한 루프에서 정밀분석할 동적 후보 수")
    parser.add_argument("--run-until", default="AUTO")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    setup_logging()
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    try:
        engine, policy, finalize = load_engine()
    except Exception:
        logging.exception("KIS 엔진 시작 실패")
        return 2

    selected = ["KR", "US"] if args.markets == "BOTH" else [args.markets]
    seen_active = set()
    with connect() as db:
        if not claim_single_instance(db, int(time.time())):
            return 3
        keep_windows_awake(True)
        try:
            while not STOP:
                started = time.time()
                now = datetime.now(KST)
                active = [m for m in selected if market_is_open(m, now)]
                seen_active.update(active)
                if args.run_until.upper() == "AUTO" and not active and set(selected).issubset(seen_active):
                    break
                if not active:
                    if args.once:
                        break
                    heartbeat(db, int(time.time()))
                    db.commit()
                    time.sleep(60)
                    continue

                for market in active:
                    pool = discover_market_members(engine, market, max(8, args.dynamic_pool))
                    issuing = signal_window_open(market, now)
                    # 매 루프 전체를 정밀분석하지 않고 배치 순환해 KIS 호출 제한을 보호한다.
                    state_key = f"rotation::{market}"
                    try:
                        row = db.execute("SELECT heartbeat FROM collector_state WHERE singleton=1").fetchone()
                    except Exception:
                        row = None
                    rotation = int((row[0] if row else int(time.time())) // max(30, args.interval)) if pool else 0
                    batch = max(1, min(args.batch_size, len(pool))) if pool else 0
                    start_idx = (rotation * batch) % len(pool) if pool else 0
                    members = [pool[(start_idx + i) % len(pool)] for i in range(batch)] if pool else []
                    for member in members:
                        if STOP:
                            break
                        timestamp = int(time.time())
                        if issuing:
                            item = analyze_one(engine, policy, finalize, market, member)
                            if item:
                                store_result(db, market, item, timestamp, max(60, args.signal_bucket))
                        else:
                            price = refresh_quote_only(engine, market, member)
                            store_quote(db, market, member[0], price, timestamp)
                        heartbeat(db, timestamp)
                        db.commit()
                        time.sleep(0.35)

                grade_pending(db, int(time.time()))
                db.commit()
                export_summary(db)
                if args.once:
                    break
                time.sleep(max(5, args.interval - (time.time() - started)))

            grade_pending(db, int(time.time()))
            db.commit()
            export_summary(db)
        finally:
            keep_windows_awake(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
