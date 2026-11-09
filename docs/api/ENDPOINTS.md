# API 경로 목록

생성 원본 `project_app:create_app`. 운영 DB 접속 없이 OpenAPI에서 생성한다.

`{site_code}`는 매장 코드, `{schedule_id}`·`{task_id}`는 DB UUID다. 화면의 원본 간트 번호와 구분한다.

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/api/v1/me` | 현재 사용자와 매장별 권한 |
| GET | `/api/v1/sites` | 세계지도에 표시할 권한 있는 매장 |
| GET | `/api/v1/sites/{site_code}/schedules` | 매장의 공정표 버전 |
| GET | `/api/v1/sites/{site_code}/overview` | 선택 매장 요약·현지 시간·전체 및 주간 현황 |
| GET | `/api/v1/sites/{site_code}/participants` | 변경 요청을 주고받을 상대 회사 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/field-home` | 현장 관리자 이번 주 작업과 우선 확인 항목 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/vision-input` | Vision 연결용 AI 대상 작업과 일일 증거 사진 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks` | 간트 작업과 검토 상태 조회 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}` | 선택한 작업의 현장 보고와 AI 관측 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/history` | 작업 제출·본사 검토·자동 이관 이력 |
| PATCH | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/classification` | 본사 작업 분류와 확인 경로 설정 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/submissions` | 사진과 진행률 보고·재제출 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/reviews` | 본사 수락·반려·완료 확인 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/observations` | Vision 관측 등록과 현장 확인 자동 이관 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/change-requests` | 양사 간 변경 요청과 답변 대기 목록 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/tasks/{task_id}/change-requests` | 일정·기간·작업 변경 요청과 첨부 등록 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/change-requests/{request_id}` | 변경 요청 내용·첨부·주고받은 대화 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/change-requests/{request_id}/messages` | 상대 회사에 답변과 파일 첨부 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/change-requests/{request_id}/decision` | 수신 회사 결정과 작업·일정 적용을 한 번에 처리 |
| POST | `/api/v1/sites/{site_code}/files` | 파일 바이트 업로드·해시 검증·DB 등록 |
| GET | `/api/v1/sites/{site_code}/files/{file_id}` | 매장·요청 참여 권한을 확인하고 파일 다운로드 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/report-input` | 이번 주 비교·다음 주 작업과 보고서 준비 상태 |
| POST | `/api/v1/sites/{site_code}/schedules/{schedule_id}/reports` | 주간 보고서 생성 작업 접수·입력 고정 |
| GET | `/api/v1/sites/{site_code}/schedules/{schedule_id}/reports` | 주차별 생성 보고서와 최신 자료 변경 여부 |
| GET | `/api/v1/sites/{site_code}/report-jobs` | Agent가 가져갈 생성 대기 작업 |
| GET | `/api/v1/sites/{site_code}/report-jobs/{report_id}/input` | Agent 전용 고정 입력과 근거 자료 |
| POST | `/api/v1/sites/{site_code}/report-jobs/{report_id}/claim` | Agent 작업 15분 점유·중복 실행 방지 |
| POST | `/api/v1/sites/{site_code}/report-jobs/{report_id}/result` | Agent 생성 결과·실패 사유 등록 |
| POST | `/api/v1/sites/{site_code}/reports/{report_id}/decision` | 본사 최종 보고서 승인·반려 |
| GET | `/health` | Health |

인증된 업무 API **29개**, 별도 상태 확인 API 1개.

추가 입력·응답·오류 형식은 `PROJECT_API.openapi.json` 또는 실행 서버의 `/docs`를 확인한다.

재생성 `python src/backend/export_project_api.py`
