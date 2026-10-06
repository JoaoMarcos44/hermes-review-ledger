# Hermes Review Ledger 0.1.0

A small native Hermes plugin for persistent, profile-local GitHub PR investigations and operator-approved conditional investigation lessons.

Hermes investigates code using its authorized host tools. Review Ledger records state and agent-reported evidence, coordinates a single writer, and helps retrieve relevant questions. It does not execute repository commands, independently observe tests, train models, or establish that a reviewer became better.

## Verified environment and public contract

The implementation was tested against the public NousResearch/hermes-agent source at commit `0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4`, runtime identity `git.0dbaf33`. Its `pyproject.toml` uses the placeholder version `0.0.0`; this project does not invent a minimum Hermes release number.

- Linux, Python 3.14.7, pytest 8.4.2, SQLite 3.53.1 in WAL mode
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

## Local installation

This V1 is a directory plugin. The complete source directory must include `plugin.yaml`, the root `__init__.py`, `review_ledger/`, and `skills/`. Installing only its Python library wheel does not install a native Hermes plugin or the skill.

1. Choose an existing Hermes profile through Hermes' normal profile mechanism, or explicitly select a profile directory using `HERMES_HOME`. Do not replace an existing profile's configuration.
2. Copy this source directory into that profile's `plugins/review-ledger/` directory. Avoid overwriting an existing plugin directory. The supported native discovery mechanism loads the manifest and root `register(ctx)`.
3. Merge the configuration below into that profile's `config.yaml`. Keep its other settings and authorized repositories. `plugins.isolation` applies to the entire profile: this V1 needs `in_process` for its operator CLI. If the profile requires `host` isolation, use a separate suitable profile rather than silently weakening that policy.
4. Run `hermes plugins doctor /absolute/path/to/plugins/review-ledger --ci`, `hermes plugins validate /absolute/path/to/plugins/review-ledger`, and `hermes plugins list`. If this plugin was previously disabled, `hermes plugins enable review-ledger` removes its explicit disabled entry; a disabled entry otherwise takes precedence over the enabled list.
5. Start a new Hermes session in the selected profile. Ask it to load `review-ledger:review-ledger` using `skill_view`; `skills_list` gives the exact qualified name in the tested runtime.

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

Use the standard Hermes environment configuration to supply `REVIEW_LEDGER_GITHUB_TOKEN`, or set it in the process environment before starting Hermes. Do not paste tokens into conversations, source files, tool arguments, or reports. The plugin only reads the explicitly configured environment-variable name; it never searches Git, GitHub CLI, netrc, credential helpers, or other programs.

Use a token limited to the authorized repositories and read access required by GitHub's pull-request endpoints. Even public PR reads require an explicitly supplied token in this V1. Authorization is checked before network access and the stable repository ID returned by GitHub is checked against stored identity.

To remove the code, first disable it with `hermes plugins disable review-ledger`, stop existing sessions, and remove only the installed plugin directory through Hermes' supported removal workflow. Review Ledger has no data deletion hook. Its SQLite data and optional artifacts are outside the installed code; retain them or back them up separately. Removing plugin configuration can remove the repository allowlist; restore that configuration before reusing retained data.

## What activates the plugin

There are three separate steps: installing its files, enabling discovery in a
chosen Hermes profile, and invoking a tool for an authorized review. Merely copying
this directory or loading its skill does not start a review.

- Hermes discovery calls the root `register(ctx)`, which registers seven tools,
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
revision or revocation. It does not train model weights, launch additional models,
rewrite the skill, or run a self-improvement loop.

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
2. `ledger_status(repository, run_id, limit, offset)` reads bounded state. A second session can follow the run but cannot write over its owner.
3. `ledger_record(repository, run_id, generation, request_key, action, data)` records an observation, proposes a finding, records a snapshot-scoped assessment, or invalidates an owned observation.
4. `ledger_run(..., action="pause", note=...)` preserves state and releases ownership. A later trusted session can acquire an unowned run with its current generation. `complete` closes the investigation; another open can create a new run.
5. `ledger_export(repository, run_id, format="markdown" | "json", limit, offset, max_chars)` returns report text. Save it using an authorized host file tool if wanted. It does not publish anything.

Every write needs a scoped retry key. An identical retry returns its original operation receipt without duplicating records; changed content with the same key returns `idempotency_conflict`. Receipts describe the committed operation, not a new authorization or a guarantee of current ownership. Consult `ledger_status` for live state. Exact lesson-use retries additionally recheck revocation; an old receipt never authorizes reuse of a revoked lesson.

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
2. A local operator inspects and explicitly approves that exact version.
3. `ledger_recall` returns up to five eligible active versions. Selection is deterministic, bounded, and based on terms, tags and symbols. No embeddings, probabilities or model calls are used.
4. Before applying one, `ledger_lesson(action="use")` records its exact version, applicability and explanation and rechecks eligibility.
5. `ledger_lesson(action="result")` records usefulness, behavioral result, execution blocking and a separate explanation. Refutation can be useful. Blocked execution is inconclusive. Unused lessons receive no invented outcome.
6. The operator can suspend or restrict a version. A revision creates a new candidate, preserving all prior versions and exact-version uses. Approving a newer version retires the old one without rewriting ongoing history.

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

SQLite initialization and schema migration are coordinated between processes. Unknown newer schemas are rejected before journal/schema changes. Foreign keys, parameterized SQL, short transactions, WAL and bounded lock waits are used. Database contention produces an explicit error; retry with the same request key. Network calls and optional artifact staging are outside database writer transactions.

Initial resource choices, not benchmark claims:

- 10-second GitHub request timeout, at most 3 file pages and 200 files in the Hermes adapter
- Individual returned patches capped at 4,000 characters; missing, incomplete and cut-off patches are explicit
- Only five file previews are returned by open; snapshot file metadata remains bounded
- Recall up to 5 lessons, default 6,000 serialized characters, configurable 500–20,000
- A bounded 200-candidate recall window, stable ordering and continuation/omission indicators
- Status 1–25 rows per collection; export 1–50 rows per collection with a character cap
- Optional UTF-8 artifact text at most 64 KiB; generated IDs, confined directories, temporary write and atomic publication
- No arbitrary filesystem paths accepted by model tools

The GitHub client performs only fixed-host HTTPS GETs. Redirects, unauthorized repositories and untrusted pagination targets are rejected. No credentials are sent to links extracted from PR content. A transport/auth/rate-limit failure remains an error, never an empty clean review. GitHub has no atomic multi-endpoint snapshot API: final HEAD/base revalidation cannot rule out an undetected ABA ref change.

JSON exports include `export_format_version: 1`, scope, snapshot and references, preserving reported strings verbatim. Markdown renders reported free text as literal content by escaping Markdown/HTML structure and indenting continuation lines; this prevents evidence text from creating report sections or image syntax. Renderer-specific extensions are not certified. Markdown separates supported current assessments, hypotheses/other assessments, historical evidence and limits. Missing artifacts and revoked lesson versions are explicit. Exports are bounded snapshots, not synchronization. Import is out of scope.

Create a consistent SQLite backup:

```sh
hermes review-ledger backup
```

The command uses `sqlite3.Connection.backup`, restores the produced bytes into an independent temporary directory, and checks integrity, foreign keys and schema version before publishing the backup under the plugin's `backups/` directory. Every connection is explicitly closed before temporary-directory cleanup or file publication, including on Windows where open file handles can prevent those operations. Its result includes `restore_verified: true` only after those checks. Backups contain the database; optional artifact files are not bundled. Preserve the artifact directory separately if those attachments matter. No automated restore/import or data-delete operation is provided.

The SQLite backup copy phase has a ten-second progress deadline. Opening the database, copying the restored file, and running integrity checks are outside that deadline; this is not a total wall-clock guarantee. Optional artifact staging is cleaned up after ordinary failed operations or idempotent retries; an abrupt process termination can leave an unreferenced bounded artifact file. There is no background cleanup process.

Do not store credentials, sensitive variables, or full conversations. No regex is claimed to remove every secret. The plugin never transmits ledger memory to another service and has no remote telemetry.

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

The suite covers Unicode/spaced profile paths, real filesystem cleanup, deterministic connection closure, native SQLite writer contention and retry, transaction rollback, persistence/restart, full learning cycle, scoped idempotency, repository/profile isolation, full snapshot identity, real two-process initialization/reuse/acquisition, stale generation rejection, evidence limitations, source invalidation, exact lesson versions, bounded deterministic retrieval without FTS, unknown schemas, backup/restoration, missing/unsafe artifacts, pagination, GitHub errors and native Hermes surfaces.

Actual validation results are recorded only after running the final source. Do not infer coverage for an OS without an execution report from its real native runner. Live GitHub authentication, a real user PR pilot, and a full interactive model conversation require separate validation. The plugin does not claim measured improvement in review quality.

The validation totals and actual command output are recorded in the accompanying implementation report and native CI artifacts, each tied to the source revision. Missing, pending, failed, or skipped native runs are never counted as passes. The official CLI Plugin Doctor reported 7 tools and 0 hooks with no findings. `hermes plugins validate` passed, including manifest/registration agreement and its no-core-override check. Source distribution and library wheel builds succeeded. Build artifacts are local and were not published.

## Source layout and licensing

Runtime modules use only the Python standard library. Hermes integration is confined to `tools.py` and the local operator adapter. Domain models, service and learning do not import Hermes or any model SDK.

The initial SQL migration is inside `review_ledger/migrations/` so Python source distributions can include it reliably. There is one authoritative editable store, SQLite; reports and exports are derived output.

No project license or authorship declaration is invented. Licensing remains the repository owner's decision. Upstream Hermes source was used for compatibility verification in a separate checkout and is not vendored into this repository.

## Deliberately deferred

MCP, dashboards, other hosting, external review imports, memory synchronization, embeddings/graphs/PostgreSQL, auxiliary models, telemetry, daemons/cron/webhooks, distributed queues, parallel agents, target-repository test execution, code-fix commits, publication and merges are outside V1. None has a placeholder framework or configuration switch here.
