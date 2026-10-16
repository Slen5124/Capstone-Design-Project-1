"""Tests for the automatic orchestration (synthetic data: code behaviour only, not real accuracy).

    python -m unittest vision_preprocessing.tests.test_auto -v
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import tempfile
import unittest
from pathlib import Path

from vision_preprocessing.auto import run_auto
from vision_preprocessing.index_views import detect_candidates, reconcile_views
from vision_preprocessing.runctx import ItemCache, RunContext, RunLock, RunLockError
from vision_preprocessing.schemas import load_config
from vision_preprocessing.source_adapter import load_images
from vision_preprocessing.split_dataset import load_assignments
from vision_preprocessing.state_annotations import eligibility, save_label
from vision_preprocessing.target_registry import save_applicability, save_roi, save_target
from vision_preprocessing.tests.fixtures import W01, W02, build
from vision_preprocessing.timeline import _classify

TAX = load_config(Path(__file__).resolve().parent.parent / "config" / "taxonomy.json")
ACT = "wall_board_installation"
REV = "tester_kim"


def quiet(fn, *a, **k):
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class TestLockAndCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vp_lock_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_lock(self, pid):
        (self.tmp / "RUN_LOCK.json").write_text(json.dumps({"owner": "other", "pid": pid, "host": platform.node(),
                                                            "started_utc": "x"}), encoding="utf-8")

    def test_lock_blocks_live_owner_and_takes_over_dead_one(self):
        self._fake_lock(os.getppid())                      # a live process on this machine
        with self.assertRaises(RunLockError):
            RunLock(self.tmp, "auto").acquire()
        self._fake_lock(2 ** 22 + 12345)                   # no such process -> stale lock is taken over
        lock = RunLock(self.tmp, "auto").acquire()
        self.assertEqual(json.loads((self.tmp / "RUN_LOCK.json").read_text())["pid"], os.getpid())
        inner = RunLock(self.tmp, "assign").acquire()      # nested use in the same process is allowed
        inner.release()
        self.assertTrue((self.tmp / "RUN_LOCK.json").exists())   # ... and does not release the outer lock
        lock.release()
        self.assertFalse((self.tmp / "RUN_LOCK.json").exists())

    def test_lock_from_other_machine_needs_force(self):
        (self.tmp / "RUN_LOCK.json").write_text(json.dumps({"owner": "vm", "pid": 1, "host": "other-host"}))
        with self.assertRaises(RunLockError):
            RunLock(self.tmp, "auto").acquire()
        RunLock(self.tmp, "auto", force=True).acquire().release()

    def test_cache_compaction(self):
        c = ItemCache(self.tmp / "c.jsonl")
        for i in range(300):
            c.put(f"k{i % 50}", "fp", {"i": i})
        c.close()
        self.assertTrue(c.maybe_compact())
        lines = (self.tmp / "c.jsonl").read_text().strip().splitlines()
        self.assertEqual(len(lines), 50)
        again = ItemCache(self.tmp / "c.jsonl")
        self.assertEqual(again.get("k3", "fp"), {"i": 253})


class TestSiteConfigs(unittest.TestCase):
    def test_all_site_configs_valid_and_new_site_command(self):
        from vision_preprocessing.cli import main
        from vision_preprocessing.schemas import validate_site
        cfg_dir = Path(__file__).resolve().parent.parent / "config"
        names = [p.stem for p in sorted(cfg_dir.glob("*.json")) if p.name != "taxonomy.json"]
        self.assertTrue(names)
        for name in names:
            site = load_config(cfg_dir / f"{name}.json")
            self.assertEqual(validate_site(site, TAX), [], name)
            self.assertTrue(site["paths"]["store"].endswith(f"{site['site_id']}.sqlite"))
        tmp = Path(tempfile.mkdtemp(prefix="vp_site_"))
        try:
            out = tmp / "x.json"
            quiet(main, ["new-site", "--site-id", "abc", "--name", "A", "--zip-contains", "#1_-_A", "--out", str(out)])
            site = load_config(out)
            self.assertEqual((site["site_id"], site["source"]["photo_zip_contains"]), ("ABC", "#1_-_A"))
            self.assertTrue(all(t["identity_status"] == "proposed" for t in site["targets"]))
            self.assertEqual(validate_site(site, TAX), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRelocation(unittest.TestCase):
    def test_project_moved_to_another_computer(self):
        from vision_preprocessing.runctx import PACKAGE_DIR, auto_relocation_map, map_path
        site = {"paths": {"preprocess_root": "Z:\\nowhere\\proj\\전처리된 데이터"}}
        pm = auto_relocation_map(site, [])
        here = PACKAGE_DIR.parent
        self.assertEqual(Path(map_path("Z:\\nowhere\\proj\\전처리된 데이터\\automatic_results\\m.jsonl", pm)),
                         here / "automatic_results" / "m.jsonl")
        self.assertEqual(Path(map_path("Z:\\nowhere\\proj\\Data\\a.zip", pm)), here.parent / "Data" / "a.zip")
        explicit = [("Z:\\nowhere\\proj\\Data", "/elsewhere/Data")]   # explicit entries win
        pm2 = auto_relocation_map(site, explicit)
        self.assertEqual(map_path("Z:\\nowhere\\proj\\Data\\a.zip", pm2), str(Path("/elsewhere/Data") / "a.zip"))
        site_ok = {"paths": {"preprocess_root": str(here)}}                  # existing root: unchanged
        self.assertEqual(auto_relocation_map(site_ok, []), [])


class TestQaV3Relocation(unittest.TestCase):
    def test_project_json_paths_follow_new_data_dir(self):
        from vision_preprocessing.tools.relocate_qa_v3_project import main, relocate
        doc = {"photos": ["D:\\job\\x\\Data\\a.zip", "D:\\job\\x\\Data\\b.zip"],
               "json_zip": "D:\\job\\x\\Data\\processed_json 1008(1).zip",
               "references": [{"source": {"zip": "D:\\job\\x\\Data\\a.zip", "entry": "e.jpg"}}],
               "source_manifest": "C:\\Users\\u\\elsewhere\\manifest.json"}
        tmp = Path(tempfile.mkdtemp(prefix="vp_reloc_"))
        try:
            new, changes = relocate(doc, tmp / "Data")
            self.assertEqual(new["photos"][1], str(tmp / "Data" / "b.zip"))
            self.assertEqual(new["references"][0]["source"]["zip"], str(tmp / "Data" / "a.zip"))
            self.assertEqual(new["source_manifest"], doc["source_manifest"])      # unrelated paths untouched
            self.assertEqual(len(changes), 4)
            proj = tmp / "project.json"
            proj.write_text(json.dumps(doc), encoding="utf-8")
            (tmp / "Data").mkdir()
            for n in ("a.zip", "b.zip", "processed_json 1008(1).zip"):
                (tmp / "Data" / n).write_bytes(b"x")
            self.assertEqual(quiet(main, ["--project", str(proj), "--data-dir", str(tmp / "Data")]), 0)   # dry run
            self.assertEqual(json.loads(proj.read_text())["photos"][0], doc["photos"][0])
            self.assertEqual(quiet(main, ["--project", str(proj), "--data-dir", str(tmp / "Data"), "--apply"]), 0)
            self.assertEqual(json.loads(proj.read_text())["photos"][0], str(tmp / "Data" / "a.zip"))
            self.assertTrue(list(tmp.glob("project.json.bak_*")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTimelineClassify(unittest.TestCase):
    def test_segments_between_anchors(self):
        days = [f"2025-03-{d:02d}" for d in range(1, 11)]
        change = {d: {"score": 1.0, "z": 0.0, "strong": d == "2025-03-07"} for d in days}
        anchors = [("2025-03-01", 0.0, "a"), ("2025-03-05", 0.0, "b"), ("2025-03-09", 1.0, "c")]
        res = _classify(days, change, anchors, {"same_fraction_tol": 0.05})["per_day"]
        self.assertEqual(res["2025-03-03"]["phase"], "stable")
        self.assertEqual(res["2025-03-03"]["state_hint"], 0.0)
        self.assertEqual(res["2025-03-07"]["phase"], "transition")
        self.assertEqual(res["2025-03-10"]["phase"], "stable_extrapolated")
        self.assertEqual(res["2025-03-10"]["state_hint"], 1.0)
        res2 = _classify(days, change, [], {"same_fraction_tol": 0.05})["per_day"]
        # no anchors: no knowledge ("unanchored"), except strong change days which become transitions
        self.assertEqual(res2["2025-03-07"]["phase"], "transition")
        self.assertTrue(all(p["phase"] == "unanchored" for d, p in res2.items() if d != "2025-03-07"))


class TestAutoPipeline(unittest.TestCase):
    """auto on the synthetic site: stages, resume, drafts never approved, to-do, ROI-change re-approval."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vp_auto_"))
        cls.fx = build(cls.tmp / "site")
        cls.ctx = quiet(RunContext.create, cls.fx["config"], argv=["test"], label="auto")
        cls.store = cls.ctx.store()
        cls.state1 = quiet(run_auto, cls.ctx, stages=["ingest", "thumbs", "views"])
        cls.images = load_images(cls.ctx)
        s = cls.store
        for tid in ("W01", "W02"):
            t = s.get("target", tid)
            save_target(s, dict(t["payload"], identity_status="confirmed"), REV)
            s.decide("target", tid, s.current_version("target", tid), REV)
            save_applicability(s, tid, ACT, "applicable", "test", REV, TAX)
            s.decide("applicability", f"{tid}|{ACT}", s.current_version("applicability", f"{tid}|{ACT}"), REV)
        for tid in ("F01", "C01"):   # retire targets the synthetic photos do not show
            t = s.get("target", tid)
            save_target(s, dict(t["payload"], identity_status="retired"), REV)
        s.decide("view", "V01", s.current_version("view", "V01"), REV)
        ref = next(im for im in cls.images if im["image_id"] == s.get("view", "V01")["payload"]["reference_image_id"])
        cls.ref = ref
        for tid, poly in (("W01", W01), ("W02", W02)):
            save_roi(s, target_id=tid, view_id="V01", reference_image=ref, polygons=[{"exterior": poly, "holes": []}],
                     exclusions=[], author=REV)
            s.decide("roi", f"{tid}|V01", s.current_version("roi", f"{tid}|V01"), REV)
        # two approved anchors on W02 (whole ROI installed) on different days of V01
        from vision_preprocessing.assign_surfaces import build_observations, load_observations
        v1 = sorted([im for im in cls.images if im["capture"]["time_local"] < "2025-02-18" and not im.get("duplicate_of")],
                    key=lambda im: im["capture"]["time_local"])
        cls.anchor_ims = [v1[1], v1[-2]]
        quiet(build_observations, cls.ctx, images=cls.anchor_ims)
        obs = load_observations(cls.ctx)
        for im in cls.anchor_ims:
            o = obs[f"{im['image_id']}|W02"]
            r = save_label(s, TAX, im, {"target_id": "W02", "activity_id": ACT, "roi_ref": o["roi_ref"],
                                        "fill_roi_state": "installed", "regions": [], "undeterminable": [], "occluded": []},
                           author=REV)
            s.decide("state_label", f"{im['image_id']}|W02|{ACT}", r["version"], REV)
        cls.state2 = quiet(run_auto, cls.ctx, start="roi_drafts")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_all_stages_done(self):
        st = self.state2["stages"]
        for name in ("ingest", "thumbs", "views", "roi_drafts", "drift", "timeline", "split", "samples", "drafts",
                     "export", "todo"):
            self.assertEqual(st[name]["status"], "done", name)
        self.assertFalse((self.ctx.run_dir / "RUN_LOCK.json").exists())

    def test_02_resume_uses_caches(self):
        st = quiet(run_auto, self.ctx, stages=["ingest", "thumbs"])["stages"]
        prog = json.loads((self.ctx.run_dir / "progress.json").read_text())
        self.assertEqual(prog["thumbs"]["skipped_cached"], prog["thumbs"]["total"])
        self.assertEqual(prog["ingest"]["skipped_cached"], prog["ingest"]["total"])
        self.assertEqual(st["ingest"]["status"], "done")

    def test_03_drafts_are_never_approved(self):
        drafts = self.state2["stages"]["drafts"]["summary"]
        self.assertGreater(sum(drafts["drafts_created"].values()), 0, drafts)
        states = self.store.review_states("state_label")
        anchors = {f"{im['image_id']}|W02|{ACT}" for im in self.anchor_ims}
        for rec in self.store.latest("state_label"):
            if rec["key"] in anchors:
                continue
            self.assertEqual(rec["author"], "auto")
            self.assertIn(rec["source"], ("propagated_draft", "bulk_prefill_draft"))
            self.assertEqual(states[rec["key"]]["status"], "unreviewed")
        # no drafts for targets/views whose ROI is not approved (V02 has no ROI)
        skipped = drafts["skipped"]
        self.assertTrue(any(k.startswith("roi_") for k in skipped), skipped)

    def test_04_samples_quota_and_messy_share(self):
        rep = json.loads((self.ctx.run_dir / "stages" / "samples" / "samples_report.json").read_text())
        rows = [json.loads(l) for l in open(self.ctx.run_dir / "stages" / "samples" / "samples.jsonl")]
        per_day = {}
        for r in rows:
            per_day.setdefault((r["view_id"], r["day"]), []).append(r)
        for (vid, day), rs in per_day.items():
            normal = [r for r in rs if r["kind"] in ("stable", "transition")]
            self.assertLessEqual(len(normal), 6)
        self.assertEqual(rep["samples"], len(rows))
        self.assertTrue(all(r["light"] != "dark" for r in rows))

    def test_05_split_phase_stratified_no_leakage(self):
        self.ctx.site["split"]["policy"] = "phase_stratified"
        from vision_preprocessing.split_dataset import make_splits
        out = quiet(make_splits, self.ctx, load_images(self.ctx))
        self.assertTrue(out["leakage_check"]["passed"])
        a = load_assignments(self.ctx)
        by_day = {}
        for row in a.values():
            by_day.setdefault((row["view_id"], (row["time"] or "")[:10]), set()).add(row["split"])
        self.assertTrue(all(len(s) == 1 for s in by_day.values()))
        self.assertGreaterEqual(len({r["split"] for r in a.values()} & {"train", "val", "test"}), 2)

    def test_06_export_and_todo(self):
        ex = self.state2["stages"]["export"]["summary"]["train"]
        self.assertGreaterEqual(ex["eligible_labels"], 1, ex)
        self.assertTrue(ex.get("validation_passed"), ex)
        dp = json.loads((Path(ex["export_dir"]) / "daily_progress.json").read_text(encoding="utf-8"))
        w02 = [r for r in dp["rows"] if r["target_id"] == "W02"]
        self.assertTrue(w02 and all(r["change"] in ("first_value", "no_change", "increase", "decrease_check", "held")
                                    for r in w02))
        self.assertTrue(any(r["change"] == "no_change" for r in w02), w02)   # two installed anchors
        todo = json.loads((self.ctx.run_dir / "human_todo.json").read_text(encoding="utf-8"))
        ids = {s["id"]: s["count"] for s in todo["sections"]}
        self.assertGreater(ids["roi_missing"], 0)          # V02 (moved camera) needs ROIs
        self.assertGreater(ids["drafts_to_review"], 0)
        self.assertTrue((self.ctx.run_dir / "HUMAN_TODO.md").exists())

    def test_07b_roi_drafts_for_moved_view_are_unapproved(self):
        rep = json.loads((self.ctx.run_dir / "stages" / "views" / "roi_drafts.json").read_text())
        for c in rep["created"]:
            rec = self.store.get("roi", c["roi"])
            self.assertEqual((rec["source"], rec["author"]), ("auto_candidate", "auto"))
            self.assertNotEqual(self.store.review_state("roi", c["roi"])["status"], "approved")
        self.assertEqual(len(rep["created"]) + len(rep["failed"]), 2, rep)   # W01|V02, W02|V02

    def test_07_drift_static_view_is_ok(self):
        rep = json.loads((self.ctx.run_dir / "stages" / "drift" / "drift_report.json").read_text())
        self.assertNotIn("redraw_requested", rep["views"]["V01"]["counts"])

    def test_08_coarse_to_fine_views_same_boundary(self):
        self.ctx.site["view_detection"]["coarse_stride"] = 3
        try:
            rep = quiet(detect_candidates, self.ctx, load_images(self.ctx))
        finally:
            self.ctx.site["view_detection"]["coarse_stride"] = 1
        segs = rep["segments"]
        self.assertEqual(len(segs), 2, [(s["start_time"], s["end_time"]) for s in segs])
        self.assertTrue(segs[1]["start_time"].startswith("2025-02-18"), segs[1]["start_time"])
        self.assertLess(rep["frames_compared"], rep["frames_total"])

    def test_09_reconcile_keeps_human_decisions(self):
        rep = {"segments": [{"candidate_id": "VCX", "camera_id": "SITE_A_CAM01", "start_time": "2025-02-10T10:00:00",
                             "end_time": "2025-02-16T15:00:00", "reference_image_id": self.ref["image_id"]}]}
        before = self.store.get("view", "V01")["version"]
        out = reconcile_views(self.store, rep, self.ctx.site)
        self.assertEqual(self.store.get("view", "V01")["version"], before)          # approved: untouched
        self.assertTrue(any(p["view_id"] == "V01" for p in out["proposals"]))
        self.assertEqual(self.store.review_state("view", "V01")["status"], "approved")

    def test_10_roi_change_then_rebase_reapproves(self):
        from vision_preprocessing.review_app import ReviewService
        from vision_preprocessing.todo import rebase_candidates
        s = self.store
        moved = [[max(390, x - 4), y] for x, y in W02]
        save_roi(s, target_id="W02", view_id="V01", reference_image=self.ref, polygons=[{"exterior": moved, "holes": []}],
                 exclusions=[{"points": [[600, 150], [630, 150], [630, 200], [600, 200]], "reason": "door_opening"}],
                 author=REV)
        s.decide("roi", "W02|V01", s.current_version("roi", "W02|V01"), REV)
        cands = rebase_candidates(s)
        keys = {c["key"] for c in cands.get("W02|V01", []) if c["was_approved"]}
        self.assertEqual(keys, {f"{im['image_id']}|W02|{ACT}" for im in self.anchor_ims})
        for k in keys:
            self.assertEqual(s.review_state("state_label", k)["status"], "invalidated")
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            res = quiet(svc.rebase_labels, {"keys": sorted(keys), "approve": True}, REV)
            self.assertEqual(res["rebased"], 2, res)
            for im in self.anchor_ims:
                k = f"{im['image_id']}|W02|{ACT}"
                self.assertEqual(s.review_state("state_label", k)["status"], "approved")
                o = svc.obs()[f"{im['image_id']}|W02"]
                self.assertTrue(eligibility(s, im, o, "W02", ACT)["eligible"])
        finally:
            svc.reader.close()
            svc.obs_cache.close()

    def test_11_bulk_review_states_match_single(self):
        s = self.store
        bulk = s.review_states("state_label")
        for rec in s.latest("state_label"):
            self.assertEqual(bulk[rec["key"]]["status"], s.review_state("state_label", rec["key"])["status"], rec["key"])

    def test_13_todo_page_and_sample_list(self):
        from vision_preprocessing.review_app import ReviewService
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            todo = svc.todo()
            self.assertIn("rebase", todo)
            self.assertTrue(any(s["id"] == "roi_missing" for s in todo["sections"]))
            miss = next(s for s in todo["sections"] if s["id"] == "roi_missing")
            if miss["items"]:
                x = miss["items"][0]
                svc.post("/api/roi_skip", {"author": REV, "target_id": x["target_id"], "view_id": x["view_id"], "visible": False})
                after = next(s for s in svc.todo()["sections"] if s["id"] == "roi_missing")
                self.assertNotIn(x["roi"], [i["roi"] for i in after["items"]])      # "not visible" removes the request
                self.assertEqual(after["count"], miss["count"] - 1)
            lst = svc.image_list()
            self.assertTrue(any(i["sample"] for i in lst))
            self.assertTrue(svc.thumb(lst[0]["image_id"]).startswith(b"\xff\xd8"))
        finally:
            svc.reader.close()
            svc.obs_cache.close()

    def test_14_consistency_flags_backwards_labels(self):
        from vision_preprocessing.assign_surfaces import build_observations, load_observations
        from vision_preprocessing.todo import label_consistency
        s = self.store
        later = sorted([im for im in self.images if not im.get("duplicate_of")
                        and self.anchor_ims[1]["capture"]["time_local"] < im["capture"]["time_local"] < "2025-02-18"],
                       key=lambda im: im["capture"]["time_local"])[0]
        quiet(build_observations, self.ctx, images=[later])
        o = load_observations(self.ctx)[f"{later['image_id']}|W02"]
        key = f"{later['image_id']}|W02|{ACT}"
        if s.get("state_label", key) is not None:
            p = dict(s.get("state_label", key)["payload"])
        else:
            p = {"target_id": "W02", "activity_id": ACT, "regions": [], "undeterminable": [], "occluded": []}
        p.update(roi_ref=o["roi_ref"], fill_roi_state="not_installed", regions=[])
        r = save_label(s, TAX, later, p, author=REV)
        s.decide("state_label", key, r["version"], REV)
        flags = label_consistency(s, load_images(self.ctx))
        self.assertTrue(any(f["image_id"] == later["image_id"] and f["target_id"] == "W02" for f in flags), flags)
        # the reviewer compares both photos and replaces the wrong label with the earlier state
        from vision_preprocessing.review_app import ReviewService
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            flag = next(f for f in flags if f["image_id"] == later["image_id"] and f["target_id"] == "W02")
            res = quiet(svc.replace_label, {"key": key, "from_image_id": flag["compare_with"]}, REV)
            self.assertEqual(res["approval"]["decision"], "approved")
            self.assertEqual(s.get("state_label", key)["payload"]["fill_roi_state"], "installed")
        finally:
            svc.reader.close()
            svc.obs_cache.close()
        flags = label_consistency(s, load_images(self.ctx))
        self.assertFalse(any(f["image_id"] == later["image_id"] and f["target_id"] == "W02" for f in flags), flags)

    def test_12_lock_blocks_auto_while_review_runs(self):
        lock = self.ctx.run_dir / "RUN_LOCK.json"
        lock.write_text(json.dumps({"owner": "review server", "pid": os.getppid(), "host": platform.node()}))
        try:
            with self.assertRaises(RunLockError):
                quiet(run_auto, self.ctx, stages=["todo"])
        finally:
            lock.unlink()


if __name__ == "__main__":
    unittest.main()


class TestFirstStageRename(unittest.TestCase):
    def test_renamed_first_stage_folder_is_followed(self):
        from vision_preprocessing.auto import pointer_check
        from vision_preprocessing.source_adapter import SourceReader
        from vision_preprocessing.tools.rename_first_stage_run import main as rename_main
        from vision_preprocessing.tools.relocate_qa_v3_project import relocate_pointers
        from vision_preprocessing.tests.fixtures import file_hashes
        tmp = Path(tempfile.mkdtemp(prefix="vp_rename_"))
        try:
            fx = build(tmp, n_days=3)
            run = tmp / "automatic_results" / "run_fixture"
            n = fx["n_photos"]
            (run / "summary.json").write_text(json.dumps({"status": "complete", "remaining_photos": 0, "scope": "full_dataset",
                                                          "completed_photos": n, "total_photos": n}), encoding="utf-8")
            ctx = quiet(RunContext.create, fx["config"], label="before")
            quiet(run_auto, ctx, stages=["ingest"])
            img = next(im for im in load_images(ctx) if im.get("annotation"))
            before = SourceReader(ctx).annotation_bytes(img)
            hashes = file_hashes([fx["manifest"], fx["ann_zip"]])
            # dry run changes nothing
            self.assertEqual(quiet(rename_main, ["--root", str(tmp)]), 0)
            self.assertTrue(run.exists())
            self.assertEqual(quiet(rename_main, ["--root", str(tmp), "--name", "1차결과_test", "--apply"]), 0)
            new = tmp / "automatic_results" / "1차결과_test"
            self.assertFalse(run.exists())
            self.assertTrue((new / "README_1차결과.md").exists())
            marker = json.loads((new / "FIRST_STAGE_RUN.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["original_run_name"], "run_fixture")
            self.assertIn(WIN := "D:\\fixture\\automatic_results\\run_fixture", marker["original_run_dirs"])
            ptr = json.loads((tmp / "automatic_results" / "latest_active_run.json").read_text(encoding="utf-8"))
            self.assertEqual(ptr["run_dir"], WIN.replace("run_fixture", "1차결과_test"))   # keeps the D:\ form
            self.assertTrue(list((tmp / "automatic_results").glob("latest_active_run.json.bak_*")))
            # contents untouched (sha256 of manifest and annotation ZIP)
            self.assertEqual(list(file_hashes([new / "manifest.jsonl", new / "chunks/part_000001/annotations/needs_review.zip"]).values()),
                             list(hashes.values()))
            # an existing vision run (recorded the old name) follows the renamed folder
            ctx2 = RunContext.open(ctx.run_dir)
            self.assertEqual(Path(ctx2.source_run_dir), new)
            self.assertEqual(SourceReader(ctx2).annotation_bytes(img), before)
            self.assertFalse(pointer_check(ctx2)["changed"])
            # a new vision run reads the updated pointer and still finds the old-named annotation paths
            ctx3 = quiet(RunContext.create, fx["config"], label="after")
            self.assertEqual(Path(ctx3.source_run_dir), new)
            quiet(run_auto, ctx3, stages=["ingest"])
            self.assertEqual(len(load_images(ctx3)), len(load_images(ctx)))
            # renamed again by hand (Explorer) with a stale pointer: still found through the marker
            os.rename(new, tmp / "automatic_results" / "renamed_by_hand")
            ctx4 = RunContext.open(ctx.run_dir)
            self.assertEqual(Path(ctx4.source_run_dir).name, "renamed_by_hand")
            self.assertEqual(SourceReader(ctx4).annotation_bytes(img), before)
            # relocate tool repoints the stale pointer (for 전처리_상태.cmd)
            fixes = relocate_pointers(tmp)
            self.assertEqual([Path(nv["run_dir"]).name for _, _, nv in fixes], ["renamed_by_hand"])
            ctx5 = quiet(RunContext.create, fx["config"], label="stale_pointer")      # pointer still names 1차결과_test
            self.assertEqual(Path(ctx5.source_run_dir).name, "renamed_by_hand")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
