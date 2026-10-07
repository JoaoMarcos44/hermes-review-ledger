# Evidence-based review feedback pilot

This is a manual experiment using the existing V1 record contracts. The Ledger
does not call models, execute checks, adjudicate truth, or update model weights.
It keeps one writer per run and records observations as `agent_reported`.

## Define the question before choosing models

Measure these hypotheses separately:

1. Does an additional analysis improve this review?
2. Does a different model improve more than another pass by the same model?
3. Do approved lessons improve later reviews on held-out case families?

Use synthetic cases or explicitly permitted public material. Do not send Ledger
data to external services. A second model/provider does not inherit permission
to receive private code, conversation history, credentials, or memory. Keep this
pilot separate from private Ledger data; any private input requires separate,
specific authorization and must respect the plugin's data-sharing restrictions.

## Bounded protocol

Before a run, freeze the snapshot, contract and its source, fault assumptions,
allowed inputs/tools, time and cost budget, case family, and evaluation split.
Distinguish safety (a forbidden event) from liveness (progress under stated
recovery and scheduling assumptions). A stale read is not inherently a defect;
compare it with the promised consistency model. A finite wait without a stated
deadline does not establish that eventual progress is impossible.

1. A and B independently inspect the same permitted snapshot and contract.
   Preserve both reports before either sees the other's conclusions. Store
   claims, assumptions and references, not private reasoning transcripts.
2. B receives A's claims and evidence. For each claim, B may agree, challenge,
   request evidence, or remain inconclusive. There is no criticism quota.
3. The authorized investigator/operator checks a discriminating observation:
   what would support the claim, and what would refute it? Examine the checker
   and include relevant negative and positive controls. A proposed correction
   needs its own validation; finding a defect does not validate the correction.
4. The single run owner records the reports and scoped assessments. Neither
   model identity, voting, self-scores nor agreement establishes truth. Preserve
   uncertainty and limitations; obtain human contract review when necessary.
5. Stop after one independent pass and one critique/verification pass, or at the
   declared budget. Extend only for a new, authorized, testable question. Pause
   for missing evidence; do not turn an unresolved disagreement into a verdict.

Checks stay within authorized local defensive/data-integrity work. This guide
does not authorize executing untrusted repository code, exploiting targets,
publishing reviews, changing branches, or running unattended agents.

## Map the protocol to existing records

Use `ledger_record` with its existing `action` and `data` fields. Put the contract,
source, assumptions, participant label, discriminating check, and limitations in
existing text fields such as `details`; these are not new tool arguments.

- Record an untested critique as `kind="note", outcome="incomplete"`, with a
  summary and limitations. Do not call it a behavioral failure or an independent
  verification. A claim can be a `finding`; that creates an unverified record.
- Use `assessment` with `state="inconclusive", basis="none"` and explicit
  limitations when evidence cannot decide. `observation_ids` must be from the
  current run. Historical records guide new checks, not current support.
- Actual authorized inspection may support an inspection-based assessment.
  A behavioral report requires test details and environment. Even when a host
  really ran the test, the Ledger's provenance remains `agent_reported`.
- A blocked check uses `infrastructure_failure`, `timeout`, `skipped`, or
  `incomplete` as appropriate. It cannot support/refute a finding. “No failure
  observed” is weaker than “hypothesis refuted”; record that distinction.
- If a critique merely fails to reproduce a prior report, record the limitation.
  Do not invalidate the source. A demonstrated source error may justify the
  existing invalidation process; dependent assessments then need revalidation
  and dependent lessons are suspended.

For example, this is valid `data` for `action="observation"`:

```json
{
  "kind": "note",
  "outcome": "incomplete",
  "summary": "Critic requests evidence for a claimed progress guarantee",
  "details": "Contract source and recovery assumptions have not been established. A finite observation alone cannot decide eventual progress.",
  "limitations": "Untested critique; no behavioral result or independent verification."
}
```

Propose a conditional lesson only from eligible evidence, with `question`,
`conditions`, `exclusions`, `verification`, and exact observation sources.
Proposals remain candidates until the separate operator approval. Record exact
version applicability before use, and usefulness/result afterward. A valid
exception can motivate a narrower candidate; an architecture change can make
an old lesson inapplicable without making its historical evidence false.

## Evaluation, kept outside the runtime

Compare A with the full budget, B alone, A with a same-model second pass,
A+B independent without debate, and A+B with structured critique. Apply the
same adjudication criteria and comparable total budgets to all conditions.
Blind adjudicators to model labels and vary execution order when feasible.

Record raw counts and denominators, abstentions, inconclusive outcomes, and
adjudication coverage alongside:

- Precision: supported true-defect findings / adjudicated defect findings.
- Known-defect detection: known defects found / evaluable known defects.
- Critique benefit and harm: incorrect initial conclusions corrected, and
  correct initial conclusions incorrectly reversed, with their eligible totals.
- False alarms on related controls; independently adjudicate unexpected defects
  rather than assuming a control is perfect.
- Actual time, monetary cost, tool use, and human effort per useful result.

A zero denominator is undefined, not 100%. Detection applies only to the
benchmark's known defects, not all production bugs. Report unresolved labels
and uncertain estimates rather than inventing a combined quality score.
Related faulty/fixed snapshots and repeated schedules are one case family,
not independent experiments. Choose pilot size and success tolerances before
collecting results; a small pilot diagnoses the procedure, not superiority.

Keep development and evaluation families separate. Historical inputs must not
include future fixes, later discussions, or lessons derived from the evaluated
defect. Declare possible training-data contamination for public cases. For the
learning experiment, freeze approved lessons and compare the same pipeline with
and without them on held-out families. Improvement in today's report does not
establish improvement across reviews. No comparative model experiment or
accuracy improvement is claimed by this guide or its record-contract tests.
