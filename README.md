# 반복단타 스캐너 v5.1

한국투자증권(KIS) REST 데이터를 사용하는 **수동매매 판단 보조용** Streamlit 앱입니다. 주문 API는 포함하지 않으며, 어떤 신호·점수·보정확률도 수익이나 승률을 보장하지 않습니다. 사용자가 직접 시세, 호가, 유동성, 위험 상태와 목표 구간을 확인한 뒤 매수·매도를 판단하도록 설계했습니다.

> 이 앱은 연구·분석 도구이며 개인 맞춤형 금융 자문이 아닙니다. 투자에는 원금 손실 위험이 있습니다.

## v5.1 핵심 기능

| 영역 | 구현 내용 |
|---|---|
| 전략 구조 | `TREND_SWING`(상승 추세 눌림)과 `RANGE_SWING`(박스 하단 평균회귀)을 분리합니다. |
| 실제 반복폭 | 완료 1분봉의 시간순 저점→고점 Swing에서 0.5~5.0% 유효 상승 Swing 3회 이상을 찾고 대표 폭·주기를 계산합니다. |
| 지속성 | Swing 일관성, 대표폭, VWAP 체류, 구조, 3중 유동성, 주기 안정성, 회복력, 스프레드, 피로도를 0~100 지속성 점수로 평가합니다. |
| 위험 상태 | Soft Stop, Hard Stop, 동적 회복시간과 `NORMAL_PULLBACK`·`SHAKEOUT`·`WARNING`·`REAL_BREAKDOWN`·`HARD_EXIT` 상태를 표시합니다. |
| 가격 계획 | 실제 매수 참고가, 완료 5분봉 구조 기반 1차·2차 목표, Soft/Hard Stop을 차트와 카드에서 함께 표시합니다. |
| 전종목 후보 | 국내 KRX와 미국 NASDAQ·NYSE·AMEX의 KIS 시장 순위 응답에서 거래대금·거래량·상승률 후보를 중복 제거해 1차 우선순위로 제시합니다. |
| 후보 게이트 | 공통 `FINAL_BUY` 게이트는 세션·Horizon·Swing 3회·지속성·진입 위치·실행 안전·손익비·Cycle 상태를 한 번에 검사합니다. |
| 보정확률 | 동일 시장·세션·전략·점수 구간의 T1-before-stop 실측 표본이 30건 이상일 때만 표시합니다. |
| 주문 | 읽기 전용 시세·호가·분봉 호출만 하며 자동 주문은 하지 않습니다. |

## 전종목 후보 검색

왼쪽의 **전종목 후보 검색**은 수천 종목의 1분봉·호가를 전부 요청하는 기능이 아닙니다. 국내는 시장 순위 API 2건, 미국은 NASDAQ·NYSE·AMEX 각각의 거래대금·상승률 순위 API 총 6건으로 전종목 후보군을 1차 선별합니다. 거래대금·거래량 순위와 상승률 순위에 동시에 있는 종목을 우선 정렬하며, 이 후보 점수는 매수 신호·승률·수익 예측이 아닙니다.

표에서 종목을 고른 뒤 **정밀 분석**을 눌러야만 해당 종목의 현재가·1호가·완료 1분봉을 추가 확인하고, 진입·손절·완료 5분봉 기반 목표가를 표시합니다. 전종목 후보 검색의 호출량과 제한은 [`docs/full_universe_screening_design.md`](docs/full_universe_screening_design.md) 및 [`docs/kis_api_budget.md`](docs/kis_api_budget.md)를 따릅니다.

## 화면 읽는 법

분석 후 먼저 상태 배너와 **지금 대기하거나 조심해야 하는 이유**를 확인하세요. `진입 고려`는 공통 FINAL_BUY 조건을 모두 통과했다는 뜻일 뿐, 매수 지시가 아닙니다.

| 카드 | 의미 |
|---|---|
| 모델 점수 | 지속성·실행 안전·손익비 등 현재 조건 충족도이며 승률이 아닙니다. |
| 지속성 | 0~100 점수와 `PERSISTENT_A/B`, `WATCH`, `UNSTABLE` 등급입니다. |
| 위험 상태 | 정상 파동·정상 눌림·Shakeout·경고·실제 붕괴·Hard Exit을 구분합니다. |
| Horizon | 당일 완료분봉과 남은 세션 시간을 기준으로 `EARLY_FORMING`, `PROJECTED_90/180`, `OBSERVED_300`을 표시합니다. |
| 보정 확률 | 검증 표본 30건 전에는 `보정 전`으로만 표시합니다. |
| Soft Stop | 지지 훼손 확인을 시작하는 가격입니다. 즉시 손절 명령이 아닙니다. |
| Hard Stop | 정상 노이즈 완충 범위 밖의 구조 무효화 가격입니다. |

## Streamlit Secrets 설정

### 1. KIS 필수 Secrets

Streamlit Community Cloud의 **App settings → Secrets**에 아래 값을 넣습니다. 실제 값은 GitHub에 올리지 마세요.

```toml
KIS_APP_KEY = "발급받은 앱키"
KIS_APP_SECRET = "발급받은 앱시크릿"
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

# Community Cloud에서는 당일 발급 토큰을 직접 설정합니다.
KIS_ACCESS_TOKEN = "당일_접근_토큰"
# KIS_ACCESS_TOKEN_EXPIRES_AT = "2026-08-18T07:00:00+09:00"

KIS_ALLOW_TOKEN_ISSUE = "false"
KIS_MIN_REQUEST_INTERVAL_SECONDS = "1.0"
KIS_MAX_REQUESTS_PER_MINUTE = "30"
KIS_MAX_REQUESTS_PER_FIVE_HOURS = "1100"
KIS_MAX_RETRIES = "1"
```

화면을 열기만 해서는 KIS 요청을 보내지 않습니다. 선택 종목 분석은 최대 3건, 빠른검색은 국내 14건·미국 21건, 60초 자동 새로고침은 5시간 최대 약 900건의 요청을 사용합니다. 앱은 1분 30건·5시간 1,100건의 보수적 rolling 예산을 적용하고 429 응답 뒤에는 추가 요청을 대기 처리합니다. 자세한 계산식과 조정 원칙은 [`docs/kis_api_budget.md`](docs/kis_api_budget.md)를 확인하세요.

### 2. v5.1 외부 영속 저장소

5시간 분봉 버퍼, Cycle Cooldown/Hard Kill, 검증 원본과 Calibration은 Cloud 로컬 파일에 보관하면 안 됩니다. Community Cloud는 로컬 파일의 지속성을 보장하지 않으므로 외부 저장소가 필요합니다.[1]

1. Supabase 프로젝트를 만듭니다.
2. SQL Editor에서 이 저장소의 `supabase_schema.sql`을 한 번 실행합니다.
3. Streamlit Secrets와 지속 수집기 환경 변수에 아래를 설정합니다.

```toml
SUPABASE_URL = "https://프로젝트-식별자.supabase.co"
SUPABASE_KEY = "서버-전용-키"
APP_ADMIN_PASSWORD = "관리자전용비밀번호"
```

`SUPABASE_KEY`는 브라우저 코드나 GitHub에 노출하면 안 됩니다. 외부 저장소를 설정하지 않아도 집중 분석 화면은 사용할 수 있지만, 5시간 지속성·Cooldown·Hard Kill·보정확률은 재시작 뒤 보존되지 않습니다.

## 지속 수집기

`run_live_validation_v5_1.py`는 **읽기 전용** 수집기입니다. 종목별 1분봉·신호·Cycle 상태를 외부 저장소에 누적하며 주문을 내지 않습니다. 이 프로그램은 컴퓨터가 켜져 있는 동안 또는 별도의 지속 실행 환경에서만 동작합니다.

```bash
export KIS_APP_KEY="..."
export KIS_APP_SECRET="..."
export KIS_ACCESS_TOKEN="..."
export SUPABASE_URL="https://...supabase.co"
export SUPABASE_KEY="..."

# 한국 정규장 예시: 1분 간격, 005930과 000660
python run_live_validation_v5_1.py --market KR --symbols 005930,000660 --interval 60

# 미국 예시: NASDAQ 티커 2개
python run_live_validation_v5_1.py --market US --symbols SOXL,NVDA --exchange NAS --interval 60
```

`--once` 옵션은 한 번만 수집한 뒤 종료합니다. 호출 주기는 KIS의 실제 호출 한도와 종목 수를 확인한 뒤 정해야 하며, 3초 무조건 폴링을 기본값으로 사용하지 않습니다. 미국 연장거래는 정규장보다 유동성이 낮고 변동성이 높을 수 있으므로 별도 스프레드·유동성 게이트가 적용됩니다.[2]

## 데이터 준비도와 장마감 규칙

| 완료 1분봉 | 상태 | 신규 진입 해석 |
|---|---|---|
| 0~29분 | `EARLY_FORMING` | 구조 형성 관찰; FINAL_BUY 금지 |
| 30~89분 | `EARLY_PROJECTED` | 초기 추정; 보수적 관찰 중심 |
| 90~179분 | `PROJECTED_90` | 반복구조 후보 평가 가능 |
| 180~299분 | `PROJECTED_180` | 지속성 추정 신뢰 강화 |
| 300분 이상 | `OBSERVED_300` | 당일 5시간 구조 기준 평가 |
| 남은 세션 45분 미만 | `EXIT_MANAGEMENT` | 신규 진입 차단; 청산·관리 중심 |

## 로컬 실행과 테스트

```bash
python -m pip install -r requirements.txt
streamlit run app.py
pytest -q
```

테스트는 토큰 자동 발급 차단, KIS 응답 파싱, 분당·5시간 호출 예산, 반복박스, Swing 통계, 위험 상태, FINAL_BUY, Cycle 중복 방지, Hard Kill, Calibration 표본 30건 규칙을 다룹니다. 실제 KIS API 호출은 테스트에 포함하지 않습니다.

## 한계

| 한계 | 해석 |
|---|---|
| 점수와 승률 | 지속성·모델 점수는 승률이 아니며, 실측 검증 없이 80% 승률을 주장할 수 없습니다. |
| 5시간 데이터 | Streamlit 화면만 열어서는 비접속 시간의 분봉을 누적하지 못합니다. 지속 수집기와 외부 DB가 필요합니다. |
| 체결 | 주문·체결 API를 연결하지 않으므로 실제 체결가·반복 회차는 자동으로 추정하지 않습니다. |
| 비용 | 화면 비용 가정은 실제 수수료·세금·슬리피지·스프레드와 다를 수 있습니다. |
| 사건 위험 | 뉴스, 거래정지, 갭 급변, 데이터 지연은 차트 기반 모델만으로 완전히 예측할 수 없습니다. |

## 참고 자료

[1] [Streamlit Docs — Connecting to data](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)

[2] [FINRA — Extended-Hours Trading: Know the Risks](https://www.finra.org/investors/insights/extended-hours-trading)

[3] [KIS Developers — 한국투자증권 Open API 개발자센터](https://apiportal.koreainvestment.com/intro)
