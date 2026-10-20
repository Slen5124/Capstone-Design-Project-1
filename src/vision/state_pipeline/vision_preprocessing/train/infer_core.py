"""Torch-free part of inference: ROI proposal, installed ratio, daily rows, events, vision_output JSON.

Inputs are per-photo state maps from the model (uint8: 0 not_installed, 1 installed, 2 occluded, 3 not_target)
at export size. Everything here is plain numpy so it can be tested without a GPU.

Rules (same meaning as vision_output/1.0, see docs/AI_HANDOFF.md and the agent guide):
    installed_ratio   = installed / (installed + not_installed) inside the ROI      (occluded pixels left out)
    observed_fraction = (installed + not_installed) / ROI pixels                      (< 0.3 -> held, ratio null)
    held values are never filled with 0 or carried forward.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date as Date, datetime, timezone

import numpy as np

SCHEMA = "vision_output/1.0"
RULES = {
    "held_below_observed": 0.30,      # observed_fraction below this -> held
    "low_confidence_below": 0.60,     # mean max-softmax inside the ROI below this -> low_confidence
    "min_roi_px": 500,
    "increase_min": 0.02,             # delta >= this -> increase
    "decrease_suspect": -0.05,        # delta <= this -> decrease_suspect (more likely an error than removal)
    "large_increase": 0.30,           # one-day jump -> event
    "complete_ratio": 0.98,           # >= this on 2 consecutive observation days -> installation_complete
    "complete_days": 2,
    "long_gap_days": 3,               # more than this many days without an observation -> long_gap
    "roi_vote": 0.5,                  # pixel kept if measurable (0/1/2) in >= this share of the ROI photos
    "roi_min_frac": 0.01,             # ROI component smaller than this share of the image is dropped
    "roi_close_px": 9,                # morphological closing / opening size (pixels at export size)
}
DEFAULT_TIME_RE = r"-(?P<date>\d{8})-(?P<time>\d{6})"
MEASURABLE = (0, 1, 2)


# ------------------------------------------------------------------ time
def capture_time(name: str, regex: str = DEFAULT_TIME_RE):
    """Camera-local capture time from the file name (time zone unknown, same as preprocessing)."""
    m = re.search(regex, name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("date") + m.group("time"), "%Y%m%d%H%M%S")
    except ValueError:
        return None


# ------------------------------------------------------------------ ROI
def _morph(mask: np.ndarray, k: int) -> np.ndarray:
    try:
        import cv2
    except ImportError:            # no OpenCV: keep the raw vote
        return mask
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, ker)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, ker).astype(bool)


def _components(mask: np.ndarray):
    try:
        import cv2
        n, lab, stats, cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        return [(lab == i, int(stats[i, 4]), float(cent[i, 0])) for i in range(1, n)]
    except ImportError:            # no OpenCV: one region
        return [(mask, int(mask.sum()), float(np.nonzero(mask)[1].mean()) if mask.any() else 0.0)]


def propose_roi(state_maps: list[np.ndarray], rules: dict = RULES, prefix: str = "A"):
    """ROI proposal from the first few good photos of a view.

    A pixel is in the ROI if the model calls it measurable wall (not_installed / installed / occluded) in at least
    `roi_vote` of the photos. Separate regions become separate targets A01, A02 ... (left to right). These are
    PROVISIONAL ids - a person maps them to W01, W02 ... once (review app), then the ROI is fixed.
    Returns (roi_mask uint8 [0 none, k = target k], targets list)."""
    if not state_maps:
        raise ValueError("no photos for the ROI proposal")
    votes = np.mean([np.isin(s, MEASURABLE) for s in state_maps], axis=0)
    cand = _morph(votes >= rules["roi_vote"], rules["roi_close_px"])
    h, w = cand.shape
    comps = [c for c in _components(cand) if c[1] >= rules["roi_min_frac"] * h * w]
    comps.sort(key=lambda c: c[2])
    roi = np.zeros((h, w), np.uint8)
    targets = []
    for k, (m, area, cx) in enumerate(comps[:250], start=1):
        roi[m] = k
        ys, xs = np.nonzero(m)
        targets.append({"index": k, "target_id": f"{prefix}{k:02d}", "surface_type": "wall", "roi_status": "provisional",
                        "area_px": area, "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]})
    return roi, targets


# ------------------------------------------------------------------ per photo
def measure(state: np.ndarray, roi: np.ndarray, targets: list[dict], conf: np.ndarray | None = None,
            rules: dict = RULES) -> list[dict]:
    """One row per target for one photo."""
    rows = []
    for t in targets:
        r = roi == t["index"]
        n = int(r.sum())
        n0, n1, n2 = (int(((state == v) & r).sum()) for v in (0, 1, 2))
        obs = (n0 + n1) / n if n else 0.0
        row = {"target_id": t["target_id"], "roi_px": n, "not_installed_px": n0, "installed_px": n1, "occluded_px": n2,
               "observed_fraction": round(obs, 4), "installed_ratio": None, "status": "held", "hold_reason": []}
        if n < rules["min_roi_px"]:
            row["hold_reason"].append("roi_too_small")
        elif obs < rules["held_below_observed"]:
            row["hold_reason"].append(f"observed_fraction<{rules['held_below_observed']}")
        else:
            row["installed_ratio"] = round(n1 / (n0 + n1), 4)
            row["status"] = "ok"
        if conf is not None and n:
            mc = float(conf[r].mean())
            row["mean_confidence"] = round(mc, 4)
            if row["status"] == "ok" and mc < rules["low_confidence_below"]:
                row["status"] = "low_confidence"
        row["hold_reason"] = row["hold_reason"] or None
        rows.append(row)
    return rows


# ------------------------------------------------------------------ per day
def daily_rows(photo_rows: list[dict], site_id: str, activity: str, view_id: str | None, provisional: bool,
               rules: dict = RULES) -> list[dict]:
    """photo_rows: {date, photo, target_id, installed_ratio, observed_fraction, status, evidence?, mean_confidence?}.
    One row per (date, target): median of the usable photos; previous value / delta / change."""
    by = defaultdict(list)
    for r in photo_rows:
        by[(r["target_id"], r["date"])].append(r)
    out = []
    for tid in sorted({k[0] for k in by}):
        prev = None
        for d in sorted(k[1] for k in by if k[0] == tid):
            rs = by[(tid, d)]
            ok = [r for r in rs if r["status"] in ("ok", "low_confidence") and r["installed_ratio"] is not None]
            vals = [r["installed_ratio"] for r in ok]
            confs = [r["mean_confidence"] for r in rs if r.get("mean_confidence") is not None]
            row = {"date": d, "site_id": site_id, "view_ids": [view_id] if view_id else [], "target_id": tid,
                   "activity_id": activity, "source": "model", "revision": 1, "provisional": provisional,
                   "status": "held", "hold_reason": None, "installed_ratio": None, "installed_ratio_min": None,
                   "installed_ratio_max": None,
                   "observed_fraction": round(float(np.mean([r["observed_fraction"] for r in rs])), 4),
                   "mean_confidence": round(float(np.mean(confs)), 4) if confs else None,
                   "photos": len(rs), "photos_ok": len(ok), "previous_date": None, "previous_ratio": None,
                   "delta": None, "change": "held", "days_since_previous": None,
                   "evidence": [r["evidence"] for r in rs if r.get("evidence")][:3]}
            if ok:
                row.update(installed_ratio=round(float(np.median(vals)), 4), installed_ratio_min=round(min(vals), 4),
                           installed_ratio_max=round(max(vals), 4),
                           status="low_confidence" if all(r["status"] == "low_confidence" for r in ok) else "ok")
                if prev is None:
                    row["change"] = "first_value"
                else:
                    delta = round(row["installed_ratio"] - prev["installed_ratio"], 4)
                    row.update(previous_date=prev["date"], previous_ratio=prev["installed_ratio"], delta=delta,
                               days_since_previous=(Date.fromisoformat(d) - Date.fromisoformat(prev["date"])).days,
                               change="increase" if delta >= rules["increase_min"]
                               else "decrease_suspect" if delta <= rules["decrease_suspect"] else "no_change")
                prev = row
            else:
                reasons = sorted({h for r in rs for h in (r.get("hold_reason") or [])})
                row["hold_reason"] = reasons or ["no_usable_photo"]
            out.append(row)
    return sorted(out, key=lambda r: (r["date"], r["target_id"]))


def events(daily: list[dict], rules: dict = RULES) -> list[dict]:
    ev = []
    by_t = defaultdict(list)
    for r in daily:
        by_t[r["target_id"]].append(r)
    for tid, rs in by_t.items():
        rs = sorted(rs, key=lambda r: r["date"])
        good = [r for r in rs if r["installed_ratio"] is not None]
        run, done = 0, False
        for r in good:
            run = run + 1 if r["installed_ratio"] >= rules["complete_ratio"] else 0
            if run >= rules["complete_days"] and not done:
                ev.append({"date": r["date"], "site_id": r["site_id"], "target_id": tid, "type": "installation_complete",
                           "detail": f"installed_ratio >= {rules['complete_ratio']} on {rules['complete_days']} consecutive observation days",
                           "next_step": "paint_check"})
                done = True
            d = r.get("delta")
            if d is not None and d >= rules["large_increase"]:
                ev.append({"date": r["date"], "site_id": r["site_id"], "target_id": tid, "type": "large_increase",
                           "detail": f"installed_ratio {d:+.2f} since {r['previous_date']}", "next_step": "none"})
            if r.get("change") == "decrease_suspect":
                ev.append({"date": r["date"], "site_id": r["site_id"], "target_id": tid, "type": "decrease_suspect",
                           "detail": f"installed_ratio {d:+.2f} since {r['previous_date']}", "next_step": "human_check"})
            if (r.get("days_since_previous") or 0) > rules["long_gap_days"]:
                ev.append({"date": r["date"], "site_id": r["site_id"], "target_id": tid, "type": "long_gap",
                           "detail": f"{r['days_since_previous']} days without a usable observation (no interpolation)",
                           "next_step": "none"})
    return sorted(ev, key=lambda e: (e["date"], e["target_id"], e["type"]))


def vision_output(site_id: str, daily: list[dict], targets: list[dict], view: dict, model_info: dict,
                  rules: dict = RULES) -> dict:
    return {
        "schema_version": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "site_id": site_id,
        "model": model_info,
        "semantics": {
            "installed_ratio": "share of the observable part of the target ROI (pixels) where wall board is installed; "
                               "NOT area, NOT store-wide progress, NOT schedule progress",
            "observed_fraction": f"share of the ROI that was visible (not occluded); below {rules['held_below_observed']} the day is held",
            "status": {"ok": "value usable", "held": "no value; do not fill with 0 or carry forward",
                       "low_confidence": "value present but uncertain"},
            "change": ["first_value", "increase", "no_change", "decrease_suspect", "held"],
            "provisional": "true while the ROI is an automatic proposal (target ids A01.. not yet mapped to W01..)",
            "rules": rules},
        "targets": [{"target_id": t["target_id"], "surface_type": t.get("surface_type", "wall"),
                     "roi_status": t.get("roi_status"), "schedule_link": None} for t in targets],
        "views": [view],
        "daily_progress": daily,
        "events": events(daily, rules),
        "warnings": (["ROI is an automatic proposal: map A01.. to the site's target ids and confirm it once"]
                     if any(t.get("roi_status") == "provisional" for t in targets) else []),
    }


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
