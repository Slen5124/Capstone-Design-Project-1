"""R09: leakage-aware split of images into train / val / test.

Unit of assignment = group. Groups are built with union-find so that
  * identical photo bytes (duplicate downloads, "(1)" copies) share a group,
  * all photos of one (view, time block) share a group (default block = day).
Crops/augmentations inherit the split of their parent image at export time.

Policy ``temporal_blocks`` (default): per view, blocks are ordered in time and
cut by the configured fractions; ``gap_blocks`` blocks at each boundary are
dropped (split = "excluded_gap") so near-in-time frames never cross splits.
Policy ``grouped_random`` shuffles whole blocks with the recorded seed.
Images without a capture time are excluded unless configured otherwise.

This is a single-site temporal split. It measures generalization to later
dates of the same camera, not to other sites.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import datetime

from .index_views import assign_view, store_views
from .runctx import RunContext, atomic_write_json
from .schemas import label_key
from .source_adapter import load_images


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def block_of(time_local: str | None, block: str) -> str | None:
    if not time_local:
        return None
    t = datetime.fromisoformat(time_local)
    if block == "day":
        return t.strftime("%Y-%m-%d")
    if block == "week":
        y, w, _ = t.isocalendar()
        return f"{y}-W{w:02d}"
    if block.endswith("h"):
        hours = int(block[:-1])
        return t.strftime("%Y-%m-%d") + f"T{(t.hour // hours) * hours:02d}"
    raise ValueError(f"unsupported block {block}")


def make_splits(ctx: RunContext, images: list[dict] | None = None) -> dict:
    cfg = dict({"policy": "temporal_blocks", "block": "day", "fractions": {"train": 0.7, "val": 0.15, "test": 0.15},
                "gap_blocks": 1, "seed": 0, "unknown_time": "exclude"}, **ctx.site.get("split", {}))
    store = ctx.store()
    images = images if images is not None else load_images(ctx)
    views = store_views(store)
    uf = _UF()
    info = {}
    phase_block, phase_of_day = {}, {}
    if cfg["policy"] == "phase_stratified":
        phase_block, phase_of_day = _phase_blocks(ctx, images, views, cfg)
    for im in images:
        v = assign_view(im, views)
        b = block_of(im["capture"]["time_local"], cfg["block"])
        view = v["view_id"] or "unassigned"
        if phase_block and b is not None:
            b = phase_block.get((view, b), b)
        node = f"img:{im['image_id']}"
        info[im["image_id"]] = {"view_id": view, "block": b, "time": im["capture"]["time_local"],
                                "sha": im["source_photo"].get("sha256_actual") or im["source_photo"]["sha256"]}
        uf.union(node, f"sha:{info[im['image_id']]['sha']}")
        if b is not None:
            uf.union(node, f"blk:{view}:{b}")
        if im.get("duplicate_of"):
            uf.union(node, f"img:{im['duplicate_of']}")
    groups = defaultdict(list)
    for iid in info:
        groups[uf.find(f"img:{iid}")].append(iid)
    # group descriptor: view (first), earliest time
    gdesc = []
    for gid, members in groups.items():
        times = [info[m]["time"] for m in members if info[m]["time"]]
        views_in = sorted({info[m]["view_id"] for m in members})
        gdesc.append({"group": gid, "members": sorted(members), "view_id": views_in[0], "views": views_in,
                      "start": min(times) if times else None, "end": max(times) if times else None})
    assignment, policy_detail = {}, {}
    by_view = defaultdict(list)
    for g in gdesc:
        if g["start"] is None and cfg["unknown_time"] == "exclude":
            for m in g["members"]:
                assignment[m] = "excluded_unknown_time"
            continue
        by_view[g["view_id"]].append(g)
    names = list(cfg["fractions"])
    if cfg["policy"] == "phase_stratified":
        policy_detail = _assign_stratified(by_view, assignment, cfg, phase_of_day, info)
        by_view = {}
    for view, gs in by_view.items():
        if cfg["policy"] == "temporal_blocks":
            gs.sort(key=lambda g: g["start"] or "")
        elif cfg["policy"] == "grouped_random":
            gs.sort(key=lambda g: g["start"] or "")
            random.Random(f"{cfg['seed']}:{view}").shuffle(gs)
        else:
            raise ValueError(f"unknown split policy {cfg['policy']}")
        n = len(gs)
        bounds, acc = [], 0.0
        for name in names:
            acc += cfg["fractions"][name]
            bounds.append(round(acc * n))
        labels = []
        start = 0
        for name, end in zip(names, bounds):
            labels += [name] * (end - start)
            start = end
        labels += [names[-1]] * (n - len(labels))
        if cfg["policy"] == "temporal_blocks" and cfg["gap_blocks"]:
            orig = list(labels)
            for i in range(1, n):
                if orig[i] != orig[i - 1]:
                    for k in range(i, min(n, i + cfg["gap_blocks"])):
                        labels[k] = "excluded_gap"
        ranges = defaultdict(list)          # label -> list of contiguous [start, end] runs
        prev = None
        for g, lab in zip(gs, labels):
            for m in g["members"]:
                assignment[m] = lab
            if lab == prev and ranges[lab]:
                ranges[lab][-1][1] = g["end"]
            else:
                ranges[lab].append([g["start"], g["end"]])
            prev = lab
        policy_detail[view] = {"groups": n, "time_ranges": dict(ranges)}
    leakage = check_leakage(assignment, info, cfg)
    group_of = {m: g["group"] for g in gdesc for m in g["members"]}
    out = {"policy": cfg, "per_view": policy_detail, "counts": Counter(assignment.values()),
           "leakage_check": leakage, "statement": "single-site temporal split; not a cross-site evaluation"}
    stage = ctx.stage_dir("split")
    atomic_write_json(stage / "split_policy.json", out)
    rows = [{"image_id": iid, "split": assignment[iid], "group": group_of[iid], **info[iid]} for iid in sorted(info)]
    with open(stage / "assignments.jsonl", "w", encoding="utf-8") as handle:
        import json
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    out["distribution"] = distribution_report(ctx, rows)
    atomic_write_json(stage / "split_report.json", out)
    return out


CHANGE_PHASES = ("transition", "unknown", "stable_suspect", "anchor_change")


def _phase_blocks(ctx, images, views, cfg) -> tuple[dict, dict]:
    """(view, day) -> block id. Days are grouped into short runs of the same phase class
    (change / stable) so that splits are balanced across construction phases (item 13)."""
    import json
    from .timeline import day_phases
    path = ctx.stage_dir("timeline") / "timeline_candidates.json"
    phases = day_phases(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
    days_by_view = defaultdict(set)
    for im in images:
        if im["capture"]["time_local"]:
            v = assign_view(im, views)["view_id"] or "unassigned"
            days_by_view[v].add(im["capture"]["time_local"][:10])
    block, phase_of_day = {}, {}
    for view, days in days_by_view.items():
        run, prev_cls = [], None
        for d in sorted(days):
            ph = phases.get((view, d), {}).get("phase", "no_timeline")  # e.g. a view without ROIs yet
            cls = "change" if ph in CHANGE_PHASES else "stable"
            phase_of_day[(view, d)] = cls
            limit = cfg.get("change_block_days", 1) if cls == "change" else cfg.get("stable_block_days", 3)
            if cls != prev_cls or len(run) >= limit:
                run = []
            run.append(d)
            block[(view, d)] = f"{cls}:{run[0]}"
            prev_cls = cls
    return block, phase_of_day


def _assign_stratified(by_view, assignment, cfg, phase_of_day, info) -> dict:
    """Per phase class (change / stable), groups are ordered by a seeded hash of their id and cut by the
    configured fractions. Balanced per class, and stable across re-runs: adding a few new days moves only
    a few groups (a label approved earlier keeps its split in most cases)."""
    import hashlib
    names = list(cfg["fractions"])
    total = sum(cfg["fractions"].values())
    cum, acc = [], 0.0
    for n in names:
        acc += cfg["fractions"][n] / total
        cum.append(acc)
    by_cls = defaultdict(list)
    for view, gs in by_view.items():
        for g in gs:
            days = {(info[m]["view_id"], (info[m]["time"] or "")[:10]) for m in g["members"]}
            cls = "change" if any(phase_of_day.get(d) == "change" for d in days) else "stable"
            by_cls[cls].append((view, g))
    counts = defaultdict(lambda: defaultdict(int))
    detail = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for cls, items in by_cls.items():
        items.sort(key=lambda x: hashlib.sha1(f"{cfg['seed']}|{x[1]['group']}".encode()).hexdigest())
        n = len(items)
        for k, (view, g) in enumerate(items):
            pos = (k + 0.5) / n
            split = next((name for name, c in zip(names, cum) if pos < c), names[-1])
            counts[cls][split] += len(g["members"])
            for m in g["members"]:
                assignment[m] = split
            detail[view][cls][split].append([g["start"], g["end"]])
    return {"phase_classes_photos": {c: dict(v) for c, v in counts.items()},
            "groups_per_class": {c: len(v) for c, v in by_cls.items()},
            "per_view": {v: {c: {s: sorted(r) for s, r in d.items()} for c, d in cl.items()} for v, cl in detail.items()},
            "note": "groups = runs of days with the same phase class (change/stable) per view, each class cut by "
                    "the fractions in seeded-hash order"}


def check_leakage(assignment: dict, info: dict, cfg: dict) -> dict:
    by_sha = defaultdict(set)
    for iid, split in assignment.items():
        if not split.startswith("excluded"):
            by_sha[info[iid]["sha"]].add(split)
    dup_cross = [sha for sha, s in by_sha.items() if len(s) > 1]
    min_gap = None
    per_view = defaultdict(list)
    for iid, split in assignment.items():
        if split.startswith("excluded") or not info[iid]["time"]:
            continue
        per_view[info[iid]["view_id"]].append((info[iid]["time"], split))
    for items in per_view.values():
        items.sort()
        for (t1, s1), (t2, s2) in zip(items, items[1:]):
            if s1 != s2:
                gap = (datetime.fromisoformat(t2) - datetime.fromisoformat(t1)).total_seconds() / 3600
                min_gap = gap if min_gap is None else min(min_gap, gap)
    return {"duplicate_content_across_splits": len(dup_cross), "min_hours_between_adjacent_splits": min_gap,
            "passed": not dup_cross}


def distribution_report(ctx: RunContext, rows: list[dict]) -> dict:
    store = ctx.store()
    dates = defaultdict(Counter)
    views = defaultdict(Counter)
    labels = defaultdict(Counter)
    label_index = defaultdict(list)
    for rec in store.latest("state_label"):
        image_id, target_id, act = rec["key"].split("|")
        label_index[image_id].append(rec)
    for r in rows:
        dates[r["split"]][(r["time"] or "unknown")[:10]] += 1
        views[r["split"]][r["view_id"]] += 1
        for rec in label_index.get(r["image_id"], []):
            status = store.review_state("state_label", rec["key"])["status"]
            act = rec["key"].split("|")[2]
            states = sorted({reg["state"] for reg in rec["payload"].get("regions", [])} |
                            ({rec["payload"]["fill_roi_state"]} if rec["payload"].get("fill_roi_state") else set()))
            labels[r["split"]][f"{act}:{status}:{'+'.join(states) or 'none'}"] += 1
    return {"images_per_split_per_date": {k: dict(sorted(v.items())) for k, v in dates.items()},
            "images_per_split_per_view": {k: dict(v) for k, v in views.items()},
            "state_labels_per_split": {k: dict(v) for k, v in labels.items()},
            "units": "images (not targets, not labels) except state_labels_per_split = (image,target,activity) labels"}


def load_assignments(ctx: RunContext) -> dict[str, dict]:
    import json
    path = ctx.stage_dir("split") / "assignments.jsonl"
    if not path.exists():
        raise FileNotFoundError("run 'split' first")
    out = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            out[row["image_id"]] = row
    return out
