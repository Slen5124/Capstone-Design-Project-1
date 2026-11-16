import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import measure_targets, render_overlay
from layout import STATE_CLASSES, LEGACY_CLASSES
from run import read_json
from training_data import read_training_manifest, load_sample, audit
from synthetic_export import create_export

CONFIG = read_json(ROOT / "config.json")
TINY = {**CONFIG, "export_size": [64, 48], "min_roi_px": 10}


class ThreeSurfaceData(unittest.TestCase):
    def test_eight_states_and_floor_measurement(self):
        targets = [{"index": i+1, "target_key": f"T{i}", "surface_type": surface,
                    "activity": activity, "roi_confirmed": True}
                   for i, (surface, activity) in enumerate((('wall', 'wall_board_installation'),
                        ('ceiling', 'ceiling_panel_installation'), ('floor', 'floor_finish_installation')))]
        state = np.array([[0, 1, 2, 3, 4, 5, 6, 7]], np.uint8)
        roi = np.array([[1, 1, 2, 2, 3, 3, 3, 3]], np.uint8)
        rows = measure_targets(state, np.ones(state.shape), roi, targets, {**CONFIG, "min_roi_px": 1}, True)
        self.assertEqual([r["installed_ratio"] for r in rows], [.5, .5, .5])
        self.assertEqual(rows[2]["occluded_px"], 1)
        self.assertEqual(rows[2]["observed_fraction"], .5)
        overlay = render_overlay(np.full((1, 8, 3), 100, np.uint8), state, roi, targets[2], CONFIG)
        self.assertEqual(overlay.shape, (1, 8, 3))

    def test_valid_export_and_floor_pixel_targets(self):
        with tempfile.TemporaryDirectory() as td:
            items = read_training_manifest(create_export(td, (64, 48)))
            report = audit(items, TINY)
            self.assertTrue(report["passed"], report)
            self.assertGreater(report["state_pixels"]["train"]["floor_installed"], 0)
            self.assertEqual(set(np.unique(load_sample(items[0], (64, 48))[2])), {0, 1, 2, 3, 4, 5, 7})

    def test_unlabelled_floor_blocks_training(self):
        with tempfile.TemporaryDirectory() as td:
            items = read_training_manifest(create_export(td, (64, 48)))
            for item in items:
                Image.fromarray(np.full((48, 64), 255, np.uint8)).save(item["states"]["floor"])
            report = audit(items, TINY)
            self.assertFalse(report["passed"])
            self.assertIn("no_training_pixels:floor_installed", report["problems"])

    def test_no_automatic_override_of_not_applicable(self):
        with tempfile.TemporaryDirectory() as td:
            items = read_training_manifest(create_export(td, (64, 48)))
            for item in items:
                item["targets"][2]["activities"]["floor_finish_installation"]["applicability"] = "not_applicable"
            self.assertFalse(audit(items, TINY)["passed"])

    def test_group_leakage_blocks_training(self):
        with tempfile.TemporaryDirectory() as td:
            items = read_training_manifest(create_export(td, (64, 48)))
            items[2]["date"] = items[0]["date"]
            self.assertIn("cross_split_site_view_day", audit(items, TINY)["problems"])

    def test_duplicate_image_blocks_training(self):
        with tempfile.TemporaryDirectory() as td:
            items = read_training_manifest(create_export(td, (64, 48)))
            items[2]["files"]["image"] = items[0]["files"]["image"]
            self.assertIn("cross_split_identical_image", audit(items, TINY)["problems"])

    def test_weak_validation_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            manifest = create_export(td, (64, 48))
            p = manifest.parent / "synthetic_export/metadata.jsonl"
            records = [json.loads(line) for line in p.read_text().splitlines()]
            records[2]["label_source"] = "weak"
            p.write_text('\n'.join(json.dumps(r) for r in records))
            with self.assertRaises(ValueError):
                read_training_manifest(manifest)


@unittest.skipIf(importlib.util.find_spec("torch") is None, "Torch environment required for weight transfer tests")
class WeightTransfer(unittest.TestCase):
    def test_old_occlusion_is_moved_not_relabelled_as_floor(self):
        import torch
        from checkpoint import transfer_weights
        model = torch.nn.Module()
        model.encoder = torch.nn.Linear(2, 2)
        model.state_head = torch.nn.Conv2d(2, 8, 1)
        initial_floor = model.state_head.weight[4:6].detach().clone()
        source = {"encoder": CONFIG["encoder"], "decoder": "upernet", "n_sem": 8, "n_state": 6,
                  "vit_pad_crop": True, "state_classes": LEGACY_CLASSES,
                  "state_dict": {"state_head.weight": torch.arange(6.).view(6, 1, 1, 1).expand(6, 2, 1, 1).clone(),
                                 "state_head.bias": torch.arange(6.)}}
        report = transfer_weights(model, source, CONFIG)
        self.assertEqual(model.state_head.bias[6].item(), 4.)
        self.assertEqual(model.state_head.bias[7].item(), 5.)
        self.assertTrue(torch.equal(model.state_head.weight[4:6], initial_floor))
        self.assertEqual(report["new_random_state_classes"], STATE_CLASSES[4:6])

    def test_floor_classes_receive_gradients(self):
        import torch
        from vision_preprocessing.train.train_quick import weighted_ce
        logits = torch.zeros((1, 8, 2, 2), requires_grad=True)
        targets = torch.tensor([[[4, 5], [4, 5]]])
        weighted_ce(logits, targets, torch.ones(1)).backward()
        self.assertGreater(logits.grad[:, 4:6].abs().sum().item(), 0.)


if __name__ == "__main__":
    unittest.main()
