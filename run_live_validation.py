# -*- coding: utf-8 -*-
"""KIS 초단타 신호 무인 수집·사후검증기 — v3.6 shared-core.

scalp_app.py 안의 SHARED_STRATEGY_CORE를 직접 읽어 동일한 반복폭·손절·목표·
추세·전망·확률·안전게이트를 사용한다. 별도 repeat_scalp_engine.py 또는
regime_session_upgrade.py 파일은 필요하지 않다.
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
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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


def f(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


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
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_BUNDLED"
            for target in node.targets
        ):
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

    if not any(type(x).__name__ == "Loader" and x.__class__.__module__ == __name__ for x in sys.meta_path):
        sys.meta_path.insert(0, Loader())

    from scanner.kis_engine import KISUnifiedScanner, apply_mode_policy, finalize_trade_item
    return KISUnifiedScanner(), apply_mode_policy, finalize_trade_item


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL, ticker TEXT NOT NULL, name TEXT,
            issued INTEGER NOT NULL, base_price REAL NOT NULL,
            verdict TEXT, score REAL, entry_ok INTEGER NOT NULL DEFAULT 0,
            forecast5 REAL, forecast10 REAL, forecast15 REAL, forecast20 REAL, forecast30 REAL, forecast60 REAL,
            actual5 REAL, actual10 REAL, actual15 REAL, actual20 REAL, actual30 REAL, actual60 REAL,
            max_up30 REAL, max_down30 REAL, max_up60 REAL, max_down60 REAL, result_done INTEGER NOT NULL DEFAULT 0,
            stop_price REAL, data_valid INTEGER NOT NULL DEFAULT 0,
            hit1_before_stop INTEGER, hit2_before_stop INTEGER, hit3_before_stop INTEGER,
            stop_first INTEGER, detail_json TEXT,
            repeat_entry_price REAL, target1_price REAL, target2_price REAL,
            repeat_range_percent REAL, repeat_candidate INTEGER NOT NULL DEFAULT 0,
            entry_touched INTEGER, target1_before_stop INTEGER, target2_before_stop INTEGER,
            continuation_score REAL, continuation_label TEXT, extra_after_target1 REAL,
            target1_model_prob REAL, target1_first_prob REAL, target_prob_confidence REAL,
            strategy_type TEXT,
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
        "forecast15": "REAL", "forecast60": "REAL", "actual15": "REAL", "actual60": "REAL",
        "max_up60": "REAL", "max_down60": "REAL",
        "target1_model_prob": "REAL", "target1_first_prob": "REAL",
        "target_prob_confidence": "REAL", "strategy_type": "TEXT",
    }
    for column, definition in migrations.items():
        if column not in existing:
            db.execute(f"ALTER TABLE signals ADD COLUMN {column} {definition}")
    return db


def market_is_open(market: str, now: datetime) -> bool:
    if market == "KR":
        local = now.astimezone(KST)
        return local.weekday() < 5 and clock_time(8, 50) <= local.time() <= clock_time(15, 35)
    try:
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))
        return ny.weekday() < 5 and clock_time(4, 0) <= ny.time() <= clock_time(20, 0)
    except Exception:
        return False


def signal_window_open(market: str, now: datetime) -> bool:
    if market == "KR":
        local = now.astimezone(KST)
        return local.weekday() < 5 and clock_time(9, 0) <= local.time() <= clock_time(14, 30)
    try:
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))
        return ny.weekday() < 5 and clock_time(4, 0) <= ny.time() <= clock_time(19, 0)
    except Exception:
        return False


def row_for(ticker: str, name: str, exchange: str) -> dict:
    return {"ticker": ticker, "name": name, "exchange": exchange, "asset_type": "검증대상"}










def load_shared_strategy_core() -> dict:
    """scalp_app.py의 순수 전략코드만 읽어 검증기와 UI 계산을 완전히 통일한다."""
    source_path = ROOT / "scalp_app.py"
    if not source_path.exists():
        raise FileNotFoundError("run_live_validation.py와 같은 폴더에 scalp_app.py가 필요합니다.")
    source = source_path.read_text(encoding="utf-8")
    start_marker = "# === SHARED_STRATEGY_CORE_START ==="
    end_marker = "# === SHARED_STRATEGY_CORE_END ==="
    if start_marker not in source or end_marker not in source:
        raise RuntimeError("scalp_app.py에서 SHARED_STRATEGY_CORE를 찾지 못했습니다.")
    core = source.split(start_marker, 1)[1].split(end_marker, 1)[0]
    namespace = {"math": math, "pd": pd, "KST": KST, "ET": ZoneInfo("America/New_York")}
    exec(compile(core, str(source_path) + "::<shared_core>", "exec"), namespace)
    return namespace


_SHARED = load_shared_strategy_core()
apply_repeat_scalp_overlay = _SHARED["apply_repeat_scalp_overlay"]
adapt_repeat_overlay = _SHARED["_adapt_repeat_overlay_for_ui"]
hourly_structure_plan = _SHARED["hourly_structure_plan"]
intraday_regime_plan = _SHARED["intraday_regime_plan"]
forward_forecast_plan = _SHARED["forward_forecast_plan"]
data_quality_gate = _SHARED["data_quality_gate"]
execution_safety_plan = _SHARED["execution_safety_plan"]
target_probability_plan = _SHARED["target_probability_plan"]
swing_cycle_plan = _SHARED["swing_cycle_plan"]
post_entry_risk_plan = _SHARED["post_entry_risk_plan"]
_forecast_flags = _SHARED["_forecast_flags"]
STRATEGY_VERSION = _SHARED["STRATEGY_VERSION"]


def analyze_one(engine, policy, finalize, market: str, member: tuple[str, str, str]) -> dict | None:
    """UI와 같은 계산 순서로 한 종목을 분석한다. Streamlit 상태머신만 제외한다."""
    ticker, name, exchange = member
    mode = "국내 30분 1% 타점" if market == "KR" else "미국 30분 1% 타점"
    market_name = "국내" if market == "KR" else "미국"
    try:
        result = engine.analyze(row_for(ticker, name, exchange), mode)
        result = policy(finalize(result), mode)
        result = hourly_structure_plan(result, market_name)
        result = apply_repeat_scalp_overlay(result, market)
        result = adapt_repeat_overlay(result)
        result = intraday_regime_plan(result, market_name)

        quality_rows, gate, spread = data_quality_gate(result, market_name)
        if spread is not None:
            result["verified_spread_percent"] = spread
        result["data_gate_rows"] = quality_rows
        result["data_gate_passed"] = bool(gate and result.get("repeat_quality_pass", False))

        result = forward_forecast_plan(result, market_name)
        result = execution_safety_plan(result, market_name)
        result = target_probability_plan(result)
        flags = _forecast_flags(result)
        result.update(
            forecast_all_down=bool(flags["all_down"]),
            forecast_medium_down=bool(flags["medium_down"]),
            forecast_valid_pullback=bool(flags["valid_pullback_forecast"]),
            strategy_version=STRATEGY_VERSION,
        )

        hourly = str(result.get("hourly_structure_state", "FORMING"))
        regime = str(result.get("intraday_regime_state", "UNKNOWN"))
        strategy_type = str(result.get("repeat_strategy_type", result.get("strategy_type", "TREND"))).upper()
        is_box = strategy_type == "BOX"
        if is_box:
            structure_ok = bool(result.get("repeat_box_valid")) and hourly != "BEAR" and regime not in {
                "DOWNTREND", "WEAK_BEARISH", "UNKNOWN_WEAK", "UNKNOWN"
            }
        else:
            structure_ok = hourly == "BULL" and regime in {"STRONG_UPTREND", "UPTREND_PULLBACK"}
        if regime == "UPTREND_PULLBACK" and not flags["valid_pullback_forecast"]:
            structure_ok = False

        final_candidate = all([
            bool(result.get("data_gate_passed")),
            bool(result.get("repeat_candidate")),
            bool(result.get("execution_safety_passed")),
            bool(result.get("swing_cycle_valid")),
            str(result.get("post_entry_risk_state","")) not in {"REAL_BREAKDOWN","HARD_EXIT"},
            not flags["all_down"],
            not flags["medium_down"],
            structure_ok,
            str(result.get("repeat_scalp_state", "")) not in {"EXIT", "TAKE_PROFIT"},
        ])

        entry = f(result.get("repeat_scalp_buy_level", result.get("structural_entry")))
        target1 = f(result.get("structural_target1"))
        target2 = f(result.get("structural_target2"))
        stop = f(result.get("stop_loss"))
        width = f(result.get("repeat_scalp_range_percent"))

        reasons = []
        if not result.get("data_gate_passed"):
            reasons.append("데이터/반복품질 게이트 미통과")
        if not result.get("execution_safety_passed"):
            reasons.extend(result.get("execution_safety_reasons") or ["손절 노이즈 안전게이트 미통과"])
        if flags["all_down"]:
            reasons.append("5·15·30·60분 모두 하락")
        elif flags["medium_down"]:
            reasons.append("15·30·60분 모두 하락")
        if not structure_ok:
            reasons.append("60분/큰추세 구조 미확정")

        result.update(
            strategy_type=strategy_type,
            strategy_entry=entry,
            strategy_target1=target1,
            strategy_target2=target2,
            strategy_stop=stop,
            strategy_range_percent=width,
            final_candidate=bool(final_candidate),
            entry_checks_passed=bool(final_candidate),
            trade_decision="BUY" if final_candidate else "WAIT",
            trade_decision_reasons=reasons,
            chart_verdict="BUY" if final_candidate else "WAIT",
        )
        if f(result.get("price")) <= 0:
            raise ValueError("현재가 0")
        return result
    except Exception as exc:
        logging.warning("분석 실패 %s %s: %s", market, ticker, exc)
        return None


DETAIL_KEYS = (
    "strategy_version", "repeat_lookback_minutes", "repeat_context_minutes", "repeat_pivot_lookback_minutes",
    "repeat_oscillation_count", "swing_cycle_valid", "swing_cycle_reason",
    "swing_up_width_percent", "swing_down_width_percent", "swing_width_samples", "swing_down_samples",
    "swing_width_consistency", "swing_up_duration_minutes", "swing_down_duration_minutes",
    "swing_cycle_duration_minutes", "swing_current_phase", "swing_current_elapsed_minutes",
    "swing_current_move_percent", "swing_speed_ratio", "swing_volume_burst_ratio",
    "swing_context_low", "swing_context_high", "swing_context_width_percent",
    "post_entry_risk_state", "post_entry_risk_label", "post_entry_action",
    "post_entry_soft_stop", "post_entry_hard_stop", "post_entry_noise_buffer",
    "post_entry_return_1m", "post_entry_return_3m", "post_entry_return_5m",
    "post_entry_sell_volume_share", "post_entry_shakeout", "post_entry_real_breakdown",
    "post_entry_upside_breakout",
    "repeat_rvol_5_20",
    "execution_safety_passed", "execution_safety_reasons",
    "execution_stop_distance_percent", "execution_noise_floor_percent",
    "execution_stop_inside_noise", "execution_stop_too_wide", "execution_effective_rr",
    "forecast_all_down", "forecast_medium_down", "forecast_valid_pullback",
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
    "forecast_5m", "forecast_15m", "forecast_30m", "forecast_60m", "forward_forecasts",
    "target1_reach_probability", "target2_reach_probability",
    "target1_eta_minutes", "target2_eta_minutes",
    "stop_first_risk_probability", "target1_before_stop_probability",
    "target_probability_confidence", "target_probability_label",
    "strategy_type", "strategy_entry", "strategy_target1", "strategy_target2", "strategy_stop",
    "trade_decision", "trade_decision_reasons", "final_candidate", "final_candidate_before_safety",
    "execution_safety_passed", "execution_safety_reasons",
    "execution_stop_distance_percent", "execution_target1_distance_percent",
    "execution_noise_floor_percent", "execution_safe_stop_reference",
    "execution_stop_inside_noise", "execution_stop_too_wide", "execution_effective_rr",
    "execution_atr14_percent", "execution_median_tr_percent", "execution_forecast5_noise_percent",
    "forecast_all_down", "forecast_medium_down",
)


def store_result(db: sqlite3.Connection, market: str, item: dict, now_ts: int, bucket_seconds: int) -> None:
    ticker = str(item.get("ticker") or "").upper()
    price = f(item.get("price"))
    if not ticker or price <= 0:
        return

    captured = int(now_ts)
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

    repeat_entry = f(item.get("strategy_entry", item.get("repeat_entry", item.get("repeat_scalp_buy_level"))))
    target1 = f(item.get("strategy_target1", item.get("structural_target1", item.get("structural_target"))))
    target2 = f(item.get("strategy_target2", item.get("structural_target2")))
    repeat_range = f(item.get("strategy_range_percent", item.get("repeat_scalp_range_percent")))
    repeat_candidate = int(bool(item.get("final_candidate")))
    continuation_score = f(item.get("repeat_continuation_score", item.get("upside_continuation_score")))
    continuation_label = str(item.get("repeat_continuation_label") or item.get("upside_continuation_label") or "")
    extra_after_target1 = f(item.get("repeat_extra_after_target1", item.get("additional_upside_after_target1")))
    stop_price = f(item.get("strategy_stop", item.get("stop_loss")))

    duplicate = db.execute(
        "SELECT 1 FROM signals WHERE market=? AND ticker=? AND issued BETWEEN ? AND ? LIMIT 1",
        (market, ticker, bucket, bucket + bucket_seconds - 1),
    ).fetchone()
    if duplicate:
        return

    db.execute("""
        INSERT INTO signals(
            market,ticker,name,issued,base_price,verdict,score,entry_ok,
            forecast5,forecast10,forecast15,forecast20,forecast30,forecast60,stop_price,data_valid,detail_json,
            repeat_entry_price,target1_price,target2_price,repeat_range_percent,repeat_candidate,
            continuation_score,continuation_label,extra_after_target1,
            target1_model_prob,target1_first_prob,target_prob_confidence,strategy_type
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        market, ticker, str(item.get("name") or ticker), now_ts, price,
        str(item.get("chart_verdict") or item.get("trade_decision") or "WAIT"), f(item.get("score")),
        int(bool(item.get("entry_checks_passed")) and bool(item.get("execution_safety_passed"))),
        f(item.get("forecast_5m")), f(item.get("forecast_10m")), f(item.get("forecast_15m")),
        f(item.get("forecast_20m")), f(item.get("forecast_30m")), f(item.get("forecast_60m")),
        stop_price, data_valid, json.dumps(detail, ensure_ascii=False, default=str),
        repeat_entry, target1, target2, repeat_range, repeat_candidate,
        continuation_score, continuation_label, extra_after_target1,
        f(item.get("target1_reach_probability")),
        f(item.get("target1_before_stop_probability")),
        f(item.get("target_probability_confidence")),
        str(item.get("strategy_type") or "NONE"),
    ))


def store_quote(db: sqlite3.Connection, market: str, ticker: str, price: float, now_ts: int) -> None:
    if price <= 0:
        return
    captured = int(now_ts)
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
        SELECT id,market,ticker,issued,base_price,actual5,actual10,actual15,actual20,actual30,actual60,
               stop_price,repeat_entry_price,target1_price,target2_price,repeat_candidate
        FROM signals WHERE result_done=0 AND issued>=?
    """, (now_ts - 3 * 86400,)).fetchall()

    for (signal_id, market, ticker, issued, base, a5, a10, a15, a20, a30, a60, stop_price,
         repeat_entry_price, target1_price, target2_price, repeat_candidate) in rows:
        updates = {}
        for minutes, existing in ((5, a5), (10, a10), (15, a15), (20, a20), (30, a30), (60, a60)):
            if existing is None and now_ts >= issued + minutes * 60:
                quote = nearest_quote(db, market, ticker, issued + minutes * 60)
                if quote and base > 0:
                    updates[f"actual{minutes}"] = (f(quote[0]) / base - 1) * 100

        if now_ts >= issued + 60 * 60:
            extrema60 = db.execute("""
                SELECT MAX(price),MIN(price) FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
            """, (market, ticker, issued, issued + 60 * 60)).fetchone()
            if extrema60 and extrema60[0] is not None:
                updates["max_up60"] = (f(extrema60[0]) / base - 1) * 100
                updates["max_down60"] = (f(extrema60[1]) / base - 1) * 100

        if now_ts >= issued + 30 * 60:
            extrema = db.execute("""
                SELECT MAX(price),MIN(price) FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
            """, (market, ticker, issued, issued + 30 * 60)).fetchone()
            if extrema and extrema[0] is not None:
                updates["max_up30"] = (f(extrema[0]) / base - 1) * 100
                updates["max_down30"] = (f(extrema[1]) / base - 1) * 100

        if now_ts >= issued + 60 * 60:
            path = db.execute("""
                SELECT captured,price FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ? ORDER BY captured
            """, (market, ticker, issued, issued + 60 * 60)).fetchall()

            stop = f(stop_price)
            repeat_entry = f(repeat_entry_price)
            target1 = f(target1_price)
            target2 = f(target2_price)

            entry_index = None
            if path and repeat_entry > 0:
                if f(path[0][1]) <= repeat_entry:
                    entry_index = 0
                else:
                    entry_index = next((i for i, (_, p) in enumerate(path) if f(p) <= repeat_entry), None)
                updates["entry_touched"] = int(entry_index is not None)

            if entry_index is not None:
                trade_path = path[entry_index:]
                stop_rel = next((i for i, (_, p) in enumerate(trade_path) if stop > 0 and f(p) <= stop), None)
                t1_rel = next((i for i, (_, p) in enumerate(trade_path) if target1 > 0 and f(p) >= target1), None)
                t2_rel = next((i for i, (_, p) in enumerate(trade_path) if target2 > target1 > 0 and f(p) >= target2), None)
                updates["target1_before_stop"] = int(t1_rel is not None and (stop_rel is None or t1_rel < stop_rel))
                updates["target2_before_stop"] = (
                    int(t2_rel is not None and (stop_rel is None or t2_rel < stop_rel))
                    if target2 > target1 > 0 else None
                )
                updates["stop_first"] = int(stop_rel is not None and (t1_rel is None or stop_rel < t1_rel))
            elif repeat_entry > 0:
                updates["target1_before_stop"] = None
                updates["target2_before_stop"] = None
                updates["stop_first"] = 0
            else:
                stop_index = next((i for i, (_, p) in enumerate(path) if stop > 0 and f(p) <= stop), None)
                first_target_index = next((i for i, (_, p) in enumerate(path) if f(p) >= base * 1.01), None)
                updates["stop_first"] = int(
                    stop_index is not None and (first_target_index is None or stop_index < first_target_index)
                )

            legacy_stop_index = next((i for i, (_, p) in enumerate(path) if stop > 0 and f(p) <= stop), None)
            for goal in (1, 2, 3):
                target = base * (1 + goal / 100)
                target_index = next((i for i, (_, p) in enumerate(path) if f(p) >= target), None)
                hit = target_index is not None and (legacy_stop_index is None or target_index < legacy_stop_index)
                updates[f"hit{goal}_before_stop"] = int(hit)

            projected = {
                5: updates.get("actual5", a5), 10: updates.get("actual10", a10),
                15: updates.get("actual15", a15), 20: updates.get("actual20", a20),
                30: updates.get("actual30", a30), 60: updates.get("actual60", a60),
            }
            if all(value is not None for value in projected.values()) or now_ts >= issued + 75 * 60:
                updates["result_done"] = 1

        if updates:
            assignments = ",".join(f"{key}=?" for key in updates)
            db.execute(f"UPDATE signals SET {assignments} WHERE id=?", (*updates.values(), signal_id))


def export_summary(db: sqlite3.Connection) -> None:
    rows = db.execute("""
        SELECT market,ticker,name,datetime(issued,'unixepoch','+9 hours'),base_price,
               verdict,score,entry_ok,forecast5,actual5,forecast10,actual10,forecast15,actual15,
               forecast20,actual20,forecast30,actual30,forecast60,actual60,max_up30,max_down30,max_up60,max_down60,
               stop_price,data_valid,
               repeat_entry_price,target1_price,target2_price,repeat_range_percent,repeat_candidate,
               entry_touched,target1_before_stop,target2_before_stop,
               continuation_score,continuation_label,extra_after_target1,
               hit1_before_stop,hit2_before_stop,hit3_before_stop,stop_first,
               target1_model_prob,target1_first_prob,target_prob_confidence,strategy_type
        FROM signals ORDER BY issued DESC
    """).fetchall()
    headers = [
        "시장","티커","종목명","신호시각(KST)","기준가","판정","점수","기존진입통과",
        "예상5분","실제5분","예상10분","실제10분","예상15분","실제15분","예상20분","실제20분",
        "예상30분","실제30분","예상60분","실제60분","30분최대상승","30분최대하락","60분최대상승","60분최대하락","손절가","데이터유효",
        "반복매수가","차트1차목표","차트2차목표","반복폭%","반복후보",
        "매수가체결","1차선도달","2차선도달","추가상승점수","추가상승판정","1차후추가폭%",
        "+1%비교통계","+2%비교통계","+3%비교통계","손절먼저",
        "1차모델확률","1차선도달모델확률","확률신뢰도","전략유형",
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
        ("5분 방향", 14, 15), ("10분 방향", 16, 17),
        ("20분 방향", 18, 19), ("30분 방향", 20, 21),
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
                row[0], row[1], row[2], "후보" if row[3] else "관찰",
                f"{f(row[5]):.2f}%", "체결" if row[6] else "미체결",
                row[22], row[23], row[24], row[7], row[8], row[9],
                row[10] or "-", f"{f(row[11]):.2f}%", round(f(row[12]), 3), round(f(row[13]), 3),
            )
        ) + "</tr>" for row in completed[:100]
    )

    warning = (
        "반복단타 성공률은 안전 게이트를 통과하고 반복 매수가가 실제로 체결된 표본에서 계산합니다. "
        "Soft Stop 단순 터치는 즉시 손절로 채점하지 않고, Hard Stop 또는 실제 하락전환 기준을 사용합니다. "
        "현재 quotes는 60초 해상도이므로 틱 단위 선후관계와 동일하지 않습니다."
    )
    document = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>초단타 자동검증 결과</title>
    <style>body{{font-family:Arial,sans-serif;max-width:1350px;margin:30px auto;padding:0 16px;color:#20242c}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
    .card{{background:#f4f6fa;border-radius:12px;padding:16px}}.card b,.card span{{display:block}}
    .card b{{font-size:26px;margin:8px 0}}.warn{{background:#fff4dc;padding:15px;border-radius:10px;margin:18px 0}}
    table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #ddd;padding:7px;text-align:left}}</style>
    <h1>초단타 자동검증 결과</h1>
    <p>완료 {len(completed)}건 · 유효 {len(valid)}건 · 안전 반복후보 {len(repeat_candidates)}건 · 실제 매수가 체결 {len(filled)}건</p>
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
    db.execute(
        "INSERT OR REPLACE INTO collector_state(singleton,pid,heartbeat) VALUES(1,?,?)",
        (os.getpid(), now_ts),
    )
    db.commit()
    return True


def heartbeat(db: sqlite3.Connection, now_ts: int) -> None:
    db.execute(
        "UPDATE collector_state SET heartbeat=? WHERE singleton=1 AND pid=?",
        (now_ts, os.getpid()),
    )


def keep_windows_awake(enable: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        continuous, system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            continuous | system_required if enable else continuous
        )
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
    modes = (
        ("국내 돌파", "국내 거래대금 급증")
        if market == "KR"
        else ("미국 30분 1% 타점", "미국 급등주")
    )
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
                change = f(
                    c.get("screen_change")
                    if c.get("screen_change") is not None
                    else c.get("change_percent", c.get("change", old.get("screen_change", 0)))
                )
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
        price = f(c.get("screen_price"))
        volume = int(f(c.get("screen_volume")))
        change = f(c.get("screen_change"))
        value = price * volume
        if market == "KR":
            valid = 1000 <= price <= 500000 and -1.0 <= change < 18 and volume >= 50000 and value >= 8_000_000_000
            score = min(value / 50_000_000_000, 4.0) + min(volume / 500_000, 3.0) + max(0.0, change) * 0.12
        else:
            valid = 0.5 <= price <= 500 and -1.5 <= change < 40 and volume >= 50000 and value >= 5_000_000
            score = min(value / 50_000_000, 4.0) + min(volume / 1_000_000, 3.0) + max(0.0, change) * 0.10
        if valid:
            rows.append((
                score,
                value,
                (
                    str(c["ticker"]),
                    str(c.get("name") or c["ticker"]),
                    str(c.get("exchange") or ("KR" if market == "KR" else "NASDAQ")),
                ),
            ))
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
                    row = db.execute("SELECT heartbeat FROM collector_state WHERE singleton=1").fetchone()
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
