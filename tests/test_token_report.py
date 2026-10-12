"""Synthetic token-report contracts: canonical host counters, provenance-safe correlation.

All fixtures are synthetic. No live host profile, real session data, or provider
response is read or fabricated as live evidence.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from review_ledger.models import Actor, LedgerError
from review_ledger.token_report import TokenReport, format_report_human


class FakeMetadataReader:
    """Injected reader seam: returns allowlisted metadata, never prompt text."""

    def __init__(self, sessions=None, auxiliary=None):
        self.sessions = sessions or {}
        self.auxiliary = auxiliary or {}
        self.closed = False
        self.call_log = []

    def get_session(self, session_id):
        self.call_log.append(("get_session", session_id))
        return self.sessions.get(session_id)

    def auxiliary_usage_by_task(self, session_id):
        self.call_log.append(("auxiliary_usage_by_task", session_id))
        return self.auxiliary.get(session_id, {})

    def close(self):
        self.closed = True


def _main_metadata(**overrides):
    base = {
        "id": "sess-a",
        "source": "cli",
        "model": "synthetic-model",
        "billing_provider": "synthetic-provider",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 20,
        "cache_write_tokens": 10,
        "reasoning_tokens": 15,
        "api_call_count": 3,
        "estimated_cost_usd": 0.01,
        "actual_cost_usd": None,
        "cost_status": "estimated",
    }
    base.update(overrides)
    return base


# ── Cycle 1: canonical accounting ────────────────────────────────────────────


def test_canonical_prompt_and_total_from_main_counters(ledger, opened, actor):
    """prompt = input + cache_read + cache_write; total = prompt + output."""
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    main = report["sessions"][0]["main"]
    assert main["input_tokens"] == 100
    assert main["cache_read_tokens"] == 20
    assert main["cache_write_tokens"] == 10
    assert main["prompt_tokens"] == 130  # 100 + 20 + 10
    assert main["output_tokens"] == 50
    assert main["total_tokens"] == 180  # 130 + 50
    assert main["reasoning_tokens"] == 15  # subset, not added again
    assert main["coverage"] == "complete"


def test_auxiliary_tasks_reported_separately(ledger, opened, actor):
    """Auxiliary per-task usage is separate from main; lineage not double-counted."""
    aux = {"compression": {"input_tokens": 200, "output_tokens": 80,
                           "cache_read_tokens": 0, "cache_write_tokens": 0,
                           "reasoning_tokens": 0, "api_call_count": 1}}
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()},
                                auxiliary={actor.session_id: aux})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    session = report["sessions"][0]
    assert session["auxiliary"]["tasks"]["compression"]["input_tokens"] == 200
    assert session["auxiliary"]["tasks"]["compression"]["total_tokens"] == 280
    assert session["auxiliary"]["totals"]["input_tokens"] == 200
    # Main and auxiliary are separate; combined = main + aux
    assert report["totals"]["combined"]["input_tokens"] == 300  # 100 + 200
    assert report["totals"]["combined"]["total_tokens"] == 460  # 180 + 280


def test_reasoning_is_subset_not_added_again(ledger, opened, actor):
    """Reasoning tokens are a subset of output; never added to total."""
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata(reasoning_tokens=15)})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    main = report["sessions"][0]["main"]
    # total = prompt + output (reasoning already inside output)
    assert main["total_tokens"] == 180
    assert main["total_tokens"] == main["prompt_tokens"] + main["output_tokens"]


# ── Cycle 2: shared session across multiple runs ────────────────────────────


def test_shared_session_not_double_counted_and_disclosed(ledger, synthetic_snapshot, actor):
    """Two runs sharing one session: session counted once, disclosed as shared."""
    # Open two different PRs with the same actor → two runs, same session
    snap7 = {**synthetic_snapshot, "number": 7, "url": "https://github.com/synthetic/example/pull/7"}
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    opened7 = ledger.open(snap7, actor, "shared-open-7")["run"]
    opened8 = ledger.open(snap8, actor, "shared-open-8")["run"]

    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    # Report on run7: session is shared with run8
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened7["id"])
    session = report["sessions"][0]
    assert session["shared_session"] is True
    assert set(session["correlated_runs"]) == {opened7["id"], opened8["id"]}
    # Session counted once in totals
    assert report["totals"]["session_count"] == 1
    assert report["totals"]["main"]["total_tokens"] == 180
    # Disclosed in shared_sessions
    assert len(report["shared_sessions"]) == 1
    assert report["shared_sessions"][0]["session_id"] == actor.session_id


def test_shared_session_reported_once_for_whole_repo(ledger, synthetic_snapshot, actor):
    """Repository-wide report: shared session appears once, not per-run."""
    snap7 = {**synthetic_snapshot, "number": 7, "url": "https://github.com/synthetic/example/pull/7"}
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    ledger.open(snap7, actor, "shared-open-7")
    ledger.open(snap8, actor, "shared-open-8")

    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example")
    assert report["totals"]["session_count"] == 1
    assert report["totals"]["main"]["total_tokens"] == 180
    assert len(report["shared_sessions"]) == 1


# ── Cycle 3: completed-run history preservation ─────────────────────────────


def test_completed_run_still_correlates_via_audit(ledger, opened, actor):
    """After complete (owner_session=NULL), audit events preserve the session."""
    ledger.run_action("synthetic/example", opened["id"], actor, opened["generation"],
                      "complete", "complete-1")
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    assert len(report["sessions"]) == 1
    assert report["sessions"][0]["session_id"] == actor.session_id
    assert report["sessions"][0]["main"]["total_tokens"] == 180


def test_acquire_then_release_preserves_session(ledger, synthetic_snapshot, actor):
    """A run acquired and released by a session is still correlated after release."""
    actor2 = Actor("synthetic-trusted-session-b")
    opened = ledger.open(synthetic_snapshot, actor2, "acquire-open-1")["run"]
    # open() already sets owner_session=actor2; acquire on same actor is a no-op
    ledger.run_action("synthetic/example", opened["id"], actor2, opened["generation"],
                      "acquire", "acquire-1")
    ledger.run_action("synthetic/example", opened["id"], actor2, opened["generation"],
                      "release", "release-1")
    reader = FakeMetadataReader(sessions={actor2.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    assert report["sessions"][0]["session_id"] == actor2.session_id


# ── Cycle 4: missing / partial / zero ────────────────────────────────────────


def test_missing_session_disclosed_not_zero(ledger, opened, actor):
    """A session with no host metadata is disclosed as missing, not zero."""
    reader = FakeMetadataReader(sessions={})  # session not in reader
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    assert report["totals"]["missing_session_count"] == 1
    assert report["missing_session_ids"] == [actor.session_id]
    assert report["coverage"] == "partial"
    session = report["sessions"][0]
    assert session["main_coverage"] == "missing"
    assert session["main"]["total_tokens"] is None


def test_known_zero_distinguished_from_missing(ledger, opened, actor):
    """All-zero counters are known-zero (complete), not missing."""
    meta = _main_metadata(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                          cache_write_tokens=0, reasoning_tokens=0, api_call_count=0)
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    session = report["sessions"][0]
    assert session["main"]["total_tokens"] == 0
    assert session["main"]["coverage"] == "complete"
    assert report["totals"]["missing_session_count"] == 0


# ── Cycle 5: invalid counters ────────────────────────────────────────────────


def test_negative_counter_rejected(ledger, opened, actor):
    """Negative token counters are rejected."""
    meta = _main_metadata(input_tokens=-1)
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    with pytest.raises(ValueError, match="nonnegative"):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])


def test_bool_counter_rejected(ledger, opened, actor):
    """Boolean token counters are rejected."""
    meta = _main_metadata(output_tokens=True)
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    with pytest.raises(ValueError, match="boolean"):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])


def test_float_counter_rejected(ledger, opened, actor):
    """Non-integer token counters are rejected."""
    meta = _main_metadata(cache_read_tokens=1.5)
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    with pytest.raises(ValueError, match="integer"):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])


# ── Cycle 6: foreign scope rejection ────────────────────────────────────────


def test_foreign_run_id_rejected(ledger, synthetic_snapshot, actor):
    """A run ID from another repository is rejected."""
    # Open a PR in synthetic/other, then try to report on synthetic/example with that run ID
    snap_other = {**synthetic_snapshot, "repository_full_name": "synthetic/other",
                  "url": "https://github.com/synthetic/other/pull/1"}
    opened_other = ledger.open(snap_other, actor, "other-open-1")["run"]
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    with pytest.raises(LedgerError):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened_other["id"])


def test_foreign_session_id_rejected(ledger, opened, actor):
    """A session not correlated to any run in this repo is rejected."""
    reader = FakeMetadataReader(sessions={"foreign-session": _main_metadata()})
    with pytest.raises(LedgerError, match="not correlated"):
        TokenReport(ledger, reader).report("synthetic/example", session_id="foreign-session")


def test_session_not_correlated_to_run_rejected(ledger, synthetic_snapshot, opened, actor):
    """A session correlated to a different run is rejected when run_id is given."""
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    ledger.open(snap8, actor, "other-open-8")
    reader = FakeMetadataReader(sessions={"other-session": _main_metadata()})
    with pytest.raises(LedgerError, match="not correlated to this run"):
        TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"],
                                          session_id="other-session")


# ── Cycle 7: reader failure / close resource cleanup ─────────────────────────


def test_reader_failure_propagates(ledger, opened, actor):
    """A reader exception is not silently swallowed."""

    class FailingReader:
        def get_session(self, session_id):
            raise RuntimeError("host unavailable")

        def auxiliary_usage_by_task(self, session_id):
            return {}

        def close(self):
            pass

    with pytest.raises(RuntimeError, match="host unavailable"):
        TokenReport(ledger, FailingReader()).report("synthetic/example", run_id=opened["id"])


def test_reader_close_called_on_context(ledger, opened, actor):
    """The reader close seam is available for resource cleanup."""
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader)
    report.report("synthetic/example", run_id=opened["id"])
    # TokenReport doesn't own the reader lifecycle; caller closes explicitly
    assert reader.closed is False
    reader.close()
    assert reader.closed is True


# ── Cycle 8: human formatter safety ─────────────────────────────────────────


def test_human_formatter_no_urls_or_prompts(ledger, opened, actor):
    """Human formatter emits no credential-bearing URLs, raw errors, or prompts."""
    meta = _main_metadata()
    meta["billing_base_url"] = "https://secret-provider.example/v1"
    meta["system_prompt"] = "SECRET PROMPT TEXT"
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    text = format_report_human(report)
    assert "secret-provider.example" not in text
    assert "SECRET PROMPT" not in text
    assert "https://" not in text
    # Token counts ARE present
    assert "total=180" in text


def test_human_formatter_shows_attribution_and_shared(ledger, synthetic_snapshot, actor):
    """Human formatter shows whole-session attribution and shared-session warning."""
    snap7 = {**synthetic_snapshot, "number": 7, "url": "https://github.com/synthetic/example/pull/7"}
    snap8 = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    opened7 = ledger.open(snap7, actor, "shared-open-7")["run"]
    ledger.open(snap8, actor, "shared-open-8")
    reader = FakeMetadataReader(sessions={actor.session_id: _main_metadata()})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened7["id"])
    text = format_report_human(report)
    assert "whole_session_non_marginal" in text
    assert "SHARED" in text


def test_human_formatter_cost_status_distinct(ledger, opened, actor):
    """Cost actual/estimate/included/unknown are shown distinctly; unknown not $0."""
    meta = _main_metadata(actual_cost_usd=None, estimated_cost_usd=None)
    reader = FakeMetadataReader(sessions={actor.session_id: meta})
    report = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
    text = format_report_human(report)
    assert "unknown" in text
    assert "$0" not in text


# ── Cycle 9: production adapter allowlist + lazy init ───────────────────────


def test_sessiondb_adapter_allowlists_metadata():
    """SessionDBMetadataReader strips prompt/config/text fields."""
    from review_ledger.hermes_usage import SessionDBMetadataReader, _SESSION_METADATA_FIELDS

    mock_db = MagicMock()
    raw_row = {
        "id": "sess-x", "source": "cli", "model": "m",
        "input_tokens": 10, "output_tokens": 5,
        "cache_read_tokens": 2, "cache_write_tokens": 1,
        "reasoning_tokens": 3, "api_call_count": 1,
        "estimated_cost_usd": 0.001, "actual_cost_usd": None,
        "cost_status": "estimated", "cost_source": "provider",
        "system_prompt": "SECRET", "tool_names": "SECRET",
        "_system_prompt_resolved": "SECRET", "_tool_names_resolved": "SECRET",
        "model_config": '{"secret": true}', "preview": "SECRET",
    }
    mock_db.get_session.return_value = raw_row
    mock_db.auxiliary_usage_by_task.return_value = {}

    reader = SessionDBMetadataReader.__new__(SessionDBMetadataReader)
    reader._db_path = None
    reader._db = mock_db

    result = reader.get_session("sess-x")
    assert result is not None
    assert "SECRET" not in str(result)
    assert "system_prompt" not in result
    assert "model_config" not in result
    assert "preview" not in result
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5


def test_sessiondb_adapter_lazy_init():
    """SessionDB is not opened until a method is called."""
    from review_ledger.hermes_usage import SessionDBMetadataReader

    reader = SessionDBMetadataReader()
    assert reader._db is None


def test_sessiondb_adapter_close():
    """close() closes the underlying SessionDB."""
    from review_ledger.hermes_usage import SessionDBMetadataReader

    reader = SessionDBMetadataReader()
    mock_db = MagicMock()
    reader._db = mock_db
    reader.close()
    mock_db.close.assert_called_once()
    assert reader._db is None


def test_sessiondb_adapter_auxiliary_passthrough():
    """auxiliary_usage_by_task delegates to SessionDB."""
    from review_ledger.hermes_usage import SessionDBMetadataReader

    mock_db = MagicMock()
    mock_db.auxiliary_usage_by_task.return_value = {"compression": {"input_tokens": 100}}

    reader = SessionDBMetadataReader.__new__(SessionDBMetadataReader)
    reader._db_path = None
    reader._db = mock_db

    result = reader.auxiliary_usage_by_task("sess-x")
    assert result == {"compression": {"input_tokens": 100}}
    mock_db.auxiliary_usage_by_task.assert_called_once_with("sess-x")
