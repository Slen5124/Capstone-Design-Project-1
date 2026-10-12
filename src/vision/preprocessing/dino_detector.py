# SPDX-License-Identifier: GPL-3.0-only
# Modified standalone adaptation of X-AnyLabeling; preserve upstream GPL notices.
# See LICENSE.x-anylabeling.txt and THIRD_PARTY_NOTICES.md.
"""Local CPU Grounding DINO ONNX adapter; produces unreviewed box proposals.

No input imagery is transmitted. Local model and tokenizer files are read-only
and must be supplied explicitly; this module does not download assets.
Image normalization and text attention construction adapt the public X-AnyLabeling
GroundingDINOBase / Grounding_DINO implementation. Local ONNX names and types are
checked rather than inferred from a public version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

SOURCES = [
    "https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/__base__/grounding_dino.py",
    "https://github.com/CVHub520/X-AnyLabeling/blob/main/anylabeling/services/auto_labeling/grounding_dino.py",
]


class LocalBertTokenizer:
    """BERT basic/WordPiece tokenizer for short English detection phrases.

    Uses the full BERT vocabulary from a local tokenizer JSON. This dependency-
    free implementation intentionally rejects non-ASCII phrases, so unsupported
    normalizer behavior cannot silently create wrong tokens.
    """
    def __init__(self, tokenizer_path: str | Path):
        raw = Path(tokenizer_path).read_bytes()
        data = json.loads(raw)
        # Older official tokenizer JSON omits model.type; decoder and prefix identify it.
        if data["model"].get("type", "WordPiece") != "WordPiece" or data.get("decoder", {}).get("type") != "WordPiece":
            raise ValueError("Expected BERT WordPiece tokenizer")
        self.vocab = data["model"]["vocab"]
        self.unk = self.vocab[data["model"]["unk_token"]]
        self.max_chars = data["model"].get("max_input_chars_per_word", 100)
        self.asset = str(tokenizer_path)
        self.sha256 = hashlib.sha256(raw).hexdigest()
        expected = {"[PAD]": 0, "[UNK]": 100, "[CLS]": 101, "[SEP]": 102, ".": 1012, "?": 1029}
        if any(self.vocab.get(k) != v for k, v in expected.items()):
            raise ValueError("Unexpected BERT vocabulary special token IDs")

    def encode(self, caption: str) -> tuple[list[int], list[tuple[int, int]]]:
        if not caption.isascii():
            raise ValueError("Only ASCII English detection phrases supported")
        caption = "".join(c for c in caption.lower() if ord(c) >= 32 or c in "\t\n\r")
        # BERT basic tokenization separates every punctuation character.
        pieces = []
        for word in caption.split():
            current = ""
            for char in word:
                if unicodedata.category(char).startswith("P") or 33 <= ord(char) <= 47 or 58 <= ord(char) <= 64 or 91 <= ord(char) <= 96 or 123 <= ord(char) <= 126:
                    if current:
                        pieces.append(current)
                        current = ""
                    pieces.append(char)
                else:
                    current += char
            if current:
                pieces.append(current)
        ids = [101]
        for word in pieces:
            if len(word) > self.max_chars:
                ids.append(self.unk)
                continue
            start, word_ids = 0, []
            while start < len(word):
                end, found = len(word), None
                while end > start:
                    candidate = ("##" if start else "") + word[start:end]
                    if candidate in self.vocab:
                        found = self.vocab[candidate]
                        break
                    end -= 1
                if found is None:
                    word_ids = [self.unk]
                    break
                word_ids.append(found)
                start = end
            ids.extend(word_ids)
        ids.append(102)
        if len(ids) > 256:
            raise ValueError("Detection caption exceeds ONNX text limit")
        ranges, start = [], None
        for i, token in enumerate(ids):
            if token in (101, 102, 1012, 1029):
                if start is not None:
                    ranges.append((start, i))
                    start = None
            elif start is None:
                start = i
        return ids, ranges


def text_inputs(ids: list[int]) -> dict[str, np.ndarray]:
    token_ids = np.array([ids], dtype=np.int64)
    n = len(ids)
    mask = np.eye(n, dtype=bool)[None]
    positions = np.zeros((1, n), dtype=np.int64)
    previous = 0
    for col, token in enumerate(ids):
        if token not in (101, 102, 1012, 1029):
            continue
        if col == 0 or col == n - 1:
            mask[0, col, col] = True
            positions[0, col] = 0
        else:
            mask[0, previous + 1:col + 1, previous + 1:col + 1] = True
            positions[0, previous + 1:col + 1] = np.arange(col - previous)
        previous = col
    return {
        "input_ids": token_ids,
        "attention_mask": np.ones((1, n), dtype=bool),
        "position_ids": positions,
        "token_type_ids": np.zeros((1, n), dtype=np.int64),
        "text_token_mask": mask,
    }


class GroundingDetector:
    def __init__(self, model_path, tokenizer_path, threads=4,
                 input_size=(1200, 800), threshold=0.30, max_per_class=12):
        self.model_path = Path(model_path)
        self.tokenizer = LocalBertTokenizer(Path(tokenizer_path))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(self.model_path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        expected = {
            "img": "tensor(float)", "input_ids": "tensor(int64)",
            "attention_mask": "tensor(bool)", "position_ids": "tensor(int64)",
            "token_type_ids": "tensor(int64)", "text_token_mask": "tensor(bool)",
        }
        actual = {x.name: x.type for x in self.session.get_inputs()}
        if actual != expected:
            raise ValueError(f"Unexpected ONNX input schema: {actual}")
        if [x.name for x in self.session.get_outputs()] != ["logits", "boxes"]:
            raise ValueError("Unexpected ONNX output names")
        self.input_size = tuple(input_size)
        self.threshold = float(threshold)
        self.max_per_class = int(max_per_class)
        self.last_run = {}

    def predict(self, rgb: np.ndarray, labels=("wall", "floor", "ceiling")) -> list[dict]:
        rgb = np.asarray(rgb)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise ValueError("Expected original-resolution HWC uint8 RGB")
        labels = [str(label).lower().strip().rstrip(".") for label in labels]
        if not labels or len(labels) != len(set(labels)) or any(not label or "." in label for label in labels):
            raise ValueError("Expected unique English labels without period separators")
        caption = " . ".join(labels) + " ."
        ids, ranges = self.tokenizer.encode(caption)
        if len(ranges) != len(labels) or 100 in ids:
            raise ValueError("Label tokenization did not produce known phrase ranges")
        image = cv2.resize(rgb, self.input_size, interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
        image = (image - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
        inputs = text_inputs(ids)
        inputs["img"] = image.transpose(2, 0, 1)[None].astype(np.float32)
        began = time.perf_counter()
        logits, normalized_boxes = self.session.run(["logits", "boxes"], inputs)
        seconds = time.perf_counter() - began
        if logits.ndim != 3 or normalized_boxes.ndim != 3 or normalized_boxes.shape[-1] != 4:
            raise ValueError(f"Unexpected ONNX output shapes {logits.shape}, {normalized_boxes.shape}")
        # Grounding DINO masks unused token slots with -inf; sigmoid maps them to 0.
        if np.isnan(logits).any() or np.isposinf(logits).any() or not np.isfinite(normalized_boxes).all():
            raise ValueError("Invalid ONNX detection outputs")
        scores = 1.0 / (1.0 + np.exp(-np.clip(logits[0], -80, 80)))
        phrase_scores = np.stack([scores[:, start:end].max(axis=1) for start, end in ranges], axis=1)
        winning = phrase_scores.argmax(axis=1)
        height, width = rgb.shape[:2]
        candidates = []
        for q in np.where(phrase_scores.max(axis=1) > self.threshold)[0]:
            name = labels[int(winning[q])]
            score = float(phrase_scores[q, winning[q]])
            cx, cy, w, h = normalized_boxes[0, q].astype(float)
            xyxy = np.clip([(cx-w/2)*width, (cy-h/2)*height, (cx+w/2)*width, (cy+h/2)*height],
                           [0, 0, 0, 0], [width, height, width, height])
            if not np.isfinite(xyxy).all() or xyxy[2] <= xyxy[0] or xyxy[3] <= xyxy[1]:
                continue
            candidates.append({"class_name": name, "score": score,
                               "xyxy": xyxy.tolist(), "query_index": int(q),
                               "needs_review": True, "label_origin": "dino_box_proposal"})
        candidates.sort(key=lambda item: item["score"], reverse=True)
        results, counts = [], {}
        for item in candidates:
            label = item["class_name"]
            if counts.get(label, 0) >= self.max_per_class:
                continue
            # Suppress nearly identical same-class proposals; preserve separate walls.
            if any(_box_iou(item["xyxy"], old["xyxy"]) > .90 for old in results if old["class_name"] == label):
                continue
            results.append(item)
            counts[label] = counts.get(label, 0) + 1
        self.last_run = {"inference_seconds": seconds, "caption": caption, "token_ids": ids,
                         "phrase_ranges": ranges, "output_shapes": [list(logits.shape), list(normalized_boxes.shape)],
                         "input_size": list(self.input_size), "threshold": self.threshold,
                         "model_path": str(self.model_path), "tokenizer_asset": self.tokenizer.asset,
                         "tokenizer_sha256": self.tokenizer.sha256, "providers": self.session.get_providers(),
                         "method_note": "Maximum token score per supplied phrase; no free phrase decoding; score is not calibrated accuracy",
                         "sources": SOURCES}
        return results


def _box_iou(first, second) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection = max(0., min(ax2, bx2)-max(ax1, bx1)) * max(0., min(ay2, by2)-max(ay1, by1))
    union = (ax2-ax1)*(ay2-ay1)+(bx2-bx1)*(by2-by1)-intersection
    return intersection / union if union > 0 else 0.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output_prefix", type=Path)
    parser.add_argument("--labels", nargs="+", default=["wall", "floor", "ceiling"])
    parser.add_argument("--threshold", type=float, default=.30)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--model-path", "--model", dest="model", type=Path, required=True,
                        help="Path to an existing local Grounding DINO ONNX model")
    parser.add_argument("--tokenizer-path", "--tokenizer", dest="tokenizer", type=Path, required=True,
                        help="Path to an existing local BERT WordPiece tokenizer JSON")
    args = parser.parse_args()
    outputs = [args.output_prefix.with_suffix(".json"), args.output_prefix.with_suffix(".jpg")]
    if any(path.exists() for path in outputs):
        raise FileExistsError("Refusing to overwrite diagnostic results")
    original_hash = hashlib.sha256(args.image.read_bytes()).hexdigest()
    with Image.open(args.image) as image:
        image = image.convert("RGB")
        rgb = np.asarray(image)
    started = time.perf_counter()
    model = GroundingDetector(threads=args.threads, threshold=args.threshold, input_size=(args.width, args.height),
                             model_path=args.model, tokenizer_path=args.tokenizer)
    loaded = time.perf_counter() - started
    predictions = model.predict(rgb, args.labels)
    data = {"source": str(args.image), "source_sha256": original_hash, "image_size": list(image.size),
            "load_seconds": loaded, "boxes": predictions, "run": model.last_run,
            "checked": False, "training_eligible": False}
    if hashlib.sha256(args.image.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError("Original image changed during diagnostic")
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    outputs[0].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    overlay = image.copy()
    drawing = ImageDraw.Draw(overlay)
    colors = {"wall": "cyan", "floor": "lime", "ceiling": "magenta", "person": "yellow"}
    for prediction in predictions:
        color = colors.get(prediction["class_name"], "orange")
        box = prediction["xyxy"]
        drawing.rectangle(box, outline=color, width=5)
        drawing.text((box[0]+3, box[1]+3), f"{prediction['class_name']} {prediction['score']:.2f}", fill=color)
    overlay.thumbnail((1600, 1200))
    overlay.save(outputs[1], quality=92)
    print(json.dumps({"boxes": predictions, "load_seconds": loaded,
                      "inference_seconds": model.last_run["inference_seconds"],
                      "outputs": [str(path) for path in outputs]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
