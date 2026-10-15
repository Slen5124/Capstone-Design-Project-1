"""Persistent review store: versioned records, human approvals, invalidation.

* Every change of a record creates a new version (append-only history).
* An approval is bound to (kind, key, version, content_hash) **and** to the exact
  versions of the records it depends on (e.g. a state label depends on the ROI,
  target, view and applicability versions it was drawn against).
* Saving a new version invalidates the record's own active approval and every
  active approval that depends on the previous version.
* Automatic writers can never overwrite a record whose latest version was
  written manually (``ManualRecordProtected``).
* Approvals require a non-empty human reviewer name; system identities are
  refused. Code paths in this package never call ``decide`` on their own.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schemas import canonical_json, content_hash

SYSTEM_ACTORS = {"", "auto", "system", "config", "pipeline", "model", "claude", "assistant"}


class StoreError(ValueError):
    pass


class ManualRecordProtected(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  kind TEXT NOT NULL, key TEXT NOT NULL, version INTEGER NOT NULL,
  payload TEXT NOT NULL, content_hash TEXT NOT NULL, source TEXT NOT NULL,
  author TEXT NOT NULL, created_at TEXT NOT NULL, deps TEXT NOT NULL, note TEXT,
  PRIMARY KEY (kind, key, version));
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT NOT NULL,
  version INTEGER NOT NULL, content_hash TEXT NOT NULL, deps TEXT NOT NULL,
  decision TEXT NOT NULL, reviewer TEXT NOT NULL, decided_at TEXT NOT NULL,
  status TEXT NOT NULL, invalidated_at TEXT, invalidated_reason TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS approvals_key ON approvals(kind, key);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, kind TEXT, key TEXT, version INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

STORE_SCHEMA_VERSION = "review_store/1"


class ReviewStore:
    def __init__(self, path: Path | str, site_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._batch = 0
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=TRUNCATE")  # no journal deletion: works on restricted/mounted folders
            self._db.executescript(_SCHEMA)
            row = self._db.execute("SELECT value FROM meta WHERE name='schema_version'").fetchone()
            if row is None:
                self._db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (STORE_SCHEMA_VERSION,))
                if site_id:
                    self._db.execute("INSERT INTO meta VALUES ('site_id', ?)", (site_id,))
                self._db.commit()
            elif row["value"] != STORE_SCHEMA_VERSION:
                raise StoreError(f"unsupported store schema {row['value']}")
            stored_site = self._db.execute("SELECT value FROM meta WHERE name='site_id'").fetchone()
            if site_id and stored_site and stored_site["value"] != site_id:
                raise StoreError(f"store belongs to site {stored_site['value']}, not {site_id}")

    def close(self):
        with self._lock:
            self._db.close()

    def _commit(self):
        if not self._batch:
            self._db.commit()

    @contextlib.contextmanager
    def transaction(self):
        """Group many writes into one commit (bulk draft creation). Rolls back on error."""
        with self._lock:
            self._batch += 1
            try:
                yield self
            except BaseException:
                self._batch -= 1
                if not self._batch:
                    self._db.rollback()
                raise
            self._batch -= 1
            if not self._batch:
                self._db.commit()

    # ------------------------------------------------------------- records
    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        return {"kind": row["kind"], "key": row["key"], "version": row["version"],
                "payload": json.loads(row["payload"]), "content_hash": row["content_hash"],
                "source": row["source"], "author": row["author"], "created_at": row["created_at"],
                "deps": json.loads(row["deps"]), "note": row["note"]}

    def get(self, kind: str, key: str, version: int | None = None) -> dict | None:
        with self._lock:
            if version is None:
                row = self._db.execute("SELECT * FROM records WHERE kind=? AND key=? ORDER BY version DESC LIMIT 1",
                                       (kind, key)).fetchone()
            else:
                row = self._db.execute("SELECT * FROM records WHERE kind=? AND key=? AND version=?",
                                       (kind, key, version)).fetchone()
        return self._row(row)

    def current_version(self, kind: str, key: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(version) v FROM records WHERE kind=? AND key=?", (kind, key)).fetchone()
        return row["v"] or 0

    def history(self, kind: str, key: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM records WHERE kind=? AND key=? ORDER BY version", (kind, key)).fetchall()
        return [self._row(r) for r in rows]

    def latest(self, kind: str, prefix: str | None = None) -> list[dict]:
        sql = ("SELECT r.* FROM records r JOIN (SELECT kind, key, MAX(version) v FROM records WHERE kind=? "
               "GROUP BY kind, key) m ON r.kind=m.kind AND r.key=m.key AND r.version=m.v")
        args: list[Any] = [kind]
        if prefix is not None:
            sql += " WHERE r.key LIKE ? ESCAPE '\\'"
            args.append(prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY r.key"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def put(self, kind: str, key: str, payload: dict, *, source: str, author: str,
            deps: Iterable[Iterable] = (), note: str | None = None) -> dict:
        """Write a new version if content or dependencies changed.

        Returns {"version", "changed", "invalidated": [approval ids]}.
        """
        if not author:
            raise StoreError("author is required")
        deps_list = [[d[0], d[1], int(d[2])] for d in deps]
        h = content_hash(payload)
        with self._lock:
            current = self.get(kind, key)
            if current and current["content_hash"] == h and current["deps"] == deps_list:
                return {"version": current["version"], "changed": False, "invalidated": []}
            if current and current["source"] == "manual" and source != "manual":
                raise ManualRecordProtected(f"{kind}/{key} v{current['version']} is manual; automatic source "
                                            f"{source!r} may not overwrite it")
            version = (current["version"] if current else 0) + 1
            now = utc_now()
            self._db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (kind, key, version, canonical_json(payload), h, source, author, now,
                              json.dumps(deps_list), note))
            invalidated = []
            if current:
                invalidated = self._invalidate(kind, key, current["version"], version, now)
            self._event(author, "put", kind, key, version,
                        {"source": source, "previous_version": current["version"] if current else None,
                         "invalidated_approvals": invalidated})
            self._commit()
        return {"version": version, "changed": True, "invalidated": invalidated}

    def _invalidate(self, kind, key, old_version, new_version, now) -> list[int]:
        ids = []
        # candidate rows only (the LIKE is a superset filter; the exact check is done below)
        pattern = "%" + json.dumps([kind, key])[1:-1] + ",%"
        rows = self._db.execute("SELECT * FROM approvals WHERE status='active' AND ((kind=? AND key=?) OR deps LIKE ?)",
                                (kind, key, pattern)).fetchall()
        for row in rows:
            reason = None
            if row["kind"] == kind and row["key"] == key:
                reason = f"record_changed:v{row['version']}->v{new_version}"
            else:
                for dep in json.loads(row["deps"]):
                    if dep[0] == kind and dep[1] == key and int(dep[2]) == old_version:
                        reason = f"dependency_changed:{kind}/{key}:v{old_version}->v{new_version}"
                        break
            if reason:
                self._db.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                                 "WHERE id=?", (now, reason, row["id"]))
                ids.append(row["id"])
        return ids

    # ----------------------------------------------------------- approvals
    def decide(self, kind: str, key: str, version: int, reviewer: str, decision: str = "approved",
               note: str | None = None) -> dict:
        reviewer = (reviewer or "").strip()
        if reviewer.casefold() in SYSTEM_ACTORS:
            raise StoreError("a named human reviewer is required for approval/rejection")
        if decision not in ("approved", "rejected"):
            raise StoreError("decision must be approved or rejected")
        with self._lock:
            record = self.get(kind, key)
            if record is None:
                raise StoreError(f"{kind}/{key} does not exist")
            if record["version"] != int(version):
                raise StoreError(f"{kind}/{key}: version {version} is not current (v{record['version']})")
            stale = [d for d in record["deps"] if self.current_version(d[0], d[1]) != int(d[2])]
            if stale:
                raise StoreError(f"{kind}/{key}: drawn against outdated dependencies {stale}; re-save first")
            now = utc_now()
            self._db.execute("UPDATE approvals SET status='superseded' WHERE kind=? AND key=? AND status='active'",
                             (kind, key))
            cursor = self._db.execute(
                "INSERT INTO approvals (kind,key,version,content_hash,deps,decision,reviewer,decided_at,status,note) "
                "VALUES (?,?,?,?,?,?,?,?, 'active', ?)",
                (kind, key, record["version"], record["content_hash"], json.dumps(record["deps"]),
                 decision, reviewer, now, note))
            self._event(reviewer, decision, kind, key, record["version"], {"note": note})
            self._commit()
            return {"approval_id": cursor.lastrowid, "decision": decision, "version": record["version"]}

    def review_state(self, kind: str, key: str) -> dict:
        """Effective review state of the current version.

        status: approved | rejected | unreviewed | invalidated | missing
        """
        with self._lock:
            record = self.get(kind, key)
            if record is None:
                return {"status": "missing", "version": None, "approval": None}
            row = self._db.execute(
                "SELECT * FROM approvals WHERE kind=? AND key=? AND version=? ORDER BY id DESC LIMIT 1",
                (kind, key, record["version"])).fetchone()
            older = self._db.execute(
                "SELECT * FROM approvals WHERE kind=? AND key=? AND status='invalidated' ORDER BY id DESC LIMIT 1",
                (kind, key)).fetchone()
        if row is None or row["status"] != "active":
            if row is not None and row["status"] == "invalidated":
                return {"status": "invalidated", "version": record["version"],
                        "approval": dict(row), "reason": row["invalidated_reason"]}
            if older is not None:
                return {"status": "invalidated", "version": record["version"], "approval": dict(older),
                        "reason": older["invalidated_reason"]}
            return {"status": "unreviewed", "version": record["version"], "approval": None}
        if row["content_hash"] != record["content_hash"]:
            return {"status": "invalidated", "version": record["version"], "approval": dict(row),
                    "reason": "content_hash_mismatch"}
        for dep in json.loads(row["deps"]):
            if self.current_version(dep[0], dep[1]) != int(dep[2]):
                return {"status": "invalidated", "version": record["version"], "approval": dict(row),
                        "reason": f"dependency_outdated:{dep[0]}/{dep[1]}"}
        return {"status": row["decision"], "version": record["version"], "approval": dict(row)}

    def review_states(self, kind: str) -> dict[str, dict]:
        """review_state() for every latest record of a kind in a few queries (large lists in the UI)."""
        with self._lock:
            latest = {r["key"]: r for r in self._db.execute(
                "SELECT r.key, r.version, r.content_hash FROM records r JOIN (SELECT key, MAX(version) v FROM records "
                "WHERE kind=? GROUP BY key) m ON r.key=m.key AND r.version=m.v WHERE r.kind=?", (kind, kind))}
            rows = self._db.execute("SELECT * FROM approvals WHERE kind=? ORDER BY id", (kind,)).fetchall()
            cur = {(r["kind"], r["key"]): r["v"] for r in self._db.execute(
                "SELECT kind, key, MAX(version) v FROM records GROUP BY kind, key")}
        by_version, invalidated = {}, {}
        for row in rows:
            if row["key"] in latest and row["version"] == latest[row["key"]]["version"]:
                by_version[row["key"]] = row
            if row["status"] == "invalidated":
                invalidated[row["key"]] = row
        out = {}
        for key, rec in latest.items():
            row, older = by_version.get(key), invalidated.get(key)
            if row is None or row["status"] != "active":
                if row is not None and row["status"] == "invalidated":
                    out[key] = {"status": "invalidated", "version": rec["version"], "reason": row["invalidated_reason"]}
                elif older is not None:
                    out[key] = {"status": "invalidated", "version": rec["version"], "reason": older["invalidated_reason"]}
                else:
                    out[key] = {"status": "unreviewed", "version": rec["version"]}
                continue
            if row["content_hash"] != rec["content_hash"]:
                out[key] = {"status": "invalidated", "version": rec["version"], "reason": "content_hash_mismatch"}
                continue
            bad = next((d for d in json.loads(row["deps"]) if cur.get((d[0], d[1])) != int(d[2])), None)
            if bad:
                out[key] = {"status": "invalidated", "version": rec["version"],
                            "reason": f"dependency_outdated:{bad[0]}/{bad[1]}"}
            else:
                out[key] = {"status": row["decision"], "version": rec["version"], "reviewer": row["reviewer"]}
        return out

    def approvals(self, kind: str | None = None, key: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM approvals WHERE 1=1", []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if key:
            sql += " AND key=?"; args.append(key)
        with self._lock:
            return [dict(r) for r in self._db.execute(sql + " ORDER BY id", args).fetchall()]

    # -------------------------------------------------------------- events
    def _event(self, actor, action, kind, key, version, detail):
        self._db.execute("INSERT INTO events (ts,actor,action,kind,key,version,detail) VALUES (?,?,?,?,?,?,?)",
                         (utc_now(), actor, action, kind, key, version, json.dumps(detail, ensure_ascii=False)))

    def log(self, actor: str, action: str, detail: dict):
        with self._lock:
            self._event(actor, action, None, None, None, detail)
            self._commit()

    def events(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def snapshot(self) -> dict:
        with self._lock:
            kinds = [r["kind"] for r in self._db.execute("SELECT DISTINCT kind FROM records")]
        out = {"store_schema": STORE_SCHEMA_VERSION, "created_utc": utc_now(), "records": {}, "approvals": self.approvals()}
        for kind in kinds:
            out["records"][kind] = [dict(r, review=self.review_state(kind, r["key"])) for r in self.latest(kind)]
        return out

    def counts(self) -> dict:
        out = {}
        for kind in ("view", "target", "applicability", "roi", "observation", "state_label"):
            states: dict[str, int] = {}
            for st in self.review_states(kind).values():
                states[st["status"]] = states.get(st["status"], 0) + 1
            out[kind] = states
        return out
