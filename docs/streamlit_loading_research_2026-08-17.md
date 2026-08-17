# Streamlit 무한 로딩 외부 조사 — 2026-08-17

현재 앱은 공개 상태이며 `healthz`가 `200 {"status":"ok"}`를 반환하지만, 브라우저 본문은 스피너에서 멈춘다. 이는 단순 접근 권한이나 프로세스 미기동 문제와 일치하지 않는다.

Streamlit Community Cloud 지원 커뮤니티에는 백엔드가 예외 없이 완료되고 네트워크 요청이 정상이어도 브라우저가 로딩 화면에 멈출 수 있으며, 컨테이너·리소스·플랫폼 오케스트레이션 또는 의존성·Python 호환성이 원인이 될 수 있다는 사례가 있다. 해당 사례는 재부팅이 임시 복구가 될 수 있으나 지속 시 배포 환경을 새로 구성하는 절차를 제안한다.[1]

또 다른 Community Cloud 사례에서는 여러 앱이 동시에 로딩 스피너에 머물렀고, Streamlit 측에서 짧은 서비스 중단이 있었다고 확인했다.[2] 따라서 현재 증상은 앱 코드뿐 아니라 Community Cloud 플랫폼·브라우저 WebSocket 경로도 함께 점검해야 한다.

## 출처

[1] [Streamlit Community — App stuck on blank loading screen](https://discuss.streamlit.io/t/app-stuck-on-blank-loading-screen-websocket-connects-but-never-streams-data-no-errors-in-logs/121845)

[2] [Streamlit Community — App does not load, just spins](https://discuss.streamlit.io/t/app-does-not-load-just-spins/61910)
