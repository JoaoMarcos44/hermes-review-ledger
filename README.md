# Hermes Review Ledger V1 (1.0.1)

<p align="center">
  <img src="docs/assets/ledger-pixel.gif" width="640" height="360" alt="Ledger, a green-haired, purple-suited pixel character, laughing and holding a playing card. Silent animation." />
</p>

**Meet Ledger.** The project's visual identity is a tribute to Heath Ledger and his portrayal of the Joker.
The animation is silent. [View the still image](docs/assets/ledger-pixel.png).


**V1 optional context pilot:** [installation, exact defaults, operator/agent workflow, version mapping, limitations and evaluation](docs/v15-pilot.md). The original workflow remains available with pilot features disabled.

A small native Hermes plugin for persistent, profile-local GitHub PR investigations and operator-approved conditional investigation lessons.

Hermes investigates code using its authorized host tools. Review Ledger records state and agent-reported evidence, coordinates a single writer, and helps retrieve relevant questions. It does not execute repository commands, independently observe tests, train models, or establish that a reviewer became better.

## Verified environment and public contract

The implementation was tested against the public NousResearch/hermes-agent source at commit `0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4`, runtime identity `git.0dbaf33`. Its `pyproject.toml` uses the placeholder version `0.0.0`; this project does not invent a minimum Hermes release number.

- Earlier native validation included Linux, Python 3.14.7, pytest 8.4.2 and SQLite 3.53.1; each new revision needs its own exact-commit run
- New databases now use rollback DELETE journaling; existing WAL databases require a known-fixed SQLite runtime as described below
- Real Hermes PluginManager, registry/model dispatcher, CLI registration, Plugin Doctor and skill loading exercised in temporary profiles
- Native runtime tests use Hermes' `in_process` plugin mode. In `plugins.isolation: host`, upstream skips CLI registration; the complete operator workflow in that mode is not supported or claimed by this V1.
- Core code also targets Python 3.12–3.14; the current Hermes checkout itself requires Python 3.14
- Missing public `ctx.state.data_dir` or trusted injected `session_id` fails closed
- No personal Hermes installation was upgraded or modified
- Native Linux/macOS/Windows validation is defined in `.github/workflows/native-tests.yml`; actual support evidence is the execution report for the exact commit and OS
- Network-shared databases, live GitHub authentication and actual model conversations have not been tested

Relevant public sources:
- [Native plugin documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/)
- [Skills guide](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills/)
- [Profile-local state directory implementation](https://github.com/NousResearch/hermes-agent/blob/0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4/hermes_cli/plugins_state.py#L134-L147)
- [Actual session-context injection](https://github.com/NousResearch/hermes-agent/blob/0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4/model_tools.py#L826-L844)
- [GitHub PR API](https://docs.github.com/en/rest/pulls/pulls)
- [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)
- [pytest temporary fixtures](https://docs.pytest.org/en/stable/how-to/tmp_path.html)

## Command-line installation

Follow the [complete install and update guide](docs/installation.md), including
clean virtual environments, offline dependencies, backups and rollback limits.
V1 is package/plugin **1.0.1**. Earlier 0.x builds were private development
iterations, not public releases; historical records retain their original labels.
Version **1.0.1** is a bugfix patch within Ledger V1: it distinguishes the corrected
package from 1.0.0 for installation and runtime snapshot identity.
SQLite schema is **5** after the additive frozen-reference increment; protocol is **2** after the deliberate lesson-policy update. Product identity does not reset either counter.

Frozen review/comment references reuse existing findings and the Context Engine;
see [review references](docs/review-references.md). Configurable automatic lesson
activation follows the separate [audited policy](docs/lesson-automation.md).

Choose an existing Hermes profile and stop its Hermes sessions before changing
plugin code. The installer requires an absolute directory containing an existing
`config.yaml`. It never guesses the default profile, creates a new profile, edits
configuration, upgrades Hermes, reads credentials, or enables the plugin.

### One command from a source checkout

With this private repository already checked out, run in its root directory:

```sh
python -m review_ledger install --profile-dir "/absolute/path/to/your/hermes-profile"
```

Use the Python interpreter for your environment: `python3` on many Linux/macOS
setups, or `py -3.14` on Windows if that is how your installed Python is selected.
The same command works with a quoted Windows path, for example:

```powershell
py -3.14 -m review_ledger install --profile-dir "C:\Users\YourName\Hermes Profiles\reviews"
```

This source-checkout installation is offline and uses the Python standard
library for installation; optional skill registration requires packaged PyYAML. Python 3.12–3.14 can run the installer; the pinned Hermes runtime used by
this project's integration tests itself requires Python 3.14.

### Install the Python command with pip

In a virtual environment, pip can obtain the package directly from the private
Git repository. Replace `COMMIT_SHA` with the exact reviewed 40-character commit:

```sh
python -m pip install "git+ssh://git@github.com/JoaoMarcos44/hermes-review-ledger.git@COMMIT_SHA"
hermes-review-ledger install --profile-dir "/absolute/path/to/your/hermes-profile"
```

Git and access to the private repository must already work through your own SSH
configuration. An existing authenticated HTTPS Git setup can instead use the
same repository's HTTPS URL. Never put a token/password in the URL, command,
conversation, or source. Nothing has been released on npm or PyPI. Do not use an
unqualified registry package with this name as a substitute.

Pip installs the command and a complete bundled native-plugin payload. It does
not select or modify a Hermes profile. The explicit `install --profile-dir` step
copies `plugin.yaml`, the root `__init__.py`, runtime modules, SQL migrations, versioned critic prompt, and
bundled skill together. If the console command is not on PATH, use
`python -m review_ledger` with that virtual environment's interpreter.

A locally built wheel can be installed with
`python -m pip install /path/to/hermes_review_ledger-1.0.1-py3-none-any.whl`.
For fully offline installation, prepare the runtime dependency wheels first and
use `--no-index --find-links /path/to/wheelhouse` (see the guide).
`--no-deps` is only appropriate when the required PyYAML is already installed. Wheels and source distributions are
built and checked in tests; they are not automatically published anywhere.

### Enable and configure the selected profile

Installation is only the code-copy step. Merge the following configuration into
that same profile's `config.yaml`, retaining its other settings. Enabling a
plugin loads local code with the user's permissions. `plugins.isolation` applies
to the entire profile: the complete V1 operator CLI requires `in_process`.
If a profile requires `host` isolation, choose a separate suitable profile. The
installer does not change this policy or silently grant activation.

Existing enabled/disabled entries and Hermes' own profile migration rules still
govern what loads at the next startup. Installing or upgrading code does not
reset that policy. Inspect the selected profile's configuration and plugin list
before restarting, particularly when the plugin was enabled previously.

```yaml
plugins:
  isolation: in_process
  enabled:
    - review-ledger
  entries:
    review-ledger:
      settings:
        authorized_repositories:
          - YOUR_OWNER/YOUR_REPOSITORY
        github_token_env: REVIEW_LEDGER_GITHUB_TOKEN
        context_budget: 6000
```

Use the standard Hermes environment configuration to supply `REVIEW_LEDGER_GITHUB_TOKEN`, or set it in the process environment before starting a single-profile Hermes process. The Hermes adapter resolves the explicitly configured name through the host's profile-scoped `get_secret` API. Gateway and desktop turns therefore use the served profile's credential scope. A required but absent scope is an explicit `secret_scope_required` error; the adapter does not fall back to the process environment itself. The standalone standard-library GitHub client retains an environment resolver unless another resolver is explicitly injected. Do not paste tokens into conversations, source files, tool arguments, or reports. No resolver searches Git, GitHub CLI, netrc, credential helpers, or other programs.

Use a token limited to the authorized repositories and read access required by GitHub's pull-request endpoints. Even public PR reads require an explicitly supplied token in this V1. Authorization is checked before network access and the stable repository ID returned by GitHub is checked against stored identity.

After reviewing configuration, use Hermes' normal profile selection or set
`HERMES_HOME` to the exact directory passed to the installer. In that same
profile run `hermes plugins enable review-ledger` if activation is intended;
this also removes an explicit disabled entry, which otherwise wins over enabled.
Then run `hermes plugins doctor /absolute/profile/plugins/review-ledger --ci`,
`hermes plugins validate /absolute/profile/plugins/review-ledger`, and
`hermes plugins list`. Start a fresh session and load the qualified skill
`review-ledger:review-ledger` through `skill_view`.

### Reinstall, upgrade, status and removal

Update the package in its virtual environment first, then run `upgrade` (or
`install`) for each intended profile. Neither command downloads packages.
The `upgrade` command also installs when the target is absent. Identical
content is a no-op. An upgrade replaces only an intact installer-owned code tree;
its manifest records every shipped file's SHA-256. Modified files, unrecognized
contents, manual installations, links/junctions, or malformed ownership metadata
are refused. There is no force-overwrite flag. Keep a manual installation aside
yourself after inspecting it; the installer does not adopt it automatically.

```sh
python -m review_ledger upgrade --profile-dir "/absolute/path/to/your/hermes-profile"
python -m review_ledger status --profile-dir "/absolute/path/to/your/hermes-profile"
python -m review_ledger uninstall --profile-dir "/absolute/path/to/your/hermes-profile"
```

Before uninstalling, disable the plugin through Hermes in that same profile and
stop its sessions. Uninstall removes only the installer-owned plugin code. It
retains configuration, repository allowlists, profile-local SQLite data, lessons,
artifacts and backups. Pip uninstall alone removes the Python command, not the
code copied to a profile. Remove profile code first if that is your intention.

The installer holds an OS-level per-profile lock and prepares complete code
outside Hermes' discovery directory. It journals the replacement and restores
the previous code if publication fails. A process interruption during replacement
is recovered before the next install/uninstall. It does not retain a successful
upgrade's old code as permanent version history. Use a reviewed earlier source
revision for an intentional code downgrade, subject to its data-schema support.

Close Hermes or other programs holding code files on Windows and retry a reported
sharing violation. If rollback itself is blocked, the error identifies the private
installer state containing the previous code; preserve it and rerun after fixing
access. Partial cleanup of unchanged owned files is retryable. Configuration and
plugin-data are never part of this transaction. The persistent lock/state files
are small management metadata, not ledger storage.

The lock descriptor must identify a regular file without physical aliases.
Existing lock contents are never initialized or rewritten; an empty lock left
by an interrupted first creation can still be locked and reused.

A hard termination while preparing a new tree can leave a hidden
`.review-ledger-prepare-*` directory beside the profile configuration. It is not
discovered by Hermes and does not block the next install. Inspect any abandoned
preparation before removing it manually. Unknown or edited files are never
silently cleaned up. Local, same-volume filesystems are required for rename
publication; network shares and sudden power-loss durability are not certified.
The lock coordinates this installer, not concurrent edits by other programs with
the same local user's permissions.

## What activates the plugin

There are three separate steps: installing its files, enabling discovery in a
chosen Hermes profile, and invoking a tool for an authorized review. Merely copying
this directory or loading its skill does not start a review.

- Hermes discovery calls the root `register(ctx)`, which registers nine tools (context and critic are disabled by default),
  one skill, and the operator CLI. Registration opens no database, reads no GitHub
  token, performs no HTTP requests, and starts no background task.
- `ledger_open` is the only network-facing tool. After trusted-session and
  repository checks, it reads a scoped review generation, fetches the authorized
  GitHub PR, and opens or reuses its run. The SQLite file is created lazily when a
  ledger operation first needs storage. No GitHub credentials are needed merely
  to register, read saved state, or use operator commands.
- `ledger_record`, `ledger_run`, `ledger_lesson`, `ledger_recall`, `ledger_status`,
  and `ledger_export` act only when explicitly called through the host. They do
  not schedule another invocation. A new commit on GitHub is noticed only during
  another `ledger_open`; there is no webhook, file watcher, or polling service.
- Lesson approval, restriction, suspension, ownership recovery, and backup are
  explicit local operator CLI commands. Describing a lesson as approved in a
  model argument cannot approve it.
- `pause` releases ownership and stores progress; `complete` ends that run.
  Nothing resumes itself when Hermes exits. A later trusted session must open or
  explicitly acquire an unowned run.

The learning cycle is persisted bookkeeping: observation, candidate, operator
approval, recall, exact-version use, reported result, and optional operator
revision or revocation. It does not train model weights, rewrite the skill, or run
a self-improvement loop. The separate optional critic is described below.

### Optional claim critic

Integrated version 1.0.1 includes one optional critic tool, `ledger_critic`, with `prepare`, `run`, `status` and
`assess` actions. The critic is disabled by default. Preparation and reading are
local; only explicit execution reaches the optional provider boundary. The
pinned Hermes host cannot publicly prove its complete effective egress/fallback
route before dispatch, so the real adapter returns `critic_route_unverifiable`
without calling a model, even when enabled. Normal review remains available.

Read [optional critic configuration, lifecycle and limits](docs/optional-critic.md)
before opting in. The default limits are 18,000 input characters, 2,000 output
tokens, 60 seconds and three logical attempts per run. These are initial caps,
not measured quality/cost guarantees. Consent must cover repository context and
the effective model/provider/account/fallback route. No credentials, permissions,
paid smoke, automatic debate or publication are authorized by enabling the plugin.

### Evidence-based feedback and superseded PRs

The bundled skill also covers contract-first criticism and preserving a review
when the base already satisfies the PR's purpose. These use existing observations,
assessments, completion notes and operator-approved lessons; they add no tools,
runtime entities, background execution or schema migration.

- [Manual feedback pilot](docs/review-feedback-pilot.md): independent claims,
  bounded critique, evidence-based adjudication, comparable baselines, and
  separate measurements of immediate review benefit and held-out lesson benefit.
- [Superseded PR investigations](docs/superseded-reviews.md): preserve scoped
  evidence without confusing obsolete proposed code with the internal superseded
  snapshot state, or treating old conclusions as a permanent skip instruction.

These guides do not claim measured reviewer improvement, token savings, or a
completed comparison between models. The plugin still assigns `agent_reported`
provenance and keeps one writer. Default manual mode requires separate operator
approval; explicit automatic mode follows the bounded audited policy.
Neither a critique nor lack of reproduction automatically invalidates evidence.
A changed skill hash creates a new comparison identity on the next open; this
documentation/skill update does not retroactively revalidate historical results.

Internal loops are bounded: GitHub transport has at most two retries after an
initial GET, pagination advances one page at a time under page/file caps, SQLite
writer acquisition has at most three attempts, and retrieval/export have explicit
page and character limits. These are not a universal wall-clock deadline for every
OS or network operation. The database and artifact directory have no aggregate
retention quota; the operator must monitor disk usage. A host model could choose
to call tools repeatedly, but that behavior is outside this plugin and subject to
the host's conversation and authorization controls.

## Tools and persistent review cycle

All model-facing operations return JSON with an explicit `state`; failures return `state: error` with a stable `error.code`. Unknown fields are rejected. The host provides profile and session identity separately from model arguments.

1. `ledger_open(repository, pull_number, request_key)` reads GitHub metadata/files, brackets pagination with matching full HEAD/base metadata, and atomically reuses or creates a run. It returns ownership, snapshot, completeness warnings, initial eligible lessons, and a small file preview.
2. `ledger_status(repository, run_id, limit, offset, history_offset, max_chars)` reads bounded state and a separately paginated `related_runs` index. Its complete compact JSON response defaults to at most 24,000 characters, configurable per request from 4,000 to 64,000. Large records become explicit detail references; smaller pages advance only past returned records. A second session can follow the run but cannot write over its owner. Alternatively, `ledger_status(repository, pull_number, limit, offset)` discovers this PR's runs without knowing any stored IDs. Supply exactly one of `run_id` or `pull_number`. The append-only run index uses SQLite insertion order, so clock changes do not change which run was recorded last. References expose revisions/status, not owner sessions or evidence; `latest_recorded` means the latest local record, not a fresh GitHub check.
3. `ledger_record(repository, run_id, generation, request_key, action, data)` records an observation, proposes a finding, records a snapshot-scoped assessment, or invalidates an owned observation.
4. `ledger_run(..., action="pause", note=...)` preserves state and releases ownership. A later trusted session can acquire an unowned run with its current generation. `complete` closes the investigation; another open can create a new run.
5. `ledger_export(repository, run_id, format="markdown" | "json", limit, offset, max_chars)` returns report text. Save it using an authorized host file tool if wanted. It does not publish anything.

Every write needs a scoped retry key. An identical retry returns its original operation receipt without duplicating records; changed content with the same key returns `idempotency_conflict`. Receipts describe the committed operation, not a new authorization or a guarantee of current ownership. Consult `ledger_status` for live state. Exact lesson-use retries additionally recheck revocation; an old receipt never authorizes reuse of a revoked lesson.

Retrieve a status reference through `ledger_status(repository, run_id,
detail_collection, detail_id, offset, max_chars)`. Collections are `run`,
`observation`, `assessment`, or `finding`. Omit `detail_id` for the run itself;
other collections require the ID returned by status. Detail mode rejects `limit`
and `history_offset`, and `offset` is a character position in the complete
canonical JSON. Follow `next_offset`, concatenate the `content` fragments, and
verify the reassembled UTF-8 SHA-256 against `content_sha256`. Require the same
digest on every page and restart if the underlying record changes. A reference
or partial page is not the complete evidence. Retrieval remains confined to the
selected repository and run.

The adapter captures an append-only per-review generation before HTTP work and
checks it in the writer transaction before replacing another snapshot. A delayed
fetch cannot silently supersede a run committed by another opener. A matching
active snapshot can still be reused; exact retries return their original receipts.
A `snapshot_conflict` requires fetching a fresh snapshot and retrying. This
freshness guard does not eliminate GitHub's separately documented ABA limitation.

Run identity includes complete HEAD/base revisions, GitHub PR comparison mode, relevant configuration, skill version and skill hash. New comparisons preserve history and do not inherit assessments as current. A reproduction patch revision, if reported, is a separate field from the reviewed HEAD.

Writes require the trusted session and monotonically increasing ownership generation. Ownership changes invalidate delayed writers. There is no timeout-based takeover, heartbeat, scheduler or background execution.

### Observation and finding example

An observation payload:

```json
{
  "kind": "inspection",
  "outcome": "inspection",
  "summary": "A specific path was inspected",
  "details": "Describe the inspected behavior and references",
  "limitations": "Inspection only; no behavior was executed"
}
```

All observations are assigned `agent_reported` by the application. A supplied `provenance`, `verified`, `approved` or administrative field is rejected.

Outcomes separate inspection, behavioral failure/pass, hypothesis refutation, infrastructure failure, timeout, skip and incomplete reporting. A command is inert text. A nonzero exit code does not prove a defect. Missing environment data stays absent.

A finding's `claim` is separate from assessments. Assessments use `unverified`, `supported`, `refuted`, `inconclusive` or `not_applicable`, and separately `current`, `needs_revalidation` or `historical` freshness. Supported inspection is labeled as inspection. Behavioral support/refutation requires reported details, environment and eligible current-run observations, but the schema cannot establish semantic truth.

Resolution requires an explicit link to the original behavioral failure and current passing verification of that original behavior, including limits. Green CI, “fixed”, removal/weakening/skipping of a test, or absence of a symptom alone does not prove resolution. Zero supported findings is valid.

The [original bundled skill](skills/review-ledger/SKILL.md) documents all operation-specific data fields.

## Conditional learning and operator approval

Learning remains within one repository and one resolved profile.

1. The agent proposes a conditional question with application conditions, exclusions, suggested investigation, tags/symbols and eligible observation sources. This is a `candidate`.
2. In default manual mode, a local operator inspects and explicitly approves that exact version. Explicit automatic mode can activate eligible proposals under the [bounded policy](docs/lesson-automation.md).
3. `ledger_recall` returns up to five eligible active versions or compact `lesson_references` with `version_id` and `required_context_chars`. Selection is deterministic, bounded, and based on terms, tags and symbols. SQL filtering and ranking both use Unicode NFC/casefold without changing stored text. No embeddings, probabilities or model calls are used.
4. Before applying one, `ledger_lesson(action="use")` records its exact version, applicability and explanation and rechecks eligibility.
5. `ledger_lesson(action="result")` records usefulness, behavioral result, execution blocking and a separate explanation. Refutation can be useful. Blocked execution is inconclusive. Unused lessons receive no invented outcome.
6. The operator can suspend or restrict a version. A revision creates a new candidate, preserving all prior versions and exact-version uses. Approving a newer version retires the old one without rewriting ongoing history.

### Retrieval within a fixed context budget

The configured `context_budget` caps every tool request even if the caller asks
for more. It covers serialized lesson/reference content; the bounded response
metadata is additional. A large lesson is represented by an ID and required
size rather than silently losing conditions or exclusions. Follow
`next_result_offset` with the same search and candidate-window `offset` until it
is null; only then advance `next_offset` to the next 200-candidate window.
Ranking is deterministic within each window. These are live pagination cursors,
not a frozen search snapshot; restart the search if operator changes occur.

Retrieve a reference through the same tool:
`ledger_recall(repository, run_id, version_id, offset=0, context_budget=...)`.
Do not include search fields or `limit` in detail mode. Here `offset` is a
character position in the complete canonical JSON. Each returned `content`
fragment respects the budget after JSON string escaping; follow `next_offset`
until null. Concatenate all fragments, require the same `content_sha256` on all
pages, and verify the SHA-256 of the reassembled UTF-8 content before reading its
conditions, exclusions and sources. A partial fragment is not a usable strategy.
Restart if its digest changes. Every page and subsequent exact-version use
rechecks eligibility; revocation/source invalidation is an explicit error.
The local operator's existing `inspect` command remains available for full
inspection, including inactive history.

Local operator examples (replace IDs with actual tool responses):

```sh
hermes review-ledger inspect OWNER/REPO version_ID
hermes review-ledger approve OWNER/REPO version_ID --reason "Evaluated sources, conditions and exclusions" --request-key approve-001
hermes review-ledger restrict OWNER/REPO version_ID --reason "Condition requires a narrower architecture" --request-key restrict-001
hermes review-ledger suspend OWNER/REPO version_ID --reason "Pending source revalidation" --request-key suspend-001
hermes review-ledger invalidate OWNER/REPO observation_ID --reason "The source report was corrected" --request-key invalidate-001
```

Restriction makes that version ineligible until an explicitly approved revised candidate supplies the narrower conditions. Invalidating any dependent source suspends active lessons and prevents retrieval, including before an exact-version reuse or export. Repeated runs of the same PR are not counted as independent experiences; this V1 does not calculate empirical lesson scores.

The approval commands are separate from model tools; `approved=true` is not authorization. This separation is a workflow boundary, not isolation from another process with the same local user's privileges. The skill explicitly tells the agent not to invoke operator approval through another host tool to evade review.

Explicit abandoned-run recovery:

```sh
hermes review-ledger release OWNER/REPO run_ID --generation 3 --reason "Operator confirmed handoff" --request-key release-001
hermes review-ledger transfer OWNER/REPO run_ID --generation 4 --session TRUSTED_SESSION_FROM_STATUS --reason "Operator-directed transfer" --request-key transfer-001
```

An old timestamp does not establish that a session died.

## Storage, limits, export and backup

The official store is:

`<resolved profile home>/plugin-data/<Hermes-generated plugin namespace>/review-ledger.sqlite3`

The integration resolves the public `ctx.state.data_dir` on each invocation. It never hard-codes `~/.hermes`, writes into installed code, or shares a cached connection across profiles. The data store is bound to its resolved profile identity.

SQLite initialization and schema migration are coordinated between processes.
Unknown/newer schemas and profile mismatches are rejected before journal changes.
Foreign keys, parameterized SQL, FULL synchronization, short transactions and
bounded lock waits are used. Database contention produces an explicit error;
retry with the same request key. Network calls and optional artifact staging are
outside database writer transactions.

The current data schema is version 5, preserving the `adaptive-v15` lineage.
First access upgrades an authorized version-1/2 database or genuine V1.5
schema-3 database atomically: query indexes, adaptive context and critic tables
are applied in order. Review records, receipts, exact resources, approvals,
usage records, lessons and artifacts are preserved. Two processes coordinate
migration with the same SQLite writer lock. Stop sessions and retain a verified
backup before upgrading.

The experimental critic 0.2.0 schema 3 is incompatible and is refused without
mutation. Never relabel its version or lineage. Back it up with the original
experimental tooling, retaining its artifacts and later history separately.
Use a fresh empty profile, or restore a verified pre-experimental V1/V2 backup
at its original resolved profile data path; ownership is path-bound. Do not
rewrite the ownership key or move a restored database to another profile. See the
[V1.5 compatibility guide](docs/v15-pilot.md#release-identities-and-compatibility).
Older code refuses schema 5; no automatic schema downgrade is provided.

### Explicit SQLite journal policy

SQLite 3.35 or newer is required. New and rollback-mode databases use DELETE
journaling. Readers can block a writer's commit in rollback mode, so contention
can be more visible than under WAL; it remains bounded and retryable. Local,
same-machine filesystems are required; network-shared databases are unsupported.

Existing WAL databases stay in WAL only with SQLite >=3.51.3 or the explicitly
recognized upstream backports 3.44.6 and 3.50.7. This is a check of Python's linked
SQLite runtime, not the Python version or a separately installed sqlite command.
The [official WAL-reset advisory](https://sqlite.org/wal.html#walreset) identifies
these fixes. A blanket >=3.44.6 comparison would wrongly accept other affected
release lines. Unverified vendor backports and other older release numbers fail
closed; no vulnerability/corruption reproduction is used as a safety test.

On an unrecognized runtime, WAL header flags or -wal/-shm sidecars cause an
`unsupported_sqlite_wal` refusal before SQLite opens the database. Even stale
sidecars require operator review; a refusal is not evidence of corruption.
There is no automatic source-journal conversion or Python/SQLite upgrade.
Stop all ledger sessions, including older plugin clients, before upgrading.
Do not change journal mode externally while sessions run. To recover a refused
legacy WAL installation, use a known-fixed runtime to create and verify a backup
before any operator-managed offline migration. Keep the original database and
its sidecars together; do not delete sidecars to bypass the check. The plugin
does not provide an automatic restore/migration command.

Initial resource choices, not benchmark claims:

- 10-second GitHub request timeout, at most 3 file pages and 200 files in the Hermes adapter
- Individual returned patches capped at 4,000 characters; missing, incomplete and cut-off patches are explicit
- Only five file previews are returned by open; snapshot file metadata remains bounded
- Snapshot metadata is capped at 60,000 compact JSON characters. A large file list retains a prefix and explicitly records `snapshot_metadata_budget`, incomplete capture, and known omitted files rather than refusing the entire PR
- Recall up to 5 complete lessons or compact references per result page, default 6,000 serialized characters, configurable 500–20,000; detail pages obey the same configured cap
- A bounded 200-candidate recall window, stable ordering and continuation/omission indicators
- Status 1–25 rows per collection, with a 24,000-character default response cap and complete detail retrieval; export 1–50 rows per collection with a character cap
- Optional UTF-8 artifact text at most 64 KiB; generated IDs, confined directories, temporary write and atomic publication
- No arbitrary filesystem paths accepted by model tools

The GitHub client performs only fixed-host HTTPS GETs. Redirects, unauthorized repositories and untrusted pagination targets are rejected. No credentials are sent to links extracted from PR content. A transport/auth/rate-limit failure remains an error, never an empty clean review. GitHub has no atomic multi-endpoint snapshot API: final HEAD/base revalidation cannot rule out an undetected ABA ref change.

JSON exports include `export_format_version: 1`, scope, snapshot and references, preserving reported strings verbatim. Markdown renders reported free text as literal content by escaping Markdown/HTML structure and indenting continuation lines; this prevents evidence text from creating report sections or image syntax. Renderer-specific extensions are not certified. Markdown displays initial capture completeness (`files_complete`, `patches_complete`, `total_files`, `omitted_files`, `truncation_reasons`) explicitly, including unknown older values, separately from later agent observations and export pagination. It separates supported current assessments, hypotheses/other assessments, historical evidence and limits. Missing artifacts and revoked lesson versions are explicit. Exports are bounded snapshots, not synchronization. Import is out of scope.

Create a consistent SQLite backup:

```sh
hermes review-ledger backup
```

The command uses `sqlite3.Connection.backup`, explicitly sets the private backup destination to DELETE journaling without changing the source, restores the produced bytes into an independent temporary directory, and checks integrity, foreign keys, schema/profile identity and immutable skill resources in that restored snapshot before publishing the backup under the plugin's `backups/` directory. Every connection is explicitly closed before temporary-directory cleanup or file publication, including on Windows where open file handles can prevent those operations. Its result includes `restore_verified: true` only after those checks. Backups contain the database; optional artifact files are not bundled. Preserve the artifact directory separately if those attachments matter. No automated restore/import or data-delete operation is provided.

The SQLite backup copy phase has a ten-second progress deadline. Opening the database, copying the restored file, and running integrity checks are outside that deadline; this is not a total wall-clock guarantee. Optional artifact staging is cleaned up after ordinary failed operations or idempotent retries; an abrupt process termination can leave an unreferenced bounded artifact file. There is no background cleanup process.

Do not store credentials, sensitive variables, or full conversations. No regex is claimed to remove every secret. Normal review does not transmit ledger memory to another service and has no remote telemetry. The optional critic has a separate explicit consent boundary; on the pinned host its real adapter refuses before transmission because the effective route cannot be publicly verified.

## Testing

All repository records, sessions, observations and HTTP fixtures in the tests are synthetic. Tests use temporary profiles/filesystems and never access a user's credentials or personal workspace.

Create a virtual environment with `python -m venv .venv`, then use its interpreter.

Linux/macOS:

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Windows PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[test]"
.venv\Scripts\python.exe -m pytest -q
```

Real Hermes integration is an explicit additional run on Python 3.14:

```sh
python scripts/ci/run_native_tests.py --hermes-source /path/to/verified/hermes-agent
```

Here `python` must be the environment interpreter with the required dependencies.
The command is shell-independent and records the actual native OS, Python/SQLite
versions, JUnit results, and full test output. It verifies the pinned clean Hermes
revision and rejects skipped or missing integration tests. See the
[validation runner](scripts/ci/README.md) for setup and report details.

The prepared GitHub Actions workflow has exactly three native jobs, one each on
Ubuntu, macOS and Windows with Python 3.14. Each runs the complete suite, including
the genuine Hermes integration cases. It is manual-only, has read-only repository
permissions, and does not start on push. Publishing the workflow or seeing it in
this source tree is not proof of a successful run. Execution requires the owner's
authorization and an account quota/billing check; paid overages are not authorized.

The source checkout must have its runtime dependencies available to that interpreter. The integration harness uses real host modules in isolated subprocesses, clears credentials, and substitutes only GitHub HTTP responses. When `HERMES_SOURCE_DIR` is absent, the integration tests are visibly skipped and are not proof of host compatibility. An explicitly selected missing/broken source fails instead of being presented as a pass.

The suite covers Unicode/spaced profile paths, real filesystem cleanup, deterministic connection closure, native SQLite writer contention and retry, transaction rollback, persistence/restart, full learning cycle, scoped idempotency, repository/profile isolation, full snapshot identity, real two-process initialization/reuse/acquisition, stale generation rejection, evidence limitations, source invalidation, exact lesson versions, bounded deterministic Unicode retrieval without FTS, complete large-lesson detail pagination, ID-free historical-run discovery, capture-completeness rendering, synthetic journal/runtime policy boundaries, unknown schemas, backup/restoration, missing/unsafe artifacts, pagination, GitHub errors and native Hermes surfaces.

Actual validation results are recorded only after running the final source. Do not infer coverage for an OS without an execution report from its real native runner. Live GitHub authentication, a real user PR pilot, and a full interactive model conversation require separate validation. The plugin does not claim measured improvement in review quality.

The validation totals and actual command output are recorded in the accompanying implementation report and native CI artifacts, each tied to the source revision. Missing, pending, failed, or skipped native runs are never counted as passes. Earlier 0.1.0 validation reported 7 tools and 0 hooks with no findings; that result is historical, not validation of the integrated nine-tool release. The earlier `hermes plugins validate` pass included manifest/registration agreement and its no-core-override check. New validation results must be tied to integrated 1.0.1; the old native results do not certify this extension. Package tests build and install the source distribution and complete installer wheel locally; artifacts are not published.

## Source layout and licensing

Runtime modules use the Python standard library and PyYAML for safe local skill frontmatter parsing. Hermes integration is confined to `tools.py`, `critic_hermes.py` and the local operator adapter. Domain models, service and learning do not import Hermes or any model SDK.

The initial SQL migration is inside `review_ledger/migrations/`. `setup.py` builds the wheel’s complete native-plugin payload from the authoritative root manifest, entry point, skill and runtime files; there is no second editable copy to keep in sync. `review_ledger/installer.py` implements the explicit profile command. There is one authoritative editable store, SQLite; reports and exports are derived output.

No project license or authorship declaration is invented. Licensing remains the repository owner's decision. Upstream Hermes source was used for compatibility verification in a separate checkout and is not vendored into this repository.

## Deliberately deferred

MCP, dashboards, other hosting, automatic/network external review imports, memory synchronization, embeddings/graphs/PostgreSQL, unrestricted auxiliary models, telemetry, daemons/cron/webhooks, distributed queues, parallel agents, target-repository test execution, code-fix commits, publication and merges are outside V1. The bounded optional claim critic is the only auxiliary-model extension; none of the other deferred features has a placeholder framework here.

## Optional compact context (V1)

Deterministic Python structural views are opt-in; legacy calls remain the default.
See [context compression](docs/context-compression.md) for independent character/byte
budgets, honest optional token accounting, full/reference detail, offline benchmarks
and limits. Compression adds no migration; this integrated build uses schema 5; the optional critic is still disabled by default.

## Configurable lesson automation (V1)

Lessons remain manual by default. An explicit local operator/profile choice can
activate bounded eligible proposals automatically, with provenance, audit, quotas
and immutable-version rollback. See [exact gates and enable/disable commands](docs/lesson-automation.md).
Automatic approval is a policy decision, never independent evidence or model training.
