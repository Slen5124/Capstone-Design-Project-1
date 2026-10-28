"""Synthetic 4-site data for the LOSO tests: same folder layout and metadata fields as real exports, tiny images."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
from PIL import Image

H, W = 192, 320
TARGETS = [  # index, id, surface, box (y0, y1, x0, x1)
    (1, "C01", "ceiling", (0, 40, 0, W)),
    (3, "W01", "wall", (50, 150, 0, 150)),
    (4, "W02", "wall", (50, 150, 170, 300)),
    (5, "W03", "wall", (160, 185, 250, 300)),      # small (~2% of the photo) -> ROI zoom path
]


def make_export(root: Path, site: str, start: str, n_dates: int, per_date: int, views=("V01", "V02"),
                label_source=None, ceiling=True, seed=0):
    rng = np.random.default_rng(seed)
    root = Path(root)
    for d in ("images", "semantic_masks", "target_masks", "visibility_masks", "splits",
              "state_masks/wall_board_installation", "state_masks/ceiling_panel_installation"):
        (root / d).mkdir(parents=True, exist_ok=True)
    meta, ids = [], []
    d0 = dt.date.fromisoformat(start)
    for di in range(n_dates):
        day = d0 + dt.timedelta(days=di)
        prog = (di + 0.5) / n_dates
        for k in range(per_date):
            t = f"{day:%Y%m%d}T{8 + k:02d}0000"
            sid = f"{site}-{t}-{(seed * 1000 + di * 10 + k):08x}"
            ids.append(sid)
            img = (rng.random((H, W, 3)) * 120).astype(np.uint8)
            sem = np.full((H, W), 1, np.uint8)
            tg = np.zeros((H, W), np.uint8)
            stw = np.full((H, W), 255, np.uint8)
            stc = np.full((H, W), 255, np.uint8)
            vis = np.zeros((H, W), np.uint8)
            for idx, tid, surf, (y0, y1, x0, x1) in TARGETS:
                if surf == "ceiling" and not ceiling:
                    continue
                tg[y0:y1, x0:x1] = idx
                sem[y0:y1, x0:x1] = 2 if surf == "ceiling" else 0
                vis[y0:y1, x0:x1] = 1
                st = stc if surf == "ceiling" else stw
                st[y0:y1, x0:x1] = 0
                cut = x0 + int((x1 - x0) * min(1.0, prog * (1.3 if tid == "W02" else 1.0)))
                st[y0:y1, x0:cut] = 1
                img[y0:y1, x0:cut] = (220, 220, 205)
            py, px = 60 + 10 * k, 60
            sem[py:py + 25, px:px + 12] = 7
            vis[py:py + 25, px:px + 12][tg[py:py + 25, px:px + 12] > 0] = 2
            stw[py:py + 25, px:px + 12] = 255
            for arr, sub, ext in ((img, "images", "jpg"), (sem, "semantic_masks", "png"), (tg, "target_masks", "png"),
                                  (vis, "visibility_masks", "png"), (stw, "state_masks/wall_board_installation", "png"),
                                  (stc, "state_masks/ceiling_panel_installation", "png")):
                Image.fromarray(arr).save(root / sub / f"{sid}.{ext}")
            m = {"sample_id": sid, "image_id": sid, "kind": "full", "split": "train",
                 "files": {"image": f"images/{sid}.jpg", "semantic": f"semantic_masks/{sid}.png",
                           "target": f"target_masks/{sid}.png", "visibility": f"visibility_masks/{sid}.png"},
                 "capture": {"time_local": f"{day.isoformat()}T{8 + k:02d}:00:00"}, "view_id": views[(di + k) % len(views)]}
            if label_source:
                m["label_source"] = label_source
            meta.append(m)
    (root / "metadata.jsonl").write_text("\n".join(json.dumps(m) for m in meta) + "\n", encoding="utf-8")
    (root / "splits" / "train.txt").write_text("\n".join(ids), encoding="utf-8")
    (root / "splits" / "val.txt").write_text("", encoding="utf-8")
    (root / "splits" / "test.txt").write_text("", encoding="utf-8")
    tj = [{"index": i, "target_id": t, "surface_type": s} for i, t, s, _ in TARGETS if ceiling or s != "ceiling"]
    (root / "targets.json").write_text(json.dumps({"targets": tj}), encoding="utf-8")
    return ids


def make_world(root: Path):
    """4 sites + private prepare config + local.json + a fast test protocol. -> dict of paths."""
    root = Path(root)
    ex = root / "exports"
    a_h = make_export(ex / "A_human", "SITEA", "2025-01-06", 12, 2, seed=1, ceiling=False)
    make_export(ex / "A_prop", "SITEAP", "2025-01-06", 12, 1, seed=2, label_source="propagated", ceiling=False)
    make_export(ex / "C_human", "SITEC", "2025-03-03", 10, 2, seed=3)
    b = make_export(ex / "B_full", "SITEB", "2025-05-05", 12, 2, seed=4)
    d = make_export(ex / "D_full", "SITED", "2025-07-07", 9, 2, seed=5, ceiling=False)
    (root / "B_ids.txt").write_text("\n".join(f"{i}|W01\n{i}|W02\n{i}|C01" for i in b[::3]) + "\n", encoding="utf-8")
    (root / "D_ids.txt").write_text("\n".join(f"{i}|W01" for i in d[::3]) + "\n", encoding="utf-8")
    exports = {"SITE_A": {"human": str(ex / "A_human"), "propagated": str(ex / "A_prop")},
               "SITE_B": {"full": str(ex / "B_full")}, "SITE_C": {"human": str(ex / "C_human")},
               "SITE_D": {"full": str(ex / "D_full")}}
    prep = {"exports": exports, "human_ids": {"SITE_B": str(root / "B_ids.txt"), "SITE_D": str(root / "D_ids.txt")},
            "exclude_ids": [a_h[5]],
            "exceptions": [{"site": "SITE_B", "target": "W01", "date": "2025-05-05", "reason": "test exception"}],
            "names": {"SITE_A": "synthetic A", "SITE_B": "synthetic B", "SITE_C": "synthetic C", "SITE_D": "synthetic D"}}
    (root / "prepare_config.json").write_text(json.dumps(prep), encoding="utf-8")
    from vision_preprocessing.train.loso.common import PROTOCOL_FILE
    P = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
    P["encoder"] = "vit_small_patch14_reg4_dinov2.lvd142m"
    P["seeds"] = [0]
    P["train"].update(epochs=2, batch=2, num_workers=0, log_every_steps=5)
    P["posteval"]["tta_scales"] = [1.0, 1.25]
    P["metrics"]["bootstrap_B"] = 200
    (root / "protocol_test.json").write_text(json.dumps(P), encoding="utf-8")
    local = {"bundle": str(root / "bundle"), "results": str(root / "results"), "exports": exports}
    (root / "local.json").write_text(json.dumps(local), encoding="utf-8")
    return {"root": root, "prepare": root / "prepare_config.json", "protocol": root / "protocol_test.json",
            "local": root / "local.json", "bundle": root / "bundle", "results": root / "results",
            "expected": root / "expected_digests.json"}
