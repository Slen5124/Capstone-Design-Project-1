"""Leave-one-site-out folds (protocol.json "split"), written once into the PRIVATE bundle.

    test   = every human-labelled photo of the held-out site; sites in split.test_one_per_date keep one photo per date
             (fixed seed). Id-list sites (human labels on some targets only) are graded on the listed targets.
    val    = human photos of the training-side val sites, by DATE BLOCKS: block = (date - first date) // val_block_days,
             one residue class of block % val_every_blocks (fixed seed) is val. Photos within buffer_days of a val date
             (and not val) are dropped from training, and so are propagated photos on val / buffer dates.
             Id-list sites are never val: their weak labels are copies of their human labels.
    train  = the other human photos of the val sites (x human_repeat at training time) + propagated photos (filtered as
             above) + weak photos of the training-side full-export sites (every weak_every-th by sample id).
    nothing of the held-out site is used for training.

The bundle holds photo ids (they contain real site names) -> it is shared privately, never committed. Only digests go
into the repository (expected_digests.json), so anyone can prove their bundle and data are the ones we used."""
from __future__ import annotations

import datetime as dt
import json
import random
import shutil
import time
from pathlib import Path

from .common import (EXPECTED_DIGESTS_FILE, export_fingerprint, ids_digest, json_digest, load_protocol, log, read_json,
                     write_json)


def read_meta(export: Path) -> list[dict]:
    """photos of an export with the same filters as data_prep.read_export (split train / val / test, at least one state
    mask), so every id in the bundle can be loaded by the trainer."""
    import os
    export = Path(export)
    split_ids = {}
    for sp in ("train", "val", "test"):
        f = export / "splits" / f"{sp}.txt"
        if f.exists():
            for i in f.read_text(encoding="utf-8").split():
                split_ids[i] = sp
    masks = set()
    sm = export / "state_masks"
    if sm.is_dir():
        for d in sm.iterdir():
            if d.is_dir():
                masks |= {n[:-4] for n in os.listdir(d) if n.endswith(".png")}
    rows = []
    for line in (export / "metadata.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        if m.get("kind", "full") != "full":
            continue
        sid = m["sample_id"]
        if split_ids.get(sid, m.get("split")) not in ("train", "val", "test") or sid not in masks:
            continue
        t = (m.get("capture") or {}).get("time_local", "")
        rows.append({"id": sid, "image_id": m.get("image_id", sid), "date": t[:10], "time": t,
                     "view": m.get("view_id") or "", "label_source": m.get("label_source", "human")})
    return rows


def parse_id_list(path: Path) -> dict:
    """'image_id|target_id' (or 'image_id') per line -> {image_id: sorted target ids or None (= all)}."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        iid, _, tid = line.partition("|")
        cur = out.setdefault(iid, set() if tid else None)
        if tid and cur is not None:
            cur.add(tid)
    return {k: (sorted(v) if v is not None else None) for k, v in out.items()}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


class ValWindow:
    """Calendar val blocks of a site: block = (date - first human date) // block_days; blocks with block % every == k
    are val. Any photo (human or propagated) on a val-block day is val / dropped, and photos within buffer_days of a
    val-block day are dropped from training - decided on CALENDAR days, not only on days that have a human photo."""

    def __init__(self, human_dates, block_days: int, every: int, buffer_days: int, seed_key: str):
        ds = sorted(set(human_dates))
        self.d0, self.block_days, self.every, self.buffer = _d(ds[0]), block_days, every, buffer_days
        k0 = random.Random(seed_key).randrange(every)
        for k in [k0] + [r for r in range(every) if r != k0]:     # a residue that holds at least one human photo
            self.k = k
            if any(self.is_val(d) for d in ds):
                break

    def _isv(self, day: dt.date) -> bool:
        return ((day - self.d0).days // self.block_days) % self.every == self.k

    def is_val(self, d: str) -> bool:
        return self._isv(_d(d))

    def is_buffer(self, d: str) -> bool:
        x = _d(d)
        return not self._isv(x) and any(self._isv(x + dt.timedelta(days=j)) for j in range(-self.buffer, self.buffer + 1) if j)

    def describe(self, dates):
        ds = sorted(set(dates))
        return {"first_day": self.d0.isoformat(), "block_days": self.block_days, "every": self.every, "residue": self.k,
                "val": [d for d in ds if self.is_val(d)], "buffer": [d for d in ds if self.is_buffer(d)]}


def build(protocol: dict, cfg: dict) -> dict:
    """cfg (private prepare config): exports {site: {kind: dir}}, human_ids {site: file}, exclude_ids [..],
    exceptions [{site, target, date, reason}], names {site: real name} (README only)."""
    sp = protocol["split"]
    sites = protocol["sites"]
    exclude = set(cfg.get("exclude_ids") or [])
    meta, human, test_targets = {}, {}, {}
    for site, sdef in sites.items():
        ex = cfg["exports"][site]
        for kind in sdef["exports"]:
            meta[(site, kind)] = [m for m in read_meta(ex[kind]) if m["id"] not in exclude and m["image_id"] not in exclude]
        if sdef["human_source"] == "export":
            human[site] = [m for m in meta[(site, "human")] if m["label_source"] == "human"]
            test_targets[site] = None
        else:
            ids = parse_id_list(cfg["human_ids"][site])
            human[site] = [m for m in meta[(site, "full")] if m["image_id"] in ids or m["id"] in ids]
            test_targets[site] = {m["id"]: ids.get(m["image_id"], ids.get(m["id"])) for m in human[site]}
            missing = set(ids) - {m["image_id"] for m in human[site]} - {m["id"] for m in human[site]}
            if missing:
                raise SystemExit(f"{site}: {len(missing)} listed human photos are not in the export (e.g. {sorted(missing)[:3]})")
    # test sets 사람 라벨링을 기준으로 선택
    test = {}
    for site in sites:
        hs = sorted(human[site], key=lambda m: (m["date"], m["id"]))
        if site in sp["test_one_per_date"]:
            by = {}
            for m in hs:
                by.setdefault(m["date"], []).append(m["id"])
            rng = random.Random(f"test|{site}|{sp['subsample_seed']}")
            test[site] = [rng.choice(sorted(v)) for _, v in sorted(by.items())]
        else:
            test[site] = [m["id"] for m in hs]
    # val / train of the val sites (independent of the fold: a site has the same val set in every fold)
    val, train_h, drop_dates, windows = {}, {}, {}, {}
    for site, sdef in sites.items():
        if not sdef.get("val_site"):
            continue
        win = ValWindow([m["date"] for m in human[site]], sp["val_block_days"], sp["val_every_blocks"], sp["buffer_days"],
                        f"val|{site}|{sp['val_seed']}")
        windows[site] = win
        val[site] = sorted(m["id"] for m in human[site] if win.is_val(m["date"]))
        train_h[site] = sorted(m["id"] for m in human[site] if not win.is_val(m["date"]) and not win.is_buffer(m["date"]))
        all_dates = [m["date"] for (s2, _), ms in meta.items() if s2 == site for m in ms]
        drop_dates[site] = win.describe(all_dates)
    prop = {}
    for (site, kind), ms in meta.items():
        if kind == "propagated":
            win = windows.get(site)
            prop[site] = sorted(m["id"] for m in ms if win is None or (not win.is_val(m["date"]) and not win.is_buffer(m["date"])))
    weak = {}
    for (site, kind), ms in meta.items():
        if kind == "full":
            weak[site] = sorted(m["id"] for m in ms)[:: max(1, sp["weak_every"])]
    folds = {}
    for fold in protocol["folds"]: # test 현장을 train, val 에서 빼는 명시적 코드
        f = {"fold": fold, "protocol_version": protocol["protocol_version"],
             "test": {"site": fold, "ids": test[fold], "only_targets": ({i: test_targets[fold][i] for i in test[fold]}
                                                                       if test_targets[fold] is not None else None)},
             "train_human": {s: v for s, v in train_h.items() if s != fold},
             "val": {s: v for s, v in val.items() if s != fold},
             "train_propagated": {s: v for s, v in prop.items() if s != fold},
             "train_weak": _cap_weak({s: v for s, v in weak.items() if s != fold}, sp.get("weak_cap_total"))}
        exc = [e for e in cfg.get("exceptions") or [] if e["site"] == fold]
        tdates = sorted({m["date"] for m in human[fold] if m["id"] in set(test[fold])})
        tids = sorted(test[fold], key=lambda i: next(m["date"] for m in human[fold] if m["id"] == i) + i)
        n = min(6, len(tids))
        pick = [tids[round(k * (len(tids) - 1) / max(n - 1, 1))] for k in range(n)] if n else []
        exc_dates = {e["date"] for e in exc}
        pick += [i for i in tids if next(m["date"] for m in human[fold] if m["id"] == i) in exc_dates][:4]
        f["overlay_ids"] = sorted(dict.fromkeys(pick))
        f["exceptions"] = exc
        f["test_dates"] = tdates
        f["digests"] = fold_digests(f)
        folds[fold] = f
    fps = {f"{s}/{k}": export_fingerprint(cfg["exports"][s][k]) for s, sdef in sites.items() for k in sdef["exports"]}
    manifest = {"protocol_version": protocol["protocol_version"], "protocol_sha256": protocol["_sha256"][:16],
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "exports": fps, "exclusions_digest": ids_digest(exclude),
                "folds": {k: v["digests"] for k, v in folds.items()},
                "val_dates": drop_dates,
                "counts": {k: {"test": len(v["test"]["ids"]),
                               "train_human": sum(map(len, v["train_human"].values())),
                               "val": sum(map(len, v["val"].values())),
                               "train_propagated": sum(map(len, v["train_propagated"].values())),
                               "train_weak": sum(map(len, v["train_weak"].values()))} for k, v in folds.items()}}
    return {"folds": folds, "manifest": manifest, "exclusions": sorted(exclude), "exceptions": cfg.get("exceptions") or []}


def _cap_weak(weak: dict, cap):
    """optional: at most `cap` weak photos per fold, split evenly over the training-side weak sites (evenly spaced in
    sample-id order), so every fold trains on the same amount of weak data and takes the same time."""
    if not cap or not weak:
        return weak
    share = int(cap) // len(weak)
    out = {}
    for s, ids in weak.items():
        if len(ids) <= share:
            out[s] = ids
        else:
            out[s] = [ids[round(k * (len(ids) - 1) / max(share - 1, 1))] for k in range(share)]
    return out


def fold_digests(f: dict) -> dict:
    d = {"test": ids_digest(f["test"]["ids"]),
         "test_targets": json_digest(f["test"]["only_targets"]) if f["test"]["only_targets"] is not None else None,
         "train_human": json_digest({s: ids_digest(v) for s, v in f["train_human"].items()}),
         "val": json_digest({s: ids_digest(v) for s, v in f["val"].items()}),
         "train_propagated": json_digest({s: ids_digest(v) for s, v in f["train_propagated"].items()}),
         "train_weak": json_digest({s: ids_digest(v) for s, v in f["train_weak"].items()}),
         "exceptions": json_digest(f.get("exceptions") or []),
         "overlay_ids": ids_digest(f.get("overlay_ids") or [])}
    d["fold"] = json_digest(d)
    return d


def write_bundle(out: Path, built: dict, cfg: dict, protocol: dict, expected_file: Path | None = EXPECTED_DIGESTS_FILE):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "folds").mkdir(parents=True)
    for k, f in built["folds"].items():
        write_json(out / "folds" / f"{k}.json", f)
    write_json(out / "bundle_manifest.json", built["manifest"])
    write_json(out / "exclusions.json", built["exclusions"])
    write_json(out / "exceptions.json", built["exceptions"])
    ex = {s: {k: f"<path to the {s} {k} export>" for k in sdef["exports"]} for s, sdef in protocol["sites"].items()}
    write_json(out / "local.example.json", {"bundle": "<path to this bundle folder>", "results": "<path for results>",
                                            "exports": ex})
    names = cfg.get("names") or {}
    lines = ["# PRIVATE LOSO bundle - do not commit, do not upload", "",
             f"protocol {protocol['protocol_version']}, created {built['manifest']['created']}", "",
             "| pseudonym | site | export kinds |", "|---|---|---|"]
    for s, sdef in protocol["sites"].items():
        lines.append(f"| {s} | {names.get(s, '')} | {', '.join(sdef['exports'])} |")
    lines += ["", "Fill local.json from local.example.json (one path per export), then run `check`."]
    (out / "README_private.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if expected_file is not None:
        write_json(expected_file, {"protocol_version": protocol["protocol_version"],
                                   "protocol_sha256": protocol["_sha256"][:16],
                                   "exports": {k: v["digest"] for k, v in built["manifest"]["exports"].items()},
                                   "folds": {k: v["fold"] for k, v in built["manifest"]["folds"].items()},
                                   "counts": built["manifest"]["counts"]})


def load_fold(bundle: Path, fold: str) -> dict:
    f = read_json(Path(bundle) / "folds" / f"{fold}.json")
    if f is None:
        raise SystemExit(f"fold {fold} not found in bundle {bundle}")
    if fold_digests(f)["fold"] != f["digests"]["fold"]:
        raise SystemExit(f"fold file {fold} was modified (digest mismatch)")
    return f


def prepare(cfg_path: Path, out: Path, protocol_path: Path | None = None, expected_file=EXPECTED_DIGESTS_FILE):
    protocol = load_protocol(protocol_path)
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    built = build(protocol, cfg)
    write_bundle(out, built, cfg, protocol, expected_file)
    log(f"bundle written to {out}")
    for k, c in built["manifest"]["counts"].items():
        log(f"  fold {k}: {c}")
    return built
