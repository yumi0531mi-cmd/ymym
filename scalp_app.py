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
import math
import re
import requests
import sqlite3
import sys
import tempfile
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import regime_session_upgrade as _rsu

st.set_page_config(page_title="초단타 VWAP 타점", page_icon="⚡", layout="wide")

_REQUIRED_ENGINE_VERSION = "2026.08.13-v3"
_REQUIRED_ENGINE_SYMBOLS = (
    "_num", "apply_repeat_scalp_overlay", "box_regime_plan", "data_quality_plan",
    "forward_forecast_plan", "hourly_structure_plan", "intraday_regime_plan",
    "resolve_vwap_series", "session_for", "strategy_target_plan",
    "target_reach_probability_plan", "trade_decision_plan",
)
_engine_version = str(getattr(_rsu, "SCANNER_ENGINE_VERSION", "구버전/표시없음"))
_missing_engine_symbols = [name for name in _REQUIRED_ENGINE_SYMBOLS if not hasattr(_rsu, name)]
if _engine_version != _REQUIRED_ENGINE_VERSION or _missing_engine_symbols:
    st.error(
        "공통 엔진 파일 버전이 맞지 않습니다. "
        f"scalp_app.py 요구 버전: {_REQUIRED_ENGINE_VERSION} / "
        f"현재 regime_session_upgrade.py: {_engine_version}"
    )
    if _missing_engine_symbols:
        st.code("누락 함수: " + ", ".join(_missing_engine_symbols))
    st.info("regime_session_upgrade.py를 이번에 함께 받은 파일로 교체한 뒤 앱을 재부팅해 주세요.")
    st.stop()

_num = _rsu._num
apply_repeat_scalp_overlay = _rsu.apply_repeat_scalp_overlay
box_regime_plan = _rsu.box_regime_plan
data_quality_plan = _rsu.data_quality_plan
forward_forecast_plan = _rsu.forward_forecast_plan
hourly_structure_plan = _rsu.hourly_structure_plan
intraday_regime_plan = _rsu.intraday_regime_plan
resolve_vwap_series = _rsu.resolve_vwap_series
session_for = _rsu.session_for
strategy_target_plan = _rsu.strategy_target_plan
target_reach_probability_plan = _rsu.target_reach_probability_plan
trade_decision_plan = _rsu.trade_decision_plan


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
    code = "KR" if market == "국내" else "US"
    sess = session_for(code, now)
    return {"session": sess.session_name, "tradable": sess.tradable, "local_time": sess.local_time}


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


@st.cache_data(ttl=20,show_spinner=False)


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
        # 번들 KIS 엔진의 미국 후보 검색을 사용한다. 엔진 내부에서 most_actives/day_gainers를
        # 모집한 뒤 정밀 가격·분봉은 KIS analyze()가 다시 검증한다.
        try: absorb(scanner().candidates("미국 30분 1% 타점"),"미국 거래량/모멘텀")
        except Exception: pass
        try: absorb(scanner().candidates("미국 급등주"),"미국 급등 보조")
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
    """공통 엔진의 최종판정만 후보표용 작은 dict로 변환한다."""
    if not bool(item.get("final_candidate")):
        return None
    strategy=str(item.get("strategy_type","NONE"))
    if strategy=="UPTREND":
        trade_type="우상향 반복단타"
        stage="🟢 정상 눌림/우상향 재매수"
        priority=6
    elif strategy=="RANGE":
        trade_type="박스 반복단타"
        stage="🟦 박스 하단 재매수"
        priority=5
    else:
        return None

    screen_volume=int(row.get("screen_volume",0) or 0)
    screen_value=float(row.get("screen_value",0) or 0)
    rvol=float(item.get("rvol",0) or 0)
    pfirst=float(item.get("target1_before_stop_probability",0) or 0)
    p1=float(item.get("target1_reach_probability",0) or 0)
    conf=float(item.get("target_probability_confidence",0) or 0)
    rank=priority*100+pfirst*1.5+p1*.5+conf*.4+min(rvol,5)*5+min(math.log10(max(screen_volume,1)),9)*6

    return {
        "ticker":str(item.get("ticker") or row.get("ticker")),
        "name":str(item.get("name") or row.get("name") or row.get("ticker")),
        "stage":stage,"trade_type":trade_type,
        "regime_label":str(item.get("intraday_regime_label","-")),
        "hourly_label":str(item.get("hourly_structure_label","-")),
        "hourly_bull_score":int(item.get("hourly_structure_score",0) or 0),
        "hourly_bear_score":int(item.get("hourly_structure_bear_score",0) or 0),
        "hourly_bars":int(item.get("hourly_bars",0) or 0),
        "box_label":str(item.get("box_label","-")),
        "price":float(item.get("price",0) or 0),
        "score":float(item.get("score",0) or 0),"rvol":rvol,
        "risk_reward":float(item.get("strategy_risk_reward",0) or 0),"rank":rank,
        "repeat_width":float(item.get("strategy_range_percent",0) or 0),
        "box_width":float(item.get("box_width_percent",0) or 0),
        "support":float(item.get("strategy_support",0) or 0),
        "target1":float(item.get("strategy_target1",0) or 0),
        "target2":float(item.get("strategy_target2",0) or 0),
        "stop":float(item.get("strategy_stop",0) or 0),
        "screen_volume":screen_volume,"screen_value":screen_value,
        "spread":item.get("verified_spread_percent"),
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
        "t1_probability":p1,
        "t2_probability":float(item.get("target2_reach_probability",0) or 0),
        "t1_before_stop_probability":pfirst,
        "stop_risk_probability":float(item.get("stop_first_risk_probability",0) or 0),
        "probability_confidence":conf,
        "t1_eta":int(item.get("target1_eta_minutes",0) or 0),
        "t2_eta":int(item.get("target2_eta_minutes",0) or 0),
        "exchange":str(row.get("exchange") or ("KR" if market=="국내" else "NASDAQ")),
        "row":dict(row),"_seen_at":time.time(),
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
    market_code="KR" if market=="국내" else "US"

    if fast_scan:
        # 첫 WebSocket 구독 직후 호가가 아직 비어 있으면 1회만 즉시 보완한다.
        if float(item.get("best_bid",0) or 0)<=0 or float(item.get("best_ask",0) or 0)<=0:
            try:
                refreshed=scanner().refresh_quotes([item],mode)
                if refreshed: item.update(refreshed[0])
            except Exception:
                pass
    else:
        for _ in range(2):
            if float(item.get("best_bid",0) or 0)>0 and float(item.get("best_ask",0) or 0)>0:
                break
            try:
                refreshed=scanner().refresh_quotes([item],mode)
                if refreshed: item.update(refreshed[0])
            except Exception:
                pass
            time.sleep(0.12)
        if market=="미국":
            item=normalize_us_item(item,row)

    # 단일 계산 파이프라인: 실측 -> 추세/박스 -> 전략가격 -> 품질 -> 미래분포 -> 목표확률 -> 최종판정
    item=resolve_vwap_series(item)
    item=hourly_structure_plan(item,market)
    item=apply_repeat_scalp_overlay(item,market_code)
    item=_adapt_repeat_overlay_for_ui(item)
    item=intraday_regime_plan(item,market)
    item=box_regime_plan(item)
    item=strategy_target_plan(item)

    tradable=market_clock(market)["tradable"]
    item=data_quality_plan(item,market_code,tradable=tradable)
    item=forward_forecast_plan(item,market)
    item=target_reach_probability_plan(item)
    item=trade_decision_plan(item)
    return _trim_heavy_item(item,360)


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
        "1차 도달예상":c["target1"],
        "1차 도달확률":f"{float(c.get('t1_probability',0) or 0):.0f}%",
        "1차 선도달":f"{float(c.get('t1_before_stop_probability',0) or 0):.0f}%",
        "확률신뢰":f"{float(c.get('probability_confidence',0) or 0):.0f}%",
        "ETA":f"{int(c.get('t1_eta',0) or 0)}분" if int(c.get('t1_eta',0) or 0)>0 else "-",
        "손절선도달위험":f"{float(c.get('stop_risk_probability',0) or 0):.0f}%",
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
quality_checks=latest.get("data_quality_checks",{}) or {}
quality_rows=[{"검문":k,"통과":bool(v),"내용":"통과" if v else "미통과"} for k,v in quality_checks.items()]
quality_passed=bool(latest.get("data_gate_passed"))
intraday_label=str(latest.get("intraday_regime_label","⚪ 큰 추세 확인 중"))
intraday_reason=str(latest.get("intraday_regime_reason",""))
rr=float(latest.get("strategy_risk_reward",0) or 0)
state=str(latest.get("repeat_scalp_state","UNAVAILABLE"))
width=float(latest.get("strategy_range_percent",0) or 0)
support=float(latest.get("strategy_support",0) or 0)
stop=float(latest.get("strategy_stop",0) or 0)
t1=float(latest.get("strategy_target1",0) or 0)
t2=float(latest.get("strategy_target2",0) or 0)
if locked_entry>0 and t1>locked_entry: width=(t1/locked_entry-1)*100
cal=calibration_stats(selected_ticker) if require_validation else {m:{"samples":0,"accuracy":0,"bias":0,"mae":0} for m in (5,15,30,60)}
validated=cal[5]["samples"]>=20 and cal[15]["samples"]>=20 and cal[5]["accuracy"]>=55 and cal[15]["accuracy"]>=55
decision=str(latest.get("trade_decision","AVOID"))
level="success" if decision=="BUY_REVIEW" else "warning" if decision=="WAIT" else "error"
if require_validation and level=="success" and not validated:
    level="warning"
    latest["trade_decision_reasons"]=list(latest.get("trade_decision_reasons",[]) or [])+["실전 검증 표본 부족"]
latest["entry_checks_passed"]=level=="success"
if level=="success":
    st.success(f"🟢 매수 검토 · {latest.get('strategy_type','-')} · 반복폭 {width:.2f}% · 1차 {fmt(t1)}")
elif state=="TAKE_PROFIT":
    st.warning(f"🟠 1차 저항 접근 · {fmt(t1)}")
elif level=="error":
    st.error("🔴 회피 · " + " / ".join(latest.get("trade_decision_reasons",[]) or ["조건 미충족"]))
else:
    st.info("🟡 대기 · " + " / ".join(latest.get("trade_decision_reasons",[]) or [f"반복폭 {width:.2f}%"]))

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

p1=float(latest.get("target1_reach_probability",0) or 0)
p2=float(latest.get("target2_reach_probability",0) or 0)
pfirst=float(latest.get("target1_before_stop_probability",0) or 0)
stoprisk=float(latest.get("stop_first_risk_probability",0) or 0)
eta1=int(latest.get("target1_eta_minutes",0) or 0)
eta2=int(latest.get("target2_eta_minutes",0) or 0)
cols=st.columns(6)
cols[0].metric(f"{selected_ticker} · {latest.get('name','')}",fmt(price),f"{change:+.2f}%")
cols[1].metric("재매수 기준",fmt(locked_entry),f"{fmt(entry_zone_low)}~{fmt(entry_zone_high)} · {entry_lock_age}분 유지")
cols[2].metric("1차 도달예상",fmt(t1),f"도달 {p1:.0f}% · 약 {eta1}분" if eta1 else f"도달 {p1:.0f}%")
cols[3].metric("2차 도달예상",fmt(t2) if t2>0 else "-",f"도달 {p2:.0f}% · 약 {eta2}분" if t2>0 and eta2 else (f"도달 {p2:.0f}%" if t2>0 else None))
cols[4].metric("1차 선도달",f"{pfirst:.0f}%",f"{latest.get('target_probability_label','-')} · 신뢰 {float(latest.get('target_probability_confidence',0) or 0):.0f}%")
cols[5].metric("손절 선도달 위험",f"{stoprisk:.0f}%",f"손절 {fmt(stop)}")
st.caption(f"1차 근거: {latest.get('target1_basis','-')} · 2차 근거: {latest.get('target2_basis','-')} · 확률은 현재 차트 조건이 유지된다는 전제의 모델 추정치")


with st.expander("향후 5·15·30·60분 예상 상세",expanded=True):
    forecast_rows=[]
    for h in (5,15,30,60):
        f=(ff.get(h,{}) or {})
        forecast_rows.append({"시간":f"{h}분 후","중심예상":f"{float(f.get('center_pct',0) or 0):+.2f}%","예상범위":f"{float(f.get('low_pct',0) or 0):+.2f}~{float(f.get('high_pct',0) or 0):+.2f}%","예상가격":f"{fmt(f.get('low_price',0))} ~ {fmt(f.get('high_price',0))}","상승확률":f"{float(f.get('up_probability',0) or 0):.0f}%","모델신뢰":f"{float(f.get('confidence',0) or 0):.0f}%"})
    st.dataframe(pd.DataFrame(forecast_rows),hide_index=True,use_container_width=True)
    st.caption("예상범위는 확정 수익률이 아니라 현재 차트 구조가 유지될 때의 조건부 범위입니다. 실제 결과는 실행 중 자동 누적·채점됩니다.")

st.subheader("실시간 차트 · 지지 / 1차 / 2차 목표")
render_chart(latest)
with st.expander("실시간 데이터 검문",expanded=not quality_passed):
    st.dataframe(pd.DataFrame(quality_rows),hide_index=True,use_container_width=True)
with st.expander("진입 전 뉴스·공시 위험 확인"):
    if st.button("뉴스·SEC·거래정지 확인",use_container_width=True):
        try: st.session_state["scalp_risk_check"]=scanner().analyze_candidate(selected_row,mode)
        except Exception as e: st.error(str(e))
    checked=st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker"))==selected_ticker: st.write(checked.get("news_summary","뉴스 확인 완료")); st.write("거래정지:",checked.get("halt_active",False))

st.caption(f"화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
