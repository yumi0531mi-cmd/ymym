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
    """한국투자증권 미국주식 KST 거래시간을 기준으로 세션을 판정한다.

    주간거래 + 프리 + 정규 + 애프터를 모두 포함한다.
    애프터 07:00~09:00 구간은 연장 신청이 필요한 계좌가 있을 수 있으므로
    세션명에 '연장'을 명시한다.
    """
    now_kst=(now_utc or datetime.now(timezone.utc)).astimezone(KST)
    now_et=now_kst.astimezone(ET)
    dst=bool(now_et.dst() and now_et.dst().total_seconds()!=0)
    minute=now_kst.hour*60+now_kst.minute
    wd=now_kst.weekday()
    prev_wd=(now_kst-timedelta(days=1)).weekday()
    label=f"{now_kst.strftime('%H:%M:%S')} KST / {now_et.strftime('%H:%M:%S')} ET"

    if dst:
        day=(10*60,17*60)
        pre=(17*60,22*60+30)
        regular_late=(22*60+30,24*60)
        regular_early=(0,5*60)
        after=(5*60,7*60)
        after_ext=(7*60,9*60)
    else:
        day=(10*60,18*60)
        pre=(18*60,23*60+30)
        regular_late=(23*60+30,24*60)
        regular_early=(0,6*60)
        after=(6*60,7*60)
        after_ext=(7*60,9*60)

    if wd<5 and day[0]<=minute<day[1]:
        return SessionState(True,"미국 주간거래",label)
    if wd<5 and pre[0]<=minute<pre[1]:
        return SessionState(True,"미국 프리마켓",label)
    if wd<5 and regular_late[0]<=minute<regular_late[1]:
        return SessionState(True,"미국 정규장",label)
    if prev_wd<5 and regular_early[0]<=minute<regular_early[1]:
        return SessionState(True,"미국 정규장",label)
    if prev_wd<5 and after[0]<=minute<after[1]:
        return SessionState(True,"미국 애프터마켓",label)
    if prev_wd<5 and after_ext[0]<=minute<after_ext[1]:
        return SessionState(True,"미국 애프터마켓 연장",label)
    return SessionState(False,"미국 장외시간/휴장",label)


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


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))





def resolve_vwap_series(item: dict) -> dict:
    """분봉별 VWAP 배열을 보장한다. 엔진 배열이 없으면 OHLCV로 직접 계산한다."""
    if not isinstance(item,dict):
        return item
    closes=[_num(x) for x in (item.get("chart_close_1m",[]) or [])]
    highs=[_num(x) for x in (item.get("chart_high_1m",[]) or [])]
    lows=[_num(x) for x in (item.get("chart_low_1m",[]) or [])]
    vols=[max(0.0,_num(x)) for x in (item.get("chart_volume_1m",[]) or [])]
    n=min(len(closes),len(highs),len(lows))
    if n<=0:
        item["chart_vwap_1m"]=[]
        return item
    raw=[_num(x) for x in (item.get("chart_vwap_1m",[]) or [])]
    valid=len(raw)>=n and sum(v>0 for v in raw[-n:])>=max(10,int(n*0.8))
    if valid:
        series=raw[-n:]
        item["_vwap_series_source"]="engine"
    else:
        vv=vols[-n:] if len(vols)>=n else [0.0]*(n-len(vols))+vols
        series=[]; cpv=cv=0.0
        for h,l,c,v in zip(highs[-n:],lows[-n:],closes[-n:],vv):
            typical=(h+l+c)/3.0
            cpv+=typical*v; cv+=v
            series.append(cpv/cv if cv>0 else typical)
        item["_vwap_series_source"]="computed"
    item["chart_vwap_1m"]=series
    if _num(item.get("vwap"))<=0 and series:
        item["vwap"]=series[-1]
    return item



def apply_repeat_scalp_overlay(item, market_code):
    """실제 1분봉만으로 지지·저항·ATR 반복단타 가격을 계산한다.

    중요한 원칙:
    - 미래예측(forecast_*)은 여기서 사용하지 않는다.
    - 목표가를 만든 뒤 미래예측이 그 목표의 도달 가능성을 평가한다.
      즉 '목표가가 예측을 만들고 예측이 다시 목표가를 만드는' 순환 의존을 제거한다.
    """
    if not isinstance(item, dict):
        return item
    item = dict(item)
    price = _num(item.get("price"))
    frame = _intraday_ohlcv(item)
    if price <= 0 or len(frame) < 20:
        item.update(repeat_chart_valid=False, repeat_chart_reason="현재가 또는 연속 1분봉 20개 미만", repeat_candidate=False)
        return item

    highs=frame["high"].tolist(); lows=frame["low"].tolist()
    closes=frame["close"].tolist(); volumes=frame["volume"].tolist()
    atr14,median_range=_atr_and_range(frame)
    if atr14<=0: atr14=median_range
    if median_range<=0: median_range=atr14

    vwap=_num(item.get("vwap"))
    ema9=_num(item.get("ema9")); ema20=_num(item.get("ema20"))
    rsi=_num(item.get("rsi"),50.0)
    rvol=_num(item.get("rvol"))

    bid=_num(item.get("best_bid")); ask=_num(item.get("best_ask"))
    spread=((ask-bid)/((ask+bid)/2)*100) if ask>=bid>0 else None
    spread_limit=0.35 if "레버리지" in str(item.get("asset_type","")) else 0.25

    ret5=(closes[-1]/closes[-6]-1)*100 if len(closes)>=6 and closes[-6]>0 else 0.0
    ret15=(closes[-1]/closes[-16]-1)*100 if len(closes)>=16 and closes[-16]>0 else 0.0
    ret30=(closes[-1]/closes[-31]-1)*100 if len(closes)>=31 and closes[-31]>0 else 0.0
    recent_hi=max(highs[-6:]); previous_hi=max(highs[-12:-6]) if len(highs)>=12 else recent_hi
    recent_lo=min(lows[-6:]); previous_lo=min(lows[-12:-6]) if len(lows)>=12 else recent_lo

    up_volume=down_volume=0.0
    for i in range(max(1,len(closes)-20),len(closes)):
        vol=volumes[i] if i<len(volumes) else 0.0
        if closes[i]>closes[i-1]: up_volume+=vol
        elif closes[i]<closes[i-1]: down_volume+=vol
    volume_ratio=up_volume/down_volume if down_volume>0 else (2.0 if up_volume>0 else 0.0)

    trend_checks={
        "VWAP 위":price>vwap>0,
        "EMA 정배열":price>=ema9>=ema20>0,
        "15분 약세 아님":ret15>=-0.10,
        "30분 큰 약세 아님":ret30>=-0.25,
        "고점 유지/상승":recent_hi>=previous_hi*0.998,
        "저점 유지/상승":recent_lo>=previous_lo*0.998,
        "상승봉 거래량 우세":volume_ratio>=1.00,
        "RSI 과열 아님":rsi<(82 if market_code=="US" else 78),
        "RVOL 확보":rvol>=0.65,
    }
    trend_score=sum(bool(v) for v in trend_checks.values())

    swing_lows=_swing_levels(lows,"low")
    supports=[(x,"1분봉 스윙 저점") for x in swing_lows if 0<x<price]
    recent_low=min(lows[-20:])
    if 0<recent_low<price: supports.append((recent_low,"최근 20분 저점"))
    if 0<vwap<price: supports.append((vwap,"VWAP"))
    if 0<ema9<price: supports.append((ema9,"EMA9"))
    if 0<ema20<price: supports.append((ema20,"EMA20"))
    if not supports:
        item.update(repeat_chart_valid=False,repeat_chart_reason="현재가 아래 실제 지지선 미확인",
                    repeat_candidate=False,repeat_trend_score=trend_score,repeat_trend_checks=trend_checks)
        return item

    support,support_basis=max(supports,key=lambda x:x[0])
    swing_highs=_swing_levels(highs,"high")
    minimum=support*1.005
    resistances=[x for x in swing_highs if x>price and x>=minimum]
    look=min(60,len(highs))
    box_high=max(highs[-look:]); box_low=min(lows[-look:])
    prior_high=max(highs[-look:-1]) if look>=2 else box_high
    for level in (box_high,prior_high):
        if level>price and level>=minimum:
            resistances.append(level)
    resistances=_dedupe_levels(resistances)

    target1=target2=0.0
    b1=b2=""
    if resistances:
        target1=resistances[0]; b1="실제 1분봉 첫 저항"
        higher=[x for x in resistances[1:] if x>target1*1.0005]
        if higher:
            target2=higher[0]; b2="실제 1분봉 다음 저항"
    elif trend_score>=7:
        # 실제 저항이 없는 신고가 구간만 투영 허용
        projection=max(atr14*1.20,median_range*1.50)
        target1=price+projection
        b1="신고가 구간 ATR/봉폭 투영"
        target2=target1+max(atr14*1.10,projection*0.75)
        b2="신고가 구간 2차 ATR 투영"

    if not (0<support<price<target1):
        item.update(repeat_chart_valid=False,repeat_chart_reason="지지 < 현재가 < 1차저항 구조 불충족",
                    repeat_candidate=False,repeat_support=support,repeat_trend_score=trend_score,
                    repeat_trend_checks=trend_checks)
        return item

    stop_buffer=max(atr14*0.35,median_range*0.45,support*0.0015)
    stop_buffer=min(stop_buffer,support*0.006)
    stop=max(0.0,support-stop_buffer)
    entry=support
    width=(target1/entry-1)*100
    risk=entry-stop; reward=target1-entry
    rr=reward/risk if risk>0 else 0.0
    t1cur=(target1/price-1)*100
    t2cur=(target2/price-1)*100 if target2>price else 0.0
    extra=(target2/target1-1)*100 if target2>target1>0 else 0.0

    near=max(median_range*0.75,atr14*0.35)
    if price<=support:
        state,label="BREAKDOWN","🔴 지지 이탈"
    elif price>=target1-near:
        state,label="TAKE_PROFIT","🟠 1차 저항 근접"
    elif price<=support+near and trend_score>=6:
        state,label="BUY_ZONE","🟢 반복 매수구간 근접"
    elif trend_score>=6:
        state,label="WAIT_PULLBACK","🟡 지지 눌림 대기"
    else:
        state,label="WAIT_TREND","⚪ 추세 재확인"

    preferred=0.50<=width<=1.50
    quality={
        "분봉 실데이터":not bool(item.get("intraday_fallback")),
        "VWAP 확인":vwap>0,
        "EMA9·20 확인":ema9>0 and ema20>0,
        "스프레드 허용":spread is None or spread<=spread_limit,
    }
    quality_pass=all(quality.values())
    candidate=bool(preferred and trend_score>=6 and state not in {"BREAKDOWN","TAKE_PROFIT"} and rr>=1.20 and quality_pass)

    # 2차 지속성은 미래예측이 아니라 현재 실측 구조만 사용
    cont_checks={
        "실제/투영 2차 존재":target2>target1,
        "VWAP 위":price>vwap>0,
        "EMA 정배열":ema9>=ema20>0,
        "15분 약세 아님":ret15>=-0.10,
        "30분 약세 아님":ret30>=-0.20,
        "상승 거래량 우세":volume_ratio>=1.0,
        "RVOL 확보":rvol>=0.8,
    }
    cont_score=sum(bool(v) for v in cont_checks.values())
    if target2<=target1: cont_state,cont_label="NONE","⚪ 2차 저항 미확인"
    elif cont_score>=6: cont_state,cont_label="HIGH","🟢 2차 확장 구조 양호"
    elif cont_score>=4: cont_state,cont_label="MID","🟡 2차는 1차 돌파 후 확인"
    else: cont_state,cont_label="LOW","🔴 2차 확장 근거 약함"

    item.update(
        repeat_chart_valid=True,repeat_chart_reason="실제 1분봉 지지·저항/ATR 계산 완료",
        repeat_candidate=candidate,repeat_entry=entry,repeat_support=support,repeat_stop=stop,
        repeat_target1=target1,repeat_target2=target2,repeat_width_percent=width,
        repeat_target1_current_upside=t1cur,repeat_target2_current_upside=t2cur,
        repeat_extra_after_target1=extra,repeat_risk_reward=rr,repeat_state=state,repeat_label=label,
        repeat_trend_score=trend_score,repeat_trend_checks=trend_checks,
        repeat_support_basis=support_basis,repeat_target1_basis=b1,repeat_target2_basis=b2,
        repeat_atr14=atr14,repeat_median_range=median_range,repeat_volume_ratio=volume_ratio,
        repeat_continuation_state=cont_state,repeat_continuation_label=cont_label,
        repeat_continuation_score=cont_score,repeat_continuation_checks=cont_checks,
        repeat_preferred_range=preferred,repeat_quality_pass=quality_pass,
        repeat_quality_checks=quality,repeat_spread_percent=spread,repeat_spread_limit=spread_limit,
        repeat_chart_box_low=box_low,repeat_chart_box_high=box_high,
    )
    return item


def _aggregate_ohlcv(item:dict,minutes:int,market:str):
    times=item.get("chart_time_1m",[]) or []; opens=item.get("chart_open_1m",[]) or []; highs=item.get("chart_high_1m",[]) or []; lows=item.get("chart_low_1m",[]) or []; closes=item.get("chart_close_1m",[]) or []; volumes=item.get("chart_volume_1m",[]) or []
    n=min(len(times),len(opens),len(highs),len(lows),len(closes))
    if n<minutes: return []
    if len(volumes)<n: volumes=list(volumes)+[0]*(n-len(volumes))
    df=pd.DataFrame({"time":pd.to_datetime(times[:n],errors="coerce"),"open":[float(x or 0) for x in opens[:n]],"high":[float(x or 0) for x in highs[:n]],"low":[float(x or 0) for x in lows[:n]],"close":[float(x or 0) for x in closes[:n]],"volume":[float(x or 0) for x in volumes[:n]]}).dropna(subset=["time"])
    df=df[(df.open>0)&(df.high>0)&(df.low>0)&(df.close>0)].sort_values("time").set_index("time")
    if df.empty: return []
    offset="30min" if market=="미국" and minutes in (60,) else "0min"
    grouped=df.resample(f"{minutes}min",origin="start_day",offset=offset,closed="left",label="left")
    bars=grouped.agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}); bars["count"]=grouped["close"].count(); bars=bars.dropna()
    if bars.empty: return []
    bars=bars[bars.index+pd.Timedelta(minutes=minutes)<=df.index[-1]+pd.Timedelta(minutes=1)]
    bars=bars[bars["count"]>=max(2,math.ceil(minutes*0.8))]
    return [{"time":idx,"open":float(r.open),"high":float(r.high),"low":float(r.low),"close":float(r.close),"volume":float(r.volume)} for idx,r in bars.iterrows()]

def hourly_structure_plan(item:dict, market:str):
    """최근 완성 60분봉 3~5개의 구조를 큰 추세 필터로 사용한다.

    추가 API 호출 없이 이미 받은 1분봉을 60분봉으로 묶는다.
    HH/HL, LH/LL, 60분 EMA 기울기, 종가 지속성, 거래량을 함께 본다.
    """
    bars=_aggregate_ohlcv(item,60,market)
    bars=bars[-5:]
    if len(bars)<2:
        item.update(
            hourly_structure_state="FORMING",
            hourly_structure_label="⚪ 60분 구조 형성 중",
            hourly_structure_score=0,
            hourly_structure_bear_score=0,
            hourly_hh_count=0,
            hourly_hl_count=0,
            hourly_lh_count=0,
            hourly_ll_count=0,
            hourly_bars=len(bars),
        )
        return item

    closes=[float(b["close"]) for b in bars]
    highs=[float(b["high"]) for b in bars]
    lows=[float(b["low"]) for b in bars]
    vols=[float(b.get("volume",0) or 0) for b in bars]

    hh=sum(1 for i in range(1,len(bars)) if highs[i]>highs[i-1])
    hl=sum(1 for i in range(1,len(bars)) if lows[i]>=lows[i-1])
    lh=sum(1 for i in range(1,len(bars)) if highs[i]<highs[i-1])
    ll=sum(1 for i in range(1,len(bars)) if lows[i]<lows[i-1])

    ema=pd.Series(closes,dtype=float).ewm(span=min(3,len(closes)),adjust=False).mean()
    ema_up=len(ema)>=2 and float(ema.iloc[-1])>float(ema.iloc[-2])
    ema_down=len(ema)>=2 and float(ema.iloc[-1])<float(ema.iloc[-2])

    close_up=sum(1 for i in range(1,len(closes)) if closes[i]>closes[i-1])
    close_down=sum(1 for i in range(1,len(closes)) if closes[i]<closes[i-1])

    first_close=closes[0]
    structure_return=(closes[-1]/first_close-1)*100 if first_close>0 else 0.0

    vol_med=float(pd.Series(vols[:-1]).median()) if len(vols)>=2 else 0.0
    last_vol_ok=vol_med<=0 or vols[-1]>=vol_med*0.65
    last_vol_strong=vol_med>0 and vols[-1]>=vol_med*1.05

    pairs=max(1,len(bars)-1)
    bull_checks={
        "60분 고점 상승": hh>=max(1,pairs-1),
        "60분 저점 상승": hl>=max(1,pairs-1),
        "60분 EMA 상승": ema_up,
        "60분 종가 상승 우세": close_up>=math.ceil(pairs*0.60),
        "최근 60분 거래량 유지": last_vol_ok,
        "60분 구조 수익률 양수": structure_return>0,
    }
    bear_checks={
        "60분 고점 하락": lh>=max(1,pairs-1),
        "60분 저점 하락": ll>=max(1,pairs-1),
        "60분 EMA 하락": ema_down,
        "60분 종가 하락 우세": close_down>=math.ceil(pairs*0.60),
        "60분 구조 수익률 음수": structure_return<0,
        "최근 하락 거래량 강함": last_vol_strong and closes[-1]<closes[-2],
    }
    bull_score=sum(bool(v) for v in bull_checks.values())
    bear_score=sum(bool(v) for v in bear_checks.values())

    # 2개뿐일 때는 확정 대신 방향만 표시. 3개부터 구조 판정 강화.
    enough=len(bars)>=3
    if enough and bull_score>=5 and bear_score<=2:
        state,label="STRONG_BULL","🟢 60분봉 우상향 지속"
    elif enough and bull_score>=4 and bear_score<=3:
        state,label="BULL","🟢 60분봉 상승구조"
    elif enough and bear_score>=5 and bull_score<=2:
        state,label="STRONG_BEAR","🔴 60분봉 하락구조"
    elif enough and bear_score>=4:
        state,label="BEAR","🟠 60분봉 약세구조"
    else:
        state,label="MIXED","⚪ 60분봉 혼조"

    item.update(
        hourly_structure_state=state,
        hourly_structure_label=label,
        hourly_structure_score=bull_score,
        hourly_structure_bear_score=bear_score,
        hourly_structure_checks=bull_checks,
        hourly_structure_bear_checks=bear_checks,
        hourly_hh_count=hh,
        hourly_hl_count=hl,
        hourly_lh_count=lh,
        hourly_ll_count=ll,
        hourly_close_up_count=close_up,
        hourly_close_down_count=close_down,
        hourly_structure_return=structure_return,
        hourly_bars=len(bars),
        hourly_last_volume_ratio=(vols[-1]/vol_med if vol_med>0 else 0.0),
    )
    return item

def intraday_regime_plan(item:dict, market:str):
    """당일 세션 전체 흐름을 우선하고, 15/30/60분은 보조로 사용한다."""
    price=float(item.get("price",0) or 0)
    closes=[float(x or 0) for x in (item.get("chart_close_1m",[]) or [])]
    highs=[float(x or 0) for x in (item.get("chart_high_1m",[]) or [])]
    lows=[float(x or 0) for x in (item.get("chart_low_1m",[]) or [])]
    vols=[float(x or 0) for x in (item.get("chart_volume_1m",[]) or [])]
    vwaps=[float(x or 0) for x in (item.get("chart_vwap_1m",[]) or [])]
    times=list(item.get("chart_time_1m",[]) or [])
    n=min(len(closes),len(highs),len(lows))
    if n<30 or price<=0:
        item.update(intraday_regime_state="UNKNOWN",
                    intraday_regime_label="⚪ 큰 추세 자료 형성 중",
                    intraday_regime_reason="최소 30분 실데이터 필요",
                    intraday_trade_type="대기",
                    intraday_short_pullback=False,
                    intraday_downtrend_confirmed=False)
        return item

    closes=closes[-n:]; highs=highs[-n:]; lows=lows[-n:]
    vols=(vols[-n:] if len(vols)>=n else [0.0]*(n-len(vols))+vols)
    vwaps=(vwaps[-n:] if len(vwaps)>=n else [0.0]*(n-len(vwaps))+vwaps)
    times=(times[-n:] if len(times)>=n else list(range(n)))

    # 마지막 연속 세션만 사용
    try:
        t=pd.to_datetime(pd.Series(times),errors="coerce")
        if t.notna().sum()>=max(20,n//2):
            gaps=t.diff().dt.total_seconds().fillna(0)
            cuts=[i for i,x in enumerate(gaps.tolist()) if x>45*60]
            if cuts:
                cut=cuts[-1]
                closes=closes[cut:]; highs=highs[cut:]; lows=lows[cut:]; vols=vols[cut:]; vwaps=vwaps[cut:]; times=times[cut:]
    except Exception:
        pass

    n=len(closes)
    if n<30:
        item.update(intraday_regime_state="UNKNOWN",
                    intraday_regime_label="⚪ 큰 추세 자료 형성 중",
                    intraday_regime_reason="현재 세션 연속 분봉 30개 미만",
                    intraday_trade_type="대기",
                    intraday_short_pullback=False,
                    intraday_downtrend_confirmed=False)
        return item

    s=pd.Series(closes,dtype=float)
    ema20=s.ewm(span=20,adjust=False).mean()
    ema50=s.ewm(span=min(50,n),adjust=False).mean()
    e20=float(ema20.iloc[-1]); e50=float(ema50.iloc[-1])
    e20_prev=float(ema20.iloc[max(0,n-11)])
    e50_prev=float(ema50.iloc[max(0,n-16)])
    e20_slope=(e20/e20_prev-1)*100 if e20_prev>0 else 0.0
    e50_slope=(e50/e50_prev-1)*100 if e50_prev>0 else 0.0

    def ret(m):
        return (closes[-1]/closes[-1-m]-1)*100 if n>m and closes[-1-m]>0 else 0.0
    r5,r15,r30=ret(5),ret(15),ret(30)
    r60=ret(60) if n>60 else ret(min(45,n-1))
    session_ret=(closes[-1]/closes[0]-1)*100 if closes[0]>0 else 0.0

    # 각 시점의 종가 vs 각 시점의 VWAP
    pairs=[(c,v) for c,v in zip(closes,vwaps) if v>0]
    vwap_hold=(sum(c>=v for c,v in pairs)/len(pairs)) if pairs else 0.0

    def block_structure(span=15):
        if n<span*3: return False,False,False,False
        h1=max(highs[-span*3:-span*2]); h2=max(highs[-span*2:-span]); h3=max(highs[-span:])
        l1=min(lows[-span*3:-span*2]); l2=min(lows[-span*2:-span]); l3=min(lows[-span:])
        return h3>=h2>=h1, l3>=l2>=l1, h3<h2<=h1, l3<l2<=l1
    hh,hl,lh,ll=block_structure(15)

    session_high=max(highs); session_low=min(lows)
    dd=(price/session_high-1)*100 if session_high>0 else 0.0

    # 눌림 회복 횟수: EMA20 또는 VWAP 부근 눌림 뒤 5분 내 회복
    reclaim_count=0
    for i in range(5,n-5):
        v=vwaps[i] if i<len(vwaps) else 0
        basis=max(0.0, min(x for x in [v,float(ema20.iloc[i])] if x>0)) if (v>0 or float(ema20.iloc[i])>0) else 0
        if basis>0 and lows[i]<=basis*1.002:
            future=max(closes[i+1:min(n,i+6)])
            if future>=basis*1.004:
                reclaim_count+=1

    upvol=downvol=0.0
    for i in range(1,n):
        if closes[i]>closes[i-1]: upvol+=vols[i]
        elif closes[i]<closes[i-1]: downvol+=vols[i]
    up_down_ratio=upvol/downvol if downvol>0 else (2.0 if upvol>0 else 0.0)

    current_vwap=next((v for v in reversed(vwaps) if v>0),0.0)
    below_vwap_15=(current_vwap>0 and n>=15 and sum(1 for c,v in zip(closes[-15:],vwaps[-15:]) if v>0 and c<v)>=12)
    below_ema20_15=(n>=15 and sum(1 for i,c in enumerate(closes[-15:],start=n-15) if c<float(ema20.iloc[i]))>=12)

    hourly_state=str(item.get("hourly_structure_state","FORMING"))
    hourly_bull=int(item.get("hourly_structure_score",0) or 0)
    hourly_bear=int(item.get("hourly_structure_bear_score",0) or 0)

    up_checks={
        "세션 상승":session_ret>0.5,
        "30분 상승":r30>0,
        "60분 상승":r60>0,
        "60분봉 다중 상승구조":hourly_state in {"STRONG_BULL","BULL"},
        "EMA20 상승":e20_slope>0,
        "EMA50 상승":e50_slope>=0,
        "현재가 EMA20 위":price>=e20,
        "EMA20>=EMA50":e20>=e50,
        "세션 VWAP 위 체류":vwap_hold>=0.60,
        "Higher High":hh,
        "Higher Low":hl,
        "눌림 회복 2회+":reclaim_count>=2,
        "상승봉 거래량 우세":up_down_ratio>=1.05,
    }
    up_score=sum(bool(v) for v in up_checks.values())

    down_checks={
        "60분봉 다중 하락구조":hourly_state in {"STRONG_BEAR","BEAR"},
        "15분 VWAP 지속 이탈":below_vwap_15,
        "15분 EMA20 지속 이탈":below_ema20_15,
        "EMA20 하락":e20_slope<0,
        "EMA20<EMA50":e20<e50,
        "30분 하락":r30<0,
        "Lower High":lh,
        "Lower Low":ll,
        "하락 거래량 우세":up_down_ratio<0.85,
        "세션 고점 대비 -1%+":dd<=-1.0,
    }
    down_score=sum(bool(v) for v in down_checks.values())

    short_pullback=(up_score>=8 and r30>=0 and hourly_state not in {"STRONG_BEAR","BEAR"} and (r5<0 or r15<0) and down_score<7)
    if down_score>=8 and r30<0 and hourly_state in {"STRONG_BEAR","BEAR"} and below_vwap_15 and below_ema20_15:
        state,label,reason="DOWNTREND","🔴 하락추세 지속",f"세션 구조 훼손 {down_score}/10"
    elif down_score>=7 and r30<0 and lh and ll:
        state,label,reason="DOWNTREND_REVERSAL","🔴 실제 하락추세 전환",f"LH·LL + VWAP/EMA 이탈 {down_score}/10"
    elif short_pullback:
        state,label,reason="UPTREND_PULLBACK","🟢 상승추세 중 정상 눌림",f"큰 상승구조 {up_score}/13 유지 · {item.get('hourly_structure_label','60분 형성 중')}"
    elif up_score>=10 and r30>0 and hourly_state not in {"STRONG_BEAR","BEAR"}:
        state,label,reason="STRONG_UPTREND","🟢 장중 강한 우상향",f"세션 추세 {up_score}/13 · {item.get('hourly_structure_label','60분 형성 중')}"
    elif up_score>=8 and down_score<=5:
        state,label,reason="UPTREND_WEAKENING","🟡 상승추세 약화",f"상승 {up_score}/13 · 하락 {down_score}/10"
    elif down_score>=6 and r30<0:
        state,label,reason="UPTREND_WEAKENING","🟠 하락전환 의심",f"확정 전 경고 {down_score}/10"
    else:
        state,label,reason="UNKNOWN","⚪ 방향 확인 중",f"세션 {session_ret:+.2f}% · 30분 {r30:+.2f}%"

    item.update(
        intraday_regime_state=state,
        intraday_regime_label=label,
        intraday_regime_reason=reason,
        intraday_trade_type=("우상향 반복단타" if state in {"STRONG_UPTREND","UPTREND_PULLBACK"} else "관찰/대기"),
        intraday_short_pullback=state=="UPTREND_PULLBACK",
        intraday_downtrend_confirmed=state in {"DOWNTREND_REVERSAL","DOWNTREND"},
        intraday_uptrend_score=up_score,
        intraday_reversal_score=down_score,
        intraday_return_5m=r5,
        intraday_return_15m=r15,
        intraday_return_30m=r30,
        intraday_return_60m=r60,
        intraday_session_return=session_ret,
        intraday_vwap_hold_ratio=vwap_hold,
        intraday_reclaim_count=reclaim_count,
        intraday_up_down_ratio=up_down_ratio,
        intraday_ema20=e20,
        intraday_ema50=e50,
        intraday_ema20_slope=e20_slope,
        intraday_ema50_slope=e50_slope,
        intraday_higher_high=hh,
        intraday_higher_low=hl,
        intraday_lower_high=lh,
        intraday_lower_low=ll,
        intraday_drawdown_from_high=dd,
    )
    return item

def _safe_pct_return(values,minutes):
    if len(values)>minutes and values[-1-minutes]>0:
        return (values[-1]/values[-1-minutes]-1)*100
    return 0.0

def forward_forecast_plan(item:dict, market:str):
    """현재 차트로 향후 5/15/30/60분의 중심예상·범위·상승확률을 계산한다.

    확정값이 아니라 현재 장중 구조가 유지된다는 조건부 예상이다.
    """
    price=float(item.get("price",0) or 0)
    closes=[float(x or 0) for x in (item.get("chart_close_1m",[]) or []) if float(x or 0)>0]
    highs=[float(x or 0) for x in (item.get("chart_high_1m",[]) or []) if float(x or 0)>0]
    lows=[float(x or 0) for x in (item.get("chart_low_1m",[]) or []) if float(x or 0)>0]
    vols=[float(x or 0) for x in (item.get("chart_volume_1m",[]) or [])]
    if price<=0 or len(closes)<30:
        item["forward_forecasts"]={}
        return item

    n=len(closes)
    r1=pd.Series(closes,dtype=float).pct_change().dropna()*100
    recent_r1=r1.tail(min(90,len(r1)))
    sigma1=float(recent_r1.std(ddof=0)) if len(recent_r1)>=10 else 0.0
    median_abs=float(recent_r1.abs().median()) if len(recent_r1) else 0.0
    bar_range=[]
    for h,l in zip(highs[-60:],lows[-60:]):
        if l>0: bar_range.append((h/l-1)*100)
    median_bar_range=float(pd.Series(bar_range).median()) if bar_range else 0.0
    sigma1=max(sigma1,median_abs*1.20,median_bar_range*0.45,0.03)

    r5=_safe_pct_return(closes,5); r15=_safe_pct_return(closes,15)
    r30=_safe_pct_return(closes,30); r60=_safe_pct_return(closes,60)
    session_ret=float(item.get("intraday_session_return",0) or 0)
    session_minutes=max(30,n-1)

    # 분당 드리프트: 짧은 추세와 큰 추세를 섞되 긴 예측일수록 감쇠한다.
    per_min=(r5/5)*0.15+(r15/15)*0.25+(r30/30)*0.30+(r60/60)*0.20+(session_ret/session_minutes)*0.10
    # 노이즈가 큰 종목에서 직선 외삽이 폭주하지 않게 제한한다.
    per_min=max(-sigma1*0.45,min(sigma1*0.45,per_min))

    regime=str(item.get("intraday_regime_state","UNKNOWN"))
    box_state=str(item.get("box_state","UNAVAILABLE"))
    hourly_state=str(item.get("hourly_structure_state","FORMING"))
    hourly_bull=float(item.get("hourly_structure_score",0) or 0)
    hourly_bear=float(item.get("hourly_structure_bear_score",0) or 0)
    hourly_ret=float(item.get("hourly_structure_return",0) or 0)
    vwap_hold=float(item.get("intraday_vwap_hold_ratio",0) or 0)
    up_score=float(item.get("intraday_uptrend_score",0) or 0)
    down_score=float(item.get("intraday_reversal_score",0) or 0)
    rvol=float(item.get("rvol",0) or 0)
    spread=item.get("verified_spread_percent",item.get("repeat_spread_percent"))
    try: spread=float(spread) if spread is not None else None
    except Exception: spread=None

    t1=float(item.get("structural_target1",item.get("structural_target",0)) or 0)
    t2=float(item.get("structural_target2",0) or 0)
    box_low=float(item.get("box_low",0) or 0); box_high=float(item.get("box_high",0) or 0)
    box_pos=float(item.get("box_position",0.5) or 0.5)

    base_quality=50.0
    base_quality+=min(15.0,max(0.0,(n-30)/330*15.0))
    base_quality+=8.0 if vwap_hold>=0.60 else 3.0 if vwap_hold>=0.50 else -4.0
    base_quality+=5.0 if rvol>=1.0 else -3.0
    base_quality+=4.0 if spread is not None and spread<=0.20 else 0.0
    base_quality-=8.0 if spread is None else 0.0
    base_quality=max(25.0,min(88.0,base_quality))

    out={}
    for horizon in (5,15,30,60):
        decay={5:0.95,15:0.80,30:0.66,60:0.52}[horizon]
        center=per_min*horizon*decay

        if regime=="STRONG_UPTREND":
            center+=sigma1*math.sqrt(horizon)*0.20
        elif regime=="UPTREND_PULLBACK":
            center+=sigma1*math.sqrt(horizon)*0.12
            if horizon<=15: center-=max(0.0,-r5)*0.10
        elif regime in {"DOWNTREND_REVERSAL","DOWNTREND"}:
            center-=sigma1*math.sqrt(horizon)*0.22
        elif regime=="UPTREND_WEAKENING":
            center*=0.55

        # 최근 3~5개 완성 60분봉 구조는 긴 예측일수록 더 강하게 반영한다.
        hourly_weight={5:0.02,15:0.05,30:0.10,60:0.18}[horizon]
        if hourly_state=="STRONG_BULL":
            center+=max(0.0,hourly_ret)*hourly_weight + sigma1*math.sqrt(horizon)*0.08
        elif hourly_state=="BULL":
            center+=max(0.0,hourly_ret)*hourly_weight*0.65
        elif hourly_state=="STRONG_BEAR":
            center-=max(0.0,-hourly_ret)*hourly_weight + sigma1*math.sqrt(horizon)*0.10
        elif hourly_state=="BEAR":
            center-=max(0.0,-hourly_ret)*hourly_weight*0.65

        # 진짜 박스는 방향 외삽 대신 현재 위치에서 중앙/상단으로 평균회귀한다.
        if box_state=="RANGE" and box_low>0 and box_high>box_low:
            midpoint=(box_low+box_high)/2
            if box_pos<=0.35:
                destination=midpoint if horizon<=15 else box_high*0.998
            elif box_pos>=0.70:
                destination=midpoint
            else:
                destination=midpoint
            box_center=(destination/price-1)*100
            center=center*0.25+box_center*0.75

        band=max(sigma1*math.sqrt(horizon)*1.15,
                 median_bar_range*math.sqrt(max(1,horizon/5))*0.65,
                 0.08)
        # 60분 예측은 불확실성을 더 넓게 표시한다.
        if horizon==60: band*=1.12

        low=center-band
        high=center+band

        # 차트 장벽을 범위에 반영하되, 강한 우상향은 2차 목표까지 열어둔다.
        if box_state=="RANGE" and box_low>0 and box_high>0:
            low=max(low,(box_low/price-1)*100-0.15)
            high=min(high,(box_high/price-1)*100+0.15)
        elif regime in {"STRONG_UPTREND","UPTREND_PULLBACK"}:
            barrier=t2 if t2>price else t1 if t1>price else 0
            if barrier>0:
                high=min(high,max((barrier/price-1)*100+band*0.35,center+band*0.40))

        if low>high:
            low,high=min(low,center),max(high,center)
        center=max(low,min(high,center))

        # 상승확률: 중심예상/불확실성 + 큰 추세 근거. 50%에서 과도하게 멀어지지 않도록 제한.
        z=center/max(band,0.05)
        prob=50+30*math.tanh(z)
        prob+=(up_score-6)*1.4-(down_score-4)*1.5
        if regime=="STRONG_UPTREND": prob+=5
        elif regime=="UPTREND_PULLBACK": prob+=3
        elif regime in {"DOWNTREND_REVERSAL","DOWNTREND"}: prob-=7
        if hourly_state=="STRONG_BULL": prob+=6 if horizon>=30 else 3
        elif hourly_state=="BULL": prob+=3 if horizon>=30 else 1
        elif hourly_state=="STRONG_BEAR": prob-=7 if horizon>=30 else 3
        elif hourly_state=="BEAR": prob-=4 if horizon>=30 else 2
        if box_state=="RANGE":
            if box_pos<=0.35: prob+=5
            elif box_pos>=0.70: prob-=5
        prob=max(15.0,min(85.0,prob))

        confidence=base_quality
        if int(item.get("hourly_bars",0) or 0)>=3:
            confidence+=4
        if hourly_state in {"STRONG_BULL","STRONG_BEAR"}:
            confidence+=3
        if horizon==60: confidence-=8
        elif horizon==30: confidence-=4
        confidence=max(20.0,min(90.0,confidence))

        out[horizon]={
            "center_pct":round(center,3),"low_pct":round(low,3),"high_pct":round(high,3),
            "up_probability":round(prob,1),"confidence":round(confidence,1),
            "center_price":round(price*(1+center/100),6),
            "low_price":round(price*(1+low/100),6),"high_price":round(price*(1+high/100),6),
        }

    item["forward_forecasts"]=out
    # 기존 엔진 필드도 새 조건부 예측 중심값과 동기화한다.
    item["forecast_5m"]=out[5]["center_pct"]
    item["forecast_15m"]=out[15]["center_pct"]
    item["forecast_30m"]=out[30]["center_pct"]
    item["forecast_60m"]=out[60]["center_pct"]
    item["forecast_10m"]=(out[5]["center_pct"]+out[15]["center_pct"])/2
    item["forecast_20m"]=(out[15]["center_pct"]+out[30]["center_pct"])/2
    item["forecast_method"]="1분봉 변동성 + 5/15/30/60분 모멘텀 + 최근 3~5개 완성 60분봉 HH/HL·LH/LL 구조 + 세션추세 + VWAP체류 + EMA구조 + 박스/저항 보정"
    return item


def box_regime_plan(item:dict):
    """1~2% 왕복 박스를 이상치에 덜 민감하게 판정한다."""
    frame=_intraday_ohlcv(item)
    if len(frame)<45:
        item.update(box_state="UNAVAILABLE",box_label="⚪ 박스 자료 형성 중")
        return item
    frame=frame.tail(min(120,len(frame))).copy()
    lows=frame["low"].astype(float); highs=frame["high"].astype(float); closes=frame["close"].astype(float)
    # 한 번의 긴 꼬리 때문에 박스폭이 왜곡되지 않도록 5/95 분위 밴드 사용
    lo=float(lows.quantile(0.05)); hi=float(highs.quantile(0.95))
    if not (hi>lo>0):
        item.update(box_state="NOT_RANGE",box_label="⚪ 박스 조건 미달")
        return item
    width=(hi/lo-1)*100
    atr,_=_atr_and_range(frame)
    tol=max((hi-lo)*0.10,atr*0.45,lo*0.0012)
    lower_touches=int((lows<=lo+tol).sum())
    upper_touches=int((highs>=hi-tol).sum())
    mid=(hi+lo)/2
    sides=(closes>=mid).astype(int)
    crossings=int((sides.diff().abs()==1).sum())
    ema20=closes.ewm(span=20,adjust=False).mean()
    # 박스 여부는 마지막 10분 EMA보다 전체 구간 회귀기울기가 더 신뢰성이 높다.
    ys=[float(v) for v in closes.tolist()]
    nn=len(ys); xm=(nn-1)/2.0; ym=sum(ys)/nn
    denom=sum((i-xm)**2 for i in range(nn))
    reg_slope=(sum((i-xm)*(y-ym) for i,y in enumerate(ys))/denom) if denom>0 else 0.0
    slope=(reg_slope*(nn-1)/ym*100) if ym>0 else 0.0
    pos=float((closes.iloc[-1]-lo)/(hi-lo))

    # 앞/뒤 절반 폭이 비슷해야 진짜 박스
    half=len(frame)//2
    def half_width(x):
        qlo=float(x["low"].quantile(.05)); qhi=float(x["high"].quantile(.95))
        return (qhi/qlo-1)*100 if qhi>qlo>0 else 0.0
    w1=half_width(frame.iloc[:half]); w2=half_width(frame.iloc[half:])
    stability=(min(w1,w2)/max(w1,w2)) if max(w1,w2)>0 else 0.0
    net_move=abs((float(closes.iloc[-1])/float(closes.iloc[0])-1)*100) if float(closes.iloc[0])>0 else 99.0
    path_move=float(closes.pct_change().abs().sum()*100)
    directional_efficiency=(net_move/path_move) if path_move>0 else 1.0

    slope_limit=min(0.35,max(0.22,width*0.25))
    valid=(1.0<=width<=2.0 and lower_touches>=2 and upper_touches>=2 and crossings>=3
           and abs(slope)<=slope_limit and stability>=0.65
           and directional_efficiency<=0.35)
    # 실제 과거 하단→상단 왕복 성공률과 소요시간을 계산한다.
    low_flags=[float(x)<=lo+tol for x in lows.tolist()]
    high_flags=[float(x)>=hi-tol for x in highs.tolist()]
    low_starts=[i for i,flag in enumerate(low_flags) if flag and (i==0 or not low_flags[i-1])]
    attempts=successes=0; durations=[]
    for start in low_starts:
        if start>=len(low_flags)-3:
            continue
        attempts+=1
        end=min(len(high_flags),start+61)
        hit=next((j for j in range(start+1,end) if high_flags[j]),None)
        if hit is not None:
            successes+=1; durations.append(hit-start)
    # 베타(1,1) 스무딩: 표본이 적을수록 0/100% 극단값을 피한다.
    cycle_rate=(successes+1)/(attempts+2)*100 if attempts>0 else 50.0
    cycle_eta=float(pd.Series(durations).median()) if durations else 0.0

    break_risk=valid and (pos<0.04 or closes.iloc[-1]<lo) and closes.iloc[-1]<float(ema20.iloc[-1])
    if break_risk: state,label="RANGE_BREAK_RISK","🟠 박스 하단 이탈 위험"
    elif valid: state,label="RANGE","🟦 1~2% 왕복 박스"
    else: state,label="NOT_RANGE","⚪ 박스 조건 미달"
    item.update(box_state=state,box_label=label,box_width_percent=width,box_low=lo,box_high=hi,
                box_lower_touches=lower_touches,box_upper_touches=upper_touches,
                box_mid_crossings=crossings,box_position=pos,box_ema20_slope=slope,
                box_stability=stability,box_net_move_percent=net_move,box_directional_efficiency=directional_efficiency,
                box_cycle_attempts=attempts,box_cycle_successes=successes,
                box_cycle_success_rate=cycle_rate,box_cycle_eta_minutes=cycle_eta)
    return item



def strategy_target_plan(item:dict) -> dict:
    """우상향형/박스형 중 하나를 선택하고 그 전략의 가격만 최종 가격으로 사용한다."""
    if not isinstance(item,dict): return item
    item=dict(item)
    price=_num(item.get("price"))
    regime=str(item.get("intraday_regime_state","UNKNOWN"))
    box_state=str(item.get("box_state","UNAVAILABLE"))
    atr=_num(item.get("repeat_atr14"))
    median=_num(item.get("repeat_median_range"))

    # 큰 추세가 살아 있으면 우상향 전략을 우선. 박스는 큰 추세가 불명확할 때만 선택.
    if regime in {"STRONG_UPTREND","UPTREND_PULLBACK"} and bool(item.get("repeat_chart_valid")):
        strategy="UPTREND"
        entry=_num(item.get("repeat_entry")); support=_num(item.get("repeat_support"))
        stop=_num(item.get("repeat_stop")); t1=_num(item.get("repeat_target1")); t2=_num(item.get("repeat_target2"))
        b1=str(item.get("repeat_target1_basis") or "실제 1분봉 저항")
        b2=str(item.get("repeat_target2_basis") or "")
    elif box_state=="RANGE":
        strategy="RANGE"
        lo=_num(item.get("box_low")); hi=_num(item.get("box_high"))
        entry=support=lo
        buffer=max(atr*0.35,median*0.45,lo*0.0015)
        stop=max(0.0,lo-buffer)
        t1=hi; t2=0.0
        b1="박스 95% 분위 상단"; b2=""
    else:
        strategy="NONE"
        entry=_num(item.get("repeat_entry")); support=_num(item.get("repeat_support"))
        stop=_num(item.get("repeat_stop")); t1=_num(item.get("repeat_target1")); t2=_num(item.get("repeat_target2"))
        b1=str(item.get("repeat_target1_basis") or ""); b2=str(item.get("repeat_target2_basis") or "")

    risk=entry-stop if entry>stop>0 else 0.0
    reward=t1-entry if t1>entry>0 else 0.0
    rr=reward/risk if risk>0 else 0.0
    width=(t1/entry-1)*100 if t1>entry>0 else 0.0
    item.update(
        strategy_type=strategy,strategy_entry=entry,strategy_support=support,strategy_stop=stop,
        strategy_target1=t1,strategy_target2=t2,strategy_range_percent=width,strategy_risk_reward=rr,
        structural_entry=entry,structural_support=support,stop_loss=stop,
        structural_target=t1,structural_target1=t1,structural_target2=t2,
        target1_basis=b1,target2_basis=b2,
        target1_upside_percent=((t1/price-1)*100 if t1>price>0 else 0.0),
        target2_upside_percent=((t2/price-1)*100 if t2>price>0 else 0.0),
        risk_reward=rr,risk_reward_target1=rr,
        repeat_scalp_buy_level=entry,repeat_scalp_sell_level=t1,repeat_scalp_invalidation=stop,
        repeat_scalp_range_percent=width,
        level_plan_valid=bool(0<stop<entry<price<t1) if strategy=="UPTREND" else bool(0<stop<entry<=price<t1),
    )
    return item



def data_quality_plan(item:dict, market_code:str, tradable:bool=True) -> dict:
    """UI와 검증기가 동일하게 사용하는 데이터 품질 판정."""
    if not isinstance(item,dict): return item
    item=dict(item)
    price=_num(item.get("price"))
    bars=len(item.get("chart_close_1m",[]) or [])
    vwap=_num(item.get("vwap")); ema9=_num(item.get("ema9")); ema20=_num(item.get("ema20"))
    rvol=_num(item.get("rvol"))
    bid=_num(item.get("best_bid")); ask=_num(item.get("best_ask"))
    spread=((ask-bid)/((ask+bid)/2)*100) if ask>=bid>0 else None
    spread_limit=0.35 if "레버리지" in str(item.get("asset_type","")) else 0.25
    last_age=None
    times=item.get("chart_time_1m",[]) or []
    if times:
        try:
            last=pd.Timestamp(pd.to_datetime(times[-1]))
            tz=KST if str(market_code).upper()=="KR" else ET
            if last.tzinfo is None: last=last.tz_localize(tz)
            last=last.tz_convert("UTC")
            last_age=max(0.0,(pd.Timestamp.now(tz="UTC")-last).total_seconds())
        except Exception:
            last_age=None
    checks={
        "현재가":price>0,
        "1분봉20+":bars>=20,
        "VWAP":vwap>0,
        "EMA9/20":ema9>0 and ema20>0,
        "실데이터":not bool(item.get("intraday_fallback")),
        "분봉신선도":(not tradable) or (last_age is not None and last_age<=180),
        "RVOL범위":0.05<=rvol<=20,
        "스프레드":spread is not None and spread<=spread_limit,
    }
    core=all(checks[k] for k in ("현재가","1분봉20+","VWAP","EMA9/20","실데이터","분봉신선도","RVOL범위"))
    execution=core and checks["스프레드"]
    item.update(data_quality_checks=checks,data_core_passed=core,data_gate_passed=execution,
                execution_data_ready=execution,verified_spread_percent=spread,
                verified_spread_limit=spread_limit,last_bar_age_seconds=last_age)
    return item



def target_reach_probability_plan(item: dict) -> dict:
    """목표 도달/선도달 확률을 추정한다.

    우선순위: 실제 가격구조 > 변동성/모멘텀 > 60분 구조.
    투영 목표는 신뢰도를 낮추며, 확률은 90%를 넘기지 않는다.
    """
    if not isinstance(item,dict): return item
    item=dict(item)
    price=_num(item.get("price"))
    t1=_num(item.get("strategy_target1",item.get("structural_target1")))
    t2=_num(item.get("strategy_target2",item.get("structural_target2")))
    stop=_num(item.get("strategy_stop",item.get("stop_loss")))
    ff=item.get("forward_forecasts") or {}
    if price<=0 or t1<=price or not ff:
        item.update(target1_reach_probability=0.0,target2_reach_probability=0.0,
                    target1_eta_minutes=0,target2_eta_minutes=0,
                    stop_first_risk_probability=0.0,target1_before_stop_probability=0.0,
                    target_probability_confidence=0.0,target_probability_label="자료 부족")
        return item

    hourly=str(item.get("hourly_structure_state","FORMING"))
    regime=str(item.get("intraday_regime_state","UNKNOWN"))
    strategy=str(item.get("strategy_type","NONE"))
    vwap_hold=_num(item.get("intraday_vwap_hold_ratio"))
    trend=_num(item.get("repeat_trend_score"))
    rvol=_num(item.get("rvol"))
    structure=0.0
    structure += {"STRONG_BULL":6,"BULL":3,"BEAR":-5,"STRONG_BEAR":-8}.get(hourly,0)
    structure += {"STRONG_UPTREND":6,"UPTREND_PULLBACK":4,"DOWNTREND_REVERSAL":-8,"DOWNTREND":-10}.get(regime,0)
    structure += _clamp((vwap_hold-.55)*18,-5,5)
    structure += _clamp((trend-6)*1.2,-4,4)
    structure += _clamp((rvol-.8)*1.3,-2,3)

    def target_stats(target):
        dist=(target/price-1)*100
        probs=[]; eta=0
        for h in (5,15,30,60):
            f=ff.get(h) or ff.get(str(h)) or {}
            center=_num(f.get("center_pct")); lo=_num(f.get("low_pct")); hi=_num(f.get("high_pct"))
            sigma=max(.06,abs(hi-lo)/2.56)
            endpoint=1-_normal_cdf((dist-center)/sigma)
            # 장중 터치 확률 보정은 제한적으로만 적용
            touch=_clamp(endpoint*100*1.10 + structure,1,90)
            probs.append((h,touch,hi))
            if eta==0 and hi>=dist and touch>=50: eta=h
        best=max(x[1] for x in probs)
        return best,eta,dist

    p1,eta1,_=target_stats(t1)
    p2=eta2=0.0
    if t2>t1:
        p2,eta2,_=target_stats(t2); p2=min(p2,p1*.92)

    # 실제 저항보다 ATR 투영은 낮은 신뢰
    projected1="투영" in str(item.get("target1_basis",""))
    projected2="투영" in str(item.get("target2_basis",""))
    if projected1: p1-=7
    if projected2: p2-=9

    # 박스 전략은 '60분 후 종가'보다 실제 과거 하단→상단 왕복 이력이 더 직접적인 근거다.
    if strategy=="RANGE" and str(item.get("box_state"))=="RANGE":
        attempts=int(_num(item.get("box_cycle_attempts")))
        cycle_rate=_num(item.get("box_cycle_success_rate"),50.0)
        cycle_eta=_num(item.get("box_cycle_eta_minutes"))
        pos=_num(item.get("box_position"),0.5)
        if attempts>=2 and pos<=0.42:
            history_weight=min(0.75,0.45+attempts*0.05)
            p1=p1*(1-history_weight)+cycle_rate*history_weight
            if cycle_eta>0:
                eta1=max(5,min(60,int(round(cycle_eta))))

    stoprisk=0.0
    if 0<stop<price:
        dd=(stop/price-1)*100
        risks=[]
        for h in (5,15,30,60):
            f=ff.get(h) or ff.get(str(h)) or {}
            center=_num(f.get("center_pct")); lo=_num(f.get("low_pct")); hi=_num(f.get("high_pct"))
            sigma=max(.06,abs(hi-lo)/2.56)
            endpoint=_normal_cdf((dd-center)/sigma)
            risks.append(_clamp(endpoint*100*1.10,1,90))
        stoprisk=max(risks)
        if hourly in {"BEAR","STRONG_BEAR"}: stoprisk+=5
        if regime in {"DOWNTREND_REVERSAL","DOWNTREND"}: stoprisk+=7

    p1=_clamp(p1,1,90); p2=_clamp(p2,0,min(88,p1)); stoprisk=_clamp(stoprisk,1,90)
    # 독립사건으로 가장하지 않고 보수적으로 감점한 선도달 점수
    first=_clamp(p1 - stoprisk*0.55,1,88)

    confs=[_num((ff.get(h) or {}).get("confidence")) for h in (15,30,60)]
    confidence=sum(confs)/len(confs) if confs else 0.0
    if projected1: confidence-=8
    if strategy=="RANGE" and int(_num(item.get("box_cycle_attempts")))>=3:
        confidence+=6
    if int(item.get("hourly_bars",0) or 0)<3: confidence-=8
    if not bool(item.get("data_core_passed",True)): confidence-=12
    confidence=_clamp(confidence,20,88)

    if first>=78: label="🟢 1차 선도달 가능성 높음"
    elif first>=68: label="🟡 1차 선도달 우세"
    elif first>=55: label="⚪ 우위 약함"
    else: label="🔴 목표 선도달 신뢰 낮음"
    item.update(target1_reach_probability=round(p1,1),target2_reach_probability=round(p2,1),
                target1_eta_minutes=int(eta1),target2_eta_minutes=int(eta2),
                stop_first_risk_probability=round(stoprisk,1),
                target1_before_stop_probability=round(first,1),
                target_probability_confidence=round(confidence,1),
                target_probability_label=label,
                target_probability_model="차트저항/박스 + 조건부 5·15·30·60분 분포 + 60분구조 + VWAP/RVOL; 투영목표 감점")
    return item



def trade_decision_plan(item:dict) -> dict:
    """모든 계산의 최종 단일 판정. 다른 점수/투표로 다시 뒤집지 않는다."""
    if not isinstance(item,dict): return item
    item=dict(item)
    strategy=str(item.get("strategy_type","NONE"))
    regime=str(item.get("intraday_regime_state","UNKNOWN"))
    hourly=str(item.get("hourly_structure_state","FORMING"))
    box=str(item.get("box_state","UNAVAILABLE"))
    rr=_num(item.get("strategy_risk_reward",item.get("risk_reward")))
    width=_num(item.get("strategy_range_percent",item.get("repeat_scalp_range_percent")))
    pfirst=_num(item.get("target1_before_stop_probability"))
    p1=_num(item.get("target1_reach_probability"))
    stoprisk=_num(item.get("stop_first_risk_probability"))
    conf=_num(item.get("target_probability_confidence"))
    data_ok=bool(item.get("data_gate_passed"))
    price=_num(item.get("price")); entry=_num(item.get("strategy_entry")); t1=_num(item.get("strategy_target1"))

    reasons=[]
    if not data_ok: reasons.append("실시간 데이터/호가 검문 미통과")
    if rr<1.20: reasons.append(f"RR {rr:.2f}<1.20")
    if pfirst<68: reasons.append(f"1차 선도달 {pfirst:.0f}%<68%")
    if stoprisk>35: reasons.append(f"손절 선도달 위험 {stoprisk:.0f}%>35%")
    if conf<45: reasons.append(f"확률 신뢰 {conf:.0f}%<45%")
    if not (0<entry<t1): reasons.append("진입/목표 가격구조 불량")

    if strategy=="UPTREND":
        if regime not in {"STRONG_UPTREND","UPTREND_PULLBACK"}: reasons.append("우상향 추세 미확정")
        if hourly in {"STRONG_BEAR","BEAR"}: reasons.append("60분봉 약세")
        if not (0.50<=width<=1.50): reasons.append(f"반복폭 {width:.2f}% 범위 밖")
    elif strategy=="RANGE":
        pos=_num(item.get("box_position"),0.5)
        if box!="RANGE": reasons.append("박스 미확정")
        if pos>0.42: reasons.append(f"박스 하단 진입위치 아님({pos*100:.0f}%)")
        if not (1.0<=_num(item.get("box_width_percent"))<=2.0): reasons.append("박스폭 1~2% 아님")
    else:
        reasons.append("전략 미확정")

    final=not reasons
    if final:
        decision="BUY_REVIEW"
        label="🟢 매수 검토"
    elif pfirst>=55 and strategy!="NONE" and data_ok:
        decision="WAIT"
        label="🟡 대기"
    else:
        decision="AVOID"
        label="🔴 회피"
    item.update(final_candidate=final,entry_checks_passed=final,trade_decision=decision,
                trade_decision_label=label,trade_decision_reasons=reasons,
                repeat_candidate=bool(item.get("repeat_candidate")) and strategy!="NONE")
    return item
