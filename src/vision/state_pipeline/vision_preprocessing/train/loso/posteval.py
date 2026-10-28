"""Test-time variants of a trained model (no training), scored on val and test with the same evaluate():

    base        one forward pass (what training reported)
    flip        mean of the softmax of the photo and of its mirror image
    ms          flip x scales (protocol posteval.tta_scales, e.g. 1.0 / 1.25 / 1.5 of 1024x576), resized back and averaged
    ms_roizoom  ms, then every ROI smaller than roi_zoom_max_frac of the photo is cropped (+ roi_zoom_margin), enlarged
                to the photo size, predicted again (with flip) and pasted back inside that ROI

The ROI is known at inference (approved per camera view), so zooming on it is a legitimate inference step.
Which variant is recommended is decided on VAL only (report.py)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .common import write_csv, write_json


def _probs(model, x, amp):
    import torch
    with torch.autocast(device_type=x.device.type, enabled=amp):
        ls, lt = model(x)
    return torch.softmax(ls.float(), 1), torch.softmax(lt.float(), 1)


def _flip_probs(model, x, amp):
    a_s, a_t = _probs(model, x, amp)
    b_s, b_t = _probs(model, x.flip(-1), amp)
    return (a_s + b_s.flip(-1)) / 2, (a_t + b_t.flip(-1)) / 2


def _resize(t, size):
    import torch.nn.functional as F
    return F.interpolate(t, size=size, mode="bilinear", align_corners=False)


def make_predict(variant: str, protocol: dict, surfaces, amp: bool):
    import torch
    pe = protocol["posteval"]

    def flip(model, x, items):
        with torch.no_grad():
            return _flip_probs(model, x, amp)

    def ms(model, x, items):
        h, w = x.shape[-2:]
        acc_s = acc_t = None
        with torch.no_grad():
            for s in pe["tta_scales"]:
                xs = x if s == 1.0 else _resize(x, (max(14, round(h * s)), max(14, round(w * s))))
                ps, pt = _flip_probs(model, xs, amp)
                if ps.shape[-2:] != (h, w):
                    ps, pt = _resize(ps, (h, w)), _resize(pt, (h, w))
                acc_s = ps if acc_s is None else acc_s + ps
                acc_t = pt if acc_t is None else acc_t + pt
        n = len(pe["tta_scales"])
        return acc_s / n, acc_t / n

    def ms_roizoom(model, x, items):
        ps, pt = ms(model, x, items)
        h, w = x.shape[-2:]
        with torch.no_grad():
            for b, it in enumerate(items):
                tg = np.asarray(Image.open(it["target"]), dtype=np.uint8)
                if tg.shape != (h, w):
                    continue
                for ti in it["slot_of"]:
                    if it.get("only_targets") is not None and ti not in it["only_targets"]:
                        continue
                    roi = tg == ti
                    frac = float(roi.mean())
                    if not (0 < frac < pe["roi_zoom_max_frac"]):
                        continue
                    ys, xs_ = np.where(roi)
                    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs_.min(), xs_.max() + 1
                    m = int(round(pe["roi_zoom_margin"] * max(y1 - y0, x1 - x0)))
                    y0, y1 = max(0, y0 - m), min(h, y1 + m)
                    x0, x1 = max(0, x0 - m), min(w, x1 + m)
                    if y1 - y0 < 32:
                        y0, y1 = max(0, min(y0, h - 32)), max(0, min(y0, h - 32)) + 32
                    if x1 - x0 < 32:
                        x0, x1 = max(0, min(x0, w - 32)), max(0, min(x0, w - 32)) + 32
                    ch, cw = y1 - y0, x1 - x0
                    sc = min(h / ch, w / cw)
                    crop = _resize(x[b:b + 1, :, y0:y1, x0:x1], (max(14, round(ch * sc)), max(14, round(cw * sc))))
                    zs, zt = _flip_probs(model, crop, amp)
                    zs, zt = _resize(zs, (ch, cw))[0], _resize(zt, (ch, cw))[0]
                    sel = torch.from_numpy(roi[y0:y1, x0:x1]).to(x.device)
                    ps[b, :, y0:y1, x0:x1] = torch.where(sel, zs, ps[b, :, y0:y1, x0:x1])
                    pt[b, :, y0:y1, x0:x1] = torch.where(sel, zt, pt[b, :, y0:y1, x0:x1])
        return ps, pt

    return {"flip": flip, "ms": ms, "ms_roizoom": ms_roizoom}[variant]


def _summary(r: dict) -> dict:
    d = {"install_miou": r.get("state_miou_install"), "mae": r.get("ratio_mae_roi_model")}
    for name, v in (r.get("per_surface") or {}).items():
        d[f"{name}_mae"] = v.get("ratio_mae_roi_model")
        d[f"{name}_maxerr"] = v.get("ratio_maxerr_roi_model")
        d[f"{name}_miou"] = v.get("state_miou_install")
    return d


def run_variants(model, split, device, amp, surfaces, protocol, out: Path):
    from ..train_quick import evaluate
    from .common import log, read_json
    out = Path(out)
    summary = {"params": protocol["posteval"], "variants": {}}
    m = read_json(out / "metrics.json", {}) or {}
    summary["variants"]["base"] = {s: _summary(m[s]) for s in ("val", "test") if s in m}
    for v in protocol["posteval"]["variants"]:
        if v == "base":
            continue
        fn = make_predict(v, protocol, surfaces, amp)
        summary["variants"][v] = {}
        for s in ("val", "test"):
            if not split.get(s):
                continue
            r = evaluate(model, split[s], device, amp, surfaces, outside=protocol["train"]["state_outside"], predict=fn)
            write_csv(out / f"photo_rows_{v}_{s}.csv", r["photo_rows"])
            summary["variants"][v][s] = _summary(r)
        log(f"posteval {v}: " + "  ".join(f"{s} wall {d.get('wall_mae')}" for s, d in summary["variants"][v].items()))
    write_json(out / "posteval.json", summary)
    return summary
