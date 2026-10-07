"""Synthetic opinion boundaries: no execution, provider calls, or target exploitation."""
import json
import pytest
from review_ledger.critic import Critic, CriticConfig, CriticResult
from review_ledger.critic_hermes import HermesCriticProvider
from review_ledger.models import LedgerError

REPO = "synthetic/example"


def setup_case(ledger, opened, actor, provider, claim="Synthetic recorded claim"):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": claim}, "boundary-finding")["finding_id"]
    config = CriticConfig(enabled=True, authorized_repositories=(REPO,), provider="synthetic", model="fixture")
    critic = Critic(ledger, config, provider)
    ident = critic.prepare(REPO, opened["id"], actor, 1, "boundary-prepare", [finding])["critic_run_id"]
    return critic, ident, finding


def output(packet, position="support"):
    return {"items": [{"finding_id": f["id"], "position": position,
        "rationale": "Synthetic opinion; not established truth.",
        "contract_status": "unknown", "contract_statement": "No verified contract was supplied.",
        "reference_ids": [f["id"]], "objections": [], "missing_information": [],
        "limitations": ["Synthetic provider response"]} for f in packet["findings"]]}


def test_host_refuses_before_reading_llm_or_credentials(ledger, opened, actor):
    class Context:
        def __getattribute__(self, name):
            raise AssertionError("No host context property may be read before safe route proof")
    cfg = CriticConfig(enabled=True, authorized_repositories=(REPO,), provider="synthetic", model="fixture")
    adapter = HermesCriticProvider(Context(), cfg)
    critic, ident, _ = setup_case(ledger, opened, actor, adapter)
    with pytest.raises(LedgerError) as error:
        critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)
    assert error.value.code == "critic_route_unverifiable"
    with ledger.store.connect() as conn:
        row = conn.execute("SELECT execution,started_at FROM critic_runs WHERE id=?", (ident,)).fetchone()
        assert tuple(row) == ("prepared", None)


@pytest.mark.parametrize("usage", [{"cost_usd": float("nan")}, {"input_tokens": True}, {"output_tokens": -1}, {"cost_usd": float("inf")}])
def test_invalid_metadata_is_failed_not_a_verified_result(ledger, opened, actor, usage):
    class Fake:
        def evaluate(self, packet):
            return CriticResult(json.dumps(output(packet)), usage=usage)
    critic, ident, _ = setup_case(ledger, opened, actor, Fake())
    result = critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)
    assert result["state"] == "failed"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM critic_items").fetchone()[0] == 0


def test_provider_cannot_mutate_selection_used_for_validation(ledger, opened, actor):
    class Fake:
        def evaluate(self, packet):
            packet["findings"][0]["id"] = "finding_invented"
            packet["included"]["finding_ids"] = ["finding_invented"]
            return CriticResult(json.dumps(output(packet)))
    critic, ident, _ = setup_case(ledger, opened, actor, Fake())
    assert critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)["state"] == "failed"


@pytest.mark.parametrize("contract", ["Eventual delivery with no finite deadline", "Delivery must complete within ten logical ticks"])
def test_distributed_contract_fixture_preserves_distinct_recorded_limits(ledger, opened, actor, contract):
    class Fake:
        def evaluate(self, packet):
            response = output(packet)
            item = response["items"][0]
            item["position"] = "insufficient_information"
            item["missing_information"] = ["Recorded discriminating check under this contract"]
            item["contract_statement"] = packet["findings"][0]["claim"]
            return CriticResult(json.dumps(response))
    critic, ident, fid = setup_case(ledger, opened, actor, Fake(), contract)
    assert critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)["state"] == "returned"
    with ledger.store.connect() as conn:
        response = json.loads(conn.execute("SELECT response_json FROM critic_items").fetchone()[0])
    assert response["contract_statement"] == contract
    assert response["position"] == "insufficient_information"
    assert ledger.status(REPO, opened["id"], actor)["assessments"] == []


def test_failed_proposed_fix_is_separate_from_supported_objection(ledger, opened, actor, observed):
    class Fake:
        def evaluate(self, packet):
            response = output(packet)
            item = response["items"][0]
            item["position"] = "challenge"
            item["objections"] = [{"category": "proposed_fix", "claim": "The proposed ordering may lose a synthetic update.",
                "counter_hypothesis": "An independent recovery rule may preserve it.",
                "recommended_check": "Compare synthetic control outcomes under each declared contract.",
                "would_withdraw_if": "Recorded controls establish the claimed guarantee.", "reference_ids": [item["finding_id"]]}]
            return CriticResult(json.dumps(response))
    critic, ident, _ = setup_case(ledger, opened, actor, Fake())
    critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)
    with ledger.store.connect() as conn:
        objection = conn.execute("SELECT id FROM critic_objections").fetchone()[0]
    result = critic.assess(REPO, opened["id"], actor, 1, "boundary-assess", ident, objection,
                           "supported", "behavior", "Synthetic control reported the proposed correction still fails.",
                           "This supports the objection; it does not verify a replacement fix.", [observed])
    assert result["adjudication"] == "supported"
    assert ledger.status(REPO, opened["id"], actor)["assessments"] == []
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM lesson_versions WHERE state='active'").fetchone()[0] == 0


def test_large_file_inventory_does_not_crowd_selected_context(ledger, synthetic_snapshot, actor):
    many = [{"filename": f"long/synthetic/path/file_{i}.py", "sha": "c" * 40,
             "status": "modified", "patch_status": "unavailable"} for i in range(200)]
    opened = ledger.open({**synthetic_snapshot, "files": many, "total_files": 200}, actor, "large-open")["run"]
    critic, ident, _ = setup_case(ledger, opened, actor, None)
    with ledger.store.connect() as conn:
        packet = json.loads(conn.execute("SELECT packet_json FROM critic_runs WHERE id=?", (ident,)).fetchone()[0])
    assert packet["capture"]["total_files"] == 200
    assert packet["capture"]["file_metadata_not_sent"] > 0
    assert "files" not in packet["capture"]
    assert len(json.dumps(packet)) < critic.config.max_input_chars


def test_never_dispatched_prepare_can_be_rebound_after_operator_transfer(ledger, opened, actor):
    from review_ledger.models import Actor
    critic, ident, finding = setup_case(ledger, opened, actor, None)
    ledger.operator_transfer(REPO, opened["id"], "new-synthetic-owner", 1, "Explicit recovery", "recover")
    with ledger.store.connect() as conn:
        generation = conn.execute("SELECT generation FROM runs WHERE id=?", (opened["id"],)).fetchone()[0]
    next_id = critic.prepare(REPO, opened["id"], Actor("new-synthetic-owner"), generation,
                             "new-owner-prepare", [finding])["critic_run_id"]
    assert next_id != ident
    with ledger.store.connect() as conn:
        old = conn.execute("SELECT execution,started_at,error_code FROM critic_runs WHERE id=?", (ident,)).fetchone()
    assert tuple(old) == ("abandoned", None, "replaced_before_dispatch")


def test_clock_rollback_does_not_change_latest_adjudication(ledger, opened, actor, observed, monkeypatch):
    import review_ledger.critic as module
    class Fake:
        def evaluate(self, packet):
            response = output(packet)
            item = response["items"][0]
            item["position"] = "challenge"
            item["objections"] = [{"category": "contract", "claim": "Synthetic objection", "counter_hypothesis": "Synthetic alternative", "recommended_check": "Inspect the recorded synthetic contract", "would_withdraw_if": "Contrary synthetic observation", "reference_ids": []}]
            return CriticResult(json.dumps(response))
    critic, ident, _ = setup_case(ledger, opened, actor, Fake())
    critic.run(REPO, opened["id"], actor, 1, "boundary-run", ident)
    with ledger.store.connect() as conn:
        oid = conn.execute("SELECT id FROM critic_objections").fetchone()[0]
    monkeypatch.setattr(module, "now", lambda: "2030-01-01T00:00:00+00:00")
    critic.assess(REPO, opened["id"], actor, 1, "first-assess", ident, oid, "supported", "behavior", "First check", "Synthetic", [observed])
    monkeypatch.setattr(module, "now", lambda: "2020-01-01T00:00:00+00:00")
    critic.assess(REPO, opened["id"], actor, 1, "last-assess", ident, oid, "refuted", "behavior", "Later check", "Synthetic", [observed])
    with ledger.store.connect() as conn:
        detail = Critic.detail(conn, ledger.scope(REPO), conn.execute("SELECT * FROM critic_runs WHERE id=?", (ident,)).fetchone())
    objection = detail["items"][0]["objections"][0]
    assert objection["adjudication"] == "refuted"
    assert objection["assessments"][-1]["freshness"] == "current"
