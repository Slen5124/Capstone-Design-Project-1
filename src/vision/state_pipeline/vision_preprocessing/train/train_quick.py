"""Quick training check: frozen pretrained encoder + decoder (linear / unet / upernet) + two heads.

    semantic head : 8 object classes  (semantic_masks, automatic pseudo-labels -> lower loss weight)
    state head    : 4 classes  0 not_installed / 1 installed   (state_masks/<activity>, human-approved)
                               2 occluded                      (visibility_masks == 2, human-reviewed)
                               3 not_target (floor / ceiling pixels outside the wall ROIs - "this is not a wall")
    255 = ignore in both.  Model input is RGB only.
    installed ratio = installed / (installed + not_installed); occluded / not_target pixels are left out.

Reads one or more export folders made by `vp auto` (exports/<..>_train_full). Writes to --out:
    model_quick.pt (decoder + heads + config; encoder weights are NOT saved - they come from timm),
    metrics.json (val/test IoU, per-date installed-ratio error vs the human labels), log.csv, overlays/*.jpg

This is a sanity check of the architecture on the data we have. Scores from a single site say nothing about
accuracy on a new site (that needs a held-out city).

    python train_quick.py --export "<...>\\exports\\20261014T062524Z_bcf8_train_full" --decoder unet
    python train_quick.py --list-encoders            (which DINOv3 / DINOv2 / ConvNeXt weights timm offers)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F

N_SEM = 8
N_STATE = 4                       # not_installed, installed, occluded, not_target
NOT_TARGET_SEM = (1, 2)           # floor, ceiling
OCCLUDER_IDS = (5, 6, 7)          # construction_materials, tools, person (label_map.json of the export)
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


# ------------------------------------------------------------------ data
def read_export(export: Path, activity: str):
    meta = [json.loads(l) for l in (export / "metadata.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    splits = {}
    for s in ("train", "val", "test"):
        p = export / "splits" / f"{s}.txt"
        splits[s] = set(p.read_text(encoding="utf-8").split()) if p.exists() else set()
    targets = json.loads((export / "targets.json").read_text(encoding="utf-8")) if (export / "targets.json").exists() else {"targets": []}
    tindex = {t["index"]: t["target_id"] for t in targets.get("targets", [])}
    wall_idx = [t["index"] for t in targets.get("targets", [])
                if t.get("surface_type", "wall" if str(t.get("target_id", "")).startswith("W") else "") == "wall"]
    items = []
    for m in meta:
        sid = m["sample_id"]
        split = next((s for s, ids in splits.items() if sid in ids), m.get("split"))
        if split not in ("train", "val", "test"):
            continue
        state = export / "state_masks" / activity / f"{sid}.png"
        if not state.exists():
            continue
        items.append({"id": sid, "split": split, "export": str(export),
                      "image": str(export / m["files"]["image"]), "semantic": str(export / m["files"]["semantic"]),
                      "target": str(export / m["files"]["target"]), "state": str(state),
                      "visibility": str(export / m["files"]["visibility"]),
                      "date": (m.get("capture") or {}).get("time_local", "")[:10], "tindex": tindex,
                      "wall_idx": wall_idx})
    return items


def load_item(it):
    img = np.asarray(Image.open(it["image"]).convert("RGB"), dtype=np.uint8)
    sem = np.asarray(Image.open(it["semantic"]), dtype=np.uint8)
    st = np.asarray(Image.open(it["state"]), dtype=np.uint8)
    vis = np.asarray(Image.open(it["visibility"]), dtype=np.uint8)
    tg = np.asarray(Image.open(it["target"]), dtype=np.uint8)
    return img, sem, st, vis, tg


def state_target(st, vis, sem, tg, wall_idx):
    """4-class state target. Floor / ceiling ROIs (not_applicable activities) count as 'not a wall target'."""
    t = st.copy()
    in_wall = np.isin(tg, wall_idx)
    t[(st == 255) & (vis == 2) & in_wall] = 2
    t[(st == 255) & ~in_wall & (tg != 255) & np.isin(sem, NOT_TARGET_SEM)] = 3
    return t


class SegData(torch.utils.data.Dataset):
    def __init__(self, items, train=False, copy_paste=0.0, seed=0):
        self.items, self.train, self.copy_paste = items, train, copy_paste
        self.rng = random.Random(seed)

    def __len__(self):
        return len(self.items)

    def _photometric(self, img):
        x = img.astype(np.float32)
        x = x * self.rng.uniform(0.6, 1.3) + self.rng.uniform(-20, 20)            # brightness / contrast (day-night)
        x = x * np.array([self.rng.uniform(0.9, 1.1) for _ in range(3)], np.float32)  # colour cast (lights)
        return np.clip(x, 0, 255).astype(np.uint8)

    def __getitem__(self, i):
        it = self.items[i]
        img, sem, st, vis, tg = load_item(it)
        img, sem = img.copy(), sem.copy()
        st = state_target(st, vis, sem, tg, it["wall_idx"])
        if self.train:
            if self.copy_paste and self.rng.random() < self.copy_paste:
                # paste the occluders (people / materials / tools) of another training photo over this one;
                # those pixels become that occluder class; state = occluded inside an ROI, ignore elsewhere
                dimg, dsem, _, _, _ = load_item(self.items[self.rng.randrange(len(self.items))])
                if dsem.shape == sem.shape:
                    m = np.isin(dsem, OCCLUDER_IDS)
                    img[m] = dimg[m]
                    sem[m] = dsem[m]
                    in_wall = np.isin(tg, it["wall_idx"])
                    st[m & in_wall] = 2
                    st[m & ~in_wall] = 255
            img = self._photometric(img)
        x = torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
        x = (x - MEAN) / STD
        return x, torch.from_numpy(sem.astype(np.int64)), torch.from_numpy(st.astype(np.int64)), i


# ------------------------------------------------------------------ model
def conv_bn(i, o, k=3):
    return nn.Sequential(nn.Conv2d(i, o, k, padding=k // 2, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class UNetDecoder(nn.Module):
    def __init__(self, chans, out=(256, 128, 64, 32, 16)):
        super().__init__()
        c4, c3, c2, c1 = chans[3], chans[2], chans[1], chans[0]
        self.b1 = nn.Sequential(conv_bn(c4 + c3, out[0]), conv_bn(out[0], out[0]))
        self.b2 = nn.Sequential(conv_bn(out[0] + c2, out[1]), conv_bn(out[1], out[1]))
        self.b3 = nn.Sequential(conv_bn(out[1] + c1, out[2]), conv_bn(out[2], out[2]))
        self.b4 = nn.Sequential(conv_bn(out[2], out[3]), conv_bn(out[3], out[3]))
        self.b5 = nn.Sequential(conv_bn(out[3], out[4]), conv_bn(out[4], out[4]))
        self.out_ch = out[4]

    def forward(self, f, size):
        x = f[3]
        for blk, skip in ((self.b1, f[2]), (self.b2, f[1]), (self.b3, f[0])):
            x = F.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False)
            x = blk(torch.cat([x, skip], 1))
        x = self.b4(F.interpolate(x, scale_factor=2, mode="bilinear", align_corners=False))
        return self.b5(F.interpolate(x, size=size, mode="bilinear", align_corners=False))


class UPerNetDecoder(nn.Module):
    def __init__(self, chans, ch=128, bins=(1, 2, 3, 6)):
        super().__init__()
        self.ppm = nn.ModuleList([nn.Sequential(nn.AdaptiveAvgPool2d(b), conv_bn(chans[3], ch, 1)) for b in bins])
        self.ppm_out = conv_bn(chans[3] + ch * len(bins), ch)
        self.lat = nn.ModuleList([conv_bn(c, ch, 1) for c in chans[:3]])
        self.fpn = nn.ModuleList([conv_bn(ch, ch) for _ in chans[:3]])
        self.fuse = conv_bn(ch * 4, ch)
        self.out_ch = ch

    def forward(self, f, size):
        top = f[3]
        p = [top] + [F.interpolate(m(top), size=top.shape[-2:], mode="bilinear", align_corners=False) for m in self.ppm]
        x = self.ppm_out(torch.cat(p, 1))
        outs = [x]
        for i in (2, 1, 0):
            lat = self.lat[i](f[i])
            x = lat + F.interpolate(x, size=lat.shape[-2:], mode="bilinear", align_corners=False)
            outs.insert(0, self.fpn[i](x))
        s = outs[0].shape[-2:]
        x = self.fuse(torch.cat([F.interpolate(o, size=s, mode="bilinear", align_corners=False) for o in outs], 1))
        return F.interpolate(x, size=size, mode="bilinear", align_corners=False)


class LinearDecoder(nn.Module):
    """'Linear probe': all encoder stages resized to 1/4 and concatenated; no learned spatial mixing."""

    def __init__(self, chans):
        super().__init__()
        self.bn = nn.BatchNorm2d(sum(chans))
        self.out_ch = sum(chans)

    def forward(self, f, size):
        s = f[0].shape[-2:]
        x = self.bn(torch.cat([F.interpolate(t, size=s, mode="bilinear", align_corners=False) for t in f], 1))
        return F.interpolate(x, size=size, mode="bilinear", align_corners=False)


class TwoHeadSeg(nn.Module):
    def __init__(self, encoder: str, decoder: str, pretrained=True, freeze=True):
        super().__init__()
        import timm
        self.encoder = timm.create_model(encoder, pretrained=pretrained, features_only=True, out_indices=(0, 1, 2, 3))
        chans = self.encoder.feature_info.channels()
        self.freeze = freeze
        if freeze:
            for p in self.encoder.parameters():
                p.requires_grad_(False)
            self.encoder.eval()
        self.decoder = {"unet": UNetDecoder, "upernet": UPerNetDecoder, "linear": LinearDecoder}[decoder](chans)
        self.sem_head = nn.Conv2d(self.decoder.out_ch, N_SEM, 1)
        self.state_head = nn.Conv2d(self.decoder.out_ch, N_STATE, 1)

    def train(self, mode=True):
        super().train(mode)
        if self.freeze:
            self.encoder.eval()
        return self

    def forward(self, x):
        if self.freeze:
            with torch.no_grad():
                f = self.encoder(x)
        else:
            f = self.encoder(x)
        y = self.decoder(f, x.shape[-2:])
        return self.sem_head(y), self.state_head(y)


# ------------------------------------------------------------------ metrics
class Confusion:
    def __init__(self, n):
        self.n, self.m = n, np.zeros((n, n), np.int64)

    def add(self, pred, gt):
        v = gt != 255
        self.m += np.bincount(self.n * gt[v].astype(np.int64) + pred[v], minlength=self.n * self.n).reshape(self.n, self.n)

    def iou(self):
        tp = np.diag(self.m).astype(float)
        den = self.m.sum(0) + self.m.sum(1) - tp
        return [float(t / d) if d > 0 else None for t, d in zip(tp, den)]


def _ratio(pred, region):
    n1 = int(((pred == 1) & region).sum())
    n0 = int(((pred == 0) & region).sum())
    n2 = int(((pred == 2) & region).sum())
    return (n1 / (n0 + n1) if n0 + n1 else None), ((n0 + n1) / (n0 + n1 + n2) if n0 + n1 + n2 else None)


def evaluate(model, items, device, amp, overlay_dir=None, n_overlay=6):
    """Three installed-ratio errors, from easiest to the real inference situation:
         label_region : over the pixels the human label marks observable (old metric, optimistic)
         roi_model    : over the whole ROI; the model itself decides occluded / not_target pixels
         image_model  : no ROI at all; all pixels the model calls installed / not_installed (new-site mode),
                        compared with the human ratio over all observable target pixels of the photo"""
    model.eval()
    sem_c, st_c = Confusion(N_SEM), Confusion(N_STATE)
    per = defaultdict(lambda: defaultdict(list))
    img_rows = defaultdict(lambda: defaultdict(list))
    loader = torch.utils.data.DataLoader(SegData(items), batch_size=2, shuffle=False, num_workers=0)
    shown = 0
    with torch.no_grad():
        for x, sem, st, idx in loader:
            with torch.autocast(device_type=device.type, enabled=amp):
                ls, lt = model(x.to(device))
            ps = ls.argmax(1).cpu().numpy()
            pt = lt.argmax(1).cpu().numpy()
            for b in range(x.shape[0]):
                it = items[int(idx[b])]
                g_sem, g_t = sem[b].numpy(), st[b].numpy()
                sem_c.add(ps[b], g_sem)
                st_c.add(pt[b], g_t)
                g_raw = np.asarray(Image.open(it["state"]), dtype=np.uint8)
                tg = np.asarray(Image.open(it["target"]), dtype=np.uint8)
                obs = g_raw != 255
                for ti, tid in it["tindex"].items():
                    roi = tg == ti
                    m = obs & roi
                    if m.sum() < 500:
                        continue
                    d = per[(it["date"], tid)]
                    d["gt"].append(float((g_raw[m] == 1).mean()))
                    d["gt_obs"].append(float(m.sum() / max(roi.sum(), 1)))
                    d["label_region"].append(_ratio(pt[b], m)[0])
                    r, o = _ratio(pt[b], roi)
                    d["roi_model"].append(r)
                    d["roi_model_obs"].append(o)
                all_obs = obs & (tg > 0) & (tg != 255)
                if all_obs.sum() >= 500:
                    img_rows[it["date"]]["gt"].append(float((g_raw[all_obs] == 1).mean()))
                    img_rows[it["date"]]["image_model"].append(_ratio(pt[b], np.ones_like(obs))[0])
                if overlay_dir is not None and shown < n_overlay:
                    save_overlay(it, pt[b], g_raw, overlay_dir / f"{it['id']}.jpg")
                    shown += 1

    def mean(v):
        v = [a for a in v if a is not None]
        return round(float(np.mean(v)), 4) if v else None

    rows = [{"date": dt, "target": t, "gt_ratio": mean(v["gt"]), "gt_observed": mean(v["gt_obs"]),
             "label_region": mean(v["label_region"]), "roi_model": mean(v["roi_model"]),
             "roi_model_observed": mean(v["roi_model_obs"])} for (dt, t), v in sorted(per.items())]
    irows = [{"date": dt, "gt_ratio": mean(v["gt"]), "image_model": mean(v["image_model"])} for dt, v in sorted(img_rows.items())]

    def mae(rs, key):
        e = [abs(r["gt_ratio"] - r[key]) for r in rs if r.get(key) is not None and r["gt_ratio"] is not None]
        return (round(float(np.mean(e)), 4), round(float(np.max(e)), 4)) if e else (None, None)

    sem_iou, st_iou = sem_c.iou(), st_c.iou()
    present = [v for v in sem_iou if v is not None]
    out = {"state_iou": dict(zip(("not_installed", "installed", "occluded", "not_target"), st_iou)),
           "state_miou_install": float(np.mean([v for v in st_iou[:2] if v is not None])) if any(v is not None for v in st_iou[:2]) else None,
           "semantic_iou": dict(zip(("wall", "floor", "ceiling", "stud", "hvac", "materials", "tools", "person"), sem_iou)),
           "semantic_miou_present": float(np.mean(present)) if present else None}
    for key, rs in (("label_region", rows), ("roi_model", rows), ("image_model", irows)):
        out[f"ratio_mae_{key}"], out[f"ratio_maxerr_{key}"] = mae(rs, key)
    out.update(ratio_rows=rows, image_rows=irows,
               note="single-site numbers say nothing about a new site; roi_model / image_model are the inference-like ones")
    return out


def save_overlay(it, pred_state, gt_state, path):
    img = np.asarray(Image.open(it["image"]).convert("RGB")).astype(np.float32)
    colours = ((1, (0, 200, 80)), (0, (230, 40, 40)), (2, (150, 150, 150)))
    ov, gt = img.copy(), img.copy()
    for v, c in colours:
        ov[pred_state == v] = ov[pred_state == v] * 0.45 + np.array(c) * 0.55
    for v, c in colours[:2]:
        gt[gt_state == v] = gt[gt_state == v] * 0.45 + np.array(c) * 0.55
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate([img, gt, ov], 1).astype(np.uint8)).save(path, quality=85)


def semantic_class_weights(items, cap=10.0):
    """sqrt(median / freq) per class from the training masks, so rare classes (person, tools) are not ignored."""
    cnt = np.zeros(N_SEM, np.float64)
    for it in items:
        s = np.asarray(Image.open(it["semantic"]), dtype=np.uint8)
        cnt += np.bincount(s[s != 255].ravel(), minlength=256)[:N_SEM]
    f = cnt / max(cnt.sum(), 1)
    med = np.median(f[f > 0]) if (f > 0).any() else 1.0
    w = np.where(f > 0, np.sqrt(med / np.maximum(f, 1e-12)), 1.0)
    return torch.tensor(np.clip(w, 0.5, cap), dtype=torch.float32), f

# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", nargs="+", type=Path, help="export folder(s) (exports/<..>_train_full)")
    ap.add_argument("--activity", default="wall_board_installation")
    ap.add_argument("--encoder", default="convnext_tiny.fb_in22k",
                    help="timm model name; DINOv3 ConvNeXt-Tiny once access is granted (see --list-encoders)")
    ap.add_argument("--decoder", choices=("unet", "upernet", "linear"), default="unet")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--sem-weight", type=float, default=0.5)
    ap.add_argument("--copy-paste", type=float, default=0.5, help="probability of occluder copy-paste per training photo")
    ap.add_argument("--no-class-weights", action="store_true", help="plain semantic loss (no rare-class weighting)")
    ap.add_argument("--no-freeze", action="store_true", help="also train the encoder (not recommended with few labels)")
    ap.add_argument("--no-pretrained", action="store_true", help="random encoder weights (tests only)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="use only N photos per split (smoke test)")
    ap.add_argument("--list-encoders", action="store_true")
    args = ap.parse_args(argv)
    import timm
    if args.list_encoders:
        for pat in ("*dinov3*", "*dinov2*", "convnext_tiny*"):
            print(pat, timm.list_pretrained(pat)[:30])
        return 0
    if not args.export:
        ap.error("--export is required")
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    items = [it for e in args.export for it in read_export(e, args.activity)]
    split = {s: [it for it in items if it["split"] == s] for s in ("train", "val", "test")}
    if args.limit:
        split = {s: v[: args.limit] for s, v in split.items()}
    print({s: len(v) for s, v in split.items()}, flush=True)
    if not split["train"]:
        print("no training photos")
        return 2
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    print(f"device {device}" + (f" ({torch.cuda.get_device_name(0)})" if amp else " - CPU is very slow"), flush=True)
    model = TwoHeadSeg(args.encoder, args.decoder, pretrained=not args.no_pretrained, freeze=not args.no_freeze).to(device)
    trainable = [p for p in model.parameters() if p.requires_grad]
    print(f"encoder {args.encoder}  decoder {args.decoder}  trainable params {sum(p.numel() for p in trainable)/1e6:.2f}M  "
          f"frozen {sum(p.numel() for p in model.encoder.parameters())/1e6:.2f}M", flush=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = args.out or Path(args.export[0]).parent.parent / "train_quick" / f"{stamp}_{args.encoder.replace('/', '_')}_{args.decoder}"
    out.mkdir(parents=True, exist_ok=True)
    opt = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=1e-4)
    steps = args.epochs * math.ceil(len(split["train"]) / args.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=max(steps, 1), pct_start=0.1)
    scaler = torch.amp.GradScaler(device.type, enabled=amp)
    ce = nn.CrossEntropyLoss(ignore_index=255)
    w_sem, freq = semantic_class_weights(split["train"])
    ce_sem = nn.CrossEntropyLoss(ignore_index=255, weight=w_sem.to(device) if not args.no_class_weights else None)
    print("semantic class freq", np.round(freq, 4).tolist(), "weights", np.round(w_sem.numpy(), 2).tolist(), flush=True)
    loader = torch.utils.data.DataLoader(SegData(split["train"], train=True, copy_paste=args.copy_paste, seed=args.seed),
                                         batch_size=args.batch, shuffle=True, num_workers=0, drop_last=False)
    best, log = -1e9, []
    for ep in range(1, args.epochs + 1):
        model.train()
        t0, tot, n = time.time(), 0.0, 0
        for x, sem, st, _ in loader:
            x, sem, st = x.to(device), sem.to(device), st.to(device)
            with torch.autocast(device_type=device.type, enabled=amp):
                ls, lt = model(x)
                loss_s = ce_sem(ls.float(), sem) if (sem != 255).any() else ls.sum() * 0
                loss_t = ce(lt.float(), st) if (st != 255).any() else lt.sum() * 0
                loss = args.sem_weight * loss_s + loss_t
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            tot += float(loss) * x.shape[0]
            n += x.shape[0]
        row = {"epoch": ep, "loss": round(tot / max(n, 1), 4), "sec": round(time.time() - t0, 1)}
        if split["val"] and (ep % max(1, args.epochs // 10) == 0 or ep == args.epochs):
            v = evaluate(model, split["val"], device, amp)
            row.update(val_install_miou=v["state_miou_install"], val_sem_miou=v["semantic_miou_present"],
                       val_mae_roi_model=v["ratio_mae_roi_model"], val_mae_image_model=v["ratio_mae_image_model"])
            score = -(v["ratio_mae_roi_model"] if v["ratio_mae_roi_model"] is not None else 1.0)   # select on the inference-like error
            if score > best:
                best = score
                torch.save({"encoder": args.encoder, "decoder": args.decoder, "n_sem": N_SEM,
                            "state_dict": {k: t for k, t in model.state_dict().items() if not k.startswith("encoder.")},
                            "input": {"mean": MEAN.flatten().tolist(), "std": STD.flatten().tolist(), "rgb_only": True},
                            "exports": [str(e) for e in args.export], "epoch": ep, "val": v,
                            "license_note": "encoder weights from timm; DINOv3 weights fall under the DINOv3 License"},
                           out / "model_quick.pt")
        log.append(row)
        print(json.dumps(row), flush=True)
    with open(out / "log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in log for k in r}))
        w.writeheader()
        w.writerows(log)
    result = {"encoder": args.encoder, "decoder": args.decoder, "epochs": args.epochs, "counts": {s: len(v) for s, v in split.items()},
              "device": str(device)}
    if (out / "model_quick.pt").exists():
        ck = torch.load(out / "model_quick.pt", map_location=device, weights_only=False)
        model.load_state_dict(ck["state_dict"], strict=False)
        result["best_epoch"] = ck["epoch"]
    for s in ("val", "test"):
        if split[s]:
            result[s] = evaluate(model, split[s], device, amp, overlay_dir=out / "overlays" / s)
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    t = result.get("test") or {}
    si, se = t.get("state_iou") or {}, t.get("semantic_iou") or {}
    f = lambda v: "-" if v is None else f"{v:.3f}"
    print(f"\nRESULT {args.encoder}+{args.decoder} (test)\n"
          f"  state IoU   not_installed {f(si.get('not_installed'))}  installed {f(si.get('installed'))}  "
          f"occluded {f(si.get('occluded'))}  not_target {f(si.get('not_target'))}\n"
          f"  semantic    mIoU {f(t.get('semantic_miou_present'))}  person {f(se.get('person'))}  tools {f(se.get('tools'))}  "
          f"materials {f(se.get('materials'))}\n"
          f"  ratio MAE   label_region {f(t.get('ratio_mae_label_region'))}  roi_model {f(t.get('ratio_mae_roi_model'))}  "
          f"image_model {f(t.get('ratio_mae_image_model'))}   (max roi_model {f(t.get('ratio_maxerr_roi_model'))})\n-> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
