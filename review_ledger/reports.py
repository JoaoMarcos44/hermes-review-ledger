"""Bounded, versioned exports with current source-eligibility checks."""
from __future__ import annotations

import json
import html
import re

from .learning import Learning
from .models import Actor, LedgerError, choice, integer
from .service import Ledger
from .storage import now


def export(ledger: Ledger, repository: str, run_id: str, actor: Actor, *, format: str,
           limit: int = 25, offset: int = 0, max_chars: int = 100_000) -> dict:
    choice(format, "format", {"json", "markdown"})
    integer(limit, "limit", 1, 50)
    integer(offset, "offset", 0, 1_000_000)
    integer(max_chars, "max_chars", 2000, 200_000)
    scope = ledger.scope(repository)
    with ledger.store.connect() as conn:
        # A short read transaction gives one consistent export snapshot, including revocations.
        conn.execute("BEGIN")
        try:
            run = ledger.store.run(conn, scope, run_id)
            snapshot = ledger._run_view(run, actor)
            comparison_snapshot = snapshot.pop("snapshot")
            collections = {}
            omitted = {}
            queries = {
                "observations": "SELECT * FROM observations WHERE repository_id=? AND run_id=? ORDER BY id LIMIT ? OFFSET ?",
                "assessments": "SELECT a.*,f.claim FROM assessments a JOIN findings f ON a.finding_id=f.id WHERE a.repository_id=? AND a.run_id=? ORDER BY a.created_at,a.id LIMIT ? OFFSET ?",
                "findings": "SELECT f.* FROM findings f WHERE f.repository_id=? AND (f.origin_run_id=? OR EXISTS (SELECT 1 FROM assessments a WHERE a.finding_id=f.id AND a.run_id=?)) ORDER BY f.id LIMIT ? OFFSET ?",
                "lesson_uses": "SELECT * FROM lesson_uses WHERE repository_id=? AND run_id=? ORDER BY id LIMIT ? OFFSET ?",
            }
            for name, query in queries.items():
                params = (scope.repository_id, run_id, run_id, limit + 1, offset) if name == "findings" else (scope.repository_id, run_id, limit + 1, offset)
                rows = [dict(r) for r in conn.execute(query, params)]
                omitted[name] = len(rows) > limit
                collections[name] = rows[:limit]
            for observation in collections["observations"]:
                if observation["artifact_id"]:
                    observation["artifact"] = ledger.store.artifact_status(observation["artifact_id"])
            for assessment in collections["assessments"]:
                assessment["sources"] = [dict(r) for r in conn.execute("SELECT observation_id,relation FROM assessment_sources WHERE repository_id=? AND assessment_id=? ORDER BY observation_id LIMIT 20", (scope.repository_id, assessment["id"]))]
            lessons = []
            seen = set()
            for use in collections["lesson_uses"]:
                use["eligible_now"] = Learning.eligible(conn, scope, use["version_id"])
                use["revocation_notice"] = None if use["eligible_now"] else "This historical use is preserved; its exact version is no longer eligible for reuse."
                if use["version_id"] not in seen:
                    lessons.append(Learning.version(conn, scope, use["version_id"]))
                    seen.add(use["version_id"])
            output = {"export_format_version": 1, "generated_at": now(),
                      "scope": {"repository_id": scope.repository_id, "repository_name": scope.repository_name,
                                "profile_key": ledger.store.profile_key},
                      "run": snapshot, "snapshot": comparison_snapshot, **collections,
                      "lesson_versions": lessons, "omitted": omitted,
                      "next_offset": offset + limit if any(omitted.values()) else None,
                      "limitations": ["All observations are agent_reported, including inspection and behavioral reports.",
                                      "Schema validity does not verify semantic truth or execution.",
                                      "No findings, CI success, an exit code, or a 'fixed' message does not prove correctness.",
                                      "Exports are bounded snapshots, not synchronization or import formats."]}
            # Foreign-key references are retained even when their target is on another page.
            output["references"] = {"review_id": run["review_id"], "run_id": run_id,
                                    "skill_version": run["skill_version"], "skill_hash": run["skill_hash"]}
            content = json.dumps(output, indent=2, ensure_ascii=False) if format == "json" else markdown(output)
            if len(content) > max_chars:
                raise LedgerError("export_budget_exceeded", "Export exceeds its character budget; use a smaller page limit")
            conn.commit()
            return {"state": "exported", "format": format, "content": content, "chars": len(content),
                    "omitted": omitted, "next_offset": output["next_offset"]}
        except BaseException:
            conn.rollback()
            raise


def markdown(data: dict) -> str:
    run = data["run"]
    lines = ["# Review Ledger report", "", f"Repository: {_literal(data['scope']['repository_name'])}",
             f"Run: {run['id']} ({run['status']})", f"HEAD: {run['head_sha']}", f"Base: {run['base_sha']}",
             f"Comparison: {run['comparison']}", f"Skill: {run['skill_version']} / {run['skill_hash']}"]
    lines.extend(_snapshot_lines(data.get("snapshot") or {}))
    lines.extend(["", "## Supported current assessments"])
    current = [a for a in data["assessments"] if a["state"] == "supported" and a["freshness"] == "current"]
    lines.extend(_assessment_lines(current) or ["None recorded on this page. Zero supported findings is a valid result."])
    lines.extend(["", "## Hypotheses and other current assessments"])
    other = [a for a in data["assessments"] if a["state"] != "supported" and a["freshness"] == "current"]
    lines.extend(_assessment_lines(other) or ["None recorded on this page."])
    assessed = {a["finding_id"] for a in data["assessments"]}
    for finding in data["findings"]:
        if finding["id"] not in assessed:
            lines.append(f"- {finding['id']}: {_literal(finding['claim'])} (no assessment on this page; consult other pages before classifying)")
    lines.extend(["", "## Historical evidence and revalidation"])
    lines.extend(_assessment_lines([a for a in data["assessments"] if a["freshness"] != "current"]) or ["None recorded on this page."])
    lines.extend(["", "## Observations (agent-reported)"])
    for obs in data["observations"]:
        lines.extend([f"- {obs['id']}: {_literal(obs['summary'])}", f"  Outcome: {obs['outcome']}; method: {obs['kind']}; valid: {bool(obs['valid'])}",
                      f"  Limitations: {_literal(obs['limitations'])}"])
        if obs.get("artifact"):
            lines.append(f"  Artifact: {obs['artifact']['id']} ({obs['artifact']['state']})")
    lines.extend(["", "## Lessons used"])
    for use in data["lesson_uses"]:
        lines.append(f"- {use['version_id']}: applicability={use['applicability']}; usefulness={use['usefulness']}; behavioral result={use['behavioral_result']}; execution block={use['execution_block']}; eligible now={use['eligible_now']}")
    lines.extend(["", "## Limitations"] + [f"- {item}" for item in data["limitations"]])
    if any(data["omitted"].values()):
        lines.append(f"- More records omitted. Request next offset {data['next_offset']}.")
    return "\n".join(lines) + "\n"


def _snapshot_lines(snapshot: dict) -> list[str]:
    lines = ["", "## Captured-material completeness (initial snapshot)",
             "These facts describe the initial capture. Later agent-reported observations do not replace them.",
             "Complete file enumeration or captured patches do not establish review coverage or correctness."]
    for key, label in (("files_complete", "Files complete"), ("patches_complete", "Patches complete")):
        value = snapshot.get(key)
        rendered = str(value).lower() if isinstance(value, bool) else "unknown"
        lines.append(f"- {label}: {_literal(rendered)}")
    for key, label in (("total_files", "Total files"), ("omitted_files", "Omitted files")):
        value = snapshot.get(key)
        rendered = str(value) if type(value) is int and value >= 0 else "unknown"
        lines.append(f"- {label}: {_literal(rendered)}")
    reasons = snapshot.get("truncation_reasons")
    if isinstance(reasons, list):
        lines.append("- Truncation reasons:" if reasons else "- Truncation reasons: none recorded")
        lines.extend(f"  - {_literal(str(reason))}" for reason in reasons)
    else:
        lines.append("- Truncation reasons: unknown")
    lines.append("Missing capture facts remain unknown; no truncation reasons recorded does not imply completeness.")
    return lines


def _assessment_lines(rows):
    lines = []
    for item in rows:
        lines.extend([f"- {item['finding_id']}: {_literal(item['claim'])}",
                      f"  Assessment: {item['state']}; freshness: {item['freshness']}; basis: {item['basis']} (agent-reported)",
                      f"  Rationale: {_literal(item['rationale'])}", f"  Limitations: {_literal(item['limitations'])}",
                      "  Sources: " + ", ".join(s["observation_id"] for s in item["sources"])])
    return lines


def _literal(value: str) -> str:
    """Keep reported text inside its item, without Markdown or HTML structure."""
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|~=>-])", r"\\\1", escaped)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\n    ")
