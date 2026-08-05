import html
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="한·미 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 한·미 당일 단타 스캐너")
st.caption("📱 모바일 V8 · 미국 동전주 급등 발견 + 눌림 매수신호")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 760px;}
    h1 {font-size: clamp(1.65rem, 7vw, 2.35rem) !important; line-height: 1.15 !important;}
    h2, h3 {font-size: 1.15rem !important;}
    div[data-testid="stButton"] > button {width: 100%; min-height: 3rem; font-weight: 800;}
    div[data-testid="stSelectbox"] {margin-bottom: .2rem;}
    .stock-card {
        background: #11151d;
        color: #e7ebf3;
        border: 1px solid #293142;
        border-radius: 18px;
        padding: 16px;
        margin: 10px 0;
        box-shadow: 0 8px 24px rgba(0,0,0,.18);
    }
    .card-head {display:flex; justify-content:space-between; gap:12px; align-items:flex-start;}
    .stock-name {font-size:1.3rem; font-weight:900; line-height:1.2;}
    .ticker {color:#8b95a8; font-size:.82rem; margin-top:4px;}
    .price {font-size:1.35rem; font-weight:900; color:#f4f7fb; text-align:right; white-space:nowrap;}
    .change-up {color:#57cf78; font-size:.86rem; text-align:right;}
    .change-down {color:#ff6b6b; font-size:.86rem; text-align:right;}
    .verdict {margin:14px 0 10px; padding:10px 12px; border-radius:11px; font-weight:900; font-size:1.05rem;}
    .v-green {background:#153723; color:#6ee79a; border:1px solid #285f3c;}
    .v-yellow {background:#3c3213; color:#ffd45d; border:1px solid #6b571a;}
    .v-red {background:#431d20; color:#ff858b; border:1px solid #743038;}
    .v-gray {background:#242a34; color:#c6cedb; border:1px solid #3a4250;}
    .warning-box {background:#321b1e; color:#ff9b9f; border:1px solid #6d3037; border-radius:10px; padding:9px 11px; margin:8px 0 12px; font-size:.88rem;}
    .grid2 {display:grid; grid-template-columns:1fr 1fr; gap:0 14px;}
    .data-row {display:flex; justify-content:space-between; gap:8px; padding:9px 0; border-bottom:1px solid #252c37; font-size:.88rem;}
    .data-label {color:#8993a5; white-space:nowrap;}
    .data-value {font-weight:800; text-align:right;}
    .tf-grid {display:grid; grid-template-columns:repeat(4,1fr); gap:7px; margin:12px 0;}
    .tf {background:#1b202a; border-radius:9px; padding:8px 3px; text-align:center; font-size:.75rem;}
    .tf strong {display:block; font-size:.9rem; margin-bottom:2px;}
    .ok {color:#63db88;} .bad {color:#ff777d;} .neutral {color:#ffd15c;}
    .levels-title {font-size:.88rem; color:#aeb7c6; font-weight:800; margin:14px 0 6px;}
    .levels {display:grid; grid-template-columns:repeat(2,1fr); gap:8px;}
    .level {background:#1a202a; padding:10px; border-radius:10px;}
    .level span {display:block; color:#8993a5; font-size:.72rem; margin-bottom:3px;}
    .level b {font-size:1rem;}
    .entry {color:#f4f7fb;} .stop {color:#ff7278;} .target {color:#62dc88;}
    .footnote {color:#7f899b; font-size:.72rem; margin-top:12px; line-height:1.45;}
    @media (max-width: 520px) {
        .block-container {padding-left:.75rem; padding-right:.75rem;}
        .stock-card {padding:14px 12px; border-radius:14px;}
        .grid2 {grid-template-columns:1fr 1fr; gap:0 10px;}
        .data-row {font-size:.78rem;}
        .tf {font-size:.68rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return ""


APP_KEY = load_secret("KIS_APP_KEY")
APP_SECRET = load_secret("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"


def to_int(value):
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return 0


def to_float(value):
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return 0.0


@st.cache_data(ttl=82800, show_spinner=False)
def issue_access_token(app_key, app_secret):
    response = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"접근토큰 발급 실패: HTTP {response.status_code}")
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(data.get("error_description") or data.get("msg1") or "접근토큰을 받지 못했습니다.")
    return token


def make_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


def get_market_cap_ranking(token, market_code):
    response = None
    for attempt in range(3):
        response = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/ranking/market-cap",
            headers=make_headers(token, "FHPST01740000"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20174",
                "FID_DIV_CLS_CODE": "1",
                "FID_INPUT_ISCD": market_code,
                "FID_TRGT_CLS_CODE": "0",
                "FID_TRGT_EXLS_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
            },
            timeout=20,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    if response.status_code != 200:
        raise RuntimeError(f"시가총액 조회 실패: HTTP {response.status_code}")
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "시가총액 순위를 받지 못했습니다."))
    return data.get("output", [])


def get_volume_rank(token, sort_code):
    """sort_code: 1=거래증가율, 3=거래금액순"""
    response = None
    for attempt in range(3):
        response = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=make_headers(token, "FHPST01710000"),
            params={
                # 거래량 순위 서버는 현재 J(KRX)만 정상 처리합니다.
                # 선정된 후보의 현재가는 아래에서 다시 UN(통합)으로 조회합니다.
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_BLNG_CLS_CODE": str(sort_code),
                # 한투 공식 실행 예제에서 사용하는 값을 그대로 적용합니다.
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "1000",
                "FID_INPUT_PRICE_2": "200000",
                "FID_VOL_CNT": "100000",
                "FID_INPUT_DATE_1": "",
            },
            timeout=20,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "응답 없음"
        raise RuntimeError(f"거래량 순위 조회 실패: HTTP {status}")

    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "거래량 순위를 받지 못했습니다."))
    return data.get("output", [])


def get_us_ranking(token, endpoint, tr_id, params):
    response = None
    for attempt in range(3):
        response = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=make_headers(token, tr_id),
            params=params,
            timeout=20,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "응답 없음"
        raise RuntimeError(f"미국주식 순위 조회 실패: HTTP {status}")

    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "미국주식 순위를 받지 못했습니다."))
    return data.get("output2") or []


def get_us_market_cap_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/market-cap",
        "HHDFS76350100",
        {
            "EXCD": exchange,
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_price_fluct_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/price-fluct",
        "HHDFS76260000",
        {
            "EXCD": exchange,
            "GUBN": "1",
            "MINX": "5",
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_volume_surge_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/volume-surge",
        "HHDFS76270000",
        {
            "EXCD": exchange,
            "MINX": "5",
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


US_EXCHANGE_NAMES = {
    "NAS": "NASDAQ",
    "NYS": "NYSE",
    "AMS": "AMEX",
}


def is_excluded_us_product(name, ticker):
    upper_name = str(name).upper()
    upper_ticker = str(ticker).upper()
    excluded_words = (
        "ETF", "ETN", "PROSHARES", "DIREXION", "ULTRAPRO", "ULTRASHORT",
        "2X", "3X", "BEAR", "SHORT", "WARRANT", "RIGHT", "UNIT",
        "ACQUISITION", "SPAC",
    )
    if any(word in upper_name for word in excluded_words):
        return True
    return upper_ticker.endswith((".WS", ".W", ".U"))


def us_stock_name(row):
    return str(
        row.get("name")
        or row.get("enam")
        or row.get("ename")
        or row.get("knam")
        or row.get("symb")
        or ""
    ).strip()


def build_us_quality_table(rows):
    records = []
    for row in rows:
        ticker = str(row.get("symb", "")).strip().upper()
        name = us_stock_name(row)
        if not ticker or not name or is_excluded_us_product(name, ticker):
            continue
        price = to_float(row.get("last"))
        change_pct = to_float(row.get("rate"))
        volume = to_int(row.get("tvol"))
        trading_value = to_float(row.get("tamt"))
        market_cap = to_float(row.get("mcap") or row.get("tomv"))
        if price <= 0 or volume < 10_000:
            continue
        vwap = trading_value / volume if trading_value > 0 and volume > 0 else 0
        vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 0

        if change_pct >= 10 or vwap_gap > 4:
            status = "🔴 추격주의"
        elif change_pct <= -3 or (vwap > 0 and price < vwap):
            status = "⚪ 약세·대기"
        elif 0.5 <= change_pct <= 7 and trading_value >= 20_000_000:
            status = "🟢 미국 기술지표 예정"
        else:
            status = "🟡 유동성 관찰"

        exchange = str(row.get("_exchange") or row.get("excd") or "").strip()
        records.append({
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "시총순위": to_int(row.get("rank")),
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": round(price, 4),
            "등락률(%)": round(change_pct, 2),
            "오늘거래량": volume,
            "오늘거래대금($)": round(trading_value, 2),
            "오늘거래대금(백만$)": round(trading_value / 1_000_000, 2),
            "VWAP근사($)": round(vwap, 4),
            "VWAP위치(%)": round(vwap_gap, 2),
            "시가총액(API)": market_cap,
            "현재판정": status,
        })

    table = pd.DataFrame(records)
    if table.empty:
        return table
    return table.sort_values(
        ["시가총액(API)", "오늘거래대금($)"], ascending=False
    ).head(30).reset_index(drop=True)


def build_us_momentum_table(price_rows, surge_rows):
    records = {}

    def update_row(row, source):
        exchange = str(row.get("_exchange") or row.get("excd") or "").strip()
        ticker = str(row.get("symb", "")).strip().upper()
        name = us_stock_name(row)
        if not ticker or not name or is_excluded_us_product(name, ticker):
            return
        key = f"{exchange}:{ticker}"
        item = records.setdefault(key, {
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": 0.0,
            "등락률(%)": 0.0,
            "15분등락률(%)": 0.0,
            "거래량증가율(%)": 0.0,
            "오늘거래량": 0,
            "오늘거래대금($)": 0.0,
        })
        item["현재가($)"] = max(item["현재가($)"], to_float(row.get("last")))
        item["등락률(%)"] = max(item["등락률(%)"], to_float(row.get("rate")))
        item["오늘거래량"] = max(item["오늘거래량"], to_int(row.get("tvol")))
        item["오늘거래대금($)"] = max(item["오늘거래대금($)"], to_float(row.get("tamt")))
        if source == "price":
            item["15분등락률(%)"] = max(item["15분등락률(%)"], to_float(row.get("n_rate")))
        else:
            item["거래량증가율(%)"] = max(item["거래량증가율(%)"], to_float(row.get("n_rate")))

    for row in price_rows:
        update_row(row, "price")
    for row in surge_rows:
        update_row(row, "surge")

    clean = []
    for item in records.values():
        price = item["현재가($)"]
        daily_rate = item["등락률(%)"]
        volume = item["오늘거래량"]
        amount = item["오늘거래대금($)"]
        if price < 1 or daily_rate < 3 or volume < 10_000 or amount < 100_000:
            continue
        vwap = amount / volume if volume > 0 else 0
        vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 0
        short_rate = item["15분등락률(%)"]
        volume_growth = item["거래량증가율(%)"]
        if daily_rate >= 25 or short_rate >= 15 or vwap_gap > 10:
            status = "🔴 추격금지·과열"
        elif 5 <= daily_rate <= 20 and amount >= 2_000_000 and price >= vwap:
            status = "🟢 미국 급등 정밀검사 예정"
        else:
            status = "🟡 거래량 확대 감시"
        item["VWAP근사($)"] = round(vwap, 4)
        item["VWAP위치(%)"] = round(vwap_gap, 2)
        item["오늘거래대금(백만$)"] = round(amount / 1_000_000, 2)
        item["현재판정"] = status
        item["급등점수"] = round(
            min(daily_rate, 100) + 2 * min(max(short_rate, 0), 30)
            + min(max(volume_growth, 0) / 100, 30)
            + math.log10(max(amount, 1)),
            2,
        )
        clean.append(item)

    table = pd.DataFrame(clean)
    if table.empty:
        return table
    return table.sort_values("급등점수", ascending=False).head(30).reset_index(drop=True)


# 미국 순위 API는 장 구분에 따라 공백 또는 입력값 오류를 반환할 수 있어,
# V7은 한투 공식 '해외주식 복수종목 시세조회'(최대 10종목)로 검증한다.
US_QUALITY_UNIVERSE = [
    ("NAS", "AAPL"), ("NAS", "MSFT"), ("NAS", "NVDA"),
    ("NAS", "AMZN"), ("NAS", "GOOGL"), ("NAS", "META"),
    ("NAS", "AVGO"), ("NAS", "TSLA"), ("NAS", "COST"),
    ("NAS", "NFLX"), ("NAS", "AMD"), ("NAS", "QCOM"),
    ("NAS", "AMAT"), ("NAS", "MU"), ("NAS", "CSCO"),
    ("NAS", "ADBE"), ("NAS", "INTU"), ("NAS", "PEP"),
    ("NAS", "TXN"), ("NAS", "AMGN"),
    ("NYS", "ORCL"), ("NYS", "JPM"), ("NYS", "V"),
    ("NYS", "MA"), ("NYS", "WMT"), ("NYS", "LLY"),
    ("NYS", "XOM"), ("NYS", "UNH"), ("NYS", "HD"),
    ("NYS", "PG"), ("NYS", "JNJ"), ("NYS", "ABBV"),
    ("NYS", "BAC"), ("NYS", "KO"), ("NYS", "CRM"),
    ("NYS", "CVX"), ("NYS", "IBM"), ("NYS", "GE"),
    ("NYS", "CAT"), ("NYS", "DIS"),
]

US_MOMENTUM_SEED = [
    ("NAS", "NVDA"), ("NAS", "TSLA"), ("NAS", "AMD"),
    ("NAS", "PLTR"), ("NAS", "SOFI"), ("NAS", "RIVN"),
    ("NAS", "LCID"), ("NAS", "MARA"), ("NAS", "RIOT"),
    ("NAS", "MSTR"), ("NAS", "COIN"), ("NAS", "HOOD"),
    ("NAS", "SMCI"), ("NAS", "ARM"), ("NAS", "INTC"),
    ("NAS", "RKLB"), ("NAS", "ASTS"), ("NAS", "IONQ"),
    ("NAS", "RGTI"), ("NAS", "QBTS"), ("NAS", "APP"),
    ("NAS", "ALAB"), ("NAS", "CELH"), ("NAS", "UPST"),
    ("NAS", "AFRM"), ("NAS", "DKNG"), ("NAS", "CLSK"),
    ("NAS", "BITF"), ("NAS", "HUT"), ("NAS", "WULF"),
    ("NAS", "OPEN"), ("NAS", "GRAB"),
    ("NYS", "BBAI"), ("NYS", "PATH"), ("NYS", "U"),
    ("NYS", "JOBY"), ("NYS", "ACHR"), ("NYS", "SNAP"),
    ("NYS", "NIO"), ("NYS", "BABA"), ("NYS", "CAVA"),
    ("NYS", "CVNA"), ("NYS", "GME"), ("NYS", "AMC"),
    ("NYS", "MP"), ("NYS", "OKLO"), ("NYS", "NU"),
    ("NYS", "SE"), ("NYS", "NET"), ("NYS", "HIMS"),
]

US_DAY_EXCHANGE = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
US_NORMAL_EXCHANGE = {value: key for key, value in US_DAY_EXCHANGE.items()}


def unique_us_pairs(pairs):
    seen = set()
    result = []
    for exchange, ticker in pairs:
        exchange = US_NORMAL_EXCHANGE.get(str(exchange).upper(), str(exchange).upper())
        ticker = str(ticker).strip().upper()
        key = (exchange, ticker)
        if exchange not in US_EXCHANGE_NAMES or not re.fullmatch(r"[A-Z]{1,6}", ticker):
            continue
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def resolve_us_session(session_choice):
    """한투 미국 주간거래는 별도 거래소 코드를 사용한다."""
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    summer = bool(now_ny.dst())
    minutes = now_kst.hour * 60 + now_kst.minute
    day_end = 17 * 60 if summer else 18 * 60

    if session_choice.startswith("주간"):
        mode = "day"
    elif session_choice.startswith("프리"):
        mode = "normal"
    else:
        mode = "day" if 10 * 60 <= minutes < day_end else "normal"

    if mode == "day":
        detail = f"주간거래 10:00~{('17:00' if summer else '18:00')}"
    else:
        pre_start = 17 * 60 if summer else 18 * 60
        regular_start = 22 * 60 + 30 if summer else 23 * 60 + 30
        regular_end = 5 * 60 if summer else 6 * 60
        if pre_start <= minutes < regular_start:
            detail = "프리마켓"
        elif minutes >= regular_start or minutes < regular_end:
            detail = "정규장"
        elif regular_end <= minutes < 9 * 60:
            detail = "애프터마켓"
        else:
            detail = "휴장·전환 구간(직전 정규시세)"
    return mode, detail, now_kst.strftime("%Y-%m-%d %H:%M:%S")


def session_exchange(exchange, session_mode):
    normal = US_NORMAL_EXCHANGE.get(exchange, exchange)
    if session_mode == "day":
        return US_DAY_EXCHANGE.get(normal, normal)
    return normal


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def get_us_multiple_prices(token, pairs, session_mode):
    """한투 공식 API로 한 호출에 최대 10종목을 조회한다."""
    pairs = unique_us_pairs(pairs)
    rows = []
    errors = []

    for batch in chunks(pairs, 10):
        params = {"AUTH": "", "NREC": str(len(batch))}
        for number, (exchange, ticker) in enumerate(batch, start=1):
            params[f"EXCD_{number:02d}"] = session_exchange(exchange, session_mode)
            params[f"SYMB_{number:02d}"] = ticker

        response = None
        for attempt in range(3):
            try:
                response = requests.get(
                    f"{BASE_URL}/uapi/overseas-price/v1/quotations/multprice",
                    headers=make_headers(token, "HHDFS76220000"),
                    params=params,
                    timeout=12,
                )
                if response.status_code == 200:
                    break
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(0.45 * (attempt + 1))
                    continue
                break
            except requests.RequestException as error:
                if attempt == 2:
                    errors.append(str(error))
                else:
                    time.sleep(0.45 * (attempt + 1))

        if response is None:
            continue
        if response.status_code != 200:
            errors.append(f"복수종목 시세 HTTP {response.status_code}")
            continue
        data = response.json()
        if data.get("rt_cd") != "0":
            errors.append(data.get("msg1") or "복수종목 시세 조회 실패")
            continue

        requested = {ticker: exchange for exchange, ticker in batch}
        output_rows = data.get("output2") or data.get("output") or []
        if isinstance(output_rows, dict):
            output_rows = output_rows.get("output2") or []
        for row in output_rows:
            row = dict(row)
            ticker = str(row.get("symb", "")).strip().upper()
            row["_base_exchange"] = requested.get(ticker, "")
            row["_session_mode"] = session_mode
            rows.append(row)

        # 초당 호출 한도에 여유를 두되, 기존 1종목씩 호출보다 훨씬 빠르다.
        time.sleep(0.08)

    return rows, errors


YAHOO_EXCHANGE_MAP = {
    "NMS": "NAS", "NGM": "NAS", "NCM": "NAS", "NAS": "NAS",
    "NYQ": "NYS", "NYS": "NYS", "ASE": "AMS", "AMS": "AMS",
}


def fetch_yahoo_screener(screen_id, max_price=0):
    url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    response = requests.get(
        url,
        params={"scrIds": screen_id, "count": "100", "start": "0"},
        headers={"user-agent": "Mozilla/5.0"},
        timeout=4,
    )
    response.raise_for_status()
    data = response.json()
    result = ((data.get("finance") or {}).get("result") or [{}])[0]
    pairs = []
    for quote in result.get("quotes") or []:
        ticker = str(quote.get("symbol", "")).upper()
        exchange = YAHOO_EXCHANGE_MAP.get(str(quote.get("exchange", "")).upper())
        quote_price = to_float(quote.get("regularMarketPrice"))
        if max_price and not 0.05 <= quote_price <= max_price:
            continue
        if exchange and re.fullmatch(r"[A-Z]{1,6}", ticker):
            pairs.append((exchange, ticker))
    return pairs


@st.cache_data(ttl=45, show_spinner=False)
def get_yahoo_us_candidates(max_price=0):
    """급등률·거래량 상위를 동시에 받고, 최종 시세는 한투로 재검증한다."""
    result_by_screen = {}
    errors = []
    screen_ids = ("day_gainers", "most_actives")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_yahoo_screener, item, max_price): item
            for item in screen_ids
        }
        for future in as_completed(futures):
            try:
                result_by_screen[futures[future]] = future.result()
            except Exception as error:
                errors.append(f"{futures[future]}: {error}")
    pairs = (
        result_by_screen.get("day_gainers", [])
        + result_by_screen.get("most_actives", [])
    )
    result = unique_us_pairs(pairs)
    if not result and errors:
        raise RuntimeError(" / ".join(errors))
    return result


def build_us_fast_table(rows, candidates, strategy):
    order = {ticker: index + 1 for index, (_, ticker) in enumerate(candidates)}
    records = []
    for row in rows:
        ticker = str(row.get("symb", "")).strip().upper()
        exchange = str(row.get("_base_exchange") or row.get("excd") or "").strip()
        exchange = US_NORMAL_EXCHANGE.get(exchange, exchange)
        price = to_float(row.get("last"))
        base = to_float(row.get("base"))
        rate = to_float(row.get("rate"))
        if rate == 0 and price > 0 and base > 0:
            rate = (price / base - 1) * 100
        volume = to_int(row.get("tvol"))
        previous_volume = to_int(row.get("pvol"))
        amount = to_float(row.get("tamt"))
        vwap = amount / volume if amount > 0 and volume > 0 else 0
        vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
        volume_ratio = volume / previous_volume * 100 if previous_volume > 0 else 0
        market_cap = to_float(row.get("tomv"))
        strength = to_float(row.get("powx"))
        bid = to_float(row.get("pbid"))
        ask = to_float(row.get("pask"))
        spread_pct = (ask - bid) / price * 100 if ask > 0 and bid > 0 and price > 0 else 0
        name = str(row.get("knam") or row.get("name") or ticker).strip()
        if price <= 0:
            continue

        score = (
            max(rate, -20) * 4
            + min(math.log10(max(amount, 1)), 12) * 3
            + min(volume_ratio, 1000) / 25
            + max(vwap_gap, -10)
        )
        if strategy == "penny":
            passed = (
                0.05 <= price <= 10
                and 3 <= rate <= 300
                and volume >= 100_000
                and amount >= 50_000
            )
            if rate >= 80 or vwap_gap >= 15:
                status = "🔴 폭등·추격금지"
            elif passed and 0 <= vwap_gap <= 5:
                status = "🟢 눌림 정밀검사"
            elif passed:
                status = "🟡 VWAP 복귀 대기"
            else:
                status = "⚪ 조건미달"
            # 동전주는 절대 거래대금보다 상승률·거래량 확대에 가중치를 둔다.
            score = (
                min(rate, 300) * 1.5
                + min(volume_ratio, 2000) / 15
                + min(math.log10(max(volume, 1)), 9) * 5
                + min(math.log10(max(amount, 1)), 12) * 2
                - max(vwap_gap - 6, 0) * 4
            )
        elif strategy == "momentum":
            passed = rate >= 2 and volume >= 10_000 and amount >= 100_000
            if rate >= 25 or vwap_gap >= 10:
                status = "🔴 추격주의"
            elif passed and price >= vwap:
                status = "🟢 급등 정밀검사"
            elif rate >= 1 or volume_ratio >= 120:
                status = "🟡 거래량 확대 감시"
            else:
                status = "⚪ 조건대기"
        else:
            passed = 0.2 <= rate <= 8 and amount >= 1_000_000
            if rate >= 10 or vwap_gap >= 6:
                status = "🔴 추격주의"
            elif passed and price >= vwap:
                status = "🟢 기술지표 후보"
            elif price < vwap and rate < 0:
                status = "⚪ 약세·대기"
            else:
                status = "🟡 유동성 관찰"

        records.append({
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": round(price, 4),
            "등락률(%)": round(rate, 2),
            "세션": "주간거래" if row.get("_session_mode") == "day" else "프리·정규·애프터",
            "시세시간(KST)": str(row.get("khms") or ""),
            "오늘거래량": volume,
            "전일대비거래량(%)": round(volume_ratio, 1),
            "오늘거래대금(백만$)": round(amount / 1_000_000, 2),
            "VWAP($)": round(vwap, 4),
            "VWAP위치(%)": round(vwap_gap, 2),
            "체결강도": round(strength, 1),
            "매수호가($)": round(bid, 4),
            "매도호가($)": round(ask, 4),
            "스프레드(%)": round(spread_pct, 2),
            "시가총액(API)": market_cap,
            "후보순위": order.get(ticker, 999),
            "고속점수": round(score, 2),
            "조건통과": passed,
            "현재판정": status,
        })

    table = pd.DataFrame(records)
    if table.empty:
        return table
    if strategy in ("momentum", "penny"):
        passed = table[table["조건통과"]].copy()
        if not passed.empty:
            table = passed
        return table.sort_values("고속점수", ascending=False).head(30).reset_index(drop=True)
    return table.sort_values(
        ["시가총액(API)", "오늘거래대금(백만$)"], ascending=False
    ).head(30).reset_index(drop=True)


def is_excluded_product(name):
    excluded_words = (
        "KODEX", "TIGER", "RISE", "ACE", "SOL ", "HANARO", "KOSEF",
        "ARIRANG", "PLUS ", "TIMEFOLIO", "KBSTAR", "ETF", "ETN",
        "인버스", "레버리지", "선물", "스팩",
    )
    upper_name = name.upper()
    return any(word.upper() in upper_name for word in excluded_words)


def build_universe(kospi_rows, kosdaq_rows):
    records = []
    for market_name, rows in (("KOSPI", kospi_rows), ("KOSDAQ", kosdaq_rows)):
        for row in rows:
            ticker = str(row.get("mksc_shrn_iscd", "")).strip()
            name = str(row.get("hts_kor_isnm", "")).strip()
            if not ticker or not name or is_excluded_product(name):
                continue
            price = to_int(row.get("stck_prpr"))
            volume = to_int(row.get("acml_vol"))
            shares = to_int(row.get("lstn_stcn"))
            if price <= 0 or shares <= 0:
                continue
            records.append({
                "시장": market_name,
                "시총순위": to_int(row.get("data_rank")),
                "종목코드": ticker,
                "종목명": name,
                "KRX기준가": price,
                "KRX등락률(%)": round(to_float(row.get("prdy_ctrt")), 2),
                "KRX누적거래량": volume,
                "1차거래대금근사": price * volume,
                "시가총액(조원)": round((price * shares) / 1_000_000_000_000, 2),
            })
    return pd.DataFrame(records)


def build_momentum_universe(rank_rows):
    records = {}
    for row in rank_rows:
        ticker = str(row.get("mksc_shrn_iscd", "")).strip()
        name = str(row.get("hts_kor_isnm", "")).strip()
        if not ticker or not name or is_excluded_product(name):
            continue

        price = to_int(row.get("stck_prpr"))
        volume = to_int(row.get("acml_vol"))
        shares = to_int(row.get("lstn_stcn"))
        trading_value = to_int(row.get("acml_tr_pbmn"))
        if price < 1000 or price > 200000 or volume < 100000 or shares <= 0:
            continue

        item = {
            "시장": "국내",
            "시총순위": 0,
            "급등순위": to_int(row.get("data_rank")),
            "종목코드": ticker,
            "종목명": name,
            "KRX기준가": price,
            "KRX등락률(%)": round(to_float(row.get("prdy_ctrt")), 2),
            "KRX누적거래량": volume,
            "1차거래대금근사": trading_value or price * volume,
            "시가총액(조원)": round((price * shares) / 1_000_000_000_000, 2),
            "거래량증가율(%)": round(to_float(row.get("vol_inrt")), 1),
            "거래량회전율(%)": round(to_float(row.get("vol_tnrt")), 2),
            "평균거래량": to_int(row.get("avrg_vol")),
        }

        previous = records.get(ticker)
        if previous is None:
            records[ticker] = item
        else:
            previous["거래량증가율(%)"] = max(
                previous["거래량증가율(%)"], item["거래량증가율(%)"]
            )
            previous["거래량회전율(%)"] = max(
                previous["거래량회전율(%)"], item["거래량회전율(%)"]
            )
            previous["1차거래대금근사"] = max(
                previous["1차거래대금근사"], item["1차거래대금근사"]
            )

    return pd.DataFrame(records.values())


def get_integrated_price(token, ticker):
    response = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price-2",
        headers=make_headers(token, "FHPST01010000"),
        params={
            "FID_COND_MRKT_DIV_CODE": "UN",
            "FID_INPUT_ISCD": ticker,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 통합현재가 조회 실패: HTTP {response.status_code}")
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"{ticker}: {data.get('msg1', '통합현재가를 받지 못했습니다.')}")

    row = data.get("output") or {}
    price = to_int(row.get("stck_prpr"))
    volume = to_int(row.get("acml_vol"))
    trading_value = to_int(row.get("acml_tr_pbmn"))
    if price <= 0:
        return None

    return {
        "종목코드": ticker,
        "현재가": price,
        "등락률(%)": round(to_float(row.get("prdy_ctrt")), 2),
        "VWAP": round(trading_value / volume, 2) if volume > 0 else 0,
        "시가": to_int(row.get("stck_oprc")),
        "고가": to_int(row.get("stck_hgpr")),
        "저가": to_int(row.get("stck_lwpr")),
        "오늘누적거래량": volume,
        "오늘누적거래대금": trading_value,
        "전일대비거래량(%)": round(to_float(row.get("prdy_vrss_vol_rate")), 1),
        "시세기준": "통합(UN)",
    }


def collect_integrated_prices(token, tickers):
    prices = {}
    errors = []
    for ticker in tickers:
        try:
            item = get_integrated_price(token, ticker)
            if item:
                prices[ticker] = item
        except Exception as error:
            errors.append(str(error))
        time.sleep(0.12)
    return prices, errors


def subtract_one_minute(hhmmss):
    try:
        value = datetime.strptime(hhmmss, "%H%M%S") - timedelta(minutes=1)
        return value.strftime("%H%M%S")
    except Exception:
        return "000000"


def get_minute_page(token, ticker, end_time, market_code="UN"):
    response = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "FHKST03010200"),
        params={
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": end_time,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_ETC_CLS_CODE": "",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 분봉 조회 실패: HTTP {response.status_code}")
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", f"{ticker} 분봉을 받지 못했습니다."))
    return data.get("output2") or []


def get_market_cursor(market_code):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    current = now.strftime("%H%M%S")
    if market_code == "J":
        if current < "090000":
            return "090000"
        return min(current, "153000")
    if current < "080000":
        return "080000"
    return min(current, "200000")


def get_recent_minutes_for_market(token, ticker, market_code, pages=4):
    all_rows = []
    cursor = get_market_cursor(market_code)

    for _ in range(pages):
        rows = get_minute_page(token, ticker, cursor, market_code=market_code)
        if not rows:
            break
        all_rows.extend(rows)

        valid_times = [str(row.get("stck_cntg_hour", "")) for row in rows]
        valid_times = [value for value in valid_times if len(value) == 6 and value.isdigit()]
        if not valid_times:
            break
        next_cursor = subtract_one_minute(min(valid_times))
        if next_cursor == cursor or next_cursor == "000000":
            break
        cursor = next_cursor
        time.sleep(0.18)

    records = []
    for row in all_rows:
        date_text = str(row.get("stck_bsop_date", "")).strip()
        time_text = str(row.get("stck_cntg_hour", "")).strip()
        if len(date_text) != 8 or len(time_text) != 6:
            continue
        try:
            timestamp = pd.to_datetime(date_text + time_text, format="%Y%m%d%H%M%S")
        except Exception:
            continue
        records.append({
            "시간": timestamp,
            "시가": to_float(row.get("stck_oprc")),
            "고가": to_float(row.get("stck_hgpr")),
            "저가": to_float(row.get("stck_lwpr")),
            "종가": to_float(row.get("stck_prpr")),
            "거래량": to_float(row.get("cntg_vol")),
        })

    minute = pd.DataFrame(records)
    if minute.empty:
        return minute
    minute = minute.drop_duplicates("시간").sort_values("시간").set_index("시간")
    minute = minute[(minute[["시가", "고가", "저가", "종가"]] > 0).all(axis=1)]
    return minute.tail(120)


def get_recent_minutes(token, ticker, pages=4):
    # 먼저 통합시장 분봉을 사용합니다.
    try:
        integrated = get_recent_minutes_for_market(token, ticker, "UN", pages=pages)
    except Exception:
        integrated = pd.DataFrame()
    if len(integrated) >= 35:
        return integrated

    # 통합 분봉이 비었거나 부족하면 오늘 KRX 분봉으로 자동 재시도합니다.
    krx = get_recent_minutes_for_market(token, ticker, "J", pages=pages)
    if len(krx) > len(integrated):
        return krx
    return integrated


@st.cache_data(ttl=8, show_spinner=False)
def get_us_recent_minutes(token, exchange, ticker, session_mode):
    """한투 해외주식 분봉을 1회 호출로 최대 120개 받는다."""
    response = requests.get(
        f"{BASE_URL}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "HHDFS76950200"),
        params={
            "AUTH": "",
            "EXCD": session_exchange(exchange, session_mode),
            "SYMB": ticker,
            "NMIN": "1",
            "PINC": "0",
            "NEXT": "",
            "NREC": "120",
            "FILL": "",
            "KEYB": "",
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 미국 분봉 조회 실패: HTTP {response.status_code}")
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1") or f"{ticker} 미국 분봉을 받지 못했습니다.")

    records = []
    for row in data.get("output2") or []:
        date_text = str(row.get("tymd", "")).strip()
        time_text = str(row.get("xhms", "")).strip().zfill(6)
        if len(date_text) != 8 or len(time_text) != 6:
            continue
        try:
            timestamp = pd.to_datetime(date_text + time_text, format="%Y%m%d%H%M%S")
        except Exception:
            continue
        records.append({
            "시간": timestamp,
            "시가": to_float(row.get("open")),
            "고가": to_float(row.get("high")),
            "저가": to_float(row.get("low")),
            "종가": to_float(row.get("last")),
            "거래량": to_float(row.get("evol")),
        })
    minute = pd.DataFrame(records)
    if minute.empty:
        return minute
    minute = minute.drop_duplicates("시간").sort_values("시간").set_index("시간")
    minute = minute[(minute[["시가", "고가", "저가", "종가"]] > 0).all(axis=1)]
    return minute.tail(120)


def resample_bars(minute, minutes):
    if minute.empty:
        return minute
    bars = minute.resample(f"{minutes}min").agg({
        "시가": "first",
        "고가": "max",
        "저가": "min",
        "종가": "last",
        "거래량": "sum",
    })
    return bars.dropna(subset=["시가", "고가", "저가", "종가"])


def add_indicators(bars):
    result = bars.copy()
    close = result["종가"]
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result["RSI"] = 100 - (100 / (1 + rs))
    result.loc[(avg_loss == 0) & (avg_gain > 0), "RSI"] = 100

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    result["MACD"] = ema12 - ema26
    result["MACD시그널"] = result["MACD"].ewm(span=9, adjust=False).mean()
    result["MACD히스토그램"] = result["MACD"] - result["MACD시그널"]
    result["EMA9"] = close.ewm(span=9, adjust=False).mean()

    prev_close = close.shift(1)
    true_range = pd.concat([
        result["고가"] - result["저가"],
        (result["고가"] - prev_close).abs(),
        (result["저가"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    result["ATR"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    return result


def safe_last(series, default=0.0):
    try:
        value = series.dropna().iloc[-1]
        return float(value)
    except Exception:
        return default


def analyze_selected_stock(minute, quote_row):
    frames = {}
    for minutes in (1, 3, 5, 15):
        frames[minutes] = add_indicators(resample_bars(minute, minutes))

    one = frames[1]
    three = frames[3]
    five = frames[5]
    fifteen = frames[15]
    price = float(quote_row["현재가"])
    day_vwap = float(quote_row["VWAP"])
    change_pct = float(quote_row["등락률(%)"])

    rsi_1 = safe_last(one["RSI"])
    rsi_3 = safe_last(three["RSI"])
    rsi_5 = safe_last(five["RSI"])
    macd_3 = safe_last(three["MACD"])
    signal_3 = safe_last(three["MACD시그널"])
    hist_3 = safe_last(three["MACD히스토그램"])
    previous_hist_3 = float(three["MACD히스토그램"].dropna().iloc[-2]) if three["MACD히스토그램"].dropna().shape[0] >= 2 else 0

    trend_1 = safe_last(one["종가"]) >= safe_last(one["EMA9"])
    trend_3 = safe_last(three["종가"]) >= safe_last(three["EMA9"])
    trend_5 = safe_last(five["종가"]) >= safe_last(five["EMA9"])
    trend_15 = len(fifteen) >= 3 and safe_last(fifteen["종가"]) >= safe_last(fifteen["EMA9"])

    recent_volume = one["거래량"].tail(5).mean() if len(one) >= 5 else 0
    prior_volume = one["거래량"].iloc[-25:-5].mean() if len(one) >= 25 else 0
    volume_speed = float(recent_volume / prior_volume) if prior_volume and prior_volume > 0 else 0
    vwap_gap = ((price / day_vwap) - 1) * 100 if day_vwap > 0 else 0

    bullish_macd = macd_3 > signal_3 and hist_3 >= previous_hist_3
    rsi_ok = 45 <= rsi_3 <= 70 and 42 <= rsi_5 <= 72
    price_ok = 0 <= vwap_gap <= 2.0
    trend_ok = trend_1 and trend_3 and trend_5
    volume_ok = volume_speed >= 1.0
    overheated = rsi_1 >= 78 or rsi_3 >= 75 or vwap_gap > 3.0 or change_pct >= 12

    score = sum([
        price_ok,
        rsi_ok,
        bullish_macd,
        trend_ok,
        trend_15,
        volume_ok,
    ])

    if overheated:
        verdict = "🔴 추격 금지"
    elif score >= 5 and price_ok and bullish_macd and trend_ok:
        verdict = "🟢 진입 조건 충족"
    elif score >= 3:
        verdict = "🟡 눌림 대기"
    else:
        verdict = "⚪ 진입 금지"

    atr_5 = safe_last(five["ATR"])
    recent_low = float(five["저가"].tail(3).min()) if not five.empty else price
    entry = min(price, max(day_vwap, safe_last(one["EMA9"], price)))
    if atr_5 > 0:
        stop = min(recent_low, entry - 0.8 * atr_5)
        target1 = entry + 1.2 * atr_5
        target2 = entry + 2.0 * atr_5
    else:
        stop = entry * 0.985
        target1 = entry * 1.02
        target2 = entry * 1.035

    timeframe_summary = {}
    for minutes, frame in frames.items():
        rsi_value = safe_last(frame["RSI"], float("nan"))
        macd_value = safe_last(frame["MACD"])
        signal_value = safe_last(frame["MACD시그널"])
        timeframe_summary[minutes] = {
            "trend": safe_last(frame["종가"]) >= safe_last(frame["EMA9"]),
            "rsi": rsi_value,
            "macd_up": macd_value > signal_value,
            "bars": len(frame),
        }

    return {
        "frames": frames,
        "verdict": verdict,
        "score": score,
        "rsi_1": rsi_1,
        "rsi_3": rsi_3,
        "rsi_5": rsi_5,
        "macd_3": macd_3,
        "signal_3": signal_3,
        "volume_speed": volume_speed,
        "vwap_gap": vwap_gap,
        "trend_1": trend_1,
        "trend_3": trend_3,
        "trend_5": trend_5,
        "trend_15": trend_15,
        "bullish_macd": bullish_macd,
        "entry": round(entry),
        "stop": round(stop),
        "target1": round(target1),
        "target2": round(target2),
        "timeframe_summary": timeframe_summary,
    }


def analyze_us_penny_stock(minute, quote_row):
    frames = {minutes: add_indicators(resample_bars(minute, minutes)) for minutes in (1, 3, 5, 15)}
    one, three, five = frames[1], frames[3], frames[5]
    price = float(quote_row["현재가($)"])
    vwap = float(quote_row["VWAP($)"])
    change_pct = float(quote_row["등락률(%)"])
    spread_pct = float(quote_row.get("스프레드(%)", 0) or 0)

    rsi_1 = safe_last(one["RSI"])
    rsi_3 = safe_last(three["RSI"])
    rsi_5 = safe_last(five["RSI"])
    macd_3 = safe_last(three["MACD"])
    signal_3 = safe_last(three["MACD시그널"])
    hist = three["MACD히스토그램"].dropna()
    hist_now = float(hist.iloc[-1]) if len(hist) else 0
    hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else hist_now
    bullish_macd = macd_3 > signal_3 and hist_now >= hist_prev

    trend_1 = safe_last(one["종가"]) >= safe_last(one["EMA9"])
    trend_3 = safe_last(three["종가"]) >= safe_last(three["EMA9"])
    trend_5 = safe_last(five["종가"]) >= safe_last(five["EMA9"])
    recent_volume = one["거래량"].tail(3).mean() if len(one) >= 3 else 0
    prior_volume = one["거래량"].iloc[-23:-3].mean() if len(one) >= 23 else 0
    volume_speed = float(recent_volume / prior_volume) if prior_volume > 0 else 0
    vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
    recent_high = float(one["고가"].tail(30).max()) if not one.empty else price
    recent_low = float(one["저가"].tail(8).min()) if not one.empty else price
    pullback_pct = (price / recent_high - 1) * 100 if recent_high > 0 else 0
    reclaim = trend_1 and len(one) >= 2 and one["종가"].iloc[-1] >= one["종가"].iloc[-2]

    checks = {
        "VWAP 위 0~5%": 0 <= vwap_gap <= 5,
        "RSI 비과열": 45 <= rsi_3 <= 70 and rsi_1 < 78,
        "3분 MACD 상승": bullish_macd,
        "1·3분 EMA9 위": trend_1 and trend_3,
        "최근 거래량 1.2배": volume_speed >= 1.2,
        "스프레드 3% 이하": spread_pct == 0 or spread_pct <= 3,
        "눌림 후 재상승": -10 <= pullback_pct <= 0 and reclaim,
    }
    score = sum(checks.values())
    overheated = change_pct >= 80 or vwap_gap > 10 or rsi_1 >= 85 or spread_pct > 6
    mandatory = checks["VWAP 위 0~5%"] and checks["3분 MACD 상승"] and checks["1·3분 EMA9 위"]

    if overheated:
        verdict = "🔴 추격금지"
    elif score >= 5 and mandatory:
        verdict = "🟢 매수검토"
    elif score >= 3:
        verdict = "🟡 눌림대기"
    else:
        verdict = "⚪ 진입금지"

    atr_1 = safe_last(one["ATR"])
    risk = max(atr_1 * 1.2, price * 0.02)
    entry = max(price, safe_last(one["EMA9"], price))
    stop = max(recent_low, entry - risk, entry * 0.97)
    return {
        "frames": frames,
        "verdict": verdict,
        "score": score,
        "checks": checks,
        "rsi_1": rsi_1,
        "rsi_3": rsi_3,
        "rsi_5": rsi_5,
        "bullish_macd": bullish_macd,
        "volume_speed": volume_speed,
        "vwap_gap": vwap_gap,
        "pullback_pct": pullback_pct,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target1": round(entry * 1.05, 4),
        "target2": round(entry * 1.08, 4),
    }


def analyze_us_penny_candidates(token, table, session_mode, limit=8):
    """스캐 상위권만 분봉을 동시 조회해 매수타점을 계산한다."""
    targets = [row.to_dict() for _, row in table.head(limit).iterrows()]

    def inspect(row):
        minute = get_us_recent_minutes(
            token,
            str(row["거래소코드"]),
            str(row["종목코드"]),
            session_mode,
        )
        if len(minute) < 35:
            return {
                "row": row,
                "analysis": None,
                "error": f"분봉 {len(minute)}개(최소 35개 필요)",
            }
        return {"row": row, "analysis": analyze_us_penny_stock(minute, row), "error": ""}

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(inspect, row) for row in targets]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:
                results.append({"row": {}, "analysis": None, "error": str(error)})

    verdict_order = {
        "🟢 매수검토": 0,
        "🟡 눌림대기": 1,
        "⚪ 진입금지": 2,
        "🔴 추격금지": 3,
    }
    results.sort(key=lambda item: (
        verdict_order.get((item.get("analysis") or {}).get("verdict"), 9),
        -float((item.get("analysis") or {}).get("score", 0)),
    ))
    return results


def render_compact_card(saved):
    analysis = saved["analysis"]
    quote = saved["row"]
    stock_name = html.escape(str(quote["종목명"]))
    ticker = html.escape(str(quote["종목코드"]))
    market = html.escape(str(quote["시장"]))
    scan_name = "급등주" if str(saved.get("scan_type", "")).endswith("momentum") else "우량주"
    change_pct = float(quote["등락률(%)"])
    change_class = "change-up" if change_pct >= 0 else "change-down"

    verdict = analysis["verdict"]
    if verdict.startswith("🟢"):
        verdict_class = "v-green"
    elif verdict.startswith("🟡"):
        verdict_class = "v-yellow"
    elif verdict.startswith("🔴"):
        verdict_class = "v-red"
    else:
        verdict_class = "v-gray"

    warnings = []
    if analysis["vwap_gap"] < 0:
        warnings.append("현재가가 VWAP 아래")
    elif analysis["vwap_gap"] > 3:
        warnings.append("VWAP에서 너무 멀어 추격 위험")
    if analysis["rsi_1"] >= 78 or analysis["rsi_3"] >= 75:
        warnings.append("RSI 과열")
    if not analysis["bullish_macd"]:
        warnings.append("3분 MACD 상승 확인 안 됨")
    if analysis["volume_speed"] < 1:
        warnings.append("최근 거래량 속도 둔화")
    if not analysis["trend_15"]:
        warnings.append("15분 상승 추세 미확인")
    if float(quote.get("거래량회전율(%)", 0)) >= 20:
        warnings.append("거래량 회전율 과열")
    warning_text = " · ".join(warnings) if warnings else "핵심 경고 없음 — 호가와 체결 상태를 마지막으로 확인하세요."

    timeframe_cards = []
    for minutes in (1, 3, 5, 15):
        item = analysis["timeframe_summary"][minutes]
        if item["bars"] < 3:
            css_class = "neutral"
            state = "부족"
        elif item["trend"] and item["macd_up"]:
            css_class = "ok"
            state = "상승"
        elif not item["trend"] and not item["macd_up"]:
            css_class = "bad"
            state = "약세"
        else:
            css_class = "neutral"
            state = "혼조"
        rsi_text = "-" if pd.isna(item["rsi"]) else f"{item['rsi']:.0f}"
        timeframe_cards.append(
            f'<div class="tf"><strong>{minutes}분</strong><span class="{css_class}">{state}</span><br>RSI {rsi_text}</div>'
        )

    macd_text = "상승" if analysis["bullish_macd"] else "약화"
    macd_class = "ok" if analysis["bullish_macd"] else "bad"
    updated_at = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M")
    extra_left = ""
    extra_right = ""
    if str(saved.get("scan_type", "")).endswith("momentum"):
        extra_left = (
            '<div class="data-row"><span class="data-label">거래량 증가율</span>'
            f'<span class="data-value">{float(quote.get("거래량증가율(%)", 0)):,.1f}%</span></div>'
        )
        extra_right = (
            '<div class="data-row"><span class="data-label">거래량 회전율</span>'
            f'<span class="data-value">{float(quote.get("거래량회전율(%)", 0)):,.2f}%</span></div>'
        )

    card_html = f"""
    <div class="stock-card">
      <div class="card-head">
        <div>
          <div class="stock-name">{stock_name}</div>
          <div class="ticker">{scan_name} · {market} · {ticker} · 통합(UN)</div>
        </div>
        <div>
          <div class="price">{int(quote['현재가']):,}원</div>
          <div class="{change_class}">{change_pct:+.2f}%</div>
        </div>
      </div>
      <div class="verdict {verdict_class}">{html.escape(verdict)} · 점수 {analysis['score']}/6</div>
      <div class="warning-box">⚠️ {html.escape(warning_text)}</div>
      <div class="tf-grid">{''.join(timeframe_cards)}</div>
      <div class="grid2">
        <div>
          <div class="data-row"><span class="data-label">당일 VWAP</span><span class="data-value">{int(quote['VWAP']):,}원</span></div>
          <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{analysis['vwap_gap']:+.2f}%</span></div>
          <div class="data-row"><span class="data-label">RSI 1/3/5분</span><span class="data-value">{analysis['rsi_1']:.0f}/{analysis['rsi_3']:.0f}/{analysis['rsi_5']:.0f}</span></div>
          <div class="data-row"><span class="data-label">오늘 거래량</span><span class="data-value">{int(quote['오늘누적거래량']):,}주</span></div>
          <div class="data-row"><span class="data-label">전일 대비 거래량</span><span class="data-value">{float(quote['전일대비거래량(%)']):,.1f}%</span></div>
          {extra_left}
        </div>
        <div>
          <div class="data-row"><span class="data-label">3분 MACD</span><span class="data-value {macd_class}">{macd_text}</span></div>
          <div class="data-row"><span class="data-label">거래량 속도</span><span class="data-value">{analysis['volume_speed']:.2f}배</span></div>
          <div class="data-row"><span class="data-label">거래대금</span><span class="data-value">{float(quote['오늘거래대금(억원)']):,.0f}억원</span></div>
          <div class="data-row"><span class="data-label">시가총액</span><span class="data-value">{float(quote['시가총액(조원)']):,.2f}조원</span></div>
          <div class="data-row"><span class="data-label">당일 고가/저가</span><span class="data-value">{int(quote['고가']):,}/{int(quote['저가']):,}</span></div>
          {extra_right}
        </div>
      </div>
      <div class="levels-title">매매 레벨 · 조건 충족 시 참고</div>
      <div class="levels">
        <div class="level"><span>조건부 진입가</span><b class="entry">{analysis['entry']:,}원</b></div>
        <div class="level"><span>손절 기준</span><b class="stop">{analysis['stop']:,}원</b></div>
        <div class="level"><span>1차 목표</span><b class="target">{analysis['target1']:,}원</b></div>
        <div class="level"><span>2차 목표</span><b class="target">{analysis['target2']:,}원</b></div>
      </div>
      <div class="footnote">갱신 {updated_at} · 자동매수 신호가 아닙니다. 실제 주문 전 메리츠 통합호가와 시장 상태를 확인하세요.</div>
    </div>
    """
    # 줄바꿈·들여쓰기를 제거해 Streamlit이 HTML 일부를
    # 코드 상자로 오인하지 못하게 합니다.
    compact_html = "".join(line.strip() for line in card_html.splitlines())
    st.markdown(compact_html, unsafe_allow_html=True)


def decide_status(row):
    price = row["현재가"]
    vwap = row["VWAP"]
    change_pct = row["등락률(%)"]
    trading_value = row["오늘누적거래대금"]
    if change_pct >= 12:
        return "🔴 추격금지"
    if change_pct <= -3 or (vwap > 0 and price < vwap):
        return "⚪ 진입금지"
    if 1 <= change_pct <= 8 and trading_value >= 30_000_000_000 and price >= vwap:
        return "🟢 기술지표검사 대상"
    if 0 <= change_pct <= 8 and trading_value >= 20_000_000_000 and price >= vwap:
        return "🟡 눌림대기"
    return "⚪ 조건대기"


def decide_momentum_status(row):
    price = float(row["현재가"])
    vwap = float(row["VWAP"])
    change_pct = float(row["등락률(%)"])
    trading_value = float(row["오늘누적거래대금"])
    volume_ratio = float(row["전일대비거래량(%)"])
    turnover = float(row.get("거래량회전율(%)", 0))
    vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 0

    if change_pct >= 18 or vwap_gap > 4 or turnover >= 30:
        return "🔴 과열·추격금지"
    if change_pct < 2 or (vwap > 0 and price < vwap):
        return "⚪ 조건대기"
    if (
        3 <= change_pct <= 12
        and trading_value >= 5_000_000_000
        and volume_ratio >= 120
    ):
        return "🟢 급등 정밀검사"
    if 2 <= change_pct <= 15 and trading_value >= 3_000_000_000:
        return "🟡 거래량 확대 감시"
    return "⚪ 조건대기"


def choose_momentum_targets(universe):
    if universe.empty:
        return []
    amount_codes = universe.nlargest(18, "1차거래대금근사")["종목코드"].tolist()
    growth_codes = universe.nlargest(18, "거래량증가율(%)")["종목코드"].tolist()
    return list(dict.fromkeys(amount_codes + growth_codes))[:30]


def merge_realtime(universe, prices, scanner_type="quality"):
    realtime = pd.DataFrame(prices.values())
    if realtime.empty:
        return realtime
    result = universe.merge(realtime, on="종목코드", how="inner")
    result["VWAP위치(%)"] = ((result["현재가"] / result["VWAP"] - 1) * 100).replace([float("inf"), -float("inf")], 0).round(2)
    result["오늘거래대금(억원)"] = (result["오늘누적거래대금"] / 100_000_000).round(1)
    if scanner_type == "momentum":
        result = result[
            result["등락률(%)"].between(2, 20)
            & (result["오늘누적거래량"] >= 100_000)
            & (result["오늘누적거래대금"] >= 3_000_000_000)
        ].copy()
        result["현재판정"] = result.apply(decide_momentum_status, axis=1)
    else:
        result["현재판정"] = result.apply(decide_status, axis=1)
    return result.sort_values("오늘누적거래대금", ascending=False).reset_index(drop=True)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.caption("✅ 한국투자증권 국내 통합시장·미국 시세 API 연결 준비")

market_label = st.radio(
    "시장 선택",
    ["🇰🇷 국내주식", "🇺🇸 미국주식"],
    horizontal=True,
    label_visibility="collapsed",
)
market_code = "kr" if market_label.startswith("🇰🇷") else "us"
strategy_options = ["🏦 우량주 단타", "🔥 급등주 단타"]
if market_code == "us":
    strategy_options.append("🪙 동전주 급등")
strategy_label = st.radio(
    "검색 방식",
    strategy_options,
    horizontal=True,
    label_visibility="collapsed",
)

if strategy_label.startswith("🏦"):
    strategy_code = "quality"
elif strategy_label.startswith("🪙"):
    strategy_code = "penny"
else:
    strategy_code = "momentum"
scanner_type = f"{market_code}_{strategy_code}"
is_domestic = market_code == "kr"

scan_button_labels = {
    "kr_quality": "국내 우량주 통합시장 검사",
    "kr_momentum": "국내 급등주 거래량 검사",
    "us_quality": "미국 우량주 10종목씩 고속 검사",
    "us_momentum": "미국 급등주 10종목씩 고속 검사",
    "us_penny": "미국 동전주 급등 실시간 발견",
}

us_session_choice = "자동(현재 장)"
use_yahoo_candidates = False
if not is_domestic:
    st.info(
        "미국 V8은 한투 공식 복수종목 시세로 한 번에 10종목씩 조회합니다. "
        "가격·거래량·판정은 한투 데이터만 사용합니다."
    )
    us_session_choice = st.selectbox(
        "미국장 시세 선택",
        ["자동(현재 장)", "주간거래", "프리·정규·애프터"],
        help="자동은 한국시간에 따라 주간거래 코드와 미국 정규거래소 코드를 바꾸어 조회합니다.",
    )
    if strategy_code in ("momentum", "penny"):
        use_yahoo_candidates = st.checkbox(
            "야후 급등종목을 후보목록에만 추가",
            value=strategy_code == "penny",
            help="야후는 후보 발견용입니다. 표시 가격과 최종 판정은 모두 한투 API로 다시 확인합니다.",
        )

if st.button(scan_button_labels[scanner_type], type="primary"):
    try:
        token = issue_access_token(APP_KEY, APP_SECRET)
        price_errors = []

        if scanner_type == "kr_quality":
            with st.spinner("국내 시가총액 상위 종목을 선정하는 중입니다..."):
                kospi_rows = get_market_cap_ranking(token, "0001")
                kosdaq_rows = get_market_cap_ranking(token, "1001")
                universe = build_universe(kospi_rows, kosdaq_rows)
                targets = universe.nlargest(30, "1차거래대금근사")["종목코드"].tolist()
            if universe.empty:
                raise RuntimeError("조건에 맞는 국내 우량주 후보를 받지 못했습니다.")
            with st.spinner("국내 통합시장 현재가를 조회하는 중입니다..."):
                prices, price_errors = collect_integrated_prices(token, targets)
                table = merge_realtime(universe, prices, scanner_type="quality")

        elif scanner_type == "kr_momentum":
            with st.spinner("국내 거래증가율·거래금액 상위 종목을 선정하는 중입니다..."):
                growth_rows = get_volume_rank(token, "1")
                amount_rows = get_volume_rank(token, "3")
                universe = build_momentum_universe(growth_rows + amount_rows)
                targets = choose_momentum_targets(universe)
            if universe.empty:
                raise RuntimeError("조건에 맞는 국내 급등주 후보를 받지 못했습니다.")
            with st.spinner("국내 통합시장 현재가를 조회하는 중입니다..."):
                prices, price_errors = collect_integrated_prices(token, targets)
                table = merge_realtime(universe, prices, scanner_type="momentum")

        elif scanner_type == "us_quality":
            with st.spinner("미국 우량주를 10종목씩 빠르게 조회하는 중입니다..."):
                session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
                us_candidates = unique_us_pairs(US_QUALITY_UNIVERSE)
                market_rows, price_errors = get_us_multiple_prices(
                    token, us_candidates, session_mode
                )
                table = build_us_fast_table(
                    market_rows, us_candidates, strategy="quality"
                )
                us_source_note = "한투 공식 우량주 후보목록"

        else:
            scan_name = "동전주 급등" if strategy_code == "penny" else "급등주"
            with st.spinner(f"미국 {scan_name} 후보를 발견한 뒤 한투로 재검증하는 중입니다..."):
                session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
                us_candidates = list(US_MOMENTUM_SEED)
                yahoo_count = 0
                yahoo_error = ""
                if use_yahoo_candidates:
                    try:
                        yahoo_pairs = get_yahoo_us_candidates(
                            10 if strategy_code == "penny" else 0
                        )
                        yahoo_count = len(yahoo_pairs)
                        us_candidates = yahoo_pairs + us_candidates
                    except Exception as error:
                        yahoo_error = str(error)
                us_candidates = unique_us_pairs(us_candidates)[:100]
                market_rows, price_errors = get_us_multiple_prices(
                    token, us_candidates, session_mode
                )
                table = build_us_fast_table(
                    market_rows, us_candidates, strategy=strategy_code
                )
                us_source_note = (
                    f"한투 검증 + 야후 후보 {yahoo_count}종목"
                    if use_yahoo_candidates and not yahoo_error
                    else "한투 급등 감시목록"
                )

        if table.empty:
            if price_errors:
                raise RuntimeError("\n".join(price_errors[:3]))
            raise RuntimeError("현재 조건에 맞는 종목을 받지 못했습니다. 미국 장 운영 시간에 다시 확인해 주세요.")

        if scanner_type == "us_penny":
            with st.spinner("상위 동전주의 분봉을 다시 검사해 매수타점을 계산하는 중입니다..."):
                st.session_state["us_penny_signals"] = analyze_us_penny_candidates(
                    token, table, session_mode, limit=8
                )
        else:
            st.session_state.pop("us_penny_signals", None)

        st.session_state["scan_table"] = table
        st.session_state["scan_type"] = scanner_type
        if not is_domestic:
            st.session_state["us_scan_meta"] = {
                "session": session_detail,
                "time": scan_time,
                "source": us_source_note,
                "errors": price_errors,
            }
        st.session_state.pop("last_analysis", None)
        market_text = "국내" if is_domestic else "미국"
        kind_text = "우량주" if strategy_code == "quality" else "동전주" if strategy_code == "penny" else "급등주"
        st.toast(f"{market_text} {kind_text} {len(table)}종목 갱신 완료")
    except Exception as error:
        st.error("종목 검사에 실패했습니다.")
        st.code(str(error))


has_current_scan = (
    "scan_table" in st.session_state
    and st.session_state.get("scan_type") == scanner_type
)

if has_current_scan and not is_domestic:
    table = st.session_state["scan_table"]
    us_meta = st.session_state.get("us_scan_meta", {})
    us_kind = "우량주" if strategy_code == "quality" else "동전주 급등" if strategy_code == "penny" else "급등주"
    st.success(f"미국 {us_kind} 후보 {len(table)}종목을 받았습니다.")
    st.caption(
        f"세션: {us_meta.get('session', '-')} · "
        f"갱신: {us_meta.get('time', '-')} KST · "
        f"후보: {us_meta.get('source', '-')}. "
        "표시 가격과 판정은 한투 API 기준입니다."
    )

    if strategy_code == "penny":
        st.subheader("🎯 급등주 자동 매수타점")
        signal_items = [
            item for item in st.session_state.get("us_penny_signals", [])
            if item.get("analysis")
        ]
        actionable = [
            item for item in signal_items
            if item["analysis"]["verdict"] in ("🟢 매수검토", "🟡 눌림대기")
        ]
        shown = (actionable or signal_items)[:3]
        if not shown:
            st.warning("현재 분봉이 충분한 동전주가 없습니다. 장이 열린 후 다시 조회하세요.")
        for item in shown:
            row = item["row"]
            analysis = item["analysis"]
            verdict = analysis["verdict"]
            color = "#61df88" if verdict.startswith("🟢") else "#ffd45d" if verdict.startswith("🟡") else "#ff7b81"
            name = html.escape(str(row.get("종목명") or row.get("종목코드")))
            ticker = html.escape(str(row.get("종목코드", "")))
            st.markdown(
                f'''<div class="stock-card" style="border-color:{color}">
                <div class="card-head"><div><div class="stock-name">{name}</div>
                <div class="ticker">{ticker} · ${row['현재가($)']:.4f} · {row['등락률(%)']:+.1f}%</div></div>
                <div style="color:{color};font-weight:900">{verdict}<br><small>{analysis['score']}/7</small></div></div>
                <div class="grid2" style="margin-top:10px">
                  <div class="data-row"><span class="data-label">매수검토가</span><span class="data-value">${analysis['entry']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">손절가</span><span class="data-value bad">${analysis['stop']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">1차 목표</span><span class="data-value ok">${analysis['target1']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">2차 목표</span><span class="data-value ok">${analysis['target2']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">RSI 1/3/5</span><span class="data-value">{analysis['rsi_1']:.0f}/{analysis['rsi_3']:.0f}/{analysis['rsi_5']:.0f}</span></div>
                  <div class="data-row"><span class="data-label">3분 MACD</span><span class="data-value">{'상승' if analysis['bullish_macd'] else '약화'}</span></div>
                  <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{analysis['vwap_gap']:+.1f}%</span></div>
                  <div class="data-row"><span class="data-label">최근 거래량</span><span class="data-value">{analysis['volume_speed']:.1f}배</span></div>
                </div></div>''',
                unsafe_allow_html=True,
            )
        st.caption("🟢는 자동매수가 아니라 수동 진입 검토 알림입니다. 표시가가 달라지면 다시 조회하세요.")

    us_columns = [
        "종목코드", "종목명", "현재가($)", "등락률(%)", "오늘거래량",
        "전일대비거래량(%)", "오늘거래대금(백만$)", "VWAP($)",
        "VWAP위치(%)", "스프레드(%)", "체결강도", "현재판정",
    ]

    st.dataframe(
        table[us_columns],
        use_container_width=True,
        hide_index=True,
        height=500,
        column_config={
            "현재가($)": st.column_config.NumberColumn(format="$%.2f"),
            "등락률(%)": st.column_config.NumberColumn(format="%.2f%%"),
            "오늘거래량": st.column_config.NumberColumn(format="%d주"),
            "전일대비거래량(%)": st.column_config.NumberColumn(format="%.1f%%"),
            "오늘거래대금(백만$)": st.column_config.NumberColumn(format="$%.2fM"),
            "VWAP($)": st.column_config.NumberColumn(format="$%.2f"),
            "VWAP위치(%)": st.column_config.NumberColumn(format="%.2f%%"),
            "스프레드(%)": st.column_config.NumberColumn(format="%.2f%%"),
            "체결강도": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    if us_meta.get("errors"):
        st.caption("일부 묶음은 재시도 후 제외됐습니다. 표시된 종목은 정상 응답입니다.")
    if strategy_code != "penny":
        st.warning("이 표는 후보 압축용이며 매수 신호가 아닙니다. RSI·MACD는 선택 종목 정밀검사에서 확인하세요.")


if has_current_scan and is_domestic:
    table = st.session_state["scan_table"]
    display_columns = [
        "시장", "시총순위", "종목코드", "종목명", "시세기준", "현재가", "등락률(%)",
        "VWAP", "VWAP위치(%)", "오늘누적거래량", "전일대비거래량(%)",
        "오늘거래대금(억원)", "현재판정",
    ]
    if strategy_code == "momentum":
        display_columns[1] = "급등순위"
        display_columns[10:10] = ["거래량증가율(%)", "거래량회전율(%)"]
        preferred_statuses = ["🟢 급등 정밀검사", "🟡 거래량 확대 감시"]
    else:
        preferred_statuses = ["🟢 기술지표검사 대상", "🟡 눌림대기"]
    preferred = table[table["현재판정"].isin(preferred_statuses)].copy()
    if preferred.empty:
        preferred = table.head(10).copy()

    labels = {
        f"{row['현재판정']}  {row['종목명']} ({row['종목코드']})": index
        for index, row in preferred.iterrows()
    }
    selected_label = st.selectbox("정밀검사할 종목", list(labels.keys()))

    if st.button("선택 종목 한눈에 검사", type="secondary"):
        selected_row = table.loc[labels[selected_label]]
        st.session_state.pop("last_analysis", None)
        try:
            with st.spinner("통합·KRX 최근 분봉을 불러와 기술지표를 계산하는 중입니다..."):
                token = issue_access_token(APP_KEY, APP_SECRET)
                minute = get_recent_minutes(token, selected_row["종목코드"], pages=4)

            if len(minute) < 35:
                now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
                if now_kst.strftime("%H%M%S") < "090000":
                    st.warning("오늘 장이 시작되기 전입니다. 오전 9시 이후에 다시 검사해 주세요.")
                else:
                    st.warning(
                        f"통합·KRX 오늘 분봉이 {len(minute)}개라 "
                        "RSI·MACD 판정을 만들지 않았습니다. 다른 종목을 선택해 주세요."
                    )
                st.stop()

            analysis = analyze_selected_stock(minute, selected_row)
            st.session_state["last_analysis"] = {
                "label": selected_label,
                "row": selected_row.to_dict(),
                "analysis": analysis,
                "scan_type": scanner_type,
            }
        except Exception as error:
            st.error("기술지표 검사에 실패했습니다.")
            st.code(str(error))

if (
    is_domestic
    and "last_analysis" in st.session_state
    and st.session_state["last_analysis"].get("scan_type") == scanner_type
):
    saved = st.session_state["last_analysis"]
    analysis = saved["analysis"]
    render_compact_card(saved)

    reasons = pd.DataFrame([
        {"검사항목": "VWAP", "결과": "통과" if 0 <= analysis["vwap_gap"] <= 2 else "미통과", "현재값": f"{analysis['vwap_gap']:+.2f}%"},
        {"검사항목": "RSI", "결과": "통과" if 45 <= analysis["rsi_3"] <= 70 else "미통과", "현재값": f"3분 {analysis['rsi_3']:.1f}"},
        {"검사항목": "MACD", "결과": "통과" if analysis["bullish_macd"] else "미통과", "현재값": "상승" if analysis["bullish_macd"] else "약화"},
        {"검사항목": "1·3·5분 추세", "결과": "통과" if analysis["trend_1"] and analysis["trend_3"] and analysis["trend_5"] else "미통과", "현재값": f"{analysis['trend_1']}/{analysis['trend_3']}/{analysis['trend_5']}"},
        {"검사항목": "15분 추세", "결과": "통과" if analysis["trend_15"] else "미통과", "현재값": "상승" if analysis["trend_15"] else "확인필요"},
        {"검사항목": "거래량 속도", "결과": "통과" if analysis["volume_speed"] >= 1 else "미통과", "현재값": f"{analysis['volume_speed']:.2f}배"},
    ])
    with st.expander("세부 판정 근거 보기"):
        st.dataframe(reasons, use_container_width=True, hide_index=True)

    with st.expander("상세 차트 열기"):
        st.caption("상단 카드는 이 차트들의 RSI·MACD·EMA9 계산 결과를 요약한 것입니다.")
        tab1, tab3, tab5, tab15 = st.tabs(["1분", "3분", "5분", "15분"])
        for tab, minutes in ((tab1, 1), (tab3, 3), (tab5, 5), (tab15, 15)):
            with tab:
                frame = analysis["frames"][minutes]
                st.caption(f"{minutes}분 가격·EMA9")
                st.line_chart(frame[["종가", "EMA9"]], use_container_width=True, height=220)
                if frame["RSI"].notna().any():
                    st.caption("RSI(14)")
                    st.line_chart(frame[["RSI"]], use_container_width=True, height=170)
                st.caption("MACD(12,26,9)")
                st.line_chart(frame[["MACD", "MACD시그널"]], use_container_width=True, height=170)
                st.caption("거래량")
                st.bar_chart(frame[["거래량"]], use_container_width=True, height=170)


if has_current_scan and is_domestic:
    table = st.session_state["scan_table"]
    with st.expander(f"전체 {len(table)}종목 표 보기"):
        st.caption("현재가·VWAP·거래량·거래대금은 한국투자증권 통합시장(UN) 값입니다.")
        st.dataframe(
            table[display_columns],
            use_container_width=True,
            hide_index=True,
            column_config={
                "현재가": st.column_config.NumberColumn(format="%d원"),
                "등락률(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "VWAP": st.column_config.NumberColumn(format="%.0f원"),
                "VWAP위치(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "오늘누적거래량": st.column_config.NumberColumn(format="%d주"),
                "거래량증가율(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "거래량회전율(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "전일대비거래량(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "오늘거래대금(억원)": st.column_config.NumberColumn(format="%.1f억원"),
            },
        )
