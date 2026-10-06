"""Normal synthetic continuity, Unicode and bounded-retrieval regressions."""
from __future__ import annotations

import hashlib
import json
import unicodedata

import pytest

from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError, canonical
from review_ledger.reports import export

REPO = "synthetic/example"


def approved(ledger, opened, actor, lesson_data, *, key="one", **changes):
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    result = learning.propose(scope, opened["id"], actor, 1,
                              {**lesson_data, **changes}, "propose-" + key)
    learning.operator(scope, result["version_id"], "approve", "Synthetic operator evaluation", "approve-" + key)
    return result["version_id"]


def test_new_session_discovers_old_run_without_saved_ids(ledger, opened, actor, synthetic_snapshot, observed):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic historical claim"}, "finding")["finding_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "supported", "basis": "inspection", "rationale": "Synthetic source inspection",
        "limitations": "Not an executed external project", "observation_ids": [observed],
    }, "assessment")
    current = ledger.open({**synthetic_snapshot, "head_sha": "d" * 40}, actor, "new-head")["run"]
    fresh = Actor("entirely-new-session")
    index = ledger.history(REPO, 7, limit=1)
    assert index["runs"][0]["id"] == current["id"]
    assert index["runs"][0]["snapshot_state"] == "latest_recorded"
    second = ledger.history(REPO, 7, limit=1, offset=index["next_offset"])
    ref = second["runs"][0]
    assert ref["snapshot_state"] == "historical" and ref["head_sha"] == "a" * 40
    assert "owner_session" not in ref and "generation" not in ref
    old = ledger.status(REPO, ref["id"], fresh)
    assert old["run"]["can_write"] is False
    assert old["assessments"][0]["freshness"] == "historical"
    assert ledger.status(REPO, current["id"], fresh)["assessments"] == []
    assert json.loads(export(ledger, REPO, ref["id"], fresh, format="json")["content"])["assessments"][0]["freshness"] == "historical"
    with pytest.raises(LedgerError):
        ledger.record(REPO, ref["id"], fresh, 1, "finding", {"claim": "Cannot edit"}, "fresh-edit")


def test_history_is_bounded_to_repository_pr_and_profile(ledger, opened, actor, synthetic_snapshot, tmp_path):
    from review_ledger.service import Ledger
    from review_ledger.storage import Store
    for index in range(1, 4):
        ledger.open({**synthetic_snapshot, "head_sha": str(index) * 40}, actor, f"head-{index}")
    ledger.open({**synthetic_snapshot, "number": 8}, actor, "another-pr")
    ledger.open({**synthetic_snapshot, "repository_id": 1002, "repository_full_name": "synthetic/other"}, actor, "another-repo")
    rows, offset = [], 0
    while True:
        page = ledger.history(REPO, 7, limit=2, offset=offset)
        assert len(page["runs"]) <= 2
        rows.extend(page["runs"])
        offset = page["next_offset"]
        if offset is None:
            break
    assert len(rows) == 4 and len({r["id"] for r in rows}) == 4
    assert sum(r["snapshot_state"] == "latest_recorded" for r in rows) == 1
    other = Ledger(Store(tmp_path / "other-profile", "different"), [REPO], skill_version="x", skill_hash="y")
    with pytest.raises(LedgerError, match="Open this repository"):
        other.history(REPO, 7)
    with pytest.raises(LedgerError):
        ledger.history("unapproved/repository", 7)


def test_status_includes_independently_paginated_related_runs(ledger, opened, actor, synthetic_snapshot):
    current = opened
    for index in range(1, 4):
        current = ledger.open({**synthetic_snapshot, "head_sha": str(index) * 40}, actor, f"head-{index}")["run"]
    first = ledger.status(REPO, current["id"], actor, limit=1)
    assert first["historical_runs"] == 3
    assert first["next_offset"] is None
    related = first["related_runs"]
    assert related["next_offset"] == 1
    next_page = ledger.status(REPO, current["id"], actor, limit=1, history_offset=1)["related_runs"]
    assert related["runs"][0]["id"] != next_page["runs"][0]["id"]


@pytest.mark.parametrize("query", ["ÍNDICE", "índice", "Índice", unicodedata.normalize("NFD", "índice")])
def test_unicode_search_uses_same_normalization_and_preserves_text(ledger, opened, actor, lesson_data, query):
    ident = approved(ledger, opened, actor, lesson_data, question="ÍNDICE de alteração?", tags=["AÇÃO"], symbols=["SALVAR_ÍNDICE"])
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    for kind, term in (("terms", query), ("tags", "ação"), ("symbols", "salvar_índice")):
        result = learning.recall(scope, opened["id"], **{kind: [term]})
        assert [item["id"] for item in result["lessons"]] == [ident]
        assert result["lessons"][0]["question"] == "ÍNDICE de alteração?"
        assert result["lessons"][0]["tags"] == ["AÇÃO"]


def test_unicode_search_preserves_literal_wildcards(ledger, opened, actor, lesson_data):
    approved(ledger, opened, actor, lesson_data, question="Percent 100% and _underscore")
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    assert learning.recall(scope, opened["id"], terms=["100%"])['lessons']
    assert not learning.recall(scope, opened["id"], terms=["100_"])['lessons']


def test_reference_pages_and_detail_reassemble_every_field(ledger, opened, actor, lesson_data):
    conditions = [f"Condition {i}: " + "Í" * 470 for i in range(10)]
    exclusions = [f"Exclusion {i}: " + '"\\' * 225 for i in range(10)]
    ident = approved(ledger, opened, actor, lesson_data, conditions=conditions, exclusions=exclusions)
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    recalled = learning.recall(scope, opened["id"], context_budget=500)
    assert recalled["lessons"] == []
    ref = recalled["lesson_references"][0]
    assert ref["version_id"] == ident and ref["required_context_chars"] > 6000
    assert recalled["context_chars"] <= 500
    chunks, offset, digest = [], 0, None
    while True:
        page = learning.detail(scope, opened["id"], ident, context_budget=500, offset=offset)
        assert page["context_chars"] <= 500
        assert page["context_chars"] == len(canonical(page["content"]))
        assert page["offset"] == offset
        assert digest in (None, page["content_sha256"])
        digest = page["content_sha256"]
        chunks.append(page["content"])
        offset = page["next_offset"]
        if offset is None:
            break
    content = "".join(chunks)
    assert hashlib.sha256(content.encode()).hexdigest() == digest
    assert len(content) == ref["required_context_chars"]
    restored = json.loads(content)
    assert restored["conditions"] == sorted(conditions)
    assert restored["exclusions"] == sorted(exclusions)
    with ledger.store.connect() as conn:
        assert restored == learning.version(conn, scope, ident)
    with pytest.raises(LedgerError):
        learning.detail(scope, opened["id"], ident, offset=len(content))


def test_every_budget_reference_reachable_with_result_pagination(ledger, opened, actor, lesson_data):
    expected = {approved(ledger, opened, actor, lesson_data, key=str(i), question=f"Synthetic strategy {i}") for i in range(7)}
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    seen, result_offset = set(), 0
    while True:
        page = learning.recall(scope, opened["id"], context_budget=500, result_offset=result_offset)
        refs = page["lesson_references"]
        assert page["lessons"] == []
        assert 1 <= len(refs) <= 5 and page["context_chars"] <= 500
        ids = {ref["version_id"] for ref in refs}
        assert not seen & ids
        seen |= ids
        result_offset = page["next_result_offset"]
        if result_offset is None:
            break
    assert seen == expected


@pytest.mark.parametrize("revoke", ["suspend", "invalidate", "supersede"])
def test_detail_rechecks_exact_version_eligibility_each_page(ledger, opened, actor, lesson_data, observed, revoke):
    ident = approved(ledger, opened, actor, lesson_data)
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    first = learning.detail(scope, opened["id"], ident, context_budget=500)
    assert first["next_offset"] is not None
    if revoke == "invalidate":
        ledger.operator_invalidate(REPO, observed, "Synthetic correction", "invalidate")
    elif revoke == "supersede":
        approved(ledger, opened, actor, lesson_data, key="new", previous_version_id=ident)
    else:
        learning.operator(scope, ident, "suspend", "Synthetic review", "suspend")
    with pytest.raises(LedgerError) as exc:
        learning.detail(scope, opened["id"], ident, context_budget=500, offset=first["next_offset"])
    assert exc.value.code == "lesson_not_eligible"
    with pytest.raises(LedgerError):
        learning.use(scope, opened["id"], actor, 1, ident, "applicable", "Synthetic", "stale-use")


def test_detail_obeys_repository_run_scope(ledger, opened, actor, lesson_data, synthetic_snapshot):
    ident = approved(ledger, opened, actor, lesson_data)
    other = ledger.open({**synthetic_snapshot, "repository_id": 1002, "repository_full_name": "synthetic/other"}, actor, "other")["run"]
    with pytest.raises(LedgerError):
        Learning(ledger.store).detail(ledger.scope("synthetic/other"), other["id"], ident)
    with pytest.raises(LedgerError):
        Learning(ledger.store).detail(ledger.scope(REPO), other["id"], ident)


@pytest.mark.parametrize("second_time", ["2026-10-06T21:00:00+00:00", "2026-10-06T21:00:01+00:00"])
def test_history_append_order_survives_clock_rollback_or_ties(ledger, actor, synthetic_snapshot, monkeypatch, second_time):
    monkeypatch.setattr("review_ledger.service.now", lambda: "2026-10-06T21:00:01+00:00")
    old = ledger.open(synthetic_snapshot, actor, "before-clock-change")["run"]
    monkeypatch.setattr("review_ledger.service.now", lambda: second_time)
    new = ledger.open({**synthetic_snapshot, "head_sha": "d" * 40}, actor, "after-clock-change")["run"]
    index = ledger.history(REPO, 7, limit=1)
    assert index["runs"][0]["id"] == new["id"]
    assert index["runs"][0]["snapshot_state"] == "latest_recorded"
    older = ledger.history(REPO, 7, limit=1, offset=index["next_offset"])
    assert older["runs"][0]["id"] == old["id"]
    assert older["runs"][0]["snapshot_state"] == "historical"
