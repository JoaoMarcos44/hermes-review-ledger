"""Review operations and data-integrity rules; independent of the host runtime."""
from __future__ import annotations

import hashlib
import json

from .models import (Actor, Assessment, ELIGIBLE_OUTCOMES, LedgerError, OUTCOMES,
                     Scope, canonical, choice, digest, fields, integer, sha, strings, text)
from .storage import Store, new_id, now
from .references import References, REFERENCE_FIELDS, REQUIRED_REFERENCE_FIELDS


MAX_SNAPSHOT_CHARS = 60_000


class Ledger:
    def __init__(self, store: Store, authorized_repositories: list[str], *, skill_version: str,
                 skill_hash: str, relevant_config: dict | None = None):
        self.store = store
        self.authorized = {name.casefold() for name in authorized_repositories}
        self.skill_version = skill_version
        self.skill_hash = skill_hash
        self.config = relevant_config or {}
        if len(canonical(self.config)) > 4000:
            raise LedgerError("resource_limit", "Relevant configuration exceeds its snapshot budget")

    def authorize(self, name: str):
        text(name, "repository", 200)
        if name.casefold() not in self.authorized:
            raise LedgerError("repository_not_authorized", "Repository is not explicitly configured for this profile")

    def scope(self, name: str) -> Scope:
        self.authorize(name)
        with self.store.connect() as conn:
            row = conn.execute("SELECT id,name FROM repositories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if row is None:
                raise LedgerError("scope_not_found", "Open this repository's PR first")
            return Scope(row["id"], row["name"])

    def open_generation(self, repository: str, pull_number: int) -> int:
        """Capture append-only review state before the adapter fetches GitHub."""
        self.authorize(repository)
        integer(pull_number, "PR number", 1, 2**31 - 1)
        with self.store.connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM runs r JOIN reviews v ON v.id=r.review_id
                JOIN repositories p ON p.id=v.repository_id
                WHERE p.name=? COLLATE NOCASE AND v.number=?""",
                (repository, pull_number),
            ).fetchone()[0]

    @staticmethod
    def _snapshot_metadata(snapshot: dict) -> dict:
        """Retain a file prefix within the capture budget and record omissions."""
        saved = {k: snapshot[k] for k in ("repository_id", "repository_full_name", "number", "head_sha", "base_sha", "comparison")}
        saved.update({k: snapshot.get(k) for k in ("files_complete", "patches_complete", "total_files", "omitted_files", "truncation_reasons")})
        saved["files"] = [{k: f.get(k) for k in ("filename", "previous_filename", "sha", "status", "patch_status")} for f in snapshot.get("files", [])]
        if len(canonical(saved)) <= MAX_SNAPSHOT_CHARS:
            return saved

        files = saved["files"]
        total, omitted = saved["total_files"], saved["omitted_files"]
        known_omitted = omitted if type(omitted) is int and omitted >= 0 else None
        if type(total) is int and total >= len(files):
            known_omitted = max(known_omitted or 0, total - len(files))
        reasons = saved["truncation_reasons"]
        saved["truncation_reasons"] = list(reasons) if isinstance(reasons, (list, tuple)) else []
        if "snapshot_metadata_budget" not in saved["truncation_reasons"]:
            saved["truncation_reasons"].append("snapshot_metadata_budget")
        saved["files_complete"] = False
        saved["patches_complete"] = False

        def capture(count):
            saved["files"] = files[:count]
            # Unknown upstream omissions stay unknown; a known count includes
            # both upstream exclusions and the descriptors removed here.
            saved["omitted_files"] = None if known_omitted is None else known_omitted + len(files) - count
            return len(canonical(saved)) <= MAX_SNAPSHOT_CHARS

        if not capture(0):
            raise LedgerError("resource_limit", "Snapshot metadata outside file descriptors exceeds 60,000 characters")
        low, high = 0, len(files)
        while low < high:
            middle = (low + high + 1) // 2
            if capture(middle):
                low = middle
            else:
                high = middle - 1
        capture(low)
        return saved

    def open(self, snapshot: dict, actor: Actor, request_key: str, *,
             expected_open_generation: int | None = None) -> dict:
        """Open supplied data; the trusted fetching adapter supplies its freshness guard."""
        text(request_key, "request_key", 128)
        if expected_open_generation is not None:
            integer(expected_open_generation, "expected_open_generation", 0, 2**63 - 1)
        name = text(snapshot["repository_full_name"], "repository", 200)
        self.authorize(name)
        repo_id = integer(snapshot["repository_id"], "repository_id", 1, 2**63 - 1)
        head, base = sha(snapshot["head_sha"], "head_sha"), sha(snapshot["base_sha"], "base_sha")
        number = integer(snapshot["number"], "PR number", 1, 2**31 - 1)
        comparison = choice(snapshot["comparison"], "comparison", {"github_pr"})
        # Preserve comparison identity and completeness, not complete patches or conversations.
        saved = self._snapshot_metadata(snapshot)
        scope = Scope(repo_id, name)
        with self.store.connect() as conn, self.store.transaction(conn):
            existing = conn.execute("SELECT * FROM repositories WHERE id=? OR name=? COLLATE NOCASE", (repo_id, name)).fetchall()
            if existing and (len(existing) != 1 or existing[0]["id"] != repo_id or existing[0]["name"].casefold() != name.casefold()):
                raise LedgerError("repository_identity_changed", "Repository name/identity changed; operator must review authorization")
            conn.execute("INSERT OR IGNORE INTO repositories VALUES (?,?,?,?,?)",
                         (repo_id, text(snapshot["repository_node_id"], "repository node ID", 256) if snapshot.get("repository_node_id") is not None else None, name, name.casefold(), now()))
        identity = {"head": head, "base": base, "comparison": comparison, "config": self.config,
                    "skill_version": self.skill_version, "skill_hash": self.skill_hash}
        payload = {"snapshot": saved, "identity": identity, "session": actor.session_id}

        def write(conn):
            review = conn.execute("SELECT * FROM reviews WHERE repository_id=? AND number=?", (repo_id, number)).fetchone()
            if review is None:
                review_id = new_id("review")
                conn.execute("INSERT INTO reviews VALUES (?,?,?,?,?)",
                             (review_id, repo_id, number, text(snapshot.get("title", "Untitled"), "title", 1000),
                              text(snapshot["url"], "PR URL", 1000)))
            else:
                review_id = review["id"]
            snapshot_key = digest(identity)
            run = conn.execute("SELECT * FROM runs WHERE review_id=? AND snapshot_key=? AND status IN ('active','paused')", (review_id, snapshot_key)).fetchone()
            if run is not None:
                return {"state": "reused", "run": self._run_view(run, actor), "history_inherited": False}
            if expected_open_generation is not None:
                current_generation = conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE repository_id=? AND review_id=?",
                    (repo_id, review_id),
                ).fetchone()[0]
                if current_generation != expected_open_generation:
                    raise LedgerError("snapshot_conflict", "Another opener changed this review during the fetch; fetch a fresh snapshot and retry")
            previous = conn.execute("SELECT id FROM runs WHERE review_id=? AND status IN ('active','paused')", (review_id,)).fetchall()
            for previous_run in previous:
                conn.execute("UPDATE runs SET status='superseded',owner_session=NULL,generation=generation+1,updated_at=? WHERE id=?", (now(), previous_run[0]))
                conn.execute("UPDATE assessments SET freshness='historical' WHERE run_id=?", (previous_run[0],))
            conn.execute("UPDATE assessments SET freshness='historical' WHERE run_id IN (SELECT id FROM runs WHERE review_id=?)", (review_id,))
            run_id, stamp = new_id("run"), now()
            conn.execute("""INSERT INTO runs
                (id,repository_id,review_id,snapshot_key,head_sha,base_sha,comparison,config_json,skill_version,skill_hash,snapshot_json,status,owner_session,generation,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,1,?,?)""",
                         (run_id, repo_id, review_id, snapshot_key, head, base, comparison, canonical(self.config),
                          self.skill_version, self.skill_hash, canonical(saved), actor.session_id, stamp, stamp))
            self.store.audit(conn, scope, run_id, "opened", actor.session_id, {"generation": 1})
            return {"state": "created", "run": self._run_view(self.store.run(conn, scope, run_id), actor), "history_inherited": False}

        return self.store.write(scope, "open", str(number), request_key, payload, write)

    @staticmethod
    def _run_view(run, actor: Actor | None = None):
        result = dict(run)
        for field in ("config_json", "snapshot_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        result["can_write"] = bool(actor and run["owner_session"] == actor.session_id and run["status"] in ("active", "paused"))
        return result

    @staticmethod
    def _run_references(conn, scope: Scope, review_id: str, *, limit: int,
                        offset: int, exclude_id: str | None = None) -> dict:
        """Discover bounded snapshot references, never session identities or evidence."""
        # V1 runs are append-only. Use their SQLite insertion order rather than
        # wall time (which can move backwards) or random UUID tie-breaks.
        latest = conn.execute(
            "SELECT id FROM runs WHERE repository_id=? AND review_id=? ORDER BY rowid DESC LIMIT 1",
            (scope.repository_id, review_id),
        ).fetchone()[0]
        clause = " AND id<>?" if exclude_id else ""
        args = [scope.repository_id, review_id] + ([exclude_id] if exclude_id else [])
        count = conn.execute("SELECT COUNT(*) FROM runs WHERE repository_id=? AND review_id=?" + clause, args).fetchone()[0]
        rows = conn.execute(
            "SELECT id,head_sha,base_sha,comparison,status,created_at,updated_at FROM runs "
            "WHERE repository_id=? AND review_id=?" + clause + " ORDER BY rowid DESC LIMIT ? OFFSET ?",
            [*args, limit + 1, offset],
        ).fetchall()
        refs = [{**dict(row), "snapshot_state": "latest_recorded" if row["id"] == latest else "historical"}
                for row in rows[:limit]]
        return {"runs": refs, "total_runs": count, "omitted": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None,
                "notice": "Recorded snapshots only; latest_recorded is not a fresh GitHub check. Historical assessments are not inherited."}

    def history(self, repository: str, pull_number: int, *, limit=10, offset=0, max_chars=24_000) -> dict:
        """Read a same-PR index even when a new session knows no prior run ID."""
        scope = self.scope(repository)
        integer(pull_number, "pull_number", 1, 2**31 - 1)
        integer(limit, "limit", 1, 25)
        integer(offset, "offset", 0, 1_000_000)
        integer(max_chars, "max_chars", 4000, 64_000)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            review = conn.execute("SELECT id FROM reviews WHERE repository_id=? AND number=?",
                                  (scope.repository_id, pull_number)).fetchone()
            if review is None:
                raise LedgerError("scope_not_found", "PR is not present in this repository and profile")
            result = self._run_references(conn, scope, review["id"], limit=limit, offset=offset)
            output = {"state": "ok", "repository": repository, "pull_number": pull_number,
                      "max_chars": max_chars, **result}
            while len(canonical(output)) > max_chars and len(output["runs"]) > 1:
                output["runs"].pop()
                output["omitted"] = True
                output["next_offset"] = offset + len(output["runs"])
            return output

    @staticmethod
    def _findings(conn, scope: Scope, run_id: str, limit: int, offset: int) -> list[dict]:
        """Read origin and reassessed claims through their run indexes."""
        return [dict(row) for row in conn.execute("""
            SELECT f.* FROM findings f WHERE f.repository_id=? AND f.origin_run_id=?
            UNION
            SELECT f.* FROM assessments a JOIN findings f
                ON f.repository_id=a.repository_id AND f.id=a.finding_id
                WHERE a.repository_id=? AND a.run_id=?
            ORDER BY id LIMIT ? OFFSET ?
            """, (scope.repository_id, run_id, scope.repository_id, run_id, limit, offset))]

    @staticmethod
    def _assessment_sources(conn, scope: Scope, assessment: dict) -> None:
        assessment["sources"] = [dict(row) for row in conn.execute(
            "SELECT observation_id,relation FROM assessment_sources "
            "WHERE repository_id=? AND assessment_id=? ORDER BY observation_id",
            (scope.repository_id, assessment["id"]))]

    @staticmethod
    def _status_reference(collection: str, item: dict) -> dict:
        """Identify complete stored detail; never present a shortened claim as evidence."""
        content = canonical(item)
        retained = {
            "run": ("id", "head_sha", "base_sha", "comparison", "status", "generation", "can_write"),
            "observation": ("id", "kind", "outcome", "provenance", "valid"),
            "assessment": ("id", "finding_id", "state", "freshness", "basis"),
            "finding": ("id", "origin_run_id"),
        }
        result = {key: item[key] for key in retained[collection]}
        result.update(detail_required=True, detail_collection=collection,
                      content_chars=len(content), content_sha256=hashlib.sha256(content.encode()).hexdigest())
        if collection != "run":
            result["detail_id"] = item["id"]
        return result

    def status(self, repository: str, run_id: str, actor: Actor, *, limit=10, offset=0,
               history_offset=0, max_chars=24_000) -> dict:
        scope = self.scope(repository)
        integer(limit, "limit", 1, 25)
        integer(offset, "offset", 0, 1_000_000)
        integer(history_offset, "history_offset", 0, 1_000_000)
        integer(max_chars, "max_chars", 4000, 64_000)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            run = self.store.run(conn, scope, run_id)
            observations = [dict(r) for r in conn.execute("SELECT * FROM observations WHERE repository_id=? AND run_id=? ORDER BY id LIMIT ? OFFSET ?", (scope.repository_id, run_id, limit + 1, offset))]
            assessments = [dict(r) for r in conn.execute("""SELECT a.*,f.claim FROM assessments a JOIN findings f ON a.finding_id=f.id
                WHERE a.repository_id=? AND a.run_id=? ORDER BY a.created_at,a.id LIMIT ? OFFSET ?""", (scope.repository_id, run_id, limit + 1, offset))]
            findings = self._findings(conn, scope, run_id, limit + 1, offset)
            for obs in observations[:limit]:
                if obs["artifact_id"]:
                    obs["artifact"] = self.store.artifact_status(obs["artifact_id"])
            for assessment in assessments[:limit]:
                self._assessment_sources(conn, scope, assessment)
            history = self._run_references(conn, scope, run["review_id"], limit=limit,
                                           offset=history_offset, exclude_id=run_id)
            output = {"state": "ok", "run": self._run_view(run, actor), "observations": observations[:limit],
                    "assessments": assessments[:limit], "findings": findings[:limit], "historical_runs": history["total_runs"],
                    "related_runs": history,
                    "omitted": {"observations": len(observations) > limit, "assessments": len(assessments) > limit, "findings": len(findings) > limit},
                    "next_offset": offset + limit if max(len(observations), len(assessments), len(findings)) > limit else None,
                    "max_chars": max_chars,
                    "detail_notice": "Items with detail_required are references, not complete evidence. "
                                     "Use ledger_status detail_collection/detail_id with this run_id, "
                                     "reassemble all pages with one digest and verify it before interpreting omitted fields.",
                    "provenance_notice": "Agent-reported records are not host-verified evidence."}
            size = len(canonical(output))
            if size <= max_chars:
                return output

            # Replace the largest payloads first, retaining complete smaller
            # records whenever the complete response can still fit.
            candidates = []
            for key, collection in (("run", "run"), ("observations", "observation"),
                                    ("assessments", "assessment"), ("findings", "finding")):
                items = [output[key]] if key == "run" else output[key]
                for index, item in enumerate(items):
                    reference = self._status_reference(collection, item)
                    saving = len(canonical(item)) - len(canonical(reference))
                    if saving > 0:
                        candidates.append((saving, key, index, reference))
            for saving, key, index, reference in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
                if key == "run":
                    output[key] = reference
                else:
                    output[key][index] = reference
                size -= saving
                if size <= max_chars:
                    return output

            # Every returned row consumes one position even when it is a
            # reference. Shrink prefixes with matching cursors; never skip the
            # undisplayed tail of the originally requested window.
            while len(canonical(output)) > max_chars and len(history["runs"]) > 1:
                history["runs"].pop()
                history["omitted"] = True
                history["next_offset"] = history_offset + len(history["runs"])
            count = limit
            rows = {"observations": observations, "assessments": assessments, "findings": findings}
            while len(canonical(output)) > max_chars and count > 1:
                count -= 1
                for key in rows:
                    output[key] = output[key][:count]
                    output["omitted"][key] = len(rows[key]) > count
                output["next_offset"] = offset + count if any(output["omitted"].values()) else None
            if len(canonical(output)) > max_chars:
                raise LedgerError("status_budget_exceeded", "Status references exceed the character budget")
            return output

    def status_detail(self, repository: str, run_id: str, actor: Actor, *, detail_collection: str,
                      detail_id: str | None = None, offset=0, max_chars=24_000) -> dict:
        """Return complete, scoped record JSON in pages whose whole envelope fits."""
        scope = self.scope(repository)
        choice(detail_collection, "detail_collection", {"run", "observation", "assessment", "finding"})
        integer(offset, "offset", 0, 1_000_000)
        integer(max_chars, "max_chars", 4000, 64_000)
        if detail_collection == "run":
            if detail_id is not None:
                raise LedgerError("invalid_input", "Run detail uses run_id without detail_id")
        else:
            text(detail_id, "detail_id", 64)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            run = self.store.run(conn, scope, run_id)
            if detail_collection == "run":
                item = self._run_view(run, actor)
            elif detail_collection == "observation":
                row = conn.execute("SELECT * FROM observations WHERE repository_id=? AND run_id=? AND id=?",
                                   (scope.repository_id, run_id, detail_id)).fetchone()
                item = dict(row) if row is not None else None
                if item is not None and item["artifact_id"]:
                    item["artifact"] = self.store.artifact_status(item["artifact_id"])
            elif detail_collection == "assessment":
                row = conn.execute("SELECT a.*,f.claim FROM assessments a JOIN findings f ON a.finding_id=f.id "
                                   "WHERE a.repository_id=? AND a.run_id=? AND a.id=?",
                                   (scope.repository_id, run_id, detail_id)).fetchone()
                item = dict(row) if row is not None else None
                if item is not None:
                    self._assessment_sources(conn, scope, item)
            else:
                row = conn.execute("SELECT f.* FROM findings f WHERE f.repository_id=? AND f.id=? AND "
                                   "(f.origin_run_id=? OR EXISTS (SELECT 1 FROM assessments a "
                                   "WHERE a.repository_id=? AND a.run_id=? AND a.finding_id=f.id))",
                                   (scope.repository_id, detail_id, run_id, scope.repository_id, run_id)).fetchone()
                item = dict(row) if row is not None else None
            if item is None:
                raise LedgerError("scope_not_found", "Detail record is not in this run, repository and profile")
            content = canonical(item)
            if offset >= len(content):
                raise LedgerError("invalid_input", "Detail offset must refer to a character within the complete record JSON")
            checksum = hashlib.sha256(content.encode()).hexdigest()

            def page(length):
                end = offset + length
                result = {"state": "detail", "run_id": run_id, "detail_collection": detail_collection,
                          "content_format": "canonical_json", "content": content[offset:end],
                          "content_sha256": checksum, "total_chars": len(content), "offset": offset,
                          "next_offset": end if end < len(content) else None,
                          "complete": offset == 0 and end == len(content), "max_chars": max_chars,
                          "notice": "Reassemble all pages with the same content_sha256 and verify the digest before "
                                    "interpreting the record. Restart if the digest changes; detail is checked in scope on every page."}
                if detail_collection != "run":
                    result["detail_id"] = detail_id
                return result

            low, high = 0, min(len(content) - offset, max_chars)
            while low < high:
                length = (low + high + 1) // 2
                if len(canonical(page(length))) <= max_chars:
                    low = length
                else:
                    high = length - 1
            if low == 0:
                raise LedgerError("status_budget_exceeded", "Detail envelope exceeds the character budget")
            return page(low)

    def run_action(self, repository: str, run_id: str, actor: Actor, generation: int,
                   action: str, request_key: str, note: str = "") -> dict:
        scope = self.scope(repository)
        choice(action, "action", {"acquire", "release", "pause", "complete"})
        integer(generation, "generation", 1, 2**63 - 1)
        text(note, "note", 4000, empty=True)
        payload = {"actor": actor.session_id, "generation": generation, "action": action, "note": note}

        def write(conn):
            run = self.store.run(conn, scope, run_id)
            if action == "acquire":
                if run["generation"] != generation or run["owner_session"] not in (None, actor.session_id):
                    raise LedgerError("ownership_conflict", "Only the unowned current generation can be acquired")
                if run["status"] not in ("active", "paused"):
                    raise LedgerError("run_not_writable", "This investigation is no longer active")
                if run["owner_session"] is None:
                    conn.execute("UPDATE runs SET owner_session=?,generation=generation+1,status='active',updated_at=?,note=? WHERE id=?", (actor.session_id, now(), note, run_id))
            else:
                self.store.owner(conn, scope, run_id, actor, generation)
                status = {"pause": "paused", "complete": "completed", "release": run["status"]}[action]
                conn.execute("UPDATE runs SET owner_session=NULL,generation=generation+1,status=?,updated_at=?,note=? WHERE id=?", (status, now(), note, run_id))
            self.store.audit(conn, scope, run_id, action, actor.session_id, {"generation": generation, "note": note})
            return {"state": action, "run": self._run_view(self.store.run(conn, scope, run_id), actor)}
        return self.store.write(scope, "run", run_id, request_key, payload, write)

    def record(self, repository: str, run_id: str, actor: Actor, generation: int,
               action: str, data: dict, request_key: str) -> dict:
        scope = self.scope(repository)
        choice(action, "action", {"observation", "finding", "assessment", "invalidate_observation",
                                   "external_reference", "invalidate_external_reference"})
        integer(generation, "generation", 1, 2**63 - 1)
        validators = {
            "observation": ({"kind", "outcome", "summary", "details", "limitations", "environment", "command_text", "reproduction_patch_sha", "artifact_text"}, {"kind", "outcome", "summary", "limitations"}),
            "finding": ({"claim"}, {"claim"}),
            "external_reference": (REFERENCE_FIELDS, REQUIRED_REFERENCE_FIELDS),
            "invalidate_external_reference": ({"reference_id", "reason"}, {"reference_id", "reason"}),
            "assessment": ({"finding_id", "state", "basis", "rationale", "limitations", "observation_ids", "resolution"}, {"finding_id", "state", "basis", "rationale", "limitations", "observation_ids"}),
            "invalidate_observation": ({"observation_id", "reason"}, {"observation_id", "reason"}),
        }
        fields(data, *validators[action])
        if action in {"external_reference", "invalidate_external_reference"}:
            # Check Unicode before Store.write hashes the untrusted raw payload.
            try:
                canonical(data).encode("utf-8")
            except UnicodeEncodeError as exc:
                raise LedgerError("invalid_input", "External reference data requires valid Unicode text") from exc
        payload = {"actor": actor.session_id, "generation": generation, "action": action, "data": data}

        # Stage bounded optional files outside SQLite's writer transaction.
        artifact = None
        if action == "observation" and "artifact_text" in data:
            with self.store.connect() as conn:
                receipt = self.store.receipt(conn, scope, "record", run_id, request_key, payload)
                if receipt is not None:
                    return receipt
                try:
                    self.store.owner(conn, scope, run_id, actor, generation)
                except LedgerError:
                    # Another exact request may have committed before an owner
                    # change between the receipt read and this preflight check.
                    receipt = self.store.receipt(conn, scope, "record", run_id, request_key, payload)
                    if receipt is not None:
                        return receipt
                    raise
            artifact = self.store.put_artifact(data["artifact_text"])

        def write(conn):
            run = self.store.owner(conn, scope, run_id, actor, generation)
            if action == "observation":
                return self._observation(conn, scope, run, data, artifact)
            if action == "finding":
                ident = new_id("finding")
                conn.execute("INSERT INTO findings VALUES (?,?,?,?,?)", (ident, scope.repository_id, run_id, text(data["claim"], "claim"), now()))
                return {"state": "proposed", "finding_id": ident, "assessment": "unverified"}
            if action == "assessment":
                return self._assessment(conn, scope, run, data)
            if action == "external_reference":
                return References.record(conn, scope, run, data, actor.session_id)
            if action == "invalidate_external_reference":
                return References.invalidate(conn, scope, run, data, actor.session_id)
            obs = conn.execute("SELECT * FROM observations WHERE repository_id=? AND id=? AND run_id=?", (scope.repository_id, data["observation_id"], run_id)).fetchone()
            if obs is None:
                raise LedgerError("scope_not_found", "Observation is not in this run")
            self.invalidate(conn, scope, obs["id"], text(data["reason"], "reason"), actor.session_id)
            return {"state": "invalidated", "observation_id": obs["id"]}
        try:
            result = self.store.write(scope, "record", run_id, request_key, payload, write)
        except BaseException:
            if artifact is not None:
                self.store.remove_unreferenced_artifact(artifact)
            raise
        if artifact is not None and result.get("artifact_id") != artifact:
            self.store.remove_unreferenced_artifact(artifact)
        return result

    def _observation(self, conn, scope, run, data, artifact=None):
        kind = choice(data["kind"], "kind", {"inspection", "test", "note"})
        outcome = choice(data["outcome"], "outcome", OUTCOMES)
        summary = text(data["summary"], "summary")
        limits = text(data["limitations"], "limitations")
        details = text(data.get("details", ""), "details", 8000, empty=True)
        environment = text(data["environment"], "environment", 2000) if data.get("environment") is not None else None
        command = text(data["command_text"], "command_text", 2000) if data.get("command_text") is not None else None
        patch = sha(data["reproduction_patch_sha"], "reproduction_patch_sha") if data.get("reproduction_patch_sha") is not None else None
        ident = new_id("observation")
        conn.execute("""INSERT INTO observations (id,repository_id,run_id,provenance,kind,outcome,summary,details,limitations,environment,command_text,reproduction_patch_sha,artifact_id,created_at)
            VALUES (?,?,?,'agent_reported',?,?,?,?,?,?,?,?,?,?)""", (ident, scope.repository_id, run["id"], kind, outcome, summary, details, limits, environment, command, patch, artifact, now()))
        return {"state": "recorded", "observation_id": ident, "provenance": "agent_reported", "artifact_id": artifact,
                "snapshot": {"head_sha": run["head_sha"], "base_sha": run["base_sha"]}}

    @staticmethod
    def _assessment(conn, scope, run, data):
        finding = conn.execute("SELECT * FROM findings WHERE repository_id=? AND id=?", (scope.repository_id, data["finding_id"])).fetchone()
        if finding is None:
            raise LedgerError("scope_not_found", "Finding is not in this repository")
        origin = conn.execute("SELECT review_id FROM runs WHERE id=? AND repository_id=?", (finding["origin_run_id"], scope.repository_id)).fetchone()
        if origin is None or origin["review_id"] != run["review_id"]:
            raise LedgerError("scope_not_found", "Finding belongs to a different PR; propose a separate claim for this case")
        state = choice(data["state"], "state", set(Assessment))
        basis = choice(data["basis"], "basis", {"inspection", "behavior", "none"})
        rationale, limits = text(data["rationale"], "rationale"), text(data["limitations"], "limitations")
        ids = strings(data["observation_ids"], "observation_ids", 20, 64)
        observations = []
        for ident in ids:
            obs = conn.execute("SELECT * FROM observations WHERE repository_id=? AND run_id=? AND id=? AND valid=1", (scope.repository_id, run["id"], ident)).fetchone()
            if obs is None:
                raise LedgerError("ineligible_evidence", "Assessments require valid observations from this exact run")
            observations.append(obs)
        if state in ("supported", "refuted"):
            if not observations or basis == "none" or any(o["outcome"] not in ELIGIBLE_OUTCOMES for o in observations):
                raise LedgerError("ineligible_evidence", "Infrastructure, timeout, skipped, incomplete or absent reports cannot support/refute a finding")
            if basis == "behavior" and any(o["outcome"] == "inspection" or o["kind"] != "test" or not (o["environment"] or "").strip() or not o["details"].strip() for o in observations):
                raise LedgerError("incomplete_behavior_report", "Behavior assessment needs test details and environment; inspection remains inspection")
        resolution = data.get("resolution")
        if resolution is not None:
            fields(resolution, {"original_observation_id", "verification_observation_id", "original_behavior", "limitations"},
                   {"original_observation_id", "verification_observation_id", "original_behavior", "limitations"})
            if state != "refuted" or basis != "behavior":
                raise LedgerError("invalid_resolution", "Resolution requires a behavior-based refutation with original-behavior verification")
            original = conn.execute("SELECT * FROM observations WHERE repository_id=? AND id=? AND valid=1", (scope.repository_id, resolution["original_observation_id"])).fetchone()
            verified = next((o for o in observations if o["id"] == resolution["verification_observation_id"]), None)
            if original is None or original["outcome"] != "behavior_failure" or verified is None or verified["outcome"] != "behavior_passed":
                raise LedgerError("invalid_resolution", "Resolution must link original reported failure and current passing behavioral verification")
            linked = conn.execute("""SELECT 1 FROM assessment_sources s JOIN assessments a ON a.id=s.assessment_id
                WHERE a.repository_id=? AND a.finding_id=? AND a.state='supported' AND s.observation_id=? LIMIT 1""",
                                  (scope.repository_id, finding["id"], original["id"])).fetchone()
            if linked is None:
                raise LedgerError("invalid_resolution", "The original failure must be a recorded source of this finding's supported assessment")
            if original["id"] not in ids and len(ids) >= 20:
                raise LedgerError("resource_limit", "Resolution source references exceed the 20-source budget")
            text(resolution["original_behavior"], "original_behavior")
            text(resolution["limitations"], "resolution limitations")
        # Preserve old evaluations; only the new evaluation is current for this finding/run.
        conn.execute("UPDATE assessments SET freshness='historical' WHERE repository_id=? AND finding_id=? AND run_id=?", (scope.repository_id, finding["id"], run["id"]))
        ident = new_id("assessment")
        conn.execute("INSERT INTO assessments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (ident, scope.repository_id, finding["id"], run["id"], state, "current", basis, rationale, limits, canonical(resolution) if resolution else None, now()))
        for source in ids:
            conn.execute("INSERT INTO assessment_sources VALUES (?,?,?,?)", (scope.repository_id, ident, source, "contradicts" if state == "refuted" else "supports"))
        if resolution and resolution["original_observation_id"] not in ids:
            conn.execute("INSERT INTO assessment_sources VALUES (?,?,?,?)", (scope.repository_id, ident, resolution["original_observation_id"], "supports"))
        return {"state": "assessed", "assessment_id": ident, "assessment": state, "freshness": "current", "basis": basis,
                "provenance_notice": "Schema validation does not establish semantic truth."}

    @staticmethod
    def invalidate(conn, scope, observation_id, reason, actor):
        conn.execute("UPDATE observations SET valid=0,invalid_reason=? WHERE repository_id=? AND id=?", (reason, scope.repository_id, observation_id))
        conn.execute("""UPDATE assessments SET freshness='needs_revalidation' WHERE repository_id=? AND freshness='current'
            AND id IN (SELECT assessment_id FROM assessment_sources WHERE repository_id=? AND observation_id=?)""", (scope.repository_id, scope.repository_id, observation_id))
        conn.execute("""UPDATE lesson_versions SET state='suspended',reason=? WHERE repository_id=? AND state='active'
            AND id IN (SELECT version_id FROM lesson_sources WHERE repository_id=? AND observation_id=?)""", ("Source invalidated: " + reason, scope.repository_id, scope.repository_id, observation_id))
        conn.execute("""UPDATE critic_runs SET freshness='needs_revalidation' WHERE repository_id=? AND freshness='current'
            AND id IN (SELECT critic_run_id FROM critic_sources WHERE repository_id=? AND observation_id=?)""", (scope.repository_id, scope.repository_id, observation_id))
        conn.execute("""UPDATE critic_assessments SET freshness='needs_revalidation' WHERE repository_id=? AND freshness='current'
            AND (id IN (SELECT assessment_id FROM critic_assessment_sources WHERE repository_id=? AND observation_id=?)
            OR objection_id IN (SELECT o.id FROM critic_objections o JOIN critic_items i ON i.id=o.item_id
                JOIN critic_runs r ON r.id=i.critic_run_id WHERE r.repository_id=? AND r.freshness!='current'))""",
                     (scope.repository_id, scope.repository_id, observation_id, scope.repository_id))
        conn.execute("""UPDATE lesson_versions SET state='suspended',reason=? WHERE repository_id=? AND state='active'
            AND id IN (SELECT l.version_id FROM critic_lesson_links l JOIN critic_assessments a ON a.id=l.assessment_id
                       WHERE l.repository_id=? AND a.freshness!='current')""",
                     ("Critic verification requires revalidation: " + reason, scope.repository_id, scope.repository_id))
        Store.audit(conn, scope, observation_id, "invalidate_observation", actor, {"reason": reason})

    def operator_transfer(self, repository: str, run_id: str, session_id: str | None,
                          expected_generation: int, reason: str, request_key: str):
        scope = self.scope(repository)
        integer(expected_generation, "expected_generation", 1, 2**63 - 1)
        if session_id is not None:
            text(session_id, "operator-specified session", 256)
        text(reason, "reason")
        def write(conn):
            run = self.store.run(conn, scope, run_id)
            if run["generation"] != expected_generation or run["status"] not in ("active", "paused"):
                raise LedgerError("ownership_conflict", "Ownership generation or run state changed")
            conn.execute("UPDATE runs SET owner_session=?,generation=generation+1,updated_at=? WHERE id=?", (session_id, now(), run_id))
            self.store.audit(conn, scope, run_id, "operator_transfer", "local_operator", {"reason": reason, "new_session": session_id})
            return {"state": "transferred" if session_id else "released", "run": self._run_view(self.store.run(conn, scope, run_id))}
        return self.store.write(scope, "operator_transfer", run_id, request_key,
                                {"session": session_id, "generation": expected_generation, "reason": reason}, write)

    def operator_invalidate(self, repository: str, observation_id: str, reason: str, request_key: str):
        scope = self.scope(repository)
        text(reason, "reason")
        def write(conn):
            obs = conn.execute("SELECT id FROM observations WHERE repository_id=? AND id=?", (scope.repository_id, observation_id)).fetchone()
            if obs is None:
                raise LedgerError("scope_not_found", "Observation is not in this repository")
            self.invalidate(conn, scope, observation_id, reason, "local_operator")
            return {"state": "invalidated", "observation_id": observation_id}
        return self.store.write(scope, "operator_invalidate", observation_id, request_key, {"reason": reason}, write)
