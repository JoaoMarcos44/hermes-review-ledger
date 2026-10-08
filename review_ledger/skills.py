"""Explicit operator-approved, repository-local instruction snapshots.

No imported declaration grants tools or evidence authority. Text may influence a
model; validation is not a complete defense against prompt injection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

import yaml

from .models import LedgerError, Scope, canonical, digest, integer, strings, text
from .storage import Store, new_id, now

MAX_FILES = 16
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_DEPTH = 4
MAX_VERSIONS = 256
QUALIFIED_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*\Z")
TEXT_SUFFIXES = {".md", ".txt", ".rst"}


def _qualified(value):
    value = text(value, "qualified_id", 130)
    if not QUALIFIED_ID.fullmatch(value) or any(p in {"review-ledger", "protocol"} for p in value.split("/")):
        raise LedgerError("reserved_namespace", "Use a qualified namespace/name outside review-ledger and protocol")
    return value


def _relative(value):
    value = text(value, "reference path", 240)
    path = PurePosixPath(value)
    if (path.is_absolute() or "\\" in value or ":" in value or
            any(p in {"", ".", ".."} for p in value.split("/")) or
            len(path.parts) > MAX_DEPTH or path.suffix.lower() not in TEXT_SUFFIXES):
        raise LedgerError("unsafe_path", "Only bounded relative Markdown/plain-text reference paths are supported")
    return value


def _read(root: Path, relative: str) -> str:
    """Walk all components with no-follow descriptors; never follow a link race."""
    if os.name == "nt":
        from .windows_import import read_bytes
        try:
            result = read_bytes(root, relative, MAX_FILE_BYTES).decode("utf-8")
        except UnicodeError as exc:
            raise LedgerError("unsafe_path", "Cannot import selected regular UTF-8 text") from exc
        if "\x00" in result:
            raise LedgerError("unsupported_resource", "Binary resources are unsupported")
        return result
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise LedgerError("unsupported_platform", "Safe descriptor-relative local import is unavailable")
    # Do not normalize away operator-supplied traversal components.
    if ".." in root.parts:
        raise LedgerError("unsafe_path", "Package paths must not contain parent traversal")
    absolute = root.absolute()
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*absolute.parts[1:], *PurePosixPath(relative).parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(PurePosixPath(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(leaf)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise LedgerError("unsupported_resource", "Only regular text files of at most 64 KiB are supported")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise LedgerError("resource_limit", "Imported text exceeds the per-file limit")
            result = raw.decode("utf-8")
            if "\x00" in result:
                raise LedgerError("unsupported_resource", "Binary resources are unsupported")
            return result
        finally:
            os.close(leaf)
    except (OSError, UnicodeError) as exc:
        raise LedgerError("unsafe_path", "Cannot import selected regular UTF-8 text without following links") from exc
    finally:
        os.close(fd)


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(k, deep=deep) for k, _ in node.value]
        if any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise LedgerError("invalid_skill", "Frontmatter keys must be unique strings")
        return super().construct_mapping(node, deep=deep)


def _frontmatter(instructions):
    lines = instructions.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise LedgerError("invalid_skill", "SKILL.md requires YAML frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise LedgerError("invalid_skill", "Unterminated YAML frontmatter")
    raw = "".join(lines[1:end])
    if len(raw.encode()) > 8192 or not "".join(lines[end + 1:]).strip():
        raise LedgerError("invalid_skill", "Frontmatter must fit 8 KiB and instructions must be nonempty")
    try:
        depth = 0
        for index, token in enumerate(yaml.scan(raw)):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
                raise LedgerError("invalid_skill", "YAML aliases and anchors are unsupported")
            if isinstance(token, (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken,
                                  yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)):
                depth += 1
            if isinstance(token, (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken,
                                  yaml.tokens.FlowSequenceEndToken)):
                depth -= 1
            if index > 1000 or depth > 8:
                raise LedgerError("resource_limit", "Frontmatter structure is too large")
        data = yaml.load(raw, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise LedgerError("invalid_skill", "Malformed or unsupported safe YAML frontmatter") from exc
    allowed = {"name", "description", "license", "compatibility", "metadata", "allowed-tools", "applicability"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise LedgerError("unsupported_resource", "Unsupported frontmatter fields; executable dependencies are not supported")
    name = text(data.get("name"), "skill name", 64)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise LedgerError("invalid_skill", "Skill name must use lowercase letters, digits and single hyphens")
    text(data.get("description"), "skill description", 1024)
    for key in ("license", "compatibility", "allowed-tools"):
        if key in data:
            text(data[key], key, 2048)
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict) or len(metadata) > 20:
        raise LedgerError("invalid_skill", "metadata must be a small string mapping")
    for key, value in metadata.items():
        text(key, "metadata key", 100)
        text(value, "metadata value", 2048)
    applicability = data.get("applicability", {})
    if not isinstance(applicability, dict) or set(applicability) - {"tags", "symbols", "conditions", "exclusions", "phases", "repositories"}:
        raise LedgerError("invalid_skill", "Unsupported applicability fields")
    for key, value in applicability.items():
        strings(value, key, 20, 500)
    if any(p not in {"discover", "investigate", "assess", "resume"} for p in applicability.get("phases", [])):
        raise LedgerError("invalid_skill", "Unsupported applicability phase")
    return data


def _identity(metadata, instructions, resources):
    return digest({"metadata": metadata, "instructions": instructions, "resources": resources})


class Skills:
    """Operator mutation methods must never be exposed as model-facing tools."""
    def __init__(self, store: Store):
        self.store = store

    def register(self, scope: Scope, package_path, *, qualified_id: str, references=(), approved=False, enabled=False):
        if approved is not True:
            raise LedgerError("approval_required", "Explicit operator approval of one local package is required")
        if type(enabled) is not bool:
            raise LedgerError("invalid_input", "enabled must be boolean")
        qualified_id = _qualified(qualified_id)
        if not isinstance(references, (list, tuple)) or len(references) >= MAX_FILES:
            raise LedgerError("resource_limit", "At most 15 explicit text references are supported")
        paths = [_relative(p) for p in references]
        if len(set(paths)) != len(paths) or "SKILL.md" in paths:
            raise LedgerError("invalid_input", "References must be unique and must not repeat SKILL.md")
        root = Path(package_path)
        instructions = _read(root, "SKILL.md")
        metadata = _frontmatter(instructions)
        if metadata["name"] != qualified_id.split("/")[1]:
            raise LedgerError("invalid_skill", "Qualified name must match the SKILL.md name")
        resources = {p: _read(root, p) for p in sorted(paths)}
        if sum(len(s.encode()) for s in [instructions, *resources.values()]) > MAX_TOTAL_BYTES:
            raise LedgerError("resource_limit", "Selected skill text exceeds 256 KiB")
        # Source identity deliberately excludes private absolute filesystem paths.
        metadata = {**metadata, "source": "operator-approved-local-package", "source_package": root.name,
                    "scope": "repository", "tool_grants": False,
                    "unsupported_capabilities": ["execution", "remote-fetch", "implicit-tool-grants"]}
        checksum = _identity(metadata, instructions, resources)
        with self.store.connect() as conn, self.store.transaction(conn):
            old = conn.execute("SELECT id FROM optional_skill_versions WHERE repository_id=? AND qualified_id=? AND content_digest=?",
                               (scope.repository_id, qualified_id, checksum)).fetchone()
            if old:
                return self.version(conn, scope, old["id"])
            count = conn.execute("SELECT COUNT(*) FROM optional_skill_versions WHERE repository_id=?", (scope.repository_id,)).fetchone()[0]
            if count >= MAX_VERSIONS:
                raise LedgerError("resource_limit", "Repository optional-skill version limit reached")
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM optional_skill_versions WHERE repository_id=? AND qualified_id=?", (scope.repository_id, qualified_id)).fetchone()[0]
            ident = new_id("skill")
            conn.execute("INSERT INTO optional_skill_versions VALUES (?,?,?,?,?,?,?,?,?)",
                         (ident, scope.repository_id, qualified_id, version, checksum, canonical(metadata), instructions, int(enabled), now()))
            for path, content in resources.items():
                conn.execute("INSERT INTO optional_skill_resources VALUES (?,?,?,?,?)", (scope.repository_id, ident, path, content, hashlib.sha256(content.encode()).hexdigest()))
            self.store.audit(conn, scope, ident, "skill_register", "operator", {"qualified_id": qualified_id, "content_digest": checksum, "enabled": enabled})
            return self.version(conn, scope, ident)

    def update(self, scope: Scope, qualified_id: str, package_path, *, references=(), approved=False):
        with self.store.connect() as conn:
            if conn.execute("SELECT 1 FROM optional_skill_versions WHERE repository_id=? AND qualified_id=?", (scope.repository_id, qualified_id)).fetchone() is None:
                raise LedgerError("scope_not_found", "Register this skill before updating")
        return self.register(scope, package_path, qualified_id=qualified_id, references=references, approved=approved)

    @staticmethod
    def version(conn, scope: Scope, version_id: str, *, require_enabled=False):
        row = conn.execute("SELECT * FROM optional_skill_versions WHERE repository_id=? AND id=?", (scope.repository_id, version_id)).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "Skill version is not in this repository")
        if require_enabled and not row["enabled"]:
            raise LedgerError("ineligible_skill", "Skill version is disabled or revoked")
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        resource_rows = conn.execute("SELECT path,content,content_digest FROM optional_skill_resources WHERE repository_id=? AND version_id=? ORDER BY path LIMIT ?", (scope.repository_id, version_id, MAX_FILES + 1)).fetchall()
        resources = {r["path"]: r["content"] for r in resource_rows}
        if len(resources) >= MAX_FILES or any(hashlib.sha256(r["content"].encode()).hexdigest() != r["content_digest"] for r in resource_rows) or _identity(item["metadata"], item["instructions"], resources) != item["content_digest"]:
            raise LedgerError("artifact_corrupt", "Immutable skill snapshot is missing or has a digest mismatch")
        item["resources"] = resources
        item["eligible_now"] = bool(item["enabled"])
        return item

    @staticmethod
    def applicability(metadata, *, repository, phase, tags=(), symbols=()):
        """Explicit OR-within/AND-between constraints; prose is never guessed."""
        from .learning import normalize_search
        declared = metadata.get("applicability", {})
        unknown = []
        matched = []
        actual = {"repositories": [repository], "phases": [phase] if phase is not None else [], "tags": tags, "symbols": symbols}
        for field, values in actual.items():
            expected = declared.get(field, [])
            if not expected:
                continue
            if not values:
                unknown.append(field)
            elif not ({normalize_search(v) for v in expected} & {normalize_search(v) for v in values}):
                return {"state": "inapplicable", "reason": "declared " + field + " mismatch", "eligible": False}
            else:
                matched.append(field)
        unknown.extend(k for k in ("conditions", "exclusions") if declared.get(k))
        if not declared:
            unknown.append("no declared constraints")
        return {"state": "unknown" if unknown else "matched", "eligible": True,
                "matched_fields": matched, "unresolved_fields": unknown,
                "notice": "Structured matches do not verify prose conditions or establish evidence"}

    @staticmethod
    def candidates(conn, scope: Scope, *, limit=50):
        integer(limit, "limit", 1, 100)
        # Metadata-only discovery, bounded before full text is loaded.
        rows = conn.execute("SELECT id,qualified_id,version,content_digest,metadata_json,length(CAST(instructions AS BLOB)) + COALESCE((SELECT SUM(length(CAST(content AS BLOB))) FROM optional_skill_resources r WHERE r.version_id=optional_skill_versions.id),0) AS size_bytes FROM optional_skill_versions WHERE repository_id=? AND enabled=1 ORDER BY qualified_id,version DESC,id LIMIT ?", (scope.repository_id, limit)).fetchall()
        return [{**{k: r[k] for k in r.keys() if k != "metadata_json"}, "metadata": json.loads(r["metadata_json"])} for r in rows]

    def inspect(self, scope: Scope, version_id: str):
        with self.store.connect() as conn:
            return self.version(conn, scope, version_id)

    def load(self, scope: Scope, version_id: str):
        with self.store.connect() as conn:
            return self.version(conn, scope, version_id, require_enabled=True)

    def list(self, scope: Scope, *, enabled_only=False, limit=50):
        integer(limit, "limit", 1, 100)
        with self.store.connect() as conn:
            rows = conn.execute("SELECT id,qualified_id,version,content_digest,enabled,created_at FROM optional_skill_versions WHERE repository_id=? AND (?=0 OR enabled=1) ORDER BY qualified_id,version DESC LIMIT ?", (scope.repository_id, int(enabled_only), limit)).fetchall()
            return [dict(r) for r in rows]

    def _state(self, scope, version_id, enabled, reason, request_key):
        text(reason, "reason", 2000)
        def write(conn):
            self.version(conn, scope, version_id)
            conn.execute("UPDATE optional_skill_versions SET enabled=? WHERE repository_id=? AND id=?", (int(enabled), scope.repository_id, version_id))
            self.store.audit(conn, scope, version_id, "skill_enable" if enabled else "skill_disable", "operator", {"reason": reason})
            return {"skill_version_id": version_id, "enabled": enabled}
        return self.store.write(scope, "skill_state", version_id, request_key, {"enabled": enabled, "reason": reason}, write)

    def enable(self, scope, version_id, *, reason, request_key):
        return self._state(scope, version_id, True, reason, request_key)

    def disable(self, scope, version_id, *, reason, request_key):
        return self._state(scope, version_id, False, reason, request_key)
