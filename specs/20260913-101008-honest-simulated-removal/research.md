# Research: A Simulated Removal Says It Was Simulated

## R1 — Where "was it simulated" comes from

**Decision**: a `simulated: bool` attribute on the version-control boundary — declared on the
`VersionControl` protocol, `False` on `GitVersionControl`, `True` on `SimulatedVersionControl`.

**Rationale**: the issue's own observation: the boundary is what writes `[simulated]` into its audit
records, so it is the one component that cannot be wrong about it. `cancel` works the same way —
its honest wording comes from the boundary's `StopOutcome.method == "simulated"`, not from asking
the level. `Boundaries`' docstring says downstream code "never asks about the level", and three
verbs asking it would be three places to get the table wrong.

**Alternatives considered**:
- *`effects.is_real("version_control", ctx.effect_level)`.* Agrees in production, since `wire()`
  selects the class from the same level, but is a second source of truth — and tests routinely pair
  `SimulatedVersionControl` with `EffectLevel.LIVE`, so the two already disagree there.
- *A `simulated` field on `RemovalResult`, like `WorktreeHandle.simulated`.* Covers
  `remove_worktree` only; `delete_branch` returns a bool and `prune_worktrees` a string, and
  widening both return types to carry one flag the object already knows is churn.

Test fakes that subclass `SimulatedVersionControl` to get its audit records but **really delete the
directory** (`ListingVcs`, `DeletingVcs`) are real-acting and declare `simulated = False`.

## R2 — The wording

**Decision**: the real line with "would" in place of the past tense, then one parenthesised line:

```
would remove worktree /home/…/issue-66
would delete branch robot-army/issue-66-…
  (simulated: effect level is `plan`; version control is real from `local`, so nothing on disk was touched)
```

**Rationale**: the issue's suggested text, and `cancel`'s precedent of saying it in the sentence
rather than in a footnote. One note line rather than a suffix on each line keeps the "would" lines
as short as the real ones and states the reason once.

**Alternatives considered**: suffixing every line with "(simulated)" — repeats itself and still
doesn't say *why* or *what level fixes it*, which is what the operator needs to act on.

## R3 — "Real from `local`" is derived

**Decision**: `effects.real_from(boundary)` — the first `EffectLevel`, in declaration order, at
which `REAL_AT[boundary]` holds.

**Rationale**: `consequences()` derives the banner from `REAL_AT` for the same reason: a
hand-written `local` drifts the day the table changes. `EffectLevel` is declared in ascending
order, which the level ladder already relies on.

## R4 — Cleanup: what changes

**Decision**:
1. When the boundary is simulated, the removal half of every reason reads "worktree removal
   simulated" and the branch half "branch deletion simulated — {evidence}". Retention reasons keep
   their wording after the removal half.
2. After a reported removal, if the worktree directory is still on disk the item is `retained`
   with `SURVIVED_REASON`, and the branch is not attempted — the #59 guard, one shared string.
3. `Decision.simulated` is `True` for decisions reached through a simulated removal; `cleanup`'s
   summary counts items that "would have their worktree removed" and prints the note line.

**Rationale**: the reason is both what `cleanup` prints and what `show` displays months later, so
fixing only the printed line would leave the lie in the record. The survivor guard is the same
defect in persistent form: without it, a `local` round's real directory cleaned at `plan` becomes
`done`, which the automatic pass never revisits.

`retained`, not `skipped`, for a survivor: `skipped` is retried every pass, and at `plan` every
retry simulates the same removal and changes nothing. `cleanup <id>` reconsiders a `retained`
item once the level is real.

**Alternatives considered**: guarding only when simulated — `_remove_checkout` guards
unconditionally, because the check costs one `is_dir()` and real git reporting success over a
surviving directory would be wrong either way.

## R5 — The cross-verb rule

**Decision**: one test, parametrised over `cancel`, `worktree remove`, `worktree prune` and
`cleanup` at `plan`, asserting that the output mentions "simulated", and that every line containing
one of the real path's completed phrasings — `removed worktree`, `deleted branch`, `worktree
removed`, `branch removed`, `nothing to prune`, `had their worktree removed`, `stopped session` —
also contains "simulated".

**Rationale**: the phrase list is exactly the real path's vocabulary, so a verb reworded later
fails the test the moment it copies the real sentence into the simulated branch. "Also contains
simulated in the same line" is what lets `cancel`'s "stopped session … via a simulated stop" pass
without rewording it (FR-010).

## R6 — Machine-readable output and the record

**Decision**: `result.data["simulated"]` and `outcome["simulated"]` are set in `_remove_checkout`,
so they appear whenever git was reached; `worktree prune`'s data gains `simulated`; `cleanup`'s
decisions each carry it.

**Rationale**: a refusal before git has no simulated or real step to describe. Setting it where git
is reached keeps one place responsible for it.
