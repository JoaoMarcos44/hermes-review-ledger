"""Native filesystem and connection-lifetime contracts on every supported OS."""
from contextlib import closing
import shutil
import sqlite3

import pytest

from review_ledger.models import LedgerError
from review_ledger.service import Ledger
from review_ledger.storage import Store


def test_backup_closes_all_connections(ledger, opened, monkeypatch):
    """A transaction context is not a connection-lifetime context."""
    connections = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    try:
        result = ledger.store.backup()
        assert result["restore_verified"] is True
        assert len(connections) == 3
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def test_backup_and_artifacts_in_unicode_profile(tmp_path, synthetic_snapshot, actor):
    root = tmp_path / "Perfil João 中文 with spaces" / "ledger"
    ledger = Ledger(
        Store(root, "unicode-profile"), ["synthetic/example"],
        skill_version="0.1.0", skill_hash="d" * 64,
    )
    run = ledger.open(synthetic_snapshot, actor, "open")["run"]
    content = "Relatório: ação, café, 中文, 🧪\n"
    observation = {
        "kind": "inspection", "outcome": "inspection",
        "summary": "Unicode filesystem fixture", "limitations": "Synthetic",
        "artifact_text": content,
    }
    recorded = ledger.record(
        "synthetic/example", run["id"], actor, 1, "observation", observation, "record",
    )
    artifact = root / "artifacts" / recorded["artifact_id"]
    assert artifact.read_text(encoding="utf-8") == content
    assert ledger.store.artifact_status(recorded["artifact_id"])["bytes"] == len(content.encode("utf-8"))

    backup = ledger.store.backup()
    with closing(sqlite3.connect(backup["path"])) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1

    # A completed operation must release all handles before files can be moved
    # or removed. These are real native operations, not a mocked platform check.
    renamed = root.with_name("ledger moved")
    root.rename(renamed)
    shutil.rmtree(renamed)
    assert not renamed.exists()


def test_failed_backup_does_not_publish_or_leave_staging(ledger, opened, monkeypatch):
    from review_ledger import storage

    def interrupted_copy(source, destination):
        raise OSError("Synthetic disk-copy failure")

    monkeypatch.setattr(storage.shutil, "copyfile", interrupted_copy)
    with pytest.raises(OSError, match="Synthetic disk-copy failure"):
        ledger.store.backup()
    assert list((ledger.store.data_dir / "backups").iterdir()) == []


def test_native_writer_contention_can_retry_without_duplicate(ledger, opened, actor):
    ledger.store.busy_timeout_ms = 20
    arguments = (
        "synthetic/example", opened["id"], actor, 1,
        "finding", {"claim": "Synthetic retry after contention"}, "same-retry-key",
    )
    with ledger.store.connect() as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(LedgerError) as exc:
                ledger.record(*arguments)
            assert exc.value.code == "storage_busy"
        finally:
            blocker.rollback()

    first = ledger.record(*arguments)
    assert ledger.record(*arguments) == first
    status = ledger.status("synthetic/example", opened["id"], actor)
    assert [finding["id"] for finding in status["findings"]] == [first["finding_id"]]


def test_failed_writer_rolls_back_data_and_receipt(ledger, opened):
    scope = ledger.scope("synthetic/example")

    def interrupted_write(connection):
        connection.execute("UPDATE runs SET note='Must roll back' WHERE id=?", (opened["id"],))
        raise RuntimeError("Synthetic interrupted operation")

    with pytest.raises(RuntimeError, match="Synthetic interrupted operation"):
        ledger.store.write(scope, "fixture", opened["id"], "failed-write", {}, interrupted_write)

    with ledger.store.connect() as connection:
        assert connection.execute("SELECT note FROM runs WHERE id=?", (opened["id"],)).fetchone()[0] == ""
        assert connection.execute("SELECT COUNT(*) FROM idempotency WHERE request_key='failed-write'").fetchone()[0] == 0


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_transaction_rolls_back_before_connection_closes(ledger, opened, failure):
    with ledger.store.connect() as connection:
        with pytest.raises(failure):
            with ledger.store.transaction(connection):
                connection.execute("UPDATE runs SET note='Must roll back' WHERE id=?", (opened["id"],))
                raise failure("Synthetic transaction interruption")
        assert connection.in_transaction is False
        assert connection.execute("SELECT note FROM runs WHERE id=?", (opened["id"],)).fetchone()[0] == ""
