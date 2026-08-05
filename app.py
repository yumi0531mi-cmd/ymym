import time

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="국내 우량주 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내 우량주 당일 단타 스캐너")
st.caption("시가총액 상위 60종목 중 거래가 활발한 종목을 통합시장(KRX+NXT) 현재가로 다시 검사합니다.")


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

        st.success(f"통합시장 현재가를 받은 {len(table)}종목을 표시합니다.")
        st.caption("현재가가 메리츠의 '통합' 현재가와 일치하는지 삼성전자 등 한 종목을 먼저 비교해 주세요.")

        display_columns = [
            "시장", "시총순위", "종목코드", "종목명", "시세기준", "현재가", "등락률(%)",
            "VWAP", "VWAP위치(%)", "오늘누적거래량", "전일대비거래량(%)",
            "오늘거래대금(억원)", "현재판정",
        ]
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

        st.warning("아직 RSI·MACD·분봉 추세를 넣기 전입니다. '기술지표검사 대상'은 매수 신호가 아닙니다.")

    except Exception as error:
        st.error("통합 실시간 검사에 실패했습니다.")
        st.code(str(error))
