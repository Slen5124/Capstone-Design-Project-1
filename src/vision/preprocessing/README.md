# 로컬 비전 전처리

현장 JPEG의 목록과 대표 사진을 정리하고, Grounding DINO → SAM 2.1 및 DEIMv2 검출 결과를 이용해 5개 클래스 마스크 **초안**을 만든다. 기존 전처리에서 사용한 로직을 재사용할 수 있도록 모델 경로와 사진별 설정을 외부 입력으로 분리했다.

이 폴더에는 코드와 실행 안내만 있다. 사진, 사진 목록, ROI 좌표, 타임스탬프, 라벨, 모델 가중치, 토크나이저, 미리보기, 실행 결과는 포함하지 않는다. 코드의 출처와 모델별 이용 조건은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 확인한다.

## 파일과 역할

| 파일 | 입력 → 출력 | 역할 |
| --- | --- | --- |
| `inventory_select.py` | 원본 JPEG 폴더 → 목록·품질 후보·대표 사진 선택 JSON | 읽기 전용 스캔, SHA256 중복 확인, 대표 사진 선정 |
| `dino_detector.py` | RGB 이미지·텍스트 클래스 → 박스·검출 점수 | Grounding DINO ONNX 실행 |
| `person_detector.py` | RGB 이미지 → COCO 박스·검출 점수 | DEIMv2 ONNX 실행; 클래스 0이 사람인 export 사용 |
| `run_batch.py` | 선택 JSON·원본·ONNX·선택적 뷰 설정 → 마스크·검수 자료 | 박스를 SAM 프롬프트로 전달하고 결과를 합성 |
| `roi_review.py` | 기존 초안 PNG·QA PNG·로컬 ROI JSON → 새 초안 PNG | 사람이 지정한 벽 영역 등을 별도 결과로 반영 |

현재 실행기는 **ONNX Runtime CPUExecutionProvider**를 사용한다. CUDA/GPU 실행을 구현한 코드가 아니다. 모델 학습, 공정 상태 분류, 사진 정합, 진행률 계산은 이 폴더의 범위에 포함되지 않는다.

## 준비

Python 3.12 환경을 권장한다. `requirements.txt`는 기존 전처리 환경에서 확인한 버전을 기록했다. 새 환경에서의 재설치는 별도로 확인해야 한다.

아래 명령은 저장소 루트에서 PowerShell로 실행한다. 예시 경로는 실행자가 로컬에서 구성할 경로이며 실제 현장 경로가 아니다.

```powershell
python -m venv .venv-preprocess
.\.venv-preprocess\Scripts\python.exe -m pip install -r src/vision/preprocessing/requirements.txt
```

모델 파일은 별도로 준비한다. 파일 이름만 같은 임의 ONNX는 호환되지 않는다. 공개 가중치와 ONNX export의 출처·이용 조건을 확인하고 로컬에서 관리한다.

| 자산 | 현재 어댑터가 기대하는 형식 |
| --- | --- |
| Grounding DINO | Swin-T OGC quantized ONNX, 이미지 입력 및 텍스트 attention/position 입력을 받는 X-AnyLabeling 호환 export |
| SAM 2.1 | X-AnyLabeling 호환 encoder/decoder 쌍; encoder의 3개 특징 출력과 decoder의 점·박스 프롬프트 입력 |
| DEIMv2 | HGNetv2-N COCO ONNX, 640 letterbox 입력 및 contiguous COCO 클래스 0..79 출력 |
| 토크나이저 | 해당 DINO 모델과 일치하는 BERT tokenizer JSON |

encoder/decoder는 같은 export에서 나온 쌍을 사용한다. 원본은 저장소 밖에 두거나 무시되는 `data/`, `artifacts/`, `weights/`에 둔다. 명령의 경로를 자신의 로컬 파일로 바꾸면 된다.

## 1. 목록 정리와 대표 사진 선정

```powershell
.\.venv-preprocess\Scripts\python.exe src/vision/preprocessing/inventory_select.py --source data/raw/site_images --output artifacts/preprocessing/inventory --workers 4 --cap 220
```

`.jpg`/`.jpeg`를 재귀적으로 찾는다. 파일명 끝의 `-YYYYMMDD-HHMMSS` 패턴에서 날짜·시간 **후보**를 읽는다. 시간대와 실제 촬영 시각을 검증하지 않으며, 이 패턴이 없는 이미지는 목록에는 남지만 시간 순서 대표 선정에서 제외된다. 같은 폴더에 여러 현장이나 카메라를 섞지 않는다.

저해상도 밝기·선명도, SHA256 중복, 인접 사진 차이와 날짜별 분포를 이용해 대표 사진을 선정한다. 변화 점수는 조명·가림·카메라 이동에 영향을 받으며 실제 시공 변화나 진행률이 아니다. 저해상도 디코드 성공은 원본 전체 픽셀의 정상 판정이 아니다.

출력 `selection.json`을 다음 단계에 넣는다. 목록·선택 결과·연락표도 현장 정보가 포함된 생성물이므로 Git에 올리지 않는다. 기존 결과가 있는 출력 폴더는 덮어쓰지 않는다.

## 2. 모델 마스크 초안 생성

먼저 `--pilot-count 3`처럼 소수의 대표 사진으로 실행한다. 아래 자산 이름은 로컬 파일을 가리키는 자리 표시자다.

```powershell
.\.venv-preprocess\Scripts\python.exe src/vision/preprocessing/run_batch.py --selection artifacts/preprocessing/inventory/selection.json --output artifacts/preprocessing/pilot --sam-encoder weights/sam_encoder.onnx --sam-decoder weights/sam_decoder.onnx --dino-model weights/dino.onnx --person-model weights/person.onnx --tokenizer weights/tokenizer.json --pilot-count 3 --threads 4
```

전체 대표 사진에는 `--pilot-count`를 생략하고 **새 출력 폴더**를 지정한다. `--shard-index 0 --shard-count 2`와 `--shard-index 1 --shard-count 2`로 나눌 경우에도 각 작업은 서로 다른 출력 폴더를 사용한다.

흐름은 `원본 RGB → DINO/DEIMv2 박스 → SAM 특징·박스/점 프롬프트 → 클래스별 마스크 → 합성 PNG·QA·검수 대기열`이다. SAM은 여러 후보 중 예측 IoU 점수가 높은 마스크를 고른다. 원본 SAM 마스크를 보존하며 합성에는 박스 내부 픽셀을 사용한다. 점수는 실제 정답률이나 보정된 확률이 아니다.

| ID | 의미 |
| --- | --- |
| 1 | wall |
| 2 | floor |
| 3 | ceiling |
| 4 | person |
| 5 | 인식된 other 객체 후보 |
| 255 | 미분류·충돌·제외 영역; 학습 시 ignore 대상으로 검토 |

`semantic_drafts/`의 원본 크기 단일 채널 PNG가 기준이다. 표면 간 충돌과 person/other 충돌은 255로 남긴다. 감지한 전경 객체는 표면보다 우선한다. **검출되지 않은 나머지 픽셀을 정답 other로 간주하지 않는다.** `candidate_5class/`는 잔여 영역을 other 후보로 표시하는 비교용이며 학습 정답으로 바로 사용하지 않는다.

`previews/`와 HTML은 사람이 확인하기 위한 오버레이다. 모델 입력은 원본 RGB이며 이 오버레이를 입력하지 않는다. LabelMe 형태의 `annotations/`는 외곽선 표시용이므로 앱에서 구멍이 채워져 보일 수 있다. PNG를 기준으로 검수한다.

`uncertainty/`의 0..4 값은 각각 추가 플래그 없음, 미분류 잔여, 클래스 충돌, 제외 영역, 전경 영역에서 보류한 표면 주장이다. 0은 검수 통과를 뜻하지 않는다. `review_queue.csv`의 우선순위도 미검증 휴리스틱이다. 모든 결과는 `checked=false`, `training_eligible=false`, `needs_review=true`로 남는다.

## 3. 사진별 설정과 ROI 보정

기존 검수에서 수정한 사진별 좌표는 코드에 포함하지 않았다. 따라서 **기본 실행만으로 수동 보정 결과까지 재현하지는 않는다.** 촬영 구도가 바뀌거나 물체가 가릴 때 다른 사진의 좌표를 그대로 적용하지 않는다.

필요하면 `--view-config private/view.local.json`을 추가한다. 이 JSON은 다음 구조를 사용하며 좌표는 로컬에서 작성한다.

| 필드 | 형식 |
| --- | --- |
| `images` | 이미지 ID 또는 파일명 stem → rule 객체 |
| `time_ranges` | `start`, `end`, `rule` 객체의 리스트; start/end는 ISO 날짜·시간 후보 문자열 |
| rule의 `reference_size` | `[width, height]` 기준 이미지 픽셀 크기 |
| `withheld_surface_classes` | 보류할 wall/floor/ceiling 문자열 리스트 |
| `manual_prompts` | `class_name`, 픽셀 `xyxy`, 선택적 `points` 리스트 |
| 각 point | `xy: [x,y]`, `label: 1`(포함) 또는 `0`(제외) |
| `foreground_ignore_polygons` | 픽셀 `[x,y]` 점들을 묶은 다각형 리스트 |

일치하는 시간 범위 설정 위에 개별 이미지 설정을 적용한다. 시간 범위 일치는 동일 카메라·영역임을 확인한 것이 아니다. 좌표는 기준 크기에서 현재 원본 크기로 배율만 조정하며, 정합 모델이나 원근 보정은 실행하지 않는다. 날짜·좌표 설정도 실제 데이터이므로 Git에 올리지 않는다. 워터마크 같은 고정 제외 영역은 선택적으로 `--ignore-rectangle X1 Y1 X2 Y2`에 0..1 정규화 좌표를 전달한다. 기본 제외 좌표는 없다.

벽 골조와 골조 사이 공간을 하나의 벽 영역으로 검수하는 정책에는 별도의 ROI 수정기를 사용할 수 있다. `roi_review.py`의 로컬 설정은 정확히 `reference_size`, `wall_planes`, `other_exclusions`, `withhold_exclusions` 키를 갖는다. 각 영역은 이름 → 픽셀 다각형의 객체이며, 사용할 영역이 없으면 빈 객체를 넣는다. 독립 기둥 등 제외 대상은 해당 사진을 확인하여 지정한다.

```powershell
$maskHash = (Get-FileHash -Algorithm SHA256 -LiteralPath artifacts/preprocessing/pilot/semantic_drafts/frame.png).Hash.ToLower()
.\.venv-preprocess\Scripts\python.exe src/vision/preprocessing/roi_review.py --input-mask artifacts/preprocessing/pilot/semantic_drafts/frame.png --input-uncertainty artifacts/preprocessing/pilot/uncertainty/frame.png --roi-config private/roi.local.json --expected-mask-sha256 $maskHash --output artifacts/preprocessing/roi_revision
```

`frame.png`는 실행자가 수정할 파일로 바꾼다. 수정기는 PNG 값·크기·부모 해시를 확인하고 기존 입력을 보존한다. 사람/other 전경, 기존 충돌·보류·제외 표시는 보호하며, 새 폴더에 `semantic_draft.png`, `uncertainty.png`, `review_metadata.json`을 저장한다. ROI 수정 후에도 결과는 미검수 초안이다. QA 5/6/7은 로컬 벽 영역/other 제외/벽 주장 보류 수정의 출처 표시이며 정답 보증이 아니다.

## 학습에 넘기기 전

검수자가 클래스 경계·가림·뷰 변경을 확인하고 별도의 reviewed 데이터 버전을 만든다. 사진과 PNG 라벨의 크기·ID 매핑을 유지한다. 같은 현장·시간대의 인접 사진과 파생 crop이 train/validation/test에 섞이지 않도록 **분할을 먼저 정하고** 대표 선정·라벨 전파·증강을 수행한다. 여러 현장으로 평가할 때는 현장 단위 테스트도 별도로 확보한다.

픽셀 비율과 검출 점수로 매장 전체 진행률을 만들지 않는다. 공정 상태 라벨, 대상 ID, 관찰 범위, 전체 작업량의 분모와 일정 매핑은 별도 설계와 검증이 필요하다.

## 확인된 범위와 한계

공개용 분리 과정에서는 기존 CPU 환경으로 CLI, 토크나이저·박스 변환, 합성 규칙, 설정 검증, 합성 이미지 기반 실행 흐름을 확인했다. 이것은 공개 패키지의 연결·예외 처리 확인이며 새로운 현장의 정확도, 자동 라벨 품질, GPU 속도 또는 모든 ONNX export 호환성을 보장하지 않는다.

원본과 모든 생성물은 로컬에 보관한다. 커밋 시 이 폴더의 `.py`, 사람이 작성한 `.md`, 의존성·제외 규칙 및 라이선스 고지만 명시적으로 선택한다. `git add -f`나 폴더 전체 stage로 제외 규칙을 우회하지 않는다.
