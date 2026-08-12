# -*- coding: utf-8 -*-
"""
반복단타 스캐너 업그레이드 모듈 (regime_session_upgrade.py)

run_live_validation.py / scalp_app.py 에 아래 3가지를 추가로 꽂아 넣기 위한 모듈입니다.

  1) RegimeConfirmer   : "짧은 눌림"과 "실제 하락전환"을 구분한다.
                         짧은 눌림은 화면에 노출하지 않고(이전 상태 유지),
                         일정 횟수/시간 연속으로 확인된 경우에만 하락전환을 표시한다.
  2) SessionRouter      : 국내=정규장만 / 미국=프리+정규+애프터+주간거래(데이장) 전부.
  3) full_market_universe : candidates() 모드에만 의존하지 않고
                         국내는 pykrx 전체 티커, 미국은 스크리너 전체를 1차 유동성 필터링한다.

주의: scanner.kis_engine 원본 소스가 없는 상태에서 작성했습니다.
      필드명(vwap, chart_*_1m 등)은 실제 kis_engine 출력과 다를 수 있으니
      엔진 복구 후 반드시 필드명을 대조해서 확인하세요.
      (특히 chart_vwap_1m 배열이 실제로 존재하는지부터 확인 - 없으면
       아래 build_vwap_series() 로 직접 계산해서 대체해야 합니다.)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

KST = timezone(timedelta(hours=9), name="KST")
ET = ZoneInfo("America/New_York") if ZoneInfo else None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 0) VWAP 계열 자동 대체 (chart_vwap_1m 필드가 없거나 비어있어도 동작)
# ---------------------------------------------------------------------------

def resolve_vwap_series(item: dict) -> list[float]:
    """item['chart_vwap_1m']이 실제로 유효한 분봉별 VWAP 배열인지 검사하고,
    없거나 부실하면(길이 부족/전부 0 등) OHLCV로 직접 VWAP을 계산해서 대신 돌려준다.

    kis_engine 원본이 손상되어 실제 필드 존재 여부를 못 보는 상태이므로,
    "엔진이 뭘 주든 일단 화면 계산은 항상 정상 동작하게" 만드는 안전장치다.
    엔진 복구 후 chart_vwap_1m이 정상 제공되는 걸 확인하면 이 함수는 자동으로
    엔진 값을 그대로 쓰게 되므로(아래 valid_raw 분기) 별도로 코드를 되돌릴 필요 없다.
    """
    closes = item.get("chart_close_1m", []) or []
    n = len(closes)
    if n == 0:
        return []

    raw = item.get("chart_vwap_1m", []) or []
    nonzero = sum(1 for x in raw if _safe_float(x) > 0)
    valid_raw = len(raw) >= max(1, n - 2) and nonzero >= max(1, int(n * 0.8))
    if valid_raw:
        item["_vwap_series_source"] = "engine_chart_vwap_1m"
        out = [_safe_float(x) for x in raw]
        return out[-n:] if len(out) >= n else [0.0] * (n - len(out)) + out

    # ---- 대체 계산: 세션 내 누적 (고가+저가+종가)/3 * 거래량 방식 VWAP ----
    highs = item.get("chart_high_1m", []) or []
    lows = item.get("chart_low_1m", []) or []
    volumes = item.get("chart_volume_1m", []) or []
    m = min(n, len(highs), len(lows))
    if m == 0:
        item["_vwap_series_source"] = "unavailable"
        return [0.0] * n
    vols = [_safe_float(x) for x in volumes[-m:]] if len(volumes) >= m else \
        [0.0] * (m - len(volumes)) + [_safe_float(x) for x in volumes]

    cum_pv = 0.0
    cum_v = 0.0
    series = []
    for i in range(m):
        typical = (_safe_float(highs[-m:][i]) + _safe_float(lows[-m:][i]) + _safe_float(closes[-m:][i])) / 3.0
        vol = max(0.0, vols[i] if i < len(vols) else 0.0)
        cum_pv += typical * vol
        cum_v += vol
        series.append(cum_pv / cum_v if cum_v > 0 else typical)

    item["_vwap_series_source"] = "computed_fallback_typical_price"
    if m < n:
        series = [series[0] if series else 0.0] * (n - m) + series
    return series


# ---------------------------------------------------------------------------
# 1) 추세 지속성 확인 (짧은 눌림 vs 진짜 하락전환)
# ---------------------------------------------------------------------------

@dataclass
class _TickerHistory:
    states: list = field(default_factory=list)     # [(ts, raw_state)]
    displayed_state: str = "UNKNOWN"
    displayed_label: str = "⚪ 추세 확인 중"
    confirmed_at: float = 0.0
    last_bullish_at: float = 0.0


class RegimeConfirmer:
    """intraday_regime_plan()이 만든 raw_state를 그대로 화면에 쓰지 않고
    지속성 필터를 거쳐서 표시용 상태로 바꿔준다.

    - CONFIRM_COUNT회 연속, CONFIRM_MINUTES 분 이상 하락 신호가 유지되어야
      '하락추세 확정(DOWNTREND_CONFIRMED)'으로 표시를 바꾼다.
    - 그 전까지 하락 신호가 나와도 표시 상태는 이전 상태(예: 상승추세 눌림)를
      그대로 유지한 채, 보조 문구로만 "일시적 조정 관찰 중"을 덧붙인다.
      => 화면에 하락전환처럼 보이는 걸 막아서, 형님이 그거 보고
         손절 안 해도 될 자리에서 던지는 걸 방지.
    - 일단 확정되면 COOLDOWN_MINUTES 동안은 재상승으로 바로 안 바뀌게(채터링 방지)
      최소 유지시간을 둔다. 단, 손절가 이탈처럼 명확한 무효화 신호는 즉시 반영해야
      하므로 이 쿨다운은 '표시 라벨'에만 적용하고 stop_loss 체크는 항상 즉시 통과시킨다.
    """

    CONFIRM_COUNT = 3          # 연속 몇 번 하락 신호가 나와야 확정할지
    CONFIRM_MINUTES = 3.0      # 최소 몇 분 이상 유지되어야 확정할지
    COOLDOWN_MINUTES = 2.0     # 확정 후 최소 유지 시간 (채터링 방지)
    HISTORY_WINDOW = 20        # 종목별 최근 몇 개 스냅샷까지 기억할지

    # intraday_regime_plan()의 raw_state 중 '하락 계열'로 취급할 값들
    DOWN_STATES = {"DOWNTREND", "DOWNTREND_REVERSAL"}
    UP_STATES = {"STRONG_UPTREND", "UPTREND_PULLBACK", "UPTREND_WEAKENING"}

    def __init__(self):
        self._data: dict[str, _TickerHistory] = {}

    def update(self, ticker: str, raw_state: str, raw_label: str, raw_reason: str,
               stop_hit: bool, now_ts: float | None = None) -> dict:
        """매 tick마다 호출. item에 병합할 dict를 돌려준다.

        stop_hit=True (실제 손절가 이탈 등 명확한 무효화)이면 지속성 확인을
        건너뛰고 즉시 하락 확정으로 표시한다. 이건 안전장치용이라 절대 죽이면 안 됨.
        """
        now_ts = now_ts or time.time()
        h = self._data.setdefault(ticker, _TickerHistory())
        h.states.append((now_ts, raw_state))
        h.states = [x for x in h.states if now_ts - x[0] <= self.CONFIRM_MINUTES * 4 * 60]
        h.states = h.states[-self.HISTORY_WINDOW:]

        if stop_hit:
            h.displayed_state = "DOWNTREND_CONFIRMED"
            h.displayed_label = "🔴 하락추세 확정 (손절가 이탈)"
            h.confirmed_at = now_ts
            return self._as_dict(h, raw_state, raw_reason, confirmed=True)

        recent = [s for ts, s in h.states if now_ts - ts <= self.CONFIRM_MINUTES * 60]
        recent_down = [s for s in recent if s in self.DOWN_STATES]
        down_ratio_ok = (
            len(recent) >= self.CONFIRM_COUNT
            and len(recent_down) / max(1, len(recent)) >= 0.8
        )
        span_ok = (
            len(h.states) >= self.CONFIRM_COUNT
            and (now_ts - h.states[-self.CONFIRM_COUNT][0]) >= self.CONFIRM_MINUTES * 60
        )
        confirmed_down = down_ratio_ok and span_ok

        in_cooldown = (now_ts - h.confirmed_at) < self.COOLDOWN_MINUTES * 60

        if raw_state in self.UP_STATES:
            h.last_bullish_at = now_ts

        if confirmed_down:
            h.displayed_state = "DOWNTREND_CONFIRMED"
            h.displayed_label = "🔴 하락추세 확정"
            h.confirmed_at = now_ts
            return self._as_dict(h, raw_state, raw_reason, confirmed=True)

        if h.displayed_state == "DOWNTREND_CONFIRMED" and in_cooldown:
            # 확정된 지 얼마 안 됐으면 바로 상승으로 되돌리지 않는다 (채터링 방지)
            return self._as_dict(h, raw_state, raw_reason, confirmed=True)

        if raw_state in self.DOWN_STATES:
            # 하락 신호는 나왔지만 아직 지속성 확인 전 -> 이전 상태 유지 + 관찰 문구만 추가
            h.displayed_state = h.displayed_state if h.displayed_state != "UNKNOWN" else "WATCH_PULLBACK"
            base_label = h.displayed_label if h.displayed_label else "⚪ 추세 확인 중"
            note = "🟡 일시적 조정 관찰 중 (하락 확정 아님, 곧 회복 가능)"
            return {
                "regime_state_display": h.displayed_state,
                "regime_label_display": f"{base_label} · {note}",
                "regime_confirmed_down": False,
                "regime_pullback_watch": True,
                "regime_raw_state": raw_state,
                "regime_raw_reason": raw_reason,
            }

        # 상승/박스/중립 등 정상 상태는 그대로 표시 상태 갱신
        h.displayed_state = raw_state
        h.displayed_label = raw_label
        return self._as_dict(h, raw_state, raw_reason, confirmed=False)

    @staticmethod
    def _as_dict(h: _TickerHistory, raw_state: str, raw_reason: str, confirmed: bool) -> dict:
        return {
            "regime_state_display": h.displayed_state,
            "regime_label_display": h.displayed_label,
            "regime_confirmed_down": confirmed,
            "regime_pullback_watch": False,
            "regime_raw_state": raw_state,
            "regime_raw_reason": raw_reason,
        }


def apply_regime_confirmation(item: dict, confirmer: RegimeConfirmer, now_ts: float | None = None) -> dict:
    """intraday_regime_plan() 실행 직후에 호출.
    item['intraday_regime_state'] / ['intraday_regime_label'] 을 확정본으로 덮어쓴다.
    """
    ticker = str(item.get("ticker") or "")
    raw_state = str(item.get("intraday_regime_state", "UNKNOWN"))
    raw_label = str(item.get("intraday_regime_label", ""))
    raw_reason = str(item.get("intraday_regime_reason", ""))
    price = float(item.get("price", 0) or 0)
    stop = float(item.get("stop_loss", item.get("repeat_stop", 0)) or 0)
    stop_hit = stop > 0 and price > 0 and price <= stop

    result = confirmer.update(ticker, raw_state, raw_label, raw_reason, stop_hit, now_ts)
    item.update(result)
    # 화면에 노출할 최종 값은 raw가 아니라 display 값을 쓰도록 교체
    item["intraday_regime_state"] = result["regime_state_display"]
    item["intraday_regime_label"] = result["regime_label_display"]
    return item


# ---------------------------------------------------------------------------
# 2) 세션 라우팅: 국내=정규장만 / 미국=프리+정규+애프터+주간거래(데이장) 전부
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    tradable: bool
    session_name: str
    local_time: str


def kr_session(now_utc: datetime | None = None) -> SessionState:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(KST)
    minute = now.hour * 60 + now.minute
    tradable = now.weekday() < 5 and 9 * 60 <= minute < 15 * 60 + 30
    return SessionState(tradable, "국내 정규장" if tradable else "국내 장외", now.strftime("%H:%M:%S KST"))


def _us_dst_active(now_kst: datetime) -> bool:
    """미국 서머타임(둘째주 일요일 3월 ~ 첫째주 일요일 11월) 여부.
    KST 기준 날짜로 대략 판정 (경계일 새벽 시간대는 오차 가능하니 중요 시점엔 실제 캘린더로 재확인)."""
    if ET is None:
        return True
    try:
        now_et = now_kst.astimezone(ET)
        # ZoneInfo가 알아서 DST를 반영하므로 UTC offset으로 판별
        return now_et.dst().total_seconds() != 0
    except Exception:
        return True


def us_session(now_utc: datetime | None = None) -> SessionState:
    """미국주식 세션 4종을 모두 tradable로 본다: 주간거래(데이장) + 프리마켓 + 정규장 + 애프터마켓.

    시간대는 한국투자증권(KIS) 공지 기준(2023-05-18, 주간거래 확대 공지)을 따랐다.
      - 서머타임 적용: 주간거래 10:00~17:00 / 프리마켓 17:00~22:30 / 정규장 22:30~05:00 / 애프터마켓 05:00~09:00 (KST)
      - 서머타임 해제: 위 시간대가 1시간씩 뒤로 밀림 (주간거래 11:00~18:00 / 프리 18:00~23:30 / 정규 23:30~06:00 / 애프터 06:00~10:00)
    이 값은 KIS가 공지 없이 바꿀 수 있으니, 실거래 전에 반드시 KIS 앱/HTS 공지사항으로 재확인하세요.
    """
    now_kst = (now_utc or datetime.now(timezone.utc)).astimezone(KST)
    dst = _us_dst_active(now_kst)

    if dst:
        windows = [
            (10 * 60, 17 * 60, "미국 주간거래(데이장)"),
            (17 * 60, 22 * 60 + 30, "미국 프리마켓"),
        ]
        # 정규장이 자정을 넘기므로(22:30~05:00) 별도 처리
        overnight = (22 * 60 + 30, 24 * 60, "미국 정규장")
        early = (0, 5 * 60, "미국 정규장")
        after = (5 * 60, 9 * 60, "미국 애프터마켓")
    else:
        windows = [
            (11 * 60, 18 * 60, "미국 주간거래(데이장)"),
            (18 * 60, 23 * 60 + 30, "미국 프리마켓"),
        ]
        overnight = (23 * 60 + 30, 24 * 60, "미국 정규장")
        early = (0, 6 * 60, "미국 정규장")
        after = (6 * 60, 10 * 60, "미국 애프터마켓")

    minute = now_kst.hour * 60 + now_kst.minute
    weekday_ok = now_kst.weekday() < 5  # 정규장이 한국시간 새벽까지 이어지는 특성상 요일 경계는 근사치
    label = f"{now_kst.strftime('%H:%M:%S')} KST"

    for start, end, name in windows:
        if weekday_ok and start <= minute < end:
            return SessionState(True, name, label)
    if weekday_ok and overnight[0] <= minute < overnight[1]:
        return SessionState(True, overnight[2], label)
    if early[0] <= minute < early[1]:
        # 자정 넘어 이어지는 정규장은 전날이 평일이었으면 유효
        prev_weekday_ok = (now_kst - timedelta(days=1)).weekday() < 5
        if prev_weekday_ok or weekday_ok:
            return SessionState(True, early[2], label)
    if after[0] <= minute < after[1]:
        prev_weekday_ok = (now_kst - timedelta(days=1)).weekday() < 5
        if prev_weekday_ok or weekday_ok:
            return SessionState(True, after[2], label)
    return SessionState(False, "미국 장외시간(휴장)", label)


def session_for(market_code: str, now_utc: datetime | None = None) -> SessionState:
    """market_code: 'KR' or 'US'"""
    return kr_session(now_utc) if market_code == "KR" else us_session(now_utc)


# ---------------------------------------------------------------------------
# 3) 전종목 스캔 (candidates() 서브셋이 아니라 시장 전체 1차 필터)
# ---------------------------------------------------------------------------

def kr_full_universe(min_price: float = 1000, max_price: float = 500000,
                      min_trading_value: float = 3_000_000_000, min_volume: int = 50_000) -> list[dict]:
    """pykrx로 KOSPI+KOSDAQ 전 종목의 당일(또는 최근 영업일) OHLCV를 받아
    유동성 조건으로 1차 필터링한 결과를 돌려준다.
    engine.candidates() 서브셋이 아니라 진짜 '시장 전체'가 모집단이 된다.
    """
    from pykrx import stock as krx_stock

    today = datetime.now(KST).strftime("%Y%m%d")
    rows = []
    for market_name in ("KOSPI", "KOSDAQ"):
        try:
            df = krx_stock.get_market_ohlcv_by_ticker(today, market=market_name)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for code, r in df.iterrows():
            try:
                price = float(r.get("종가", 0) or 0)
                volume = int(r.get("거래량", 0) or 0)
                value = float(r.get("거래대금", price * volume) or price * volume)
                change = float(r.get("등락률", 0) or 0)
            except Exception:
                continue
            if not (min_price <= price <= max_price):
                continue
            if volume < min_volume or value < min_trading_value:
                continue
            if change >= 18:   # 상한가 근접 등 초기 필터 (원 코드와 동일 기준)
                continue
            try:
                name = krx_stock.get_market_ticker_name(code)
            except Exception:
                name = code
            rows.append({
                "ticker": str(code), "name": str(name), "exchange": "KR",
                "asset_type": "전종목스캔", "screen_price": price,
                "screen_change": change, "screen_volume": volume, "screen_value": value,
            })
    rows.sort(key=lambda x: (x["screen_volume"], x["screen_value"]), reverse=True)
    return rows


def us_full_universe_from_screener(fetch_screener_rows) -> list[dict]:
    """미국 전체 종목 스크리너는 보통 이미 기존 kis_engine 쪽에 nasdaq.com 헤더 처리한
    함수가 있을 겁니다(메모리 기준 이전에 이슈 해결한 이력 있음). 그 함수를 인자로
    주입받아서 1차 필터만 이 모듈에서 공통으로 처리하는 방식입니다.

    fetch_screener_rows: () -> list[dict]  (ticker/name/exchange/price/volume/change 를 담은 raw row 리스트를 반환하는 콜백)
    """
    try:
        raw_rows = fetch_screener_rows() or []
    except Exception:
        raw_rows = []
    rows = []
    for r in raw_rows:
        try:
            price = float(r.get("price", r.get("lastsale", 0)) or 0)
            volume = int(float(r.get("volume", 0) or 0))
            change = float(r.get("change_percent", r.get("pctchange", 0)) or 0)
        except Exception:
            continue
        value = price * volume
        if not (0.5 <= price <= 500):
            continue
        if volume < 50_000 or value < 3_000_000:
            continue
        if change >= 40:
            continue
        ticker = str(r.get("ticker", r.get("symbol", ""))).upper()
        if not ticker:
            continue
        rows.append({
            "ticker": ticker, "name": str(r.get("name", ticker)), "exchange": str(r.get("exchange", "NASDAQ")),
            "asset_type": "전종목스캔", "screen_price": price, "screen_change": change,
            "screen_volume": volume, "screen_value": value,
        })
    rows.sort(key=lambda x: (x["screen_volume"], x["screen_value"]), reverse=True)
    return rows


def merge_and_rank_universe(engine_candidates: list[dict], full_scan_rows: list[dict], limit: int = 60) -> list[dict]:
    """엔진이 자체적으로 뽑아준 candidates()결과(모멘텀/돌파 특화)와
    전종목 1차 필터 결과를 합쳐서 중복 제거 후 유동성 기준 상위 N개만 넘긴다.
    이후 정밀분석(precise_analysis)은 기존처럼 6개씩 순환 처리하면 됨.
    """
    seen: dict[str, dict] = {}
    for r in list(engine_candidates) + list(full_scan_rows):
        t = str(r.get("ticker", "")).upper()
        if not t:
            continue
        old = seen.get(t)
        if old is None or float(r.get("screen_value", 0) or 0) > float(old.get("screen_value", 0) or 0):
            seen[t] = r
    merged = list(seen.values())
    merged.sort(key=lambda x: (int(x.get("screen_volume", 0) or 0), float(x.get("screen_value", 0) or 0)), reverse=True)
    return merged[:limit]
