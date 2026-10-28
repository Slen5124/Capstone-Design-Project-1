"""Every photo vs the reference photo of its view: find photos whose camera position differs from the one the ROI
was drawn on (stage `align` of `vp auto`), and the fixes the review UI offers for them.

Why: view boundaries are detected on a subset of frames, so short camera moves (a bump, the start of a re-mount, a
lens covered for an hour) end up inside a view. Their ROI then lies on the wrong pixels.

Per photo (resumable cache `stages/view_align/align.jsonl`, key = photo | view | view version | reference):
  pass 1  SIFT + RANSAC homography against the view reference
  pass 2  photos that are 'uncertain' (day vs night reference) or 'shifted' are re-checked against already aligned
          photos of the same view with similar brightness / nearby time
  shift = mean displacement of the 4 image corners / image width  (same measure as `drift`)
Segments = runs of shifted photos (uncertain / dark photos inside a run do not break it). Each gets a suggestion:
  trim    a few photos at the start / end of the view (camera being moved)  -> shorten the view
  split   a longer run at the start / end / middle                           -> new view, ROI mapped onto it (draft)
  reref   most of the view disagrees with its reference photo                -> new reference, ROI mapped (draft)
Nothing is changed here; the review UI applies a fix only when a person presses its button, and mapped ROIs are
always drafts that a person approves.
"""
from __future__ import annotations

import json
import time as _time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .index_views import assign_view, excluded_views, store_views
from .runctx import RunContext, atomic_write_json

DEFAULTS = {"max_side": 960, "same_below": 0.005, "shifted_above": 0.015, "min_inliers": 25, "dark_below": 35,
            "min_segment": 3, "trim_max_photos": 8, "reference_outlier_share": 0.7, "peers": 5,
            "status": "unvalidated defaults (LV 2026-10-21: 9 real moves found, lighting-only differences cleared by pass 2)"}
DISPLAY_SIDE = 1600


def config(ctx) -> dict:
    return dict(DEFAULTS, **(ctx.site.get("view_align") or {}))


class Matcher:
    """Grey, CLAHE, SIFT on photos at `max_side`; reads the review display cache when present, otherwise the original
    photo (and writes the cache, so this stage also pre-decrypts photos for the review UI)."""

    def __init__(self, ctx, cfg, reader=None):
        import cv2
        self.cv2, self.ctx, self.cfg = cv2, ctx, cfg
        self.sift = cv2.SIFT_create(3000)
        self.clahe = cv2.createCLAHE(2.0, (8, 8))
        self.bf = cv2.BFMatcher()
        self.reader = reader
        self.cache_dir = Path(ctx.run_dir) / "cache" / "display"
        self._f = {}

    def gray(self, im) -> np.ndarray | None:
        cv2 = self.cv2
        p = self.cache_dir / f"{im['image_id']}_{DISPLAY_SIDE}.jpg"
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) if p.exists() else None
        if g is None:
            if self.reader is None:
                from .source_adapter import SourceReader
                self.reader = SourceReader(self.ctx)
            import io
            rgb, _ = self.reader.rgb(im, DISPLAY_SIDE)
            buf = io.BytesIO()
            rgb.save(buf, "JPEG", quality=88)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_bytes(buf.getvalue())
            tmp.replace(p)
            g = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
        w = self.cfg["max_side"]
        return cv2.resize(g, (w, round(g.shape[0] * w / g.shape[1])), interpolation=cv2.INTER_AREA)

    def feats(self, im):
        iid = im["image_id"]
        if iid not in self._f:
            if len(self._f) > 400:
                self._f.clear()
            g = self.gray(im)
            h, w = g.shape
            mask = np.full_like(g, 255)
            mask[int(h * .86):, int(w * .78):] = 0                # camera vendor logo (static, matches itself)
            k, d = self.sift.detectAndCompute(self.clahe.apply(g), mask)
            self._f[iid] = (np.float32([p.pt for p in k]) if k else np.zeros((0, 2), np.float32), d, (w, h), float(g.mean()))
        return self._f[iid]

    def homography(self, a, b):
        """(H a->b at max_side scale or None, inliers, shift)."""
        cv2 = self.cv2
        pa, da, (w, h), _ = self.feats(a)
        pb, db, _, _ = self.feats(b)
        if da is None or db is None or len(da) < 8 or len(db) < 8:
            return None, 0, None
        ms = [x[0] for x in self.bf.knnMatch(da, db, k=2) if len(x) == 2 and x[0].distance < .72 * x[1].distance]
        if len(ms) < 12:
            return None, len(ms), None
        H, inl = cv2.findHomography(pa[[m.queryIdx for m in ms]], pb[[m.trainIdx for m in ms]], cv2.RANSAC, 3.0)
        n = int(inl.sum()) if inl is not None else 0
        if H is None or n < self.cfg["min_inliers"]:
            return None, n, None
        c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        return H, n, float(np.linalg.norm(cv2.perspectiveTransform(c, H) - c, axis=2).mean() / w)

    def classify(self, s) -> str:
        return "same" if s < self.cfg["same_below"] else "minor" if s < self.cfg["shifted_above"] else "shifted"

    def full_res(self, H, size):
        """H at max_side scale -> H in original pixel coordinates."""
        s = self.cfg["max_side"] / float(size[0])
        S = np.diag([s, s, 1.0])
        return np.linalg.inv(S) @ H @ S


def _ts(t: str) -> float:
    return _time.mktime(_time.strptime(t, "%Y-%m-%dT%H:%M:%S"))


def check_alignment(ctx: RunContext, images: list[dict], light: dict | None = None, reader=None, quiet: bool = False) -> dict:
    cfg = config(ctx)
    store = ctx.store()
    views = store_views(store)
    vrec = {v["view_id"]: v for v in views}
    excl = excluded_views(store)
    by_id = {im["image_id"]: im for im in images}
    out_dir = ctx.stage_dir("view_align")
    cache_path = out_dir / "align.jsonl"
    cache = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                cache[r["_key"]] = r
    m = Matcher(ctx, cfg, reader)
    rows_by_view = defaultdict(list)
    todo = []
    for im in images:
        if im["status"] != "ok" or im.get("duplicate_of") or not (im.get("capture") or {}).get("time_local"):
            continue
        v = assign_view(im, views)["view_id"]
        if not v or v in excl:
            continue
        ref = vrec[v].get("reference_image_id")
        key = f"{im['image_id']}|{v}|v{vrec[v]['_version']}|{ref}"
        if key in cache:
            rows_by_view[v].append(cache[key])
        else:
            todo.append((key, v, im))
    t0 = _time.time()
    with open(cache_path, "a", encoding="utf-8") as fh:
        for n, (key, v, im) in enumerate(todo):
            ref = by_id.get(vrec[v].get("reference_image_id"))
            row = {"_key": key, "image_id": im["image_id"], "time": im["capture"]["time_local"], "view_id": v,
                   "view_version": vrec[v]["_version"]}
            try:
                bright = m.feats(im)[3]
                row["mean"] = round(bright, 1)
                if (light or {}).get(im["image_id"]) == "dark" or bright < cfg["dark_below"]:
                    row["status"] = "dark"
                elif ref is None:
                    row["status"] = "no_reference"
                elif ref["image_id"] == im["image_id"]:
                    row.update(status="same", shift=0.0, reference=True)
                else:
                    H, ninl, s = m.homography(ref, im)
                    row.update(inliers=ninl, status="uncertain" if s is None else m.classify(s))
                    if s is not None:
                        row["shift"] = round(s, 4)
            except Exception as error:  # noqa: BLE001 - one unreadable photo must not stop the stage
                row.update(status="error", error=f"{type(error).__name__}: {error}"[:200])
            fh.write(json.dumps(row) + "\n")
            rows_by_view[v].append(row)
            if not quiet and n and n % 500 == 0:
                print(f"[align] {n}/{len(todo)} photos ({_time.time() - t0:.0f}s)", flush=True)
    # pass 2: re-check doubtful photos against aligned photos of the same view (similar brightness / nearby time)
    p2_path = out_dir / "align_peer.json"
    p2 = json.loads(p2_path.read_text(encoding="utf-8")) if p2_path.exists() else {}
    for v, rows in rows_by_view.items():
        good = [r for r in rows if r.get("status") == "same"]
        for r in rows:
            if r.get("status") not in ("uncertain", "shifted") or r["_key"] in p2 or not good:
                continue
            peers = sorted(good, key=lambda p: abs(p.get("mean", 0) - r.get("mean", 0)))[:3]
            peers += sorted(good, key=lambda p: abs(_ts(p["time"]) - _ts(r["time"])))[:2]
            best = None
            for p in {p["image_id"]: p for p in peers}.values():
                if p["image_id"] not in by_id:
                    continue
                H, ninl, s = m.homography(by_id[p["image_id"]], by_id[r["image_id"]])
                if s is not None and (best is None or ninl > best[1]):
                    best = (s, ninl, p["image_id"])
            p2[r["_key"]] = ({"status2": m.classify(best[0]), "peer": best[2], "peer_shift": round(best[0], 4),
                              "peer_inliers": best[1]} if best else {"status2": "uncertain"})
            if len(p2) % 50 == 0:
                atomic_write_json(p2_path, p2)                  # resumable
    atomic_write_json(p2_path, p2)
    final = {}
    for v, rows in rows_by_view.items():
        final[v] = sorted([dict(r, **p2.get(r["_key"], {})) for r in rows], key=lambda r: r["time"])
    segments = find_segments(final, cfg)
    counts = {v: _count(rs) for v, rs in final.items()}
    report = {"config": cfg, "segments": segments, "views": counts,
              "meaning": "camera position of every photo vs its view reference; segments = runs of photos whose ROI is "
                         "on the wrong pixels. Fixes are applied only by a person in the review UI."}
    atomic_write_json(out_dir / "align_report.json", report)
    return report


def status(r) -> str:
    return r.get("status2") or r.get("status")


def _count(rows):
    c = defaultdict(int)
    for r in rows:
        c[status(r)] += 1
    return dict(c)


def find_segments(final: dict, cfg: dict) -> list[dict]:
    segs = []
    for v, rows in final.items():
        meas = [r for r in rows if status(r) in ("same", "minor", "shifted")]
        if not meas:
            continue
        shifted_all = [r for r in meas if status(r) == "shifted"]
        if len(shifted_all) >= cfg["min_segment"] and len(shifted_all) / len(meas) >= cfg["reference_outlier_share"]:
            sh = sorted(r.get("peer_shift", r.get("shift")) or 0 for r in shifted_all)
            segs.append(_seg(v, rows, shifted_all, rows[0], rows[-1], "reference_outlier", "reref", sh))
            continue
        run = []
        for r in rows + [{"status": "_end"}]:
            st = status(r)
            if st == "shifted":
                run.append(r)
            elif st in ("uncertain", "dark", "error") and run:
                run.append(r)
            else:
                shifted = [x for x in run if status(x) == "shifted"]
                while run and status(run[-1]) != "shifted":
                    run.pop()                                   # trailing doubtful photos belong to nobody
                if run:
                    first_meas, last_meas = meas[0], meas[-1]
                    at_head = run[0]["time"] <= first_meas["time"]
                    at_tail = run[-1]["time"] >= last_meas["time"]
                    if len(shifted) >= cfg["min_segment"] or ((at_head or at_tail) and len(shifted) >= 2):
                        kind = "head" if at_head else "tail" if at_tail else "middle"
                        if at_head:                             # include doubtful photos before the first measurable one
                            run = [x for x in rows if x["time"] <= run[-1]["time"]]
                        if at_tail:
                            run = [x for x in rows if x["time"] >= run[0]["time"]]
                        suggest = "trim" if kind != "middle" and len(run) <= cfg["trim_max_photos"] else "split"
                        sh = sorted(x.get("peer_shift", x.get("shift")) or 0 for x in shifted)
                        segs.append(_seg(v, rows, shifted, run[0], run[-1], kind, suggest, sh))
                run = []
    return sorted(_merge(segs, final, cfg), key=lambda s: (s["from"], s["view_id"]))


def _merge(segs, final, cfg, max_gap: int = 2):
    """Two runs of the same view separated by at most `max_gap` aligned photos are one camera position (a single
    photo that happens to match must not cut a long move in two)."""
    out = []
    for v in sorted({s["view_id"] for s in segs}):
        rows = final[v]
        vs = sorted([s for s in segs if s["view_id"] == v], key=lambda s: s["from"])
        cur = vs[0]
        for nxt in vs[1:]:
            gap = [r for r in rows if cur["to"] < r["time"] < nxt["from"] and status(r) in ("same", "minor")]
            if cur["kind"] != "reference_outlier" and nxt["kind"] != "reference_outlier" and len(gap) <= max_gap:
                ids = cur["shifted_ids"] + nxt["shifted_ids"]
                shifted = [r for r in rows if r["image_id"] in set(ids)]
                kind = "head" if cur["kind"] == "head" else "tail" if nxt["kind"] == "tail" else "middle"
                if kind == "head" and nxt["kind"] == "tail":
                    kind = "reference_outlier"
                inside = [r for r in rows if cur["from"] <= r["time"] <= nxt["to"]]
                sh = sorted(r.get("peer_shift", r.get("shift")) or 0 for r in shifted)
                suggest = ("reref" if kind == "reference_outlier" else
                           "trim" if kind != "middle" and len(inside) <= cfg["trim_max_photos"] else "split")
                cur = _seg(v, rows, shifted, inside[0], inside[-1], kind, suggest, sh)
            else:
                out.append(cur)
                cur = nxt
        out.append(cur)
    return out


def _seg(v, rows, shifted, first, last, kind, suggest, sh):
    inside = [r for r in rows if first["time"] <= r["time"] <= last["time"]]
    ex = shifted[len(shifted) // 2]
    return {"view_id": v, "view_version": rows[0].get("view_version"), "kind": kind, "suggest": suggest,
            "from": first["time"], "to": last["time"], "photos": len(inside), "shifted": len(shifted),
            "median_shift": round(float(sh[len(sh) // 2]), 4) if sh else None,
            "example_image_id": ex["image_id"], "shifted_ids": [r["image_id"] for r in shifted][:400],
            "view_photos": len(rows)}


# ------------------------------------------------------------------ fixes (called by the review UI)
def map_rois(m: Matcher, src_ims: list[dict], dst_ims: list[dict], size) -> tuple[np.ndarray, int]:
    """Best homography (full-resolution coordinates) from the old camera position (src photos) to the new one (dst)."""
    best = None
    for a in src_ims:
        for b in dst_ims:
            H, n, _ = m.homography(a, b)
            if H is not None and (best is None or n > best[1]):
                best = (H, n, b)
    if best is None:
        raise ValueError("두 카메라 위치 사이에서 맞는 점을 찾지 못했습니다 (ROI를 새 위치에 직접 그려 주세요)")
    H, n, b = best
    target = dst_ims[0]
    if b["image_id"] != target["image_id"]:                      # chain to the new reference photo
        H2, n2, _ = m.homography(b, target)
        if H2 is not None:
            H = H2 @ H
            n = min(n, n2)
    return m.full_res(H, size), n


def warp_payload(H: np.ndarray, payload: dict) -> tuple[list, list]:
    import cv2
    W, Hh = payload["image_size"]

    def tr(pts):
        q = cv2.perspectiveTransform(np.float32(pts).reshape(-1, 1, 2), H).reshape(-1, 2)
        q[:, 0] = q[:, 0].clip(0, W)
        q[:, 1] = q[:, 1].clip(0, Hh)
        return [[round(float(x), 1), round(float(y), 1)] for x, y in q]

    polys = [dict(p, exterior=tr(p["exterior"]), holes=[tr(h) for h in (p.get("holes") or [])]) for p in payload["polygons"]]
    excl = [dict(e, points=tr(e["points"])) for e in payload.get("exclusions") or []]
    return polys, excl
