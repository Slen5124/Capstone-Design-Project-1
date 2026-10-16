#!/usr/bin/env python3
"""Read-only LabelMe ZIP quality audit. no_rule_trigger is NOT a visual pass."""
import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import posixpath
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyzipper
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_JSON = 64 * 1024 * 1024


def norm(path):
    return posixpath.normpath(str(path).replace("\\", "/")).lstrip("/").casefold()


def base(path):
    return posixpath.basename(norm(path))


def stem(path):
    return posixpath.splitext(norm(path))[0]


def number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def dimensions(doc):
    values = [doc.get("imageWidth"), doc.get("imageHeight")]
    return tuple(int(v) for v in values) if all(
        number(v) and v > 0 and v == int(v) for v in values) else None


def row(kind, photo=None):
    return {
        "record_type": kind, "photo_id": photo["id"] if photo else "",
        "photo_zip": str(photo["path"]) if photo else "",
        "photo_entry": photo["info"].filename if photo else "",
        "json_entries": [], "issues": [], "warnings": [], "roi_coverage": [],
        "semantic_status": "not_checked", "_candidate": False,
        "_abstain": False, "_roi_candidate": False,
    }


def flag(record, code, candidate=True):
    record["issues"].append(code)
    record["_candidate" if candidate else "_abstain"] = True


def validate(doc, record):
    geometries = defaultdict(list)
    if not isinstance(doc, dict):
        flag(record, "JSON_ROOT_NOT_OBJECT")
        return geometries, None, False
    if not isinstance(doc.get("imagePath"), str) or not doc["imagePath"].strip():
        flag(record, "IMAGE_PATH_MISSING_OR_INVALID")
    size = dimensions(doc)
    if size is None:
        flag(record, "INVALID_IMAGE_DIMENSIONS")
    shapes = doc.get("shapes")
    if not isinstance(shapes, list):
        flag(record, "SHAPES_NOT_LIST")
        return geometries, size, False
    if not shapes:
        flag(record, "EMPTY_SHAPES")
        return geometries, size, False
    complete = True
    for index, shape in enumerate(shapes):
        prefix = f"SHAPE_{index}"
        if not isinstance(shape, dict):
            flag(record, prefix + "_NOT_OBJECT")
            complete = False
            continue
        label = shape.get("label")
        if not isinstance(label, str) or not label.strip():
            flag(record, prefix + "_INVALID_LABEL")
            complete = False
            continue
        kind = shape.get("shape_type") or "polygon"
        if not isinstance(kind, str) or kind not in {"polygon", "rectangle"}:
            flag(record, prefix + "_UNSUPPORTED_GEOMETRY", candidate=False)
            complete = False
            continue
        points = shape.get("points")
        if (not isinstance(points, list) or not all(
                isinstance(p, (list, tuple)) and len(p) == 2
                and all(number(v) for v in p) for p in points)):
            flag(record, prefix + "_INVALID_POINTS")
            complete = False
            continue
        points = [tuple(p) for p in points]
        required = 2 if kind == "rectangle" else 3
        if len(set(points)) < required or (kind == "rectangle" and len(points) != 2):
            flag(record, prefix + "_INSUFFICIENT_DISTINCT_POINTS")
            complete = False
            continue
        try:
            if kind == "rectangle":
                (x1, y1), (x2, y2) = points
                geometry = box(min(x1, x2), min(y1, y2),
                               max(x1, x2), max(y1, y2))
            else:
                geometry = Polygon(points)
            if (not math.isfinite(geometry.area) or geometry.area <= 1e-8
                    or not geometry.is_valid):
                flag(record, prefix + "_DEGENERATE_OR_INVALID_POLYGON")
                complete = False
                continue
            if size and any(x < 0 or y < 0 or x > size[0] or y > size[1]
                            for x, y in points):
                flag(record, prefix + "_OUT_OF_BOUNDS")
                complete = False
                continue
            geometries[label.strip().casefold()].append((index, geometry))
        except Exception as exc:
            flag(record, prefix + "_GEOMETRY_ERROR_" + type(exc).__name__)
            complete = False
    hits = 0
    for entries in geometries.values():
        try:
            polys = [g for _, g in entries]
            tree = STRtree(polys)
            for i, geometry in enumerate(polys):
                for j in tree.query(geometry):
                    j = int(j)
                    if j <= i:
                        continue
                    overlap = geometry.intersection(polys[j]).area
                    union = geometry.area + polys[j].area - overlap
                    if not math.isfinite(overlap) or not math.isfinite(union):
                        flag(record, "DUPLICATE_MEASURE_NONFINITE", candidate=False)
                        complete = False
                        continue
                    if union > 0 and overlap / union >= 0.98:
                        flag(record, f"DUPLICATE_SHAPES_{entries[i][0]}_{entries[j][0]}")
                        hits += 1
                    if hits >= 100:
                        record["warnings"].append("DUPLICATE_SHAPE_DETAILS_TRUNCATED")
                        return geometries, size, complete
        except Exception as exc:
            flag(record, "DUPLICATE_GEOMETRY_CHECK_ERROR_" + type(exc).__name__,
                 candidate=False)
            complete = False
    return geometries, size, complete


def load_roi(path):
    if not path:
        return {}
    with open(path, "r", encoding="utf-8-sig") as handle:
        doc = json.load(handle)
    images = doc.get("images") if isinstance(doc, dict) else None
    if not isinstance(images, dict):
        raise ValueError("ROI config requires an images object")
    result = {}
    for name, spec in images.items():
        if not isinstance(name, str) or norm(name) != base(name):
            raise ValueError("ROI keys must be individual image basenames")
        key = base(name)
        if key in result or not isinstance(spec, dict):
            raise ValueError("Duplicate or invalid ROI image specification")
        regions = spec.get("regions", [])
        if not isinstance(regions, list):
            raise ValueError("ROI regions must be a list")
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError("Each ROI region must be an object")
            bounds, threshold = region.get("box"), region.get("min_coverage", 0.6)
            if (not isinstance(region.get("label"), str) or not region["label"].strip()
                    or not isinstance(bounds, list) or len(bounds) != 4
                    or not all(number(v) and 0 <= v <= 1 for v in bounds)
                    or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]
                    or not number(threshold) or not 0 <= threshold <= 1):
                raise ValueError("Invalid normalized ROI or coverage threshold")
        result[key] = spec
    return result


def check_roi(spec, geometries, size, complete, record):
    if spec.get("coordinate_verified") is not True or not spec.get("regions"):
        record["semantic_status"] = "abstain"
        record["warnings"].append("ROI_COORDINATES_NOT_VERIFIED")
        return
    if not size or not complete:
        record["semantic_status"] = "abstain"
        record["warnings"].append("ROI_GEOMETRY_UNUSABLE")
        return
    record["semantic_status"] = "roi_rules_only"
    for index, region in enumerate(spec["regions"]):
        x1, y1, x2, y2 = region["box"]
        entries = geometries.get(region["label"].strip().casefold(), [])
        try:
            target = box(x1 * size[0], y1 * size[1], x2 * size[0], y2 * size[1])
            coverage = (unary_union([g for _, g in entries]).intersection(target).area
                        / target.area) if entries else 0.0
            if not math.isfinite(coverage):
                raise ValueError("Non-finite ROI coverage")
        except Exception as exc:
            record["semantic_status"] = "abstain"
            record["warnings"].append("ROI_GEOMETRY_CHECK_ERROR_" + type(exc).__name__)
            continue
        record["roi_coverage"].append(f"{index}:{region['label']}={coverage:.4f}")
        if coverage < region.get("min_coverage", 0.6):
            record["_roi_candidate"] = True
            record["issues"].append(f"ROI_{index}_LOW_LABEL_COVERAGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos", nargs="+", required=True, type=Path)
    parser.add_argument("--json-zip", required=True, type=Path)
    parser.add_argument("--out", default=Path("qa_results"), type=Path)
    parser.add_argument("--hash-duplicates", action="store_true")
    parser.add_argument("--check-images", action="store_true")
    parser.add_argument("--roi-config", type=Path)
    args = parser.parse_args()
    roi = load_roi(args.roi_config)
    archives = []
    try:
        paths = [p.resolve() for p in args.photos] + [args.json_zip.resolve()]
        if len(set(paths)) != len(paths):
            parser.error("Each ZIP input must be specified once")
        for path in paths:
            archives.append(pyzipper.AESZipFile(path, "r"))
        if any(i.flag_bits & 1 for archive in archives for i in archive.infolist()):
            password = os.environ.get("QA_ZIP_PASSWORD")
            if password is None:
                password = getpass.getpass("ZIP password: ")
            for archive in archives:
                archive.setpassword(password.encode("utf-8"))
            del password
        photos = []
        by_path, by_stem, by_base, by_base_stem = (
            defaultdict(list) for _ in range(4))
        for archive_index, archive in enumerate(archives[:-1]):
            for entry_index, info in enumerate(archive.infolist()):
                if info.is_dir() or posixpath.splitext(base(info.filename))[1] not in IMAGE_EXT:
                    continue
                photo = {"id": f"P{archive_index}:{entry_index}", "info": info,
                         "archive": archive, "path": paths[archive_index]}
                photos.append(photo)
                for mapping, key in (
                    (by_path, norm(info.filename)), (by_stem, stem(info.filename)),
                    (by_base, base(info.filename)),
                    (by_base_stem, stem(base(info.filename)))):
                    mapping[key].append(photo)
        if not photos:
            parser.error("No supported image entries found")
        records = {p["id"]: row("image", p) for p in photos}
        extra, uncertain, roi_used, decoded = [], set(), set(), {}

        def match(info, doc):
            image_path = doc.get("imagePath") if isinstance(doc, dict) else None
            if isinstance(image_path, str) and image_path.strip():
                relative_key = norm(posixpath.join(
                    posixpath.dirname(norm(info.filename)),
                    image_path.replace("\\", "/")))
                strict = {}
                for key in (norm(image_path), relative_key):
                    for photo in by_path.get(key, []):
                        strict[photo["id"]] = photo
                if strict:
                    return list(strict.values()), "strict_imagePath_or_json_relative"
                choices = by_base.get(base(image_path), [])
                return choices, "unique_basename" if choices else "unmatched"
            for mapping, key, method in (
                (by_stem, stem(info.filename), "json_relative_stem"),
                (by_base_stem, stem(base(info.filename)), "json_basename_stem")):
                choices = mapping.get(key, [])
                if choices:
                    return choices, method
            return [], "unmatched"

        def image_size(photo):
            if photo["id"] not in decoded:
                try:
                    with photo["archive"].open(photo["info"]) as stream:
                        with Image.open(stream) as image:
                            image.load()
                            decoded[photo["id"]] = (image.size, None)
                except Exception as exc:
                    decoded[photo["id"]] = (None, type(exc).__name__)
            return decoded[photo["id"]]

        json_infos = [(i, info) for i, info in enumerate(archives[-1].infolist())
                      if not info.is_dir() and base(info.filename).endswith(".json")]
        if not json_infos:
            parser.error("No JSON entries found in --json-zip")
        json_index_complete, json_error_counts = True, Counter()
        for count, (entry_index, info) in enumerate(json_infos, 1):
            doc, error, parse_error = None, None, False
            try:
                if info.file_size > MAX_JSON:
                    error = "JSON_SIZE_LIMIT"
                else:
                    with archives[-1].open(info) as stream:
                        payload = stream.read(MAX_JSON + 1)
                    if len(payload) > MAX_JSON:
                        error = "JSON_SIZE_LIMIT"
                    else:
                        try:
                            doc = json.loads(payload)
                        except (ValueError, UnicodeError, RecursionError):
                            error, parse_error = "JSON_PARSE_ERROR", True
            except Exception as exc:
                error = "JSON_READ_ERROR_" + type(exc).__name__
            if error:
                json_index_complete = False
                json_error_counts[error] += 1
            choices, method = match(info, doc)
            if len(choices) == 1:
                photo, record = choices[0], records[choices[0]["id"]]
            else:
                photo, record = None, row("unmatched_json")
                extra.append(record)
                uncertain.update(p["id"] for p in choices)
                if choices:
                    flag(record, "AMBIGUOUS_IMAGE_PAIR", candidate=False)
                elif (isinstance(doc, dict) and isinstance(doc.get("imagePath"), str)
                      and doc["imagePath"].strip()):
                    flag(record, "ORPHAN_JSON")
                else:
                    flag(record, "UNRESOLVED_JSON_PAIR", candidate=False)
            record["json_entries"].append(f"J{entry_index}:{info.filename}")
            record["warnings"].append("MATCH_METHOD_" + method)
            if error:
                flag(record, error, candidate=parse_error)
            else:
                geometries, declared, complete = validate(doc, record)
                if photo and args.check_images:
                    actual, decode_error = image_size(photo)
                    if decode_error:
                        flag(record, "IMAGE_READ_OR_DECODE_ERROR_" + decode_error,
                             candidate=False)
                        complete = False
                    elif declared and actual != declared:
                        flag(record, f"IMAGE_SIZE_MISMATCH_{declared}_{actual}")
                        complete = False
                if photo and base(photo["info"].filename) in roi:
                    key = base(photo["info"].filename)
                    roi_used.add(key)
                    if len(by_base[key]) != 1:
                        record["semantic_status"] = "abstain"
                        record["warnings"].append("ROI_BASENAME_AMBIGUOUS")
                    else:
                        check_roi(roi[key], geometries, declared, complete, record)
                del geometries
            if count % 500 == 0:
                print(f"JSON checked: {count}/{len(json_infos)}", flush=True)
        for photo in photos:
            record = records[photo["id"]]
            if not record["json_entries"]:
                if photo["id"] in uncertain:
                    flag(record, "AMBIGUOUS_JSON_PAIR", candidate=False)
                elif not json_index_complete:
                    flag(record, "JSON_INDEX_INCOMPLETE", candidate=False)
                else:
                    flag(record, "MISSING_JSON")
            elif len(record["json_entries"]) > 1:
                flag(record, "MULTIPLE_JSON_FOR_IMAGE")
                if record["semantic_status"] != "not_checked":
                    record["semantic_status"] = "abstain"
            if args.check_images:
                _, error = image_size(photo)
                if error and "IMAGE_READ_OR_DECODE_ERROR_" + error not in record["issues"]:
                    flag(record, "IMAGE_READ_OR_DECODE_ERROR_" + error, candidate=False)
            key = base(photo["info"].filename)
            if key in roi and record["semantic_status"] == "not_checked":
                roi_used.add(key)
                record["semantic_status"] = "abstain"
                record["warnings"].append("ROI_COULD_NOT_BE_CHECKED")
        crc_groups, size_groups = defaultdict(list), defaultdict(list)
        for photo in photos:
            info = photo["info"]
            crc_groups[(info.CRC, info.file_size)].append(photo)
            size_groups[info.file_size].append(photo)
        hash_candidates = {}
        for (crc, _), group in crc_groups.items():
            if crc and len(group) > 1:
                for photo in group:
                    records[photo["id"]]["warnings"].append("CRC_SIZE_DUPLICATE_CANDIDATE")
                    hash_candidates[photo["id"]] = photo
        for group in size_groups.values():
            if len(group) > 1 and any(p["info"].CRC == 0 for p in group):
                for photo in group:
                    records[photo["id"]]["warnings"].append("SIZE_ONLY_DUPLICATE_CANDIDATE")
                    hash_candidates[photo["id"]] = photo
        digest_groups, hashed = defaultdict(list), 0
        if args.hash_duplicates:
            for count, photo in enumerate(hash_candidates.values(), 1):
                try:
                    digest = hashlib.sha256()
                    with photo["archive"].open(photo["info"]) as stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            digest.update(chunk)
                    digest_groups[(photo["info"].file_size, digest.hexdigest())].append(photo)
                    hashed += 1
                except Exception as exc:
                    flag(records[photo["id"]], "DUPLICATE_HASH_READ_ERROR_"
                         + type(exc).__name__, candidate=False)
                if count % 500 == 0:
                    print(f"Duplicate candidates hashed: {count}/{len(hash_candidates)}",
                          flush=True)
        exact_groups = [g for g in digest_groups.values() if len(g) > 1]
        for group_index, group in enumerate(exact_groups, 1):
            for photo in group:
                flag(records[photo["id"]], f"EXACT_DUPLICATE_IMAGE_GROUP_{group_index}")
        output = list(records.values()) + extra
        for record in output:
            record["structural_status"] = ("review_candidate" if record["_candidate"]
                else "abstain" if record["_abstain"] else "no_rule_trigger")
            record["quality_status"] = (
                "review_candidate" if record["_candidate"] or record["_roi_candidate"]
                else "abstain" if record["_abstain"] or
                record["semantic_status"] == "abstain" else "no_rule_trigger")
            record["issues"] = list(dict.fromkeys(record["issues"]))
            record["warnings"] = list(dict.fromkeys(record["warnings"]))
        args.out.mkdir(parents=True, exist_ok=True)
        run_dir = args.out.resolve() / datetime.now(timezone.utc).strftime(
            "run_%Y%m%dT%H%M%S_%fZ")
        run_dir.mkdir(exist_ok=False)
        fields = ["record_type", "photo_id", "photo_zip", "photo_entry", "json_entries",
                  "quality_status", "structural_status", "semantic_status", "issues",
                  "warnings", "roi_coverage"]
        with (run_dir / "quality.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for record in output:
                writer.writerow({key: ";".join(record[key]) if isinstance(record[key], list)
                                 else record[key] for key in fields})
        summary = {
            "input_photos": len(photos), "input_json": len(json_infos),
            "json_index_complete": json_index_complete,
            "json_input_error_counts": dict(json_error_counts),
            "photos_without_unique_json": sum(len(r["json_entries"]) != 1
                                             for r in records.values()),
            "unmatched_json": len(extra),
            "confirmed_orphan_json": sum("ORPHAN_JSON" in r["issues"] for r in extra),
            "ambiguous_json": sum("AMBIGUOUS_IMAGE_PAIR" in r["issues"] for r in extra),
            "photos_with_multiple_json": sum(len(r["json_entries"]) > 1
                                            for r in records.values()),
            "image_quality_counts": dict(Counter(r["quality_status"]
                                                for r in records.values())),
            "all_row_structural_counts": dict(Counter(r["structural_status"] for r in output)),
            "image_semantic_counts": dict(Counter(r["semantic_status"]
                                                 for r in records.values())),
            "issue_counts": dict(Counter(code for r in output for code in r["issues"])),
            "duplicates_checked": {
                "method": "SHA256 on metadata candidates" if args.hash_duplicates
                          else "metadata warnings only",
                "candidate_images": len(hash_candidates), "hashed_images": hashed,
                "exact_groups": len(exact_groups),
                "exact_extra_images": sum(len(g) - 1 for g in exact_groups)},
            "roi_config_unused_keys": sorted(set(roi) - roi_used),
            "inspection_scope": {
                "structural": "LabelMe fields and polygon/rectangle geometry.",
                "image_decode_attempts": len(decoded),
                "image_decode_successes": sum(e is None for _, e in decoded.values()),
                "image_decode_failures": sum(e is not None for _, e in decoded.values()),
                "semantic": "Verified per-image ROI coverage only; no automatic "
                            "camera grouping or reference propagation.",
                "json_index_policy": "Any JSON read/parse/size-limit failure "
                                     "prevents definitive missing-JSON judgments."},
            "settings": {
                "photos": [str(p) for p in paths[:-1]], "json_zip": str(paths[-1]),
                "out_root": str(args.out.resolve()), "out": str(run_dir),
                "hash_duplicates": args.hash_duplicates, "check_images": args.check_images,
                "roi_config": str(args.roi_config.resolve()) if args.roi_config else None,
                "json_size_limit_bytes": MAX_JSON, "duplicate_polygon_iou": 0.98},
            "meaning": {
                "no_rule_trigger": "No candidate/abstain rule fired; warnings may "
                                   "exist. NOT a visual quality pass.",
                "roi_rules_only": "Coverage checked in this individual image's verified ROI.",
                "abstain": "Insufficient or ambiguous evidence; manual review required.",
                "roi_warning": "Only use visible, unoccluded regions with coordinates "
                               "verified in the named image. No cross-image propagation."}}
        with (run_dir / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        print(f"Saved: {run_dir / 'quality.csv'}")
        print(f"Saved: {run_dir / 'summary.json'}")
        print(json.dumps(summary["image_quality_counts"], ensure_ascii=False))
    finally:
        for archive in archives:
            archive.close()


if __name__ == "__main__":
    main()

