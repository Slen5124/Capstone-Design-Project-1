"""Propagate human-approved state labels into stable periods (pseudo-labels, NOT approvals).

Rule ("sandwich"): two consecutive approved labels of the same target in the same camera view, same split,
installed ratio within --tol of each other and at most --max-gap-h apart. Photos taken between them in that view
(well lit, no camera drift that day, not already labelled) get that target's installed / not-installed pixels
copied from the two anchors (pixel kept where the anchors agree, or where only one of them sees it). Occluders are
re-detected per photo (its own SAM3 objects), so moving people / materials are not copied. Test-split anchors are
never used, so the test set stays human-labelled only.

    plan   (reads labels and run metadata only)
        python -m vision_preprocessing.tools.propagate_labels plan --run <run> --export <run>\\exports\\<..>_train_full
        -> <run>\\propagation\\plan.json + periods.csv   (review these, then build)
    build  (reads the selected photos; ZIP password prompt as usual; writes a NEW export, nothing else is changed)
        python -m vision_preprocessing.tools.propagate_labels build --run <run> --plan <run>\\propagation\\plan.json
        -> <run>\\exports\\<stamp>_propagated   (train with:  train_quick --export <human export> <propagated export>)

The review store, the human export and the source data are only read.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ACT = "wall_board_installation"
OCCLUDER_SEM = (5, 6, 7)          # materials, tools, person (label_map.json)


def _t(s):
    return datetime.fromisoformat(s)


def _jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ plan
def make_plan(run: Path, export: Path, tol=0.02, max_gap_h=24.0, per_day=8, allowed_drift=("ok", "minor_drift"),
              allowed_light=("ok",), views=None, overrides=None):
    """overrides (human review of the plan, see apply_overrides):
         drop_anchors : [{"image_id", "targets": [..]}]  human labels the reviewer found wrong - not used as anchors
         assign       : [{"image_ids": [..], "target_id", "anchors": [a, b]}]  photos that must take these anchors' state
    """
    ov = overrides or {}
    dropped = {(d["image_id"], t) for d in ov.get("drop_anchors", []) for t in d.get("targets", [])}
    meas = [m for m in _jsonl(export / "measurements_wall_board_installation.jsonl")
            if m.get("activity_id", ACT) == ACT and m["target_id"].startswith("W")]
    meta = {m["image_id"]: m for m in _jsonl(export / "metadata.jsonl")}
    labelled = set(meta)
    images = [im for im in _jsonl(run / "stages" / "ingest" / "images.jsonl")
              if im.get("status") == "ok" and not im.get("duplicate_of")]
    light = {}
    tp = run / "stages" / "thumbs" / "thumbs.jsonl"
    if tp.exists():
        for r in _jsonl(tp):
            light[r["_key"]] = r["value"].get("light")
    drift = {}
    dp = run / "stages" / "drift" / "drift_report.json"
    if dp.exists():
        for v, x in json.loads(dp.read_text(encoding="utf-8")).get("views", {}).items():
            for d in x.get("days", []):
                drift[(v, d["day"])] = d["status"]
    view_rng = views or _views_from_export(export, meas)

    # anchors and sandwich pairs per target
    by_t = defaultdict(list)
    for m in meas:
        if m["installed_ratio_within_observed"] is None or m["status"] != "ok":
            continue
        if (m["image_id"], m["target_id"]) in dropped:
            continue
        sp = meta.get(m["image_id"], {}).get("split")
        by_t[m["target_id"]].append(dict(m, split=sp))
    pairs = []
    for tid, rs in by_t.items():
        rs.sort(key=lambda r: r["time_local"])
        for a, b in zip(rs, rs[1:]):
            ok = (a["view_id"] == b["view_id"] and a["split"] == b["split"] and a["split"] in ("train", "val")
                  and abs(a["installed_ratio_within_observed"] - b["installed_ratio_within_observed"]) <= tol
                  and (_t(b["time_local"]) - _t(a["time_local"])).total_seconds() <= max_gap_h * 3600)
            pairs.append({"target_id": tid, "view_id": a["view_id"], "split": a["split"], "ok": ok,
                          "start": a["time_local"], "end": b["time_local"],
                          "anchors": [a["image_id"], b["image_id"]],
                          "ratios": [a["installed_ratio_within_observed"], b["installed_ratio_within_observed"]]})

    # candidate photos
    def view_of(t):
        hits = [v for v, (s, e) in view_rng.items() if s <= t <= e]
        return hits[0] if len(hits) == 1 else None

    cand = {}
    skipped = defaultdict(int)
    good = [p for p in pairs if p["ok"]]
    for im in images:
        iid, t = im["image_id"], im["capture"]["time_local"]
        if not t or iid in labelled:
            continue
        hit = [p for p in good if p["start"] < t < p["end"]]
        if not hit:
            continue
        v = view_of(t)
        if v is None:
            skipped["ambiguous_or_no_view"] += 1
            continue
        hit = [p for p in hit if p["view_id"] == v]
        if not hit:
            skipped["view_mismatch"] += 1
            continue
        if light.get(iid, "ok") not in allowed_light:
            skipped["low_light"] += 1
            continue
        if drift.get((v, t[:10]), "ok") not in allowed_drift:
            skipped["camera_drift_day"] += 1
            continue
        splits = {p["split"] for p in hit}
        if len(splits) != 1:
            skipped["split_boundary"] += 1
            continue
        cand[iid] = {"image_id": iid, "time_local": t, "view_id": v, "split": splits.pop(),
                     "targets": {p["target_id"]: {"anchors": p["anchors"], "ratios": p["ratios"]} for p in hit}}

    # sample: per (day, view) up to per_day photos, evenly spread in time, photos covering more targets first
    by_day = defaultdict(list)
    for c in cand.values():
        by_day[(c["time_local"][:10], c["view_id"])].append(c)
    selected = []
    for k, cs in sorted(by_day.items()):
        cs.sort(key=lambda c: c["time_local"])
        nmax = max(len(c["targets"]) for c in cs)
        pool = [c for c in cs if len(c["targets"]) == nmax] or cs
        if len(pool) < per_day:
            pool = cs
        step = len(pool) / per_day if len(pool) > per_day else 1
        selected += [pool[int(i * step)] for i in range(min(per_day, len(pool)))]

    # reviewer overrides: photos that must take a given pair of anchors' state (added if not selected yet)
    ratio_of = {(m["image_id"], m["target_id"]): m["installed_ratio_within_observed"] for m in meas}
    by_id = {c["image_id"]: c for c in selected}
    for asg in ov.get("assign", []):
        tid, anchors = asg["target_id"], asg["anchors"]
        a0 = next((m for m in meas if m["image_id"] == anchors[0] and m["target_id"] == tid), None)
        for iid in asg["image_ids"]:
            c = by_id.get(iid)
            if c is None:
                im = next((im for im in images if im["image_id"] == iid), None)
                if im is None or a0 is None:
                    continue
                c = {"image_id": iid, "time_local": im["capture"]["time_local"], "view_id": a0["view_id"],
                     "split": meta.get(anchors[0], {}).get("split"), "targets": {}}
                selected.append(c)
                by_id[iid] = c
            c["targets"][tid] = {"anchors": anchors, "ratios": [ratio_of.get((a, tid)) for a in anchors],
                                 "override": asg.get("note", "reviewer")}
    selected.sort(key=lambda c: c["time_local"])

    # periods = chains of consecutive ok pairs (for the human review table)
    periods = []
    for tid in sorted(by_t):
        cur = None
        for p in [p for p in pairs if p["target_id"] == tid]:
            if p["ok"] and cur and cur["view_id"] == p["view_id"] and cur["split"] == p["split"] and cur["end"] == p["start"]:
                cur["end"] = p["end"]
                cur["ratios"] += p["ratios"]
                cur["anchors"] += 1
            elif p["ok"]:
                cur = {"target_id": tid, "view_id": p["view_id"], "split": p["split"], "start": p["start"], "end": p["end"],
                       "ratios": list(p["ratios"]), "anchors": 2}
                periods.append(cur)
            else:
                cur = None
    for pr in periods:
        r = pr.pop("ratios")
        pr.update(ratio_min=round(min(r), 4), ratio_max=round(max(r), 4),
                  candidates=sum(1 for c in cand.values() if pr["target_id"] in c["targets"] and pr["start"] < c["time_local"] < pr["end"]),
                  selected=sum(1 for c in selected if pr["target_id"] in c["targets"] and pr["start"] < c["time_local"] < pr["end"]))
    return {"schema": "propagation_plan/1", "created": datetime.now().replace(microsecond=0).isoformat(),
            "run": str(run), "export": str(export), "activity": ACT,
            "rule": {"tol": tol, "max_gap_h": max_gap_h, "per_day": per_day, "allowed_drift": list(allowed_drift),
                     "allowed_light": list(allowed_light), "splits_used": ["train", "val"],
                     "note": "pseudo-labels from human anchors; not approvals; test split never used"},
            "overrides": ov,
            "counts": {"anchor_pairs": len(pairs), "ok_pairs": len(good), "candidates": len(cand),
                       "selected": len(selected), "skipped": dict(skipped)},
            "periods": periods, "selected": selected}


def _views_from_export(export: Path, meas):
    """View time ranges from the run's view records (via the export review snapshot)."""
    snap = export / "review_snapshot.json"
    rng = {}
    if snap.exists():
        s = json.loads(snap.read_text(encoding="utf-8"))
        for rec in (s.get("view") or s.get("records", {}).get("view") or []):
            p = rec.get("payload", rec)
            if p.get("start_time") and p.get("end_time"):
                rng[p["view_id"]] = (p["start_time"], p["end_time"])
    if not rng:   # fall back to the span of the labelled photos per view
        for m in meas:
            s, e = rng.get(m["view_id"], (m["time_local"], m["time_local"]))
            rng[m["view_id"]] = (min(s, m["time_local"]), max(e, m["time_local"]))
    return rng


def views_from_sqlite(path: Path):
    import sqlite3
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    latest = {}
    for key, ver, payload in con.execute("select key, version, payload from records where kind='view' order by key, version"):
        latest[key] = json.loads(payload)
    con.close()
    return {k: (p["start_time"], p["end_time"]) for k, p in latest.items()
            if not p.get("retired") and p.get("start_time") and p.get("end_time")}


def write_plan(plan, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "periods.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["target_id", "view_id", "split", "start", "end", "ratio_min", "ratio_max",
                                          "anchors", "candidates", "selected"])
        w.writeheader()
        w.writerows(plan["periods"])


# ------------------------------------------------------------------ build
def compose_state(anchor_states, anchor_targets, index, new_sem, new_tg):
    """Target `index` pixels from the anchors (0/1 only): agree -> keep, one side unknown -> keep, conflict -> 255.
    Pixels covered by an occluder in the NEW photo -> 255 (train_quick turns visibility==2 into 'occluded')."""
    out = np.full(new_tg.shape, 255, np.uint8)
    region = new_tg == index
    vals = []
    for st, tg in zip(anchor_states, anchor_targets):
        v = np.where((tg == index) & np.isin(st, (0, 1)), st, 255).astype(np.uint8)
        vals.append(v)
    a = vals[0]
    b = vals[1] if len(vals) > 1 else vals[0]
    both = (a != 255) & (b != 255)
    keep = np.where(both, np.where(a == b, a, 255), np.where(a != 255, a, b)).astype(np.uint8)
    out[region] = keep[region]
    out[region & np.isin(new_sem, OCCLUDER_SEM)] = 255
    return out


def build(run: Path, plan_path: Path, name=None, path_map=None, reuse_draft: Path | None = None):
    from PIL import Image
    from ..export_dataset import export
    from ..rasterize import save_mask_png
    from ..runctx import RunContext, atomic_write_json, utc_stamp

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    human = Path(plan["export"])
    sel = plan["selected"]
    if not sel:
        raise SystemExit("plan has no selected photos")
    name = name or f"{utc_stamp()}_propagated"
    run_dir = Path(run)
    if reuse_draft:            # photos + SAM3 object masks already rendered by an earlier build (no ZIP reading)
        src = Path(reuse_draft)
        rep = json.loads((src / "export_report.json").read_text(encoding="utf-8"))
    else:
        ctx = RunContext.open(run, path_map)
        run_dir = ctx.run_dir
        rep = export(ctx, mode="draft", image_ids=[c["image_id"] for c in sel], name=name + "_draftsrc")
        src = Path(rep["export_dir"])
    hrep = json.loads((human / "export_report.json").read_text(encoding="utf-8"))
    if rep.get("size") != hrep.get("size"):
        raise SystemExit(f"size mismatch: draft {rep.get('size')} vs human export {hrep.get('size')}")
    out = run_dir / "exports" / name
    for sub in ("images", "semantic_masks", "target_masks", "visibility_masks", f"state_masks/{ACT}", "splits"):
        (out / sub).mkdir(parents=True, exist_ok=False if sub == "images" else True)
    htargets = json.loads((human / "targets.json").read_text(encoding="utf-8"))
    dtargets = json.loads((src / "targets.json").read_text(encoding="utf-8"))
    if [(t["index"], t["target_id"]) for t in htargets["targets"]] != [(t["index"], t["target_id"]) for t in dtargets["targets"]]:
        raise SystemExit("target index mismatch between the human export and the draft export")
    idx = {t["target_id"]: t["index"] for t in htargets["targets"]}
    wall_idx = [t["index"] for t in htargets["targets"] if t.get("surface_type") == "wall"]
    for f in ("targets.json", "label_map.json"):
        (out / f).write_bytes((human / f).read_bytes())
    smeta = {m["image_id"]: m for m in _jsonl(src / "metadata.jsonl")}
    load = lambda p: np.asarray(Image.open(p), dtype=np.uint8)
    split_lists = defaultdict(list)
    written, dropped = 0, defaultdict(int)
    with open(out / "metadata.jsonl", "w", encoding="utf-8") as mh:
        for c in sel:
            m = smeta.get(c["image_id"])
            if not m:
                dropped["not_in_draft_export"] += 1
                continue
            sid = m["sample_id"]
            sem = load(src / m["files"]["semantic"])
            # ROI: the view's approved ROI = the anchors' target mask (same view, fixed per view). The draft export
            # has no ROI for photos the assign stage never sampled, so it is not used for the ROI.
            first = next(iter(c["targets"].values()))["anchors"][0]
            tg = load(human / f"target_masks/{first}.png")
            if tg.shape != sem.shape:
                dropped["size_mismatch"] += 1
                continue
            vis = np.zeros(tg.shape, np.uint8)
            vis[np.isin(tg, wall_idx)] = 1
            vis[(vis == 1) & np.isin(sem, OCCLUDER_SEM)] = 2          # this photo's own people / materials / tools
            state = np.full(tg.shape, 255, np.uint8)
            used = {}
            for tid, info in c["targets"].items():
                anchors = info["anchors"]
                st = [load(human / f"state_masks/{ACT}/{a}.png") for a in anchors]
                tgs = [load(human / f"target_masks/{a}.png") for a in anchors]
                s = compose_state(st, tgs, idx[tid], sem, tg)
                state[s != 255] = s[s != 255]
                used[tid] = {"anchors": anchors, "anchor_ratios": info["ratios"], "pixels": int((s != 255).sum())}
            if int((state != 255).sum()) < 64:
                dropped["too_few_pixels"] += 1
                continue
            for key in ("image", "semantic"):
                (out / m["files"][key]).write_bytes((src / m["files"][key]).read_bytes())
            save_mask_png(tg, out / m["files"]["target"])
            save_mask_png(vis, out / m["files"]["visibility"])
            save_mask_png(state, out / f"state_masks/{ACT}/{sid}.png")
            m2 = dict(m, split=c["split"], mode="train", training_ready=True, view_id=c["view_id"],
                      label_source="propagated", propagation={"view_id": c["view_id"], "targets": used, "plan": str(plan_path),
                                                              "roi_from": first})
            mh.write(json.dumps(m2, ensure_ascii=False) + "\n")
            split_lists[c["split"]].append(sid)
            written += 1
    for s in ("train", "val", "test"):
        (out / "splits" / f"{s}.txt").write_text("\n".join(split_lists.get(s, [])), encoding="utf-8")
    report = {"schema_version": hrep.get("schema_version"), "mode": "train", "label_source": "propagated",
              "size": hrep.get("size"), "resize_mode": hrep.get("resize_mode"), "samples_written": written,
              "dropped": dict(dropped), "split_counts": {k: len(v) for k, v in split_lists.items()},
              "human_export": str(human), "draft_source": str(src), "plan": str(plan_path), "rule": plan["rule"],
              "note": "pseudo-labels copied from human-approved anchors in stable periods; NOT approvals; no test split"}
    atomic_write_json(out / "export_report.json", report)
    print(json.dumps({k: report[k] for k in ("samples_written", "dropped", "split_counts")}, ensure_ascii=False))
    print(f"[propagate] -> {out}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--run", required=True, type=Path)
    p.add_argument("--export", required=True, type=Path, help="the human-label train export (anchors)")
    p.add_argument("--tol", type=float, default=0.02)
    p.add_argument("--max-gap-h", type=float, default=24.0)
    p.add_argument("--per-day", type=int, default=8)
    p.add_argument("--store", type=Path, default=None, help="review store .sqlite (view time ranges; read-only)")
    p.add_argument("--overrides", type=Path, default=None, help="reviewer corrections (json, see make_plan)")
    b = sub.add_parser("build")
    b.add_argument("--run", required=True, type=Path)
    b.add_argument("--plan", required=True, type=Path)
    b.add_argument("--name", default=None)
    b.add_argument("--path-map", nargs="*", default=None)
    b.add_argument("--reuse-draft", type=Path, default=None, help="draft export of an earlier build (skips reading the ZIPs)")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        views = views_from_sqlite(a.store) if a.store else None
        ov = json.loads(a.overrides.read_text(encoding="utf-8")) if a.overrides else None
        plan = make_plan(a.run, a.export, a.tol, a.max_gap_h, a.per_day, views=views, overrides=ov)
        write_plan(plan, a.run / "propagation")
        print(json.dumps(plan["counts"], ensure_ascii=False))
        print(f"[propagate] plan -> {a.run / 'propagation' / 'plan.json'}  ({len(plan['periods'])} periods)")
    else:
        build(a.run, a.plan, a.name, a.path_map, a.reuse_draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
