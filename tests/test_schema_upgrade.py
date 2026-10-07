"""Upgrade genuine V1/V2 files, preserve original fields, and fence lineages."""
from contextlib import closing
import hashlib
import multiprocessing
from pathlib import Path
import sqlite3
import shutil

import pytest

from review_ledger.models import Actor, LedgerError, Scope, canonical, digest
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


def original_columns():
    """Derive historical columns from immutable migration 001, not today's code."""
    migration = Path(storage.__file__).parent / "migrations" / "001_initial.sql"
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.executescript(migration.read_text(encoding="utf-8"))
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: tuple(row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')) for table in tables}


def records(path):
    # Every original field is compared; additive V1.5 columns do not weaken
    # preservation checks. The single deliberate metadata addition is checked
    # separately by assert_current, while all prior metadata remains compared.
    with closing(sqlite3.connect(path)) as conn:
        result = {}
        for table, columns in original_columns().items():
            selected = ",".join(f'"{column}"' for column in columns)
            where = " WHERE key != 'schema_lineage'" if table == "ledger_meta" else ""
            result[table] = conn.execute(f'SELECT {selected} FROM "{table}"{where} ORDER BY rowid').fetchall()
        return result


def v2_store(root):
    """Create a real V2 file by applying only the original 001 and 002 SQL."""
    store = v1_store(root)
    migration = Path(storage.__file__).parent / "migrations" / "002_query_indexes.sql"
    with closing(sqlite3.connect(store.path)) as conn:
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version=2")
        conn.commit()
        assert "contribution" not in {row[1] for row in conn.execute("PRAGMA table_info(lesson_uses)")}
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_manifests'").fetchone() is None
        assert conn.execute("SELECT 1 FROM ledger_meta WHERE key='schema_lineage'").fetchone() is None
    return store



def v15_store(root):
    """Create the shipped adaptive schema 3 directly, with every new table used."""
    from review_ledger.skills import _identity

    store = v2_store(root)
    migration = Path(storage.__file__).parent / "migrations" / "003_adaptive_context.sql"
    metadata = {"name": "example", "description": "Synthetic preserved instruction", "tool_grants": False}
    instructions = "Preserve the complete condition and exclusion.\n"
    resources = {"references/notes.md": "Preserved approved resource bytes: é🔬\n"}
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("UPDATE runs SET generation=17,status='paused' WHERE id='run_fixture'")
        conn.execute("""INSERT INTO context_manifests VALUES (
            'context_fixture',1001,'run_fixture','snapshot','protocol_fixture','protocol_hash',
            'policy_fixture','[]','request_fingerprint','content_digest','delivery_receipt',
            'synthetic-time','{"query":"fixture"}')""")
        conn.execute("INSERT INTO optional_skill_versions VALUES (?,?,?,?,?,?,?,?,?)",
                     ("skill_fixture", 1001, "synthetic/example", 1, _identity(metadata, instructions, resources),
                      canonical(metadata), instructions, 1, "synthetic-time"))
        for path, content in resources.items():
            conn.execute("INSERT INTO optional_skill_resources VALUES (?,?,?,?,?)",
                         (1001, "skill_fixture", path, content, hashlib.sha256(content.encode()).hexdigest()))
        conn.execute("""UPDATE lesson_uses SET contribution='redundant',feedback_applicability='applicable',
            supporting_observation_ids_json='["observation_fixture"]' WHERE id='use_fixture'""")
        conn.execute("""INSERT INTO improvement_proposals VALUES (
            'improvement_fixture',1001,'run_fixture','version_fixture',NULL,'use_fixture','review_needed',
            '{}','Synthetic preserved reason','Synthetic expected benefit','[]','synthetic-time')""")
        conn.execute("INSERT INTO improvement_outcomes VALUES (1001,'improvement_fixture','use_fixture')")
        conn.execute("INSERT INTO improvement_aggregates VALUES (1001,'version_fixture',?,'synthetic-time')", (canonical({"window_size": 1}),))
        conn.execute("""INSERT INTO usage_events VALUES (
            'usage_fixture',1001,'run_fixture','ledger_context','fixture','synthetic-time',
            2,6,3,10,'response_digest',1.25,7,3,4,0,1)""")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not storage.CRITIC_TABLES.intersection(row[0] for row in conn.execute("SELECT name FROM sqlite_master"))
    return store


def all_records(path, tables):
    """Capture every field of each specified historical table, including metadata."""
    with closing(sqlite3.connect(path)) as conn:
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in sorted(tables)}

def assert_current(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION == 4
    assert conn.execute("SELECT value FROM ledger_meta WHERE key='schema_lineage'").fetchone()[0] == "adaptive-v15"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert storage.LEDGER_TABLES <= tables
    for name, expected in INDEXES.items():
        columns = tuple(row[2] for row in conn.execute(f'PRAGMA index_info("{name}")'))
        assert columns == expected


def test_v1_upgrade_preserves_all_records_receipts_and_foreign_keys(tmp_path):
    store = v1_store(tmp_path / "v1")
    before = records(store.path)
    with store.connect() as conn:
        assert_current(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        receipt = store.receipt(conn, Scope(1001, REPO), "fixture", "run_fixture", "retry", {"synthetic": True})
        assert receipt == {"state": "recorded", "preserved": True}
    assert records(store.path) == before
    # Reopening an already upgraded file performs no additional migration.
    after = store.path.read_bytes()
    with store.connect() as conn:
        assert_current(conn)
    assert store.path.read_bytes() == after
    backup = store.backup()
    assert backup["restore_verified"] is True
    assert records(backup["path"]) == before
    with closing(sqlite3.connect(backup["path"])) as conn:
        assert_current(conn)


def test_new_database_applies_all_migrations_and_backup_restores_current(tmp_path):
    store = Store(tmp_path / "new", PROFILE)
    with store.connect() as conn:
        assert_current(conn)
    backup = store.backup()
    assert backup["restore_verified"] is True
    with closing(sqlite3.connect(backup["path"])) as conn:
        assert_current(conn)
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


@pytest.mark.parametrize("interrupted_version", [2, 3, 4])
def test_interrupted_migration_rolls_back_indexes_and_schema_version(tmp_path, monkeypatch, interrupted_version):
    store = v1_store(tmp_path / "interrupted")
    before = records(store.path)
    migrate = store._migration

    def interrupted(conn, version):
        migrate(conn, version)
        if version == interrupted_version:
            conn.execute("CREATE INDEX synthetic_partial ON findings(repository_id,origin_run_id)")
            raise RuntimeError("Synthetic interrupted migration")

    monkeypatch.setattr(store, "_migration", interrupted)
    with pytest.raises(RuntimeError, match="Synthetic interrupted migration"):
        with store.connect():
            pass
    assert records(store.path) == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='synthetic_partial'").fetchone() is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_manifests'").fetchone() is None
        assert "contribution" not in {row[1] for row in conn.execute("PRAGMA table_info(lesson_uses)")}
        assert conn.execute("SELECT 1 FROM ledger_meta WHERE key='schema_lineage'").fetchone() is None
    monkeypatch.setattr(store, "_migration", migrate)
    with store.connect() as conn:
        assert_current(conn)


def _upgrade_worker(root, start, output):
    try:
        if not start.wait(timeout=10):
            raise RuntimeError("Synthetic start barrier timed out")
        with Store(Path(root), PROFILE).connect() as conn:
            output.put(("ok", conn.execute("PRAGMA user_version").fetchone()[0]))
    except Exception as exc:
        output.put(("error", type(exc).__name__, str(exc)))


@pytest.mark.parametrize("factory", [v1_store, v2_store, v15_store], ids=["v1", "v2", "v15"])
def test_two_native_processes_upgrade_once_without_changing_records(tmp_path, factory):
    store = factory(tmp_path / "concurrent")
    before = records(store.path)
    adaptive_before = all_records(store.path, storage.V15_TABLES) if factory is v15_store else None
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
        assert results == [("ok", storage.SCHEMA_VERSION), ("ok", storage.SCHEMA_VERSION)], results
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
    if adaptive_before is not None:
        assert all_records(store.path, storage.V15_TABLES) == adaptive_before
    with store.connect() as conn:
        assert_current(conn)


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


def test_genuine_v2_upgrade_keeps_original_columns_and_default_feedback(tmp_path):
    store = v2_store(tmp_path / "v2")
    before = records(store.path)
    with closing(sqlite3.connect(store.path)) as conn:
        original_journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    with store.connect() as conn:
        assert_current(conn)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == original_journal
        feedback = conn.execute("SELECT contribution,feedback_applicability,supporting_observation_ids_json FROM lesson_uses WHERE id='use_fixture'").fetchone()
        assert tuple(feedback) == ("unknown", "unknown", "[]")
        for table in ("context_manifests", "optional_skill_versions", "optional_skill_resources", "improvement_proposals", "improvement_outcomes", "improvement_aggregates", "usage_events"):
            assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
    assert records(store.path) == before
    restored = store.backup()
    assert restored["restore_verified"]
    assert records(restored["path"]) == before
    with closing(sqlite3.connect(restored["path"])) as conn:
        assert_current(conn)


def test_interrupted_v2_to_v3_rolls_back_columns_tables_and_lineage(tmp_path, monkeypatch):
    store = v2_store(tmp_path / "v2-interrupted")
    before = records(store.path)
    migrate = store._migration

    def interrupted(conn, version):
        migrate(conn, version)
        if version == 3:
            raise RuntimeError("Synthetic interruption after complete V3 DDL")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_migration", interrupted)
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            with store.connect():
                pass
    assert records(store.path) == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_manifests'").fetchone() is None
        assert conn.execute("SELECT 1 FROM ledger_meta WHERE key='schema_lineage'").fetchone() is None
        assert "contribution" not in {row[1] for row in conn.execute("PRAGMA table_info(lesson_uses)")}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with store.connect() as conn:
        assert_current(conn)
    assert records(store.path) == before


def test_experimental_critic_schema3_is_rejected_without_mutation(tmp_path, monkeypatch):
    store = v2_store(tmp_path / "critic-experimental")
    with closing(sqlite3.connect(store.path)) as conn:
        # The entire experimental critic DDL is unchanged in migration 004.
        # Only its sequence number changes in the combined, adaptive lineage.
        migration = Path(storage.__file__).parent / "migrations" / "004_critic.sql"
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version=3")
        conn.commit()
    before = store.path.read_bytes()
    monkeypatch.setattr(store, "_migration", lambda *_: pytest.fail("Foreign lineage must never be migrated"))
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pytest.fail("Experimental critic schema was incorrectly opened")
    assert caught.value.code == "schema_lineage_conflict"
    message = str(caught.value)
    assert "matching experimental critic build" in message
    assert "pre-critic schema-2 backup" in message
    assert "original resolved profile data path" in message
    assert "later records and critic history remain in the experimental backup" in message
    assert "If no pre-critic backup exists" in message
    assert "Do not edit PRAGMA user_version or schema_lineage" in message
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["missing_marker", "wrong_marker", "missing_required_table", "missing_outcomes", "missing_aggregates"])
def test_schema3_lineage_marker_and_required_tables_are_both_checked(tmp_path, tamper):
    store = Store(tmp_path / tamper, PROFILE)
    with store.connect() as conn:
        assert_current(conn)
    with closing(sqlite3.connect(store.path)) as conn:
        if tamper == "missing_marker":
            conn.execute("DELETE FROM ledger_meta WHERE key='schema_lineage'")
        elif tamper == "wrong_marker":
            conn.execute("UPDATE ledger_meta SET value='experimental-critic' WHERE key='schema_lineage'")
        elif tamper == "missing_outcomes":
            conn.execute("DROP TABLE improvement_outcomes")
        elif tamper == "missing_aggregates":
            conn.execute("DROP TABLE improvement_aggregates")
        else:
            conn.execute("DROP TABLE usage_events")
        conn.commit()
    before = store.path.read_bytes()
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pass
    assert caught.value.code == "schema_lineage_conflict"
    assert store.path.read_bytes() == before



def test_populated_v15_upgrade_preserves_every_field_resources_owner_and_receipts(tmp_path):
    from review_ledger.skills import Skills

    store = v15_store(tmp_path / "v15")
    before = all_records(store.path, storage.V15_TABLES)
    scope = Scope(1001, REPO)
    with store.connect() as conn:
        assert_current(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        owner = Store.owner(conn, scope, "run_fixture", Actor("synthetic-owner"), 17)
        assert owner["status"] == "paused"
        for actor, generation in ((Actor("other-owner"), 17), (Actor("synthetic-owner"), 16)):
            with pytest.raises(LedgerError) as caught:
                Store.owner(conn, scope, "run_fixture", actor, generation)
            assert caught.value.code == "ownership_conflict"
        assert store.receipt(conn, scope, "fixture", "run_fixture", "retry", {"synthetic": True}) == {
            "state": "recorded", "preserved": True}
        assert Skills.version(conn, scope, "skill_fixture", require_enabled=True)["resources"] == {
            "references/notes.md": "Preserved approved resource bytes: é🔬\n"}
        for table in storage.CRITIC_TABLES:
            assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
    assert all_records(store.path, storage.V15_TABLES) == before
    after = store.path.read_bytes()
    with store.connect() as conn:
        assert_current(conn)
    assert store.path.read_bytes() == after

    backup = store.backup()
    assert backup["restore_verified"] is True
    restored = Store(tmp_path / "restored-v15", PROFILE)
    restored.data_dir.mkdir()
    shutil.copyfile(backup["path"], restored.path)
    with restored.connect() as conn:
        assert_current(conn)
        assert Skills.version(conn, scope, "skill_fixture", require_enabled=True)["resources"]
        Store.owner(conn, scope, "run_fixture", Actor("synthetic-owner"), 17)
    assert all_records(restored.path, storage.V15_TABLES) == before


def test_interrupted_v15_to_v4_preserves_all_adaptive_rows_and_retries(tmp_path, monkeypatch):
    store = v15_store(tmp_path / "v15-interrupted")
    before = all_records(store.path, storage.V15_TABLES)
    migrate = store._migration

    def interrupted(conn, version):
        assert version == 4
        migrate(conn, version)
        raise RuntimeError("Synthetic interruption after critic DDL")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_migration", interrupted)
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            with store.connect():
                pass
    assert all_records(store.path, storage.V15_TABLES) == before
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert not storage.CRITIC_TABLES.intersection(tables)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with store.connect() as conn:
        assert_current(conn)
    assert all_records(store.path, storage.V15_TABLES) == before


@pytest.mark.parametrize("factory", [v1_store, v2_store, v15_store], ids=["v1", "v2", "v15"])
def test_wrong_profile_refuses_each_historical_schema_before_migration(tmp_path, monkeypatch, factory):
    store = factory(tmp_path / "wrong-profile")
    before = store.path.read_bytes()
    wrong = Store(store.data_dir, "different-profile")
    monkeypatch.setattr(wrong, "_migration", lambda *_: pytest.fail("Profile fence must precede migration"))
    with pytest.raises(LedgerError) as caught:
        with wrong.connect():
            pass
    assert caught.value.code == "profile_mismatch"
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("table", sorted(storage.CRITIC_TABLES))
def test_schema4_requires_every_critic_table_before_any_write(tmp_path, monkeypatch, table):
    store = Store(tmp_path / "missing-critic-table", PROFILE)
    with store.connect():
        pass
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute(f'DROP TABLE "{table}"')
    before = store.path.read_bytes()
    monkeypatch.setattr(store, "_migration", lambda *_: pytest.fail("Malformed current schema cannot be migrated"))
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pass
    assert caught.value.code == "unknown_schema"
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["feedback_column", "manifest_column", "resource_foreign_key", "critic_collision"])
def test_schema3_shape_and_collision_are_checked_before_upgrade(tmp_path, monkeypatch, tamper):
    store = v15_store(tmp_path / tamper)
    with closing(sqlite3.connect(store.path)) as conn:
        if tamper == "feedback_column":
            conn.execute("ALTER TABLE lesson_uses DROP COLUMN contribution")
        elif tamper == "manifest_column":
            conn.execute("ALTER TABLE context_manifests DROP COLUMN request_json")
        elif tamper == "resource_foreign_key":
            conn.execute("ALTER TABLE optional_skill_resources RENAME TO old_resources")
            conn.execute("""CREATE TABLE optional_skill_resources (
                repository_id INTEGER NOT NULL, version_id TEXT NOT NULL, path TEXT NOT NULL,
                content TEXT NOT NULL, content_digest TEXT NOT NULL, PRIMARY KEY(version_id,path))""")
            conn.execute("INSERT INTO optional_skill_resources SELECT * FROM old_resources")
            conn.execute("DROP TABLE old_resources")
        else:
            conn.execute("CREATE TABLE critic_runs (id TEXT PRIMARY KEY)")
        conn.commit()
    before = store.path.read_bytes()
    monkeypatch.setattr(store, "_migration", lambda *_: pytest.fail("Unknown layout cannot be migrated"))
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pass
    expected = "schema_lineage_conflict" if tamper == "critic_collision" else "unknown_schema"
    assert caught.value.code == expected
    assert store.path.read_bytes() == before


def test_future_combined_schema_refused_without_mutation(tmp_path, monkeypatch):
    store = v15_store(tmp_path / "future-combined")
    with store.connect() as conn:
        conn.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
    before = store.path.read_bytes()
    monkeypatch.setattr(store, "_migration", lambda *_: pytest.fail("Future schema cannot be migrated"))
    with pytest.raises(LedgerError) as caught:
        with store.connect():
            pass
    assert caught.value.code == "schema_too_new"
    assert store.path.read_bytes() == before


def test_version_and_layout_validation_share_one_read_snapshot(tmp_path):
    store = v15_store(tmp_path / "consistent-snapshot")
    with closing(sqlite3.connect(store.path, isolation_level=None)) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        assert store._checked_version(conn) == 3
        assert statements[0] == "BEGIN"
        assert statements[-1] == "ROLLBACK"
        assert not conn.in_transaction
        statements.clear()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO ledger_meta VALUES ('pending-marker','pending')")
        assert store._checked_version(conn) == 3
        assert conn.in_transaction
        assert "ROLLBACK" not in statements
        conn.rollback()
