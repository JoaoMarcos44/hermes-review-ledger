"""Synthetic regressions for durable retries and bounded run retrieval."""
from __future__ import annotations

import copy

import pytest

from review_ledger.models import LedgerError, canonical
from review_ledger.service import MAX_SNAPSHOT_CHARS


REPO = "synthetic/example"


@pytest.mark.parametrize("handoff", ["release", "pause", "transfer", "complete", "supersede"])
def test_artifact_receipt_survives_handoff(
    ledger, opened, actor, observation_data, synthetic_snapshot, monkeypatch, handoff,
):
    data = {**observation_data, "artifact_text": "Synthetic durable attachment"}
    receipt = ledger.record(REPO, opened["id"], actor, 1, "observation", data, "attachment")
    if handoff == "transfer":
        ledger.operator_transfer(REPO, opened["id"], "synthetic-b", 1, "Synthetic recovery", "transfer")
    elif handoff == "supersede":
        ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")
    else:
        ledger.run_action(REPO, opened["id"], actor, 1, handoff, "handoff")

    def unexpected_stage(content):
        pytest.fail("An existing receipt must not stage another artifact")

    monkeypatch.setattr(ledger.store, "put_artifact", unexpected_stage)
    assert ledger.record(REPO, opened["id"], actor, 1, "observation", data, "attachment") == receipt
    with pytest.raises(LedgerError) as conflict:
        ledger.record(REPO, opened["id"], actor, 1, "observation",
                      {**data, "summary": "Different synthetic payload"}, "attachment")
    assert conflict.value.code == "idempotency_conflict"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations WHERE run_id=?", (opened["id"],)).fetchone()[0] == 1
    assert len(list((ledger.store.data_dir / "artifacts").iterdir())) == 1


def test_artifact_new_write_rechecks_owner_after_staging(ledger, opened, actor, observation_data, monkeypatch):
    original_stage = ledger.store.put_artifact

    def stage_then_transfer(content):
        artifact = original_stage(content)
        ledger.operator_transfer(REPO, opened["id"], "synthetic-b", 1, "Synthetic handoff", "transfer")
        return artifact

    monkeypatch.setattr(ledger.store, "put_artifact", stage_then_transfer)
    with pytest.raises(LedgerError) as conflict:
        ledger.record(REPO, opened["id"], actor, 1, "observation",
                      {**observation_data, "artifact_text": "Synthetic attachment"}, "attachment")
    assert conflict.value.code == "ownership_conflict"
    assert list((ledger.store.data_dir / "artifacts").iterdir()) == []
    assert ledger.status(REPO, opened["id"], actor)["observations"] == []


def test_artifact_concurrent_receipt_rechecked_in_writer(ledger, opened, actor, observation_data, monkeypatch):
    data = {**observation_data, "artifact_text": "Synthetic attachment"}
    original_stage = ledger.store.put_artifact
    committed = []

    def stage_after_other_request(content):
        artifact = original_stage(content)
        monkeypatch.setattr(ledger.store, "put_artifact", original_stage)
        committed.append(ledger.record(REPO, opened["id"], actor, 1, "observation", data, "attachment"))
        ledger.run_action(REPO, opened["id"], actor, 1, "release", "release")
        return artifact

    monkeypatch.setattr(ledger.store, "put_artifact", stage_after_other_request)
    result = ledger.record(REPO, opened["id"], actor, 1, "observation", data, "attachment")
    assert result == committed[0]
    assert len(ledger.status(REPO, opened["id"], actor)["observations"]) == 1
    assert len(list((ledger.store.data_dir / "artifacts").iterdir())) == 1


@pytest.mark.parametrize("upstream_omissions", [0, 50])
def test_long_file_metadata_captures_prefix_with_truthful_omissions(
    ledger, actor, synthetic_snapshot, upstream_omissions,
):
    files = [{"filename": f"synthetic/{index}/" + "x" * 3900,
              "previous_filename": f"synthetic/previous/{index}/" + "y" * 3900,
              "sha": "c" * 40, "status": "renamed", "patch_status": "available"}
             for index in range(200)]
    supplied = {**synthetic_snapshot, "files": files,
                "total_files": len(files) + upstream_omissions,
                "omitted_files": upstream_omissions,
                "files_complete": upstream_omissions == 0,
                "patches_complete": upstream_omissions == 0,
                "truncation_reasons": ["max_files"] if upstream_omissions else []}
    before = copy.deepcopy(supplied)
    receipt = ledger.open(supplied, actor, "long-file-metadata")
    saved = receipt["run"]["snapshot"]
    retained = len(saved["files"])
    assert 0 < retained < len(files)
    assert len(canonical(saved)) <= MAX_SNAPSHOT_CHARS
    assert saved["files"] == files[:retained]
    assert saved["omitted_files"] == supplied["total_files"] - retained
    assert saved["files_complete"] is False
    assert saved["patches_complete"] is False
    assert "snapshot_metadata_budget" in saved["truncation_reasons"]
    if upstream_omissions:
        assert "max_files" in saved["truncation_reasons"]
    assert supplied == before
    assert ledger.open(supplied, actor, "long-file-metadata") == receipt


def test_long_metadata_preserves_unknown_upstream_omissions(ledger, actor, synthetic_snapshot):
    supplied = {**synthetic_snapshot, "files": [
        {"filename": f"synthetic/{index}/" + "x" * 3900, "sha": "c" * 40,
         "status": "modified", "patch_status": "available"} for index in range(30)
    ], "total_files": None, "omitted_files": None, "truncation_reasons": None}
    saved = ledger.open(supplied, actor, "unknown-omissions")["run"]["snapshot"]
    assert len(canonical(saved)) <= MAX_SNAPSHOT_CHARS
    assert saved["omitted_files"] is None
    assert saved["files_complete"] is False
    assert "snapshot_metadata_budget" in saved["truncation_reasons"]


def test_findings_include_origin_and_reassessment_without_duplicates(
    ledger, opened, actor, synthetic_snapshot,
):
    inherited = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Earlier synthetic claim"}, "earlier")["finding_id"]
    unrelated = ledger.open({**synthetic_snapshot, "number": 8}, actor, "other-pr")["run"]
    ledger.record(REPO, unrelated["id"], actor, 1, "finding", {"claim": "Other PR claim"}, "unrelated")
    current = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")["run"]
    new = ledger.record(REPO, current["id"], actor, 1, "finding", {"claim": "Current synthetic claim"}, "current")["finding_id"]
    for key, finding in [("earlier-v1", inherited), ("earlier-v2", inherited), ("current-v1", new)]:
        ledger.record(REPO, current["id"], actor, 1, "assessment", {
            "finding_id": finding, "state": "unverified", "basis": "none",
            "rationale": "Synthetic", "limitations": "Synthetic", "observation_ids": [],
        }, key)
    expected = sorted([inherited, new])
    assert [row["id"] for row in ledger.status(REPO, current["id"], actor)["findings"]] == expected
    with ledger.store.connect() as conn:
        scope = ledger.scope(REPO)
        assert [row["id"] for row in ledger._findings(conn, scope, current["id"], 1, 1)] == expected[1:]


def test_findings_query_uses_run_indexes(ledger, opened):
    statements = []

    class RecordingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters):
            statements.append((statement, parameters))
            return self.connection.execute(statement, parameters)

    with ledger.store.connect() as conn:
        ledger._findings(RecordingConnection(conn), ledger.scope(REPO), opened["id"], 2, 0)
        statement, parameters = statements[0]
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + statement, parameters)]
    assert any("SEARCH f" in detail and "origin_run_id=?" in detail for detail in plan), plan
    assert any("SEARCH a" in detail and "run_id=?" in detail for detail in plan), plan
    assert not any("SCAN a" in detail or "CORRELATED" in detail for detail in plan), plan
