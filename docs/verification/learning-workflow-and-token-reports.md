# Verification: Learning Workflow and Token Reports

Investigation record for the closed-loop learning workflow and operator
token-report feature. Documents baseline mechanisms, sibling boundaries,
alternatives rejected, tested receipts, and open items.

## Scope

- Repository: `JoaoMarcos44/hermes-review-ledger`
- Baseline: `8762289fb399362c9886c1d59f8ce0591392537a`
- Branch: `fix/learning-workflow-and-token-reports`
- Package **1.0.3**, protocol **4**, schema remains **5**.

## Baseline gaps and implemented boundaries

The baseline stored uses/outcomes and improvement diagnoses, but lacked this
read-only cross-run projection and host-token CLI. An owned run could complete
with unresolved considered uses. Behavioral-shape refusals lacked safe recovery
hints. The mechanisms below describe the candidate, not baseline functionality.

### Workflow view (`review_ledger/workflow.py`)

- `WorkflowView.summary(scope, run_id, limit, offset, conn)` — compact
  repository-scoped queue of pending improvements and lesson uses.
- `WorkflowView.detail(scope, improvement_id, conn, limit, offset)` —
  detailed view of a specific improvement with target metadata and
  source outcome pagination.
- Reuses `Improvements` and `Learning` eligibility helpers on the same
  read transaction.
- Completion preflight in `service.py` refuses `complete` when pending
  `applicable`/`uncertain` lesson uses lack `usefulness` outcomes.

### Token report (`review_ledger/token_report.py`)

- `TokenReport(ledger, reader).report(repository, run_id, session_id,
  include_workers)` — correlates trusted audit actors to unique session IDs
  and builds a truthful token report.
- Canonical computation: `prompt = input + cache_read + cache_write`;
  `total = prompt + output`; reasoning is a subset of output.
- Cost view: `actual` / `estimated` / `included` / `unknown` are distinct;
  `null` is not zero.
- Shared-session detection: sessions correlated to multiple runs are flagged.
- Auxiliary lineage deduplication: maximal ancestor scopes counted once.

### Host reader (`review_ledger/hermes_usage.py`)

- `SessionDBMetadataReader` wraps `SessionDB(read_only=True)` lazily.
- Retains an allowlisted projection; never emits prompts/config/previews.
  Host getters and branch scope helpers may internally read fuller rows.
- `auxiliary_lineage_ids` uses `_session_lineage_root_to_tip` and
  `_is_explicit_branch_session` as a compatibility shim matching the
  auxiliary getter's scope. Not a public API contract.
- `list_sessions_metadata` uses `list_sessions_rich` with
  `project_compression_tips=False`; bounded to 1000 sessions.

### Operator CLI (`review_ledger/operator.py`)

- `hermes review-ledger tokens owner/repo [--run-id] [--session-id]
  [--include-workers] [--format human|json]` — operator-only, read-only.
- `hermes review-ledger usage repository [--run-id]` — existing usage report.
- No approval, activation, or semantic-edit operations.

## Sibling boundaries

- **PR #7** (`Context/Improvements/local-size`) — provides existing
  context/improvement/local-size APIs. Inherited, not newly claimed.
- **PR #10** (proactive lesson creation) — provides conditional lesson
  creation. Inherited, not newly claimed.
- **Existing `usage` command** — reports Ledger-scope usage, not host tokens.
- **Existing `improve` API** — requires one run; does not expose cross-run queue.

The new gap is the **read/closeout consumer** and a **bridge to persisted
host usage**.

## Alternatives rejected

| Alternative | Reason |
|---|---|
| Raw live sqlite3 reads | Reuses host's tracked read-only lifecycle; avoids direct connection. |
| Character estimates (chars/4) | Wrong metric; provider semantics differ. |
| Per-tool totals without provider telemetry | No host API for per-tool attribution; would invent data. |
| Automatic semantic edits from diagnosis | Diagnostics do not establish concrete fixes or execution authority. |
| Separate policy clones | Duplicates canonical procedure; violates profile sole-policy rule. |
| Auto-approve / auto-activate | Invents outcomes; violates ownership and evidence gates. |
| Plugin-state get/set aggregate | No public atomic transform/iteration API; read-modify-write loses concurrency. |
| New migration + provider-event collector | Not necessary to analyze existing spending; adds upgrade and coverage risks. |

## Tested receipts

Receipts reside in the scratch implementation evidence area. Portable
command patterns are described below; personal absolute paths are not
published.

### Token report tests

- **Final focused**: 58 passed (token-final-focused).
- **Dispatch**: 5 passed (token-dispatch-green).
- **Resource limits**: 5 passed (`token-resource-green`).
- **Integrated feature contracts**: 100 passed (`final-feature-contracts`).
- **Procedure/budget correction controls**: 12 passed, including nine regressions
  and three procedure tests (`protocol-budget-correction-green`).

These suites **overlap** and are NOT a unique total. Individual tests appear
in multiple suites.

### Workflow tests

- **Red→Green**: reproduction of closeout omission, then fix verification.
- Focused workflow/status/feedback receipts were inspected and rerun by the parent.
- Early worker runs and fixture repairs are development history, not final-head evidence.

### Reader/scope tests

- Read-only enforcement: red→green.
- Prefix scope matching: red→green (including faithful and admitted variants).

## Native test evidence

Two actual native tests passed on pinned clean Hermes
`0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4` via isolated real
loader/registries/CLI/SessionDB. Only GitHub HTTP was synthetic.

This is **Windows-only** evidence. Local execution does not establish remote CI.

## Integrated native verification and accepted environment limitation

The canonical `scripts/ci/run_native_tests.py` matrix collected **1269** tests:
**1265 passed, 4 failed, 0 errors, 0 skipped in 366.21s**, exit 1. This is not a
full-suite pass. JUnit and runtime accounting were preserved.

Two failures were stale assertions expecting procedure `3` after the intentional
procedure `4` update. Both were corrected without weakening the contract:
**2 passed in 7.80s**. Both complete affected native files then passed:
**18 passed in 76.33s**, exit 0. These executions overlap, not additive coverage.

The other failures are `test_artifact_symlink_refused` and
`test_symlink_reference_and_ancestor`: native Windows `symlink_to` fails with
**WinError 1314** before reaching production assertions. The exact four-case
control on baseline `8762289fb399362c9886c1d59f8ce0591392537a`, with the same
interpreter and pinned Hermes, produced **2 failed, 2 passed in 8.42s**; only the
same symlink setup failures remain. Their assertions are unchanged, not skipped.
The production symlink assertions were not exercised on that unprivileged
Windows host. No assertions were removed or skipped, permissions were not
changed, and the matrix was not called green.

An earlier foreground native run exceeded the tool timeout before final JUnit;
it remains interrupted/inconclusive, not passing evidence. The tracked run above
supplies actual accounting. Python 3.14.7, pytest 8.4.2, SQLite 3.53.1; Windows.

Reproduce the full native matrix using the repository's documented pinned SDK:

```sh
python scripts/ci/run_native_tests.py --hermes-source /absolute/hermes-source \
  --expected-platform Windows --reports-dir /absolute/verification-reports
```

Reproduce the affected native files with the pinned host and required native mode:

```sh
HERMES_SOURCE_DIR=/absolute/hermes-source HERMES_INTEGRATION_REQUIRED=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tests/test_hermes_integration.py tests/test_package_resources_native.py --tb=short
```

## Development history (not green evidence)

- Workflow and token repair workers stopped in repetition loops; partial files
  were frozen, inspected and independently retested, not accepted from summaries.
- Fixture corrections were made during RED→GREEN iterations.
- SDK checkout recovery and installer slice-control tests informed boundaries.

These are **development history**, not evidence of correctness.

## Security, compatibility, rollback

- **No schema migration** — schema remains 5.
- **No credentials returned** — metadata allowlist excludes prompt/config.
- **No new provider requests** — read-only host session counters only.
- **No config toggles / automatic takeover** — operator-only command.
- **Host snapshot limits** — 1000 runs, 1000 sessions, 20000 audit events.
- **Compatible host APIs required** — unsupported hosts fail explicitly.
- **Native evidence Windows only** — remote checks must be read separately.
- **No new model token tool** — operator-only report; no approval operation.

## Resumed final verification

The recovered implementation was verified again after the two final repairs:
truthful auxiliary-coverage formatting and the native dependency declaration.
Package **1.0.3**, procedure **4**, and schema **5** remain distinct identities.

- Focused feature contracts: **107 passed in 12.36s**, pytest exit 0; measured
  command wall time **12.785s**. This is the fourteen changed feature/adapter
  test files, not a second unique total added to the full matrix.
- Canonical native matrix: **1276 collected, 1274 passed, 2 failed, 0 errors,
  0 skipped in 571.31s**, exit 1. All **24 required real-Hermes contracts**
  passed on pinned Hermes `0dbaf33f67acf1f6d8e8e6c6efe8042ef8db98c4`.
- Baseline control: the exact two symlink cases failed at fixture creation with
  the same **WinError 1314** on unchanged baseline production. No product
  assertions were reached in these cases. They were neither removed nor skipped.
- Closeout regression: the identical two selectors produced **2 failed in
  0.61s** on unchanged baseline production and **2 passed in 5.03s** on the
  implementation. Failure was `DID NOT RAISE LedgerError`, not setup/collection.
- Native dependency declaration: **1 failed in 0.08s** before adding
  `python_dependencies`, then **1 passed in 0.04s**. Parent file verification:
  **4 passed in 0.04s**. The native parser recognizes the explicit requirement.
- Auxiliary formatter: **5 failed, 1 passed in 0.59s** before the repair;
  identical command **6 passed in 0.75s** afterward. Directly affected token/CLI
  scopes passed **67 tests in 6.17s**, overlapping final feature/full coverage.
- Wheel and sdist built with `python -m build --sdist --wheel --no-isolation`.
  A clean wheel environment was provisioned **offline** from the local package
  and cached PyYAML. Package compatibility checks passed; the actual installed
  synthetic profile reported **1.0.3** and all **38 runtime payload files**
  matched the final source and installer manifest byte-for-byte. Configuration
  remained unchanged. An initial smoke-harness JSON parser rejected documented
  human instructions after successful install; exact status read-back recovered
  verification without repeating the installation. This was not a product fault.
- The isolated local AST graph was updated without LLM extraction. Its fuzzy
  query/path is orientation only: the returned class-level `uses` edge is
  inferred, not a verified call chain. Source and behavioral tests are authority.
- All changed source files passed AST/whitespace checks. Credential-pattern
  scans returned no hits; a pattern scan is not proof of complete secret absence.

The final matrix's source/test/runtime files were hash-checked against the
frozen source. Subsequent changes to this evidence document do not alter those
verified executable bytes. Native execution remains Windows-only and uses
synthetic GitHub transport; it does not prove live-provider egress or semantic
learning quality. No extra critic/model call, automatic approval, invented
feedback, schema migration, or marginal token/cost-savings claim is introduced.

The live runtime's missing PyYAML was repaired in its actual bootstrapped
Python 3.14 dependency generation, and the SQLite backup reported
`restore_verified: true`. The operator explicitly selected **deferred live-code
replacement while sessions are active**. The existing live plugin remains
**1.0.2**; the verified update is an artifact, not a hot-reload claim. Historical
runs/lessons, configuration, credentials and other profiles were not rewritten.

## Open items

- Symlink production assertions remain unexercised on this unprivileged Windows host.
- Remote checks and publication identity are recorded in the PR, not inferred locally.
- Procedure 4 preserves the tested low-cap context/reference contracts.
- Cross-platform native evidence is unavailable (Windows-only environment).
- Live plugin upgrade is deliberately deferred until active sessions are closed.
