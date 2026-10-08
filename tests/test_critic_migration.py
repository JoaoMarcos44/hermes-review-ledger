"""Upgrade real V2 SQL layouts, preserve data, and restore criticism history."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import shutil
import sqlite3

import pytest

import review_ledger.storage as storage
from review_ledger.critic import Critic, CriticConfig, CriticResult
from review_ledger.context import Context
from review_ledger.learning import Learning
from review_ledger.skills import Skills
from review_ledger.usage import Usage
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
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
        if version == 4:
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION


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
        assert list(pool.map(migrate, range(4))) == [storage.SCHEMA_VERSION] * 4
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_restores_all_adaptive_and_critic_tables(ledger, opened, actor, observed, lesson_data, tmp_path):
    fid = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic claim"}, "finding")["finding_id"]
    aid = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": fid, "state": "supported", "basis": "behavior", "rationale": "Synthetic check",
        "limitations": "Agent reported", "observation_ids": [observed]}, "original-assess")["assessment_id"]
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
    cid = critic.prepare(REPO, opened["id"], actor, 1, "prepare", [fid], observation_ids=[observed], assessment_ids=[aid])["critic_run_id"]
    assert critic.run(REPO, opened["id"], actor, 1, "run", cid)["state"] == "returned"
    with ledger.store.connect() as conn:
        objection = conn.execute("SELECT id FROM critic_objections").fetchone()[0]
    assessed = critic.assess(REPO, opened["id"], actor, 1, "assess", cid, objection,
                             "supported", "behavior", "Synthetic check", "Reported only", [observed])
    scope, learning = ledger.scope(REPO), Learning(ledger.store, improvements_enabled=True)
    candidate = learning.propose(scope, opened["id"], actor, 1,
                                 {**lesson_data, "critic_assessment_ids": [assessed["critic_assessment_id"]]}, "lesson")
    learning.operator(scope, candidate["version_id"], "approve", "Synthetic local review", "approve")
    use = learning.use(scope, opened["id"], actor, 1, candidate["version_id"], "applicable", "Synthetic use", "use")
    outcome = learning.result(scope, opened["id"], actor, 1, use["use_id"], {
        "usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
        "explanation": "Synthetic redundant context", "contribution": "redundant"}, "outcome")
    assert outcome["improvement_id"]
    package = tmp_path / "snapshot-example"
    package.mkdir()
    (package / "SKILL.md").write_text("---\nname: example\ndescription: Synthetic retry checks\n---\nPreserve full instructions.\n")
    (package / "notes.md").write_text("Synthetic immutable referenced bytes: é🔬\n")
    skill = Skills(ledger.store).register(scope, package, qualified_id="synthetic/example",
                                         references=["notes.md"], approved=True, enabled=True)
    Context(ledger, budget=24000, skills_enabled=True).prepare(REPO, opened["id"], actor, query="retry")
    assert Usage(ledger.store).record(scope, event_id="backup-event", tool="ledger_context", request_text="retry",
                                      response_text="Synthetic context", run_id=opened["id"], latency_ms=1, source="fixture")
    backup = ledger.store.backup()
    assert backup["restore_verified"]
    restored = Store(tmp_path / "restored", ledger.store.profile_key)
    restored.data_dir.mkdir()
    shutil.copyfile(backup["path"], restored.path)
    with ledger.store.connect() as original, restored.connect() as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
        for table in sorted(storage.V4_TABLES):
            rows = [tuple(r) for r in original.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            assert rows, f"Backup fixture must exercise {table}"
            assert rows == [tuple(r) for r in copy.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        assert copy.execute("PRAGMA foreign_key_check").fetchall() == []
        assert Skills.version(copy, scope, skill["id"], require_enabled=True)["resources"] == {
            "notes.md": "Synthetic immutable referenced bytes: é🔬\n"}
