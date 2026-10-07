"""Synthetic manual feedback and obsolete-PR dispositions using existing APIs.

These records describe local fixtures only; no external repository is fetched or
executed, and no semantic truth is inferred from an agent-reported observation.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError
from review_ledger.reports import export

REPO = "synthetic/example"


def _finding(ledger, opened, actor, observed):
    finding = ledger.record(REPO, opened["id"], actor, opened["generation"], "finding", {
        "claim": "Synthetic repeated write may duplicate a persistent effect",
    }, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, opened["generation"], "assessment", {
        "finding_id": finding, "state": "supported", "basis": "behavior",
        "rationale": "Synthetic original fixture reports the repeated effect",
        "limitations": "Agent-reported local fixture, not an external execution",
        "observation_ids": [observed],
    }, "original-assessment")
    return finding


@pytest.mark.parametrize(("guide", "kind", "outcome"), [
    ("review-feedback-pilot.md", "note", "incomplete"),
    ("superseded-reviews.md", "inspection", "inspection"),
])
def test_documented_observation_examples_match_record_contract(
        ledger, opened, actor, guide, kind, outcome):
    document = (Path(__file__).resolve().parents[1] / "docs" / guide).read_text(encoding="utf-8")
    examples = re.findall(r"```json\n(.*?)\n```", document, flags=re.DOTALL)
    assert len(examples) == 1
    data = json.loads(examples[0])
    assert data["kind"] == kind and data["outcome"] == outcome
    recorded = ledger.record(REPO, opened["id"], actor, opened["generation"],
                             "observation", data, "documented-example")
    assert recorded["provenance"] == "agent_reported"
    status = ledger.status(REPO, opened["id"], actor)
    assert len(status["observations"]) == 1
    observation = status["observations"][0]
    assert observation["id"] == recorded["observation_id"]
    assert all(observation[field] == value for field, value in data.items())
    assert status["findings"] == status["assessments"] == []


def test_criticism_without_evidence_remains_inconclusive(ledger, opened, actor):
    criticism = ledger.record(REPO, opened["id"], actor, opened["generation"], "observation", {
        "kind": "note", "outcome": "incomplete",
        "summary": "Synthetic reviewer questions a claim without supplying evidence",
        "limitations": "Criticism is unverified; no inspected path or test is available",
    }, "criticism")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, opened["generation"], "finding", {
        "claim": "The synthetic criticism may identify a review mistake",
    }, "criticism-finding")["finding_id"]
    assessment = {
        "finding_id": finding, "state": "inconclusive", "basis": "none",
        "rationale": "Awaiting evidence for the synthetic criticism",
        "limitations": "No behavior or source inspection establishes the claim",
        "observation_ids": [criticism],
    }
    result = ledger.record(REPO, opened["id"], actor, opened["generation"],
                           "assessment", assessment, "inconclusive")

    # Neither absent evidence nor a recorded criticism supplies eligible support.
    for index, sources in enumerate(([], [criticism])):
        with pytest.raises(LedgerError) as exc:
            ledger.record(REPO, opened["id"], actor, opened["generation"], "assessment", {
                **assessment, "state": "supported", "basis": "inspection",
                "observation_ids": sources,
            }, f"unsupported-promotion-{index}")
        assert exc.value.code == "ineligible_evidence"

    with pytest.raises(LedgerError) as exc:
        Learning(ledger.store).propose(ledger.scope(REPO), opened["id"], actor,
                                      opened["generation"], {
            "question": "Should this synthetic claim be investigated differently?",
            "conditions": ["A comparable claim is under review"],
            "exclusions": ["The criticism has not been verified"],
            "verification": "Obtain source inspection or behavioral evidence first",
            "sources": [{"observation_id": criticism, "relation": "supports"}],
        }, "unsupported-lesson")
    assert exc.value.code == "ineligible_source"
    status = ledger.status(REPO, opened["id"], actor)
    assert len(status["assessments"]) == 1
    assert status["assessments"][0]["id"] == result["assessment_id"]
    assert status["assessments"][0]["state"] == "inconclusive"
    assert status["assessments"][0]["freshness"] == "current"


def test_feedback_revision_does_not_replace_approved_lesson_until_operator(
        ledger, opened, actor, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    original = learning.propose(scope, opened["id"], actor, opened["generation"],
                                lesson_data, "original-lesson")["version_id"]
    learning.operator(scope, original, "approve", "Synthetic operator evaluation", "approve-original")
    revision = {
        **lesson_data, "previous_version_id": original,
        "conditions": ["A retry crosses the synthetic persistence boundary"],
        "exclusions": ["Synthetic deduplication covers the full persistent effect"],
    }
    proposed = learning.propose(scope, opened["id"], actor, opened["generation"],
                               revision, "feedback-revision")
    candidate = proposed["version_id"]
    assert proposed["state"] == "candidate"
    with ledger.store.connect() as conn:
        version = learning.version(conn, scope, candidate)
    assert version["approved_at"] is None
    assert version["eligible_now"] is False
    assert [item["id"] for item in learning.recall(scope, opened["id"])["lessons"]] == [original]
    with pytest.raises(LedgerError) as exc:
        learning.detail(scope, opened["id"], candidate)
    assert exc.value.code == "lesson_not_eligible"
    with pytest.raises(LedgerError) as exc:
        learning.use(scope, opened["id"], actor, opened["generation"], candidate,
                     "applicable", "Synthetic revised conditions match", "use-candidate")
    assert exc.value.code == "lesson_not_eligible"
    with pytest.raises(LedgerError) as exc:
        learning.propose(scope, opened["id"], actor, opened["generation"],
                         {**revision, "approved": True}, "self-approval")
    assert exc.value.code == "invalid_input"

    learning.operator(scope, candidate, "approve", "Synthetic operator evaluated the revision", "approve-revision")
    assert [item["id"] for item in learning.recall(scope, opened["id"])["lessons"]] == [candidate]
    with ledger.store.connect() as conn:
        assert learning.version(conn, scope, original)["state"] == "retired"
        assert learning.version(conn, scope, candidate)["approved_at"] is not None


@pytest.mark.parametrize(("outcome", "behavioral_result", "execution_block"), [
    ("behavior_passed", "no_failure_observed", "none"),
    ("infrastructure_failure", "inconclusive", "infrastructure"),
])
def test_unsuccessful_reproduction_does_not_invalidate_original_source(
        ledger, opened, actor, observed, observation_data, lesson_data,
        outcome, behavioral_result, execution_block):
    _finding(ledger, opened, actor, observed)
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, opened["generation"],
                               lesson_data, "lesson")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic operator evaluation", "approve")
    use = learning.use(scope, opened["id"], actor, opened["generation"], version,
                       "uncertain", "Synthetic reproduction conditions differ", "use")
    ledger.record(REPO, opened["id"], actor, opened["generation"], "observation", {
        **observation_data, "outcome": outcome,
        "summary": "Synthetic attempt did not reproduce the original reported effect",
        "limitations": "Different fixture conditions; this does not disprove the original report",
    }, "reproduction-attempt")
    result = learning.result(scope, opened["id"], actor, opened["generation"], use["use_id"], {
        "usefulness": "inconclusive", "behavioral_result": behavioral_result,
        "execution_block": execution_block,
        "explanation": "Synthetic attempt cannot establish whether the original source is wrong",
    }, "inconclusive-result")

    assert result["eligible_now"] is True
    report = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    original = next(item for item in report["observations"] if item["id"] == observed)
    assert original["valid"] == 1 and original["invalid_reason"] is None
    assert report["assessments"][0]["state"] == "supported"
    assert report["assessments"][0]["freshness"] == "current"
    assert report["lesson_uses"][0]["behavioral_result"] == behavioral_result
    assert report["lesson_uses"][0]["eligible_now"] is True
    assert [item["id"] for item in learning.recall(scope, opened["id"])["lessons"]] == [version]


@pytest.mark.parametrize("change", ["same_snapshot", "head_sha", "base_sha", "skill"])
def test_obsolete_pr_disposition_is_discoverable_but_does_not_skip_future_run(
        ledger, opened, actor, observed, synthetic_snapshot, change):
    _finding(ledger, opened, actor, observed)
    disposition = "Synthetic PR #7 was superseded by PR #8; no further review requested for this captured state"
    observation = ledger.record(REPO, opened["id"], actor, opened["generation"], "observation", {
        "kind": "inspection", "outcome": "inspection", "summary": disposition,
        "details": "Synthetic replacement reference: https://github.com/synthetic/example/pull/8",
        "limitations": "Agent-reported disposition; replacement status is not proof that a finding was fixed",
    }, "obsolete-pr-disposition")["observation_id"]
    with pytest.raises(LedgerError) as exc:
        ledger.run_action(REPO, opened["id"], actor, opened["generation"],
                          "superseded", "invalid-disposition-action")
    assert exc.value.code == "invalid_input"
    finished = ledger.run_action(REPO, opened["id"], actor, opened["generation"],
                                 "complete", "complete-disposition", disposition)["run"]
    assert finished["status"] == "completed"
    assert finished["note"] == disposition
    assert finished["can_write"] is False

    # A new session can recover the decision without saved run identifiers.
    fresh = Actor("synthetic-feedback-follow-up-session")
    history = ledger.history(REPO, synthetic_snapshot["number"])
    assert history["runs"][0]["id"] == opened["id"]
    assert history["runs"][0]["status"] == "completed"
    recovered = ledger.status(REPO, history["runs"][0]["id"], fresh)
    assert recovered["run"]["note"] == disposition
    assert recovered["assessments"][0]["state"] == "supported"
    assert recovered["assessments"][0]["freshness"] == "current"
    assert any(item["id"] == observation and item["summary"] == disposition
               for item in recovered["observations"])

    snapshot = dict(synthetic_snapshot)
    if change in ("head_sha", "base_sha"):
        snapshot[change] = "e" * 40
    elif change == "skill":
        ledger.skill_hash = "f" * 64
    next_open = ledger.open(snapshot, fresh, "later-review")
    current = next_open["run"]
    assert next_open["state"] == "created"
    assert next_open["history_inherited"] is False
    assert current["id"] != opened["id"]
    assert current["status"] == "active" and current["can_write"] is True
    if change == "same_snapshot":
        assert current["snapshot_key"] == opened["snapshot_key"]
    else:
        assert current["snapshot_key"] != opened["snapshot_key"]
    status = ledger.status(REPO, current["id"], fresh)
    assert status["observations"] == status["findings"] == status["assessments"] == []
    old = ledger.status(REPO, opened["id"], fresh)
    assert old["run"]["status"] == "completed"
    assert old["run"]["note"] == disposition
    assert old["assessments"][0]["freshness"] == "historical"
    assert {item["id"] for item in ledger.history(REPO, synthetic_snapshot["number"])["runs"]} == {
        opened["id"], current["id"],
    }
