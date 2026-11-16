from pathlib import Path
import sys
from uuid import uuid4

import httpx
from pypdf import PdfReader

from aloha_fixture import Fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/llm_agent_module/weekly_reports"))
from project_worker import ProjectClient, run_job


def test_weekly_extension_is_opt_in(monkeypatch):
    monkeypatch.delenv("ALO_ENABLE_WEEKLY_REPORTS", raising=False)
    f = Fixture()
    try:
        paths = f.app.openapi()["paths"]
        assert "/api/v1/sites/{site_code}/schedules/{schedule_id}/reports" in paths
        assert all(not path.startswith("/api/v1/weekly/") for path in paths)
    finally:
        f.close()


def test_weekly_worker_generates_pdf_without_consuming_legacy_job(monkeypatch, tmp_path):
    monkeypatch.setenv("ALO_ENABLE_WEEKLY_REPORTS", "1")
    f = Fixture()
    try:
        week = {"week_start": str(f.start)}
        legacy = f.request("POST", f.base + "/reports", {"id": str(uuid4()), **week})
        assert legacy.status_code == 201, legacy.text
        reports = f.base.replace("/api/v1/", "/api/v1/weekly/", 1) + "/reports"
        draft = f.request("POST", reports, {"id": str(uuid4()), "mode": "draft", **week})
        assert draft.status_code == 201, draft.text

        def transport(request):
            result = f.client.request(request.method, request.url.raw_path.decode(),
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(result.status_code, headers=dict(result.headers), content=result.content)

        client = ProjectClient(token=f.tokens["agent"], transport=httpx.MockTransport(transport))
        result = run_job(client, f.code, draft.json(), tmp_path)
        assert result["status"] == "validated"
        pdf = PdfReader(tmp_path / draft.json()["id"] / "report.pdf")
        assert any("Drywall installation" in page.extract_text() for page in pdf.pages)
        old_jobs = f.request("GET", f"/api/v1/sites/{f.code}/report-jobs", actor="agent").json()
        assert [job["id"] for job in old_jobs["items"]] == [legacy.json()["id"]]
    finally:
        f.close()
