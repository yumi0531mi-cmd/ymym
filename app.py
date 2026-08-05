import json
import time

import pandas as pd
import requests
import streamlit as st
import websocket


st.set_page_config(
    page_title="국내 우량주 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내 우량주 당일 단타 스캐너")
st.caption("시가총액 상위 60종목 중 거래가 활발한 종목을 통합시장(KRX+NXT) 실시간 체결로 다시 검사합니다.")


def load_secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return ""


APP_KEY = load_secret("KIS_APP_KEY")
APP_SECRET = load_secret("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"
TOTAL_TR_ID = "H0UNCNT0"


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


@st.cache_data(ttl=82800, show_spinner=False)
def issue_ws_approval_key(app_key, app_secret):
    response = requests.post(
        f"{BASE_URL}/oauth2/Approval",
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"웹소켓 접속키 발급 실패: HTTP {response.status_code}")
    data = response.json()
    approval_key = data.get("approval_key")
    if not approval_key:
        raise RuntimeError(data.get("error_description") or data.get("msg1") or "웹소켓 접속키를 받지 못했습니다.")
    return approval_key


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


TOTAL_COLUMNS = [
    "종목코드", "체결시각", "현재가", "전일대비부호", "전일대비", "등락률(%)",
    "VWAP", "시가", "고가", "저가", "매도1호가", "매수1호가", "체결량",
    "오늘누적거래량", "오늘누적거래대금", "매도체결건수", "매수체결건수",
    "순매수체결건수", "체결강도",
]


def parse_total_tick(payload):
    values = payload.split("^")
    if len(values) < 19:
        return None
    row = dict(zip(TOTAL_COLUMNS, values[:19]))
    return {
        "종목코드": row["종목코드"],
        "체결시각": row["체결시각"],
        "현재가": to_int(row["현재가"]),
        "등락률(%)": round(to_float(row["등락률(%)"]), 2),
        "VWAP": to_float(row["VWAP"]),
        "시가": to_int(row["시가"]),
        "고가": to_int(row["고가"]),
        "저가": to_int(row["저가"]),
        "매도1호가": to_int(row["매도1호가"]),
        "매수1호가": to_int(row["매수1호가"]),
        "오늘누적거래량": to_int(row["오늘누적거래량"]),
        "오늘누적거래대금": to_int(row["오늘누적거래대금"]),
        "체결강도": round(to_float(row["체결강도"]), 1),
    }


def collect_integrated_ticks(approval_key, tickers, wait_seconds=7):
    ws = websocket.create_connection(WS_URL, timeout=10)
    ticks = {}
    try:
        for ticker in tickers:
            request_data = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {"input": {"tr_id": TOTAL_TR_ID, "tr_key": ticker}},
            }
            ws.send(json.dumps(request_data))
            time.sleep(0.06)

        deadline = time.time() + wait_seconds
        ws.settimeout(1)
        while time.time() < deadline and len(ticks) < len(tickers):
            try:
                message = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue

            if not message:
                continue

            if message.startswith("0|"):
                parts = message.split("|", 3)
                if len(parts) == 4 and parts[1] == TOTAL_TR_ID:
                    tick = parse_total_tick(parts[3])
                    if tick and tick["현재가"] > 0:
                        ticks[tick["종목코드"]] = tick
                continue

            try:
                control = json.loads(message)
                if control.get("header", {}).get("tr_id") == "PINGPONG":
                    ws.send(message)
            except Exception:
                pass
    finally:
        ws.close()
    return ticks


def decide_status(row):
    price = row["현재가"]
    vwap = row["VWAP"]
    change_pct = row["등락률(%)"]
    trading_value = row["오늘누적거래대금"]
    strength = row["체결강도"]
    if change_pct >= 12:
        return "🔴 추격금지"
    if change_pct <= -3 or (vwap > 0 and price < vwap):
        return "⚪ 진입금지"
    if 1 <= change_pct <= 8 and trading_value >= 30_000_000_000 and strength >= 100 and price >= vwap:
        return "🟢 기술지표검사 대상"
    if 0 <= change_pct <= 8 and trading_value >= 20_000_000_000 and price >= vwap:
        return "🟡 눌림대기"
    return "⚪ 조건대기"


def merge_realtime(universe, ticks):
    realtime = pd.DataFrame(ticks.values())
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
    "시가총액 순위 선정에는 KRX 자료를 사용하지만, 아래 정밀검사 표의 현재가·VWAP·거래량·거래대금·체결강도는 "
    "한국투자증권 통합 실시간 체결(H0UNCNT0)만 표시합니다. 통합 체결을 받지 못한 종목은 표에서 제외됩니다."
)

if st.button("통합 실시간 우량주 검사", type="primary"):
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

        with st.spinner("통합시장 실시간 체결을 약 7초 동안 수신하는 중입니다..."):
            approval_key = issue_ws_approval_key(APP_KEY, APP_SECRET)
            ticks = collect_integrated_ticks(approval_key, targets)
            table = merge_realtime(universe, ticks)

        if table.empty:
            st.error("통합 실시간 체결을 받지 못했습니다.")
            st.warning("장 운영시간인지 확인하고 잠시 후 다시 눌러주세요. KRX 가격으로 임의 대체하지 않았습니다.")
            st.stop()

        st.success(f"통합 실시간 체결이 들어온 {len(table)}종목을 정확한 시세로 표시합니다.")
        st.caption("현재가가 메리츠의 '통합' 현재가와 일치하는지 삼성전자 등 한 종목을 먼저 비교해 주세요.")

        display_columns = [
            "시장", "시총순위", "종목코드", "종목명", "체결시각", "현재가", "등락률(%)",
            "VWAP", "VWAP위치(%)", "오늘누적거래량", "오늘거래대금(억원)", "체결강도", "현재판정",
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
                "오늘거래대금(억원)": st.column_config.NumberColumn(format="%.1f억원"),
                "체결강도": st.column_config.NumberColumn(format="%.1f"),
            },
        )

        st.warning("아직 RSI·MACD·분봉 추세를 넣기 전입니다. '기술지표검사 대상'은 매수 신호가 아닙니다.")

    except Exception as error:
        st.error("통합 실시간 검사에 실패했습니다.")
        st.code(str(error))
