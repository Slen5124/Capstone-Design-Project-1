"""To-do page: ROI audit findings, ignoring not-applicable targets in overlap checks, gaps between views, acks."""
import json
import tempfile
import types
import unittest
from pathlib import Path

from vision_preprocessing.store import ReviewStore
from vision_preprocessing.todo import build_todo, unassigned_gaps

SQ = [[0, 0], [100, 0], [100, 100], [0, 100]]


def im(iid, t):
    return {"image_id": iid, "camera_id": "C1", "status": "ok", "capture": {"time_local": t}}


class TestTodoAudit(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="vp_todo_"))
        self.st = st = ReviewStore(d / "s.sqlite")
        for vid, a, b in (("V01", "2025-01-01T00:00:00", "2025-01-01T10:00:00"), ("V02", "2025-01-02T00:00:00", "2025-01-02T10:00:00")):
            st.put("view", vid, {"view_id": vid, "start_time": a, "end_time": b, "reference_image_id": f"{vid}-ref"}, source="manual", author="t")
        for tid in ("W01", "F01"):
            st.put("target", tid, {"target_id": tid}, source="manual", author="t")
        r = st.put("applicability", "F01|floor", {"target_id": "F01", "activity_id": "floor", "applicability": "not_applicable"}, source="manual", author="t")
        st.decide("applicability", "F01|floor", r["version"], "t", "approved")
        st.put("applicability", "W01|wall", {"target_id": "W01", "activity_id": "wall", "applicability": "applicable"}, source="manual", author="t")
        for key in ("W01|V01", "F01|V01"):      # F01 is a copy of W01, but F01 is never measured
            st.put("roi", key, {"polygons": [{"exterior": SQ}], "exclusions": [], "image_size": [200, 200],
                                "reference_image_id": "V01-ref"}, source="manual", author="t")
        (d / "stages" / "roi_audit").mkdir(parents=True)
        (d / "stages" / "roi_audit" / "findings.json").write_text(json.dumps({"findings": [
            {"id": "f1", "view_id": "V01", "severity": "fix", "rois": {"W01|V01": 1}, "title": "x", "images": []}]}), encoding="utf-8")
        self.ctx = types.SimpleNamespace(store=lambda: st, run_dir=d, site={}, info={"run_id": "r"})
        self.images = [im("V01-ref", "2025-01-01T01:00:00"), im("gap1", "2025-01-01T12:00:00"), im("gap2", "2025-01-01T13:00:00"),
                       im("V02-ref", "2025-01-02T01:00:00")]

    def sec(self, sid):
        return next((s for s in build_todo(self.ctx, self.images)["sections"] if s["id"] == sid), None)

    def test_not_applicable_target_not_a_conflict(self):
        self.assertEqual(self.sec("roi_conflicts")["count"], 0)

    def test_audit_finding_resolves_when_roi_saved_again(self):
        self.assertEqual(self.sec("roi_audit")["count"], 1)
        self.st.put("roi", "W01|V01", {"polygons": [{"exterior": SQ}], "exclusions": [], "image_size": [200, 200]}, source="manual", author="t")
        self.assertEqual(self.sec("roi_audit")["count"], 0)

    def test_audit_ack_and_exclusion(self):
        self.st.put("todo_ack", "roi_audit|f1", {"key": "roi_audit|f1", "accepted": True}, source="manual", author="t")
        self.assertEqual(self.sec("roi_audit")["count"], 0)

    def test_gaps(self):
        g = self.sec("unassigned_photos")
        self.assertEqual(g["count"], 1)
        self.assertEqual((g["items"][0]["after_view"], g["items"][0]["before_view"], g["items"][0]["photos"]), ("V01", "V02", 2))
        self.st.put("todo_ack", "unassigned|V01-V02", {"key": "unassigned|V01-V02", "accepted": True}, source="manual", author="t")
        self.assertEqual(self.sec("unassigned_photos")["count"], 0)

    def test_gap_ack_by_photo_ids_survives_view_renames(self):
        self.st.put("todo_ack", "unassigned|OLD-NAMES", {"key": "x", "accepted": True, "image_ids": ["gap1", "gap2"]},
                    source="manual", author="t")
        self.assertEqual(self.sec("unassigned_photos")["count"], 0)

    def test_gap_function_edges(self):
        g = unassigned_gaps([im("a", "2024-12-31T00:00:00")], [{"view_id": "V01", "start_time": "2025-01-01T00:00:00", "end_time": "2025-01-02T00:00:00"}])
        self.assertEqual(g[0]["gap"], "start-V01")


if __name__ == "__main__":
    unittest.main()


def test_anchor_rehomed_when_photo_left_view():
    from vision_preprocessing.todo import _rehome_anchors
    im = lambda i, t: {"image_id": i, "capture": {"time_local": t}}
    items = [{"image_id": "a", "target_id": "W01", "activity_id": "w", "view_id": "V22", "day": "2025-10-30", "time": "2025-10-30T09:29:48"},
             {"image_id": "z", "target_id": "W01", "activity_id": "w", "view_id": "V23", "day": "2025-11-01", "time": "2025-11-01T09:00:00"}]
    view_of = {"b": "V22", "c": "V22", "z": "V23"}
    photos = {"V22": [im("c", "2025-10-30T11:00:00"), im("b", "2025-10-30T09:39:49"), im("d", "2025-10-31T09:30:00")]}
    out = _rehome_anchors(items, view_of, photos)
    assert [x["image_id"] for x in out] == ["b", "z"] and out[0]["time"] == "2025-10-30T09:39:49"
    # no photo of the view that day -> dropped
    assert _rehome_anchors(items[:1], {}, {"V22": [im("d", "2025-10-31T09:30:00")]}) == []
