"""DeepLabv3+의 5클래스 학습 연결을 확인하는 작은 로컬 실험.

미검수 초안을 이용한 실행 확인이며, 공정률/시공 상태 모델이나 논문 평가가 아니다.
현장 데이터는 읽기만 하며 새 출력 폴더에 가중치와 예측 결과를 저장한다.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image, ImageDraw
import torch
import torch.nn.functional as F
import segmentation_models_pytorch as smp

from dataset import load_pilot, SmokeDataset

CLASSES = ["wall", "floor", "ceiling", "person", "other"]
COLORS = np.array([[90, 185, 110], [225, 125, 105], [115, 135, 225],
                   [245, 205, 70], [160, 125, 180]], dtype=np.uint8)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_model(encoder_checkpoint=None):
    # SMP의 실제 DeepLabv3+ 구현이다. torchvision의 DeepLabv3와 구분한다.
    # 다운로드는 여기서 하지 않는다. 로컬 ImageNet encoder 가중치를 명시하여 넣는다.
    model = smp.DeepLabV3Plus(encoder_name="resnet50", encoder_weights=None,
                            in_channels=3, classes=5, activation=None)
    if encoder_checkpoint is not None:
        state = torch.load(encoder_checkpoint, map_location="cpu", weights_only=True)
        model.encoder.load_state_dict(state)  # SMP ResNet encoder가 fc 키를 제거한다.
    return model


def summarize_confusion(confusion):
    # 255 픽셀은 confusion을 만들기 전 제외한다. union=0인 클래스만 평균에서 제외.
    intersection = np.diag(confusion).astype(float)
    support = confusion.sum(axis=1)
    predicted = confusion.sum(axis=0)
    union = support + predicted - intersection
    ious = [float(intersection[i] / union[i]) if union[i] else None for i in range(5)]
    present = [x for x in ious if x is not None]
    return {
        "confusion_matrix_gt_rows_prediction_columns": confusion.tolist(),
        "per_class_iou": dict(zip(CLASSES, ious)),
        "mean_iou_nonempty_union": float(np.mean(present)) if present else None,
        "valid_pixel_accuracy": float(intersection.sum() / support.sum()) if support.sum() else None,
        "draft_support_pixels": dict(zip(CLASSES, support.tolist())),
        "predicted_valid_pixels": dict(zip(CLASSES, predicted.tolist())),
        "metric_scope": "agreement_with_unreviewed_draft_masks_not_ground_truth_accuracy",
    }


@torch.no_grad()
def evaluate(model, dataset, device, weights):
    model.eval()
    confusion = np.zeros((5, 5), dtype=np.int64)
    numerator, denominator, predictions = 0.0, 0.0, []
    for image, target in dataset:
        x, y = image[None].to(device), target[None].to(device)
        # 평가는float32. 초기와 학습 후를 같은eval 조건으로 비교한다.
        logits = model(x)
        prediction = logits.argmax(1)
        valid = y != 255
        if not valid.any():
            raise ValueError("No valid pixels in evaluation image")
        loss_map = F.cross_entropy(logits, y, weight=weights, ignore_index=255, reduction="none")
        numerator += float(loss_map[valid].sum())
        denominator += float(weights[y[valid]].sum())
        flat = (5 * y[valid] + prediction[valid]).cpu()
        confusion += torch.bincount(flat, minlength=25).reshape(5, 5).numpy()
        predictions.append(prediction[0].cpu().numpy().astype(np.uint8))
    metrics = summarize_confusion(confusion)
    metrics["weighted_cross_entropy"] = numerator / denominator
    return metrics, predictions


def constant_baseline(dataset, class_id):
    confusion = np.zeros((5, 5), dtype=np.int64)
    confusion[:, class_id] = dataset.histogram
    result = summarize_confusion(confusion)
    result["constant_class_chosen_from_train_only"] = CLASSES[class_id]
    return result


def export_predictions(output, entries, dataset, predictions, split):
    # 예측은 원래 계약의1..5로 복원한다.255는 예측 클래스가 아닌 손실 제외 값이다.
    # raw 예측은 전체 화면,display는 입력 라벨의unknown/워터마크를255로 가린 별도 파일이다.
    directory = output / "predictions" / split
    directory.mkdir(parents=True)
    files = []
    width, height = dataset.size
    for order, (entry, (_, target), prediction) in enumerate(zip(entries, dataset, predictions), 1):
        alias = "{}_{:02d}".format(split, order)
        semantic = prediction + 1
        Image.fromarray(semantic).save(directory / (alias + "_semantic.png"))
        display = semantic.copy()
        valid = target.numpy() != 255
        display[~valid] = 255
        Image.fromarray(display).save(directory / (alias + "_display.png"))
        # 원본 해상도 raw prediction은 nearest로 복원한다. 시각화와 혼동하지 않는다.
        with Image.open(entry["image_path"]) as source:
            native = source.size
            image = np.asarray(source.convert("RGB").resize((width, height), Image.Resampling.BILINEAR))
        Image.fromarray(semantic).resize(native, Image.Resampling.NEAREST).save(directory / (alias + "_native_semantic.png"))
        color = COLORS[prediction]
        overlay = image.copy()
        overlay[valid] = (0.55 * image[valid] + 0.45 * color[valid]).astype(np.uint8)
        # 왼쪽=원본 사진,중앙=미검수 초안,오른쪽=모델 추론. 미분류는 색칠하지 않는다.
        draft = image.copy()
        gt = target.numpy()
        draft[valid] = (0.55 * image[valid] + 0.45 * COLORS[gt[valid]]).astype(np.uint8)
        canvas = Image.new("RGB", (width * 3, height + 30), (245, 245, 245))
        for i, tile in enumerate([image, draft, overlay]):
            canvas.paste(Image.fromarray(tile), (i * width, 30))
        draw = ImageDraw.Draw(canvas)
        for i, title in enumerate(["SOURCE", "DRAFT LABEL (UNREVIEWED)", "MODEL PREDICTION"]):
            draw.text((i * width + 8, 8), title, fill="black")
        canvas.save(directory / (alias + "_comparison.jpg"), quality=90)
        files.append({"alias": alias, "image_id": entry["image_id"], "native_size": list(native)})
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2", type=Path, required=True)
    parser.add_argument("--v3", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoder-checkpoint", type=Path, required=True)
    parser.add_argument("--selection-json", type=Path, required=True,
                        help="Git에서 제외한 로컬JSON: train_suffixes, validation_suffixes")
    parser.add_argument("--allow-drafts-for-smoke", action="store_true")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--height", type=int, default=288)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new output folder; existing run is preserved")
    if args.steps < 1 or args.batch_size < 2 or args.height % 16 or args.width % 16:
        raise ValueError("steps>=1, batch-size>=2, size must be a multiple of16")
    if not args.encoder_checkpoint.is_file():
        raise FileNotFoundError(args.encoder_checkpoint)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    device = torch.device("cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu")
    selection = json.loads(args.selection_json.read_text(encoding="utf-8"))
    train_entries, val_entries, audit = load_pilot(args.v2, args.v3, selection.get("train_suffixes"),
                                                selection.get("validation_suffixes"), args.allow_drafts_for_smoke)
    train = SmokeDataset(train_entries, size=(args.width, args.height))
    validation = SmokeDataset(val_entries, size=(args.width, args.height))
    # 작은 실험에서도 두 그룹에5개 클래스가 모두 남는지 확인한다.
    if np.any(train.histogram == 0) or np.any(validation.histogram == 0):
        raise ValueError("Pilot must contain all5 classes in both splits after resize")
    args.output.mkdir(parents=True)
    audit["train_class_pixels"] = dict(zip(CLASSES, train.histogram.tolist()))
    audit["validation_class_pixels"] = dict(zip(CLASSES, validation.histogram.tolist()))
    audit["train_valid_coverage"] = train.valid_coverage
    audit["validation_valid_coverage"] = validation.valid_coverage
    write_json(args.output / "data_audit.json", audit)
    write_json(args.output / "split_manifest.json", {
        "train": train_entries, "validation": val_entries,
        "split_note": "different_days_same_site_smoke_not_chronological_forecasting_or_unseen_site_test",
    })
    config = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    config.update({"model": "DeepLabV3Plus", "encoder": "resnet50", "classes": CLASSES,
                   "encoder_checkpoint_sha256": sha256(args.encoder_checkpoint),
                   "encoder_source": "torchvision ResNet50 IMAGENET1K_V2", "backbone_lr": 1e-5,
                   "decoder_lr": 1e-3, "ignore_index": 255, "amp": device.type == "cuda",
                   "encoder_batch_norm": "running_statistics_frozen_small_pilot",
                   "dataset_masks_human_reviewed": False, "construction_progress_prediction": False})
    write_json(args.output / "config.json", config)
    write_json(args.output / "environment.json", {
        "torch": torch.__version__, "torchvision": importlib.metadata.version("torchvision"),
        "smp": smp.__version__, "numpy": np.__version__, "pillow": importlib.metadata.version("pillow"),
        "device": str(device), "cuda_build": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
    })
    model = build_model(args.encoder_checkpoint).to(device)
    # 희소한person이 무시되지 않게 역제곱근 빈도 가중치를 사용하고 최대5로 제한한다.
    frequencies = train.histogram / train.histogram.sum()
    class_weights = np.minimum(1 / np.sqrt(frequencies), 5)
    class_weights /= class_weights.mean()
    weights = torch.tensor(class_weights, dtype=torch.float32, device=device)
    initial_train, _ = evaluate(model, train, device, weights)
    initial_val, _ = evaluate(model, validation, device, weights)
    head_before = model.segmentation_head[0].weight.detach().clone()
    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(), "lr": 1e-5},
        {"params": list(model.decoder.parameters()) + list(model.segmentation_head.parameters()), "lr": 1e-3},
    ], weight_decay=1e-4)
    scaler = torch.amp.GradScaler(device.type, enabled=device.type == "cuda")
    history = []
    started = time.perf_counter()
    # 표본을 순환시켜3장 모두 학습한다. 전체9918장은 이번 실행의 대상이 아니다.
    for step in range(args.steps):
        model.train()
        model.encoder.eval()  # encoder의BN 통계는 고정하지만 가중치의 기울기는 계산한다.
        indices = [(step + i) % len(train) for i in range(args.batch_size)]
        x = torch.stack([train[i][0] for i in indices]).to(device)
        y = torch.stack([train[i][1] for i in indices]).to(device)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type=device.type, enabled=device.type == "cuda"):
            logits = model(x)
            loss = F.cross_entropy(logits, y, weight=weights, ignore_index=255)
        if not torch.isfinite(loss):
            raise FloatingPointError("Non-finite loss")
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        if not torch.isfinite(gradient_norm):
            raise FloatingPointError("Non-finite gradient")
        scaler.step(optimizer)
        scaler.update()
        history.append({"step": step + 1, "loss": float(loss.detach()), "gradient_norm": float(gradient_norm)})
        if step == 0 or (step + 1) % 10 == 0 or step + 1 == args.steps:
            print(json.dumps(history[-1]), flush=True)
    training_seconds = time.perf_counter() - started
    final_train, train_predictions = evaluate(model, train, device, weights)
    final_val, val_predictions = evaluate(model, validation, device, weights)
    changed = not torch.equal(head_before, model.segmentation_head[0].weight)
    checkpoint_path = args.output / "model_last.pt"
    torch.save({"state_dict": model.cpu().state_dict(), "architecture": "DeepLabV3Plus",
                "encoder": "resnet50", "class_names": CLASSES, "seed": args.seed,
                "config": config}, checkpoint_path)
    # 저장한 가중치를 새 모델에 불러와 같은 추론이 나오는지 확인한다.
    restored = build_model().to(device)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    restored.load_state_dict(checkpoint["state_dict"])
    _, reloaded_predictions = evaluate(restored, validation, device, weights)
    reload_identical = all(np.array_equal(a, b) for a, b in zip(val_predictions, reloaded_predictions))
    exports = export_predictions(args.output, train_entries, train, train_predictions, "train")
    exports += export_predictions(args.output, val_entries, validation, val_predictions, "validation")
    majority = int(train.histogram.argmax())
    results = {
        "scope": "local_smoke_unreviewed_draft_agreement_only", "train_images": len(train),
        "validation_images": len(validation), "steps": args.steps, "training_seconds": training_seconds,
        "class_weights": dict(zip(CLASSES, class_weights.tolist())),
        "initial_train": initial_train, "initial_validation": initial_val,
        "final_train": final_train, "final_validation": final_val,
        "validation_train_majority_class_baseline": constant_baseline(validation, majority),
        "checkpoint_sha256": sha256(checkpoint_path),
        "smoke_checks": {"all_losses_finite": True, "all_gradient_norms_finite": True,
                         "segmentation_head_updated": changed, "checkpoint_reloaded_predictions_identical": reload_identical,
                         "train_eval_loss_decreased": final_train["weighted_cross_entropy"] < initial_train["weighted_cross_entropy"]},
        "predictions": exports,
    }
    write_json(args.output / "history.json", history)
    write_json(args.output / "metrics.json", results)
    gallery = '<!doctype html><meta charset="utf-8"><title>Vision smoke</title><style>body{font-family:sans-serif;background:#f6f4ef;padding:24px}img{max-width:100%;margin-bottom:24px}p{max-width:1000px}</style><h1>DeepLabv3+ 5-class smoke</h1><p>Source / Unreviewed draft / Model prediction. Green=wall, red=floor, blue=ceiling, yellow=person, purple=other. Unknown remains uncolored. These are draft-agreement diagnostics, not ground-truth accuracy or construction progress.</p>'
    for row in exports:
        split = row["alias"].rsplit("_", 1)[0]
        gallery += '<h2>{}</h2><img src="predictions/{}/{}_comparison.jpg">'.format(row["alias"], split, row["alias"])
    (args.output / "index.html").write_text(gallery, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "training_seconds": training_seconds,
                      "validation_draft_mIoU": final_val["mean_iou_nonempty_union"],
                      "smoke_checks": results["smoke_checks"]}), flush=True)
    if not all(results["smoke_checks"].values()):
        raise RuntimeError("Smoke checks failed; see metrics.json")


if __name__ == "__main__":
    main()
