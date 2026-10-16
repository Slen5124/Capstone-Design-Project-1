"""Stage "thumbs": one small JPEG + brightness statistics per photo.

Used by the timeline candidates (ROI appearance per day), sample selection
(dark / low-light frames) and the review UI grid (fast thumbnails instead of
decoding 2688x1512 photos). Thumbnails live in the run's cache folder; they
are derived data and can be deleted and rebuilt at any time.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from .runctx import ItemCache, Progress, RunContext, atomic_write_json
from .schemas import content_hash
from .source_adapter import SourceIntegrityError, SourceReader

THUMB_W, THUMB_H = 320, 180
THUMB_CODE = ("thumbs.py", "source_adapter.py")


def thumb_dir(ctx: RunContext) -> Path:
    path = ctx.run_dir / "cache" / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumb_path(ctx: RunContext, image_id: str) -> Path:
    return thumb_dir(ctx) / f"{image_id}.jpg"


def frame_stats(arr: np.ndarray) -> dict:
    gray = arr.mean(axis=2)
    return {"mean": round(float(gray.mean()), 1), "p10": round(float(np.percentile(gray, 10)), 1),
            "p90": round(float(np.percentile(gray, 90)), 1),
            "dark_fraction": round(float((gray <= 20).mean()), 4),
            "overexposed_fraction": round(float((gray >= 250).mean()), 4),
            "rgb_mean": [round(float(v), 1) for v in arr.reshape(-1, 3).mean(axis=0)]}


def light_class(stats: dict, cfg: dict) -> str:
    if stats["mean"] < cfg.get("dark_mean", 30):
        return "dark"
    if stats["mean"] < cfg.get("low_light_mean", 55):
        return "low_light"
    if stats["overexposed_fraction"] > cfg.get("overexposed_fraction", 0.25):
        return "overexposed"
    return "ok"


def build_thumbs(ctx: RunContext, images: list[dict]) -> dict:
    cfg = dict({"dark_mean": 30, "low_light_mean": 55, "overexposed_fraction": 0.25}, **ctx.site.get("thumbs", {}))
    stage = ctx.stage_dir("thumbs")
    cache = ItemCache(stage / "thumbs.jsonl")
    fp_cfg = ctx.config_fingerprint("thumbs", code=THUMB_CODE)
    reader = SourceReader(ctx)
    todo = [im for im in images if im["status"] == "ok"]
    progress = Progress(ctx, "thumbs", len(todo), "images", every=200)
    counts = {}
    try:
        for im in todo:
            fp = content_hash([fp_cfg, im["source_photo"]["sha256"], THUMB_W, THUMB_H])
            path = thumb_path(ctx, im["image_id"])
            hit = cache.get(im["image_id"], fp)
            if hit is not None and path.exists():
                counts[hit["light"]] = counts.get(hit["light"], 0) + 1
                progress.tick("skipped_cached")
                continue
            try:
                rgb, _ = reader.rgb(im, max(THUMB_W, THUMB_H) * 2)
            except SourceIntegrityError as error:
                cache.put(im["image_id"], fp, {"image_id": im["image_id"], "light": "held", "error": error.code})
                progress.tick("held")
                continue
            small = rgb.resize((THUMB_W, THUMB_H), Image.BILINEAR)
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=88)
            path.write_bytes(buf.getvalue())
            stats = frame_stats(np.asarray(small, np.float32))
            value = {"image_id": im["image_id"], "light": light_class(stats, cfg), **stats}
            cache.put(im["image_id"], fp, value)
            counts[value["light"]] = counts.get(value["light"], 0) + 1
            progress.tick("saved")
    finally:
        cache.close()
        cache.maybe_compact()
        reader.close()
    report = {"images": progress.finish(), "light_classes": counts, "config": cfg,
              "meaning": "brightness classes are engineering heuristics for sampling, not image-quality labels"}
    atomic_write_json(stage / "thumbs_report.json", report)
    return report


def load_thumb_stats(ctx: RunContext) -> dict[str, dict]:
    cache = ItemCache(ctx.stage_dir("thumbs") / "thumbs.jsonl")
    return {k: row["value"] for k, row in cache.items.items()}


def load_thumb(ctx: RunContext, image_id: str) -> np.ndarray | None:
    path = thumb_path(ctx, image_id)
    if not path.exists():
        return None
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))
