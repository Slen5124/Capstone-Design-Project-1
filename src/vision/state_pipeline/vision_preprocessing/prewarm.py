"""Decrypt the photos ONCE into the review cache so the review UI opens every photo instantly.

    vp.cmd prewarm --run <run dir>                 every photo of the run (review photos first)
    vp.cmd prewarm --run <run dir> --focus-only    only samples / anchors / labelled photos / references

What it writes: <run>/cache/display/<image_id>_1600.jpg, the same file the review server writes the first time a
photo is opened (so nothing new is introduced - this only does it ahead of time, in parallel). The original ZIPs are
only read. The ZIP password is asked once (or taken from QA_ZIP_PASSWORD), passed to the worker processes in memory
and never written anywhere. Already cached photos are skipped, so it can be stopped (Ctrl+C) and resumed.

Why: SITE_C / SITE_D ZIPs use ZipCrypto, which Python decrypts byte by byte (about a second per photo); doing it once in
several processes is much faster than paying it every time a photo is opened. Disk: ~0.2-0.4 MB per photo.
"""
from __future__ import annotations

import io
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

DISPLAY_SIDE = 1600
_reader = None


def cache_path(run_dir: Path, image_id: str, side: int = DISPLAY_SIDE) -> Path:
    return Path(run_dir) / "cache" / "display" / f"{image_id}_{side}.jpg"


def _init(run_dir: str, password: str | None):
    global _reader
    if password is not None:
        os.environ["QA_ZIP_PASSWORD"] = password          # this worker process only (memory, not disk)
    from .runctx import RunContext
    from .source_adapter import SourceReader
    _reader = SourceReader(RunContext.open(Path(run_dir)))


def _one(run_dir: str, im: dict) -> tuple[str, str]:
    out = cache_path(Path(run_dir), im["image_id"])
    if out.exists():
        return im["image_id"], "cached"
    try:
        rgb, _ = _reader.rgb(im, DISPLAY_SIDE)
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=88)
        tmp = out.with_suffix(".part")
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, out)
        return im["image_id"], "written"
    except Exception as error:  # noqa: BLE001 - one bad photo must not stop the rest
        return im["image_id"], f"error: {type(error).__name__}: {error}"


def focus_ids(ctx) -> set:
    """Photos the reviewer opens first: samples, anchor requests, labelled photos, view / ROI reference photos."""
    import json
    from .runctx import read_jsonl
    store = ctx.store()
    ids = {s["image_id"] for s in read_jsonl(ctx.run_dir / "stages" / "samples" / "samples.jsonl")}
    rep = ctx.run_dir / "stages" / "samples" / "drafts_report.json"
    if rep.exists():
        ids |= {x["image_id"] for x in json.loads(rep.read_text(encoding="utf-8")).get("needs_drawing", [])}
    ids |= {r["key"].split("|")[0] for r in store.latest("state_label")}
    ids |= {r["payload"].get("reference_image_id") for r in store.latest("view")}
    ids |= {r["payload"].get("reference_image_id") for r in store.latest("roi")}
    return ids


def needs_password(images: list[dict], ctx) -> bool:
    import zipfile
    zips = {ctx.map(im["source_photo"]["zip"]) for im in images}
    for z in zips:
        try:
            with zipfile.ZipFile(z) as f:
                if any(i.flag_bits & 1 for i in f.infolist()[:20]):
                    return True
        except (OSError, zipfile.BadZipFile):
            pass
    return False


def prewarm(ctx, focus_only: bool = False, workers: int | None = None, limit: int = 0) -> dict:
    from .source_adapter import load_images
    images = [im for im in load_images(ctx) if im["status"] == "ok" and not im.get("duplicate_of")]
    focus = focus_ids(ctx)
    images.sort(key=lambda im: (im["image_id"] not in focus, im["capture"]["time_local"] or ""))
    if focus_only:
        images = [im for im in images if im["image_id"] in focus]
    todo = [im for im in images if not cache_path(ctx.run_dir, im["image_id"]).exists()]
    if limit:
        todo = todo[:limit]
    cache_path(ctx.run_dir, "x").parent.mkdir(parents=True, exist_ok=True)
    print(f"[prewarm] {len(images)} photos, {len(images) - len(todo)} already cached, {len(todo)} to decrypt", flush=True)
    if not todo:
        return {"photos": len(images), "written": 0, "errors": 0}
    password = os.environ.get("QA_ZIP_PASSWORD")
    if password is None and needs_password(todo, ctx):
        import getpass
        password = getpass.getpass("ZIP password: ")
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
    t0, done, written, errors = time.time(), 0, 0, []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(ctx.run_dir), password)) as pool:
        del password
        futs = [pool.submit(_one, str(ctx.run_dir), im) for im in todo]
        for f in as_completed(futs):
            iid, status = f.result()
            done += 1
            if status == "written":
                written += 1
            elif status.startswith("error"):
                errors.append((iid, status))
            if done % 100 == 0 or done == len(todo):
                rate = done / max(1e-6, time.time() - t0)
                print(f"[prewarm] {done}/{len(todo)}  {rate:.1f} photos/s  ~{(len(todo) - done) / max(rate, 1e-6) / 60:.0f} min left",
                      flush=True)
    for iid, status in errors[:10]:
        print(f"[prewarm] {iid}: {status}", flush=True)
    print(f"[prewarm] done: {written} written, {len(errors)} errors, cache {cache_path(ctx.run_dir, 'x').parent}", flush=True)
    return {"photos": len(images), "written": written, "errors": len(errors)}
