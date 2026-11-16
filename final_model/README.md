# final_model — 벽·천장·바닥 통합 학습 및 추론

2026-11-16, `final-vision/0.2`. **세 공종 통합 학습·추론 코드를 구현했다. 추가 현장 데이터는 팀원 PC에서 준비 중이며, 실제 새 데이터 재학습과 정확도 검증은 아직 하지 않았다.** 기존 6상태 가중치만으로 바닥 상태를 판단할 수는 없다.

## 구조

```text
검수된 사진·대상 ROI·가시성·공종별 설치/미설치 마스크
 → export 입력 검사 / 라벨 분포 / 학습·검증 중복 검사
 → 기존 6상태 가중치를 클래스 이름으로 8상태 모델에 이관
 → 동결 DINOv2-B registers + 학습 가능한 특징 피라미드·UPerNet·두 헤드
 → 검증 세트로 가중치 선택 → best.pt
 → 새 사진 + 등록된 구도·대상 ROI
 → 대상 8클래스 + 시공상태 8클래스
 → 공종별 관측 설치 비율 또는 판단 보류
 → 상태 마스크·오버레이·JSON·백엔드 요청 초안
```

VCC, TTA, 단조 증가 보정, 자동 공정 완료 판정은 없다. 기존 연구/LOSO 코드를 수정하지 않고 모델 정의만 재사용한다. 이 폴더는 저장소 전체에 의존하며 독립 배포 패키지는 아니다.

## 클래스와 가중치

| 값 | v0.2 시공상태 |
|---|---|
| 0 / 1 | 벽 보드 미설치 / 설치 |
| 2 / 3 | 천장 패널 미설치 / 설치 |
| 4 / 5 | 바닥 마감 미설치 / 설치 |
| 6 | 가림 |
| 7 | 비대상 |
| 255 | 학습 무시 / 유효하지 않은 예측 |

대상(semantic) 헤드는 별개다: 벽·바닥·천장·스터드·HVAC·자재·도구·사람.

- 기본 `config.json`: v0.2 / 8상태. 새로 학습한 best.pt 또는 last.pt를 사용한다.
- 기존 6상태: `config.wall_ceiling_v1.json`을 명시해 벽·천장 추론에 사용한다. 바닥 요청은 거부한다.
- 6→8 이관: 벽·천장 0~3 유지, 기존 가림 4→6 / 비대상 5→7 이동. 바닥 4/5만 새로 초기화한다. encoder/adapter/decoder/semantic head를 재사용한다.
- 8상태 체크포인트로 추가 학습도 가능하다. 클래스 순서가 다른 파일이나 불완전한 가중치는 거부한다.
- 인코더는 학습하지 않는다. 승인된 timm 가중치가 로컬 캐시에 필요하며 자동 다운로드하지 않는다. 새 체크포인트에 기록한 인코더 hash와 추론 때 로드한 hash가 다르면 중단한다.

## 파일

| 파일 | 역할 |
|---|---|
| layout.py | 버전별 클래스 의미 |
| training_data.py | export 읽기, 3공종 정답 구성, 데이터 검사 |
| checkpoint.py | 기존 가중치의 클래스별 이관 |
| train.py | 추가 학습, 공종별 검증·시험, best/last 저장 |
| training.example.json | 여러 현장 export를 묶는 예시 |
| run.py, model.py | 체크포인트 복원 및 새 사진 추론 |
| core.py | 대상별 측정, 판단 보류, 오버레이 |
| backend_contract.py | 현재 백엔드 Observation 요청 초안 |
| input.example.json | 추론 입력 및 작업 매핑 예시 |
| tests/ | 회귀 검사·합성 가중치 실행 검사 |

## 팀원이 전달할 라벨 형식

원본 사진과 ROI만 있거나 ROI별 숫자(65%)만 있으면 현재 분할 학습을 바로 수행할 수 없다. 기존 벽 라벨처럼 **설치 영역과 미설치 영역을 표시한 마스크**를 공종별로 export한다. 사진과 모든 마스크는 동일한 1024×576 letterbox 좌표계를 사용한다. 라벨 원본과 도구 프로젝트도 별도로 보존한다.

```text
export/
  targets.json
  metadata.jsonl
  splits/train.txt, val.txt, test.txt  (선택: 없으면 metadata의 split 사용)
  images/...
  semantic_masks/...
  target_masks/...
  visibility_masks/...
  state_masks/
    wall_board_installation/<sample_id>.png
    ceiling_panel_installation/<sample_id>.png
    floor_finish_installation/<sample_id>.png
```

이미지·semantic·target·visibility 위치는 metadata에서 지정한다. 상태 마스크는 위 경로 규약을 따른다.

targets.json 예시:

```json
{"targets":[{"index":3,"target_id":"FLOOR_01","surface_type":"floor","activities":{"floor_finish_installation":{"applicability":"applicable","review":"approved"}}}]}
```

metadata.jsonl 한 줄 예시:

```json
{"sample_id":"sample_001","view_id":"VIEW_01","split":"train","label_source":"human","capture":{"time_local":"2026-01-01T12:00:00"},"files":{"image":"images/sample_001.png","semantic":"semantic_masks/sample_001.png","target":"target_masks/sample_001.png","visibility":"visibility_masks/sample_001.png"}}
```

- 공종별 상태: 0=미설치, 1=설치, 255=모름/라벨 없음. 통합 0~7 값을 공종별 입력에 넣지 않는다.
- target: 0=배경, 양의 정수=targets의 index, 255/65535=무시. uint8/uint16 지원. 중첩은 무시로 처리한다.
- visibility: 0=ROI 밖, 1=보임, 2=가림, 3=판단 불가, 255=무시. 상태 정답 0/1은 보이는 영역에서 사용한다.
- semantic: 기존 대상 클래스 0~7, 255=무시. 바닥=1과 바닥 설치=1은 서로 다른 마스크의 값이다.
- label_source: human / propagated / weak 명시. 검증·시험에는 human만 허용한다. 자동 라벨을 검수 없이 human으로 바꾸지 않는다.
- not_applicable은 자동 해제하지 않는다. 실제 적용되는 ROI인지 사람이 확인하고 수정/export한다.
- 한 장에 모든 공종이 보일 필요는 없다. 전체 train에는 공종별 설치·미설치 정답이, val에는 공종별 유효한 ROI 정답이 필요하다.
- 동일 사진 또는 동일 현장·구도·날짜가 여러 split에 들어가면 차단한다. 근접 프레임·인접 날짜 누수나 기존 초기화 모델이 이미 본 사진까지 검증하는 것은 아니다. 시간 간격 및 과거 학습 이력을 별도로 확인한다.

## 학습 실행

실제 데이터와 설정은 Git 밖 또는 private/에 둔다. training.example.json을 비공개 설정으로 복사해 export 경로를 입력한다. 상대 경로는 JSON의 디렉터리 기준이다. 여러 현장을 exports에 추가할 수 있다. 기존 데이터와 가중치는 덮어쓰지 않는다.

저장소 루트의 PowerShell에서 학습 환경 Python으로 실행:

```powershell
# 데이터 검사만 수행
python .\final_model\train.py --data "<비공개 training.local.json>" --out "<새 검사 결과 폴더>" --audit-only

# 기존 6상태 또는 새 8상태 가중치에서 추가 학습
python .\final_model\train.py `
  --data "<비공개 training.local.json>" `
  --init "<기존 체크포인트>" `
  --out "<여유 공간이 있는 드라이브의 새 학습 폴더>" `
  --epochs 15 --batch 2 --lr 0.0001 --device cuda
```

- 확인 환경: Python 3.9.25, torch 2.6.0+cu124, timm 1.0.30, NumPy, Pillow.
- 출력 드라이브에 최소 1GiB 여유 공간을 검사한다. 데이터와 결과 크기에 따라 더 필요하다. 검증 당시 D: 공간 부족으로 합성 테스트 출력만 C:로 이동했다.
- 기본 학습: 동결 인코더, AdamW, LR 1e-4, 15에폭, 배치 2, semantic CE×0.5 + state CE. human/propagated/weak 가중치는 1/0.5/0.3. 수평 반전과 밝기 증강을 사용한다. 새 규격이며 기존 LOSO와 동일하지 않다.
- 지원 GPU는 BF16 학습. FP16 대체 경로는 loss scaling과 실패 step 기록을 사용한다. 성공한 최적화 step이 없는 에폭이나 비정상 수치에서는 중단한다.
- best.pt 선택: 검증 세트의 공종별 설치 비율 오차를 동일 비중으로 평균한다. 보류를 제외해 점수를 부풀리지 않도록 선택 점수에는 보류 ROI당 1.0 오차를 부여한다. 관측 MAE(%p), 관측 비율(coverage), 상태별 IoU를 따로 보고한다.
- test는 best 선택 후 한 번 평가한다. test가 없으면 미평가로 기록한다. 초기화 모델이 과거에 본 이미지를 새 test로 사용하지 않는다.
- 결과: data_audit.json, training_record.json, history.json, best.pt, last.pt, training_complete.json. 모두 비공개 보관한다.
- optimizer resume는 구현하지 않았다. --init best.pt는 새 추가 학습 run이다.

## 추론 실행

input.example.json에 사진, 촬영 시각(UTC offset 포함), source_wh, ROI, 구도·대상·작업 매핑을 넣는다. 원본 ROI는 roi_space=original, export ROI는 model_input으로 지정한다. 카메라 이동 시 새 구도/ROI를 등록한다. 미확인 구도·ROI는 비율을 보류한다. EXIF 회전 사진은 ROI와 함께 방향을 먼저 맞춘다.

```powershell
python .\final_model\run.py --input "<비공개 input.local.json>" --validate-only

python .\final_model\run.py `
  --input "<비공개 input.local.json>" `
  --checkpoint "<새 학습 폴더>\best.pt" `
  --out "<새 추론 결과 폴더>" --device cuda

# 기존 6상태 확인용. 입력에 바닥 대상을 넣지 않는다.
python .\final_model\run.py `
  --config .\final_model\config.wall_ceiling_v1.json `
  --input "<벽·천장 입력 JSON>" --checkpoint "<기존 6상태 가중치>" --out "<새 출력 폴더>"
```

사진별 semantic/state/ROI PNG, confidence NPY, 대상별 overlay, vision_result.json, provenance, API 요청 초안을 저장한다. 마스크·오버레이는 모델 입력 좌표계이며 원본으로 변환할 transform을 JSON에 기록한다. 초록=설치, 빨강=미설치, 회색=가림, 노랑=경계.

비율은 ROI 내 설치/(설치+미설치)다. 가림·비대상·다른 표면·유효하지 않은 픽셀은 분모에서 제외한다. 500px/관측 비율 0.3/평균 max-softmax 0.6은 미보정 임시 기준이다. 미달은 held/null이며 0%나 이전 값으로 채우지 않는다. **실제 면적 또는 작업 전체/매장 전체 공정률은 아니다.**

## 백엔드·간트차트

현재 Observation API에 맞춘 초안만 생성하며 전송하지 않는다. task/schedule/daily record UUID, 작업–촬영 위치 연결, 업로드 후 overlay UUID가 필요하다. 설치율 0~1과 현장 제출 progress_percent 0~100을 구분한다.

모델 provenance·confidence의 서버 저장, held 검수 큐, 인증/재시도/실전 전송, 공정표 비교, 간트 UI, 전후 비교 및 일별 집계는 후속 작업이다. 바닥 마감 설치율을 양생·검사 등 바닥 공사의 모든 단계 완료율로 취급하지 않는다.

## 검증 상태

```powershell
python -m unittest discover -s .\final_model\tests -v
```

31개 회귀 검사 항목을 환경을 나누어 확인했다. Python 3.9 비전 환경: 30개 통과/백엔드 계약 1개 제외. Python 3.12: 29개 통과/Torch 가중치 검사 2개 제외. 실제 백엔드 계약 검사는 Python 3.10 이상과 Pydantic 2가 필요하다.

기존 6상태 이관 → 생성한 픽셀/라벨로 GPU 1에폭 학습 → 8상태 가중치 저장 → 새 프로세스에서 로딩 → 벽·천장·바닥 3개 대상 추론·파일 저장까지 확인했다. 테스트 가중치는 synthetic_smoke_only로 표시하며 현장 운영에 사용하지 않는다. 정확도 실험이 아니다. 실제 추가 라벨의 유효성·수량·분포는 데이터 수령 후 검사한다.

기존 연구 소스·학습 데이터·연구 결과는 수정하지 않았다. 커밋·푸시는 사용자가 수행한다.
