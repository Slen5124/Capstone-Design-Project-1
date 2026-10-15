"""Reference-region checks and conservative duplicate correction drafts.

This module never modifies its input, relabels shapes, or creates missing masks.
Reference regions must already be projected and visibility-gated by the caller.
"""
from collections import defaultdict
from copy import deepcopy
import json
import math

from PIL import Image, ImageChops, ImageDraw
from shapely.geometry import MultiPoint, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _label(value):
    return value.strip().casefold() if isinstance(value, str) else ""


def _size(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(_number(v) and v > 0 and int(v) == v for v in value))


def _shape_geometry(shape, size):
    """Return valid geometry or conservative coordinate-hull upper bound.

    A hull is a measurement bound for the submitted coordinates, not a repaired
    annotation and not an assertion about the intended unseen object.
    """
    if not isinstance(shape, dict):
        return None, None, "NOT_OBJECT"
    points = shape.get("points")
    if (not isinstance(points, list) or not points or not all(
            isinstance(p, (list, tuple)) and len(p) == 2
            and all(_number(v) for v in p) for p in points)):
        return None, None, "INVALID_POINTS"
    points = [tuple(p) for p in points]
    kind = shape.get("shape_type") or "polygon"
    if not isinstance(kind, str) or kind not in {"polygon", "rectangle"}:
        # Point/line/circle semantics do not establish a polygon-hull bound.
        return None, None, "UNSUPPORTED_GEOMETRY"
    try:
        hull = MultiPoint(points).convex_hull
        if kind == "rectangle":
            if len(points) != 2:
                return None, None, "INVALID_RECTANGLE_POINTS"
            (x1, y1), (x2, y2) = points
            geometry = box(min(x1, x2), min(y1, y2),
                           max(x1, x2), max(y1, y2))
            # A rectangle's two diagonal points do not bound it by their line.
            hull = geometry
        else:
            if len(set(points)) < 3:
                return None, hull, "INSUFFICIENT_DISTINCT_POINTS"
            geometry = Polygon(points)
        if not math.isfinite(geometry.area):
            return None, None, "NONFINITE_GEOMETRY_AREA"
        if not geometry.is_valid or geometry.area <= 1e-8:
            return None, hull, "DEGENERATE_OR_INVALID_POLYGON"
        if size and any(x < 0 or y < 0 or x > size[0] or y > size[1]
                        for x, y in points):
            return None, hull, "OUT_OF_BOUNDS"
        return geometry, geometry, None
    except Exception:
        return None, None, "GEOMETRY_ERROR"


def _metadata_key(shape):
    try:
        return json.dumps({k: v for k, v in shape.items() if k != "points"},
                          sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _fraction(geometries, target):
    if not geometries:
        return 0.0
    value = unary_union(geometries).intersection(target).area / target.area
    if not math.isfinite(value):
        raise ValueError("Nonfinite measured coverage")
    return min(1.0, max(0.0, value))


def _region(region, image_size):
    if not isinstance(region, dict) or not _label(region.get("label")):
        raise ValueError("INVALID_REGION_LABEL")
    geometry, _, error = _shape_geometry(
        {"points": region.get("points"), "shape_type": "polygon"}, image_size)
    if error:
        raise ValueError("INVALID_REGION_GEOMETRY")
    exclusions = region.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("INVALID_REGION_EXCLUSIONS")
    for points in exclusions:
        exclusion, _, error = _shape_geometry(
            {"points": points, "shape_type": "polygon"}, image_size)
        if error:
            raise ValueError("INVALID_REGION_EXCLUSION")
        geometry = geometry.difference(exclusion)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 1e-8:
        raise ValueError("REGION_HAS_NO_VISIBLE_AREA")
    minimum = region.get("min_coverage", .6)
    maximum = region.get("max_conflict", .25)
    if not all(_number(v) and 0 <= v <= 1 for v in (minimum, maximum)):
        raise ValueError("INVALID_REGION_THRESHOLD")
    conflicts = region.get("conflict_labels", [])
    if not isinstance(conflicts, list) or not all(_label(v) for v in conflicts):
        raise ValueError("INVALID_CONFLICT_LABELS")
    return geometry, minimum, maximum, list(dict.fromkeys(map(_label, conflicts)))


def _raw_raster_points(shape, image_size):
    """Validate coordinates for renderer diagnostics without repairing them."""
    if not isinstance(shape, dict):
        return None, None
    kind = shape.get("shape_type") or "polygon"
    if not isinstance(kind, str) or kind not in {"polygon", "rectangle"}:
        return None, None
    points = shape.get("points")
    if (not isinstance(points, list) or not all(
            isinstance(p, (list, tuple)) and len(p) == 2
            and all(_number(v) for v in p) for p in points)):
        return None, None
    points = [tuple(p) for p in points]
    if (kind == "polygon" and len(set(points)) < 3
            or kind == "rectangle" and len(points) != 2
            or any(x < 0 or y < 0 or x > image_size[0] or y > image_size[1]
                   for x, y in points)):
        return None, None
    return kind, points


def _draw_raw(draw, kind, points, fill):
    if kind == "rectangle":
        (x1, y1), (x2, y2) = points
        # Reordering diagonal endpoints follows the existing rectangle meaning;
        # no stored coordinates are changed.
        draw.rectangle((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), fill=fill)
    else:
        draw.polygon(points, fill=fill)


def _rendered_region(region, image_size):
    mask = Image.new("L", tuple(map(int, image_size)), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([tuple(p) for p in region["points"]], fill=255)
    for exclusion in region.get("exclusions", []):
        draw.polygon([tuple(p) for p in exclusion], fill=0)
    return mask, mask.histogram()[255]


def _rank_proposals(items, region_targets):
    """Order review proposals by explicit support, never choose a correct label."""
    proposals = []
    for item in items:
        geometry = item["geometry"]
        upper_only = geometry is None
        measurement = item["upper"] if upper_only else geometry
        evidence, score = [], 0.0
        for region_id, label, target in region_targets:
            if label != item["label"] or measurement is None:
                continue
            overlap = measurement.intersection(target).area
            if upper_only:
                fraction = overlap / target.area
                evidence.append({"region_id": region_id,
                                 "roi_coverage_upper": fraction,
                                 "measurement": "submitted_coordinate_hull_upper_bound"})
                score += fraction
            else:
                total = geometry.area + target.area - overlap
                iou = overlap / total if total else 0.0
                evidence.append({"region_id": region_id,
                                 "roi_coverage": overlap / target.area,
                                 "roi_iou": iou, "measurement": "valid_geometry"})
                score += iou
        proposals.append({"shape_index": item["index"], "label": item["label"],
                          "teacher_evidence": evidence, "score": score,
                          "support_is_upper_bound_only": upper_only,
                          "ranking_is_not_label_correctness": True})
    proposals.sort(key=lambda proposal: (-proposal["score"], proposal["shape_index"]))
    return proposals


def analyze(doc, regions, image_size, basis="reviewed_image", raster_diagnostics=False):
    """Produce candidates, bounded ROI measurements and exact-dedupe drafts.

    ``semantic_status`` is ``reviewed_roi_rules_only``,
    ``provisional_reference_rules_only``, ``partial_abstain``, ``abstain`` or
    ``not_checked``. Optional renderer diagnostics may instead return
    ``rendered_reference_rules_only`` or ``provisional_rendered_reference_rules_only``.
    None denotes a visual pass. Every correction is a draft.
    """
    result = {"issues": [], "warnings": [], "metrics": [],
              "semantic_status": "not_checked", "duplicate_groups": [],
              "corrected_doc": None, "correction_actions": []}
    if basis not in {"reviewed_image", "provisional_reference_projection"}:
        result["warnings"].append("UNSUPPORTED_SEMANTIC_BASIS")
        result["semantic_status"] = "abstain"
        return result
    if not _size(image_size):
        result["warnings"].append("SEMANTIC_IMAGE_SIZE_UNUSABLE")
        result["semantic_status"] = "abstain"
        return result
    if not isinstance(doc, dict) or not isinstance(doc.get("shapes"), list):
        result["warnings"].append("SEMANTIC_SHAPES_UNUSABLE")
        result["semantic_status"] = "abstain"
        return result
    if not isinstance(regions, list):
        result["warnings"].append("SEMANTIC_REGIONS_UNUSABLE")
        result["semantic_status"] = "abstain"
        return result
    by_label, usable, region_targets = defaultdict(list), [], []
    for index, shape in enumerate(doc["shapes"]):
        label = _label(shape.get("label")) if isinstance(shape, dict) else ""
        geometry, upper, error = _shape_geometry(shape, image_size)
        item = {"index": index, "label": label, "geometry": geometry,
                "upper": upper, "error": error, "shape": shape}
        if not label:
            result["warnings"].append(f"SEMANTIC_SHAPE_{index}_INVALID_LABEL")
            # Unknown-label shapes cannot safely establish expected-label absence.
            item["geometry"], item["upper"] = None, None
            by_label[""].append(item)
        else:
            by_label[label].append(item)
            if geometry is not None:
                usable.append(item)
        if error:
            result["warnings"].append(f"SEMANTIC_SHAPE_{index}_{error}")

    rendered_cache = {}

    def rendered_label(label):
        if label in rendered_cache:
            return rendered_cache[label]
        mask = Image.new("L", tuple(map(int, image_size)), 0)
        draw = ImageDraw.Draw(mask)
        uncertain = False
        for item in by_label[label]:
            kind, points = _raw_raster_points(item["shape"], image_size)
            if kind is None:
                uncertain = True
                continue
            try:
                _draw_raw(draw, kind, points, 255)
            except (ValueError, TypeError, OverflowError):
                uncertain = True
                continue
            if item["error"]:
                result["warnings"].append("INVALID_POLYGON_RASTER_INTERPRETATION_REQUIRES_REVIEW")
                result["warnings"].append(
                    f"SEMANTIC_SHAPE_{item['index']}_RAW_COORDINATE_RENDERING_ONLY")
        rendered_cache[label] = mask, uncertain
        return mask, uncertain

    checked, abstained, rendered_checked = 0, 0, 0
    for ordinal, region in enumerate(regions):
        region_id = str(region.get("id", ordinal)) if isinstance(region, dict) else str(ordinal)
        prefix = f"ROI_{ordinal}"
        try:
            target, minimum, maximum, conflicts = _region(region, image_size)
        except (ValueError, TypeError) as exc:
            result["warnings"].append(f"{prefix}_{str(exc)}")
            result["metrics"].append({"region_id": region_id, "basis": basis,
                                      "status": "abstain", "needs_review": True})
            abstained += 1
            continue
        label = _label(region["label"])
        region_targets.append((region_id, label, target))
        entries = by_label[label]
        valid = [item["geometry"] for item in entries if item["geometry"] is not None]
        invalid = [item for item in entries if item["geometry"] is None]
        unbounded = any(item["upper"] is None for item in invalid) or bool(by_label[""])
        upper = [item["upper"] for item in entries if item["upper"] is not None]
        try:
            lower_coverage = _fraction(valid, target)
            upper_coverage = 1.0 if unbounded else _fraction(upper, target)
            conflict_coverage = {
                other: _fraction([item["geometry"] for item in by_label[other]
                                  if item["geometry"] is not None], target)
                for other in conflicts if other != label}
        except Exception:
            result["warnings"].append(f"{prefix}_MEASUREMENT_FAILED")
            result["metrics"].append({"region_id": region_id, "basis": basis,
                                      "status": "abstain", "needs_review": True})
            abstained += 1
            continue
        metric = {"region_id": region_id, "label": label, "basis": basis,
                  "visible_roi_area": target.area,
                  "coverage_lower": lower_coverage, "coverage_upper": upper_coverage,
                  "min_coverage": minimum, "conflict_coverage": conflict_coverage,
                  "max_conflict": maximum, "invalid_expected_shape_indices":
                  [item["index"] for item in invalid], "status": "checked",
                  "needs_review": True}
        if upper_coverage < minimum:
            suffix = ("EXPECTED_LABEL_ABSENT" if not entries and not by_label[""]
                      else "LOW_LABEL_COVERAGE")
            result["issues"].append(f"{prefix}_{suffix}")
            metric["missing_evidence"] = (
                "no_shapes_with_expected_label" if not entries else
                "submitted_coordinate_upper_bound_below_threshold" if invalid else
                "valid_geometry_coverage_below_threshold")
        elif lower_coverage < minimum and (invalid or by_label[""]):
            metric["status"] = "abstain"
            result["warnings"].append(f"{prefix}_EXPECTED_LABEL_GEOMETRY_UNCERTAIN")
            abstained += 1
        # A conflict can be established from valid shapes even if the expected
        # label's measurement is uncertain.
        for other, fraction in conflict_coverage.items():
            if fraction > maximum:
                result["issues"].append(f"{prefix}_CONFLICTING_LABEL_{other}")
        if raster_diagnostics:
            metric["geometric_status"] = metric["status"]
            metric["rendered_pixel_method"] = "Pillow.ImageDraw.polygon/rectangle original pixel grid"
            metric["rendered_status"] = "abstain"
            metric["rendered_conflict_coverage"] = {}
            try:
                region_mask, region_pixels = _rendered_region(region, image_size)
                if not region_pixels:
                    raise ValueError("Region has no rendered pixels")
                expected_mask, raster_uncertain = rendered_label(label)
                raster_coverage = ImageChops.multiply(expected_mask, region_mask).histogram()[255] / region_pixels
                metric["rendered_coverage"] = raster_coverage
                metric["rendered_roi_pixels"] = region_pixels
                if raster_uncertain or by_label[""]:
                    result["warnings"].append(f"{prefix}_RENDERED_EXPECTED_LABEL_UNUSABLE")
                else:
                    metric["rendered_status"] = "checked"
                    rendered_checked += 1
                    if raster_coverage < minimum:
                        suffix = "EXPECTED_LABEL_ABSENT" if not entries else "LOW_LABEL_COVERAGE"
                        result["issues"].append(f"{prefix}_RENDERED_{suffix}")
                for other in conflicts:
                    if other == label:
                        continue
                    conflict_mask, conflict_uncertain = rendered_label(other)
                    conflict_fraction = ImageChops.multiply(conflict_mask, region_mask).histogram()[255] / region_pixels
                    metric["rendered_conflict_coverage"][other] = conflict_fraction
                    # Renderable shapes can establish positive conflict evidence
                    # even when another conflicting shape cannot be rendered.
                    if conflict_fraction > maximum:
                        result["issues"].append(f"{prefix}_RENDERED_CONFLICTING_LABEL_{other}")
                    if conflict_uncertain:
                        result["warnings"].append(f"{prefix}_RENDERED_CONFLICT_LABEL_{other}_PARTIAL")
            except (ValueError, TypeError, OverflowError) as exc:
                result["warnings"].append(f"{prefix}_RENDERED_MEASUREMENT_FAILED_{type(exc).__name__}")
        result["metrics"].append(metric)
        if metric["status"] == "checked":
            checked += 1
    if regions:
        result["semantic_status"] = (
            "partial_abstain" if checked and abstained else "abstain" if abstained
            else "reviewed_roi_rules_only" if basis == "reviewed_image"
            else "provisional_reference_rules_only")
    if raster_diagnostics and rendered_checked:
        result["semantic_status"] = (
            "rendered_reference_rules_only" if basis == "reviewed_image"
            else "provisional_rendered_reference_rules_only")
        result["warnings"].append("RASTER_DIAGNOSTICS_ARE_RENDERER_EVIDENCE_ONLY")
    if basis == "provisional_reference_projection":
        result["warnings"].append("REFERENCE_PROJECTION_REQUIRES_VISUAL_REVIEW")

    # Group exact duplicates by valid geometry and complete remaining metadata.
    groups = defaultdict(list)
    for item in usable:
        groups[item["label"]].append(item)
    remove, pairs = set(), 0
    # Identical invalid dictionaries carry no distinct information. Remove only
    # literal duplicates, preserving one unchanged copy of the invalid shape.
    literal_invalid = defaultdict(list)
    for entries in by_label.values():
        for item in entries:
            if item["geometry"] is not None or not isinstance(item["shape"], dict):
                continue
            try:
                fingerprint = json.dumps(item["shape"], sort_keys=True,
                                         ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                continue
            literal_invalid[fingerprint].append(item["index"])
    for indices in literal_invalid.values():
        if len(indices) < 2:
            continue
        indices.sort()
        keep, discarded = indices[0], indices[1:]
        remove.update(discarded)
        result["duplicate_groups"].append({
            "kind": "exact_literal_duplicate", "shape_indices": indices,
            "keep_index": keep, "remove_indices": discarded,
            "evidence": "entire_original_shape_dictionaries_identical_no_geometry_repair",
            "needs_review": True})
        result["issues"].append("EXACT_LITERAL_DUPLICATE_SHAPES_" + "_".join(map(str, indices)))
        result["correction_actions"].append({
            "action": "remove_exact_redundant_shapes", "keep_index": keep,
            "remove_indices": discarded, "needs_review": True,
            "reason": "Entire repeated shape dictionaries are identical; retained coordinates remain unchanged."})
    for entries in groups.values():
        geometries = [item["geometry"] for item in entries]
        tree = STRtree(geometries)
        metadata = [_metadata_key(item["shape"]) for item in entries]
        exact_parent = list(range(len(entries)))

        def find(index):
            while exact_parent[index] != index:
                exact_parent[index] = exact_parent[exact_parent[index]]
                index = exact_parent[index]
            return index

        for i, geometry in enumerate(geometries):
            for value in tree.query(geometry):
                j = int(value)
                if j <= i:
                    continue
                if pairs >= 1000:
                    break
                other = geometries[j]
                intersection = geometry.intersection(other).area
                union = geometry.area + other.area - intersection
                iou = intersection / union if union else 0.0
                if iou < .98:
                    continue
                pairs += 1
                exact = (metadata[i] is not None and metadata[i] == metadata[j]
                         and geometry.equals(other))
                if exact:
                    exact_parent[find(j)] = find(i)
                    continue
                proposals = _rank_proposals((entries[i], entries[j]), region_targets)
                result["duplicate_groups"].append({
                    "kind": "near_duplicate", "shape_indices":
                    [entries[i]["index"], entries[j]["index"]], "iou": iou,
                    "selection": "human_review_required", "basis": basis,
                    "metadata_equal": metadata[i] is not None and metadata[i] == metadata[j],
                    "ranked_proposals": proposals, "needs_review": True})
                result["issues"].append(
                    f"NEAR_DUPLICATE_SHAPES_{entries[i]['index']}_{entries[j]['index']}")
            if pairs >= 1000:
                result["warnings"].append("DUPLICATE_DETAILS_LIMIT_REACHED")
                break
        components = defaultdict(list)
        for i, item in enumerate(entries):
            components[find(i)].append(item["index"])
        for indices in components.values():
            if len(indices) < 2:
                continue
            indices.sort()
            keep, discarded = indices[0], indices[1:]
            remove.update(discarded)
            result["duplicate_groups"].append({
                "kind": "exact_semantic_duplicate", "shape_indices": indices,
                "keep_index": keep, "remove_indices": discarded,
                "evidence": "valid_geometry_equal_and_all_other_shape_attributes_equal",
                "needs_review": True})
            result["issues"].append("EXACT_DUPLICATE_SHAPES_" + "_".join(map(str, indices)))
            result["correction_actions"].append({
                "action": "remove_exact_redundant_shapes", "keep_index": keep,
                "remove_indices": discarded, "needs_review": True,
                "reason": "No geometric or shape-attribute information is removed."})

    # Different labels can describe the same submitted object region. Present
    # these as competing annotations for human review, never auto-delete one.
    cross_pairs = 0
    if usable:
        geometries = [item["geometry"] for item in usable]
        tree = STRtree(geometries)
        for i, geometry in enumerate(geometries):
            for value in tree.query(geometry):
                j = int(value)
                if j <= i or usable[i]["label"] == usable[j]["label"]:
                    continue
                if cross_pairs >= 1000:
                    break
                overlap = geometry.intersection(geometries[j]).area
                union = geometry.area + geometries[j].area - overlap
                iou = overlap / union if union else 0.0
                if iou < .98:
                    continue
                cross_pairs += 1
                indices = [usable[i]["index"], usable[j]["index"]]
                result["duplicate_groups"].append({
                    "kind": "cross_label_overlap", "shape_indices": indices,
                    "labels": [usable[i]["label"], usable[j]["label"]],
                    "iou": iou, "coordinate_relation": "nearly_identical_valid_geometry",
                    "selection": "human_review_required", "basis": basis,
                    "ranked_proposals": _rank_proposals((usable[i], usable[j]), region_targets),
                    "needs_review": True})
                result["issues"].append("CROSS_LABEL_OVERLAP_SHAPES_" + "_".join(map(str, indices)))
            if cross_pairs >= 1000:
                break
    raw_coordinate_groups = defaultdict(list)
    for label, entries in by_label.items():
        if not label:
            continue
        for item in entries:
            if item["geometry"] is not None:
                continue
            kind, points = _raw_raster_points(item["shape"], image_size)
            if kind is not None:
                fingerprint = json.dumps({"shape_type": kind, "points": points},
                                         sort_keys=True, allow_nan=False)
                raw_coordinate_groups[fingerprint].append(item)
    for entries in raw_coordinate_groups.values():
        for i, first in enumerate(entries):
            for second in entries[i + 1:]:
                if cross_pairs >= 1000:
                    break
                if first["label"] == second["label"]:
                    continue
                cross_pairs += 1
                indices = [first["index"], second["index"]]
                result["duplicate_groups"].append({
                    "kind": "cross_label_overlap", "shape_indices": indices,
                    "labels": [first["label"], second["label"]], "iou": None,
                    "coordinate_relation": "same_submitted_points_and_shape_type_invalid_topology",
                    "selection": "human_review_required", "basis": basis,
                    "ranked_proposals": _rank_proposals((first, second), region_targets),
                    "needs_review": True})
                result["issues"].append("CROSS_LABEL_OVERLAP_SHAPES_" + "_".join(map(str, indices)))
            if cross_pairs >= 1000:
                break
        if cross_pairs >= 1000:
            break
    if cross_pairs >= 1000:
        result["warnings"].append("CROSS_LABEL_OVERLAP_DETAILS_LIMIT_REACHED")
    if remove:
        corrected = deepcopy(doc)
        corrected["shapes"] = [shape for i, shape in enumerate(corrected["shapes"])
                               if i not in remove]
        result["corrected_doc"] = corrected
    result["issues"] = list(dict.fromkeys(result["issues"]))
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result
