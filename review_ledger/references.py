"""Frozen, untrusted external review context, never observation or lesson evidence.

Capture time means locally available at that time. Reported source timestamps do
not establish that captured text existed at any earlier time or is true now.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .models import LedgerError, choice, digest, fields, integer, sha, text
from .storage import Store, new_id, now

MAX_BODY_CHARS = 8000
REFERENCE_FIELDS = {"finding_id", "provider", "event_type", "external_id", "url", "body",
                    "origin_at", "source_updated_at", "source_revision"}
REQUIRED_REFERENCE_FIELDS = REFERENCE_FIELDS - {"source_updated_at", "source_revision"}
CONTENT_FIELDS = ("provider", "event_type", "external_id", "url", "body", "origin_at",
                  "source_updated_at", "source_revision")
METADATA_COLUMNS = ("id", "repository_id", "review_id", "run_id", "finding_id", "provider",
                    "event_type", "external_id", "url", "source_revision", "origin_at",
                    "source_updated_at", "captured_at", "body_chars", "body_bytes",
                    "content_sha256", "version", "provenance", "valid", "invalid_reason",
                    "invalidated_at")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)")
_EXTERNAL_ID = re.compile(r"[1-9][0-9]{0,18}")
_ANCHORS = {"review": "pullrequestreview-", "issue_comment": "issuecomment-",
            "review_comment": "discussion_r"}


def normalize_timestamp(value, field="as_of"):
    """Validate offset-aware RFC3339, preserving instants as UTC microseconds."""
    text(value, field, 40)
    if not _TIMESTAMP.fullmatch(value) or value.endswith("-00:00"):
        raise LedgerError("invalid_input", f"{field} requires an explicit RFC3339 timezone and at most six fractional digits")
    try:
        parsed = datetime.fromisoformat(value.replace("t", "T").replace("z", "Z"))
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError) as exc:
        raise LedgerError("invalid_input", f"{field} must be a valid RFC3339 timestamp") from exc


class References:
    """One scoped versioned row per distinct reported snapshot; no remote fetches."""

    @staticmethod
    def _review(conn, scope, run_id):
        Store.repository(conn, scope)
        return Store.run(conn, scope, run_id)["review_id"]

    @staticmethod
    def _filters(conn, scope, run_id, as_of, watermark):
        review_id = References._review(conn, scope, run_id)
        clause = "repository_id=? AND review_id=?"
        args = [scope.repository_id, review_id]
        if as_of is not None:
            clause += " AND captured_at<=?"
            args.append(normalize_timestamp(as_of))
        if watermark is not None:
            clause += " AND rowid<=?"
            args.append(integer(watermark, "watermark", 0, 2**63 - 1))
        return clause, args

    @staticmethod
    def _view(row, run_id):
        result = dict(row)
        result["association_state"] = "agent_reported"
        result["snapshot_relation"] = "current_run" if result["run_id"] == run_id else "historical_run"
        if "body" in result:
            if (digest({key: result[key] for key in CONTENT_FIELDS}) != result["content_sha256"]
                    or len(result["body"]) != result["body_chars"]
                    or len(result["body"].encode("utf-8")) != result["body_bytes"]):
                raise LedgerError("reference_corrupt", "Stored external reference content failed its integrity check")
        return result

    @staticmethod
    def watermark(conn, scope, run_id):
        review_id = References._review(conn, scope, run_id)
        return conn.execute("SELECT COALESCE(MAX(rowid),0) FROM external_review_references "
                            "WHERE repository_id=? AND review_id=?",
                            (scope.repository_id, review_id)).fetchone()[0]

    @staticmethod
    def get(conn, scope, run_id, reference_id, as_of=None, watermark=None,
            require_valid=True, *, include_body=True):
        text(reference_id, "reference_id", 64)
        if type(require_valid) is not bool or type(include_body) is not bool:
            raise LedgerError("invalid_input", "Reference validity and body selection must be boolean")
        clause, args = References._filters(conn, scope, run_id, as_of, watermark)
        columns = ",".join(METADATA_COLUMNS) + (",body" if include_body else "")
        row = conn.execute(f"SELECT rowid AS capture_sequence,{columns} FROM external_review_references "
                           f"WHERE {clause} AND id=?", [*args, reference_id]).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "External reference is not available in this PR and capture boundary")
        if require_valid and not row["valid"]:
            raise LedgerError("reference_invalidated", "External reference was invalidated; prepare a new context explicitly")
        return References._view(row, run_id)

    @staticmethod
    def list(conn, scope, run_id, as_of=None, watermark=None, limit=10, offset=0):
        integer(limit, "limit", 1, 100)
        integer(offset, "offset", 0, 1_000_000)
        clause, args = References._filters(conn, scope, run_id, as_of, watermark)
        rows = conn.execute(f"SELECT rowid AS capture_sequence,{','.join(METADATA_COLUMNS)} "
                            f"FROM external_review_references WHERE {clause} AND valid=1 "
                            "ORDER BY rowid DESC LIMIT ? OFFSET ?", [*args, limit, offset])
        return [References._view(row, run_id) for row in rows]

    @staticmethod
    def record(conn, scope, run, data, actor):
        fields(data, REFERENCE_FIELDS, REQUIRED_REFERENCE_FIELDS)
        finding_id = text(data["finding_id"], "finding_id", 64)
        finding = conn.execute("SELECT r.review_id FROM findings f JOIN runs r "
                               "ON r.repository_id=f.repository_id AND r.id=f.origin_run_id "
                               "WHERE f.repository_id=? AND f.id=?",
                               (scope.repository_id, finding_id)).fetchone()
        if finding is None or finding["review_id"] != run["review_id"]:
            raise LedgerError("scope_not_found", "External reference requires an existing finding from this PR")
        provider = choice(data["provider"], "provider", {"github"})
        event_type = choice(data["event_type"], "event_type", set(_ANCHORS))
        external_id = text(data["external_id"], "external_id", 19)
        if not _EXTERNAL_ID.fullmatch(external_id) or int(external_id) > 2**63 - 1:
            raise LedgerError("invalid_input", "external_id requires a positive canonical decimal GitHub ID")
        pull = conn.execute("SELECT number FROM reviews WHERE repository_id=? AND id=?",
                            (scope.repository_id, run["review_id"])).fetchone()[0]
        url = text(data["url"], "url", 2048)
        expected = f"https://github.com/{scope.repository_name}/pull/{pull}#{_ANCHORS[event_type]}{external_id}"
        if url != expected:
            raise LedgerError("invalid_input", "url must exactly match the canonical GitHub event URL for this repository and PR")
        body = text(data["body"], "body", MAX_BODY_CHARS, empty=True)
        try:
            body_bytes = len(body.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise LedgerError("invalid_input", "body requires valid Unicode text") from exc
        content = {"provider": provider, "event_type": event_type, "external_id": external_id,
                   "url": url, "body": body,
                   "source_revision": sha(data["source_revision"], "source_revision") if data.get("source_revision") is not None else None,
                   "origin_at": normalize_timestamp(data["origin_at"], "origin_at") if data["origin_at"] is not None else None,
                   "source_updated_at": normalize_timestamp(data["source_updated_at"], "source_updated_at") if data.get("source_updated_at") is not None else None}
        checksum = digest(content)
        identity = [scope.repository_id, run["review_id"], finding_id, provider, event_type, external_id]
        clause = "repository_id=? AND review_id=? AND finding_id=? AND provider=? AND event_type=? AND external_id=?"
        existing = conn.execute(f"SELECT id,valid FROM external_review_references WHERE {clause} AND content_sha256=?",
                                [*identity, checksum]).fetchone()
        if existing is not None:
            if not existing["valid"]:
                raise LedgerError("reference_invalidated", "Identical external reference content was invalidated and cannot be resurrected")
            reference = References.get(conn, scope, run["id"], existing["id"], include_body=False)
            return {"state": "deduplicated", "reference_id": existing["id"], "reference": reference}
        version = conn.execute(f"SELECT COALESCE(MAX(version),0)+1 FROM external_review_references WHERE {clause}",
                               identity).fetchone()[0]
        ident, captured = new_id("reference"), now()
        columns = ("id", "repository_id", "review_id", "run_id", "finding_id", *CONTENT_FIELDS,
                   "captured_at", "body_chars", "body_bytes", "content_sha256", "version", "provenance")
        values = [ident, scope.repository_id, run["review_id"], run["id"], finding_id,
                  *(content[key] for key in CONTENT_FIELDS), captured, len(body), body_bytes,
                  checksum, version, "agent_reported"]
        conn.execute(f"INSERT INTO external_review_references ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        Store.audit(conn, scope, ident, "external_reference", actor, {"finding_id": finding_id, "version": version})
        reference = References.get(conn, scope, run["id"], ident, include_body=False)
        return {"state": "recorded", "reference_id": ident, "reference": reference}

    @staticmethod
    def invalidate(conn, scope, run, data, actor):
        fields(data, {"reference_id", "reason"}, {"reference_id", "reason"})
        reason = text(data["reason"], "reason", 4000)
        reference = References.get(conn, scope, run["id"], data["reference_id"], require_valid=False, include_body=False)
        # Revocation remains available after the capture run is superseded.
        # The service fences the requesting run's owner/generation, and get()
        # above restricts the target to the same repository and PR.
        if reference["valid"]:
            conn.execute("UPDATE external_review_references SET valid=0,invalid_reason=?,invalidated_at=? "
                         "WHERE repository_id=? AND review_id=? AND id=?",
                         (reason, now(), scope.repository_id, run["review_id"], reference["id"]))
            Store.audit(conn, scope, reference["id"], "invalidate_external_reference", actor,
                        {"reason": reason, "requesting_run_id": run["id"], "capture_run_id": reference["run_id"]})
        return {"state": "invalidated", "reference_id": reference["id"],
                "notice": "Only the external context snapshot was invalidated; no assessment or lesson truth changed."}
