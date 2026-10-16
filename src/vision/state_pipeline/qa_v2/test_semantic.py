"""Behavior tests for reviewed-region candidates and reversible drafts."""
from copy import deepcopy
import unittest

from semantic import analyze


def polygon(label, points=None, **metadata):
    shape = {"label": label, "points": points or [[0, 0], [100, 0], [100, 100], [0, 100]],
             "shape_type": "polygon", "group_id": None, "flags": {}}
    shape.update(metadata)
    return shape


def region(**values):
    spec = {"id": "wall_1", "label": "wall",
            "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
            "min_coverage": .6, "conflict_labels": ["ceiling"], "max_conflict": .25,
            "exclusions": []}
    spec.update(values)
    return spec


class SemanticTest(unittest.TestCase):
    def test_empty_annotations_report_expected_wall_absence(self):
        result = analyze({"shapes": []}, [region()], (100, 100))
        self.assertIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertEqual(result["metrics"][0]["coverage_upper"], 0)

    def test_wall_region_covered_by_ceiling_reports_both_evidence_types(self):
        result = analyze({"shapes": [polygon("ceiling")]}, [region()], (100, 100))
        self.assertIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertIn("ROI_0_CONFLICTING_LABEL_ceiling", result["issues"])
        self.assertEqual(result["metrics"][0]["conflict_coverage"]["ceiling"], 1)
        self.assertIsNone(result["corrected_doc"])

    def test_visibility_exclusion_does_not_count_occluded_half_as_missing(self):
        left = [[0, 0], [50, 0], [50, 100], [0, 100]]
        right = [[50, 0], [100, 0], [100, 100], [50, 100]]
        result = analyze({"shapes": [polygon("wall", left), polygon("ceiling", right)]},
                         [region(exclusions=[right])], (100, 100))
        self.assertEqual(result["metrics"][0]["coverage_lower"], 1)
        self.assertEqual(result["metrics"][0]["conflict_coverage"]["ceiling"], 0)
        self.assertFalse(result["issues"])

    def test_unrelated_invalid_geometry_does_not_block_missing_wall(self):
        result = analyze({"shapes": [polygon("floor", [["bad", 0]])]},
                         [region()], (100, 100))
        self.assertIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertEqual(result["semantic_status"], "reviewed_roi_rules_only")

    def test_invalid_wall_with_unknown_coordinates_abstains_missing_judgment(self):
        result = analyze({"shapes": [polygon("wall", [["bad", 0]])]},
                         [region()], (100, 100))
        self.assertFalse(any("COVERAGE" in issue or "ABSENT" in issue
                             for issue in result["issues"]))
        self.assertEqual(result["semantic_status"], "abstain")
        self.assertEqual(result["metrics"][0]["coverage_upper"], 1)

    def test_invalid_wall_hull_does_not_silently_become_corrected_mask(self):
        crossing = [[0, 0], [100, 100], [100, 0], [0, 100]]
        result = analyze({"shapes": [polygon("wall", crossing)]}, [region()], (100, 100))
        self.assertEqual(result["semantic_status"], "abstain")
        self.assertEqual(result["metrics"][0]["coverage_lower"], 0)
        self.assertEqual(result["metrics"][0]["coverage_upper"], 1)
        self.assertIsNone(result["corrected_doc"])

    def test_small_invalid_wall_hull_supports_conservative_missing_candidate(self):
        crossing = [[0, 0], [20, 20], [20, 0], [0, 20]]
        result = analyze({"shapes": [polygon("wall", crossing)]}, [region()], (100, 100))
        self.assertIn("ROI_0_LOW_LABEL_COVERAGE", result["issues"])
        self.assertAlmostEqual(result["metrics"][0]["coverage_upper"], .04)
        self.assertIsNone(result["corrected_doc"])

    def test_exact_duplicates_form_draft_without_source_mutation(self):
        first = polygon("wall")
        reordered = deepcopy(first)
        reordered["points"] = list(reversed(first["points"]))
        doc = {"imagePath": "photo.jpg", "shapes": [first, reordered, deepcopy(first)]}
        before = deepcopy(doc)
        result = analyze(doc, [region()], (100, 100))
        self.assertEqual(doc, before)
        self.assertEqual(len(result["corrected_doc"]["shapes"]), 1)
        self.assertEqual(result["corrected_doc"]["imagePath"], "photo.jpg")
        self.assertEqual(result["correction_actions"][0]["remove_indices"], [1, 2])
        self.assertTrue(result["correction_actions"][0]["needs_review"])

    def test_metadata_differences_preserve_both_shapes(self):
        for metadata in ({"group_id": 7}, {"flags": {"occluded": True}}, {"score": .5}):
            with self.subTest(metadata=metadata):
                doc = {"shapes": [polygon("wall"), polygon("wall", **metadata)]}
                result = analyze(doc, [region()], (100, 100))
                self.assertIsNone(result["corrected_doc"])
                self.assertEqual(result["duplicate_groups"][0]["kind"], "near_duplicate")
                self.assertFalse(result["duplicate_groups"][0]["metadata_equal"])

    def test_near_duplicate_ranking_never_automatically_selects_or_deletes(self):
        near = [[0, 0], [99, 0], [99, 100], [0, 100]]
        result = analyze({"shapes": [polygon("wall"), polygon("wall", near)]},
                         [region()], (100, 100))
        self.assertIsNone(result["corrected_doc"])
        group = result["duplicate_groups"][0]
        self.assertEqual(group["selection"], "human_review_required")
        self.assertEqual(group["ranked_proposals"][0]["shape_index"], 0)
        self.assertAlmostEqual(group["iou"], .99)

    def test_provisional_reference_results_are_visibly_provisional(self):
        result = analyze({"shapes": []}, [region()], (100, 100),
                         basis="provisional_reference_projection")
        self.assertEqual(result["semantic_status"], "provisional_reference_rules_only")
        self.assertIn("REFERENCE_PROJECTION_REQUIRES_VISUAL_REVIEW", result["warnings"])
        self.assertEqual(result["metrics"][0]["basis"], "provisional_reference_projection")

    def test_one_invalid_region_does_not_block_other_valid_region(self):
        result = analyze({"shapes": []}, [region(), region(points=[[0, 0]])], (100, 100))
        self.assertIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertEqual(result["semantic_status"], "partial_abstain")

    def test_unknown_label_does_not_prove_wall_absent(self):
        result = analyze({"shapes": [polygon("")]}, [region()], (100, 100))
        self.assertNotIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertEqual(result["semantic_status"], "abstain")

    def test_malformed_type_is_contained_in_one_shape(self):
        bad = polygon("floor", shape_type=["polygon"])
        result = analyze({"shapes": [bad]}, [region()], (100, 100))
        self.assertIn("ROI_0_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertIn("SEMANTIC_SHAPE_0_UNSUPPORTED_GEOMETRY", result["warnings"])

    def test_rectangle_with_undefined_point_count_does_not_bound_missing_wall(self):
        bad = polygon("wall", [[0, 0], [5, 5], [5, 0]], shape_type="rectangle")
        result = analyze({"shapes": [bad]}, [region()], (100, 100))
        self.assertEqual(result["semantic_status"], "abstain")
        self.assertEqual(result["metrics"][0]["coverage_upper"], 1)

    def test_selftouch_polygon_can_render_present_without_false_missing(self):
        selftouch = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0], [50, 50], [0, 0]]
        result = analyze({"shapes": [polygon("wall", selftouch)]}, [region()],
                         (100, 100), raster_diagnostics=True)
        self.assertEqual(result["semantic_status"], "rendered_reference_rules_only")
        self.assertEqual(result["metrics"][0]["geometric_status"], "abstain")
        self.assertEqual(result["metrics"][0]["rendered_coverage"], 1)
        self.assertNotIn("ROI_0_RENDERED_LOW_LABEL_COVERAGE", result["issues"])
        self.assertIn("INVALID_POLYGON_RASTER_INTERPRETATION_REQUIRES_REVIEW", result["warnings"])
        self.assertIsNone(result["corrected_doc"])

    def test_disconnected_bridge_renderer_exposes_missing_area_despite_full_hull(self):
        bridge = [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0],
                  [80, 80], [100, 80], [100, 100], [80, 100], [80, 80], [0, 0]]
        center = [[40, 40], [60, 40], [60, 60], [40, 60]]
        result = analyze({"shapes": [polygon("wall", bridge)]}, [region(points=center)],
                         (100, 100), raster_diagnostics=True)
        self.assertEqual(result["metrics"][0]["coverage_upper"], 1)
        self.assertEqual(result["metrics"][0]["geometric_status"], "abstain")
        self.assertLess(result["metrics"][0]["rendered_coverage"], .1)
        self.assertIn("ROI_0_RENDERED_LOW_LABEL_COVERAGE", result["issues"])
        self.assertIsNone(result["corrected_doc"])

    def test_literal_invalid_duplicates_removed_without_repair_or_metadata_loss(self):
        crossing = [[0, 0], [100, 100], [100, 0], [0, 100]]
        first = polygon("wall", crossing)
        variant = polygon("wall", crossing, score=.7)
        doc = {"shapes": [first, deepcopy(first), variant]}
        before = deepcopy(doc)
        result = analyze(doc, [region()], (100, 100), raster_diagnostics=True)
        self.assertEqual(doc, before)
        self.assertEqual(result["corrected_doc"]["shapes"], [first, variant])
        self.assertEqual(result["duplicate_groups"][0]["kind"], "exact_literal_duplicate")
        self.assertEqual(result["correction_actions"][0]["remove_indices"], [1])

    def test_raster_unusable_expected_points_abstain_instead_of_missing(self):
        for bad in (polygon("wall", [["bad", 0]]),
                    polygon("wall", [[-1, 0], [100, 0], [100, 100], [0, 100]]),
                    polygon("wall", shape_type="circle"), polygon("")):
            with self.subTest(shape=bad):
                result = analyze({"shapes": [bad]}, [region()], (100, 100),
                                 raster_diagnostics=True)
                self.assertEqual(result["metrics"][0]["rendered_status"], "abstain")
                self.assertFalse(any("RENDERED_LOW_LABEL_COVERAGE" in issue
                                     or "RENDERED_EXPECTED_LABEL_ABSENT" in issue
                                     for issue in result["issues"]))

    def test_raster_reports_ceiling_conflict_and_absent_wall(self):
        result = analyze({"shapes": [polygon("ceiling")]}, [region()], (100, 100),
                         basis="provisional_reference_projection", raster_diagnostics=True)
        self.assertEqual(result["semantic_status"], "provisional_rendered_reference_rules_only")
        self.assertIn("ROI_0_RENDERED_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertIn("ROI_0_RENDERED_CONFLICTING_LABEL_ceiling", result["issues"])
        self.assertEqual(result["metrics"][0]["rendered_conflict_coverage"]["ceiling"], 1)

    def test_raster_visibility_exclusions_remove_occlusion_and_conflict(self):
        left = [[0, 0], [50, 0], [50, 100], [0, 100]]
        right = [[50, 0], [100, 0], [100, 100], [50, 100]]
        result = analyze({"shapes": [polygon("wall", left), polygon("ceiling", right)]},
                         [region(exclusions=[right])], (100, 100), raster_diagnostics=True)
        self.assertEqual(result["metrics"][0]["rendered_coverage"], 1)
        self.assertEqual(result["metrics"][0]["rendered_conflict_coverage"]["ceiling"], 0)

    def test_raster_empty_shapes_remain_explicit_missing_candidate(self):
        result = analyze({"shapes": []}, [region()], (100, 100), raster_diagnostics=True)
        self.assertIn("ROI_0_RENDERED_EXPECTED_LABEL_ABSENT", result["issues"])
        self.assertEqual(result["metrics"][0]["rendered_coverage"], 0)

    def test_wall_and_ceiling_same_geometry_remain_until_human_review(self):
        doc = {"shapes": [polygon("ceiling"), polygon("wall")]}
        before = deepcopy(doc)
        result = analyze(doc, [region()], (100, 100))
        self.assertEqual(doc, before)
        self.assertIsNone(result["corrected_doc"])
        self.assertFalse(result["correction_actions"])
        group = next(g for g in result["duplicate_groups"] if g["kind"] == "cross_label_overlap")
        self.assertEqual(group["shape_indices"], [0, 1])
        self.assertEqual(group["selection"], "human_review_required")
        self.assertEqual(group["ranked_proposals"][0]["shape_index"], 1)
        self.assertTrue(group["ranked_proposals"][0]["ranking_is_not_label_correctness"])
        self.assertIn("CROSS_LABEL_OVERLAP_SHAPES_0_1", result["issues"])

    def test_invalid_same_raw_coordinates_different_labels_are_review_candidates(self):
        crossing = [[0, 0], [100, 100], [100, 0], [0, 100]]
        result = analyze({"shapes": [polygon("ceiling", crossing), polygon("wall", crossing)]},
                         [region()], (100, 100), basis="provisional_reference_projection",
                         raster_diagnostics=True)
        self.assertIsNone(result["corrected_doc"])
        self.assertFalse(result["correction_actions"])
        group = next(g for g in result["duplicate_groups"] if g["kind"] == "cross_label_overlap")
        self.assertIsNone(group["iou"])
        self.assertEqual(group["basis"], "provisional_reference_projection")
        self.assertTrue(group["ranked_proposals"][0]["support_is_upper_bound_only"])
        self.assertEqual(group["ranked_proposals"][0]["teacher_evidence"][0]["measurement"],
                         "submitted_coordinate_hull_upper_bound")

    def test_cross_label_overlap_requires_near_identical_geometry(self):
        half = [[0, 0], [50, 0], [50, 100], [0, 100]]
        result = analyze({"shapes": [polygon("wall"), polygon("ceiling", half)]},
                         [region()], (100, 100))
        self.assertFalse(any(g["kind"] == "cross_label_overlap"
                             for g in result["duplicate_groups"]))

    def test_invalid_cross_label_candidates_require_finite_inframe_raw_coordinates(self):
        for bad in ([["bad", 0], [100, 100], [100, 0], [0, 100]],
                    [[-1, 0], [100, 100], [100, 0], [0, 100]]):
            with self.subTest(points=bad):
                result = analyze({"shapes": [polygon("wall", bad), polygon("ceiling", bad)]},
                                 [region()], (100, 100), raster_diagnostics=True)
                self.assertFalse(any(g["kind"] == "cross_label_overlap"
                                     for g in result["duplicate_groups"]))


if __name__ == "__main__":
    unittest.main()
