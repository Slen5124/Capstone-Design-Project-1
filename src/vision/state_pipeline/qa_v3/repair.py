"""Bounded automatic annotation repair; new semantic masks remain pseudo-labels.

The caller owns teacher provenance, projection and visibility gates. Only regions
explicitly marked auto_transfer_ok can guide automatic semantic decisions.
"""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from shapely import make_valid
from shapely.geometry import LineString, Polygon
from shapely.ops import split, triangulate, unary_union
from shapely.strtree import STRtree

_spec = importlib.util.spec_from_file_location(
    "_qa_v2_semantic_repair_helpers", Path(__file__).resolve().parent.parent / "qa_v2" / "semantic.py")
_semantic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_semantic)

DEFAULT_POLICY = {"min_pixel_iou": .995, "max_hole_fraction": .001,
                  "max_hole_pixels": 4, "max_clip_pixels": 2,
                  "max_components": 32, "duplicate_iou": .98,
                  "semantic_support": .8, "semantic_margin": .5,
                  "same_label_margin": .1, "relabel_shape_coverage": .9,
                  "restore_min_coverage": .6, "restore_missing_surfaces": True,
                  "max_restoration_components": 512, "allowed_labels": None,
                  "allow_raster_hole_decomposition": False,
                  "teacher_boundary_drift_pixels": 1.0,
                  "teacher_boundary_sliver_area_pixels": 1.0}


def _parts(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry]
    result = []
    if hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            result.extend(_parts(child))
    return result


def _mask(points, kind, size, crop=None):
    if crop is None:
        xs, ys = zip(*points)
        left, top = max(0, math.floor(min(xs)) - 2), max(0, math.floor(min(ys)) - 2)
        right = min(size[0], math.ceil(max(xs)) + 3)
        bottom = min(size[1], math.ceil(max(ys)) + 3)
        crop = (left, top, right, bottom)
    left, top, right, bottom = crop
    if right <= left or bottom <= top:
        raise ValueError("NO_VISIBLE_RASTER_AREA")
    image = Image.new("L", (right - left, bottom - top), 0)
    _semantic._draw_raw(ImageDraw.Draw(image), kind,
                        [(x - left, y - top) for x, y in points], 255)
    return np.asarray(image).copy(), crop


def _pixel_iou(first, second):
    union = np.count_nonzero((first != 0) | (second != 0))
    return (np.count_nonzero((first != 0) & (second != 0)) / union) if union else 1.0


def _render_parts(parts, crop):
    left, top, right, bottom = crop
    image = Image.new("L", (right - left, bottom - top), 0)
    draw = ImageDraw.Draw(image)
    for poly in parts:
        draw.polygon([(x - left, y - top) for x, y in list(poly.exterior.coords)[:-1]], fill=255)
    return np.asarray(image)


def _geometry_mask(geometry, crop):
    left, top, right, bottom = crop
    mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
    for polygon in _parts(geometry):
        image = Image.new("L", (right - left, bottom - top), 0)
        draw = ImageDraw.Draw(image)
        draw.polygon([(x - left, y - top) for x, y in polygon.exterior.coords], fill=255)
        for ring in polygon.interiors:
            draw.polygon([(x - left, y - top) for x, y in ring.coords], fill=0)
        mask |= np.asarray(image)
    return mask


def _hole_free_parts(geometry):
    """Cut through supplied holes without dropping their excluded area."""
    pending, output = _parts(geometry), []
    while pending:
        polygon = pending.pop()
        if not polygon.interiors:
            output.append(polygon)
            continue
        representative = Polygon(polygon.interiors[0]).representative_point()
        minx, _, maxx, _ = polygon.bounds
        line = LineString([(minx - 1, representative.y), (maxx + 1, representative.y)])
        pieces = _parts(split(polygon, line))
        if (len(pieces) >= 2 and all(part.is_valid for part in pieces)
                and sum(len(part.interiors) for part in pieces) < len(polygon.interiors)):
            pending.extend(pieces)
            continue
        # Fallback for an unusual geometry that cannot be split cleanly.
        for triangle in triangulate(polygon):
            for part in _parts(triangle.intersection(polygon)):
                if part.area <= 1e-8:
                    continue
                if not part.is_valid or part.interiors:
                    raise ValueError("HOLE_DECOMPOSITION_FAILED")
                output.append(part)
    return output


def _known_surface_components(geometry, size, policy, return_metadata=False,
                              allowed_existing_geometry=None, strict_exclusions=None):
    """Preserve explicitly supplied teacher holes through component polygons."""
    output = _hole_free_parts(geometry)
    geometric_drift = (unary_union(output).symmetric_difference(geometry).area
                       if output else geometry.area)
    minx, miny, maxx, maxy = geometry.bounds
    crop = (max(0, math.floor(minx) - 2), max(0, math.floor(miny) - 2),
            min(size[0], math.ceil(maxx) + 3), min(size[1], math.ceil(maxy) + 3))
    left, top, right, bottom = crop
    if right <= left or bottom <= top:
        raise ValueError("TEACHER_OUT_OF_FRAME")
    source_mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
    excluded_mask = np.zeros_like(source_mask)
    for polygon in _parts(geometry):
        source = Image.new("L", (right - left, bottom - top), 0)
        draw = ImageDraw.Draw(source)
        draw.polygon([(x - left, y - top) for x, y in polygon.exterior.coords], fill=255)
        for ring in polygon.interiors:
            draw.polygon([(x - left, y - top) for x, y in ring.coords], fill=0)
            hole = Image.new("L", (right - left, bottom - top), 0)
            ImageDraw.Draw(hole).polygon([(x - left, y - top) for x, y in ring.coords], fill=255)
            excluded_mask |= np.asarray(hole)
        source_mask |= np.asarray(source)
    # A disjoint polygon island inside another polygon's hole is visible, not
    # excluded; evaluate the complete source union before enforcing hole pixels.
    excluded_mask[source_mask != 0] = 0
    # Difference holes may already contain a correctly labeled wall. Overlap on
    # those pixels does not change the final label union; actual teacher fixture
    # exclusions remain protected even when a source wall polygon covers them.
    allowed_overlap_mask = np.zeros_like(source_mask)
    geometry_exclusion_overlap_pixels = 0
    if allowed_existing_geometry is not None:
        allowed_overlap_mask = _geometry_mask(allowed_existing_geometry, crop)
        excluded_mask[allowed_overlap_mask != 0] = 0
    if strict_exclusions is not None:
        explicit = Image.new("L", (right - left, bottom - top), 0)
        draw = ImageDraw.Draw(explicit)
        for points in strict_exclusions:
            draw.polygon([(x - left, y - top) for x, y in points], fill=255)
        explicit_mask = np.asarray(explicit)
        # Geometric difference excludes an occluder's interior, but its closed
        # exterior/notch edge can still paint occluder boundary pixels. The
        # teacher explicitly excludes those pixels; remove them from the source
        # pixel expectation before judging fidelity, rather than accepting fill.
        geometry_exclusion_overlap_pixels = int(np.count_nonzero((source_mask != 0) & (explicit_mask != 0)))
        source_mask[explicit_mask != 0] = 0
        excluded_mask |= explicit_mask
        allowed_overlap_mask[excluded_mask != 0] = 0
    rendered = _render_parts(output, crop) if output else np.zeros_like(source_mask)
    pixel_iou = _pixel_iou(source_mask, rendered)
    filled_excluded = int(np.count_nonzero((excluded_mask != 0) & (rendered != 0)))
    normal_ok = (output and len(output) <= policy["max_restoration_components"]
                 and geometric_drift <= max(1e-6, geometry.area * 1e-8)
                 and pixel_iou >= policy["min_pixel_iou"] and filled_excluded == 0)
    metadata = {"method": "projected_geometry_hole_decomposition", "pixel_iou": pixel_iou,
                "geometric_symmetric_difference_area": geometric_drift,
                "excluded_hole_pixels": int(np.count_nonzero(excluded_mask)),
                "filled_exclusion_pixels": filled_excluded, "boundary_drift_limit_pixels": 0.0,
                "source_geometry_pixels_removed_by_explicit_exclusion": geometry_exclusion_overlap_pixels,
                "existing_label_overlap_pixels": int(np.count_nonzero((source_mask == 0) & (rendered != 0) & (allowed_overlap_mask != 0)))}
    if not normal_ok:
        # Canonicalize the known visible teacher pixel mask when inclusive
        # polygon borders would otherwise paint an excluded shelf/fixture edge.
        raster_policy = dict(policy, allow_raster_hole_decomposition=True,
                             max_components=policy["max_restoration_components"])
        working_mask = source_mask.copy()
        cleanup_iterations = 0
        for attempt in range(4):
            output, metrics = _contour_parts(working_mask, crop, raster_policy)
            rendered = _render_parts(output, crop)
            filled = (excluded_mask != 0) & (rendered != 0)
            filled_excluded = int(np.count_nonzero(filled))
            if not filled_excluded:
                break
            if attempt == 3:
                raise ValueError("TEACHER_EXCLUSION_PIXELS_FILLED")
            # Remove only neighbors of demonstrated false-positive occluder
            # pixels. Never erode the whole region or loosen the original IoU.
            local = cv2.dilate(filled.astype(np.uint8), np.ones((3, 3), dtype=np.uint8)) != 0
            working_mask[local] = 0
            cleanup_iterations += 1
        pixel_iou = _pixel_iou(source_mask, rendered)
        if pixel_iou < policy["min_pixel_iou"]:
            raise ValueError("TEACHER_CANONICAL_ORIGINAL_PIXEL_DRIFT")
        restored = unary_union(output)
        # An inclusive excluded pixel occupies one full grid cell beyond a
        # polygon endpoint. One cell has a sqrt(2)-pixel diagonal; use that
        # explicit Euclidean bound, never an unbounded geometric adjustment.
        grid_limit = policy["teacher_boundary_drift_pixels"] + int(bool(cleanup_iterations))
        tolerance = grid_limit * math.sqrt(2)
        outside_sliver = restored.difference(geometry.buffer(tolerance)).area
        missing_core_sliver = geometry.buffer(-tolerance).difference(restored).area
        # Fractional polygon intersections can leave subpixel slivers at a
        # pixel-cell-diagonal bound. Permit at most one square pixel total,
        # independently of unchanged mask-IoU and zero-occluder-fill checks.
        area_tolerance = max(1e-6, geometry.area * 1e-8,
                             policy["teacher_boundary_sliver_area_pixels"])
        if outside_sliver + missing_core_sliver > area_tolerance:
            raise ValueError("TEACHER_CANONICAL_BOUNDARY_DRIFT")
        metadata = dict(metrics, method="canonical original-grid encoding of projected visible surface",
                        pixel_iou=pixel_iou,
                        geometric_symmetric_difference_area=restored.symmetric_difference(geometry).area,
                        excluded_hole_pixels=int(np.count_nonzero(excluded_mask)),
                        source_geometry_pixels_removed_by_explicit_exclusion=geometry_exclusion_overlap_pixels,
                        filled_exclusion_pixels=0, boundary_drift_limit_pixels=tolerance,
                        boundary_drift_grid_cells=grid_limit, exclusion_cleanup_iterations=cleanup_iterations,
                        boundary_outside_sliver_area=outside_sliver,
                        boundary_missing_core_sliver_area=missing_core_sliver,
                        boundary_sliver_area_limit=area_tolerance,
                        cleanup_removed_source_pixels=int(np.count_nonzero((source_mask != 0) & (working_mask == 0))),
                        lost_source_pixels=int(np.count_nonzero((source_mask != 0) & (rendered == 0))),
                        added_pixels=int(np.count_nonzero((source_mask == 0) & (rendered != 0))),
                        existing_label_overlap_pixels=int(np.count_nonzero((source_mask == 0) & (rendered != 0) & (allowed_overlap_mask != 0))))
    return (output, pixel_iou, metadata) if return_metadata else (output, pixel_iou)


def _contour_parts(mask, crop, policy):
    """Vectorize the submitted pixel interpretation, then verify original pixels.

    Nearest-neighbor 2x contours avoid zero-width one-pixel boundaries. The
    source mask and acceptance comparison both stay on the original pixel grid.
    make_valid operates only on derived digital contours, never raw coordinates.
    """
    pixels = int(np.count_nonzero(mask))
    if not pixels:
        raise ValueError("EMPTY_RENDERED_SHAPE")
    external, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, external, -1, 255, cv2.FILLED)
    holes = int(np.count_nonzero((filled != 0) & (mask == 0)))
    preserve_holes = policy.get("allow_raster_hole_decomposition", False)
    if not preserve_holes and holes > max(policy["max_hole_pixels"], pixels * policy["max_hole_fraction"]):
        raise ValueError("SIGNIFICANT_RASTER_HOLES")
    scaled = cv2.resize(mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    contours, hierarchy = cv2.findContours(scaled, cv2.RETR_CCOMP if preserve_holes else cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
    left, top, _, _ = crop
    parts = []
    for index, contour in enumerate(contours):
        if preserve_holes and hierarchy[0][index][3] >= 0:
            continue
        coordinates = [(float(p[0][0]) / 2 + left, float(p[0][1]) / 2 + top) for p in contour]
        if len(set(coordinates)) < 3:
            continue
        rings = []
        if preserve_holes:
            child = int(hierarchy[0][index][2])
            while child >= 0:
                ring = [(float(p[0][0]) / 2 + left, float(p[0][1]) / 2 + top) for p in contours[child]]
                if len(set(ring)) >= 3:
                    rings.append(ring)
                child = int(hierarchy[0][child][0])
        geometry = Polygon(coordinates, rings)
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        candidates = _hole_free_parts(geometry) if preserve_holes else _parts(geometry)
        parts.extend(poly for poly in candidates
                     if poly.is_valid and poly.area > 1e-8 and not poly.interiors)
    if not parts:
        raise ValueError("RASTER_COMPONENTS_UNUSABLE")
    candidate_count = len(parts)
    if len(parts) > policy["max_components"]:
        parts = sorted(parts, key=lambda polygon: polygon.area, reverse=True)[:policy["max_components"]]
    restored = _render_parts(parts, crop)
    overlap = _pixel_iou(mask, restored)
    if overlap < policy["min_pixel_iou"]:
        raise ValueError("RASTER_VECTOR_PIXEL_DRIFT")
    lost = int(np.count_nonzero((mask != 0) & (restored == 0)))
    added = int(np.count_nonzero((mask == 0) & (restored != 0)))
    return parts, {"pixel_iou": overlap, "source_pixels": pixels,
                   "output_pixels": int(np.count_nonzero(restored)),
                   "lost_source_pixels": lost, "added_pixels": added,
                   "candidate_component_count": candidate_count,
                   "discarded_component_count": candidate_count - len(parts),
                   "source_hole_pixels": holes,
                   "discarded_hole_pixels": 0 if preserve_holes else holes,
                   "semantic_intent_verified": False,
                   "method": "canonical encoding of original-resolution submitted raster, including holes; original-grid verification"
                   if preserve_holes else "original-resolution Pillow raster; 2x nearest contours; original-grid verification"}


def _repair_shape(shape, size, policy):
    geometry, _, error = _semantic._shape_geometry(shape, size)
    if not isinstance(shape, dict):
        return None, None, "SHAPE_NOT_OBJECT"
    kind = shape.get("shape_type") or "polygon"
    points = shape.get("points")
    if (not isinstance(kind, str) or kind not in {"polygon", "rectangle"}
            or not isinstance(points, list) or not all(
                isinstance(point, (list, tuple)) and len(point) == 2
                and all(_semantic._number(value) for value in point) for point in points)):
        return None, None, "UNUSABLE_SHAPE_COORDINATES_OR_TYPE"
    if (kind == "polygon" and len(set(map(tuple, points))) < 3
            or kind == "rectangle" and len(points) != 2):
        return None, None, "UNUSABLE_SHAPE_POINT_COUNT"
    points = [tuple(point) for point in points]
    overshoot = max(max(0, -x, x - size[0], -y, y - size[1]) for x, y in points)
    if overshoot > policy["max_clip_pixels"]:
        return None, None, "COORDINATE_CLIP_TOO_LARGE"
    clipped = [(max(0, min(size[0], x)), max(0, min(size[1], y))) for x, y in points]
    normalized = clipped
    if kind == "rectangle":
        (x1, y1), (x2, y2) = clipped
        normalized = [(min(x1, x2), min(y1, y2)), (max(x1, x2), max(y1, y2))]
    if error is None and normalized == points:
        return [deepcopy(shape)], {"original_valid": True, "changed": False}, None
    try:
        before, crop = _mask(points, kind, size)
        after, _ = _mask(normalized, kind, size, crop)
        clip_iou = _pixel_iou(before, after)
        if clip_iou < policy["min_pixel_iou"]:
            raise ValueError("COORDINATE_CLIP_PIXEL_DRIFT")
        tentative = deepcopy(shape)
        tentative["points"] = [list(point) for point in normalized]
        valid, _, _ = _semantic._shape_geometry(tentative, size)
        if valid is not None:
            return [tentative], {"original_valid": geometry is not None, "changed": True,
                                 "pixel_iou": clip_iou, "max_coordinate_clip_pixels": overshoot,
                                 "method": "bounded_coordinate_clip_or_rectangle_endpoint_reorder"}, None
        parts, metrics = _contour_parts(after, crop, policy)
        final_iou = _pixel_iou(before, _render_parts(parts, crop))
        if final_iou < policy["min_pixel_iou"]:
            raise ValueError("TOTAL_REPAIR_PIXEL_DRIFT")
        output = []
        for poly in parts:
            item = deepcopy(shape)
            item["shape_type"] = "polygon"
            item["points"] = [[x, y] for x, y in list(poly.exterior.coords)[:-1]]
            if _semantic._shape_geometry(item, size)[0] is None:
                raise ValueError("REPAIRED_COMPONENT_INVALID_OR_OUT_OF_FRAME")
            output.append(item)
        return output, dict(metrics, changed=True, original_valid=geometry is not None,
                            pixel_iou=final_iou, max_coordinate_clip_pixels=overshoot), None
    except (ValueError, TypeError, OverflowError) as exc:
        return None, None, str(exc)


def _teachers(regions, size, unresolved):
    result = []
    for ordinal, region in enumerate(regions or []):
        if not isinstance(region, dict):
            unresolved.append({"code": "INVALID_TEACHER_REGION", "region_index": ordinal})
            continue
        try:
            target, minimum, maximum, conflicts = _semantic._region(region, size)
        except Exception:
            unresolved.append({"code": "INVALID_TEACHER_REGION", "region_id": region.get("id", ordinal)})
            continue
        result.append({"id": region.get("id", ordinal), "label": _semantic._label(region["label"]),
                       "target": target, "region": region, "min_coverage": minimum,
                       "conflicts": conflicts, "max_conflict": maximum,
                       "auto": region.get("auto_transfer_ok") is True,
                       "full": region.get("role") == "complete_surface"
                       and region.get("full_surface_transfer_ok", True) is True})
    originally_approved = [teacher["auto"] for teacher in result]
    contradictory_indices = set()
    for index, first in enumerate(result):
        for second_index in range(index + 1, len(result)):
            second = result[second_index]
            if not originally_approved[index] or not originally_approved[second_index]:
                continue
            if second["label"] not in first["conflicts"] and first["label"] not in second["conflicts"]:
                continue
            overlap = first["target"].intersection(second["target"]).area
            if overlap / min(first["target"].area, second["target"].area) > .25:
                contradictory_indices.update((index, second_index))
                unresolved.append({"code": "CONTRADICTORY_TEACHER_REGIONS", "region_ids": [first["id"], second["id"]]})
    for index in contradictory_indices:
        result[index]["auto"] = False
    return result


def _support(item, teachers):
    expected, conflict, references = 0.0, 0.0, []
    for teacher in teachers:
        if not teacher["auto"]:
            continue
        coverage = item["geometry"].intersection(teacher["target"]).area / teacher["target"].area
        if teacher["label"] == item["label"]:
            expected = max(expected, coverage)
            references.append({"region_id": teacher["id"], "expected_roi_coverage": coverage})
        elif item["label"] in teacher["conflicts"]:
            conflict = max(conflict, coverage)
            references.append({"region_id": teacher["id"], "conflicting_roi_coverage": coverage})
    return {"expected": expected, "conflict": conflict, "score": expected - conflict,
            "regions": references}


def _union(items, label):
    return unary_union([item["geometry"] for item in items
                        if not item.get("removed") and item["label"] == label])


def repair_annotation(doc, size, regions, evidence, policy=None):
    """Return actual bounded repair drafts and machine-readable unresolved cases.

    Regions role=complete_surface and auto_transfer_ok=True (plus
    full_surface_transfer_ok when supplied) are the only restoration/relabel
    sources. A small diagnostic patch can disambiguate duplicate candidates but
    can never fabricate or relabel an entire wall surface.
    """
    limits = dict(DEFAULT_POLICY, **(policy or {}))
    actions, unresolved, coverage = [], [], []
    result = {"corrected_doc": None, "actions": actions, "unresolved": unresolved,
              "coverage": coverage, "changed": False, "classification": "unresolved",
              "label_origin": "automatic_repair_pseudo_label", "training_eligible": False,
              "quality_inputs": {}}
    if (not _semantic._size(size) or not isinstance(doc, dict)
            or not isinstance(doc.get("shapes"), list)):
        unresolved.append({"code": "DOCUMENT_OR_IMAGE_SIZE_UNUSABLE"})
        return result
    size = tuple(map(int, size))
    teachers = _teachers(regions, size, unresolved)
    items, original_valid_count = [], 0
    existing_groups = [shape.get("group_id") for shape in doc["shapes"] if isinstance(shape, dict)
                       and isinstance(shape.get("group_id"), int) and not isinstance(shape.get("group_id"), bool)]
    next_group_id = max(existing_groups, default=0) + 1
    for index, shape in enumerate(doc["shapes"]):
        fixed, metrics, error = _repair_shape(shape, size, limits)
        label = _semantic._label(shape.get("label")) if isinstance(shape, dict) else ""
        if error:
            unresolved.append({"code": error, "source_shape_index": index})
            items.append({"shape": deepcopy(shape), "source": index, "geometry": None,
                          "label": label, "original_valid": False})
            continue
        original_valid_count += int(metrics["original_valid"])
        for component, corrected in enumerate(fixed):
            geometry = _semantic._shape_geometry(corrected, size)[0]
            items.append({"shape": corrected, "source": index, "component": component,
                          "geometry": geometry, "label": label,
                          "original_valid": metrics["original_valid"]})
        if metrics["changed"]:
            actions.append({"action": "repair_geometry", "source_shape_index": index,
                            "component_count": len(fixed), "metrics": metrics})
        if not label:
            unresolved.append({"code": "EMPTY_OR_INVALID_LABEL", "source_shape_index": index})
        elif limits["allowed_labels"] is not None and label not in {
                _semantic._label(value) for value in limits["allowed_labels"]}:
            unresolved.append({"code": "UNMAPPED_LABEL", "source_shape_index": index, "label": label})
    # Remove an empty label only when it repeats a geometrically equivalent,
    # labeled shape with an explicitly gated teacher supporting that label.
    for empty in items:
        if empty["label"] or empty["geometry"] is None:
            continue
        for other in items:
            if (not other["label"] or other["geometry"] is None
                    or not empty["geometry"].equals(other["geometry"])):
                continue
            support = _support(other, teachers)
            if support["expected"] >= limits["semantic_support"] and support["score"] >= limits["semantic_margin"]:
                empty["removed"] = True
                actions.append({"action": "remove_empty_label_equivalent", "source_shape_index": empty["source"],
                                "kept_source_shape_index": other["source"], "teacher_support": support})
                unresolved[:] = [issue for issue in unresolved if not (
                    issue.get("source_shape_index") == empty["source"] and issue["code"] == "EMPTY_OR_INVALID_LABEL")]
                break
    valid_items = [item for item in items if item["geometry"] is not None and item["label"] and not item.get("removed")]
    if valid_items:
        tree = STRtree([item["geometry"] for item in valid_items])
        for i, first in enumerate(valid_items):
            if first.get("removed"):
                continue
            for value in tree.query(first["geometry"]):
                j = int(value)
                if j <= i:
                    continue
                second = valid_items[j]
                if first.get("removed") or second.get("removed"):
                    continue
                overlap = first["geometry"].intersection(second["geometry"]).area
                total = first["geometry"].area + second["geometry"].area - overlap
                iou = overlap / total if total else 0
                if iou < limits["duplicate_iou"]:
                    continue
                first_support, second_support = _support(first, teachers), _support(second, teachers)
                same_label = first["label"] == second["label"]
                winner = None
                if same_label:
                    first_metadata = _semantic._metadata_key(first["shape"])
                    attributes_equal = first_metadata is not None and first_metadata == _semantic._metadata_key(second["shape"])
                    if attributes_equal and first["geometry"].equals(second["geometry"]):
                        winner = first
                    elif (first["original_valid"] != second["original_valid"]
                          and .2 >= limits["same_label_margin"]):
                        winner = first if first["original_valid"] else second
                    elif abs(first_support["score"] - second_support["score"]) >= limits["same_label_margin"]:
                        preferred = first if first_support["score"] > second_support["score"] else second
                        preferred_support = first_support if preferred is first else second_support
                        if preferred_support["expected"] >= limits["semantic_support"]:
                            winner = preferred
                else:
                    preferred = first if first_support["score"] > second_support["score"] else second
                    good = first_support if preferred is first else second_support
                    bad = second_support if preferred is first else first_support
                    if (good["expected"] >= limits["semantic_support"]
                            and bad["conflict"] >= limits["semantic_support"]
                            and good["score"] - bad["score"] >= limits["semantic_margin"]):
                        winner = preferred
                if winner is None:
                    unresolved.append({"code": "DUPLICATE_SELECTION_UNCERTAIN", "source_shape_indices":
                                       [first["source"], second["source"]], "labels": [first["label"], second["label"]],
                                       "iou": iou, "teacher_support": [first_support, second_support]})
                    continue
                loser = second if winner is first else first
                loser["removed"] = True
                actions.append({"action": "remove_duplicate", "kept_source_shape_index": winner["source"],
                                "removed_source_shape_index": loser["source"], "kept_label": winner["label"],
                                "removed_label": loser["label"], "iou": iou,
                                "reason": "same_label_geometric_or_validity_evidence" if same_label else "gated_teacher_disambiguated_labels",
                                "teacher_support": [first_support, second_support]})
    # Relabel only within a gated complete surface, never by extrapolating a patch.
    for item in valid_items:
        if item.get("removed"):
            continue
        candidates = []
        for teacher in teachers:
            if (not teacher["auto"] or not teacher["full"]
                    or item["label"] not in teacher["conflicts"]):
                continue
            fraction = item["geometry"].intersection(teacher["target"]).area / item["geometry"].area
            if fraction > limits["relabel_shape_coverage"]:
                candidates.append((teacher, fraction))
        if len({teacher["label"] for teacher, _ in candidates}) == 1:
            teacher, fraction = max(candidates, key=lambda pair: pair[1])
            old = item["label"]
            item["label"] = teacher["label"]
            item["shape"]["label"] = teacher["label"]
            actions.append({"action": "relabel_full_surface_supported_shape", "source_shape_index": item["source"],
                            "old_label": old, "new_label": item["label"], "region_id": teacher["id"],
                            "shape_coverage": fraction, "teacher_evidence": evidence})
        elif candidates:
            unresolved.append({"code": "CONTRADICTORY_FULL_SURFACE_TEACHERS", "source_shape_index": item["source"]})
    # A source shape with unusable expected or unknown geometry prevents absence
    # inference. Do not paint over an unmeasurable annotation.
    for teacher in teachers:
        target, label = teacher["target"], teacher["label"]
        existing = _union([item for item in items if item["geometry"] is not None], label)
        before = existing.intersection(target).area / target.area
        record = {"region_id": teacher["id"], "label": label, "role": teacher["region"].get("role", "patch"),
                  "coverage_before_restore": before, "min_coverage": teacher["min_coverage"],
                  "auto_transfer_ok": teacher["auto"], "full_surface_transfer_ok": teacher["full"]}
        coverage.append(record)
        threshold = teacher["min_coverage"] if "min_coverage" in teacher["region"] else limits["restore_min_coverage"]
        if before < threshold:
            unusable = any(not item.get("removed") and item["geometry"] is None
                           and (item["label"] == label or not item["label"]) for item in items)
            if (not limits["restore_missing_surfaces"] or not teacher["auto"]
                    or not teacher["full"] or unusable):
                unresolved.append({"code": "MISSING_LABEL_NOT_AUTOMATICALLY_RESTORABLE", "region_id": teacher["id"],
                                   "label": label, "reason": "unusable_source_geometry" if unusable else "complete_surface_or_transfer_gate_missing"})
            else:
                missing = target.difference(existing)
                try:
                    polygons, restoration_pixel_iou, restoration_metrics = _known_surface_components(
                        missing, size, limits, return_metadata=True,
                        allowed_existing_geometry=existing,
                        strict_exclusions=teacher["region"].get("exclusions", []))
                except (ValueError, TypeError) as exc:
                    unresolved.append({"code": str(exc), "region_id": teacher["id"]})
                    polygons = []
                if polygons:
                    added = []
                    for poly in polygons:
                        shape = {"label": label, "points": [[x, y] for x, y in list(poly.exterior.coords)[:-1]],
                                 "shape_type": "polygon", "group_id": next_group_id, "flags": {"qa_pseudo_label": True}}
                        if _semantic._shape_geometry(shape, size)[0] is None:
                            unresolved.append({"code": "PROJECTED_SURFACE_OUT_OF_FRAME_OR_INVALID", "region_id": teacher["id"]})
                            added = []
                            break
                        added.append({"shape": shape, "source": None, "geometry": poly,
                                      "label": label, "original_valid": False})
                    if added:
                        items.extend(added)
                        actions.append({"action": "restore_missing_complete_surface", "region_id": teacher["id"],
                                        "label": label, "component_count": len(added), "teacher_evidence": evidence,
                                        "group_id": next_group_id,
                                        "restoration_pixel_iou": restoration_pixel_iou,
                                        "restoration_metrics": restoration_metrics,
                                        "label_origin": "projected_complete_surface_pseudo_label"})
                        next_group_id += 1
        after = _union([item for item in items if item["geometry"] is not None], label).intersection(target).area / target.area
        record["coverage_after"] = after
        record["conflict_coverage_after"] = {}
        for conflict in teacher["conflicts"]:
            conflict_area = _union([item for item in items if item["geometry"] is not None], conflict).intersection(target).area / target.area
            record["conflict_coverage_after"][conflict] = conflict_area
            if conflict_area > teacher["max_conflict"]:
                unresolved.append({"code": "RESIDUAL_CONFLICTING_LABEL_COVERAGE", "region_id": teacher["id"],
                                   "expected_label": label, "conflicting_label": conflict,
                                   "coverage": conflict_area})
        if after < threshold and not any(issue.get("region_id") == teacher["id"] for issue in unresolved):
            unresolved.append({"code": "RESIDUAL_LOW_LABEL_COVERAGE", "region_id": teacher["id"], "label": label})
    # Later complete-surface restoration can also resolve an earlier diagnostic
    # patch. Evaluate final masks once all teachers have finished; retain source
    # geometry and label uncertainties independently of these ROI measurements.
    missing_codes = {"MISSING_LABEL_NOT_AUTOMATICALLY_RESTORABLE", "RESIDUAL_LOW_LABEL_COVERAGE"}
    for teacher, record in zip(teachers, coverage):
        target, label = teacher["target"], teacher["label"]
        final_coverage = _union([item for item in items if item["geometry"] is not None], label).intersection(target).area / target.area
        threshold = teacher["min_coverage"] if "min_coverage" in teacher["region"] else limits["restore_min_coverage"]
        record["coverage_after"] = final_coverage
        if final_coverage >= threshold:
            unresolved[:] = [issue for issue in unresolved if not (
                issue.get("region_id") == teacher["id"] and issue["code"] in missing_codes)]
        elif not any(issue.get("region_id") == teacher["id"] and issue["code"] in missing_codes for issue in unresolved):
            unresolved.append({"code": "RESIDUAL_LOW_LABEL_COVERAGE", "region_id": teacher["id"], "label": label})
        for conflict in teacher["conflicts"]:
            final_conflict = _union([item for item in items if item["geometry"] is not None], conflict).intersection(target).area / target.area
            record["conflict_coverage_after"][conflict] = final_conflict
            matching = lambda issue: (issue.get("region_id") == teacher["id"]
                                      and issue["code"] == "RESIDUAL_CONFLICTING_LABEL_COVERAGE"
                                      and issue.get("conflicting_label") == conflict)
            if final_conflict <= teacher["max_conflict"]:
                unresolved[:] = [issue for issue in unresolved if not matching(issue)]
            elif not any(matching(issue) for issue in unresolved):
                unresolved.append({"code": "RESIDUAL_CONFLICTING_LABEL_COVERAGE", "region_id": teacher["id"],
                                   "expected_label": label, "conflicting_label": conflict, "coverage": final_conflict})
            else:
                for issue in unresolved:
                    if matching(issue):
                        issue["coverage"] = final_conflict
    remaining_items = [item for item in items if not item.get("removed")]
    # Unknown labels remain visible to the classifier even if their geometry was
    # repaired. They are never silently reclassified from coordinate overlap.
    for item in remaining_items:
        if not item["label"] and not any(issue.get("source_shape_index") == item["source"]
                                        and issue["code"] == "EMPTY_OR_INVALID_LABEL" for issue in unresolved):
            unresolved.append({"code": "EMPTY_OR_INVALID_LABEL", "source_shape_index": item["source"]})
    result["changed"] = bool(actions)
    if actions:
        corrected = deepcopy(doc)
        corrected["shapes"] = [item["shape"] for item in remaining_items]
        result["corrected_doc"] = corrected
    result["classification"] = ("partially_repaired" if actions and unresolved else "automatically_repaired"
                                if actions else "unresolved" if unresolved else "no_change_needed")
    result["quality_inputs"] = {"source_shape_count": len(doc["shapes"]), "output_shape_count": len(remaining_items),
                                "original_valid_geometry_count": original_valid_count,
                                "action_count": len(actions), "unresolved_count": len(unresolved),
                                "automatic_teacher_count": sum(teacher["auto"] for teacher in teachers),
                                "complete_surface_teacher_count": sum(teacher["auto"] and teacher["full"] for teacher in teachers),
                                "unmapped_labels": sorted({issue["label"] for issue in unresolved if issue["code"] == "UNMAPPED_LABEL"}),
                                "semantic_assessment": "gated_teacher_rules" if any(teacher["auto"] for teacher in teachers) else "unassessed"}
    return result
