"""Point the qa_v3 automatic preprocessing project (stage 1) at the Data folder of THIS computer.

qa_v3 keeps absolute paths of the photo ZIPs / processed JSON ZIP in
``automatic_reference_v3/project.json`` (written on the original PC, e.g. D:\\work\\project\\Data\\...).
On another computer run this once before ``전처리_실행.cmd``:

    .venv\\Scripts\\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project            (dry run, shows changes)
    .venv\\Scripts\\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project --apply

Options: ``--data-dir`` (default: the ``Data`` folder next to ``전처리된 데이터``), ``--add-zip`` to add the photo ZIP
of a new site. The original file is kept as ``project.json.bak_<time>``. Photos and ZIPs are never touched.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parents[2]          # ...\전처리된 데이터


def _pure(p: str):
    return PureWindowsPath(p) if ("\\" in p or ":" in p[:3]) else PurePosixPath(p)


def relocate(doc: dict, new_data: Path) -> tuple[dict, list[tuple[str, str]]]:
    old_data = str(_pure(doc["photos"][0]).parent)
    changes = []

    def fix(value):
        if isinstance(value, str) and value.casefold().startswith(old_data.casefold()):
            rest = value[len(old_data):].lstrip("\\/")
            new = str(new_data.joinpath(*_pure(rest).parts)) if rest else str(new_data)
            if new != value:
                changes.append((value, new))
            return new
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value

    return fix(doc), changes


def relocate_pointers(root: Path) -> list[tuple[Path, dict, dict]]:
    """latest_*_run.json pointers hold absolute paths of the original PC. If the folder they name is not there,
    point them at the folder of the same name in THIS project's automatic_results (or, for a renamed stage-1
    folder, the one whose FIRST_STAGE_RUN.json records that original name)."""
    results = root / "automatic_results"
    out = []
    for p in (root / "automatic_reference_v3" / "latest_active_run.json",
              root / "automatic_reference_v3" / "latest_automatic_run.json",
              results / "latest_active_run.json"):
        if not p.exists():
            continue
        doc = json.loads(p.read_text(encoding="utf-8"))
        raw = doc.get("run_dir") or doc.get("path")
        if not raw or Path(raw).exists():
            continue
        cand = results / _pure(raw).name
        if not cand.exists() and results.is_dir():
            for d in results.iterdir():
                m = d / "FIRST_STAGE_RUN.json"
                if m.is_file():
                    doc_m = json.loads(m.read_text(encoding="utf-8"))
                    if _pure(raw).name in (doc_m.get("original_run_name"), doc_m.get("renamed_to")):
                        cand = d
        if cand.exists():
            new = dict(doc, **{k: str(cand) for k in ("run_dir", "path") if doc.get(k)})
            out.append((p, doc, new))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, default=PROJECT_ROOT / "automatic_reference_v3" / "project.json")
    ap.add_argument("--data-dir", type=Path, default=PROJECT_ROOT.parent / "Data")
    ap.add_argument("--add-zip", action="append", default=[], help="photo ZIP of an additional site (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    doc = json.loads(args.project.read_text(encoding="utf-8"))
    new, changes = relocate(doc, args.data_dir.resolve())
    for z in args.add_zip:
        z = str(Path(z).resolve())
        if z not in new["photos"]:
            new["photos"].append(z)
            changes.append(("(added)", z))
    print(f"project : {args.project}")
    print(f"data dir: {args.data_dir.resolve()}")
    print(f"changes : {len(changes)} path(s)")
    for a, b in changes[:8]:
        print(f"   {a}\n -> {b}")
    missing = [p for p in new["photos"] + [new["json_zip"]] if not Path(p).exists()]
    for p in missing:
        print(f"WARNING: not found on this computer: {p}")
    root = args.project.resolve().parents[1]
    ptr = relocate_pointers(root)
    for p, old, nv in ptr:
        print(f"pointer : {p.name}\n   {old.get('run_dir') or old.get('path')}\n -> {nv.get('run_dir') or nv.get('path')}")
    if not args.apply:
        print("dry run only; add --apply to write")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if changes:
        shutil.copy2(args.project, args.project.with_name(args.project.name + f".bak_{stamp}"))
        args.project.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written (backup: {args.project.name}.bak_{stamp})")
    for p, _, nv in ptr:
        shutil.copy2(p, p.with_name(p.name + f".bak_{stamp}"))
        p.write_text(json.dumps(nv, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"pointer written: {p} (backup .bak_{stamp})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
