"""Synthetic explicit local skill imports; no host, network, or model involved."""
from pathlib import Path
from contextlib import closing
import sqlite3

import pytest

from review_ledger.models import LedgerError
from review_ledger.skills import Skills, MAX_FILE_BYTES


@pytest.fixture
def registry(ledger, opened):
    scope = ledger.scope("synthetic/example")
    # Coordinator migration provides these tables; tests run against real Store.
    return Skills(ledger.store), scope


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "example"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: example\ndescription: Synthetic retry checks\nlicense: MIT\nmetadata:\n  author: Synthetic author\napplicability:\n  tags: [retry]\nallowed-tools: shell\n---\nCheck both effects and acknowledgements.\n", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "notes.md").write_text("Never omit a crucial exclusion.\n", encoding="utf-8")
    return root


def add(registry, package, **kwargs):
    skills, scope = registry
    return skills.register(scope, package, qualified_id="synthetic/example", approved=True, **kwargs)


def test_explicit_approval_and_cold_start(registry, package):
    skills, scope = registry
    assert skills.list(scope) == []
    with pytest.raises(LedgerError, match="approval"):
        skills.register(scope, package, qualified_id="synthetic/example")
    skill = add(registry, package)
    assert not skill["eligible_now"]
    with pytest.raises(LedgerError, match="disabled"):
        skills.load(scope, skill["id"])
    assert skill["metadata"]["tool_grants"] is False
    assert skill["metadata"]["license"] == "MIT"
    assert str(package) not in str(skill)


def test_snapshot_update_revoke_and_scope(registry, package, ledger, synthetic_snapshot, actor):
    skills, scope = registry
    old = add(registry, package, references=["references/notes.md"])
    skills.enable(scope, old["id"], reason="Synthetic operator approval", request_key="enable-one")
    assert skills.load(scope, old["id"])["resources"]["references/notes.md"].startswith("Never")
    (package / "SKILL.md").write_text((package / "SKILL.md").read_text() + "New source bytes.\n")
    assert "New source bytes" not in skills.load(scope, old["id"])["instructions"]
    new = skills.update(scope, "synthetic/example", package, references=["references/notes.md"], approved=True)
    assert new["version"] == 2 and not new["enabled"]
    assert old["content_digest"] != new["content_digest"]
    assert skills.load(scope, old["id"])["eligible_now"]
    skills.disable(scope, old["id"], reason="Synthetic revocation", request_key="disable-one")
    with pytest.raises(LedgerError, match="disabled"):
        skills.load(scope, old["id"])
    with skills.store.connect() as conn:
        assert skills.candidates(conn, scope) == []
    synthetic_snapshot.update(repository_id=1002, repository_node_id="synthetic-other-node", repository_full_name="synthetic/other", url="https://github.com/synthetic/other/pull/7")
    ledger.open(synthetic_snapshot, actor, "open-other")
    other = ledger.scope("synthetic/other")
    assert skills.list(other) == []
    with pytest.raises(LedgerError, match="repository"):
        skills.inspect(other, old["id"])


@pytest.mark.parametrize("name", ["review-ledger/example", "custom/review-ledger", "protocol/example", "custom/protocol", "example", "../example"])
def test_reserved_names(registry, package, name):
    skills, scope = registry
    with pytest.raises(LedgerError):
        skills.register(scope, package, qualified_id=name, approved=True)


@pytest.mark.parametrize("reference", ["../secret.md", "/tmp/secret.md", "references/../SKILL.md", "a\\b.md", "script.py", "a/b/c/d/e.md", "file:///tmp/a.md"])
def test_unsafe_reference_paths(registry, package, reference):
    with pytest.raises(LedgerError):
        add(registry, package, references=[reference])


def test_symlink_reference_and_ancestor(registry, package, tmp_path):
    target = tmp_path / "outside.md"
    target.write_text("Outside data")
    (package / "references" / "link.md").symlink_to(target)
    with pytest.raises(LedgerError):
        add(registry, package, references=["references/link.md"])
    alias = tmp_path / "alias"
    alias.symlink_to(package, target_is_directory=True)
    with pytest.raises(LedgerError):
        add(registry, alias)


@pytest.mark.parametrize("frontmatter", [
    "name: example\nname: example\ndescription: duplicate", 
    "name: example\ndescription: !!python/object/apply:os.system ['false']",
    "name: example\ndescription: &alias hello\nlicense: *alias",
    "name: example\ndescription: example\nscripts: [run.py]",
    "name: example\ndescription: example\nmetadata: {version: 1}",
])
def test_safe_yaml_and_unsupported_dependencies(registry, package, frontmatter):
    (package / "SKILL.md").write_text(f"---\n{frontmatter}\n---\nText")
    with pytest.raises(LedgerError):
        add(registry, package)


def test_limits_and_unselected_files(registry, package):
    (package / "run.py").write_text("raise RuntimeError('must never execute')")
    add(registry, package)  # Unselected executable content is not read or run.
    (package / "references" / "huge.md").write_text("x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(LedgerError):
        add(registry, package, references=["references/huge.md"])
    with pytest.raises(LedgerError):
        add(registry, package, references=[f"{i}.md" for i in range(16)])


def test_snapshot_hash_and_missing_resource(registry, package):
    skills, scope = registry
    skill = add(registry, package, references=["references/notes.md"], enabled=True)
    with skills.store.connect() as conn:
        conn.execute("DELETE FROM optional_skill_resources WHERE version_id=?", (skill["id"],))
    with pytest.raises(LedgerError, match="digest mismatch"):
        skills.load(scope, skill["id"])


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_import_dedup_and_backup_contains_text(registry, package, line_ending):
    skills, scope = registry
    expected = "Never omit a crucial exclusion." + line_ending
    (package / "references" / "notes.md").write_bytes(expected.encode("utf-8"))
    skill = add(registry, package, references=["references/notes.md"], enabled=True)
    assert add(registry, package, references=["references/notes.md"])["id"] == skill["id"]
    destination = package.parent / "copy.sqlite3"
    with skills.store.connect() as conn, closing(sqlite3.connect(destination)) as copy:
        conn.backup(copy)
    with closing(sqlite3.connect(destination)) as copy:
        assert copy.execute("SELECT content FROM optional_skill_resources").fetchone()[0] == expected
        assert copy.execute("SELECT content_digest FROM optional_skill_versions").fetchone()[0] == skill["content_digest"]


def test_replayed_enable_does_not_resurrect_revocation(registry, package):
    skills, scope = registry
    skill = add(registry, package)
    skills.enable(scope, skill["id"], reason="Approve", request_key="enable-fixed")
    skills.disable(scope, skill["id"], reason="Revoke", request_key="disable-fixed")
    skills.enable(scope, skill["id"], reason="Approve", request_key="enable-fixed")
    with pytest.raises(LedgerError, match="disabled"):
        skills.load(scope, skill["id"])


def test_total_limit_and_no_network(registry, package, monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("Skill import must not access network")
    monkeypatch.setattr(socket, "socket", forbidden)
    add(registry, package)
    paths = []
    for i in range(4):
        path = f"references/{i}.md"
        (package / path).write_text("x" * MAX_FILE_BYTES)
        paths.append(path)
    with pytest.raises(LedgerError, match="256 KiB"):
        add(registry, package, references=paths)


def test_tampered_instruction_hash(registry, package):
    skills, scope = registry
    skill = add(registry, package, enabled=True)
    with skills.store.connect() as conn:
        conn.execute("UPDATE optional_skill_versions SET instructions='changed' WHERE id=?", (skill["id"],))
    with pytest.raises(LedgerError, match="digest mismatch"):
        skills.load(scope, skill["id"])
