# -*- coding: utf-8 -*-
"""반복단타 스캐너 v6.1 PRACTICAL — 단타 참고용 UI.

원칙
- UI는 KIS/전략 계산을 직접 하지 않는다.
- worker snapshot만 읽는다.
- 실제 FINAL BUY와 '참고용 대기/회피'를 분리한다.
- 0이나 오래된 값을 매매가처럼 보여주지 않는다.
"""
from __future__ import annotations
import json, math, sqlite3, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

UI_VERSION="v6.1-practical-ui"
EXPECTED_ENGINE_VERSION="v6.1-practical-scalp-core"
ROOT=Path(__file__).resolve().parent
FAST_DB=ROOT/"validation_data"/"fast_scanner.sqlite3"
KST=timezone(timedelta(hours=9),name="KST")
st.set_page_config(page_title="반복단타 스캐너 v6.1",page_icon="⚡",layout="wide")

def n(v,d=0.0):
    try:
        x=float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d

def fmt(v):
    x=n(v,float("nan"))
    if not math.isfinite(x) or x<=0:return "-"
    if x>=1000:return f"{x:,.0f}"
    if x>=10:return f"{x:,.2f}"
    return f"{x:,.4f}"

def age(ts):
    return max(0,int(time.time()-n(ts))) if n(ts)>0 else None

def connect(readonly=False):
    FAST_DB.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(FAST_DB,timeout=.30)
    con.execute("PRAGMA busy_timeout=300")
    if readonly:
        try: con.execute("PRAGMA query_only=ON")
        except Exception: pass
    return con

def ensure_tables():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS ui_focus(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            market TEXT,ticker TEXT,name TEXT,exchange TEXT,updated REAL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS ui_control(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            active_market TEXT NOT NULL DEFAULT 'KR',updated REAL NOT NULL DEFAULT 0)""")
        con.execute("INSERT OR IGNORE INTO ui_control(singleton,active_market,updated) VALUES(1,'KR',0)")
        con.commit()

def daemon_state():
    try:
        with connect(True) as con:
            r=con.execute("""SELECT pid,heartbeat,last_error,kr_pool_count,us_pool_count,
                kr_cursor,us_cursor,last_loop_ms,last_api_ms,cache_skips
                FROM fast_daemon_state WHERE singleton=1""").fetchone()
        if not r:return {}
        return dict(pid=r[0],heartbeat=n(r[1]),last_error=r[2] or "",
                    kr_pool_count=int(n(r[3])),us_pool_count=int(n(r[4])),
                    kr_cursor=int(n(r[5])),us_cursor=int(n(r[6])),
                    last_loop_ms=n(r[7]),last_api_ms=n(r[8]),cache_skips=int(n(r[9])))
    except Exception as exc:
        return {"last_error":f"snapshot DB: {type(exc).__name__}: {exc}"}

def snapshots(market):
    try:
        with connect(True) as con:
            rs=con.execute("""SELECT ticker,name,exchange,updated,precise_updated,quote_updated,
                score,final_buy,item_json FROM fast_snapshot
                WHERE market=? ORDER BY final_buy DESC,score DESC,updated DESC LIMIT 250""",(market,)).fetchall()
        out=[]
        for r in rs:
            try:x=json.loads(r[8] or "{}")
            except Exception:x={}
            x.update(_ticker=r[0],_name=r[1] or r[0],_exchange=r[2] or "",
                     _updated=n(r[3]),_precise_updated=n(r[4]),_quote_updated=n(r[5]),
                     _score=n(r[6]),_final_buy=bool(r[7]))
            out.append(x)
        return out
    except Exception:
        return []

def set_market(market):
    try:
        with connect() as con:
            con.execute("""INSERT INTO ui_control(singleton,active_market,updated) VALUES(1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET active_market=excluded.active_market,updated=excluded.updated""",
                (market,time.time()))
            con.commit()
    except Exception:
        pass

def set_focus(market,x):
    ticker=str(x.get("_ticker") or "")
    if not ticker:return
    try:
        with connect() as con:
            con.execute("""INSERT INTO ui_focus(singleton,market,ticker,name,exchange,updated)
                VALUES(1,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET
                market=excluded.market,ticker=excluded.ticker,name=excluded.name,
                exchange=excluded.exchange,updated=excluded.updated""",
                (market,ticker,str(x.get("_name") or ticker),str(x.get("_exchange") or ""),time.time()))
            con.commit()
    except Exception:
        pass

def practical_status(x, hb_age):
    qage=age(x.get("_quote_updated"))
    sage=age(x.get("_precise_updated"))
    risk=str(x.get("post_entry_risk_state") or "FORMING")
    data_ok=bool(x.get("data_freshness_pass"))
    swing_ok=int(n(x.get("confirmed_swing_count")))>=3 and .5<=n(x.get("swing_up_width_percent"))<=5
    engine_ok=str(x.get("strategy_engine_version") or x.get("strategy_version") or "")==EXPECTED_ENGINE_VERSION
    stale = hb_age is None or hb_age>20 or qage is None or qage>15 or sage is None or sage>180

    if risk=="HARD_EXIT":
        return "🚨 즉시회피","HARD_EXIT"
    if risk in {"REAL_BREAKDOWN","WARNING"}:
        return "🔴 신규진입 금지",risk
    if stale or not data_ok or not engine_ok:
        return "⚪ 데이터 확인중","STALE"
    if bool(x.get("_final_buy")) and swing_ok:
        return "🟢 매수 검토","FINAL_BUY"
    if swing_ok:
        return "🟡 눌림/재진입 대기","WATCH"
    return "⚪ 반복형성 관찰","FORMING"

def rr_text(x):
    rr=n(x.get("execution_effective_rr",x.get("risk_reward")))
    return f"{rr:.2f}" if rr>0 else "-"

ensure_tables()
if st_autorefresh:
    st_autorefresh(interval=2500,key="v61_practical_tick")

state=daemon_state()
hb=age(state.get("heartbeat"))

with st.sidebar:
    market_name=st.radio("시장",["국내","미국"],horizontal=True)
    market="KR" if market_name=="국내" else "US"
    set_market(market)
    min_score=st.slider("최소 모델점수",0,90,45,5)
    only_repeat=st.toggle("확정 Swing 3회 이상만",False)
    query=st.text_input("종목명/코드 필터",placeholder="현대차, 005380, SOXL").strip().casefold()
    st.caption(f"UI {UI_VERSION}")
    st.caption(f"실행파일 {Path(__file__).resolve()}")

st.title("⚡ 반복단타 스캐너 v6.1 · 단타 참고용")
st.caption("거래량 TOP100 ∪ 거래대금 TOP100 → 0.5~5% confirmed Swing → TREND/RANGE → Persistence → Risk")
st.caption("자동매매가 아니라 수동 진입 참고용입니다. 매수 검토는 신선한 시세·구조·FINAL BUY가 동시에 살아 있을 때만 표시합니다.")

if hb is None or hb>20:
    st.error("🔴 worker가 꺼져 있거나 20초 이상 멈췄습니다. 화면 가격을 매매 판단에 사용하지 마세요.")
else:
    st.caption(f"🟢 worker {hb}초 전 · PID {state.get('pid')} · KIS 정밀호출 {n(state.get('last_api_ms')):.0f}ms · loop {n(state.get('last_loop_ms')):.0f}ms")

if state.get("last_error"):
    st.warning(state["last_error"])

rows=snapshots(market)
if query:
    rows=[x for x in rows if query in str(x.get("_ticker","")).casefold() or query in str(x.get("_name","")).casefold()]
rows=[x for x in rows if n(x.get("_score"))>=min_score]
if only_repeat:
    rows=[x for x in rows if int(n(x.get("confirmed_swing_count")))>=3 and .5<=n(x.get("swing_up_width_percent"))<=5]

m=st.columns(5)
m[0].metric("유동성 후보",state.get("kr_pool_count" if market=="KR" else "us_pool_count",0))
m[1].metric("화면 후보",len(rows))
m[2].metric("FINAL BUY",sum(bool(x.get("_final_buy")) for x in rows))
m[3].metric("확정 반복",sum(int(n(x.get("confirmed_swing_count")))>=3 for x in rows))
m[4].metric("캐시 재사용",int(n(state.get("cache_skips"))))

if not rows:
    st.info("현재 조건에 맞는 snapshot이 없습니다. 최소 점수를 낮추거나 worker 상태를 확인하세요.")
    st.stop()

def board_row(x):
    status,_=practical_status(x,hb)
    return {
        "상태":status,
        "종목":f"{x.get('_ticker')} · {x.get('_name')}",
        "현재가":fmt(x.get("price")),
        "유형":x.get("strategy_type_v51",x.get("strategy_type","-")),
        "Swing":f"{n(x.get('swing_up_width_percent')):.2f}%",
        "반복":int(n(x.get("confirmed_swing_count"))),
        "Persistence":round(n(x.get("persistence_score")),1),
        "Evidence":f"{n(x.get('evidence_confidence')):.0f}%",
        "Fatigue":round(n(x.get("pattern_fatigue")),1),
        "RR":rr_text(x),
        "시세":f"{age(x.get('_quote_updated'))}초" if age(x.get("_quote_updated")) is not None else "-",
        "구조":f"{age(x.get('_precise_updated'))}초" if age(x.get("_precise_updated")) is not None else "-",
    }

st.subheader("후보판")
st.dataframe(pd.DataFrame([board_row(x) for x in rows[:40]]),hide_index=True,use_container_width=True,height=430)

labels=[f"{x.get('_ticker')} · {x.get('_name')}" for x in rows]
previous=st.session_state.get(f"locked_focus_{market}")
default_index=labels.index(previous) if previous in labels else 0
selected=st.selectbox("집중 분석할 종목",labels,index=default_index,key=f"focus_select_{market}")
st.session_state[f"locked_focus_{market}"]=selected
x=rows[labels.index(selected)]
set_focus(market,x)

status,code=practical_status(x,hb)
engine=str(x.get("strategy_engine_version") or x.get("strategy_version") or "-")
price=n(x.get("price"))
risk=str(x.get("post_entry_risk_state") or "FORMING")
strategy=str(x.get("strategy_type_v51",x.get("strategy_type","NONE")))
entry=n(x.get("plan_entry",x.get("proposed_entry")))
t1=n(x.get("plan_target1",x.get("proposed_target1")))
t2=n(x.get("plan_target2",x.get("proposed_target2")))
soft=n(x.get("plan_soft_stop",x.get("proposed_soft_stop")))
hard=n(x.get("plan_hard_stop",x.get("proposed_hard_stop")))
swing=n(x.get("swing_up_width_percent"))
count=int(n(x.get("confirmed_swing_count")))
persist=n(x.get("persistence_score"))
evidence=n(x.get("evidence_confidence"))
fatigue=n(x.get("pattern_fatigue"))
eta=x.get("next_cycle_eta_minutes")

if code=="FINAL_BUY":
    st.success(f"{status} · {strategy} · Swing {swing:.2f}% · {count}회")
elif code in {"HARD_EXIT","REAL_BREAKDOWN","WARNING"}:
    st.error(f"{status} · {risk}")
else:
    st.info(f"{status} · {strategy} · Swing {swing:.2f}% · {count}회")

a=st.columns(6)
a[0].metric(f"{x.get('_ticker')} · {x.get('_name')}",fmt(price))
a[1].metric("재매수가",fmt(entry))
a[2].metric("1차 목표",fmt(t1))
a[3].metric("2차 목표",fmt(t2))
a[4].metric("Soft Stop",fmt(soft))
a[5].metric("Hard Stop",fmt(hard))

b=st.columns(6)
b[0].metric("대표 Swing",f"{swing:.2f}%")
b[1].metric("확정 반복",f"{count}회")
b[2].metric("Persistence",f"{persist:.0f}/100")
b[3].metric("Evidence",f"{evidence:.0f}%")
b[4].metric("Fatigue",f"{fatigue:.0f}")
b[5].metric("Cycle ETA",f"{n(eta):.0f}분" if eta is not None else "-")

# 사용자가 실제로 보기 쉬운 3줄 요약. 새로운 전략 계산이 아니라 기존 결과를 표현만 한다.
if code=="FINAL_BUY" and entry>0 and t1>0 and hard>0:
    st.markdown(
        f"**진입:** {fmt(entry)} 부근 재매수 구간 확인 후 수동 진입 검토  \n"
        f"**손절:** Soft {fmt(soft)}는 흔들림 관찰, Hard {fmt(hard)} 이탈은 즉시 회피  \n"
        f"**익절:** 1차 {fmt(t1)} / 2차 {fmt(t2)} · Risk {risk}"
    )
else:
    reasons=x.get("final_buy_reasons") or []
    st.markdown(
        f"**진입:** 지금은 {status}  \n"
        f"**손절:** Risk {risk} · Hard {fmt(hard)}  \n"
        f"**대기 이유:** {' · '.join(map(str,reasons[:4])) if reasons else '반복구조/진입구간 확인 중'}"
    )

st.caption(
    f"quote age {age(x.get('_quote_updated'))}초 | structure age {age(x.get('_precise_updated'))}초 | "
    f"position {x.get('position_state','-')} | plan {x.get('plan_id',x.get('proposed_plan_id','-'))} | engine {engine}"
)

with st.expander("판정 근거",expanded=False):
    checks=x.get("final_buy_checks") or {}
    if checks:
        st.dataframe(pd.DataFrame([{"조건":k,"통과":"✅" if v else "❌"} for k,v in checks.items()]),
                     hide_index=True,use_container_width=True)
    st.write("거절 이유:"," · ".join(map(str,x.get("final_buy_reasons") or [])) or "-")
    st.write("피로도 이유:"," · ".join(map(str,x.get("pattern_fatigue_reasons") or [])) or "-")
