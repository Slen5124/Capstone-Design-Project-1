"""Collect finished runs (one or more result folders / zips from several PCs) into the paper tables.

Main unit: date x wall rows of the TEST site (and date x ceiling on the ceiling folds). Per run and per
(test-time variant, temporal variant): MAE, max error, share within tolerances, completion F1, rows; with and without
the pre-registered exception rows. Then: per (model, fold) mean +- std over seeds; macro mean over macro_folds (computed
per seed, then mean +- std); the case folds separately. The recommended pipeline per model is chosen on VAL only.

Outputs (summary/): main_table.csv, macro_table.csv, pipeline_table.csv, bootstrap.csv, size_bins.csv, resources.csv,
curves_epoch.csv, curves_step.csv, val_selected_epoch.csv, events.csv, risk_coverage.csv, runs.csv, summary.md"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from .common import EXPECTED_DIGESTS_FILE, load_protocol, log, num, read_csv, read_json, write_csv, write_json
from .temporal import daily_truth, direct_keys, smooth


# ------------------------------------------------------------------ loading
def find_runs(roots, P=None, allow_test_runs=False, expected_file=None):
    """-> run dirs (with DONE.json) that belong to THIS protocol: same version, full recipe (epochs, no --limit,
    pretrained encoder) and the official fold lists (expected_digests.json). Zips are extracted to a temporary folder.
    Duplicates of one (model, fold, seed) are reported and only the first is kept."""
    dirs = []
    for r in roots:
        r = Path(r)
        if r.is_file() and r.suffix == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix="loso_zip_"))
            with zipfile.ZipFile(r) as z:
                z.extractall(tmp)
            r = tmp
        dirs += [p.parent for p in sorted(r.rglob("DONE.json"))]
    exp = (read_json(expected_file or EXPECTED_DIGESTS_FILE) or {}).get("folds") if not allow_test_runs else None
    seen, out = {}, []
    for d in dirs:
        cfg = read_json(d / "config.json", {}) or {}
        ident = cfg.get("identity")
        if not ident:
            log(f"skipped (no config.json identity): {d}")
            continue
        why = None
        if P is not None and ident.get("protocol_version") != P["protocol_version"]:
            why = f"protocol {ident.get('protocol_version')}"
        elif P is not None and not allow_test_runs and (ident.get("epochs") != P["train"]["epochs"] or ident.get("limit")
                                                          or not ident.get("pretrained", True)):
            why = "test run (epochs / limit / no-pretrained)"
        elif exp and exp.get(ident.get("fold")) != ident.get("fold_digest"):
            why = "fold lists differ from expected_digests.json"
        if why:
            log(f"skipped ({why}): {d}")
            continue
        key = (ident.get("model"), ident.get("fold"), ident.get("seed"))
        if key in seen:
            log(f"duplicate run {key}: keeping {seen[key]}, ignoring {d}")
            continue
        seen[key] = d
        out.append(d)
    return out


def load_run(d: Path) -> dict:
    cfg = read_json(d / "config.json", {}) or {}
    m = read_json(d / "metrics.json", {}) or {}
    rows = {}
    for f in d.glob("photo_rows_*_*.csv"):
        stem = f.stem[len("photo_rows_"):]
        variant, split = stem.rsplit("_", 1)
        rows[(variant, split)] = read_csv(f)
    return {"dir": d, "cfg": cfg, "metrics": m, "ident": cfg["identity"], "rows": rows,
            "epochs": read_csv(d / "epochs.csv") if (d / "epochs.csv").exists() else [],
            "steps": read_csv(d / "steps.csv") if (d / "steps.csv").exists() else [],
            "resources": read_json(d / "resources.json", {}), "posteval": read_json(d / "posteval.json", {}),
            "exceptions": m.get("exceptions") or []}


# ------------------------------------------------------------------ per-run scores
def _is_exc(exceptions, target, date):
    """an exception without 'target' covers every surface of that date."""
    return any(e.get("date") == date and e.get("target") in (None, "", "*", target) for e in exceptions)


def score(rows, tvariant, P, exceptions=(), surface="wall", exclude_exceptions=False):
    gt = daily_truth(rows)
    pred, events = smooth(rows, tvariant, P["temporal"]["reset_drop"], P["temporal"]["reset_days"])
    direct = direct_keys(rows)
    keys = [k for k in gt if k[0] == surface and k in pred and not (exclude_exceptions and _is_exc(exceptions, k[1], k[2]))]
    if not keys:
        return None
    e = np.array([abs(gt[k] - pred[k]) for k in keys])
    thr = P["metrics"]["completion_threshold"]
    tp_ = sum(1 for k in keys if gt[k] >= thr and pred[k] >= thr)
    fp_ = sum(1 for k in keys if gt[k] < thr and pred[k] >= thr)
    fn_ = sum(1 for k in keys if gt[k] >= thr and pred[k] < thr)
    prec = tp_ / (tp_ + fp_) if tp_ + fp_ else None
    rec = tp_ / (tp_ + fn_) if tp_ + fn_ else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if (prec == 0 or rec == 0) else None)
    out = {"mae": float(e.mean()), "maxerr": float(e.max()), "rows": len(keys),
           "completion_precision": prec, "completion_recall": rec, "completion_f1": f1,
           "events": len([ev for ev in events if ev[0] == surface]),
           "coverage": sum(1 for k in keys if k in direct) / len(keys)}
    for t in P["metrics"]["tolerances"]:
        out[f"within_{int(round(t * 100))}"] = float((e <= t + 1e-9).mean())
    out["_errors"] = {k: float(abs(gt[k] - pred[k])) for k in keys}
    return out


def size_bin_errors(rows, P, surface="wall"):
    edges = P["metrics"]["size_bins"]
    names = [f"<{edges[0]:.0%}"] + [f"{a:.0%}-{b:.0%}" for a, b in zip(edges, edges[1:])] + [f">={edges[-1]:.0%}"]
    acc = defaultdict(list)
    for r in rows:
        if r["surface"] != surface:
            continue
        g, p, f = num(r.get("gt_ratio")), num(r.get("roi_model")), num(r.get("roi_frac"))
        if g is None or p is None or f is None:
            continue
        i = sum(1 for x in edges if f >= x)
        acc[names[i]].append(abs(g - p))
    return {n: (float(np.mean(v)), len(v)) for n, v in acc.items()}, names


def _ms(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None, None, 0
    return float(np.mean(v)), (float(np.std(v, ddof=1)) if len(v) > 1 else 0.0), len(v)


def _fmt(m, s, nd=3):
    return "-" if m is None else (f"{m:.{nd}f} ± {s:.{nd}f}" if s is not None else f"{m:.{nd}f}")


# ------------------------------------------------------------------ main
def build_report(roots, out: Path, protocol_path=None, lang="en", figures=True, allow_test_runs=False, expected_file=None):
    P = load_protocol(protocol_path)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [load_run(d) for d in find_runs(roots, P, allow_test_runs, expected_file)]
    if not runs:
        raise SystemExit("no finished runs of this protocol version found")
    log(f"{len(runs)} finished runs")
    models = [m for m in P["models"] if any(r["ident"]["model"] == m for r in runs)]
    folds = P["folds"]
    tvars, ivars = P["temporal"]["variants"], P["posteval"]["variants"]
    # per run, per (ivar, tvar, split, surface, exceptions?) scores
    S = {}
    for r in runs:
        i = r["ident"]
        for (iv, sp), rows in r["rows"].items():
            for tv in tvars:
                for surf in ("wall", "ceiling"):
                    for excl in (False, True):
                        sc = score(rows, tv, P, r["exceptions"] if sp == "test" else (), surf, excl)
                        if sc is not None:
                            S[(i["model"], i["fold"], i["seed"], iv, tv, sp, surf, excl)] = sc
    write_csv(out / "runs.csv", [{"model": r["ident"]["model"], "fold": r["ident"]["fold"], "seed": r["ident"]["seed"],
                                  "gpu": r["cfg"].get("gpu"), "dir": str(r["dir"]), "git": (r["cfg"].get("git") or {}).get("commit"),
                                  "code_train_quick": (r["cfg"].get("code_sha256") or {}).get("train_quick.py")} for r in runs])

    def seeds_of(model, fold):
        return sorted({r["ident"]["seed"] for r in runs if r["ident"]["model"] == model and r["ident"]["fold"] == fold})

    metric_keys = ["mae", "maxerr", "within_5", "within_10", "completion_f1", "coverage", "rows"]

    def agg(model, fold, iv, tv, sp, surf, excl):
        vals = [S.get((model, fold, s, iv, tv, sp, surf, excl)) for s in seeds_of(model, fold)]
        vals = [v for v in vals if v is not None]
        d = {"n_seeds": len(vals)}
        for k in metric_keys:
            d[k], d[k + "_std"], _ = _ms([v.get(k) for v in vals])
        return d

    # main table: test, base, raw (+ with exceptions excluded)
    main = []
    for model in models:
        for fold in folds:
            for surf in ("wall", "ceiling"):
                if surf == "ceiling" and fold not in P["ceiling_folds"]:
                    continue
                for excl in (False, True):
                    d = agg(model, fold, "base", "raw", "test", surf, excl)
                    if d["n_seeds"]:
                        miou = _ms([((r["metrics"].get("test") or {}).get("per_surface") or {}).get(surf, {}).get("state_miou_install")
                                    for r in runs if r["ident"]["model"] == model and r["ident"]["fold"] == fold])
                        main.append(dict({"model": model, "fold": fold, "surface": surf, "exceptions_excluded": excl,
                                          "role": "macro" if fold in P["macro_folds"] else "case",
                                          "install_miou": miou[0], "install_miou_std": miou[1]}, **d))
    write_csv(out / "main_table.csv", main)

    def _miou(model, fold, seed, surf):
        for r in runs:
            i = r["ident"]
            if i["model"] == model and i["fold"] == fold and i["seed"] == seed:
                return (((r["metrics"].get("test") or {}).get("per_surface") or {}).get(surf) or {}).get("state_miou_install")
        return None

    def macro(model, iv, tv, sp, surf, excl, fl=None):
        fl = fl or [f for f in P["macro_folds"] if not (surf == "ceiling" and f not in P["ceiling_folds"])]
        per_seed = []
        common = None
        for f in fl:
            ss = set(seeds_of(model, f))
            common = ss if common is None else common & ss
        for s in sorted(common or []):
            v = [S.get((model, f, s, iv, tv, sp, surf, excl)) for f in fl]
            if all(x is not None for x in v):
                row = {}
                for k in metric_keys:                      # a key counts only when every fold has it
                    vals = [x.get(k) for x in v]
                    row[k] = None if any(a is None for a in vals) else (float(np.max(vals)) if k == "maxerr" else
                                                                         float(np.sum(vals)) if k == "rows" else float(np.mean(vals)))
                mi = [_miou(model, f, s, surf) for f in fl]
                row["install_miou"] = None if any(a is None for a in mi) else float(np.mean(mi))
                per_seed.append(row)
        d = {"n_seeds": len(per_seed), "folds": "+".join(fl)}
        for k in metric_keys + ["install_miou"]:
            d[k], d[k + "_std"], _ = _ms([p[k] for p in per_seed])
        return d

    macro_rows = []
    for model in models:
        for surf in ("wall", "ceiling"):
            for excl in (False, True):
                d = macro(model, "base", "raw", "test", surf, excl)
                if d["n_seeds"]:
                    macro_rows.append(dict({"model": model, "surface": surf, "exceptions_excluded": excl}, **d))
    write_csv(out / "macro_table.csv", macro_rows)
    # pipelines: every (test-time variant x temporal variant); recommended = lowest mean VAL wall MAE
    pipe, chosen = [], {}
    for model in models:
        best = None
        for iv in ivars:
            for tv in tvars:
                vals = [S[k]["mae"] for k in S if k[0] == model and k[3] == iv and k[4] == tv and k[5] == "val" and k[6] == "wall" and not k[7]]
                vm = float(np.mean(vals)) if vals else None
                t = macro(model, iv, tv, "test", "wall", False)
                te = macro(model, iv, tv, "test", "wall", True)
                pipe.append({"model": model, "inference": iv, "temporal": tv, "val_wall_mae": vm, "val_runs": len(vals),
                             "test_macro_wall_mae": t["mae"], "test_macro_wall_mae_std": t["mae_std"],
                             "test_macro_wall_mae_excl": te["mae"], "n_seeds": t["n_seeds"]})
                if vm is not None and (best is None or vm < best[0]):
                    best = (vm, iv, tv)
        if best:
            chosen[model] = {"inference": best[1], "temporal": best[2], "val_wall_mae": best[0]}
    for p in pipe:
        c = chosen.get(p["model"]) or {}
        p["chosen_on_val"] = c.get("inference") == p["inference"] and c.get("temporal") == p["temporal"]
    write_csv(out / "pipeline_table.csv", pipe)
    write_json(out / "chosen_pipelines.json", chosen)
    # bootstrap: paired model differences on date x wall rows (errors averaged over common seeds)
    rng = np.random.default_rng(P["metrics"]["bootstrap_seed"])
    B = P["metrics"]["bootstrap_B"]
    boot = []

    def row_errors(model, fold, iv, tv, seeds, excl=False):
        acc = defaultdict(list)
        for s in seeds:
            sc = S.get((model, fold, s, iv, tv, "test", "wall", excl))
            if sc:
                for k, e in sc["_errors"].items():
                    acc[k].append(e)
        return {k: float(np.mean(v)) for k, v in acc.items() if len(v) == len(seeds)}

    settings = [("base", "raw", False), ("base", "raw", True)] + [(c["inference"], c["temporal"], False) for c in chosen.values()]
    for iv, tv, excl in dict.fromkeys(settings):
        for i, a in enumerate(models):
            for b in models[i + 1:]:
                per_fold = {}
                for f in P["macro_folds"] + P["case_folds"]:
                    seeds = sorted(set(seeds_of(a, f)) & set(seeds_of(b, f)))
                    if not seeds:
                        continue
                    ea, eb = row_errors(a, f, iv, tv, seeds, excl), row_errors(b, f, iv, tv, seeds, excl)
                    keys = sorted(set(ea) & set(eb))
                    if keys:
                        per_fold[f] = np.array([ea[k] - eb[k] for k in keys])
                for f, d in per_fold.items():
                    idx = rng.integers(0, len(d), size=(B, len(d)))
                    bs = d[idx].mean(1)
                    boot.append({"inference": iv, "temporal": tv, "exceptions_excluded": excl, "a": a, "b": b, "scope": f,
                                 "diff_a_minus_b": float(d.mean()),
                                 "ci_lo": float(np.percentile(bs, 2.5)), "ci_hi": float(np.percentile(bs, 97.5)),
                                 "p_a_better": float((bs < 0).mean()), "rows": len(d)})
                mf = [f for f in P["macro_folds"] if f in per_fold]
                if len(mf) == len(P["macro_folds"]):
                    bs = np.zeros(B)
                    for f in mf:
                        d = per_fold[f]
                        bs += d[rng.integers(0, len(d), size=(B, len(d)))].mean(1)
                    bs /= len(mf)
                    boot.append({"inference": iv, "temporal": tv, "exceptions_excluded": excl, "a": a, "b": b, "scope": "macro",
                                 "diff_a_minus_b": float(np.mean([per_fold[f].mean() for f in mf])),
                                 "ci_lo": float(np.percentile(bs, 2.5)), "ci_hi": float(np.percentile(bs, 97.5)),
                                 "p_a_better": float((bs < 0).mean()), "rows": int(sum(len(per_fold[f]) for f in mf))})
    write_csv(out / "bootstrap.csv", boot)
    # ROI size bins (photo level, test, base)
    sb = []
    for model in models:
        per = defaultdict(list)
        names = None
        for r in runs:
            if r["ident"]["model"] != model or r["ident"]["fold"] not in P["macro_folds"]:
                continue
            rows = r["rows"].get(("base", "test"))
            if rows:
                d, names = size_bin_errors(rows, P)
                for n, (m, c) in d.items():
                    per[(n, r["ident"]["seed"])].append((m, c))
        for n in names or []:
            seed_vals = []
            for s in sorted({k[1] for k in per if k[0] == n}):
                v = per[(n, s)]
                tot = sum(c for _, c in v)
                seed_vals.append(sum(m * c for m, c in v) / tot if tot else None)
            m, sd, k = _ms(seed_vals)
            nrows = sum(c for (nn, _), v in per.items() if nn == n for _, c in v)
            sb.append({"model": model, "roi_size": n, "photo_mae": m, "photo_mae_std": sd, "n_seeds": k, "photo_rows": nrows})
    write_csv(out / "size_bins.csv", sb)
    # resources
    rs = []
    for model in models:
        rr = [r["resources"] for r in runs if r["ident"]["model"] == model and r["resources"]]
        if rr:
            rs.append({"model": model, "trainable_params_M": round(rr[0]["trainable_params"] / 1e6, 3),
                       "frozen_params_M": round(rr[0]["frozen_params"] / 1e6, 2),
                       "inference_ms": _ms([x.get("inference_ms_per_photo_1024x576") for x in rr])[0],
                       "inference_peak_gpu_gb": _ms([x.get("inference_peak_gpu_gb") for x in rr])[0],
                       "train_peak_gpu_gb": _ms([x.get("train_peak_gpu_gb") for x in rr])[0],
                       "epoch_sec": _ms([x.get("epoch_train_sec_mean") for x in rr])[0], "runs": len(rr),
                       "gpus": "; ".join(sorted({str(x.get("gpu")) for x in rr}))})
    write_csv(out / "resources.csv", rs)
    # curves
    ce = []
    for model in models:
        for fold in folds:
            eps = [r["epochs"] for r in runs if r["ident"]["model"] == model and r["ident"]["fold"] == fold and r["epochs"]]
            if not eps:
                continue
            for e in sorted({int(float(x["epoch"])) for rows in eps for x in rows}):
                row = {"model": model, "fold": fold, "epoch": e}
                for k in ("loss", "loss_sem", "loss_state", "val_wall_mae", "test_wall_mae", "val_install_miou", "test_install_miou",
                          "test_ceiling_mae", "gpu_peak_gb", "sec_train"):
                    vals = [num(x.get(k)) for rows in eps for x in rows if int(float(x["epoch"])) == e]
                    row[k], row[k + "_std"], row["n_seeds"] = _ms(vals)
                ce.append(row)
    write_csv(out / "curves_epoch.csv", ce)
    cs = []
    for model in models:
        st = [r["steps"] for r in runs if r["ident"]["model"] == model and r["steps"]]
        steps = sorted({int(float(x["step"])) for rows in st for x in rows})
        for s_ in steps:
            vals = [num(x.get("loss")) for rows in st for x in rows if int(float(x["step"])) == s_]
            m, sd, k = _ms(vals)
            cs.append({"model": model, "step": s_, "loss": m, "loss_std": sd, "n_runs": k})
    write_csv(out / "curves_step.csv", cs)
    # auxiliary: the val-selected epoch (the model is always the last epoch)
    vs = []
    for r in runs:
        eps = [x for x in r["epochs"] if num(x.get("val_wall_mae")) is not None]
        if eps:
            b = min(eps, key=lambda x: num(x["val_wall_mae"]))
            last = eps[-1]
            vs.append({"model": r["ident"]["model"], "fold": r["ident"]["fold"], "seed": r["ident"]["seed"],
                       "val_best_epoch": int(float(b["epoch"])), "test_wall_mae_at_val_best": num(b.get("test_wall_mae")),
                       "test_wall_mae_last": num(last.get("test_wall_mae"))})
    write_csv(out / "val_selected_epoch.csv", vs)
    # reset events (demolition / rework flags) of the reset variant
    ev = []
    for r in runs:
        rows = r["rows"].get(("base", "test"))
        if rows:
            _, e = smooth(rows, "causal_view_w_reset", P["temporal"]["reset_drop"], P["temporal"]["reset_days"])
            for s_, t, v, d in e:
                ev.append({"model": r["ident"]["model"], "fold": r["ident"]["fold"], "seed": r["ident"]["seed"],
                           "surface": s_, "target": t, "view": v, "date": d})
    write_csv(out / "events.csv", ev)
    # abstention: keep only the most confident date x wall rows (confidence = mean visible share x mean confidence of
    # the photos of that day) and see how the error falls - which days a person should check
    cov_levels = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]
    rc = []
    for model in models:
        per = defaultdict(list)
        for r in runs:
            if r["ident"]["model"] != model or r["ident"]["fold"] not in P["macro_folds"]:
                continue
            rows = [x for x in (r["rows"].get(("base", "test")) or []) if x["surface"] == "wall"]
            gt = daily_truth(rows)
            pr, _ = smooth(rows, "raw")
            cf = defaultdict(list)
            for x in rows:
                o, c = num(x.get("pred_observed")), num(x.get("conf"))
                if o is not None and c is not None:
                    cf[(x["surface"], x["target"], x["date"])].append(o * c)
            keys = sorted((k for k in gt if k in pr and cf.get(k)), key=lambda k: -float(np.mean(cf[k])))
            for c in cov_levels:
                kk = keys[: max(1, int(round(len(keys) * c)))]
                if kk:
                    per[(c, r["ident"]["seed"])].append(float(np.mean([abs(gt[k] - pr[k]) for k in kk])))
        for c in cov_levels:
            m, sd, k = _ms([float(np.mean(v)) for (cc, _), v in per.items() if cc == c])
            if m is not None:
                rc.append({"model": model, "coverage": c, "wall_mae": m, "wall_mae_std": sd, "n_seeds": k})
    write_csv(out / "risk_coverage.csv", rc)
    write_md(out, P, main, macro_rows, pipe, chosen, boot, sb, rs, models)
    write_json(out / "report_meta.json", {"protocol_version": P["protocol_version"], "runs": len(runs), "models": models})
    if figures:
        from .figures import make_figures
        make_figures(out, runs, P, S, chosen, lang=lang)
    log(f"report -> {out}")
    return out


def write_md(out, P, main, macro_rows, pipe, chosen, boot, sb, rs, models):
    L = [f"# LOSO results ({P['protocol_version']})", "",
         "Wall / ceiling installed-ratio MAE over date x surface rows of the held-out site (mean ± std over seeds).", "",
         "## Macro average (" + " + ".join(P["macro_folds"]) + ")", "",
         "| model | surface | exceptions | MAE | max err (worst fold) | ±5%p | ±10%p | completion F1 | install mIoU | coverage | seeds |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in macro_rows:
        L.append(f"| {r['model']} | {r['surface']} | {'excluded' if r['exceptions_excluded'] else 'included'} | "
                 f"{_fmt(r['mae'], r['mae_std'])} | {_fmt(r['maxerr'], r['maxerr_std'])} | {_fmt(r['within_5'], r['within_5_std'], 2)} | "
                 f"{_fmt(r['within_10'], r['within_10_std'], 2)} | {_fmt(r['completion_f1'], r['completion_f1_std'], 2)} | "
                 f"{_fmt(r['install_miou'], r['install_miou_std'])} | {_fmt(r['coverage'], None, 2)} | {r['n_seeds']} |")
    L += ["", "## Per fold (exceptions included)", "", "| model | fold | surface | MAE | max err | install mIoU | rows | seeds |",
          "|---|---|---|---|---|---|---|---|"]
    for r in main:
        if not r["exceptions_excluded"]:
            L.append(f"| {r['model']} | {r['fold']} ({r['role']}) | {r['surface']} | {_fmt(r['mae'], r['mae_std'])} | "
                     f"{_fmt(r['maxerr'], r['maxerr_std'])} | {_fmt(r['install_miou'], r['install_miou_std'])} | "
                     f"{'-' if r['rows'] is None else int(r['rows'])} | {r['n_seeds']} |")
    L += ["", "## Pipelines chosen on val", ""]
    for m, c in chosen.items():
        t = next((p for p in pipe if p["model"] == m and p["chosen_on_val"]), {})
        L.append(f"- {m}: inference `{c['inference']}`, temporal `{c['temporal']}` (val wall MAE {c['val_wall_mae']:.3f}) -> "
                 f"test macro wall MAE {_fmt(t.get('test_macro_wall_mae'), t.get('test_macro_wall_mae_std'))}")
    L += ["", "## Paired bootstrap (base / raw, macro)", "", "| a | b | a - b | 95% CI | P(a better) |", "|---|---|---|---|---|"]
    for b in boot:
        if b["scope"] == "macro" and b["inference"] == "base" and b["temporal"] == "raw" and not b["exceptions_excluded"]:
            L.append(f"| {b['a']} | {b['b']} | {b['diff_a_minus_b']:+.4f} | [{b['ci_lo']:+.4f}, {b['ci_hi']:+.4f}] | {b['p_a_better']:.3f} |")
    L += ["", "## ROI size (photo-level wall MAE, macro folds, base)", "", "| model | ROI size | MAE | photo rows |", "|---|---|---|---|"]
    for r in sb:
        L.append(f"| {r['model']} | {r['roi_size']} | {_fmt(r['photo_mae'], r['photo_mae_std'])} | {r['photo_rows']} |")
    L += ["", "## Resources", "", "| model | trainable M | inference ms | peak GPU GB | epoch s |", "|---|---|---|---|---|"]
    for r in rs:
        L.append(f"| {r['model']} | {r['trainable_params_M']} | {r['inference_ms']} | {r['train_peak_gpu_gb']} | {r['epoch_sec']} |")
    (Path(out) / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
