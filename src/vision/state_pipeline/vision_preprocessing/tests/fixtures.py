"""Synthetic site fixture (code-behaviour tests only; says nothing about real accuracy).

Builds, under a temp root, the same layout as the real project:
    <root>/Data/photos.zip                     original photos (JPEG)
    <root>/automatic_results/latest_active_run.json
    <root>/automatic_results/run_fixture/manifest.jsonl (+ annotation ZIP)
Paths inside the manifest use a fake Windows prefix (D:\\fixture\\...) to
exercise the path map, exactly like reading the real Windows manifest elsewhere.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 640, 360
WIN_ROOT = "D:\\fixture"
PREFIX = "Example_Store_-_#000_-_Example,_XX-"

W01 = [[0, 40], [250, 80], [250, 260], [0, 330]]
W02 = [[390, 80], [640, 40], [640, 330], [390, 260]]


def _texture(seed=7):
    rng = np.random.default_rng(seed)
    big = Image.fromarray((rng.random((520, 920, 3)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(big)
    for _ in range(260):
        x, y = rng.integers(0, 900), rng.integers(0, 500)
        r = int(rng.integers(4, 22))
        col = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.rectangle([x, y, x + r, y + r * 0.7], fill=col)
        else:
            d.ellipse([x, y, x + r, y + r], outline=col, width=2)
    return big


def _shapes(dx=0, dy=0):
    def mv(pts):
        return [[x - dx, y - dy] for x, y in pts]
    shapes = [
        ("wall", [[0, 40], [120, 59], [120, 300], [0, 330]]),
        ("wall", [[120, 59], [250, 80], [250, 260], [120, 300]]),
        ("wall", [[390, 80], [640, 40], [640, 330], [390, 260]]),
        ("wall", [[200, 150], [440, 150], [440, 200], [200, 200]]),   # spans W01 / back wall / W02
        ("person", [[60, 150], [100, 150], [100, 280], [60, 280]]),
        ("floor", [[0, 330], [250, 260], [390, 260], [640, 330], [640, 360], [0, 360]]),
        ("ceiling", [[0, 0], [640, 0], [640, 50], [0, 50]]),           # overlaps walls at y 40..50 -> conflict
        ("unidentifiable", [[300, 100], [330, 100], [330, 120], [300, 120]]),
    ]
    return [{"label": l, "points": mv(p), "group_id": None, "shape_type": "polygon", "flags": {},
             "description": "sam3_auto", "score": 0.9} for l, p in shapes]


def build(root: Path, n_days: int = 12, move_after_day: int = 8) -> dict:
    root = Path(root)
    (root / "Data").mkdir(parents=True)
    run = root / "automatic_results" / "run_fixture"
    (run / "chunks" / "part_000001" / "annotations").mkdir(parents=True)
    tex = _texture()
    photos, records = {}, []
    entries = []
    for day in range(n_days):
        for hour in (10, 15):
            moved = day >= move_after_day
            ox, oy = (160, 90) if moved else (100, 60)
            stamp = f"202502{10 + day:02d}-{hour:02d}0000"
            img = tex.crop((ox, oy, ox + W, oy + H))
            # simulated progress: a light "board" area on the left wall grows day by day
            d = ImageDraw.Draw(img)
            dx, dy = ox - 100, oy - 60
            d.rectangle([20 - dx, 120 - dy, 20 - dx + 10 * day, 240 - dy], fill=(225, 222, 210))
            noise = np.random.default_rng(day * 100 + hour).integers(-6, 7, (H, W, 3))
            img = Image.fromarray(np.clip(np.asarray(img, np.int16) + noise, 0, 255).astype(np.uint8))
            # brightness change in the afternoon (lighting, not a camera move)
            if hour == 15:
                img = Image.fromarray(np.clip(np.asarray(img, np.float32) * 0.8, 0, 255).astype(np.uint8))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=92)
            entry = f"{PREFIX}{stamp}.jpg"
            photos[entry] = buf.getvalue()
            entries.append((entry, (ox - 100, oy - 60)))
    # byte-identical duplicates: download-suffix copy and "(1)" copy
    first = entries[0][0]
    dl = first.replace(".jpg", " - 2026-10-07T172716.781.jpg")
    cp = first.replace(".jpg", " (1).jpg")
    photos[dl] = photos[first]
    photos[cp] = photos[first]
    entries += [(dl, (0, 0)), (cp, (0, 0))]
    with zipfile.ZipFile(root / "Data" / "Example_Store_-_#000_-_Example,_XX-test.zip", "w") as z:
        for name, data in photos.items():
            z.writestr(name, data)
    ann_zip = run / "chunks" / "part_000001" / "annotations" / "needs_review.zip"
    with zipfile.ZipFile(ann_zip, "w") as z:
        for i, (entry, (dx, dy)) in enumerate(entries):
            doc = {"version": "5.2.1", "flags": {}, "shapes": _shapes(dx, dy), "imagePath": entry,
                   "imageData": None, "imageHeight": H, "imageWidth": W}
            if i == 3:
                doc["imageWidth"] = W + 10       # size mismatch -> held
            payload = json.dumps(doc).encode()
            name = f"P0/{entry.lower()}__{i:04d}.json"
            z.writestr(name, payload)
            rec = {"id": f"P0:{i}:{i}", "source_json": {"ordinal": i, "entry": entry.replace(".jpg", ".json"),
                                                         "sha256": "x", "error": None, "match_method": "basename_imagePath"},
                   "reference_id": "BR04", "actions": [], "unresolved": [{"code": "DUPLICATE_SELECTION_UNCERTAIN"}],
                   "classification": "needs_review" if i % 5 else "auto_fixed",
                   "source_photo": {"zip": WIN_ROOT + "\\Data\\Example_Store_-_#000_-_Example,_XX-test.zip", "entry": entry,
                                    "sha256": hashlib.sha256(photos[entry]).hexdigest()},
                   "source_json_zip": WIN_ROOT + "\\Data\\processed_json.zip",
                   "label_origin": "automatic_repair_pseudo_label", "human_confirmed": False,
                   "training_eligible": False, "changed": True,
                   "annotation_output": {"zip": WIN_ROOT + "\\automatic_results\\run_fixture\\chunks\\part_000001\\annotations\\needs_review.zip",
                                         "entry": name},
                   "output_json_sha256": hashlib.sha256(payload).hexdigest(), "checkpoint_photo_id": f"P0:{i}",
                   "transfer_evidence": [{"id": "x", "metrics": {"a": 1}}], "coverage": []}
            if i == 5:
                rec["source_photo"]["sha256"] = "0" * 64   # hash mismatch -> held
            if i == 7:
                rec["annotation_output"] = None             # missing JSON -> held
                rec.pop("output_json_sha256")
            records.append(rec)
    # a record of another site and a rejected record (both must be excluded)
    records.append(dict(records[1], id="OTHER", source_photo={"zip": WIN_ROOT + "\\Data\\SITE_B.zip",
                                                              "entry": "x.jpg", "sha256": "y"}))
    records.append(dict(records[2], id="REJ", classification="rejected"))
    with open(run / "manifest.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    (root / "automatic_results" / "latest_active_run.json").write_text(json.dumps(
        {"path": WIN_ROOT + "\\automatic_results\\run_fixture", "run_dir": WIN_ROOT + "\\automatic_results\\run_fixture",
         "status": "complete"}), encoding="utf-8")
    pkg = Path(__file__).resolve().parent.parent
    site = json.loads((pkg / "config" / "site_example.json").read_text(encoding="utf-8"))
    site["paths"]["preprocess_root"] = WIN_ROOT
    site["paths"]["output_root"] = str(root / "out")
    site["paths"]["store"] = str(root / "out" / "store.sqlite")
    site["paths"].pop("teacher_surfaces", None)
    site["path_map"] = {WIN_ROOT: str(root)}
    site["taxonomy"] = str(pkg / "config" / "taxonomy.json")
    site["view_detection"].update(min_inliers=25, max_side=640)
    site["export"]["size"] = [320, 240]
    site["export"]["min_valid_pixels"] = 16
    site["split"]["fractions"] = {"train": 0.5, "val": 0.25, "test": 0.25}
    site["split"].update(policy="temporal_blocks", gap_blocks=1)   # the legacy policy is tested explicitly
    site["view_detection"]["coarse_stride"] = 1
    cfg = root / "site.json"
    cfg.write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"root": root, "config": cfg, "photo_zip": root / "Data" / "Example_Store_-_#000_-_Example,_XX-test.zip", "ann_zip": ann_zip,
            "manifest": run / "manifest.jsonl", "entries": entries, "n_photos": len(entries)}


def file_hashes(paths) -> dict:
    return {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
