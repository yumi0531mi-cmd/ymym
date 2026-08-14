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

