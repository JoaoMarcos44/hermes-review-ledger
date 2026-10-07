"""Synthetic full-response budgets, ownership and exact guidance tests."""
import json
import pytest
from review_ledger.context import Context
from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError, canonical, digest
from review_ledger.protocol import protocol
from review_ledger import tools

REPO = "synthetic/example"


def active(ledger, opened, actor, lesson_data):
    scope = ledger.scope(REPO)
    learning = Learning(ledger.store)
    ident = learning.propose(scope, opened["id"], actor, 1, lesson_data, "propose")["version_id"]
    learning.operator(scope, ident, "approve", "Synthetic approval", "approve")
    return ident


@pytest.mark.parametrize("cap", [2000, 3000, 4000, 6000, 12000, 64000])
def test_whole_response_budget_and_cold_start(ledger, opened, actor, cap):
    result = Context(ledger, cap).prepare(REPO, opened["id"], actor, query='"Unicode é🔬\\' * 20, max_chars=cap)
    assert len(canonical(result)) <= cap
    assert result["state"] in {"ok", "requires_more_context"}
    if result["state"] == "ok":
        assert result["protocol"] == protocol()
        assert result["residency"].startswith("unknown")
        assert result["checkpoint"] is None
        assert result["content_digest"] == digest({k:v for k,v in result.items() if k != "content_digest"})


def test_conditions_exclusions_exact_and_read_only(ledger, opened, actor, lesson_data):
    ident = active(ledger, opened, actor, lesson_data)
    second = Actor("observer")
    before = ledger.status(REPO, opened["id"], actor)
    result = Context(ledger).prepare(REPO, opened["id"], second, query="retry")
    lesson = next(x for x in result["records"] if x["kind"] == "lesson")
    assert lesson["content"]["conditions"] == sorted(lesson_data["conditions"])
    assert lesson["content"]["exclusions"] == lesson_data["exclusions"]
    assert lesson["sources"][0]["id"] == ident
    assert ledger.status(REPO, opened["id"], actor) == before
    assert "owner_session" not in canonical(result)


def test_oversized_units_are_not_loaded_and_detail_is_coherent(ledger, opened, actor, lesson_data):
    lesson_data["conditions"] = [str(i)+"necessary"*50 for i in range(8)]
    ident = active(ledger, opened, actor, lesson_data)
    context = Context(ledger, 6000)
    result = context.prepare(REPO, opened["id"], actor, query="retry")
    assert not any(x["kind"] == "lesson" for x in result["records"])
    assert any(x["id"] == ident and x["state"] == "not_loaded" for x in result["references"])
    detail = context.detail(REPO, opened["id"], actor, kind="lesson", record_id=ident, section="exclusions")
    assert detail["complete"] is False
    assert detail["content"]["exclusions"] == lesson_data["exclusions"]
    assert len(canonical(detail)) <= 6000


def test_resume_pins_versions_and_revocation_wins(ledger, opened, actor, lesson_data):
    ident = active(ledger, opened, actor, lesson_data)
    context = Context(ledger)
    first = context.prepare(REPO, opened["id"], actor, query="retry")
    resumed = context.resume(REPO, opened["id"], Actor("new-session"), manifest_id=first["manifest_id"])
    assert resumed["query"] == first["query"]
    assert resumed["manifest_id"] != first["manifest_id"]
    ledger.operator_invalidate(REPO, lesson_data["sources"][0]["observation_id"], "Synthetic correction", "invalidate")
    with pytest.raises(LedgerError, match="eligible"):
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"])
    with pytest.raises(LedgerError):
        context.detail(REPO, opened["id"], actor, kind="lesson", record_id=ident)


def test_snapshot_scope_and_unknown_question(ledger, opened, actor, synthetic_snapshot):
    context = Context(ledger)
    first = context.prepare(REPO, opened["id"], actor, query="")
    assert context.resume(REPO, opened["id"], actor)["query"] == ""
    newer = ledger.open({**synthetic_snapshot,"head_sha":"e"*40}, actor,"new")["run"]
    with pytest.raises(LedgerError):
        context.resume(REPO, newer["id"], actor, manifest_id=first["manifest_id"])
    with pytest.raises(LedgerError):
        context.prepare("denied/repo", opened["id"], actor, query="")


def test_protocol_cannot_be_model_overridden(ledger, opened, actor, monkeypatch):
    ledger.pilot = {"context_enabled": True}
    ledger.bundle_budget = 12000
    monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
    args={"repository":REPO,"run_id":opened["id"],"action":"prepare","query":"retry","protocol":"override"}
    result=json.loads(tools.handle(None,"ledger_context",args,session_id=actor.session_id))
    assert result["error"]["code"] == "invalid_input"
    args.pop("protocol")
    result=json.loads(tools.handle(None,"ledger_context",args,session_id=actor.session_id))
    assert result["protocol"] == protocol()


def test_disabled_compatibility_and_telemetry_failure(ledger, opened, actor, monkeypatch):
    monkeypatch.setattr(tools,"ledger_for_context",lambda ctx:ledger)
    args={"repository":REPO,"run_id":opened["id"],"action":"prepare","query":""}
    assert json.loads(tools.handle(None,"ledger_context",args,session_id=actor.session_id))["error"]["code"] == "feature_disabled"
    ledger.pilot={"usage_enabled":True}
    from review_ledger.usage import Usage
    monkeypatch.setattr(Usage,"record",lambda *a,**kw:(_ for _ in ()).throw(OSError("telemetry failure")))
    args={"repository":REPO,"run_id":opened["id"],"action":"finding","data":{"claim":"Synthetic claim"},"generation":1,"request_key":"write"}
    one=json.loads(tools.handle(None,"ledger_record",args,session_id=actor.session_id))
    two=json.loads(tools.handle(None,"ledger_record",args,session_id=actor.session_id))
    assert one==two and one["state"] != "error"
    assert len(ledger.status(REPO,opened["id"],actor)["findings"])==1


def test_identical_lessons_dedup_preserves_distinct_versions(ledger, opened, actor, lesson_data):
    first = active(ledger, opened, actor, lesson_data)
    scope = ledger.scope(REPO)
    learning=Learning(ledger.store)
    second=learning.propose(scope,opened["id"],actor,1,lesson_data,"second")["version_id"]
    learning.operator(scope,second,"approve","Synthetic approval","approve-second")
    result=Context(ledger).prepare(REPO,opened["id"],actor,query="retry")
    lessons=[r for r in result["records"] if r["kind"]=="lesson"]
    assert len(lessons)==1
    assert {s["id"] for s in lessons[0]["sources"]}=={first,second}
