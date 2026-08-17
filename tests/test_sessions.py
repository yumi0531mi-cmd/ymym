from datetime import datetime

from scanner.models import Market
from scanner.sessions import KST, market_session, remaining_session_minutes


def test_us_sessions_include_kis_day_pre_regular_and_after_in_summer_time():
    # 2026-08-17 is a Monday and New York daylight saving time is active.
    assert market_session(Market.US, datetime(2026, 8, 17, 10, 0, tzinfo=KST)) == "US_DAY"
    assert market_session(Market.US, datetime(2026, 8, 17, 17, 0, tzinfo=KST)) == "US_PRE"
    assert market_session(Market.US, datetime(2026, 8, 17, 22, 30, tzinfo=KST)) == "US_REGULAR"
    assert market_session(Market.US, datetime(2026, 8, 18, 5, 30, tzinfo=KST)) == "US_AFTER"


def test_us_day_session_remaining_minutes_uses_korean_time_boundary():
    observed = datetime(2026, 8, 17, 16, 30, tzinfo=KST)
    assert remaining_session_minutes(Market.US, observed) == 30
