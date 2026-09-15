# Feature Specification: A hand-deleted worktree on a finished item is reported

**Feature Branch**: `robot-army/issue-113-a-hand-deleted-worktree-on-a-terminal`

**Created**: 2026-09-15

**Status**: Draft

**Input**: GitHub issue #113 — "A hand-deleted worktree on a terminal item is invisible: no
prunable_worktree anomaly, and worktree remove records nothing". Found by the scenario 10
verification run for #105 (`docs/verification-2026-09-01-cleanup-guards.md`, Finding 2).

## Background

Reconciliation reports a work item whose worktree directory has gone missing as a
`prunable_worktree` anomaly. It only ever looked at items that are **not** finished (`done` or
`abandoned`). A finished item is the ordinary case — its issue closed — and it is exactly the item
the maintainer reclaims disk from, so a finished item whose directory was deleted by hand is
reported by nothing: `reconcile` counts `prunable_worktrees 0`, `anomalies` lists nothing, and
only `git worktree list` says `prunable`. That is the shipped default, `[cleanup] on_issue_close =
false`.

Two things stand in the way of the obvious fix, and the issue describes both:

- A worktree that **cleanup** removed keeps its path on the record on purpose, so "what was at this
  path?" stays answerable. Simply dropping the state filter would flag every worktree cleanup
  legitimately removed. The rule has to be "missing, and nothing on the record accounts for it".
- A worktree removed with **`robot-army worktree remove <id>`** writes no cleanup record. (What it
  does do, since milestone 001, is forget the path — which the issue's text predates noticing, and
  which contradicts the guide's promise that the path is kept after a successful removal.) The
  command the maintainer uses by hand and the pass that runs unattended leave different records of
  the same fact.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — A finished item's missing worktree is reported (Priority: P1)

The maintainer runs with automatic cleanup off. An item finished some time ago. At some point its
worktree directory was deleted by hand — `rm -rf` to reclaim disk, or a tidy-up of the worktree
root. The next reconciliation pass reports it as a `prunable_worktree` anomaly naming the item, the
path, the branch, and what to run about it.

**Why this priority**: It is the defect. Everything else in this feature exists so that this can be
done without false reports.

**Independent Test**: Seed a `done` item with a recorded worktree path that does not exist and no
cleanup record; run a reconciliation pass; one `prunable_worktree` anomaly for that item is open.

**Acceptance Scenarios**:

1. **Given** a `done` item with a recorded worktree path whose directory is gone and no cleanup
   record, **When** reconciliation runs, **Then** one `prunable_worktree` anomaly is raised for it
   and the pass counts it.
2. **Given** the same for an `abandoned` item, **When** reconciliation runs, **Then** it is raised
   the same way.
3. **Given** a finished item whose cleanup record says it was looked at and kept (`retained`, or
   `skipped` because a session was live), **When** its directory is then deleted by hand and
   reconciliation runs, **Then** it is raised — "kept" does not account for "missing".
4. **Given** a finished item whose cleanup record says its worktree was removed (`done` or
   `branch_retained`), **When** reconciliation runs, **Then** nothing is raised for it, however many
   passes run.
5. **Given** a raised anomaly, **When** further passes run while nothing changes, **Then** no second
   open anomaly is created for the same item.

---

### User Story 2 — `worktree remove` records what it did, as cleanup does (Priority: P1)

The maintainer removes a finished item's worktree by hand with `robot-army worktree remove <id>`.
Afterwards `robot-army show <id>`, the item's web page and `worktree list` say what happened — the
worktree was removed by that command, whether its branch went with it, and when — and the recorded
path and branch are still there to answer "what was at this path?". Reconciliation does not report
the now-missing directory, and the automatic cleanup pass does not reconsider the item.

**Why this priority**: Without it, User Story 1 reports every worktree the maintainer removed by
hand, which is worse than the gap. It is also the honest record the issue asks for.

**Independent Test**: Run `worktree remove <id>` on a `done` item with nothing live and a clean
tree; the item's cleanup record is `done`, its path and branch are unchanged, it is not a cleanup
candidate, and a following reconciliation pass raises nothing for it.

**Acceptance Scenarios**:

1. **Given** a finished item with a clean worktree and nothing running, **When** `worktree remove
   <id>` removes the worktree and deletes the branch, **Then** the item's cleanup record is `done`
   with a reason naming `worktree remove`, the time is recorded, and the worktree path and branch
   remain on the record.
2. **Given** the removal succeeds but the branch survives, **When** it is recorded, **Then** the
   cleanup record is `branch_retained` with a reason saying the branch is still there.
3. **Given** a forced removal, **When** it is recorded, **Then** the reason says it was forced.
4. **Given** a removal refused by any guard — a live session, git's dirty-tree refusal, a directory
   still on disk after a reported removal, an unresolvable repository, a declined confirmation —
   **When** the command exits, **Then** no cleanup record is written and nothing on the item
   changes.
5. **Given** a simulated removal (version control below its real level), **When** it is recorded,
   **Then** the reason says the removal was simulated, in the words cleanup already uses for that.
6. **Given** an item that is **not** finished (for example `failed`, `interrupted` or
   `awaiting_review`), **When** its worktree is removed, **Then** no cleanup record is written and
   the path is forgotten as it is today, because such an item can still be given a fresh worktree
   and a record describing the old one would then be wrong.

---

### User Story 3 — A reported item can be settled with the command the report names (Priority: P2)

The maintainer sees the anomaly from User Story 1 and wants the condition to go away properly:
git's leftover record cleared, the branch dealt with, and the item's record saying the worktree is
gone. They run the command the anomaly names, `robot-army worktree remove <id>`, and it works even
though the directory is already gone — including after `worktree prune` has already cleared git's
record. From then on reconciliation does not raise the anomaly again, even after it is
acknowledged.

**Why this priority**: The anomaly index only suppresses a repeat while the anomaly is open, so an
acknowledged report for a finished item would come back on the next pass for as long as the record
is unchanged. The report is only useful if there is a way to act on it.

**Independent Test**: Seed a `done` item whose directory is gone; run `worktree remove <id>`; it
exits zero, records `done` (or `branch_retained`), and a pass after acknowledging the anomaly
raises nothing.

**Acceptance Scenarios**:

1. **Given** a finished item whose directory is gone and git still lists it as a prunable worktree,
   **When** `worktree remove <id>` runs, **Then** git's record is cleared, the branch half is
   attempted, and the outcome is recorded.
2. **Given** a finished item whose directory is gone and git no longer knows the worktree (already
   pruned), **When** `worktree remove <id>` runs, **Then** git's "not a working tree" is treated as
   a statement about its record, not a refusal about contents — the branch half is attempted and the
   outcome recorded — exactly as cleanup already treats it.
3. **Given** the directory is gone and the branch was already deleted too, **When** `worktree
   remove <id>` runs, **Then** it reports the branch as already gone and records `done`; it never
   reports a branch that does not exist as one that survived.
4. **Given** an item whose cleanup record already says its worktree was removed, **When** `worktree
   remove <id>` runs again, **Then** it says the worktree was already removed, when, and by what
   decision, touches nothing, and exits non-zero.
5. **Given** the anomaly for a `done` item, **When** the maintainer reads it, **Then** it names
   `worktree remove <id>` and, because the item is `done`, `cleanup <id>` as ways to settle it; for
   an `abandoned` item it names only `worktree remove <id>`, since cleanup does not consider
   abandoned items.

---

### Edge Cases

- **Automatic cleanup on.** A `done` item whose directory was deleted by hand is reclaimed by
  cleanup in the same pass, before the sweep looks, so it is recorded rather than reported.
- **Items removed by hand before this change** have no path on record and nothing to report. There
  is nothing to backfill, and nothing is.
- **A directory on disk but git's worktree record gone** is not this feature's condition; it is
  not "missing", and the sweep already treats git's listing and the disk as either being enough to
  call a directory missing.
- **A live session on a finished item whose directory is gone.** `worktree remove <id>` still
  refuses without `--force`; the session guard does not change.
- **The item's repository no longer resolves.** Reported as `config_missing_repo`, as it is for
  unfinished items today; now also for finished ones with a worktree still on record.
- **A second removal attempt on a recorded removal** is refused before git is reached, so it cannot
  report a false "branch still exists" warning.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Reconciliation MUST consider every item with a recorded worktree path for the
  missing-directory report, whatever its work state.
- **FR-002**: An item MUST NOT be reported as missing its worktree when its cleanup record says the
  worktree was removed (`done` or `branch_retained`). Any other cleanup record, or none, does not
  account for a missing directory.
- **FR-003**: When `robot-army worktree remove <id>` removes the worktree of a finished item
  (`done` or `abandoned`), it MUST record a cleanup decision on the item: `done` when the branch
  was deleted, was already gone, or there was none; `branch_retained` when the branch survived.
  The reason MUST name the command, and MUST say when the removal was forced or simulated.
- **FR-004**: After such a removal the item's worktree path and branch MUST stay on the record, as
  they do after cleanup.
- **FR-005**: For an item that is not finished, `worktree remove <id>` MUST keep today's behaviour
  on success — forget the path, write no cleanup record.
- **FR-006**: A removal that is refused, aborted or abandoned MUST write no cleanup record and MUST
  NOT change the item.
- **FR-007**: `worktree remove <id>` MUST succeed for an item whose worktree directory is already
  gone, whether or not git still has a record of it, provided no session guard refuses; git's "not
  a working tree" for an absent directory is not a refusal. It MUST NOT treat git's refusal as
  success while the directory exists.
- **FR-008**: When the worktree directory was already gone, `worktree remove <id>` MUST report a
  branch that no longer exists as already gone, not as surviving.
- **FR-009**: `worktree remove <id>` MUST refuse, touching nothing, for an item whose cleanup record
  already says the worktree was removed, and say when and by what decision.
- **FR-010**: The missing-worktree anomaly for a finished item MUST name the command(s) that settle
  it: `worktree remove <id>`, plus `cleanup <id>` for a `done` item.
- **FR-011**: The recorded decision MUST be written as part of the existing `worktree.remove` audit
  action — its outcome says which cleanup state was recorded — so the log alone says the removal
  happened and what the record now says.

### Key Entities

- **Work item cleanup record** — the existing three fields (state, reason, time) describing what
  happened to a finished item's disk. Gains a second writer, the manual removal command, and a
  second reader, the missing-worktree sweep. No new state value.
- **`prunable_worktree` anomaly** — unchanged in shape; raised for more items, and its note names
  the settling command for finished ones.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In quickstart scenario 10 run with automatic cleanup off, item d (a `done` item whose
  worktree was deleted by hand) produces exactly one `prunable_worktree` anomaly.
- **SC-002**: Zero missing-worktree reports are raised for items whose worktree was removed by
  cleanup or by `worktree remove`, over any number of passes.
- **SC-003**: After `worktree remove <id>` on a finished item, `show <id>` states the removal, its
  time, the path and the branch — every one of the four answerable without reading the log.
- **SC-004**: Every item reported by the new rule can be settled with one command named in the
  report, after which zero further reports are raised for it.

## Assumptions

- Cleanup runs before the missing-worktree sweep in a reconciliation pass, so automatic cleanup,
  when enabled, records a hand-deleted `done` item before the sweep would report it. To be
  confirmed in the plan.
- The existing cleanup states are enough: a manual removal is the same fact on disk as cleanup's
  `done` and `branch_retained`, and the reason distinguishes who did it. A new state value would
  have to be taught to every reader of the column for no decision that depends on it.
- Anomalies of this kind do not resolve themselves; that remains so. A settled item is not raised
  again, and an open report is acknowledged by the maintainer as today.
- The verification record and the quickstart are not amended: the quickstart's expectation becomes
  true.
