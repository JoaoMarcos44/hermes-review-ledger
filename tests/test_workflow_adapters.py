"""Workflow discovery is reachable through every existing read boundary."""
import json

import pytest

from review_ledger.context import Context
from review_ledger.reports import export
from review_ledger import tools
from test_workflow_integration import guidance, diagnosis  # noqa: F401


@pytest.mark.parametrize("case", ["context", "export", "status_schema", "queue_offset"])
def test_workflow_read_boundaries(ledger, opened, actor, guidance, monkeypatch, case):
    diagnosis(guidance, opened, actor)
    if case == "context":
        result = Context(ledger, 12000).prepare("synthetic/example", opened["id"], actor, query="scoped retry", phase="assess")
        assert result["workflow"]["pending_improvements_count"] == 1
    elif case == "export":
        result = export(ledger, "synthetic/example", opened["id"], actor, format="json")
        content = json.loads(result["content"])
        assert content["workflow"]["pending_improvements_count"] == 1
    elif case == "status_schema":
        props = tools.SCHEMAS["ledger_status"]["parameters"]["properties"]
        assert "workflow" in props["detail_collection"]["enum"]
    else:
        monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
        result = json.loads(tools.handle(None, "ledger_status", {
            "repository": "synthetic/example", "run_id": opened["id"], "workflow_offset": 1,
        }, session_id=actor.session_id))
        assert result["state"] == "ok"
        assert result["workflow"]["queue_offset"] == 1


def test_pending_feedback_hints_reach_handler(ledger, opened, actor, guidance, monkeypatch):
    learning, scope, version = guidance
    use = learning.use(scope, opened["id"], actor, 1, version, "uncertain", "Considered but unresolved", "adapter-use")
    monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
    result = json.loads(tools.handle(None, "ledger_run", {
        "repository": "synthetic/example", "run_id": opened["id"], "generation": 1,
        "action": "complete", "request_key": "adapter-complete",
    }, session_id=actor.session_id))
    assert result["error"]["code"] == "pending_lesson_feedback"
    assert use["use_id"] in result["error"]["hints"]["pending_use_ids"]
