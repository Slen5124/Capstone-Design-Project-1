"""Read existing export format without modifying labels or legacy LOSO splits."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from layout import SURFACES, STATE_CLASSES


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def export_path(root, value):
    path = (root / value).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("Export file paths must remain inside their export folder")
    return path


def read_training_manifest(path):
    doc = read_json(path)
    if doc.get("schema_version") != "final-training/0.2" or not doc.get("exports"):
        raise ValueError("Expected final-training/0.2 exports manifest")
    items, export_roots = [], set()
    for entry in doc["exports"]:
        root = (Path(path).resolve().parent / entry["path"]).resolve()
        if root in export_roots:
            raise ValueError("An export was listed twice")
        export_roots.add(root)
        site = entry["site_id"]
        if not isinstance(site, str) or not site.strip():
            raise ValueError("Every export needs a site_id")
        targets = read_json(root / "targets.json")["targets"]
        indices = [t["index"] for t in targets]
        if (len(set(indices)) != len(indices) or
                any(type(k) is not int or not 1 <= k <= 65534 or k == 255 for k in indices)):
            raise ValueError("Invalid or duplicate target indices")
        split_files = {}
        for split in ("train", "val", "test"):
            f = root / "splits" / (split + ".txt")
            for sample_id in f.read_text(encoding="utf-8-sig").split() if f.exists() else []:
                if sample_id in split_files:
                    raise ValueError("Sample assigned to multiple splits")
                split_files[sample_id] = split
        seen = set()
        for line in (root / "metadata.jsonl").read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            meta = json.loads(line)
            sid = meta["sample_id"]
            if sid in seen:
                raise ValueError("Duplicate sample_id inside an export")
            seen.add(sid)
            split = split_files.get(sid, meta.get("split"))
            if split not in ("train", "val", "test"):
                raise ValueError("Every sample needs an explicit train/val/test split")
            source = meta.get("label_source")
            if source not in ("human", "propagated", "weak"):
                raise ValueError("Explicit label_source human/propagated/weak is required")
            if split != "train" and source != "human":
                raise ValueError("Validation/test must use reviewed human labels")
            time_text = (meta.get("capture") or {}).get("time_local", "")
            date = datetime.fromisoformat(time_text.replace("Z", "+00:00")).date().isoformat()
            view = meta.get("view_id")
            if not isinstance(view, str) or not view.strip():
                raise ValueError("view_id is required for time-group leakage checks")
            files = {key: export_path(root, meta["files"][key]) for key in ("image", "semantic", "target", "visibility")}
            states = {surface: export_path(root, f"state_masks/{values[2]}/{sid}.png")
                      for surface, values in SURFACES.items()}
            items.append({"key": f"{site}|{root.name}|{sid}", "sample_id": sid, "site_id": site,
                          "view_id": view, "date": date, "split": split, "label_source": source,
                          "weight": {"human": 1., "propagated": .5, "weak": .3}[source],
                          "files": files, "states": states, "targets": targets})
        if set(split_files) - seen:
            raise ValueError("Split file refers to missing metadata samples")
    if not items:
        raise ValueError("No training samples")
    return items


def mask(path):
    with Image.open(path) as im:
        value = np.array(im)
    if value.ndim != 2 or not np.issubdtype(value.dtype, np.integer):
        raise ValueError("Labels must be single-channel integer masks, not colored overlays")
    return value


def load_sample(item, size):
    with Image.open(item["files"]["image"]) as im:
        if im.getexif().get(274, 1) != 1:
            raise ValueError("Resolve image orientation together with masks before export")
        rgb = np.array(im.convert("RGB"))
    semantic, target, visibility = (mask(item["files"][key]) for key in ("semantic", "target", "visibility"))
    shape = (size[1], size[0])
    if rgb.shape[:2] != shape or any(m.shape != shape for m in (semantic, target, visibility)):
        raise ValueError("Every exported image/mask must share the declared model-input size")
    if not np.isin(semantic, list(range(8)) + [255]).all() or not np.isin(visibility, [0, 1, 2, 3, 255]).all():
        raise ValueError("Unknown semantic/visibility class")
    valid_ids = {t["index"] for t in item["targets"]} | {0, 255, 65535}
    if set(map(int, np.unique(target))) - valid_ids:
        raise ValueError("Target mask contains undeclared indices")
    raw = np.full(shape, 255, np.uint8)
    active = []
    for surface, (not_id, installed_id, activity) in SURFACES.items():
        own = []
        for t in item["targets"]:
            if t.get("surface_type") != surface:
                continue
            app = (t.get("activities") or {}).get(activity) or {}
            if app.get("applicability") == "not_applicable":
                continue
            if app.get("applicability") != "applicable" or app.get("review") != "approved":
                raise ValueError("Target activity applicability must be explicitly reviewed")
            own.append(t["index"])
            active.append({**t, "surface_type": surface})
        path = item["states"][surface]
        if not path.exists():
            continue  # Optional per photo; audit requires supervision for all three surfaces overall.
        state = mask(path)
        if state.shape != shape or not np.isin(state, [0, 1, 255]).all():
            raise ValueError("Per-activity state mask must use 0/1/255 at export size")
        inside = np.isin(target, own) & (visibility == 1)
        raw[inside & (state == 0)] = not_id
        raw[inside & (state == 1)] = installed_id
    training_state = raw.copy()
    inside = np.isin(target, [t["index"] for t in active])
    training_state[(raw == 255) & inside & (visibility == 2)] = 6
    # Never call an unlabelled wall/floor/ceiling background. Preserve all uncertain pixels.
    training_state[(raw == 255) & ~inside & ~np.isin(target, [255, 65535]) & np.isin(semantic, [4, 5, 6, 7])] = 7
    return rgb, semantic.astype(np.uint8), training_state, raw, target, active


def audit(items, config):
    counts = {s: Counter() for s in ("train", "val", "test")}
    rows = {s: Counter() for s in counts}
    hashes, groups = {}, {}
    problems = []
    for item in items:
        rgb, _, state, raw, targets, active = load_sample(item, config["export_size"])
        split = item["split"]
        image_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
        group = (item["site_id"], item["view_id"], item["date"])
        for registry, key, label in ((hashes, image_hash, "identical_image"), (groups, group, "site_view_day")):
            if key in registry and registry[key] != split:
                problems.append(f"cross_split_{label}")
            registry[key] = split
        labels, pixels = np.unique(state, return_counts=True)
        counts[split].update({STATE_CLASSES[int(k)]: int(v) for k, v in zip(labels, pixels) if k != 255})
        for target in active:
            n0, n1, _ = SURFACES[target["surface_type"]]
            region = targets == target["index"]
            if int((region & np.isin(raw, [n0, n1])).sum()) >= config["min_roi_px"]:
                rows[split][target["surface_type"]] += 1
    for name in STATE_CLASSES[:6]:
        if counts["train"][name] == 0:
            problems.append("no_training_pixels:" + name)
    for surface in SURFACES:
        if rows["val"][surface] == 0:
            problems.append("no_validation_roi:" + surface)
    if not any(i["split"] == "train" for i in items):
        problems.append("no_training_samples")
    return {"schema_version": "training-audit/0.2", "passed": not problems,
            "problems": sorted(set(problems)), "samples": dict(Counter(i["split"] for i in items)),
            "state_pixels": {k: dict(v) for k, v in counts.items()},
            "labelled_roi_counts": {k: dict(v) for k, v in rows.items()},
            "leakage_check": "exact decoded image and site/view/day; not near-duplicate or temporal-buffer certification"}
