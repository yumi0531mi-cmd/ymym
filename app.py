import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="국내 우량주 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내 우량주 당일 단타 스캐너")
st.caption(
    "KOSPI 시가총액 상위 30개와 KOSDAQ 시가총액 상위 30개를 "
    "오늘 장중 데이터로 검색합니다."
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
        raise RuntimeError(
            f"토큰 발급 실패: HTTP {response.status_code}"
        )

    data = response.json()
    token = data.get("access_token")

    if not token:
        raise RuntimeError(
            data.get("error_description")
            or data.get("msg1")
            or "접근토큰을 받지 못했습니다."
        )

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
    response = requests.get(
        (
            f"{BASE_URL}"
            "/uapi/domestic-stock/v1/ranking/market-cap"
        ),
        headers=make_headers(
            token,
            "FHPST01740000",
        ),
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
        raise RuntimeError(
            f"시가총액 조회 실패: HTTP {response.status_code}"
        )

    data = response.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(
            data.get("msg1", "시가총액 순위를 받지 못했습니다.")
        )

    return data.get("output", [])


def is_excluded_product(name):
    excluded_words = (
        "KODEX",
        "TIGER",
        "RISE",
        "ACE",
        "SOL ",
        "HANARO",
        "KOSEF",
        "ARIRANG",
        "PLUS ",
        "TIMEFOLIO",
        "KBSTAR",
        "ETF",
        "ETN",
        "인버스",
        "레버리지",
        "선물",
        "스팩",
    )

    upper_name = name.upper()
    return any(
        word.upper() in upper_name
        for word in excluded_words
    )


def decide_status(change_pct, trading_value):
    if change_pct >= 12:
        return "🔴 추격주의"

    if change_pct <= -3:
        return "⚪ 약세"

    if (
        1.0 <= change_pct <= 8.0
        and trading_value >= 30_000_000_000
    ):
        return "🟢 실시간검사 대상"

    if (
        0 <= change_pct < 1.0
        and trading_value >= 20_000_000_000
    ):
        return "🟡 돌파대기"

    return "⚪ 조건대기"


def build_market_table(kospi_rows, kosdaq_rows):
    records = []

    market_groups = (
        ("KOSPI", kospi_rows),
        ("KOSDAQ", kosdaq_rows),
    )

    for market_name, rows in market_groups:
        for row in rows:
            ticker = str(
                row.get("mksc_shrn_iscd", "")
            ).strip()

            name = str(
                row.get("hts_kor_isnm", "")
            ).strip()

            if not ticker or not name:
                continue

            if is_excluded_product(name):
                continue

            price = to_int(
                row.get("stck_prpr")
            )

            change_pct = to_float(
                row.get("prdy_ctrt")
            )

            volume = to_int(
                row.get("acml_vol")
            )

            shares = to_int(
                row.get("lstn_stcn")
            )

            if price <= 0 or shares <= 0:
                continue

            market_cap = price * shares

            # 현재가 × 오늘 누적 거래량을 이용한 장중 근사치입니다.
            # 다음 단계에서 실시간 체결금액으로 정밀 계산합니다.
            trading_value = price * volume

            status = decide_status(
                change_pct,
                trading_value,
            )

            records.append(
                {
                    "시장": market_name,
                    "시총순위": to_int(
                        row.get("data_rank")
                    ),
                    "종목코드": ticker,
                    "종목명": name,
                    "현재가": price,
                    "등락률(%)": round(
                        change_pct,
                        2,
                    ),
                    "오늘 누적거래량": volume,
                    "오늘 거래대금 근사(억원)": round(
                        trading_value / 100_000_000,
                        1,
                    ),
                    "시가총액(조원)": round(
                        market_cap / 1_000_000_000_000,
                        2,
                    ),
                    "현재판정": status,
                }
            )

    table = pd.DataFrame(records)

    if table.empty:
        return table

    return table.sort_values(
        by=[
            "시장",
            "시총순위",
        ],
        ascending=[
            True,
            True,
        ],
    ).reset_index(drop=True)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.success("한국투자증권 API 연결 준비가 완료됐습니다.")

st.info(
    "거래대금은 오늘 장 시작 후 현재까지의 누적 기준입니다. "
    "현재 단계에서는 현재가×누적거래량으로 근사하며, "
    "다음 단계에서 실시간 체결금액으로 정밀 계산합니다."
)

if st.button("우량주 60종목 검색", type="primary"):
    try:
        with st.spinner(
            "KOSPI·KOSDAQ 시가총액 상위 종목을 불러오는 중입니다..."
        ):
            token = issue_access_token(
                APP_KEY,
                APP_SECRET,
            )

            kospi_rows = get_market_cap_ranking(
                token,
                "0001",
            )

            kosdaq_rows = get_market_cap_ranking(
                token,
                "1001",
            )

            table = build_market_table(
                kospi_rows,
                kosdaq_rows,
            )

        if table.empty:
            st.warning("조건에 맞는 종목을 받지 못했습니다.")
            st.stop()

        candidates = table[
            table["현재판정"].isin(
                [
                    "🟢 실시간검사 대상",
                    "🟡 돌파대기",
                ]
            )
        ].copy()

        st.subheader("🎯 오늘 실시간 정밀검사 후보")

        if candidates.empty:
            st.warning(
                "현재 우량주 60종목 중 1차 조건을 통과한 종목이 없습니다."
            )
        else:
            candidates = candidates.sort_values(
                by="오늘 거래대금 근사(억원)",
                ascending=False,
            )

            st.dataframe(
                candidates,
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("📋 시가총액 상위 우량주 전체")

        st.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "현재가": st.column_config.NumberColumn(
                    format="%d원"
                ),
                "등락률(%)": st.column_config.NumberColumn(
                    format="%.2f%%"
                ),
                "오늘 누적거래량": st.column_config.NumberColumn(
                    format="%d주"
                ),
                "오늘 거래대금 근사(억원)":
                    st.column_config.NumberColumn(
                        format="%.1f억원"
                    ),
                "시가총액(조원)":
                    st.column_config.NumberColumn(
                        format="%.2f조원"
                    ),
            },
        )

    except Exception as error:
        st.error("우량주 검색에 실패했습니다.")
        st.code(str(error))
