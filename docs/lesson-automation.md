# Ledger V1: configurable lesson automation

Ledger remains package 1.0.0 with SQLite schema 5 in this integrated V1 build.
The bundled procedure revision is 2 to record this deliberate contract change;
this is not a Ledger V2 product. The default is `manual`, including profiles upgrading without a setting. Nothing
retroactively approves existing candidates. No background worker or model call is
started. Automatic mode acts only during an explicit, owner-fenced `propose` or
`improve` operation. Recording a result can still generate a review diagnostic;
it never invents new conditions, edits, outcomes, or a successful experiment.

## Enable, inspect, disable

After opening an authorized repository in the selected profile, the local operator
can select its audited mode using the existing Hermes CLI registration:

```sh
hermes review-ledger automation owner/repo automatic --reason "Enable bounded lesson activation" --request-key enable-lessons-1
hermes review-ledger automation-status owner/repo
hermes review-ledger automation owner/repo manual --reason "Review candidates myself" --request-key disable-lessons-1
```

Alternatively set `lesson_automation_mode: automatic` in the profile's existing
`review-ledger` plugin configuration. Only `automatic` and `manual` are accepted.
A repository-local CLI choice takes precedence over profile configuration, even
for an already-created handler. Configure `improvements_enabled: true` to use the
existing `improve` model action and outcome diagnostics. The activation mode alone
does not enable other features, permissions, imported skills, critic, or tools.
Disabling keeps already-active lessons; suspend/restrict an exact version to revoke
its retrieval. A completed activation is not undone by a later toggle.

## Exact automatic eligibility

- At most 3 activations per review run, persisted transactionally. Retries and
  policy toggles cannot replenish the quota; creating a genuinely new run starts
  that run's separate quota. There is no claimed lifetime or cross-run quota.
- Initial candidates require 1..20 valid, eligible, agent-reported observations,
  all linked as supports, including behavioral support from the current run.
  Inspection alone and contradictory source links leave a candidate for review.
- Revisions require a concrete `improve` proposal linked to the exact active,
  eligible target. Direct `revise` remains a candidate for manual approval.
- An automatic improvement requires current-run behavioral evaluation evidence
  and reported outcomes from at least two distinct PR review records. Two PRs
  are a diversity check, **not proof of independent cases**; related PR families
  are not detected. Multiple sessions, commits or runs of one PR cannot satisfy
  this check. The reported outcomes must be tested, applicable, unblocked,
  non-inconclusive, with known contribution and behavioral supporting evidence
  from each outcome's own run matching its failure/pass/refutation category. A usefulness label alone is insufficient.
- All existing source, critic-freshness, profile, repository, generation and
  ownership fences remain. The full proposal graph is validated before automatic
  activation, within the same write transaction. No partial activation is committed.
- Insufficient quality or quota leaves a reviewable candidate with an explicit
  reason. Invalid references and stale targets continue to fail existing checks.
- Automatic decisions identify `lesson_policy_v1`, policy version, origin, selected
  policy event, run, exact candidate and reason in the persistent audit. They are
  never labeled as human approval or independently verified truth. These checks
  validate provenance and structured reports, not the factual truth of free text.

No training, changed model weights, empirical quality improvement or token/cost
saving is claimed. A conditional lesson is guidance that must be checked in each
new investigation, not authority to execute commands or bypass permissions.

## Review, invalidate, reverse

`inspect`, ordinary recall/detail and the existing export show the exact version
and source provenance; export includes audit history. Manual `approve`, `suspend`,
`restrict`, and source `invalidate` remain available. Invalidating a source prevents
retrieval/use of dependent versions through the existing eligibility checks.

Restore the content of a selected historical version as a **new** version:

```sh
hermes review-ledger rollback owner/repo VERSION_ID --reason "Restore the earlier strategy after review" --request-key rollback-1
```

Rollback never deletes or rewrites historical content, approval records or outcomes.
Only previously approved versions can be restored. It copies that version's sources and critic links, requires them to remain eligible,
retires currently active versions, and records the old and new identities. It is
an explicit local-operator decision and is not exposed as a model action. Suspended
content can only return through such a new explicit decision with still-valid
sources; invalid evidence cannot be revived. Repeated request keys return the same
historical receipt, not a fresh action; inspect current state after a receipt.

## Persistence and boundaries

Policy and quota records use the existing audited SQLite schema, so automation
adds no migration. The combined V1 build adds migration 005 for frozen external
references, while preserving migrations 001..004 byte-for-byte. External review
metadata is never eligible lesson evidence and cannot trigger promotion. Normal verified
SQLite backups preserve these records. Configuration stored outside SQLite needs
its own backup as before. Profiles that never select automatic mode stay manual.
There is no new API approval flag, policy toggle, operator identity or filesystem
path accepted from model tool arguments. This application setting does not change
host security permissions, authorize model/provider calls, enable imported skills,
publish files, commit, push, merge, purchase, or alter any personal profile.
