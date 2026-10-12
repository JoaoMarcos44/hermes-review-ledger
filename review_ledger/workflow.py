"""Bounded read-only workflow view for closed-loop learning diagnostics.

Surfaces pending improvements and lesson uses across runs in the same repository,
with explicit actionable metadata. Does not author or approve improvements automatically.
"""
from __future__ import annotations

import json

from .improvements import Improvements
from .learning import Learning
from .models import LedgerError, Scope, integer, text


class WorkflowView:
    """Read-only cross-run diagnostic queue for repository-scoped learning feedback."""

    def __init__(self, store):
        self.store = store
        self.improvements = Improvements(store)
        self.learning = Learning(store)

    def summary(self, scope: Scope, *, run_id: str | None = None, limit: int = 5,
                offset: int = 0, conn=None) -> dict:
        """Compact repository-scoped summary of pending learning feedback.

        When run_id is supplied, pending lesson feedback is scoped to that exact
        run; the improvement queue remains repository-scoped and can include
        prior runs. When conn is supplied, the caller's read transaction is
        reused; otherwise a standalone read transaction is opened.
        """
        integer(limit, "limit", 1, 100)
        integer(offset, "offset", 0, 1_000_000)
        if conn is not None:
            self.store.repository(conn, scope)
            if run_id is not None:
                self.store.run(conn, scope, run_id)
            return self._summary(conn, scope, run_id, limit, offset)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.repository(conn, scope)
            if run_id is not None:
                self.store.run(conn, scope, run_id)
            return self._summary(conn, scope, run_id, limit, offset)

    def _summary(self, conn, scope, run_id, limit, offset):
        # True total count of pending improvements (not just the first page).
        total = conn.execute("""
            SELECT COUNT(*) FROM improvement_proposals
            WHERE repository_id=? AND status IN ('review_needed','proposed')
        """, (scope.repository_id,)).fetchone()[0]
        # Bounded first page of pending improvements with full validator columns.
        improvement_rows = conn.execute("""
            SELECT * FROM improvement_proposals
            WHERE repository_id=? AND status IN ('review_needed','proposed')
            ORDER BY created_at,id LIMIT ? OFFSET ?
        """, (scope.repository_id, limit, offset)).fetchall()
        improvements = []
        for row in improvement_rows:
            row_dict = dict(row)
            item = {
                "improvement_id": row_dict["id"],
                "target_version_id": row_dict["target_version_id"],
                "run_id": row_dict["run_id"],
                "status": row_dict["status"],
                "state": "not_loaded",
                "detail": {"tool": "ledger_status", "detail_collection": "workflow",
                           "detail_id": row_dict["id"]},
                "next_step": ("evaluate_exact_candidate_then_operator_review" if row_dict["status"] == "proposed"
                              else "investigate_diagnosis_then_propose_concrete_improvement"),
            }
            # Include exact source outcome IDs (bounded).
            outcome_ids = [r[0] for r in conn.execute(
                "SELECT outcome_id FROM improvement_outcomes WHERE repository_id=? AND improvement_id=? ORDER BY outcome_id LIMIT 21",
                (scope.repository_id, row_dict["id"]))]
            item["source_outcome_ids"] = outcome_ids[:1]
            item["source_outcomes_omitted"] = len(outcome_ids) > 1
            # Include eligibility check under the same snapshot.
            try:
                self.improvements._validate(conn, scope, row_dict, require_current_target=True)
                item["eligible_now"] = True
            except LedgerError as exc:
                item["eligible_now"] = False
                item["ineligible_reason"] = exc.code
            improvements.append(item)
        # Pending lesson uses: scoped to run_id when supplied.
        if run_id is not None:
            pending_uses = conn.execute("""
                SELECT COUNT(*) FROM lesson_uses
                WHERE repository_id=? AND run_id=? AND applicability IN ('applicable', 'uncertain')
                AND usefulness IS NULL
            """, (scope.repository_id, run_id)).fetchone()[0]
        else:
            pending_uses = conn.execute("""
                SELECT COUNT(*) FROM lesson_uses
                WHERE repository_id=? AND applicability IN ('applicable', 'uncertain')
                AND usefulness IS NULL
            """, (scope.repository_id,)).fetchone()[0]
        # Total active lessons.
        active_lessons = conn.execute("""
            SELECT COUNT(*) FROM lesson_versions WHERE repository_id=? AND state='active'
        """, (scope.repository_id,)).fetchone()[0]
        return {
            "state": "ok",
            "pending_improvements_count": total,
            "improvements": improvements,
            "pending_use_count": pending_uses,
            "active_lesson_count": active_lessons,
            "improvements_omitted": offset + len(improvements) < total,
            "queue_offset": offset,
            "queue_next_offset": offset + len(improvements) if offset + len(improvements) < total else None,
            "pending_use_scope": "run" if run_id is not None else "repository",
            "run_id": run_id,
            "next_step": "Review each proposal and record explicit outcomes; no improvement is automatically authored or approved.",
            "notice": "Diagnostics only; no improvement is automatically authored or approved. "
                      "Review each proposal and record explicit outcomes.",
        }

    def detail(self, scope: Scope, improvement_id: str, *, conn=None,
               limit: int = 20, offset: int = 0) -> dict:
        """Detailed view of a specific improvement with actionable metadata.

        Returns state=workflow_detail and exact identity. Preserves the target's
        limits/exclusions/provenance as untrusted data. Delegates eligibility
        and snapshot parsing to existing helpers on the same connection.
        """
        integer(limit, "limit", 1, 100)
        integer(offset, "offset", 0, 1_000_000)
        if conn is not None:
            self.store.repository(conn, scope)
            return self._detail(conn, scope, improvement_id, limit, offset)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.repository(conn, scope)
            return self._detail(conn, scope, improvement_id, limit, offset)

    def _detail(self, conn, scope, improvement_id, limit, offset):
        text(improvement_id, "improvement_id", 64)
        row = conn.execute("""
            SELECT * FROM improvement_proposals WHERE repository_id=? AND id=?
        """, (scope.repository_id, improvement_id)).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "Improvement is not in this repository")
        row_dict = dict(row)
        item = {
            "state": "workflow_detail",
            "improvement_id": row_dict["id"],
            "target_version_id": row_dict["target_version_id"],
            "candidate_version_id": row_dict["candidate_version_id"],
            "run_id": row_dict["run_id"],
            "status": row_dict["status"],
            "reason": row_dict["reason"],
            "changes": json.loads(row_dict["changes_json"]),
            "evaluation_references": json.loads(row_dict["evaluation_references_json"]),
        }
        # Exact source outcome IDs with truthful limit/offset pagination.
        total_outcomes = conn.execute("""
            SELECT COUNT(*) FROM improvement_outcomes WHERE repository_id=? AND improvement_id=?
        """, (scope.repository_id, improvement_id)).fetchone()[0]
        source_rows = conn.execute("""
            SELECT outcome_id FROM improvement_outcomes WHERE repository_id=? AND improvement_id=?
            ORDER BY outcome_id LIMIT ? OFFSET ?
        """, (scope.repository_id, improvement_id, limit, offset)).fetchall()
        item["source_outcome_ids"] = [r[0] for r in source_rows]
        item["omitted"] = {
            "source_outcomes": max(0, total_outcomes - offset - len(source_rows)),
            "offset": offset,
            "limit": limit,
        }
        # Load the exact repository-scoped target independently of eligibility
        # validation; history is readable context, never approval authority.
        try:
            target = Learning.version(conn, scope, row_dict["target_version_id"])
        except LedgerError:
            target = None
        if target is not None:
            item["target"] = {
                "version_id": target["id"],
                "question": target["question"],
                "state": target["state"],
                "eligible_now": target["eligible_now"],
                "conditions": target["conditions"],
                "exclusions": target["exclusions"],
                "verification": target["verification"],
                "tags": target["tags"],
                "symbols": target["symbols"],
                "sources": target["sources"],
            }
        # Validate and include eligibility under the same snapshot.
        try:
            self.improvements._validate(conn, scope, row_dict, require_current_target=row_dict["status"] != "applied")
            item["eligible_now"] = (Learning.eligible(conn, scope, row_dict["candidate_version_id"])
                                    if row_dict["status"] == "applied" else row_dict["status"] != "rejected")
            item["actionable_now"] = item["eligible_now"] and row_dict["status"] in ("review_needed", "proposed")
        except LedgerError as exc:
            item["eligible_now"] = False
            item["ineligible_reason"] = exc.code
            item["actionable_now"] = False
        # Include aggregate feedback summary.
        summary = conn.execute("""
            SELECT summary_json FROM improvement_aggregates WHERE repository_id=? AND version_id=?
        """, (scope.repository_id, item["target_version_id"])).fetchone()
        item["aggregate"] = json.loads(summary[0]) if summary else None
        item["notice"] = "Controlled procedural improvement; expected benefit is a hypothesis."
        return item
