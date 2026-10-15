"""R03: physical targets (W01/F01/C01...), activity applicability, reference ROIs.

* Targets are site-level records, created once (from the site config or the
  review UI) — never per photo. The store is authoritative after creation:
  later config differences are reported, not applied.
* A reference ROI belongs to (target_id, view_id) and is drawn on that view's
  reference image in original pixel coordinates. It is the *measurement ROI*
  (the full surface/work zone, including not-yet-installed and occluded
  parts). Openings that are not part of the work are explicit exclusions.
* qa_v3 teacher regions are "visible installed segments" and are offered only
  as display hints, never as ROIs.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schemas import (APPLICABILITY, SURFACE_TYPES, TARGET_IDENTITY, applicability_key, roi_effective_geometry,
                      roi_key, validate_polygons)


class RegistryError(ValueError):
    pass


def compatible_activities(surface_type: str, taxonomy: dict) -> list[str]:
    return [a for a, d in taxonomy["activities"].items() if surface_type in d["surface_types"]]


def sync_from_config(store, site: dict, taxonomy: dict, author: str = "config") -> dict:
    report = {"targets_created": [], "targets_differ_from_config": [], "applicability_created": []}
    for t in site.get("targets", []):
        payload = {"target_id": t["target_id"], "site_id": site["site_id"], "surface_type": t["surface_type"],
                   "zone_id": t.get("zone_id"), "name": t.get("name", t["target_id"]),
                   "identity_status": t.get("identity_status", "proposed"),
                   "identity_note": t.get("identity_note", "Proposed in site config; confirm physical identity.")}
        current = store.get("target", t["target_id"])
        if current is None:
            store.put("target", t["target_id"], payload, source="config", author=author)
            report["targets_created"].append(t["target_id"])
        elif {k: current["payload"].get(k) for k in payload} != payload:
            report["targets_differ_from_config"].append(t["target_id"])
        for act in compatible_activities(t["surface_type"], taxonomy):
            key = applicability_key(t["target_id"], act)
            if store.get("applicability", key) is None:
                item = (t.get("activities") or {}).get(act) or {}
                store.put("applicability", key, {
                    "target_id": t["target_id"], "activity_id": act,
                    "applicability": item.get("applicability", "unknown"),
                    "basis": item.get("basis", "not stated"), "evidence_image_ids": item.get("evidence_image_ids", [])},
                    source="config", author=author)
                report["applicability_created"].append(key)
    return report


def save_target(store, payload: dict, author: str) -> dict:
    if payload.get("surface_type") not in SURFACE_TYPES:
        raise RegistryError(f"surface_type must be one of {SURFACE_TYPES}")
    if payload.get("identity_status") not in TARGET_IDENTITY:
        raise RegistryError(f"identity_status must be one of {TARGET_IDENTITY}")
    current = store.get("target", payload["target_id"])
    if current and current["payload"]["surface_type"] != payload["surface_type"]:
        raise RegistryError("surface_type of an existing target cannot change; create a new target instead")
    return store.put("target", payload["target_id"], payload, source="manual", author=author)


def save_applicability(store, target_id: str, activity_id: str, applicability: str, basis: str, author: str,
                       taxonomy: dict) -> dict:
    target = store.get("target", target_id)
    if target is None:
        raise RegistryError(f"unknown target {target_id}")
    if activity_id not in compatible_activities(target["payload"]["surface_type"], taxonomy):
        raise RegistryError(f"{activity_id} does not apply to surface {target['payload']['surface_type']}")
    if applicability not in APPLICABILITY:
        raise RegistryError(f"applicability must be one of {APPLICABILITY}")
    if not (basis or "").strip():
        raise RegistryError("a basis (근거) is required")
    payload = {"target_id": target_id, "activity_id": activity_id, "applicability": applicability, "basis": basis}
    return store.put("applicability", applicability_key(target_id, activity_id), payload,
                     source="manual", author=author)


def validate_roi(payload: dict, image_size: tuple[int, int]) -> list[str]:
    errors = []
    if payload.get("coord_space") != "original_pixel_xy":
        errors.append("coord_space must be original_pixel_xy")
    if list(payload.get("image_size") or []) != list(image_size):
        errors.append(f"image_size {payload.get('image_size')} != reference image {list(image_size)}")
    if not payload.get("polygons"):
        errors.append("ROI needs at least one polygon")
    errors += validate_polygons(payload.get("polygons", []), image_size, "polygons")
    errors += validate_polygons([{"exterior": e.get("points"), "holes": []} for e in payload.get("exclusions", [])],
                                image_size, "exclusions")
    for e in payload.get("exclusions", []):
        if not (e.get("reason") or "").strip():
            errors.append("each exclusion needs a reason (e.g. door_opening)")
    if not errors:
        geometry = roi_effective_geometry(payload["polygons"], payload.get("exclusions", []))
        if geometry is None or geometry.is_empty or geometry.area < 100:
            errors.append("ROI minus exclusions is empty or tiny")
    return errors


def save_roi(store, *, target_id: str, view_id: str, reference_image: dict, polygons: list, exclusions: list,
             author: str, note: str | None = None, source: str = "manual") -> dict:
    target = store.get("target", target_id)
    view = store.get("view", view_id)
    if target is None or view is None:
        raise RegistryError("unknown target or view")
    payload = {"target_id": target_id, "view_id": view_id, "reference_image_id": reference_image["image_id"],
               "reference_image_sha256": reference_image["source_photo"].get("sha256"),
               "image_size": list(reference_image["size"]), "coord_space": "original_pixel_xy",
               "surface_type": target["payload"]["surface_type"],
               "polygons": polygons, "exclusions": exclusions, "roi_kind": "measurement_roi"}
    errors = validate_roi(payload, tuple(reference_image["size"]))
    if errors:
        raise RegistryError("; ".join(errors))
    # ROI approvals depend only on the ROI itself. Target/view approval is checked separately at
    # export time, so renaming a target or confirming its identity does not invalidate geometry.
    return store.put("roi", roi_key(target_id, view_id), payload, source=source, author=author, note=note)


def roi_hints(ctx, images_by_entry: dict) -> list[dict]:
    """qa_v3 teacher regions as display-only hints (normalized -> pixel)."""
    cfg = ctx.site.get("roi_hints") or {}
    rel = ctx.site.get("paths", {}).get("teacher_surfaces")
    if not rel:
        return []
    path = Path(ctx.preprocess_root) / rel
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    wanted = set(cfg.get("teacher_reference_ids") or [])
    out = []
    for ref in data.get("references", []):
        if wanted and ref.get("id") not in wanted:
            continue
        image = images_by_entry.get(ref.get("source_entry"))
        for region in ref.get("regions", []):
            item = {"hint_id": region.get("id"), "teacher_reference_id": ref.get("id"), "label": region.get("label"),
                    "source_entry": ref.get("source_entry"), "image_id": image["image_id"] if image else None,
                    "meaning": "qa_v3 assistant draft of a visible installed segment; NOT a measurement ROI",
                    "human_confirmed": data.get("human_confirmed_ground_truth", False)}
            if image and data.get("coordinate_space", "").startswith("normalized"):
                w, h = image["size"]
                item["points"] = [[x * w, y * h] for x, y in region.get("points", [])]
                item["exclusions"] = [[[x * w, y * h] for x, y in ex] for ex in region.get("exclusions", [])]
            out.append(item)
    return out
