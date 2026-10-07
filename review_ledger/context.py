"""Bounded deterministic context. Guidance and historical records are not proof."""
from __future__ import annotations

import hashlib
import json
from .learning import Learning, normalize_search
from .models import LedgerError, canonical, choice, digest, integer, strings, text
from .protocol import protocol
from .storage import new_id, now

POLICY_VERSION = "1"
MIN_BUDGET = 2000
MAX_BUDGET = 64000


class Context:
    def __init__(self, ledger, budget=12000, *, skills_enabled=False, guidance_enabled=True):
        self.ledger = ledger
        self.store = ledger.store
        self.budget = integer(budget, "configured bundle budget", MIN_BUDGET, MAX_BUDGET)
        self.skills_enabled = skills_enabled
        self.guidance_enabled = guidance_enabled

    def _budget(self, requested):
        return min(self.budget, integer(self.budget if requested is None else requested,
                                       "max_chars", MIN_BUDGET, MAX_BUDGET))

    @staticmethod
    def _identity(kind, ident, content):
        return {"kind": kind, "id": ident, "sha256": digest(content)}

    @staticmethod
    def _size(value):
        return len(canonical(value))

    def prepare(self, repository, run_id, actor, *, query, phase="investigate",
                max_chars=None, tags=None, symbols=None, _manifest=None):
        scope = self.ledger.scope(repository)
        query = text(query, "query", 1000, empty=True)
        choice(phase, "phase", {"discover", "investigate", "assess", "resume"})
        tags = strings(tags or [], "tags", 20, 80)
        symbols = strings(symbols or [], "symbols", 20, 200)
        budget = self._budget(max_chars)
        p = protocol()
        terms = normalize_search(query).split()[:20]
        terms = [term[:80] for term in terms]
        # Existing baseline retrieval is bounded and Unicode-consistent; no FTS,
        # model, network, or whole-database materialization is required.
        recall = (Learning(self.store).recall(scope, run_id, terms=terms, tags=tags,
                  symbols=symbols, limit=5, context_budget=20000)
                  if self.guidance_enabled and not _manifest else {"lessons": [], "lesson_references": [], "omitted": {}})
        with self.store.connect() as conn, self.store.transaction(conn):
            run = self.store.run(conn, scope, run_id)
            if _manifest:
                if any(i["kind"] == "skill" for i in _manifest["selections"]) and not self.skills_enabled:
                    raise LedgerError("feature_disabled", "Pinned optional guidance cannot be reused while skills are disabled")
                if _manifest["protocol_hash"] != p["sha256"]:
                    raise LedgerError("protocol_changed", "Prepare an explicit refreshed bundle after a software update")
                if _manifest["snapshot_key"] != run["snapshot_key"]:
                    raise LedgerError("snapshot_changed", "Manifest belongs to a different snapshot")
            manifest_id = new_id("context")
            result = {"state": "ok", "run_id": run_id,
                      "snapshot": {"head_sha": run["head_sha"], "base_sha": run["base_sha"],
                                   "comparison": run["comparison"], "status": run["status"]},
                      "protocol": p, "policy_version": POLICY_VERSION,
                      "query": query, "phase": phase, "checkpoint": None,
                      "records": [], "references": [], "omitted": [],
                      "selection": {"candidate_count": 0, "selected_count": 0,
                                    "ranking": "bounded candidate window; no global ranking"},
                      "residency": "unknown; essential content is self-contained on every delivery",
                      "manifest_id": manifest_id, "content_digest": "0" * 64,
                      "notice": "Guidance is not evidence. Historical reports require current verification.",
                      "max_chars": budget}
            selected = []
            omissions = 0

            def reference(kind, ident, content, reason="budget"):
                nonlocal omissions
                ref = {"kind": kind, "id": ident, "state": "not_loaded", "reason": reason,
                       "required_chars": self._size(content), "detail": {"action": "detail", "kind": kind, "record_id": ident}}
                result["references"].append(ref)
                if self._size(result) + 300 > budget:
                    result["references"].pop()
                    omissions += 1

            def add(kind, ident, content, *, applicability=None, source_info=None):
                result["selection"]["candidate_count"] += 1
                identity = self._identity(kind, ident, content)
                if source_info:
                    identity.update(source_info)
                # Deduplicate only exactly equal semantic content of the same kind.
                for existing in result["records"]:
                    if existing["kind"] == kind and existing["content"] == content:
                        existing["sources"].append(identity)
                        if self._size(result) + 300 <= budget:
                            selected.append(identity)
                            return
                        existing["sources"].pop()
                        reference(kind, ident, content)
                        return
                record = {"kind": kind, "sources": [identity], "content": content}
                if applicability is not None:
                    record["applicability"] = applicability
                result["records"].append(record)
                if self._size(result) + 300 > budget:
                    result["records"].pop()
                    reference(kind, ident, content)
                else:
                    selected.append(identity)
                    result["selection"]["selected_count"] += 1

            if run["note"]:
                checkpoint = {"recorded_note": run["note"], "source": "run.note", "completed_steps_inferred": False}
                result["checkpoint"] = checkpoint
                if self._size(result) + 300 > budget:
                    result["checkpoint"] = None
                    reference("run", run_id, checkpoint)
            snapshot = json.loads(run["snapshot_json"])
            add("snapshot_completeness", run_id, {k: snapshot.get(k) for k in
                ("files_complete", "patches_complete", "total_files", "omitted_files", "truncation_reasons")})
            lesson_ids = ([i["id"] for i in _manifest["selections"] if i["kind"] == "lesson"] if _manifest else
                          [i["id"] for i in recall["lessons"]] + [i["version_id"] for i in recall["lesson_references"]])
            for ident in lesson_ids:
                lesson = Learning.version(conn, scope, ident)
                if not lesson["eligible_now"]:
                    if _manifest:
                        raise LedgerError("context_revoked", "A selected lesson is no longer eligible; refresh explicitly")
                    continue
                working = {k: lesson[k] for k in ("question", "conditions", "exclusions", "verification", "tags", "symbols")}
                # Source identities remain attached even when text is identical.
                working["evidence_notice"] = "Conditional guidance, not proof; sources are historical agent reports"
                add("lesson", ident, working, applicability="unknown; check every condition and exclusion")
            if self.skills_enabled:
                from .skills import Skills
                pinned = bool(_manifest)
                if pinned:
                    candidates = [Skills.version(conn, scope, i["id"], require_enabled=True)
                                  for i in _manifest["selections"] if i["kind"] == "skill"]
                    prior_request = json.loads(_manifest["request_json"])
                    skill_tags, skill_symbols = prior_request.get("tags", []), prior_request.get("symbols", [])
                else:
                    candidates = Skills.candidates(conn, scope, limit=50)
                    skill_tags, skill_symbols = tags, symbols
                seen_names = set()
                matched_skills = []
                for candidate in candidates:
                    # Fresh preparation selects newest enabled content per name;
                    # resuming preserves exact recorded version IDs.
                    if not pinned and candidate["qualified_id"] in seen_names:
                        continue
                    seen_names.add(candidate["qualified_id"])
                    applicability = Skills.applicability(candidate["metadata"], repository=scope.repository_name,
                                                         phase=phase, tags=skill_tags, symbols=skill_symbols)
                    if not applicability["eligible"]:
                        if pinned:
                            raise LedgerError("context_inapplicable", "Pinned skill no longer applies to this phase; prepare an explicit refresh")
                        reference("skill", candidate["id"], candidate, reason=applicability["reason"])
                        continue
                    searchable = normalize_search(canonical(candidate["metadata"]))
                    score = sum(term in searchable for term in terms)
                    matched_skills.append((score, candidate, applicability))
                matched_skills.sort(key=lambda entry: (-entry[0], entry[1]["qualified_id"], entry[1]["id"]))
                for _, candidate, applicability in matched_skills:
                    ident = candidate["id"]
                    if phase == "discover":
                        metadata = candidate["metadata"]
                        content = {"id": ident, "qualified_id": candidate["qualified_id"],
                                   "version": candidate["version"], "content_digest": candidate["content_digest"],
                                   "description": metadata["description"],
                                   "applicability": metadata.get("applicability", {}),
                                   "size_bytes": candidate["size_bytes"], "instructions_state": "not_loaded",
                                   "detail": {"action": "detail", "kind": "skill", "record_id": ident}}
                    else:
                        # Preserve every selected instruction/reference in one complete
                        # unit. The budget helper returns a reference, never truncation.
                        full = Skills.version(conn, scope, ident, require_enabled=True)
                        content = {k: full[k] for k in ("metadata", "instructions", "resources")}
                    add("skill", ident, content, applicability=applicability,
                        source_info={"qualified_id": candidate["qualified_id"], "version": candidate["version"],
                                     "approved_content_digest": candidate["content_digest"]})
                if not pinned and len(candidates) == 50:
                    result["omitted"].append({"kind": "skill", "reason": "bounded candidate window", "detail": "operator skill-list"})
            for table, kind in (("observations", "observation"), ("assessments", "assessment")):
                rows = conn.execute(f"SELECT * FROM {table} WHERE repository_id=? AND run_id=? ORDER BY created_at DESC,id LIMIT 11",
                                    (scope.repository_id, run_id)).fetchall()
                for row in rows[:10]:
                    item = dict(row)
                    item.pop("repository_id", None)
                    if kind == "assessment":
                        finding = conn.execute("SELECT claim FROM findings WHERE repository_id=? AND id=?",
                                               (scope.repository_id, item["finding_id"])).fetchone()
                        item["claim"] = finding["claim"] if finding else None
                        item["sources"] = [dict(r) for r in conn.execute("SELECT observation_id,relation FROM assessment_sources WHERE repository_id=? AND assessment_id=? ORDER BY observation_id LIMIT 20", (scope.repository_id,item["id"]))]
                    add(kind, row["id"], item)
                if len(rows) > 10:
                    result["omitted"].append({"kind": kind, "reason": "record window", "detail": "ledger_status pagination"})
            findings = self.ledger._findings(conn, scope, run_id, 11, 0)
            for finding in findings[:10]:
                add("finding", finding["id"], finding)
            if len(findings) > 10:
                result["omitted"].append({"kind": "finding", "reason": "record window", "detail": "ledger_status pagination"})
            if recall.get("omitted", {}).get("result_limit"):
                result["omitted"].append({"kind": "lesson", "reason": "result limit", "detail": "ledger_recall",
                                          "next_result_offset": recall.get("next_result_offset"), "candidate_offset": 0})
            if recall.get("omitted", {}).get("candidate_window"):
                result["omitted"].append({"kind": "lesson", "reason": "bounded candidate window", "detail": "ledger_recall pagination"})
            if omissions:
                result["omitted"].append({"reason": "reference metadata budget", "count": omissions,
                                          "detail": "narrow query or use ledger_status/ledger_recall"})
            result["narrowing"] = "Use a shorter query, focused detail section, or an operator-approved larger cap."
            # Even envelope metadata and escaping count. Do not trim a condition.
            if self._size(result) > budget:
                result = {"state": "requires_more_context", "run_id": run_id, "max_chars": budget,
                          "protocol": {"version": p["version"], "sha256": p["sha256"],
                                       "state": "not_loaded", "detail": {"action": "detail", "kind": "protocol"}},
                          "reason": "Essential context and metadata do not fit; no guidance is loaded",
                          "narrowing": "Request focused detail or an operator-approved larger cap"}
                return result
            result["content_digest"] = digest({k: v for k, v in result.items() if k != "content_digest"})
            # Immutable selected references, not full bundle copies. Delivery is
            # rendered only; Hermes does not prove receipt or cache residency.
            conn.execute("INSERT INTO context_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (manifest_id, scope.repository_id, run_id, run["snapshot_key"], p["version"], p["sha256"],
                          POLICY_VERSION, canonical(selected), digest({"query": query, "phase": phase, "tags": tags, "symbols": symbols}),
                          result["content_digest"], "rendered; delivery unconfirmed; residency unknown", now(),
                          canonical({"query": query, "phase": phase, "tags": tags, "symbols": symbols})))
            return result

    def resume(self, repository, run_id, actor, *, query=None, max_chars=None, manifest_id=None):
        scope = self.ledger.scope(repository)
        manifest = None
        if manifest_id:
            with self.store.connect() as conn:
                row = conn.execute("SELECT * FROM context_manifests WHERE repository_id=? AND run_id=? AND id=?",
                                   (scope.repository_id, run_id, manifest_id)).fetchone()
                if row is None:
                    raise LedgerError("scope_not_found", "Context manifest is not in this run")
                manifest = dict(row)
                manifest["selections"] = json.loads(manifest["selections_json"])
                old_request = json.loads(manifest["request_json"])
                if query is None:
                    query = old_request["query"]
        # An absent current question remains absent. Notes are delivered separately.
        return self.prepare(repository, run_id, actor, query=query or "", phase="resume",
                            max_chars=max_chars, tags=old_request.get("tags", []) if manifest else None,
                            symbols=old_request.get("symbols", []) if manifest else None, _manifest=manifest)

    def detail(self, repository, run_id, actor, *, kind, record_id=None, section=None, max_chars=None):
        scope = self.ledger.scope(repository)
        budget = self._budget(max_chars)
        choice(kind, "kind", {"protocol", "lesson", "skill", "observation", "assessment", "finding", "run", "snapshot_completeness"})
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            run = self.store.run(conn, scope, run_id)
            if kind == "protocol":
                content = protocol()
            elif kind == "lesson":
                content = Learning.version(conn, scope, record_id)
                if not content["eligible_now"]:
                    raise LedgerError("lesson_not_eligible", "Exact lesson version is no longer eligible")
            elif kind == "skill":
                if not self.skills_enabled:
                    raise LedgerError("feature_disabled", "Optional skill loading is disabled")
                from .skills import Skills
                content = Skills.version(conn, scope, record_id, require_enabled=True)
                applicability = Skills.applicability(content["metadata"], repository=scope.repository_name, phase=None)
                if not applicability["eligible"]:
                    raise LedgerError("context_inapplicable", "Skill has a known repository applicability mismatch")
                content["applicability_now"] = applicability
            elif kind in {"run", "snapshot_completeness"}:
                if record_id and record_id != run_id:
                    raise LedgerError("scope_not_found", "Run reference differs from the selected run")
                content = dict(run)
                content.pop("owner_session", None)
                content["snapshot"] = json.loads(content.pop("snapshot_json"))
                content["config"] = json.loads(content.pop("config_json"))
            elif kind == "finding":
                row = conn.execute("SELECT * FROM findings f WHERE repository_id=? AND id=? AND (origin_run_id=? OR EXISTS (SELECT 1 FROM assessments a WHERE a.repository_id=f.repository_id AND a.finding_id=f.id AND a.run_id=?))", (scope.repository_id,record_id,run_id,run_id)).fetchone()
                if row is None:
                    raise LedgerError("scope_not_found", "Finding is not in this run")
                content = dict(row)
            else:
                table = {"observation": "observations", "assessment": "assessments"}[kind]
                row = conn.execute(f"SELECT * FROM {table} WHERE repository_id=? AND run_id=? AND id=?",
                                   (scope.repository_id, run_id, record_id)).fetchone()
                if row is None:
                    raise LedgerError("scope_not_found", "Record is not in this run")
                content = dict(row)
            full_digest = digest(content)
            if section is not None:
                text(section, "section", 80)
                if section not in content:
                    raise LedgerError("invalid_input", "Unknown semantic section")
                content = {section: content[section]}
            response = {"state": "detail", "kind": kind, "id": record_id,
                        "content": content, "content_sha256": full_digest,
                        "integrity": "Complete stored record assembled and hashed by backend",
                        "complete": section is None, "section": section,
                        "notice": "Focused sections are partial; necessary conditions and exclusions still apply",
                        "max_chars": budget}
            if self._size(response) > budget:
                response = {"state": "requires_more_context", "kind": kind, "id": record_id,
                            "content_sha256": full_digest, "complete": False,
                            "available_sections": list(content), "required_chars": self._size(response),
                            "max_chars": budget, "narrowing": "Select a complete semantic section or use legacy authorized detail pagination"}
            if self._size(response) > budget:
                raise LedgerError("resource_limit", "Detail reference metadata exceeds configured cap")
            return response
