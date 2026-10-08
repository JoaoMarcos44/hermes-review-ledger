# Frozen external review references (Ledger V1)

This additive feature records copied GitHub review metadata beside existing
findings. It does not fetch GitHub discussions, execute quoted instructions,
verify an author's identity, resolve a finding, or approve a lesson. The provider
is currently restricted to `github`; no other provider or host adapter is claimed.
No network collector, sync worker, webhook, model call, or background job is added.

## Record and retrieve

Use the existing `ledger_record` tool with action `external_reference`, the current
run's ownership generation and a unique request key. Its `data` object contains:

- `finding_id`: an existing finding from the same PR
- `provider`: `github`
- `event_type`: `review`, `issue_comment`, or `review_comment`
- `external_id`: the positive decimal source ID as a string
- `url`: the exact canonical source URL for this authorized repository and PR
- `body`: copied UTF-8 text, at most 8,000 characters; it is never executed
- `origin_at`: a timezone-aware source timestamp, or explicit `null` when unknown
- Optional `source_updated_at`: a timezone-aware reported update timestamp or null
- Optional `source_revision`: a full lowercase Git SHA or null, when provided by the source

Canonical source URLs are the PR URL followed by `#pullrequestreview-ID` for a
review, `#issuecomment-ID` for a general comment, or `#discussion_rID` for an
inline review comment. User info, another host/repository/PR, query parameters,
encoded alternatives and arbitrary links are rejected. Validation proves only
that the copied fields have the required shape and scope, not that the remote
source exists or matches the copied text.

The backend assigns the capture timestamp, immutable snapshot identity/version,
content hash, capture sequence and `agent_reported` provenance. Model-supplied
provenance or capture fields are rejected. The relationship to the finding is
agent-reported too; it is not independently verified. Unknown source dates remain
unknown. A reported source commit is distinct from the capture run's code snapshot.

Identical source snapshots reuse their original identity and original capture
metadata; edits create another version instead of replacing historical text.
An invalidated identical snapshot cannot be resurrected by another retry key.
These are application-level append-only guarantees, not tamper-proof storage
against an administrator who can edit the database file. Reads check the stored
content hash. Keep verified backups.

With context enabled, existing `ledger_context` `prepare` returns a bounded page
of metadata references without their bodies. Use `reference_offset` to request
another page when the output reports an omitted page. Retrieve exact text through
`detail`, with `kind: external_reference` and its `record_id`. Existing character,
byte and optional-token caps apply; oversized detail requires a complete semantic
section or a larger operator-authorized cap. A displayed reference does not mean
the body was read.

References from earlier runs are discoverable only within the same PR, carrying
their original capture run and an explicit historical label. They do not become
current-snapshot evidence. A new run must perform its own discriminating checks
through the existing observation/assessment/resolution workflow.

## Time and frozen manifests

`reference_as_of` optionally filters external references by their backend-assigned
local capture time. It does not filter lessons, observations, findings or other
context, and is **not a complete retrospective-review evaluation mode**. In
particular, copying an edited comment today with an older `origin_at` does not
make that text available to yesterday's local cutoff. Dates reported by GitHub
or supplied by an agent do not establish when that exact text first existed.

An independent timestamp cutoff relies on the local clock being accurate. Clock
rollback can make capture timestamps misleading; they are recorded clock values,
not trusted external attestations.

A manifest pins exact reference versions and a capture-sequence watermark.
Resuming cannot silently import later references, including those with older
reported origin dates. Invalidation is still checked now; the filter is not a
way to revive withdrawn material. An independent detail request must carry its
own desired `reference_as_of` cutoff. Changing the filter/page requires a fresh
prepare rather than altering an existing manifest.

## Invalidation and evidence boundary

Use `ledger_record` action `invalidate_external_reference` with `reference_id`
and `reason`, under the current owned run of the same PR. The invalidated snapshot
and audit history remain available in bounded exports. Fresh context excludes
it; resuming a manifest that depended on it fails closed.

A reference ID cannot substitute for an observation ID in an assessment, lesson
source or critic verification. Statements such as “fixed,” approval, disagreement
or a resolved thread are reasons to investigate, not test results. Invalidation
of a copied reference also does not prove that a finding or its original evidence
was false. No people-ranking or automatic inference of semantic relationships
is performed.

## Upgrade and measurement

Package identity remains Ledger V1 / `1.0.1`; the bundled protocol is 2 for the explicitly authorized lesson-automation contract.
SQLite schema becomes **5** via additive `005_review_references.sql`. Migrations
001–004 remain byte-identical. The migration preserves old data and receipts,
checks historical schema layout/profile identity, and is transactional. Stop
older sessions and retain a verified backup before explicitly upgrading a
profile. Schema-4 code must not be used on the migrated profile; no downgrade is
provided. This local implementation did not modify a personal profile.

The user explicitly clarified that enabling automatic mode constitutes the
authorization for eligible future lesson promotions. The combined build includes
that bounded policy, with manual mode still the default. The operator can enable
or disable it; nothing was activated in a personal profile by this implementation.
See [lesson automation](lesson-automation.md). External comments remain ineligible
as evidence and cannot themselves trigger a lesson promotion in either mode.

Additional metadata and detail calls have a cost. Measure complete serialized
prepare/detail/resume trajectories, including omitted-page recovery, rather than
claim savings from a shorter body preview. The synthetic tests establish scope,
versioning and retrieval contracts; they do not establish improved review quality,
provider token charges or model comprehension. Compression remains opt-in.
