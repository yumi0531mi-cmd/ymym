from __future__ import annotations

from scanner.market_screener import merge_rankings
from scanner.models import Market


def test_merge_rankings_prioritizes_symbols_in_both_rankings():
    rankings = {
        "거래대금·거래량 순위": [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "1000"},
            {"mksc_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스", "stck_prpr": "200000"},
        ],
        "상승률 순위": [
            {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "prdy_ctrt": "3.2"},
        ],
    }

    candidates = merge_rankings(Market.KR, rankings)

    assert [candidate.symbol for candidate in candidates] == ["005930", "000660"]
    assert candidates[0].screen_score == 100
    assert candidates[1].screen_score == 45


def test_merge_rankings_excludes_us_directional_products():
    rankings = {
        "거래대금·거래량 순위": [{"symb": "SOXL", "ovrs_item_name": "DIREXION SEMICONDUCTOR BULL 3X", "last": "20.1"}],
        "상승률 순위": [{"symb": "SOXL", "ovrs_item_name": "DIREXION SEMICONDUCTOR BULL 3X", "rate": "4.0"}],
    }

    candidates = merge_rankings(Market.US, rankings)

    assert candidates == []


def test_merge_rankings_keeps_plain_us_stock():
    rankings = {"상승률 순위": [{"symb": "NVDA", "ovrs_item_name": "NVIDIA", "last": "120", "rate": "4.0"}]}
    candidates = merge_rankings(Market.US, rankings)
    assert [candidate.symbol for candidate in candidates] == ["NVDA"]


def test_merge_rankings_excludes_kr_directional_products_but_keeps_plain_etfs():
    rankings = {
        "상승률 순위": [
            {"mksc_shrn_iscd": "069500", "hts_kor_isnm": "KODEX 200", "prdy_ctrt": "1.2"},
            {"mksc_shrn_iscd": "122630", "hts_kor_isnm": "KODEX 레버리지", "prdy_ctrt": "4.2"},
            {"mksc_shrn_iscd": "252670", "hts_kor_isnm": "KODEX 200선물인버스2X", "prdy_ctrt": "3.1"},
            {"mksc_shrn_iscd": "123456", "hts_kor_isnm": "일반 ETN", "prdy_ctrt": "2.0"},
        ]
    }

    candidates = merge_rankings(Market.KR, rankings)

    assert {candidate.symbol for candidate in candidates} == {"069500", "123456"}
