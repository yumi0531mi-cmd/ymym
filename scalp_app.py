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
from scanner.kis_engine import KISUnifiedScanner,apply_mode_policy,finalize_trade_item,yahoo_screen

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
    """기존 엔진 결과를 보존하면서 반복단타 전용 차트 레벨 및 하락 차단 게이트를 추가한다."""
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
    f5 = _num(item.get("forecast_5m"))
    f10 = _num(item.get("forecast_10m"))
    f15 = _num(item.get("forecast_15m"))
    f30 = _num(item.get("forecast_30m"))
    prob_up = _num(item.get("prob_up_5m"), 50.0)
    model_conf = _num(item.get("model_confidence"), 50.0)

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
        "상승봉 거래량 우세": volume_ratio >= 1.05,
        "MACD 비약세": macd >= 0,
        "RVOL 확보": rvol >= 0.80,
        "5분 전망 양수": f5 > 0.0,
        "15분 전망 양수": f15 > 0.0,
        "5분 상승확률 우세": prob_up >= 50.0,
    }
    continuation_score = sum(bool(v) for v in continuation_checks.values())
    
    # 예측 모델의 하락 상태 검증 (음수 예측이나 상승확률 45% 미만시 충돌 발생)
    is_forecast_negative = (f5 < 0 or f10 < 0 or f15 < 0 or prob_up < 45.0)

    if target2 <= target1:
        continuation_state = "NONE"
        continuation_label = "⚪ 2차 목표 미확인"
    elif is_forecast_negative:
        continuation_state = "LOW"
        continuation_label = "🔴 예측 모델 하락 경고 (매수 주의)"
    elif continuation_score >= 8 and model_conf >= 50.0:
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
    elif price <= support + near and trend_score >= 6 and not is_forecast_negative:
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
        and not is_forecast_negative
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
    rr = _num(item.get("repeat_risk_reward"))

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
        target2_basis=str(item.get("repeat_target2_basis") or ""),
        support_basis=str(item.get("repeat_support_basis") or ""),
        scalp_state=legacy_state,
        continuation_state=legacy_cont,
    )
    return item
