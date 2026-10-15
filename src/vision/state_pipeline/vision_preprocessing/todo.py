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


def build_todo(ctx: RunContext, images: list[dict] | None = None) -> dict:
    from .source_adapter import load_images
    store = ctx.store()
    images = images if images is not None else load_images(ctx)
    views = store_views(store)
    active_views = [v for v in views if not v.get("retired")]
    photos_per_view = Counter()
    unassigned = 0
    for im in images:
        if im.get("duplicate_of"):
            continue
        v = assign_view(im, views)
        if v["view_id"]:
            photos_per_view[v["view_id"]] += 1
        else:
            unassigned += 1
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
    section("view_proposals", "구도(view) 경계 제안 확인", rec.get("proposals", []),
            "제안이 맞으면 '수락+승인'. 자동 경계는 후보일 뿐이라 사람이 한 번 확인해야 합니다.")
    unapproved = [{"view_id": v["view_id"], "start_time": v.get("start_time"), "end_time": v.get("end_time"),
                   "reference_image_id": v.get("reference_image_id"), "photos": photos_per_view.get(v["view_id"], 0),
                   "review": v["_review"]}
                  for v in active_views if v["_review"] != "approved" and photos_per_view.get(v["view_id"], 0)]
    section("views_unapproved", "구도(view) 승인", unapproved,
            "기준 사진(reference)이 이 구도를 대표하는지, 기간이 맞는지 보고 Approve view.")
    if unassigned:
        section("unassigned_photos", "어느 구도에도 속하지 않은 사진", [{"photos": unassigned}],
                "구도 경계 제안을 수락하면 대부분 해결됩니다. 남으면 그 사진으로 새 구도를 만드세요.")
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
    drift = _read(ctx, "drift/drift_report.json") or {}
    section("drift_redraw", "카메라가 밀린 날: ROI 다시 그려주세요", drift.get("redraw_requests", []),
            "그 날짜부터 새 구도를 만들고(그 날 사진을 기준으로) ROI를 다시 그리거나 복사해서 고치세요.")
    if drift.get("severe_shakes"):
        section("severe_shakes", "심한 흔들림 보고", drift["severe_shakes"], "참고용. 계속 반복되면 알려주세요.", level="info")
    # 3. labels that need re-approval because their ROI was edited
    rebase = rebase_candidates(store)
    section("roi_changed_relabel", "ROI가 바뀌어 다시 승인할 라벨",
            [{"roi": k, "labels": len(v), "previously_approved": sum(1 for x in v if x["was_approved"])} for k, v in rebase.items()],
            "To-do 화면에서 썸네일을 보고 '새 ROI로 옮겨 다시 승인'. 경계 근처가 달라진 사진만 열어서 고치면 됩니다.")
    # 3b. sanity check of APPROVED labels: installation should not go backwards in time, and should not
    #     jump a lot within a few hours (usually a copied label that no longer matches the photo)
    section("label_consistency", "승인된 라벨 중 확인이 필요한 것 (설치율이 거꾸로 가거나 몇 시간 만에 크게 바뀜)",
            label_consistency(store, images),
            "사진을 크게 보고 맞으면 그대로 두세요. 틀렸으면 '열기'에서 영역을 고쳐 Save → Approve 하면 됩니다.")
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
    need = drafts.get("needs_drawing", [])
    section("needs_drawing", "직접 그릴 기준(앵커) 사진", need[:300],
            "승인된 라벨이 근처에 없어 초안을 못 만든 곳입니다. 이 사진들만 installed / not_installed 를 그리고 승인한 뒤 "
            f"auto를 다시 돌리면 나머지 표본({drafts.get('no_basis_total', 0)}건)에 초안이 생깁니다.")
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
