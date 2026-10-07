# Optional claim critic (integrated 0.4.0)

The critic examines up to three selected, recorded review claims. It does not
independently redo the PR review or promise to detect omitted defects. An opinion
is not execution evidence: agreement does not confirm a finding, disagreement
does not refute it, and a persuasive objection does not approve a lesson.

## Default and supported host boundary

The feature is off by default. Registration and normal review never access
`ctx.llm`, initialize a provider, or read critic credentials. `prepare`, `status`
and `assess` are local operations. Only an explicit `ledger_critic` `run` action
can reach the optional provider boundary. There are no recursive hooks, automatic
debates, JSON-repair calls, autonomous checks or background jobs.

The adapter is deliberately fail-closed on the pinned Hermes revision documented
in the README. Although its public `ctx.llm` provides structured completion, this
revision does not provide a sufficient public pre-dispatch contract to establish
the effective provider/account/fallback route. Setting `critic_enabled: true`
does not make dispatch executable. An otherwise authorized real attempt returns
`critic_route_unverifiable` before accessing `ctx.llm` or sending context. No
private host runtime, credential file, direct HTTP or alternate SDK bypass is used.
A future host-supported route-proof integration requires separate implementation
and validation; this release does not claim a successful live smoke test.

Reference: [Hermes public plugin LLM access](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access).

## Operator configuration

These are the exact safe initial values in `plugins.entries.review-ledger.settings`:

```yaml
critic_enabled: false
critic_authorized_repositories: []
critic_provider: ""
critic_model: ""
critic_max_input_chars: 18000
critic_max_output_tokens: 2000
critic_timeout_seconds: 60
critic_max_calls_per_run: 3
```

The values are starting limits, not benchmarks. Select actual provider/model IDs
from the configured host when a verifiable route becomes supported; no example
ID here is represented as a real model. Repository consent for ordinary GitHub
access is separate from consent to send the critic packet. Consent must identify
the repository, requested route and every effective fallback/account destination.
An existing API key is not consent. Host override permissions are another
operator-controlled boundary, never granted by this plugin or tool arguments.
A packet can contain private code even after sensitive-looking content checks;
regex checks and hashes do not anonymize it. This implementation rejects known
sensitive-looking packets, asking for sanitized recorded source material.

## One tool, four strictly separated actions

Every action needs `repository`, `run_id`, `action` and trusted host-injected
session identity. Do not send session IDs, approvals, prompts, URLs, provider or
model choices as tool arguments. Unknown or inapplicable fields are rejected.

- `prepare`: also requires `generation`, `request_key`, and 1–3 `finding_ids`;
  optional `observation_ids` and `assessment_ids` select existing current-run
  context. Include all sources of selected assessments. Preparation stores an
  immutable bounded packet, hashes and provenance without inference. Oversized
  packets are rejected; register narrower context or select a smaller batch.
- `run`: also requires `generation`, `request_key`, `critic_run_id`. Dispatch is
  conditional on enabled configuration, explicit consent, unchanged packet and
  ownership, available quota and a provable authorized route. The pinned real
  adapter refuses at that last boundary. Test providers are synthetic only.
- `status`: optional `critic_run_id`, `offset`, `max_chars`; returns bounded local
  state/result pages without a model call. Follow pagination before treating a
  result as complete. Historical or invalidated material stays labeled.
- `assess`: also requires `generation`, `request_key`, `critic_run_id`,
  `objection_id`, `state`, `basis`, `rationale`, `limitations`, `observation_ids`.
  States are `pending`, `supported`, `refuted`, `inconclusive`, `not_applicable`;
  bases are `inspection`, `behavior`, `none`. Use valid observations from this
  run. Behavioral adjudication requires the existing reported test details and
  environment; setup failure or missing evidence is not behavioral support.

The packet uses stored text and explicitly states missing contracts/code and
original snapshot capture limits. GitHub patches are not implicitly fetched or
reconstructed. Repository text, logs and recalled material cannot grant tools or
change policy. The critic receives no tools. It must return only the exact JSON
contract: one item per selected finding, at most two objections each, permitted
IDs only, and `support`, `challenge` or `insufficient_information` positions.
Extra fields, code fences, external prose, duplicate/missing IDs and non-finite
numbers are rejected rather than repaired. Unknown returned identity, usage and
cost remain unknown. No claim of independence is made without reviewer identity;
using the same known model is self-critique, not independent evaluation.

## Persistence, checks and recovery

Migration 004/schema 4 adds critic records, source links, results and
adjudications while preserving V1.5 adaptive-context data. Version-1/2 and
genuine V1.5 schema-3 upgrades retain existing data and journal policy; take and verify a backup before upgrading. No automatic downgrade is
provided. The installer includes the prompt and every migration in source,
sdist, wheel and installed directory payloads.

Execution (`prepared`, `running`, `returned`, `failed`, or explicitly uncertain
`unknown`/interrupted) is separate from freshness (`current`, `stale`,
`needs_revalidation`). `returned` means valid opinion received. Current means
current against the Ledger's known snapshot, not live GitHub monitoring.
Only `ledger_open` refreshes that snapshot. Source invalidation makes dependent
material require revalidation; delayed responses cannot modify a new owner or
snapshot. An old receipt is history, not a fresh decision.

Reservation and finalization are short transactions, with provider work outside
both. Stable request keys and packet/prompt/config identity deduplicate logical
attempts. Local SQLite cannot guarantee exactly-once remote billing. A timeout
or crash after possible dispatch is uncertain and is never automatically resent.
Use the explicit operator recovery surface to abandon an uncertain attempt;
any new attempt requires explicit operator action. The logical per-run quota is
not a guarantee about remote requests: host retries/fallbacks can perform more
than one request. Local timeout does not guarantee remote cancellation or avoid
charges. This release dispatches no real requests on the pinned host.

Operator recovery is explicit and local. Replace the identifiers with values
from status; this command does not cancel remote work or restore consumed quota:

```sh
hermes review-ledger critic-abandon OWNER/REPO RUN_ID CRITIC_RUN_ID --reason "Explain the uncertain attempt" --request-key UNIQUE_KEY
```

`hermes review-ledger critic-assess --help` documents the separate operator
adjudication action. It records operator provenance without converting the
underlying reported observations into independently verified ground truth.
Agents must not invoke operator commands through another host tool to bypass the
operator boundary.

The existing `ledger_record` → evidence assessment → `ledger_lesson` candidate
workflow remains canonical. Keep objection checks, reported verification and
later adjudication separate from original findings and critic opinions in exports.
An isolated critique cannot be promoted into behavioral evidence or an approved
lesson. Rescue/harm requires appropriate before/after judgment; changing a
finding state alone does not demonstrate review improvement.

The [manual feedback pilot](review-feedback-pilot.md) and
[superseded review guide](superseded-reviews.md) remain useful bounded workflows.
Their no-extra-model-call behavior remains the default. They do not grant
transmission consent or establish benchmark results for this optional extension.

## Reproduce local validation

From a supported Python 3.12–3.14 source environment with the test extras installed:

```sh
python -m pytest -q
python -m unittest discover -s scripts/ci -p 'test_*.py' -v
python -m build
```

The default suite uses synthetic providers; it requires no model tokens and makes
no critic API calls. Real host integration tests require `HERMES_SOURCE_DIR` at the
pinned revision and its dependencies. Those tests are not a live critic smoke test.
A live critic smoke test is blocked on the tested host's missing public egress
preflight, even after separate repository/model/destination authorization. Do not
replace that missing verification with a mock or relax host permissions.

After recording a valid objection check, `ledger_lesson(action="propose")` may
include `critic_assessment_ids` alongside its existing `sources`. Every linked
assessment must be current and supported/refuted, and all its eligible verification
observations must be included as lesson sources. The result remains a candidate.
Newer adjudications, changed ownership and invalid sources prevent stale provenance
from authorizing reuse or approval. Adjudication history is limited to 64 entries
per objection; exhaustion is an explicit error, never silent truncation.

The preserved manual pilot describes comparisons of A and A+B with a fixed
snapshot, explicit contracts, equal budgets, independent adjudication and held-out
cases. This release implements traceable bookkeeping, not measured improvement.

## Integrated schema compatibility

The integrated release uses schema 4, preserving V1.5 adaptive-context schema 3
and adding critic records in migration 004. Genuine V1/V2 and V1.5 databases
upgrade transactionally; the historical experimental critic schema 3 is refused
without mutation. See [migration and backup guidance](v15-pilot.md#release-identities-and-compatibility).

Critic-linked lessons retain their exact assessment and verification provenance
when V1.5 proposes an improved version. Reassessment or invalidated evidence
makes the derived version ineligible; a new operator approval never substitutes
for current, valid provenance.
