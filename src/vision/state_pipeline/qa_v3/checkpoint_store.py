"""Durable, immutable output chunks for the automatic annotation batch.

Only committed ``chunks/part_NNNNNN`` directories count as completed work.
An interrupted ZIP is never appended to; its pending directory is retained
under an abandoned name and the affected photos are processed again.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile


CATEGORIES = ("auto_fixed", "kept", "needs_review", "rejected")
CSV_FIELDS = (
    "id", "classification", "photo_zip", "photo_entry", "source_json_entry",
    "annotation_output", "changed", "action_count", "unresolved", "reference_id",
)
_PART = re.compile(r"part_(\d{6})\Z")
_PENDING = re.compile(r"\.pending_part_(\d{6})_[0-9a-f]{32}\Z")
_ABANDONED = re.compile(r"\.abandoned_part_(\d{6})_[0-9a-f]{32}\Z")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_name(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Unsafe annotation relative name")
    result = PurePosixPath(value)
    if result.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ValueError("Unsafe annotation relative name")
    return result.as_posix()


def _sync_directory(path):
    # Windows does not support opening ordinary directory handles through os.open.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _empty_counts():
    return {"classes": {}, "actions": {}, "unresolved": {}, "exported_json": 0,
            "changed_json": 0, "partially_repaired_json": 0, "records": 0}


def _record_counts(records):
    result = _empty_counts()
    classes, actions, unresolved = Counter(), Counter(), Counter()
    for record in records:
        category = record["classification"]
        if category not in CATEGORIES:
            raise ValueError("Unknown output classification")
        classes[category] += 1
        actions.update(item["action"] for item in record.get("actions", []))
        unresolved.update(item["code"] for item in record.get("unresolved", []))
        result["records"] += 1
        if record.get("annotation_output") is not None:
            result["exported_json"] += 1
            result["changed_json"] += int(bool(record.get("changed")))
            result["partially_repaired_json"] += int(bool(record.get("changed")) and category == "needs_review")
    result.update(classes=dict(classes), actions=dict(actions), unresolved=dict(unresolved))
    return result


def _merge_counts(left, right):
    for key in ("classes", "actions", "unresolved"):
        left[key] = dict(Counter(left[key]) + Counter(right[key]))
    for key in ("exported_json", "changed_json", "partially_repaired_json", "records"):
        left[key] += right[key]


def _csv_row(record):
    photo = record.get("source_photo", {})
    return {
        "id": record["id"], "classification": record["classification"],
        "photo_zip": photo.get("zip", ""), "photo_entry": photo.get("entry", ""),
        "source_json_entry": record.get("source_json", {}).get("entry"),
        "annotation_output": json.dumps(record.get("annotation_output"), ensure_ascii=False),
        "changed": bool(record.get("changed")), "action_count": len(record.get("actions", [])),
        "unresolved": ";".join(item["code"] for item in record.get("unresolved", [])),
        "reference_id": record.get("reference_id", ""),
    }


class CheckpointStore:
    """Single-writer chunk store. The caller owns the run-level process lock.

    Finalize records with ``write_record`` and then mark their whole photo with
    ``complete_photo``. A checkpoint cannot commit the records of half a photo.
    The constructor validates all committed chunks when ``resume=True``.
    """

    def __init__(self, run_dir, layout="zip", checkpoint_every=20, resume=False):
        if layout not in {"zip", "files"} or not isinstance(checkpoint_every, int) or checkpoint_every < 1:
            raise ValueError("Invalid checkpoint output option")
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir = self.run_dir / "chunks"
        if self.chunks_dir.is_symlink():
            raise ValueError("Chunk directory cannot be a symbolic link")
        self.chunks_dir.mkdir(exist_ok=True)
        self.layout = layout
        self.checkpoint_every = checkpoint_every
        self._state = {"chunks": [], "completed": set(), "counts": _empty_counts(), "bytes": 0, "next_part": 1}
        self._active = None
        self._closed = False
        if resume:
            self._scan()
        elif any(self.chunks_dir.iterdir()):
            raise FileExistsError("Output chunks already exist; use resume")

    @property
    def _chunks(self):
        return self._state["chunks"]

    @property
    def _completed(self):
        return self._state["completed"]

    @property
    def _counts(self):
        return self._state["counts"]

    @property
    def _bytes_written(self):
        return self._state["bytes"]

    @_bytes_written.setter
    def _bytes_written(self, value):
        self._state["bytes"] = value

    @property
    def _next_part(self):
        return self._state["next_part"]

    @_next_part.setter
    def _next_part(self, value):
        self._state["next_part"] = value

    def _scan(self):
        children = sorted(self.chunks_dir.iterdir(), key=lambda p: p.name)
        pending = []
        for path in children:
            if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(self.chunks_dir):
                raise ValueError("Unexpected or unsafe chunk path: " + path.name)
            match = _PART.fullmatch(path.name)
            if match:
                metadata = self._validate_chunk(path)
                repeated = self._completed.intersection(metadata["completed_photo_ids"])
                if repeated:
                    raise ValueError("Duplicate completed photo IDs across chunks: " + ", ".join(sorted(repeated)))
                self._completed.update(metadata["completed_photo_ids"])
                _merge_counts(self._counts, metadata["counters"])
                self._chunks.append((path, metadata))
                self._bytes_written += sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
                self._next_part = max(self._next_part, int(match.group(1)) + 1)
            elif _PENDING.fullmatch(path.name):
                pending.append(path)
            elif not _ABANDONED.fullmatch(path.name):
                raise ValueError("Unexpected chunk directory: " + path.name)
        # Validate committed work first. A damaged committed result causes a
        # failure rather than falsely skipping source photos or changing files.
        for path in pending:
            self._quarantine(path)

    def _read_records(self, path):
        with (path / "manifest.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("Checkpoint record must be an object")
                    yield value

    def _validate_chunk(self, path, *, reference_root=None, part_name=None):
        reference_root = path if reference_root is None else reference_root
        part_name = path.name if part_name is None else part_name
        try:
            metadata = json.loads((path / "completed.json").read_text(encoding="utf-8"))
            if metadata["schema_version"] != 1 or metadata["layout"] != self.layout or metadata["part"] != part_name:
                raise ValueError("Checkpoint schema, layout, or part mismatch")
            ids = metadata["completed_photo_ids"]
            if not isinstance(ids, list) or any(not isinstance(v, str) or not v for v in ids) or len(set(ids)) != len(ids):
                raise ValueError("Invalid completed photo IDs")
            files = metadata["files_sha256"]
            if not isinstance(files, dict) or not {"manifest.jsonl", "classification.csv"}.issubset(files):
                raise ValueError("Checkpoint catalogs are missing")
            actual = set()
            for file in path.rglob("*"):
                if file.is_symlink() or not file.resolve().is_relative_to(path):
                    raise ValueError("Unsafe checkpoint file")
                if file.is_file() and file != path / "completed.json":
                    actual.add(file.relative_to(path).as_posix())
            if actual != set(files):
                raise ValueError("Checkpoint payload file list changed")
            for name, expected in files.items():
                relative = _relative_name(name)
                file = path / relative
                if not isinstance(expected, str) or _hash_file(file) != expected:
                    raise ValueError("Checkpoint payload hash changed: " + name)
            records = list(self._read_records(path))
            if {record.get("checkpoint_photo_id") for record in records} != set(ids):
                raise ValueError("Completed photo IDs do not exactly match checkpoint records")
            seen_record_ids = set()
            annotation_entries = {}
            referenced_files = set()
            archive_handles = {}
            try:
                for record in records:
                    if record.get("checkpoint_photo_id") not in ids:
                        raise ValueError("Record refers to an incomplete photo")
                    if not isinstance(record.get("id"), str) or record["id"] in seen_record_ids:
                        raise ValueError("Duplicate or invalid checkpoint record ID")
                    seen_record_ids.add(record["id"])
                    if record.get("classification") not in CATEGORIES:
                        raise ValueError("Unknown output classification")
                    output = record.get("annotation_output")
                    if output is None:
                        continue
                    category = record["classification"]
                    if self.layout == "zip":
                        expected_path = path / "annotations" / (category + ".zip")
                        expected_reference = reference_root / "annotations" / (category + ".zip")
                        if set(output) != {"zip", "entry"} or Path(output["zip"]).resolve() != expected_reference.resolve():
                            raise ValueError("Annotation archive path escaped its committed chunk")
                        name = _relative_name(output["entry"])
                        key = (category, name)
                        if key in annotation_entries:
                            raise ValueError("Duplicate annotation archive entry")
                        annotation_entries[key] = True
                        if category not in archive_handles:
                            archive_handles[category] = zipfile.ZipFile(expected_path, "r")
                        payload = archive_handles[category].read(name)
                    else:
                        if set(output) != {"path"}:
                            raise ValueError("Invalid annotation file reference")
                        annotation_path = Path(output["path"])
                        expected_root = reference_root / "annotations" / category
                        if not annotation_path.is_absolute() or not annotation_path.resolve().is_relative_to(expected_root.resolve()):
                            raise ValueError("Annotation file path escaped its committed chunk")
                        key = (category, str(annotation_path.resolve()))
                        if key in annotation_entries:
                            raise ValueError("Duplicate annotation file reference")
                        annotation_entries[key] = True
                        relative = annotation_path.resolve().relative_to(expected_root.resolve())
                        physical = path / "annotations" / category / relative
                        referenced_files.add(physical.relative_to(path).as_posix())
                        payload = physical.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != record.get("output_json_sha256"):
                        raise ValueError("Annotation JSON hash changed")
                if self.layout == "zip":
                    for category in CATEGORIES:
                        if category not in archive_handles:
                            archive_handles[category] = zipfile.ZipFile(path / "annotations" / (category + ".zip"), "r")
                        names = archive_handles[category].namelist()
                        if len(set(names)) != len(names) or set(names) != {name for cls, name in annotation_entries if cls == category}:
                            raise ValueError("Unexpected or duplicated annotation archive entries")
                        # Every listed JSON entry was read above. ZipFile.read
                        # already checks its CRC, so a second testzip pass would
                        # unnecessarily decompress the whole dataset again.
                elif {name for name in files if name.startswith("annotations/")} != referenced_files:
                    raise ValueError("Unexpected annotation files without manifest records")
            finally:
                for handle in archive_handles.values():
                    handle.close()
            if len(records) != metadata["record_count"] or _record_counts(records) != metadata["counters"]:
                raise ValueError("Checkpoint counters do not match its records")
            return metadata
        except (KeyError, TypeError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise ValueError("Invalid committed checkpoint: " + path.name) from exc

    def _ensure_active(self):
        if self._closed:
            raise RuntimeError("Checkpoint store is closed")
        if self._active is not None:
            return
        part = f"part_{self._next_part:06d}"
        pending = self.chunks_dir / (".pending_" + part + "_" + uuid.uuid4().hex)
        pending.mkdir()
        (pending / "annotations").mkdir()
        archives = {}
        if self.layout == "zip":
            for category in CATEGORIES:
                archives[category] = zipfile.ZipFile(pending / "annotations" / (category + ".zip"), "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        else:
            for category in CATEGORIES:
                (pending / "annotations" / category).mkdir()
        manifest = (pending / "manifest.jsonl").open("x", encoding="utf-8", newline="\n")
        csvfile = (pending / "classification.csv").open("x", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        writer.writeheader()
        self._active = {"part": part, "path": pending, "final": self.chunks_dir / part,
                        "archives": archives, "manifest": manifest, "csvfile": csvfile,
                        "writer": writer, "uncompleted_records": [], "records": [],
                        "photo_ids": [], "record_ids": set(), "output_names": set(), "write_failed": False}

    def write_record(self, record, payload, relative_name):
        """Save one result; ``complete_photo`` makes all its records committable."""
        if not isinstance(record, dict) or "doc" in record:
            raise ValueError("Pass a finalized record without its doc payload")
        category = record.get("classification")
        if category not in CATEGORIES or not isinstance(record.get("id"), str):
            raise ValueError("Invalid finalized output record")
        name = _relative_name(relative_name)
        if payload is not None and not isinstance(payload, bytes):
            raise TypeError("Annotation payload must be bytes or None")
        # Serialize before making any writes so invalid values do not leave a
        # half-written result inside an otherwise committable photo.
        result = json.loads(_json_bytes(record))
        self._ensure_active()
        active = self._active
        if result["id"] in active["record_ids"]:
            raise ValueError("Duplicate checkpoint record ID")
        result["annotation_output"] = None
        if payload is not None:
            key = (category, name)
            if key in active["output_names"]:
                raise ValueError("Duplicate annotation output name")
            try:
                if self.layout == "zip":
                    active["archives"][category].writestr(name, payload)
                    result["annotation_output"] = {"zip": str(active["final"] / "annotations" / (category + ".zip")), "entry": name}
                else:
                    target = active["path"] / "annotations" / category / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    result["annotation_output"] = {"path": str(active["final"] / "annotations" / category / name)}
            except BaseException:
                # Includes Ctrl+C between writing a ZIP member and adding its
                # manifest record. Such a batch can only be quarantined.
                active["write_failed"] = True
                raise
            result["output_json_sha256"] = hashlib.sha256(payload).hexdigest()
            active["output_names"].add(key)
        else:
            result.pop("output_json_sha256", None)
        active["record_ids"].add(result["id"])
        active["uncompleted_records"].append(result)
        return result

    def complete_photo(self, photo_id):
        if not isinstance(photo_id, str) or not photo_id:
            raise ValueError("Invalid photo ID")
        if self._active is None or not self._active["uncompleted_records"]:
            raise ValueError("Cannot complete a photo without output records")
        active = self._active
        if photo_id in self._completed or photo_id in active["photo_ids"]:
            raise ValueError("Photo was already completed")
        for record in active["uncompleted_records"]:
            record["checkpoint_photo_id"] = photo_id
            active["manifest"].write(_json_bytes(record).decode("utf-8") + "\n")
            active["writer"].writerow(_csv_row(record))
            active["records"].append(record)
        active["uncompleted_records"].clear()
        active["photo_ids"].append(photo_id)
        if len(active["photo_ids"]) >= self.checkpoint_every:
            return self.commit()
        return None

    @staticmethod
    def _close_handles(active):
        for archive in active["archives"].values():
            archive.close()
        for name in ("manifest", "csvfile"):
            handle = active[name]
            if not handle.closed:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()

    def commit(self):
        if self._closed:
            raise RuntimeError("Checkpoint store is closed")
        adopted = self._adopt_renamed_active()
        if adopted is not None:
            return json.loads(_json_bytes(adopted))
        active = self._active
        if active is None:
            return None
        if active["write_failed"]:
            raise RuntimeError("Cannot commit a failed annotation write")
        if active["uncompleted_records"]:
            raise RuntimeError("Cannot commit an incomplete photo")
        if not active["photo_ids"]:
            return None
        self._close_handles(active)
        files = {}
        for file in sorted(active["path"].rglob("*")):
            if file.is_file() and file != active["path"] / "completed.json":
                with file.open("r+b") as handle:
                    os.fsync(handle.fileno())
                files[file.relative_to(active["path"]).as_posix()] = _hash_file(file)
        metadata = {"schema_version": 1, "part": active["part"], "layout": self.layout,
                    "completed_photo_ids": active["photo_ids"], "record_count": len(active["records"]),
                    "counters": _record_counts(active["records"]), "files_sha256": files}
        marker = active["path"] / "completed.json"
        if marker.exists():
            # A Ctrl+C after a complete marker write may retry commit. Never
            # replace an incomplete or mismatched marker with a success marker.
            try:
                if json.loads(marker.read_text(encoding="utf-8")) != metadata:
                    raise ValueError("Pending completion marker changed")
            except json.JSONDecodeError as exc:
                raise ValueError("Pending completion marker is incomplete") from exc
        else:
            with marker.open("xb") as handle:
                handle.write(_json_bytes(metadata))
                handle.flush()
                os.fsync(handle.fileno())
        # The directory still has a pending name and can be safely abandoned if
        # a payload is corrupt or was written without a bound manifest record.
        self._validate_chunk(active["path"], reference_root=active["final"], part_name=active["part"])
        _sync_directory(active["path"])
        if active["final"].exists():
            raise FileExistsError("Committed chunk already exists")
        active["commit_metadata"] = metadata
        active["commit_bytes"] = sum(file.stat().st_size for file in active["path"].rglob("*") if file.is_file())
        try:
            active["path"].rename(active["final"])
        finally:
            # A signal may be delivered after the rename succeeded but before
            # the following Python statement runs. Adopt that durable directory
            # in the signal's finally path, without ever quarantining it.
            self._adopt_renamed_active()
        _sync_directory(self.chunks_dir)
        return json.loads(_json_bytes(metadata))

    def _adopt_renamed_active(self):
        active = self._active
        if active is None or active["path"].exists():
            return None
        if not active["final"].is_dir() or "commit_metadata" not in active:
            raise RuntimeError("Pending checkpoint disappeared before a verified commit")
        metadata = active["commit_metadata"]
        if not any(path == active["final"] for path, _ in self._chunks):
            counts = json.loads(_json_bytes(self._counts))
            _merge_counts(counts, metadata["counters"])
            # Replace all cached durable totals in one assignment. A second
            # signal cannot expose a half-updated completed-ID/count snapshot.
            self._state = {
                "chunks": self._chunks + [(active["final"], metadata)],
                "completed": self._completed | set(active["photo_ids"]),
                "counts": counts, "bytes": self._bytes_written + active["commit_bytes"],
                "next_part": max(self._next_part, int(active["part"].split("_")[1]) + 1),
            }
        self._active = None
        return metadata

    def _quarantine(self, path):
        match = _PENDING.fullmatch(path.name)
        if not match or path.is_symlink() or path.parent != self.chunks_dir:
            raise ValueError("Only owned pending chunks may be quarantined")
        target = self.chunks_dir / (f".abandoned_part_{match.group(1)}_" + uuid.uuid4().hex)
        path.rename(target)
        _sync_directory(self.chunks_dir)
        return target

    def close(self, commit=True):
        if self._closed:
            return
        try:
            self._adopt_renamed_active()
            if self._active is not None:
                if commit and not self._active["uncompleted_records"] and self._active["photo_ids"]:
                    self.commit()
                else:
                    self._close_handles(self._active)
                    self._quarantine(self._active["path"])
                    self._active = None
        finally:
            self._closed = True

    def recover(self):
        """Return only durable completed work, never an open pending batch."""
        return {"completed_photo_ids": set(self._completed),
                "counters": json.loads(_json_bytes(self._counts)), "chunk_count": len(self._chunks),
                "bytes_written": self._bytes_written}

    @property
    def completed_photo_ids(self):
        return set(self._completed)

    @property
    def counters(self):
        return json.loads(_json_bytes(self._counts))

    @property
    def chunk_count(self):
        return len(self._chunks)

    @property
    def bytes_written(self):
        return self._bytes_written

    def iter_records(self):
        for path, _ in self._chunks:
            yield from self._read_records(path)

    def regenerate_catalogs(self):
        """Atomically rebuild top catalogs from committed chunks, using no ZIP append."""
        token = uuid.uuid4().hex
        manifest_tmp = self.run_dir / (".manifest_" + token + ".tmp")
        csv_tmp = self.run_dir / (".classification_" + token + ".tmp")
        with manifest_tmp.open("x", encoding="utf-8", newline="\n") as manifest, csv_tmp.open("x", encoding="utf-8-sig", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for record in self.iter_records():
                manifest.write(_json_bytes(record).decode("utf-8") + "\n")
                writer.writerow(_csv_row(record))
            for handle in (manifest, csvfile):
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(manifest_tmp, self.run_dir / "manifest.jsonl")
        os.replace(csv_tmp, self.run_dir / "classification.csv")
        _sync_directory(self.run_dir)
        return {"manifest": str(self.run_dir / "manifest.jsonl"), "classification": str(self.run_dir / "classification.csv")}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close(commit=exc_type is None)

