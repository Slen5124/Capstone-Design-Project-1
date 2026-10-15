"""Bridge to the existing qa_v3 / qa_v2 modules (imported, never modified)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("qa_v3", "qa_v2"):
    p = str(_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from dataset_io import ExportDataset  # noqa: E402  (qa_v3)

# Heavy evidence fields are dropped from memory; counts are kept for provenance.
_HEAVY = ("transfer_evidence", "coverage", "actions", "attempted_references")


class SiteExportDataset(ExportDataset):
    """qa_v3 ExportDataset restricted to one site, with explicit categories.

    Reuses ExportDataset's ZIP handle management (``archive``) and item reader
    (``__getitem__``). The base constructor would keep all 36k full manifest
    records (~10 KB of transfer evidence each) in memory, so records are
    streamed, filtered by site and slimmed here. Paths are translated with the
    caller's path map so a manifest written on Windows can be read elsewhere.
    Records without ``annotation_output`` are kept (flagged later) instead of
    being silently dropped as in the base class.
    """

    def __init__(self, run_dir, categories, photo_zip_contains, path_mapper=lambda p: p,
                 password=None, verify_hashes=True):
        self.run_dir = Path(run_dir)
        self.verify_hashes = verify_hashes
        self.handles = {}
        import os
        self.password = password if password is not None else os.environ.get("QA_ZIP_PASSWORD")
        self.records = []
        self.skipped_categories: dict[str, int] = {}
        with (self.run_dir / "manifest.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                photo = record.get("source_photo") or {}
                if photo_zip_contains not in (photo.get("zip") or ""):
                    continue
                if record.get("classification") not in categories:
                    key = record.get("classification")
                    self.skipped_categories[key] = self.skipped_categories.get(key, 0) + 1
                    continue
                slim = {k: v for k, v in record.items() if k not in _HEAVY}
                slim["evidence_counts"] = {k: len(record.get(k) or []) for k in _HEAVY}
                slim["unresolved_codes"] = sorted({u.get("code") if isinstance(u, dict) else str(u)
                                                   for u in record.get("unresolved") or []})
                slim.pop("unresolved", None)
                slim["source_photo"] = dict(photo, zip=path_mapper(photo["zip"]))
                out = record.get("annotation_output")
                if out:
                    out = dict(out)
                    if "zip" in out:
                        out["zip"] = path_mapper(out["zip"])
                    if "path" in out:
                        out["path"] = path_mapper(out["path"])
                    slim["annotation_output"] = out
                self.records.append(slim)


def project_regions(*args, **kwargs):
    """qa_v2.registration.project_regions (per-region SIFT/RANSAC homography)."""
    from registration import project_regions as _project  # qa_v2
    return _project(*args, **kwargs)
