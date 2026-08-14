# -*- coding: utf-8 -*-
"""초단타 전용 Streamlit 앱.

같은 폴더의 app.py에 번들된 KIS 엔진을 읽어 사용한다.
반복단타 기본 후보는 실제 차트의 지지→1차 저항 폭 0.5~1.5%만 표시한다.
1차·2차 목표가는 임의 +1%/+2%가 아니라 실제 분봉 저항/돌파 구조로 계산한다.
"""
from __future__ import annotations

import ast
import base64
import importlib.abc
import importlib.util
import json
import math
import re
import requests
import sqlite3
import sys
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="초단타 VWAP 타점", page_icon="⚡", layout="wide")

try:
    from run_live_validation import (
        KR_UNIVERSE as AUDIT_KR_UNIVERSE,
        US_UNIVERSE as AUDIT_US_UNIVERSE,
        connect as audit_connect,
        export_summary as audit_export_summary,
        grade_pending as audit_grade_pending,
        signal_window_open as audit_signal_window_open,
        market_is_open as audit_market_is_open,
        store_quote as audit_store_quote,
        store_result as audit_store_result,
        DB_PATH as AUDIT_DB_PATH,
        CSV_PATH as AUDIT_CSV_PATH,
    )
    AUDIT_IMPORT_ERROR = ""
except Exception as exc:
    AUDIT_KR_UNIVERSE = []
    AUDIT_US_UNIVERSE = []
    AUDIT_IMPORT_ERROR = str(exc)

KST = timezone(timedelta(hours=9), name="KST")
ET = ZoneInfo("America/New_York")
HISTORY_DB = Path(tempfile.gettempdir()) / "ymym_scalp_validation.sqlite3"

KR_UNIVERSE = [
    {"ticker":"005930","name":"삼성전자","exchange":"KR","asset_type":"우량주"},
    {"ticker":"000660","name":"SK하이닉스","exchange":"KR","asset_type":"우량주"},
    {"ticker":"035420","name":"NAVER","exchange":"KR","asset_type":"우량주"},
    {"ticker":"005380","name":"현대차","exchange":"KR","asset_type":"우량주"},
    {"ticker":"000270","name":"기아","exchange":"KR","asset_type":"우량주"},
    {"ticker":"035720","name":"카카오","exchange":"KR","asset_type":"우량주"},
    {"ticker":"068270","name":"셀트리온","exchange":"KR","asset_type":"우량주"},
    {"ticker":"105560","name":"KB금융","exchange":"KR","asset_type":"우량주"},
    {"ticker":"055550","name":"신한지주","exchange":"KR","asset_type":"우량주"},
    {"ticker":"086790","name":"하나금융지주","exchange":"KR","asset_type":"우량주"},
    {"ticker":"066570","name":"LG전자","exchange":"KR","asset_type":"우량주"},
    {"ticker":"051910","name":"LG화학","exchange":"KR","asset_type":"우량주"},
    {"ticker":"006400","name":"삼성SDI","exchange":"KR","asset_type":"우량주"},
    {"ticker":"207940","name":"삼성바이오로직스","exchange":"KR","asset_type":"우량주"},
    {"ticker":"012330","name":"현대모비스","exchange":"KR","asset_type":"우량주"},
    {"ticker":"028260","name":"삼성물산","exchange":"KR","asset_type":"우량주"},
    {"ticker":"017670","name":"SK텔레콤","exchange":"KR","asset_type":"우량주"},
    {"ticker":"030200","name":"KT","exchange":"KR","asset_type":"우량주"},
    {"ticker":"069500","name":"KODEX 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"102110","name":"TIGER 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"396500","name":"TIGER 반도체TOP10","exchange":"KR","asset_type":"ETF"},
    {"ticker":"122630","name":"KODEX 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"123320","name":"TIGER 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"488080","name":"TIGER 반도체TOP10레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"423920","name":"TIGER 미국필라델피아반도체레버리지(합성)","exchange":"KR","asset_type":"레버리지 ETF"},
]

US_UNIVERSE = [
    {"ticker":"AAPL","name":"애플","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"MSFT","name":"마이크로소프트","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"NVDA","name":"엔비디아","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AMZN","name":"아마존","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"META","name":"메타","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"TSLA","name":"테슬라","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"GOOGL","name":"알파벳","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AMD","name":"AMD","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"INTC","name":"인텔","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AVGO","name":"브로드컴","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"QCOM","name":"퀄컴","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"MU","name":"마이크론","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"CSCO","name":"시스코","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"BAC","name":"뱅크오브아메리카","exchange":"NYSE","asset_type":"우량주"},
    {"ticker":"JPM","name":"JP모건","exchange":"NYSE","asset_type":"우량주"},
    {"ticker":"XOM","name":"엑슨모빌","exchange":"NYSE","asset_type":"우량주"},
    {"ticker":"WMT","name":"월마트","exchange":"NYSE","asset_type":"우량주"},
    {"ticker":"QQQ","name":"나스닥100 ETF","exchange":"NASDAQ","asset_type":"ETF"},
    {"ticker":"SPY","name":"S&P500 ETF","exchange":"AMEX","asset_type":"ETF"},
    {"ticker":"IWM","name":"러셀2000 ETF","exchange":"AMEX","asset_type":"ETF"},
    {"ticker":"SMH","name":"반도체 ETF","exchange":"NASDAQ","asset_type":"ETF"},
    {"ticker":"SOXL","name":"반도체 3배 레버리지 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"SOXS","name":"반도체 3배 인버스 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"TQQQ","name":"나스닥100 3배 레버리지 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
    {"ticker":"SQQQ","name":"나스닥100 3배 인버스 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
    {"ticker":"UPRO","name":"S&P500 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},
    {"ticker":"SPXU","name":"S&P500 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},
    {"ticker":"SSO","name":"S&P500 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},
    {"ticker":"SDS","name":"S&P500 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},
    {"ticker":"QLD","name":"나스닥100 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},
    {"ticker":"QID","name":"나스닥100 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},
    {"ticker":"TECL","name":"미국 기술주 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},
    {"ticker":"TECS","name":"미국 기술주 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},
    {"ticker":"FAS","name":"미국 금융주 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},
    {"ticker":"FAZ","name":"미국 금융주 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},
    {"ticker":"LABU","name":"미국 바이오 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},
    {"ticker":"LABD","name":"미국 바이오 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},
    {"ticker":"TNA","name":"러셀2000 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},
    {"ticker":"TZA","name":"러셀2000 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},
]


def market_clock(market: str, now: datetime | None = None) -> dict:
    now_kst = now.astimezone(KST) if now else datetime.now(KST)
    if market == "국내":
        minute = now_kst.hour * 60 + now_kst.minute
        tradable = now_kst.weekday() < 5 and 9 * 60 <= minute < 15 * 60 + 30
        return {"session": "국내 정규장" if tradable else "국내 장외시간", "tradable": tradable, "local_time": now_kst.strftime("%H:%M:%S KST")}
    now_et = now_kst.astimezone(ET)
    minute = now_et.hour * 60 + now_et.minute
    weekday = now_et.weekday() < 5
    if weekday and 4*60 <= minute < 9*60+30:
        session, tradable = "미국 프리마켓", True
    elif weekday and 9*60+30 <= minute < 16*60:
        session, tradable = "미국 정규장", True
    elif weekday and 16*60 <= minute < 20*60:
        session, tradable = "미국 애프터마켓", True
    else:
        session, tradable = "미국 장외시간", False
    return {"session":session,"tradable":tradable,"local_time":f"{now_et.strftime('%H:%M:%S')} ET / {now_kst.strftime('%H:%M:%S')} KST"}


def db_connect():
    con = sqlite3.connect(HISTORY_DB, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,ticker TEXT NOT NULL,issued REAL NOT NULL,
        base_price REAL NOT NULL,f5 REAL NOT NULL,f10 REAL NOT NULL,f20 REAL NOT NULL DEFAULT 0,
        f30 REAL NOT NULL,actual5 REAL,actual10 REAL,actual20 REAL,actual30 REAL,
        UNIQUE(ticker,issued))""")
    return con


@st.cache_data(ttl=86400, show_spinner=False)
def kr_name_map() -> dict:
    def norm(text): return re.sub(r"[^0-9a-z가-힣]", "", str(text).casefold())
    mapping = {norm(r["name"]):(r["ticker"],r["name"]) for r in KR_UNIVERSE}
    try:
        from pykrx import stock as krx_stock
        for market_name in ("KOSPI","KOSDAQ"):
            for code in krx_stock.get_market_ticker_list(market=market_name):
                name = krx_stock.get_market_ticker_name(code)
                if name: mapping[norm(name)] = (str(code),str(name))
    except Exception:
        pass
    return mapping


def normalize_us_exchange(raw: str) -> str:
    raw = str(raw or "").upper().strip()
    if raw in {"NMS","NGM","NCM","NAS","NASDAQ"}: return "NASDAQ"
    if raw in {"NYQ","NYS","NYSE"}: return "NYSE"
    if raw in {"ASE","PCX","AMEX"}: return "AMEX"
    return ""


@st.cache_data(ttl=86400, show_spinner=False)
def yahoo_symbol_search(query: str) -> dict | None:
    quotes=[]
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        try:
            r=requests.get(f"https://{host}/v1/finance/search",params={"q":query,"quotesCount":10,"newsCount":0},headers={"User-Agent":"Mozilla/5.0"},timeout=8)
            r.raise_for_status(); quotes=r.json().get("quotes") or []
            if quotes: break
        except Exception: continue
    allowed=[q for q in quotes if str(q.get("quoteType","")).upper() in {"EQUITY","ETF"}]
    us=[q for q in allowed if str(q.get("exchange","")).upper() not in {"KSC","KOE"}]
    q=(us or allowed or [None])[0]
    if not q: return None
    ticker=str(q.get("symbol","")).upper().strip(); exchange=normalize_us_exchange(q.get("exchange",""))
    if not ticker or not exchange: return None
    return {"ticker":ticker,"name":str(q.get("shortname") or q.get("longname") or ticker),"exchange":exchange,"asset_type":"직접 검색"}


def resolve_manual(value: str, market: str) -> dict | None:
    value=value.strip()
    if not value: return None
    if market=="국내":
        if value.isdigit(): return {"ticker":value.zfill(6),"name":value.zfill(6),"exchange":"KR","asset_type":"직접 검색"}
        norm=re.sub(r"[^0-9a-z가-힣]","",value.casefold()); mapping=kr_name_map()
        if norm in mapping:
            code,name=mapping[norm]; return {"ticker":code,"name":name,"exchange":"KR","asset_type":"직접 검색"}
        partial=next(((c,n) for k,(c,n) in mapping.items() if norm in k),None)
        if partial: return {"ticker":partial[0],"name":partial[1],"exchange":"KR","asset_type":"직접 검색"}
        return None
    ticker=re.sub(r"[^A-Z0-9.\-]","",value.upper())
    known=next((dict(r) for r in US_UNIVERSE if r["ticker"]==ticker),None)
    if known: known["asset_type"]="직접 검색"; return known
    return yahoo_symbol_search(value)


def _load_bundled_sources() -> dict:
    source_path=Path(__file__).with_name("app.py")
    if not source_path.exists():
        dev=Path(__file__).resolve().parent.parent/"ymym_stock_scanner_fixed"/"app.py"
        if dev.exists(): source_path=dev
    if not source_path.exists(): st.error("같은 폴더에 기존 app.py가 필요합니다."); st.stop()
    tree=ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="_BUNDLED" for t in node.targets):
            val=ast.literal_eval(node.value)
            if isinstance(val,dict): return val
    raise RuntimeError("app.py에서 번들 엔진을 찾지 못했습니다.")

_BUNDLED=_load_bundled_sources()

class _Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    PACKAGES={"scanner","utils","config","engine","data","ui"}
    def find_spec(self,fullname,path=None,target=None):
        if fullname in _BUNDLED: return importlib.util.spec_from_loader(fullname,self,is_package=False)
        if fullname in self.PACKAGES: return importlib.util.spec_from_loader(fullname,self,is_package=True)
        return None
    def create_module(self,spec): return None
    def exec_module(self,module):
        name=module.__name__
        if name in self.PACKAGES:
            module.__path__=[]; module.__package__=name; module.__file__=str(Path.cwd()/name/"__init__.py"); return
        code=zlib.decompress(base64.b64decode(_BUNDLED[name])).decode("utf-8")
        module.__file__=str(Path.cwd().joinpath(*name.split(".")).with_suffix(".py")); module.__package__=name.rpartition(".")[0]
        exec(compile(code,module.__file__,"exec"),module.__dict__)

if not any(isinstance(x,_Loader) for x in sys.meta_path): sys.meta_path.insert(0,_Loader())
from scanner.kis_engine import KISUnifiedScanner,apply_mode_policy,finalize_trade_item  # noqa:E402

@st.cache_resource
def scanner(): return KISUnifiedScanner()

def fmt(value):
    try:
        x=float(value)
        if not math.isfinite(x): return "-"
        if x>=1000: return f"{x:,.0f}"
        if x>=10: return f"{x:,.2f}"
        return f"{x:,.4f}"
    except Exception: return "-"


def verified_us_change(quote:dict,fallback_price:float=0.0):
    price=float(quote.get("price",fallback_price) or fallback_price or 0)
    previous=float(quote.get("previous",quote.get("previous_close",quote.get("base",0))) or 0)
    raw=float(quote.get("change",quote.get("change_percent",0)) or 0)
    if price>0 and previous>0: return price,previous,round((price/previous-1)*100,4),"현재가·전일종가 재계산"
    return price,previous,raw,"KIS 원본 등락률"


def normalize_us_item(item:dict,row:dict|None=None):
    if not item: return item
    ticker=str((row or {}).get("ticker") or item.get("ticker") or "").upper(); exchange=str((row or {}).get("exchange") or item.get("exchange") or "")
    if not ticker or not exchange: return item
    try:
        q=scanner().client.us_quote(ticker,exchange); price,prev,change,source=verified_us_change(q,float(item.get("price",0) or 0))
        if price>0: item["price"]=price
        item.update(previous_close=prev,change_percent=change,screen_change=change,change_validation_source=source)
    except Exception as e: item["change_validation_error"]=f"{type(e).__name__}: {e}"
    return item


def data_quality_gate(item:dict,market:str):
    price=float(item.get("price",0) or 0); bars=int(item.get("intraday_bar_count",0) or len(item.get("chart_close_1m",[]) or []))
    vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); rvol=float(item.get("rvol",0) or 0)
    bid=float(item.get("best_bid",0) or 0); ask=float(item.get("best_ask",0) or 0)
    spread=((ask-bid)/((ask+bid)/2)*100) if ask>0 and bid>0 and ask>=bid else None
    last_bar_age=None
    try:
        times=item.get("chart_time_1m",[]) or []; last=pd.Timestamp(pd.to_datetime(times[-1]))
        if last.tzinfo is None: last=last.tz_localize(KST if market=="국내" else ET)
        last=last.tz_convert("UTC"); last_bar_age=max(0.0,(pd.Timestamp.now(tz="UTC")-last).total_seconds())
    except Exception: pass
    checks=[
        {"검문":"현재가","통과":price>0,"내용":fmt(price)},
        {"검문":"1분봉 수","통과":bars>=20,"내용":f"{bars}개"},
        {"검문":"VWAP·EMA","통과":vwap>0 and ema9>0,"내용":f"VWAP {fmt(vwap)} / EMA9 {fmt(ema9)}"},
        {"검문":"분봉 출처","통과":not bool(item.get("intraday_fallback")),"내용":str(item.get("intraday_source","미확인"))},
        {"검문":"마지막 분봉 시각","통과":last_bar_age is not None and last_bar_age<=180,"내용":f"{last_bar_age:.0f}초 전" if last_bar_age is not None else "시각 없음"},
        {"검문":"RVOL 정상범위","통과":0.05<=rvol<=20,"내용":f"{rvol:.1f}배"},
        {"검문":"실시간 호가","통과":spread is not None,"내용":f"{spread:.3f}%" if spread is not None else "미수신"},
    ]
    max_spread=0.35 if "레버리지" in str(item.get("asset_type","")) else 0.25
    if spread is not None: checks.append({"검문":"스프레드","통과":spread<=max_spread,"내용":f"기준 {max_spread:.2f}% 이하"})
    return checks,all(bool(r["통과"]) for r in checks[:5]),spread


def _dedupe_price_levels(levels:list[float],tol=0.08):
    result=[]
    for level in sorted(float(x) for x in levels if float(x or 0)>0):
        if not result: result.append(level); continue
        if abs(level/result[-1]-1)*100<=tol: result[-1]=max(result[-1],level)
        else: result.append(level)
    return result


def structural_trade_plan(item:dict,market:str):
    price=float(item.get("price",0) or 0)
    highs=[float(x) for x in (item.get("chart_high_1m",[]) or []) if float(x or 0)>0]
    lows=[float(x) for x in (item.get("chart_low_1m",[]) or []) if float(x or 0)>0]
    closes=[float(x) for x in (item.get("chart_close_1m",[]) or []) if float(x or 0)>0]
    volumes=[float(x or 0) for x in (item.get("chart_volume_1m",[]) or [])]
    if price<=0 or len(highs)<12 or len(lows)<12 or len(closes)<12:
        item.update(level_plan_valid=False,level_plan_reason="분봉 고가·저가 자료 부족"); return item
    vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); ema20=float(item.get("ema20",0) or 0)
    ret5=(closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0; ret15=(closes[-1]/closes[-16]-1)*100 if len(closes)>=16 else 0; ret30=(closes[-1]/closes[-31]-1)*100 if len(closes)>=31 else 0
    higher_high=len(highs)>=12 and max(highs[-6:])>max(highs[-12:-6]); higher_low=len(lows)>=12 and min(lows[-6:])>min(lows[-12:-6])
    up=down=0.0
    for i in range(max(1,len(closes)-20),len(closes)):
        vol=volumes[i] if i<len(volumes) else 0
        if closes[i]>closes[i-1]: up+=vol
        elif closes[i]<closes[i-1]: down+=vol
    volume_dom=up/down if down>0 else (2.0 if up>0 else 0.0); vgap=(price/vwap-1)*100 if vwap>0 else 99
    checks={"VWAP 위":price>vwap>0,"EMA 정배열":price>=ema9>ema20>0,"5분 상승":ret5>0,"15분 상승":ret15>0,"30분 상승":ret30>0,"고점 상승":higher_high,"저점 상승":higher_low,"상승봉 거래량 우세":volume_dom>=1.05,"VWAP 과대이격 아님":0<=vgap<=(2.5 if market=="국내" else 3.0),"최근 고가권 유지":price>=max(highs[-30:])*0.97}
    trend_score=sum(map(bool,checks.values()))
    swing_highs=[highs[i] for i in range(2,len(highs)-2) if highs[i]>=max(highs[i-2:i]) and highs[i]>=max(highs[i+1:i+3])]
    resistances=_dedupe_price_levels(swing_highs); above=[x for x in resistances if x>price]
    swing_lows=[lows[i] for i in range(2,len(lows)-2) if lows[i]<=min(lows[i-2:i]) and lows[i]<=min(lows[i+1:i+3])]
    supports=[(x,"최근 실제 1분봉 스윙 저점") for x in swing_lows if 0<x<price]
    if 0<vwap<price: supports.append((vwap,"VWAP"))
    if 0<ema9<price: supports.append((ema9,"EMA9"))
    if 0<ema20<price: supports.append((ema20,"EMA20"))
    if not supports: item.update(level_plan_valid=False,level_plan_reason="현재가 아래 실제 지지선 없음"); return item
    support,support_reason=max(supports,key=lambda x:x[0])
    lookback=min(30,len(highs)); box_high=max(highs[-lookback:]); box_low=min(lows[-lookback:]); box_width=max(0,box_high-box_low); prior_high=max(highs[-lookback:-1]) if lookback>=2 else box_high; breakout=price>prior_high>0
    t1=t2=0.0; b1=b2=""
    if above:
        t1=above[0]; b1="현재가 위 가장 가까운 실제 1분봉 스윙 저항"
        if len(above)>=2: t2=above[1]; b2="1차 위 다음 실제 1분봉 스윙 저항"
        elif box_width>0 and trend_score>=7: t2=t1+box_width; b2="다음 저항 미형성 · 최근 실제 30분 박스폭 투영"
    elif breakout and box_width>0:
        t1=prior_high+box_width; t2=t1+box_width; b1="기존 고점 돌파 · 최근 실제 박스폭 1회 투영"; b2="돌파 유지 시 실제 박스폭 2회 투영"
    if t1<=price: item.update(level_plan_valid=False,level_plan_reason="현재가 위 차트 기반 1차 목표 미확인"); return item
    if t2<=t1: t2=0.0
    risk=price-support; reward1=t1-price; reward2=t2-price if t2>price else 0
    rr1=reward1/risk if risk>0 else 0; rr2=reward2/risk if risk>0 and reward2>0 else 0
    t1pct=(t1/price-1)*100; t2pct=(t2/price-1)*100 if t2>price else 0; repeat_width=(t1/support-1)*100 if t1>support>0 else 0
    item.update(continuous_rise=trend_score>=7 and ret15>0 and ret30>0,continuous_rise_score=trend_score,continuous_rise_checks=checks,trend_return_5m=ret5,trend_return_15m=ret15,trend_return_30m=ret30,up_down_volume_ratio=volume_dom,structural_entry=price,structural_support=support,stop_loss=support,structural_target=t1,structural_target1=t1,structural_target2=t2,target1_upside_percent=t1pct,target2_upside_percent=t2pct,risk_reward=rr1,risk_reward_target1=rr1,risk_reward_target2=rr2,level_plan_valid=risk>0 and reward1>0,target_basis=b1,target1_basis=b1,target2_basis=b2,stop_basis=f"{support_reason} 이탈 시 상승 시나리오 무효",level_plan_reason=f"1차 {fmt(t1)} ({t1pct:+.2f}%) / 2차 {fmt(t2) if t2 else '-'} / 지지 {fmt(support)}",chart_resistance_levels=resistances,chart_box_high=box_high,chart_box_low=box_low,chart_box_width=box_width,breakout_active=breakout,repeat_scalp_range_percent=repeat_width,repeat_scalp_preferred_range=0.50<=repeat_width<=1.50)
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


def multi_timeframe_plan(item:dict,market:str):
    price=float(item.get("price",0) or 0); previous=float(item.get("previous_close",item.get("prev_close",0)) or 0); day_open=float(item.get("open",item.get("day_open",0)) or 0)
    day_change=(price/previous-1)*100 if price>0 and previous>0 else float(item.get("change_percent",item.get("change",0)) or 0)
    daily_bullish=day_change>=-0.30 and (day_open<=0 or price>=day_open*0.995); daily_bearish=day_change<=-1 and day_open>0 and price<day_open
    results={}
    for m in (5,15,60):
        bars=_aggregate_ohlcv(item,m,market); c=[r["close"] for r in bars]; h=[r["high"] for r in bars]; l=[r["low"] for r in bars]; v=[r["volume"] for r in bars]
        avail=len(bars)>=2; ret=(c[-1]/c[-2]-1)*100 if avail and c[-2]>0 else 0; ema=pd.Series(c).ewm(span=min(5,max(2,len(c))),adjust=False).mean() if c else pd.Series(dtype=float); ema_up=len(ema)>=2 and float(ema.iloc[-1])>=float(ema.iloc[-2]); lower=len(bars)>=3 and h[-1]<h[-2] and l[-1]<l[-2]; prior_vol=float(pd.Series(v[:-1]).median()) if len(v)>=2 else 0; vok=not v or prior_vol<=0 or v[-1]>=prior_vol*0.7; bull=avail and ret>=0 and ema_up and not lower and vok; bear=avail and ret<0 and (not ema_up or lower)
        results[m]={"available":avail,"bars":len(bars),"return":ret,"bullish":bool(bull),"bearish":bool(bear)}
    five,fifteen,hourly=results[5],results[15],results[60]; hourly_allows=hourly["bullish"] if hourly["available"] else (daily_bullish and fifteen["bullish"]); alignment=daily_bullish and hourly_allows and fifteen["bullish"] and five["bullish"]
    mtf_exit=(fifteen["bearish"] and (hourly["bearish"] or daily_bearish)) or (five["bearish"] and fifteen["bearish"] and price<float(item.get("vwap",0) or 0)); higher=daily_bullish and fifteen["bullish"] and (hourly["bullish"] or not hourly["available"])
    def status(d): return "자료 형성 중" if not d["available"] else "상승 · 허용" if d["bullish"] else "하락 · 차단" if d["bearish"] else "중립 · 대기"
    item.update(mtf_alignment=bool(alignment),mtf_exit=bool(mtf_exit),mtf_higher_trend=bool(higher),mtf_short_pullback=bool(higher and five["bearish"]),mtf_checks={"일봉·당일 큰 방향":daily_bullish,"60분봉 방향 허용":hourly_allows,"15분봉 상승":fifteen["bullish"],"5분봉 상승":five["bullish"]},mtf_status={"60분봉":status(hourly),"15분봉":status(fifteen),"5분봉":status(five)},mtf_detail={str(k):v for k,v in results.items()},daily_direction_change=day_change)
    return item


def upside_continuation_plan(item:dict):
    price=float(item.get("price",0) or 0); t1=float(item.get("structural_target1",0) or 0); t2=float(item.get("structural_target2",0) or 0); vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); ema20=float(item.get("ema20",0) or 0); macd=float(item.get("macd_histogram",0) or 0); vol=float(item.get("up_down_volume_ratio",0) or 0); ret15=float(item.get("trend_return_15m",0) or 0); ret30=float(item.get("trend_return_30m",0) or 0); trend=int(item.get("continuous_rise_score",0) or 0); f20=float(item.get("forecast_20m",0) or 0); f30=float(item.get("forecast_30m",0) or 0)
    checks={"차트상 2차 목표 존재":t2>t1>price,"VWAP 위 유지":price>vwap>0,"EMA 정배열":ema9>ema20>0,"15분 실제 상승":ret15>0,"30분 실제 상승":ret30>0,"상승봉 거래량 우세":vol>=1.05,"MACD 비약세":macd>=0,"지속상승 7점 이상":trend>=7,"20분 예측 약세 아님":f20>-0.35,"30분 예측 약세 아님":f30>-0.35}
    score=sum(map(bool,checks.values())); extra=(t2/t1-1)*100 if t2>t1>0 else 0; total=(t2/price-1)*100 if t2>price>0 else 0
    if t2<=t1: state,label,reason="NO_TARGET2","⚪ 2차 차트 목표 미확인","1차 위 신뢰할 목표 없음"
    elif score>=8: state,label,reason="STRONG","🟢 1차 돌파 후 추가상승 가능",f"근거 {score}/10"
    elif score>=6: state,label,reason="WATCH","🟡 1차 도달 후 추세 확인",f"근거 {score}/10"
    else: state,label,reason="LIMITED","🔴 1차 목표 부근 상승 제한 가능",f"근거 {score}/10"
    item.update(upside_continuation_state=state,upside_continuation_label=label,upside_continuation_score=score,upside_continuation_checks=checks,additional_upside_after_target1=extra,target2_total_upside=total,repeat_scalp_can_extend=state=="STRONG",repeat_scalp_extension_label=label,repeat_scalp_extension_reason=reason,repeat_scalp_extension_percent=extra)
    return item


def repeat_scalp_plan(item:dict):
    price=float(item.get("price",0) or 0); support=float(item.get("structural_support",0) or 0); target=float(item.get("structural_target1",item.get("structural_target",0)) or 0); vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); ema20=float(item.get("ema20",0) or 0)
    closes=[float(x) for x in (item.get("chart_close_1m",[]) or []) if float(x or 0)>0]; highs=[float(x) for x in (item.get("chart_high_1m",[]) or []) if float(x or 0)>0]; lows=[float(x) for x in (item.get("chart_low_1m",[]) or []) if float(x or 0)>0]; volumes=[float(x or 0) for x in (item.get("chart_volume_1m",[]) or [])]
    if not item.get("level_plan_valid") or min(price,support,target)<=0 or len(closes)<12: item.update(repeat_scalp_state="UNAVAILABLE",repeat_scalp_label="⚪ 반복단타 판정 대기",repeat_scalp_reason="실제 지지·저항 확인 대기"); return item
    ranges=[max(0,highs[i]-lows[i]) for i in range(max(0,len(highs)-20),len(highs))]; median_range=float(pd.Series(ranges).median()) if ranges else 0; median_vol=float(pd.Series(volumes[-20:]).median()) if volumes else 0; last_vol=volumes[-1] if volumes else 0
    trend=int(item.get("continuous_rise_score",0) or 0); ret15=float(item.get("trend_return_15m",0) or 0); width=(target/support-1)*100 if target>support>0 else 0; mtf=bool(item.get("mtf_alignment")); mtf_exit=bool(item.get("mtf_exit")); rsi=float(item.get("rsi",50) or 50); prior_rsi=float(item.get("rsi_previous",rsi) or rsi)
    box_high=max(highs[-30:]) if len(highs)>=30 else 0; box_low=min(lows[-30:]) if len(lows)>=30 else 0; box_range=(box_high/box_low-1)*100 if box_high>box_low>0 else 0; box_valid=0.5<=box_range<=4; lower_zone=box_low>0 and price<=box_low+(box_high-box_low)*0.35; upper_zone=box_high>0 and price>=box_low+(box_high-box_low)*0.75; rsi_recovery=(prior_rsi<=35 and rsi>prior_rsi) or 40<=rsi<=68; trend_intact=mtf and price>=vwap>0 and ema9>=ema20>0 and trend>=6 and ret15>=0; near_support=support<=price<=support+max(median_range,1e-9); near_target=target>=price and target-price<=max(median_range,1e-9); bounce=len(closes)>=2 and closes[-1]>closes[-2] and lows[-1]<=support+max(median_range,1e-9); volume_returns=median_vol<=0 or last_vol>=median_vol
    recent_high=max(highs[-6:]); prior_high=max(highs[-12:-6]); recent_low=min(lows[-6:]); prior_low=min(lows[-12:-6]); lower_structure=recent_high<prior_high and recent_low<prior_low; vwap_break=len(closes)>=3 and vwap>0 and all(x<vwap for x in closes[-3:]); ema_bear=ema9<ema20 and ema20>0; macd_bear=float(item.get("macd_histogram",0) or 0)<0
    down=up=0
    for i in range(max(1,len(closes)-12),len(closes)):
        vol=volumes[i] if i<len(volumes) else 0
        if closes[i]<closes[i-1]: down+=vol
        elif closes[i]>closes[i-1]: up+=vol
    reversal_checks={"VWAP 아래 3개 봉":vwap_break,"EMA9·EMA20 하락 정렬":ema_bear,"고점·저점 동시 하락":lower_structure,"MACD 음전환":macd_bear,"하락봉 거래량 우세":down>up*1.15}; reversal=sum(map(bool,reversal_checks.values())); breakdown=price<support or reversal>=3 or mtf_exit
    if breakdown: state,label,reason="EXIT","🔴 추세 꺾임·매도",f"하락 전환 {reversal}/5"
    elif width<0.5: state,label,reason="RANGE_TOO_NARROW",f"⚪ 반복폭 부족 +{width:.2f}%","0.5% 미만"
    elif width>1.5: state,label,reason="RANGE_TOO_WIDE",f"🔵 반복폭 넓음 +{width:.2f}%","상승여력은 있으나 기본 반복후보 범위 밖"
    elif price>=target or near_target or upper_zone: state,label,reason="TAKE_PROFIT","🟠 1차 목표 접근·분할매도",f"실제 차트 저항 {fmt(target)}"
    elif trend_intact and box_valid and (near_support or lower_zone) and bounce and volume_returns and rsi_recovery: state,label,reason="BUY_PULLBACK","🟢 눌림 반등 매수",f"지지 {fmt(support)} 반등"
    elif trend_intact and box_valid and price>ema9 and volume_returns: state,label,reason="HOLD_OR_BREAKOUT","🟢 보유·돌파 매수 검토",f"1차 {fmt(target)}까지 공간"
    elif trend_intact: state,label,reason="WAIT_PULLBACK","🟡 눌림목 재매수 대기",f"지지 {fmt(support)} 대기"
    else: state,label,reason="WAIT_TREND","🔵 추세 재확인 대기","상위시간대 정렬 대기"
    item.update(repeat_scalp_state=state,repeat_scalp_label=label,repeat_scalp_reason=reason,repeat_scalp_buy_level=support,repeat_scalp_sell_level=target,repeat_scalp_invalidation=support,repeat_scalp_median_bar_range=median_range,repeat_scalp_reversal_score=reversal,repeat_scalp_reversal_checks=reversal_checks,repeat_scalp_range_percent=width,repeat_scalp_preferred_range=0.5<=width<=1.5,repeat_box_valid=box_valid,repeat_box_low=box_low,repeat_box_high=box_high,repeat_box_range_percent=box_range,repeat_rsi_recovery=rsi_recovery,trailing_stop_enabled=state in {"HOLD_OR_BREAKOUT","TAKE_PROFIT"},trailing_stop_percent=0.5,trailing_stop_price=max(highs[-10:])*0.995 if highs else 0)
    return item


def strategy_consensus(item:dict):
    price=float(item.get("price",0) or 0); vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); ema20=float(item.get("ema20",0) or 0); rsi=float(item.get("rsi",50) or 50); macd=float(item.get("macd_histogram",0) or 0); stoch=float(item.get("stochastic_k",50) or 50); rvol=float(item.get("rvol",0) or 0)
    f5=float(item.get("forecast_5m",0) or 0); f10=float(item.get("forecast_10m",0) or 0); f20=float(item.get("forecast_20m",0) or 0); f30=float(item.get("forecast_30m",0) or 0); orderbook=str(item.get("orderbook_signal","")); obv=str(item.get("obv_trend","")); patterns=" ".join(map(str,item.get("pattern_signals",[]) or [])); trend_text=" ".join(str(item.get(k,"")) for k in ("trend_5m","trend_15m","trend_30m"))
    votes=[]
    def add(name,buy,sell,reason): votes.append({"기법":name,"판정":"매수" if buy and not sell else "매도" if sell and not buy else "대기","근거":reason})
    add("VWAP 추세",price>vwap>0,0<price<vwap,f"VWAP {fmt(vwap)}")
    add("EMA 추세",ema9>ema20>0 and price>=ema9,0<ema9<ema20 and price<=ema9,f"EMA9 {fmt(ema9)} / EMA20 {fmt(ema20)}")
    add("MACD 모멘텀",macd>0,macd<0,f"히스토그램 {macd:+.4f}")
    add("RSI·스토캐스틱",42<=rsi<=68 and stoch<80,rsi>=75 or stoch>=90,f"RSI {rsi:.1f} / %K {stoch:.1f}")
    add("거래량·RVOL",rvol>=1.5 and f5>=0.35,rvol<0.7 or (rvol>=1.5 and f5<=-0.35),f"RVOL {rvol:.1f}배")
    add("OBV 수급","상승" in obv,"하락" in obv,obv or "미확인")
    add("호가·체결","매수" in orderbook,"매도" in orderbook,orderbook or "미확인")
    add("캔들 패턴",any(x in patterns for x in ("상승","망치","돌파","장악")),any(x in patterns for x in ("하락","유성","윗꼬리")),patterns or "뚜렷한 패턴 없음")
    forecasts=(f5,f10,f20,f30); add("다중 시간봉",all(x>=0.35 for x in forecasts),any(x<=-0.35 for x in forecasts),f"5 {f5:+.2f}% / 10 {f10:+.2f}% / 20 {f20:+.2f}% / 30 {f30:+.2f}%")
    add("추세 정렬",trend_text.count("상승")>=2,trend_text.count("하락")>=2,trend_text or "추세 계산 중")
    buys=sum(r["판정"]=="매수" for r in votes); sells=sum(r["판정"]=="매도" for r in votes); return votes,buys,sells,len(votes)-buys-sells


def market_regime(item:dict):
    price=float(item.get("price",0) or 0); vwap=float(item.get("vwap",0) or 0); ema9=float(item.get("ema9",0) or 0); ema20=float(item.get("ema20",0) or 0); rvol=float(item.get("rvol",0) or 0)
    if rvol>=3: return "변동성 급증장","돌파·호가·거래량 기법 우선"
    if price>vwap>0 and ema9>ema20>0: return "상승 추세장","눌림목·VWAP 지지·추세추종 우선"
    if 0<price<vwap and 0<ema9<ema20: return "하락 추세장","신규 매수 금지·반등 확인 우선"
    return "횡보장","VWAP 평균회귀·박스 돌파 확인 우선"


def weighted_strategy_score(rows,regime):
    weights={r["기법"]:1.0 for r in rows}
    names=("VWAP 추세","EMA 추세","MACD 모멘텀","다중 시간봉","추세 정렬") if regime=="상승 추세장" else ("VWAP 추세","RSI·스토캐스틱","캔들 패턴","호가·체결") if regime=="횡보장" else ("거래량·RVOL","호가·체결","VWAP 추세","다중 시간봉") if regime=="변동성 급증장" else ("VWAP 추세","EMA 추세","다중 시간봉","추세 정렬")
    mult=1.6 if regime in {"상승 추세장","횡보장"} else 1.8 if regime=="변동성 급증장" else 2.0
    for n in names: weights[n]=mult
    total=sum(weights.values()) or 1; signed=sum(weights[r["기법"]]*(1 if r["판정"]=="매수" else -1 if r["판정"]=="매도" else 0) for r in rows); buy=sum(weights[r["기법"]] for r in rows if r["판정"]=="매수")
    return round(signed/total*100,1),round(buy/total*100,1)


@st.cache_data(ttl=20,show_spinner=False)
def benchmark_context(market:str,ticker:str):
    try:
        if market=="미국":
            mapping={"SOXL":("SOXX","NASDAQ",1),"SOXS":("SOXX","NASDAQ",-1),"SMH":("SOXX","NASDAQ",1),"TQQQ":("QQQ","NASDAQ",1),"SQQQ":("QQQ","NASDAQ",-1),"QLD":("QQQ","NASDAQ",1),"QID":("QQQ","AMEX",-1),"FAS":("XLF","AMEX",1),"FAZ":("XLF","AMEX",-1),"LABU":("XBI","AMEX",1),"LABD":("XBI","AMEX",-1),"TNA":("IWM","AMEX",1),"TZA":("IWM","AMEX",-1),"XOM":("XLE","AMEX",1),"JPM":("XLF","AMEX",1),"BAC":("XLF","AMEX",1)}
            bench,exchange,direction=mapping.get(ticker,("SPY","AMEX",1)); q=scanner().client.us_quote(bench,exchange); _,_,change,_=verified_us_change(q); bars=scanner().client.us_intraday(bench,exchange,minutes=1); intraday=((float(bars["close"].iloc[-1])/float(bars["close"].iloc[-6])-1)*100*direction) if len(bars)>=6 else 0
            return {"name":bench,"change":change*direction,"intraday":intraday,"confirmed":len(bars)>=20}
        members={"488080":[("005930",0.5),("000660",0.5)],"396500":[("005930",0.5),("000660",0.5)]}.get(ticker,[("069500",1.0)]); change=intraday=0; enough=True
        for code,w in members:
            change+=float(scanner().client.kr_quote(code).get("change",0) or 0)*w; bars=scanner().client.kr_intraday(code); enough=enough and len(bars)>=20
            if len(bars)>=6: intraday+=(float(bars["close"].iloc[-1])/float(bars["close"].iloc[-6])-1)*100*w
        return {"name":"+".join(c for c,_ in members),"change":change,"intraday":intraday,"confirmed":enough}
    except Exception as e: return {"name":"시장지표","change":0.0,"intraday":0.0,"confirmed":False,"error":type(e).__name__}


@st.cache_data(ttl=60,show_spinner=False)
def live_filtered_universe(market:str):
    source=KR_UNIVERSE if market=="국내" else US_UNIVERSE; accepted=[]; ranked={}; modes=("국내 돌파","국내 거래대금 급증") if market=="국내" else ("미국 30분 1% 타점","미국 급등주")
    for mode in modes:
        try:
            for row in scanner().candidates(mode):
                c=dict(row); ticker=str(c.get("ticker") or c.get("code") or "").upper().strip()
                if not ticker: continue
                old=ranked.get(ticker,{}); merged={**old,**c}; merged["screen_price"]=float(c.get("screen_price") or old.get("screen_price") or c.get("price") or 0); merged["screen_change"]=float(c.get("screen_change") if c.get("screen_change") is not None else old.get("screen_change",c.get("change_percent",c.get("change",0))) or 0); merged["screen_volume"]=max(int(float(old.get("screen_volume",0) or 0)),int(float(c.get("screen_volume",c.get("volume",0)) or 0))); merged["ticker"]=ticker; ranked[ticker]=merged
        except Exception: pass
    for c in ranked.values():
        price=float(c.get("screen_price",0) or 0); change=float(c.get("screen_change",0) or 0); volume=int(c.get("screen_volume",0) or 0); value=price*volume
        valid=(2000<=price<=300000 and 0.3<=change<15 and volume>=100000 and value>=30_000_000_000) if market=="국내" else (3<=price<=200 and 0.2<=change<12 and volume>=100000 and value>=30_000_000)
        if valid: accepted.append(c)
    fallback=[] if accepted else source[:12]
    def fetch(row):
        try:
            q=scanner().client.kr_quote(row["ticker"]) if market=="국내" else scanner().client.us_quote(row["ticker"],row["exchange"]); c=dict(row); c["screen_price"]=float(q.get("price",0) or 0); c["screen_volume"]=int(float(q.get("volume",q.get("accumulated_volume",0)) or 0)); c["screen_change"]=float(q.get("change",0) or 0) if market=="국내" else verified_us_change(q)[2]; return c if c["screen_price"]>0 else None
        except Exception: return None
    if fallback:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for fut in as_completed([pool.submit(fetch,r) for r in fallback]):
                c=fut.result();
                if c: accepted.append(c)
    dedup={r["ticker"]:r for r in accepted}; return sorted(dedup.values(),key=lambda r:float(r.get("screen_price",0) or 0)*int(r.get("screen_volume",0) or 0),reverse=True)[:12]


@st.cache_data(ttl=5,show_spinner=False)
def latest_entry_candidates(market:str,minimum_score:float,limit:int=5):
    if AUDIT_IMPORT_ERROR: return []
    code="KR" if market=="국내" else "US"; cutoff=int(time.time())-15*60
    try:
        with audit_connect() as db:
            rows=db.execute("""SELECT s.ticker,s.name,s.issued,s.base_price,s.verdict,s.score,s.entry_ok,s.data_valid,s.forecast5,s.forecast10,s.forecast20,s.forecast30,s.detail_json FROM signals s JOIN(SELECT ticker,MAX(issued) issued FROM signals WHERE market=? AND issued>=? GROUP BY ticker) x ON x.ticker=s.ticker AND x.issued=s.issued WHERE s.market=?""",(code,cutoff,code)).fetchall()
    except Exception: return []
    out=[]
    for ticker,name,issued,price,verdict,score,entry_ok,data_valid,f5,f10,f20,f30,detail_json in rows:
        try: d=json.loads(detail_json or "{}")
        except Exception: d={}
        score=float(score or 0)
        if score<minimum_score: continue
        width=float(d.get("repeat_scalp_range_percent",0) or 0); rr=float(d.get("risk_reward",0) or 0); spread=d.get("verified_spread_percent")
        try: spread=float(spread) if spread is not None else None
        except Exception: spread=None
        if not (data_valid and d.get("level_plan_valid") and rr>=1.5 and 0.5<=width<=1.5 and spread is not None and spread<=(0.35 if code=="KR" else 0.25)): continue
        state=str(d.get("repeat_scalp_state","UNAVAILABLE"))
        if state in {"UNAVAILABLE","EXIT","TAKE_PROFIT","RANGE_TOO_NARROW","RANGE_TOO_WIDE"}: continue
        stage,priority=("🟢 눌림 반등 매수",4) if state=="BUY_PULLBACK" else ("🟢 돌파 매수 검토",3) if state=="HOLD_OR_BREAKOUT" else ("🟡 눌림목 재매수 대기",2) if state=="WAIT_PULLBACK" else ("🔵 추세 재확인 대기",1)
        t1=float(d.get("structural_target1",d.get("structural_target",0)) or 0); t2=float(d.get("structural_target2",0) or 0); ext=str(d.get("upside_continuation_label",d.get("repeat_scalp_extension_label","⚪ 추가상승 미확인"))); extpct=float(d.get("additional_upside_after_target1",d.get("repeat_scalp_extension_percent",0)) or 0); trend=int(d.get("continuous_rise_score",0) or 0); rvol=float(d.get("rvol",0) or 0); positive=sum(float(x or 0)>=0.35 for x in (f5,f10,f20,f30)); rank=priority*100+trend*12+score+positive*5+min(rvol,5)*2+min(rr,4)*2+(25 if d.get("upside_continuation_state")=="STRONG" else 0)
        out.append({"ticker":ticker,"name":name or ticker,"stage":stage,"price":float(price or 0),"score":score,"rvol":rvol,"risk_reward":rr,"issued":int(issued),"rank":rank,"trend_score":trend,"repeat_width":width,"support":float(d.get("structural_support",0) or 0),"target1":t1,"target2":t2,"extension_label":ext,"extension_percent":extpct})
    return sorted(out,key=lambda x:x["rank"],reverse=True)[:limit]


def precise_analysis(row:dict,mode:str):
    raw=scanner().analyze(dict(row),mode); item=apply_mode_policy(finalize_trade_item(raw),mode); market="국내" if mode.startswith("국내") else "미국"
    for _ in range(2):
        if float(item.get("best_bid",0) or 0)>0 and float(item.get("best_ask",0) or 0)>0: break
        try:
            refreshed=scanner().refresh_quotes([item],mode)
            if refreshed: item.update(refreshed[0])
        except Exception: pass
        time.sleep(0.2)
    if market=="미국": item=normalize_us_item(item,row)
    item=structural_trade_plan(item,market); item=multi_timeframe_plan(item,market); item=upside_continuation_plan(item); item=repeat_scalp_plan(item); _,gate,spread=data_quality_gate(item,market); item["data_gate_passed"]=bool(gate); item["verified_spread_percent"]=spread; return item


def background_audit_tick(enabled:bool,now_ts:float,ui_market:str):
    code="KR" if ui_market=="국내" else "US"; base=AUDIT_KR_UNIVERSE if code=="KR" else AUDIT_US_UNIVERSE
    if not enabled or AUDIT_IMPORT_ERROR or not base: return
    now_dt=datetime.fromtimestamp(now_ts,KST)
    if not audit_market_is_open(code,now_dt): return
    last_key=f"audit_last_tick::{code}"; idx_key=f"audit_member_index::{code}"
    if now_ts-float(st.session_state.get(last_key,0))<60: return
    dynamic=live_filtered_universe(ui_market); members=[(str(r.get("ticker")),str(r.get("name") or r.get("ticker")),str(r.get("exchange") or "KR")) for r in dynamic if r.get("ticker")] or base; idx=int(st.session_state.get(idx_key,0))%len(members); ticker,name,exchange=members[idx]; row={"ticker":ticker,"name":name,"exchange":exchange,"asset_type":"검증대상"}
    try:
        with audit_connect() as db:
            if audit_signal_window_open(code,now_dt): item=precise_analysis(row,"국내 30분 1% 타점" if code=="KR" else "미국 30분 1% 타점"); audit_store_result(db,code,item,int(now_ts),60)
            else:
                q=scanner().client.kr_quote(ticker) if code=="KR" else scanner().client.us_quote(ticker,exchange); audit_store_quote(db,code,ticker,float(q.get("price",0) or 0),int(now_ts))
            audit_grade_pending(db,int(now_ts)); db.commit(); audit_export_summary(db)
        st.session_state["audit_last_ok"]=f"{code} {ticker} · {now_dt.strftime('%H:%M:%S')}"; st.session_state.pop("audit_last_error",None)
    except Exception as e: st.session_state["audit_last_error"]=f"{ticker}: {type(e).__name__} · {e}"
    finally: st.session_state[last_key]=now_ts; st.session_state[idx_key]=idx+1


def render_chart(item:dict):
    times=item.get("chart_time_1m",[]) or []; closes=item.get("chart_close_1m",[]) or []; opens=item.get("chart_open_1m",[]) or []; highs=item.get("chart_high_1m",[]) or []; lows=item.get("chart_low_1m",[]) or []; vw=item.get("chart_vwap_1m",[]) or []; e9=item.get("chart_ema9_1m",[]) or []; e20=item.get("chart_ema20_1m",[]) or []; sig=item.get("chart_signal_1m",[]) or []
    n=min(map(len,(times,closes,opens,highs,lows,vw,e9,e20,sig))) if times else 0
    if n<=0: st.info("1분봉 수집 중"); return
    df=pd.DataFrame({"시간":pd.to_datetime(times[:n],errors="coerce"),"시가":opens[:n],"고가":highs[:n],"저가":lows[:n],"종가":closes[:n],"VWAP":vw[:n],"EMA9":e9[:n],"EMA20":e20[:n],"신호":sig[:n]}).dropna(subset=["시간"]); df["색상"]=df.apply(lambda r:"상승" if r["종가"]>=r["시가"] else "하락",axis=1); color=alt.Scale(domain=["상승","하락"],range=["#ef5350","#2962ff"]); base=alt.Chart(df).encode(x=alt.X("시간:T",title=None)); wick=base.mark_rule().encode(y=alt.Y("저가:Q",scale=alt.Scale(zero=False)),y2="고가:Q",color=alt.Color("색상:N",scale=color,legend=None)); body=base.mark_bar(size=7).encode(y="시가:Q",y2="종가:Q",color=alt.Color("색상:N",scale=color,legend=None)); lines=alt.Chart(df).transform_fold(["VWAP","EMA9","EMA20"],as_=["지표","값"]).mark_line().encode(x="시간:T",y=alt.Y("값:Q",scale=alt.Scale(zero=False)),color="지표:N"); chart=wick+body+lines
    levels=[]; support=float(item.get("structural_support",0) or 0); t1=float(item.get("structural_target1",item.get("structural_target",0)) or 0); t2=float(item.get("structural_target2",0) or 0)
    if support>0: levels.append({"가격":support,"구간":"지지·손절"})
    if t1>0: levels.append({"가격":t1,"구간":"1차 목표"})
    if t2>0: levels.append({"가격":t2,"구간":"2차 목표"})
    if levels:
        lf=pd.DataFrame(levels); chart=chart+alt.Chart(lf).mark_rule(strokeWidth=2,strokeDash=[6,4]).encode(y=alt.Y("가격:Q",scale=alt.Scale(zero=False)),color=alt.Color("구간:N",title=None))+alt.Chart(lf).mark_text(align="left",dx=5,dy=-5,fontWeight="bold").encode(y="가격:Q",text="구간:N",color=alt.Color("구간:N",legend=None))
    st.altair_chart(chart.properties(height=430),use_container_width=True)


def calibration_stats(ticker:str):
    stats={}
    with db_connect() as db:
        for m in (5,10,20,30):
            rows=db.execute(f"SELECT f{m},actual{m} FROM predictions WHERE ticker=? AND actual{m} IS NOT NULL ORDER BY issued DESC LIMIT 300",(ticker,)).fetchall()
            if not rows: stats[m]={"samples":0,"accuracy":0,"bias":0,"mae":0}; continue
            errors=[float(e)-float(a) for e,a in rows]; accuracy=sum((float(e)>=0)==(float(a)>=0) for e,a in rows)/len(rows)*100; stats[m]={"samples":len(rows),"accuracy":accuracy,"bias":sum(errors)/len(errors),"mae":sum(abs(x) for x in errors)/len(errors)}
    return stats


def update_prediction_audit(ticker,price,item,now_ts):
    bucket=float(int(now_ts//300)*300)
    with db_connect() as db:
        if price>0: db.execute("INSERT OR IGNORE INTO predictions(ticker,issued,base_price,f5,f10,f20,f30) VALUES(?,?,?,?,?,?,?)",(ticker,bucket,price,float(item.get("forecast_5m",0) or 0),float(item.get("forecast_10m",0) or 0),float(item.get("forecast_20m",0) or 0),float(item.get("forecast_30m",0) or 0)))
    return []

st.title("⚡ 초단타 VWAP 매수타점")
st.caption("반복단타 후보는 실제 차트의 지지→1차 저항 폭 0.5~1.5%만 기본 표시합니다. 1차·2차 목표는 실제 차트 저항으로 계산합니다.")
with st.sidebar:
    market=st.radio("시장",["국내","미국"],horizontal=True); session_info=market_clock(market); st.caption(f"{session_info['session']} · {session_info['local_time']}"); mode="국내 30분 1% 타점" if market=="국내" else "미국 30분 1% 타점"; minimum_score=st.slider("최소 점수",30,90,50,5); manual_ticker=st.text_input("종목명 또는 종목코드 검색",placeholder="현대차, 005380, SOXL").strip(); run_mode=st.radio("실행 모드",["가벼운 현재가","선택 종목 집중","시장 자동검증"],key="scalp_run_mode"); focus_only=run_mode=="선택 종목 집중"; auto_audit=run_mode=="시장 자동검증"; require_validation=st.toggle("실전 검증 잠금",True); st.caption("반복단타 기본폭 0.5~1.5%")
now=time.time(); live_refresh_active=focus_only or auto_audit; st_autorefresh(interval=1500 if focus_only else 10000 if auto_audit else 8000,key="scalp_tick")
if auto_audit and not AUDIT_IMPORT_ERROR and not manual_ticker: background_audit_tick(True,now,market)

candidate_board=[] if manual_ticker else latest_entry_candidates(market,minimum_score)
st.subheader("실시간 반복단타 후보")
if candidate_board:
    st.dataframe(pd.DataFrame([{"판정":c["stage"],"종목":f"{c['ticker']} · {c['name']}","현재가":c["price"],"반복 매수":c["support"],"반복 매도/1차":c["target1"],"반복폭":f"{c['repeat_width']:.2f}%","2차 목표":c["target2"] if c["target2"]>0 else None,"추가상승":c["extension_label"],"1차→2차":f"+{c['extension_percent']:.2f}%" if c["extension_percent"]>0 else "-","점수":round(c["score"]),"RVOL":round(c["rvol"],1),"손익비":round(c["risk_reward"],2)} for c in candidate_board]),hide_index=True,use_container_width=True)
else: st.info("현재 0.5~1.5% 반복폭과 필수 조건을 동시에 통과한 후보가 없습니다.")

options=[]
if manual_ticker:
    resolved=resolve_manual(manual_ticker,market)
    if resolved: options=[resolved]
else: options=live_filtered_universe(market)
if not options: st.warning("자동 후보가 없습니다. 종목을 직접 검색해 주세요."); st.stop()
selected_ticker=st.selectbox("집중 분석할 종목",[str(r.get("ticker","")) for r in options],format_func=lambda t:next((f"{t} · {r.get('name',t)}" for r in options if str(r.get('ticker',''))==t),t)); selected_row=next(r for r in options if str(r.get("ticker",""))==selected_ticker); selected_row.setdefault("exchange","KR" if market=="국내" else "NASDAQ")
if st.session_state.get("scalp_selected")!=selected_ticker: st.session_state["scalp_selected"]=selected_ticker; st.session_state["scalp_last_precise"]=0.0; st.session_state.pop("scalp_latest",None)
latest=dict(st.session_state.get("scalp_latest",{})); due=not latest or (live_refresh_active and now-float(st.session_state.get("scalp_last_precise",0))>=(5 if focus_only else 60))
if due:
    try: latest=precise_analysis(selected_row,mode); st.session_state["scalp_latest"]=latest; st.session_state["scalp_last_precise"]=now
    except Exception as e: st.error(f"분석 대기: {e}"); st.stop()
price=float(latest.get("price",0) or 0); change=float(latest.get("change_percent",0) or 0)
if price<=0: st.error("현재가 미확인"); st.stop()
quality_rows,quality_passed,_=data_quality_gate(latest,market); regime_name,regime_method=market_regime(latest); strategy_rows,buy_votes,sell_votes,wait_votes=strategy_consensus(latest); weighted_score,weighted_buy=weighted_strategy_score(strategy_rows,regime_name); context=benchmark_context(market,selected_ticker); context_aligned=bool(context.get("confirmed")) and (float(context.get("change",0) or 0)>=-0.05 and float(context.get("intraday",0) or 0)>=-0.10); rr=float(latest.get("risk_reward",0) or 0); state=str(latest.get("repeat_scalp_state","UNAVAILABLE")); width=float(latest.get("repeat_scalp_range_percent",0) or 0); support=float(latest.get("structural_support",0) or 0); t1=float(latest.get("structural_target1",latest.get("structural_target",0)) or 0); t2=float(latest.get("structural_target2",0) or 0)
cal=calibration_stats(selected_ticker) if require_validation else {m:{"samples":0,"accuracy":0,"bias":0,"mae":0} for m in (5,10,20,30)}; validated=cal[5]["samples"]>=20 and cal[10]["samples"]>=20 and cal[5]["accuracy"]>=55 and cal[10]["accuracy"]>=55
level="success"
if not quality_passed or not latest.get("level_plan_valid") or not context_aligned or rr<1.5 or state in {"EXIT","TAKE_PROFIT","RANGE_TOO_NARROW","RANGE_TOO_WIDE"} or sell_votes>=3: level="error"
elif require_validation and not validated: level="warning"
elif state not in {"BUY_PULLBACK","HOLD_OR_BREAKOUT"} or buy_votes<4: level="warning"
latest["entry_checks_passed"]=level=="success"
if level=="success": st.success(f"🟢 매수 검토 · 반복폭 {width:.2f}% · 1차 {fmt(t1)}")
elif state=="TAKE_PROFIT": st.warning(f"🟠 1차 목표 접근 · {fmt(t1)}")
else: st.info(f"🟡 대기 · 반복폭 {width:.2f}%")

cols=st.columns(5); cols[0].metric(f"{selected_ticker} · {latest.get('name','')}",fmt(price),f"{change:+.2f}%"); cols[1].metric("진입 기준가",fmt(latest.get("structural_entry"))); cols[2].metric("1차 목표가",fmt(t1),f"{float(latest.get('target1_upside_percent',0) or 0):+.2f}%"); cols[3].metric("2차 목표가",fmt(t2) if t2>0 else "-",f"{float(latest.get('target2_upside_percent',0) or 0):+.2f}%" if t2>0 else None); cols[4].metric("손절·지지",fmt(support))
ext_state=str(latest.get("upside_continuation_state","NO_TARGET2")); ext_label=str(latest.get("upside_continuation_label","⚪ 추가상승 미확인")); ext_pct=float(latest.get("additional_upside_after_target1",0) or 0); ext_score=int(latest.get("upside_continuation_score",0) or 0)
if ext_state=="STRONG": st.success(f"{ext_label} · 근거 {ext_score}/10 · 1차→2차 +{ext_pct:.2f}%")
elif ext_state=="WATCH": st.warning(f"{ext_label} · 근거 {ext_score}/10")
elif ext_state=="LIMITED": st.error(f"{ext_label} · 근거 {ext_score}/10")
else: st.info(ext_label)
st.caption(f"1차 근거: {latest.get('target1_basis','-')} · 2차 근거: {latest.get('target2_basis','-')}")

st.subheader("실시간 차트 · 지지 / 1차 / 2차 목표")
render_chart(latest)
with st.expander("추가상승 판정 근거"):
    st.dataframe(pd.DataFrame([{"조건":k,"충족":"✅" if v else "❌"} for k,v in (latest.get("upside_continuation_checks",{}) or {}).items()]),hide_index=True,use_container_width=True)
with st.expander("매수·매도 기법별 판정"):
    st.dataframe(pd.DataFrame(strategy_rows),hide_index=True,use_container_width=True)
with st.expander("실시간 데이터 검문",expanded=not quality_passed):
    st.dataframe(pd.DataFrame(quality_rows),hide_index=True,use_container_width=True)
with st.expander("진입 전 뉴스·공시 위험 확인"):
    if st.button("뉴스·SEC·거래정지 확인",use_container_width=True):
        try: st.session_state["scalp_risk_check"]=scanner().analyze_candidate(selected_row,mode)
        except Exception as e: st.error(str(e))
    checked=st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker"))==selected_ticker: st.write(checked.get("news_summary","뉴스 확인 완료")); st.write("거래정지:",checked.get("halt_active",False))

st.caption(f"화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
