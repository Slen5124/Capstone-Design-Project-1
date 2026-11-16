# 주간 보고서와 프로젝트 도우미

별도 시험 환경에서 보고서 PDF 생성과 Qwen 질문답변을 확인할 수 있습니다. API 담당자가 기존 PostgreSQL 테이블, 승인된 계정 및 파일 저장소를 먼저 준비합니다. 기존 API와 공개 시연 화면은 기존 실행 방법을 사용합니다.

## 시험 서버

1. 기존 [프로젝트 API 안내](README.md)에 따라 백엔드를 준비합니다. 보고서 워커에는 `src/llm_agent_module/weekly_reports/requirements.txt`도 설치합니다.
2. 활성화할 기능을 API 서비스 환경에 설정합니다. `ALO_ENABLE_WEEKLY_REPORTS=1`은 `/api/v1/weekly/` 보고서 API를 열고, `ALO_ENABLE_COPILOT=1`은 권한 있는 매장의 챗봇 API를 엽니다. 보고서와 챗봇을 모두 확인하려면 두 설정을 적용합니다.
3. 로컬 웹 화면을 4319번 포트에서 테스트할 때 API의 `ALO_API_ALLOWED_ORIGINS`에 `http://127.0.0.1:4319`를 허용합니다. 컴퓨터에서 API를 직접 띄울 때는 저장소 루트에서 `python -m uvicorn project_app:create_app --factory --app-dir src/backend --host 127.0.0.1 --port 8002`를 실행합니다.
4. 보고서 워커에 `ALO_PROJECT_API_BASE`와 테스트 매장 범위의 `ALO_PROJECT_AGENT_TOKEN`을 서비스 설정으로 제공합니다. `python src/llm_agent_module/weekly_reports/project_worker.py --once`로 대기 작업을 처리할 수 있습니다. `--schedule`을 추가하면 일요일 자동 생성 대상도 확인합니다. PDF에서 사용할 추가 한글 글꼴 파일은 필요하면 `REPORT_KOREAN_FONT`에 지정합니다.

새 보고서 및 이전 API의 보고서 작업은 별개의 생성 형식으로 기록됩니다. 신규 워커는 신규 API에서 접수한 작업을 가져옵니다. 파일 검증과 조회 권한은 기존 서비스 규칙을 사용합니다.

## Qwen 연결

시험 서버는 학교 GPU 게이트웨이 또는 팀이 승인한 Qwen 클라우드 서비스에 연결합니다. 팀원의 브라우저는 프로젝트 API를 사용합니다. 접속 키와 비밀 설정은 서비스 환경에서 관리합니다.

- 학교 GPU는 `ALO_COPILOT_PROVIDER=gpu`를 설정합니다. 서비스 설정에 `ALO_QWEN_GPU_HOST`, `ALO_QWEN_GPU_PORT`, `ALO_QWEN_GPU_USER`, `ALO_QWEN_GPU_KEY_PATH`, `ALO_QWEN_GPU_KNOWN_HOSTS`를 지정합니다. SSH 인증은 검증한 호스트 키와 전용 키를 사용하며, 원격 실행 명령은 `aloha-qwen-gateway`입니다.
- 게이트웨이 운영자는 `src/llm_agent_module/gpu_copilot/`의 스크립트를 전용 계정의 `~/.aloha-qwen-copilot/`에 배치합니다. 실제 GPU 파티션과 CUDA 환경은 Slurm 스크립트에서 설정합니다. 해당 폴더의 비공개 `config.json`에 `model_path`, `llama_server`, `api_key_file`, `activity_file`, `idle_seconds`를 채웁니다. SSH 공개 키는 게이트웨이를 실행하는 강제 명령으로 제한합니다.
- 클라우드 방식은 `ALO_COPILOT_PROVIDER=cloud`와 해당 워크스페이스의 `ALO_QWEN_API_KEY`, `ALO_QWEN_BASE_URL`, `ALO_QWEN_MODEL`을 서비스 환경에 설정합니다.
- 공정표 없는 매장의 아카이브 요약이 필요하면 담당자가 승인된 요약 JSON을 서버에 보관하고 `ALO_COPILOT_ARCHIVE_INDEX`에 파일 경로를 설정합니다.

## 화면에서 테스트하기

저장소 루트에서 `python -m http.server 4319 --bind 127.0.0.1 --directory prototype/aloha-web`을 실행합니다. [프로젝트 도구](http://127.0.0.1:4319/project-tools.html)를 열고 시험 API 주소와 본인 계정의 API 토큰을 입력한 뒤, 매장·공정표·월요일 날짜를 선택합니다.

- 본사 계정으로 초안 생성 요청을 한 뒤 워커가 완료하면 목록을 갱신해 PDF를 받습니다.
- 등록된 작업명만 먼저 말하고 확인 질문을 받은 뒤 `진행률`이라고 답합니다. 처음 지정한 작업과 주차로 조회하는지 확인합니다.
- 확인 질문 뒤 다른 작업이나 주차를 명시하고 새 범위가 우선되는지 확인합니다.

## 로컬 검사

```text
python -m pytest -q tests/test_project_api.py tests/test_weekly_report_extension.py tests/test_copilot.py tests/test_copilot_history.py
node prototype/aloha-web/tests/project-api.test.cjs
node prototype/aloha-web/tests/project-tools.test.cjs
```

Python 검사에는 기존 `tests/requirements.txt`와 워커 의존성을 사용합니다. PDF 통합 검사는 임시 DB와 합성 이미지로 실행되고, 챗봇 검사는 테스트용 모델 응답을 사용합니다.
