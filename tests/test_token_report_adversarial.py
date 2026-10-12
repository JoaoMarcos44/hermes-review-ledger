"""Adversarial token contracts grounded in the public host's real metadata shape."""
from unittest.mock import MagicMock

import pytest

from review_ledger.models import Actor
from review_ledger.token_report import TokenReport
from test_token_report import FakeMetadataReader, _main_metadata


@pytest.mark.parametrize("case", ["missing_total", "reasoning_subset", "worker_selection", "lineage_overlap", "aggregate_overflow"])
def test_token_scope_and_aggregate_contracts(ledger, synthetic_snapshot, opened, actor, case):
    repo = "synthetic/example"
    if case == "missing_total":
        result = TokenReport(ledger, FakeMetadataReader()).report(repo, run_id=opened["id"])
        assert result["totals"]["combined"]["total_tokens"] is None
        return
    if case == "reasoning_subset":
        reader = FakeMetadataReader({actor.session_id: _main_metadata(output_tokens=1, reasoning_tokens=2)})
        with pytest.raises(ValueError):
            TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        return
    if case == "aggregate_overflow":
        max_safe = 2**53 - 1
        reader = FakeMetadataReader({actor.session_id: _main_metadata(input_tokens=max_safe, cache_read_tokens=1, cache_write_tokens=0, output_tokens=0, reasoning_tokens=0)})
        with pytest.raises(ValueError):
            TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        return
    root_id, child_id = actor.session_id, "synthetic-child-session"
    main = {root_id: _main_metadata(id=root_id), child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent")}

    class LineageReader(FakeMetadataReader):
        def list_sessions_metadata(self):
            return list(self.sessions.values())
        def auxiliary_lineage_ids(self, sid):
            return [root_id, child_id] if case == "lineage_overlap" else [sid]

    auxiliary = {"compression": {"input_tokens": 200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "api_call_count": 1}}
    reader = LineageReader(main, {root_id: auxiliary, child_id: auxiliary})
    if case == "worker_selection":
        report = TokenReport(ledger, reader).report(repo, run_id=opened["id"], include_workers=True)
        assert {s["session_id"] for s in report["sessions"]} == {root_id, child_id}
        assert report["totals"]["session_count"] == 2
    else:
        ledger.open({**synthetic_snapshot, "number": 8}, Actor(child_id), "second-actor")
        report = TokenReport(ledger, reader).report(repo)
        assert report["totals"]["auxiliary"]["total_tokens"] == 280


@pytest.mark.parametrize("case", ["host_auxiliary_alias", "operator_transfer_history"])
def test_native_shape_and_durable_owner_correlation(ledger, opened, actor, case):
    if case == "host_auxiliary_alias":
        from review_ledger.hermes_usage import SessionDBMetadataReader
        db = MagicMock()
        db.auxiliary_usage_by_task.return_value = {"compression": {"api_calls": 2, "input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}}
        reader = SessionDBMetadataReader.__new__(SessionDBMetadataReader)
        reader._db_path = None
        reader._db = db
        normalized = reader.auxiliary_usage_by_task(actor.session_id)
        assert normalized["compression"]["api_call_count"] == 2
        assert "api_calls" not in normalized["compression"]
        return
    new_owner = Actor("synthetic-transferred-owner")
    moved = ledger.operator_transfer("synthetic/example", opened["id"], new_owner.session_id, opened["generation"], "Synthetic operator fixture only", "transfer")
    generation = moved["run"]["generation"]
    ledger.run_action("synthetic/example", opened["id"], new_owner, generation, "complete", "transferred-complete")
    reader = FakeMetadataReader({actor.session_id: _main_metadata(id=actor.session_id), new_owner.session_id: _main_metadata(id=new_owner.session_id)})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    assert {s["session_id"] for s in report["sessions"]} == {actor.session_id, new_owner.session_id}


# ── Additional regression controls ────────────────────────────────────────────


def test_worker_cycle_safe(ledger, opened, actor):
    """Worker expansion terminates on cyclic parent_session_id references."""
    root_id, child_id = actor.session_id, "synthetic-child-session"
    # Create a cycle: child points to root, root points to child
    main = {
        root_id: _main_metadata(id=root_id, parent_session_id=child_id),
        child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent"),
    }

    class CyclicReader(FakeMetadataReader):
        def list_sessions_metadata(self):
            return list(self.sessions.values())

    reader = CyclicReader(main, {})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"], include_workers=True)
    # Should include both without infinite loop
    assert {s["session_id"] for s in report["sessions"]} == {root_id, child_id}


def test_duplicate_direct_worker_ids_deduped(ledger, opened, actor):
    """Duplicate worker IDs from list_sessions_metadata are deduplicated."""
    root_id, child_id = actor.session_id, "synthetic-child-session"
    main = {
        root_id: _main_metadata(id=root_id),
        child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent"),
    }

    class DuplicateReader(FakeMetadataReader):
        def list_sessions_metadata(self):
            # Return child twice
            return [self.sessions[root_id], self.sessions[child_id], self.sessions[child_id]]

    reader = DuplicateReader(main, {})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"], include_workers=True)
    assert report["totals"]["session_count"] == 2


def test_unrelated_equal_aux_values_both_count(ledger, synthetic_snapshot, opened, actor):
    """Two sessions with equal aux values but different lineages both count."""
    root_id, child_id = actor.session_id, "synthetic-child-session"
    main = {root_id: _main_metadata(id=root_id), child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent")}
    auxiliary = {"compression": {"input_tokens": 200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "api_call_count": 1}}

    class SeparateLineageReader(FakeMetadataReader):
        def auxiliary_lineage_ids(self, sid):
            return [sid]  # Each session has its own lineage

    reader = SeparateLineageReader(main, {root_id: auxiliary, child_id: auxiliary})
    ledger.open({**synthetic_snapshot, "number": 8}, Actor(child_id), "second-actor")
    report = TokenReport(ledger, reader).report("synthetic/example")
    # Both sessions have separate lineages, so both aux values count
    assert report["totals"]["auxiliary"]["total_tokens"] == 560


def test_partial_aux_coverage(ledger, opened, actor):
    """Partial auxiliary coverage is reported as partial, not complete."""
    aux = {"compression": {"input_tokens": 200, "output_tokens": None, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "api_call_count": 1}}
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()}, auxiliary={actor.session_id: aux})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    session = report["sessions"][0]
    assert session["auxiliary"]["coverage"] == "partial"


def test_aggregate_multi_session_overflow(ledger, synthetic_snapshot, opened, actor):
    """Sum of multiple sessions exceeding 2**53-1 raises ValueError."""
    max_safe = 2**53 - 1
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    child_actor = Actor("synthetic-child-session")
    ledger.open(snap8, child_actor, "overflow-open-8")
    reader = FakeMetadataReader(
        sessions={sid: _main_metadata(id=sid, input_tokens=max_safe//2+1, output_tokens=0,
                                     reasoning_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
                  for sid in (actor.session_id, child_actor.session_id)}
    )
    with pytest.raises(ValueError, match="overflow"):
        TokenReport(ledger, reader).report("synthetic/example")


def test_shared_unselected_scope_not_counted(ledger, synthetic_snapshot, opened, actor):
    """A session shared with another run is not counted when not in scope."""
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    child_actor = Actor("synthetic-child-session")
    ledger.open(snap8, child_actor, "shared-open-8")
    # Report on run opened (actor's session only)
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    assert report["totals"]["session_count"] == 1


def test_invalid_flags_rejected(ledger, opened, actor):
    """Invalid flag values are handled gracefully."""
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    with pytest.raises(ValueError, match="include_workers"):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"], include_workers=1)
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"], include_workers=False)
    assert report["totals"]["session_count"] == 1


def test_adapter_list_sessions_metadata_allowlist():
    """SessionDBMetadataReader.list_sessions_metadata strips prompt/config fields."""
    from review_ledger.hermes_usage import SessionDBMetadataReader
    mock_db = MagicMock()
    mock_db.list_sessions_rich.return_value = [
        {"id": "sess-1", "source": "cli", "model": "m", "input_tokens": 10,
         "system_prompt": "SECRET", "model_config": '{"secret": true}', "preview": "SECRET",
         "parent_session_id": None, "api_call_count": 1}
    ]
    reader = SessionDBMetadataReader.__new__(SessionDBMetadataReader)
    reader._db_path = None
    reader._db = mock_db
    result = reader.list_sessions_metadata()
    assert len(result) == 1
    assert "SECRET" not in str(result[0])
    assert "system_prompt" not in result[0]
    assert "model_config" not in result[0]
    assert "preview" not in result[0]
    assert result[0]["input_tokens"] == 10
