# Requirements traceability

검증 열 표기
- **T**: 합성 데이터 단위 테스트 (`tests/test_pipeline.py`) — 코드 동작 검증
- **B**: SITE_A 실제 소량 실행 (run `run_20261013T031949Z_884f37_site_a_small`, 48장)
- **사람**: 사람 검수가 필요함 (코드가 대신하지 않음)

| R | 요구 | 구현 (파일 · 함수) | 검증 |
|---|---|---|---|
| R01 | manifest/ExportDataset 활용, 누락·크기·해시 불일치 기록, 촬영시각≠다운로드시각, 시간대 임의 지정 금지 | `legacy.SiteExportDataset`(ExportDataset 상속: needs_review를 명시적으로 포함, 사이트별로 걸러 메모리 절약), `source_adapter.ArchivePool`(ExportDataset.archive 재사용), `inspect`, `parse_capture_time`, `mark_duplicates`, `run_ingest` | T `test_pairing_and_checks`, `test_capture_time_vs_download_time`, `test_duplicates_grouped`, `test_originals_unchanged`, `test_resume_uses_cache`, `test_reopen_with_explicit_path_map` · B 48장 모두 해시 일치, 크기 2688×1512, held 0, timezone=null |
| R02 | 구도 구간 사람 설정·수정, 자동 탐지는 후보, 조명/진행≠이동, 이동 후 ROI 무조건 재사용 금지 | `index_views.detect_candidates`(순차 SIFT/RANSAC, 어두운 프레임은 비교하지 않음, `camera_shift`와 `appearance_change_or_move_unresolved` 구분), `contact_sheet`, `views_from_candidates`(draft), `views_from_config`, `assign_view`, UI `/api/view` | T `test_camera_move_candidate_not_lighting` · B 후보 VC01~VC05, 경계 4개 중 판정 불가(appearance/move) 3개와 small shift 1개, contact_sheet.jpg · **사람**: 경계 확정 |
| R03 | 벽·바닥·천장 대상 등록, 기준 사진 ROI·제외 영역, 바닥·천장 구역 분리, 사진마다 대상을 새로 만들지 않음 | `target_registry.sync_from_config`(insert-only), `save_target`, `save_applicability`, `validate_roi`, `save_roi`, `roi_hints`(teacher는 힌트로만) | T `test_roi_bounds_and_validation`, `test_holes_and_exclusions_in_mask` · B W01/W02/F01/C01 ROI(assistant draft, 미승인) |
| R04 | 기하 관계로 연결 후보, 다중 조각 → 하나의 target, 여러 대상에 걸친 도형은 검수 항목, 임계값 설정·근거 저장, 폴리곤이 없다고 부재로 보지 않음, 마스크 합집합을 ROI로 쓰지 않음 | `assign_surfaces._observe_image`(fractions, link_candidate/split_candidate/partial_overlap, coverage, occlusion_candidates, quality flags), `load_shapes` | T `test_multi_polygon_one_target`, `test_spanning_polygon_is_review_item_not_link`, `test_no_roi_is_not_absence`, `test_nothing_auto_approved` · B 대상별 평균 링크 W01 8.2 / W02 5.1 / F01 7.0 / C01 17.3 |
| R05 | 동일 구도 재사용, 기존 registration 활용, 대상별 변환(일괄 변환 금지), 실패 시 보류·근거, ROI 전파≠상태 전파 | `assign_surfaces.register_roi` → `legacy.project_regions`(qa_v2 대상별 호모그래피), same_view_reuse / registered_draft / roi_pending_*, `verify_same_view_registration` 옵션 | T `test_cross_view_registration_is_per_target_and_unconfirmed` · B V02~V05의 108개 관찰 모두 정합 실패로 보류(사유: insufficient_mutual_ratio_matches 83 등) |
| R06 | taxonomy로 작업 정의, 예시 작업 자동 적용 금지, applicability unknown 기본, 무엇의 설치인지 구분, 객체 라벨로 상태 정답을 만들지 않음 | `config/taxonomy.json`(installs, installed_criterion=UNCONFIRMED, labeling_guidance), `state_annotations.validate_label`, `save_label`, `eligibility`, `propagate_label_draft` | T `test_01_unreviewed_labels_block_training` · B state_label 0건(실제 라벨은 만들지 않음), applicability 4건 모두 unknown · **사람**: 작업 정의·적용 여부 |
| R07 | 원본 사진 표시, 기준/현재 비교, 레이어 토글, ROI·상태·가림 편집, draft/승인 구분, 대상·작업·버전별 승인, 변경 시 무효화, 재시작 후 유지 | `review_app.ReviewService`·`make_handler`·`serve`, `review_static/index.html`, `store.ReviewStore.put/decide/review_state/_invalidate` | T `test_save_approve_restart`, `test_03_roi_change_invalidates_approval_and_cache`, `test_04_persistence_after_restart`, `test_05_reviewer_and_manual_protection` · Playwright로 합성 데이터 UI 확인(폴리곤 그리기→저장→승인) · B 서버 API 응답 확인(meta/image/shapes/item, 시스템 이름 승인 거부) |
| R08 | 원본 좌표에서 마스크 → 학습 크기 변환, 객체/대상/상태/가림 마스크 분리, 클래스 ID label_map, 대상 ID와 클래스 ID 분리, 255 ignore, 겹침은 순서가 아닌 정책으로, 구멍·제외 보존 | `rasterize.fill_polys`, `roi_mask`, `semantic_mask`(레이어 정책), `target_mask`·`target_dtype`, `state_and_visibility`, `make_transform`/`apply_image`/`apply_mask`/`to_original`, `render_overlay` | T `test_semantic_order_independent_and_conflicts`, `test_state_mask_semantics`, `test_transform_rgb_mask_alignment`, `test_target_dtype_overflow` · B 오버레이 8장 |
| R09 | 인접 프레임 무작위 분할 금지, crop은 같은 split, 중복·근접 구간 누수 검사, 정책 파일, 분포 보고, 단일 현장 한계 명시, 평가 라벨 기반 전파 금지 | `split_dataset.make_splits`(union-find: 해시·view·일 블록), `check_leakage`, `distribution_report`, export의 `propagated_from_eval_split` 차단 | T `test_06_split_no_leakage` · B train 34 / val 4 / test 4 / gap 6, 중복 누수 0, 인접 split 최소 간격 71.4시간 |
| R10 | 권장 폴더 구조, 포맷·dtype 명시, overflow 검사, 동일 변환, nearest, 좌표 변환 기록, 전체/crop 구분, 부분 정답, 미승인을 정답으로 위장하지 않음, 전부 ignore인 샘플 제외, 모델 입력은 RGB | `export_dataset.plan_export`, `export`, `label_map`, `_next_actions`, `validate_dataset.validate_export` | T `test_02_partial_approval_export`(전체 + crop, validate 통과) · B train export **blocked_no_approved_labels**(정상), draft full 6, draft crop 16(벽·바닥·천장), validate 통과 |
| R11 | 설정 snapshot·schema_version, 변경 시 캐시 무효화, resume, 진행 표시, 단위 구분, dry-run/limit | `runctx.RunContext.create/open/_resolve`, `ItemCache`, `Progress`, `config_fingerprint`(현재 코드 해시 포함), `cli` `--dry-run`/`--limit` | T `test_resume_uses_cache`, `test_03`(ROI 버전 변경 → 관찰 재계산) · B run_config.json, progress.json, 재실행 시 cached 48 |
| 후처리 | 대상별 관찰 비율, 전후 비교, 보류 | `postprocess.measure_sample`, `measure_export`, `compare_measurements` | T `test_measure_ratio_and_hold` |

원본 보존(B): 실행 전후 sha256이 같음 — SITE_A 사진 ZIP, processed_json ZIP, manifest.jsonl, classification.csv, summary.json, latest_active_run.json, teacher_surfaces.json, qa_v3/dataset_io.py, qa_v2/registration.py, chunks 파일 목록·크기·mtime. qa_v2·qa_v3·automatic_results 아래에 새로 생긴 파일도 없습니다.

## 자동화 확장 (auto, 2026-10-13)

검증 열: **T2** = `tests/test_auto.py` (합성), **B2** = SITE_A 시험 실행 `run_20261013T101152Z_2a5355_trial600b` (612장, 시험용 저장소 복사본 `SITE_A_trial.sqlite` 사용 — 실제 저장소는 건드리지 않음)

| 항목 | 구현 | 검증 |
|---|---|---|
| 한 번에 실행·resume·단계 선택 | `auto.run_auto`, `cli auto --config/--run/--stage/--start/--limit` | T2 `test_01_all_stages_done`, `test_02_resume_uses_caches` · B2 11단계 모두 완료 |
| 실행 잠금(auto ↔ 검수 서버) | `runctx.RunLock`(같은 PC의 죽은 프로세스 잠금은 자동 해제, 다른 PC 잠금은 --force-unlock) | T2 `test_lock_*`, `test_12_lock_blocks_auto_while_review_runs` · B2 서버 실행 중 RUN_LOCK.json 확인 |
| 캐시 크기(관찰 slim·compaction), 단계별 코드 해시 | `ItemCache.maybe_compact`, `config_fingerprint(code=...)`, slim links/occluder | T2 `test_cache_compaction` |
| 1만 장 UI | 표본 필터 기본, 목록 300개씩, Grid 120장 페이지, 썸네일 캐시, 사진을 열 때만 관찰 갱신, 일괄 review_states | T2 `test_13_todo_page_and_sample_list`, `test_11_bulk_review_states_match_single` · Playwright로 /, Grid, /todo 확인(콘솔 오류 0) |
| 구도 경계(coarse-to-fine, 외관 변화 병합) | `index_views.detect_candidates(coarse_stride)`, `_refine_boundaries`, 기준 사진 재비교 병합, `reconcile_views`(승인된 view는 제안만) | T2 `test_08_coarse_to_fine_views_same_boundary`, `test_09_reconcile_keeps_human_decisions` · B2 99장 비교로 4개 구도, 외관 변화 경계 2개 병합(기준 사진 corner shift 0.18%·0.14%), 실제 이동 1개(3.9%) |
| 새 구도 ROI 초안 | `roi_drafts.draft_missing_rois`(대상별 정합, 미승인 초안) | T2 `test_07b` · B2 V01→다른 구도 12건 모두 정합 실패(어안·시점 차이) → 사람 그리기 |
| 카메라 밀림 / 흔들림 | `drift.check_drift` (0.5% / 1.5% / spread 1.5%) | T2 `test_07_drift_static_view_is_ok` · B2 redraw 0, minor 3일(V03), 심한 흔들림 보고 1 |
| 상태 타임라인 후보 | `timeline.build_timeline`, `_classify`, 자기검사(leave-one-anchor-out) | T2 `test_segments_between_anchors` · B2 앵커 28개 중 23 일치, 0 불일치, 5 판단 보류(정확도 추정 아님) |
| 단계 층화 split | `split_dataset._phase_blocks`, `_assign_stratified`(해시 순서, 재실행 안정) | T2 `test_05_split_phase_stratified_no_leakage` · B2 누수 0 |
| 표본·초안 (승인 없음) | `sampling.select_samples`, `make_drafts`, `anchor_requests` | T2 `test_03_drafts_are_never_approved`, `test_04_samples_quota_and_messy_share` · B2 표본 251, 초안 352(안정 복사 164, 전체 채움 18, 변화 구간 복사 170), 앵커 요청 12 |
| ROI 변경 후 일괄 재승인 | `todo.rebase_candidates`, `ReviewService.rebase_labels`, /todo 썸네일 확인 | T2 `test_10_roi_change_then_rebase_reapproves` |
| 할 일 목록 | `todo.build_todo`, `write_todo_markdown`, `review_static/todo.html` | T2 `test_06_export_and_todo` · B2 HUMAN_TODO.md |
| 진행률 % (라벨 기준) | `postprocess.measure_export` (보이는 영역 안 설치 비율) | B2 승인 라벨 29개 → 측정 30행, 예: 02-19 W01 71%(관찰 90%), W02 100% |
