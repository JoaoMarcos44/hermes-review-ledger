"""Outcome-linked, operator-controlled revisions of conditional lessons.

Diagnostics ask for review, never invent semantic changes. Reported feedback is
not independent verification or an automatic ranking signal.
"""
from __future__ import annotations

import json

from .learning import Learning
from .models import Actor, ELIGIBLE_OUTCOMES, LedgerError, Scope, canonical, fields, integer, strings, text
from .storage import Store, new_id, now

CHANGE_FIELDS = frozenset({"conditions", "exclusions", "tags", "symbols", "verification"})


class Improvements:
    def __init__(self, store: Store, *, automation_mode: str = "manual"):
        self.store = store
        self.learning = Learning(store, automation_mode=automation_mode)

    @staticmethod
    def _outcome(conn, scope, outcome_id, target_version_id):
        row = conn.execute("SELECT * FROM lesson_uses WHERE repository_id=? AND id=? AND version_id=?",
                           (scope.repository_id, outcome_id, target_version_id)).fetchone()
        if row is None or row["usefulness"] is None:
            raise LedgerError("ineligible_outcome", "An exact recorded outcome for the target version is required")
        # A historical use may remain relevant after retirement, but its evidence
        # must still be valid. Retirement itself does not declare evidence false.
        source_rows = conn.execute("""SELECT o.valid,o.outcome FROM lesson_sources s
            JOIN observations o ON o.repository_id=s.repository_id AND o.id=s.observation_id
            WHERE s.repository_id=? AND s.version_id=? LIMIT 21""",
                                   (scope.repository_id, target_version_id)).fetchall()
        if not source_rows or len(source_rows) > 20 or any(not r["valid"] or r["outcome"] not in ELIGIBLE_OUTCOMES for r in source_rows):
            raise LedgerError("ineligible_source", "Outcome guidance has invalid supporting evidence")
        for ident in json.loads(row["supporting_observation_ids_json"]):
            obs = conn.execute("SELECT valid FROM observations WHERE repository_id=? AND id=?",
                               (scope.repository_id, ident)).fetchone()
            if obs is None or not obs["valid"]:
                raise LedgerError("ineligible_source", "An outcome support observation was invalidated")
        return row

    @staticmethod
    def record_outcome(conn, scope: Scope, run_id: str, outcome_id: str):
        """Called inside the original result transaction, once per immutable use."""
        outcome = conn.execute("SELECT * FROM lesson_uses WHERE repository_id=? AND id=?",
                               (scope.repository_id, outcome_id)).fetchone()
        version_id = outcome["version_id"]
        rows = conn.execute("""SELECT u.usefulness,u.contribution,u.feedback_applicability,r.review_id
            FROM lesson_uses u JOIN runs r ON r.repository_id=u.repository_id AND r.id=u.run_id
            WHERE u.repository_id=? AND u.version_id=? AND u.usefulness IS NOT NULL
            ORDER BY u.updated_at DESC,u.id LIMIT 201""", (scope.repository_id, version_id)).fetchall()
        window = rows[:200]
        summary = {"window_size": len(window), "window_limit": 200, "older_outcomes_omitted": len(rows) > 200,
                   "reported_useful": sum(r["usefulness"] == "useful" for r in window),
                   "reported_redundant": sum(r["contribution"] == "redundant" for r in window),
                   "reported_inapplicable": sum(r["feedback_applicability"] == "inapplicable" for r in window),
                   "distinct_review_count": len({r["review_id"] for r in window}),
                   "independent_support": None,
                   "notice": "Historical agent reports, grouped by PR; related PR families are not known to be independent. No ranking boost."}
        conn.execute("""INSERT INTO improvement_aggregates VALUES (?,?,?,?)
            ON CONFLICT(repository_id,version_id) DO UPDATE SET summary_json=excluded.summary_json,updated_at=excluded.updated_at""",
                     (scope.repository_id, version_id, canonical(summary), now()))
        reason = None
        if outcome["feedback_applicability"] == "inapplicable" or outcome["applicability"] == "not_applicable":
            reason = "Reported inapplicable retrieval: review explicit conditions and exclusions."
        elif outcome["contribution"] == "redundant":
            reason = "Reported redundant guidance: review applicability and investigation order."
        # Inconclusive execution, a refutation, or praise alone does not diagnose
        # a lesson defect. No semantic condition is inferred from free text.
        if reason is None or not Learning.eligible(conn, scope, version_id):
            return None
        try:
            Improvements._outcome(conn, scope, outcome_id, version_id)
        except LedgerError:
            return None
        old = conn.execute("SELECT id FROM improvement_proposals WHERE detector_outcome_id=?", (outcome_id,)).fetchone()
        if old:
            return old["id"]
        ident = new_id("improvement")
        conn.execute("""INSERT INTO improvement_proposals
            (id,repository_id,run_id,target_version_id,detector_outcome_id,status,changes_json,reason,
             expected_benefit,evaluation_references_json,created_at) VALUES (?,?,?,?,?,'review_needed','{}',?,?,'[]',?)""",
                     (ident, scope.repository_id, run_id, version_id, outcome_id, reason,
                      "Hypothesis: a concrete scoped revision may reduce irrelevant or redundant retrieval; evaluation is required.", now()))
        conn.execute("INSERT INTO improvement_outcomes VALUES (?,?,?)", (scope.repository_id, ident, outcome_id))
        return ident

    @staticmethod
    def _validate(conn, scope, proposal, *, require_current_target=True):
        target = Learning.version(conn, scope, proposal["target_version_id"])
        if require_current_target and not target["eligible_now"]:
            raise LedgerError("stale_improvement", "The exact target is no longer active and eligible; propose a fresh revision")
        outcomes = conn.execute("SELECT outcome_id FROM improvement_outcomes WHERE repository_id=? AND improvement_id=? LIMIT 21",
                                (scope.repository_id, proposal["id"])).fetchall()
        if not outcomes or len(outcomes) > 20:
            raise LedgerError("ineligible_outcome", "Improvement requires 1..20 source outcomes")
        for outcome in outcomes:
            Improvements._outcome(conn, scope, outcome["outcome_id"], target["id"])
        for ident in json.loads(proposal["evaluation_references_json"]):
            obs = conn.execute("SELECT valid,outcome FROM observations WHERE repository_id=? AND id=?", (scope.repository_id, ident)).fetchone()
            if obs is None or not obs["valid"] or obs["outcome"] not in ELIGIBLE_OUTCOMES:
                raise LedgerError("ineligible_source", "Evaluation evidence is no longer eligible")
        return target

    @staticmethod
    def validate_approval(conn, scope, version_id):
        proposal = conn.execute("SELECT * FROM improvement_proposals WHERE repository_id=? AND candidate_version_id=?",
                                (scope.repository_id, version_id)).fetchone()
        if proposal is None:
            return
        if proposal["status"] != "proposed":
            raise LedgerError("invalid_transition", "Only an unapplied concrete improvement can be approved")
        target = Improvements._validate(conn, scope, proposal)
        candidate = Learning.version(conn, scope, version_id)
        changes = json.loads(proposal["changes_json"])
        if (candidate["previous_id"] != target["id"] or candidate["question"] != target["question"]
                or candidate["critic_assessment_ids"] != target["critic_assessment_ids"]):
            raise LedgerError("stale_improvement", "Candidate does not match the exact approved proposal")
        for field in CHANGE_FIELDS:
            expected = changes[field]["after"] if field in changes else target[field]
            if candidate[field] != expected or (field in changes and changes[field]["before"] != target[field]):
                raise LedgerError("stale_improvement", "Candidate fields differ from the inspectable proposal")

    @staticmethod
    def record_operator(conn, scope, version_id, action):
        if action == "approve":
            conn.execute("UPDATE improvement_proposals SET status='applied' WHERE repository_id=? AND candidate_version_id=? AND status='proposed'",
                         (scope.repository_id, version_id))
        elif action in {"suspend", "restrict"}:
            conn.execute("UPDATE improvement_proposals SET status='rejected' WHERE repository_id=? AND candidate_version_id=? AND status='proposed'",
                         (scope.repository_id, version_id))

    def propose(self, scope: Scope, run_id: str, actor: Actor, generation: int, data: dict, request_key: str):
        fields(data, {"target_version_id", "source_outcome_ids", "changes", "reason", "expected_benefit", "evaluation_references"},
               {"target_version_id", "source_outcome_ids", "changes", "reason", "expected_benefit"})
        target_id = text(data["target_version_id"], "target_version_id", 64)
        outcomes = strings(data["source_outcome_ids"], "source_outcome_ids", 20, 64)
        if not outcomes or len(outcomes) != len(data["source_outcome_ids"]):
            raise LedgerError("invalid_input", "Provide 1..20 distinct recorded source outcome IDs")
        changes = data["changes"]
        fields(changes, CHANGE_FIELDS)
        if not changes:
            raise LedgerError("invalid_input", "A concrete improvement needs at least one allowed field change")
        reason = text(data["reason"], "reason", 4000)
        benefit = text(data["expected_benefit"], "expected_benefit", 2000)
        evaluations = strings(data.get("evaluation_references", []), "evaluation_references", 20, 64)
        if len(evaluations) != len(data.get("evaluation_references", [])):
            raise LedgerError("invalid_input", "Duplicate evaluation references are not allowed")

        def write(conn):
            self.store.owner(conn, scope, run_id, actor, generation)
            target = Learning.version(conn, scope, target_id)
            if not target["eligible_now"]:
                raise LedgerError("stale_improvement", "The target must be the exact active eligible version")
            supports = {s["observation_id"]: s["relation"] for s in target["sources"]}
            for ident in outcomes:
                outcome = self._outcome(conn, scope, ident, target_id)
                for observation_id in json.loads(outcome["supporting_observation_ids_json"]):
                    supports.setdefault(observation_id, "supports")
            for ident in evaluations:
                supports.setdefault(ident, "supports")
            # All dependencies become lesson sources so ordinary recall, use,
            # manifests and exports inherit the same source invalidation fence.
            lesson_data = {key: target[key] for key in ("question", *CHANGE_FIELDS)}
            lesson_data.update(changes)
            lesson_data["previous_version_id"] = target_id
            # A procedural revision must retain the exact adjudications whose
            # freshness gates the target, including across review-run reuse.
            lesson_data["critic_assessment_ids"] = target["critic_assessment_ids"]
            lesson_data["sources"] = [{"observation_id": ident, "relation": relation} for ident, relation in sorted(supports.items())]
            # Reuse all existing lesson validation and candidate lifecycle inside
            # this writer transaction; only the outer operation writes a receipt.
            candidate = self.learning.propose(scope, run_id, actor, generation, lesson_data, request_key, _conn=conn)
            normalized = Learning.version(conn, scope, candidate["version_id"])
            diff = {key: {"before": target[key], "after": normalized[key]} for key in changes if target[key] != normalized[key]}
            if not diff:
                raise LedgerError("invalid_input", "Proposal does not change any allowed field")
            ident = new_id("improvement")
            conn.execute("""INSERT INTO improvement_proposals
                (id,repository_id,run_id,target_version_id,candidate_version_id,status,changes_json,reason,
                 expected_benefit,evaluation_references_json,created_at) VALUES (?,?,?,?,?,'proposed',?,?,?,?,?)""",
                         (ident, scope.repository_id, run_id, target_id, candidate["version_id"], canonical(diff), reason,
                          benefit, canonical(evaluations), now()))
            for outcome_id in outcomes:
                conn.execute("INSERT INTO improvement_outcomes VALUES (?,?,?)", (scope.repository_id, ident, outcome_id))
            self.store.audit(conn, scope, ident, "improvement_proposed", actor.session_id, {"candidate_version_id": candidate["version_id"]})
            candidate.update(self.learning._automate(conn, scope, run_id, candidate["version_id"]))
            return {**candidate, "improvement_id": ident, "changes": diff, "expected_benefit_is_hypothesis": True}
        return self.store.write(scope, "improvement_propose", run_id, request_key,
                                {"actor": actor.session_id, "generation": generation, "data": data}, write)

    def inspect(self, scope: Scope, improvement_id: str):
        text(improvement_id, "improvement_id", 64)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.repository(conn, scope)
            row = conn.execute("SELECT * FROM improvement_proposals WHERE repository_id=? AND id=?", (scope.repository_id, improvement_id)).fetchone()
            if row is None:
                raise LedgerError("scope_not_found", "Improvement is not in this repository")
            item = dict(row)
            item["changes"] = json.loads(item.pop("changes_json"))
            item["evaluation_references"] = json.loads(item.pop("evaluation_references_json"))
            item["source_outcome_ids"] = [r[0] for r in conn.execute("SELECT outcome_id FROM improvement_outcomes WHERE improvement_id=? ORDER BY outcome_id LIMIT 20", (improvement_id,))]
            try:
                self._validate(conn, scope, row, require_current_target=item["status"] != "applied")
                item["eligible_now"] = item["status"] != "rejected"
                if item["status"] == "applied":
                    item["eligible_now"] = Learning.eligible(conn, scope, item["candidate_version_id"])
            except LedgerError as exc:
                item["eligible_now"], item["ineligible_reason"] = False, exc.code
            summary = conn.execute("SELECT summary_json FROM improvement_aggregates WHERE repository_id=? AND version_id=?",
                                   (scope.repository_id, item["target_version_id"])).fetchone()
            item["aggregate"] = json.loads(summary[0]) if summary else None
            item["notice"] = "Controlled procedural improvement; expected benefit is a hypothesis, feedback is agent-reported."
            return item

    def list(self, scope: Scope, run_id: str, *, limit: int = 20):
        integer(limit, "limit", 1, 100)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.run(conn, scope, run_id)
            rows = conn.execute("""SELECT *
                FROM improvement_proposals WHERE repository_id=? AND run_id=? ORDER BY created_at,id LIMIT ?""",
                                (scope.repository_id, run_id, limit + 1)).fetchall()
            items = []
            for row in rows[:limit]:
                item = {key: row[key] for key in ("id", "target_version_id", "candidate_version_id", "status", "reason", "created_at")}
                try:
                    self._validate(conn, scope, row, require_current_target=row["status"] != "applied")
                    item["eligible_now"] = row["status"] != "rejected"
                    if row["status"] == "applied":
                        item["eligible_now"] = Learning.eligible(conn, scope, row["candidate_version_id"])
                except LedgerError as exc:
                    item["eligible_now"], item["ineligible_reason"] = False, exc.code
                items.append(item)
            return {"improvements": items, "more": len(rows) > limit}
