"""Truthful, read-only token analyzer correlated to ledger runs and sessions.

All data comes from canonical host session counters via an injected reader
seam. No model-provided usage, no chars/4, no invented provider responses.
Monetary actual / estimate / included / unknown are distinct; unknown is
never replaced with $0.  Whole-session, non-marginal attribution is explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from datetime import datetime, timezone
from typing import Any, Protocol

from .models import LedgerError


# ── Reader seam ──────────────────────────────────────────────────────────────

class MetadataReader(Protocol):
    """Injected seam for host session usage metadata (read-only).

    ``get_session`` returns allowlisted metadata only — never prompt text,
    tool names, model_config, or preview.  ``auxiliary_usage_by_task`` returns
    per-task usage summed over the session's compression lineage by the host.
    """

    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    def auxiliary_usage_by_task(self, session_id: str) -> dict[str, dict[str, Any]]: ...


# ── Allowlisted metadata fields (NO prompt / config / text) ─────────────────

_SESSION_METADATA_FIELDS = frozenset({
    "id", "source", "model", "billing_provider", "billing_mode",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_call_count", "estimated_cost_usd", "actual_cost_usd",
    "cost_status", "cost_source", "started_at", "ended_at", "last_active",
    "parent_session_id", "message_count", "archived", "hidden",
})

_TOKEN_COUNTER_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens",
)

_AUX_TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "api_call_count",
)

_AUX_TOTAL_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "api_call_count",
)

# JSON-safe integer bound (2**53 - 1)
_OVERFLOW = 2**53 - 1
MAX_RUNS = 1000
MAX_SESSIONS = 1000
MAX_AUDIT_EVENTS = 20000


# ── Counter validation ──────────────────────────────────────────────────────

def _validate_counter(value, field, session_id):
    """Reject malformed counters: negative, bool, non-int, overflow."""
    if value is None:
        return None
    if type(value) is bool:
        raise ValueError(f"Token counter {field} for session {session_id} must not be a boolean")
    if type(value) is not int:
        raise ValueError(f"Token counter {field} for session {session_id} must be an integer")
    if value < 0:
        raise ValueError(f"Token counter {field} for session {session_id} must be nonnegative")
    if value > _OVERFLOW:
        raise ValueError(f"Token counter {field} for session {session_id} exceeds overflow bound")
    return value


def _validate_cost(value, field, session_id):
    """Validate cost fields: non-negative finite float or None."""
    if value is None:
        return None
    if type(value) is bool:
        raise ValueError(f"Cost field {field} for session {session_id} must not be a boolean")
    if type(value) not in (int, float):
        raise ValueError(f"Cost field {field} for session {session_id} must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"Cost field {field} for session {session_id} must be finite")
    if value < 0:
        raise ValueError(f"Cost field {field} for session {session_id} must be nonnegative")
    return float(value)


def _safe_add(a, b, field, session_id):
    """Add two counters, checking for overflow."""
    if a is None or b is None:
        return None
    result = a + b
    if result > _OVERFLOW:
        raise ValueError(f"Token counter {field} for session {session_id} exceeds overflow bound")
    return result


# ── Canonical computation ───────────────────────────────────────────────────

def _canonical_main(metadata, session_id):
    """Compute canonical main-task usage from session metadata.

    prompt = input + cache_read + cache_write (inclusive)
    total  = prompt + output
    reasoning is a subset of output — never added again.
    """
    counters = {}
    for field in _TOKEN_COUNTER_FIELDS:
        counters[field] = _validate_counter(metadata.get(field), field, session_id)

    # Reasoning must be a subset of output
    if (counters["reasoning_tokens"] is not None and counters["output_tokens"] is not None
            and counters["reasoning_tokens"] > counters["output_tokens"]):
        raise ValueError(f"Token counter reasoning_tokens for session {session_id} must not exceed output_tokens")

    prompt = None
    if all(counters[f] is not None for f in ("input_tokens", "cache_read_tokens", "cache_write_tokens")):
        prompt = _safe_add(
            _safe_add(counters["input_tokens"], counters["cache_read_tokens"], "prompt_tokens", session_id),
            counters["cache_write_tokens"], "prompt_tokens", session_id
        )

    total = None
    if prompt is not None and counters["output_tokens"] is not None:
        total = _safe_add(prompt, counters["output_tokens"], "total_tokens", session_id)

    api_call_count = _validate_counter(metadata.get("api_call_count"), "api_call_count", session_id)

    coverage = "complete" if api_call_count is not None and all(counters[f] is not None for f in _TOKEN_COUNTER_FIELDS) else "partial"

    return {
        "input_tokens": counters["input_tokens"],
        "output_tokens": counters["output_tokens"],
        "cache_read_tokens": counters["cache_read_tokens"],
        "cache_write_tokens": counters["cache_write_tokens"],
        "reasoning_tokens": counters["reasoning_tokens"],
        "prompt_tokens": prompt,
        "total_tokens": total,
        "api_call_count": api_call_count,
        "coverage": coverage,
        "source": "host_session_counters",
    }


def _canonical_auxiliary(tasks, session_id):
    """Compute canonical auxiliary usage from per-task breakdown.

    The host's ``auxiliary_usage_by_task`` already sums compression lineage;
    we never add lineage members separately.
    """
    if tasks is None:
        return {"tasks": {}, "totals": {f: None for f in (*_AUX_TOTAL_FIELDS, "prompt_tokens", "total_tokens")},
                "coverage": "missing", "source": "host_session_model_usage"}
    if not tasks:
        return {
            "tasks": {},
            "totals": {f: 0 for f in _AUX_TOTAL_FIELDS} | {"prompt_tokens": 0, "total_tokens": 0},
            "coverage": "complete",
            "source": "host_session_model_usage",
        }

    canonical_tasks = {}
    totals = {f: 0 for f in _AUX_TOTAL_FIELDS}
    all_complete = True

    for task_name in sorted(tasks):
        task_data = tasks[task_name]
        task_counters = {}
        for field in _AUX_TOKEN_FIELDS:
            task_counters[field] = _validate_counter(task_data.get(field), field, session_id)

        # Reasoning must be a subset of output
        if (task_counters["reasoning_tokens"] is not None and task_counters["output_tokens"] is not None
                and task_counters["reasoning_tokens"] > task_counters["output_tokens"]):
            raise ValueError(f"Token counter reasoning_tokens for task {task_name} in session {session_id} must not exceed output_tokens")

        prompt = None
        if all(task_counters[f] is not None for f in ("input_tokens", "cache_read_tokens", "cache_write_tokens")):
            prompt = _safe_add(
                _safe_add(task_counters["input_tokens"], task_counters["cache_read_tokens"], "prompt_tokens", session_id),
                task_counters["cache_write_tokens"], "prompt_tokens", session_id
            )

        total = None
        if prompt is not None and task_counters["output_tokens"] is not None:
            total = _safe_add(prompt, task_counters["output_tokens"], "total_tokens", session_id)

        if not all(task_counters[f] is not None for f in _AUX_TOKEN_FIELDS):
            all_complete = False

        canonical_tasks[task_name] = {
            "input_tokens": task_counters["input_tokens"],
            "output_tokens": task_counters["output_tokens"],
            "cache_read_tokens": task_counters["cache_read_tokens"],
            "cache_write_tokens": task_counters["cache_write_tokens"],
            "reasoning_tokens": task_counters["reasoning_tokens"],
            "prompt_tokens": prompt,
            "total_tokens": total,
            "api_call_count": task_counters["api_call_count"],
        }

        for field in _AUX_TOKEN_FIELDS:
            totals[field] = _safe_add(totals[field], task_counters[field], f"auxiliary.{field}", session_id)

    totals_prompt = None
    if all(totals[f] is not None for f in ("input_tokens", "cache_read_tokens", "cache_write_tokens")):
        totals_prompt = _safe_add(
            _safe_add(totals["input_tokens"], totals["cache_read_tokens"], "auxiliary.prompt_tokens", session_id),
            totals["cache_write_tokens"], "auxiliary.prompt_tokens", session_id
        )
    totals_total = None
    if totals_prompt is not None and totals["output_tokens"] is not None:
        totals_total = _safe_add(totals_prompt, totals["output_tokens"], "auxiliary.total_tokens", session_id)

    return {
        "tasks": canonical_tasks,
        "totals": {**totals, "prompt_tokens": totals_prompt, "total_tokens": totals_total},
        "coverage": "complete" if all_complete else "partial",
        "source": "host_session_model_usage",
    }


def _cost_view(metadata, session_id):
    """Extract cost view: actual vs estimate vs included vs unknown."""
    estimated = _validate_cost(metadata.get("estimated_cost_usd"), "estimated_cost_usd", session_id)
    actual = _validate_cost(metadata.get("actual_cost_usd"), "actual_cost_usd", session_id)
    status_raw = metadata.get("cost_status")

    if actual is not None:
        status = "actual"
    elif estimated is not None:
        if isinstance(status_raw, str) and status_raw in ("included", "subscription"):
            status = "included"
        else:
            status = "estimated"
    else:
        status = "unknown"

    source = metadata.get("cost_source")
    return {
        "estimated_usd": estimated,
        "actual_usd": actual,
        "status": status,
        "source": source if isinstance(source, str) else None,
    }


@dataclass
class _Selection:
    """One captured Ledger view: selected sessions, correlation, and run map."""

    session_ids: set[str]
    correlation: dict[str, list[str]]
    run_session_map: dict[str, set[str]]
    scope_kind: str


# ── TokenReport ─────────────────────────────────────────────────────────────

class TokenReport:
    """Read-only token analyzer correlated to ledger runs and sessions.

    Correlates trusted run audit actors ('opened', 'acquire', plus explicit
    owner) to unique session IDs in THIS authorized repo.  Completed owner
    clearing cannot lose history because audit events preserve the actor
    session_id.  Foreign run/session selectors are rejected.  Bound queries
    and disclose omissions — never silent totals.
    """

    def __init__(self, ledger, reader: MetadataReader):
        self.ledger = ledger
        self.reader = reader

    def report(self, repository, run_id=None, session_id=None, include_workers=False):
        """Produce a truthful token report for a repository, run, or session.

        All bounded Ledger reads are taken inside one explicit read snapshot
        that is released before any host metadata or worker enumeration, so
        selection and shared-membership disclosures share one captured view.
        No model-provided usage, no filesystem path arguments, no other
        profiles.  Returns body-free JSON-friendly data.
        """
        if type(include_workers) is not bool:
            raise ValueError("include_workers must be boolean")
        scope = self.ledger.scope(repository)

        with self.ledger.store.connect() as conn:
            owns_snapshot = not conn.in_transaction
            if owns_snapshot:
                conn.execute("BEGIN")
            try:
                if run_id is not None:
                    selection = self._select_run(conn, scope, run_id, session_id)
                elif session_id is not None:
                    selection = self._select_session(conn, scope, session_id)
                else:
                    selection = self._select_repository(conn, scope)
            finally:
                # Release the read snapshot BEFORE any host read so durable
                # writers are never blocked by this report.
                if owns_snapshot:
                    conn.rollback()

        if include_workers:
            selection = self._expand_workers_selection(selection)
        return self._build_report(scope, selection, include_workers=include_workers)

    # ── correlation helpers ────────────────────────────────────────────────

    def _correlate_run_sessions(self, conn, scope, run_id):
        """Correlate trusted audit actors to unique session IDs for a run."""
        run = conn.execute(
            "SELECT * FROM runs WHERE repository_id=? AND id=?",
            (scope.repository_id, run_id),
        ).fetchone()
        if run is None:
            raise LedgerError("scope_not_found", "Run is not present in this repository and profile")

        session_ids: set[str] = set()
        correlation: dict[str, list[str]] = {}

        # Explicit owner (may be NULL after completion)
        if run["owner_session"]:
            session_ids.add(run["owner_session"])
            correlation.setdefault(run["owner_session"], []).append("owner")

        # Trusted audit actors: 'opened', 'acquire', 'operator_transfer'
        audit_rows = conn.execute(
            "SELECT actor, action, detail_json FROM audit_events "
            "WHERE repository_id=? AND entity_id=? AND action IN ('opened','acquire','operator_transfer') "
            "ORDER BY created_at,id LIMIT ?",
            (scope.repository_id, run_id, MAX_AUDIT_EVENTS + 1),
        ).fetchall()
        if len(audit_rows) > MAX_AUDIT_EVENTS:
            raise LedgerError("resource_limit", "Run owner history exceeds the bounded event cap")
        for row in audit_rows:
            action = row["action"]
            actor = row["actor"]
            if action == "operator_transfer":
                # New owner is in detail_json.new_session, not actor
                try:
                    detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    detail = {}
                new_session = detail.get("new_session")
                if new_session:
                    session_ids.add(new_session)
                    correlation.setdefault(new_session, []).append("operator_transfer")
            elif actor and actor != "local_operator":
                session_ids.add(actor)
                correlation.setdefault(actor, []).append(action)

        return run, session_ids, correlation

    def _select_run(self, conn, scope, run_id, session_id):
        """Select one run's correlated sessions from the current snapshot."""
        _, session_ids, correlation = self._correlate_run_sessions(conn, scope, run_id)
        if session_id is not None:
            if session_id not in session_ids:
                raise LedgerError("scope_not_found", "Session is not correlated to this run")
            session_ids = {session_id}
        return _Selection(
            session_ids, correlation, self._repo_run_session_map(conn, scope),
            "session" if session_id is not None else "run",
        )

    def _repo_run_session_map(self, conn, scope):
        """Map every run in this repo to its correlated session IDs."""
        runs = conn.execute(
            "SELECT id FROM runs WHERE repository_id=? ORDER BY id LIMIT ?",
            (scope.repository_id, MAX_RUNS + 1),
        ).fetchall()
        if len(runs) > MAX_RUNS:
            raise LedgerError("resource_limit", "Repository history exceeds the bounded run cap")
        run_session_map: dict[str, set[str]] = {}
        for run in runs:
            _, sids, _ = self._correlate_run_sessions(conn, scope, run["id"])
            run_session_map[run["id"]] = sids
        return run_session_map

    def _expand_workers_selection(self, selection):
        """Add worker descendants and propagate direct + ancestor memberships.

        Uses ONE bounded host enumeration (``list_sessions_metadata``) plus the
        already captured run/session map; it performs no further Ledger reads.
        A worker that directly owns a run keeps that membership and also
        inherits every membership of its ancestors.
        """
        method = getattr(self.reader, "list_sessions_metadata", None)
        if not callable(method):
            raise LedgerError("host_usage_unavailable", "This host reader cannot enumerate requested descendants")
        all_sessions = method()

        children_of: dict[str, list[str]] = {}
        parent_of: dict[str, str] = {}
        for entry in all_sessions:
            parent = entry.get("parent_session_id")
            if parent:
                parent_of[entry["id"]] = parent
                children_of.setdefault(parent, []).append(entry["id"])

        # BFS from in-scope sessions to find all descendants (each visited once).
        expanded = set(selection.session_ids)
        queue = list(selection.session_ids)
        while queue:
            current = queue.pop(0)
            for child_id in children_of.get(current, []):
                if child_id not in expanded:
                    expanded.add(child_id)
                    queue.append(child_id)

        membership_of: dict[str, set[str]] = {}
        for run_id, session_ids in selection.run_session_map.items():
            for session_id in session_ids:
                membership_of.setdefault(session_id, set()).add(run_id)

        workers = expanded - selection.session_ids
        selection.session_ids = expanded
        for session_id in sorted(expanded):
            if session_id in workers:
                selection.correlation.setdefault(session_id, []).append("worker")
            # A worker keeps its direct membership and inherits every
            # membership of its ancestors, so a child owning another run
            # retains both.  The walk is bounded by the visited set.
            inherited: set[str] = set()
            seen: set[str] = set()
            ancestor = parent_of.get(session_id)
            while ancestor is not None and ancestor not in seen:
                seen.add(ancestor)
                inherited |= membership_of.get(ancestor, set())
                ancestor = parent_of.get(ancestor)
            if inherited:
                selection.correlation.setdefault(session_id, []).append("inherited")
                for run_id in inherited:
                    selection.run_session_map.setdefault(run_id, set()).add(session_id)
        return selection

    def _select_session(self, conn, scope, session_id):
        """Find runs in this repository correlated to this session."""
        runs = conn.execute(
            "SELECT id, owner_session FROM runs WHERE repository_id=? ORDER BY id LIMIT ?",
            (scope.repository_id, MAX_RUNS + 1),
        ).fetchall()
        if len(runs) > MAX_RUNS:
            raise LedgerError("resource_limit", "Repository history exceeds the bounded run cap")

        correlated_run_ids, correlation = [], {}
        for run in runs:
            _, members, reasons = self._correlate_run_sessions(conn, scope, run["id"])
            if session_id in members:
                correlated_run_ids.append(run["id"])
                correlation.setdefault(session_id, []).extend(reasons[session_id])
        if not correlated_run_ids:
            raise LedgerError("scope_not_found", "Session is not correlated to any run in this repository")
        return _Selection({session_id}, correlation, self._repo_run_session_map(conn, scope), "session")

    def _select_repository(self, conn, scope):
        """Correlate every run in this repository from the current snapshot."""
        runs = conn.execute(
            "SELECT id FROM runs WHERE repository_id=? ORDER BY id LIMIT ?",
            (scope.repository_id, MAX_RUNS + 1),
        ).fetchall()
        if len(runs) > MAX_RUNS:
            raise LedgerError("resource_limit", "Repository history exceeds the bounded run cap")

        all_session_ids: set[str] = set()
        correlation: dict[str, list[str]] = {}
        run_session_map: dict[str, set[str]] = {}
        for run in runs:
            _, session_ids, corr = self._correlate_run_sessions(conn, scope, run["id"])
            run_session_map[run["id"]] = session_ids
            all_session_ids |= session_ids
            for sid, basis in corr.items():
                correlation.setdefault(sid, []).extend(basis)
        return _Selection(all_session_ids, correlation, run_session_map, "repository")

    # ── report builder ────────────────────────────────────────────────────

    def _auxiliary_plan(self, session_ids, metadata):
        """Count maximal ancestor scopes once, never every overlapping prefix."""
        method = getattr(self.reader, "auxiliary_lineage_ids", None)
        scopes = {}
        for sid in sorted(session_ids):
            if metadata[sid] is None:
                continue
            ids = method(sid) if callable(method) else [sid]
            if (not isinstance(ids, list) or not ids or len(ids) > 100 or sid not in ids
                    or any(not isinstance(i, str) or not i or len(i) > 256 for i in ids)):
                raise LedgerError("host_usage_unavailable", "Host auxiliary scope identity is unavailable")
            scopes[sid] = frozenset(ids)
        unique = set(scopes.values())
        maximal = [key for key in unique if not any(key < other for other in unique)]
        for i, key in enumerate(maximal):
            if any(key & other for other in maximal[i + 1:]):
                raise LedgerError("host_usage_unavailable", "Non-nested auxiliary scopes cannot be counted exactly")
        owners = {key: min(sid for sid, value in scopes.items() if value == key) for key in maximal}
        representatives = {sid: owners[next(key for key in maximal if value <= key)]
                           for sid, value in scopes.items()}
        return representatives, scopes

    def _build_report(self, scope, selection, include_workers):
        """Build the final report from the captured selection (host reads only)."""
        session_ids = selection.session_ids
        correlation = selection.correlation
        run_session_map = selection.run_session_map
        scope_kind = selection.scope_kind
        if len(session_ids) > MAX_SESSIONS:
            raise LedgerError("resource_limit", "Selected sessions exceed the bounded session cap")
        sessions = []
        missing_ids = []
        shared_sessions = []

        # Track which sessions are shared across multiple runs in this snapshot.
        session_run_count: dict[str, list[str]] = {}
        for rid, sids in run_session_map.items():
            for sid in sids:
                session_run_count.setdefault(sid, []).append(rid)

        metadata_by_session = {sid: self.reader.get_session(sid) for sid in sorted(session_ids)}
        auxiliary_representatives, auxiliary_scopes = self._auxiliary_plan(session_ids, metadata_by_session)

        total_main = {f: 0 for f in _TOKEN_COUNTER_FIELDS}
        total_main["api_call_count"] = 0
        total_aux = {f: 0 for f in _AUX_TOTAL_FIELDS}
        all_complete = True
        has_missing = False

        for sid in sorted(session_ids):
            correlated_runs = session_run_count.get(sid, [])
            is_shared = len(correlated_runs) > 1

            metadata = metadata_by_session[sid]
            if metadata is None:
                missing_ids.append(sid)
                all_complete = False
                has_missing = True
                sessions.append({
                    "session_id": sid,
                    "correlated_runs": correlated_runs,
                    "correlation_basis": correlation.get(sid, []),
                    "main": {f: None for f in (*_TOKEN_COUNTER_FIELDS, "api_call_count", "prompt_tokens", "total_tokens")},
                    "main_coverage": "missing",
                    "auxiliary": {
                        "tasks": {},
                        "totals": {f: None for f in (*_AUX_TOTAL_FIELDS, "prompt_tokens", "total_tokens")},
                        "coverage": "missing",
                    },
                    "cost": {"estimated_usd": None, "actual_usd": None, "status": "unknown"},
                    "attribution": "whole_session_non_marginal",
                    "shared_session": is_shared,
                })
                continue

            main = _canonical_main(metadata, sid)

            # Auxiliary lineage dedup
            aux_tasks = {}
            aux_coverage = "complete"
            aux_totals = {f: None for f in _AUX_TOTAL_FIELDS}
            if auxiliary_representatives[sid] != sid:
                aux_coverage = "already_counted"
            else:
                raw_aux = self.reader.auxiliary_usage_by_task(sid)
                aux = _canonical_auxiliary(raw_aux, sid)
                aux_tasks, aux_coverage, aux_totals = aux["tasks"], aux["coverage"], aux["totals"]

            cost = _cost_view(metadata, sid)

            if main["coverage"] != "complete":
                all_complete = False
            if aux_coverage not in ("complete", "already_counted"):
                all_complete = False

            for f in (*_TOKEN_COUNTER_FIELDS, "api_call_count"):
                total_main[f] = _safe_add(total_main[f], main[f], f"main.{f}", sid)
            if aux_coverage != "already_counted":
                for f in _AUX_TOKEN_FIELDS:
                    total_aux[f] = _safe_add(total_aux[f], aux_totals.get(f), f"auxiliary.{f}", sid)

            sessions.append({
                "session_id": sid,
                "correlated_runs": correlated_runs,
                "correlation_basis": correlation.get(sid, []),
                "main": main,
                "auxiliary": {
                    "tasks": aux_tasks,
                    "totals": aux_totals if aux_coverage != "already_counted"
                    else {f: None for f in (*_AUX_TOTAL_FIELDS, "prompt_tokens", "total_tokens")},
                    "coverage": aux_coverage,
                    "scope_session_ids": sorted(auxiliary_scopes[sid]),
                    "counted_with_session": auxiliary_representatives[sid],
                },
                "cost": cost,
                "attribution": "whole_session_non_marginal",
                "shared_session": is_shared,
            })

        # Compute combined totals
        # If any session is missing, totals are unknown (None)
        if has_missing:
            combined = {f: None for f in (*_TOKEN_COUNTER_FIELDS, "api_call_count", "prompt_tokens", "total_tokens")}
            main_totals = {f: None for f in (*_TOKEN_COUNTER_FIELDS, "api_call_count", "prompt_tokens", "total_tokens")}
            aux_totals_out = {f: None for f in (*_AUX_TOTAL_FIELDS, "prompt_tokens", "total_tokens")}
        else:
            combined = {f: _safe_add(total_main[f], total_aux[f], f"combined.{f}", "aggregate")
                        for f in _TOKEN_COUNTER_FIELDS}
            combined_prompt = None
            if all(combined[f] is not None for f in ("input_tokens", "cache_read_tokens", "cache_write_tokens")):
                combined_prompt = _safe_add(
                    _safe_add(combined["input_tokens"], combined["cache_read_tokens"], "combined.prompt_tokens", "aggregate"),
                    combined["cache_write_tokens"], "combined.prompt_tokens", "aggregate"
                )
            combined_total = None
            if combined_prompt is not None and combined["output_tokens"] is not None:
                combined_total = _safe_add(combined_prompt, combined["output_tokens"], "combined.total_tokens", "aggregate")
            combined["prompt_tokens"] = combined_prompt
            combined["total_tokens"] = combined_total
            combined["api_call_count"] = _safe_add(total_main["api_call_count"], total_aux["api_call_count"], "combined.api_call_count", "aggregate")

            main_prompt = _safe_add(
                _safe_add(total_main["input_tokens"], total_main["cache_read_tokens"], "main.prompt_tokens", "aggregate"),
                total_main["cache_write_tokens"], "main.prompt_tokens", "aggregate"
            )
            main_total = _safe_add(main_prompt, total_main["output_tokens"], "main.total_tokens", "aggregate")
            main_totals = {
                **total_main,
                "prompt_tokens": main_prompt,
                "total_tokens": main_total,
            }

            aux_prompt = _safe_add(
                _safe_add(total_aux["input_tokens"], total_aux["cache_read_tokens"], "auxiliary.prompt_tokens", "aggregate"),
                total_aux["cache_write_tokens"], "auxiliary.prompt_tokens", "aggregate"
            )
            aux_total = _safe_add(aux_prompt, total_aux["output_tokens"], "auxiliary.total_tokens", "aggregate")
            aux_totals_out = {
                **total_aux,
                "prompt_tokens": aux_prompt,
                "total_tokens": aux_total,
            }

        for sid in sorted(session_ids):
            runs = session_run_count.get(sid, [])
            if len(runs) > 1:
                shared_sessions.append({"session_id": sid, "run_ids": sorted(runs)})

        coverage = "complete" if all_complete and not missing_ids else ("partial" if sessions else "missing")

        return {
            "schema_version": 1,
            "repository": scope.repository_name,
            "scope": scope_kind,
            "sessions": sessions,
            "totals": {
                "main": main_totals,
                "auxiliary": aux_totals_out,
                "combined": combined,
                "session_count": len(session_ids),
                "missing_session_count": len(missing_ids),
            },
            "shared_sessions": shared_sessions,
            "missing_session_ids": sorted(missing_ids),
            "coverage": coverage,
            "observed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "limitations": [
                "Whole-session totals may include other work from the same session.",
                "Per-tool or precise per-run marginal tokens are unavailable.",
                "Per-session cost is the session-row cost and excludes auxiliary estimates.",
            ] + (
                ["Worker descendants are not included in this report."]
                if not include_workers else []
            ),
        }


# ── Human formatter ────────────────────────────────────────────────────────

def format_report_human(report):
    """Format a report as human-readable text.

    Never emits credential-bearing URLs, base_url, raw errors, config, or
    prompts.  Token counts and cost status are shown without pricing tokens
    or inferring quota/savings.
    """
    lines = []
    lines.append(f"Token report for {report['repository']}")
    lines.append(f"Scope: {report['scope']}; coverage: {report['coverage']}")
    for name in ("main", "auxiliary", "combined"):
        totals = report["totals"][name]
        lines.append(f"{name}: uncached={totals['input_tokens']} cache_read={totals['cache_read_tokens']} "
                     f"cache_write={totals['cache_write_tokens']} output={totals['output_tokens']} "
                     f"prompt={totals['prompt_tokens']} total={totals['total_tokens']} api_calls={totals['api_call_count']}")
    lines.append(f"Sessions: {report['totals']['session_count']}"
                 f" (missing: {report['totals']['missing_session_count']})")

    for session in report["sessions"]:
        sid = session["session_id"]
        lines.append(f"\n  Session {sid}")
        lines.append(f"    Attribution: {session['attribution']}")
        lines.append(f"    Correlated runs: {', '.join(session['correlated_runs']) or 'none'}")
        if session["shared_session"]:
            lines.append("    SHARED: this session is correlated to multiple runs")

        main = session["main"]
        if session.get("main_coverage") == "missing":
            lines.append("    Main usage: MISSING (host metadata not found)")
        else:
            lines.append(f"    Main: prompt={main['prompt_tokens']} output={main['output_tokens']} "
                         f"total={main['total_tokens']} reasoning={main['reasoning_tokens']} "
                         f"api_calls={main['api_call_count']} [{main['coverage']}]")

        aux = session["auxiliary"]
        aux_coverage = aux.get("coverage", "complete")
        if aux_coverage == "missing":
            lines.append("    Auxiliary: MISSING (host metadata not found)")
        elif aux_coverage == "already_counted":
            counted_with = aux.get("counted_with_session", "unknown")
            lines.append(f"    Auxiliary: already counted with session {counted_with}")
        elif aux["tasks"]:
            lines.append(f"    Auxiliary tasks: {len(aux['tasks'])} [{aux_coverage}]")
            for task_name, task in aux["tasks"].items():
                lines.append(f"      {task_name}: total={task['total_tokens']} api_calls={task['api_call_count']}")
        else:
            lines.append("    Auxiliary: none")

        cost = session["cost"]
        if cost["status"] == "unknown":
            lines.append("    Cost: unknown")
        else:
            lines.append(f"    Cost: {cost['status']}"
                         + (f" ${cost['actual_usd']:.6f}" if cost["actual_usd"] is not None else "")
                         + (f" (est ${cost['estimated_usd']:.6f})" if cost["estimated_usd"] is not None else ""))

    if report["missing_session_ids"]:
        lines.append(f"\n  Missing session IDs: {', '.join(report['missing_session_ids'])}")
    if report["shared_sessions"]:
        lines.append(f"\n  Shared sessions: {len(report['shared_sessions'])}")
        for shared in report["shared_sessions"]:
            lines.append(f"    {shared['session_id']}: {', '.join(shared['run_ids'])}")

    lines.append("\n  Limitations:")
    for lim in report["limitations"]:
        lines.append(f"    - {lim}")

    return "\n".join(lines)
