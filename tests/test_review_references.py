"""Synthetic frozen external metadata: scoped history, never evidence or truth."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3

import pytest

from review_ledger import storage
from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError, Scope, digest
from review_ledger.references import CONTENT_FIELDS, References, normalize_timestamp
from review_ledger.service import Ledger
from review_ledger.storage import Store

REPO = "synthetic/example"


@pytest.fixture
def reference_data(ledger, opened, actor):
    finding = ledger.record(REPO, opened["id"], actor, opened["generation"], "finding",
                            {"claim": "Synthetic claim associated with a copied review"}, "reference-finding")
    return {"finding_id": finding["finding_id"], "provider": "github", "event_type": "review",
            "external_id": "123", "url": "https://github.com/synthetic/example/pull/7#pullrequestreview-123",
            "body": "Synthetic copied review. This is not verified evidence.",
            "origin_at": "2026-10-01T12:00:00Z"}


def record(ledger, opened, actor, data, key="reference"):
    return ledger.record(REPO, opened["id"], actor, opened["generation"], "external_reference", data, key)


def get(ledger, opened, ident, **kwargs):
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        return References.get(conn, scope, opened["id"], ident, **kwargs)


def invalidate(ledger, opened, actor, ident, key="invalidate"):
    return ledger.record(REPO, opened["id"], actor, opened["generation"], "invalidate_external_reference",
                         {"reference_id": ident, "reason": "Synthetic incorrectly copied context"}, key)


@pytest.mark.parametrize("event,anchor", [("review", "pullrequestreview-"),
                                           ("issue_comment", "issuecomment-"),
                                           ("review_comment", "discussion_r")])
def test_exact_provider_event_metadata_is_backend_stamped(ledger, opened, actor, reference_data, event, anchor):
    data = {**reference_data, "event_type": event,
            "url": f"https://github.com/{REPO}/pull/7#{anchor}123",
            "source_revision": "a" * 40, "source_updated_at": "2026-10-01T14:30:00+02:30"}
    result = record(ledger, opened, actor, data)
    row = get(ledger, opened, result["reference_id"])
    assert row["provider"] == "github"
    assert row["provenance"] == row["association_state"] == "agent_reported"
    assert row["origin_at"] == row["source_updated_at"] == "2026-10-01T12:00:00.000000+00:00"
    assert row["captured_at"] > row["origin_at"]
    assert row["run_id"] == opened["id"] and row["finding_id"] == data["finding_id"]
    assert row["snapshot_relation"] == "current_run"
    assert row["version"] == row["capture_sequence"] == row["valid"] == 1
    assert row["content_sha256"] == digest({key: row[key] for key in CONTENT_FIELDS})
    assert row["body_chars"] == len(row["body"])
    assert row["body_bytes"] == len(row["body"].encode())
    assert "body" not in result["reference"]


@pytest.mark.parametrize("field", ["provenance", "association_state", "captured_at", "valid", "version",
                                  "content_sha256", "run_id", "repository_id", "review_id", "observation_ids"])
def test_unknown_backend_and_evidence_fields_rejected(ledger, opened, actor, reference_data, field):
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, {**reference_data, field: "forged"})
    assert caught.value.code == "invalid_input"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_review_references").fetchone()[0] == 0


@pytest.mark.parametrize("url", [
    "http://github.com/synthetic/example/pull/7#pullrequestreview-123",
    "https://github.com.evil.invalid/synthetic/example/pull/7#pullrequestreview-123",
    "https://github.com@evil.invalid/synthetic/example/pull/7#pullrequestreview-123",
    "https://user@github.com/synthetic/example/pull/7#pullrequestreview-123",
    "https://github.com:443/synthetic/example/pull/7#pullrequestreview-123",
    "https://github.com/synthetic/other/pull/7#pullrequestreview-123",
    "https://github.com/synthetic/example/pull/8#pullrequestreview-123",
    "https://github.com/synthetic/example/pull/07#pullrequestreview-123",
    "https://github.com/synthetic/example/issues/7#pullrequestreview-123",
    "https://github.com/synthetic/example/pull/7?x=1#pullrequestreview-123",
    "https://github.com/synthetic/example/pull/7#pullrequestreview-124",
    "https://github.com/synthetic/example/pull/7#issuecomment-123",
    "https://github.com/synthetic/example/pull/7#pullrequestreview-%31%32%33",
    "https://github.com/synthetic/example/pull/7#pullrequestreview-123/",
    "https://github.com/synthetic/example/pull/7#pullrequestreview-123\n",
    "https://github.com/synthetic/example/pull/../pull/7#pullrequestreview-123",
    "https://github.com/synthetic/example/pull/7#pullrequestreview-123?redirect=evil",
])
def test_noncanonical_and_cross_scope_urls_rejected(ledger, opened, actor, reference_data, url):
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, {**reference_data, "url": url})
    assert caught.value.code == "invalid_input"


@pytest.mark.parametrize("changes", [
    {"provider": "gitlab"}, {"event_type": "commit_comment"}, {"external_id": 123},
    {"external_id": "00123"}, {"external_id": "0"}, {"external_id": "-1"},
    {"external_id": str(2**63)}, {"external_id": "1 OR 1=1"},
    {"body": "x" * 8001}, {"body": "\x00"}, {"body": "\ud800"},
    {"source_revision": "abc123"}, {"source_revision": "A" * 40},
    {"origin_at": "2026-10-01"}, {"origin_at": "2026-10-01T12:00:00"},
    {"origin_at": "2026-10-01T12:00:00+00:99"}, {"origin_at": "2026-02-30T12:00:00Z"},
    {"origin_at": "2026-10-01T12:00:00+00:60"}, {"origin_at": "2026-10-01T12:00:00+24:00"},
    {"origin_at": "2026-10-01T12:00:00-00:00"}, {"source_updated_at": "yesterday"},
])
def test_strict_field_validation(ledger, opened, actor, reference_data, changes):
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, {**reference_data, **changes})
    assert caught.value.code == "invalid_input"


def test_unknown_origin_is_explicit_and_empty_body_is_preserved(ledger, opened, actor, reference_data):
    data = {**reference_data, "origin_at": None, "body": "", "source_updated_at": None, "source_revision": None}
    result = record(ledger, opened, actor, data)
    row = get(ledger, opened, result["reference_id"])
    assert row["origin_at"] is row["source_updated_at"] is row["source_revision"] is None
    assert row["body"] == "" and row["body_chars"] == row["body_bytes"] == 0
    del data["origin_at"]
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, data, "missing-origin")
    assert caught.value.code == "invalid_input"


def test_maximum_unicode_body_metadata_reads_do_not_select_body(ledger, opened, actor, reference_data):
    result = record(ledger, opened, actor, {**reference_data, "body": "🔬" * 8000})
    assert result["reference"]["body_chars"] == 8000
    assert result["reference"]["body_bytes"] == 32000
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        queries = []
        conn.set_trace_callback(queries.append)
        row = References.get(conn, scope, opened["id"], result["reference_id"], include_body=False)
        listed = References.list(conn, scope, opened["id"])
        conn.set_trace_callback(None)
        assert "body" not in row and listed == [row]
        selects = [q.lower().split(" from ")[0] for q in queries if "from external_review_references" in q.lower()]
        assert selects and all(",body," not in q and not q.endswith(",body") and "select *" not in q for q in selects)


def test_retry_dedup_and_distinct_content_versions(ledger, opened, actor, reference_data):
    first = record(ledger, opened, actor, reference_data)
    assert record(ledger, opened, actor, reference_data) == first
    duplicate = record(ledger, opened, actor, reference_data, "different-key")
    assert duplicate["state"] == "deduplicated" and duplicate["reference_id"] == first["reference_id"]
    assert duplicate["reference"] == first["reference"]
    edited = record(ledger, opened, actor, {**reference_data, "body": "Synthetic edit"}, "edit")
    assert edited["reference"]["version"] == 2
    assert edited["reference"]["content_sha256"] != first["reference"]["content_sha256"]
    assert get(ledger, opened, first["reference_id"])["body"] == reference_data["body"]
    assert record(ledger, opened, actor, reference_data, "repeat-first")["reference_id"] == first["reference_id"]
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, {**reference_data, "body": "Changed retry"})
    assert caught.value.code == "idempotency_conflict"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_review_references").fetchone()[0] == 2


def test_invalidation_preserves_hash_and_cannot_resurrect(ledger, opened, actor, reference_data):
    first = record(ledger, opened, actor, reference_data)
    before = get(ledger, opened, first["reference_id"])
    result = invalidate(ledger, opened, actor, first["reference_id"])
    assert invalidate(ledger, opened, actor, first["reference_id"]) == result
    invalid = get(ledger, opened, first["reference_id"], require_valid=False)
    assert invalid["valid"] == 0 and invalid["invalid_reason"] and invalid["invalidated_at"]
    assert invalid["content_sha256"] == before["content_sha256"]
    with pytest.raises(LedgerError) as caught:
        get(ledger, opened, first["reference_id"])
    assert caught.value.code == "reference_invalidated"
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, reference_data, "invalid-repeat")
    assert caught.value.code == "reference_invalidated"
    # The old operation receipt is historical, not a new valid snapshot.
    assert record(ledger, opened, actor, reference_data) == first
    assert get(ledger, opened, first["reference_id"], require_valid=False)["valid"] == 0
    changed = record(ledger, opened, actor, {**reference_data, "body": "Explicitly recaptured edit"}, "corrected")
    assert changed["reference"]["version"] == 2


def test_reference_never_becomes_observation_assessment_or_lesson_source(ledger, opened, actor,
                                                                       reference_data, observed, lesson_data):
    ref = record(ledger, opened, actor, reference_data)["reference_id"]
    assessment = {"finding_id": reference_data["finding_id"], "state": "supported", "basis": "behavior",
                  "rationale": "Synthetic evidence check", "limitations": "Agent reported", "observation_ids": [ref]}
    with pytest.raises(LedgerError) as caught:
        ledger.record(REPO, opened["id"], actor, 1, "assessment", assessment, "invalid-evidence")
    assert caught.value.code == "ineligible_evidence"
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    with pytest.raises(LedgerError):
        learning.propose(scope, opened["id"], actor, 1,
                         {**lesson_data, "sources": [{"observation_id": ref, "relation": "supports"}]}, "invalid-lesson")
    assessment["observation_ids"] = [observed]
    assessed = ledger.record(REPO, opened["id"], actor, 1, "assessment", assessment, "real-evidence")
    lesson = learning.propose(scope, opened["id"], actor, 1, lesson_data, "real-lesson")
    learning.operator(scope, lesson["version_id"], "approve", "Synthetic operator", "approve")
    invalidate(ledger, opened, actor, ref)
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT state,freshness FROM assessments WHERE id=?", (assessed["assessment_id"],)).fetchone()[:] == ("supported", "current")
        assert conn.execute("SELECT state FROM lesson_versions WHERE id=?", (lesson["version_id"],)).fetchone()[0] == "active"
        assert conn.execute("SELECT valid FROM observations WHERE id=?", (observed,)).fetchone()[0] == 1


def test_owner_generation_and_run_status_fences(ledger, opened, actor, reference_data):
    for other, generation in [(Actor("different-owner"), 1), (actor, 2)]:
        with pytest.raises(LedgerError) as caught:
            ledger.record(REPO, opened["id"], other, generation, "external_reference", reference_data, "wrong-owner")
        assert caught.value.code == "ownership_conflict"
    first = record(ledger, opened, actor, reference_data)
    ledger.run_action(REPO, opened["id"], actor, 1, "complete", "complete")
    assert record(ledger, opened, actor, reference_data) == first
    with pytest.raises(LedgerError) as caught:
        record(ledger, opened, actor, reference_data, "after-complete")
    assert caught.value.code in {"ownership_conflict", "run_not_writable"}
    with pytest.raises(LedgerError):
        invalidate(ledger, opened, actor, first["reference_id"])


def test_same_pr_history_keeps_capture_run_and_cross_pr_reads_fail(ledger, opened, actor, reference_data, synthetic_snapshot):
    first = record(ledger, opened, actor, reference_data)
    newer = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "newer-run")["run"]
    old = get(ledger, newer, first["reference_id"])
    assert old["run_id"] == opened["id"] and old["snapshot_relation"] == "historical_run"
    duplicate = record(ledger, newer, actor, reference_data, "new-run-duplicate")
    assert duplicate["reference_id"] == first["reference_id"]
    assert duplicate["reference"]["captured_at"] == first["reference"]["captured_at"]
    assert duplicate["reference"]["run_id"] == opened["id"]
    with pytest.raises(LedgerError) as caught:
        invalidate(ledger, opened, actor, first["reference_id"])
    assert caught.value.code in {"ownership_conflict", "run_not_writable"}
    invalidate(ledger, newer, actor, first["reference_id"])
    assert get(ledger, newer, first["reference_id"], require_valid=False)["valid"] == 0
    with pytest.raises(LedgerError) as caught:
        record(ledger, newer, actor, reference_data, "invalidated-new-run-repeat")
    assert caught.value.code == "reference_invalidated"
    edited = record(ledger, newer, actor, {**reference_data, "body": "New-run edit"}, "new-run-edit")
    assert edited["reference"]["run_id"] == newer["id"] and edited["reference"]["version"] == 2
    other_pr = ledger.open({**synthetic_snapshot, "number": 8}, actor, "other-pr")["run"]
    with pytest.raises(LedgerError) as caught:
        get(ledger, other_pr, first["reference_id"])
    assert caught.value.code == "scope_not_found"
    with pytest.raises(LedgerError) as caught:
        record(ledger, other_pr, actor, reference_data, "cross-pr-write")
    assert caught.value.code == "scope_not_found"
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        assert References.list(conn, scope, other_pr["id"]) == []
        assert References.watermark(conn, scope, other_pr["id"]) == 0


def test_repository_profile_and_unrelated_finding_fences(ledger, opened, actor, reference_data, synthetic_snapshot):
    first = record(ledger, opened, actor, reference_data)
    other = ledger.open({**synthetic_snapshot, "repository_id": 2002, "repository_full_name": "synthetic/other"}, actor, "other-repo")["run"]
    scope = ledger.scope("synthetic/other")
    with ledger.store.connect() as conn:
        with pytest.raises(LedgerError) as caught:
            References.get(conn, scope, other["id"], first["reference_id"])
        assert caught.value.code == "scope_not_found"
        with pytest.raises(LedgerError):
            References.get(conn, Scope(scope.repository_id, "forged/name"), other["id"], first["reference_id"])
    with pytest.raises(LedgerError) as caught:
        ledger.record("synthetic/other", other["id"], actor, 1, "external_reference", reference_data, "wrong-finding")
    assert caught.value.code == "scope_not_found"
    wrong = Store(ledger.store.data_dir, "wrong-profile")
    with pytest.raises(LedgerError) as caught:
        with wrong.connect():
            pass
    assert caught.value.code == "profile_mismatch"
    with pytest.raises(LedgerError) as caught:
        ledger.record("unauthorized/repo", opened["id"], actor, 1, "external_reference", reference_data, "wrong-repo")
    assert caught.value.code == "repository_not_authorized"


def test_origin_time_never_grants_early_local_availability(ledger, opened, actor, reference_data, monkeypatch):
    import review_ledger.references as references
    monkeypatch.setattr(references, "now", lambda: "2026-10-08T12:00:00.000000+00:00")
    ref = record(ledger, opened, actor, reference_data)["reference_id"]
    with pytest.raises(LedgerError) as caught:
        get(ledger, opened, ref, as_of="2026-10-02T00:00:00Z")
    assert caught.value.code == "scope_not_found"
    assert get(ledger, opened, ref, as_of="2026-10-08T14:00:00+02:00")["id"] == ref
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        watermark = References.watermark(conn, scope, opened["id"])
        assert References.list(conn, scope, opened["id"], as_of="2026-10-02T00:00:00Z") == []
    # Wall clock can regress: the insertion watermark still excludes later rows.
    monkeypatch.setattr(references, "now", lambda: "2026-10-07T12:00:00.000000+00:00")
    later = record(ledger, opened, actor, {**reference_data, "body": "Late capture with earlier backend clock"}, "late")["reference_id"]
    with pytest.raises(LedgerError):
        get(ledger, opened, later, as_of="2026-10-08T12:00:00Z", watermark=watermark)
    with ledger.store.connect() as conn:
        rows = References.list(conn, scope, opened["id"], as_of="2026-10-08T12:00:00Z", watermark=watermark)
        assert [row["id"] for row in rows] == [ref]
        assert References.list(conn, scope, opened["id"], watermark=0) == []


def test_metadata_pagination_insertion_order_and_invalid_exclusion(ledger, opened, actor, reference_data):
    ids = [record(ledger, opened, actor, {**reference_data, "body": str(i)}, f"page-{i}")["reference_id"] for i in range(4)]
    invalidate(ledger, opened, actor, ids[1])
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        assert [r["id"] for r in References.list(conn, scope, opened["id"], limit=2)] == [ids[3], ids[2]]
        assert [r["id"] for r in References.list(conn, scope, opened["id"], limit=2, offset=2)] == [ids[0]]


def test_full_read_detects_body_hash_and_size_corruption(ledger, opened, actor, reference_data):
    ident = record(ledger, opened, actor, reference_data)["reference_id"]
    # Direct file-owner writes are outside the API immutability boundary.
    with ledger.store.connect() as conn:
        conn.execute("UPDATE external_review_references SET body='tampered' WHERE id=?", (ident,))
    with pytest.raises(LedgerError) as caught:
        get(ledger, opened, ident)
    assert caught.value.code == "reference_corrupt"


def test_concurrent_same_content_deduplicates(ledger, opened, actor, reference_data):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: record(ledger, opened, actor, reference_data, f"concurrent-{i}"), range(4)))
    assert len({r["reference_id"] for r in results}) == 1
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_review_references").fetchone()[0] == 1


def make_v4(tmp_path, monkeypatch, synthetic_snapshot, actor):
    store = Store(tmp_path / "schema4", "synthetic-v4")
    ledger = Ledger(store, [REPO], skill_version="1.0.0", skill_hash="d" * 64)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "SCHEMA_VERSION", 4)
        opened = ledger.open(synthetic_snapshot, actor, "v4-open")["run"]
        fid = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Preserved v4 claim"}, "v4-finding")["finding_id"]
        with store.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='external_review_references'").fetchone()
    return ledger, opened, fid


def snapshot_tables(path, tables):
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}


def test_v4_to_v5_upgrade_preserves_every_existing_row_and_is_atomic(tmp_path, monkeypatch, synthetic_snapshot, actor):
    ledger, opened, fid = make_v4(tmp_path, monkeypatch, synthetic_snapshot, actor)
    store = ledger.store
    before = snapshot_tables(store.path, storage.V4_TABLES)
    migrate = Store._migration
    def interrupted(conn, version):
        assert version == 5
        migrate(conn, version)
        raise RuntimeError("Synthetic interrupted reference migration")
    with monkeypatch.context() as patch:
        patch.setattr(Store, "_migration", staticmethod(interrupted))
        with pytest.raises(RuntimeError, match="Synthetic interrupted"):
            with store.connect():
                pass
    assert snapshot_tables(store.path, storage.V4_TABLES) == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='external_review_references'").fetchone()
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert snapshot_tables(store.path, storage.V4_TABLES) == before


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_reference_table_under_old_version_refused_without_writes(tmp_path, monkeypatch, version):
    store = Store(tmp_path / f"premature-{version}", "synthetic-premature")
    with monkeypatch.context() as patch:
        patch.setattr(storage, "SCHEMA_VERSION", version)
        with store.connect():
            pass
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("CREATE TABLE external_review_references (id TEXT PRIMARY KEY)")
        conn.commit()
    before = store.path.read_bytes()
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pass
    assert caught.value.code == "schema_lineage_conflict"
    assert store.path.read_bytes() == before


def test_missing_or_wrong_reference_layout_refused_before_writes(ledger, opened):
    with ledger.store.connect() as conn:
        conn.execute("ALTER TABLE external_review_references DROP COLUMN body_bytes")
    before = ledger.store.path.read_bytes()
    with pytest.raises(LedgerError) as caught:
        with ledger.store.connect():
            pass
    assert caught.value.code == "unknown_schema"
    assert ledger.store.path.read_bytes() == before


def test_reference_history_and_invalidations_round_trip_verified_backup(ledger, opened, actor, reference_data, tmp_path):
    first = record(ledger, opened, actor, reference_data)["reference_id"]
    second = record(ledger, opened, actor, {**reference_data, "body": "An edited synthetic review"}, "second")["reference_id"]
    invalidate(ledger, opened, actor, first)
    scope = ledger.scope(REPO)
    expected = snapshot_tables(ledger.store.path, storage.LEDGER_TABLES)
    backup = ledger.store.backup()
    assert backup["restore_verified"]
    restored = Store(tmp_path / "restored", ledger.store.profile_key)
    restored.data_dir.mkdir()
    shutil.copyfile(backup["path"], restored.path)
    with restored.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert References.get(conn, scope, opened["id"], first, require_valid=False)["valid"] == 0
        assert References.get(conn, scope, opened["id"], second)["version"] == 2
    assert snapshot_tables(restored.path, storage.LEDGER_TABLES) == expected
