# 구현·검증 기록

2026-11-09 로컬 작업 결과. 운영 배포 기록이 아니다.

## 완료

- 인증된 업무 API 29개와 상태 확인 API 1개
- 사용자·회사 고정 인증과 매장별 역할 확인
- 20개 canonical 테이블 직접 사용, 추가 테이블·컬럼 없음
- 자동 현장 이관, 기본 100% 진행률, 부분 진행 사유, 제출·재제출·검토
- 파일 범위·무결성·촬영 위치 연결 검증
- 회사 간 요청·첨부·대화와 결정/반영 원자성
- 보고서 입력 고정, 이전 주·당시 다음 주 전망, Agent 점유·결과·승인
- 모바일 화면 및 선택형 실제 API 연결 모드
- 생성형 OpenAPI와 기능별 BE↔DB 가이드

## 실행한 검증

- Python `pytest tests` — 59 passed. SQLite 임시 DB와 실제 FastAPI HTTP 계약 테스트. 기존 기능 포함.
- Node `npm test` — 7개 스크립트 통과. 진행률, 시연 이력, 요청, 시간대, API 클라이언트 포함.
- `node build.cjs` — 5개 정적 페이지 생성.
- Python 컴파일과 새 JS 파일 문법 검사 통과.
- Gitleaks의 backend, FE src, docs/api 검사 — 유출 패턴 없음.
- TestClient는 사용 중인 Starlette의 httpx 사용 중단 예정 경고 1개를 출력했으며 실패는 없었다.

## 미실행·후속

- AWS/운영 DB 접근·수정·배포 및 Git 커밋·푸시
- 실제 PostgreSQL의 FK·트리거·행 잠금·권한·RLS 검증
- 실제 GPU/LLM 호출·보고서 생성 워커 실행
- 운영 로그인/SSO 발급·폐기 체계와 공개 서비스 설정
- 실기기 촬영·화면·네트워크 검증, 브라우저 자동화와 컴퓨터 유즈
- 본사·지도·간트·보고서 시연 화면의 실제 API 전환
- 기존 시스템 JSON 증빙의 정규 제출 이관과 미연결 업로드 정리 정책

## 이어서 할 순서

1. 복제한 20개 테이블 DB와 파일 저장소를 준비한다.
2. users·organizations·site_memberships와 서버 인증 매핑을 사전 설정한다.
3. project_app:create_app을 로컬/내부망에서 실행한다.
4. 모바일 ?mode=api에서 현장 계정으로 조회·제출·요청을 검증한다.
5. 본사 계정과 Vision/Agent 서비스 계정으로 결과 연결을 검증한다.
6. PostgreSQL 통합 검증 후 FE 나머지 화면을 전환하고 운영 연결을 결정한다.

자세한 API 연결점은 README.md, ENDPOINTS.md, DB_CONNECTIONS.md에 있다.
