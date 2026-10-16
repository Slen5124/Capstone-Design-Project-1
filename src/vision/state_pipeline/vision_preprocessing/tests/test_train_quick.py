"""Smoke test for train/train_quick.py on a tiny synthetic export (code behaviour only; skipped without torch/timm)."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import torch  # noqa: F401
    import timm  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False


def fake_export(root: Path, n=12, h=96, w=160):
    rng = np.random.default_rng(0)
    for d in ("images", "semantic_masks", "target_masks", "visibility_masks", "state_masks/wall_board_installation", "splits"):
        (root / d).mkdir(parents=True, exist_ok=True)
    meta, ids = [], []
    for i in range(n):
        sid = f"SITE-202502{10 + i:02d}T100000-{i:08x}"
        ids.append(sid)
        img = (rng.random((h, w, 3)) * 255).astype(np.uint8)
        sem = np.zeros((h, w), np.uint8)
        sem[: h // 4] = 2
        sem[h - 10:] = 1
        sem[30:60, 20:40] = 7                       # a person
        tg = np.zeros((h, w), np.uint8)
        tg[h // 4: h - 10, : w // 2] = 3
        tg[h // 4: h - 10, w // 2:] = 4
        st = np.full((h, w), 255, np.uint8)
        st[tg > 0] = 0
        st[h // 4: h - 10, : (w // 2) * i // n] = 1   # progress grows
        st[sem == 7] = 255
        vis = np.zeros((h, w), np.uint8)
        vis[tg > 0] = 1
        vis[(tg > 0) & (sem == 7)] = 2               # person occludes the wall
        img[st == 1] = (220, 220, 210)              # "board" is light
        for arr, sub in ((img, "images"), (sem, "semantic_masks"), (tg, "target_masks"), (vis, "visibility_masks"),
                         (st, "state_masks/wall_board_installation")):
            Image.fromarray(arr).save(root / sub / f"{sid}.{'jpg' if sub == 'images' else 'png'}")
        meta.append({"sample_id": sid, "files": {"image": f"images/{sid}.jpg", "semantic": f"semantic_masks/{sid}.png",
                                                 "target": f"target_masks/{sid}.png",
                                                 "visibility": f"visibility_masks/{sid}.png"},
                     "capture": {"time_local": f"2025-02-{10 + i:02d}T10:00:00"}})
    (root / "metadata.jsonl").write_text("\n".join(json.dumps(m) for m in meta), encoding="utf-8")
    (root / "splits" / "train.txt").write_text("\n".join(ids[:8]), encoding="utf-8")
    (root / "splits" / "val.txt").write_text("\n".join(ids[8:10]), encoding="utf-8")
    (root / "splits" / "test.txt").write_text("\n".join(ids[10:]), encoding="utf-8")
    (root / "targets.json").write_text(json.dumps({"targets": [{"index": 3, "target_id": "W01", "surface_type": "wall"},
                                                                   {"index": 4, "target_id": "W02", "surface_type": "wall"}]}),
                                       encoding="utf-8")


@unittest.skipUnless(HAVE, "torch/timm not installed")
class TestTrainQuick(unittest.TestCase):
    def test_state_target_classes(self):
        from vision_preprocessing.train.train_quick import state_target
        st = np.array([[0, 1, 255, 255, 255, 255]], np.uint8)
        vis = np.array([[1, 1, 2, 1, 0, 2]], np.uint8)
        sem = np.array([[0, 0, 7, 2, 0, 7]], np.uint8)
        tg = np.array([[3, 3, 3, 1, 0, 1]], np.uint8)        # 3 = wall ROI, 1 = ceiling ROI, 0 = none
        # wall-ROI occluder -> occluded; ceiling ROI -> not_target; outside-ROI wall and ceiling-ROI person -> unknown
        self.assertEqual(state_target(st, vis, sem, tg, [3, 4]).tolist(), [[0, 1, 2, 3, 255, 255]])

    def test_all_decoders_run_and_write_outputs(self):
        from vision_preprocessing.train.train_quick import main
        tmp = Path(tempfile.mkdtemp(prefix="vp_train_"))
        try:
            fake_export(tmp / "exp")
            for dec in ("unet", "upernet", "linear"):
                out = tmp / f"out_{dec}"
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = main(["--export", str(tmp / "exp"), "--decoder", dec, "--encoder", "convnext_atto",
                               "--no-pretrained", "--epochs", "2", "--batch", "2", "--out", str(out)])
                self.assertEqual(rc, 0)
                m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
                self.assertIn("state_miou_install", m["test"])
                for k in ("ratio_mae_label_region", "ratio_mae_roi_model", "ratio_mae_image_model"):
                    self.assertIn(k, m["test"])
                self.assertIn("occluded", m["test"]["state_iou"])
                self.assertTrue(m["test"]["ratio_rows"])                       # per date x target rows
                self.assertTrue((out / "model_quick.pt").exists())
                self.assertTrue(list((out / "overlays" / "test").glob("*.jpg")))
                ck = torch.load(out / "model_quick.pt", weights_only=False)
                self.assertFalse(any(k.startswith("encoder.") for k in ck["state_dict"]))   # encoder not saved
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
