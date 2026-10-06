"""Synthetic regressions found while reviewing the initial implementation."""
import json

import pytest

from review_ledger.learning import Learning
from review_ledger.models import LedgerError
from review_ledger.reports import export

REPO = "synthetic/example"


def _approved(ledger, opened, actor, lesson_data):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, lesson_data, "candidate")["version_id"]
    learning.operator(scope, version, "approve", "Synthetic operator evaluation", "approval")
    return learning, scope, version


def test_use_retry_rechecks_revocation(ledger, opened, actor, lesson_data):
    learning, scope, version = _approved(ledger, opened, actor, lesson_data)
    first = learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic scope matches", "use")
    assert first == learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic scope matches", "use")
    learning.operator(scope, version, "suspend", "Synthetic source pending", "suspend")
    with pytest.raises(LedgerError) as exc:
        learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic scope matches", "use")
    assert exc.value.code == "lesson_not_eligible"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM lesson_uses").fetchone()[0] == 1


def test_result_preserves_applicability_reason(ledger, opened, actor, lesson_data):
    learning, scope, version = _approved(ledger, opened, actor, lesson_data)
    used = learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic application rationale", "use")
    learning.result(scope, opened["id"], actor, 1, used["use_id"], {
        "usefulness": "useful", "behavioral_result": "hypothesis_refuted", "execution_block": "none", "explanation": "Synthetic result rationale"}, "result")
    report = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert report["lesson_uses"][0]["explanation"] == "Synthetic application rationale"
    assert report["lesson_uses"][0]["result_explanation"] == "Synthetic result rationale"
    assert learning.use(scope, opened["id"], actor, 1, version, "applicable", "Synthetic application rationale", "other-retry")["use_id"] == used["use_id"]


def test_resolution_tracks_original_failure_dependency(ledger, opened, actor, observed, synthetic_snapshot, observation_data):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic original behavior"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [observed]}, "assessment")
    current = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")["run"]
    passing = ledger.record(REPO, current["id"], actor, 1, "observation", {**observation_data, "outcome": "behavior_passed"}, "passing")["observation_id"]
    assessed = ledger.record(REPO, current["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "refuted", "basis": "behavior", "rationale": "Synthetic original behavior passed",
        "limitations": "Agent reported", "observation_ids": [passing], "resolution": {
            "original_observation_id": observed, "verification_observation_id": passing,
            "original_behavior": "Synthetic repeat effect", "limitations": "Agent reported"}}, "resolution")
    assert assessed["freshness"] == "current"
    ledger.operator_invalidate(REPO, observed, "Synthetic original report corrected", "invalidate")
    status = ledger.status(REPO, current["id"], actor)
    assert status["assessments"][0]["freshness"] == "needs_revalidation"


def test_resolution_rejects_unrelated_original(ledger, opened, actor, observed, observation_data):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic unrelated claim"}, "finding")["finding_id"]
    passing = ledger.record(REPO, opened["id"], actor, 1, "observation", {**observation_data, "outcome": "behavior_passed"}, "pass")["observation_id"]
    with pytest.raises(LedgerError) as exc:
        ledger.record(REPO, opened["id"], actor, 1, "assessment", {
            "finding_id": finding, "state": "refuted", "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [passing],
            "resolution": {"original_observation_id": observed, "verification_observation_id": passing, "original_behavior": "Unrelated", "limitations": "Synthetic"}}, "bad-resolution")
    assert exc.value.code == "invalid_resolution"


def test_artifact_retries_and_rollback_do_not_leave_files(ledger, opened, actor, observation_data):
    data = {**observation_data, "artifact_text": "Synthetic limited artifact"}
    first = ledger.record(REPO, opened["id"], actor, 1, "observation", data, "artifact")
    assert first == ledger.record(REPO, opened["id"], actor, 1, "observation", data, "artifact")
    assert len(list((ledger.store.data_dir / "artifacts").iterdir())) == 1
    with pytest.raises(LedgerError):
        ledger.record(REPO, opened["id"], actor, 1, "observation", {**data, "outcome": "not-an-outcome"}, "bad")
    assert len(list((ledger.store.data_dir / "artifacts").iterdir())) == 1


def test_large_valid_snapshot_remains_exportable(ledger, synthetic_snapshot, actor):
    files = [{"filename": f"synthetic/{index}/" + "x" * 170, "sha": "c" * 40, "status": "modified", "patch_status": "unavailable"} for index in range(150)]
    run = ledger.open({**synthetic_snapshot, "files": files}, actor, "large")["run"]
    report = export(ledger, REPO, run["id"], actor, format="json", limit=1)
    data = json.loads(report["content"])
    assert "snapshot" not in data["run"]
    assert len(data["snapshot"]["files"]) == 150
    assert report["chars"] < 100000


def test_learning_generation_must_be_integer(ledger, opened, actor, lesson_data):
    with pytest.raises(LedgerError) as exc:
        Learning(ledger.store).propose(ledger.scope(REPO), opened["id"], actor, True, lesson_data, "boolean-generation")
    assert exc.value.code == "invalid_input"


def test_unassessed_claim_visible_in_status(ledger, opened, actor):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic pending hypothesis"}, "finding")["finding_id"]
    status = ledger.status(REPO, opened["id"], actor)
    assert status["findings"][0]["id"] == finding
    assert status["assessments"] == []


def test_finding_cannot_move_to_another_pr(ledger, opened, actor, synthetic_snapshot):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic first PR claim"}, "finding")["finding_id"]
    other = ledger.open({**synthetic_snapshot, "number": 8}, actor, "another-pr")["run"]
    with pytest.raises(LedgerError) as exc:
        ledger.record(REPO, other["id"], actor, 1, "assessment", {"finding_id": finding, "state": "unverified", "basis": "none", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": []}, "wrong-pr")
    assert exc.value.code == "scope_not_found"


def test_completed_snapshot_assessment_becomes_historical(ledger, opened, actor, observed, synthetic_snapshot):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {"finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [observed]}, "assessment")
    ledger.run_action(REPO, opened["id"], actor, 1, "complete", "complete")
    ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")
    assert ledger.status(REPO, opened["id"], actor)["assessments"][0]["freshness"] == "historical"


def test_open_freshness_guard_preserves_concurrent_committed_snapshot(ledger, actor, synthetic_snapshot):
    captured = ledger.open_generation(REPO, 7)
    current = ledger.open(
        {**synthetic_snapshot, "head_sha": "e" * 40}, actor, "current",
        expected_open_generation=captured,
    )["run"]
    with pytest.raises(LedgerError) as exc:
        ledger.open(synthetic_snapshot, actor, "delayed", expected_open_generation=captured)
    assert exc.value.code == "snapshot_conflict"
    status = ledger.status(REPO, current["id"], actor)
    assert status["run"]["status"] == "active"
    assert status["historical_runs"] == 0


def test_open_freshness_guard_allows_matching_reuse_and_exact_retry(ledger, actor, synthetic_snapshot):
    captured = ledger.open_generation(REPO, 7)
    first = ledger.open(synthetic_snapshot, actor, "first", expected_open_generation=captured)
    reused = ledger.open(synthetic_snapshot, actor, "same-snapshot", expected_open_generation=captured)
    assert reused["state"] == "reused"
    assert reused["run"]["id"] == first["run"]["id"]
    assert ledger.open(
        synthetic_snapshot, actor, "first",
        expected_open_generation=ledger.open_generation(REPO, 7),
    ) == first


def test_open_freshness_guard_is_scoped_to_pull_request(ledger, actor, synthetic_snapshot):
    captured = ledger.open_generation(REPO, 7)
    ledger.open({**synthetic_snapshot, "number": 8}, actor, "other-pr")
    assert ledger.open_generation(REPO, 7) == captured
    assert ledger.open(
        synthetic_snapshot, actor, "first-pr", expected_open_generation=captured,
    )["state"] == "created"


def test_markdown_preserves_reported_text_as_literal_content(ledger, opened, actor, observed):
    reported = "Review notes\n# Checklist\n![diagram](https://example.invalid/image) <section> & **text**"
    finding = ledger.record(
        REPO, opened["id"], actor, 1, "finding", {"claim": reported}, "literal-finding",
    )["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "supported", "basis": "inspection",
        "rationale": reported, "limitations": reported, "observation_ids": [observed],
    }, "literal-assessment")
    ledger.record(REPO, opened["id"], actor, 1, "observation", {
        "kind": "inspection", "outcome": "inspection", "summary": reported,
        "limitations": reported,
    }, "literal-observation")
    markdown = export(ledger, REPO, opened["id"], actor, format="markdown")["content"]
    assert "\n# Checklist" not in markdown
    assert "\n    \\# Checklist" in markdown
    assert "![diagram](" not in markdown
    assert "\\!\\[diagram\\]\\(" in markdown
    assert "<section>" not in markdown
    assert "&lt;section&gt; &amp; \\*\\*text\\*\\*" in markdown
    original = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert original["findings"][0]["claim"] == reported
    assert original["assessments"][0]["rationale"] == reported


def test_invalid_open_key_has_no_storage_side_effect(ledger, actor, synthetic_snapshot):
    with pytest.raises(LedgerError) as exc:
        ledger.open(synthetic_snapshot, actor, "")
    assert exc.value.code == "invalid_input"
    assert not ledger.store.data_dir.exists()
