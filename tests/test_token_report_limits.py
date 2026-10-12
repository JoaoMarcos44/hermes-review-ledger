"""Missing coverage and selector scope must never be advertised as complete."""
import pytest

from review_ledger.models import Actor, LedgerError
from review_ledger.token_report import TokenReport
from test_token_report import FakeMetadataReader, _main_metadata


@pytest.mark.parametrize("case", ["partial_main", "partial_aux", "api_calls_missing", "invalid_flag", "worker_failure", "repo_scope", "transfer_session"])
def test_truthful_coverage_and_selectors(ledger, synthetic_snapshot, opened, actor, case):
    if case == "partial_main":
        reader = FakeMetadataReader({actor.session_id: _main_metadata(output_tokens=None)})
        result = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
        assert result["totals"]["combined"]["total_tokens"] is None
    elif case == "partial_aux":
        auxiliary = {"compression": {"input_tokens": 5, "output_tokens": None, "reasoning_tokens": 0,
                                    "cache_read_tokens": 0, "cache_write_tokens": 0, "api_call_count": 1}}
        reader = FakeMetadataReader({actor.session_id: _main_metadata()}, {actor.session_id: auxiliary})
        result = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
        assert result["totals"]["auxiliary"]["total_tokens"] is None
        assert result["totals"]["combined"]["total_tokens"] is None
    elif case == "api_calls_missing":
        reader = FakeMetadataReader({actor.session_id: _main_metadata(api_call_count=None)})
        result = TokenReport(ledger, reader).report("synthetic/example", run_id=opened["id"])
        assert result["coverage"] == "partial"
        assert result["totals"]["main"]["api_call_count"] is None
    elif case == "invalid_flag":
        reader = FakeMetadataReader({actor.session_id: _main_metadata()})
        with pytest.raises((ValueError, LedgerError)):
            TokenReport(ledger, reader).report("synthetic/example", include_workers=1)
    elif case == "worker_failure":
        class FailedWorkerReader(FakeMetadataReader):
            def list_sessions_metadata(self):
                raise RuntimeError("reader scan unavailable")
        with pytest.raises((RuntimeError, LedgerError)):
            TokenReport(ledger, FailedWorkerReader({actor.session_id: _main_metadata()})).report("synthetic/example", include_workers=True)
    elif case == "repo_scope":
        result = TokenReport(ledger, FakeMetadataReader({actor.session_id: _main_metadata()})).report("synthetic/example")
        assert result["scope"] == "repository"
    else:
        target = Actor("synthetic-transferred-session")
        moved = ledger.operator_transfer("synthetic/example", opened["id"], target.session_id, 1, "Synthetic fixture", "limits-transfer")
        ledger.run_action("synthetic/example", opened["id"], target, moved["run"]["generation"], "complete", "limits-complete")
        result = TokenReport(ledger, FakeMetadataReader({target.session_id: _main_metadata()})).report("synthetic/example", session_id=target.session_id)
        assert result["scope"] == "session"
        assert [s["session_id"] for s in result["sessions"]] == [target.session_id]
