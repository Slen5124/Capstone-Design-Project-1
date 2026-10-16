"""Conservative, local planar registration for reviewed reference regions.

A separate SIFT/RANSAC homography is fitted for each wall/ceiling region. An
optional ``registration_points`` polygon supplies a larger feature anchor on
the SAME reviewed plane; ``points`` remains the visible semantic checking ROI.
The anchor must contain the semantic ROI. It must not cross other wall planes,
the ceiling, movable foreground objects, or other surfaces. This same-plane
assertion requires human review and cannot be established from coordinates.
A single image-wide transform would mix surfaces at different depths and is not
used here. Projected regions are candidate-checking aids, never ground truth:
feature matches cannot establish target visibility, unchanged semantics, or
absence of newly introduced occluders. Low texture, changed views, and weak
geometric evidence produce abstention rather than a missing-wall verdict.

The caller must verify image byte identity before setting ``source_identity``.
Only that path, with equal dimensions, yields ``basis='reviewed_image'``.
Thresholds below are conservative engineering gates, not calibrated accuracy
or confidence estimates. They must be evaluated on reviewed site images.

OpenCV reference: https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np
from PIL import Image
from shapely.geometry import MultiPoint, Polygon, box


MAX_DIMENSION = 1000
MIN_MATCHES = 12
MIN_INLIER_RATIO = 0.65
MIN_FEATURE_HULL_COVERAGE = 0.15
MIN_FEATURE_SPAN = 0.35
MIN_FRAME_OVERLAP = 0.90


def _polygon(points: Any) -> Polygon:
    coordinates = np.asarray(points, dtype=np.float64)
    if coordinates.ndim != 2 or coordinates.shape[1] != 2 or len(coordinates) < 3:
        raise ValueError("region_requires_at_least_three_xy_points")
    if not np.isfinite(coordinates).all():
        raise ValueError("nonfinite_region_coordinates")
    polygon = Polygon(coordinates)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 1:
        raise ValueError("invalid_or_degenerate_region_polygon")
    return polygon


def _validate_region(region: dict, size: tuple[int, int]) -> tuple[Polygon, Any]:
    if not isinstance(region, dict):
        raise ValueError("region_must_be_an_object")
    if not isinstance(region.get("label"), str) or not region["label"].strip():
        raise ValueError("region_requires_nonempty_label")
    polygon = _polygon(region.get("points"))
    frame = box(0, 0, *size)
    if frame.intersection(polygon).area / polygon.area < 0.999:
        raise ValueError("reviewed_region_outside_reference_image")
    anchor = _polygon(region.get("registration_points", region["points"]))
    if frame.intersection(anchor).area / anchor.area < 0.999:
        raise ValueError("registration_anchor_outside_reference_image")
    if anchor.intersection(polygon).area / polygon.area < 0.999:
        raise ValueError("registration_anchor_does_not_cover_semantic_region")
    effective = polygon
    for points in region.get("exclusions", []):
        exclusion = _polygon(points)
        if frame.intersection(exclusion).area / exclusion.area < 0.999:
            raise ValueError("reviewed_exclusion_outside_reference_image")
        effective = effective.difference(exclusion)
    if effective.is_empty or effective.area <= 1:
        raise ValueError("region_fully_excluded")
    return polygon, effective


def _gray(image: Image.Image) -> tuple[np.ndarray, float]:
    image = image.convert("RGB")
    scale = min(1.0, MAX_DIMENSION / max(image.size))
    if scale < 1:
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    # Rounded output dimensions have slightly different x/y scales. All point
    # transforms use the actual dimensions below, not this nominal scale.
    return np.asarray(image.convert("L")), scale


def _scaled_points(points: Any, scale_xy: np.ndarray) -> np.ndarray:
    return np.asarray(points, dtype=np.float64) * scale_xy


def _transform(points: np.ndarray, homography: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack([points, np.ones(len(points))])
    projected = homogeneous @ homography.T
    denominator = projected[:, 2]
    if np.any(np.abs(denominator) < 1e-8):
        raise ValueError("projection_crosses_horizon")
    return projected[:, :2] / denominator[:, None]


def _spread(points: np.ndarray, polygon: Polygon) -> tuple[float, float, float]:
    xmin, ymin, xmax, ymax = polygon.bounds
    span = np.ptp(points, axis=0)
    hull_ratio = MultiPoint(points).convex_hull.area / max(polygon.area, 1e-9)
    return hull_ratio, span[0] / max(xmax - xmin, 1e-9), span[1] / max(ymax - ymin, 1e-9)


def _geometry_gate(
    homography: np.ndarray, source: Polygon, target_size: tuple[int, int], metrics: dict,
) -> tuple[np.ndarray, Polygon, list[str]]:
    reasons = []
    if homography.shape != (3, 3) or not np.isfinite(homography).all():
        raise ValueError("nonfinite_homography")
    normalizer = homography[2, 2]
    if abs(normalizer) < 1e-10:
        raise ValueError("unstable_homography_normalization")
    homography = homography / normalizer
    condition = float(np.linalg.cond(homography))
    metrics["homography_condition"] = condition
    if not np.isfinite(condition) or condition > 1e7 or abs(np.linalg.det(homography)) < 1e-10:
        raise ValueError("singular_or_unstable_homography")
    source_points = np.asarray(source.exterior.coords[:-1], dtype=np.float64)
    sample_points = np.vstack([source_points, [source.centroid.x, source.centroid.y]])
    denominator = sample_points @ homography[2, :2] + homography[2, 2]
    if np.any(denominator * denominator[0] <= 0) or np.min(np.abs(denominator)) / np.max(np.abs(denominator)) < 0.10:
        raise ValueError("projection_crosses_or_nears_horizon")
    projected_points = _transform(source_points, homography)
    if not np.isfinite(projected_points).all():
        raise ValueError("nonfinite_projected_region")
    projected = _polygon(projected_points)
    if source.exterior.is_ccw != projected.exterior.is_ccw:
        reasons.append("reflection_or_reversed_region")
    ratio = projected.area / source.area
    metrics["projected_area_ratio"] = float(ratio)
    if not 0.20 <= ratio <= 5.0:
        reasons.append("extreme_projected_area_scale")
    frame = box(0, 0, *target_size)
    overlap = frame.intersection(projected).area / projected.area
    metrics["in_frame_fraction"] = float(overlap)
    if overlap < MIN_FRAME_OVERLAP:
        reasons.append("projected_region_mostly_outside_target")
    # Check the local differential on all vertices and centroid. A valid polygon
    # alone does not reject a badly conditioned perspective deformation.
    for point, denom in zip(sample_points, denominator):
        numerator = homography[:2, :2] @ point + homography[:2, 2]
        jacobian = (homography[:2, :2] * denom - np.outer(numerator, homography[2, :2])) / denom ** 2
        singular_values = np.linalg.svd(jacobian, compute_uv=False)
        if np.linalg.det(jacobian) <= 0:
            reasons.append("reflection_or_local_fold")
            break
        if singular_values[-1] < 0.25 or singular_values[0] > 4.0 or singular_values[0] / singular_values[-1] > 6.0:
            reasons.append("extreme_local_scale_or_distortion")
            break
    return homography, projected, reasons


def project_regions(
    reference_rgb: Image.Image,
    target_rgb: Image.Image,
    reference_regions: list[dict],
    source_identity: bool = False,
) -> dict:
    """Return independently accepted regions and evidence for every request.

    ``accepted`` is true only when all requested regions pass. For partial
    registration, ``regions`` still contains the usable individual projections.
    Consumers should inspect per-region evidence. Exclusions are projected with
    their region. The returned pixel coordinates belong to the target's original
    dimensions. Identity is an explicit caller assertion; this function checks
    size but does not compare encoded image bytes. ``registration_points``, if
    present, is a containing polygon on the same human-confirmed plane used only
    to obtain feature correspondences. It does not enlarge the semantic ROI.
    """
    basis = "reviewed_image" if source_identity else "provisional_reference_projection"
    result = {"regions": [], "evidence": [], "basis": basis, "accepted": False}
    if not reference_regions:
        return result
    if source_identity and reference_rgb.size != target_rgb.size:
        for index, region in enumerate(reference_regions):
            result["evidence"].append({
                "id": region.get("id", str(index)) if isinstance(region, dict) else str(index),
                "status": "abstain", "reasons": ["identity_image_dimensions_differ"],
                "metrics": {}, "basis": basis,
            })
        return result
    valid_regions = []
    for index, region in enumerate(reference_regions):
        evidence = {
            "id": region.get("id", str(index)) if isinstance(region, dict) else str(index),
            "status": "abstain", "reasons": [], "metrics": {}, "basis": basis,
        }
        result["evidence"].append(evidence)
        try:
            polygon, effective = _validate_region(region, reference_rgb.size)
        except (ValueError, TypeError, KeyError) as error:
            evidence["reasons"] = [str(error)]
            continue
        if source_identity:
            output = copy.deepcopy(region)
            output["basis"] = basis
            result["regions"].append(output)
            evidence["status"] = "accepted"
            evidence["reasons"] = ["caller_verified_same_image_identity"]
        else:
            evidence["visibility_not_verified"] = True
            valid_regions.append((region, polygon, effective, evidence))
    if source_identity:
        result["accepted"] = len(result["regions"]) == len(reference_regions)
        return result
    if not valid_regions:
        return result
    # cv2 is deliberately lazy: per-image reviewed ROIs need no feature runtime.
    try:
        import cv2
    except ImportError:
        for _, _, _, evidence in valid_regions:
            evidence["reasons"] = ["opencv_unavailable", "visibility_not_verified"]
        return result
    reference_gray, _ = _gray(reference_rgb)
    target_gray, _ = _gray(target_rgb)
    reference_scale = np.array([reference_gray.shape[1] / reference_rgb.width, reference_gray.shape[0] / reference_rgb.height])
    target_scale = np.array([target_gray.shape[1] / target_rgb.width, target_gray.shape[0] / target_rgb.height])
    sift = cv2.SIFT_create(nfeatures=12000, contrastThreshold=0.03)
    target_keypoints, target_descriptors = sift.detectAndCompute(target_gray, None)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    for region, _, _, evidence in valid_regions:
        metrics = evidence["metrics"]
        try:
            points = _scaled_points(region["points"], reference_scale)
            source_polygon = _polygon(points)
            anchor_points = _scaled_points(region.get("registration_points", region["points"]), reference_scale)
            anchor_polygon = _polygon(anchor_points)
            effective_anchor = anchor_polygon
            metrics["registration_anchor_used"] = "registration_points" in region
            mask = np.zeros(reference_gray.shape, dtype=np.uint8)
            cv2.fillPoly(mask, [np.rint(anchor_points).astype(np.int32)], 255)
            for exclusion in region.get("exclusions", []):
                excluded_points = _scaled_points(exclusion, reference_scale)
                effective_anchor = effective_anchor.difference(_polygon(excluded_points))
                cv2.fillPoly(mask, [np.rint(excluded_points).astype(np.int32)], 0)
            if effective_anchor.is_empty or effective_anchor.area <= 1:
                raise ValueError("registration_anchor_fully_excluded")
            source_keypoints, source_descriptors = sift.detectAndCompute(reference_gray, mask)
            metrics["source_features"] = len(source_keypoints)
            metrics["target_features"] = len(target_keypoints)
            if source_descriptors is None or target_descriptors is None or len(source_descriptors) < MIN_MATCHES or len(target_descriptors) < MIN_MATCHES:
                raise ValueError("insufficient_texture_features")
            forward = matcher.knnMatch(source_descriptors, target_descriptors, k=2)
            reverse = matcher.match(target_descriptors, source_descriptors)
            reverse_map = {match.queryIdx: match.trainIdx for match in reverse}
            matches = [
                pair[0] for pair in forward if len(pair) == 2
                and pair[0].distance < 0.70 * pair[1].distance
                and reverse_map.get(pair[0].trainIdx) == pair[0].queryIdx
            ]
            metrics["matches"] = len(matches)
            if len(matches) < MIN_MATCHES:
                raise ValueError("insufficient_mutual_ratio_matches")
            source_matched = np.asarray([source_keypoints[match.queryIdx].pt for match in matches], dtype=np.float64)
            target_matched = np.asarray([target_keypoints[match.trainIdx].pt for match in matches], dtype=np.float64)
            homography, inlier_mask = cv2.findHomography(
                source_matched, target_matched, cv2.RANSAC, 3.0,
                maxIters=5000, confidence=0.999,
            )
            if homography is None or inlier_mask is None:
                raise ValueError("homography_fit_failed")
            inliers = inlier_mask.ravel().astype(bool)
            count = int(inliers.sum())
            ratio = count / len(matches)
            metrics.update(inliers=count, inlier_ratio=float(ratio))
            if count < MIN_MATCHES or ratio < MIN_INLIER_RATIO:
                raise ValueError("insufficient_ransac_inliers")
            homography, projected_polygon, geometry_reasons = _geometry_gate(
                homography, source_polygon, (target_gray.shape[1], target_gray.shape[0]), metrics,
            )
            anchor_metrics = {}
            homography, projected_anchor, anchor_reasons = _geometry_gate(
                homography, anchor_polygon, (target_gray.shape[1], target_gray.shape[0]), anchor_metrics,
            )
            metrics["registration_anchor_geometry"] = anchor_metrics
            geometry_reasons.extend("registration_anchor_" + reason for reason in anchor_reasons)
            source_inliers = source_matched[inliers]
            target_inliers = target_matched[inliers]
            source_hull, source_x, source_y = _spread(source_inliers, effective_anchor)
            target_hull, target_x, target_y = _spread(target_inliers, projected_anchor)
            metrics.update(
                source_feature_hull_fraction=float(source_hull),
                source_feature_span_x=float(source_x), source_feature_span_y=float(source_y),
                target_feature_hull_fraction=float(target_hull),
                target_feature_span_x=float(target_x), target_feature_span_y=float(target_y),
            )
            if min(source_hull, target_hull) < MIN_FEATURE_HULL_COVERAGE or min(source_x, source_y, target_x, target_y) < MIN_FEATURE_SPAN:
                geometry_reasons.append("inliers_not_spread_across_region")
            errors = np.linalg.norm(_transform(source_inliers, homography) - target_inliers, axis=1)
            inverse_errors = np.linalg.norm(_transform(target_inliers, np.linalg.inv(homography)) - source_inliers, axis=1)
            metrics.update(
                reprojection_median_px=float(np.median(errors)),
                reprojection_p95_px=float(np.percentile(errors, 95)),
                inverse_reprojection_median_px=float(np.median(inverse_errors)),
            )
            if np.median(errors) > 2.0 or np.percentile(errors, 95) > 4.0 or np.median(inverse_errors) > 2.0:
                geometry_reasons.append("reprojection_error_too_large")
            if geometry_reasons:
                evidence["reasons"] = list(dict.fromkeys(geometry_reasons)) + ["visibility_not_verified"]
                continue
            output = copy.deepcopy(region)
            output["points"] = (_transform(points, homography) / target_scale).tolist()
            if "registration_points" in region:
                output["registration_points"] = (_transform(anchor_points, homography) / target_scale).tolist()
            output["exclusions"] = [
                (_transform(_scaled_points(exclusion, reference_scale), homography) / target_scale).tolist()
                for exclusion in region.get("exclusions", [])
            ]
            # Recheck exclusions rather than silently repairing transformed shapes.
            for exclusion in region.get("exclusions", []):
                _, _, exclusion_reasons = _geometry_gate(
                    homography, _polygon(_scaled_points(exclusion, reference_scale)),
                    (target_gray.shape[1], target_gray.shape[0]), {},
                )
                if exclusion_reasons:
                    raise ValueError("projected_exclusion_" + exclusion_reasons[0])
            output["basis"] = basis
            output["visibility_not_verified"] = True
            metrics["homography_resized"] = homography.tolist()
            metrics["reference_resize_xy"] = reference_scale.tolist()
            metrics["target_resize_xy"] = target_scale.tolist()
            result["regions"].append(output)
            evidence["status"] = "accepted_provisional"
            evidence["reasons"] = ["per_region_planar_registration_passed", "visibility_not_verified"]
        except (ValueError, TypeError, KeyError, np.linalg.LinAlgError, cv2.error) as error:
            evidence["reasons"] = [str(error), "visibility_not_verified"]
    result["accepted"] = len(result["regions"]) == len(reference_regions)
    return result
