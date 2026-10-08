"""Functional journal-policy tests; no fault or vulnerability reproduction."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import Barrier, Event

import pytest

from review_ledger import storage
from review_ledger.models import LedgerError
from review_ledger.storage import Store, sqlite_has_wal_reset_fix


@pytest.mark.parametrize("version,fixed", [
    ((3, 35, 0), False), ((3, 43, 2), False),
    ((3, 44, 5), False), ((3, 44, 6), True), ((3, 44, 7), False),
    ((3, 45, 0), False), ((3, 49, 3), False),
    ((3, 50, 6), False), ((3, 50, 7), True), ((3, 50, 8), False),
    ((3, 51, 0), False), ((3, 51, 2), False), ((3, 51, 3), True),
    ((3, 51, 4), True), ((3, 52, 0), True),
])
def test_wal_runtime_allowlist_uses_exact_documented_backports(version, fixed):
    assert sqlite_has_wal_reset_fix(version) is fixed


@pytest.mark.parametrize("version", [(3, 35, 0), (3, 44, 5), (3, 50, 6), (3, 51, 3)])
def test_new_database_explicitly_uses_delete_on_supported_runtimes(tmp_path, monkeypatch, version):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    store = Store(tmp_path / "profile", "synthetic-policy")
    for _ in range(2):
        with store.connect() as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    assert store.path.read_bytes()[18:20] == b"\x01\x01"
    assert not store.path.with_name(store.path.name + "-wal").exists()


def test_runtime_below_minimum_rejected_without_creating_database(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 1))
    store = Store(tmp_path / "profile", "synthetic-policy")
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == "unsupported_sqlite"
    assert not store.path.exists()


def _legacy_wal(store):
    with store.connect():
        pass
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert store.path.read_bytes()[18:20] == b"\x02\x02"


@pytest.mark.parametrize("version", [(3, 35, 0), (3, 44, 5), (3, 45, 0), (3, 50, 6), (3, 51, 2)])
def test_unsafe_legacy_wal_rejected_before_sqlite_open(tmp_path, monkeypatch, version):
    store = Store(tmp_path / "profile", "synthetic-policy")
    _legacy_wal(store)
    before = store.path.read_bytes()
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)

    def forbidden_open(*args, **kwargs):
        pytest.fail("An unsupported runtime must not open an existing WAL database")

    monkeypatch.setattr(sqlite3, "connect", forbidden_open)
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == "unsupported_sqlite_wal"
    assert "Stop all ledger sessions" in str(exc.value)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_sidecar_signal_is_conservatively_refused_on_unsafe_runtime(tmp_path, monkeypatch, suffix):
    store = Store(tmp_path / "profile", "synthetic-policy")
    with store.connect():
        pass
    # An empty synthetic leftover is enough to require operator review. SQLite
    # is never asked to parse it, and neither it nor the main file is modified.
    sidecar = store.path.with_name(store.path.name + suffix)
    sidecar.touch()
    before = store.path.read_bytes()
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == "unsupported_sqlite_wal"
    assert store.path.read_bytes() == before
    assert sidecar.exists() and sidecar.stat().st_size == 0


@pytest.mark.parametrize("version", [(3, 44, 6), (3, 50, 7), (3, 51, 3), (3, 52, 0)])
def test_fixed_runtime_retains_existing_wal_without_migration(tmp_path, monkeypatch, version):
    store = Store(tmp_path / "profile", "synthetic-policy")
    _legacy_wal(store)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    with store.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with store.transaction(connection):
            connection.execute("INSERT INTO ledger_meta VALUES ('synthetic-marker','preserved')")
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM ledger_meta WHERE key='synthetic-marker'").fetchone()[0] == "preserved"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_sqlite_journal_mode_is_rechecked_before_initialization(tmp_path, monkeypatch):
    store = Store(tmp_path / "profile", "synthetic-policy")
    _legacy_wal(store)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    # Model a journal-mode change after the filesystem preflight without racing
    # actual database operations or trying to provoke an engine fault.
    monkeypatch.setattr(store, "_preflight_journal", lambda: None)
    monkeypatch.setattr(store, "_initialize", lambda connection: pytest.fail("Initialization must not run"))
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == "unsupported_sqlite_wal"


@pytest.mark.parametrize("journal", ["delete", "wal"])
@pytest.mark.parametrize("kind,code", [
    ("future", "schema_too_new"), ("table", "unknown_schema"),
    ("view", "unknown_schema"), ("profile", "profile_mismatch"),
])
def test_schema_and_profile_fences_preserve_journal_and_bytes(tmp_path, monkeypatch, journal, kind, code):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 3))
    store = Store(tmp_path / "profile", "synthetic-policy")
    store.data_dir.mkdir()
    if kind == "profile":
        with Store(store.data_dir, "different-synthetic-profile").connect():
            pass
    with closing(sqlite3.connect(store.path)) as connection:
        if kind == "future":
            connection.execute("PRAGMA user_version=99")
        elif kind == "table":
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        elif kind == "view":
            connection.execute("CREATE VIEW unrelated AS SELECT 1 AS value")
        assert connection.execute(f"PRAGMA journal_mode={journal}").fetchone()[0] == journal
    before = store.path.read_bytes()
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == code
    assert store.path.read_bytes() == before
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == journal


def test_journal_selection_does_not_accept_silent_wal_fallback(tmp_path, monkeypatch):
    store = Store(tmp_path / "profile", "synthetic-policy")
    with store.connect():
        pass
    connect = sqlite3.connect

    class RefusedDelete(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement.lower() == "pragma journal_mode=delete":
                # A synthetic driver refusal, not a real journal-mode change.
                return super().execute("SELECT 'wal'")
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=RefusedDelete, **kw))
    with pytest.raises(LedgerError) as exc:
        with store.connect():
            pass
    assert exc.value.code == "unsupported_journal"


def test_concurrent_initialization_keeps_delete_and_single_profile(tmp_path):
    root = tmp_path / "concurrent"
    start = Barrier(2)

    def initialize():
        store = Store(root, "synthetic-policy")
        start.wait(timeout=10)
        with store.connect() as connection:
            return (connection.execute("PRAGMA journal_mode").fetchone()[0],
                    connection.execute("SELECT count(*) FROM ledger_meta WHERE key='profile_key'").fetchone()[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(initialize), pool.submit(initialize)]]
    assert results == [("delete", 1), ("delete", 1)]


def test_concurrent_initialization_rejects_other_profile(tmp_path):
    root = tmp_path / "concurrent"
    start = Barrier(2)

    def initialize(profile):
        start.wait(timeout=10)
        try:
            with Store(root, profile).connect():
                return "ok"
        except LedgerError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize, profile) for profile in ["synthetic-a", "synthetic-b"]]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(results) == ["ok", "profile_mismatch"]


@pytest.mark.parametrize("journal", ["delete", "wal"])
def test_backup_publishes_delete_and_preserves_source_mode(tmp_path, monkeypatch, journal):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 3))
    store = Store(tmp_path / "profile", "synthetic-policy")
    with store.connect() as connection:
        connection.execute("INSERT INTO ledger_meta VALUES ('synthetic-marker','preserved')")
    if journal == "wal":
        _legacy_wal(store)
    backup = store.backup()
    assert backup["restore_verified"] is True
    path = Path(backup["path"])
    assert path.read_bytes()[18:20] == b"\x01\x01"
    assert list(path.parent.iterdir()) == [path]
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT value FROM ledger_meta WHERE key='synthetic-marker'").fetchone()[0] == "preserved"
    with store.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == journal


def test_backup_on_unsafe_legacy_wal_does_not_publish(tmp_path, monkeypatch):
    store = Store(tmp_path / "profile", "synthetic-policy")
    _legacy_wal(store)
    before = store.path.read_bytes()
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    with pytest.raises(LedgerError) as exc:
        store.backup()
    assert exc.value.code == "unsupported_sqlite_wal"
    assert store.path.read_bytes() == before
    assert list((store.data_dir / "backups").iterdir()) == []


def test_delete_backup_with_normal_writer_contention(tmp_path, monkeypatch):
    store = Store(tmp_path / "profile", "synthetic-policy", busy_timeout_ms=20)
    ready, release, backup_started = Event(), Event(), Event()
    connect = sqlite3.connect

    class TrackedBackup(sqlite3.Connection):
        def backup(self, target, **kwargs):
            backup_started.set()
            return super().backup(target, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=TrackedBackup, **kw))

    def write():
        with store.connect() as connection, store.transaction(connection):
            connection.execute("INSERT INTO ledger_meta VALUES ('synthetic-marker','committed')")
            ready.set()
            assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert ready.wait(timeout=10)
            backup = pool.submit(store.backup)
            assert backup_started.wait(timeout=10)
            release.set()
            try:
                writer.result(timeout=15)
                retry = False
            except LedgerError as exc:
                # DELETE readers can outlast this deliberately short commit
                # budget, especially on slower native filesystems. Bounded
                # backpressure with full rollback is part of the contract.
                assert exc.code == "storage_busy"
                retry = True
            result = backup.result(timeout=15)
        finally:
            release.set()
    assert result["restore_verified"] is True
    with closing(sqlite3.connect(result["path"])) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        # Online backups can consistently observe either side of a commit.
        rows = connection.execute("SELECT value FROM ledger_meta WHERE key='synthetic-marker'").fetchall()
        assert rows in ([], [("committed",)])
        if retry:
            assert rows == []  # Never publish a writer's rolled-back value.
    if retry:
        with store.connect() as connection:
            assert connection.execute("SELECT value FROM ledger_meta WHERE key='synthetic-marker'").fetchall() == []
        write()
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM ledger_meta WHERE key='synthetic-marker'").fetchone()[0] == "committed"
