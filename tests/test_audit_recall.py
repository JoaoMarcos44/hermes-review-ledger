"""Synthetic regressions for literal search, bounded retrieval and wire budgets."""
from __future__ import annotations

from contextlib import contextmanager
from itertools import count
import json
import unicodedata

import pytest

from review_ledger.learning import Learning
from review_ledger.models import canonical
from review_ledger import tools


REPO = "synthetic/example"


def _approve(ledger, opened, actor, data, key):
    learning = Learning(ledger.store)
    scope = ledger.scope(REPO)
    version = learning.propose(scope, opened["id"], actor, 1, data, "propose-" + key)["version_id"]
    learning.operator(scope, version, "approve", "Synthetic approval", "approve-" + key)
    return version


@pytest.mark.parametrize("field,query_key", [
    ("conditions", "terms"), ("exclusions", "terms"), ("tags", "tags"), ("symbols", "symbols"),
])
@pytest.mark.parametrize("value", [r'C:\work\100%_ação.py', 'chave"ÍNDICE'])
def test_search_matches_decoded_literal_values(ledger, opened, actor, lesson_data, field, query_key, value):
    data = {**lesson_data, "question": "Synthetic unrelated question", "verification": "Synthetic unrelated check",
            "conditions": ["Synthetic unrelated condition"], "exclusions": [], "tags": [], "symbols": [],
            field: [value]}
    version = _approve(ledger, opened, actor, data, "literal")
    query = unicodedata.normalize("NFD", value.casefold())
    learning = Learning(ledger.store)
    result = learning.recall(ledger.scope(REPO), opened["id"], **{query_key: [query]})

    assert [item["id"] for item in result["lessons"]] == [version]
    assert result["lessons"][0][field] == [value]
    if "%" in query:
        # A wildcard character supplied by the caller remains literal after JSON decoding.
        assert not learning.recall(ledger.scope(REPO), opened["id"],
                                   **{query_key: [query.replace("%", "_")]})["lessons"]


def test_term_ranking_uses_the_same_decoded_values(ledger, opened, actor, lesson_data, monkeypatch):
    sequence = count()
    monkeypatch.setattr("review_ledger.learning.new_id", lambda kind: f"{kind}_{next(sequence):032x}")
    escaped = r'C:\work\"index".py'
    common = {**lesson_data, "question": "RankingMarker", "conditions": ["Synthetic condition"],
              "exclusions": [], "tags": [], "symbols": [], "verification": "Synthetic check"}
    _approve(ledger, opened, actor, common, "weak")
    stronger = _approve(ledger, opened, actor, {**common, "symbols": [escaped]}, "strong")

    result = Learning(ledger.store).recall(ledger.scope(REPO), opened["id"],
                                          terms=["RankingMarker", escaped], limit=1)
    assert [item["id"] for item in result["lessons"]] == [stronger]


def test_candidate_windows_load_only_result_page_details(ledger, opened, actor, lesson_data, monkeypatch):
    expected = {_approve(ledger, opened, actor, {**lesson_data, "question": f"Synthetic window lesson {index}"}, str(index))
                for index in range(205)}
    statements = []
    connect = ledger.store.connect

    @contextmanager
    def traced_connect():
        with connect() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(ledger.store, "connect", traced_connect)
    learning, scope = Learning(ledger.store), ledger.scope(REPO)
    statements.clear()
    first = learning.recall(scope, opened["id"], context_budget=500)
    source_queries = [statement for statement in statements if "from lesson_sources" in statement.casefold()]
    assert len(source_queries) == 6  # One eligibility aggregate and five full detail loads.
    assert first["next_offset"] == 200 and first["next_result_offset"] == 5

    seen, offset, result_offset = set(), 0, 0
    while True:
        page = learning.recall(scope, opened["id"], offset=offset, result_offset=result_offset, context_budget=500)
        ids = {reference["version_id"] for reference in page["lesson_references"]}
        assert not page["lessons"] and 1 <= len(ids) <= 5
        assert not seen & ids
        seen |= ids
        if page["next_result_offset"] is not None:
            result_offset = page["next_result_offset"]
        elif page["next_offset"] is not None:
            offset, result_offset = page["next_offset"], 0
        else:
            break
    assert seen == expected


def test_recall_budget_matches_the_serialized_tool_lesson(ledger, opened, actor, lesson_data, monkeypatch):
    data = {**lesson_data, "question": "SerializationBudgetMarker", "conditions": ["C"],
            "exclusions": [], "tags": [], "symbols": [], "verification": "V"}
    baseline = _approve(ledger, opened, actor, data, "budget-baseline")
    with ledger.store.connect() as conn:
        baseline_size = len(canonical(Learning.version(conn, ledger.scope(REPO), baseline)))
    remaining = 5999 - baseline_size
    for field, maximum in (("verification", 2000), ("question", 1500)):
        growth = min(remaining, maximum - len(data[field]))
        data[field] += "x" * growth
        remaining -= growth
    growth = min(remaining, 499)
    data["conditions"][0] += "x" * growth
    remaining -= growth
    while remaining:
        length = min(500, remaining - 3)
        assert length > 0
        data["conditions"].append(str(len(data["conditions"])) + "x" * (length - 1))
        remaining -= length + 3
    version = _approve(ledger, opened, actor, data, "budget-full")
    monkeypatch.setattr(tools, "ledger_for_context", lambda _ctx: ledger)

    raw = tools.handle(None, "ledger_recall", {"repository": REPO, "run_id": opened["id"],
                       "terms": ["SerializationBudgetMarkerxxxx"], "context_budget": 6000},
                       session_id=actor.session_id)
    response = json.loads(raw)
    assert [item["id"] for item in response["lessons"]] == [version]
    start = raw.index("[", raw.index('"lessons"')) + 1
    while raw[start].isspace():
        start += 1
    _, end = json.JSONDecoder().raw_decode(raw[start:])
    assert len(raw[start:start + end]) == response["context_chars"] == 5999
    assert response["context_chars"] <= response["context_budget"]
