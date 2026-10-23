"""Label / ROI saves must not fail for reasons the reviewer cannot see: points outside the photo, a crossing edge,
an observation that the background refresh has not reached yet. Failed saves are logged."""
import json
import unittest

from vision_preprocessing.schemas import sanitize_polygons
from vision_preprocessing.tests.test_pipeline import ACT, REVIEWER, PipelineCase, TAX
from vision_preprocessing.target_registry import save_roi
from vision_preprocessing.tests.fixtures import W01


class TestSanitize(unittest.TestCase):
    def test_clamp_and_repair(self):
        out, notes = sanitize_polygons([{"exterior": [[-5, -5], [60, 0], [60, 60], [0, 60]]}], (50, 50))
        self.assertEqual(out[0]["exterior"], [[0.0, 0.0], [50.0, 0.0], [50.0, 50.0], [0.0, 50.0]])
        self.assertEqual(len(notes), 1)
        out, notes = sanitize_polygons([{"exterior": [[0, 0], [10, 10], [10, 0], [0, 10]]}], (50, 50))   # bow-tie
        self.assertEqual(len(out), 2)
        self.assertTrue(notes)

    def test_valid_polygon_untouched(self):
        p = [{"exterior": [[1.25, 1], [20, 1], [20, 20]], "holes": []}]
        out, notes = sanitize_polygons(p, (50, 50))
        self.assertIs(out[0], p[0])
        self.assertEqual(notes, [])


class TestRobustSaves(PipelineCase):
    def test_bowtie_label_saved_with_fix_note(self):
        im = self.v1[3]
        bow = [[20, 120], [120, 240], [120, 120], [20, 240]]
        r = self.label(im, regions=[{"state": "installed", "polygons": [{"exterior": bow, "holes": []}]}])
        self.assertTrue(r.get("fixed"))
        saved = self.store.get("state_label", f"{im['image_id']}|W01|{ACT}")["payload"]["regions"][0]["polygons"]
        self.assertEqual(len(saved), 2)

    def test_resave_same_roi_no_new_version(self):
        rec = self.store.get("roi", "W02|V01")
        r = save_roi(self.store, target_id="W02", view_id="V01", reference_image=self.ref, polygons=rec["payload"]["polygons"],
                     exclusions=rec["payload"]["exclusions"], author=REVIEWER)
        self.assertEqual(self.store.get("roi", "W02|V01")["version"], rec["version"])
        self.assertFalse(r.get("changed", True) and r.get("version") != rec["version"])

    def test_label_save_after_roi_change_without_manual_refresh(self):
        from vision_preprocessing.review_app import ReviewService
        from vision_preprocessing.runctx import RunContext
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            im = self.v1[4]
            moved = [[x + (3 if i == 0 else 0), y] for i, (x, y) in enumerate(W01)]
            save_roi(svc.store, target_id="W01", view_id="V01", reference_image=self.ref, polygons=[{"exterior": moved, "holes": []}],
                     exclusions=[], author=REVIEWER)          # store changed behind the server's back
            body = {"author": REVIEWER, "image_id": im["image_id"], "target_id": "W01", "activity_id": ACT, "regions": [],
                    "fill_roi_state": "not_installed", "undeterminable": [], "occluded": []}
            r = svc.post("/api/label", body)
            self.assertEqual(r["payload"]["roi_ref"]["version"] if "payload" in r else
                             svc.store.get("state_label", f"{im['image_id']}|W01|{ACT}")["payload"]["roi_ref"]["version"],
                             svc.store.get("roi", "W01|V01")["version"])
            svc.log_error("/api/label", body, "test message")
            rows = [json.loads(x) for x in (self.ctx.run_dir / "review_errors.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[-1]["error"], "test message")
        finally:
            svc.reader.close()


    def test_roi_save_answers_with_record_and_stale_approve_tells_current(self):
        """After 'Save ROI draft' the UI shows 'Approve ROI vN' from the answer; approving an older version by mistake
        gets the current version (and its author) back so the reviewer can approve that one with one click."""
        from vision_preprocessing.review_app import ReviewService, StaleVersion
        from vision_preprocessing.runctx import RunContext
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            old = svc.store.get("roi", "W01|V01")["version"]
            moved = [[x + (5 if i == 0 else 0), y] for i, (x, y) in enumerate(W01)]
            r = svc.post("/api/roi", {"author": REVIEWER, "image_id": self.ref["image_id"], "target_id": "W01", "view_id": "V01",
                                      "polygons": [{"exterior": moved, "holes": []}], "exclusions": []})
            self.assertEqual(r["record"]["version"], old + 1)
            self.assertEqual(r["record"]["review"], "unreviewed")
            with self.assertRaises(StaleVersion) as cm:
                svc.post("/api/decide", {"author": REVIEWER, "kind": "roi", "key": "W01|V01", "version": old})
            self.assertEqual(cm.exception.current["version"], old + 1)
            self.assertEqual(cm.exception.current["author"], REVIEWER)
            svc.post("/api/decide", {"author": REVIEWER, "kind": "roi", "key": "W01|V01", "version": old + 1})
            self.assertEqual(svc.store.review_state("roi", "W01|V01")["status"], "approved")
        finally:
            svc.reader.close()
            svc.obs_reader.close()

    def test_image_list_patches_only_refreshed_rows(self):
        from vision_preprocessing.review_app import ReviewService
        from vision_preprocessing.runctx import RunContext
        svc = ReviewService(RunContext.open(self.ctx.run_dir))
        try:
            rows = svc.image_list()
            im = self.v1[2]
            moved = [[x + (4 if i == 0 else 0), y] for i, (x, y) in enumerate(W01)]
            save_roi(svc.store, target_id="W01", view_id="V01", reference_image=self.ref, polygons=[{"exterior": moved, "holes": []}],
                     exclusions=[], author=REVIEWER)
            rows2 = svc.image_list()                              # store changed: full rebuild, rows now stale
            row = next(x for x in rows2 if x["image_id"] == im["image_id"])
            self.assertTrue(row["obs"]["W01"]["stale"])
            svc._reassign([im])                                   # background refresh of one photo
            self.assertEqual(svc._dirty_rows, {im["image_id"]})
            rows3 = svc.image_list()
            self.assertIsNot(rows3, rows2)
            row = next(x for x in rows3 if x["image_id"] == im["image_id"])
            self.assertFalse(row["obs"]["W01"]["stale"])
            others = [x["image_id"] for x in rows2 if x["image_id"] != im["image_id"] and x["obs"].get("W01", {}).get("stale")]
            self.assertTrue(others)
            self.assertTrue(all(x["obs"]["W01"]["stale"] for x in rows3 if x["image_id"] in others))   # untouched rows kept
            self.assertEqual(svc._dirty_rows, set())
            self.assertIs(svc.image_list(), rows3)                 # nothing changed: same object, no rebuild
            b1 = svc.image_list_bytes(); b2 = svc.image_list_bytes()
            self.assertIs(b1, b2)
        finally:
            svc.reader.close()
            svc.obs_reader.close()

    def test_no_cross_view_registration_for_hidden_or_unmeasured_targets(self):
        """A wall marked 'not visible' in a view (or a not_applicable floor) gets no_roi at once instead of a
        full-resolution registration attempt against every other view's ROI."""
        import tempfile
        from pathlib import Path
        from vision_preprocessing.assign_surfaces import build_observations
        from vision_preprocessing.runctx import ItemCache
        from vision_preprocessing.schemas import obs_key
        from vision_preprocessing.index_views import assign_view, store_views
        im = next(x for x in self.images if assign_view(x, store_views(self.store))["view_id"] == "V02" and x["status"] == "ok")
        c = ItemCache(Path(tempfile.mkdtemp()) / "o.jsonl")
        build_observations(self.ctx, images=[im], cache=c, quiet=True)
        self.assertEqual(c.items[obs_key(im["image_id"], "W01")]["value"]["status"], "roi_pending_registration_failed")
        self.store.put("roi_skip", "W01|V02", {"target_id": "W01", "view_id": "V02", "visible": False, "reason": "not_visible_in_view"},
                       source="manual", author=REVIEWER)
        c2 = ItemCache(Path(tempfile.mkdtemp()) / "o2.jsonl")
        build_observations(self.ctx, images=[im], cache=c2, quiet=True)
        self.assertEqual(c2.items[obs_key(im["image_id"], "W01")]["value"]["status"], "no_roi")
        self.assertNotEqual(c.items[obs_key(im["image_id"], "W01")]["_fp"], c2.items[obs_key(im["image_id"], "W01")]["_fp"])


class TestGuidePage(unittest.TestCase):
    def test_guide_renders(self):
        from vision_preprocessing.review_app import render_guide
        h = render_guide().decode("utf-8")
        self.assertIn("<h1>", h)
        self.assertIn("<table>", h)
        self.assertIn("검수", h)


if __name__ == "__main__":
    unittest.main()
