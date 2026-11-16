"""Prepare canonical API drafts only. No credentials, HTTP calls, uploads or approvals."""
import json
from urllib.parse import quote
from uuid import UUID, NAMESPACE_URL, uuid5


def observation_draft(site_code, view_key, photo, target, row):
    required = {"schedule_id": target.get("schedule_id"), "task_id": target.get("task_id"),
                "daily_record_id": photo.get("daily_record_id")}
    missing = [key for key, value in required.items() if not value]
    if missing:
        return {"ready": False, "reason": "missing_backend_ids", "missing": missing}
    ids = {key: str(UUID(value)) for key, value in required.items()}
    revision = photo.get("revision", 1)
    if type(revision) is not int or not 1 <= revision <= 100000:
        raise ValueError("revision must be an integer between 1 and 100000")
    if row["status"] not in ("observed", "held", "failed", "unobserved"):
        raise ValueError("Invalid observation status")
    ratio = row["installed_ratio"]
    if (row["status"] == "observed") != (ratio is not None):
        raise ValueError("Only observed rows may have a ratio")
    if ratio is not None and not 0 <= ratio <= 1:
        raise ValueError("installed_ratio must be a fraction, not a percent")
    if row["status"] != "observed" and not row["hold_reason"]:
        raise ValueError("Held / failed observations require a reason")
    # Idempotent for the same source and revision. Changed model / ROI / policy needs a new revision.
    identity = json.dumps([site_code, ids, target["target_key"], view_key, revision], sort_keys=True)
    body = {"id": str(uuid5(NAMESPACE_URL, "final-vision:" + identity)),
            "daily_record_id": ids["daily_record_id"], "target_key": target["target_key"],
            "view_key": view_key, "revision": revision, "status": row["status"],
            "installed_ratio": ratio, "hold_reason": row["hold_reason"], "overlay_file_id": None}
    return {"ready": False, "reason": "upload_overlay_and_bind_file_id_before_sending",
            "method": "POST", "path": f"/api/v1/sites/{quote(site_code, safe='')}/schedules/"
            f"{ids['schedule_id']}/tasks/{ids['task_id']}/observations", "body": body}
