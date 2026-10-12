"""Host auxiliary getters expose ancestor prefixes, not equal full lineages."""
import pytest

from review_ledger.models import Actor
from review_ledger.token_report import TokenReport
from test_token_report import FakeMetadataReader, _main_metadata


@pytest.mark.parametrize("case", ["prefix", "identity_failure"])
def test_auxiliary_prefix_and_identity_failure(ledger, opened, actor, synthetic_snapshot, case):
    tip = Actor("synthetic-tip-session")
    tip_snapshot = {**synthetic_snapshot, "number": 8, "url": "https://github.com/synthetic/example/pull/8"}
    ledger.open(tip_snapshot, tip, "aux-tip-run")
    task = lambda n: {"compression": {"input_tokens": n, "output_tokens": 7, "reasoning_tokens": 0,
                                   "cache_read_tokens": 0, "cache_write_tokens": 0, "api_call_count": 1}}
    class PrefixReader(FakeMetadataReader):
        def auxiliary_lineage_ids(self, sid):
            if case == "identity_failure":
                raise RuntimeError("identity unavailable")
            return [actor.session_id] if sid == actor.session_id else [actor.session_id, tip.session_id]
    reader = PrefixReader({actor.session_id: _main_metadata(), tip.session_id: _main_metadata()},
                          {actor.session_id: task(10), tip.session_id: task(30)})
    if case == "identity_failure":
        with pytest.raises(RuntimeError, match="identity unavailable"):
            TokenReport(ledger, reader).report("synthetic/example")
    else:
        result = TokenReport(ledger, reader).report("synthetic/example")
        assert result["totals"]["auxiliary"]["input_tokens"] == 30
        assert result["totals"]["auxiliary"]["total_tokens"] == 37
        assert result["totals"]["session_count"] == 2
        assert result["coverage"] == "complete"
