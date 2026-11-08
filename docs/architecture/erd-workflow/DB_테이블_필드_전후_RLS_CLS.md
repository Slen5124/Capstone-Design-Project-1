# ALOHA DB 전·후 필드 트리와 접근 권한

현재 운영 public 스키마는 **테이블 20개와 필드 186개**다. 2026년 11월 9일 005 통합 적용과 실제 스키마 일치를 확인했다. **전**은 이전 구조, **후**는 현재 통합 구조다. `+`는 통합 버전에서 추가한 필드다.

## 이전 기준 구조

```text
example_db/public [운영 조회 기록]
├─ sites
│  ├─ id
│  ├─ code
│  ├─ name
│  ├─ address
│  ├─ timezone
│  ├─ description
│  └─ created_at
├─ locations
│  ├─ id
│  ├─ site_id
│  ├─ code
│  ├─ name
│  ├─ description
│  └─ created_at
├─ files
│  ├─ id
│  ├─ relative_path
│  ├─ kind
│  ├─ size_bytes
│  ├─ sha256
│  ├─ status
│  └─ created_at
├─ schedules
│  ├─ id
│  ├─ site_id
│  ├─ version
│  ├─ document_date
│  ├─ description
│  └─ created_at
├─ schedule_sources
│  ├─ id
│  ├─ schedule_id
│  ├─ file_id
│  ├─ display_order
│  └─ description
├─ schedule_tasks
│  ├─ id
│  ├─ schedule_id
│  ├─ schedule_source_id
│  ├─ source_task_id
│  ├─ display_order
│  ├─ week
│  ├─ task_name
│  ├─ start
│  ├─ finish
│  ├─ predecessor_id
│  ├─ successor_id
│  └─ surface_type
├─ schedule_task_locations
│  ├─ site_id
│  ├─ schedule_id
│  ├─ task_id
│  └─ location_id
├─ daily_records
│  ├─ id
│  ├─ site_id
│  ├─ location_id
│  ├─ observation_date
│  ├─ captured_at
│  ├─ photo_file_id
│  ├─ model_version_id
│  ├─ result_file_id
│  ├─ changes_file_id
│  ├─ report_file_id
│  ├─ previous_record_id
│  ├─ schedule_id
│  ├─ comparison_rule_version
│  ├─ status
│  ├─ description
│  ├─ error_message
│  ├─ created_at
│  └─ updated_at
├─ weekly_reports
│  ├─ id
│  ├─ site_id
│  ├─ schedule_id
│  ├─ week
│  ├─ period_start
│  ├─ period_finish
│  ├─ report_file_id
│  ├─ metadata_file_id
│  ├─ description
│  └─ created_at
├─ gt_datasets
│  ├─ id
│  ├─ name
│  ├─ version
│  ├─ archive_file_id
│  ├─ manifest_file_id
│  ├─ label_schema_version
│  ├─ preprocessing_tool
│  ├─ review_status
│  ├─ description
│  └─ created_at
└─ model_versions
   ├─ id
   ├─ model_type
   ├─ version
   ├─ backbone
   ├─ gt_dataset_id
   ├─ weights_file_id
   ├─ config_file_id
   ├─ classes_file_id
   ├─ metrics_file_id
   ├─ trained_at
   ├─ code_version
   ├─ library_versions
   ├─ description
   └─ created_at
```

## 현재 통합 구조 20개

기존 필드도 모두 표시했다. 각 줄의 설명은 해당 필드를 읽거나 쓰는 기능이다. 기존 테이블의 필드에 붙은 `+`는 확장 이유를, 신규 테이블의 필드는 해당 기능에서의 저장 목적을 나타낸다.

```text
example_db/public [통합 적용]
├─ sites [필드 추가]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ code · 조회 코드 · URL 또는 외부 시스템 연동
│  ├─ name · 표시명 · 목록 및 상세 화면
│  ├─ address · 매장 상세에서 주소 표시
│  ├─ timezone · 현지 날짜·시각 표시, 대표 사진과 보고 주차의 기준
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  ├─ created_at · 등록 시각 · 최신순 조회와 이력 추적
│  ├─ + latitude · 추가 이유 · 주소 재검색 없이 지도 핀의 위도 확정
│  └─ + longitude · 추가 이유 · 주소 재검색 없이 지도 핀의 경도 확정
├─ locations [필드 추가]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ code · 조회 코드 · URL 또는 외부 시스템 연동
│  ├─ name · 표시명 · 목록 및 상세 화면
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  ├─ created_at · 등록 시각 · 최신순 조회와 이력 추적
│  ├─ + camera_provider · 추가 이유 · OxBlue 등 공급자에 맞는 수집 경로 선택
│  ├─ + camera_external_id · 추가 이유 · 공급자에서 정확한 카메라·촬영 지점 조회
│  └─ + credential_ref · 추가 이유 · 카메라 수집기가 비밀 저장소의 인증 정보를 찾는 참조값
├─ files [필드 추가]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ relative_path · 파일 서비스 내부에서 바이트 위치 확인 · 브라우저 응답에서 제외
│  ├─ kind · PDF·사진·JSON·첨부 유형별 검증과 조회
│  ├─ size_bytes · 업로드 용량 제한과 등록 후 파일 검증
│  ├─ sha256 · 파일 무결성 확인 및 동일 원본 판별
│  ├─ status · 업로드 준비·검증·실패 상태
│  ├─ created_at · 등록 시각 · 최신순 조회와 이력 추적
│  ├─ + site_id · 추가 이유 · 파일 소유 매장을 지정해 다른 매장으로 연결·다운로드되는 것을 방지
│  ├─ + visibility · 추가 이유 · 현장 공유 파일과 모델·학습자료 등 내부 파일 구분
│  └─ + uploaded_by · 추가 이유 · 검증·연결 전 업로드의 접근자와 등록 이력 확인
├─ schedules [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ version · 간트 버전 선택과 보고서·이력의 기준 버전 고정
│  ├─ document_date · 공정표 원본 문서 날짜 표시
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  └─ created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ schedule_sources [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ file_id · 해당 버전의 원본 공정표 PDF 조회
│  ├─ display_order · 원본 순서 재현 · 목록 정렬
│  └─ description · 화면에 표시할 설명 또는 운영 메모
├─ schedule_tasks [필드 추가]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ schedule_source_id · 작업이 추출된 원본 PDF 식별
│  ├─ source_task_id · PDF에 표시된 작업 번호 · 대조용. DB 관계는 id 사용
│  ├─ display_order · 원본 순서 재현 · 목록 정렬
│  ├─ week · 원본 주차 표기 보존 · 여러 주에 걸친 작업은 날짜 범위로 조회
│  ├─ task_name · 간트·작업 검토·주간 보고서에 작업명 표시
│  ├─ start · 간트의 원본 계획 시작일 · 변경 비교 기준
│  ├─ finish · 간트의 원본 계획 종료일 · 변경 비교 기준
│  ├─ predecessor_id · 선행 작업 표시와 기존 선후 관계 검증
│  ├─ successor_id · 후행 작업 표시와 기존 선후 관계 검증
│  ├─ surface_type · 기존 벽·바닥·천장 태그 조회 호환
│  ├─ + category_codes · 추가 이유 · 벽+설비처럼 작업에 복수 분류를 부여
│  ├─ + processing_route · 추가 이유 · 분류와 별도로 AI 관측·현장 증빙·보류 경로 확정
│  ├─ + effective_start · 추가 이유 · 적용된 시작일을 간트에서 원본과 함께 조회
│  └─ + effective_finish · 추가 이유 · 적용된 종료일로 간트·지연·이월을 계산
├─ schedule_task_locations [재사용]
│  ├─ site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ task_id · 작업 UUID 연결 · 작업별 상세와 이력 조회
│  └─ location_id · 작업에 관련된 촬영 위치 조회 · 작업별 관측 매핑의 후보 범위
├─ daily_records [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ location_id · 대표 사진을 찍은 고정 위치 식별
│  ├─ observation_date · 현지 날짜 기준 하루 대표 사진 조회
│  ├─ captured_at · 실제 촬영 시각과 시간대 표시
│  ├─ photo_file_id · 대표 원본 사진 열기
│  ├─ model_version_id · 사진 분석에 사용한 모델 버전 추적
│  ├─ result_file_id · Vision 원본 결과 JSON 조회
│  ├─ changes_file_id · 이전 관측과의 변화 JSON 조회
│  ├─ report_file_id · 기존 일일 관측 Markdown 조회
│  ├─ previous_record_id · 같은 위치의 비교 대상 일일 기록 연결
│  ├─ schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ comparison_rule_version · 변화 계산에 사용한 비교 규칙 재현
│  ├─ status · 처리 상태 · 목록 필터와 허용 작업 판정
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  ├─ error_message · 수집·분석 실패 원인 추적 · 운영자용
│  ├─ created_at · 등록 시각 · 최신순 조회와 이력 추적
│  └─ updated_at · 처리 상태가 마지막으로 바뀐 시각
├─ weekly_reports [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ week · 주차 표기와 필터. 기간이 겹치는 작업 조회는 일정으로 계산
│  ├─ period_start · 집계·보고 기간 시작일
│  ├─ period_finish · 집계·보고 기간 종료일
│  ├─ report_file_id · 제공받은 원본 주간 PDF 다운로드
│  ├─ metadata_file_id · 원본 주간 보고서의 메타데이터 JSON 조회 · 기존 연결 유지
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  └─ created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ gt_datasets [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ name · 표시명 · 목록 및 상세 화면
│  ├─ version · 버전 선택과 과거 데이터 재현
│  ├─ archive_file_id · 학습 데이터 ZIP 보관 및 승인된 운영자 다운로드
│  ├─ manifest_file_id · 데이터 구성·분할·라벨 목록 조회
│  ├─ label_schema_version · 라벨 정의와 버전 확인
│  ├─ preprocessing_tool · 전처리 환경 재현
│  ├─ review_status · 데이터 검수·승인 상태
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  └─ created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ model_versions [재사용]
│  ├─ id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ model_type · 추론 모델 계열 선택
│  ├─ version · 버전 선택과 과거 데이터 재현
│  ├─ backbone · 모델 구조 확인과 실행 재현
│  ├─ gt_dataset_id · 학습에 사용한 데이터셋 출처 추적
│  ├─ weights_file_id · 추론에 필요한 모델 가중치 로드
│  ├─ config_file_id · 모델 실행 설정 로드
│  ├─ classes_file_id · 모델 출력 클래스 해석
│  ├─ metrics_file_id · 성능 결과 조회
│  ├─ trained_at · 학습 기준 시각 확인
│  ├─ code_version · 실행에 필요한 코드 버전 추적
│  ├─ library_versions · 실행에 필요한 라이브러리 버전 추적
│  ├─ description · 화면에 표시할 설명 또는 운영 메모
│  └─ created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ question_archives [적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + title · 현장별 질문 보관 목록에 표시
│  ├─ + asked_at · 질문 날짜 검색과 정렬
│  ├─ + archive_file_id · 질문·답변·근거 Markdown 열기
│  ├─ + description · 화면에 표시할 설명 또는 운영 메모
│  └─ + created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ organizations [통합 적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + name · 표시명 · 목록 및 상세 화면
│  └─ + organization_type · 본사·시공사·협력사 구분과 관리 화면 표시
├─ users [통합 적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + auth_provider · 로그인 발급 기관 구분 · 인증 전용
│  ├─ + auth_subject · 인증 기관의 계정을 내부 사용자와 연결 · 인증 전용
│  ├─ + display_name · 요청 대화·증빙·검토 담당자 이름 표시
│  └─ + is_active · 비활성 계정의 업무 접근 차단
├─ site_memberships [통합 적용]
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + user_id · 활성 소속 조회 · 사용자별 현장 접근 권한 판단
│  ├─ + organization_id · 현장 안에서 본사·현장 회사 및 요청 당사자 확인
│  ├─ + role · 현장 단위 열람·등록·검토·승인 권한 구분
│  └─ + is_active · 종료된 현장 권한 회수
├─ change_requests [통합 적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ + task_id · 작업 UUID 연결 · 작업별 상세와 이력 조회
│  ├─ + requester_id · 요청 작성자 표시와 추적
│  ├─ + from_organization_id · 발신 회사 식별 · 요청과 대화 접근 판단
│  ├─ + to_organization_id · 수신 회사 식별 · 결정할 수 있는 담당자 판단
│  ├─ + request_type · 일정 변경·작업 변경에 맞는 입력 화면과 검증
│  ├─ + proposed_start · 현재 일정과 비교할 제안 시작일
│  ├─ + proposed_finish · 현재 일정과 비교할 제안 종료일
│  ├─ + proposed_work · 변경하려는 작업 범위나 시공 내용
│  ├─ + reason · 요청 상세와 검토에 사용할 변경 사유
│  ├─ + status · 처리 상태 · 목록 필터와 허용 작업 판정
│  └─ + created_at · 등록 시각 · 최신순 조회와 이력 추적
├─ task_events [통합 적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ + task_id · 작업 UUID 연결 · 작업별 상세와 이력 조회
│  ├─ + actor_id · 제출·댓글·검토·승인 행위자 표시 및 추적
│  ├─ + event_type · 제출·재제출·검토·대화·결정·일정 적용을 구분
│  ├─ + occurred_at · 작업 이력·대화의 시간순과 주간 보고 범위
│  ├─ + request_id · 해당 요청의 대화·결정·적용에 연결 · 다른 요청과 분리
│  ├─ + target_event_id · 반려나 확인이 어느 제출 건에 대한 것인지 연결
│  ├─ + observation_id · AI 결과 수락·반려의 대상 관측 버전 연결
│  ├─ + body · 대화 본문 또는 담당자가 남긴 작업 내용
│  └─ + details · 이벤트별 부가정보 · 반려 사유, 제출 차수, 변경 전후 값
├─ task_event_files [통합 적용]
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + event_id · 어느 제출·대화의 첨부인지 연결하고 부모 권한 확인
│  ├─ + file_id · 파일 ID 연결 · 권한 검사 후 파일 조회
│  └─ + sort_order · 한 이벤트에 첨부한 파일의 화면 표시 순서
├─ task_observations [통합 적용]
│  ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
│  ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
│  ├─ + schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
│  ├─ + task_id · 작업 UUID 연결 · 작업별 상세와 이력 조회
│  ├─ + daily_record_id · 근거가 된 대표 원본 사진·분석 JSON 연결
│  ├─ + target_key · 벽면 등 관측 대상 식별 · 작업에 명시적으로 연결
│  ├─ + view_key · 시야별 결과 식별과 비교
│  ├─ + revision · 같은 데이터의 수정본 순서 · 덮어쓰기 없이 보존
│  ├─ + installed_ratio · 관측 가능한 영역의 설치 비율 · 작업 검토와 보고서 표시
│  ├─ + status · 처리 상태 · 목록 필터와 허용 작업 판정
│  ├─ + hold_reason · 가림·시야 부족·판단 불가 사유 표시
│  ├─ + overlay_file_id · 원본과 분석 오버레이 비교
│  └─ + created_at · 등록 시각 · 최신순 조회와 이력 추적
└─ agent_reports [통합 적용]
   ├─ + id · 고유 키 · 상세 조회와 다른 테이블의 연결 기준
   ├─ + site_id · 매장 연결 · 현장 범위 조회와 RLS 검사
   ├─ + schedule_id · 공정표 버전 연결 · 간트와 보고서 범위 지정
   ├─ + period_start · 집계·보고 기간 시작일
   ├─ + period_finish · 집계·보고 기간 종료일
   ├─ + revision · 같은 데이터의 수정본 순서 · 덮어쓰기 없이 보존
   ├─ + status · 처리 상태 · 목록 필터와 허용 작업 판정
   ├─ + report_file_id · 생성 보고서 파일 미리보기·다운로드
   ├─ + generated_at · 생성본 정렬과 버전 확인
   ├─ + approved_by · 본사 승인자 표시와 추적
   ├─ + approved_at · 승인본 여부와 승인 시각 표시
   └─ + provenance · 생성 당시 사용한 작업·관측 revision·증빙과 모델 버전 확인
```


[상세 ERD](ALOHA_ERD_업무흐름.html)에서 테이블을 선택하면 각 필드의 사용처를 확인할 수 있다.

## 필드와 연결 규칙

| 영역 | 필드의 역할과 제약 |
|---|---|
| 매장 분리 | `sites.id`를 매장 경계로 사용한다. 기존 `sites.code`는 URL·표시용으로 유지한다. 회사·현장 권한은 `site_memberships`에 둔다. 같은 사람이 여러 현장이나 회사에서 맡는 역할을 별도 행으로 보관한다. `(site_id, user_id, organization_id, role)` 중복을 제한한다. |
| 작업 ID | 원본 PDF의 `source_task_id`는 표시와 원본 추적에 사용한다. 새 참조는 기존 `schedule_tasks.id`를 쓴다. 새 작업 데이터에는 `(site_id, schedule_id) → schedules(site_id,id)`와 `(schedule_id, task_id) → schedule_tasks(schedule_id,id)`를 묶어 다른 현장·공정표 작업 연결을 차단한다. |
| 일정과 분류 | `start/finish`는 원본, `effective_start/effective_finish`는 적용한 변경 일정이다. 유효 일정이 없으면 원본을 사용한다. 기존 `surface_type`은 호환용으로 유지하고, `category_codes`와 `processing_route`는 별도로 저장한다. |
| 요청과 이력 | `change_requests`는 변경안과 현재 상태를 갖는다. `task_events`는 증빙 제출·재제출·검토·요청 메시지·수락·반려·일정 적용을 행 유형으로 구분한다. `request_id`는 해당 작업·현장 소속이어야 하며 `target_event_id`는 검토한 제출 건을 가리킨다. AI 결과 검토는 `observation_id`로 연결한다. |
| 동시 결정 | 대기 상태인 요청만 조건부 UPDATE하고 결정 이벤트를 같은 트랜잭션에서 추가한다. 요청당 최종 결정 1개를 부분 UNIQUE로 강제한다. 일정 적용 시 유효 일정과 전후 이벤트도 함께 저장한다. |
| 파일 | `files.site_id`는 현장 파일의 소유 현장, `visibility`는 `site/internal`을 구분한다. 현장 파일의 `site_id`는 필수다. `uploaded_by`는 신규 업로드의 등록자 ID이며, 기존 파일은 확인 전 NULL로 둔다. `task_event_files`에는 같은 현장의 이벤트·파일만 연결되도록 복합 FK를 둔다. 기존 파일 42건은 출처를 확인해 현장 소유를 부여하고, 매핑 전에는 내부 범위에 둔다. |
| 관측 | 대표 사진과 원본 분석 JSON은 `daily_records`에 연결하고 작업별 수치와 수정본은 `task_observations`에 보관한다. 같은 현장·작업·일일 기록·대상·시야·revision의 중복을 막는다. `installed_ratio`는 0~1이며 가림 등으로 관측 불가한 상태는 NULL과 사유를 보존한다. |
| 보고서 | `weekly_reports`는 원본 PDF와 기존 `metadata_file_id`를 유지한다. 재생성·승인되는 문서는 `agent_reports`에서 공정표 버전·기간·revision으로 관리한다. `provenance`에는 생성 당시 근거 ID와 모델·프롬프트 버전을 담는다. |

## RLS · 어떤 행에 접근할 수 있는지

| 적용 위치 | 정책 |
|---|---|
| `sites` | 로그인 사용자의 활성 `site_memberships.site_id`와 일치하는 매장만 조회한다. 관리자 등록·변경은 별도 운영 경로에서 처리한다. |
| `locations`, `schedules`, `schedule_task_locations`, `daily_records`, `weekly_reports`, `question_archives`, `task_observations`, `agent_reports` | 각 행의 `site_id`로 검사한다. 조회·등록·수정은 각각 역할별 정책을 둔다. 분석·보고 에이전트에는 작업을 배정받은 현장과 결과 등록 범위만 부여한다. |
| `schedule_sources`, `schedule_tasks` | `schedule_id → schedules.site_id`로 검사한다. 기존 두 테이블의 현장 ID를 중복 저장할 필요가 없다. |
| `change_requests`, `task_events`, `task_event_files` | 현장 소속을 확인한다. 요청의 경우 해당 현장의 발신·수신 회사 또는 해당 요청에 대한 권한을 부여받은 본사 관리자로 제한한다. 요청 이벤트와 첨부는 부모 요청의 범위를 상속한다. 수락·반려는 수신 측의 허용된 담당자만 가능하다. |
| `files` | 현장 범위와 참조 대상 권한을 함께 확인한다. 같은 현장에 속해도 권한이 없는 요청의 첨부는 UUID만으로 열 수 없다. 새 첨부는 업로더만 접근할 수 있는 준비 상태에서 검증한 후 연결한다. 권한 없는 사용자의 파일 다운로드·URL 발급은 서버에서 거부한다. |
| `users`, `organizations`, `site_memberships` | 앱 사용자는 본인 정보와 본인의 소속을 기본으로 조회한다. 작업 화면에 필요한 타인·회사 표시명은 권한을 검사한 목록 API로 제공한다. 멤버십 수정은 관리자 경로로 분리한다. 멤버십 정책이 자기 테이블을 재귀 조회하지 않도록 한다. |
| `gt_datasets`, `model_versions`, 내부 파일 | 사용자용 DB 역할에는 조회 권한을 부여하지 않는다. 분석 서비스와 운영자에만 필요한 범위로 권한을 둔다. |

FastAPI가 인증한 사용자 ID를 트랜잭션 범위로 DB에 전달한다. 트랜잭션과 조회는 같은 커넥션에서 실행한다. 브라우저가 보낸 `user_id`·`site_id`는 인증 근거로 사용할 수 없다. 사용자 컨텍스트가 없으면 기본 거부한다. 이 방식은 신뢰된 서버가 매개변수화된 쿼리만 실행한다는 전제를 둔다. 브라우저와 에이전트에 DB 연결이나 임의 SQL 실행 권한을 부여하지 않는다. 트랜잭션이 끝나면 컨텍스트가 사라지도록 설정해 연결 풀에서 다음 요청으로 이어지지 않게 한다. [PostgreSQL 트랜잭션 범위 설정](https://www.postgresql.org/docs/16/functions-admin.html)

마이그레이션 소유 역할과 실행 역할을 분리한다. 실행 역할은 테이블 소유자나 `BYPASSRLS` 역할의 권한을 가지지 않는다. 쓰기 정책에는 `WITH CHECK`를 적용해 다른 현장으로 행을 만들거나 이동시키는 요청도 차단한다. [PostgreSQL 16 RLS](https://www.postgresql.org/docs/16/ddl-rowsecurity.html)

## CLS · 어떤 필드를 읽거나 바꿀 수 있는지

PostgreSQL에서는 컬럼 단위 `GRANT`와 서버 응답 필드 제한을 함께 사용한다. API·분석·운영의 DB 역할을 구분하고, 제한할 테이블에 넓은 테이블 단위 권한이 남아 있는지 확인한다. API가 공용 DB 역할을 쓰는 경우 본사·현장 사용자 간 응답 필드 차이는 API에서 추가로 제한한다. [PostgreSQL 16 GRANT](https://www.postgresql.org/docs/16/sql-grant.html)

| 필드 | 제한 방식 |
|---|---|
| `users.auth_provider`, `users.auth_subject` | 인증 처리 전용. 공사 화면은 사용자 ID·표시명만 받는다. |
| `locations.credential_ref` | 카메라 수집 서비스와 운영자만 읽는다. 카메라 토큰 원문은 비밀 저장소에 둔다. |
| `files.relative_path`, `files.sha256` | 저장·검증 역할만 읽는다. 브라우저는 권한 확인을 거친 파일 다운로드 API를 쓴다. |
| `daily_records.error_message`, `task_events.details`, `agent_reports.provenance` | 운영자 또는 해당 기능의 처리 역할에만 원본을 제공한다. 일반 응답은 허용한 상태·사유·근거 필드만 골라 보낸다. |
| `change_requests.status`, `schedule_tasks.effective_start/effective_finish`, `agent_reports.approved_by/approved_at` | 일반 수정 API와 역할에서 임의 변경을 막는다. 검증된 수락·적용·승인 작업에서만 서버가 기록한다. |

기존 백엔드에는 SQLAlchemy의 테이블 전체 조회와 전체 행 응답이 있다. 컬럼 권한을 적용하기 전에 해당 조회를 명시적 컬럼 목록과 응답 모델로 바꾼다. 그대로 적용하면 제한된 컬럼을 포함한 쿼리가 권한 오류로 실패할 수 있다.

## 효율성과 매장 추가 기준

- 매장은 테이블을 복제하지 않고 `sites`, `site_memberships`, `locations`에 행을 추가한다. 카메라가 여러 대라면 카메라별 촬영 위치를 우선 사용한다.
- 새 인덱스는 실제 화면 조회와 RLS 정책부터 맞춘다. 멤버십 `(user_id, site_id)`, 작업 이력 `(site_id, task_id, occurred_at DESC)`, 요청 대화 `(request_id, occurred_at, id)`, 요청 대기 목록 `(site_id, status, created_at DESC)`을 우선 검토한다. 기존 공정표와 관측 인덱스는 재사용한다.
- 요청·첨부·관측의 관계는 FK로 유지한다. `details`와 `provenance`는 가변 부가정보와 생성 당시 근거에만 사용한다. 필요한 이력은 기간·페이지 제한으로 읽는다.
- 적용 순서는 소유 현장 확인과 백필, 조회 컬럼 명시, 실행 역할과 API 인증 도입, RLS·컬럼 권한 적용, 복제 DB 검증이다. 매장 A와 B 교차 조회·수정, 같은 매장의 다른 회사 요청 첨부 다운로드, 컨텍스트가 없는 쿼리, 재사용 연결의 컨텍스트 초기화, 동시 수락을 검증한다.

기존 스키마 구조와 후속 필드·정책 설계를 정리한 자료다. 20개 테이블 통합은 운영에 적용했다. 사용자별 RLS·컬럼 권한은 인증된 사용자 컨텍스트와 신규 API 연결에 맞춰 추가 적용해야 한다.
