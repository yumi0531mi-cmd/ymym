import html
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="국내 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내 당일 단타 스캐너")
st.caption("📱 모바일 V5 · 분봉 자동 재시도 버전")

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


def render_compact_card(saved):
    analysis = saved["analysis"]
    quote = saved["row"]
    stock_name = html.escape(str(quote["종목명"]))
    ticker = html.escape(str(quote["종목코드"]))
    market = html.escape(str(quote["시장"]))
    scan_name = "급등주" if saved.get("scan_type") == "momentum" else "우량주"
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
    if saved.get("scan_type") == "momentum":
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

st.caption("✅ 한국투자증권 통합시장(UN) 연결 준비")

scanner_label = st.radio(
    "검색 방식",
    ["🏦 우량주 단타", "🔥 급등주 단타"],
    horizontal=True,
    label_visibility="collapsed",
)
scanner_type = "quality" if scanner_label.startswith("🏦") else "momentum"
scan_button_label = "우량주 통합시장 검사" if scanner_type == "quality" else "급등주 거래량 검사"

if st.button(scan_button_label, type="primary"):
    try:
        token = issue_access_token(APP_KEY, APP_SECRET)
        if scanner_type == "quality":
            with st.spinner("시가총액 상위 종목을 선정하는 중입니다..."):
                kospi_rows = get_market_cap_ranking(token, "0001")
                kosdaq_rows = get_market_cap_ranking(token, "1001")
                universe = build_universe(kospi_rows, kosdaq_rows)
                targets = universe.nlargest(30, "1차거래대금근사")["종목코드"].tolist()
        else:
            with st.spinner("거래증가율·거래금액 상위 급등주를 선정하는 중입니다..."):
                growth_rows = get_volume_rank(token, "1")
                amount_rows = get_volume_rank(token, "3")
                universe = build_momentum_universe(growth_rows + amount_rows)
                targets = choose_momentum_targets(universe)

        if universe.empty:
            st.warning("조건에 맞는 1차 후보를 받지 못했습니다.")
            st.stop()

        with st.spinner("통합시장 현재가를 순서대로 조회하는 중입니다..."):
            prices, price_errors = collect_integrated_prices(token, targets)
            table = merge_realtime(universe, prices, scanner_type=scanner_type)

        if table.empty:
            st.error("통합시장 현재가를 받지 못했습니다.")
            if price_errors:
                st.code("\n".join(price_errors[:3]))
            st.warning("KRX 가격으로 임의 대체하지 않았습니다.")
            st.stop()

        st.session_state["scan_table"] = table
        st.session_state["scan_type"] = scanner_type
        st.session_state.pop("last_analysis", None)
        kind_text = "우량주" if scanner_type == "quality" else "급등주"
        st.toast(f"{kind_text} 통합시장 {len(table)}종목 갱신 완료")
    except Exception as error:
        st.error("종목 검사에 실패했습니다.")
        st.code(str(error))


if "scan_table" in st.session_state and st.session_state.get("scan_type") == scanner_type:
    table = st.session_state["scan_table"]
    display_columns = [
        "시장", "시총순위", "종목코드", "종목명", "시세기준", "현재가", "등락률(%)",
        "VWAP", "VWAP위치(%)", "오늘누적거래량", "전일대비거래량(%)",
        "오늘거래대금(억원)", "현재판정",
    ]
    if scanner_type == "momentum":
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
    "last_analysis" in st.session_state
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


if "scan_table" in st.session_state and st.session_state.get("scan_type") == scanner_type:
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
