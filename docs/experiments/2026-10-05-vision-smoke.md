# 2026-10-05 — 5클래스 DeepLabv3+ 실행 확인

> (2026-10-23 추가) 이 실험은 초기 계획(DeepLabv3+ 5클래스 분할)의 실행 확인이며, 이후 계획을 바꿨습니다.
> 현재는 사람이 검수한 설치 상태 라벨로 2-헤드 모델을 학습합니다 — `src/vision/state_pipeline/` 과 그 README 참고.
> 아래 "다음 단계"의 DeepLabv3+/U-Net 비교는 하지 않았고, 디코더 비교(U-Net · UPerNet · Linear)는 현재 코드의 `--decoder` 로 합니다.

**데이터 로딩 → fine-tuning → 체크포인트 저장 → 새 모델 재로딩 → 추론 저장 연결을 확인했습니다.** 이번 결과는 미검수 초안 마스크를 이용한 smoke이며 정식 성능 평가가 아닙니다.

## 실행 구성

| 항목 | 실제 실행 |
|---|---|
| 모델 | SMP0.5.0 DeepLabv3+ / ResNet-50 |
| 초기 encoder | torchvision ResNet50 IMAGENET1K_V2 |
| 클래스 | wall / floor / ceiling / person / other |
| 입력 | RGB512×288, ImageNet 정규화 |
| 마스크 | canonical PNG1~5→0~4,255는 CE/지표에서 제외,nearest resize |
| 표본 | 같은 현장의 대표5장: 학습3장, 확인2장 |
| 분리 | 사진ID·해시·날짜 중복 없음. 시간순 예측/다른 현장 평가 아님 |
| 학습 | 40 optimizer steps, batch2, AdamW, encoder LR1e-5, decoder/head LR1e-3 |
| encoder BN | 작은 표본 때문에 running statistics 고정. encoder 가중치는 학습 |
| 장비 | RTX4060 Laptop8GB / torch2.6.0+cu124 / torchvision0.21.0+cu124 |
| 결과 보관 | Git에서 제외한 artifacts/vision_smoke/ |

V2 manifest의 비활성 사진을 먼저 제외하고 V3 두 장은 같은 image_id의 부모 마스크를 교체했습니다. 원본과 metadata 승인 상태는 변경하지 않았습니다. 소량 smoke를 명시적으로 허용한 상태입니다.

## 관측 결과

| 항목 | 값 |
|---|---|
| 동일eval 조건의 학습 CE | 1.6154 → 0.0606 |
| 학습 초안과의 mIoU 일치도 | 93.0% |
| 확인2장 초안과의 mIoU 일치도 | 44.7% |
| 확인2장의 유효 픽셀 일치도 | 73.1% |
| train 최다 클래스 상수 예측의 확인 mIoU | 5.0% |

확인2장 클래스별 초안 IoU:

| 클래스 | 일치도 |
|---|---|
| wall | 55.4% |
| floor | 79.9% |
| ceiling | 70.4% |
| person | 0.0% |
| other | 17.9% |

모든loss/gradient가 유한했고 segmentation head 가중치가 바뀌었습니다. 저장한 체크포인트를 새 모델에 로드한 후 확인2장의 픽셀 예측이 같았습니다. 지표는255 제외 후의 혼동행렬에서 계산하며 union=0인 클래스만 macro mIoU에서 제외합니다.

학습3장의 높은 일치도에 비해 확인2장 결과가 낮습니다. 과적합과 단계별 외관 차이를 볼 수 있으며, 사람은 확인 표본에서 잡지 못했습니다. 특히 기타의 적치 자재·장비와 벽/천장의 혼동을 더 검수해야 합니다. 두 확인용 비교 이미지는 로컬에서 직접 살펴보았습니다.

## 다음 단계

1. 사람과 기타의 경계, 시공 중 벽 골조 정책을 다시 확인하고 검수 라벨을 늘립니다.
2. 12개 프로젝트 중 라벨 품질과 촬영 구도를 확인한 자료로 정식 프로젝트 단위 Train/Validation/Test를 만듭니다.
3. 동일 계약·검수 정답·학습 예산으로 DeepLabv3+/U-Net을 비교합니다. 이번 smoke 수치는 논문 성능표에 넣지 않습니다.

실행 방법은 [src/vision/README](../../src/vision/README.md)에 있습니다. 실제 사진·오버레이·파일 목록·절대 경로·가중치는 이 문서와 Git 커밋에 포함하지 않습니다. 원격 push는 이번 실행에 포함하지 않았습니다.
