# -*- coding: utf-8 -*-
"""초단타 반복매매 전용 Streamlit 앱.

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
import gc
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

AUDIT_IMPORT_ERROR = "UI에서는 검증기를 직접 실행하지 않습니다. 별도 프로세스로 실행하세요."
AUDIT_KR_UNIVERSE = []
AUDIT_US_UNIVERSE = []

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
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,ticker TEXT NOT NULL,issued REAL NOT NULL,
        base_price REAL NOT NULL,f5 REAL NOT NULL DEFAULT 0,f10 REAL NOT NULL DEFAULT 0,
        f15 REAL NOT NULL DEFAULT 0,f20 REAL NOT NULL DEFAULT 0,f30 REAL NOT NULL DEFAULT 0,
        f60 REAL NOT NULL DEFAULT 0,
        actual5 REAL,actual10 REAL,actual15 REAL,actual20 REAL,actual30 REAL,actual60 REAL,
        UNIQUE(ticker,issued))""")
    # 기존 DB는 삭제하지 않고 컬럼만 추가한다.
    existing={row[1] for row in con.execute("PRAGMA table_info(predictions)")}
    for name,ddl in (
        ("f15","REAL NOT NULL DEFAULT 0"),("f60","REAL NOT NULL DEFAULT 0"),
        ("actual15","REAL"),("actual60","REAL")
    ):
        if name not in existing:
            con.execute(f"ALTER TABLE predictions ADD COLUMN {name} {ddl}")
    con.execute("""CREATE TABLE IF NOT EXISTS prediction_quotes(
        ticker TEXT NOT NULL,captured INTEGER NOT NULL,price REAL NOT NULL,
        PRIMARY KEY(ticker,captured))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_prediction_quotes ON prediction_quotes(ticker,captured)")
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
from scanner.kis_engine import KISUnifiedScanner,apply_mode_policy,finalize_trade_item,yahoo_screen  # noqa:E402

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
    gate_rows=checks[:6]
    if spread is not None:
        gate_rows=checks
    return checks,all(bool(r["통과"]) for r in gate_rows),spread


def _dedupe_price_levels(levels:list[float],tol=0.08):
    result=[]
    for level in sorted(float(x) for x in levels if float(x or 0)>0):
        if not result: result.append(level); continue
        if abs(level/result[-1]-1)*100<=tol: result[-1]=max(result[-1],level)
        else: result.append(level)
    return result


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

def _adapt_repeat_overlay_for_ui(item: dict) -> dict:
    """반복단타 전용 계산값을 기존 초단타 화면 필드와 동기화한다."""
    if not isinstance(item, dict):
        return item
    width = _num(item.get("repeat_width_percent"))
    state = str(item.get("repeat_state") or "WAIT_TREND")
    if width < 0.50 and item.get("repeat_chart_valid"):
        legacy_state = "RANGE_TOO_NARROW"
    elif width > 1.50 and item.get("repeat_chart_valid"):
        legacy_state = "RANGE_TOO_WIDE"
    else:
        legacy_state = {
            "BUY_ZONE": "BUY_PULLBACK",
            "WAIT_PULLBACK": "WAIT_PULLBACK",
            "TAKE_PROFIT": "TAKE_PROFIT",
            "BREAKDOWN": "EXIT",
            "WAIT_TREND": "WAIT_TREND",
        }.get(state, "WAIT_TREND")

    cont = str(item.get("repeat_continuation_state") or "NONE")
    legacy_cont = {
        "HIGH": "STRONG", "MID": "WATCH", "LOW": "LIMITED", "NONE": "NO_TARGET2"
    }.get(cont, "NO_TARGET2")

    entry = _num(item.get("repeat_entry"))
    support = _num(item.get("repeat_support"))
    stop = _num(item.get("repeat_stop"))
    target1 = _num(item.get("repeat_target1"))
    target2 = _num(item.get("repeat_target2"))
    extra = _num(item.get("repeat_extra_after_target1"))
    rr = _num(item.get("repeat_risk_reward"))
    trend_score = int(_num(item.get("repeat_trend_score")))

    item.update(
        structural_entry=entry,
        structural_support=support,
        structural_target=target1,
        structural_target1=target1,
        structural_target2=target2,
        stop_loss=stop,
        target1_upside_percent=_num(item.get("repeat_target1_current_upside")),
        target2_upside_percent=_num(item.get("repeat_target2_current_upside")),
        risk_reward=rr,
        risk_reward_target1=rr,
        level_plan_valid=bool(item.get("repeat_chart_valid")),
        level_plan_reason=str(item.get("repeat_chart_reason") or ""),
        target_basis=str(item.get("repeat_target1_basis") or ""),
        target1_basis=str(item.get("repeat_target1_basis") or ""),
        target2_basis=str(item.get("repeat_target2_basis") or ""),
        stop_basis=f"{item.get('repeat_support_basis', '차트 지지')} 아래 ATR 완충 손절",
        chart_box_high=_num(item.get("repeat_chart_box_high")),
        chart_box_low=_num(item.get("repeat_chart_box_low")),
        chart_box_width=max(0.0, _num(item.get("repeat_chart_box_high")) - _num(item.get("repeat_chart_box_low"))),
        continuous_rise=trend_score >= 7,
        continuous_rise_score=trend_score,
        continuous_rise_checks=item.get("repeat_trend_checks") or {},
        repeat_scalp_state=legacy_state,
        repeat_scalp_label=str(item.get("repeat_label") or ""),
        repeat_scalp_reason=str(item.get("repeat_chart_reason") or ""),
        repeat_scalp_buy_level=entry,
        repeat_scalp_sell_level=target1,
        repeat_scalp_invalidation=stop,
        repeat_scalp_median_bar_range=_num(item.get("repeat_median_range")),
        repeat_scalp_range_percent=width,
        repeat_scalp_preferred_range=bool(item.get("repeat_preferred_range")),
        repeat_scalp_can_extend=cont == "HIGH",
        repeat_scalp_extension_label=str(item.get("repeat_continuation_label") or ""),
        repeat_scalp_extension_reason=f"추가상승 근거 {int(_num(item.get('repeat_continuation_score')))}/10",
        repeat_scalp_extension_percent=extra,
        upside_continuation_state=legacy_cont,
        upside_continuation_label=str(item.get("repeat_continuation_label") or ""),
        upside_continuation_score=int(_num(item.get("repeat_continuation_score"))),
        upside_continuation_checks=item.get("repeat_continuation_checks") or {},
        additional_upside_after_target1=extra,
        target2_total_upside=_num(item.get("repeat_target2_current_upside")),
        verified_spread_percent=item.get("repeat_spread_percent"),
    )
    return item


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


def box_regime_plan(item:dict):
    """진짜 왕복 박스인지 별도로 검증한다."""
    closes=[float(x or 0) for x in (item.get("chart_close_1m",[]) or []) if float(x or 0)>0]
    highs=[float(x or 0) for x in (item.get("chart_high_1m",[]) or []) if float(x or 0)>0]
    lows=[float(x or 0) for x in (item.get("chart_low_1m",[]) or []) if float(x or 0)>0]
    if len(closes)<45:
        item.update(box_state="UNAVAILABLE",box_label="⚪ 박스 자료 형성 중")
        return item

    look=min(120,len(closes))
    c=closes[-look:]; h=highs[-look:]; l=lows[-look:]
    hi=max(h); lo=min(l)
    width=(hi/lo-1)*100 if hi>lo>0 else 0.0
    band=max((hi-lo)*0.12, lo*0.0015)
    lower_touches=sum(1 for x in l if x<=lo+band)
    upper_touches=sum(1 for x in h if x>=hi-band)

    mid=(hi+lo)/2
    crossings=0
    prev=None
    for x in c:
        side=1 if x>mid else -1
        if prev is not None and side!=prev: crossings+=1
        prev=side

    series=pd.Series(c)
    ema20=series.ewm(span=20,adjust=False).mean()
    slope=(float(ema20.iloc[-1])/float(ema20.iloc[max(0,len(ema20)-11)])-1)*100 if len(ema20)>10 else 0.0
    pos=(c[-1]-lo)/(hi-lo) if hi>lo else 0.5

    valid=(1.0<=width<=2.2 and lower_touches>=2 and upper_touches>=2 and crossings>=3 and abs(slope)<=0.30)
    break_risk=valid and pos<=0.10 and c[-1]<float(ema20.iloc[-1])

    if break_risk:
        state,label="RANGE_BREAK_RISK","🟠 박스 하단 이탈 위험"
    elif valid:
        state,label="RANGE","🟦 박스 반복단타 가능"
    else:
        state,label="NOT_RANGE","⚪ 박스 조건 미달"

    item.update(
        box_state=state,
        box_label=label,
        box_width_percent=width,
        box_low=lo,
        box_high=hi,
        box_lower_touches=lower_touches,
        box_upper_touches=upper_touches,
        box_mid_crossings=crossings,
        box_position=pos,
        box_ema20_slope=slope,
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


def _compact_discovery_row(row:dict, market:str) -> dict:
    """후보 검색 단계에서는 큰 차트/분봉/원본응답을 버리고 작은 스칼라만 남긴다."""
    c=dict(row or {})
    ticker=str(c.get("ticker") or c.get("code") or "").upper().strip()
    return {
        "ticker":ticker,
        "name":str(c.get("name") or c.get("stock_name") or ticker),
        "exchange":str(c.get("exchange") or c.get("market") or ("KR" if market=="국내" else "NASDAQ")),
        "asset_type":str(c.get("asset_type") or "동적후보"),
        "screen_price":float(c.get("screen_price") or c.get("price") or c.get("current_price") or 0),
        "screen_change":float(c.get("screen_change") if c.get("screen_change") is not None else c.get("change_percent",c.get("change",0)) or 0),
        "screen_volume":int(float(c.get("screen_volume") or c.get("volume") or c.get("accumulated_volume") or 0)),
    }


def _trim_heavy_item(item:dict, keep_bars:int=360) -> dict:
    """실시간 분석 결과에서 화면/전략에 필요한 최근 분봉만 남겨 메모리 사용을 제한한다."""
    if not isinstance(item,dict):
        return item
    bar_keys=(
        "chart_time_1m","chart_open_1m","chart_high_1m","chart_low_1m","chart_close_1m",
        "chart_volume_1m","chart_vwap_1m","chart_ema9_1m","chart_ema20_1m","chart_signal_1m",
    )
    for key in bar_keys:
        value=item.get(key)
        if isinstance(value,(list,tuple)) and len(value)>keep_bars:
            item[key]=list(value[-keep_bars:])
    # 엔진이 부가적으로 붙이는 대형 원본/데이터프레임은 UI에서 사용하지 않는다.
    for key in list(item.keys()):
        value=item.get(key)
        if key.startswith("raw_") or key in {"raw_response","raw_payload","intraday_frame","minute_frame","history_frame","orderbook_raw"}:
            item.pop(key,None)
            continue
        try:
            if isinstance(value,pd.DataFrame):
                item.pop(key,None)
        except Exception:
            pass
    item["intraday_bar_count"]=min(int(item.get("intraday_bar_count",keep_bars) or keep_bars),keep_bars)
    return item


@st.cache_data(ttl=90,show_spinner=False,max_entries=4)
def discovery_snapshot(market:str):
    """거래량 상위 모집단을 먼저 만든 뒤 KIS 정밀분석으로 재검증한다."""
    ranked={}
    raw_count=0

    def absorb(rows,source):
        nonlocal raw_count
        for row in rows or []:
            raw_count+=1
            c=_compact_discovery_row(row,market)
            ticker=c.get("ticker","")
            if not ticker:
                continue
            old=ranked.get(ticker,{})
            ranked[ticker]={
                "ticker":ticker,
                "name":c.get("name") or old.get("name") or ticker,
                "exchange":c.get("exchange") or old.get("exchange") or ("KR" if market=="국내" else "NASDAQ"),
                "asset_type":c.get("asset_type") or old.get("asset_type") or "동적후보",
                "screen_price":c.get("screen_price") or old.get("screen_price",0),
                "screen_change":c.get("screen_change") if c.get("screen_change") is not None else old.get("screen_change",0),
                "screen_volume":max(int(old.get("screen_volume",0) or 0),int(c.get("screen_volume",0) or 0)),
                "discovery_source":source if not old.get("discovery_source") else old.get("discovery_source"),
            }

    if market=="국내":
        # KIS 국내 거래량 순위 API가 먼저다.
        try: absorb(scanner().candidates("국내 우량주"),"KIS 거래량순위")
        except Exception: pass
        # 돌파 목록은 보조로 합치되 거래량 정렬보다 우선하지 않는다.
        try: absorb(scanner().candidates("국내 돌파"),"KIS 돌파보조")
        except Exception: pass
    else:
        # 미국은 후보 발견만 Yahoo most_actives를 사용하고 최종 현재가/분봉/호가는 KIS로 재검증한다.
        try: absorb(yahoo_screen("most_actives"),"Most Actives")
        except Exception: pass
        try: absorb(scanner().candidates("미국 30분 1% 타점"),"모멘텀보조")
        except Exception: pass

    stage1=[]
    for c in ranked.values():
        price=float(c.get("screen_price",0) or 0)
        change=float(c.get("screen_change",0) or 0)
        volume=int(c.get("screen_volume",0) or 0)
        value=price*volume
        if market=="국내":
            valid=1000<=price<=500000 and -3.0<=change<25 and volume>=100000 and value>=5_000_000_000
        else:
            valid=0.20<=price<=500 and -5.0<=change<80 and volume>=100000 and value>=2_000_000
        if not valid:
            continue
        # 후보 모집 단계는 거래량을 절대 우선한다. 등락률은 거의 가중하지 않는다.
        liquidity_score=(min(math.log10(max(volume,1)),10)*8
                         +min(math.log10(max(value,1)),15)*3
                         +min(max(change,0),10)*0.05)
        d=dict(c); d["screen_value"]=value; d["discovery_score"]=liquidity_score
        stage1.append(d)

    stage1.sort(key=lambda r:(int(r.get("screen_volume",0) or 0),float(r.get("screen_value",0) or 0)),reverse=True)
    stage1=stage1[:30]
    fallback_used=False
    if not stage1:
        fallback_used=True
        source=KR_UNIVERSE if market=="국내" else US_UNIVERSE
        stage1=[dict(r,screen_price=0,screen_volume=0,screen_change=0,screen_value=0,discovery_score=0,discovery_source="고정 안전망") for r in source[:12]]
    return {"raw_count":raw_count,"unique_count":len(ranked),"stage1_count":len(stage1),"rows":stage1,"fallback_used":fallback_used}


def live_filtered_universe(market:str):
    return discovery_snapshot(market)["rows"]


@st.cache_data(ttl=55,show_spinner=False,max_entries=8)


def _candidate_public_view(item:dict,row:dict,market:str):
    """정밀분석 결과에서 후보표에 필요한 작은 값만 남긴다."""
    width=float(item.get("repeat_scalp_range_percent",0) or 0)
    box_width=float(item.get("box_width_percent",0) or 0)
    trend_state=str(item.get("intraday_regime_state","UNKNOWN"))
    box_state=str(item.get("box_state","UNAVAILABLE"))
    repeat_ok=0.5<=width<=1.5
    box_ok=box_state=="RANGE"

    score=float(item.get("score",0) or 0)
    rr=float(item.get("risk_reward",0) or 0)
    spread=item.get("verified_spread_percent")
    try:
        spread=float(spread) if spread is not None else None
    except Exception:
        spread=None

    quality=bool(item.get("data_gate_passed")) and bool(item.get("repeat_quality_pass",False)) and bool(item.get("level_plan_valid"))
    if not quality or rr<1.0:
        return None
    if spread is not None and spread>(0.35 if market=="국내" else 0.25):
        return None

    trade_type=None
    priority=0
    if trend_state=="UPTREND_PULLBACK" and repeat_ok:
        trade_type="우상향 반복단타"
        stage="🟢 정상 눌림 · 재매수 후보"
        priority=6
    elif trend_state=="STRONG_UPTREND" and repeat_ok:
        trade_type="우상향 반복단타"
        stage="🟢 강한 우상향 · 눌림 대기"
        priority=5
    elif box_ok:
        trade_type="박스 반복단타"
        stage="🟦 박스 하단 재매수 대기"
        priority=5
    else:
        return None

    t1=float(item.get("structural_target1",item.get("structural_target",0)) or 0)
    t2=float(item.get("structural_target2",0) or 0)
    support=float(item.get("structural_support",0) or 0)
    stop=float(item.get("stop_loss",0) or 0)

    if trade_type=="박스 반복단타":
        box_low=float(item.get("box_low",0) or 0)
        box_high=float(item.get("box_high",0) or 0)
        if box_low>0:
            support=box_low
        if box_high>support:
            t1=box_high
        atr=float(item.get("repeat_atr14",0) or 0)
        if support>0:
            stop=max(0.0,support-max(atr*0.35,support*0.0020))

    screen_volume=int(row.get("screen_volume",0) or 0)
    screen_value=float(row.get("screen_value",0) or 0)
    rvol=float(item.get("rvol",0) or 0)
    f60=(item.get("forward_forecasts",{}).get(60,{}) or {})
    f60_prob=float(f60.get("up_probability",50) or 50)

    rank=(priority*100
          + int(item.get("intraday_uptrend_score",0) or 0)*12
          + min(rvol,5)*5
          + min(math.log10(max(screen_volume,1)),9)*6
          + min(math.log10(max(screen_value,1)),14)*2
          + score + (f60_prob-50)*0.8)

    return {
        "ticker":str(item.get("ticker") or row.get("ticker")),
        "name":str(item.get("name") or row.get("name") or row.get("ticker")),
        "stage":stage,
        "trade_type":trade_type,
        "regime_label":str(item.get("intraday_regime_label","-")),
        "hourly_label":str(item.get("hourly_structure_label","-")),
        "hourly_bull_score":int(item.get("hourly_structure_score",0) or 0),
        "hourly_bear_score":int(item.get("hourly_structure_bear_score",0) or 0),
        "hourly_bars":int(item.get("hourly_bars",0) or 0),
        "box_label":str(item.get("box_label","-")),
        "price":float(item.get("price",0) or 0),
        "score":score,
        "rvol":rvol,
        "risk_reward":rr,
        "rank":rank,
        "repeat_width":width,
        "box_width":box_width,
        "support":support,
        "target1":t1,
        "target2":t2,
        "stop":stop,
        "screen_volume":screen_volume,
        "screen_value":screen_value,
        "spread":spread,
        "return_15m":float(item.get("intraday_return_15m",0) or 0),
        "return_30m":float(item.get("intraday_return_30m",0) or 0),
        "return_60m":float(item.get("intraday_return_60m",0) or 0),
        "session_return":float(item.get("intraday_session_return",0) or 0),
        "vwap_hold":float(item.get("intraday_vwap_hold_ratio",0) or 0),
        "reclaim_count":int(item.get("intraday_reclaim_count",0) or 0),
        "box_lower_touches":int(item.get("box_lower_touches",0) or 0),
        "box_upper_touches":int(item.get("box_upper_touches",0) or 0),
        "box_crossings":int(item.get("box_mid_crossings",0) or 0),
        "downtrend_confirmed":bool(item.get("intraday_downtrend_confirmed")),
        "forward_forecasts":item.get("forward_forecasts",{}) or {},
        "exchange":str(row.get("exchange") or ("KR" if market=="국내" else "NASDAQ")),
        "row":dict(row),
        "_seen_at":time.time(),
    }


def dynamic_repeat_candidates(market:str,minimum_score:float,limit:int=6):
    """빠른 UI용 증분 스캔.

    한 번의 Streamlit rerun에서 정밀분석은 딱 1종목만 한다.
    최근 4분간 분석된 좋은 후보는 session_state에 작은 dict로 누적한다.
    """
    snap=discovery_snapshot(market)
    rows=list(snap["rows"])
    if not rows:
        return {"raw_count":snap["raw_count"],"unique_count":snap["unique_count"],
                "stage1_count":0,"analyzed_count":0,"width_pass_count":0,
                "final_count":0,"fallback_used":snap["fallback_used"],"rows":[]}

    mode="국내 30분 1% 타점" if market=="국내" else "미국 30분 1% 타점"
    cursor_key=f"fast_scan_cursor::{market}"
    cache_key=f"fast_candidate_cache::{market}"

    cursor=int(st.session_state.get(cursor_key,0)) % len(rows)
    row=rows[cursor]
    st.session_state[cursor_key]=(cursor+1)%len(rows)

    cache=dict(st.session_state.get(cache_key,{}) or {})
    now_ts=time.time()

    # 4분 지난 후보 제거
    cache={k:v for k,v in cache.items() if now_ts-float(v.get("_seen_at",0) or 0)<=240}

    analyzed=0
    width_pass=0
    try:
        item=precise_analysis(dict(row),mode,fast_scan=True)
        analyzed=1
        width=float(item.get("repeat_scalp_range_percent",0) or 0)
        box_ok=str(item.get("box_state",""))=="RANGE"
        if 0.5<=width<=1.5 or box_ok:
            width_pass=1
        candidate=_candidate_public_view(item,row,market)
        if candidate and float(candidate.get("score",0) or 0)>=minimum_score:
            cache[str(candidate["ticker"])]=candidate
        else:
            # 해당 종목이 더 이상 유효하지 않으면 예전 후보도 제거
            cache.pop(str(row.get("ticker") or ""),None)
    except Exception:
        pass

    st.session_state[cache_key]=cache
    final=sorted(cache.values(),key=lambda x:float(x.get("rank",0) or 0),reverse=True)[:limit]
    return {
        "raw_count":snap["raw_count"],"unique_count":snap["unique_count"],
        "stage1_count":snap["stage1_count"],"analyzed_count":analyzed,
        "width_pass_count":width_pass,"final_count":len(final),
        "fallback_used":snap["fallback_used"],"scan_cursor":cursor+1,
        "scan_total":len(rows),"rows":final,
    }


def _locked_entry_plan(ticker:str, proposed:float, stop:float, target1:float, price:float, now_ts:float):
    """새로고침 때 진입 기준가가 흔들리지 않도록 종목별 계획을 잠근다.

    - 최초 후보 확정 시 proposed(차트 지지/반복매수 기준)를 저장
    - 기본 15분 유지
    - 손절선 이탈 또는 1차 목표 도달 시 즉시 새 계획 허용
    - 최소 3분 경과 후 새 지지선이 기존 기준에서 0.40% 이상 이동하면 구조 변경으로 재설정
    """
    locks=st.session_state.setdefault("scalp_entry_locks",{})
    ticker=str(ticker or "").upper()
    proposed=float(proposed or 0); stop=float(stop or 0); target1=float(target1 or 0); price=float(price or 0)
    rec=locks.get(ticker)

    reset=False
    if rec:
        locked=float(rec.get("entry",0) or 0)
        age=max(0.0,now_ts-float(rec.get("locked_at",now_ts) or now_ts))
        structural_shift=(locked>0 and proposed>0 and abs(proposed/locked-1)*100>=0.40 and age>=180)
        expired=age>=900
        invalidated=(stop>0 and price<=stop) or (target1>0 and price>=target1)
        reset=expired or structural_shift or invalidated

    if not rec or reset or float(rec.get("entry",0) or 0)<=0:
        entry=proposed if proposed>0 else price
        rec={"entry":entry,"locked_at":now_ts}
        locks[ticker]=rec

    entry=float(rec.get("entry",0) or 0)
    # 한 호가가 아니라 진입 구간으로 표시: 기준가 ±0.15%
    zone_low=entry*0.9985 if entry>0 else 0
    zone_high=entry*1.0015 if entry>0 else 0
    return entry,zone_low,zone_high,int(max(0,(now_ts-float(rec.get("locked_at",now_ts)))/60))


def _apply_entry_locks_to_board(rows:list[dict], now_ts:float):
    out=[]
    for c in rows or []:
        d=dict(c)
        entry,lo,hi,age=_locked_entry_plan(
            d.get("ticker",""), d.get("support",0), d.get("stop",0),
            d.get("target1",0), d.get("price",0), now_ts
        )
        d["locked_entry"]=entry
        d["entry_zone_low"]=lo
        d["entry_zone_high"]=hi
        d["entry_lock_age_min"]=age
        if entry>0 and float(d.get("target1",0) or 0)>entry:
            d["repeat_width_locked"]=(float(d["target1"])/entry-1)*100
        else:
            d["repeat_width_locked"]=float(d.get("repeat_width",0) or 0)
        out.append(d)
    return out


def precise_analysis(row:dict,mode:str,fast_scan:bool=False):
    raw=scanner().analyze(dict(row),mode)
    item=_trim_heavy_item(apply_mode_policy(finalize_trade_item(raw),mode),360)
    del raw
    market="국내" if mode.startswith("국내") else "미국"
    if not fast_scan:
        for _ in range(2):
            if float(item.get("best_bid",0) or 0)>0 and float(item.get("best_ask",0) or 0)>0:
                break
            try:
                refreshed=scanner().refresh_quotes([item],mode)
                if refreshed:
                    item.update(refreshed[0])
            except Exception:
                pass
            time.sleep(0.12)
        if market=="미국":
            item=normalize_us_item(item,row)

    # 상위시간대는 방향 필터용으로 유지하고, 반복단타 가격은 별도 1분봉 차트 엔진으로 계산한다.
    item=multi_timeframe_plan(item,market)
    item=hourly_structure_plan(item,market)
    item=apply_repeat_scalp_overlay(item,"KR" if market=="국내" else "US")
    item=_adapt_repeat_overlay_for_ui(item)
    item=intraday_regime_plan(item,market)
    item=box_regime_plan(item)
    _,gate,spread=data_quality_gate(item,market)
    if spread is not None:
        item["verified_spread_percent"]=spread
    item=forward_forecast_plan(item,market)
    item["data_gate_passed"]=bool(gate and item.get("repeat_quality_pass",False))
    return _trim_heavy_item(item,360)


def background_audit_tick(enabled:bool,now_ts:float,ui_market:str):
    """UI 프로세스에서는 자동검증을 돌리지 않는다.

    검증기는 별도 터미널에서 run_live_validation.py로 실행해야
    Streamlit 화면이 KIS 분석/DB 채점 때문에 멈추지 않는다.
    """
    if enabled:
        st.session_state["audit_last_ok"] = "검증기는 별도 프로세스에서 실행"
    return


def render_chart(item:dict):
    times=item.get("chart_time_1m",[]) or []; closes=item.get("chart_close_1m",[]) or []; opens=item.get("chart_open_1m",[]) or []; highs=item.get("chart_high_1m",[]) or []; lows=item.get("chart_low_1m",[]) or []; vw=item.get("chart_vwap_1m",[]) or []; e9=item.get("chart_ema9_1m",[]) or []; e20=item.get("chart_ema20_1m",[]) or []; sig=item.get("chart_signal_1m",[]) or []
    n=min(map(len,(times,closes,opens,highs,lows,vw,e9,e20,sig))) if times else 0
    if n<=0: st.info("1분봉 수집 중"); return
    n=min(n,120); times=times[-n:]; closes=closes[-n:]; opens=opens[-n:]; highs=highs[-n:]; lows=lows[-n:]; vw=vw[-n:]; e9=e9[-n:]; e20=e20[-n:]; sig=sig[-n:]
    df=pd.DataFrame({"시간":pd.to_datetime(times,errors="coerce"),"시가":opens,"고가":highs,"저가":lows,"종가":closes,"VWAP":vw,"EMA9":e9,"EMA20":e20,"신호":sig}).dropna(subset=["시간"]); df["색상"]=df.apply(lambda r:"상승" if r["종가"]>=r["시가"] else "하락",axis=1); color=alt.Scale(domain=["상승","하락"],range=["#ef5350","#2962ff"]); base=alt.Chart(df).encode(x=alt.X("시간:T",title=None)); wick=base.mark_rule().encode(y=alt.Y("저가:Q",scale=alt.Scale(zero=False)),y2="고가:Q",color=alt.Color("색상:N",scale=color,legend=None)); body=base.mark_bar(size=7).encode(y="시가:Q",y2="종가:Q",color=alt.Color("색상:N",scale=color,legend=None)); lines=alt.Chart(df).transform_fold(["VWAP","EMA9","EMA20"],as_=["지표","값"]).mark_line().encode(x="시간:T",y=alt.Y("값:Q",scale=alt.Scale(zero=False)),color="지표:N"); chart=wick+body+lines
    levels=[]; entry=float(item.get("structural_entry",0) or 0); support=float(item.get("structural_support",0) or 0); stop=float(item.get("stop_loss",0) or 0); t1=float(item.get("structural_target1",item.get("structural_target",0)) or 0); t2=float(item.get("structural_target2",0) or 0)
    if entry>0: levels.append({"가격":entry,"구간":"반복 매수가"})
    if support>0 and abs(support-entry)/max(entry,1e-9)>0.0001: levels.append({"가격":support,"구간":"차트 지지"})
    if stop>0: levels.append({"가격":stop,"구간":"손절가"})
    if t1>0: levels.append({"가격":t1,"구간":"1차 목표"})
    if t2>0: levels.append({"가격":t2,"구간":"2차 목표"})
    if levels:
        lf=pd.DataFrame(levels); chart=chart+alt.Chart(lf).mark_rule(strokeWidth=2,strokeDash=[6,4]).encode(y=alt.Y("가격:Q",scale=alt.Scale(zero=False)),color=alt.Color("구간:N",title=None))+alt.Chart(lf).mark_text(align="left",dx=5,dy=-5,fontWeight="bold").encode(y="가격:Q",text="구간:N",color=alt.Color("구간:N",legend=None))
    st.altair_chart(chart.properties(height=430),use_container_width=True)


def calibration_stats(ticker:str):
    stats={}
    with db_connect() as db:
        for m in (5,15,30,60):
            rows=db.execute(f"SELECT f{m},actual{m} FROM predictions WHERE ticker=? AND actual{m} IS NOT NULL ORDER BY issued DESC LIMIT 300",(ticker,)).fetchall()
            if not rows:
                stats[m]={"samples":0,"accuracy":0,"bias":0,"mae":0}
                continue
            errors=[float(e)-float(a) for e,a in rows]
            accuracy=sum((float(e)>=0)==(float(a)>=0) for e,a in rows)/len(rows)*100
            stats[m]={"samples":len(rows),"accuracy":accuracy,"bias":sum(errors)/len(errors),"mae":sum(abs(x) for x in errors)/len(errors)}
    return stats


def _grade_prediction_rows(db,ticker,now_ts):
    rows=db.execute("SELECT id,issued,base_price,actual5,actual15,actual30,actual60 FROM predictions WHERE ticker=? AND issued>=?",(ticker,now_ts-2*86400)).fetchall()
    for pid,issued,base,a5,a15,a30,a60 in rows:
        updates={}
        for m,existing in ((5,a5),(15,a15),(30,a30),(60,a60)):
            if existing is not None or now_ts<issued+m*60 or base<=0:
                continue
            target=int(issued+m*60)
            q=db.execute("SELECT price FROM prediction_quotes WHERE ticker=? AND captured BETWEEN ? AND ? ORDER BY ABS(captured-?) LIMIT 1",(ticker,target-180,target+180,target)).fetchone()
            if q:
                updates[f"actual{m}"]=(float(q[0])/float(base)-1)*100
        if updates:
            assignments=",".join(f"{k}=?" for k in updates)
            db.execute(f"UPDATE predictions SET {assignments} WHERE id=?",(*updates.values(),pid))


def update_prediction_audit(ticker,price,item,now_ts):
    if price<=0:
        return
    quote_bucket=int(now_ts//60)*60
    signal_bucket=float(int(now_ts//300)*300)
    ff=item.get("forward_forecasts",{}) or {}
    with db_connect() as db:
        db.execute("INSERT OR REPLACE INTO prediction_quotes(ticker,captured,price) VALUES(?,?,?)",(ticker,quote_bucket,price))
        db.execute("""INSERT OR IGNORE INTO predictions(ticker,issued,base_price,f5,f10,f15,f20,f30,f60)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (ticker,signal_bucket,price,
                    float((ff.get(5,{}) or {}).get("center_pct",item.get("forecast_5m",0)) or 0),
                    float(item.get("forecast_10m",0) or 0),
                    float((ff.get(15,{}) or {}).get("center_pct",item.get("forecast_15m",0)) or 0),
                    float(item.get("forecast_20m",0) or 0),
                    float((ff.get(30,{}) or {}).get("center_pct",item.get("forecast_30m",0)) or 0),
                    float((ff.get(60,{}) or {}).get("center_pct",item.get("forecast_60m",0)) or 0)))
        _grade_prediction_rows(db,ticker,int(now_ts))
        db.commit()


st.title("⚡ 초단타 VWAP 매수타점")
st.caption("거래량 상위 30개를 순환 분석하고, 우상향/박스 엔진을 분리합니다. 상단의 5·15·30·60분 값은 과거가 아니라 현재 차트 기반 향후 조건부 예상입니다.")
with st.sidebar:
    market=st.radio("시장",["국내","미국"],horizontal=True); session_info=market_clock(market); st.caption(f"{session_info['session']} · {session_info['local_time']}"); mode="국내 30분 1% 타점" if market=="국내" else "미국 30분 1% 타점"; minimum_score=st.slider("최소 점수",30,90,50,5); manual_ticker=st.text_input("종목명 또는 종목코드 검색",placeholder="현대차, 005380, SOXL").strip(); run_mode=st.radio("실행 모드",["빠른 자동스캔","선택 종목 집중","검증기 상태"],key="scalp_run_mode"); focus_only=run_mode=="선택 종목 집중"; auto_audit=run_mode=="검증기 상태"; require_validation=st.toggle("실전 검증 잠금",True); st.caption("반복단타 기본폭 0.5~1.5%")
now=time.time(); live_refresh_active=True; st_autorefresh(interval=3000 if focus_only else 5000,key="scalp_tick")
if auto_audit: st.caption("자동검증은 별도 run_live_validation.py 프로세스에서 실행합니다.")

startup_key=f"fast_ui_started::{market}"
first_paint=not bool(st.session_state.get(startup_key))
if first_paint:
    st.session_state[startup_key]=True
    dynamic_board={"rows":[],"raw_count":0,"unique_count":0,"stage1_count":0,"analyzed_count":0,"width_pass_count":0,"final_count":0,"fallback_used":False}
else:
    dynamic_board={"rows":[],"raw_count":0,"unique_count":0,"stage1_count":0,"analyzed_count":0,"width_pass_count":0,"final_count":0,"fallback_used":False} if manual_ticker else dynamic_repeat_candidates(market,minimum_score,6)
candidate_board=_apply_entry_locks_to_board(dynamic_board["rows"],now)
st.subheader("실시간 동적 반복단타 후보")
if not manual_ticker:
    f1,f2,f3,f4=st.columns(4)
    f1.metric("실시간 검색",f"{dynamic_board['unique_count']}종목")
    f2.metric("1차 유동성 통과",f"{dynamic_board['stage1_count']}종목")
    f3.metric("이번 새로고침 분석",f"{dynamic_board['analyzed_count']}종목")
    f4.metric("최종 후보",f"{dynamic_board['final_count']}종목")
    if dynamic_board.get("fallback_used"): st.warning("실시간 순위 API 후보가 없어 고정 유니버스를 임시 안전망으로 사용 중입니다.")
if candidate_board:
    st.dataframe(pd.DataFrame([{
        "유형":c.get("trade_type","-"),
        "큰 추세":c.get("regime_label","-"),
        "60분봉 구조":c.get("hourly_label","-"),
        "60분봉 수":int(c.get("hourly_bars",0) or 0),
        "박스판정":c.get("box_label","-"),
        "현재 상태":c["stage"],
        "종목":f"{c['ticker']} · {c['name']}",
        "현재가":c["price"],
        "재매수 구간":f"{fmt(c['entry_zone_low'])}~{fmt(c['entry_zone_high'])}",
        "1차 목표":c["target1"],
        "반복폭":f"{c['repeat_width_locked']:.2f}%",
        "세션":f"{float(c.get('session_return',0) or 0):+.2f}%",
        "15분":f"{float(c.get('return_15m',0) or 0):+.2f}%",
        "30분":f"{float(c.get('return_30m',0) or 0):+.2f}%",
        "최근60분":f"{float(c.get('return_60m',0) or 0):+.2f}%",
        "향후60분예상":f"{float(c.get('forecast60_low',0) or 0):+.2f}~{float(c.get('forecast60_high',0) or 0):+.2f}%",
        "60분상승확률":f"{float(c.get('forecast60_prob',0) or 0):.0f}%",
        "VWAP위체류":f"{float(c.get('vwap_hold',0) or 0)*100:.0f}%",
        "눌림회복":f"{int(c.get('reclaim_count',0))}회",
        "박스폭":f"{float(c.get('box_width',0) or 0):.2f}%",
        "하단/상단터치":f"{int(c.get('box_lower_touches',0))}/{int(c.get('box_upper_touches',0))}",
        "중앙왕복":f"{int(c.get('box_crossings',0))}회",
        "하락전환":"🔴 확인" if c.get("downtrend_confirmed") else "아님",
        "거래량":f"{int(c.get('screen_volume',0)):,}",
        "거래대금":round(c["screen_value"]/1000000,1),
        "RVOL":round(c["rvol"],1),
        "스프레드":f"{c['spread']:.3f}%" if c.get("spread") is not None else "-"
    } for c in candidate_board]),hide_index=True,use_container_width=True)
else: st.info("현재 실시간 시장에서 우상향 반복단타 또는 1~2% 왕복 박스 품질 조건을 통과한 후보가 없습니다.")

options=[]
if manual_ticker:
    resolved=resolve_manual(manual_ticker,market)
    if resolved: options=[resolved]
else:
    # 최종 후보가 있으면 그 종목을 우선 선택 목록에 올리고, 없으면 1차 동적 후보를 사용한다.
    final_rows=[dict(c.get("row") or {},ticker=c["ticker"],name=c["name"],exchange=c.get("exchange","NASDAQ")) for c in candidate_board]
    base_rows=live_filtered_universe(market)
    seen=set(); options=[]
    for r in final_rows+base_rows[:30]:
        t=str(r.get("ticker") or "").upper()
        if t and t not in seen: seen.add(t); options.append(r)
if not options: st.warning("자동 후보가 없습니다. 종목을 직접 검색해 주세요."); st.stop()
selected_ticker=st.selectbox("집중 분석할 종목",[str(r.get("ticker","")) for r in options],format_func=lambda t:next((f"{t} · {r.get('name',t)}" for r in options if str(r.get('ticker',''))==t),t)); selected_row=next(r for r in options if str(r.get("ticker",""))==selected_ticker); selected_row.setdefault("exchange","KR" if market=="국내" else "NASDAQ")
if st.session_state.get("scalp_selected")!=selected_ticker: st.session_state["scalp_selected"]=selected_ticker; st.session_state["scalp_last_precise"]=0.0; st.session_state.pop("scalp_latest",None)
latest=dict(st.session_state.get("scalp_latest",{})); refresh_seconds=5 if focus_only else 60 if auto_audit else 30; due=not latest or now-float(st.session_state.get("scalp_last_precise",0))>=refresh_seconds
if due:
    try: latest=precise_analysis(selected_row,mode); st.session_state["scalp_latest"]=latest; st.session_state["scalp_last_precise"]=now
    except Exception as e: st.error(f"분석 대기: {e}"); st.stop()
price=float(latest.get("price",0) or 0); change=float(latest.get("change_percent",0) or 0)
proposed_entry=float(latest.get("repeat_scalp_buy_level",latest.get("structural_support",latest.get("structural_entry",0))) or 0)
locked_entry,entry_zone_low,entry_zone_high,entry_lock_age=_locked_entry_plan(
    selected_ticker, proposed_entry,
    float(latest.get("stop_loss",0) or 0),
    float(latest.get("structural_target1",latest.get("structural_target",0)) or 0),
    price, now
)
latest["locked_entry"]=locked_entry
latest["entry_zone_low"]=entry_zone_low
latest["entry_zone_high"]=entry_zone_high
if price<=0: st.error("현재가 미확인"); st.stop()
try: update_prediction_audit(selected_ticker,price,latest,now)
except Exception: pass
quality_rows,quality_passed,_=data_quality_gate(latest,market); regime_name,regime_method=market_regime(latest)
intraday_label=str(latest.get("intraday_regime_label","⚪ 큰 추세 확인 중"))
intraday_reason=str(latest.get("intraday_regime_reason",""))
strategy_rows,buy_votes,sell_votes,wait_votes=strategy_consensus(latest); weighted_score,weighted_buy=weighted_strategy_score(strategy_rows,regime_name); context=benchmark_context(market,selected_ticker); context_aligned=bool(context.get("confirmed")) and (float(context.get("change",0) or 0)>=-0.05 and float(context.get("intraday",0) or 0)>=-0.10); rr=float(latest.get("risk_reward",0) or 0); state=str(latest.get("repeat_scalp_state","UNAVAILABLE")); width=float(latest.get("repeat_scalp_range_percent",0) or 0); support=float(latest.get("structural_support",0) or 0); stop=float(latest.get("stop_loss",0) or 0); t1=float(latest.get("structural_target1",latest.get("structural_target",0)) or 0); t2=float(latest.get("structural_target2",0) or 0)
if locked_entry>0 and t1>locked_entry: width=(t1/locked_entry-1)*100
cal=calibration_stats(selected_ticker) if require_validation else {m:{"samples":0,"accuracy":0,"bias":0,"mae":0} for m in (5,15,30,60)}; validated=cal[5]["samples"]>=20 and cal[15]["samples"]>=20 and cal[5]["accuracy"]>=55 and cal[15]["accuracy"]>=55
level="success"
if not quality_passed or not latest.get("repeat_quality_pass",False) or not latest.get("level_plan_valid") or not latest.get("repeat_candidate",False) or not context_aligned or rr<1.2 or state in {"EXIT","TAKE_PROFIT","RANGE_TOO_NARROW","RANGE_TOO_WIDE"} or sell_votes>=3: level="error"
elif require_validation and not validated: level="warning"
elif state not in {"BUY_PULLBACK","HOLD_OR_BREAKOUT"} or buy_votes<4: level="warning"
latest["entry_checks_passed"]=level=="success"
if level=="success": st.success(f"🟢 매수 검토 · 반복폭 {width:.2f}% · 1차 {fmt(t1)}")
elif state=="TAKE_PROFIT": st.warning(f"🟠 1차 목표 접근 · {fmt(t1)}")
else: st.info(f"🟡 대기 · 반복폭 {width:.2f}%")

ff=latest.get("forward_forecasts",{}) or {}
forecast_cols=st.columns(4)
for col,h in zip(forecast_cols,(5,15,30,60)):
    f=(ff.get(h,{}) or {})
    lo=float(f.get("low_pct",0) or 0); hi=float(f.get("high_pct",0) or 0)
    center=float(f.get("center_pct",0) or 0); prob=float(f.get("up_probability",0) or 0)
    col.metric(f"향후 {h}분 예상",f"{center:+.2f}%",f"범위 {lo:+.2f}~{hi:+.2f}% · 상승 {prob:.0f}%")
st.caption(
    f"조건부 예상 · {latest.get('forecast_method','차트 기반')} | "
    f"과거 실제: 15분 {float(latest.get('intraday_return_15m',0) or 0):+.2f}% · "
    f"30분 {float(latest.get('intraday_return_30m',0) or 0):+.2f}% · "
    f"60분 {float(latest.get('intraday_return_60m',0) or 0):+.2f}% | "
    f"큰 추세: {intraday_label} · {intraday_reason}"
)

cols=st.columns(6); cols[0].metric(f"{selected_ticker} · {latest.get('name','')}",fmt(price),f"{change:+.2f}%"); cols[1].metric("재매수 기준",fmt(locked_entry),f"{fmt(entry_zone_low)}~{fmt(entry_zone_high)} · {entry_lock_age}분 유지"); cols[2].metric("1차 목표가",fmt(t1),f"진입기준 +{width:.2f}%"); cols[3].metric("2차 목표가",fmt(t2) if t2>0 else "-",f"{float(latest.get('target2_upside_percent',0) or 0):+.2f}%" if t2>0 else None); cols[4].metric("현재 차트 지지",fmt(support)); cols[5].metric("손절가",fmt(stop))
ext_state=str(latest.get("upside_continuation_state","NO_TARGET2")); ext_label=str(latest.get("upside_continuation_label","⚪ 추가상승 미확인")); ext_pct=float(latest.get("additional_upside_after_target1",0) or 0); ext_score=int(latest.get("upside_continuation_score",0) or 0)
if ext_state=="STRONG": st.success(f"{ext_label} · 근거 {ext_score}/10 · 1차→2차 +{ext_pct:.2f}%")
elif ext_state=="WATCH": st.warning(f"{ext_label} · 근거 {ext_score}/10")
elif ext_state=="LIMITED": st.error(f"{ext_label} · 근거 {ext_score}/10")
else: st.info(ext_label)
st.caption(f"1차 근거: {latest.get('target1_basis','-')} · 2차 근거: {latest.get('target2_basis','-')}")

with st.expander("향후 5·15·30·60분 예상 상세",expanded=True):
    forecast_rows=[]
    for h in (5,15,30,60):
        f=(ff.get(h,{}) or {})
        forecast_rows.append({"시간":f"{h}분 후","중심예상":f"{float(f.get('center_pct',0) or 0):+.2f}%","예상범위":f"{float(f.get('low_pct',0) or 0):+.2f}~{float(f.get('high_pct',0) or 0):+.2f}%","예상가격":f"{fmt(f.get('low_price',0))} ~ {fmt(f.get('high_price',0))}","상승확률":f"{float(f.get('up_probability',0) or 0):.0f}%","모델신뢰":f"{float(f.get('confidence',0) or 0):.0f}%"})
    st.dataframe(pd.DataFrame(forecast_rows),hide_index=True,use_container_width=True)
    st.caption("예상범위는 확정 수익률이 아니라 현재 차트 구조가 유지될 때의 조건부 범위입니다. 실제 결과는 실행 중 자동 누적·채점됩니다.")

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
