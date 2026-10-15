"""Stages E + F: automatic labelling-sample selection and draft labels.

E. Samples (per view and day; quotas in config "sampling"):
   * stable days                 -> ``stable_per_day`` photos (default 2)
   * transition / unknown days   -> ``transition_per_day`` photos (default 6)
   * photos spread over the day (time bins); in each bin the cleanest photo
     (well lit, close to the day's typical ROI appearance)
   * ``messy_fraction`` (default 0.15) of all samples are deliberately "messy"
     photos (something in front of the target, low light) so the model also
     learns from occluded / imperfect views; fully dark photos are never used
   * photos that already have a label are always kept as samples
E is independent of approvals; it only lists photos.

F. Drafts for every sample (never approved here; source = propagated_draft /
   bulk_prefill_draft, author "auto"):
   * stable day and an approved label of the same target in the same view,
     same ROI version, same split and no change point in between
       -> copy of that label's state regions ("same as before")
   * stable day with a uniform state hint (0 % or 100 %) -> whole-ROI fill
   * transition / unknown days with an earlier approved label -> copy flagged
     "needs editing" (the reviewer adjusts the regions)
   * otherwise no draft: the to-do queue asks for a drawing
   Drafts are only made where the ROI and the view are approved, so a later
   ROI edit cannot silently invalidate a large number of drafts (item 5).
"""
from __future__ import annotations

import json
from collections import defaultdict

import numpy as np

from .assign_surfaces import build_observations, load_observations, open_observation_cache
from .index_views import assign_view, store_views
from .runctx import RunContext, atomic_write_json, read_jsonl
from .schemas import label_key, obs_key
from .state_annotations import LabelError, propagate_label_draft, save_label
from .store import StoreError
from .thumbs import load_thumb_stats

DEFAULTS = {"stable_per_day": 2, "transition_per_day": 6, "messy_fraction": 0.15, "max_samples": 0,
            "messy_light_classes": ["low_light"], "outlier_messy_score": 12.0,
            "status": "unvalidated defaults (user decision 10: keep some messy photos)"}
AUTO_AUTHOR = "auto"
CHANGE = ("transition", "unknown", "stable_suspect", "anchor_change")


def _timeline(ctx):
    path = ctx.stage_dir("timeline") / "timeline_candidates.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def select_samples(ctx: RunContext, images: list[dict]) -> dict:
    from .timeline import day_phases
    cfg = dict(DEFAULTS, **ctx.site.get("sampling", {}))
    store = ctx.store()
    views = store_views(store)
    stats = load_thumb_stats(ctx)
    tl = _timeline(ctx)
    phases = day_phases(tl) if tl else {}
    outlier = defaultdict(dict)  # image_id -> max outlier score over targets
    if tl:
        for entry in tl["targets"].values():
            for day, scores in (entry.get("photo_outlier_score") or {}).items():
                for iid, sc in scores.items():
                    if sc == sc:  # not NaN
                        outlier[iid] = max(outlier.get(iid) or 0.0, sc)
    labeled = {r["key"].split("|")[0] for r in store.latest("state_label")}
    per_day = defaultdict(list)
    for im in images:
        if im["status"] != "ok" or im.get("duplicate_of"):
            continue
        v = assign_view(im, views)
        if not v["view_id"]:
            continue
        per_day[(v["view_id"], im["capture"]["time_local"][:10])].append(im)
    samples, messy_pool = [], []
    for (vid, day), ims in sorted(per_day.items()):
        ims.sort(key=lambda im: im["capture"]["time_local"])
        phase = phases.get((vid, day), {}).get("phase", "no_timeline")  # e.g. view without ROIs yet
        quota = cfg["transition_per_day"] if phase in CHANGE else cfg["stable_per_day"]
        chosen = [im for im in ims if im["image_id"] in labeled]
        for im in chosen:
            samples.append(_row(im, vid, day, phase, "already_labeled", stats, outlier))
        clean = [im for im in ims if im["image_id"] not in labeled and stats.get(im["image_id"], {}).get("light") == "ok"
                 and (outlier.get(im["image_id"]) or 0.0) < cfg["outlier_messy_score"]]
        need = max(0, quota - len(chosen))
        if need and clean:
            bins = np.array_split(np.arange(len(clean)), min(need, len(clean)))
            for b in bins:
                cand = [clean[i] for i in b]
                best = min(cand, key=lambda im: (outlier.get(im["image_id"]) or 0.0))
                samples.append(_row(best, vid, day, phase, "transition" if phase in CHANGE else "stable", stats, outlier))
        picked = {s["image_id"] for s in samples}
        for im in ims:
            if im["image_id"] in picked:
                continue
            light = stats.get(im["image_id"], {}).get("light")
            score = outlier.get(im["image_id"]) or 0.0
            if light in cfg["messy_light_classes"] or (light == "ok" and score >= cfg["outlier_messy_score"]):
                messy_pool.append((score + (5.0 if light != "ok" else 0.0), _row(im, vid, day, phase, "messy", stats, outlier)))
    normal = sum(1 for s in samples if s["kind"] != "already_labeled")
    n_messy = int(round(cfg["messy_fraction"] * normal / max(1e-6, 1 - cfg["messy_fraction"])))
    # spread messy photos over days: round-robin over days, strongest first within a day
    by_day = defaultdict(list)
    for score, row in sorted(messy_pool, key=lambda x: -x[0]):
        by_day[(row["view_id"], row["day"])].append(row)
    keys = sorted(by_day, key=lambda k: (0 if by_day[k][0]["phase"] in CHANGE else 1, k))
    messy, depth = [], 0
    while len(messy) < n_messy and any(len(v) > depth for v in by_day.values()):
        for k in keys:
            if len(by_day[k]) > depth and len(messy) < n_messy:
                messy.append(by_day[k][depth])
        depth += 1
    samples += messy
    if cfg["max_samples"] and len(samples) > cfg["max_samples"]:
        keep = [s for s in samples if s["kind"] == "already_labeled"]
        rest = [s for s in samples if s["kind"] != "already_labeled"]
        idx = np.linspace(0, len(rest) - 1, max(0, cfg["max_samples"] - len(keep))).round().astype(int)
        samples = keep + [rest[i] for i in sorted(set(idx.tolist()))]
    samples.sort(key=lambda s: (s["view_id"], s["time"]))
    stage = ctx.stage_dir("samples")
    with open(stage / "samples.jsonl", "w", encoding="utf-8") as handle:
        for s in samples:
            handle.write(json.dumps(s, ensure_ascii=False) + "\n")
    report = {"config": cfg, "samples": len(samples), "by_kind": _count(s["kind"] for s in samples),
              "by_view": _count(s["view_id"] for s in samples), "by_phase": _count(s["phase"] for s in samples),
              "days_with_samples": len({(s["view_id"], s["day"]) for s in samples}),
              "photos_considered": sum(len(v) for v in per_day.values()),
              "timeline_available": tl is not None}
    atomic_write_json(stage / "samples_report.json", report)
    return report


def _row(im, vid, day, phase, kind, stats, outlier):
    st = stats.get(im["image_id"], {})
    return {"image_id": im["image_id"], "view_id": vid, "day": day, "time": im["capture"]["time_local"],
            "phase": phase, "kind": kind, "light": st.get("light"), "brightness": st.get("mean"),
            "outlier_score": outlier.get(im["image_id"])}


def load_samples(ctx: RunContext) -> list[dict]:
    return list(read_jsonl(ctx.stage_dir("samples") / "samples.jsonl"))


def make_drafts(ctx: RunContext, images: list[dict]) -> dict:
    """Assign observations for the samples and create draft labels (never approvals)."""
    from .split_dataset import load_assignments
    store, tax = ctx.store(), ctx.taxonomy
    samples = load_samples(ctx)
    by_id = {im["image_id"]: im for im in images}
    sample_ims = [by_id[s["image_id"]] for s in samples if s["image_id"] in by_id]
    cache = open_observation_cache(ctx)
    try:
        build_observations(ctx, images=sample_ims, cache=cache)
        obs_all = load_observations(ctx, cache)
    finally:
        cache.close()
        cache.maybe_compact()
    try:
        split_of = {k: v["split"] for k, v in load_assignments(ctx).items()}
    except FileNotFoundError:
        split_of = {}
    tl = _timeline(ctx) or {"targets": {}}
    views = store_views(store)
    view_ok = {v["view_id"]: v["_review"] == "approved" for v in views}
    states = store.review_states("state_label")
    roi_states = store.review_states("roi")
    targets = [r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"]
    approved_src = defaultdict(list)  # (tid, act, view, roi_version) -> [image]
    for rec in store.latest("state_label"):
        if states.get(rec["key"], {}).get("status") != "approved":
            continue
        iid, tid, act = rec["key"].split("|")
        im = by_id.get(iid)
        if im is None:
            continue
        ref = rec["payload"].get("roi_ref") or {}
        if ref.get("kind") != "roi":
            continue
        v = assign_view(im, views)["view_id"]
        approved_src[(tid, act, v, ref.get("version"))].append(im)
    created, skipped, no_basis = defaultdict(int), defaultdict(int), []
    with store.transaction():
        for s in samples:
            im = by_id.get(s["image_id"])
            if im is None:
                continue
            for t in targets:
                tid = t["key"]
                o = obs_all.get(obs_key(im["image_id"], tid))
                for act, adef in tax["activities"].items():
                    if t["payload"]["surface_type"] not in adef["surface_types"]:
                        continue
                    app = store.get("applicability", f"{tid}|{act}")
                    if not app or app["payload"]["applicability"] != "applicable":
                        skipped["not_applicable_or_unknown"] += 1
                        continue
                    key = label_key(im["image_id"], tid, act)
                    if store.get("state_label", key) is not None:
                        skipped["label_exists"] += 1
                        continue
                    if not o or o.get("status") != "ok" or o.get("roi_source") not in ("reference_image", "same_view_reuse"):
                        skipped["roi_" + str((o or {}).get("status", "missing")) + ("" if not o or o.get("status") != "ok"
                                                                          else "_source_" + str(o.get("roi_source")))] += 1
                        continue
                    if roi_states.get(o["roi_ref"]["key"], {}).get("status") != "approved" or not view_ok.get(o["view_id"]):
                        skipped["roi_or_view_not_approved_yet"] += 1
                        continue
                    entry = tl["targets"].get(f"{o['view_id']}|{tid}", {})
                    per_day = ((entry.get("activities") or {}).get(act) or {}).get("per_day", {})
                    day = s["day"]
                    ph = per_day.get(day, {"phase": "unknown", "state_hint": None})
                    srcs = approved_src.get((tid, act, o["view_id"], o["roi_ref"]["version"]), [])
                    result = _draft_one(store, tax, im, o, tid, act, ph, srcs, per_day, entry.get("change_points", []),
                                        split_of)
                    if result is None:
                        no_basis.append({"image_id": im["image_id"], "target_id": tid, "activity_id": act,
                                         "view_id": o["view_id"], "day": day, "time": s["time"], "phase": ph["phase"]})
                        skipped["no_basis_for_draft"] += 1
                    else:
                        created[result] += 1
    needs_drawing = anchor_requests(no_basis, tl, ctx.site.get("sampling", {}).get("anchor_requests_per_target", 6))
    report = {"samples": len(samples), "drafts_created": dict(created), "skipped": dict(skipped),
              "needs_drawing": needs_drawing, "no_basis_total": len(no_basis), "approved_anything": False,
              "next": "draw + approve the requested anchor photos, then run auto again: the other samples get drafts",
              "meaning": "drafts only; every draft must be checked and approved by a person in the review UI"}
    atomic_write_json(ctx.stage_dir("samples") / "drafts_report.json", report)
    return report


def anchor_requests(no_basis: list[dict], tl: dict | None, per_target: int = 6) -> list[dict]:
    """Ask for a FEW hand-drawn anchors instead of every sample: per (view, target, activity) the first and
    last sample day and the samples just before/inside/after each attention window. After these are
    approved, the next auto run turns the remaining samples into drafts."""
    groups = defaultdict(list)
    for r in no_basis:
        groups[(r["view_id"], r["target_id"], r["activity_id"])].append(r)
    out = []
    for (vid, tid, act), rows in sorted(groups.items()):
        rows.sort(key=lambda r: r["time"])
        days = sorted({r["day"] for r in rows})
        want = [days[0], days[-1], days[len(days) // 2]]
        entry = ((tl or {}).get("targets", {}).get(f"{vid}|{tid}") or {})
        for w in ((entry.get("activities") or {}).get(act) or {}).get("attention_windows", []):
            before = [d for d in days if d < w["start"]]
            after = [d for d in days if d > w["end"]]
            inside = [d for d in days if w["start"] <= d <= w["end"]]
            want += ([before[-1]] if before else []) + ([inside[len(inside) // 2]] if inside else []) + ([after[0]] if after else [])
        picked, seen = [], set()
        for d in want:
            if d in seen:
                continue
            seen.add(d)
            r = next(r for r in rows if r["day"] == d)
            picked.append(dict(r, why="anchor request (no approved label nearby)"))
            if len(picked) >= per_target:
                break
        out += sorted(picked, key=lambda r: r["time"])
    return out


def _draft_one(store, tax, im, o, tid, act, ph, srcs, per_day, change_points, split_of):
    day = im["capture"]["time_local"][:10]
    my_split = split_of.get(im["image_id"])
    phase = ph["phase"]

    def ok_split(src):
        # a source label may come from the same split or from train (never from val/test into train),
        # so the nearest correct source is not skipped just because it sits in another split
        sp = split_of.get(src["image_id"])
        return my_split is None or sp is None or sp == my_split or sp == "train"

    def no_change_between(a, b):
        lo, hi = min(a, b), max(a, b)
        return not any(lo < c <= hi for c in change_points) and all(
            per_day.get(d, {}).get("phase") not in CHANGE for d in per_day if lo <= d <= hi)

    if phase not in CHANGE:
        cands = [s for s in srcs if ok_split(s)]
        cands = [s for s in cands if no_change_between(s["capture"]["time_local"][:10], day)]
        if cands:
            src = min(cands, key=lambda s: abs(_days(s["capture"]["time_local"][:10], day)))
            try:
                propagate_label_draft(store, tax, src, im, o, tid, act, AUTO_AUTHOR,
                                      note=f"auto draft: stable period, copied from {src['image_id']}",
                                      draft_basis="stable_copy")
                return "copied_from_stable_anchor"
            except (LabelError, StoreError):
                pass
        hint = ph.get("state_hint")
        if hint is not None and (hint <= 0.02 or hint >= 0.98):
            state = "installed" if hint >= 0.98 else "not_installed"
            payload = {"target_id": tid, "activity_id": act, "regions": [], "fill_roi_state": state,
                       "undeterminable": [], "occluded": [], "whole_target_undeterminable": None,
                       "roi_ref": o["roi_ref"], "roi_source": o["roi_source"],
                       "note": f"auto draft: stable period, whole ROI {state} (timeline {ph.get('confidence')})",
                       "draft_basis": "stable_whole_roi_fill"}
            try:
                save_label(store, tax, im, payload, author=AUTO_AUTHOR, source="bulk_prefill_draft", view_id=o["view_id"])
                return "whole_roi_fill"
            except (LabelError, StoreError):
                return None
        return None
    earlier = [s for s in srcs if s["capture"]["time_local"] < im["capture"]["time_local"] and ok_split(s)]
    if not earlier:
        return None
    src = max(earlier, key=lambda s: s["capture"]["time_local"])
    try:
        propagate_label_draft(store, tax, src, im, o, tid, act, AUTO_AUTHOR,
                              note=f"auto draft (CHANGE PERIOD - edit the regions): copied from {src['image_id']}",
                              draft_basis="change_period_copy_needs_editing")
        return "copied_needs_editing"
    except (LabelError, StoreError):
        return None


def _days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _count(items) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
