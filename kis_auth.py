import time
import requests
import streamlit as st

# Streamlit Cloud의 Secrets에서 키 값을 자동으로 가져옵니다.
def get_kis_access_token() -> str:
    """
    st.secrets의 API 키를 사용하되, 
    발급받은 Access Token은 메모리(st.session_state)에 23시간 동안 저장하여 
    새로고침 시 토큰 재발급 네트워크 호출을 완전히 차단합니다.
    """
    now = time.time()

    # 1. 이미 메모리에 토큰이 있고 유효기간이 남았다면 KIS 서버 호출 없이 즉시 반환
    if "kis_access_token" in st.session_state and "kis_token_expires_at" in st.session_state:
        if now < st.session_state["kis_token_expires_at"]:
            return st.session_state["kis_access_token"]

    # 2. Secrets 설정 확인
    try:
        # secrets.toml 설정 이름에 맞게 수정 가능 (예: st.secrets["KIS_APP_KEY"] 등)
        app_key = st.secrets["KIS_APP_KEY"]
        app_secret = st.secrets["KIS_APP_SECRET"]
    except KeyError as e:
        st.error(f"Streamlit Secrets에서 키를 찾을 수 없습니다: {e}")
        return ""

    # 3. 하루 1회만 실행되는 실제 KIS 토큰 발급 요청
    url = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
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
            raise ValueError(f"토큰 발급 응답 이상: {data}")

        # 23시간(82,800초) 동안 메모리에 토큰 보관
        st.session_state["kis_access_token"] = f"Bearer {token}"
        st.session_state["kis_token_expires_at"] = now + 82800

        return st.session_state["kis_access_token"]

    except Exception as e:
        st.error(f"[KIS API] 토큰 발급 중 오류 발생: {e}")
        return st.session_state.get("kis_access_token", "")


def get_kis_headers(tr_id: str) -> dict:
    """KIS API 요청용 공통 헤더 생성"""
    token = get_kis_access_token()
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": token,
        "appkey": st.secrets["KIS_APP_KEY"],
        "appsecret": st.secrets["KIS_APP_SECRET"],
        "tr_id": tr_id,
        "custtype": "P",
    }
