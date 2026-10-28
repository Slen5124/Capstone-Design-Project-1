"""Shared helpers of the LOSO experiment: protocol / local config, hashing, determinism, small file I/O.

Nothing here knows real site names or paths: those live only in the private bundle and the local config
(local.json, never committed)."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL_FILE = HERE / "protocol.json"
EXPECTED_DIGESTS_FILE = HERE / "expected_digests.json"
CODE_FILES = ("train_quick.py", "data_prep.py", "decoders.py", "loso/common.py", "loso/folds.py", "loso/trainer.py",
              "loso/posteval.py", "loso/temporal.py", "loso/report.py", "loso/figures.py", "loso/cli.py", "loso/progress.py")


class ProtocolError(SystemExit):
    pass


def load_protocol(path: Path | None = None) -> dict:
    p = Path(path) if path else PROTOCOL_FILE
    d = json.loads(p.read_text(encoding="utf-8"))
    # hash of the CONTENT (canonical JSON), so a CRLF checkout on Windows gives the same value as LF on Linux
    d["_sha256"] = sha256_bytes(json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return d


def load_local(path: Path) -> dict:
    """local.json: {"bundle": dir, "results": dir (optional), "exports": {SITE_X: {kind: dir}}}. Relative paths are
    relative to the local.json file."""
    path = Path(path)
    if not path.exists():
        raise ProtocolError(f"local config not found: {path}  (copy local.example.json from the bundle and fill in the paths)")
    d = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    def res(v):
        q = Path(os.path.expanduser(str(v)))
        return q if q.is_absolute() else (base / q).resolve()

    if not d.get("results") or str(d["results"]).startswith("<"):
        raise ProtocolError(f"{path}: set \"results\" (a folder OUTSIDE the git repository for the run outputs)")
    if not d.get("bundle") or str(d["bundle"]).startswith("<"):
        raise ProtocolError(f"{path}: set \"bundle\" (the unzipped private bundle folder)")
    out = {"bundle": res(d["bundle"]), "results": res(d["results"]),
           "exports": {s: {k: res(v) for k, v in kinds.items()} for s, kinds in (d.get("exports") or {}).items()},
           "_path": str(path.resolve())}
    return out


# ------------------------------------------------------------------ hashing
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path, chunk=1 << 20) -> str | None:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for blk in iter(lambda: f.read(chunk), b""):
                h.update(blk)
        return h.hexdigest()
    except OSError:
        return None


def ids_digest(ids) -> str:
    return sha256_bytes("\n".join(sorted(ids)).encode("utf-8"))[:16]


def json_digest(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"))[:16]


def export_fingerprint(export: Path) -> dict:
    """Cheap identity of an export folder: metadata.jsonl (it carries the source photo / annotation hashes and the
    mask statistics of every sample), targets.json, splits, and the number of images."""
    e = Path(export)
    fp = {"metadata": (sha256_file(e / "metadata.jsonl") or "missing")[:16],
          "targets": (sha256_file(e / "targets.json") or "missing")[:16],
          "splits": json_digest({s: (sha256_file(e / "splits" / f"{s}.txt") or "")[:16] for s in ("train", "val", "test")}),
          "n_images": len(os.listdir(e / "images")) if (e / "images").is_dir() else 0}
    fp["digest"] = json_digest(fp)
    return fp


def text_hash(p: Path) -> str | None:
    """sha256 of a text file with line endings normalised (same on Windows CRLF and Linux LF checkouts)."""
    try:
        return sha256_bytes(Path(p).read_bytes().replace(b"\r\n", b"\n"))
    except OSError:
        return None


def code_hashes() -> dict:
    root = HERE.parent
    return {f: (text_hash(root / f) or "missing")[:16] for f in CODE_FILES}


def git_commit() -> dict:
    """HEAD commit of the repository this code lives in (read from .git, no git binary needed)."""
    for p in [HERE] + list(HERE.parents):
        g = p / ".git"
        if g.is_dir():
            try:
                head = (g / "HEAD").read_text().strip()
                if head.startswith("ref:"):
                    ref = head.split(" ", 1)[1].strip()
                    f = g / ref
                    sha = f.read_text().strip() if f.exists() else None
                    if sha is None and (g / "packed-refs").exists():
                        for line in (g / "packed-refs").read_text().splitlines():
                            if line.endswith(" " + ref):
                                sha = line.split()[0]
                    return {"commit": sha, "ref": ref}
                return {"commit": head, "ref": None}
            except OSError:
                return {"commit": None, "ref": None}
    return {"commit": None, "ref": None, "note": "not inside a git checkout"}


# ------------------------------------------------------------------ determinism
def set_determinism(seed: int):
    """Fix every seed (initialisation, data order and augmentation follow from it). Must run before the first CUDA call.

    Strict GPU determinism (cudnn.deterministic / use_deterministic_algorithms) is deliberately OFF: on the reference
    laptop it made training 8x slower (5.5 s vs 0.67 s per step). Without it, GPU results differ between repeated runs
    only in the last digits (summation order); that is far below the seed-to-seed spread we report (3 seeds, mean +- std)
    and is measured once across PCs with `compare`. cudnn.benchmark stays OFF so the algorithm choice does not vary."""
    os.environ.setdefault("PYTHONHASHSEED", "0")
    import numpy as np
    import torch
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = False
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(False)


def rng_state() -> dict:
    import numpy as np
    import torch
    return {"python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}


def set_rng_state(s: dict):
    import numpy as np
    import torch
    random.setstate(s["python"])
    np.random.set_state(s["numpy"])
    torch.set_rng_state(s["torch"])
    if s.get("cuda") and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(s["cuda"])


# ------------------------------------------------------------------ files
def write_json(p: Path, obj, indent=1):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, default=str), encoding="utf-8")
    os.replace(tmp, p)


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_csv(p: Path, rows, fields=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, p)


def read_csv(p: Path):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v):
    """csv cell -> float / None."""
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_dir(results: Path, model: str, fold: str, seed: int) -> Path:
    return Path(results) / model / fold / f"seed{seed}"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def python_ok() -> bool:
    return sys.version_info >= (3, 9)
