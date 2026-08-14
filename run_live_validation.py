# -*- coding: utf-8 -*-
"""KIS 초단타 신호 무인 수집·사후검증기.

app.py 번들 반복단타 엔진의 실제 confirmed Swing 0.5~5%, TREND/RANGE,
Persistence, Evidence Confidence, Pattern Fatigue, 실제 1차/2차 목표,
SHAKEOUT/REAL_BREAKDOWN/HARD_EXIT를 저장하고 사후검증한다.
+1/+2/+3% 고정 목표는 매매 목표로 사용하지 않는다.
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
            stop_first INTEGER,
            target1_price REAL, target2_price REAL, soft_stop REAL, hard_stop REAL,
            target1_hit INTEGER, target2_hit INTEGER, hard_stop_first INTEGER,
            max_up300 REAL, max_down300 REAL,
            persistence60 REAL, persistence180 REAL, persistence300 REAL,
            result_5h_done INTEGER NOT NULL DEFAULT 0,
            detail_json TEXT, UNIQUE(market,ticker,issued)
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
        "target1_price": "REAL", "target2_price": "REAL",
        "soft_stop": "REAL", "hard_stop": "REAL",
        "target1_hit": "INTEGER", "target2_hit": "INTEGER", "hard_stop_first": "INTEGER",
        "max_up300": "REAL", "max_down300": "REAL",
        "persistence60": "REAL", "persistence180": "REAL", "persistence300": "REAL",
        "result_5h_done": "INTEGER NOT NULL DEFAULT 0",
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
        return local.weekday() < 5 and clock_time(9, 0) <= local.time() <= clock_time(15, 0)
    try:
        from zoneinfo import ZoneInfo
        ny = now.astimezone(ZoneInfo("America/New_York"))
        return ny.weekday() < 5 and clock_time(4, 0) <= ny.time() <= clock_time(20, 0)
    except Exception:
        return False


def row_for(ticker: str, name: str, exchange: str) -> dict:
    return {"ticker": ticker, "name": name, "exchange": exchange, "asset_type": "검증대상"}


def analyze_one(engine, policy, finalize, market: str, member: tuple[str, str, str]) -> dict | None:
    ticker, name, exchange = member
    mode = "국내 반복단타" if market == "KR" else "미국 반복단타"
    try:
        result = engine.analyze(row_for(ticker, name, exchange), mode)
        result = policy(finalize(result), mode)
        if f(result.get("price")) <= 0:
            raise ValueError("현재가 0")
        return result
    except Exception as exc:
        logging.warning("분석 실패 %s %s: %s", market, ticker, exc)
        return None


DETAIL_KEYS = (
    "chart_verdict", "entry_checks_passed", "FINAL_BUY", "score",
    "repeat_strategy_valid", "repeat_strategy_state", "repeat_strategy_reason",
    "swing_type", "confirmed_swing_count", "confirmed_swing_widths", "confirmed_swing_signature",
    "repeat_swing_width_percent", "repeat_swing_min_percent", "repeat_swing_max_percent",
    "persistence_score", "persistence_5h_status", "structure_observed_minutes",
    "evidence_confidence", "pattern_fatigue",
    "rvol", "vwap", "ema9", "ema20", "rsi", "five_min_risk_score",
    "change_percent", "screen_change", "change", "data_completeness",
    "structural_entry", "structural_support", "structural_hard_stop",
    "soft_stop", "hard_stop", "structural_target1", "structural_target2",
    "target1_upside_percent", "target2_upside_percent",
    "target1_timeframe", "target2_timeframe", "target1_basis", "target2_basis",
    "structure_support_5m", "structure_support_15m", "structure_support_60m",
    "structure_resistance_5m", "structure_resistance_15m", "structure_resistance_60m",
    "net_swing_percent", "net_swing_basis",
    "breakdown_state", "SHAKEOUT", "REAL_BREAKDOWN", "HARD_EXIT",
    "rsi_pullback_reference", "ma20_reference_status",
    "verified_spread_percent", "spread_pct", "data_gate_passed",
)


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

    duplicate = db.execute(
        "SELECT 1 FROM signals WHERE market=? AND ticker=? AND issued BETWEEN ? AND ? LIMIT 1",
        (market, ticker, bucket, bucket + bucket_seconds - 1),
    ).fetchone()
    if duplicate:
        return

    db.execute("""
        INSERT INTO signals(
            market,ticker,name,issued,base_price,verdict,score,entry_ok,
            forecast5,forecast10,forecast20,forecast30,stop_price,data_valid,
            target1_price,target2_price,soft_stop,hard_stop,detail_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        market, ticker, str(item.get("name") or ticker), now_ts, price,
        str(item.get("chart_verdict") or "WAIT"), f(item.get("score")),
        int(bool(item.get("FINAL_BUY"))),
        f(item.get("forecast_5m")), f(item.get("forecast_10m")),
        f(item.get("forecast_20m")), f(item.get("forecast_30m")),
        f(item.get("soft_stop", item.get("stop_loss"))), data_valid,
        f(item.get("structural_target1")), f(item.get("structural_target2")),
        f(item.get("soft_stop")), f(item.get("hard_stop")),
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
    return db.execute("""
        SELECT price,captured FROM quotes
        WHERE market=? AND ticker=? AND captured BETWEEN ? AND ?
        ORDER BY ABS(captured-?) LIMIT 1
    """, (market, ticker, target - tolerance, target + tolerance, target)).fetchone()


def nearest_persistence_snapshot(
    db: sqlite3.Connection, market: str, ticker: str, target: int, tolerance: int = 600
) -> float | None:
    rows = db.execute("""
        SELECT issued,detail_json FROM signals
        WHERE market=? AND ticker=? AND issued BETWEEN ? AND ?
        ORDER BY ABS(issued-?) LIMIT 5
    """, (market,ticker,target-tolerance,target+tolerance,target)).fetchall()
    for _, detail_json in rows:
        try:
            detail=json.loads(detail_json or "{}")
            value=detail.get("persistence_score")
            if value is not None:
                return f(value)
        except Exception:
            continue
    return None


def _first_index(path: list[tuple[int, float]], predicate) -> int | None:
    return next((i for i, (_, price) in enumerate(path) if predicate(f(price))), None)


def grade_pending(db: sqlite3.Connection, now_ts: int) -> None:
    rows = db.execute("""
        SELECT id,market,ticker,issued,base_price,actual5,actual10,actual20,actual30,
               target1_price,target2_price,soft_stop,hard_stop,result_done,result_5h_done,
               persistence60,persistence180,persistence300,detail_json
        FROM signals
        WHERE (result_done=0 OR result_5h_done=0) AND issued>=?
    """, (now_ts - 7 * 86400,)).fetchall()

    for row in rows:
        (signal_id, market, ticker, issued, base, a5, a10, a20, a30,
         target1, target2, soft_stop, hard_stop, result_done, result_5h_done,
         persistence60, persistence180, persistence300, detail_json) = row
        updates = {}

        # Persistence는 최초값 복사가 아니라 60/180/300분 시점의 재분석 신호에서 가져온다.
        for minutes, existing in ((60,persistence60),(180,persistence180),(300,persistence300)):
            if existing is None and now_ts >= issued + minutes*60:
                snapshot = nearest_persistence_snapshot(db, market, ticker, issued + minutes*60)
                if snapshot is not None:
                    updates[f"persistence{minutes}"] = snapshot
        for minutes, existing in ((5,a5),(10,a10),(20,a20),(30,a30)):
            if existing is None and now_ts >= issued + minutes*60:
                quote = nearest_quote(db, market, ticker, issued + minutes*60)
                if quote and base > 0:
                    updates[f"actual{minutes}"] = (f(quote[0])/base - 1)*100

        path30 = []
        if now_ts >= issued + 30*60 and not result_done:
            path30 = db.execute("""
                SELECT captured,price FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ? ORDER BY captured
            """, (market,ticker,issued,issued+30*60)).fetchall()
            if path30:
                prices=[f(p) for _,p in path30 if f(p)>0]
                if prices and base>0:
                    updates["max_up30"]=(max(prices)/base-1)*100
                    updates["max_down30"]=(min(prices)/base-1)*100
                t1i=_first_index(path30, lambda p: f(target1)>0 and p>=f(target1))
                t2i=_first_index(path30, lambda p: f(target2)>0 and p>=f(target2))
                hardi=_first_index(path30, lambda p: f(hard_stop)>0 and p<=f(hard_stop))
                updates["target1_hit"] = int(t1i is not None and (hardi is None or t1i<hardi)) if f(target1)>0 else None
                updates["target2_hit"] = int(t2i is not None and (hardi is None or t2i<hardi)) if f(target2)>0 else None
                updates["hard_stop_first"] = int(hardi is not None and (t1i is None or hardi<t1i)) if f(hard_stop)>0 else None
            projected={5:updates.get("actual5",a5),10:updates.get("actual10",a10),20:updates.get("actual20",a20),30:updates.get("actual30",a30)}
            if all(v is not None for v in projected.values()) or now_ts>=issued+45*60:
                updates["result_done"]=1

        # 5시간 지속성은 실제 300분 시세가 있는 표본만 완료 처리한다.
        if now_ts >= issued + 300*60 and not result_5h_done:
            path300 = db.execute("""
                SELECT captured,price FROM quotes
                WHERE market=? AND ticker=? AND captured BETWEEN ? AND ? ORDER BY captured
            """, (market,ticker,issued,issued+300*60)).fetchall()
            if path300:
                prices=[f(p) for _,p in path300 if f(p)>0]
                if prices and base>0:
                    updates["max_up300"]=(max(prices)/base-1)*100
                    updates["max_down300"]=(min(prices)/base-1)*100

                # 실제 1·2차 목표/Hard Stop은 30분 한정이 아니라
                # 최대 5시간 경로에서 최종 선도달 순서를 다시 판정한다.
                t1i=_first_index(path300, lambda p: f(target1)>0 and p>=f(target1))
                t2i=_first_index(path300, lambda p: f(target2)>0 and p>=f(target2))
                hardi=_first_index(path300, lambda p: f(hard_stop)>0 and p<=f(hard_stop))
                if f(target1)>0:
                    updates["target1_hit"] = int(t1i is not None and (hardi is None or t1i<hardi))
                if f(target2)>0:
                    updates["target2_hit"] = int(t2i is not None and (hardi is None or t2i<hardi))
                if f(hard_stop)>0:
                    updates["hard_stop_first"] = int(hardi is not None and (t1i is None or hardi<t1i))

                if len(path300) >= 240:  # at least 80% of expected minute samples
                    # 300분 Persistence는 위의 실제 재분석 snapshot이 있을 때만 기록된다.
                    updates["result_5h_done"] = 1

        if updates:
            assignments=",".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE signals SET {assignments} WHERE id=?", (*updates.values(),signal_id))


def export_summary(db: sqlite3.Connection) -> None:
    rows=db.execute("""
        SELECT market,ticker,name,datetime(issued,'unixepoch','+9 hours'),base_price,
               verdict,score,entry_ok,forecast5,actual5,forecast10,actual10,
               forecast20,actual20,forecast30,actual30,max_up30,max_down30,
               target1_price,target1_hit,target2_price,target2_hit,soft_stop,hard_stop,hard_stop_first,
               max_up300,max_down300,persistence300,result_5h_done
        FROM signals ORDER BY issued DESC
    """).fetchall()
    headers=[
        "시장","티커","종목명","신호시각(KST)","기준가","판정","점수","FINAL_BUY",
        "예상5분","실제5분","예상10분","실제10분","예상20분","실제20분","예상30분","실제30분",
        "30분최대상승","30분최대하락","실제1차목표","1차선도달","실제2차목표","2차선도달",
        "SoftStop","HardStop","HardStop먼저","5시간최대상승","5시간최대하락","Persistence300","5시간검증완료",
    ]
    with CSV_PATH.open("w",newline="",encoding="utf-8-sig") as handle:
        writer=csv.writer(handle); writer.writerow(headers); writer.writerows(rows)
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
    report_path=DB_PATH.parent/"validation_report.html"
    completed=db.execute("""
        SELECT market,ticker,name,entry_ok,data_valid,forecast5,actual5,forecast10,actual10,
               forecast20,actual20,forecast30,actual30,target1_hit,target2_hit,hard_stop_first,
               max_up30,max_down30,result_5h_done,max_up300,max_down300,persistence300
        FROM signals WHERE result_done=1 ORDER BY issued DESC
    """).fetchall()
    valid=[r for r in completed if r[4]==1]
    entries=[r for r in valid if r[3]==1]
    cards=[]
    for label,ei,ai in (("5분 방향",5,6),("10분 방향",7,8),("20분 방향",9,10),("30분 방향",11,12)):
        judged=[(f(r[ei])>=0)==(f(r[ai])>=0) for r in valid if r[ai] is not None]
        cards.append((label,ratio(judged),len(judged)))
    t1=[bool(r[13]) for r in entries if r[13] is not None]
    t2=[bool(r[14]) for r in entries if r[14] is not None]
    hard_avoid=[not bool(r[15]) for r in entries if r[15] is not None]
    cards += [("실제 1차 선도달",ratio(t1),len(t1)),("실제 2차 선도달",ratio(t2),len(t2)),("HardStop 선도달 회피",ratio(hard_avoid),len(hard_avoid))]
    fiveh=[r for r in entries if r[18]==1]
    card_html="".join(f'<div class="card"><small>{html.escape(label)}</small><b>{value}</b><span>표본 {n}건</span></div>' for label,value,n in cards)
    recent="".join("<tr>"+"".join(f"<td>{html.escape(str(v if v is not None else '-'))}</td>" for v in (r[0],r[1],r[2],"YES" if r[3] else "NO",r[13],r[14],r[15],round(f(r[16]),3),round(f(r[17]),3),r[18],round(f(r[19]),3),round(f(r[20]),3),r[21]))+"</tr>" for r in completed[:100])
    warning=("5시간 실전표본이 아직 없습니다. Persistence 5시간 성공률을 추정하지 않았습니다." if not fiveh else f"5시간 검증완료 {len(fiveh)}건. 서로 다른 날짜·장세에서 추가 검증이 필요합니다.")
    doc=f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>반복단타 자동검증</title>
    <style>body{{font-family:Arial,sans-serif;max-width:1200px;margin:30px auto;padding:0 16px;color:#20242c}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#f4f6fa;border-radius:12px;padding:16px}}.card b,.card span{{display:block}}.card b{{font-size:26px;margin:8px 0}}.warn{{background:#fff4dc;padding:15px;border-radius:10px;margin:18px 0}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{border-bottom:1px solid #ddd;padding:7px}}</style>
    <h1>반복단타 자동검증</h1><p>완료 {len(completed)}건 · 유효 {len(valid)}건 · FINAL_BUY {len(entries)}건</p><div class='warn'>{warning}</div><div class='cards'>{card_html}</div>
    <h2>최근 신호</h2><table><thead><tr><th>시장</th><th>티커</th><th>종목</th><th>FINAL_BUY</th><th>1차</th><th>2차</th><th>Hard먼저</th><th>30m최대+</th><th>30m최대-</th><th>5h완료</th><th>5h최대+</th><th>5h최대-</th><th>Persistence</th></tr></thead><tbody>{recent}</tbody></table></html>"""
    report_path.write_text(doc,encoding="utf-8")


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="KR", choices=("KR", "US", "BOTH"))
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--signal-bucket", type=int, default=300)
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
                    repeat_mode = "국내 반복단타" if market == "KR" else "미국 반복단타"
                    fallback_members = KR_UNIVERSE if market == "KR" else US_UNIVERSE
                    try:
                        discovered = engine.candidates(repeat_mode)
                    except Exception as discovery_error:
                        logging.warning("후보검색 실패 %s: %s", market, discovery_error)
                        discovered = []
                    dynamic_members = [
                        (str(row.get("ticker")), str(row.get("name") or row.get("ticker")), str(row.get("exchange") or ("KR" if market=="KR" else "NASDAQ")))
                        for row in discovered if row.get("ticker")
                    ]
                    members = dynamic_members[:20] or fallback_members
                    issuing = signal_window_open(market, now)
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
