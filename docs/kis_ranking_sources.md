# KIS 후보풀 순위 API 근거

후보풀은 거래량 TOP100과 거래대금 TOP100의 중복 제거 합집합으로 구성한다. 이 기준은 사용자 요구사항이며, 현재 앱의 기존 `거래량 + 상승률` 병합 방식을 대체한다.

## KIS 공식 샘플 확인

공식 저장소: https://github.com/koreainvestment/open-trading-api

국내 `거래량순위[v1_국내주식-047]` 샘플은 `/uapi/domestic-stock/v1/quotations/volume-rank`, TR ID `FHPST01710000`를 사용한다. `FID_BLNG_CLS_CODE`는 `0`일 때 평균거래량, `3`일 때 거래금액순으로 문서화돼 있다. 동일한 순위 API를 두 번 호출해 거래량과 거래대금 TOP100을 각각 받아 합집합을 만든다.

해외 `해외주식 거래량순위[해외주식-043]` 샘플은 `/uapi/overseas-stock/v1/ranking/trade-vol`, TR ID `HHDFS76310010`를 사용한다. 해외 거래대금 순위 API는 `/uapi/overseas-stock/v1/ranking/trade-pbmn`, TR ID `HHDFS76320010`이다. 두 결과는 NAS·NYS·AMS별로 수집한 후 중복을 제거한다.

후보풀의 크기는 합집합 크기 그대로 화면에 표시한다. 가격 상한·당일 과열 제외·최소 신호 점수·1차 목표·현재 진입 조건은 후보풀 생성 뒤 단계별로 적용한다.
