"""
SAM 3 기반 일괄 초벌 라벨링 스크립트 (Batch Pre-labeling with SAM 3)

- 모델: Meta SAM 3 (Ultralytics 통합), 가중치 파일 `sam3.pt`
- 방식: SAM3SemanticPredictor + 텍스트 프롬프트 (Promptable Concept Segmentation)
        -> 사진 속에서 "floor tile", "drywall" 등 지정한 개념의 모든 인스턴스를 찾아 마스크 생성
        -> 클래스 이름이 이미 붙은 상태로 X-AnyLabeling(LabelMe) JSON 저장

[사전 준비 - 필수]
1. sam3.pt 가중치는 자동 다운로드되지 않습니다 (Meta 게이트 모델).
   https://huggingface.co/facebook/sam3 에서 접근 요청(Request access) → 승인 후 sam3.pt 다운로드
   → 이 프로젝트의 `weights/sam3.pt` 위치에 넣어주세요.
2. 텍스트 프롬프트용 CLIP 패키지 (Ultralytics 포크):
   pip uninstall clip -y
   pip install git+https://github.com/ultralytics/CLIP.git
3. GPU 사용을 위해 CUDA 버전 PyTorch 필요 (CPU에서는 매우 느림):
   pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
"""

import glob
import json
import os
import sys

import cv2
import torch
from ultralytics.models.sam import SAM3SemanticPredictor

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
WEIGHTS_PATH = os.path.join("weights", "sam3.pt")
RAW_DIR = os.path.join("data", "raw_images")
OUT_DIR = os.path.join("data", "processed_json")

# 클래스 정의서 (teaching.md 3교시) - 팀과 확정 후 수정하세요.
# SAM 3는 짧은 영어 명사구 프롬프트에서 가장 잘 동작합니다.
CLASS_PROMPTS = [
    "ceiling",
    "wall",
    "floor",
    "human",
    "unidentifiable",
]

CONF_THRESHOLD = 0.55  # 낮추면 더 많이 찾고(오탐 증가), 높이면 확실한 것만 남김


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


def main():
    # 1) 가중치 존재 확인 (자동 다운로드 불가 모델이므로 친절하게 안내)
    if not os.path.isfile(WEIGHTS_PATH):
        print("[경고] weights/sam3.pt 가중치가 없습니다. Ultralytics의 자동 다운로드를 시도합니다.")

    use_gpu = torch.cuda.is_available()
    if not use_gpu:
        print("[경고] CUDA GPU를 찾지 못해 CPU로 실행합니다. SAM 3는 CPU에서 매우 느립니다.")

    os.makedirs(OUT_DIR, exist_ok=True)

    # 2) SAM 3 시맨틱 예측기 생성
    overrides = {
        "conf": CONF_THRESHOLD,
        "task": "segment",
        "mode": "predict",
        "model": WEIGHTS_PATH,
        "verbose": False,
        "save": False,
    }
    if use_gpu:
        overrides["quantize"] = 16  # FP16: A6000에서 속도↑, VRAM↓

    print(f"Loading SAM 3 ({WEIGHTS_PATH})...")
    predictor = SAM3SemanticPredictor(overrides=overrides)

    image_paths = sorted(glob.glob(os.path.join(RAW_DIR, "*.jpg")))
    print(f"총 {len(image_paths)}장 발견. 프롬프트: {CLASS_PROMPTS}")

    for idx, img_path in enumerate(image_paths, 1):
        filename = os.path.basename(img_path)
        out_json_path = os.path.join(OUT_DIR, os.path.splitext(filename)[0] + ".json")

        if os.path.exists(out_json_path):  # 이어서 실행할 때 중복 처리 방지
            continue

        # 손상된 이미지 파일은 건너뜀
        if cv2.imread(img_path) is None:
            print(f"[{idx}/{len(image_paths)}] 건너뜀 (이미지 읽기 실패): {filename}")
            continue

        predictor.set_image(img_path)
        results = predictor(text=CLASS_PROMPTS)
        n = results_to_labelme(img_path, results[0], out_json_path)
        print(f"[{idx}/{len(image_paths)}] {filename} -> {n}개 객체")

    print(f"\n완료. 결과: '{OUT_DIR}' (X-AnyLabeling에서 검수하세요)")


if __name__ == "__main__":
    main()
