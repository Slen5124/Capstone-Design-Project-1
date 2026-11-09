# ALOHA API 개발·연결 안내

2026-11-09 신규 구현. 기존 **20개 테이블**을 직접 사용한다. 테이블·컬럼 추가, AWS 변경, 배포는 하지 않았다.

## 이번에 만들어진 것

- 인증된 매장·공정표·작업·주간 현장 목록 조회
- 벽·바닥·천장 AI 관측 등록과 탐지 불가 시 현장 확인 자동 이관
- 현장 사진 업로드, 기본 100% 진행률 보고, 부분 진행 사유, 재제출·검토 이력
- 본사 관측 수락, 증빙 수락·반려, 사진으로 확인하는 작업 완료
- 양사 간 일정·추가 기간·작업 변경 요청, 파일 첨부, 대화, 수신 회사의 결정과 적용
- 주간 보고서 입력 조회, 입력 고정, Agent 작업 점유·결과 등록, 본사 최종 승인
- 모바일 현장 화면과 선택해서 사용하는 실제 API 연결 모드

API는 실제 SQL을 실행한다. 테스트용 JSON을 반환하는 모의 서버가 아니다. 운영 자격증명과 DB를 임의로 연결하지 않았으므로 현재 시연 사이트의 데이터가 운영 DB로 전송되지는 않는다.

## 파일과 개발 책임

```text
src/backend/
├── project_app.py       새 FastAPI 진입점, 인증 설정, CORS, 공통 오류
├── project_api.py       매장·간트·현장 제출·본사 검토·Vision HTTP 경로
├── project_models.py    입력 형식, 퍼센트·사유·날짜·첨부 검증
├── project_service.py   권한·매장 범위, DB 조회·공통 이벤트, 주간 자료 조합
├── project_changes.py   회사 간 요청·대화·원자적 결정과 적용
├── project_files.py     파일 바이트 검사·등록·인가된 다운로드
└── project_reports.py   고정된 보고서 입력과 Agent 작업·결과·승인

prototype/aloha-web/src/
├── project-api.js       FE에서 재사용하는 HTTP 클라이언트
├── pages/field-api.js   실제 API 연결용 모바일 현장 화면
├── pages/field-adapter.js  브라우저 저장 기반 시연 화면
└── field-manager.css    PC에서도 한 열의 모바일 화면으로 표시

docs/api/
├── README.md           실행·연결 순서와 기능 설명
├── ENDPOINTS.md        생성된 전체 경로 목록
├── PROJECT_API.openapi.json  Swagger·타입 생성용 명세
└── DB_CONNECTIONS.md   기능별 읽기·쓰기, 새로 저장하는 자료
```

## 실행

새 서비스는 기존 `main:app`과 구분한 `project_app:create_app`이다. 기존 수집용 API에는 미인증 경로가 있으므로 새 서비스의 외부 프록시에 함께 연결하지 않는다.

1. 기존 requirements를 설치한다. DB는 20개 canonical 테이블과 기존 005·006의 제약이 적용된 환경을 준비한다. 운영에 마이그레이션을 다시 실행하지 않는다.
2. 전달용 `api/db.py`는 `ALO_DATABASE_URL`을 환경 변수에서 읽는다. 개인별 테스트 DB 연결 값은 안전한 별도 경로로 제공받는다.
3. `ALO_STORAGE_ROOT`를 실제 파일 저장 경로로 설정한다. DB 파일 행만 복사하고 파일 바이트를 옮기지 않으면 증빙 검증이 실패한다.
4. `ALO_API_IDENTITIES`에 서버가 신뢰하는 사용자·회사 및 서비스 인증 매핑을 JSON으로 설정한다. 예시는 아래를 따른다.
5. FE가 다른 포트라면 `ALO_API_ALLOWED_ORIGINS=http://127.0.0.1:4335`처럼 **정확한 FE 출처**를 설정한다. 여러 출처는 쉼표로 구분한다. `*`는 거부한다.
6. 프로젝트 루트에서 실행한다.

```powershell
python -m uvicorn project_app:create_app --factory --app-dir api --host 127.0.0.1 --port 8001
```

- 상태 확인 `/health`
- Swagger `/docs`의 Authorize에 사용자 토큰 입력
- OpenAPI `/openapi.json`
- 모바일 시연 `http://127.0.0.1:4335/02-field-submission.html`
- 실제 API 연결 `http://127.0.0.1:4335/02-field-submission.html?mode=api`

FE는 API 서버 주소와 **현장 사용자 토큰**으로 연결한 뒤 매장·공정표·조회 주를 선택한다. 조회 주를 비우면 매장 현지 날짜의 이번 주를 사용한다. 과거 시연 자료는 월요일 `2025-02-17`처럼 날짜를 선택한다.

## 인증 매핑

개발 연결용 불투명 Bearer 토큰 방식이다. 비밀번호 로그인·SSO 발급 서버를 구현한 것은 아니다. 토큰을 가진 사용자를 서버의 사용자·회사에 고정하고, 요청마다 DB에서 사용자 활성 여부와 해당 매장의 활성 소속·역할을 확인한다.

다음은 형식 설명용이다. `<...>`를 실제 UUID와 SHA-256으로 교체하고 서버 비밀 설정에만 둔다.

```json
[
  {"token_sha256":"<현장 사용자 토큰의 SHA-256>","user_id":"<users.id>","organization_id":"<현장 회사 organizations.id>"},
  {"token_sha256":"<본사 사용자 토큰의 SHA-256>","user_id":"<users.id>","organization_id":"<본사 회사 organizations.id>"},
  {"token_sha256":"<Vision 전용 토큰의 SHA-256>","kind":"vision","site_ids":["<허용 sites.id>"]},
  {"token_sha256":"<보고서 Agent 전용 토큰의 SHA-256>","kind":"agent","site_ids":["<허용 sites.id>"]}
]
```

- 원문 토큰은 충분히 긴 무작위 값으로 별도 발급·보관한다. 코드·Git·URL·로그에 넣지 않는다.
- SHA-256은 Python의 `hashlib.sha256(token.encode()).hexdigest()`로 계산한다.
- 사용자·회사·매장 소속은 관리자가 사전 등록한다. 본문에서 `actor_id`나 역할을 지정하면 거부한다.
- 인증을 설정하지 않으면 새 API는 503으로 차단한다. 잘못된 토큰은 401, 매장·역할 위반은 403이다.
- 실제 연결 FE는 토큰을 현재 페이지 메모리에만 둔다. 새로고침하면 다시 연결한다.
- 여러 회사에 소속된 사용자는 해당 회사에 바인딩된 인증값으로 접속한다.

## 현장 보고 흐름

```text
FE 이번 주 목록
└── GET field-home
    └── BE가 소속·공정표·현지 주간 범위를 확인
        ├── schedule_tasks에서 이번 주와 겹치는 작업 조회
        └── task_events·task_observations에서 상태·확인 요청·최신 보고 조합

현장 촬영·진행률 선택
├── POST files → 파일 저장소 + files
└── POST submissions → task_events + task_event_files
    ├── 진행률 생략 시 100%
    ├── 0~100% 직접 설정, 100% 미만이면 남은 작업 사유 필수
    ├── 첫 제출 / 재제출을 구분하고 모든 버전 보관
    └── observation_id로 자동 이관된 벽·시야를 명시적으로 연결

본사 검토
└── POST reviews
    ├── accept → 증빙 또는 AI 관측을 수락
    ├── reject → 반려 사유 필수
    └── complete → 최신 100% 제출의 실제 사진 무결성 확인 후 완료
```

100%는 현장 보고 값이다. 본사 완료 확인과 별개다. AI 관측에 `complete`를 보내거나 75% 증빙을 완료 처리하면 거부한다. 과거 제출을 검토하려 하면 409로 최신 자료 재조회를 요구한다.

## Vision 연결

1. 기존 사진 수집·분석 흐름에서 매장 일일 사진을 `daily_records`에 등록한다. 이 신규 API는 카메라 로그인·다운로드·모델 실행을 직접 수행하지 않는다.
2. `vision-input`으로 이번 주 AI 대상 작업과 연결된 촬영 위치의 일일 사진 ID를 조회한다.
3. Vision이 `installed / (installed + uninstalled)`로 비율을 계산한다. 가림 등 인식 불가 영역은 분모에서 제외한다.
4. 관측 성공은 `status=observed`, `installed_ratio=0..1`로 등록한다. 전체 판단이 불가능하면 `status=unobserved`, 비율은 `null`, `hold_reason`을 보낸다.
5. BE는 관측과 현장 확인 요청을 **같은 트랜잭션**에 저장한다. `failed`도 현장 확인 요청으로 연결한다. `held`는 대기 상태로 보존하며 자동 이관하지 않는다.
6. 같은 관측 ID·내용 재전송은 중복 생성하지 않는다. 같은 대상·시야·날짜의 새 결과는 증가한 revision을 사용한다. 새 관측으로 대체된 이전 이관은 현재 할 일에서 제외하고 이력은 보존한다.

사진이 같은 매장이더라도 작업에 연결되지 않은 촬영 위치이면 거부한다. 현장 보고의 진행률은 0~100%, Vision 비율은 0~1이다.

## 회사 간 조정

- 현장→본사, 본사→현장 모두 요청 가능하다. 상대 회사는 이 매장에 활성 HQ 또는 현장 담당자가 있어야 한다.
- `extension`은 현재 종료일보다 늦은 날짜, `schedule`은 변경 날짜, `work`는 변경할 작업 내용을 받는다.
- 생성 시 화면에서 본 적용 시작·종료일을 함께 보낸다. 그사이 일정이 바뀌면 409이다.
- 요청별 메시지와 파일을 공통 이벤트에 남긴다. 요청용 파일은 업로더와 요청 참여 담당자에게만 제공한다.
- 수신 회사만 결정한다. 수락·반영은 한 트랜잭션이다. 기준 작업·일정이 달라졌다면 승인 이력도 남기지 않고 취소한다.
- 원본 `start`, `finish`, `task_name`은 보존한다. 날짜는 effective 필드, 작업 변경 내용은 적용 이벤트와 `effective_work` 응답으로 표현한다.
- 모바일 실제 연결 모드에서도 조정 요청·첨부·대화·수락·반려를 사용할 수 있다.

## 주간 보고서와 Agent

1. 본사가 `POST reports`로 생성 작업을 접수한다. 당시 입력 자료, 진행률·사진·검토 상태, 비교 자료, 다음 주 계획과 입력 해시를 고정한다.
2. 이번 주 처음 시작한 작업에는 지난주 설명을 만들지 않는다. 계속 작업의 지난주 자료는 이전 주 종료 시점까지의 제출·관측으로 제한한다. 이후에 승인된 일정 변경도 과거 주의 다음 주 계획에 끼워 넣지 않는다.
3. Agent는 `GET report-jobs` → `POST claim` → `GET input` 순서로 가져간다. 점유 시간은 15분이며 동시 점유는 409이다. 만료 뒤 새 점유 ID로 다시 작업한다.
4. 외부 LLM·보고서 생성기가 고정 입력으로 PDF 또는 DOCX를 만든다. `purpose=report`로 파일을 업로드하고 `claim_id`, `input_digest`와 결과 파일 ID를 `POST result`에 보낸다. 실패도 사유를 기록한다.
5. 본사는 생성본을 검토한 뒤 승인한다. 미검토 작업·미해결 이관·변경된 입력이 있으면 승인하지 않는다. 같은 결과의 재전송은 중복 처리하지 않는다.

이번 구현은 **생성 작업의 DB 저장·중복 실행 차단·입력 전달·결과 접수**까지다. 실제 LLM/GPU 호출, 자동 폴링 워커, 문서 내용 검증기는 별도 실행 프로그램에서 이 계약을 호출해야 한다.

## 연결 완료 범위와 남은 일

| 영역 | 이번 결과 | 남은 연결 |
|---|---|---|
| 모바일 현장 FE | 시연 + 실제 API 연결, 사진·진행률·요청·대화 | 실기기 촬영·네트워크·레이아웃 QA |
| 지도·본사·간트·보고서 FE | 필요한 API와 JS 클라이언트 제공 | 기존 브라우저 저장 화면을 새 API 응답으로 전환 |
| BE↔DB | 실제 테이블 읽기·쓰기와 트랜잭션 구현 | 운영 계정 권한·PostgreSQL 제약을 복제 DB에서 통합 검증 |
| 인증 | 개별 사용자·회사 바인딩과 매장별 역할 확인 | 운영 로그인/SSO 발급·폐기·회수 |
| DB 보안 | 기존 FK/트리거 + 새 API의 매장/회사/역할/파일 인가 | 사용자별 RLS·컬럼 제한 정책의 실제 적용·검증 |
| Vision | 사진 입력 조회·관측 접수·자동 이관 | 카메라 수집·모델 실행기의 호출 연결 |
| 보고서 Agent | DB 작업·점유·고정 입력·결과·승인 | 실제 LLM·문서 생성 워커 |
| 운영 | 로컬 실행 코드·설정·명세 제공 | 서버 배포·TLS·프록시·백업·제한 설정 |

기존 수집 서비스와 새 API를 동시에 다른 경로로 공개하면 새 API의 접근 제한을 우회할 수 있다. 운영에서는 공개 진입점을 새 서비스로 한정하고 기존 서비스는 내부로 유지해야 한다.

## 검증 명령

```powershell
.venv-schedule/Scripts/python.exe -m pytest tests -q --basetemp=tmp/pytest-api-check
cd prototype/aloha-web
npm test
node build.cjs
```

Windows 제한 환경에서 FastAPI TestClient의 내부 socketpair가 막히면 로컬 테스트 프로세스에 필요한 실행 권한으로 검사한다. 테스트는 임시 SQLite를 사용하며 AWS에 접속하지 않는다. SQLite 검증은 PostgreSQL의 FK·트리거·행 잠금·RLS 실험을 대신하지 않는다. 컴퓨터 유즈와 브라우저 자동화는 사용하지 않았다.


## FE 코드에서 바로 연결하는 예

`project-api.js`를 로드하고 API가 반환한 UUID를 사용한다. 간트의 원본 번호 `45`를 task_id 대신 보내지 않는다.

```javascript
const api = new ProjectAPI({baseUrl: 'http://127.0.0.1:8001', token});
const home = await api.fieldHome(siteCode, scheduleId, '2025-02-17');
const task = home.tasks[0];
const uploaded = await api.upload(siteCode, photoFile);
await api.submit(siteCode, scheduleId, task.id, {
  id: crypto.randomUUID(),
  photo_file_ids: [uploaded.id],
  progress_percent: 75,
  captured_at: capturedDate.toISOString(),
  body: '잔여 보드 구간은 자재 이동 후 시공 예정',
  observation_id: task.handoffs[0]?.observation_id ?? null
});
```

응답을 받지 못한 재시도에는 같은 입력과 같은 제출 UUID를 사용한다. 다른 입력으로 새 보고를 작성하면 새 UUID를 사용한다. 업로드 성공 뒤 제출만 실패했으면 파일을 재업로드하지 않고 uploaded.id를 재사용한다.

본사 화면은 `tasks`, `taskDetail`, `classify`, `review`, `reports`, `createReport`, `decideReport`를, 요청 화면은 `changeRequests`, `createChange`, `requestDetail`, `message`, `decide`를 연결한다. 사진은 인증된 `api.file(siteCode, fileId)`의 Blob으로 표시하거나 내려받는다.

AI 분류에는 소문자 `wall/floor/ceiling`을 사용한다. 대표 표면이 없는 단일 표면 분류는 surface_type을 자동 설정하고, 여러 표면이면 대표 surface_type을 명시한다. Vision 서비스는 허용 매장의 사진만, 보고서 Agent는 해당 매장의 사진·보고서만 다운로드할 수 있다. 내부 시스템용으로 등록된 사진도 서비스가 사용할 수 있지만 회사 간 요청 첨부에는 접근하지 못한다.
