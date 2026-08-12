# -*- coding: utf-8 -*-

"""초단타 전용 Streamlit 앱.



GitHub 저장소 루트의 기존 app.py 안에 번들된 KIS 엔진만 읽어 사용한다.

기존 통합 UI는 실행하지 않는다.

"""

import ast

import base64

import importlib.abc

import importlib.util

import re

import requests

import sqlite3

import tempfile

import sys

import time

import types

import zlib

from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timedelta, timezone

from pathlib import Path

from zoneinfo import ZoneInfo



import altair as alt

import pandas as pd

import streamlit as st

from streamlit_autorefresh import st_autorefresh



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

except Exception as audit_import_exception:

    AUDIT_KR_UNIVERSE = []

    AUDIT_US_UNIVERSE = []

    AUDIT_IMPORT_ERROR = str(audit_import_exception)



KST = timezone(timedelta(hours=9), name="KST")

ET = ZoneInfo("America/New_York")

HISTORY_DB = Path(tempfile.gettempdir()) / "ymym_scalp_validation.sqlite3"





def market_clock(market: str, now: datetime | None = None) -> dict:

    """Return an explicit, DST-safe market session without guessing from KST."""

    now_kst = now.astimezone(KST) if now else datetime.now(KST)

    if market == "국내":

        minute = now_kst.hour * 60 + now_kst.minute

        weekday = now_kst.weekday() < 5

        if weekday and 9 * 60 <= minute < 15 * 60 + 30:

            session, tradable = "국내 정규장", True

        else:

            session, tradable = "국내 장외시간", False

        return {"session": session, "tradable": tradable, "local_time": now_kst.strftime("%H:%M:%S KST")}



    now_et = now_kst.astimezone(ET)

    minute = now_et.hour * 60 + now_et.minute

    weekday = now_et.weekday() < 5

    if weekday and 4 * 60 <= minute < 9 * 60 + 30:

        session, tradable = "미국 프리마켓", True

    elif weekday and 9 * 60 + 30 <= minute < 16 * 60:

        session, tradable = "미국 정규장", True

    elif weekday and 16 * 60 <= minute < 20 * 60:

        session, tradable = "미국 애프터마켓", True

    else:

        session, tradable = "미국 장외시간", False

    return {

        "session": session, "tradable": tradable,

        "local_time": f"{now_et.strftime('%H:%M:%S')} ET / {now_kst.strftime('%H:%M:%S')} KST",

    }





def db_connect():

    connection = sqlite3.connect(HISTORY_DB, timeout=10)

    connection.execute("PRAGMA journal_mode=WAL")

    connection.execute("""

        CREATE TABLE IF NOT EXISTS predictions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ticker TEXT NOT NULL, issued REAL NOT NULL, base_price REAL NOT NULL,

            f5 REAL NOT NULL, f10 REAL NOT NULL, f20 REAL NOT NULL DEFAULT 0, f30 REAL NOT NULL,

            actual5 REAL, actual10 REAL, actual20 REAL, actual30 REAL,

            UNIQUE(ticker, issued)

        )

    """)

    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(predictions)")}

    for column, definition in (("f20", "REAL NOT NULL DEFAULT 0"), ("actual20", "REAL")):

        if column not in existing_columns:

            connection.execute(f"ALTER TABLE predictions ADD COLUMN {column} {definition}")

    return connection



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

    {"ticker":"URTY","name":"러셀2000 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},

    {"ticker":"SRTY","name":"러셀2000 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},

    {"ticker":"UDOW","name":"다우30 3배 레버리지","exchange":"AMEX","asset_type":"3배 레버리지 ETF"},

    {"ticker":"SDOW","name":"다우30 3배 인버스","exchange":"AMEX","asset_type":"3배 인버스 ETF"},

    {"ticker":"NUGT","name":"금광주 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"DUST","name":"금광주 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},

    {"ticker":"GUSH","name":"미국 원유생산 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"DRIP","name":"미국 원유생산 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},

    {"ticker":"BOIL","name":"천연가스 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"KOLD","name":"천연가스 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},

    {"ticker":"UCO","name":"원유 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"SCO","name":"원유 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},

    {"ticker":"BITX","name":"비트코인 선물 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"BITI","name":"비트코인 선물 인버스","exchange":"AMEX","asset_type":"인버스 ETF"},

    {"ticker":"MSTU","name":"마이크로스트래티지 2배 레버리지","exchange":"AMEX","asset_type":"2배 레버리지 ETF"},

    {"ticker":"MSTZ","name":"마이크로스트래티지 2배 인버스","exchange":"AMEX","asset_type":"2배 인버스 ETF"},

    {"ticker":"NVDL","name":"엔비디아 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"NVD","name":"엔비디아 2배 인버스","exchange":"NASDAQ","asset_type":"2배 인버스 ETF"},

    {"ticker":"TSLL","name":"테슬라 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"TSLS","name":"테슬라 1배 인버스","exchange":"NASDAQ","asset_type":"인버스 ETF"},

    {"ticker":"AMZU","name":"아마존 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"AMZD","name":"아마존 2배 인버스","exchange":"NASDAQ","asset_type":"2배 인버스 ETF"},

    {"ticker":"GGLL","name":"알파벳 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"GGLS","name":"알파벳 2배 인버스","exchange":"NASDAQ","asset_type":"2배 인버스 ETF"},

    {"ticker":"AAPU","name":"애플 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"AAPD","name":"애플 2배 인버스","exchange":"NASDAQ","asset_type":"2배 인버스 ETF"},

    {"ticker":"METU","name":"메타 2배 레버리지","exchange":"NASDAQ","asset_type":"2배 레버리지 ETF"},

    {"ticker":"METD","name":"메타 2배 인버스","exchange":"NASDAQ","asset_type":"2배 인버스 ETF"},

]





@st.cache_data(ttl=86400, show_spinner=False)

def kr_name_map() -> dict:

    def norm(text: str) -> str:

        return re.sub(r"[^0-9a-z가-힣]", "", str(text).casefold())

    mapping = {norm(row["name"]): (row["ticker"], row["name"]) for row in KR_UNIVERSE}

    try:

        from pykrx import stock as krx_stock

        for market_name in ("KOSPI", "KOSDAQ"):

            for code in krx_stock.get_market_ticker_list(market=market_name):

                name = krx_stock.get_market_ticker_name(code)

                if name:

                    mapping[norm(name)] = (str(code), str(name))

    except Exception:

        pass

    try:

        import FinanceDataReader as fdr

        listing = fdr.StockListing("KRX")

        code_col = "Code" if "Code" in listing.columns else "Symbol"

        name_col = "Name" if "Name" in listing.columns else "MarketName"

        for _, row in listing.iterrows():

            code, name = str(row.get(code_col, "")), str(row.get(name_col, ""))

            if code and name and name != "nan":

                mapping[norm(name)] = (code.zfill(6), name)

    except Exception:

        pass

    mapping.update({

        norm("SK텔레콤"): ("017670", "SK텔레콤"),

        norm("LG전자"): ("066570", "LG전자"),

        norm("KB금융"): ("105560", "KB금융"),

        norm("신한지주"): ("055550", "신한지주"),

        norm("셀트리온"): ("068270", "셀트리온"),

        norm("기아"): ("000270", "기아"),

        norm("포스코홀딩스"): ("005490", "POSCO홀딩스"),

    })

    return mapping





@st.cache_data(ttl=86400, show_spinner=False)

def yahoo_symbol_search(query: str) -> dict | None:

    """Resolve an arbitrary US ticker or company name without restricting asset type."""

    try:

        quotes = []

        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):

            try:

                response = requests.get(

                    f"https://{host}/v1/finance/search",

                    params={"q": query, "quotesCount": 10, "newsCount": 0},

                    headers={"User-Agent": "Mozilla/5.0"}, timeout=8,

                )

                response.raise_for_status()

                quotes = response.json().get("quotes") or []

                if quotes:

                    break

            except Exception:

                continue

        allowed = [q for q in quotes if str(q.get("quoteType", "")).upper() in {"EQUITY", "ETF"}]

        us = [q for q in allowed if str(q.get("exchange", "")).upper() not in {"KSC", "KOE"}]

        q = (us or allowed or [None])[0]

        if not q:

            return None

        ticker = str(q.get("symbol", "")).upper()

        exchange_raw = str(q.get("exchange", "")).upper()

        exchange = "AMEX" if exchange_raw in {"ASE", "PCX", "NYQ"} else "NASDAQ" if exchange_raw in {"NMS", "NGM", "NCM"} else "NYSE"

        return {"ticker": ticker, "name": str(q.get("shortname") or q.get("longname") or ticker),

                "exchange": exchange, "asset_type": "직접 검색"}

    except Exception:

        return None





def resolve_manual(value: str, market: str) -> dict | None:

    value = value.strip()

    if not value:

        return None

    if market == "국내":

        if value.isdigit():

            return {"ticker": value.zfill(6), "name": value.zfill(6), "exchange": "KR", "asset_type": "직접 검색"}

        normalized = re.sub(r"[^0-9a-z가-힣]", "", value.casefold())

        mapping = kr_name_map()

        exact = mapping.get(normalized)

        if exact:

            return {"ticker": exact[0], "name": exact[1], "exchange": "KR", "asset_type": "직접 검색"}

        partial = next(((code, name) for key, (code, name) in mapping.items() if normalized in key), None)

        if partial:

            return {"ticker": partial[0], "name": partial[1], "exchange": "KR", "asset_type": "직접 검색"}

        return None

    ticker = re.sub(r"[^A-Z0-9.\-]", "", value.upper())

    known = next((dict(row) for row in US_UNIVERSE if row["ticker"] == ticker), None)

    if known:

        known["asset_type"] = "직접 검색"

        return known

    found = yahoo_symbol_search(value)

    if found:

        return found

    if ticker:

        return {"ticker": ticker, "name": ticker, "exchange": "NASDAQ", "asset_type": "직접 검색"}

    return None





def _load_bundled_sources() -> dict:

    source_path = Path(__file__).with_name("app.py")

    if not source_path.exists():

        development_copy = Path(__file__).resolve().parent.parent / "ymym_stock_scanner_fixed" / "app.py"

        if development_copy.exists():

            source_path = development_copy

    if not source_path.exists():

        st.error("같은 폴더에 기존 app.py가 필요합니다.")

        st.stop()

    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    for node in tree.body:

        if isinstance(node, ast.Assign):

            if any(isinstance(target, ast.Name) and target.id == "_BUNDLED" for target in node.targets):

                value = ast.literal_eval(node.value)

                if isinstance(value, dict):

                    return value

    raise RuntimeError("app.py에서 번들 엔진을 찾지 못했습니다.")





_BUNDLED = _load_bundled_sources()





class _Loader(importlib.abc.MetaPathFinder, importlib.abc.Loader):

    PACKAGES = {"scanner", "utils", "config", "engine", "data", "ui"}



    def find_spec(self, fullname, path=None, target=None):

        if fullname in _BUNDLED:

            return importlib.util.spec_from_loader(fullname, self, is_package=False)

        if fullname in self.PACKAGES:

            return importlib.util.spec_from_loader(fullname, self, is_package=True)

        return None



    def create_module(self, spec):

        return None



    def exec_module(self, module):

        name = module.__name__

        if name in self.PACKAGES:

            module.__path__ = []

            module.__package__ = name

            module.__file__ = str(Path.cwd() / name / "__init__.py")

            return

        code = zlib.decompress(base64.b64decode(_BUNDLED[name])).decode("utf-8")

        module.__file__ = str(Path.cwd().joinpath(*name.split(".")).with_suffix(".py"))

        module.__package__ = name.rpartition(".")[0]

        exec(compile(code, module.__file__, "exec"), module.__dict__)





sys.meta_path.insert(0, _Loader())



from scanner.kis_engine import (  # noqa: E402

    KISUnifiedScanner,

    apply_mode_policy,

    finalize_trade_item,

)



st.set_page_config(page_title="초단타 VWAP 타점", page_icon="⚡", layout="wide")

st.markdown("""

<style>

  .block-container {padding-top: 1rem; max-width: 1500px;}

  [data-testid="stMetric"] {background:#f7f8fb;border:1px solid #e6e8ee;border-radius:12px;padding:8px;}

  [data-testid="stMetricValue"] {font-size:clamp(1.25rem,2.2vw,2.15rem) !important; line-height:1.15; overflow-wrap:anywhere;}

  [data-testid="stMetricLabel"] {font-size:.88rem !important;}

  .trade-action {border-radius:14px;padding:15px 18px;margin:.45rem 0 .8rem 0;border:2px solid #d8dce6;background:#f7f8fb;}

  .trade-action.buy {border-color:#19a15f;background:#ecfbf3}.trade-action.sell {border-color:#e45656;background:#fff0f0}

  .trade-action.wait {border-color:#dcae32;background:#fff9e8}.trade-action.stop {border-color:#b93838;background:#ffe9e9}

  .trade-action h2 {font-size:1.45rem;margin:0 0 .35rem 0}.trade-action p {font-size:1rem;margin:.12rem 0}

  .forecast-card {background:#f7f8fb;border:1px solid #e6e8ee;border-radius:12px;padding:12px;min-height:118px;}

  .forecast-card .title {font-size:.9rem;line-height:1.25;margin-bottom:9px;}

  .forecast-card .price {font-size:1.45rem;font-weight:650;line-height:1.15;white-space:normal;overflow-wrap:anywhere;}

  .forecast-card .basis {font-size:.78rem;color:#777;margin-top:8px;line-height:1.25;}

  @media(max-width:700px){

    .block-container{padding:.45rem}

    [data-testid="stMetric"]{padding:6px}

    [data-testid="stMetricValue"]{font-size:1.22rem !important}

    [data-testid="stMetricLabel"]{font-size:.76rem !important}

    .trade-action h2{font-size:1.2rem}.trade-action p{font-size:.9rem}

    .forecast-card{padding:9px;min-height:105px}.forecast-card .price{font-size:1.05rem}.forecast-card .title{font-size:.76rem}

  }

</style>

""", unsafe_allow_html=True)





@st.cache_resource

def scanner() -> KISUnifiedScanner:

    return KISUnifiedScanner()





def fmt(value) -> str:

    try:

        number = float(value)

        if number >= 1000:

            return f"{number:,.0f}"

        if number >= 10:

            return f"{number:,.2f}"

        return f"{number:,.4f}"

    except Exception:

        return "-"





def verdict_text(item: dict) -> tuple[str, str]:

    verdict = str(item.get("chart_verdict", "WAIT"))

    if verdict == "BUY_READY" and item.get("entry_checks_passed"):

        return "🟢 매수 검토", "success"

    if verdict == "BUY_READY":

        return "🟡 차트 매수 준비·위험확인 필요", "warning"

    if verdict == "NO_ENTRY":

        return "🔴 매수 금지·매도 검토", "error"

    return "🟡 대기", "warning"





def forecast_label(percent: float) -> str:

    if percent >= 0.35:

        return "상승 우세"

    if percent <= -0.35:

        return "하락 위험"

    return "횡보·불확실"





def strategy_consensus(item: dict) -> tuple[list[dict], int, int, int]:

    """Major intraday methods vote independently; contradictory methods stay visible."""

    price = float(item.get("price", 0) or 0)

    vwap = float(item.get("vwap", 0) or 0)

    ema9 = float(item.get("ema9", 0) or 0)

    ema20 = float(item.get("ema20", 0) or 0)

    rsi = float(item.get("rsi", 50) or 50)

    macd = float(item.get("macd_histogram", 0) or 0)

    stoch = float(item.get("stochastic_k", 50) or 50)

    rvol = float(item.get("rvol", 0) or 0)

    f5 = float(item.get("forecast_5m", 0) or 0)

    f10 = float(item.get("forecast_10m", 0) or 0)

    f30 = float(item.get("forecast_30m", 0) or 0)

    orderbook = str(item.get("orderbook_signal", ""))

    obv = str(item.get("obv_trend", ""))

    patterns = " ".join(map(str, item.get("pattern_signals", []) or []))

    trend_text = " ".join(str(item.get(key, "")) for key in ("trend_5m", "trend_15m", "trend_30m"))



    votes = []

    def add(name: str, buy: bool, sell: bool, reason: str):

        signal = "매수" if buy and not sell else "매도" if sell and not buy else "대기"

        votes.append({"기법": name, "판정": signal, "근거": reason})



    add("VWAP 추세", price > vwap > 0, 0 < price < vwap, f"현재가 {('위' if price > vwap else '아래')}·VWAP {fmt(vwap)}")

    add("EMA 추세", ema9 > ema20 > 0 and price >= ema9, 0 < ema9 < ema20 and price <= ema9, f"EMA9 {fmt(ema9)} / EMA20 {fmt(ema20)}")

    add("MACD 모멘텀", macd > 0, macd < 0, f"히스토그램 {macd:+.4f}")

    add("RSI·스토캐스틱", 42 <= rsi <= 68 and stoch < 80, rsi >= 75 or stoch >= 90, f"RSI {rsi:.1f} / %K {stoch:.1f}")

    add("거래량·RVOL", rvol >= 1.5 and f5 > 0, rvol < 0.7 or (rvol >= 1.5 and f5 < 0), f"RVOL {rvol:.1f}배")

    add("OBV 수급", "상승" in obv, "하락" in obv, obv or "미확인")

    add("호가·체결", "매수" in orderbook, "매도" in orderbook, orderbook or "미확인")

    add("캔들 패턴", any(x in patterns for x in ("상승", "망치", "돌파", "장악")), any(x in patterns for x in ("하락", "유성", "윗꼬리")), patterns or "뚜렷한 패턴 없음")

    add("다중 시간봉", all(x > 0 for x in (f5, f10, f30)), any(x < 0 for x in (f5, f10, f30)), f"5분 {f5:+.2f}% / 10분 {f10:+.2f}% / 30분 {f30:+.2f}%")

    add("추세 정렬", trend_text.count("상승") >= 2, trend_text.count("하락") >= 2, trend_text or "추세 계산 중")

    buys = sum(row["판정"] == "매수" for row in votes)

    sells = sum(row["판정"] == "매도" for row in votes)

    waits = len(votes) - buys - sells

    return votes, buys, sells, waits





def data_quality_gate(item: dict, market: str) -> tuple[list[dict], bool, float | None]:

    price = float(item.get("price", 0) or 0)

    bars = int(item.get("intraday_bar_count", 0) or len(item.get("chart_close_1m", []) or []))

    vwap = float(item.get("vwap", 0) or 0)

    ema9 = float(item.get("ema9", 0) or 0)

    rvol = float(item.get("rvol", 0) or 0)

    bid = float(item.get("best_bid", 0) or 0)

    ask = float(item.get("best_ask", 0) or 0)

    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if ask > 0 and bid > 0 and ask >= bid else None

    last_bar_age = None

    try:

        times = item.get("chart_time_1m", []) or []

        last_bar = pd.Timestamp(pd.to_datetime(times[-1]))

        # KIS may return either timezone-aware timestamps or naive Korean

        # exchange timestamps. Treating a naive KST value as UTC made stale

        # bars look fresh for nine hours.

        if last_bar.tzinfo is None:

            last_bar = last_bar.tz_localize(KST)

        last_bar = last_bar.tz_convert("UTC")

        last_bar_age = max(0.0, (pd.Timestamp.now(tz="UTC") - last_bar).total_seconds())

    except Exception:

        pass

    checks = [

        {"검문": "현재가", "통과": price > 0, "내용": fmt(price)},

        {"검문": "1분봉 수", "통과": bars >= 20, "내용": f"{bars}개"},

        {"검문": "VWAP·EMA", "통과": vwap > 0 and ema9 > 0, "내용": f"VWAP {fmt(vwap)} / EMA9 {fmt(ema9)}"},

        {"검문": "분봉 출처", "통과": not bool(item.get("intraday_fallback")), "내용": str(item.get("intraday_source", "미확인"))},

        {"검문": "마지막 분봉 시각", "통과": last_bar_age is not None and last_bar_age <= 180,

         "내용": f"{last_bar_age:.0f}초 전" if last_bar_age is not None else "시각 없음"},

        {"검문": "RVOL 정상범위", "통과": 0.05 <= rvol <= 20, "내용": f"{rvol:.1f}배"},

        {"검문": "실시간 호가", "통과": spread is not None, "내용": f"{spread:.3f}%" if spread is not None else "미수신"},

    ]

    max_spread = 0.35 if "레버리지" in str(item.get("asset_type", "")) else 0.25

    if spread is not None:

        checks.append({"검문": "스프레드", "통과": spread <= max_spread, "내용": f"기준 {max_spread:.2f}% 이하"})

    # Only chart-critical evidence can make the whole analysis unavailable.

    # KIS occasionally omits an order-book snapshot and RVOL can be distorted

    # early in a session; those remain visible warnings but must not erase a

    # valid current price, fresh one-minute bars, VWAP and EMA analysis.

    critical_checks = checks[:5]

    return checks, all(bool(row["통과"]) for row in critical_checks), spread





def market_regime(item: dict) -> tuple[str, str]:

    price = float(item.get("price", 0) or 0)

    vwap = float(item.get("vwap", 0) or 0)

    ema9 = float(item.get("ema9", 0) or 0)

    ema20 = float(item.get("ema20", 0) or 0)

    rvol = float(item.get("rvol", 0) or 0)

    if rvol >= 3:

        return "변동성 급증장", "돌파·호가·거래량 기법 우선"

    if price > vwap > 0 and ema9 > ema20 > 0:

        return "상승 추세장", "눌림목·VWAP 지지·추세추종 우선"

    if 0 < price < vwap and 0 < ema9 < ema20:

        return "하락 추세장", "신규 매수 금지·반등 확인 우선"

    return "횡보장", "VWAP 평균회귀·박스 돌파 확인 우선"





def weighted_strategy_score(rows: list[dict], regime: str) -> tuple[float, float]:

    weights = {row["기법"]: 1.0 for row in rows}

    if regime == "상승 추세장":

        for name in ("VWAP 추세", "EMA 추세", "MACD 모멘텀", "다중 시간봉", "추세 정렬"):

            weights[name] = 1.6

    elif regime == "횡보장":

        for name in ("VWAP 추세", "RSI·스토캐스틱", "캔들 패턴", "호가·체결"):

            weights[name] = 1.6

    elif regime == "변동성 급증장":

        for name in ("거래량·RVOL", "호가·체결", "VWAP 추세", "다중 시간봉"):

            weights[name] = 1.8

    elif regime == "하락 추세장":

        for name in ("VWAP 추세", "EMA 추세", "다중 시간봉", "추세 정렬"):

            weights[name] = 2.0

    total = sum(weights.values()) or 1.0

    signed = sum(weights[row["기법"]] * (1 if row["판정"] == "매수" else -1 if row["판정"] == "매도" else 0) for row in rows)

    buy_weight = sum(weights[row["기법"]] for row in rows if row["판정"] == "매수")

    return round(signed / total * 100, 1), round(buy_weight / total * 100, 1)





@st.cache_data(ttl=20, show_spinner=False)

def benchmark_context(market: str, ticker: str) -> dict:

    try:

        if market == "미국":

            mapping = {

                "SOXL": ("SOXX", "NASDAQ", 1), "SOXS": ("SOXX", "NASDAQ", -1),

                "TQQQ": ("QQQ", "NASDAQ", 1), "SQQQ": ("QQQ", "NASDAQ", -1),

            }

            bench, exchange, direction = mapping.get(ticker, ("QQQ", "NASDAQ", 1))

            quote = scanner().client.us_quote(bench, exchange)

            _, _, verified_change, _ = verified_us_change(quote)

            change = verified_change * direction

            bars = scanner().client.us_intraday(bench, exchange, minutes=1)

            intraday = ((float(bars["close"].iloc[-1]) / float(bars["close"].iloc[-6]) - 1) * 100 * direction) if len(bars) >= 6 else 0.0

            return {"name": bench, "change": change, "intraday": intraday, "confirmed": len(bars) >= 20}

        mapping = {

            "488080": [("005930", 0.5), ("000660", 0.5)],

            "396500": [("005930", 0.5), ("000660", 0.5)],

        }

        members = mapping.get(ticker, [("069500", 1.0)])

        change = 0.0

        intraday = 0.0

        enough = True

        for code, weight in members:

            change += float(scanner().client.kr_quote(code).get("change", 0) or 0) * weight

            bars = scanner().client.kr_intraday(code)

            enough = enough and len(bars) >= 20

            if len(bars) >= 6:

                intraday += (float(bars["close"].iloc[-1]) / float(bars["close"].iloc[-6]) - 1) * 100 * weight

        return {"name": "+".join(code for code, _ in members), "change": change, "intraday": intraday, "confirmed": enough}

    except Exception as error:

        return {"name": "시장지표", "change": 0.0, "confirmed": False, "error": type(error).__name__}





def verified_us_change(quote: dict, fallback_price: float = 0.0) -> tuple[float, float, float, str]:

    """Return a cross-checked US price/change instead of trusting KIS ``rate``.



    Some overseas quote responses have exposed the previous close in the field

    that older response schemas labelled ``rate``.  Treating that number as a

    percentage produced values such as SOXL +132.71%.  The previous close and

    current price are independent fields, so their arithmetic return is the

    authoritative value whenever both are present.

    """

    price = float(quote.get("price", fallback_price) or fallback_price or 0)

    previous = float(

        quote.get("previous", quote.get("previous_close", quote.get("base", 0))) or 0

    )

    raw_change = float(quote.get("change", quote.get("change_percent", 0)) or 0)

    if price > 0 and previous > 0:

        calculated = (price / previous - 1.0) * 100.0

        return price, previous, round(calculated, 4), "현재가·전일종가 재계산"

    return price, previous, raw_change, "KIS 원본 등락률(전일종가 미수신)"





def normalize_us_item(item: dict, row: dict | None = None) -> dict:

    """Apply the same verified US change to cards, scores and candidate filters."""

    if not item:

        return item

    ticker = str((row or {}).get("ticker") or item.get("ticker") or "").upper()

    exchange = str((row or {}).get("exchange") or item.get("exchange") or "NASDAQ")

    if not ticker:

        return item

    try:

        quote = scanner().client.us_quote(ticker, exchange)

        price, previous, change, source = verified_us_change(

            quote, float(item.get("price", 0) or 0)

        )

        if price > 0:

            item["price"] = price

        item["previous_close"] = previous

        item["raw_kis_change_percent"] = float(

            quote.get("change", quote.get("change_percent", 0)) or 0

        )

        item["change_percent"] = change

        item["screen_change"] = change

        item["change_validation_source"] = source

    except Exception as error:

        item["change_validation_error"] = f"{type(error).__name__}: {error}"

    return item





@st.cache_data(ttl=60, show_spinner=False)

def live_filtered_universe(market: str) -> list[dict]:

    """거래소 전체 순위에서 상승·유동성 후보를 만든다.



    종목마다 현재가를 순차 조회하지 않는다. 한국은 KIS 거래소 순위,

    미국은 Yahoo/TradingView/Nasdaq의 전시장 스크리너 결과를 합친 뒤

    화면에 보여줄 소수만 남긴다. 고정 목록은 모든 순위 소스가 실패했을

    때만 쓰는 장애 대비용이다.

    """

    source = KR_UNIVERSE if market == "국내" else US_UNIVERSE

    limit = 300000 if market == "국내" else 200

    accepted: list[dict] = []

    ranked_rows: dict[str, dict] = {}

    modes = (

        ("국내 돌파", "국내 거래대금 급증")

        if market == "국내"

        else ("미국 30분 1% 타점", "미국 급등주")

    )

    for mode in modes:

        try:

            for row in scanner().candidates(mode):

                candidate = dict(row)

                ticker = str(candidate.get("ticker") or candidate.get("code") or "").upper().strip()

                if not ticker:

                    continue

                old = ranked_rows.get(ticker, {})

                # 같은 종목이 상승률/거래량 순위 양쪽에 있으면 더 완전한 값을 보존한다.

                merged = {**old, **candidate}

                merged["screen_price"] = float(candidate.get("screen_price") or old.get("screen_price") or candidate.get("price") or 0)

                merged["screen_change"] = float(candidate.get("screen_change") if candidate.get("screen_change") is not None else old.get("screen_change", candidate.get("change_percent", candidate.get("change", 0))) or 0)

                merged["screen_volume"] = max(int(float(old.get
