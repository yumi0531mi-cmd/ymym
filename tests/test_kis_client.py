from __future__ import annotations

from datetime import datetime

from scanner.kis_client import KISClient, KISError, secrets_fingerprint
from scanner.models import Market


def client_with(responses):
    client = KISClient({"KIS_APP_KEY": "test", "KIS_APP_SECRET": "test"}, cache_dir=".test_cache")
    queue = list(responses)
    client._get = lambda *args, **kwargs: queue.pop(0)  # type: ignore[method-assign]
    return client


def test_us_quote_accepts_list_orderbook_output():
    client = client_with([
        {"output": {"last": "133.32", "base": "132.00", "tvol": "12345", "tamt": "1640000"}},
        {"output1": [{"pbid1": "133.31", "pask1": "133.33"}]},
    ])
    quote = client.quote("SOXL", Market.US, "NAS")
    assert quote.price == 133.32
    assert quote.bid == 133.31 and quote.ask == 133.33


def test_kr_quote_and_orderbook_are_separate_responses():
    client = client_with([
        {"output": {"stck_prpr": "230000", "stck_sdpr": "228000", "acml_vol": "1000"}},
        {"output1": {"bidp1": "229500", "askp1": "230000"}},
    ])
    quote = client.quote("005930", Market.KR)
    assert quote.price == 230000
    assert quote.bid == 229500 and quote.ask == 230000


def test_us_intraday_uses_exchange_date_not_today():
    client = client_with([{"output2": [
        {"xymd": "20260814", "xhms": "093000", "open": "10", "high": "11", "low": "9", "last": "10.5", "evol": "100"},
        {"xymd": "20260814", "xhms": "093100", "open": "10.5", "high": "11", "low": "10", "last": "10.8", "evol": "120"},
    ]}])
    bars = client.intraday("TEST", Market.US, "NAS")
    assert len(bars) == 2
    assert bars.index[0].year == 2026 and bars.index[0].month == 8 and bars.index[0].day == 14
    assert str(bars.index.tz) in ("Asia/Seoul", "UTC+09:00")


def test_missing_live_price_is_an_error_not_a_fake_plan():
    client = client_with([{"output": {"last": "0"}}, {"output1": []}])
    try:
        client.quote("BAD", Market.US, "NAS")
    except KISError:
        return
    raise AssertionError("missing current price must raise KISError")


def test_manual_streamlit_token_never_issues_a_new_token(tmp_path):
    client = KISClient({"KIS_ACCESS_TOKEN": "daily-token"}, cache_dir=tmp_path)
    assert client.token_mode == "수동 토큰"
    assert client._token() == "daily-token"


def test_nested_streamlit_secret_section_is_supported(tmp_path):
    client = KISClient({"kis": {"KIS_ACCESS_TOKEN": "nested-daily-token"}}, cache_dir=tmp_path)
    assert client.token_mode == "수동 토큰"
    assert client._token() == "nested-daily-token"


def test_secrets_fingerprint_changes_without_exposing_secret_value():
    before = secrets_fingerprint({"KIS_ACCESS_TOKEN": "token-a"})
    after = secrets_fingerprint({"KIS_ACCESS_TOKEN": "token-b"})
    assert before != after
    assert "token-a" not in before and "token-b" not in after


def test_token_issuance_is_disabled_without_explicit_local_opt_in(tmp_path):
    client = KISClient({"KIS_APP_KEY": "test", "KIS_APP_SECRET": "test"}, cache_dir=tmp_path)
    try:
        client._token()
    except KISError as exc:
        assert "KIS_ACCESS_TOKEN" in str(exc)
        return
    raise AssertionError("automatic issuance must be disabled by default")


def test_kr_market_rankings_use_two_first_page_requests(tmp_path):
    client = KISClient({"KIS_ACCESS_TOKEN": "daily-token"}, cache_dir=tmp_path)
    responses = [{"output": [{"mksc_shrn_iscd": "005930"}]}, {"output": [{"mksc_shrn_iscd": "000660"}]}]
    calls = []

    def fake_get(path, tr_id, params):
        calls.append((path, tr_id, params))
        return responses.pop(0)

    client._get = fake_get  # type: ignore[method-assign]
    rankings = client.market_rankings(Market.KR)

    assert len(calls) == 2
    assert {call[0] for call in calls} == {
        "/uapi/domestic-stock/v1/quotations/volume-rank",
        "/uapi/domestic-stock/v1/ranking/fluctuation",
    }
    assert len(rankings["거래대금·거래량 순위"]) == 1
    assert len(rankings["상승률 순위"]) == 1


def test_us_market_rankings_use_two_requests_per_exchange(tmp_path):
    client = KISClient({"KIS_ACCESS_TOKEN": "daily-token"}, cache_dir=tmp_path)
    responses = [{"output2": [{"symb": "TEST"}]} for _ in range(6)]
    calls = []

    def fake_get(path, tr_id, params):
        calls.append((path, tr_id, params))
        return responses.pop(0)

    client._get = fake_get  # type: ignore[method-assign]
    rankings = client.market_rankings(Market.US)

    assert len(calls) == 6
    assert {call[2]["EXCD"] for call in calls} == {"NAS", "NYS", "AMS"}
    assert len(rankings["거래대금·거래량 순위"]) == 3
    assert len(rankings["상승률 순위"]) == 3
