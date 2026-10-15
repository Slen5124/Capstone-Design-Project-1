"""Show progress or request a checkpoint stop without opening a browser."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import time

try:
    from console_progress import read_progress as _read_progress
except ImportError:
    _read_progress = None

_READ_CACHE = {}
_RETRY_DELAYS = (.01, .02, .04, .08, .08, .08, .08)

TERMINAL_STATES = {"complete", "completed", "stopped", "incomplete", "failed", "cancelled", "paused"}
PHASE_NAMES = {
    "starting": "시작 준비", "loading": "원본 목록 및 기준 읽기",
    "teachers": "대표사진 기준 읽기", "indexing": "JSON 연결 색인 만들기",
    "json_index": "JSON 연결 색인 만들기", "processing": "사진 및 JSON 수정",
    "exporting": "결과 저장", "checkpoint": "체크포인트 저장",
    "finalizing": "마무리", "complete": "완료", "stopped": "안전 중단",
    "stopping": "현재 작업 저장 후 중단 중", "finished": "실행 종료",
}
CLASS_NAMES = {"auto_fixed": "자동 수정", "kept": "유지", "needs_review": "남은 문제", "rejected": "처리 불가"}


def read_json(path: Path):
    """Retry a Windows atomic-replace race; keep a labelled prior snapshot."""
    key = str(path.resolve())
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            if path.name == "progress.json" and _read_progress is not None:
                value = _read_progress(path)
            else:
                with path.open(encoding="utf-8-sig") as handle:
                    value = json.load(handle)
            value = value if isinstance(value, dict) else {}
            _READ_CACHE[key] = dict(value)
            return value
        except OSError as exc:
            transient = isinstance(exc, (PermissionError, FileNotFoundError)) or getattr(exc, "winerror", None) in {5, 32, 33}
            # The shared reporter helper already made bounded retries.
            if path.name == "progress.json" and _read_progress is not None:
                break
            if transient and attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])
                continue
            break
        except ValueError:
            break
    if key in _READ_CACHE:
        return dict(_READ_CACHE[key], _status_read_stale=True)
    return {"_status_read_unavailable": True}


def within_results(path: Path, results: Path) -> Path:
    """Only permit a stop sentinel in this installed project's result root."""
    resolved = path.expanduser().resolve()
    root = results.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("실행 폴더는 이 프로젝트의 automatic_results 하위 폴더여야 합니다.")
    if not resolved.is_dir():
        raise ValueError("실행 폴더가 존재하지 않습니다.")
    return resolved


def choose_run(project_root: Path, requested: Path | None = None) -> Path | None:
    results = project_root / "automatic_results"
    if requested is not None:
        return within_results(requested, results)
    for pointer in (results / "latest_active_run.json",
                    project_root / "automatic_reference_v3" / "latest_active_run.json",
                    project_root / "automatic_reference_v3" / "latest_automatic_run.json"):
        value = read_json(pointer)
        candidate = value.get("run_dir") or value.get("path")
        if candidate:
            path = Path(candidate)
            if not path.is_absolute():
                path = results / path
            try:
                return within_results(path, results)
            except ValueError:
                pass
    if not results.is_dir():
        return None
    candidates = [p for p in results.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not candidates:
        return None
    active = [p for p in candidates if read_json(p / "progress.json").get("status") in {"running", "starting"}]
    return max(active or candidates, key=lambda p: p.name)


def first(value: dict, names: tuple, default=None):
    for key in names:
        if value.get(key) is not None:
            return value[key]
    return default


def duration(seconds):
    try:
        seconds = max(0, int(float(seconds)))
    except (ValueError, TypeError, OverflowError):
        return "계산 중"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def snapshot(run_dir: Path) -> dict:
    summary = read_json(run_dir / "summary.json")
    progress = read_json(run_dir / "progress.json")
    value = dict(summary)
    value.update(progress)
    # A completed summary is authoritative when an older progress file lingers.
    if str(summary.get("status", "")).casefold() in TERMINAL_STATES and str(progress.get("status", "")).casefold() == "running":
        value.update(summary)
        if summary.get("status") in {"complete", "completed"} and summary.get("selected_photos") is not None:
            value["done"] = summary["selected_photos"]
    value["run_dir"] = str(run_dir)
    if summary.get("_status_read_stale") or progress.get("_status_read_stale"):
        value["_status_read_stale"] = True
    return value


def render(value: dict, run_dir: Path) -> str:
    status = str(value.get("status", "대기")).casefold()
    phase = str(value.get("phase", status)).casefold()
    done = first(value, ("done", "completed", "processed_photos", "completed_photos", "photos_done"), None)
    total = first(value, ("total", "selected_photos", "total_selected", "photos_total"), None)
    if done is None and status in {"complete", "completed"}:
        done = total
    units = value.get("unit", "장")
    if phase in {"indexing", "json_index"}:
        units = "개 JSON"
    ratio = ""
    if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
        ratio = f" ({100 * done / total:.1f}%)"
    elapsed = duration(value.get("elapsed_seconds"))
    eta = duration(first(value, ("eta_seconds", "remaining_seconds")))
    if status in {"complete", "completed"}:
        eta = "00:00:00"
    counts = first(value, ("classes", "counters", "class_counts"), {})
    if not isinstance(counts, dict):
        counts = {}
    try:
        free_gb = shutil.disk_usage(run_dir).free / 1024 ** 3
        storage = f"{free_gb:.2f} GiB"
    except OSError:
        storage = "확인 불가"
    lines = [f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}] {PHASE_NAMES.get(phase, phase)} / {status}",
             f"진행: {done if done is not None else '?'} / {total if total is not None else '?'} {units}{ratio}",
             f"경과: {elapsed}   현재 단계 예상 남은 시간: {eta}   디스크 여유: {storage}",
             "분류: " + ", ".join(f"{CLASS_NAMES[k]} {counts.get(k, 0)}" for k in CLASS_NAMES),
             f"결과: {run_dir}"]
    checkpoint = first(value, ("checkpoint_done", "checkpoint_completed", "checkpoint_photos", "committed_photos"))
    if checkpoint is not None:
        lines.append(f"저장된 체크포인트: {checkpoint}장")
    current = first(value, ("current", "current_photo", "current_entry", "last_photo"))
    if current:
        lines.append(f"최근 처리: {str(current)[:220]}")
    if value.get("error"):
        lines.append(f"중단 사유: {value['error']}")
    if value.get("_status_read_stale"):
        lines.append("상태 파일을 잠시 읽을 수 없어 마지막으로 확인한 상태를 표시합니다.")
    elif value.get("_status_read_unavailable") and not value.get("status"):
        lines.append("상태 파일을 아직 읽을 수 없습니다. 잠시 후 다시 확인하세요.")
    if (run_dir / "STOP").exists():
        lines.append("안전 중단 요청이 있습니다. 현재 작업을 저장한 뒤 종료합니다.")
    return "\n".join(lines)


def request_stop(run_dir: Path, results: Path) -> bool:
    checked = within_results(run_dir, results)
    value = snapshot(checked)
    if value.get("_status_read_stale"):
        raise ValueError("상태 파일을 잠시 읽을 수 없어 중단 요청을 만들지 않았습니다. 잠시 후 다시 실행하세요.")
    if value.get("status") in TERMINAL_STATES:
        print("이미 종료된 실행입니다. 중단 요청을 만들지 않았습니다.", flush=True)
        return False
    if value.get("status") not in {"running", "starting"}:
        raise ValueError("진행 중인 실행 상태를 확인하지 못해 중단 요청을 만들지 않았습니다.")
    sentinel = checked / "STOP"
    try:
        with sentinel.open("x", encoding="utf-8") as handle:
            handle.write("stop requested from local command-line status utility\n")
    except FileExistsError:
        print("안전 중단이 이미 요청됐습니다.", flush=True)
    else:
        print("안전 중단을 요청했습니다. 현재 작업 저장 후 종료하므로 실행 창을 강제로 닫지 마세요.", flush=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2.)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    if args.interval < .2 or args.interval > 60:
        parser.error("interval must be 0.2..60 seconds")
    run_dir = choose_run(args.project_root, args.run)
    if run_dir is None:
        print("실행 결과가 아직 없습니다.")
        return 0
    if args.stop:
        request_stop(run_dir, args.project_root / "automatic_results")
    previous = None
    try:
        while True:
            value = snapshot(run_dir)
            content = render(value, run_dir)
            if content != previous:
                print(content + "\n", flush=True)
                previous = content
            if not args.watch or value.get("status") in TERMINAL_STATES:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("상태 표시만 종료했습니다. 전처리 실행은 계속됩니다.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"상태 확인 오류: {exc}", file=sys.stderr)
        raise SystemExit(2)
