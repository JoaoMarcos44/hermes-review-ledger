# Diagnose review repetition without mistaking evidence for a stuck loop

This is a documentation-only runbook and a bounded incident reconstruction. It
adds no retry policy, watchdog, tool, schema, lesson, runtime fix, or deployment.
Ledger is a recorder of agent-reported evidence, not an agent scheduler or an
independent judge of progress. See the [feedback pilot](review-feedback-pilot.md)
for contract-first review and the [installation guide](installation.md) for
profile-safe updates.

## Incident scope and corrected conclusion

The inspected case was an interactive Hermes review of
[NousResearch/hermes-agent PR #135861](https://github.com/NousResearch/hermes-agent/pull/135861),
pinned to head `3e4ff288e436b4f116d3c02060f2488b3ddfbd7d` and base
`46d7718a52ff33accb15dc0501736fbdb6833cab`. The Ledger source baseline examined
for this report is `33ed72931ca57cb4be3563b78efc711d54410998`.

A health observer initially called the review a non-converging loop after seeing
short output tails with six, then seven failing tests. A subsequent inspection
of complete test output, tool arguments, patch receipts, cleanup, and subsequent
Ledger records **does not support an endless loop**. It shows a finite sequence
of reviewer probes, changing test scope, one repeated read of an already-deleted
probe, and a separate context-pilot failure. The review later recorded findings
and assessments, completed its Ledger run, and exported a Markdown report. The
completed state was independently read back with `ledger_status`; the export
receipt reported `state="exported"` with `next_offset=null`. Its operator also
instructed it to finish the loop; therefore later progress does not establish
that it would have stopped without steering.

The browser findings themselves are not adjudicated by this document. Their
fixtures were controlled mocks, not live hosted-browser tests. Failure counts
are execution evidence, not proof of official security scope, real credential
use, an exploit, or the truth of every finding.

## Evidence timeline

Times below are UTC on 2026-10-10; the interactive session began on 2026-10-09 in
UTC-03. Only selected non-sensitive event metadata is reproduced. Local profile
paths, complete conversations, private reasoning, credential-shaped values, and
browser payloads are intentionally excluded.

| UTC | Event confirmed in the transcript | What it establishes |
| --- | --- | --- |
| 01:37:44 | `ledger_context(action="prepare")` returned `internal_error` | No usable context-pilot bundle was delivered. |
| 02:00:44 | `ledger_context(action="resume")` returned the same error | Changing action did not restore this path; two calls are not an automatic retry storm. |
| 02:06:09 | Temporary reviewer probe: 4 failed, 0 passed; runner wall 6.3 s; pytest 1.47 s | Four contract assertions reached the reviewed code and failed. |
| 02:08:42 | Patch added snapshot and type private-page cases | The next run had different coverage. |
| 02:09:01 | Probe: 6 failed, 0 passed; runner wall 6.7 s; pytest 1.55 s | Two new cases failed in addition to the original four. |
| 02:09:33 | Patch changed a redaction assertion from a fixed marker to inequality, retaining the no-original-value assertion | Same test set, changed oracle. This was not a production fix. |
| 02:09:50 | Probe: 6 failed, 0 passed; runner wall 6.4 s; pytest 1.51 s | The six failed test identities were unchanged; the assertion text was not. |
| 02:10:31 | Patch moved the vision call-count assertion before the success assertion | The next run could expose the controlled call boundary instead of stopping at the first assertion. |
| 02:17:08 | Related-PR ancestry command reported `no merge base` in a shallow clone | Ancestry comparison was incomplete; it did not establish incompatible histories. |
| 02:18:01 | Patch added one page-evaluation output-redaction case | The next run again had different coverage. |
| 02:18:40 | Existing REST-path positive control: 1 selected test passed; runner wall 12.4 s | A sibling control passed; this does not validate the mocked hosted-MCP path. |
| 02:18:56 | Probe: 7 failed, 0 passed; runner wall 7.2 s; pytest 1.61 s | One new case accounts for the increased failure count. |
| 02:19:26 | The reviewer removed its untracked temporary probe and obtained a clean worktree | Cleanup happened; it does not turn the failed assertions into passes. |
| 02:20:21 | A later read requested that same deleted file and returned `not_found` | One stale action is confirmed, not an indefinitely repeated cycle. |
| After operator steering | `ledger_record` accepted observations, findings and assessments | The review crossed from probe execution into evidence recording. |

The bounded transcript audit found four probe executions with failed-test
cardinalities **4, 6, 6, 7**, six patch calls to the temporary probe, two context
errors, one probe deletion, and one read-after-delete. These are not aggregate
repository-suite totals. Patch receipts carried `success: true`; partial-read
warnings were advisory, not failed edits. The official suite and the temporary
probe were distinct artifacts.

## Why the diagnosis went wrong

### Confirmed observation error: a tail and a count replaced a causal trace

The observer assumed the goal of each test run was to make the reviewed code
pass. In a review, an intentionally red probe can instead finish a question by
showing a contract violation. The observer did not first compare selectors,
failed-test identities, assertion changes, or production changes. It interpreted
increased coverage as worsening convergence.

This is a diagnostic workflow error, not evidence that Ledger caused the
reviewer to loop. Active status, recent timestamps, token totals and zero
compression-failure counters indicate activity only. They do not show useful
progress. Conversely, a red exit code or a repeated command does not prove
absence of progress.

### Confirmed local inefficiency: reading a removed temporary artifact

The explicit deletion succeeded, then a later read requested the deleted file.
That action could not provide the requested evidence without restoring a saved
artifact. Classify it as stale-plan/artifact handling. One occurrence is not
enough to diagnose an endless loop, and cleanup is not a substitute for keeping
a reproducible evidence artifact when that is required by the review contract.

### Separate failure: context resource resolution

Both context calls exposed only a sanitized `internal_error`. The exact exception
from those historical invocations was not captured. The following mechanism is
independently reproducible on the stated Ledger baseline; it is consistent with
the observed error but is **not proved to be its historical cause**:

- [protocol reader](../review_ledger/protocol.py) calls
  `importlib.resources.files("review_ledger")` with a fixed top-level name.
- The sibling [critic prompt reader](../review_ledger/critic_contract.py) uses the
  same fixed name.
- A directory plugin can run under a `hermes_plugins.<slug>` namespace rather
  than as the canonical top-level package. If no canonical `review_ledger` is
  importable, both readers raise `ModuleNotFoundError`. A different canonical
  installation could instead mask the lookup mismatch; that collision was not
  tested for this report.
- [Context.prepare](../review_ledger/context.py) loads the protocol before
  collecting the bundle; `resume` returns through `prepare`. Swapping prepare
  for resume or changing a character budget does not repair a package import.
- The generic exception boundary in [tools.handle](../review_ledger/tools.py)
  intentionally returns `internal_error` without exposing raw exceptions or
  configuration. The error alone cannot identify a database, resource, or
  installation problem.

A fresh, source-level namespace probe produced two expected
`ModuleNotFoundError` results and four handler projections of `internal_error`
(prepare/resume, full/compact), with a minimal synthetic Ledger seam. Normal
canonical imports read both resources successfully. The first probe attempt in
an editable-installed environment refused to proceed because a canonical
package was already discoverable; a clean interpreter removed that confounder.

This was **not** a PluginManager integration run, a live profile invocation, a
repair, or a replay of the historical process. Code read from a profile on disk
can differ from modules held by an already-running CLI. Do not infer a live
repair from replaced files. This PR does not install, modify, or restart a
plugin and does not include or claim ownership of any separate runtime patch.

## Reproduce the resource boundary without a live profile

Save the following snippet as `resource_probe.py` in an owned scratch directory.
Run it with a clean Python 3.12-3.14 interpreter against a checkout of the Ledger
baseline above:

```sh
python -I resource_probe.py /absolute/path/to/pinned/ledger-checkout
```

The guard intentionally rejects an environment with an ambient canonical
installation. The code imports package modules only; it does not execute the
root plugin registration, open a Ledger database, use credentials, or contact a
browser/provider. It asserts the baseline failure rather than suppressing it.

```python
import importlib
import importlib.util
from pathlib import Path
import sys
import types

root = Path(sys.argv[1]).resolve()
assert importlib.util.find_spec("review_ledger") is None, "Use a clean interpreter"
for name, path in (
    ("hermes_plugins", []),
    ("hermes_plugins.review_ledger", [str(root)]),
):
    package = types.ModuleType(name)
    package.__path__ = path
    sys.modules[name] = package
name = "hermes_plugins.review_ledger.review_ledger"
spec = importlib.util.spec_from_file_location(
    name, root / "review_ledger" / "__init__.py",
    submodule_search_locations=[str(root / "review_ledger")],
)
package = importlib.util.module_from_spec(spec)
sys.modules[name] = package
spec.loader.exec_module(package)
readers = (
    importlib.import_module(name + ".protocol").protocol,
    importlib.import_module(name + ".critic_contract").load_prompt,
)
for reader in readers:
    try:
        reader()
    except ModuleNotFoundError as exc:
        assert exc.name == "review_ledger"
        print("expected baseline failure:", reader.__name__)
    else:
        raise AssertionError("Baseline resource lookup unexpectedly succeeded")
sys.path.insert(0, str(root))
assert importlib.import_module("review_ledger.protocol").protocol()["content"]
assert importlib.import_module("review_ledger.critic_contract").load_prompt()
print("canonical resource positive controls passed")
```

A later fixed revision should not satisfy the expected-failure assertions.
Do not edit assertions to label a different revision as the same reproduction.
For a runtime fix, independently verify the real native loader, both sibling
readers, absence/collision of the canonical package, separate profile copies,
and the operator's fresh-process deployment path.

## Progress-based triage procedure

1. **Declare the question and budget.** Freeze repository/head/base, permitted
   checks, expected outcome, scope, and stop condition. In a review, a red probe
   can answer a question; in implementation, it can be the starting point for a
   later RED/GREEN contract.
2. **Inspect complete results.** Separate collection/setup failure, timeout,
   behavioral assertion, blocked write, success-with-warning, and missing
   artifact. Compare actual selectors and failed identities, not the same file
   name or the number of failures alone.
3. **Explain each retry's delta.** Record the changed input, source, oracle,
   environment, or new falsifiable question and its predicted result. A repeated
   unchanged command can be justified for a declared determinism/flake check;
   it still needs a bounded repetition budget. If no discriminating reason
   exists, stop that branch rather than rerunning it for activity's sake.
4. **Record answered questions.** Preserve command, result, relevant patch or
   artifact, environment, limitations, and source revision. Do not keep editing
   a reviewer probe merely to make the target appear green. Never delete or
   weaken required regression assertions to pass CI.
5. **Separate infrastructure recovery.** An unchanged context `internal_error`
   needs an independently justified diagnostic, not unlimited prepare/resume
   retries. Use loaded guidance and existing status/detail APIs only where their
   contracts suffice; do not pretend missing exact protocol/guidance was read.
   Pause if unavailable context is essential to the next authorized step.
6. **Transition deliberately.** Reconcile evidence, record assessments, and
   complete if the declared scope is satisfied. Otherwise pause with the exact
   missing evidence. New scope requires a new bounded question, not an implicit
   restart of already-answered work.
7. **Respect ownership and steering.** A second health observer reads status but
   must not acquire another session's owned run, complete/pause it, update its
   findings, send instructions, kill processes, or edit its artifacts. Operator
   steering is separate from an observer's diagnosis.

| Classification | Required signal | Response |
| --- | --- | --- |
| Productive investigation | A new/answered question, changed scenario, sharper oracle, justified bounded repetition, or persisted evidence | Continue within the declared budget, then reconcile. |
| Local wasted action | A stale target or unchanged retry with no discriminating purpose | Stop that branch, retain evidence, refresh the plan. |
| Suspected non-progress loop | Repeated state/action/result with no new evidence or authorized scope, across a declared budget | Report the sequence and pause/escalate; do not infer the cause from activity alone. |
| Infrastructure blocker | Setup/transport/resource failure before the intended behavior | Record that boundary and investigate it separately, or pause. |
| Unknown | Partial output, missing artifact or no stable comparison | Obtain the smallest missing receipt; retain uncertainty. |

This table is guidance for a human or agent, not an implemented automatic loop
detector. Neither Ledger nor this document guarantees convergence or decides a
universal number of permitted retries.

## Record the distinction with existing APIs

An owner can use the following `data` for
`ledger_record(action="observation")` without inventing a loop state or new tool
fields. Supply the existing authorized repository/run/generation and a unique
request key outside this example. It is a synthetic note, not a behavioral test
report:

```json
{
  "kind": "note",
  "outcome": "incomplete",
  "summary": "Synthetic review-progress check: repeated failures need causal classification",
  "details": "Compare the question, selector, fixture, oracle, source revision and complete output before calling this a non-progress loop. A red reviewer probe may answer a question; an infrastructure error does not establish target behavior.",
  "limitations": "Synthetic diagnostic note only. No target execution, historical cause, automatic loop detection or independent semantic verification is claimed."
}
```

Use a separate eligible observation with details and environment for a real
behavioral result. Record infrastructure failures, timeouts and incomplete
checks as such. A note cannot promote a supported behavioral assessment or
trigger a conditional lesson. If a run is paused/incomplete, do not propose a
lesson merely to close the workflow. See [lesson automation](lesson-automation.md).

## Alternatives, verification and residual uncertainty

- **Failure-count watchdog:** rejected as a conclusion for this case because
  additional cases account for 4 -> 6 -> 7, and the repeated six-case run changed
  its assertion. A detector would need a separately defined progress contract.
- **Force all reviewer probes green:** rejected; that changes review into
  implementation and discards useful counterexample evidence.
- **Disable Ledger or reset its database:** unsupported. Context errors coexist
  with working status/evidence recording; there is no corruption proof here.
- **Add a runtime retry limit/resource fix in this PR:** out of scope. A resource
  mechanism can be reproduced, but the historical exception and a complete
  native-loader repair are separate evidence obligations.
- **Documentation and bounded classification:** chosen. It corrects the
  evidenced observer mistake without altering session control, data, scheduling,
  or package behavior. The existing feedback/installation guides remain
  authoritative for their respective contracts.

Verification for this document includes metadata-only read-back of the
four-run sequence and the resource probe above. Existing focused Ledger tests
check feedback recording, context contracts and behavioral-report eligibility;
they do not certify the historical browser findings or prove future reviews
will never stall. Full/native integration suites and live hosted-browser tests
are not claimed. The exact historical context exception, unsteered convergence,
and comparative review-quality benefit remain unknown.
