from __future__ import annotations

from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from scanner.kis_client import KISClient


MOCK_RANKINGS = {
    "거래대금·거래량 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "100000"},
    ],
    "상승률 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "prdy_ctrt": "2.0"},
    ],
}


def test_full_market_button_renders_candidates_without_live_kis_request():
    with patch.object(KISClient, "market_rankings", return_value=MOCK_RANKINGS):
        app = AppTest.from_file("/home/ubuntu/ymym_review/app.py")
        app.run(timeout=30)
        full_market_button = next(button for button in app.button if button.label == "전종목 후보 검색")
        full_market_button.click()
        app.run(timeout=30)

    assert not app.exception
    assert any(item.value == "전종목 1차 후보 결과" for item in app.subheader)
    assert any(item.label == "전종목 후보를 골라 정밀 분석" for item in app.selectbox)
