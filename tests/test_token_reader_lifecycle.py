"""Usage readers acquire only the host's read-only lifecycle."""
import sys
from types import SimpleNamespace

from review_ledger.hermes_usage import SessionDBMetadataReader


def test_reader_acquires_read_only_host_handle(monkeypatch, tmp_path):
    seen = []
    class FakeSessionDB:
        def __init__(self, db_path=None, read_only=False):
            seen.append(read_only)
        def get_session(self, sid):
            return {"id": sid, "input_tokens": 4}
        def close(self):
            seen.append("closed")
    monkeypatch.setitem(sys.modules, "hermes_state", SimpleNamespace(SessionDB=FakeSessionDB))
    with SessionDBMetadataReader(tmp_path / "state.db") as reader:
        assert reader.get_session("synthetic-readonly-session")["input_tokens"] == 4
    assert seen == [True, "closed"]
