"""Synthetic, no-model mechanism tests for controlled outcome improvements."""
from __future__ import annotations

import json

import pytest

from review_ledger.improvements import Improvements
from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError

REPO = "synthetic/example"


@pytest.fixture
def active(ledger, opened, actor, lesson_data):
    learning = Learning(ledger.store, improvements_enabled=True)
    scope = ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, lesson_data, "initial")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic operator approval", "approve-initial")
    return learning, scope, version


def outcome(active, opened, actor, *, applicability="applicable", **changes):
    learning, scope, version = active
    use = learning.use(scope, opened["id"], actor, opened["generation"], version, applicability, "Synthetic use", "use")
    data = {"usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
            "explanation": "Synthetic retrieval was redundant", "contribution": "redundant", **changes}
    result = learning.result(scope, opened["id"], actor, opened["generation"], use["use_id"], data, "outcome")
    return result, data


def revision(version, use_id, **changes):
    return {"target_version_id": version, "source_outcome_ids": [use_id],
            "changes": changes or {"tags": ["scoped_retry"], "conditions": ["A scoped_retry write can be replayed"]},
            "reason": "Synthetic observed context does not warrant broad retrieval", "expected_benefit": "Hypothesis: narrower retrieval avoids irrelevant guidance"}


def test_exact_improvement_changes_later_retrieval(ledger, opened, actor, active):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    improvements = Improvements(ledger.store)
    diagnosis = improvements.inspect(scope, result["improvement_id"])
    assert diagnosis["status"] == "review_needed"
    assert diagnosis["changes"] == {}
    assert diagnosis["source_outcome_ids"] == [result["use_id"]]
    assert diagnosis["aggregate"]["independent_support"] is None
    data = revision(version, result["use_id"], tags=["z_specific", "a_specific"], conditions=["Scoped replay is possible"])
    proposed = improvements.propose(scope, opened["id"], actor, 1, data, "proposal")
    assert improvements.propose(scope, opened["id"], actor, 1, data, "proposal") == proposed
    detail = improvements.inspect(scope, proposed["improvement_id"])
    assert detail["changes"]["tags"] == {"before": ["persistence", "retry"], "after": ["a_specific", "z_specific"]}
    assert learning.recall(scope, opened["id"], tags=["a_specific"])["lessons"] == []
    approved = learning.operator(scope, proposed["version_id"], "approve", "Exact synthetic change reviewed", "approve-new")
    assert learning.operator(scope, proposed["version_id"], "approve", "Exact synthetic change reviewed", "approve-new") == approved
    recalled = learning.recall(scope, opened["id"], tags=["a_specific"])["lessons"]
    assert len(recalled) == 1 and recalled[0]["id"] == proposed["version_id"]
    assert recalled[0]["conditions"] == ["Scoped replay is possible"]
    assert recalled[0]["exclusions"] == ["Deduplication is shown to cover the relevant effect"]
    assert improvements.inspect(scope, proposed["improvement_id"])["status"] == "applied"
    # A later outcome is recorded against the exact approved replacement.
    used = learning.use(scope, opened["id"], actor, 1, proposed["version_id"], "applicable", "Synthetic narrowed match", "use-new")
    helped = learning.result(scope, opened["id"], actor, 1, used["use_id"], {"usefulness": "useful", "behavioral_result": "hypothesis_refuted", "execution_block": "none", "explanation": "Useful synthetic refutation", "contribution": "useful"}, "new-outcome")
    assert helped["improvement_id"] is None


@pytest.mark.parametrize("feedback", [
    {"usefulness": "inconclusive", "contribution": "unknown", "behavioral_result": "inconclusive", "execution_block": "infrastructure"},
    {"usefulness": "useful", "contribution": "useful", "behavioral_result": "hypothesis_refuted"},
    {"usefulness": "not_useful", "contribution": "unknown"},
])
def test_inconclusive_refutation_and_undetected_feedback_do_not_invent_diagnosis(ledger, opened, actor, active, feedback):
    result, _ = outcome(active, opened, actor, **feedback)
    assert result["improvement_id"] is None
    assert Improvements(ledger.store).list(active[1], opened["id"])["improvements"] == []


def test_diagnosis_is_idempotent(ledger, opened, actor, active):
    result, data = outcome(active, opened, actor, applicability="not_applicable", contribution="unknown")
    learning, scope, _ = active
    assert learning.result(scope, opened["id"], actor, 1, result["use_id"], data, "outcome") == result
    assert len(Improvements(ledger.store).list(scope, opened["id"])["improvements"]) == 1
    assert Improvements(ledger.store).inspect(scope, result["improvement_id"])["aggregate"]["window_size"] == 1


@pytest.mark.parametrize("field", ["protocol", "permissions", "budget", "question", "source_skill", "state"])
def test_proposal_rejects_protected_fields(ledger, opened, actor, active, field):
    result, _ = outcome(active, opened, actor)
    data = revision(active[2], result["use_id"], **{field: "altered"})
    with pytest.raises(LedgerError) as caught:
        Improvements(ledger.store).propose(active[1], opened["id"], actor, 1, data, "bad")
    assert caught.value.code == "invalid_input"


def test_stale_target_approval_is_atomic(ledger, opened, actor, active, lesson_data):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    improvement = Improvements(ledger.store).propose(scope, opened["id"], actor, 1, revision(version, result["use_id"]), "revision")
    newer = learning.propose(scope, opened["id"], actor, 1, {**lesson_data, "previous_version_id": version}, "other-revision")
    learning.operator(scope, newer["version_id"], "approve", "Another change approved", "other-approve")
    with pytest.raises(LedgerError) as caught:
        learning.operator(scope, improvement["version_id"], "approve", "Stale attempted approval", "stale-approve")
    assert caught.value.code == "stale_improvement"
    assert Improvements(ledger.store).inspect(scope, improvement["improvement_id"])["eligible_now"] is False
    assert learning.recall(scope, opened["id"])["lessons"][0]["id"] == newer["version_id"]


@pytest.mark.parametrize("when", ["before_approval", "after_approval"])
def test_outcome_support_invalidation_revokes_proposal_and_guidance(ledger, opened, actor, active, observation_data, when):
    learning, scope, version = active
    support = ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "summary": "Synthetic evaluation support"}, "support")["observation_id"]
    result, _ = outcome(active, opened, actor, supporting_observation_ids=[support])
    improvements = Improvements(ledger.store)
    proposal = improvements.propose(scope, opened["id"], actor, 1, revision(version, result["use_id"]), "revision")
    if when == "after_approval":
        learning.operator(scope, proposal["version_id"], "approve", "Evaluated", "approve")
    ledger.operator_invalidate(REPO, support, "Synthetic support corrected", "invalidate")
    assert improvements.inspect(scope, proposal["improvement_id"])["eligible_now"] is False
    if when == "before_approval":
        with pytest.raises(LedgerError) as caught:
            learning.operator(scope, proposal["version_id"], "approve", "Evaluated", "approve")
        assert caught.value.code == "ineligible_source"
    else:
        assert learning.recall(scope, opened["id"])["lessons"] == []
        with pytest.raises(LedgerError):
            learning.use(scope, opened["id"], actor, 1, proposal["version_id"], "applicable", "Stale", "stale")


def test_proposal_write_requires_owner_and_generation(ledger, opened, actor, active):
    result, _ = outcome(active, opened, actor)
    improvements, scope = Improvements(ledger.store), active[1]
    for claimant, generation in [(Actor("other-session"), 1), (actor, 99)]:
        with pytest.raises(LedgerError) as caught:
            improvements.propose(scope, opened["id"], claimant, generation, revision(active[2], result["use_id"]), "bad-owner")
        assert caught.value.code == "ownership_conflict"


def test_disabled_learning_keeps_ordinary_result_without_diagnosis(ledger, opened, actor, active):
    learning, scope, version = active
    learning.improvements_enabled = False
    result, _ = outcome(active, opened, actor)
    assert result["improvement_id"] is None
    assert Improvements(ledger.store).list(scope, opened["id"])["improvements"] == []


def test_noop_revision_leaves_no_candidate(ledger, opened, actor, active):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    with pytest.raises(LedgerError):
        Improvements(ledger.store).propose(scope, opened["id"], actor, 1, revision(version, result["use_id"], tags=["retry", "persistence"]), "noop")
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lesson_versions").fetchone()[0] == 1


def test_candidate_tamper_is_rejected_at_exact_approval(ledger, opened, actor, active):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    proposal = Improvements(ledger.store).propose(scope, opened["id"], actor, 1, revision(version, result["use_id"]), "revision")
    with ledger.store.connect() as conn:
        conn.execute("UPDATE lesson_versions SET tags_json=? WHERE id=?", (json.dumps(["different"]), proposal["version_id"]))
    with pytest.raises(LedgerError) as caught:
        learning.operator(scope, proposal["version_id"], "approve", "Previously inspected proposal", "approve")
    assert caught.value.code == "stale_improvement"


def test_support_cannot_cross_run(ledger, synthetic_snapshot, opened, actor, active, observation_data):
    other = ledger.open({**synthetic_snapshot, "number": 8}, actor, "other")["run"]
    support = ledger.record(REPO, other["id"], actor, 1, "observation", observation_data, "other-support")["observation_id"]
    with pytest.raises(LedgerError) as caught:
        outcome(active, opened, actor, supporting_observation_ids=[support])
    assert caught.value.code == "ineligible_source"


def test_outcome_target_and_repository_are_exact(ledger, synthetic_snapshot, opened, actor, active, lesson_data):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    second = learning.propose(scope, opened["id"], actor, 1, lesson_data, "another-lesson")["version_id"]
    learning.operator(scope, second, "approve", "Another lesson", "another-approve")
    with pytest.raises(LedgerError) as caught:
        Improvements(ledger.store).propose(scope, opened["id"], actor, 1, revision(second, result["use_id"]), "wrong-target")
    assert caught.value.code == "ineligible_outcome"
    other = ledger.open({**synthetic_snapshot, "repository_id": 1002, "repository_node_id": "other-node", "repository_full_name": "synthetic/other"}, actor, "other-scope")["run"]
    with pytest.raises(LedgerError) as caught:
        Improvements(ledger.store).propose(ledger.scope("synthetic/other"), other["id"], actor, 1, revision(version, result["use_id"]), "wrong-scope")
    assert caught.value.code == "scope_not_found"


def test_invalidated_source_cannot_be_reused_for_new_proposal(ledger, opened, actor, active, observed):
    result, _ = outcome(active, opened, actor)
    ledger.operator_invalidate(REPO, observed, "Evidence corrected", "invalidate")
    improvements = Improvements(ledger.store)
    assert improvements.list(active[1], opened["id"])["improvements"][0]["eligible_now"] is False
    with pytest.raises(LedgerError) as caught:
        improvements.propose(active[1], opened["id"], actor, 1, revision(active[2], result["use_id"]), "invalid")
    assert caught.value.code == "stale_improvement"


def test_aggregates_are_bounded_and_repeat_pr_is_not_independent(ledger, opened, actor, active):
    learning, scope, version = active
    result, _ = outcome(active, opened, actor)
    # Fixture history simulates repeated snapshots of this same PR without
    # claiming 201 independent cases or changing actual review conclusions.
    with ledger.store.connect() as conn, ledger.store.transaction(conn):
        run = dict(conn.execute("SELECT * FROM runs WHERE id=?", (opened["id"],)).fetchone())
        for index in range(205):
            copy = {**run, "id": f"fixture_run_{index}", "snapshot_key": f"snapshot-{index}"}
            conn.execute(f"INSERT INTO runs ({','.join(copy)}) VALUES ({','.join('?' for _ in copy)})", tuple(copy.values()))
            conn.execute("""INSERT INTO lesson_uses (id,repository_id,version_id,run_id,applicability,usefulness,
                behavioral_result,execution_block,explanation,result_explanation,created_at,updated_at,contribution)
                VALUES (?,?,?,?,'applicable','useful','hypothesis_refuted','none','Synthetic','Synthetic','x',?,'useful')""",
                         (f"fixture_use_{index}", scope.repository_id, version, copy["id"], f"z{index:04}"))
        Improvements.record_outcome(conn, scope, opened["id"], result["use_id"])
    aggregate = Improvements(ledger.store).inspect(scope, result["improvement_id"])["aggregate"]
    assert aggregate["window_size"] == 200
    assert aggregate["older_outcomes_omitted"] is True
    assert aggregate["distinct_review_count"] == 1
    assert aggregate["independent_support"] is None


def test_duplicate_outcome_ids_rejected(ledger, opened, actor, active):
    result, _ = outcome(active, opened, actor)
    data = revision(active[2], result["use_id"])
    data["source_outcome_ids"] *= 2
    with pytest.raises(LedgerError) as caught:
        Improvements(ledger.store).propose(active[1], opened["id"], actor, 1, data, "duplicates")
    assert caught.value.code == "invalid_input"
