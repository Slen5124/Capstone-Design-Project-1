"""R10: U-Net (or any dense model) training export + draft previews and overlays.

Model input is the RGB image only. Target IDs, capture times and ROI metadata
are written to metadata.jsonl for loaders/evaluation; nothing here feeds them
into the network automatically.

Modes
  draft  preview of everything that exists (draft labels included) under
         ``draft_state_masks/``; ``training_ready=false``; no split lists.
  train  only approved (image, target, activity) labels; everything else 255.
         If nothing is approved the export is BLOCKED (report + empty lists).

Layout (train)
  images/<sample>.jpg                 RGB, transformed
  semantic_masks/<sample>.png         uint8 object class ids, 255 ignore (pseudo-label origin, see metadata)
  target_masks/<sample>.png           uint8 (<=254 targets) or uint16, 0 none, max value ignore/overlap
  state_masks/<activity>/<sample>.png uint8 0/1 (2 = occluded if --occlusion-class), 255 ignore
  visibility_masks/<sample>.png       uint8 0 outside ROI, 1 visible, 2 occluded, 3 undeterminable
  metadata.jsonl, targets.json, label_map.json, splits/{train,val,test}.txt, export_report.json
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import SCHEMA_VERSION
from .assign_surfaces import load_observations
from .rasterize import (apply_image, apply_mask, make_transform, render_overlay, roi_mask, save_mask_png,
                        semantic_mask, state_and_visibility, target_dtype, target_mask)
from .runctx import Progress, RunContext, atomic_write_json, utc_stamp
from .schemas import label_key, obs_key
from .source_adapter import SourceIntegrityError, SourceReader, load_images
from .split_dataset import load_assignments
from .state_annotations import eligibility
from .target_registry import compatible_activities, roi_hints


def _targets(store, taxonomy):
    out = []
    recs = sorted([r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"],
                  key=lambda r: r["key"])
    for index, r in enumerate(recs, 1):
        p = r["payload"]
        acts = {}
        for act in compatible_activities(p["surface_type"], taxonomy):
            key = f"{r['key']}|{act}"
            a = store.get("applicability", key)
            acts[act] = {"applicability": a["payload"]["applicability"] if a else "missing",
                         "review": store.review_state("applicability", key)["status"]}
        out.append({"index": index, "target_id": r["key"], "surface_type": p["surface_type"], "zone_id": p.get("zone_id"),
                    "name": p.get("name"), "identity_status": p.get("identity_status"), "version": r["version"],
                    "review": store.review_state("target", r["key"])["status"], "activities": acts})
    return out


def label_map(taxonomy: dict, n_targets: int, occlusion_class: bool) -> dict:
    oc = taxonomy["object_classes"]
    dtype, ignore = target_dtype(n_targets)
    sv = dict(taxonomy["state_export"]["values"])
    if occlusion_class:
        sv["occluded"] = taxonomy["state_export"]["optional_occluded_class"]
    return {"schema_version": SCHEMA_VERSION, "taxonomy_version": taxonomy.get("taxonomy_version"),
            "semantic": {"file_format": "PNG 8-bit grayscale", "dtype": "uint8",
                         "classes": [{"id": c["id"], "name": c["name"], "source_labels": c["labels"], "layer": c["layer"]}
                                     for c in oc["classes"]],
                         "ignore_value": oc.get("ignore_value", 255), "ignore_meaning": "unlabeled, ignore-labels, same-layer conflicts, padding",
                         "overlap_policy": oc["layers"], "mapping_source": oc.get("source")},
            "target": {"file_format": "PNG 8-bit" if dtype == np.uint8 else "PNG 16-bit", "dtype": np.dtype(dtype).name,
                       "background_value": 0, "ignore_value": ignore,
                       "meaning": "index from targets.json; ignore = overlapping ROIs or padding. Not an object class."},
            "state": {"file_format": "PNG 8-bit grayscale", "dtype": "uint8", "values": sv, "ignore_value": 255,
                      "ignore_is_model_class": False, "ignored_by_default": taxonomy["state_export"]["ignored_by_default"]},
            "visibility": {"file_format": "PNG 8-bit grayscale", "dtype": "uint8",
                           "values": taxonomy["visibility_export"]["values"], "padding_value": 0,
                           "notes": taxonomy["visibility_export"].get("notes")},
            "ratio_semantics": taxonomy.get("ratio_semantics")}


def _usable_roi(store, obs, mode):
    if not obs or obs.get("status") != "ok" or not obs.get("roi"):
        return False, f"roi_{(obs or {}).get('status', 'missing')}"
    if mode == "draft":
        return True, None
    ref = obs["roi_ref"]
    cur = store.get(ref["kind"], ref["key"])
    if cur is None or cur["version"] != ref["version"]:
        return False, "observation_stale_rerun_assign"
    if obs["roi_source"] in ("reference_image", "same_view_reuse"):
        if store.review_state("roi", ref["key"])["status"] != "approved":
            return False, "roi_not_approved"
        if store.review_state("view", obs["view_id"])["status"] != "approved":
            return False, "view_not_approved"
        return True, None
    if obs["roi_source"] == "manual":
        ok = store.review_state("observation", ref["key"])["status"] == "approved"
        return ok, None if ok else "manual_observation_not_approved"
    return False, f"roi_source_{obs['roi_source']}_unconfirmed"


def plan_export(ctx: RunContext, mode: str, images: list[dict], assignments: dict | None) -> dict:
    """Decide per (image, target, activity) what can be exported (no pixel work)."""
    store, tax = ctx.store(), ctx.taxonomy
    obs_all = load_observations(ctx)
    targets = _targets(store, tax)
    plan, reasons = [], Counter()
    eligible_count = 0
    for im in images:
        split = (assignments or {}).get(im["image_id"], {}).get("split", "unsplit")
        row = {"image": im, "split": split, "targets": []}
        for t in targets:
            obs = obs_all.get(obs_key(im["image_id"], t["target_id"]))
            usable, why = _usable_roi(store, obs, mode)
            item = {"target": t, "obs": obs, "roi_usable": usable, "roi_reason": why, "activities": {}}
            for act in t["activities"]:
                if mode == "train":
                    e = eligibility(store, im, obs, t["target_id"], act)
                    if e["eligible"] and e["label"]["payload"].get("propagated_from") and assignments:
                        src = assignments.get(e["label"]["payload"]["propagated_from"]["image_id"], {}).get("split")
                        if src in ("val", "test") and split == "train":
                            e = dict(e, eligible=False, reasons=["propagated_from_eval_split"])
                    if e["eligible"]:
                        eligible_count += 1
                    for r in e["reasons"]:
                        reasons[f"{act}:{r}"] += 1
                    item["activities"][act] = {"use_label": e["eligible"], "label": e["label"] if e["eligible"] else None,
                                               "reasons": e["reasons"], "review": e["label_review"]["status"]}
                else:
                    label = store.get("state_label", label_key(im["image_id"], t["target_id"], act))
                    item["activities"][act] = {"use_label": label is not None, "label": label,
                                               "reasons": [] if label else ["state_unlabeled"],
                                               "review": store.review_state("state_label", label["key"])["status"] if label else "missing"}
            row["targets"].append(item)
        plan.append(row)
    return {"rows": plan, "targets": targets, "eligible_labels": eligible_count, "ineligible_reasons": reasons}


def export(ctx: RunContext, *, mode: str = "draft", size=None, resize_mode: str | None = None, crop: bool = False,
           occlusion_class: bool = False, auto_occluder_ignore: bool | None = None, limit: int = 0,
           include_duplicates: bool = False, name: str | None = None, image_ids: list[str] | None = None) -> dict:
    if mode not in ("draft", "train"):
        raise ValueError("mode must be draft or train")
    ecfg = dict({"image_format": "jpg", "jpeg_quality": 95, "size": None, "resize_mode": "letterbox",
                 "crop_margin": 0.05, "min_valid_pixels": 64, "auto_occluder_ignore": True}, **ctx.site.get("export", {}))
    size = size or ecfg["size"]
    resize_mode = resize_mode or ecfg["resize_mode"]
    auto_occ = ecfg["auto_occluder_ignore"] if auto_occluder_ignore is None else auto_occluder_ignore
    store, tax = ctx.store(), ctx.taxonomy
    images = [im for im in load_images(ctx) if include_duplicates or im.get("duplicate_of") is None]
    try:
        assignments = load_assignments(ctx)
    except FileNotFoundError:
        if mode == "train":
            raise
        assignments = None
    skipped_unlabeled = 0
    if image_ids is not None:
        wanted = set(image_ids)
        images = [im for im in images if im["image_id"] in wanted]
    elif mode == "train":
        # only photos that have at least one state label can contribute; skip the rest without per-target checks
        labeled = {r["key"].split("|")[0] for r in store.latest("state_label")}
        skipped_unlabeled = sum(1 for im in images if im["image_id"] not in labeled)
        images = [im for im in images if im["image_id"] in labeled]
    if limit:
        images = images[:limit]
    plan = plan_export(ctx, mode, images, assignments)
    targets = plan["targets"]
    import secrets
    out = ctx.run_dir / "exports" / (name or f"{utc_stamp()}_{secrets.token_hex(2)}_{mode}{'_crop' if crop else '_full'}")
    out.mkdir(parents=True, exist_ok=False)
    lm = label_map(tax, len(targets), occlusion_class)
    atomic_write_json(out / "label_map.json", lm)
    atomic_write_json(out / "targets.json", {"schema_version": SCHEMA_VERSION, "targets": targets})
    activities = sorted(tax["activities"])
    report = {"schema_version": SCHEMA_VERSION, "mode": mode, "crop": crop, "size": size, "resize_mode": resize_mode,
              "occlusion_class": occlusion_class, "auto_occluder_ignore": auto_occ,
              "units": {"images": len(images), "targets": len(targets),
                        "target_activity_pairs_per_image": sum(len(t["activities"]) for t in targets)},
              "photos_without_any_label_skipped": skipped_unlabeled,
              "eligible_labels": plan["eligible_labels"],
              "ineligible_label_reasons": dict(plan["ineligible_reasons"].most_common()),
              "training_ready": mode == "train"}
    if mode == "train":
        (out / "splits").mkdir()
        for s in ("train", "val", "test"):
            (out / "splits" / f"{s}.txt").write_text("", encoding="utf-8")
        if plan["eligible_labels"] == 0:
            report.update(status="blocked_no_approved_labels", samples_written=0,
                          next_actions=_next_actions(store, ctx))
            atomic_write_json(out / "export_report.json", report)
            atomic_write_json(out / "review_snapshot.json", store.snapshot())
            print(f"[export] BLOCKED: no approved (image,target,activity) labels. See {out / 'export_report.json'}")
            return dict(report, export_dir=str(out))
    state_dir = "state_masks" if mode == "train" else "draft_state_masks"
    for sub in ["images", "semantic_masks", "target_masks", "visibility_masks"] + [f"{state_dir}/{a}" for a in activities]:
        (out / sub).mkdir(parents=True, exist_ok=True)
    if mode == "draft":
        (out / "README_NOT_TRAINING_DATA.txt").write_text(
            "Draft preview. State masks here come from unapproved draft labels and must not be used as ground truth.\n",
            encoding="utf-8")
    reader = SourceReader(ctx)
    progress = Progress(ctx, f"export-{mode}", len(plan["rows"]), "images", every=5)
    split_lists = defaultdict(list)
    excluded = []
    samples = 0
    meta_handle = open(out / "metadata.jsonl", "w", encoding="utf-8")
    try:
        for row in plan["rows"]:
            im, split = row["image"], row["split"]
            if mode == "train" and split.startswith("excluded"):
                excluded.append({"image_id": im["image_id"], "reason": split})
                progress.tick("held")
                continue
            if mode == "train" and not any(a["use_label"] for t in row["targets"] for a in t["activities"].values()):
                excluded.append({"image_id": im["image_id"], "reason": "no_eligible_activity_label"})
                progress.tick("held")
                continue
            try:
                rgb, _ = reader.rgb(im)
                doc = reader.annotation(im)
            except SourceIntegrityError as error:
                excluded.append({"image_id": im["image_id"], "reason": error.code})
                progress.tick("failed")
                continue
            sz = im["size"]
            sem, sem_stats = semantic_mask(doc, sz, tax)
            usable = [t for t in row["targets"] if t["roi_usable"]]
            roi_masks = {t["target"]["target_id"]: roi_mask(t["obs"]["roi"], sz) for t in usable}
            tmask, tstats = target_mask([(t["target"]["index"], t["obs"]["roi"]) for t in usable], sz, len(targets))
            vis = np.zeros((sz[1], sz[0]), np.uint8)
            states = {a: np.full((sz[1], sz[0]), 255, np.uint8) for a in activities}
            per_target = []
            for t in row["targets"]:
                tid = t["target"]["target_id"]
                entry = {"target_id": tid, "index": t["target"]["index"], "roi_usable": t["roi_usable"],
                         "roi_reason": t["roi_reason"], "roi_source": (t["obs"] or {}).get("roi_source"),
                         "roi_ref": (t["obs"] or {}).get("roi_ref"), "activities": {}}
                if t["roi_usable"]:
                    occl = []
                    if auto_occ:
                        occl = [p for c in t["obs"].get("occlusion_candidates", []) for p in c["polygons"]]
                    for act, a in t["activities"].items():
                        label = a["label"]["payload"] if a["use_label"] else None
                        st, v, s = state_and_visibility(label, roi_masks[tid], sz, occl, occlusion_class)
                        valid = st != 255
                        clash = valid & (states[act] != 255) & (states[act] != st)
                        states[act][valid] = st[valid]
                        states[act][clash] = 255
                        vis = np.maximum(vis, v)
                        entry["activities"][act] = {"label_used": a["use_label"], "review": a["review"],
                                                    "reasons": a["reasons"], "stats_original_px": s,
                                                    "cross_target_conflict_px": int(clash.sum()),
                                                    "label_version": a["label"]["version"] if a["label"] else None,
                                                    "label_source": a["label"]["source"] if a["label"] else None}
                        if a["use_label"] and mode == "train":
                            ap = store.review_state("state_label", a["label"]["key"])["approval"]
                            entry["activities"][act]["approval"] = {k: ap[k] for k in ("id", "reviewer", "decided_at", "version")}
                else:
                    for act, a in t["activities"].items():
                        entry["activities"][act] = {"label_used": False, "reasons": [t["roi_reason"]] + a["reasons"]}
                per_target.append(entry)
            jobs = [("full", None, im["image_id"])]
            if crop:
                jobs = []
                for t in usable:
                    g = roi_masks[t["target"]["target_id"]]
                    ys, xs = np.nonzero(g)
                    if not len(xs):
                        continue
                    mx = ecfg["crop_margin"] * (xs.max() - xs.min() + 1)
                    my = ecfg["crop_margin"] * (ys.max() - ys.min() + 1)
                    box = (xs.min() - mx, ys.min() - my, xs.max() + 1 + mx, ys.max() + 1 + my)
                    jobs.append(("crop", (t["target"]["target_id"], box), f"{im['image_id']}__{t['target']['target_id']}"))
            for kind, cinfo, sample_id in jobs:
                T = make_transform(sz, size, resize_mode if size else "none", cinfo[1] if cinfo else None)
                img_out = apply_image(rgb, T)
                sem_o = apply_mask(sem, T, 255)
                tm_o = apply_mask(tmask, T, tstats["ignore_value"])
                vis_o = apply_mask(vis, T, 0)
                act_meta = {}
                any_valid = False
                for act in activities:
                    st_o = apply_mask(states[act], T, 255)
                    valid_px = int((st_o != 255).sum())
                    counts = {name: int((st_o == v).sum()) for name, v in lm["state"]["values"].items()}
                    is_valid = valid_px >= ecfg["min_valid_pixels"]
                    any_valid |= is_valid
                    act_meta[act] = {"valid": is_valid, "valid_px": valid_px, **{f"{k}_px": v for k, v in counts.items()},
                                     "file": f"{state_dir}/{act}/{sample_id}.png"}
                    save_mask_png(st_o, out / state_dir / act / f"{sample_id}.png")
                if mode == "train" and not any_valid:
                    excluded.append({"sample_id": sample_id, "image_id": im["image_id"], "reason": "all_state_pixels_ignore"})
                    for act in activities:
                        (out / state_dir / act / f"{sample_id}.png").unlink()
                    continue
                ext = "jpg" if ecfg["image_format"] == "jpg" else "png"
                if ext == "jpg":
                    img_out.save(out / "images" / f"{sample_id}.jpg", quality=ecfg["jpeg_quality"])
                else:
                    img_out.save(out / "images" / f"{sample_id}.png")
                save_mask_png(sem_o, out / "semantic_masks" / f"{sample_id}.png")
                save_mask_png(tm_o, out / "target_masks" / f"{sample_id}.png")
                save_mask_png(vis_o, out / "visibility_masks" / f"{sample_id}.png")
                meta = {"sample_id": sample_id, "kind": kind, "image_id": im["image_id"], "parent_image_id": im["image_id"],
                        "crop_target_id": cinfo[0] if cinfo else None, "split": split, "mode": mode,
                        "training_ready": mode == "train",
                        "files": {"image": f"images/{sample_id}.{ext}", "semantic": f"semantic_masks/{sample_id}.png",
                                  "target": f"target_masks/{sample_id}.png", "visibility": f"visibility_masks/{sample_id}.png"},
                        "model_inputs": ["image"],
                        "transform": T,
                        "source": {"photo": im["source_photo"], "annotation": im["annotation"]},
                        "capture": im["capture"], "camera_id": im["camera_id"], "site_id": im["site_id"],
                        "view_id": (row["targets"][0]["obs"] or {}).get("view_id") if row["targets"] else None,
                        "content_group": im.get("content_group"),
                        "semantic_label_origin": "qa_v3 automatic pseudo-label (classification "
                                                 f"{(im['annotation'] or {}).get('qa_v3_classification')}); not human-verified",
                        "semantic_stats_original_px": sem_stats, "target_mask_stats": tstats,
                        "targets": per_target, "activities": act_meta}
                meta_handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
                samples += 1
                if mode == "train":
                    split_lists[split].append(sample_id)
            progress.tick("saved")
    finally:
        meta_handle.close()
        reader.close()
    if mode == "train":
        for s in ("train", "val", "test"):
            (out / "splits" / f"{s}.txt").write_text("\n".join(split_lists.get(s, [])) + ("\n" if split_lists.get(s) else ""),
                                                      encoding="utf-8")
    with open(out / "excluded.jsonl", "w", encoding="utf-8") as handle:
        for e in excluded:
            handle.write(json.dumps(e, ensure_ascii=False) + "\n")
    report.update(status="ok" if samples else ("empty" if mode == "train" else "ok_draft_empty"),
                  samples_written=samples, excluded=dict(Counter(e["reason"] for e in excluded)),
                  split_counts={k: len(v) for k, v in split_lists.items()}, progress=progress.finish(),
                  next_actions=_next_actions(store, ctx))
    atomic_write_json(out / "export_report.json", report)
    atomic_write_json(out / "review_snapshot.json", store.snapshot())
    print(f"[export] {mode}: {samples} samples -> {out}")
    return dict(report, export_dir=str(out))


def _next_actions(store, ctx) -> list[str]:
    acts = []
    counts = store.counts()
    for kind in ("view", "target", "applicability", "roi", "state_label"):
        c = counts.get(kind, {})
        pending = sum(v for k, v in c.items() if k != "approved")
        if not c:
            acts.append(f"{kind}: none exist yet — create in review UI")
        elif pending:
            acts.append(f"{kind}: {pending} not approved ({c}) — review in UI")
    for rec in store.latest("target"):
        if rec["payload"].get("identity_status") != "confirmed":
            acts.append(f"target {rec['key']}: identity_status={rec['payload'].get('identity_status')} -> confirm or mark unresolved")
    for rec in store.latest("applicability"):
        if rec["payload"]["applicability"] == "unknown":
            acts.append(f"applicability {rec['key']}: unknown -> decide applicable/not_applicable with basis")
    return acts


def make_overlays(ctx: RunContext, limit: int = 12, image_ids: list[str] | None = None, max_side: int = 900) -> list[str]:
    from PIL import Image  # noqa: F401
    store, tax = ctx.store(), ctx.taxonomy
    images = [im for im in load_images(ctx) if im.get("duplicate_of") is None]
    if image_ids:
        images = [im for im in images if im["image_id"] in set(image_ids)]
    elif limit and len(images) > limit:
        step = (len(images) - 1) / (limit - 1) if limit > 1 else 1
        images = [images[round(i * step)] for i in range(limit)]
    obs_all = load_observations(ctx)
    by_entry = {im["source_photo"]["entry"]: im for im in load_images(ctx, include_held=True)}
    hints = roi_hints(ctx, by_entry)
    reader = SourceReader(ctx)
    out_dir = ctx.run_dir / "overlays"
    out_dir.mkdir(exist_ok=True)
    written = []
    targets = _targets(store, tax)
    try:
        for im in images:
            rgb, _ = reader.rgb(im)
            doc = reader.annotation(im)
            sem, _ = semantic_mask(doc, im["size"], tax)
            rois, occl, states = {}, {}, {}
            for t in targets:
                o = obs_all.get(obs_key(im["image_id"], t["target_id"]))
                if o and o.get("status") == "ok" and o.get("roi"):
                    rois[t["target_id"]] = o["roi"]
                    occl[t["target_id"]] = [p for c in o.get("occlusion_candidates", []) for p in c["polygons"]]
                    rm = roi_mask(o["roi"], im["size"])
                    for act in t["activities"]:
                        lab = store.get("state_label", label_key(im["image_id"], t["target_id"], act))
                        if lab:
                            st, _, _ = state_and_visibility(lab["payload"], rm, im["size"])
                            states[f"{t['target_id']}:{act}"] = st
            title = (f"{im['image_id']}  {im['capture']['time_local']} (tz unconfirmed)  "
                     f"qa_v3={(im['annotation'] or {}).get('qa_v3_classification')}  DRAFT - not reviewed")
            panel = render_overlay(rgb, sem, tax, rois, occl, states or None, title, max_side,
                                   hints=[h for h in hints if h.get("image_id") == im["image_id"]])
            path = out_dir / f"{im['image_id']}.jpg"
            panel.save(path, quality=88)
            written.append(str(path))
    finally:
        reader.close()
    return written
