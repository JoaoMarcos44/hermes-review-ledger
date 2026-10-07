"""Exact optional skill disclosure and conservative applicability."""
import json

import pytest

from review_ledger.context import Context
from review_ledger.models import LedgerError, canonical
from review_ledger.skills import Skills


def register(ledger, tmp_path, *, name="example", applicability="", body="Check the complete condition and exclusion."):
    package = tmp_path / name
    package.mkdir(exist_ok=True)
    (package / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Synthetic retry investigation\n{applicability}---\n{body}\n")
    return Skills(ledger.store).register(ledger.scope("synthetic/example"), package,
                                        qualified_id=f"synthetic/{name}", approved=True, enabled=True)


def prepare(ledger, opened, actor, **kwargs):
    return Context(ledger, budget=24000, skills_enabled=True).prepare(
        "synthetic/example", opened["id"], actor, query="retry", **kwargs)


def skill_records(bundle):
    return [r for r in bundle["records"] if r["kind"] == "skill"]


def test_structured_applicability_filters_and_unicode(ledger, opened, actor, tmp_path):
    wanted = register(ledger, tmp_path, applicability="applicability:\n  tags: [CAFÉ]\n  symbols: [Save]\n  phases: [investigate, resume]\n  repositories: [synthetic/example]\n  exclusions: ['Do not apply to read-only effects']\n")
    register(ledger, tmp_path, name="other", applicability="applicability:\n  repositories: [synthetic/other]\n")
    bundle = prepare(ledger, opened, actor, tags=["cafe\u0301"], symbols=["save"])
    records = skill_records(bundle)
    assert len(records) == 1
    assert records[0]["sources"][0]["id"] == wanted["id"]
    assert records[0]["applicability"]["state"] == "unknown"
    assert records[0]["applicability"]["unresolved_fields"] == ["exclusions"]
    assert "Do not apply" in records[0]["content"]["instructions"]
    assert any("repositories mismatch" in r["reason"] for r in bundle["references"])
    mismatch = prepare(ledger, opened, actor, tags=["network"], symbols=["save"])
    assert not skill_records(mismatch)


def test_missing_constraints_are_uncertain_not_invented_match(ledger, opened, actor, tmp_path):
    register(ledger, tmp_path, applicability="applicability:\n  tags: [retry]\n")
    record = skill_records(prepare(ledger, opened, actor))[0]
    assert record["applicability"]["state"] == "unknown"
    assert record["applicability"]["unresolved_fields"] == ["tags"]


def test_discovery_card_does_not_load_instructions(ledger, opened, actor, tmp_path):
    skill = register(ledger, tmp_path, body="Unique full instructions not present in discovery.")
    bundle = prepare(ledger, opened, actor, phase="discover")
    card = skill_records(bundle)[0]["content"]
    assert card["instructions_state"] == "not_loaded"
    assert card["size_bytes"] > 0
    assert "Unique full instructions" not in canonical(bundle)
    detail = Context(ledger, budget=24000, skills_enabled=True).detail("synthetic/example", opened["id"], actor,
                                                                     kind="skill", record_id=skill["id"])
    assert "Unique full instructions" in detail["content"]["instructions"]


def test_resume_pins_old_version_new_prepare_refreshes(ledger, opened, actor, tmp_path):
    old = register(ledger, tmp_path, body="Old complete instruction.")
    bundle = prepare(ledger, opened, actor)
    new = register(ledger, tmp_path, body="New complete instruction.")
    context = Context(ledger, budget=24000, skills_enabled=True)
    resumed = context.resume("synthetic/example", opened["id"], actor, manifest_id=bundle["manifest_id"])
    assert skill_records(resumed)[0]["sources"][0]["id"] == old["id"]
    refreshed = prepare(ledger, opened, actor)
    assert len(skill_records(refreshed)) == 1
    assert skill_records(refreshed)[0]["sources"][0]["id"] == new["id"]
    Skills(ledger.store).disable(ledger.scope("synthetic/example"), old["id"], reason="Revoked", request_key="revoke-pinned")
    with pytest.raises(LedgerError, match="disabled"):
        context.resume("synthetic/example", opened["id"], actor, manifest_id=bundle["manifest_id"])


def test_oversized_instruction_unit_is_reference_not_partial(ledger, opened, actor, tmp_path):
    skill = register(ledger, tmp_path, body="Important condition. " + "x" * 15000 + " Crucial exclusion.")
    bundle = prepare(ledger, opened, actor, max_chars=6000)
    assert len(canonical(bundle)) <= 6000
    assert not skill_records(bundle)
    assert any(r["id"] == skill["id"] and r["state"] == "not_loaded" for r in bundle["references"])
    assert "Important condition" not in canonical(bundle)
    with ledger.store.connect() as conn:
        row = conn.execute("SELECT selections_json FROM context_manifests WHERE id=?", (bundle["manifest_id"],)).fetchone()
        assert not any(i["kind"] == "skill" for i in json.loads(row[0]))


def test_optional_skill_feature_disabled(ledger, opened, actor, tmp_path):
    register(ledger, tmp_path)
    bundle = Context(ledger, budget=24000).prepare("synthetic/example", opened["id"], actor, query="retry")
    assert not skill_records(bundle)
