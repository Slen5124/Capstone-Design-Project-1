"""train/data_prep.py (torch-free): unified wall+ceiling state layout, hold-out city reading, target restriction."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from vision_preprocessing.train.data_prep import Surfaces, _parse_ids, load_item, read_export, state_target

ACTS = ("wall_board_installation", "ceiling_panel_installation")


def fake_export(root: Path, n=6, h=64, w=96, ceiling_applicable=True):
    for d in ("images", "semantic_masks", "target_masks", "visibility_masks", "splits") + tuple(f"state_masks/{a}" for a in ACTS):
        (root / d).mkdir(parents=True, exist_ok=True)
    meta, ids = [], []
    for i in range(n):
        sid = f"SITE-202502{10 + i:02d}T100000-{i:08x}"
        ids.append(sid)
        img = np.full((h, w, 3), 120, np.uint8)
        sem = np.zeros((h, w), np.uint8)
        sem[: h // 4] = 2                          # ceiling
        sem[h - 8:] = 1                            # floor
        sem[20:30, 10:20] = 7                      # a person on the wall
        sem[h // 4: h // 4 + 4, 60:80] = 4         # duct under the ceiling (outside every ROI)
        tg = np.zeros((h, w), np.uint8)
        tg[h // 4 + 6: h - 8, : w // 2] = 3        # W01
        tg[h // 4 + 6: h - 8, w // 2:] = 4         # W02
        tg[2: h // 4 - 2, 10: w - 10] = 1          # C01
        wall = np.full((h, w), 255, np.uint8)
        wall[np.isin(tg, (3, 4))] = 0
        wall[h // 4 + 6: h - 8, : (w // 2) * i // n] = 1
        wall[sem == 7] = 255
        ceil = np.full((h, w), 255, np.uint8)
        if ceiling_applicable:
            ceil[tg == 1] = 1 if i % 2 else 0
        vis = np.zeros((h, w), np.uint8)
        vis[tg > 0] = 1
        vis[(tg > 0) & (sem == 7)] = 2
        for arr, sub in ((img, "images"), (sem, "semantic_masks"), (tg, "target_masks"), (vis, "visibility_masks"),
                         (wall, f"state_masks/{ACTS[0]}"), (ceil, f"state_masks/{ACTS[1]}")):
            Image.fromarray(arr).save(root / sub / f"{sid}.{'jpg' if sub == 'images' else 'png'}")
        meta.append({"sample_id": sid, "image_id": sid, "split": "train",
                     "files": {"image": f"images/{sid}.jpg", "semantic": f"semantic_masks/{sid}.png",
                               "target": f"target_masks/{sid}.png", "visibility": f"visibility_masks/{sid}.png"},
                     "capture": {"time_local": f"2025-02-{10 + i:02d}T10:00:00"}})
    (root / "metadata.jsonl").write_text("\n".join(json.dumps(m) for m in meta), encoding="utf-8")
    (root / "splits" / "train.txt").write_text("\n".join(ids[:4]), encoding="utf-8")
    (root / "splits" / "val.txt").write_text(ids[4], encoding="utf-8")
    (root / "splits" / "test.txt").write_text(ids[5], encoding="utf-8")
    app = "applicable" if ceiling_applicable else "not_applicable"
    (root / "targets.json").write_text(json.dumps({"targets": [
        {"index": 1, "target_id": "C01", "surface_type": "ceiling", "activities": {ACTS[1]: {"applicability": app}}},
        {"index": 3, "target_id": "W01", "surface_type": "wall", "activities": {ACTS[0]: {"applicability": "applicable"}}},
        {"index": 4, "target_id": "W02", "surface_type": "wall", "activities": {ACTS[0]: {"applicability": "applicable"}}}]}),
        encoding="utf-8")
    return ids


class TestSurfaces(unittest.TestCase):
    def test_layouts(self):
        s1 = Surfaces("wall_board_installation")
        self.assertEqual((s1.n_state, s1.occ, s1.nt, s1.class_names), (4, 2, 3, ["not_installed", "installed", "occluded", "not_target"]))
        self.assertEqual(s1.nt_sem_full, (1, 2, 4, 5, 6, 7))
        s2 = Surfaces("all")
        self.assertEqual((s2.n_state, s2.occ, s2.nt), (6, 4, 5))
        self.assertEqual(s2.class_names[:4], ["wall_not_installed", "wall_installed", "ceiling_not_installed", "ceiling_installed"])
        self.assertEqual(s2.nt_sem_full, (1, 4, 5, 6, 7))          # ceiling pixels are no longer "not target"

    def test_parse_ids(self):
        d = _parse_ids(["a", "b|W01", "b|W02", "# c", "", "a|W01"])
        self.assertEqual(d, {"a": None, "b": {"W01", "W02"}})


class TestUnifiedMasks(unittest.TestCase):
    def test_single_surface_unchanged_and_unified_layout(self):
        tmp = Path(tempfile.mkdtemp())
        fake_export(tmp)
        wall = Surfaces("wall_board_installation")
        items = read_export(tmp, wall)
        self.assertEqual({it["split"] for it in items}, {"train", "val", "test"})
        it = items[3]
        img, sem, st, vis, tg = load_item(it, wall)
        self.assertEqual(set(np.unique(st)) - {255}, {0, 1})
        t = state_target(st, vis, sem, tg, it, wall)
        self.assertEqual(int(t[25, 15]), 2)                       # person on the wall -> occluded
        self.assertEqual(int(t[2, 5]), 3)                         # ceiling pixel outside the C01 ROI -> not_target (wall model)
        self.assertEqual(int(t[h := 16, 70]), 3)                  # duct -> not_target
        both = Surfaces("all")
        it2 = read_export(tmp, both)[3]
        img, sem, st, vis, tg = load_item(it2, both)
        self.assertEqual(set(np.unique(st)) - {255}, {0, 1, 3})   # wall 0/1, ceiling installed (odd index) = 3
        t = state_target(st, vis, sem, tg, it2, both)
        self.assertEqual(int(t[25, 15]), 4)                       # occluded = 4
        self.assertEqual(int(t[16, 70]), 5)                       # duct -> not_target = 5
        self.assertEqual(int(t[2, 5]), 255)                       # ceiling pixel outside C01: unknown, not "not target"
        self.assertEqual(int(t[5, 20]), 3)                        # inside C01, installed

    def test_not_applicable_ceiling_stays_ignored(self):
        tmp = Path(tempfile.mkdtemp())
        fake_export(tmp, ceiling_applicable=False)
        both = Surfaces("all")
        it = read_export(tmp, both)[1]
        self.assertEqual(it["slot_of"], {3: 0, 4: 0})
        _, _, st, _, _ = load_item(it, both)
        self.assertEqual(set(np.unique(st)) - {255}, {0, 1})

    def test_holdout_city_split_and_target_restriction(self):
        tmp = Path(tempfile.mkdtemp())
        ids = fake_export(tmp)
        both = Surfaces("all")
        hold = _parse_ids([ids[0], f"{ids[2]}|W01"])
        items = read_export(tmp, both, holdout_ids=hold)
        by = {it["id"]: it for it in items}
        self.assertEqual(by[ids[0]]["split"], "holdout")
        self.assertIsNone(by[ids[0]]["only_targets"])
        self.assertEqual((by[ids[2]]["split"], by[ids[2]]["only_targets"]), ("holdout", {3}))
        self.assertTrue(all(by[i]["split"] == "holdout_weak" for i in ids if i not in (ids[0], ids[2])))
        self.assertFalse(any(it["split"] == "train" for it in items))
        _, _, st, vis, tg = load_item(by[ids[2]], both)
        self.assertTrue((st[tg == 4] == 255).all())                # W02 not human-labelled -> ignored
        self.assertTrue((st[tg == 1] == 255).all())                # C01 too
        self.assertTrue(((st[tg == 3] != 255)).any())             # W01 graded
        t = state_target(st, vis, sem := np.zeros_like(tg), tg, by[ids[2]], both)
        self.assertTrue((t[tg == 4] == 255).all())                 # no occluded / not_target inside the ignored W02 either


if __name__ == "__main__":
    unittest.main()
