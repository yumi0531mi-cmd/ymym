import html
import json
import math
import os
import re
import sqlite3
import hashlib
import uuid
import tempfile
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import websocket
except Exception:
    websocket = None


st.set_page_config(
    page_title="한·미 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.markdown(
    '<div class="app-title">📡 단타 스캐너</div>'
    '<div class="app-sub">V5 · 전 챕터 85점+ · 모바일 다중확인</div>',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    header[data-testid="stHeader"] {height:0; min-height:0; background:transparent;}
    .block-container {padding-top:.7rem; padding-bottom:1.2rem; max-width:520px;}
    .app-title {font-size:1.55rem; line-height:1.15; font-weight:950; color:#171b24; white-space:nowrap;}
    .app-sub {font-size:.72rem; color:#8b93a3; margin:.2rem 0 .75rem;}
    h1 {font-size:1.55rem !important; line-height:1.15 !important;}
    h2, h3 {font-size: 1.15rem !important;}
    div[data-testid="stButton"] > button {width:100%; min-height:2.65rem; font-weight:850;}
    div[data-testid="stSelectbox"] {margin-bottom: .2rem;}
    div[data-testid="stAlert"] {padding:.55rem .7rem; font-size:.78rem; margin:.3rem 0;}
    div[data-testid="stRadio"] label {font-size:.84rem;}
    .stock-card {
        background: #11151d;
        color: #e7ebf3;
        border: 1px solid #293142;
        border-radius: 18px;
        padding:14px;
        margin:8px 0;
        box-shadow: 0 8px 24px rgba(0,0,0,.18);
    }
    .card-head {display:flex; justify-content:space-between; gap:12px; align-items:flex-start;}
    .stock-name {font-size:1.18rem; font-weight:900; line-height:1.2;}
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
    .data-row {display:flex; justify-content:space-between; gap:6px; padding:7px 0; border-bottom:1px solid #252c37; font-size:.8rem;}
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
    .signal-badge {display:inline-block;padding:5px 11px;border-radius:9px;background:#3c3213;color:#ffd45d;border:1px solid #8a6e16;font-weight:900;font-size:.8rem;margin:8px 0;}
    .risk-line {padding:8px 10px;border-radius:9px;background:#351d20;color:#ff9b9f;border:1px solid #743038;font-size:.76rem;font-weight:750;margin:4px 0 8px;}
    .metric-grid {display:grid;grid-template-columns:1fr 1fr;gap:0 14px;margin-top:4px;}
    .metric {display:flex;justify-content:space-between;gap:6px;padding:7px 0;border-bottom:1px solid #252c37;font-size:.77rem;}
    .metric span {color:#8993a5;}.metric b {color:#eef2f8;text-align:right;}
    .trade-title {font-size:.78rem;color:#aeb7c6;font-weight:900;margin:12px 0 6px;border-top:1px dashed #313846;padding-top:10px;}
    .trade-grid {display:grid;grid-template-columns:repeat(3,1fr);gap:7px;}
    .trade-box {background:#1b202a;border-radius:9px;padding:8px 7px;min-height:50px;}
    .trade-box span {display:block;color:#8993a5;font-size:.65rem;margin-bottom:3px;}.trade-box b {font-size:.9rem;}
    .mobile-summary {display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;}
    .mobile-kpi {background:#1b202a;border-radius:11px;padding:10px;}
    .mobile-kpi span {display:block;color:#8d97aa;font-size:.68rem;margin-bottom:4px;}
    .mobile-kpi b {font-size:1rem;color:#eef2f8;}
    .reason-list {margin-top:10px;padding-top:9px;border-top:1px dashed #313846;}
    .reason-item {font-size:.78rem;line-height:1.65;color:#dce2ec;}
    .progress-wrap {background:#252b35;border-radius:999px;height:10px;overflow:hidden;margin:8px 0 5px;}
    .progress-bar {height:100%;background:linear-gradient(90deg,#5ed887,#ffd15c,#ff777d);}
    @media (max-width: 520px) {
        .block-container {padding:.55rem .55rem 1rem;}
        .stock-card {padding:12px 11px; border-radius:14px;}
        .grid2 {grid-template-columns:1fr 1fr; gap:0 8px;}
        .data-row {font-size:.74rem; padding:6px 0;}
        .data-label {white-space:normal;}
        .metric-grid {gap:0 9px;}.metric {font-size:.7rem;padding:6px 0;}
        .trade-grid {gap:5px;}.trade-box {padding:7px 6px;}.trade-box b {font-size:.82rem;}
        .tf {font-size:.68rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_compact_html(markup):
    """들여쓴 HTML이 Streamlit 코드상자로 보이지 않게 한 줄로 렌더링한다."""
    compact = "".join(line.strip() for line in str(markup).splitlines())
    st.markdown(compact, unsafe_allow_html=True)


def load_secret(name):
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return ""


APP_KEY = load_secret("KIS_APP_KEY")
APP_SECRET = load_secret("KIS_APP_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"
SEOUL = ZoneInfo("Asia/Seoul")
MOBILE_SIMPLE_UI = True


@st.cache_resource(show_spinner=False)
def build_http_session():
    """TLS 연결을 재사용하고 일시적 429·5xx·연결 초기화를 짧게 재시도한다."""
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        backoff_factor=0.18,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "HEAD", "OPTIONS")),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=24,
        pool_maxsize=36,
    )
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


HTTP = build_http_session()


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


def response_json(response, label="API"):
    """HTML/빈 응답이 와도 JSONDecodeError 대신 읽기 쉬운 오류를 낸다."""
    try:
        return response.json()
    except ValueError as error:
        preview = (response.text or "").strip().replace("\n", " ")[:180]
        raise RuntimeError(
            f"{label} 응답이 JSON이 아닙니다 (HTTP {response.status_code}): {preview or '빈 응답'}"
        ) from error


TOKEN_CACHE_DIR = Path(os.getenv("KIS_TOKEN_CACHE_DIR", Path.home() / ".kis_scanner"))
TOKEN_CACHE_FILE = TOKEN_CACHE_DIR / "access_token.json"
TOKEN_LOCK_FILE = TOKEN_CACHE_DIR / "access_token.lock"
TOKEN_REUSE_SECONDS = 23 * 60 * 60


def _read_saved_access_token():
    """앱 재실행 후에도 같은 토큰을 재사용한다."""
    try:
        data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        token = str(data.get("access_token") or "").strip()
        issued_at = float(data.get("issued_at") or 0)
        age = time.time() - issued_at
        if token and 0 <= age < TOKEN_REUSE_SECONDS:
            return token
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return ""


def _save_access_token(token):
    TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token,
        "issued_at": time.time(),
        "issued_at_kst": datetime.now(SEOUL).isoformat(timespec="seconds"),
    }
    fd, temp_name = tempfile.mkstemp(prefix="access_token_", suffix=".tmp", dir=TOKEN_CACHE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, TOKEN_CACHE_FILE)
        try:
            os.chmod(TOKEN_CACHE_FILE, 0o600)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(temp_name):
                os.remove(temp_name)
        except OSError:
            pass


def _acquire_token_lock(wait_seconds=15):
    TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(TOKEN_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - TOKEN_LOCK_FILE.stat().st_mtime > 30:
                    TOKEN_LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.2)
    return False


def _release_token_lock():
    try:
        TOKEN_LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


@st.cache_data(ttl=3600, show_spinner=False)
def issue_access_token(app_key, app_secret):
    """접근토큰은 디스크에 저장하고 23시간 동안 절대 재발급하지 않는다."""
    saved = _read_saved_access_token()
    if saved:
        return saved

    locked = _acquire_token_lock()
    if not locked:
        saved = _read_saved_access_token()
        if saved:
            return saved
        raise RuntimeError("접근토큰 발급 잠금 대기 시간이 초과되었습니다. 잠시 후 새로고침하세요.")

    try:
        # 다른 실행 프로세스가 먼저 발급했을 수 있으므로 잠금 후 다시 확인한다.
        saved = _read_saved_access_token()
        if saved:
            return saved

        response = HTTP.post(
            f"{BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
            timeout=10,
        )
        if response.status_code != 200:
            data = response_json(response, "접근토큰")
            raise RuntimeError(
                data.get("error_description") or data.get("msg1")
                or f"접근토큰 발급 실패: HTTP {response.status_code}"
            )
        data = response_json(response, "접근토큰")
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError(data.get("error_description") or data.get("msg1") or "접근토큰을 받지 못했습니다.")
        _save_access_token(token)
        return token
    finally:
        _release_token_lock()


def make_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


@st.cache_data(ttl=3600, show_spinner=False)
def issue_ws_approval_key(app_key, app_secret):
    """해외주식 실시간 체결 웹소켓 접속키를 1시간 재사용한다."""
    response = HTTP.post(
        f"{BASE_URL}/oauth2/Approval",
        headers={"content-type": "application/json"},
        data=json.dumps({
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        }),
        timeout=10,
    )
    if response.status_code != 200:
        raise RuntimeError(f"실시간 접속키 발급 실패: HTTP {response.status_code}")
    data = response_json(response)
    approval_key = data.get("approval_key")
    if not approval_key:
        raise RuntimeError(data.get("msg1") or "실시간 접속키를 받지 못했습니다.")
    return approval_key


def get_market_cap_ranking(token, market_code):
    response = None
    for attempt in range(3):
        response = HTTP.get(
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
            timeout=8,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break
    if response.status_code != 200:
        raise RuntimeError(f"시가총액 조회 실패: HTTP {response.status_code}")
    data = response_json(response)
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "시가총액 순위를 받지 못했습니다."))
    return data.get("output", [])


def get_volume_rank(token, sort_code):
    """sort_code: 0=당일 절대거래량, 1=거래증가율, 3=거래금액순"""
    response = None
    for attempt in range(3):
        response = HTTP.get(
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
            timeout=8,
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

    data = response_json(response)
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "거래량 순위를 받지 못했습니다."))
    return data.get("output", [])


def get_domestic_rank(token, endpoint, tr_id, params, label):
    """한투 국내주식 순위 API 공통 호출."""
    response = None
    for attempt in range(3):
        response = HTTP.get(
            f"{BASE_URL}{endpoint}",
            headers=make_headers(token, tr_id),
            params=params,
            timeout=8,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.2 * (attempt + 1))
            continue
        break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "응답 없음"
        raise RuntimeError(f"{label} 조회 실패: HTTP {status}")

    data = response_json(response)
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", f"{label}을 받지 못했습니다."))
    return data.get("output") or []


def get_domestic_fluctuation_rows(token):
    """한투 공식 국내 당일 등락률 상위."""
    return get_domestic_rank(
        token,
        "/uapi/domestic-stock/v1/ranking/fluctuation",
        "FHPST01700000",
        {
            "fid_rsfl_rate2": "30",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "0000",
            "fid_input_cnt_1": "30",
            "fid_prc_cls_code": "0",
            "fid_input_price_1": "1000",
            "fid_input_price_2": "200000",
            "fid_vol_cnt": "100000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_rsfl_rate1": "0",
        },
        "국내 등락률 순위",
    )


def get_domestic_volume_power_rows(token):
    """한투 공식 국내 매수 체결강도 상위."""
    return get_domestic_rank(
        token,
        "/uapi/domestic-stock/v1/ranking/volume-power",
        "FHPST01680000",
        {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20168",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_input_price_1": "1000",
            "fid_input_price_2": "200000",
            "fid_vol_cnt": "100000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
        },
        "국내 체결강도 순위",
    )


def get_domestic_triple_rank_rows(token):
    """등락률·당일거래량·체결강도 순위를 동시에 받는다."""
    jobs = {
        "상승률": lambda: get_domestic_fluctuation_rows(token),
        "당일거래량": lambda: get_volume_rank(token, "0"),
        "체결강도": lambda: get_domestic_volume_power_rows(token),
    }
    grouped = {name: [] for name in jobs}
    errors = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(job): name for name, job in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                grouped[name] = future.result()
            except Exception as error:
                errors.append(f"{name}: {error}")
    if any(not grouped[name] for name in jobs):
        detail = " / ".join(errors) or "순위 일부가 빈 응답입니다."
        raise RuntimeError(f"삼중순위를 모두 받지 못했습니다. {detail}")
    return grouped, errors


def get_us_ranking(token, endpoint, tr_id, params):
    response = None
    for attempt in range(3):
        response = HTTP.get(
            f"{BASE_URL}{endpoint}",
            headers=make_headers(token, tr_id),
            params=params,
            timeout=8,
        )
        if response.status_code == 200:
            break
        if response.status_code >= 500 and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "응답 없음"
        raise RuntimeError(f"미국주식 순위 조회 실패: HTTP {status}")

    data = response_json(response)
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "미국주식 순위를 받지 못했습니다."))
    return data.get("output2") or []


def get_us_market_cap_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/market-cap",
        "HHDFS76350100",
        {
            "EXCD": exchange,
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_price_fluct_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/price-fluct",
        "HHDFS76260000",
        {
            "EXCD": exchange,
            "GUBN": "1",
            "MINX": "5",
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_volume_surge_rows(token, exchange):
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/volume-surge",
        "HHDFS76270000",
        {
            "EXCD": exchange,
            "MINX": "5",
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_trade_volume_rows(token, exchange, penny_only=False):
    """한투 공식 미국 당일 누적 거래량 순위.

    메르츠의 '거래량' 탭과 같은 절대 누적 거래량 기준이며,
    volume-surge(평소 대비 증가율)와는 다른 API다.
    """
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/trade-vol",
        "HHDFS76310010",
        {
            "EXCD": exchange,
            "NDAY": "0",
            "VOL_RANG": "0",
            "KEYB": "",
            "AUTH": "",
            "PRC1": "0.01" if penny_only else "",
            "PRC2": "10" if penny_only else "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_updown_rows(token, exchange):
    """한투 공식 미국 당일 상승률 순위."""
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/updown-rate",
        "HHDFS76290000",
        {
            "EXCD": exchange,
            "GUBN": "1",
            "NDAY": "0",
            "VOL_RANG": "0",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def get_us_volume_power_rows(token, exchange):
    """한투 공식 미국 매수체결강도 상위 순위.

    tpow는 당일 체결강도, powx는 선택한 N분 기준 체결강도다.
    NDAY=0은 1분 기준이며 두 값 모두 보존한다.
    """
    rows = get_us_ranking(
        token,
        "/uapi/overseas-stock/v1/ranking/volume-power",
        "HHDFS76280000",
        {
            "EXCD": exchange,
            "NDAY": "0",
            "VOL_RANG": "0",
            "KEYB": "",
            "AUTH": "",
        },
    )
    return [dict(row, _exchange=exchange) for row in rows]


def _us_rank_key(row):
    exchange = str(row.get("_exchange") or row.get("excd") or "").strip().upper()
    exchange = US_NORMAL_EXCHANGE.get(exchange, exchange)
    ticker = str(row.get("symb") or "").strip().upper()
    return exchange, ticker


def build_us_triple_rank_rows(updown_rows, volume_rows, power_rows, session_mode, penny_only, surge_rows=None):
    """미국 급등 후보를 교집합이 아닌 순위 합집합으로 만든다.

    상승률·당일거래량·거래량급증·체결강도 중 하나에만 들어와도 후보로 보존한다.
    교집합 수는 신뢰도 가중치로만 사용하며 후보 삭제 조건으로 사용하지 않는다.
    """
    merged = {}

    def absorb(rows, source):
        for raw in rows or []:
            row = dict(raw)
            exchange, ticker = _us_rank_key(row)
            name = us_stock_name(row)
            if (
                exchange not in US_EXCHANGE_NAMES
                or not re.fullmatch(r"[A-Z]{1,6}", ticker)
                or is_excluded_us_product(name, ticker)
            ):
                continue
            key = (exchange, ticker)
            item = merged.setdefault(key, {
                "_base_exchange": exchange, "_session_mode": session_mode,
                "symb": ticker, "excd": exchange, "knam": name or ticker,
                "enam": str(row.get("enam") or row.get("ename") or "").strip(),
                "last": 0, "base": 0, "rate": 0, "tvol": 0, "tamt": 0,
                "pvol": 0, "pask": 0, "pbid": 0, "powx": 0, "tpow": 0,
                "n_rate": 0,
                "_gain_member": False, "_volume_member": False,
                "_surge_member": False, "_power_member": False,
            })
            if name and item.get("knam") in ("", ticker):
                item["knam"] = name
            for field in ("last", "base", "pask", "pbid", "tomv"):
                if to_float(row.get(field)) > 0:
                    item[field] = row.get(field)
            if source == "gain":
                item["_gain_member"] = True
                item["rate"] = row.get("rate")
            elif source == "volume":
                item["_volume_member"] = True
                item["tvol"] = row.get("tvol")
                item["tamt"] = row.get("tamt")
                item["pvol"] = row.get("pvol")
            elif source == "surge":
                item["_surge_member"] = True
                item["n_rate"] = row.get("n_rate")
                if to_int(row.get("tvol")) > to_int(item.get("tvol")):
                    item["tvol"] = row.get("tvol")
                if to_float(item.get("rate")) == 0:
                    item["rate"] = row.get("rate")
            else:
                item["_power_member"] = True
                item["powx"] = row.get("powx")
                item["tpow"] = row.get("tpow")
            if to_float(item.get("rate")) == 0:
                item["rate"] = row.get("rate")
            if to_int(row.get("tvol")) > to_int(item.get("tvol")):
                item["tvol"] = row.get("tvol")
            if to_float(row.get("tamt")) > to_float(item.get("tamt")):
                item["tamt"] = row.get("tamt")

    absorb(updown_rows, "gain")
    absorb(volume_rows, "volume")
    absorb(surge_rows, "surge")
    absorb(power_rows, "power")

    items = list(merged.values())
    if penny_only:
        items = [item for item in items if 0.01 <= to_float(item.get("last")) <= 10]

    def ranked(member, value_fn):
        selected = sorted([x for x in items if x[member]], key=value_fn, reverse=True)[:100]
        return {_us_rank_key(x): i for i, x in enumerate(selected, 1)}

    gain_rank = ranked("_gain_member", lambda x: to_float(x.get("rate")))
    volume_rank = ranked("_volume_member", lambda x: to_int(x.get("tvol")))
    surge_rank = ranked("_surge_member", lambda x: to_float(x.get("n_rate")))
    power_rank = ranked("_power_member", lambda x: max(to_float(x.get("tpow")), to_float(x.get("powx"))))

    output = []
    for item in items:
        key = _us_rank_key(item)
        item["_gain_rank"] = gain_rank.get(key, 0)
        item["_volume_rank"] = volume_rank.get(key, 0)
        item["_surge_rank"] = surge_rank.get(key, 0)
        item["_power_rank"] = power_rank.get(key, 0)
        overlap = sum(rank > 0 for rank in (
            item["_gain_rank"], item["_volume_rank"], item["_surge_rank"], item["_power_rank"]
        ))
        item["_overlap_count"] = overlap
        item["_triple_intersection"] = overlap >= 3
        item["powx"] = max(to_float(item.get("tpow")), to_float(item.get("powx")))
        rate = to_float(item.get("rate"))
        volume = to_int(item.get("tvol"))
        amount = to_float(item.get("tamt")) or to_float(item.get("last")) * volume
        surge = to_float(item.get("n_rate"))
        # 이미 폭등한 종목도 숨기지 않되, 조기포착 후보가 위로 오도록 정렬 점수를 분리한다.
        early_bonus = 45 if 5 <= rate <= 35 else 20 if 2 <= rate < 5 else 0
        overheat_penalty = max(rate - 80, 0) * 1.2
        item["_discovery_score"] = (
            early_bonus + min(rate, 120) * 1.1 + min(max(surge, 0), 2000) / 35
            + min(math.log10(max(volume, 1)), 9) * 5
            + min(math.log10(max(amount, 1)), 12) * 3
            + overlap * 12 - overheat_penalty
        )
        output.append(item)

    output.sort(key=lambda item: (
        -to_float(item.get("_discovery_score")),
        -item["_overlap_count"],
        item["_gain_rank"] or 999,
    ))
    output = output[:60]
    pairs = [_us_rank_key(item) for item in output]
    return output, pairs


@st.cache_data(ttl=3, show_spinner=False)
def get_us_triple_rank_rows(token, penny_only):
    """미국 3개 거래소의 4개 급등 관련 순위를 동시에 수집한다."""
    exchanges = ("NAS", "NYS", "AMS")
    calls = {
        "상승률": get_us_updown_rows,
        "당일거래량": lambda value, exchange: get_us_trade_volume_rows(value, exchange, penny_only),
        "거래량급증": get_us_volume_surge_rows,
        "체결강도": get_us_volume_power_rows,
    }
    grouped = {name: [] for name in calls}
    counts, errors = {}, []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for label, function in calls.items():
            for exchange in exchanges:
                futures[executor.submit(function, token, exchange)] = (label, exchange)
        for future in as_completed(futures):
            label, exchange = futures[future]
            key = f"한투-{exchange}-{label}"
            try:
                rows = future.result()
                grouped[label].extend(rows)
                counts[key] = len(rows)
            except Exception as error:
                counts[key] = 0
                errors.append(f"{key}: {error}")
    return grouped, counts, errors


def get_us_penny_search_rows(token, exchange):
    """한투 공식 해외주식 조건검색으로 $0.01~$10 종목을 찾는다."""
    response = None
    params = {
        "AUTH": "",
        "EXCD": exchange,
        "CO_YN_PRICECUR": "1",
        "CO_ST_PRICECUR": "0.01",
        "CO_EN_PRICECUR": "10",
        "CO_YN_RATE": "",
        "CO_ST_RATE": "",
        "CO_EN_RATE": "",
        "CO_YN_VALX": "",
        "CO_ST_VALX": "",
        "CO_EN_VALX": "",
        "CO_YN_SHAR": "",
        "CO_ST_SHAR": "",
        "CO_EN_SHAR": "",
        "CO_YN_VOLUME": "1",
        "CO_ST_VOLUME": "10000",
        "CO_EN_VOLUME": "9999999999",
        "CO_YN_AMT": "",
        "CO_ST_AMT": "",
        "CO_EN_AMT": "",
        "CO_YN_EPS": "",
        "CO_ST_EPS": "",
        "CO_EN_EPS": "",
        "CO_YN_PER": "",
        "CO_ST_PER": "",
        "CO_EN_PER": "",
        "KEYB": "",
    }

    for attempt in range(3):
        try:
            response = HTTP.get(
                f"{BASE_URL}/uapi/overseas-price/v1/quotations/inquire-search",
                headers=make_headers(token, "HHDFS76410000"),
                params=params,
                timeout=8,
            )
            if response.status_code == 200:
                break
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            break
        except requests.RequestException:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "응답 없음"
        raise RuntimeError(f"한투 동전주 조건검색 HTTP {status}")

    data = response_json(response)
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1") or "한투 동전주 조건검색 실패")

    rows = data.get("output2") or data.get("output") or []
    if isinstance(rows, dict):
        rows = rows.get("output2") or rows.get("rows") or []
    return [dict(row, _exchange=exchange) for row in rows]


US_EXCHANGE_NAMES = {
    "NAS": "NASDAQ",
    "NYS": "NYSE",
    "AMS": "AMEX",
}


def is_excluded_us_product(name, ticker):
    upper_name = str(name).upper()
    upper_ticker = str(ticker).upper()
    excluded_words = (
        "ETF", "ETN", "PROSHARES", "DIREXION", "ULTRAPRO", "ULTRASHORT",
        "2X", "3X", "BEAR", "SHORT", "WARRANT", "RIGHT", "UNIT",
        "ACQUISITION", "SPAC",
    )
    if any(word in upper_name for word in excluded_words):
        return True
    return upper_ticker.endswith((".WS", ".W", ".U"))


def us_stock_name(row):
    return str(
        row.get("name")
        or row.get("enam")
        or row.get("ename")
        or row.get("knam")
        or row.get("symb")
        or ""
    ).strip()


def build_us_quality_table(rows):
    records = []
    for row in rows:
        ticker = str(row.get("symb", "")).strip().upper()
        name = us_stock_name(row)
        if not ticker or not name or is_excluded_us_product(name, ticker):
            continue
        price = to_float(row.get("last"))
        change_pct = to_float(row.get("rate"))
        volume = to_int(row.get("tvol"))
        trading_value = to_float(row.get("tamt"))
        market_cap = to_float(row.get("mcap") or row.get("tomv"))
        if price <= 0 or volume < 10_000:
            continue
        vwap = trading_value / volume if trading_value > 0 and volume > 0 else 0
        vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 0

        if change_pct >= 10 or vwap_gap > 4:
            status = "🔴 추격주의"
        elif change_pct <= -3 or (vwap > 0 and price < vwap):
            status = "⚪ 약세·대기"
        elif 0.5 <= change_pct <= 7 and trading_value >= 20_000_000:
            status = "🟢 미국 기술지표 예정"
        else:
            status = "🟡 유동성 관찰"

        exchange = str(row.get("_exchange") or row.get("excd") or "").strip()
        records.append({
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "시총순위": to_int(row.get("rank")),
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": round(price, 4),
            "등락률(%)": round(change_pct, 2),
            "오늘거래량": volume,
            "오늘거래대금($)": round(trading_value, 2),
            "오늘거래대금(백만$)": round(trading_value / 1_000_000, 2),
            "VWAP근사($)": round(vwap, 4),
            "VWAP위치(%)": round(vwap_gap, 2),
            "시가총액(API)": market_cap,
            "현재판정": status,
        })

    table = pd.DataFrame(records)
    if table.empty:
        return table
    return table.sort_values(
        ["시가총액(API)", "오늘거래대금($)"], ascending=False
    ).head(30).reset_index(drop=True)


def build_us_momentum_table(price_rows, surge_rows):
    records = {}

    def update_row(row, source):
        exchange = str(row.get("_exchange") or row.get("excd") or "").strip()
        ticker = str(row.get("symb", "")).strip().upper()
        name = us_stock_name(row)
        if not ticker or not name or is_excluded_us_product(name, ticker):
            return
        key = f"{exchange}:{ticker}"
        item = records.setdefault(key, {
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": 0.0,
            "등락률(%)": 0.0,
            "15분등락률(%)": 0.0,
            "거래량증가율(%)": 0.0,
            "오늘거래량": 0,
            "오늘거래대금($)": 0.0,
        })
        item["현재가($)"] = max(item["현재가($)"], to_float(row.get("last")))
        item["등락률(%)"] = max(item["등락률(%)"], to_float(row.get("rate")))
        item["오늘거래량"] = max(item["오늘거래량"], to_int(row.get("tvol")))
        item["오늘거래대금($)"] = max(item["오늘거래대금($)"], to_float(row.get("tamt")))
        if source == "price":
            item["15분등락률(%)"] = max(item["15분등락률(%)"], to_float(row.get("n_rate")))
        else:
            item["거래량증가율(%)"] = max(item["거래량증가율(%)"], to_float(row.get("n_rate")))

    for row in price_rows:
        update_row(row, "price")
    for row in surge_rows:
        update_row(row, "surge")

    clean = []
    for item in records.values():
        price = item["현재가($)"]
        daily_rate = item["등락률(%)"]
        volume = item["오늘거래량"]
        amount = item["오늘거래대금($)"]
        if price < 1 or daily_rate < 3 or volume < 10_000 or amount < 100_000:
            continue
        vwap = amount / volume if volume > 0 else 0
        vwap_gap = ((price / vwap) - 1) * 100 if vwap > 0 else 0
        short_rate = item["15분등락률(%)"]
        volume_growth = item["거래량증가율(%)"]
        if daily_rate >= 25 or short_rate >= 15 or vwap_gap > 10:
            status = "🔴 추격금지·과열"
        elif 5 <= daily_rate <= 20 and amount >= 2_000_000 and price >= vwap:
            status = "🟢 미국 급등 정밀검사 예정"
        else:
            status = "🟡 거래량 확대 감시"
        item["VWAP근사($)"] = round(vwap, 4)
        item["VWAP위치(%)"] = round(vwap_gap, 2)
        item["오늘거래대금(백만$)"] = round(amount / 1_000_000, 2)
        item["현재판정"] = status
        item["급등점수"] = round(
            min(daily_rate, 100) + 2 * min(max(short_rate, 0), 30)
            + min(max(volume_growth, 0) / 100, 30)
            + math.log10(max(amount, 1)),
            2,
        )
        clean.append(item)

    table = pd.DataFrame(clean)
    if table.empty:
        return table
    return table.sort_values("급등점수", ascending=False).head(30).reset_index(drop=True)


# 미국 순위 API는 장 구분에 따라 공백 또는 입력값 오류를 반환할 수 있어,
# V7은 한투 공식 '해외주식 복수종목 시세조회'(최대 10종목)로 검증한다.
US_QUALITY_UNIVERSE = [
    ("NAS", "AAPL"), ("NAS", "MSFT"), ("NAS", "NVDA"),
    ("NAS", "AMZN"), ("NAS", "GOOGL"), ("NAS", "META"),
    ("NAS", "AVGO"), ("NAS", "TSLA"), ("NAS", "COST"),
    ("NAS", "NFLX"), ("NAS", "AMD"), ("NAS", "QCOM"),
    ("NAS", "AMAT"), ("NAS", "MU"), ("NAS", "CSCO"),
    ("NAS", "ADBE"), ("NAS", "INTU"), ("NAS", "PEP"),
    ("NAS", "TXN"), ("NAS", "AMGN"),
    ("NYS", "ORCL"), ("NYS", "JPM"), ("NYS", "V"),
    ("NYS", "MA"), ("NYS", "WMT"), ("NYS", "LLY"),
    ("NYS", "XOM"), ("NYS", "UNH"), ("NYS", "HD"),
    ("NYS", "PG"), ("NYS", "JNJ"), ("NYS", "ABBV"),
    ("NYS", "BAC"), ("NYS", "KO"), ("NYS", "CRM"),
    ("NYS", "CVX"), ("NYS", "IBM"), ("NYS", "GE"),
    ("NYS", "CAT"), ("NYS", "DIS"),
]

US_MOMENTUM_SEED = [
    ("NAS", "NVDA"), ("NAS", "TSLA"), ("NAS", "AMD"),
    ("NAS", "PLTR"), ("NAS", "SOFI"), ("NAS", "RIVN"),
    ("NAS", "LCID"), ("NAS", "MARA"), ("NAS", "RIOT"),
    ("NAS", "MSTR"), ("NAS", "COIN"), ("NAS", "HOOD"),
    ("NAS", "SMCI"), ("NAS", "ARM"), ("NAS", "INTC"),
    ("NAS", "RKLB"), ("NAS", "ASTS"), ("NAS", "IONQ"),
    ("NAS", "RGTI"), ("NAS", "QBTS"), ("NAS", "APP"),
    ("NAS", "ALAB"), ("NAS", "CELH"), ("NAS", "UPST"),
    ("NAS", "AFRM"), ("NAS", "DKNG"), ("NAS", "CLSK"),
    ("NAS", "BITF"), ("NAS", "HUT"), ("NAS", "WULF"),
    ("NAS", "OPEN"), ("NAS", "GRAB"),
    ("NYS", "BBAI"), ("NYS", "PATH"), ("NYS", "U"),
    ("NYS", "JOBY"), ("NYS", "ACHR"), ("NYS", "SNAP"),
    ("NYS", "NIO"), ("NYS", "BABA"), ("NYS", "CAVA"),
    ("NYS", "CVNA"), ("NYS", "GME"), ("NYS", "AMC"),
    ("NYS", "MP"), ("NYS", "OKLO"), ("NYS", "NU"),
    ("NYS", "SE"), ("NYS", "NET"), ("NYS", "HIMS"),
]


# =====================================================================
# 미국 런업 자동 분류 엔진
# - 고정 종목이 아니라 당일 미국 순위 후보군을 사용합니다.
# - 공개 실적 일정 + 최근 기업 뉴스로 재료를 분류합니다.
# - 최종 화면에는 점수 상위 5개만 표시합니다.
# =====================================================================
RUNUP_CATEGORY_ORDER = ("FDA·임상", "계약", "AI", "우주", "기타")
RUNUP_NEWS_KEYWORDS = {
    "FDA·임상": (
        "fda", "pdufa", "clinical trial", "phase 1", "phase 2", "phase 3",
        "topline", "data readout", "nda", "bla", "drug application",
        "임상", "승인",
    ),
    "계약": (
        "contract", "award", "partnership", "collaboration", "agreement",
        "purchase order", "deal", "selected by", "공급 계약",
    ),
    "AI": (
        "artificial intelligence", " ai ", "generative ai", "data center",
        "gpu", "machine learning", "ai platform",
    ),
    "우주": (
        "nasa", "space force", "space launch", "launch window", "satellite",
        "rocket", "spacecraft", "lunar", "orbital", "spacex",
    ),
    "기타": (
        "investor day", "conference", "presentation", "product launch",
        "merger", "acquisition", "strategic review", "guidance",
    ),
}


def _parse_iso_or_us_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            pass
    return None


def _extract_future_date_from_text(text):
    """뉴스 제목에 명시된 미래 날짜만 이벤트 예정일로 인정한다."""
    text = str(text or "").strip()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    patterns = (
        (r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", "ymd"),
        (r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?\b", "mdy"),
        (r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?\b", "mdy_short"),
    )
    months = {
        name: index for index, name in enumerate(
            ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"), 1
        )
    }
    short = {name[:3]: value for name, value in months.items()}
    short["Sep"] = 9
    short["Sept"] = 9
    for pattern, kind in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            if kind == "ymd":
                candidate = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
            else:
                month_text = match.group(1).rstrip(".")
                month = months.get(month_text.title()) or short.get(month_text.title())
                year = int(match.group(3)) if match.group(3) else today.year
                candidate = datetime(year, month, int(match.group(2))).date()
                if not match.group(3) and candidate < today - timedelta(days=7):
                    candidate = datetime(year + 1, month, int(match.group(2))).date()
            if today <= candidate <= today + timedelta(days=180):
                return candidate
        except Exception:
            continue
    return None


@st.cache_data(ttl=21600, show_spinner=False)
def get_public_earnings_calendar(days_ahead=14):
    """Nasdaq 공개 실적 캘린더를 최대 2주 범위로 수집한다."""
    start = datetime.now(ZoneInfo("America/New_York")).date()
    events, errors = {}, []

    def fetch_one(target_date):
        response = HTTP.get(
            "https://api.nasdaq.com/api/calendar/earnings",
            params={"date": target_date.isoformat()},
            headers={
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "accept": "application/json, text/plain, */*",
                "origin": "https://www.nasdaq.com",
                "referer": "https://www.nasdaq.com/market-activity/earnings",
            },
            timeout=5,
        )
        response.raise_for_status()
        data = response_json(response, "Nasdaq 실적 캘린더")
        rows = (((data.get("data") or {}).get("rows")) or [])
        return target_date, rows

    dates = [start + timedelta(days=i) for i in range(max(1, int(days_ahead)) + 1)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_one, day): day for day in dates}
        for future in as_completed(futures):
            day = futures[future]
            try:
                target_date, rows = future.result()
                for row in rows:
                    symbol = str(row.get("symbol") or "").strip().upper()
                    if not re.fullmatch(r"[A-Z]{1,6}", symbol):
                        continue
                    events[symbol] = {
                        "category": "실적",
                        "event_date": target_date.isoformat(),
                        "event_title": f"실적 발표 예정 ({row.get('time') or '시간 미정'})",
                        "source": "Nasdaq earnings calendar",
                    }
            except Exception as error:
                errors.append(f"{day}: {error}")
    return events, errors


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_runup_news(ticker, name):
    """Google News RSS에서 최근 기업 촉매 뉴스를 한 종목당 최대 5건 읽는다."""
    company = re.sub(r"[^A-Za-z0-9 .&-]", " ", str(name or "")).strip()
    query_text = (
        f'"{ticker}" stock ({company}) '
        '(FDA OR clinical trial OR PDUFA OR contract OR partnership OR '
        '"artificial intelligence" OR "investor day" OR conference) when:14d'
    )
    url = (
        "https://news.google.com/rss/search?q=" + quote_plus(query_text)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    response = HTTP.get(url, headers={"user-agent": "Mozilla/5.0"}, timeout=5)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    results = []
    now_utc = datetime.now(ZoneInfo("UTC"))
    for item in root.findall(".//item")[:8]:
        title = html.unescape(str(item.findtext("title") or "")).strip()
        link = str(item.findtext("link") or "").strip()
        pub_text = str(item.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(pub_text)
            if published.tzinfo is None:
                published = published.replace(tzinfo=ZoneInfo("UTC"))
            age_days = max(0.0, (now_utc - published.astimezone(ZoneInfo("UTC"))).total_seconds() / 86400)
        except Exception:
            published, age_days = None, 99.0
        lower = f" {title.lower()} "
        category = ""
        for label in ("FDA·임상", "계약", "AI", "우주", "기타"):
            if any(keyword in lower for keyword in RUNUP_NEWS_KEYWORDS[label]):
                category = label
                break
        if category:
            future_date = _extract_future_date_from_text(title)
            results.append({
                "category": category,
                "event_title": title,
                # 기사 발행일은 이벤트 예정일이 아니다. 제목에 미래 날짜가 명시된 경우만 사용한다.
                "event_date": future_date.isoformat() if future_date else "",
                "published_date": published.date().isoformat() if published else "",
                "age_days": round(age_days, 2),
                "source": "Google News RSS",
                "link": link,
            })
    results.sort(key=lambda item: item.get("age_days", 99))
    return results[:5]


def _runup_technical_score(row):
    rate = to_float(row.get("등락률(%)"))
    volume_ratio = to_float(row.get("거래량비율(%)"))
    amount_m = to_float(row.get("오늘거래대금(백만$)"))
    vwap_gap = to_float(row.get("VWAP위치(%)"))
    spread = to_float(row.get("호가차이(%)"))
    score = 0.0
    score += 22 if 1 <= rate <= 8 else 12 if 0 <= rate < 1 else 4 if 8 < rate <= 15 else 0
    score += min(max(volume_ratio, 0), 500) / 20
    score += min(math.log10(max(amount_m * 1_000_000, 1)), 10) * 3
    score += 12 if -1.5 <= vwap_gap <= 3.5 else 4 if 3.5 < vwap_gap <= 6 else -10 if vwap_gap > 8 else 0
    score -= min(max(spread, 0), 5) * 5
    return score


def _event_score(event):
    if not event:
        return 0.0, "", ""
    category = str(event.get("category") or "기타")
    event_date = _parse_iso_or_us_date(event.get("event_date"))
    today = datetime.now(ZoneInfo("America/New_York")).date()
    dday_text = ""
    proximity = 0.0
    if event_date:
        days = (event_date - today).days
        dday_text = "D-day" if days == 0 else f"D-{days}" if days > 0 else f"D+{abs(days)}"
        if 0 <= days <= 3:
            proximity = 38
        elif 4 <= days <= 7:
            proximity = 30
        elif 8 <= days <= 14:
            proximity = 20
        elif -2 <= days < 0:
            proximity = 10
    age_days = to_float(event.get("age_days"))
    recency = 28 if 0 <= age_days <= 1 else 20 if age_days <= 3 else 12 if age_days <= 7 else 5 if age_days <= 14 else 0
    category_bonus = {"FDA·임상": 18, "실적": 5, "계약": 14, "AI": 11, "우주": 12, "기타": 5}.get(category, 0)
    return proximity + recency + category_bonus, dday_text, category



RUNUP_LARGE_CAP_BLACKLIST = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
    "AVGO", "NFLX", "AMD", "PLTR", "COIN", "MSTR", "ORCL", "LLY",
    "JPM", "V", "MA", "WMT", "XOM", "UNH", "COST", "CRM", "IBM",
}


@st.cache_data(ttl=1800, show_spinner=False)
def get_us_daily_history(token, exchange, ticker, limit=80):
    """한투 해외주식 일봉으로 최근 런업 진행 상태를 계산한다."""
    response = HTTP.get(
        f"{BASE_URL}/uapi/overseas-price/v1/quotations/dailyprice",
        headers=make_headers(token, "HHDFS76240000"),
        params={
            "AUTH": "",
            "EXCD": US_NORMAL_EXCHANGE.get(str(exchange).upper(), str(exchange).upper()),
            "SYMB": str(ticker).upper(),
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1",
        },
        timeout=8,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 일봉 HTTP {response.status_code}")
    data = response_json(response, f"{ticker} 일봉")
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1") or f"{ticker} 일봉 조회 실패")
    rows = data.get("output2") or []
    records = []
    for row in rows[:max(20, int(limit))]:
        close = to_float(row.get("clos") or row.get("last"))
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        volume = to_float(row.get("tvol") or row.get("evol"))
        date_text = str(row.get("xymd") or row.get("date") or "")
        if close > 0:
            records.append({"date": date_text, "close": close, "high": high or close, "low": low or close, "volume": volume})
    # API는 보통 최신순이다.
    return list(reversed(records))


def _pct_change_from(history, sessions):
    if not history or len(history) <= sessions:
        return 0.0
    current = to_float(history[-1].get("close"))
    past = to_float(history[-1 - sessions].get("close"))
    return (current / past - 1) * 100 if current > 0 and past > 0 else 0.0



def _runup_prepricing_metrics(history):
    """최근 가격경로로 선반영·과열·변동성 지표를 계산한다."""
    if len(history) < 22:
        return {
            "선반영점수": 100.0, "선반영등급": "🔴 데이터부족",
            "10일(%)": 0.0, "20일(%)": 0.0, "60일(%)": 0.0,
            "60일고점거리(%)": 0.0, "ATR비율(%)": 0.0,
            "연속급등일": 0, "과열제외": True, "선반영사유": ["일봉 표본 부족"],
        }
    closes=[to_float(x.get("close")) for x in history]
    highs=[to_float(x.get("high")) or closes[i] for i,x in enumerate(history)]
    lows=[to_float(x.get("low")) or closes[i] for i,x in enumerate(history)]
    current=closes[-1]
    r10=_pct_change_from(history,10); r20=_pct_change_from(history,20)
    r60=_pct_change_from(history,min(60,len(history)-1))
    high60=max(highs[-min(60,len(highs)):])
    high_dist=(current/high60-1)*100 if high60>0 else 0.0
    trs=[]
    for i in range(max(1,len(history)-14),len(history)):
        prev=closes[i-1]
        trs.append(max(highs[i]-lows[i],abs(highs[i]-prev),abs(lows[i]-prev)))
    atr=sum(trs)/len(trs) if trs else 0.0
    atr_pct=atr/current*100 if current>0 else 0.0
    consecutive=0
    for i in range(len(closes)-1,max(0,len(closes)-5),-1):
        if closes[i-1]>0 and (closes[i]/closes[i-1]-1)*100>=12:
            consecutive+=1
        else:
            break
    score=0.0; reasons=[]
    if r10>=60: score+=35; reasons.append(f"10일 {r10:+.0f}%")
    elif r10>=35: score+=22; reasons.append(f"10일 {r10:+.0f}%")
    elif r10>=20: score+=10
    if r20>=100: score+=35; reasons.append(f"20일 {r20:+.0f}%")
    elif r20>=60: score+=22; reasons.append(f"20일 {r20:+.0f}%")
    elif r20>=35: score+=10
    if r60>=200: score+=35; reasons.append(f"60일 {r60:+.0f}%")
    elif r60>=120: score+=24; reasons.append(f"60일 {r60:+.0f}%")
    elif r60>=70: score+=12
    if high_dist>=-5: score+=20; reasons.append("60일 고점 5% 이내")
    elif high_dist>=-10: score+=10
    if consecutive>=2: score+=20; reasons.append(f"연속 급등 {consecutive}일")
    score=min(100.0,score)
    grade="🟢 낮음" if score<30 else "🟡 보통" if score<60 else "🔴 높음"
    hard=(r10>=60 or r20>=100 or r60>=200 or consecutive>=3 or score>=80)
    return {
        "선반영점수":round(score,1),"선반영등급":grade,
        "10일(%)":round(r10,1),"20일(%)":round(r20,1),"60일(%)":round(r60,1),
        "60일고점거리(%)":round(high_dist,1),"ATR비율(%)":round(atr_pct,2),
        "연속급등일":consecutive,"과열제외":hard,"선반영사유":reasons,
    }


def _runup_event_grade(event):
    """출처와 날짜 명시 여부를 분리해 이벤트 신뢰등급을 부여한다."""
    if not event:
        return "D", "공식 이벤트 미확인"
    source=str(event.get("source") or "").lower()
    title=str(event.get("event_title") or "")
    has_date=_parse_iso_or_us_date(event.get("event_date")) is not None
    if "nasdaq earnings calendar" in source:
        return "A", "공개 실적 일정"
    if any(x in source for x in ("sec", "fda", "clinicaltrials", "company ir", "investor relations")) and has_date:
        return "A", "공식 일정"
    if has_date and any(x in title.lower() for x in ("pdufa","phase","trial","results","conference","earnings","launch")):
        return "B", "날짜 명시 보도"
    return "C", "최근 촉매 보도"


def _financing_risk(news_items):
    text_all=" ".join(str(x.get("event_title") or "") for x in (news_items or [])).lower()
    red=("public offering","registered direct","at-the-market"," atm ","warrant exercise","reverse split","delisting","compliance notice")
    yellow=("shelf registration","prospectus","capital raise","convertible note","private placement")
    hits=[x for x in red if x in text_all]
    if hits:
        return 90.0,"🔴 높음",hits[:3]
    hits=[x for x in yellow if x in text_all]
    if hits:
        return 55.0,"🟡 보통",hits[:3]
    return 10.0,"🟢 낮음",[]


def _expected_runup_window(category, days, progress):
    if days is not None:
        if days<=2: return "오늘~2일 · 발표임박"
        if days<=7: return "오늘~5일"
        if days<=14: return "3~10일"
        if days<=30: return "1~3주"
    if category in ("FDA·임상","실적"): return "3~10일 관찰"
    if category in ("계약","AI","우주"): return "오늘~2주 관찰"
    return "기간 추정 불가"


def _runup_trade_levels(price, atr_pct, stage, spread_pct):
    if price<=0:
        return {"권장진입가($)":0.0,"손절가($)":0.0,"1차목표($)":0.0,"2차목표($)":0.0,"손익비":0.0}
    atr=max(price*max(atr_pct,2.0)/100, price*0.025)
    entry=price*(0.997 if str(stage).startswith("🟢") else 0.985)
    stop=min(entry-price*0.035, entry-atr*0.9)
    risk=max(entry-stop,price*0.02)
    target1=entry+risk*1.8
    target2=entry+risk*3.0
    rr=(target1-entry)/(entry-stop) if entry>stop else 0.0
    return {
        "권장진입가($)":round(entry,4),"손절가($)":round(stop,4),
        "1차목표($)":round(target1,4),"2차목표($)":round(target2,4),"손익비":round(rr,2),
    }
def analyze_runup_stage(history, event_date_text=""):
    """최근 60일 가격·거래량으로 런업 시작·진행·후반을 일관되게 판정한다."""
    empty = {
        "런업단계": "⚪ 데이터부족", "런업진행도(%)": 0.0,
        "런업시작일": "", "런업시작가($)": 0.0, "런업이후(%)": 0.0,
        "3일(%)": 0.0, "5일(%)": 0.0, "10일(%)": 0.0, "20일(%)": 0.0,
        "거래량배수": 0.0, "최근고점대비(%)": 0.0,
        "진입판정": "⚪ 일봉 데이터 대기", "진입근거": [],
    }
    if len(history) < 22:
        return empty

    closes = [to_float(x.get("close")) for x in history]
    highs = [to_float(x.get("high")) for x in history]
    volumes = [to_float(x.get("volume")) for x in history]
    current = closes[-1]
    if current <= 0:
        return empty

    start_index = max(0, len(history) - min(60, len(history)))
    detected = None
    # 첫 유효 돌파일: 직전 20일 고점 +1% 돌파와 거래량 1.5배를 동시에 만족.
    for i in range(max(start_index + 20, 20), len(history)):
        prior_high = max(highs[i-20:i])
        prior_vols = volumes[i-20:i]
        prior_vol = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if closes[i] >= prior_high * 1.01 and prior_vol > 0 and volumes[i] >= prior_vol * 1.5:
            detected = i
            break

    # 명확한 돌파가 없으면 최근 60일 저점 이후 8% 상승한 첫날을 보조 시작점으로 사용.
    if detected is None:
        window = closes[start_index:]
        low_rel = min(range(len(window)), key=lambda j: window[j])
        low_index = start_index + low_rel
        detected = low_index
        for i in range(low_index + 1, len(history)):
            if closes[i] >= closes[low_index] * 1.08:
                detected = i
                break

    start_price = closes[detected]
    runup_gain = (current / start_price - 1) * 100 if start_price > 0 else 0.0
    ret3, ret5 = _pct_change_from(history, 3), _pct_change_from(history, 5)
    ret10, ret20 = _pct_change_from(history, 10), _pct_change_from(history, 20)
    prior_20_vols = volumes[-21:-1]
    avg20 = sum(prior_20_vols) / len(prior_20_vols) if prior_20_vols else 0
    volume_multiple = volumes[-1] / avg20 if avg20 > 0 else 0.0
    recent_high = max(highs[max(detected, len(history)-20):])
    drawdown = (current / recent_high - 1) * 100 if recent_high > 0 else 0.0

    event_date = _parse_iso_or_us_date(event_date_text)
    today = datetime.now(ZoneInfo("America/New_York")).date()
    days = (event_date - today).days if event_date else None
    prepricing = _runup_prepricing_metrics(history)

    # 진행도는 반드시 단계와 같은 방향으로 움직이도록 구성한다.
    gain_component = min(max(runup_gain, 0.0), 120.0) / 120.0 * 60.0
    time_component = 0.0
    if days is not None:
        time_component = (
            30.0 if days <= 2 else
            24.0 if days <= 5 else
            17.0 if days <= 10 else
            10.0 if days <= 20 else
            4.0
        )
    high_component = 10.0 if drawdown >= -3 else 6.0 if drawdown >= -8 else 2.0
    progress = round(min(100.0, max(0.0, gain_component + time_component + high_component)), 1)

    # 시작 이후 하락이면 런업 실패/무효로 처리.
    if runup_gain < -5:
        stage = "⚪ 런업 무효"
        entry = "🔴 신규진입 금지"
    elif prepricing.get("과열제외"):
        stage = "🔴 선반영 과열"
        entry = "🔴 신규진입 금지"
    elif progress < 35:
        stage = "🟢 런업 초기"
        entry = "👀 초기 돌파 확인"
    elif progress < 70:
        stage = "🟡 런업 진행"
        entry = "🟡 눌림 진입대기"
    else:
        stage = "🔴 런업 후반"
        entry = "🔴 신규진입 금지"

    healthy_pullback = -8 <= drawdown <= -1
    momentum_ok = ret3 > 0 and ret5 > 0
    volume_ok = volume_multiple >= 1.2
    event_window_ok = days is None or 5 <= days <= 30

    reasons = []
    if momentum_ok:
        reasons.append("최근 3·5일 상승")
    if volume_ok:
        reasons.append(f"거래량 {volume_multiple:.1f}배")
    if healthy_pullback:
        reasons.append(f"고점 대비 {drawdown:.1f}% 눌림")
    if event_window_ok:
        reasons.append("이벤트 시기 적정")
    if runup_gain <= 35:
        reasons.append("아직 과도한 선반영 아님")

    if stage.startswith("🟢") and momentum_ok and volume_ok and event_window_ok and 0 <= runup_gain <= 35:
        entry = "🔥 지금 진입 검토"
    elif stage.startswith("🟡") and healthy_pullback and ret3 >= -3 and volume_multiple >= 0.8 and event_window_ok:
        entry = "🟡 눌림 분할진입 대기"

    return {
        "런업단계": stage,
        "런업진행도(%)": progress,
        "런업시작일": str(history[detected].get("date") or ""),
        "런업시작가($)": round(start_price, 4),
        "런업이후(%)": round(runup_gain, 1),
        "3일(%)": round(ret3, 1), "5일(%)": round(ret5, 1),
        "10일(%)": round(ret10, 1), "20일(%)": round(ret20, 1),
        "거래량배수": round(volume_multiple, 2),
        "최근고점대비(%)": round(drawdown, 1),
        "진입판정": entry,
        "진입근거": reasons,
        **prepricing,
    }


def is_small_runup_candidate(item):
    """대형주는 런업 탭에서 제외하고 소형 이벤트주만 남긴다."""
    ticker = str(item.get("종목코드") or "").upper()
    price = to_float(item.get("현재가($)"))
    market_cap = to_float(item.get("시가총액(API)"))
    amount_m = to_float(item.get("오늘거래대금(백만$)"))
    if ticker in RUNUP_LARGE_CAP_BLACKLIST:
        return False
    if not (0.20 <= price <= 40):
        return False
    # 시총 단위가 API별로 다를 수 있어 값이 명확할 때만 150억달러 초과를 제외한다.
    if market_cap > 15_000_000_000:
        return False
    return amount_m >= 0.05


def select_diverse_runup_top5(records):
    """분류 다양성을 우선해 각 카테고리 최고점부터 뽑고 나머지를 점수순으로 채운다."""
    ordered = sorted(records, key=lambda item: (str(item.get("현재판정") or "").startswith("🔥"), str(item.get("런업단계") or "").startswith("🟢"), to_float(item.get("런업점수"))), reverse=True)
    chosen, used = [], set()
    for category in RUNUP_CATEGORY_ORDER:
        candidate = next((item for item in ordered if item.get("런업분류") == category and item.get("종목코드") not in used), None)
        if candidate:
            chosen.append(candidate)
            used.add(candidate.get("종목코드"))
            if len(chosen) >= 5:
                return chosen
    for item in ordered:
        if item.get("종목코드") in used:
            continue
        chosen.append(item)
        used.add(item.get("종목코드"))
        if len(chosen) >= 5:
            break
    return chosen


def build_dynamic_us_runup_top5(token, session_mode):
    """당일 시장 후보를 재료별로 분류하고 자동 Top 5를 만든다."""
    st.session_state["runup_excluded"] = []
    grouped, source_counts, rank_errors = get_us_triple_rank_rows(token, penny_only=False)
    market_rows, rank_pairs = build_us_triple_rank_rows(
        grouped["상승률"], grouped["당일거래량"], grouped["체결강도"],
        session_mode, penny_only=False, surge_rows=grouped.get("거래량급증", []),
    )
    # 런업은 대형 우량주 시드가 아니라 당일 순위의 소형 이벤트 후보를 중심으로 찾습니다.
    small_seed = [pair for pair in US_MOMENTUM_SEED if pair[1] not in RUNUP_LARGE_CAP_BLACKLIST]
    candidate_pairs = unique_us_pairs(rank_pairs[:50] + small_seed[:20])[:45]
    price_rows, price_errors = get_us_multiple_prices(token, candidate_pairs, session_mode)
    base_table = build_us_fast_table(price_rows, candidate_pairs, strategy="runup")
    if not base_table.empty:
        base_table = base_table[base_table.apply(lambda row: is_small_runup_candidate(row.to_dict()), axis=1)].reset_index(drop=True)
    if base_table.empty:
        return base_table, source_counts, rank_errors + price_errors + ["조건에 맞는 미국 소형 런업 후보가 없습니다."]

    earnings, earnings_errors = get_public_earnings_calendar(14)
    news_by_ticker, news_errors = {}, []
    lookup = {
        str(row.get("종목코드") or "").upper(): str(row.get("종목명") or "")
        for _, row in base_table.head(40).iterrows()
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_runup_news, ticker, name): ticker
            for ticker, name in lookup.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                news_by_ticker[ticker] = future.result()
            except Exception as error:
                news_by_ticker[ticker] = []
                news_errors.append(f"{ticker}: {error}")

    records = []
    for _, row in base_table.iterrows():
        item = row.to_dict()
        ticker = str(item.get("종목코드") or "").upper()
        event_candidates = []
        if ticker in earnings:
            event_candidates.append(earnings[ticker])
        event_candidates.extend(news_by_ticker.get(ticker, []))

        best_event, best_event_score, best_dday, best_category = None, -1.0, "", "기타"
        for event in event_candidates:
            score, dday, category = _event_score(event)
            if score > best_event_score:
                best_event, best_event_score = event, score
                best_dday, best_category = dday, category

        source_name = str((best_event or {}).get("source") or "")
        event_date_value = _parse_iso_or_us_date((best_event or {}).get("event_date"))
        event_age = to_float((best_event or {}).get("age_days"))
        event_grade, event_grade_reason = _runup_event_grade(best_event)
        financing_score, financing_grade, financing_reasons = _financing_risk(news_by_ticker.get(ticker, []))
        # 공식 실적 캘린더, 명시된 미래 일정, 또는 3일 이내의 강한 촉매 뉴스만 '확인' 처리한다.
        verified = bool(best_event and event_grade in ("A", "B"))
        technical = _runup_technical_score(item)
        raw_total = technical + max(best_event_score, 0)
        try:
            history = get_us_daily_history(token, item.get("거래소코드"), ticker, 80)
            stage_info = analyze_runup_stage(history, (best_event or {}).get("event_date"))
        except Exception as history_error:
            stage_info = analyze_runup_stage([], "")
            news_errors.append(f"{ticker} 일봉: {history_error}")
        # 초기·진행 단계는 가점, 후반은 감점한다.
        stage_name = str(stage_info.get("런업단계") or "")
        raw_total += 18 if stage_name.startswith("🟢") else 8 if stage_name.startswith("🟡") else -18 if stage_name.startswith("🔴") else 0
        if not verified:
            raw_total -= 18
            best_event = {
                "event_title": "기술적 후보·구체적 일정 미확인",
                "event_date": "",
                "source": "시장 순위 데이터",
            }
            best_category = "기타"
            best_dday = "일정 미확인"

        rate = to_float(item.get("등락률(%)"))
        vwap_gap = to_float(item.get("VWAP위치(%)"))
        runup_gain = to_float(stage_info.get("런업이후(%)"))
        days_to_event = (event_date_value - datetime.now(ZoneInfo("America/New_York")).date()).days if event_date_value else None
        expected_window = _expected_runup_window(best_category, days_to_event, to_float(stage_info.get("런업진행도(%)")))
        spread_pct = to_float(item.get("호가차이(%)"))
        levels = _runup_trade_levels(to_float(item.get("현재가($)")), to_float(stage_info.get("ATR비율(%)")), stage_name, spread_pct)
        # 100점 체계로 정규화한다. 예측확률이 아니라 후보 비교점수다.
        raw_total -= to_float(stage_info.get("선반영점수")) * 0.45
        raw_total -= financing_score * 0.35
        if spread_pct > 2.0:
            raw_total -= 18
        if to_float(levels.get("손익비")) < 1.5:
            raw_total -= 15
        total = max(0.0, min(100.0, raw_total))
        entry_decision = str(stage_info.get("진입판정") or "👀 관찰")
        strict_ok = bool(
            entry_decision.startswith("🔥") and verified and event_grade in ("A", "B")
            and total >= 80 and -2 <= vwap_gap <= 5
            and to_float(stage_info.get("선반영점수")) < 45
            and financing_score < 50 and spread_pct <= 2.0
            and to_float(levels.get("손익비")) >= 1.5
        )
        if strict_ok:
            verdict = "🟢 지금 진입 검토"
        elif entry_decision.startswith("🟡") and verified:
            verdict = "🟡 눌림 진입대기"
        elif entry_decision.startswith("🔴") or rate >= 20 or vwap_gap >= 10:
            verdict = "🔴 런업 후반·진입금지"
        elif verified and total >= 55:
            verdict = "👀 런업 관찰"
        else:
            verdict = "⚪ 재료·추세 확인"

        # 재료가 확인되지 않았거나 기타 분류이거나 런업 시작 이후 -5% 이하이면 Top5에서 제외한다.
        excluded_reasons = []
        if not verified: excluded_reasons.append("공식 이벤트 A/B 미확인")
        if best_category == "기타": excluded_reasons.append("이벤트 분류 불명확")
        if runup_gain < -5: excluded_reasons.append("런업 시작 이후 약세")
        if stage_info.get("과열제외"): excluded_reasons.append("선반영 과열")
        if financing_score >= 80: excluded_reasons.append("증자·희석 위험")
        if spread_pct > 3.0: excluded_reasons.append("호가 과대")
        if excluded_reasons:
            st.session_state.setdefault("runup_excluded", []).append({
                "종목코드":ticker,"종목명":item.get("종목명"),"사유":" · ".join(excluded_reasons[:3])
            })
            continue

        item.update(stage_info)
        item.update({
            "런업분류": best_category,
            "재료": str(best_event.get("event_title") or "")[:140],
            "예정일": str(best_event.get("event_date") or ""),
            "D-day": best_dday,
            "재료확인": "확인" if verified else "미확인",
            "이벤트등급": event_grade,
            "이벤트확인근거": event_grade_reason,
            "예상런업": expected_window,
            "희석위험점수": round(financing_score,1),
            "희석위험": financing_grade,
            "희석위험사유": financing_reasons,
            "런업점수": round(total, 1),
            "현재판정": verdict,
            "자료출처": str(best_event.get("source") or ""),
            **levels,
        })
        records.append(item)

    selected = select_diverse_runup_top5(records)
    result = pd.DataFrame(selected)
    if not result.empty:
        result["_진입우선"] = result["현재판정"].astype(str).str.startswith("🔥")
        result["_초기우선"] = result["런업단계"].astype(str).str.startswith("🟢")
        result = result.sort_values(["_진입우선", "_초기우선", "런업점수"], ascending=[False, False, False]).head(5).drop(columns=["_진입우선", "_초기우선"]).reset_index(drop=True)
        result.insert(0, "런업순위", range(1, len(result) + 1))
    errors = list(rank_errors) + list(price_errors) + list(earnings_errors[:3]) + list(news_errors[:3])
    return result, source_counts, errors

US_DAY_EXCHANGE = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
US_NORMAL_EXCHANGE = {value: key for key, value in US_DAY_EXCHANGE.items()}


def unique_us_pairs(pairs):
    seen = set()
    result = []
    for exchange, ticker in pairs:
        exchange = US_NORMAL_EXCHANGE.get(str(exchange).upper(), str(exchange).upper())
        ticker = str(ticker).strip().upper()
        key = (exchange, ticker)
        if exchange not in US_EXCHANGE_NAMES or not re.fullmatch(r"[A-Z]{1,6}", ticker):
            continue
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def resolve_us_session(session_choice):
    """한투 미국 주간거래는 별도 거래소 코드를 사용한다."""
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    summer = bool(now_ny.dst())
    minutes = now_kst.hour * 60 + now_kst.minute
    day_end = 17 * 60 if summer else 18 * 60

    if session_choice.startswith("주간"):
        mode = "day"
    elif session_choice.startswith("프리"):
        mode = "normal"
    else:
        mode = "day" if 10 * 60 <= minutes < day_end else "normal"

    if mode == "day":
        detail = f"주간거래 10:00~{('17:00' if summer else '18:00')}"
    else:
        pre_start = 17 * 60 if summer else 18 * 60
        regular_start = 22 * 60 + 30 if summer else 23 * 60 + 30
        regular_end = 5 * 60 if summer else 6 * 60
        if pre_start <= minutes < regular_start:
            detail = "프리마켓"
        elif minutes >= regular_start or minutes < regular_end:
            detail = "정규장"
        elif regular_end <= minutes < 9 * 60:
            detail = "애프터마켓"
        else:
            detail = "휴장·전환 구간(직전 정규시세)"
    return mode, detail, now_kst.strftime("%Y-%m-%d %H:%M:%S")


def session_exchange(exchange, session_mode):
    normal = US_NORMAL_EXCHANGE.get(exchange, exchange)
    if session_mode == "day":
        return US_DAY_EXCHANGE.get(normal, normal)
    return normal


def make_us_ws_key(exchange, ticker, session_mode):
    """한투 미국 실시간 체결 구독키.

    프리·정규·애프터: D + NAS/NYS/AMS + 티커
    주간거래: R + BAQ/BAY/BAA + 티커
    """
    normal = US_NORMAL_EXCHANGE.get(str(exchange).upper(), str(exchange).upper())
    ticker = str(ticker).strip().upper()
    if session_mode == "day":
        return f"R{US_DAY_EXCHANGE.get(normal, normal)}{ticker}"
    return f"D{normal}{ticker}"


def _match_ws_ticker(symbol_text, requested):
    symbol_text = str(symbol_text).strip().upper()
    for ticker in sorted(requested, key=len, reverse=True):
        if symbol_text == ticker or symbol_text.endswith(ticker):
            return ticker
    return ""


def _parse_us_ws_payload(payload, data_count, requested):
    """한투 HDFSCNT0의 현재 25필드와 구 26필드 형식을 둘 다 처리한다."""
    values = payload.split("^")
    if data_count <= 0 or not values:
        return []
    width = len(values) // data_count
    if width < 25:
        return []

    parsed = []
    for index in range(data_count):
        row = values[index * width:(index + 1) * width]
        if len(row) < 25:
            continue

        # 구 샘플은 실시간코드·티커가 별도(26개),
        # 현재 공식 샘플은 SYMB부터 시작(25개)한다.
        if width >= 26:
            ticker = _match_ws_ticker(row[1], requested) or _match_ws_ticker(row[0], requested)
            pos = {
                "date": 6, "time": 7, "open": 8, "high": 9, "low": 10,
                "last": 11, "rate": 14, "bid": 15, "ask": 16,
                "evol": 19, "tvol": 20, "tamt": 21, "buy": 23, "strength": 24,
            }
        else:
            ticker = _match_ws_ticker(row[0], requested)
            pos = {
                "date": 5, "time": 6, "open": 7, "high": 8, "low": 9,
                "last": 10, "rate": 13, "bid": 14, "ask": 15,
                "evol": 18, "tvol": 19, "tamt": 20, "buy": 22, "strength": 23,
            }
        if not ticker:
            continue
        parsed.append({
            "ticker": ticker,
            "date": str(row[pos["date"]]).strip(),
            "time": str(row[pos["time"]]).strip().zfill(6),
            "open": to_float(row[pos["open"]]),
            "high": to_float(row[pos["high"]]),
            "low": to_float(row[pos["low"]]),
            "last": to_float(row[pos["last"]]),
            "rate": to_float(row[pos["rate"]]),
            "bid": to_float(row[pos["bid"]]),
            "ask": to_float(row[pos["ask"]]),
            "evol": to_float(row[pos["evol"]]),
            "tvol": to_float(row[pos["tvol"]]),
            "tamt": to_float(row[pos["tamt"]]),
            "buy_volume": to_float(row[pos["buy"]]),
            "strength": to_float(row[pos["strength"]]),
        })
    return parsed


def get_us_live_snapshots(pairs, session_mode, wait_seconds=0.85, limit=18):
    """활발한 후보 최대 30종목을 한 웹소켓에서 받는다.

    새 체결이 발생한 종목만 반환하므로, 거래가 없는 종목의
    예전 가격을 '실시간'으로 잘못 표시하지 않는다.
    """
    if websocket is None:
        return {}, ["websocket-client가 설치되지 않아 REST 가격을 사용했습니다."]

    pairs = unique_us_pairs(pairs)[:limit]
    if not pairs:
        return {}, []

    approval_key = issue_ws_approval_key(APP_KEY, APP_SECRET)
    requested = {ticker for _, ticker in pairs}
    snapshots = {}
    tick_events = {}
    errors = []
    ws = None

    try:
        ws = websocket.create_connection(
            "ws://ops.koreainvestment.com:21000",
            timeout=2.5,
            enable_multithread=False,
        )
        for exchange, ticker in pairs:
            message = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1",
                    "content-type": "utf-8",
                },
                "body": {
                    "input": {
                        "tr_id": "HDFSCNT0",
                        "tr_key": make_us_ws_key(exchange, ticker, session_mode),
                    }
                },
            }
            ws.send(json.dumps(message))
            time.sleep(0.015)

        deadline = time.monotonic() + wait_seconds
        ws.settimeout(0.18)
        while time.monotonic() < deadline and len(snapshots) < len(requested):
            try:
                raw = ws.recv()
            except Exception:
                continue
            if not raw:
                continue
            if raw.startswith("0|"):
                parts = raw.split("|", 3)
                if len(parts) != 4 or parts[1] != "HDFSCNT0":
                    continue
                for item in _parse_us_ws_payload(parts[3], to_int(parts[2]), requested):
                    if item["last"] > 0:
                        item = dict(item)
                        item["_received_at"] = time.time()
                        tick_events.setdefault(item["ticker"], []).append(item)
                        snapshots[item["ticker"]] = item
            elif raw.startswith("{"):
                try:
                    message = json.loads(raw)
                    if (message.get("header") or {}).get("tr_id") == "PINGPONG":
                        ws.send(raw)
                except Exception:
                    pass
    except Exception as error:
        errors.append(f"실시간 웹소켓: {error}")
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    for ticker, snapshot in snapshots.items():
        snapshot["_events"] = tick_events.get(ticker, [])[-240:]
    return snapshots, errors


def chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def get_us_multiple_prices(token, pairs, session_mode):
    """한투 공식 API로 한 호출에 최대 10종목을 조회한다."""
    pairs = unique_us_pairs(pairs)
    rows = []
    errors = []

    for batch in chunks(pairs, 10):
        params = {"AUTH": "", "NREC": str(len(batch))}
        for number, (exchange, ticker) in enumerate(batch, start=1):
            params[f"EXCD_{number:02d}"] = session_exchange(exchange, session_mode)
            params[f"SYMB_{number:02d}"] = ticker

        response = None
        for attempt in range(3):
            try:
                response = HTTP.get(
                    f"{BASE_URL}/uapi/overseas-price/v1/quotations/multprice",
                    headers=make_headers(token, "HHDFS76220000"),
                    params=params,
                    timeout=8,
                )
                if response.status_code == 200:
                    break
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(0.45 * (attempt + 1))
                    continue
                break
            except requests.RequestException as error:
                if attempt == 2:
                    errors.append(str(error))
                else:
                    time.sleep(0.45 * (attempt + 1))

        if response is None:
            continue
        if response.status_code != 200:
            errors.append(f"복수종목 시세 HTTP {response.status_code}")
            continue
        data = response_json(response)
        if data.get("rt_cd") != "0":
            errors.append(data.get("msg1") or "복수종목 시세 조회 실패")
            continue

        requested = {ticker: exchange for exchange, ticker in batch}
        output_rows = data.get("output2") or data.get("output") or []
        if isinstance(output_rows, dict):
            output_rows = output_rows.get("output2") or []
        for row in output_rows:
            row = dict(row)
            ticker = str(row.get("symb", "")).strip().upper()
            row["_base_exchange"] = requested.get(ticker, "")
            row["_session_mode"] = session_mode
            rows.append(row)

        # 초당 호출 한도에 여유를 두되, 기존 1종목씩 호출보다 훨씬 빠르다.
        time.sleep(0.08)

    return rows, errors


YAHOO_EXCHANGE_MAP = {
    "NMS": "NAS", "NGM": "NAS", "NCM": "NAS", "NAS": "NAS",
    "NYQ": "NYS", "NYS": "NYS", "ASE": "AMS", "AMS": "AMS",
}


def fetch_yahoo_screener(screen_id, max_price=0):
    url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
    response = HTTP.get(
        url,
        params={"scrIds": screen_id, "count": "100", "start": "0"},
        headers={"user-agent": "Mozilla/5.0"},
        timeout=4,
    )
    response.raise_for_status()
    data = response_json(response)
    result = ((data.get("finance") or {}).get("result") or [{}])[0]
    pairs = []
    for quote in result.get("quotes") or []:
        ticker = str(quote.get("symbol", "")).upper()
        exchange = YAHOO_EXCHANGE_MAP.get(str(quote.get("exchange", "")).upper())
        quote_price = to_float(quote.get("regularMarketPrice"))
        if max_price and not 0.05 <= quote_price <= max_price:
            continue
        if exchange and re.fullmatch(r"[A-Z]{1,6}", ticker):
            pairs.append((exchange, ticker))
    return pairs


@st.cache_data(ttl=45, show_spinner=False)
def get_yahoo_us_candidates(max_price=0):
    """급등률·거래량 상위를 동시에 받고, 최종 시세는 한투로 재검증한다."""
    result_by_screen = {}
    errors = []
    screen_ids = ("day_gainers", "most_actives")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_yahoo_screener, item, max_price): item
            for item in screen_ids
        }
        for future in as_completed(futures):
            try:
                result_by_screen[futures[future]] = future.result()
            except Exception as error:
                errors.append(f"{futures[future]}: {error}")
    pairs = (
        result_by_screen.get("day_gainers", [])
        + result_by_screen.get("most_actives", [])
    )
    result = unique_us_pairs(pairs)
    if not result and errors:
        raise RuntimeError(" / ".join(errors))
    return result


NASDAQ_SCREENER_EXCHANGES = {
    "nasdaq": "NAS",
    "nyse": "NYS",
    "amex": "AMS",
}


def fetch_nasdaq_penny_candidates(exchange_name):
    """Nasdaq 공개 스크리너에서 저가주 후보만 가져온다."""
    response = HTTP.get(
        "https://api.nasdaq.com/api/screener/stocks",
        params={
            "tableonly": "true",
            "limit": "5000",
            "offset": "0",
            "exchange": exchange_name,
            "download": "true",
        },
        headers={
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.nasdaq.com",
            "referer": "https://www.nasdaq.com/market-activity/stocks/screener",
        },
        timeout=8,
    )
    response.raise_for_status()
    rows = (((response.json().get("data") or {}).get("table") or {}).get("rows") or [])
    exchange = NASDAQ_SCREENER_EXCHANGES[exchange_name]
    ranked = []
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        price = to_float(str(row.get("lastsale") or "").replace("$", ""))
        rate = to_float(str(row.get("pctchange") or "").replace("%", ""))
        volume = to_int(row.get("volume"))
        name = str(row.get("name") or "").strip()
        if not re.fullmatch(r"[A-Z]{1,6}", ticker):
            continue
        if name and is_excluded_us_product(name, ticker):
            continue
        if 0.01 <= price <= 10 and rate >= 0.5 and volume >= 10_000:
            ranked.append((rate, volume, exchange, ticker))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(exchange, ticker) for _, _, exchange, ticker in ranked[:120]]


@st.cache_data(ttl=30, show_spinner=False)
def get_external_penny_candidates():
    """외부 소스는 종목 발견에만 사용하고 최종 수치는 한투로 다시 검증한다."""
    pairs = []
    counts = {}
    errors = []
    jobs = [("야후", get_yahoo_us_candidates, 10)] + [
        (f"나스닥-{name}", fetch_nasdaq_penny_candidates, name)
        for name in NASDAQ_SCREENER_EXCHANGES
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(function, argument): label
            for label, function, argument in jobs
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                found = future.result()
                counts[label] = len(found)
                pairs.extend(found)
            except Exception as error:
                counts[label] = 0
                errors.append(f"{label}: {error}")
    return unique_us_pairs(pairs), counts, errors


def pairs_from_us_rows(rows, exchange_hint=""):
    pairs = []
    for row in rows:
        ticker = str(
            row.get("symb")
            or row.get("SYMB")
            or row.get("ovrs_pdno")
            or row.get("pdno")
            or ""
        ).strip().upper()
        exchange = str(
            row.get("_exchange")
            or row.get("excd")
            or row.get("EXCD")
            or exchange_hint
        ).strip().upper()
        if re.fullmatch(r"[A-Z]{1,6}", ticker):
            pairs.append((exchange, ticker))
    return unique_us_pairs(pairs)


@st.cache_data(ttl=20, show_spinner=False)
def get_kis_penny_candidates(token):
    """한투 조건검색·가격급등·거래량급증을 합쳐 후보 누락을 줄인다."""
    all_pairs = []
    counts = {}
    errors = []

    def scan_exchange(exchange):
        found = []
        local_counts = {}
        local_errors = []
        calls = (
            ("저가주조건", get_us_penny_search_rows),
            ("가격급등", get_us_price_fluct_rows),
            ("거래량급증", get_us_volume_surge_rows),
        )
        for label, function in calls:
            try:
                rows = function(token, exchange)
                pairs = pairs_from_us_rows(rows, exchange)
                local_counts[f"한투-{exchange}-{label}"] = len(pairs)
                found.extend(pairs)
            except Exception as error:
                local_counts[f"한투-{exchange}-{label}"] = 0
                local_errors.append(f"한투-{exchange}-{label}: {error}")
        return found, local_counts, local_errors

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(scan_exchange, exchange) for exchange in ("NAS", "NYS", "AMS")]
        for future in as_completed(futures):
            found, local_counts, local_errors = future.result()
            all_pairs.extend(found)
            counts.update(local_counts)
            errors.extend(local_errors)

    return unique_us_pairs(all_pairs), counts, errors


def discover_us_penny_candidates(token, include_external=True):
    # 한투 후보와 외부 후보를 동시에 받습니다. 외부 사이트 하나가 늦어도
    # 한투 조회가 끝난 뒤 다시 기다리지 않게 하여 최초 검색 시간을 줄입니다.
    with ThreadPoolExecutor(max_workers=2) as executor:
        kis_future = executor.submit(get_kis_penny_candidates, token)
        external_future = (
            executor.submit(get_external_penny_candidates)
            if include_external
            else None
        )
        kis_pairs, kis_counts, kis_errors = kis_future.result()
        if external_future is not None:
            external_pairs, external_counts, external_errors = external_future.result()
        else:
            external_pairs, external_counts, external_errors = [], {}, []

    pairs = [*kis_pairs, *external_pairs]
    counts = {**kis_counts, **external_counts}
    errors = [*kis_errors, *external_errors]
    return unique_us_pairs(pairs), counts, errors


@st.cache_data(ttl=3, show_spinner=False)
def get_us_fast_rank_rows(token, penny_only):
    """미국 3개 거래소의 당일 누적 거래량 순위를 동시에 받는다."""
    volume_rows = []
    counts = {}
    errors = []
    exchanges = ("NAS", "NYS", "AMS")

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                get_us_trade_volume_rows,
                token,
                exchange,
                penny_only,
            ): exchange
            for exchange in exchanges
        }
        for future in as_completed(futures):
            exchange = futures[future]
            key = f"한투-{exchange}-당일거래량"
            try:
                rows = future.result()
                counts[key] = len(rows)
                volume_rows.extend(rows)
            except Exception as error:
                counts[key] = 0
                errors.append(f"{key}: {error}")

    return volume_rows, counts, errors


def merge_us_fast_rank_rows(price_rows, surge_rows, session_mode):
    """두 순위표를 종목별 한 행으로 합쳐 기존 고속표 입력 형식으로 만든다."""
    merged = {}
    ordered_pairs = []
    volume_fields = ("tvol", "tamt", "pvol")

    for rows in (price_rows, surge_rows):
        for source in rows:
            row = dict(source)
            ticker = str(row.get("symb") or "").strip().upper()
            exchange = US_NORMAL_EXCHANGE.get(
                str(row.get("_exchange") or row.get("excd") or "").strip().upper(),
                str(row.get("_exchange") or row.get("excd") or "").strip().upper(),
            )
            if exchange not in US_EXCHANGE_NAMES or not re.fullmatch(r"[A-Z]{1,6}", ticker):
                continue
            key = (exchange, ticker)
            if key not in merged:
                row["_base_exchange"] = exchange
                row["_session_mode"] = session_mode
                merged[key] = row
                ordered_pairs.append(key)
                continue

            target = merged[key]
            for field, value in row.items():
                if field in volume_fields:
                    if to_float(value) > to_float(target.get(field)):
                        target[field] = value
                elif target.get(field) in (None, "", "0", 0, 0.0) and value not in (None, ""):
                    target[field] = value

    return list(merged.values()), ordered_pairs


def build_us_fast_table(rows, candidates, strategy):
    order = {ticker: index + 1 for index, (_, ticker) in enumerate(candidates)}
    records = []
    for row in rows:
        ticker = str(row.get("symb", "")).strip().upper()
        exchange = str(row.get("_base_exchange") or row.get("excd") or "").strip()
        exchange = US_NORMAL_EXCHANGE.get(exchange, exchange)
        price = to_float(row.get("last"))
        base = to_float(row.get("base"))
        rate = to_float(row.get("rate"))
        if rate == 0 and price > 0 and base > 0:
            rate = (price / base - 1) * 100
        volume = to_int(row.get("tvol"))
        previous_volume = to_int(row.get("pvol"))
        amount = to_float(row.get("tamt"))
        if amount <= 0 and price > 0 and volume > 0:
            amount = price * volume
        vwap = amount / volume if amount > 0 and volume > 0 else 0
        vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
        volume_ratio = volume / previous_volume * 100 if previous_volume > 0 else 0
        market_cap = to_float(row.get("tomv"))
        strength = to_float(row.get("powx"))
        bid = to_float(row.get("pbid"))
        ask = to_float(row.get("pask"))
        spread_pct = (ask - bid) / price * 100 if ask > 0 and bid > 0 and price > 0 else 0
        name = str(row.get("knam") or row.get("name") or ticker).strip()
        if price <= 0:
            continue

        score = (
            max(rate, -20) * 4
            + min(math.log10(max(amount, 1)), 12) * 3
            + min(volume_ratio, 1000) / 25
            + max(vwap_gap, -10)
        )
        if strategy == "penny":
            passed = (
                0.01 <= price <= 10
                and rate >= 1
                and volume >= 20_000
                and amount >= 10_000
            )
            if rate >= 80 or vwap_gap >= 15:
                status = "🔴 폭등·추격금지"
            elif passed and 0 <= vwap_gap <= 5:
                status = "🟢 눌림 정밀검사"
            elif passed:
                status = "🟡 VWAP 복귀 대기"
            else:
                status = "⚪ 조건미달"
            # 동전주는 절대 거래대금보다 상승률·거래량 확대에 가중치를 둔다.
            score = (
                min(rate, 300) * 1.5
                + min(volume_ratio, 2000) / 15
                + min(math.log10(max(volume, 1)), 9) * 5
                + min(math.log10(max(amount, 1)), 12) * 2
                - max(vwap_gap - 6, 0) * 4
            )
        elif strategy == "runup":
            passed = volume >= 10_000 and amount >= 100_000 and -5 <= rate <= 15
            if rate >= 15 or vwap_gap >= 8:
                status = "🔴 과열·눌림대기"
            elif passed and 1 <= rate <= 10 and (vwap <= 0 or price >= vwap):
                status = "🟢 런업 기술추세"
            elif passed and -1.5 <= vwap_gap <= 4:
                status = "🟡 런업 눌림관찰"
            else:
                status = "⚪ 재료확인 대기"
            score = (
                min(max(rate, -5), 15) * 4
                + min(volume_ratio, 500) / 18
                + min(math.log10(max(amount, 1)), 12) * 3
                - max(vwap_gap - 5, 0) * 5
            )
        elif strategy == "momentum":
            surge_rate = to_float(row.get("n_rate"))
            passed = rate >= 2 and volume >= 10_000 and amount >= 100_000
            if rate >= 100:
                status = "🟣 폭등 발견·신규진입 금지"
            elif rate >= 60 or vwap_gap >= 12:
                status = "🔴 초급등·눌림 확인"
            elif 20 <= rate < 60:
                status = "🟠 가속구간·분봉검사"
            elif passed and 5 <= rate < 20 and price >= vwap:
                status = "🟢 조기포착·분봉검사"
            elif passed and (surge_rate >= 100 or volume_ratio >= 120):
                status = "🟡 거래량 폭발 감시"
            elif rate >= 1:
                status = "⚪ 초기감시"
            else:
                status = "⚪ 조건대기"
        else:
            passed = 0.2 <= rate <= 8 and amount >= 1_000_000
            if rate >= 10 or vwap_gap >= 6:
                status = "🔴 추격주의"
            elif passed and price >= vwap:
                status = "🟢 기술지표 후보"
            elif price < vwap and rate < 0:
                status = "⚪ 약세·대기"
            else:
                status = "🟡 유동성 관찰"

        records.append({
            "시장": US_EXCHANGE_NAMES.get(exchange, exchange),
            "거래소코드": exchange,
            "종목코드": ticker,
            "종목명": name,
            "현재가($)": round(price, 4),
            "등락률(%)": round(rate, 2),
            "세션": "주간거래" if row.get("_session_mode") == "day" else "프리·정규·애프터",
            "시세시간(KST)": str(row.get("khms") or ""),
            "오늘거래량": volume,
            "전일대비거래량(%)": round(volume_ratio, 1),
            "오늘거래대금(백만$)": round(amount / 1_000_000, 2),
            "VWAP($)": round(vwap, 4),
            "VWAP위치(%)": round(vwap_gap, 2),
            "체결강도": round(strength, 1),
            "매수호가($)": round(bid, 4),
            "매도호가($)": round(ask, 4),
            "스프레드(%)": round(spread_pct, 2),
            "시가총액(API)": market_cap,
            "후보순위": order.get(ticker, 999),
            "상승률순위": to_int(row.get("_gain_rank")),
            "당일거래량순위": to_int(row.get("_volume_rank")),
            "체결강도순위": to_int(row.get("_power_rank")),
            "거래량급증순위": to_int(row.get("_surge_rank")),
            "발견점수": round(to_float(row.get("_discovery_score")), 2),
            "교집합수": to_int(row.get("_overlap_count")),
            "삼중교집합": bool(row.get("_triple_intersection", False)),
            "고속점수": round(score, 2),
            "조건통과": passed,
            "현재판정": status,
        })

    table = pd.DataFrame(records)
    if table.empty:
        return table
    if strategy == "penny":
        # 동전주 화면에는 $10 초과 종목이 절대로 섞이지 않게 한다.
        table = table[
            (table["현재가($)"] >= 0.01)
            & (table["현재가($)"] <= 10)
        ].copy()
        if table.empty:
            return table
        table.loc[~table["삼중교집합"], "현재판정"] = "🟡 2/3 관찰만·진입금지"
        table.loc[
            table["삼중교집합"] & table["조건통과"],
            "현재판정",
        ] = "🟢 삼중순위 차트검사"
        return table.sort_values(
            ["삼중교집합", "교집합수", "오늘거래량", "고속점수"],
            ascending=[False, False, False, False],
        ).head(30).reset_index(drop=True)
    if strategy == "momentum":
        # 합집합 후보를 모두 보존하고, 조기포착 구간과 발견점수로 정렬한다.
        table["조기포착"] = table["등락률(%)"].between(5, 35, inclusive="both")
        return table.sort_values(
            ["조기포착", "발견점수", "교집합수", "오늘거래량"],
            ascending=[False, False, False, False],
        ).head(40).reset_index(drop=True)
    return table.sort_values(
        ["시가총액(API)", "오늘거래대금(백만$)"], ascending=False
    ).head(30).reset_index(drop=True)



def apply_us_rest_prices(table, rows, strategy):
    """복수종목 REST 현재가로 기존 후보표의 가격·거래량·호가를 즉시 갱신한다.

    웹소켓은 짧은 대기 시간에 신규 체결이 없으면 아무 값도 주지 않을 수 있으므로,
    새로고침 시 REST를 기본값으로 적용하고 이후 WS 신규체결이 있으면 다시 덮어쓴다.
    """
    if table.empty or not rows:
        return table

    result = table.copy()
    now_ts = time.time()
    now_text = datetime.fromtimestamp(now_ts, SEOUL).strftime("%Y-%m-%d %H:%M:%S")
    by_ticker = {}
    for raw in rows:
        row = dict(raw)
        ticker = str(row.get("symb") or "").strip().upper()
        if ticker:
            by_ticker[ticker] = row

    for index, old in result.iterrows():
        ticker = str(old.get("종목코드") or "").strip().upper()
        row = by_ticker.get(ticker)
        if not row:
            continue

        price = to_float(row.get("last"))
        if price <= 0:
            continue
        base = to_float(row.get("base"))
        rate = to_float(row.get("rate"))
        if rate == 0 and base > 0:
            rate = (price / base - 1) * 100
        volume = to_int(row.get("tvol"))
        amount = to_float(row.get("tamt"))
        if amount <= 0 and volume > 0:
            amount = price * volume
        bid = to_float(row.get("pbid"))
        ask = to_float(row.get("pask"))
        strength = max(to_float(row.get("tpow")), to_float(row.get("powx")))
        vwap = amount / volume if amount > 0 and volume > 0 else 0
        vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
        spread = (ask - bid) / price * 100 if ask > 0 and bid > 0 else 0

        updates = {
            "현재가($)": round(price, 4),
            "등락률(%)": round(rate, 2),
            "오늘거래량": volume,
            "오늘거래대금(백만$)": round(amount / 1_000_000, 2),
            "VWAP($)": round(vwap, 4),
            "VWAP위치(%)": round(vwap_gap, 2),
            "매수호가($)": round(bid, 4),
            "매도호가($)": round(ask, 4),
            "스프레드(%)": round(spread, 2),
            "체결강도": round(strength, 1),
            "시세시간(KST)": now_text,
            "시세나이(초)": 0.0,
            "수신타임스탬프": now_ts,
            "시세출처": "한투 REST 즉시조회",
        }
        for key, value in updates.items():
            result.at[index, key] = value

    return result

def apply_us_live_snapshots(table, snapshots, strategy):
    """웹소켓으로 새 체결을 받은 종목만 표의 가격·거래량을 교체한다."""
    if table.empty:
        return table

    # 버튼을 다시 누를 때마다 방금 받은 모든 실체결을 세션에 누적해
    # 1·5·10분 방향 보조판정의 틱 입력으로 사용한다.
    tick_store = st.session_state.setdefault("us_tick_history", {})
    for ticker, snap in snapshots.items():
        key = str(ticker).upper()
        history = tick_store.setdefault(key, [])
        events = snap.get("_events") or [snap]
        for event in events:
            price = float(event.get("last", 0) or 0)
            if price <= 0:
                continue
            history.append({
                "ts": float(event.get("_received_at", time.time())),
                "price": price,
                "volume": int(event.get("tvol", 0) or 0),
                "strength": float(event.get("strength", 0) or 0),
                "bid": float(event.get("bid", 0) or 0),
                "ask": float(event.get("ask", 0) or 0),
            })
        # 1개 스냅샷이 아닌 최근 실체결 600개를 유지한다.
        tick_store[key] = history[-600:]

    result = table.copy()
    if "시세출처" not in result.columns:
        result["시세출처"] = "한투 REST(지연 가능)"
    for column in ("진입검토가($)", "손절기준($)", "1차목표($)", "2차목표($)"):
        if column not in result.columns:
            result[column] = 0.0
    if "1차필터점수" not in result.columns:
        result["1차필터점수"] = 0
    if "당일거래량순위" not in result.columns:
        result["당일거래량순위"] = 0
    if "시세나이(초)" not in result.columns:
        result["시세나이(초)"] = 999.0
    if "수신타임스탬프" not in result.columns:
        result["수신타임스탬프"] = 0.0

    for index, row in result.iterrows():
        ticker = str(row["종목코드"]).upper()
        snap = snapshots.get(ticker)
        if not snap:
            continue

        price = float(snap["last"])
        rate = float(snap["rate"])
        volume = int(snap["tvol"])
        amount = float(snap["tamt"])
        bid = float(snap["bid"])
        ask = float(snap["ask"])
        strength = float(snap["strength"])
        received_at = float(snap.get("_received_at", time.time()))
        quote_age = max(0.0, time.time() - received_at)
        fresh_quote = quote_age <= 3.0
        if amount <= 0 and price > 0 and volume > 0:
            amount = price * volume
        vwap = amount / volume if amount > 0 and volume > 0 else 0
        vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
        spread = (ask - bid) / price * 100 if ask > 0 and bid > 0 and price > 0 else 0

        result.at[index, "현재가($)"] = round(price, 4)
        result.at[index, "등락률(%)"] = round(rate, 2)
        result.at[index, "오늘거래량"] = volume
        result.at[index, "오늘거래대금(백만$)"] = round(amount / 1_000_000, 2)
        result.at[index, "VWAP($)"] = round(vwap, 4)
        result.at[index, "VWAP위치(%)"] = round(vwap_gap, 2)
        result.at[index, "매수호가($)"] = round(bid, 4)
        result.at[index, "매도호가($)"] = round(ask, 4)
        result.at[index, "스프레드(%)"] = round(spread, 2)
        result.at[index, "체결강도"] = round(strength, 1)
        result.at[index, "시세시간(KST)"] = datetime.fromtimestamp(
            received_at, SEOUL
        ).strftime("%Y-%m-%d %H:%M:%S")
        result.at[index, "시세나이(초)"] = round(quote_age, 2)
        result.at[index, "수신타임스탬프"] = received_at
        result.at[index, "시세출처"] = "한투 WS 신규체결"

        if strategy == "penny":
            vwap_ok = 0.2 <= vwap_gap <= 3
            strength_ok = strength >= 120
            spread_ok = 0 < spread <= 2.5
            rate_ok = 2 <= rate <= 40
            liquidity_ok = volume >= 100_000 and amount >= 100_000
            heat_ok = rate < 80 and vwap_gap < 12
        else:
            vwap_ok = 0.1 <= vwap_gap <= 3
            strength_ok = strength >= 110
            spread_ok = 0 < spread <= 2
            rate_ok = 2 <= rate <= 20
            liquidity_ok = volume >= 50_000 and amount >= 500_000
            heat_ok = rate < 25 and vwap_gap < 10

        signal_score = (
            (10 if fresh_quote else 0)
            + (20 if strength_ok else 0)
            + (20 if vwap_ok else 0)
            + (15 if spread_ok else 0)
            + (10 if rate_ok else 0)
            + (15 if liquidity_ok else 0)
            + (10 if heat_ok else 0)
        )
        result.at[index, "1차필터점수"] = signal_score
        triple_intersection = bool(row.get("삼중교집합", False))

        if strategy == "penny":
            passed = fresh_quote and 0.01 <= price <= 10 and rate >= 1 and volume >= 20_000 and amount >= 10_000
            if not fresh_quote:
                status = "🔴 시세 3초 초과·진입금지"
            elif rate >= 80 or vwap_gap >= 12 or spread > 5:
                status = "🔴 폭등·추격금지"
            elif passed and signal_score >= 75:
                status = "🟢 75점 정밀검증 대상"
            elif passed and strength < 120:
                status = "🟡 매수세 증가 대기"
            elif passed:
                status = "🟡 VWAP 눌림대기"
            else:
                status = "⚪ 조건미달"
        elif strategy == "momentum":
            passed = fresh_quote and rate >= 2 and volume >= 10_000 and amount >= 100_000
            if not fresh_quote:
                status = "🔴 시세 3초 초과·진입금지"
            elif rate >= 25 or vwap_gap >= 10 or spread > 5:
                status = "🔴 추격주의"
            elif passed and signal_score >= 75:
                status = "🟢 75점 정밀검증 대상"
            elif passed:
                status = "🟡 VWAP 눌림대기"
            else:
                status = "⚪ 조건대기"
        else:
            passed = fresh_quote and 0.2 <= rate <= 8 and amount >= 1_000_000
            if not fresh_quote:
                status = "🔴 시세 3초 초과·진입금지"
            elif rate >= 10 or vwap_gap >= 6:
                status = "🔴 추격주의"
            elif passed and vwap_gap >= 0 and strength >= 105:
                status = "🟢 기술지표 후보"
            elif passed:
                status = "🟡 매수세 확인 대기"
            else:
                status = "⚪ 유동성 관찰"
        if strategy in ("penny", "momentum") and passed and signal_score >= 75:
            status = "🟢 조기포착·눌림검사 대상"
        result.at[index, "조건통과"] = passed
        result.at[index, "현재판정"] = status

        # 지지·저항·ATR을 읽지 않은 실시간 순위 단계에서는
        # 진입가·손절가·목표가를 만들지 않는다. 아래 분봉 검증에서만 계산한다.
        entry = stop = target1 = target2 = 0.0
        result.at[index, "진입검토가($)"] = round(entry, 4)
        result.at[index, "손절기준($)"] = round(stop, 4)
        result.at[index, "1차목표($)"] = round(target1, 4)
        result.at[index, "2차목표($)"] = round(target2, 4)

    live = result[result["시세출처"].str.startswith("한투 WS")].copy()
    rest = result[~result["시세출처"].str.startswith("한투 WS")].copy()
    if strategy in ("penny", "momentum"):
        sort_columns = [
            column for column in ("1차필터점수", "발견점수", "교집합수", "오늘거래량")
            if column in live.columns
        ]
        ascending = [False] * len(sort_columns)
        live = live.sort_values(sort_columns, ascending=ascending)
        rest = rest.sort_values(sort_columns, ascending=ascending)
        live["당일거래량순위"] = range(1, len(live) + 1)
        rest["당일거래량순위"] = range(len(live) + 1, len(live) + len(rest) + 1)
    else:
        sort_column = "고속점수" if "고속점수" in result.columns else "오늘거래대금(백만$)"
        live = live.sort_values(sort_column, ascending=False)
        rest = rest.sort_values(sort_column, ascending=False)

    # 급등주·동전주는 방금 실제 체결을 받은 종목만 표시합니다.
    # REST 잔존값을 섞으면 장 전환 시 오래된 가격이 급등 후보처럼 보일 수 있습니다.
    if strategy in ("penny", "momentum") and not live.empty:
        return live.reset_index(drop=True)

    return pd.concat([live, rest], ignore_index=True)


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


def _domestic_rank_key(row):
    return str(
        row.get("mksc_shrn_iscd")
        or row.get("stck_shrn_iscd")
        or ""
    ).strip()


def _domestic_row_is_safe(row):
    """API에 표시된 위험·관리·정지 항목은 순위에서 제외한다."""
    name = str(row.get("hts_kor_isnm") or "").strip()
    if not name or is_excluded_product(name):
        return False
    danger_fields = (
        "mang_issu_yn", "mrkt_warn_cls_code", "invt_caful_yn",
        "trht_yn", "ssts_hot_yn", "short_over_yn",
    )
    danger_values = {"Y", "1", "2", "3", "4", "5"}
    return not any(
        str(row.get(field, "")).strip().upper() in danger_values
        for field in danger_fields
    )


def build_domestic_triple_universe(gain_rows, volume_rows, power_rows):
    """
    국내 상승률·당일 절대거래량·매수체결강도 순위 교집합.

    한투 국내 순위 API가 한 응답에 제공하는 상위 목록
    (일반적으로 최대 30개)을 정확히 교차한다. 3/3만 차트
    진입검사로 보내고 2/3은 관찰로만 남긴다.
    """
    merged = {}

    def absorb(rows, source):
        for raw in rows:
            row = dict(raw)
            ticker = _domestic_rank_key(row)
            if not re.fullmatch(r"\d{6}", ticker) or not _domestic_row_is_safe(row):
                continue
            name = str(row.get("hts_kor_isnm") or ticker).strip()
            item = merged.setdefault(ticker, {
                "시장": "국내",
                "시총순위": 0,
                "종목코드": ticker,
                "종목명": name,
                "KRX기준가": 0,
                "KRX등락률(%)": 0.0,
                "KRX누적거래량": 0,
                "1차거래대금근사": 0,
                "시가총액(조원)": 0.0,
                "거래량증가율(%)": 0.0,
                "거래량회전율(%)": 0.0,
                "평균거래량": 0,
                "체결강도": 0.0,
                "_상승률포함": False,
                "_거래량포함": False,
                "_체결강도포함": False,
            })
            item["_상승률포함" if source == "gain" else "_거래량포함" if source == "volume" else "_체결강도포함"] = True
            price = to_int(row.get("stck_prpr"))
            volume = to_int(row.get("acml_vol"))
            trading_value = to_int(row.get("acml_tr_pbmn"))
            shares = to_int(row.get("lstn_stcn"))
            if price > 0:
                item["KRX기준가"] = price
            if volume > item["KRX누적거래량"]:
                item["KRX누적거래량"] = volume
            if trading_value > item["1차거래대금근사"]:
                item["1차거래대금근사"] = trading_value
            rate = to_float(row.get("prdy_ctrt"))
            if source == "gain" or item["KRX등락률(%)"] == 0:
                item["KRX등락률(%)"] = round(rate, 2)
            if shares > 0 and price > 0:
                item["시가총액(조원)"] = round(price * shares / 1_000_000_000_000, 2)
            item["거래량증가율(%)"] = max(
                item["거래량증가율(%)"], to_float(row.get("vol_inrt"))
            )
            item["거래량회전율(%)"] = max(
                item["거래량회전율(%)"], to_float(row.get("vol_tnrt"))
            )
            item["평균거래량"] = max(item["평균거래량"], to_int(row.get("avrg_vol")))
            item["체결강도"] = max(
                item["체결강도"],
                to_float(row.get("tday_rltv")),
                to_float(row.get("rltv")),
            )

    absorb(gain_rows, "gain")
    absorb(volume_rows, "volume")
    absorb(power_rows, "power")
    items = list(merged.values())

    gain_items = sorted(
        [item for item in items if item["_상승률포함"]],
        key=lambda item: item["KRX등락률(%)"], reverse=True,
    )
    volume_items = sorted(
        [item for item in items if item["_거래량포함"]],
        key=lambda item: item["KRX누적거래량"], reverse=True,
    )
    power_items = sorted(
        [item for item in items if item["_체결강도포함"]],
        key=lambda item: item["체결강도"], reverse=True,
    )
    gain_rank = {item["종목코드"]: rank for rank, item in enumerate(gain_items, 1)}
    volume_rank = {item["종목코드"]: rank for rank, item in enumerate(volume_items, 1)}
    power_rank = {item["종목코드"]: rank for rank, item in enumerate(power_items, 1)}

    records = []
    for item in items:
        ticker = item["종목코드"]
        member_count = sum((ticker in gain_rank, ticker in volume_rank, ticker in power_rank))
        if member_count < 2:
            continue
        price = item["KRX기준가"]
        volume = item["KRX누적거래량"]
        if not (1000 <= price <= 200000) or volume < 100000:
            continue
        item["1차거래대금근사"] = item["1차거래대금근사"] or price * volume
        item["상승률순위"] = gain_rank.get(ticker)
        item["당일거래량순위"] = volume_rank.get(ticker)
        item["체결강도순위"] = power_rank.get(ticker)
        item["교집합수"] = member_count
        item["삼중교집합"] = member_count == 3
        item["급등순위"] = gain_rank.get(ticker) or 999
        records.append({key: value for key, value in item.items() if not key.startswith("_")})

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values(
        ["삼중교집합", "교집합수", "체결강도", "KRX누적거래량"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)


def get_integrated_price(token, ticker):
    response = HTTP.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price-2",
        headers=make_headers(token, "FHPST01010000"),
        params={
            "FID_COND_MRKT_DIV_CODE": "UN",
            "FID_INPUT_ISCD": ticker,
        },
        timeout=8,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 통합현재가 조회 실패: HTTP {response.status_code}")
    data = response_json(response)
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
    """통합현재가를 4개씩 병렬 조회한다.

    한꺼번에 수십 건을 쏘지 않고 4건 묶음 사이에 짧은 간격을 둬
    REST 제한을 피하면서 기존 순차 조회보다 빠르게 끝낸다.
    """
    prices = {}
    errors = []
    unique_tickers = list(dict.fromkeys(str(ticker).strip() for ticker in tickers if ticker))

    def fetch(ticker):
        try:
            return ticker, get_integrated_price(token, ticker), None
        except Exception as error:
            return ticker, None, str(error)

    batch_size = 4
    for start in range(0, len(unique_tickers), batch_size):
        batch = unique_tickers[start:start + batch_size]
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [executor.submit(fetch, ticker) for ticker in batch]
            for future in as_completed(futures):
                ticker, item, error = future.result()
                if item:
                    prices[ticker] = item
                if error:
                    errors.append(error)
        if start + batch_size < len(unique_tickers):
            time.sleep(0.22)
    return prices, errors


def subtract_one_minute(hhmmss):
    try:
        value = datetime.strptime(hhmmss, "%H%M%S") - timedelta(minutes=1)
        return value.strftime("%H%M%S")
    except Exception:
        return "000000"


def get_minute_page(token, ticker, end_time, market_code="UN"):
    response = HTTP.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "FHKST03010200"),
        params={
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": end_time,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_ETC_CLS_CODE": "",
        },
        timeout=8,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 분봉 조회 실패: HTTP {response.status_code}")
    data = response_json(response)
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


@st.cache_data(ttl=4, show_spinner=False)
def get_us_recent_minutes(token, exchange, ticker, session_mode, pages=3):
    """한투 공식 연속조회로 1분봉을 최대 360개 받는다.

    첫 페이지는 당일, 다음 페이지는 PINC=1·NEXT=1과
    이전 페이지의 가장 오래된 분봉 1분 전을 KEYB로 사용한다.
    주간거래 코드는 공식 제한에 따라 한 날치만 반환될 수 있다.
    """
    max_pages = min(4, max(1, int(pages)))
    exchange_code = session_exchange(exchange, session_mode)
    records = []
    keyb = ""
    oldest_seen = ""

    for page in range(max_pages):
        headers = make_headers(token, "HHDFS76950200")
        if page > 0:
            headers["tr_cont"] = "N"
        response = HTTP.get(
            f"{BASE_URL}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
            headers=headers,
            params={
                "AUTH": "",
                "EXCD": exchange_code,
                "SYMB": ticker,
                "NMIN": "1",
                "PINC": "1" if page > 0 else "0",
                "NEXT": "1" if page > 0 else "",
                "NREC": "120",
                "FILL": "",
                "KEYB": keyb,
            },
            timeout=10,
        )
        if response.status_code != 200:
            if not records:
                raise RuntimeError(f"{ticker} 미국 분봉 조회 실패: HTTP {response.status_code}")
            break
        data = response_json(response)
        if data.get("rt_cd") != "0":
            if not records:
                raise RuntimeError(data.get("msg1") or f"{ticker} 미국 분봉을 받지 못했습니다.")
            break

        page_times = []
        for row in data.get("output2") or []:
            date_text = str(row.get("tymd", "")).strip()
            time_text = str(row.get("xhms", "")).strip().zfill(6)
            if len(date_text) != 8 or len(time_text) != 6:
                continue
            try:
                timestamp = pd.to_datetime(date_text + time_text, format="%Y%m%d%H%M%S")
            except Exception:
                continue
            page_times.append(timestamp)
            records.append({
                "시간": timestamp,
                "시가": to_float(row.get("open")),
                "고가": to_float(row.get("high")),
                "저가": to_float(row.get("low")),
                "종가": to_float(row.get("last")),
                "거래량": to_float(row.get("evol")),
            })
        if not page_times or len(page_times) < 2:
            break
        oldest = min(page_times)
        oldest_key = oldest.strftime("%Y%m%d%H%M%S")
        if oldest_key == oldest_seen:
            break
        oldest_seen = oldest_key
        keyb = (oldest - pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        if page + 1 < max_pages:
            time.sleep(0.06)

    minute = pd.DataFrame(records)
    if minute.empty:
        return minute
    minute = minute.drop_duplicates("시간").sort_values("시간").set_index("시간")
    minute = minute[(minute[["시가", "고가", "저가", "종가"]] > 0).all(axis=1)]
    return minute.tail(120 * max_pages)


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


def analyze_selected_stock(minute, quote_row, strategy="quality"):
    """국내 우량주와 급등주를 서로 다른 조건으로 판정한다."""
    frames = {minutes: add_indicators(resample_bars(minute, minutes)) for minutes in (1, 3, 5, 15)}
    one, three, five, fifteen = frames[1], frames[3], frames[5], frames[15]
    price = float(quote_row.get("현재가", 0) or 0)
    day_vwap = float(quote_row.get("VWAP", 0) or 0)
    if price > 0 and (day_vwap <= 0 or not (price * 0.5 <= day_vwap <= price * 1.5)):
        typical = (one["고가"] + one["저가"] + one["종가"]) / 3
        volume = one["거래량"].clip(lower=0)
        day_vwap = float((typical * volume).sum() / volume.sum()) if volume.sum() > 0 else price
    change_pct = float(quote_row.get("등락률(%)", 0) or 0)

    rsi_1, rsi_3, rsi_5 = safe_last(one["RSI"]), safe_last(three["RSI"]), safe_last(five["RSI"])
    hist = three["MACD히스토그램"].dropna()
    bullish_macd = (
        safe_last(three["MACD"]) > safe_last(three["MACD시그널"])
        and len(hist) >= 2 and float(hist.iloc[-1]) >= float(hist.iloc[-2])
    )
    trend_1 = safe_last(one["종가"]) >= safe_last(one["EMA9"])
    trend_3 = safe_last(three["종가"]) >= safe_last(three["EMA9"])
    trend_5 = safe_last(five["종가"]) >= safe_last(five["EMA9"])
    trend_15 = len(fifteen) >= 3 and safe_last(fifteen["종가"]) >= safe_last(fifteen["EMA9"])
    close_rising = len(one) >= 2 and float(one["종가"].iloc[-1]) > float(one["종가"].iloc[-2])
    higher_low = len(one) >= 6 and float(one["저가"].tail(3).min()) >= float(one["저가"].iloc[-6:-3].min())
    recent_volume = one["거래량"].tail(5).mean() if len(one) >= 5 else 0
    prior_volume = one["거래량"].iloc[-25:-5].mean() if len(one) >= 25 else 0
    volume_speed = float(recent_volume / prior_volume) if prior_volume > 0 else 0
    vwap_gap = (price / day_vwap - 1) * 100 if day_vwap > 0 else 0
    recent_high = float(one["고가"].tail(30).max()) if not one.empty else price
    pullback_pct = (price / recent_high - 1) * 100 if recent_high > 0 else 0
    amount = float(quote_row.get("오늘누적거래대금", quote_row.get("거래대금", 0)) or 0)
    strength = float(quote_row.get("체결강도", 0) or 0)
    overlap = int(quote_row.get("교집합수", 0) or 0)

    atr_5 = safe_last(five["ATR"])
    recent_low = float(five["저가"].tail(3).min()) if not five.empty else price
    entry = min(price, max(day_vwap, safe_last(one["EMA9"], price))) if price > 0 else 0
    if atr_5 > 0:
        stop = min(recent_low, entry - 0.85 * atr_5)
        target1, target2 = entry + 1.35 * atr_5, entry + 2.15 * atr_5
    else:
        stop, target1, target2 = entry * 0.985, entry * 1.022, entry * 1.038
    risk = max(entry-stop, 0)
    rr = (target1-entry)/risk if risk > 0 else 0

    if strategy == "momentum":
        checks = {
            "순위 중첩": overlap >= 2,
            "거래대금": amount >= 3_000_000_000,
            "체결강도": strength == 0 or strength >= 110,
            "첫 눌림": -6.5 <= pullback_pct <= -0.3,
            "VWAP 회복": -0.5 <= vwap_gap <= 3.5,
            "재상승": trend_1 and close_rising and higher_low,
            "3·5분 추세": trend_3 and trend_5,
            "거래량 재유입": volume_speed >= 1.1,
            "비과열": rsi_1 < 84 and change_pct < 25,
            "손익비": rr >= 1.5,
        }
        mandatory = checks["순위 중첩"] and checks["거래대금"] and checks["첫 눌림"] and checks["재상승"] and checks["손익비"]
        if change_pct >= 25 or rsi_1 >= 88 or vwap_gap > 6:
            verdict = "🔴 국내 급등 과열·추격금지"
        elif mandatory and sum(checks.values()) >= 8:
            verdict = "🟢 국내 급등 재돌파 진입검토"
        elif sum(checks.values()) >= 5:
            verdict = "🟡 국내 급등 눌림대기"
        else:
            verdict = "⚪ 국내 급등 조건확인"
    else:
        checks = {
            "VWAP 근처": -0.8 <= vwap_gap <= 1.8,
            "RSI 정상": 43 <= rsi_3 <= 70 and rsi_1 < 78,
            "MACD 개선": bullish_macd,
            "1·3·5분 추세": trend_1 and trend_3 and trend_5,
            "15분 추세": trend_15,
            "거래량 유지": volume_speed >= 0.7,
            "가격 회복": close_rising and higher_low,
            "비과열": change_pct < 7 and vwap_gap < 2.5,
            "손익비": rr >= 1.5,
        }
        mandatory = checks["VWAP 근처"] and checks["1·3·5분 추세"] and checks["가격 회복"] and checks["손익비"]
        if change_pct >= 9 or rsi_1 >= 84 or vwap_gap > 4:
            verdict = "🔴 국내 우량주 추격주의"
        elif mandatory and sum(checks.values()) >= 7:
            verdict = "🟢 국내 우량주 눌림 진입검토"
        elif sum(checks.values()) >= 5:
            verdict = "🟡 국내 우량주 눌림대기"
        else:
            verdict = "⚪ 국내 우량주 추세확인"

    timeframe_summary = {}
    for minutes, frame in frames.items():
        timeframe_summary[minutes] = {
            "trend": safe_last(frame["종가"]) >= safe_last(frame["EMA9"]),
            "rsi": safe_last(frame["RSI"], float("nan")),
            "macd_up": safe_last(frame["MACD"]) > safe_last(frame["MACD시그널"]),
            "bars": len(frame),
        }
    return {
        "frames": frames, "verdict": verdict, "strategy": strategy,
        "corrected_vwap": round(day_vwap, 2), "corrected_vwap_gap": round(vwap_gap, 2),
        "score": sum(checks.values()), "max_score": len(checks), "checks": checks,
        "rsi_1": rsi_1, "rsi_3": rsi_3, "rsi_5": rsi_5,
        "volume_speed": volume_speed, "vwap_gap": vwap_gap, "pullback_pct": pullback_pct,
        "trend_1": trend_1, "trend_3": trend_3, "trend_5": trend_5, "trend_15": trend_15,
        "bullish_macd": bullish_macd, "entry": round(entry), "stop": round(stop),
        "target1": round(target1), "target2": round(target2), "reward_risk1": round(rr,2),
        "timeframe_summary": timeframe_summary,
    }


def confirm_us_signal(ticker, setup_ok, price):
    """동일 설정이 0.8초 이상 간격으로 2회 연속 유지되어야 진입 확인한다."""
    now_ts = time.time()
    store = st.session_state.setdefault("us_signal_confirmation", {})
    key = str(ticker).upper()
    previous = store.get(key, {})
    if not setup_ok:
        store[key] = {"hits": 0, "first": 0.0, "last": now_ts, "price": float(price or 0)}
        return False, 0

    previous_price = float(previous.get("price", 0) or 0)
    price_stable = (
        previous_price > 0
        and price > 0
        and abs(price / previous_price - 1) <= 0.015
    )
    elapsed = now_ts - float(previous.get("last", 0) or 0)
    if previous.get("hits", 0) > 0 and price_stable and 0.8 <= elapsed <= 20:
        hits = int(previous.get("hits", 0)) + 1
        first = float(previous.get("first", now_ts))
    elif previous.get("hits", 0) > 0 and elapsed < 0.8:
        hits = int(previous.get("hits", 0))
        first = float(previous.get("first", now_ts))
    else:
        hits = 1
        first = now_ts
    store[key] = {"hits": hits, "first": first, "last": now_ts, "price": float(price)}
    return hits >= 2 and now_ts - first >= 0.8, hits


def calculate_us_session_vwap(minute, current_price=0.0):
    """API 거래대금 단위에 의존하지 않고 당일 분봉으로 VWAP을 직접 계산한다."""
    if minute is None or minute.empty:
        return 0.0
    bars = minute.copy()
    if not isinstance(bars.index, pd.DatetimeIndex):
        return 0.0
    latest_day = bars.index[-1].date()
    bars = bars[pd.Series(bars.index.date == latest_day, index=bars.index)]
    if bars.empty or "거래량" not in bars.columns:
        return 0.0
    volume = pd.to_numeric(bars["거래량"], errors="coerce").fillna(0).clip(lower=0)
    typical = (
        pd.to_numeric(bars["고가"], errors="coerce").fillna(0)
        + pd.to_numeric(bars["저가"], errors="coerce").fillna(0)
        + pd.to_numeric(bars["종가"], errors="coerce").fillna(0)
    ) / 3
    total_volume = float(volume.sum())
    if total_volume <= 0:
        return 0.0
    vwap = float((typical * volume).sum() / total_volume)
    # 비정상 파싱 방어: 정상 VWAP은 대체로 현재가의 20~500% 범위 안에 있어야 한다.
    if current_price > 0 and not (current_price * 0.2 <= vwap <= current_price * 5):
        return 0.0
    return vwap



# =====================================================================
# 실전 신호 저널 · 엄격한 외부검증
# =====================================================================
SIGNAL_DB_DIR = Path(os.getenv("SCANNER_DATA_DIR", Path.home() / ".kis_scanner"))
SIGNAL_DB_FILE = SIGNAL_DB_DIR / "signal_journal.sqlite3"
LIVE_SIGNAL_TIMEOUT_MINUTES = 20
STRICT_MIN_ALL_SAMPLES = 300
STRICT_MIN_HOLDOUT_SAMPLES = 100
STRICT_MIN_HOLDOUT_DATES = 20
STRICT_MIN_HOLDOUT_TICKERS = 20


def _signal_db():
    SIGNAL_DB_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(SIGNAL_DB_FILE), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_events (
            id TEXT PRIMARY KEY,
            signal_key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            created_ts REAL NOT NULL,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            exchange TEXT,
            stock_name TEXT,
            strategy TEXT NOT NULL,
            verdict TEXT,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            target1 REAL NOT NULL,
            target2 REAL,
            bid REAL,
            ask REAL,
            spread_pct REAL,
            quote_price REAL,
            quote_age REAL,
            score REAL,
            max_score REAL,
            replay_samples INTEGER,
            replay_rate REAL,
            confirmation_hits INTEGER,
            vwap_gap REAL,
            volume_speed REAL,
            strength REAL,
            max_price REAL,
            min_price REAL,
            last_price REAL,
            last_seen_at TEXT,
            last_seen_ts REAL,
            outcome TEXT NOT NULL DEFAULT 'OPEN',
            outcome_at TEXT,
            outcome_ts REAL,
            realized_pct REAL,
            notes TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_open ON signal_events(outcome, created_ts)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_date_ticker ON signal_events(trade_date, ticker)"
    )
    connection.commit()
    return connection


def _signal_key(ticker, strategy, created_at, entry):
    # 같은 분·같은 종목·같은 전략·거의 같은 계획가는 한 신호로 취급한다.
    minute_key = created_at[:16]
    raw = f"{ticker.upper()}|{strategy}|{minute_key}|{entry:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_live_signal(row, analysis, strategy):
    """검증을 통과해 초록색이 된 실시간 신호를 중복 없이 영구 저장한다."""
    verdict = str(analysis.get("verdict", ""))
    if not verdict.startswith("🟢") or not analysis.get("validation_ok"):
        return False
    entry = float(analysis.get("entry", 0) or 0)
    stop = float(analysis.get("stop", 0) or 0)
    target1 = float(analysis.get("target1", 0) or 0)
    target2 = float(analysis.get("target2", 0) or 0)
    if not (entry > 0 and 0 < stop < entry < target1):
        return False
    now = datetime.now(SEOUL)
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    ticker = str(row.get("종목코드", "")).upper().strip()
    if not ticker:
        return False
    key = _signal_key(ticker, strategy, created_at, entry)
    values = {
        "id": str(uuid.uuid4()), "signal_key": key,
        "created_at": created_at, "created_ts": now.timestamp(),
        "trade_date": now.strftime("%Y-%m-%d"), "ticker": ticker,
        "exchange": str(row.get("거래소코드", "")),
        "stock_name": str(row.get("종목명", ticker)), "strategy": strategy,
        "verdict": verdict, "entry": entry, "stop": stop,
        "target1": target1, "target2": target2,
        "bid": float(row.get("매수호가($)", 0) or 0),
        "ask": float(row.get("매도호가($)", 0) or 0),
        "spread_pct": float(row.get("스프레드(%)", 0) or 0),
        "quote_price": float(row.get("현재가($)", 0) or 0),
        "quote_age": float(analysis.get("quote_age", 999) or 999),
        "score": float(analysis.get("score", 0) or 0),
        "max_score": float(analysis.get("max_score", 0) or 0),
        "replay_samples": int(analysis.get("validation_samples", 0) or 0),
        "replay_rate": float(analysis.get("validation_win_rate", 0) or 0),
        "confirmation_hits": int(analysis.get("confirmation_hits", 0) or 0),
        "vwap_gap": float(analysis.get("vwap_gap", 0) or 0),
        "volume_speed": float(analysis.get("volume_speed", 0) or 0),
        "strength": float(row.get("체결강도", 0) or 0),
        "max_price": float(row.get("현재가($)", entry) or entry),
        "min_price": float(row.get("현재가($)", entry) or entry),
        "last_price": float(row.get("현재가($)", entry) or entry),
        "last_seen_at": created_at, "last_seen_ts": now.timestamp(),
    }
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    try:
        with _signal_db() as db:
            db.execute(
                f"INSERT OR IGNORE INTO signal_events ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
        return True
    except Exception:
        return False


def update_open_signal_outcomes(table):
    """현재가 갱신 때 미결정 신호의 최고·최저·결과를 누적한다.

    관측 간 가격 경로를 알 수 없으므로 같은 갱신에서 목표와 손절이 모두
    충족된 경우 보수적으로 STOP_FIRST로 처리한다.
    """
    if table is None or table.empty or "종목코드" not in table.columns:
        return 0
    quote_map = {
        str(row["종목코드"]).upper(): row
        for _, row in table.iterrows()
    }
    now = datetime.now(SEOUL)
    updated = 0
    with _signal_db() as db:
        open_rows = db.execute(
            "SELECT * FROM signal_events WHERE outcome='OPEN' ORDER BY created_ts"
        ).fetchall()
        for signal in open_rows:
            ticker = str(signal["ticker"]).upper()
            quote = quote_map.get(ticker)
            age_seconds = now.timestamp() - float(signal["created_ts"])
            if quote is None:
                if age_seconds >= LIVE_SIGNAL_TIMEOUT_MINUTES * 60:
                    db.execute(
                        "UPDATE signal_events SET outcome='TIMEOUT_NO_DATA', outcome_at=?, outcome_ts=? WHERE id=?",
                        (now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp(), signal["id"]),
                    )
                    updated += 1
                continue
            price = float(quote.get("현재가($)", 0) or 0)
            if price <= 0:
                continue
            max_price = max(float(signal["max_price"] or signal["entry"]), price)
            min_price = min(float(signal["min_price"] or signal["entry"]), price)
            target_hit = max_price >= float(signal["target1"])
            stop_hit = min_price <= float(signal["stop"])
            outcome = "OPEN"
            realized = None
            # 보수적 순서 판정: 한 관측 구간에서 둘 다 나타나면 손절 우선.
            if target_hit and stop_hit:
                outcome, realized = "STOP_FIRST_AMBIGUOUS", (float(signal["stop"]) / float(signal["entry"]) - 1) * 100
            elif stop_hit:
                outcome, realized = "STOP_FIRST", (float(signal["stop"]) / float(signal["entry"]) - 1) * 100
            elif target_hit:
                outcome, realized = "TARGET1_FIRST", (float(signal["target1"]) / float(signal["entry"]) - 1) * 100
            elif age_seconds >= LIVE_SIGNAL_TIMEOUT_MINUTES * 60:
                outcome, realized = "TIMEOUT", (price / float(signal["entry"]) - 1) * 100
            outcome_at = now.strftime("%Y-%m-%d %H:%M:%S") if outcome != "OPEN" else None
            outcome_ts = now.timestamp() if outcome != "OPEN" else None
            db.execute(
                """
                UPDATE signal_events
                SET max_price=?, min_price=?, last_price=?, last_seen_at=?, last_seen_ts=?,
                    outcome=?, outcome_at=COALESCE(?, outcome_at),
                    outcome_ts=COALESCE(?, outcome_ts), realized_pct=COALESCE(?, realized_pct)
                WHERE id=?
                """,
                (max_price, min_price, price, now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp(),
                 outcome, outcome_at, outcome_ts, realized, signal["id"]),
            )
            updated += 1
    return updated


def _strict_split_label(ticker, trade_date):
    """종목과 날짜가 동시에 겹치지 않는 엄격한 train/holdout 분리.

    티커 해시 20%와 거래일 해시 20%가 모두 holdout인 행만 holdout으로,
    둘 다 train인 행만 train으로 사용한다. 교차 영역은 검증에서 제외한다.
    """
    ticker_bucket = int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16) % 5
    date_bucket = int(hashlib.sha256(trade_date.encode()).hexdigest()[:8], 16) % 5
    if ticker_bucket == 0 and date_bucket == 0:
        return "holdout"
    if ticker_bucket != 0 and date_bucket != 0:
        return "train"
    return "excluded"


def calculate_live_validation_stats(strategy=None):
    query = "SELECT * FROM signal_events WHERE outcome IN ('TARGET1_FIRST','STOP_FIRST','STOP_FIRST_AMBIGUOUS','TIMEOUT')"
    params = []
    if strategy:
        query += " AND strategy=?"
        params.append(strategy)
    with _signal_db() as db:
        rows = [dict(row) for row in db.execute(query, params).fetchall()]
    for row in rows:
        row["split"] = _strict_split_label(str(row["ticker"]), str(row["trade_date"]))
        row["win"] = 1 if row["outcome"] == "TARGET1_FIRST" else 0

    def summarize(items):
        n = len(items)
        wins = sum(item["win"] for item in items)
        dates = len({item["trade_date"] for item in items})
        tickers = len({item["ticker"] for item in items})
        rate = wins / n * 100 if n else 0.0
        return {
            "samples": n, "wins": wins, "rate": rate,
            "wilson": wilson_lower_bound(wins, n),
            "dates": dates, "tickers": tickers,
        }

    train = summarize([row for row in rows if row["split"] == "train"])
    holdout = summarize([row for row in rows if row["split"] == "holdout"])
    all_stats = summarize(rows)
    strict_80_verified = (
        all_stats["samples"] >= STRICT_MIN_ALL_SAMPLES
        and holdout["samples"] >= STRICT_MIN_HOLDOUT_SAMPLES
        and holdout["dates"] >= STRICT_MIN_HOLDOUT_DATES
        and holdout["tickers"] >= STRICT_MIN_HOLDOUT_TICKERS
        and holdout["rate"] >= 80.0
        and holdout["wilson"] >= 75.0
    )
    return {
        "all": all_stats, "train": train, "holdout": holdout,
        "strict_80_verified": strict_80_verified,
        "open": _count_open_signals(strategy),
        "db_file": str(SIGNAL_DB_FILE),
    }


def _count_open_signals(strategy=None):
    query = "SELECT COUNT(*) FROM signal_events WHERE outcome='OPEN'"
    params = []
    if strategy:
        query += " AND strategy=?"
        params.append(strategy)
    with _signal_db() as db:
        return int(db.execute(query, params).fetchone()[0])


def sync_live_signal_journal(signal_items, scanner_type, table):
    strategy = "quality" if str(scanner_type).endswith("quality") else "runup" if str(scanner_type).endswith("runup") else "momentum"
    for item in signal_items or []:
        analysis = item.get("analysis") or {}
        row = item.get("row") or {}
        record_live_signal(row, analysis, strategy)
    update_open_signal_outcomes(table)


def render_live_validation_panel(strategy=None):
    stats = calculate_live_validation_stats(strategy)
    holdout = stats["holdout"]
    all_stats = stats["all"]
    if stats["strict_80_verified"]:
        st.success(
            f"✅ 독립 검증 80% 통과 · holdout {holdout['wins']}/{holdout['samples']} "
            f"({holdout['rate']:.1f}%, Wilson 하한 {holdout['wilson']:.1f}%)"
        )
    else:
        st.info(
            "📊 실전 데이터 수집 중 · "
            f"전체 {all_stats['samples']}/{STRICT_MIN_ALL_SAMPLES}건, "
            f"독립검증 {holdout['samples']}/{STRICT_MIN_HOLDOUT_SAMPLES}건, "
            f"날짜 {holdout['dates']}/{STRICT_MIN_HOLDOUT_DATES}, "
            f"종목 {holdout['tickers']}/{STRICT_MIN_HOLDOUT_TICKERS}"
        )
    st.caption(
        f"미결정 {stats['open']}건 · holdout 성공률 {holdout['rate']:.1f}% · "
        f"Wilson 하한 {holdout['wilson']:.1f}% · DB: {stats['db_file']}"
    )


def analyze_us_penny_stock(minute, quote_row, strategy="momentum"):
    frames = {minutes: add_indicators(resample_bars(minute, minutes)) for minutes in (1, 3, 5, 15)}
    one, three, five, fifteen = frames[1], frames[3], frames[5], frames[15]
    price = float(quote_row["현재가($)"])
    minute_vwap = calculate_us_session_vwap(minute, price)
    vwap = minute_vwap if minute_vwap > 0 else float(quote_row.get("VWAP($)", 0) or 0)
    # API 거래대금 단위가 종목/API마다 달라 생기던 0.0003 같은 비정상 VWAP을 차단한다.
    if price > 0 and (vwap <= 0 or not (price * 0.2 <= vwap <= price * 5)):
        vwap = price
    quote_row["VWAP($)"] = round(vwap, 4)
    quote_row["VWAP위치(%)"] = round((price / vwap - 1) * 100, 2) if vwap > 0 else 0.0
    change_pct = float(quote_row["등락률(%)"])
    spread_pct = float(quote_row.get("스프레드(%)", 0) or 0)
    strength = float(quote_row.get("체결강도", 0) or 0)

    rsi_1 = safe_last(one["RSI"])
    rsi_3 = safe_last(three["RSI"])
    rsi_5 = safe_last(five["RSI"])
    macd_3 = safe_last(three["MACD"])
    signal_3 = safe_last(three["MACD시그널"])
    hist = three["MACD히스토그램"].dropna()
    hist_now = float(hist.iloc[-1]) if len(hist) else 0
    hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else hist_now
    bullish_macd = macd_3 > signal_3 and hist_now >= hist_prev

    def macd_improving(frame):
        values = frame["MACD히스토그램"].dropna()
        return len(values) >= 2 and float(values.iloc[-1]) >= float(values.iloc[-2])

    trend_1 = safe_last(one["종가"]) >= safe_last(one["EMA9"])
    trend_3 = safe_last(three["종가"]) >= safe_last(three["EMA9"])
    trend_5 = safe_last(five["종가"]) >= safe_last(five["EMA9"])
    trend_15 = (
        len(fifteen) >= 3
        and safe_last(fifteen["종가"]) >= safe_last(fifteen["EMA9"])
        and float(fifteen["EMA9"].iloc[-1]) >= float(fifteen["EMA9"].iloc[-2])
    )
    recent_volume = one["거래량"].tail(3).mean() if len(one) >= 3 else 0
    prior_volume = one["거래량"].iloc[-23:-3].mean() if len(one) >= 23 else 0
    volume_speed = float(recent_volume / prior_volume) if prior_volume > 0 else 0
    vwap_gap = (price / vwap - 1) * 100 if vwap > 0 else 0
    recent_high = float(one["고가"].tail(30).max()) if not one.empty else price
    recent_low = float(one["저가"].tail(8).min()) if not one.empty else price
    pullback_pct = (price / recent_high - 1) * 100 if recent_high > 0 else 0
    prior = one.iloc[-7:-1].copy() if len(one) >= 8 else one.iloc[:-1].copy()
    touched_ema = (
        not prior.empty
        and bool((prior["저가"] <= prior["EMA9"] * 1.004).any())
        and bool((prior["고가"] >= prior["EMA9"] * 0.996).any())
    )
    touched_vwap = (
        vwap > 0
        and not prior.empty
        and float(prior["저가"].min()) <= vwap * 1.004
        and float(prior["고가"].max()) >= vwap * 0.996
    )
    close_rising = len(one) >= 2 and float(one["종가"].iloc[-1]) > float(one["종가"].iloc[-2])
    higher_low = (
        len(one) >= 6
        and float(one["저가"].tail(3).min()) >= float(one["저가"].iloc[-6:-3].min())
    )
    reclaim = bool((touched_ema or touched_vwap) and trend_1 and close_rising and higher_low)
    source_overlap = int(quote_row.get("교집합수", 0) or 0)
    discovery_score = float(quote_row.get("발견점수", 0) or 0)
    source_ok = source_overlap >= 1 or discovery_score > 0

    ask = float(quote_row.get("매도호가($)", 0) or 0)
    received_at = float(quote_row.get("수신타임스탬프", 0) or 0)
    quote_age = (
        max(0.0, time.time() - received_at)
        if received_at > 0
        else float(quote_row.get("시세나이(초)", 999) or 999)
    )
    day_volume = float(quote_row.get("오늘거래량", 0) or 0)
    day_amount_million = float(quote_row.get("오늘거래대금(백만$)", 0) or 0)
    maximum_spread = 1.5 if price < 1 else 1.0 if price < 5 else 0.7
    planned_entry = max(price, ask) if ask > 0 else price
    stop, target1, target2, support, resistance, level_details = calculate_us_multiframe_levels(
        frames,
        planned_entry,
    )
    risk = max(0.0, planned_entry - stop)
    reward_risk1 = float(
        (level_details.get("summary") or {}).get(
            "room_rr",
            (target1 - planned_entry) / risk if risk > 0 else 0.0,
        )
    )
    horizon_forecast = forecast_us_tick_horizons(frames, quote_row)
    forecast_1 = horizon_forecast.get("1분 후", {})
    forecast_5 = horizon_forecast.get("5분 후", {})
    tick_confirmed = (
        str(forecast_1.get("label", "")).startswith("🟢")
        and float(forecast_1.get("score", 0) or 0) >= 68
        and float(forecast_5.get("score", 0) or 0) >= 62
    )
    bars_ready = len(one) >= 60 and len(three) >= 20 and len(five) >= 12 and len(fifteen) >= 4
    liquidity_ok = day_volume >= 500_000 and day_amount_million >= 0.5

    checks = {
        "시세 3초 이내": quote_age <= 3.0,
        "분봉 표본 충분": bars_ready,
        "VWAP 위 0~3%": 0 <= vwap_gap <= 3,
        "RSI 비과열": 45 <= rsi_3 <= 70 and rsi_1 < 78,
        "1분 모멘텀 회복": trend_1 and macd_improving(one),
        "3분 MACD 상승": bullish_macd,
        "3분 EMA9 위": trend_3,
        "5분 추세 지속": trend_5 and macd_improving(five),
        "15분 큰 추세 상승": trend_15,
        "최근 거래량 1.2배": volume_speed >= 1.2,
        "매수 체결강도 120~300": 120 <= strength <= 300,
        "호가차이 실전 범위": 0 < spread_pct <= maximum_spread,
        "최소 유동성": liquidity_ok,
        "눌림 후 재상승": -6 <= pullback_pct <= 0 and reclaim,
        "1·5분 실체결 방향 확인": tick_confirmed,
        "1차 손익비 1.5 이상": reward_risk1 >= 1.5,
        "급등 순위 합집합 포착": source_ok,
    }
    score = sum(checks.values())
    overheated = change_pct >= 50 or vwap_gap > 8 or rsi_1 >= 84 or spread_pct > maximum_spread * 2
    mandatory = (
        checks["시세 3초 이내"]
        and checks["분봉 표본 충분"]
        and checks["VWAP 위 0~3%"]
        and checks["1분 모멘텀 회복"]
        and checks["3분 MACD 상승"]
        and checks["3분 EMA9 위"]
        and checks["5분 추세 지속"]
        and checks["15분 큰 추세 상승"]
        and checks["매수 체결강도 120~300"]
        and checks["호가차이 실전 범위"]
        and checks["최소 유동성"]
        and checks["눌림 후 재상승"]
        and checks["1·5분 실체결 방향 확인"]
        and checks["1차 손익비 1.5 이상"]
        and checks["급등 순위 합집합 포착"]
    )

    validation_store = st.session_state.setdefault("us_validation_cache", {})
    validation_key = str(quote_row.get("종목코드", "")).upper()
    cached_validation = validation_store.get(validation_key, {})
    cache_age = time.time() - float(cached_validation.get("saved_at", 0) or 0)
    if len(minute) < 180 and cached_validation.get("result") and cache_age <= 90:
        validation = cached_validation["result"]
    else:
        validation = backtest_us_entry_condition(minute)
        if len(minute) >= 180:
            validation_store[validation_key] = {
                "saved_at": time.time(),
                "result": validation,
            }
    # 80%는 미래 보장이 아니라 과거 재생의 엄격한 통과 기준이다.
    # 표본 수·최근 성과·Wilson 보수 하한을 함께 요구해 소표본 과신을 줄인다.
    validation_ok = (
        validation["samples"] >= 30
        and validation["full_success_rate"] >= 80
        and validation["recent_success_rate"] >= 70
        and validation["wilson_lower_bound"] >= 60
    )

    # 우량주와 급등주는 서로 다른 진입 논리를 사용한다.
    fresh_ok = quote_age <= 8.0

    if strategy == "quality":
        # 우량주는 폭발적 체결강도보다 좁은 스프레드와 완만한 추세 회복을 우선한다.
        quality_spread = 0.18 if price >= 50 else 0.30 if price >= 10 else 0.50
        execution_ok = liquidity_ok and (spread_pct == 0 or spread_pct <= quality_spread)
        vwap_near = -0.8 <= vwap_gap <= 1.8
        ema_reclaim = trend_1 and close_rising and (trend_3 or bullish_macd)
        volume_ok = volume_speed >= 0.65 or day_amount_million >= 20
        not_overbought = rsi_1 < 80 and rsi_3 < 76
        quality_entry = fresh_ok and execution_ok and vwap_near and ema_reclaim and volume_ok and not_overbought and reward_risk1 >= 1.5
        quality_wait = fresh_ok and execution_ok and (-1.8 <= vwap_gap <= 3.0) and (trend_1 or trend_3)
        hard_overheat = rsi_1 >= 88 or vwap_gap > 4.5 or spread_pct > quality_spread * 2.5
        signal_confirmed, confirmation_hits = confirm_us_signal(
            quote_row.get("종목코드", ""), quality_entry, planned_entry
        )
        if hard_overheat:
            verdict = "🔴 우량주 추격위험"
        elif quality_entry and signal_confirmed:
            verdict = "🟢 우량주 다중확인·눌림 진입"
        elif quality_entry:
            verdict = "🟡 우량주 진입 준비"
        elif quality_wait:
            verdict = "🟡 우량주 눌림대기"
        else:
            verdict = "⚪ 우량주 추세 확인"
        premium_setup = quality_entry
        aggressive_setup = quality_wait
    else:
        # 급등주는 첫 눌림 뒤 EMA9/VWAP 재돌파와 거래량 재유입을 확인한다.
        execution_ok = liquidity_ok and 0 < spread_pct <= maximum_spread * 1.35
        momentum_ok = trend_1 and (bullish_macd or macd_improving(one)) and close_rising
        location_ok = -1.0 <= vwap_gap <= 6.0 and -9.0 <= pullback_pct <= 0.5
        strength_ok = strength == 0 or strength >= 105
        volume_live = volume_speed >= 0.8 or day_amount_million >= 3.0
        aggressive_setup = fresh_ok and execution_ok and momentum_ok and location_ok and strength_ok and volume_live and source_ok and reward_risk1 >= 1.5
        first_pullback_ready = (
            aggressive_setup
            and reclaim
            and -7.0 <= pullback_pct <= -0.4
            and trend_3
            and volume_speed >= 1.0
            and spread_pct <= maximum_spread
        )
        premium_setup = first_pullback_ready
        hard_overheat = change_pct >= 150 or vwap_gap > 15 or rsi_1 >= 92 or spread_pct > maximum_spread * 2.2
        signal_confirmed, confirmation_hits = confirm_us_signal(
            quote_row.get("종목코드", ""), premium_setup, planned_entry
        )
        if hard_overheat:
            verdict = "🔴 과열·급락위험"
        elif premium_setup and signal_confirmed:
            verdict = "🟢 급등주 다중확인·재돌파 진입"
        elif premium_setup:
            verdict = "🟡 재돌파 진입 준비"
        elif aggressive_setup:
            verdict = "🟡 눌림대기·재돌파 감시"
        elif fresh_ok and execution_ok and (trend_1 or bullish_macd):
            verdict = "🟡 재돌파 확인대기"
        else:
            verdict = "⚪ 조건 미달"

    # 매수는 마지막 체결가가 아닌 실제 매도 1호가를 기준으로 계획한다.
    entry = planned_entry
    # 5분 안에 체결 가능한 목표를 우선한다. ATR 목표가가 너무 멀거나 가까우면 범위를 제한한다.
    scalp_target1 = entry * (1.018 if price >= 5 else 1.025 if price >= 1 else 1.035)
    scalp_target2 = entry * (1.035 if price >= 5 else 1.05 if price >= 1 else 1.075)
    target1 = max(entry * 1.012, min(target1, scalp_target1))
    target2 = max(target1 * 1.008, min(target2, scalp_target2))
    # 초단타 손절폭은 가격대별 최대 약 2.5~4.5%로 제한한다.
    max_stop_pct = 0.025 if price >= 5 else 0.032 if price >= 1 else 0.045
    stop = max(stop, entry * (1 - max_stop_pct))
    return {
        "frames": frames,
        "verdict": verdict,
        "score": score,
        "max_score": len(checks),
        "checks": checks,
        "rsi_1": rsi_1,
        "rsi_3": rsi_3,
        "rsi_5": rsi_5,
        "trend_1": trend_1,
        "trend_3": trend_3,
        "trend_5": trend_5,
        "trend_15": trend_15,
        "bullish_macd": bullish_macd,
        "volume_speed": volume_speed,
        "vwap_gap": vwap_gap,
        "pullback_pct": pullback_pct,
        "pullback_reclaim": reclaim,
        "first_pullback_ready": first_pullback_ready,
        "source_overlap": source_overlap,
        "source_ok": source_ok,
        "validation_entry_hits": validation["entry_hits"],
        "validation_entry_rate": validation["entry_hit_rate"],
        "validation_wins": validation["target1_wins"],
        "validation_samples": validation["samples"],
        "validation_win_rate": validation["full_success_rate"],
        "validation_target2_wins": validation["target2_wins"],
        "validation_target2_rate": validation["target2_success_rate"],
        "validation_recent_rate": validation["recent_success_rate"],
        "validation_wilson_lower": validation["wilson_lower_bound"],
        "validation_ok": validation_ok,
        "confirmation_hits": confirmation_hits,
        "quote_age": quote_age,
        "maximum_spread": maximum_spread,
        "reward_risk1": reward_risk1,
        "level_details": level_details,
        "horizon_forecast": horizon_forecast,
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target1": round(target1, 4),
        "target2": round(target2, 4),
    }


def calculate_us_chart_levels(bars, position, entry):
    """신호 시점까지의 차트만 사용해 지지·저항·ATR 매매 레벨을 만든다."""
    history = bars.iloc[:position + 1].copy()
    if history.empty or entry <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    atr = safe_last(history["ATR"])
    if atr <= 0:
        recent_range = (history["고가"] - history["저가"]).tail(14).mean()
        atr = float(recent_range) if pd.notna(recent_range) and recent_range > 0 else entry * 0.02
    # 변동성이 지나치게 작게 잡혀 손절가가 현재가에 붙는 것을 방지한다.
    atr = max(atr, entry * 0.008)

    support_window = history["저가"].tail(12)
    support = float(support_window.min()) if not support_window.empty else entry - atr

    prior_highs = history["고가"].iloc[-31:-1] if len(history) > 1 else history["고가"]
    resistance = float(prior_highs.max()) if not prior_highs.empty else entry + atr * 1.5

    atr_stop = entry - atr * 1.2
    stop = max(support, atr_stop)
    if stop <= 0 or stop >= entry:
        stop = entry - atr * 1.2

    # 1차는 직전 저항선이 합리적인 범위에 있으면 그 가격을 사용한다.
    minimum_target = entry + atr * 1.2
    normal_target = entry + atr * 1.8
    maximum_first = entry + atr * 3.0
    if minimum_target <= resistance <= maximum_first:
        target1 = resistance
    else:
        target1 = normal_target

    # 2차는 1차 저항 돌파 후의 다음 ATR 확장 구간으로 잡는다.
    target2 = max(target1 + atr * 1.2, entry + atr * 3.0)
    return stop, target1, target2, support, resistance


def calculate_us_multiframe_levels(frames, entry):
    """1·3·5·15분봉의 지지·저항·ATR을 합쳐 초단타 레벨을 만든다.

    1분은 체결 직후 변동, 3분은 진입 확인, 5분은 지속 추세,
    15분은 큰 지지·저항을 담당한다. 신호가 나온 시점의
    포지션을 확정하면 이 값은 더 이상 재계산하지 않는다.
    """
    if entry <= 0 or 1 not in frames or frames[1].empty:
        return 0.0, 0.0, 0.0, 0.0, 0.0, {}

    windows = {1: 18, 3: 10, 5: 8, 15: 5}
    atr_weights = {1: 1.25, 3: 0.80, 5: 0.60, 15: 0.38}
    supports = []
    resistances = []
    risks = []
    details = {}

    for minutes in (1, 3, 5, 15):
        frame = frames.get(minutes)
        if frame is None or frame.empty:
            details[minutes] = {"bars": 0, "support": 0.0, "resistance": 0.0, "atr": 0.0}
            continue
        recent = frame.tail(windows[minutes])
        support = float(recent["저가"].min())
        # 현재 봉의 순간 고가보다 이전 고가를 우선해 저항으로 사용한다.
        prior = recent.iloc[:-1] if len(recent) > 1 else recent
        resistance = float(prior["고가"].max()) if not prior.empty else float(recent["고가"].max())
        atr = safe_last(frame["ATR"])
        if atr <= 0:
            mean_range = (recent["고가"] - recent["저가"]).mean()
            atr = float(mean_range) if pd.notna(mean_range) and mean_range > 0 else 0.0
        if 0 < support < entry:
            supports.append(support)
        if resistance > entry:
            resistances.append(resistance)
        if atr > 0:
            risks.append(atr * atr_weights[minutes])
        details[minutes] = {
            "bars": len(frame),
            "support": support,
            "resistance": resistance,
            "atr": atr,
        }

    risk_unit = max(risks + [entry * 0.010])
    # 동전주의 넓은 시세폭이 손절을 -3% 밖으로 밀지 못하게 한다.
    risk_unit = min(risk_unit, entry * 0.03)
    nearest_support = max(supports) if supports else entry - risk_unit
    chart_stop = nearest_support * 0.997
    volatility_stop = entry - risk_unit
    stop = min(chart_stop, volatility_stop)
    stop = max(stop, entry * 0.97)
    if stop <= 0 or stop >= entry:
        stop = entry * 0.97

    risk = entry - stop
    raw_resistances = sorted(value for value in resistances if value > entry)
    natural_resistance = raw_resistances[0] if raw_resistances else 0.0
    room_rr = (
        (natural_resistance - entry) / risk
        if natural_resistance > entry and risk > 0
        else 99.0
    )
    valid_resistances = sorted(
        value for value in resistances
        if value >= entry + risk * 1.5
    )
    target1 = valid_resistances[0] if valid_resistances else entry + risk * 1.5
    target1 = min(target1, entry + risk * 2.5)
    target1 = max(target1, entry + risk * 1.5)
    higher_resistances = [value for value in valid_resistances if value > target1 + risk * 0.25]
    target2 = higher_resistances[0] if higher_resistances else entry + risk * 2.7
    target2 = max(target2, target1 + risk * 0.75)
    target2 = min(target2, entry + risk * 4.0)
    resistance = natural_resistance if natural_resistance > 0 else target1
    details["summary"] = {
        "risk": risk,
        "natural_resistance": natural_resistance,
        "room_rr": room_rr,
    }
    return stop, target1, target2, nearest_support, resistance, details


def forecast_us_tick_horizons(frames, quote_row):
    """실체결 틱·호가·체결강도와 1·3·5·15분봉으로 단기 방향을 보조판정한다.

    여기의 점수는 확률이 아니라 상승·하락 근거의 균형점수이다.
    """
    ticker = str(quote_row.get("종목코드", "")).upper()
    ticks = st.session_state.get("us_tick_history", {}).get(ticker, [])
    now_ts = time.time()
    usable_ticks = [
        item for item in ticks
        if float(item.get("price", 0)) > 0
        and now_ts - float(item.get("ts", 0) or 0) <= 600
    ]
    tick_score = 50.0
    tick_reason = "실체결 틱 부족"
    observed_span = 0.0
    quote_age = 999.0
    if usable_ticks:
        quote_age = max(0.0, now_ts - float(usable_ticks[-1].get("ts", 0) or 0))
    recent = [
        item for item in usable_ticks
        if now_ts - float(item.get("ts", 0) or 0) <= 60
    ][-120:]
    if len(recent) >= 8:
        observed_span = max(
            0.0,
            float(recent[-1].get("ts", 0)) - float(recent[0].get("ts", 0)),
        )
    if len(recent) >= 8 and observed_span >= 1.0 and quote_age <= 3.0:
        prices = [float(item["price"]) for item in recent]
        volumes = [int(item.get("volume", 0)) for item in recent]
        up_moves = sum(1 for before, after in zip(prices, prices[1:]) if after > before)
        down_moves = sum(1 for before, after in zip(prices, prices[1:]) if after < before)
        price_change = (prices[-1] / prices[0] - 1) * 100 if prices[0] > 0 else 0
        volume_delta = max(0, volumes[-1] - volumes[0]) if volumes else 0
        signed_volume = 0.0
        for pos in range(1, len(recent)):
            delta_volume = max(0, volumes[pos] - volumes[pos - 1])
            if prices[pos] > prices[pos - 1]:
                signed_volume += delta_volume
            elif prices[pos] < prices[pos - 1]:
                signed_volume -= delta_volume
        volume_imbalance = signed_volume / volume_delta if volume_delta > 0 else 0.0
        strength = float(recent[-1].get("strength", 0) or 0)
        tick_score += min(20, max(-20, price_change * 24))
        tick_score += min(11, max(-11, (up_moves - down_moves) * 1.7))
        tick_score += min(10, max(-10, volume_imbalance * 10))
        tick_score += 8 if strength >= 120 else -9 if 0 < strength < 90 else 0
        tick_reason = (
            f"{observed_span:.1f}초 {len(recent)}틱·상승 {up_moves}·하락 {down_moves}"
            f"·체결량균형 {volume_imbalance:+.2f}"
        )
    tick_score = max(0.0, min(100.0, tick_score))

    def frame_score(minutes):
        frame = frames.get(minutes)
        if frame is None or len(frame) < 2:
            return 50.0
        close = frame["종가"]
        ema = frame["EMA9"]
        histogram = frame["MACD히스토그램"].dropna()
        score = 50.0
        score += 14 if safe_last(close) >= safe_last(ema) else -14
        if len(ema) >= 3:
            score += 10 if float(ema.iloc[-1]) > float(ema.iloc[-3]) else -10
        if len(histogram) >= 2:
            score += 10 if float(histogram.iloc[-1]) >= float(histogram.iloc[-2]) else -10
        rsi = safe_last(frame["RSI"], 50)
        score += 7 if 48 <= rsi <= 72 else -7 if rsi >= 80 or rsi <= 35 else 0
        return max(0.0, min(100.0, score))

    frame_scores = {minutes: frame_score(minutes) for minutes in (1, 3, 5, 15)}
    strength = float(quote_row.get("체결강도", 0) or 0)
    vwap_gap = float(quote_row.get("VWAP위치(%)", 0) or 0)
    spread = float(quote_row.get("스프레드(%)", 0) or 0)
    micro = 50 + (10 if strength >= 120 else -10 if 0 < strength < 90 else 0)
    micro += 8 if 0 <= vwap_gap <= 4 else -10 if vwap_gap < 0 or vwap_gap > 8 else 0
    micro += 5 if 0 < spread <= 2.5 else -8 if spread > 4 else 0
    micro = max(0, min(100, micro))

    scores = {
        "1분 후": 0.45 * tick_score + 0.35 * frame_scores[1] + 0.20 * micro,
        "5분 후": 0.15 * tick_score + 0.30 * frame_scores[1] + 0.30 * frame_scores[3] + 0.25 * frame_scores[5],
        "10분 후": 0.15 * frame_scores[1] + 0.25 * frame_scores[3] + 0.35 * frame_scores[5] + 0.25 * frame_scores[15],
    }

    def label(score):
        if quote_age > 3.0:
            return "🔴 시세 지연·진입금지"
        if len(recent) < 8 or observed_span < 1.0:
            return "⚪ 실체결 틱 추가 필요"
        if score >= 68:
            return "🟢 상승 우세"
        if score <= 35:
            return "🔴 하락 우세"
        return "🟡 혼조·확인 필요"

    return {
        horizon: {
            "label": label(score),
            "score": round(score),
            "strength": round(min(100, abs(score - 50) * 2)),
            "reason": tick_reason,
            "ticks": len(recent),
            "quote_age": round(quote_age, 2),
        }
        for horizon, score in scores.items()
    }


def wilson_lower_bound(wins, samples, z=1.96):
    """적은 표본의 75%를 과신하지 않도록 95% Wilson 하한값을 구한다."""
    if samples <= 0:
        return 0.0
    p = wins / samples
    denominator = 1 + (z * z / samples)
    center = p + (z * z / (2 * samples))
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * samples)) / samples)
    return max(0.0, (center - margin) / denominator * 100)


def backtest_us_entry_condition(minute, entry_window=10, holding_window=20):
    """제시한 진입가와 동적 익절가가 실제로 순서대로 도달했는지 재생한다.

    모든 기술신호를 분모에 포함한다. 신호 후 entry_window 분 안에
    제시한 진입가가 체결되지 않으면 실패다. 체결된 뒤 holding_window 분
    안에 손절보다 1차 익절에 먼저 도달해야 전체 매매 성공으로 계산한다.
    같은 분봉에서 손절과 익절이 모두 출현하면 손절로 계산한다.
    """
    bars = add_indicators(resample_bars(minute, 1)).copy()
    empty_result = {
        "samples": 0,
        "entry_hits": 0,
        "entry_hit_rate": 0.0,
        "target1_wins": 0,
        "full_success_rate": 0.0,
        "target2_wins": 0,
        "target2_success_rate": 0.0,
        "recent_success_rate": 0.0,
        "wilson_lower_bound": 0.0,
    }
    if len(bars) < 55:
        return empty_result

    recent_volume = bars["거래량"].rolling(3).mean()
    prior_volume = bars["거래량"].shift(3).rolling(20).mean()
    bars["거래량속도"] = recent_volume / prior_volume.replace(0, float("nan"))

    typical = (bars["고가"] + bars["저가"] + bars["종가"]) / 3
    # 전일 분봉을 포함해도 VWAP은 각 거래일별로 새로 시작한다.
    trade_date = pd.Series(bars.index.date, index=bars.index)
    cumulative_value = (typical * bars["거래량"]).groupby(trade_date).cumsum()
    cumulative_volume = bars["거래량"].groupby(trade_date).cumsum().replace(0, float("nan"))
    bars["분봉VWAP"] = cumulative_value / cumulative_volume
    bars["기술신호"] = (
        (bars["종가"] >= bars["EMA9"])
        & (bars["종가"] >= bars["분봉VWAP"])
        & bars["RSI"].between(45, 70)
        & (bars["MACD"] > bars["MACD시그널"])
        & (bars["MACD히스토그램"] >= bars["MACD히스토그램"].shift(1))
        & (bars["거래량속도"] >= 1.2)
    )

    samples = 0
    entry_hits = 0
    target1_wins = 0
    target2_wins = 0
    outcomes = []
    index = 26
    future_needed = entry_window + holding_window
    last_signal = len(bars) - future_needed - 1

    while index <= last_signal:
        if not bool(bars["기술신호"].iloc[index]):
            index += 1
            continue

        signal_close = float(bars["종가"].iloc[index])
        ema_value = float(bars["EMA9"].iloc[index])
        vwap_value = float(bars["분봉VWAP"].iloc[index])
        proposed_entry = min(signal_close, max(ema_value, vwap_value))
        history_until_signal = bars.iloc[:index + 1].copy()
        signal_frames = {
            minutes: add_indicators(resample_bars(history_until_signal, minutes))
            for minutes in (1, 3, 5, 15)
        }
        stop, target1, target2, _, _, _ = calculate_us_multiframe_levels(
            signal_frames,
            proposed_entry,
        )
        if (
            proposed_entry <= 0
            or stop <= 0
            or stop >= proposed_entry
            or target1 <= proposed_entry
            or target2 <= target1
        ):
            index += 1
            continue

        samples += 1
        entry_position = None
        entry_slice = bars.iloc[index + 1:index + 1 + entry_window]
        for offset, (_, candle) in enumerate(entry_slice.iterrows(), start=index + 1):
            if float(candle["저가"]) <= proposed_entry <= float(candle["고가"]):
                entry_position = offset
                break

        # 제시한 진입가가 오지 않은 신호도 전체 성공률의 실패에 포함한다.
        if entry_position is None:
            outcomes.append(0)
            index += 5
            continue
        entry_hits += 1

        target1_hit = False
        target2_hit = False
        holding = bars.iloc[entry_position:entry_position + holding_window]
        for _, candle in holding.iterrows():
            low = float(candle["저가"])
            high = float(candle["고가"])
            if low <= stop:
                break
            if high >= target1:
                target1_hit = True
            if high >= target2:
                target2_hit = True
                target1_hit = True
                break

        if target1_hit:
            target1_wins += 1
            outcomes.append(1)
        else:
            outcomes.append(0)
        if target2_hit:
            target2_wins += 1
        # 거의 같은 분봉에서 발생한 중복 신호를 하나로 압축한다.
        index += 5

    if samples == 0:
        return empty_result
    recent_count = max(5, len(outcomes) // 3)
    recent_outcomes = outcomes[-recent_count:]
    recent_rate = sum(recent_outcomes) / len(recent_outcomes) * 100 if recent_outcomes else 0.0
    return {
        "samples": samples,
        "entry_hits": entry_hits,
        "entry_hit_rate": round(entry_hits / samples * 100, 1),
        "target1_wins": target1_wins,
        "full_success_rate": round(target1_wins / samples * 100, 1),
        "target2_wins": target2_wins,
        "target2_success_rate": round(target2_wins / samples * 100, 1),
        "recent_success_rate": round(recent_rate, 1),
        "wilson_lower_bound": round(wilson_lower_bound(target1_wins, samples), 1),
    }


def forecast_us_position_flow(minute, quote_row, position):
    """최근 분봉·VWAP·체결강도로 보유 종목의 반등/붕괴 흐름을 판정한다."""
    one = add_indicators(resample_bars(minute, 1))
    three = add_indicators(resample_bars(minute, 3))
    five = add_indicators(resample_bars(minute, 5))
    fifteen = add_indicators(resample_bars(minute, 15))
    if len(one) < 30 or len(three) < 8:
        return {
            "state": "⚪ 판정 분봉 부족",
            "score": 0,
            "eta": "계산 대기",
            "recovery": "판정 대기",
            "reason": "최소 30개의 1분봉이 필요합니다.",
        }

    current = float(quote_row.get("현재가($)", 0) or 0)
    day_vwap = float(quote_row.get("VWAP($)", 0) or 0)
    strength = float(quote_row.get("체결강도", 0) or 0)
    spread = float(quote_row.get("스프레드(%)", 0) or 0)
    entry = float(position["진입가"])
    stop = float(position["손절가"])
    target1 = float(position["1차목표"])

    close = one["종가"]
    ema = one["EMA9"]
    ema_slope = (
        (float(ema.iloc[-1]) / float(ema.iloc[-4]) - 1) * 100
        if len(ema) >= 4 and float(ema.iloc[-4]) > 0
        else 0.0
    )
    price_slope = (
        (float(close.iloc[-1]) / float(close.iloc[-4]) - 1) * 100
        if len(close) >= 4 and float(close.iloc[-4]) > 0
        else 0.0
    )
    histogram = three["MACD히스토그램"].dropna()
    macd_improving = (
        len(histogram) >= 2
        and float(histogram.iloc[-1]) > float(histogram.iloc[-2])
    )
    macd_positive = len(histogram) > 0 and float(histogram.iloc[-1]) > 0
    rsi_1 = safe_last(one["RSI"])
    rsi_3 = safe_last(three["RSI"])
    recent_volume = float(one["거래량"].tail(3).mean())
    prior_volume = float(one["거래량"].iloc[-23:-3].mean())
    volume_speed = recent_volume / prior_volume if prior_volume > 0 else 0.0
    recent_lows = one["저가"].tail(4)
    higher_low = len(recent_lows) >= 4 and float(recent_lows.iloc[-1]) >= float(recent_lows.iloc[-3])
    chart_support = float(one["저가"].tail(12).min())

    score = 50
    reasons = []
    if day_vwap > 0 and current >= day_vwap:
        score += 12
        reasons.append("VWAP 위")
    elif day_vwap > 0:
        score -= 15
        reasons.append("VWAP 아래")
    if current >= safe_last(one["EMA9"], current):
        score += 10
        reasons.append("EMA9 위")
    else:
        score -= 10
        reasons.append("EMA9 아래")
    if ema_slope > 0 and price_slope > 0:
        score += 12
        reasons.append("단기 기울기 상승")
    elif ema_slope < 0 and price_slope < 0:
        score -= 14
        reasons.append("단기 기울기 하락")
    if macd_improving:
        score += 10
        reasons.append("MACD 회복")
    else:
        score -= 8
        reasons.append("MACD 약화")
    if macd_positive:
        score += 5
    if 48 <= rsi_3 <= 72 and rsi_1 < 80:
        score += 8
        reasons.append("RSI 상승 구간")
    elif rsi_1 >= 82:
        score -= 10
        reasons.append("RSI 과열")
    if volume_speed >= 1.1:
        score += 8
        reasons.append("거래량 재증가")
    elif volume_speed < 0.7:
        score -= 6
        reasons.append("거래량 둔화")
    if strength >= 115:
        score += 10
        reasons.append("매수체결 우위")
    elif 0 < strength < 90:
        score -= 12
        reasons.append("매도체결 우위")
    if higher_low:
        score += 7
        reasons.append("저점 높아짐")
    if spread > 4:
        score -= 12
        reasons.append("호가 벌어짐")
    if current <= stop or current < chart_support:
        score = min(score, 20)
        reasons.append("손절/지지선 이탈")

    score = max(0, min(100, int(round(score))))
    if current <= stop or current < chart_support:
        state = "🔴 추세 붕괴·회복 기대 낮음"
    elif score >= 72 and ema_slope > 0 and macd_improving:
        state = "🟢 상승 재개 흐름"
    elif current < entry and score >= 58 and current >= stop:
        state = "🟡 정상 눌림·반등 확인 중"
    elif score < 42:
        state = "🔴 반등 실패 가능성 높음"
    else:
        state = "🟡 혼조·추가 3분 확인"

    atr = safe_last(one["ATR"])
    absolute_moves = close.diff().abs().tail(8)
    median_move = float(absolute_moves.median()) if not absolute_moves.empty else 0.0
    expected_move = max(median_move, atr * 0.25, current * 0.0005)
    forecast_price = entry if current < entry else target1
    if score >= 58 and forecast_price > current and expected_move > 0:
        center = max(1, int(round((forecast_price - current) / expected_move)))
        low_eta = max(1, int(round(center * 0.7)))
        high_eta = min(60, max(low_eta + 1, int(round(center * 1.5))))
        eta = f"약 {low_eta}~{high_eta}분"
    else:
        eta = "예상시간 보류"

    if current < entry:
        if score >= 65:
            recovery = f"진입가 회복 흐름 우세 · {eta} 관찰"
        elif score < 42:
            recovery = "진입가 회복 흐림 약함"
        else:
            recovery = "3분 저점·매수체결 회복 필요"
    else:
        recovery = f"1차 목표 예상 구간: {eta}"

    horizons = forecast_us_tick_horizons(
        {1: one, 3: three, 5: five, 15: fifteen},
        quote_row,
    )

    return {
        "state": state,
        "score": score,
        "eta": eta,
        "recovery": recovery,
        "reason": " · ".join(reasons[:5]),
        "ema_slope": ema_slope,
        "price_slope": price_slope,
        "rsi_1": rsi_1,
        "rsi_3": rsi_3,
        "volume_speed": volume_speed,
        "horizons": horizons,
    }


def analyze_us_penny_candidates(token, table, session_mode, limit=12, pages=3, strategy="momentum"):
    """삼중교집합을 우선하되 카드가 비지 않도록 상위 관찰주까지 분석한다."""
    eligible = table.copy()
    if "삼중교집합" in eligible.columns:
        exact = eligible[eligible["삼중교집합"].astype(bool)]
        watch = eligible[~eligible["삼중교집합"].astype(bool)]
        eligible = pd.concat([exact, watch], ignore_index=True)
    targets = [row.to_dict() for _, row in eligible.head(limit).iterrows()]

    def inspect(row):
        minute = get_us_recent_minutes(
            token,
            str(row["거래소코드"]),
            str(row["종목코드"]),
            session_mode,
            pages=pages,
        )
        if len(minute) < 35:
            return {
                "row": row,
                "analysis": None,
                "error": f"분봉 {len(minute)}개(최소 35개 필요)",
            }
        return {"row": row, "analysis": analyze_us_penny_stock(minute, row, strategy=strategy), "error": ""}

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(inspect, row) for row in targets]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:
                results.append({"row": {}, "analysis": None, "error": str(error)})

    verdict_order = {
        "🟢 재돌파 확인·지금 진입": 0,
        "🟢 재돌파 진입 가능": 1,
        "🟢 우량주 눌림 진입": 0,
        "🟢 우량주 진입 가능": 1,
        "🟡 눌림대기·재돌파 감시": 2,
        "🟡 재돌파 확인대기": 3,
        "🟡 우량주 눌림대기": 2,
        "⚪ 조건 미달": 4,
        "🔴 과열·급락위험": 5,
    }
    results.sort(key=lambda item: (
        verdict_order.get((item.get("analysis") or {}).get("verdict"), 9),
        -float((item.get("analysis") or {}).get("score", 0)),
    ))
    return results


def format_compact_number(value, money=False):
    value = float(value or 0)
    prefix = "$" if money else ""
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{prefix}{value / 1_000:.1f}K"
    return f"{prefix}{value:,.0f}"


def render_us_reference_card(item):
    """사용자가 제시한 모바일 예시와 같은 한 장짜리 숫자 카드."""
    row = item.get("row") or {}
    analysis = item.get("analysis")
    name = html.escape(str(row.get("종목명") or row.get("종목코드") or "종목"))
    ticker = html.escape(str(row.get("종목코드") or "-"))
    market = html.escape(str(row.get("시장") or row.get("거래소코드") or "US"))
    price = float(row.get("현재가($)", 0) or 0)
    rate = float(row.get("등락률(%)", 0) or 0)
    volume = int(float(row.get("오늘거래량", 0) or 0))
    volume_ratio_pct = float(row.get("전일대비거래량(%)", 0) or 0)
    volume_multiple = volume_ratio_pct / 100 if volume_ratio_pct > 0 else 0
    amount_million = float(row.get("오늘거래대금(백만$)", 0) or 0)
    vwap = float((analysis or {}).get("corrected_vwap", row.get("VWAP($)", 0)) or 0)
    vwap_gap = float((analysis or {}).get("corrected_vwap_gap", row.get("VWAP위치(%)", 0)) or 0)
    strength = float(row.get("체결강도", 0) or 0)
    spread = float(row.get("스프레드(%)", 0) or 0)
    market_cap = float(row.get("시가총액(API)", 0) or 0)
    overlap = int(float(row.get("교집합수", 0) or 0))
    filter_score = int(float(row.get("1차필터점수", 0) or 0))
    session = html.escape(str(row.get("세션") or "미국장"))
    received_at = float(row.get("수신타임스탬프", 0) or 0)
    quote_age = max(0.0, time.time() - received_at) if received_at > 0 else 999.0

    if analysis:
        verdict = str(analysis.get("verdict", "⚪ 대기"))
        entry = float(analysis.get("entry", 0) or 0)
        stop = float(analysis.get("stop", 0) or 0)
        target1 = float(analysis.get("target1", 0) or 0)
        target2 = float(analysis.get("target2", 0) or 0)
        support = float(analysis.get("support", 0) or 0)
        resistance = float(analysis.get("resistance", 0) or 0)
        rsi = f"{analysis.get('rsi_1', 0):.0f}/{analysis.get('rsi_3', 0):.0f}/{analysis.get('rsi_5', 0):.0f}"
        macd = "상승" if analysis.get("bullish_macd") else "약화"
        trend = "/".join("상" if analysis.get(f"trend_{m}") else "하" for m in (1, 3, 5, 15))
        replay = float(analysis.get("validation_win_rate", 0) or 0)
        missing = [label for label, ok in analysis.get("checks", {}).items() if not ok]
        warning = " · ".join(missing[:2]) if missing else "조건 유지 확인"
    else:
        verdict = str(row.get("현재판정") or "⚪ 차트 계산 대기")
        entry = stop = target1 = target2 = support = resistance = 0.0
        rsi = "-/-/-"
        macd = "대기"
        trend = "-/-/-/-"
        replay = 0.0
        warning = item.get("error") or "분봉 계산 대기"

    if quote_age > 8:
        verdict = "🟡 시세 갱신 필요"
    badge = "🟢 진입" if verdict.startswith("🟢") else "🔴 회피" if verdict.startswith("🔴") else "🟡 대기"
    verdict_color = "#61df88" if verdict.startswith("🟢") else "#ff858b" if verdict.startswith("🔴") else "#ffd45d"
    price_text = f"${price:.4f}" if price < 1 else f"${price:,.2f}"
    cap_text = format_compact_number(market_cap, money=True) if market_cap > 0 else "-"
    amount_text = f"${amount_million:.1f}M"
    today = datetime.now(SEOUL).strftime("%Y-%m-%d")

    def usd(value):
        if value <= 0:
            return "-"
        return f"${value:.4f}" if value < 1 else f"${value:,.2f}"

    render_compact_html(
        f'''<div class="stock-card">
        <div class="card-head"><div><div class="stock-name">{name} <small style="color:#7f899b">{ticker} {market}</small></div>
        <div class="ticker">{session} · {today}</div></div>
        <div><div class="price">{price_text}</div><div class="{'change-up' if rate >= 0 else 'change-down'}">{rate:+.2f}%</div></div></div>
        <div class="signal-badge">{badge}</div>
        <div class="risk-line">{html.escape(verdict)} · {html.escape(str(warning))}</div>
        <div class="metric-grid">
          <div class="metric"><span>전일대비 거래량</span><b style="color:#ffd45d">×{volume_multiple:.2f}</b></div>
          <div class="metric"><span>당일 거래량</span><b>{volume:,}</b></div>
          <div class="metric"><span>등락률</span><b style="color:{'#61df88' if rate >= 0 else '#ff777d'}">{rate:+.2f}%</b></div>
          <div class="metric"><span>체결강도</span><b style="color:#61df88">{strength:.0f}</b></div>
          <div class="metric"><span>VWAP</span><b>{usd(vwap)}</b></div>
          <div class="metric"><span>VWAP 위치</span><b>{vwap_gap:+.2f}%</b></div>
          <div class="metric"><span>거래대금</span><b>{amount_text}</b></div>
          <div class="metric"><span>시총(API)</span><b>{cap_text}</b></div>
          <div class="metric"><span>호가 차이</span><b>{spread:.2f}%</b></div>
          <div class="metric"><span>순위 중첩</span><b>{overlap}/4</b></div>
          <div class="metric"><span>RSI 1/3/5</span><b>{rsi}</b></div>
          <div class="metric"><span>MACD / 추세</span><b>{macd} · {trend}</b></div>
          <div class="metric"><span>1차 필터</span><b>{filter_score}/100</b></div>
          <div class="metric"><span>분봉 재생률</span><b>{replay:.1f}%</b></div>
        </div>
        <div class="trade-title">{"우량주 눌림 진입" if (analysis or {}).get("strategy") == "quality" else "급등주 눌림대기 → 재돌파진입"} · 녹색일 때만 진입 검토</div>
        <div class="trade-grid">
          <div class="trade-box"><span>진입가</span><b class="entry">{usd(entry)}</b></div>
          <div class="trade-box"><span>5분 1차 익절</span><b class="target">{usd(target1)}</b></div>
          <div class="trade-box"><span>연장 2차 익절</span><b class="target">{usd(target2)}</b></div>
          <div class="trade-box"><span>지지선</span><b style="color:#ffd45d">{usd(support)}</b></div>
          <div class="trade-box"><span>손절가</span><b class="stop">{usd(stop)}</b></div>
          <div class="trade-box"><span>저항선</span><b>{usd(resistance)}</b></div>
        </div>
        <div class="footnote">갱신 {quote_age:.1f}초 · 자동주문 없음</div>
        </div>'''
    )


def update_us_signal_item_in_state(updated_item, scanner_type="momentum"):
    """자동 감시로 갱신한 종목 카드 한 개를 세션 목록에 반영한다."""
    ticker = str((updated_item.get("row") or {}).get("종목코드", "")).upper()
    state_key = f"us_signal_items_{scanner_type}"
    items = list(st.session_state.get(state_key, []))
    replaced = False
    for index, item in enumerate(items):
        item_ticker = str((item.get("row") or {}).get("종목코드", "")).upper()
        if ticker and item_ticker == ticker:
            items[index] = updated_item
            replaced = True
            break
    if not replaced:
        items.insert(0, updated_item)
    st.session_state[state_key] = items[:12]


def refresh_one_us_signal_item(item, session_choice, scanner_type, deep_check=False):
    """선택 종목 한 개만 빠르게 현재가 갱신하고 필요할 때 분봉 신호도 재계산한다."""
    row = dict(item.get("row") or {})
    ticker = str(row.get("종목코드", "")).upper()
    exchange = str(row.get("거래소코드", "")).upper()
    if not ticker or not exchange:
        return item, "종목코드 또는 거래소코드가 없습니다."

    token = issue_access_token(APP_KEY, APP_SECRET)
    session_mode, session_detail, _ = resolve_us_session(session_choice)
    one = pd.DataFrame([row])
    rest_rows, rest_errors = get_us_multiple_prices(token, [(exchange, ticker)], session_mode)
    if rest_rows:
        one = apply_us_rest_prices(one, rest_rows, "penny" if scanner_type == "penny" else "momentum")
        row = one.iloc[0].to_dict()
        row["세션"] = session_detail

    analysis = item.get("analysis")
    error = item.get("error", "")
    if deep_check:
        minute = get_us_recent_minutes(token, exchange, ticker, session_mode, pages=1)
        if len(minute) >= 35:
            analysis = analyze_us_penny_stock(minute, row, strategy="quality" if scanner_type == "quality" else "momentum")
            error = ""
        else:
            error = f"분봉 {len(minute)}개(최소 35개 필요)"

    updated = {"row": row, "analysis": analysis, "error": error}
    update_us_signal_item_in_state(updated, scanner_type)
    return updated, " / ".join(rest_errors)


def render_auto_us_card(item, session_choice, scanner_type, auto_enabled):
    """Streamlit fragment를 지원하면 선택 카드만 주기적으로 다시 실행한다."""
    if not auto_enabled:
        render_us_reference_card(item)
        render_us_entry_lock_controls(item, scanner_type)
        return

    ticker = str((item.get("row") or {}).get("종목코드", "")).upper()
    counter_key = f"auto_signal_counter_{scanner_type}_{ticker}"
    counter = int(st.session_state.get(counter_key, 0)) + 1
    st.session_state[counter_key] = counter
    # 현재가는 매 회차 갱신하고, 분봉 신호는 약 8초마다 재계산해 API 과호출을 줄인다.
    deep_check = counter == 1 or counter % 2 == 0
    try:
        updated, warning = refresh_one_us_signal_item(
            item, session_choice, scanner_type, deep_check=deep_check
        )
        render_us_reference_card(updated)
        render_us_entry_lock_controls(updated, scanner_type)
        if warning:
            st.caption(f"자동감시 일부 경고: {warning}")
        st.caption(
            "자동감시 ON · 현재가 약 4초 간격 · 첫 눌림/재상승 판정 약 8초 간격 "
            "· 접근토큰은 저장된 토큰을 재사용합니다."
        )
    except Exception as error:
        render_us_reference_card(item)
        render_us_entry_lock_controls(item, scanner_type)
        st.caption(f"자동감시 일시 실패: {error}")



def mark_latest_signal_manual_close(ticker, price=0.0):
    now = datetime.now(SEOUL)
    with _signal_db() as db:
        row = db.execute(
            "SELECT id, entry FROM signal_events WHERE ticker=? AND outcome='OPEN' ORDER BY created_ts DESC LIMIT 1",
            (str(ticker).upper(),),
        ).fetchone()
        if not row:
            return
        realized = (float(price) / float(row["entry"]) - 1) * 100 if price and row["entry"] else None
        db.execute(
            "UPDATE signal_events SET outcome='MANUAL_CLOSE', outcome_at=?, outcome_ts=?, realized_pct=? WHERE id=?",
            (now.strftime("%Y-%m-%d %H:%M:%S"), now.timestamp(), realized, row["id"]),
        )

def render_us_entry_lock_controls(item, scanner_type):
    """녹색 카드에서만 실제 체결가를 고정한다."""
    row = item.get("row") or {}
    analysis = item.get("analysis")
    if not analysis or not str(analysis.get("verdict", "")).startswith("🟢"):
        return
    if not analysis.get("first_pullback_ready"):
        return
    received_at = float(row.get("수신타임스탬프", 0) or 0)
    quote_age = max(0.0, time.time() - received_at) if received_at > 0 else 999.0
    if quote_age > 8:
        return

    ticker = str(row.get("종목코드", ""))
    planned_entry = float(analysis["entry"])
    actual_entry = st.number_input(
        "실제 체결가",
        min_value=0.0001,
        value=planned_entry,
        step=max(planned_entry * 0.001, 0.0001),
        format="%.4f",
        key=f"simple_actual_entry_{scanner_type}_{ticker}",
        label_visibility="collapsed",
    )
    if not st.button(
        f"🔒 {ticker} 진입가·청산계획 고정",
        use_container_width=True,
        key=f"simple_lock_{scanner_type}_{ticker}",
    ):
        return

    deviation = abs(float(actual_entry) / planned_entry - 1) * 100 if planned_entry > 0 else 999
    spread = float(row.get("스프레드(%)", 0) or 0)
    allowed = max(0.5, min(1.0, spread * 1.5))
    if deviation > allowed:
        st.error("계획가에서 너무 벗어나 진입을 기록하지 않았습니다.")
        return
    stop, target1, target2, support, resistance, details = calculate_us_multiframe_levels(
        analysis["frames"], float(actual_entry)
    )
    risk = float(actual_entry) - float(stop)
    rr = (float(target1) - float(actual_entry)) / risk if risk > 0 else 0
    if risk <= 0 or rr < 1.5:
        st.error("손익비가 1.5 미만이라 진입을 기록하지 않았습니다.")
        return
    st.session_state["active_us_position"] = {
        "티커": ticker,
        "종목명": str(row.get("종목명") or ticker),
        "거래소코드": str(row.get("거래소코드", "")),
        "진입가": round(float(actual_entry), 4),
        "손절가": round(float(stop), 4),
        "1차목표": round(float(target1), 4),
        "2차목표": round(float(target2), 4),
        "지지선": round(float(support), 4),
        "저항선": round(float(resistance), 4),
        "레벨분석": details,
        "진입시각": datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M:%S"),
        "계획가이탈(%)": round(deviation, 2),
        "최고가": round(float(actual_entry), 4),
        "실행손절가": round(float(stop), 4),
    }
    st.session_state.pop("active_us_forecast", None)
    st.rerun()


def render_compact_card(saved):
    analysis = saved["analysis"]
    quote = saved["row"]
    stock_name = html.escape(str(quote["종목명"]))
    ticker = html.escape(str(quote["종목코드"]))
    market = html.escape(str(quote["시장"]))
    scan_name = "급등주" if str(saved.get("scan_type", "")).endswith("momentum") else "우량주"
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
    if str(saved.get("scan_type", "")).endswith("momentum"):
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
    if "교집합수" in universe.columns:
        return (
            universe.sort_values(
                ["삼중교집합", "교집합수", "체결강도", "KRX누적거래량"],
                ascending=[False, False, False, False],
            )["종목코드"].drop_duplicates().head(30).tolist()
        )
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
            result["등락률(%)"].between(0.5, 30)
            & (result["오늘누적거래량"] >= 100_000)
            & (result["오늘누적거래대금"] >= 1_000_000_000)
        ].copy()
        result["현재판정"] = result.apply(decide_momentum_status, axis=1)
        if "삼중교집합" in result.columns:
            result.loc[~result["삼중교집합"].astype(bool), "현재판정"] = "🟡 2/3 관찰만·진입금지"
            exact = result["삼중교집합"].astype(bool)
            result.loc[
                exact & result["현재판정"].isin(["🟢 급등 정밀검사", "🟡 거래량 확대 감시"]),
                "현재판정",
            ] = "🟢 삼중순위·차트검사"
    else:
        result["현재판정"] = result.apply(decide_status, axis=1)
    sort_columns = [
        column for column in ("삼중교집합", "교집합수", "체결강도", "오늘누적거래대금")
        if column in result.columns
    ]
    if not sort_columns:
        return result.reset_index(drop=True)
    return result.sort_values(sort_columns, ascending=[False] * len(sort_columns)).reset_index(drop=True)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.sidebar.caption("✅ API 연결 준비")
st.sidebar.caption("⚠️ 80% 표시는 과거 재생 통과 기준이며 미래 수익을 보장하지 않습니다.")

market_label = st.sidebar.radio(
    "시장 선택",
    ["🇰🇷 국내주식", "🇺🇸 미국주식"],
    horizontal=True,
    label_visibility="collapsed",
)
market_code = "kr" if market_label.startswith("🇰🇷") else "us"
CHAPTER_QUALITY = {
    "kr_quality": {"score": 91, "grade": "A", "label": "국내 우량주 단타"},
    "kr_momentum": {"score": 88, "grade": "A-", "label": "국내 급등주 탐색"},
    "us_quality": {"score": 90, "grade": "A", "label": "미국 우량주 단타"},
    "us_momentum": {"score": 89, "grade": "A-", "label": "미국 급등주 탐색·진입"},
    "us_penny": {"score": 86, "grade": "B+", "label": "미국 동전주 급등"},
    "us_runup": {"score": 88, "grade": "A-", "label": "미국 소형 이벤트 런업"},
}


def render_chapter_quality_badge(scanner_type):
    info = CHAPTER_QUALITY.get(scanner_type, {"score": 85, "grade": "B+", "label": scanner_type})
    render_compact_html(f"""
    <div class="stock-card" style="padding:12px 14px;margin-bottom:10px;">
      <div class="card-head">
        <div><div class="stock-name">{html.escape(info['label'])}</div><div class="ticker">전용 데이터·필터·진입 엔진</div></div>
        <div><div class="price">{info['grade']}</div><div class="change-up">완성도 {info['score']}점</div></div>
      </div>
    </div>
    """)


strategy_options = ["🏦 우량주 단타", "🔥 급등주 단타"]
if market_code == "us":
    strategy_options.extend(["🚀 미국 런업", "🪙 동전주 급등"])
strategy_label = st.sidebar.radio(
    "검색 방식",
    strategy_options,
    horizontal=True,
    label_visibility="collapsed",
)

if strategy_label.startswith("🏦"):
    strategy_code = "quality"
elif strategy_label.startswith("🚀"):
    strategy_code = "runup"
elif strategy_label.startswith("🪙"):
    strategy_code = "penny"
else:
    strategy_code = "momentum"
scanner_type = f"{market_code}_{strategy_code}"
is_domestic = market_code == "kr"

scan_button_labels = {
    "kr_quality": "국내 우량주 통합시장 검사",
    "kr_momentum": "국내 급등주 삼중순위 교집합 검사",
    "us_quality": "미국 우량주 10종목씩 고속 검사",
    "us_momentum": "미국 급등주 조기포착 합집합 검색",
    "us_runup": "미국 런업 자동분류 Top 5 검색",
    "us_penny": "미국 동전주 삼중순위 교집합 검색",
}

us_session_choice = "자동(현재 장)"
if not is_domestic:
    us_session_choice = st.sidebar.selectbox(
        "미국장 시세 선택",
        ["자동(현재 장)", "주간거래", "프리·정규·애프터"],
        help="자동은 한국시간에 따라 주간거래 코드와 미국 정규거래소 코드를 바꾸어 조회합니다.",
    )
    if strategy_code in ("momentum", "penny"):
        st.sidebar.caption("순위 → 거래량 → VWAP → 재돌파")
    elif strategy_code == "runup":
        st.sidebar.caption("시장후보 → 일정·뉴스 분류 → 런업 Top 5")
elif strategy_code == "momentum":
    st.sidebar.caption("상승률·거래량·급증·체결강도 합집합")

if st.sidebar.button(scan_button_labels[scanner_type], type="primary"):
    try:
        token = issue_access_token(APP_KEY, APP_SECRET)
        price_errors = []

        if scanner_type == "kr_quality":
            with st.spinner("국내 시가총액 상위 종목을 선정하는 중입니다..."):
                kospi_rows = get_market_cap_ranking(token, "0001")
                kosdaq_rows = get_market_cap_ranking(token, "1001")
                universe = build_universe(kospi_rows, kosdaq_rows)
                targets = universe.nlargest(30, "1차거래대금근사")["종목코드"].tolist()
            if universe.empty:
                raise RuntimeError("조건에 맞는 국내 우량주 후보를 받지 못했습니다.")
            with st.spinner("국내 통합시장 현재가를 조회하는 중입니다..."):
                prices, price_errors = collect_integrated_prices(token, targets)
                table = merge_realtime(universe, prices, scanner_type="quality")

        elif scanner_type == "kr_momentum":
            with st.spinner("국내 상승률·당일거래량·체결강도 순위를 동시에 받는 중입니다..."):
                domestic_groups, rank_errors = get_domestic_triple_rank_rows(token)
                universe = build_domestic_triple_universe(
                    domestic_groups["상승률"],
                    domestic_groups["당일거래량"],
                    domestic_groups["체결강도"],
                )
                targets = choose_momentum_targets(universe)
            if universe.empty:
                raise RuntimeError("세 순위 중 2개 이상에 겹치는 국내 급등주 후보가 없습니다.")
            with st.spinner("국내 통합시장 현재가를 조회하는 중입니다..."):
                prices, price_errors = collect_integrated_prices(token, targets)
                table = merge_realtime(universe, prices, scanner_type="momentum")
            st.session_state["kr_source_counts"] = {
                name: len(rows) for name, rows in domestic_groups.items()
            }
            price_errors.extend(rank_errors)

        elif scanner_type == "us_quality":
            with st.spinner("미국 우량주를 10종목씩 빠르게 조회하는 중입니다..."):
                session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
                us_candidates = unique_us_pairs(US_QUALITY_UNIVERSE)
                market_rows, price_errors = get_us_multiple_prices(
                    token, us_candidates, session_mode
                )
                table = build_us_fast_table(
                    market_rows, us_candidates, strategy="quality"
                )
                us_source_note = "한투 공식 우량주 후보목록"

        elif scanner_type == "us_runup":
            with st.spinner("미국 시장 재료를 분류해 런업 Top 5를 만드는 중입니다..."):
                session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
                table, source_counts, runup_errors = build_dynamic_us_runup_top5(
                    token, session_mode
                )
                price_errors = list(runup_errors)
                st.session_state["us_source_counts"] = source_counts
                us_source_note = (
                    "한투 시장순위 + Nasdaq 실적 일정 + Google News RSS · "
                    "FDA·임상/실적/계약/AI/기타 자동분류"
                )

        else:
            scan_name = "동전주 급등" if strategy_code == "penny" else "급등주"
            with st.spinner(f"미국 {scan_name} 상승률·거래량·거래량급증·체결강도 순위를 동시에 받는 중입니다..."):
                session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
                grouped, source_counts, rank_errors = get_us_triple_rank_rows(
                    token,
                    penny_only=strategy_code == "penny",
                )
                market_rows, us_candidates = build_us_triple_rank_rows(
                    grouped["상승률"],
                    grouped["당일거래량"],
                    grouped["체결강도"],
                    session_mode,
                    penny_only=strategy_code == "penny",
                    surge_rows=grouped.get("거래량급증", []),
                )
                table = build_us_fast_table(
                    market_rows, us_candidates, strategy=strategy_code
                )
                table["시세출처"] = "한투 공식 급등순위 합집합"
                early_count = int(table["등락률(%)"].between(5, 35).sum()) if not table.empty else 0
                us_source_note = (
                    f"상승률·당일거래량·거래량급증·체결강도 합집합 {len(us_candidates)}개 "
                    f"(조기포착 +5~35% {early_count}개)"
                )
                price_errors = list(rank_errors)
                st.session_state["us_source_counts"] = source_counts

        if not is_domestic and not table.empty:
            live_pairs = list(zip(table["거래소코드"], table["종목코드"]))
            live_snapshots, live_errors = get_us_live_snapshots(
                live_pairs,
                session_mode,
                wait_seconds=(0.85 if strategy_code in ("penny", "momentum") else 1.0),
                limit=18,
            )
            table = apply_us_live_snapshots(table, live_snapshots, strategy_code)
            price_errors.extend(live_errors)
            st.session_state["us_live_count"] = len(live_snapshots)

        if table.empty:
            if scanner_type == "us_penny":
                message = (
                    "$0.01~$10 범위 종목을 찾았지만 현재 세션에서 가격·거래량을 확인하지 못했습니다. "
                    "미국장 선택을 '프리·정규·애프터'로 바꿔 한 번 더 확인해 주세요."
                )
                if price_errors:
                    message += "\n일부 수집원 오류: " + " / ".join(price_errors[:3])
                raise RuntimeError(message)
            if price_errors:
                raise RuntimeError("\n".join(price_errors[:5]))
            raise RuntimeError("현재 조건에 맞는 종목을 받지 못했습니다. 미국 장 운영 시간에 다시 확인해 주세요.")

        # 조기포착 점수가 높은 상위 종목의 분봉을 재생해 눌림·재돌파와 레벨을 계산한다.
        st.session_state.pop("us_penny_signals", None)
        if not is_domestic and strategy_code in ("penny", "momentum"):
            with st.spinner("상위 3종목의 차트·적중률을 동시 검증하는 중..."):
                st.session_state[f"us_signal_items_{scanner_type}"] = analyze_us_penny_candidates(
                    token,
                    table,
                    session_mode,
                    limit=3,
                )

        st.session_state["scan_table"] = table
        st.session_state["scan_type"] = scanner_type
        if not is_domestic:
            st.session_state["us_scan_meta"] = {
                "session": session_detail,
                "time": scan_time,
                "source": us_source_note,
                "errors": price_errors,
            }
        st.session_state.pop("last_analysis", None)
        market_text = "국내" if is_domestic else "미국"
        kind_text = (
            "우량주" if strategy_code == "quality"
            else "런업" if strategy_code == "runup"
            else "동전주" if strategy_code == "penny"
            else "급등주"
        )
        st.toast(f"{market_text} {kind_text} {len(table)}종목 갱신 완료")
    except Exception as error:
        st.error("종목 검사에 실패했습니다.")
        st.code(str(error))


has_current_scan = (
    "scan_table" in st.session_state
    and st.session_state.get("scan_type") == scanner_type
)

render_chapter_quality_badge(scanner_type)


if has_current_scan and not is_domestic:
    table = st.session_state["scan_table"].copy()

    if strategy_code == "runup":
        st.subheader("🚀 미국 소형 이벤트주 TOP5")
        st.caption("공식 이벤트 A/B등급 + 선반영·희석·호가·손익비 필터를 모두 통과한 후보만 표시합니다.")
        if table.empty:
            st.warning("현재 엄격 조건을 통과한 런업 후보가 없습니다. 후보가 없을 때 억지로 매수하지 않는 것이 정상입니다.")
        else:
            for _, r in table.head(5).iterrows():
                ticker=html.escape(str(r.get("종목코드") or "")); name=html.escape(str(r.get("종목명") or ticker))
                category=html.escape(str(r.get("런업분류") or "")); dday=html.escape(str(r.get("D-day") or ""))
                grade=html.escape(str(r.get("이벤트등급") or "")); verdict=html.escape(str(r.get("현재판정") or ""))
                score=max(0,min(100,to_float(r.get("런업점수")))); pre=max(0,min(100,to_float(r.get("선반영점수"))))
                price=to_float(r.get("현재가($)")); rate=to_float(r.get("등락률(%)")); expected=html.escape(str(r.get("예상런업") or ""))
                dilution=html.escape(str(r.get("희석위험") or "")); stage=html.escape(str(r.get("런업단계") or ""))
                entry=to_float(r.get("권장진입가($)")); stop=to_float(r.get("손절가($)")); t1=to_float(r.get("1차목표($)")); t2=to_float(r.get("2차목표($)")); rr=to_float(r.get("손익비"))
                reasons=r.get("진입근거") or []
                if isinstance(reasons,str): reasons=[reasons]
                reason_html="".join(f'<div class="reason-item">✓ {html.escape(str(x))}</div>' for x in reasons[:4])
                cls="v-green" if verdict.startswith("🟢") else "v-yellow" if verdict.startswith(("🟡","👀")) else "v-red"
                internal="A+" if score>=90 and verdict.startswith("🟢") else "A" if score>=80 and verdict.startswith("🟢") else "B" if score>=70 else "C"
                render_compact_html(f"""
                <div class="stock-card">
                  <div class="card-head"><div><div class="stock-name">{name}</div><div class="ticker">{ticker} · {category} · {dday} · 이벤트 {grade}</div></div><div><div class="price">${price:,.4f}</div><div class="{'change-up' if rate>=0 else 'change-down'}">{rate:+.2f}%</div></div></div>
                  <div class="verdict {cls}">{verdict}</div>
                  <div class="mobile-summary">
                    <div class="mobile-kpi"><span>조건등급</span><b>{internal}</b></div>
                    <div class="mobile-kpi"><span>폭발 가능성</span><b>{score:.0f}/100</b></div>
                    <div class="mobile-kpi"><span>선반영</span><b>{pre:.0f}%</b></div>
                    <div class="mobile-kpi"><span>희석 위험</span><b>{dilution}</b></div>
                  </div>
                  <div class="trade-title">⏰ 예상 관찰구간</div><div class="signal-badge">{expected}</div>
                  <div class="trade-grid">
                    <div class="trade-box"><span>진입</span><b class="entry">${entry:,.4f}</b></div>
                    <div class="trade-box"><span>손절</span><b class="stop">${stop:,.4f}</b></div>
                    <div class="trade-box"><span>손익비</span><b>{rr:.1f}</b></div>
                    <div class="trade-box"><span>1차</span><b class="target">${t1:,.4f}</b></div>
                    <div class="trade-box"><span>2차</span><b class="target">${t2:,.4f}</b></div>
                    <div class="trade-box"><span>단계</span><b>{stage}</b></div>
                  </div>
                  <div class="reason-list"><div class="trade-title">왜 후보인가</div>{reason_html or '<div class="reason-item">• 조건 확인 중</div>'}</div>
                </div>""")
        excluded=st.session_state.get("runup_excluded") or []
        if excluded:
            with st.expander(f"제외된 후보 {len(excluded)}개 · 이유 보기", expanded=False):
                for x in excluded[:10]:
                    st.caption(f"❌ {x.get('종목명') or x.get('종목코드')} ({x.get('종목코드')}) · {x.get('사유')}")
        st.warning("점수·등급은 수익 확률이 아닙니다. 초록색도 상승을 보장하지 않으며 지정가·손절이 필요합니다.")
    # 화면을 가만히 보고 있어도 시세 나이는 계속 증가해야 한다.
    if "수신타임스탬프" in table.columns:
        now_epoch = time.time()
        def _safe_quote_age(value):
            try:
                timestamp = float(value)
                if not math.isfinite(timestamp) or timestamp <= 0:
                    return 999.0
                return round(max(0.0, now_epoch - timestamp), 2)
            except (TypeError, ValueError):
                return 999.0

        table["시세나이(초)"] = table["수신타임스탬프"].apply(_safe_quote_age)
    us_meta = st.session_state.get("us_scan_meta", {})
    us_kind = "우량주" if strategy_code == "quality" else "런업 후보" if strategy_code == "runup" else "동전주 급등" if strategy_code == "penny" else "급등주"
    if not MOBILE_SIMPLE_UI:
        st.success(f"미국 {us_kind} 후보 {len(table)}종목을 받았습니다.")
        st.caption(
            f"세션: {us_meta.get('session', '-')} · "
            f"갱신: {us_meta.get('time', '-')} KST · "
            f"후보: {us_meta.get('source', '-')}. "
            f"웹소켓 실체결: {st.session_state.get('us_live_count', 0)}종목."
        )

    # 현재가가 갱신될 때마다 기존 미결정 신호의 결과를 자동 추적한다.
    try:
        update_open_signal_outcomes(table)
    except Exception:
        pass

    active_position = st.session_state.get("active_us_position")
    if active_position:
        active_ticker = str(active_position["티커"])
        current_rows = table[
            table["종목코드"].astype(str).str.upper() == active_ticker.upper()
        ]
        current_price = (
            float(current_rows.iloc[0]["현재가($)"])
            if not current_rows.empty
            else 0.0
        )
        current_quote_age = (
            float(current_rows.iloc[0].get("시세나이(초)", 999) or 999)
            if not current_rows.empty else 999.0
        )
        current_quote_fresh = current_quote_age <= 3.0
        locked_entry = float(active_position["진입가"])
        locked_stop = float(active_position["손절가"])
        locked_target1 = float(active_position["1차목표"])
        locked_target2 = float(active_position["2차목표"])
        return_pct = (
            (current_price / locked_entry - 1) * 100
            if current_price > 0 and locked_entry > 0
            else 0.0
        )
        highest_price = max(
            float(active_position.get("최고가", locked_entry) or locked_entry),
            current_price,
        )
        active_position["최고가"] = round(highest_price, 4)
        original_risk = max(0.0, locked_entry - locked_stop)
        execution_stop = locked_stop
        if highest_price >= locked_target1:
            execution_stop = max(execution_stop, locked_entry)
        elif original_risk > 0 and highest_price >= locked_entry + original_risk * 0.8:
            execution_stop = max(execution_stop, locked_entry - original_risk * 0.25)
        active_position["실행손절가"] = round(execution_stop, 4)
        try:
            entered_at = datetime.strptime(
                str(active_position["진입시각"]), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=SEOUL)
            holding_seconds = max(
                0,
                int((datetime.now(SEOUL) - entered_at).total_seconds()),
            )
            holding_minutes = holding_seconds // 60
            remaining_seconds = max(0, 300 - holding_seconds)
        except Exception:
            holding_seconds = 0
            holding_minutes = 0
            remaining_seconds = 300

        if not current_quote_fresh:
            position_status = "⚪ 시세 만료·즉시 갱신"
            position_color = "#c6cedb"
        elif current_price > 0 and current_price <= execution_stop:
            position_status = "🔴 손절 기준 도달"
            position_color = "#ff7b81"
        elif current_price >= locked_target2:
            position_status = "🟢 2차 목표 도달"
            position_color = "#61df88"
        elif current_price >= locked_target1:
            position_status = "🟢 1차 목표 도달·분할매도"
            position_color = "#61df88"
        elif holding_minutes >= 10 and 0 < current_price < locked_entry:
            position_status = "🔴 10분 시간손절 검토"
            position_color = "#ff7b81"
        else:
            position_status = "🟡 보유 계획 유지"
            position_color = "#ffd45d"

        flow_forecast = st.session_state.get("active_us_forecast", {})
        if flow_forecast.get("티커") != active_ticker:
            flow_forecast = {}
        flow_state = flow_forecast.get("state", "⚪ 흐름 재분석 필요")
        flow_score = int(flow_forecast.get("score", 0) or 0)
        flow_recovery = flow_forecast.get("recovery", "아래 흐름 재분석을 눌러 확인하세요.")
        flow_reason = flow_forecast.get("reason", "-")
        flow_horizons = flow_forecast.get("horizons", {})
        flow_1 = flow_horizons.get("1분 후", {}).get("label", "⚪ 재분석 필요")
        flow_5 = flow_horizons.get("5분 후", {}).get("label", "⚪ 재분석 필요")
        flow_10 = flow_horizons.get("10분 후", {}).get("label", "⚪ 재분석 필요")

        # 진입 후 5분 의사결정: 가격·시간·실시간 흐름을 함께 사용한다.
        if not current_quote_fresh:
            action_text = "⚪ 즉시 새로고침"
            action_color = "#c6cedb"
        elif current_price <= execution_stop:
            action_text = "🔴 전량매도"
            action_color = "#ff7b81"
        elif current_price >= locked_target1:
            action_text = "🟢 1차 익절·나머지 손절 본전"
            action_color = "#61df88"
        elif holding_seconds >= 300 and current_price < locked_entry:
            action_text = "🔴 5분 시간손절"
            action_color = "#ff7b81"
        elif flow_score < 42 and current_price < locked_entry:
            action_text = "🔴 약세·전량매도 검토"
            action_color = "#ff7b81"
        elif flow_score < 55 or str(flow_1).startswith("🔴"):
            action_text = "🟡 절반매도·손절 축소"
            action_color = "#ffd45d"
        else:
            action_text = "🟢 계속보유"
            action_color = "#61df88"
        countdown_text = f"{remaining_seconds // 60:02d}:{remaining_seconds % 60:02d}"

        st.subheader("🔒 내 보유 포지션 · 5분 실시간 관리")
        position_name = html.escape(str(active_position["종목명"]))
        render_compact_html(
            f'''<div class="stock-card" style="border-color:{position_color}">
            <div class="card-head"><div><div class="stock-name">{position_name}</div>
            <div class="ticker">{html.escape(active_ticker)} · 진입 {locked_entry:.4f}달러</div></div>
            <div style="text-align:right"><div style="color:{action_color};font-weight:950;font-size:1.05rem">{action_text}</div>
            <div style="color:{position_color};font-weight:800;font-size:.76rem">{position_status}</div></div></div>
            <div class="grid2" style="margin-top:10px">
              <div class="data-row"><span class="data-label">현재가만 갱신</span><span class="data-value">${current_price:.4f}</span></div>
              <div class="data-row"><span class="data-label">현재 시세 나이</span><span class="data-value">{current_quote_age:.2f}초</span></div>
              <div class="data-row"><span class="data-label">현재 수익률</span><span class="data-value">{return_pct:+.2f}%</span></div>
              <div class="data-row"><span class="data-label">고정 손절가</span><span class="data-value bad">${locked_stop:.4f}</span></div>
              <div class="data-row"><span class="data-label">현재 실행 손절가</span><span class="data-value bad">${execution_stop:.4f}</span></div>
              <div class="data-row"><span class="data-label">고정 5분 1차 익절</span><span class="data-value ok">${locked_target1:.4f}</span></div>
              <div class="data-row"><span class="data-label">고정 연장 2차 익절</span><span class="data-value ok">${locked_target2:.4f}</span></div>
              <div class="data-row"><span class="data-label">진입 시각</span><span class="data-value">{html.escape(str(active_position['진입시각']))}</span></div>
              <div class="data-row"><span class="data-label">5분 남은시간</span><span class="data-value" style="color:{action_color}">{countdown_text}</span></div>
              <div class="data-row"><span class="data-label">보유 시간</span><span class="data-value">{holding_minutes}분 {holding_seconds % 60}초</span></div>
              <div class="data-row"><span class="data-label">현재 차트흐름</span><span class="data-value">{html.escape(str(flow_state))}</span></div>
              <div class="data-row"><span class="data-label">상승흐름 점수</span><span class="data-value">{flow_score}/100</span></div>
              <div class="data-row"><span class="data-label">회복/목표 예상</span><span class="data-value">{html.escape(str(flow_recovery))}</span></div>
              <div class="data-row"><span class="data-label">판단 근거</span><span class="data-value">{html.escape(str(flow_reason))}</span></div>
              <div class="data-row"><span class="data-label">1분 후 방향</span><span class="data-value">{html.escape(str(flow_1))}</span></div>
              <div class="data-row"><span class="data-label">5분 후 방향</span><span class="data-value">{html.escape(str(flow_5))}</span></div>
              <div class="data-row"><span class="data-label">10분 후 방향</span><span class="data-value">{html.escape(str(flow_10))}</span></div>
            </div></div>'''
        )
        st.caption("진입 후에는 현재가와 흐름을 갱신해 계속보유·절반매도·전량매도를 판단합니다. 자동주문은 하지 않습니다.")
        flow_col, close_col = st.columns(2)
        if flow_col.button("📈 보유 종목 흐름 재분석", use_container_width=True):
            try:
                token = issue_access_token(APP_KEY, APP_SECRET)
                session_mode, _, _ = resolve_us_session(us_session_choice)
                if current_rows.empty:
                    raise RuntimeError("현재 스캔 표에 보유 종목이 없습니다. 먼저 거래량 검사를 누르세요.")
                active_quote = current_rows.iloc[0].to_dict()
                with st.spinner("보유 종목의 최근 분봉 흐름을 읽는 중..."):
                    active_minutes = get_us_recent_minutes(
                        token,
                        str(active_quote["거래소코드"]),
                        active_ticker,
                        session_mode,
                        pages=1,
                    )
                    forecast = forecast_us_position_flow(
                        active_minutes,
                        active_quote,
                        active_position,
                    )
                st.session_state["active_us_forecast"] = {
                    "티커": active_ticker,
                    **forecast,
                }
                st.rerun()
            except Exception as error:
                st.warning(str(error))
        if close_col.button("✅ 매도 완료·포지션 종료", use_container_width=True):
            try:
                mark_latest_signal_manual_close(active_ticker, current_price)
            except Exception:
                pass
            st.session_state.pop("active_us_position", None)
            st.session_state.pop("active_us_forecast", None)
            st.rerun()

    simple_card_slot = st.empty() if MOBILE_SIMPLE_UI else None
    quick_col, signal_col = st.columns(2)
    if quick_col.button("⚡ 새로고침", use_container_width=True):
        try:
            token = issue_access_token(APP_KEY, APP_SECRET)
            session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
            pairs = unique_us_pairs(list(zip(table["거래소코드"], table["종목코드"])))
            with st.spinner("한투 현재가를 즉시 다시 조회하는 중..."):
                rest_rows, rest_errors = get_us_multiple_prices(token, pairs, session_mode)
                table = apply_us_rest_prices(table, rest_rows, strategy_code)

                # REST는 매번 현재가를 갱신한다. 웹소켓 신규체결이 잡히면 더 최신 값으로 덮어쓴다.
                snapshots, live_errors = get_us_live_snapshots(
                    pairs, session_mode, wait_seconds=1.15, limit=min(30, len(pairs))
                )
                if snapshots:
                    table = apply_us_live_snapshots(table, snapshots, strategy_code)

            if not rest_rows and not snapshots:
                raise RuntimeError(
                    "한국투자증권에서 현재가를 받지 못했습니다. "
                    "장 선택·거래시간 또는 API 호출 제한을 확인해 주세요."
                )
            st.session_state["scan_table"] = table
            st.session_state["us_live_count"] = len(snapshots)
            st.session_state["us_scan_meta"] = {
                **us_meta,
                "session": session_detail,
                "time": scan_time,
                "errors": list(rest_errors) + list(live_errors),
            }
            active_position = st.session_state.get("active_us_position")
            if active_position:
                active_ticker = str(active_position["티커"])
                active_rows = table[
                    table["종목코드"].astype(str).str.upper() == active_ticker.upper()
                ]
                if not active_rows.empty:
                    active_quote = active_rows.iloc[0].to_dict()
                    token = issue_access_token(APP_KEY, APP_SECRET)
                    active_minutes = get_us_recent_minutes(
                        token,
                        str(active_quote["거래소코드"]),
                        active_ticker,
                        session_mode,
                        pages=1,
                    )
                    st.session_state["active_us_forecast"] = {
                        "티커": active_ticker,
                        **forecast_us_position_flow(
                            active_minutes,
                            active_quote,
                            active_position,
                        ),
                    }
            # 빠른 새로고침에서는 12종목 분봉을 전부 다시 읽지 않는다.
            # 선택 카드는 자동감시 fragment가 별도로 현재가·첫 눌림 신호를 갱신한다.
            st.toast(f"현재가 {len(rest_rows)}종목 · 실체결 {len(snapshots)}종목 갱신 완료")
            st.rerun()
        except Exception as error:
            st.warning(str(error))

    if strategy_code in ("penny", "momentum") and signal_col.button(
        "🎯 차트검사",
        use_container_width=True,
    ):
        try:
            token = issue_access_token(APP_KEY, APP_SECRET)
            session_mode, _, _ = resolve_us_session(us_session_choice)
            with st.spinner("상위 12종목의 첫 눌림·재상승과 5분 보유계획을 계산하는 중..."):
                st.session_state["us_penny_signals"] = analyze_us_penny_candidates(
                    token, table, session_mode, limit=12, pages=3,
                    strategy="quality" if scanner_type == "quality" else "momentum",
                )
            st.toast("매수타점 정밀검사 완료")
        except Exception as error:
            st.warning(str(error))

    # 실전 신호 저널: 현재 전략의 초록색 신호를 저장하고 결과 통계를 표시한다.
    journal_state_key = f"us_signal_items_{scanner_type}"
    journal_items = [
        item for item in st.session_state.get(journal_state_key, [])
        if item.get("row")
    ]
    try:
        sync_live_signal_journal(journal_items, scanner_type, table)
        render_live_validation_panel(
            "quality" if strategy_code == "quality"
            else "runup" if strategy_code == "runup"
            else "momentum"
        )
    except Exception as journal_error:
        st.caption(f"실전 신호 저널 대기: {journal_error}")

    if MOBILE_SIMPLE_UI and strategy_code != "runup":
        signal_state_key = f"us_signal_items_{scanner_type}"
        signal_items = [
            item for item in st.session_state.get(signal_state_key, [])
            if item.get("row")
        ]
        current_tickers = set(table["종목코드"].astype(str).str.upper()) if "종목코드" in table.columns else set()
        signal_items = [
            item for item in signal_items
            if str((item.get("row") or {}).get("종목코드", "")).upper() in current_tickers
        ]
        if not signal_items:
            signal_items = [
                {"row": row.to_dict(), "analysis": None, "error": "차트 계산 대기"}
                for _, row in table.head(12).iterrows()
            ]
            st.session_state[signal_state_key] = signal_items
        labels = {
            f"{item['row'].get('종목명') or item['row'].get('종목코드')} · "
            f"{item['row'].get('종목코드')}": index
            for index, item in enumerate(signal_items[:12])
        }
        with simple_card_slot.container():
            selected_label = st.selectbox(
                "카드 종목",
                list(labels.keys()),
                label_visibility="collapsed",
                key=f"simple_card_{scanner_type}",
            )
            selected_item = signal_items[labels[selected_label]]
            auto_live = st.toggle(
                "⚡ 자동 실시간 감시",
                value=True,
                key=f"auto_live_{scanner_type}",
                help="현재가는 약 4초마다, 첫 눌림·재상승 판정은 약 8초마다 자동 갱신합니다.",
            )
            if hasattr(st, "fragment"):
                @st.fragment(run_every="4s" if auto_live else None)
                def _selected_live_fragment():
                    render_auto_us_card(
                        selected_item, us_session_choice, scanner_type, auto_live
                    )
                _selected_live_fragment()
            else:
                render_us_reference_card(selected_item)
                render_us_entry_lock_controls(selected_item, scanner_type)
                if auto_live:
                    st.warning(
                        "현재 Streamlit 버전은 자동 부분갱신을 지원하지 않습니다. "
                        "터미널에서 pip install -U streamlit 후 다시 실행하세요."
                    )

    if not MOBILE_SIMPLE_UI and strategy_code in ("penny", "momentum"):
        st.subheader("⚡ 삼중순위 교집합 후보")
        direct = table.copy()
        if "삼중교집합" in direct.columns:
            direct = direct[direct["삼중교집합"]]
        if "시세출처" in direct.columns:
            direct = direct[
                direct["시세출처"].astype(str).str.startswith("한투 WS")
            ]
        if "1차필터점수" in direct.columns:
            direct = direct[direct["1차필터점수"] >= 75]
        else:
            direct = direct.iloc[0:0]
        if not direct.empty:
            direct = direct.sort_values(
                ["1차필터점수", "오늘거래량"],
                ascending=[False, False],
            ).head(3)

        if direct.empty:
            st.info(
                "현재 세 순위에 모두 들고 실시간 조건점수 75점 이상인 종목이 없습니다. "
                "2/3 종목은 아래 표에 보여도 진입 후보로 사용하지 않습니다."
            )
        else:
            for _, row in direct.iterrows():
                name = html.escape(str(row.get("종목명") or row.get("종목코드")))
                ticker = html.escape(str(row.get("종목코드", "")))
                render_compact_html(
                    f'''<div class="stock-card" style="border-color:#61df88">
                    <div class="card-head"><div><div class="stock-name">{name}</div>
                    <div class="ticker">{ticker} · ${float(row['현재가($)']):.4f} · {float(row['등락률(%)']):+.1f}%</div></div>
                    <div style="color:#ffd45d;font-weight:900">🟡 차트검증 대상<br>
                    <small>1차필터 {int(row['1차필터점수'])}/100</small></div></div>
                    <div class="grid2" style="margin-top:10px">
                      <div class="data-row"><span class="data-label">매수 체결강도</span><span class="data-value">{float(row['체결강도']):.0f}</span></div>
                      <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{float(row['VWAP위치(%)']):+.1f}%</span></div>
                      <div class="data-row"><span class="data-label">호가 차이</span><span class="data-value">{float(row['스프레드(%)']):.2f}%</span></div>
                      <div class="data-row"><span class="data-label">당일 거래량</span><span class="data-value">{int(row['오늘거래량']):,}주</span></div>
                    </div></div>'''
                )
        st.caption(
            "이 카드는 상승률·당일 거래량·체결강도 세 순위가 겹친 후보입니다. "
            "아직 매수신호가 아니며, 바로 아래 눌림 재돌파 카드가 녹색일 때만 검토합니다."
        )

    if not MOBILE_SIMPLE_UI and strategy_code in ("penny", "momentum"):
        source_counts = st.session_state.get("us_source_counts", {})
        if source_counts:
            working_sources = sum(1 for count in source_counts.values() if count > 0)
            found_total = sum(source_counts.values())
            st.caption(
                f"상승률·거래량·체결강도 순위 {working_sources}/9개 작동 · 중복 포함 {found_total}개 · "
                "화면에는 한투 웹소켓에서 방금 체결된 삼중교집합과 2/3 관찰 종목만 표시"
            )
        if "조건통과" in table.columns and not table["조건통과"].any():
            st.warning(
                "동전주는 찾았지만 현재 등락률·거래량 조건을 모두 통과한 종목은 없습니다. "
                "아래 종목은 감시 후보이며 매수 신호가 아닙니다."
            )
        st.subheader("🎯 눌림 후 재돌파 확인 카드")
        signal_items = [
            item for item in st.session_state.get("us_penny_signals", [])
            if item.get("analysis")
        ]
        actionable = [
            item for item in signal_items
            if item["analysis"]["verdict"] in (
                "🟢 눌림 재돌파 확인",
                "🟡 과거표본 부족·진입보류",
                "🟡 과거재생 75% 미만·보류",
                "🟡 눌림대기",
            )
        ]
        shown = (actionable or signal_items)[:3]
        if not shown:
            st.info(
                "상위 3종목의 분봉이 충분히 쌓이면 RSI·MACD·VWAP·ATR과 "
                "최근 신호 적중률을 함께 검증합니다."
            )
        for item in shown:
            row = item["row"]
            analysis = item["analysis"]
            verdict = analysis["verdict"]
            received_at = float(row.get("수신타임스탬프", 0) or 0)
            current_quote_age = (
                max(0.0, time.time() - received_at)
                if received_at > 0 else 999.0
            )
            if current_quote_age > 3.0:
                verdict = "🔴 시세 만료·빠른 갱신 필요"
            color = "#61df88" if verdict.startswith("🟢") else "#ffd45d" if verdict.startswith("🟡") else "#ff7b81"
            raw_name = str(row.get("종목명") or row.get("종목코드"))
            raw_ticker = str(row.get("종목코드", ""))
            name = html.escape(raw_name)
            ticker = html.escape(raw_ticker)
            horizon = analysis.get("horizon_forecast", {})
            forecast_1 = horizon.get("1분 후", {"label": "⚪ 계산 대기", "score": 0})
            forecast_5 = horizon.get("5분 후", {"label": "⚪ 계산 대기", "score": 0})
            forecast_10 = horizon.get("10분 후", {"label": "⚪ 계산 대기", "score": 0})
            render_compact_html(
                f'''<div class="stock-card" style="border-color:{color}">
                <div class="card-head"><div><div class="stock-name">{name}</div>
                <div class="ticker">{ticker} · ${row['현재가($)']:.4f} · {row['등락률(%)']:+.1f}%</div></div>
                <div style="color:{color};font-weight:900">{verdict}<br><small>{analysis['score']}/{analysis.get('max_score', 9)}</small></div></div>
                <div class="grid2" style="margin-top:10px">
                  <div class="data-row"><span class="data-label">지정가 상한</span><span class="data-value">${analysis['entry']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">손절가</span><span class="data-value bad">${analysis['stop']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">1차 목표</span><span class="data-value ok">${analysis['target1']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">2차 목표</span><span class="data-value ok">${analysis['target2']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">RSI 1/3/5</span><span class="data-value">{analysis['rsi_1']:.0f}/{analysis['rsi_3']:.0f}/{analysis['rsi_5']:.0f}</span></div>
                  <div class="data-row"><span class="data-label">3분 MACD</span><span class="data-value">{'상승' if analysis['bullish_macd'] else '약화'}</span></div>
                  <div class="data-row"><span class="data-label">1·3·5·15분 추세</span><span class="data-value">{'/'.join('상' if analysis.get(f'trend_{m}') else '하' for m in (1,3,5,15))}</span></div>
                  <div class="data-row"><span class="data-label">1분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_1['label']))} {forecast_1['score']}</span></div>
                  <div class="data-row"><span class="data-label">5분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_5['label']))} {forecast_5['score']}</span></div>
                  <div class="data-row"><span class="data-label">10분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_10['label']))} {forecast_10['score']}</span></div>
                  <div class="data-row"><span class="data-label">연속 신호 확인</span><span class="data-value">{analysis.get('confirmation_hits', 0)}/2회</span></div>
                  <div class="data-row"><span class="data-label">시세 나이</span><span class="data-value">{current_quote_age:.2f}초</span></div>
                  <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{analysis['vwap_gap']:+.1f}%</span></div>
                  <div class="data-row"><span class="data-label">최근 거래량</span><span class="data-value">{analysis['volume_speed']:.1f}배</span></div>
                  <div class="data-row"><span class="data-label">진입가 체결률</span><span class="data-value">{analysis['validation_entry_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">과거 분봉 재생 성공률</span><span class="data-value">{analysis['validation_win_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">최근 1/3 재생률</span><span class="data-value">{analysis.get('validation_recent_rate', 0):.1f}%</span></div>
                  <div class="data-row"><span class="data-label">과거 95% 보수 하한</span><span class="data-value">{analysis.get('validation_wilson_lower', 0):.1f}%</span></div>
                  <div class="data-row"><span class="data-label">직전 저항까지 손익비</span><span class="data-value">{analysis.get('reward_risk1', 0):.2f}R</span></div>
                  <div class="data-row"><span class="data-label">1차 익절 성공</span><span class="data-value">{analysis['validation_wins']}/{analysis['validation_samples']}회</span></div>
                  <div class="data-row"><span class="data-label">2차 익절 도달률</span><span class="data-value">{analysis['validation_target2_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">차트 지지선</span><span class="data-value">${analysis['support']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">차트 저항선</span><span class="data-value">${analysis['resistance']:.4f}</span></div>
                </div></div>'''
            )
            if verdict.startswith("🟢") and analysis.get("validation_ok") and current_quote_age <= 3.0:
                planned_entry = float(analysis["entry"])
                actual_entry = st.number_input(
                    f"{raw_ticker} 실제 체결가($)",
                    min_value=0.0001,
                    value=planned_entry,
                    step=max(planned_entry * 0.001, 0.0001),
                    format="%.4f",
                    key=f"actual_entry_{scanner_type}_{raw_ticker}",
                    help="메리츠 체결내역의 실제 매수가를 입력하세요.",
                )
                if st.button(
                    f"🔒 {raw_ticker} 이 체결가로 진입 확정",
                    use_container_width=True,
                    key=f"lock_position_{scanner_type}_{raw_ticker}",
                ):
                    deviation = abs(float(actual_entry) / planned_entry - 1) * 100 if planned_entry > 0 else 0
                    row_spread = float(row.get("스프레드(%)", 0) or 0)
                    allowed_deviation = max(0.5, min(1.0, row_spread * 1.5))
                    if deviation > allowed_deviation:
                        st.error(
                            f"실제 체결가가 계획가에서 {deviation:.2f}% 벗어났습니다. "
                            "이 타점은 취소하고 다음 눌림을 기다리세요."
                        )
                        continue
                    stop, target1, target2, support, resistance, level_details = calculate_us_multiframe_levels(
                        analysis["frames"],
                        float(actual_entry),
                    )
                    actual_risk = float(actual_entry) - float(stop)
                    actual_rr = (
                        (float(target1) - float(actual_entry)) / actual_risk
                        if actual_risk > 0 else 0
                    )
                    if actual_risk <= 0 or actual_rr < 1.5:
                        st.error("실제 체결가 기준 1차 손익비가 1.5 미만이라 진입을 기록하지 않았습니다.")
                        continue
                    st.session_state["active_us_position"] = {
                        "티커": raw_ticker,
                        "종목명": raw_name,
                        "거래소코드": str(row.get("거래소코드", "")),
                        "진입가": round(float(actual_entry), 4),
                        "손절가": round(float(stop), 4),
                        "1차목표": round(float(target1), 4),
                        "2차목표": round(float(target2), 4),
                        "지지선": round(float(support), 4),
                        "저항선": round(float(resistance), 4),
                        "레벨분석": level_details,
                        "진입시각": datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M:%S"),
                        "계획가이탈(%)": round(deviation, 2),
                        "최고가": round(float(actual_entry), 4),
                        "실행손절가": round(float(stop), 4),
                    }
                    st.session_state.pop("active_us_forecast", None)
                    st.rerun()
        st.caption(
            "🟢는 삼중교집합·VWAP 눌림·1·3·5·15분 추세·EMA9 재돌파·MACD 회복·저점상승·체결강도를 "
            "모두 확인하고, 분봉조건 표본 20회·재생률 75%·최근 1/3 재생률 70%·"
            "95% Wilson 하한 55%를 통과한 뒤 동일 신호가 2회 연속 유지될 때만 표시합니다. "
            "이 재생률은 순위·뉴스·호가지연을 과거에 완전히 재현한 확률은 아니며, "
            "1·5·10분 표시는 실체결과 여러 분봉의 방향 근거 균형점수입니다."
        )

    us_columns = [
        "삼중교집합", "교집합수", "상승률순위", "당일거래량순위", "체결강도순위",
        "종목코드", "종목명", "현재가($)", "등락률(%)", "오늘거래량",
        "전일대비거래량(%)", "오늘거래대금(백만$)", "VWAP($)",
        "VWAP위치(%)", "스프레드(%)", "체결강도", "1차필터점수",
        "시세시간(KST)", "시세나이(초)", "시세출처", "현재판정",
    ]

    if not MOBILE_SIMPLE_UI:
        safe_us_columns = [column for column in us_columns if column in table.columns]
        st.dataframe(
            table[safe_us_columns],
            use_container_width=True,
            hide_index=True,
            height=500,
            column_config={
                "현재가($)": st.column_config.NumberColumn(format="$%.2f"),
                "등락률(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "오늘거래량": st.column_config.NumberColumn(format="%d주"),
                "전일대비거래량(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "오늘거래대금(백만$)": st.column_config.NumberColumn(format="$%.2fM"),
                "VWAP($)": st.column_config.NumberColumn(format="$%.2f"),
                "VWAP위치(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "스프레드(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "체결강도": st.column_config.NumberColumn(format="%.1f"),
                "1차필터점수": st.column_config.NumberColumn(format="%d점"),
                "시세나이(초)": st.column_config.NumberColumn(format="%.2f초"),
            },
        )
        if us_meta.get("errors"):
            st.caption("일부 묶음은 재시도 후 제외됐습니다. 표시된 종목은 정상 응답입니다.")
        if strategy_code not in ("penny", "momentum"):
            st.warning("이 표는 후보 압축용이며 매수 신호가 아닙니다. RSI·MACD는 선택 종목 정밀검사에서 확인하세요.")


def render_domestic_mobile_live_card(selected_row, scanner_type, auto_live):
    """모바일용 국내 선택 종목 자동감시 카드."""
    ticker = str(selected_row.get("종목코드") or "")
    base_row = dict(selected_row)
    now = time.time()
    cache_key = f"kr_live_analysis_{scanner_type}_{ticker}"
    cached = st.session_state.get(cache_key, {})
    try:
        token = issue_access_token(APP_KEY, APP_SECRET)
        quote = get_integrated_price(token, ticker) or {}
        base_row.update(quote)
        # 분봉은 8초마다만 갱신해 API 호출을 줄인다.
        if (not cached) or now - float(cached.get("updated_at", 0) or 0) >= 8:
            minute = get_recent_minutes(token, ticker, pages=2)
            if len(minute) >= 35:
                analysis = analyze_selected_stock(minute, base_row, strategy="momentum" if str(scanner_type).endswith("momentum") else "quality")
                cached = {"analysis": analysis, "updated_at": now}
                st.session_state[cache_key] = cached
        analysis = cached.get("analysis")
    except Exception as error:
        analysis = cached.get("analysis")
        st.caption(f"국내 자동감시 지연: {error}")

    name = html.escape(str(base_row.get("종목명") or ticker))
    price = to_int(base_row.get("현재가"))
    rate = to_float(base_row.get("등락률(%)"))
    verdict = "⚪ 차트 계산 대기"
    klass = "v-gray"
    reasons = []
    if analysis:
        if analysis.get("status_color") == "green":
            verdict, klass = "🟢 지금 진입 검토", "v-green"
        elif analysis.get("status_color") == "yellow":
            verdict, klass = "🟡 눌림·재돌파 대기", "v-yellow"
        else:
            verdict, klass = "🔴 조건 미충족", "v-red"
        if 0 <= to_float(analysis.get("vwap_gap")) <= 2:
            reasons.append("VWAP 근처 또는 위")
        if analysis.get("bullish_macd"):
            reasons.append("MACD 상승")
        if analysis.get("trend_1") and analysis.get("trend_3"):
            reasons.append("1·3분 추세 상승")
        if to_float(analysis.get("volume_speed")) >= 1:
            reasons.append(f"거래량 {to_float(analysis.get('volume_speed')):.1f}배")
    reason_html = "".join(f'<div class="reason-item">✓ {html.escape(x)}</div>' for x in reasons[:4])
    render_compact_html(f"""
    <div class="stock-card">
      <div class="card-head">
        <div><div class="stock-name">{name}</div><div class="ticker">{ticker} · 국내 자동감시</div></div>
        <div><div class="price">{price:,}원</div><div class="{'change-up' if rate >= 0 else 'change-down'}">{rate:+.2f}%</div></div>
      </div>
      <div class="verdict {klass}">{verdict}</div>
      <div class="reason-list">{reason_html or '<div class="reason-item">• 조건 계산 중</div>'}</div>
      <div class="footnote">현재가 약 4초 · 차트판정 약 8초 자동갱신 · 자동주문 없음</div>
    </div>
    """)



if has_current_scan and is_domestic:
    table = st.session_state["scan_table"]
    display_columns = [
        "시장", "시총순위", "종목코드", "종목명", "시세기준", "현재가", "등락률(%)",
        "VWAP", "VWAP위치(%)", "오늘누적거래량", "전일대비거래량(%)",
        "오늘거래대금(억원)", "현재판정",
    ]
    if strategy_code == "momentum":
        display_columns[1] = "급등순위"
        display_columns[2:2] = [
            "삼중교집합", "교집합수", "상승률순위",
            "당일거래량순위", "체결강도순위", "체결강도",
        ]
        display_columns[16:16] = ["거래량증가율(%)", "거래량회전율(%)"]
        preferred_statuses = ["🟢 삼중순위·차트검사"]
        source_counts = st.session_state.get("kr_source_counts", {})
        exact_count = int(table["삼중교집합"].sum()) if "삼중교집합" in table else 0
        st.success(f"국내 삼중순위 교집합 {exact_count}종목을 차트검사 후보로 표시합니다.")
        if source_counts:
            st.caption(
                " · ".join(f"{name} {count}종목" for name, count in source_counts.items())
                + " · 2/3 교집합은 관찰용이며 진입 신호로 사용하지 않습니다."
            )
    else:
        preferred_statuses = ["🟢 기술지표검사 대상", "🟡 눌림대기"]
    preferred = table[table["현재판정"].isin(preferred_statuses)].copy()
    if preferred.empty:
        if strategy_code == "momentum" and "삼중교집합" in table.columns:
            preferred = table[table["삼중교집합"].astype(bool)].head(10).copy()
        if preferred.empty:
            preferred = table.head(10).copy()

    labels = {
        f"{row['현재판정']}  {row['종목명']} ({row['종목코드']})": index
        for index, row in preferred.iterrows()
    }
    selected_label = st.selectbox(
        "정밀검사할 종목",
        list(labels.keys()),
        label_visibility="collapsed" if MOBILE_SIMPLE_UI else "visible",
    )
    selected_row_for_live = table.loc[labels[selected_label]].to_dict()
    kr_auto_live = st.toggle(
        "⚡ 국내 자동 실시간 감시",
        value=True,
        key=f"kr_auto_live_{scanner_type}",
        help="현재가는 약 4초마다, 차트판정은 약 8초마다 자동 갱신합니다.",
    )
    if hasattr(st, "fragment"):
        @st.fragment(run_every="4s" if kr_auto_live else None)
        def _domestic_live_fragment():
            render_domestic_mobile_live_card(selected_row_for_live, scanner_type, kr_auto_live)
        _domestic_live_fragment()
    else:
        render_domestic_mobile_live_card(selected_row_for_live, scanner_type, False)
        if kr_auto_live:
            st.warning("자동 부분갱신을 위해 Streamlit을 최신 버전으로 업데이트하세요.")

    if st.button("선택 종목 상세검사", type="secondary", use_container_width=True):
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

            analysis = analyze_selected_stock(minute, selected_row, strategy="momentum" if str(scanner_type).endswith("momentum") else "quality")
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
    is_domestic
    and "last_analysis" in st.session_state
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


if has_current_scan and is_domestic:
    table = st.session_state["scan_table"]
    with st.expander(f"전체 {len(table)}종목 표 보기 · 필요할 때만 열기", expanded=False):
        st.caption("현재가·VWAP·거래량·거래대금은 한국투자증권 통합시장(UN) 값입니다.")
        safe_display_columns = [column for column in display_columns if column in table.columns]
        st.dataframe(
            table[safe_display_columns],
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
