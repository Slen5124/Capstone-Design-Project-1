"""Thread-safe terminal heartbeat and resumable-run status, using only stdlib.

The reporter observes counters supplied by the batch. It never processes data or
changes batch state. ETA always describes the current phase, not the whole job.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time


def _number(value, *, integer=False):
    if value is None:
        return None
    result = int(value) if integer else float(value)
    if result < 0 or not math.isfinite(result):
        raise ValueError("Progress counters must be finite and nonnegative")
    return result


def _duration(seconds):
    if seconds is None:
        return "--"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _bytes(value):
    if value is None:
        return "--"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f}{unit}"
        value /= 1024


def _retry_snapshot_io(operation, *, attempts=8, delay=.01, allow_missing=False):
    """Retry bounded Windows rename/read sharing races; retain real failures.

    Windows can briefly deny a reader while a replaced file is delete-pending,
    or deny the replacement while a reader still holds the previous file open.
    Sleeping here is limited to 0.39 seconds with the default arguments. A real
    permission failure remains an error after those bounded retries.
    """
    attempts = int(attempts)
    delay = float(delay)
    if attempts < 1 or delay < 0 or not math.isfinite(delay):
        raise ValueError("Snapshot retries require positive attempts and finite nonnegative delay")
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as error:
            sharing = (isinstance(error, PermissionError)
                       or getattr(error, "winerror", None) in {5, 32, 33})
            missing = allow_missing and isinstance(error, FileNotFoundError)
            if not (sharing or missing) or attempt == attempts - 1:
                raise
            time.sleep(min(.08, delay * 2 ** attempt))


def read_progress(path, *, attempts=8, delay=.01):
    """Read an atomic status file despite short Windows sharing collisions.

    Missing/inaccessible files remain errors once retries are exhausted. Invalid
    JSON is never treated as a sharing collision or silently replaced by a fake
    empty/completed state.
    """
    path = Path(path)
    text = _retry_snapshot_io(lambda: path.read_text(encoding="utf-8"),
                              attempts=attempts, delay=delay, allow_missing=True)
    return json.loads(text)


class ProgressReporter:
    """Print a heartbeat, persist progress.json, and append console.log.

    ``update`` changes only a locked snapshot. A background observer emits it at
    ``interval`` seconds even if the pipeline is blocked inside a long operation.
    The caller owns counts and decides whether a run actually completed.
    ``close`` never marks an unfinished run complete.
    """

    def __init__(self, run_dir, interval=2.0, *, stream=None, secrets=(),
                 clock=None, disk_usage=None):
        self.run_dir = Path(run_dir)
        self.interval = float(interval)
        if not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("Progress interval must be positive")
        self.stream = sys.stdout if stream is None else stream
        self._clock = clock or time.monotonic
        self._disk_usage = disk_usage or shutil.disk_usage
        self._secrets = tuple(str(v) for v in
                              (*secrets, os.environ.get("QA_ZIP_PASSWORD", "")) if v)
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._closed = False
        self._started = self._clock()
        self._phase_started = self._started
        self._baseline_done = 0
        self._state = {"phase": "prepare", "status": "running", "done": 0,
                       "checkpoint_done": 0,
                       "total": None, "classes": {}, "actions": {},
                       "current": "", "export_bytes": 0, "free_bytes": None,
                       "message": ""}
        self._reported_error = False

    def _clean(self, value, limit=160):
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)\b(password|token|secret|authorization)\s*[:=]\s*\S+",
                      r"\1=[REDACTED]", text)
        # Filenames and messages must not inject additional terminal/log lines.
        text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
        return text[:limit]

    def _counts(self, counts):
        if not isinstance(counts, Mapping):
            raise TypeError("Progress counts must be a mapping")
        return {self._clean(key, 80): _number(value, integer=True)
                for key, value in counts.items()}

    def update(self, *, phase=None, done=None, total=None, classes=None,
               actions=None, current=None, export_bytes=None, free_bytes=None,
               status=None, message=None, checkpoint_done=None):
        """Update observed state. Unspecified fields keep their previous value.

        A phase change resets its rate baseline. The run's overall elapsed time
        is preserved. Use ``total=0`` for an explicitly empty phase; initially
        unknown totals are represented by ``None`` in progress.json.
        """
        with self._lock:
            if self._closed:
                return
            changed_phase = phase is not None and str(phase) != self._state["phase"]
            if changed_phase:
                self._state["phase"] = self._clean(phase, 40)
                self._state["done"] = 0 if done is None else _number(done, integer=True)
                self._state["total"] = None
                self._state["current"] = ""
                self._state["message"] = ""
                self._phase_started = self._clock()
                self._baseline_done = self._state["done"]
            for key, value in (("done", done), ("total", total),
                               ("checkpoint_done", checkpoint_done),
                               ("export_bytes", export_bytes), ("free_bytes", free_bytes)):
                if value is not None:
                    self._state[key] = _number(value, integer=True)
            if classes is not None:
                self._state["classes"] = self._counts(classes)
            if actions is not None:
                self._state["actions"] = self._counts(actions)
            for key, value in (("current", current), ("status", status), ("message", message)):
                if value is not None:
                    self._state[key] = self._clean(value, 120 if key == "current" else 160)
            if changed_phase:
                self._wake.set()

    def snapshot(self):
        with self._lock:
            state = dict(self._state)
            state["classes"] = dict(state["classes"])
            state["actions"] = dict(state["actions"])
            now = self._clock()
            phase_elapsed = max(0., now - self._phase_started)
            completed = max(0, state["done"] - self._baseline_done)
            # A few completed items and a real observation period are needed
            # before reporting a rate. Repeated stalled heartbeats count zero.
            meaningful = completed >= 3 and phase_elapsed >= 1.0
            rate = completed / phase_elapsed if meaningful else None
            total = state["total"]
            state["percent"] = (min(100., 100. * state["done"] / total)
                                if total else None)
            state["rate_per_second"] = rate
            state["eta_seconds"] = (max(0., total - state["done"]) / rate
                                    if rate and total is not None else None)
            state["eta_scope"] = "current_phase"
            state["phase_elapsed_seconds"] = phase_elapsed
            state["elapsed_seconds"] = max(0., now - self._started)
        if state["free_bytes"] is None:
            try:
                state["free_bytes"] = int(self._disk_usage(self.run_dir).free)
            except OSError:
                pass
        state["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return state

    def _line(self, state):
        total = "?" if state["total"] is None else str(state["total"])
        percent = "--" if state["percent"] is None else f'{state["percent"]:.1f}%'
        rate = "--" if state["rate_per_second"] is None else f'{state["rate_per_second"]:.2f}/s'
        classes = ",".join(f"{key}:{value}" for key, value in state["classes"].items()) or "--"
        actions = ",".join(f"{key}:{value}" for key, value in state["actions"].items()) or "--"
        # UTC is explicit so clocks in status files can be compared across runs.
        return (f'[{state["timestamp"]}] phase={state["phase"]} status={state["status"]} '
                f'{state["done"]}/{total} ({percent}) elapsed={_duration(state["elapsed_seconds"])} '
                f'checkpoint={state["checkpoint_done"]} '
                f'rate={rate} phase_ETA={_duration(state["eta_seconds"])} '
                f'classes=[{classes}] actions=[{actions}] '
                f'export={_bytes(state["export_bytes"])} free={_bytes(state["free_bytes"])} '
                f'current={state["current"] or "--"}'
                + (f' message={state["message"]}' if state["message"] else ""))

    def _emit(self):
        with self._io_lock:
            state = self.snapshot()
            line = self._clean(self._line(state), 4000)
            self.run_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.run_dir / ".progress.json.tmp"
            # replace() is atomic on the same filesystem: a reader sees either
            # the previous complete JSON document or the new complete document.
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
            _retry_snapshot_io(lambda: temporary.replace(self.run_dir / "progress.json"))
            with (self.run_dir / "console.log").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
            try:
                self.stream.write(line + "\n")
                self.stream.flush()
            except UnicodeEncodeError:
                # Legacy CMD code pages still get counters and readable escapes.
                safe = line.encode(getattr(self.stream, "encoding", None) or "ascii",
                                   errors="backslashreplace").decode(
                                       getattr(self.stream, "encoding", None) or "ascii")
                self.stream.write(safe + "\n")
                self.stream.flush()
            except (BrokenPipeError, OSError):
                # A detached terminal does not stop the data pipeline or logs.
                pass

    def _heartbeat(self):
        while not self._stop.is_set():
            self._wake.wait(self.interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self._emit()
            except OSError:
                # A disk/log failure must not crash a background thread silently
                # or turn into a fake completed batch. The batch owns disk policy.
                if not self._reported_error:
                    self._reported_error = True
                    try:
                        self.stream.write("Progress log write failed; batch counters remain available.\n")
                        self.stream.flush()
                    except (OSError, UnicodeEncodeError):
                        pass

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Progress reporter has already closed")
            if self._thread is not None:
                return self
            self._emit()
            self._wake.clear()
            self._thread = threading.Thread(target=self._heartbeat,
                                            name="annotation-progress", daemon=True)
            self._thread.start()
        return self

    def close(self):
        """Stop observing and persist the final observed state, without success."""
        with self._lock:
            if self._closed:
                return
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._emit()
        with self._lock:
            self._closed = True

    def finish(self, status="complete", *, message=None):
        with self._lock:
            if self._closed:
                return
            self._state["status"] = self._clean(status, 40)
            if status == "complete":
                self._state["phase"] = "complete"
            if message is not None:
                self._state["message"] = self._clean(message)
        self.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None and not self._closed:
            # Class name is useful, but exception text may contain credentials.
            self.finish(status="error", message=exc_type.__name__)
        else:
            self.close()
        return False
