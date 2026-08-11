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
HISTORY_DB = Path(tempfile.gettempdir()) / "ymym_scalp_validation.sqlite3"


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
    core_tickers = {str(row.get("ticker", "")).upper() for row in source}
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
                merged["screen_volume"] = max(int(float(old.get("screen_volume", 0) or 0)), int(float(candidate.get("screen_volume", candidate.get("volume", 0)) or 0)))
                merged["ticker"] = ticker
                ranked_rows[ticker] = merged
        except Exception:
            continue

    blocked_words = ("스팩", "우선주", "관리", "정리매매", "인버스")
    for candidate in ranked_rows.values():
        ticker = str(candidate.get("ticker", "")).upper()
        # 자동 후보는 회복 가능성과 체결 안정성을 우선해 대형 우량주·대표
        # ETF·충분히 거래되는 레버리지 ETF 풀 안에서만 고른다. 직접 검색은 예외다.
        if ticker not in core_tickers:
            continue
        price = float(candidate.get("screen_price", 0) or 0)
        change = float(candidate.get("screen_change", 0) or 0)
        volume = int(candidate.get("screen_volume", 0) or 0)
        name = str(candidate.get("name", ""))
        trading_value = price * volume
        if market == "국내":
            # 반복단타는 단순 급등률보다 체결 가능한 유동성과 남은 움직임이
            # 중요하다. 상한가 근접주·동전주·거래대금 부족주는 제외한다.
            valid = (
                2_000 <= price <= limit
                and 0.30 <= change < 15.0
                and volume >= 300_000
                and trading_value >= 30_000_000_000
                and not any(word in name for word in blocked_words)
            )
        else:
            # 미국 자동 후보에서도 저가 소형 급등주를 제거한다. 충분한 거래량과
            # 거래대금을 동시에 충족해야 여러 차례 진입·청산할 가능성이 있다.
            # 직접 검색은 이 제한을 받지 않는다.
            valid = (
                5.0 <= price <= limit
                and 0.20 <= change < 12.0
                and volume >= 500_000
                and trading_value >= 25_000_000
                and not any(word in name.upper() for word in ("WARRANT", "RIGHT", "UNIT"))
            )
        if valid:
            candidate["asset_type"] = "반복단타 예비후보·정밀검증 전"
            accepted.append(candidate)
    # The ranked market response is one bulk call. Do not follow it with an
    # unbounded sequence of per-symbol calls: that was the main reason the UI
    # looked frozen. Static blue-chip/ETF fallbacks are deliberately bounded.
    # If the bulk rising-rank endpoint returned anything, use it immediately.
    # Blue chips/ETFs remain available through direct search. Only when the
    # bulk endpoint is empty do we make a very small four-symbol fallback.
    # 전체시장 순위가 모두 실패했을 때만 작은 고정 목록으로 장애를 완화한다.
    # 정상 상황에서는 이 경로가 실행되지 않으므로 화면 속도에 영향이 없다.
    fallback_source = [] if accepted else source[:12]

    def fetch_candidate(row: dict) -> dict | None:
        try:
            quote = (scanner().client.kr_quote(row["ticker"]) if market == "국내"
                     else scanner().client.us_quote(row["ticker"], row["exchange"]))
            candidate = dict(row)
            candidate["screen_price"] = float(quote.get("price", 0) or 0)
            candidate["screen_volume"] = int(float(
                quote.get("volume", quote.get("accumulated_volume", quote.get("acml_vol", 0))) or 0
            ))
            if market == "미국":
                price, previous, verified_change, source_name = verified_us_change(quote)
                candidate["screen_price"] = price
                candidate["previous_close"] = previous
                candidate["screen_change"] = verified_change
                candidate["change_validation_source"] = source_name
            else:
                candidate["screen_change"] = float(quote.get("change", 0) or 0)
            change = candidate["screen_change"]
            # Korean automatic candidates need room before the +30% ceiling.
            # At +25% or above, remaining upside is too small for a fresh scalp
            # and execution can freeze near the limit-up queue.
            price = candidate["screen_price"]
            volume = candidate["screen_volume"]
            trading_value = price * volume
            change_ok = 0.30 <= change < 15.0 if market == "국내" else 0.20 <= change < 12.0
            liquidity_ok = (
                volume >= 300_000 and trading_value >= 30_000_000_000
                if market == "국내"
                else volume >= 500_000 and trading_value >= 25_000_000
            )
            if 0 < price <= limit and change_ok and liquidity_ok:
                candidate["asset_type"] = str(candidate.get("asset_type") or "상승 후보")
                return candidate
        except Exception:
            return None
        return None

    if fallback_source:
        # 제한된 핵심 풀만 병렬 조회해 화면 정지를 피하고 개인 KIS 호출 한도를
        # 넘지 않는다. 전 종목 개별 조회는 절대 실행하지 않는다.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_candidate, row) for row in fallback_source]
            for future in as_completed(futures):
                candidate = future.result()
                if candidate:
                    accepted.append(candidate)
    else:
        for row in fallback_source:
            candidate = fetch_candidate(row)
            if candidate:
                accepted.append(candidate)
    deduped = {row["ticker"]: row for row in accepted}
    return sorted(
        deduped.values(),
        # 상승률이 가장 큰 종목보다 거래대금이 크고 과열되지 않은 종목을
        # 우선한다. 정밀분석에서 실제 반복 폭·VWAP·호가를 다시 검증한다.
        key=lambda row: (
            0 if "레버리지" in str(row.get("asset_type", "")) or "인버스" in str(row.get("asset_type", "")) else 1,
            float(row.get("screen_price", 0) or 0) * int(row.get("screen_volume", 0) or 0),
            -abs(float(row.get("screen_change", 0) or 0) - 3.0),
        ),
        reverse=True,
    )[:12]


def latest_entry_candidates(market: str, minimum_score: float, limit: int = 5) -> list[dict]:
    """이미 자동검증기가 수집한 최신 분석을 재사용해 추가 API 호출 없이 후보를 정렬한다."""
    market_code = "KR" if market == "국내" else "US"
    cutoff = int(time.time()) - 3 * 60
    try:
        with audit_connect() as db:
            rows = db.execute("""
                SELECT s.ticker,s.name,s.issued,s.base_price,s.verdict,s.score,
                       s.entry_ok,s.data_valid,s.forecast5,s.forecast10,
                       s.forecast20,s.forecast30,s.detail_json
                FROM signals s
                JOIN (
                    SELECT ticker,MAX(issued) AS issued FROM signals
                    WHERE market=? AND issued>=? GROUP BY ticker
                ) latest ON latest.ticker=s.ticker AND latest.issued=s.issued
                WHERE s.market=?
            """, (market_code, cutoff, market_code)).fetchall()
    except Exception:
        return []
    candidates = []
    for ticker, name, issued, price, verdict, score, entry_ok, data_valid, f5, f10, f20, f30, detail_json in rows:
        try:
            detail = json.loads(detail_json or "{}")
        except Exception:
            detail = {}
        forecasts = [float(x or 0) for x in (f5, f10, f20, f30)]
        positive_count = sum(x > 0 for x in forecasts)
        rvol = float(detail.get("rvol", 0) or 0)
        risk_reward = float(detail.get("risk_reward", 0) or 0)
        current_change = float(
            detail.get("screen_change", detail.get("change_percent", detail.get("change", 0))) or 0
        )
        trend_score = int(detail.get("continuous_rise_score", 0) or 0)
        continuous_rise = bool(detail.get("continuous_rise"))
        level_plan_valid = bool(detail.get("level_plan_valid"))
        repeat_state = str(detail.get("repeat_scalp_state", "UNAVAILABLE"))
        repeat_width = float(detail.get("repeat_scalp_range_percent", 0) or 0)
        verified_spread = detail.get("verified_spread_percent")
        try:
            verified_spread = float(verified_spread) if verified_spread is not None else None
        except (TypeError, ValueError):
            verified_spread = None
        score = float(score or 0)
        if market_code == "KR" and current_change >= 20.0:
            continue
        max_repeat_spread = 0.35 if market_code == "KR" else 0.25
        if not (
            data_valid and level_plan_valid and risk_reward >= 1.5
            and repeat_width >= 0.30
            and verified_spread is not None and verified_spread <= max_repeat_spread
        ):
            continue
        if repeat_state in {"UNAVAILABLE", "EXIT", "TAKE_PROFIT"}:
            continue
        if repeat_state == "BUY_PULLBACK":
            stage, priority = "🟢 눌림 반등 매수", 4
        elif repeat_state == "HOLD_OR_BREAKOUT":
            stage, priority = "🟢 돌파 매수 검토", 3
        elif repeat_state == "WAIT_PULLBACK":
            stage, priority = "🟡 눌림목 재매수 대기", 2
        else:
            stage, priority = "🔵 추세 재확인 대기", 1
        trigger = float(
            detail.get("structural_entry", 0)
            or detail.get("breakout_entry", 0)
            or detail.get("pullback_entry", 0)
            or 0
        )
        rank = priority * 100 + trend_score * 12 + score + positive_count * 5 + min(rvol, 5) * 2 + min(risk_reward, 4) * 2
        candidates.append({
            "ticker": ticker, "name": name or ticker, "stage": stage,
            "price": float(price or 0), "trigger": trigger, "score": score,
            "rvol": rvol, "risk_reward": risk_reward, "issued": int(issued), "rank": rank,
            "trend_score": trend_score,
            "repeat_state": repeat_state,
            "repeat_width": repeat_width,
            "target": float(detail.get("structural_target", 0) or 0),
            "support": float(detail.get("structural_support", 0) or 0),
        })
    return sorted(candidates, key=lambda x: x["rank"], reverse=True)[:limit]


def update_prediction_audit(ticker: str, price: float, item: dict, now_ts: float) -> list[dict]:
    bucket = float(int(now_ts // 300) * 300)
    with db_connect() as db:
        if price > 0:
            db.execute(
                "INSERT OR IGNORE INTO predictions(ticker,issued,base_price,f5,f10,f20,f30) VALUES(?,?,?,?,?,?,?)",
                (ticker, bucket, price, float(item.get("forecast_5m", 0) or 0),
                 float(item.get("forecast_10m", 0) or 0), float(item.get("forecast_20m", 0) or 0),
                 float(item.get("forecast_30m", 0) or 0)),
            )
        pending = db.execute(
            "SELECT id,issued,base_price,f5,f10,f20,f30,actual5,actual10,actual20,actual30 FROM predictions WHERE ticker=? AND issued>=?",
            (ticker, now_ts - 86400),
        ).fetchall()
        for row in pending:
            record_id, issued, base, f5, f10, f20, f30, a5, a10, a20, a30 = row
            elapsed = now_ts - issued
            updates = {}
            if elapsed >= 300 and a5 is None:
                updates["actual5"] = (price / base - 1) * 100
            if elapsed >= 600 and a10 is None:
                updates["actual10"] = (price / base - 1) * 100
            if elapsed >= 1200 and a20 is None:
                updates["actual20"] = (price / base - 1) * 100
            if elapsed >= 1800 and a30 is None:
                updates["actual30"] = (price / base - 1) * 100
            for column, value in updates.items():
                db.execute(f"UPDATE predictions SET {column}=? WHERE id=?", (value, record_id))
        rows = db.execute(
            "SELECT issued,base_price,f5,f10,f20,f30,actual5,actual10,actual20,actual30 FROM predictions WHERE ticker=? ORDER BY issued DESC LIMIT 100",
            (ticker,),
        ).fetchall()
    records = []
    for issued, base, f5, f10, f20, f30, a5, a10, a20, a30 in rows:
        record = {"ticker": ticker, "issued": issued, "기준시각": datetime.fromtimestamp(issued, KST).strftime("%m-%d %H:%M"),
                  "기준가": base, "예상5분": f5, "예상10분": f10, "예상20분": f20, "예상30분": f30}
        for minutes, expected, actual in ((5, f5, a5), (10, f10, a10), (20, f20, a20), (30, f30, a30)):
            if actual is not None:
                record[f"실제{minutes}분"] = round(actual, 3)
                record[f"적중{minutes}분"] = (expected >= 0) == (actual >= 0)
        records.append(record)
    return records


def calibration_stats(ticker: str) -> dict:
    stats = {}
    with db_connect() as db:
        for minutes in (5, 10, 20, 30):
            rows = db.execute(
                f"SELECT f{minutes},actual{minutes} FROM predictions WHERE ticker=? AND actual{minutes} IS NOT NULL ORDER BY issued DESC LIMIT 300",
                (ticker,),
            ).fetchall()
            if not rows:
                stats[minutes] = {"samples": 0, "accuracy": 0.0, "bias": 0.0, "mae": 0.0}
                continue
            errors = [float(expected) - float(actual) for expected, actual in rows]
            accuracy = sum((float(expected) >= 0) == (float(actual) >= 0) for expected, actual in rows) / len(rows) * 100
            stats[minutes] = {"samples": len(rows), "accuracy": accuracy,
                              "bias": sum(errors) / len(errors), "mae": sum(abs(x) for x in errors) / len(errors)}
    return stats


def render_chart(item: dict) -> None:
    times = item.get("chart_time_1m", [])
    closes = item.get("chart_close_1m", [])
    if not times or len(times) != len(closes):
        st.info("1분봉을 수집 중입니다.")
        return
    frame = pd.DataFrame({
        "시간": pd.to_datetime(times, errors="coerce"),
        "시가": item.get("chart_open_1m", []),
        "고가": item.get("chart_high_1m", []),
        "저가": item.get("chart_low_1m", []),
        "종가": closes,
        "VWAP": item.get("chart_vwap_1m", []),
        "EMA9": item.get("chart_ema9_1m", []),
        "EMA20": item.get("chart_ema20_1m", []),
        "신호": item.get("chart_signal_1m", []),
    }).dropna(subset=["시간"])
    frame["색상"] = frame.apply(lambda row: "상승" if row["종가"] >= row["시가"] else "하락", axis=1)
    color_scale = alt.Scale(domain=["상승", "하락"], range=["#ef5350", "#2962ff"])
    base = alt.Chart(frame).encode(x=alt.X("시간:T", title=None))
    wick = base.mark_rule().encode(
        y=alt.Y("저가:Q", scale=alt.Scale(zero=False), title="가격"), y2="고가:Q",
        color=alt.Color("색상:N", scale=color_scale, legend=None))
    body = base.mark_bar(size=7).encode(
        y="시가:Q", y2="종가:Q", color=alt.Color("색상:N", scale=color_scale, legend=None))
    lines = alt.Chart(frame).transform_fold(
        ["VWAP", "EMA9", "EMA20"], as_=["지표", "값"]
    ).mark_line(strokeWidth=2).encode(
        x="시간:T", y=alt.Y("값:Q", scale=alt.Scale(zero=False)),
        color=alt.Color("지표:N", scale=alt.Scale(
            domain=["VWAP", "EMA9", "EMA20"], range=["#f9a825", "#00a86b", "#8e44ad"]), title=None))
    buy = alt.Chart(frame[frame["신호"] == "BUY"]).mark_point(
        shape="triangle-up", filled=True, size=180, color="#00a86b").encode(x="시간:T", y="저가:Q")
    sell = alt.Chart(frame[frame["신호"] == "SELL"]).mark_point(
        shape="triangle-down", filled=True, size=180, color="#d32f2f").encode(x="시간:T", y="고가:Q")
    st.altair_chart((wick + body + lines + buy + sell).properties(height=430), use_container_width=True)


def structural_trade_plan(item: dict, market: str) -> dict:
    """실제 분봉 스윙 고저점과 VWAP·EMA로만 진입/매도/무효가를 만든다."""
    price = float(item.get("price", 0) or 0)
    highs = [float(x) for x in (item.get("chart_high_1m", []) or []) if float(x or 0) > 0]
    lows = [float(x) for x in (item.get("chart_low_1m", []) or []) if float(x or 0) > 0]
    closes = [float(x) for x in (item.get("chart_close_1m", []) or []) if float(x or 0) > 0]
    volumes = [float(x or 0) for x in (item.get("chart_volume_1m", []) or [])]
    if price <= 0 or len(highs) < 10 or len(lows) < 10:
        item["level_plan_valid"] = False
        item["level_plan_reason"] = "분봉 고가·저가가 부족해 지지·저항을 확정하지 못함"
        return item

    vwap = float(item.get("vwap", 0) or 0)
    ema9 = float(item.get("ema9", 0) or 0)
    ema20 = float(item.get("ema20", 0) or 0)
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0
    ret15 = (closes[-1] / closes[-16] - 1) * 100 if len(closes) >= 16 else 0.0
    ret30 = (closes[-1] / closes[-31] - 1) * 100 if len(closes) >= 31 else 0.0
    higher_high = len(highs) >= 12 and max(highs[-6:]) > max(highs[-12:-6])
    higher_low = len(lows) >= 12 and min(lows[-6:]) > min(lows[-12:-6])
    up_volume = down_volume = 0.0
    for index in range(max(1, len(closes) - 20), len(closes)):
        volume = volumes[index] if index < len(volumes) else 0.0
        if closes[index] > closes[index - 1]:
            up_volume += volume
        elif closes[index] < closes[index - 1]:
            down_volume += volume
    volume_dominance = up_volume / down_volume if down_volume > 0 else (2.0 if up_volume > 0 else 0.0)
    vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 99.0
    trend_checks = {
        "VWAP 위": price > vwap > 0,
        "EMA 정배열": price >= ema9 > ema20 > 0,
        "5분 상승": ret5 > 0,
        "15분 상승": ret15 > 0,
        "30분 상승": ret30 > 0,
        "고점 상승": higher_high,
        "저점 상승": higher_low,
        "상승봉 거래량 우세": volume_dominance >= 1.05,
        "VWAP 과대이격 아님": 0 <= vwap_gap <= (2.5 if market == "국내" else 3.0),
        "당일 고가권 유지": price >= max(highs) * 0.97,
    }
    trend_score = sum(bool(value) for value in trend_checks.values())
    item.update({
        "continuous_rise": trend_score >= 7 and ret15 > 0 and ret30 > 0,
        "continuous_rise_score": trend_score,
        "continuous_rise_checks": trend_checks,
        "trend_return_5m": ret5,
        "trend_return_15m": ret15,
        "trend_return_30m": ret30,
        "up_down_volume_ratio": volume_dominance,
    })

    swing_highs = [
        highs[i] for i in range(2, len(highs) - 2)
        if highs[i] >= max(highs[i - 2:i]) and highs[i] >= max(highs[i + 1:i + 3])
    ]
    resistance_candidates = [x for x in swing_highs if x > price]
    if not resistance_candidates:
        item["level_plan_valid"] = False
        item["level_plan_reason"] = "현재가 위에서 확인된 실제 1분봉 스윙 고점이 없음"
        return item
    resistance = min(resistance_candidates)
    resistance_reason = "최근 1분봉에서 확인된 가장 가까운 스윙 고점"

    swing_lows = [
        lows[i] for i in range(2, len(lows) - 2)
        if lows[i] <= min(lows[i - 2:i]) and lows[i] <= min(lows[i + 1:i + 3])
    ]
    support_candidates = [(x, "최근 1분봉 스윙 저점") for x in swing_lows if x < price]
    if 0 < vwap < price:
        support_candidates.append((vwap, "실제 체결량 가중 VWAP"))
    if 0 < ema9 < price:
        support_candidates.append((ema9, "1분봉 EMA9"))
    if not support_candidates:
        item["level_plan_valid"] = False
        item["level_plan_reason"] = "현재가 아래에서 확인된 실제 스윙 저점·VWAP·EMA 지지가 없음"
        return item
    support, support_reason = max(support_candidates, key=lambda value: value[0])
    stop = support
    entry = price
    risk = entry - stop
    reward = resistance - entry
    rr = reward / risk if risk > 0 else 0.0

    item.update({
        "structural_entry": entry,
        "structural_target": resistance,
        "structural_support": support,
        "stop_loss": stop,
        "risk_reward": rr,
        "level_plan_valid": reward > 0 and risk > 0,
        "target_basis": resistance_reason,
        "stop_basis": f"{support_reason} 이탈 시 상승 시나리오 무효",
        "level_plan_reason": f"{resistance_reason} {fmt(resistance)} / 지지선 {fmt(support)}",
    })
    return item


def repeat_scalp_plan(item: dict) -> dict:
    """Classify a repeatable trend scalp using only observed one-minute levels."""
    price = float(item.get("price", 0) or 0)
    support = float(item.get("structural_support", 0) or 0)
    target = float(item.get("structural_target", 0) or 0)
    vwap = float(item.get("vwap", 0) or 0)
    ema9 = float(item.get("ema9", 0) or 0)
    ema20 = float(item.get("ema20", 0) or 0)
    closes = [float(x) for x in (item.get("chart_close_1m", []) or []) if float(x or 0) > 0]
    highs = [float(x) for x in (item.get("chart_high_1m", []) or []) if float(x or 0) > 0]
    lows = [float(x) for x in (item.get("chart_low_1m", []) or []) if float(x or 0) > 0]
    volumes = [float(x or 0) for x in (item.get("chart_volume_1m", []) or [])]
    if not item.get("level_plan_valid") or min(price, support, target) <= 0 or len(closes) < 12:
        item.update({
            "repeat_scalp_state": "UNAVAILABLE",
            "repeat_scalp_label": "⚪ 반복단타 판정 대기",
            "repeat_scalp_reason": "실제 지지선과 위쪽 저항선이 모두 확인될 때까지 대기",
        })
        return item

    ranges = [max(0.0, highs[i] - lows[i]) for i in range(max(0, len(highs) - 20), len(highs))]
    median_range = float(pd.Series(ranges).median()) if ranges else 0.0
    median_volume = float(pd.Series(volumes[-20:]).median()) if volumes else 0.0
    last_volume = volumes[-1] if volumes else 0.0
    trend_score = int(item.get("continuous_rise_score", 0) or 0)
    ret15 = float(item.get("trend_return_15m", 0) or 0)
    swing_percent = ((target / support) - 1) * 100 if support > 0 and target > support else 0.0
    trend_intact = price >= vwap > 0 and ema9 >= ema20 > 0 and trend_score >= 6 and ret15 >= 0
    near_support = support <= price <= support + max(median_range, 1e-9)
    near_target = target >= price and target - price <= max(median_range, 1e-9)
    bounce = len(closes) >= 2 and closes[-1] > closes[-2] and lows[-1] <= support + max(median_range, 1e-9)
    volume_returns = median_volume <= 0 or last_volume >= median_volume
    recent_high = max(highs[-6:]) if len(highs) >= 12 else price
    prior_high = max(highs[-12:-6]) if len(highs) >= 12 else recent_high
    recent_low = min(lows[-6:]) if len(lows) >= 12 else price
    prior_low = min(lows[-12:-6]) if len(lows) >= 12 else recent_low
    lower_structure = recent_high < prior_high and recent_low < prior_low
    vwap_break_persistent = len(closes) >= 3 and vwap > 0 and all(value < vwap for value in closes[-3:])
    ema_bearish = ema9 < ema20 and ema20 > 0
    macd_bearish = float(item.get("macd_histogram", 0) or 0) < 0
    down_volume = up_volume = 0.0
    for index in range(max(1, len(closes) - 12), len(closes)):
        volume = volumes[index] if index < len(volumes) else 0.0
        if closes[index] < closes[index - 1]:
            down_volume += volume
        elif closes[index] > closes[index - 1]:
            up_volume += volume
    sell_volume_dominant = down_volume > up_volume * 1.15
    reversal_checks = {
        "VWAP 아래 3개 봉": vwap_break_persistent,
        "EMA9·EMA20 하락 정렬": ema_bearish,
        "고점·저점 동시 하락": lower_structure,
        "MACD 음전환": macd_bearish,
        "하락봉 거래량 우세": sell_volume_dominant,
    }
    reversal_score = sum(bool(value) for value in reversal_checks.values())
    breakdown = price < support or reversal_score >= 3

    if breakdown:
        state, label = "EXIT", "🔴 추세 꺾임·전량매도·재진입 금지"
        reason = (
            f"확인된 지지 {fmt(support)} 이탈 또는 하락 전환 근거 "
            f"{reversal_score}/5 동시 발생"
        )
    elif swing_percent < 1.0:
        state, label = "RANGE_TOO_NARROW", f"⚪ 이번 반복 예상 범위 약 +{swing_percent:.2f}%"
        reason = f"분봉에서 확인된 매수 {fmt(support)} 부근 → 매도 {fmt(target)} 부근"
    elif price >= target or near_target:
        state, label = "TAKE_PROFIT", "🟠 매도 접근·분할매도"
        reason = f"실제 1분봉 저항 {fmt(target)} 도달 구간"
    elif trend_intact and near_support and bounce and volume_returns:
        state, label = "BUY_PULLBACK", "🟢 눌림 반등 매수"
        reason = f"실제 지지 {fmt(support)} 방어 후 양봉 전환·거래량 유지"
    elif trend_intact and price > ema9 and volume_returns:
        state, label = "HOLD_OR_BREAKOUT", "🟢 보유·돌파 매수 검토"
        reason = f"VWAP·EMA 상승 구조 유지, 실제 저항 {fmt(target)}까지 공간 확인"
    elif trend_intact:
        state, label = "WAIT_PULLBACK", "🟡 눌림목 재매수 대기"
        reason = f"추격하지 말고 실제 지지 {fmt(support)} 반등을 기다림"
    else:
        state, label = "WAIT_TREND", "🔵 추세 재확인 대기"
        reason = "VWAP·EMA 정렬과 15분 상승 흐름이 다시 일치할 때까지 대기"

    item.update({
        "repeat_scalp_state": state,
        "repeat_scalp_label": label,
        "repeat_scalp_reason": reason,
        "repeat_scalp_buy_level": support,
        "repeat_scalp_sell_level": target,
        "repeat_scalp_invalidation": support,
        "repeat_scalp_median_bar_range": median_range,
        "repeat_scalp_reversal_score": reversal_score,
        "repeat_scalp_reversal_checks": reversal_checks,
        "repeat_scalp_range_percent": swing_percent,
    })
    return item


def precise_analysis(row: dict, mode: str) -> dict:
    raw = scanner().analyze(dict(row), mode)
    item = apply_mode_policy(finalize_trade_item(raw), mode)
    if not mode.startswith("국내"):
        item = normalize_us_item(item, row)
    # The bundled engine reads Korean total depth but historically discarded
    # level-1 bid/ask prices from the same REST response. Hydrate those exact
    # KIS fields for direct searches instead of treating them as unavailable.
    if mode.startswith("국내") and "직접" in str(row.get("asset_type", "")):
        try:
            payload = scanner().client._get(
                "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                "FHKST01010200",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": str(row.get("ticker", ""))},
            )
            depth = payload.get("output1") or payload.get("output") or {}
            item["best_ask"] = float(depth.get("askp1", 0) or 0)
            item["best_bid"] = float(depth.get("bidp1", 0) or 0)
            item["ask_total"] = float(depth.get("total_askp_rsqn", item.get("ask_total", 0)) or 0)
            item["bid_total"] = float(depth.get("total_bidp_rsqn", item.get("bid_total", 0)) or 0)
            item["orderbook_source"] = "한국투자증권 REST 1호가"
        except Exception as error:
            item["orderbook_fetch_error"] = f"{type(error).__name__}: {error}"
    # The quote endpoint and the minute-bar endpoint do not always complete at
    # the same instant. Retry the lightweight quote/order-book refresh before
    # declaring depth data missing; never request a new OAuth token here.
    for _ in range(2):
        bid = float(item.get("best_bid", 0) or 0)
        ask = float(item.get("best_ask", 0) or 0)
        if bid > 0 and ask > 0:
            break
        try:
            refreshed = scanner().refresh_quotes([item], mode)
            if refreshed:
                item.update(refreshed[0])
        except Exception:
            pass
        time.sleep(0.2)
    market = "국내" if mode.startswith("국내") else "미국"
    if market == "미국":
        item = normalize_us_item(item, row)
    item = repeat_scalp_plan(structural_trade_plan(item, market))
    _, gate_ok, spread = data_quality_gate(item, market)
    item["data_gate_passed"] = bool(gate_ok)
    item["verified_spread_percent"] = spread
    return item


def background_audit_tick(enabled: bool, now_ts: float, ui_market: str) -> None:
    """열어 둔 앱 안에서 토큰을 공유하며 선택 시장 종목을 순환 검증한다."""
    market_code = "KR" if ui_market == "국내" else "US"
    base_members = AUDIT_KR_UNIVERSE if market_code == "KR" else AUDIT_US_UNIVERSE
    if not enabled or AUDIT_IMPORT_ERROR or not base_members:
        return
    now_dt = datetime.fromtimestamp(now_ts, KST)
    if not audit_market_is_open(market_code, now_dt):
        return
    last_key = f"audit_last_tick::{market_code}"
    index_key = f"audit_member_index::{market_code}"
    last = float(st.session_state.get(last_key, 0.0))
    # Heavy full-minute-bar validation must not monopolize the interactive app.
    # Candidate discovery remains cached and lightweight; validation samples
    # one symbol per minute while the user can search and trade immediately.
    if now_ts - last < 60:
        return
    dynamic_rows = live_filtered_universe(ui_market)
    dynamic_members = [
        (str(row.get("ticker")), str(row.get("name") or row.get("ticker")), str(row.get("exchange") or "KR"))
        for row in dynamic_rows if row.get("ticker")
    ]
    audit_members = dynamic_members or base_members
    index = int(st.session_state.get(index_key, 0)) % len(audit_members)
    ticker, name, exchange_name = audit_members[index]
    row = {"ticker": ticker, "name": name, "exchange": exchange_name, "asset_type": "검증대상"}
    try:
        with audit_connect() as db:
            if audit_signal_window_open(market_code, now_dt):
                audit_mode = "국내 30분 1% 타점" if market_code == "KR" else "미국 30분 1% 타점"
                item = precise_analysis(row, audit_mode)
                audit_store_result(db, market_code, item, int(now_ts), 60)
            else:
                quote = (scanner().client.kr_quote(ticker) if market_code == "KR"
                         else scanner().client.us_quote(ticker, exchange_name))
                audit_store_quote(db, market_code, ticker, float(quote.get("price", 0) or 0), int(now_ts))
            audit_grade_pending(db, int(now_ts))
            db.commit()
            audit_export_summary(db)
        st.session_state["audit_last_ok"] = f"{market_code} {ticker} · {now_dt.strftime('%H:%M:%S')}"
        st.session_state.pop("audit_last_error", None)
    except Exception as error:
        st.session_state["audit_last_error"] = f"{ticker}: {type(error).__name__} · {error}"
    finally:
        st.session_state[last_key] = now_ts
        st.session_state[index_key] = index + 1


st.title("⚡ 초단타 VWAP 매수타점")
st.caption("집중 모드에서는 약 2.5초마다 화면을 갱신하고, 20초마다 1분봉·VWAP·EMA·거래량·호가를 정밀 재분석합니다.")


with st.sidebar:
    st.header("초단타 설정")
    market = st.radio("시장", ["국내", "미국"], horizontal=True)
    mode = "국내 30분 1% 타점" if market == "국내" else "미국 30분 1% 타점"
    exchange = "KR" if market == "국내" else "자동 판별"
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input("종목명 또는 종목코드 검색", placeholder="현대차, 005380, SOXL").strip()
    run_mode = st.radio(
        "실행 모드",
        ["가벼운 현재가", "선택 종목 집중", "오늘 한국장 자동검증"],
        horizontal=False,
        key="scalp_run_mode",
        help="한 번에 하나의 모드만 실행해 API 과호출과 상태 충돌을 막습니다.",
    )
    focus_only = run_mode == "선택 종목 집중"
    auto_audit = run_mode == "오늘 한국장 자동검증"
    require_validation = st.toggle("실전 검증 잠금", True, help="해당 종목의 실제 5·10분 검증표본이 쌓이기 전에는 초록색 매수 신호를 차단합니다.")
    st.caption("분석 대상: 우량주·ETF·레버리지 ETF")

now = time.time()
manual_search_active = bool(manual_ticker)
# 두 실시간 기능을 모두 꺼도 현재가만 가볍게 움직이도록 5초 타이머를
# 유지한다. 무거운 후보·분봉·지수 계산은 아래에서 live_refresh_active로
# 별도 차단한다.
live_refresh_active = bool(focus_only or auto_audit)
st_autorefresh(
    interval=2500 if focus_only else 8000 if auto_audit else 5000,
    key="scalp_tick",
)
if AUDIT_IMPORT_ERROR:
    st.sidebar.error("자동검증 파일이 없습니다: run_live_validation.py")
elif auto_audit:
    # Direct search has priority over the background universe collector.
    # The collector resumes automatically as soon as the search box is empty.
    audit_paused_for_focus = manual_search_active or focus_only
    if not audit_paused_for_focus:
        background_audit_tick(True, now, market)
    audit_now = datetime.fromtimestamp(now, KST)
    audit_minute = audit_now.hour * 60 + audit_now.minute
    if audit_now.weekday() >= 5:
        audit_phase = "휴장일 · 다음 영업일 대기"
    elif audit_minute < 8 * 60 + 50:
        audit_phase = "준비 완료 · 08:50 자동 시작"
    elif audit_minute < 9 * 60:
        audit_phase = "사전 시세 확인 중 · 09:00 신호 시작"
    elif audit_minute < 15 * 60:
        audit_phase = "신호 수집·사후 채점 중"
    elif audit_minute <= 15 * 60 + 35:
        audit_phase = "신규 신호 종료 · 30분 사후 채점 중"
    else:
        audit_phase = "오늘 검증 완료 · 결과를 내려받으세요"
    last_audit_ok = st.session_state.get("audit_last_ok")
    displayed_phase = "집중분석 우선 · 후보 수집 일시정지" if audit_paused_for_focus else audit_phase
    st.sidebar.success(
        "자동검증 · " + displayed_phase
        + (f"\n\n최근 처리: {last_audit_ok}" if last_audit_ok else "")
    )
    if st.session_state.get("audit_last_error"):
        st.sidebar.warning(st.session_state["audit_last_error"])
    if AUDIT_CSV_PATH.exists():
        st.sidebar.download_button(
            "검증 CSV 내려받기", AUDIT_CSV_PATH.read_bytes(),
            file_name="validation_summary.csv", mime="text/csv", key="audit_csv_download",
        )
    audit_report_path = AUDIT_DB_PATH.parent / "validation_report.html"
    if audit_report_path.exists():
        st.sidebar.download_button(
            "검증 보고서 내려받기", audit_report_path.read_bytes(),
            file_name="validation_report.html", mime="text/html", key="audit_report_download",
        )
    try:
        with audit_connect() as audit_db:
            total_signals = int(audit_db.execute("SELECT COUNT(*) FROM signals").fetchone()[0])
            completed_signals = int(audit_db.execute("SELECT COUNT(*) FROM signals WHERE result_done=1").fetchone()[0])
        st.sidebar.caption(f"저장 {total_signals}건 · 30분 채점완료 {completed_signals}건")
    except Exception:
        pass

candidate_board = [] if manual_search_active else latest_entry_candidates(market, minimum_score)
st.subheader("실시간 진입 후보")
if manual_search_active:
    st.caption("직접 검색 우선 모드 · 자동 후보 수집을 잠시 멈추고 선택 종목만 분석합니다.")
elif candidate_board:
    board_rows = []
    for candidate in candidate_board:
        support_text = fmt(candidate["support"])
        trigger_text = "현재 조건 충족" if candidate["stage"].startswith("🟢") else f"{support_text} 지지·VWAP 회복 확인"
        board_rows.append({
            "판정": candidate["stage"], "종목": f"{candidate['ticker']} · {candidate['name']}",
            "현재가": candidate["price"], "진입 발동": trigger_text,
            "실제 저항": candidate["target"], "무효 지지": candidate["support"],
            "지속상승": f"{candidate['trend_score']}/10",
            "점수": round(candidate["score"]), "RVOL": round(candidate["rvol"], 1),
            "손익비": round(candidate["risk_reward"], 2),
            "분석시각": datetime.fromtimestamp(candidate["issued"], KST).strftime("%H:%M:%S"),
        })
    st.dataframe(pd.DataFrame(board_rows), hide_index=True, use_container_width=True)
    st.caption("🟢도 주문 보장이 아니라 현재 데이터의 진입 조건 충족입니다. 표시된 발동가·손절 조건을 함께 확인하세요.")
else:
        if focus_only:
            st.info(
                "정밀 검증을 통과한 진입 후보가 없습니다. 집중 모드에서는 아래 상승·유동성 목록에서 "
                "선택한 한 종목만 빠르게 정밀 분석합니다."
            )
        else:
            st.info("현재 진입 조건에 가까운 후보가 없습니다. 자동검증기가 전 종목을 한 바퀴 도는 동안 갱신됩니다.")

options = live_filtered_universe(market) if not manual_ticker else []
resolved_manual = None
if manual_ticker:
    resolved = resolve_manual(manual_ticker, market)
    if resolved:
        resolved_manual = resolved
        options.insert(0, resolved)
    else:
        st.sidebar.error("종목을 찾지 못했습니다. 이름을 조금 더 정확히 입력해 주세요.")
        options = live_filtered_universe(market)
dedup = {}
for row in options:
    ticker = str(row.get("ticker", "")).upper()
    if ticker:
        dedup[ticker] = row
options = list(dedup.values())
# 수집 단계의 필터를 통과했더라도 캐시·이전 선택·검증 DB에서 다시
# 유입될 수 있다. 자동 국내 후보는 화면 직전에도 +20% 이상을 강제 제외한다.
# 직접 검색은 사용자가 원하는 종목을 확인할 수 있도록 예외로 둔다.
if market == "국내" and not resolved_manual:
    options = [
        row for row in options
        if 0 < float(
            row.get("screen_change", row.get("change_percent", row.get("change", 0))) or 0
        ) < 20.0
    ]

if not options:
    st.warning("현재 가격 조건을 통과하고 시세가 확인된 자동 후보가 없습니다. 원하는 종목을 직접 검색해 주세요.")
    st.stop()

selected_ticker = st.selectbox(
    "집중 분석할 종목",
    [str(row.get("ticker", "")) for row in options],
    format_func=lambda ticker: next(
        (f"{ticker} · {row.get('name', ticker)} · {row.get('asset_type', '')}"
         for row in options if str(row.get("ticker", "")) == ticker), ticker),
    key=f"focus_ticker::{market}::{resolved_manual.get('ticker') if resolved_manual else 'default'}",
)
selected_row = next(row for row in options if str(row.get("ticker", "")) == selected_ticker)
selected_row.setdefault("exchange", "KR" if market == "국내" else "NASDAQ")

if st.session_state.get("scalp_selected") != selected_ticker:
    st.session_state["scalp_selected"] = selected_ticker
    st.session_state["scalp_last_precise"] = 0.0
    st.session_state.pop("scalp_latest", None)
    st.session_state["scalp_live_history"] = []

latest = dict(st.session_state.get("scalp_latest", {}))
precise_refresh_seconds = 20 if focus_only else 60
precise_due = bool(
    not latest
    or (
        live_refresh_active
        and now - float(st.session_state.get("scalp_last_precise", 0)) >= precise_refresh_seconds
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
    try:
        refreshed = scanner().refresh_quotes([latest], mode)
        if refreshed:
            latest.update(refreshed[0])
            if market == "미국":
                latest = normalize_us_item(latest, selected_row)
            latest = repeat_scalp_plan(structural_trade_plan(latest, market))
            st.session_state["scalp_latest"] = latest
        st.session_state["scalp_last_quote"] = now
    except Exception as error:
        st.session_state["scalp_quote_error"] = str(error)

if st.session_state.get("scalp_error"):
    st.error(f"분석 대기: {st.session_state['scalp_error']}")
if not latest:
    st.stop()

if latest.get("intraday_fallback"):
    st.warning("KIS 미국 분봉이 부족하여 Yahoo 보조 분봉을 표시합니다. 이때는 실시간 매수 신호를 내지 않고 관찰만 합니다.")

price = float(latest.get("price", 0) or 0)
change = float(latest.get("change_percent", 0) or 0)
calibration = calibration_stats(selected_ticker) if require_validation else {
    horizon: {"samples": 0, "accuracy": 0.0, "mae": 0.0, "bias": 0.0}
    for horizon in (5, 10, 20, 30)
}
for horizon in (5, 10, 20, 30):
    stat = calibration[horizon]
    if stat["samples"] >= 20:
        key = f"forecast_{horizon}m"
        latest[key] = round(float(latest.get(key, 0) or 0) - float(stat["bias"]), 3)
validated_signal = (
    calibration[5]["samples"] >= 20 and calibration[10]["samples"] >= 20
    and calibration[5]["accuracy"] >= 55 and calibration[10]["accuracy"] >= 55
)
label, level = verdict_text(latest)
quality_rows, quality_passed, spread_pct = data_quality_gate(latest, market)
regime_name, regime_method = market_regime(latest)
strategy_rows, buy_votes, sell_votes, wait_votes = strategy_consensus(latest)
weighted_score, weighted_buy = weighted_strategy_score(strategy_rows, regime_name)
context_key = f"scalp_context::{market}::{selected_ticker}"
if live_refresh_active or context_key not in st.session_state:
    st.session_state[context_key] = benchmark_context(market, selected_ticker)
context = st.session_state[context_key]
forecast_up = float(latest.get("forecast_5m", 0) or 0) > 0
context_aligned = bool(context.get("confirmed")) and (
    not forecast_up or (
        float(context.get("change", 0) or 0) >= -0.05
        and float(context.get("intraday", 0) or 0) >= -0.10
    )
)
risk_reward = float(latest.get("risk_reward", 0) or 0)
price_limit = 300000 if market == "국내" else 200
is_manual_search = str(selected_row.get("asset_type", "")) == "직접 검색"
continuous_rise = bool(latest.get("continuous_rise"))
continuous_rise_score = int(latest.get("continuous_rise_score", 0) or 0)
repeat_state = str(latest.get("repeat_scalp_state", "UNAVAILABLE"))
repeat_label = str(latest.get("repeat_scalp_label", "⚪ 반복단타 판정 대기"))
repeat_buy = float(latest.get("repeat_scalp_buy_level", 0) or 0)
repeat_sell = float(latest.get("repeat_scalp_sell_level", 0) or 0)
repeat_stop = float(latest.get("stop_loss", latest.get("repeat_scalp_invalidation", 0)) or 0)
repeat_width = float(latest.get("repeat_scalp_range_percent", 0) or 0)
if repeat_state == "EXIT":
    regime_name = "하락 전환"
    regime_method = "신규 매수 중단·실제 지지 회복과 하락 전환 해소 확인"
if price <= 0:
    st.error("⛔ 현재가가 확인되지 않아 분석과 매수 판정을 중단했습니다.")
    st.stop()
if price > price_limit and not is_manual_search:
    level, label = "error", f"🔴 설정 금액 초과 · {fmt(price_limit)} 이하만 분석 대상"
    latest["entry_checks_passed"] = False
elif not quality_passed:
    level, label = "error", "🔴 판정 불가 · 실시간 데이터 검문 미통과"
    latest["entry_checks_passed"] = False
elif not bool(latest.get("level_plan_valid")):
    level, label = "error", "🔴 진입 금지 · 실제 지지·저항 가격대 미확인"
    latest["entry_checks_passed"] = False
elif not is_manual_search and not continuous_rise:
    level, label = "error", f"🔴 후보 제외 · 장중 지속상승 {continuous_rise_score}/10"
    latest["entry_checks_passed"] = False
elif not context_aligned:
    level, label = "error", f"🔴 진입 금지 · 기초지수/시장({context.get('name')}) 동조 미확인"
    latest["entry_checks_passed"] = False
elif risk_reward < 1.5:
    level, label = "error", f"🔴 진입 금지 · 손익비 {risk_reward:.2f} (최소 1.50 필요)"
    latest["entry_checks_passed"] = False
elif repeat_state == "EXIT":
    level, label = "error", repeat_label
    latest["entry_checks_passed"] = False
elif repeat_state == "TAKE_PROFIT":
    level, label = "warning", repeat_label
    latest["entry_checks_passed"] = False
elif repeat_state in {"BUY_PULLBACK", "HOLD_OR_BREAKOUT"} and buy_votes >= 4 and sell_votes <= 2:
    level, label = "success", repeat_label
    latest["entry_checks_passed"] = True
elif sell_votes >= 3:
    level, label = "error", f"🔴 진입 금지 · 매도/약세 기법 {sell_votes}개 감지"
    latest["entry_checks_passed"] = False
elif weighted_score < 35 or weighted_buy < 55:
    level, label = "warning", f"🟡 대기 · 장세가중 합의 {weighted_score:+.1f}점"
    latest["entry_checks_passed"] = False
elif buy_votes < 6 or sell_votes > 0:
    level, label = "warning", f"🟡 대기 · 매수 합의 {buy_votes}/10 · 매도 경고 {sell_votes}/10"
    latest["entry_checks_passed"] = False
elif require_validation and not validated_signal:
    level, label = "warning", "🟡 모의검증 중 · 실제 적중표본이 쌓이기 전 실전 신호 잠금"
    latest["entry_checks_passed"] = False
elif level == "success":
    label = f"🟢 매수 검토 · 주요 기법 합의 {buy_votes}/10"

# The first thing a trader sees is one unambiguous action. Detailed indicators
# remain below as evidence, not as competing instructions.
if repeat_state == "BUY_PULLBACK" and level == "success":
    action_class, action_title = "buy", "🟢 지금 매수 구간"
    action_line = f"{fmt(repeat_buy)} 부근 분할매수 → {fmt(repeat_sell)} 부근 분할매도"
elif repeat_state == "HOLD_OR_BREAKOUT" and level == "success":
    action_class, action_title = "buy", "🟢 돌파 확인 후 매수"
    action_line = f"현재가 지지 확인 → {fmt(repeat_sell)} 부근 분할매도"
elif repeat_state == "TAKE_PROFIT":
    action_class, action_title = "sell", "🟠 지금 분할매도"
    action_line = f"확인된 저항 {fmt(repeat_sell)} 도달 구간 · 추격매수 금지"
elif repeat_state == "EXIT":
    action_class, action_title = "stop", "🔴 반복단타 종료·매도"
    action_line = f"추세가 꺾였습니다. {fmt(repeat_stop)} 이탈 시 재진입하지 마세요."
elif repeat_state == "RANGE_TOO_NARROW":
    action_class, action_title = "wait", f"🟡 이번 반복 예상 범위 약 +{repeat_width:.2f}%"
    action_line = f"매수 {fmt(repeat_buy)} 부근 → 매도 {fmt(repeat_sell)} 부근 · 1% 목표에는 미달"
else:
    action_class, action_title = "wait", "🟡 지금은 대기"
    action_line = f"{fmt(repeat_buy)} 지지 반등 또는 매수 합의가 확인될 때까지 매수하지 마세요."
st.markdown(
    f'<div class="trade-action {action_class}"><h2>{action_title}</h2>'
    f'<p><b>{action_line}</b></p><p>손절·무효 기준: {fmt(repeat_stop)} · '
    f'확인된 반복 폭: {repeat_width:.2f}%</p></div>',
    unsafe_allow_html=True,
)
top = st.columns([1.4, 1, 1, 1, 1])
top[0].metric(f"{latest.get('ticker')} · {latest.get('name')}", fmt(price), f"{change:+.2f}%")
top[1].metric("VWAP", fmt(latest.get("vwap")))
top[2].metric("EMA9", fmt(latest.get("ema9")))
top[3].metric("RVOL", f"{float(latest.get('rvol', 0) or 0):.1f}배")
top[4].metric("하락위험", f"{int(latest.get('five_min_risk_score', 0) or 0)}점")

status_cols = st.columns(4)
status_cols[0].metric("데이터 검문", "통과" if quality_passed else "실패")
status_cols[1].metric("현재 장세", regime_name)
status_cols[2].metric(
    f"기초지수 {context.get('name')}",
    f"{float(context.get('change', 0) or 0):+.2f}%",
    f"최근 5분 {float(context.get('intraday', 0) or 0):+.2f}%" if context.get("confirmed") else "분봉 미확인",
)
status_cols[3].metric("손익비", f"{risk_reward:.2f}")
st.caption(f"현재 적용 기법: {regime_method}")

level_cols = st.columns(4)
level_cols[0].metric("진입 기준가", fmt(latest.get("structural_entry")))
level_cols[1].metric("1차 저항 매도가", fmt(latest.get("structural_target")))
level_cols[2].metric("확인된 지지선", fmt(latest.get("structural_support")))
level_cols[3].metric("시나리오 무효·손절", fmt(latest.get("stop_loss")))
st.caption(
    f"매도가 근거: {latest.get('target_basis', '미확인')} · "
    f"손절 근거: {latest.get('stop_basis', latest.get('level_plan_reason', '미확인'))}"
)
st.caption(
    f"장중 지속상승 판정 {continuous_rise_score}/10 · "
    f"5분 {'상승' if float(latest.get('trend_return_5m', 0) or 0) > 0 else '하락'} · "
    f"15분 {'상승' if float(latest.get('trend_return_15m', 0) or 0) > 0 else '하락'} · "
    f"30분 {'상승' if float(latest.get('trend_return_30m', 0) or 0) > 0 else '하락'}"
)
with st.expander("장중 지속상승 판정 근거", expanded=False):
    trend_rows = [
        {"조건": key, "통과": "✅" if value else "❌"}
        for key, value in (latest.get("continuous_rise_checks", {}) or {}).items()
    ]
    if trend_rows:
        st.dataframe(pd.DataFrame(trend_rows), hide_index=True, use_container_width=True)

st.subheader("추세 반복단타")
repeat_reason = str(latest.get("repeat_scalp_reason", "실제 지지·저항 확인 대기"))
repeat_message = (
    f"{repeat_label} · 매수 기준 {fmt(latest.get('repeat_scalp_buy_level'))} · "
    f"매도 기준 {fmt(latest.get('repeat_scalp_sell_level'))} · {repeat_reason}"
)
if repeat_state == "EXIT":
    st.error(repeat_message)
elif repeat_state == "TAKE_PROFIT":
    st.warning(repeat_message)
elif repeat_state in {"BUY_PULLBACK", "HOLD_OR_BREAKOUT"}:
    st.success(repeat_message)
else:
    st.info(repeat_message)
with st.expander("추세 꺾임 판정 근거", expanded=repeat_state == "EXIT"):
    reversal_rows = [
        {"하락 전환 조건": key, "감지": "예" if value else "아니오"}
        for key, value in (latest.get("repeat_scalp_reversal_checks", {}) or {}).items()
    ]
    if reversal_rows:
        st.dataframe(pd.DataFrame(reversal_rows), hide_index=True, use_container_width=True)

getattr(st, level)(label)
if level == "success":
    st.write(
        f"진입: **{fmt(latest.get('structural_entry'))}** · "
        f"1차 저항 매도: **{fmt(latest.get('structural_target'))}** · "
        f"손절: **{fmt(latest.get('stop_loss'))}**"
    )
elif level == "error":
    st.write(latest.get("invalidation_reason", "VWAP·EMA·호가 조건 이탈"))
else:
    st.write(latest.get("entry_trigger", "VWAP 지지와 거래량 재증가를 기다리세요."))

pullback_price = float(latest.get("pullback_entry", 0) or 0)
breakout_price = float(latest.get("breakout_entry", 0) or 0)
stop_price = float(latest.get("stop_loss", 0) or 0)
if level == "success":
    st.success(
        f"▶ 지금 진입 검토: 현재 체결가 {fmt(price)} · "
        f"최근 스윙 저항 {fmt(latest.get('structural_target'))}에서 1차 매도 · "
        f"확인된 지지 {fmt(stop_price)} 이탈 시 매수 판단 무효"
    )
elif level == "warning":
    st.warning(
        f"▶ 지금은 매수하지 않음 · 확인된 지지 {fmt(latest.get('structural_support'))}에서 반등하고 "
        f"VWAP·EMA를 회복해 매수 합의 6/10 이상이 될 때만 진입 검토"
    )
else:
    if price > price_limit and not is_manual_search:
        st.error("▶ 자동 후보 가격 조건 초과 · 이 종목을 직접 검색하면 금액 제한 없이 다시 판정합니다.")
    else:
        st.error(f"▶ 현재는 진입하지 않음 · {fmt(stop_price)} 위 회복과 매수 합의 재형성 전까지 관찰")

consensus_cols = st.columns(4)
consensus_cols[0].metric("매수 기법", f"{buy_votes}/10")
consensus_cols[1].metric("매도 기법", f"{sell_votes}/10")
consensus_cols[2].metric("대기 기법", f"{wait_votes}/10")
consensus_cols[3].metric("장세가중 합의", f"{weighted_score:+.1f}점", f"매수비중 {weighted_buy:.1f}%")
with st.expander("매수·매도 기법별 판정 근거", expanded=False):
    st.dataframe(pd.DataFrame(strategy_rows), hide_index=True, use_container_width=True)
with st.expander("실시간 데이터 검문 내역", expanded=not quality_passed):
    st.dataframe(pd.DataFrame(quality_rows), hide_index=True, use_container_width=True)

f5 = float(latest.get("forecast_5m", 0) or 0)
f10 = float(latest.get("forecast_10m", 0) or 0)
f20 = float(latest.get("forecast_20m", 0) or 0)
f30 = float(latest.get("forecast_30m", 0) or 0)
forecast_cols = st.columns(4)
for column, minutes, forecast in zip(forecast_cols, (5, 10, 20, 30), (f5, f10, f20, f30)):
    direction = forecast_label(forecast)
    if forecast >= 0.35:
        grounded_price = fmt(latest.get("structural_target"))
        basis = latest.get("target_basis", "스윙 고점")
    elif forecast <= -0.35:
        grounded_price = fmt(latest.get("structural_support"))
        basis = latest.get("stop_basis", "스윙 저점")
    else:
        grounded_price = f"{fmt(latest.get('structural_support'))}<br>~ {fmt(latest.get('structural_target'))}"
        basis = "확인된 지지·저항 사이"
    column.markdown(
        f'<div class="forecast-card"><div class="title">{minutes}분 판정 · {direction}</div>'
        f'<div class="price">{grounded_price}</div><div class="basis">{basis}</div></div>',
        unsafe_allow_html=True,
    )

target_probability = int(latest.get("target1_probability", 0) or 0)
if level == "success" and min(f5, f10, f20, f30) > 0:
    st.success(
        f"🚦 매수 전 조건 통과 · 5·10·20·30분 방향 일치 · "
        f"1차 목표 도달 추정 {target_probability}%"
    )
elif min(f5, f10, f20, f30) < 0:
    st.error("⛔ 단기 예상이 약세입니다. 신규 진입하지 마세요.")
else:
    st.warning("⏳ 시간대별 방향이 일치하지 않습니다. 진입을 기다리세요.")

st.caption("표시 가격은 임의 퍼센트 목표가가 아니라 현재 1분봉에서 실제로 확인된 지지·저항 가격입니다. 확인되지 않으면 숫자를 만들지 않고 진입을 차단합니다.")

render_chart(latest)

with st.expander("진입 전 뉴스·공시 위험을 지금 한 번 확인"):
    if st.button("뉴스·SEC·거래정지 확인", use_container_width=True):
        with st.spinner("위험자료 확인 중..."):
            try:
                checked = scanner().analyze_candidate(selected_row, mode)
                st.session_state["scalp_risk_check"] = checked
                st.session_state["scalp_latest"] = checked
            except Exception as error:
                st.error(str(error))
    checked = st.session_state.get("scalp_risk_check")
    if checked and str(checked.get("ticker")) == selected_ticker:
        st.write(checked.get("news_summary", "뉴스 확인 완료"))
        st.write("규제검증:", checked.get("regulatory_checked", False), "· 거래정지:", checked.get("halt_active", False))

audit_records = update_prediction_audit(selected_ticker, price, latest, now) if require_validation else []
completed = []
for record in audit_records:
    if record["ticker"] != selected_ticker:
        continue
    for minutes in (5, 10, 20, 30):
        if f"실제{minutes}분" in record:
            completed.append({
                "기준시각": record["기준시각"], "구간": f"{minutes}분",
                "예상(%)": record[f"예상{minutes}분"], "실제(%)": record[f"실제{minutes}분"],
                "방향 적중": "적중" if record[f"적중{minutes}분"] else "실패",
            })
with st.expander("자동 적중률 검증 기록", expanded=False):
    stat_cols = st.columns(4)
    for column, minutes in zip(stat_cols, (5, 10, 20, 30)):
        stat = calibration[minutes]
        column.metric(f"{minutes}분 검증", f"{stat['accuracy']:.1f}%", f"표본 {stat['samples']}건 · 평균오차 {stat['mae']:.2f}%")
    if completed:
        accuracy = sum(row["방향 적중"] == "적중" for row in completed) / len(completed) * 100
        st.metric("이번 앱 실행 중 방향 적중률", f"{accuracy:.1f}%", f"검증 {len(completed)}건")
        st.dataframe(pd.DataFrame(completed[-30:]), hide_index=True, use_container_width=True)
    else:
        st.info("신호를 자동 저장했습니다. 5분 후부터 실제 결과와 비교합니다.")

st.caption(f"마지막 정밀분석: {latest.get('updated_at', '-')} · 화면 시각: {datetime.now(KST).strftime('%H:%M:%S')}")
