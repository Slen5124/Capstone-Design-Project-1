"""Apply locally supplied ROI envelopes to categorical mask drafts.

No images or models are loaded. The geometry is a human supplied proposal, and
the revised masks remain unreviewed. Semantic ID 255 is unknown; uncertainty
values are categorical provenance flags, not confidence scores.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


SEMANTIC_IDS = frozenset({1, 2, 3, 4, 5, 255})
QA_VALUES = {
    0: "no additional flag; not a visual pass",
    1: "unassigned residual",
    2: "cross-class conflict",
    3: "ignore region",
    4: "withheld surface claim",
    5: "locally supplied wall envelope draft",
    6: "locally supplied other exclusion draft",
    7: "locally supplied withheld wall region",
}
CONFIG_KEYS = frozenset(
    {"reference_size", "wall_planes", "other_exclusions", "withhold_exclusions"}
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ROI configuration contains a duplicate JSON key")
        result[key] = value
    return result


def _reference_size(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("reference_size must be [width, height]")
    if any(type(item) is not int or not 0 < item <= 2**31 - 1 for item in value):
        raise ValueError("reference_size must contain positive pixel dimensions")
    return tuple(value)


def _validate_polygons(polygons, reference_size):
    if not isinstance(polygons, dict):
        raise ValueError("Each ROI category must be an object mapping names to polygons")
    rw, rh = _reference_size(reference_size)
    for name, points in polygons.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Polygon names must be nonempty strings")
        if not isinstance(points, (list, tuple)) or len(points) < 3:
            raise ValueError("Each polygon must contain at least three [x, y] points")
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Polygon points must be [x, y] pairs")
            if any(
                isinstance(coord, bool)
                or not isinstance(coord, (int, float))
                or (isinstance(coord, float) and not math.isfinite(coord))
                for coord in point
            ):
                raise ValueError("Polygon coordinates must be finite numbers")
            x, y = point
            if not (0 <= x < rw and 0 <= y < rh):
                raise ValueError("Polygon coordinates must match reference_size")
        if len({tuple(point) for point in points}) < 3:
            raise ValueError("A polygon must contain at least three distinct points")


def validate_roi_config(config):
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ValueError("ROI configuration must contain exactly: " + ", ".join(sorted(CONFIG_KEYS)))
    reference_size = _reference_size(config["reference_size"])
    for key in CONFIG_KEYS - {"reference_size"}:
        _validate_polygons(config[key], reference_size)
    return config


def _validate_array(array, allowed_values, name):
    if not isinstance(array, np.ndarray) or array.ndim != 2 or array.dtype != np.uint8:
        raise ValueError(name + " must be a two-dimensional uint8 categorical mask")
    if not set(np.unique(array).tolist()) <= set(allowed_values):
        raise ValueError(name + " contains unsupported categorical values")


def read_categorical_png(data: bytes, allowed_values, name):
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "PNG" or image.mode != "L":
            raise ValueError(name + " must be an 8-bit grayscale PNG in L mode")
        image.load()
        array = np.asarray(image).copy()
    _validate_array(array, allowed_values, name)
    return array


def raster(polygons, reference_size, size):
    """Scale reference coordinates and fill polygons with the original rounding rule."""
    _validate_polygons(polygons, reference_size)
    w, h = _reference_size(size)
    rw, rh = _reference_size(reference_size)
    mask = np.zeros((h, w), np.uint8)
    for points in polygons.values():
        polygon = np.asarray(points, np.float32)
        polygon[:, 0] *= w / rw
        polygon[:, 1] *= h / rh
        cv2.fillPoly(mask, [np.rint(polygon).astype(np.int32)], 1)
    return mask.astype(bool)


def revise(base, oldqa, config):
    """Keep foreground and uncertain surface proposals protected during ROI edits."""
    _validate_array(base, SEMANTIC_IDS, "input mask")
    _validate_array(oldqa, QA_VALUES, "input uncertainty")
    if base.shape != oldqa.shape:
        raise ValueError("Input mask and uncertainty must have equal dimensions")
    validate_roi_config(config)
    size = (base.shape[1], base.shape[0])
    reference_size = config["reference_size"]
    plane = raster(config["wall_planes"], reference_size, size)
    other = raster(config["other_exclusions"], reference_size, size)
    held = raster(config["withhold_exclusions"], reference_size, size)
    ignore = oldqa == 3
    foreground = np.isin(base, [4, 5])
    uncertain = (oldqa == 2) | (oldqa == 4)
    out, qa = base.copy(), oldqa.copy()
    wall_update = plane & ~(foreground | other | held | ignore | uncertain)
    out[wall_update] = 1
    qa[wall_update] = 5
    other_update = other & ~((base == 4) | ignore)
    out[other_update] = 5
    qa[other_update] = 6
    # A bounding exclusion withholds wall claims; it is not an object silhouette.
    held_update = held & (out == 1) & ~ignore
    out[held_update] = 255
    qa[held_update] = 7
    out[ignore] = 255
    qa[ignore] = 3
    return out, qa, plane, other, held


def _is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _validate_paths(mask_path, uncertainty_path, config_path, output):
    inputs = (mask_path, uncertainty_path, config_path)
    if any(not path.is_file() for path in inputs):
        raise ValueError("All inputs must be regular files")
    if len(set(inputs)) != len(inputs):
        raise ValueError("Mask, uncertainty, and ROI configuration must be different files")
    if output.exists():
        raise FileExistsError("Output must be a new directory")
    # Keep output outside the raw mask folders, including through symlink parents.
    for raw_parent in (mask_path.parent, uncertainty_path.parent):
        if _is_within(output, raw_parent) or _is_within(raw_parent, output):
            raise ValueError("Output and raw mask directories must not contain one another")
    if any(_is_within(path, output) for path in inputs):
        raise ValueError("Output must not contain an input file")


def run(args):
    expected_hash = args.expected_mask_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("expected-mask-sha256 must be a 64-character SHA256 digest")
    mask_path = args.input_mask.resolve(strict=True)
    uncertainty_path = args.input_uncertainty.resolve(strict=True)
    config_path = args.roi_config.resolve(strict=True)
    output = args.output.resolve()
    _validate_paths(mask_path, uncertainty_path, config_path, output)
    mask_bytes, uncertainty_bytes, config_bytes = (path.read_bytes() for path in (mask_path, uncertainty_path, config_path))
    parent_hash = sha256_bytes(mask_bytes)
    if parent_hash != expected_hash:
        raise ValueError("Input mask SHA256 does not match the expected parent mask")
    base = read_categorical_png(mask_bytes, SEMANTIC_IDS, "input mask")
    oldqa = read_categorical_png(uncertainty_bytes, QA_VALUES, "input uncertainty")
    config = json.loads(config_bytes.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    after, newqa, plane, other, held = revise(base, oldqa, config)
    input_hashes = {
        "parent_mask_sha256": parent_hash,
        "parent_uncertainty_sha256": sha256_bytes(uncertainty_bytes),
        "roi_config_sha256": sha256_bytes(config_bytes),
    }
    if any(
        sha256_bytes(path.read_bytes()) != digest
        for path, digest in zip((mask_path, uncertainty_path, config_path), input_hashes.values())
    ):
        raise ValueError("An input changed while the ROI revision was being prepared")
    output.mkdir(parents=True, exist_ok=False)
    mask_output = output / "semantic_draft.png"
    qa_output = output / "uncertainty.png"
    Image.fromarray(after).save(mask_output, format="PNG")
    Image.fromarray(newqa).save(qa_output, format="PNG")
    metadata = {
        "schema_version": 1,
        "method": "locally_supplied_roi_envelope_revision",
        **input_hashes,
        "expected_parent_mask_sha256_matched": True,
        "image_size": [base.shape[1], base.shape[0]],
        "changed_mask_pixels": int(np.count_nonzero(base != after)),
        "changed_uncertainty_pixels": int(np.count_nonzero(oldqa != newqa)),
        "roi_pixel_counts": {
            "wall_planes": int(plane.sum()),
            "other_exclusions": int(other.sum()),
            "withhold_exclusions": int(held.sum()),
        },
        "semantic_mask": mask_output.name,
        "semantic_mask_sha256": sha256_bytes(mask_output.read_bytes()),
        "uncertainty_mask": qa_output.name,
        "uncertainty_mask_sha256": sha256_bytes(qa_output.read_bytes()),
        "classes": {"wall": 1, "floor": 2, "ceiling": 3, "person": 4, "other": 5},
        "unknown_id": 255,
        "qa_values": QA_VALUES,
        "checked": False,
        "human_reviewed": False,
        "training_eligible": False,
        "needs_review": True,
        "label_origin": "local_roi_revision_not_ground_truth",
        "state_labels": None,
        "progress_ratio": None,
    }
    (output / "review_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "changed_mask_pixels": metadata["changed_mask_pixels"],
        "changed_uncertainty_pixels": metadata["changed_uncertainty_pixels"],
        "checked": False,
        "training_eligible": False,
        "needs_review": True,
    }))
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-mask", type=Path, required=True)
    parser.add_argument("--input-uncertainty", type=Path, required=True)
    parser.add_argument("--roi-config", type=Path, required=True)
    parser.add_argument("--expected-mask-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError) as error:
        parser.exit(2, f"ROI revision failed: {error}\n")


if __name__ == "__main__":
    main()
