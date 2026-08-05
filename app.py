import requests
import streamlit as st


st.set_page_config(
    page_title="한투 API 연결 검사",
    page_icon="🔌",
    layout="centered",
)

st.title("🔌 한국투자증권 API 연결 검사")
st.write("삼성전자 현재가를 조회해 API 연결 상태를 확인합니다.")


def load_secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return ""


APP_KEY = load_secret("KIS_APP_KEY")
APP_SECRET = load_secret("KIS_APP_SECRET")


@st.cache_data(ttl=82800, show_spinner=False)
def issue_access_token(app_key, app_secret):
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    response = requests.post(url, json=body, timeout=20)

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
            or "접근토큰이 없습니다."
        )
        raise RuntimeError(message)

    return token


def inquire_samsung_price(token, app_key, app_secret):
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
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"현재가 조회 실패: HTTP {response.status_code}"
        )

    data = response.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(
            data.get("msg1", "현재가 조회에 실패했습니다.")
        )

    return data["output"]


if not APP_KEY or not APP_SECRET:
    st.error("API 키가 설정되지 않았습니다.")
else:
    st.success("API 키를 안전하게 불러왔습니다.")

    if st.button("삼성전자 현재가 조회", type="primary"):
        try:
            with st.spinner("한국투자증권 서버에 연결 중입니다..."):
                token = issue_access_token(
                    APP_KEY,
                    APP_SECRET,
                )
                stock = inquire_samsung_price(
                    token,
                    APP_KEY,
                    APP_SECRET,
                )

            price = int(stock.get("stck_prpr", "0"))
            change_rate = float(stock.get("prdy_ctrt", "0"))
            volume = int(stock.get("acml_vol", "0"))

            st.success("한국투자증권 API 연결에 성공했습니다.")

            st.metric(
                "삼성전자 현재가",
                f"{price:,}원",
                f"{change_rate:+.2f}%",
            )

            st.write(f"오늘 누적 거래량: {volume:,}주")

        except Exception as error:
            st.error("한국투자증권 API 연결에 실패했습니다.")
            st.code(str(error))
