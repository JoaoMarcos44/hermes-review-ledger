# Opt-in deterministic context representation

V1 (package 1.0.1) extends the existing optional `Context` and `Usage` implementation.
Compression itself adds no migration and does not rewrite original records, skills or logs. The integrated V1 build uses schema 5 for external review references; migrations 001–004 remain unchanged.
The optional critic remains disabled by default and no inference is introduced.

## Enable and call

The operator enables both `context_enabled: true` and
`compression_enabled: true`. `optional_skills_enabled` remains independent.
Existing calls without projection arguments retain the legacy full response.
No extra model tools are registered.

Use `ledger_context` with its existing repository/run/action fields and:

- `mode: compact`: deterministic structural representation of selected fields.
- `mode: reference`: identities, sizes and detail instructions, not loaded bodies.
- `mode: full`: legacy by default; supplying a projection budget explicitly uses
  the opt-in, versioned full representation.
- `max_chars`: the existing whole-response character cap, 2,000–64,000.
- `max_bytes`: an independent UTF-8 cap, 2,000–256,000.
- `max_tokens`: optional local token cap, 1–256,000.
- `strict_tokens`: require an available local counter and explicit token cap.

Operator ceilings are `bundle_budget`, `bundle_byte_budget` and `bundle_token_budget`; a caller may
request smaller caps, never larger ones. The default native host does not expose
an exact prepared tokenizer. A non-strict token request therefore reports
`token_budget_verified: false` while enforcing characters/bytes. Strict mode
returns `token_count_unavailable`; it never silently substitutes a heuristic.

All limits include the actual canonical JSON envelope, escaping, notices,
references and the representation digest. The adapter sends exactly that
canonical string for projected responses. Error responses are operation errors,
not successfully budgeted context bundles. If even necessary metadata cannot fit,
`resource_limit` is returned rather than partially truncating an instruction.

`resume` inherits the requested mode and limits from the exact manifest.
Changing the representation or compactor policy requires a new explicit prepare.
Resume never assumes content survived another session or host compaction.
`detail` repeats scope, exact HEAD/base and literal stored fields. An oversized
unit becomes a reference; select a complete semantic section to retrieve it.
A reference alone does not establish that the source was read.

## Structural and authorization guarantees

The compactor factors only explicitly allowlisted fields which are present and
exactly identical, including JSON type, across records of one kind in this
response. `representation.common_fields` applies only to that kind in this
response. Merge those fields into each record's `content` to reconstruct all
selected values. No external dictionary or private decoding convention is used.

Negations, conditions, exclusions, units, numbers, chronology, symbols, code,
paths, patches, messages and free text remain literal. Complete skills are
selected as whole units or referenced. `false`, `0`, missing and `null` remain
distinct. Events are not coalesced: order, multiplicity and source identities are
preserved. Contradictory and historical assessments remain separate records.
The existing bounded candidate window remains explicit; this is not global
ranking or a guarantee of sufficient context for a review.

A compact envelope which is larger than the equivalent full selected-field
view falls back to that full view. No fields are discarded to manufacture a
percentage. Metadata can outweigh savings, especially for small cases.

Collection uses a coherent read snapshot. Transformation and counting happen
outside the write transaction. A short recheck verifies scope, run state and
selected source eligibility before recording a manifest. Invalid observations
and dependent assessments are omitted from fresh compact context. Invalidated
lessons, disabled skills and changed source content cannot be served through a
pinned compact manifest. A change after the recheck and host receipt remains a
possible race; receipt and residency are not claimed.

Source `sha256` retains the existing selected-source-field hash semantics.
Where added, `stored_source_sha256` hashes the full canonical backend source view
at collection, separately from the representation `content_digest`. The latter
hashes the complete rendered representation except its own digest field. Counts
include that digest field. Compactor policy is stored on the derived manifest,
never substituted into the original skill version or code snapshot.

## Optional local token counter

The standard path imports no tokenizer and requires no compiler, GPU, extra
service or model. An integrating trusted Python caller may explicitly construct
`TiktokenCounter` with an already locally prepared encoding, then pass it through
`Context(..., counter=counter, token_budget=...)`. The Ledger does not call
`get_encoding`, pick an encoding from a guessed model name, download encoding
data, mutate host globals, or inspect secrets. Preparing an encoding and its
local data is an explicit operator responsibility outside normal plugin import,
registration and invocation. An installed tiktoken may have platform-specific
binary requirements; it is not a mandatory Ledger dependency.

A manually chosen encoding measures precisely that encoding's string tokens,
not a different provider/model or total inference framing. Special-token-looking
strings are counted literally using the prepared encoding's ordinary-text API.
Malformed Unicode is rejected clearly. Counter identities include library,
version and encoding; changing identity within a count session is rejected.
The cache is per-request/per-instance, capped by entries and retained UTF-8 input
bytes, and never persists across profiles or sessions. No content projection
cache is added, so cache hits never bypass authorization checks.

Tests use synthetic exact counters to validate budget mechanisms. When tiktoken
is absent, the suite verifies that explicit absence and does not claim to have
validated real tiktoken behavior or a provider's actual accounting.

## Metrics and reproducible offline evaluation

The existing Usage surface records bounded numeric diagnostics in versioned
context manifest request metadata. It stores no conversation dumps or source
bodies for measurement. Its local report separates candidate-set size, actual
rendered output, and same-selected-fields structural measurements. Telemetry
failure cannot turn a completed operation into an apparent failed evidence write.

Run the synthetic benchmark with the repository's Python environment:

    python scripts/benchmark_compression.py --help

Experiment A compares the same selected records and fields under full and
compact representations. Experiment B compares the same task and eligibility
rules at the same budget through prepare, necessary detail and resume. Report
all responses in the trajectory, not just the first response. These scripted
paths do not simulate model comprehension. Character and UTF-8 byte counts are
exact; token counts remain unavailable without an explicitly supplied local
counter. Cold and repeated measurements and a separately instrumented tracemalloc
peak are distinguished; tracemalloc is not total process RSS.

Reduction of representation, reduction of observed context, and real inference
savings are different outcomes. Provider cost, whole-session tokens, cache hits,
and semantic equivalence for arbitrary models are not measured here.

## Manual comprehension pilot (not executed automatically)

1. Independently prepare review questions and answer keys covering qualifications,
   contradictory findings, absent evidence, oversized skills and historical data.
2. Freeze identical authorized records and candidate windows. Randomize whether
   the reviewer sees the full or compact variant without exposing the answer key.
3. Evaluate whether answers preserve conditions/exclusions and cite the right
   source, distinguishing unknown from refuted and skipped from passed.
4. Include necessary detail/resume calls and record total context, failures and
   latency. Evaluate answers independently of the renderer's own tests.
5. Use real provider usage only when the host supplies a trustworthy contract.
   Paid inference requires separate authorization; no successful pilot is claimed
   from synthetic tests alone.

## Recorded local synthetic result

On 2026-10-07, eight fixtures at the same 6,000-character cap, with two timed
iterations and a separate tracemalloc pass, produced:

- Experiment A, identical records/fields: median UTF-8 representation reduction
  **4.69%**; high-repetition case **39.79%**. Some cases grew instead.
- Experiment B, complete scripted request/response trajectories: median UTF-8
  context **increase of 15.05%**. All eight compact trajectories grew, from
  approximately 8.09% to 29.13%, despite structural savings in some responses.
- All eight fixtures recovered their required protected fields and respected
  response character caps. This is a structural/scripted oracle, not independent
  evaluation of a model's understanding.
- Local tokenizer/provider tokens, billing, cache savings and real inference
  savings were unavailable. No paid inference or model comprehension pilot ran.

These results support leaving compression explicitly opt-in. They do not justify
turning it on by default or advertising overall token savings. The full-view
fallback compares equivalent selected fields and metadata; required provenance
and completeness metadata can still make an opted-in trajectory larger than the
legacy interface. Benchmark JSON retains all cases, distributions, timing and
separate memory measurements; reproduce it with the command above rather than
extrapolating the best case to a real review.
