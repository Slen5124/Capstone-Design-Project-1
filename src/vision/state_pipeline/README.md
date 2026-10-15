# state_pipeline — 고정 카메라 공정 사진 전처리 · 학습 데이터 생성

고정 카메라로 찍은 실내 마감 공정 사진(벽 보드, 천장 등)에서 **"관측 가능한 영역 안에서 얼마나 설치됐는지"** 를 학습할 수 있도록,
SAM3 자동 분할 결과를 정리하고 사람이 검수한 라벨만 학습 데이터로 내보내는 파이프라인입니다.
같은 데이터로 바로 돌려볼 수 있는 간단한 학습 코드(DINOv3/ConvNeXt 인코더 + U-Net/UPerNet 디코더)도 포함합니다.

> 이 폴더에는 **코드와 사람이 쓴 문서만** 있습니다. 현장 사진, SAM3 결과, 1차 전처리 결과, 검수 저장소, 학습 가중치는 포함하지 않으며
> 커밋해서도 안 됩니다 (저장소 `CONTRIBUTING.md`의 데이터 취급 원칙). 현장 이름은 `SITE_A` 처럼 익명화되어 있습니다.

## 전체 흐름

```
현장 사진 ZIP ─▶ [S] SAM3 자동 분할 (이 폴더 밖, 사진마다 LabelMe JSON)
             ─▶ [1차] qa_v3        폴리곤 정리 · 중복 제거 · 대표 기준으로 벽/천장 보정 → auto_fixed / kept / needs_review / rejected
             ─▶ [2차] vp auto      구도(view) 묶기 · ROI 초안 · 카메라 흔들림 감지 · 날짜별 단계 · 표본 · 상태 라벨 초안
             ─▶ [검수] vp review   브라우저에서 구도/대상/ROI/라벨 승인 (사람이 승인한 것만 정답)
             ─▶ [export]           이미지 + semantic / target / visibility / state 마스크 + train/val/test 분할
             ─▶ [학습] train_quick  2-헤드 분할 모델 (물체 8클래스 / 설치 상태 4클래스)
```

설치 상태 4클래스: `0 미설치` · `1 설치` · `2 가려짐(사람·장비·자재)` · `3 대상 아님`, 무시 값 `255`.
설치율 = 설치 ÷ (설치 + 미설치). 가려진 부분은 분모에서 빠집니다.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `run_auto_preprocess.ps1`, `전처리_실행.cmd`, `전처리_상태.cmd` | 1차 전처리 실행 · 진행 상태 확인 |
| `qa_v3/` | 1차 전처리 본체 (`auto_preprocess.py`), 체크포인트 · 재시작 · 보정 로직 |
| `qa_v3/teacher_surfaces.example.json` | 대표 기준 면(벽/천장) 형식 예시. 실제 파일은 각자 로컬에서 작성 |
| `qa_v2/` | 1차에서 사용하는 정합 · 의미 검사 모듈 |
| `vision_preprocessing/` | 2차 전처리, 검수 앱, export, 학습 코드 (`vp.cmd`로 실행) |
| `vision_preprocessing/config/site_example.json` | 현장 설정 예시. 현장마다 복사해서 사용 |
| `vision_preprocessing/config/taxonomy.json` | 물체 · 대상 · 작업 분류 정의 |
| `vision_preprocessing/train/train_quick.py` | 빠른 학습 · 평가 스크립트 |
| `vision_preprocessing/docs/NEW_SITE_GUIDE_ko.md` | **새 현장 추가 사용 설명서 (먼저 읽기)** |
| `vision_preprocessing/docs/AI_HANDOFF.md` | 설계 결정 · 데이터 계약 요약 (다른 사람/LLM 인계용) |
| `docs/stage1_*.txt` | 1차 전처리 상세 안내 |

## 사용법

### 0. 작업 폴더 준비 (Windows, Python 3.10+)

데이터가 저장소 안에 섞이지 않도록 **이 폴더의 내용을 저장소 밖 작업 폴더로 복사해서** 실행합니다.

```bat
xcopy /E /I src\vision\state_pipeline D:\work\preprocessed_data
cd /d D:\work\preprocessed_data
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.v3.lock.txt
```

작업 폴더에 각자 준비할 것 (모두 로컬 전용):

- `Data\` : 현장 사진 ZIP과 SAM3 결과 ZIP
- `automatic_reference_v3\project.json` : 1차 전처리 프로젝트 설정 (사진 ZIP 위치 · 대표 사진 목록)
- `qa_v3\teacher_surfaces.json` : `teacher_surfaces.example.json` 형식의 대표 기준 면

### 1. 1차 전처리 (qa_v3)

```bat
전처리_실행.cmd            rem 메뉴 실행 (전체 / 작은 시험 / 이어서)
전처리_상태.cmd            rem 진행 상태 확인
```

ZIP 암호는 실행 중에 입력하거나 환경변수 `QA_ZIP_PASSWORD`로 넘깁니다. **파일에는 저장되지 않습니다.**
결과는 `automatic_results\run_...\` 에 분류별 ZIP으로 쌓이며, 원본은 수정하지 않습니다. 자세한 내용: `docs\stage1_README_AUTO.txt`.

### 2. 현장 설정 만들기

```bat
set VP=vision_preprocessing\vp.cmd
%VP% new-site --site-id SITE_B --name "Store B" --zip-contains "#001_-_Example_B"
```

`vision_preprocessing\config\site_b.json` 이 생깁니다. 대상(W01~, C01 등)은 `proposed` 상태로 만들어지고, 검수 화면에서 확정합니다.

### 3. 2차 전처리 → 검수 → export

```bat
%VP% auto   --config vision_preprocessing\config\site_b.json --label site_b_full   rem 자동 단계 전부
%VP% review --run state_preprocessing_results\run_..._site_b_full                  rem http://127.0.0.1:8765/todo
%VP% auto   --run state_preprocessing_results\run_..._site_b_full                  rem 검수 반영 후 다시 실행
```

검수 순서: 구도 승인 → 대상 · 작업 적용 여부 → 구도별 ROI (보이지 않는 대상은 **[안 보임]**) → 상태 라벨 승인.
자동으로 만든 것은 모두 초안이며, 이름이 기록된 사람의 승인만 학습 정답이 됩니다.
export 결과는 run 폴더의 `exports\<이름>_train_full\` 아래에 생기며, `train_quick.py`의 입력이 됩니다.

### 4. 빠른 학습 (선택, GPU 권장)

```bat
pip install torch timm      rem 환경에 맞는 CUDA 버전으로 설치
.venv\Scripts\python -m vision_preprocessing.train.train_quick --export <run 폴더>\exports\<이름>_train_full --decoder unet
```

`--decoder unet | upernet | linear`, `--encoder` (timm 모델 이름, 기본 `convnext_tiny.fb_in22k`). 인코더는 동결되며 가중치는 저장하지 않습니다.
출력: `metrics.json` (state mIoU, 설치율 오차), `log.csv`, 오버레이 이미지, `model_quick.pt`. **출력물은 커밋하지 않습니다.**

## 테스트 (합성 데이터, 실제 데이터 불필요)

```bat
.venv\Scripts\python -m unittest vision_preprocessing.tests.test_auto vision_preprocessing.tests.test_pipeline
cd qa_v3 && ..\.venv\Scripts\python -m unittest discover -p "test_*.py" && cd ..
cd qa_v2 && ..\.venv\Scripts\python -m unittest discover -p "test_*.py" && cd ..
```

확인된 결과: vision_preprocessing 49개 · qa_v3 83개 · qa_v2 42개 통과 (학습 테스트는 torch/timm이 없으면 건너뜀).

## 원칙

- 현장 데이터는 로컬에서만 처리하고 외부 서비스로 업로드하지 않습니다.
- 원본 사진 · 원본 JSON · 1차 결과는 수정하지 않으며, 폴리곤을 덮어쓰지 않습니다.
- 모델 입력은 RGB 사진뿐이고, 이전 날짜의 상태를 이후 날짜에 강제로 적용하지 않습니다.
- 사전학습 가중치(DINOv3, SAM3 등)는 저장소에 넣지 않습니다. 각 라이선스에 따라 개별적으로 받습니다.
