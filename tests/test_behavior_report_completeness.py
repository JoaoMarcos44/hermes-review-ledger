"""Blank reports remain recorded history, not behavioral verification."""
import pytest

from review_ledger.models import LedgerError
from test_critic_history import criticism  # noqa: F401 -- synthetic provider fixture

REPO = "synthetic/example"


@pytest.mark.parametrize("details", [" \t\r\n ", "\u00a0\u2003"])
@pytest.mark.parametrize("state", ["supported", "refuted"])
def test_behavior_assessment_rejects_whitespace_only_report(
        ledger, opened, actor, observation_data, details, state):
    source = ledger.record(REPO, opened["id"], actor, 1, "observation",
                           {**observation_data, "details": details}, "blank-report")["observation_id"]
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding",
                            {"claim": "Synthetic behavioral question"}, "blank-finding")["finding_id"]
    with pytest.raises(LedgerError) as caught:
        ledger.record(REPO, opened["id"], actor, 1, "assessment", {
            "finding_id": finding, "state": state, "basis": "behavior",
            "rationale": "Synthetic verification", "limitations": "Agent-reported fixture",
            "observation_ids": [source],
        }, "blank-assessment")
    assert caught.value.code == "incomplete_behavior_report"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT details FROM observations WHERE id=?", (source,)).fetchone()[0] == details
        assert conn.execute("SELECT COUNT(*) FROM assessments").fetchone()[0] == 0


@pytest.mark.parametrize("details", [" \t\r\n ", "\u00a0\u2003"])
@pytest.mark.parametrize("state", ["supported", "refuted"])
def test_critic_behavior_assessment_rejects_whitespace_only_report(
        ledger, opened, actor, observation_data, criticism, details, state):
    source = ledger.record(REPO, opened["id"], actor, 1, "observation",
                           {**observation_data, "details": details}, "blank-critic-report")["observation_id"]
    critic, critic_run_id, objection_id, previous_assessment = criticism
    with pytest.raises(LedgerError) as caught:
        critic.assess(REPO, opened["id"], actor, 1, "blank-critic-assessment",
                      critic_run_id, objection_id, state, "behavior",
                      "Synthetic verification", "Agent-reported fixture", [source])
    assert caught.value.code == "incomplete_behavior_report"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT details FROM observations WHERE id=?", (source,)).fetchone()[0] == details
        assessments = conn.execute("SELECT id,freshness FROM critic_assessments").fetchall()
        assert [tuple(row) for row in assessments] == [(previous_assessment, "current")]
