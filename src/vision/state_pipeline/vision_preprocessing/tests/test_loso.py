"""LOSO experiment: fold rules, causal correction, determinism / resume, runner -> report -> figures (synthetic data)."""
from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import torch  # noqa: F401
    import timm  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

from vision_preprocessing.train.loso.temporal import causal_last, causal_with_reset, smooth


def _pav(y):
    """reference: full increasing isotonic regression (pool adjacent violators)."""
    blocks = [[v, 1.0] for v in y]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            m = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / (blocks[i][1] + blocks[i + 1][1])
            blocks[i] = [m, blocks[i][1] + blocks[i + 1][1]]
            del blocks[i + 1]
            i = max(0, i - 1)
        else:
            i += 1
    out = []
    for v, n in blocks:
        out += [v] * int(n)
    return out


class TestTemporal(unittest.TestCase):
    def test_causal_last_equals_last_value_of_isotonic_fit(self):
        rng = random.Random(0)
        for _ in range(200):
            y = [rng.random() for _ in range(rng.randint(1, 12))]
            got = causal_last(y)
            for t in range(len(y)):
                self.assertAlmostEqual(got[t], _pav(y[:t + 1])[-1], places=9)
            self.assertTrue(all(g >= v - 1e-12 for g, v in zip(got, y)))   # only ever corrects upwards

    def test_reset_restarts_after_sustained_drop(self):
        fit, ev = causal_with_reset([0.9, 0.95, 0.1, 0.1, 0.2, 0.3], drop=0.2, days=2)
        self.assertEqual(ev, [2])
        self.assertAlmostEqual(fit[3], 0.1)
        self.assertLess(fit[-1], 0.5)
        fit2, ev2 = causal_with_reset([0.5, 0.1, 0.6, 0.7], drop=0.2, days=2)   # one-day dip: no restart
        self.assertEqual(ev2, [])
        self.assertAlmostEqual(fit2[1], 0.3)                  # pooled with the earlier day, not dropped to 0.1

    def test_per_view_series_are_separate(self):
        rows = []
        for d, (a, b) in enumerate([(0.2, 0.8), (0.3, 0.8), (0.4, 0.9)]):
            for v, p in (("V1", a), ("V2", b)):
                rows.append({"surface": "wall", "target": "W1", "date": f"2025-01-0{d + 1}", "view": v, "roi_model": p,
                             "gt_ratio": 0.5, "pred_observed": 1.0, "conf": 1.0})
        pooled, _ = smooth(rows, "causal")
        per_view, _ = smooth(rows, "causal_view")
        self.assertAlmostEqual(per_view[("wall", "W1", "2025-01-01")], 0.5)
        self.assertAlmostEqual(per_view[("wall", "W1", "2025-01-03")], 0.65)
        self.assertAlmostEqual(pooled[("wall", "W1", "2025-01-03")], 0.65)


class TestProgress(unittest.TestCase):
    def test_bar_duration_and_overall_estimate(self):
        from vision_preprocessing.train.loso.progress import bar, fmt_dur, overall, overall_line
        from vision_preprocessing.train.loso.common import write_json
        self.assertEqual(bar(0.5, 10), "[#####.....]")
        self.assertEqual(fmt_dur(3 * 3600 + 5 * 60), "3시간 05분")
        self.assertEqual(fmt_dur(None), "측정 전")
        tmp = Path(tempfile.mkdtemp(prefix="loso_prog_"))
        try:
            write_json(tmp / "mlp/SITE_A/seed0/DONE.json", {"run_sec_total": 3600})
            write_json(tmp / "mlp/SITE_A/seed1/progress.json", {"pct": 0.5, "remaining_sec": 1800,
                                                                 "updated": __import__("time").time()})
            rows, summ = overall(tmp, [("mlp", "SITE_A", 0), ("mlp", "SITE_A", 1), ("mlp", "SITE_A", 2)], 15)
            self.assertEqual([r["state"] for r in rows], ["done", "running", "todo"])
            self.assertEqual(summ["remaining_sec"], 1800 + 3600)          # todo run estimated from the finished one
            self.assertAlmostEqual(summ["pct"], 0.5)
            self.assertIn("1/3", overall_line(summ))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestValWindow(unittest.TestCase):
    def test_calendar_blocks_and_buffer(self):
        from vision_preprocessing.train.loso.folds import ValWindow
        import datetime as dt
        days = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(0, 70, 3)]   # human every 3rd day
        w = ValWindow(days, 7, 5, 1, "val|X|0")
        cal = [(dt.date(2025, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(70)]
        val = [d for d in cal if w.is_val(d)]
        self.assertTrue(val and any(d in days for d in val))
        for d in cal:                                        # every calendar day next to a val day is buffer
            x = dt.date.fromisoformat(d)
            near = any(w.is_val((x + dt.timedelta(days=j)).isoformat()) for j in (-1, 1))
            self.assertEqual(w.is_buffer(d), (not w.is_val(d)) and near, d)


class TestFolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vision_preprocessing.tests.loso_fixtures import make_world
        from vision_preprocessing.train.loso.folds import prepare
        cls.tmp = Path(tempfile.mkdtemp(prefix="loso_folds_"))
        cls.w = make_world(cls.tmp)
        cls.built = prepare(cls.w["prepare"], cls.w["bundle"], cls.w["protocol"], expected_file=cls.w["expected"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rules(self):
        from vision_preprocessing.train.loso.folds import read_meta
        b, w = self.built, self.w
        cfg = json.loads(w["prepare"].read_text())
        ex = cfg["exports"]
        excl = set(cfg["exclude_ids"])
        date = {m["id"]: m["date"] for s in ex.values() for p in s.values() for m in read_meta(Path(p))}
        man = b["manifest"]
        for fold, f in b["folds"].items():
            test = set(f["test"]["ids"])
            trained = {i for d in ("train_human", "train_propagated", "train_weak") for v in f[d].values() for i in v}
            val = {i for v in f["val"].values() for i in v}
            self.assertFalse(test & (trained | val), fold)
            self.assertFalse(val & trained, fold)
            self.assertFalse(excl & (test | trained | val), fold)
            for d in ("train_human", "val", "train_propagated", "train_weak"):
                self.assertNotIn(fold, f[d])                      # nothing of the held-out site is used
            for s, ids in f["train_human"].items():               # val / buffer dates never train
                bad = set(man["val_dates"][s]["val"]) | set(man["val_dates"][s]["buffer"])
                self.assertFalse({date[i] for i in ids} & bad)
            for s, ids in f["train_propagated"].items():
                bad = set(man["val_dates"][s]["val"]) | set(man["val_dates"][s]["buffer"])
                self.assertFalse({date[i] for i in ids} & bad)
            self.assertFalse(set(f["val"]) & {"SITE_B", "SITE_D"})   # id-list sites are never val
        fa = b["folds"]["SITE_A"]
        self.assertEqual(len(fa["test"]["ids"]), len({date[i] for i in fa["test"]["ids"]}))     # one photo per date
        self.assertIsNotNone(b["folds"]["SITE_B"]["test"]["only_targets"])
        self.assertEqual(b["folds"]["SITE_B"]["exceptions"][0]["target"], "W01")

    def test_rebuild_is_identical_and_tamper_is_detected(self):
        from vision_preprocessing.train.loso.folds import build, load_fold
        from vision_preprocessing.train.loso.common import load_protocol
        again = build(load_protocol(self.w["protocol"]), json.loads(self.w["prepare"].read_text()))
        self.assertEqual({k: v["digests"] for k, v in again["folds"].items()},
                         {k: v["digests"] for k, v in self.built["folds"].items()})
        p = self.w["bundle"] / "folds" / "SITE_C.json"
        orig = p.read_text()
        d = json.loads(orig)
        d["test"]["ids"] = d["test"]["ids"][1:]
        p.write_text(json.dumps(d))
        with self.assertRaises(SystemExit):
            load_fold(self.w["bundle"], "SITE_C")
        p.write_text(orig)

    @unittest.skipUnless(HAVE, "torch/timm not installed")
    def test_check_detects_a_different_export(self):
        from vision_preprocessing.train.loso.cli import check
        self.assertEqual(check(self.w["local"], protocol_path=self.w["protocol"], expected_file=self.w["expected"], quiet=True), 0)
        md = Path(json.loads(self.w["prepare"].read_text())["exports"]["SITE_C"]["human"]) / "metadata.jsonl"
        orig = md.read_bytes()
        md.write_bytes(orig + b"\n")
        try:
            self.assertEqual(check(self.w["local"], protocol_path=self.w["protocol"], expected_file=self.w["expected"], quiet=True), 1)
        finally:
            md.write_bytes(orig)


@unittest.skipUnless(HAVE, "torch/timm not installed")
class TestTrainReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vision_preprocessing.tests.loso_fixtures import make_world
        from vision_preprocessing.train.loso.folds import prepare
        cls.tmp = Path(tempfile.mkdtemp(prefix="loso_train_"))
        cls.w = make_world(cls.tmp)
        P = json.loads(cls.w["protocol"].read_text())
        P["posteval"]["tta_scales"] = [1.0]
        cls.w["protocol"].write_text(json.dumps(P))
        prepare(cls.w["prepare"], cls.w["bundle"], cls.w["protocol"], expected_file=cls.w["expected"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _train(self, results, **k):
        from vision_preprocessing.train.loso.trainer import train_one
        return train_one(self.w["local"], "mlp", "SITE_B", 0, results, protocol_path=self.w["protocol"], pretrained=False,
                         limit=8, posteval=k.get("posteval", False))

    def test_same_seed_same_result_and_resume_is_exact(self):
        from vision_preprocessing.train.loso.common import read_csv
        a = self._train(self.tmp / "ra")
        b = self._train(self.tmp / "rb")
        os.environ["LOSO_TEST_STOP_AFTER_EPOCH"] = "1"
        try:
            with self.assertRaises(SystemExit):
                self._train(self.tmp / "rc")
        finally:
            del os.environ["LOSO_TEST_STOP_AFTER_EPOCH"]
        self.assertTrue((self.tmp / "rc/mlp/SITE_B/seed0/resume.pt").exists())
        c = self._train(self.tmp / "rc")                    # resumes after epoch 1
        self.assertFalse((c / "resume.pt").exists())
        ra, rb, rc = (read_csv(x / "photo_rows_base_test.csv") for x in (a, b, c))
        self.assertEqual([r["roi_model"] for r in ra], [r["roi_model"] for r in rb])
        self.assertEqual([r["roi_model"] for r in ra], [r["roi_model"] for r in rc])
        ea, ec = read_csv(a / "epochs.csv"), read_csv(c / "epochs.csv")
        self.assertEqual([r["loss"] for r in ea], [r["loss"] for r in ec])
        wa = torch.load(a / "final.pt", weights_only=False)["state_dict"]
        wc = torch.load(c / "final.pt", weights_only=False)["state_dict"]
        self.assertTrue(all(torch.equal(wa[k], wc[k]) for k in wa))
        for f in ("config.json", "environment.txt", "split_manifest.json", "steps.csv", "metrics.json", "resources.json",
                  "confusion_state_test.csv", "DONE.json"):
            self.assertTrue((a / f).exists(), f)

    def test_runner_report_figures(self):
        from vision_preprocessing.train.loso.cli import pack, plan, run, status
        from vision_preprocessing.train.loso.common import load_protocol
        from vision_preprocessing.train.loso.report import build_report
        P = load_protocol(self.w["protocol"])
        order = plan(P, ["linear", "mlp", "dpt"])
        self.assertTrue(all(m != "linear" for m, _, _ in order[:-4]))        # linear runs last
        res = self.tmp / "rr"
        rc = run(self.w["local"], ["mlp", "linear"], protocol_path=self.w["protocol"], expected_file=self.w["expected"],
                 extra=["--no-pretrained", "--limit", "6"], results=res)
        self.assertEqual(rc, 0)
        self.assertEqual(sum(1 for r in status(self.w["local"], ["mlp", "linear"], self.w["protocol"], results=res) if r[3] == "done"), 8)
        out = build_report([res], self.tmp / "summary", protocol_path=self.w["protocol"], allow_test_runs=True)
        self.assertFalse(build_report.__defaults__ is None)
        from vision_preprocessing.train.loso.report import find_runs
        self.assertEqual(find_runs([res], P), [])                           # test runs never reach paper tables
        for f in ("main_table.csv", "macro_table.csv", "pipeline_table.csv", "bootstrap.csv", "size_bins.csv", "resources.csv",
                  "curves_epoch.csv", "curves_step.csv", "summary.md", "figures/fig1_learning_curves.png",
                  "figures/fig2_fold_bars.pdf", "figures/fig5_pipelines.png", "figures/fig10_risk_coverage.png",
                  "risk_coverage.csv", "figures/fig3_roi_size.csv"):
            self.assertTrue((out / f).exists(), f)
        z = self.tmp / "pack.zip"
        pack(None, z, results=res)
        import zipfile
        names = zipfile.ZipFile(z).namelist()
        self.assertFalse(any(n.endswith(".jpg") or n.endswith(".pt") for n in names))     # no photos / weights by default
        self.assertTrue(any(n.endswith("metrics.json") for n in names))


if __name__ == "__main__":
    unittest.main()
