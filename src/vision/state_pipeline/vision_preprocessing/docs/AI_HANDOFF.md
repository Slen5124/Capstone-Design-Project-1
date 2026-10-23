# AI handoff — construction-site state preprocessing (`vision_preprocessing`)

> **사용법 (사람용):** 새 채팅이나 다른 LLM에 이 파일 전체를 붙여 넣고, 이어서 하고 싶은 일을 말하면 됩니다.
> 가능하면 `NEW_SITE_GUIDE_ko.md`(사용 설명서)도 같이 붙여 넣으세요.
> 작동 방법·흐름(Part A)은 한국어, 기술 세부(Part B)는 영어로 적었습니다.
> 작성: 2026-10-14 (SITE_A 라벨링 완료), **갱신: 2026-10-21** (학습·추론 코드, SITE_A 라벨 전파, SITE_C ROI 완료·앵커 라벨링 중, 검수 화면 안정화).
> 시스템 전체(백엔드·에이전트·논문·팀 역할)는 같은 폴더의 `PROJECT_CONTEXT.md` 를 먼저 읽으세요.

**Instruction to the assistant reading this:** Part A describes how the user operates the system (Korean, keep using
Korean with the user). Part B is the technical ground truth (English). Follow the constraints in B.2 strictly.

---

# Part A — 작동 방법과 흐름 (한국어)

## A.1 사용자와 대화 방식
- 사용자 호칭: **유난님** ("사장님" 아님). 한국어로 대화.
- 원하는 방식: 짧고 단계별로, **복사해서 바로 쓰는 PowerShell 명령**, 사람 할 일은 최소로("자동·코드 중심").
- 큰 변경 전에는 먼저 계획을 말하고 확인받기. 새 기능마다 테스트. 기존 구조는 유지.
- **"의견먼저 제시, 내가 해줘 하면 실행"** — 선택지·의견을 먼저, 실행은 요청이 있을 때.
- **"뭐 안되면 나한테 이야기 해, 토큰 잡아먹지 말고"** — 막히면 오래 파지 말고 바로 보고.
- 사용자 터미널은 **PowerShell** (`cd /d`, `rem`, `%VAR%` 같은 cmd 문법 금지). 명령은 한 줄씩 `.\vision_preprocessing\vp.cmd ...`.
- **Git 명령은 사용자가 직접 친다** → 복사해서 붙일 명령만 준다. GitHub 에 올리기 전 민감 정보 목록을 보고하고 확인받는다.
- 사용자 대신 검수 store 를 고칠 때: **먼저 sqlite 백업**(`<SITE>.sqlite.bak_<날짜>_<내용>`), author `yunhan` + note
  `"decision by yunhan (...), recorded by assistant at yunhan's request (<날짜>)"`. 자동 결과(예: 호모그래피로 옮긴 ROI)는 승인하지 않고 초안으로 둔다.

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
# 암호 ZIP(SITE_C·SITE_D) 사진을 미리 풀어 검수 캐시에 저장 (검수 서버 켜둔 채 다른 창에서 가능, 이어서 됨)
vision_preprocessing\vp.cmd prewarm --run "state_preprocessing_results\run_..._site_c_full"
# 학습 / 추론 (conda ksl 환경 = torch+timm. vp 도구는 .venv)
python -m vision_preprocessing.train.train_quick --export <run>\exports\<이름> --decoder unet --out <out>
python -m vision_preprocessing.train.infer_quick --model <out>\model_quick.pt --images <사진폴더> --site-id SITE --view V01 --out <out2>
# SITE_A 라벨 전파 (안정 구간 사진에 라벨 복사 → 새 export)
python -m vision_preprocessing.tools.propagate_labels plan --run <run> --export <export> --store <sqlite>
python -m vision_preprocessing.tools.propagate_labels build --run <run> --plan <run>\propagation\plan.json --name <이름> --reuse-draft
# 테스트 (합성 데이터, 코드 동작만 확인)
.venv\Scripts\python.exe -B -m unittest discover -s vision_preprocessing/tests -t .
```
- auto와 검수 화면은 동시에 못 켬(실행 잠금). 검수 창 Ctrl+C → auto.
- 결과: `<run>\exports\<최신>_train_full\` (이미지·마스크·splits·metadata·measurements·daily_progress), 할 일: `<run>\HUMAN_TODO.md`

## A.5 현재 상태 (2026-10-21)
| 현장 | 설정 | 상태 |
|---|---|---|
| SITE_A (SITE_A) | site_example.json | **완료**: 구도 4, ROI 16, 승인 라벨 1,216. 바닥·천장 `not_applicable`. 최종 export `20261014T062524Z_bcf8_train_full` (사진 399, 라벨 798, train 256/val 58/test 85). **라벨 전파 v2** `exports\site_a_propagated_v2` (490장: train 386 / val 104, 손실 가중치 0.5). 틀린 사람 라벨 2장(02-19 04:21, 05:31)은 앵커에서 빼고 학습에서 제외(`propagation\exclude_human_wrong.txt`), store 는 그대로 |
| SITE_C (SITE_C) | site_c.json | run `run_..._site_c_full`. 구도 44 (V26·V38·V39 학습 제외), ROI 208 전부 승인, 카메라 밀림 4일 "ROI 그대로" 처리, V11 → V11 + V44 분리, V41 W03↔W04 교체. **표본 200 / 앵커 371건(벽 295, 천장 73) 라벨링 진행 중**(V01부터). C01 천장 공정을 할지 미정 |
| SITE_B (SITE_B) | site_b.json | 설정만 준비 (9,916장) |
| SITE_D (SITE_D) | site_d.json | 설정만 준비 (9,407장, 암호 ZIP) |

SITE_A 진행률(라벨 기준): W01 0%→71%(02-19)→97%(02-21)→100%(03-14), W02 0%→100%(02-19).

SITE_A 학습 비교 (`train_quick\cmp_human`, 사람 라벨만, test): state mIoU 0.952, occluded IoU 0.48, ROI 설치율 MAE 0.0043 (최대 0.0375),
사진 단위 MAE 0.027. `cmp_propagated` 는 v1(34장, 깨진 데이터)로 돌아가서 무의미 → **v2 로 다시: `--out ...\cmp_propagated_v2`** (사용자 실행 대기).

## A.6 남은 큰 일
1. SITE_C 앵커 라벨링 → auto 재실행 → 초안 Grid 검수 → export → (필요하면) 라벨 전파
2. SITE_A `cmp_propagated_v2` 재학습 → 사람 라벨만 / 전파 포함 / 디코더(U-Net·UPerNet·Linear) 비교 → 논문 실험표
3. 교차 현장 test (SITE_A 학습 → SITE_C 평가). 그 결과로 `vision_output.model.validation.installed_ratio_mae` 채움
4. SITE_B, SITE_D 라벨링
5. 백엔드·에이전트 연동: 공정표 작업 ID ↔ 비전 대상(W01…) 매핑, 설치율 ≠ 공정률 기준, 카메라 위치 ↔ 평면도
6. 페인트: "칠해진 날" 이벤트 (installation_complete 이후 VLM/LLM 판정, 기밀 때문에 로컬 우선)
7. 어안 왜곡 보정(나중에)

## A.7 결정 기록 (2026-10-15 ~ 10-21, 사용자 결정)
- **변경 1 (학습 대상 확장)**: ROI 밖의 벽 아닌 물체 픽셀은 state `not_target`(3)으로 학습, ROI 밖 벽/스터드는 255. 예전 방식은 `--state-outside roi`.
- 추론: ROI 를 주거나, 없으면 처음 쓸 만한 사진 3장의 다수결로 제안(A01, A02 … 왼쪽→오른쪽, 사람이 W01… 로 매핑하기 전까지 provisional).
- SITE_A 라벨 전파: 연속한 사람 승인 앵커 사이(같은 구도·split, |Δ| ≤ 0.02, 간격 ≤ 24 h), 밝기 정상·drift ok/minor 인 사진 하루 8장.
  사용자가 엑셀(`propagation\구간_검토_v2.xlsx`)로 구간 확인·수정(9번 1,2 → 8번 값, 10번 2,3 → 9번 3번, 40번 1,2 → 39번 1,2).
- SITE_C: V38·V39 "쓰지 말자" (모든 대상 roi_skip + view_exclusion), V26 학습 제외, V07 10/08 · V11 10/17 · V31 11/07 · V35 11/11 "ROI 그대로".
  V11 10/17 은 이후 카메라 이동 확인 → **10/14 폴리곤을 10/17 위치로 옮겨 V44 생성**(01:36~06:47, ROI 는 사용자가 확인·승인).
  V42 가 맞고 V41 을 수정(W03↔W04 맞바꿈). V37 W03 은 사용자가 새로 그린 ROI 사용(안 보임 해제). F01 은 not_applicable 이라 겹침 무시.
- 앵커: 구도마다 처음·중간·마지막 날. "앞 사진과 같음 → 복사+승인" 으로 같은 상태 사진 처리.
- 암호 ZIP: 전체 사진을 한 번 풀어 검수 캐시(`<run>\cache\display`)에 저장(prewarm). 암호 없는 사본 폴더(전체 해제)는 보안상 보류.

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
(plus 12 teacher references BR04–07, CO05–07, SITE_C01/04, VE01–03 — assistant visual drafts, not GT). Encrypted ZIPs:
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
| align | view_align | EVERY photo vs its view reference (display cache 1600 px → 960, SIFT+RANSAC; pass 2 vs aligned photos of similar brightness / nearby time so day-vs-night references do not count). Runs of shifted photos (> 1.5 %, ≥ 3; merged across ≤ 2 aligned photos) → to-do `view_misaligned` with buttons trim / split (new view, ROI mapped by homography as DRAFT) / reref. SITE_C: 9 real moves found, 2026-10-22 |
| timeline | timeline | ROI grid descriptors (Lab rel. frame brightness + edge density) on thumbnails, day-to-day robust z-scores, anchors = approved labels → phases stable / transition / unknown / unanchored / anchor_change / stable_extrapolated; leave-one-anchor-out self-check |
| split | split_dataset | `phase_stratified`: day blocks (change 1 day, stable 3 days), per phase class cut by fractions in seeded-hash order; union-find on sha + view/day; leakage check |
| samples | sampling | stable 2/day, change 6/day, ~15% messy, labelled photos always kept |
| drafts | sampling | stable → copy nearest approved label (same ROI version; source split = same or train; no change between) or whole-ROI fill (hint 0/1); change → copy nearest earlier ("needs editing"); else ≤6 anchor requests per view×target; never approves |
| export | export_dataset, validate_dataset, postprocess | eligible labels only; masks; validation; `measurements_<activity>.jsonl`; `daily_progress` (vs previous usable day, ±5%p, decrease = `decrease_check`), only applicable target/activity pairs |
| todo | todo | `HUMAN_TODO.md`, `human_todo.json`; `label_consistency` flags backwards (>15%p) or jumps (>40%p within 3 h) |

Review store record kinds added 2026-10-20: `view_exclusion` (key view_id; views out of samples/drafts/export,
export split `excluded_view`), `drift_ack` (key `V|day`; drift day accepted, ROI kept), `todo_ack` (key `<section>|<item>`;
"checked, leave as is" on the to-do page; ROI-conflict acks embed both ROI versions so an edit re-opens them).
To-do sections added: `roi_conflicts` (overlap ≥ 10 % of the smaller ROI or a copied polygon; not-applicable targets
ignored), `roi_audit` (findings from `stages/roi_audit/findings.json` written by a visual audit + automatic
"skip and ROI both exist"; a finding disappears when one of its ROIs gets a new version, its view is excluded, or it is
acked), `unassigned_photos` grouped into gaps between views, `needs_drawing` filtered live (approved anchors drop out,
saved-unapproved marked) and grouped per view with a "same as previous → copy + approve" button; ceiling/floor folded.

Training eligibility (all): target approved + confirmed; applicability approved + applicable; ROI and view approved
(or manual observation approved); label approved and on the current ROI version; not propagated from val/test into train.

Review UI (`vp review`, 127.0.0.1:8765): `/` editor (samples filter, list 300/page, grid 120/page with thumbnails,
canvas ROI/label editing, "same as previous → approve"), `/todo` (thumbnails, accept view proposal, ROI-change rebase
+ re-approve, "replace with compared state + approve"). `RUN_LOCK.json` prevents auto and the server on one run.

Relocation: `runctx.auto_relocation_map` maps the configured `preprocess_root` to the folder containing
`vision_preprocessing`, and its parent to that folder's parent (so `Data/` beside it works) when the configured path
does not exist; explicit `path_map` entries win. Cache fingerprints include per-stage code hashes (code edits invalidate
only dependent caches).

## B.4b Model, training, inference (`train/`, `tools/propagate_labels.py`)
- `train_quick.TwoHeadSeg`: frozen timm encoder (default `convnext_tiny.fb_in22k`; DINOv3 needs Meta access),
  decoders `unet` / `upernet` / `linear`, heads semantic 8 + state 4 (0 not_installed, 1 installed, 2 occluded,
  3 not_target; 255 ignore). `--state-outside full` (default) / `roi`; `--propagated-weight 0.5`; `--exclude-ids @file`;
  per-photo weighted CE; val/test use human labels only. Checkpoint stores encoder, decoder, n_state, state_outside,
  activity, export_size. Training env: conda `ksl` (torch+timm). Neither the cloud sandbox nor the device VM has torch.
- `infer_core` (torch-free) + `infer_quick`: ROI given (`--roi-mask/--roi-targets`) or reused or proposed;
  installed_ratio = installed / (installed + not_installed) in the ROI; held if observed_fraction < 0.3; daily median;
  change increase ≥ +0.02, decrease_suspect ≤ −0.05; events installation_complete (≥ 0.98 on 2 consecutive observation
  days), large_increase (≥ +0.3), decrease_suspect, long_gap (> 3 days, never interpolated). Output `vision_output/1.0`
  (schema + example in the agent folder of the context package).
- `propagate_labels plan|build`: sandwich rule (see A.7); ROI from the anchor's target mask; occluders recomputed from the
  photo's own SAM3 semantics (sem 5/6/7 → visibility 2, state 255); `label_source: propagated`.

## B.5 Decisions and defaults (user-approved)
Image-id stabilisation: left as is. Drift: ask the user to redraw above 1.5%. Shakes: report only if severe.
Messy samples kept (~15%). Doors/permanent openings = exclusions; temporary passages included. Semantic masks auxiliary;
state head first. Split phase-stratified. Fisheye undistortion deferred. Painting → post-installation event via
LLM/VLM (confidentiality decision pending). Floors finished before the camera period / covered by protection →
`not_applicable` (done at SITE_A for F01 and C01). All thresholds live in the site config and are marked unvalidated.

## B.6 Status and limitations
- See A.5 / A.7. Implemented since 10-14: training, inference, label propagation, SITE_C views/ROIs, review hardening.
- Not implemented: cross-site validation numbers, painting event detector, fisheye undistortion, `camera_moved` event in
  inference output (the agent guide lists it; inference does not emit it yet).

## B.7 Lessons learned
- Bulk "same as previous" copies the most recent earlier approved label → fix earlier photos first (13 wrong labels at
  SITE_A, caught by `label_consistency`).
- State can change within a day → `anchor_change` days are not copied within the day.
- Change-period drafts must use the nearest earlier approved label.
- PowerShell variables vanish in new windows → give full paths.
- `ConnectionAbortedError 10053` = browser cancelled a request; harmless (silenced).
- Review UI (2026-10-20/21): one background worker refreshes one photo per lock hold; store-only POSTs (decide, ROI,
  label, acks) run without the photo lock; ZIP reads use their own `read_lock`; after a label save/approve only that
  photo's list row is patched (the full SITE_C list is 4.6 MB). Startup refreshes only stale observations (`queue_stale`).
- Label/ROI saves used to fail on self-crossing polygons or points outside the photo → `schemas.sanitize_polygons`
  clamps/repairs (valid polygons are kept byte-identical so re-saving creates no new version) and reports `fixed`.
- A label save recomputes that photo's observation first if it is stale (`fresh_obs`), so the background queue can
  never make a save fail. Every refused/failed POST goes to `<run>/review_errors.log` (JSON lines) — read it first.
- Review UI (2026-10-23): "version N is not current" on Approve — the server answers with the current version and
  its author (`StaleVersion` → `{"current": ...}`); if it is the reviewer's own save the UI offers to approve that one
  in a confirm. `/api/roi` returns `record` so the Approve button switches to the new version at once. The
  observation refresh reads photos from `cache/display` (`CachedPhotoReader`, ZIP handles kept open) instead of
  decrypting the ZIP per photo; `/api/images` is cached as bytes and only refreshed rows are rebuilt (`_dirty_rows`).
  `openImage` / the background poll drop answers for a photo that is no longer open (arrow-key races).
  **Observation refresh cost**: cross-view ROI registration (`register_cross_view`) is now attempted only for
  measured targets without a "not visible" mark in that view, and only against the 2 nearest views in time — a
  not_applicable floor (F01) used to trigger full-resolution registrations against every other view for every photo
  (seconds per photo), and each refresh opened its own ZIP reader, which asked for the ZIP password on the server
  console from the background thread (the reason the UI "hung" after an ROI save). Refresh is now ~0.3-0.5 s/photo.
- Overlap guard (≥ 50 %) on ROI save asks for confirmation instead of blocking (swapping two walls needs it); a polygon
  copied from another target is still refused. ROI saves must be on the view's reference photo.
- The draft-merge bug (old target's polygon restored after a target switch) is fixed in UI and server; check
  `roi_conflicts` after any ROI session.
- ZipCrypto (SITE_C, SITE_D) is decrypted byte by byte in Python (~1 s/photo) → `vp prewarm` once, in parallel.
- Rendering polygons on the reference photo can mislead: a construction laser line looked like a polygon spike (V37).
- Folders where deletion is not permitted (cloud-synced / mounted): writers avoid deletes (journal TRUNCATE, lock
  files renamed instead of removed).

## B.8 Verification
`python -B -m unittest discover -s vision_preprocessing/tests -t .` (71 synthetic tests, 3 skipped without torch;
behaviour only). UI changes were also driven end-to-end in headless Chromium against the synthetic fixture server. On real data check `export_report.json`, `validation_report.json`, `HUMAN_TODO.md`,
`daily_progress.csv`, and that sha256 of original ZIPs / manifest are unchanged before vs after.

## B.9 File map
`vision_preprocessing/`: auto, cli, runctx (RunContext, ItemCache, RunLock, Progress, relocation), store, schemas,
source_adapter, legacy (imports qa_v3 dataset_io / qa_v2 registration), thumbs, index_views, roi_drafts, drift,
assign_surfaces, timeline, split_dataset, sampling, state_annotations, target_registry, rasterize, export_dataset,
validate_dataset, postprocess, todo, review_app, review_static/{index,todo}.html,
config/{taxonomy,site_example}.json, tests/, docs/{NEW_SITE_GUIDE_ko,AI_HANDOFF}.md,
tools/relocate_qa_v3_project.py, tools/rename_first_stage_run.py, tools/propagate_labels.py, prewarm.py,
train/{train_quick,infer_core,infer_quick}.py, README.md (Korean usage), requirements_traceability.md.
GitHub copy (sanitized: site ids SITE_A…, example teacher_surfaces template, placeholder paths):
`Capstone-Design-Project-1/src/vision/state_pipeline/` (user runs git: add → commit → pull --rebase --autostash → push → PR to main).
Project root also holds stage-1 launchers: run_auto_preprocess.ps1, 전처리_실행.cmd, 전처리_상태.cmd, README_AUTO.txt,
README_OPERATIONS.txt, automatic_reference_v3/project.json.
