# Implementation Plan: A hand-deleted worktree on a finished item is reported

**Branch**: `robot-army/issue-113-a-hand-deleted-worktree-on-a-terminal` | **Date**: 2026-09-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260915-174645-terminal-prunable-worktree/spec.md`

## Summary

`reconcile._sweep_worktrees` (`reconcile.py:2072`) reports a missing worktree directory as a
`prunable_worktree` anomaly, but only for items that are not `done` or `abandoned`. A finished item
is the normal case and the one disk gets reclaimed from, so with cleanup off (the default) a
hand-deleted finished worktree is reported by nothing.

The state filter cannot simply go: cleanup keeps `worktree_path` after a successful removal on
purpose, so every legitimately cleaned item would be flagged. The rule the sweep needs is "the
directory is missing **and the item's cleanup record does not say it was removed**". That makes the
cleanup record load-bearing, and `robot-army worktree remove <id>` does not write one — it forgets
the path instead (R1), which is also why the guide's "the path is kept after a successful removal"
has been untrue for manual removals since milestone 001.

Four changes, in dependency order:

1. **`WorkItem.worktree_reclaimed`** — `cleanup_state` is `done` or `branch_retained`. The one
   definition of "the record accounts for a missing directory" (R4).
2. **`worktree remove <id>` records what it did, for finished items.** On success it writes the
   same cleanup record cleanup would — `done` or `branch_retained`, reason naming the command —
   and keeps the path. For an unfinished item it keeps today's behaviour, because such an item can
   still get a fresh worktree and a record would then describe the wrong one (R3).
3. **`worktree remove <id>` can settle what the sweep reports.** A directory that is already gone
   is not a refusal (R6); a branch that is already gone is not a survivor (R7); a removal the record
   already accounts for is refused as `already_removed` before git is reached (R8).
4. **The sweep drops the state filter** and skips `worktree_reclaimed` items instead, and its note
   names the settling command for finished items (R10).

No migration, no new state value, no new action name, no configuration.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added.

**Storage**: SQLite via `db.py`. **No migration** — the three cleanup columns (migration 004) gain a
second writer. See [data-model.md](./data-model.md).

**Testing**: pytest. New `tests/unit/test_terminal_prunable_worktree.py` for the sweep and the
record; extensions to `tests/unit/test_worktree_remove_guard.py`, `tests/unit/test_simulated_wording.py`
and `tests/integration/test_worktree_removal.py` (real git: a hand-deleted directory, before and
after `git worktree prune`).

**Target Platform**: single Linux machine.

**Project Type**: single-project CLI/daemon with a local web interface. The web page and `show`
already render the cleanup columns and need no change.

**Performance Goals**: n/a. The sweep gains the finished items with a recorded path; it already
lists worktrees once per clone per pass, not once per item.

**Constraints**: cleanup's own behaviour and records are unchanged; every refusal `worktree remove`
has today still refuses, and still changes nothing (FR-006).

**Scale/Scope**: `models.py`, `cleanup.py` (one private helper made public), `operations.py`,
`reconcile.py`; four guide pages.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see "Post-design re-check" below.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | Passes, no Complexity Tracking entries. No new state value (R2), no new table, no knob. One property replaces a state filter; `cleanup._branch_exists` gains a second caller instead of being copied. The terminal/unfinished split in `worktree remove` is the one branch added, and it exists because the alternative leaves a stale record that silently disables cleanup for the item (R3). |
| **II. Single-User, Local-First** | Passes. Local SQLite and git only. |
| **III. Total Accountability** | Passes, and closes a gap: the `worktree.remove` outcome gains `cleanup_state` (what the row now says), `worktree_already_gone` and `branch_already_gone`, and the new `already_removed` refusal is recorded like every other. No Principle III exception is claimed. |
| **IV. Interruption Tolerance** | Passes; see below. The record is written in one transaction after the removal, and every kill point leaves a state the sweep reports and the same command settles. |
| **V. Public Code, Unsupported Project** | Passes. `result.data` gains keys with no shim. The guide pages are corrected in the same change, including the `state.md` claim that was already false. |

### What this logs

- **`worktree.remove`** — unchanged name and pair. The outcome gains:
  - `cleanup_state`: the value written to the row, present only when one was written;
  - `worktree_already_gone: true` when git's refusal was about an absent directory (R6);
  - `branch_already_gone: true` when the branch did not exist to delete (R7);
  - `refused_by: "already_removed"`, with `reason`, for R8's refusal.
  Full shape in [contracts/manual-removal-record.md](./contracts/manual-removal-record.md).
- **`git.remove_worktree`, `git.delete_branch`** — unchanged; `git.delete_branch` is absent when
  the branch was already gone, and absent on `already_removed`.
- **`cleanup.*`** — unchanged. Cleanup's path is not touched.
- **The `prunable_worktree` anomaly** — raised for more items; the row is the record, as today.

Nothing this feature adds goes unlogged.

### What happens if it is killed halfway

- **Killed after git removed the worktree, before the branch half**: directory gone, branch
  present, no record. For a finished item the path is still on the row, so the next pass reports
  `prunable_worktree`, and `worktree remove <id>` finishes the job: git's refusal about an absent
  directory is not a refusal (R6), the branch half runs, the record is written. Before this change
  the same kill left a finished item invisible.
- **Killed after both removals, before the record commits**: the same, and the re-run finds the
  branch already gone (R7) and records `done`.
- **Killed at the confirmation prompt**: unchanged — nothing removed, `audit.action` records it.
- For an **unfinished** item the path is cleared in the same place as today, so its interruption
  windows are unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/20260915-174645-terminal-prunable-worktree/
├── plan.md
├── research.md                      # R1..R11
├── data-model.md                    # no migration; one property, a second writer
├── quickstart.md                    # scenario 10 item d, and the settling command
├── contracts/
│   └── manual-removal-record.md     # M1..M16
├── checklists/requirements.md
├── spec.md
└── tasks.md                         # /speckit-tasks
```

### Source Code (repository root)

```text
src/robot_army/
├── models.py         # WorkItem.worktree_reclaimed
├── cleanup.py        # _branch_exists → branch_exists (second caller)
├── operations.py     # worktree_remove: already_removed refusal, record for finished items;
│                     #   _remove_checkout: absent directory, absent branch
└── reconcile.py      # _sweep_worktrees: no state filter; skip worktree_reclaimed; the note

tests/unit/test_terminal_prunable_worktree.py   # NEW
tests/unit/test_worktree_remove_guard.py        # path kept for finished items
tests/unit/test_simulated_wording.py            # the simulated reason
tests/integration/test_worktree_removal.py      # real git, hand-deleted directory

docs/guide/5-outcome.md    # worktree remove records; the table
docs/guide/operating.md    # prunable_worktree now covers finished items
docs/guide/state.md        # cleanup columns' second writer; the path claim; interruption rows
docs/guide/audit-log.md    # the issue #113 records
```

**Structure Decision**: unchanged single-project layout; nothing new outside `tests/` and this
spec directory.

## Complexity Tracking

No entries.

## Post-design re-check (after Phase 1)

- **I.** The design shrank: an early sketch added a `removed` cleanup state and an anomaly
  resolver. R2 and R11 removed both. **Passes.**
- **III.** The contract added `worktree_already_gone` / `branch_already_gone` so the log says why a
  removal that git "refused" is recorded as a success. **Passes.**
- **IV.** Every kill point now ends in a reported, settleable state. **Passes.**

**Gate: PASSED.** No `NEEDS CLARIFICATION` remains; the spec's assumption about pass order is
confirmed in R5.
