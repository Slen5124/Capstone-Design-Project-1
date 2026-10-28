"""Data model constants, config loading, hashing and polygon validation.

Record kinds kept in the review store (see store.py) and their keys:

    view           key = view_id
    target         key = target_id
    applicability  key = "<target_id>|<activity_id>"
    roi            key = "<target_id>|<view_id>"           (reference ROI per view)
    observation    key = "<image_id>|<target_id>"          (manual per-image ROI / occlusion)
    state_label    key = "<image_id>|<target_id>|<activity_id>"

Automatic per-image observations (ROI reuse/registration, polygon links,
occluder candidates, quality flags) are recomputable drafts and live in the run
directory (stages/assign/observations.jsonl), not in the store.

Polygon JSON used everywhere in this package (original pixel coordinates):

    {"exterior": [[x, y], ...], "holes": [[[x, y], ...], ...]}

Exclusion JSON: {"points": [[x, y], ...], "reason": "opening"}
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION

# ---------------------------------------------------------------- enumerations
SURFACE_TYPES = ("wall", "floor", "ceiling")
TARGET_IDENTITY = ("proposed", "confirmed", "unresolved", "retired")
APPLICABILITY = ("unknown", "applicable", "not_applicable")
LABEL_SOURCES = ("manual", "model_draft", "propagated_draft", "teacher_draft_import", "assistant_draft", "bulk_prefill_draft",
                 "config", "auto_candidate")
DRAFT_SOURCES = ("model_draft", "propagated_draft", "teacher_draft_import", "auto_candidate", "assistant_draft", "bulk_prefill_draft")
STATE_NAMES = ("not_installed", "installed")
UNDETERMINABLE_REASONS = ("occluded", "motion_blur", "out_of_focus", "low_light", "overexposed",
                          "partial_view", "ambiguous_boundary", "work_in_progress_unclear", "other")
ROI_SOURCES = ("reference_image", "same_view_reuse", "registered_draft", "manual", "none")

# Default state export values. 255 is an ignore index, never a model class.
STATE_VALUES = {"not_installed": 0, "installed": 1}
STATE_IGNORE = 255
STATE_OCCLUDED_OPTIONAL = 2
VISIBILITY_VALUES = {"outside_roi": 0, "visible": 1, "occluded": 2, "undeterminable": 3}


class ConfigError(ValueError):
    pass


# --------------------------------------------------------------------- hashing
def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------- config
def load_config(path: Path | str) -> dict:
    """Load JSON config; YAML is accepted only if PyYAML is already installed.

    The project venv does not ship PyYAML and this package must not change the
    environment, so the shipped configs are JSON.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as error:  # pragma: no cover - environment dependent
            raise ConfigError(f"{path}: PyYAML is not installed; use the JSON config") from error
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level config must be an object")
    return data


def validate_taxonomy(tax: dict) -> list[str]:
    errors = []
    surfaces = tax.get("surface_types", {})
    for name in surfaces:
        if name not in SURFACE_TYPES:
            errors.append(f"unknown surface type {name!r}")
    classes = tax.get("object_classes", {}).get("classes", [])
    ids = [c.get("id") for c in classes]
    if len(set(ids)) != len(ids):
        errors.append("duplicate object class ids")
    ignore = tax.get("object_classes", {}).get("ignore_value", 255)
    if ignore in ids:
        errors.append("object class id collides with ignore value")
    for c in classes:
        if not isinstance(c.get("id"), int) or not 0 <= c["id"] <= 254:
            errors.append(f"object class {c.get('name')!r} id must be int 0..254")
    for act_id, act in tax.get("activities", {}).items():
        st = act.get("surface_types") or []
        if not st or any(s not in SURFACE_TYPES for s in st):
            errors.append(f"activity {act_id}: surface_types must be subset of {SURFACE_TYPES}")
        if not act.get("installs"):
            errors.append(f"activity {act_id}: 'installs' (what is installed) is required")
    return errors


def validate_site(site: dict, tax: dict) -> list[str]:
    errors = []
    for key in ("site_id", "camera_id"):
        if not site.get(key):
            errors.append(f"site config requires {key}")
    seen = set()
    for t in site.get("targets", []):
        tid = t.get("target_id")
        if not tid or tid in seen:
            errors.append(f"target id missing/duplicate: {tid!r}")
        seen.add(tid)
        if t.get("surface_type") not in SURFACE_TYPES:
            errors.append(f"target {tid}: surface_type must be one of {SURFACE_TYPES}")
        for act_id, item in (t.get("activities") or {}).items():
            if act_id not in tax.get("activities", {}):
                errors.append(f"target {tid}: unknown activity {act_id}")
            elif t.get("surface_type") not in tax["activities"][act_id]["surface_types"]:
                errors.append(f"target {tid}: activity {act_id} does not apply to {t.get('surface_type')}")
            if (item or {}).get("applicability", "unknown") not in APPLICABILITY:
                errors.append(f"target {tid}/{act_id}: applicability must be one of {APPLICABILITY}")
    return errors


# -------------------------------------------------------------------- geometry
def _finite_xy(points: Any) -> bool:
    return (isinstance(points, list) and len(points) >= 3 and all(
        isinstance(p, (list, tuple)) and len(p) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)
        for p in points))


def polygon_geometry(poly: dict):
    """shapely Polygon from {"exterior", "holes"}; raises ValueError if unusable."""
    from shapely.geometry import Polygon
    if not isinstance(poly, dict) or not _finite_xy(poly.get("exterior")):
        raise ValueError("polygon_requires_exterior_with_3_finite_points")
    holes = poly.get("holes") or []
    for hole in holes:
        if not _finite_xy(hole):
            raise ValueError("hole_requires_3_finite_points")
    geometry = Polygon(poly["exterior"], holes)
    if geometry.is_empty or geometry.area <= 1.0:
        raise ValueError("degenerate_polygon")
    if not geometry.is_valid:
        raise ValueError("invalid_polygon_self_intersection_or_bad_hole")
    return geometry


def sanitize_polygons(polys: list, size, name: str = "polygon") -> tuple[list[dict], list[str]]:
    """Make hand-drawn polygons usable instead of rejecting them (UI clicks outside the photo, a crossing edge).

    Every point is clamped into the photo, a self-crossing polygon is repaired (make_valid) and split into its
    parts, and parts under 1 px^2 are dropped. Returns (clean polygons, notes in Korean for the reviewer).
    Malformed input (not a list of {exterior: [[x, y], ...]}) is returned unchanged so validation reports it."""
    import shapely
    from shapely.geometry import MultiPolygon, Polygon, box
    if not isinstance(polys, list):
        return polys, []
    w, h = float(size[0]), float(size[1])
    frame = box(0, 0, w, h)
    out, notes = [], []

    def clamp(points):
        return [[min(max(float(x), 0.0), w), min(max(float(y), 0.0), h)] for x, y in points]

    for i, poly in enumerate(polys):
        if not isinstance(poly, dict) or not _finite_xy(poly.get("exterior")) or \
                any(not _finite_xy(hh) for hh in (poly.get("holes") or [])):
            out.append(poly)
            continue
        ext, holes = clamp(poly["exterior"]), [clamp(hh) for hh in (poly.get("holes") or [])]
        moved = ext != [[float(x), float(y)] for x, y in poly["exterior"]] or \
            holes != [[[float(x), float(y)] for x, y in hh] for hh in (poly.get("holes") or [])]
        g = Polygon(ext, holes)
        if not moved and g.is_valid and g.area > 1.0:
            out.append(poly)                      # already fine: keep exactly as drawn (no new version on re-save)
            continue
        if moved:
            notes.append(f"{name}[{i}]: 사진 밖에 찍힌 점을 사진 테두리로 맞췄습니다")
        if not g.is_valid:
            g = shapely.make_valid(g)
            notes.append(f"{name}[{i}]: 꼬인(선이 교차하는) 폴리곤을 자동으로 고쳤습니다")
        g = g.intersection(frame)
        parts = [g] if isinstance(g, Polygon) else [q for q in getattr(g, "geoms", []) if isinstance(q, (Polygon, MultiPolygon))]
        flat = []
        for q in parts:
            flat += list(q.geoms) if isinstance(q, MultiPolygon) else [q]
        kept = [q for q in flat if q.area > 1.0]
        if not kept:
            notes.append(f"{name}[{i}]: 너무 작거나 선만 있는 폴리곤이라 뺐습니다")
            continue
        extra = {k: v for k, v in poly.items() if k not in ("exterior", "holes")}
        for q in kept:
            q = shapely.geometry.polygon.orient(q)
            out.append(dict(extra, exterior=[[round(x, 1), round(y, 1)] for x, y in list(q.exterior.coords)[:-1]],
                            holes=[[[round(x, 1), round(y, 1)] for x, y in list(r.coords)[:-1]] for r in q.interiors]))
    return out, notes


def polygons_geometry(polys: Iterable[dict]):
    from shapely.ops import unary_union
    parts = [polygon_geometry(p) for p in polys]
    return unary_union(parts) if parts else None


def validate_polygons(polys: list, size: tuple[int, int], name: str = "polygon",
                      tolerance: float = 0.5) -> list[str]:
    """Return a list of problems (empty == valid). Coordinates must lie in frame."""
    from shapely.geometry import box
    errors = []
    if not isinstance(polys, list):
        return [f"{name}: must be a list"]
    frame = box(-tolerance, -tolerance, size[0] + tolerance, size[1] + tolerance)
    for index, poly in enumerate(polys):
        try:
            geometry = polygon_geometry(poly)
        except ValueError as error:
            errors.append(f"{name}[{index}]: {error}")
            continue
        if not frame.contains(geometry):
            errors.append(f"{name}[{index}]: coordinates outside image {size[0]}x{size[1]}")
    return errors


def exclusion_polys(exclusions: list) -> list[dict]:
    return [{"exterior": e["points"], "holes": []} for e in exclusions or []]


def roi_effective_geometry(polygons: list, exclusions: list):
    """ROI union minus exclusion union (holes preserved)."""
    geometry = polygons_geometry(polygons)
    if geometry is None:
        return None
    excl = polygons_geometry(exclusion_polys(exclusions))
    if excl is not None:
        geometry = geometry.difference(excl)
    return geometry


def geometry_to_polys(geometry, min_area: float = 1.0) -> list[dict]:
    """shapely (Multi)Polygon/GeometryCollection -> list of polygon JSON."""
    if geometry is None or geometry.is_empty:
        return []
    out = []
    geoms = getattr(geometry, "geoms", [geometry])
    for g in geoms:
        if g.geom_type == "Polygon" and g.area >= min_area:
            out.append({"exterior": [[round(x, 2), round(y, 2)] for x, y in list(g.exterior.coords)[:-1]],
                        "holes": [[[round(x, 2), round(y, 2)] for x, y in list(r.coords)[:-1]]
                                  for r in g.interiors]})
        elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
            out.extend(geometry_to_polys(g, min_area))
    return out


def scale_polys(polys: list[dict], sx: float, sy: float) -> list[dict]:
    return [{"exterior": [[x * sx, y * sy] for x, y in p["exterior"]],
             "holes": [[[x * sx, y * sy] for x, y in h] for h in p.get("holes", [])]} for p in polys]


# ------------------------------------------------------------------- keys
def roi_key(target_id: str, view_id: str) -> str:
    return f"{target_id}|{view_id}"


def obs_key(image_id: str, target_id: str) -> str:
    return f"{image_id}|{target_id}"


def label_key(image_id: str, target_id: str, activity_id: str) -> str:
    return f"{image_id}|{target_id}|{activity_id}"


def applicability_key(target_id: str, activity_id: str) -> str:
    return f"{target_id}|{activity_id}"


def schema_header(kind: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "record_kind": kind}
