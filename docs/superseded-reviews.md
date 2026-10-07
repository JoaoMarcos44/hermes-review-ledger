# Preserve a superseded PR investigation

A PR can become unnecessary because the base branch already contains a suitable
solution. That conclusion is scoped evidence, not an instruction to merge,
close, push, or discard the investigation.

## Two meanings that must stay separate

- A run's internal `status="superseded"` means an active/paused recorded snapshot
  was replaced by another snapshot. It does not mean the PR is obsolete.
- “PR superseded by an existing solution” is a reported investigation conclusion.
  Record it using existing observations and a completion note; do not invent a
  new run action, assessment state, tool field, or status transition.

The run can be `completed` while the PR remains open awaiting a maintainer.
Completing it records bounded work; it neither proves the conclusion nor closes
the PR. The historical record remains useful even if the proposed code is obsolete.

## Establish the conclusion

1. Read prior runs with `ledger_status(repository, pull_number, ...)`, including
   full paginated details for relevant observations and assessments. These are
   local records, not a live GitHub check. Do not stop at a partial summary.
2. Use `ledger_open` for a fresh authorized snapshot. Compare full HEAD/base,
   comparison, relevant configuration and skill identity with the recorded run.
   New identities require current investigation; old assessments stay historical.
3. Through authorized host tools, identify the prior solution and its contract.
   Record exact revisions, what was compared, observations, limits, and any
   residual work. An empty diff alone does not prove behavioral correctness.
4. Separate the untouched PR diff from a locally constructed integration result.
   If a diff becomes empty only after removing proposed code, say which edits
   produced that result. Do not claim the original PR was identical to main.
5. If behavioral validation was actually performed, record its environment,
   results, controls and limitations separately. Copied discussion, passing CI,
   a test count, or a narrative about a database is not independent verification.
6. If no justified residual change remains in the evaluated snapshot, record
   that bounded conclusion and complete. Do not manufacture an empty commit or
   force a merge. Publication, closing, merging and rebasing require their own
   authorization. If the evidence is missing, pause with the outstanding question.

Example `data` for `ledger_record(action="observation")`, using a synthetic
case and inspection evidence, not invented execution results:

```json
{
  "kind": "inspection",
  "outcome": "inspection",
  "summary": "Synthetic proposal appears unnecessary at the recorded comparison",
  "details": "Inspection of the synthetic fixture found the required contract already represented by the base implementation. No residual change was identified within this inspection's scope.",
  "limitations": "Synthetic inspection only; no target PR or test execution. Does not establish general behavioral correctness or authorize closing the PR."
}
```

For a real case, replace this description only with evidence actually obtained.
Use a completion `note` such as “No justified residual change identified for the
recorded comparison; awaiting maintainer disposition; see recorded limitations.”
Use an assessment only when there is a concrete finding to assess. Do not create
a bug solely to express PR disposition or label “already fixed” as a verified
resolution without the resolution contract's original failure/current check.

## Resume and reuse without a permanent skip

An unchanged completed run can be read to avoid blindly repeating old work.
Status is not a scheduler, a permanent skip flag, or a promise of zero-token
reviews. A new `ledger_open` after completion can create a new run even at the
same snapshot; inspect the returned ID and generation. Do not assume reopening
reactivates the completed run or copies its evidence into the new run.

A paused run is different: it has unfinished work and released ownership. A
trusted session may acquire it only while unowned, using the current generation.
It must honor the saved limitations and current identity. Never take over another
session merely because time has passed.

HEAD/base, comparison configuration, or skill changes require reconsideration.
Even at the same identity, new contradictory evidence or corrected assumptions
can warrant another investigation. Record why work was repeated or why the
existing scoped record was sufficient; do not claim a measured token saving
unless one was actually measured.

Run history and reusable lessons serve different purposes. An approved lesson
may ask “Has the base branch already satisfied this contract?” with conditions,
exclusions, and a verification plan; it must not encode “always skip this PR” or
“this named mechanism is always correct.” `ledger_recall` returns eligible
same-repository versions, not every completed run. Candidate, revoked, suspended,
or invalid-source lessons are not usable guidance. Retrieve the complete exact
version, record applicable/not-applicable/uncertain before use, and retain the
operator approval boundary. Obsolete proposed code does not by itself invalidate
the observation that established its obsolescence.
