"""Merge directly inspected visible-surface AI drafts into an existing ZIP config.

This helper does not read, extract or modify ZIP entries. Existing local original
reference photos are read only to verify their SHA256 and dimensions. It never
promotes an assistant draft to human ground truth.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon


def _read(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _points(values: list, size: list, *, normalized: bool) -> list:
    if not isinstance(values, list) or len(values) < 3:
        raise ValueError("A surface needs at least three vertices")
    output = []
    for point in values:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Invalid surface vertex")
        x, y = point
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) for v in point):
            raise ValueError("Nonfinite or nonnumeric surface vertex")
        if normalized:
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise ValueError("Normalized surface vertex outside image")
            x, y = x * size[0], y * size[1]
        if not 0 <= x <= size[0] or not 0 <= y <= size[1]:
            raise ValueError("Surface vertex outside source image")
        output.append([round(x, 3), round(y, 3)])
    polygon = Polygon(output)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Invalid or empty visible surface polygon")
    return output


def prepare_teacher_config(seed_config: dict, surfaces: dict | str | Path | None = None,
                           *, verify_source_files: bool = True,
                           require_all: bool = False) -> dict:
    """Return a separate config containing patches and visible-surface drafts.

    Complete_surface denotes the selected, conservatively visible surface
    segment, not the entirety of an unknown physical wall. Registration anchors
    use that exact surface polygon; old patch anchors are never reused here.
    """
    if surfaces is None:
        surfaces = Path(__file__).with_name("teacher_surfaces.json")
    spec = _read(surfaces) if isinstance(surfaces, (str, Path)) else copy.deepcopy(surfaces)
    if not isinstance(spec, dict) or spec.get("human_confirmed_ground_truth") is not False:
        raise ValueError("Surface source must explicitly remain an AI draft")
    normalized = spec.get("coordinate_space") == "normalized_original_image_xy_0_to_1"
    if not normalized:
        raise ValueError("Unsupported teacher surface coordinate system")
    drafts = {item["id"]: item for item in spec.get("references", [])}
    output = copy.deepcopy(seed_config)
    seen = set()
    counts = {"diagnostic_patch": 0, "complete_surface": 0}
    for ref in output.get("references", []):
        identifier = ref.get("id")
        for region in ref.get("regions", []):
            region["role"] = "diagnostic_patch"
            region["training_eligible"] = False
            counts["diagnostic_patch"] += 1
        item = drafts.get(identifier)
        if item is None:
            if require_all:
                raise ValueError(f"No visible surface specification for {identifier}")
            continue
        seen.add(identifier)
        size = ref.get("size")
        if (not isinstance(size, list) or len(size) != 2
                or any(not isinstance(v, int) or v <= 0 for v in size)):
            raise ValueError(f"Invalid original dimensions: {identifier}")
        if item.get("original_size", spec.get("original_size")) != size:
            raise ValueError(f"Teacher source dimensions differ: {identifier}")
        source = ref.get("source", {})
        expected_sha = source.get("sha256")
        if item.get("source_sha256") != expected_sha:
            raise ValueError(f"Teacher source hash binding differs: {identifier}")
        if item.get("source_entry") != source.get("entry"):
            raise ValueError(f"Teacher source archive entry differs: {identifier}")
        if verify_source_files:
            original_path = ref.get("source_visual_review")
            if not original_path:
                raise ValueError(f"Existing original photo path missing: {identifier}")
            original = Path(original_path)
            actual_sha = hashlib.sha256(original.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                raise ValueError(f"Original photograph hash differs: {identifier}")
            with Image.open(original) as image:
                if list(image.size) != size:
                    raise ValueError(f"Original photograph dimensions differ: {identifier}")
        region_ids = {r["id"] for r in ref.get("regions", [])}
        for number, draft in enumerate(item.get("regions", [])):
            if draft.get("role") != "complete_surface" or draft.get("label") not in {"wall", "ceiling"}:
                raise ValueError(f"Unexpected surface role or label: {identifier}")
            points = _points(draft["points"], size, normalized=True)
            exclusions = [_points(p, size, normalized=True) for p in draft.get("exclusions", [])]
            effective = Polygon(points)
            for polygon in exclusions:
                effective = effective.difference(Polygon(polygon))
            if effective.is_empty or effective.area <= 0:
                raise ValueError(f"Exclusions erase the whole surface: {identifier}")
            region_id = draft.get("id", f"{identifier}_visible_{draft['label']}_{number}")
            if region_id in region_ids:
                raise ValueError(f"Duplicate region id: {region_id}")
            region_ids.add(region_id)
            region = copy.deepcopy(draft)
            region.update(points=points, exclusions=exclusions,
                          registration_points=copy.deepcopy(points),
                          reference_origin="assistant_visual_draft", human_confirmed=False,
                          training_eligible=False, source_sha256=expected_sha,
                          coordinate_space="original_image_pixels",
                          geometry_scope="selected_visible_surface_segment",
                          excludes_unknown_surface=True,
                          min_coverage=draft.get("min_coverage", .6),
                          max_conflict=draft.get("max_conflict", .25),
                          conflict_labels=["ceiling"] if draft["label"] == "wall" else ["wall"])
            ref.setdefault("regions", []).append(region)
            counts["complete_surface"] += 1
        ref.update(reviewed=False, human_confirmed=False,
                   reference_origin="assistant_visual_draft", reviewer="Codex visual review",
                   source_sha_verified=verify_source_files,
                   visible_surface_review=item.get("scene_note", ""),
                   skipped_surfaces=item.get("skipped_surfaces", []))
    if require_all and set(drafts) - seen:
        raise ValueError("Teacher specifications do not match configured references")
    output.update(schema_version=max(3, output.get("schema_version", 0)),
                  human_confirmed_ground_truth=False,
                  teacher_surface_preparation={
                      "source": "directly_viewed_existing_original_photographs",
                      "surface_origin": "assistant_visual_draft",
                      "counts": counts,
                      "visibility_scope": "conservative visible segments; not all physical wall area",
                      "registration_scope": "same visible surface polygon only",
                      "source_files_modified": False,
                      "images_copied": False,
                      "training_eligible": False,
                  })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--surfaces", type=Path, default=Path(__file__).with_name("teacher_surfaces.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("Refuse to overwrite an existing config")
    output = prepare_teacher_config(_read(args.project), args.surfaces, require_all=args.require_all)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(output["teacher_surface_preparation"], ensure_ascii=False))


if __name__ == "__main__":
    main()
