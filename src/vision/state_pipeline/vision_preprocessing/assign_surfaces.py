"""R04 + R05: per-image target observations (automatic drafts).

For every ingested image and every registered target:

1. ROI for this photo (R05)
   * manual per-image observation in the store            -> roi_source=manual
   * the view's reference image                           -> reference_image
   * another photo in the same (human-defined) view       -> same_view_reuse
     (optionally verified by per-target registration)
   * a ROI of the same target in a *different* view       -> registered_draft, using
     qa_v2.registration.project_regions **for this one target only**; failure
     leaves the observation pending with the recorded reasons. Different
     targets are never moved with one shared transform.
   * nothing                                              -> none (this is NOT "target absent")
2. Links between existing object polygons and the target (R04): geometric
   fractions of each compatible-label shape inside each target ROI. One target
   above ``link_min_shape_fraction`` -> link_candidate; several targets above
   ``cross_target_min_fraction`` -> split_candidate (review item). Nothing is
   auto-approved and missing polygons never create "absent"/"not installed".
   The union of detection masks is never used as the measurement ROI.
3. Observation drafts: occluder candidates (object polygons of occluder labels
   inside the ROI), coverage statistics and image-quality flags.

ROI propagation success says nothing about construction state. State labels
are never propagated by this module.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from .index_views import assign_view, store_views
from .legacy import project_regions
from .runctx import ItemCache, Progress, RunContext, atomic_write_json, read_jsonl
from .schemas import (content_hash, geometry_to_polys, obs_key, polygons_geometry, roi_effective_geometry,
                      roi_key, sha256_bytes)
from .source_adapter import SourceIntegrityError, SourceReader, load_images


def norm_label(value) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def load_shapes(doc: dict, size) -> list[dict]:
    """Shapely geometries for LabelMe polygon/rectangle shapes (measurement only)."""
    from shapely import make_valid
    from shapely.geometry import Polygon, box
    out = []
    for index, shape in enumerate(doc.get("shapes", [])):
        pts = shape.get("points") or []
        kind = shape.get("shape_type") or "polygon"
        try:
            if kind == "rectangle" and len(pts) == 2:
                (x1, y1), (x2, y2) = pts
                geometry = box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            elif kind == "polygon" and len(pts) >= 3:
                geometry = Polygon(pts)
            else:
                continue
            repaired = False
            if not geometry.is_valid:
                geometry = make_valid(geometry)
                geometry = geometry.buffer(0) if geometry.geom_type == "GeometryCollection" else geometry
                repaired = True
            if geometry.is_empty or geometry.area <= 0:
                continue
        except Exception:  # noqa: BLE001 - malformed coordinates are skipped and counted
            continue
        out.append({"index": index, "label": norm_label(shape.get("label")), "raw_label": shape.get("label"),
                    "geometry": geometry, "area": geometry.area, "repaired": repaired,
                    "points_sha1": sha256_bytes(str(pts).encode())[:16],
                    "description": shape.get("description"), "group_id": shape.get("group_id")})
    return out


def quality_metrics(gray: np.ndarray, mask: np.ndarray | None = None) -> dict:
    import cv2
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    sel = mask if mask is not None else np.ones(gray.shape, bool)
    if sel.sum() < 50:
        return {"pixels": int(sel.sum())}
    values = gray[sel]
    return {"pixels": int(sel.sum()), "mean": round(float(values.mean()), 1),
            "overexposed_fraction": round(float((values >= 250).mean()), 4),
            "dark_fraction": round(float((values <= 15).mean()), 4),
            "laplacian_var": round(float(lap[sel].var()), 1)}


def quality_flags(metrics: dict, cfg: dict) -> list[str]:
    flags = []
    if "mean" not in metrics:
        return flags
    if metrics["mean"] < cfg["low_light_mean"]:
        flags.append("low_light_candidate")
    if metrics["overexposed_fraction"] > cfg["overexposed_fraction"]:
        flags.append("overexposed_candidate")
    if metrics["laplacian_var"] < cfg["blur_laplacian_var"]:
        flags.append("blur_or_low_texture_candidate")
    return flags


def _roi_regions_for_registration(roi_payload: dict, target_id: str) -> list[dict]:
    """One registration region per ROI polygon (holes -> exclusions). Same target plane."""
    regions = []
    for index, poly in enumerate(roi_payload["polygons"]):
        regions.append({"id": f"{target_id}#{index}", "label": roi_payload["surface_type"], "points": poly["exterior"],
                        "exclusions": list(poly.get("holes") or []) + [e["points"] for e in roi_payload.get("exclusions", [])],
                        "n_holes": len(poly.get("holes") or [])})
    return regions


def register_roi(reader: SourceReader, ref_image: dict, image: dict, roi_payload: dict, target_id: str) -> dict:
    """Project one target's ROI with per-region homographies; all parts must pass."""
    ref_rgb, _ = reader.rgb(ref_image)
    tgt_rgb, _ = reader.rgb(image)
    regions = _roi_regions_for_registration(roi_payload, target_id)
    result = project_regions(ref_rgb, tgt_rgb, [{k: v for k, v in r.items() if k != "n_holes"} for r in regions])
    evidence = [{"id": e["id"], "status": e["status"], "reasons": e["reasons"],
                 "metrics": {k: e["metrics"].get(k) for k in ("matches", "inliers", "inlier_ratio",
                             "reprojection_median_px", "reprojection_p95_px", "homography_resized",
                             "reference_resize_xy", "target_resize_xy") if k in e["metrics"]}}
                for e in result["evidence"]]
    if not result["accepted"]:
        return {"ok": False, "evidence": evidence, "reasons": sorted({r for e in evidence for r in e["reasons"]})}
    polygons, exclusions = [], []
    n_excl = len(roi_payload.get("exclusions", []))
    for region, projected in zip(regions, result["regions"]):
        holes = projected["exclusions"][:region["n_holes"]]
        polygons.append({"exterior": projected["points"], "holes": holes})
        if not exclusions and n_excl:
            exclusions = [{"points": pts, "reason": e.get("reason")} for pts, e in
                          zip(projected["exclusions"][region["n_holes"]:], roi_payload["exclusions"])]
    shift = max(float(np.max(np.linalg.norm(np.asarray(p["exterior"], float) - np.asarray(q["exterior"], float), axis=1)))
                for p, q in zip(roi_payload["polygons"], polygons))
    return {"ok": True, "evidence": evidence, "polygons": polygons, "exclusions": exclusions,
            "max_vertex_shift_px": round(shift, 2), "visibility_not_verified": True}


ASSIGN_CODE = ("assign_surfaces.py", "schemas.py", "legacy.py", "index_views.py", "source_adapter.py",
               "qa_v2/registration.py", "qa_v3/dataset_io.py")


def open_observation_cache(ctx: RunContext) -> ItemCache:
    return ItemCache(ctx.stage_dir("assign") / "observations.jsonl")


def build_observations(ctx: RunContext, images: list[dict] | None = None, limit: int = 0,
                       cache: ItemCache | None = None, quiet: bool = False) -> dict:
    """Compute observation drafts. Pass ``cache`` (open_observation_cache) to reuse one in-memory cache
    across many small calls (review server); otherwise the cache file is opened, updated and compacted."""
    store = ctx.store()
    site, tax = ctx.site, ctx.taxonomy
    acfg = dict({"link_min_shape_fraction": 0.8, "cross_target_min_fraction": 0.05, "min_shape_area_px": 25,
                 "register_cross_view": True, "verify_same_view_registration": False, "same_view_max_shift_px": 6.0},
                **site.get("assign", {}))
    qcfg = dict({"analysis_max_side": 1024, "low_light_mean": 45, "overexposed_fraction": 0.25,
                 "blur_laplacian_var": 40}, **site.get("quality", {}))
    images = images if images is not None else load_images(ctx)
    images = [im for im in images if im.get("duplicate_of") is None]
    if limit:
        images = images[:limit]
    by_id = {im["image_id"]: im for im in load_images(ctx, include_held=True)}
    views = store_views(store)
    targets = [r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"]
    rois = {r["key"]: r for r in store.latest("roi")}
    surface_labels = {s: {norm_label(l) for l in d["object_labels"]} for s, d in tax["surface_types"].items()}
    occluders = {norm_label(l) for l in tax.get("occluder_labels", {}).get("labels", [])}
    stage = ctx.stage_dir("assign")
    own_cache = cache is None
    cache = cache if cache is not None else open_observation_cache(ctx)
    fp_cfg = ctx.config_fingerprint("assign", "quality", code=ASSIGN_CODE)
    reader = SourceReader(ctx)
    progress = Progress(ctx if not quiet else None, "assign", len(images), "images", every=50 if quiet else 10)
    if quiet:
        progress.report = lambda final=False: None  # type: ignore[assignment]
    summary = defaultdict(int)
    try:
        for image in images:
            vinfo = assign_view(image, views)
            fp_inputs = [fp_cfg, image["source_photo"]["sha256"], (image["annotation"] or {}).get("sha256"), vinfo,
                         sorted((k, r["version"], r["content_hash"]) for k, r in rois.items()),
                         sorted((t["key"], t["version"]) for t in targets),
                         sorted((r["key"], r["version"]) for r in store.latest("observation", image["image_id"] + "|"))]
            fp = content_hash(fp_inputs)
            if all(cache.get(obs_key(image["image_id"], t["key"]), fp) is not None for t in targets) and targets:
                progress.tick("skipped_cached")
                continue
            try:
                obs = _observe_image(image, vinfo, targets, rois, store, by_id, reader, acfg, qcfg,
                                     surface_labels, occluders)
            except SourceIntegrityError as error:
                obs = [{"image_id": image["image_id"], "target_id": t["key"], "status": "held_source_error",
                        "reasons": [error.code]} for t in targets]
            for o in obs:
                cache.put(obs_key(image["image_id"], o["target_id"]), fp, o)
                summary[o["status"]] += 1
                summary["roi_source:" + str(o.get("roi_source"))] += 1
            progress.tick("held" if any(o["status"].startswith("held") for o in obs) else "saved")
    finally:
        if own_cache:
            cache.close()
            cache.maybe_compact()
        reader.close()
    counts = progress.finish()
    report = {"images": counts, "observations_by_status": dict(summary), "targets": [t["key"] for t in targets],
              "views": [v["view_id"] for v in views], "meaning": "automatic drafts; no approvals implied"}
    if not quiet:
        atomic_write_json(stage / "assign_report.json", report)
    return report


def _observe_image(image, vinfo, targets, rois, store, by_id, reader, acfg, qcfg, surface_labels, occluders):
    import cv2
    from shapely.strtree import STRtree
    image_id = image["image_id"]
    results, geoms = [], {}
    for trec in targets:
        tid, tp = trec["key"], trec["payload"]
        o = {"image_id": image_id, "target_id": tid, "surface_type": tp["surface_type"],
             "view_id": vinfo["view_id"], "view_status": vinfo["status"], "view_review": vinfo.get("view_review"),
             "roi_source": "none", "roi_ref": None, "roi": None, "transform": None, "status": "no_roi",
             "reasons": [], "review_status": "draft"}
        manual = store.get("observation", obs_key(image_id, tid))
        roi_rec = rois.get(roi_key(tid, vinfo["view_id"])) if vinfo["view_id"] else None
        if manual and manual["payload"].get("roi"):
            o.update(roi_source="manual", roi=manual["payload"]["roi"],
                     roi_ref={"kind": "observation", "key": manual["key"], "version": manual["version"]},
                     status="ok", transform={"type": "manual"})
        elif roi_rec is not None:
            p = roi_rec["payload"]
            o["roi_ref"] = {"kind": "roi", "key": roi_rec["key"], "version": roi_rec["version"]}
            o["roi"] = {"polygons": p["polygons"], "exclusions": p["exclusions"]}
            if list(p["image_size"]) != list(image["size"]):
                o.update(status="held_size_mismatch", reasons=["roi_reference_size_differs"], roi=None)
            elif p["reference_image_id"] == image_id:
                o.update(roi_source="reference_image", status="ok", transform={"type": "identity"})
            else:
                o.update(roi_source="same_view_reuse", status="ok",
                         transform={"type": "identity", "assumption": "static camera within the human-defined view"})
                if acfg["verify_same_view_registration"] and p["reference_image_id"] in by_id:
                    reg = register_roi(reader, by_id[p["reference_image_id"]], image, p, tid)
                    shift = reg.get("max_vertex_shift_px")
                    o["transform"]["verification"] = {"ok": reg["ok"], "shift_px": shift, "reasons": reg.get("reasons")}
                    if not reg["ok"]:
                        o.update(status="held_same_view_registration_failed", reasons=reg["reasons"])
                    elif shift is not None and shift > acfg["same_view_max_shift_px"]:
                        o.update(status="held_roi_shift_detected", reasons=[f"shift_px={shift:.1f}"])
            if vinfo.get("view_review") != "approved":
                o["reasons"].append("view_not_approved")
        elif acfg["register_cross_view"]:
            others = [r for k, r in rois.items() if k.startswith(tid + "|")]
            if others:
                attempts = []
                for other in others:
                    p = other["payload"]
                    ref = by_id.get(p["reference_image_id"])
                    if ref is None or list(p["image_size"]) != list(image["size"]):
                        attempts.append({"roi": other["key"], "ok": False, "reasons": ["reference_unavailable"]})
                        continue
                    reg = register_roi(reader, ref, image, p, tid)
                    attempts.append({"roi": other["key"], "ok": reg["ok"], "reasons": reg.get("reasons", []),
                                     "evidence": reg["evidence"]})
                    if reg["ok"]:
                        o.update(roi_source="registered_draft", status="ok",
                                 roi={"polygons": reg["polygons"], "exclusions": reg["exclusions"]},
                                 roi_ref={"kind": "roi", "key": other["key"], "version": other["version"]},
                                 transform={"type": "per_target_homography", "evidence": reg["evidence"],
                                            "max_vertex_shift_px": reg["max_vertex_shift_px"],
                                            "visibility_not_verified": True},
                                 reasons=["registered_roi_requires_review"])
                        break
                if o["roi_source"] == "none":
                    o.update(status="roi_pending_registration_failed",
                             reasons=sorted({r for a in attempts for r in a["reasons"]}))
                o["registration_attempts"] = [{k: v for k, v in a.items() if k != "evidence"} for a in attempts]
        if o["roi"] is not None and o["status"] == "ok":
            try:
                g = roi_effective_geometry(o["roi"]["polygons"], o["roi"].get("exclusions", []))
                if g is None or g.is_empty:
                    raise ValueError("empty_roi")
                geoms[tid] = g
            except ValueError as error:
                o.update(status="held_invalid_roi", reasons=[str(error)])
        results.append(o)
    if not geoms:
        return results
    # ---- polygon links and coverage (R04) ----
    doc = reader.annotation(image)
    shapes = [s for s in load_shapes(doc, image["size"]) if s["area"] >= acfg["min_shape_area_px"]]
    tree = STRtree([s["geometry"] for s in shapes]) if shapes else None
    rgb, scale = reader.rgb(image, qcfg["analysis_max_side"])
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    image_quality = quality_metrics(gray)
    fractions: dict[int, dict[str, float]] = defaultdict(dict)
    for tid, g in geoms.items():
        if tree is None:
            break
        for i in tree.query(g):
            s = shapes[int(i)]
            inter = s["geometry"].intersection(g).area
            if inter > 0:
                fractions[int(i)][tid] = inter / s["area"]
    by_target = {o["target_id"]: o for o in results}
    for o in results:
        if o["target_id"] in geoms:
            o.update(links=[], review_items=[], coverage={}, occlusion_candidates=[], quality={})
    for i, fr in fractions.items():
        s = shapes[i]
        for tid in list(fr):
            if s["label"] not in surface_labels.get(by_target[tid]["surface_type"], set()):
                fr.pop(tid)  # only compatible object labels can link to a target
        if not fr:
            continue
        above_cross = {t: f for t, f in fr.items() if f >= acfg["cross_target_min_fraction"]}
        ref = {"shape_index": s["index"], "label": s["raw_label"], "points_sha1": s["points_sha1"],
               "area_px": round(s["area"], 1), "fractions": {t: round(f, 4) for t, f in fr.items()},
               "geometry_repaired_for_measurement": s["repaired"]}
        if len(above_cross) >= 2:
            for t in above_cross:
                by_target[t]["review_items"].append(dict(ref, kind="split_candidate",
                                                         note="shape spans several targets; split or assign in review"))
        elif len(above_cross) == 1:
            t, f = next(iter(above_cross.items()))
            kind = "link_candidate" if f >= acfg["link_min_shape_fraction"] else "partial_overlap"
            if kind == "link_candidate":
                # slim link record (kept for every photo, so size matters with 10k+ photos x targets)
                by_target[t]["links"].append({"shape_index": s["index"], "label": s["raw_label"], "fraction": round(f, 3)})
            else:
                by_target[t]["review_items"].append(dict(ref, kind=kind))
    from shapely.ops import unary_union
    for tid, g in geoms.items():
        o = by_target[tid]
        roi_area = g.area
        same, occl, other = [], [], []
        if tree is not None:
            for i in tree.query(g):
                s = shapes[int(i)]
                if s["label"] in surface_labels.get(o["surface_type"], set()):
                    same.append(s["geometry"])
                elif s["label"] in occluders:
                    occl.append(s["geometry"])
                else:
                    other.append(s["geometry"])
        cov = {}
        for name, items in (("same_surface_label", same), ("occluder_labels", occl), ("other_labels", other)):
            u = unary_union(items).intersection(g) if items else None
            cov[name] = round((u.area if u is not None else 0.0) / roi_area, 4)
            if name == "occluder_labels" and u is not None and not u.is_empty:
                o["occlusion_candidates"] = [{"source": "object_polygon_occluder_labels", "status": "draft",
                                              "polygons": _round_polys(geometry_to_polys(u.simplify(1.5), min_area=20.0))}]
        cov["roi_area_px"] = round(roi_area, 1)
        cov["note"] = "Fractions of ROI area covered by existing object polygons; not state, not visibility."
        o["coverage"] = cov
        o["link_count"] = len(o["links"])
        o["review_item_count"] = len(o["review_items"])
        if len(o["review_items"]) > 40:
            o["review_items"] = sorted(o["review_items"], key=lambda r: -r["area_px"])[:40]
            o["review_items_truncated"] = True
        mask = _small_mask(g, gray.shape, scale)
        roi_q = quality_metrics(gray, mask)
        o["quality"] = {"image": image_quality, "roi": roi_q,
                        "flags": sorted(set(quality_flags(image_quality, qcfg)) | set(quality_flags(roi_q, qcfg))),
                        "status": "unvalidated_candidate_flags"}
    return results


def _round_polys(polys: list[dict]) -> list[dict]:
    r = lambda pts: [[round(x, 1), round(y, 1)] for x, y in pts]  # noqa: E731
    return [{"exterior": r(p["exterior"]), "holes": [r(h) for h in p.get("holes", [])]} for p in polys]


def _small_mask(geometry, shape, scale):
    from PIL import Image, ImageDraw
    img = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(img)
    for g in getattr(geometry, "geoms", [geometry]):
        if g.geom_type != "Polygon":
            continue
        draw.polygon([(x * scale, y * scale) for x, y in g.exterior.coords], fill=1)
        for r in g.interiors:
            draw.polygon([(x * scale, y * scale) for x, y in r.coords], fill=0)
    return np.asarray(img, bool)


def load_observations(ctx: RunContext, cache: ItemCache | None = None) -> dict[str, dict]:
    if cache is not None:
        return {k: row["value"] for k, row in cache.items.items()}
    path = ctx.stage_dir("assign") / "observations.jsonl"
    out = {}
    for row in read_jsonl(path):
        out[row["_key"]] = row["value"]
    return out
