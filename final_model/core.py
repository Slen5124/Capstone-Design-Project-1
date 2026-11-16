"""Per-photo, per-target measurement; no temporal correction or completion claims."""
from __future__ import annotations

import numpy as np
from layout import SURFACES, STATE_CLASSES, classes_for, surfaces_for


def validate_config(config):
    classes_for(config)
    expected = {"pipeline_version": "final-vision/0.2" if config["n_state"] == 8 else "final-vision/0.1",
                "encoder": "vit_base_patch14_reg4_dinov2.lvd142m", "decoder": "upernet",
                "n_sem": 8, "n_state": config["n_state"], "export_size": [1024, 576]}
    allowed = set(expected) | {"min_roi_px", "min_observed_fraction", "min_mean_confidence", "quality_policy_status"}
    if set(config) != allowed:
        raise ValueError("Missing or unsupported config keys; VCC/TTA options are not supported")
    if config["quality_policy_status"] != "provisional_requires_validation":
        raise ValueError("This release has no calibrated quality policy")
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f"Final model config mismatch: {key}")
    for key in ("min_observed_fraction", "min_mean_confidence"):
        value = config[key]
        if type(value) not in (int, float) or not np.isfinite(value) or not 0 < value <= 1:
            raise ValueError(f"Invalid threshold: {key}")
    if type(config["min_roi_px"]) is not int or config["min_roi_px"] < 1:
        raise ValueError("min_roi_px must be a positive integer")


def validate_targets(roi, targets, config=None):
    if roi.ndim != 2 or not np.issubdtype(roi.dtype, np.integer):
        raise ValueError("ROI must be a single-channel integer mask")
    ids, keys = set(), set()
    for target in targets:
        idx, key = target["index"], target["target_key"]
        if type(idx) is not int or not 1 <= idx <= 65534 or idx == 255 or idx in ids:
            raise ValueError("Target indices must be unique positive integers, excluding ignore IDs")
        if not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 128 or key in keys:
            raise ValueError("Target keys must be unique nonempty strings of at most 128 characters")
        surface = target["surface_type"]
        allowed = surfaces_for(config) if config is not None else SURFACES
        if surface not in allowed or target["activity"] != allowed[surface][2]:
            raise ValueError("Target activity is not supported by this class layout")
        if type(target.get("roi_confirmed")) is not bool:
            raise ValueError("roi_confirmed must be an explicit boolean")
        ids.add(idx)
        keys.add(key)
    if not targets:
        raise ValueError("At least one target is required")
    unknown = set(map(int, np.unique(roi))) - ids - {0, 255, 65535}
    if unknown:
        raise ValueError(f"ROI contains unmapped target indices: {sorted(unknown)}")


def measure_targets(state, confidence, roi, targets, config, view_confirmed):
    validate_targets(roi, targets, config)
    if state.shape != roi.shape or confidence.shape != roi.shape:
        raise ValueError("State, confidence and ROI coordinates must match")
    if not np.isin(state, list(range(config["n_state"])) + [255]).all():
        raise ValueError("Unexpected state labels")
    finite = np.isfinite(confidence) & (confidence >= 0) & (confidence <= 1) & (state != 255)
    rows = []
    for target in targets:
        inside = roi == target["index"]
        n = int(inside.sum())
        not_id, installed_id, _ = SURFACES[target["surface_type"]]
        n0 = int((inside & finite & (state == not_id)).sum())
        n1 = int((inside & finite & (state == installed_id)).sum())
        observed = (n0 + n1) / n if n else 0.0
        # Same definition as the existing inference rule: mean max-softmax across the ROI.
        mean_conf = float(confidence[inside].mean()) if n and finite[inside].all() else None
        reasons = []
        if not view_confirmed:
            reasons.append("view_not_confirmed")
        if not target["roi_confirmed"]:
            reasons.append("roi_not_confirmed")
        if n < config["min_roi_px"]:
            reasons.append("roi_too_small")
        if observed < config["min_observed_fraction"]:
            reasons.append("insufficient_observed_area")
        if mean_conf is None:
            reasons.append("missing_or_nonfinite_prediction")
        elif mean_conf < config["min_mean_confidence"]:
            reasons.append("low_confidence")
        rows.append({"target_key": target["target_key"], "surface_type": target["surface_type"],
                     "activity": target["activity"], "status": "held" if reasons else "observed",
                     "installed_ratio": None if reasons else n1 / (n0 + n1),
                     "hold_reason": ";".join(reasons) or None, "roi_px": n,
                     "installed_px": n1, "not_installed_px": n0,
                     "occluded_px": int((inside & (state == config["n_state"] - 2)).sum()),
                     "observed_fraction": observed, "mean_confidence": mean_conf})
    return rows


def render_overlay(rgb, state, roi, target, config):
    """One target per overlay: red=not installed, green=installed, gray=occluded."""
    result = rgb.astype(np.float32).copy()
    inside = roi == target["index"]
    not_id, installed_id, _ = SURFACES[target["surface_type"]]
    for label, color in ((not_id, (230, 40, 40)), (installed_id, (0, 200, 80)),
                         (config["n_state"] - 2, (150, 150, 150))):
        mask = inside & (state == label)
        result[mask] = result[mask] * 0.45 + np.array(color) * 0.55
    padded = np.pad(inside, 1)
    interior = (padded[:-2, 1:-1] & padded[2:, 1:-1] &
                padded[1:-1, :-2] & padded[1:-1, 2:])
    result[inside & ~interior] = (255, 220, 0)
    return result.astype(np.uint8)
