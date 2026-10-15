"""Daily camera-drift check inside each view (item 8) and severe-shake report (item 7).

For every view and every day, up to ``frames_per_day`` well-lit photos spread
over the day are compared with the view's reference photo (global SIFT/RANSAC
homography, mean image-corner displacement / image width). Only camera motion
is measured; nothing is moved or approved here.

Per day:
  shift <  ignore_below            -> ok (ROI reused unchanged)
  ignore_below .. redraw_above     -> minor_drift (ROI reused; day flagged)
  shift >= redraw_above            -> redraw_requested (to-do for the reviewer:
                                      redraw / adjust the ROI from this day, i.e.
                                      a new view starting here)
  no reliable match on any frame   -> unmatched (lighting / occlusion / big change;
                                      reported, not a move by itself)
  spread of shifts within the day >= shake_spread -> severe_shake reported
The thresholds are unvalidated engineering defaults (config "drift").
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .index_views import _Features, assign_view, store_views
from .runctx import ItemCache, Progress, RunContext, atomic_write_json
from .schemas import content_hash
from .source_adapter import SourceIntegrityError, SourceReader

DEFAULTS = {"frames_per_day": 3, "ignore_below": 0.005, "redraw_above": 0.015, "shake_spread": 0.015,
            "consecutive_days_for_move": 2, "max_side": 960, "min_inliers": 40, "min_inlier_ratio": 0.4,
            "same_view_max_corner_shift": 0.012, "move_min_corner_shift": 0.03,
            "status": "unvalidated engineering defaults"}


def _spread_pick(items: list[dict], k: int) -> list[dict]:
    if len(items) <= k:
        return items
    idx = np.linspace(0, len(items) - 1, k).round().astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def check_drift(ctx: RunContext, images: list[dict], light: dict[str, str] | None = None,
                reader: SourceReader | None = None) -> dict:
    cfg = dict(DEFAULTS, **ctx.site.get("drift", {}))
    store = ctx.store()
    views = store_views(store)
    by_id = {im["image_id"]: im for im in images}
    light = light or {}
    reader = reader or SourceReader(ctx)
    feats = _Features(cfg["max_side"])
    stage = ctx.stage_dir("drift")
    cache = ItemCache(stage / "drift_checks.jsonl")
    fp_cfg = content_hash({k: cfg[k] for k in ("max_side", "min_inliers", "min_inlier_ratio")})
    per_view_days: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for im in images:
        if im.get("duplicate_of") or im["status"] != "ok" or light.get(im["image_id"]) not in (None, "ok"):
            continue
        v = assign_view(im, views)
        if v["view_id"]:
            per_view_days[v["view_id"]][im["capture"]["time_local"][:10]].append(im)
    jobs, surrogate = [], {}
    for vid, days in per_view_days.items():
        view = next(v for v in views if v["view_id"] == vid)
        ref = by_id.get(view.get("reference_image_id"))
        if ref is None:
            # reference photo not part of this run (e.g. a limited test run): use a member photo instead
            members = sorted((im for ims in days.values() for im in ims), key=lambda im: im["capture"]["time_local"])
            ref = members[len(members) // 2]
            surrogate[vid] = ref["image_id"]
        for day, ims in sorted(days.items()):
            ims.sort(key=lambda im: im["capture"]["time_local"])
            for im in _spread_pick(ims, cfg["frames_per_day"]):
                jobs.append((vid, day, ref, im))
    progress = Progress(ctx, "drift", len(jobs), "comparisons", every=50)
    ref_feat: dict[str, dict] = {}
    results: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    try:
        for vid, day, ref, im in jobs:
            key = f"{ref['image_id']}>{im['image_id']}"
            fp = content_hash([fp_cfg, ref["source_photo"]["sha256"], im["source_photo"]["sha256"]])
            hit = cache.get(key, fp)
            if hit is None:
                try:
                    if ref["image_id"] not in ref_feat:
                        ref_feat[ref["image_id"]] = feats.compute(reader.rgb(ref, cfg["max_side"])[0])
                    if im["image_id"] == ref["image_id"]:
                        hit = {"result": "same", "corner_shift": 0.0, "reason": "reference"}
                    else:
                        hit = feats.compare(ref_feat[ref["image_id"]], feats.compute(reader.rgb(im, cfg["max_side"])[0]), cfg)
                except SourceIntegrityError as error:
                    hit = {"result": "uncertain", "reason": error.code, "corner_shift": None}
                cache.put(key, fp, hit)
                progress.tick("saved")
            else:
                progress.tick("skipped_cached")
            results[vid][day].append({"image_id": im["image_id"], "time": im["capture"]["time_local"],
                                      "corner_shift": hit.get("corner_shift"), "result": hit.get("result"),
                                      "reason": hit.get("reason"), "inliers": hit.get("inliers")})
    finally:
        cache.close()
        cache.maybe_compact()
    report_views, todo, shakes = {}, [], []
    for vid, days in sorted(results.items()):
        rows = []
        for day, checks in sorted(days.items()):
            shifts = [c["corner_shift"] for c in checks if c["corner_shift"] is not None]
            med = float(np.median(shifts)) if shifts else None
            spread = (max(shifts) - min(shifts)) if len(shifts) >= 2 else 0.0
            if med is None:
                status = "unmatched"
            elif med >= cfg["redraw_above"]:
                status = "redraw_requested"
            elif med >= cfg["ignore_below"]:
                status = "minor_drift"
            else:
                status = "ok"
            row = {"day": day, "status": status, "median_shift": None if med is None else round(med, 4),
                   "spread": round(spread, 4), "checks": checks}
            if spread >= cfg["shake_spread"]:
                row["severe_shake"] = True
                shakes.append({"view_id": vid, "day": day, "spread": round(spread, 4)})
            rows.append(row)
        # a redraw request is raised once per run of consecutive drifted days
        run = []
        for row in rows + [None]:
            if row is not None and row["status"] == "redraw_requested":
                run.append(row)
                continue
            if run:
                likely_move = len(run) >= cfg["consecutive_days_for_move"]
                todo.append({"kind": "redraw_roi", "view_id": vid, "from_day": run[0]["day"], "to_day": run[-1]["day"],
                             "median_shift": max(r["median_shift"] for r in run),
                             "likely_camera_move": likely_move,
                             "example_image_id": run[0]["checks"][0]["image_id"]})
                run = []
        report_views[vid] = {"days": rows, "counts": _count(r["status"] for r in rows)}
        if vid in surrogate:
            report_views[vid]["surrogate_reference"] = surrogate[vid]
    report = {"config": cfg, "views": report_views, "redraw_requests": todo, "severe_shakes": shakes,
              "comparisons": progress.finish(),
              "meaning": "camera motion against the view reference only; says nothing about construction state"}
    atomic_write_json(stage / "drift_report.json", report)
    return report


def drift_status_by_image(ctx: RunContext) -> dict[str, dict]:
    """{(view_id, day): status} lookup from the last drift report (empty if not run)."""
    path = ctx.stage_dir("drift") / "drift_report.json"
    if not path.exists():
        return {}
    import json
    rep = json.loads(path.read_text(encoding="utf-8"))
    return {f"{vid}|{r['day']}": {"status": r["status"], "median_shift": r["median_shift"]}
            for vid, v in rep["views"].items() for r in v["days"]}


def _count(items) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
