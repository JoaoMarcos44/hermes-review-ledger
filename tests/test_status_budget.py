"""Synthetic status wire budgets, lossless detail and scoped pagination."""
from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from review_ledger import tools
from review_ledger.models import Actor, LedgerError, canonical
from review_ledger.service import Ledger
from review_ledger.storage import Store


REPO = "synthetic/example"


def _large_observation(**changes):
    return {"kind": "inspection", "outcome": "inspection", "summary": "S" * 4000,
            "details": ('Í"\\\n' * 2000), "limitations": "L" * 4000,
            "environment": "E" * 2000, "command_text": "C" * 2000, **changes}


def _wire(ledger, actor, monkeypatch, **arguments):
    monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
    raw = tools.handle(None, "ledger_status", {"repository": REPO, **arguments},
                       session_id=actor.session_id)
    result = json.loads(raw)
    assert len(raw) == len(canonical(result))
    if result["state"] != "error":
        assert len(raw) <= arguments.get("max_chars", 24_000)
    return result


def _assemble(ledger, run_id, actor, monkeypatch, collection, ident=None, max_chars=4000):
    chunks, offset, checksum = [], 0, None
    while True:
        selectors = {"detail_collection": collection}
        if ident is not None:
            selectors["detail_id"] = ident
        page = _wire(ledger, actor, monkeypatch, run_id=run_id, **selectors,
                     offset=offset, max_chars=max_chars)
        assert page["state"] == "detail", page
        assert page["offset"] == offset and page["content_format"] == "canonical_json"
        assert checksum in (None, page["content_sha256"])
        checksum = page["content_sha256"]
        chunks.append(page["content"])
        offset = page["next_offset"]
        if offset is None:
            break
        assert offset == sum(map(len, chunks))
        assert page["complete"] is False
    content = "".join(chunks)
    assert len(content) == page["total_chars"]
    assert hashlib.sha256(content.encode()).hexdigest() == checksum
    return json.loads(content), checksum, len(chunks)


def test_small_status_preserves_complete_rows(ledger, opened, actor, observed, monkeypatch):
    page = _wire(ledger, actor, monkeypatch, run_id=opened["id"])
    assert page["run"] == opened
    row = page["observations"][0]
    assert row["id"] == observed and row["details"] and row["environment"]
    assert "detail_required" not in row and "detail_required" not in page["run"]


def test_ten_large_observations_fit_wire_budget_with_complete_detail(ledger, opened, actor, monkeypatch):
    ids = {ledger.record(REPO, opened["id"], actor, 1, "observation", _large_observation(), f"large-{index}")["observation_id"]
           for index in range(10)}
    page = _wire(ledger, actor, monkeypatch, run_id=opened["id"])
    assert {row["id"] for row in page["observations"]} == ids
    assert page["next_offset"] is None
    reference = next(row for row in page["observations"] if row.get("detail_required"))
    assert reference["detail_collection"] == "observation" and "summary" not in reference
    record, checksum, chunks = _assemble(ledger, opened["id"], actor, monkeypatch,
                                        "observation", reference["detail_id"])
    assert record["summary"] == _large_observation()["summary"]
    assert record["details"] == _large_observation()["details"]
    assert record["environment"] == _large_observation()["environment"]
    assert checksum == reference["content_sha256"] and chunks > 1


def test_large_run_snapshot_is_reachable_as_complete_detail(ledger, actor, synthetic_snapshot, monkeypatch):
    files = [{"filename": f"synthetic/{index}/" + "x" * 180, "sha": "c" * 40,
              "status": "modified", "patch_status": "available"} for index in range(200)]
    run = ledger.open({**synthetic_snapshot, "files": files, "total_files": len(files)}, actor, "large-snapshot")["run"]
    page = _wire(ledger, actor, monkeypatch, run_id=run["id"], max_chars=4000)
    assert page["run"]["detail_required"] and "snapshot" not in page["run"]
    assert page["run"]["generation"] == 1 and page["run"]["can_write"] is True
    record, checksum, chunks = _assemble(ledger, run["id"], actor, monkeypatch, "run")
    assert record == run
    assert checksum == page["run"]["content_sha256"] and chunks > 1


@pytest.mark.parametrize("max_chars", [4000, 4001, 24_000, 64_000])
def test_budgeted_pagination_keeps_every_collection_id(ledger, opened, actor, monkeypatch, max_chars):
    expected = {"observations": set(), "findings": set(), "assessments": set()}
    for index in range(30):
        observed = ledger.record(REPO, opened["id"], actor, 1, "observation", _large_observation(), f"o-{index}")
        expected["observations"].add(observed["observation_id"])
        if index < 17:
            finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "F" * 4000}, f"f-{index}")["finding_id"]
            expected["findings"].add(finding)
            assessment = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
                "finding_id": finding, "state": "unverified", "basis": "none", "rationale": "R" * 4000,
                "limitations": "L" * 4000, "observation_ids": [],
            }, f"a-{index}")["assessment_id"]
            expected["assessments"].add(assessment)
    seen, offset = {key: set() for key in expected}, 0
    while True:
        page = _wire(ledger, actor, monkeypatch, run_id=opened["id"], limit=25,
                     offset=offset, max_chars=max_chars)
        for key in seen:
            ids = {row["id"] for row in page[key]}
            assert not seen[key] & ids
            seen[key] |= ids
        next_offset = page["next_offset"]
        if next_offset is None:
            break
        assert next_offset > offset
        offset = next_offset
    assert seen == expected


def test_small_budget_history_cursors_do_not_skip_runs(ledger, opened, actor, synthetic_snapshot, monkeypatch):
    latest, expected = opened, {opened["id"]}
    for index in range(1, 31):
        latest = ledger.open({**synthetic_snapshot, "head_sha": f"{index:040x}"}, actor, f"head-{index}")["run"]
        expected.add(latest["id"])
    discovered, offset = set(), 0
    while True:
        page = _wire(ledger, actor, monkeypatch, pull_number=7, limit=25, offset=offset, max_chars=4000)
        ids = {row["id"] for row in page["runs"]}
        assert not discovered & ids
        discovered |= ids
        offset = page["next_offset"]
        if offset is None:
            break
    assert discovered == expected
    related, offset = set(), 0
    while True:
        page = _wire(ledger, actor, monkeypatch, run_id=latest["id"], limit=25,
                     history_offset=offset, max_chars=4000)["related_runs"]
        ids = {row["id"] for row in page["runs"]}
        assert not related & ids
        related |= ids
        offset = page["next_offset"]
        if offset is None:
            break
    assert related == expected - {latest["id"]}


def test_assessment_and_finding_detail_keep_claim_sources_and_text(ledger, opened, actor, observed, monkeypatch):
    claim = 'Synthetic "claim"\\path\n' * 120
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": claim}, "finding")["finding_id"]
    assessed = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "supported", "basis": "inspection", "rationale": "R" * 4000,
        "limitations": "L" * 4000, "observation_ids": [observed],
    }, "assessment")["assessment_id"]
    page = _wire(ledger, actor, monkeypatch, run_id=opened["id"], max_chars=4000)
    for collection, ident in (("assessment", assessed), ("finding", finding)):
        record, checksum, _ = _assemble(ledger, opened["id"], actor, monkeypatch, collection, ident)
        assert record["claim"] == claim
        reference = next(row for row in page[collection + "s"] if row["id"] == ident)
        assert checksum == reference["content_sha256"]
        if collection == "assessment":
            assert record["sources"] == [{"observation_id": observed, "relation": "supports"}]
            assert record["rationale"] == "R" * 4000 and record["limitations"] == "L" * 4000


def test_detail_is_read_only_and_checks_run_repository_profile(ledger, opened, actor, observed,
                                                             synthetic_snapshot, tmp_path, monkeypatch):
    follower = Actor("synthetic-follower")
    record, _, _ = _assemble(ledger, opened["id"], follower, monkeypatch, "observation", observed)
    assert record["id"] == observed
    current = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")["run"]
    other = ledger.open({**synthetic_snapshot, "repository_id": 1002, "repository_full_name": "synthetic/other"},
                        actor, "other-repository")["run"]
    for repository, run_id in ((REPO, current["id"]), ("synthetic/other", other["id"]), ("synthetic/other", opened["id"])):
        with pytest.raises(LedgerError) as rejected:
            ledger.status_detail(repository, run_id, follower, detail_collection="observation", detail_id=observed)
        assert rejected.value.code == "scope_not_found"
    isolated = Ledger(Store(tmp_path / "other-profile", "another-profile"), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
    with pytest.raises(LedgerError) as rejected:
        isolated.status_detail(REPO, opened["id"], follower, detail_collection="observation", detail_id=observed)
    assert rejected.value.code == "scope_not_found"
    assert ledger.status(REPO, current["id"], actor)["run"]["generation"] == 1


def test_detail_digest_changes_when_observation_is_invalidated(ledger, opened, actor, monkeypatch):
    ident = ledger.record(REPO, opened["id"], actor, 1, "observation", _large_observation(), "large")["observation_id"]
    first = _wire(ledger, actor, monkeypatch, run_id=opened["id"], detail_collection="observation",
                  detail_id=ident, max_chars=4000)
    ledger.operator_invalidate(REPO, ident, "Synthetic source correction", "invalidate")
    second = _wire(ledger, actor, monkeypatch, run_id=opened["id"], detail_collection="observation",
                   detail_id=ident, offset=first["next_offset"], max_chars=4000)
    assert first["content_sha256"] != second["content_sha256"]
    record, _, _ = _assemble(ledger, opened["id"], actor, monkeypatch, "observation", ident)
    assert record["valid"] == 0 and record["invalid_reason"] == "Synthetic source correction"


def test_finding_detail_matches_origin_or_assessment_in_the_selected_run(ledger, opened, actor,
                                                                       synthetic_snapshot, monkeypatch):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Historical synthetic claim"}, "finding")["finding_id"]
    data = {"finding_id": finding, "state": "unverified", "basis": "none",
            "rationale": "Synthetic reassessment", "limitations": "Synthetic only", "observation_ids": []}
    old_assessment = ledger.record(REPO, opened["id"], actor, 1, "assessment", data, "old-assessment")["assessment_id"]
    current = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")["run"]
    for collection, ident in (("finding", finding), ("assessment", old_assessment)):
        denied = _wire(ledger, actor, monkeypatch, run_id=current["id"], detail_collection=collection, detail_id=ident)
        assert denied["error"]["code"] == "scope_not_found"
    ledger.record(REPO, current["id"], actor, 1, "assessment", data, "current-assessment")
    record, _, _ = _assemble(ledger, current["id"], actor, monkeypatch, "finding", finding)
    assert record["claim"] == "Historical synthetic claim" and record["origin_run_id"] == opened["id"]
    other_pr = ledger.open({**synthetic_snapshot, "number": 8}, actor, "other-pr")["run"]
    denied = _wire(ledger, actor, monkeypatch, run_id=other_pr["id"], detail_collection="finding", detail_id=finding)
    assert denied["error"]["code"] == "scope_not_found"


@pytest.mark.parametrize("selectors", [
    {"detail_collection": "unknown"}, {"detail_collection": "observation"},
    {"detail_collection": "run", "detail_id": "not-applicable"},
    {"detail_collection": "run", "limit": 1}, {"detail_collection": "run", "history_offset": 0},
    {"detail_id": "missing-selector"}, {"detail_collection": "run", "offset": 1_000_000},
    {"max_chars": 3999}, {"max_chars": 64_001}, {"max_chars": True},
])
def test_status_rejects_invalid_detail_and_budget_fields(ledger, opened, actor, monkeypatch, selectors):
    response = _wire(ledger, actor, monkeypatch, run_id=opened["id"], **selectors)
    assert response["error"]["code"] == "invalid_input"


def test_pr_history_rejects_detail_selectors(ledger, opened, actor, monkeypatch):
    response = _wire(ledger, actor, monkeypatch, pull_number=7, detail_collection="run")
    assert response["error"]["code"] == "invalid_input"


def test_open_preview_only_returns_retained_snapshot_descriptors(ledger, actor, synthetic_snapshot, monkeypatch):
    module = ModuleType("agent.secret_scope")
    module.get_secret = lambda name: None
    module.UnscopedSecretError = type("UnscopedSecretError", (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
    files = [{"filename": f"synthetic/{index}/" + '"' * 3900,
              "previous_filename": f"synthetic/old/{index}/" + '"' * 3900,
              "sha": "c" * 40, "status": "renamed", "patch_status": "available", "patch": "Synthetic inert preview"}
             for index in range(200)]
    supplied = {**synthetic_snapshot, "files": files, "total_files": len(files)}
    snapshot = SimpleNamespace(as_dict=lambda: supplied)
    monkeypatch.setattr(tools, "GitHubClient", lambda **kwargs: SimpleNamespace(fetch_snapshot=lambda *args: snapshot))
    context = SimpleNamespace(get_config=lambda key, default=None: default)
    result = tools._open_review(context, ledger, REPO, actor, {"request_key": "preview", "pull_number": 7})
    retained = result["run"]["snapshot"]["files"]
    assert 0 < len(retained) < 5
    assert [item["filename"] for item in result["files"]] == [item["filename"] for item in retained]
    assert result["files_returned"] == len(retained)
    assert result["files_omitted_from_response"] == 200 - len(retained)
    assert result["files_not_retained_in_snapshot"] == 200 - len(retained)
    assert all("patch" not in item for item in retained)
