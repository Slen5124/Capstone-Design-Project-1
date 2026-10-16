from copy import deepcopy
import unittest
import numpy as np

from repair import DEFAULT_POLICY, _contour_parts, _known_surface_components, _render_parts, repair_annotation
from shapely.geometry import Polygon
from shapely.ops import unary_union


def shape(label="wall", points=None, **values):
    result = {"label": label, "points": points or [[0, 0], [99, 0], [99, 99], [0, 99]],
              "shape_type": "polygon", "flags": {}, "group_id": None}
    result.update(values)
    return result


def teacher(label="wall", points=None, **values):
    result = {"id": "wall_plane", "label": label,
              "points": points or [[0, 0], [99, 0], [99, 99], [0, 99]],
              "min_coverage": .6, "conflict_labels": ["ceiling"] if label == "wall" else ["wall"],
              "role": "complete_surface", "auto_transfer_ok": True,
              "full_surface_transfer_ok": True, "exclusions": []}
    result.update(values)
    return result


class RepairTests(unittest.TestCase):
    def call(self, doc, regions=None, size=(100, 100), policy=None):
        return repair_annotation(doc, size, regions or [], {"projection": "gated_by_caller"}, policy)

    def test_malformed_shape_is_retained_and_classified_unresolved(self):
        doc = {"shapes": [None, shape(points=[["bad", 0]])]}
        before = deepcopy(doc)
        result = self.call(doc)
        self.assertEqual(doc, before)
        self.assertFalse(result["changed"])
        self.assertEqual(result["classification"], "unresolved")
        self.assertIn("SHAPE_NOT_OBJECT", [issue["code"] for issue in result["unresolved"]])
        self.assertIn("UNUSABLE_SHAPE_COORDINATES_OR_TYPE", [issue["code"] for issue in result["unresolved"]])

    def test_selftouch_contour_repair_preserves_source_pixels_and_metadata(self):
        points = [[0, 0], [99, 0], [99, 99], [0, 99], [0, 0], [50, 50], [0, 0]]
        doc = {"imagePath": "photo.jpg", "shapes": [shape(points=points, group_id=42, flags={"custom": True})]}
        before = deepcopy(doc)
        result = self.call(doc)
        self.assertTrue(result["changed"])
        self.assertEqual(doc, before)
        self.assertEqual(result["classification"], "automatically_repaired")
        self.assertGreaterEqual(result["actions"][0]["metrics"]["pixel_iou"], .995)
        for item in result["corrected_doc"]["shapes"]:
            self.assertTrue(Polygon(item["points"]).is_valid)
            self.assertEqual(item["group_id"], 42)
            self.assertEqual(item["flags"], {"custom": True})

    def test_invalid_multipiece_polygon_is_repaired_without_pixel_loss(self):
        bowtie = [[0, 0], [99, 99], [99, 0], [0, 99]]
        result = self.call({"shapes": [shape(points=bowtie)]})
        self.assertTrue(result["changed"])
        self.assertGreaterEqual(result["actions"][0]["metrics"]["pixel_iou"], .995)
        self.assertTrue(all(Polygon(item["points"]).is_valid for item in result["corrected_doc"]["shapes"]))

    def test_significant_ambiguous_raster_hole_is_refused(self):
        ring = [[0, 0], [99, 0], [99, 99], [0, 99], [0, 0],
                [30, 30], [30, 70], [70, 70], [70, 30], [30, 30], [0, 0]]
        result = self.call({"shapes": [shape(points=ring)]})
        self.assertFalse(result["changed"])
        self.assertIn("SIGNIFICANT_RASTER_HOLES", [issue["code"] for issue in result["unresolved"]])

    def test_rectangle_endpoint_reorder_is_automatic_and_pixel_identical(self):
        doc = {"shapes": [shape(points=[[80, 80], [10, 10]], shape_type="rectangle")]}
        result = self.call(doc)
        self.assertEqual(result["corrected_doc"]["shapes"][0]["points"], [[10, 10], [80, 80]])
        self.assertEqual(result["actions"][0]["metrics"]["pixel_iou"], 1)

    def test_tiny_outofbounds_clip_accepted_only_with_pixel_preservation(self):
        points = [[-1, 0], [99, 0], [99, 99], [-1, 99]]
        result = self.call({"shapes": [shape(points=points)]})
        self.assertTrue(result["changed"])
        self.assertEqual(result["actions"][0]["metrics"]["pixel_iou"], 1)
        large = [[-10, 0], [99, 0], [99, 99], [-10, 99]]
        refused = self.call({"shapes": [shape(points=large)]})
        self.assertFalse(refused["changed"])
        self.assertEqual(refused["unresolved"][0]["code"], "COORDINATE_CLIP_TOO_LARGE")

    def test_wall_vs_ceiling_duplicate_automatically_keeps_teacher_supported_label(self):
        doc = {"shapes": [shape("ceiling"), shape("wall")]}
        result = self.call(doc, [teacher(role="diagnostic_patch", full_surface_transfer_ok=False)])
        self.assertEqual(len(result["corrected_doc"]["shapes"]), 1)
        self.assertEqual(result["corrected_doc"]["shapes"][0]["label"], "wall")
        self.assertEqual(result["actions"][0]["action"], "remove_duplicate")
        self.assertFalse(result["unresolved"])

    def test_cross_label_duplicate_without_matching_teacher_is_unresolved(self):
        result = self.call({"shapes": [shape("ladder"), shape("equipment")]}, [teacher(role="diagnostic_patch")])
        self.assertFalse(result["changed"])
        self.assertIn("DUPLICATE_SELECTION_UNCERTAIN", [issue["code"] for issue in result["unresolved"]])

    def test_exact_same_label_metadata_equivalent_duplicate_removed(self):
        first = shape("ladder", flags={"confidence": .7})
        result = self.call({"shapes": [first, deepcopy(first)]})
        self.assertEqual(result["corrected_doc"]["shapes"], [first])
        self.assertEqual(result["classification"], "automatically_repaired")

    def test_same_label_metadata_conflict_without_selection_evidence_is_retained(self):
        result = self.call({"shapes": [shape(group_id=1), shape(group_id=2)]})
        self.assertFalse(result["changed"])
        self.assertEqual(result["unresolved"][0]["code"], "DUPLICATE_SELECTION_UNCERTAIN")

    def test_empty_label_removed_only_when_equivalent_labeled_geometry_has_teacher(self):
        doc = {"shapes": [shape(""), shape("wall")]}
        result = self.call(doc, [teacher()])
        self.assertEqual(len(result["corrected_doc"]["shapes"]), 1)
        self.assertFalse(result["unresolved"])
        unsupported = self.call(doc)
        self.assertFalse(unsupported["changed"])
        self.assertEqual(unsupported["unresolved"][0]["code"], "EMPTY_OR_INVALID_LABEL")

    def test_full_surface_missing_wall_is_restored_as_pseudo_label(self):
        result = self.call({"shapes": []}, [teacher()])
        self.assertEqual(result["classification"], "automatically_repaired")
        self.assertEqual(result["coverage"][0]["coverage_after"], 1)
        restored = result["corrected_doc"]["shapes"][0]
        self.assertEqual(restored["label"], "wall")
        self.assertTrue(restored["flags"]["qa_pseudo_label"])
        self.assertFalse(result["training_eligible"])
        self.assertEqual(result["actions"][0]["action"], "restore_missing_complete_surface")

    def test_patch_or_failed_gate_cannot_restore_wall(self):
        for reference in [teacher(role="diagnostic_patch"), teacher(auto_transfer_ok=False),
                          teacher(full_surface_transfer_ok=False)]:
            with self.subTest(reference=reference):
                result = self.call({"shapes": []}, [reference])
                self.assertFalse(result["changed"])
                self.assertEqual(result["classification"], "unresolved")

    def test_full_surface_can_relabel_but_patch_cannot(self):
        result = self.call({"shapes": [shape("ceiling")]}, [teacher()])
        self.assertEqual(result["corrected_doc"]["shapes"][0]["label"], "wall")
        self.assertEqual(result["actions"][0]["action"], "relabel_full_surface_supported_shape")
        patch = self.call({"shapes": [shape("ceiling")]}, [teacher(role="diagnostic_patch")])
        self.assertFalse(patch["changed"])

    def test_small_full_surface_inside_cross_surface_shape_cannot_relabel_whole_shape(self):
        small = [[0, 0], [30, 0], [30, 99], [0, 99]]
        result = self.call({"shapes": [shape("ceiling")]},
                           [teacher(points=small, role="diagnostic_patch")])
        self.assertFalse(result["changed"])
        self.assertEqual(result["classification"], "unresolved")

    def test_teacher_exclusion_hole_restoration_preserves_visible_surface(self):
        outer = [[0, 0], [299, 0], [299, 299], [0, 299]]
        hole = [[100, 100], [130, 100], [130, 130], [100, 130]]
        result = self.call({"shapes": []}, [teacher(points=outer, exclusions=[hole])], size=(300, 300))
        self.assertTrue(result["changed"])
        self.assertFalse(result["unresolved"])
        expected = Polygon(outer).difference(Polygon(hole))
        restored = unary_union([Polygon(item["points"]) for item in result["corrected_doc"]["shapes"]])
        self.assertLess(restored.difference(expected.buffer(2 ** .5)).area, 1e-6)
        self.assertLess(expected.buffer(-2 ** .5).difference(restored).area, 1e-6)
        self.assertGreaterEqual(result["actions"][0]["restoration_pixel_iou"], .995)
        self.assertEqual(result["actions"][0]["restoration_metrics"]["filled_exclusion_pixels"], 0)
        self.assertEqual(len({item["group_id"] for item in result["corrected_doc"]["shapes"]}), 1)

    def test_small_teacher_hole_uses_canonical_encoding_without_occluder_fill(self):
        hole = [[30, 30], [70, 30], [70, 70], [30, 70]]
        result = self.call({"shapes": []}, [teacher(exclusions=[hole])])
        self.assertTrue(result["changed"])
        self.assertFalse(result["unresolved"])
        metrics = result["actions"][0]["restoration_metrics"]
        self.assertEqual(metrics["method"], "canonical original-grid encoding of projected visible surface")
        self.assertEqual(metrics["pixel_iou"], 1)
        self.assertEqual(metrics["filled_exclusion_pixels"], 0)
        self.assertEqual(metrics["excluded_hole_pixels"], 41 * 41)

    def test_two_teacher_holes_remain_unfilled_after_canonical_restore(self):
        holes = [[[10, 10], [35, 10], [35, 45], [10, 45]],
                 [[55, 50], [85, 50], [85, 85], [55, 85]]]
        result = self.call({"shapes": []}, [teacher(exclusions=holes)])
        self.assertTrue(result["changed"])
        self.assertFalse(result["unresolved"])
        metrics = result["actions"][0]["restoration_metrics"]
        self.assertEqual(metrics["pixel_iou"], 1)
        self.assertEqual(metrics["filled_exclusion_pixels"], 0)
        self.assertEqual(metrics["excluded_hole_pixels"], 26 * 36 + 31 * 36)

    def test_allowed_existing_label_cannot_override_explicit_teacher_occluder(self):
        outer = Polygon([[0, 0], [99, 0], [99, 99], [0, 99]])
        hole_points = [[30, 30], [70, 30], [70, 70], [30, 70]]
        missing = outer.difference(Polygon(hole_points))
        parts, iou, metrics = _known_surface_components(
            missing, (100, 100), dict(DEFAULT_POLICY), True,
            allowed_existing_geometry=outer, strict_exclusions=[hole_points])
        rendered = _render_parts(parts, (0, 0, 100, 100))
        self.assertEqual(np.count_nonzero(rendered[30:71, 30:71]), 0)
        self.assertEqual(metrics["filled_exclusion_pixels"], 0)
        self.assertEqual(metrics["existing_label_overlap_pixels"], 0)
        self.assertGreaterEqual(iou, .995)

    def test_subpixel_existing_wall_cutouts_permit_only_same_label_border_overlap(self):
        outer = Polygon([[0, 0], [399, 0], [399, 399], [0, 399]])
        holes = []
        for x in (40.25, 140.25, 240.25):
            y = 80.25
            holes.append(Polygon([[x, y], [x + 12.5, y + 1.25], [x + 30.5, y],
                                  [x + 30.5, y + 30.5], [x + 20.25, y + 28.75], [x, y + 30.5]]))
        existing = unary_union(holes)
        missing = outer.difference(existing)
        parts, iou, metrics = _known_surface_components(
            missing, (400, 400), dict(DEFAULT_POLICY), True,
            allowed_existing_geometry=existing, strict_exclusions=[])
        self.assertTrue(parts)
        self.assertGreaterEqual(iou, .995)
        self.assertEqual(metrics["filled_exclusion_pixels"], 0)
        self.assertGreater(metrics["existing_label_overlap_pixels"], 0)
        self.assertEqual(metrics["method"], "projected_geometry_hole_decomposition")

    def test_unknown_label_schema_marks_unmapped_without_rewriting(self):
        result = self.call({"shapes": [shape("banana")]}, policy={"allowed_labels": ["wall", "ceiling"]})
        self.assertFalse(result["changed"])
        self.assertEqual(result["quality_inputs"]["unmapped_labels"], ["banana"])
        self.assertEqual(result["unresolved"][0]["code"], "UNMAPPED_LABEL")

    def test_contradictory_teachers_cannot_select_or_restore(self):
        wall, ceiling = teacher(), teacher("ceiling", id="ceiling_plane")
        result = self.call({"shapes": [shape("wall"), shape("ceiling")]}, [wall, ceiling])
        self.assertFalse(result["changed"])
        self.assertIn("CONTRADICTORY_TEACHER_REGIONS", [issue["code"] for issue in result["unresolved"]])

    def test_three_way_teacher_conflict_disables_every_involved_reference(self):
        regions = [teacher(id="wall_a"), teacher("ceiling", id="ceiling_b"), teacher(id="wall_c")]
        result = self.call({"shapes": [shape("ceiling")]}, regions)
        self.assertFalse(result["changed"])
        self.assertIsNone(result["corrected_doc"])
        self.assertEqual(result["quality_inputs"]["automatic_teacher_count"], 0)
        conflicts = [issue for issue in result["unresolved"] if issue["code"] == "CONTRADICTORY_TEACHER_REGIONS"]
        self.assertEqual(len(conflicts), 2)
        involved = {identifier for issue in conflicts for identifier in issue["region_ids"]}
        self.assertEqual(involved, {"wall_a", "ceiling_b", "wall_c"})

    def test_optional_raster_hole_canonical_encoding_preserves_all_source_pixels(self):
        ring = [[0, 0], [99, 0], [99, 99], [0, 99], [0, 0],
                [30, 30], [30, 70], [70, 70], [70, 30], [30, 30], [0, 0]]
        result = self.call({"shapes": [shape(points=ring)]}, policy={
            "allow_raster_hole_decomposition": True, "max_components": 512})
        self.assertTrue(result["changed"])
        self.assertFalse(result["unresolved"])
        metrics = result["actions"][0]["metrics"]
        self.assertEqual(metrics["pixel_iou"], 1)
        self.assertEqual(metrics["lost_source_pixels"], 0)
        self.assertEqual(metrics["added_pixels"], 0)
        self.assertGreater(metrics["source_hole_pixels"], 100)
        self.assertEqual(metrics["discarded_hole_pixels"], 0)
        self.assertFalse(metrics["semantic_intent_verified"])
        self.assertTrue(all(Polygon(item["points"]).is_valid
                            for item in result["corrected_doc"]["shapes"]))

    def test_many_disconnected_components_can_be_encoded_with_larger_explicit_cap(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        for y in range(0, 20, 4):
            for x in range(0, 80, 4):
                mask[y:y + 2, x:x + 2] = 255
        limits = dict(DEFAULT_POLICY, max_components=512, allow_raster_hole_decomposition=True)
        parts, metrics = _contour_parts(mask, (0, 0, 100, 100), limits)
        self.assertEqual(len(parts), 100)
        self.assertEqual(metrics["pixel_iou"], 1)
        self.assertEqual(metrics["discarded_component_count"], 0)

    def test_component_trimming_is_accepted_only_with_global_pixel_iou_bound(self):
        mask = np.zeros((150, 200), dtype=np.uint8)
        mask[:100, :100] = 255
        for x in range(0, 80, 2):
            mask[120, x] = 255
        limits = dict(DEFAULT_POLICY, max_components=1, allow_raster_hole_decomposition=True)
        parts, metrics = _contour_parts(mask, (0, 0, 200, 150), limits)
        self.assertEqual(len(parts), 1)
        self.assertEqual(metrics["discarded_component_count"], 40)
        self.assertEqual(metrics["lost_source_pixels"], 40)
        self.assertGreaterEqual(metrics["pixel_iou"], .995)
        for x in range(80, 160, 2):
            mask[120, x] = 255
        with self.assertRaisesRegex(ValueError, "RASTER_VECTOR_PIXEL_DRIFT"):
            _contour_parts(mask, (0, 0, 200, 150), limits)

    def test_later_complete_restore_resolves_earlier_diagnostic_missing_metric(self):
        patch = teacher(id="diagnostic", points=[[20, 20], [80, 20], [80, 80], [20, 80]],
                        role="diagnostic_patch", full_surface_transfer_ok=False)
        result = self.call({"shapes": []}, [patch, teacher(id="complete")])
        self.assertEqual(result["classification"], "automatically_repaired")
        self.assertFalse(result["unresolved"])
        self.assertEqual(result["coverage"][0]["coverage_before_restore"], 0)
        self.assertEqual(result["coverage"][0]["coverage_after"], 1)

    def test_resolved_diagnostic_missing_does_not_remove_source_geometry_errors(self):
        bad = shape("floor", [["bad", 0]])
        patch = teacher(id="diagnostic", role="diagnostic_patch", full_surface_transfer_ok=False)
        result = self.call({"shapes": [bad]}, [patch, teacher(id="complete")])
        self.assertEqual(result["classification"], "partially_repaired")
        self.assertEqual(result["coverage"][0]["coverage_after"], 1)
        self.assertIn("UNUSABLE_SHAPE_COORDINATES_OR_TYPE", [issue["code"] for issue in result["unresolved"]])
        self.assertFalse(any(issue["code"] == "MISSING_LABEL_NOT_AUTOMATICALLY_RESTORABLE"
                             and issue.get("region_id") == "diagnostic" for issue in result["unresolved"]))

    def test_complete_patch_cannot_relabel_cross_surface_shape_and_residual_is_explicit(self):
        small = [[0, 0], [30, 0], [30, 99], [0, 99]]
        result = self.call({"shapes": [shape("ceiling")]}, [teacher(points=small)])
        self.assertEqual(result["classification"], "partially_repaired")
        self.assertEqual(result["corrected_doc"]["shapes"][0]["label"], "ceiling")
        self.assertIn("RESIDUAL_CONFLICTING_LABEL_COVERAGE", [issue["code"] for issue in result["unresolved"]])


if __name__ == "__main__":
    unittest.main()
