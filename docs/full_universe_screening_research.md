# 국내·미국 전종목 스크리닝 조사 — 2026-08-17

현재 앱의 빠른검색은 국내 14개·미국 21개로 고정된 시작목록만 조회한다. 따라서 전 종목 스캔은 구현되어 있지 않다.

한국투자증권의 공식 `open-trading-api` 예제 저장소를 조사한 결과, KIS는 개별 종목 현재가를 전수 요청하지 않고도 시장별 순위 API를 통해 1차 후보를 가져올 수 있는 경로를 제공한다. 국내는 거래량·등락률·거래대금·시장가치 등 순위 API와 `volume-rank`가, 해외는 시가총액·신고저가·가격등락·거래증가·거래대금·거래회전율·거래량·등락률·체결강도·거래량급증 순위 API가 공식 예제에 포함돼 있다.[1]

전종목이라는 표현은 모든 상장 종목의 개별 1분봉을 동시에 분석한다는 뜻으로 구현하면 안 된다. 수천 종목에 대해 현재가·호가·분봉을 개별 요청하면 KIS 호출 제한을 빠르게 초과한다. 대신 순위 API를 몇 건 호출해 국내 KOSPI·KOSDAQ·ETF와 미국 NASDAQ·NYSE·AMEX의 당일 유동성·등락·거래대금 상위 후보를 모은 뒤, 중복 제거한 소수 후보에만 개별 현재가·호가·1분봉의 3건 정밀 분석을 적용하는 단계형 구조가 적절하다.

## 출처

[1] [Korea Investment & Securities 공식 Open API 예제 저장소](https://github.com/koreainvestment/open-trading-api) — `examples_user/domestic_stock/domestic_stock_functions.py`, `examples_user/overseas_stock/overseas_stock_functions.py`의 ranking API 경로 확인.
