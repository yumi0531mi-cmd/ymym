from scanner.models import Market
from scanner.realtime import KISRealtimeHub, KR_TRADE_TR_ID, US_TRADE_TR_ID


class ApprovalOnlyClient:
    def websocket_approval_key(self):
        return "approval-test-key"


def test_kis_realtime_parses_domestic_trade_tick():
    hub = KISRealtimeHub(ApprovalOnlyClient())
    values = ["005930", "101500", "70100", "2", "100", "1.43", "0", "0", "0", "0", "70200", "70000", "10", "12345"]

    hub._consume(f"0|{KR_TRADE_TR_ID}|1|{'^'.join(values)}")

    tick = hub.tick(Market.KR, "005930")
    assert tick is not None
    assert tick.price == 70100
    assert tick.change_pct == 1.43
    assert tick.bid == 70000
    assert tick.ask == 70200
    assert tick.volume == 12345


def test_kis_realtime_parses_us_trade_tick_and_normalizes_symbol():
    hub = KISRealtimeHub(ApprovalOnlyClient())
    values = [
        "DNASAAPL", "N", "20260817", "20260817", "101500", "191500", "0", "0", "0", "0",
        "165.25", "2", "1.15", "0.70", "165.24", "165.26", "0", "0", "0", "123456",
    ]

    hub._consume(f"0|{US_TRADE_TR_ID}|1|{'^'.join(values)}")

    tick = hub.tick(Market.US, "AAPL")
    assert tick is not None
    assert tick.price == 165.25
    assert tick.change_pct == 0.70
    assert tick.bid == 165.24
    assert tick.ask == 165.26
    assert tick.volume == 123456


def test_kis_realtime_subscription_uses_official_market_keys():
    request = KISRealtimeHub._request("approval-key", KR_TRADE_TR_ID, "005930")

    assert '"approval_key": "approval-key"' in request
    assert '"tr_id": "H0STCNT0"' in request
    assert KISRealtimeHub._us_tr_key("NAS", "NVDA") == "DNASNVDA"
    assert KISRealtimeHub._us_tr_key("NYS", "JPM") == "DNYSJPM"
    assert KISRealtimeHub._us_tr_key("AMS", "SPY") == "DAMSSPY"
