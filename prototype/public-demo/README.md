# 공사 관리 웹 프로토타입 — 공개용 예시

브랜드, 매장, 공정표, 사진, 날짜와 수치는 가상 자료입니다. 원본 공정표·실제 현장 사진·보고서·접속 정보는 포함하지 않습니다. QR은 example.com으로 연결됩니다.

글꼴 `assets/noto-sans-kr.ttf`(Noto Sans KR)는 SIL Open Font License 1.1을 따르며, 저작권 표시와 라이선스 전문은 [`assets/OFL.txt`](assets/OFL.txt)에 있습니다.

Node.js 설치 후 `node build.cjs`, `node server.cjs`를 실행하고 http://127.0.0.1:4318 에서 확인합니다. 브라우저 로컬 저장만 사용하는 시연이며 서버 동기화·AI 분석·알림은 구현되어 있지 않습니다.


이 디렉터리는 공개 시연 전용이다. 실제 사진·공정표·업로드 파일로 assets를 교체하지 않는다.
루트 `.gitignore`의 경로별 허용 목록은 기존 공개 시연 자료와 HTML 소스에만 적용하며 `dist/`는 커밋하지 않는다.
백엔드 문서와 함께 통합했지만 실제 API·AI 연결을 추가한 것은 아니다.

사진을 불러오지 못하면 안내와 재시도 버튼을 표시한다. `node tests/media-state.test.cjs`로 오류·복구 동작을 확인할 수 있다.

별도의 `prototype/aloha-web/src/`에는 화면 상태 분류와 챗봇 응답·근거 처리 모듈이 포함된다. 테스트는 해당 `tests/`의 `aws-view-model.test.cjs`와 `qwen-chat.test.cjs`로 실행한다. 이 모듈은 공개 시연 화면에 연결된 실서비스가 아니다.
