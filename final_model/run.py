"""Offline final-model inference. Outputs remain local; no backend writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import UUID

import numpy as np
from PIL import Image

from backend_contract import observation_draft
from core import measure_targets, render_overlay, validate_config, validate_targets
from model import REPO, Predictor, sha256_file
from vision_preprocessing.rasterize import apply_mask


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def local_path(base, value):
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def preflight(input_path, config):
    validate_config(config)
    doc = read_json(input_path)
    base = input_path.resolve().parent
    for key in ("site_code", "view_key"):
        if (not isinstance(doc.get(key), str) or not doc[key].strip() or
                doc[key] != doc[key].strip() or len(doc[key]) > 128):
            raise ValueError(f"A nonempty {key} is required (max 128 characters)")
    if type(doc.get("view_confirmed")) is not bool:
        raise ValueError("view_confirmed must be an explicit boolean")
    if doc.get("roi_space") not in ("original", "model_input"):
        raise ValueError("roi_space must explicitly be original or model_input")
    source_wh = doc["source_wh"]
    if len(source_wh) != 2 or any(type(v) is not int or v <= 0 for v in source_wh):
        raise ValueError("source_wh must contain positive integer width and height")
    roi_path = local_path(base, doc["roi_mask"])
    with Image.open(roi_path) as image:
        roi = np.array(image)
    validate_targets(roi, doc["targets"], config)
    if roi.dtype.itemsize > 2:
        if roi.min() < 0 or roi.max() > 65535:
            raise ValueError("ROI index exceeds uint16 range")
        roi = roi.astype(np.uint16)
    expected = source_wh if doc["roi_space"] == "original" else config["export_size"]
    if (roi.shape[1], roi.shape[0]) != tuple(expected):
        raise ValueError("ROI dimensions do not match the declared coordinate system")
    for target in doc["targets"]:
        for key in ("schedule_id", "task_id"):
            if target.get(key) is not None:
                UUID(target[key])
    if not doc["photos"]:
        raise ValueError("At least one photo is required")
    seen = set()
    for photo in doc["photos"]:
        time_text = photo["captured_at"]
        captured = datetime.fromisoformat(time_text[:-1] + "+00:00" if time_text.endswith("Z") else time_text)
        if captured.utcoffset() is None:
            raise ValueError("captured_at must include the camera's actual UTC offset")
        path = local_path(base, photo["path"])
        with Image.open(path) as image:
            if image.size != tuple(source_wh):
                raise ValueError("Photo dimensions differ from the registered view")
            if image.getexif().get(274, 1) != 1:
                raise ValueError("EXIF rotation must be resolved together with the ROI before inference")
            image.load()
        record = photo.get("daily_record_id")
        if record is not None:
            UUID(record)
        revision = photo.get("revision", 1)
        if type(revision) is not int or not 1 <= revision <= 100000:
            raise ValueError("Invalid observation revision")
        key = (record or str(path), revision)
        if key in seen:
            raise ValueError("Duplicate photo / daily_record_id with the same revision")
        seen.add(key)
    return doc, roi, roi_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    config = read_json(args.config)
    doc, roi, roi_path = preflight(args.input, config)
    if args.validate_only:
        print(f"Input valid: {len(doc['photos'])} photos, {len(doc['targets'])} targets. Model not executed.")
        return 0
    if args.checkpoint is None or args.out is None:
        parser.error("--checkpoint and --out are required for inference")
    if args.out.exists():
        raise ValueError("Output folder already exists; choose a new folder to preserve evidence")
    predictor = Predictor(args.checkpoint, config, args.device)
    args.out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parent
    code_paths = [root / name for name in ("run.py", "core.py", "model.py", "layout.py", "backend_contract.py")]
    code_paths += [REPO / "src/vision/state_pipeline/vision_preprocessing" / name for name in
                   ("rasterize.py", "train/train_quick.py", "train/decoders.py")]
    provenance = {"pipeline_version": config["pipeline_version"], "source": "model",
                  "generated_at": datetime.now(timezone.utc).isoformat(), "model": predictor.info,
                  "config": config, "input_sha256": sha256_file(args.input),
                  "roi_sha256": sha256_file(roi_path), "config_sha256": sha256_file(args.config),
                  "code_sha256": {str(p.relative_to(REPO)).replace('\\', '/'): sha256_file(p) for p in code_paths}}
    write_json(args.out / "run_provenance.json", provenance)
    # Save explicit ROI registration and mapping for reproducibility; output directory is private.
    write_json(args.out / "input_registration.json", doc)
    rows, drafts, photo_evidence = [], [], []
    for photo_i, photo in enumerate(doc["photos"]):
        path = local_path(args.input.resolve().parent, photo["path"])
        with Image.open(path) as image:
            rgb, semantic, state, confidence, transform = predictor.predict(image)
        transformed_roi = (apply_mask(roi, transform, 0) if doc["roi_space"] == "original" else roi).copy()
        # Padding never represents an observable construction surface, even with model-input ROIs.
        valid_canvas = apply_mask(np.ones((doc["source_wh"][1], doc["source_wh"][0]), np.uint8), transform, 0)
        transformed_roi[valid_canvas == 0] = 0
        folder = args.out / f"photo_{photo_i:05d}"
        folder.mkdir()
        Image.fromarray(semantic).save(folder / "semantic.png")
        Image.fromarray(state).save(folder / "state.png")
        Image.fromarray(transformed_roi).save(folder / "roi.png")
        np.save(folder / "confidence.npy", confidence)
        evidence = {"photo_index": photo_i, "captured_at": photo["captured_at"],
                    "photo_sha256": sha256_file(path), "transform": transform,
                    "semantic_mask": f"{folder.name}/semantic.png", "state_mask": f"{folder.name}/state.png",
                    "roi_mask": f"{folder.name}/roi.png", "confidence_map": f"{folder.name}/confidence.npy"}
        photo_evidence.append(evidence)
        measurements = measure_targets(state, confidence, transformed_roi, doc["targets"],
                                       config, doc["view_confirmed"])
        for target_i, (target, row) in enumerate(zip(doc["targets"], measurements)):
            overlay = f"{folder.name}/target_{target_i:03d}_overlay.png"
            Image.fromarray(render_overlay(rgb, state, transformed_roi, target, config)).save(args.out / overlay)
            row.update(photo_index=photo_i, captured_at=photo["captured_at"],
                       view_key=doc["view_key"], site_code=doc["site_code"],
                       schedule_id=target.get("schedule_id"), task_id=target.get("task_id"),
                       daily_record_id=photo.get("daily_record_id"), revision=photo.get("revision", 1),
                       overlay=overlay, state_mask=evidence["state_mask"])
            rows.append(row)
            draft = observation_draft(doc["site_code"], doc["view_key"], photo, target, row)
            drafts.append({"photo_index": photo_i, "target_key": target["target_key"],
                           "local_overlay": overlay, **draft})
        print(f"Processed photo {photo_i + 1}/{len(doc['photos'])}", flush=True)
    write_json(args.out / "api_drafts.json", {"sent": False, "requests": drafts,
               "note": "Register photos/overlays and verify task/location scope before sending. "
                       "Model provenance is local; backend persistence of it is not implemented."})
    # Publish completion last, only after every artifact and request draft has been written.
    write_json(args.out / "vision_result.json", {
        "schema_version": config["pipeline_version"], "run_status": "complete", "site_code": doc["site_code"],
        "meaning": "Observed ROI installed pixel fraction; not physical area or whole-task/store progress",
        "provenance_file": "run_provenance.json", "photos": photo_evidence, "observations": rows})
    print(f"Saved {len(rows)} observations. Backend requests were NOT sent.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as exc:
        print(f"Final pipeline stopped: {exc}", file=sys.stderr)
        sys.exit(1)
