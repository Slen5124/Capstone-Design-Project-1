"""R01: connect the existing qa_v3 run (manifest + ZIPs) to image records and check them.

Nothing here writes to the source ZIPs or to ``automatic_results``. Photos are
read from the original ZIP on demand (never extracted to disk).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath

from PIL import Image

from .legacy import ExportDataset, SiteExportDataset
from .runctx import ItemCache, Progress, RunContext, atomic_write_json, read_jsonl
from .schemas import content_hash, sha256_bytes

HOLD_CODES = {
    "PHOTO_ZIP_MISSING", "PHOTO_ENTRY_MISSING", "PHOTO_HASH_MISMATCH", "PHOTO_DECODE_FAILED",
    "ANNOTATION_OUTPUT_MISSING", "ANNOTATION_ENTRY_MISSING", "ANNOTATION_HASH_MISMATCH",
    "ANNOTATION_PARSE_FAILED", "ANNOTATION_SIZE_MISMATCH", "ANNOTATION_NOT_LABELME",
}


class SourceIntegrityError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _basename(value: str) -> str:
    return PureWindowsPath(value).name if "\\" in value else PurePosixPath(value).name


# ------------------------------------------------------------ capture time
def parse_capture_time(entry: str, cfg: dict) -> dict:
    """Capture time from the photo file name; download suffixes are kept apart."""
    pattern = re.compile(cfg["filename_regex"], re.IGNORECASE)
    name = _basename(entry)
    match = pattern.search(name)
    out = {"time_local": None, "source": "filename", "status": "unparsed", "timezone": cfg.get("timezone"),
           "timezone_confirmed": bool(cfg.get("timezone_confirmed")) and cfg.get("timezone") is not None,
           "download_time_in_name": None, "copy_suffix": None}
    if not match:
        return out
    d, t = match.group("date"), match.group("time")
    try:
        from datetime import datetime
        stamp = datetime.strptime(d + t, "%Y%m%d%H%M%S")
    except ValueError:
        out["status"] = "unparsed_invalid_date"
        return out
    out["time_local"] = stamp.isoformat()
    out["status"] = "parsed"
    if match.groupdict().get("copy"):
        out["copy_suffix"] = match.group("copy").strip()
        out["status"] = "parsed_copy_suffix"
    if match.groupdict().get("download"):
        out["download_time_in_name"] = match.group("dl")
        out["status"] = "parsed_download_suffix_present"
    return out


def make_image_id(site_id: str, zip_path: str, entry: str, time_local: str | None) -> str:
    digest = hashlib.sha1((_basename(zip_path) + "/" + entry).encode("utf-8")).hexdigest()[:8]
    stamp = time_local.replace("-", "").replace(":", "") if time_local else "notime"
    return f"{site_id}-{stamp}-{digest}"


# ------------------------------------------------------------------ reader
class ArchivePool(ExportDataset):
    """ExportDataset's ZIP handle pool without loading a manifest."""

    def __init__(self, password=None):  # noqa: D401 - intentionally minimal
        import os
        self.handles, self.records, self.verify_hashes = {}, [], True
        self.password = password if password is not None else os.environ.get("QA_ZIP_PASSWORD")


class SourceReader:
    """Read photos/annotations for ingested image records (with hash checks)."""

    def __init__(self, ctx: RunContext | None = None):
        self.ctx = ctx
        self.pool = ArchivePool()
        # Image records keep the paths exactly as written in the qa_v3 manifest (e.g. D:\\...);
        # they are translated for the current machine only at read time.
        self._m = ctx.map if ctx is not None else (lambda p: p)

    def photo_bytes(self, image: dict, verify: bool = True) -> bytes:
        photo = image["source_photo"]
        zip_path = self._m(photo["zip"])
        if not Path(zip_path).exists():
            raise SourceIntegrityError("PHOTO_ZIP_MISSING", photo["zip"])
        archive = self.pool.archive(zip_path, True)
        try:
            data = archive.read(photo["entry"])
        except KeyError as error:
            raise SourceIntegrityError("PHOTO_ENTRY_MISSING", photo["entry"]) from error
        if verify and photo.get("sha256") and sha256_bytes(data) != photo["sha256"]:
            raise SourceIntegrityError("PHOTO_HASH_MISMATCH", photo["entry"])
        return data

    def rgb(self, image: dict, max_side: int | None = None, verify: bool = True) -> tuple[Image.Image, float]:
        """RGB image (optionally downscaled); returns (image, scale_from_original)."""
        data = self.photo_bytes(image, verify)
        with Image.open(io.BytesIO(data)) as im:
            if max_side:
                im.draft("RGB", (max_side, max_side))  # fast JPEG DCT scaling
            im = im.convert("RGB")
        w0, _ = image["size"]
        if max_side and max(im.size) > max_side:
            factor = max_side / max(im.size)
            im = im.resize((max(1, round(im.width * factor)), max(1, round(im.height * factor))), Image.BILINEAR)
        return im, im.width / w0

    def annotation_bytes(self, image: dict) -> bytes:
        ann = image.get("annotation")
        if not ann:
            raise SourceIntegrityError("ANNOTATION_OUTPUT_MISSING", image["image_id"])
        if ann.get("path"):
            path = Path(self._m(ann["path"]))
            if not path.exists():
                raise SourceIntegrityError("ANNOTATION_ENTRY_MISSING", str(path))
            return path.read_bytes()
        zip_path = self._m(ann["zip"])
        if not Path(zip_path).exists():
            raise SourceIntegrityError("ANNOTATION_ENTRY_MISSING", ann["zip"])
        try:
            return self.pool.archive(zip_path).read(ann["entry"])
        except KeyError as error:
            raise SourceIntegrityError("ANNOTATION_ENTRY_MISSING", ann["entry"]) from error

    def annotation(self, image: dict, verify: bool = True) -> dict:
        payload = self.annotation_bytes(image)
        ann = image["annotation"]
        if verify and ann.get("sha256") and sha256_bytes(payload) != ann["sha256"]:
            raise SourceIntegrityError("ANNOTATION_HASH_MISMATCH", ann.get("entry", ""))
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SourceIntegrityError("ANNOTATION_PARSE_FAILED", str(error)) from error

    def close(self):
        self.pool.close()


# ------------------------------------------------------------------ ingest
def _select(records: list[dict], cap_cfg: dict, limit: int, select: str, dates: set[str] | None,
            entries: set[str] | None, must_ids: set[str] | None = None, site_id: str | None = None) -> list[dict]:
    """``must_ids``: image ids that are always included in a limited run (photos with labels / references)."""
    rows = _select_core(records, cap_cfg, limit, select, dates, entries)
    if not limit or not must_ids:
        return rows
    have = {id(r) for r in rows}
    extra = []
    for record in records:
        if id(record) in have:
            continue
        photo = record["source_photo"]
        cap = parse_capture_time(photo["entry"], cap_cfg)
        if make_image_id(site_id, photo["zip"], photo["entry"], cap["time_local"]) in must_ids:
            extra.append(record)
    return rows + extra


def _select_core(records: list[dict], cap_cfg: dict, limit: int, select: str, dates: set[str] | None,
                 entries: set[str] | None) -> list[dict]:
    keyed = []
    for record in records:
        entry = record["source_photo"]["entry"]
        cap = parse_capture_time(entry, cap_cfg)
        if dates and (cap["time_local"] or "")[:10] not in dates:
            continue
        if entries and entry not in entries:
            continue
        keyed.append(((cap["time_local"] or "9999"), entry, record))
    keyed.sort(key=lambda k: (k[0], k[1]))
    rows = [k[2] for k in keyed]
    if not limit or limit >= len(rows):
        return rows
    if select == "first":
        return rows[:limit]
    if limit == 1:
        return rows[:1]
    step = (len(rows) - 1) / (limit - 1)
    picked = sorted({round(i * step) for i in range(limit)})
    return [rows[i] for i in picked]


def inspect(reader: SourceReader, record: dict, site: dict) -> dict:
    """Build one image record and run R01 checks. Problems are recorded, never hidden."""
    photo = record["source_photo"]
    cap = parse_capture_time(photo["entry"], site["capture_time"])
    image_id = make_image_id(site["site_id"], photo["zip"], photo["entry"], cap["time_local"])
    out_ann = record.get("annotation_output")
    image = {
        "image_id": image_id, "site_id": site["site_id"], "camera_id": site["camera_id"],
        "capture": cap,
        "size": None, "annotation_size": None,
        "source_photo": {"zip": photo["zip"], "zip_name": _basename(photo["zip"]), "entry": photo["entry"],
                         "sha256": photo.get("sha256"), "sha256_verified": None},
        "annotation": None if not out_ann else {
            **({"zip": out_ann["zip"], "entry": out_ann["entry"]} if "zip" in out_ann else {"path": out_ann["path"]}),
            "sha256": record.get("output_json_sha256"), "sha256_verified": None,
            "qa_v3_classification": record.get("classification"),
            "qa_v3_label_origin": record.get("label_origin"),
            "qa_v3_human_confirmed": record.get("human_confirmed"),
            "qa_v3_training_eligible": record.get("training_eligible"),
            "qa_v3_unresolved_codes": record.get("unresolved_codes", []),
            "qa_v3_reference_id": record.get("reference_id"),
            "qa_v3_manifest_id": record.get("id"),
            "source_json_entry": (record.get("source_json") or {}).get("entry"),
            "source_json_sha256": (record.get("source_json") or {}).get("sha256"),
            "source_json_zip": record.get("source_json_zip"),
            "pair_match_method": (record.get("source_json") or {}).get("match_method"),
        },
        "label_counts": {}, "shape_count": 0,
        "checks": [], "warnings": [], "status": "ok",
    }
    checks, warnings = image["checks"], image["warnings"]
    verify_photo = site.get("ingest", {}).get("verify_photo_hash", True)
    verify_ann = site.get("ingest", {}).get("verify_annotation_hash", True)
    decode = site.get("ingest", {}).get("decode_check", "header")
    try:
        data = reader.photo_bytes(image, verify=verify_photo)
        image["source_photo"]["sha256_actual"] = sha256_bytes(data)
        image["source_photo"]["sha256_verified"] = bool(verify_photo)
        try:
            with Image.open(io.BytesIO(data)) as im:
                image["size"] = list(im.size)
                if decode == "full":
                    im.load()
        except Exception as error:  # noqa: BLE001 - any decoder error is a hold
            checks.append({"code": "PHOTO_DECODE_FAILED", "detail": type(error).__name__})
    except SourceIntegrityError as error:
        checks.append({"code": error.code, "detail": error.detail})
    if cap["status"].startswith("unparsed"):
        warnings.append({"code": "CAPTURE_TIME_UNPARSED", "detail": photo["entry"]})
    elif cap["status"] == "parsed_download_suffix_present":
        warnings.append({"code": "DOWNLOAD_TIME_SUFFIX_IN_NAME",
                         "detail": "suffix is a download time; capture time taken from the camera stamp before it"})
    if not out_ann:
        checks.append({"code": "ANNOTATION_OUTPUT_MISSING", "detail": "qa_v3 exported no JSON for this photo"})
    else:
        try:
            doc = reader.annotation(image, verify=verify_ann)
            image["annotation"]["sha256_verified"] = bool(verify_ann)
            if not isinstance(doc, dict) or not isinstance(doc.get("shapes"), list):
                checks.append({"code": "ANNOTATION_NOT_LABELME", "detail": "missing shapes list"})
            else:
                image["annotation_size"] = [doc.get("imageWidth"), doc.get("imageHeight")]
                if image["size"] and image["annotation_size"] != image["size"]:
                    checks.append({"code": "ANNOTATION_SIZE_MISMATCH",
                                   "detail": f"json {image['annotation_size']} vs photo {image['size']}"})
                labels = Counter(str(s.get("label", "")).strip() for s in doc["shapes"])
                image["label_counts"] = dict(labels)
                image["shape_count"] = len(doc["shapes"])
                kinds = Counter(s.get("shape_type") or "polygon" for s in doc["shapes"])
                if set(kinds) - {"polygon", "rectangle"}:
                    warnings.append({"code": "UNSUPPORTED_SHAPE_TYPES", "detail": dict(kinds)})
                image_path = doc.get("imagePath")
                # Case-only differences are ignored (qa_v3 lower-cases names; Windows paths are case-insensitive).
                if image_path and _basename(image_path).casefold() != _basename(photo["entry"]).casefold():
                    warnings.append({"code": "IMAGE_PATH_BASENAME_DIFFERS",
                                     "detail": f"json imagePath {image_path!r} vs entry {photo['entry']!r}"})
                if doc.get("imageData"):
                    warnings.append({"code": "EMBEDDED_IMAGE_DATA_IGNORED", "detail": "imageData present"})
        except SourceIntegrityError as error:
            checks.append({"code": error.code, "detail": error.detail})
    if any(c["code"] in HOLD_CODES for c in checks):
        image["status"] = "held"
    return image


def mark_duplicates(images: list[dict]) -> dict:
    """Group identical photo bytes. Canonical = name without copy/download suffix, else first."""
    groups = defaultdict(list)
    for image in images:
        sha = image["source_photo"].get("sha256_actual") or image["source_photo"].get("sha256")
        if sha:
            groups[sha].append(image)
    summary = {"groups": 0, "images_in_groups": 0}
    for sha, members in groups.items():
        rank = sorted(members, key=lambda im: (im["capture"]["status"] != "parsed", im["source_photo"]["entry"]))
        canonical = rank[0]["image_id"]
        for im in members:
            im["content_group"] = sha[:16]
            im["duplicate_of"] = None if im["image_id"] == canonical else canonical
        if len(members) > 1:
            summary["groups"] += 1
            summary["images_in_groups"] += len(members)
    return summary


def run_ingest(ctx: RunContext, *, limit: int = 0, select: str = "spread", dates: list[str] | None = None,
               entries_file: Path | None = None, dry_run: bool = False) -> dict:
    site = ctx.site
    src = site["source"]
    dataset = SiteExportDataset(ctx.source_run_dir, tuple(src["categories"]), src["photo_zip_contains"])
    entries = None
    if entries_file:
        entries = {line.strip() for line in Path(entries_file).read_text(encoding="utf-8").splitlines() if line.strip()}
    must = None
    if limit:
        # a limited (test) run always contains the photos the review store already refers to
        store = ctx.store()
        must = {r["key"].split("|")[0] for r in store.latest("state_label")}
        must |= {r["payload"].get("reference_image_id") for kind in ("view", "roi") for r in store.latest(kind)}
    selected = _select(dataset.records, site["capture_time"], limit, select, set(dates) if dates else None, entries,
                       must, site["site_id"])
    plan = {"source_run_dir": str(ctx.source_run_dir), "site_records": len(dataset.records),
            "excluded_categories": dataset.skipped_categories, "selected": len(selected),
            "categories": src["categories"], "limit": limit, "select": select}
    print(f"[ingest] source run {ctx.source_run_dir}")
    print(f"[ingest] site records {len(dataset.records)} (excluded categories {dataset.skipped_categories}); selected {len(selected)}")
    if dry_run:
        return plan
    stage = ctx.stage_dir("ingest")
    cache = ItemCache(stage / "cache.jsonl")
    fp_base = ctx.config_fingerprint("source", "capture_time", "ingest", "site_id", "camera_id",
                                     code=("source_adapter.py", "legacy.py", "schemas.py", "qa_v3/dataset_io.py"))
    reader = SourceReader(ctx)
    progress = Progress(ctx, "ingest", len(selected), "images")
    images = []
    try:
        for record in selected:
            key = record["source_photo"]["zip"] + "|" + record["source_photo"]["entry"]
            fp = content_hash([fp_base, record["source_photo"].get("sha256"), record.get("output_json_sha256"),
                               record.get("annotation_output")])
            cached = cache.get(key, fp)
            if cached is not None:
                images.append(cached)
                progress.tick("skipped_cached")
                continue
            image = inspect(reader, record, site)
            cache.put(key, fp, image)
            images.append(image)
            progress.tick("held" if image["status"] == "held" else "saved")
    finally:
        reader.close()
        dataset.close()
        cache.close()
        cache.maybe_compact()
    dup = mark_duplicates(images)
    ids = Counter(im["image_id"] for im in images)
    collisions = [k for k, v in ids.items() if v > 1]
    if collisions:
        raise RuntimeError(f"image_id collision: {collisions[:5]}")
    with open(stage / "images.jsonl", "w", encoding="utf-8") as handle:
        for image in images:
            handle.write(json.dumps(image, ensure_ascii=False) + "\n")
    report = dict(plan, counts=progress.finish(),
                  status=Counter(im["status"] for im in images),
                  check_codes=Counter(c["code"] for im in images for c in im["checks"]),
                  warning_codes=Counter(w["code"] for im in images for w in im["warnings"]),
                  capture_status=Counter(im["capture"]["status"] for im in images),
                  timezone_confirmed=False if not site["capture_time"].get("timezone_confirmed") else True,
                  dates=dict(sorted(Counter((im["capture"]["time_local"] or "unknown")[:10] for im in images).items())),
                  duplicates=dup, qa_v3_classification=Counter((im["annotation"] or {}).get("qa_v3_classification") for im in images),
                  label_totals=sum((Counter(im["label_counts"]) for im in images), Counter()),
                  sizes=Counter(str(im["size"]) for im in images))
    atomic_write_json(stage / "ingest_report.json", report)
    return report


def load_images(ctx: RunContext, include_held: bool = False) -> list[dict]:
    path = ctx.stage_dir("ingest") / "images.jsonl"
    if not path.exists():
        raise FileNotFoundError("run 'ingest' first")
    return [im for im in read_jsonl(path) if include_held or im["status"] == "ok"]
