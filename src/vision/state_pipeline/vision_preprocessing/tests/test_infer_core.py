"""Inference post-processing (ROI proposal, ratio, daily rows, events) - numpy only, no torch needed."""
from __future__ import annotations

import unittest

import numpy as np

from vision_preprocessing.train.infer_core import capture_time, daily_rows, events, measure, propose_roi, vision_output


def state_map(h=60, w=100, left=0.5, right=0.0, person=False):
    s = np.full((h, w), 3, np.uint8)                       # not_target everywhere
    s[10:50, 5:40] = 0                                      # left wall, not installed
    s[10:50, 5:5 + int(35 * left)] = 1                      # installed part
    s[10:50, 60:95] = 0                                     # right wall
    s[10:50, 60:60 + int(35 * right)] = 1
    if person:
        s[10:50, 5:30] = 2                                  # most of the left wall occluded
    return s


class TestInferCore(unittest.TestCase):
    def test_capture_time(self):
        self.assertEqual(capture_time("Example_-_#000-20250227-192337.jpg").isoformat(), "2025-02-27T19:23:37")
        self.assertIsNone(capture_time("no_time.jpg"))

    def test_propose_roi_two_walls_left_to_right(self):
        roi, targets = propose_roi([state_map(), state_map(left=0.2), state_map(person=True)])
        self.assertEqual([t["target_id"] for t in targets], ["A01", "A02"])
        self.assertTrue(all(t["roi_status"] == "provisional" for t in targets))
        self.assertLess(targets[0]["bbox_xyxy"][0], targets[1]["bbox_xyxy"][0])
        self.assertEqual(int(roi[30, 20]), 1)
        self.assertEqual(int(roi[30, 80]), 2)
        self.assertEqual(int(roi[5, 50]), 0)

    def test_measure_ratio_and_held(self):
        roi, targets = propose_roi([state_map()] * 3)
        rows = {r["target_id"]: r for r in measure(state_map(left=0.6), roi, targets, rules=dict(_rules(), min_roi_px=10))}
        self.assertAlmostEqual(rows["A01"]["installed_ratio"], 21 / 35, places=2)
        self.assertEqual(rows["A02"]["installed_ratio"], 0.0)
        held = {r["target_id"]: r for r in measure(state_map(person=True), roi, targets, rules=dict(_rules(), min_roi_px=10))}
        self.assertEqual(held["A01"]["status"], "held")
        self.assertIsNone(held["A01"]["installed_ratio"])                     # never 0 when not observable

    def test_daily_rows_and_events(self):
        seq = [("2025-03-01", 0.10), ("2025-03-02", 0.50), ("2025-03-03", 0.44), ("2025-03-04", None),
               ("2025-03-09", 0.99), ("2025-03-10", 0.99)]
        rows = []
        for d, v in seq:
            rows.append({"date": d, "target_id": "A01", "installed_ratio": v, "observed_fraction": 0.9 if v is not None else 0.1,
                         "status": "ok" if v is not None else "held", "hold_reason": None if v is not None else ["observed_fraction<0.3"]})
        daily = daily_rows(rows, "SITE_B", "wall_board_installation", "V01", provisional=True)
        ch = [r["change"] for r in daily]
        self.assertEqual(ch, ["first_value", "increase", "decrease_suspect", "held", "increase", "no_change"])
        self.assertIsNone(daily[3]["installed_ratio"])
        self.assertEqual(daily[4]["previous_date"], "2025-03-03")               # held day is skipped, not carried
        types = [(e["date"], e["type"]) for e in events(daily)]
        self.assertIn(("2025-03-02", "large_increase"), types)
        self.assertIn(("2025-03-03", "decrease_suspect"), types)
        self.assertIn(("2025-03-09", "long_gap"), types)
        self.assertIn(("2025-03-10", "installation_complete"), types)
        doc = vision_output("SITE_B", daily, [{"target_id": "A01", "roi_status": "provisional"}], {"view_id": "V01"}, {"source": "model"})
        self.assertEqual(doc["schema_version"], "vision_output/1.0")
        self.assertTrue(doc["warnings"])
        self.assertTrue(all(r["provisional"] for r in doc["daily_progress"]))


def _rules():
    from vision_preprocessing.train.infer_core import RULES
    return dict(RULES)


if __name__ == "__main__":
    unittest.main()
