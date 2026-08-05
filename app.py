import html
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

try:
    import websocket
except Exception:
    websocket = None


st.set_page_config(
    page_title="한·미 단타 스캐너",
    page_icon="📡",
    layout="wide",
)

st.title("📡 한·미 당일 단타 스캐너")
st.caption("📱 모바일 V12 · 상승률×당일거래량×매수체결강도 교집합 + 눌림 재돌파")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 760px;}
    h1 {font-size: clamp(1.65rem, 7vw, 2.35rem) !important; line-height: 1.15 !important;}
    h2, h3 {font-size: 1.15rem !important;}
    div[data-testid="stButton"] > button {width: 100%; min-height: 3rem; font-weight: 800;}
    div[data-testid="stSelectbox"] {margin-bottom: .2rem;}
    .stock-card {
        background: #11151d;
        color: #e7ebf3;
        border: 1px solid #293142;
        border-radius: 18px;
        padding: 16px;
        margin: 10px 0;
        box-shadow: 0 8px 24px rgba(0,0,0,.18);
    }
    .card-head {display:flex; justify-content:space-between; gap:12px; align-items:flex-start;}
    .stock-name {font-size:1.3rem; font-weight:900; line-height:1.2;}
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
    .data-row {display:flex; justify-content:space-between; gap:8px; padding:9px 0; border-bottom:1px solid #252c37; font-size:.88rem;}
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
    @media (max-width: 520px) {
        .block-container {padding-left:.75rem; padding-right:.75rem;}
        .stock-card {padding:14px 12px; border-radius:14px;}
        .grid2 {grid-template-columns:1fr 1fr; gap:0 10px;}
        .data-row {font-size:.78rem;}
        .tf {font-size:.68rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
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


@st.cache_data(ttl=3600, show_spinner=False)
def issue_ws_approval_key(app_key, app_secret):
    """해외주식 실시간 체결 웹소켓 접속키를 1시간 재사용한다."""
    response = requests.post(
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
    data = response.json()
    approval_key = data.get("approval_key")
    if not approval_key:
        raise RuntimeError(data.get("msg1") or "실시간 접속키를 받지 못했습니다.")
    return approval_key


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


def get_volume_rank(token, sort_code):
    """sort_code: 0=당일 절대거래량, 1=거래증가율, 3=거래금액순"""
    response = None
    for attempt in range(3):
        response = requests.get(
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
            timeout=20,
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

    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1", "거래량 순위를 받지 못했습니다."))
    return data.get("output", [])


def get_domestic_rank(token, endpoint, tr_id, params, label):
    """한투 국내주식 순위 API 공통 호출."""
    response = None
    for attempt in range(3):
        response = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=make_headers(token, tr_id),
            params=params,
            timeout=20,
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

    data = response.json()
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
    with ThreadPoolExecutor(max_workers=3) as executor:
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
        response = requests.get(
            f"{BASE_URL}{endpoint}",
            headers=make_headers(token, tr_id),
            params=params,
            timeout=20,
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

    data = response.json()
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


def build_us_triple_rank_rows(updown_rows, volume_rows, power_rows, session_mode, penny_only):
    """상승률·절대 당일거래량·매수체결강도 상위의 정확한 교집합을 만든다.

    API가 돌려준 목록 전체를 미국 3개 거래소 기준으로 다시 정렬한다.
    3개 순위에 모두 든 종목만 삼중교집합이며, 2개만 겹친 종목은
    데이터 확인용 관찰 후보로만 남긴다.
    """
    merged = {}

    def absorb(rows, source):
        for raw in rows:
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
                "_base_exchange": exchange,
                "_session_mode": session_mode,
                "symb": ticker,
                "excd": exchange,
                "knam": name or ticker,
                "enam": str(row.get("enam") or row.get("ename") or "").strip(),
                "last": 0,
                "base": 0,
                "rate": 0,
                "tvol": 0,
                "tamt": 0,
                "pvol": 0,
                "pask": 0,
                "pbid": 0,
                "powx": 0,
                "tpow": 0,
                "_gain_member": False,
                "_volume_member": False,
                "_power_member": False,
            })
            if name and item.get("knam") in ("", ticker):
                item["knam"] = name
            for field in ("last", "base", "pask", "pbid", "tomv"):
                if to_float(row.get(field)) > 0:
                    item[field] = row.get(field)
            if source == "gain":
                item["_gain_member"] = True
                item["rate"] = row.get("rate")
                if to_int(row.get("tvol")) > to_int(item.get("tvol")):
                    item["tvol"] = row.get("tvol")
            elif source == "volume":
                item["_volume_member"] = True
                item["tvol"] = row.get("tvol")
                item["tamt"] = row.get("tamt")
                item["pvol"] = row.get("pvol")
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

    absorb(updown_rows, "gain")
    absorb(volume_rows, "volume")
    absorb(power_rows, "power")

    items = list(merged.values())
    if penny_only:
        items = [item for item in items if 0.01 <= to_float(item.get("last")) <= 10]

    gain_items = sorted(
        [item for item in items if item["_gain_member"]],
        key=lambda item: to_float(item.get("rate")),
        reverse=True,
    )[:100]
    volume_items = sorted(
        [item for item in items if item["_volume_member"]],
        key=lambda item: to_int(item.get("tvol")),
        reverse=True,
    )[:100]
    power_items = sorted(
        [item for item in items if item["_power_member"]],
        key=lambda item: max(to_float(item.get("tpow")), to_float(item.get("powx"))),
        reverse=True,
    )[:100]

    gain_rank = {_us_rank_key(item): index for index, item in enumerate(gain_items, 1)}
    volume_rank = {_us_rank_key(item): index for index, item in enumerate(volume_items, 1)}
    power_rank = {_us_rank_key(item): index for index, item in enumerate(power_items, 1)}

    output = []
    for item in items:
        key = _us_rank_key(item)
        item["_gain_rank"] = gain_rank.get(key, 0)
        item["_volume_rank"] = volume_rank.get(key, 0)
        item["_power_rank"] = power_rank.get(key, 0)
        overlap = sum(rank > 0 for rank in (
            item["_gain_rank"], item["_volume_rank"], item["_power_rank"]
        ))
        item["_overlap_count"] = overlap
        item["_triple_intersection"] = overlap == 3
        # 체결강도 API의 당일값을 화면의 기본 체결강도로 사용한다.
        item["powx"] = max(to_float(item.get("tpow")), to_float(item.get("powx")))
        if overlap >= 2:
            output.append(item)

    output.sort(key=lambda item: (
        not item["_triple_intersection"],
        -item["_overlap_count"],
        item["_gain_rank"] or 999,
        item["_volume_rank"] or 999,
        item["_power_rank"] or 999,
    ))
    pairs = [(_us_rank_key(item)) for item in output[:30]]
    return output[:30], pairs


@st.cache_data(ttl=3, show_spinner=False)
def get_us_triple_rank_rows(token, penny_only):
    """미국 3개 거래소×3개 공식 순위를 동시에 받아 교집합을 만든다."""
    exchanges = ("NAS", "NYS", "AMS")
    calls = {
        "상승률": get_us_updown_rows,
        "당일거래량": lambda value, exchange: get_us_trade_volume_rows(
            value, exchange, penny_only
        ),
        "체결강도": get_us_volume_power_rows,
    }
    grouped = {"상승률": [], "당일거래량": [], "체결강도": []}
    counts = {}
    errors = []
    with ThreadPoolExecutor(max_workers=9) as executor:
        futures = {}
        for label, function in calls.items():
            for exchange in exchanges:
                future = executor.submit(function, token, exchange)
                futures[future] = (label, exchange)
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
            response = requests.get(
                f"{BASE_URL}/uapi/overseas-price/v1/quotations/inquire-search",
                headers=make_headers(token, "HHDFS76410000"),
                params=params,
                timeout=15,
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

    data = response.json()
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


def get_us_live_snapshots(pairs, session_mode, wait_seconds=2.2, limit=30):
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
                response = requests.get(
                    f"{BASE_URL}/uapi/overseas-price/v1/quotations/multprice",
                    headers=make_headers(token, "HHDFS76220000"),
                    params=params,
                    timeout=12,
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
        data = response.json()
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
    response = requests.get(
        url,
        params={"scrIds": screen_id, "count": "100", "start": "0"},
        headers={"user-agent": "Mozilla/5.0"},
        timeout=4,
    )
    response.raise_for_status()
    data = response.json()
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
    response = requests.get(
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
        elif strategy == "momentum":
            passed = rate >= 2 and volume >= 10_000 and amount >= 100_000
            if rate >= 25 or vwap_gap >= 10:
                status = "🔴 추격주의"
            elif passed and price >= vwap:
                status = "🟢 급등 정밀검사"
            elif rate >= 1 or volume_ratio >= 120:
                status = "🟡 거래량 확대 감시"
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
        table.loc[~table["삼중교집합"], "현재판정"] = "🟡 2/3 관찰만·진입금지"
        table.loc[
            table["삼중교집합"] & table["조건통과"],
            "현재판정",
        ] = "🟢 삼중순위 차트검사"
        return table.sort_values(
            ["삼중교집합", "교집합수", "오늘거래량", "고속점수"],
            ascending=[False, False, False, False],
        ).head(30).reset_index(drop=True)
    return table.sort_values(
        ["시가총액(API)", "오늘거래대금(백만$)"], ascending=False
    ).head(30).reset_index(drop=True)


def apply_us_live_snapshots(table, snapshots, strategy):
    """웹소켓으로 새 체결을 받은 종목만 표의 가격·거래량을 교체한다."""
    if table.empty:
        return table

    # 버튼을 다시 누를 때마다 방금 받은 실체결을 세션에 누적해
    # 1·5·10분 방향 보조판정의 틱 입력으로 사용한다.
    tick_store = st.session_state.setdefault("us_tick_history", {})
    received_at = datetime.now(SEOUL).timestamp()
    for ticker, snap in snapshots.items():
        key = str(ticker).upper()
        history = tick_store.setdefault(key, [])
        history.append({
            "ts": received_at,
            "price": float(snap.get("last", 0) or 0),
            "volume": int(snap.get("tvol", 0) or 0),
            "strength": float(snap.get("strength", 0) or 0),
            "bid": float(snap.get("bid", 0) or 0),
            "ask": float(snap.get("ask", 0) or 0),
        })
        tick_store[key] = history[-120:]

    result = table.copy()
    if "시세출처" not in result.columns:
        result["시세출처"] = "한투 REST(지연 가능)"
    for column in ("진입검토가($)", "손절기준($)", "1차목표($)", "2차목표($)"):
        if column not in result.columns:
            result[column] = 0.0
    if "진입점수" not in result.columns:
        result["진입점수"] = 0
    if "당일거래량순위" not in result.columns:
        result["당일거래량순위"] = 0

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
        result.at[index, "시세시간(KST)"] = f"{snap['date']} {snap['time']}"
        result.at[index, "시세출처"] = "한투 WS 실시간지연체결"

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
            10  # 방금 웹소켓 체결
            + (20 if strength_ok else 0)
            + (20 if vwap_ok else 0)
            + (15 if spread_ok else 0)
            + (10 if rate_ok else 0)
            + (15 if liquidity_ok else 0)
            + (10 if heat_ok else 0)
        )
        result.at[index, "진입점수"] = signal_score
        triple_intersection = bool(row.get("삼중교집합", False))

        if strategy == "penny":
            passed = 0.01 <= price <= 10 and rate >= 1 and volume >= 20_000 and amount >= 10_000
            if rate >= 80 or vwap_gap >= 12 or spread > 5:
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
            passed = rate >= 2 and volume >= 10_000 and amount >= 100_000
            if rate >= 25 or vwap_gap >= 10 or spread > 5:
                status = "🔴 추격주의"
            elif passed and signal_score >= 75:
                status = "🟢 75점 정밀검증 대상"
            elif passed:
                status = "🟡 VWAP 눌림대기"
            else:
                status = "⚪ 조건대기"
        else:
            passed = 0.2 <= rate <= 8 and amount >= 1_000_000
            if rate >= 10 or vwap_gap >= 6:
                status = "🔴 추격주의"
            elif passed and vwap_gap >= 0 and strength >= 105:
                status = "🟢 기술지표 후보"
            elif passed:
                status = "🟡 매수세 확인 대기"
            else:
                status = "⚪ 유동성 관찰"
        if strategy in ("penny", "momentum"):
            if not triple_intersection:
                status = "🟡 2/3 관찰만·진입금지"
                passed = False
                signal_score = min(signal_score, 74)
                result.at[index, "진입점수"] = signal_score
            elif passed and signal_score >= 75:
                status = "🟢 삼중순위·눌림검사 대상"
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
            column for column in ("삼중교집합", "교집합수", "오늘거래량")
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


def get_minute_page(token, ticker, end_time, market_code="UN"):
    response = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "FHKST03010200"),
        params={
            "FID_COND_MRKT_DIV_CODE": market_code,
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


@st.cache_data(ttl=8, show_spinner=False)
def get_us_recent_minutes(token, exchange, ticker, session_mode):
    """한투 해외주식 분봉을 1회 호출로 최대 120개 받는다."""
    response = requests.get(
        f"{BASE_URL}/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
        headers=make_headers(token, "HHDFS76950200"),
        params={
            "AUTH": "",
            "EXCD": session_exchange(exchange, session_mode),
            "SYMB": ticker,
            "NMIN": "1",
            "PINC": "0",
            "NEXT": "",
            "NREC": "120",
            "FILL": "",
            "KEYB": "",
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker} 미국 분봉 조회 실패: HTTP {response.status_code}")
    data = response.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(data.get("msg1") or f"{ticker} 미국 분봉을 받지 못했습니다.")

    records = []
    for row in data.get("output2") or []:
        date_text = str(row.get("tymd", "")).strip()
        time_text = str(row.get("xhms", "")).strip().zfill(6)
        if len(date_text) != 8 or len(time_text) != 6:
            continue
        try:
            timestamp = pd.to_datetime(date_text + time_text, format="%Y%m%d%H%M%S")
        except Exception:
            continue
        records.append({
            "시간": timestamp,
            "시가": to_float(row.get("open")),
            "고가": to_float(row.get("high")),
            "저가": to_float(row.get("low")),
            "종가": to_float(row.get("last")),
            "거래량": to_float(row.get("evol")),
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
    triple_ok = bool(quote_row.get("삼중교집합", True))

    score = sum([
        price_ok,
        rsi_ok,
        bullish_macd,
        trend_ok,
        trend_15,
        volume_ok,
    ])

    if not triple_ok:
        verdict = "⚪ 삼중교집합 아님·진입금지"
    elif overheated:
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

    timeframe_summary = {}
    for minutes, frame in frames.items():
        rsi_value = safe_last(frame["RSI"], float("nan"))
        macd_value = safe_last(frame["MACD"])
        signal_value = safe_last(frame["MACD시그널"])
        timeframe_summary[minutes] = {
            "trend": safe_last(frame["종가"]) >= safe_last(frame["EMA9"]),
            "rsi": rsi_value,
            "macd_up": macd_value > signal_value,
            "bars": len(frame),
        }

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
        "triple_intersection": triple_ok,
        "entry": round(entry),
        "stop": round(stop),
        "target1": round(target1),
        "target2": round(target2),
        "timeframe_summary": timeframe_summary,
    }


def analyze_us_penny_stock(minute, quote_row):
    frames = {minutes: add_indicators(resample_bars(minute, minutes)) for minutes in (1, 3, 5, 15)}
    one, three, five, fifteen = frames[1], frames[3], frames[5], frames[15]
    price = float(quote_row["현재가($)"])
    vwap = float(quote_row["VWAP($)"])
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
    triple_ok = bool(quote_row.get("삼중교집합", False))

    checks = {
        "VWAP 위 0~5%": 0 <= vwap_gap <= 5,
        "RSI 비과열": 45 <= rsi_3 <= 70 and rsi_1 < 78,
        "1분 모멘텀 회복": trend_1 and macd_improving(one),
        "3분 MACD 상승": bullish_macd,
        "3분 EMA9 위": trend_3,
        "5분 추세 지속": trend_5 and macd_improving(five),
        "15분 큰 추세 상승": trend_15,
        "최근 거래량 1.2배": volume_speed >= 1.2,
        "매수 체결강도 120 이상": strength >= 120,
        "스프레드 3% 이하": spread_pct == 0 or spread_pct <= 3,
        "눌림 후 재상승": -10 <= pullback_pct <= 0 and reclaim,
        "상승률·거래량·체결강도 교집합": triple_ok,
    }
    score = sum(checks.values())
    overheated = change_pct >= 80 or vwap_gap > 10 or rsi_1 >= 85 or spread_pct > 6
    mandatory = (
        checks["VWAP 위 0~5%"]
        and checks["1분 모멘텀 회복"]
        and checks["3분 MACD 상승"]
        and checks["3분 EMA9 위"]
        and checks["5분 추세 지속"]
        and checks["15분 큰 추세 상승"]
        and checks["매수 체결강도 120 이상"]
        and checks["눌림 후 재상승"]
        and checks["상승률·거래량·체결강도 교집합"]
    )

    validation = backtest_us_entry_condition(minute)
    validation_ok = (
        validation["samples"] >= 20
        and validation["full_success_rate"] >= 75
    )

    green_score = max(10, len(checks) - 2)
    if overheated:
        verdict = "🔴 추격금지"
    elif score >= green_score and mandatory and validation_ok:
        verdict = "🟢 눌림 재돌파 확인"
    elif score >= green_score and mandatory and validation["samples"] < 20:
        verdict = "🟡 과거표본 부족·진입보류"
    elif score >= green_score and mandatory:
        verdict = "🟡 과거재생 75% 미만·보류"
    elif not triple_ok:
        verdict = "⚪ 삼중교집합 아님"
    elif score >= 3:
        verdict = "🟡 눌림대기"
    else:
        verdict = "⚪ 진입금지"

    # 녹색 판정은 눌림을 이미 거친 재돌파 시점이므로 현재 확인가를 기준으로 한다.
    # 녹색이 아니면 EMA9·VWAP 재확인 가격만 제시하며 진입 신호로 사용하지 않는다.
    ema_entry = safe_last(one["EMA9"], price)
    reference_entry = max(ema_entry, vwap) if vwap > 0 else ema_entry
    entry = price if verdict.startswith("🟢") else min(price, reference_entry) if reference_entry > 0 else price
    stop, target1, target2, support, resistance, level_details = calculate_us_multiframe_levels(
        frames,
        entry,
    )
    horizon_forecast = forecast_us_tick_horizons(frames, quote_row)
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
        "triple_intersection": triple_ok,
        "validation_entry_hits": validation["entry_hits"],
        "validation_entry_rate": validation["entry_hit_rate"],
        "validation_wins": validation["target1_wins"],
        "validation_samples": validation["samples"],
        "validation_win_rate": validation["full_success_rate"],
        "validation_target2_wins": validation["target2_wins"],
        "validation_target2_rate": validation["target2_success_rate"],
        "validation_ok": validation_ok,
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

    risk_unit = max(risks + [entry * 0.012])
    # 동전주의 아주 넓은 시세폭이 손절을 -7% 이상으로 밀지 못하게 한다.
    risk_unit = min(risk_unit, entry * 0.04)
    nearest_support = max(supports) if supports else entry - risk_unit
    chart_stop = nearest_support * 0.997
    volatility_stop = entry - risk_unit
    stop = min(chart_stop, volatility_stop)
    stop = max(stop, entry * 0.95)
    if stop <= 0 or stop >= entry:
        stop = entry * 0.97

    risk = entry - stop
    valid_resistances = sorted(
        value for value in resistances
        if value >= entry + risk * 1.05
    )
    target1 = valid_resistances[0] if valid_resistances else entry + risk * 1.35
    target1 = min(target1, entry + risk * 2.5)
    target1 = max(target1, entry + risk * 1.15)
    higher_resistances = [value for value in valid_resistances if value > target1 + risk * 0.25]
    target2 = higher_resistances[0] if higher_resistances else entry + risk * 2.4
    target2 = max(target2, target1 + risk * 0.75)
    target2 = min(target2, entry + risk * 4.0)
    resistance = valid_resistances[0] if valid_resistances else target1
    return stop, target1, target2, nearest_support, resistance, details


def forecast_us_tick_horizons(frames, quote_row):
    """누적된 웹소켓 틱과 1·3·5·15분봉으로 1·5·10분 방향을 보조판정한다."""
    ticker = str(quote_row.get("종목코드", "")).upper()
    ticks = st.session_state.get("us_tick_history", {}).get(ticker, [])
    usable_ticks = [item for item in ticks if float(item.get("price", 0)) > 0]
    tick_score = 50.0
    tick_reason = "틱 부족"
    if len(usable_ticks) >= 3:
        recent = usable_ticks[-8:]
        prices = [float(item["price"]) for item in recent]
        volumes = [int(item.get("volume", 0)) for item in recent]
        up_moves = sum(1 for before, after in zip(prices, prices[1:]) if after > before)
        down_moves = sum(1 for before, after in zip(prices, prices[1:]) if after < before)
        price_change = (prices[-1] / prices[0] - 1) * 100 if prices[0] > 0 else 0
        volume_delta = max(0, volumes[-1] - volumes[0]) if volumes else 0
        strength = float(recent[-1].get("strength", 0) or 0)
        tick_score += min(18, max(-18, price_change * 18))
        tick_score += min(10, max(-10, (up_moves - down_moves) * 2.5))
        tick_score += 8 if strength >= 120 else -8 if 0 < strength < 90 else 0
        tick_score += 4 if volume_delta > 0 else 0
        tick_reason = f"최근 {len(recent)}틱 상승 {up_moves}·하락 {down_moves}"

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
        if len(usable_ticks) < 3:
            return "⚪ 틱 자료 추가 필요"
        if score >= 65:
            return "🟢 상승 우세"
        if score <= 38:
            return "🔴 하락 우세"
        return "🟡 혼조·확인 필요"

    return {
        horizon: {"label": label(score), "score": round(score), "reason": tick_reason}
        for horizon, score in scores.items()
    }


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
    }
    if len(bars) < 55:
        return empty_result

    recent_volume = bars["거래량"].rolling(3).mean()
    prior_volume = bars["거래량"].shift(3).rolling(20).mean()
    bars["거래량속도"] = recent_volume / prior_volume.replace(0, float("nan"))

    typical = (bars["고가"] + bars["저가"] + bars["종가"]) / 3
    cumulative_value = (typical * bars["거래량"]).cumsum()
    cumulative_volume = bars["거래량"].cumsum().replace(0, float("nan"))
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
        if target2_hit:
            target2_wins += 1
        # 거의 같은 분봉에서 발생한 중복 신호를 하나로 압축한다.
        index += 5

    if samples == 0:
        return empty_result
    return {
        "samples": samples,
        "entry_hits": entry_hits,
        "entry_hit_rate": round(entry_hits / samples * 100, 1),
        "target1_wins": target1_wins,
        "full_success_rate": round(target1_wins / samples * 100, 1),
        "target2_wins": target2_wins,
        "target2_success_rate": round(target2_wins / samples * 100, 1),
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


def analyze_us_penny_candidates(token, table, session_mode, limit=8):
    """삼중교집합 종목만 분봉을 동시 조회해 눌림 재돌파를 검사한다."""
    eligible = table.copy()
    if "삼중교집합" in eligible.columns:
        eligible = eligible[eligible["삼중교집합"]]
    targets = [row.to_dict() for _, row in eligible.head(limit).iterrows()]

    def inspect(row):
        minute = get_us_recent_minutes(
            token,
            str(row["거래소코드"]),
            str(row["종목코드"]),
            session_mode,
        )
        if len(minute) < 35:
            return {
                "row": row,
                "analysis": None,
                "error": f"분봉 {len(minute)}개(최소 35개 필요)",
            }
        return {"row": row, "analysis": analyze_us_penny_stock(minute, row), "error": ""}

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(inspect, row) for row in targets]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:
                results.append({"row": {}, "analysis": None, "error": str(error)})

    verdict_order = {
        "🟢 눌림 재돌파 확인": 0,
        "🟡 과거표본 부족·진입보류": 1,
        "🟡 과거재생 75% 미만·보류": 2,
        "🟡 눌림대기": 3,
        "⚪ 삼중교집합 아님": 4,
        "⚪ 진입금지": 4,
        "🔴 추격금지": 5,
    }
    results.sort(key=lambda item: (
        verdict_order.get((item.get("analysis") or {}).get("verdict"), 9),
        -float((item.get("analysis") or {}).get("score", 0)),
    ))
    return results


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
    return result.sort_values(sort_columns, ascending=[False] * len(sort_columns)).reset_index(drop=True)


if not APP_KEY or not APP_SECRET:
    st.error("한국투자증권 API 키가 설정되지 않았습니다.")
    st.stop()

st.caption("✅ 한국투자증권 국내 통합시장·미국 시세 API 연결 준비")

market_label = st.radio(
    "시장 선택",
    ["🇰🇷 국내주식", "🇺🇸 미국주식"],
    horizontal=True,
    label_visibility="collapsed",
)
market_code = "kr" if market_label.startswith("🇰🇷") else "us"
strategy_options = ["🏦 우량주 단타", "🔥 급등주 단타"]
if market_code == "us":
    strategy_options.append("🪙 동전주 급등")
strategy_label = st.radio(
    "검색 방식",
    strategy_options,
    horizontal=True,
    label_visibility="collapsed",
)

if strategy_label.startswith("🏦"):
    strategy_code = "quality"
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
    "us_momentum": "미국 급등주 삼중순위 교집합 검색",
    "us_penny": "미국 동전주 삼중순위 교집합 검색",
}

us_session_choice = "자동(현재 장)"
if not is_domestic:
    st.info(
        "미국 V12는 한투 공식 상승률 상위·당일 누적거래량 상위·매수체결강도 상위를 "
        "동시에 받은 뒤 세 순위의 교집합만 차트검사합니다. "
        "교집합 검색은 후보 압축이며 매수신호가 아닙니다."
    )
    us_session_choice = st.selectbox(
        "미국장 시세 선택",
        ["자동(현재 장)", "주간거래", "프리·정규·애프터"],
        help="자동은 한국시간에 따라 주간거래 코드와 미국 정규거래소 코드를 바꾸어 조회합니다.",
    )
    if strategy_code in ("momentum", "penny"):
        st.caption(
            "① 삼중순위 교집합 → ② ETF·ETN·SPAC 제외 → "
            "③ VWAP 눌림 → ④ EMA9 재돌파·MACD 회복·저점상승 확인"
        )
elif strategy_code == "momentum":
    st.info(
        "국내 V12는 한투 공식 등락률 상위·당일 절대거래량 상위·"
        "매수체결강도 상위를 동시에 받아 3개 순위의 교집합만 차트를 검사합니다. "
        "한투 순위 API가 한 번에 제공하는 상위 목록 범위를 사용합니다."
    )

if st.button(scan_button_labels[scanner_type], type="primary"):
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

        else:
            scan_name = "동전주 급등" if strategy_code == "penny" else "급등주"
            with st.spinner(f"미국 {scan_name} 상승률·거래량·체결강도 순위를 동시에 받는 중입니다..."):
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
                )
                table = build_us_fast_table(
                    market_rows, us_candidates, strategy=strategy_code
                )
                table["시세출처"] = "한투 공식 삼중순위"
                exact_count = int(table["삼중교집합"].sum()) if not table.empty else 0
                us_source_note = (
                    f"상승률·당일거래량·체결강도 삼중교집합 {exact_count}개 "
                    f"(2/3 관찰 포함 {len(us_candidates)}개)"
                )
                price_errors = list(rank_errors)
                st.session_state["us_source_counts"] = source_counts

        if not is_domestic and not table.empty:
            live_pairs = list(zip(table["거래소코드"], table["종목코드"]))
            live_snapshots, live_errors = get_us_live_snapshots(
                live_pairs,
                session_mode,
                wait_seconds=(1.0 if strategy_code in ("penny", "momentum") else 2.2),
                limit=30,
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

        # 삼중교집합 중 상위 3종목만 분봉을 재생해 눌림·재돌파와 레벨을 계산한다.
        st.session_state.pop("us_penny_signals", None)
        if not is_domestic and strategy_code in ("penny", "momentum"):
            with st.spinner("상위 3종목의 차트·적중률을 동시 검증하는 중..."):
                st.session_state["us_penny_signals"] = analyze_us_penny_candidates(
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
        kind_text = "우량주" if strategy_code == "quality" else "동전주" if strategy_code == "penny" else "급등주"
        st.toast(f"{market_text} {kind_text} {len(table)}종목 갱신 완료")
    except Exception as error:
        st.error("종목 검사에 실패했습니다.")
        st.code(str(error))


has_current_scan = (
    "scan_table" in st.session_state
    and st.session_state.get("scan_type") == scanner_type
)

if has_current_scan and not is_domestic:
    table = st.session_state["scan_table"]
    us_meta = st.session_state.get("us_scan_meta", {})
    us_kind = "우량주" if strategy_code == "quality" else "동전주 급등" if strategy_code == "penny" else "급등주"
    st.success(f"미국 {us_kind} 후보 {len(table)}종목을 받았습니다.")
    st.caption(
        f"세션: {us_meta.get('session', '-')} · "
        f"갱신: {us_meta.get('time', '-')} KST · "
        f"후보: {us_meta.get('source', '-')}. "
        f"웹소켓 실체결: {st.session_state.get('us_live_count', 0)}종목."
    )

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
        locked_entry = float(active_position["진입가"])
        locked_stop = float(active_position["손절가"])
        locked_target1 = float(active_position["1차목표"])
        locked_target2 = float(active_position["2차목표"])
        return_pct = (
            (current_price / locked_entry - 1) * 100
            if current_price > 0 and locked_entry > 0
            else 0.0
        )
        if current_price > 0 and current_price <= locked_stop:
            position_status = "🔴 손절 기준 도달"
            position_color = "#ff7b81"
        elif current_price >= locked_target2:
            position_status = "🟢 2차 목표 도달"
            position_color = "#61df88"
        elif current_price >= locked_target1:
            position_status = "🟢 1차 목표 도달·분할매도"
            position_color = "#61df88"
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

        st.subheader("🔒 내 보유 포지션 · 매매계획 고정")
        position_name = html.escape(str(active_position["종목명"]))
        st.markdown(
            f'''<div class="stock-card" style="border-color:{position_color}">
            <div class="card-head"><div><div class="stock-name">{position_name}</div>
            <div class="ticker">{html.escape(active_ticker)} · 진입 {locked_entry:.4f}달러</div></div>
            <div style="color:{position_color};font-weight:900">{position_status}</div></div>
            <div class="grid2" style="margin-top:10px">
              <div class="data-row"><span class="data-label">현재가만 갱신</span><span class="data-value">${current_price:.4f}</span></div>
              <div class="data-row"><span class="data-label">현재 수익률</span><span class="data-value">{return_pct:+.2f}%</span></div>
              <div class="data-row"><span class="data-label">고정 손절가</span><span class="data-value bad">${locked_stop:.4f}</span></div>
              <div class="data-row"><span class="data-label">고정 1차 매도가</span><span class="data-value ok">${locked_target1:.4f}</span></div>
              <div class="data-row"><span class="data-label">고정 2차 매도가</span><span class="data-value ok">${locked_target2:.4f}</span></div>
              <div class="data-row"><span class="data-label">진입 시각</span><span class="data-value">{html.escape(str(active_position['진입시각']))}</span></div>
              <div class="data-row"><span class="data-label">현재 차트흐름</span><span class="data-value">{html.escape(str(flow_state))}</span></div>
              <div class="data-row"><span class="data-label">상승흐름 점수</span><span class="data-value">{flow_score}/100</span></div>
              <div class="data-row"><span class="data-label">회복/목표 예상</span><span class="data-value">{html.escape(str(flow_recovery))}</span></div>
              <div class="data-row"><span class="data-label">판단 근거</span><span class="data-value">{html.escape(str(flow_reason))}</span></div>
              <div class="data-row"><span class="data-label">1분 후 방향</span><span class="data-value">{html.escape(str(flow_1))}</span></div>
              <div class="data-row"><span class="data-label">5분 후 방향</span><span class="data-value">{html.escape(str(flow_5))}</span></div>
              <div class="data-row"><span class="data-label">10분 후 방향</span><span class="data-value">{html.escape(str(flow_10))}</span></div>
            </div></div>''',
            unsafe_allow_html=True,
        )
        st.caption("스캐너를 다시 검색해도 이 진입가·손절가·1·2차 매도가는 바뀌지 않습니다.")
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
            st.session_state.pop("active_us_position", None)
            st.session_state.pop("active_us_forecast", None)
            st.rerun()

    quick_col, signal_col = st.columns(2)
    if quick_col.button("⚡ 현재 종목 1초 갱신", use_container_width=True):
        try:
            session_mode, session_detail, scan_time = resolve_us_session(us_session_choice)
            pairs = list(zip(table["거래소코드"], table["종목코드"]))
            with st.spinner("한투 미국 실시간 체결을 받는 중..."):
                snapshots, live_errors = get_us_live_snapshots(
                    pairs, session_mode, wait_seconds=1.0, limit=30
                )
            if not snapshots:
                raise RuntimeError(
                    "1초 동안 새 체결을 받지 못했습니다. "
                    "현재 장 선택과 거래 시간을 확인해 주세요."
                )
            table = apply_us_live_snapshots(table, snapshots, strategy_code)
            st.session_state["scan_table"] = table
            st.session_state["us_live_count"] = len(snapshots)
            st.session_state["us_scan_meta"] = {
                **us_meta,
                "session": session_detail,
                "time": scan_time,
                "errors": live_errors,
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
                    )
                    st.session_state["active_us_forecast"] = {
                        "티커": active_ticker,
                        **forecast_us_position_flow(
                            active_minutes,
                            active_quote,
                            active_position,
                        ),
                    }
            st.toast(f"실시간 체결 {len(snapshots)}종목 갱신 완료")
            st.rerun()
        except Exception as error:
            st.warning(str(error))

    if strategy_code in ("penny", "momentum") and signal_col.button(
        "🎯 상위 3종목 차트·승률 재검증",
        use_container_width=True,
    ):
        try:
            token = issue_access_token(APP_KEY, APP_SECRET)
            session_mode, _, _ = resolve_us_session(us_session_choice)
            with st.spinner("상위 3종목의 지지·저항·ATR·전체 매매성공률을 검증하는 중..."):
                st.session_state["us_penny_signals"] = analyze_us_penny_candidates(
                    token, table, session_mode, limit=3
                )
            st.toast("매수타점 정밀검사 완료")
        except Exception as error:
            st.warning(str(error))

    if strategy_code in ("penny", "momentum"):
        st.subheader("⚡ 삼중순위 교집합 후보")
        direct = table.copy()
        if "삼중교집합" in direct.columns:
            direct = direct[direct["삼중교집합"]]
        if "시세출처" in direct.columns:
            direct = direct[
                direct["시세출처"].astype(str).str.startswith("한투 WS")
            ]
        if "진입점수" in direct.columns:
            direct = direct[direct["진입점수"] >= 75]
        else:
            direct = direct.iloc[0:0]
        if not direct.empty:
            direct = direct.sort_values(
                ["진입점수", "오늘거래량"],
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
                st.markdown(
                    f'''<div class="stock-card" style="border-color:#61df88">
                    <div class="card-head"><div><div class="stock-name">{name}</div>
                    <div class="ticker">{ticker} · ${float(row['현재가($)']):.4f} · {float(row['등락률(%)']):+.1f}%</div></div>
                    <div style="color:#ffd45d;font-weight:900">🟡 차트검증 대상<br>
                    <small>{int(row['진입점수'])}/100</small></div></div>
                    <div class="grid2" style="margin-top:10px">
                      <div class="data-row"><span class="data-label">매수 체결강도</span><span class="data-value">{float(row['체결강도']):.0f}</span></div>
                      <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{float(row['VWAP위치(%)']):+.1f}%</span></div>
                      <div class="data-row"><span class="data-label">호가 차이</span><span class="data-value">{float(row['스프레드(%)']):.2f}%</span></div>
                      <div class="data-row"><span class="data-label">당일 거래량</span><span class="data-value">{int(row['오늘거래량']):,}주</span></div>
                    </div></div>''',
                    unsafe_allow_html=True,
                )
        st.caption(
            "이 카드는 상승률·당일 거래량·체결강도 세 순위가 겹친 후보입니다. "
            "아직 매수신호가 아니며, 바로 아래 눌림 재돌파 카드가 녹색일 때만 검토합니다."
        )

    if strategy_code in ("penny", "momentum"):
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
            color = "#61df88" if verdict.startswith("🟢") else "#ffd45d" if verdict.startswith("🟡") else "#ff7b81"
            raw_name = str(row.get("종목명") or row.get("종목코드"))
            raw_ticker = str(row.get("종목코드", ""))
            name = html.escape(raw_name)
            ticker = html.escape(raw_ticker)
            horizon = analysis.get("horizon_forecast", {})
            forecast_1 = horizon.get("1분 후", {"label": "⚪ 계산 대기", "score": 0})
            forecast_5 = horizon.get("5분 후", {"label": "⚪ 계산 대기", "score": 0})
            forecast_10 = horizon.get("10분 후", {"label": "⚪ 계산 대기", "score": 0})
            st.markdown(
                f'''<div class="stock-card" style="border-color:{color}">
                <div class="card-head"><div><div class="stock-name">{name}</div>
                <div class="ticker">{ticker} · ${row['현재가($)']:.4f} · {row['등락률(%)']:+.1f}%</div></div>
                <div style="color:{color};font-weight:900">{verdict}<br><small>{analysis['score']}/{analysis.get('max_score', 9)}</small></div></div>
                <div class="grid2" style="margin-top:10px">
                  <div class="data-row"><span class="data-label">매수검토가</span><span class="data-value">${analysis['entry']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">손절가</span><span class="data-value bad">${analysis['stop']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">1차 목표</span><span class="data-value ok">${analysis['target1']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">2차 목표</span><span class="data-value ok">${analysis['target2']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">RSI 1/3/5</span><span class="data-value">{analysis['rsi_1']:.0f}/{analysis['rsi_3']:.0f}/{analysis['rsi_5']:.0f}</span></div>
                  <div class="data-row"><span class="data-label">3분 MACD</span><span class="data-value">{'상승' if analysis['bullish_macd'] else '약화'}</span></div>
                  <div class="data-row"><span class="data-label">1·3·5·15분 추세</span><span class="data-value">{'/'.join('상' if analysis.get(f'trend_{m}') else '하' for m in (1,3,5,15))}</span></div>
                  <div class="data-row"><span class="data-label">1분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_1['label']))} {forecast_1['score']}</span></div>
                  <div class="data-row"><span class="data-label">5분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_5['label']))} {forecast_5['score']}</span></div>
                  <div class="data-row"><span class="data-label">10분 후 보조예상</span><span class="data-value">{html.escape(str(forecast_10['label']))} {forecast_10['score']}</span></div>
                  <div class="data-row"><span class="data-label">VWAP 위치</span><span class="data-value">{analysis['vwap_gap']:+.1f}%</span></div>
                  <div class="data-row"><span class="data-label">최근 거래량</span><span class="data-value">{analysis['volume_speed']:.1f}배</span></div>
                  <div class="data-row"><span class="data-label">진입가 체결률</span><span class="data-value">{analysis['validation_entry_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">전체 매매성공률</span><span class="data-value">{analysis['validation_win_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">1차 익절 성공</span><span class="data-value">{analysis['validation_wins']}/{analysis['validation_samples']}회</span></div>
                  <div class="data-row"><span class="data-label">2차 익절 도달률</span><span class="data-value">{analysis['validation_target2_rate']:.1f}%</span></div>
                  <div class="data-row"><span class="data-label">차트 지지선</span><span class="data-value">${analysis['support']:.4f}</span></div>
                  <div class="data-row"><span class="data-label">차트 저항선</span><span class="data-value">${analysis['resistance']:.4f}</span></div>
                </div></div>''',
                unsafe_allow_html=True,
            )
            if verdict.startswith("🟢") and analysis.get("validation_ok"):
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
                    stop, target1, target2, support, resistance, level_details = calculate_us_multiframe_levels(
                        analysis["frames"],
                        float(actual_entry),
                    )
                    deviation = abs(float(actual_entry) / planned_entry - 1) * 100 if planned_entry > 0 else 0
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
                    }
                    st.session_state.pop("active_us_forecast", None)
                    st.rerun()
        st.caption(
            "🟢는 삼중교집합·VWAP 눌림·1·3·5·15분 추세·EMA9 재돌파·MACD 회복·저점상승·체결강도를 "
            "모두 확인하고, 확보된 최근 과거 신호가 20회 이상이며 재생 성공률이 75% 이상일 때만 표시합니다. "
            "이는 최근 분봉 재생 결과이며 앞으로의 수익을 보장하는 승률은 아닙니다. "
            "1·5·10분 표시는 방금 체결과 여러 분봉의 방향 우세를 요약한 보조판정입니다."
        )

    us_columns = [
        "삼중교집합", "교집합수", "상승률순위", "당일거래량순위", "체결강도순위",
        "종목코드", "종목명", "현재가($)", "등락률(%)", "오늘거래량",
        "전일대비거래량(%)", "오늘거래대금(백만$)", "VWAP($)",
        "VWAP위치(%)", "스프레드(%)", "체결강도", "진입점수",
        "진입검토가($)", "손절기준($)", "1차목표($)", "2차목표($)",
        "시세시간(KST)", "시세출처", "현재판정",
    ]

    st.dataframe(
        table[us_columns],
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
            "진입점수": st.column_config.NumberColumn(format="%d점"),
            "진입검토가($)": st.column_config.NumberColumn(format="$%.4f"),
            "손절기준($)": st.column_config.NumberColumn(format="$%.4f"),
            "1차목표($)": st.column_config.NumberColumn(format="$%.4f"),
            "2차목표($)": st.column_config.NumberColumn(format="$%.4f"),
        },
    )
    if us_meta.get("errors"):
        st.caption("일부 묶음은 재시도 후 제외됐습니다. 표시된 종목은 정상 응답입니다.")
    if strategy_code not in ("penny", "momentum"):
        st.warning("이 표는 후보 압축용이며 매수 신호가 아닙니다. RSI·MACD는 선택 종목 정밀검사에서 확인하세요.")


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
    selected_label = st.selectbox("정밀검사할 종목", list(labels.keys()))

    if st.button("선택 종목 한눈에 검사", type="secondary"):
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

            analysis = analyze_selected_stock(minute, selected_row)
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
    with st.expander(f"전체 {len(table)}종목 표 보기"):
        st.caption("현재가·VWAP·거래량·거래대금은 한국투자증권 통합시장(UN) 값입니다.")
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
                "거래량증가율(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "거래량회전율(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "전일대비거래량(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "오늘거래대금(억원)": st.column_config.NumberColumn(format="%.1f억원"),
            },
        )
