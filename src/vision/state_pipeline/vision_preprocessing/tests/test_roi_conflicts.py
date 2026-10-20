"""ROI overlap / copied-polygon detection (the 'previous wall left in the draft' slip)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vision_preprocessing.store import ReviewStore
from vision_preprocessing.target_registry import roi_conflicts


def sq(x0, y0, x1, y1):
    return {"exterior": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "holes": []}


class TestRoiConflicts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vp_conf_")
        self.store = ReviewStore(Path(self.tmp) / "s.sqlite")
        self.store.put("roi", "W01|V1", {"polygons": [sq(0, 0, 100, 100)], "exclusions": []}, source="manual", author="t")
        self.store.put("roi", "C01|V1", {"polygons": [sq(0, 95, 300, 140)], "exclusions": []}, source="manual", author="t")

    def test_copied_polygon_is_flagged(self):
        c = roi_conflicts(self.store, "V1", "W02", [sq(0, 0, 100, 100), sq(200, 0, 300, 100)], min_overlap=0.5)
        self.assertEqual([(x["target_id"], x["polygon_copied_from_it"]) for x in c], [("W01", True)])

    def test_clean_neighbour_and_small_boundary_overlap_pass(self):
        self.assertEqual(roi_conflicts(self.store, "V1", "W02", [sq(110, 0, 200, 100)], min_overlap=0.10), [])

    def test_large_overlap_is_flagged(self):
        c = roi_conflicts(self.store, "V1", "W02", [sq(50, 0, 150, 100)], min_overlap=0.10)
        self.assertEqual(c[0]["target_id"], "W01")
        self.assertFalse(c[0]["polygon_copied_from_it"])


if __name__ == "__main__":
    unittest.main()
