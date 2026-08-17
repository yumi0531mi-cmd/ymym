from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from .models import Market

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def market_session(market: Market, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=KST)
    if market == Market.KR:
        local = now.astimezone(KST)
        if local.weekday() < 5 and time(9, 0) <= local.time() <= time(15, 30):
            return "KR_REGULAR"
        return "KR_CLOSED"
    # 한국투자증권의 미국 주간거래는 한국시간 기준으로 운영된다.
    # 미국 서머타임 동안에는 10:00~17:00, 그 외에는 10:00~18:00이다.
    kr_local = now.astimezone(KST)
    day_end = time(17, 0) if bool(now.astimezone(ET).dst()) else time(18, 0)
    if kr_local.weekday() < 5 and time(10, 0) <= kr_local.time() < day_end:
        return "US_DAY"
    local = now.astimezone(ET)
    if local.weekday() >= 5:
        return "US_CLOSED"
    t = local.time()
    if time(4, 0) <= t < time(9, 30):
        return "US_PRE"
    if time(9, 30) <= t <= time(16, 0):
        return "US_REGULAR"
    if time(16, 0) < t <= time(20, 0):
        return "US_AFTER"
    return "US_DAY_OR_CLOSED"


def session_end(market: Market, now: datetime | None = None) -> datetime | None:
    """Return the end of the currently tradable configured session in the market timezone."""
    now = now or datetime.now(tz=KST)
    if market == Market.KR:
        local = now.astimezone(KST)
        if market_session(market, local) != "KR_REGULAR":
            return None
        return datetime.combine(local.date(), time(15, 30), tzinfo=KST)
    if market_session(market, now) == "US_DAY":
        kr_local = now.astimezone(KST)
        day_end = time(17, 0) if bool(now.astimezone(ET).dst()) else time(18, 0)
        return datetime.combine(kr_local.date(), day_end, tzinfo=KST)
    local = now.astimezone(ET)
    current = market_session(market, local)
    ends = {
        "US_PRE": time(9, 30),
        "US_REGULAR": time(16, 0),
        "US_AFTER": time(20, 0),
    }
    end = ends.get(current)
    return datetime.combine(local.date(), end, tzinfo=ET) if end else None


def remaining_session_minutes(market: Market, now: datetime | None = None) -> int:
    now = now or datetime.now(tz=KST)
    end = session_end(market, now)
    if end is None:
        return 0
    local_now = now.astimezone(end.tzinfo)
    return max(0, int((end - local_now).total_seconds() // 60))


def trading_date(market: Market, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=KST)
    local = now.astimezone(KST if market == Market.KR else ET)
    return local.strftime("%Y-%m-%d")
