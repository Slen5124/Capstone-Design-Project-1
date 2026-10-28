"""Paper figures from the collected runs (PNG 300 dpi + PDF; one column = 8.5 cm).

    fig1_learning_curves      train loss / val wall MAE / test wall MAE (analysis only) per epoch, mean +- std band
    fig2_fold_bars            test wall MAE per fold x model (error bar = std over seeds)
    fig3_roi_size             photo-level wall MAE by ROI size bin
    fig4_timeseries_<fold>    per wall: truth, raw prediction, chosen temporal correction (best model, seed 0)
    fig5_pipelines            macro test wall MAE for every test-time x temporal variant
    fig6_scatter              predicted vs true ratio (date x wall), per fold
    fig7_cost_accuracy        inference time vs macro wall MAE (marker area ~ trainable parameters)
    fig8_confusion            state confusion matrix (row-normalised), test, summed over folds / seeds, best model
    fig10_risk_coverage       wall MAE when only the most confident days are reported automatically (abstention)
    figures_with_photos/fig9_overlays_<fold>   photo | label | prediction of each model (site photos: check before use)
Every figure's data is also written as fig*.csv next to it, so it can be redrawn elsewhere."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from .common import log, num, write_csv
from .temporal import daily_truth, smooth

COLORS = {"mlp": "#4C78A8", "upernet": "#F58518", "dpt": "#54A24B", "linear": "#9D9D9D"}
NAMES = {"mlp": "MLP", "upernet": "UPerNet", "dpt": "DPT", "linear": "Linear"}
TXT = {
    "en": {"epoch": "epoch", "loss": "train loss", "val": "val wall MAE", "test": "test-site wall MAE (analysis)",
           "mae": "wall MAE", "fold": "held-out site", "roi": "ROI size (share of the photo)", "date": "date",
           "ratio": "installed ratio", "truth": "label", "raw": "prediction", "smooth": "causal correction",
           "pred": "predicted ratio", "true": "label ratio", "ms": "inference ms / photo", "gt": "label", "pr": "prediction"},
    "ko": {"epoch": "에폭", "loss": "학습 손실", "val": "val 벽 MAE", "test": "test 현장 벽 MAE (분석용)",
           "mae": "벽 MAE", "fold": "hold-out 현장", "roi": "ROI 크기 (사진 대비)", "date": "날짜",
           "ratio": "설치율", "truth": "정답", "raw": "예측", "smooth": "인과 보정",
           "pred": "예측 설치율", "true": "정답 설치율", "ms": "추론 시간 (ms/장)", "gt": "정답", "pr": "예측"},
}
CM = 1 / 2.54


def _setup(lang):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if lang == "ko":
        names = {f.name for f in font_manager.fontManager.ttflist}
        for cand in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Apple SD Gothic Neo", "Noto Serif CJK KR"):
            if cand in names:
                plt.rcParams["font.family"] = cand
                break
        else:
            log("no Korean font found - figures fall back to English labels")
            lang = "en"
    plt.rcParams.update({"font.size": 7, "axes.titlesize": 7, "axes.labelsize": 7, "legend.fontsize": 6,
                         "xtick.labelsize": 6, "ytick.labelsize": 6, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.unicode_minus": False, "savefig.dpi": 300})
    return plt, TXT[lang]


def _save(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")


def make_figures(summary_dir: Path, runs, P, S, chosen, lang="en"):
    from .common import read_csv
    plt, T = _setup(lang)
    fd = Path(summary_dir) / "figures"
    fd.mkdir(parents=True, exist_ok=True)
    models = [m for m in P["models"] if any(r["ident"]["model"] == m for r in runs)]
    # 1 learning curves (mean over folds and seeds)
    ce = read_csv(Path(summary_dir) / "curves_epoch.csv")
    fig, ax = plt.subplots(1, 3, figsize=(17.5 * CM, 4.6 * CM))
    rows = []
    for m in models:
        for j, (k, lab) in enumerate((("loss", T["loss"]), ("val_wall_mae", T["val"]), ("test_wall_mae", T["test"]))):
            by = defaultdict(list)
            for r in ce:
                if r["model"] == m and num(r.get(k)) is not None and r["fold"] in P["macro_folds"]:
                    by[int(r["epoch"])].append(num(r[k]))
            if not by:
                continue
            e = sorted(by)
            mu = np.array([np.mean(by[x]) for x in e])
            sd = np.array([np.std(by[x]) for x in e])
            ax[j].plot(e, mu, color=COLORS.get(m), label=NAMES.get(m, m), lw=1.2)
            ax[j].fill_between(e, mu - sd, mu + sd, color=COLORS.get(m), alpha=0.15, lw=0)
            ax[j].set_xlabel(T["epoch"])
            ax[j].set_ylabel(lab)
            rows += [{"model": m, "metric": k, "epoch": x, "mean": float(a), "std": float(b)} for x, a, b in zip(e, mu, sd)]
    from matplotlib.ticker import MaxNLocator
    for a in ax:
        a.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax[0].legend(frameon=False)
    fig.tight_layout()
    _save(fig, fd / "fig1_learning_curves")
    write_csv(fd / "fig1_learning_curves.csv", rows)
    plt.close(fig)
    # 2 fold bars
    main = read_csv(Path(summary_dir) / "main_table.csv")
    folds = [f for f in P["folds"] if any(r["fold"] == f for r in main)]
    fig, ax = plt.subplots(figsize=(8.5 * CM, 5 * CM))
    w = 0.8 / max(1, len(models))
    rows = []
    for i, m in enumerate(models):
        xs, ys, es = [], [], []
        for j, f in enumerate(folds):
            r = next((r for r in main if r["model"] == m and r["fold"] == f and r["surface"] == "wall"
                      and r["exceptions_excluded"] == "False"), None)
            if r and num(r["mae"]) is not None:
                xs.append(j + (i - (len(models) - 1) / 2) * w)
                ys.append(num(r["mae"]))
                es.append(num(r["mae_std"]) or 0)
                rows.append({"model": m, "fold": f, "mae": ys[-1], "std": es[-1]})
        ax.bar(xs, ys, w, yerr=es, color=COLORS.get(m), label=NAMES.get(m, m), capsize=1.5, error_kw={"lw": 0.6})
    ax.set_xticks(range(len(folds)))
    ax.set_xticklabels([f + ("*" if f in P["case_folds"] else "") for f in folds])
    ax.set_xlabel(T["fold"])
    ax.set_ylabel(T["mae"])
    ax.legend(frameon=False, ncol=len(models))
    _save(fig, fd / "fig2_fold_bars")
    write_csv(fd / "fig2_fold_bars.csv", rows)
    plt.close(fig)
    # 3 ROI size
    sb = read_csv(Path(summary_dir) / "size_bins.csv")
    bins = list(dict.fromkeys(r["roi_size"] for r in sb))
    order = sorted(bins, key=lambda b: (0 if b.startswith("<") else 2 if b.startswith(">") else 1))
    fig, ax = plt.subplots(figsize=(8.5 * CM, 5 * CM))
    for i, m in enumerate(models):
        xs, ys, es = [], [], []
        for j, b in enumerate(order):
            r = next((r for r in sb if r["model"] == m and r["roi_size"] == b), None)
            if r and num(r["photo_mae"]) is not None:
                xs.append(j + (i - (len(models) - 1) / 2) * w)
                ys.append(num(r["photo_mae"]))
                es.append(num(r["photo_mae_std"]) or 0)
        ax.bar(xs, ys, w, yerr=es, color=COLORS.get(m), label=NAMES.get(m, m), capsize=1.5, error_kw={"lw": 0.6})
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order)
    ax.set_xlabel(T["roi"])
    ax.set_ylabel(T["mae"])
    ax.legend(frameon=False, ncol=len(models))
    _save(fig, fd / "fig3_roi_size")
    write_csv(fd / "fig3_roi_size.csv", [{"model": r["model"], "roi_size": r["roi_size"], "photo_mae": r["photo_mae"],
                                          "std": r["photo_mae_std"], "photo_rows": r["photo_rows"]} for r in sb])
    plt.close(fig)
    # best model by macro wall MAE (base / raw)
    mt = read_csv(Path(summary_dir) / "macro_table.csv")
    cand = [(num(r["mae"]), r["model"]) for r in mt if r["surface"] == "wall" and r["exceptions_excluded"] == "False"
            and num(r["mae"]) is not None]
    best = min(cand)[1] if cand else models[0]
    tv_best = (chosen.get(best) or {}).get("temporal", "causal_view_w")
    # 4 time series per fold
    for f in P["folds"]:
        run = next((r for r in runs if r["ident"]["model"] == best and r["ident"]["fold"] == f and r["ident"]["seed"] == min(
            x["ident"]["seed"] for x in runs if x["ident"]["model"] == best and x["ident"]["fold"] == f)), None)
        if not run or ("base", "test") not in run["rows"]:
            continue
        rows_ = [r for r in run["rows"][("base", "test")] if r["surface"] == "wall"]
        gt = daily_truth(rows_)
        raw, _ = smooth(rows_, "raw")
        sm, _ = smooth(rows_, tv_best, P["temporal"]["reset_drop"], P["temporal"]["reset_days"])
        walls = sorted({k[1] for k in gt})
        if not walls:
            continue
        n = len(walls)
        cols = min(3, n)
        fig, axs = plt.subplots((n + cols - 1) // cols, cols, figsize=(17.5 * CM, 3.8 * CM * ((n + cols - 1) // cols)),
                                squeeze=False)
        out = []
        for i, t in enumerate(walls):
            a = axs[i // cols][i % cols]
            ds = sorted(d for (s, tt, d) in gt if tt == t)
            x = np.arange(len(ds))
            g = [gt[("wall", t, d)] for d in ds]
            r_ = [raw.get(("wall", t, d)) for d in ds]
            s_ = [sm.get(("wall", t, d)) for d in ds]
            a.plot(x, g, "k-", lw=1.2, label=T["truth"])
            a.plot(x, r_, "o", ms=2, color=COLORS.get(best), alpha=0.6, label=T["raw"])
            a.plot(x, s_, "-", lw=1, color=COLORS.get(best), label=T["smooth"])
            a.set_title(t)
            a.set_ylim(-0.05, 1.05)
            a.set_xticks(x[:: max(1, len(x) // 4)])
            a.set_xticklabels([ds[k][5:] for k in x[:: max(1, len(x) // 4)]])
            out += [{"wall": t, "date": d, "truth": gg, "raw": rr, "corrected": ss} for d, gg, rr, ss in zip(ds, g, r_, s_)]
        for j in range(n, len(axs) * cols):
            axs[j // cols][j % cols].axis("off")
        axs[0][0].legend(frameon=False)
        axs[0][0].set_ylabel(T["ratio"])
        fig.tight_layout()
        _save(fig, fd / f"fig4_timeseries_{f}")
        write_csv(fd / f"fig4_timeseries_{f}.csv", out)
        plt.close(fig)
    # 5 pipelines
    pipe = read_csv(Path(summary_dir) / "pipeline_table.csv")
    tvars, ivars = P["temporal"]["variants"], P["posteval"]["variants"]
    fig, ax = plt.subplots(1, len(models), figsize=(17.5 * CM, 4.8 * CM), squeeze=False, sharey=True)
    for i, m in enumerate(models):
        a = ax[0][i]
        mat = np.full((len(ivars), len(tvars)), np.nan)
        for p in pipe:
            if p["model"] == m and num(p["test_macro_wall_mae"]) is not None:
                mat[ivars.index(p["inference"]), tvars.index(p["temporal"])] = num(p["test_macro_wall_mae"])
        im = a.imshow(mat, cmap="viridis_r", aspect="auto")
        for (yy, xx), v in np.ndenumerate(mat):
            if not np.isnan(v):
                a.text(xx, yy, f"{v:.3f}", ha="center", va="center", fontsize=5, color="w")
        a.set_xticks(range(len(tvars)))
        a.set_xticklabels([t.replace("causal_", "c_") for t in tvars], rotation=45, ha="right")
        a.set_yticks(range(len(ivars)))
        a.set_yticklabels(ivars)
        a.set_title(NAMES.get(m, m))
    fig.tight_layout()
    _save(fig, fd / "fig5_pipelines")
    write_csv(fd / "fig5_pipelines.csv", pipe)
    plt.close(fig)
    # 6 scatter (best model, all seeds)
    fig, ax = plt.subplots(1, len(P["folds"]), figsize=(17.5 * CM, 4.6 * CM), squeeze=False)
    rows = []
    for j, f in enumerate(P["folds"]):
        a = ax[0][j]
        for r in runs:
            if r["ident"]["model"] != best or r["ident"]["fold"] != f or ("base", "test") not in r["rows"]:
                continue
            rr = r["rows"][("base", "test")]
            gt = daily_truth(rr)
            pr, _ = smooth(rr, "raw")
            ks = [k for k in gt if k[0] == "wall" and k in pr]
            a.scatter([gt[k] for k in ks], [pr[k] for k in ks], s=3, alpha=0.5, color=COLORS.get(best))
            rows += [{"fold": f, "seed": r["ident"]["seed"], "wall": k[1], "date": k[2], "truth": gt[k], "pred": pr[k]} for k in ks]
        a.plot([0, 1], [0, 1], "k--", lw=0.6)
        a.set_title(f)
        a.set_xlim(-0.03, 1.03)
        a.set_ylim(-0.03, 1.03)
        a.set_xlabel(T["true"])
    ax[0][0].set_ylabel(T["pred"])
    fig.tight_layout()
    _save(fig, fd / "fig6_scatter")
    write_csv(fd / "fig6_scatter.csv", rows)
    plt.close(fig)
    # 7 cost vs accuracy
    rs = read_csv(Path(summary_dir) / "resources.csv")
    fig, ax = plt.subplots(figsize=(8.5 * CM, 5 * CM))
    rows7 = []
    for r in rs:
        m = r["model"]
        acc = next((num(x["mae"]) for x in mt if x["model"] == m and x["surface"] == "wall" and x["exceptions_excluded"] == "False"), None)
        ms_ = num(r["inference_ms"])
        if acc is None or ms_ is None:
            continue
        ax.scatter([ms_], [acc], s=20 + 20 * (num(r["trainable_params_M"]) or 0), color=COLORS.get(m), label=NAMES.get(m, m))
        rows7.append({"model": m, "inference_ms": ms_, "macro_wall_mae": acc, "trainable_params_M": num(r["trainable_params_M"])})
        ax.annotate(f"{NAMES.get(m, m)} ({num(r['trainable_params_M']):.1f}M)", (ms_, acc), fontsize=5, xytext=(3, 3),
                    textcoords="offset points")
    ax.set_xlabel(T["ms"])
    ax.set_ylabel(T["mae"])
    _save(fig, fd / "fig7_cost_accuracy")
    write_csv(fd / "fig7_cost_accuracy.csv", rows7)
    plt.close(fig)
    # 8 confusion (best model)
    tot, cls = None, None
    for r in runs:
        if r["ident"]["model"] != best:
            continue
        p = Path(r["dir"]) / "confusion_state_test.csv"
        if p.exists():
            c = read_csv(p)
            cls = [x["gt"] for x in c]
            mtx = np.array([[float(x[k]) for k in cls] for x in c])
            tot = mtx if tot is None else tot + mtx
    if tot is not None:
        nm = tot / np.maximum(tot.sum(1, keepdims=True), 1)
        fig, ax = plt.subplots(figsize=(8.5 * CM, 7 * CM))
        ax.imshow(nm, cmap="Blues", vmin=0, vmax=1)
        for (yy, xx), v in np.ndenumerate(nm):
            ax.text(xx, yy, f"{v:.2f}", ha="center", va="center", fontsize=5, color="w" if v > 0.5 else "k")
        ax.set_xticks(range(len(cls)))
        ax.set_xticklabels(cls, rotation=45, ha="right")
        ax.set_yticks(range(len(cls)))
        ax.set_yticklabels(cls)
        ax.set_xlabel(T["pr"])
        ax.set_ylabel(T["gt"])
        ax.set_title(NAMES.get(best, best))
        _save(fig, fd / "fig8_confusion")
        write_csv(fd / "fig8_confusion.csv", [dict({"gt": cls[i]}, **{cls[j]: float(nm[i, j]) for j in range(len(cls))}) for i in range(len(cls))])
        plt.close(fig)
    # 10 risk-coverage (abstention)
    rcv = read_csv(Path(summary_dir) / "risk_coverage.csv") if (Path(summary_dir) / "risk_coverage.csv").exists() else []
    if rcv:
        fig, ax = plt.subplots(figsize=(8.5 * CM, 5 * CM))
        for m in models:
            pts = sorted((num(r["coverage"]), num(r["wall_mae"]), num(r["wall_mae_std"]) or 0) for r in rcv if r["model"] == m)
            if pts:
                xs, ys, es = zip(*pts)
                ax.plot(xs, ys, "o-", ms=2.5, color=COLORS.get(m), label=NAMES.get(m, m), lw=1)
                ax.fill_between(xs, np.array(ys) - np.array(es), np.array(ys) + np.array(es), color=COLORS.get(m), alpha=0.15, lw=0)
        ax.invert_xaxis()
        ax.set_xlabel("coverage (share of days reported automatically)" if T is TXT["en"] else "자동 보고 비율")
        ax.set_ylabel(T["mae"])
        ax.legend(frameon=False)
        _save(fig, fd / "fig10_risk_coverage")
        write_csv(fd / "fig10_risk_coverage.csv", rcv)
        plt.close(fig)
    # 9 overlays (site photos -> separate folder)
    _overlays(Path(summary_dir) / "figures_with_photos", runs, models, P, plt, T)
    log(f"figures -> {fd}")


def _overlays(fd: Path, runs, models, P, plt, T):
    from PIL import Image
    for f in P["folds"]:
        per_model = {}
        for m in models:
            rs = sorted([r for r in runs if r["ident"]["model"] == m and r["ident"]["fold"] == f], key=lambda r: r["ident"]["seed"])
            if rs and (Path(rs[0]["dir"]) / "overlays" / "test").is_dir():
                per_model[m] = Path(rs[0]["dir"]) / "overlays" / "test"
        if not per_model:
            continue
        ids = sorted({p.stem for d in per_model.values() for p in d.glob("*.jpg")})[:4]
        if not ids:
            continue
        cols = 2 + len(per_model)
        fig, axs = plt.subplots(len(ids), cols, figsize=(17.5 * CM, 2.4 * CM * len(ids)), squeeze=False)
        for i, sid in enumerate(ids):
            first = next(d for d in per_model.values() if (d / f"{sid}.jpg").exists())
            trip = np.asarray(Image.open(first / f"{sid}.jpg"))
            w3 = trip.shape[1] // 3
            axs[i][0].imshow(trip[:, :w3])
            axs[i][1].imshow(trip[:, w3:2 * w3])
            for j, (m, d) in enumerate(per_model.items()):
                p = d / f"{sid}.jpg"
                if p.exists():
                    t = np.asarray(Image.open(p))
                    axs[i][2 + j].imshow(t[:, 2 * (t.shape[1] // 3):])
                if i == 0:
                    axs[i][2 + j].set_title(NAMES.get(m, m))
            if i == 0:
                axs[i][0].set_title("photo")
                axs[i][1].set_title(T["gt"])
            for a in axs[i]:
                a.axis("off")
        fig.tight_layout()
        _save(fig, fd / f"fig9_overlays_{f}")
        plt.close(fig)
