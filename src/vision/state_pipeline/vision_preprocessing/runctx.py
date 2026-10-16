"""Run directories, resolved configuration snapshot, progress and resume caches."""
from __future__ import annotations

import json
import os
import platform
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator

from . import SCHEMA_VERSION
from .schemas import ConfigError, content_hash, load_config, sha256_file, validate_site, validate_taxonomy
from .store import ReviewStore

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = PACKAGE_DIR / "config" / "taxonomy.json"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):  # Windows readers may hold the old file briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
    os.replace(tmp, path)


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A truncated last line after a crash is skipped and recomputed.
                    continue


# ------------------------------------------------------------------ paths
def parse_path_map(items: list[str] | None, config_map: dict | None) -> list[tuple[str, str]]:
    pairs = list((config_map or {}).items())
    for item in items or []:
        if "=" not in item:
            raise ConfigError(f"--path-map expects FROM=TO, got {item!r}")
        a, b = item.split("=", 1)
        pairs.append((a, b))
    # Longest prefix first.
    return sorted(pairs, key=lambda p: -len(p[0]))


def auto_relocation_map(site: dict, path_map: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Make the project movable: if the configured project root does not exist here (another computer or
    drive), map it to the folder this package actually lives in, and its parent (where Data/ sits next to
    the project folder) to this package's grandparent. Explicit path_map entries always win."""
    root = (site.get("paths") or {}).get("preprocess_root")
    if not root or Path(map_path(root, path_map)).exists():
        return path_map
    here = PACKAGE_DIR.parent                     # ...\전처리된 데이터 (folder holding vision_preprocessing)
    pure = PureWindowsPath(root) if "\\" in root or ":" in root[:3] else PurePosixPath(root)
    extra = [(root, str(here)), (str(pure.parent), str(here.parent))]
    have = {a.casefold() for a, _ in path_map}
    merged = list(path_map) + [(a, b) for a, b in extra if a.casefold() not in have]
    return sorted(merged, key=lambda p: -len(p[0]))


FIRST_STAGE_MARKER = "FIRST_STAGE_RUN.json"   # written by tools/rename_first_stage_run.py


def _leaf(p: str) -> str:
    return (PureWindowsPath(p) if "\\" in p or ":" in p[:3] else PurePosixPath(p)).name


def _read_marker(run_dir: Path) -> dict | None:
    m = Path(run_dir) / FIRST_STAGE_MARKER
    try:
        return json.loads(m.read_text(encoding="utf-8")) if m.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def find_renamed_first_stage_run(results_dir: Path, original: str) -> Path | None:
    """A stage-1 (qa_v3) run folder that was renamed keeps its original name in FIRST_STAGE_RUN.json."""
    name = _leaf(original)
    if not Path(results_dir).is_dir():
        return None
    for d in sorted(Path(results_dir).iterdir()):
        doc = _read_marker(d) if d.is_dir() else None
        if doc and name in [doc.get("original_run_name"), doc.get("renamed_to")] + [_leaf(x) for x in doc.get("original_run_dirs", [])]:
            return d
    return None


def first_stage_rename_map(source_run: Path) -> list[tuple[str, str]]:
    """Paths inside a renamed run's manifest still name the old folder; map them to the folder as it is now."""
    doc = _read_marker(source_run) or {}
    return [(o, str(source_run)) for o in doc.get("original_run_dirs", []) if o]


def _merge_map(path_map, extra):
    have = {a.casefold() for a, _ in path_map}
    return sorted(list(path_map) + [(a, b) for a, b in extra if a.casefold() not in have], key=lambda p: -len(p[0]))


def map_path(value: str, path_map: list[tuple[str, str]]) -> str:
    """Translate a path recorded on another OS (e.g. D:\\...) to the local one."""
    if value is None:
        return value
    for src, dst in path_map:
        if value.casefold().startswith(src.casefold()):
            rest = value[len(src):].lstrip("\\/")
            parts = PureWindowsPath(rest).parts if "\\" in rest else PurePosixPath(rest).parts
            return str(Path(dst).joinpath(*parts))
    return value


# --------------------------------------------------------------- run context
class RunContext:
    """A unique output run directory plus resolved, snapshotted configuration."""

    def __init__(self, run_dir: Path, info: dict):
        self.run_dir = Path(run_dir)
        self.info = info
        self.site = info["site_config"]
        self.taxonomy = info["taxonomy"]
        self.path_map = [tuple(p) for p in info["resolved"]["path_map"]]
        self._store = None

    # -- path resolution (kept separate so a run made on one machine can be reopened on another)
    @staticmethod
    def _resolve(site: dict, path_map, output_root=None, store_path=None, source_run_original=None) -> dict:
        paths = site.get("paths", {})
        root = Path(map_path(paths["preprocess_root"], path_map))
        pointer = root / paths.get("latest_run_pointer", "automatic_results/latest_active_run.json")
        pointer_content = None
        if source_run_original is None:
            if not pointer.exists():
                raise ConfigError(f"latest run pointer not found: {pointer}")
            pointer_content = json.loads(pointer.read_text(encoding="utf-8"))
            if pointer_content.get("status") not in (None, "complete"):
                raise ConfigError(f"pointer run status is {pointer_content.get('status')!r}, not complete")
            source_run_original = pointer_content.get("run_dir") or pointer_content["path"]
        source_run = Path(map_path(source_run_original, path_map))
        if not (source_run / "manifest.jsonl").exists():
            renamed = find_renamed_first_stage_run(root / "automatic_results", source_run_original)
            if renamed is not None:
                source_run = renamed
        path_map = _merge_map(path_map, first_stage_rename_map(source_run))
        if not (source_run / "manifest.jsonl").exists():
            raise ConfigError(f"source run lacks manifest.jsonl: {source_run}")
        out = Path(output_root) if output_root else Path(map_path(paths.get("output_root") or "state_preprocessing_results", path_map))
        if not out.is_absolute():
            out = root / out
        spec = str(store_path) if store_path else paths.get("store") or f"state_preprocessing_results/review_store/{site['site_id']}.sqlite"
        store = Path(map_path(spec, path_map))
        if not store.is_absolute():
            store = root / store
        return {"preprocess_root": str(root), "latest_run_pointer": str(pointer),
                "latest_run_pointer_content": pointer_content, "source_run_dir_original": source_run_original,
                "source_run_dir": str(source_run), "output_root": str(out), "store_spec": spec,
                "store_path": str(store), "path_map": [list(p) for p in path_map]}

    # -- creation
    @classmethod
    def create(cls, config_path: Path, *, output_root: Path | None = None, taxonomy_path: Path | None = None,
               path_map_args: list[str] | None = None, store_path: Path | None = None, argv=None,
               label: str | None = None) -> "RunContext":
        config_path = Path(config_path).resolve()
        site = load_config(config_path)
        taxonomy_path = Path(taxonomy_path or site.get("taxonomy") or DEFAULT_TAXONOMY)
        if not taxonomy_path.is_absolute():
            taxonomy_path = (config_path.parent / taxonomy_path).resolve()
        taxonomy = load_config(taxonomy_path)
        errors = validate_taxonomy(taxonomy) + validate_site(site, taxonomy)
        if errors:
            raise ConfigError("; ".join(errors))
        path_map = auto_relocation_map(site, parse_path_map(path_map_args, site.get("path_map")))
        resolved = cls._resolve(site, path_map, output_root, store_path)
        root = Path(resolved["preprocess_root"])
        run_id = f"run_{utc_stamp()}_{secrets.token_hex(3)}" + (f"_{label}" if label else "")
        run_dir = Path(resolved["output_root"]) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)  # never reuse/overwrite an existing run
        resolved["source_manifest_fingerprint"] = _head_hash(Path(resolved["source_run_dir"]) / "manifest.jsonl")
        info = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "argv": list(argv or sys.argv),
            "config_path": str(config_path),
            "config_sha256": sha256_file(config_path),
            "taxonomy_path": str(taxonomy_path),
            "taxonomy_sha256": sha256_file(taxonomy_path),
            "site_config": site,
            "taxonomy": taxonomy,
            "resolved": resolved,
            "environment": environment_info(root),
            "code_sha256": code_hashes(root),
        }
        atomic_write_json(run_dir / "run_config.json", info)
        return cls(run_dir, info)

    @classmethod
    def open(cls, run_dir: Path, path_map_args: list[str] | None = None) -> "RunContext":
        """Reopen a run. The recorded source run (not the *current* pointer) is used, so results stay
        reproducible; paths are re-translated if the run was created on another machine."""
        run_dir = Path(run_dir)
        path = run_dir / "run_config.json"
        if not path.exists():
            raise ConfigError(f"not a run directory: {run_dir}")
        info = json.loads(path.read_text(encoding="utf-8"))
        site, res = info["site_config"], info["resolved"]
        if path_map_args:
            candidates = [parse_path_map(path_map_args, site.get("path_map"))]
        else:
            candidates = [[tuple(p) for p in res["path_map"]], parse_path_map(None, site.get("path_map")),
                          auto_relocation_map(site, parse_path_map(None, site.get("path_map")))]
        for pm in candidates:
            if Path(map_path(site["paths"]["preprocess_root"], pm)).exists():
                break
        else:
            raise ConfigError("preprocess_root not reachable on this machine; pass --path-map FROM=TO")
        if [list(p) for p in pm] != res["path_map"] or not Path(res["source_run_dir"]).exists():
            new = cls._resolve(site, pm, None, res.get("store_spec"),
                               res.get("source_run_dir_original") or res["source_run_dir"])
            info["resolved"] = dict(res, **{k: new[k] for k in ("preprocess_root", "source_run_dir", "store_path",
                                                                "path_map", "latest_run_pointer")})
            info["resolved"]["reopened_with_path_map"] = True
        return cls(run_dir, info)

    # -- helpers
    @property
    def source_run_dir(self) -> Path:
        return Path(self.info["resolved"]["source_run_dir"])

    @property
    def preprocess_root(self) -> Path:
        return Path(self.info["resolved"]["preprocess_root"])

    def map(self, value: str) -> str:
        return map_path(value, self.path_map)

    def store(self) -> ReviewStore:
        if self._store is None:
            self._store = ReviewStore(self.info["resolved"]["store_path"], self.site["site_id"])
        return self._store

    def stage_dir(self, name: str) -> Path:
        path = self.run_dir / "stages" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def config_fingerprint(self, *sections: str, code: tuple[str, ...] | None = None) -> str:
        """Fingerprint of config sections + taxonomy + current code.

        ``code`` limits the code part to the modules a stage actually depends on (file names inside
        this package, or "qa_v3/..."/"qa_v2/..." paths), so that e.g. editing the review UI does not
        invalidate the ingest cache of 10k photos. ``None`` = every module (most conservative).
        """
        parts = {s: self.site.get(s) for s in sections}
        parts["taxonomy"] = self.taxonomy
        if getattr(self, "_code_now", None) is None:
            self._code_now = code_hashes(self.preprocess_root)  # current code: code edits invalidate caches
        if code is None:
            parts["code"] = self._code_now
        else:
            wanted = {c if "/" in c else f"vision_preprocessing/{c}" for c in code}
            parts["code"] = {k: v for k, v in self._code_now.items() if k in wanted}
        return content_hash(parts)

    def check_config_drift(self) -> dict:
        """Compare snapshot with the config file now on disk (reported, not applied)."""
        path = Path(self.info["config_path"])
        now = sha256_file(path) if path.exists() else None
        return {"config_changed_since_run_created": now != self.info["config_sha256"], "current_sha256": now}


def _head_hash(path: Path, size: int = 1 << 20) -> dict:
    stat = path.stat()
    with open(path, "rb") as handle:
        head = handle.read(size)
    import hashlib
    return {"bytes": stat.st_size, "mtime": stat.st_mtime, "sha256_first_MiB": hashlib.sha256(head).hexdigest()}


def environment_info(root: Path) -> dict:
    versions = {}
    for module in ("numpy", "PIL", "shapely", "cv2", "pyzipper"):
        try:
            versions[module] = getattr(__import__(module), "__version__", "present")
        except Exception:
            versions[module] = None
    return {"python": sys.version.split()[0], "executable": sys.executable, "platform": platform.platform(),
            "packages": versions}


def code_hashes(root: Path) -> dict:
    out = {}
    for path in sorted(PACKAGE_DIR.glob("*.py")) + sorted((PACKAGE_DIR / "config").glob("*.json")):
        out[f"vision_preprocessing/{path.relative_to(PACKAGE_DIR).as_posix()}"] = sha256_file(path)
    for rel in ("qa_v3/dataset_io.py", "qa_v2/registration.py"):
        p = root / rel
        out[rel] = sha256_file(p) if p.exists() else None
    return out


# -------------------------------------------------------------------- progress
class Progress:
    """Console + progress.json reporting. Counts are per *unit* (images, observations, labels)."""

    def __init__(self, ctx: RunContext | None, stage: str, total: int, unit: str = "images", every: int = 25):
        self.ctx, self.stage, self.total, self.unit, self.every = ctx, stage, total, unit, every
        self.counts = {"processed": 0, "saved": 0, "skipped_cached": 0, "held": 0, "failed": 0}
        self.start = time.time()
        self._last = 0.0

    def tick(self, outcome: str = "saved", n: int = 1):
        self.counts["processed"] += n
        if outcome != "processed":
            self.counts[outcome] = self.counts.get(outcome, 0) + n
        done = self.counts["processed"]
        if done % self.every == 0 or done == self.total or time.time() - self._last > 10:
            self.report()

    def report(self, final: bool = False):
        self._last = time.time()
        elapsed = time.time() - self.start
        done = self.counts["processed"]
        rate = done / elapsed if elapsed > 0 else 0
        eta = (self.total - done) / rate if rate > 0 and self.total else None
        line = (f"[{self.stage}] {done}/{self.total} {self.unit} | saved {self.counts['saved']} | cached "
                f"{self.counts['skipped_cached']} | held {self.counts['held']} | failed {self.counts['failed']}"
                f" | {elapsed:.0f}s" + (f" | ETA {eta:.0f}s" if eta is not None and not final else ""))
        print(line, flush=True)
        if self.ctx is not None:
            path = self.ctx.run_dir / "progress.json"
            try:
                current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except json.JSONDecodeError:
                current = {}
            current[self.stage] = {"unit": self.unit, "total": self.total, **self.counts,
                                   "elapsed_s": round(elapsed, 1), "final": final,
                                   "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            atomic_write_json(path, current)

    def finish(self) -> dict:
        self.report(final=True)
        return dict(self.counts, total=self.total, unit=self.unit)


class ItemCache:
    """Append-only jsonl cache keyed by item key with an input fingerprint.

    A cached item is reused only when its fingerprint (inputs + config + code +
    upstream versions) is identical; anything else is recomputed (invalidation).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: dict[str, dict] = {}
        self.rows_in_file = 0
        for row in read_jsonl(self.path):
            self.items[row["_key"]] = row
            self.rows_in_file += 1
        self._handle = None

    def get(self, key: str, fingerprint: str) -> dict | None:
        row = self.items.get(key)
        if row is not None and row.get("_fp") == fingerprint:
            return row["value"]
        return None

    def put(self, key: str, fingerprint: str, value: Any):
        row = {"_key": key, "_fp": fingerprint, "value": value}
        self.items[key] = row
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.path, "a", encoding="utf-8")
        self._handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._handle.flush()
        self.rows_in_file += 1

    def values(self) -> list:
        return [row["value"] for row in self.items.values()]

    def close(self):
        if self._handle:
            self._handle.close()
            self._handle = None

    def compact(self):
        """Rewrite with only the latest row per key (atomic replace)."""
        self.close()
        tmp = self.path.with_name(self.path.name + ".compact")
        with open(tmp, "w", encoding="utf-8") as handle:
            for row in self.items.values():
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        for attempt in range(20):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                time.sleep(0.1 * (attempt + 1))
        else:
            os.replace(tmp, self.path)
        self.rows_in_file = len(self.items)

    def maybe_compact(self, ratio: float = 1.5, min_rows: int = 200) -> bool:
        """Compact when superseded rows make the file much larger than needed (append-only growth)."""
        if self.rows_in_file > max(min_rows, ratio * max(1, len(self.items))):
            self.compact()
            return True
        return False


# ------------------------------------------------------------------ run lock
class RunLockError(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid exists on THIS machine (never sends a signal on Windows)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """One writer per run directory (auto / assign / review server ...).

    The lock is a small JSON file created with O_EXCL. A lock left behind by a
    crashed process on the same machine is taken over automatically; a lock from
    another machine (e.g. the run folder opened from a VM) is only broken with
    ``force=True``.
    """

    def __init__(self, run_dir: Path, owner: str, force: bool = False):
        self.path = Path(run_dir) / "RUN_LOCK.json"
        self.owner, self.force = owner, force
        self.held = False
        self.reentrant = False

    def info(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def acquire(self) -> "RunLock":
        me = {"owner": self.owner, "pid": os.getpid(), "host": platform.node(),
              "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for _ in range(3):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                cur = self.info() or {}
                same_host = cur.get("host") == me["host"]
                if cur and same_host and cur.get("pid") == me["pid"]:
                    self.held, self.reentrant = True, True   # nested use in one process: outer holder releases
                    return self
                dead = same_host and not _pid_alive(int(cur.get("pid") or 0))
                if not cur or dead or self.force:
                    self._break()
                    continue
                raise RunLockError(
                    f"run is in use by '{cur.get('owner')}' (pid {cur.get('pid')} on {cur.get('host')}, since "
                    f"{cur.get('started_utc')}). Stop it first (Ctrl+C in its window). If that process no longer "
                    f"exists, rerun with --force-unlock.")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(me, handle)
            self.held = True
            return self
        raise RunLockError(f"could not acquire {self.path}")

    def _break(self):
        """Remove a stale lock; on folders where deletion is not permitted, overwrite it instead."""
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except PermissionError:
            stale = self.path.with_name(f"RUN_LOCK.stale.{secrets.token_hex(3)}.json")
            os.replace(self.path, stale)

    def release(self):
        if not self.held or self.reentrant:
            self.held = False
            return
        cur = self.info() or {}
        if cur.get("pid") == os.getpid() and cur.get("host") == platform.node():
            try:
                os.remove(self.path)
            except PermissionError:
                os.replace(self.path, self.path.with_name(f"RUN_LOCK.released.{secrets.token_hex(3)}.json"))
            except FileNotFoundError:
                pass
        self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
