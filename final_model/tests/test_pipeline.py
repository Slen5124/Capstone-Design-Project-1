import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import measure_targets, validate_config, validate_targets
from backend_contract import observation_draft
import run

CONFIG = run.read_json(ROOT / "config.wall_ceiling_v1.json")
SMALL = {**CONFIG, "min_roi_px": 1}
WALL = {"index": 1, "target_key": "W01", "surface_type": "wall",
        "activity": "wall_board_installation", "roi_confirmed": True}
CEILING = {"index": 2, "target_key": "C01", "surface_type": "ceiling",
           "activity": "ceiling_panel_installation", "roi_confirmed": True}
IDS = {"schedule_id": "11111111-1111-4111-8111-111111111111",
       "task_id": "22222222-2222-4222-8222-222222222222"}
PHOTO = {"daily_record_id": "33333333-3333-4333-8333-333333333333", "revision": 1}


def measurement(labels, target=None, conf=.9, **kwargs):
    state = np.array([labels], np.uint8)
    return measure_targets(state, np.full(state.shape, conf), np.ones(state.shape, np.uint8),
                           [target or WALL], SMALL, kwargs.get("view_confirmed", True))[0]


class Measurements(unittest.TestCase):
    def test_wall_and_ceiling_in_one_frame(self):
        state = np.array([[0, 1, 1, 2, 3, 4]], np.uint8)
        roi = np.array([[1, 1, 1, 2, 2, 2]], np.uint8)
        rows = measure_targets(state, np.full(state.shape, .9), roi, [WALL, CEILING], SMALL, True)
        self.assertAlmostEqual(rows[0]["installed_ratio"], 2 / 3)
        self.assertEqual(rows[1]["installed_ratio"], .5)
        self.assertAlmostEqual(rows[1]["observed_fraction"], 2 / 3)

    def test_other_surface_and_occlusion_not_in_denominator(self):
        row = measurement([0, 1, 2, 3, 4, 5])
        self.assertEqual(row["installed_ratio"], .5)
        self.assertAlmostEqual(row["observed_fraction"], 1 / 3)

    def test_all_occluded_is_null_not_zero(self):
        row = measurement([4, 4])
        self.assertEqual(row["status"], "held")
        self.assertIsNone(row["installed_ratio"])

    def test_zero_installation_is_valid_observation(self):
        row = measurement([0, 0])
        self.assertEqual(row["status"], "observed")
        self.assertEqual(row["installed_ratio"], 0)

    def test_low_confidence_holds_ratio(self):
        self.assertIsNone(measurement([1, 1], conf=.4)["installed_ratio"])

    def test_nan_prediction_holds_ratio(self):
        row = measurement([1, 1], conf=float("nan"))
        self.assertIsNone(row["installed_ratio"])
        self.assertIn("nonfinite", row["hold_reason"])
        json.dumps(row, allow_nan=False)

    def test_unconfirmed_roi_and_view_hold(self):
        self.assertIsNone(measurement([1], target={**WALL, "roi_confirmed": False})["installed_ratio"])
        self.assertIsNone(measurement([1], view_confirmed=False)["installed_ratio"])

    def test_small_roi_holds(self):
        row = measure_targets(np.ones((2, 2), np.uint8), np.ones((2, 2)),
                              np.ones((2, 2), np.uint8), [WALL], CONFIG, True)[0]
        self.assertIn("roi_too_small", row["hold_reason"])

    def test_duplicate_and_unknown_roi_indices_rejected(self):
        with self.assertRaises(ValueError):
            validate_targets(np.ones((2, 2), np.uint8), [WALL, WALL])
        with self.assertRaises(ValueError):
            validate_targets(np.full((2, 2), 3, np.uint8), [WALL])

    def test_unsupported_state_and_config_rejected(self):
        with self.assertRaises(ValueError):
            measurement([7])
        with self.assertRaises(ValueError):
            validate_config({**CONFIG, "encoder": "another_model"})
        with self.assertRaises(ValueError):
            validate_targets(np.ones((1, 1), np.uint8), [{**WALL, "activity": "paint"}])

    def test_floor_is_explicitly_unsupported_not_measured_as_wall(self):
        floor = {**WALL, "surface_type": "floor", "activity": "floor_finish_installation"}
        with self.assertRaisesRegex(ValueError, "not supported"):
            measurement([1, 1], target=floor)
        with self.assertRaises(ValueError):
            validate_config({**CONFIG, "n_state": 8})  # changing a number cannot add learned floor states

    def test_unused_options_and_boolean_thresholds_rejected(self):
        for change in ({"vcc": True}, {"tta": True}, {"min_mean_confidence": True},
                       {"min_observed_fraction": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**CONFIG, **change})

    def test_uint16_targets_and_trimmed_keys(self):
        target = {**WALL, "index": 300}
        rows = measure_targets(np.ones((1, 2), np.uint8), np.ones((1, 2)),
                               np.full((1, 2), 300, np.uint16), [target], SMALL, True)
        self.assertEqual(rows[0]["installed_ratio"], 1)
        for change in ({"index": 65536}, {"target_key": " W01 "}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_targets(np.ones((1, 2), np.uint8), [{**WALL, **change}])


class BackendContract(unittest.TestCase):
    def test_ids_are_required(self):
        draft = observation_draft("SITE_TEST", "V01", {}, WALL, measurement([1]))
        self.assertFalse(draft["ready"])
        self.assertEqual(draft["reason"], "missing_backend_ids")

    @unittest.skipIf(sys.version_info < (3, 10), "Backend schema requires Python >=3.10; run full suite in backend environment")
    def test_draft_matches_current_backend_model(self):
        sys.path.insert(0, str(ROOT.parent / "src/backend"))
        from project_models import Observation
        draft = observation_draft("SITE_TEST", "V01", PHOTO, {**WALL, **IDS}, measurement([0, 1]))
        parsed = Observation.model_validate(draft["body"])
        self.assertEqual(parsed.installed_ratio, .5)
        self.assertIsNone(parsed.overlay_file_id)
        self.assertFalse(draft["ready"])

    def test_revision_and_idempotency(self):
        args = ("SITE_TEST", "V01", PHOTO, {**WALL, **IDS}, measurement([0, 1]))
        a, b = observation_draft(*args), observation_draft(*args)
        self.assertEqual(a["body"]["id"], b["body"]["id"])
        c = observation_draft(args[0], args[1], {**PHOTO, "revision": 2}, args[3], args[4])
        self.assertNotEqual(a["body"]["id"], c["body"]["id"])

    def test_held_contract_and_percent_rejection(self):
        row = measurement([4])
        draft = observation_draft("SITE_TEST", "V01", PHOTO, {**WALL, **IDS}, row)
        self.assertIsNone(draft["body"]["installed_ratio"])
        with self.assertRaises(ValueError):
            observation_draft("SITE_TEST", "V01", PHOTO, {**WALL, **IDS},
                              {**measurement([1]), "installed_ratio": 65})


class EndToEndLocal(unittest.TestCase):
    def setup_input(self, folder):
        Image.new("RGB", (32, 16), (128, 128, 128)).save(folder / "photo.png")
        Image.fromarray(np.ones((16, 32), np.uint8)).save(folder / "roi.png")
        doc = {"site_code": "SITE_TEST", "view_key": "V01", "view_confirmed": True,
               "source_wh": [32, 16], "roi_space": "original", "roi_mask": "roi.png",
               "targets": [{**WALL, **IDS}],
               "photos": [{"path": "photo.png", "captured_at": "2026-01-01T12:00:00+00:00", **PHOTO}]}
        run.write_json(folder / "input.json", doc)
        return doc

    def test_geometry_and_evidence_and_draft(self):
        class FakePredictor:
            def __init__(self, *args):
                self.info = {"synthetic_test": True}

            def predict(self, image):
                from vision_preprocessing.rasterize import apply_image, make_transform
                transform = make_transform(image.size, CONFIG["export_size"], "letterbox")
                rgb = np.array(apply_image(image.convert("RGB"), transform))
                state = np.ones(rgb.shape[:2], np.uint8)
                return rgb, np.zeros_like(state), state, np.full(state.shape, .9), transform

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            self.setup_input(folder)
            argv = ["--config", str(ROOT / "config.wall_ceiling_v1.json"),
                    "--input", str(folder / "input.json"), "--checkpoint", "not-used.pt",
                    "--out", str(folder / "out")]
            with patch.object(run, "Predictor", FakePredictor):
                self.assertEqual(run.main(argv), 0)
            result = run.read_json(folder / "out/vision_result.json")
            row = result["observations"][0]
            self.assertEqual(row["installed_ratio"], 1)
            self.assertEqual(row["roi_px"], 1024 * 512)  # letterbox padding is excluded
            self.assertTrue((folder / "out" / row["overlay"]).is_file())
            with Image.open(folder / "out" / row["state_mask"]) as image:
                self.assertEqual(image.size, (1024, 576))
            self.assertFalse(run.read_json(folder / "out/api_drafts.json")["sent"])
            with self.assertRaises(ValueError):
                run.main(argv)  # never overwrite an existing result folder

    def test_wrong_dimensions_and_missing_timezone_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            doc = self.setup_input(folder)
            doc["photos"][0]["captured_at"] = "2026-01-01T12:00:00"
            run.write_json(folder / "input.json", doc)
            with self.assertRaises(ValueError):
                run.preflight(folder / "input.json", CONFIG)
            doc["photos"][0]["captured_at"] += "+00:00"
            doc["source_wh"] = [64, 32]
            run.write_json(folder / "input.json", doc)
            with self.assertRaises(ValueError):
                run.preflight(folder / "input.json", CONFIG)

    def test_utc_z_accepted_and_exif_rotation_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            doc = self.setup_input(folder)
            doc["photos"][0]["captured_at"] = "2026-01-01T12:00:00Z"
            run.write_json(folder / "input.json", doc)
            run.preflight(folder / "input.json", CONFIG)
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (32, 16)).save(folder / "rotated.jpg", exif=exif)
            doc["photos"][0]["path"] = "rotated.jpg"
            run.write_json(folder / "input.json", doc)
            with self.assertRaisesRegex(ValueError, "EXIF"):
                run.preflight(folder / "input.json", CONFIG)

    def test_failed_inference_does_not_publish_complete_result(self):
        class FailingPredictor:
            def __init__(self, *args):
                self.info = {"synthetic_test": True}

            def predict(self, image):
                raise RuntimeError("simulated inference failure")

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            self.setup_input(folder)
            with patch.object(run, "Predictor", FailingPredictor), self.assertRaises(RuntimeError):
                run.main(["--config", str(ROOT / "config.wall_ceiling_v1.json"),
                          "--input", str(folder / "input.json"), "--checkpoint", "unused.pt",
                          "--out", str(folder / "out")])
            self.assertFalse((folder / "out/vision_result.json").exists())

    def test_json_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.json"
            run.write_json(path, {"valid": True})
            with self.assertRaises(ValueError):
                run.write_json(path, {"invalid": float("nan")})
            self.assertEqual(run.read_json(path), {"valid": True})


if __name__ == "__main__":
    unittest.main()
