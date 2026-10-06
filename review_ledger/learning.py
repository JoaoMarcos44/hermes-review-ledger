"""Versioned, explicitly approved conditional investigation strategies."""
from __future__ import annotations

import json

from .models import (Actor, ELIGIBLE_OUTCOMES, LedgerError, Scope, canonical, choice,
                     fields, integer, strings, text)
from .storage import Store, new_id, now


class Learning:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def eligible(conn, scope: Scope, version_id: str) -> bool:
        lesson = conn.execute("SELECT state FROM lesson_versions WHERE repository_id=? AND id=?", (scope.repository_id, version_id)).fetchone()
        if lesson is None or lesson["state"] != "active":
            return False
        sources = conn.execute("""SELECT o.valid,o.outcome FROM lesson_sources s JOIN observations o ON o.id=s.observation_id
            WHERE s.repository_id=? AND s.version_id=? LIMIT 21""", (scope.repository_id, version_id)).fetchall()
        return bool(sources) and len(sources) <= 20 and all(s["valid"] and s["outcome"] in ELIGIBLE_OUTCOMES for s in sources)

    @staticmethod
    def version(conn, scope: Scope, version_id: str) -> dict:
        row = conn.execute("SELECT * FROM lesson_versions WHERE repository_id=? AND id=?", (scope.repository_id, version_id)).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "Lesson version is not in this repository and profile")
        item = dict(row)
        for key in ("conditions", "exclusions", "tags", "symbols"):
            item[key] = json.loads(item.pop(key + "_json"))
        item["sources"] = [dict(r) for r in conn.execute("""SELECT s.observation_id,s.relation,o.run_id,o.provenance,o.valid,o.outcome
            FROM lesson_sources s JOIN observations o ON o.id=s.observation_id WHERE s.repository_id=? AND s.version_id=? ORDER BY s.observation_id LIMIT 20""", (scope.repository_id, version_id))]
        item["eligible_now"] = Learning.eligible(conn, scope, version_id)
        return item

    def propose(self, scope: Scope, run_id: str, actor: Actor, generation: int, data: dict, request_key: str):
        fields(data, {"question", "conditions", "exclusions", "verification", "tags", "symbols", "sources", "previous_version_id"},
               {"question", "conditions", "exclusions", "verification", "sources"})
        question = text(data["question"], "question", 1500)
        conditions = strings(data["conditions"], "conditions", 10, 500)
        exclusions = strings(data["exclusions"], "exclusions", 10, 500)
        if not conditions:
            raise LedgerError("invalid_input", "A conditional lesson needs at least one application condition")
        verification = text(data["verification"], "verification", 2000)
        tags = strings(data.get("tags", []), "tags", 20, 80)
        symbols = strings(data.get("symbols", []), "symbols", 20, 200)
        if not isinstance(data["sources"], list) or not 1 <= len(data["sources"]) <= 20:
            raise LedgerError("invalid_input", "A lesson requires 1..20 explicit source observations")
        sources = []
        for source in data["sources"]:
            fields(source, {"observation_id", "relation"}, {"observation_id", "relation"})
            sources.append((text(source["observation_id"], "observation_id", 64), choice(source["relation"], "relation", {"supports", "contradicts"})))
        if len({s[0] for s in sources}) != len(sources):
            raise LedgerError("invalid_input", "Duplicate lesson sources are not allowed")

        def write(conn):
            self.store.owner(conn, scope, run_id, actor, generation)
            for ident, _ in sources:
                obs = conn.execute("SELECT valid,outcome FROM observations WHERE repository_id=? AND id=?", (scope.repository_id, ident)).fetchone()
                if obs is None or not obs["valid"] or obs["outcome"] not in ELIGIBLE_OUTCOMES:
                    raise LedgerError("ineligible_source", "Lesson sources must be eligible observations in this repository")
            previous = data.get("previous_version_id")
            if previous:
                old = self.version(conn, scope, previous)
                lesson_id = old["lesson_id"]
                version = conn.execute("SELECT MAX(version)+1 FROM lesson_versions WHERE lesson_id=?", (lesson_id,)).fetchone()[0]
            else:
                lesson_id, version = new_id("lesson"), 1
            ident = new_id("version")
            conn.execute("""INSERT INTO lesson_versions
                (id,repository_id,lesson_id,version,previous_id,question,conditions_json,exclusions_json,verification,tags_json,symbols_json,state,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'candidate',?)""",
                         (ident, scope.repository_id, lesson_id, version, previous, question, canonical(conditions), canonical(exclusions), verification, canonical(tags), canonical(symbols), now()))
            for source_id, relation in sources:
                conn.execute("INSERT INTO lesson_sources VALUES (?,?,?,?)", (scope.repository_id, ident, source_id, relation))
            self.store.audit(conn, scope, ident, "proposed", actor.session_id, {"run_id": run_id})
            return {"state": "candidate", "lesson_id": lesson_id, "version_id": ident, "version": version,
                    "approval_required": "Separate local operator CLI operation"}
        return self.store.write(scope, "lesson_propose", run_id, request_key,
                                {"actor": actor.session_id, "generation": generation, "data": data}, write)

    def operator(self, scope: Scope, version_id: str, action: str, reason: str, request_key: str):
        choice(action, "operator action", {"approve", "suspend", "restrict"})
        text(reason, "operator evaluation/reason", 4000)
        target_state = {"approve": "active", "suspend": "suspended", "restrict": "restricted"}[action]

        def write(conn):
            lesson = self.version(conn, scope, version_id)
            if action == "approve":
                if lesson["state"] != "candidate":
                    raise LedgerError("invalid_transition", "Only a new candidate version can be approved; revise suspended knowledge first")
                if not lesson["sources"] or any(not s["valid"] or s["outcome"] not in ELIGIBLE_OUTCOMES for s in lesson["sources"]):
                    raise LedgerError("ineligible_source", "An invalid source prevents approval")
                # A delayed approval cannot displace a newer approved version.
                newer = conn.execute("SELECT 1 FROM lesson_versions WHERE lesson_id=? AND version>? AND approved_at IS NOT NULL", (lesson["lesson_id"], lesson["version"])).fetchone()
                if newer:
                    raise LedgerError("version_conflict", "A newer lesson version has already been approved")
                conn.execute("UPDATE lesson_versions SET state='retired',reason='Superseded by approved version' WHERE lesson_id=? AND state='active'", (lesson["lesson_id"],))
                conn.execute("UPDATE lesson_versions SET state='active',approved_at=?,reason=? WHERE id=?", (now(), reason, version_id))
            else:
                conn.execute("UPDATE lesson_versions SET state=?,reason=? WHERE id=?", (target_state, reason, version_id))
            self.store.audit(conn, scope, version_id, action, "local_operator", {"reason": reason})
            return {"state": target_state,
                    "version_id": version_id, "reason": reason}
        return self.store.write(scope, "lesson_operator", version_id, request_key, {"action": action, "reason": reason}, write)

    def recall(self, scope: Scope, run_id: str, *, terms: list[str] | None = None,
               tags: list[str] | None = None, symbols: list[str] | None = None,
               limit: int = 5, context_budget: int = 6000, offset: int = 0) -> dict:
        terms = strings(terms or [], "terms", 20, 80)
        tags = strings(tags or [], "tags", 20, 80)
        symbols = strings(symbols or [], "symbols", 20, 200)
        integer(limit, "limit", 1, 5)
        integer(context_budget, "context_budget", 500, 20000)
        integer(offset, "offset", 0, 1_000_000)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.run(conn, scope, run_id)
            # SQL scope/state/text filtering and a bounded candidate window; no FTS dependency.
            clauses, args = [], [scope.repository_id]
            for term in terms + tags + symbols:
                # Parameter binding plus LIKE escaping keeps user text literal.
                term = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("lower(question || conditions_json || exclusions_json || tags_json || symbols_json || verification) LIKE ? ESCAPE '\\'")
                args.append("%" + term + "%")
            filter_sql = " AND (" + " OR ".join(clauses) + ")" if clauses else ""
            args.extend([201, offset])
            rows = conn.execute("SELECT id FROM lesson_versions WHERE repository_id=? AND state='active'" + filter_sql + " ORDER BY lesson_id,version,id LIMIT ? OFFSET ?", args).fetchall()
            candidates = []
            for row in rows[:200]:
                if not self.eligible(conn, scope, row[0]):
                    continue
                item = self.version(conn, scope, row[0])
                haystack = canonical(item).casefold()
                score = sum(1 for t in terms if t.casefold() in haystack) + 2 * len(set(tags) & set(item["tags"])) + 3 * len(set(symbols) & set(item["symbols"]))
                candidates.append((score, item))
            candidates.sort(key=lambda pair: (-pair[0], pair[1]["lesson_id"], pair[1]["version"], pair[1]["id"]))
            selected, used, omitted_budget = [], 0, 0
            for _, item in candidates:
                if len(selected) >= limit:
                    break
                size = len(canonical(item))
                if used + size > context_budget:
                    omitted_budget += 1
                    continue
                selected.append(item)
                used += size
            result = {"state": "ok", "lessons": selected, "context_chars": used, "context_budget": context_budget,
                    "omitted": {"candidate_window": len(rows) > 200, "budget": omitted_budget,
                                "result_limit": len(candidates) > len(selected) + omitted_budget},
                    "next_offset": offset + 200 if len(rows) > 200 else None,
                    "notice": "Conditions guide investigation; similarity does not prove a defect. Recheck eligibility before use."}
            conn.commit()
            return result

    def use(self, scope: Scope, run_id: str, actor: Actor, generation: int,
            version_id: str, applicability: str, explanation: str, request_key: str):
        choice(applicability, "applicability", {"applicable", "not_applicable", "uncertain"})
        text(explanation, "explanation", 4000)
        def write(conn):
            self.store.owner(conn, scope, run_id, actor, generation)
            if not self.eligible(conn, scope, version_id):
                raise LedgerError("lesson_not_eligible", "This exact lesson version has been revoked, superseded, or has an invalid source")
            existing = conn.execute("SELECT * FROM lesson_uses WHERE version_id=? AND run_id=?", (version_id, run_id)).fetchone()
            if existing:
                if existing["applicability"] != applicability or existing["explanation"] != explanation:
                    raise LedgerError("use_conflict", "This version already has a use in this run; record its result separately")
                return {"state": "existing", "use_id": existing["id"], "version_id": version_id}
            ident, stamp = new_id("use"), now()
            conn.execute("""INSERT INTO lesson_uses (id,repository_id,version_id,run_id,applicability,explanation,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""", (ident, scope.repository_id, version_id, run_id, applicability, explanation, stamp, stamp))
            return {"state": "recorded", "use_id": ident, "version_id": version_id, "result": None}
        def replay_check(conn):
            if not self.eligible(conn, scope, version_id):
                raise LedgerError("lesson_not_eligible", "The original use receipt remains in history, but this version is no longer eligible for reuse")
        return self.store.write(scope, "lesson_use", run_id, request_key,
                                {"actor": actor.session_id, "generation": generation, "version": version_id, "applicability": applicability, "explanation": explanation}, write, replay_check)

    def result(self, scope: Scope, run_id: str, actor: Actor, generation: int,
               use_id: str, data: dict, request_key: str):
        fields(data, {"usefulness", "behavioral_result", "execution_block", "explanation"},
               {"usefulness", "behavioral_result", "execution_block", "explanation"})
        usefulness = choice(data["usefulness"], "usefulness", {"useful", "not_useful", "inconclusive"})
        behavior = choice(data["behavioral_result"], "behavioral_result", {"failure_observed", "hypothesis_refuted", "no_failure_observed", "inconclusive", "not_tested"})
        blocked = choice(data["execution_block"], "execution_block", {"none", "infrastructure", "timeout", "skipped", "budget", "missing_evidence"})
        explanation = text(data["explanation"], "explanation", 4000)
        if blocked != "none" and behavior not in ("inconclusive", "not_tested"):
            raise LedgerError("invalid_outcome", "Blocked execution cannot establish a behavioral result")
        def write(conn):
            self.store.owner(conn, scope, run_id, actor, generation)
            use = conn.execute("SELECT * FROM lesson_uses WHERE repository_id=? AND run_id=? AND id=?", (scope.repository_id, run_id, use_id)).fetchone()
            if use is None:
                raise LedgerError("scope_not_found", "Record a use in this run before reporting its result")
            if use["applicability"] == "not_applicable" and behavior not in ("not_tested", "inconclusive"):
                raise LedgerError("invalid_outcome", "A nonapplicable strategy cannot have a tested behavioral result")
            if use["usefulness"] is not None:
                raise LedgerError("result_exists", "Results are immutable; a retry must use the original request key")
            conn.execute("UPDATE lesson_uses SET usefulness=?,behavioral_result=?,execution_block=?,result_explanation=?,updated_at=? WHERE id=?",
                         (usefulness, behavior, blocked, explanation, now(), use_id))
            return {"state": "recorded", "use_id": use_id, "version_id": use["version_id"],
                    "eligible_now": self.eligible(conn, scope, use["version_id"]),
                    "notice": "Outcome is agent-reported; a useful refutation is not an execution failure."}
        return self.store.write(scope, "lesson_result", run_id, request_key,
                                {"actor": actor.session_id, "generation": generation, "use_id": use_id, "data": data}, write)
