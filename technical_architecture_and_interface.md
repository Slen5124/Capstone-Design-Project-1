1. 하드웨어 리소스 및 메모리 통제

제약 사항: 상용 AI API 사용이 제한되므로, 단일 로컬 서버(NVIDIA A6000, 62GB VRAM) 내에서 비전 AI 추론 모델과 로컬 LLM이 동시 또는 순차적으로 구동되어야 함. Out Of Memory(OOM) 방지가 최우선 과제임.

리소스 할당 가이드라인:

Vision Module (PyTorch 기반 YOLO/SAM 3): 최대 30GB VRAM 할당.

LLM & RAG Module (Ollama 기반 경량 모델): 최대 25GB VRAM 할당 (양자화 적용 필수).

System Overhead: 안정성을 위한 7GB 여유 공간 확보.

2. 모듈 간 데이터 통신 인터페이스 (Vision -> LLM)
비전 모델이 현장 스냅샷을 분석한 후, LLM 에이전트에게 전달해야 하는 최종 출력값이자 LLM의 입력값이 되는 JSON 데이터규격이 정해져 있음.
아직 미정.
JSON
{
    //TODO
}

3. 로컬 에이전트(LLM) 동작 규칙

외부 통신 차단: OpenAI(GPT-4), Anthropic(Claude) 등 상용 API를 호출하는 코드는 절대 작성하지 말 것. 모든 추론은 로컬 호스트의 Agent를 통해서만 이루어져야 함.

RAG 파이프라인 연동: LLM은 JSON 데이터를 받은 직후, 로컬 벡터 DB에 저장된 '텍스트 공정표'를 검색하여 계획 대비 실제 공정의 지연 여부를 스스로 비교 및 계산해야 함.

4. 기본 디렉토리 구조 (Scaffolding)

Plaintext
/alo_yoga_monitoring_project
├── /data
│   ├── /raw_images        # 현장 스냅샷 원본
│   ├── /processed_json    # 비전 모델이 출력한 JSON 메타데이터
│   └── /schedules         # 공정표 및 도면 (RAG 검색용)
├── /vision_module         # SAM 3, YOLOv8 등 객체 탐지/분할 스크립트
├── /llm_agent_module      # RAG 검색, Ollama 통신 및 이메일 리포트 생성 스크립트
└── main_pipeline.py       # 비전과 LLM 모듈을 연결하여 자동화하는 메인 컨트롤러