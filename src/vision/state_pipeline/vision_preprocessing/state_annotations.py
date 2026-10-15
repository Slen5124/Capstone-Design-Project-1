"""R06: activity/state labels per (image, target, activity) and training eligibility.

State labels are stored separately from the object polygons of the source
JSON (which are never modified). A label is drawn against a specific ROI
version; changing the ROI, target, view or applicability invalidates any
approval of the label (see store.py).

Training eligibility of (image, target, activity) requires ALL of:
  target approved with identity_status=confirmed; applicability approved and
  'applicable'; the ROI used for this photo approved (reference ROI + view, or
  a manual per-image observation); the state label approved; the label drawn
  against the ROI version currently used. Everything else stays ignore (255).
"""
from __future__ import annotations

from .schemas import (STATE_NAMES, applicability_key, label_key, obs_key, roi_effective_geometry, roi_key,
                      validate_polygons)


class LabelError(ValueError):
    pass


def validate_label(payload: dict, taxonomy: dict, image_size) -> list[str]:
    errors = []
    act = taxonomy["activities"].get(payload.get("activity_id"))
    if act is None:
        return [f"unknown activity {payload.get('activity_id')}"]
    if list(payload.get("image_size") or []) != list(image_size):
        errors.append("image_size must equal the photo size")
    if payload.get("coord_space") != "original_pixel_xy":
        errors.append("coord_space must be original_pixel_xy")
    states = set(act.get("states", STATE_NAMES))
    for i, region in enumerate(payload.get("regions", [])):
        if region.get("state") not in states:
            errors.append(f"regions[{i}].state must be one of {sorted(states)}")
        errors += validate_polygons(region.get("polygons", []), image_size, f"regions[{i}]")
    if payload.get("fill_roi_state") not in (None, *states):
        errors.append("fill_roi_state must be null or a state")
    reasons = set(taxonomy.get("undeterminable_reasons", []))
    for i, item in enumerate(payload.get("undeterminable", [])):
        if item.get("reason") not in reasons:
            errors.append(f"undeterminable[{i}].reason must be one of {sorted(reasons)}")
        errors += validate_polygons(item.get("polygons", []), image_size, f"undeterminable[{i}]")
    for i, item in enumerate(payload.get("occluded", [])):
        errors += validate_polygons(item.get("polygons", []), image_size, f"occluded[{i}]")
    whole = payload.get("whole_target_undeterminable")
    if whole is not None and whole not in reasons:
        errors.append("whole_target_undeterminable must be null or a reason")
    if not payload.get("roi_ref"):
        errors.append("roi_ref (ROI version the label was drawn against) is required")
    return errors


def label_dependencies(store, roi_ref: dict) -> list:
    """A label approval is bound to the exact ROI (or manual observation) version it was drawn on.

    Target identity, applicability and view approvals are checked at export time
    (``eligibility``) instead, so editing e.g. a target name does not invalidate labels,
    while any ROI geometry change does.
    """
    ref = store.get(roi_ref["kind"], roi_ref["key"])
    if ref is None:
        raise LabelError(f"ROI reference {roi_ref} not found")
    if ref["version"] != roi_ref["version"]:
        raise LabelError(f"ROI {roi_ref['key']} is now v{ref['version']}, label drawn on v{roi_ref['version']}")
    return [(roi_ref["kind"], roi_ref["key"], roi_ref["version"])]


def save_label(store, taxonomy: dict, image: dict, payload: dict, *, author: str, source: str = "manual",
               view_id: str | None = None) -> dict:
    target = store.get("target", payload["target_id"])
    if target is None:
        raise LabelError("unknown target")
    if target["payload"]["surface_type"] not in taxonomy["activities"][payload["activity_id"]]["surface_types"]:
        raise LabelError("activity does not apply to this target's surface type")
    payload = dict(payload, image_id=image["image_id"], coord_space="original_pixel_xy",
                   image_size=list(image["size"]))
    payload.setdefault("regions", [])
    payload.setdefault("undeterminable", [])
    payload.setdefault("occluded", [])
    payload.setdefault("fill_roi_state", None)
    errors = validate_label(payload, taxonomy, tuple(image["size"]))
    if errors:
        raise LabelError("; ".join(errors))
    if store.get("applicability", applicability_key(payload["target_id"], payload["activity_id"])) is None:
        raise LabelError("no applicability record for this target/activity")
    deps = label_dependencies(store, payload["roi_ref"])
    key = label_key(image["image_id"], payload["target_id"], payload["activity_id"])
    return store.put("state_label", key, payload, source=source, author=author, deps=deps)


def save_manual_observation(store, image: dict, target_id: str, payload: dict, author: str) -> dict:
    """Manual per-image ROI (e.g. confirmed registration result) and occlusion notes."""
    target = store.get("target", target_id)
    if target is None:
        raise LabelError("unknown target")
    payload = dict(payload, image_id=image["image_id"], target_id=target_id, coord_space="original_pixel_xy",
                   image_size=list(image["size"]))
    if payload.get("roi"):
        errors = validate_polygons(payload["roi"].get("polygons", []), tuple(image["size"]), "roi.polygons")
        if not payload["roi"].get("polygons"):
            errors.append("roi.polygons required")
        if errors:
            raise LabelError("; ".join(errors))
    return store.put("observation", obs_key(image["image_id"], target_id), payload, source="manual", author=author)


def roi_for_image(store, obs: dict | None) -> tuple[dict | None, dict | None]:
    """Return (roi_ref, roi geometry json) currently used for this observation."""
    if not obs or not obs.get("roi_ref") or not obs.get("roi"):
        return None, None
    return obs["roi_ref"], obs["roi"]


def eligibility(store, image: dict, obs: dict | None, target_id: str, activity_id: str) -> dict:
    reasons = []
    t = store.review_state("target", target_id)
    trec = store.get("target", target_id)
    if t["status"] != "approved":
        reasons.append(f"target_{t['status']}")
    if not trec or trec["payload"].get("identity_status") != "confirmed":
        reasons.append("target_identity_not_confirmed")
    akey = applicability_key(target_id, activity_id)
    arec = store.get("applicability", akey)
    astate = store.review_state("applicability", akey)
    if not arec or arec["payload"]["applicability"] != "applicable":
        reasons.append(f"applicability_{arec['payload']['applicability'] if arec else 'missing'}")
    if astate["status"] != "approved":
        reasons.append(f"applicability_{astate['status']}")
    roi_ref = obs.get("roi_ref") if obs else None
    if not obs or obs.get("status") != "ok" or not roi_ref:
        reasons.append(f"roi_{(obs or {}).get('status', 'missing')}")
    else:
        current = store.get(roi_ref["kind"], roi_ref["key"])
        if current is None or current["version"] != roi_ref["version"]:
            reasons.append("observation_stale_rerun_assign")
        if obs["roi_source"] in ("reference_image", "same_view_reuse"):
            if store.review_state("roi", roi_ref["key"])["status"] != "approved":
                reasons.append("roi_not_approved")
            if obs.get("view_id") and store.review_state("view", obs["view_id"])["status"] != "approved":
                reasons.append("view_not_approved")
        elif obs["roi_source"] == "manual":
            if store.review_state("observation", roi_ref["key"])["status"] != "approved":
                reasons.append("manual_observation_not_approved")
        else:
            reasons.append(f"roi_source_{obs['roi_source']}_requires_manual_confirmation")
    key = label_key(image["image_id"], target_id, activity_id)
    label = store.get("state_label", key)
    lstate = store.review_state("state_label", key)
    if label is None:
        reasons.append("state_unlabeled")
    else:
        if lstate["status"] != "approved":
            reasons.append(f"state_label_{lstate['status']}")
        if roi_ref and label["payload"].get("roi_ref") != roi_ref:
            reasons.append("label_drawn_on_different_roi_version")
    return {"eligible": not reasons, "reasons": reasons, "label": label, "label_review": lstate}


def propagate_label_draft(store, taxonomy: dict, src_image: dict, dst_image: dict, dst_obs: dict, target_id: str,
                          activity_id: str, author: str, note: str | None = None, draft_basis: str | None = None) -> dict:
    """Copy a label to another photo of the same view as a *propagated_draft*.

    Allowed only when both photos use the identical ROI version through the same
    view (identity transform). The copy must be reviewed; it is never approved
    here and it does not imply the state persisted between the two dates.
    """
    src = store.get("state_label", label_key(src_image["image_id"], target_id, activity_id))
    if src is None:
        raise LabelError("source label missing")
    if dst_obs.get("roi_source") not in ("same_view_reuse", "reference_image") or dst_obs.get("roi_ref") != src["payload"]["roi_ref"]:
        raise LabelError("propagation requires the same reference ROI version in the same view")
    if store.get("state_label", label_key(dst_image["image_id"], target_id, activity_id)) is not None:
        raise LabelError("destination already has a label")
    payload = {k: v for k, v in src["payload"].items() if k not in ("image_id", "note", "draft_basis", "propagated_from")}
    # Occlusion / undeterminable marks belong to the source photo (people, lifts and materials move),
    # so only the state regions are copied. Object-polygon occluders of THIS photo are applied at export.
    payload["occluded"] = []
    payload["undeterminable"] = []
    payload["whole_target_undeterminable"] = None
    payload["propagated_from"] = {"image_id": src_image["image_id"], "label_version": src["version"]}
    payload["note"] = note or "propagated draft: verify every region against THIS photo"
    if draft_basis:
        payload["draft_basis"] = draft_basis
    return save_label(store, taxonomy, dst_image, payload, author=author, source="propagated_draft",
                      view_id=dst_obs.get("view_id"))
