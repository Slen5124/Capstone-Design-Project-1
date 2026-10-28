# vision_preprocessing — 대상·작업 상태 전처리와 학습 데이터 export

기존 qa_v2/qa_v3 결과(객체 종류별 폴리곤)는 **그대로 둔 채**, 그 위에 아래 세 층을 덧붙입니다.

| 층 | 예 | 저장 위치 |
|---|---|---|
| 1. 객체 종류 | wall / floor / ceiling / person / materials … | 기존 JSON (읽기 전용) → `semantic_masks/` |
| 2. 물리적 대상 | W01 / W02 / F01 / C01 (현장 내 안정 ID) | 검수 저장소 `target`, `roi` |
| 3. 작업별 상태 | W01 × wall_board_installation, F01 × floor_finish_installation … | 검수 저장소 `state_label` |

자동으로 만든 것은 전부 draft입니다. 사람(이름이 있는 검수자)이 대상·작업·라벨 버전 단위로 승인한 것만 학습 정답이 됩니다.

---

> 다른 도시를 라벨링하거나 다른 컴퓨터로 옮길 때: `docs\NEW_SITE_GUIDE_ko.md` (사용 설명서),
> 다른 채팅/LLM에 이어서 맡길 때: `docs\AI_HANDOFF.md` 를 먼저 읽으세요.
> 현장 설정: `config\site_example.json` (현장마다 복사해서 사용) · 새 도시: `vp.cmd new-site ...`

## 0. 한 번에 실행 (`auto`) — 기본 사용법

```bat
cd /d "D:\work\project\전처리된 데이터"
set VP=vision_preprocessing\vp.cmd

rem (1) 자동 단계 전부: 새 run을 만들고 끝까지 진행 (SITE_A 10,175장 기준 수십 분 예상, 중간에 끊겨도 됨)
%VP% auto --config vision_preprocessing\config\site_example.json --label site_a_full
rem    끊겼거나 검수 후 다시 돌릴 때: 같은 run으로 이어서 (캐시가 있어 빠름)
%VP% auto --run state_preprocessing_results\run_..._site_a_full

rem (2) 사람이 할 일 보기·처리 → 브라우저 http://127.0.0.1:8765/todo
%VP% review --run state_preprocessing_results\run_..._site_a_full

rem (3) 검수 창을 닫고(Ctrl+C) (1)의 두 번째 줄을 다시 실행 → 새 초안·export·할 일 갱신
```

`auto` 단계 (`--stage a,b` 로 일부만, `--start x` 로 x부터, `--limit N` 으로 N장 시험 실행):

| 단계 | 하는 일 | 사람 승인 필요? |
|---|---|---|
| ingest | manifest·ZIP 연결, 사진/JSON 해시 검사 | 아니오 |
| thumbs | 320x180 썸네일 + 밝기 분류(dark/low_light/ok) | 아니오 |
| views | 구도 경계 후보(6장 간격 비교 후 경계 정밀화, 외관 변화로 생긴 가짜 경계는 기준 사진 재비교로 병합) → 새 draft view / 승인된 view는 **제안**만 | 예: 제안 수락 |
| roi_drafts | ROI 없는 구도에 다른 구도 ROI를 대상별 정합으로 옮긴 **초안** | 예: ROI 승인 |
| drift | 구도별 하루 3장을 기준 사진과 비교. 0.5% 미만 무시, 0.5~1.5% 표시만, 1.5% 초과면 "ROI 다시 그려주세요" | 예: 다시 그리기 |
| timeline | 대상별 날짜 단계 후보(stable/transition/unknown/unanchored), 승인 라벨을 기준점으로 사용 | 아니오 (후보일 뿐) |
| split | 단계(변화/안정)별·날짜 묶음 train/val/test, 누수 검사 | 아니오 |
| samples | 표본: 안정일 2장/일, 변화일 6장/일, 지저분한 사진 약 15% | 아니오 |
| drafts | 표본에 라벨 **초안**(안정 구간 복사, 전체 채움, 변화 구간 복사+수정 필요) | 예: 초안 승인 |
| export | 승인된 라벨만 학습 export + 검증 + 대상별 "보이는 영역 안 설치 비율" 계산 | 아니오 |
| todo | `HUMAN_TODO.md`, `human_todo.json` (할 일 목록) | — |

- 자동 단계는 아무것도 승인하지 않습니다. 초안 작성자는 `auto`로 남고, `auto` 이름으로는 승인할 수 없습니다.
- 실행 잠금: `auto`와 검수 서버(`review`)는 같은 run을 동시에 쓰지 못합니다(`RUN_LOCK.json`). 비정상 종료로 남은 잠금은 같은 PC에서 자동으로 풀리며, 다른 PC에서 남긴 잠금은 `--force-unlock` 으로만 풉니다.
- 할 일 순서: 구도 → ROI(문·통로 포함) → 라벨. ROI를 나중에 고치면 그 ROI로 그린 라벨 승인이 무효화되므로, To-do 화면의 "ROI가 바뀌어 다시 승인할 라벨"에서 썸네일을 보고 한 번에 다시 승인합니다.
- 문·통로: 끝까지 남는 문·창·설비 개구부는 ROI 제외영역(이유 `door_opening`/`window`/`fixture`/`not_in_scope`)으로 두고, 나중에 보드로 막을 임시 통로는 ROI에 포함합니다(막히기 전 `not_installed`). 바닥 동선은 ROI에 두고 보호재는 `판단 불가/covered_by_protection` 으로 표시합니다.
- 승인 라벨이 근처에 없는 대상(예: 천장 C01)은 "직접 그릴 기준(앵커) 사진" 몇 장만 요청합니다. 그 사진을 그리고 승인한 뒤 auto를 다시 돌리면 나머지 표본에 초안이 생깁니다.
- 원본 qa_v3 run 포인터(`latest_active_run.json`)가 바뀌어도 run은 기록된 원본을 계속 씁니다. 새 원본을 쓰려면 새 run을 만드세요(검수 저장소는 공유되어 승인이 이어집니다).
- 임계값은 모두 `site_example.json`의 `view_detection / drift / timeline / sampling / split` 에 있고, 검증되지 않은 기본값으로 표시되어 있습니다.

---

## 1. 파일 구성 (전부 새 파일, 기존 코드는 수정하지 않았음)

```
전처리된 데이터\
  vision_preprocessing\
    vp.cmd                 기존 .venv 파이썬으로 CLI 실행 (패키지 설치·교체 없음)
    config\taxonomy.json   표면 종류, 객체 클래스 ID, 작업 정의(미확정 표시), 상태/가림 값
    config\site_example.json       SITE_A 현장 설정 (경로, 대상 후보, 임계값, split, export)
    schemas.py             상수·키·설정 로드·폴리곤 검증
    legacy.py              qa_v3 ExportDataset / qa_v2 registration 연결 (import만)
    store.py               SQLite 검수 저장소: 버전 이력, 승인, 무효화, 이벤트
    runctx.py              run 디렉터리, 경로 해석·기록, 진행 표시, resume 캐시
    source_adapter.py      R01 입력 연결·검사
    index_views.py         R02 구도(카메라 시점) 후보·구도 기록
    target_registry.py     R03 대상·작업 적용 여부·기준 ROI
    assign_surfaces.py     R04/R05 ROI 재사용·대상별 정합, 폴리곤 연결, 가림/화질 후보
    state_annotations.py   R06 상태 라벨 저장·학습 가능 판정·전파 초안
    review_app.py          R07 로컬 검수 서버 (127.0.0.1 전용)
    review_static\index.html  검수 UI
    rasterize.py           R08 마스크·좌표 변환·오버레이
    split_dataset.py       R09 누수 방지 분할
    export_dataset.py      R10 draft/train export, 오버레이
    validate_dataset.py    export 검증
    postprocess.py         추론/라벨 마스크 → 대상별 관찰 비율 → 전후 비교
    thumbs.py              썸네일·밝기 분류 (auto: thumbs)
    drift.py               구도 안 카메라 밀림·흔들림 검사 (auto: drift)
    roi_drafts.py          ROI 없는 구도에 대상별 정합 ROI 초안 (auto: roi_drafts)
    timeline.py            대상별 상태 변화 후보 (auto: timeline)
    sampling.py            표본 선택·라벨 초안 (auto: samples, drafts)
    todo.py                사람 할 일 목록 (auto: todo, /todo 화면)
    auto.py                전체 자동 실행·resume
    review_static\todo.html  할 일 화면
    cli.py, __main__.py    명령행
    train\train_quick.py   학습: 동결 인코더 + 디코더(unet / upernet / linear / mlp / dpt / probe) + 헤드 2개
    train\decoders.py      ViT 전용 디코더: 경량 MLP(SegFormer식) · DPT · linear probe
    train\infer_quick.py   인퍼런스: 사진 → 상태 맵 → ROI(주어진 것 / 자동 제안) → 설치율 → vision_output.json
    train\infer_core.py    인퍼런스 후처리 (ROI 제안 · 설치율 · 날짜별 행 · 이벤트, torch 불필요)
    train\loso\            현장 단위 교차검증(LOSO) 실험: 규격 · 분할 · 학습 · 추론 변형 · 시간축 보정 · 표/그림
    tests\                 합성 데이터 단위 테스트 (코드 동작 검증용)
    requirements_traceability.md
```

설정 파일이 YAML이 아닌 JSON인 이유: 기존 `.venv`(Python 3.13)에 PyYAML이 없고, 환경을 바꾸지 않기로 했기 때문입니다. PyYAML이 설치되어 있으면 `.yaml`도 읽습니다.

의존성: numpy, Pillow, shapely, opencv-python-headless, pyzipper — 모두 기존 `.venv`에 이미 있음.

## 2. 데이터 모델 (요약)

검수 저장소(`state_preprocessing_results\review_store\SITE_A.sqlite`)의 레코드 종류:

| kind | key | 주요 필드 |
|---|---|---|
| view | `V01` | camera_id, start_time/end_time(현지시각, 시간대 미확인), reference_image_id, classification_source(auto_candidate/config/manual) |
| target | `W01` | surface_type(wall/floor/ceiling), zone_id, name, identity_status(proposed/confirmed/unresolved/retired) |
| applicability | `W01\|wall_board_installation` | applicability(unknown/applicable/not_applicable), basis(근거) |
| roi | `W01\|V01` | reference_image_id, image_size, coord_space=original_pixel_xy, polygons[{exterior, holes}], exclusions[{points, reason}], roi_kind=measurement_roi |
| observation | `<image_id>\|W01` | 사람이 확정한 사진별 ROI(예: 정합 결과 확인) |
| state_label | `<image_id>\|W01\|wall_board_installation` | roi_ref(그린 ROI 버전), regions[{state, polygons}], fill_roi_state, undeterminable[{reason, polygons}], occluded[], whole_target_undeterminable, propagated_from |

- 모든 레코드는 수정할 때마다 새 버전이 생기고(이력 보존), 승인은 `(kind, key, version, content_hash, 의존 버전)`에 묶입니다.
- 상태 라벨 승인은 그린 시점의 ROI 버전에 의존합니다. ROI를 고치면 ROI 승인과 그 ROI로 그린 라벨 승인이 자동 무효화됩니다(`invalidated`).
- 자동 생성기는 사람이 쓴(manual) 레코드를 덮어쓸 수 없습니다. 승인에는 `auto/system/pipeline/claude` 같은 시스템 이름을 쓸 수 없습니다.
- 사진별 자동 관찰(ROI 재사용/정합 결과, 폴리곤 연결, 가림 후보, 화질 플래그)은 다시 계산할 수 있는 draft이므로 run 폴더(`stages/assign/observations.jsonl`)에 둡니다.

이미지 레코드(`stages/ingest/images.jsonl`): image_id(`SITE_A-<촬영시각>-<zip+entry 해시>`), site/camera, capture{time_local, status, timezone=null, timezone_confirmed=false, download_time_in_name, copy_suffix}, size, source_photo{zip, entry, sha256, sha256_verified}, annotation{zip, entry, sha256, qa_v3_classification, …}, checks/warnings, status(ok/held), content_group/duplicate_of.

### ROI 정의 (taxonomy.json의 roi_definitions)

- 측정 ROI: 평가할 대상 면·작업 구역 전체. 아직 설치되지 않은 부분과 가려진 부분도 포함합니다. 작업 대상이 아닌 문·개구부는 exclusions로 뺍니다.
- 객체 마스크: 기존 JSON의 wall/floor/ceiling 등입니다. ROI도 아니고 상태 정답도 아닙니다.
- 관찰 가능 영역: ROI에서 가림·판정 불가 영역을 뺀 나머지입니다. 가림이 있어도 ROI 자체를 줄이지 않습니다.

qa_v3 teacher 영역(BR04~BR07)은 "보이는 설치 구간"을 그린 초안이므로 ROI로 쓰지 않고, UI에서 참고 힌트로만 보여줍니다.

## 3. 클래스·값 매핑 (export `label_map.json`)

| 마스크 | 형식 | 값 |
|---|---|---|
| semantic_masks | PNG 8bit, uint8 | 0 wall, 1 floor, 2 ceiling, 3 metal_stud_framing, 4 hvac_ductwork, 5 construction_materials, 6 tools, 7 person, 255 ignore |
| target_masks | PNG 8bit uint8 (대상 ≤254), 아니면 16bit uint16 | 0 없음, 1..N = targets.json index, 255(65535) = ROI 겹침/패딩 |
| state_masks/<activity> | PNG 8bit uint8 | 0 not_installed, 1 installed, 255 ignore (`--occlusion-class` 사용 시 2 occluded) |
| visibility_masks | PNG 8bit uint8 | 0 ROI 밖, 1 보임, 2 가림, 3 판정 불가 |

- 기존 코드에는 객체 클래스 ID 매핑이 없었습니다. 그래서 SITE_A JSON에서 실제로 쓰인 라벨로 ID를 정하고 그 출처를 `taxonomy.json`에 적어 두었습니다. `unidentifiable`은 ignore로 처리합니다.
- 겹침 정책: 앞 레이어(foreground: person/tools/materials) > building_element(studs/HVAC) > surface 순으로 앞 레이어가 이깁니다(정당한 가림). 같은 레이어 안에서 다른 클래스가 겹치면 충돌로 보고 255로 둡니다. JSON 도형 순서는 쓰지 않습니다. 이 정책 자체는 `policy_status: unconfirmed_default`, 즉 아직 확정되지 않은 기본값입니다.
- 상태 마스크에서 255가 되는 경우: ROI 밖, 미검수, 미라벨, 가림(사람이 표시한 것과 객체 폴리곤 가림 후보), 판정 불가, installed/not_installed 충돌, 대상 간 충돌. 255는 모델 출력 클래스가 아닙니다.

## 4. 단계별 명령 (Windows, 기존 venv)

```bat
cd /d "D:\work\project\전처리된 데이터"
set VP=vision_preprocessing\vp.cmd

rem 0) 새 run 생성: latest_active_run.json을 읽어 원본 run 경로를 정하고, 설정·코드 해시를 run_config.json에 기록
%VP% init --config vision_preprocessing\config\site_example.json --label site_a_small
rem   -> 출력된 run dir를 RUN 변수에 넣기
set RUN=state_preprocessing_results\run_YYYYMMDDTHHMMSSZ_xxxxxx_site_a_small

%VP% ingest        --run %RUN% --limit 48           rem R01 (--dry-run, --dates 2025-02-18,2025-02-19, --select first)
%VP% index-views   --run %RUN% --create-draft-views rem R02 후보 + draft view (contact_sheet.jpg)
%VP% sync-registry --run %RUN%                      rem R03 W01/W02/F01/C01, 적용 여부 unknown (insert-only)
rem  (ROI는 검수 UI에서 view의 기준 사진 위에 그림)
%VP% assign        --run %RUN%                      rem R04/R05
%VP% overlay       --run %RUN% --limit 12
%VP% review        --run %RUN% --port 8765          rem R07 → http://127.0.0.1:8765/
%VP% split         --run %RUN%                      rem R09
%VP% export        --run %RUN% --mode draft --limit 6
%VP% export        --run %RUN% --mode train                       rem 전체 이미지
%VP% export        --run %RUN% --mode train --crop --size 512x512 rem 대상 ROI crop (벽·바닥·천장)
%VP% validate      --export %RUN%\exports\<export_dir> --run %RUN%
%VP% measure       --export %RUN%\exports\<train_export> --activity wall_board_installation [--pred-dir <예측 PNG 폴더>]
%VP% compare       --measurements %RUN%\exports\<train_export>\measurements_wall_board_installation.jsonl
%VP% status        --run %RUN%
```

`vp.cmd`는 `전처리된 데이터` 폴더 기준으로 동작하므로 `--run`에는 그 폴더 기준 상대경로나 절대경로를 넣습니다.
한 번에 돌려 보려면 `%VP% smoke --config vision_preprocessing\config\site_example.json --limit 24`를 쓰면 됩니다.

export 옵션: `--size WxH`, `--resize-mode letterbox|stretch|none`, `--crop`, `--occlusion-class`, `--no-auto-occluder-ignore`.

## 5. 로컬 검수 UI

```bat
%VP% review --run %RUN% --port 8765
```

브라우저에서 `http://127.0.0.1:8765/`을 엽니다. 서버는 로컬에서만 접근할 수 있고, 사진은 원본 ZIP에서 그때그때 읽으며 외부로 전송하지 않습니다.

1. 오른쪽 위에 검수자 이름을 입력합니다(필수).
2. View: 구간과 기준 사진을 확인·수정한 뒤 Approve. 기준 사진이 아닌 사진에서는 "Go to reference"로 이동합니다.
3. Target: identity를 `confirmed` 또는 `unresolved`로 정하고 Save → Approve.
4. Activity applicability: `applicable/not_applicable/unknown`과 근거를 입력하고 Save → Approve.
5. Measurement ROI: 기준 사진에서 "Draw ROI polygon"(클릭으로 점 추가, Enter나 더블클릭으로 닫기, Backspace는 점 취소, Esc는 그리기 취소)과 "Draw exclusion"(사유 입력)을 쓴 뒤 Save ROI draft → Approve ROI.
   - 이미 그린 ROI·제외 영역·상태 영역 수정: "Edit vertices" → 꼭짓점 드래그(이동) / 변 더블클릭(꼭짓점 추가) / 꼭짓점 우클릭(삭제) / 폴리곤 안 클릭 후 Delete 키(폴리곤 삭제). "Revert unsaved"는 저장 전 변경 취소. 수정 후 반드시 Save → 다시 Approve.
6. 다른 사진에서는 "Refresh observation"으로 ROI 재사용이나 대상별 정합 결과를 확인합니다. 정합으로 얻은 ROI는 "Confirm registered ROI"를 누른 뒤 승인해야 쓸 수 있습니다.
7. State label: installed / not_installed / occluded / undeterminable(사유 선택) 영역을 그리고, 필요하면 "fill rest of ROI"를 씁니다. Save label draft → Approve label 또는 Reject.
8. 레이어(objects / ROI / 가림 후보 / state / 연결 폴리곤 / qa_v3 힌트)는 따로 켜고 끌 수 있고, "compare with reference"로 기준 사진을 나란히 볼 수 있습니다. 확대는 마우스 휠, 이동은 Shift+드래그입니다.
9. 오른쪽 아래 "training eligibility"에서 학습 정답이 되지 못하는 이유를 바로 볼 수 있습니다.

저장 내용은 SQLite에 바로 기록되므로, 서버나 브라우저를 다시 열어도 수정 내용과 승인 이력이 그대로 남습니다.

## 6. 전체 데이터 실행과 resume

```bat
%VP% init   --config vision_preprocessing\config\site_example.json --label site_a_full
%VP% ingest --run %RUN%                 rem 10,175장, 사진·JSON 해시를 모두 검사
%VP% index-views --run %RUN%            rem 10분 간격 연속 프레임 비교 (config view_detection.stride로 간격 조절)
%VP% assign --run %RUN%
%VP% split  --run %RUN%
%VP% export --run %RUN% --mode train
```

- resume: 같은 `--run`으로 같은 명령을 다시 실행하면 됩니다. 입력 해시, 설정, 현재 코드 해시, ROI·view 버전이 같은 항목은 캐시를 그대로 쓰고, 달라진 항목만 다시 계산합니다(캐시 무효화). 중간에 멈춰도 처리한 항목은 jsonl에 한 줄씩 저장되어 있습니다.
- 진행 상황은 콘솔과 `<run>\progress.json`에 표시됩니다. 처리·저장·캐시·보류·실패 수가 나오고, 단위(images/observations)를 구분해서 적습니다.
- 설정을 바꿨다면 새 run을 만드는 것을 권장합니다. `status`로 snapshot과 현재 설정 파일의 차이를 확인할 수 있습니다.
- 검수 저장소는 run과 별도로 영속되므로, 새 run에서도 같은 승인을 그대로 씁니다.

## 7. 테스트

```bat
cd /d "D:\work\project\전처리된 데이터"
.venv\Scripts\python.exe -m unittest discover -s vision_preprocessing\tests -t . -v
```

합성 데이터로 49개 테스트(test_pipeline 26 + test_auto 23)를 돌리며, 코드 동작만 검증합니다. 실제 현장의 상태 인식 정확도를 보여주는 결과가 아닙니다.

## 학습 · 인퍼런스 (`train\`, torch · timm 필요)

```bat
rem 학습 (기본: 상태 헤드가 ROI 밖 비벽 물체도 '대상 아님'으로 배움 → 새 현장에서 ROI 자동 제안 가능)
.venv\Scripts\python -m vision_preprocessing.train.train_quick --export <run>\exports\<이름>_train_full --decoder unet
rem   비교: --decoder upernet / linear,  예전 방식: --state-outside roi

rem 인퍼런스 (구도 하나의 사진 폴더 단위)
.venv\Scripts\python -m vision_preprocessing.train.infer_quick --model <학습 결과>\model_quick.pt ^
    --images <사진 폴더> --site-id SITE_B --view V01 --out <출력 폴더>
rem   승인된 ROI가 있으면: --roi-mask <export>\target_masks\<사진>.png --roi-targets <export>\targets.json
```

- ROI가 없으면 처음 쓸 만한 사진 3장에서 "측정 가능한 벽(미설치·설치·가려짐)" 영역을 투표로 묶어 A01, A02 …(왼쪽→오른쪽)로 **제안**합니다.
  `provisional` 상태이므로 사람이 한 번 W01 … 로 대응시키고 확인해야 합니다. 같은 출력 폴더로 다시 돌리면 그 ROI를 그대로 씁니다.
- 설치율 = ROI 안 설치 ÷ (설치 + 미설치). 보이는 비율이 0.3 미만이면 `held`(값 없음, 0으로 채우지 않음).
- 결과: `vision_output.json`(에이전트 연동 형식 1.0), `photo_rows.jsonl`(사진별 값), `roi\`, `overlays\`(날짜별 첫 사진).
- 구도 변경(카메라 이동) 감지는 아직 인퍼런스에 없습니다. 구도별로 나눠서 실행하세요.

### LOSO 실험 (논문 본 실험, `train\loso\`)

현장 하나씩 빼고 학습해 처음 보는 현장에서 평가합니다(leave-one-site-out). 인코더·디코더·학습 레시피·시드·분할 규칙은
`train\loso\protocol.json`에 고정되어 있고, 실행하는 사람은 데이터 경로(local.json)만 적습니다.

```bat
python -m vision_preprocessing.train.loso check  --local <local.json>
python -m vision_preprocessing.train.loso run    --local <local.json> --models upernet,linear
python -m vision_preprocessing.train.loso status --local <local.json>
python -m vision_preprocessing.train.loso report --results <결과 폴더> <팀원 결과.zip> --out <summary> --lang ko
```

- `train\loso\PROTOCOL.md`: 무엇을 고정했고 왜(사전 예상 포함). `train\loso\DATA_SETUP.md`: 데이터 배치·실행·결과 전달(Linux/Windows).
- 분할 목록(사진 ID)은 개인 전달 번들에만 있고, 저장소에는 해시(`expected_digests.json`)만 있습니다.

