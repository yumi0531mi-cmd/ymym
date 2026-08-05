import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="국내주식 실시간 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 국내주식 실시간 스캐너")
st.caption("한국투자증권 공식 API로 장중 거래량 상위 종목을 조회합니다.")


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
    url = f"{BASE_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    response = requests.post(
        url,
        json=body,
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"토큰 발급 실패: HTTP {response.status_code}"
        )

    data = response.json()
    token = data.get("access_token")

    if not token:
        message = (
            data.get("error_description")
            or data.get("msg1")
            or "접근토큰을 받지 못했습니다."
        )
        raise RuntimeError(message)

    return token


def get_volume_ranking(token):
    url = (
        f"{BASE_URL}"
        "/uapi/domestic-stock/v1/quotations/volume-rank"
    )

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHPST01710000",
        "custtype": "P",
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0",
        "FID_INPUT_PRICE_2": "0",
        "FID_VOL_CNT": "0",
        "FID_INPUT_DATE_1": "",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"거래량 순위 조회 실패: HTTP {response.status_code}"
        )

    data = response.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(
            data.get("msg1", "거래량 순위를 받지 못했습니다.")
        )

    return data.get("output", [])


def build_table(rows):
    records = []

    for row in rows:
        ticker = str(row.get("mksc_shrn_iscd", "")).strip()
        name = str(row.get("hts_kor_isnm", "")).strip()
        price = to_int(row.get("stck_prpr"))
        change_pct = to_float(row.get("prdy_ctrt"))
        volume = to_int(row.get("acml_vol"))
        volume_increase = to_float(row.get("vol_inrt"))
        trading_value_million = to_int(
            row.get("acml_tr_pbmn")
        )

        trading_value_won = trading_value_million * 1_000_000

        if not ticker or not name or price <= 0:
            continue

        if change_pct >= 18:
            status = "회피(과열)"
        elif change_pct >= 3 and trading_value_won >= 10_000_000_000:
            status = "관찰"
        elif change_pct < 0:
            status = "약세"
        else:
            status = "대기"

        records.append(
            {
                "종목코드": ticker,
                "종목명": name,
                "현재가": price,
                "등락률(%)": round(change_pct, 2),
                "누적거래량": volume,
                "거래량증가율(%)": round(volume_increase, 1),
                "누적거래대금(억원)": round(
                    trading_value_won / 100_000_000,
                    1,
                ),
                "현재판정": status,
            }
        )

    return pd.DataFrame(records)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.success("한국투자증권 API 키를 정상적으로 불러왔습니다.")

st.info(
    "현재 단계는 공식 API의 거래량 상위 데이터를 정확히 받는지 "
    "확인하는 과정입니다. 아직 매수 추천 단계가 아닙니다."
)

if st.button("국내 거래량 상위 조회", type="primary"):
    try:
        with st.spinner("한국투자증권에서 거래량 순위를 불러오는 중입니다..."):
            access_token = issue_access_token(
                APP_KEY,
                APP_SECRET,
            )
            ranking_rows = get_volume_ranking(
                access_token
            )
            ranking_table = build_table(
                ranking_rows
            )

        if ranking_table.empty:
            st.warning("조회된 종목이 없습니다.")
        else:
            st.success(
                f"거래량 상위 {len(ranking_table)}개 종목을 불러왔습니다."
            )

            st.dataframe(
                ranking_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "현재가": st.column_config.NumberColumn(
                        format="%d원"
                    ),
                    "등락률(%)": st.column_config.NumberColumn(
                        format="%.2f%%"
                    ),
                    "누적거래량": st.column_config.NumberColumn(
                        format="%d주"
                    ),
                    "거래량증가율(%)": st.column_config.NumberColumn(
                        format="%.1f%%"
                    ),
                    "누적거래대금(억원)": st.column_config.NumberColumn(
                        format="%.1f억원"
                    ),
                },
            )

    except Exception as error:
        st.error("국내 거래량 순위 조회에 실패했습니다.")
        st.code(str(error))
