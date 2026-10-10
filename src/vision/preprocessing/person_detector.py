# SPDX-License-Identifier: GPL-3.0-only
# Modified standalone adaptation of X-AnyLabeling; preserve upstream GPL notices.
# See LICENSE.x-anylabeling.txt and THIRD_PARTY_NOTICES.md.
"""Local CPU DEIMv2 person box proposals, not reviewed person masks.

Preprocessing adapts the X-AnyLabeling DEIMv2 adapter: RGB / 255,
640-pixel aspect-preserving resize and centered black padding. The model's
``orig_target_sizes`` is the padded size, so output boxes must be unpadded.
The compatible COCO export uses contiguous class 0 for ``person``. Supply a
local ONNX model explicitly; this module does not download assets.

Primary references (checked 2026-10-05):
https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/deimv2.py
https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/configs/auto_labeling/deimv2_hgnetv2_n_coco.yaml

No extra NMS is applied, matching that adapter. Scores are model scores,
not calibrated probabilities or measured accuracy. A missing detection
does not establish that pixels belong to a reviewed ``other`` class.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw


# Contiguous export indices, not the discontinuous official COCO category IDs.
# Ordered exactly as the primary X-AnyLabeling YAML linked above.
COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
)
assert len(COCO_CLASSES) == 80


def prepare_image(rgb: np.ndarray, image_size: int = 640):
    """Return input tensor and exact original-to-padded image transform."""
    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("predict expects RGB uint8 array of shape H x W x 3")
    height, width = rgb.shape[:2]
    if width < 1 or height < 1:
        raise ValueError("empty image")
    ratio = min(image_size / width, image_size / height)
    new_width = max(1, int(width * ratio))
    new_height = max(1, int(height * ratio))
    padding_x = (image_size - new_width) // 2
    padding_y = (image_size - new_height) // 2
    image = Image.fromarray(rgb)
    resized = image.resize((new_width, new_height), Image.Resampling.BILINEAR)
    padded = Image.new("RGB", (image_size, image_size), (0, 0, 0))
    padded.paste(resized, (padding_x, padding_y))
    tensor = np.ascontiguousarray(
        np.asarray(padded, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    )
    transform = {
        "width": width,
        "height": height,
        "scale_x": new_width / width,
        "scale_y": new_height / height,
        "padding_x": padding_x,
        "padding_y": padding_y,
    }
    return tensor, transform


def recover_boxes(boxes: np.ndarray, transform: dict) -> np.ndarray:
    """Convert padded xyxy coordinates to clipped original pixel coordinates."""
    boxes = np.asarray(boxes, dtype=np.float32).reshape(-1, 4).copy()
    boxes[:, [0, 2]] = (
        boxes[:, [0, 2]] - transform["padding_x"]
    ) / transform["scale_x"]
    boxes[:, [1, 3]] = (
        boxes[:, [1, 3]] - transform["padding_y"]
    ) / transform["scale_y"]
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, transform["width"])
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, transform["height"])
    return boxes


class PersonDetector:
    """Reusable ONNX session; ``predict(rgb)`` returns person box drafts."""

    def __init__(
        self,
        model_path: str | Path,
        score_threshold: float = 0.40,
        threads: int = 4,
    ):
        if not 0.0 <= score_threshold <= 1.0:
            raise ValueError("score_threshold must be in [0, 1]")
        if threads < 1:
            raise ValueError("threads must be positive")
        self.model_path = Path(model_path).resolve(strict=True)
        self.score_threshold = float(score_threshold)
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        input_names = {item.name for item in self.session.get_inputs()}
        output_names = {item.name for item in self.session.get_outputs()}
        if input_names != {"images", "orig_target_sizes"}:
            raise ValueError(f"unexpected model input names: {input_names}")
        if output_names != {"labels", "boxes", "scores"}:
            raise ValueError(f"unexpected model output names: {output_names}")
        self.model_details = {
            "model_name": self.model_path.name,
            "model_sha256": hashlib.sha256(self.model_path.read_bytes()).hexdigest(),
            "providers": self.session.get_providers(),
            "score_threshold": self.score_threshold,
            "score_semantics": "unreviewed model score; not calibrated class probability",
            "preprocessing": "RGB / 255; bilinear aspect resize; centered black 640 letterbox",
            "box_coordinates": "original-image float pixel xyxy",
            "class_mapping_type": "contiguous 0..79 export index; not COCO category_id",
            "class_mapping": list(COCO_CLASSES),
            "nms": "none, matching primary X-AnyLabeling DEIMv2 adapter",
            "primary_sources_checked_on": "2026-10-05",
            "primary_sources": [
                "https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/deimv2.py",
                "https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/configs/auto_labeling/deimv2_hgnetv2_n_coco.yaml",
            ],
            "absence_uncertainty": (
                "No detection is not absence evidence. COCO does not cover construction "
                "materials, ladder or scissor lift; missed and unsupported objects remain "
                "unknown, never automatically reviewed other. Detected classes can be "
                "semantically wrong and are candidates requiring review."
            ),
            "label_status": "model_draft_not_ground_truth",
        }

    def predict(self, rgb: np.ndarray) -> list[dict]:
        """Preserved person-only interface for SAM box prompting."""
        return [
            {"xyxy": item["xyxy"], "score": item["score"], "class_name": "person"}
            for item in self.predict_all(rgb) if item["class_id"] == 0
        ]

    def predict_all(self, rgb: np.ndarray) -> list[dict]:
        """Return all COCO candidate boxes; use one inference per input photo.

        ``class_id`` is a contiguous export index. To get nonperson candidates,
        filter ``item['class_id'] != 0``. These boxes are not object masks or GT.
        """
        tensor, transform = prepare_image(rgb)
        labels, boxes, scores = self.session.run(
            ["labels", "boxes", "scores"],
            {
                "images": tensor,
                "orig_target_sizes": np.array([[640, 640]], dtype=np.int64),
            },
        )
        labels = np.asarray(labels)[0].reshape(-1)
        boxes = np.asarray(boxes)[0].reshape(-1, 4)
        scores = np.asarray(scores)[0].reshape(-1)
        if not (len(labels) == len(boxes) == len(scores)):
            raise ValueError("model output lengths disagree")
        keep = (labels >= 0) & (labels < len(COCO_CLASSES)) & np.isfinite(scores) & (
            scores >= self.score_threshold
        )
        kept_scores = scores[keep]
        kept_labels = labels[keep]
        recovered = recover_boxes(boxes[keep], transform)
        result = []
        for box, score, label in zip(recovered, kept_scores, kept_labels):
            if not np.all(np.isfinite(box)):
                continue
            xmin, ymin, xmax, ymax = map(float, box)
            if xmax <= xmin or ymax <= ymin:
                continue
            result.append(
                {"xyxy": [xmin, ymin, xmax, ymax], "score": float(score),
                 "class_name": COCO_CLASSES[int(label)], "class_id": int(label)}
            )
        return sorted(result, key=lambda item: item["score"], reverse=True)


def _geometry_check():
    for width, height in [(2688, 1512), (2048, 1537), (371, 913)]:
        rgb = np.zeros((height, width, 3), dtype=np.uint8)
        tensor, transform = prepare_image(rgb)
        original = np.array([[0, 0, width, height], [10, 20, 100, 200]], np.float32)
        padded = original.copy()
        padded[:, [0, 2]] = (
            padded[:, [0, 2]] * transform["scale_x"] + transform["padding_x"]
        )
        padded[:, [1, 3]] = (
            padded[:, [1, 3]] * transform["scale_y"] + transform["padding_y"]
        )
        np.testing.assert_allclose(recover_boxes(padded, transform), original,
                                   atol=0.001)
        assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    return "letterbox inverse coordinates passed for landscape, odd and portrait sizes"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--model-path", "--model", dest="model", type=Path,
                        help="Existing local DEIMv2 ONNX model; required with --image")
    parser.add_argument("--threshold", type=float, default=0.40)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overlay", type=Path)
    args = parser.parse_args()
    if args.image is not None and args.model is None:
        parser.error("--model-path is required with --image")
    report = {"geometry_check": _geometry_check(),
              "status": "model_draft_not_ground_truth", "nms": "none"}
    if args.image:
        started = time.perf_counter()
        detector = PersonDetector(args.model, args.threshold, args.threads)
        report["session_load_seconds"] = round(time.perf_counter() - started, 4)
        with Image.open(args.image) as image:
            rgb = np.asarray(image.convert("RGB"))
        started = time.perf_counter()
        all_predictions = detector.predict_all(rgb)
        predictions = [item for item in all_predictions if item["class_id"] == 0]
        report.update({
            "image_name": args.image.name,
            "original_size_wh": [rgb.shape[1], rgb.shape[0]],
            "inference_seconds": round(time.perf_counter() - started, 4),
            "score_threshold": args.threshold,
            "person_predictions": predictions,
            "all_class_predictions": all_predictions,
            "model_name": detector.model_path.name,
            "model_sha256": hashlib.sha256(detector.model_path.read_bytes()).hexdigest(),
            "providers": detector.session.get_providers(),
        })
        if args.overlay:
            if args.overlay.exists():
                raise FileExistsError("refusing to overwrite overlay")
            canvas = Image.fromarray(rgb)
            draw = ImageDraw.Draw(canvas)
            for item in all_predictions:
                color = "lime" if item["class_id"] == 0 else "orange"
                draw.rectangle(item["xyxy"], outline=color, width=5)
                draw.text(item["xyxy"][:2],
                          f"{item['class_name']} {item['score']:.3f} DRAFT",
                          fill=color, stroke_width=1, stroke_fill="black")
            draw.text((20, 20), "DEIMv2 BOX CANDIDATES - NOT REVIEWED MASKS",
                      fill="yellow", stroke_width=1, stroke_fill="black")
            args.overlay.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(args.overlay)
            report["overlay_path"] = str(args.overlay.resolve())
    if args.output:
        if args.output.exists():
            raise FileExistsError("refusing to overwrite existing report")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
