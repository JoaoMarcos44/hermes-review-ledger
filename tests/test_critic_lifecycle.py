"""Synthetic local criticism lifecycle tests; no host inference or network."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from review_ledger.critic import Critic, CriticConfig, CriticResult
from review_ledger.models import LedgerError

REPO = "synthetic/example"


def config(**overrides):
    return CriticConfig(**dict(enabled=True, provider="synthetic", model="fixture",
                              authorized_repositories=(REPO,), **overrides))


def response(packet, position="challenge"):
    return json.dumps({"items": [{
        "finding_id": f["id"], "position": position,
        "rationale": "A declared delivery deadline is needed to distinguish delayed from lost delivery.",
        "contract_status": "unknown", "contract_statement": "Is there a bounded delivery guarantee?",
        "reference_ids": [f["id"]],
        "objections": [{"category": "contract", "claim": "A finite delay alone does not establish loss.",
            "counter_hypothesis": "Delivery may be eventually consistent.",
            "recommended_check": "Compare the recorded deadline with the declared contract.",
            "would_withdraw_if": "The declared deadline elapsed without delivery.",
            "reference_ids": [f["id"]]}] if position == "challenge" else [],
        "missing_information": ["Declared delivery deadline"] if position == "insufficient_information" else [],
        "limitations": ["Synthetic recorded context only"]} for f in packet["findings"]]})


class Fake:
    def __init__(self, callback=None, position="challenge"):
        self.calls = 0
        self.callback = callback
        self.position = position

    def evaluate(self, packet):
        self.calls += 1
        if self.callback:
            self.callback(packet)
        return CriticResult(response(packet, self.position), "synthetic", "fixture")


@pytest.fixture
def finding(ledger, opened, actor):
    return ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Synthetic delayed delivery is a defect"}, "finding")["finding_id"]


def prepare(critic, opened, actor, finding, key="prepare", **kwargs):
    return critic.prepare(REPO, opened["id"], actor, 1, key, [finding], **kwargs)["critic_run_id"]


def run(critic, opened, actor, ident, key="run"):
    return critic.run(REPO, opened["id"], actor, 1, key, ident)


def detail(critic, opened, ident):
    chunks, offset = [], 0
    while True:
        page = critic.status(REPO, opened["id"], ident, offset=offset, max_chars=1000)
        chunks.append(page["content"])
        offset = page["next_offset"]
        if offset is None:
            break
    content = "".join(chunks)
    assert hashlib.sha256(content.encode()).hexdigest() == page["content_sha256"]
    return json.loads(content)


@pytest.mark.parametrize("position", ["support", "challenge", "insufficient_information"])
def test_prepare_run_status_opinion_never_changes_finding(ledger, opened, actor, finding, position):
    fake = Fake(position=position)
    critic = Critic(ledger, config(), fake)
    before = ledger.status(REPO, opened["id"], actor)
    ident = prepare(critic, opened, actor, finding)
    assert fake.calls == 0
    assert run(critic, opened, actor, ident)["state"] == "returned"
    result = detail(critic, opened, ident)
    assert result["items"][0]["response"]["position"] == position
    assert result["items"][0]["provenance"] == "critic_generated"
    assert result["metadata"]["usage"] is None
    assert result["metadata"]["independence"] == "unknown"
    after = ledger.status(REPO, opened["id"], actor)
    assert after["assessments"] == before["assessments"]
    assert fake.calls == 1


def test_idempotent_dispatch_dedup_and_conflicts(ledger, opened, actor, finding):
    fake = Fake()
    critic = Critic(ledger, config(), fake)
    ident = prepare(critic, opened, actor, finding)
    assert prepare(critic, opened, actor, finding, "other-prepare") == ident
    run(critic, opened, actor, ident)
    run(critic, opened, actor, ident)
    run(critic, opened, actor, ident, "other-run")
    assert fake.calls == 1
    with pytest.raises(LedgerError) as exc:
        run(critic, opened, actor, "critic_different", "run")
    assert exc.value.code == "idempotency_conflict"


def test_two_instances_one_dispatch_and_no_transaction_during_provider(ledger, opened, actor, finding):
    entered, release = Event(), Event()
    def callback(packet):
        # A second independent connection must acquire the writer lock now.
        with ledger.store.connect() as conn, ledger.store.transaction(conn):
            assert conn.in_transaction
            conn.execute("SELECT count(*) FROM critic_runs").fetchone()
        entered.set()
        assert release.wait(10)
    fake = Fake(callback)
    first, second = Critic(ledger, config(), fake), Critic(ledger, config(), fake)
    ident = prepare(first, opened, actor, finding)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(run, first, opened, actor, ident)
        try:
            assert entered.wait(10)
            assert run(second, opened, actor, ident, "concurrent-run")["state"] == "running"
            assert fake.calls == 1
        finally:
            release.set()
        assert pending.result(timeout=10)["state"] == "returned"


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, KeyboardInterrupt])
def test_uncertain_dispatch_never_automatically_repeats(ledger, opened, actor, finding, failure):
    def fail(packet):
        raise failure("Synthetic interruption")
    fake = Fake(fail)
    critic = Critic(ledger, config(), fake)
    ident = prepare(critic, opened, actor, finding)
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            run(critic, opened, actor, ident)
    else:
        assert run(critic, opened, actor, ident)["state"] == "unknown"
    assert detail(critic, opened, ident)["dispatch_uncertainty"]
    run(critic, opened, actor, ident)
    assert run(critic, opened, actor, ident, "retry-new-key")["state"] == "unknown"
    assert fake.calls == 1


@pytest.mark.parametrize("fence", ["ownership", "head", "source"])
def test_late_response_keeps_receipt_without_reusable_items(ledger, opened, actor, finding, observed, synthetic_snapshot, fence):
    def change(packet):
        if fence == "ownership":
            ledger.operator_transfer(REPO, opened["id"], "synthetic-new-owner", 1, "Synthetic recovery", "transfer")
        elif fence == "head":
            ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-head")
        else:
            ledger.operator_invalidate(REPO, observed, "Synthetic source correction", "invalidate")
    critic = Critic(ledger, config(), Fake(change))
    ident = prepare(critic, opened, actor, finding, observation_ids=[observed])
    assert run(critic, opened, actor, ident)["result_discarded"]
    result = detail(critic, opened, ident)
    assert result["freshness"] != "current"
    assert result["items"] == []


@pytest.mark.parametrize("cfg,code", [
    (CriticConfig(), "critic_disabled"),
    (CriticConfig(enabled=True), "critic_consent_required"),
    (CriticConfig(enabled=True, authorized_repositories=(REPO,)), "critic_route_required"),
])
def test_disabled_or_missing_consent_zero_calls(ledger, opened, actor, finding, cfg, code):
    fake = Fake()
    critic = Critic(ledger, cfg, fake)
    ident = prepare(critic, opened, actor, finding)
    with pytest.raises(LedgerError) as exc:
        run(critic, opened, actor, ident)
    assert exc.value.code == code
    assert fake.calls == 0
    assert ledger.status(REPO, opened["id"], actor)["run"]["id"] == opened["id"]


def test_persisted_quota_shared_between_instances(ledger, opened, actor, finding):
    fake = Fake()
    first = Critic(ledger, config(max_calls_per_run=1), fake)
    ident = prepare(first, opened, actor, finding)
    run(first, opened, actor, ident)
    other = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": "Different synthetic claim"}, "finding2")["finding_id"]
    second = Critic(ledger, config(max_calls_per_run=1), fake)
    next_ident = prepare(second, opened, actor, other, "prepare2")
    with pytest.raises(LedgerError) as exc:
        run(second, opened, actor, next_ident, "run2")
    assert exc.value.code == "critic_quota_exceeded"
    assert fake.calls == 1


def test_prepare_rejects_foreign_context_and_oversized_or_sensitive_packet(ledger, opened, actor, finding, synthetic_snapshot):
    other = ledger.open({**synthetic_snapshot, "number": 8}, actor, "other-review")["run"]
    foreign = ledger.record(REPO, other["id"], actor, 1, "finding", {"claim": "Foreign review secret"}, "foreign")["finding_id"]
    critic = Critic(ledger, config(), Fake())
    with pytest.raises(LedgerError) as exc:
        prepare(critic, opened, actor, foreign)
    assert exc.value.code == "scope_not_found"
    for claim, cfg, expected in [("x" * 3000, config(max_input_chars=2000), "critic_packet_budget"),
                                  ("password=synthetic-do-not-send", config(), "critic_sensitive_context")]:
        fid = ledger.record(REPO, opened["id"], actor, 1, "finding", {"claim": claim}, expected)["finding_id"]
        with pytest.raises(LedgerError) as exc:
            prepare(Critic(ledger, cfg, Fake()), opened, actor, fid, expected)
        assert exc.value.code == expected


def test_assess_supported_refuted_inconclusive_and_source_revocation(ledger, opened, actor, finding, observed, observation_data):
    critic = Critic(ledger, config(), Fake())
    ident = prepare(critic, opened, actor, finding)
    run(critic, opened, actor, ident)
    objection = detail(critic, opened, ident)["items"][0]["objections"][0]["id"]
    def assess(state, sources, key):
        return critic.assess(REPO, opened["id"], actor, 1, key, ident, objection, state,
                             "behavior", "Synthetic discriminating check", "Agent-reported only", sources)
    for state in ("supported", "refuted"):
        assert assess(state, [observed], state)["provenance"] == "agent_reported"
    blocked = ledger.record(REPO, opened["id"], actor, 1, "observation",
                            {**observation_data, "outcome": "infrastructure_failure"}, "blocked")["observation_id"]
    with pytest.raises(LedgerError) as exc:
        assess("supported", [blocked], "bad-support")
    assert exc.value.code == "ineligible_evidence"
    assess("inconclusive", [blocked], "inconclusive")
    ledger.operator_invalidate(REPO, observed, "Synthetic revoked result", "invalidate")
    history = detail(critic, opened, ident)["items"][0]["objections"][0]["assessments"]
    assert len(history) == 3
    assert history[0]["freshness"] != "current"
    assert ledger.status(REPO, opened["id"], actor)["assessments"] == []


def test_selected_assessment_requires_all_sources(ledger, opened, actor, finding, observed):
    aid = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "supported", "basis": "behavior", "rationale": "Synthetic observation",
        "limitations": "Agent report", "observation_ids": [observed]}, "assessment")["assessment_id"]
    critic = Critic(ledger, config(), Fake())
    with pytest.raises(LedgerError) as exc:
        prepare(critic, opened, actor, finding, assessment_ids=[aid])
    assert exc.value.code == "critic_context_incomplete"
    ident = prepare(critic, opened, actor, finding, "complete", assessment_ids=[aid], observation_ids=[observed])
    assert detail(critic, opened, ident)["packet"]["included"]["assessment_ids"] == [aid]


def test_current_verification_source_revocation_marks_assessment(ledger, opened, actor, finding, observed):
    critic = Critic(ledger, config(), Fake())
    ident = prepare(critic, opened, actor, finding)
    run(critic, opened, actor, ident)
    objection = detail(critic, opened, ident)["items"][0]["objections"][0]["id"]
    critic.assess(REPO, opened["id"], actor, 1, "assess", ident, objection, "supported", "behavior",
                  "Synthetic check", "Agent report only", [observed])
    ledger.operator_invalidate(REPO, observed, "Synthetic result revoked", "invalidate")
    assessment = detail(critic, opened, ident)["items"][0]["objections"][0]["assessments"][0]
    assert assessment["freshness"] == "needs_revalidation"
    assert assessment["observation_ids"] == [observed]


def test_known_route_mismatch_preserved_and_unknown_identity_not_invented(ledger, opened, actor, finding):
    class Different(Fake):
        def evaluate(self, packet):
            return CriticResult(response(packet), "different-synthetic-provider", "different-fixture",
                                {"input_tokens": 10, "cost_usd": None, "private_host_field": "must-not-persist"})
    critic = Critic(ledger, config(), Different())
    ident = prepare(critic, opened, actor, finding)
    run(critic, opened, actor, ident)
    metadata = detail(critic, opened, ident)["metadata"]
    assert metadata["returned_model"] == "different-fixture"
    assert metadata["route_matches_requested"] is False
    assert metadata["reviewer_identity"] is None
    assert metadata["usage"]["cost_usd"] is None
    assert "private_host_field" not in metadata["usage"]


def test_prepared_config_change_and_revoked_source_block_before_dispatch(ledger, opened, actor, finding, observed):
    fake = Fake()
    critic = Critic(ledger, config(), fake)
    ident = prepare(critic, opened, actor, finding, observation_ids=[observed])
    changed = Critic(ledger, config(max_calls_per_run=2), fake)
    with pytest.raises(LedgerError) as exc:
        run(changed, opened, actor, ident, "changed-config")
    assert exc.value.code == "critic_config_changed"
    ledger.operator_invalidate(REPO, observed, "Synthetic source revoked", "invalidate")
    with pytest.raises(LedgerError) as exc:
        run(critic, opened, actor, ident)
    assert exc.value.code == "critic_stale"
    assert fake.calls == 0


def test_explicit_abandonment_new_attempt_keeps_uncertainty_history(ledger, opened, actor, finding):
    def fail(packet):
        raise TimeoutError("Synthetic timeout")
    fake = Fake(fail)
    critic = Critic(ledger, config(), fake)
    ident = prepare(critic, opened, actor, finding)
    assert run(critic, opened, actor, ident)["state"] == "unknown"
    assert critic.abandon(REPO, opened["id"], ident, "Explicit synthetic abandonment", "abandon")["state"] == "abandoned"
    new_ident = prepare(critic, opened, actor, finding, "new-explicit-prepare")
    assert new_ident != ident
    assert detail(critic, opened, ident)["execution"] == "abandoned"
    assert run(critic, opened, actor, new_ident, "new-explicit-run")["state"] == "unknown"
    assert fake.calls == 2


def _process_dispatch(root, profile, run_id, session, critic_id, start, output):
    """Spawn-safe process entry point with its own Ledger/Store/provider instance."""
    from pathlib import Path
    from review_ledger.models import Actor
    from review_ledger.service import Ledger
    from review_ledger.storage import Store
    class ProcessFake(Fake):
        def evaluate(self, packet):
            output.put(("dispatch", critic_id))
            return super().evaluate(packet)
    try:
        ledger = Ledger(Store(Path(root), profile), [REPO], skill_version="0.1.0", skill_hash="d" * 64)
        if not start.wait(10):
            raise RuntimeError("Synthetic barrier timeout")
        result = Critic(ledger, config(), ProcessFake()).run(REPO, run_id, Actor(session), 1, "same-process-key", critic_id)
        output.put(("result", result["state"]))
    except BaseException as exc:
        output.put(("error", type(exc).__name__, str(exc)))


def test_two_native_processes_reserve_only_one_logical_call(ledger, opened, actor, finding):
    import multiprocessing
    critic = Critic(ledger, config(), Fake())
    ident = prepare(critic, opened, actor, finding)
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    processes = [context.Process(target=_process_dispatch, args=(str(ledger.store.data_dir),
        ledger.store.profile_key, opened["id"], actor.session_id, ident, start, output)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        start.set()
        messages = [output.get(timeout=20) for _ in range(3)]
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert sum(m[0] == "dispatch" for m in messages) == 1
        assert sum(m[0] == "result" for m in messages) == 2
        assert not any(m[0] == "error" for m in messages)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(10)
            if process.pid is not None and not process.is_alive():
                process.close()
        output.close()
        output.join_thread()
    assert detail(critic, opened, ident)["execution"] == "returned"
    assert len(detail(critic, opened, ident)["items"]) == 1
