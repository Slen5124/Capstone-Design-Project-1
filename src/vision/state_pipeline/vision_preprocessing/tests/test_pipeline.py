"""Code-behaviour tests on SYNTHETIC data (they verify logic, not real-site recognition accuracy).

Run from the project root (the folder containing qa_v3/, qa_v2/, vision_preprocessing/):
    python -m unittest discover -s vision_preprocessing/tests -t . -v
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from vision_preprocessing.assign_surfaces import build_observations, load_observations
from vision_preprocessing.export_dataset import export
from vision_preprocessing.index_views import detect_candidates, views_from_candidates
from vision_preprocessing.postprocess import compare_measurements, measure_export
from vision_preprocessing.rasterize import (apply_image, apply_mask, fill_polys, load_mask_png, make_transform,
                                            roi_mask, semantic_mask, state_and_visibility, target_dtype, to_original)
from vision_preprocessing.runctx import RunContext
from vision_preprocessing.schemas import load_config
from vision_preprocessing.source_adapter import load_images, run_ingest
from vision_preprocessing.split_dataset import load_assignments, make_splits
from vision_preprocessing.state_annotations import LabelError, eligibility, save_label, save_manual_observation
from vision_preprocessing.store import ManualRecordProtected, ReviewStore, StoreError
from vision_preprocessing.target_registry import (RegistryError, save_applicability, save_roi, save_target,
                                                  sync_from_config, validate_roi)
from vision_preprocessing.tests.fixtures import W01, W02, build, file_hashes
from vision_preprocessing.validate_dataset import validate_export

TAX = load_config(Path(__file__).resolve().parent.parent / "config" / "taxonomy.json")
ACT = "wall_board_installation"
REVIEWER = "tester_kim"


def quiet(fn, *a, **k):
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class PipelineCase(unittest.TestCase):
    """Builds a fixture run: ingest -> view candidates -> registry -> ROIs (V01) -> assign."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="vp_test_"))
        cls.fx = build(cls.tmp / "site")
        cls.before = file_hashes([cls.fx["photo_zip"], cls.fx["ann_zip"], cls.fx["manifest"]])
        cls.ctx = quiet(RunContext.create, cls.fx["config"], argv=["test"])
        cls.store = cls.ctx.store()
        cls.ingest = quiet(run_ingest, cls.ctx)
        cls.images = load_images(cls.ctx)
        cls.by_id = {im["image_id"]: im for im in load_images(cls.ctx, include_held=True)}
        cls.cands = quiet(detect_candidates, cls.ctx, cls.images)
        views_from_candidates(cls.store, cls.cands, cls.ctx.site)
        sync_from_config(cls.store, cls.ctx.site, cls.ctx.taxonomy)
        ref = cls.by_id[cls.store.get("view", "V01")["payload"]["reference_image_id"]]
        cls.ref = ref
        save_roi(cls.store, target_id="W01", view_id="V01", reference_image=ref, polygons=[{"exterior": W01, "holes": []}],
                 exclusions=[{"points": [[150, 150], [200, 150], [200, 250], [150, 250]], "reason": "door_opening"}],
                 author=REVIEWER)
        save_roi(cls.store, target_id="W02", view_id="V01", reference_image=ref, polygons=[{"exterior": W02, "holes": []}],
                 exclusions=[], author=REVIEWER)
        quiet(build_observations, cls.ctx)
        cls.obs = load_observations(cls.ctx)
        cls.v1 = [im for im in cls.images if cls.obs.get(im["image_id"] + "|W01", {}).get("status") == "ok"]

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def label(self, im, target="W01", installed_w=40, fill="not_installed", **extra):
        o = load_observations(self.ctx)[im["image_id"] + "|" + target]
        p = {"target_id": target, "activity_id": ACT, "roi_ref": o["roi_ref"], "fill_roi_state": fill,
             "regions": [{"state": "installed", "polygons": [{"exterior": [[20, 120], [20 + installed_w, 120],
                                                                           [20 + installed_w, 240], [20, 240]], "holes": []}]}],
             "undeterminable": [], "occluded": []}
        p.update(extra)
        return save_label(self.store, TAX, im, p, author=REVIEWER, view_id=o["view_id"])

    def approve_chain(self, target="W01"):
        t = self.store.get("target", target)
        if t["payload"]["identity_status"] != "confirmed":
            save_target(self.store, dict(t["payload"], identity_status="confirmed"), REVIEWER)
        if self.store.review_state("target", target)["status"] != "approved":
            self.store.decide("target", target, self.store.current_version("target", target), REVIEWER)
        key = f"{target}|{ACT}"
        if self.store.get("applicability", key)["payload"]["applicability"] != "applicable":
            save_applicability(self.store, target, ACT, "applicable", "test basis", REVIEWER, TAX)
        if self.store.review_state("applicability", key)["status"] != "approved":
            self.store.decide("applicability", key, self.store.current_version("applicability", key), REVIEWER)
        if self.store.review_state("view", "V01")["status"] != "approved":
            self.store.decide("view", "V01", self.store.current_version("view", "V01"), REVIEWER)
        rk = f"{target}|V01"
        if self.store.review_state("roi", rk)["status"] != "approved":
            self.store.decide("roi", rk, self.store.current_version("roi", rk), REVIEWER)


class TestIngestAndSources(PipelineCase):
    def test_originals_unchanged(self):
        quiet(export, self.ctx, mode="draft", limit=2)
        self.assertEqual(self.before, file_hashes([self.fx["photo_zip"], self.fx["ann_zip"], self.fx["manifest"]]))

    def test_pairing_and_checks(self):
        held = {c["code"] for im in self.by_id.values() for c in im["checks"]}
        self.assertTrue({"PHOTO_HASH_MISMATCH", "ANNOTATION_OUTPUT_MISSING", "ANNOTATION_SIZE_MISMATCH"} <= held)
        for im in self.by_id.values():
            if im["checks"]:
                self.assertEqual(im["status"], "held")
        from vision_preprocessing.source_adapter import SourceReader
        reader = SourceReader(self.ctx)
        for im in self.images:
            doc = reader.annotation(im)
            self.assertEqual(doc["imagePath"], im["source_photo"]["entry"])   # JSON <-> photo link
        reader.close()
        self.assertEqual(self.ingest["site_records"], self.fx["n_photos"])     # other site excluded
        self.assertIn("rejected", self.ingest["excluded_categories"])
        self.assertIn("needs_review", {im["annotation"]["qa_v3_classification"] for im in self.images})

    def test_capture_time_vs_download_time(self):
        dl = [im for im in self.by_id.values() if im["capture"]["download_time_in_name"]]
        self.assertEqual(len(dl), 1)
        self.assertEqual(dl[0]["capture"]["time_local"], "2025-02-10T10:00:00")
        self.assertEqual(dl[0]["capture"]["download_time_in_name"], "2026-10-07T172716.781")
        self.assertTrue(all(im["capture"]["timezone"] is None and not im["capture"]["timezone_confirmed"]
                            for im in self.by_id.values()))

    def test_duplicates_grouped(self):
        groups = {}
        for im in self.by_id.values():
            groups.setdefault(im["content_group"], []).append(im)
        dup = [g for g in groups.values() if len(g) > 1]
        self.assertEqual(len(dup), 1)
        self.assertEqual(sum(1 for im in dup[0] if im["duplicate_of"] is None), 1)

    def test_reopen_with_explicit_path_map(self):
        ctx = RunContext.open(self.ctx.run_dir, [f"D:\\fixture={self.fx['root']}"])
        self.assertTrue(Path(ctx.info["resolved"]["source_run_dir"]).exists())
        self.assertEqual(len(load_images(ctx)), len(self.images))

    def test_resume_uses_cache(self):
        report = quiet(run_ingest, self.ctx)
        self.assertEqual(report["counts"]["skipped_cached"], self.fx["n_photos"])


class TestViews(PipelineCase):
    def test_camera_move_candidate_not_lighting(self):
        segs = self.cands["segments"]
        self.assertEqual(len(segs), 2, segs)
        self.assertTrue(segs[1]["start_time"].startswith("2025-02-18"))
        # afternoon frames are darker (lighting) but stay in their segment
        self.assertTrue(segs[0]["end_time"].endswith("15:00:00"))
        self.assertEqual(self.store.get("view", "V01")["source"], "auto_candidate")
        self.assertEqual(self.store.review_state("view", "V01")["status"] in ("unreviewed", "approved"), True)


class TestGeometryAndMasks(unittest.TestCase):
    def test_roi_bounds_and_validation(self):
        bad = {"coord_space": "original_pixel_xy", "image_size": [100, 80],
               "polygons": [{"exterior": [[0, 0], [120, 0], [120, 50]], "holes": []}], "exclusions": []}
        self.assertTrue(any("outside image" in e for e in validate_roi(bad, (100, 80))))
        bowtie = dict(bad, polygons=[{"exterior": [[0, 0], [50, 50], [50, 0], [0, 50]], "holes": []}])
        self.assertTrue(validate_roi(bowtie, (100, 80)))

    def test_holes_and_exclusions_in_mask(self):
        roi = {"polygons": [{"exterior": [[10, 10], [90, 10], [90, 70], [10, 70]], "holes": [[[20, 20], [30, 20], [30, 30], [20, 30]]]}],
               "exclusions": [{"points": [[60, 20], [80, 20], [80, 60], [60, 60]], "reason": "door_opening"}]}
        m = roi_mask(roi, (100, 80))
        self.assertTrue(m[50, 40])
        self.assertFalse(m[25, 25])       # hole
        self.assertFalse(m[40, 70])       # exclusion
        self.assertFalse(m[5, 5])         # outside

    def test_semantic_order_independent_and_conflicts(self):
        shapes = [{"label": "wall", "points": [[0, 0], [60, 0], [60, 60], [0, 60]]},
                  {"label": "ceiling", "points": [[40, 0], [100, 0], [100, 30], [40, 30]]},   # same layer overlap
                  {"label": "person", "points": [[10, 10], [20, 10], [20, 50], [10, 50]]}]     # front layer
        a, sa = semantic_mask({"shapes": shapes}, (100, 80), TAX)
        b, _ = semantic_mask({"shapes": list(reversed(shapes))}, (100, 80), TAX)
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(a[15, 50], 255)   # wall/ceiling conflict -> ignore, not "last shape wins"
        self.assertEqual(a[30, 15], 7)     # person occludes wall
        self.assertEqual(a[45, 45], 0)     # wall
        self.assertEqual(a[70, 90], 255)   # unlabeled -> ignore
        self.assertGreater(sa["conflict_px"], 0)

    def test_state_mask_semantics(self):
        roi = np.zeros((80, 100), bool)
        roi[10:70, 10:90] = True
        label = {"regions": [{"state": "installed", "polygons": [{"exterior": [[10, 10], [40, 10], [40, 40], [10, 40]], "holes": []}]},
                             {"state": "not_installed", "polygons": [{"exterior": [[30, 10], [60, 10], [60, 40], [30, 40]], "holes": []}]}],
                 "fill_roi_state": None, "undeterminable": [{"reason": "motion_blur", "polygons": [{"exterior": [[70, 50], [85, 50], [85, 65], [70, 65]], "holes": []}]}],
                 "occluded": []}
        occl = [{"exterior": [[12, 50], [25, 50], [25, 65], [12, 65]], "holes": []}]
        st, vis, s = state_and_visibility(label, roi, (100, 80), occl)
        self.assertEqual(st[20, 20], 1)
        self.assertEqual(st[20, 50], 0)
        self.assertEqual(st[20, 35], 255)   # installed/not_installed conflict
        self.assertEqual(st[60, 50], 255)   # unlabeled inside ROI stays ignore (not 0)
        self.assertEqual(st[5, 5], 255)     # outside ROI
        self.assertEqual(st[55, 77], 255)   # undeterminable
        self.assertEqual(st[55, 18], 255)   # occluded
        self.assertEqual((vis[55, 18], vis[55, 77], vis[60, 50], vis[5, 5]), (2, 3, 1, 0))
        st2, _, _ = state_and_visibility(label, roi, (100, 80), occl, occlusion_class=True)
        self.assertEqual(st2[55, 18], 2)
        st3, _, _ = state_and_visibility(None, roi, (100, 80))
        self.assertTrue((st3 == 255).all())

    def test_transform_rgb_mask_alignment(self):
        img = Image.new("RGB", (400, 200), (0, 0, 0))
        mask = np.zeros((200, 400), np.uint8)
        img.paste((255, 0, 0), (300, 50, 340, 90))
        mask[50:90, 300:340] = 1
        for mode, box in (("letterbox", None), ("stretch", None), ("letterbox", (250, 20, 390, 150))):
            T = make_transform((400, 200), (160, 160), mode, box)
            im2 = np.asarray(apply_image(img, T))
            m2 = apply_mask(mask, T, 255)
            red = (im2[..., 0] > 128) & (im2[..., 1] < 100)
            inter = (red & (m2 == 1)).sum() / max(1, (m2 == 1).sum())
            self.assertGreater(inter, 0.85, (mode, box))
            self.assertTrue(set(np.unique(m2)) <= {0, 1, 255})
            ys, xs = np.nonzero(m2 == 1)
            x0, y0 = to_original(xs.min(), ys.min(), T)
            self.assertAlmostEqual(x0, 300, delta=3)
            self.assertAlmostEqual(y0, 50, delta=3)

    def test_target_dtype_overflow(self):
        self.assertEqual(target_dtype(254)[0], np.uint8)
        self.assertEqual(target_dtype(255)[0], np.uint16)
        with self.assertRaises(OverflowError):
            target_dtype(70000)


class TestLinksAndPropagation(PipelineCase):
    def test_multi_polygon_one_target(self):
        o = self.obs[self.v1[0]["image_id"] + "|W01"]
        self.assertEqual(sorted(l["shape_index"] for l in o["links"]), [0, 1])
        self.assertEqual(o["review_status"], "draft")

    def test_spanning_polygon_is_review_item_not_link(self):
        o1 = self.obs[self.v1[0]["image_id"] + "|W01"]
        o2 = self.obs[self.v1[0]["image_id"] + "|W02"]
        self.assertIn(3, [r["shape_index"] for r in o1["review_items"] if r["kind"] == "split_candidate"])
        self.assertIn(3, [r["shape_index"] for r in o2["review_items"] if r["kind"] == "split_candidate"])
        self.assertNotIn(3, [l["shape_index"] for l in o1["links"] + o2["links"]])

    def test_nothing_auto_approved(self):
        self.assertEqual(self.store.approvals(), [])
        self.assertTrue(all(o["review_status"] == "draft" for o in self.obs.values()))

    def test_no_roi_is_not_absence(self):
        o = self.obs[self.v1[0]["image_id"] + "|F01"]
        self.assertEqual((o["roi_source"], o["status"]), ("none", "no_roi"))
        self.assertEqual(self.store.latest("state_label", self.v1[0]["image_id"] + "|F01"), [])

    def test_cross_view_registration_is_per_target_and_unconfirmed(self):
        v2 = [o for o in self.obs.values() if o["view_id"] == "V02"]
        w02 = [o for o in v2 if o["target_id"] == "W02"]
        self.assertTrue(all(o["roi_source"] == "registered_draft" for o in w02), [o["status"] for o in w02])
        w01 = [o for o in v2 if o["target_id"] == "W01"]
        self.assertTrue(all(o["status"].startswith("roi_pending") for o in w01))   # failure -> held, not reused
        e = eligibility(self.store, self.by_id[w02[0]["image_id"]], w02[0], "W02", ACT)
        self.assertIn("roi_source_registered_draft_requires_manual_confirmation", e["reasons"])


class TestReviewApprovals(PipelineCase):
    def test_01_unreviewed_labels_block_training(self):
        for im in self.v1[:3]:
            self.label(im)
        out = quiet(export, self.ctx, mode="train") if self._split() else None
        self.assertEqual(out["status"], "blocked_no_approved_labels")
        self.assertEqual(out["samples_written"], 0)

    def _split(self):
        quiet(make_splits, self.ctx)
        return True

    def test_02_partial_approval_export(self):
        self.approve_chain("W01")
        assign = load_assignments(self.ctx) if self._split() else None
        train_imgs = [im for im in self.v1 if assign[im["image_id"]]["split"] == "train"]
        im = train_imgs[0]
        self.label(im)
        self.label(im, target="W02", fill="installed", regions=[])
        k = f"{im['image_id']}|W01|{ACT}"
        self.store.decide("state_label", k, self.store.current_version("state_label", k), REVIEWER)
        out = quiet(export, self.ctx, mode="train", size=None, resize_mode="none")
        self.assertEqual(out["status"], "ok")
        exp = Path(out["export_dir"])
        metas = [json.loads(l) for l in (exp / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
        meta = next(m for m in metas if m["image_id"] == im["image_id"])
        st = load_mask_png(exp / meta["activities"][ACT]["file"])
        self.assertEqual(st[180, 40], 1)          # W01 approved: installed region
        self.assertEqual(st[300, 50], 0)          # W01 fill not_installed
        self.assertEqual(st[200, 500], 255)       # W02 has only a draft label -> ignore
        self.assertEqual(st[175, 175], 255)       # door exclusion
        self.assertEqual(st[200, 80], 255)        # person occluder candidate ignored
        self.assertFalse(meta["activities"]["floor_finish_installation"]["valid"])
        w02 = next(t for t in meta["targets"] if t["target_id"] == "W02")
        self.assertFalse(w02["activities"][ACT]["label_used"])
        v = quiet(validate_export, exp, self.store)
        self.assertTrue(v["passed"], v["problems"])
        crop = quiet(export, self.ctx, mode="train", crop=True, size=[128, 128])
        v2 = quiet(validate_export, Path(crop["export_dir"]), self.store)
        self.assertTrue(v2["passed"], v2["problems"])
        self.__class__.approved_image = im

    def test_03_roi_change_invalidates_approval_and_cache(self):
        im = self.__class__.approved_image
        k = f"{im['image_id']}|W01|{ACT}"
        self.assertEqual(self.store.review_state("state_label", k)["status"], "approved")
        old = load_observations(self.ctx)[im["image_id"] + "|W01"]["roi_ref"]["version"]
        r = save_roi(self.store, target_id="W01", view_id="V01", reference_image=self.ref,
                     polygons=[{"exterior": [[0, 45], [250, 85], [250, 260], [0, 330]], "holes": []}], exclusions=[],
                     author=REVIEWER)
        self.assertTrue(r["invalidated"])                       # ROI approval + dependent label approval
        self.assertEqual(self.store.review_state("state_label", k)["status"], "invalidated")
        self.assertEqual(self.store.review_state("roi", "W01|V01")["status"], "invalidated")
        quiet(build_observations, self.ctx)
        new = load_observations(self.ctx)[im["image_id"] + "|W01"]["roi_ref"]["version"]
        self.assertEqual(new, old + 1)                          # cache recomputed with new ROI version
        with self.assertRaises(StoreError):                     # approving a label drawn on old ROI is refused
            self.store.decide("state_label", k, self.store.current_version("state_label", k), REVIEWER)
        out = quiet(export, self.ctx, mode="train")
        self.assertEqual(out["status"], "blocked_no_approved_labels")

    def test_04_persistence_after_restart(self):
        path = self.ctx.info["resolved"]["store_path"]
        before = self.store.approvals()
        s2 = ReviewStore(path, "SITE_A")
        self.assertEqual(len(s2.approvals()), len(before))
        self.assertEqual(len(s2.history("roi", "W01|V01")), 2)
        s2.close()

    def test_05_reviewer_and_manual_protection(self):
        k = "W02|V01"
        with self.assertRaises(StoreError):
            self.store.decide("roi", k, self.store.current_version("roi", k), "auto")
        with self.assertRaises(ManualRecordProtected):
            self.store.put("roi", k, {"x": 1}, source="model_draft", author="pipeline")

    def test_06_split_no_leakage(self):
        out = quiet(make_splits, self.ctx)
        self.assertTrue(out["leakage_check"]["passed"])
        assign = load_assignments(self.ctx)
        groups = {}
        for row in assign.values():
            groups.setdefault(row["sha"], set()).add(row["split"])
        self.assertTrue(all(len(s) == 1 for s in groups.values()))
        by_day = {}
        for row in assign.values():
            by_day.setdefault((row["view_id"], row["block"]), set()).add(row["split"])
        self.assertTrue(all(len(s) == 1 for s in by_day.values()))
        self.assertIn("excluded_gap", {r["split"] for r in assign.values()})


class TestReviewServer(PipelineCase):
    def _serve(self):
        from http.server import ThreadingHTTPServer
        from vision_preprocessing.review_app import ReviewService, make_handler
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, svc, f"http://127.0.0.1:{srv.server_address[1]}"

    @staticmethod
    def _call(base, path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
                return 200, (json.loads(data) if r.headers.get("Content-Type", "").startswith("application/json") else data)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_save_approve_restart(self):
        srv, svc, base = self._serve()
        try:
            self.assertEqual(self._call(base, "/api/meta")[0], 200)
            im = self.v1[2]
            code, img = self._call(base, f"/api/image/{im['image_id']}?max=320")
            self.assertEqual(code, 200)
            self.assertEqual(Image.open(io.BytesIO(img)).size[0], 320)
            body = {"author": "", "image_id": im["image_id"], "target_id": "W02", "activity_id": ACT,
                    "regions": [], "fill_roi_state": "installed", "undeterminable": [], "occluded": []}
            self.assertEqual(self._call(base, "/api/label", body)[0], 400)            # reviewer required
            body["author"] = REVIEWER
            code, res = self._call(base, "/api/label", body)
            self.assertEqual(code, 200, res)
            key = f"{im['image_id']}|W02|{ACT}"
            code, res = self._call(base, "/api/decide", {"author": REVIEWER, "kind": "state_label", "key": key,
                                                         "version": res["version"]})
            self.assertEqual(code, 200, res)
        finally:
            srv.shutdown()
            srv.server_close()
            svc.reader.close()
        srv, svc, base = self._serve()   # restart: state reloaded from the store
        try:
            code, item = self._call(base, f"/api/item/{im['image_id']}")
            lab = item["targets"]["W02"]["activities"][ACT]["label"]
            self.assertEqual((lab["review"], lab["payload"]["fill_roi_state"]), ("approved", "installed"))
            code, page = self._call(base, "/")
            self.assertIn(b"State Label Review", page)
        finally:
            srv.shutdown()
            srv.server_close()
            svc.reader.close()


class TestPostprocess(unittest.TestCase):
    def test_measure_ratio_and_hold(self):
        from vision_preprocessing.postprocess import measure_sample
        target = np.zeros((10, 10), np.uint8)
        target[:, :5] = 1
        vis = np.where(target == 1, 1, 0).astype(np.uint8)
        vis[:5, :5] = 2
        state = np.full((10, 10), 255, np.uint8)
        state[5:, :5] = 1
        state[9, :5] = 0
        out = measure_sample(state, target, vis, [{"target_id": "W01", "index": 1}],
                             {"installed": 1, "not_installed": 0}, min_roi_px=10)
        self.assertEqual(out[0]["installed_ratio_within_observed"], 0.8)
        self.assertEqual(out[0]["observed_fraction"], 0.5)
        vis[:, :5] = 2
        out = measure_sample(state, target, vis, [{"target_id": "W01", "index": 1}],
                             {"installed": 1, "not_installed": 0}, min_roi_px=10)
        self.assertEqual(out[0]["status"], "held")


if __name__ == "__main__":
    unittest.main()
