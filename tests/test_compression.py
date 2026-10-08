"""Synthetic, offline contracts for deterministic context representations.

These tests establish field preservation and authorization boundaries, not
universal semantic equivalence or provider-side inference savings.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from review_ledger import compression
from review_ledger.compression import expand_records, project_bundle, project_records, select_bundle
from review_ledger.context import Context
from review_ledger.learning import Learning
from review_ledger.models import Actor, LedgerError, canonical, digest
from review_ledger.protocol import protocol
from review_ledger.service import Ledger
from review_ledger.skills import Skills
from review_ledger.storage import Store

REPO = "synthetic/example"


class LiteralCounter:
    """A test-only exact character counter, explicitly not a model tokenizer."""
    identity = {"library": "synthetic-fixture", "version": "1", "encoding": "unicode-code-points"}

    def __init__(self):
        self.seen = []

    def count(self, value):
        self.seen.append(value)
        return len(value)


class UnavailableCounter(LiteralCounter):
    def count(self, value):
        raise RuntimeError("Synthetic local counter unavailable")


def record(kind, ident, content):
    return {"kind": kind, "sources": [{"kind": kind, "id": ident, "sha256": digest(content)}],
            "content": deepcopy(content)}


def bundle(records):
    return {"state": "ok", "records": deepcopy(records), "references": [], "omitted": [],
            "selection": {"candidate_count": len(records), "selected_count": 0},
            "snapshot": {"head_sha": "a" * 40, "base_sha": "b" * 40},
            "_snapshot_key": "private-selection-key"}


def prepared(ledger, opened, actor, **kwargs):
    return Context(ledger, 64000, compression_enabled=True).prepare(
        REPO, opened["id"], actor, query="retry", mode="compact", **kwargs)


def expanded(result):
    return expand_records(result["records"], result["representation"].get("common_fields", {}))


def records_of(result, kind):
    return [r for r in expanded(result) if r["kind"] == kind]


def assert_sealed(result):
    assert result["content_digest"] == digest({k: v for k, v in result.items() if k != "content_digest"})


def approve_lesson(ledger, opened, actor, lesson_data, suffix="one"):
    learning = Learning(ledger.store)
    scope = ledger.scope(REPO)
    ident = learning.propose(scope, opened["id"], actor, 1, lesson_data, "propose-" + suffix)["version_id"]
    learning.operator(scope, ident, "approve", "Synthetic independent fixture approval", "approve-" + suffix)
    return ident


def registered_skill(ledger, tmp_path, body):
    package = tmp_path / "compression-skill"
    package.mkdir(exist_ok=True)
    (package / "SKILL.md").write_text(
        "---\nname: compression-check\ndescription: Synthetic retry guidance\n---\n" + body + "\n",
        encoding="utf-8")
    return Skills(ledger.store).register(ledger.scope(REPO), package,
        qualified_id="synthetic/compression-check", approved=True, enabled=True)


def assessment(ledger, opened, actor, observed, state="supported", key="assessment"):
    finding = ledger.record(REPO, opened["id"], actor, 1, "finding",
                            {"claim": "Retry may repeat a reported effect only after persistence."}, "finding-" + key)["finding_id"]
    assessed = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": state, "basis": "behavior", "rationale": "Only the reported boundary was tested.",
        "limitations": "No other interruption point was tested; not a universal claim.",
        "observation_ids": [observed]}, key)["assessment_id"]
    return finding, assessed


@pytest.mark.parametrize("kind", ["observation", "assessment", "finding", "lesson", "skill", "snapshot_completeness"])
def test_projection_roundtrips_all_selected_fields_and_literal_types(kind):
    content = {
        "run_id": "run-synthetic", "provenance": "agent_reported", "outcome": "skipped",
        "condition": "Only after persist and before acknowledgement; never before persist.",
        "expectation": "0 writes must remain 0; at most 2 ms, not 2 s.",
        "verification": "Reported by the agent; execution was not observed.",
        "limitations": "May fail; must not be assumed. Not every boundary was tested.",
        "question": "Could any retry repeat the effect?", "conditions": ["Only if a write completed"],
        "exclusions": ["Except read-only operations", "Unless identity is stable"],
        "state": "inconclusive", "freshness": "historical", "version": 0,
        "false_value": False, "zero_value": 0, "empty_value": [], "null_value": None,
        "head_sha": "a" * 40, "base_sha": "b" * 40,
        "code": 'def check(x):\n    return x == "\\n"  # not a newline\n',
        "patch": "@@ -1,2 +1,2 @@\n-if old:\n+if new:\n     pass\n",
        "command": 'python -c "print(0)"', "path": "src/with spaces/é.py",
        "error": "ValueError: expected 0, got null", "timestamp": "2026-01-02T03:04:05.000000Z",
        "instructions": "1. Check the entire condition.\n2. Do not omit this exclusion.\n",
        "references": [{"id": "source-one", "relation": "contradicts"}],
    }
    rows = [record(kind, "source-one", content), record(kind, "source-two", content)]
    original = deepcopy(rows)
    projected, common = project_records(rows)
    assert expand_records(projected, common) == original
    assert rows == original
    assert len(projected) == 2
    assert [r["sources"][0]["id"] for r in projected] == ["source-one", "source-two"]
    assert "missing_value" not in expand_records(projected, common)[0]["content"]


@pytest.mark.parametrize("key", ["valid", "environment", "limitations", "outcome"])
def test_factoring_never_equates_false_zero_empty_null_or_absent(key):
    contents = [{key: value} for value in [False, 0, [], None]] + [{}]
    rows = [record("observation", str(i), content) for i, content in enumerate(contents)]
    projected, common = project_records(rows)
    assert key not in common.get("observation", {})
    assert canonical(expand_records(projected, common)) == canonical(rows)


def test_shared_fields_are_response_scoped_and_exceptions_stay_literal():
    rows = [record("observation", "one", {"run_id": "same", "limitations": "Only 1 ms was tested."}),
            record("observation", "two", {"run_id": "same", "limitations": "Except after the second write."})]
    out = project_bundle(bundle(rows))
    assert expand_records(out["records"], out["representation"]["common_fields"]) == rows
    assert "limitations" not in out["representation"]["common_fields"].get("observation", {})
    assert out["representation"]["task_sufficiency"] == "unknown"
    assert "response" in out["representation"]["common_scope"]
    assert "_snapshot_key" not in out
    assert_sealed(out)


def test_whole_unit_selection_preserves_temporal_order_and_multiplicity():
    rows = [record("observation", str(i), {"run_id": "same", "summary": "Repeated event", "position": i}) for i in range(8)]
    out, _ = select_bundle(bundle(rows), mode="compact", fits=lambda _: True)
    assert expanded(out) == rows
    assert [r["content"]["position"] for r in expanded(out)] == list(range(8))


def test_reference_contains_identity_size_and_load_instruction_not_source_prose():
    content = {"conditions": ["Required start " + "x" * 5000 + " essential final exception"], "exclusions": ["Never delete"]}
    source = record("lesson", "lesson-synthetic", content)
    out, _ = select_bundle(bundle([source]), mode="reference", fits=lambda _: True)
    assert out["records"] == []
    ref = out["references"][0]
    assert ref["state"] == "not_loaded"
    assert ref["sources"] == source["sources"]
    assert ref["required_chars"] == len(canonical(content))
    assert ref["required_bytes"] == len(canonical(content).encode("utf-8"))
    assert ref["detail"] == {"action": "detail", "kind": "lesson", "record_id": "lesson-synthetic"}
    assert "Required start" not in canonical(out)
    assert "essential final exception" not in canonical(out)


def test_short_compact_growth_uses_smaller_authorized_full_view():
    source = bundle([record("observation", "one", {"summary": "Short"})])
    out, metrics = select_bundle(source, mode="compact", fits=lambda _: True)
    full = project_bundle(source, "full")
    full["selection"]["selected_count"] = 1
    compression.seal(full)
    assert len(canonical(out).encode("utf-8")) <= len(canonical(full).encode("utf-8"))
    assert expanded(out) == source["records"]
    assert metrics["fallback_count"] >= 1


def test_candidate_window_is_bounded_and_declared():
    rows = [record("observation", str(i), {"summary": str(i)}) for i in range(compression.MAX_CANDIDATES + 7)]
    calls = []
    out, _ = select_bundle(bundle(rows), mode="compact", fits=lambda value: calls.append(value) is None)
    assert len(out["records"]) <= compression.MAX_CANDIDATES
    assert len(calls) <= compression.MAX_CANDIDATES * 3 + 4
    assert out["omitted"]
    assert sum(item.get("count", 0) for item in out["omitted"]) >= 7


def test_disabled_mode_preserves_legacy_response_contract(ledger, opened, actor):
    result = Context(ledger).prepare(REPO, opened["id"], actor, query="retry")
    assert "representation" not in result and "limits" not in result and "counter" not in result
    assert result["protocol"] == protocol()
    for mode in ("compact", "reference"):
        with pytest.raises(LedgerError) as error:
            Context(ledger).prepare(REPO, opened["id"], actor, query="retry", mode=mode)
        assert error.value.code == "feature_disabled"


@pytest.mark.parametrize("chars,bytes_cap", [(2000, 2000), (4000, 4000), (6000, 6000), (12000, 8000), (24000, 10000), (64000, 256000)])
@pytest.mark.parametrize("mode", ["compact", "reference", "full"])
def test_every_actual_final_envelope_fits_chars_and_utf8(ledger, opened, actor, observation_data, chars, bytes_cap, mode):
    observation_data["details"] = ('é🔬\\"\n' * 500) + "Never omit the final condition."
    ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "unicode")
    result = Context(ledger, chars, compression_enabled=True, byte_budget=bytes_cap).prepare(
        REPO, opened["id"], actor, query='quote " slash \\ unicode é🔬' * 15,
        mode=mode, max_bytes=bytes_cap)
    actual = canonical(result)
    assert len(actual) <= chars
    assert len(actual.encode("utf-8")) <= bytes_cap
    assert_sealed(result)
    if result["state"] == "ok":
        rows = records_of(result, "observation")
        assert not rows or rows[0]["content"]["details"] == observation_data["details"]


def test_requested_caps_can_only_reduce_operator_caps(ledger, opened, actor):
    counter = LiteralCounter()
    context = Context(ledger, 6000, compression_enabled=True, byte_budget=7000, token_budget=5500, counter=counter)
    result = context.prepare(REPO, opened["id"], actor, query="", mode="compact",
                             max_chars=64000, max_bytes=256000, max_tokens=256000)
    assert result["limits"] == {"max_chars": 6000, "max_bytes": 7000, "max_tokens": 5500, "token_budget_verified": True}
    actual = canonical(result)
    assert len(actual) <= 5500 and len(actual.encode("utf-8")) <= 7000
    assert actual in counter.seen


@pytest.mark.parametrize("option,value", [("max_chars", True), ("max_chars", -1), ("max_chars", 2.5), ("max_chars", 64001),
    ("max_bytes", False), ("max_bytes", "6000"), ("max_bytes", -1), ("max_bytes", 256001),
    ("max_tokens", True), ("max_tokens", 0), ("max_tokens", -1), ("max_tokens", 256001),
    ("strict_tokens", 1), ("strict_tokens", "false"), ("mode", "unknown")])
def test_invalid_model_limits_are_rejected(ledger, opened, actor, option, value):
    kwargs = {"mode": "compact", option: value}
    with pytest.raises(LedgerError) as error:
        Context(ledger, compression_enabled=True).prepare(REPO, opened["id"], actor, query="", **kwargs)
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("option,value", [("budget", True), ("byte_budget", False), ("byte_budget", -1),
    ("token_budget", True), ("token_budget", 0), ("compression_enabled", 1)])
def test_invalid_operator_limits_are_rejected(ledger, option, value):
    with pytest.raises(LedgerError) as error:
        Context(ledger, **{option: value})
    assert error.value.code == "invalid_input"


def test_final_token_count_includes_metadata_and_escaping(ledger, opened, actor, observation_data):
    counter = LiteralCounter()
    observation_data["details"] = ('"\\é🔬\n' * 400) + "Preserve the final exclusion."
    ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "tokens")
    result = Context(ledger, 12000, compression_enabled=True, counter=counter).prepare(
        REPO, opened["id"], actor, query="<|endoftext|>", mode="compact", max_tokens=6500, strict_tokens=True)
    actual = canonical(result)
    assert len(actual) <= 6500
    assert actual in counter.seen
    assert result["limits"]["token_budget_verified"] is True
    assert len(counter.seen) == len(set(counter.seen))


def test_absent_counter_reports_unverified_budget_without_fake_tokens(ledger, opened, actor):
    result = prepared(ledger, opened, actor, max_tokens=1)
    assert result["limits"]["max_tokens"] == 1
    assert result["limits"]["token_budget_verified"] is False
    assert result["counter"] is None


@pytest.mark.parametrize("counter", [None, UnavailableCounter()])
def test_strict_tokens_refuses_unavailable_local_count(ledger, opened, actor, counter):
    with pytest.raises(LedgerError) as error:
        Context(ledger, compression_enabled=True, counter=counter).prepare(
            REPO, opened["id"], actor, query="", mode="compact", max_tokens=8000, strict_tokens=True)
    assert error.value.code == "token_count_unavailable"


def test_non_strict_counter_failure_falls_back_honestly_to_bytes_and_chars(ledger, opened, actor):
    result = Context(ledger, compression_enabled=True, counter=UnavailableCounter()).prepare(
        REPO, opened["id"], actor, query="", mode="compact", max_tokens=8000)
    assert result["limits"]["token_budget_verified"] is False
    assert len(canonical(result)) <= 12000


def test_compression_does_not_mutate_sources_or_source_versions(ledger, opened, actor, lesson_data):
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    with ledger.store.connect() as conn:
        before = {table: [dict(r) for r in conn.execute("SELECT * FROM " + table)]
                  for table in ["observations", "lesson_versions", "lesson_sources", "runs"]}
    result = prepared(ledger, opened, actor)
    lessons = records_of(result, "lesson")
    assert lessons[0]["sources"][0]["id"] == ident
    assert lessons[0]["content"]["conditions"] == sorted(lesson_data["conditions"])
    assert lessons[0]["content"]["exclusions"] == lesson_data["exclusions"]
    assert lessons[0]["content"]["verification"] == lesson_data["verification"]
    with ledger.store.connect() as conn:
        after = {table: [dict(r) for r in conn.execute("SELECT * FROM " + table)] for table in before}
    assert after == before
    for item in expanded(result):
        assert all(source["sha256"] == digest(item["content"]) for source in item["sources"])
    assert_sealed(result)


def test_distinct_attempts_and_contradictory_assessments_remain_distinct(ledger, opened, actor, observed, observation_data):
    second = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "repeat-attempt")["observation_id"]
    finding, first_assessment = assessment(ledger, opened, actor, observed)
    latest = ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "refuted", "basis": "behavior", "rationale": "Another report contradicts this hypothesis.",
        "limitations": "Only this synthetic case was reported.", "observation_ids": [second]}, "contradictory")["assessment_id"]
    result = prepared(ledger, opened, actor)
    observations = records_of(result, "observation")
    assert len(observations) == 2
    assert {r["sources"][0]["id"] for r in observations} == {observed, second}
    assessments = {r["sources"][0]["id"]: r["content"] for r in records_of(result, "assessment")}
    assert assessments[first_assessment]["state"] == "supported"
    assert assessments[first_assessment]["freshness"] == "historical"
    assert assessments[latest]["state"] == "refuted"
    assert assessments[latest]["freshness"] == "current"
    assert assessments[first_assessment]["sources"][0]["relation"] == "supports"
    assert assessments[latest]["sources"][0]["relation"] == "contradicts"


def test_oversized_lesson_is_reference_and_detail_repeats_scope(ledger, opened, actor, lesson_data):
    lesson_data["conditions"] = [str(i) + " complete essential condition" * 15 for i in range(8)]
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    context = Context(ledger, 6000, compression_enabled=True)
    result = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact")
    assert result["state"] == "ok"
    assert not records_of(result, "lesson")
    assert any(ref["detail"]["record_id"] == ident and ref["state"] == "not_loaded" for ref in result["references"])
    assert "complete essential condition" not in canonical(result)
    detail = context.detail(REPO, opened["id"], actor, kind="lesson", record_id=ident,
                            section="exclusions", mode="compact")
    assert detail["complete"] is False
    assert detail["content"]["exclusions"] == lesson_data["exclusions"]
    assert detail["scope"]["repository"] == REPO
    assert detail["scope"]["run_id"] == opened["id"]
    assert detail["scope"]["head_sha"] == "a" * 40
    assert detail["scope"]["base_sha"] == "b" * 40
    assert len(canonical(detail)) <= 6000
    assert_sealed(detail)


def test_large_skill_is_never_partial_instructions(ledger, opened, actor, tmp_path):
    body = "Required start. " + "x" * 15000 + " Required final exclusion."
    skill = registered_skill(ledger, tmp_path, body)
    context = Context(ledger, 6000, compression_enabled=True, skills_enabled=True)
    result = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact")
    assert not records_of(result, "skill")
    assert any(ref["detail"]["record_id"] == skill["id"] for ref in result["references"])
    assert "Required start" not in canonical(result)
    detail = context.detail(REPO, opened["id"], actor, kind="skill", record_id=skill["id"], mode="compact")
    assert detail["state"] == "requires_more_context" and detail["complete"] is False
    assert "content" not in detail
    assert "Required start" not in canonical(detail)


@pytest.mark.parametrize("mode", ["compact", "reference", "full"])
def test_manifest_resume_preserves_requested_representation(ledger, opened, actor, lesson_data, mode):
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode=mode, max_bytes=256000)
    with ledger.store.connect() as conn:
        request = json.loads(conn.execute("SELECT request_json FROM context_manifests WHERE id=?", (first["manifest_id"],)).fetchone()[0])
    assert request["representation_mode"] == mode
    assert request["compression_policy"] == compression.POLICY_VERSION
    resumed = context.resume(REPO, opened["id"], Actor("fresh-host-session"), manifest_id=first["manifest_id"])
    assert resumed["query"] == "retry"
    assert "unknown" in resumed["residency"]
    assert resumed["protocol"] == protocol()
    assert ident in canonical(resumed)
    assert resumed["manifest_id"] != first["manifest_id"]
    if mode == "reference":
        assert resumed["records"] == []
    else:
        assert records_of(resumed, "lesson")


@pytest.mark.parametrize("mode", ["compact", "reference", "full"])
def test_manifest_policy_change_requires_explicit_refresh(ledger, opened, actor, monkeypatch, mode):
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode=mode, max_bytes=256000)
    monkeypatch.setattr(compression, "POLICY_VERSION", "next-policy")
    with pytest.raises(LedgerError) as error:
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"])
    assert error.value.code == "representation_changed"


def test_manifest_mode_change_requires_explicit_refresh(ledger, opened, actor):
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact")
    with pytest.raises(LedgerError) as error:
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"], mode="reference")
    assert error.value.code == "representation_changed"


@pytest.mark.parametrize("mode", ["compact", "reference"])
def test_revoked_lesson_cannot_return_via_prior_manifest_or_detail(ledger, opened, actor, lesson_data, mode):
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode=mode)
    ledger.operator_invalidate(REPO, lesson_data["sources"][0]["observation_id"], "Synthetic corrected source", "invalidate")
    with pytest.raises(LedgerError):
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"])
    with pytest.raises(LedgerError):
        context.detail(REPO, opened["id"], actor, kind="lesson", record_id=ident, mode=mode)


def test_invalidated_observation_and_dependent_assessment_omitted_on_fresh_prepare(ledger, opened, actor, observed):
    _, assessed = assessment(ledger, opened, actor, observed)
    ledger.operator_invalidate(REPO, observed, "Synthetic corrected source", "invalidate")
    result = prepared(ledger, opened, actor)
    assert result["state"] == "ok"
    assert not records_of(result, "observation")
    assert not records_of(result, "assessment")
    assert any(item.get("id") == observed for item in result["omitted"])
    assert any(item.get("id") == assessed for item in result["omitted"])


@pytest.mark.parametrize("kind", ["observation", "assessment"])
@pytest.mark.parametrize("mode", ["compact", "reference"])
def test_invalidated_sources_cannot_be_loaded_through_projected_detail(ledger, opened, actor, observed, kind, mode):
    _, assessed = assessment(ledger, opened, actor, observed)
    ledger.operator_invalidate(REPO, observed, "Synthetic corrected source", "invalidate")
    with pytest.raises(LedgerError) as error:
        Context(ledger, 64000, compression_enabled=True).detail(REPO, opened["id"], actor,
            kind=kind, record_id=observed if kind == "observation" else assessed, mode=mode)
    assert error.value.code in {"context_revoked", "ineligible_evidence"}


def test_revocation_during_render_is_rechecked_before_delivery(ledger, opened, actor, lesson_data, monkeypatch):
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    original = compression.select_bundle
    def revoke_after_render(*args, **kwargs):
        result = original(*args, **kwargs)
        ledger.operator_invalidate(REPO, lesson_data["sources"][0]["observation_id"], "Synthetic mid-render correction", "invalidate-mid-render")
        return result
    monkeypatch.setattr(compression, "select_bundle", revoke_after_render)
    with pytest.raises(LedgerError) as error:
        prepared(ledger, opened, actor)
    assert error.value.code in {"context_revoked", "context_changed"}
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_manifests").fetchone()[0] == 0
        assert Learning.version(conn, ledger.scope(REPO), ident)["eligible_now"] is False


def test_profile_repository_and_run_scope_are_never_reused_by_content_hash(ledger, opened, actor, observed, synthetic_snapshot, tmp_path):
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact")
    other = Ledger(Store(tmp_path / "separate-profile", "synthetic-profile-b"), [REPO],
                   skill_version="0.1.0", skill_hash="d" * 64, relevant_config={"context_budget": 6000})
    other_run = other.open(synthetic_snapshot, actor, "other-open")["run"]
    other_context = Context(other, 64000, compression_enabled=True)
    with pytest.raises(LedgerError):
        other_context.resume(REPO, other_run["id"], actor, manifest_id=first["manifest_id"])
    with pytest.raises(LedgerError):
        other_context.detail(REPO, other_run["id"], actor, kind="observation", record_id=observed, mode="compact")
    newer = ledger.open({**synthetic_snapshot, "head_sha": "e" * 40}, actor, "new-snapshot")["run"]
    with pytest.raises(LedgerError):
        context.detail(REPO, newer["id"], actor, kind="observation", record_id=observed, mode="compact")
    with pytest.raises(LedgerError):
        context.prepare("denied/repository", opened["id"], actor, query="", mode="compact")


def test_detail_assessment_is_interpretable_without_parent_bundle(ledger, opened, actor, observed):
    finding, assessed = assessment(ledger, opened, actor, observed)
    result = Context(ledger, 64000, compression_enabled=True).detail(
        REPO, opened["id"], actor, kind="assessment", record_id=assessed, mode="compact")
    assert result["scope"]["head_sha"] == "a" * 40
    assert result["content"]["finding_id"] == finding
    assert result["content"].get("claim") == "Retry may repeat a reported effect only after persistence."
    assert result["content"].get("sources") == [{"observation_id": observed, "relation": "supports"}]
    assert result["content"]["limitations"] == "No other interruption point was tested; not a universal claim."
    assert result["complete"] is True


def test_explicit_strict_resume_cannot_be_weakened_by_a_non_strict_manifest(ledger, opened, actor):
    context = Context(ledger, 64000, compression_enabled=True)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact", max_tokens=12000)
    assert first["limits"]["token_budget_verified"] is False
    with pytest.raises(LedgerError) as error:
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"], strict_tokens=True)
    assert error.value.code == "token_count_unavailable"


def test_historical_assessment_invalidation_during_detail_is_rechecked(ledger, opened, actor, observed, observation_data, monkeypatch):
    finding, historical = assessment(ledger, opened, actor, observed)
    second = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "independent-source")["observation_id"]
    ledger.record(REPO, opened["id"], actor, 1, "assessment", {
        "finding_id": finding, "state": "refuted", "basis": "behavior", "rationale": "Synthetic later report.",
        "limitations": "Another synthetic boundary only.", "observation_ids": [second]}, "make-first-historical")
    original_seal = compression.seal
    invalidated = False
    def invalidate_after_read(result):
        nonlocal invalidated
        if not invalidated and result.get("kind") == "assessment" and "content" in result:
            invalidated = True
            ledger.operator_invalidate(REPO, observed, "Synthetic mid-detail correction", "invalidate-historical")
        return original_seal(result)
    monkeypatch.setattr(compression, "seal", invalidate_after_read)
    with pytest.raises(LedgerError) as error:
        Context(ledger, 64000, compression_enabled=True).detail(
            REPO, opened["id"], actor, kind="assessment", record_id=historical, mode="compact")
    assert invalidated
    assert error.value.code in {"context_revoked", "context_changed", "ineligible_evidence"}


@pytest.mark.parametrize("mode", ["compact", "reference", "full"])
def test_detail_final_envelope_and_hash_are_counted_after_scope(ledger, opened, actor, observation_data, mode):
    observation_data["details"] = 'é🔬"\\\n' * 500
    observed = ledger.record(REPO, opened["id"], actor, 1, "observation", observation_data, "detail-budget")["observation_id"]
    counter = LiteralCounter()
    context = Context(ledger, 12000, compression_enabled=True, byte_budget=5000, counter=counter)
    result = context.detail(REPO, opened["id"], actor, kind="observation", record_id=observed,
                            mode=mode, max_tokens=4000, strict_tokens=True)
    actual = canonical(result)
    assert len(actual) <= 4000 and len(actual.encode("utf-8")) <= 5000
    assert actual in counter.seen
    assert_sealed(result)
    assert result["limits"]["token_budget_verified"] is True
    assert result["scope"]["run_id"] == opened["id"]
    if "content" in result:
        assert result["content"]["details"] == observation_data["details"]
    else:
        assert result["state"] == "requires_more_context" and result["complete"] is False


def test_checkpoint_remains_literal_and_never_infers_completed_steps(ledger, opened, actor):
    note = "After step 0, before commit: may retry, must not mark complete. Pending 2 ms check."
    # Synthetic storage fixture represents an already recorded pause checkpoint.
    with ledger.store.connect() as conn:
        conn.execute("UPDATE runs SET note=? WHERE id=?", (note, opened["id"]))
    result = prepared(ledger, opened, actor)
    assert result["checkpoint"] == {"recorded_note": note, "source": "run.note", "completed_steps_inferred": False}
    reference = Context(ledger, 64000, compression_enabled=True).prepare(
        REPO, opened["id"], actor, query="", mode="reference")
    assert reference["checkpoint"] is None
    assert note not in canonical(reference)
    assert any(ref["kind"] == "run" and ref["state"] == "not_loaded" for ref in reference["references"])


def test_metrics_failure_does_not_fail_successful_context(ledger, opened, actor, monkeypatch):
    from review_ledger.usage import Usage
    monkeypatch.setattr(Usage, "compression_measurement", lambda *a, **kw: (_ for _ in ()).throw(OSError("Synthetic metrics failure")))
    result = prepared(ledger, opened, actor)
    assert result["state"] == "ok"
    assert_sealed(result)
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_manifests WHERE id=?", (result["manifest_id"],)).fetchone()[0] == 1


def test_tokenizer_and_projection_run_outside_write_transactions(ledger, opened, actor, monkeypatch):
    original_transaction = ledger.store.transaction
    original_project = compression.project_bundle
    from contextlib import contextmanager
    in_write = False
    @contextmanager
    def watched_transaction(conn):
        nonlocal in_write
        with original_transaction(conn):
            in_write = True
            try:
                yield
            finally:
                in_write = False
    class TransactionCheckingCounter(LiteralCounter):
        def count(self, value):
            assert not in_write, "A local count ran inside a write transaction"
            return super().count(value)
    def watched_project(*args, **kwargs):
        assert not in_write, "Projection ran inside a write transaction"
        return original_project(*args, **kwargs)
    monkeypatch.setattr(ledger.store, "transaction", watched_transaction)
    monkeypatch.setattr(compression, "project_bundle", watched_project)
    result = Context(ledger, 64000, compression_enabled=True, counter=TransactionCheckingCounter()).prepare(
        REPO, opened["id"], actor, query="", mode="compact", max_tokens=64000, strict_tokens=True)
    assert result["state"] == "ok"


@pytest.mark.parametrize("mode", ["", False, 0, [], {}])
def test_resume_rejects_invalid_modes_instead_of_silently_defaulting(ledger, opened, actor, mode):
    with pytest.raises(LedgerError) as error:
        Context(ledger, compression_enabled=True).resume(REPO, opened["id"], actor, mode=mode)
    assert error.value.code == "invalid_input"


def test_detail_representation_uses_the_current_shared_policy_version(ledger, opened, actor, observed, monkeypatch):
    monkeypatch.setattr(compression, "POLICY_VERSION", "synthetic-policy-next")
    result = Context(ledger, 64000, compression_enabled=True).detail(
        REPO, opened["id"], actor, kind="observation", record_id=observed, mode="compact")
    assert result["representation"]["policy_version"] == "synthetic-policy-next"


def test_stored_source_hash_version_and_provenance_are_separate_from_projection(ledger, opened, actor, lesson_data):
    ident = approve_lesson(ledger, opened, actor, lesson_data)
    result = prepared(ledger, opened, actor)
    lesson = records_of(result, "lesson")[0]
    observation = records_of(result, "observation")[0]
    with ledger.store.connect() as conn:
        original_lesson = Learning.version(conn, ledger.scope(REPO), ident)
        original_observation = dict(conn.execute("SELECT * FROM observations WHERE id=?", (lesson_data["sources"][0]["observation_id"],)).fetchone())
    assert lesson["sources"][0]["stored_source_sha256"] == digest(original_lesson)
    assert lesson["sources"][0]["version"] == original_lesson["version"]
    assert lesson["sources"][0]["source_references"] == lesson_data["sources"]
    assert observation["sources"][0]["stored_source_sha256"] == digest(original_observation)
    assert observation["sources"][0]["sha256"] == digest(observation["content"])
    assert observation["content"]["provenance"] == "agent_reported"
    assert result["content_digest"] != lesson["sources"][0]["stored_source_sha256"]


def test_resume_inherits_smaller_manifest_caps_and_strictness(ledger, opened, actor):
    counter = LiteralCounter()
    context = Context(ledger, 64000, compression_enabled=True, counter=counter)
    first = context.prepare(REPO, opened["id"], actor, query="retry", mode="compact",
                            max_chars=5000, max_bytes=7000, max_tokens=4800, strict_tokens=True)
    resumed = context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"])
    assert resumed["limits"] == first["limits"]
    assert len(canonical(resumed)) <= 4800
    # Losing local count availability cannot silently weaken a pinned strict cap.
    context.counter = None
    with pytest.raises(LedgerError) as error:
        context.resume(REPO, opened["id"], actor, manifest_id=first["manifest_id"], strict_tokens=False)
    assert error.value.code == "token_count_unavailable"


def test_core_projection_is_host_neutral_and_offline_in_clean_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import socket
import sys
original_import = builtins.__import__
def reject_optional_import(name, *args, **kwargs):
    if name.split(".")[0] in {"hermes", "hermes_cli", "model_tools", "mcp", "tiktoken"}:
        raise AssertionError("Core transformation imported optional host/tokenizer dependency: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_optional_import
def no_network(*args, **kwargs):
    raise AssertionError("Core transformation attempted network access")
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
from review_ledger.compression import project_records, expand_records
from review_ledger.context import Context
from review_ledger.token_count import CountSession
rows = [{"kind": "observation", "sources": [{"id": "synthetic"}],
         "content": {"summary": "No execution; do not infer success.", "valid": False}}]
projected, common = project_records(rows)
assert expand_records(projected, common) == rows
assert CountSession().measure("é🔬<|endoftext|>")["tokens"] is None
assert not any(name.startswith(("hermes_cli", "tiktoken", "mcp.")) for name in sys.modules)
'''
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    completed = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, " + repr(str(root)) + ")\n" + script],
                               cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
