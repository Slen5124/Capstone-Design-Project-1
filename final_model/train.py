"""Versioned wall/ceiling/floor training. Original LOSO code and labels stay unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil

import numpy as np

from core import measure_targets, validate_config
from layout import STATE_CLASSES, SURFACES
from model import sha256_file
from run import read_json, write_json
from training_data import read_training_manifest, load_sample, audit


class Samples:
    def __init__(self, items, config, augment=False):
        self.items, self.config, self.augment = items, config, augment

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        import torch
        from vision_preprocessing.train.train_quick import MEAN, STD
        rgb, semantic, state, _, _, _ = load_sample(self.items[index], self.config["export_size"])
        if self.augment:
            if random.random() < .5:
                rgb, semantic, state = rgb[:, ::-1], semantic[:, ::-1], state[:, ::-1]
            rgb = np.clip(rgb.astype(np.float32) * random.uniform(.85, 1.15), 0, 255)
        x = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).float() / 255
        return ((x - MEAN) / STD, torch.from_numpy(semantic.copy()).long(),
                torch.from_numpy(state.copy()).long(), self.items[index]["weight"])


def evaluate(model, items, config, device):
    import torch
    confusion = np.zeros((8, 8), np.int64)
    rows = []
    dataset = Samples(items, config)
    model.eval()
    with torch.inference_mode():
        for index, item in enumerate(items):
            x, _, gt, _ = dataset[index]
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
                _, logits = model(x[None].to(device))
            if not torch.isfinite(logits).all():
                raise FloatingPointError("Nonfinite validation predictions")
            probs = logits.float().softmax(1)[0]
            confidence, prediction = probs.max(0)
            pred = prediction.cpu().numpy().astype(np.uint8)
            confidence = confidence.cpu().numpy()
            truth = gt.numpy()
            valid = truth != 255
            confusion += np.bincount(8 * truth[valid] + pred[valid], minlength=64).reshape(8, 8)
            _, _, _, raw, roi, targets = load_sample(item, config["export_size"])
            for target in targets:
                surface = target["surface_type"]
                n0, n1, activity = SURFACES[surface]
                labelled = (roi == target["index"]) & np.isin(raw, [n0, n1])
                if labelled.sum() < config["min_roi_px"]:
                    continue
                target_arg = {"index": target["index"], "target_key": str(target["target_id"]),
                              "surface_type": surface, "activity": activity, "roi_confirmed": True}
                # Slice to one declared ROI so the strict inference validator sees no unrelated IDs.
                one_roi = np.where(roi == target["index"], roi, 0)
                measured = measure_targets(pred, confidence, one_roi, [target_arg], config, True)[0]
                gt_ratio = float((raw[labelled] == n1).mean())
                ratio = measured["installed_ratio"]
                rows.append({"sample_key": item["key"], "surface": surface, "target_id": target["target_id"],
                             "gt_ratio": gt_ratio, "pred_ratio": ratio, "status": measured["status"],
                             "error": abs(gt_ratio - ratio) if ratio is not None else None})
    per_surface = {}
    for surface in SURFACES:
        selected = [r for r in rows if r["surface"] == surface]
        errors = [r["error"] for r in selected if r["error"] is not None]
        per_surface[surface] = {
            "labelled_rois": len(selected), "observed_rois": len(errors),
            "coverage": len(errors) / len(selected) if selected else None,
            "mae_pp_observed": float(np.mean(errors) * 100) if errors else None,
            "selection_error": float(np.mean([r["error"] if r["error"] is not None else 1.0 for r in selected])) if selected else None}
    scores = [d["selection_error"] for d in per_surface.values()]
    denominator = confusion.sum(0) + confusion.sum(1) - np.diag(confusion)
    return {"per_surface": per_surface,
            "macro_selection_error": float(np.mean(scores)) if all(s is not None for s in scores) else None,
            "state_iou": {name: float(confusion[i, i] / denominator[i]) if denominator[i] else None
                          for i, name in enumerate(STATE_CLASSES)}, "state_confusion": confusion.tolist(),
            "rows": rows, "selection_rule": "equal-surface mean absolute ratio error; held ROI penalty=1; no test selection"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--init", type=Path, help="Reviewed 6-state or 8-state checkpoint; required for training")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--synthetic-smoke", action="store_true", help="Mark generated-data test weights as non-production")
    args = parser.parse_args(argv)
    config = read_json(args.config)
    validate_config(config)
    if config["n_state"] != 8:
        raise ValueError("This trainer only creates the new three-surface checkpoint")
    if args.epochs < 1 or args.batch < 1 or not np.isfinite(args.lr) or args.lr <= 0:
        raise ValueError("epochs, batch and learning rate must be positive")
    if args.out.exists():
        raise ValueError("Choose a new output directory; previous experiments are preserved")
    storage = args.out.resolve().parent
    while not storage.exists():
        storage = storage.parent
    if not args.audit_only and shutil.disk_usage(storage).free < 1024 ** 3:
        raise ValueError("Training output requires at least 1 GiB free; choose a different drive")
    items = read_training_manifest(args.data)
    report = audit(items, config)
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "data_audit.json", report)
    if not report["passed"]:
        raise ValueError("Dataset audit blocked training: " + ", ".join(report["problems"]))
    if args.audit_only:
        print("Dataset audit passed; no training executed.")
        return 0
    if not args.init:
        raise ValueError("--init is required; no training was started")
    os.environ["HF_HUB_OFFLINE"] = "1"
    import torch
    import timm
    from checkpoint import transfer_weights
    from vision_preprocessing.train.train_quick import TwoHeadSeg, MEAN, STD, weighted_ce
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available()
                          else "cpu" if args.device == "auto" else args.device)
    ck = torch.load(args.init, map_location="cpu", weights_only=True)
    model = TwoHeadSeg(config["encoder"], config["decoder"], pretrained=True, freeze=True,
                       n_state=8, vit_pad_crop=True)
    transfer = transfer_weights(model, ck, config)
    encoder_hash = hashlib.sha256()
    for key, value in sorted(model.encoder.state_dict().items()):
        encoder_hash.update(key.encode())
        encoder_hash.update(str((tuple(value.shape), value.dtype)).encode())
        encoder_hash.update(value.cpu().contiguous().numpy().tobytes())
    model.to(device)
    amp_dtype = torch.bfloat16 if device.type == "cuda" and torch.cuda.is_bf16_supported() else torch.float16
    split = {s: [i for i in items if i["split"] == s] for s in ("train", "val", "test")}
    record = {"schema_version": "final-training/0.2", "config": config, "seed": args.seed,
              "epochs": args.epochs, "batch": args.batch, "lr": args.lr, "encoder_frozen": True,
              "training_precision": str(amp_dtype) if device.type == "cuda" else "float32",
              "initialization_sha256": sha256_file(args.init), "transfer": transfer,
              "data_manifest_sha256": sha256_file(args.data), "torch": torch.__version__, "timm": timm.__version__,
              "code_sha256": {name: sha256_file(Path(__file__).with_name(name)) for name in
                              ("train.py", "training_data.py", "checkpoint.py", "layout.py", "core.py", "model.py")},
              "encoder_state_sha256": encoder_hash.hexdigest(),
              "initialization_data_overlap": "not_audited; old task-trained weights may have seen new evaluation images",
              "training_scope": "synthetic_smoke_only" if args.synthetic_smoke else "reviewed_exports",
              "samples": [{"key": i["key"], "split": i["split"], "label_source": i["label_source"],
                           "site_id": i["site_id"], "view_id": i["view_id"], "date": i["date"], "targets": i["targets"],
                           "files": {k: {"path": str(p), "sha256": sha256_file(p)} for k, p in i["files"].items()},
                           "states": {k: {"path": str(p), "sha256": sha256_file(p)} for k, p in i["states"].items() if p.exists()}}
                          for i in items]}
    write_json(args.out / "training_record.json", record)
    loader = torch.utils.data.DataLoader(Samples(split["train"], config, augment=True),
                                        batch_size=args.batch, shuffle=True, num_workers=0)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=1e-4)
    scaler = torch.amp.GradScaler(device.type, enabled=device.type == "cuda" and amp_dtype == torch.float16, init_scale=256.)
    best_score, history = float("inf"), []
    for epoch in range(1, args.epochs + 1):
        model.train()
        total, samples, skipped_steps = 0., 0, 0
        for x, sem, state, weights in loader:
            x, sem, state, weights = x.to(device), sem.to(device), state.to(device), weights.float().to(device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, enabled=device.type == "cuda", dtype=amp_dtype):
                semantic_logits, state_logits = model(x)
                loss = .5 * weighted_ce(semantic_logits.float(), sem, weights) + weighted_ce(state_logits.float(), state, weights)
            if not torch.isfinite(loss):
                raise FloatingPointError("Nonfinite training loss; no completed run published")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                if not scaler.is_enabled():
                    raise FloatingPointError("Nonfinite gradients outside scaled FP16 training")
                scaler.step(optimizer)  # GradScaler skips the invalid step and reduces its scale.
                scaler.update()
                skipped_steps += 1
                continue
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1., error_if_nonfinite=True)
            scaler.step(optimizer)
            scaler.update()
            total += loss.item() * x.shape[0]
            samples += x.shape[0]
        if not samples:
            raise FloatingPointError("No successful optimization steps in this epoch")
        validation = evaluate(model, split["val"], config, device)
        score = validation["macro_selection_error"]
        if score is None:
            raise ValueError("All three surfaces require usable validation ground truth")
        checkpoint = {"encoder": config["encoder"], "decoder": config["decoder"], "n_sem": 8, "n_state": 8,
                      "activity": "all_surfaces_v2", "state_classes": STATE_CLASSES, "surfaces": list(SURFACES),
                      "state_outside": "full", "vit_pad_crop": True, "export_size": config["export_size"],
                      "input": {"mean": MEAN.flatten().tolist(), "std": STD.flatten().tolist(), "rgb_only": True},
                      "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items() if not k.startswith("encoder.")},
                      "epoch": epoch, "seed": args.seed, "protocol_version": "final-training/0.2",
                      "training_scope": record["training_scope"], "encoder_state_sha256": encoder_hash.hexdigest(),
                      "validation": validation}
        for name in ["last.pt"] + (["best.pt"] if score < best_score else []):
            temp = args.out / (name + ".tmp")
            torch.save(checkpoint, temp)
            temp.replace(args.out / name)
        best_score = min(best_score, score)
        history.append({"epoch": epoch, "loss": total / samples, "skipped_fp16_steps": skipped_steps, "validation": validation})
        write_json(args.out / "history.json", history)
        print(f"epoch={epoch} loss={total/samples:.5f} validation_selection_error={score:.5f}", flush=True)
    best = torch.load(args.out / "best.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(best["state_dict"], strict=False)
    test = evaluate(model, split["test"], config, device) if split["test"] else None
    write_json(args.out / "training_complete.json", {"completed": True, "best_epoch": best["epoch"],
               "test": test, "test_available": bool(split["test"]), "training_scope": record["training_scope"],
               "note": "Training completion is not deployment approval or validated new-site generalization"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
