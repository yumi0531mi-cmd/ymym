# -*- coding: utf-8 -*-
"""반복단타 스캐너 v5.9 공통 전략 엔진.

목적
- 실제 1분봉에서 0.5~5.0% 완성 Swing을 미래봉 없이 탐지
- TREND_SWING / RANGE_SWING 분류
- 5시간 Persistence와 Evidence Confidence 분리
- 가짜손절(SHAKEOUT)과 실제 붕괴를 상태로 분리
- 비용/스프레드/틱을 뺀 Net Swing으로 실거래 가능성 확인
- 진입 plan_id 및 PRE_ENTRY/IN_POSITION/EXITED 상태
- 후보판/집중분석/검증기가 동일 evaluate_strategy() 사용

주의: 99%는 '설계 항목의 코드 반영 목표'이며 수익률/무오류 보장이 아니다.
"""
from __future__ import annotations
import json, math, sqlite3, time, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import pandas as pd
except Exception:
    pd = None

KST = timezone(timedelta(hours=9), name="KST")
ET = ZoneInfo("America/New_York")
VERSION = "v5.9-liquidity100-union"
SCHEMA_VERSION = "v5.5-schema-1"
PLAN_VERSION = "v5.5-plan-1"
SWING_MIN, SWING_MAX, MIN_SWINGS = 0.50, 5.00, 3
TARGET_HORIZON_MIN = 300

def _num(v:Any, default:float=0.0)->float:
    try:
        x=float(v); return x if math.isfinite(x) else default
    except Exception: return default

def _clamp(x,lo,hi): return max(lo,min(hi,x))
def _median(xs, default=0.0):
    a=sorted(float(x) for x in xs if math.isfinite(float(x)))
    if not a:return default
    n=len(a); m=n//2
    return a[m] if n%2 else (a[m-1]+a[m])/2

def _series(item,key):
    out=[]
    for v in item.get(key,[]) or []:
        x=_num(v,float("nan"))
        if math.isfinite(x): out.append(x)
    return out

def _market_code(market):
    s=str(market or "").upper()
    return "KR" if s in {"KR","국내"} or s.startswith("국내") else "US"

def _tick(price, market):
    if price<=0:return 0.0
    if _market_code(market)=="US": return 0.0001 if price<1 else 0.01
    if price<2000:return 1
    if price<5000:return 5
    if price<20000:return 10
    if price<50000:return 50
    if price<200000:return 100
    if price<500000:return 500
    return 1000

def _round_tick(price, market):
    t=_tick(price,market)
    return round(price/t)*t if t else price

def _session_bounds(market, now):
    if _market_code(market)=="KR":
        local=now.astimezone(KST)
        if local.weekday()>=5:return None,None,"국내 휴장"
        return local.replace(hour=9,minute=0,second=0,microsecond=0), local.replace(hour=15,minute=30,second=0,microsecond=0), "국내 정규장"
    local=now.astimezone(ET)
    if local.weekday()>=5:return None,None,"미국 휴장"
    m=local.hour*60+local.minute
    if 4*60<=m<9*60+30:
        return local.replace(hour=4,minute=0,second=0,microsecond=0),local.replace(hour=9,minute=30,second=0,microsecond=0),"미국 프리마켓"
    if 9*60+30<=m<16*60:
        return local.replace(hour=9,minute=30,second=0,microsecond=0),local.replace(hour=16,minute=0,second=0,microsecond=0),"미국 정규장"
    if 16*60<=m<20*60:
        return local.replace(hour=16,minute=0,second=0,microsecond=0),local.replace(hour=20,minute=0,second=0,microsecond=0),"미국 애프터마켓"
    return None,None,"미국 장외시간"

def session_horizon(market, now_ts=None):
    now=datetime.fromtimestamp(now_ts or time.time(),tz=KST)
    op,cl,label=_session_bounds(market,now)
    if not op:return dict(session_label=label,tradable=False,elapsed_minutes=0,remaining_minutes=0,persistence_horizon_minutes=0,late_session=True)
    local=now.astimezone(op.tzinfo)
    elapsed=max(0,int((local-op).total_seconds()//60)); remain=max(0,int((cl-local).total_seconds()//60))
    return dict(session_label=label,tradable=op<=local<cl,elapsed_minutes=elapsed,remaining_minutes=remain,
                persistence_horizon_minutes=min(300,remain),late_session=remain<45)

def _bar_age_seconds(item, market, now_ts):
    times=item.get("chart_time_1m",[]) or []
    if not times or pd is None:return None
    try:
        t=pd.Timestamp(pd.to_datetime(times[-1]))
        if t.tzinfo is None:t=t.tz_localize(KST if _market_code(market)=="KR" else ET)
        now=pd.Timestamp(datetime.fromtimestamp(now_ts,tz=timezone.utc))
        return max(0.0,(now-t.tz_convert("UTC")).total_seconds())
    except Exception:return None

def _atr_pct(highs,lows,closes,n=14):
    k=min(len(closes),len(highs),len(lows))
    if k<3:return 0.0
    trs=[]
    for i in range(max(1,k-n),k):
        prev=closes[i-1]
        trs.append(max(highs[i]-lows[i],abs(highs[i]-prev),abs(lows[i]-prev)))
    atr=_median(trs)
    return atr/closes[-1]*100 if closes[-1]>0 else 0.0

def online_swing_plan(item, market):
    """확정은 반대방향 reversal이 발생한 시점에만 한다. 미래봉을 보지 않는다."""
    out=dict(item)
    highs=_series(out,"chart_high_1m"); lows=_series(out,"chart_low_1m"); closes=_series(out,"chart_close_1m")
    times=list(out.get("chart_time_1m",[]) or [])
    n=min(len(highs),len(lows),len(closes),len(times) if times else 10**9)
    if n<8:
        out.update(online_swing_valid=False, online_swing_reason="1분봉 표본부족", confirmed_swing_count=0)
        return out
    highs,lows,closes=highs[-n:],lows[-n:],closes[-n:]
    times=(times[-n:] if times else list(range(n)))
    spread=_num(out.get("verified_spread_percent",out.get("repeat_spread_percent")),-1)
    atrp=_atr_pct(highs,lows,closes)
    tickp=_tick(closes[-1],market)/closes[-1]*100 if closes[-1]>0 else 0
    cost=max(0.0,spread if spread>=0 else 0.0)+tickp*2+(_num(out.get("expected_slippage_percent"),0.03 if _market_code(market)=="KR" else 0.05)*2)
    reversal=_clamp(max(0.22,atrp*0.75,tickp*4,cost*1.25),0.22,2.0)

    piv=[]; direction=0
    # direction 0: 아직 첫 방향 미확정. running low/high에서 threshold가 나오면 시작한다.
    run_low=lows[0]; low_i=0
    run_high=highs[0]; high_i=0
    extreme=closes[0]; ex_i=0
    for i in range(1,n):
        h,l=highs[i],lows[i]
        if direction==0:
            if l<run_low: run_low=l; low_i=i
            if h>run_high: run_high=h; high_i=i
            up=(h/run_low-1)*100 if run_low>0 else 0
            down=(1-l/run_high)*100 if run_high>0 else 0
            if up>=reversal and low_i< i:
                piv.append(("L",low_i,run_low,times[low_i]))
                direction=1; extreme=h; ex_i=i
            elif down>=reversal and high_i< i:
                piv.append(("H",high_i,run_high,times[high_i]))
                direction=-1; extreme=l; ex_i=i
            continue
        if direction==1:
            if h>=extreme: extreme=h; ex_i=i
            rev=(extreme-l)/extreme*100 if extreme>0 else 0
            if rev>=reversal:
                piv.append(("H",ex_i,extreme,times[ex_i]))
                direction=-1; extreme=l; ex_i=i
        else:
            if l<=extreme: extreme=l; ex_i=i
            rev=(h-extreme)/extreme*100 if extreme>0 else 0
            if rev>=reversal:
                piv.append(("L",ex_i,extreme,times[ex_i]))
                direction=1; extreme=h; ex_i=i

    # 동일 타입 연속 pivot 정리
    clean=[]
    for p in piv:
        if not clean or clean[-1][0]!=p[0]: clean.append(p)
        elif (p[0]=="H" and p[2]>clean[-1][2]) or (p[0]=="L" and p[2]<clean[-1][2]): clean[-1]=p

    swings=[]
    for a,b in zip(clean,clean[1:]):
        if a[0]=="L" and b[0]=="H" and b[2]>a[2]:
            gross=(b[2]/a[2]-1)*100
            net=gross-cost
            if SWING_MIN<=gross<=SWING_MAX:
                swings.append(dict(low=a[2],high=b[2],low_i=a[1],high_i=b[1],low_time=str(a[3]),high_time=str(b[3]),
                                   gross_pct=gross,net_pct=net,duration=max(1,b[1]-a[1])))
    widths=[s["gross_pct"] for s in swings]; nets=[s["net_pct"] for s in swings]
    durations=[s["duration"] for s in swings]
    rep=_median(widths); netrep=_median(nets); cyc=[]
    lows_p=[p for p in clean if p[0]=="L"]
    for a,b in zip(lows_p,lows_p[1:]): cyc.append(max(1,b[1]-a[1]))
    cycle=_median(cyc,_median(durations,0))
    consistency=0.0
    if len(widths)>=2 and rep>0:
        mad=_median([abs(x-rep) for x in widths])
        consistency=_clamp(1-mad/max(rep,0.01),0,1)

    provisional=dict(type=("H" if direction>=0 else "L"),price=extreme,index=ex_i,time=str(times[ex_i]),confirmed=False)
    out.update(
        online_reversal_threshold_percent=round(reversal,4), execution_cost_percent=round(cost,4),
        online_confirmed_pivots=clean[-16:], online_provisional_pivot=provisional,
        confirmed_swings=swings[-10:], confirmed_swing_count=len(swings),
        swing_width_samples=[round(x,4) for x in widths[-10:]],
        swing_up_width_percent=round(rep,4), net_swing_width_percent=round(netrep,4),
        swing_width_consistency=round(consistency,4), swing_cycle_duration_minutes=round(cycle,2),
        repeat_oscillation_count=len(swings), swing_cycle_valid=len(swings)>=MIN_SWINGS and SWING_MIN<=rep<=SWING_MAX,
        online_swing_valid=len(swings)>=MIN_SWINGS and netrep>0,
        online_swing_reason=("확정 Swing 3회 이상" if len(swings)>=MIN_SWINGS else f"확정 Swing {len(swings)}/3"),
    )
    return out

def _turnover(item,bars=None):
    c=_series(item,"chart_close_1m"); v=_series(item,"chart_volume_1m"); n=min(len(c),len(v))
    if n<=0:return 0.0
    if bars:n=min(n,bars)
    return sum(c[-i]*v[-i] for i in range(1,n+1))

def tier_liquidity(item,market,h):
    day=_turnover(item); hour=_turnover(item,60); elapsed=max(1,h["elapsed_minutes"])
    if _market_code(market)=="KR": daybase,hourbase,slen=30e9,3e9,390
    elif "정규" in h["session_label"]: daybase,hourbase,slen=50e6,5e6,390
    elif "프리" in h["session_label"]: daybase,hourbase,slen=3e6,.5e6,330
    else: daybase,hourbase,slen=2e6,.3e6,240
    dayreq=daybase*_clamp(elapsed/slen,.08,1); hourreq=hourbase*_clamp(min(len(_series(item,"chart_close_1m")),60)/60,.15,1)
    rvol=_num(item.get("rvol")); spread=_num(item.get("verified_spread_percent"),-1)
    spread_limit=.35 if "레버리지" in str(item.get("asset_type","")) else .25
    t1=day>=dayreq and hour>=hourreq; t2=rvol>=.8 if rvol>0 else False; known=spread>=0; t3=known and spread<=spread_limit
    score=(40 if t1 else min(40,40*day/max(dayreq,1)))+(35 if t2 else min(35,35*rvol/.8))+(25 if t3 else 0)
    return dict(liquidity_tier1_pass=t1,liquidity_tier2_pass=t2,liquidity_tier3_pass=t3,liquidity_tier3_known=known,
                liquidity_score=round(score,2),liquidity_day_value=day,liquidity_hour_value=hour,liquidity_spread_limit=spread_limit)

def _strategy_type(item):
    piv=item.get("online_confirmed_pivots",[]) or []
    lows=[p[2] for p in piv if p[0]=="L"][-4:]; highs=[p[2] for p in piv if p[0]=="H"][-4:]
    if len(lows)>=3 and len(highs)>=3:
        hl=sum(b>a for a,b in zip(lows,lows[1:])); hh=sum(b>a for a,b in zip(highs,highs[1:]))
        low_disp=(max(lows)-min(lows))/_median(lows)*100 if _median(lows)>0 else 99
        high_disp=(max(highs)-min(highs))/_median(highs)*100 if _median(highs)>0 else 99
        if hl>=2 and hh>=2:return "TREND_SWING"
        if low_disp<=1.2 and high_disp<=1.2:return "RANGE_SWING"
    return "NONE"

def _fatigue(item):
    widths=[_num(x) for x in item.get("swing_width_samples",[]) if _num(x)>0]; f=0; reasons=[]
    if len(widths)>=3 and widths[-3]>widths[-2]>widths[-1]: f+=25; reasons.append("최근 3개 Swing 폭 축소")
    rep=_num(item.get("swing_up_width_percent"))
    if widths and rep>0 and widths[-1]<rep*.7:f+=20; reasons.append("최근 폭 대표값 대비 30% 이상 축소")
    sell=_num(item.get("post_entry_sell_volume_share"),.5)
    if sell>=.62:f+=15; reasons.append("매도거래량 증가")
    down=_num(item.get("swing_down_duration_minutes")); cycle=_num(item.get("swing_cycle_duration_minutes"))
    if cycle>0 and down>cycle*.65:f+=15; reasons.append("회복 지연")
    return dict(pattern_fatigue=_clamp(f,0,100),pattern_fatigue_reasons=reasons)

def persistence_plan(item,market,now_ts=None):
    h=session_horizon(market,now_ts); c=_series(item,"chart_close_1m"); observed=min(len(c),360)
    cnt=int(_num(item.get("confirmed_swing_count"))); rep=_num(item.get("swing_up_width_percent")); cons=_num(item.get("swing_width_consistency"))
    liq=tier_liquidity(item,market,h); fat=_fatigue(item); st=_strategy_type(item)
    swing=min(100,cnt/3*70+cons*30) if cnt else 0
    structure=85 if st=="TREND_SWING" else 80 if st=="RANGE_SWING" else 30
    net=_num(item.get("net_swing_width_percent")); netscore=100 if net>=.35 else max(0,net/.35*100)
    score=_clamp(.30*swing+.20*structure+.20*liq["liquidity_score"]+.15*netscore+.15*(100-fat["pattern_fatigue"]),0,100)
    if observed>=300: mode,conf="OBSERVED_300",min(95,78+(observed-300)/4)
    elif observed>=180: mode,conf="PROJECTED_180",60+(observed-180)/7
    elif observed>=90: mode,conf="PROJECTED_90",45+(observed-90)/7
    elif observed>=30: mode,conf="EARLY_PROJECTED",30+(observed-30)/4
    else: mode,conf="EARLY_FORMING",10+observed*.6
    conf=_clamp(conf+min(8,cnt*1.5)+max(0,cons-.5)*10,10,95)
    cycle=_num(item.get("swing_cycle_duration_minutes")); mintrade=max(20,int(cycle*1.25)+5) if cycle else 30
    # 고정 45분 대신 cycle ETA 기반. 단 최소 15분 안전완충.
    newtime=h["remaining_minutes"]>=max(15,mintrade)
    grade="PERSISTENT_A" if score>=80 else "PERSISTENT_B" if score>=70 else "WATCH" if score>=60 else "UNSTABLE"
    return dict(**h,observed_minutes=observed,persistence_mode=mode,persistence_score=round(score,2),
                persistence_confidence=round(conf,1),evidence_confidence=round(conf,1),persistence_grade=grade,
                persistence_projected=observed<300,persistence_new_entry_time_ok=newtime,persistence_min_trade_time=mintrade,
                next_cycle_eta_minutes=round(cycle,1) if cycle else None,**liq,**fat)

def _risk_state(item):
    price=_num(item.get("price")); soft=_num(item.get("post_entry_soft_stop",item.get("soft_stop")))
    hard=_num(item.get("post_entry_hard_stop",item.get("hard_stop",item.get("stop_loss"))))
    given=str(item.get("post_entry_risk_state","FORMING"))
    if hard>0 and price>0 and price<=hard:return "HARD_EXIT"
    if given in {"REAL_BREAKDOWN","HARD_EXIT"}:return given
    if soft>0 and price>0 and price<soft:
        # LL/매도압/VWAP이탈 중 2개 이상이면 WARNING, 아니면 SHAKEOUT
        lows=_series(item,"chart_low_1m"); ll=len(lows)>=3 and lows[-1]<min(lows[-3:-1])
        sell=_num(item.get("post_entry_sell_volume_share"),.5)>=.65
        vwap=_num(item.get("vwap")); below=vwap>0 and price<vwap
        bad=sum((ll,sell,below))
        return "WARNING" if bad>=2 else "SHAKEOUT"
    return given if given not in {"FORMING",""} else "NORMAL_SWING"

def dynamic_recovery_window(item):
    d=_num(item.get("swing_down_duration_minutes")); c=_num(item.get("swing_cycle_duration_minutes"))
    base=d if d>0 else (c*.35 if c>0 else 6)
    return int(round(_clamp(base*.35,1,4)))

def _plan_levels(item,market):
    entry=_num(item.get("repeat_entry",item.get("repeat_scalp_buy_level",item.get("structural_support"))))
    t1=_num(item.get("repeat_target1",item.get("structural_target1",item.get("structural_target"))))
    t2=_num(item.get("repeat_target2",item.get("structural_target2")))
    soft=_num(item.get("post_entry_soft_stop",item.get("soft_stop",item.get("stop_loss"))))
    hard=_num(item.get("post_entry_hard_stop",item.get("hard_stop",item.get("stop_loss"))))
    return tuple(_round_tick(x,market) if x>0 else None for x in (entry,t1,t2,soft,hard))

def _plan_id(item,market,levels):
    ticker=str(item.get("ticker") or item.get("code") or "?")
    piv=item.get("online_confirmed_pivots",[]) or []
    anchor=str(piv[-1][3]) if piv else "forming"
    raw=f"{VERSION}|{market}|{ticker}|{anchor}|{levels}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def data_freshness_plan(item,market,now_ts=None):
    now_ts=now_ts or time.time(); age=_bar_age_seconds(item,market,now_ts)
    q=_num(item.get("quote_age_seconds"),-1)
    # quote_age를 공급하지 않는 기존 KIS 엔진은 현재가가 있으면 '미계측'으로 분리한다.
    q_known=q>=0; q_ok=(q<=15) if q_known else _num(item.get("price"))>0
    bar_ok=age is not None and age<=180
    return dict(data_freshness_pass=bool(bar_ok and q_ok),data_bar_age_seconds=age,
                data_quote_age_seconds=(q if q_known else None),quote_age_measured=q_known)

def evaluate_strategy(item,market,now_ts=None,cycle_state=None,calibrated_probability=None,calibration_samples=0):
    if not isinstance(item,dict):return item
    now_ts=now_ts or time.time()
    out=online_swing_plan(dict(item),market)
    out.update(persistence_plan(out,market,now_ts)); out.update(data_freshness_plan(out,market,now_ts))
    st=_strategy_type(out); risk=_risk_state(out); out["post_entry_risk_state"]=risk
    out["strategy_type_v51"]=st; out["strategy_type"]=st; out["recovery_window_minutes"]=dynamic_recovery_window(out)
    state=dict(cycle_state or {})
    cooldown=now_ts<_num(state.get("cooldown_until")); hardkill=bool(state.get("hard_kill"))
    out.update(cycle_cooldown_active=cooldown,cycle_cooldown_until=_num(state.get("cooldown_until")),
               cycle_hard_kill=hardkill,cycle_breakdown_count=int(_num(state.get("breakdown_count"))),
               cycle_hard_exit_count=int(_num(state.get("hard_exit_count"))))
    cnt=int(_num(out.get("confirmed_swing_count"))); width=_num(out.get("swing_up_width_percent")); net=_num(out.get("net_swing_width_percent"))
    # 기존 UI의 BUY_ZONE + 현재 위치를 함께 인정
    rep_entry=_num(out.get("repeat_entry",out.get("repeat_scalp_buy_level",out.get("structural_support"))))
    price=_num(out.get("price")); repwidth=max(width,.5)
    near_entry=rep_entry>0 and price>0 and abs(price/rep_entry-1)*100<=min(.35,repwidth*.30)
    buy_zone=str(out.get("repeat_state",out.get("repeat_scalp_state",""))) in {"BUY_ZONE","BUY_PULLBACK"} or near_entry
    rr=_num(out.get("execution_effective_rr",out.get("risk_reward"))); safety=bool(out.get("execution_safety_passed"))
    spread_known=bool(out.get("liquidity_tier3_known"))
    observed=int(_num(out.get("observed_minutes"))); minconf=999 if observed<30 else 42 if observed<90 else 50 if observed<180 else 55
    probgate=(calibrated_probability>=60) if calibrated_probability is not None and calibration_samples>=30 else True
    checks={
      "세션 거래가능":bool(out.get("tradable")),"남은시간 충분":bool(out.get("persistence_new_entry_time_ok")),
      "데이터 최신":bool(out.get("data_freshness_pass")),"Tier1 유동성":bool(out.get("liquidity_tier1_pass")),
      "Tier2 순간유동성":bool(out.get("liquidity_tier2_pass")),"스프레드 확인":spread_known,
      "Tier3 스프레드":bool(out.get("liquidity_tier3_pass")),"확정 Swing 3회+":cnt>=3,
      "대표폭 0.5~5%":SWING_MIN<=width<=SWING_MAX,"비용후 Swing 양수":net>0,
      "Persistence 70+":_num(out.get("persistence_score"))>=70,"Evidence 신뢰":_num(out.get("evidence_confidence"))>=minconf,
      "Trend/Range 확정":st in {"TREND_SWING","RANGE_SWING"},"현재 매수구간":buy_zone,
      "실행 안전":safety,"RR 1.1+":rr>=1.10,"진짜 붕괴 아님":risk not in {"REAL_BREAKDOWN","HARD_EXIT","WARNING"},
      "Cooldown 아님":not cooldown,"Hard Kill 아님":not hardkill,"확률게이트":probgate,
    }
    final=all(checks.values())
    raw=.50*_num(out.get("persistence_score"))+.15*_num(out.get("liquidity_score"))+.15*(100-_num(out.get("pattern_fatigue")))+.10*(100 if safety else 0)+.10*_clamp(_num(out.get("target1_before_stop_probability")),0,100)
    if risk=="WARNING":raw-=15
    if risk in {"REAL_BREAKDOWN","HARD_EXIT"}:raw=min(raw,20)
    levels=_plan_levels(out,market); pid=_plan_id(out,market,levels)
    out.update(strategy_engine_version=VERSION,strategy_version=VERSION,schema_version=SCHEMA_VERSION,plan_version=PLAN_VERSION,
               proposed_plan_id=pid,proposed_entry=levels[0],proposed_target1=levels[1],proposed_target2=levels[2],
               proposed_soft_stop=levels[3],proposed_hard_stop=levels[4],
               final_buy=final,final_buy_checks=checks,final_buy_reasons=[k for k,v in checks.items() if not v],
               model_raw_score=round(_clamp(raw,0,100),2),calibrated_probability=calibrated_probability,
               calibration_samples=int(calibration_samples),calibration_state=("보정완료" if calibrated_probability is not None and calibration_samples>=30 else "보정전"),
               probability_gate_pass=probgate)
    return out

def update_cycle_state(item,state=None,now_ts=None):
    now_ts=now_ts or time.time(); s=dict(state or {})
    for k,v in {"cooldown_until":0.0,"breakdown_count":0,"hard_exit_count":0,"hard_kill":False,"last_risk_event":"",
                "position_state":"PRE_ENTRY","active_plan":None}.items(): s.setdefault(k,v)
    out=evaluate_strategy(item,"KR" if str(item.get("exchange","")).upper()=="KR" else "US",now_ts,s,
                          item.get("calibrated_probability"),int(_num(item.get("calibration_samples"))))
    risk=str(out.get("post_entry_risk_state")); times=out.get("chart_time_1m",[]) or []; bar=str(times[-1]) if times else str(int(now_ts//60))
    eid=f"{risk}:{bar}"
    if risk in {"REAL_BREAKDOWN","HARD_EXIT"} and eid!=s.get("last_risk_event"):
        if risk=="REAL_BREAKDOWN":s["breakdown_count"]+=1; s["cooldown_until"]=max(_num(s["cooldown_until"]),now_ts+600)
        else:s["hard_exit_count"]+=1; s["cooldown_until"]=max(_num(s["cooldown_until"]),now_ts+900)
        s["last_risk_event"]=eid
    if s["breakdown_count"]>=2 or s["hard_exit_count"]>=2:s["hard_kill"]=True
    # FINAL BUY가 뜬 순간 계획을 잠근다. 가격이 진입가를 터치하면 IN_POSITION.
    if out.get("final_buy") and not s.get("active_plan"):
        s["active_plan"]={k:out.get(k) for k in ("proposed_plan_id","proposed_entry","proposed_target1","proposed_target2","proposed_soft_stop","proposed_hard_stop")}
        s["position_state"]="PRE_ENTRY"
    p=s.get("active_plan") or {}; entry=_num(p.get("proposed_entry")); price=_num(out.get("price"))
    if s["position_state"]=="PRE_ENTRY" and entry>0 and price>0 and price<=entry:s["position_state"]="IN_POSITION"
    if s["position_state"]=="IN_POSITION" and risk in {"REAL_BREAKDOWN","HARD_EXIT"}:s["position_state"]="EXITED"
    if p:
        out.update(plan_id=p.get("proposed_plan_id"),plan_entry=p.get("proposed_entry"),plan_target1=p.get("proposed_target1"),
                   plan_target2=p.get("proposed_target2"),plan_soft_stop=p.get("proposed_soft_stop"),plan_hard_stop=p.get("proposed_hard_stop"))
    out["position_state"]=s["position_state"]
    out=evaluate_strategy(out,"KR" if str(out.get("exchange","")).upper()=="KR" else "US",now_ts,s,
                          out.get("calibrated_probability"),int(_num(out.get("calibration_samples"))))
    return s,out

def calibrated_from_db(db_path,raw_score,strategy_type,min_samples=30,bucket_width=10,asof_ts=None):
    """Walk-forward: asof_ts가 주어지면 그 시각 이전 완료표본만 사용."""
    path=Path(db_path)
    if not path.exists():return None,0
    lo=int(raw_score//bucket_width)*bucket_width; hi=lo+bucket_width; vals=[]
    try:
        con=sqlite3.connect(path,timeout=3)
        sql="SELECT issued,target1_before_stop,detail_json FROM signals WHERE result_done=1 AND target1_before_stop IS NOT NULL"
        params=[]
        if asof_ts is not None: sql+=" AND issued<?"; params.append(int(asof_ts))
        sql+=" ORDER BY issued DESC LIMIT 5000"
        rows=con.execute(sql,params).fetchall(); con.close()
        for issued,res,detail in rows:
            try:d=json.loads(detail or "{}")
            except Exception:continue
            rs=_num(d.get("model_raw_score"),-1); st=str(d.get("strategy_type_v51",d.get("strategy_type","")))
            if lo<=rs<hi and (not strategy_type or st==strategy_type):vals.append(int(bool(res)))
        if len(vals)<min_samples:return None,len(vals)
        return round(sum(vals)/len(vals)*100,1),len(vals)
    except Exception:return None,0
