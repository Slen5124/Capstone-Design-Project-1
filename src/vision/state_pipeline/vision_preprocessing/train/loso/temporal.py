"""Causal (real-time) monotone correction of the installed ratio: a finished board stays finished, so the reported
ratio of a wall should not go down - but only PAST predictions may be used (the value of day t is fixed on day t).

For an increasing isotonic fit of y_1..y_t (weights w), the fitted LAST value is  max_{j<=t} sum_{j..t}(w y) / sum_{j..t}(w).
It is never below the day's own prediction (the correction only lifts), but the reported series itself can step down
when a low day is pooled with the days before it - it is the best monotone reading of the history, not a running max.

Variants (protocol temporal.variants), all on the photo rows of a run (photo x wall: date, view, prediction, visible
share, confidence):
    raw                  date x wall mean of the photo predictions (the official metric)
    causal               the causal fit per wall over the date means (earlier method)
    causal_view          causal fit per (camera view, wall); a day's views are combined weighted by the predicted
                         visible share (different views see different parts of a wall, so their ratios are not one series)
    causal_view_w        same, the fit weighted by visible share x confidence of the photos
    causal_view_w_reset  same + restart: if a series falls more than reset_drop below its fitted level on reset_days
                         consecutive dates (demolition / rework), the fit restarts there and the date is flagged"""
from __future__ import annotations

from collections import defaultdict


def causal_last(values, weights=None):
    """fitted last value of the increasing isotonic regression of every prefix -> list (same length)."""
    w = weights or [1.0] * len(values)
    out = []
    for t in range(len(values)):
        best, sw, swy = None, 0.0, 0.0
        for j in range(t, -1, -1):
            sw += w[j]
            swy += w[j] * values[j]
            m = swy / sw if sw > 0 else values[j]
            best = m if best is None or m > best else best
        out.append(best)
    return out


def causal_with_reset(values, weights=None, drop=0.2, days=2):
    """causal_last with restarts. -> (fitted list, indices where a restart began)."""
    w = weights or [1.0] * len(values)
    out, events, start, low = [], [], 0, 0
    for t in range(len(values)):
        if out:
            low = low + 1 if values[t] < out[-1] - drop else 0
            if low >= days:
                start = t - days + 1
                events.append(start)
                low = 0
        seg = causal_last(values[start:t + 1], w[start:t + 1])
        out.append(seg[-1])
    return out, events


def _f(v):
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


def daily_truth(rows):
    """(surface, target, date) -> mean ground-truth ratio of the photos of that day."""
    acc = defaultdict(list)
    for r in rows:
        g = _f(r.get("gt_ratio"))
        if g is not None:
            acc[(r["surface"], r["target"], r["date"])].append(g)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _fill(pred: dict, rows) -> dict:
    """date x wall rows that have a label but no prediction (the model saw the whole ROI as occluded / not a target)
    get the wall's previous reported value - what a running system would show - or 0 before the first one. Every model
    is then scored on the same rows; how many were filled is reported as coverage (direct_keys)."""
    keys = {(r["surface"], r["target"], r["date"]) for r in rows if _f(r.get("gt_ratio")) is not None}
    by = defaultdict(list)
    for k in keys:
        by[k[:2]].append(k[2])
    out = dict(pred)
    for (s, t), ds in by.items():
        last = None
        for d in sorted(ds):
            k = (s, t, d)
            if k in pred:
                last = pred[k]
            else:
                out[k] = last if last is not None else 0.0
    return out


def direct_keys(rows) -> set:
    """date x wall rows with at least one photo prediction."""
    return {(r["surface"], r["target"], r["date"]) for r in rows if _f(r.get("roi_model")) is not None}


def smooth(rows, variant: str, drop=0.2, days=2):
    """-> ({(surface, target, date): predicted ratio} for every labelled row, [events (surface, target, view, date)])."""
    pred, events = _smooth(rows, variant, drop, days)
    return _fill(pred, rows), events


def _smooth(rows, variant: str, drop=0.2, days=2):
    preds = [(r, _f(r.get("roi_model"))) for r in rows]
    preds = [(r, p) for r, p in preds if p is not None]
    events = []
    if variant in ("raw", "causal"):
        acc = defaultdict(list)
        for r, p in preds:
            acc[(r["surface"], r["target"], r["date"])].append(p)
        day = {k: sum(v) / len(v) for k, v in acc.items()}
        if variant == "raw":
            return day, events
        out, by = {}, defaultdict(list)
        for (s, t, d), v in day.items():
            by[(s, t)].append((d, v))
        for (s, t), ser in by.items():
            ser.sort()
            fit = causal_last([v for _, v in ser])
            for (d, _), y in zip(ser, fit):
                out[(s, t, d)] = y
        return out, events
    weighted = variant in ("causal_view_w", "causal_view_w_reset")
    reset = variant == "causal_view_w_reset"
    if variant not in ("causal_view", "causal_view_w", "causal_view_w_reset"):
        raise ValueError(f"unknown temporal variant {variant}")
    acc = defaultdict(lambda: {"p": [], "obs": [], "w": []})
    for r, p in preds:
        obs = _f(r.get("pred_observed"))
        obs = 1.0 if obs is None else obs
        conf = _f(r.get("conf"))
        conf = 1.0 if conf is None else conf
        a = acc[(r["surface"], r["target"], r.get("view", ""), r["date"])]
        a["p"].append(p)
        a["obs"].append(obs)
        a["w"].append(max(obs * conf, 1e-6) if weighted else 1.0)
    series = defaultdict(list)
    for (s, t, v, d), a in acc.items():
        sw = sum(a["w"])
        y = sum(w * p for w, p in zip(a["w"], a["p"])) / sw
        series[(s, t, v)].append((d, y, sw, sum(a["obs"]) / len(a["obs"])))
    comb = defaultdict(lambda: [0.0, 0.0])
    for (s, t, v), ser in series.items():
        ser.sort()
        ys, ws = [x[1] for x in ser], [x[2] for x in ser]
        if reset:
            fit, ev = causal_with_reset(ys, ws, drop, days)
            events += [(s, t, v, ser[i][0]) for i in ev]
        else:
            fit = causal_last(ys, ws)
        for (d, _, _, obs), y in zip(ser, fit):
            c = comb[(s, t, d)]
            cw = max(obs, 1e-6)
            c[0] += cw * y
            c[1] += cw
    return {k: v[0] / v[1] for k, v in comb.items()}, events
