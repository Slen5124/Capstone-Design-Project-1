"""Conservative automatic *draft* transfer of same-plane teacher regions.

This module combines qa_v2.registration's independent per-plane SIFT/RANSAC
geometry with a brightness-compensated, tiled RGB/gradient comparison. It never
uses a whole-image homography to combine distinct surfaces. A larger reviewed
same-plane ``registration_points`` anchor can register a smooth visible patch.
Only explicit ``role='complete_surface'`` regions permit a full-surface draft;
patch and diagnostic_patch regions authorize only their own local polygon.

The appearance limits are initial engineering gates, not measured accuracy or
proof of unchanged construction/visibility. Similar-looking occluders, repeated
textures, and illumination/paint changes may remain indistinguishable. Successful
output is still a pseudo-label draft, ``training_eligible=False``. No original
image or annotation is saved, modified, relabeled, or copied by this module.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

# Keep this module runnable from its own directory and from the v3 test runner.
_V2 = Path(__file__).resolve().parent.parent / "qa_v2"
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
from registration import project_regions


MIN_STRONG_INLIERS = 24
MIN_STRONG_INLIER_RATIO = 0.80
MIN_STRONG_SPREAD = 0.30
MAX_BAD_AREA_FRACTION = 0.10
MIN_EVALUATED_AREA_FRACTION = 0.90
MIN_WARP_VALID_FRACTION = 0.98


def homography_original(metrics: dict) -> np.ndarray:
    """Convert resized-reference -> resized-target H to original pixel H.

    H_original = inverse(S_target) @ H_resized @ S_reference, where the
    diagonal matrices contain the actual x/y resize factors used by qa_v2.
    This preserves noninteger image dimensions and rounded resize dimensions.
    """
    homography = np.asarray(metrics["homography_resized"], dtype=np.float64)
    reference_scale = np.asarray(metrics["reference_resize_xy"], dtype=np.float64)
    target_scale = np.asarray(metrics["target_resize_xy"], dtype=np.float64)
    if (homography.shape != (3, 3) or reference_scale.shape != (2,)
            or target_scale.shape != (2,) or not np.isfinite(homography).all()
            or not np.isfinite(reference_scale).all() or not np.isfinite(target_scale).all()
            or min(*reference_scale, *target_scale) <= 0):
        raise ValueError("invalid_registration_scale_or_homography")
    source_matrix = np.diag([*reference_scale, 1.0])
    target_matrix = np.diag([*target_scale, 1.0])
    return np.linalg.inv(target_matrix) @ homography @ source_matrix


def _rgb_small(image: Image.Image, scale: np.ndarray) -> np.ndarray:
    size = (round(image.width * scale[0]), round(image.height * scale[1]))
    if min(size) <= 0:
        raise ValueError("invalid_resized_image_dimensions")
    return np.asarray(image.convert("RGB").resize(size, Image.Resampling.LANCZOS), dtype=np.float32)


def _mask(shape: tuple[int, int], points: list, exclusions: list, scale: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    polygon = np.rint(np.asarray(points, dtype=np.float64) * scale).astype(np.int32)
    cv2.fillPoly(mask, [polygon], 1)
    for excluded in exclusions:
        polygon = np.rint(np.asarray(excluded, dtype=np.float64) * scale).astype(np.int32)
        cv2.fillPoly(mask, [polygon], 0)
    # Avoid anti-aliased polygon borders and descriptor/interpolation boundaries.
    return cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1).astype(bool)


def _gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _gradients(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return (cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3, scale=1 / 8),
            cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3, scale=1 / 8))


def _gradient_correlation(first: tuple, second: tuple, mask: np.ndarray) -> tuple[float, float, float]:
    source = np.column_stack([channel[mask] for channel in first]).reshape(-1)
    target = np.column_stack([channel[mask] for channel in second]).reshape(-1)
    source_norm = float(np.linalg.norm(source))
    target_norm = float(np.linalg.norm(target))
    energy_source = float(np.sqrt(np.mean(source ** 2)))
    energy_target = float(np.sqrt(np.mean(target ** 2)))
    correlation = float(np.dot(source, target) / max(source_norm * target_norm, 1e-9))
    return correlation, energy_source, energy_target


def _smooth_illumination(source_gray, compensated_gray, anchor, roi):
    """Fit a bounded, smooth scalar lighting field from stable anchor tiles.

    Fit support requires matching gradients or genuinely flat, stable texture.
    A local per-tile free brightness adjustment could explain away occluders;
    this uses one robust affine lighting field for the SAME surface plane.
    RGB comparisons remain scalar-adjusted, preserving color-change evidence.
    """
    height, width = source_gray.shape
    source_gradients = _gradients(source_gray)
    target_gradients = _gradients(compensated_gray)
    ys, xs = np.nonzero(anchor)
    if not len(xs):
        return np.zeros_like(source_gray), {"applied": False, "support_tiles": 0}
    xedges = np.rint(np.linspace(xs.min(), xs.max() + 1, 7)).astype(int)
    yedges = np.rint(np.linspace(ys.min(), ys.max() + 1, 7)).astype(int)
    supports = []
    signed_residual = source_gray - compensated_gray
    for y0, y1 in zip(yedges[:-1], yedges[1:]):
        for x0, x1 in zip(xedges[:-1], xedges[1:]):
            local = anchor[y0:y1, x0:x1]
            if local.sum() < max(16, local.size * .35):
                continue
            first = tuple(channel[y0:y1, x0:x1] for channel in source_gradients)
            second = tuple(channel[y0:y1, x0:x1] for channel in target_gradients)
            correlation, source_energy, target_energy = _gradient_correlation(first, second, local)
            residual = signed_residual[y0:y1, x0:x1][local]
            median = float(np.median(residual))
            spread = float(np.percentile(residual, 90) - np.percentile(residual, 10))
            stable = (source_energy >= 2 and correlation >= .85 and spread <= 24
                      or source_energy < 2 and target_energy < 3.5 and spread <= 8)
            if stable and abs(median) <= 25:
                yy, xx = np.nonzero(local)
                supports.append([(float(xx.mean()) + x0) / width,
                                 (float(yy.mean()) + y0) / height, median])
    if len(supports) < 6:
        return np.zeros_like(source_gray), {"applied": False, "support_tiles": len(supports)}
    supports = np.asarray(supports)
    design = np.column_stack([np.ones(len(supports)), supports[:, :2]])
    retained = np.ones(len(supports), dtype=bool)
    coefficients = np.zeros(3)
    for _ in range(5):
        if retained.sum() < 6 or np.linalg.matrix_rank(design[retained]) < 3:
            return np.zeros_like(source_gray), {"applied": False, "support_tiles": len(supports), "reason": "lighting_fit_not_distributed"}
        coefficients = np.linalg.lstsq(design[retained], supports[retained, 2], rcond=None)[0]
        errors = design @ coefficients - supports[:, 2]
        median_error = np.median(errors[retained])
        mad = np.median(np.abs(errors[retained] - median_error))
        next_retained = np.abs(errors - median_error) <= max(3.0, 2.5 * 1.4826 * mad)
        if np.array_equal(next_retained, retained):
            break
        retained = next_retained
    yy, xx = np.indices(source_gray.shape, dtype=np.float32)
    field = (coefficients[0] + coefficients[1] * xx / width + coefficients[2] * yy / height).astype(np.float32)
    maximum = float(np.max(np.abs(field[roi])))
    if maximum > 20:
        return np.zeros_like(source_gray), {"applied": False, "support_tiles": len(supports),
                                           "reason": "lighting_field_exceeds_bounded_range", "proposed_maximum_offset": maximum}
    return field, {"applied": True, "support_tiles": len(supports), "fit_inlier_tiles": int(retained.sum()),
                   "coefficients_normalized_reference_xy": coefficients.tolist(), "maximum_offset": maximum,
                   "model": "robust_same_plane_affine_illumination_only"}


def _photometric_fit(source_gray, target_gray, anchor):
    """Estimate exposure on gradient-consistent tiles, excluding occluders.

    Whole-ROI percentiles are badly biased when a large black foreground object
    appears. Stable local gradient structure provides fit support without using
    a freely adjustable brightness transform for each candidate transfer tile.
    """
    ys, xs = np.nonzero(anchor)
    source_gradients, target_gradients = _gradients(source_gray), _gradients(target_gray)
    xedges = np.rint(np.linspace(xs.min(), xs.max() + 1, 7)).astype(int)
    yedges = np.rint(np.linspace(ys.min(), ys.max() + 1, 7)).astype(int)
    supports, gains = [], []
    for y0, y1 in zip(yedges[:-1], yedges[1:]):
        for x0, x1 in zip(xedges[:-1], xedges[1:]):
            local = anchor[y0:y1, x0:x1]
            if local.sum() < max(16, local.size * .35):
                continue
            first = tuple(channel[y0:y1, x0:x1] for channel in source_gradients)
            second = tuple(channel[y0:y1, x0:x1] for channel in target_gradients)
            correlation, source_energy, target_energy = _gradient_correlation(first, second, local)
            source_values = source_gray[y0:y1, x0:x1][local]
            target_values = target_gray[y0:y1, x0:x1][local]
            source_levels = np.percentile(source_values, [10, 50, 90])
            target_levels = np.percentile(target_values, [10, 50, 90])
            source_range = source_levels[2] - source_levels[0]
            target_range = target_levels[2] - target_levels[0]
            if source_energy >= 2 and correlation >= .85:
                supports.append((source_levels[1], target_levels[1]))
                if min(source_range, target_range) >= 12:
                    gains.append(float(source_range / target_range))
            elif source_energy < 2 and target_energy < 3.5 and abs(source_levels[1] - target_levels[1]) <= 45:
                supports.append((source_levels[1], target_levels[1]))
    if len(supports) >= 6:
        gain = float(np.median(gains)) if gains else 1.0
        offset = float(np.median([source - gain * target for source, target in supports]))
        return gain, offset, {"method": "gradient_consistent_anchor_tile_medians", "support_tiles": len(supports), "gain_support_tiles": len(gains)}
    source_levels = np.percentile(source_gray[anchor], [10, 90])
    target_levels = np.percentile(target_gray[anchor], [10, 90])
    source_range, target_range = source_levels[1] - source_levels[0], target_levels[1] - target_levels[0]
    gain = float(source_range / target_range) if min(source_range, target_range) >= 12 else 1.0
    offset = float(np.median(source_gray[anchor] - gain * target_gray[anchor]))
    return gain, offset, {"method": "fallback_anchor_percentiles", "support_tiles": len(supports)}


def _appearance(reference_rgb: Image.Image, target_rgb: Image.Image, region: dict, metrics: dict) -> dict:
    reference_scale = np.asarray(metrics["reference_resize_xy"], dtype=np.float64)
    target_scale = np.asarray(metrics["target_resize_xy"], dtype=np.float64)
    homography = np.asarray(metrics["homography_resized"], dtype=np.float64)
    reference = _rgb_small(reference_rgb, reference_scale)
    target = _rgb_small(target_rgb, target_scale)
    reference_height, reference_width = reference.shape[:2]
    inverse = np.linalg.inv(homography)
    aligned = cv2.warpPerspective(target, inverse, (reference_width, reference_height), flags=cv2.INTER_LINEAR)
    target_valid = cv2.warpPerspective(
        np.ones(target.shape[:2], np.uint8), inverse, (reference_width, reference_height), flags=cv2.INTER_NEAREST,
    ).astype(bool)
    exclusions = region.get("exclusions", [])
    roi = _mask(reference.shape[:2], region["points"], exclusions, reference_scale)
    anchor = _mask(reference.shape[:2], region.get("registration_points", region["points"]), exclusions, reference_scale)
    if roi.sum() < 256 or anchor.sum() < 256:
        return {"passed": False, "reasons": ["appearance_region_too_small"], "tiles": []}
    valid_fraction = float((roi & target_valid).sum() / roi.sum())
    fit_mask = anchor & target_valid
    if fit_mask.sum() < 256 or valid_fraction < MIN_WARP_VALID_FRACTION:
        return {"passed": False, "reasons": ["appearance_warp_incomplete"], "warp_valid_fraction": valid_fraction, "tiles": []}
    reference_blur = cv2.GaussianBlur(reference, (0, 0), 1.0)
    aligned_blur = cv2.GaussianBlur(aligned, (0, 0), 1.0)
    source_gray, target_gray = _gray(reference_blur), _gray(aligned_blur)
    gain, offset, fit_evidence = _photometric_fit(source_gray, target_gray, fit_mask)
    reasons = []
    if not 0.75 <= gain <= 1.33 or abs(offset) > 45:
        reasons.append("appearance_extreme_photometric_change")
    compensated_rgb = gain * aligned_blur + offset
    compensated_gray = _gray(compensated_rgb)
    lighting, lighting_evidence = _smooth_illumination(source_gray, compensated_gray, fit_mask, roi & target_valid)
    compensated_rgb += lighting[:, :, None]
    compensated_gray = _gray(compensated_rgb)
    rgb_error = np.max(np.abs(reference_blur - compensated_rgb), axis=2)
    gray_error = np.abs(source_gray - compensated_gray)
    source_gradients, target_gradients = _gradients(source_gray), _gradients(compensated_gray)
    effective_roi = roi & target_valid
    ys, xs = np.nonzero(roi)
    columns = min(6, max(2, int((xs.max() - xs.min() + 1) // 6)))
    rows = min(6, max(2, int((ys.max() - ys.min() + 1) // 6)))
    xedges = np.rint(np.linspace(xs.min(), xs.max() + 1, columns + 1)).astype(int)
    yedges = np.rint(np.linspace(ys.min(), ys.max() + 1, rows + 1)).astype(int)
    tiles, tested_pixels, bad_pixels, textured_pixels = [], 0, 0, 0
    for y0, y1 in zip(yedges[:-1], yedges[1:]):
        for x0, x1 in zip(xedges[:-1], xedges[1:]):
            local = effective_roi[y0:y1, x0:x1]
            area = int(local.sum())
            if area < max(8, int(local.size * 0.35)):
                continue
            source_tile_gradients = tuple(channel[y0:y1, x0:x1] for channel in source_gradients)
            target_tile_gradients = tuple(channel[y0:y1, x0:x1] for channel in target_gradients)
            correlation, source_energy, target_energy = _gradient_correlation(source_tile_gradients, target_tile_gradients, local)
            local_gray_error = gray_error[y0:y1, x0:x1][local]
            local_rgb_error = rgb_error[y0:y1, x0:x1][local]
            median_gray = float(np.median(local_gray_error))
            p90_gray = float(np.percentile(local_gray_error, 90))
            median_rgb = float(np.median(local_rgb_error))
            p90_rgb = float(np.percentile(local_rgb_error, 90))
            is_textured = source_energy >= 2.0
            if is_textured:
                passed = correlation >= 0.70 and median_gray <= 8 and p90_gray <= 20 and p90_rgb <= 24
                textured_pixels += area
            else:
                # Flat unchanged wall consistency is weaker than texture
                # correspondence and remains explicitly identified in evidence.
                passed = median_gray <= 4 and p90_gray <= 10 and median_rgb <= 7 and p90_rgb <= 14 and target_energy <= 3.5
            tiles.append({"bounds_resized_reference": [int(x0), int(y0), int(x1), int(y1)],
                          "pixels": area, "passed": bool(passed),
                          "comparison": "gradient_texture" if is_textured else "flat_surface_consistency",
                          "gradient_correlation": correlation, "source_gradient_energy": source_energy,
                          "target_gradient_energy": target_energy,
                          "gray_residual_median": median_gray, "gray_residual_p90": p90_gray,
                          "rgb_residual_median": median_rgb, "rgb_residual_p90": p90_rgb})
            tested_pixels += area
            if not passed:
                bad_pixels += area
    evaluated_fraction = tested_pixels / max(int(roi.sum()), 1)
    bad_fraction = bad_pixels / max(tested_pixels, 1)
    global_rgb_p90 = float(np.percentile(rgb_error[effective_roi], 90))
    global_gray_p90 = float(np.percentile(gray_error[effective_roi], 90))
    if len(tiles) < min(12, columns * rows) or evaluated_fraction < MIN_EVALUATED_AREA_FRACTION:
        reasons.append("appearance_insufficient_distributed_tiles")
    if bad_fraction > MAX_BAD_AREA_FRACTION:
        reasons.append("appearance_changed_or_occluded_area")
    if global_rgb_p90 > 24 or global_gray_p90 > 20:
        reasons.append("appearance_global_residual_too_large")
    return {"passed": not reasons, "reasons": reasons, "tiles": tiles,
            "photometric_gain": float(gain), "photometric_offset": offset,
            "photometric_fit": fit_evidence,
            "smooth_illumination": lighting_evidence,
            "warp_valid_fraction": valid_fraction, "evaluated_area_fraction": float(evaluated_fraction),
            "bad_area_fraction": float(bad_fraction), "textured_area_fraction": float(textured_pixels / max(tested_pixels, 1)),
            "rgb_residual_p90": global_rgb_p90, "gray_residual_p90": global_gray_p90,
            "appearance_error_score": float(bad_fraction + global_rgb_p90 / 24),
            "limits_are_initial_engineering_gates": True, "visibility_verified": False}


def _polygon_parts(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [part for child in geometry.geoms for part in _polygon_parts(child)]
    return []


def _project_points(points, homography):
    points = np.asarray(points, dtype=np.float64)
    homogeneous = np.column_stack([points, np.ones(len(points))]) @ homography.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-8):
        raise ValueError("subregion_projection_crosses_horizon")
    return (homogeneous[:, :2] / homogeneous[:, 2, None]).tolist()


def _allowed_subregions(source, projected, metrics, appearance, target_size):
    """Offer only matched tile interiors; never authorize the failed full ROI.

    Each result is a bounded visible segment of the source complete-surface
    teacher. Failed/unexamined tiles and excluded surfaces stay outside it.
    The caller must report partial coverage and retain the parent failure.
    """
    if (source.get("role") != "complete_surface" or projected.get("auto_transfer_ok")
            or appearance.get("evaluated_area_fraction", 0) < MIN_EVALUATED_AREA_FRACTION
            or appearance.get("warp_valid_fraction", 0) < MIN_WARP_VALID_FRACTION
            or "appearance_extreme_photometric_change" in appearance.get("reasons", [])):
        return []
    source_geometry = Polygon(source["points"])
    for points in source.get("exclusions", []):
        source_geometry = source_geometry.difference(Polygon(points))
    reference_scale = np.asarray(metrics["reference_resize_xy"], dtype=np.float64)
    inset = 1.0 / float(min(reference_scale))
    # The appearance mask is eroded on its resized grid. Keep transfer geometry
    # inside the same conservative visible boundary on the original pixel grid.
    source_geometry = source_geometry.buffer(-inset)
    homography = homography_original(metrics)
    target_frame = box(0, 0, *target_size)
    subregions, good_tiles, tile_rectangles = [], [], []
    for tile in appearance.get("tiles", []):
        if not tile.get("passed"): continue
        x0, y0, x1, y1 = tile["bounds_resized_reference"]
        tile_rectangles.append(box(x0 / reference_scale[0], y0 / reference_scale[1],
                                   x1 / reference_scale[0], y1 / reference_scale[1]))
        good_tiles.append(tile)
    if not tile_rectangles: return []
    # Neighboring accepted cells merge; failed cells remain holes or gaps.
    allowed_geometry = source_geometry.intersection(unary_union(tile_rectangles))
    error_score = float(sum(tile["pixels"] * tile["rgb_residual_p90"] for tile in good_tiles) / max(sum(tile["pixels"] for tile in good_tiles), 1) / 24)
    for component, polygon in enumerate(_polygon_parts(allowed_geometry)):
            if polygon.area <= 16:
                continue
            points = _project_points(list(polygon.exterior.coords)[:-1], homography)
            exclusions = [_project_points(list(ring.coords)[:-1], homography) for ring in polygon.interiors]
            target_geometry = Polygon(points, exclusions).intersection(target_frame)
            if target_geometry.is_empty or not target_geometry.is_valid:
                continue
            for target_component, target_polygon in enumerate(_polygon_parts(target_geometry)):
                if target_polygon.area <= 16:
                    continue
                region = copy.deepcopy(projected)
                region.update(
                    id=f"{source['id']}__matched_visible_{component}_{target_component}",
                    points=[list(point) for point in list(target_polygon.exterior.coords)[:-1]],
                    exclusions=[[list(point) for point in list(ring.coords)[:-1]] for ring in target_polygon.interiors],
                    auto_transfer_ok=True, full_surface_transfer_ok=True,
                    partial_surface_transfer=True, source_complete_surface_id=source["id"], parent_region_id=source["id"],
                    geometry_scope="partial_visible_surface", scope="partial_visible_surface",
                    transfer_evidence_parent_id=source["id"],
                    matched_tile_bounds_resized_reference=[tile["bounds_resized_reference"] for tile in good_tiles],
                    conservative_boundary_inset_original_px=inset,
                    parent_transfer_reasons=projected.get("transfer_reasons", []),
                    transfer_reasons=[], appearance_error_score=error_score,
                    training_eligible=False, needs_review=True,
                )
                subregions.append(region)
    return subregions


def transfer(reference_rgb: Image.Image, target_rgb: Image.Image, regions: list[dict], source_identity: bool = False) -> dict:
    """Return v2 registration plus per-region automatic pseudo-label permissions.

    ``auto_transfer_ok`` permits only that region's polygon draft. A full-surface
    mask additionally requires ``full_surface_transfer_ok`` and an explicit
    reviewed complete_surface role. Success never authorizes extending a patch,
    guessing unseen surfaces, or marking a draft as ground truth.
    """
    result = project_regions(reference_rgb, target_rgb, regions, source_identity)
    result["training_eligible"] = False
    result["label_origin"] = "automatic_teacher_transfer_draft"
    result["automatic_allowed_subregions"] = []
    original_by_id = {str(region.get("id", index)): region for index, region in enumerate(regions)}
    evidence_by_id = {str(item.get("id", index)): item for index, item in enumerate(result["evidence"])}
    for index, projected in enumerate(result["regions"]):
        region_id = str(projected.get("id", index))
        evidence = evidence_by_id[region_id]
        source = original_by_id[region_id]
        role = source.get("role", "diagnostic_patch")
        projected["role"] = role
        reasons = []
        if role not in {"complete_surface", "patch", "diagnostic_patch"}:
            reasons.append("unsupported_transfer_role")
        if source_identity:
            appearance = {"passed": True, "reasons": [], "basis": "caller_verified_identical_source_bytes", "appearance_error_score": 0.0}
            strong_geometry = True
        else:
            metrics = evidence.get("metrics", {})
            strong_geometry = (metrics.get("inliers", 0) >= MIN_STRONG_INLIERS
                               and metrics.get("inlier_ratio", 0) >= MIN_STRONG_INLIER_RATIO
                               and metrics.get("source_feature_hull_fraction", 0) >= MIN_STRONG_SPREAD
                               and metrics.get("target_feature_hull_fraction", 0) >= MIN_STRONG_SPREAD
                               and metrics.get("reprojection_median_px", float("inf")) <= 1.2
                               and metrics.get("reprojection_p95_px", float("inf")) <= 2.5)
            if not strong_geometry:
                reasons.append("registration_not_strong_enough_for_automatic_draft")
            try:
                appearance = _appearance(reference_rgb, target_rgb, source, metrics)
                projected["homography_original"] = homography_original(metrics).tolist()
            except (ValueError, KeyError, np.linalg.LinAlgError, cv2.error) as error:
                appearance = {"passed": False, "reasons": ["appearance_error_" + type(error).__name__], "appearance_error_score": None}
        reasons.extend(appearance["reasons"])
        allowed = bool(strong_geometry and appearance["passed"] and not reasons)
        projected["auto_transfer_ok"] = allowed
        projected["full_surface_transfer_ok"] = allowed and role == "complete_surface"
        projected["training_eligible"] = False
        projected["needs_review"] = True
        projected["transfer_reasons"] = reasons
        projected["appearance_error_score"] = appearance.get("appearance_error_score")
        evidence["appearance"] = appearance
        evidence["auto_transfer_ok"] = allowed
        evidence["full_surface_transfer_ok"] = projected["full_surface_transfer_ok"]
        evidence["transfer_reasons"] = reasons
        evidence["transfer_status"] = "automatic_draft_allowed" if allowed else "automatic_transfer_abstain"
        if strong_geometry and not allowed and role == "complete_surface":
            subregions = _allowed_subregions(source, projected, evidence.get("metrics", {}), appearance, target_rgb.size)
            result["automatic_allowed_subregions"].extend(subregions)
            evidence["automatic_allowed_subregion_count"] = len(subregions)
            projected["role"] = "withheld_diagnostic"
            projected["original_role"] = role
    result["regions"].extend(result["automatic_allowed_subregions"])
    result["auto_transfer_ok"] = bool(result["regions"] and len(result["regions"]) == len(regions) and all(region["auto_transfer_ok"] for region in result["regions"]))
    return result
