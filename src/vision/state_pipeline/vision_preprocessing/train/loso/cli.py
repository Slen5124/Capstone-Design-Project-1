"""LOSO experiment command line (same on Windows and Linux). Run from the folder that contains `vision_preprocessing`:

    python -m vision_preprocessing.train.loso check  --local local.json
    python -m vision_preprocessing.train.loso run    --local local.json --models mlp,dpt
    python -m vision_preprocessing.train.loso status --local local.json [--watch 60]
    python -m vision_preprocessing.train.loso pack   --local local.json --out results_mlp_dpt.zip
    python -m vision_preprocessing.train.loso report --results <dir or zip> [<dir or zip> ...] --out summary [--lang ko]
    python -m vision_preprocessing.train.loso compare <run dir A> <run dir B>
    (maintainer only) python -m vision_preprocessing.train.loso prepare --config prepare_config.json --out <bundle dir>

`run` checks the data first, then trains every (model, seed, fold) that has no DONE.json yet - seed by seed, the models
of protocol.run_last (linear) after all others. Interrupted? Run the same command again: finished runs are skipped and an
unfinished one resumes after its last finished epoch."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from .common import (EXPECTED_DIGESTS_FILE, HERE, export_fingerprint, load_local, load_protocol, log, read_csv,
                     read_json, run_dir)
from .progress import overall_line

PKG_PARENT = HERE.parents[2]          # the folder that contains vision_preprocessing/


# ------------------------------------------------------------------ check
def check(local_path, strict=False, protocol_path=None, quiet=False, expected_file=None) -> int:
    from .folds import load_fold
    P = load_protocol(protocol_path)
    local = load_local(local_path)
    errors, warns = [], []
    man = read_json(local["bundle"] / "bundle_manifest.json")
    if man is None:
        errors.append(f"bundle not found / incomplete: {local['bundle']}")
    else:
        if man.get("protocol_version") != P["protocol_version"] or man.get("protocol_sha256") != P["_sha256"][:16]:
            errors.append("bundle was made for a different protocol.json (version or content)")
        exp = read_json(expected_file or EXPECTED_DIGESTS_FILE)
        if exp is None:
            warns.append("expected_digests.json missing in the repository - cannot confirm the bundle is the official one")
        else:
            if exp.get("folds") != {k: v["fold"] for k, v in man["folds"].items()}:
                errors.append("bundle fold lists differ from the repository's expected_digests.json")
            if exp.get("exports") != {k: v["digest"] for k, v in man["exports"].items()}:
                errors.append("bundle export fingerprints differ from expected_digests.json")
        for f in P["folds"]:
            try:
                load_fold(local["bundle"], f)
            except SystemExit as e:
                errors.append(str(e))
        for key, fp in (man.get("exports") or {}).items():
            site, kind = key.split("/")
            path = (local["exports"].get(site) or {}).get(kind)
            if path is None:
                errors.append(f"local.json: no path for {site}/{kind}")
                continue
            if not Path(path).is_dir():
                errors.append(f"{site}/{kind}: folder not found: {path}")
                continue
            got = export_fingerprint(path)
            if got["digest"] != fp["digest"]:
                diff = [k for k in ("metadata", "targets", "splits", "n_images") if got.get(k) != fp.get(k)]
                errors.append(f"{site}/{kind}: export differs from the one the folds were made from ({', '.join(diff)})")
    import platform
    env = P["environment"]
    try:
        import numpy
        import timm
        import torch
        vv = {"python": ".".join(platform.python_version().split(".")[:2]), "torch": torch.__version__.split("+")[0],
              "timm": timm.__version__, "numpy": numpy.__version__}
        for k, want in (("python", env["python"]), ("torch", env["torch"]), ("timm", env["timm"]), ("numpy", env["numpy"])):
            if vv[k] != want:
                (errors if strict else warns).append(f"{k} {vv[k]} (protocol: {want})")
        if torch.cuda.is_available():
            gb = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
            if gb < env["min_gpu_gb"]:
                warns.append(f"GPU has {gb:.1f} GB (protocol assumes >= {env['min_gpu_gb']} GB)")
            gpu = f"{torch.cuda.get_device_name(0)} {gb:.1f} GB"
        else:
            warns.append("no CUDA GPU - training would be extremely slow")
            gpu = "none"
    except ImportError as e:
        errors.append(f"missing package: {e}")
        gpu = "?"
    res = Path(local["results"] or "results")
    res.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(res).free / 2 ** 30
    if free < env["min_free_disk_gb"]:
        warns.append(f"only {free:.1f} GB free at {res}")
    if not quiet or errors or warns:
        for w in warns:
            log(f"WARNING {w}")
        for e in errors:
            log(f"ERROR   {e}")
        log(("check OK" if not errors else f"check FAILED ({len(errors)} errors)") + f" | GPU {gpu} | free disk {free:.0f} GB | results {res}")
    return 1 if errors else 0


# ------------------------------------------------------------------ plan / run
def plan(P, models, folds=None, seeds=None):
    folds = folds or P["folds"]
    last = [m for m in models if m in P["run_last"]]
    first = [m for m in models if m not in last]

    def seeds_of(m):
        allowed = P["seeds_override"].get(m, P["seeds"])
        return allowed if seeds is None else [s for s in seeds if s in allowed]   # linear stays seed 0 only

    out = []
    for group in (first, last):
        all_seeds = sorted({s for m in group for s in seeds_of(m)})
        for s in all_seeds:
            for m in group:
                if s in seeds_of(m):
                    out += [(m, f, s) for f in folds]
    return out


def status(local_path, models=None, protocol_path=None, results=None, folds=None, seeds=None, quiet=False):
    """table of every planned run (bar, %, remaining time) + the overall line. -> [(model, fold, seed, state, info)]"""
    from .progress import bar, fmt_dur, overall, overall_line
    P = load_protocol(protocol_path)
    local = load_local(local_path)
    res = Path(results or local["results"] or "results")
    todo = plan(P, models or P["models"], folds, seeds)
    rows, summ = overall(res, todo, P["train"]["epochs"])
    names = {"done": "완료", "running": "진행 중", "paused": "중단됨", "todo": "대기"}
    out = []
    for r in rows:
        info = "" if r["state"] == "done" else f"남은 {fmt_dur(r['remaining'])}" if r["remaining"] is not None else ""
        if not quiet:
            print(f"{r['model']:8s} {r['fold']:7s} seed{r['seed']}  {bar(r['pct'], 20)} {r['pct'] * 100:5.1f}%  "
                  f"{names.get(r['state'], r['state']):6s} {info}")
        out.append((r["model"], r["fold"], r["seed"], r["state"], info))
    if not quiet:
        print(overall_line(summ))
        print("(중단됨 = 진행 기록이 30분 넘게 갱신되지 않음. 같은 run 명령을 다시 치면 이어서 진행)")
    return out


def watch(local_path, every=60, **k):
    """status, refreshed every `every` seconds (Ctrl+C to stop)."""
    import time as _t
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print(_t.strftime("%Y-%m-%d %H:%M:%S"), f"(새로고침 {every}초, 끝내려면 Ctrl+C)")
            status(local_path, **k)
            _t.sleep(every)
    except KeyboardInterrupt:
        return 0


def _overall(res, todo, P):
    from .progress import overall
    return overall(res, todo, P["train"]["epochs"])


def run(local_path, models, folds=None, seeds=None, workers=None, keep_going=False, skip_check=False, protocol_path=None,
        extra=None, expected_file=None, results=None):
    P = load_protocol(protocol_path)
    bad = [m for m in models if m not in P["models"]]
    if bad:
        raise SystemExit(f"unknown model(s) {bad}; protocol models: {P['models']}")
    if not skip_check and check(local_path, protocol_path=protocol_path, expected_file=expected_file) != 0:
        raise SystemExit("data / bundle check failed - nothing was started")
    local = load_local(local_path)
    local_path = Path(local_path).resolve()
    res = Path(results or local["results"] or "results").resolve()
    todo = plan(P, models, folds, seeds)
    log(f"{len(todo)} runs planned ({sum(1 for m, f, s in todo if (run_dir(res, m, f, s) / 'DONE.json').exists())} already done)")
    env = dict(os.environ, CUBLAS_WORKSPACE_CONFIG=":4096:8", PYTHONHASHSEED="0", PYTHONUNBUFFERED="1",
               PYTHONIOENCODING="utf-8")
    failed = []
    for m, f, s in todo:
        d = run_dir(res, m, f, s)
        if (d / "DONE.json").exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "vision_preprocessing.train.loso", "train", "--local", str(local_path), "--model", m,
               "--fold", f, "--seed", str(s), "--results", str(res)] + (["--workers", str(workers)] if workers is not None else []) \
              + (["--protocol", str(Path(protocol_path).resolve())] if protocol_path else []) + (extra or [])
        _, summ = _overall(res, todo, P)
        log(overall_line(summ))
        log(f"start {m} / {f} / seed {s}")
        with open(d / "stdout.txt", "a", encoding="utf-8") as fo:
            p = subprocess.Popen(cmd, cwd=str(PKG_PARENT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                sys.stdout.write(line)
                fo.write(line)
            rc = p.wait()
        if rc != 0:
            failed.append((m, f, s))
            log(f"FAILED {m} / {f} / seed {s} (exit {rc}) - see {d / 'stdout.txt'}")
            if not keep_going:
                return 1
    _, summ = _overall(res, todo, P)
    log(overall_line(summ))
    log("all planned runs finished" if not failed else f"{len(failed)} runs failed: {failed}")
    return 1 if failed else 0


# ------------------------------------------------------------------ pack / compare
def pack(local_path, out_zip, models=None, with_overlays=False, with_weights=False, results=None):
    local = load_local(local_path) if local_path else None
    res = Path(results or (local and local["results"]) or "results")
    keep = {".json", ".csv", ".txt", ".jsonl"}
    n = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for done in sorted(res.rglob("DONE.json")):
            d = done.parent
            rel = d.relative_to(res)
            if models and rel.parts[0] not in models:
                continue
            for p in d.rglob("*"):
                if not p.is_file():
                    continue
                ok = p.suffix in keep or (with_overlays and p.suffix == ".jpg") or (with_weights and p.name == "final.pt")
                if ok:
                    z.write(p, (rel / p.relative_to(d)).as_posix())
            n += 1
    log(f"{n} finished runs packed into {out_zip}" + ("" if with_overlays else " (no overlay photos)"))
    return 0


def compare(a, b):
    import numpy as np
    from .common import num, write_json
    a, b = Path(a), Path(b)
    out = {}
    for name in ("photo_rows_base_test.csv", "photo_rows_base_val.csv"):
        if (a / name).exists() and (b / name).exists():
            ra = {(r["id"], r["target"]): num(r["roi_model"]) for r in read_csv(a / name)}
            rb = {(r["id"], r["target"]): num(r["roi_model"]) for r in read_csv(b / name)}
            ks = [k for k in ra if k in rb and ra[k] is not None and rb[k] is not None]
            d = np.array([abs(ra[k] - rb[k]) for k in ks]) if ks else np.array([0.0])
            out[name] = {"rows": len(ks), "max_abs_diff": float(d.max()), "mean_abs_diff": float(d.mean())}
    if (a / "epochs.csv").exists() and (b / "epochs.csv").exists():
        ea, eb = read_csv(a / "epochs.csv"), read_csv(b / "epochs.csv")
        out["epochs"] = [{"epoch": x["epoch"], "loss_a": num(x["loss"]), "loss_b": num(y["loss"]),
                          "val_wall_mae_a": num(x.get("val_wall_mae")), "val_wall_mae_b": num(y.get("val_wall_mae")),
                          "test_wall_mae_a": num(x.get("test_wall_mae")), "test_wall_mae_b": num(y.get("test_wall_mae"))}
                         for x, y in zip(ea, eb)]
    ma, mb = read_json(a / "metrics.json", {}), read_json(b / "metrics.json", {})
    for s in ("val", "test"):
        if s in ma and s in mb:
            pa, pb = ma[s].get("per_surface") or {}, mb[s].get("per_surface") or {}
            out[f"{s}_wall_mae"] = [(pa.get("wall") or {}).get("ratio_mae_roi_model"), (pb.get("wall") or {}).get("ratio_mae_roi_model")]
    ca, cb = read_json(a / "config.json", {}), read_json(b / "config.json", {})
    out["machines"] = [ca.get("gpu"), cb.get("gpu")]
    out["same_code"] = ca.get("code_sha256") == cb.get("code_sha256")
    write_json(a / f"compare_with_{b.parent.parent.name}_{b.parent.name}_{b.name}.json", out)
    for k, v in out.items():
        if k != "epochs":
            print(k, v)
    return 0


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vision_preprocessing.train.loso", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="(maintainer) build the private bundle from the private prepare config")
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--zip", type=Path, default=None)
    p = sub.add_parser("check", help="verify bundle, data, software, GPU, disk")
    p.add_argument("--local", required=True, type=Path)
    p.add_argument("--strict", action="store_true", help="version differences are errors")
    p = sub.add_parser("status", help="progress of every planned run: bar, %, remaining time, finish time")
    p.add_argument("--local", required=True, type=Path)
    p.add_argument("--models", default=None)
    p.add_argument("--folds", default=None)
    p.add_argument("--seeds", default=None)
    p.add_argument("--results", type=Path, default=None)
    p.add_argument("--watch", type=int, default=0, metavar="SEC", help="refresh every SEC seconds (e.g. 60)")
    p = sub.add_parser("run", help="train every planned run that is not done yet")
    p.add_argument("--local", required=True, type=Path)
    p.add_argument("--models", required=True, help="comma list, e.g. mlp,dpt")
    p.add_argument("--folds", default=None)
    p.add_argument("--seeds", default=None)
    p.add_argument("--workers", type=int, default=None, help="DataLoader workers (results do not depend on it)")
    p.add_argument("--keep-going", action="store_true")
    p.add_argument("--skip-check", action="store_true")
    p.add_argument("--results", type=Path, default=None, help="results folder (default: local.json 'results')")
    p = sub.add_parser("train", help="one run (used by `run`)")
    p.add_argument("--local", required=True, type=Path)
    p.add_argument("--model", required=True)
    p.add_argument("--fold", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--results", type=Path, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--epochs", type=int, default=None, help="tests only")
    p.add_argument("--limit", type=int, default=0, help="tests only")
    p.add_argument("--no-pretrained", action="store_true", help="tests only")
    p.add_argument("--no-posteval", action="store_true")
    p.add_argument("--protocol", type=Path, default=None, help="tests only (default: the repository protocol.json)")
    p = sub.add_parser("report", help="tables + figures from result folders / zips")
    p.add_argument("--results", nargs="+", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--lang", choices=("en", "ko"), default="en")
    p.add_argument("--no-figures", action="store_true")
    p = sub.add_parser("pack", help="zip the finished runs to hand them over")
    p.add_argument("--local", type=Path, default=None)
    p.add_argument("--results", type=Path, default=None)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--models", default=None)
    p.add_argument("--with-overlays", action="store_true", help="include overlay photos (site images!)")
    p.add_argument("--with-weights", action="store_true", help="include final.pt (~16 MB per run)")
    p = sub.add_parser("compare", help="difference between two runs (same config on two PCs / twice)")
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        from .folds import prepare
        prepare(a.config, a.out)
        if a.zip:
            shutil.make_archive(str(a.zip.with_suffix("")), "zip", a.out)
            log(f"zip -> {a.zip}")
        return 0
    if a.cmd == "check":
        return check(a.local, a.strict)
    if a.cmd == "status":
        kw = dict(models=a.models.split(",") if a.models else None, results=a.results,
                  folds=a.folds.split(",") if a.folds else None, seeds=[int(x) for x in a.seeds.split(",")] if a.seeds else None)
        if a.watch:
            return watch(a.local, a.watch, **kw)
        status(a.local, **kw)
        return 0
    if a.cmd == "run":
        sl = [int(x) for x in a.seeds.split(",")] if a.seeds else None
        fl = a.folds.split(",") if a.folds else None
        return run(a.local, a.models.split(","), fl, sl, a.workers, a.keep_going, a.skip_check, results=a.results)
    if a.cmd == "train":
        from .trainer import train_one
        train_one(a.local, a.model, a.fold, a.seed, a.results, protocol_path=a.protocol, workers=a.workers, epochs=a.epochs,
                  pretrained=not a.no_pretrained, limit=a.limit, posteval=not a.no_posteval)
        return 0
    if a.cmd == "report":
        from .report import build_report
        build_report(a.results, a.out, lang=a.lang, figures=not a.no_figures)
        return 0
    if a.cmd == "pack":
        return pack(a.local, a.out, a.models.split(",") if a.models else None, a.with_overlays, a.with_weights, a.results)
    if a.cmd == "compare":
        return compare(a.a, a.b)
    return 2
