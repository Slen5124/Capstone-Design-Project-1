"""Command line entry point.

    python -m vision_preprocessing.cli <command> [options]

Commands (run inside one run directory; re-running a command on the same run
resumes: items whose input fingerprint is unchanged are reused, changed ones
are recomputed):

    init           create a new run dir from a site config (resolves latest_active_run.json)
    ingest         R01 read manifest/ZIPs, check hashes/sizes/time stamps
    index-views    R02 camera-view boundary candidates (+ --create-draft-views)
    sync-registry  R03 targets/applicability/views from config into the review store (insert-only)
    assign         R04/R05 ROI reuse/registration, polygon links, occlusion/quality drafts
    overlay        mask overlays for visual checking
    review         R07 local review UI (127.0.0.1)
    split          R09 leakage-aware split
    export         R10 draft preview or training export
    validate       validate an export directory
    measure        per-target observed-area ratios from state masks (labels or predictions)
    compare        before/after pairs from a measurement file
    status         counts of images / targets / labels / approvals
    smoke          init + ingest(limit) + views + registry + assign + overlay + split + draft/train export + validate
    auto           ALL automatic stages in one go (resumable): ingest, thumbs, views, drift, timeline, split,
                   samples, drafts, export, todo.  New run: --config ...   Continue: --run <run_dir>
    todo           rebuild HUMAN_TODO.md for a run
    new-site       write a site config for another city from the SITE_A template (targets left 'proposed')
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runctx import RunContext, RunLock, RunLockError, atomic_write_json


def _ctx(args) -> RunContext:
    return RunContext.open(Path(args.run), getattr(args, "path_map", None))


def _locked(fn):
    """Writer commands hold the run lock (one writer per run: auto / review server / assign ...)."""
    def wrapper(args):
        ctx_dir = Path(args.run)
        with RunLock(ctx_dir, args.cmd, force=getattr(args, "force_unlock", False)):
            return fn(args)
    return wrapper


def _size(value):
    if not value:
        return None
    w, h = value.lower().split("x")
    return [int(w), int(h)]


def cmd_init(args):
    ctx = RunContext.create(Path(args.config), output_root=Path(args.output_root) if args.output_root else None,
                            path_map_args=args.path_map, store_path=Path(args.store) if args.store else None,
                            argv=sys.argv, label=args.label)
    r = ctx.info["resolved"]
    print(f"[init] run dir      : {ctx.run_dir}")
    print(f"[init] pointer      : {r['latest_run_pointer']} -> {r['source_run_dir']}")
    print(f"[init] review store : {r['store_path']}")
    return ctx


@_locked
def cmd_ingest(args):
    from .source_adapter import run_ingest
    ctx = _ctx(args)
    report = run_ingest(ctx, limit=args.limit, select=args.select, dates=args.dates.split(",") if args.dates else None,
                        entries_file=Path(args.entries_file) if args.entries_file else None, dry_run=args.dry_run)
    print(json.dumps({k: report[k] for k in report if k in ("selected", "status", "check_codes", "warning_codes",
                                                             "capture_status", "duplicates")}, ensure_ascii=False, default=str))


@_locked
def cmd_index_views(args):
    from .index_views import detect_candidates, views_from_candidates
    from .source_adapter import load_images
    ctx = _ctx(args)
    report = detect_candidates(ctx, load_images(ctx))
    for s in report["segments"]:
        print(f"  {s['candidate_id']} {s['start_time']} .. {s['end_time']} sampled={s['sampled_images']} "
              f"ref={s['reference_image_id']} uncertain={len(s['uncertain_frames'])}")
    if args.create_draft_views:
        created = views_from_candidates(ctx.store(), report, ctx.site)
        print(f"[index-views] draft views created: {[c['view_id'] for c in created] or 'none (store already has views)'}")


def cmd_sync(args):
    from .index_views import views_from_config
    from .target_registry import sync_from_config
    ctx = _ctx(args)
    store = ctx.store()
    print(json.dumps({"views": views_from_config(store, ctx.site),
                      "registry": sync_from_config(store, ctx.site, ctx.taxonomy)}, ensure_ascii=False))


@_locked
def cmd_assign(args):
    from .assign_surfaces import build_observations
    ctx = _ctx(args)
    print(json.dumps(build_observations(ctx, limit=args.limit), ensure_ascii=False, default=str)[:2000])


def cmd_overlay(args):
    from .export_dataset import make_overlays
    ctx = _ctx(args)
    paths = make_overlays(ctx, limit=args.limit, image_ids=args.image_ids.split(",") if args.image_ids else None)
    print(f"[overlay] {len(paths)} overlays in {ctx.run_dir / 'overlays'}")


def cmd_review(args):
    from .review_app import serve
    serve(_ctx(args), port=args.port, force_unlock=args.force_unlock)


def cmd_auto(args):
    from .auto import STAGES, run_auto
    if args.run:
        ctx = _ctx(args)
    elif args.config:
        ctx = RunContext.create(Path(args.config), path_map_args=args.path_map, argv=sys.argv, label=args.label or "auto",
                                store_path=Path(args.store) if args.store else None)
        print(f"[auto] new run: {ctx.run_dir}")
    else:
        raise SystemExit("auto needs --config (new run) or --run (continue a run)")
    stages = args.stage.split(",") if args.stage else None
    for s in stages or []:
        if s not in STAGES:
            raise SystemExit(f"unknown stage {s}; stages: {', '.join(STAGES)}")
    state = run_auto(ctx, stages=stages, start=args.start, opts={"limit": args.limit}, force_unlock=args.force_unlock)
    todo = (state["stages"].get("todo") or {}).get("summary") or {}
    print(f"\n[auto] finished. run dir: {ctx.run_dir}")
    if todo:
        print(f"[auto] human to-do: {todo.get('file')}  ({todo.get('total_actions')} actions)")
    print(f"[auto] review UI: vp review --run \"{ctx.run_dir}\"   then open http://127.0.0.1:8765/todo")


def cmd_new_site(args):
    import copy
    pkg = Path(__file__).resolve().parent
    tpl = json.loads((pkg / "config" / "site_example.json").read_text(encoding="utf-8"))
    d = copy.deepcopy(tpl)
    sid = args.site_id.upper()
    d.update(site_id=sid, site_name=args.name, camera_id=args.camera_id or f"{sid}_CAM01", views=[], review_notes=[],
             camera_notes="Camera moves are NOT verified; see index-views candidates.")
    d["paths"]["store"] = f"state_preprocessing_results/review_store/{sid}.sqlite"
    d["source"]["photo_zip_contains"] = args.zip_contains
    d["roi_hints"]["teacher_reference_ids"] = [x for x in (args.teacher_ids or "").split(",") if x]
    for t in d["targets"]:
        t["identity_status"] = "proposed"
        for a in t["activities"].values():
            a.update(applicability="unknown", basis="template target; confirm in the review UI for this site")
    out = Path(args.out) if args.out else pkg / "config" / f"{sid.lower()}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; choose --out")
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)


def cmd_todo(args):
    from .todo import build_todo, write_todo_markdown
    ctx = _ctx(args)
    print(write_todo_markdown(ctx, build_todo(ctx)))


@_locked
def cmd_split(args):
    from .split_dataset import make_splits
    out = make_splits(_ctx(args))
    print(json.dumps({"counts": out["counts"], "leakage_check": out["leakage_check"]}, ensure_ascii=False, default=str))


@_locked
def cmd_export(args):
    from .export_dataset import export
    out = export(_ctx(args), mode=args.mode, size=_size(args.size), resize_mode=args.resize_mode, crop=args.crop,
                 occlusion_class=args.occlusion_class,
                 auto_occluder_ignore=False if args.no_auto_occluder_ignore else None, limit=args.limit)
    print(json.dumps({k: out.get(k) for k in ("status", "samples_written", "eligible_labels", "split_counts",
                                              "excluded", "export_dir")}, ensure_ascii=False, default=str))
    if out.get("next_actions"):
        print("[export] next human review actions:")
        for a in out["next_actions"][:30]:
            print("   -", a)


def cmd_validate(args):
    from .source_adapter import load_images
    from .validate_dataset import validate_export
    store, images = None, None
    if args.run:
        ctx = _ctx(args)
        store = ctx.store()
        images = {im["image_id"]: im for im in load_images(ctx, include_held=True)}
    out = validate_export(Path(args.export), store, images)
    print(json.dumps({k: out[k] for k in ("status", "samples_checked", "passed")}, ensure_ascii=False))
    for p in out["problems"][:50]:
        print("   PROBLEM:", p)
    return 0 if out["passed"] else 2


def cmd_measure(args):
    from .postprocess import measure_export
    print(measure_export(Path(args.export), args.activity, Path(args.pred_dir) if args.pred_dir else None,
                         args.min_observed_fraction))


def cmd_compare(args):
    from .postprocess import compare_measurements
    print(compare_measurements(Path(args.measurements)))


def cmd_status(args):
    ctx = _ctx(args)
    out = {"run": str(ctx.run_dir), "config_drift": ctx.check_config_drift(), "store_counts": ctx.store().counts()}
    for name in ("ingest/ingest_report.json", "assign/assign_report.json", "split/split_policy.json"):
        p = ctx.run_dir / "stages" / name
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            out[name] = {k: d.get(k) for k in ("selected", "status", "counts", "observations_by_status", "leakage_check") if k in d}
    progress = ctx.run_dir / "progress.json"
    if progress.exists():
        out["progress"] = json.loads(progress.read_text(encoding="utf-8"))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def cmd_prewarm(args):
    from .prewarm import prewarm
    prewarm(_ctx(args), focus_only=args.focus_only, workers=args.workers, limit=args.limit)


def cmd_snapshot(args):
    ctx = _ctx(args)
    path = ctx.run_dir / "review_store_snapshot.json"
    atomic_write_json(path, ctx.store().snapshot())
    print(path)


def cmd_smoke(args):
    args.label = args.label or "smoke"
    ctx = cmd_init(args)
    args.run = str(ctx.run_dir)
    args.select, args.dates, args.entries_file, args.dry_run = "spread", args.dates, None, False
    cmd_ingest(args)
    args.create_draft_views = True
    cmd_index_views(args)
    cmd_sync(args)
    args.limit_assign = args.limit
    cmd_assign(argparse.Namespace(run=args.run, limit=0))
    cmd_overlay(argparse.Namespace(run=args.run, limit=min(args.limit or 8, 8), image_ids=None))
    cmd_split(args)
    from .export_dataset import export
    from .validate_dataset import validate_export
    d = export(ctx, mode="draft", size=_size(args.size) if args.size else None, limit=min(args.limit or 6, 6))
    print("[smoke] draft export validation:", validate_export(Path(d["export_dir"]), ctx.store())["passed"])
    t = export(ctx, mode="train", size=_size(args.size) if args.size else None)
    print("[smoke] train export status:", t["status"])
    print(f"[smoke] run dir: {ctx.run_dir}")


def build_parser():
    p = argparse.ArgumentParser(prog="vision_preprocessing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, run=True):
        sp = sub.add_parser(name)
        sp.set_defaults(fn=fn)
        if run:
            sp.add_argument("--run", required=True, help="run directory created by 'init'")
            sp.add_argument("--path-map", action="append", help="FROM=TO path translation (only when the run "
                            "was created on another machine and auto-detection fails)")
            sp.add_argument("--force-unlock", action="store_true",
                            help="break a run lock left by a process that no longer exists")
        return sp

    for name, fn in (("init", cmd_init), ("smoke", cmd_smoke)):
        sp = add(name, fn, run=False)
        sp.add_argument("--config", required=True)
        sp.add_argument("--output-root")
        sp.add_argument("--store")
        sp.add_argument("--label")
        sp.add_argument("--path-map", action="append", help="FROM=TO prefix translation for paths in the manifest")
        if name == "smoke":
            sp.add_argument("--limit", type=int, default=24)
            sp.add_argument("--dates")
            sp.add_argument("--size")
    sp = add("ingest", cmd_ingest)
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--select", choices=("spread", "first"), default="spread")
    sp.add_argument("--dates", help="comma separated YYYY-MM-DD filter")
    sp.add_argument("--entries-file")
    sp.add_argument("--dry-run", action="store_true")
    sp = add("index-views", cmd_index_views)
    sp.add_argument("--create-draft-views", action="store_true")
    add("sync-registry", cmd_sync)
    sp = add("assign", cmd_assign)
    sp.add_argument("--limit", type=int, default=0)
    sp = add("overlay", cmd_overlay)
    sp.add_argument("--limit", type=int, default=12)
    sp.add_argument("--image-ids")
    sp = add("review", cmd_review)
    sp.add_argument("--port", type=int, default=8765)
    add("split", cmd_split)
    sp = add("export", cmd_export)
    sp.add_argument("--mode", choices=("draft", "train"), default="draft")
    sp.add_argument("--size", help="WxH, e.g. 1024x576 (default from config)")
    sp.add_argument("--resize-mode", choices=("letterbox", "stretch", "none"))
    sp.add_argument("--crop", action="store_true", help="one sample per target ROI (wall/floor/ceiling)")
    sp.add_argument("--occlusion-class", action="store_true", help="state value 2 = occluded instead of 255")
    sp.add_argument("--no-auto-occluder-ignore", action="store_true")
    sp.add_argument("--limit", type=int, default=0)
    sp = sub.add_parser("validate")
    sp.set_defaults(fn=cmd_validate)
    sp.add_argument("--export", required=True)
    sp.add_argument("--run")
    sp = sub.add_parser("measure")
    sp.set_defaults(fn=cmd_measure)
    sp.add_argument("--export", required=True)
    sp.add_argument("--activity", required=True)
    sp.add_argument("--pred-dir")
    sp.add_argument("--min-observed-fraction", type=float, default=0.3)
    sp = sub.add_parser("compare")
    sp.set_defaults(fn=cmd_compare)
    sp.add_argument("--measurements", required=True)
    add("status", cmd_status)
    add("snapshot", cmd_snapshot)
    sp = add("prewarm", cmd_prewarm)
    sp.add_argument("--focus-only", action="store_true", help="only samples / anchors / labelled / reference photos")
    sp.add_argument("--workers", type=int, default=None, help="parallel processes (default: CPU count - 1, max 8)")
    sp.add_argument("--limit", type=int, default=0, help="stop after N photos (test)")
    add("todo", cmd_todo)
    sp = sub.add_parser("new-site")
    sp.set_defaults(fn=cmd_new_site)
    sp.add_argument("--site-id", required=True, help="short id used in image ids and the review store, e.g. SITE_B")
    sp.add_argument("--name", required=True)
    sp.add_argument("--zip-contains", required=True, help="text that appears in the site's photo ZIP name, e.g. #001_-_Example_B")
    sp.add_argument("--camera-id")
    sp.add_argument("--teacher-ids", help="optional qa_v3 teacher reference ids (display hints), comma separated")
    sp.add_argument("--out")
    sp = sub.add_parser("auto")
    sp.set_defaults(fn=cmd_auto)
    sp.add_argument("--config", help="site config: creates a NEW run (resolves latest_active_run.json)")
    sp.add_argument("--run", help="continue this run (cached stages are skipped quickly)")
    sp.add_argument("--label")
    sp.add_argument("--store")
    sp.add_argument("--path-map", action="append")
    sp.add_argument("--stage", help="comma separated stages to run only")
    sp.add_argument("--start", help="run from this stage to the end")
    sp.add_argument("--limit", type=int, default=0, help="ingest only N photos spread over time (test run)")
    sp.add_argument("--force-unlock", action="store_true")
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles (cp949) must not crash on symbols
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    result = args.fn(args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
