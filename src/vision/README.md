# (보관) 5클래스 분할 모델의 작은 실행 확인 — 초기 계획

> **이 폴더의 `train_smoke.py` / `dataset.py` 는 2026-10-05 실행 확인까지만 쓴 초기 계획(DeepLabv3+ 5클래스 분할)의 코드입니다.**
> 현재 파이프라인은 [`state_pipeline/`](state_pipeline/README.md) 입니다: SAM3 분할 결과를 정리(qa_v3) → 구도·ROI·라벨 검수(vp review) →
> export → `vision_preprocessing/train/train_quick.py` (동결 ConvNeXt 인코더 + U-Net/UPerNet/Linear, 물체 8클래스 + 설치 상태 4클래스).
> 설치 상태와 진행률은 그쪽에서 예측합니다. 아래 내용은 기록용으로 남깁니다.

DeepLabv3+ + ResNet-50으로 벽·바닥·천장·사람·기타를 학습하고, 가중치를 저장·다시 로드하여 추론하는 최소 코드였습니다. 시공 상태와 진행률은 예측하지 않습니다.

`dataset.py`는 V2의 활성 후보에 V3 수정본을 **같은 image_id로 교체**하고, 제외된 사진·중복·손상된 마스크를 검사합니다. 기준은 `semantic_drafts/*.png`입니다. 현재 계약은2688×1512, L모드, ID1~5/255입니다. 사진·마스크 해시와 V3 부모 마스크를 검사하므로 처음 시작할 때 전체 활성 후보219장도 읽습니다. 학습할 사진은 로컬 선택 설정에 지정한 표본뿐입니다.

## 환경

실제로 실행한 환경은 Python3.9, PyTorch2.6.0+cu124, torchvision0.21.0+cu124, SMP0.5.0, NumPy1.26.4, Pillow10.4.0입니다. CUDA를 사용할 수 없으면 `--device cpu`를 사용할 수 있지만 동일 실행 시간은 보장하지 않습니다.

기존 연구 환경을 바꾸지 않고 별도 가상환경을 만드세요. PyTorch와 torchvision은 해당 장비에 맞는 조합으로 먼저 설치한 뒤 아래 의존성을 설치합니다.

```powershell
python -m pip install -r .\src\vision\requirements-smoke.txt
```

encoder는 torchvision의 **ResNet50 IMAGENET1K_V2** 가중치를 사용합니다. 실행 코드는 네트워크 요청을 하지 않으며 미리 받은 `resnet50-11ad3fa6.pth`를 `--encoder-checkpoint`로 지정합니다. 모델을 로드할 때 SMP의 `encoder_weights=None`으로 만든 뒤 해당 encoder의 가중치만 넣습니다. torchvision DeepLabv3를 v3+라고 부르는 구현이 아닙니다.

공개 가중치: [PyTorch ResNet-50](https://download.pytorch.org/models/resnet50-11ad3fa6.pth) · [SMP 구현](https://github.com/qubvel-org/segmentation_models.pytorch).

## 로컬 선택 설정

사진의 실제 파일 목록을 코드에 넣지 않습니다. Git 추적에서 제외한 `artifacts/vision_smoke/selection.local.json`을 만들고, 각 파일명의 마지막 날짜·시각 부분을 지정하세요. 아래 값은 자리표시자입니다.

```json
{
  "train_suffixes": ["YYYYMMDD-HHMMSS", "YYYYMMDD-HHMMSS", "YYYYMMDD-HHMMSS"],
  "validation_suffixes": ["YYYYMMDD-HHMMSS", "YYYYMMDD-HHMMSS"]
}
```

각 suffix는 활성 사진 한 장과 정확히 일치해야 하며, 두 그룹의 사진 ID·해시·날짜가 겹치면 실행을 거부합니다. 새 위치의 원본이나 다른 해상도 자료를 쓰려면 dataset 계약을 먼저 확장해야 합니다.

## 실행

아래 폴더들은 각자의 로컬 경로로 바꾸세요. 기존 출력이 있는 경우 덮어쓰지 않으므로 `--output`은 새로운 폴더로 지정합니다.

```powershell
python .\src\vision\train_smoke.py `
  --v2 'D:\datasets\surface_v2' `
  --v3 'D:\datasets\surface_v3' `
  --selection-json '.\artifacts\vision_smoke\selection.local.json' `
  --encoder-checkpoint '.\artifacts\vision_smoke\weights\resnet50-11ad3fa6.pth' `
  --output '.\artifacts\vision_smoke\run_001' `
  --allow-drafts-for-smoke --device cuda --steps 40
```

미검수 초안은 기본적으로 거부합니다. `--allow-drafts-for-smoke`는 연결 확인에 한해 초안 사용을 명시하는 옵션으로, 원래의 `checked`/`training_eligible` 값을 바꾸거나 GT로 승인하지 않습니다.

## 동작과 출력

- RGB는512×288 bilinear resize 후 ImageNet 정규화합니다. 마스크는 nearest로 줄이고1~5를 loss용0~4로 변환합니다.
- unknown/ignore255는 CE와 confusion matrix에서 제외합니다. 두 그룹에5개 클래스가 모두 남는지 확인합니다.
- ImageNet encoder를 포함한 모델을40스텝 fine-tune합니다. encoder LR은1e-5, decoder/head LR은1e-3, 배치 크기는2입니다. 작은 표본에서는 encoder BN의 통계를 고정합니다.
- 출력에는 `model_last.pt`, `metrics.json`, `history.json`, `data_audit.json`, 로컬 split/config/environment, PNG 예측, 비교 JPG, `index.html`이 있습니다.
- raw semantic 예측은1~5입니다. `display.png`는 기존 라벨의255를 가려 표시하는 버전으로, 모델이 unknown을 판단한 결과가 아닙니다. 원본 해상도 PNG는 저해상도 예측을 nearest로 확대한 것입니다.

학습 손실·유한한 기울기·head 변경·체크포인트 재로딩을 확인합니다. 지표는 **미검수 초안과의 일치도**이며 train mIoU는 과적합 확인입니다. absent-class는 union=0인 경우에만 macro mIoU에서 제외합니다. validation에는 train에서 선택한 최다 클래스만 예측하는 기준선도 기록합니다.

이번 동일 현장·다른 날짜 표본은 실행 확인용입니다. 정식 평가는 별도 검수 정답과 **프로젝트 단위 Train/Validation/Test 분리**로 진행해야 합니다. 현장 사진·예측 이미지·가중치·파일 목록·절대 경로가 담긴 실행 JSON은 `artifacts/`에 두고 커밋하지 마세요.
