# Data Model: Spec Kit Awareness

Four columns and two frozen dataclasses. No new table, no new state machine, and no change to either
existing one — a phase is not a state (FR-016), and the surest way to keep that true is for it never
to appear in `WORK_ITEM_TRANSITIONS`.

## Migration 007 — `work_items`

| Column | Type | Null | Written by | Meaning |
|---|---|---|---|---|
| `speckit_baseline` | TEXT | yes | `worktree.prepare`, once, in the same transaction as `worktree_path` and `branch` | JSON array of the feature directory names present in the worktree at creation. `NULL` means no baseline was recorded — a pre-migration item, or one prepared before this shipped. Not the same as `[]`, which means "a Spec Kit worktree with no existing features". |
| `speckit_phase` | TEXT | yes | the observation pass, on transition only | The last derived rung: `specify`, `plan`, `tasks`, or `implement`. `NULL` means nothing has been observed, which is a legitimate resting state. |
| `speckit_feature_dir` | TEXT | yes | with `speckit_phase` | The worktree-relative directory the phase was derived from, e.g. `specs/007-speckit-extensions`. Carried so the display can name it and so a change of directory is distinguishable from a change of rung. |
| `speckit_phase_at` | TEXT | yes | with `speckit_phase` | UTC ISO-8601 timestamp of the transition. This is what "since 14:02" on the item view reads. |

`ALTER TABLE ... ADD COLUMN` four times. No backfill: every column is nullable and `NULL` is the
correct value for every existing row. `models.WorkItem` gains the four matching optional fields.

### Why `speckit_baseline` lives on the item and not on the repo

The baseline describes *one worktree at one moment*, not a repository. Two items in the same
repository dispatched a week apart have different baselines, and the second one's must include the
first one's feature directory if that work was merged in the meantime. A repository-level cache would
be wrong exactly when two items overlap, which is the case it exists for.

## `speckit.Detection`

```text
Detection
  detected:     bool          # both halves present
  scaffolding:  bool          # .specify/ and .specify/templates/spec-template.md
  commands:     tuple[str,…]  # which of the four lifecycle commands were found
  form:         str | None    # "skills" | "commands" | "mixed" | None
  reason:       str           # why not, when not: which half was missing, or unreadable
```

Derived on demand from a path; never stored. Produced twice per repository for different paths and
different questions: against the **worktree** at dispatch (FR-001, what the session will see) and
against the **primary clone** for the repositories listing (FR-021, what the author is asking about).
The same function answers both; only the path differs.

`detected` is true only when `scaffolding` is true and all four commands were found. `reason` always
carries a sentence fit to appear in the log verbatim.

## `speckit.Phase`

```text
Phase
  rung:         str   # specify | plan | tasks | implement
  feature_dir:  str   # worktree-relative
```

Also derived, never returned partially. `observe()` returns `None` for every "nothing to say" case,
which is deliberately one value rather than several: no `specs/` directory, no directory outside the
baseline, a new directory with no artifacts yet, an unreadable worktree, and a removed worktree are
all the same instruction to the caller — *leave what is recorded alone*.

## The rules that govern writes to the phase columns

These are the whole state model, and they are stated as rules rather than as a transition table
because the ladder is ordered and the interesting cases are the ones that refuse to move.

1. **`NULL` baseline ⇒ never observe.** No phase is derived and no column is written.

   *Built differently from the first draft of this rule, and the change is worth recording.*
   The draft said the reason was "recorded once per item"; doing that needs somewhere to
   remember that it had been logged, which is a persistent column for a log line. Principle I
   settles that. Instead the silence is explained where the question is actually asked:
   `robot-army show` says "detected, but no baseline was recorded for this worktree, so no
   phase is derived" for exactly this case, computed at render time from four `stat` calls.
   Nothing is logged per cycle, and nothing is logged per item either.
2. **Advance only, within a feature directory.** If the derived rung is at or below the stored rung
   for the same `speckit_feature_dir`, nothing is written and nothing is logged. A ladder that can
   descend would turn an ordinary artifact edit into a spurious transition.
3. **A change of feature directory is recorded as such.** If the derived directory differs from the
   stored one, the new rung is written whatever its height, and the record names both directories.
   This is the "two features in one worktree" case; it may look like a step backwards and the record
   is what stops it reading as a bug.
4. **Absence never clears.** `observe()` returning `None` leaves every column untouched. A worktree
   removed by cleanup leaves the last known phase standing as history.
5. **One record per transition.** No record is written when nothing changed (the Principle III
   omission named in the plan).
6. **The audit line precedes the commit.** Same ordering as `states.transition_work_item`: a crash
   between them can duplicate a line on re-derivation, never lose one.

## Relationship to existing entities

- **`WorkItem`** gains four fields and no behaviour. Nothing reads them to make a decision — only to
  display (FR-015) and to detect transitions.
- **`RepoConfig`** gains `speckit: bool | None`, resolved by `Config.speckit_enabled_for(key)` in the
  same shape as `permission_mode_for` and `model_for`.
- **`ReconcileResult`** gains one integer counter, `speckit_phase_changes`, so the pass appears in the
  startup summary alongside every other thing reconciliation did.
- **Anomalies, health, capacity, ordering, cleanup, and both state machines** are untouched. That is a
  requirement (FR-016), not an observation.
