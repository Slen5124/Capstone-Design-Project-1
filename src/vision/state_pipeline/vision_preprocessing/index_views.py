"""R02: camera views (구도). Human-defined segments + automatic *candidates*.

A view is a period during which one camera keeps the same framing. Views are
records in the review store (editable/approvable in the review UI or seeded
from the site config). The detector below only proposes boundaries:

* It compares each frame with the last confidently matched frame (sequential,
  so gradual construction progress does not accumulate) using a global
  SIFT/RANSAC homography and measures how far the image corners move. Dark
  (night) frames are not compared and are reported as uncertain. A
  whole-image homography is used **only** to detect camera motion; it is never
  used to transfer surface ROIs (that is done per target in assign_surfaces).
* Lighting change, night frames, occlusion or construction progress usually
  produce *uncertain* matches, which are reported and never become a move by
  themselves. A move is proposed only when ``confirm_frames`` consecutive frames
  match each other but are shifted against the anchor.
"""
from __future__ import annotations

import numpy as np

from .runctx import ItemCache, Progress, RunContext, atomic_write_json
from .schemas import content_hash
from .source_adapter import SourceIntegrityError, SourceReader


# ----------------------------------------------------------- view lookup
def store_views(store) -> list[dict]:
    out = []
    for rec in store.latest("view"):
        p = dict(rec["payload"])
        p["_version"] = rec["version"]
        p["_review"] = store.review_state("view", rec["key"])["status"]
        out.append(p)
    return out


def assign_view(image: dict, views: list[dict]) -> dict:
    """Return {"view_id", "status", "candidates"} for an image."""
    matches = []
    for view in views:
        if view.get("retired"):
            continue
        if image["image_id"] in (view.get("image_ids_extra") or []):
            matches.append(view)
            continue
        if view.get("camera_id") not in (None, image["camera_id"]):
            continue
        t = image["capture"]["time_local"]
        if t and view.get("start_time") and view.get("end_time") and view["start_time"] <= t <= view["end_time"]:
            matches.append(view)
    if not matches:
        return {"view_id": None, "status": "unassigned", "candidates": []}
    if len(matches) > 1:
        return {"view_id": None, "status": "ambiguous_overlapping_views", "candidates": [v["view_id"] for v in matches]}
    view = matches[0]
    return {"view_id": view["view_id"], "status": "assigned", "view_version": view["_version"],
            "view_review": view["_review"], "candidates": []}


# ------------------------------------------------------------- detection
class _Features:
    def __init__(self, max_side: int):
        import cv2
        self.cv2 = cv2
        self.max_side = max_side
        self.sift = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02)
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)

    def compute(self, rgb) -> dict:
        gray = self.cv2.cvtColor(np.asarray(rgb), self.cv2.COLOR_RGB2GRAY)
        gray = self.clahe.apply(gray)
        kp, desc = self.sift.detectAndCompute(gray, None)
        return {"pts": np.float32([k.pt for k in kp]) if kp else np.zeros((0, 2), np.float32), "desc": desc,
                "size": (gray.shape[1], gray.shape[0]), "brightness": float(np.asarray(rgb).mean())}

    def compare(self, a: dict, b: dict, cfg: dict) -> dict:
        cv2 = self.cv2
        out = {"matches": 0, "inliers": 0, "inlier_ratio": 0.0, "corner_shift": None,
               "brightness_delta": round(b["brightness"] - a["brightness"], 1)}
        if a["desc"] is None or b["desc"] is None or len(a["desc"]) < 8 or len(b["desc"]) < 8:
            out["result"] = "uncertain"
            out["reason"] = "insufficient_features"
            return out
        pairs = self.matcher.knnMatch(a["desc"], b["desc"], k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        out["matches"] = len(good)
        if len(good) < cfg["min_inliers"]:
            out["result"], out["reason"] = "uncertain", "too_few_matches"
            return out
        src = a["pts"][[m.queryIdx for m in good]]
        dst = b["pts"][[m.trainIdx for m in good]]
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0, maxIters=3000, confidence=0.995)
        if H is None:
            out["result"], out["reason"] = "uncertain", "homography_failed"
            return out
        inl = int(mask.sum())
        out["inliers"], out["inlier_ratio"] = inl, round(inl / len(good), 3)
        if inl < cfg["min_inliers"] or inl / len(good) < cfg["min_inlier_ratio"]:
            out["result"], out["reason"] = "uncertain", "weak_geometric_support"
            return out
        w, h = a["size"]
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        moved = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
        shift = float(np.mean(np.linalg.norm(moved - corners.reshape(-1, 2), axis=1)) / w)
        out["corner_shift"] = round(shift, 4)
        if shift <= cfg["same_view_max_corner_shift"]:
            out["result"], out["reason"] = "same", "aligned"
        elif shift >= cfg["move_min_corner_shift"]:
            out["result"], out["reason"] = "shifted", "corner_shift_above_threshold"
        else:
            out["result"], out["reason"] = "uncertain", "small_shift_between_thresholds"
        return out


def detect_candidates(ctx: RunContext, images: list[dict], reader: SourceReader | None = None,
                      light: dict[str, str] | None = None) -> dict:
    """``light``: optional {image_id: light class} from the thumbs stage; dark frames are then not compared
    at all (they cannot be matched) and are attached to the surrounding segment by time.

    ``coarse_stride`` > 1 compares only every n-th usable frame and then refines every proposed boundary
    on all frames in between (coarse-to-fine; same result for a camera that moves rarely, ~n x faster).
    """
    cfg = dict({"max_side": 960, "min_inliers": 40, "min_inlier_ratio": 0.5, "same_view_max_corner_shift": 0.012,
                "move_min_corner_shift": 0.03, "stride": 1, "confirm_frames": 2, "coarse_stride": 1},
               **ctx.site.get("view_detection", {}))
    reader = reader or SourceReader(ctx)
    feats = _Features(cfg["max_side"])
    all_frames = sorted([im for im in images if im.get("duplicate_of") is None and im["capture"]["time_local"]],
                        key=lambda im: im["capture"]["time_local"])
    by_id_all = {im["image_id"]: im for im in all_frames}
    light = light or {}
    usable = [im for im in all_frames if light.get(im["image_id"]) not in ("dark", "held")]
    stride = max(1, int(cfg["stride"])) * max(1, int(cfg.get("coarse_stride") or 1))
    frames = usable[::stride]
    if usable and frames and frames[-1] is not usable[-1]:
        frames.append(usable[-1])
    no_time = [im["image_id"] for im in images if not im["capture"]["time_local"]]
    stage = ctx.stage_dir("views")
    cache = ItemCache(stage / "frame_checks.jsonl")
    fp_cfg = content_hash(cfg)
    progress = Progress(ctx, "index-views", len(frames), "images")
    feature_cache: dict[str, dict] = {}

    def features(im):
        if im["image_id"] not in feature_cache:
            rgb, _ = reader.rgb(im, cfg["max_side"])
            feature_cache[im["image_id"]] = feats.compute(rgb)
            if len(feature_cache) > 6:  # bounded memory
                feature_cache.pop(next(iter(feature_cache)))
        return feature_cache[im["image_id"]]

    def compare(a, b):
        key = f"{a['image_id']}>{b['image_id']}"
        fp = content_hash([fp_cfg, a["source_photo"]["sha256"], b["source_photo"]["sha256"]])
        hit = cache.get(key, fp)
        if hit is None:
            hit = feats.compare(features(a), features(b), cfg)
            cache.put(key, fp, hit)
        return hit

    segments, checks = [], []
    if not frames:
        progress.finish()
        return {"segments": [], "frames": 0, "no_time_images": no_time}
    # Sequential comparison: each frame is compared with the last frame that matched with
    # confidence ("prev_ok"), so gradual construction progress does not accumulate against a
    # fixed anchor. A move needs `confirm_frames` consecutive frames that are shifted against
    # prev_ok but consistent with each other.
    seg = {"anchor": frames[0], "members": [frames[0]], "uncertain": [], "boundary": None}
    prev_ok = frames[0]
    pending: list[tuple[dict, dict]] = []      # consecutive 'shifted' frames
    unsure: list[tuple[dict, dict]] = []       # consecutive non-dark 'uncertain' frames
    passengers: list[tuple[dict, dict]] = []   # dark frames seen while a run is open
    progress.tick("saved")

    def start_segment(run, kind):
        nonlocal seg, prev_ok, passengers
        segments.append(seg)
        first, ev = run[0]
        members = sorted([p[0] for p in run + passengers], key=lambda f: f["capture"]["time_local"])
        seg = {"anchor": first, "members": members, "uncertain": [{"image_id": p[0]["image_id"], **p[1]} for p in passengers],
               "boundary": {"type": kind, "after_image_id": segments[-1]["members"][-1]["image_id"],
                            "first_image_id": first["image_id"], "evidence": ev}}
        if kind == "appearance_change_or_move_unresolved":
            seg["uncertain"].extend({"image_id": p[0]["image_id"], **p[1]} for p in run)
        prev_ok = run[-1][0]
        passengers = []

    def flush(run, note):
        nonlocal passengers
        seg["uncertain"].extend({"image_id": p[0]["image_id"], **p[1], "note": note} for p in run)
        seg["uncertain"].extend({"image_id": p[0]["image_id"], **p[1]} for p in passengers)
        seg["members"].extend(sorted([p[0] for p in run + passengers], key=lambda f: f["capture"]["time_local"]))
        passengers = []

    try:
        for im in frames[1:]:
            try:
                # dark frames were already removed using the thumbnail brightness (light classes); only
                # without thumbnails do we need to decode the photo here just to measure brightness
                bright = 255.0 if light.get(im["image_id"]) else features(im)["brightness"]
                if bright < cfg.get("min_brightness", 35):
                    result = {"result": "uncertain", "reason": "low_light_frame_not_compared", "brightness": round(bright, 1)}
                else:
                    result = compare(prev_ok, im)
            except SourceIntegrityError as error:
                result = {"result": "uncertain", "reason": error.code}
            checks.append({"image_id": im["image_id"], "compared_with": prev_ok["image_id"], **result})
            dark = result.get("reason") == "low_light_frame_not_compared"
            if dark and (pending or unsure):
                passengers.append((im, result))
                progress.tick("saved")
                continue
            if result["result"] == "shifted":
                if unsure:
                    flush(unsure, "unresolved_appearance_change"); unsure = []
                consistent = not pending or compare(pending[-1][0], im)["result"] == "same"
                pending = (pending + [(im, result)]) if consistent else [(im, result)]
                if len(pending) >= cfg["confirm_frames"]:
                    start_segment(pending, "camera_shift")
                    pending = []
            elif result["result"] == "uncertain" and not dark:
                if pending:
                    flush(pending, "unconfirmed_shift"); pending = []
                # Frames that cannot be matched to prev_ok but match each other: either a camera move
                # or a large appearance change (lighting / construction progress). Proposed as an
                # unresolved boundary for the reviewer; never classified as a move here.
                consistent = not unsure or compare(unsure[-1][0], im)["result"] == "same"
                unsure = (unsure + [(im, result)]) if consistent else [(im, result)]
                if len(unsure) >= cfg["confirm_frames"]:
                    start_segment(unsure, "appearance_change_or_move_unresolved")
                    unsure = []
            else:
                for run, note in ((pending, "unconfirmed_shift"), (unsure, "unresolved_appearance_change")):
                    if run:
                        flush(run, note)
                pending, unsure = [], []
                seg["members"].append(im)
                if result["result"] == "uncertain":
                    seg["uncertain"].append({"image_id": im["image_id"], **result})
                else:
                    prev_ok = im
            progress.tick("saved")
        for run, note in ((pending, "unconfirmed_trailing_shift"), (unsure, "unresolved_trailing_change")):
            if run:
                flush(run, note)
        segments.append(seg)
    finally:
        cache.close()
    refinements = []
    if stride > 1 and len(segments) > 1:
        refinements = _refine_boundaries(segments, usable, compare)
    # Unresolved boundaries (frames stopped matching, but no measurable shift) are usually big appearance
    # changes (fit-out, lighting). Re-check them with the two segments' references and relaxed matching:
    # if the references align, it is the same framing and the segments are merged.
    merges = []
    relaxed = dict(cfg, min_inliers=int(cfg.get("merge_min_inliers", 25)),
                   min_inlier_ratio=float(cfg.get("merge_min_inlier_ratio", 0.25)))
    i = 0
    while i < len(segments) - 1:
        nxt = segments[i + 1]
        if (nxt["boundary"] or {}).get("type") != "appearance_change_or_move_unresolved":
            i += 1
            continue
        ra = by_id_all.get(_pick_reference(segments[i], light))
        rb = by_id_all.get(_pick_reference(nxt, light))
        try:
            res = feats.compare(features(ra), features(rb), relaxed) if ra and rb else {"result": "uncertain"}
        except SourceIntegrityError as error:
            res = {"result": "uncertain", "reason": error.code}
        if res.get("result") == "same":
            merges.append({"kept": segments[i]["anchor"]["image_id"], "merged_first": nxt["members"][0]["image_id"],
                           "evidence": res})
            segments[i]["members"] = sorted(segments[i]["members"] + nxt["members"], key=lambda f: f["capture"]["time_local"])
            segments[i]["uncertain"] += nxt["uncertain"]
            segments[i].setdefault("merged_boundaries", []).append(nxt["boundary"])
            del segments[i + 1]
        else:
            nxt["boundary"]["reference_check"] = res
            i += 1
    cache.close()          # refinement may have appended comparisons after the main loop
    cache.maybe_compact()
    # Every photo (incl. dark and not-sampled frames) belongs to the segment whose time range contains it;
    # ranges are made contiguous so no photo falls into a gap between two candidates.
    starts = [s["members"][0]["capture"]["time_local"] for s in segments]
    out = []
    for index, s in enumerate(segments, 1):
        lo = starts[index - 1]
        hi = starts[index] if index < len(segments) else None
        members = [f for f in all_frames if f["capture"]["time_local"] >= lo and (hi is None or f["capture"]["time_local"] < hi)]
        if index == 1:
            members = [f for f in all_frames if hi is None or f["capture"]["time_local"] < hi]
        out.append({"candidate_id": f"VC{index:02d}", "camera_id": s["anchor"]["camera_id"],
                    "start_time": members[0]["capture"]["time_local"] if members else lo,
                    "end_time": members[-1]["capture"]["time_local"] if members else lo,
                    "first_image_id": members[0]["image_id"] if members else s["members"][0]["image_id"],
                    "last_image_id": members[-1]["image_id"] if members else s["members"][-1]["image_id"],
                    "reference_image_id": _pick_reference(s, light), "sampled_images": len(s["members"]),
                    "photos": len(members), "days": len({f["capture"]["time_local"][:10] for f in members}),
                    "boundary": s["boundary"], "boundary_type": (s["boundary"] or {}).get("type"),
                    "uncertain_frames": s["uncertain"][:200], "uncertain_count": len(s["uncertain"]),
                    "status": "candidate_unconfirmed"})
    report = {"config": cfg, "frames_compared": len(frames), "frames_total": len(all_frames),
              "frames_dark_skipped": len(all_frames) - len(usable), "no_time_images": no_time,
              "segments": out, "result_counts": _count(checks), "boundary_refinement": refinements,
              "merged_appearance_boundaries": merges,
              "meaning": "Boundary candidates for human review. Uncertain frames are not moves."}
    atomic_write_json(stage / "view_candidates.json", report)
    try:
        report["contact_sheet"] = contact_sheet(ctx, frames, out, checks, reader, stage / "contact_sheet.jpg")
    except Exception as error:  # noqa: BLE001 - the sheet is a convenience
        report["contact_sheet_error"] = str(error)
    progress.finish()
    return report


def _pick_reference(seg: dict, light: dict) -> str:
    """Reference photo of a candidate: a well-lit, confidently matched member near the segment middle."""
    unsure = {u["image_id"] for u in seg["uncertain"]}
    good = [m for m in seg["members"] if m["image_id"] not in unsure and light.get(m["image_id"], "ok") == "ok"]
    if not good:
        return seg["anchor"]["image_id"]
    return good[len(good) // 2]["image_id"] if len(good) > 4 else good[0]["image_id"]


def _refine_boundaries(segments: list[dict], usable: list[dict], compare) -> list[dict]:
    """Move each coarse boundary to the exact photo: the frames between the last coarse frame of the earlier
    segment and the first coarse frame of the later one are compared with both."""
    out = []
    pos = {im["image_id"]: i for i, im in enumerate(usable)}
    for prev, seg in zip(segments, segments[1:]):
        a = [m for m in prev["members"] if m["image_id"] in pos]
        b = seg["members"][0]
        if not a or b["image_id"] not in pos:
            continue
        a_last = max(a, key=lambda m: pos[m["image_id"]])
        between = usable[pos[a_last["image_id"]] + 1:pos[b["image_id"]]]
        decided = []
        for f in between:
            ra, rb = compare(a_last, f)["result"], compare(b, f)["result"]
            decided.append((f, "A" if ra == "same" and rb != "same" else "B" if rb == "same" and ra != "same" else "?"))
        # boundary = after the last frame that clearly belongs to A, before the first frame that clearly belongs to B
        last_a = max([i for i, (_, d) in enumerate(decided) if d == "A"], default=-1)
        moved = [f for f, _ in decided[last_a + 1:]]
        for f in moved:
            if f not in seg["members"]:
                seg["members"].append(f)
        seg["members"].sort(key=lambda f: f["capture"]["time_local"])
        for f, _ in decided[:last_a + 1]:
            prev["members"].append(f)
        prev["members"].sort(key=lambda f: f["capture"]["time_local"])
        out.append({"boundary_after": (decided[last_a][0] if last_a >= 0 else a_last)["image_id"],
                    "first_of_next": seg["members"][0]["image_id"], "frames_checked": len(decided),
                    "undecided": sum(1 for _, d in decided if d == "?")})
    return out


def contact_sheet(ctx, frames, segments, checks, reader, path, thumb_w: int = 240, cols: int = 8, per_end: int = 4):
    """Thumbnail grid for boundary review: for every candidate its reference photo and the first/last
    compared frames (so each boundary is visible on one page even for 10k-photo sites)."""
    from PIL import Image, ImageDraw
    res = {c["image_id"]: c for c in checks}
    by_id = {f["image_id"]: f for f in frames}
    rows_spec = []
    for s in segments:
        lo, hi = s["start_time"], s["end_time"]
        seq = [f for f in frames if lo <= f["capture"]["time_local"] <= hi]
        pick = seq[:per_end] + [f for f in seq[-per_end:] if f not in seq[:per_end]]
        ref = by_id.get(s["reference_image_id"])
        rows_spec.append((s, ([ref] if ref is not None else []) + [f for f in pick if f is not ref]))
    if not rows_spec or not frames:
        return None
    th = round(thumb_w * frames[0]["size"][1] / frames[0]["size"][0])
    n_rows = sum((len(p) + cols - 1) // cols for _, p in rows_spec)
    sheet = Image.new("RGB", (cols * thumb_w, n_rows * (th + 28) + 22 * len(rows_spec)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    y = 0
    for s, pick in rows_spec:
        d.text((4, y + 4), f"{s['candidate_id']}  {s['start_time']} .. {s['end_time']}  photos={s.get('photos')}  "
                           f"boundary={s.get('boundary_type') or 'start'}", fill=(255, 255, 255))
        y += 22
        for k, im in enumerate(pick):
            if k and k % cols == 0:
                y += th + 28
            x = (k % cols) * thumb_w
            try:
                rgb, _ = reader.rgb(im, thumb_w * 2)
                sheet.paste(rgb.resize((thumb_w, th)), (x, y + 28))
            except Exception:  # noqa: BLE001
                pass
            c = res.get(im["image_id"], {})
            tag = "REF" if im["image_id"] == s["reference_image_id"] else c.get("result", "anchor")
            col = {"same": (150, 220, 150), "shifted": (255, 120, 120), "uncertain": (255, 210, 90),
                   "REF": (120, 200, 255)}.get(tag, (180, 180, 255))
            d.text((x + 3, y + 2), im["capture"]["time_local"][5:16], fill=(255, 255, 255))
            d.text((x + 3, y + 14), f"{tag} {c.get('corner_shift', '') or ''}", fill=col)
        y += th + 28
    sheet.save(path, quality=85)
    return str(path)


def _count(checks):
    out = {}
    for c in checks:
        out[c["result"]] = out.get(c["result"], 0) + 1
    return out


def views_from_candidates(store, candidates: dict, site: dict, author: str = "pipeline") -> list[dict]:
    """Create *draft* views from candidates if the store has no views yet."""
    if store.latest("view"):
        return []
    created = []
    for index, seg in enumerate(candidates["segments"], 1):
        view_id = f"V{index:02d}"
        payload = {"view_id": view_id, "site_id": site["site_id"], "camera_id": seg["camera_id"],
                   "start_time": seg["start_time"], "end_time": seg["end_time"],
                   "reference_image_id": seg["reference_image_id"], "image_ids_extra": [],
                   "classification_source": "auto_candidate", "candidate_id": seg["candidate_id"],
                   "notes": "Draft from automatic boundary candidates; boundaries/reference must be confirmed."}
        r = store.put("view", view_id, payload, source="auto_candidate", author=author)
        created.append({"view_id": view_id, **r})
    return created


def views_from_config(store, site: dict, author: str = "config") -> list[dict]:
    created = []
    for view in site.get("views", []):
        if store.get("view", view["view_id"]) is None:
            payload = dict(view, site_id=site["site_id"], classification_source="config")
            payload.setdefault("camera_id", site["camera_id"])
            payload.setdefault("image_ids_extra", [])
            created.append({"view_id": view["view_id"], **store.put("view", view["view_id"], payload,
                                                                    source="config", author=author)})
    return created


# ------------------------------------------------------------ reconciliation
def _overlap(a0, a1, b0, b1) -> float:
    from datetime import datetime
    lo, hi = max(a0, b0), min(a1, b1)
    if lo > hi:
        return 0.0
    return (datetime.fromisoformat(hi) - datetime.fromisoformat(lo)).total_seconds() + 1.0


def reconcile_views(store, report: dict, site: dict, author: str = "auto") -> dict:
    """Bring the store's views in line with new boundary candidates WITHOUT touching human decisions.

    * candidate overlapping no view                      -> new draft view (auto_candidate)
    * matched view, not approved and not manual          -> updated in place (still a draft)
    * matched view that is approved or manually edited   -> left unchanged; a *proposal* is returned
      for the to-do list (the reviewer accepts it with one click = a human approval)
    The reference photo of a view that already has ROIs is never changed automatically.
    """
    views = {r["key"]: r for r in store.latest("view")}
    states = store.review_states("view")
    rois_by_view = {}
    for r in store.latest("roi"):
        rois_by_view.setdefault(r["key"].split("|")[1], []).append(r["key"])
    used, created, updated, proposals, unchanged = set(), [], [], [], []
    for seg in report.get("segments", []):
        best, best_ov = None, 0.0
        for vid, rec in views.items():
            p = rec["payload"]
            if vid in used or p.get("retired") or not p.get("start_time"):
                continue
            ov = _overlap(seg["start_time"], seg["end_time"], p["start_time"], p["end_time"])
            if ov > best_ov:
                best, best_ov = vid, ov
        if best is None:
            n = 1
            while f"V{n:02d}" in views:
                n += 1
            vid = f"V{n:02d}"
            payload = {"view_id": vid, "site_id": site["site_id"], "camera_id": seg["camera_id"],
                       "start_time": seg["start_time"], "end_time": seg["end_time"],
                       "reference_image_id": seg["reference_image_id"], "image_ids_extra": [],
                       "classification_source": "auto_candidate", "candidate_id": seg["candidate_id"],
                       "notes": "Draft from automatic boundary candidates; boundaries/reference must be confirmed."}
            store.put("view", vid, payload, source="auto_candidate", author=author)
            views[vid] = store.get("view", vid)
            used.add(vid)
            created.append(vid)
            continue
        used.add(best)
        rec = views[best]
        p = rec["payload"]
        want = {"start_time": seg["start_time"], "end_time": seg["end_time"]}
        if not rois_by_view.get(best):
            want["reference_image_id"] = seg["reference_image_id"]
        if all(p.get(k) == v for k, v in want.items()):
            unchanged.append(best)
            continue
        if states.get(best, {}).get("status") == "approved" or rec["source"] == "manual":
            proposals.append({"kind": "update_view", "view_id": best, "current": {k: p.get(k) for k in want}, "proposed": want,
                              "candidate_id": seg["candidate_id"], "photos": seg.get("photos"),
                              "boundary_type": seg.get("boundary_type"),
                              "reason": "view is approved/manual; automatic boundaries differ"})
            continue
        store.put("view", best, dict(p, **want, candidate_id=seg["candidate_id"]), source="auto_candidate", author=author)
        updated.append(best)
    leftover = [v for v in views if v not in used and not views[v]["payload"].get("retired")]
    retired = []
    for vid in leftover:
        rec = views[vid]
        if states.get(vid, {}).get("status") != "approved" and rec["source"] != "manual" and not rois_by_view.get(vid):
            store.put("view", vid, dict(rec["payload"], retired=True,
                                        notes="retired automatically: no boundary candidate supports this view any more"),
                      source="auto_candidate", author=author)
            retired.append(vid)
        else:
            proposals.append({"kind": "retire_view", "view_id": vid, "current": {k: rec["payload"].get(k) for k in
                              ("start_time", "end_time", "reference_image_id")}, "proposed": {"retired": True},
                              "reason": "no boundary candidate matches this view (it may have been a lighting change)"})
    return {"created": created, "updated": updated, "unchanged": unchanged, "retired": retired,
            "proposals": proposals}
