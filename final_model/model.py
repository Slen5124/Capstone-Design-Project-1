"""Reuse the research model implementation, with a strict final-model checkpoint contract."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src/vision/state_pipeline"))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class Predictor:
    def __init__(self, checkpoint, config, device="auto"):
        # An approved, locally cached encoder is required; this entry point never downloads weights.
        os.environ["HF_HUB_OFFLINE"] = "1"
        import torch
        import timm
        from vision_preprocessing.train.train_quick import TwoHeadSeg, MEAN, STD
        from layout import classes_for
        state_classes = classes_for(config)

        ck = torch.load(checkpoint, map_location="cpu", weights_only=True)
        for key in ("encoder", "decoder", "n_sem", "n_state", "export_size"):
            if ck.get(key) != config[key]:
                raise ValueError(f"Checkpoint/config mismatch: {key}")
        expected_activity = "all_surfaces_v2" if config["n_state"] == 8 else "all"
        if ck.get("state_classes") != state_classes or ck.get("activity") != expected_activity:
            raise ValueError("Checkpoint class order/activity does not match the selected layout")
        if ck.get("vit_pad_crop") is not True:
            raise ValueError("Final pipeline requires the pad/crop-corrected checkpoint")
        input_config = ck.get("input", {})
        if (input_config.get("rgb_only") is not True or
                not np.allclose(input_config.get("mean", []), MEAN.flatten().numpy()) or
                not np.allclose(input_config.get("std", []), STD.flatten().numpy())):
            raise ValueError("Checkpoint input normalization mismatch")
        full_encoder = any(k.startswith("encoder.") for k in ck["state_dict"])
        self.model = TwoHeadSeg(ck["encoder"], ck["decoder"], pretrained=not full_encoder,
                                freeze=True, n_state=config["n_state"], vit_pad_crop=True)
        missing, unexpected = self.model.load_state_dict(ck["state_dict"], strict=False)
        bad = missing if full_encoder else [k for k in missing if not k.startswith("encoder.")]
        if bad or unexpected:
            raise ValueError(f"Checkpoint weights mismatch: {(bad + list(unexpected))[:5]}")
        encoder_hash = hashlib.sha256()
        for key, value in sorted(self.model.encoder.state_dict().items()):
            encoder_hash.update(key.encode())
            encoder_hash.update(str((tuple(value.shape), value.dtype)).encode())
            encoder_hash.update(value.detach().cpu().contiguous().numpy().tobytes())
        if ck.get("encoder_state_sha256") and ck["encoder_state_sha256"] != encoder_hash.hexdigest():
            raise ValueError("Cached encoder weights differ from the weights used during training")
        self.device = torch.device("cuda" if device == "auto" and torch.cuda.is_available()
                                   else "cpu" if device == "auto" else device)
        self.model.to(self.device).eval()
        self.mean, self.std = MEAN, STD
        self.size = ck["export_size"]
        self.info = {"encoder": ck["encoder"], "decoder": ck["decoder"],
                     "checkpoint_sha256": sha256_file(checkpoint),
                     "encoder_state_sha256": encoder_hash.hexdigest(),
                     "fold": ck.get("fold"), "seed": ck.get("seed"), "epoch": ck.get("epoch"),
                     "protocol_version": ck.get("protocol_version"),
                     "torch": torch.__version__, "timm": timm.__version__,
                     "device": str(self.device), "precision": "cuda_amp_fp16" if self.device.type == "cuda" else "fp32",
                     "state_classes": state_classes, "training_scope": ck.get("training_scope", "legacy_research")}

    def predict(self, image):
        import torch
        from vision_preprocessing.rasterize import apply_image, make_transform

        transform = make_transform(image.size, self.size, "letterbox")
        rgb = np.array(apply_image(image.convert("RGB"), transform), dtype=np.uint8)
        tensor = (torch.from_numpy(rgb).permute(2, 0, 1).float() / 255 - self.mean) / self.std
        with torch.inference_mode(), torch.autocast(device_type=self.device.type,
                                                  enabled=self.device.type == "cuda"):
            semantic_logits, state_logits = self.model(tensor[None].to(self.device))
        probs = torch.softmax(state_logits.float(), 1)[0]
        confidence, state = probs.max(0)
        state = state.cpu().numpy().astype(np.uint8)
        confidence = confidence.cpu().numpy()
        invalid = (~torch.isfinite(probs).all(0)).cpu().numpy()
        state[invalid], confidence[invalid] = 255, np.nan
        semantic = semantic_logits[0].argmax(0).cpu().numpy().astype(np.uint8)
        semantic[(~torch.isfinite(semantic_logits[0]).all(0)).cpu().numpy()] = 255
        return rgb, semantic, state, confidence, transform
