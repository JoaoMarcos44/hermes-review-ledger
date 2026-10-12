"""Regression for historical workflow detail P2 (PR15).

An ineligible improvement target (retired after an approved concrete
revision, suspended, or with an invalidated source observation) must keep its
exact historical target metadata with actionable_now=false and the refusal
reason; the active positive control keeps full actionability. Exercised
through real public transitions and bounded status_detail reassembly.

All fixtures are synthetic; no real Ledger data is accessed.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from review_ledger.improvements import Improvements
from review_ledger.learning import Learning
from review_ledger.workflow import WorkflowView

REPO = "synthetic/example"

# case -> exact historical target state after the case transition.
# Invalidating a source observation suspends lessons citing it directly
# (Ledger.invalidate, service.py), so source_invalidated also lands on
# 'suspended'; sources[0]["valid"] == 0 is its distinguishing provenance.
EXPECTED_TARGET_STATE = {
    "retired_after_approved_revision": "retired",
    "suspended_target": "suspended",
    "source_invalidated": "suspended",
    "active_control": "active",
}


def _status_detail_record(ledger, run_id, actor, improvement_id):
    """Reassemble the bounded status_detail pages and verify the digest."""
    pages = []
    offset = 0
    while True:
        page = ledger.status_detail(REPO, run_id, actor, detail_collection="workflow",
                                    detail_id=improvement_id, offset=offset)
        pages.append(page)
        if page["complete"] or page["next_offset"] is None:
            break
        offset = page["next_offset"]
    content = "".join(page["content"] for page in pages)
    assert all(page["content_sha256"] == pages[0]["content_sha256"] for page in pages)
    assert hashlib.sha256(content.encode()).hexdigest() == pages[0]["content_sha256"]
    assert len(content) == pages[0]["total_chars"]
    return json.loads(content)


@pytest.mark.parametrize("case", sorted(EXPECTED_TARGET_STATE))
def test_workflow_historical_detail_preserves_exact_target(
        ledger, opened, actor, observed, lesson_data, case):
    learning = Learning(ledger.store, improvements_enabled=True)
    scope = ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, lesson_data,
                               f"hist-propose-{case}")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic approval",
                      f"hist-approve-{case}")
    use = learning.use(scope, opened["id"], actor, opened["generation"], version,
                       "applicable", "Synthetic use", f"hist-use-{case}")
    result = learning.result(scope, opened["id"], actor, opened["generation"],
                             use["use_id"],
                             {"usefulness": "not_useful", "behavioral_result": "not_tested",
                              "execution_block": "none", "explanation": "Synthetic redundant",
                              "contribution": "redundant"},
                             f"hist-outcome-{case}")
    diagnosis_id = result["improvement_id"]
    if case == "retired_after_approved_revision":
        revision = Improvements(ledger.store).propose(
            scope, opened["id"], actor, opened["generation"],
            {"target_version_id": version, "source_outcome_ids": [result["use_id"]],
             "changes": {"tags": ["scoped-retry", "persistence"]},
             "reason": "Synthetic concrete revision",
             "expected_benefit": "Synthetic hypothesis"},
            f"hist-revise-{case}")
        learning.operator(scope, revision["version_id"], "approve",
                          "Synthetic revision approval", f"hist-approve-rev-{case}")
    elif case == "suspended_target":
        learning.operator(scope, version, "suspend", "Synthetic suspension",
                          f"hist-suspend-{case}")
    elif case == "source_invalidated":
        ledger.record(REPO, opened["id"], actor, opened["generation"],
                      "invalidate_observation",
                      {"observation_id": observed, "reason": "Synthetic invalidation"},
                      f"hist-invalidate-{case}")

    detail = WorkflowView(ledger.store).detail(scope, diagnosis_id)
    assert detail["state"] == "workflow_detail"
    assert detail["improvement_id"] == diagnosis_id
    assert detail["status"] == "review_needed"
    assert detail["run_id"] == opened["id"]
    assert detail["target_version_id"] == version
    assert detail["candidate_version_id"] is None
    assert detail["source_outcome_ids"] == [result["use_id"]]
    assert detail["omitted"] == {"source_outcomes": 0, "offset": 0, "limit": 20}
    assert detail["changes"] == {}
    assert detail["evaluation_references"] == []
    assert detail["notice"] == "Controlled procedural improvement; expected benefit is a hypothesis."
    assert detail["aggregate"] is not None

    # The exact historical target is always retained, never omitted.
    # models.strings() canonicalizes conditions/exclusions/tags/symbols to a
    # sorted set, so the exact stored form is the sorted fixture value.
    target = detail["target"]
    assert target["version_id"] == version
    assert target["question"] == lesson_data["question"]
    assert target["conditions"] == sorted(lesson_data["conditions"])
    assert target["exclusions"] == sorted(lesson_data["exclusions"])
    assert target["verification"] == lesson_data["verification"]
    assert target["tags"] == sorted(lesson_data["tags"])
    assert target["symbols"] == sorted(lesson_data["symbols"])
    expected_valid = 0 if case == "source_invalidated" else 1
    assert target["sources"] == [{
        "observation_id": observed, "relation": "supports", "run_id": opened["id"],
        "provenance": "agent_reported", "valid": expected_valid,
        "outcome": "behavior_failure"}]

    if case == "active_control":
        assert target["state"] == "active"
        assert target["eligible_now"] is True
        assert detail["eligible_now"] is True
        assert detail["actionable_now"] is True
        assert "ineligible_reason" not in detail
    else:
        assert target["state"] == EXPECTED_TARGET_STATE[case]
        assert target["eligible_now"] is False
        assert detail["eligible_now"] is False
        assert detail["actionable_now"] is False
        assert detail["ineligible_reason"] == "stale_improvement"

    # Bounded status_detail reassembly carries the same exact contract.
    record = _status_detail_record(ledger, opened["id"], actor, diagnosis_id)
    assert record["state"] == "workflow_detail"
    assert record["target"]["version_id"] == version
    assert record["target"]["conditions"] == sorted(lesson_data["conditions"])
    assert record["target"]["exclusions"] == sorted(lesson_data["exclusions"])
    assert record["target"]["verification"] == lesson_data["verification"]
    assert record["target"]["tags"] == sorted(lesson_data["tags"])
    assert record["target"]["symbols"] == sorted(lesson_data["symbols"])
    assert record["target"]["sources"] == target["sources"]
    if case == "active_control":
        assert record["actionable_now"] is True
    else:
        assert record["actionable_now"] is False
        assert record["ineligible_reason"] == "stale_improvement"
