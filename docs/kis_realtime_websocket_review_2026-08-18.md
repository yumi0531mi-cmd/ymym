# KIS 개인 API 기반 1초 실시간 시세 검토 — 2026-08-18 KST

## 공식 출처

- KIS Open Trading API 공식 저장소: <https://github.com/koreainvestment/open-trading-api>
- KIS Developers 포털: <https://apiportal.koreainvestment.com/>

## 확인된 내용

한국투자증권 공식 샘플 저장소는 REST 접근토큰과 별도로 WebSocket 접속키 발급 기능을 제공하며, 국내주식·해외주식별 WebSocket 통합 함수와 실행 예제를 포함한다. 예제는 `auth_ws()`로 WebSocket 접속키를 발급한 뒤, `KISWebSocket` 연결에서 국내 실시간 호가 구독을 수행한다.

공식 저장소의 구조에는 국내주식 및 해외주식 각각의 WebSocket 통합 함수·예제가 존재한다. 따라서 기존 KIS 실전 앱키·앱시크릿을 기반으로 현재가·체결·호가를 실시간 구독하는 것은 공식 지원 범위다. 이는 KIS REST의 1분·5시간 예산을 사용하지 않는 별도 실시간 연결이어야 하며, 분봉·후보선별·목표가 재계산 REST 호출과 분리해야 한다.

공식 예제의 문제 해결 안내는 실시간 WebSocket 연결 오류 시 KIS Developers 고객 HTS ID 설정이 정확한지 점검하도록 적고 있다. 단순 시세 구독에 필요한 정확한 구독 필드와 해외 종목 구독 코드·연결 제한은 구현 전에 공식 예제 파일과 API 포털의 최신 명세를 다시 확인한다.

## 설계 결론

토스증권 추가 연동 대신 기존 KIS 개인 API만 쓴다. 기존 REST 클라이언트는 전종목 후보선별, 완료 1·5분봉 분석, 15분 단위 호가·구조 갱신을 담당한다. 별도의 KIS WebSocket 수신기는 화면에 표시 중인 후보의 현재가·등락률·최근 체결을 1초 단위로 메모리에 반영한다. 자동 주문 관련 엔드포인트는 구현하지 않는다.
