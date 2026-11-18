# Vision 최종 결과 전달 어댑터

v8 공유본에서 담당한 결과 검증 및 전달 코드를 공개용으로 옮겼습니다. 이 폴더는 모델 학습·추론을 실행하지 않습니다.

## 호출 관계

- `vision_contract.validate_delivery(document, checklist)`는 `vision_output/1.0`을 검사해 작업 UUID, 주차, ROI, 비율과 근거 파일 UUID를 검증합니다.
- `vision_upload.submit`은 계약 조회 후 위 검증기를 호출합니다. `--dry-run`은 결과를 검증하고, 일반 실행은 원본 JSON을 기존 `/workflow/sites/{site}/weeks/{week}/vision-results`에 전달합니다.
- 입력은 실제 모델 결과여야 합니다. 테스트·예시·사람 라벨 자료를 실제 추론 결과로 승격하지 않습니다. 합성 테스트는 이런 입력이 거절되는지 확인합니다.

## 실행

Python 표준 라이브러리로 실행할 수 있습니다. 아래 예시의 매장명과 주차는 가상 값입니다. 실제 주소·토큰은 승인된 서비스 환경의 `ALO_API_BASE`, `ALO_WORKFLOW_VISION_TOKEN`에 설정합니다.

```text
python src/llm_agent_module/vision_delivery/vision_upload.py --help
python src/llm_agent_module/vision_delivery/vision_upload.py --site sample-site --week 7 --contract
python src/llm_agent_module/vision_delivery/vision_upload.py --site sample-site --week 7 --input /path/to/private-result.json --dry-run
python -m pytest -q tests/test_vision_contract.py tests/test_vision_upload.py
```

외부 서버에는 HTTPS를 사용합니다. SSH 터널 등 로컬 연결은 loopback HTTP를 지원합니다. API 토큰은 명령 인수나 Git 파일에 넣지 않습니다.

## 연결과 검증 범위

이 업로더는 기존 `/workflow` 계약용입니다. 공개 저장소의 `/api/v1` API에는 이 경로를 새로 등록하지 않습니다. 해당 계약을 구현한 승인된 서버에서 사용해야 합니다.

팀원의 `final_model/backend_contract.py`는 `/api/v1/.../observations` 요청 초안을 만듭니다. 그 출력은 이 업로더의 입력 형식과 다릅니다. 자동 연결하려면 등록된 사진·오버레이 UUID, 작업과 공정표 연결, 결과 상태, 주차 완료 전달을 별도로 검증해야 합니다.

이 추가분은 합성 자료·가짜 HTTP 클라이언트로 로컬 검사합니다. 실제 PostgreSQL, AWS 업로드, 학교 GPU 추론 및 `final_model` 결과의 전체 전달은 이 변경에서 시험하지 않았습니다.

이전 `vision_submit.py`는 관리자 토큰과 기존 배치 API를 사용하는 별도 경로입니다. 최신 Vision 전달 계약과 혼동하지 않도록 이번 반영에서 제외했습니다.
