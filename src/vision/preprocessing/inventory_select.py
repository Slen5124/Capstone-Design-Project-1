"""Read-only local JPEG inventory and reproducible representative-frame sampling.

No classes, state labels, or masks are inferred here. Image differences are
selection heuristics, not construction-change measurements. Dates/times are
filename candidates with no verified timezone. Every record remains unreviewed.
"""
import argparse
import collections
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

cv2.setNumThreads(1)
TIME_PATTERN = re.compile(r"-(\d{8})-(\d{6})(?: \(\d+\))?(?=\.[^.]+$)")


def parse_time(name):
    match = TIME_PATTERN.search(name)
    if not match:
        return None
    try:
        return dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def scan_one(path, site_id=None):
    stamp = parse_time(path.name)
    record = {
        "filename": path.name, "source_path": str(path.resolve()),
        "file_bytes": path.stat().st_size, "sha256": None,
        "captured_at_candidate": stamp.isoformat() if stamp else None,
        "timestamp_source": "filename_candidate", "timezone": None,
        "timestamp_verified": False, "date_candidate": stamp.date().isoformat() if stamp else None,
        "time_candidate": stamp.time().isoformat() if stamp else None,
        "camera_id": None, "camera_session_id": None,
        "site_id": site_id, "width": None, "height": None,
        "exif_orientation": None, "decode_lowres_ok": False,
        "full_resolution_decode_verified": False,
        "brightness_mean_lowres": None, "sharpness_laplacian_var_lowres": None,
        "dark_pixel_fraction_lowres": None, "bright_pixel_fraction_lowres": None,
        "perceptual_hash": None, "dhash": None, "previous_filename": None,
        "previous_gap_seconds_candidate": None, "previous_brightness_delta": None,
        "previous_spatial_difference_normalized": None, "previous_phash_hamming": None,
        "exact_duplicate_group": None, "exact_duplicate_count": 1,
        "quality_flags": [], "decode_error": None,
        "checked": False, "needs_review": True, "training_eligible": False,
        "split": "pending", "selection_reasons": [], "selected": False,
        "state_labels": None, "progress_ratio": None,
    }
    try:
        payload = path.read_bytes()
        record["sha256"] = hashlib.sha256(payload).hexdigest()
        with Image.open(io.BytesIO(payload)) as header:
            record["width"], record["height"] = header.size
            record["exif_orientation"] = int(header.getexif().get(274, 1))
        reduced = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
        if reduced is None:
            raise ValueError("OpenCV reduced JPEG decoder returned no image")
        gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
        metric_gray = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
        record["decode_lowres_ok"] = True
        record["brightness_mean_lowres"] = round(float(metric_gray.mean()), 5)
        record["sharpness_laplacian_var_lowres"] = round(float(cv2.Laplacian(metric_gray, cv2.CV_64F).var()), 5)
        record["dark_pixel_fraction_lowres"] = round(float((metric_gray < 10).mean()), 6)
        record["bright_pixel_fraction_lowres"] = round(float((metric_gray > 245).mean()), 6)
        small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        low = cv2.dct(small)[:8, :8]
        bits = low > np.median(low.reshape(-1)[1:])
        phash = sum(int(v) << i for i, v in enumerate(bits.reshape(-1)))
        record["perceptual_hash"] = f"{phash:016x}"
        dsmall = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        dbits = dsmall[:, 1:] > dsmall[:, :-1]
        record["dhash"] = f"{sum(int(v) << i for i, v in enumerate(dbits.reshape(-1))):016x}"
        spatial = cv2.resize(gray, (96, 54), interpolation=cv2.INTER_AREA).astype(np.float32)
        normalized = np.clip((spatial - spatial.mean()) / max(8.0, float(spatial.std())), -3, 3)
        if record["exif_orientation"] != 1:
            record["quality_flags"].append("exif_orientation_needs_registration_review")
        if record["brightness_mean_lowres"] < 35:
            record["quality_flags"].append("very_dark_candidate")
        if record["brightness_mean_lowres"] > 220:
            record["quality_flags"].append("very_bright_candidate")
        if record["dark_pixel_fraction_lowres"] > .35:
            record["quality_flags"].append("large_dark_area_candidate")
        if record["bright_pixel_fraction_lowres"] > .35:
            record["quality_flags"].append("large_bright_area_candidate")
        return record, normalized
    except Exception as exc:
        record["decode_error"] = f"{type(exc).__name__}: {exc}"
        record["quality_flags"].append("decode_error")
        return record, None


def font(size):
    for name in ("malgun.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheets(records, directory, prefix, per_page=16):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    cellw, cellh = 360, 245
    title_font, small_font = font(17), font(12)
    for start in range(0, len(records), per_page):
        subset = records[start:start+per_page]
        columns = 4
        rows = math.ceil(len(subset) / columns)
        canvas = Image.new("RGB", (columns * cellw, rows * cellh + 42), "#f4f4f0")
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 10), f"{prefix} | page {start // per_page + 1} | drafts / candidate timestamps", fill="#202020", font=title_font)
        for i, r in enumerate(subset):
            x, y = i % columns * cellw, 42 + i // columns * cellh
            with Image.open(r["source_path"]) as im:
                im.draft("RGB", (cellw, 200))
                im = im.convert("RGB")
                im.thumbnail((cellw-8, 198))
                canvas.paste(im, (x+4+(cellw-8-im.width)//2, y))
            draw.text((x+6, y+200), f"{r['captured_at_candidate']} [{r['width']}x{r['height']}]", fill="#202020", font=small_font)
            label = ", ".join(r.get("selection_reasons", []))[:55]
            draw.text((x+6, y+217), label, fill="#4a4a4a", font=small_font)
            if r.get("quality_flags"):
                draw.text((x+6, y+231), ", ".join(r["quality_flags"])[:55], fill="#9c3318", font=small_font)
        path = directory / f"{prefix}_{start // per_page + 1:02d}.jpg"
        canvas.save(path, quality=89)
        paths.append(str(path.resolve()))
    return paths


def write_csv(path, records):
    if not records:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as out:
        writer = csv.DictWriter(out, fieldnames=list(records[0]))
        writer.writeheader()
        for r in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in r.items()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site-id", default=None, help="Optional site identifier for inventory records")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--cap", type=int, default=220)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Output already exists; use a new directory to preserve previous run")
    args.output.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in args.source.rglob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"})
    if len({p.name for p in files}) != len(files):
        raise SystemExit("Duplicate basenames found; disambiguation required before sampling")
    files.sort(key=lambda p: (parse_time(p.name) or dt.datetime.max, p.name))
    start = time.monotonic()
    print(f"Scanning {len(files)} JPEGs read-only, workers={args.workers}", flush=True)
    records, last_spatial, last_record = [], None, None
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, (r, spatial) in enumerate(pool.map(scan_one, files, [args.site_id] * len(files)), 1):
            if spatial is not None and last_spatial is not None:
                r["previous_filename"] = last_record["filename"]
                if r["captured_at_candidate"] and last_record["captured_at_candidate"]:
                    r["previous_gap_seconds_candidate"] = (dt.datetime.fromisoformat(r["captured_at_candidate"]) - dt.datetime.fromisoformat(last_record["captured_at_candidate"])).total_seconds()
                r["previous_brightness_delta"] = round(abs(r["brightness_mean_lowres"] - last_record["brightness_mean_lowres"]), 5)
                r["previous_spatial_difference_normalized"] = round(float(np.mean(np.abs(spatial-last_spatial)) / 6), 6)
                r["previous_phash_hamming"] = (int(r["perceptual_hash"], 16) ^ int(last_record["perceptual_hash"], 16)).bit_count()
                if (r["width"],r["height"]) != (last_record["width"],last_record["height"]):
                    r["quality_flags"].append("dimension_change_needs_registration")
            if spatial is not None:
                last_spatial, last_record = spatial, r
            records.append(r)
            if index % 500 == 0 or index == len(files):
                print(f"Scanned {index}/{len(files)} in {time.monotonic()-start:.1f}s", flush=True)
    valid = [r for r in records if r["decode_lowres_ok"]]
    sharp = np.array([r["sharpness_laplacian_var_lowres"] for r in valid])
    bottom = float(np.quantile(sharp, .1)) if len(sharp) else 0
    for r in valid:
        if r["sharpness_laplacian_var_lowres"] < bottom:
            r["quality_flags"].append("lowres_sharpness_bottom_decile_candidate")
    sha_groups = collections.defaultdict(list)
    for r in records:
        if r["sha256"]:
            sha_groups[r["sha256"]].append(r)
    duplicate_groups = []
    for digest, group in sha_groups.items():
        if len(group) > 1:
            gid = f"sha256:{digest}"
            duplicate_groups.append({"group": gid, "filenames": [r["filename"] for r in group]})
            for r in group:
                r["exact_duplicate_group"], r["exact_duplicate_count"] = gid, len(group)
    by_name = {r["filename"]:r for r in records}
    by_date = collections.defaultdict(list)
    for r in valid:
        if r["date_candidate"]:
            by_date[r["date_candidate"]].append(r)
    selected = {}
    selected_hashes = set()

    def quality(r):
        brightness = r["brightness_mean_lowres"]
        return math.log1p(r["sharpness_laplacian_var_lowres"]) - abs(brightness-120)/60 - (r["dark_pixel_fraction_lowres"]+r["bright_pixel_fraction_lowres"])*4 - len(r["quality_flags"])*.3

    def add(r, reason):
        if r["filename"] in selected:
            if reason not in r["selection_reasons"]:
                r["selection_reasons"].append(reason)
            return True
        if len(selected) >= args.cap or r["sha256"] in selected_hashes or not r["decode_lowres_ok"]:
            return False
        r["selected"] = True
        r["selection_reasons"].append(reason)
        selected[r["filename"]] = r
        selected_hashes.add(r["sha256"])
        return True

    daily = []
    for date, group in sorted(by_date.items()):
        preferred = [r for r in group if "08:00:00" <= r["time_candidate"] <= "18:00:00"]
        pick = max(preferred or group, key=quality)
        add(pick, "daily_quality_representative")
        daily.append(pick)
    changes = sorted((r for r in valid if r["previous_spatial_difference_normalized"] is not None and r["captured_at_candidate"] is not None), key=lambda r:r["previous_spatial_difference_normalized"], reverse=True)
    top_changes = []
    for r in changes[:30]:
        top_changes.append({k:r[k] for k in ["filename","previous_filename","captured_at_candidate","previous_gap_seconds_candidate","previous_spatial_difference_normalized","previous_brightness_delta","previous_phash_hamming"]})
    # Every large consecutive change is a candidate, not proof of camera movement.
    # Restrict pairs to a >=60-minute separation from previously chosen change
    # pairs to avoid filling the budget with the same transient occluder event.
    event_times = []
    accepted_events = []
    for r in changes:
        stamp = dt.datetime.fromisoformat(r["captured_at_candidate"])
        if any(abs((stamp-old).total_seconds()) < 3600 for old in event_times):
            continue
        prior = by_name.get(r["previous_filename"])
        if prior is None:
            continue
        add(prior, "large_spatial_change_before_candidate")
        add(r, "large_spatial_change_after_candidate")
        event_times.append(stamp)
        accepted_events.append(r)
        if len(accepted_events) == 20:
            break
    # Include at most three representatives per ISO week before daily extras.
    weeks = collections.defaultdict(list)
    for r in daily:
        stamp = dt.date.fromisoformat(r["date_candidate"])
        weeks[f"{stamp.isocalendar().year}-W{stamp.isocalendar().week:02d}"].append(r)
    weekly = []
    for week, group in sorted(weeks.items()):
        for index in sorted({0, len(group)//2, len(group)-1}):
            r = group[index]
            add(r, "weekly_timeline_representative")
            weekly.append(r)
    # Different nominal times give examples of light and moving-object variation.
    for date, group in sorted(by_date.items()):
        nominal = [r for r in group if "04:00:00" <= r["time_candidate"] <= "21:00:00"]
        if not nominal:
            continue
        anchor = next(r for r in daily if r["date_candidate"] == date)
        alternatives = [r for r in nominal if abs((dt.datetime.fromisoformat(r["captured_at_candidate"])-dt.datetime.fromisoformat(anchor["captured_at_candidate"])).total_seconds()) >= 4*3600]
        if alternatives:
            add(max(alternatives, key=quality), "same_day_different_nominal_time")
    # One difficult candidate per week; these flags do not identify actual
    # occluders, because no occlusion model was run in the inventory step.
    for week, group_daily in sorted(weeks.items()):
        dates = {r["date_candidate"] for r in group_daily}
        group = [r for r in valid if r["date_candidate"] in dates]
        if group:
            add(min(group, key=quality), "weekly_lighting_or_sharpness_difficult_candidate")
    # Fill remaining slots with spatial-change candidates separated by >=2h
    # from selected images on that same day. No duplicated basename/hash.
    for r in changes:
        if len(selected) >= args.cap:
            break
        stamp = dt.datetime.fromisoformat(r["captured_at_candidate"])
        existing = [s for s in selected.values() if s["date_candidate"] == r["date_candidate"]]
        if all(abs((stamp-dt.datetime.fromisoformat(s["captured_at_candidate"])).total_seconds()) >= 2*3600 for s in existing):
            add(r, "spatial_variation_candidate")
    picks = sorted(selected.values(), key=lambda r:r["captured_at_candidate"] or "")
    with (args.output/"manifest.jsonl").open("w", encoding="utf-8") as out:
        for r in records:
            out.write(json.dumps(r, ensure_ascii=False)+"\n")
    write_csv(args.output/"inventory.csv", records)
    write_csv(args.output/"selection.csv", picks)
    (args.output/"selection.json").write_text(json.dumps({"selected": picks, "count":len(picks), "cap":args.cap, "all_labels_are_drafts":True}, ensure_ascii=False, indent=2),encoding="utf-8")
    (args.output/"exact_duplicate_groups.json").write_text(json.dumps(duplicate_groups,ensure_ascii=False,indent=2),encoding="utf-8")
    representative_pages = contact_sheets(picks,args.output/"contact_sheets","representatives")
    daily_pages = contact_sheets(daily,args.output/"daily_views","daily")
    weekly_pages = contact_sheets(weekly,args.output/"weekly_views","weekly")
    pair_records = []
    for r in accepted_events[:15]:
        pair_records.extend([by_name[r["previous_filename"]],r])
    pair_pages = contact_sheets(pair_records,args.output/"change_pairs","before_after",per_page=16)
    dimensions = collections.Counter(f"{r['width']}x{r['height']}" for r in records)
    flags = collections.Counter(flag for r in records for flag in r["quality_flags"])
    summary = {
        "source":str(args.source.resolve()), "output":str(args.output.resolve()),
        "jpeg_count":len(files), "valid_lowres_decode_count":len(valid),
        "decode_error_count":len(files)-len(valid), "date_count":len(by_date),
        "date_min_candidate":min(by_date) if by_date else None,
        "date_max_candidate":max(by_date) if by_date else None,
        "dimension_counts":dict(dimensions), "quality_flag_counts":dict(flags),
        "exact_duplicate_group_count":len(duplicate_groups),
        "exact_duplicate_extra_file_count":sum(len(g["filenames"])-1 for g in duplicate_groups),
        "selected_count":len(picks), "selected_cap":args.cap,
        "selected_date_count":len({r["date_candidate"] for r in picks}),
        "selection_reason_counts":dict(collections.Counter(reason for r in picks for reason in r["selection_reasons"])),
        "top30_abrupt_consecutive_spatial_changes":top_changes,
        "representative_contact_sheets":representative_pages,
        "daily_contact_sheets":daily_pages, "weekly_contact_sheets":weekly_pages,
        "change_pair_contact_sheets":pair_pages,
        "scan_seconds":round(time.monotonic()-start,2),
        "thresholds_and_method":{
            "reduced_jpeg_decode":"OpenCV IMREAD_REDUCED_COLOR_8, not full-resolution corruption verification",
            "quality_metric_size":[160,90], "spatial_metric_size":[96,54],
            "sharpness_bottom_decile_threshold":bottom,
            "very_dark_mean_threshold":35,"very_bright_mean_threshold":220,
            "dark_pixel_cutoff":10,"bright_pixel_cutoff":245,"large_dark_or_bright_fraction":.35,
            "spatial_difference":"mean abs difference of individually zero-mean/std-normalized, clipped [-3,3] grayscale thumbnails divided by6; no alignment",
            "phash":"32x32 grayscale DCT top8x8 median hash, 64bits",
            "daily_nominal_preferred_time":"08:00:00 to18:00:00 filename local candidate; timezone unverified",
            "large_spatial_pairs":20,"pair_event_separation_seconds":3600,
            "same_day_alternative_separation_seconds":14400,
            "fill_frame_separation_seconds":7200,
        },
        "limitations":[
            "No surface/person/occluder labels generated in inventory step",
            "Low-res brightness/sharpness flags are heuristic candidates, not calibrated quality labels",
            "Spatial differences may reflect lighting, people, equipment, camera movement, or work; they are not construction progress",
            "Camera/session IDs not physically verified; dimension stability does not confirm view stability",
            "Timezone and filename timestamps are not verified",
            "Only representative frames will receive later model draft masks",
            "All records are unchecked, needs_review=true, training_eligible=false, split=pending",
        ],
    }
    (args.output/"scan_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:summary[k] for k in ["jpeg_count","valid_lowres_decode_count","decode_error_count","date_count","selected_count","dimension_counts","exact_duplicate_group_count","scan_seconds"]},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
