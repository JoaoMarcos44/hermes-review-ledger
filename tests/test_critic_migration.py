"""Upgrade real V2 SQL layouts, preserve data, and restore criticism history."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

import review_ledger.storage as storage
from review_ledger.critic import Critic, CriticConfig, CriticResult
from review_ledger.models import LedgerError
from review_ledger.service import Ledger
from review_ledger.storage import Store

REPO = "synthetic/example"


@pytest.fixture
def v2_store(tmp_path, monkeypatch, synthetic_snapshot, actor, observation_data):
    store = Store(tmp_path / "v2", "synthetic-v2-profile")
    # Initialize through the actual original migrations, never by dropping V3 tables.
    with monkeypatch.context() as patch:
        patch.setattr(storage, "SCHEMA_VERSION", 2)
        ledger = Ledger(store, [REPO], skill_version="0.1.0", skill_hash="d" * 64)
        opened = ledger.open(synthetic_snapshot, actor, "open-v2")["run"]
        oid = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "record-v2")["observation_id"]
        with store.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
            assert not conn.execute("SELECT name FROM sqlite_master WHERE name='critic_runs'").fetchone()
    return store, opened, oid


def test_actual_v2_upgrade_preserves_sources_journal_and_receipts(v2_store):
    store, opened, oid = v2_store
    with closing(sqlite3.connect(store.path)) as conn:
        before = conn.execute("SELECT * FROM observations").fetchall()
        receipts = conn.execute("SELECT * FROM idempotency ORDER BY request_key").fetchall()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert [tuple(r) for r in conn.execute("SELECT * FROM observations")] == before
        assert [tuple(r) for r in conn.execute("SELECT * FROM idempotency ORDER BY request_key")] == receipts
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == journal
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert storage.CRITIC_TABLES <= tables


def test_migration_failure_rolls_back_all_ddl_and_version(v2_store, monkeypatch):
    store, opened, oid = v2_store
    original = Store._migration
    def broken(conn, version):
        original(conn, version)
        if version == 3:
            raise RuntimeError("Synthetic interruption after DDL")
    with monkeypatch.context() as patch:
        patch.setattr(Store, "_migration", staticmethod(broken))
        with pytest.raises(RuntimeError, match="Synthetic interruption"):
            with store.connect():
                pass
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT id FROM observations").fetchone()[0] == oid
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='critic_runs'").fetchone()
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_wrong_profile_cannot_migrate_v2(v2_store):
    store, opened, oid = v2_store
    wrong = Store(store.data_dir, "different-profile")
    with pytest.raises(LedgerError) as exc:
        with wrong.connect():
            pass
    assert exc.value.code == "profile_mismatch"
    with closing(sqlite3.connect(store.path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='critic_runs'").fetchone()


def test_concurrent_v2_initialization(v2_store):
    store, opened, oid = v2_store
    def migrate(_):
        with Store(store.data_dir, store.profile_key).connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(migrate, range(4))) == [3] * 4
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_restores_packet_opinion_assessment_and_links(ledger, opened, actor, observed, tmp_path):
    fid = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic claim"}, "finding")["finding_id"]
    class Fake:
        def evaluate(self, packet):
            return CriticResult(json.dumps({"items": [{"finding_id": fid, "position": "challenge",
                "rationale": "Recorded contract is unknown.", "contract_status": "unknown",
                "contract_statement": "What delivery deadline applies?", "reference_ids": [fid],
                "objections": [{"category": "contract", "claim": "Delay alone is inconclusive.",
                    "counter_hypothesis": "Delivery may be eventual.", "recommended_check": "Record the deadline.",
                    "would_withdraw_if": "Declared deadline was exceeded.", "reference_ids": [fid]}],
                "missing_information": [], "limitations": ["Synthetic"]}]}))
    critic = Critic(ledger, CriticConfig(enabled=True, provider="synthetic", model="fixture", authorized_repositories=(REPO,)), Fake())
    cid = critic.prepare(REPO, opened["id"], actor, 1, "prepare", [fid], observation_ids=[observed])["critic_run_id"]
    assert critic.run(REPO, opened["id"], actor, 1, "run", cid)["state"] == "returned"
    with ledger.store.connect() as conn:
        objection = conn.execute("SELECT id FROM critic_objections").fetchone()[0]
    critic.assess(REPO, opened["id"], actor, 1, "assess", cid, objection, "supported", "behavior", "Synthetic check", "Reported only", [observed])
    backup = ledger.store.backup()
    assert backup["restore_verified"]
    restored = Store(tmp_path / "restored", ledger.store.profile_key)
    restored.data_dir.mkdir()
    shutil.copyfile(backup["path"], restored.path)
    with ledger.store.connect() as original, restored.connect() as copy:
        for table in sorted(storage.CRITIC_TABLES):
            assert [tuple(r) for r in original.execute(f"SELECT * FROM {table} ORDER BY rowid")] == [tuple(r) for r in copy.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
