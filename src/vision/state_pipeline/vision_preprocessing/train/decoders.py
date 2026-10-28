"""Decoders for a frozen plain ViT (DINOv2): they take the 4 selected block outputs AS THEY ARE (same resolution,
stride 14, `feats` = list of [B, C, h, w]) - no ViT pyramid adapter in front.

    MLPHead     SegFormer-style all-MLP head (Xie et al., NeurIPS 2021), adapted to a plain ViT: per block a 1x1
                projection, all resized to 1/4 of the input, concatenated, one 1x1 fusion. No spatial mixing - the
                light baseline that shows how far the frozen features alone go.
    DPTHead     DPT (Ranftl et al., ICCV 2021): 'reassemble' each block to its own scale (4x, 2x, 1x, 1/2x of the token
                grid) and fuse them coarse-to-fine with residual conv units. Built for plain ViTs. GroupNorm instead of
                BatchNorm (small batches); channels chosen so the decoder is about the size of the UPerNet one.
    LinearProbe ('probe') 'multi-layer linear probe' (as in the DINOv2 paper's linear evaluation): BatchNorm over the concatenated
                raw block features, the 1x1 heads of TwoHeadSeg are the only other weights. Lower bound.

Every decoder returns features with `out_ch` channels at ITS OWN output resolution (MLP 1/4, DPT 4x the token grid,
linear the token grid); TwoHeadSeg puts the two 1x1 heads on top and resizes the 14 logit maps to the input size
(cheap, and identical to resizing the features first because the heads are 1x1 / linear). All weights are trained from scratch (no pretrained decoder exists for this encoder / task)."""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def _up(x, size):
    return F.interpolate(x, size=size, mode="bilinear", align_corners=False)


class MLPHead(nn.Module):
    def __init__(self, chans, dim=256):
        super().__init__()
        self.proj = nn.ModuleList([nn.Conv2d(c, dim, 1) for c in chans])          # per-pixel linear layer per block
        self.fuse = nn.Sequential(nn.Conv2d(dim * len(chans), dim, 1, bias=False), nn.BatchNorm2d(dim), nn.ReLU(inplace=True))
        self.out_ch = dim

    def forward(self, feats, size):
        h, w = size
        s = (max(1, h // 4), max(1, w // 4))
        x = torch.cat([_up(p(f), s) for p, f in zip(self.proj, feats)], 1)
        return self.fuse(x)


def _gn(c):
    return nn.GroupNorm(min(32, c), c)


class _ResConvUnit(nn.Module):
    """DPT residual conv unit: x + conv(act(conv(act(x))))."""

    def __init__(self, c):
        super().__init__()
        self.c1, self.n1 = nn.Conv2d(c, c, 3, padding=1, bias=False), _gn(c)
        self.c2, self.n2 = nn.Conv2d(c, c, 3, padding=1, bias=False), _gn(c)

    def forward(self, x):
        y = self.n1(self.c1(F.relu(x)))
        y = self.n2(self.c2(F.relu(y)))
        return x + y


class _Fusion(nn.Module):
    """DPT feature fusion block: (x + RCU(skip)) -> RCU -> resize to the next (finer) level -> 1x1."""

    def __init__(self, c):
        super().__init__()
        self.rcu_skip, self.rcu = _ResConvUnit(c), _ResConvUnit(c)
        self.out = nn.Conv2d(c, c, 1)

    def forward(self, x, skip=None, size=None):
        if skip is not None:
            x = _up(x, skip.shape[-2:]) if x.shape[-2:] != skip.shape[-2:] else x
            x = x + self.rcu_skip(skip)
        x = self.rcu(x)
        if size is not None:
            x = _up(x, size)
        return self.out(x)


class DPTHead(nn.Module):
    def __init__(self, chans, dims=(64, 128, 256, 256), dim=128):
        super().__init__()
        assert len(chans) == 4, "DPT needs 4 blocks"
        self.read = nn.ModuleList([nn.Conv2d(c, d, 1) for c, d in zip(chans, dims)])
        self.resample = nn.ModuleList([
            nn.ConvTranspose2d(dims[0], dims[0], 4, stride=4),           # token grid x4
            nn.ConvTranspose2d(dims[1], dims[1], 2, stride=2),           # x2
            nn.Identity(),                                               # x1
            nn.Conv2d(dims[3], dims[3], 3, stride=2, padding=1),         # x1/2
        ])
        self.layer_rn = nn.ModuleList([nn.Conv2d(d, dim, 3, padding=1, bias=False) for d in dims])
        self.fusion = nn.ModuleList([_Fusion(dim) for _ in dims])
        self.out_ch = dim

    def forward(self, feats, size):
        r = [rn(rs(rd(f))) for f, rd, rs, rn in zip(feats, self.read, self.resample, self.layer_rn)]
        x = self.fusion[3](r[3], None, r[2].shape[-2:])
        x = self.fusion[2](x, r[2], r[1].shape[-2:])
        x = self.fusion[1](x, r[1], r[0].shape[-2:])
        return self.fusion[0](x, r[0], None)


class LinearProbe(nn.Module):
    def __init__(self, chans):
        super().__init__()
        self.bn = nn.BatchNorm2d(sum(chans))
        self.out_ch = sum(chans)

    def forward(self, feats, size):
        s = feats[0].shape[-2:]
        return self.bn(torch.cat([f if f.shape[-2:] == s else _up(f, s) for f in feats], 1))


VIT_NATIVE = {"mlp": MLPHead, "dpt": DPTHead, "probe": LinearProbe}
