"""Tests for bounded closed-loop learning workflow: completion preflight,
read-only workflow view, and truthful status integration.

All fixtures are synthetic; no real Ledger data is accessed.
"""
from __future__ import annotations

import json

import pytest

from review_ledger.improvements import Improvements
from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError
from review_ledger.service import Ledger

REPO = "synthetic/example"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def active(ledger, opened, actor, lesson_data):
    learning = Learning(ledger.store, improvements_enabled=True)
    scope = ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, lesson_data, "initial")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic operator approval", "approve-initial")
    return learning, scope, version


@pytest.fixture
def completed_use(active, opened, actor):
    """A lesson use with a result already recorded."""
    learning, scope, version = active
    use = learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    data = {"usefulness": "useful", "behavioral_result": "hypothesis_refuted", "execution_block": "none",
            "explanation": "Synthetic useful refutation", "contribution": "useful"}
    result = learning.result(scope, opened["id"], actor, opened["generation"], use["use_id"], data, "outcome")
    return result


# ---------------------------------------------------------------------------
# RED 1: Completion preflight blocks pending applicable/uncertain uses
# ---------------------------------------------------------------------------

def test_complete_blocked_by_pending_applicable_use(ledger, opened, actor, active):
    """Completion must refuse when an applicable use has no recorded result."""
    learning, scope, version = active
    learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    with pytest.raises(LedgerError) as caught:
        ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", "complete-1")
    assert caught.value.code == "pending_lesson_feedback"


def test_complete_blocked_by_pending_uncertain_use(ledger, opened, actor, active):
    """Completion must refuse when an uncertain use has no recorded result."""
    learning, scope, version = active
    learning.use(scope, opened["id"], actor, opened["generation"], version, "uncertain", "Synthetic uncertain", "use")
    with pytest.raises(LedgerError) as caught:
        ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", "complete-1")
    assert caught.value.code == "pending_lesson_feedback"


def test_complete_allowed_when_no_pending_uses(ledger, opened, actor, completed_use):
    """Completion succeeds when all uses have recorded results."""
    result = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", "complete-1")
    assert result["state"] == "complete"


def test_complete_allowed_when_only_not_applicable_use(ledger, opened, actor, active):
    """not_applicable uses do not require a result to permit completion."""
    learning, scope, version = active
    learning.use(scope, opened["id"], actor, opened["generation"], version, "not_applicable", "Synthetic n/a", "use")
    result = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", "complete-1")
    assert result["state"] == "complete"


def test_complete_allowed_with_zero_uses(ledger, opened, actor):
    """Zero uses/findings/lessons remain valid for completion."""
    result = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", "complete-1")
    assert result["state"] == "complete"


def test_pause_remains_legal_with_pending_uses(ledger, opened, actor, active):
    """Pause does not require pending feedback resolution."""
    learning, scope, version = active
    learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    pause = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "pause", "pause-1")
    assert pause["state"] == "pause"


def test_idempotent_complete_retry_remains_legal(ledger, opened, actor, completed_use):
    """Retrying complete with the same request key returns the original receipt."""
    key = "complete-retry"
    first = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", key)
    second = ledger.run_action(REPO, opened["id"], actor, opened["generation"], "complete", key)
    assert first == second


# ---------------------------------------------------------------------------
# RED 2: Read-only workflow view
# ---------------------------------------------------------------------------

def test_workflow_view_shows_pending_improvements(ledger, opened, actor, active):
    """The workflow view exposes repository-scoped pending improvements."""
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    scope = ledger.scope(REPO)
    view = WorkflowView(ledger.store)
    summary = view.summary(scope)
    assert summary["state"] == "ok"
    assert summary["pending_improvements_count"] >= 1
    ids = [imp["improvement_id"] for imp in summary["improvements"]]
    assert result["improvement_id"] in ids


def test_workflow_view_excludes_foreign_repository(ledger, synthetic_snapshot, opened, actor, active):
    """Improvements from other repositories are not visible."""
    from review_ledger.workflow import WorkflowView
    _record_redundant_outcome(active, opened, actor)
    other = ledger.open({**synthetic_snapshot, "repository_id": 1002, "repository_node_id": "other-node",
                         "repository_full_name": "synthetic/other"}, actor, "other-scope")["run"]
    scope_other = ledger.scope("synthetic/other")
    view = WorkflowView(ledger.store)
    summary = view.summary(scope_other)
    assert summary["pending_improvements_count"] == 0


def test_workflow_view_excludes_resolved_improvements(ledger, opened, actor, active):
    """Improvements that are no longer pending (applied/rejected) are excluded.

    Corrected contract: a review_needed diagnosis has no candidate_version_id.
    To resolve it, exercise real Improvements.propose (creating a concrete
    candidate version) then operator approval of that version. The original
    target is retired by approval and must not be presented as actionable.
    The original review_needed diagnosis is preserved as historical record.
    """
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    learning, scope, version = active
    improvements = Improvements(ledger.store)
    view = WorkflowView(ledger.store)
    # The diagnosis is pending before resolution.
    summary = view.summary(scope)
    ids = [imp["improvement_id"] for imp in summary["improvements"]]
    assert result["improvement_id"] in ids
    # Propose a concrete revision (creates a new proposed proposal).
    proposal = improvements.propose(
        scope, opened["id"], actor, opened["generation"],
        {"target_version_id": version,
         "source_outcome_ids": [result["use_id"]],
         "changes": {"conditions": ["An operation can be repeated",
                                     "The operation has a persistent effect",
                                     "A new scoped condition"]},
         "reason": "Synthetic concrete revision",
         "expected_benefit": "Synthetic hypothesis"},
        "propose-resolved")
    candidate_version_id = proposal["version_id"]
    new_improvement_id = proposal["improvement_id"]
    # A concrete candidate remains visible while operator review is pending.
    summary = view.summary(scope)
    ids = [imp["improvement_id"] for imp in summary["improvements"]]
    assert new_improvement_id in ids
    # The original diagnosis is preserved as historical record.
    assert result["improvement_id"] in ids
    # Approve the concrete candidate version (proposed -> applied, target retired).
    learning.operator(scope, candidate_version_id, "approve", "Synthetic approval", "approve-resolved")
    # Applied changes leave the actionable queue; old diagnostics remain history.
    summary = view.summary(scope)
    assert new_improvement_id not in [c["improvement_id"] for c in summary["improvements"]]
    original = next(c for c in summary["improvements"] if c["improvement_id"] == result["improvement_id"])
    assert original["eligible_now"] is False
    detail = view.detail(scope, new_improvement_id)
    assert detail["state"] == "workflow_detail"
    assert detail["target"]["state"] == "retired"
    assert detail["target"]["eligible_now"] is False


def test_workflow_view_includes_pending_lesson_uses(ledger, opened, actor, active):
    """The workflow view surfaces pending lesson uses in the current run."""
    from review_ledger.workflow import WorkflowView
    learning, scope, version = active
    learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    view = WorkflowView(ledger.store)
    summary = view.summary(scope)
    assert summary["pending_use_count"] >= 1


def test_workflow_detail_returns_actionable_metadata(ledger, opened, actor, active):
    """The workflow detail includes exact target/outcome/PR IDs and eligibility."""
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    scope = ledger.scope(REPO)
    view = WorkflowView(ledger.store)
    detail = view.detail(scope, result["improvement_id"])
    assert detail["state"] == "workflow_detail"
    assert detail["improvement_id"] == result["improvement_id"]
    assert detail["target_version_id"] is not None
    assert detail["status"] == "review_needed"
    # Exact identity fields.
    assert detail["run_id"] is not None
    assert detail["candidate_version_id"] is None  # review_needed has no candidate
    # Exact source outcome IDs.
    assert result["use_id"] in detail["source_outcome_ids"]
    # Target version info.
    assert detail["target"]["version_id"] == detail["target_version_id"]
    assert detail["target"]["state"] == "active"


def test_workflow_detail_includes_omission_metadata(ledger, opened, actor, active):
    """The workflow detail records offset/omission when bounded."""
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    scope = ledger.scope(REPO)
    view = WorkflowView(ledger.store)
    detail = view.detail(scope, result["improvement_id"], limit=1)
    assert detail["omitted"] is not None
    assert detail["omitted"]["limit"] == 1
    assert detail["omitted"]["offset"] == 0
    # With one source outcome and limit=1, nothing is omitted.
    assert detail["omitted"]["source_outcomes"] == 0


# ---------------------------------------------------------------------------
# RED 3: Status summary includes compact workflow
# ---------------------------------------------------------------------------

def test_status_includes_workflow_summary(ledger, opened, actor, active):
    """Ledger.status includes a compact workflow summary."""
    _record_redundant_outcome(active, opened, actor)
    status = ledger.status(REPO, opened["id"], actor)
    assert "workflow" in status
    assert status["workflow"]["state"] == "ok"
    assert "pending_improvements_count" in status["workflow"]


def test_status_workflow_summary_small_enough_for_budget(ledger, opened, actor, active):
    """The workflow summary is bounded and does not break the 4k status budget."""
    _record_redundant_outcome(active, opened, actor)
    status = ledger.status(REPO, opened["id"], actor, max_chars=4000)
    assert len(json.dumps(status)) <= 4000


# ---------------------------------------------------------------------------
# RED 4: status_detail supports workflow collection
# ---------------------------------------------------------------------------

def test_status_detail_workflow_collection(ledger, opened, actor, active):
    """status_detail with detail_collection='workflow' returns bounded detail."""
    result, _ = _record_redundant_outcome(active, opened, actor)
    detail = ledger.status_detail(REPO, opened["id"], actor, detail_collection="workflow",
                                 detail_id=result["improvement_id"])
    assert detail["state"] == "detail"
    assert detail["detail_collection"] == "workflow"


# ---------------------------------------------------------------------------
# RED 5: Safe structured hints for behavior support-role failures
# ---------------------------------------------------------------------------

def test_ledger_error_hints_are_optional_and_sanitized():
    """LedgerError can carry optional hints without exposing bodies."""
    err = LedgerError("test_code", "Test message")
    assert not hasattr(err, "hints") or err.hints is None


def test_service_reports_hint_ids_not_bodies(ledger, opened, actor):
    """When a behavior assessment fails, the error includes field category hints."""
    # Record an inspection observation (not a behavior test)
    obs = ledger.record(REPO, opened["id"], actor, opened["generation"], "observation",
                        {"kind": "inspection", "outcome": "inspection", "summary": "Synthetic inspection",
                         "limitations": "Synthetic"}, "obs-1")["observation_id"]
    # Propose a finding
    finding = ledger.record(REPO, opened["id"], actor, opened["generation"], "finding",
                           {"claim": "Synthetic claim"}, "finding-1")["finding_id"]
    # Try to assess with behavior basis using inspection observation
    with pytest.raises(LedgerError) as caught:
        ledger.record(REPO, opened["id"], actor, opened["generation"], "assessment",
                     {"finding_id": finding, "state": "supported", "basis": "behavior",
                      "rationale": "Synthetic", "limitations": "Synthetic",
                      "observation_ids": [obs]}, "assess-1")
    assert caught.value.code == "incomplete_behavior_report"
    # Shape hints identify the source without treating inspection as execution.
    assert caught.value.hints["invalid_sources"][0]["observation_id"] == obs
    assert "kind" in caught.value.hints["invalid_sources"][0]["required_fields"]
    assert "details" in caught.value.hints["invalid_sources"][0]["required_fields"]


# ---------------------------------------------------------------------------
# RED 6: Table-driven workflow view contract tests
# ---------------------------------------------------------------------------

def test_workflow_summary_true_total_vs_limit(ledger, synthetic_snapshot, opened, actor, active):
    """pending_improvements_count is the true total, not the first page length."""
    from review_ledger.workflow import WorkflowView
    learning, scope, version = active
    view = WorkflowView(ledger.store)
    # Create improvements in multiple runs (one use per version per run).
    for i in range(3):
        snapshot = {**synthetic_snapshot, "head_sha": f"{i + 1:040d}"}
        run = ledger.open(snapshot, actor, f"open-total-{i}")["run"]
        use = learning.use(scope, run["id"], actor, run["generation"], version, "applicable",
                           f"Synthetic use {i}", f"use-total-{i}")
        data = {"usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
                "explanation": "Synthetic redundant", "contribution": "redundant"}
        learning.result(scope, run["id"], actor, run["generation"], use["use_id"], data, f"outcome-total-{i}")
    summary = view.summary(scope, limit=2)
    assert summary["pending_improvements_count"] == 3
    assert len(summary["improvements"]) == 2
    assert summary["improvements_omitted"] is True


def test_workflow_summary_includes_prior_run_queue(ledger, synthetic_snapshot, opened, actor, active):
    """The repository queue includes improvements from prior runs."""
    from review_ledger.workflow import WorkflowView
    learning, scope, version = active
    result, _ = _record_redundant_outcome(active, opened, actor)
    # Open a second run and create another improvement.
    snapshot = {**synthetic_snapshot, "head_sha": "2" * 40}
    run2 = ledger.open(snapshot, actor, "open-prior")["run"]
    use2 = learning.use(scope, run2["id"], actor, run2["generation"], version, "applicable",
                        "Synthetic use 2", "use-prior")
    data2 = {"usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
             "explanation": "Synthetic redundant 2", "contribution": "redundant"}
    result2 = learning.result(scope, run2["id"], actor, run2["generation"], use2["use_id"], data2, "outcome-prior")
    view = WorkflowView(ledger.store)
    summary = view.summary(scope)
    ids = [imp["improvement_id"] for imp in summary["improvements"]]
    assert result["improvement_id"] in ids
    assert result2["improvement_id"] in ids


def test_workflow_source_invalidation_makes_ineligible(ledger, opened, actor, active, observed):
    """Invalidating a source observation makes the improvement ineligible."""
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    scope = ledger.scope(REPO)
    # Invalidate the source observation of the target version.
    ledger.record(REPO, opened["id"], actor, opened["generation"], "invalidate_observation",
                  {"observation_id": observed, "reason": "Synthetic invalidation"}, "invalidate-1")
    view = WorkflowView(ledger.store)
    summary = view.summary(scope)
    cards = {imp["improvement_id"]: imp for imp in summary["improvements"]}
    card = cards[result["improvement_id"]]
    assert card["eligible_now"] is False
    assert card["ineligible_reason"] in ("stale_improvement", "ineligible_source")
    detail = view.detail(scope, result["improvement_id"])
    assert detail["eligible_now"] is False
    assert detail["ineligible_reason"] in ("stale_improvement", "ineligible_source")


def test_workflow_summary_run_scoped_pending_feedback(ledger, synthetic_snapshot, opened, actor, active):
    """When run_id is supplied, pending feedback is scoped to that exact run."""
    from review_ledger.workflow import WorkflowView
    learning, scope, version = active
    # Record a use in the current run (pending feedback).
    learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    # Open a second run and record a use there.
    snapshot = {**synthetic_snapshot, "head_sha": "2" * 40}
    run2 = ledger.open(snapshot, actor, "open-scoped")["run"]
    learning.use(scope, run2["id"], actor, run2["generation"], version, "applicable", "Synthetic use 2", "use-scoped")
    view = WorkflowView(ledger.store)
    # Without run_id: repository-scoped count includes both runs.
    summary = view.summary(scope)
    assert summary["pending_use_count"] == 2
    # With run_id: scoped to that run only.
    summary_run = view.summary(scope, run_id=opened["id"])
    assert summary_run["pending_use_count"] == 1
    summary_run2 = view.summary(scope, run_id=run2["id"])
    assert summary_run2["pending_use_count"] == 1


def test_workflow_read_only_no_mutations(ledger, opened, actor, active):
    """summary and detail do not write to the ledger."""
    from review_ledger.workflow import WorkflowView
    result, _ = _record_redundant_outcome(active, opened, actor)
    scope = ledger.scope(REPO)
    view = WorkflowView(ledger.store)
    # Count audit events before.
    with ledger.store.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    summary = view.summary(scope)
    detail = view.detail(scope, result["improvement_id"])
    # Count audit events after.
    with ledger.store.connect() as conn:
        after = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_redundant_outcome(active, opened, actor):
    """Record a useful+redundant outcome that triggers an improvement proposal."""
    learning, scope, version = active
    use = learning.use(scope, opened["id"], actor, opened["generation"], version, "applicable", "Synthetic use", "use")
    data = {"usefulness": "not_useful", "behavioral_result": "not_tested", "execution_block": "none",
            "explanation": "Synthetic redundant", "contribution": "redundant"}
    result = learning.result(scope, opened["id"], actor, opened["generation"], use["use_id"], data, "outcome")
    return result, data
