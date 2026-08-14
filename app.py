# -*- coding: utf-8 -*-
"""KIS 15-strategy intraday scanner - Streamlit app.

Version 0.1.0
- KIS REST token with cache
- Domestic: volume-rank endpoint, volume + trading-amount ranking modes
- US: NAS/NYS/AMS volume + trading-amount rankings
- Two-stage scan: broad discovery -> limited intraday-detail analysis
- 15 independent strategy engines
- 5/10/15/30 minute signed forecast ranges

IMPORTANT: strategy score is an uncalibrated rule score, NOT a win rate.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

try:
    import streamlit as st
except ImportError:  # Allows syntax/import tests outside Streamlit environment.
    st = None

from strategy_engine import Candle, ENGINE_VERSION, analyze

APP_VERSION = "0.1.0"
KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_FILE = Path(".kis_token_cache.json")
VALIDATION_LOG = Path("validation_signals.jsonl")


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _secret(*names: str, default: str = "") -> str:
    # Supports several common existing Streamlit secret names without forcing renaming.
    if st is not None:
        for name in names:
            try:
                v = st.secrets.get(name)
                if v:
                    return str(v)
            except Exception:
                pass
    for name in names:
        v = os.getenv(name)
        if v:
            return v
    return default


class KISError(RuntimeError):
    pass


class KISClient:
    def __init__(self, app_key: str, app_secret: str, timeout: float = 6.0):
        if not app_key or not app_secret:
            raise KISError("Streamlit Secrets에서 KIS APP KEY / APP SECRET을 찾지 못했습니다.")
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.session = requests.Session()
        self._token: str | None = None
        self._token_expiry = 0.0

    def _load_cached_token(self) -> bool:
        try:
            d = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if d.get("app_key_tail") == self.app_key[-6:] and float(d.get("expiry", 0)) > time.time()+120:
                self._token = str(d["token"])
                self._token_expiry = float(d["expiry"])
                return True
        except Exception:
            return False
        return False

    def token(self) -> str:
        if self._token and self._token_expiry > time.time()+120:
            return self._token
        if self._load_cached_token():
            return str(self._token)
        url = BASE_URL + "/oauth2/tokenP"
        r = self.session.post(url, json={"grant_type":"client_credentials","appkey":self.app_key,"appsecret":self.app_secret}, timeout=self.timeout)
        if r.status_code != 200:
            raise KISError(f"KIS 토큰 발급 실패 HTTP {r.status_code}: {r.text[:180]}")
        d = r.json()
        token = d.get("access_token")
        if not token:
            raise KISError(f"KIS 토큰 응답 오류: {d.get('msg1') or d}")
        expires = max(600, int(d.get("expires_in", 86400)))
        self._token, self._token_expiry = str(token), time.time()+expires
        try:
            TOKEN_FILE.write_text(json.dumps({"token":token,"expiry":self._token_expiry,"app_key_tail":self.app_key[-6:]}), encoding="utf-8")
        except Exception:
            pass
        return str(token)

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict:
        headers = {
            "content-type":"application/json; charset=utf-8",
            "authorization":"Bearer " + self.token(),
            "appkey":self.app_key,
            "appsecret":self.app_secret,
            "tr_id":tr_id,
            "custtype":"P",
        }
        r = self.session.get(BASE_URL+path, headers=headers, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise KISError(f"{path} HTTP {r.status_code}: {r.text[:180]}")
        d = r.json()
        if str(d.get("rt_cd", "0")) not in ("0", ""):
            raise KISError(str(d.get("msg1") or d.get("msg_cd") or "KIS API 오류"))
        return d

    def domestic_rank(self, market_code: str, amount_mode: bool) -> list[dict]:
        # KIS volume-rank API: FID_BLNG_CLS_CODE=3 means trading-amount order.
        p = {
            "FID_COND_MRKT_DIV_CODE":"J", "FID_COND_SCR_DIV_CODE":"20171",
            "FID_INPUT_ISCD":market_code, "FID_DIV_CLS_CODE":"1",
            "FID_BLNG_CLS_CODE":"3" if amount_mode else "0",
            "FID_TRGT_CLS_CODE":"111111111", "FID_TRGT_EXLS_CLS_CODE":"111111",
            "FID_INPUT_PRICE_1":"", "FID_INPUT_PRICE_2":"", "FID_VOL_CNT":"", "FID_INPUT_DATE_1":"",
        }
        d = self.get("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", p)
        out = []
        for x in d.get("output", []) or []:
            out.append({
                "symbol":str(x.get("mksc_shrn_iscd", "")), "name":str(x.get("hts_kor_isnm", "")), "exchange":"KRX",
                "price":_num(x.get("stck_prpr")), "change_pct":_num(x.get("prdy_ctrt")), "volume":_num(x.get("acml_vol")),
                "amount":_num(x.get("acml_tr_pbmn")), "rank":int(_num(x.get("data_rank"), 9999)),
                "avg_volume":_num(x.get("avrg_vol")), "source":"amount" if amount_mode else "volume",
            })
        return out

    def overseas_rank(self, exchange: str, amount_mode: bool) -> list[dict]:
        path = "/uapi/overseas-stock/v1/ranking/trade-pbmn" if amount_mode else "/uapi/overseas-stock/v1/ranking/trade-vol"
        tr = "HHDFS76320010" if amount_mode else "HHDFS76310010"
        p = {"KEYB":"", "AUTH":"", "EXCD":exchange, "NDAY":"0", "VOL_RANG":"0", "PRC1":"", "PRC2":""}
        d = self.get(path, tr, p)
        out = []
        for x in d.get("output2", []) or []:
            out.append({
                "symbol":str(x.get("symb", "")), "name":str(x.get("name") or x.get("ename") or x.get("symb") or ""),
                "exchange":str(x.get("excd", exchange)), "price":_num(x.get("last")), "change_pct":_num(x.get("rate")),
                "volume":_num(x.get("tvol")), "amount":_num(x.get("tamt")), "rank":int(_num(x.get("rank"), 9999)),
                "avg_volume":_num(x.get("a_tvol")), "ask":_num(x.get("pask")), "bid":_num(x.get("pbid")),
                "source":"amount" if amount_mode else "volume",
            })
        return out

    def domestic_minute_bars(self, symbol: str, count: int = 60) -> list[Candle]:
        # Official KIS domestic minute chart endpoint / TR commonly documented as FHKST03010200.
        now = datetime.now(KST).strftime("%H%M%S")
        p = {"FID_ETC_CLS_CODE":"", "FID_COND_MRKT_DIV_CODE":"J", "FID_INPUT_ISCD":symbol,
             "FID_INPUT_HOUR_1":now, "FID_PW_DATA_INCU_YN":"Y"}
        d = self.get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200", p)
        rows = d.get("output2", []) or []
        bars = []
        for x in reversed(rows[:count]):
            bars.append(Candle(str(x.get("stck_cntg_hour", "")), _num(x.get("stck_oprc")), _num(x.get("stck_hgpr")),
                               _num(x.get("stck_lwpr")), _num(x.get("stck_prpr")), _num(x.get("cntg_vol") or x.get("acml_vol"))))
        return [b for b in bars if b.close > 0]

    def overseas_minute_bars(self, exchange: str, symbol: str, count: int = 60) -> list[Candle]:
        # KIS overseas minute endpoint; response field names are parsed defensively for current API variants.
        p = {"AUTH":"", "EXCD":exchange, "SYMB":symbol, "NMIN":"1", "PINC":"1", "NEXT":"", "NREC":str(min(count,120)), "FILL":"", "KEYB":""}
        d = self.get("/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice", "HHDFS76950200", p)
        rows = d.get("output2", []) or d.get("output1", []) or d.get("output", []) or []
        bars = []
        for x in reversed(rows[:count]):
            close = _num(x.get("last") or x.get("clos") or x.get("ovrs_nmix_prpr"))
            opn = _num(x.get("open") or x.get("oprc") or close, close)
            high = _num(x.get("high") or x.get("hgpr") or close, close)
            low = _num(x.get("low") or x.get("lwpr") or close, close)
            vol = _num(x.get("tvol") or x.get("evol") or x.get("vol") or x.get("acml_vol"))
            bars.append(Candle(str(x.get("xymd") or x.get("khms") or x.get("time") or ""), opn, high, low, close, vol))
        return [b for b in bars if b.close > 0]


def merge_candidates(rows: list[dict], max_candidates: int = 200) -> list[dict]:
    merged: dict[tuple[str,str], dict] = {}
    for r in rows:
        key = (r.get("exchange", ""), r.get("symbol", ""))
        if not key[1]:
            continue
        cur = merged.setdefault(key, dict(r, sources=set()))
        cur["sources"].add(r.get("source", ""))
        # Preserve the richest values across ranking sources.
        for k in ("volume","amount","avg_volume","ask","bid","price"):
            if _num(r.get(k)) > _num(cur.get(k)):
                cur[k] = r.get(k)
        cur["rank"] = min(int(cur.get("rank",9999)), int(r.get("rank",9999)))
    out = []
    for r in merged.values():
        r["sources"] = "+".join(sorted(x for x in r["sources"] if x))
        price, bid, ask = _num(r.get("price")), _num(r.get("bid")), _num(r.get("ask"))
        r["spread_pct"] = ((ask-bid)/price*100) if price>0 and ask>0 and bid>0 and ask>=bid else 0.0
        avg = _num(r.get("avg_volume")); vol = _num(r.get("volume"))
        r["rvol_hint"] = vol/avg if avg>0 else 1.0
        # Discovery score only: liquidity/current activity. It is NOT a strategy score or win probability.
        r["discovery_score"] = min(100.0, 28 + min(30, max(0, r["rvol_hint"]-1)*12) + min(25, abs(_num(r.get("change_pct")))*3) + (10 if "amount" in r["sources"] else 0) + (7 if "volume" in r["sources"] else 0))
        out.append(r)
    return sorted(out, key=lambda x:(x["discovery_score"], _num(x.get("amount")), _num(x.get("volume"))), reverse=True)[:max_candidates]


def discover(client: KISClient, market: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    if market == "KR":
        for code in ("0001", "1001"):  # KOSPI / KOSDAQ
            for amount in (False, True):
                try: rows.extend(client.domestic_rank(code, amount))
                except Exception as e: errors.append(f"KR {code} {'대금' if amount else '거래량'}: {e}")
    else:
        for excd in ("NAS", "NYS", "AMS"):
            for amount in (False, True):
                try: rows.extend(client.overseas_rank(excd, amount))
                except Exception as e: errors.append(f"US {excd} {'대금' if amount else '거래량'}: {e}")
    return merge_candidates(rows, 200), errors


def shortlist(candidates: list[dict], n: int = 18) -> list[dict]:
    # Do not drop strategy calculations; only limit expensive minute-bar API calls to the best discovery pool.
    liquid = [x for x in candidates if _num(x.get("price"))>0 and (_num(x.get("spread_pct")) <= 0.8 or _num(x.get("spread_pct")) == 0)]
    return liquid[:n]


def quote_context(c: dict) -> dict:
    price, bid, ask = _num(c.get("price")), _num(c.get("bid")), _num(c.get("ask"))
    spread = ((ask-bid)/price*100) if price>0 and ask>0 and bid>0 and ask>=bid else _num(c.get("spread_pct"))
    return {"spread_pct":spread, "relative_strength":max(-2.0,min(2.0,_num(c.get("change_pct"))/2.0)), "event_score":0.0}


def save_signal(result: dict) -> None:
    payload = {"logged_at":datetime.now(timezone.utc).isoformat(), **result}
    try:
        with VALIDATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run_scan(client: KISClient, market: str, session_name: str, detail_count: int = 18) -> tuple[list[dict], list[dict], list[str]]:
    candidates, errors = discover(client, market)
    results: list[dict] = []
    for c in shortlist(candidates, detail_count):
        try:
            if market == "KR":
                bars = client.domestic_minute_bars(c["symbol"], 60)
            else:
                bars = client.overseas_minute_bars(c["exchange"], c["symbol"], 60)
            if len(bars) < 8:
                errors.append(f"{c['symbol']}: 분봉 부족({len(bars)})")
                continue
            result = analyze(c["symbol"], c["name"], market, session_name, bars, quote_context(c), "OK")
            d = result.to_dict()
            results.append(d)
            save_signal(d)
        except Exception as e:
            errors.append(f"{c.get('symbol')}: {e}")
        time.sleep(0.06)
    results.sort(key=lambda x:(x["decision"]=="🟢 진입", x["uncalibrated_score"]), reverse=True)
    return candidates, results, errors


def _fmt_price(v: float, market: str) -> str:
    return f"{v:,.0f}" if market == "KR" else f"${v:,.2f}"


def render():
    if st is None:
        raise RuntimeError("Streamlit이 설치되어 있지 않습니다. Streamlit Community Cloud에서 실행하세요.")
    st.set_page_config(page_title="KIS 15전략 단타 스캐너", layout="wide")
    st.title("KIS 15전략 단타 스캐너")
    st.caption(f"APP {APP_VERSION} · ENGINE {ENGINE_VERSION} · 점수는 보정 전 규칙점수이며 승률이 아닙니다.")
    key = _secret("KIS_APP_KEY","APP_KEY","appkey","KIS_APPKEY")
    secret = _secret("KIS_APP_SECRET","APP_SECRET","appsecret","KIS_APPSECRET")
    if not key or not secret:
        st.error("Streamlit Secrets에서 KIS APP KEY / APP SECRET을 찾지 못했습니다. 기존 키 이름도 여러 형태로 자동 탐색합니다.")
        st.stop()
    client = KISClient(key, secret)
    tab_kr, tab_day, tab_pre, tab_regular, tab_after = st.tabs(["🇰🇷 국내", "🇺🇸 데이", "🇺🇸 프리", "🇺🇸 정규", "🇺🇸 애프터"])
    def one_tab(container, market: str, sess: str):
        with container:
            st.caption("후보는 거래량·거래대금 순위를 합쳐 넓게 찾고, 정밀 분봉 분석은 API 부담을 줄이기 위해 상위 유동성 후보에 집중합니다.")
            detail = st.slider("정밀 분석 종목 수", 8, 24, 18, key=f"n_{market}_{sess}")
            if st.button("지금 스캔", type="primary", key=f"scan_{market}_{sess}"):
                with st.spinner("KIS 시세 분석 중..."):
                    cand, res, errs = run_scan(client, market, sess, detail)
                st.metric("발견 후보", len(cand)); st.metric("정밀 분석 성공", len(res))
                buys = [r for r in res if r["decision"]=="🟢 진입"]
                st.subheader(f"🟢 지금 진입 후보 {len(buys)}개")
                for r in res[:10]:
                    f10 = next(x for x in r["forecasts"] if x["minutes"]==10)
                    f15 = next(x for x in r["forecasts"] if x["minutes"]==15)
                    f30 = next(x for x in r["forecasts"] if x["minutes"]==30)
                    with st.container(border=True):
                        st.markdown(f"**{r['decision']} · {r['name']} ({r['symbol']})** — {r['primary_strategy']}")
                        st.write(f"현재 {_fmt_price(r['current'], market)} | 진입 {_fmt_price(r['entry_low'], market)}~{_fmt_price(r['entry_high'], market)} | 1차 {_fmt_price(r['target1'], market)} | 2차 {_fmt_price(r['target2'], market)} | Hard Stop {_fmt_price(r['hard_stop'], market)}")
                        st.write(f"10분 {f10['center_pct']:+.2f}% ({f10['low_pct']:+.2f}~{f10['high_pct']:+.2f}%) · 15분 {f15['center_pct']:+.2f}% ({f15['low_pct']:+.2f}~{f15['high_pct']:+.2f}%) · 30분 {f30['center_pct']:+.2f}% ({f30['low_pct']:+.2f}~{f30['high_pct']:+.2f}%)")
                        st.caption(f"보정 전 점수 {r['uncalibrated_score']:.1f} · Regime {r['regime']} · 보조: {', '.join(r['supporting_strategies']) or '-'}")
                if errs:
                    with st.expander(f"데이터/API 경고 {len(errs)}건"):
                        st.write("\n".join(errs[:30]))
    one_tab(tab_kr, "KR", "KR_REGULAR")
    one_tab(tab_day, "US", "US_DAY")
    one_tab(tab_pre, "US", "US_PRE")
    one_tab(tab_regular, "US", "US_REGULAR")
    one_tab(tab_after, "US", "US_AFTER")


if __name__ == "__main__":
    render()
