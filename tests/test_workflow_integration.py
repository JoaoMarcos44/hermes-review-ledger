"""Integrated workflow guards: scope, actionable stages, and body-free discovery."""
import json

import pytest

from review_ledger.improvements import Improvements
from review_ledger.learning import Learning
from review_ledger.models import LedgerError, canonical
from review_ledger.workflow import WorkflowView

REPO = "synthetic/example"


@pytest.fixture
def guidance(ledger, opened, actor, lesson_data):
    learning = Learning(ledger.store, improvements_enabled=True)
    scope = ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, lesson_data, "integration-lesson")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic fixture approval", "integration-approve")
    return learning, scope, version


def diagnosis(guidance, opened, actor):
    learning, scope, version = guidance
    use = learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic considered guidance", "integration-use")
    result = learning.result(scope, opened["id"], actor, 1, use["use_id"], {
        "usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
        "explanation": "Synthetic diagnosed redundancy", "contribution": "redundant",
    }, "integration-outcome")
    return result


@pytest.mark.parametrize("case", ["status_run_scope", "proposed_stage", "metadata_cards"])
def test_integrated_workflow_discovery(ledger, synthetic_snapshot, opened, actor, guidance, case):
    learning, scope, version = guidance
    if case == "status_run_scope":
        learning.use(scope, opened["id"], actor, 1, version, "applicable", "Pending current use", "integration-current")
        other = ledger.open({**synthetic_snapshot, "number": 8}, actor, "integration-other")["run"]
        learning.use(scope, other["id"], actor, 1, version, "uncertain", "Pending other use", "integration-other-use")
        status = ledger.status(REPO, opened["id"], actor, max_chars=4000)
        assert status["workflow"]["pending_use_count"] == 1
        return
    result = diagnosis(guidance, opened, actor)
    if case == "proposed_stage":
        proposal = Improvements(ledger.store).propose(scope, opened["id"], actor, 1, {
            "target_version_id": version, "source_outcome_ids": [result["use_id"]],
            "changes": {"tags": ["scoped-retry"]}, "reason": "Synthetic concrete revision",
            "expected_benefit": "Hypothesis, not verified quality",
        }, "integration-revision")
        cards = WorkflowView(ledger.store).summary(scope)["improvements"]
        assert any(c["improvement_id"] == proposal["improvement_id"] and c["status"] == "proposed" for c in cards)
        return
    summary = WorkflowView(ledger.store).summary(scope)
    card = next(c for c in summary["improvements"] if c["improvement_id"] == result["improvement_id"])
    assert "reason" not in card, "Discovery must not repeat unrestricted diagnostic prose"
    assert card["state"] == "not_loaded"
    assert card["detail"]["detail_collection"] == "workflow"
    status = ledger.status(REPO, opened["id"], actor, max_chars=4000)
    assert len(canonical(status)) <= 4000


@pytest.mark.parametrize("case", ["completion_receipt", "behavior_source_hints"])
def test_safe_workflow_error_hints(ledger, opened, actor, guidance, observation_data, case):
    learning, scope, version = guidance
    if case == "completion_receipt":
        use = learning.use(scope, opened["id"], actor, 1, version, "applicable", "Pending feedback", "integration-pending")
        with pytest.raises(LedgerError) as caught:
            ledger.run_action(REPO, opened["id"], actor, 1, "complete", "integration-complete")
        hints = caught.value.hints
        assert isinstance(hints, dict)
        assert use["use_id"] in hints["pending_use_ids"]
        with ledger.store.connect() as conn:
            row = conn.execute("SELECT status,owner_session,generation FROM runs WHERE id=?", (opened["id"],)).fetchone()
            assert tuple(row) == ("active", actor.session_id, 1)
            assert conn.execute("SELECT COUNT(*) FROM idempotency WHERE request_key='integration-complete'").fetchone()[0] == 0
        learning.result(scope, opened["id"], actor, 1, use["use_id"], {
            "usefulness": "inconclusive", "behavioral_result": "not_tested", "execution_block": "budget",
            "explanation": "Truthful blocked outcome, no execution invented",
        }, "integration-blocked")
        first = ledger.run_action(REPO, opened["id"], actor, 1, "complete", "integration-complete")
        assert ledger.run_action(REPO, opened["id"], actor, 1, "complete", "integration-complete") == first
        return
    sentinel = "private-fixture-prose-not-for-hints"
    obs = ledger.record(REPO, opened["id"], actor, 1, "observation", {
        "kind": "inspection", "outcome": "inspection", "summary": sentinel, "details": sentinel,
        "limitations": "Static fixture, not a test",
    }, "integration-static")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic contract"}, "integration-finding")["finding_id"]
    with pytest.raises(LedgerError) as caught:
        ledger.record(REPO, opened["id"], actor, 1, "assessment", {
            "finding_id": finding, "state": "supported", "basis": "behavior",
            "rationale": "Synthetic context only", "limitations": "No product executed", "observation_ids": [obs],
        }, "integration-assessment")
    assert caught.value.code == "incomplete_behavior_report"
    hints = caught.value.hints
    assert isinstance(hints, dict)
    assert hints["invalid_sources"][0]["observation_id"] == obs
    assert "kind" in hints["invalid_sources"][0]["required_fields"]
    assert sentinel not in json.dumps(hints)
