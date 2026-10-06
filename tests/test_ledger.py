"""Synthetic persistence, ownership, evidence, learning, and operational tests."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError
from review_ledger.reports import export
from review_ledger.service import Ledger
from review_ledger.storage import Store

REPO = "synthetic/example"


def test_first_use_without_history(ledger, opened, actor):
    status = ledger.status(REPO, opened["id"], actor)
    assert status["observations"] == []
    assert status["assessments"] == []
    assert status["historical_runs"] == 0
    recalled = Learning(ledger.store).recall(ledger.scope(REPO), opened["id"])
    assert recalled["lessons"] == []
    assert not any(recalled["omitted"].values())


def test_restart_persistence_and_relations(ledger, opened, observed, actor):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic repeat effect"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic observation",
        "limitations": "Reported by agent", "observation_ids": [observed]}, "assessment")
    restarted = Ledger(Store(ledger.store.data_dir, ledger.store.profile_key), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    result = json.loads(export(restarted, REPO, opened["id"], actor, format="json")["content"])
    assert result["assessments"][0]["sources"][0]["observation_id"] == observed
    assert result["observations"][0]["provenance"] == "agent_reported"
    assert result["snapshot"]["head_sha"] == "a" * 40
    assert result["export_format_version"] == 1


def test_second_session_follows_then_resumes(ledger, synthetic_snapshot, opened, actor):
    second = Actor("synthetic-trusted-session-b")
    followed = ledger.open(synthetic_snapshot, second, "follow")["run"]
    assert followed["id"] == opened["id"]
    assert not followed["can_write"]
    with pytest.raises(LedgerError, match="unowned"):
        ledger.run_action(REPO, opened["id"], second, 1, "acquire", "bad-acquire")
    paused = ledger.run_action(REPO, opened["id"], actor, 1, "pause", "pause", "Synthetic evidence budget exhausted")["run"]
    assert paused["owner_session"] is None
    acquired = ledger.run_action(REPO, opened["id"], second, paused["generation"], "acquire", "resume")["run"]
    assert acquired["owner_session"] == second.session_id
    assert acquired["generation"] == 3


def test_idempotency_scope_operation_conflict(ledger, opened, actor, observation_data):
    one = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "same")
    two = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "same")
    assert one == two
    with pytest.raises(LedgerError) as exc:
        ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "summary": "Changed"}, "same")
    assert exc.value.code == "idempotency_conflict"
    assert len(ledger.status(REPO, opened["id"], actor)["observations"]) == 1
    ledger.run_action(REPO, opened["id"], actor, 1, "release", "same")  # different operation scope


def test_repository_profile_isolation(ledger, synthetic_snapshot, opened, actor, observed, tmp_path):
    other = {**synthetic_snapshot, "repository_id": 1002, "repository_node_id": "synthetic-other", "repository_full_name": "synthetic/other"}
    other_run = ledger.open(other, actor, "other")["run"]
    with pytest.raises(LedgerError) as exc:
        ledger.status("synthetic/other", opened["id"], actor)
    assert exc.value.code == "scope_not_found"
    finding = ledger.record("synthetic/other", other_run["id"], actor, 1, "finding", {"claim": "Other scope"}, "finding")["finding_id"]
    with pytest.raises(LedgerError):
        ledger.record("synthetic/other", other_run["id"], actor, 1, "assessment", {
            "finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Wrong scope", "limitations": "Synthetic", "observation_ids": [observed]}, "bad-ref")
    isolated = Ledger(Store(tmp_path / "profile-b", "synthetic-profile-b"), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    with pytest.raises(LedgerError):
        isolated.status(REPO, opened["id"], actor)
    with pytest.raises(LedgerError) as exc:
        with Store(ledger.store.data_dir, "wrong-profile").connect():
            pass
    assert exc.value.code == "profile_mismatch"


@pytest.mark.parametrize("change", ["head_sha", "base_sha", "config", "skill"])
def test_snapshot_change_no_current_inheritance(ledger, synthetic_snapshot, opened, observed, actor, change):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [observed]}, "assessment")
    new_snapshot = dict(synthetic_snapshot)
    if change in ("head_sha", "base_sha"):
        new_snapshot[change] = "e" * 40
    elif change == "config":
        ledger.config = {"context_budget": 7000}
    else:
        ledger.skill_hash = "f" * 64
    current = ledger.open(new_snapshot, actor, "changed")["run"]
    assert current["id"] != opened["id"]
    assert ledger.status(REPO, current["id"], actor)["assessments"] == []
    assert ledger.status(REPO, opened["id"], actor)["assessments"][0]["freshness"] == "historical"


def test_fencing_rejects_late_owner(ledger, opened, actor, observation_data):
    moved = ledger.operator_transfer(REPO, opened["id"], "synthetic-trusted-session-b", 1, "Explicit synthetic recovery", "transfer")["run"]
    assert moved["generation"] == 2
    for operation in (lambda: ledger.run_action(REPO, opened["id"], actor, 1, "complete", "late-complete"),
                      lambda: ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "late-record")):
        with pytest.raises(LedgerError) as exc:
            operation()
        assert exc.value.code == "ownership_conflict"
    assert ledger.status(REPO, opened["id"], actor)["observations"] == []


@pytest.mark.parametrize("outcome", ["infrastructure_failure", "timeout", "skipped", "incomplete"])
def test_nonbehavior_evidence_cannot_support_or_fix(ledger, opened, actor, observation_data, outcome):
    obs = ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "outcome": outcome}, "observation")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic"}, "finding")["finding_id"]
    for state in ("supported", "refuted"):
        with pytest.raises(LedgerError) as exc:
            ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": state, "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [obs]}, state)
        assert exc.value.code == "ineligible_evidence"


def test_provenance_and_incomplete_behavior(ledger, opened, actor, observation_data):
    with pytest.raises(LedgerError) as exc:
        ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "provenance": "host_observed"}, "forged")
    assert exc.value.code == "invalid_input"
    incomplete = {k: v for k, v in observation_data.items() if k != "environment"}
    obs = ledger.record(REPO, opened["id"], actor, 1, "observation", incomplete, "incomplete")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic"}, "finding")["finding_id"]
    with pytest.raises(LedgerError) as exc:
        ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [obs]}, "assess")
    assert exc.value.code == "incomplete_behavior_report"


def test_inspection_support_is_labelled(ledger, opened, actor):
    obs = ledger.record(REPO, opened["id"], actor, 1, "observation", {"kind": "inspection", "outcome": "inspection", "summary": "Synthetic inspection", "limitations": "No execution"}, "inspect")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic inspected claim"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": "supported", "basis": "inspection", "rationale": "Synthetic inspected source", "limitations": "No execution", "observation_ids": [obs]}, "assess")
    report = export(ledger, REPO, opened["id"], actor, format="markdown")["content"]
    assert "basis: inspection (agent-reported)" in report


def test_complete_learning_cycle(ledger, synthetic_snapshot, opened, actor, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    proposed = learning.propose(scope, opened["id"], actor, 1, lesson_data, "lesson")
    assert proposed["state"] == "candidate"
    assert learning.recall(scope, opened["id"])["lessons"] == []
    with pytest.raises(LedgerError):
        learning.propose(scope, opened["id"], actor, 1, {**lesson_data, "approved": True}, "autoapprove")
    learning.operator(scope, proposed["version_id"], "approve", "Synthetic operator evaluation accepted conditions and exclusions", "approve")
    different_case = ledger.open({**synthetic_snapshot, "number": 8}, actor, "second-case")["run"]
    recalled = learning.recall(scope, different_case["id"], tags=["retry"])
    assert [v["id"] for v in recalled["lessons"]] == [proposed["version_id"]]
    used = learning.use(scope, different_case["id"], actor, 1, proposed["version_id"], "applicable", "Synthetic repeated effect present", "use")
    result = learning.result(scope, different_case["id"], actor, 1, used["use_id"], {
        "usefulness": "useful", "behavioral_result": "hypothesis_refuted", "execution_block": "none", "explanation": "Synthetic refutation narrowed the investigation"}, "result")
    assert result["version_id"] == proposed["version_id"]
    learning.operator(scope, proposed["version_id"], "restrict", "Synthetic architecture excludes this prior scope; revise conditions", "restrict")
    assert learning.recall(scope, different_case["id"])["lessons"] == []
    revised = learning.propose(scope, different_case["id"], actor, 1, {**lesson_data, "previous_version_id": proposed["version_id"], "conditions": ["Synthetic narrower storage architecture"]}, "revise")
    learning.operator(scope, revised["version_id"], "approve", "Synthetic narrower case evaluated", "approve-v2")
    report = json.loads(export(ledger, REPO, different_case["id"], actor, format="json")["content"])
    assert report["lesson_uses"][0]["version_id"] == proposed["version_id"]
    assert report["lesson_uses"][0]["eligible_now"] is False
    assert report["lesson_uses"][0]["behavioral_result"] == "hypothesis_refuted"
    learning.operator(scope, revised["version_id"], "suspend", "Synthetic follow-up required", "suspend")
    assert learning.recall(scope, different_case["id"])["lessons"] == []


def test_invalidation_revokes_before_recall_use_and_export(ledger, opened, actor, observed, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    candidate = learning.propose(scope, opened["id"], actor, 1, lesson_data, "lesson")["version_id"]
    learning.operator(scope, candidate, "approve", "Synthetic evaluation", "approve")
    learning.use(scope, opened["id"], actor, 1, candidate, "applicable", "Synthetic", "use")
    ledger.operator_invalidate(REPO, observed, "Synthetic source corrected", "invalidate")
    assert learning.recall(scope, opened["id"])["lessons"] == []
    with pytest.raises(LedgerError) as exc:
        learning.use(scope, opened["id"], actor, 1, candidate, "applicable", "Synthetic", "another-use")
    assert exc.value.code == "lesson_not_eligible"
    report = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert report["lesson_uses"][0]["eligible_now"] is False


def test_blocked_outcome_is_not_behavior_failure(ledger, opened, actor, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    candidate = learning.propose(scope, opened["id"], actor, 1, lesson_data, "lesson")["version_id"]
    learning.operator(scope, candidate, "approve", "Synthetic", "approve")
    use = learning.use(scope, opened["id"], actor, 1, candidate, "uncertain", "Synthetic", "use")
    with pytest.raises(LedgerError) as exc:
        learning.result(scope, opened["id"], actor, 1, use["use_id"], {"usefulness": "useful", "behavioral_result": "failure_observed", "execution_block": "infrastructure", "explanation": "Synthetic setup failed"}, "bad-result")
    assert exc.value.code == "invalid_outcome"
    learning.result(scope, opened["id"], actor, 1, use["use_id"], {"usefulness": "inconclusive", "behavioral_result": "inconclusive", "execution_block": "infrastructure", "explanation": "Synthetic setup failed"}, "result")


def test_recall_boundaries_deterministic_no_fts(ledger, opened, actor, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    for index in range(7):
        item = learning.propose(scope, opened["id"], actor, 1, {**lesson_data, "question": f"Synthetic question {index} for retry?"}, f"lesson-{index}")
        learning.operator(scope, item["version_id"], "approve", "Synthetic", f"approve-{index}")
    one = learning.recall(scope, opened["id"], context_budget=20000)
    two = learning.recall(scope, opened["id"], context_budget=20000)
    assert one == two
    assert len(one["lessons"]) == 5
    assert one["omitted"]["result_limit"]
    tiny = learning.recall(scope, opened["id"], context_budget=500)
    assert tiny["context_chars"] <= 500
    assert tiny["omitted"]["budget"]
    assert learning.recall(scope, opened["id"], terms=["unmatched-synthetic"])["lessons"] == []


def test_future_schema_refused_without_modification(tmp_path):
    root = tmp_path / "future"
    root.mkdir()
    path = root / "review-ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with pytest.raises(LedgerError) as exc:
        with Store(root, "future").connect():
            pass
    assert exc.value.code == "schema_too_new"
    assert path.read_bytes() == before
    assert not (root / "review-ledger.sqlite3-wal").exists()


def test_backup_restoration_and_missing_artifact(ledger, opened, actor, observation_data):
    record = ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "artifact_text": "Synthetic short log"}, "artifact")
    artifact = record["artifact_id"]
    assert ledger.store.artifact_status(artifact)["state"] == "present"
    backup = ledger.store.backup()
    assert backup["restore_verified"]
    with closing(sqlite3.connect(backup["path"])) as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    (ledger.store.data_dir / "artifacts" / artifact).unlink()
    report = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert report["observations"][0]["artifact"]["state"] == "missing"
    with pytest.raises(LedgerError):
        ledger.store.artifact_status("../other")


def test_artifact_symlink_refused(ledger, tmp_path):
    ledger.store.data_dir.mkdir(parents=True)
    (ledger.store.data_dir / "artifacts").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(LedgerError) as exc:
        ledger.store.put_artifact("Synthetic")
    assert exc.value.code == "unsafe_path"


def test_export_and_status_pagination(ledger, opened, actor, observation_data):
    for index in range(3):
        ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, f"obs-{index}")
    status = ledger.status(REPO, opened["id"], actor, limit=1)
    assert len(status["observations"]) == 1
    assert status["next_offset"] == 1
    output = export(ledger, REPO, opened["id"], actor, format="json", limit=1)
    assert output["omitted"]["observations"]
    assert output["next_offset"] == 1
    with pytest.raises(LedgerError) as exc:
        export(ledger, REPO, opened["id"], actor, format="json", max_chars=2000)
    assert exc.value.code == "export_budget_exceeded"


def _process_open(root, snapshot, session, start, queue):
    ledger = Ledger(Store(Path(root), "synthetic-multiprocess"), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    start.wait(10)
    try:
        result = ledger.open(snapshot, Actor(session), "open-" + session)
        queue.put(("ok", result["run"]["id"], result["run"]["owner_session"]))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc)))


def _process_acquire(root, run_id, session, generation, start, queue):
    ledger = Ledger(Store(Path(root), "synthetic-multiprocess"), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    start.wait(10)
    try:
        result = ledger.run_action(REPO, run_id, Actor(session), generation, "acquire", "acquire-" + session)
        queue.put(("ok", result["run"]["owner_session"]))
    except LedgerError as exc:
        queue.put(("error", exc.code))


def _two_processes(target, args):
    context = multiprocessing.get_context("spawn")
    start, queue = context.Event(), context.Queue()
    processes = [context.Process(target=target, args=(*a, start, queue)) for a in args]
    try:
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
            if process.pid is not None and not process.is_alive():
                process.close()
        queue.close()
        queue.join_thread()


def test_real_two_process_initialization_active_reuse_and_owner(tmp_path, synthetic_snapshot):
    root = str(tmp_path / "multiprocess")
    results = _two_processes(_process_open, [(root, synthetic_snapshot, "synthetic-a"), (root, synthetic_snapshot, "synthetic-b")])
    assert [r[0] for r in results] == ["ok", "ok"], results
    assert results[0][1:] == results[1][1:]
    run_id, owner = results[0][1:]
    ledger = Ledger(Store(Path(root), "synthetic-multiprocess"), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    released = ledger.run_action(REPO, run_id, Actor(owner), 1, "release", "release")["run"]
    acquisitions = _two_processes(_process_acquire, [(root, run_id, "synthetic-c", released["generation"]), (root, run_id, "synthetic-d", released["generation"])])
    assert sorted(r[0] for r in acquisitions) == ["error", "ok"], acquisitions
    assert next(r[1] for r in acquisitions if r[0] == "error") == "ownership_conflict"
