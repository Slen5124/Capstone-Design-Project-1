"""Quick training check: frozen pretrained encoder + decoder (linear / unet / upernet) + two heads.

    semantic head : 8 object classes  (semantic_masks, automatic pseudo-labels -> lower loss weight)
    state head    : 4 classes  0 not_installed / 1 installed   (state_masks/<activity>, human-approved)
                               2 occluded                      (visibility_masks == 2, human-reviewed)
                               3 not_target  --state-outside full (default): every non-wall object pixel outside the
                                             wall ROIs (floor, ceiling, duct, materials, tools, person) - so the state
                                             map itself marks "measurable wall" (0/1/2) vs "not a target" (3) and an
                                             ROI can be proposed from it on a new site.  Wall / stud pixels outside the
                                             ROIs stay 255 (could be an untracked wall).
                                             --state-outside roi (old): only floor / ceiling inside floor/ceiling ROIs.
    255 = ignore in both.  Model input is RGB only.
    installed ratio = installed / (installed + not_installed); occluded / not_target pixels are left out.
    --activity all : ONE model for wall + ceiling, 6 state classes
                     0 wall not_installed / 1 wall installed / 2 ceiling not_installed / 3 ceiling installed /
                     4 occluded / 5 not_target.  Metrics are reported per surface.
    --holdout-export DIR --holdout-ids @file : a whole city kept out of training; the photos listed in the file
                     (image_id or image_id|target_id, one per line = the human-labelled ones) are the cross-site
                     test ("holdout"), every other photo of that export is a secondary test against its weak labels
                     ("holdout_weak", capped by --holdout-weak-limit). Nothing from a hold-out export is trained on.

Reads one or more export folders made by `vp auto` (exports/<..>_train_full). Writes to --out:
    model_quick.pt (decoder + heads + config; encoder weights are NOT saved - they come from timm),
    metrics.json (val/test IoU, per-date installed-ratio error vs the human labels, per-photo rows, confusion
    matrices), log.csv (rewritten every epoch), overlays/*.jpg,
    config.json + split_manifest.json (arguments, versions, code hashes, exact photo ids - written before training),
    ckpt/epoch_NN.pt (every evaluated epoch; --no-epoch-ckpt), epoch_metrics.jsonl (val + hold-out per evaluated
    epoch; the hold-out curve is for analysis only, the model is always selected on val; --no-track-holdout)

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

try:
    from .data_prep import N_STATE, Surfaces, _parse_ids, load_item, read_export, state_target
    from .decoders import VIT_NATIVE, MLPHead
except ImportError:                                   # run as a plain script (python train/train_quick.py)
    from data_prep import N_STATE, Surfaces, _parse_ids, load_item, read_export, state_target  # type: ignore
    from decoders import VIT_NATIVE, MLPHead  # type: ignore

N_SEM = 8
OCCLUDER_IDS = (5, 6, 7)          # construction_materials, tools, person (label_map.json of the export)
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
MEAN_U8 = (124, 116, 104)             # canvas colour for geometric augmentation (= ImageNet mean)
class SegData(torch.utils.data.Dataset):
    def __init__(self, items, surfaces: Surfaces, train=False, copy_paste=0.0, seed=0, outside="full", geo=False):
        self.items, self.surfaces, self.train, self.copy_paste, self.outside = items, surfaces, train, copy_paste, outside
        self.geo = geo
        self.rng = random.Random(seed)

    def _geometric(self, img, sem, st):
        """Scale 0.5-1.0 + random placement on a canvas of the original size + horizontal flip: shows the walls smaller,
        shifted and mirrored (the way a farther / differently placed camera sees them). Masks: nearest, padding = 255."""
        h, w = sem.shape
        s = self.rng.uniform(0.5, 1.0) if self.rng.random() < 0.8 else 1.0
        nh, nw = max(32, int(h * s)), max(32, int(w * s))
        if (nh, nw) != (h, w):
            im = Image.fromarray(img).resize((nw, nh), Image.BILINEAR)
            sm = Image.fromarray(sem).resize((nw, nh), Image.NEAREST)
            sa = Image.fromarray(st).resize((nw, nh), Image.NEAREST)
            canvas = np.full((h, w, 3), MEAN_U8, np.uint8)
            csem, cst = np.full((h, w), 255, np.uint8), np.full((h, w), 255, np.uint8)
            y0, x0 = self.rng.randrange(h - nh + 1), self.rng.randrange(w - nw + 1)
            canvas[y0:y0 + nh, x0:x0 + nw] = np.asarray(im)
            csem[y0:y0 + nh, x0:x0 + nw] = np.asarray(sm)
            cst[y0:y0 + nh, x0:x0 + nw] = np.asarray(sa)
            img, sem, st = canvas, csem, cst
        if self.rng.random() < 0.5:
            img, sem, st = img[:, ::-1].copy(), sem[:, ::-1].copy(), st[:, ::-1].copy()
        return img, sem, st

    def __len__(self):
        return len(self.items)

    def _photometric(self, img):
        x = img.astype(np.float32)
        x = x * self.rng.uniform(0.6, 1.3) + self.rng.uniform(-20, 20)            # brightness / contrast (day-night)
        x = x * np.array([self.rng.uniform(0.9, 1.1) for _ in range(3)], np.float32)  # colour cast (lights)
        return np.clip(x, 0, 255).astype(np.uint8)

    def __getitem__(self, i):
        it = self.items[i]
        img, sem, st, vis, tg = load_item(it, self.surfaces)
        img, sem = img.copy(), sem.copy()
        st = state_target(st, vis, sem, tg, it, self.surfaces, self.outside)
        if self.train:
            if self.copy_paste and self.rng.random() < self.copy_paste:
                # paste the occluders (people / materials / tools) of another training photo over this one;
                # those pixels become that occluder class; state = occluded inside an ROI, ignore elsewhere
                dimg, dsem, _, _, _ = load_item(self.items[self.rng.randrange(len(self.items))], self.surfaces)
                if dsem.shape == sem.shape:
                    m = np.isin(dsem, OCCLUDER_IDS)
                    img[m] = dimg[m]
                    sem[m] = dsem[m]
                    in_roi = np.isin(tg, list(it["slot_of"].keys()))
                    st[m & in_roi] = self.surfaces.occ
                    st[m & ~in_roi & (tg != 255)] = self.surfaces.nt if self.outside == "full" else 255
            if self.geo:
                img, sem, st = self._geometric(img, sem, st)
            img = self._photometric(img)
        if it.get("kl"):                       # label-free photo of a new site: only the input is used (KL to the base model)
            sem, st = np.full_like(sem, 255), np.full_like(st, 255)
        x = torch.from_numpy(np.ascontiguousarray(img)).permute(2, 0, 1).float() / 255.0
        x = (x - MEAN) / STD
        return x, torch.from_numpy(sem.astype(np.int64)), torch.from_numpy(st.astype(np.int64)), i


# ------------------------------------------------------------------ model
def conv_bn(i, o, k=3):
    return nn.Sequential(nn.Conv2d(i, o, k, padding=k // 2, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


def conv_gn(i, o, k=1):
    """Conv + GroupNorm: for the pyramid-pooling branches, whose 1x1 / 2x2 maps break BatchNorm when a batch has one photo."""
    return nn.Sequential(nn.Conv2d(i, o, k, padding=k // 2, bias=False), nn.GroupNorm(32, o), nn.ReLU(inplace=True))


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
        self.ppm = nn.ModuleList([nn.Sequential(nn.AdaptiveAvgPool2d(b), conv_gn(chans[3], ch, 1)) for b in bins])
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


DECODERS = ("unet", "upernet", "linear", "mlp", "dpt", "probe")


def is_vit(encoder: str) -> bool:
    return encoder.startswith("vit") or "dinov2" in encoder or "dinov3_vit" in encoder


class ViTPyramid(nn.Module):
    """Plain ViT (DINOv2) gives 4 same-resolution token maps (stride 14). The decoders want a 4-level pyramid at strides
    4 / 8 / 16 / 32, so each selected block is projected (1x1 conv + GroupNorm) and resampled to that scale - the
    'simple feature pyramid' of ViTDet / a light ViT-Adapter. The projections train with the decoder."""

    def __init__(self, in_ch, out=(96, 192, 384, 768)):
        super().__init__()
        self.proj = nn.ModuleList([nn.Sequential(nn.Conv2d(in_ch, o, 1, bias=False), nn.GroupNorm(32, o), nn.GELU()) for o in out])
        self.out = list(out)

    def forward(self, feats, size):
        h, w = size
        outs = []
        for k, (f, m) in enumerate(zip(feats, self.proj)):
            s = 4 * 2 ** k
            outs.append(F.interpolate(m(f), size=(max(1, h // s), max(1, w // s)), mode="bilinear", align_corners=False))
        return outs


class TwoHeadSeg(nn.Module):
    def __init__(self, encoder: str, decoder: str, pretrained=True, freeze=True, n_state=N_STATE, vit_pad_crop=True):
        super().__init__()
        import timm
        self.vit = is_vit(encoder)
        # timm pads a ViT input at the bottom / right to a multiple of the patch size; the token grid covers the PADDED
        # image, so the logits are resized to the padded size and cropped (vit_pad_crop). Old checkpoints (before this
        # fix) stretched the grid over the unpadded image: load them with vit_pad_crop=False.
        self.pad_crop = bool(vit_pad_crop) and self.vit
        if decoder not in DECODERS:
            raise ValueError(f"unknown decoder {decoder!r}; choose from {DECODERS}")
        if self.vit:
            probe = timm.create_model(encoder, pretrained=False)
            nb = len(probe.blocks)
            del probe
            idx = tuple(round(nb * q) - 1 for q in (0.25, 0.5, 0.75, 1.0))          # ViT-S/B: (2, 5, 8, 11)
            self.encoder = timm.create_model(encoder, pretrained=pretrained, features_only=True, out_indices=idx,
                                             dynamic_img_size=True, dynamic_img_pad=True)   # any photo size (pads to 14x)
            enc_ch = self.encoder.feature_info.channels()
            self.patch = self.encoder.feature_info.reduction()[0] if hasattr(self.encoder, "feature_info") else 14
            if decoder in VIT_NATIVE:                 # mlp / dpt / probe read the ViT blocks directly
                self.adapter, chans = None, enc_ch
            else:                                     # unet / upernet / linear want a CNN-like pyramid
                self.adapter = ViTPyramid(enc_ch[0])
                chans = self.adapter.out
        else:
            if decoder in ("dpt", "probe"):
                raise ValueError(f"decoder {decoder!r} is for plain ViT encoders (DINOv2)")
            self.encoder = timm.create_model(encoder, pretrained=pretrained, features_only=True, out_indices=(0, 1, 2, 3))
            self.adapter = None
            chans = self.encoder.feature_info.channels()
        self.freeze = freeze
        if freeze:
            for p in self.encoder.parameters():
                p.requires_grad_(False)
            self.encoder.eval()
        if self.vit and decoder in VIT_NATIVE:
            self.decoder = VIT_NATIVE[decoder](chans)
        else:
            self.decoder = {"unet": UNetDecoder, "upernet": UPerNetDecoder, "linear": LinearDecoder, "mlp": MLPHead}[decoder](chans)
        self.sem_head = nn.Conv2d(self.decoder.out_ch, N_SEM, 1)
        self.state_head = nn.Conv2d(self.decoder.out_ch, n_state, 1)

    def trainable_modules(self):
        return [m for m in (self.adapter, self.decoder, self.sem_head, self.state_head) if m is not None]

    def reset_trainable(self, seed: int):
        """Re-initialise adapter / decoder / heads from `seed` alone (PyTorch default init), so their starting weights do
        not depend on how many random numbers building the encoder happened to use."""
        torch.manual_seed(seed)
        for top in self.trainable_modules():
            for m in top.modules():              # conv / norm layers; containers have no reset_parameters
                if hasattr(m, "reset_parameters"):
                    m.reset_parameters()

    def train(self, mode=True):
        super().train(mode)
        if self.freeze:
            self.encoder.eval()
        return self

    def _work_size(self, size):
        """input size the features describe: the padded size for a ViT (pad_crop), else the input size."""
        h, w = int(size[0]), int(size[1])
        if self.pad_crop:
            p = self.patch
            return (math.ceil(h / p) * p, math.ceil(w / p) * p)
        return (h, w)

    def features(self, x):
        if self.freeze:
            with torch.no_grad():
                f = self.encoder(x)
        else:
            f = self.encoder(x)
        return self.adapter(f, self._work_size(x.shape[-2:])) if self.adapter is not None else f

    def heads(self, f, size):
        h, w = int(size[0]), int(size[1])
        ws = self._work_size(size)
        y = self.decoder(f, ws)
        ls, lt = self.sem_head(y), self.state_head(y)
        if ls.shape[-2:] != ws:                       # decoders working below input resolution: resize the logits
            ls = F.interpolate(ls, size=ws, mode="bilinear", align_corners=False)
            lt = F.interpolate(lt, size=ws, mode="bilinear", align_corners=False)
        if ws != (h, w):                               # drop the ViT padding (bottom / right)
            ls, lt = ls[..., :h, :w], lt[..., :h, :w]
        return ls, lt

    def forward(self, x):
        return self.heads(self.features(x), x.shape[-2:])


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


def _ratio(pred, region, surfaces: Surfaces, k=0):
    """installed share and observed share of `region` from a predicted state map, for surface slot k."""
    n1 = int(((pred == surfaces.inst(k)) & region).sum())
    n0 = int(((pred == surfaces.notinst(k)) & region).sum())
    n2 = int(((pred == surfaces.occ) & region).sum())
    return (n1 / (n0 + n1) if n0 + n1 else None), ((n0 + n1) / (n0 + n1 + n2) if n0 + n1 + n2 else None)


def evaluate(model, items, device, amp, surfaces: Surfaces, overlay_dir=None, n_overlay=6, outside="full",
             predict=None, overlay_ids=None):
    """Three installed-ratio errors, from easiest to the real inference situation:
         label_region : over the pixels the human label marks observable (old metric, optimistic)
         roi_model    : over the whole ROI; the model itself decides occluded / not_target pixels
         image_model  : no ROI at all; all pixels the model calls installed / not_installed (new-site mode),
                        compared with the human ratio over all observable target pixels of the photo
       Everything is reported overall and per surface (wall / ceiling) when the model learns both.
       predict(model, x, items) -> (semantic probs, state probs) at input size replaces model(x) (test-time variants);
       overlay_ids: save overlays for exactly these photo ids (instead of the first n_overlay)."""
    model.eval()
    sem_c, st_c = Confusion(N_SEM), Confusion(surfaces.n_state)
    photo_rows = []                                           # one row per photo x target: lets any subset be re-scored later
    per = defaultdict(lambda: defaultdict(list))
    img_rows = defaultdict(lambda: defaultdict(list))
    loader = torch.utils.data.DataLoader(SegData(items, surfaces, outside=outside), batch_size=2, shuffle=False, num_workers=0)
    shown = 0
    with torch.no_grad():
        for x, sem, st, idx in loader:
            if predict is None:
                with torch.autocast(device_type=device.type, enabled=amp):
                    ls, lt = model(x.to(device))
                pst = torch.softmax(lt.float(), 1)
            else:
                ls, pst = predict(model, x.to(device), [items[int(i)] for i in idx])
            ps = ls.argmax(1).cpu().numpy()
            pt = pst.argmax(1).cpu().numpy()
            conf = pst.max(1).values.cpu().numpy()                  # per-pixel confidence of the state prediction
            for b in range(x.shape[0]):
                it = items[int(idx[b])]
                g_sem, g_t = sem[b].numpy(), st[b].numpy()
                sem_c.add(ps[b], g_sem)
                st_c.add(pt[b], g_t)
                _, _, g_raw, _, tg = load_item(it, surfaces)       # raw labels: 2k / 2k+1 per surface, 255 elsewhere
                obs = g_raw != 255
                for ti, tid in it["tindex"].items():
                    k = it["slot_of"].get(ti)
                    if k is None or (it.get("only_targets") is not None and ti not in it["only_targets"]):
                        continue
                    roi = tg == ti
                    m = obs & roi
                    if m.sum() < 500:
                        continue
                    d = per[(it["date"], tid, surfaces.names[k])]
                    d["gt"].append(float((g_raw[m] == surfaces.inst(k)).mean()))
                    d["gt_obs"].append(float(m.sum() / max(roi.sum(), 1)))
                    d["label_region"].append(_ratio(pt[b], m, surfaces, k)[0])
                    r, o = _ratio(pt[b], roi, surfaces, k)
                    d["roi_model"].append(r)
                    d["roi_model_obs"].append(o)
                    photo_rows.append({"id": it["id"], "date": it["date"], "target": tid, "surface": surfaces.names[k],
                                       "gt_ratio": round(d["gt"][-1], 4), "roi_model": None if r is None else round(r, 4),
                                       "view": _view_of(it), "time": it.get("time", ""),
                                       "roi_frac": round(float(roi.mean()), 6), "gt_observed": round(d["gt_obs"][-1], 4),
                                       "pred_observed": None if o is None else round(o, 4),
                                       "conf": round(float(conf[b][roi].mean()), 4)})
                if it.get("only_targets") is None:              # whole-photo ratio needs every target labelled
                    for k, name in enumerate(surfaces.names):
                        own = np.isin(tg, [i for i, kk in it["slot_of"].items() if kk == k])
                        all_obs = obs & own
                        if all_obs.sum() >= 500:
                            img_rows[(it["date"], name)]["gt"].append(float((g_raw[all_obs] == surfaces.inst(k)).mean()))
                            img_rows[(it["date"], name)]["image_model"].append(_ratio(pt[b], np.ones_like(obs), surfaces, k)[0])
                if overlay_dir is not None and (it["id"] in overlay_ids if overlay_ids is not None else shown < n_overlay):
                    save_overlay(it, pt[b], g_raw, overlay_dir / f"{it['id']}.jpg", surfaces)
                    shown += 1

    def mean(v):
        v = [a for a in v if a is not None]
        return round(float(np.mean(v)), 4) if v else None

    rows = [{"date": dt, "target": t, "surface": sf, "gt_ratio": mean(v["gt"]), "gt_observed": mean(v["gt_obs"]),
             "label_region": mean(v["label_region"]), "roi_model": mean(v["roi_model"]),
             "roi_model_observed": mean(v["roi_model_obs"])} for (dt, t, sf), v in sorted(per.items())]
    irows = [{"date": dt, "surface": sf, "gt_ratio": mean(v["gt"]), "image_model": mean(v["image_model"])}
             for (dt, sf), v in sorted(img_rows.items())]

    def mae(rs, key):
        e = [abs(r["gt_ratio"] - r[key]) for r in rs if r.get(key) is not None and r["gt_ratio"] is not None]
        return (round(float(np.mean(e)), 4), round(float(np.max(e)), 4)) if e else (None, None)

    sem_iou, st_iou = sem_c.iou(), st_c.iou()
    present = [v for v in sem_iou if v is not None]
    inst_ious = [v for v in st_iou[:surfaces.occ] if v is not None]
    out = {"state_iou": dict(zip(surfaces.class_names, st_iou)),
           "state_miou_install": float(np.mean(inst_ious)) if inst_ious else None,
           "semantic_iou": dict(zip(("wall", "floor", "ceiling", "stud", "hvac", "materials", "tools", "person"), sem_iou)),
           "semantic_miou_present": float(np.mean(present)) if present else None, "photos": len(items)}
    for key, rs in (("label_region", rows), ("roi_model", rows), ("image_model", irows)):
        out[f"ratio_mae_{key}"], out[f"ratio_maxerr_{key}"] = mae(rs, key)
    if len(surfaces.names) > 1:
        out["per_surface"] = {}
        for k, name in enumerate(surfaces.names):
            ii = [v for v in st_iou[2 * k:2 * k + 2] if v is not None]
            d = {"state_miou_install": float(np.mean(ii)) if ii else None}
            for key, rs in (("label_region", rows), ("roi_model", rows), ("image_model", irows)):
                d[f"ratio_mae_{key}"], d[f"ratio_maxerr_{key}"] = mae([r for r in rs if r["surface"] == name], key)
            out["per_surface"][name] = d
    out.update(ratio_rows=rows, image_rows=irows, photo_rows=photo_rows,
               state_confusion=st_c.m.tolist(), semantic_confusion=sem_c.m.tolist(),   # rows = ground truth, cols = prediction
               note="single-site numbers say nothing about a new site; roi_model / image_model are the inference-like ones")
    return out


def save_overlay(it, pred_state, gt_state, path, surfaces: Surfaces):
    img = np.asarray(Image.open(it["image"]).convert("RGB")).astype(np.float32)
    colours = [(surfaces.inst(k), (0, 200, 80)) for k in range(len(surfaces.names))] + \
              [(surfaces.notinst(k), (230, 40, 40)) for k in range(len(surfaces.names))]
    ov, gt = img.copy(), img.copy()
    for v, c in colours + [(surfaces.occ, (150, 150, 150))]:
        ov[pred_state == v] = ov[pred_state == v] * 0.45 + np.array(c) * 0.55
    for v, c in colours:
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

def _export_size(export: Path):
    """[W, H] the export was made with (export_report.json), so inference resizes photos the same way."""
    try:
        rep = json.loads((Path(export) / "export_report.json").read_text(encoding="utf-8"))
        return rep.get("size")
    except (OSError, ValueError):
        return None


def _view_of(it) -> str:
    """view_id of a photo from its export metadata (cached per export); '' when unknown."""
    cache = _view_of.cache.setdefault(it["export"], None)
    if cache is None:
        cache = {}
        try:
            for l in (Path(it["export"]) / "metadata.jsonl").read_text(encoding="utf-8").splitlines():
                if l.strip():
                    m = json.loads(l)
                    cache[m["sample_id"]] = m.get("view_id") or ""
        except OSError:
            pass
        _view_of.cache[it["export"]] = cache
    return cache.get(it["id"], "")


_view_of.cache = {}


def weighted_ce(logits, target, sample_w, class_w=None):
    """Cross-entropy averaged per photo, then weighted per photo (propagated pseudo-labels count less)."""
    loss = F.cross_entropy(logits, target, weight=class_w, ignore_index=255, reduction="none")
    valid = (target != 255).float()
    n = valid.sum((1, 2))
    per = (loss * valid).sum((1, 2)) / n.clamp(min=1)
    w = sample_w * (n > 0).float()
    return (per * w).sum() / w.sum().clamp(min=1e-6)


# ------------------------------------------------------------------ run records (reproducibility / later analysis)
def _sha256(path: Path) -> str | None:
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def _ids_digest(ids) -> str:
    import hashlib
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]


def _write_run_record(out: Path, args, argv, device, split, anchors, kl_items, exclude):
    """config.json (every argument, versions, GPU, code hashes) + split_manifest.json (the exact photo ids of every
    split), written BEFORE training so two runs can be proven to share data / code. Also starts a fresh
    epoch_metrics.jsonl."""
    import platform
    import timm
    here = Path(__file__).resolve().parent
    cfg = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "argv": list(argv) if argv is not None else sys.argv[1:],
           "args": {k: (str(v) if isinstance(v, Path) else [str(x) for x in v] if isinstance(v, list) else v)
                    for k, v in vars(args).items()},
           "versions": {"python": platform.python_version(), "torch": torch.__version__, "timm": timm.__version__,
                        "numpy": np.__version__, "cuda": torch.version.cuda},
           "device": str(device), "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
           "code_sha256": {f: _sha256(here / f) for f in ("train_quick.py", "data_prep.py")},
           "seed": args.seed, "cudnn_deterministic": torch.backends.cudnn.deterministic,
           "selection": "val ratio_mae_roi_model (lowest); hold-out is never used for selection"}
    (out / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    man = {}
    for s, v in list(split.items()) + [("anchors", anchors), ("kl", kl_items)]:
        ids = [it["id"] for it in v]
        uniq = sorted(set(ids))
        man[s] = {"n": len(ids), "n_unique": len(uniq), "digest": _ids_digest(uniq), "ids": uniq}
    man["excluded"] = sorted(exclude)
    (out / "split_manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "epoch_metrics.jsonl").write_text("", encoding="utf-8")


def _slim(r: dict, keep_photos=False) -> dict:
    """evaluate() result without the long per-date tables (photo_rows kept for the hold-out curve)."""
    drop = {"ratio_rows", "image_rows", "note"} | (set() if keep_photos else {"photo_rows"})
    return {k: v for k, v in r.items() if k not in drop}


def _surface_cols(prefix: str, r: dict) -> dict:
    """flat per-surface columns for log.csv, e.g. val_wall_install_miou / holdout_ceiling_mae_roi_model."""
    cols = {}
    for name, d in (r.get("per_surface") or {}).items():
        cols[f"{prefix}_{name}_install_miou"] = d.get("state_miou_install")
        cols[f"{prefix}_{name}_mae_roi_model"] = d.get("ratio_mae_roi_model")
    return cols


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", nargs="+", type=Path, help="export folder(s) (exports/<..>_train_full)")
    ap.add_argument("--weak-export", nargs="*", type=Path, default=[],
                    help="export folder(s) with WEAK labels (bulk-approved rule / propagation drafts): train only, loss weight "
                         "--weak-weight, never val / test")
    ap.add_argument("--weak-weight", type=float, default=0.3, help="loss weight of weak-label photos (human labels = 1)")
    ap.add_argument("--weak-every", type=int, default=1,
                    help="keep every N-th weak-label photo (time order): photos 10 min apart are near-duplicates, N=3 keeps "
                         "one per ~30 min and cuts the epoch time by 3")
    ap.add_argument("--activity", default="wall_board_installation",
                    help="wall_board_installation | ceiling_panel_installation | all (one model for wall + ceiling)")
    ap.add_argument("--holdout-export", nargs="*", type=Path, default=[],
                    help="export folder(s) of a city kept OUT of training (cross-site test); see --holdout-ids")
    ap.add_argument("--holdout-ids", nargs="*", default=[],
                    help="@file: image_id or image_id|target_id per line = the human-labelled photos of the hold-out export "
                         "(graded as 'holdout'); every other photo of it is graded against its weak labels ('holdout_weak')")
    ap.add_argument("--holdout-weak-limit", type=int, default=1000,
                    help="at most N evenly spaced photos for the holdout_weak grading (0 = all)")
    # --- new-site adaptation (few human anchors + KL to a base model) ---
    ap.add_argument("--holdout-anchors", type=int, default=0,
                    help="K of the hold-out city's human-labelled photos become TRAINING anchors (earliest photo of each "
                         "view first, then by date); the rest stay the hold-out test. 0 = none (pure cross-site test)")
    ap.add_argument("--anchor-repeat", type=int, default=0,
                    help="each anchor appears R times per epoch (0 = like any human photo, i.e. --human-repeat). "
                         "A few anchors among thousands of photos are otherwise too weak a signal")
    ap.add_argument("--holdout-kl", type=int, default=0,
                    help="N evenly spaced photos of the hold-out city used LABEL-FREE: their state prediction is kept close "
                         "to the base model's (KL). Needs --init")
    ap.add_argument("--init", type=Path, default=None,
                    help="model_quick.pt to start from (decoder + heads); also the frozen base for the KL term")
    ap.add_argument("--kl-weight", type=float, default=1.0, help="weight of the KL term (0 = off)")
    ap.add_argument("--kl-temp", type=float, default=2.0, help="softmax temperature of the KL term")
    ap.add_argument("--kl-until", default=None,
                    help="KL photos only up to this date (YYYY-MM-DD, or 'anchors' = the last anchor's date): a one-time "
                         "set-up at install sees no later photos. Default: the whole hold-out period")
    ap.add_argument("--aug", choices=("photo", "geo"), default="photo",
                    help="geo: + random scale 0.5-1.0, shift, horizontal flip (walls seen smaller / elsewhere)")
    ap.add_argument("--human-repeat", type=int, default=1,
                    help="repeat human-labelled training photos R times per epoch (they are a few % of a weak-label set)")
    ap.add_argument("--encoder", default="convnext_tiny.fb_in22k",
                    help="timm model name; DINOv3 ConvNeXt-Tiny once access is granted (see --list-encoders)")
    ap.add_argument("--decoder", choices=DECODERS, default="unet")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--sem-weight", type=float, default=0.5)
    ap.add_argument("--copy-paste", type=float, default=0.5, help="probability of occluder copy-paste per training photo")
    ap.add_argument("--state-outside", choices=("full", "roi"), default="full",
                    help="full: non-wall pixels outside the wall ROIs are trained as not_target (enables ROI proposal); "
                         "roi: old behaviour (ablation)")
    ap.add_argument("--propagated-weight", type=float, default=0.5,
                    help="loss weight of propagated pseudo-label photos (label_source=propagated); human labels = 1")
    ap.add_argument("--exclude-ids", nargs="*", default=[],
                    help="image / sample ids to leave out (e.g. human labels found wrong in review); @file reads one id per line")
    ap.add_argument("--no-class-weights", action="store_true", help="plain semantic loss (no rare-class weighting)")
    ap.add_argument("--no-freeze", action="store_true", help="also train the encoder (not recommended with few labels)")
    ap.add_argument("--no-pretrained", action="store_true", help="random encoder weights (tests only)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-epoch-ckpt", action="store_true",
                    help="do not keep ckpt/epoch_NN.pt (decoder + heads of every evaluated epoch; ~14 MB each)")
    ap.add_argument("--no-track-holdout", action="store_true",
                    help="do not score the hold-out human photos after every evaluated epoch (epoch_metrics.jsonl). "
                         "That curve is for analysis only - the model is ALWAYS selected on val")
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
    exclude = set()
    for x in args.exclude_ids:
        exclude |= set(Path(x[1:]).read_text(encoding="utf-8").split()) if x.startswith("@") else {x}
    surfaces = Surfaces(args.activity)
    items = [it for e in args.export for it in read_export(e, surfaces, exclude, args.propagated_weight)]
    for e in args.weak_export:
        weak = sorted(read_export(e, surfaces, exclude, args.propagated_weight, args.weak_weight), key=lambda it: it["id"])
        items += weak[::max(1, args.weak_every)]
    holdout_ids = {}
    for x in args.holdout_ids:
        holdout_ids.update(_parse_ids(Path(x[1:]).read_text(encoding="utf-8").splitlines() if x.startswith("@") else [x]))
    if args.holdout_export and not holdout_ids:
        ap.error("--holdout-export needs --holdout-ids (the human-labelled photos of that city)")
    for e in args.holdout_export:
        items += read_export(e, surfaces, exclude, args.propagated_weight, holdout_ids=holdout_ids)
    # new-site anchors: K hold-out human photos move into training (one per view first, earliest dates)
    anchors = []
    if args.holdout_anchors:
        hold = sorted([it for it in items if it["split"] == "holdout"], key=lambda it: it["id"])
        seen_views, rest = set(), []
        for it in hold:
            v = _view_of(it)
            (anchors if v not in seen_views else rest).append(it)
            seen_views.add(v)
        anchors = (anchors + rest)[: args.holdout_anchors]
        for it in anchors:
            it["split"], it["weight"] = "train", 1.0
    # train on human + propagated + weak; select / evaluate on human labels only (pseudo-labels never grade the model)
    split = {"train": [it for it in items if it["split"] == "train"]}
    for s in ("val", "test", "holdout"):
        split[s] = [it for it in items if it["split"] == s and it["label_source"] == "human"]
    hw = sorted([it for it in items if it["split"] == "holdout_weak"], key=lambda it: it["id"])
    kl_items = []
    if args.holdout_kl:
        if args.init is None:
            ap.error("--holdout-kl needs --init (the base model the predictions are kept close to)")
        pool = hw
        if args.kl_until:                           # one-time set-up at install: no photo later than the set-up day
            if args.kl_until == "anchors" and not anchors:
                ap.error("--kl-until anchors needs --holdout-anchors")
            lim = max(a["date"] for a in anchors) if args.kl_until == "anchors" else args.kl_until
            pool = [it for it in hw if it["date"] <= lim]
        n = min(args.holdout_kl, len(pool))
        kl_items = [dict(pool[int(k * (len(pool) - 1) / max(n - 1, 1))], kl=True, split="kl") for k in range(n)]
    if args.human_repeat > 1:
        split["train"] += [it for it in split["train"] if it["label_source"] == "human"] * (args.human_repeat - 1)
    if anchors and args.anchor_repeat:          # anchors: exactly R copies per epoch, instead of following --human-repeat
        aid = {id(a) for a in anchors}
        split["train"] = [it for it in split["train"] if id(it) not in aid] + anchors * args.anchor_repeat
    if args.holdout_weak_limit and len(hw) > args.holdout_weak_limit:
        hw = [hw[int(k * (len(hw) - 1) / (args.holdout_weak_limit - 1))] for k in range(args.holdout_weak_limit)]
    split["holdout_weak"] = hw
    n_prop = sum(1 for it in split["train"] if it["label_source"] == "propagated")
    n_weak = sum(1 for it in split["train"] if it["label_source"] == "weak")
    print(f"activity {args.activity} -> surfaces {surfaces.names}, state classes {surfaces.class_names}", flush=True)
    if anchors or kl_items:
        print(f"new-site adaptation: anchors {len(anchors)} {[(a['id'][-24:-9] + ' ' + _view_of(a)).strip() for a in anchors][:12]} | "
              f"KL photos {len(kl_items)} (weight {args.kl_weight}, T {args.kl_temp}) | init {args.init}", flush=True)
    print(f"excluded ids {len(exclude)} | propagated photos in train {n_prop} (weight {args.propagated_weight}) | "
          f"weak-label photos in train {n_weak} (weight {args.weak_weight})"
          + (f" | hold-out human photos {len(split['holdout'])} (ids listed {len(holdout_ids)}), weak-graded {len(hw)}"
             if args.holdout_export else ""), flush=True)
    if args.limit:
        split = {s: v[: args.limit] for s, v in split.items()}
        kl_items = kl_items[: args.limit]
    print({s: len(v) for s, v in split.items()} | ({"kl": len(kl_items)} if kl_items else {}), flush=True)
    if not split["train"]:
        print("no training photos")
        return 2
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp = device.type == "cuda"
    print(f"device {device}" + (f" ({torch.cuda.get_device_name(0)})" if amp else " - CPU is very slow"), flush=True)
    model = TwoHeadSeg(args.encoder, args.decoder, pretrained=not args.no_pretrained, freeze=not args.no_freeze,
                       n_state=surfaces.n_state).to(device)
    base = None
    if args.init:
        ck0 = torch.load(args.init, map_location="cpu", weights_only=False)
        if ck0.get("encoder") != args.encoder or ck0.get("decoder") != args.decoder or ck0.get("n_state", 4) != surfaces.n_state:
            raise SystemExit(f"--init model is {ck0.get('encoder')}+{ck0.get('decoder')} n_state {ck0.get('n_state')}; "
                             f"this run is {args.encoder}+{args.decoder} n_state {surfaces.n_state}")
        missing, unexpected = model.load_state_dict(ck0["state_dict"], strict=False)
        bad = [k for k in missing if not k.startswith("encoder.")] + list(unexpected)
        if bad:
            raise SystemExit(f"--init checkpoint does not match: {bad[:5]}")
        print(f"init from {args.init} (epoch {ck0.get('epoch')})", flush=True)
        if kl_items and args.kl_weight > 0:
            import copy
            base = copy.deepcopy(model).eval()                   # frozen reference for the KL term (shares nothing)
            for p in base.parameters():
                p.requires_grad_(False)
    trainable = [p for p in model.parameters() if p.requires_grad]
    print(f"encoder {args.encoder}  decoder {args.decoder}  trainable params {sum(p.numel() for p in trainable)/1e6:.2f}M  "
          f"frozen {sum(p.numel() for p in model.encoder.parameters())/1e6:.2f}M", flush=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = args.out or Path(args.export[0]).parent.parent / "train_quick" / f"{stamp}_{args.encoder.replace('/', '_')}_{args.decoder}"
    out.mkdir(parents=True, exist_ok=True)
    _write_run_record(out, args, argv, device, split, anchors, kl_items, exclude)
    opt = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=1e-4)
    steps = args.epochs * math.ceil(len(split["train"]) / args.batch)
    if steps >= 20:
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps, pct_start=0.1)
    else:                                                   # smoke runs: OneCycle cannot warm up in < 2 steps
        sched = torch.optim.lr_scheduler.ConstantLR(opt, factor=1.0, total_iters=1)
    scaler = torch.amp.GradScaler(device.type, enabled=amp)
    w_sem, freq = semantic_class_weights(split["train"])
    cw_sem = w_sem.to(device) if not args.no_class_weights else None
    sample_w = torch.tensor([it["weight"] for it in split["train"]], dtype=torch.float32)
    print("semantic class freq", np.round(freq, 4).tolist(), "weights", np.round(w_sem.numpy(), 2).tolist(), flush=True)
    loader = torch.utils.data.DataLoader(SegData(split["train"], surfaces, train=True, copy_paste=args.copy_paste, seed=args.seed,
                                                 outside=args.state_outside, geo=args.aug == "geo"),
                                         batch_size=args.batch, shuffle=True, num_workers=0,
                                         drop_last=len(split["train"]) > args.batch)   # a last batch of 1 photo breaks BatchNorm
    kl_loader = None
    if kl_items and base is not None:
        kl_loader = torch.utils.data.DataLoader(SegData(kl_items, surfaces, train=True, copy_paste=0.0, seed=args.seed + 1,
                                                        outside=args.state_outside, geo=args.aug == "geo"),
                                                batch_size=args.batch, shuffle=True, num_workers=0, drop_last=len(kl_items) > args.batch)
    kl_iter = None
    best, log = -1e9, []
    track_holdout = bool(split["holdout"]) and not args.no_track_holdout
    for ep in range(1, args.epochs + 1):
        model.train()
        if amp:
            torch.cuda.reset_peak_memory_stats()
        lr0 = opt.param_groups[0]["lr"]
        t0, tot, n, kl_sum = time.time(), 0.0, 0, 0.0
        for x, sem, st, ix in loader:
            x, sem, st = x.to(device), sem.to(device), st.to(device)
            sw = sample_w[ix].to(device)
            with torch.autocast(device_type=device.type, enabled=amp):
                ls, lt = model(x)
                loss_s = weighted_ce(ls.float(), sem, sw, cw_sem) if (sem != 255).any() else ls.sum() * 0
                loss_t = weighted_ce(lt.float(), st, sw) if (st != 255).any() else lt.sum() * 0
                loss = args.sem_weight * loss_s + loss_t
                if kl_loader is not None:
                    # one label-free batch of the new site per supervised batch: keep its state prediction close to the
                    # base model's (what the base already got right stays; the anchors move only what they disagree with)
                    try:
                        xk = next(kl_iter)[0]
                    except (StopIteration, TypeError):
                        kl_iter = iter(kl_loader)
                        xk = next(kl_iter)[0]
                    xk = xk.to(device)
                    with torch.no_grad():
                        fk = model.features(xk)
                        _, lt0 = base.heads(fk, xk.shape[-2:])
                    _, ltk = model.heads(fk, xk.shape[-2:])
                    T = args.kl_temp
                    loss_kl = F.kl_div(F.log_softmax(ltk.float() / T, 1), F.softmax(lt0.float() / T, 1),
                                       reduction="batchmean") * (T * T) / (ltk.shape[-1] * ltk.shape[-2])
                    loss = loss + args.kl_weight * loss_kl
                    kl_sum += float(loss_kl.detach()) * xk.shape[0]
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            tot += float(loss.detach()) * x.shape[0]
            n += x.shape[0]
        row = {"epoch": ep, "loss": round(tot / max(n, 1), 4), "sec": round(time.time() - t0, 1), "lr_start": lr0,
               "gpu_peak_gb": round(torch.cuda.max_memory_allocated() / 2 ** 30, 2) if amp else None}
        if kl_loader is not None:
            row["kl"] = round(kl_sum / max(n, 1), 5)
        if split["val"] and (ep % max(1, args.epochs // 10) == 0 or ep == args.epochs):
            v = evaluate(model, split["val"], device, amp, surfaces, outside=args.state_outside)
            row.update(val_install_miou=v["state_miou_install"], val_sem_miou=v["semantic_miou_present"],
                       val_mae_roi_model=v["ratio_mae_roi_model"], val_mae_image_model=v["ratio_mae_image_model"])
            row.update(_surface_cols("val", v))
            ck = {"encoder": args.encoder, "decoder": args.decoder, "n_sem": N_SEM, "n_state": surfaces.n_state,
                  "state_outside": args.state_outside, "activity": args.activity,
                  "surfaces": surfaces.names, "state_classes": surfaces.class_names,
                  "export_size": _export_size(args.export[0]),
                  "state_dict": {k: t for k, t in model.state_dict().items() if not k.startswith("encoder.")},
                  "input": {"mean": MEAN.flatten().tolist(), "std": STD.flatten().tolist(), "rgb_only": True},
                  "exports": [str(e) for e in args.export], "epoch": ep, "val": v, "vit_pad_crop": model.pad_crop,
                  "license_note": "encoder weights from timm; DINOv3 weights fall under the DINOv3 License"}
            score = -(v["ratio_mae_roi_model"] if v["ratio_mae_roi_model"] is not None else 1.0)   # select on the inference-like error
            row["selected"] = score > best
            if score > best:
                best = score
                torch.save(ck, out / "model_quick.pt")
            if not args.no_epoch_ckpt:
                (out / "ckpt").mkdir(exist_ok=True)
                torch.save(ck, out / "ckpt" / f"epoch_{ep:02d}.pt")
            ev = {"epoch": ep, "val": _slim(v)}
            if track_holdout:                     # analysis only: never feeds the selection above
                h = evaluate(model, split["holdout"], device, amp, surfaces, outside=args.state_outside)
                row.update(holdout_install_miou=h["state_miou_install"], holdout_mae_roi_model=h["ratio_mae_roi_model"])
                row.update(_surface_cols("holdout", h))
                ev["holdout"] = _slim(h, keep_photos=True)
            with open(out / "epoch_metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        log.append(row)
        print(json.dumps(row), flush=True)
        with open(out / "log.csv", "w", newline="", encoding="utf-8") as f:   # rewritten every epoch: survives a crash
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in log for k in r)))
            w.writeheader()
            w.writerows(log)
    result = {"encoder": args.encoder, "decoder": args.decoder, "state_outside": args.state_outside, "epochs": args.epochs,
              "propagated_in_train": n_prop, "propagated_weight": args.propagated_weight,
              "weak_in_train": n_weak, "weak_weight": args.weak_weight, "weak_every": args.weak_every,
              "weak_exports": [str(e) for e in args.weak_export],
              "holdout_exports": [str(e) for e in args.holdout_export], "activity": args.activity,
              "aug": args.aug, "human_repeat": args.human_repeat, "init": str(args.init) if args.init else None,
              "anchors": [a["id"] for a in anchors], "anchor_repeat": args.anchor_repeat, "kl_photos": len(kl_items), "kl_weight": args.kl_weight if kl_items else 0,
              "kl_temp": args.kl_temp, "kl_until": args.kl_until,
              "surfaces": surfaces.names, "state_classes": surfaces.class_names,
              "excluded_ids": sorted(exclude), "counts": {s: len(v) for s, v in split.items()},
              "device": str(device)}
    if (out / "model_quick.pt").exists():
        ck = torch.load(out / "model_quick.pt", map_location=device, weights_only=False)
        model.load_state_dict(ck["state_dict"], strict=False)
        result["best_epoch"] = ck["epoch"]
    elif args.init and args.epochs == 0:       # --epochs 0 --init M = re-score M (e.g. on a fixed common hold-out subset)
        result["eval_only_of"], result["best_epoch"] = str(args.init), ck0.get("epoch")
    for s in ("val", "test", "holdout", "holdout_weak"):
        if split[s]:
            result[s] = evaluate(model, split[s], device, amp, surfaces, overlay_dir=out / "overlays" / s, outside=args.state_outside)
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    f = lambda v: "-" if v is None else f"{v:.3f}"
    for s, title in (("test", "test (same sites as training)"), ("holdout", "HOLD-OUT city, human labels"),
                     ("holdout_weak", "hold-out city, weak labels (reference only)")):
        t = result.get(s)
        if not t:
            continue
        si, se = t.get("state_iou") or {}, t.get("semantic_iou") or {}
        print(f"\nRESULT {args.encoder}+{args.decoder} — {title}, {t.get('photos')} photos")
        print("  state IoU   " + "  ".join(f"{k} {f(v)}" for k, v in si.items()))
        print(f"  semantic    mIoU {f(t.get('semantic_miou_present'))}  person {f(se.get('person'))}  tools {f(se.get('tools'))}  "
              f"materials {f(se.get('materials'))}")
        print(f"  ratio MAE   label_region {f(t.get('ratio_mae_label_region'))}  roi_model {f(t.get('ratio_mae_roi_model'))}  "
              f"image_model {f(t.get('ratio_mae_image_model'))}   (max roi_model {f(t.get('ratio_maxerr_roi_model'))})")
        for name, d in (t.get("per_surface") or {}).items():
            print(f"    [{name}]  install mIoU {f(d.get('state_miou_install'))}  roi_model MAE {f(d.get('ratio_mae_roi_model'))} "
                  f"(max {f(d.get('ratio_maxerr_roi_model'))})  image_model MAE {f(d.get('ratio_mae_image_model'))}")
    print(f"-> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
