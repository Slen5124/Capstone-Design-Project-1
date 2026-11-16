"""Generated-pixel training fixtures. Never use these outputs as field-performance evidence."""
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from layout import SURFACES


def create_export(work, size=(1024, 576)):
    work = Path(work)
    root = work / "synthetic_export"
    root.mkdir(parents=True, exist_ok=False)
    targets = [{"index": i + 1, "target_id": f"T{i+1}", "surface_type": surface,
                "activities": {values[2]: {"applicability": "applicable", "review": "approved"}}}
               for i, (surface, values) in enumerate(SURFACES.items())]
    (root / "targets.json").write_text(json.dumps({"targets": targets}), encoding="utf-8")
    w, h = size
    roi = np.zeros((h, w), np.uint16)
    semantic = np.full((h, w), 4, np.uint8)
    for i, surface in enumerate(SURFACES):
        y0, y1 = i * h // 3 + 2, (i + 1) * h // 3 - 2
        roi[y0:y1, 2:w-2] = i + 1
        semantic[roi == i + 1] = {"wall": 0, "ceiling": 2, "floor": 1}[surface]
    visibility = (roi > 0).astype(np.uint8)
    meta = []
    for i, split in enumerate(("train", "train", "val", "test")):
        sid = f"sample_{i}"
        rng = np.random.default_rng(i)
        files = {}
        for key, array in (("image", rng.integers(60, 180, (h, w, 3), dtype=np.uint8)),
                           ("semantic", semantic), ("target", roi), ("visibility", visibility)):
            folder = root / key
            folder.mkdir(exist_ok=True)
            Image.fromarray(array).save(folder / (sid + ".png"))
            files[key] = f"{key}/{sid}.png"
        for k, (surface, (_, _, activity)) in enumerate(SURFACES.items(), 1):
            folder = root / "state_masks" / activity
            folder.mkdir(parents=True, exist_ok=True)
            state = np.full((h, w), 255, np.uint8)
            state[roi == k] = 0
            state[(roi == k) & (np.arange(w)[None, :] >= w // 2)] = 1
            Image.fromarray(state).save(folder / (sid + ".png"))
        meta.append({"sample_id": sid, "split": split, "label_source": "human", "view_id": "V01",
                     "capture": {"time_local": f"2026-01-{i+1:02d}T12:00:00"}, "files": files})
    (root / "metadata.jsonl").write_text("\n".join(json.dumps(m) for m in meta), encoding="utf-8")
    manifest = work / "training.local.json"
    manifest.write_text(json.dumps({"schema_version": "final-training/0.2",
                                   "exports": [{"site_id": "SYNTHETIC_ONLY", "path": "synthetic_export"}]}), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(create_export(Path(sys.argv[1])))
