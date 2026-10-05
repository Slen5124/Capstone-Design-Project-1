import os
import gc
import json
import torch
from vision_module.vision_core import VisionProcessor
from llm_agent_module.rag_agent import LLMAgent

def main():
    print("======================================================")
    print("  Alo Yoga Construction Progress Monitoring Pipeline  ")
    print("======================================================")
    
    # 경로 설정
    raw_images_dir = "data/raw_images"
    json_dir = "data/processed_json"
    schedules_dir = "data/schedules"
    
    # 테스트용 더미 파일 경로 설정
    image_path = os.path.join(raw_images_dir, "snapshot_15min.jpg")
    output_json_path = os.path.join(json_dir, "snapshot_15min_result.json")
    
    # -------------------------------------------------------------------
    # [Phase 1: 비전 분석 모듈]
    # 비전 모델을 로드하여 이미지를 분석하고 JSON 결과를 도출합니다.
    # OOM 방지를 위해 Vision은 최대 30GB VRAM만 사용하도록 제한합니다.
    # -------------------------------------------------------------------
    print("\n--- [Phase 1: Vision Analysis] ---")
    vision_processor = VisionProcessor(vram_limit_gb=30.0)
    vision_processor.load_model()
    
    # 15분 단위 스냅샷 이미지 분석 (시뮬레이션)
    vision_result = vision_processor.process_image(image_path)
    
    # 처리된 결과를 JSON 형태로 저장
    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(vision_result, f, ensure_ascii=False, indent=4)
        
    print(f"Vision analysis completed. Results saved to {output_json_path}")
    
    # -------------------------------------------------------------------
    # [OOM 방지: 비전 모델 메모리 해제]
    # A6000(62GB) 단일 서버에서 LLM을 구동하기 위해 VRAM을 안전하게 비워줍니다.
    # 동시 구동이 필요할 경우에는 VRAM Limit를 조절하여 멀티프로세싱으로 분리해야 합니다.
    # -------------------------------------------------------------------
    print("\n--- [System: Reclaiming VRAM] ---")
    del vision_processor
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print("Vision model unloaded and VRAM cleared successfully.")
    
    # -------------------------------------------------------------------
    # [Phase 2: RAG 및 LLM 에이전트 분석]
    # RAG 파이프라인으로 공정표를 검색하고 로컬 LLM을 호출하여 최종 리포트를 작성합니다.
    # LLM은 양자화를 통해 최대 25GB VRAM 내에서 구동되도록 제한(권장)합니다.
    # -------------------------------------------------------------------
    print("\n--- [Phase 2: RAG & LLM Analysis] ---")
    llm_agent = LLMAgent(vram_limit_gb=25.0)
    
    # 텍스트 공정표가 담긴 폴더를 바탕으로 벡터 DB 초기화
    llm_agent.initialize_rag(schedules_dir)
    
    # RAG 기반으로 비전 분석 결과를 평가하여 리포트 생성
    report = llm_agent.generate_report(vision_result)
    
    print("\n[Final Automated Report]")
    print(report)
    
    # -------------------------------------------------------------------
    # [Phase 3: 보고서 자동 발송]
    # -------------------------------------------------------------------
    print("\n--- [Phase 3: Notification] ---")
    print("Sending the generated report and suspicious snapshot to stakeholders at 08:00 AM...")

if __name__ == "__main__":
    main()
