"""Synthetic critic history: no host model, remote service, or credentials."""
import json

import pytest

from review_ledger.critic import Critic, CriticConfig, CriticResult
from review_ledger.learning import Learning
from review_ledger.models import LedgerError
from review_ledger.reports import export

REPO = "synthetic/example"


class FakeOpinion:
    def evaluate(self, packet):
        return CriticResult(json.dumps({"items": [{
            "finding_id": packet["findings"][0]["id"], "position": "challenge",
            "rationale": "Check the declared boundary.", "contract_status": "unknown",
            "contract_statement": "No contract was supplied.", "reference_ids": [],
            "objections": [{"category": "boundary", "claim": "Boundary may differ.",
                            "counter_hypothesis": "Guard might cover the boundary.",
                            "recommended_check": "Inspect the recorded guard.",
                            "would_withdraw_if": "The guard excludes this case.", "reference_ids": []}],
            "missing_information": ["Boundary contract"], "limitations": ["No execution."]
        }]}), provider="synthetic", model="synthetic")


@pytest.fixture
def criticism(ledger, opened, actor, observed):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Original finding"}, "history-finding")["finding_id"]
    critic = Critic(ledger, CriticConfig(enabled=True, authorized_repositories=(REPO,),
                                       provider="synthetic", model="synthetic"), FakeOpinion())
    prepared = critic.prepare(REPO, opened["id"], actor, 1, "history-prepare", [finding], [observed])
    ident = prepared["critic_run_id"]
    assert critic.run(REPO, opened["id"], actor, 1, "history-run", ident)["state"] == "returned"
    with ledger.store.connect() as conn:
        objection = conn.execute("SELECT id FROM critic_objections").fetchone()[0]
    assessed = critic.assess(REPO, opened["id"], actor, 1, "history-assess", ident, objection,
                             "refuted", "behavior", "Synthetic verification refutes this objection.",
                             "Agent-reported only.", [observed])
    return critic, ident, objection, assessed["critic_assessment_id"]


def propose(ledger, opened, actor, lesson_data, assessment):
    return Learning(ledger.store).propose(ledger.scope(REPO), opened["id"], actor, 1,
                                          {**lesson_data, "critic_assessment_ids": [assessment]}, "history-proposal")


def test_candidate_provenance_never_autoapproves(ledger, opened, actor, lesson_data, criticism):
    result = propose(ledger, opened, actor, lesson_data, criticism[3])
    assert result["state"] == "candidate"
    learning = Learning(ledger.store)
    scope = ledger.scope(REPO)
    with ledger.store.connect() as conn:
        version = learning.version(conn, scope, result["version_id"])
    assert version["critic_assessment_ids"] == [criticism[3]]
    assert version["critic_links_eligible"]
    assert not version["eligible_now"]
    assert learning.recall(scope, opened["id"])["lessons"] == []
    learning.operator(scope, result["version_id"], "approve", "Reviewed synthetic sources", "history-approve")
    assert len(learning.recall(scope, opened["id"])["lessons"]) == 1


def test_new_adjudication_revokes_exact_lesson_link(ledger, opened, actor, observed, lesson_data, criticism):
    critic, ident, objection, assessment = criticism
    result = propose(ledger, opened, actor, lesson_data, assessment)
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    learning.operator(scope, result["version_id"], "approve", "Checked", "history-approve")
    critic.assess(REPO, opened["id"], actor, 1, "history-reassess", ident, objection,
                  "inconclusive", "none", "Further context is needed", "Synthetic", [])
    with ledger.store.connect() as conn:
        assert not learning.eligible(conn, scope, result["version_id"])
        assert not learning.version(conn, scope, result["version_id"])["critic_links_eligible"]
    assert learning.recall(scope, opened["id"])["lessons"] == []


def test_candidate_requires_all_verification_sources(ledger, opened, actor, observation_data, criticism, lesson_data):
    unrelated = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "unrelated-observation")["observation_id"]
    with pytest.raises(LedgerError, match="verification observations"):
        propose(ledger, opened, actor, {**lesson_data, "sources": [{"observation_id": unrelated, "relation": "supports"}]}, criticism[3])


def test_delayed_approval_rechecks_staleness(ledger, opened, actor, lesson_data, criticism):
    result = propose(ledger, opened, actor, lesson_data, criticism[3])
    ledger.operator_transfer(REPO, opened["id"], "another-owner", 1, "Synthetic transfer", "history-transfer")
    with pytest.raises(LedgerError, match="invalid source"):
        Learning(ledger.store).operator(ledger.scope(REPO), result["version_id"], "approve", "Checked", "history-approve")


def test_exports_keep_original_opinion_verification_and_assessment_separate(ledger, opened, actor, observed, criticism):
    result = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert result["findings"][0]["claim"] == "Original finding"
    assert result["assessments"] == []
    assert result["critic_runs"][0]["items"][0]["provenance"] == "critic_generated"
    assert result["critic_assessments"][0]["state"] == "refuted"
    assert result["critic_verifications"][0]["id"] == observed
    assert result["critic_verifications"][0]["provenance"] == "agent_reported"
    content = export(ledger, REPO, opened["id"], actor, format="markdown")["content"]
    for heading in ("Original findings", "Supported current assessments", "Critic opinions", "Critic verification observations", "Critic objection assessments", "Limitations"):
        assert "## " + heading in content
    assert "would" in content and "withdraw" in content
    with pytest.raises(LedgerError, match="budget"):
        export(ledger, REPO, opened["id"], actor, format="json", max_chars=2000)
    page = json.loads(export(ledger, REPO, opened["id"], actor, format="json", offset=1)["content"])
    assert page["critic_runs"] == []


def test_invalidated_verification_suspends_linked_lesson(ledger, opened, actor, observed, lesson_data, criticism):
    result = propose(ledger, opened, actor, lesson_data, criticism[3])
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    learning.operator(scope, result["version_id"], "approve", "Checked", "history-approve")
    ledger.operator_invalidate(REPO, observed, "Synthetic source withdrawn", "history-invalidate")
    with ledger.store.connect() as conn:
        version = learning.version(conn, scope, result["version_id"])
    assert version["state"] == "suspended"
    assert not version["eligible_now"] and not version["critic_links_eligible"]
    exported = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert exported["critic_runs"][0]["freshness"] == "needs_revalidation"
    assert exported["critic_assessments"][0]["freshness"] == "needs_revalidation"
    assert not exported["critic_verifications"][0]["valid"]


def test_operator_commands_are_explicit_and_abandon_is_not_cancellation(ledger, opened, criticism, monkeypatch, capsys):
    import argparse
    from review_ledger import operator, tools
    monkeypatch.setattr(tools, "ledger_for_context", lambda context: ledger)
    parser = argparse.ArgumentParser()
    operator.configure_parser(parser)
    args = parser.parse_args(["critic-assess", REPO, opened["id"], criticism[1],
                              "--generation", "1", "--objection-id", criticism[2],
                              "--state", "inconclusive", "--basis", "none", "--rationale", "Operator check",
                              "--limitations", "Synthetic only", "--request-key", "operator-assess"])
    assert operator.dispatch(object(), args) == 0
    assert json.loads(capsys.readouterr().out)["provenance"] == "local_operator"
    args = parser.parse_args(["critic-abandon", REPO, opened["id"], criticism[1],
                              "--reason", "Explicitly abandon", "--request-key", "operator-abandon"])
    assert operator.dispatch(object(), args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "abandoned"
    assert "not cancelled" in result["notice"]


def test_export_critic_history_has_independent_page_cursor(ledger, opened, actor, observed, criticism):
    critic = criticism[0]
    second = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Second original"}, "history-second-finding")["finding_id"]
    critic.prepare(REPO, opened["id"], actor, 1, "history-second-prepare", [second], [observed])
    first = json.loads(export(ledger, REPO, opened["id"], actor, format="json", limit=1)["content"])
    assert len(first["critic_runs"]) == 1 and first["omitted"]["critic_runs"]
    second_page = json.loads(export(ledger, REPO, opened["id"], actor, format="json", limit=1, offset=first["next_offset"])["content"])
    assert len(second_page["critic_runs"]) == 1
    assert second_page["critic_runs"][0]["id"] != first["critic_runs"][0]["id"]
