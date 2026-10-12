# 🧠 U-Net 시맨틱 분할 서버 학습 가이드 (`unet_use.md`)

이 문서는 **Alo Yoga 공사 진행 모니터링 프로젝트**에서 SAM 3 초벌 전처리 데이터셋을 활용하여 시공 객체(바닥, 벽체, 배관, 철골 등 10개 클래스)를 학습시키는 **`src/train_unet_server.py`의 핵심 기능, 아키텍처, 파라미터 설정 및 서버 실행법**을 정리한 가이드입니다.

---

## 📌 목차
1. [개요 및 모델 아키텍처](#1-개요-및-모델-아키텍처)
2. [핵심 기능 7가지 요약](#2-핵심-기능-7가지-요약)
3. [상단 설정 파라미터 (Settings) 가이드](#3-상단-설정-파라미터-settings-가이드)
4. [도시별 10% 층화 검증 분할 (Stratified Split)](#4-도시별-10-층화-검증-분할-stratified-split)
5. [시각자료 및 그래프 자동 생성 기능](#5-시각자료-및-그래프-자동-생성-기능)
6. [동적 에포크 저장 스케줄러 (Dynamic Saving)](#6-동적-에포크-저장-스케줄러-dynamic-saving)
7. [서버 실행 방법 (KNU AI Server 기준)](#7-서버-실행-방법-knu-ai-server-기준)
8. [출력 디렉토리 및 산출물 파일 구조](#8-출력-디렉토리-및-산출물-파일-구조)
9. [트러블슈팅 및 예외 대응](#9-트러블슈팅-및-예외-대응)

---

## 1. 개요 및 모델 아키텍처

### 1.1 학습 목적
* SAM 3를 통해 초벌 라벨링된 대규모 현장 데이터셋(36,864장)을 교과서로 삼아, **경량 고속 시맨틱 분할 모델(U-Net)**을 학습시킵니다.
* 입력: $512 \times 512 \times 3$ RGB 현장 사진
* 출력: $512 \times 512$ 픽셀 단위 10개 클래스 분류 마스크 (바닥, 벽, 경량철골, 천장, 배관, 인부, 자재, 공구, 미식별, 배경)

### 1.2 U-Net 구조 특징
* **Encoder (Downsampling)**: 4단계 합성곱 및 풀링을 통해 현장 사진의 시각적 특징 추출 ($64 \rightarrow 128 \rightarrow 256 \rightarrow 512$).
* **Bottleneck**: 1024 채널 고차원 시공 맥락 인코딩.
* **Decoder (Upsampling)**: Skip Connection을 통해 인코더의 세밀한 공간 정보를 복원하여 정밀한 자재 경계선 복원 ($512 \rightarrow 256 \rightarrow 128 \rightarrow 64 \rightarrow 10$).

---

## 2. 핵심 기능 7가지 요약

1. **도시별 10% 층화 분할 (Stratified Split)**: 4개 도시(Brea, Costa Mesa, Las Vegas, Venice)에서 각각 정확히 10%씩 검증 데이터로 분리하여 지역 편향 없는 공정한 모델 평가 보장.
2. **에포크별 정밀 검증 루프 (Validation Loop)**: 매 에포크마다 `Train Loss`, `Val Loss`, `Val Pixel Accuracy (%)`, `Val mIoU (%)` 정량 지표 실시간 계산.
3. **최고 성능 모델 자동 보관 (`unet_best.pth`)**: Validation Loss가 역대 최저를 갱신할 때마다 최고 성능 가중치를 손실 없이 별도 저장.
4. **동적 에포크 저장 스케줄러**: 디스크 절약과 정밀 수렴을 위해 **20 에포크 이하에서는 5단위, 20 에포크 초과에서는 1단위**로 체크포인트 및 시각자료 자동 분기 저장.
5. **직관적인 시각자료 자동 생성**: 4개 도시 대표 사진에 대한 **[원본 | 정답 라벨 | U-Net 예측 | 50% 반투명 오버레이]** 4열 비교 이미지 및 실시간 학습 곡선 그래프(`training_curves.png`) 자동 생성.
6. **체크포인트 자동 이어 학습 (Resume)**: 학습 중단 시 `weights/` 폴더 내 가장 최신 체크포인트를 감지하여 다음 에포크부터 끊김 없이 이어 학습.
7. **대규모 데이터 결함 자동 방어 (Fault Tolerance)**: 0바이트/손상 이미지 자동 스킵, 깨진 JSON 파일 안전 무시 처리로 튕김 없는 무중단 학습 보장.

---

## 3. 상단 설정 파라미터 (Settings) 가이드

[`src/train_unet_server.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/train_unet_server.py)의 상단(라인 36~53)에서 주요 하이퍼파라미터를 원클릭으로 수정할 수 있습니다:

```python
# =============================================================================
# 1. 설정 (Settings)
# =============================================================================
IMG_SIZE = (512, 512)      # 입력 이미지 및 마스크 해상도
BATCH_SIZE = 16            # 배치 크기 (A6000 62GB에서 16 권장, 로컬은 4~8)
EPOCHS = 25                # 총 학습 에포크 수
LEARNING_RATE = 2e-4       # Adam 옵티마이저 학습률
NUM_WORKERS = 4            # 데이터 로딩 CPU 멀티프로세스 수 (서버 4, 로컬 1)
VAL_RATIO = 0.10           # 각 도시별 Validation 데이터 비율 (10%)
RESUME = True              # weights/ 폴더의 최신 체크포인트부터 자동 이어 학습
CHECKPOINT_PATH = ""       # 특정 가중치를 직접 지정할 경우 경로 입력 (비워두면 자동 탐색)

# =============================================================================
# 에포크 기록 및 체크포인트 저장 주기 설정
# =============================================================================
SAVE_THRESHOLD_EPOCH = 20  # 저장 주기가 전환되는 기준 에포크 (현재 20)
SAVE_INTERVAL_LOW = 5      # 기준(20) 이하일 때 저장 주기: 5 에포크마다 (5, 10, 15, 20)
SAVE_INTERVAL_HIGH = 1     # 기준(20) 초과일 때 저장 주기: 1 에포크마다 (21, 22, 23...)
```

---

## 4. 도시별 10% 층화 검증 분할 (Stratified Split)

현장 사진 파일명의 구조(`Alo_Yoga_-_#건설번호_-_도시명,_주-연월일-시분초.jpg`)를 자동으로 분석하여, 4개 도시 데이터를 고르게 분할합니다:

```text
=================================================================
 [*] 도시별 데이터셋 층화 분할 (Stratified Split 90% : 10%)
=================================================================
  - [Brea,_CA]: 전체 10,175장       -> Train: 9,158장 | Val: 1,017장
  - [Costa_Mesa,_CA]: 전체 9,918장  -> Train: 8,927장 | Val: 991장
  - [Las_Vegas,_NV]: 전체 7,362장   -> Train: 6,626장 | Val: 736장
  - [Venice,_CA]: 전체 9,409장      -> Train: 8,469장 | Val: 940장
 [*] 총 분할 결과: Train 33,180장 (90%), Val 3,684장 (10%)
=================================================================
```

* 고정 시드(`seed=42`)를 사용하여 언제 재실행하더라도 **정확히 동일한 3,684장의 검증셋이 유지**되므로 에포크 간 성능 비교가 공정합니다.

---

## 5. 시각자료 및 그래프 자동 생성 기능

학습 진행 상태를 비전문가나 팀원도 즉시 이해할 수 있도록 [runs/](file:///c:/Users/2233w/Desktop/3-2/Aloha/runs) 폴더에 시각자료가 자동 생성됩니다:

### 5.1 도시별 4열 비교 시각화 ([runs/visualizations/](file:///c:/Users/2233w/Desktop/3-2/Aloha/runs/visualizations))
검증 세트에서 라벨이 풍부한 4개 도시 대표 사진 4장을 고정 선정하여, 저장 주기마다 4열 고해상도 이미지를 출력합니다:

| 열 번호 | 패널 명칭 | 내용 |
| :---: | :--- | :--- |
| **1열** | `1. Original Image` | 시공 현장 원본 사진 (512x512) |
| **2열** | `2. Ground Truth Mask` | SAM 3 기반 정답 마스크 (클래스별 색상) |
| **3열** | `3. UNet Prediction` | U-Net 모델이 예측한 픽셀 마스크 |
| **4열** | `4. Overlay (Image + Pred)` | 원본 위에 예측 마스크를 50% 알파 합성한 결과 |

* **하단 범례**: 바닥(초록), 경량철골(파랑), 벽체(주황), 천장(보라), 배관(청록), 인부(빨강), 자재(노랑), 공구(남색), 미식별(회색) 등 10개 클래스 색상표 부착.
* **저장 파일명**: `epoch_05_Brea_CA.png`, `epoch_20_Costa_Mesa_CA.png` 등.

### 5.2 실시간 학습 곡선 그래프 ([runs/training_curves.png](file:///c:/Users/2233w/Desktop/3-2/Aloha/runs/training_curves.png))
* **좌측 그래프**: `Train Loss` vs `Validation Loss` 비교 곡선 (과적합 발생 시각적 포착 가능)
* **우측 그래프**: `Validation Pixel Accuracy (%)` 및 `Validation mIoU (%)` 성능 개선 추이

---

## 6. 동적 에포크 저장 스케줄러 (Dynamic Saving)

저장 주기 규칙에 따라 디스크 I/O와 용량을 최적화합니다:

```text
[Epoch 1 ~ 20 구간]
 ├─ 체크포인트 저장: 5 에포크 단위 (Epoch 5, 10, 15, 20)
 └─ 도시별 비교 이미지: 5 에포크 단위 저장

[Epoch 21 ~ 25 구간]
 ├─ 체크포인트 저장: 매 1 에포크 단위 (Epoch 21, 22, 23, 24, 25)
 └─ 도시별 비교 이미지: 매 1 에포크 단위 저장

[상시 동작]
 ├─ Val Loss 역대 최저 갱신 시: 즉시 weights/unet_best.pth 저장
 ├─ 학습 곡선(training_curves.png) & 이력(training_history.json): 매 에포크 자동 갱신
 └─ 전체 학습 종료 시: weights/unet_final.pth 최종 저장
```

---

## 7. 서버 실행 방법 (KNU AI Server 기준)

### 7.1 SSH 접속 및 GPU 연산 노드 대여
```powershell
# 1) Windows PowerShell에서 로그인 서버 접속
ssh -p 10000 coss32@155.230.81.91

# 2) GPU 1대(A6000), CPU 4코어, RAM 32GB 대여 (ABRM02 -> DIS06 등)
srun -p p02 --gres=gpu:1 --cpus-per-task=4 --mem=32G --time=04:00:00 --job-name=taeung-gpu --pty bash
```

### 7.2 환경 켜기 및 실행 (GPU 노드 내부)
```bash
# 1) Conda 환경 활성화
source /abr/coss32/anaconda3/etc/profile.d/conda.sh
conda activate alo-train
export PYTHONNOUSERSITE=1

# 2) 저장소 이동 및 최신 코드 받기
cd ~/Aloha
git pull origin Preprocessing_SAM3

# 3) matplotlib 확인 (시각자료 생성을 위해 필요)
pip install matplotlib

# 4) U-Net 학습 실행
python ./src/train_unet_server.py
```

### 7.3 실행 로그 출력 예시
```text
[*] 사용할 디바이스: cuda
[*] 도시별 데이터셋 층화 분할 (Stratified Split 90% : 10%)
...
[*] [이어 학습하기] 체크포인트 'weights/unet_epoch_20.pth' 로드 완료! Epoch 21부터 학습을 시작합니다.
[*] 모델 학습을 시작합니다! (진행 범위: Epoch 21 ~ 25)

Train [21/25]: 100%|██████████| 2074/2074 [18:22<00:00,  1.88it/s, loss=0.291]
Val   [21/25]: 100%|██████████| 231/231   [00:15<00:00, 14.82it/s]

====> Epoch 21 완료
      Train Loss: 0.2894
      Val   Loss: 0.3012 | Pixel Acc: 86.45% | mIoU: 65.12%
[*] [시각자료 업데이트] 학습 곡선 및 도시별 예측 비교 이미지가 '.../runs'에 저장되었습니다.
[*] [에포크 기록 & 체크포인트 저장] Epoch 21 가중치 및 도시별 시각화 완료 (적용 주기: 1 에포크)
[*] 🌟 [최고 성능 갱신] Best Val Loss: 0.3012 -> 'weights/unet_best.pth' 저장 완료!
```

---

## 8. 출력 디렉토리 및 산출물 파일 구조

학습이 진행됨에 따라 프로젝트 내에 생성되는 산출물입니다:

```text
Aloha/
├── weights/
│   ├── unet_epoch_5.pth        # 5 에포크 정기 체크포인트
│   ├── unet_epoch_10.pth       # 10 에포크 정기 체크포인트
│   ├── unet_epoch_20.pth       # 20 에포크 정기 체크포인트
│   ├── unet_epoch_21.pth       # 21 에포크 (1단위 저장)
│   ├── unet_best.pth           # [핵심] Validation Loss 최저 모델
│   └── unet_final.pth          # 최종 종료 가중치
├── runs/
│   ├── training_curves.png     # [핵심] 실시간 Loss & mIoU 학습 곡선 그래프
│   ├── training_history.json   # 에포크별 수치 누적 로그
│   └── visualizations/         # 도시별 4열 비교 사진 모음
│       ├── epoch_20_Brea_CA.png
│       ├── epoch_20_Costa_Mesa_CA.png
│       ├── epoch_20_Las_Vegas_NV.png
│       └── epoch_20_Venice_CA.png
└── unet_use.md                 # 본 학습 설명서
```

---

## 9. 트러블슈팅 및 예외 대응

### Q1. `ModuleNotFoundError: No module named 'matplotlib'` 에러가 발생합니다.
* **원인**: 가상환경에 `matplotlib`이 누락되었습니다.
* **해결**: `pip install matplotlib`을 실행하세요. (스크립트 내부에 `try-except` 안전장치가 적용되어 있어 설치하지 않아도 학습 자체는 중단 없이 돌아갑니다.)

### Q2. GPU 메모리 부족(`CUDA out of memory`)이 발생합니다.
* **해결**: `src/train_unet_server.py` 상단 설정에서 `BATCH_SIZE = 16`을 `8`로 낮추세요. (기본적으로 FP16 AMP 혼합 정밀도가 활성화되어 있어 16에서도 약 12~14GB VRAM만 점유합니다.)

### Q3. 특정 체크포인트부터 다시 학습을 시작하고 싶습니다.
* **해결**: 상단 설정에서 `CHECKPOINT_PATH = "weights/unet_epoch_15.pth"`와 같이 원하는 파일 경로를 지정하고 실행하면 해당 시점부터 이어서 학습합니다.

---

**작성자**: Aloha 프로젝트 AI 비전 연구팀 (강태웅)  
**최종 업데이트**: 2026년 10월 12일  
**관련 스크립트**: [`src/train_unet_server.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/train_unet_server.py), [`src/train_unet.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/train_unet.py)
