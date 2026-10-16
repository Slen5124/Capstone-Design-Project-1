"""Validate an export directory (files, dtypes, values, transforms, splits, approvals)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from .rasterize import load_mask_png
from .runctx import atomic_write_json, read_jsonl


def validate_export(export_dir: Path, store=None, ingest_images: dict | None = None) -> dict:
    export_dir = Path(export_dir)
    lm = json.loads((export_dir / "label_map.json").read_text(encoding="utf-8"))
    report = json.loads((export_dir / "export_report.json").read_text(encoding="utf-8"))
    problems, checked = [], 0
    mode = report["mode"]
    state_dir = "state_masks" if mode == "train" else "draft_state_masks"
    sem_ok = {c["id"] for c in lm["semantic"]["classes"]} | {lm["semantic"]["ignore_value"]}
    st_ok = set(lm["state"]["values"].values()) | {255}
    vis_ok = set(lm["visibility"]["values"].values())
    t_dtype = np.dtype(lm["target"]["dtype"])
    t_ok_max = lm["target"]["ignore_value"]
    split_of = {}
    if mode == "train":
        for s in ("train", "val", "test"):
            for line in (export_dir / "splits" / f"{s}.txt").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    if line in split_of:
                        problems.append(f"sample {line} listed in {split_of[line]} and {s}")
                    split_of[line] = s
    parents = defaultdict(set)
    shas = defaultdict(set)
    for meta in read_jsonl(export_dir / "metadata.jsonl"):
        checked += 1
        sid = meta["sample_id"]
        files = meta["files"]
        try:
            with Image.open(export_dir / files["image"]) as im:
                wh = list(im.size)
                if im.mode != "RGB":
                    problems.append(f"{sid}: image mode {im.mode}")
        except FileNotFoundError:
            problems.append(f"{sid}: image missing")
            continue
        if wh != meta["transform"]["out_wh"]:
            problems.append(f"{sid}: image size {wh} != transform out {meta['transform']['out_wh']}")
        sem = load_mask_png(export_dir / files["semantic"])
        tm = load_mask_png(export_dir / files["target"])
        vis = load_mask_png(export_dir / files["visibility"])
        for name, m in (("semantic", sem), ("target", tm), ("visibility", vis)):
            if [m.shape[1], m.shape[0]] != wh:
                problems.append(f"{sid}: {name} mask size {m.shape[::-1]} != image {wh}")
        if not set(np.unique(sem).tolist()) <= sem_ok:
            problems.append(f"{sid}: semantic values {sorted(set(np.unique(sem).tolist()) - sem_ok)} not in label_map")
        if tm.dtype != t_dtype or tm.max() > t_ok_max:
            problems.append(f"{sid}: target mask dtype/range wrong ({tm.dtype}, max {tm.max()})")
        if not set(np.unique(vis).tolist()) <= vis_ok:
            problems.append(f"{sid}: visibility values out of range")
        valid_union = np.zeros(sem.shape, bool)
        for act, a in meta["activities"].items():
            st = load_mask_png(export_dir / a["file"])
            if st.shape != sem.shape:
                problems.append(f"{sid}/{act}: state size mismatch")
                continue
            vals = set(np.unique(st).tolist())
            if not vals <= st_ok:
                problems.append(f"{sid}/{act}: state values {sorted(vals - st_ok)} not allowed")
            valid = int((st != 255).sum())
            if valid != a["valid_px"]:
                problems.append(f"{sid}/{act}: valid_px {a['valid_px']} != actual {valid}")
            if ((st != 255) & (vis == 0)).any():
                problems.append(f"{sid}/{act}: state pixels outside every ROI")
            valid_union |= st != 255
        if mode == "train":
            if meta["split"] != split_of.get(sid):
                problems.append(f"{sid}: metadata split {meta['split']} != split list {split_of.get(sid)}")
            if not any(a["valid"] for a in meta["activities"].values()):
                problems.append(f"{sid}: train sample without any valid activity")
            for t in meta["targets"]:
                for act, a in t["activities"].items():
                    if a.get("label_used"):
                        if a.get("review") != "approved":
                            problems.append(f"{sid}/{t['target_id']}/{act}: unapproved label used")
                        if store is not None:
                            key = f"{meta['image_id']}|{t['target_id']}|{act}"
                            rs = store.review_state("state_label", key)
                            if rs["status"] != "approved" or rs["version"] != a["label_version"]:
                                problems.append(f"{sid}/{t['target_id']}/{act}: approval no longer effective "
                                                f"({rs['status']} v{rs['version']}) - export is stale")
        parents[meta["parent_image_id"]].add(meta["split"])
        sha = (meta["source"]["photo"].get("sha256_actual") or meta["source"]["photo"].get("sha256"))
        shas[sha].add(meta["split"])
        if ingest_images is not None:
            src = ingest_images.get(meta["image_id"])
            if src is None or src["source_photo"]["sha256"] != meta["source"]["photo"]["sha256"]:
                problems.append(f"{sid}: source hash differs from ingest record")
    if mode == "train":
        for p, s in parents.items():
            if len(s) > 1:
                problems.append(f"parent image {p} (crops) spans splits {sorted(s)}")
        for sha, s in shas.items():
            if len(s) > 1:
                problems.append(f"identical photo content {sha[:12]} spans splits {sorted(s)}")
    out = {"export_dir": str(export_dir), "mode": mode, "status": report.get("status"), "samples_checked": checked,
           "problems": problems, "passed": not problems}
    atomic_write_json(export_dir / "validation_report.json", out)
    return out
