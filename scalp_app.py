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


def verdict_text(item: dict) -> tuple[str, str]:
    """chart_verdict를 Streamlit 표시 문구와 레벨로 변환한다."""
    verdict = str(item.get("chart_verdict", "WAIT") or "WAIT").upper()
    entry_ok = bool(item.get("entry_checks_passed"))
    if verdict == "BUY_READY" and entry_ok:
        return "🟢 매수 검토", "success"
    if verdict == "BUY_READY":
        return "🟡 차트 매수 준비·위험확인 필요", "warning"
    if verdict == "NO_ENTRY":
        return "🔴 매수 금지·매도 검토", "error"
    return "🟡 대기", "warning"


def forecast_label(percent: float) -> str:
    """예측 퍼센트를 화면용 방향 문구로 변환한다."""
    try:
        value = float(percent)
    except (TypeError, ValueError, OverflowError):
        return "데이터 부족"
    if value >= 0.35:
        return "상승 우세"
    if value <= -0.35:
        return "하락 위험"
    return "횡보·불확실"


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
        if not result:
            result.append(level)
            continue
        if abs(level/result[-1]-1)*100<=tol:
            result[-1]=max(result[-1],level)
        else:
            result.append(level)
    return result


def _confirmed_swing_points(bars:list[dict], left:int=2, right:int=2) -> tuple[list[dict], list[dict]]:
    """완성된 봉에서만 confirmed swing high/low를 찾는다.

    오른쪽 봉이 형성된 뒤에만 확정하므로 현재 진행 중인 고점/저점을
    지지·저항으로 미리 확정하지 않는다.
    """
    if not bars:
        return [], []
    size=len(bars)
    if size < left + right + 1:
        return [], []

    highs=[]
    lows=[]
    for i in range(left, size-right):
        h=float(bars[i].get("high",0) or 0)
        l=float(bars[i].get("low",0) or 0)
        if h<=0 or l<=0:
            continue

        left_high=max(float(bars[j].get("high",0) or 0) for j in range(i-left,i))
        right_high=max(float(bars[j].get("high",0) or 0) for j in range(i+1,i+right+1))
        left_low=min(float(bars[j].get("low",0) or 0) for j in range(i-left,i))
        right_low=min(float(bars[j].get("low",0) or 0) for j in range(i+1,i+right+1))

        if h >= left_high and h > right_high:
            highs.append({"price":h,"index":i,"time":bars[i].get("time")})
        if l <= left_low and l < right_low:
            lows.append({"price":l,"index":i,"time":bars[i].get("time")})
    return highs,lows


def _nearest_level(levels:list[dict], price:float, side:str) -> dict|None:
    valid=[]
    for row in levels:
        level=float(row.get("price",0) or 0)
        if side=="below" and 0<level<price:
            valid.append(row)
        elif side=="above" and level>price:
            valid.append(row)
    if not valid:
        return None
    if side=="below":
        return max(valid,key=lambda row:float(row.get("price",0) or 0))
    return min(valid,key=lambda row:float(row.get("price",0) or 0))



def _epoch_seconds(value) -> float:
    """Timestamp/문자열을 비교 가능한 epoch seconds로 변환한다."""
    if value is None:
        return 0.0
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize(KST)
        return float(ts.timestamp())
    except Exception:
        return 0.0


def _meaningful_resistance_candidates(
    price:float,
    support:float,
    five_highs:list[dict],
    fifteen_highs:list[dict],
    hourly_highs:list[dict],
) -> list[dict]:
    """미세 1분봉 저항을 버리고 5/15/60분 confirmed 저항만 정렬한다."""
    rows=[]
    for timeframe,points,priority in (
        ("5분",five_highs,1),
        ("15분",fifteen_highs,2),
        ("60분",hourly_highs,3),
    ):
        for point in points:
            level=float(point.get("price",0) or 0)
            if level<=price or support<=0:
                continue
            width=(level/support-1)*100
            # 반복단타의 최소 실전 폭 0.5%도 안 되는 저항은 '미세저항'으로
            # 분류하고 1차 목표가로 사용하지 않는다.
            if width < 0.50:
                continue
            rows.append({
                "price":level,
                "timeframe":timeframe,
                "priority":priority,
                "range_from_support":width,
                "time":point.get("time"),
            })

    rows=sorted(rows,key=lambda row:(float(row["price"]),-int(row["priority"])))
    dedup=[]
    for row in rows:
        if not dedup:
            dedup.append(row)
            continue
        gap=abs(float(row["price"])/float(dedup[-1]["price"])-1)*100
        if gap<=0.10:
            # 비슷한 가격이면 더 큰 시간대(60 > 15 > 5)를 신뢰한다.
            if int(row["priority"])>int(dedup[-1]["priority"]):
                dedup[-1]=row
        else:
            dedup.append(row)
    return dedup


def structural_trade_plan(item:dict,market:str):
    """60/15분 큰 구조 → 5분 confirmed swing → 1분 실행 순서로 가격대를 만든다."""
    price=float(item.get("price",0) or 0)
    one_highs=[float(x) for x in (item.get("chart_high_1m",[]) or []) if float(x or 0)>0]
    one_lows=[float(x) for x in (item.get("chart_low_1m",[]) or []) if float(x or 0)>0]
    closes=[float(x) for x in (item.get("chart_close_1m",[]) or []) if float(x or 0)>0]
    volumes=[float(x or 0) for x in (item.get("chart_volume_1m",[]) or [])]

    if price<=0 or len(closes)<30 or len(one_highs)<30 or len(one_lows)<30:
        item.update(
            level_plan_valid=False,
            level_plan_reason="큰 구조 계산에 필요한 1분봉 자료가 부족함",
        )
        return item

    vwap=float(item.get("vwap",0) or 0)
    ema9=float(item.get("ema9",0) or 0)
    ema20=float(item.get("ema20",0) or 0)

    # 큰 구조는 완성된 5/15/60분봉으로만 계산한다.
    bars5=_aggregate_ohlcv(item,5,market)
    bars15=_aggregate_ohlcv(item,15,market)
    bars60=_aggregate_ohlcv(item,60,market)

    if len(bars5)<5:
        item.update(
            level_plan_valid=False,
            level_plan_reason="완성된 5분봉이 부족해 confirmed Swing을 확정할 수 없음",
        )
        return item

    swing5_h,swing5_l=_confirmed_swing_points(bars5,2,2)
    # 15/60분은 당일 데이터가 짧을 수 있어 최소 3개 봉이면 1-1 pivot을 사용한다.
    swing15_h,swing15_l=_confirmed_swing_points(
        bars15,1 if len(bars15)<5 else 2,1 if len(bars15)<5 else 2
    )
    swing60_h,swing60_l=_confirmed_swing_points(bars60,1,1)

    support5=_nearest_level(swing5_l,price,"below")
    support15=_nearest_level(swing15_l,price,"below")
    support60=_nearest_level(swing60_l,price,"below")
    resistance5=_nearest_level(swing5_h,price,"above")
    resistance15=_nearest_level(swing15_h,price,"above")
    resistance60=_nearest_level(swing60_h,price,"above")

    if not support5:
        item.update(
            level_plan_valid=False,
            level_plan_reason="현재가 아래 confirmed 5분 Swing 지지가 아직 없음",
            confirmed_swing_5m_count=len(swing5_h)+len(swing5_l),
        )
        return item

    execution_support=float(support5["price"])
    macro_support_candidates=[
        ("15분",float(support15["price"])) if support15 else None,
        ("60분",float(support60["price"])) if support60 else None,
    ]
    macro_support_candidates=[row for row in macro_support_candidates if row and row[1]>0]
    macro_support=max(macro_support_candidates,key=lambda row:row[1]) if macro_support_candidates else None

    target_rows=_meaningful_resistance_candidates(
        price,execution_support,swing5_h,swing15_h,swing60_h
    )
    if not target_rows:
        item.update(
            level_plan_valid=False,
            level_plan_reason=(
                "현재가 위 5/15/60분 confirmed 저항 중 "
                "지지 대비 0.5% 이상인 실제 목표가가 없음"
            ),
            structure_support_5m=execution_support,
            structure_support_15m=float(support15["price"]) if support15 else 0.0,
            structure_support_60m=float(support60["price"]) if support60 else 0.0,
            confirmed_swing_5m_count=len(swing5_h)+len(swing5_l),
        )
        return item

    t1_row=target_rows[0]
    t2_row=target_rows[1] if len(target_rows)>=2 else None
    t1=float(t1_row["price"])
    t2=float(t2_row["price"]) if t2_row else 0.0

    # 2차는 1차와 사실상 같은 가격이면 인정하지 않는다.
    if t2>0 and (t2/t1-1)*100<0.20:
        t2=0.0
        t2_row=None

    risk=price-execution_support
    reward1=t1-price
    reward2=t2-price if t2>price else 0.0
    rr1=reward1/risk if risk>0 else 0.0
    rr2=reward2/risk if risk>0 and reward2>0 else 0.0
    t1pct=(t1/price-1)*100
    t2pct=(t2/price-1)*100 if t2>price else 0.0
    repeat_width=(t1/execution_support-1)*100 if t1>execution_support>0 else 0.0

    # 기존 장중 상승 점수는 유지하되 1분 미세 저항과 분리한다.
    ret5=(closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0.0
    ret15=(closes[-1]/closes[-16]-1)*100 if len(closes)>=16 else 0.0
    ret30=(closes[-1]/closes[-31]-1)*100 if len(closes)>=31 else 0.0
    higher_high=len(one_highs)>=12 and max(one_highs[-6:])>max(one_highs[-12:-6])
    higher_low=len(one_lows)>=12 and min(one_lows[-6:])>min(one_lows[-12:-6])

    up=down=0.0
    for i in range(max(1,len(closes)-20),len(closes)):
        vol=volumes[i] if i<len(volumes) else 0.0
        if closes[i]>closes[i-1]:
            up+=vol
        elif closes[i]<closes[i-1]:
            down+=vol
    volume_dom=up/down if down>0 else (2.0 if up>0 else 0.0)
    vgap=(price/vwap-1)*100 if vwap>0 else 99.0

    checks={
        "VWAP 위":price>vwap>0,
        "EMA 정배열":price>=ema9>ema20>0,
        "5분 상승":ret5>0,
        "15분 상승":ret15>0,
        "30분 상승":ret30>0,
        "고점 상승":higher_high,
        "저점 상승":higher_low,
        "상승봉 거래량 우세":volume_dom>=1.05,
        "VWAP 과대이격 아님":0<=vgap<=(2.5 if market=="국내" else 3.0),
        "confirmed 5분 Swing 존재":bool(swing5_h and swing5_l),
    }
    trend_score=sum(map(bool,checks.values()))

    macro_support_price=float(macro_support[1]) if macro_support else 0.0
    macro_support_tf=str(macro_support[0]) if macro_support else ""
    macro_resistance_candidates=[
        ("15분",float(resistance15["price"])) if resistance15 else None,
        ("60분",float(resistance60["price"])) if resistance60 else None,
    ]
    macro_resistance_candidates=[
        row for row in macro_resistance_candidates
        if row and row[1]>price
    ]
    macro_resistance=min(
        macro_resistance_candidates,key=lambda row:row[1]
    ) if macro_resistance_candidates else None

    target1_basis=(
        f"{t1_row['timeframe']} confirmed Swing 저항 "
        f"(지지→저항 실제폭 {repeat_width:.2f}%)"
    )
    target2_basis=(
        f"{t2_row['timeframe']} confirmed Swing/큰 구조 저항"
        if t2_row else
        "1차 위 confirmed 15/60분 저항 미확인"
    )
    stop_basis="confirmed 5분 Swing 지지 이탈 시 단기 반복 시나리오 무효"
    if macro_support:
        stop_basis+=f" · 큰 구조 {macro_support_tf} 지지 {fmt(macro_support_price)} 별도 감시"

    item.update(
        continuous_rise=trend_score>=7 and ret15>0 and ret30>0,
        continuous_rise_score=trend_score,
        continuous_rise_checks=checks,
        trend_return_5m=ret5,
        trend_return_15m=ret15,
        trend_return_30m=ret30,
        up_down_volume_ratio=volume_dom,

        # 실행 가격대
        structural_entry=price,
        structural_support=execution_support,
        stop_loss=execution_support,
        structural_target=t1,
        structural_target1=t1,
        structural_target2=t2,
        target1_upside_percent=t1pct,
        target2_upside_percent=t2pct,
        risk_reward=rr1,
        risk_reward_target1=rr1,
        risk_reward_target2=rr2,
        level_plan_valid=risk>0 and reward1>0,

        # 근거
        target_basis=target1_basis,
        target1_basis=target1_basis,
        target2_basis=target2_basis,
        stop_basis=stop_basis,
        level_plan_reason=(
            f"5분 confirmed 지지 {fmt(execution_support)} → "
            f"1차 {fmt(t1)} ({repeat_width:.2f}% Swing) → "
            f"2차 {fmt(t2) if t2 else '-'}"
        ),

        # 시간대별 큰 구조
        structure_support_5m=execution_support,
        structure_support_15m=float(support15["price"]) if support15 else 0.0,
        structure_support_60m=float(support60["price"]) if support60 else 0.0,
        structure_resistance_5m=float(resistance5["price"]) if resistance5 else 0.0,
        structure_resistance_15m=float(resistance15["price"]) if resistance15 else 0.0,
        structure_resistance_60m=float(resistance60["price"]) if resistance60 else 0.0,
        macro_support=macro_support_price,
        macro_support_timeframe=macro_support_tf,
        macro_resistance=float(macro_resistance[1]) if macro_resistance else 0.0,
        macro_resistance_timeframe=str(macro_resistance[0]) if macro_resistance else "",
        target1_timeframe=str(t1_row["timeframe"]),
        target2_timeframe=str(t2_row["timeframe"]) if t2_row else "",
        structure_support_5m_time=_epoch_seconds(support5.get("time")),
        target1_confirmed_time=_epoch_seconds(t1_row.get("time")),
        target2_confirmed_time=_epoch_seconds(t2_row.get("time")) if t2_row else 0.0,
        confirmed_swing_signature=(
            f"{round(execution_support,8)}|{round(t1,8)}|"
            f"{int(_epoch_seconds(support5.get('time')))}|{int(_epoch_seconds(t1_row.get('time')))}"
        ),
        confirmed_swing_5m_count=len(swing5_h)+len(swing5_l),
        confirmed_swing_15m_count=len(swing15_h)+len(swing15_l),
        confirmed_swing_60m_count=len(swing60_h)+len(swing60_l),

        # 후보 필터용 실제 Swing 폭
        repeat_scalp_range_percent=repeat_width,
        repeat_scalp_preferred_range=0.50<=repeat_width<=1.50,

        # 디버그/표시용 수준
        chart_resistance_levels=[float(row["price"]) for row in target_rows],
        chart_support_levels_5m=[float(row["price"]) for row in swing5_l],
    )
    item["structural_hard_stop"] = _structural_hard_stop(item)
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



CYCLE_COOLDOWN_SECONDS = 180
HARD_KILL_SECONDS = 900


def _cycle_key(market: str, ticker: str) -> str:
    return f"trade_cycle::{market}::{ticker}"


def _empty_cycle() -> dict:
    return {
        "state": "IDLE",
        "entry_price": 0.0,
        "target1": 0.0,
        "target2": 0.0,
        "soft_stop": 0.0,
        "hard_stop": 0.0,
        "started_at": 0.0,
        "closed_at": 0.0,
        "target1_hit": False,
        "target2_hit": False,
        "soft_breach_count": 0,
        "breakdown_state": "NORMAL",
        "exit_reason": "",
        "cooldown_until": 0.0,
        "hard_kill_until": 0.0,
        "cycle_no": 0,
        "last_swing_signature": "",
        "last_closed_at": 0.0,
        "entry_structure": {},
    }


def _structural_hard_stop(item: dict) -> float:
    """Soft Stop 아래의 다음 confirmed 큰 지지를 Hard Stop으로 선택한다."""
    soft = float(item.get("structural_support", 0) or 0)
    candidates = []
    for key in (
        "structure_support_15m",
        "structure_support_60m",
        "macro_support",
    ):
        value = float(item.get(key, 0) or 0)
        if value > 0 and (soft <= 0 or value < soft):
            candidates.append(value)

    # 5분 confirmed 지지 목록 중 현재 Soft Stop 아래의 다음 지지도 허용.
    for value in item.get("chart_support_levels_5m", []) or []:
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if value > 0 and (soft <= 0 or value < soft):
            candidates.append(value)

    if candidates:
        return max(candidates)

    # 아래 큰 지지가 아직 형성되지 않았다면 Hard Stop을 임의 퍼센트로 만들지 않는다.
    # 이 경우 Soft Stop만 표시하고 Hard Stop 미확인으로 둔다.
    return 0.0


def start_trade_cycle(
    item: dict,
    market: str,
    ticker: str,
    actual_entry_price: float,
    now_ts: float,
    prior_cycle: dict | None = None,
) -> dict:
    """실제 매수 시점의 계획을 고정한다. 이후 live 구조가 바뀌어도 이 값은 유지된다."""
    target1 = float(item.get("structural_target1", item.get("structural_target", 0)) or 0)
    target2 = float(item.get("structural_target2", 0) or 0)
    soft_stop = float(item.get("structural_support", 0) or 0)
    hard_stop = _structural_hard_stop(item)

    if actual_entry_price <= 0:
        raise ValueError("실제 진입가가 0 이하입니다.")
    if target1 <= actual_entry_price:
        raise ValueError("1차 목표가가 실제 진입가보다 높지 않아 Cycle을 시작할 수 없습니다.")
    if soft_stop <= 0 or soft_stop >= actual_entry_price:
        raise ValueError("Soft Stop으로 사용할 confirmed 지지가 진입가 아래에 없습니다.")
    if hard_stop <= 0 or hard_stop >= soft_stop:
        raise ValueError("Soft Stop 아래 confirmed 큰 구조 Hard Stop이 아직 없습니다.")

    net_swing_percent = (target1 / actual_entry_price - 1) * 100
    actual_risk_reward = (
        (target1 - actual_entry_price) / (actual_entry_price - soft_stop)
        if actual_entry_price > soft_stop
        else 0.0
    )
    if net_swing_percent < 0.50:
        raise ValueError(
            f"실제 체결가 기준 1차 순수 목표여력이 {net_swing_percent:.2f}%로 0.5% 미만입니다."
        )
    if actual_risk_reward < 1.50:
        raise ValueError(
            f"실제 체결가 기준 손익비가 {actual_risk_reward:.2f}로 1.50 미만입니다."
        )

    prior_cycle = prior_cycle or _empty_cycle()

    current_signature = str(item.get("confirmed_swing_signature", "") or "")
    support_confirmed_at = float(item.get("structure_support_5m_time", 0) or 0)
    last_signature = str(prior_cycle.get("last_swing_signature", "") or "")
    last_closed_at = float(prior_cycle.get("last_closed_at", 0) or 0)

    # 첫 Cycle이 아닌 재매수는 반드시 '새로 확정된 5분 Swing 저점'이 필요하다.
    # Cooldown 시간만 지났다고 같은 옛 지지/저항을 재사용하지 않는다.
    if last_closed_at > 0:
        if not current_signature:
            raise ValueError("새 confirmed Swing 식별값이 없어 재매수할 수 없습니다.")
        if current_signature == last_signature:
            raise ValueError("이전 Cycle과 같은 confirmed Swing이라 재매수할 수 없습니다.")
        if support_confirmed_at <= last_closed_at:
            raise ValueError("이전 청산 후 새로 확정된 5분 Swing 지지가 아직 없습니다.")

    if float(prior_cycle.get("hard_kill_until", 0) or 0) > now_ts:
        raise ValueError("HARD KILL 시간 중이라 새 Cycle을 시작할 수 없습니다.")
    if float(prior_cycle.get("cooldown_until", 0) or 0) > now_ts:
        raise ValueError("Cooldown 시간 중이라 새 Cycle을 시작할 수 없습니다.")

    cycle_no = int(prior_cycle.get("cycle_no", 0) or 0) + 1

    return {
        "state": "OPEN",
        "entry_price": float(actual_entry_price),
        "target1": target1,
        "target2": target2 if target2 > target1 else 0.0,
        "soft_stop": soft_stop,
        "hard_stop": hard_stop,
        "started_at": float(now_ts),
        "closed_at": 0.0,
        "target1_hit": False,
        "target2_hit": False,
        "soft_breach_count": 0,
        "breakdown_state": "NORMAL",
        "exit_reason": "",
        "cooldown_until": 0.0,
        "hard_kill_until": float(prior_cycle.get("hard_kill_until", 0) or 0),
        "cycle_no": cycle_no,
        "last_swing_signature": last_signature,
        "last_closed_at": last_closed_at,
        "net_swing_percent": net_swing_percent,
        "actual_risk_reward": actual_risk_reward,
        "entry_structure": {
            "repeat_width": float(item.get("repeat_scalp_range_percent", 0) or 0),
            "target1_timeframe": str(item.get("target1_timeframe", "")),
            "target2_timeframe": str(item.get("target2_timeframe", "")),
            "support_5m": float(item.get("structure_support_5m", 0) or 0),
            "support_15m": float(item.get("structure_support_15m", 0) or 0),
            "support_60m": float(item.get("structure_support_60m", 0) or 0),
            "macro_resistance": float(item.get("macro_resistance", 0) or 0),
            "confirmed_swing_signature": current_signature,
            "support_confirmed_at": support_confirmed_at,
            "target1_confirmed_at": float(item.get("target1_confirmed_time", 0) or 0),
        },
    }


def classify_cycle_breakdown(cycle: dict, live_item: dict) -> tuple[str, dict]:
    """Soft Stop 이탈을 SHAKEOUT과 REAL_BREAKDOWN으로 분리한다."""
    if cycle.get("state") not in {"OPEN", "TARGET1_HIT"}:
        return "NORMAL", {}

    price = float(live_item.get("price", 0) or 0)
    soft = float(cycle.get("soft_stop", 0) or 0)
    hard = float(cycle.get("hard_stop", 0) or 0)
    vwap = float(live_item.get("vwap", 0) or 0)
    ema9 = float(live_item.get("ema9", 0) or 0)
    ema20 = float(live_item.get("ema20", 0) or 0)
    reversal_score = int(live_item.get("repeat_scalp_reversal_score", 0) or 0)
    mtf_exit = bool(live_item.get("mtf_exit"))

    below_soft = soft > 0 and price < soft
    below_hard = hard > 0 and price <= hard
    below_vwap = vwap > 0 and price < vwap
    ema_bearish = ema20 > 0 and ema9 < ema20

    evidence = {
        "Soft Stop 이탈": below_soft,
        "Hard Stop 이탈": below_hard,
        "VWAP 아래": below_vwap,
        "EMA 하락정렬": ema_bearish,
        "하락전환 근거 3개 이상": reversal_score >= 3,
        "상위 시간대 EXIT": mtf_exit,
    }

    if below_hard:
        return "HARD_EXIT", evidence

    if below_soft:
        # 한두 번의 미세 이탈만으로 바로 붕괴로 확정하지 않는다.
        # 상위 시간대/EMA/VWAP/하락근거가 함께 깨질 때 REAL_BREAKDOWN.
        bearish_confirmations = sum(
            bool(x)
            for x in (
                below_vwap,
                ema_bearish,
                reversal_score >= 3,
                mtf_exit,
            )
        )
        if int(cycle.get("soft_breach_count", 0) or 0) >= 2 and bearish_confirmations >= 2:
            return "REAL_BREAKDOWN", evidence
        return "SHAKEOUT", evidence

    return "NORMAL", evidence


def update_trade_cycle(cycle: dict, live_item: dict, now_ts: float) -> dict:
    """실시간 시세로 Cycle 상태만 갱신한다. 고정 진입/목표/손절 가격은 변경하지 않는다."""
    cycle = dict(cycle or _empty_cycle())
    if cycle.get("state") not in {"OPEN", "TARGET1_HIT"}:
        return cycle

    price = float(live_item.get("price", 0) or 0)
    target1 = float(cycle.get("target1", 0) or 0)
    target2 = float(cycle.get("target2", 0) or 0)
    soft = float(cycle.get("soft_stop", 0) or 0)

    if target1 > 0 and price >= target1:
        cycle["target1_hit"] = True
        cycle["state"] = "TARGET1_HIT"

    if target2 > 0 and price >= target2:
        cycle["target2_hit"] = True
        cycle["state"] = "EXITED"
        cycle["closed_at"] = now_ts
        cycle["last_closed_at"] = now_ts
        cycle["last_swing_signature"] = str(
            (cycle.get("entry_structure", {}) or {}).get("confirmed_swing_signature", "") or ""
        )
        cycle["exit_reason"] = "TARGET2_HIT"
        cycle["cooldown_until"] = now_ts + CYCLE_COOLDOWN_SECONDS
        return cycle

    if soft > 0 and price < soft:
        cycle["soft_breach_count"] = int(cycle.get("soft_breach_count", 0) or 0) + 1
    else:
        cycle["soft_breach_count"] = 0

    breakdown_state, evidence = classify_cycle_breakdown(cycle, live_item)
    cycle["breakdown_state"] = breakdown_state
    cycle["breakdown_evidence"] = evidence

    if breakdown_state == "HARD_EXIT":
        cycle["state"] = "EXITED"
        cycle["closed_at"] = now_ts
        cycle["last_closed_at"] = now_ts
        cycle["last_swing_signature"] = str(
            (cycle.get("entry_structure", {}) or {}).get("confirmed_swing_signature", "") or ""
        )
        cycle["exit_reason"] = "HARD_EXIT"
        cycle["cooldown_until"] = now_ts + CYCLE_COOLDOWN_SECONDS
        cycle["hard_kill_until"] = now_ts + HARD_KILL_SECONDS
    elif breakdown_state == "REAL_BREAKDOWN":
        cycle["state"] = "EXITED"
        cycle["closed_at"] = now_ts
        cycle["last_closed_at"] = now_ts
        cycle["last_swing_signature"] = str(
            (cycle.get("entry_structure", {}) or {}).get("confirmed_swing_signature", "") or ""
        )
        cycle["exit_reason"] = "REAL_BREAKDOWN"
        cycle["cooldown_until"] = now_ts + CYCLE_COOLDOWN_SECONDS

    return cycle


def close_trade_cycle(cycle: dict, now_ts: float, reason: str = "MANUAL_EXIT") -> dict:
    cycle = dict(cycle or _empty_cycle())
    cycle["state"] = "EXITED"
    cycle["closed_at"] = now_ts
    cycle["last_closed_at"] = now_ts
    cycle["last_swing_signature"] = str(
        (cycle.get("entry_structure", {}) or {}).get("confirmed_swing_signature", "") or ""
    )
    cycle["exit_reason"] = reason
    cycle["cooldown_until"] = now_ts + CYCLE_COOLDOWN_SECONDS
    return cycle


def reset_trade_cycle(cycle: dict, now_ts: float) -> dict:
    """종료된 Cycle을 IDLE로 돌리되 Cooldown/Hard Kill과 회차는 유지한다."""
    old = dict(cycle or _empty_cycle())
    fresh = _empty_cycle()
    fresh["cycle_no"] = int(old.get("cycle_no", 0) or 0)
    fresh["last_swing_signature"] = str(old.get("last_swing_signature", "") or "")
    fresh["last_closed_at"] = float(old.get("last_closed_at", 0) or 0)
    fresh["cooldown_until"] = float(old.get("cooldown_until", 0) or 0)
    fresh["hard_kill_until"] = float(old.get("hard_kill_until", 0) or 0)
    if fresh["hard_kill_until"] > now_ts:
        fresh["state"] = "HARD_KILL"
    elif fresh["cooldown_until"] > now_ts:
        fresh["state"] = "COOLDOWN"
    return fresh


def cycle_status_text(cycle: dict, now_ts: float) -> str:
    state = str(cycle.get("state", "IDLE"))
    if state == "HARD_KILL":
        remain = max(0, int(float(cycle.get("hard_kill_until", 0) or 0) - now_ts))
        return f"HARD KILL · 재진입 금지 {remain//60}분 {remain%60}초"
    if state == "COOLDOWN":
        remain = max(0, int(float(cycle.get("cooldown_until", 0) or 0) - now_ts))
        return f"Cooldown · 재매수 대기 {remain//60}분 {remain%60}초"
    if state == "OPEN":
        return f"보유 Cycle #{int(cycle.get('cycle_no',0) or 0)}"
    if state == "TARGET1_HIT":
        return f"1차 목표 도달 · Cycle #{int(cycle.get('cycle_no',0) or 0)}"
    if state == "EXITED":
        return f"Cycle 종료 · {cycle.get('exit_reason','')}"
    return "진입 전 · 새 Cycle 대기"



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
    macro_support=float(item.get("macro_support",0) or 0); macro_resistance=float(item.get("macro_resistance",0) or 0)
    if support>0: levels.append({"가격":support,"구간":"5분 confirmed 지지"})
    if macro_support>0 and abs(macro_support/support-1)*100>0.10: levels.append({"가격":macro_support,"구간":"15/60분 큰 지지"})
    if t1>0: levels.append({"가격":t1,"구간":"1차 목표"})
    if t2>0: levels.append({"가격":t2,"구간":"2차 목표"})
    if macro_resistance>0 and all(abs(macro_resistance/x-1)*100>0.10 for x in (t1,t2) if x>0): levels.append({"가격":macro_resistance,"구간":"15/60분 큰 저항"})
    if levels:
        lf=pd.DataFrame(levels); chart=chart+alt.Chart(lf).mark_rule(strokeWidth=2,strokeDash=[6,4]).encode(y=alt.Y("가격:Q",scale=alt.Scale(zero=False)),color=alt.Color("구간:N",title=None))+alt.Chart(lf).mark_text(align="left",dx=5,dy=-5,fontWeight="bold").encode(y="가격:Q",text="구간:N",color=alt.Color("구간:N",legend=None))
    st.altair_chart(chart.properties(height=430),use_container_width=True)



def update_prediction_audit(ticker: str, price: float, item: dict, now_ts: float) -> list[dict]:
    """선택 종목의 5/10/20/30분 예측을 별도 로컬 DB에 저장·사후 채점한다."""
    bucket = float(int(now_ts // 300) * 300)
    with db_connect() as db:
        if price > 0:
            db.execute(
                "INSERT OR IGNORE INTO predictions(ticker,issued,base_price,f5,f10,f20,f30) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    ticker,
                    bucket,
                    price,
                    float(item.get("forecast_5m", 0) or 0),
                    float(item.get("forecast_10m", 0) or 0),
                    float(item.get("forecast_20m", 0) or 0),
                    float(item.get("forecast_30m", 0) or 0),
                ),
            )

        pending = db.execute(
            "SELECT id,issued,base_price,f5,f10,f20,f30,actual5,actual10,actual20,actual30 "
            "FROM predictions WHERE ticker=? AND issued>=?",
            (ticker, now_ts - 86400),
        ).fetchall()

        for row in pending:
            record_id, issued, base_price, f5, f10, f20, f30, a5, a10, a20, a30 = row
            if base_price <= 0:
                continue
            elapsed = now_ts - issued
            updates = {}
            if elapsed >= 300 and a5 is None:
                updates["actual5"] = (price / base_price - 1) * 100
            if elapsed >= 600 and a10 is None:
                updates["actual10"] = (price / base_price - 1) * 100
            if elapsed >= 1200 and a20 is None:
                updates["actual20"] = (price / base_price - 1) * 100
            if elapsed >= 1800 and a30 is None:
                updates["actual30"] = (price / base_price - 1) * 100
            for column, value in updates.items():
                db.execute(f"UPDATE predictions SET {column}=? WHERE id=?", (value, record_id))

        rows = db.execute(
            "SELECT issued,base_price,f5,f10,f20,f30,actual5,actual10,actual20,actual30 "
            "FROM predictions WHERE ticker=? ORDER BY issued DESC LIMIT 100",
            (ticker,),
        ).fetchall()

    records = []
    for issued, base_price, f5, f10, f20, f30, a5, a10, a20, a30 in rows:
        record = {
            "ticker": ticker,
            "issued": issued,
            "기준시각": datetime.fromtimestamp(issued, KST).strftime("%m-%d %H:%M"),
            "기준가": base_price,
            "예상5분": f5,
            "예상10분": f10,
            "예상20분": f20,
            "예상30분": f30,
        }
        for minutes, expected, actual in (
            (5, f5, a5),
            (10, f10, a10),
            (20, f20, a20),
            (30, f30, a30),
        ):
            if actual is not None:
                record[f"실제{minutes}분"] = round(actual, 3)
                record[f"적중{minutes}분"] = (float(expected) >= 0) == (float(actual) >= 0)
        records.append(record)
    return records


@st.cache_data(ttl=10, show_spinner=False)
def calibration_stats(ticker: str) -> dict:
    stats = {}
    with db_connect() as db:
        for minutes in (5, 10, 20, 30):
            rows = db.execute(
                f"SELECT f{minutes},actual{minutes} FROM predictions "
                f"WHERE ticker=? AND actual{minutes} IS NOT NULL "
                "ORDER BY issued DESC LIMIT 300",
                (ticker,),
            ).fetchall()
            if not rows:
                stats[minutes] = {"samples": 0, "accuracy": 0.0, "bias": 0.0, "mae": 0.0}
                continue
            errors = [float(expected) - float(actual) for expected, actual in rows]
            accuracy = (
                sum((float(expected) >= 0) == (float(actual) >= 0) for expected, actual in rows)
                / len(rows)
                * 100
            )
            stats[minutes] = {
                "samples": len(rows),
                "accuracy": accuracy,
                "bias": sum(errors) / len(errors),
                "mae": sum(abs(value) for value in errors) / len(errors),
            }
    return stats


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("⚡ 초단타 VWAP 매수타점")
st.caption(
    "집중 모드에서는 현재가를 빠르게 갱신하고, 구조 계산은 별도 주기로 수행합니다. "
    "반복단타 기본 후보는 실제 차트의 지지→1차 저항 폭 0.5~1.5%입니다."
)

with st.sidebar:
    st.header("초단타 설정")
    market = st.radio("시장", ["국내", "미국"], horizontal=True)
    session_info = market_clock(market)
    st.caption(f"{session_info['session']} · {session_info['local_time']}")

    mode = "국내 30분 1% 타점" if market == "국내" else "미국 30분 1% 타점"
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input(
        "종목명 또는 종목코드 검색",
        placeholder="현대차, 005380, SOXL",
    ).strip()

    run_mode = st.radio(
        "실행 모드",
        ["가벼운 현재가", "선택 종목 집중", "시장 자동검증"],
        horizontal=False,
        key="scalp_run_mode",
        help="한 번에 하나의 모드만 실행해 API 과호출과 상태 충돌을 줄입니다.",
    )
    focus_only = run_mode == "선택 종목 집중"
    auto_audit = run_mode == "시장 자동검증"
    require_validation = st.toggle(
        "실전 검증 잠금",
        True,
        help="선택 종목의 실제 5·10분 검증표본이 쌓이기 전에는 초록색 매수 신호를 잠급니다.",
    )
    st.caption("반복단타 기본폭 0.5~1.5% · 1차/2차 목표는 차트 저항 기반")

now = time.time()
manual_search_active = bool(manual_ticker)
live_refresh_active = bool(focus_only or auto_audit)

st_autorefresh(
    interval=1500 if focus_only else 10000 if auto_audit else 8000,
    key="scalp_tick",
)

# 자동검증 상태/다운로드 메뉴 복원
if AUDIT_IMPORT_ERROR:
    st.sidebar.error("자동검증 파일 오류: " + AUDIT_IMPORT_ERROR)
elif auto_audit:
    audit_paused_for_focus = manual_search_active or focus_only
    if not audit_paused_for_focus:
        background_audit_tick(True, now, market)

    audit_now = datetime.fromtimestamp(now, KST)
    audit_minute = audit_now.hour * 60 + audit_now.minute

    if market == "미국":
        if session_info["tradable"]:
            audit_phase = f"{session_info['session']} · 신호 수집·사후 채점 중"
        else:
            audit_phase = f"{session_info['session']} · 다음 미국 세션 대기"
    elif audit_now.weekday() >= 5:
        audit_phase = "휴장일 · 다음 영업일 대기"
    elif audit_minute < 8 * 60 + 50:
        audit_phase = "준비 완료 · 08:50 자동 시작"
    elif audit_minute < 9 * 60:
        audit_phase = "사전 시세 확인 중 · 09:00 신호 시작"
    elif audit_minute < 15 * 60:
        audit_phase = "한국 정규장 · 신호 수집·사후 채점 중"
    elif audit_minute <= 15 * 60 + 35:
        audit_phase = "신규 신호 종료 · 30분 사후 채점 중"
    else:
        audit_phase = "한국장 검증 완료 · 결과 다운로드 가능"

    displayed_phase = (
        "집중분석 우선 · 후보 수집 일시정지"
        if audit_paused_for_focus
        else audit_phase
    )
    last_audit_ok = st.session_state.get("audit_last_ok")
    st.sidebar.success(
        "자동검증 · "
        + displayed_phase
        + (f"\n\n최근 처리: {last_audit_ok}" if last_audit_ok else "")
    )

    if st.session_state.get("audit_last_error"):
        st.sidebar.warning(st.session_state["audit_last_error"])

    if AUDIT_CSV_PATH.exists():
        st.sidebar.download_button(
            "검증 CSV 내려받기",
            AUDIT_CSV_PATH.read_bytes(),
            file_name="validation_summary.csv",
            mime="text/csv",
            key="audit_csv_download",
        )

    audit_report_path = AUDIT_DB_PATH.parent / "validation_report.html"
    if audit_report_path.exists():
        st.sidebar.download_button(
            "검증 보고서 내려받기",
            audit_report_path.read_bytes(),
            file_name="validation_report.html",
            mime="text/html",
            key="audit_report_download",
        )

    try:
        with audit_connect() as audit_db:
            total_signals = int(audit_db.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
            completed_signals = int(
                audit_db.execute("SELECT COUNT(*) FROM signals WHERE result_done=1").fetchone()[0]
            )
        st.sidebar.caption(f"저장 {total_signals}건 · 30분 채점완료 {completed_signals}건")
    except Exception:
        pass


# 실시간 반복단타 후보
candidate_board = [] if manual_search_active else latest_entry_candidates(market, minimum_score)

st.subheader("실시간 반복단타 후보")
st.caption(
    "실제 지지→실제 1차 저항 폭이 0.5~1.5%인 종목만 기본 후보로 표시합니다. "
    "2차 목표와 추가상승 가능성은 별도로 보여줍니다."
)

if manual_search_active:
    st.caption("직접 검색 우선 모드 · 자동 후보 수집을 잠시 멈추고 선택 종목만 분석합니다.")
elif candidate_board:
    board_rows = []
    for candidate in candidate_board:
        board_rows.append({
            "판정": candidate["stage"],
            "종목": f"{candidate['ticker']} · {candidate['name']}",
            "현재가": candidate["price"],
            "반복 매수": candidate["support"],
            "반복 매도/1차": candidate["target1"],
            "반복폭": f"{candidate['repeat_width']:.2f}%",
            "2차 목표": candidate["target2"] if candidate["target2"] > 0 else None,
            "추가상승": candidate["extension_label"],
            "1차→2차": (
                f"+{candidate['extension_percent']:.2f}%"
                if candidate["extension_percent"] > 0
                else "-"
            ),
            "지속상승": f"{candidate['trend_score']}/10",
            "점수": round(candidate["score"]),
            "RVOL": round(candidate["rvol"], 1),
            "손익비": round(candidate["risk_reward"], 2),
            "분석시각": datetime.fromtimestamp(candidate["issued"], KST).strftime("%H:%M:%S"),
        })
    st.dataframe(pd.DataFrame(board_rows), hide_index=True, use_container_width=True)
    st.caption("후보표의 0.5~1.5%는 목표가 고정값이 아니라 실제 지지→실제 1차 저항의 차트폭입니다.")
else:
    if focus_only:
        st.info("현재 0.5~1.5% 반복폭과 필수 조건을 동시에 통과한 후보가 없습니다. 아래에서 직접 종목을 집중 분석할 수 있습니다.")
    else:
        st.info("현재 0.5~1.5% 반복폭과 필수 조건을 동시에 통과한 후보가 없습니다.")


# 종목 선택
options = live_filtered_universe(market) if not manual_ticker else []
resolved_manual = None

if manual_ticker:
    resolved = resolve_manual(manual_ticker, market)
    if resolved:
        resolved_manual = resolved
        options.insert(0, resolved)
    else:
        st.sidebar.error("종목을 찾지 못했습니다. 이름 또는 코드를 다시 확인해 주세요.")
        options = live_filtered_universe(market)

dedup = {}
for row in options:
    ticker = str(row.get("ticker", "")).upper()
    if ticker:
        dedup[ticker] = row
options = list(dedup.values())

if market == "국내" and not resolved_manual:
    options = [
        row for row in options
        if 0
        < float(
            row.get("screen_change", row.get("change_percent", row.get("change", 0))) or 0
        )
        < 20.0
    ]

if not options:
    st.warning("현재 자동 후보가 없습니다. 원하는 종목을 직접 검색해 주세요.")
    st.stop()

selected_ticker = st.selectbox(
    "집중 분석할 종목 (자동 목록은 정밀검증 대기, 위 표만 확정 후보)",
    [str(row.get("ticker", "")) for row in options],
    format_func=lambda ticker: next(
        (
            f"{ticker} · {row.get('name', ticker)} · {row.get('asset_type', '')}"
            for row in options
            if str(row.get("ticker", "")) == ticker
        ),
        ticker,
    ),
    key=f"focus_ticker::{market}::{resolved_manual.get('ticker') if resolved_manual else 'default'}",
)

selected_row = next(row for row in options if str(row.get("ticker", "")) == selected_ticker)
selected_row.setdefault("exchange", "KR" if market == "국내" else "NASDAQ")

if st.session_state.get("scalp_selected") != selected_ticker:
    st.session_state["scalp_selected"] = selected_ticker
    st.session_state["scalp_last_precise"] = 0.0
    st.session_state["scalp_last_quote"] = 0.0
    st.session_state.pop("scalp_latest", None)
    st.session_state["scalp_live_history"] = []
    # 종목별 Cycle은 별도 키로 보존한다. 종목 전환이 기존 보유 계획을 덮어쓰지 않는다.


# 정밀계산 주기와 현재가 갱신 주기 분리 복원
latest = dict(st.session_state.get("scalp_latest", {}))
precise_refresh_seconds = 5 if focus_only else 60

precise_due = bool(
    not latest
    or (
        live_refresh_active
        and now - float(st.session_state.get("scalp_last_precise", 0))
        >= precise_refresh_seconds
    )
)

if precise_due or not latest:
    with st.spinner(f"{selected_ticker} 1분봉 정밀분석 중..."):
        try:
            latest = precise_analysis(selected_row, mode)
            st.session_state["scalp_latest"] = latest
            st.session_state["scalp_last_precise"] = now
            st.session_state.pop("scalp_error", None)
        except Exception as error:
            st.session_state["scalp_error"] = str(error)

elif now - float(st.session_state.get("scalp_last_quote", 0)) >= (1 if focus_only else 5):
    # 현재가/호가만 빠르게 새로 받고, 기존 1분봉 구조를 재사용한다.
    try:
        refreshed = scanner().refresh_quotes([latest], mode)
        if refreshed:
            latest.update(refreshed[0])
            if market == "미국":
                latest = normalize_us_item(latest, selected_row)

            # 가격이 달라지면 기존 분봉을 재사용하여 구조값만 경량 재판정.
            latest = structural_trade_plan(latest, market)
            latest = multi_timeframe_plan(latest, market)
            latest = upside_continuation_plan(latest)
            latest = repeat_scalp_plan(latest)

            _, fast_gate_ok, fast_spread = data_quality_gate(latest, market)
            latest["data_gate_passed"] = bool(fast_gate_ok)
            latest["verified_spread_percent"] = fast_spread
            st.session_state["scalp_latest"] = latest

        st.session_state["scalp_last_quote"] = now
        st.session_state.pop("scalp_quote_error", None)
    except Exception as error:
        st.session_state["scalp_quote_error"] = str(error)

if st.session_state.get("scalp_error"):
    st.error(f"분석 대기: {st.session_state['scalp_error']}")
if st.session_state.get("scalp_quote_error"):
    st.caption(f"현재가 갱신 경고: {st.session_state['scalp_quote_error']}")

if not latest:
    st.stop()

if latest.get("intraday_fallback"):
    st.warning(
        "KIS 분봉이 부족해 보조 분봉이 사용된 상태입니다. "
        "이 경우 실시간 매수 신호는 잠그고 관찰용으로만 봅니다."
    )

price = float(latest.get("price", 0) or 0)
change = float(latest.get("change_percent", 0) or 0)

if price <= 0:
    st.error("⛔ 현재가가 확인되지 않아 분석과 매수 판정을 중단했습니다.")
    st.stop()

cycle_key = _cycle_key(market, selected_ticker)
cycle = dict(st.session_state.get(cycle_key, _empty_cycle()))

# 시간이 지나 Cooldown/Hard Kill이 끝났으면 자동으로 새 Cycle 대기 상태로 전환한다.
if cycle.get("state") in {"COOLDOWN", "HARD_KILL"}:
    if (
        float(cycle.get("hard_kill_until", 0) or 0) <= now
        and float(cycle.get("cooldown_until", 0) or 0) <= now
    ):
        cycle = reset_trade_cycle(cycle, now)
        cycle["state"] = "IDLE"

# 보유 중이면 실시간 구조로 상태만 재평가한다.
# 진입가/1차/2차/Soft/Hard Stop 숫자는 update_trade_cycle에서 절대 수정하지 않는다.
if cycle.get("state") in {"OPEN", "TARGET1_HIT"}:
    cycle = update_trade_cycle(cycle, latest, now)
    st.session_state[cycle_key] = cycle
elif cycle.get("state") == "EXITED":
    # EXITED 상태는 사용자가 확인할 수 있도록 유지한다.
    st.session_state[cycle_key] = cycle


# 예측 Calibration
calibration = (
    calibration_stats(selected_ticker)
    if require_validation
    else {
        horizon: {"samples": 0, "accuracy": 0.0, "mae": 0.0, "bias": 0.0}
        for horizon in (5, 10, 20, 30)
    }
)

for horizon in (5, 10, 20, 30):
    stat = calibration[horizon]
    if stat["samples"] >= 20:
        key = f"forecast_{horizon}m"
        latest[key] = round(
            float(latest.get(key, 0) or 0) - float(stat["bias"]),
            3,
        )

validated_signal = (
    calibration[5]["samples"] >= 20
    and calibration[10]["samples"] >= 20
    and calibration[5]["accuracy"] >= 55
    and calibration[10]["accuracy"] >= 55
)


# 공통 판정값
quality_rows, quality_passed, spread_pct = data_quality_gate(latest, market)
regime_name, regime_method = market_regime(latest)
strategy_rows, buy_votes, sell_votes, wait_votes = strategy_consensus(latest)
weighted_score, weighted_buy = weighted_strategy_score(strategy_rows, regime_name)

context_key = f"scalp_context::{market}::{selected_ticker}"
if live_refresh_active or context_key not in st.session_state:
    st.session_state[context_key] = benchmark_context(market, selected_ticker)
context = st.session_state[context_key]

forecast_up = float(latest.get("forecast_5m", 0) or 0) >= 0.35
context_aligned = bool(context.get("confirmed")) and (
    not forecast_up
    or (
        float(context.get("change", 0) or 0) >= -0.05
        and float(context.get("intraday", 0) or 0) >= -0.10
    )
)

risk_reward = float(latest.get("risk_reward", 0) or 0)
is_manual_search = str(selected_row.get("asset_type", "")) == "직접 검색"
continuous_rise = bool(latest.get("continuous_rise"))
continuous_rise_score = int(latest.get("continuous_rise_score", 0) or 0)

repeat_state = str(latest.get("repeat_scalp_state", "UNAVAILABLE"))
repeat_label = str(latest.get("repeat_scalp_label", "⚪ 반복단타 판정 대기"))
repeat_buy = float(latest.get("repeat_scalp_buy_level", 0) or 0)
repeat_sell = float(latest.get("repeat_scalp_sell_level", 0) or 0)
repeat_stop = float(
    latest.get("stop_loss", latest.get("repeat_scalp_invalidation", 0)) or 0
)
repeat_width = float(latest.get("repeat_scalp_range_percent", 0) or 0)

higher_trend = bool(latest.get("mtf_higher_trend"))
short_pullback = bool(latest.get("mtf_short_pullback"))

target1 = float(
    latest.get("structural_target1", latest.get("structural_target", 0)) or 0
)
target2 = float(latest.get("structural_target2", 0) or 0)
target1_pct = float(latest.get("target1_upside_percent", 0) or 0)
target2_pct = float(latest.get("target2_upside_percent", 0) or 0)

label, level = verdict_text(latest)

if repeat_state == "EXIT":
    regime_name = "하락 전환"
    regime_method = "신규 매수 중단 · 실제 지지 회복과 하락 전환 해소 확인"

if not quality_passed:
    level, label = "error", "🔴 판정 불가 · 실시간 데이터 검문 미통과"
elif not bool(latest.get("level_plan_valid")):
    level, label = "error", "🔴 진입 금지 · 실제 지지·저항 가격대 미확인"
elif not is_manual_search and not continuous_rise:
    level, label = "error", f"🔴 후보 제외 · 장중 지속상승 {continuous_rise_score}/10"
elif not context_aligned:
    level, label = "error", f"🔴 진입 금지 · 기초지수/시장({context.get('name')}) 동조 미확인"
elif risk_reward < 1.5:
    level, label = "error", f"🔴 진입 금지 · 손익비 {risk_reward:.2f} (최소 1.50)"
elif repeat_state == "EXIT":
    level, label = "error", repeat_label
elif repeat_state == "TAKE_PROFIT":
    level, label = "warning", repeat_label
elif repeat_state in {"RANGE_TOO_NARROW", "RANGE_TOO_WIDE"}:
    level, label = "warning", repeat_label
elif sell_votes >= 3:
    level, label = "error", f"🔴 진입 금지 · 매도/약세 기법 {sell_votes}개 감지"
elif require_validation and not validated_signal:
    level, label = "warning", "🟡 모의검증 중 · 실제 적중표본이 쌓이기 전 실전 신호 잠금"
elif (
    repeat_state in {"BUY_PULLBACK", "HOLD_OR_BREAKOUT"}
    and buy_votes >= 4
    and sell_votes <= 2
):
    level, label = "success", repeat_label
elif weighted_score < 35 or weighted_buy < 55:
    level, label = "warning", f"🟡 대기 · 장세가중 합의 {weighted_score:+.1f}점"
elif buy_votes < 6 or sell_votes > 0:
    level, label = "warning", f"🟡 대기 · 매수 합의 {buy_votes}/10 · 매도 경고 {sell_votes}/10"

latest["entry_checks_passed"] = level == "success"
live_hard_stop = float(latest.get("structural_hard_stop", 0) or 0)
live_net_swing = ((target1 / price) - 1) * 100 if target1 > price > 0 else 0.0
live_signature = str(latest.get("confirmed_swing_signature", "") or "")
live_support_confirmed_at = float(latest.get("structure_support_5m_time", 0) or 0)
previous_signature = str(cycle.get("last_swing_signature", "") or "")
previous_closed_at = float(cycle.get("last_closed_at", 0) or 0)
fresh_swing_for_reentry = bool(
    previous_closed_at <= 0
    or (
        live_signature
        and live_signature != previous_signature
        and live_support_confirmed_at > previous_closed_at
    )
)
latest["net_swing_percent"] = live_net_swing
latest["fresh_swing_for_reentry"] = fresh_swing_for_reentry
latest["FINAL_BUY"] = bool(
    level == "success"
    and 0.50 <= repeat_width <= 1.50
    and live_net_swing >= 0.50
    and live_hard_stop > 0
    and live_hard_stop < float(latest.get("structural_support", 0) or 0) < price
    and fresh_swing_for_reentry
    and repeat_state in {"BUY_PULLBACK", "HOLD_OR_BREAKOUT"}
)

# 보유 Cycle이 있으면 신규 진입 판정보다 Cycle 관리가 우선한다.
cycle_state = str(cycle.get("state", "IDLE"))
cycle_breakdown = str(cycle.get("breakdown_state", "NORMAL"))

if cycle_state in {"OPEN", "TARGET1_HIT"}:
    # live 구조의 목표가가 바뀌더라도 고정 Cycle 가격은 그대로 사용한다.
    target1 = float(cycle.get("target1", target1) or target1)
    target2 = float(cycle.get("target2", target2) or target2)
    repeat_stop = float(cycle.get("soft_stop", repeat_stop) or repeat_stop)

if cycle_state == "EXITED":
    level = "error" if cycle.get("exit_reason") in {"HARD_EXIT", "REAL_BREAKDOWN"} else "warning"
elif cycle_state == "HARD_KILL":
    level = "error"
elif cycle_state == "COOLDOWN":
    level = "warning"


# 상단 한 줄 행동지시 복원
if cycle_state == "HARD_KILL":
    action_class, action_title = "stop", "🔴 HARD KILL · 재진입 금지"
    action_line = cycle_status_text(cycle, now)
elif cycle_state == "COOLDOWN":
    action_class, action_title = "wait", "🟡 Cooldown · 재매수 대기"
    action_line = cycle_status_text(cycle, now)
elif cycle_state == "EXITED":
    reason = str(cycle.get("exit_reason", ""))
    if reason in {"HARD_EXIT", "REAL_BREAKDOWN"}:
        action_class, action_title = "stop", "🔴 Cycle 종료 · 재진입 대기"
    else:
        action_class, action_title = "sell", "🟠 Cycle 종료"
    action_line = f"{reason or '청산'} · 새 Swing 확인 후 다음 Cycle"
elif cycle_state in {"OPEN", "TARGET1_HIT"}:
    if cycle_breakdown == "SHAKEOUT":
        action_class, action_title = "wait", "🟡 SHAKEOUT 의심 · 즉시 손절 아님"
        action_line = (
            f"Soft Stop {fmt(cycle.get('soft_stop'))} 일시 이탈 · "
            "VWAP/EMA/상위시간대 붕괴 동반 여부 재확인"
        )
    elif cycle_breakdown == "REAL_BREAKDOWN":
        action_class, action_title = "stop", "🔴 REAL_BREAKDOWN · 매도"
        action_line = "Soft Stop 이탈이 반복되고 하락 구조가 함께 확인됐습니다."
    elif cycle_breakdown == "HARD_EXIT":
        action_class, action_title = "stop", "🔴 HARD_EXIT · 즉시 청산"
        action_line = f"Hard Stop {fmt(cycle.get('hard_stop'))} 이탈"
    elif cycle_state == "TARGET1_HIT":
        action_class, action_title = "sell", "🟠 1차 목표 도달 · 일부 익절"
        action_line = (
            f"고정 1차 {fmt(cycle.get('target1'))} 도달 · "
            + (
                f"잔여분 2차 {fmt(cycle.get('target2'))} / 트레일링 관리"
                if float(cycle.get("target2",0) or 0) > 0
                else "2차 confirmed 저항 없음 · 잔여분은 트레일링 관리"
            )
        )
    else:
        action_class, action_title = "buy", "🟢 보유 Cycle 관리 중"
        action_line = (
            f"고정 진입 {fmt(cycle.get('entry_price'))} → "
            f"1차 {fmt(cycle.get('target1'))}"
            + (
                f" → 2차 {fmt(cycle.get('target2'))}"
                if float(cycle.get("target2",0) or 0) > 0
                else ""
            )
        )
elif not quality_passed:
    action_class, action_title = "wait", "⚪ 시세 확인 중·주문 대기"
    action_line = "현재가·분봉·호가 중 미수신 항목을 확인 중입니다."
elif repeat_state == "BUY_PULLBACK" and level == "success":
    action_class, action_title = "buy", "🟢 지금 매수 구간"
    action_line = f"{fmt(repeat_buy)} 부근 분할매수 → 1차 {fmt(target1)}"
elif repeat_state == "HOLD_OR_BREAKOUT" and level == "success":
    action_class, action_title = "buy", "🟢 돌파 확인 후 매수"
    action_line = f"현재가 지지 확인 → 1차 {fmt(target1)}"
elif repeat_state == "TAKE_PROFIT":
    action_class, action_title = "sell", "🟠 1차 목표 접근·분할매도"
    action_line = f"실제 차트 저항 {fmt(target1)} 도달 구간 · 추격매수 금지"
elif repeat_state == "EXIT":
    action_class, action_title = "stop", "🔴 반복단타 종료·매도"
    action_line = f"추세가 꺾였습니다. {fmt(repeat_stop)} 이탈 시 재진입하지 마세요."
elif short_pullback:
    action_class, action_title = "wait", "🟡 상승 추세 속 단기 조정·매수 대기"
    action_line = f"{fmt(repeat_buy)} 지지 후 5분봉 재상승을 기다리세요."
elif higher_trend:
    action_class, action_title = "wait", "🔵 상승 방향 유지·매수 타점 대기"
    action_line = f"{fmt(repeat_buy)} 지지 후 VWAP 회복 시만 진입하세요."
elif repeat_state == "RANGE_TOO_NARROW":
    action_class, action_title = "wait", f"⚪ 반복폭 부족 +{repeat_width:.2f}%"
    action_line = "실제 지지→1차 저항 폭이 0.5% 미만입니다."
elif repeat_state == "RANGE_TOO_WIDE":
    action_class, action_title = "wait", f"🔵 반복폭 넓음 +{repeat_width:.2f}%"
    action_line = "상승여력은 있지만 기본 0.5~1.5% 반복단타 후보 범위 밖입니다."
else:
    action_class, action_title = "wait", "🟡 지금은 대기"
    action_line = f"{fmt(repeat_buy)} 지지 반등 또는 매수 합의를 기다리세요."

display_soft_stop = (
    float(cycle.get("soft_stop",0) or 0)
    if cycle_state in {"OPEN","TARGET1_HIT"}
    else repeat_stop
)
display_hard_stop = (
    float(cycle.get("hard_stop",0) or 0)
    if cycle_state in {"OPEN","TARGET1_HIT"}
    else float(latest.get("structural_hard_stop",0) or 0)
)

st.markdown(
    f'<div class="trade-action {action_class}">'
    f'<h2>{action_title}</h2>'
    f'<p><b>{action_line}</b></p>'
    f'<p>Soft Stop: {fmt(display_soft_stop)} · '
    f'Hard Stop: {fmt(display_hard_stop) if display_hard_stop>0 else "미확인"} · '
    f'확인된 반복폭: {repeat_width:.2f}%</p>'
    f'</div>',
    unsafe_allow_html=True,
)

if action_class in {"buy", "sell", "stop"}:
    alert_key = f"signal_alert::{market}::{selected_ticker}::{action_class}::{cycle_state}"
    last_alert = float(st.session_state.get(alert_key, 0) or 0)
    if now - last_alert >= 180:
        st.toast(f"{selected_ticker} · {action_title}")
        st.session_state[alert_key] = now


# Cycle Manager
st.subheader("Cycle Manager · 진입 후 가격 고정")

cycle = dict(st.session_state.get(cycle_key, cycle))
cycle_state = str(cycle.get("state", "IDLE"))

if cycle_state in {"OPEN", "TARGET1_HIT"}:
    cycle_cols = st.columns(8)
    cycle_cols[0].metric("고정 진입가", fmt(cycle.get("entry_price")))
    cycle_cols[1].metric("고정 1차 목표", fmt(cycle.get("target1")))
    cycle_cols[2].metric(
        "고정 2차 목표",
        fmt(cycle.get("target2")) if float(cycle.get("target2",0) or 0)>0 else "-"
    )
    cycle_cols[3].metric("Soft Stop", fmt(cycle.get("soft_stop")))
    cycle_cols[4].metric(
        "Hard Stop",
        fmt(cycle.get("hard_stop")) if float(cycle.get("hard_stop",0) or 0)>0 else "미확인"
    )
    cycle_cols[5].metric("Net Swing", f"{float(cycle.get('net_swing_percent',0) or 0):.2f}%")
    cycle_cols[6].metric("실제 손익비", f"{float(cycle.get('actual_risk_reward',0) or 0):.2f}배")
    cycle_cols[7].metric("Cycle 상태", cycle_status_text(cycle, now))

    st.caption(
        "위 5개 가격은 진입 순간 고정됩니다. 아래 실시간 구조가 변해도 "
        "진입가·1차·2차·Soft/Hard Stop을 자동으로 다시 쓰지 않습니다."
    )

    if cycle.get("breakdown_state") == "SHAKEOUT":
        st.warning(
            "SHAKEOUT 감지: Soft Stop 아래로 잠깐 밀렸지만 "
            "REAL_BREAKDOWN 확정 조건은 아직 부족합니다."
        )

    breakdown_rows = [
        {"붕괴 검문": key, "감지": "예" if value else "아니오"}
        for key, value in (cycle.get("breakdown_evidence", {}) or {}).items()
    ]
    if breakdown_rows:
        with st.expander("보유 중 SHAKEOUT / REAL_BREAKDOWN 근거", expanded=False):
            st.dataframe(pd.DataFrame(breakdown_rows), hide_index=True, use_container_width=True)

    if st.button("내가 매도함 · Cycle 종료", use_container_width=True, key=f"cycle_close::{selected_ticker}"):
        cycle = close_trade_cycle(cycle, now, "MANUAL_EXIT")
        st.session_state[cycle_key] = cycle
        st.rerun()

elif cycle_state == "EXITED":
    st.warning(
        f"{cycle_status_text(cycle, now)} · "
        f"Cooldown 종료 후 새 confirmed Swing이 형성될 때만 재매수합니다."
    )
    if st.button("종료 확인 · Cooldown 상태로", use_container_width=True, key=f"cycle_reset::{selected_ticker}"):
        cycle = reset_trade_cycle(cycle, now)
        st.session_state[cycle_key] = cycle
        st.rerun()

elif cycle_state in {"COOLDOWN", "HARD_KILL"}:
    if cycle_state == "HARD_KILL":
        st.error(cycle_status_text(cycle, now))
    else:
        st.warning(cycle_status_text(cycle, now))

    if (
        float(cycle.get("hard_kill_until", 0) or 0) <= now
        and float(cycle.get("cooldown_until", 0) or 0) <= now
    ):
        if st.button("새 Cycle 대기 상태로 전환", use_container_width=True, key=f"cycle_idle::{selected_ticker}"):
            cycle = reset_trade_cycle(cycle, now)
            cycle["state"] = "IDLE"
            st.session_state[cycle_key] = cycle
            st.rerun()

else:
    entry_default = float(price)
    actual_entry = st.number_input(
        "실제 체결 진입가",
        min_value=0.0,
        value=entry_default,
        step=max(entry_default * 0.0001, 0.01),
        format="%.4f" if entry_default < 1000 else "%.0f",
        key=f"actual_entry::{market}::{selected_ticker}",
        help="실제로 체결된 가격을 입력한 뒤 아래 버튼을 누르면 그 순간의 계획이 고정됩니다.",
    )

    preview_hard = _structural_hard_stop(latest)
    preview_cols = st.columns(5)
    preview_cols[0].metric("고정 예정 진입", fmt(actual_entry))
    preview_cols[1].metric("고정 예정 1차", fmt(target1))
    preview_cols[2].metric("고정 예정 2차", fmt(target2) if target2 > 0 else "-")
    preview_cols[3].metric("고정 예정 Soft", fmt(latest.get("structural_support")))
    preview_cols[4].metric("고정 예정 Hard", fmt(preview_hard) if preview_hard > 0 else "미확인")

    actual_soft = float(latest.get("structural_support",0) or 0)
    actual_net_swing = ((target1 / actual_entry) - 1) * 100 if target1 > actual_entry > 0 else 0.0
    actual_rr = (
        (target1 - actual_entry) / (actual_entry - actual_soft)
        if actual_entry > actual_soft > 0
        else 0.0
    )

    st.caption(
        f"실제 체결가 기준 Net Swing {actual_net_swing:.2f}% · "
        f"손익비 {actual_rr:.2f}배"
    )

    can_start_cycle = bool(
        level == "success"
        and 0.50 <= repeat_width <= 1.50
        and actual_net_swing >= 0.50
        and actual_rr >= 1.50
        and preview_hard > 0
        and preview_hard < actual_soft < actual_entry
        and fresh_swing_for_reentry
        and target1 > actual_entry > 0
    )

    if not can_start_cycle:
        reasons = []
        if not (0.50 <= repeat_width <= 1.50):
            reasons.append(f"confirmed Swing {repeat_width:.2f}%")
        if actual_net_swing < 0.50:
            reasons.append(f"Net Swing {actual_net_swing:.2f}%")
        if actual_rr < 1.50:
            reasons.append(f"실제 손익비 {actual_rr:.2f}")
        if preview_hard <= 0:
            reasons.append("Hard Stop 미확인")
        if not fresh_swing_for_reentry:
            reasons.append("이전 청산 후 새 5분 confirmed Swing 미형성")
        if level != "success":
            reasons.append("진입 검문 미통과")
        st.info(
            "현재는 FINAL_BUY 조건이 아닙니다. "
            + (" · ".join(reasons) if reasons else "구조 재확인 필요")
        )

    if st.button(
        "매수함 · 현재 계획 고정",
        use_container_width=True,
        disabled=not can_start_cycle,
        key=f"cycle_start::{market}::{selected_ticker}",
    ):
        try:
            cycle = start_trade_cycle(
                latest,
                market,
                selected_ticker,
                float(actual_entry),
                now,
                prior_cycle=cycle,
            )
            st.session_state[cycle_key] = cycle
            st.rerun()
        except Exception as error:
            st.error(f"Cycle 시작 실패: {error}")


# 트레일링 스탑 표시 복원
trailing_price = float(latest.get("trailing_stop_price", 0) or 0)
if latest.get("trailing_stop_enabled") and trailing_price > 0:
    st.caption(
        f"상방 돌파 대응: 고점 추적 중 · 고점 대비 "
        f"{float(latest.get('trailing_stop_percent', 0.5) or 0.5):.1f}% 하락 "
        f"또는 {fmt(trailing_price)} 이탈 시 수익 보존 매도 알림"
    )


# 핵심 수치
top = st.columns([1.4, 1, 1, 1, 1])
top[0].metric(f"{latest.get('ticker')} · {latest.get('name')}", fmt(price), f"{change:+.2f}%")
top[1].metric("VWAP", fmt(latest.get("vwap")))
top[2].metric("단기 평균선(EMA9)", fmt(latest.get("ema9")))
top[3].metric("평소 대비 거래량", f"{float(latest.get('rvol', 0) or 0):.1f}배")
top[4].metric("하락위험", f"{int(latest.get('five_min_risk_score', 0) or 0)}점")

status_cols = st.columns(4)
status_cols[0].metric("데이터 검문", "통과" if quality_passed else "실패")
status_cols[1].metric("현재 장세", regime_name)
status_cols[2].metric(
    f"기초지수 {context.get('name')}",
    f"{float(context.get('change', 0) or 0):+.2f}%",
    (
        f"최근 5분 {float(context.get('intraday', 0) or 0):+.2f}%"
        if context.get("confirmed")
        else "분봉 미확인"
    ),
)
status_cols[3].metric("예상수익÷예상손실", f"{risk_reward:.2f}배")
st.caption(f"현재 적용 기법: {regime_method}")


# 1차/2차 목표 전체 표시
if cycle_state in {"OPEN", "TARGET1_HIT"}:
    st.caption("아래는 LIVE 구조입니다. 위 Cycle Manager의 고정 가격과 구분해서 보세요.")
level_cols = st.columns(5)
level_cols[0].metric("진입 기준가", fmt(latest.get("structural_entry")))
level_cols[1].metric(
    "1차 목표가",
    fmt(target1),
    f"{target1_pct:+.2f}%" if target1 > 0 else None,
)
level_cols[2].metric(
    "2차 목표가",
    fmt(target2) if target2 > 0 else "-",
    f"{target2_pct:+.2f}%" if target2 > 0 else None,
)
level_cols[3].metric("확인된 지지선", fmt(latest.get("structural_support")))
level_cols[4].metric("시나리오 무효·손절", fmt(latest.get("stop_loss")))

st.caption(
    f"1차 근거: {latest.get('target1_basis', latest.get('target_basis', '미확인'))} · "
    f"2차 근거: {latest.get('target2_basis', '미확인')} · "
    f"손절 근거: {latest.get('stop_basis', latest.get('level_plan_reason', '미확인'))}"
)

with st.expander("큰 구조 지지·저항 · 60분→15분→5분", expanded=False):
    structure_rows = [
        {
            "시간대": "60분",
            "지지": fmt(latest.get("structure_support_60m")),
            "저항": fmt(latest.get("structure_resistance_60m")),
            "confirmed Swing": int(latest.get("confirmed_swing_60m_count",0) or 0),
            "역할": "장 전체 큰 구조",
        },
        {
            "시간대": "15분",
            "지지": fmt(latest.get("structure_support_15m")),
            "저항": fmt(latest.get("structure_resistance_15m")),
            "confirmed Swing": int(latest.get("confirmed_swing_15m_count",0) or 0),
            "역할": "몇 시간 구조·2차 목표",
        },
        {
            "시간대": "5분",
            "지지": fmt(latest.get("structure_support_5m")),
            "저항": fmt(latest.get("structure_resistance_5m")),
            "confirmed Swing": int(latest.get("confirmed_swing_5m_count",0) or 0),
            "역할": "0.5~1.5% 반복 Swing",
        },
        {
            "시간대": "1분",
            "지지": "목표 계산에 사용 안 함",
            "저항": "목표 계산에 사용 안 함",
            "confirmed Swing": "-",
            "역할": "진입·재돌파 타이밍만",
        },
    ]
    st.dataframe(pd.DataFrame(structure_rows), hide_index=True, use_container_width=True)

st.caption(
    f"장중 지속상승 판정 {continuous_rise_score}/10 · "
    f"5분 {'상승' if float(latest.get('trend_return_5m', 0) or 0) > 0 else '하락'} · "
    f"15분 {'상승' if float(latest.get('trend_return_15m', 0) or 0) > 0 else '하락'} · "
    f"30분 {'상승' if float(latest.get('trend_return_30m', 0) or 0) > 0 else '하락'}"
)


# 추가상승 배너
extension_state = str(latest.get("upside_continuation_state", "NO_TARGET2"))
extension_label = str(latest.get("upside_continuation_label", "⚪ 추가상승 미확인"))
extension_score = int(latest.get("upside_continuation_score", 0) or 0)
extension_pct = float(latest.get("additional_upside_after_target1", 0) or 0)

if extension_state == "STRONG":
    st.success(
        f"{extension_label} · 근거 {extension_score}/10 · "
        f"1차→2차 추가여력 +{extension_pct:.2f}%"
    )
elif extension_state == "WATCH":
    st.warning(
        f"{extension_label} · 근거 {extension_score}/10 · "
        "1차 목표 돌파 시 거래량과 VWAP 유지를 확인하세요."
    )
elif extension_state == "LIMITED":
    st.error(f"{extension_label} · 근거 {extension_score}/10")
else:
    st.info(extension_label)


# 누락됐던 근거 메뉴 전부 복원
with st.expander("장중 지속상승 판정 근거", expanded=False):
    trend_rows = [
        {"조건": key, "통과": "✅" if value else "❌"}
        for key, value in (latest.get("continuous_rise_checks", {}) or {}).items()
    ]
    if trend_rows:
        st.dataframe(pd.DataFrame(trend_rows), hide_index=True, use_container_width=True)
    else:
        st.info("지속상승 근거 계산값이 아직 없습니다.")

with st.expander("다중 시간대 승인 · 일봉→60분→15분→5분→1분", expanded=False):
    mtf_status = latest.get("mtf_status", {}) or {}
    mtf_rows = [{"시간대 역할": key, "현재 판정": value} for key, value in mtf_status.items()]
    if mtf_rows:
        st.dataframe(pd.DataFrame(mtf_rows), hide_index=True, use_container_width=True)
    else:
        st.info("다중 시간대 계산값이 아직 없습니다.")

    mtf_detail = latest.get("mtf_detail", {}) or {}
    if mtf_detail:
        st.caption(
            " · ".join(
                f"{minutes}분 {float(detail.get('return', 0) or 0):+.2f}%"
                f"({'상승' if detail.get('bullish') else '하락' if detail.get('bearish') else '중립'})"
                for minutes, detail in mtf_detail.items()
            )
        )

with st.expander("추가상승 판정 근거", expanded=False):
    extension_rows = [
        {"조건": key, "충족": "✅" if value else "❌"}
        for key, value in (latest.get("upside_continuation_checks", {}) or {}).items()
    ]
    if extension_rows:
        st.dataframe(pd.DataFrame(extension_rows), hide_index=True, use_container_width=True)
    else:
        st.info("2차 목표 또는 추가상승 근거가 아직 형성되지 않았습니다.")


# 추세 반복단타 섹션 복원
st.subheader("추세 반복단타")
repeat_reason = str(latest.get("repeat_scalp_reason", "실제 지지·저항 확인 대기"))
repeat_message = (
    f"{repeat_label} · 매수 기준 {fmt(latest.get('repeat_scalp_buy_level'))} · "
    f"1차 매도 기준 {fmt(latest.get('repeat_scalp_sell_level'))} · "
    f"반복폭 {repeat_width:.2f}% · {repeat_reason}"
)

if action_class == "buy":
    st.success(f"{action_title} · {action_line}")
elif action_class in {"sell", "stop"}:
    st.error(f"{action_title} · {action_line}")
else:
    st.info(f"{action_title} · {action_line}")

st.caption(f"세부 계산: {repeat_message}")

with st.expander("추세 꺾임 판정 근거", expanded=repeat_state == "EXIT"):
    reversal_rows = [
        {"하락 전환 조건": key, "감지": "예" if value else "아니오"}
        for key, value in (latest.get("repeat_scalp_reversal_checks", {}) or {}).items()
    ]
    if reversal_rows:
        st.dataframe(pd.DataFrame(reversal_rows), hide_index=True, use_container_width=True)
    else:
        st.info("하락전환 근거 계산값이 아직 없습니다.")


# 최종 판정 상세 복원
with st.expander("최종 판정의 세부 차단·허용 근거", expanded=False):
    getattr(st, level)(label)

    if level == "success":
        st.write(
            f"진입: **{fmt(latest.get('structural_entry'))}** · "
            f"1차 목표: **{fmt(target1)}** · "
            f"2차 목표: **{fmt(target2) if target2 > 0 else '-'}** · "
            f"손절: **{fmt(latest.get('stop_loss'))}**"
        )
    elif level == "error":
        st.write(
            latest.get(
                "invalidation_reason",
                latest.get(
                    "level_plan_reason",
                    "VWAP·이동평균·호가·구조 조건을 다시 확인하세요.",
                ),
            )
        )
    else:
        st.write(
            latest.get(
                "entry_trigger",
                f"{fmt(repeat_buy)} 지지와 VWAP 회복, 거래량 재증가를 기다리세요.",
            )
        )

st.caption(f"한 줄 결론: {action_title} · {action_line}")


# 기법 합의/검문 복원
consensus_cols = st.columns(5)
consensus_cols[0].metric(
    "FINAL_BUY",
    "YES" if latest.get("FINAL_BUY") else "NO",
    "새 Swing" if latest.get("fresh_swing_for_reentry") else "재매수 Swing 대기",
)
consensus_cols[1].metric("매수 기법", f"{buy_votes}/10")
consensus_cols[2].metric("매도 기법", f"{sell_votes}/10")
consensus_cols[3].metric("대기 기법", f"{wait_votes}/10")
consensus_cols[4].metric(
    "종합 매수 강도",
    f"{weighted_score:+.1f}점",
    f"매수 근거 비중 {weighted_buy:.1f}%",
)

with st.expander("매수·매도 기법별 판정 근거", expanded=False):
    st.dataframe(pd.DataFrame(strategy_rows), hide_index=True, use_container_width=True)

with st.expander("실시간 데이터 검문 내역", expanded=not quality_passed):
    st.dataframe(pd.DataFrame(quality_rows), hide_index=True, use_container_width=True)


# 5/10/20/30분 전망 카드 복원
f5 = float(latest.get("forecast_5m", 0) or 0)
f10 = float(latest.get("forecast_10m", 0) or 0)
f20 = float(latest.get("forecast_20m", 0) or 0)
f30 = float(latest.get("forecast_30m", 0) or 0)

forecast_cols = st.columns(4)
for column, minutes, forecast in zip(
    forecast_cols,
    (5, 10, 20, 30),
    (f5, f10, f20, f30),
):
    direction = forecast_label(forecast)

    if forecast >= 0.35:
        grounded_price = fmt(target1)
        basis = latest.get("target1_basis", "실제 차트 1차 저항")
    elif forecast <= -0.35:
        grounded_price = fmt(latest.get("structural_support"))
        basis = latest.get("stop_basis", "실제 차트 지지")
    else:
        grounded_price = (
            f"{fmt(latest.get('structural_support'))}<br>~ {fmt(target1)}"
        )
        basis = "확인된 지지·1차 저항 사이"

    column.markdown(
        f'<div class="forecast-card">'
        f'<div class="title">{minutes}분 판정 · {direction}</div>'
        f'<div class="price">{grounded_price}</div>'
        f'<div class="basis">{basis}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

forecasts = (f5, f10, f20, f30)
if level == "success" and all(value >= 0.35 for value in forecasts):
    st.success("🚦 매수 전 조건 통과 · 5·10·20·30분 모두 상승 우세")
elif any(value <= -0.35 for value in forecasts):
    st.error("⛔ 단기 예상에 하락 위험 구간이 있습니다. 신규 진입을 보류하세요.")
else:
    st.warning("⏳ 시간대별 방향이 완전히 정렬되지 않았습니다. 진입을 기다리세요.")

st.caption(
    "1차·2차 목표 가격은 +1%/+2% 고정값이 아니라 실제 차트 저항/돌파 구조에서 계산합니다. "
    "+1/+2/+3%는 자동검증 통계에서만 별도로 사용됩니다."
)


# 차트
st.subheader("실시간 차트 분석 · 지지 / 1차 / 2차 목표")
st.caption("5·15·60분으로 방향을 확인하고, 아래 1분봉에서 실제 지지·1차·2차 목표를 표시합니다.")
render_chart(latest)


# 뉴스/공시 위험 메뉴 복원 — scalp_latest를 덮어쓰지 않음
with st.expander("진입 전 뉴스·공시 위험을 지금 한 번 확인"):
    if st.button("뉴스·SEC·거래정지 확인", use_container_width=True):
        with st.spinner("위험자료 확인 중..."):
            try:
                checked = scanner().analyze_candidate(selected_row, mode)
                st.session_state["scalp_risk_check"] = checked
            except Exception as error:
                st.error(str(error))

    checked = st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker")) == selected_ticker:
        st.write(checked.get("news_summary", "뉴스 확인 완료"))
        st.write(
            "규제검증:",
            checked.get("regulatory_checked", False),
            "· 거래정지:",
            checked.get("halt_active", False),
        )


# 자동 적중률 기록 메뉴 복원
audit_records = (
    update_prediction_audit(selected_ticker, price, latest, now)
    if require_validation
    else []
)

completed = []
for record in audit_records:
    if record["ticker"] != selected_ticker:
        continue
    for minutes in (5, 10, 20, 30):
        if f"실제{minutes}분" in record:
            completed.append({
                "기준시각": record["기준시각"],
                "구간": f"{minutes}분",
                "예상(%)": record[f"예상{minutes}분"],
                "실제(%)": record[f"실제{minutes}분"],
                "방향 적중": "적중" if record[f"적중{minutes}분"] else "실패",
            })

with st.expander("자동 적중률 검증 기록", expanded=False):
    stat_cols = st.columns(4)
    for column, minutes in zip(stat_cols, (5, 10, 20, 30)):
        stat = calibration[minutes]
        column.metric(
            f"{minutes}분 검증",
            f"{stat['accuracy']:.1f}%",
            f"표본 {stat['samples']}건 · 평균오차 {stat['mae']:.2f}%",
        )

    if completed:
        execution_accuracy = (
            sum(row["방향 적중"] == "적중" for row in completed)
            / len(completed)
            * 100
        )
        st.metric(
            "이번 앱 기록 방향 적중률",
            f"{execution_accuracy:.1f}%",
            f"검증 {len(completed)}건",
        )
        st.dataframe(
            pd.DataFrame(completed[-30:]),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("신호를 자동 저장했습니다. 해당 시간 경과 후 실제 결과와 비교합니다.")


st.caption(
    f"마지막 정밀분석: {latest.get('updated_at', '-')} · "
    f"화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}"
)
