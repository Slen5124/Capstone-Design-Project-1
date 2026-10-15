"""Behavior checks for heartbeat, honest phase ETA, and sanitized status."""
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from console_progress import ProgressReporter, read_progress


TEMP_ROOT = Path(__file__).resolve().parent.parent / "tmp"
TEMP_ROOT.mkdir(exist_ok=True)


class ProgressTests(unittest.TestCase):
    def test_heartbeat_reports_stalled_work_without_false_completion(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            output = io.StringIO()
            with ProgressReporter(temporary, interval=.025, stream=output) as reporter:
                reporter.update(phase="index", done=0, total=100, current="huge.json")
                time.sleep(.09)
                state = read_progress(Path(temporary) / "progress.json")
                self.assertEqual(state["done"], 0)
                self.assertEqual(state["status"], "running")
                self.assertIsNone(state["eta_seconds"])
                self.assertGreaterEqual(len(output.getvalue().splitlines()), 3)
            final = json.loads((Path(temporary) / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(final["phase"], "index")
            self.assertEqual(final["status"], "running")

    def test_eta_is_for_current_phase_and_resets_on_transition(self):
        clock = [10.]
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            reporter = ProgressReporter(temporary, stream=io.StringIO(), clock=lambda: clock[0])
            reporter.update(phase="index", done=0, total=20)
            clock[0] += 2
            reporter.update(done=4)
            state = reporter.snapshot()
            self.assertEqual(state["rate_per_second"], 2)
            self.assertEqual(state["eta_seconds"], 8)
            self.assertEqual(state["eta_scope"], "current_phase")
            reporter.update(phase="repair", done=0, total=100)
            state = reporter.snapshot()
            self.assertIsNone(state["eta_seconds"])
            self.assertEqual(state["phase_elapsed_seconds"], 0)
            self.assertEqual(state["elapsed_seconds"], 2)
            clock[0] += 10
            reporter.update(done=2)
            self.assertIsNone(reporter.snapshot()["eta_seconds"])

    def test_resume_baseline_does_not_count_prior_run_throughput(self):
        clock = [10.]
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            reporter = ProgressReporter(temporary, stream=io.StringIO(), clock=lambda: clock[0])
            reporter.update(phase="repair", done=80, total=100)
            clock[0] += 2
            self.assertIsNone(reporter.snapshot()["eta_seconds"])
            reporter.update(done=84)
            self.assertEqual(reporter.snapshot()["rate_per_second"], 2)

    def test_atomic_json_log_secret_redaction_and_finish(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            secret = "not-a-real-test-credential"
            with patch.dict(os.environ, {"QA_ZIP_PASSWORD": secret}):
                output = io.StringIO()
                reporter = ProgressReporter(temporary, stream=output)
                reporter.update(phase="repair", done=2, total=4,
                                current=f"BR01\n{secret}\x1b[31m.jpg",
                                classes={"auto_fixed": 1, "needs_review": 1},
                                actions={"wall_restore": 1}, export_bytes=2048,
                                message="password=another-test-secret")
                reporter.start()
                reporter.finish(status="interrupted")
            root = Path(temporary)
            state_text = (root / "progress.json").read_text(encoding="utf-8")
            log = (root / "console.log").read_text(encoding="utf-8")
            self.assertNotIn(secret, state_text + log + output.getvalue())
            self.assertNotIn("another-test-secret", state_text + log)
            state = json.loads(state_text)
            self.assertEqual(state["status"], "interrupted")
            self.assertEqual(state["done"], 2)
            self.assertEqual(state["percent"], 50)
            self.assertNotIn("\n", state["current"])
            self.assertFalse((root / ".progress.json.tmp").exists())
            self.assertEqual(log.count("\n"), 2)

    def test_terminal_encoding_fallback_keeps_status_log(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

            def write(self, value):
                value.encode("ascii")
                return super().write(value)

        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            output = AsciiStream()
            with ProgressReporter(temporary, stream=output) as reporter:
                reporter.update(current="벽사진.jpg")
            self.assertIn("\\ubcbd", output.getvalue())
            state = json.loads((Path(temporary) / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current"], "벽사진.jpg")

    def test_free_space_and_unknown_total_are_explicit(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            reporter = ProgressReporter(temporary, stream=io.StringIO(),
                                        disk_usage=lambda _: SimpleNamespace(free=123456))
            state = reporter.snapshot()
            self.assertEqual(state["free_bytes"], 123456)
            self.assertIsNone(state["total"])
            self.assertIsNone(state["percent"])
            self.assertIsNone(state["rate_per_second"])

    def test_durable_checkpoint_count_is_separate_and_survives_phase_changes(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            output = io.StringIO()
            with ProgressReporter(temporary, stream=output) as reporter:
                reporter.update(phase="repair", done=5, total=10, checkpoint_done=3)
                self.assertEqual(reporter.snapshot()["checkpoint_done"], 3)
                reporter.update(phase="export", done=0, total=1)
                self.assertEqual(reporter.snapshot()["checkpoint_done"], 3)
                with self.assertRaises(ValueError):
                    reporter.update(checkpoint_done=-1)
            self.assertEqual(read_progress(Path(temporary) / "progress.json")["checkpoint_done"], 3)
            self.assertIn("checkpoint=3", output.getvalue())

    def test_context_error_does_not_log_exception_payload(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            with self.assertRaises(ValueError):
                with ProgressReporter(temporary, stream=io.StringIO()):
                    raise ValueError("password=exception-secret")
            state_text = (Path(temporary) / "progress.json").read_text(encoding="utf-8")
            self.assertNotIn("exception-secret", state_text)
            self.assertEqual(json.loads(state_text)["status"], "error")

    def test_concurrent_readers_and_atomic_heartbeat_replacements(self):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            output = io.StringIO()
            path = Path(temporary) / "progress.json"
            with ProgressReporter(temporary, interval=.001, stream=output) as reporter:
                reporter.update(phase="repair", done=0, total=150)
                for done in range(1, 151):
                    reporter.update(done=done, current=f"photo_{done}.jpg")
                    state = read_progress(path)
                    self.assertIn(state["phase"], {"prepare", "repair"})
                    self.assertEqual(state["status"], "running")
                    self.assertLessEqual(state["done"], done)
                    time.sleep(.001)
                reporter.finish(status="complete")
            final = read_progress(path)
            self.assertEqual(final["done"], 150)
            self.assertEqual(final["status"], "complete")
            self.assertGreaterEqual(len(output.getvalue().splitlines()), 3)
            self.assertNotIn("write failed", output.getvalue())

    def test_atomic_writer_retries_only_bounded_sharing_collisions(self):
        original_replace = Path.replace
        calls = []

        def collision_then_replace(path, target):
            calls.append(target)
            if len(calls) <= 2:
                raise PermissionError("simulated Windows reader sharing collision")
            return original_replace(path, target)

        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as temporary:
            with patch.object(Path, "replace", collision_then_replace):
                reporter = ProgressReporter(temporary, stream=io.StringIO())
                reporter.start()
                reporter.finish(status="interrupted")
            self.assertEqual(len(calls), 4)
            self.assertEqual(read_progress(Path(temporary) / "progress.json")["status"], "interrupted")

    def test_reader_retries_sharing_but_retains_corruption_and_denial(self):
        path = Path("progress.json")
        with patch.object(Path, "read_text", side_effect=[PermissionError("sharing"), '{"done": 3}']):
            self.assertEqual(read_progress(path, delay=0), {"done": 3})
        with patch.object(Path, "read_text", return_value="invalid JSON") as reader:
            with self.assertRaises(json.JSONDecodeError):
                read_progress(path, delay=0)
            self.assertEqual(reader.call_count, 1)
        with patch.object(Path, "read_text", side_effect=PermissionError("persistent denied")) as reader:
            with self.assertRaises(PermissionError):
                read_progress(path, attempts=3, delay=0)
            self.assertEqual(reader.call_count, 3)


if __name__ == "__main__":
    unittest.main()
