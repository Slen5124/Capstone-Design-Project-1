import time

class LLMAgent:
    def __init__(self, vram_limit_gb=25.0):
        self.vram_limit_gb = vram_limit_gb
        # Ollama의 경우 모델 실행 시 OOM 방지를 위해 num_gpu, num_ctx 파라미터를 통해 VRAM 제어
        # 권장: gemma:7b 또는 llama3 (경량화/양자화 모델)
        self.llm_model = "gemma:7b" 
        self.vector_db = None
        print(f"[LLM Agent] Agent initialized. Target LLM: {self.llm_model} (VRAM Limit: ~{self.vram_limit_gb}GB)")
        
    def initialize_rag(self, schedule_dir):
        """
        텍스트 공정표 및 도면 데이터를 벡터 DB(ChromaDB 등)에 임베딩하여 RAG 파이프라인 초기화
        """
        print(f"[LLM Agent] Initializing Vector DB with schedules from '{schedule_dir}'...")
        # 실제 환경: LangChain DocumentLoader -> TextSplitter -> Embeddings -> Chroma Vector DB
        time.sleep(1)
        self.vector_db = "Mock Vector DB Loaded"
        print("[LLM Agent] RAG pipeline ready.")

    def _search_schedule(self, vision_data):
        """
        Vision 모듈에서 받은 JSON 데이터를 기반으로 벡터 DB에서 계획(공정표) 정보 검색
        """
        zone = vision_data.get('zone', 'Unknown Zone')
        print(f"[LLM Agent] Retrieving schedule context for zone: '{zone}'")
        # 실제 환경: self.vector_db.similarity_search(query=f"{zone} schedule today")
        time.sleep(0.5)
        # 검색된 더미 텍스트
        return "9월 30일 계획: Store Front 창문 프레임 및 조명 기구 설치 완료."

    def generate_report(self, vision_data):
        """
        로컬 LLM을 사용하여 시각 분석 데이터와 RAG 컨텍스트를 종합, 지연 판단 및 보고서 생성
        """
        schedule_context = self._search_schedule(vision_data)
        
        print(f"[LLM Agent] Generating report via local LLM ({self.llm_model})...")
        # 실제 환경: ollama.chat(model=self.llm_model, messages=[...]) 또는 LangChain 연동
        time.sleep(2)
        
        # 추출한 비전 데이터 파싱
        objects = vision_data.get('detected_objects', [])
        vision_summary = ", ".join([f"{obj['class']}({obj['status']})" for obj in objects])
        
        # 생성된 더미 리포트
        report = (
            f"=== 🏗️ 일일 공정 모니터링 자동 리포트 ===\n"
            f"• 촬영 시간: {vision_data.get('timestamp')}\n"
            f"• 분석 구역: {vision_data.get('zone')}\n\n"
            f"[공정표 기준 계획]\n"
            f"- {schedule_context}\n\n"
            f"[비전 AI 현장 분석 결과]\n"
            f"- 인식된 상태: {vision_summary}\n\n"
            f"[LLM 종합 판단 및 지연 의심 여부]\n"
            f"- ⚠️ 창문 프레임(window_frame)은 'installed' 상태로 정상 진행 중이나, "
            f"조명 기구(lighting_fixture)가 'missing'으로 감지되었습니다.\n"
            f"- 계획 대비 조명 기구 설치 공정에 지연이 발생했을 가능성이 높습니다. 현장 확인이 필요합니다.\n"
            f"==========================================="
        )
        return report
