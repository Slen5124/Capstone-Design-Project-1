"""The human to-do queue: only the decisions a person has to make, in the order that avoids rework.

Order matters (item 5): views first, then ROIs (incl. doors/passages), then
labels. Approving labels before the ROI of their view is final would make
every later ROI edit invalidate them.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .index_views import assign_view, store_views
from .runctx import RunContext, atomic_write_json


def _read(ctx, rel):
    path = ctx.run_dir / "stages" / rel
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def unassigned_gaps(images: list[dict], views: list[dict], max_examples: int = 8) -> list[dict]:
    """Photos that fall between views, grouped by the gap they are in (key = view before / after)."""
    spans = sorted((v["start_time"], v["end_time"], v["view_id"]) for v in views if v.get("start_time"))
    groups = defaultdict(list)
    for im in images:
        t = im["capture"]["time_local"]
        before = [sp for sp in spans if sp[1] < t]
        after = [sp for sp in spans if sp[0] > t]
        groups[(before[-1][2] if before else None, after[0][2] if after else None)].append(im)
    out = []
    for (b, a), ims in sorted(groups.items(), key=lambda kv: kv[1][0]["capture"]["time_local"]):
        ims.sort(key=lambda im: im["capture"]["time_local"])
        idx = sorted({round(k * (len(ims) - 1) / max(1, max_examples - 1)) for k in range(min(max_examples, len(ims)))})
        out.append({"gap": f"{b or 'start'}-{a or 'end'}", "after_view": b, "before_view": a, "photos": len(ims),
                    "from": ims[0]["capture"]["time_local"], "to": ims[-1]["capture"]["time_local"],
                    "examples": [{"image_id": ims[k]["image_id"], "time": ims[k]["capture"]["time_local"]} for k in idx],
                    "image_ids": [im["image_id"] for im in ims][:5000]})
    return out


def rebase_candidates(store) -> dict[str, list[dict]]:
    """Labels drawn on an older version of their ROI, grouped by ROI key (ROI was edited afterwards)."""
    rois = {r["key"]: r["version"] for r in store.latest("roi")}
    states = store.review_states("state_label")
    out = defaultdict(list)
    for rec in store.latest("state_label"):
        ref = rec["payload"].get("roi_ref") or {}
        if ref.get("kind") != "roi" or ref.get("key") not in rois or ref.get("version") == rois[ref["key"]]:
            continue
        st = states.get(rec["key"], {})
        was_approved = any(a["decision"] == "approved" for a in store.approvals("state_label", rec["key"]))
        out[ref["key"]].append({"key": rec["key"], "image_id": rec["key"].split("|")[0], "label_version": rec["version"],
                                "drawn_on_roi_version": ref["version"], "current_roi_version": rois[ref["key"]],
                                "status": st.get("status"), "was_approved": was_approved, "source": rec["source"]})
    return dict(out)


def label_consistency(store, images, back_tol: float = 0.15, jump: float = 0.4, jump_hours: float = 3.0) -> list[dict]:
    from datetime import datetime
    from .timeline import installed_fraction
    by_id = {im["image_id"]: im for im in images}
    views = store_views(store)
    states = store.review_states("state_label")
    rows = defaultdict(list)
    for rec in store.latest("state_label"):
        if states.get(rec["key"], {}).get("status") != "approved":
            continue
        iid, tid, act = rec["key"].split("|")
        im = by_id.get(iid)
        ref = rec["payload"].get("roi_ref") or {}
        roi = store.get(ref.get("kind"), ref.get("key"), ref.get("version")) if ref else None
        if im is None or roi is None or not im["capture"]["time_local"]:
            continue
        roi_p = roi["payload"].get("roi") if ref["kind"] == "observation" else roi["payload"]
        f = installed_fraction(rec["payload"], roi_p, im["size"])
        if f is None:
            continue
        vid = assign_view(im, views)["view_id"]
        rows[(tid, act, vid)].append((im["capture"]["time_local"], iid, f, rec["author"]))
    out = []
    for (tid, act, vid), rs in rows.items():
        rs.sort()
        best, best_id = 0.0, None
        prev = None
        for t, iid, f, author in rs:
            reason = None
            if f < best - back_tol:
                reason = f"installed {f:.0%} < earlier {best:.0%} (거꾸로 감)"
            elif prev is not None:
                hours = (datetime.fromisoformat(t) - datetime.fromisoformat(prev[0])).total_seconds() / 3600
                if abs(f - prev[2]) > jump and hours <= jump_hours:
                    reason = f"{prev[2]:.0%} → {f:.0%} in {hours:.1f} h (급변)"
            if reason:
                out.append({"image_id": iid, "target_id": tid, "activity_id": act, "view_id": vid, "time": t,
                            "installed": round(f, 3), "reason": reason, "compare_with": best_id if f < best - back_tol
                            else (prev[1] if prev else None), "approved_draft_by_auto": author == "auto"})
            if f > best:
                best, best_id = f, iid
            prev = (t, iid, f)
    return sorted(out, key=lambda r: (r["target_id"], r["time"]))


def _rehome_anchors(items: list[dict], view_of: dict, view_photos: dict) -> list[dict]:
    """The anchor list is written by auto; if a view was trimmed / split since then, an anchor photo may no longer
    belong to its view. Replace it by the nearest photo of that view on the same day (or drop it; the next auto run
    picks anchors again)."""
    out, taken = [], {(x["image_id"], x["target_id"], x["activity_id"]) for x in items}
    for x in items:
        v = x.get("view_id")
        if view_of.get(x["image_id"]) == v:
            out.append(x)
            continue
        t = x.get("time") or ""
        same_day = [im for im in view_photos.get(v, []) if im["capture"]["time_local"][:10] == t[:10]
                    and (im["image_id"], x["target_id"], x["activity_id"]) not in taken]
        if not same_day:
            continue
        def gap(im):
            a, b = im["capture"]["time_local"], t
            return abs(_ts(a) - _ts(b))
        im = min(same_day, key=gap)
        taken.add((im["image_id"], x["target_id"], x["activity_id"]))
        out.append(dict(x, image_id=im["image_id"], time=im["capture"]["time_local"],
                        why=(x.get("why") or "") + f" (moved from {x['image_id']}: photo left the view)"))
    return out


def _ts(t: str) -> float:
    from datetime import datetime
    return datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").timestamp()


def build_todo(ctx: RunContext, images: list[dict] | None = None) -> dict:
    from .source_adapter import load_images
    store = ctx.store()
    images = images if images is not None else load_images(ctx)
    views = store_views(store)
    from .index_views import drift_acks, excluded_views
    excl = excluded_views(store)
    acks = drift_acks(store)
    # "checked, leave it as is" decisions made on the to-do page (key = "<section>|<item key>")
    todo_acks = {r["key"] for r in store.latest("todo_ack") if r["payload"].get("accepted")}
    # photos a person already decided to leave out (gap acks / trims); kept by photo id so renaming or splitting the
    # neighbouring views does not bring the same photos back
    acked_photos = {i for r in store.latest("todo_ack") if r["payload"].get("accepted") for i in r["payload"].get("image_ids") or []}
    active_views = [v for v in views if not v.get("retired") and v["view_id"] not in excl]
    photos_per_view = Counter()
    unassigned = []
    view_of, view_photos = {}, defaultdict(list)
    for im in images:
        if im.get("duplicate_of"):
            continue
        v = assign_view(im, views)
        if v["view_id"]:
            photos_per_view[v["view_id"]] += 1
            view_of[im["image_id"]] = v["view_id"]
            if im.get("status") == "ok" and (im.get("capture") or {}).get("time_local"):
                view_photos[v["view_id"]].append(im)
        elif im.get("status") == "ok" and (im.get("capture") or {}).get("time_local"):
            unassigned.append(im)
    targets = [r for r in store.latest("target") if r["payload"].get("identity_status") != "retired"]
    # a target whose every activity is approved not_applicable needs no ROI (nothing is measured there)
    _app = {}
    _ast = store.review_states("applicability")
    for r in store.latest("applicability"):
        tid = r["payload"]["target_id"]
        na = r["payload"]["applicability"] == "not_applicable" and _ast.get(r["key"], {}).get("status") == "approved"
        _app.setdefault(tid, []).append(na)
    roi_targets = [t for t in targets if not (_app.get(t["key"]) and all(_app[t["key"]]))]
    # target|view pairs the reviewer marked "not visible in this view" need no ROI
    roi_skips = {r["key"] for r in store.latest("roi_skip") if r["payload"].get("visible") is False}
    roi_states = store.review_states("roi")
    rois = {r["key"]: r for r in store.latest("roi")}
    sections = []

    def section(sid, title, items, how, level="action"):
        sections.append({"id": sid, "title": title, "count": len(items), "items": items, "how": how, "level": level})

    # 1. views
    rec = _read(ctx, "views/view_reconcile.json") or {}
    # proposals against a view a person already approved are reference only: the automatic boundary detector does not
    # know about the camera-position fixes (trim / split / reref) and would undo them; a view no one approved yet is a
    # real to-do
    v_states = store.review_states("view")
    props, fyi = [], []
    for pr in rec.get("proposals", []):
        (fyi if v_states.get(pr.get("view_id"), {}).get("status") == "approved" else props).append(pr)
    section("view_proposals", "구도(view) 경계 제안 확인", props,
            "제안이 맞으면 '수락+승인'. 자동 경계는 후보일 뿐이라 사람이 한 번 확인해야 합니다.")
    section("view_proposals_fyi", "자동 경계 제안 (참고 · 이미 승인한 구도)", fyi,
            "사람이 승인한 구도와 자동 감지 경계가 다른 곳입니다. 카메라 위치 수정(기간 빼기·나누기)을 자동 감지는 모르므로 "
            "보통 무시합니다. 정말 경계가 틀렸을 때만 '수락+승인'(그 구도의 라벨은 다시 승인해야 함).", level="info")
    unapproved = [{"view_id": v["view_id"], "start_time": v.get("start_time"), "end_time": v.get("end_time"),
                   "reference_image_id": v.get("reference_image_id"), "photos": photos_per_view.get(v["view_id"], 0),
                   "review": v["_review"]}
                  for v in active_views if v["_review"] != "approved" and photos_per_view.get(v["view_id"], 0)]
    section("views_unapproved", "구도(view) 승인", unapproved,
            "기준 사진(reference)이 이 구도를 대표하는지, 기간이 맞는지 보고 Approve view.")
    gaps = [g for g in unassigned_gaps(unassigned, [v for v in views if not v.get("retired")])
            if f"unassigned|{g['gap']}" not in todo_acks and not set(g["image_ids"]) <= acked_photos]
    section("unassigned_photos", "어느 구도에도 속하지 않은 사진 (구도 사이 빈 시간)", gaps,
            "보통 카메라를 옮기는 중에 찍힌 사진이라 학습에서 자동으로 빠집니다. 사진을 보고 쓸 수 없으면 '확인 (제외 유지)'. "
            "앞/뒤 구도와 같은 화면이면 그 구도의 기간을 늘리세요(열기 → Edit view).")
    # 1b. photos whose camera position differs from their view reference (stage `align`)
    al = _read(ctx, "view_align/align_report.json") or {}
    vver = {v["view_id"]: v["_version"] for v in views}
    vref = {v["view_id"]: v.get("reference_image_id") for v in views}
    mis = []
    for sg in al.get("segments", []):
        v = sg["view_id"]
        ack = f"view_misaligned|{v}|{sg['from']}|{sg['to']}|v{sg['view_version']}"
        if v in excl or vver.get(v) != sg["view_version"] or ack in todo_acks:
            continue                       # fixed (the view changed), excluded or acked; re-checked on the next auto
        mis.append(dict({k: sg[k] for k in ("view_id", "view_version", "kind", "suggest", "from", "to", "photos", "shifted",
                                            "median_shift", "example_image_id", "view_photos")},
                        reference_image_id=vref.get(v), ack_key=ack))
    section("view_misaligned", "카메라 위치가 다른 사진 (구도 안에 섞임)", mis,
            "왼쪽 = ROI를 그린 기준 사진, 오른쪽 = 같은 구도로 묶였지만 카메라가 움직인 사진(같은 ROI를 그려 봄). "
            "추천 버튼(초록)을 누르면: 기간에서 빼기 = 그 사진들은 학습에서 빠짐 / 새 구도로 나누기·기준 사진 바꾸기 = ROI를 새 위치로 "
            "옮긴 초안이 생김 → 아래 'ROI 승인' 칸에서 확인 후 Approve. 빛 때문에 잘못 잡힌 것이면 '확인 (그대로 둠)'.")
    # 2. ROIs per view x target (incl. doors / passages)
    roi_missing, roi_unapproved = [], []
    for v in active_views:
        if not photos_per_view.get(v["view_id"]):
            continue
        for t in roi_targets:
            key = f"{t['key']}|{v['view_id']}"
            if key in roi_skips and key not in rois:
                continue
            if key not in rois:
                others = sorted(k.split("|")[1] for k in rois if k.startswith(t["key"] + "|"))
                roi_missing.append({"roi": key, "view_id": v["view_id"], "target_id": t["key"],
                                    "reference_image_id": v.get("reference_image_id"),
                                    "copy_from_candidates": others, "photos": photos_per_view[v["view_id"]]})
            elif roi_states.get(key, {}).get("status") != "approved":
                roi_unapproved.append({"roi": key, "view_id": v["view_id"], "target_id": t["key"],
                                       "version": rois[key]["version"], "status": roi_states.get(key, {}).get("status"),
                                       "reference_image_id": rois[key]["payload"].get("reference_image_id")})
    section("roi_missing", "ROI 그리기 (새 구도)", roi_missing,
            "기준 사진에서 그리거나, 비슷한 구도에서 'Copy as drafts' 후 꼭짓점만 고치세요. "
            "문·창 같은 영구 개구부는 제외영역(Draw exclusion, 이유 door_opening/window), "
            "나중에 막을 임시 통로는 ROI에 포함합니다(막히기 전 = not_installed).")
    section("roi_unapproved", "ROI 승인", roi_unapproved,
            "ROI 범위와 문·통로 제외가 맞으면 Approve ROI. 라벨 검수 전에 ROI를 먼저 확정하세요.")
    # 2b. ROI sanity: ROIs of different targets in one view should not overlap much, and no ROI should carry a copy
    #     of another target's polygon (left over in the draft while switching targets)
    from .target_registry import roi_conflicts
    conflicts, seen = [], set()
    measured = {t["key"] for t in roi_targets}          # not-applicable targets (e.g. a floor) are never measured
    for key, r in rois.items():
        tid, vid = key.split("|")
        if vid in excl or tid not in measured:
            continue
        for c in roi_conflicts(store, vid, tid, r["payload"]["polygons"], r["payload"].get("exclusions"), min_overlap=0.10,
                               rois=list(rois.values())):
            if c["target_id"] not in measured:
                continue
            pair = (vid,) + tuple(sorted((tid, c["target_id"])))
            if pair in seen and not c["polygon_copied_from_it"]:
                continue
            seen.add(pair)
            okey = f"{c['target_id']}|{vid}"
            ack = f"roi_conflicts|{key}@v{r['version']}|{okey}@v{rois[okey]['version'] if okey in rois else 0}"
            if ack in todo_acks:
                continue
            conflicts.append({"roi": key, "view_id": vid, "target_id": tid, "other": c["target_id"],
                              "overlap_of_smaller": c["overlap_of_smaller"], "copied_polygon": c["polygon_copied_from_it"],
                              "reference_image_id": r["payload"].get("reference_image_id"), "ack_key": ack})
    section("roi_conflicts", "ROI 겹침 확인 (다른 대상과 겹치거나 다른 대상 폴리곤이 섞임)", conflicts,
            "copied_polygon=true 이면 그 ROI에 다른 벽의 폴리곤이 들어 있습니다: 열기 → Edit vertices → 해당 폴리곤 선택 후 Delete → "
            "Save → Approve. 단순 겹침이면 경계를 맞춰 주세요(천장-벽 경계의 몇 % 겹침은 괜찮습니다).")
    # 2c. ROI audit: problems found by looking at every view (stages/roi_audit/findings.json) plus automatic checks.
    #     An item disappears by itself once one of its ROIs is saved again, its view is excluded, or it is acked.
    audit = []
    for f in (_read(ctx, "roi_audit/findings.json") or {}).get("findings", []):
        vids = {k.split("|")[1] for k in f.get("rois", {})} or {f.get("view_id")}
        if vids <= set(excl) or f"roi_audit|{f['id']}" in todo_acks:
            continue
        if any(k in rois and rois[k]["version"] != v for k, v in f.get("rois", {}).items()):
            continue
        audit.append(dict(f, ack_key=f"roi_audit|{f['id']}"))
    for key in sorted(roi_skips & set(rois)):
        tid, vid = key.split("|")
        if vid in excl or tid not in measured:
            continue
        ack = f"roi_audit|skip_and_roi|{key}@v{rois[key]['version']}"
        if ack in todo_acks:
            continue
        audit.append({"id": f"skip_and_roi|{key}", "view_id": vid, "severity": "fix", "rois": {key: rois[key]["version"]},
                      "title": f"{key}: '안 보임'으로 표시했는데 ROI도 있음",
                      "detail": "둘 중 하나만 맞습니다. 이 벽이 보이면 ROI를 확인하고 '확인 (그대로 둠)', 안 보이면 ROI를 지우거나 고치세요.",
                      "images": [{"image_id": rois[key]["payload"].get("reference_image_id"), "view_id": vid, "label": "기준 사진"}],
                      "open_target": tid, "ack_key": ack})
    section("roi_audit", "ROI 검수 결과 (고칠 것 / 확인할 것)", audit,
            "그림의 색 선 = 지금 저장된 ROI. 고칠 것은 '열기'에서 고친 뒤 Save → Approve 하면 목록에서 자동으로 빠집니다. "
            "확인만 할 것은 사진을 보고 괜찮으면 '확인 (그대로 둠)'.")
    drift = _read(ctx, "drift/drift_report.json") or {}
    redraw = [r for r in drift.get("redraw_requests", [])
              if r.get("view_id") not in excl and (r.get("view_id"), r.get("from_day")) not in acks]
    section("drift_redraw", "카메라가 밀린 날: ROI 다시 그려주세요", redraw,
            "그 날짜부터 새 구도를 만들고(그 날 사진을 기준으로) ROI를 다시 그리거나 복사해서 고치세요.")
    shakes = [x for x in drift.get("severe_shakes", []) if x.get("view_id") not in excl and (x.get("view_id"), x.get("day")) not in acks]
    if shakes:
        section("severe_shakes", "심한 흔들림 보고", shakes,
                "참고용(할 일 아님). 학습 제외한 구도와 'ROI 그대로 써도 됨'으로 처리한 날은 뺐습니다.", level="info")
    if excl:
        section("views_excluded", "학습에서 제외한 구도", [{"view_id": k, **v} for k, v in sorted(excl.items())],
                "이 구도의 사진은 표본·라벨 초안·학습 export에서 빠집니다.", level="info")
    # 3. labels that need re-approval because their ROI was edited
    rebase = rebase_candidates(store)
    section("roi_changed_relabel", "ROI가 바뀌어 다시 승인할 라벨",
            [{"roi": k, "labels": len(v), "previously_approved": sum(1 for x in v if x["was_approved"])} for k, v in rebase.items()],
            "To-do 화면에서 썸네일을 보고 '새 ROI로 옮겨 다시 승인'. 경계 근처가 달라진 사진만 열어서 고치면 됩니다.")
    # 3b. sanity check of APPROVED labels: installation should not go backwards in time, and should not
    #     jump a lot within a few hours (usually a copied label that no longer matches the photo)
    lc = []
    for x in label_consistency(store, images):
        x["ack_key"] = f"label_consistency|{x['image_id']}|{x['target_id']}|{x['activity_id']}"
        if x["ack_key"] not in todo_acks:
            lc.append(x)
    section("label_consistency", "승인된 라벨 중 확인이 필요한 것 (설치율이 거꾸로 가거나 몇 시간 만에 크게 바뀜)", lc,
            "사진을 크게 보고 맞으면 '확인 (그대로 둠)'. 틀렸으면 '열기'에서 영역을 고쳐 Save → Approve 하면 됩니다.")
    # 4. drafts waiting for review
    states = store.review_states("state_label")
    by_basis = Counter()
    for r in store.latest("state_label"):
        st = states.get(r["key"], {}).get("status")
        if st == "unreviewed" and r["source"] != "manual":
            by_basis[r["payload"].get("draft_basis") or r["source"]] += 1
    order = {"stable_copy": 0, "stable_whole_roi_fill": 1, "change_period_copy_needs_editing": 2}
    section("drafts_to_review", "자동 초안 검수",
            [{"kind": k, "drafts": n} for k, n in sorted(by_basis.items(), key=lambda kv: order.get(kv[0], 9))],
            "왼쪽 필터 'samples' + 'draft' → Grid view에서 훑어보고 맞으면 선택 후 승인. "
            "change_period_* 는 사진을 열어 경계를 고친 뒤 승인.")
    drafts = _read(ctx, "samples/drafts_report.json") or {}
    # the report is written by 'auto'; drop what has been labelled since then, so the list shrinks while working
    lstates = store.review_states("state_label")
    have_label = {r["key"]: lstates.get(r["key"], {}).get("status", "unreviewed") for r in store.latest("state_label")}
    surface = {t["key"]: t["payload"].get("surface_type") for t in targets}
    need_all = [x for x in drafts.get("needs_drawing", []) if x.get("view_id") not in excl]
    need_all = _rehome_anchors(need_all, view_of, view_photos)
    need = [dict(x, surface_type=surface.get(x["target_id"]),
                 label=have_label.get(f"{x['image_id']}|{x['target_id']}|{x['activity_id']}")) for x in need_all
            if have_label.get(f"{x['image_id']}|{x['target_id']}|{x['activity_id']}") != "approved"]
    done_by_view = Counter(x["view_id"] for x in need_all) - Counter(x["view_id"] for x in need)
    for x in need:
        x["view_done"] = done_by_view.get(x["view_id"], 0)
    # approved anchors stay visible (with their label) next to the remaining ones, so the next photo can be compared
    # with the one already drawn; they are not counted as work
    # (the anchor list is rebuilt by auto without the photos that have labels, so take them from the store: per view
    # with open anchors, the approved photos of the wanted targets, at most 3 photos per view)
    done = []
    open_views = {x["view_id"] for x in need}
    want = defaultdict(set)
    for x in need:
        want[x["view_id"]].add((x["target_id"], x["activity_id"]))
    by_img = {im["image_id"]: im for im in images}
    per_view = defaultdict(dict)
    for r in store.latest("state_label"):
        if have_label.get(r["key"]) != "approved":
            continue
        iid, tid, act = r["key"].split("|")
        im = by_img.get(iid)
        if im is None:
            continue
        v = assign_view(im, views)["view_id"]
        if v in open_views and (tid, act) in want[v]:
            per_view[v].setdefault(iid, []).append((tid, act, r["payload"], im["capture"]["time_local"]))
    for v, photos in per_view.items():
        for iid, labs in sorted(photos.items(), key=lambda kv: kv[1][0][3])[:3]:
            for tid, act, lp, t in labs:
                done.append({"image_id": iid, "target_id": tid, "activity_id": act, "view_id": v, "time": t,
                             "surface_type": surface.get(tid), "done": True,
                             "label_preview": {"fill": lp.get("fill_roi_state"),
                                               "regions": [{"state": q.get("state"), "polygons": q.get("polygons")} for q in lp.get("regions") or []],
                                               "occluded": [o.get("polygons") for o in lp.get("occluded") or []]}})
    section("needs_drawing", "직접 그릴 기준(앵커) 사진", need[:600] + done[:300],
            "구도마다 처음·중간·마지막 날 사진을 한 장씩 직접 그립니다(그 사이 날짜는 auto가 이 라벨을 보고 초안을 만듭니다). "
            "저장한 사진은 여기서 바로 빠집니다. 앞에서 승인한 사진과 상태가 같으면 그 구도의 '앞 사진과 같음 → 복사+승인'을 쓰세요. "
            f"다 하면 auto를 다시 돌리면 나머지 표본({drafts.get('no_basis_total', 0)}건)에 초안이 생깁니다.")
    # 5. registry
    t_states, a_states = store.review_states("target"), store.review_states("applicability")
    reg = [{"kind": "target", "key": t["key"], "status": t_states.get(t["key"], {}).get("status")}
           for t in targets if t_states.get(t["key"], {}).get("status") != "approved"]
    reg += [{"kind": "applicability", "key": r["key"], "status": a_states.get(r["key"], {}).get("status"),
             "applicability": r["payload"]["applicability"]} for r in store.latest("applicability")
            if a_states.get(r["key"], {}).get("status") != "approved" and r["key"].split("|")[0] in {t["key"] for t in targets}]
    section("registry", "대상/공정 적용 여부 승인", reg, "대상 이름·면 종류와 공정 적용 여부를 확인하고 Approve.")
    notes = ctx.site.get("review_notes", [])
    if notes:
        section("notes", "확인 메모", notes, "예전에 발견된 확인 사항입니다.", level="info")
    for sec in sections:
        if sec["id"] == "needs_drawing":
            sec["count"] = sum(1 for x in sec["items"] if not x.get("done"))
    out = {"run_id": ctx.info["run_id"], "sections": sections,
           "total_actions": sum(s["count"] for s in sections if s["level"] == "action"),
           "order_note": "위에서부터 순서대로: 구도 → ROI → 라벨. ROI를 먼저 확정해야 라벨 승인이 무효화되지 않습니다."}
    return out


def write_todo_markdown(ctx: RunContext, todo: dict) -> str:
    lines = [f"# 유난님 할 일 ({todo['run_id']})", "", todo["order_note"], "",
             "검수 화면: `vp review --run <이 폴더>` 실행 후 브라우저에서 http://127.0.0.1:8765/todo", ""]
    n = 0
    for s in todo["sections"]:
        if not s["count"]:
            continue
        n += 1
        lines.append(f"## {n}. {s['title']} — {s['count']}건" + (" (참고)" if s["level"] == "info" else ""))
        lines.append("")
        lines.append(s["how"])
        lines.append("")
        for it in s["items"][:20]:
            if isinstance(it, dict):
                lines.append("- " + ", ".join(f"{k}={v}" for k, v in it.items() if not isinstance(v, (list, dict))))
            else:
                lines.append(f"- {it}")
        if s["count"] > 20:
            lines.append(f"- … 외 {s['count'] - 20}건 (To-do 화면 참고)")
        lines.append("")
    if n == 0:
        lines.append("지금은 사람이 할 일이 없습니다.")
    path = ctx.run_dir / "HUMAN_TODO.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    atomic_write_json(ctx.run_dir / "human_todo.json", todo)
    return str(path)
