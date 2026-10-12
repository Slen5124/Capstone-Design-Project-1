# 🏗️ SAM 3 기반 데이터 전처리 및 초벌 라벨링 가이드 (`process.md`)

이 문서는 **Alo Yoga 공사 현장 진행 모니터링 프로젝트**에서 사용되는 **Meta SAM 3 (Segment Anything Model 3)** 기반 데이터 전처리 파이프라인의 전체 사용법, 파라미터 설정, 검수 가이드, 그리고 후속 딥러닝(U-Net) 모델과의 연계 과정을 상세히 정리한 공식 매뉴얼입니다.

---

## 📌 목차
1. [개요 및 전처리 아키텍처](#1-개요-및-전처리-아키텍처)
2. [사전 준비 및 환경 세팅](#2-사전-준비-및-환경-세팅)
3. [데이터 규격 및 디렉토리 구조](#3-데이터-규격-및-디렉토리-구조)
4. [SAM 3 자동 전처리 스크립트 실행법](#4-sam-3-자동-전처리-스크립트-실행법)
5. [주요 설정 파라미터 튜닝](#5-주요-설정-파라미터-튜닝)
6. [X-AnyLabeling을 활용한 시각 검수 (Human-in-the-loop)](#6-x-anylabeling을-활용한-시각-검수-human-in-the-loop)
7. [후속 딥러닝(U-Net) 파이프라인 연계](#7-후속-딥러닝u-net-파이프라인-연계)
8. [자주 묻는 질문(FAQ) 및 트러블슈팅](#8-자주-묻는-질문faq-및-트러블슈팅)

---

## 1. 개요 및 전처리 아키텍처

### 1.1 왜 데이터 전처리가 필요한가?
시공 현장 감시 AI(U-Net, DeepLabV3 등)가 사진 속에서 바닥, 벽체, 배관, 경량철골 등을 식별하려면 **정확한 픽셀 단위 정답지(Ground Truth Segmentation Mask)**가 필요합니다. 
수만 장의 사진을 사람이 일일이 점(Polygon)을 찍어 라벨링하는 것은 불가능에 가깝기 때문에, **SAM 3의 Promptable Concept Segmentation(텍스트 프롬프트 기반 자동 분할)** 기술을 활용하여 초벌 라벨링을 1차로 대량 자동화하고 사람이 오탐만 수정하는 방식을 채택합니다.

```
[Raw 현장 사진 (3.6만 장)]
       │
       ▼
[SAM 3 시맨틱 분할기 (batch_preprocess_sam.py)]
 ├─ 텍스트 프롬프트: "floor", "metal stud framing", "wall", "ceiling", "HVAC ductwork" ...
 ├─ FP16 양자화 및 GPU 병렬 가속 (Multiprocessing)
       │
       ▼
[LabelMe 호환 JSON 생성 (data/processed_json/)]
 ├─ 픽셀 좌표 다각형(Polygon) 정보 포함
 ├─ description: "sam3_auto" 자동 마킹
       │
       ▼
[X-AnyLabeling 수동 검수 (Human-in-the-loop)]
 ├─ 오탐 영역 삭제 및 누락된 경계 보정
       │
       ▼
[U-Net 학습 데이터셋 완성 (train_unet_server.py)]
```

### 1.2 SAM 3의 특징과 장점
* **단일 단계(One-Stage) 개념 분할**: 기존 방식(Grounding DINO로 네모 박스를 친 후 SAM으로 마스크를 따는 2단계 방식)과 달리, SAM 3는 텍스트 단어(`"floor"`, `"wall"`)만 전달하면 사진 전체에서 해당 개념의 인스턴스를 즉시 픽셀 마스크로 검출합니다.
* **LabelMe 포맷 직결**: X-AnyLabeling 및 CVAT에서 바로 열람·수정할 수 있는 다각형 포맷으로 자동 변환 저장됩니다.

---

## 2. 사전 준비 및 환경 세팅

### 2.1 필수 라이브러리 설치
터미널(PowerShell 또는 Bash)에서 아래 패키지들을 설치합니다:

```bash
# 기본 비전 및 딥러닝 패키지
pip install torch torchvision opencv-python ultralytics tqdm

# SAM 3 텍스트 프롬프트용 전용 CLIP (Ultralytics 포크 버전 필수)
pip uninstall clip -y
pip install git+https://github.com/ultralytics/CLIP.git
```

### 2.2 SAM 3 모델 가중치(`sam3.pt`) 확보
> ⚠️ **주의**: Meta의 정책상 SAM 3 가중치는 자동으로 다운로드되지 않는 게이트(Gated) 모델입니다.
1. [Hugging Face Meta SAM 3 저장소](https://huggingface.co/facebook/sam3)에 접속하여 로그인합니다.
2. **"Request access"** 버튼을 눌러 이용 권한을 신청합니다 (보통 몇 시간 내 승인).
3. 승인 완료 후 `sam3.pt` 파일을 다운로드하여 프로젝트의 `weights/` 폴더에 배치합니다:
   ```text
   Aloha/
   └── weights/
       └── sam3.pt   <--- (약 2.4GB 가중치 파일 배치)
   ```

---

## 3. 데이터 규격 및 디렉토리 구조

### 3.1 파일 명명 규칙
`data/raw_images/` 폴더에 위치하는 사진은 다음 명명 규칙을 엄격히 따릅니다:
```text
Alo_Yoga_-_건설번호_-_지역명,_CA-연월일-시분초.jpg
```
* **예시**:
  - `Alo_Yoga_-_#120_-_Brea,_CA-20250124-105200.jpg`
  - `Alo_Yoga_-_#105_-_Costa_Mesa,_CA-20250201-140000.jpg`
  - `Alo_Yoga_-_#112_-_Las_Vegas,_NV-20250210-093000.jpg`
  - `Alo_Yoga_-_#108_-_Venice,_CA-20250215-162000.jpg`

### 3.2 디렉토리 계층 구조
```text
Aloha/
├── data/
│   ├── raw_images/         # 원본 현장 사진 (.jpg)
│   ├── processed_json/     # SAM 3가 생성한 라벨링 결과 (.json)
│   └── schedules/          # 공정표 및 스케줄 문서
├── weights/
│   ├── sam3.pt             # SAM 3 사전학습 가중치 (전처리용)
│   ├── unet_best.pth       # U-Net 최고 성능 체크포인트 (학습용)
│   └── unet_final.pth      # U-Net 최종 가중치
├── src/
│   ├── batch_preprocess_sam.py # [전처리 스크립트]
│   ├── train_unet_server.py    # [서버 U-Net 학습]
│   └── train_unet.py           # [로컬 U-Net 학습]
└── process.md              # 본 전처리 가이드 문서
```

### 3.3 클래스 정의표 (Class Definition)
학습 및 전처리에서 인식하는 10개 클래스(배경 0번 + 시공 객체 9종) 정의:

| ID | 영문 프롬프트 (CLASS_PROMPTS) | 한글 명칭 | 설명 |
| :-: | :--- | :--- | :--- |
| **0** | `background` | 배경 | 정의되지 않은 기타 영역 |
| **1** | `floor` | 바닥 | 콘크리트 슬래브, 타일, 바닥 마감면 |
| **2** | `metal stud framing` | 경량 철골 | 벽체를 세우기 전 설치된 아연도금 경량 철골 스터드 |
| **3** | `wall` | 벽체 | 석고보드(Drywall), 합판, 페인트 도장 벽면 |
| **4** | `ceiling` | 천장 | 천장 마감면, 석고 천장재, 천장 구조체 |
| **5** | `HVAC ductwork` | 공조 배관 | 천장부에 노출된 은색 직사각/원형 공조 덕트 및 파이프 |
| **6** | `person` | 작업자 | 안전모, 조끼 등을 착용한 현장 인부 |
| **7** | `construction materials` | 시공 자재 | 바닥에 쌓인 석고보드 묶음, 목재 파렛트, 박스 등 |
| **8** | `Tools` | 공구/장비 | 사다리, 이동식 비계(Scaffold), 수공구 카트 |
| **9** | `unidentifiable` | 미식별 객체 | 먼지, 역광, 가림 등으로 식별 불가한 영역 |

---

## 4. SAM 3 자동 전처리 스크립트 실행법

전처리 스크립트는 `src/batch_preprocess_sam.py`에 구현되어 있으며, Windows 로컬 환경과 Linux GPU 서버 모두 지원합니다.

### 4.1 기본 실행 명령어
터미널에서 프로젝트 루트 디렉토리로 이동 후 실행합니다:

```bash
python src/batch_preprocess_sam.py
```

### 4.2 실행 출력 예시
```text
Loading SAM 3 (weights/sam3.pt)...
[*] 총 36,864장의 사진 발견.
[*] 병렬 작업자(NUM_WORKERS) 수: 4 | 텍스트 프롬프트: 9개 클래스
Processing Images:  15%|███▍                   | 5530/36864 [12:40<1:11:45,  7.27it/s]
[성공] Alo_Yoga_-_#120_-_Brea,_CA-20250124-105200.jpg -> 14개 객체 검출
[성공] Alo_Yoga_-_#120_-_Brea,_CA-20250124-110200.jpg -> 12개 객체 검출
...
모든 작업 완료. 결과 위치: 'data/processed_json' (X-AnyLabeling에서 검수하세요)
```

---

## 5. 주요 설정 파라미터 튜닝

[`src/batch_preprocess_sam.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/batch_preprocess_sam.py)의 상단 설정 블록에서 사용자의 PC 사양 및 요구사항에 맞게 조절할 수 있습니다:

```python
# ---------------------------------------------------------------------------
# 주요 설정값
# ---------------------------------------------------------------------------
NUM_WORKERS = 4          # 병렬 프로세스 수 (아래 하드웨어 권장 기준 참고)
CONF_THRESHOLD = 0.40    # 신뢰도 임계값 (0.3 ~ 0.5 권장)
OVERWRITE_EXISTING = False # False: 이미 생성된 JSON은 건너뜀 (이어하기 기능)
                          # True: 기존 JSON을 덮어쓰고 다시 추출
```

### 5.1 하드웨어별 `NUM_WORKERS` 권장 설정
SAM 3 모델 1개 인스턴스는 약 4~6GB의 GPU VRAM을 사용합니다. 보유하신 GPU 메모리에 맞춰 프로세스 수를 조절하세요:
* **RTX 4060 / 3060 (8~12GB VRAM)**: `NUM_WORKERS = 1 ~ 2`
* **RTX 4080 / 5060 Ti / 3090 (16~24GB VRAM)**: `NUM_WORKERS = 2 ~ 3`
* **서버 NVIDIA RTX A6000 (62GB VRAM)**: `NUM_WORKERS = 6 ~ 8` (초당 수십 장 초고속 처리 가능)

### 5.2 `CONF_THRESHOLD` 설정 팁
* `CONF_THRESHOLD = 0.25 ~ 0.30`: 조금이라도 의심되는 객체까지 적극적으로 잡습니다. (오탐이 늘어날 수 있음)
* `CONF_THRESHOLD = 0.40 ~ 0.45` **(기본 추천)**: 비교적 확실한 객체만 정밀하게 다각형을 추출합니다.
* `CONF_THRESHOLD = 0.55 이상`: 매우 명확한 부분만 마스크를 따며, 조명이 어둡거나 가려진 자재는 누락될 수 있습니다.

---

## 6. X-AnyLabeling을 활용한 시각 검수 (Human-in-the-loop)

자동 추출된 JSON 파일은 프로젝트 루트의 [`X-AnyLabeling-v4.1.0-Windows-CUDA12.exe`](file:///c:/Users/2233w/Desktop/3-2/Aloha/X-AnyLabeling-v4.1.0-Windows-CUDA12.exe) 프로그램을 통해 시각적으로 직접 확인하고 수정할 수 있습니다.

### 6.1 프로그램 실행 및 폴더 연결
1. `X-AnyLabeling-v4.1.0-Windows-CUDA12.exe`를 더블클릭하여 실행합니다.
2. 좌측 메뉴에서 **`Open Dir (폴더 열기)`**를 클릭하고 `data/raw_images` 폴더를 선택합니다.
3. 상단 메뉴의 **`File` -> `Change Output Dir (저장 폴더 변경)`**을 눌러 `data/processed_json` 폴더를 선택합니다.
4. 사진을 넘겨보면 SAM 3가 자동으로 따놓은 **색칠된 다각형(Polygon)**들이 화면에 즉시 로드됩니다.

### 6.2 효율적인 검수 요령
* **오탐 삭제**: 벽인데 바닥으로 잘못 인식된 영역 클릭 후 `Delete` 키로 삭제.
* **경계 수정**: 다각형 모서리의 점(Vertex)을 마우스로 드래그하여 실제 기둥이나 문틀 경계에 맞게 미세 조정.
* **누락 객체 추가**: 상단 툴바의 **`Create Polygons (단축키 Ctrl+N)`**을 눌러 빠진 공구나 작업자 영역을 직접 그리고 이름 지정.
* **단축키**: `D` (다음 사진), `A` (이전 사진), `Ctrl+S` (저장).

### 6.3 현장 라벨링 일관성(Consistency) 원칙
* **가려짐(Occlusion) 처리**: 사다리나 작업자 뒤로 가려진 벽체나 바닥은 **"상상해서 연결하지 않고, 사진에 눈으로 보이는 영역까지만 분리해서 칠한다"**를 기본 원칙으로 합니다.
* **원근감(Perspective) 왜곡**: 천장 배관이나 바닥 타일이 멀리서 뭉개진 경우 무리해서 세부 픽셀을 나누지 않고 덩어리로 라벨링합니다.

---

## 7. 후속 딥러닝(U-Net) 파이프라인 연계

전처리 완료된 데이터는 `src/train_unet_server.py`의 `SegmentationDataset` 클래스를 통해 U-Net 학습 텐서로 자동 변환됩니다.

### 7.1 다각형 마스크 변환 원리
```python
# train_unet_server.py 내부 처리 로직
# 1) 원본 크기의 0(배경) 캔버스 마스크 생성
mask = np.zeros((orig_h, orig_w), dtype=np.uint8)

# 2) JSON 내부의 폴리곤 좌표를 읽어 클래스 ID 번호로 채움
for shape in data.get('shapes', []):
    label = shape['label']
    class_id = CLASS_MAP.get(label, 0)
    points = np.array(shape['points'], dtype=np.int32)
    cv2.fillPoly(mask, [points], class_id) # 다각형 내부를 정수 ID로 래스터화

# 3) 512x512 해상도로 리사이즈 (NEAREST 보간법으로 ID 보존)
mask = cv2.resize(mask, (512, 512), interpolation=cv2.INTER_NEAREST)
```

### 7.2 데이터 무결성 방어 (Fault Tolerance)
* **0바이트 또는 손상 이미지**: `imread` 실패 시 크래시 없이 다음 정상 이미지로 자동 우회.
* **미완성 또는 문법 에러 JSON**: 라벨링 도중 강제 종료되어 손상된 JSON은 `try-except`로 무시하고 빈 배경(0)으로 안전 처리.
* **도시별 10% 층화 분할(Stratified Split)**: 4개 도시(Brea, Costa Mesa, Las Vegas, Venice)의 데이터가 각각 정확히 10%씩 검증(Validation) 세트로 격리되어 모델의 지역 편향(Overfitting)을 방지합니다.

---

## 8. 자주 묻는 질문(FAQ) 및 트러블슈팅

### Q1. `FileNotFoundError: sam3.pt` 에러가 발생합니다.
* **원인**: `weights/sam3.pt` 파일이 존재하지 않습니다.
* **해결**: [Hugging Face Meta SAM 3](https://huggingface.co/facebook/sam3)에서 접근 승인 후 다운로드한 `sam3.pt`를 `Aloha/weights/` 폴더 안에 넣어주세요.

### Q2. `CUDA out of memory` (VRAM 부족) 에러가 발생합니다.
* **해결 1**: `src/batch_preprocess_sam.py`의 `NUM_WORKERS` 값을 낮추세요 (예: 4 -> 1 또는 2).
* **해결 2**: 설정 블록의 `overrides["imgsz"]` 값을 `644`에서 `512` 또는 `448`로 줄이세요.

### Q3. 기존에 라벨링했던 JSON 파일을 덮어쓰고 새로 돌리고 싶습니다.
* **해결**: `src/batch_preprocess_sam.py`에서 `OVERWRITE_EXISTING = True`로 수정한 뒤 스크립트를 재실행하면 모든 사진에 대해 새로 라벨을 덮어씁니다.

### Q4. X-AnyLabeling에서 사진은 뜨는데 마스크가 안 보입니다.
* **해결**: 상단 메뉴 `File` -> `Change Output Dir`가 `data/processed_json`으로 정확히 지정되어 있는지 확인하세요. 사진 파일명과 JSON 파일명이 확장자를 제외하고 정확히 일치해야 마스크가 연동됩니다.

---

**작성자**: Aloha 프로젝트 AI 비전 연구팀 (강태웅)  
**최종 업데이트**: 2026년 10월 12일  
**관련 스크립트**: [`src/batch_preprocess_sam.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/batch_preprocess_sam.py), [`src/train_unet_server.py`](file:///c:/Users/2233w/Desktop/3-2/Aloha/src/train_unet_server.py)
