"""Inference: photos of one camera view -> state map -> ROI (given or proposed) -> installed ratio -> vision_output.json

    python -m vision_preprocessing.train.infer_quick --model <train_quick out>\\model_quick.pt ^
        --images "<folder with the view's photos>" --site-id SITE_B --view V01 --out <out folder>

ROI, in this order:
    --roi-mask roi.png --roi-targets targets.json   ROI drawn / approved in preprocessing (export size, 0 none, k target)
    <out>/roi/<view>_roi.png + .json                 ROI proposed earlier for this view (reused, stays fixed)
    otherwise                                         proposed from the first --roi-photos usable photos (A01, A02 ...
                                                      left to right, provisional until a person maps / confirms them)
Model input is RGB only. Run per camera view (photos of a moved camera need their own view / ROI).
Outputs: vision_output.json, photo_rows.jsonl, roi/<view>_roi.png|json, overlays/<date>_<photo>.jpg (first photo per day).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import torch

from ..rasterize import apply_image, make_transform, save_mask_png
from .infer_core import DEFAULT_TIME_RE, RULES, capture_time, daily_rows, measure, propose_roi, vision_output, write_json
from .train_quick import MEAN, STD, TwoHeadSeg

IMG_EXT = {".jpg", ".jpeg", ".png"}
COLOURS = {1: (0, 200, 80), 0: (230, 40, 40), 2: (150, 150, 150)}


def list_photos(paths, regex):
    files = []
    for p in map(Path, paths):
        files += sorted(f for f in p.rglob("*") if f.suffix.lower() in IMG_EXT) if p.is_dir() else [p]
    out, skipped = [], []
    for f in files:
        t = capture_time(f.name, regex)
        (out.append((t, f)) if t else skipped.append(f.name))
    return sorted(out), skipped


def load_model(path, device, pretrained=True):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    model = TwoHeadSeg(ck["encoder"], ck["decoder"], pretrained=pretrained, freeze=True, n_state=ck.get("n_state", 4),
                       vit_pad_crop=ck.get("vit_pad_crop", False))   # checkpoints older than the fix stretched the grid
    missing, unexpected = model.load_state_dict(ck["state_dict"], strict=False)
    bad = [k for k in missing if not k.startswith("encoder.")] + list(unexpected)
    if bad:
        raise SystemExit(f"checkpoint does not match the model: {bad[:5]}")
    return model.to(device).eval(), ck


def predict(model, img: Image.Image, T, device, slot=None):
    """State map 0 not_installed / 1 installed / 2 occluded / 3 not_target. For a unified (wall + ceiling) model `slot`
    picks the surface: its two classes become 0/1, the other surface's classes become not_target."""
    x = np.asarray(apply_image(img.convert("RGB"), T), dtype=np.uint8)
    t = (torch.from_numpy(x).permute(2, 0, 1).float() / 255.0 - MEAN) / STD
    with torch.no_grad(), torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
        _, ls = model(t[None].to(device))
    p = torch.softmax(ls.float(), 1)[0]
    bad = (~torch.isfinite(p).all(0)).cpu().numpy()          # NaN / Inf pixels: argmax would call them class 0 (not installed)
    conf, state = p.max(0)
    state = state.cpu().numpy().astype(np.uint8)
    n = p.shape[0]
    if n > 4 and slot is not None:
        occ, nt = n - 2, n - 1
        m = np.full(n, 3, np.uint8)
        m[2 * slot], m[2 * slot + 1], m[occ], m[nt] = 0, 1, 2, 3
        state = m[state]
    conf = conf.cpu().numpy()
    if bad.any():                                            # neither installed nor not installed: left out of the ratio
        state[bad], conf[bad] = 255, 0.0
    return x, state, conf


def overlay(x, state, roi, path):
    img = x.astype(np.float32)
    ov = img.copy()
    inside = roi > 0
    for v, c in COLOURS.items():
        m = inside & (state == v)
        ov[m] = ov[m] * 0.45 + np.array(c) * 0.55
    edge = inside & ~(np.roll(inside, 1, 0) & np.roll(inside, -1, 0) & np.roll(inside, 1, 1) & np.roll(inside, -1, 1))
    ov[edge] = (255, 220, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate([img, ov], 1).astype(np.uint8)).save(path, quality=85)


def usable(x) -> bool:
    return float(x.mean()) >= 40.0          # skip night / black frames for the ROI proposal


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--images", required=True, nargs="+")
    ap.add_argument("--site-id", required=True)
    ap.add_argument("--view", default="V01")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--activity", default=None, help="default: the activity the model was trained on")
    ap.add_argument("--roi-mask", type=Path, help="approved ROI mask at export size (0 none, k = target k)")
    ap.add_argument("--roi-targets", type=Path, help="targets.json of the export (index -> target_id)")
    ap.add_argument("--roi-photos", type=int, default=3, help="usable photos used to propose the ROI")
    ap.add_argument("--time-regex", default=DEFAULT_TIME_RE)
    ap.add_argument("--size", type=int, nargs=2, default=None, help="W H (default: the export size stored in the model)")
    ap.add_argument("--no-pretrained", action="store_true", help="tests only")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, ck = load_model(args.model, device, pretrained=not args.no_pretrained)
    activity = args.activity or ck.get("activity", "wall_board_installation")
    slot = None
    if ck.get("n_state", 4) > 4:                     # unified wall + ceiling model: choose the surface to measure
        acts = ["wall_board_installation", "ceiling_panel_installation"]
        if activity == "all":
            activity = acts[0]
        if activity not in acts:
            raise SystemExit(f"unified model knows {acts}; pass --activity one of them")
        slot = acts.index(activity)
        print(f"[infer] unified model, measuring {activity} (surface slot {slot})", flush=True)
    if ck.get("state_outside", "roi") != "full" and not args.roi_mask:
        print("[infer] warning: this model was trained with --state-outside roi; automatic ROI proposals will be poor. "
              "Pass --roi-mask or retrain with the default (full).", flush=True)
    size = args.size or ck.get("export_size") or [1024, 576]
    photos, skipped = list_photos(args.images, args.time_regex)
    if skipped:
        print(f"[infer] {len(skipped)} files without a capture time in the name were skipped (e.g. {skipped[0]})", flush=True)
    if not photos:
        raise SystemExit("no photos")
    roi_dir = args.out / "roi"
    roi_png, roi_json = roi_dir / f"{args.view}_roi.png", roi_dir / f"{args.view}_roi.json"

    cache = {}

    def run(i):
        if i not in cache:
            t, f = photos[i]
            img = Image.open(f)
            T = make_transform(img.size, size, "letterbox")
            cache[i] = predict(model, img, T, device, slot)
        return cache[i]

    # ---- ROI
    if args.roi_mask:
        roi = np.asarray(Image.open(args.roi_mask), dtype=np.uint8).copy()
        roi[roi == 255] = 0
        tj = json.loads(args.roi_targets.read_text(encoding="utf-8")) if args.roi_targets else {"targets": []}
        names = {t["index"]: t for t in tj.get("targets", [])}
        targets = [{"index": int(k), "target_id": names.get(int(k), {}).get("target_id", f"T{int(k):02d}"),
                    "surface_type": names.get(int(k), {}).get("surface_type", "wall"), "roi_status": "human_confirmed"}
                   for k in np.unique(roi) if k > 0 and names.get(int(k), {}).get("surface_type", "wall") == "wall"]
        roi_info = {"source": "given", "file": str(args.roi_mask)}
    elif roi_png.exists() and roi_json.exists():
        roi = np.asarray(Image.open(roi_png), dtype=np.uint8)
        saved = json.loads(roi_json.read_text(encoding="utf-8"))
        targets, roi_info = saved["targets"], {"source": "reused", "file": str(roi_png), **saved.get("info", {})}
    else:
        picked, maps = [], []
        for i in range(len(photos)):
            x, st, _ = run(i)
            if usable(x):
                picked.append(photos[i][1].name)
                maps.append(st)
            if len(maps) >= args.roi_photos:
                break
        roi, targets = propose_roi(maps)
        roi_info = {"source": "proposed", "photos": picked, "rule": f"measurable in >= {RULES['roi_vote']} of the photos"}
        roi_dir.mkdir(parents=True, exist_ok=True)
        save_mask_png(roi, roi_png)
        write_json(roi_json, {"view_id": args.view, "targets": targets, "info": roi_info})
        print(f"[infer] ROI proposed from {len(maps)} photos: {[t['target_id'] for t in targets]} -> {roi_png}", flush=True)
    if tuple(roi.shape) != (size[1], size[0]):
        raise SystemExit(f"ROI mask is {roi.shape[1]}x{roi.shape[0]} but photos are resized to {size[0]}x{size[1]} (use --size)")
    if not targets:
        print("[infer] warning: no ROI region found - every value will be held", flush=True)
    provisional = any(t.get("roi_status") == "provisional" for t in targets)

    # ---- every photo
    rows, seen_days = [], set()
    for i, (t, f) in enumerate(photos):
        x, st, conf = run(i)
        cache.pop(i, None)
        day = t.date().isoformat()
        ev = None
        if day not in seen_days:
            ev = f"overlays/{day}_{f.stem}.jpg"
            overlay(x, st, roi, args.out / ev)
            seen_days.add(day)
        for r in measure(st, roi, targets, conf):
            rows.append(dict(r, date=day, time_local=t.isoformat(), photo=f.name, evidence=ev))
    with open(args.out / "photo_rows.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    daily = daily_rows(rows, args.site_id, activity, args.view, provisional)
    view = {"view_id": args.view, "start_time": photos[0][0].isoformat(), "end_time": photos[-1][0].isoformat(),
            "roi": roi_info, "targets": [{"target_id": t["target_id"], "roi_status": t.get("roi_status")} for t in targets]}
    model_info = {"model_version": f"{ck['encoder']}+{ck['decoder']}@epoch{ck.get('epoch')}", "source": "model",
                  "checkpoint": str(args.model), "state_outside": ck.get("state_outside"),
                  "validation": {"held_out_site": None, "installed_ratio_mae": None,
                                 "note": "fill from metrics.json of a run whose test site was not used for training"}}
    doc = vision_output(args.site_id, daily, targets, view, model_info)
    write_json(args.out / "vision_output.json", doc)
    print(f"[infer] {len(photos)} photos, {len(daily)} daily rows, {len(doc['events'])} events -> {args.out / 'vision_output.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
