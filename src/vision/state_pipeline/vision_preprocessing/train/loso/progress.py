"""Progress bars, percentages and remaining-time estimates (plain text: works in PowerShell, cmd, Linux terminals and
in log files; only ASCII '#' / '.' for the bars, so a Korean Windows console (cp949) shows them too).

    run level   printed by the trainer about once a minute and at every epoch / phase change, and written to
                progress.json in the run folder (so `status` in another terminal shows it live)
    overall     printed by `run` before / after every run and by `status`: done runs, %, remaining time, finish time"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

from .common import read_csv, read_json, write_json, run_dir


def fmt_dur(sec) -> str:
    if sec is None:
        return "측정 전"
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}시간 {m:02d}분"
    if m:
        return f"{m}분 {s:02d}초"
    return f"{s}초"


def bar(frac, width=24) -> str:
    frac = 0.0 if frac is None else min(1.0, max(0.0, frac))
    n = int(round(frac * width))
    return "[" + "#" * n + "." * (width - n) + "]"


def finish_at(sec) -> str:
    return "?" if sec is None else time.strftime("%m/%d %H:%M", time.localtime(time.time() + sec))


class RunProgress:
    """ETA of one run = remaining training steps x measured step time + remaining evaluations x measured evaluation time
    + final evaluation + test-time variants (about posteval_factor evaluations; see posteval_factor())."""

    def __init__(self, out: Path, ident: dict, epochs: int, steps_per_epoch: int, start_ep: int, prior_rows=(),
                 every_sec=60, posteval_factor=6.0, log=print):
        self.out, self.ident, self.epochs, self.spe = Path(out), ident, epochs, max(1, steps_per_epoch)
        self.every, self.pf, self.log = every_sec, posteval_factor, log
        rows = list(prior_rows)
        self.prior = sum(float(r.get("sec_train") or 0) + float(r.get("sec_eval") or 0) for r in rows)
        self.step_times = [float(r["sec_train"]) / self.spe for r in rows if r.get("sec_train")]
        self.eval_times = [float(r["sec_eval"]) for r in rows if r.get("sec_eval")]
        self.t0, self.last, self.ep, self.i, self.phase = time.time(), 0.0, start_ep, 0, "train"
        self.sess_steps, self.sess_t = 0, None
        self.done_epochs = start_ep - 1

    # ---- estimates
    def _t_step(self):
        if self.sess_steps >= 10 and self.sess_t is not None:
            return (time.time() - self.sess_t) / self.sess_steps
        return statistics.mean(self.step_times) if self.step_times else None

    def _t_eval(self):
        return statistics.mean(self.eval_times) if self.eval_times else None

    def remaining(self):
        ts, te = self._t_step(), self._t_eval()
        if self.phase == "done":
            return 0.0
        if ts is None:
            return None
        te = te if te is not None else 0.0
        if self.phase in ("train", "eval"):
            steps_left = (self.epochs - self.ep) * self.spe + (self.spe - self.i)
            evals_left = self.epochs - self.ep + 1           # this epoch's evaluation onwards
            return steps_left * ts + evals_left * te + te + self.pf * te
        if self.phase == "final_eval":
            return te + self.pf * te
        if self.phase == "posteval":
            return self.pf * te * 0.5
        return None

    def elapsed(self):
        return self.prior + (time.time() - self.t0)

    def pct(self):
        """share of the run's expected total time; never shown going backwards (estimates get corrected upwards)."""
        rem = self.remaining()
        if rem is None:                                   # nothing measured yet: a cautious step-based guess
            cur = (self.done_epochs + self.i / self.spe) / self.epochs * 0.5
        else:
            el = self.elapsed()
            cur = el / max(el + rem, 1e-6)
        self._shown = max(getattr(self, "_shown", 0.0), cur) if self.phase != "done" else 1.0
        return self._shown

    # ---- events
    def step(self, ep, i):
        if self.sess_t is None:
            self.sess_t = time.time()
        self.ep, self.i = ep, i
        self.sess_steps += 1
        if time.time() - self.last >= self.every:
            self.show()

    def epoch_done(self, row):
        if row.get("sec_train"):
            self.step_times.append(float(row["sec_train"]) / self.spe)
        if row.get("sec_eval"):
            self.eval_times.append(float(row["sec_eval"]))
        self.done_epochs = int(row["epoch"])
        self.ep, self.i = int(row["epoch"]) + 1, 0
        self.sess_steps, self.sess_t = 0, None               # next epoch re-measures (workers restart each epoch)
        self.phase = "final_eval" if self.ep > self.epochs else "train"
        self.show()

    def set_phase(self, phase):
        self.phase = phase
        if phase == "eval":
            self.i = self.spe
        self.show()

    def show(self):
        self.last = time.time()
        rem, pct = self.remaining(), self.pct()
        i = self.ident
        if self.phase == "train":
            where = f"epoch {min(self.ep, self.epochs)}/{self.epochs} {bar(self.i / self.spe, 10)} step {self.i}/{self.spe}"
        elif self.phase == "eval":
            where = f"epoch {min(self.ep, self.epochs)}/{self.epochs} 검증·분석 평가 중"
        else:
            where = {"final_eval": "최종 평가 중", "posteval": "추론 변형(TTA·ROI 확대) 평가 중", "done": "완료"}.get(self.phase, self.phase)
        self.log(f"[진행] {i['model']}/{i['fold']}/seed{i['seed']} {bar(pct)} {pct * 100:5.1f}% | {where} | "
                 f"남은 시간 {fmt_dur(rem)} (완료 예상 {finish_at(rem)})")
        write_json(self.out / "progress.json", {
            "model": i["model"], "fold": i["fold"], "seed": i["seed"], "phase": self.phase, "epoch": min(self.ep, self.epochs),
            "epochs": self.epochs, "step_in_epoch": self.i, "steps_per_epoch": self.spe, "pct": round(pct, 4),
            "remaining_sec": None if rem is None else round(rem), "elapsed_sec": round(self.elapsed()),
            "updated": time.time(), "updated_text": time.strftime("%Y-%m-%d %H:%M:%S")})

    def finish(self):
        self.phase = "done"
        self.show()
        return self.elapsed()


def posteval_factor(protocol) -> float:
    """cost of the test-time variants in 'evaluations': flip = 2 passes, multi-scale = 2 x sum(scale^2) (pixels grow with
    the square of the scale), ROI zoom = multi-scale + a little."""
    sc = protocol["posteval"]["tta_scales"]
    v = protocol["posteval"]["variants"]
    ms = 2 * sum(x * x for x in sc)
    return ("flip" in v) * 2 + ("ms" in v) * ms + ("ms_roizoom" in v) * ms * 1.1


# ------------------------------------------------------------------ overall (runner / status)
def run_state(d: Path, epochs: int):
    """-> (state, pct, remaining_sec or None, total_sec of a finished run or None)."""
    done = read_json(d / "DONE.json")
    if done:
        return "done", 1.0, 0.0, done.get("run_sec_total")
    pr = read_json(d / "progress.json")
    if pr:
        fresh = time.time() - float(pr.get("updated", 0)) < 30 * 60
        rem = pr.get("remaining_sec")
        return ("running" if fresh else "paused"), float(pr.get("pct") or 0), rem, None
    if (d / "epochs.csv").exists():
        n = len(read_csv(d / "epochs.csv"))
        return "paused", n / max(epochs, 1) * 0.9, None, None
    return "todo", 0.0, None, None


def overall(results: Path, plan_rows, epochs: int):
    """plan_rows: [(model, fold, seed)]. -> (rows, summary). Remaining time of runs not measured yet = median total time
    of finished runs of the same (model, fold), else same fold, else same model, else any finished run."""
    rows = []
    for m, f, s in plan_rows:
        st, pct, rem, tot = run_state(run_dir(results, m, f, s), epochs)
        rows.append({"model": m, "fold": f, "seed": s, "state": st, "pct": pct, "remaining": rem, "total": tot})
    fin = [r for r in rows if r["state"] == "done" and r["total"]]

    def typical(m, f):
        for sel in (lambda r: r["model"] == m and r["fold"] == f, lambda r: r["fold"] == f, lambda r: r["model"] == m,
                    lambda r: True):
            v = [r["total"] for r in fin if sel(r)]
            if v:
                return statistics.median(v)
        return None

    unknown = 0
    for r in rows:
        if r["state"] == "done":
            continue
        if r["remaining"] is None:
            t = typical(r["model"], r["fold"])
            r["remaining"] = None if t is None else t * (1 - r["pct"])
        if r["remaining"] is None:
            unknown += 1
    n_done = sum(1 for r in rows if r["state"] == "done")
    rem_total = sum(r["remaining"] or 0 for r in rows if r["state"] != "done")
    frac = (n_done + sum(r["pct"] for r in rows if r["state"] != "done")) / max(len(rows), 1)
    summary = {"runs": len(rows), "done": n_done, "pct": frac, "remaining_sec": None if unknown == len(rows) - n_done and unknown else rem_total,
               "unknown_runs": unknown}
    return rows, summary


def overall_line(summary) -> str:
    rem = summary["remaining_sec"]
    extra = f" (+ 시간 미측정 {summary['unknown_runs']}회)" if summary["unknown_runs"] and rem is not None else ""
    return (f"[전체] {summary['done']}/{summary['runs']} 완료 {bar(summary['pct'], 30)} {summary['pct'] * 100:5.1f}% | "
            f"남은 시간 {fmt_dur(rem)}{extra} | 완료 예상 {finish_at(rem)}")
