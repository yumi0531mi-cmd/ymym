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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

try:
    from run_live_validation import (
        KR_UNIVERSE as AUDIT_KR_UNIVERSE,
        connect as audit_connect,
        export_summary as audit_export_summary,
        grade_pending as audit_grade_pending,
        signal_window_open as audit_signal_window_open,
        store_quote as audit_store_quote,
        store_result as audit_store_result,
        DB_PATH as AUDIT_DB_PATH,
        CSV_PATH as AUDIT_CSV_PATH,
    )
    AUDIT_IMPORT_ERROR = ""
except Exception as audit_import_exception:
    AUDIT_KR_UNIVERSE = []
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
    {"ticker":"069500","name":"KODEX 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"102110","name":"TIGER 200","exchange":"KR","asset_type":"ETF"},
    {"ticker":"396500","name":"TIGER 반도체TOP10","exchange":"KR","asset_type":"ETF"},
    {"ticker":"122630","name":"KODEX 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"123320","name":"TIGER 레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"488080","name":"TIGER 반도체TOP10레버리지","exchange":"KR","asset_type":"레버리지 ETF"},
    {"ticker":"423920","name":"TIGER 미국필라델피아반도체레버리지(합성)","exchange":"KR","asset_type":"레버리지 ETF"},
]
US_UNIVERSE = [
    {"ticker":"GOOGL","name":"알파벳","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"AMD","name":"AMD","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"INTC","name":"인텔","exchange":"NASDAQ","asset_type":"우량주"},
    {"ticker":"SMH","name":"반도체 ETF","exchange":"NASDAQ","asset_type":"ETF"},
    {"ticker":"SOXL","name":"반도체 3배 레버리지 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"SOXS","name":"반도체 3배 인버스 ETF","exchange":"AMEX","asset_type":"레버리지 ETF"},
    {"ticker":"TQQQ","name":"나스닥100 3배 레버리지 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
    {"ticker":"SQQQ","name":"나스닥100 3배 인버스 ETF","exchange":"NASDAQ","asset_type":"레버리지 ETF"},
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
  @media(max-width:700px){.block-container{padding:.55rem}.stMetric{font-size:.8rem}}
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
        last_bar = pd.to_datetime(times[-1], utc=True)
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
    return checks, all(bool(row["통과"]) for row in checks), spread


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
            change = float(quote.get("change", 0) or 0) * direction
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


@st.cache_data(ttl=60, show_spinner=False)
def live_filtered_universe(market: str) -> list[dict]:
    """상승 종목을 먼저 찾고 가격·유동성을 통과한 종목만 후보로 사용한다."""
    source = KR_UNIVERSE if market == "국내" else US_UNIVERSE
    limit = 300000 if market == "국내" else 200
    accepted = []
    if market == "국내":
        try:
            ranked = scanner().candidates("국내 돌파")
            blocked_words = ("스팩", "우선주", "관리", "정리매매", "인버스")
            for row in ranked:
                candidate = dict(row)
                price = float(candidate.get("screen_price", 0) or 0)
                change = float(candidate.get("screen_change", 0) or 0)
                volume = int(candidate.get("screen_volume", 0) or 0)
                name = str(candidate.get("name", ""))
                trading_value = price * volume
                if (
                    0 < price <= limit and 0 < change < 29.5
                    and volume >= 100_000 and trading_value >= 10_000_000_000
                    and not any(word in name for word in blocked_words)
                ):
                    candidate["asset_type"] = "실시간 상승·유동성 후보"
                    accepted.append(candidate)
        except Exception:
            pass
    for row in source:
        try:
            quote = (scanner().client.kr_quote(row["ticker"]) if market == "국내"
                     else scanner().client.us_quote(row["ticker"], row["exchange"]))
            candidate = dict(row)
            candidate["screen_price"] = float(quote.get("price", 0) or 0)
            candidate["screen_change"] = float(quote.get("change", 0) or 0)
            change = candidate["screen_change"]
            change_ok = 0 < change < 29.5 if market == "국내" else change > 0
            if 0 < candidate["screen_price"] <= limit and change_ok:
                accepted.append(candidate)
        except Exception:
            continue
    deduped = {row["ticker"]: row for row in accepted}
    return sorted(
        deduped.values(),
        key=lambda row: (float(row.get("screen_change", 0) or 0), int(row.get("screen_volume", 0) or 0)),
        reverse=True,
    )[:20]


def latest_entry_candidates(market: str, minimum_score: float, limit: int = 5) -> list[dict]:
    """이미 자동검증기가 수집한 최신 분석을 재사용해 추가 API 호출 없이 후보를 정렬한다."""
    market_code = "KR" if market == "국내" else "US"
    cutoff = int(time.time()) - 12 * 60
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
        trend_score = int(detail.get("continuous_rise_score", 0) or 0)
        continuous_rise = bool(detail.get("continuous_rise"))
        level_plan_valid = bool(detail.get("level_plan_valid"))
        score = float(score or 0)
        if not (data_valid and continuous_rise and level_plan_valid and trend_score >= 7 and risk_reward >= 1.5):
            continue
        if entry_ok and score >= minimum_score and trend_score >= 8 and positive_count >= 3:
            stage, priority = "🟢 지금 진입 검토", 3
        elif score >= minimum_score and positive_count >= 2:
            stage, priority = "🟡 상승 지속·눌림 대기", 2
        else:
            stage, priority = "🔵 상승 구조·확인 대기", 1
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


def precise_analysis(row: dict, mode: str) -> dict:
    raw = scanner().analyze(dict(row), mode)
    item = apply_mode_policy(finalize_trade_item(raw), mode)
    market = "국내" if mode.startswith("국내") else "미국"
    return structural_trade_plan(item, market)


def background_audit_tick(enabled: bool, now_ts: float) -> None:
    """열어 둔 초단타 앱 안에서 토큰을 공유하며 한국장 전 종목을 순환 검증한다."""
    if not enabled or AUDIT_IMPORT_ERROR or not AUDIT_KR_UNIVERSE:
        return
    now_dt = datetime.fromtimestamp(now_ts, KST)
    # 08:50 전과 15:36 이후에는 KIS를 호출하지 않는다.
    hhmm = now_dt.hour * 60 + now_dt.minute
    if now_dt.weekday() >= 5 or hhmm < 8 * 60 + 50 or hhmm > 15 * 60 + 35:
        return
    last = float(st.session_state.get("audit_last_tick", 0.0))
    if now_ts - last < 25:
        return
    dynamic_rows = live_filtered_universe("국내")
    dynamic_members = [
        (str(row.get("ticker")), str(row.get("name") or row.get("ticker")), str(row.get("exchange") or "KR"))
        for row in dynamic_rows if row.get("ticker")
    ]
    audit_members = dynamic_members or AUDIT_KR_UNIVERSE
    index = int(st.session_state.get("audit_member_index", 0)) % len(audit_members)
    ticker, name, exchange_name = audit_members[index]
    row = {"ticker": ticker, "name": name, "exchange": exchange_name, "asset_type": "검증대상"}
    try:
        with audit_connect() as db:
            if audit_signal_window_open("KR", now_dt):
                item = precise_analysis(row, "국내 30분 1% 타점")
                audit_store_result(db, "KR", item, int(now_ts), 300)
            else:
                quote = scanner().client.kr_quote(ticker)
                audit_store_quote(db, "KR", ticker, float(quote.get("price", 0) or 0), int(now_ts))
            audit_grade_pending(db, int(now_ts))
            db.commit()
            audit_export_summary(db)
        st.session_state["audit_last_ok"] = f"{ticker} · {now_dt.strftime('%H:%M:%S')}"
        st.session_state.pop("audit_last_error", None)
    except Exception as error:
        st.session_state["audit_last_error"] = f"{ticker}: {type(error).__name__} · {error}"
    finally:
        st.session_state["audit_last_tick"] = now_ts
        st.session_state["audit_member_index"] = index + 1


st_autorefresh(interval=1000, key="scalp_tick")
st.title("⚡ 초단타 VWAP 매수타점")
st.caption("선택 종목은 약 1초마다 현재가를 확인하고, 20초마다 1분봉·VWAP·EMA·거래량·호가를 정밀 재분석합니다.")

with st.sidebar:
    st.header("초단타 설정")
    market = st.radio("시장", ["국내", "미국"], horizontal=True)
    mode = "국내 30분 1% 타점" if market == "국내" else "미국 30분 1% 타점"
    exchange = "KR" if market == "국내" else "자동 판별"
    minimum_score = st.slider("최소 점수", 30, 90, 50, 5)
    manual_ticker = st.text_input("종목명 또는 종목코드 검색", placeholder="현대차, 005380, SOXL").strip()
    focus_only = st.toggle("선택 종목 1초 집중", True)
    require_validation = st.toggle("실전 검증 잠금", True, help="해당 종목의 실제 5·10분 검증표본이 쌓이기 전에는 초록색 매수 신호를 차단합니다.")
    st.caption("분석 대상: 우량주·ETF·레버리지 ETF")

now = time.time()
auto_audit = st.sidebar.toggle(
    "오늘 한국장 자동검증",
    False,
    help="오전 9시~오후 3시 신규 신호를 기록하고 3시 35분까지 5·10·20·30분 결과를 자동 채점합니다.",
    key="kr_live_audit_enabled",
)
if AUDIT_IMPORT_ERROR:
    st.sidebar.error("자동검증 파일이 없습니다: run_live_validation.py")
elif auto_audit:
    background_audit_tick(True, now)
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
    st.sidebar.success(
        "자동검증 · " + audit_phase
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

candidate_board = latest_entry_candidates(market, minimum_score)
st.subheader("실시간 진입 후보")
if candidate_board:
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
precise_refresh_seconds = 60 if auto_audit else 20
precise_due = now - float(st.session_state.get("scalp_last_precise", 0)) >= precise_refresh_seconds
if precise_due or not latest:
    with st.spinner(f"{selected_ticker} 1분봉 정밀분석 중..."):
        try:
            latest = precise_analysis(selected_row, mode)
            st.session_state["scalp_latest"] = latest
            st.session_state["scalp_last_precise"] = now
            st.session_state.pop("scalp_error", None)
        except Exception as error:
            st.session_state["scalp_error"] = str(error)
elif now - float(st.session_state.get("scalp_last_quote", 0)) >= 1:
    try:
        refreshed = scanner().refresh_quotes([latest], mode)
        if refreshed:
            latest.update(refreshed[0])
            latest = structural_trade_plan(latest, market)
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
calibration = calibration_stats(selected_ticker)
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
context = benchmark_context(market, selected_ticker)
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
        grounded_price = f"{fmt(latest.get('structural_support'))}~{fmt(latest.get('structural_target'))}"
        basis = "확인된 지지·저항 사이"
    column.metric(f"{minutes}분 판정 · {direction}", grounded_price)
    column.caption(str(basis))

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

audit_records = update_prediction_audit(selected_ticker, price, latest, now)
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
