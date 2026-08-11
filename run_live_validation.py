# -*- coding: utf-8 -*-
"""KIS 초단타 신호 무인 수집·사후검증기.

같은 폴더의 app.py에 번들된 엔진을 재사용한다. Streamlit 화면과 무관하게
실행되며 SQLite에 신호와 5/10/20/30분 후 결과를 영구 저장한다.
"""
from __future__ import annotations

import argparse
import ast
import base64
import csv
import importlib.abc
import importlib.util
import json
import logging
import html
import signal
import sqlite3
import sys
import time
import types
import zlib
import os
import math
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9), name="KST")
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "validation_data" / "live_validation.sqlite3"
LOG_PATH = ROOT / "validation_data" / "collector.log"
CSV_PATH = ROOT / "validation_data" / "validation_summary.csv"
STOP = False

KR_UNIVERSE = [
    ("005930", "삼성전자", "KR"), ("000660", "SK하이닉스", "KR"),
    ("035420", "NAVER", "KR"), ("005380", "현대차", "KR"),
    ("069500", "KODEX 200", "KR"), ("102110", "TIGER 200", "KR"),
    ("396500", "TIGER 반도체TOP10", "KR"),
    ("122630", "KODEX 레버리지", "KR"),
    ("123320", "TIGER 레버리지", "KR"),
    ("488080", "TIGER 반도체TOP10레버리지", "KR"),
]
US_UNIVERSE = [
    ("GOOGL", "알파벳", "NASDAQ"), ("AMD", "AMD", "NASDAQ"),
    ("INTC", "인텔", "NASDAQ"), ("SMH", "반도체 ETF", "NASDAQ"),
    ("SOXL", "반도체 3배 레버리지 ETF", "AMEX"),
    ("SOXS", "반도체 3배 인버스 ETF", "AMEX"),
    ("TQQQ", "나스닥100 3배 레버리지 ETF", "NASDAQ"),
    ("SQQQ", "나스닥100 3배 인버스 ETF", "NASDAQ"),
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
    # Codex 작업 폴더에서는 통합 앱이 이웃 결과 폴더에 있고, 실제 GitHub 배포 시에는
    # 검증기와 같은 저장소 루트에 있다.
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
            isinstance(target, ast.Name) and target.id == "_BUNDLED" for target in node.targets
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
            stop_first INTEGER, detail_json TEXT, UNIQUE(market,ticker,issued)
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
    # 이전 시험 DB도 삭제 없이 새 열을 추가한다.
    existing = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
    migrations = {
        "stop_price": "REAL", "data_valid": "INTEGER NOT NULL DEFAULT 0",
        "hit1_before_stop": "INTEGER", "hit2_before_stop": "INTEGER",
        "hit3_before_stop": "INTEGER", "stop_first": "INTEGER",
    }
    for column, definition in migrations.items():
        if column not in existing:
            db.execute(f"ALTER TABLE signals ADD COLUMN {column} {definition}")
    return db


def f(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError, OverflowError):
        return default


def market_is_open(market: str, now: datetime) -> bool:
    if market == "KR":
        local = now.astimezone(KST)
        if local.weekday() >= 5:
            return False
        return clock_time(8, 50) <= local.time() <= clock_time(15, 35)
    # 미국 프리마켓부터 애프터마켓까지 시세 추적. 뉴욕 시간으로 계산해 서머타임을 반영한다.
    try:
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return False
        return clock_time(4, 0) <= ny.time() <= clock_time(20, 0)
    except Exception:
        return False


def signal_window_open(market: str, now: datetime) -> bool:
    """신규 예측을 발행하는 시간. 이후 35분은 기존 예측 채점용 시세만 받는다."""
    if market == "KR":
        local = now.astimezone(KST)
        if local.weekday() >= 5:
            return False
        return clock_time(9, 0) <= local.time() <= clock_time(15, 0)
    try:
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return False
        return clock_time(9, 30) <= ny.time() <= clock_time(16, 0)
    except Exception:
        return False


def row_for(ticker: str, name: str, exchange: str) -> dict:
    return {"ticker": ticker, "name": name, "exchange": exchange, "asset_type": "검증대상"}


def analyze_one(engine, policy, finalize, market: str, member: tuple[str, str, str]) -> dict | None:
    ticker, name, exchange = member
    mode = "국내 30분 1% 타점" if market == "KR" else "미국 30분 1% 타점"
    try:
        result = engine.analyze(row_for(ticker, name, exchange), mode)
        result = policy(finalize(result), mode)
        if f(result.get("price")) <= 0:
            raise ValueError("현재가 0")
        return result
    except Exception as exc:
        logging.warning("분석 실패 %s %s: %s", market, ticker, exc)
        return None


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
    detail = {
        key: item.get(key) for key in (
            "chart_verdict", "entry_checks_passed", "risk_reward", "rvol", "vwap", "ema9",
            "rsi", "five_min_risk_score", "change_percent", "data_completeness",
            "pullback_entry", "breakout_entry", "stop_loss", "risk_reward",
            "structural_entry", "structural_target", "structural_support",
            "level_plan_valid", "target_basis", "stop_basis",
            "continuous_rise", "continuous_rise_score", "continuous_rise_checks",
            "trend_return_5m", "trend_return_15m", "trend_return_30m",
            "up_down_volume_ratio",
            "repeat_scalp_state", "repeat_scalp_label", "repeat_scalp_reason",
            "repeat_scalp_buy_level", "repeat_scalp_sell_level",
            "repeat_scalp_invalidation", "repeat_scalp_median_bar_range",
            "repeat_scalp_reversal_score", "repeat_scalp_reversal_checks",
            "screen_change", "change", "data_gate_passed",
            "verified_spread_percent",
        )
    }
    # When the UI precision pipeline supplied a freshness-aware gate result,
    # persist that exact result. Legacy standalone runs retain the conservative
    # compatibility calculation below.
    if "data_gate_passed" in item:
        data_valid = int(bool(item.get("data_gate_passed")))
    else:
        data_valid = int(
            price > 0 and f(item.get("vwap")) > 0 and f(item.get("ema9")) > 0
            and not bool(item.get("intraday_fallback"))
            and f(item.get("data_completeness"), 100.0) >= 60.0
        )
    # 실제 발생 시각을 보존해야 5/10/20/30분 사후가격이 정확히 정렬된다.
    # 중복 방지만 별도의 시간 구간 조회로 수행한다.
    duplicate = db.execute(
        "SELECT 1 FROM signals WHERE market=? AND ticker=? AND issued BETWEEN ? AND ? LIMIT 1",
        (market, ticker, bucket, bucket + bucket_seconds - 1),
    ).fetchone()
    if duplicate:
        return
    db.execute("""
        INSERT INTO signals(
            market,ticker,name,issued,base_price,verdict,score,entry_ok,
            forecast5,forecast10,forecast20,forecast30,stop_price,data_valid,detail_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        market, ticker, str(item.get("name") or ticker), now_ts, price,
        str(item.get("chart_verdict") or "WAIT"), f(item.get("score")),
        int(bool(item.get("entry_checks_passed"))), f(item.get("forecast_5m")),
        f(item.get("forecast_10m")), f(item.get("forecast_20m")),
        f(item.get("forecast_30m")), f(item.get("stop_loss")), data_valid,
        json.dumps(detail, ensure_ascii=False, default=str),
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
    row = db.execute("""
        SELECT price,captured FROM quotes
        WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
        ORDER BY ABS(captured-?) LIMIT 1
    """, (market, ticker, target - tolerance, target + tolerance, target)).fetchone()
    return row


def grade_pending(db: sqlite3.Connection, now_ts: int) -> None:
    rows = db.execute("""
        SELECT id,market,ticker,issued,base_price,actual5,actual10,actual20,actual30,stop_price
        FROM signals WHERE result_done=0 AND issued>=?
    """, (now_ts - 3 * 86400,)).fetchall()
    for signal_id, market, ticker, issued, base, a5, a10, a20, a30, stop_price in rows:
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
            stop_index = next((index for index, (_, price) in enumerate(path) if stop > 0 and f(price) <= stop), None)
            first_target_index = next((index for index, (_, price) in enumerate(path) if f(price) >= base * 1.01), None)
            updates["stop_first"] = int(
                stop_index is not None and (first_target_index is None or stop_index < first_target_index)
            )
            for goal in (1, 2, 3):
                target = base * (1 + goal / 100)
                target_index = next((index for index, (_, price) in enumerate(path) if f(price) >= target), None)
                hit = target_index is not None and (stop_index is None or target_index < stop_index)
                updates[f"hit{goal}_before_stop"] = int(hit)
            projected = {
                5: updates.get("actual5", a5), 10: updates.get("actual10", a10),
                20: updates.get("actual20", a20), 30: updates.get("actual30", a30),
            }
            # 일시적인 인터넷/API 지연이면 15분 동안 추가 시세를 기다린다.
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
               stop_price,data_valid,hit1_before_stop,hit2_before_stop,hit3_before_stop,stop_first
        FROM signals ORDER BY issued DESC
    """).fetchall()
    headers = ["시장","티커","종목명","신호시각(KST)","기준가","판정","점수","진입통과",
               "예상5분","실제5분","예상10분","실제10분","예상20분","실제20분",
               "예상30분","실제30분","30분최대상승","30분최대하락","손절가","데이터유효",
               "+1%선도달","+2%선도달","+3%선도달","손절선도달"]
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
        SELECT market,ticker,name,entry_ok,data_valid,forecast5,actual5,forecast10,actual10,
               forecast20,actual20,forecast30,actual30,hit1_before_stop,hit2_before_stop,
               hit3_before_stop,stop_first,max_up30,max_down30
        FROM signals WHERE result_done=1 ORDER BY issued DESC
    """).fetchall()
    valid = [row for row in completed if row[4] == 1]
    entries = [row for row in valid if row[3] == 1]
    observations = [row for row in valid if row[3] != 1]
    cards = []
    for label, expected_index, actual_index in (("5분 방향",5,6),("10분 방향",7,8),("20분 방향",9,10),("30분 방향",11,12)):
        judged = [((f(row[expected_index]) >= 0) == (f(row[actual_index]) >= 0)) for row in valid if row[actual_index] is not None]
        cards.append((label, ratio(judged), len(judged), wilson_interval(judged)))
    for label, index in (("진입신호 +1%",13),("진입신호 +2%",14),("진입신호 +3%",15)):
        judged = [bool(row[index]) for row in entries if row[index] is not None]
        cards.append((label, ratio(judged), len(judged), wilson_interval(judged)))
    observation_plus_one = [bool(row[13]) for row in observations if row[13] is not None]
    cards.append((
        "관찰군 +1%", ratio(observation_plus_one), len(observation_plus_one),
        wilson_interval(observation_plus_one),
    ))
    avoided_stop = [not bool(row[16]) for row in entries if row[16] is not None]
    cards.append(("진입신호 손절회피", ratio(avoided_stop), len(avoided_stop), wilson_interval(avoided_stop)))
    recent_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value if value is not None else '-'))}</td>" for value in (
            row[0], row[1], row[2], "통과" if row[3] else "관찰", "유효" if row[4] else "제외",
            row[13], row[14], row[15], row[16], round(f(row[17]),3), round(f(row[18]),3),
        )) + "</tr>" for row in completed[:100]
    )
    card_html = "".join(
        f'<div class="card"><small>{html.escape(label)}</small><b>{value}</b><span>표본 {samples}건</span>'
        f'<span>95% 범위 {interval[0]*100:.1f}~{interval[1]*100:.1f}%</span></div>'
        if interval else f'<div class="card"><small>{html.escape(label)}</small><b>{value}</b><span>표본 없음</span></div>'
        for label, value, samples, interval in cards
    )
    plus_one = [bool(row[13]) for row in entries if row[13] is not None]
    plus_one_interval = wilson_interval(plus_one)
    observation_interval = wilson_interval(observation_plus_one)
    entry_rate = sum(plus_one) / len(plus_one) if plus_one else 0.0
    observation_rate = (
        sum(observation_plus_one) / len(observation_plus_one)
        if observation_plus_one else 0.0
    )
    selection_lift = entry_rate - observation_rate
    statistically_passed = bool(
        len(plus_one) >= 100 and len(observation_plus_one) >= 100
        and plus_one_interval and observation_interval
        and plus_one_interval[0] >= 0.80
        and plus_one_interval[0] > observation_interval[1]
        and selection_lift >= 0.05
    )
    if statistically_passed:
        warning = (
            "+1% 선도달률의 95% 신뢰하한이 80% 이상이고 관찰군보다 "
            f"{selection_lift*100:.1f}%p 높았습니다. 그래도 다른 날짜·장세의 별도 검증이 필요합니다."
        )
    elif len(valid) < 100:
        warning = "표본 100건 미만이므로 실전 판단에 사용하지 마세요. 관측 적중률이 높아도 검증 통과가 아닙니다."
    else:
        warning = (
            "현재 결과는 '+1% 성공률 80%와 관찰군 대비 선별력'을 통계적으로 입증하지 못했습니다. "
            "조건을 바꾸거나 검증을 계속해야 합니다."
        )
    document = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>초단타 자동검증 결과</title>
    <style>body{{font-family:Arial,sans-serif;max-width:1200px;margin:30px auto;padding:0 16px;color:#20242c}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#f4f6fa;border-radius:12px;padding:16px}}
    .card b,.card span{{display:block}}.card b{{font-size:28px;margin:8px 0}}.warn{{background:#fff4dc;padding:15px;border-radius:10px;margin:18px 0}}
    table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}</style>
    <h1>초단타 자동검증 결과</h1><p>완료 {len(completed)}건 · 유효 데이터 {len(valid)}건 · 실제 진입통과 {len(entries)}건</p>
    <div class='warn'>{warning}<br>표시 수익률은 세금·수수료·환전비용·슬리피지 반영 전입니다.</div><div class='cards'>{card_html}</div>
    <h2>최근 완료 신호</h2><table><thead><tr><th>시장</th><th>티커</th><th>종목</th><th>판정</th><th>데이터</th><th>+1%</th><th>+2%</th><th>+3%</th><th>손절먼저</th><th>최대상승%</th><th>최대하락%</th></tr></thead><tbody>{recent_rows}</tbody></table></html>"""
    report_path.write_text(document, encoding="utf-8")


def stop_handler(*_):
    global STOP
    STOP = True


def claim_single_instance(db: sqlite3.Connection, now_ts: int) -> bool:
    """실수로 두 번 실행해 KIS 호출이 중복되는 것을 차단한다."""
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
    db.execute("UPDATE collector_state SET heartbeat=? WHERE singleton=1 AND pid=?", (now_ts, os.getpid()))


def keep_windows_awake(enable: bool) -> None:
    """실행 중 Windows 자동 절전만 막고 종료 시 원래 동작으로 복구한다."""
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="KR", choices=("KR", "US", "BOTH"))
    parser.add_argument("--interval", type=int, default=60, help="종목별 재분석 간격(초)")
    parser.add_argument("--signal-bucket", type=int, default=300, help="동일 신호 중복 방지 간격(초)")
    parser.add_argument(
        "--run-until", default="AUTO",
        help="AUTO면 선택 시장의 추적 시간이 끝난 뒤 종료, KST HH:MM 지정 가능, 빈 값이면 계속",
    )
    parser.add_argument("--once", action="store_true", help="한 바퀴만 시험")
    args = parser.parse_args()
    setup_logging()
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    logging.info("검증기 시작 DB=%s", DB_PATH)
    try:
        engine, policy, finalize = load_engine()
    except Exception:
        logging.exception("KIS 엔진 시작 실패")
        return 2
    selected = ["KR", "US"] if args.markets == "BOTH" else [args.markets]
    seen_active: set[str] = set()
    with connect() as db:
        if not claim_single_instance(db, int(time.time())):
            logging.error("검증기가 이미 실행 중입니다. 창을 하나만 유지하세요.")
            return 3
        keep_windows_awake(True)
        try:
            while not STOP:
                started = time.time()
                now = datetime.now(KST)
                if args.run_until and args.run_until.upper() != "AUTO":
                    hour, minute = map(int, args.run_until.split(":"))
                    if now.time() >= clock_time(hour, minute):
                        logging.info("설정 종료시각 %s 도달", args.run_until)
                        break
                active = [m for m in selected if market_is_open(m, now)]
                seen_active.update(active)
                if (
                    args.run_until.upper() == "AUTO" and not active
                    and set(selected).issubset(seen_active)
                ):
                    logging.info("선택 시장의 오늘 추적 시간이 종료되었습니다.")
                    break
                if not active:
                    logging.info("선택 시장 장외시간 - 60초 대기")
                    if args.once:
                        break
                    heartbeat(db, int(time.time())); db.commit(); time.sleep(60)
                    continue
                for market in active:
                    members = KR_UNIVERSE if market == "KR" else US_UNIVERSE
                    issuing = signal_window_open(market, now)
                    for member in members:
                        if STOP:
                            break
                        timestamp = int(time.time())
                        if issuing:
                            item = analyze_one(engine, policy, finalize, market, member)
                            if item:
                                store_result(db, market, item, timestamp, max(60, args.signal_bucket))
                                logging.info("%s %s %.4f %s", market, member[0], f(item.get("price")), item.get("chart_verdict"))
                        else:
                            price = refresh_quote_only(engine, market, member)
                            store_quote(db, market, member[0], price, timestamp)
                        heartbeat(db, timestamp); db.commit()
                        time.sleep(0.35)  # 엔진 내부 제한기에 더해 종목 전환 완충
                grade_pending(db, int(time.time()))
                db.commit(); export_summary(db)
                if args.once:
                    break
                time.sleep(max(5, args.interval - (time.time() - started)))
            grade_pending(db, int(time.time())); db.commit(); export_summary(db)
        finally:
            keep_windows_awake(False)
    logging.info("검증기 정상 종료 CSV=%s", CSV_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
