"""Stage D: per-target state timeline CANDIDATES (where installation probably changed).

Inputs: thumbnails (stage thumbs), the latest ROI of every (target, view) and
the human-approved state labels ("anchors"). For every (view, target, day):

1. ROI appearance descriptor per photo: a grid over the ROI (thumbnail scale)
   with per-cell mean L*a*b* (L relative to the whole-frame brightness, to
   damp exposure changes) and edge density. Only well-lit photos are used.
2. Day descriptor = median over the day's photos; change score = robust
   distance to the previous valid day, normalised by the median absolute
   deviation of all scores of this (view, target)  -> z-score.
3. Anchors: installed fraction inside the ROI of approved labels (per day).
4. Days are classified:
     stable          between two anchors with the same installed fraction and
                     no strong change in between (state_hint = that fraction)
     transition      between two anchors that differ, or a strong change day
     stable_extrap.  before the first / after the last anchor until a strong
                     change (lower confidence)
     unknown         change detected after the last / before the first anchor
     unanchored      no approved label for this target yet (no knowledge, not change)
   Strong change days are listed as likely change points.

These are candidates for choosing labelling samples and drafts. They are not
labels and not progress values; the method is unvalidated (a self-check
against the anchors is reported).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .index_views import assign_view, store_views
from .rasterize import roi_mask, state_and_visibility
from .runctx import RunContext, atomic_write_json
from .schemas import scale_polys
from .thumbs import THUMB_H, THUMB_W, load_thumb, load_thumb_stats

DEFAULTS = {"grid": [8, 6], "min_cell_px": 12, "z_change": 3.5, "abs_change": 6.0, "same_fraction_tol": 0.05,
            "light_classes": ["ok"], "max_photos_per_day": 12, "status": "unvalidated engineering defaults"}


def _scale_label(payload: dict, sx: float, sy: float) -> dict:
    out = dict(payload)
    out["regions"] = [dict(r, polygons=scale_polys(r["polygons"], sx, sy)) for r in payload.get("regions", [])]
    out["undeterminable"] = [dict(u, polygons=scale_polys(u["polygons"], sx, sy)) for u in payload.get("undeterminable", [])]
    out["occluded"] = [dict(o, polygons=scale_polys(o["polygons"], sx, sy)) for o in payload.get("occluded", [])]
    return out


def _scale_roi(roi: dict, sx: float, sy: float) -> dict:
    return {"polygons": scale_polys(roi["polygons"], sx, sy),
            "exclusions": [dict(e, points=[[x * sx, y * sy] for x, y in e["points"]]) for e in roi.get("exclusions", [])]}


def installed_fraction(payload: dict, roi: dict, image_size) -> float | None:
    """Installed share of the determinable ROI area of one label (thumbnail resolution)."""
    sx, sy = THUMB_W / image_size[0], THUMB_H / image_size[1]
    mask = roi_mask(_scale_roi(roi, sx, sy), (THUMB_W, THUMB_H))
    state, _, stats = state_and_visibility(_scale_label(payload, sx, sy), mask, (THUMB_W, THUMB_H))
    valid = stats["valid_px"]
    if valid < 0.2 * max(1, stats["roi_px"]):
        return None
    return stats["installed_px"] / valid


class _Grid:
    def __init__(self, mask: np.ndarray, grid, min_px):
        ys, xs = np.nonzero(mask)
        self.mask = mask
        self.cells = []
        if len(xs) == 0:
            return
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        gx, gy = grid
        for i in range(gx):
            for j in range(gy):
                cx0, cx1 = x0 + (x1 - x0) * i // gx, x0 + (x1 - x0) * (i + 1) // gx
                cy0, cy1 = y0 + (y1 - y0) * j // gy, y0 + (y1 - y0) * (j + 1) // gy
                m = np.zeros_like(mask)
                m[cy0:cy1, cx0:cx1] = True
                m &= mask
                if m.sum() >= min_px:
                    self.cells.append(m)

    def describe(self, lab: np.ndarray, edges: np.ndarray, frame_l: float) -> np.ndarray:
        out = np.full((len(self.cells), 4), np.nan, np.float32)
        for k, m in enumerate(self.cells):
            out[k, 0] = lab[..., 0][m].mean() - frame_l
            out[k, 1] = lab[..., 1][m].mean()
            out[k, 2] = lab[..., 2][m].mean()
            out[k, 3] = edges[m].mean() * 100.0
        return out


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    d = np.abs(a - b)
    w = np.array([1.0, 0.5, 0.5, 0.5], np.float32)
    per_cell = np.nansum(d * w, axis=1)
    valid = ~np.isnan(d).all(axis=1)
    return float(np.median(per_cell[valid])) if valid.any() else float("nan")


def build_timeline(ctx: RunContext, images: list[dict]) -> dict:
    import cv2
    cfg = dict(DEFAULTS, **ctx.site.get("timeline", {}))
    store, tax = ctx.store(), ctx.taxonomy
    views = store_views(store)
    stats = load_thumb_stats(ctx)
    label_states = store.review_states("state_label")
    targets = [r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"]
    by_id = {im["image_id"]: im for im in images}
    # photos per view and day (usable for appearance)
    per_view = defaultdict(lambda: defaultdict(list))
    for im in images:
        if im["status"] != "ok" or im.get("duplicate_of"):
            continue
        v = assign_view(im, views)
        if not v["view_id"]:
            continue
        day = im["capture"]["time_local"][:10]
        if stats.get(im["image_id"], {}).get("light") in cfg["light_classes"]:
            per_view[v["view_id"]][day].append(im)
    # anchors: approved labels
    anchors = defaultdict(list)  # (view, target, act) -> [(day, fraction, image_id)]
    for rec in store.latest("state_label"):
        if label_states.get(rec["key"], {}).get("status") != "approved":
            continue
        iid, tid, act = rec["key"].split("|")
        im = by_id.get(iid)
        if im is None:
            continue
        v = assign_view(im, views)
        roi_rec = store.get(rec["payload"]["roi_ref"]["kind"], rec["payload"]["roi_ref"]["key"],
                            rec["payload"]["roi_ref"]["version"])
        if roi_rec is None or not v["view_id"]:
            continue
        roi = roi_rec["payload"].get("roi") if rec["payload"]["roi_ref"]["kind"] == "observation" else roi_rec["payload"]
        frac = installed_fraction(rec["payload"], roi, im["size"])
        if frac is not None:
            anchors[(v["view_id"], tid, act)].append((im["capture"]["time_local"][:10], round(frac, 3), iid))
    out_targets = {}
    rois = {r["key"]: r for r in store.latest("roi")}
    thumb_cache: dict[str, tuple] = {}

    def thumb_lab(iid):
        if iid not in thumb_cache:
            rgb = load_thumb(ctx, iid)
            if rgb is None:
                thumb_cache[iid] = None
            else:
                lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
                edges = cv2.Canny(cv2.GaussianBlur(rgb[..., 1], (3, 3), 0), 40, 120) > 0
                thumb_cache[iid] = (lab, edges, float(lab[..., 0].mean()))
            if len(thumb_cache) > 4000:
                thumb_cache.pop(next(iter(thumb_cache)))
        return thumb_cache[iid]

    for vid in sorted(per_view):
        days = sorted(per_view[vid])
        for t in targets:
            tid = t["key"]
            roi_rec = rois.get(f"{tid}|{vid}")
            acts = [a for a, d in tax["activities"].items() if t["payload"]["surface_type"] in d["surface_types"]]
            entry = {"view_id": vid, "target_id": tid, "roi_version": roi_rec["version"] if roi_rec else None,
                     "roi_review": store.review_state("roi", f"{tid}|{vid}")["status"] if roi_rec else "missing"}
            if roi_rec is None:
                entry["status"] = "no_roi_for_view"
                out_targets[f"{vid}|{tid}"] = entry
                continue
            size = roi_rec["payload"]["image_size"]
            mask = roi_mask(_scale_roi(roi_rec["payload"], THUMB_W / size[0], THUMB_H / size[1]), (THUMB_W, THUMB_H))
            grid = _Grid(mask, cfg["grid"], cfg["min_cell_px"])
            if not grid.cells:
                entry["status"] = "roi_too_small_at_thumbnail_scale"
                out_targets[f"{vid}|{tid}"] = entry
                continue
            day_desc, outliers = {}, {}
            for day in days:
                ims = per_view[vid][day]
                if len(ims) > cfg["max_photos_per_day"]:
                    idx = np.linspace(0, len(ims) - 1, cfg["max_photos_per_day"]).round().astype(int)
                    ims = [ims[i] for i in sorted(set(idx.tolist()))]
                descs = []
                for im in ims:
                    tl = thumb_lab(im["image_id"])
                    if tl is not None:
                        descs.append((im["image_id"], grid.describe(*tl)))
                if not descs:
                    continue
                med = np.nanmedian(np.stack([d for _, d in descs]), axis=0)
                day_desc[day] = med
                # photo far from its day median = something in front of the target / unusual (messy candidate)
                outliers[day] = {iid: round(_distance(d, med), 2) for iid, d in descs}
            vdays = [d for d in days if d in day_desc]
            scores = {vdays[0]: None} if vdays else {}
            for a, b in zip(vdays, vdays[1:]):
                scores[b] = _distance(day_desc[a], day_desc[b])
            vals = np.array([s for s in scores.values() if s is not None and not np.isnan(s)], np.float32)
            med_s = float(np.median(vals)) if len(vals) else 0.0
            mad = float(np.median(np.abs(vals - med_s))) * 1.4826 if len(vals) else 0.0
            change = {}
            for d, s in scores.items():
                z = None if s is None or np.isnan(s) else (s - med_s) / (mad + 1e-6)
                change[d] = {"score": None if s is None else round(float(s), 2), "z": None if z is None else round(float(z), 2),
                             "strong": bool(z is not None and z >= cfg["z_change"] and s >= cfg["abs_change"])}
            entry.update(status="ok", days=len(vdays), change_points=[d for d in vdays if change[d]["strong"]],
                         activities={})
            for act in acts:
                anc = sorted(anchors.get((vid, tid, act), []))
                entry["activities"][act] = _classify(vdays, change, anc, cfg)
            entry["photo_outlier_score"] = outliers
            entry["change"] = change
            out_targets[f"{vid}|{tid}"] = entry
    report = {"config": cfg, "targets": out_targets,
              "anchors": {"|".join(k): v for k, v in anchors.items()},
              "self_check": _self_check(out_targets, anchors, cfg),
              "meaning": "candidates for sampling/drafts only; not labels, not progress values"}
    atomic_write_json(ctx.stage_dir("timeline") / "timeline_candidates.json", report)
    return report


def _classify(days: list[str], change: dict, anchors: list[tuple], cfg: dict) -> dict:
    tol = cfg["same_fraction_tol"]
    per_day = {}
    adays = defaultdict(list)
    for d, f, iid in anchors:
        adays[d].append(f)
    amap = {d: float(np.mean(v)) for d, v in adays.items()}
    akeys = sorted(amap)
    strong = {d for d in days if change.get(d, {}).get("strong")}
    for d in days:
        prev_a = max([a for a in akeys if a <= d], default=None)
        next_a = min([a for a in akeys if a >= d], default=None)
        if d in amap:
            if d in strong or (d in adays and max(adays[d]) - min(adays[d]) > tol):
                # the state changed DURING this day (strong appearance change or differing labels that day):
                # the day's anchor must not be copied to other photos of the same day
                per_day[d] = {"phase": "anchor_change", "state_hint": None, "confidence": "change_within_anchor_day",
                              "anchors": [round(x, 3) for x in adays[d]]}
            else:
                per_day[d] = {"phase": "anchor", "state_hint": round(amap[d], 3), "confidence": "approved_label"}
            continue
        between = lambda lo, hi: any(lo < s <= hi for s in strong)  # noqa: E731
        if prev_a and next_a:
            same = abs(amap[prev_a] - amap[next_a]) <= tol
            if same and not between(prev_a, next_a):
                per_day[d] = {"phase": "stable", "state_hint": round(amap[prev_a], 3), "confidence": "between_equal_anchors"}
            elif same:
                per_day[d] = {"phase": "stable_suspect", "state_hint": round(amap[prev_a], 3),
                              "confidence": "equal_anchors_but_change_detected"}
            else:
                per_day[d] = {"phase": "transition", "state_hint": None, "confidence": "between_different_anchors",
                              "from": round(amap[prev_a], 3), "to": round(amap[next_a], 3)}
        elif prev_a:
            if between(prev_a, d):
                per_day[d] = {"phase": "unknown", "state_hint": None, "confidence": "change_after_last_anchor"}
            else:
                per_day[d] = {"phase": "stable_extrapolated", "state_hint": round(amap[prev_a], 3),
                              "confidence": "after_last_anchor_no_change"}
        elif next_a:
            if between(d, next_a):
                per_day[d] = {"phase": "unknown", "state_hint": None, "confidence": "change_before_first_anchor"}
            else:
                per_day[d] = {"phase": "stable_extrapolated", "state_hint": round(amap[next_a], 3),
                              "confidence": "before_first_anchor_no_change"}
        else:
            # no approved label at all for this target yet: not evidence of change, just no knowledge
            per_day[d] = {"phase": "unanchored", "state_hint": None, "confidence": "no_anchor"}
        if d in strong and per_day[d]["phase"] != "anchor":
            per_day[d]["strong_change"] = True
            if per_day[d]["phase"] in ("stable_extrapolated", "unanchored"):
                per_day[d]["phase"] = "transition"
    windows, cur = [], None
    for d in days:
        if per_day[d]["phase"] in ("transition", "unknown", "stable_suspect", "anchor_change"):
            if cur is None:
                cur = {"start": d, "end": d, "phase": per_day[d]["phase"]}
            else:
                cur["end"] = d
        elif cur is not None:
            windows.append(cur)
            cur = None
    if cur:
        windows.append(cur)
    return {"per_day": per_day, "anchor_days": akeys, "attention_windows": windows,
            "phase_counts": _count(p["phase"] for p in per_day.values())}


def _self_check(out_targets: dict, anchors: dict, cfg: dict) -> dict:
    """Leave-one-anchor-out: would the remaining anchors + change points have predicted each anchor?"""
    checked = agree = disagree = undecided = 0
    for (vid, tid, act), anc in anchors.items():
        entry = out_targets.get(f"{vid}|{tid}")
        if not entry or entry.get("status") != "ok" or len(anc) < 2:
            continue
        days = sorted(entry["change"])
        for i, (d, f, _) in enumerate(sorted(anc)):
            rest = [a for j, a in enumerate(sorted(anc)) if j != i]
            res = _classify(days, entry["change"], rest, cfg)["per_day"].get(d)
            if res is None:
                continue
            checked += 1
            if res["state_hint"] is None:
                undecided += 1
            elif abs(res["state_hint"] - f) <= max(cfg["same_fraction_tol"], 0.1):
                agree += 1
            else:
                disagree += 1
    return {"anchors_checked": checked, "predicted_correctly": agree, "predicted_wrong": disagree,
            "left_undecided": undecided,
            "meaning": "rough consistency check on the few approved labels; not an accuracy estimate"}


def _count(items) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def day_phases(report: dict) -> dict[tuple[str, str], dict]:
    """{(view_id, day): {"phase": strongest phase over targets/activities, "targets": {...}}}."""
    rank = {"transition": 4, "anchor_change": 4, "unknown": 3, "stable_suspect": 2, "stable_extrapolated": 1, "unanchored": 1,
            "stable": 0, "anchor": 0}
    out: dict[tuple[str, str], dict] = {}
    for key, entry in report["targets"].items():
        if entry.get("status") != "ok":
            continue
        for act, a in entry["activities"].items():
            for d, p in a["per_day"].items():
                cur = out.setdefault((entry["view_id"], d), {"phase": "stable", "targets": {}})
                cur["targets"][f"{entry['target_id']}|{act}"] = p
                if rank[p["phase"]] > rank[cur["phase"]]:
                    cur["phase"] = p["phase"]
    return out
