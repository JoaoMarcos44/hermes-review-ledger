"""Production adapter: wraps Hermes SessionDB(read_only=True) as a MetadataReader.

Lazily opens the host session database only when a method is called.  Allowlists
metadata fields from get_session — never prompt text, tool names, model_config,
or preview.  auxiliary_usage_by_task delegates directly; the host already sums
compression lineage, so we never add lineage members separately.

No direct sqlite3 connection to live state.db.  The public tracked read-only
lifecycle (SessionDB with read_only=True) is the only host access path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# Allowlisted metadata fields from SessionDB.get_session.
# NEVER include: system_prompt, tool_names, _system_prompt_resolved,
# _tool_names_resolved, model_config, preview, or any prompt-bearing field.
_SESSION_METADATA_FIELDS = frozenset({
    "id", "source", "model", "billing_provider", "billing_mode",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_call_count", "estimated_cost_usd", "actual_cost_usd",
    "cost_status", "cost_source", "started_at", "ended_at", "last_active",
    "parent_session_id", "message_count", "archived", "hidden",
})


class SessionDBMetadataReader:
    """Read-only adapter over Hermes SessionDB for token-report metadata.

    Lazily opens SessionDB(read_only=True) on first use.  Allowlists metadata
    fields; never emits or stores prompt text.  close() releases the tracked
    host connection.
    """

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path is not None else None
        self._db: Any = None  # lazy: not opened until a method is called

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def _session_db(self):
        """Lazily open the read-only SessionDB; never in __init__."""
        if self._db is None:
            from hermes_state import SessionDB
            self._db = SessionDB(self._db_path, read_only=True)
        return self._db

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return allowlisted metadata for a session, or None if not found.

        Prompt fields, tool names, model_config, and preview are stripped.
        """
        row = self._session_db().get_session(session_id)
        if row is None:
            return None
        return {k: row[k] for k in _SESSION_METADATA_FIELDS if k in row}

    def auxiliary_usage_by_task(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Per-task auxiliary usage summed over compression lineage by the host.

        The real host returns ``api_calls`` (not ``api_call_count``); we
        normalize to the canonical name.  Unknown fields are dropped.
        """
        raw = self._session_db().auxiliary_usage_by_task(session_id)
        normalized = {}
        for task, usage in raw.items():
            canonical = {}
            for k, v in usage.items():
                if k == "api_calls":
                    canonical["api_call_count"] = v
                elif k in ("input_tokens", "output_tokens", "cache_read_tokens",
                           "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd"):
                    canonical[k] = v
                # Drop unknown fields
            normalized[task] = canonical
        return normalized

    def auxiliary_lineage_ids(self, session_id: str) -> list[str]:
        """Match auxiliary_usage_by_task's ancestor prefix and branch cut.

        get_compression_lineage also includes future tips and is not the usage
        scope. This bounded compatibility shim uses the same two host scope
        helpers as its auxiliary getter; an unsupported host fails explicitly.
        """
        db = self._session_db()
        walk = getattr(db, "_session_lineage_root_to_tip", None)
        is_branch = getattr(db, "_is_explicit_branch_session", None)
        if not callable(walk) or not callable(is_branch):
            raise RuntimeError("Host auxiliary usage scope identity is unavailable")
        chain = walk(session_id)
        if not isinstance(chain, list) or len(chain) >= 100 or session_id not in chain:
            raise RuntimeError("Host auxiliary usage scope cannot be established completely")
        for i in range(len(chain) - 1, -1, -1):
            if is_branch(chain[i]):
                return chain[i:]
        return chain

    def list_sessions_metadata(self) -> list[dict[str, Any]]:
        """List all sessions with allowlisted metadata for worker enumeration.

        Uses list_sessions_rich with include_subagents=True to find worker
        sessions.  Bounded to a reasonable limit; callers should disclose
        if the limit is reached.
        """
        rows = self._session_db().list_sessions_rich(
            include_children=True,
            include_subagents=True,
            include_archived=True,
            include_hidden=True,
            compact_rows=True,
            project_compression_tips=False,
            limit=1001,
        )
        if len(rows) > 1000:
            raise RuntimeError("Host descendant scan exceeds its complete bounded window")
        return [{k: row[k] for k in _SESSION_METADATA_FIELDS if k in row} for row in rows]

    def close(self) -> None:
        """Close the underlying SessionDB; idempotent."""
        if self._db is not None:
            self._db.close()
            self._db = None
