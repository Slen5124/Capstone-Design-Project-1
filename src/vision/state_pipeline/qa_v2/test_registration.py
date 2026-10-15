"""Synthetic geometry checks; these do not validate accuracy on construction photos."""

import unittest

import numpy as np
from PIL import Image, ImageDraw

from registration import _geometry_gate, project_regions
from shapely.geometry import Polygon


def textured_image(seed=17, size=(600, 400)):
    rng = np.random.default_rng(seed)
    image = Image.fromarray(rng.integers(30, 230, (size[1], size[0], 3), dtype=np.uint8))
    draw = ImageDraw.Draw(image)
    for _ in range(220):
        x, y = rng.integers([12, 12], [size[0] - 12, size[1] - 12])
        radius = int(rng.integers(2, 10))
        color = tuple(int(x) for x in rng.integers(0, 256, 3))
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
    return image


def region(region_id="wall", points=None):
    return {"id": region_id, "label": "wall", "points": points or [[40, 40], [260, 40], [260, 360], [40, 360]],
            "min_coverage": 0.8, "conflict_labels": ["ceiling"], "max_conflict": 0.2, "exclusions": []}


class RegistrationTests(unittest.TestCase):
    def test_identity_preserves_reviewed_region_without_feature_requirement(self):
        image = Image.new("RGB", (600, 400))
        reference = region()
        result = project_regions(image, image, [reference], source_identity=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["basis"], "reviewed_image")
        self.assertEqual(result["regions"][0]["points"], reference["points"])
        self.assertNotIn("basis", reference)

    def test_identity_size_mismatch_abstains(self):
        result = project_regions(Image.new("RGB", (600, 400)), Image.new("RGB", (601, 400)), [region()], True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["regions"], [])
        self.assertIn("identity_image_dimensions_differ", result["evidence"][0]["reasons"])

    def test_blank_reference_abstains(self):
        result = project_regions(Image.new("RGB", (600, 400), "white"), textured_image(), [region()])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["regions"], [])
        self.assertIn("insufficient_texture_features", result["evidence"][0]["reasons"])

    def test_planar_translation_projects_region_and_exclusion(self):
        import cv2
        image = textured_image()
        transform = np.float64([[1, 0, 14], [0, 1, -8], [0, 0, 1]])
        target = Image.fromarray(cv2.warpPerspective(np.asarray(image), transform, image.size))
        reference = region()
        reference["exclusions"] = [[[90, 90], [120, 90], [120, 130], [90, 130]]]
        result = project_regions(image, target, [reference])
        self.assertTrue(result["accepted"], result["evidence"])
        expected = np.asarray(reference["points"]) + [14, -8]
        self.assertLess(np.max(np.abs(np.asarray(result["regions"][0]["points"]) - expected)), 1.0)
        self.assertTrue(result["regions"][0]["visibility_not_verified"])
        self.assertEqual(result["basis"], "provisional_reference_projection")
        expected_exclusion = np.asarray(reference["exclusions"][0]) + [14, -8]
        self.assertLess(np.max(np.abs(np.asarray(result["regions"][0]["exclusions"][0]) - expected_exclusion)), 1.0)

    def test_two_planes_use_distinct_transforms(self):
        import cv2
        image = textured_image()
        target_array = np.zeros_like(np.asarray(image))
        left = np.asarray(image).copy()
        left[:, 300:] = 0
        right = np.asarray(image).copy()
        right[:, :300] = 0
        first_transform = np.float64([[1, 0, 12], [0, 1, 5], [0, 0, 1]])
        second_transform = np.float64([[1, 0, -9], [0, 1, -6], [0, 0, 1]])
        target_array = np.maximum(cv2.warpPerspective(left, first_transform, image.size),
                                  cv2.warpPerspective(right, second_transform, image.size))
        second = region("ceiling", [[340, 40], [560, 40], [560, 360], [340, 360]])
        second["label"] = "ceiling"
        references = [region(), second]
        result = project_regions(image, Image.fromarray(target_array), references)
        self.assertTrue(result["accepted"], result["evidence"])
        for projected, reference, offset in zip(result["regions"], references, ([12, 5], [-9, -6])):
            self.assertLess(np.max(np.abs(np.asarray(projected["points"]) - (np.asarray(reference["points"]) + offset))), 1.0)
        matrices = [item["metrics"]["homography_resized"] for item in result["evidence"]]
        self.assertGreater(abs(matrices[0][0][2] - matrices[1][0][2]), 15)

    def test_reflection_geometry_rejected(self):
        source = Polygon(region()["points"])
        _, _, reasons = _geometry_gate(np.float64([[-1, 0, 600], [0, 1, 0], [0, 0, 1]]), source, (600, 400), {})
        self.assertIn("reflection_or_reversed_region", reasons)

    def test_resized_registration_returns_original_pixel_coordinates(self):
        import cv2
        image = textured_image(size=(1601, 1001))
        reference = region(points=[[100, 100], [1500, 100], [1500, 900], [100, 900]])
        transform = np.float64([[1, 0, 24], [0, 1, -16], [0, 0, 1]])
        target = Image.fromarray(cv2.warpPerspective(np.asarray(image), transform, image.size))
        result = project_regions(image, target, [reference])
        self.assertTrue(result["accepted"], result["evidence"])
        self.assertLess(np.max(np.abs(np.asarray(result["regions"][0]["points"]) - (np.asarray(reference["points"]) + [24, -16]))), 2.0)

    def test_local_texture_patch_does_not_justify_whole_region_projection(self):
        import cv2
        image = Image.new("RGB", (600, 400), "white")
        image.paste(textured_image(size=(70, 70)), (80, 100))
        target = Image.fromarray(cv2.warpPerspective(np.asarray(image), np.float64([[1, 0, 8], [0, 1, 0], [0, 0, 1]]), image.size))
        result = project_regions(image, target, [region()])
        self.assertFalse(result["accepted"])
        self.assertIn("inliers_not_spread_across_region", result["evidence"][0]["reasons"])

    def test_same_plane_anchor_supports_small_untextured_semantic_patch(self):
        import cv2
        image = textured_image()
        image.paste("white", (80, 120, 200, 260))
        semantic = region(points=[[90, 130], [190, 130], [190, 250], [90, 250]])
        target = Image.fromarray(cv2.warpPerspective(np.asarray(image), np.float64([[1, 0, 14], [0, 1, -8], [0, 0, 1]]), image.size))
        without_anchor = project_regions(image, target, [semantic])
        self.assertFalse(without_anchor["accepted"])
        semantic["registration_points"] = region()["points"]
        with_anchor = project_regions(image, target, [semantic])
        self.assertTrue(with_anchor["accepted"], with_anchor["evidence"])
        expected = np.asarray(semantic["points"]) + [14, -8]
        self.assertLess(np.max(np.abs(np.asarray(with_anchor["regions"][0]["points"]) - expected)), 1.0)
        self.assertTrue(with_anchor["evidence"][0]["metrics"]["registration_anchor_used"])
        self.assertLess(Polygon(with_anchor["regions"][0]["points"]).area, Polygon(with_anchor["regions"][0]["registration_points"]).area)

    def test_invalid_or_noncontaining_anchor_abstains(self):
        image = textured_image()
        for anchor, reason in (
            ([[40, 40], [260, 360], [260, 40], [40, 360]], "invalid_or_degenerate_region_polygon"),
            ([[0, 0], [30, 0], [30, 30], [0, 30]], "registration_anchor_does_not_cover_semantic_region"),
            ([[-20, 0], [600, 0], [600, 400], [-20, 400]], "registration_anchor_outside_reference_image"),
        ):
            with self.subTest(reason=reason):
                reference = region()
                reference["registration_points"] = anchor
                result = project_regions(image, image, [reference])
                self.assertFalse(result["accepted"])
                self.assertIn(reason, result["evidence"][0]["reasons"])

    def test_invalid_reviewed_polygon_abstains(self):
        reference = region(points=[[40, 40], [260, 360], [260, 40], [40, 360]])
        result = project_regions(textured_image(), textured_image(), [reference], True)
        self.assertFalse(result["accepted"])
        self.assertIn("invalid_or_degenerate_region_polygon", result["evidence"][0]["reasons"])

    def test_partial_registration_does_not_hide_failed_region(self):
        import cv2
        image = textured_image()
        image.paste("white", (300, 0, 600, 400))
        target = Image.fromarray(cv2.warpPerspective(np.asarray(image), np.float64([[1, 0, 8], [0, 1, 0], [0, 0, 1]]), image.size))
        references = [region(), region("blank", [[340, 40], [560, 40], [560, 360], [340, 360]])]
        result = project_regions(image, target, references)
        self.assertFalse(result["accepted"])
        self.assertEqual(len(result["regions"]), 1)
        self.assertEqual(result["evidence"][1]["status"], "abstain")


if __name__ == "__main__":
    unittest.main(verbosity=2)
