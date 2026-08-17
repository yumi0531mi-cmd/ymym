from __future__ import annotations

from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from scanner.kis_client import KISClient


APP_PATH = "/home/ubuntu/ymym_review/app.py"
MOCK_RANKINGS = {
    "거래대금·거래량 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70000", "acml_vol": "100000"},
    ],
    "상승률 순위": [
        {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "prdy_ctrt": "2.0"},
    ],
}


def test_missing_token_disables_kis_request_buttons():
    st.cache_resource.clear()
    app = AppTest.from_file(APP_PATH)
    app.run(timeout=30)

    buttons = {button.label: button for button in app.button}
    assert buttons["고정 유동성 시작목록 검색"].disabled
    assert buttons["전종목 후보 검색"].disabled
    assert buttons["선택 종목 분석"].disabled
    assert any("한국투자증권 연결: 아직 준비 중입니다" in item.value for item in app.warning)


def test_full_market_button_renders_candidates_with_connected_mock_client():
    original_init = KISClient.__init__

    def init_with_mock_token(self, secrets=None, cache_dir=".scanner_cache"):
        original_init(self, {"KIS_ACCESS_TOKEN": "test-token"}, cache_dir=cache_dir)

    st.cache_resource.clear()
    with (
        patch.object(KISClient, "__init__", init_with_mock_token),
        patch.object(KISClient, "market_rankings", return_value=MOCK_RANKINGS),
    ):
        app = AppTest.from_file(APP_PATH)
        app.run(timeout=30)
        full_market_button = next(button for button in app.button if button.label == "전종목 후보 검색")
        assert not full_market_button.disabled
        full_market_button.click()
        app.run(timeout=30)

    assert not app.exception
    assert any(item.value == "전종목 1차 후보 결과" for item in app.subheader)
    assert any(item.label == "전종목 후보를 골라 정밀 분석" for item in app.selectbox)
