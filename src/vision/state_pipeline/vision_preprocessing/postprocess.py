"""Post-processing link: state masks (labels or model predictions) -> per target ratios -> before/after.

The ratio reported is the *installed share within the observable image area of
the target ROI*. It is not a real-area progress value (no perspective
correction, no activity weighting, no store-wide aggregation).

Inputs are export-space masks (same transform as the exported image): a state
mask (0/1/255; a model prediction may contain 0/1 everywhere), the exported
target mask and visibility mask. Observable = inside target ROI, visibility==1
(not occluded/undeterminable) and state != 255.

Comparison applies the same model/label source independently to two photos;
it never carries an earlier state into a later photo. Pairs are held when
either side is held, the target mapping differs or the view differs.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .rasterize import load_mask_png
from .runctx import atomic_write_json, read_jsonl


def measure_sample(state: np.ndarray, target: np.ndarray, visibility: np.ndarray, targets: list[dict],
                   values: dict, min_observed_fraction: float = 0.3, min_roi_px: int = 200) -> list[dict]:
    out = []
    inst_v, not_v = values["installed"], values["not_installed"]
    for t in targets:
        roi = target == t["index"]
        roi_px = int(roi.sum())
        rec = {"target_id": t["target_id"], "roi_px": roi_px}
        if roi_px < min_roi_px:
            rec.update(status="held", reason="roi_not_in_view_or_too_small")
            out.append(rec)
            continue
        observable = roi & (visibility == 1) & (state != 255)
        inst = int((observable & (state == inst_v)).sum())
        noti = int((observable & (state == not_v)).sum())
        obs_px = inst + noti
        frac = obs_px / roi_px
        rec.update(observable_px=obs_px, installed_px=inst, not_installed_px=noti,
                   occluded_px=int((roi & (visibility == 2)).sum()), undeterminable_px=int((roi & (visibility == 3)).sum()),
                   observed_fraction=round(frac, 4),
                   installed_ratio_within_observed=round(inst / obs_px, 4) if obs_px else None)
        if frac < min_observed_fraction:
            rec.update(status="held", reason=f"observed_fraction<{min_observed_fraction}")
        else:
            rec["status"] = "ok"
        out.append(rec)
    return out


def measure_export(export_dir: Path, activity: str, pred_dir: Path | None = None, min_observed_fraction: float = 0.3) -> Path:
    export_dir = Path(export_dir)
    lm = json.loads((export_dir / "label_map.json").read_text(encoding="utf-8"))
    targets = json.loads((export_dir / "targets.json").read_text(encoding="utf-8"))["targets"]
    values = lm["state"]["values"]
    rows = []
    for meta in read_jsonl(export_dir / "metadata.jsonl"):
        sid = meta["sample_id"]
        if pred_dir is not None:
            path = Path(pred_dir) / f"{sid}.png"
            source = "prediction"
        else:
            path = export_dir / meta["activities"][activity]["file"]
            source = "label" + ("" if meta["training_ready"] else "_DRAFT")
        if not path.exists():
            continue
        state = load_mask_png(path)
        target = load_mask_png(export_dir / meta["files"]["target"])
        vis = load_mask_png(export_dir / meta["files"]["visibility"])
        relevant = [t for t in targets if activity in t["activities"]]
        for m in measure_sample(state, target, vis, relevant, values, min_observed_fraction):
            rows.append({"sample_id": sid, "image_id": meta["image_id"], "time_local": meta["capture"]["time_local"],
                         "timezone_confirmed": meta["capture"]["timezone_confirmed"], "view_id": meta.get("view_id"),
                         "activity_id": activity, "state_source": source, **m,
                         "ratio_semantics": lm.get("ratio_semantics")})
    out = export_dir / f"measurements_{activity}{'_pred' if pred_dir else ''}.jsonl"
    with open(out, "w", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out


def compare_measurements(path: Path) -> Path:
    """Consecutive comparable pairs per (target, activity) in time order."""
    by = defaultdict(list)
    for r in read_jsonl(Path(path)):
        by[(r["target_id"], r["activity_id"])].append(r)
    pairs = []
    for (tid, act), rows in by.items():
        rows.sort(key=lambda r: r["time_local"] or "")
        for a, b in zip(rows, rows[1:]):
            reasons = []
            if a["status"] != "ok" or b["status"] != "ok":
                reasons.append("one_side_held")
            if a.get("view_id") != b.get("view_id"):
                reasons.append("different_view_check_roi_correspondence")
            if a["state_source"] != b["state_source"]:
                reasons.append("different_state_source")
            pair = {"target_id": tid, "activity_id": act, "before": a["sample_id"], "after": b["sample_id"],
                    "before_time": a["time_local"], "after_time": b["time_local"],
                    "status": "held" if reasons else "ok", "reasons": reasons}
            if not reasons:
                pair["delta_installed_ratio_within_observed"] = round(
                    b["installed_ratio_within_observed"] - a["installed_ratio_within_observed"], 4)
                pair["observed_fraction"] = [a["observed_fraction"], b["observed_fraction"]]
            pairs.append(pair)
    out = Path(path).with_name(Path(path).stem + "_pairs.jsonl")
    with open(out, "w", encoding="utf-8") as handle:
        for p in pairs:
            handle.write(json.dumps(p, ensure_ascii=False) + "\n")
    return out


def daily_progress(export_dir: Path, change_threshold: float = 0.05) -> dict:
    """Day-level table: per (date, target, activity) the median installed ratio of the day's usable photos
    and the change against the previous day that had a usable value ("전날 대비").

    Only status=ok photos count. A comparison is held (not computed) when either day has no usable photo,
    and flagged when the two days are seen from different camera views (different ROI shape).
    A decrease is reported as 'decrease_check' because installation normally does not go backwards.
    """
    import csv
    import statistics
    export_dir = Path(export_dir)
    rows = []
    for f in sorted(export_dir.glob("measurements_*.jsonl")):
        if f.stem.endswith("_pairs") or f.stem.endswith("_pred"):
            continue
        rows += list(read_jsonl(f))
    # only (target, activity) pairs that are applicable: e.g. a floor that was finished before the camera
    # period (not_applicable) has no progress to report
    tpath = export_dir / "targets.json"
    if tpath.exists():
        applicable = {(t["target_id"], a) for t in json.loads(tpath.read_text(encoding="utf-8"))["targets"]
                      for a, v in (t.get("activities") or {}).items() if v.get("applicability") == "applicable"}
        rows = [r for r in rows if (r["target_id"], r["activity_id"]) in applicable]
    by = defaultdict(list)
    for r in rows:
        by[((r["time_local"] or "")[:10], r["target_id"], r["activity_id"])].append(r)
    days = []
    for (d, tid, act), rs in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        ok = [r for r in rs if r.get("status") == "ok" and r.get("installed_ratio_within_observed") is not None]
        vals = [r["installed_ratio_within_observed"] for r in ok]
        views = sorted({r.get("view_id") for r in rs if r.get("view_id")})
        days.append({"date": d, "target_id": tid, "activity_id": act, "view_ids": views,
                     "status": "ok" if ok else "held",
                     "hold_reason": None if ok else sorted({r.get("reason") for r in rs if r.get("reason")}),
                     "installed_ratio": round(statistics.median(vals), 4) if vals else None,
                     "installed_ratio_min": round(min(vals), 4) if vals else None,
                     "installed_ratio_max": round(max(vals), 4) if vals else None,
                     "observed_fraction": round(statistics.median([r["observed_fraction"] for r in ok]), 4) if ok else None,
                     "photos": len(rs), "photos_ok": len(ok),
                     "state_source": sorted({r.get("state_source") for r in rs})})
    last = {}
    for row in days:
        key = (row["target_id"], row["activity_id"])
        prev = last.get(key)
        if row["installed_ratio"] is None:
            row.update(change="held", change_reason="no usable photo this day", previous_date=prev["date"] if prev else None)
        elif prev is None:
            row.update(change="first_value", previous_date=None)
        else:
            delta = round(row["installed_ratio"] - prev["installed_ratio"], 4)
            change = ("increase" if delta > change_threshold else "decrease_check" if delta < -change_threshold
                      else "no_change")
            row.update(previous_date=prev["date"], previous_ratio=prev["installed_ratio"], delta=delta, change=change,
                       days_since_previous=None)
            from datetime import date
            row["days_since_previous"] = (date.fromisoformat(row["date"]) - date.fromisoformat(prev["date"])).days
            if set(row["view_ids"]) != set(prev["view_ids"]):
                row["change_reason"] = "camera view changed between the two days (different ROI shape): compare with care"
        if row["installed_ratio"] is not None:
            last[key] = row
    out = {"change_threshold": change_threshold,
           "ratio_semantics": "installed share within the observable part of the target ROI (pixels); "
                              "not m2 progress, not store-wide progress",
           "rows": days}
    (export_dir / "daily_progress.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["date", "target_id", "activity_id", "status", "installed_ratio", "previous_date", "previous_ratio", "delta",
            "change", "change_reason", "days_since_previous", "installed_ratio_min", "installed_ratio_max",
            "observed_fraction", "photos", "photos_ok", "view_ids"]
    with open(export_dir / "daily_progress.csv", "w", newline="", encoding="utf-8-sig") as h:
        w = csv.writer(h)
        w.writerow(cols)
        for r in sorted(days, key=lambda r: (r["date"], r["target_id"])):
            w.writerow(["|".join(r[c]) if c == "view_ids" else r.get(c, "") for c in cols])
    return {"rows": len(days), "changes": sum(1 for r in days if r.get("change") in ("increase", "decrease_check")),
            "file": str(export_dir / "daily_progress.json")}
