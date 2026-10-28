"""One LOSO run = (model, fold, seed): train with the fixed recipe of protocol.json, log everything, keep the LAST epoch.

Files in the run folder (results/<model>/<fold>/seed<k>/):
    config.json          protocol + run identity, versions, GPU, code / weight hashes, git commit, data digests
    environment.txt      every installed package == version
    split_manifest.json  photo ids and digests of train / val / test
    steps.csv            every log_every_steps steps: loss (total / object / state), lr, grad norm, speed
    epochs.csv           per epoch: train losses, val metrics, test metrics (analysis only), time, peak GPU memory
    epoch_metrics.jsonl  per epoch: full val / test metric dicts
    resume.pt            state after the last finished epoch (weights, optimiser, scheduler, AMP, RNG) - deleted at the end
    final.pt             decoder + heads of the last epoch (the model the paper reports)
    metrics.json         final val / test evaluation (+ per-surface, confusion matrices)
    photo_rows_<variant>_<split>.csv, posteval.json   test-time variants (base / flip / multi-scale / ROI zoom)
    confusion_state_<split>.csv, confusion_object_<split>.csv
    resources.json       trainable / frozen parameters, inference speed, peak memory, epoch time
    overlays/<split>/    photo | label | prediction for the pre-chosen overlay ids
    DONE.json            written last; a run with DONE.json is skipped by `run`"""
from __future__ import annotations

import math
import os
import platform
import random
import sys
import time
from pathlib import Path

from .common import (code_hashes, git_commit, json_digest, load_local, load_protocol, log, read_json, rng_state,
                     run_dir, set_determinism, set_rng_state, sha256_file, write_csv, write_json)
from .folds import load_fold
from .progress import RunProgress, posteval_factor
from ..train_quick import SegData


def _env_lines():
    try:
        from importlib import metadata
        pk = sorted({(d.metadata["Name"] or "?").lower(): d.version for d in metadata.distributions()}.items())
        return [f"{n}=={v}" for n, v in pk]
    except Exception as e:                       # pragma: no cover
        return [f"(package list unavailable: {e})"]


def _weights_hash(encoder: str):
    try:
        from huggingface_hub import try_to_load_from_cache
        for fn in ("model.safetensors", "pytorch_model.bin"):
            p = try_to_load_from_cache(f"timm/{encoder}", fn)
            if isinstance(p, str) and os.path.exists(p):
                return {"file": fn, "sha256": sha256_file(Path(p))}
    except Exception:
        pass
    return {"file": None, "sha256": None, "note": "not in the Hugging Face cache (no-pretrained test run?)"}


def build_items(local: dict, fold: dict, surfaces, protocol: dict, exclude: set):
    """-> {'train': [...], 'val': [...], 'test': [...]} item dicts of data_prep.read_export, roles from the fold file."""
    from ..data_prep import read_export
    tp = protocol["train"]
    cache = {}

    def export_items(site, kind):
        key = (site, kind)
        if key not in cache:
            try:
                path = local["exports"][site][kind]
            except KeyError:
                raise SystemExit(f"local.json has no export path for {site}/{kind}")
            cache[key] = {it["id"]: it for it in read_export(Path(path), surfaces, exclude)}
        return cache[key]

    def take(site, kind, ids, role, source, weight):
        pool = export_items(site, kind)
        miss = [i for i in ids if i not in pool]
        if miss:
            raise SystemExit(f"{len(miss)} photos of {site}/{kind} ({role}) are missing from the export, e.g. {miss[:3]} "
                             "- is this the same export as in the bundle? run `check`")
        return [dict(pool[i], split=role, label_source=source, weight=weight, only_targets=None) for i in ids]

    sites = protocol["sites"]

    def human_kind(site):
        return "human" if sites[site]["human_source"] == "export" else "full"

    split = {"train": [], "val": [], "test": []}
    for s, ids in fold["train_human"].items():
        split["train"] += take(s, human_kind(s), ids, "train", "human", 1.0)
    human_train = list(split["train"])
    split["train"] += human_train * (tp["human_repeat"] - 1)
    for s, ids in fold["train_propagated"].items():
        split["train"] += take(s, "propagated", ids, "train", "propagated", tp["propagated_weight"])
    for s, ids in fold["train_weak"].items():
        split["train"] += take(s, "full", ids, "train", "weak", tp["weak_weight"])
    for s, ids in fold["val"].items():
        split["val"] += take(s, human_kind(s), ids, "val", "human", 1.0)
    t = fold["test"]
    split["test"] = take(t["site"], human_kind(t["site"]), t["ids"], "test", "human", 1.0)
    if t.get("only_targets") is not None:
        for it in split["test"]:
            tids = t["only_targets"].get(it["id"])
            if tids is not None:
                it["only_targets"] = {i for i, name in it["tindex"].items() if name in set(tids)}
    return split


class LosoData(SegData):
    """Augmentation randomness depends only on (seed, epoch, photo index): the same photo gets the same augmentation in
    the same epoch whatever the worker count, and a resumed run continues exactly. Module-level so DataLoader workers
    can pickle it on Windows (spawn)."""

    def __init__(self, *a, seed=0, **k):
        super().__init__(*a, seed=seed, **k)
        self.base_seed, self.epoch = seed, 0

    def __getitem__(self, i):
        self.rng = random.Random(f"aug|{self.base_seed}|{self.epoch}|{i}")
        return super().__getitem__(i)


def _class_weights(train_items, cache_file: Path):
    import torch
    from ..train_quick import semantic_class_weights
    key = json_digest(sorted(it["id"] for it in train_items) + [len(train_items)])
    c = read_json(cache_file, {}) or {}
    if c.get("key") == key:
        return torch.tensor(c["weights"], dtype=torch.float32), c["freq"]
    w, f = semantic_class_weights(train_items)
    write_json(cache_file, {"key": key, "weights": w.tolist(), "freq": [float(x) for x in f]})
    return w, [float(x) for x in f]


def _surf(r: dict, prefix: str) -> dict:
    out = {f"{prefix}_install_miou": r.get("state_miou_install"), f"{prefix}_sem_miou": r.get("semantic_miou_present"),
           f"{prefix}_mae": r.get("ratio_mae_roi_model")}
    for name, d in (r.get("per_surface") or {}).items():
        out[f"{prefix}_{name}_mae"] = d.get("ratio_mae_roi_model")
        out[f"{prefix}_{name}_maxerr"] = d.get("ratio_maxerr_roi_model")
        out[f"{prefix}_{name}_miou"] = d.get("state_miou_install")
    for k, v in (r.get("state_iou") or {}).items():
        out[f"{prefix}_iou_{k}"] = v
    return out


def _slim(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("ratio_rows", "image_rows", "note", "photo_rows")}


def _ck(model, protocol, fold_name, model_name, seed, epoch, surfaces, extra=None):
    from ..train_quick import MEAN, N_SEM, STD
    tp = protocol["train"]
    d = {"encoder": protocol["encoder"], "decoder": protocol.get("decoder_of", {}).get(model_name, model_name),
         "model_name": model_name, "vit_pad_crop": model.pad_crop, "n_sem": N_SEM, "n_state": surfaces.n_state,
         "state_outside": tp["state_outside"], "activity": tp["activity"], "surfaces": surfaces.names,
         "state_classes": surfaces.class_names, "export_size": [1024, 576],
         "state_dict": {k: t for k, t in model.state_dict().items() if not k.startswith("encoder.")},
         "input": {"mean": MEAN.flatten().tolist(), "std": STD.flatten().tolist(), "rgb_only": True},
         "epoch": epoch, "fold": fold_name, "seed": seed, "protocol_version": protocol["protocol_version"],
         "select": tp["select"], "license_note": "encoder weights from timm (DINOv2: Apache-2.0); decoder trained here"}
    d.update(extra or {})
    return d


def _truncate(path: Path, max_epoch: int, key="epoch"):
    from .common import read_csv
    if path.exists():
        rows = [r for r in read_csv(path) if int(float(r[key])) <= max_epoch]
        write_csv(path, rows)


def _truncate_jsonl(path: Path, max_epoch: int):
    import json
    if path.exists():
        keep = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and json.loads(l)["epoch"] <= max_epoch]
        path.write_text("".join(l + "\n" for l in keep), encoding="utf-8")


def measure_inference(model, device, size=(576, 1024), n=20):
    """-> (ms per photo, peak GPU GB during inference), batch 1, AMP."""
    import torch
    if device.type != "cuda":
        return None, None
    model.eval()
    torch.cuda.reset_peak_memory_stats()
    x = torch.randn(1, 3, *size, device=device)
    with torch.no_grad(), torch.autocast(device_type="cuda"):
        for _ in range(3):
            model(x)
        torch.cuda.synchronize()
        t = time.time()
        for _ in range(n):
            model(x)
        torch.cuda.synchronize()
    return round((time.time() - t) / n * 1000, 1), round(torch.cuda.max_memory_allocated() / 2 ** 30, 2)


def train_one(local_path: Path, model_name: str, fold_name: str, seed: int, results: Path | None = None,
              protocol_path: Path | None = None, workers: int | None = None, epochs: int | None = None,
              pretrained: bool = True, limit: int = 0, posteval: bool = True):
    """Train / resume one run. epochs / limit / pretrained=False are for tests only (recorded in config.json)."""
    protocol = load_protocol(protocol_path)
    set_determinism(seed)
    import numpy as np
    import torch
    import torch.nn.functional as F  # noqa: F401
    from ..data_prep import Surfaces
    from ..train_quick import TwoHeadSeg, evaluate, weighted_ce
    local = load_local(local_path)
    tp = dict(protocol["train"])
    if epochs is not None:
        tp["epochs"] = epochs
    if workers is not None:
        tp["num_workers"] = workers
    results = Path(results or local["results"] or "results")
    out = run_dir(results, model_name, fold_name, seed)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "DONE.json").exists():
        log(f"{out} already done")
        return out
    bundle = local["bundle"]
    fold = load_fold(bundle, fold_name)
    exclude = set(read_json(bundle / "exclusions.json", []) or [])
    surfaces = Surfaces(tp["activity"])
    split = build_items(local, fold, surfaces, protocol, exclude)
    if limit:
        split = {k: v[:limit] for k, v in split.items()}
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    amp = bool(tp["amp"]) and device.type == "cuda"
    identity = {"model": model_name, "fold": fold_name, "seed": seed, "protocol_version": protocol["protocol_version"],
                "protocol_sha256": protocol["_sha256"][:16], "fold_digest": fold["digests"]["fold"],
                "epochs": tp["epochs"], "limit": limit, "pretrained": pretrained, "encoder": protocol["encoder"]}
    run_key = json_digest(identity)
    import timm
    cfg = {"run_key": run_key, "identity": identity, "protocol": {k: v for k, v in protocol.items() if k != "_sha256"},
           "train_effective": tp, "bundle_manifest_digest": json_digest(read_json(bundle / "bundle_manifest.json", {})),
           "versions": {"python": platform.python_version(), "torch": torch.__version__, "timm": timm.__version__,
                        "numpy": np.__version__, "cuda": torch.version.cuda,
                        "cudnn": torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None},
           "platform": platform.platform(), "device": str(device),
           "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
           "gpu_mem_gb": round(torch.cuda.get_device_properties(0).total_memory / 2 ** 30, 2) if device.type == "cuda" else None,
           "determinism": {"cudnn_deterministic": torch.backends.cudnn.deterministic, "cudnn_benchmark": torch.backends.cudnn.benchmark,
                           "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
                           "note": "seeds, data order and augmentation are fixed; strict GPU determinism is off (8x slower), "
                                   "so repeated GPU runs differ only in the last digits"},
           "code_sha256": code_hashes(), "git": git_commit(), "encoder_weights": None, "argv": sys.argv}
    resume = out / "resume.pt"
    state = None
    if resume.exists():
        state = torch.load(resume, map_location="cpu", weights_only=False)
        if state.get("run_key") != run_key:
            raise SystemExit(f"{resume} belongs to a different run (protocol / fold / settings changed); move it away first")
        log(f"resuming {out} after epoch {state['epoch']}")
    decoder = protocol.get("decoder_of", {}).get(model_name, model_name)
    model = TwoHeadSeg(protocol["encoder"], decoder, pretrained=pretrained, freeze=True, n_state=surfaces.n_state)
    model.reset_trainable(seed)
    model = model.to(device)
    cfg["encoder_weights"] = _weights_hash(protocol["encoder"]) if pretrained else {"note": "random encoder (test run)"}
    if state is None:
        write_json(out / "config.json", cfg)
        (out / "environment.txt").write_text("\n".join(_env_lines()) + "\n", encoding="utf-8")
        man = {k: {"n": len(v), "n_unique": len({it["id"] for it in v}), "ids": sorted({it["id"] for it in v})} for k, v in split.items()}
        man["fold_digests"] = fold["digests"]
        write_json(out / "split_manifest.json", man)
        for f in ("steps.csv", "epochs.csv", "epoch_metrics.jsonl"):
            (out / f).unlink(missing_ok=True)
    trainable = [p for p in model.parameters() if p.requires_grad]
    n_tr, n_fr = sum(p.numel() for p in trainable), sum(p.numel() for p in model.encoder.parameters())
    log(f"{model_name} / {fold_name} / seed {seed}: train {len(split['train'])} (unique {len({i['id'] for i in split['train']})}), "
        f"val {len(split['val'])}, test {len(split['test'])} | trainable {n_tr/1e6:.2f}M frozen {n_fr/1e6:.1f}M | {device}")
    w_sem, freq = _class_weights(split["train"], results / "_cache" / f"class_weights_{fold_name}.json")
    cw_sem = w_sem.to(device) if tp["class_weights"] else None
    sample_w = torch.tensor([it["weight"] for it in split["train"]], dtype=torch.float32)
    ds = LosoData(split["train"], surfaces, train=True, copy_paste=tp["copy_paste"], seed=seed, outside=tp["state_outside"],
                  geo=tp["aug"] == "geo")
    steps_per_epoch = max(1, len(ds) // tp["batch"]) if len(ds) > tp["batch"] else 1
    total = tp["epochs"] * steps_per_epoch
    opt = torch.optim.AdamW(trainable, lr=tp["lr"], weight_decay=tp["weight_decay"])
    if total >= 20:
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=tp["lr"], total_steps=total, pct_start=tp["pct_start"])
    else:                                                     # tiny test runs
        sched = torch.optim.lr_scheduler.ConstantLR(opt, factor=1.0, total_iters=1)
    scaler = torch.amp.GradScaler(device.type, enabled=amp)
    start_ep, gstep = 1, 0
    if state is not None:
        model.load_state_dict(state["model"], strict=False)
        opt.load_state_dict(state["opt"])
        sched.load_state_dict(state["sched"])
        scaler.load_state_dict(state["scaler"])
        set_rng_state(state["rng"])
        start_ep, gstep = state["epoch"] + 1, state["global_step"]
        _truncate(out / "epochs.csv", state["epoch"])
        _truncate(out / "steps.csv", state["epoch"])
        _truncate_jsonl(out / "epoch_metrics.jsonl", state["epoch"])
    from .common import read_csv
    ep_rows = read_csv(out / "epochs.csv") if (out / "epochs.csv").exists() else []
    st_rows = read_csv(out / "steps.csv") if (out / "steps.csv").exists() else []
    t_run = time.time()
    prog = RunProgress(out, identity, tp["epochs"], steps_per_epoch, start_ep, ep_rows, log=log,
                       every_sec=float(os.environ.get("LOSO_PROGRESS_EVERY", 60)),
                       posteval_factor=posteval_factor(protocol) if posteval else 0.0)
    prog.show()
    for ep in range(start_ep, tp["epochs"] + 1):
        ds.epoch = ep
        g = torch.Generator()
        g.manual_seed(seed * 1000 + ep)
        order = torch.randperm(len(ds), generator=g).tolist()
        loader = torch.utils.data.DataLoader(ds, batch_size=tp["batch"], sampler=order, num_workers=tp["num_workers"],
                                             drop_last=len(ds) > tp["batch"], persistent_workers=False)
        model.train()
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats()
        lr0, t0 = opt.param_groups[0]["lr"], time.time()
        acc = {"loss": 0.0, "loss_sem": 0.0, "loss_state": 0.0, "n": 0}
        win = {"loss": 0.0, "loss_sem": 0.0, "loss_state": 0.0, "n": 0, "t": time.time()}
        for x, sem, st, ix in loader:
            x, sem, st = x.to(device), sem.to(device), st.to(device)
            sw = sample_w[ix].to(device)
            with torch.autocast(device_type=device.type, enabled=amp):
                ls, lt = model(x)
                loss_s = weighted_ce(ls.float(), sem, sw, cw_sem) if (sem != 255).any() else ls.sum() * 0
                loss_t = weighted_ce(lt.float(), st, sw) if (st != 255).any() else lt.sum() * 0
                loss = tp["sem_weight"] * loss_s + loss_t
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            gnorm = float(torch.nn.utils.clip_grad_norm_(trainable, float("inf")))
            scaler.step(opt)
            scaler.update()
            sched.step()
            gstep += 1
            prog.step(ep, gstep - (ep - 1) * steps_per_epoch)
            b = x.shape[0]
            for k, v in (("loss", loss), ("loss_sem", loss_s), ("loss_state", loss_t)):
                acc[k] += float(v.detach()) * b
                win[k] += float(v.detach()) * b
            acc["n"] += b
            win["n"] += b
            if gstep % tp["log_every_steps"] == 0:
                dt_ = time.time() - win["t"]
                st_rows.append({"step": gstep, "epoch": ep, "lr": opt.param_groups[0]["lr"],
                                "loss": round(win["loss"] / win["n"], 5), "loss_sem": round(win["loss_sem"] / win["n"], 5),
                                "loss_state": round(win["loss_state"] / win["n"], 5), "grad_norm": round(gnorm, 4),
                                "imgs_per_s": round(win["n"] / max(dt_, 1e-6), 2)})
                win = {"loss": 0.0, "loss_sem": 0.0, "loss_state": 0.0, "n": 0, "t": time.time()}
        sec_train = time.time() - t0
        row = {"epoch": ep, "lr_start": lr0, "lr_end": opt.param_groups[0]["lr"],
               "loss": round(acc["loss"] / max(acc["n"], 1), 5), "loss_sem": round(acc["loss_sem"] / max(acc["n"], 1), 5),
               "loss_state": round(acc["loss_state"] / max(acc["n"], 1), 5), "sec_train": round(sec_train, 1),
               "imgs_per_s": round(acc["n"] / max(sec_train, 1e-6), 2),
               "gpu_peak_gb": round(torch.cuda.max_memory_allocated() / 2 ** 30, 2) if device.type == "cuda" else None}
        ev = {"epoch": ep}
        if ep % max(1, tp["eval_every"]) == 0 or ep == tp["epochs"]:
            prog.set_phase("eval")
            t1 = time.time()
            for name in ("val", "test"):           # test = analysis only; the model is the last epoch whatever these say
                if split[name]:
                    r = evaluate(model, split[name], device, amp, surfaces, outside=tp["state_outside"])
                    row.update(_surf(r, name))
                    ev[name] = _slim(r)
            row["sec_eval"] = round(time.time() - t1, 1)
        ep_rows.append(row)
        write_csv(out / "epochs.csv", ep_rows)
        write_csv(out / "steps.csv", st_rows)
        with open(out / "epoch_metrics.jsonl", "a", encoding="utf-8") as f:
            import json
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        log(f"epoch {ep}/{tp['epochs']} loss {row['loss']} val wall {row.get('val_wall_mae')} test wall {row.get('test_wall_mae')} "
            f"({row['sec_train']}s)")
        tmp = out / "resume.pt.tmp"
        torch.save({"run_key": run_key, "epoch": ep, "global_step": gstep,
                    "model": {k: t for k, t in model.state_dict().items() if not k.startswith("encoder.")},
                    "opt": opt.state_dict(), "sched": sched.state_dict(), "scaler": scaler.state_dict(), "rng": rng_state()}, tmp)
        os.replace(tmp, resume)
        prog.epoch_done(row)
        if os.environ.get("LOSO_TEST_STOP_AFTER_EPOCH") == str(ep):      # test hook: simulate a crash / power cut
            raise SystemExit(3)
    # ---- final model = last epoch
    final_ep = tp["epochs"]
    torch.save(_ck(model, protocol, fold_name, model_name, seed, final_ep, surfaces), out / "final.pt")
    metrics = {"identity": identity, "final_epoch": final_ep,
               "counts": {k: len(v) for k, v in split.items()},
               "counts_unique": {k: len({i["id"] for i in v}) for k, v in split.items()}, "class_weight_freq": freq,
               "exceptions": fold.get("exceptions") or [], "test_site": fold["test"]["site"]}
    ov = set(fold.get("overlay_ids") or [])
    from .posteval import run_variants
    for name in ("val", "test"):
        if not split[name]:
            continue
        r = evaluate(model, split[name], device, amp, surfaces, overlay_dir=out / "overlays" / name, outside=tp["state_outside"],
                     overlay_ids=ov if name == "test" else set())
        metrics[name] = _slim(r)
        write_csv(out / f"photo_rows_base_{name}.csv", r["photo_rows"])
        write_csv(out / f"daily_rows_base_{name}.csv", r["ratio_rows"])
        cls = surfaces.class_names
        write_csv(out / f"confusion_state_{name}.csv",
                  [dict({"gt": cls[i]}, **{cls[j]: r["state_confusion"][i][j] for j in range(len(cls))}) for i in range(len(cls))])
        sc = list(r["semantic_iou"].keys())
        write_csv(out / f"confusion_object_{name}.csv",
                  [dict({"gt": sc[i]}, **{sc[j]: r["semantic_confusion"][i][j] for j in range(len(sc))}) for i in range(len(sc))])
    write_json(out / "metrics.json", metrics)
    res = {"trainable_params": n_tr, "frozen_params": n_fr,
           "decoder_params": {m: sum(p.numel() for p in getattr(model, m).parameters()) if getattr(model, m) is not None else 0
                              for m in ("adapter", "decoder", "sem_head", "state_head")},
           "inference_ms_per_photo_1024x576": None, "inference_peak_gpu_gb": None,
           "train_peak_gpu_gb": max([float(r["gpu_peak_gb"]) for r in ep_rows if r.get("gpu_peak_gb") not in (None, "", "None")] or [0]) or None,
           "epoch_train_sec_mean": round(float(np.mean([float(r["sec_train"]) for r in ep_rows])), 1) if ep_rows else None,
           "device": str(device), "gpu": cfg["gpu"]}
    res["inference_ms_per_photo_1024x576"], res["inference_peak_gpu_gb"] = measure_inference(model, device)
    write_json(out / "resources.json", res)
    t_pe = time.time()
    if posteval:
        prog.set_phase("posteval")
        run_variants(model, split, device, amp, surfaces, protocol, out)
    total = prog.finish()
    write_json(out / "DONE.json", {"run_key": run_key, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                   "train_minutes_this_session": round((time.time() - t_run) / 60, 1),
                                   "run_sec_total": round(total), "posteval_sec": round(time.time() - t_pe),
                                   "protocol_version": protocol["protocol_version"], "fold_digest": fold["digests"]["fold"]})
    resume.unlink(missing_ok=True)
    log(f"done -> {out}")
    return out
