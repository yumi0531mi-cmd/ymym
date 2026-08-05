import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="국내 우량주 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내 우량주 당일 단타 스캐너")
st.caption("통합시장 현재가로 1차 선별한 뒤, 선택 종목의 RSI·MACD·VWAP·1·3·5·15분 추세를 검사합니다.")


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


def get_minute_page(token, ticker, end_time):
    response = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "FHKST03010200"),
        params={
            "FID_COND_MRKT_DIV_CODE": "UN",
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


def get_recent_minutes(token, ticker, pages=4):
    all_rows = []
    cursor = "235959"

    for _ in range(pages):
        rows = get_minute_page(token, ticker, cursor)
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
    }


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


def merge_realtime(universe, prices):
    realtime = pd.DataFrame(prices.values())
    if realtime.empty:
        return realtime
    result = universe.merge(realtime, on="종목코드", how="inner")
    result["VWAP위치(%)"] = ((result["현재가"] / result["VWAP"] - 1) * 100).replace([float("inf"), -float("inf")], 0).round(2)
    result["오늘거래대금(억원)"] = (result["오늘누적거래대금"] / 100_000_000).round(1)
    result["현재판정"] = result.apply(decide_status, axis=1)
    return result.sort_values("오늘누적거래대금", ascending=False).reset_index(drop=True)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.success("한국투자증권 API 연결 준비가 완료됐습니다.")
st.info(
    "시가총액 순위 선정에는 KRX 자료를 사용하지만, 아래 표의 현재가·VWAP·거래량·거래대금은 "
    "한국투자증권 주식현재가 시세2의 통합시장(UN) 값만 표시합니다."
)

if st.button("통합 현재가 우량주 검사", type="primary"):
    try:
        with st.spinner("시가총액 상위 종목을 선정하는 중입니다..."):
            token = issue_access_token(APP_KEY, APP_SECRET)
            kospi_rows = get_market_cap_ranking(token, "0001")
            kosdaq_rows = get_market_cap_ranking(token, "1001")
            universe = build_universe(kospi_rows, kosdaq_rows)

        if universe.empty:
            st.warning("시가총액 상위 종목을 받지 못했습니다.")
            st.stop()

        targets = universe.nlargest(30, "1차거래대금근사")["종목코드"].tolist()
        with st.spinner("통합시장 현재가를 순서대로 조회하는 중입니다..."):
            prices, price_errors = collect_integrated_prices(token, targets)
            table = merge_realtime(universe, prices)

        if table.empty:
            st.error("통합시장 현재가를 받지 못했습니다.")
            if price_errors:
                st.code("\n".join(price_errors[:3]))
            st.warning("KRX 가격으로 임의 대체하지 않았습니다.")
            st.stop()

        st.session_state["scan_table"] = table
        st.success(f"통합시장 현재가를 받은 {len(table)}종목을 표시합니다.")
    except Exception as error:
        st.error("통합시장 검사에 실패했습니다.")
        st.code(str(error))


if "scan_table" in st.session_state:
    table = st.session_state["scan_table"]
    display_columns = [
        "시장", "시총순위", "종목코드", "종목명", "시세기준", "현재가", "등락률(%)",
        "VWAP", "VWAP위치(%)", "오늘누적거래량", "전일대비거래량(%)",
        "오늘거래대금(억원)", "현재판정",
    ]
    st.subheader("1차 유동성·VWAP 검사")
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
            "전일대비거래량(%)": st.column_config.NumberColumn(format="%.1f%%"),
            "오늘거래대금(억원)": st.column_config.NumberColumn(format="%.1f억원"),
        },
    )

    preferred = table[table["현재판정"].isin(["🟢 기술지표검사 대상", "🟡 눌림대기"])].copy()
    if preferred.empty:
        preferred = table.head(10).copy()

    labels = {
        f"{row['종목명']} ({row['종목코드']})": index
        for index, row in preferred.iterrows()
    }
    st.subheader("2차 RSI·MACD·분봉 정밀검사")
    selected_label = st.selectbox("정밀검사할 종목", list(labels.keys()))

    if st.button("선택 종목 기술지표 검사", type="secondary"):
        selected_row = table.loc[labels[selected_label]]
        try:
            with st.spinner("통합시장 최근 분봉 120개를 불러와 기술지표를 계산하는 중입니다..."):
                token = issue_access_token(APP_KEY, APP_SECRET)
                minute = get_recent_minutes(token, selected_row["종목코드"], pages=4)

            if len(minute) < 35:
                st.warning(f"분봉이 {len(minute)}개뿐이라 RSI·MACD 판정을 만들지 않았습니다.")
                st.stop()

            analysis = analyze_selected_stock(minute, selected_row)
            st.session_state["last_analysis"] = {
                "label": selected_label,
                "row": selected_row.to_dict(),
                "analysis": analysis,
            }
        except Exception as error:
            st.error("기술지표 검사에 실패했습니다.")
            st.code(str(error))


if "last_analysis" in st.session_state:
    saved = st.session_state["last_analysis"]
    analysis = saved["analysis"]
    quote = saved["row"]
    st.divider()
    st.subheader(f"{saved['label']} 정밀검사 결과")

    if analysis["verdict"].startswith("🟢"):
        st.success(analysis["verdict"])
    elif analysis["verdict"].startswith("🔴"):
        st.error(analysis["verdict"])
    elif analysis["verdict"].startswith("🟡"):
        st.warning(analysis["verdict"])
    else:
        st.info(analysis["verdict"])

    metric_columns = st.columns(6)
    metric_columns[0].metric("통합 현재가", f"{int(quote['현재가']):,}원")
    metric_columns[1].metric("당일 VWAP", f"{int(quote['VWAP']):,}원", f"{analysis['vwap_gap']:+.2f}%")
    metric_columns[2].metric("RSI 1분", f"{analysis['rsi_1']:.1f}")
    metric_columns[3].metric("RSI 3분", f"{analysis['rsi_3']:.1f}")
    metric_columns[4].metric("RSI 5분", f"{analysis['rsi_5']:.1f}")
    metric_columns[5].metric("최근 거래량 속도", f"{analysis['volume_speed']:.2f}배")

    reasons = pd.DataFrame([
        {"검사항목": "VWAP", "결과": "통과" if 0 <= analysis["vwap_gap"] <= 2 else "미통과", "현재값": f"{analysis['vwap_gap']:+.2f}%"},
        {"검사항목": "RSI", "결과": "통과" if 45 <= analysis["rsi_3"] <= 70 else "미통과", "현재값": f"3분 {analysis['rsi_3']:.1f}"},
        {"검사항목": "MACD", "결과": "통과" if analysis["bullish_macd"] else "미통과", "현재값": "상승" if analysis["bullish_macd"] else "약화"},
        {"검사항목": "1·3·5분 추세", "결과": "통과" if analysis["trend_1"] and analysis["trend_3"] and analysis["trend_5"] else "미통과", "현재값": f"{analysis['trend_1']}/{analysis['trend_3']}/{analysis['trend_5']}"},
        {"검사항목": "15분 추세", "결과": "통과" if analysis["trend_15"] else "미통과", "현재값": "상승" if analysis["trend_15"] else "확인필요"},
        {"검사항목": "거래량 속도", "결과": "통과" if analysis["volume_speed"] >= 1 else "미통과", "현재값": f"{analysis['volume_speed']:.2f}배"},
    ])
    st.dataframe(reasons, use_container_width=True, hide_index=True)

    levels = st.columns(4)
    levels[0].metric("조건부 진입가", f"{analysis['entry']:,}원")
    levels[1].metric("손절 기준", f"{analysis['stop']:,}원")
    levels[2].metric("1차 목표", f"{analysis['target1']:,}원")
    levels[3].metric("2차 목표", f"{analysis['target2']:,}원")

    tab1, tab3, tab5, tab15 = st.tabs(["1분", "3분", "5분", "15분"])
    for tab, minutes in ((tab1, 1), (tab3, 3), (tab5, 5), (tab15, 15)):
        with tab:
            frame = analysis["frames"][minutes]
            st.caption(f"{minutes}분 가격·EMA9")
            st.line_chart(frame[["종가", "EMA9"]], use_container_width=True)
            if frame["RSI"].notna().any():
                st.caption("RSI(14)")
                st.line_chart(frame[["RSI"]], use_container_width=True)
            st.caption("MACD(12,26,9)")
            st.line_chart(frame[["MACD", "MACD시그널"]], use_container_width=True)
            st.caption("거래량")
            st.bar_chart(frame[["거래량"]], use_container_width=True)

    st.warning(
        "'진입 조건 충족'은 자동매수나 수익 보장이 아닙니다. 현재 버전은 당일 분봉 기반 시험 신호이며, "
        "실전 진입 전 메리츠 호가·시장 상태를 확인하고 손절 기준을 지켜야 합니다."
    )
