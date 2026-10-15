# AI handoff — construction-site state preprocessing (`vision_preprocessing`)

> **사용법 (사람용):** 새 채팅이나 다른 LLM에 이 파일 전체를 붙여 넣고, 이어서 하고 싶은 일을 말하면 됩니다.
> 가능하면 `NEW_SITE_GUIDE_ko.md`(사용 설명서)도 같이 붙여 넣으세요.
> 작동 방법·흐름(Part A)은 한국어, 기술 세부(Part B)는 영어로 적었습니다.
> 작성: 2026-10-14, SITE_A 라벨링 완료 시점.

**Instruction to the assistant reading this:** Part A describes how the user operates the system (Korean, keep using
Korean with the user). Part B is the technical ground truth (English). Follow the constraints in B.2 strictly.

---

# Part A — 작동 방법과 흐름 (한국어)

## A.1 사용자와 대화 방식
- 사용자 호칭: **유난님** ("사장님" 아님). 한국어로 대화.
- 원하는 방식: 짧고 단계별로, **복사해서 바로 쓰는 PowerShell 명령**, 사람 할 일은 최소로("자동·코드 중심").
- 큰 변경 전에는 먼저 계획을 말하고 확인받기. 새 기능마다 테스트. 기존 구조는 유지.

## A.2 폴더 구조 (묶음 zip 기준, `<작업폴더>`는 어디든 가능)
```
<작업폴더>\
  Data\                         원본 사진 ZIP + processed_json ZIP  (기밀, 묶음에 없음 → 따로 복사)
  전처리된 데이터\
    vision_preprocessing\        이 파이프라인 (vp.cmd 로 실행)
    qa_v2\ qa_v3\                기존 코드 (import만, 수정 금지)
    automatic_results\           qa_v3 자동 전처리 결과 (묶음에 없음 → 따로 복사, 약 6GB)
    state_preprocessing_results\ 실행 결과(run 폴더)와 검수 저장소(review_store\<현장>.sqlite)
    .venv\                       파이썬 환경 (새 PC에서 새로 만듦)
```
- 설정 파일에는 원래 PC 경로(`D:\work\project\...`)가 적혀 있지만, 그 경로가 없으면 **자동으로 위 구조로 바꿔 읽습니다**
  (`Data`가 `전처리된 데이터` 옆에 있으면 설정 수정 불필요).
- ZIP 파일 이름을 바꾸면 안 됨(사진 ID가 ZIP 이름으로 만들어짐).

## A.3 전체 흐름
두 단계로 나뉩니다.
| 단계 | 실행 | 하는 일 | 결과 |
|---|---|---|---|
| **1차 자동 전처리 (qa_v3)** | `전처리_실행.cmd` (메뉴) | 사진 ZIP + 원본 JSON ZIP → 객체 폴리곤 자동 보정(형상 복구·중복 정리·벽/천장 의미 보정), 사진-JSON 연결 | `automatic_results\run_*` + `latest_active_run.json`. 4개 도시 36,862장 완료(약 5시간). **복사해 왔으면 다시 안 돌려도 됨** |
| **2차 상태 전처리 (vision_preprocessing)** | `vp.cmd auto` + `vp.cmd review` | 아래 표 | 학습 데이터, 진행률 |

1차 단계를 새 PC에서 돌릴 때: `python -m vision_preprocessing.tools.relocate_qa_v3_project --apply` 로 qa_v3 설정의 ZIP 경로를 먼저 맞춤
(새 도시는 `--add-zip`). 실행 중 ZIP 암호를 물어봄(저장 안 함). 상태 `전처리_상태.cmd -Watch`, 중단 `-Stop`, 이어하기 `전처리_실행.cmd -Resume <run>`.

2차 단계 흐름:
```
[자동] auto ─▶ [사람] 할 일 화면(/todo)에서 검수 ─▶ [자동] auto 다시 ─▶ [사람] 검수 ─▶ … ─▶ 할 일 "초안 검수" 0건 = 끝
```
| 단계 | 자동(auto)이 하는 일 | 사람이 하는 일 |
|---|---|---|
| 1 | 사진 확인, 썸네일, 카메라 구도(view) 구분 | 구도 경계 제안 수락, 구도 승인 |
| 2 | 다른 구도 ROI를 옮긴 초안(가능할 때) | 구도×대상마다 ROI 그리기/승인 (문·창은 제외영역, 임시 통로는 포함) |
| 3 | — | 대상 확정, 공정 적용 여부 (이미 끝난 공정은 `not_applicable`) |
| 4 | 상태 변화 후보, 표본 선택(안정 2장/일, 변화 6장/일, 지저분한 사진 15%) | 기준(앵커) 사진 몇 장 그리기 |
| 5 | 라벨 초안(안정 구간 복사 / 변화 구간 복사+수정 필요) | Grid에서 초안 승인 (**앞쪽 날짜부터 고치고 일괄 승인**) |
| 6 | 학습 데이터 export, 검증, 진행률·전날 대비, 이상 라벨 표시 | 이상 라벨 확인(급변·역행) |

## A.4 명령 (모두 `<작업폴더>\전처리된 데이터` 에서)
```powershell
# 처음: 새 run 만들고 전체 자동 실행
vision_preprocessing\vp.cmd auto --config vision_preprocessing\config\site_b.json --label site_b_full
# 이어서/다시: 같은 run (마지막 줄에 나온 run 폴더 경로를 그대로 사용, $변수는 새 창에서 사라짐)
vision_preprocessing\vp.cmd auto --run "state_preprocessing_results\run_..._site_b_full"
# 검수 화면 → 브라우저 http://127.0.0.1:8765/todo
vision_preprocessing\vp.cmd review --run "state_preprocessing_results\run_..._site_b_full"
# 새 도시 설정 만들기
vision_preprocessing\vp.cmd new-site --site-id SEOUL --name "Example Store #200 Seoul" --zip-contains "#200_-_Seoul"
# 테스트 (합성 데이터, 코드 동작만 확인)
.venv\Scripts\python.exe -m unittest vision_preprocessing.tests.test_pipeline vision_preprocessing.tests.test_auto
```
- auto와 검수 화면은 동시에 못 켬(실행 잠금). 검수 창 Ctrl+C → auto.
- 결과: `<run>\exports\<최신>_train_full\` (이미지·마스크·splits·metadata·measurements·daily_progress), 할 일: `<run>\HUMAN_TODO.md`

## A.5 현재 상태
| 현장 | 설정 | 상태 |
|---|---|---|
| SITE_A (SITE_A) | site_example.json | **완료**: 구도 4, ROI 16, 승인 라벨 1,216. 바닥·천장은 촬영 전 완공 → 적용 안 함. 학습: 벽 W01·W02, 라벨 798, 사진 399 |
| SITE_B (SITE_B) | site_b.json | 설정만 준비 (9,916장) — 다음 추천 (교차 현장 test용) |
| SITE_C (SITE_C) | site_c.json | 설정만 준비 (7,362장) |
| SITE_D (SITE_D) | site_d.json | 설정만 준비 (9,407장) |

SITE_A 진행률(라벨 기준): W01 0%→71%(02-19)→97%(02-21)→100%(03-14), W02 0%→100%(02-19).

## A.6 남은 큰 일
1. 다른 현장 라벨링 (SITE_B부터)
2. U-Net 학습 → 전체 사진 추론 → 백엔드로 진행률(형식은 지금 `daily_progress`와 같음, `state_source=prediction`)
3. 페인트: 진행률이 아니라 "칠해진 날" 이벤트로 (벽 95% 이상 2일 → 변화일 후보 → LLM/VLM 확인). 기밀 때문에 로컬 VLM vs 승인된 외부 API 결정 필요
4. 어안 왜곡 보정(나중에)

---

# Part B — Technical reference (English)

## B.1 Goal
From fixed construction-site camera photos (RGB, fisheye, ~every 10 min) build human-approved training data for a
segmentation model (multi-head U-Net is only a proposal; preprocessing must not depend on a specific encoder/head) that
reports, per physical target and activity, the **installed share within the observable part of the target's
measurement ROI**.

## B.2 Hard constraints (keep them)
- Never modify original ZIPs, the processed JSON ZIP, `automatic_results`, qa_v2/qa_v3 code; never overwrite polygons;
  new data lives in separate files / the review store.
- Confidential data: local processing only; no upload of photos/labels to external APIs or clouds. Review server binds
  to 127.0.0.1 only.
- Automatic output is a draft; automatic success is never human approval. Approvals require a named human reviewer
  (system names such as auto/system/pipeline/claude are refused).
- Do not hardcode the source run: resolve `automatic_results/latest_active_run.json`; new outputs go to unique run dirs.
- Do not invent process definitions; unconfirmed things stay marked unconfirmed (taxonomy `definition_status`).
- Do not claim real accuracy from synthetic tests.
- Measurement ROI ≠ object mask ≠ observable region. ROI = whole target zone incl. not-yet-installed and occluded
  parts; never shrink it for occlusion; permanent openings are explicit exclusions; temporary passages stay in the ROI.
- Percent ≠ m² progress ≠ store-wide progress. Do not force earlier states onto later dates; hold non-comparable pairs.

## B.3 Data model
1. Object type: qa_v3 pseudo-label polygons (read-only) → semantic mask 0 wall, 1 floor, 2 ceiling,
   3 metal_stud_framing, 4 hvac_ductwork, 5 construction_materials, 6 tools, 7 person, 255 ignore
   (layer policy foreground > building_element > surface; same-layer conflict → 255).
2. Physical target `W01/W02/F01/C01…` with per-view measurement ROI (`roi` key `W01|V01`, polygons + exclusions).
3. Per-activity state label (`state_label` key `<image_id>|<target>|<activity>`): regions installed/not_installed,
   fill_roi_state, occluded, undeterminable(reason), whole_target_undeterminable, roi_ref (exact ROI version).

Review store: SQLite per site (`state_preprocessing_results/review_store/<SITE>.sqlite`), append-only versions,
approvals bound to (version, content hash, dependency versions; label → ROI version), automatic invalidation,
manual records protected from automatic writers, journal mode TRUNCATE.
Image id = `<SITE>-<capture time>-<sha1(zip basename + "/" + entry)[:8]>` → renaming ZIPs orphans labels.
Masks: target uint8/uint16 (0 none, max = overlap/pad), state uint8 (0/1, 255 ignore, optional 2 occluded),
visibility (0 outside, 1 visible, 2 occluded, 3 undeterminable). Nearest-neighbour resize; transforms recorded.

## B.4a Stage 1 — qa_v3 automatic preprocessing (pre-existing code, not written in this project; do not modify)
`run_auto_preprocess.ps1` / `전처리_실행.cmd` → `qa_v3/auto_preprocess.py --workspace automatic_reference_v3 --out automatic_results`
(imports qa_v2). Input: photo ZIPs + `processed_json` ZIP listed with absolute paths in `automatic_reference_v3/project.json`
(plus 12 teacher references BR04–07, CO05–07, LV01/04, VE01–03 — assistant visual drafts, not GT). Encrypted ZIPs:
password from prompt or `QA_ZIP_PASSWORD`, never stored. Output per run: `manifest.jsonl`, `classification.csv`,
`summary.json`, `chunks/part_*/annotations/{auto_fixed,kept,needs_review,rejected}.zip`; pointer
`automatic_results/latest_active_run.json`. Checkpoint/resume, safe stop (`STOP` file), `--max-minutes`, `--min-free-mb`.
Last complete run, renamed to `automatic_results/1차결과_qa_v3_전체4도시_20261009` (original
`run_20261009T163018_494922Z`, recorded in its `FIRST_STAGE_RUN.json`; manifest paths still name the original folder and
`runctx.first_stage_rename_map` maps them; `find_renamed_first_stage_run` resolves runs that recorded the old name;
rename tool `tools/rename_first_stage_run.py`, pointer repair in `relocate_qa_v3_project`): 36,862 photos (4 sites), 5 h 04 min, needs_review 36,668 /
auto_fixed 192 / rejected 12. Categories are pseudo-label states, not human ground truth; stage 2 reads
auto_fixed/kept/needs_review. On a new machine run `python -m vision_preprocessing.tools.relocate_qa_v3_project --apply`
(rewrites the Data prefix in project.json and repoints stale latest_*_run.json pointers, keeps .bak files) before stage 1.

## B.4 Stage 2 pipeline (`vp auto`; resumable; `--stage a,b`, `--start x`, `--limit N`)
| stage | module | behaviour |
|---|---|---|
| ingest | source_adapter | manifest + ZIPs, sha256/size checks, capture time from file name (download suffix ≠ capture time), duplicates |
| thumbs | thumbs | 320×180 thumbnails, light class dark/low_light/ok |
| views | index_views | sequential SIFT/RANSAC on every 6th usable frame (coarse_stride) + boundary refinement; unresolved boundaries re-checked on segment references with relaxed matching and merged when aligned; `reconcile_views` updates only unapproved auto views, approved/manual views get proposals; unmatched auto views retired |
| roi_drafts | roi_drafts | missing ROIs drafted by per-target registration (qa_v2.registration) from another view (unapproved) |
| drift | drift | 3 photos/day vs view reference: <0.5% ok, 0.5–1.5% minor, >1.5% redraw request; within-day spread ≥1.5% = severe shake |
| timeline | timeline | ROI grid descriptors (Lab rel. frame brightness + edge density) on thumbnails, day-to-day robust z-scores, anchors = approved labels → phases stable / transition / unknown / unanchored / anchor_change / stable_extrapolated; leave-one-anchor-out self-check |
| split | split_dataset | `phase_stratified`: day blocks (change 1 day, stable 3 days), per phase class cut by fractions in seeded-hash order; union-find on sha + view/day; leakage check |
| samples | sampling | stable 2/day, change 6/day, ~15% messy, labelled photos always kept |
| drafts | sampling | stable → copy nearest approved label (same ROI version; source split = same or train; no change between) or whole-ROI fill (hint 0/1); change → copy nearest earlier ("needs editing"); else ≤6 anchor requests per view×target; never approves |
| export | export_dataset, validate_dataset, postprocess | eligible labels only; masks; validation; `measurements_<activity>.jsonl`; `daily_progress` (vs previous usable day, ±5%p, decrease = `decrease_check`), only applicable target/activity pairs |
| todo | todo | `HUMAN_TODO.md`, `human_todo.json`; `label_consistency` flags backwards (>15%p) or jumps (>40%p within 3 h) |

Training eligibility (all): target approved + confirmed; applicability approved + applicable; ROI and view approved
(or manual observation approved); label approved and on the current ROI version; not propagated from val/test into train.

Review UI (`vp review`, 127.0.0.1:8765): `/` editor (samples filter, list 300/page, grid 120/page with thumbnails,
canvas ROI/label editing, "same as previous → approve"), `/todo` (thumbnails, accept view proposal, ROI-change rebase
+ re-approve, "replace with compared state + approve"). `RUN_LOCK.json` prevents auto and the server on one run.

Relocation: `runctx.auto_relocation_map` maps the configured `preprocess_root` to the folder containing
`vision_preprocessing`, and its parent to that folder's parent (so `Data/` beside it works) when the configured path
does not exist; explicit `path_map` entries win. Cache fingerprints include per-stage code hashes (code edits invalidate
only dependent caches).

## B.5 Decisions and defaults (user-approved)
Image-id stabilisation: left as is. Drift: ask the user to redraw above 1.5%. Shakes: report only if severe.
Messy samples kept (~15%). Doors/permanent openings = exclusions; temporary passages included. Semantic masks auxiliary;
state head first. Split phase-stratified. Fisheye undistortion deferred. Painting → post-installation event via
LLM/VLM (confidentiality decision pending). Floors finished before the camera period / covered by protection →
`not_applicable` (done at SITE_A for F01 and C01). All thresholds live in the site config and are marked unvalidated.

## B.6 Status and limitations
- SITE_A done (see A.5). Final export `20261014T062524Z_bcf8_train_full`: 399 photos, 798 eligible labels,
  train 256 / val 58 / test 85, validation passed. Wall installation spans only ~3 days → test has no partial samples;
  use another site as a cross-site test.
- Not implemented: model training/inference, prediction comparison events, painting event detector, fisheye
  undistortion.

## B.7 Lessons learned
- Bulk "same as previous" copies the most recent earlier approved label → fix earlier photos first (13 wrong labels at
  SITE_A, caught by `label_consistency`).
- State can change within a day → `anchor_change` days are not copied within the day.
- Change-period drafts must use the nearest earlier approved label.
- PowerShell variables vanish in new windows → give full paths.
- `ConnectionAbortedError 10053` = browser cancelled a request; harmless (silenced).
- Folders where deletion is not permitted (cloud-synced / mounted): writers avoid deletes (journal TRUNCATE, lock
  files renamed instead of removed).

## B.8 Verification
`python -m unittest vision_preprocessing.tests.test_pipeline vision_preprocessing.tests.test_auto` (49 synthetic
tests; behaviour only). On real data check `export_report.json`, `validation_report.json`, `HUMAN_TODO.md`,
`daily_progress.csv`, and that sha256 of original ZIPs / manifest are unchanged before vs after.

## B.9 File map
`vision_preprocessing/`: auto, cli, runctx (RunContext, ItemCache, RunLock, Progress, relocation), store, schemas,
source_adapter, legacy (imports qa_v3 dataset_io / qa_v2 registration), thumbs, index_views, roi_drafts, drift,
assign_surfaces, timeline, split_dataset, sampling, state_annotations, target_registry, rasterize, export_dataset,
validate_dataset, postprocess, todo, review_app, review_static/{index,todo}.html,
config/{taxonomy,site_example}.json, tests/, docs/{NEW_SITE_GUIDE_ko,AI_HANDOFF}.md,
tools/relocate_qa_v3_project.py, tools/rename_first_stage_run.py, README.md (Korean usage), requirements_traceability.md.
Project root also holds stage-1 launchers: run_auto_preprocess.ps1, 전처리_실행.cmd, 전처리_상태.cmd, README_AUTO.txt,
README_OPERATIONS.txt, automatic_reference_v3/project.json.
