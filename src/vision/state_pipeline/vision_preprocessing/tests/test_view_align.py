"""align stage: segment logic, and the to-do fixes (trim / split / new reference) on a synthetic store."""
import json
import unittest

import numpy as np

from vision_preprocessing.view_align import DEFAULTS, find_segments
from vision_preprocessing.tests.test_pipeline import REVIEWER, PipelineCase


def row(t, st, sh=None, v="V01"):
    return {"_key": t, "image_id": "I" + t, "time": f"2025-01-01T{t}:00", "view_id": v, "view_version": 1, "status": st,
            **({"shift": sh} if sh is not None else {})}


class TestSegments(unittest.TestCase):
    def test_tail_head_middle_reference(self):
        rows = [row(f"{h:02d}:00", "same", 0.001) for h in range(0, 10)]
        tail = rows + [row("10:00", "shifted", 0.05), row("10:10", "uncertain"), row("10:20", "shifted", 0.05), row("10:30", "dark")]
        segs = find_segments({"V01": tail}, DEFAULTS)
        self.assertEqual([(s["kind"], s["suggest"], s["photos"]) for s in segs], [("tail", "trim", 4)])
        head = [row("00:0%d" % i, "shifted", 0.2) for i in range(3)] + rows[1:]
        self.assertEqual(find_segments({"V01": head}, DEFAULTS)[0]["kind"], "head")
        mid = rows[:5] + [row(f"05:{m:02d}", "shifted", 0.03) for m in range(0, 50, 5)] + rows[6:]
        s = find_segments({"V01": mid}, DEFAULTS)[0]
        self.assertEqual((s["kind"], s["suggest"]), ("middle", "split"))
        ref = [rows[0]] + [row(f"0{h}:30", "shifted", 0.03) for h in range(1, 9)]
        self.assertEqual(find_segments({"V01": ref}, DEFAULTS)[0]["suggest"], "reref")

    def test_one_matching_photo_does_not_cut_a_move(self):
        rows = [row(f"{h:02d}:00", "same", 0.001) for h in range(0, 10)]
        mv = [row(f"10:{m:02d}", "shifted", 0.03) for m in range(0, 30, 5)] + [row("10:31", "minor", 0.01)] + \
             [row(f"11:{m:02d}", "shifted", 0.03) for m in range(0, 30, 5)]
        segs = find_segments({"V01": rows + mv}, DEFAULTS)
        self.assertEqual([(s["kind"], s["photos"]) for s in segs], [("tail", 13)])

    def test_lighting_only_is_no_segment(self):
        rows = [row(f"{h:02d}:00", "same" if h % 2 else "uncertain", 0.001 if h % 2 else None) for h in range(12)]
        self.assertEqual(find_segments({"V01": rows}, DEFAULTS), [])


class TestViewFix(PipelineCase):
    def _svc(self):
        from vision_preprocessing.review_app import ReviewService
        from vision_preprocessing.runctx import RunContext
        return ReviewService(RunContext.open(self.ctx.run_dir))

    def _report(self, svc, kind, suggest, n_tail=3):
        from vision_preprocessing.index_views import assign_view, store_views
        views = store_views(svc.store)
        ims = sorted([im for im in svc.images if im["status"] == "ok" and not im.get("duplicate_of")
                      and assign_view(im, views)["view_id"] == "V01"], key=lambda im: im["capture"]["time_local"])
        ref = svc.store.get("view", "V01")["payload"]["reference_image_id"]
        seg_ims = [im for im in ims if im["image_id"] != ref][-n_tail:] if kind == "tail" else ims
        seg = {"view_id": "V01", "view_version": svc.store.get("view", "V01")["version"], "kind": kind, "suggest": suggest,
               "from": seg_ims[0]["capture"]["time_local"], "to": seg_ims[-1]["capture"]["time_local"],
               "photos": len(seg_ims), "shifted": len(seg_ims), "median_shift": 0.05,
               "example_image_id": seg_ims[len(seg_ims) // 2]["image_id"], "shifted_ids": [im["image_id"] for im in seg_ims],
               "view_photos": len(ims)}
        d = self.ctx.run_dir / "stages" / "view_align"
        d.mkdir(parents=True, exist_ok=True)
        (d / "align_report.json").write_text(json.dumps({"segments": [seg]}), encoding="utf-8")
        return seg

    def test_split_tail_creates_view_and_draft_rois(self):
        import vision_preprocessing.view_align as va
        svc = self._svc()
        orig = va.map_rois
        va.map_rois = lambda m, src, dst, size: (np.eye(3), 99)
        try:
            seg = self._report(svc, "tail", "split")
            todo = svc.todo()
            self.assertEqual([x["view_id"] for x in next(s for s in todo["sections"] if s["id"] == "view_misaligned")["items"]], ["V01"])
            r = svc.post("/api/view_fix", {"author": REVIEWER, "view_id": "V01", "from": seg["from"], "to": seg["to"], "action": "split"})
            new = r["new_view"]
            v1, vn = svc.store.get("view", "V01")["payload"], svc.store.get("view", new)["payload"]
            self.assertLess(v1["end_time"], seg["from"])
            self.assertEqual((vn["start_time"], vn["reference_image_id"]), (seg["from"], seg["example_image_id"]))
            self.assertEqual(svc.store.review_state("view", new)["status"], "approved")
            for k in r["rois_drafted"]:
                self.assertNotEqual(svc.store.review_state("roi", k)["status"], "approved")   # drafts only
            self.assertFalse(next(s for s in svc.todo()["sections"] if s["id"] == "view_misaligned")["items"])  # resolved
            with self.assertRaises(Exception):                                               # stale item refused
                svc.post("/api/view_fix", {"author": REVIEWER, "view_id": "V01", "from": seg["from"], "to": seg["to"], "action": "trim"})
        finally:
            va.map_rois = orig
            svc.reader.close()


if __name__ == "__main__":
    unittest.main()
