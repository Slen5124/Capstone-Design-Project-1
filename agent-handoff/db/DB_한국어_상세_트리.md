# DB 한국어 상세 트리

2026년 11월 9일 운영 public 스키마를 읽기 전용으로 확인했다. 저장 테이블은 **20개**, 필드는 **186개**이며 뷰는 **0개**다. BE는 실제 테이블을 직접 조회·저장한다.

한국어 이름 뒤의 영문은 실제 테이블·필드명이다. 필수는 DB에 NULL을 저장할 수 없다는 뜻이며 기본값이 있으면 입력에서 생략할 수 있다. PK가 여러 필드에 표시된 경우 함께 사용하는 복합 기본키다. FK는 관계와 제약 항목에서 전체 연결 필드를 확인한다.

UUID는 식별자, TEXT·VARCHAR는 문자, DATE는 날짜, TIMESTAMPTZ는 시간대 정보를 처리하는 시각, JSON·JSONB는 구조화 자료다. TEXT[]는 문자 목록이다.

## 저장 테이블

```text
공사 관리 DB / public
├── 매장과 담당자
│   ├── 매장 / sites · 9개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 관리 코드 (code) [TEXT | 필수]
│   │   ├── 이름 (name) [TEXT | 필수]
│   │   ├── 주소 (address) [TEXT | 선택]
│   │   ├── 현지 시간대 (timezone) [TEXT | 필수] — IANA 시간대 이름으로 현지 날짜와 보고 주차를 계산
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 위도 (latitude) [NUMERIC(8,5) | 선택]
│   │   ├── 경도 (longitude) [NUMERIC(8,5) | 선택]
│   │   └── 관계와 제약
│   │       └── 중복 금지 (code)
│   ├── 회사 / organizations · 3개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 이름 (name) [TEXT | 필수]
│   │   └── 회사 구분 (organization_type) [TEXT | 필수 | 기본 'partner']
│   ├── 사용자 / users · 5개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 인증 제공자 (auth_provider) [TEXT | 필수 | 기본 'local'] — 동일한 식별값을 가진 다른 인증 기관의 계정을 구분
│   │   ├── 인증 기관의 사용자 식별값 (auth_subject) [TEXT | 필수] — 인증용 내부 값으로 일반 화면 응답에서 제한할 대상
│   │   ├── 표시 이름 (display_name) [TEXT | 필수]
│   │   ├── 활성 여부 (is_active) [BOOLEAN | 필수 | 기본 true]
│   │   └── 관계와 제약
│   │       └── 중복 금지 (auth_provider, auth_subject)
│   └── 매장별 소속과 권한 / site_memberships · 5개 필드
│       ├── 매장 ID (site_id) [UUID | 필수 | PK | FK]
│       ├── 사용자 ID (user_id) [UUID | 필수 | PK | FK]
│       ├── 회사 ID (organization_id) [UUID | 필수 | PK | FK]
│       ├── 매장 안에서의 역할 (role) [TEXT | 필수] — 허용값 hq / field / viewer
│       ├── 활성 여부 (is_active) [BOOLEAN | 필수 | 기본 true]
│       └── 관계와 제약
│           ├── FK (organization_id) → 회사 / organizations (id)
│           ├── FK (site_id) → 매장 / sites (id)
│           └── FK (user_id) → 사용자 / users (id)
├── 공정표와 작업
│   ├── 공정표 버전 / schedules · 6개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 버전 (version) [TEXT | 필수]
│   │   ├── 원본 문서 날짜 (document_date) [DATE | 선택]
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   └── 관계와 제약
│   │       ├── FK (site_id) → 매장 / sites (id)
│   │       ├── 중복 금지 (site_id, id)
│   │       └── 중복 금지 (site_id, version)
│   ├── 공정표 원본 / schedule_sources · 5개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│   │   ├── 파일 ID (file_id) [UUID | 필수 | FK]
│   │   ├── 표시 순서 (display_order) [INTEGER | 필수]
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   └── 관계와 제약
│   │       ├── FK (file_id) → 공통 파일 / files (id)
│   │       ├── 중복 금지 (schedule_id, file_id)
│   │       ├── FK (schedule_id) → 공정표 버전 / schedules (id)
│   │       └── 중복 금지 (schedule_id, id)
│   ├── 공정표 작업 / schedule_tasks · 16개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│   │   ├── 공정표 원본 ID (schedule_source_id) [UUID | 필수 | FK]
│   │   ├── 원본 간트의 작업 번호 (source_task_id) [TEXT | 선택] — 화면의 작업 번호이며 DB 관계 연결에는 id 사용
│   │   ├── 표시 순서 (display_order) [INTEGER | 필수]
│   │   ├── 주차 표기 (week) [TEXT | 선택]
│   │   ├── 작업명 (task_name) [TEXT | 필수]
│   │   ├── 원본 계획 시작일 (start) [DATE | 선택]
│   │   ├── 원본 계획 종료일 (finish) [DATE | 선택]
│   │   ├── 선행 작업 ID (predecessor_id) [UUID | 선택 | FK]
│   │   ├── 후행 작업 ID (successor_id) [UUID | 선택 | FK]
│   │   ├── 대표 표면 분류 (surface_type) [TEXT | 선택] — 허용값 floor / ceiling / wall
│   │   ├── 작업 분류 코드 목록 (category_codes) [TEXT[] | 필수 | 기본 빈 목록]
│   │   ├── 작업 확인 경로 (processing_route) [TEXT | 선택] — ai는 AI 관측, field는 현장 증빙, manual_check는 담당자 확인 · 허용값 ai / field / manual_check
│   │   ├── 적용 시작일 (effective_start) [DATE | 선택] — 값이 없으면 원본 start 사용
│   │   ├── 적용 종료일 (effective_finish) [DATE | 선택] — 값이 없으면 원본 finish 사용
│   │   └── 관계와 제약
│   │       ├── FK (schedule_id) → 공정표 버전 / schedules (id)
│   │       ├── 중복 금지 (schedule_id, id)
│   │       ├── FK (schedule_id, predecessor_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│   │       ├── 중복 금지 (schedule_id, predecessor_id) DEFERRABLE INITIALLY DEFERRED
│   │       ├── FK (schedule_id, schedule_source_id) → 공정표 원본 / schedule_sources (schedule_id, id)
│   │       ├── FK (schedule_id, successor_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│   │       ├── 중복 금지 (schedule_id, successor_id) DEFERRABLE INITIALLY DEFERRED
│   │       └── 중복 금지 (schedule_source_id, source_task_id)
│   └── 작업과 촬영 위치 연결 / schedule_task_locations · 4개 필드
│       ├── 매장 ID (site_id) [UUID | 필수 | FK]
│       ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│       ├── 작업 ID (task_id) [UUID | 필수 | PK | FK]
│       ├── 촬영 위치 ID (location_id) [UUID | 필수 | PK | FK]
│       └── 관계와 제약
│           ├── FK (schedule_id, task_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│           ├── FK (site_id, location_id) → 촬영 위치와 카메라 / locations (site_id, id)
│           └── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
├── 파일과 사진 관측
│   ├── 공통 파일 / files · 10개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 저장소 내부 상대 경로 (relative_path) [TEXT | 필수] — 파일 서비스 내부에서 사용하며 화면에는 인가된 다운로드 경로 제공
│   │   ├── 파일 종류 (kind) [TEXT | 필수]
│   │   ├── 파일 크기 바이트 (size_bytes) [BIGINT | 선택]
│   │   ├── 파일 무결성 해시 (sha256) [TEXT | 선택] — 파일 내용이 등록 당시와 같은지 검증
│   │   ├── 처리 상태 (status) [TEXT | 필수 | 기본 'pending'] — 허용값 pending / ready / failed
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 매장 ID (site_id) [UUID | 선택 | FK] — 기존 소유 미확정 파일은 비어 있을 수 있어 업무 연결 전 소유 범위 확인
│   │   ├── 파일 공개 범위 구분 (visibility) [TEXT | 필수 | 기본 'internal'] — 허용값 internal / site / request
│   │   ├── 파일 등록 사용자 ID (uploaded_by) [UUID | 선택 | FK]
│   │   └── 관계와 제약
│   │       ├── 중복 금지 (relative_path)
│   │       ├── FK (site_id) → 매장 / sites (id)
│   │       ├── 중복 금지 (site_id, id)
│   │       └── FK (uploaded_by) → 사용자 / users (id)
│   ├── 촬영 위치와 카메라 / locations · 9개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 관리 코드 (code) [TEXT | 필수]
│   │   ├── 이름 (name) [TEXT | 필수]
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 카메라 공급자 (camera_provider) [TEXT | 선택]
│   │   ├── 외부 카메라 식별자 (camera_external_id) [TEXT | 선택]
│   │   ├── 비밀 저장소의 카메라 인증 참조 (credential_ref) [TEXT | 선택] — 인증 원문을 넣지 않고 비밀 저장소의 참조값 사용
│   │   └── 관계와 제약
│   │       ├── 중복 금지 (site_id, code)
│   │       ├── FK (site_id) → 매장 / sites (id)
│   │       └── 중복 금지 (site_id, id)
│   ├── 일별 대표 사진과 분석 / daily_records · 18개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 촬영 위치 ID (location_id) [UUID | 필수 | FK]
│   │   ├── 현지 관측 날짜 (observation_date) [DATE | 필수]
│   │   ├── 사진 촬영 시각 (captured_at) [TIMESTAMPTZ | 필수]
│   │   ├── 대표 원본 사진 파일 ID (photo_file_id) [UUID | 필수 | FK]
│   │   ├── 사용한 모델 버전 ID (model_version_id) [UUID | 선택 | FK]
│   │   ├── 결과 파일 ID (result_file_id) [UUID | 선택 | FK]
│   │   ├── 변화 비교 결과 파일 ID (changes_file_id) [UUID | 선택 | FK]
│   │   ├── 보고서 파일 ID (report_file_id) [UUID | 선택 | FK]
│   │   ├── 비교할 이전 일별 기록 ID (previous_record_id) [UUID | 선택 | FK]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 선택 | FK]
│   │   ├── 변화 비교 규칙 버전 (comparison_rule_version) [TEXT | 선택]
│   │   ├── 처리 상태 (status) [TEXT | 필수 | 기본 'pending'] — 허용값 pending / processing / completed / failed
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   ├── 내부 오류 내용 (error_message) [TEXT | 선택]
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 마지막 갱신 시각 (updated_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   └── 관계와 제약
│   │       ├── FK (changes_file_id) → 공통 파일 / files (id)
│   │       ├── 중복 금지 (location_id, id)
│   │       ├── 중복 금지 (location_id, observation_date)
│   │       ├── FK (location_id, previous_record_id) → 일별 대표 사진과 분석 / daily_records (location_id, id)
│   │       ├── FK (model_version_id) → 분석 모델 버전 / model_versions (id)
│   │       ├── FK (photo_file_id) → 공통 파일 / files (id)
│   │       ├── FK (report_file_id) → 공통 파일 / files (id)
│   │       ├── FK (result_file_id) → 공통 파일 / files (id)
│   │       ├── 중복 금지 (site_id, id)
│   │       ├── FK (site_id, location_id) → 촬영 위치와 카메라 / locations (site_id, id)
│   │       └── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│   └── 작업별 AI 관측 / task_observations · 13개 필드
│       ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│       ├── 매장 ID (site_id) [UUID | 필수 | FK]
│       ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│       ├── 작업 ID (task_id) [UUID | 필수 | FK]
│       ├── 근거 일별 기록 ID (daily_record_id) [UUID | 필수 | FK]
│       ├── 관측 대상 식별값 (target_key) [TEXT | 필수]
│       ├── 촬영 시야 식별값 (view_key) [TEXT | 필수]
│       ├── 수정본 번호 (revision) [INTEGER | 필수 | 기본 1] — 동일 대상·시야·근거의 수정본 순서
│       ├── 관측 가능한 영역의 설치 비율 (installed_ratio) [NUMERIC | 선택] — 0~1 저장이며 화면 퍼센트는 100을 곱해 표시
│       ├── 처리 상태 (status) [TEXT | 필수] — observed는 관측, unobserved는 미관측, held는 판단 보류, failed는 실패 · 허용값 observed / unobserved / held / failed
│       ├── 관측 불가 또는 보류 사유 (hold_reason) [TEXT | 선택]
│       ├── 분석 표시 이미지 파일 ID (overlay_file_id) [UUID | 선택 | FK]
│       ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│       └── 관계와 제약
│           ├── FK (schedule_id, task_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│           ├── FK (site_id, daily_record_id) → 일별 대표 사진과 분석 / daily_records (site_id, id)
│           ├── FK (site_id, overlay_file_id) → 공통 파일 / files (site_id, id)
│           ├── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│           ├── 중복 금지 (site_id, schedule_id, task_id, id)
│           └── 중복 금지 (task_id, daily_record_id, target_key, view_key, revision)
├── 요청과 검토 이력
│   ├── 일정 및 작업 변경 요청 / change_requests · 14개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│   │   ├── 작업 ID (task_id) [UUID | 필수 | FK]
│   │   ├── 변경 요청 작성자 ID (requester_id) [UUID | 필수 | FK]
│   │   ├── 발신 회사 ID (from_organization_id) [UUID | 필수 | FK]
│   │   ├── 수신 회사 ID (to_organization_id) [UUID | 필수 | FK]
│   │   ├── 변경 요청 종류 (request_type) [TEXT | 필수] — 허용값 schedule / extension / work
│   │   ├── 제안 시작일 (proposed_start) [DATE | 선택]
│   │   ├── 제안 종료일 (proposed_finish) [DATE | 선택]
│   │   ├── 제안 작업 내용 (proposed_work) [TEXT | 선택]
│   │   ├── 요청 또는 결정 사유 (reason) [TEXT | 필수]
│   │   ├── 처리 상태 (status) [TEXT | 필수 | 기본 'pending'] — 허용값 pending / accepted / rejected / applied
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   └── 관계와 제약
│   │       ├── FK (schedule_id, task_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│   │       ├── FK (site_id, requester_id, from_organization_id) → 매장별 소속과 권한 / site_memberships (site_id, user_id, organization_id)
│   │       ├── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│   │       ├── 중복 금지 (site_id, schedule_id, task_id, id)
│   │       └── FK (to_organization_id) → 회사 / organizations (id)
│   ├── 공통 업무 이력 / task_events · 12개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 선택 | FK] — 일반 업무 이벤트는 필수
│   │   ├── 작업 ID (task_id) [UUID | 선택 | FK] — 일반 업무 이벤트는 필수
│   │   ├── 행위자 ID (actor_id) [UUID | 선택 | FK] — 일반 업무 이벤트는 필수이며 시스템 workflow_state는 비어 있을 수 있음
│   │   ├── 업무 이력 종류 (event_type) [TEXT | 필수] — 허용값 submission / resubmission / review_accept / review_reject / completion_confirm / message / request_accept / request_reject / schedule_apply / work_apply / classification_change / workflow_state
│   │   ├── 행위 발생 시각 (occurred_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 연결할 변경 요청 ID (request_id) [UUID | 선택 | FK]
│   │   ├── 대상 제출 또는 이력 ID (target_event_id) [UUID | 선택 | FK]
│   │   ├── 대상 AI 관측 ID (observation_id) [UUID | 선택 | FK]
│   │   ├── 대화 본문 또는 검토 의견 (body) [TEXT | 선택]
│   │   ├── 이벤트별 부가정보 JSON (details) [JSONB | 필수 | 기본 빈 객체] — 업무별 부가정보와 시스템 실행 상태의 relation·key·row 보관
│   │   └── 관계와 제약
│   │       ├── FK (actor_id) → 사용자 / users (id)
│   │       ├── FK (schedule_id, task_id) → 공정표 작업 / schedule_tasks (schedule_id, id)
│   │       ├── FK (site_id) → 매장 / sites (id)
│   │       ├── 중복 금지 (site_id, id)
│   │       ├── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│   │       ├── FK (site_id, schedule_id, task_id, observation_id) → 작업별 AI 관측 / task_observations (site_id, schedule_id, task_id, id)
│   │       ├── FK (site_id, schedule_id, task_id, request_id) → 일정 및 작업 변경 요청 / change_requests (site_id, schedule_id, task_id, id)
│   │       └── FK (site_id, target_event_id) → 공통 업무 이력 / task_events (site_id, id)
│   └── 업무 이력의 첨부 / task_event_files · 4개 필드
│       ├── 매장 ID (site_id) [UUID | 필수 | FK]
│       ├── 업무 이력 ID (event_id) [UUID | 필수 | PK | FK]
│       ├── 파일 ID (file_id) [UUID | 필수 | PK | FK]
│       ├── 첨부 정렬 순서 (sort_order) [INTEGER | 필수 | 기본 0]
│       └── 관계와 제약
│           ├── FK (site_id, event_id) → 공통 업무 이력 / task_events (site_id, id)
│           └── FK (site_id, file_id) → 공통 파일 / files (site_id, id)
├── 보고서와 질문
│   ├── 등록된 원본 주간 보고서 / weekly_reports · 10개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 선택 | FK]
│   │   ├── 주차 표기 (week) [TEXT | 필수]
│   │   ├── 보고 기간 시작일 (period_start) [DATE | 선택]
│   │   ├── 보고 기간 종료일 (period_finish) [DATE | 선택]
│   │   ├── 보고서 파일 ID (report_file_id) [UUID | 필수 | FK]
│   │   ├── 원본 보고서 메타데이터 파일 ID (metadata_file_id) [UUID | 필수 | FK]
│   │   ├── 설명 (description) [TEXT | 선택]
│   │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   └── 관계와 제약
│   │       ├── FK (metadata_file_id) → 공통 파일 / files (id)
│   │       ├── 중복 금지 (metadata_file_id)
│   │       ├── FK (report_file_id) → 공통 파일 / files (id)
│   │       ├── 중복 금지 (report_file_id)
│   │       ├── FK (site_id) → 매장 / sites (id)
│   │       ├── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│   │       └── 중복 금지 (site_id, week)
│   ├── AI 생성 보고서 / agent_reports · 12개 필드
│   │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│   │   ├── 매장 ID (site_id) [UUID | 필수 | FK]
│   │   ├── 공정표 버전 ID (schedule_id) [UUID | 필수 | FK]
│   │   ├── 보고 기간 시작일 (period_start) [DATE | 필수]
│   │   ├── 보고 기간 종료일 (period_finish) [DATE | 필수]
│   │   ├── 수정본 번호 (revision) [INTEGER | 필수 | 기본 1]
│   │   ├── 처리 상태 (status) [TEXT | 필수 | 기본 'pending'] — 허용값 pending / failed / validated / approved
│   │   ├── 보고서 파일 ID (report_file_id) [UUID | 선택 | FK]
│   │   ├── 보고서 생성 시각 (generated_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│   │   ├── 보고서 승인 사용자 ID (approved_by) [UUID | 선택 | FK]
│   │   ├── 승인 시각 (approved_at) [TIMESTAMPTZ | 선택]
│   │   ├── 생성에 사용한 근거와 모델 정보 JSON (provenance) [JSONB | 필수 | 기본 빈 객체] — 생성 당시 사용한 작업·관측 수정본·증빙과 모델 정보를 추적
│   │   └── 관계와 제약
│   │       ├── FK (approved_by) → 사용자 / users (id)
│   │       ├── FK (site_id, report_file_id) → 공통 파일 / files (site_id, id)
│   │       ├── FK (site_id, schedule_id) → 공정표 버전 / schedules (site_id, id)
│   │       └── 중복 금지 (site_id, schedule_id, period_start, period_finish, revision)
│   └── 질문과 답변 보관 / question_archives · 7개 필드
│       ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
│       ├── 매장 ID (site_id) [UUID | 필수 | FK]
│       ├── 질문 보관 제목 (title) [TEXT | 필수]
│       ├── 질문 시각 (asked_at) [TIMESTAMPTZ | 필수]
│       ├── 보관 파일 ID (archive_file_id) [UUID | 필수 | FK]
│       ├── 설명 (description) [TEXT | 선택]
│       ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
│       └── 관계와 제약
│           ├── FK (archive_file_id) → 공통 파일 / files (id)
│           ├── 중복 금지 (archive_file_id)
│           └── FK (site_id) → 매장 / sites (id)
└── 학습 데이터와 모델
    ├── 정답 데이터셋 / gt_datasets · 10개 필드
    │   ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
    │   ├── 이름 (name) [TEXT | 필수]
    │   ├── 버전 (version) [TEXT | 필수]
    │   ├── 정답 데이터 묶음 파일 ID (archive_file_id) [UUID | 필수 | FK]
    │   ├── 데이터 구성 명세 파일 ID (manifest_file_id) [UUID | 필수 | FK]
    │   ├── 정답 라벨 규격 버전 (label_schema_version) [TEXT | 필수]
    │   ├── 전처리 도구 정보 (preprocessing_tool) [TEXT | 필수 | 기본 'SAM3']
    │   ├── 정답 데이터 검수 상태 (review_status) [TEXT | 필수 | 기본 'draft'] — 허용값 draft / reviewed / approved
    │   ├── 설명 (description) [TEXT | 선택]
    │   ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
    │   └── 관계와 제약
    │       ├── FK (archive_file_id) → 공통 파일 / files (id)
    │       ├── FK (manifest_file_id) → 공통 파일 / files (id)
    │       └── 중복 금지 (name, version)
    └── 분석 모델 버전 / model_versions · 14개 필드
        ├── 고유 ID (id) [UUID | 필수 | PK | 기본 자동 UUID]
        ├── 모델 종류 (model_type) [TEXT | 필수] — 허용값 unet / mask-rcnn / deeplab-v3
        ├── 버전 (version) [TEXT | 필수]
        ├── 모델 백본 (backbone) [TEXT | 필수 | 기본 'ResNet-50']
        ├── 학습에 사용한 정답 데이터셋 ID (gt_dataset_id) [UUID | 선택 | FK]
        ├── 모델 가중치 파일 ID (weights_file_id) [UUID | 필수 | FK]
        ├── 모델 실행 설정 파일 ID (config_file_id) [UUID | 필수 | FK]
        ├── 클래스 정의 파일 ID (classes_file_id) [UUID | 필수 | FK]
        ├── 평가 지표 파일 ID (metrics_file_id) [UUID | 선택 | FK]
        ├── 학습 완료 시각 (trained_at) [TIMESTAMPTZ | 선택]
        ├── 실행 코드 버전 (code_version) [TEXT | 선택]
        ├── 실행 라이브러리 버전 정보 (library_versions) [JSONB | 필수 | 기본 빈 객체]
        ├── 설명 (description) [TEXT | 선택]
        ├── 등록 시각 (created_at) [TIMESTAMPTZ | 필수 | 기본 현재 시각]
        └── 관계와 제약
            ├── FK (classes_file_id) → 공통 파일 / files (id)
            ├── FK (config_file_id) → 공통 파일 / files (id)
            ├── FK (gt_dataset_id) → 정답 데이터셋 / gt_datasets (id)
            ├── FK (metrics_file_id) → 공통 파일 / files (id)
            ├── 중복 금지 (model_type, version)
            └── FK (weights_file_id) → 공통 파일 / files (id)
```

## 조회 구성

운영 DB에는 조회·호환 뷰가 없다. 필요한 검토 목록과 요청 결정 정보는 BE가 공통 테이블에서 조회해 구성한다.

```text
조회 구성
└── BE API → 공통 테이블 20개 직접 조회
```

## 함께 적용되는 업무 규칙

- 일반 task_events는 공정표·작업·행위자와 연결한다. 반려에는 사유를 남기고, 검토는 대상 제출 또는 관측 중 하나를 지정한다.
- 검토 시 활성 본사 역할을 확인한다. 요청 결정·적용 시 수신 회사의 활성 담당자인지 확인하고 결정·적용 이벤트를 요청당 한 번만 기록한다.
- 일정 적용 시 원본 start·finish는 보존하고 effective_start·effective_finish를 갱신한다. 연장 종료일은 현재 적용 종료일보다 뒤여야 한다.
- task_observations의 observed에는 설치 비율이 있어야 한다. 미관측·보류·실패는 비율을 비워 두고 사유를 기록한다.
- workflow_state는 시스템 실행 상태다. 일반 작업 이력 조회에서는 필요한 event_type을 지정해 구분한다. 실행 자료의 식별 키와 분석 전달의 주차별 revision에는 별도 유일 인덱스가 적용된다.
- agent_reports가 approved이면 승인자와 승인 시각이 함께 있어야 한다. 동일 매장·공정표·기간의 revision은 중복되지 않는다.
- 사용자별 RLS·컬럼 제한과 인증된 사용자 컨텍스트의 최종 연결은 추가 구현·검증 단계다. 내부 인증 정보·파일 경로가 일반 화면에 노출되지 않도록 API 응답 범위를 정한다.

검산 결과는 테이블 20개, 저장 필드 186개, 뷰 0개다. 실제 자료 값·접속 주소·비밀번호는 포함하지 않았다.
