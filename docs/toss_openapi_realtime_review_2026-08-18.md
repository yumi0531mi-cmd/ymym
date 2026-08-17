# 토스증권 Open API 실시간 현재가 검토 — 2026-08-18 KST

## 공식 출처

- 토스증권 Open API 소개: <https://corp.tossinvest.com/ko/open-api>
- 공식 개발자 개요: <https://openapi.tossinvest.com/openapi-docs/overview.md>
- 공식 Market Data API 명세: <https://openapi.tossinvest.com/openapi-docs/latest/api-reference/Apis/MarketDataApi.md>
- 공식 기계 판독용 문서 안내: <https://developers.tossinvest.com/llms.txt>

## 확인된 내용

토스증권 계좌 보유자는 WTS의 설정 > Open API에서 `client_id` 및 `client_secret`을 발급받을 수 있다. 모든 API는 OAuth 2.0 Client Credentials Grant 액세스 토큰을 사용하며, API 호출 서버의 허용 IP를 토스증권 WTS에 등록해야 한다.

공식 소개 페이지에는 표준 REST와 WebSocket이라는 홍보 문구가 있으나, 공식 개발자 개요 및 기계 판독용 명세의 현재 제공 연동 방식은 **REST API만**이라고 명시되어 있다. 따라서 비공식 토스 웹 내부 WebSocket을 스캐너에 쓰지 않는다.

공식 `GET /api/v1/prices`는 현재가를 최대 200개 심볼까지 콤마 구분 다건 조회한다. Market Data 그룹의 현재 공개 요청 한도는 초당 최대 15회이며, 한 번의 다건 요청으로 5개 또는 20개 카드 가격을 조회할 수 있으므로 현재가 전용 1초 갱신 자체는 공식 REST 호출 한도상 가능하다. 정확한 한도는 응답의 `X-RateLimit-*` 헤더를 확인해 런타임에서 조절해야 한다.

## 설계 결론

1초 현재가 갱신은 KIS REST 폴링이 아니라 토스증권 공식 `GET /api/v1/prices` 다건 요청을 1초마다 호출하는 방식이 가장 현실적이다. 다만 현재 Streamlit Community Cloud의 egress IP를 토스 허용 IP로 안정적으로 등록할 수 있는지 보장되지 않으므로, 허용 IP가 고정된 실행 환경이 필요하다. 자동 주문 API·계좌 API·조건주문 API는 사용하지 않고, 시세 REST API만 사용한다.

비공식 토스 WebSocket은 문서화되지 않은 내부 인터페이스로 경로·인증·응답 형식이 예고 없이 바뀔 수 있어 사용하지 않는다.
