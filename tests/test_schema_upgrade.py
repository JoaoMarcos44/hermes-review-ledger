"""Upgrade genuine V1 files with synthetic durable records and native locks."""
from contextlib import closing
import multiprocessing
from pathlib import Path
import sqlite3

import pytest

from review_ledger.models import LedgerError, Scope, canonical, digest
from review_ledger import storage
from review_ledger.storage import Store


PROFILE = "synthetic-schema-profile"
REPO = "synthetic/example"
INDEXES = {
    "assessment_finding_run": ("repository_id", "finding_id", "run_id"),
    "assessment_run_finding": ("repository_id", "run_id", "finding_id"),
    "finding_origin_run": ("repository_id", "origin_run_id", "id"),
    "lesson_recall_order": ("repository_id", "state", "lesson_id", "version", "id"),
}


def v1_store(root):
    """Build the shipped V1 schema directly; current initialization is not used."""
    store = Store(root, PROFILE)
    root.mkdir()
    migration = Path(storage.__file__).parent / "migrations" / "001_initial.sql"
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO ledger_meta VALUES ('profile_key',?)", (PROFILE,))
        conn.execute("INSERT INTO ledger_meta VALUES ('synthetic-marker','preserved')")
        conn.execute("INSERT INTO repositories VALUES (1001,'synthetic-node',?,?,?)", (REPO, REPO, "synthetic-time"))
        conn.execute("INSERT INTO reviews VALUES ('review_fixture',1001,7,'Synthetic','https://github.com/synthetic/example/pull/7')")
        conn.execute("""INSERT INTO runs
            (id,repository_id,review_id,snapshot_key,head_sha,base_sha,comparison,
             config_json,skill_version,skill_hash,snapshot_json,status,owner_session,created_at,updated_at)
            VALUES ('run_fixture',1001,'review_fixture','snapshot',?,?,'github_pr',
                    '{}','0.1.0',?,'{}','active','synthetic-owner','synthetic-time','synthetic-time')""",
                     ("a" * 40, "b" * 40, "d" * 64))
        conn.execute("""INSERT INTO observations
            (id,repository_id,run_id,provenance,kind,outcome,summary,details,limitations,created_at)
            VALUES ('observation_fixture',1001,'run_fixture','agent_reported','inspection',
                    'inspection','Synthetic preserved observation','','Synthetic','synthetic-time')""")
        conn.execute("INSERT INTO findings VALUES ('finding_fixture',1001,'run_fixture','Synthetic claim','synthetic-time')")
        conn.execute("""INSERT INTO assessments
            (id,repository_id,finding_id,run_id,state,freshness,basis,rationale,limitations,created_at)
            VALUES ('assessment_fixture',1001,'finding_fixture','run_fixture','supported',
                    'current','inspection','Synthetic rationale','Synthetic','synthetic-time')""")
        conn.execute("INSERT INTO assessment_sources VALUES (1001,'assessment_fixture','observation_fixture','supports')")
        conn.execute("""INSERT INTO lesson_versions
            (id,repository_id,lesson_id,version,question,conditions_json,exclusions_json,
             verification,tags_json,symbols_json,state,created_at,approved_at)
            VALUES ('version_fixture',1001,'lesson_fixture',1,'Synthetic question?',
                    '["Synthetic condition"]','[]','Synthetic verification','["fixture"]',
                    '["synthetic_symbol"]','active','synthetic-time','synthetic-time')""")
        conn.execute("INSERT INTO lesson_sources VALUES (1001,'version_fixture','observation_fixture','supports')")
        conn.execute("""INSERT INTO lesson_uses
            (id,repository_id,version_id,run_id,applicability,explanation,created_at,updated_at)
            VALUES ('use_fixture',1001,'version_fixture','run_fixture','applicable',
                    'Synthetic rationale','synthetic-time','synthetic-time')""")
        conn.execute("INSERT INTO audit_events VALUES ('event_fixture',1001,'version_fixture','approve','local_operator','{}','synthetic-time')")
        conn.execute("INSERT INTO idempotency VALUES (1001,'fixture','run_fixture','retry',?,?,?)",
                     (digest({"synthetic": True}), canonical({"state": "recorded", "preserved": True}), "synthetic-time"))
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    return store


def records(path):
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in sorted(storage.V2_TABLES)}


def assert_current_schema(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    for name, expected in INDEXES.items():
        columns = tuple(row[2] for row in conn.execute(f'PRAGMA index_info("{name}")'))
        assert columns == expected


def test_v1_upgrade_preserves_all_records_receipts_and_foreign_keys(tmp_path):
    store = v1_store(tmp_path / "v1")
    before = records(store.path)
    with store.connect() as conn:
        assert_current_schema(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        receipt = store.receipt(conn, Scope(1001, REPO), "fixture", "run_fixture", "retry", {"synthetic": True})
        assert receipt == {"state": "recorded", "preserved": True}
    assert records(store.path) == before
    # Reopening an already upgraded file performs no additional migration.
    after = store.path.read_bytes()
    with store.connect() as conn:
        assert_current_schema(conn)
    assert store.path.read_bytes() == after
    backup = store.backup()
    assert backup["restore_verified"] is True
    assert records(backup["path"]) == before
    with closing(sqlite3.connect(backup["path"])) as conn:
        assert_current_schema(conn)


def test_new_database_applies_all_migrations_and_backup_restores_v3(tmp_path):
    store = Store(tmp_path / "new", PROFILE)
    with store.connect() as conn:
        assert_current_schema(conn)
    backup = store.backup()
    assert backup["restore_verified"] is True
    with closing(sqlite3.connect(backup["path"])) as conn:
        assert_current_schema(conn)
        assert conn.execute("SELECT value FROM ledger_meta WHERE key='profile_key'").fetchone()[0] == PROFILE


@pytest.mark.parametrize("kind,code", [
    ("profile", "profile_mismatch"), ("newer", "schema_too_new"),
    ("unversioned", "unknown_schema"), ("incomplete", "unknown_schema"),
])
def test_refused_v1_upgrade_preserves_database_bytes_before_migration(tmp_path, monkeypatch, kind, code):
    store = v1_store(tmp_path / kind)
    with closing(sqlite3.connect(store.path)) as conn:
        if kind == "newer":
            conn.execute("PRAGMA user_version=99")
        elif kind == "unversioned":
            conn.execute("PRAGMA user_version=0")
        elif kind == "incomplete":
            conn.execute("DROP TABLE audit_events")
        conn.commit()
    before = store.path.read_bytes()
    requested = Store(store.data_dir, "different-profile" if kind == "profile" else PROFILE)
    monkeypatch.setattr(requested, "_migration", lambda *_: pytest.fail("A refusal must precede migration"))
    with pytest.raises(LedgerError) as exc:
        with requested.connect():
            pytest.fail("Refused database was opened")
    assert exc.value.code == code
    assert store.path.read_bytes() == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert not INDEXES.keys() & {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_interrupted_migration_rolls_back_indexes_and_schema_version(tmp_path, monkeypatch):
    store = v1_store(tmp_path / "interrupted")
    before = records(store.path)
    migrate = store._migration

    def interrupted(conn, version):
        if version == 2:
            conn.execute("CREATE INDEX synthetic_partial ON findings(repository_id,origin_run_id)")
            raise RuntimeError("Synthetic interrupted migration")
        migrate(conn, version)

    monkeypatch.setattr(store, "_migration", interrupted)
    with pytest.raises(RuntimeError, match="Synthetic interrupted migration"):
        with store.connect():
            pass
    assert records(store.path) == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_partial'").fetchone() is None
    monkeypatch.setattr(store, "_migration", migrate)
    with store.connect() as conn:
        assert_current_schema(conn)


def _upgrade_worker(root, start, output):
    try:
        if not start.wait(timeout=10):
            raise RuntimeError("Synthetic start barrier timed out")
        with Store(Path(root), PROFILE).connect() as conn:
            output.put(("ok", conn.execute("PRAGMA user_version").fetchone()[0]))
    except Exception as exc:
        output.put(("error", type(exc).__name__, str(exc)))


def test_two_native_processes_upgrade_v1_once_without_changing_records(tmp_path):
    store = v1_store(tmp_path / "concurrent")
    before = records(store.path)
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    workers = [context.Process(target=_upgrade_worker, args=(str(store.data_dir), start, output)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        start.set()
        results = [output.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0
        assert results == [("ok", 3), ("ok", 3)], results
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
            if worker.pid is not None and not worker.is_alive():
                worker.close()
        output.close()
        output.join_thread()
    assert records(store.path) == before
    with store.connect() as conn:
        assert_current_schema(conn)


def test_receipt_checks_key_payload_and_repository_without_executing_a_write(ledger, opened, actor):
    scope = ledger.scope(REPO)
    payload = {"synthetic": True}
    committed = ledger.store.write(scope, "fixture", opened["id"], "receipt", payload,
                                   lambda conn: {"state": "recorded", "synthetic": "preserved"})
    with ledger.store.connect() as conn:
        assert ledger.store.receipt(conn, scope, "fixture", opened["id"], "receipt", payload) == committed
        assert ledger.store.receipt(conn, scope, "other-operation", opened["id"], "receipt", payload) is None
        assert ledger.store.receipt(conn, scope, "fixture", "other-scope", "receipt", payload) is None
        for key, data, requested_scope, code in (
            ("receipt", {"synthetic": False}, scope, "idempotency_conflict"),
            ("", payload, scope, "invalid_input"),
            ("receipt", payload, Scope(scope.repository_id, "synthetic/different"), "scope_not_found"),
        ):
            with pytest.raises(LedgerError) as exc:
                ledger.store.receipt(conn, requested_scope, "fixture", opened["id"], key, data)
            assert exc.value.code == code
        assert conn.execute("SELECT COUNT(*) FROM idempotency WHERE operation='fixture'").fetchone()[0] == 1
