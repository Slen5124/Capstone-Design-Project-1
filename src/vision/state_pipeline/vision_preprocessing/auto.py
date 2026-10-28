"""`auto`: run the whole automatic part in one go, resumably, and end with the human to-do list.

Stages (each one is cached per photo / per comparison, so re-running continues
where it stopped and only recomputes what changed):

    ingest    read manifest + ZIPs, check hashes            (R01)
    thumbs    small thumbnails + brightness classes
    views     camera-view boundary candidates (coarse-to-fine) -> draft views / proposals
    roi_drafts  ROI drafts for views without ROIs (per-target registration from another view)
    drift     daily camera drift vs the view reference (redraw requests)
    align     EVERY photo vs its view reference: camera-position runs inside a view -> trim / split / new reference
              suggestions on the to-do page (also fills the review display cache = pre-decrypts photos)
    timeline  per-target state-change candidates, anchored on approved labels
    split     phase-stratified, day-grouped train/val/test split
    samples   labelling samples (stable 2/day, change 6/day, ~15 % messy)
    drafts    observations for the samples + draft labels (never approvals)
    export    training export of APPROVED labels (+ validation, installed-ratio measurement)
    todo      HUMAN_TODO.md + human_todo.json (what 유난님 has to do next)

Nothing here approves anything. Automatic success is never human approval.
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .runctx import RunContext, RunLock, atomic_write_json

STAGES = ("ingest", "thumbs", "views", "roi_drafts", "drift", "align", "timeline", "split", "samples", "drafts", "export", "todo")


def _state_path(ctx):
    return ctx.run_dir / "stages" / "auto_state.json"


def _load_state(ctx) -> dict:
    p = _state_path(ctx)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"stages": {}}


def _images(ctx, include_held=False):
    from .source_adapter import load_images
    return load_images(ctx, include_held=include_held)


def _light(ctx) -> dict:
    from .thumbs import load_thumb_stats
    return {k: v.get("light") for k, v in load_thumb_stats(ctx).items()}


def stage_ingest(ctx, opts):
    from .source_adapter import run_ingest
    r = run_ingest(ctx, limit=opts.get("limit") or 0, select="spread")
    out = {k: r[k] for k in ("selected", "status", "duplicates", "capture_status") if k in r}
    out["date_gaps"] = _date_gaps(r.get("dates", {}))
    return out


def _date_gaps(dates: dict) -> list:
    from datetime import date
    ds = sorted(d for d in dates if d != "unknown")
    gaps = []
    for a, b in zip(ds, ds[1:]):
        n = (date.fromisoformat(b) - date.fromisoformat(a)).days
        if n > 1:
            gaps.append({"after": a, "before": b, "missing_days": n - 1})
    return gaps


def stage_thumbs(ctx, opts):
    from .thumbs import build_thumbs
    r = build_thumbs(ctx, _images(ctx))
    return {"light_classes": r["light_classes"], "images": r["images"]}


def stage_views(ctx, opts):
    from .index_views import detect_candidates, reconcile_views
    from .target_registry import sync_from_config
    store = ctx.store()
    sync_from_config(store, ctx.site, ctx.taxonomy)  # insert-only: never overwrites reviewed records
    rep = detect_candidates(ctx, _images(ctx), light=_light(ctx))
    rec = reconcile_views(store, rep, ctx.site)
    atomic_write_json(ctx.stage_dir("views") / "view_reconcile.json", rec)
    return {"candidates": [{k: s[k] for k in ("candidate_id", "start_time", "end_time", "photos", "boundary_type")}
                           for s in rep["segments"]],
            "frames_compared": rep["frames_compared"], "frames_total": rep.get("frames_total"),
            "reconcile": {k: v for k, v in rec.items() if k != "proposals"}, "proposals": len(rec["proposals"])}


def stage_roi_drafts(ctx, opts):
    from .roi_drafts import draft_missing_rois
    r = draft_missing_rois(ctx, _images(ctx))
    return {"created": r["created"], "failed": len(r["failed"]), "skipped": r["skipped"]}


def stage_drift(ctx, opts):
    from .drift import check_drift
    r = check_drift(ctx, _images(ctx), light=_light(ctx))
    return {"redraw_requests": len(r["redraw_requests"]), "severe_shakes": len(r["severe_shakes"]),
            "per_view": {v: d["counts"] for v, d in r["views"].items()}}


def stage_align(ctx, opts):
    from .view_align import check_alignment
    r = check_alignment(ctx, _images(ctx), light=_light(ctx))
    by = {}
    for sg in r["segments"]:
        by[sg["suggest"]] = by.get(sg["suggest"], 0) + 1
    total = {}
    for c in r["views"].values():
        for k, n in c.items():
            total[k] = total.get(k, 0) + n
    return {"segments": len(r["segments"]), "by_suggestion": by, "photos": total}


def stage_timeline(ctx, opts):
    from .timeline import build_timeline
    r = build_timeline(ctx, _images(ctx))
    out = {"self_check": r["self_check"], "targets": {}}
    for k, e in r["targets"].items():
        out["targets"][k] = {"status": e.get("status"), "change_points": len(e.get("change_points", [])),
                             "phases": {a: v["phase_counts"] for a, v in (e.get("activities") or {}).items()}}
    return out


def stage_split(ctx, opts):
    from .split_dataset import make_splits
    r = make_splits(ctx, _images(ctx))
    return {"counts": dict(r["counts"]), "leakage_check": r["leakage_check"], "policy": r["policy"]["policy"]}


def stage_samples(ctx, opts):
    from .sampling import select_samples
    r = select_samples(ctx, _images(ctx))
    return {k: r[k] for k in ("samples", "by_kind", "by_phase", "by_view", "days_with_samples")}


def stage_drafts(ctx, opts):
    from .sampling import make_drafts
    r = make_drafts(ctx, _images(ctx))
    return {"drafts_created": r["drafts_created"], "skipped": r["skipped"], "needs_drawing": len(r["needs_drawing"])}


def stage_export(ctx, opts):
    from .export_dataset import export
    from .postprocess import measure_export
    from .validate_dataset import validate_export
    out = {}
    t = export(ctx, mode="train")
    out["train"] = {k: t.get(k) for k in ("status", "samples_written", "eligible_labels", "split_counts", "export_dir")}
    if t.get("samples_written"):
        store = ctx.store()
        images = {im["image_id"]: im for im in _images(ctx, include_held=True)}
        v = validate_export(Path(t["export_dir"]), store, images)
        out["train"]["validation_passed"] = v["passed"]
        out["train"]["validation_problems"] = v["problems"][:20]
        meas = {}
        store_ = ctx.store()
        applicable_acts = {r["key"].split("|")[1] for r in store_.latest("applicability")
                           if r["payload"]["applicability"] == "applicable"}
        for act in ctx.taxonomy["activities"]:
            if act not in applicable_acts:
                continue
            p = measure_export(Path(t["export_dir"]), act)
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
            ok = [r for r in rows if r.get("status") == "ok"]
            meas[act] = {"file": str(p), "rows": len(rows), "ok": len(ok)}
        out["train"]["measurements"] = meas
        from .postprocess import daily_progress
        out["train"]["daily_progress"] = daily_progress(Path(t["export_dir"]))
        long = max((len(str(p)) for p in Path(t["export_dir"]).rglob("*")), default=0)
        out["train"]["longest_path_chars"] = long
        if long > 240:
            out["train"]["warning"] = "paths longer than 240 characters; Windows tools may fail - use a shorter output folder"
    return out


def stage_todo(ctx, opts):
    from .todo import build_todo, write_todo_markdown
    todo = build_todo(ctx, _images(ctx))
    path = write_todo_markdown(ctx, todo)
    return {"file": path, "total_actions": todo["total_actions"],
            "sections": {s["id"]: s["count"] for s in todo["sections"] if s["count"]}}


RUNNERS = {"ingest": stage_ingest, "thumbs": stage_thumbs, "views": stage_views, "roi_drafts": stage_roi_drafts,
           "drift": stage_drift, "align": stage_align,
           "timeline": stage_timeline, "split": stage_split, "samples": stage_samples, "drafts": stage_drafts,
           "export": stage_export, "todo": stage_todo}


def pointer_check(ctx) -> dict:
    """Item 19: has latest_active_run.json moved on since this run was created? (reported, never followed)."""
    res = ctx.info["resolved"]
    try:
        cur = json.loads(Path(res["latest_run_pointer"]).read_text(encoding="utf-8"))
        now = cur.get("run_dir") or cur.get("path")
    except (OSError, json.JSONDecodeError, KeyError):
        return {"checked": False}
    rec = res.get("source_run_dir_original") or res["source_run_dir"]
    changed = now != rec
    if changed:  # same folder, only renamed / moved (FIRST_STAGE_RUN.json) -> not a newer run
        try:
            changed = Path(ctx.map(now)).resolve() != Path(res["source_run_dir"]).resolve()
        except OSError:
            pass
    return {"checked": True, "changed": changed, "recorded": rec, "current": now,
            "meaning": "this run keeps using the recorded source run; create a new run (vp auto --config ...) "
                       "to use the newer one. Labels in the review store carry over (image ids do not depend on it)."}


def run_auto(ctx: RunContext, *, stages=None, start: str | None = None, opts: dict | None = None,
             force_unlock: bool = False) -> dict:
    opts = opts or {}
    todo = list(stages or STAGES)
    if start:
        todo = list(STAGES[STAGES.index(start):])
    state = _load_state(ctx)
    state["pointer"] = pointer_check(ctx)
    if state["pointer"].get("changed"):
        print("[auto] NOTE: the qa_v3 latest_active_run pointer changed since this run was created; "
              "this run keeps the recorded source (see auto_state.json)")
    with RunLock(ctx.run_dir, "auto", force=force_unlock):
        for name in todo:
            t0 = time.time()
            print(f"\n[auto] ===== {name} =====", flush=True)
            entry = {"status": "running", "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            state["stages"][name] = entry
            atomic_write_json(_state_path(ctx), state)
            try:
                summary = RUNNERS[name](ctx, opts)
                entry.update(status="done", summary=summary)
            except Exception as error:  # noqa: BLE001 - recorded, then re-raised
                entry.update(status="failed", error=f"{type(error).__name__}: {error}",
                             traceback=traceback.format_exc()[-4000:])
                atomic_write_json(_state_path(ctx), state)
                raise
            finally:
                entry["elapsed_s"] = round(time.time() - t0, 1)
                entry["finished_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                atomic_write_json(_state_path(ctx), state)
            print(f"[auto] {name} done in {entry['elapsed_s']}s: {json.dumps(summary, ensure_ascii=False, default=str)[:600]}",
                  flush=True)
    return state
