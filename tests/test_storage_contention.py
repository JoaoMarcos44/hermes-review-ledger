"""Deterministic native SQLite contention and copied-snapshot regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from review_ledger.models import LedgerError, canonical
from review_ledger.skills import Skills, _identity
from review_ledger import storage


def test_backup_reader_blocks_commit_then_same_key_retry_is_exactly_once(ledger, opened, monkeypatch):
    store = ledger.store
    store.busy_timeout_ms = 20
    scope = ledger.scope("synthetic/example")
    writer_ready, reader_ready, writer_finished = Event(), Event(), Event()
    connect = sqlite3.connect
    writes = []

    class HeldBackupReader(sqlite3.Connection):
        def backup(self, target, **kwargs):
            # Hold a real DELETE-mode read snapshot until COMMIT exhausts the
            # writer's short busy budget. Events control ordering, not sleeps.
            self.execute("BEGIN")
            self.execute("SELECT count(*) FROM ledger_meta").fetchone()
            reader_ready.set()
            try:
                assert writer_finished.wait(timeout=10)
                return super().backup(target, **kwargs)
            finally:
                self.rollback()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=HeldBackupReader, **kw))

    def operation(connection):
        writes.append("called")
        connection.execute("INSERT INTO ledger_meta VALUES ('contention-marker','committed')")
        writer_ready.set()
        assert reader_ready.wait(timeout=10)
        return {"state": "recorded"}

    def write():
        try:
            return store.write(scope, "fixture", opened["id"], "commit-retry", {}, operation)
        finally:
            writer_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert writer_ready.wait(timeout=10)
            backup = pool.submit(store.backup)
            with pytest.raises(LedgerError) as caught:
                writer.result(timeout=15)
            assert caught.value.code == "storage_busy"
            assert caught.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
            result = backup.result(timeout=15)
        finally:
            reader_ready.set()
            writer_finished.set()

    assert writes == ["called"]  # No implicit replay of the write body.
    assert result["restore_verified"] is True
    for path in (store.path, result["path"]):
        with closing(connect(path)) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert connection.execute("SELECT value FROM ledger_meta WHERE key='contention-marker'").fetchall() == []
            assert connection.execute("SELECT count(*) FROM idempotency WHERE request_key='commit-retry'").fetchone()[0] == 0

    receipt = store.write(scope, "fixture", opened["id"], "commit-retry", {}, operation)
    assert store.write(scope, "fixture", opened["id"], "commit-retry", {}, operation) == receipt
    assert writes == ["called", "called"]
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM ledger_meta WHERE key='contention-marker'").fetchall()[0][0] == "committed"
        assert connection.execute("SELECT count(*) FROM idempotency WHERE request_key='commit-retry'").fetchone()[0] == 1


@pytest.fixture
def immutable_snapshot(ledger, opened):
    """Seed valid synthetic SQLite rows without relying on platform import APIs."""
    scope = ledger.scope("synthetic/example")
    metadata = {"name": "example", "description": "Synthetic backup fixture"}
    instructions = "Keep the required reference with these instructions."
    resources = {"notes.md": "Required synthetic reference"}
    version_id = "skill_backup_fixture"
    with ledger.store.connect() as connection, ledger.store.transaction(connection):
        connection.execute("INSERT INTO optional_skill_versions VALUES (?,?,?,?,?,?,?,?,?)", (
            version_id, scope.repository_id, "synthetic/example", 1,
            _identity(metadata, instructions, resources), canonical(metadata), instructions, 1, "synthetic-time",
        ))
        connection.execute("INSERT INTO optional_skill_resources VALUES (?,?,?,?,?)", (
            scope.repository_id, version_id, "notes.md", resources["notes.md"],
            hashlib.sha256(resources["notes.md"].encode()).hexdigest(),
        ))
        assert Skills.version(connection, scope, version_id)["resources"] == resources
    return version_id


@pytest.mark.parametrize("tamper", ["missing_resource", "changed_instruction"])
def test_backup_validates_the_copied_skill_snapshot(ledger, immutable_snapshot, monkeypatch, tamper):
    connect = sqlite3.connect

    class SourceChangedBeforeCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            # Deterministically model a source change after validation but
            # before the online backup chooses its snapshot. These mutations
            # keep SQLite/FK integrity valid, but invalidate the skill digest.
            with closing(connect(ledger.store.path, isolation_level=None)) as writer:
                if tamper == "missing_resource":
                    writer.execute("DELETE FROM optional_skill_resources WHERE version_id=?", (immutable_snapshot,))
                else:
                    writer.execute("UPDATE optional_skill_versions SET instructions='Changed' WHERE id=?", (immutable_snapshot,))
            return super().backup(target, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=SourceChangedBeforeCopy, **kw))
    with pytest.raises(LedgerError) as caught:
        ledger.store.backup()
    assert caught.value.code == "artifact_corrupt"
    assert list((ledger.store.data_dir / "backups").iterdir()) == []


def test_backup_verification_is_independent_of_later_source_changes(ledger, immutable_snapshot, monkeypatch):
    connect = sqlite3.connect

    class SourceChangedAfterCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            result = super().backup(target, **kwargs)
            with closing(connect(ledger.store.path, isolation_level=None)) as writer:
                writer.execute("DELETE FROM optional_skill_resources WHERE version_id=?", (immutable_snapshot,))
            return result

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=SourceChangedAfterCopy, **kw))
    result = ledger.store.backup()
    assert result["restore_verified"] is True
    scope = ledger.scope("synthetic/example")
    with closing(connect(result["path"])) as copied:
        copied.row_factory = sqlite3.Row
        assert Skills.version(copied, scope, immutable_snapshot)["resources"] == {"notes.md": "Required synthetic reference"}
    with ledger.store.connect() as source:
        with pytest.raises(LedgerError) as caught:
            Skills.version(source, scope, immutable_snapshot)
        assert caught.value.code == "artifact_corrupt"


@pytest.mark.parametrize("tamper,code", [("schema", "schema_too_new"), ("profile", "profile_mismatch")])
def test_backup_rechecks_schema_and_profile_on_copied_snapshot(ledger, opened, monkeypatch, tamper, code):
    connect = sqlite3.connect

    class FencesChangedBeforeCopy(sqlite3.Connection):
        def backup(self, target, **kwargs):
            with closing(connect(ledger.store.path, isolation_level=None)) as writer:
                if tamper == "schema":
                    writer.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
                else:
                    writer.execute("UPDATE ledger_meta SET value='different-profile' WHERE key='profile_key'")
            return super().backup(target, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=FencesChangedBeforeCopy, **kw))
    with pytest.raises(LedgerError) as caught:
        ledger.store.backup()
    assert caught.value.code == code
    assert list((ledger.store.data_dir / "backups").iterdir()) == []


def test_backup_exclusive_contention_obeys_progress_deadline_and_cleans_up(ledger, opened, monkeypatch):
    ledger.store.busy_timeout_ms = 20
    connect = sqlite3.connect
    statuses = []
    # Advance the copy budget only, without sleeps or altering SQLite's real
    # busy timeout. The source is genuinely held by another native connection.
    clock = iter((0, 11))
    monkeypatch.setattr(storage, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=storage.time.sleep))

    class BlockedBackup(sqlite3.Connection):
        def backup(self, target, **kwargs):
            progress = kwargs.pop("progress")

            def tracked_progress(status, remaining, total):
                statuses.append(status)
                progress(status, remaining, total)

            with closing(connect(ledger.store.path, isolation_level=None)) as blocker:
                blocker.execute("BEGIN EXCLUSIVE")
                try:
                    return super().backup(target, progress=tracked_progress, **kwargs)
                finally:
                    blocker.rollback()

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=BlockedBackup, **kw))
    with pytest.raises(LedgerError) as caught:
        ledger.store.backup()
    assert caught.value.code == "backup_timeout"
    assert statuses == [sqlite3.SQLITE_BUSY]
    assert list((ledger.store.data_dir / "backups").iterdir()) == []
    with ledger.store.connect() as source:
        assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
