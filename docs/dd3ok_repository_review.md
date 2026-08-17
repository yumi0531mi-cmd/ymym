# dd3ok 저장소 검토 — 전종목 스크리너 활용 관점

## 결론

`dd3ok` 계정의 시장 데이터 관련 저장소 중 `tossinvest-api-skill`, `naverstock-api-skill`, `naverfinance-api-skill`은 모두 **MIT 라이선스**가 명시돼 있다. 따라서 라이선스 사본과 저작권 고지를 유지하는 조건으로 코드를 재사용하거나 수정할 수 있다.[1] [2] [3] 다만 이들이 이용하는 토스·네이버의 공개 웹 기반 인터페이스는 모두 **비공식·미문서화** 방식이며, 제공자 정책·응답 형식·접근 허용 범위가 바뀔 수 있다. 따라서 해당 코드는 무제한·고빈도 전종목 수집기가 아니라, 데이터 구조와 안전한 단일·저빈도 조회 패턴을 참고하는 용도로만 적합하다.

| 저장소 | 라이선스 | 유용한 범위 | 스캐너 통합 판단 |
|---|---|---|---|
| `tossinvest-api-skill` | MIT | 국내·미국 공개 시장 랭킹, 스크리너 조건, 테마·지수·뉴스 구조 | **직접 상시 연동 제외.** 전종목 후보 생성 UI·필드 설계 참고 가능. 비공식 웹 인터페이스이며 저장소 자체도 대량 반복 조회·백그라운드 수집을 금지한다. |
| `naverstock-api-skill` | MIT | 국내 시장 랭킹·종목 검색, 국내외 기본 시세·뉴스·공시 구조 | **저빈도 보조 자료 후보.** 공개·무인증 전용이지만 고빈도·대량 스크래핑을 금지한다. 실시간 단타의 기준 가격에는 사용하지 않는다. |
| `naverfinance-api-skill` | MIT | 국내 시가총액·거래량·상승/하락·ETF/ETN 순위, 환율·시장지표 | **국내 시장 컨텍스트 보조 후보.** legacy 호환 용도이며, 현재형 기능은 `naverstock-api-skill`을 우선하도록 저장소가 안내한다. |
| `market-data-skills` | 라이선스 미표기 | 여러 공개 데이터 워크플로 참고 | **코드 재사용 금지.** 라이선스가 명시되기 전에는 아이디어·문서만 참고한다. |
| `yahoo-finance-market-skill` | 라이선스 미표기 | Yahoo Finance를 활용한 시장 데이터 워크플로 참고 | **코드 재사용 금지.** Yahoo 데이터 이용 조건·실시간성·라이선스를 별도 확인하지 않은 상태다. |

## 권장 데이터 계층

전종목 후보 선별에는 한국투자증권의 공식 순위 API를 우선 사용한다. 공식 예제에는 국내 `volume-rank`와 다수의 순위 API, 해외 NASDAQ·NYSE·AMEX별 거래대금·거래량·등락률·거래증가율·체결강도·거래량 급증 순위 API가 포함돼 있다.[4] 이 순위 응답으로 시장 전체에서 소수 후보를 만든 후에만 KIS 현재가·1호가·1분봉으로 정밀 분석한다.

네이버·토스 기반 자료는 단타 가격 산출의 원천이 아니라, 종목명 검색·테마·뉴스·공시·시장 컨텍스트 보조 표시에 한정한다. 공개 웹 기반 비공식 인터페이스에서 403·429·로그인·챌린지 응답이 나오면 재시도를 반복하지 않고 즉시 중단한다.[1] [2] [3]

> 자동 주문은 어떤 저장소도 활용하지 않으며, 후보의 진입·목표·손절 판단은 계속 KIS 시세와 완료봉 구조를 기반으로 사용자가 직접 내립니다.

## 참고 자료

[1] [dd3ok/tossinvest-api-skill](https://github.com/dd3ok/tossinvest-api-skill)

[2] [dd3ok/naverstock-api-skill](https://github.com/dd3ok/naverstock-api-skill)

[3] [dd3ok/naverfinance-api-skill](https://github.com/dd3ok/naverfinance-api-skill)

[4] [한국투자증권 공식 open-trading-api 예제](https://github.com/koreainvestment/open-trading-api)
