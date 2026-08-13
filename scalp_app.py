# -*- coding: utf-8 -*-
"""반복단타 스캐너 v5.8 FAST UI.

중요:
- 전략 계산은 하나도 삭제하지 않는다.
- KIS 후보검색/분봉/Swing/Persistence는 단일 백그라운드 thread가 담당한다.
- Streamlit UI는 SQLite snapshot만 읽는다.
- 같은 종목 정밀계산은 새 1분봉 주기에 맞춰 재사용한다.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from run_live_validation import start_fast_worker

st.set_page_config(page_title="반복단타 스캐너 v5.8 FAST", page_icon="⚡", layout="wide")

ROOT = Path(__file__).resolve().parent
FAST_DB = ROOT / "validation_data" / "fast_scanner.sqlite3"
KST = timezone(timedelta(hours=9), name="KST")
DAEMON_STALE_SEC = 15

def n(v, default=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def fmt(v):
    x=n(v,float("nan"))
    if not math.isfinite(x) or x<=0:return "-"
    if x>=1000:return f"{x:,.0f}"
    if x>=10:return f"{x:,.2f}"
    return f"{x:,.4f}"

def db():
    FAST_DB.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(FAST_DB,timeout=2)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS fast_snapshot(
        market TEXT NOT NULL,ticker TEXT NOT NULL,name TEXT,exchange TEXT,
        updated REAL NOT NULL,precise_updated REAL NOT NULL DEFAULT 0,quote_updated REAL NOT NULL DEFAULT 0,
        score REAL NOT NULL DEFAULT 0,final_buy INTEGER NOT NULL DEFAULT 0,item_json TEXT NOT NULL,
        PRIMARY KEY(market,ticker))""")
    con.execute("""CREATE TABLE IF NOT EXISTS fast_daemon_state(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),pid INTEGER,heartbeat REAL,last_error TEXT,
        kr_pool_count INTEGER NOT NULL DEFAULT 0,us_pool_count INTEGER NOT NULL DEFAULT 0,
        kr_cursor INTEGER NOT NULL DEFAULT 0,us_cursor INTEGER NOT NULL DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ui_focus(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),market TEXT,ticker TEXT,name TEXT,exchange TEXT,updated REAL)""")
    return con

def daemon_state():
    try:
        with db() as con:
            row=con.execute("SELECT pid,heartbeat,last_error,kr_pool_count,us_pool_count,kr_cursor,us_cursor FROM fast_daemon_state WHERE singleton=1").fetchone()
        if not row:return {}
        return {"pid":row[0],"heartbeat":n(row[1]),"last_error":row[2] or "",
                "kr_pool_count":int(n(row[3])),"us_pool_count":int(n(row[4])),
                "kr_cursor":int(n(row[5])),"us_cursor":int(n(row[6]))}
    except Exception:
        return {}

def ensure_daemon():
    state=daemon_state()
    if state and time.time()-n(state.get("heartbeat"))<DAEMON_STALE_SEC:
        return state, False
    try:
        info=start_fast_worker("BOTH")
        return state, bool(info.get("started"))
    except Exception as exc:
        st.session_state["daemon_launch_error"]=f"{type(exc).__name__}: {exc}"
        return state, False

def snapshots(market):
    try:
        with db() as con:
            rows=con.execute("""SELECT ticker,name,exchange,updated,precise_updated,quote_updated,score,final_buy,item_json
                                FROM fast_snapshot WHERE market=? ORDER BY final_buy DESC,score DESC,updated DESC""",(market,)).fetchall()
        out=[]
        for r in rows:
            try:item=json.loads(r[8] or "{}")
            except Exception:item={}
            item.update(_ticker=r[0],_name=r[1] or r[0],_exchange=r[2] or "",_updated=n(r[3]),
                        _precise_updated=n(r[4]),_quote_updated=n(r[5]),_score=n(r[6]),_final_buy=bool(r[7]))
            out.append(item)
        return out
    except Exception:
        return []

def set_focus(market,item):
    ticker=str(item.get("_ticker") or item.get("ticker") or "")
    if not ticker:return
    with db() as con:
        con.execute("""INSERT INTO ui_focus(singleton,market,ticker,name,exchange,updated) VALUES(1,?,?,?,?,?)
                       ON CONFLICT(singleton) DO UPDATE SET market=excluded.market,ticker=excluded.ticker,
                       name=excluded.name,exchange=excluded.exchange,updated=excluded.updated""",
                    (market,ticker,str(item.get("_name") or item.get("name") or ticker),
                     str(item.get("_exchange") or item.get("exchange") or ""),time.time()))
        con.commit()

def age(ts):
    return max(0,int(time.time()-n(ts))) if n(ts)>0 else None

state, launched = ensure_daemon()
st_autorefresh(interval=5000,key="fast_ui_tick")

with st.sidebar:
    market_name=st.radio("시장",["국내","미국"],horizontal=True)
    market="KR" if market_name=="국내" else "US"
    min_score=st.slider("최소 점수",30,90,50,5)
    query=st.text_input("종목명/코드 필터",placeholder="LK삼양, 225190, SOXL").strip().casefold()
    st.caption("v5.8 FAST · 전략 계산은 유지하고 중복호출만 제거했습니다.")
    st.caption("현재가/위험은 백그라운드에서 빠르게, Swing 구조는 별도로 갱신합니다.")

hb_age=age(state.get("heartbeat")) if state else None
if launched:
    st.info("⚙️ 스캐너 계산 worker를 시작했습니다. 계산은 뒤에서 진행되며 화면 조작은 계속 가능합니다.")
elif hb_age is None or hb_age>DAEMON_STALE_SEC:
    err=st.session_state.get("daemon_launch_error") or state.get("last_error","")
    st.warning("백그라운드 스캐너 연결 대기 중" + (f" · {err}" if err else ""))
else:
    st.caption(f"🟢 백그라운드 스캐너 정상 · heartbeat {hb_age}초 전")

rows=snapshots(market)
if query:
    rows=[x for x in rows if query in str(x.get("_ticker","")).casefold() or query in str(x.get("_name","")).casefold()]
qualified=[x for x in rows if n(x.get("_score"))>=min_score]

st.title("⚡ 반복단타 스캐너 v5.8 FAST")
st.caption("0.5~5% 실제 반복 Swing · TREND/RANGE · 5시간 Persistence · 가짜손절/진짜붕괴")

m1,m2,m3,m4=st.columns(4)
m1.metric("백그라운드 후보풀",f"{state.get('kr_pool_count' if market=='KR' else 'us_pool_count',0)}종목")
m2.metric("화면 Snapshot",f"{len(rows)}종목")
m3.metric("점수 통과",f"{len(qualified)}종목")
m4.metric("FINAL BUY",f"{sum(bool(x.get('_final_buy')) for x in qualified)}종목")

if not rows:
    st.info("아직 계산된 Snapshot이 없습니다. 화면은 멈추지 않고 백그라운드에서 후보를 채우고 있습니다.")
    st.stop()

def cardrow(x):
    return {
        "판정":"🟢 BUY" if x.get("_final_buy") else "대기",
        "종목":f"{x.get('_ticker')} · {x.get('_name')}",
        "현재가":n(x.get("price")),
        "전략":x.get("strategy_type_v51",x.get("strategy_type","-")),
        "Swing폭":f"{n(x.get('swing_up_width_percent',x.get('repeat_scalp_range_percent'))):.2f}%",
        "확정Swing":int(n(x.get("confirmed_swing_count",x.get("repeat_oscillation_count")))),
        "Persistence":round(n(x.get("persistence_score")),1),
        "신뢰도":f"{n(x.get('evidence_confidence',x.get('persistence_confidence'))):.0f}%",
        "피로도":round(n(x.get("pattern_fatigue")),1),
        "현재가 age":f"{age(x.get('_quote_updated'))}초" if age(x.get('_quote_updated')) is not None else "-",
        "구조 age":f"{age(x.get('_precise_updated'))}초" if age(x.get('_precise_updated')) is not None else "-",
    }

st.subheader("실시간 반복단타 후보")
st.dataframe(pd.DataFrame([cardrow(x) for x in qualified[:20]]),hide_index=True,use_container_width=True)

options=qualified if qualified else rows
labels=[f"{x.get('_ticker')} · {x.get('_name')}" for x in options]
lock_key=f"focus_index::{market}"
idx=min(int(st.session_state.get(lock_key,0)),len(options)-1)
selected_label=st.selectbox("집중 분석할 종목",labels,index=idx)
selected_idx=labels.index(selected_label)
st.session_state[lock_key]=selected_idx
item=options[selected_idx]
set_focus(market,item)

# 최신 snapshot을 다음 rerun에서 background quote가 갱신하므로 UI가 KIS를 기다리지 않는다.
price=n(item.get("price"))
strategy=str(item.get("strategy_type_v51",item.get("strategy_type","NONE")))
risk=str(item.get("post_entry_risk_state","FORMING"))
swing=n(item.get("swing_up_width_percent",item.get("repeat_scalp_range_percent")))
count=int(n(item.get("confirmed_swing_count",item.get("repeat_oscillation_count"))))
persist=n(item.get("persistence_score"))
conf=n(item.get("evidence_confidence",item.get("persistence_confidence")))
fatigue=n(item.get("pattern_fatigue"))
entry=n(item.get("plan_entry",item.get("proposed_entry",item.get("repeat_scalp_buy_level"))))
t1=n(item.get("plan_target1",item.get("proposed_target1",item.get("structural_target1"))))
t2=n(item.get("plan_target2",item.get("proposed_target2",item.get("structural_target2"))))
soft=n(item.get("plan_soft_stop",item.get("proposed_soft_stop",item.get("post_entry_soft_stop"))))
hard=n(item.get("plan_hard_stop",item.get("proposed_hard_stop",item.get("post_entry_hard_stop",item.get("stop_loss")))))
eta=item.get("next_cycle_eta_minutes")
final=bool(item.get("_final_buy"))

if final:
    st.success(f"🟢 FINAL BUY · {strategy} · Swing {swing:.2f}% · 확정 {count}회")
elif risk=="HARD_EXIT":
    st.error("🚨 HARD EXIT")
elif risk in {"REAL_BREAKDOWN","WARNING"}:
    st.warning(f"🟠 {risk} · 신규진입 중지")
else:
    st.info(f"⚪ 대기 · {strategy} · Swing {swing:.2f}% · 확정 {count}회")

c=st.columns(6)
c[0].metric(f"{item.get('_ticker')} · {item.get('_name')}",fmt(price))
c[1].metric("재매수가",fmt(entry))
c[2].metric("1차 목표",fmt(t1))
c[3].metric("2차 목표",fmt(t2))
c[4].metric("Soft Stop",fmt(soft))
c[5].metric("Hard Stop",fmt(hard))

d=st.columns(6)
d[0].metric("대표 Swing",f"{swing:.2f}%")
d[1].metric("확정 반복",f"{count}회")
d[2].metric("Persistence",f"{persist:.0f}/100")
d[3].metric("근거 신뢰",f"{conf:.0f}%")
d[4].metric("패턴 피로",f"{fatigue:.0f}")
d[5].metric("다음 Cycle ETA",f"{n(eta):.0f}분" if eta is not None else "-")

st.caption(
    f"위험상태 {risk} | 현재가 {age(item.get('_quote_updated')) or 0}초 전 | "
    f"정밀구조 {age(item.get('_precise_updated')) or 0}초 전 | "
    f"화면 {datetime.now(KST).strftime('%H:%M:%S')}"
)

with st.expander("판정 근거",expanded=False):
    checks=item.get("final_buy_checks") or {}
    if checks:
        st.dataframe(pd.DataFrame([{"조건":k,"통과":"✅" if v else "❌"} for k,v in checks.items()]),
                     hide_index=True,use_container_width=True)
    reasons=item.get("final_buy_reasons") or []
    if reasons: st.write("대기/거절 이유:", " · ".join(map(str,reasons)))
    st.write("패턴 피로 이유:", " · ".join(map(str,item.get("pattern_fatigue_reasons") or [])) or "-")
