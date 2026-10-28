"""Torch-free data layer of train_quick: surface layout, export reading, mask merging. Testable without torch."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

N_STATE = 4                       # single surface: not_installed, installed, occluded, not_target
SURFACE_OF_ACTIVITY = {"wall_board_installation": "wall", "ceiling_panel_installation": "ceiling",
                       "floor_finish_installation": "floor"}
SURFACE_SEM = {"wall": (0, 3), "ceiling": (2,), "floor": (1,)}   # semantic ids that may be an untracked instance of a surface


class Surfaces:
    """Which activities one model learns, and the state-class layout: 2 classes per surface + occluded + not_target."""

    def __init__(self, activity: str):
        acts = ["wall_board_installation", "ceiling_panel_installation"] if activity == "all" else [activity]
        self.activities = acts
        self.names = [SURFACE_OF_ACTIVITY.get(a, a) for a in acts]
        self.n_state = 2 * len(acts) + 2
        self.occ = 2 * len(acts)
        self.nt = 2 * len(acts) + 1
        self.class_names = [f"{n}_{st}" if len(acts) > 1 else st
                            for n in self.names for st in ("not_installed", "installed")] + ["occluded", "not_target"]
        own = {i for n in self.names for i in SURFACE_SEM.get(n, ())}
        self.nt_sem_full = tuple(i for i in (1, 2, 4, 5, 6, 7) if i not in own)      # --state-outside full
        self.nt_sem_roi = tuple(i for i in (1, 2) if i not in own)                   # --state-outside roi
        self.activity = activity

    def inst(self, k):
        return 2 * k + 1

    def notinst(self, k):
        return 2 * k


# ------------------------------------------------------------------ data
def _parse_ids(lines):
    """'image_id' or 'image_id|target_id' per line -> {image_id: set(target_ids) or None (= every target)}."""
    out = {}
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        iid, _, tid = ln.partition("|")
        if iid not in out:
            out[iid] = set() if tid else None
        if tid and out[iid] is not None:
            out[iid].add(tid)
    return out


def read_export(export: Path, surfaces: Surfaces, exclude=frozenset(), propagated_weight=0.5, weak_weight=None,
                holdout_ids=None):
    """weak_weight: this export holds WEAK labels (rule / propagation / assistant drafts approved in bulk, not drawn and
    checked photo by photo): every photo is used for training only, with this loss weight, never for val / test.
    holdout_ids: this export is a HELD-OUT city: listed photos -> split 'holdout' (human test, optionally restricted to
    the listed targets), all others -> 'holdout_weak' (secondary test on the weak labels); none train."""
    meta = [json.loads(l) for l in (export / "metadata.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    splits = {}
    for s in ("train", "val", "test"):
        p = export / "splits" / f"{s}.txt"
        splits[s] = set(p.read_text(encoding="utf-8").split()) if p.exists() else set()
    targets = json.loads((export / "targets.json").read_text(encoding="utf-8")) if (export / "targets.json").exists() else {"targets": []}
    tindex = {t["index"]: t["target_id"] for t in targets.get("targets", [])}
    surf_of = {t["index"]: t.get("surface_type", "wall" if str(t.get("target_id", "")).startswith("W") else "")
               for t in targets.get("targets", [])}
    # a target is measured for a surface slot when its activity is not approved not_applicable (export targets.json)
    slot_of = {}
    for t in targets.get("targets", []):
        for k, act in enumerate(surfaces.activities):
            if surf_of[t["index"]] == surfaces.names[k]:
                app = ((t.get("activities") or {}).get(act) or {}).get("applicability")
                if app != "not_applicable":
                    slot_of[t["index"]] = k
    wall_idx = [i for i, n in surf_of.items() if n == "wall"]
    items = []
    for m in meta:
        sid = m["sample_id"]
        if sid in exclude or m.get("image_id") in exclude:
            continue
        split = next((s for s, ids in splits.items() if sid in ids), m.get("split"))
        source, weight, only = m.get("label_source", "human"), 1.0, None
        if weak_weight is not None:
            split, source, weight = "train", "weak", weak_weight     # weak labels: every photo trains, none grades
        elif holdout_ids is not None:
            iid = m.get("image_id", sid)
            if iid in holdout_ids or sid in holdout_ids:
                tids = holdout_ids.get(iid, holdout_ids.get(sid))
                split, source = "holdout", "human"
                only = None if tids is None else {i for i, t in tindex.items() if t in tids}
            else:
                split, source, weight = "holdout_weak", "weak", 1.0
        elif source == "propagated":
            weight = propagated_weight
        if split not in ("train", "val", "test", "holdout", "holdout_weak"):
            continue
        states = [export / "state_masks" / a / f"{sid}.png" for a in surfaces.activities]
        states = [str(p) if p.exists() else None for p in states]
        if all(p is None for p in states):
            continue
        items.append({"id": sid, "split": split, "export": str(export),
                      "image": str(export / m["files"]["image"]), "semantic": str(export / m["files"]["semantic"]),
                      "target": str(export / m["files"]["target"]), "states": states,
                      "visibility": str(export / m["files"]["visibility"]),
                      "date": (m.get("capture") or {}).get("time_local", "")[:10],
                      "time": (m.get("capture") or {}).get("time_local", ""), "tindex": tindex,
                      "wall_idx": wall_idx, "slot_of": slot_of, "only_targets": only,
                      "label_source": source, "weight": weight})
    return items


def load_item(it, surfaces: Surfaces):
    """img, sem, raw state (0/1 per surface slot -> 2k / 2k+1, 255 elsewhere), visibility, target index map."""
    img = np.asarray(Image.open(it["image"]).convert("RGB"), dtype=np.uint8)
    sem = np.asarray(Image.open(it["semantic"]), dtype=np.uint8)
    vis = np.asarray(Image.open(it["visibility"]), dtype=np.uint8)
    tg = np.asarray(Image.open(it["target"]), dtype=np.uint8)
    st = np.full(tg.shape, 255, np.uint8)
    for k, path in enumerate(it["states"]):
        if path is None:
            continue
        raw = np.asarray(Image.open(path), dtype=np.uint8)
        # only pixels of targets measured for this surface (a not_applicable ceiling in one export stays 255)
        own = np.isin(tg, [i for i, kk in it["slot_of"].items() if kk == k])
        st[own & (raw == 0)] = surfaces.notinst(k)
        st[own & (raw == 1)] = surfaces.inst(k)
    if it.get("only_targets") is not None:                    # held-out photo: grade only the human-labelled targets
        st[(tg != 0) & (tg != 255) & ~np.isin(tg, list(it["only_targets"]))] = 255
    return img, sem, st, vis, tg


def state_target(st, vis, sem, tg, it, surfaces: Surfaces, outside="full"):
    """State target (see module doc). tg: 0 none, k target index, 255 ignore/padding."""
    t = st.copy()
    in_roi = np.isin(tg, list(it["slot_of"].keys()))
    if it.get("only_targets") is not None:
        in_roi &= np.isin(tg, list(it["only_targets"]))
    t[(st == 255) & (vis == 2) & in_roi] = surfaces.occ
    nt_sem = surfaces.nt_sem_roi if outside == "roi" else surfaces.nt_sem_full
    t[(st == 255) & ~in_roi & (tg != 255) & np.isin(sem, nt_sem)] = surfaces.nt
    return t


