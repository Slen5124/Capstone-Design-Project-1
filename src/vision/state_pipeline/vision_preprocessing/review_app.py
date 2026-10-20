"""R07: local review UI (stdlib HTTP server bound to 127.0.0.1; no external uploads).

Run:  python -m vision_preprocessing.cli review --run <run_dir> [--port 8765]
Open: http://127.0.0.1:8765/

Draft saves and approvals are separate actions. Approvals require a reviewer
name and are bound to (record version, dependency versions); editing a ROI or
label afterwards invalidates the affected approvals (store.py). The server
never approves anything by itself.
"""
from __future__ import annotations

import io
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from .assign_surfaces import build_observations, load_observations, load_shapes, open_observation_cache
from .index_views import assign_view, store_views
from .runctx import RunContext, RunLock, read_jsonl
from .schemas import applicability_key, label_key, obs_key, roi_key
from .source_adapter import SourceIntegrityError, SourceReader, load_images
from .state_annotations import (LabelError, eligibility, propagate_label_draft, save_label,
                                save_manual_observation)
from .store import StoreError
from .target_registry import (RegistryError, compatible_activities, roi_conflicts, roi_hints, save_applicability,
                              save_roi, save_target)

STATIC = Path(__file__).resolve().parent / "review_static"


class ReviewService:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.store = ctx.store()
        self.tax = ctx.taxonomy
        self.images = load_images(ctx, include_held=True)
        self.by_id = {im["image_id"]: im for im in self.images}
        self.reader = SourceReader(ctx)
        self.lock = threading.RLock()
        self.cache_dir = ctx.run_dir / "cache" / "display"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._obs = None
        self.obs_cache = open_observation_cache(ctx)  # one in-memory cache for the server's lifetime
        self.hints = roi_hints(ctx, {im["source_photo"]["entry"]: im for im in self.images})
        self.bg = {"running": False}
        self._bg_queue: list[str] = []          # image ids waiting for a background observation refresh
        self._bg_cv = threading.Condition()
        self._bg_thread = None
        self._obs_gen = 0                       # bumps whenever observations change (list cache key)
        self._list_cache = (None, None)
        self.samples = {s["image_id"]: s for s in read_jsonl(ctx.run_dir / "stages" / "samples" / "samples.jsonl")}

    # ---------------------------------------------------------- helpers
    def obs(self):
        if self._obs is None:
            self._obs = load_observations(self.ctx, self.obs_cache)
        return self._obs

    def focus_ids(self) -> set:
        """Photos that matter for review: samples, labelled photos, view/ROI references.
        Only these are refreshed in the background (10k+ photos would take hours)."""
        ids = set(self.samples)
        ids |= {r["key"].split("|")[0] for r in self.store.latest("state_label")}
        ids |= {r["payload"].get("reference_image_id") for r in self.store.latest("view")}
        ids |= {r["payload"].get("reference_image_id") for r in self.store.latest("roi")}
        return ids

    def thumb(self, image_id) -> bytes:
        from .thumbs import thumb_path
        path = thumb_path(self.ctx, image_id)
        if path.exists():
            return path.read_bytes()
        return self.jpeg(image_id, 480)

    def image(self, image_id) -> dict:
        im = self.by_id.get(image_id)
        if im is None:
            raise KeyError(f"unknown image {image_id}")
        return im

    def jpeg(self, image_id, max_side: int) -> bytes:
        im = self.image(image_id)
        path = self.cache_dir / f"{image_id}_{max_side}.jpg"
        if path.exists():
            return path.read_bytes()
        with self.lock:
            rgb, _ = self.reader.rgb(im, max_side)
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=88)
        path.write_bytes(buf.getvalue())
        return buf.getvalue()

    def shapes(self, image_id) -> dict:
        im = self.image(image_id)
        path = self.cache_dir / f"{image_id}_shapes.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        with self.lock:
            doc = self.reader.annotation(im)
        out = {}
        for s in load_shapes(doc, im["size"]):
            if s["area"] < 30:
                continue
            g = s["geometry"].simplify(1.5)
            for part in getattr(g, "geoms", [g]):
                if part.geom_type == "Polygon" and part.area >= 30:
                    out.setdefault(s["raw_label"], []).append(
                        {"i": s["index"], "p": [[round(x, 1), round(y, 1)] for x, y in list(part.exterior.coords)[:-1]]})
        data = {"size": im["size"], "labels": out,
                "origin": "existing JSON polygons (qa_v3 output, pseudo-label)",
                "annotation": im["annotation"]}
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _rs(self, kind, key):
        return self.store.review_state(kind, key)

    def _rec(self, kind, key):
        r = self.store.get(kind, key)
        if r is None:
            return None
        rs = self._rs(kind, key)
        return {"key": key, "version": r["version"], "payload": r["payload"], "source": r["source"],
                "author": r["author"], "created_at": r["created_at"], "review": rs["status"],
                "review_reason": rs.get("reason"), "approval": rs.get("approval")}

    def meta(self) -> dict:
        targets = []
        for r in self.store.latest("target"):
            t = self._rec("target", r["key"])
            t["activities"] = {a: self._rec("applicability", applicability_key(r["key"], a))
                               for a in compatible_activities(r["payload"]["surface_type"], self.tax)}
            targets.append(t)
        views = [self._rec("view", r["key"]) for r in self.store.latest("view")]
        return {"site_id": self.ctx.site["site_id"], "camera_id": self.ctx.site["camera_id"],
                "run_id": self.ctx.info["run_id"], "store": self.ctx.info["resolved"]["store_path"],
                "activities": self.tax["activities"], "surface_types": list(self.tax["surface_types"]),
                "undeterminable_reasons": self.tax["undeterminable_reasons"],
                "object_classes": self.tax["object_classes"]["classes"], "targets": targets, "views": views,
                "roi_definitions": self.tax.get("roi_definitions"), "ratio_semantics": self.tax.get("ratio_semantics"),
                "timezone_confirmed": bool(self.ctx.site["capture_time"].get("timezone_confirmed")),
                "store_counts": self.store.counts(), "background_refresh": self.bg}

    def image_list(self) -> list[dict]:
        key = (self.store.change_token(), self._obs_gen)
        if self._list_cache[0] == key:
            return self._list_cache[1]
        out = self._image_list()
        self._list_cache = (key, out)
        return out

    def _image_list(self) -> list[dict]:
        views = store_views(self.store)
        labels, label_status = {}, {}
        states = self.store.review_states("state_label")
        for r in self.store.latest("state_label"):
            iid, tid, act = r["key"].split("|")
            st = states.get(r["key"], {}).get("status", "unreviewed")
            labels.setdefault(iid, []).append(st)
            label_status.setdefault(iid, {})[f"{tid}|{act}"] = {"status": st, "source": r["source"],
                                                               "basis": r["payload"].get("draft_basis")}
        rois = {r["key"]: r["version"] for r in self.store.latest("roi")}
        roi_refs = {r["payload"]["reference_image_id"] for r in self.store.latest("roi")}
        obs_all = self.obs()
        targets = [r["key"] for r in self.store.latest("target")]
        out = []
        for im in self.images:
            v = assign_view(im, views)
            obs = {}
            skip = im["status"] != "ok" or im.get("duplicate_of")
            for tid in targets:
                if skip:
                    obs[tid] = {"status": "held" if im["status"] != "ok" else "duplicate", "stale": False}
                    continue
                o = obs_all.get(obs_key(im["image_id"], tid))
                cur = rois.get(roi_key(tid, v["view_id"])) if v["view_id"] else None
                if o is None:
                    obs[tid] = {"status": "missing", "stale": cur is not None}
                    continue
                ref = o.get("roi_ref") or {}
                stale = cur is not None and (ref.get("kind") != "roi" or ref.get("version") != cur) and o.get("roi_source") != "manual"
                obs[tid] = {"status": o.get("status"), "roi_source": o.get("roi_source"), "stale": stale,
                            "occl": (o.get("coverage") or {}).get("occluder_labels"),
                            "flags": (o.get("quality") or {}).get("flags", [])}
            out.append({"image_id": im["image_id"], "time": im["capture"]["time_local"], "status": im["status"],
                        "capture_status": im["capture"]["status"], "view_id": v["view_id"], "view_status": v["status"],
                        "duplicate_of": im.get("duplicate_of"), "labels": labels.get(im["image_id"], []),
                        "label_status": label_status.get(im["image_id"], {}), "obs": obs,
                        "is_view_reference": any(vv.get("reference_image_id") == im["image_id"] for vv in views),
                        "is_roi_reference": im["image_id"] in roi_refs,
                        "checks": [c["code"] for c in im["checks"]],
                        "sample": ({k: self.samples[im["image_id"]].get(k) for k in ("kind", "phase", "light")}
                                   if im["image_id"] in self.samples else None),
                        "qa_v3": (im["annotation"] or {}).get("qa_v3_classification")})
        return out

    def same_as_previous(self, body: dict, author: str) -> dict:
        """For photos the reviewer has looked at in the grid and judged unchanged: copy the most recent
        earlier APPROVED label of the same target/activity in the same view and approve it.

        Only state regions are copied (occlusion of the source photo is not); the current photo's
        object-polygon occluders are applied at export. Photos that already have a label are left
        alone. Every approval is recorded with the reviewer name and the source photo.
        """
        views = store_views(self.store)
        ims = sorted([self.image(i) for i in body.get("image_ids", [])],
                     key=lambda im: im["capture"]["time_local"] or "")
        ims = [im for im in ims if im["status"] == "ok"]
        if ims:
            self._reassign(ims)
        targets = body.get("targets") or []
        approved_by_key = {}
        for rec in self.store.latest("state_label"):
            iid, tid, act = rec["key"].split("|")
            if self._rs("state_label", rec["key"])["status"] == "approved" and iid in self.by_id:
                approved_by_key.setdefault((tid, act), []).append(self.by_id[iid])
        done, skipped = [], []
        for im in ims:
            v = assign_view(im, views)
            for tid in targets:
                trec = self.store.get("target", tid)
                acts = compatible_activities(trec["payload"]["surface_type"], self.tax) if trec else []
                for act in acts:
                    key = label_key(im["image_id"], tid, act)
                    existing = self.store.get("state_label", key)
                    if existing is not None:
                        status = self._rs("state_label", key)["status"]
                        if status == "unreviewed":  # e.g. a prefill draft shown in the grid: approve it as it is
                            try:
                                self.store.decide("state_label", key, existing["version"], author, "approved",
                                                  note="approved existing draft (bulk from grid review)")
                                done.append({"image_id": im["image_id"], "target": tid, "from": "existing_draft"})
                                approved_by_key.setdefault((tid, act), []).append(im)
                            except StoreError as error:
                                skipped.append({"image_id": im["image_id"], "target": tid, "reason": str(error)[:120]})
                        else:
                            skipped.append({"image_id": im["image_id"], "target": tid, "reason": "already_labeled_" + status})
                        continue
                    t0 = im["capture"]["time_local"] or ""
                    cands = [s for s in approved_by_key.get((tid, act), [])
                             if (s["capture"]["time_local"] or "") < t0 and assign_view(s, views)["view_id"] == v["view_id"]]
                    if not cands:
                        skipped.append({"image_id": im["image_id"], "target": tid, "reason": "no_earlier_approved_label_in_view"})
                        continue
                    src = max(cands, key=lambda s: s["capture"]["time_local"])
                    o = self.obs().get(obs_key(im["image_id"], tid))
                    try:
                        r = propagate_label_draft(self.store, self.tax, src, im, o or {}, tid, act, author)
                        self.store.decide("state_label", key, r["version"], author, "approved",
                                          note=f"same_as_previous:{src['image_id']} (bulk from grid review)")
                    except (LabelError, StoreError) as error:
                        skipped.append({"image_id": im["image_id"], "target": tid, "reason": str(error)[:120]})
                        continue
                    approved_by_key.setdefault((tid, act), []).append(im)
                    done.append({"image_id": im["image_id"], "target": tid, "from": src["image_id"]})
        return {"approved": len(done), "skipped": skipped, "done": done}

    def prefill(self, body: dict, author: str) -> dict:
        """Create whole-ROI *draft* labels (fill_roi_state) for the listed images.

        Used when the reviewer has judged a set of photos to be uniform (e.g. "everything before
        02-17 is bare framing"). Nothing is approved here; every draft still needs a per-photo
        Approve. Existing labels are never overwritten. Object-polygon occluder candidates are
        excluded automatically at export, so they are not copied into the draft.
        """
        state, tid, act = body["state"], body["target_id"], body["activity_id"]
        if state not in ("installed", "not_installed"):
            raise LabelError("state must be installed or not_installed")
        ims = [self.image(i) for i in body.get("image_ids", [])]
        stale = [im for im in ims if im["status"] == "ok"]
        if stale:
            self._reassign(stale)  # make sure every observation uses the current ROI version
        created, skipped = [], []
        for im in ims:
            key = label_key(im["image_id"], tid, act)
            if self.store.get("state_label", key) is not None:
                skipped.append({"image_id": im["image_id"], "reason": "label_exists"})
                continue
            o = self.obs().get(obs_key(im["image_id"], tid))
            if not o or o.get("status") != "ok" or not o.get("roi_ref"):
                skipped.append({"image_id": im["image_id"], "reason": f"roi_{(o or {}).get('status', 'missing')}"})
                continue
            payload = {"target_id": tid, "activity_id": act, "regions": [], "fill_roi_state": state,
                       "undeterminable": [], "occluded": [], "whole_target_undeterminable": None,
                       "roi_ref": o["roi_ref"], "roi_source": o["roi_source"],
                       "note": f"bulk prefill draft ({state}) requested by {author}; verify this photo before approving"}
            try:
                save_label(self.store, self.tax, im, payload, author=author, source="bulk_prefill_draft",
                           view_id=o.get("view_id"))
                created.append(im["image_id"])
            except (LabelError, StoreError) as error:
                skipped.append({"image_id": im["image_id"], "reason": str(error)})
        return {"created": len(created), "skipped": skipped, "created_ids": created}

    def item(self, image_id) -> dict:
        im = self.image(image_id)
        views = store_views(self.store)
        v = assign_view(im, views)
        if im["status"] == "ok" and not im.get("duplicate_of"):
            rois = {r["key"]: r["version"] for r in self.store.latest("roi")}
            for r in self.store.latest("target"):
                o = self.obs().get(obs_key(image_id, r["key"]))
                cur = rois.get(roi_key(r["key"], v["view_id"])) if v["view_id"] else None
                ref = (o or {}).get("roi_ref") or {}
                if image_id in self._bg_queue:
                    break                                  # refreshed in the background shortly (shown as stale)
                if o is None or (cur is not None and o.get("roi_source") != "manual"
                                 and (ref.get("kind") != "roi" or ref.get("version") != cur)):
                    with self.lock:
                        self._reassign([im])  # opened photo: bring its observation up to date (about a second)
                    break
        targets = {}
        for r in self.store.latest("target"):
            tid = r["key"]
            auto = self.obs().get(obs_key(image_id, tid))
            roi = self._rec("roi", roi_key(tid, v["view_id"])) if v["view_id"] else None
            stale = bool(auto and roi and auto.get("roi_ref") and auto["roi_ref"]["kind"] == "roi"
                         and auto["roi_ref"]["version"] != roi["version"]) or bool(roi and not auto) or \
                bool(roi and auto and auto.get("roi_source") == "none")
            acts = {}
            for act in compatible_activities(r["payload"]["surface_type"], self.tax):
                lk = label_key(image_id, tid, act)
                e = eligibility(self.store, im, auto, tid, act)
                acts[act] = {"label": self._rec("state_label", lk),
                             "history": [{"version": h["version"], "source": h["source"], "author": h["author"],
                                          "created_at": h["created_at"]} for h in self.store.history("state_label", lk)],
                             "eligible": e["eligible"], "reasons": e["reasons"]}
            if auto:
                auto = dict(auto, review_items=(auto.get("review_items") or [])[:80],
                            links=[{"shape_index": l["shape_index"], "label": l.get("label")} for l in (auto.get("links") or [])])
            targets[tid] = {"auto": auto, "auto_stale": stale, "roi": roi,
                            "manual_obs": self._rec("observation", obs_key(image_id, tid)), "activities": acts}
        return {"image": im, "view": v, "view_record": self._rec("view", v["view_id"]) if v["view_id"] else None,
                "targets": targets, "hints": [h for h in self.hints if h.get("image_id") == image_id],
                "all_hints": [{k: h[k] for k in ("hint_id", "image_id", "meaning")} for h in self.hints]}

    # ----------------------------------------------------------- writes
    def post(self, path: str, body: dict) -> dict:
        author = (body.get("author") or "").strip()
        if not author:
            raise StoreError("enter your reviewer name first")
        # store-only actions: no need to wait for the photo lock (background refresh / ZIP reads)
        if path == "/api/decide":
            return self.store.decide(body["kind"], body["key"], int(body["version"]), author,
                                     body.get("decision", "approved"), body.get("note"))
        if path == "/api/target":
            return save_target(self.store, body["payload"], author)
        if path == "/api/applicability":
            return save_applicability(self.store, body["target_id"], body["activity_id"], body["applicability"],
                                      body.get("basis", ""), author, self.tax)
        if path == "/api/roi_copy":
            return self.roi_copy(body, author)       # store writes only; photos refresh in the background
        if path == "/api/roi":
            # saving the ROI is a single store write - answer at once; the reference photo is refreshed first in the
            # background, then every other photo of the view (no waiting for ZIP reads / registration here)
            view = self.store.get("view", body["view_id"])
            if view is None:
                raise RegistryError("unknown view")
            ref = self.image(view["payload"]["reference_image_id"])
            if body.get("image_id") != ref["image_id"]:
                raise RegistryError("ROI must be drawn on the view's reference image")
            if not body.get("allow_overlap"):
                bad = [c for c in roi_conflicts(self.store, body["view_id"], body["target_id"], body["polygons"],
                                                body.get("exclusions", []), min_overlap=0.5)]
                if bad:
                    raise RegistryError("; ".join(
                        (f"이 ROI의 폴리곤 하나가 {c['target_id']}의 ROI와 똑같습니다 (이전 벽 폴리곤이 초안에 남음) - 지우고 저장하세요"
                         if c["polygon_copied_from_it"] else
                         f"{c['target_id']} ROI와 {c['overlap_of_smaller']:.0%} 겹칩니다 - 경계를 확인하세요") for c in bad))
            result = save_roi(self.store, target_id=body["target_id"], view_id=body["view_id"], reference_image=ref,
                              polygons=body["polygons"], exclusions=body.get("exclusions", []), author=author,
                              note=body.get("note"))
            if result.get("changed", True):
                self.refresh_view_async(body["view_id"], first=[ref["image_id"]])
            return dict(result, background_refresh=self.bg)
        with self.lock:
            if path == "/api/label":
                im = self.image(body["image_id"])
                auto = self.obs().get(obs_key(im["image_id"], body["target_id"]))
                if not auto or auto.get("status") != "ok" or not auto.get("roi_ref"):
                    raise LabelError("no usable ROI for this image/target (rerun assign or draw/confirm ROI first)")
                cur = self.store.get(auto["roi_ref"]["kind"], auto["roi_ref"]["key"])
                if cur is None or cur["version"] != auto["roi_ref"]["version"]:
                    raise LabelError("observation is stale; press 'Refresh observation' first")
                payload = {k: body.get(k) for k in ("target_id", "activity_id", "regions", "fill_roi_state",
                                                       "undeterminable", "occluded", "whole_target_undeterminable", "note")}
                payload["roi_ref"] = auto["roi_ref"]
                payload["roi_source"] = auto["roi_source"]
                return save_label(self.store, self.tax, im, payload, author=author, source="manual",
                                  view_id=auto.get("view_id"))
            if path == "/api/observation":
                im = self.image(body["image_id"])
                payload = {k: body.get(k) for k in ("roi", "note", "quality_flags", "confirmed_from")}
                result = save_manual_observation(self.store, im, body["target_id"], payload, author)
                self._reassign([im])
                return result
            if path == "/api/decide":
                return self.store.decide(body["kind"], body["key"], int(body["version"]), author,
                                         body.get("decision", "approved"), body.get("note"))
            if path == "/api/view":
                p = dict(body["payload"])
                for k in ("reference_image_id", "start_time", "end_time", "view_id"):
                    if isinstance(p.get(k), str):
                        p[k] = p[k].strip().strip('"').strip("'").strip()
                if p.get("reference_image_id") not in self.by_id:
                    raise RegistryError(f"reference_image_id must be an ingested image (got {p.get('reference_image_id')!r})")
                if p.get("start_time") and p.get("end_time") and p["start_time"] > p["end_time"]:
                    raise RegistryError("start_time after end_time")
                cur = self.store.get("view", p["view_id"])
                payload = dict(cur["payload"] if cur else {}, **p, site_id=self.ctx.site["site_id"])
                payload.setdefault("camera_id", self.ctx.site["camera_id"])
                payload.setdefault("image_ids_extra", [])
                payload["classification_source"] = "manual"
                result = self.store.put("view", p["view_id"], payload, source="manual", author=author)
                self._obs = None
                return result
            if path == "/api/roi_skip":
                tid, vid = body["target_id"], body["view_id"]
                if self.store.get("target", tid) is None or self.store.get("view", vid) is None:
                    raise RegistryError("unknown target or view")
                visible = bool(body.get("visible", False))
                return self.store.put("roi_skip", f"{tid}|{vid}", {"target_id": tid, "view_id": vid, "visible": visible,
                                                                  "reason": "not_visible_in_view" if not visible else "visible"},
                                      source="manual", author=author)
            if path == "/api/target":
                return save_target(self.store, body["payload"], author)
            if path == "/api/applicability":
                return save_applicability(self.store, body["target_id"], body["activity_id"], body["applicability"],
                                          body.get("basis", ""), author, self.tax)
            if path == "/api/reassign":
                ids = body.get("image_ids") or [body["image_id"]]
                self._reassign([self.image(i) for i in ids])
                return {"ok": True, "images": len(ids)}
            if path == "/api/same_as_previous":
                return self.same_as_previous(body, author)
            if path == "/api/prefill":
                return self.prefill(body, author)
            if path == "/api/accept_view_proposal":
                return self.accept_view_proposal(body, author)
            if path == "/api/replace_label":
                return self.replace_label(body, author)
            if path == "/api/rebase_labels":
                return self.rebase_labels(body, author)
            if path == "/api/propagate":
                src, dst = self.image(body["src_image_id"]), self.image(body["dst_image_id"])
                dst_obs = self.obs().get(obs_key(dst["image_id"], body["target_id"]))
                if not dst_obs:
                    raise LabelError("destination has no observation")
                return propagate_label_draft(self.store, self.tax, src, dst, dst_obs, body["target_id"],
                                             body["activity_id"], author)
        raise KeyError(path)

    def _reassign(self, images):
        build_observations(self.ctx, images=[im for im in images if im["status"] == "ok"], cache=self.obs_cache,
                           quiet=True, all_images=self.images)
        self._obs = None
        self._obs_gen += 1

    # -- background refresh of whole views (so no photo is left with a stale ROI) --
    def view_images(self, view_id, focus_only: bool = True):
        views = store_views(self.store)
        focus = self.focus_ids() if focus_only else None
        return [im for im in self.images if im["status"] == "ok" and im.get("duplicate_of") is None
                and (focus is None or im["image_id"] in focus)
                and (view_id is None or assign_view(im, views)["view_id"] == view_id)]

    def refresh_view_async(self, view_id=None, first=()):
        """Queue the view's photos for a background observation refresh.

        One worker thread for the whole server (several ROI saves no longer start competing threads); it refreshes
        ONE photo per lock hold and pauses in between, so clicks in the UI are served between photos."""
        ims = self.view_images(view_id)
        with self._bg_cv:
            front = [i for i in first if i in self.by_id]
            rest = [i for i in self._bg_queue if i not in front]
            queued = set(front) | set(rest)
            self._bg_queue = front + rest + [im["image_id"] for im in ims if im["image_id"] not in queued]
            running = self._bg_thread is not None and self._bg_thread.is_alive()
            self.bg = dict(self.bg, running=True, view=view_id, queued=len(self._bg_queue),
                           images=(self.bg.get("done", 0) if running else 0) + len(self._bg_queue))
            if not running:
                self.bg["done"] = 0
            if self._bg_thread is None or not self._bg_thread.is_alive():
                self._bg_thread = threading.Thread(target=self._bg_worker, daemon=True)
                self._bg_thread.start()
            self._bg_cv.notify()

    def _bg_worker(self):
        import time
        done = 0
        while True:
            with self._bg_cv:
                if not self._bg_queue:
                    self.bg = dict(self.bg, running=False, queued=0)
                    return
                iid = self._bg_queue.pop(0)
            im = self.by_id.get(iid)
            if im is not None:
                try:
                    with self.lock:
                        self._reassign([im])
                except Exception:  # noqa: BLE001 - one bad photo must not stop the refresh
                    traceback.print_exc()
            done += 1
            self.bg = dict(self.bg, done=done, queued=len(self._bg_queue), images=done + len(self._bg_queue))
            time.sleep(0.05)                      # let waiting UI requests take the lock

    def todo(self) -> dict:
        from .todo import build_todo, rebase_candidates
        out = build_todo(self.ctx, self.images)
        out["rebase"] = rebase_candidates(self.store)
        # photos to SHOW next to each item (thumbnails), so decisions can be made on the to-do page
        timed = sorted([im for im in self.images if im["status"] == "ok" and not im.get("duplicate_of")
                        and im["capture"]["time_local"]], key=lambda im: im["capture"]["time_local"])
        times = [im["capture"]["time_local"] for im in timed]
        import bisect

        def around(t, before=3, after=3):
            i = bisect.bisect_right(times, t)
            pick = timed[max(0, i - before):i] + timed[i:i + after]
            return [{"image_id": im["image_id"], "time": im["capture"]["time_local"],
                     "side": "before" if im["capture"]["time_local"] <= t else "after"} for im in pick]

        def spread(t0, t1, n=4):
            lo, hi = bisect.bisect_left(times, t0), bisect.bisect_right(times, t1)
            seg = timed[lo:hi]
            if not seg:
                return []
            idx = sorted({round(k * (len(seg) - 1) / max(1, n - 1)) for k in range(n)})
            return [{"image_id": seg[k]["image_id"], "time": seg[k]["capture"]["time_local"]} for k in idx]

        for sec in out["sections"]:
            for it in sec["items"]:
                if not isinstance(it, dict):
                    continue
                if sec["id"] == "view_proposals" and it.get("kind") == "update_view":
                    cur, prop = it["current"], it["proposed"]
                    if prop.get("end_time") != cur.get("end_time"):
                        it["photos_around_proposed_end"] = around(prop["end_time"])
                    if prop.get("start_time") != cur.get("start_time"):
                        it["photos_around_proposed_start"] = around(prop["start_time"])
                elif sec["id"] == "views_unapproved" and it.get("start_time"):
                    it["photos_in_view"] = spread(it["start_time"], it["end_time"])
        return self._with_rois(out)

    def _with_rois(self, out):
        out["rois"] = {r["key"]: {"version": r["version"], "polygons": r["payload"]["polygons"],
                                  "exclusions": r["payload"].get("exclusions", []), "image_size": r["payload"]["image_size"]}
                       for r in self.store.latest("roi")}
        return out

    def accept_view_proposal(self, body: dict, author: str) -> dict:
        """The reviewer accepts an automatic view proposal: saved as a manual view version and approved."""
        vid = body["view_id"]
        cur = self.store.get("view", vid)
        if cur is None:
            raise RegistryError("unknown view")
        prop = body.get("proposed") or {}
        allowed = {k: prop[k] for k in ("start_time", "end_time", "reference_image_id", "retired") if k in prop}
        if allowed.get("reference_image_id") and allowed["reference_image_id"] not in self.by_id:
            raise RegistryError("reference_image_id must be an ingested image")
        payload = dict(cur["payload"], **allowed, classification_source="manual",
                       accepted_proposal=body.get("candidate_id"))
        r = self.store.put("view", vid, payload, source="manual", author=author,
                           note="accepted automatic proposal in the to-do list")
        d = self.store.decide("view", vid, r["version"], author, "approved", note="accepted proposal (to-do list)")
        self._obs = None
        self.refresh_view_async(vid)
        return {"put": r, "approval": d}

    def replace_label(self, body: dict, author: str) -> dict:
        """The reviewer compared two photos and decided that photo B has the same state as photo A:
        B's label is replaced by A's state regions (occlusion of B is kept) and approved under the
        reviewer's name. Only within the same ROI version (same view, unchanged ROI)."""
        iid, tid, act = body["key"].split("|")
        im, src_im = self.image(iid), self.image(body["from_image_id"])
        src = self.store.get("state_label", label_key(src_im["image_id"], tid, act))
        cur = self.store.get("state_label", body["key"])
        o = self.obs().get(obs_key(iid, tid))
        if src is None or o is None or o.get("roi_ref") != src["payload"].get("roi_ref"):
            raise LabelError("both photos must have labels on the same ROI version (same view)")
        payload = {k: v for k, v in src["payload"].items()
                   if k not in ("image_id", "coord_space", "image_size", "note", "draft_basis", "propagated_from",
                                "occluded", "undeterminable", "whole_target_undeterminable")}
        keep = cur["payload"] if cur else {}
        payload.update(occluded=keep.get("occluded", []), undeterminable=keep.get("undeterminable", []),
                       whole_target_undeterminable=keep.get("whole_target_undeterminable"),
                       note=f"state copied from {src_im['image_id']} by {author} after side-by-side check (to-do list)")
        r = save_label(self.store, self.tax, im, payload, author=author, source="manual", view_id=o.get("view_id"))
        d = self.store.decide("state_label", body["key"], r["version"], author, "approved",
                              note=f"replaced with state of {src_im['image_id']} (to-do consistency check)")
        return {"put": r, "approval": d}

    def rebase_labels(self, body: dict, author: str) -> dict:
        """Move labels drawn on an older ROI version onto the current one (item 5).

        The label regions are unchanged (pixel coordinates); only the ROI they are clipped to changes.
        The reviewer looked at the thumbnails with the new ROI; with approve=true each moved label that was
        approved before is approved again under the reviewer's name. Drafts stay drafts.
        """
        from .todo import rebase_candidates
        keys = set(body.get("keys") or [])
        cand = [c for group in rebase_candidates(self.store).values() for c in group if not keys or c["key"] in keys]
        ims = [self.by_id[c["image_id"]] for c in cand if c["image_id"] in self.by_id]
        self._reassign(ims)
        done, skipped = [], []
        for c in cand:
            im = self.by_id.get(c["image_id"])
            rec = self.store.get("state_label", c["key"])
            _, tid, act = c["key"].split("|")
            o = self.obs().get(obs_key(c["image_id"], tid))
            if im is None or rec is None or not o or o.get("status") != "ok" or (o.get("roi_ref") or {}).get("kind") != "roi":
                skipped.append({"key": c["key"], "reason": "no current observation"})
                continue
            payload = {k: v for k, v in rec["payload"].items() if k not in ("image_id", "coord_space", "image_size")}
            payload["roi_ref"], payload["roi_source"] = o["roi_ref"], o["roi_source"]
            payload["rebased_from_roi_version"] = c["drawn_on_roi_version"]
            source = "manual" if (c["was_approved"] and body.get("approve")) or rec["source"] == "manual" else rec["source"]
            try:
                r = save_label(self.store, self.tax, im, payload, author=author if source == "manual" else rec["author"],
                               source=source, view_id=o.get("view_id"))
                if c["was_approved"] and body.get("approve"):
                    self.store.decide("state_label", c["key"], r["version"], author, "approved",
                                      note=f"re-approved after ROI change v{c['drawn_on_roi_version']}->v{c['current_roi_version']}")
                done.append(c["key"])
            except (LabelError, StoreError) as error:
                skipped.append({"key": c["key"], "reason": str(error)[:160]})
        return {"rebased": len(done), "skipped": skipped}

    def roi_copy(self, body: dict, author: str) -> dict:
        """Copy every target's ROI of one view into another view as new *unapproved* ROIs.

        Pixel coordinates are copied unchanged (clipped to the frame), so this only makes sense when
        the camera barely moved. The reviewer must adjust (Edit vertices) and approve each ROI.
        """
        from shapely.geometry import box
        from .schemas import geometry_to_polys, polygon_geometry
        src_view, dst_view = body["from_view"], body["to_view"]
        dst = self.store.get("view", dst_view)
        if dst is None:
            raise RegistryError("unknown destination view")
        ref = self.image(dst["payload"]["reference_image_id"])
        frame = box(0, 0, ref["size"][0], ref["size"][1])
        done, skipped = [], []
        for rec in self.store.latest("roi"):
            tid, vid = rec["key"].split("|")
            if vid != src_view or (body.get("targets") and tid not in body["targets"]):
                continue
            if self.store.get("roi", roi_key(tid, dst_view)) is not None and not body.get("overwrite"):
                skipped.append({"target": tid, "reason": "destination_roi_exists"})
                continue
            p = rec["payload"]
            polys = []
            for poly in p["polygons"]:
                polys += geometry_to_polys(polygon_geometry(poly).intersection(frame), min_area=100.0)
            excl = []
            for e in p.get("exclusions", []):
                for q in geometry_to_polys(polygon_geometry({"exterior": e["points"], "holes": []}).intersection(frame), 100.0):
                    excl.append({"points": q["exterior"], "reason": e.get("reason")})
            try:
                save_roi(self.store, target_id=tid, view_id=dst_view, reference_image=ref, polygons=polys, exclusions=excl,
                         author=author, note=f"copied from {src_view} (pixel coordinates unchanged); adjust and approve")
                done.append(tid)
            except (RegistryError, StoreError) as error:
                skipped.append({"target": tid, "reason": str(error)[:120]})
        if done:
            self.refresh_view_async(dst_view, first=[ref["image_id"]])
        return {"copied": done, "skipped": skipped, "from_view": src_view, "to_view": dst_view}


def make_handler(service: ReviewService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # the browser cancelled the request (e.g. moved to the next photo before this one loaded):
                # nothing to answer, nothing lost
                self.close_connection = True

        def _timed(self, t0, what):
            import time
            dt = time.time() - t0
            if dt > 1.5:
                print(f"[review] slow {what} {self.path.split('?')[0][:60]} {dt:.1f}s (background queue {len(service._bg_queue)})",
                      flush=True)

        def do_GET(self):
            import time
            t0 = time.time()
            try:
                return self._do_get()
            finally:
                self._timed(t0, "GET")

        def _do_get(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if url.path in ("/todo", "/todo.html"):
                    return self._send(200, (STATIC / "todo.html").read_bytes(), "text/html; charset=utf-8")
                if url.path == "/api/todo":
                    return self._send(200, service.todo())
                if url.path.startswith("/api/thumb/"):
                    return self._send(200, service.thumb(url.path.split("/")[-1]), "image/jpeg")
                if url.path == "/api/meta":
                    return self._send(200, service.meta())
                if url.path == "/api/images":
                    return self._send(200, service.image_list())
                if url.path.startswith("/api/image/"):
                    iid = url.path.split("/")[-1]
                    return self._send(200, service.jpeg(iid, int(q.get("max", ["1600"])[0])), "image/jpeg")
                if url.path.startswith("/api/shapes/"):
                    return self._send(200, service.shapes(url.path.split("/")[-1]))
                if url.path.startswith("/api/item/"):
                    return self._send(200, service.item(url.path.split("/")[-1]))
                if url.path == "/api/history":
                    kind, key = q["kind"][0], q["key"][0]
                    return self._send(200, {"versions": service.store.history(kind, key),
                                            "approvals": service.store.approvals(kind, key)})
                if url.path == "/api/events":
                    return self._send(200, service.store.events(200))
                return self._send(404, {"error": "not found"})
            except (KeyError, SourceIntegrityError) as error:
                return self._send(404, {"error": str(error)})
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": f"{type(error).__name__}: {error}"})

        def do_POST(self):
            import time
            t0 = time.time()
            try:
                return self._do_post()
            finally:
                self._timed(t0, "POST")

        def _do_post(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                return self._send(200, service.post(urlparse(self.path).path, body))
            except (StoreError, RegistryError, LabelError, ValueError, KeyError) as error:
                return self._send(400, {"error": str(error)})
            except Exception as error:  # noqa: BLE001
                traceback.print_exc()
                return self._send(500, {"error": f"{type(error).__name__}: {error}"})
    return Handler


def serve(ctx: RunContext, host: str = "127.0.0.1", port: int = 8765, force_unlock: bool = False):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("the review server only binds to the local machine")
    lock = RunLock(ctx.run_dir, "review server", force=force_unlock).acquire()  # auto / assign must not run meanwhile
    try:
        service = ReviewService(ctx)
        service.refresh_view_async(None)  # samples / labelled photos / references follow the current ROI versions
        server = ThreadingHTTPServer((host, port), make_handler(service))
        print(f"[review] http://{host}:{port}/  (run {ctx.info['run_id']}, store {ctx.info['resolved']['store_path']})")
        print(f"[review] to-do list: http://{host}:{port}/todo")
        print("[review] Ctrl+C to stop. Images are served from the original ZIPs; nothing is uploaded.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            service.reader.close()
            service.obs_cache.close()
            service.obs_cache.maybe_compact()
    finally:
        lock.release()
