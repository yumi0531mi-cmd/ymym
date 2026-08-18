# Render 실시간 배포

이 앱은 KIS 실전 WebSocket `ws://ops.koreainvestment.com:21000`에 직접 연결해야 체결가를 1초 화면 주기로 표시할 수 있다. Streamlit Community Cloud에서 이 포트가 연결되지 않을 때 `render.yaml`로 Render Web Service에 배포한다.

## 배포

1. Render Dashboard에서 **New > Blueprint**를 선택한다.
2. GitHub 저장소 `yumi0531mi-cmd/ymym`을 연결한다.
3. Blueprint가 요구하는 `KIS_APP_KEY`, `KIS_APP_SECRET`을 Secret 값으로 입력한다.
4. 생성 후 앱의 사이드바가 `KIS 실시간 체결 연결됨 · 1초 화면 갱신`으로 바뀌는지 확인한다.

Supabase는 선택 사항이다. 기존 검증 데이터를 계속 보관하려는 경우에만 배포 후 Render 환경 변수에 `SUPABASE_URL`, `SUPABASE_KEY`를 별도로 추가한다.

## 운영 선택

- 무료 인스턴스는 15분 동안 인바운드 트래픽이 없으면 잠들고 재기동에 시간이 걸릴 수 있다.
- 장 시작 전에 URL을 열어 연결 상태를 확인한다.
- 장중 항상 켜진 스캐너가 필요하면 Render 유료 Web Service로 변경한다.
- 비밀키는 GitHub 파일이나 `render.yaml`에 직접 입력하지 않는다.

## 판정

`KIS 체결`이 표시되면 KIS WebSocket 체결값이다. `KIS REST 기준`이면 WebSocket 연결 전 또는 재연결 중의 안전 대체값이므로 1초 체결 시세로 간주하지 않는다.
