# 운영 DB와 업무별 데이터 흐름

2026년 11월 9일 운영 DB를 읽기 전용 조회한 결과 public 스키마에는 **저장 테이블 20개, 필드 186개, 뷰 0개**가 있다. 이 폴더의 기존 ERD·`schema.json`과 SQL은 **005 적용 당시 호환 뷰 12개를 포함한 자료**다. 최신 설명은 [한국어 상세 트리](../DB_한국어_상세_트리.md)와 [설계 결정](../../planning/2026-11-09-db-design-decisions.md)을 확인한다. 뷰 제거 이후의 SQL·기존 코드 이관은 별도 검토 중이다.

## 화면과 문서

- [쉬운 HTML](ALOHA_ERD_쉬운버전.html)에서 12개 업무와 6개 자료 묶음을 확인한다.
- [상세 ERD HTML](ALOHA_ERD_업무흐름.html)에서 실제 외래 키와 전체 필드를 확인한다.
- [현재 스키마 정의](schema.json), [필드별 사용처](DB_테이블_필드_전후_RLS_CLS.md), [기능별 연결표](프로토타입_기능별_DB_API_연결표.md)를 함께 사용한다.
- [신규 버전 사용설명서](사용설명서.md)는 접속 안내를 이미 받은 연동 담당자용이다.
- [전환 및 검증 기록](../../planning/2026-11-09-db20-consolidation.md)을 참고한다.
- [보안 검사 기록](SECURITY_REVIEW.md)에서 이번 업로드 범위를 확인한다.

저장소를 내려받아 HTML을 브라우저로 열면 왼쪽 업무 버튼과 필드 펼치기가 동작한다. 외부 라이브러리나 서버 없이 열 수 있다. 이 HTML은 구조와 연결 경로를 설명한다. 업무별 경로 중 정규 API 추가 구현이 필요한 부분은 기능별 연결표에 표시했다.

## 기존 프로그램과의 호환 · 005 당시 기록

아래 뷰는 005 당시의 호환 경로다. 현재 운영 DB에는 없다. 저장소의 일부 기존 코드는 여전히 이 뷰를 참조하므로 뷰 없는 환경에서 그대로 실행한다고 가정하지 않는다.

`workflow_projects`, `workflow_cycles`, `workflow_vision_batches`, `workflow_manager_evidence`, `workflow_report_snapshots`, `workflow_vision_deliveries`, `workflow_extension_context`는 뷰다. 데이터는 `task_events.details`의 `workflow_state`에 보관한다. 기존 문자열 ID와 JSON, 보고서 스냅샷 내용을 유지한다. 주차 기록보다 먼저 생성한 보고서도 지원한다.

나머지 뷰는 `legacy_change_requests`, `legacy_site_memberships`, `site_organizations`, `change_request_decisions`, `task_review_queue`다. 추가 기간 요청 코드는 이 호환 경로를 통해 공통 요청·이력을 사용한다. 승인과 실제 일정 적용은 별도 이벤트로 기록한다.

## 설치와 업그레이드

- 새 빈 PostgreSQL 16 DB용 [005 시점 구조 SQL](../../../src/backend/sql/020_twenty_tables_clean.sql)을 `psql -1 -v ON_ERROR_STOP=1`로 적용한다. 로그인 계정·권한·운영 설정은 별도 구성한다.
- [005 전환 SQL](../../../src/backend/sql/005_consolidate_twenty_tables.sql)은 검증한 전환 전 구조를 한 번 바꾸는 용도다. 운영 적용을 마쳤으며 같은 DB에 다시 실행하지 않는다. 비어 있지 않은 미지원 업무 자료를 발견하면 전체 변경을 취소한다.
- 기존 기본 자료와 workflow 자료는 트랜잭션 안에서 내용 지문을 비교한다. 실제 원본 데이터·서버 백업과 접속 값은 저장소에 포함하지 않는다.

## 005 당시 검증과 남은 연결

복원 DB에서 SQL 제약, 매장 간 첨부 차단, 본사 검토 권한, 반려 사유, 요청의 단일 결정·적용, 추가 기간 요청 코드의 등록·승인·적용·재시도, 7개 workflow 뷰와 실제 보고서 이관을 검사했다. 전환 후 운영 health·프로젝트·주간 체크리스트·추가 기간 요청 조회와 서비스 재개를 확인했다.

기존 내부 서비스 계정의 동작을 유지했다. 최종 사용자별 RLS·컬럼 제한, 정규 관측·증빙·보고서 API와 인증된 사용자 컨텍스트 연결은 추가 구현이 필요하다. 테이블 통합 완료와 모든 업무 API 연결 완료는 구분해 사용한다.

구조 탐색기 검사는 다음 명령으로 실행한다.

```sh
node docs/architecture/erd-workflow/verify-erd.cjs
```

복원 DB 전용 회귀 검사는 `tests/verify_twenty_table_schema.sql`, `tests/verify_twenty_table_workflow.py`다. 테스트용 계정·데이터를 생성하므로 운영 DB에서 실행하지 않는다.
