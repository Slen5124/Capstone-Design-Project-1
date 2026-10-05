"""
SAM 3 기반 일괄 초벌 라벨링 스크립트 (Batch Pre-labeling with SAM 3) - 멀티프로세싱 버전

- 모델: Meta SAM 3 (Ultralytics 통합), 가중치 파일 `sam3.pt`
- 방식: SAM3SemanticPredictor + 텍스트 프롬프트 (Promptable Concept Segmentation)
        -> 사진 속에서 지정한 개념의 모든 인스턴스를 찾아 마스크 생성
        -> 클래스 이름이 이미 붙은 상태로 X-AnyLabeling(LabelMe) JSON 저장

[사전 준비 - 필수]
1. sam3.pt 가중치가 weights 폴더에 있어야 합니다.
2. GPU 병렬 처리를 지원합니다. VRAM 용량에 따라 NUM_WORKERS를 조절하세요.
"""

import glob
import json
import os
import sys
import cv2
import torch
import multiprocessing
import concurrent.futures
from tqdm import tqdm
from ultralytics.models.sam import SAM3SemanticPredictor

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
WEIGHTS_PATH = os.path.join("weights", "sam3.pt")
RAW_DIR = os.path.join("data", "raw_images")
OUT_DIR = os.path.join("data", "processed_json")

# 현재 컴퓨터의 VRAM에 맞게 작업자 수 설정 (RTX 5060 Ti 16GB 기준 2~3개 권장, A6000 62GB는 8~10개 권장)
NUM_WORKERS = 2

# 클래스 정의서 (teaching.md 3교시)
CLASS_PROMPTS = [
    "floor",
    "metal stud framing",
    "wall",
    "ceiling",
    "HVAC ductwork",
    "person",
    "construction materials",
    "Tools",
    "unidentifiable",
]

CONF_THRESHOLD = 0.4  # 낮추면 더 많이 찾고(오탐 증가), 높이면 확실한 것만 남김
OVERWRITE_EXISTING = True  # True로 변경 시 기존 결과물(JSON)을 덮어씌워 새로 라벨링을 진행합니다.

# 글로벌 변수 (각 워커 프로세스마다 독립적으로 모델을 하나씩 로드하기 위함)
_worker_predictor = None

def results_to_labelme(image_path, result, out_json_path):
    """SAM 3 결과(Results)를 X-AnyLabeling에서 열 수 있는 LabelMe JSON으로 변환."""
    height, width = result.orig_shape
    shapes = []

    if result.masks is not None and result.boxes is not None:
        class_ids = result.boxes.cls.int().tolist()
        scores = result.boxes.conf.tolist()
        names = result.names  # 텍스트 프롬프트 리스트가 클래스 이름으로 매핑됨

        for polygon, cls_id, score in zip(result.masks.xy, class_ids, scores):
            points = polygon.tolist()
            if len(points) < 3:  # 다각형은 최소 3점 필요
                continue
            shapes.append({
                "label": names[cls_id],
                "score": round(float(score), 4),
                "points": points,
                "group_id": None,
                "description": "sam3_auto",  # 사람이 검수하지 않은 초벌 결과 표시
                "shape_type": "polygon",
                "flags": {},
            })

    labelme_data = {
        "version": "5.2.1",
        "flags": {},
        "shapes": shapes,
        # JSON이 processed_json 폴더에 있으므로 원본 이미지까지의 상대 경로 기록
        "imagePath": os.path.relpath(image_path, os.path.dirname(out_json_path)),
        "imageData": None,
        "imageHeight": height,
        "imageWidth": width,
    }

    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(labelme_data, f, ensure_ascii=False, indent=2)
    return len(shapes)


def init_worker(overrides):
    """각 독립적인 워커(코어)가 실행될 때 맨 처음 한 번만 모델을 로드하는 함수"""
    global _worker_predictor
    # Windows 환경의 멀티프로세싱 제약을 피하기 위해 워커 내부에서 임포트/로드
    _worker_predictor = SAM3SemanticPredictor(overrides=overrides)


def process_single_image(img_path):
    """개별 이미지를 처리하는 함수 (각 워커가 병렬로 실행함)"""
    global _worker_predictor
    filename = os.path.basename(img_path)
    out_json_path = os.path.join(OUT_DIR, os.path.splitext(filename)[0] + ".json")

    if not OVERWRITE_EXISTING and os.path.exists(out_json_path):
        return f"[건너뜀] 이미 처리됨: {filename}"

    if cv2.imread(img_path) is None:
        return f"[오류] 이미지 읽기 실패: {filename}"

    try:
        _worker_predictor.set_image(img_path)
        results = _worker_predictor(text=CLASS_PROMPTS)
        n = results_to_labelme(img_path, results[0], out_json_path)
        return f"[성공] {filename} -> {n}개 객체"
    except Exception as e:
        return f"[실패] {filename} 에러 발생: {str(e)}"


def main():
    if not os.path.isfile(WEIGHTS_PATH):
        print(f"[경고] {WEIGHTS_PATH} 가중치가 없습니다.")
        sys.exit(1)

    use_gpu = torch.cuda.is_available()
    if not use_gpu:
        print("[경고] CUDA GPU를 찾지 못해 CPU로 실행합니다. 멀티프로세싱의 효과가 적을 수 있습니다.")

    os.makedirs(OUT_DIR, exist_ok=True)

    overrides = {
        "conf": CONF_THRESHOLD,
        "task": "segment",
        "mode": "predict",
        "model": WEIGHTS_PATH,
        "verbose": False,
        "save": False,
    }
    if use_gpu:
        overrides["quantize"] = 16  # FP16

    image_paths = sorted(glob.glob(os.path.join(RAW_DIR, "*.jpg")))
    total_imgs = len(image_paths)
    print(f"총 {total_imgs}장 발견. 병렬 작업자(Worker) 수: {NUM_WORKERS}")

    # ProcessPoolExecutor를 이용한 멀티프로세싱 적용
    with concurrent.futures.ProcessPoolExecutor(max_workers=NUM_WORKERS, initializer=init_worker, initargs=(overrides,)) as executor:
        # 진행률 바(tqdm) 적용
        futures = {executor.submit(process_single_image, path): path for path in image_paths}
        
        for future in tqdm(concurrent.futures.as_completed(futures), total=total_imgs, desc="Processing Images"):
            result_msg = future.result()
            # 원한다면 아래 print를 주석 해제하여 개별 결과를 출력할 수 있습니다.
            # print(result_msg)

    print(f"\n모든 작업 완료. 결과: '{OUT_DIR}'")

if __name__ == "__main__":
    # 윈도우 멀티프로세싱 필수 구문
    multiprocessing.freeze_support()
    main()
