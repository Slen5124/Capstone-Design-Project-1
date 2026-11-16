"""Explicit real-weight smoke test using only generated pixels; NOT an accuracy evaluation."""
import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(42)
    Image.fromarray(rng.integers(60, 180, (360, 640, 3), dtype=np.uint8)).save(args.work / "synthetic.png")
    roi = np.zeros((360, 640), np.uint8)
    roi[30:150, 30:610] = 2
    roi[180:330, 30:610] = 1
    config = run.read_json(args.config)
    if config["n_state"] == 8:
        roi[260:330, 30:610] = 3
    Image.fromarray(roi).save(args.work / "roi.png")
    doc = run.read_json(ROOT / "input.example.json")
    if config["n_state"] == 6:
        doc["targets"] = [t for t in doc["targets"] if t["surface_type"] != "floor"]
    doc.update(site_code="SYNTHETIC_ONLY", view_confirmed=True, source_wh=[640, 360], roi_mask="roi.png")
    for target in doc["targets"]:
        target["roi_confirmed"] = True
    doc["photos"][0]["path"] = "synthetic.png"
    run.write_json(args.work / "input.local.json", doc)
    run.main(["--config", str(args.config), "--input", str(args.work / "input.local.json"), "--checkpoint", str(args.checkpoint),
              "--out", str(args.work / "result"), "--device", args.device])
    result = run.read_json(args.work / "result/vision_result.json")
    assert result["run_status"] == "complete"
    surfaces = {"wall", "ceiling", "floor"} if config["n_state"] == 8 else {"wall", "ceiling"}
    assert {r["surface_type"] for r in result["observations"]} == surfaces
    assert len(result["observations"]) == len(surfaces)
    for row in result["observations"]:
        assert (args.work / "result" / row["overlay"]).is_file()
    run.write_json(args.work / "smoke_summary.json", {
        "passed": True, "real_checkpoint_loaded": True, "image": "generated_noise_only",
        "observations": len(result["observations"]), "accuracy_evaluated": False,
        "backend_sent": False})
    print("PASS: real-weight execution and saved artifacts; no field accuracy claim.")


if __name__ == "__main__":
    main()
