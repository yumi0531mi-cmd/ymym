# 검증 성과 영구 보관 설정 안내

이 스캐너는 신호가 나온 뒤 완료된 분봉으로 **목표가 선도달 여부와 비용 반영 결과**를 채점합니다. Streamlit Community Cloud의 임시 파일은 앱 재시작 때 사라질 수 있으므로, 여러 날에 걸친 실제 표본을 쌓으려면 외부 저장소 연결이 필요합니다. 이 저장소에는 이미 Supabase REST 저장 코드가 포함되어 있으며, 아래 한 번의 설정만 하면 신호·채점 결과·전략 버전이 중복 없이 저장됩니다.

> `SUPABASE_SERVICE_ROLE_KEY`는 브라우저나 공개 화면에 절대로 노출하면 안 됩니다. Streamlit Cloud의 **Secrets**에만 저장해야 합니다. Supabase도 서비스 키는 RLS를 우회하므로 고객에게 노출하지 말아야 한다고 안내합니다.[1]

## 1. Supabase 프로젝트와 테이블 만들기

먼저 [Supabase](https://supabase.com/)에서 무료 프로젝트를 하나 만듭니다. 프로젝트가 준비되면 왼쪽 메뉴에서 **SQL Editor**를 열고, 아래 SQL 전체를 붙여 넣은 뒤 **Run**을 한 번 누릅니다.

```sql
create table if not exists public.scanner_events (
  id text primary key,
  kind text not null,
  created_at timestamptz not null,
  payload jsonb not null,
  inserted_at timestamptz not null default timezone('utc', now())
);

create index if not exists scanner_events_kind_created_at_idx
  on public.scanner_events (kind, created_at asc);

alter table public.scanner_events enable row level security;
```

이 테이블은 `id`를 기본키로 사용합니다. 따라서 같은 신호를 다시 저장해도 새 행을 무한히 만들지 않고 기존 기록을 갱신합니다. `kind`와 `created_at` 색인은 전략·세션별 검증 결과를 시간순으로 읽을 때 쓰입니다. Supabase는 데이터베이스 스키마를 REST API로 자동 노출하며, 해당 API의 기본 경로는 `https://<project_ref>.supabase.co/rest/v1/`입니다.[2]

## 2. Streamlit Cloud에 두 값만 추가하기

Streamlit Cloud에서 이 앱을 연 다음 **Settings → Secrets**로 이동합니다. 기존 KIS 설정은 그대로 두고, 맨 아래에 다음 두 줄을 추가한 뒤 저장합니다.

```toml
SUPABASE_URL = "https://프로젝트ID.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "Supabase의 service_role 키"
```

`SUPABASE_URL`은 Supabase의 **Settings → API** 화면에 있는 Project URL입니다. `SUPABASE_SERVICE_ROLE_KEY`는 같은 화면의 service_role 키입니다. 키 값을 이 문서, GitHub, 화면 캡처 또는 채팅에 적지 마십시오. 저장한 뒤 앱을 재시작하면 됩니다.

| 확인 위치 | 정상 상태 | 뜻 |
|---|---|---|
| 앱의 검증 저장 상태 | **Supabase 영속 저장** | 새 신호와 사후 채점 결과가 클라우드 DB에 보관됩니다. |
| 앱의 검증 저장 상태 | **로컬 임시 저장** | 앱은 사용할 수 있지만, 재시작 후 누적 표본이 사라질 수 있습니다. |
| Supabase Table Editor | `scanner_events` 테이블에 행 생성 | 신호·채점 이벤트가 실제로 저장되고 있습니다. |

## 3. 안전한 운영 원칙

이 앱은 서버에서만 Supabase에 연결하므로 service_role 키를 사용해도 됩니다. 다만 **Secrets 이외의 위치에 키를 옮기면 안 됩니다**. RLS는 공개 스키마의 테이블에 켜 두고, 사용자 브라우저에 직접 DB 접근 권한을 주지 않는 방식입니다. Supabase 공식 문서도 공개 스키마 테이블에는 RLS를 활성화하고 필요한 역할에만 권한을 주도록 권장합니다.[1]

설정 후 첫날에는 `scanner_events`에 `validation_case`가 생기는지 확인하십시오. 30분 이상 지난 신호는 같은 종목의 새 분봉 분석 때 사후 채점되고, 결과가 다시 같은 행에 저장됩니다. 현재 저장 방식은 **실제 신호만** 보관하며 임의의 과거 성과를 만들지 않습니다.

## 참고자료

[1]: https://supabase.com/docs/guides/database/postgres/row-level-security "Supabase Row Level Security 공식 문서"
[2]: https://supabase.com/docs/guides/api "Supabase Data REST API 공식 문서"
