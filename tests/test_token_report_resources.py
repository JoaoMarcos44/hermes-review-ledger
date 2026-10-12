"""Resource and unavailable-data contracts for truthful reports."""
import pytest
from unittest.mock import MagicMock

from review_ledger import token_report
from review_ledger.hermes_usage import SessionDBMetadataReader
from review_ledger.models import Actor, LedgerError
from review_ledger.token_report import TokenReport
from test_token_report import FakeMetadataReader, _main_metadata


@pytest.mark.parametrize("case", ["empty", "aux_missing", "run_cap", "session_cap", "worker_scan_cap"])
def test_report_never_silently_claims_complete_scope(ledger, synthetic_snapshot, actor, monkeypatch, case):
    if case == "empty":
        # Establish repository identity, but make the selector's usage unavailable.
        opened = ledger.open(synthetic_snapshot, actor, "cap-open")["run"]
        report = TokenReport(ledger, FakeMetadataReader({})).report("synthetic/example", run_id=opened["id"])
        assert report["totals"]["combined"]["total_tokens"] is None
        return
    ledger.open(synthetic_snapshot, actor, "cap-root")
    if case == "aux_missing":
        class MissingAux(FakeMetadataReader):
            def auxiliary_usage_by_task(self, sid):
                return None
        result = TokenReport(ledger, MissingAux({actor.session_id: _main_metadata()})).report("synthetic/example")
        assert result["totals"]["auxiliary"]["total_tokens"] is None
        assert result["coverage"] == "partial"
    elif case == "worker_scan_cap":
        reader = SessionDBMetadataReader()
        reader._db = MagicMock()
        reader._db.list_sessions_rich.return_value = [{"id": "synthetic-session"}] * 1001
        with pytest.raises((LedgerError, RuntimeError)):
            reader.list_sessions_metadata()
    else:
        other = Actor("synthetic-other-session")
        ledger.open({**synthetic_snapshot, "number": 8}, other, "cap-other")
        monkeypatch.setattr(token_report, "MAX_RUNS" if case == "run_cap" else "MAX_SESSIONS", 1, raising=False)
        reader = FakeMetadataReader({actor.session_id: _main_metadata(), other.session_id: _main_metadata()})
        with pytest.raises(LedgerError, match="cap|bound|limit"):
            TokenReport(ledger, reader).report("synthetic/example")
