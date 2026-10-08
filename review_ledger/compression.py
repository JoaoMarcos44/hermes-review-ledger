"""Deterministic derived views; literal values and source records never change.

Only explicitly listed metadata may be factored. Common fields apply to every
record of that kind in this response, never another response or session. This is
structural preservation of selected fields, not universal semantic equivalence.
"""
from __future__ import annotations

from copy import deepcopy
from .models import canonical, digest

POLICY_VERSION = "1"
MAX_CANDIDATES = 96
# Conditions, code, free text, findings and instruction bodies stay literal.
COMMON_FIELDS = {
    "observation": ("run_id", "provenance", "kind", "outcome", "environment", "limitations", "valid"),
    "assessment": ("run_id", "freshness", "basis", "limitations"),
    "finding": ("repository_id", "origin_run_id"),
    "lesson": ("evidence_notice",),
    "skill": (), "snapshot_completeness": (),
}


def project_records(records):
    """Factor exact typed values, retaining order, multiplicity, and all sources."""
    projected = deepcopy(records)
    common = {}
    for kind, allowed in COMMON_FIELDS.items():
        group = [r for r in projected if r["kind"] == kind]
        if len(group) < 2:
            continue
        shared = {}
        for key in allowed:
            if not all(key in row["content"] for row in group):
                continue
            value = group[0]["content"][key]
            # Python equality confuses 0/False; canonical JSON does not.
            if all(canonical(row["content"][key]) == canonical(value) for row in group):
                shared[key] = value
        if shared:
            common[kind] = shared
            for row in group:
                for key in shared:
                    del row["content"][key]
    return projected, common


def expand_records(records, common_fields):
    """Reconstruct complete selected fields for offline structural verification."""
    result = deepcopy(records)
    for record in result:
        record["content"] = {**deepcopy(common_fields.get(record["kind"], {})), **record["content"]}
    return result


def project_bundle(bundle, mode="compact"):
    """Produce one JSON representation, never duplicate JSON and prose bodies."""
    result = deepcopy(bundle)
    result.pop("_snapshot_key", None)
    result.pop("_reference_selection", None)
    if mode == "compact":
        result["records"], common = project_records(result["records"])
    else:
        common = {}
    result["representation"] = {
        "mode": mode, "policy_version": POLICY_VERSION,
        "common_fields": common,
        "common_scope": "All records of the matching kind in this response only",
        "completeness": "Complete selected fields; referenced source bodies are not loaded",
        "task_sufficiency": "unknown",
    }
    return seal(result)


def seal(result):
    """Hash actual derived JSON excluding its own digest; count includes the hash."""
    result["content_digest"] = digest({k: v for k, v in result.items() if k != "content_digest"})
    return result


def record_reference(record, reason="budget"):
    content = canonical(record["content"])
    return {"kind": record["kind"], "sources": deepcopy(record["sources"]),
            "state": "not_loaded", "reason": reason,
            "required_chars": len(content), "required_bytes": len(content.encode("utf-8")),
            "detail": {"action": "detail", "kind": record["kind"], "record_id": record["sources"][0]["id"]}}


def select_bundle(bundle, *, mode, fits):
    """Bounded whole-unit greedy selection, with exact-value full-view fallback.

    One pass, at most MAX_CANDIDATES. Selection preserves existing order and the
    same bounded retrieval window, including contradictory/historical records.
    No content cache or cross-session residency assumptions are introduced.
    """
    source = deepcopy(bundle)
    records = source.pop("records")
    source["records"] = []
    # External-reference entries are already body-free complete units. They
    # still consume the same response budget and must not bypass selection.
    external_refs = [ref for ref in source["references"] if ref["kind"] == "external_reference"]
    source["references"] = [ref for ref in source["references"] if ref["kind"] != "external_reference"]
    dropped_refs = 0
    fallback_count = 0
    if mode == "reference" and source.get("checkpoint") is not None:
        note = source.pop("checkpoint")
        source["checkpoint"] = None
        source["references"].append({"kind": "run", "id": source["run_id"], "state": "not_loaded",
            "required_chars": len(canonical(note)), "required_bytes": len(canonical(note).encode("utf-8")),
            "detail": {"action": "detail", "kind": "run", "record_id": source["run_id"]}})

    def render(candidate):
        nonlocal fallback_count
        projected = project_bundle(candidate, mode)
        if mode == "compact":
            full = project_bundle(candidate, "full")
            # Compare the real envelopes with equivalent completeness metadata.
            if len(canonical(full).encode("utf-8")) < len(canonical(projected).encode("utf-8")):
                fallback_count += 1
                projected = full
        return projected

    for record in records[:MAX_CANDIDATES]:
        if mode != "reference":
            source["records"].append(record)
            if fits(render(source)):
                continue
            source["records"].pop()
        ref = record_reference(record, "reference_mode" if mode == "reference" else "budget")
        source["references"].append(ref)
        if not fits(render(source)):
            source["references"].pop()
            dropped_refs += 1
    if len(records) > MAX_CANDIDATES:
        dropped_refs += len(records) - MAX_CANDIDATES
    first_omitted_external = None
    for offset, ref in enumerate(external_refs[:MAX_CANDIDATES]):
        source["references"].append(ref)
        if not fits(render(source)):
            source["references"].pop()
            dropped_refs += 1
            if first_omitted_external is None:
                first_omitted_external = source.get("_reference_selection", {}).get("offset", 0) + offset
    dropped_refs += max(0, len(external_refs) - MAX_CANDIDATES)
    external_omission = None
    if first_omitted_external is not None:
        external_omission = {"kind": "external_reference", "reason": "reference metadata budget",
            "next_reference_offset": first_omitted_external,
            "detail": "Prepare at this reference_offset with a larger authorized cap"}
        source["omitted"].append(external_omission)
    reference_omission = None
    if dropped_refs:
        reference_omission = {"reason": "reference metadata budget", "count": dropped_refs,
                             "detail": "Narrow the question or use authorized detail"}
        source["omitted"].append(reference_omission)
    source["selection"]["selected_count"] = len(source["records"])
    result = render(source)
    if not fits(result):
        # Adding omission diagnostics may consume reserve. Drop complete units,
        # never a sentence, code line, JSON prefix, or necessary qualification.
        # Newly appended external metadata yields first, before earlier records.
        # Otherwise a reference-only page could fail despite fitting one entry.
        while not fits(result):
            position = next((i for i in range(len(source["references"]) - 1, -1, -1)
                             if source["references"][i]["kind"] == "external_reference"), None)
            if position is None:
                break
            removed = source["references"].pop(position)
            dropped_refs += 1
            offset = source.get("_reference_selection", {}).get("offset", 0) + external_refs.index(removed)
            if external_omission is None:
                external_omission = {"kind": "external_reference", "reason": "reference metadata budget",
                    "next_reference_offset": offset,
                    "detail": "Prepare at this reference_offset with a larger authorized cap"}
                source["omitted"].append(external_omission)
            else:
                external_omission["next_reference_offset"] = min(external_omission["next_reference_offset"], offset)
            if reference_omission is None:
                reference_omission = {"reason": "reference metadata budget", "count": dropped_refs,
                                     "detail": "Narrow the question or use authorized detail"}
                source["omitted"].append(reference_omission)
            else:
                reference_omission["count"] = dropped_refs
            result = render(source)
        while source["records"] and not fits(result):
            source["records"].pop()
            source["omitted"].append({"reason": "final envelope budget", "count": 1})
            source["selection"]["selected_count"] = len(source["records"])
            result = render(source)
        if not fits(result):
            return None, {"fallback_count": 0, "full_view_comparisons": fallback_count, "reference_metadata_omitted": dropped_refs}
    return result, {"fallback_count": int(mode == "compact" and result["representation"]["mode"] == "full"),
                    "full_view_comparisons": fallback_count, "reference_metadata_omitted": dropped_refs}
