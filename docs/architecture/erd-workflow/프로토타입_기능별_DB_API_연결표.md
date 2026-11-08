# 프로토타입 기능별 DB·API 연결표

[전체 ERD](ALOHA_ERD_업무흐름.html) · [전·후 전체 필드와 RLS·CLS](DB_테이블_필드_전후_RLS_CLS.md)

운영 적용된 20개 테이블을 기준으로 정리했다. 기존 workflow API는 7개 호환 뷰를 통해 공통 이력에 읽고 쓴다. 아래 정규 업무 경로 중 추가 API 연결이 필요한 항목은 마지막 열에 표시했다.

| 프로토타입 기능 | 읽는 테이블과 필드 | 쓰는 테이블과 필드 | 추가로 필요한 작업 |
|---|---|---|---|
| 로그인·현장 전환 | `users.auth_subject`, `site_memberships.site_id/organization_id/role`, `sites.id/code/name` | 관리자 경로에서 `users`, `organizations`, `site_memberships` 등록·권한 변경 | 로그인 인증 연결. FastAPI에서 활성 소속 확인. 현장별 RLS와 응답 필드 제한 |
| 홈·지도·현지 시각 | `sites.name/address/timezone`, 추가 `latitude/longitude`. 대기 건수는 `change_requests.status`와 검토 이력으로 집계 | 관리자만 `sites`에 신규 매장과 위치 등록 | 지도·현재 시각 표시는 웹에서 처리. 가상 매장 통계 대신 현장별 조회 API 사용 |
| 카메라 등록·대표 사진 | `locations.site_id`, 추가 `camera_provider/camera_external_id/credential_ref` | `files`에 사진 메타데이터, `daily_records.photo_file_id/observation_date/captured_at`에 대표 사진 연결 | OxBlue 수집, 현지 날짜별 대표 사진 선정, 실제 사진 파일 저장. 인증 원문은 비밀 저장소에 보관 |
| 공정표 업로드·작업 추출 | `files.id/kind`, `schedules.version`, `schedule_sources.file_id` | `files`, `schedules`, `schedule_sources`, `schedule_tasks.source_task_id/week/start/finish` | 파일 바이트 업로드, 추출 하네스 실행, 원본과 대조, 본사 확인 후 등록 |
| 간트 조회·작업 분류 | `schedules.id/version`, `schedule_tasks.start/finish/effective_start/effective_finish/category_codes/processing_route` | 분류·처리 경로는 `schedule_tasks`, 변경 이력은 `task_events` | 주차와 기간이 겹치는 작업 조회. 원본 일정과 적용 일정 분리. 작업 분류와 AI 관측 가능 여부는 따로 확인 |
| OxBlue 분석·작업별 관측 | `daily_records.photo_file_id/result_file_id`, `model_versions.weights_file_id/config_file_id`, `schedule_task_locations` | `task_observations.task_id/daily_record_id/target_key/view_key/revision/installed_ratio/status/hold_reason`, 오버레이 `files` | Vision 추론과 후처리. 분석 대상을 작업에 명시적으로 연결. 인식 불가와 0% 구분 |
| 현장 증빙·재제출 | `schedule_tasks.id`, `task_events.event_type/target_event_id`로 이전 검토 확인 | `task_events`에 제출 건 추가, `task_event_files`와 `files`에 사진 연결 | 업로드와 파일 검증. 재제출은 새 이력으로 보관. 현장 제출 API와 사진 다운로드 인가 |
| 본사 검토·완료 확인 | `task_events`·`task_event_files`로 증빙 확인, `task_observations`로 AI 결과 확인 | `task_events.target_event_id` 또는 `observation_id`와 결정·사유 기록 | 승인 권한 확인. AI 관측 수락과 작업 완료 확인을 서로 다른 상태로 처리 |
| 일정·작업 변경 요청·대화 | `change_requests`의 요청·발신·수신 회사·상태, `task_events.request_id`로 대화, `task_event_files`로 첨부 조회 | `change_requests`에 요청, `task_events`에 메시지·결정·일정 적용 이력, 파일 연결. 적용할 때 `schedule_tasks.effective_start/effective_finish` 갱신 | 현장·회사 권한 확인, 수신 측만 결정, 대기 요청을 조건부 변경. 승인과 실제 일정 적용을 별도 단계로 기록 |
| 검토 필요 배지·알림 | `change_requests.status`와 `task_events`에서 현장별 미처리 건 집계 | 1차에는 기존 요청·검토 이력을 사용 | 웹 배지 API. 사용자별 읽음·발송·재시도가 필요하면 알림 테이블을 별도로 설계 |
| 주간 보고서 | 원본은 `weekly_reports.report_file_id/metadata_file_id`. 초안 근거는 `schedule_tasks`, `task_events`, `task_observations`, `change_requests` | 생성본은 `agent_reports.period_start/period_finish/revision/status/report_file_id/provenance`, 생성 파일은 `files` | 주차 범위 조회, AI 초안, 본사 검토, Word 출력. 이어진 작업만 지난주와 비교 |
| 챗봇·질문 보관 | 권한이 있는 `schedule_tasks`, `task_events`, `task_observations`, `agent_reports` | `question_archives.site_id/title/asked_at/archive_file_id`와 질문·답변 Markdown 파일 | 챗봇이 권한 있는 API로 근거를 조회하고 답변·출처를 연결. 003은 질문 보관용이며 실시간 채팅 세션이 필요하면 별도 설계 |

## 구현 순서

1. **인증·파일 권한·현장·간트 조회.** 사용자 역할과 현장 범위가 먼저 필요하다. API 응답에서 인증 식별자·파일 경로·카메라 인증 참조를 제외하고, 서로 다른 매장의 데이터와 첨부를 열 수 없는지 확인한다.
2. **현장 제출·본사 검토·변경 요청.** 요청 ID와 제출 ID를 분리해 이력이 섞이지 않게 하고, 두 사용자가 동시에 수락해도 결정이 한 번만 남는지 검사한다.
3. **카메라·Vision.** 사진 파일과 관측값을 실제 작업에 연결한다. 사진 누락·가림·분석 실패와 정상 관측을 구분한다.
4. **보고서·챗봇.** 주차·현장 범위에 맞는 근거만 사용하고, 원본 보고서와 생성·승인본을 별도로 보관한다.

ERD의 테이블과 외래 키는 005 적용 후 실제 구조다. 사용자 인증·RLS·컬럼 권한과 정규 업무 API 연결에는 추가 구현·검증이 필요하다.
