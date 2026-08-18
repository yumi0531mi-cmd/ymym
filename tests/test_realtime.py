from scanner.models import Market
from scanner.realtime import (
    KISRealtimeHub,
    KR_TRADE_TR_ID,
    US_TRADE_TR_ID,
    RealtimeTick,
    process_realtime_hub,
    reset_process_realtime_hubs,
)


class ApprovalOnlyClient:
    def websocket_approval_key(self):
        return "approval-test-key"


def test_kis_realtime_parses_domestic_trade_tick():
    hub = KISRealtimeHub(ApprovalOnlyClient())
    assert hub.connected is False
    values = ["005930", "101500", "70100", "2", "100", "1.43", "0", "0", "0", "0", "70200", "70000", "10", "12345"]

    hub._consume(f"0|{KR_TRADE_TR_ID}|1|{'^'.join(values)}")

    tick = hub.tick(Market.KR, "005930")
    assert tick is not None
    assert tick.price == 70100
    assert tick.change_pct == 1.43
    assert tick.bid == 70000
    assert tick.ask == 70200
    assert tick.volume == 12345
    assert hub.connected is True
    assert hub.status_label() == "KIS 실시간 체결 연결됨 · 1초 화면 갱신"


def test_kis_realtime_parses_us_trade_tick_and_normalizes_symbol():
    hub = KISRealtimeHub(ApprovalOnlyClient())
    values = [
        "DNASAAPL", "AAPL", "2", "20260817", "101500", "20260817", "231500", "164.00", "166.00", "163.50",
        "165.25", "2", "1.15", "0.70", "123456", "20392140", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
    ]

    hub._consume(f"0|{US_TRADE_TR_ID}|1|{'^'.join(values)}")

    tick = hub.tick(Market.US, "AAPL")
    assert tick is not None
    assert tick.price == 165.25
    assert tick.change_pct == 0.70
    assert tick.bid is None
    assert tick.ask is None
    assert tick.volume == 123456


def test_kis_realtime_parses_every_trade_in_batched_frame():
    hub = KISRealtimeHub(ApprovalOnlyClient())
    first = ["005930", "101500", "70100", "2", "100", "1.43", "0", "0", "0", "0", "70200", "70000", "10", "12345"] + [""] * 32
    second = ["000660", "101501", "201000", "2", "200", "1.00", "0", "0", "0", "0", "201500", "200500", "5", "23456"] + [""] * 32
    hub._consume(f"0|{KR_TRADE_TR_ID}|2|{'^'.join(first + second)}")
    assert hub.tick(Market.KR, "005930").price == 70100
    assert hub.tick(Market.KR, "000660").price == 201000


def test_kis_realtime_subscription_uses_official_market_keys():
    request = KISRealtimeHub._request("approval-key", KR_TRADE_TR_ID, "005930")

    assert '"approval_key": "approval-key"' in request
    assert '"tr_id": "H0STCNT0"' in request
    assert KISRealtimeHub._us_tr_key("NAS", "NVDA") == "DNASNVDA"
    assert KISRealtimeHub._us_tr_key("NYS", "JPM") == "DNYSJPM"
    assert KISRealtimeHub._us_tr_key("AMS", "SPY") == "DAMSSPY"


def test_kis_realtime_excludes_forming_minute_from_completed_bars():
    from datetime import datetime, timedelta
    from scanner.realtime import RealtimeTick
    from scanner.sessions import KST

    hub = KISRealtimeHub(ApprovalOnlyClient())
    first = datetime(2026, 8, 17, 10, 0, 5, tzinfo=KST)
    hub._accumulate_bar(RealtimeTick("005930", Market.KR, 70000, 0.0, first, volume=100))
    hub._accumulate_bar(RealtimeTick("005930", Market.KR, 70100, 0.0, first + timedelta(seconds=35), volume=130))
    assert hub.completed_bar_rows(Market.KR, "005930") == []

    hub._accumulate_bar(RealtimeTick("005930", Market.KR, 70200, 0.0, first + timedelta(minutes=1), volume=150))
    rows = hub.completed_bar_rows(Market.KR, "005930")
    assert len(rows) == 1
    assert rows[0]["open"] == 70000
    assert rows[0]["high"] == 70100
    assert rows[0]["low"] == 70000
    assert rows[0]["close"] == 70100
    assert rows[0]["volume"] == 30


def test_kis_realtime_answers_application_pingpong():
    import asyncio
    import json

    class Socket:
        def __init__(self):
            self.payloads = []

        async def pong(self, payload):
            self.payloads.append(payload)

    hub = KISRealtimeHub(ApprovalOnlyClient())
    socket = Socket()
    raw = json.dumps({"header": {"tr_id": "PINGPONG"}, "body": {}})

    handled = asyncio.run(hub._handle_control_message(socket, raw))

    assert handled is True
    assert socket.payloads == [raw]


def test_kis_realtime_resets_approval_on_subscription_auth_error():
    import asyncio
    import json
    import pytest
    from scanner.kis_client import KISError

    class Socket:
        async def pong(self, payload):
            raise AssertionError("pong should not be called")

    hub = KISRealtimeHub(ApprovalOnlyClient())
    hub._approval_key = "stale-key"
    hub._approval_expires_monotonic = 999999.0
    raw = json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {"rt_cd": "1", "msg1": "approval key invalid"}})

    with pytest.raises(KISError):
        asyncio.run(hub._handle_control_message(Socket(), raw))

    assert hub._approval_key == ""
    assert hub._approval_expires_monotonic == 0.0


def test_kis_realtime_waits_longer_when_same_appkey_is_already_in_use():
    error = Exception("KIS 실시간 구독 실패: ALREADY IN USE appkey")
    assert KISRealtimeHub.reconnect_delay_after_error(1.0, error) == 30.0
    assert KISRealtimeHub.reconnect_delay_after_error(1.0, Exception("KIS 실시간 접속키 발급 실패(HTTP 403)")) == 120.0
    assert KISRealtimeHub.reconnect_delay_after_error(2.0, Exception("network timeout")) == 2.0


def test_process_realtime_hub_keeps_one_hub_for_one_secret_set():
    reset_process_realtime_hubs()
    first = process_realtime_hub(ApprovalOnlyClient(), "same-secret")
    second = process_realtime_hub(ApprovalOnlyClient(), "same-secret")
    replacement = process_realtime_hub(ApprovalOnlyClient(), "new-secret")

    assert first is second
    assert replacement is not first
    assert first._stop.is_set() is True
    reset_process_realtime_hubs()
