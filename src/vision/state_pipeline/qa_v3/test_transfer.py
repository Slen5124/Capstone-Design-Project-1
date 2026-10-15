"""Meaningful synthetic gate checks; no construction-site accuracy claim."""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "qa_v2"))
from test_registration import textured_image
from transfer import homography_original, transfer


def surface(role="complete_surface"):
    return {"id": "W1", "label": "wall", "role": role,
            "points": [[40, 40], [560, 40], [560, 360], [40, 360]],
            "exclusions": [], "min_coverage": 0.8, "conflict_labels": ["ceiling"], "max_conflict": 0.2}


def warped(image, brightness=0):
    matrix = np.float64([[1, 0, 12], [0, 1, -6], [0, 0, 1]])
    array = np.clip(np.asarray(image, dtype=np.float32) + brightness, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.warpPerspective(array, matrix, image.size))


class TransferTests(unittest.TestCase):
    def test_same_textured_plane_accepts_full_surface_draft(self):
        source = textured_image()
        result = transfer(source, warped(source), [surface()])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertTrue(result["regions"][0]["full_surface_transfer_ok"])
        self.assertFalse(result["training_eligible"])

    def test_remaining_matches_cannot_hide_large_occlusion(self):
        source = textured_image()
        target = warped(source)
        target.paste("black", (100, 100, 310, 340))
        result = transfer(source, target, [surface()])
        self.assertTrue(result["regions"], result["evidence"])
        self.assertGreaterEqual(result["evidence"][0]["metrics"]["inliers"], 24)
        self.assertFalse(result["regions"][0]["auto_transfer_ok"])
        self.assertIn("appearance_changed_or_occluded_area", result["regions"][0]["transfer_reasons"])
        self.assertTrue(result["automatic_allowed_subregions"])
        passed_bounds = {tuple(tile["bounds_resized_reference"]) for tile in result["evidence"][0]["appearance"]["tiles"] if tile["passed"]}
        for region in result["automatic_allowed_subregions"]:
            self.assertTrue(all(tuple(bounds) in passed_bounds for bounds in region["matched_tile_bounds_resized_reference"]))
            self.assertTrue(region["partial_surface_transfer"])
            self.assertFalse(region["training_eligible"])
            self.assertEqual(region["scope"], "partial_visible_surface")
        self.assertEqual(result["regions"][0]["role"], "withheld_diagnostic")
        from shapely.geometry import Polygon, box
        from shapely.ops import unary_union
        transferred = unary_union([Polygon(r["points"], r["exclusions"]) for r in result["automatic_allowed_subregions"]])
        self.assertLess(transferred.intersection(box(100, 100, 310, 340)).area, 1.0)

    def test_partial_regions_are_consumed_by_repair_without_painting_occluder(self):
        from repair import repair_annotation
        from shapely.geometry import Polygon, box
        from shapely.ops import unary_union
        source = textured_image()
        target = warped(source)
        target.paste("black", (100, 100, 310, 340))
        mapping = transfer(source, target, [surface()])
        doc = {"shapes": [], "imageWidth": 600, "imageHeight": 400}
        repaired = repair_annotation(doc, target.size, mapping["regions"], mapping["evidence"])
        self.assertTrue(repaired["corrected_doc"], repaired)
        self.assertTrue(any(action["action"] == "restore_missing_complete_surface" for action in repaired["actions"]))
        masks = unary_union([Polygon(shape["points"]) for shape in repaired["corrected_doc"]["shapes"]])
        self.assertLess(masks.intersection(box(100, 100, 310, 340)).area, 1.0)

    def test_global_brightness_change_is_compensated(self):
        source = textured_image()
        result = transfer(source, warped(source, brightness=22), [surface()])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertAlmostEqual(result["evidence"][0]["appearance"]["photometric_offset"], -22, delta=4)

    def test_patch_cannot_authorize_full_wall(self):
        source = textured_image()
        patch = surface("diagnostic_patch")
        patch["points"] = [[100, 100], [220, 100], [220, 220], [100, 220]]
        result = transfer(source, warped(source), [patch])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertFalse(result["regions"][0]["full_surface_transfer_ok"])
        self.assertEqual(result["regions"][0]["role"], "diagnostic_patch")
        self.assertEqual(result["automatic_allowed_subregions"], [])

    def test_low_texture_geometry_abstains(self):
        source = Image.new("RGB", (600, 400), "white")
        result = transfer(source, warped(source), [surface()])
        self.assertFalse(result["auto_transfer_ok"])
        self.assertEqual(result["regions"], [])

    def test_identity_permits_only_explicit_region_role(self):
        source = Image.new("RGB", (600, 400), "white")
        full = transfer(source, source, [surface()], True)
        patch = transfer(source, source, [surface("diagnostic_patch")], True)
        self.assertTrue(full["regions"][0]["full_surface_transfer_ok"])
        self.assertFalse(patch["regions"][0]["full_surface_transfer_ok"])

    def test_original_pixel_homography_accounts_for_resize(self):
        metrics = {"homography_resized": [[1, 0, 6], [0, 1, -3], [0, 0, 1]],
                   "reference_resize_xy": [.5, .4], "target_resize_xy": [.5, .4]}
        original = homography_original(metrics)
        np.testing.assert_allclose(original, [[1, 0, 12], [0, 1, -7.5], [0, 0, 1]])

    def test_smooth_patch_with_textured_same_plane_anchor_accepts_unchanged_draft(self):
        source = textured_image()
        source.paste("white", (80, 120, 220, 280))
        patch = surface("diagnostic_patch")
        patch["points"] = [[90, 130], [210, 130], [210, 270], [90, 270]]
        patch["registration_points"] = surface()["points"]
        result = transfer(source, warped(source), [patch])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertFalse(result["regions"][0]["full_surface_transfer_ok"])
        self.assertLess(result["evidence"][0]["appearance"]["textured_area_fraction"], 0.1)

    def test_smooth_spatial_lighting_change_preserves_texture(self):
        source = textured_image()
        x = np.linspace(-16, 16, source.width, dtype=np.float32)
        illuminated = np.clip(np.asarray(source, dtype=np.float32) + x[None, :, None], 0, 255).astype(np.uint8)
        result = transfer(source, warped(Image.fromarray(illuminated)), [surface()])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertTrue(result["evidence"][0]["appearance"]["smooth_illumination"]["applied"])

    def test_small_flat_patch_uses_adaptive_distributed_grid(self):
        source = textured_image()
        source.paste("white", (70, 100, 160, 190))
        patch = surface("diagnostic_patch")
        patch["points"] = [[95, 125], [117, 125], [117, 147], [95, 147]]
        patch["registration_points"] = surface()["points"]
        result = transfer(source, warped(source), [patch])
        self.assertTrue(result["auto_transfer_ok"], result["evidence"])
        self.assertGreaterEqual(len(result["evidence"][0]["appearance"]["tiles"]), 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
