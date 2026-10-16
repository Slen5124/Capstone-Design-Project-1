"""Stage "roi_drafts": draft ROIs for views that have none, by per-target registration (pending item C).

For every active view and target without a ROI, the target's ROI of another
view (approved ROIs first) is projected onto this view's reference photo with
qa_v2.registration (one target at a time, never one shared transform). A
successful projection is saved as an UNAPPROVED ROI draft (source
auto_candidate, author auto) so the reviewer only has to check / adjust it
instead of drawing it. Failure (e.g. a very different fisheye viewpoint)
leaves the ROI missing; the to-do list then asks for a drawing or a copy.
Manual ROIs are never overwritten (store protection).
"""
from __future__ import annotations

from .assign_surfaces import register_roi
from .index_views import store_views
from .runctx import RunContext, atomic_write_json
from .schemas import geometry_to_polys, polygon_geometry
from .source_adapter import SourceIntegrityError, SourceReader
from .store import StoreError
from .target_registry import RegistryError, save_roi


def _clip(polys, excl, size):
    from shapely.geometry import box
    frame = box(0, 0, size[0], size[1])
    out = []
    for p in polys:
        out += geometry_to_polys(polygon_geometry(p).intersection(frame), min_area=100.0)
    ex = []
    for e in excl:
        for q in geometry_to_polys(polygon_geometry({"exterior": e["points"], "holes": []}).intersection(frame), 100.0):
            ex.append({"points": q["exterior"], "reason": e.get("reason")})
    return out, ex


def draft_missing_rois(ctx: RunContext, images: list[dict]) -> dict:
    store = ctx.store()
    by_id = {im["image_id"]: im for im in images}
    views = [v for v in store_views(store) if not v.get("retired")]
    targets = [r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"]
    rois = {r["key"]: r for r in store.latest("roi")}
    roi_states = store.review_states("roi")
    reader = SourceReader(ctx)
    created, failed, skipped = [], [], []
    try:
        for v in views:
            ref = by_id.get(v.get("reference_image_id"))
            if ref is None:
                skipped.append({"view_id": v["view_id"], "reason": "reference_not_in_run"})
                continue
            for t in targets:
                key = f"{t['key']}|{v['view_id']}"
                if key in rois:
                    continue
                sources = [r for k, r in rois.items() if k.startswith(t["key"] + "|") and k != key]
                sources.sort(key=lambda r: (roi_states.get(r["key"], {}).get("status") != "approved", -r["version"]))
                attempts = []
                for src in sources:
                    sref = by_id.get(src["payload"]["reference_image_id"])
                    if sref is None or list(src["payload"]["image_size"]) != list(ref["size"]):
                        attempts.append({"from": src["key"], "ok": False, "reasons": ["source_reference_unavailable"]})
                        continue
                    try:
                        reg = register_roi(reader, sref, ref, src["payload"], t["key"])
                    except SourceIntegrityError as error:
                        attempts.append({"from": src["key"], "ok": False, "reasons": [error.code]})
                        continue
                    attempts.append({"from": src["key"], "ok": reg["ok"], "reasons": reg.get("reasons", [])})
                    if not reg["ok"]:
                        continue
                    polys, excl = _clip(reg["polygons"], reg["exclusions"], ref["size"])
                    try:
                        save_roi(store, target_id=t["key"], view_id=v["view_id"], reference_image=ref, polygons=polys,
                                 exclusions=excl, author="auto", source="auto_candidate",
                                 note=f"registered from {src['key']} v{src['version']} (per-target homography, "
                                      f"max vertex shift {reg.get('max_vertex_shift_px')} px); check, adjust, approve")
                        created.append({"roi": key, "from": src["key"], "shift_px": reg.get("max_vertex_shift_px")})
                        rois[key] = store.get("roi", key)
                    except (RegistryError, StoreError) as error:
                        attempts[-1].update(ok=False, reasons=[f"save_failed: {str(error)[:120]}"])
                        continue
                    break
                if key not in rois:
                    failed.append({"roi": key, "attempts": attempts})
    finally:
        reader.close()
    report = {"created": created, "failed": failed, "skipped": skipped,
              "meaning": "unapproved ROI drafts; registration success says nothing about construction state"}
    atomic_write_json(ctx.stage_dir("views") / "roi_drafts.json", report)
    return report
