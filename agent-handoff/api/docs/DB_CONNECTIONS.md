# 기능별 API ↔ BE ↔ DB 연결

새 테이블·컬럼은 추가하지 않는다. **20개 테이블 / 기존 필드 구조**를 유지하며 새 업무 값은 기존 JSON 필드의 명시된 키에 저장한다. 저장소에 실제 파일 바이트를 두고 DB에는 참조·무결성 정보를 둔다.

## 업무별 연결표

| 기능·화면 | BE 담당 | 읽는 테이블·필드 | 새로 쓰는 자료 |
|---|---|---|---|
| 인증·내 권한 | project_service, project_api | users.is_active, site_memberships의 site_id/user_id/organization_id/role/is_active | 없음. 사용자·회사·소속은 사전 등록 |
| 지도·선택 매장 | project_api | sites 이름·좌표·시간대, schedules, 작업·이벤트·수신 요청 | 없음 |
| 간트·이번 주 현장 홈 | project_service | schedule_tasks.start/finish/effective_start/effective_finish/processing_route/category_codes, task_events, task_observations | 없음 |
| 본사 작업 분류 | project_api | 소속 role=hq, schedule_tasks | category_codes, processing_route, surface_type 갱신 + classification_change 이벤트 |
| 사진·문서 업로드 | project_files | sites, 권한, files 중복 ID | 저장소 바이트 + files.id/site_id/kind/relative_path/size_bytes/sha256/status/visibility/uploaded_by |
| AI 입력 사진 | project_api | schedule_tasks.processing_route, schedule_task_locations, daily_records | 없음. 카메라 credential_ref는 반환하지 않음 |
| AI 관측 결과 | project_api | 일일 사진·작업·촬영 위치 연결·기존 revision | task_observations의 대상·시야·수정본·비율·상태·사유 |
| 탐지 불가 자동 이관 | project_api | 위 관측 | task_events의 workflow_state + observation_id + details.kind=field_handoff |
| 사진·진행률 보고 | project_api | 작업, 실제 files 무결성, 이전 제출 | task_events의 submission/resubmission + task_event_files |
| 본사 수락·반려·완료 | project_api | 최신 제출 또는 현재 관측, HQ 권한, 실제 사진 바이트 | review_accept/review_reject/completion_confirm 이벤트. 이전 제출은 보존 |
| 조정 요청 생성 | project_changes | 양사 매장 소속, 현재 적용 일정·작업, files | change_requests + 요청 개시 message 이벤트 + 첨부 연결 |
| 요청 대화·첨부 | project_changes | 요청의 발·수신 회사, 파일 권한 | message 이벤트의 request_id/body + task_event_files |
| 요청 수락·반영 | project_changes | 기존 요청·기준 해시·현재 일정 | 결정 이벤트 + 적용 이벤트 + request.status + 작업 effective 날짜. 한 트랜잭션 |
| 작업 범위 변경 | project_changes | request.proposed_work, 원본 task_name | work_apply.details.effective_work. 원본 작업명 보존 |
| 보고서 생성 접수 | project_reports | 위 작업·사진·관측·검토·일정 이력 | agent_reports에 기간·revision·pending·고정 입력·해시 |
| Agent 점유·결과 | project_reports | pending 작업·점유 기한·입력 해시·보고서 files | provenance.claim/result_digest/error, status, report_file_id |
| 최종 보고서 승인 | project_reports | 최신 입력 해시·작업 검토 상태·파일 무결성 | status/approved_by/approved_at + provenance 결정 사유·중복 키 |

## 새로 저장하는 값

```text
files [기존 테이블]
├── site_id               파일 소유 매장. NULL인 종전 파일을 자동 채택하지 않음
├── relative_path         서버가 정한 UUID 기반 경로. 화면 응답에 숨김
├── sha256 / size_bytes   내용 검증. 다운로드·증빙 등록·완료 확인에 사용
├── visibility            site=매장 사진 / request=회사 간 첨부
└── uploaded_by           인증된 업로더. 서비스 Agent 업로드는 NULL

task_observations [기존 테이블]
├── target_key / view_key / revision   벽·시야·수정본 구분
├── installed_ratio       AI 값 0~1. 관측 불가 시 NULL
├── status / hold_reason  관측 불가와 실제 0%를 구분
└── daily_record_id       근거가 된 일일 사진 기록

task_events [기존 테이블]
├── event_type            제출 / 검토 / 메시지 / 결정 / 적용 / 시스템 이관
├── observation_id        자동 이관과 현장 후속 보고가 같은 관측을 가리킴
├── target_event_id       본사에서 검토한 특정 제출 버전
├── request_id            특정 회사 간 요청의 대화·결정·적용
├── actor_id              인증에서 정한 사용자. 본문 입력으로 지정 불가
└── details [기존 JSONB에 새 키 저장]
    ├── progress_percent  현장 값 0~100. 생략 입력의 서버 기본값 100
    ├── captured_at       시간대 포함 실제 촬영 시각
    ├── attempt           제출 차수
    ├── payload_digest    동일 ID 재전송의 내용 확인. 화면 응답에는 숨김
    ├── sequence          같은 시각에 생긴 이벤트의 작업 내 순서
    ├── kind=field_handoff, target_key, view_key, status=requested
    │                     AI 탐지 불가 자동 확인 요청
    ├── kind=request_opened, baseline_digest
    │                     변경 요청 당시 기준 일정·작업
    ├── before_start / before_finish / after_start / after_finish
    │                     일정 적용 전후. 과거 주 재현에 사용
    └── effective_work    수락·적용된 작업 범위

task_event_files [기존 테이블]
└── site_id / event_id / file_id / sort_order
                          제출·메시지별 첨부와 순서

change_requests [기존 테이블]
├── from_organization_id / to_organization_id
├── request_type          extension / schedule / work
├── proposed_start / proposed_finish / proposed_work / reason
└── status                pending → applied 또는 rejected
                          accepted는 같은 트랜잭션 안의 적용 전 단계

schedule_tasks [기존 테이블]
├── start / finish / task_name         원본 보존
├── effective_start / effective_finish 적용 날짜
└── category_codes / processing_route 작업 분류·확인 경로
                          DB 분류 코드는 wall 등 소문자

agent_reports [기존 테이블]
├── period_start / period_finish / revision
├── status                pending → validated 또는 failed → 본사 approved
├── report_file_id / approved_by / approved_at
└── provenance [기존 JSONB에 새 키 저장]
    ├── kind=project_api_report
    ├── input / input_digest / requested_by  당시 입력과 생성 요청자
    ├── claim.id / claim.owner / claim.expires_at  15분 작업 점유
    ├── result_digest / error             결과 재전송 검증·실패 사유
    └── decision_digest / decided_by / decision_reason  최종 검토
```

## 조회 상태는 별도 테이블에 중복 저장하지 않음

이번 주 홈의 상태는 최신 제출, 그 제출을 대상으로 한 검토, 현재 관측 수정본과 미해결 이관에서 계산한다. 완료·검토 대기·보완 필요 카운터를 별도 컬럼에 복사하지 않는다. 사진 제출만으로 `confirmed`를 만들지 않는다.

자동 이관 처리 상태도 요청 행을 덮어쓰지 않고 후속 제출의 observation_id와 현재 관측을 비교한다. 사진·관측의 과거 버전과 반려 사유가 남는다.

## 트랜잭션·중복·범위

- 변경하는 API는 해당 sites 행을 잠근다. 같은 매장 내 제출 차수·최신 검토·결정·revision 경쟁을 직렬화한다.
- UUID와 입력 해시로 재전송을 구분한다. 같은 ID의 변경된 내용은 409로 거부한다.
- 관측과 자동 이관, 제출과 파일 연결, 요청과 초기 첨부, 요청 결정과 실제 적용은 각각 한 트랜잭션이다.
- 파일 업로드와 후속 제출은 별도 단계다. 제출 실패 시 업로드한 파일 ID를 재사용한다. 미연결 파일 자동 삭제 정책은 구현하지 않았다.
- 회사 간 파일은 실제 요청 참여 여부로 내려준다. 파일의 매장만 같다고 모든 첨부를 제공하지 않는다.
- `site_id → schedule_id → task_id`, 관측의 촬영 위치, 첨부의 매장, 이벤트 검토 대상까지 확인한다.
- 파일 저장소 내부 경로, auth_subject, 인증 매핑, 카메라 credential_ref는 일반 응답에 넣지 않는다.

## 기존 구조와 운영 연결 시 확인할 부분

- 새 API는 task_events의 정규 제출을 사용한다. 종전 workflow_manager_evidence 시스템 JSON을 정규 제출로 자동 변환하지 않는다. 필요한 이전 자료는 별도의 검증된 이관으로 연결해야 한다.
- 카메라 수집, daily_records 생성, GT·모델 등록, 간트 추출·최초 등록은 기존 내부 기능이다. 신규 사용자용 API로 재작성한 범위가 아니다.
- weekly_reports는 기존 원본 보고서 구조로 유지한다. 신규 Agent 생성본과 승인본은 agent_reports를 사용한다.
- 매장 생성, 사용자·회사·소속 초기 등록, 카메라 자격정보 등록은 관리자의 사전 프로비저닝 영역이다. 현장 사용자에게 권한 확대 API를 제공하지 않았다.
- 기존 005의 복합 FK·검토 권한·요청 단일 결정/적용 트리거와 새 API의 검증을 함께 사용한다. 새 RLS 정책이나 사용자별 DB 접속 역할은 이번에 적용하지 않았다.
- JSON 키의 형식 검사는 Pydantic·BE에서 수행한다. 다른 DB 작성 경로까지 같은 입력 검증을 보장하려면 그 경로도 새 계약을 사용하거나 DB 제약을 추가해야 한다.
- 로컬 테스트는 SQLite다. PostgreSQL의 잠금·제약·권한과 실제 기존 자료를 포함한 복제 DB 검증 후 운영에 연결한다.
