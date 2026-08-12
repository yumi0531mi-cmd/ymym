import time
import requests
import streamlit as st

# ==============================================================================
# 1. KIS API 토큰 메모리 캐싱 (하루 1회 제한 대응 & 새로고침 속도 극대화)
# ==============================================================================
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

def get_kis_access_token() -> str:
    """
    st.secrets에서 API 키를 읽어오되, 발급받은 Access Token을 
    st.session_state(메모리)에 23시간 동안 보관합니다.
    새로고침이 아무리 자주 발생해도 토큰 재발급 네트워크 호출을 100% 차단합니다.
    """
    now = time.time()

    # 이미 메모리에 토큰이 저장되어 있고, 만료되지 않았다면 즉시 반환 (0초 소요)
    if "kis_access_token" in st.session_state and "kis_token_expires_at" in st.session_state:
        if now < st.session_state["kis_token_expires_at"]:
            return st.session_state["kis_access_token"]

    # Secrets 키 이름 유연하게 탐색 (APP_KEY, KIS_APP_KEY 모두 지원)
    app_key = st.secrets.get("KIS_APP_KEY") or st.secrets.get("APP_KEY")
    app_secret = st.secrets.get("KIS_APP_SECRET") or st.secrets.get("APP_SECRET")

    if not app_key or not app_secret:
        st.error("Streamlit Secrets에서 APP_KEY 또는 KIS_APP_KEY를 찾을 수 없습니다.")
        return ""

    # 하루 딱 1번만 실행되는 KIS OAuth 토큰 발급 API 호출
    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    try:
        res = requests.post(url, headers=headers, json=body, timeout=5)
        res.raise_for_status()
        data = res.json()

        token = data.get("access_token")
        if not token:
            raise ValueError(f"토큰 발급 실패: {data}")

        # 23시간(82,800초) 동안 메모리에 토큰 보관
        st.session_state["kis_access_token"] = f"Bearer {token}"
        st.session_state["kis_token_expires_at"] = now + 82800

        return st.session_state["kis_access_token"]

    except Exception as e:
        st.error(f"[KIS API] 토큰 발급 중 오류 발생: {e}")
        return st.session_state.get("kis_access_token", "")


def get_kis_headers(tr_id: str) -> dict:
    """KIS API 요청에 필요한 공통 헤더 생성"""
    token = get_kis_access_token()
    app_key = st.secrets.get("KIS_APP_KEY") or st.secrets.get("APP_KEY", "")
    app_secret = st.secrets.get("KIS_APP_SECRET") or st.secrets.get("APP_SECRET", "")

    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": token,
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


# ==============================================================================
# 2. KIS API 데이터 조회 함수 (초단타 전용)
# ==============================================================================
@st.cache_data(ttl=2, show_spinner=False)
def fetch_kis_current_price(ticker: str) -> dict:
    """단일 종목 주가/체결가 조회 (2초 캐싱으로 딜레이 차단)"""
    headers = get_kis_headers(tr_id="FHKST01010100")
    if not headers.get("authorization"):
        return {}

    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": ticker
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=2)
        if res.status_code == 200:
            return res.json().get("output", {})
    except Exception:
        pass
    return {}


# ==============================================================================
# 3. Streamlit 대시보드 UI (scalp_app.py 메인 화면)
# ==============================================================================
st.set_page_config(page_title="Scalp Trading Dashboard", layout="wide")

st.title("⚡ 스캘핑 초단타 대시보드")

# 입력 필드
ticker_input = st.text_input("종목코드 입력 (예: Samsung 005930)", value="005930")

if ticker_input:
    data = fetch_kis_current_price(ticker_input.strip())
    
    if data:
        price = data.get("stck_prpr", "0")
        diff = data.get("prdy_vrss", "0")
        rate = data.get("prdy_ctrt", "0")
        volume = data.get("acml_vol", "0")

        col1, col2, col3 = st.columns(3)
        col1.metric("현재가", f"{int(price):,} 원", f"{rate}%")
        col2.metric("전일대비", f"{int(diff):,} 원")
        col3.metric("누적거래량", f"{int(volume):,} 주")
    else:
        st.warning("주가 데이터를 불러오는 중입니다...")
