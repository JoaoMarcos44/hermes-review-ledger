"""Combined V1.5 and critic provenance regressions, using only synthetic data."""
import json

import pytest

from review_ledger.context import Context
from review_ledger.improvements import Improvements
from review_ledger.learning import Learning
from review_ledger.models import LedgerError
from review_ledger.reports import export
from test_critic_history import criticism  # noqa: F401 -- shared synthetic fixture

REPO = "synthetic/example"


@pytest.fixture
def linked_lesson(ledger, opened, actor, lesson_data, criticism):
    learning, scope = Learning(ledger.store, improvements_enabled=True), ledger.scope(REPO)
    data = {**lesson_data, "critic_assessment_ids": [criticism[3]]}
    version = learning.propose(scope, opened["id"], actor, 1, data, "v15-linked-lesson")["version_id"]
    learning.operator(scope, version, "approve", "Checked synthetic provenance", "v15-approve")
    return learning, scope, version


def improve(ledger, run, actor, linked_lesson):
    learning, scope, version = linked_lesson
    used = learning.use(scope, run["id"], actor, run["generation"], version,
                        "applicable", "Synthetic reuse", "v15-use")
    result = learning.result(scope, run["id"], actor, run["generation"], used["use_id"],
                            {"usefulness": "not_useful", "behavioral_result": "not_tested",
                             "execution_block": "none", "explanation": "Narrow the reported match",
                             "contribution": "redundant"}, "v15-result")
    assert result["improvement_id"] is not None
    return Improvements(ledger.store).propose(
        scope, run["id"], actor, run["generation"],
        {"target_version_id": version, "source_outcome_ids": [used["use_id"]],
         "changes": {"conditions": ["Only for a scoped retry"]},
         "reason": "Reported redundant retrieval", "expected_benefit": "A narrower hypothesis"},
        "v15-improvement")


def revoke(criticism, opened, actor):
    critic, ident, objection, _ = criticism
    critic.assess(REPO, opened["id"], actor, opened["generation"], "v15-reassess", ident, objection,
                  "inconclusive", "none", "Synthetic missing context", "Not independent evidence", [])


@pytest.mark.parametrize("later_run", [False, True])
def test_improvement_preserves_critic_provenance_across_runs(
        ledger, opened, synthetic_snapshot, actor, linked_lesson, criticism, later_run):
    learning, scope, _ = linked_lesson
    run = (ledger.open({**synthetic_snapshot, "number": 8}, actor, "v15-later-run")["run"]
           if later_run else opened)
    proposal = improve(ledger, run, actor, linked_lesson)
    with ledger.store.connect() as conn:
        candidate = learning.version(conn, scope, proposal["version_id"])
    assert candidate["critic_assessment_ids"] == [criticism[3]]
    assert candidate["critic_links_eligible"] and not candidate["eligible_now"]
    learning.operator(scope, proposal["version_id"], "approve", "Reviewed exact revision", "v15-improve-approve")
    context = Context(ledger, 64000).prepare(REPO, run["id"], actor, query="retry")
    assert any(s["id"] == proposal["version_id"] for r in context["records"] for s in r["sources"])
    revoke(criticism, opened, actor)
    assert learning.recall(scope, run["id"])["lessons"] == []
    assert not Improvements(ledger.store).inspect(scope, proposal["improvement_id"])["eligible_now"]
    with pytest.raises(LedgerError) as caught:
        Context(ledger, 64000).resume(REPO, run["id"], actor, manifest_id=context["manifest_id"])
    assert caught.value.code == "context_revoked"
    report = json.loads(export(ledger, REPO, run["id"], actor, format="json")["content"])
    selected = next(s for m in report["context_manifests"] for s in m["selections"] if s["id"] == proposal["version_id"])
    assert selected["eligible_now"] is False


def test_new_cross_run_critic_link_is_rejected(ledger, opened, actor, synthetic_snapshot, lesson_data, criticism):
    run = ledger.open({**synthetic_snapshot, "number": 8}, actor, "v15-other-run")["run"]
    with pytest.raises(LedgerError) as caught:
        Learning(ledger.store).propose(ledger.scope(REPO), run["id"], actor, 1,
                                      {**lesson_data, "critic_assessment_ids": [criticism[3]]}, "v15-new-cross-run")
    assert caught.value.code == "ineligible_source"


def test_revision_cannot_claim_uninherited_cross_run_provenance(
        ledger, opened, actor, synthetic_snapshot, lesson_data, criticism):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    previous = learning.propose(scope, opened["id"], actor, 1, lesson_data, "v15-unlinked")["version_id"]
    run = ledger.open({**synthetic_snapshot, "number": 8}, actor, "v15-other-run")["run"]
    with pytest.raises(LedgerError) as caught:
        learning.propose(scope, run["id"], actor, 1,
                         {**lesson_data, "previous_version_id": previous,
                          "critic_assessment_ids": [criticism[3]]}, "v15-uninherited")
    assert caught.value.code == "ineligible_source"


def test_delayed_improvement_approval_rechecks_critic_provenance(
        ledger, opened, actor, linked_lesson, criticism):
    learning, scope, _ = linked_lesson
    proposal = improve(ledger, opened, actor, linked_lesson)
    revoke(criticism, opened, actor)
    with pytest.raises(LedgerError) as caught:
        learning.operator(scope, proposal["version_id"], "approve", "Prior approval is stale", "v15-delayed")
    assert caught.value.code == "stale_improvement"


def test_improvement_approval_rejects_removed_provenance(ledger, opened, actor, linked_lesson):
    learning, scope, _ = linked_lesson
    proposal = improve(ledger, opened, actor, linked_lesson)
    with ledger.store.connect() as conn:
        conn.execute("DELETE FROM critic_lesson_links WHERE version_id=?", (proposal["version_id"],))
    with pytest.raises(LedgerError) as caught:
        learning.operator(scope, proposal["version_id"], "approve", "Previously inspected", "v15-tampered")
    assert caught.value.code == "stale_improvement"


def test_export_retains_both_context_and_critic_provenance(ledger, opened, actor, linked_lesson, criticism):
    context = Context(ledger, 64000).prepare(REPO, opened["id"], actor, query="retry")
    report = json.loads(export(ledger, REPO, opened["id"], actor, format="json")["content"])
    assert report["context_manifests"][0]["id"] == context["manifest_id"]
    assert report["critic_runs"][0]["id"] == criticism[1]
    assert report["critic_assessments"][0]["id"] == criticism[3]
    content = export(ledger, REPO, opened["id"], actor, format="markdown")["content"]
    for heading in ("Context provenance", "Original findings", "Critic opinions", "Critic objection assessments"):
        assert "## " + heading in content


def test_critic_usage_counts_are_bounded_without_persisting_content(ledger, opened):
    from review_ledger.usage import Usage
    from review_ledger.models import canonical
    scope = ledger.scope(REPO)
    usage = Usage(ledger.store, max_events=2)
    request, response = '{"action":"status"}', '{"synthetic_only":"private opinion text"}'
    for index in range(3):
        assert usage.record(scope, event_id=f"v15-critic-{index}", tool="ledger_critic", run_id=opened["id"],
                            request_text=request, response_text=response, latency_ms=0.5)
    with ledger.store.connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM usage_events")]
    assert len(rows) == 2
    assert all(row["tool"] == "ledger_critic" for row in rows)
    assert all(row["response_chars"] == len(response) for row in rows)
    assert "private opinion text" not in canonical(rows)
    report = usage.report(scope, opened["id"])
    assert report["provider_usage"]["total_tokens"]["value"] is None
    assert report["cost"]["value"] is None


def test_inherited_critic_link_still_requires_all_verification_sources(
        ledger, opened, actor, synthetic_snapshot, linked_lesson, lesson_data, criticism, observation_data):
    learning, scope, version = linked_lesson
    run = ledger.open({**synthetic_snapshot, "number": 8}, actor, "v15-inherit-run")["run"]
    unrelated = ledger.record(REPO, run["id"], actor, 1, "observation", observation_data,
                              "v15-unrelated-source")["observation_id"]
    with pytest.raises(LedgerError) as caught:
        learning.propose(scope, run["id"], actor, 1,
                         {**lesson_data, "previous_version_id": version,
                          "critic_assessment_ids": [criticism[3]],
                          "sources": [{"observation_id": unrelated, "relation": "supports"}]},
                         "v15-inherited-missing-source")
    assert caught.value.code == "ineligible_source"


def test_inherited_critic_provenance_cannot_cross_repository(
        ledger, opened, actor, synthetic_snapshot, linked_lesson, lesson_data, criticism):
    learning, _, version = linked_lesson
    run = ledger.open({**synthetic_snapshot, "repository_id": 1002,
                       "repository_node_id": "synthetic-other", "repository_full_name": "synthetic/other"},
                      actor, "v15-other-repository")["run"]
    with pytest.raises(LedgerError) as caught:
        learning.propose(ledger.scope("synthetic/other"), run["id"], actor, 1,
                         {**lesson_data, "previous_version_id": version,
                          "critic_assessment_ids": [criticism[3]]}, "v15-wrong-repository")
    assert caught.value.code == "scope_not_found"
