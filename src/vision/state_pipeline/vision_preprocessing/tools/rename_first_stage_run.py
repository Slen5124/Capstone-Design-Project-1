"""Give a finished stage-1 (qa_v3) result folder a readable name, safely.

    automatic_results\\run_20261009T163018_494922Z  ->  automatic_results\\1차결과_qa_v3_전체4도시_20261009

What it does (dry run unless --apply):
  1. checks the run is complete (summary.json status=complete, remaining_photos=0, no STOP file);
  2. writes FIRST_STAGE_RUN.json (original name + original absolute path, counts) and README_1차결과.md
     into the folder - nothing else inside the folder is changed (manifest / chunks / annotations untouched);
  3. renames the folder;
  4. repoints latest_active_run.json / latest_automatic_run.json (backups *.bak_<time>).

The manifest inside still names the old folder; vision_preprocessing reads FIRST_STAGE_RUN.json and maps
those paths to the new folder automatically (runctx.first_stage_rename_map). Close the review server and
do not run qa_v3 / vp auto while renaming.

    python -m vision_preprocessing.tools.rename_first_stage_run                 (dry run)
    python -m vision_preprocessing.tools.rename_first_stage_run --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

PROJECT_ROOT = Path(__file__).resolve().parents[2]          # ...\전처리된 데이터
MARKER = "FIRST_STAGE_RUN.json"
README = "README_1차결과.md"


def _pure(p: str):
    return PureWindowsPath(p) if ("\\" in p or ":" in p[:3]) else PurePosixPath(p)


def _swap_leaf(raw: str, old: str, new: str) -> str:
    """Replace the last path component in a path written on any OS, keeping its form (D:\\... stays)."""
    raw = raw.rstrip("\\/")
    if _pure(raw).name != old:
        raise ValueError(f"{raw!r} does not end with {old!r}")
    return raw[: len(raw) - len(old)] + new


def pointer_files(root: Path) -> list[Path]:
    return [root / "automatic_reference_v3" / "latest_active_run.json",
            root / "automatic_reference_v3" / "latest_automatic_run.json",
            root / "automatic_results" / "latest_active_run.json"]


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def inspect_run(run: Path) -> dict:
    summary = _read(run / "summary.json")
    per_zip = collections.Counter()
    prefixes = set()
    with (run / "manifest.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            z = (r.get("source_photo") or {}).get("zip")
            if z:
                per_zip[_pure(z).name] += 1
            out = (r.get("annotation_output") or {}).get("zip") or (r.get("annotation_output") or {}).get("path")
            if out and run.name in out:
                prefixes.add(out[: out.index(run.name) + len(run.name)])
    problems = []
    if summary.get("status") != "complete":
        problems.append(f"summary status is {summary.get('status')!r}, not complete")
    if summary.get("remaining_photos"):
        problems.append(f"{summary.get('remaining_photos')} photos remaining")
    if (run / "STOP").exists():
        problems.append("STOP file present (run being stopped)")
    return {"summary": summary, "per_zip": dict(per_zip), "manifest_prefixes": sorted(prefixes), "problems": problems}


def readme_text(new_name: str, old_name: str, info: dict) -> str:
    s = info["summary"]
    rows = "\n".join(f"| {z} | {n:,} |" for z, n in sorted(info["per_zip"].items()))
    return f"""# 1차 결과 (qa_v3 자동 전처리) — {new_name}

이 폴더는 **1차 단계(qa_v3 `auto_preprocess.py`) 결과 전체**입니다. SAM3 JSON 폴리곤을 기준사진과 맞춰
자동 보정한 **물체 폴리곤 초안**(wall / floor / ceiling / person …)과 사진별 품질 분류가 들어 있습니다.
사람이 확인한 결과가 아닙니다 (`human_confirmed=false`). 원래 폴더 이름: `{old_name}`.

- 상태: `{s.get('status')}` · 사진 {s.get('completed_photos', 0):,} / {s.get('total_photos', 0):,}장 · 남은 사진 {s.get('remaining_photos', 0)}
- 범위: `{s.get('scope')}` · 원본 수정 여부 `source_originals_modified={s.get('source_originals_modified')}`
- 크기: 약 {s.get('output_bytes', 0) / 1024**3:.1f} GB · 조각 {s.get('checkpoint_parts', 0):,}개

| 사진 ZIP | 사진 수 |
|---|---|
{rows}

## 폴더 구조

```
{new_name}\\
├─ README_1차결과.md      ← 이 파일
├─ FIRST_STAGE_RUN.json   ← 원래 이름·경로 기록 (2차 코드가 이걸 읽음 — 지우지 마세요)
├─ manifest.jsonl         ← 사진 1장 = 1줄 (원본 ZIP 위치, 분류, 폴리곤 JSON 위치, sha256)
├─ classification.csv     ← 사진별 분류 (kept / auto_fixed / needs_review …)
├─ summary.json · progress.json · job.json · console.log
└─ chunks\\part_000001 … \\
     ├─ annotations\\*.zip   ← 보정된 폴리곤 JSON (LabelMe 형식)
     ├─ manifest.jsonl · classification.csv · completed.json
```

## 어디에 쓰나

| 단계 | 이 폴더 필요? |
|---|---|
| 2차 `vp auto` (뷰·ROI·라벨 초안 → 학습 데이터 export) | **필요** — `semantic_masks`와 사진 목록이 여기서 나옴 |
| 사람 검수 화면 (`vp review`) | **필요** (폴리곤 표시) |
| 인퍼런스 (학습된 모델로 새 사진 판정) | 필요 없음 |

2차는 `automatic_reference_v3\\latest_active_run.json`이 가리키는 폴더를 씁니다 (이 폴더로 맞춰져 있음).

## 다른 컴퓨터로 옮길 때

```
<작업폴더>\\
├─ Data\\                         ← 원본 사진 ZIP + processed_json ZIP (파일 이름 그대로!)
└─ 전처리된 데이터\\
    ├─ automatic_results\\{new_name}\\   ← 이 폴더를 통째로 복사
    ├─ automatic_reference_v3\\  qa_v3\\  qa_v2\\  vision_preprocessing\\ …  (패키지 v3)
```

1. 이 폴더를 위 위치에 **통째로** 복사합니다 (안의 파일은 sha256으로 검증되므로 수정 금지).
2. `Data`에 원본 ZIP을 **같은 파일 이름으로** 넣습니다. ZIP 비밀번호는 실행할 때 입력합니다.
3. 경로 맞추기 (한 번):
   ```powershell
   cd "<작업폴더>\\전처리된 데이터"
   .venv\\Scripts\\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project          # 확인만
   .venv\\Scripts\\python.exe -m vision_preprocessing.tools.relocate_qa_v3_project --apply  # 적용 (백업 생성)
   ```
   `project.json`의 ZIP 경로와 `latest_*_run.json` 포인터를 이 컴퓨터 위치로 고칩니다.
4. 그다음 2차: `vision_preprocessing\\vp.cmd auto --config vision_preprocessing\\config\\site_b.json` 등.

## 주의

- 폴더 이름은 바꿔도 되지만 `FIRST_STAGE_RUN.json`은 남겨 두세요. `manifest.jsonl` 안의 경로는 원래 이름
  (`{old_name}`)을 가리키고, 2차 코드가 이 파일을 보고 새 위치로 바꿔 읽습니다.
- 이름을 바꾼 뒤에는 위 3번(`relocate_qa_v3_project --apply`)을 실행해 포인터를 맞추세요 (`전처리_상태.cmd`용).
- 회사 현장 데이터입니다. 외부 서비스에 올리지 마세요.
"""


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT, help="the 전처리된 데이터 folder")
    ap.add_argument("--run", help="run folder name inside automatic_results (default: the latest_active_run pointer)")
    ap.add_argument("--name", help="new folder name (default: 1차결과_qa_v3_<scope>_<date>)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    results = root / "automatic_results"
    pointers = {p: _read(p) for p in pointer_files(root) if p.exists()}
    if args.run:
        old_name = args.run
    else:
        raw = next((v.get("run_dir") or v.get("path") for v in pointers.values()), None)
        if not raw:
            print("no latest_active_run pointer; pass --run")
            return 2
        old_name = _pure(raw).name
    run = results / old_name
    if not (run / "manifest.jsonl").exists():
        print(f"not a stage-1 run folder: {run}")
        return 2
    info = inspect_run(run)
    s = info["summary"]
    started = old_name[4:12] if old_name.startswith("run_") and old_name[4:12].isdigit() else ""
    scope = "전체" if s.get("scope") == "full_dataset" else (s.get("scope") or "부분")
    new_name = args.name or f"1차결과_qa_v3_{scope}{len(info['per_zip'])}도시_{started or datetime.now().strftime('%Y%m%d')}"
    target = results / new_name
    raw_old = sorted({v.get("run_dir") or v.get("path") for v in pointers.values()
                      if _pure(v.get("run_dir") or v.get("path") or "x").name == old_name} | set(info["manifest_prefixes"]))
    print(f"run      : {run}")
    print(f"new name : {new_name}")
    print(f"photos   : {s.get('completed_photos')} / {s.get('total_photos')}  per zip: {info['per_zip']}")
    print(f"old paths recorded in manifest/pointers: {raw_old}")
    for p in info["problems"]:
        print(f"REFUSED: {p}")
    if info["problems"]:
        return 1
    if target.exists():
        print(f"REFUSED: {target} already exists")
        return 1
    plan = [(p, v) for p, v in pointers.items() if _pure(v.get("run_dir") or v.get("path") or "x").name == old_name]
    for p, _ in plan:
        print(f"pointer  : {p}")
    if not args.apply:
        print("dry run only; add --apply")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    marker = {"kind": "qa_v3_first_stage_run", "original_run_name": old_name, "original_run_dirs": raw_old,
              "renamed_to": new_name, "renamed_utc": stamp, "status": s.get("status"),
              "completed_photos": s.get("completed_photos"), "total_photos": s.get("total_photos"),
              "per_photo_zip": info["per_zip"], "human_confirmed": False,
              "note": "manifest.jsonl paths still name original_run_name; vision_preprocessing maps them here."}
    (run / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    (run / README).write_text(readme_text(new_name, old_name, info), encoding="utf-8")
    os.rename(run, target)
    for p, v in plan:
        shutil.copy2(p, p.with_name(p.name + f".bak_{stamp}"))
        nv = dict(v)
        for k in ("run_dir", "path"):
            if nv.get(k):
                nv[k] = _swap_leaf(nv[k], old_name, new_name)
        p.write_text(json.dumps(nv, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"renamed -> {target}")
    print(f"pointers updated: {len(plan)} (backups *.bak_{stamp})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
