import requests
import streamlit as st


st.set_page_config(
    page_title="한투 API 연결 확인",
    page_icon="🔌",
    layout="centered",
)

st.title("🔌 한국투자증권 API 연결 확인")
st.caption("먼저 API 인증과 삼성전자 현재가 조회가 정상인지 검사합니다.")


def read_secret(name):
    try:
        return st.secrets[name]
    except Exception:
        return ""


APP_KEY = read_secret("KIS_APP_KEY")
APP_SECRET = read_secret("KIS_APP_SECRET")


@st.cache_data(ttl=82800, show_spinner=False)
def get_access_token(app_key, app_secret):
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    response = requests.post(url, json=body, timeout=15)
    response.raise_for_status()

    data = response.json()
    token = data.get("access_token")

    if not token:
        raise RuntimeError(data.get("error_description", "접근토큰이 발급되지 않았습니다."))

    return token


def get_samsung_price(token, app_key, app_secret):
    url = (
        "https://openapi.koreainvestment.com:9443"
        "/uapi/domestic-stock/v1/quotations/inquire-price"
    )

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010100",
        "custtype": "P",
    }

    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": "005930",
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "현재가 조회에 실패했습니다."))

    return data.get("output", {})


if not APP_KEY or not APP_SECRET:
    st.warning("아직 API 키가 설정되지 않았습니다.")
    st.info("다음 단계에서 APP KEY와 APP SECRET을 안전하게 설정합니다.")
else:
    if st.button("API 연결 검사", type="primary"):
        try:
            with st.spinner("한국투자증권 서버에 연결하고 있습니다..."):
                access_token = get_access_token(APP_KEY, APP_SECRET)
                stock = get_samsung_price(
                    access_token,
                    APP_KEY,
                    APP_SECRET,
                )

            price = int(stock.get("stck_prpr", 0))
            change_rate = float(stock.get("prdy_ctrt", 0))
            volume = int(stock.get("acml_vol", 0))

            st.success("한국투자증권 API 연결에 성공했습니다.")
            st.metric(
                "삼성전자 현재가",
                f"{price:,}원",
                f"{change_rate:+.2f}%",
            )
            st.write(f"누적 거래량: {volume:,}주")

        except Exception as error:
            st.error("API 연결에 실패했습니다.")
            st.code(str(error))
