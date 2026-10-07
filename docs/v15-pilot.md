# V1.5: controlled procedural improvement pilot

## Release identities and compatibility

V1.5 is a product milestone. The Python package/plugin is **0.3.0**, the immutable
procedure is **1**, the deterministic selection policy is **1**, and SQLite
schema is **3**, lineage `adaptive-v15`. These identities are independent.

This release starts at public main `99f8d98b59720847df410ac59d8e7782d9f51629`.
Open PRs 4, 5 and 6 are separate work and are not merged by this release.
In particular, PR5's experimental critic uses an incompatible schema 3.
This release rejects that database without mutation; it does not reinterpret it,
activate a critic, or claim that the two branches can be combined automatically.
A future integration needs an explicit migration and version reconciliation.
Schema 1 and genuine schema 2 databases migrate transactionally. Stop older
plugin sessions before upgrading, keep backups, and do not downgrade a migrated
profile to an older plugin. Existing profile and SQLite journal fences remain.

## Installation and defaults

Install this exact reviewed commit as a Python package with the repository's
existing `pip install git+...@COMMIT` workflow, then run
`hermes-review-ledger install --profile-dir /absolute/existing/profile`.
The native copied plugin, wheel and sdist include the same canonical procedure,
Python modules, migrations and bundled qualified skill
`review-ledger:review-ledger`. The canonical procedure lives only in
`review_ledger/resources/protocol.md`; `protocol.py` reads and hashes those bytes.

PyYAML >=6.0.2,<7 is the sole new runtime dependency, used for safe YAML
frontmatter parsing. The explicit profile copier does not install dependencies
into Hermes: ensure that reviewed dependency is available in the selected Hermes
Python environment. No tokenizer/model is installed or downloaded on startup.

All four pilot switches default to false. Eight tool names are registered to
match the manifest and native Plugin Doctor; `ledger_context` fails closed until
context is enabled. The seven original tools and legacy budget/pagination
contracts remain available. Migration does not enable any feature, import a
skill, widen a repository allowlist, start inference or alter personal profiles.

Merge these explicit settings under `plugins.entries.review-ledger.settings`:

```yaml
context_enabled: true
optional_skills_enabled: true
improvements_enabled: true
usage_enabled: true
bundle_budget: 12000
```

The existing `authorized_repositories` remains mandatory. `bundle_budget` is a
full serialized response character cap (2,000–64,000); it is not tokens or UTF-8
bytes. A model may request a smaller cap, never a larger effective cap. Existing
`context_budget` retains legacy lesson-body semantics. Registry changes, lesson
approval and pilot switches are not hashed into snapshot identity. Core bundled
skill/package compatibility still uses the existing version/hash fences.

## Operator workflow

Commands below are subcommands of `hermes review-ledger` in the selected profile;
use the CLI name Hermes reports if it qualifies it differently.

1. Open an authorized review normally so its scoped repository exists.
2. Inspect one chosen local supplementary package, then explicitly import it:

```sh
hermes review-ledger skill-add OWNER/REPO team/review-checks /absolute/local/package --reference references/checks.md --approve-import
hermes review-ledger skill-list OWNER/REPO
hermes review-ledger skill-inspect OWNER/REPO SKILL_VERSION_ID
hermes review-ledger skill-enable OWNER/REPO SKILL_VERSION_ID --reason 'Reviewed exact text for this repository' --request-key enable-1
```

`skill-update` takes the same chosen package and qualified ID and creates a new
immutable disabled version. Source edits never change approved bytes.
`skill-disable` with exact version, reason and retry key revokes it. Every import
is repository-local; no global import or cross-repository learned-data sharing
is implemented. Importing identical bytes is idempotent and never reenables a
revoked version. Fresh context selects the latest enabled version; pinned resume
preserves the selected exact version until explicit refresh or revocation.

Supported content is UTF-8 SKILL.md plus up to 15 explicitly named `.md`, `.txt`
or `.rst` references, at most 64 KiB each, 256 KiB total, path depth 4. Safe YAML
rejects duplicate keys, aliases, excessive structure and unsupported executable
dependencies. No traversal, links, remote fetches, scripts, templates or implicit
permissions are accepted. `allowed-tools` is inert metadata. Frontmatter supports
name, description, license, compatibility, string metadata, and structured
applicability (repositories, phases, tags, symbols, conditions, exclusions).
Known structured mismatches exclude content; missing values and prose conditions
remain uncertain. Constraints use OR within a field and AND between fields.

Descriptor-relative no-follow imports are supported on the tested Linux runtime.
Where that OS primitive is unavailable, registration fails closed. This is not a
claim of native Windows optional-import support. Validation does not prove that
natural-language instructions are safe from prompt injection or licensed for
redistribution. Attribution remains attached; imported prose is not evidence.

3. Inspect outcome diagnoses and exact revisions:

```sh
hermes review-ledger improvement-list OWNER/REPO RUN_ID
hermes review-ledger improvement-inspect OWNER/REPO IMPROVEMENT_ID
hermes review-ledger inspect OWNER/REPO CANDIDATE_VERSION_ID
hermes review-ledger approve OWNER/REPO CANDIDATE_VERSION_ID --reason 'Reviewed exact diff and evaluation' --request-key approve-2
```

Existing `restrict` or `suspend` rejects a proposed version; a new revision is
needed before later approval. Exact target changes, invalidated supporting
observations or stale source outcomes block approval. Repeated approval uses the
same idempotency key and cannot double-apply a change. The source observations,
outcomes, proposed field diff and evaluation references remain inspectable.

4. Inspect measurement and backup:

```sh
hermes review-ledger usage OWNER/REPO --run-id RUN_ID
hermes review-ledger backup
```

Imported text/resources are immutable canonical SQLite rows and are included in
the same SQLite backup. Missing/corrupt imported resources cause backup refusal.
The existing external observation artifact directory is still not bundled; retain
it separately where those optional artifacts matter. Backup validates a restored
SQLite copy. No personal profile is automatically restored or overwritten.

## Agent workflow and disclosure

`ledger_context prepare` takes repository, run_id, concrete query and optional
phase/tags/symbols/max_chars. One bounded response includes exact snapshot and
protocol identity, complete working units, recorded checkpoint, observations,
assessments/findings, uncertainties and explicit detail references. Discovery
cards identify optional skills and explicitly say their instructions are not
loaded. Working lessons preserve every condition, exclusion and verification
field without a model summary. Exact duplicate units keep all source identities;
contradictions remain separate.

Selection is deterministic within bounded SQL candidate windows. It does not
claim global ranking. Unknown applicability is never upgraded to an established
match. A `not_loaded` unit is not a complete strategy; use `detail` with its kind
and ID. Detail returns whole semantic records or complete selected sections and
computes integrity in the backend. If a whole section cannot fit, use the
supported legacy pagination path or ask the operator to approve a larger cap.
No mandatory condition is silently truncated. Every new context response,
including metadata and JSON escaping, stays within its configured cap.

`resume` accepts an optional manifest_id. A manifest stores lightweight exact
selection references, protocol hash, policy, request fingerprint and rendered
receipt, not a duplicate bundle. Explicit refresh is a new prepare request.
Revocation overrides historical pinning. Two sessions may prepare context but
cannot thereby acquire ownership, modify assessments or approve knowledge.
Unknown compaction/cache residency stays unknown; essential content is resent.
No missing current question or completed step is fabricated from absence.

Record exact-version use with the existing lesson tool, then report usefulness,
behavioral result and limitations. New optional feedback includes contribution,
applicability and supporting observation IDs. In pilot mode a concrete diagnosis
can yield one idempotent review-needed improvement. An agent can propose exact
replacement conditions, exclusions, tags, symbols or investigation order through
`ledger_lesson action=improve`. It cannot revise the procedure, budget, grants,
evidence requirements or imported source skill. Operator approval is separate.
Later eligible retrieval sees the approved version; invalidated knowledge is
excluded from new delivery/use and marked in historical exports.

## Measurement and evaluation boundaries

Local telemetry counts observed successful plugin calls, transient request and
rendered-response characters/UTF-8 bytes, response count, boundary latency,
candidates/selected/omitted units and repeated-response indicators. It stores no
full conversations, prompts, secrets or private absolute source paths. Actual
event identity deduplicates retries of telemetry itself; distinct deliveries of
identical text count separately. Similar text is not classified as wasted work.
Rendered output is not confirmed host delivery. Host-wide token/cache/reasoning,
provider attempt, cost, quota and total-review usage remain unavailable unless a
future verified correlated public host contract supplies them. No model-supplied
counter is treated as provider telemetry. Local counters are not subscription
usage or billing. Failure to record telemetry never changes a committed evidence
operation's result. Retention is 30 days/up to 10,000 events, separately from
evidence; operator totals cover only retained observations.

Run the entirely offline synthetic comparison:

```sh
python -m review_ledger.evaluation --budget 12000
python -m review_ledger.evaluation --budget 6000
```

The saved baseline is the real pre-V1.5 `Learning.recall/detail` behavior from
main `99f8d98`, not a fabricated database dump. Identical synthetic eligible
records/questions cover cold start, irrelevant guidance, long conditions and
crucial exclusions, exact duplicates, Unicode, contradictions, changed snapshots,
operator-restricted applicability and revoked sources. The harness measures
emitted characters/bytes, actual local latency, calls, bounds, expected retrieval
and reconstruction of critical fields. Model defect detection, missed findings,
false positives, token cost and reviewer quality are unavailable.

Initial mechanism runs showed **larger outputs**, because the new path supplies
procedure, snapshot and recorded investigation alongside guidance. At a 6,000
character cap the long-lesson case needed extra semantic-detail calls. These are
reported costs, not hidden or relabeled as savings. Stable selection/content is
deterministic; timing naturally varies. There is no promised percentage gain.

## Opt-in real-review protocol

Use three comparable conditions: A, the same authorized review workflow without
Ledger memory; B, persistent Ledger/resume with learned guidance disabled; C,
approved frozen memory plus V1.5 context. For B use the portable
`Context(..., guidance_enabled=False)` evaluation interface; the current Hermes
pilot exposes only the standard enabled-guidance path, so configure a separate
memory-free fixture/profile for B rather than claiming a new host setting.

Keep model, tools, goals, exact snapshot, mandatory checks and budget comparable.
Keep critic absent/disabled or identical across conditions. Separate development
and reserved case families; freeze memory before reserved evaluation. Historical
inputs must exclude future fixes, discussion and derived lessons. Related PRs or
repetitions are related cases, not independent support.

Predefine acceptable supported-claim precision, known-defect coverage, useful
refutations, inconclusive handling and required checks; a run doing no
investigation cannot win for zero findings. Have independent human adjudication
record correctness and effort. Collect full observable model usage, retries,
setup, learning and evaluation overhead before amortized comparison. Use
`1 - total_with / total_baseline` only for comparable units/coverage with positive
baseline. Output-size change remains distinct from total tokens/cost. Do not tune
repeatedly on sealed cases. No real model review or independently adjudicated
quality improvement is established by the offline mechanism tests.
