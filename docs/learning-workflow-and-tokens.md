# Learning Workflow and Token Reports

Operator-facing guide for the cross-run workflow queue and the read-only
token analyzer. Both surfaces are diagnostic: they surface state and correlate
host usage, but they do not author, approve, or activate anything.

## CLI reference

```sh
hermes review-ledger tokens owner/repo [--run-id ID] [--session-id ID]
                                      [--include-workers] [--format human|json]
```

- `owner/repo` — repository scope (required).
- `--run-id ID` — restrict to one ledger run.
- `--session-id ID` — restrict to one session (must correlate to the run or repo).
- `--include-workers` — expand to worker/subagent descendant sessions.
- `--format human|json` — output format (default: human).

This is an **operator-only** command. It is not a model tool, and no approval,
activation, or semantic edit is performed. The report is read-only.
## Token accounting model

### Main-task counters (per session)

| Field | Meaning |
|---|---|
| `input_tokens` | Uncached prompt tokens sent to the provider. |
| `cache_read_tokens` | Prompt tokens served from cache. |
| `cache_write_tokens` | Tokens written to cache. |
| `output_tokens` | Generated tokens (includes reasoning). |
| `reasoning_tokens` | Reasoning subset of output; never added again. |

**Derived fields:**

```
prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
total_tokens  = prompt_tokens + output_tokens
```

Reasoning is a **subset** of output, never a separate additive component.

### Auxiliary counters (per task)

The host's `auxiliary_usage_by_task` already sums compression lineage. The
report normalizes field names (`api_calls` → `api_call_count`) and computes
the same `prompt`/`total` derivation per task.

### Combined totals

```
combined = main + auxiliary (deduplicated by lineage scope)
```

### Coverage

- `complete` — all expected counters present.
- `partial` — some counters are missing; affected aggregates stay `null`, not zero.
- `missing` — host metadata not found; all fields are `null`.

## Cost distinctions

The report separates four cost states:

| Status | Meaning |
|---|---|
| `actual` | `actual_cost_usd` is present (provider-reported). |
| `estimated` | Only `estimated_cost_usd` is present. |
| `included` | Estimate exists and `cost_status` is `included` or `subscription`. |
| `unknown` | Neither field present. |

**`null` is not `zero`.** A missing cost field means the host did not report
it, not that the cost was zero. No monetary savings, quota impact, or
currency conversion is inferred.
## Attribution and shared sessions

Token totals are **whole-session, non-marginal**. A session may host work
from multiple runs or other operators; the report cannot isolate Ledger-only
spend. The field `attribution: "whole_session_non_marginal"` is always present.

When a session correlates to multiple runs in the same repository, it is
listed under `shared_sessions` and flagged per-session. This is informational
only — no causal savings or quota allocation is computed.
## Host reader compatibility shim

The token report reads through `SessionDBMetadataReader`, which wraps the
host's `SessionDB(read_only=True)` public API. **No direct sqlite3 connection
to live state is used.**

### Scope identity for auxiliary lineage

The host's `auxiliary_usage_by_task` sums over a session's compression
lineage. The report's `auxiliary_lineage_ids` method intentionally matches
the same scope by calling two existing host helpers:

1. `_session_lineage_root_to_tip(session_id)` — walks the requested ancestor prefix.
2. `_is_explicit_branch_session(session_id)` — detects the branch cut.

It returns `chain[i:]` where `i` is the last branch-session index. This is a
**compatibility shim**, not a public API contract. The host's
`get_compression_lineage` also includes future tips and is **not** the usage
scope. If either helper is missing, the report fails with
`host_usage_unavailable` rather than guessing.

### Metadata allowlist

Only these fields are retained from the host session projection: `id`, `source`, `model`,
`billing_provider`, `billing_mode`, `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`,
`api_call_count`, `estimated_cost_usd`, `actual_cost_usd`, `cost_status`,
`cost_source`, `started_at`, `ended_at`, `last_active`,
`parent_session_id`, `message_count`, `archived`, `hidden`.

Prompt text, tool names, `model_config`, and previews are never retained or
emitted in this report. Host getters and branch checks may internally read fuller
rows; the adapter does not interpret prompts or forward those fields.
## Learning workflow view

The workflow view surfaces pending improvements and lesson-use feedback
across runs in the same repository. It is read-only and diagnostic.

### What it shows

- **Pending improvements** — proposals with status `review_needed` or
  `proposed`, with exact target version, eligibility check, and source
  outcome IDs (one compact ID plus an explicit omission flag; retrieve detail).
- **Pending lesson uses** — `applicable` or `uncertain` uses where
  `usefulness` is still `null`.
- **Active lessons** — count of `active` lesson versions in the repository.

### Completion preflight

`Ledger.run_action` with `action=complete` checks for pending
`applicable` or `uncertain` lesson uses that still lack a `usefulness`
outcome. If any exist, completion is **refused** under the existing
owner/generation transaction. This enforces procedure step 7.

Truthful `usefulness=inconclusive` with the actual `execution_block` permits closure. `not_applicable`
has an explicit disposition and does not trigger fabricated execution.
`pause` and `release` remain legal.

### Queue pagination

- `workflow_offset` — row offset for the improvement queue (default: 0).
- `detail_collection=workflow` with `detail_id` — fetch a specific
  improvement's detail view.
- Detail `offset` is a character position and `max_chars` caps the complete
  response. Omit `limit` and `history_offset`; concatenate and hash all pages.

### Next-step semantics

- `proposed` → `evaluate_exact_candidate_then_operator_review`
- `review_needed` → `investigate_diagnosis_then_propose_concrete_improvement`

A diagnosis is **not** an authored improvement or an approval. No candidate
is automatically created from diagnostic text.
## Safety boundaries

- No schema migration is performed.
- No credentials are returned or displayed.
- No new provider requests are made.
- No configuration toggles or automatic takeover.
- Host snapshot limits: 1000 runs, 1000 sessions, 20000 audit events.
- Compatible host APIs are required; unsupported hosts fail explicitly.
- Native evidence is Windows-only; CI was not run.
This change uses package **1.0.3**, protocol **4**, and schema **5**.
Durable owner/audit correlation excludes read-only observers and refused opens;
whole-session usage is not all Ledger invocations or a marginal cost measure.

## See also

- [Installation guide](installation.md)
- [Verification evidence](verification/learning-workflow-and-token-reports.md)
