# Feature Specification: No Worktree Is Left Where robot-army Cannot Reach It

**Feature Branch**: `robot-army/issue-59-purge-simulated-orphans-worktrees`

**Created**: 2026-09-10

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#59 — "purge-simulated orphans worktrees beyond the
reach of the command it tells you to use, and nothing detects them afterwards" (labels: bug,
robot-army). Found cleaning up after the live round of the issue #1 verification, 2026-08-30.

**Baseline**: written against `main` at bd03aa3. Verified in that tree rather than assumed:

- `operations.purge_simulated` (`src/robot_army/operations.py:4021`) counts and deletes every
  `dry_run` work item, session and card, then prints "worktrees those rows created were NOT
  removed — use `worktree remove`". `db.purge_simulated` touches no disk by design.
- `worktree remove` (`operations.worktree_remove`, `:2534`) takes only an `item_id`
  (`cli.py:158`, `type=int`), looks the row up, and fails with "no work item with id N" when
  there is none — which, after a purge, is every simulated one.
- `worktree list` iterates work items carrying a `worktree_path`; with none it prints "no
  worktrees recorded", whatever is on disk.
- `cleanup` decides from work items too, so it cannot see a directory with no row.
- Reconciliation's `_sweep_worktrees` (`reconcile.py:2050`) walks work items and raises
  `prunable_worktree` when an item's directory is **gone**. Nothing walks the other way.
- A worktree's path is `<worktree_root>/<repo short name>/issue-<n>` (`prompt.worktree_dir`),
  and git's own list of a clone's worktrees gives each one's branch (`WorktreeInfo.branch`).
- `version_control` is real at `local`, `no-remote` and `live` (`effects.REAL_AT`), so a
  `dry_run` row can own a **real** directory — which is exactly what the report found: rows
  from a `no-remote` round, purged, leaving 80 MB behind.

## User Scenarios & Testing *(mandatory)*

<!--
  Three stories, one per route the issue suggests, in the order they close the window:
  prevent (purge takes the worktrees with it), detect (reconciliation reports a directory no
  row claims), and repair (a directory with no row is still removable by robot-army). Each is
  useful alone; together a purge can no longer put disk beyond robot-army's reach.
-->

### User Story 1 - Purging simulated rows can take their worktrees with them (Priority: P1)

After a rehearsal the maintainer runs `purge-simulated`. Today it deletes the rows and then
tells them to use a command that needs those rows. After this change, the purge looks at the
rows *before* deleting them, finds those whose worktree directory still exists on disk, names
each one (path, branch, size) in its confirmation, and offers to remove them — worktree and
branch both, under the same guards `worktree remove` applies. Only the removals the maintainer
agrees to happen. Whatever is left behind is named by path in the output, with a command that
can remove it.

**Why this priority**: It is the reported defect, and it is the only fix that closes the window
rather than cleaning up after it: the purge is the last moment anything knows which directory
belonged to which row and which branch.

**Independent Test**: Create a simulated work item whose worktree exists on disk, run
`purge-simulated` and accept the worktree removal. Confirm the directory and its branch are
gone, the rows are gone, and the audit log records each removal before the rows are deleted.

**Acceptance Scenarios**:

1. **Given** simulated rows, two of which own existing worktree directories, **When**
   `purge-simulated` runs interactively, **Then** the confirmation lists both paths with their
   branches and sizes, and asks separately whether to remove them — the default answer being
   no.
2. **Given** the maintainer agrees to both questions, **When** the purge completes, **Then**
   both worktrees and their branches are removed, then the rows are deleted, and the output
   says what was removed.
3. **Given** the maintainer agrees to purge the rows but declines the worktree removal,
   **When** the purge completes, **Then** the rows are deleted, the directories are untouched,
   and the output names each surviving path with the exact `robot-army worktree remove <path>`
   command that removes it.
4. **Given** one of the worktrees holds uncommitted changes, **When** the maintainer accepts
   removal, **Then** git's refusal stands for that one (it is not forced), the others are
   removed, and the output names the refused path, why it was refused, and how to remove it.
5. **Given** `--yes` without asking for worktree removal, **When** the purge runs, **Then**
   rows are deleted and no worktree is touched — a flag that skips a question about rows does
   not also answer a question about directories.
6. **Given** `--yes` together with an explicit request to remove worktrees, **When** the purge
   runs, **Then** the worktrees are removed without a prompt, under the same guards.
7. **Given** a simulated row whose recorded directory no longer exists, **When** the purge
   runs, **Then** it is not offered for removal and nothing is reported as removed.

---

### User Story 2 - Reconciliation reports a worktree no work item claims (Priority: P2)

A directory under the worktree root that looks like one robot-army made, and that no work item
claims, is reported as an `orphan_worktree` anomaly — the mirror of `prunable_worktree`, which
reports a claim with no directory. It carries the path, which clone lists it as a worktree and
on which branch (when one does), and how to remove it. It retracts itself once the directory is
gone or a work item claims it again. Nothing is removed automatically.

**Why this priority**: Story 1 prevents new orphans from purges; this finds the ones that
already exist (the machine in the report has three) and any made by another route — a hand
`DELETE`, a row lost to some future bug. Disk here is at 93%; a condition that costs disk and
is visible nowhere is the problem as much as the purge is.

**Independent Test**: Create `<worktree_root>/<repo>/issue-99` with no work item claiming it,
run a reconciliation pass, and confirm one `orphan_worktree` anomaly naming that path. Remove
the directory, run another pass, and confirm the anomaly is resolved with an `anomaly.resolved`
record.

**Acceptance Scenarios**:

1. **Given** a directory `issue-<n>` under a configured repository's folder in the worktree
   root that no work item (real or simulated, in any state) records as its worktree, **When**
   reconciliation runs, **Then** one `orphan_worktree` anomaly is raised for that path, and
   the pass summary counts it.
2. **Given** the anomaly is open, **When** the next pass finds the same directory still
   unclaimed, **Then** no duplicate is raised.
3. **Given** the anomaly is open, **When** the directory is removed by any means, **Then** the
   next pass resolves it and records why.
4. **Given** a directory that a work item does claim — including a `done` item cleanup has not
   reached yet — **When** reconciliation runs, **Then** no `orphan_worktree` is raised for it.
5. **Given** a directory whose name is not the shape robot-army creates, or a folder for a
   repository not configured, **When** reconciliation runs, **Then** it is not reported —
   the worktree root may be shared with the maintainer's own checkouts.

---

### User Story 3 - A worktree with no row can still be removed by robot-army (Priority: P3)

`worktree remove` accepts a path as well as an item id, so the advice the purge and the anomaly
give is advice that can be followed. Given a path under the worktree root that no work item
claims, it finds the clone that lists it as a worktree, removes the worktree and deletes its
branch — the same two steps, the same refusals, the same audit — without a row to start from.
`worktree list` also shows these unclaimed directories, so "no worktrees recorded" is never
printed over disk robot-army made and still holds.

**Why this priority**: Story 1 prevents, story 2 detects; this is what makes the orphans that
already exist, and any that stories 1 and 2 surface, removable without dropping to
`git worktree remove` and remembering the branch separately.

**Independent Test**: With an unclaimed worktree on disk, run
`robot-army worktree remove <path>` and confirm the worktree and its branch are gone and the
audit log records the removal by path.

**Acceptance Scenarios**:

1. **Given** an unclaimed worktree under the worktree root that a configured clone lists,
   **When** `worktree remove <path>` runs, **Then** the worktree is removed, its branch is
   deleted, and both are recorded.
2. **Given** the worktree is dirty, **When** `worktree remove <path>` runs without `--force`,
   **Then** git's refusal stands and is reported as it is for the item-id form; `--force`
   overrides it only after a typed confirmation.
3. **Given** a path a work item claims, **When** `worktree remove <path>` runs, **Then** it
   refuses and names the item id to use instead — the session guard lives on that route, and a
   second route around it would be a way to skip it.
4. **Given** a path outside the worktree root, or one no configured clone lists as a worktree,
   **When** `worktree remove <path>` runs, **Then** it refuses and removes nothing.
5. **Given** a live worker process whose working directory is inside the path, **When**
   `worktree remove <path>` runs, **Then** it refuses exactly as the item-id form refuses over
   a live session, and `--force` overrides it only with the same typed confirmation.
6. **Given** unclaimed worktrees on disk, **When** `worktree list` runs, **Then** they are
   listed, marked as claimed by no item, and the output does not say "no worktrees recorded".

---

### Edge Cases

- **The effect level has moved since the rows were made.** A `no-remote` rehearsal leaves real
  directories; purging at the level where version control is simulated would *log* a removal
  and do nothing. A worktree MUST NOT be reported as removed while its directory is still on
  disk. Whatever the level, the report follows the disk.
- **Killed partway through a purge that is removing worktrees.** Removals happen before the
  rows are deleted, one worktree at a time, each recorded before and after. A kill leaves the
  rows in place, so re-running the purge finds the survivors and offers them again and skips
  the ones already gone. A row whose worktree was removed has that fact recorded on it at the
  time, so the item-id form and the `prunable_worktree` sweep agree with the disk in between.
- **Removal of the worktree succeeds and branch deletion fails** (unmerged branch, git error).
  Reported the way `worktree remove` reports it today — a warning naming the surviving branch
  — and the purge continues.
- **A simulated session is still open under a row being purged.** The worktree's removal is
  refused on the same session guard `worktree remove` uses; the rows are still purged if the
  maintainer agreed to that, and the refused path is named. Story 2 and story 3 then cover it.
- **A worktree in detached HEAD** has no branch. Only the directory is removed, and nothing is
  reported as a surviving branch.
- **Two configured repositories with the same short name** share one folder under the root.
  An unclaimed directory there is attributed to whichever clone lists it; if none does, it is
  still reported (it holds disk) but cannot be removed by path.
- **A directory under the root that no clone lists as a worktree** — a half-created worktree,
  or one whose clone has been pruned. It is reported as orphaned, saying no clone lists it;
  path removal refuses it rather than deleting an arbitrary directory, and the anomaly says so.
- **A clone's worktree list cannot be read** on a pass. The pass records the error and still
  reports by the directory alone (whether a row claims it needs no git); it does not resolve an
  open anomaly on the strength of a read that failed.
- **The worktree root does not exist.** Nothing to report; not an error.
- **The session this very CLI is running in lives in a worktree** under the root. It is claimed
  by its work item and is never reported or offered.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `purge-simulated` MUST determine, before deleting anything, which simulated work
  items record a worktree whose directory exists on disk, and MUST name each (path, branch,
  size) when it asks for confirmation.
- **FR-002**: `purge-simulated` MUST ask about removing those worktrees as a separate question
  from deleting the rows, defaulting to no. The existing `--yes` MUST continue to answer only
  the row question; removing worktrees non-interactively MUST require its own explicit flag.
- **FR-003**: Each worktree removal made by `purge-simulated` MUST remove both the worktree and
  its branch, MUST apply the same session guard and the same unforced git refusal as
  `worktree remove`, and MUST NOT force.
- **FR-004**: Worktree removals MUST happen before the rows are deleted, each recorded in the
  audit log before it is attempted and with its outcome after.
- **FR-005**: `purge-simulated`'s output MUST name every worktree it did not remove whose
  directory still exists, with the exact command that removes it. The current sentence
  pointing at a command that needs the deleted rows MUST go.
- **FR-006**: No command MUST report a worktree as removed while its directory still exists.
- **FR-007**: Reconciliation MUST raise an `orphan_worktree` anomaly for each directory named
  `issue-<n>` directly under a configured repository's folder in the worktree root that no work
  item, real or simulated and in any state, records as its worktree.
- **FR-008**: The `orphan_worktree` anomaly MUST carry the path, whether a configured clone
  lists it as a worktree and on which branch, and the command that removes it.
- **FR-009**: An open `orphan_worktree` MUST be resolved, with an `anomaly.resolved` record
  saying why, on the first pass that finds the directory gone or claimed by a work item — and
  only on such a positive observation.
- **FR-010**: Reconciliation MUST NOT remove, move, or modify an orphaned directory.
- **FR-011**: `worktree remove` MUST accept a path in place of an item id. The path form MUST
  refuse a path outside the worktree root, a path a work item claims (naming the item id to use
  instead), and a path no configured clone lists as a worktree.
- **FR-012**: The path form MUST remove both the worktree and its branch, MUST keep git's
  refusal of a dirty tree unless `--force` is given with a typed confirmation, and MUST refuse
  while a live worker's working directory is inside the path, under the same override.
- **FR-013**: The path form MUST write an audit record under the same action as the item-id
  form, identifying the worktree by path rather than by item.
- **FR-014**: `worktree list` MUST include unclaimed worktrees under the worktree root, marked
  as claimed by no item, in both its text and its machine-readable output.
- **FR-015**: `orphan_worktree` MUST appear everywhere anomaly kinds are enumerated for display
  and filtering.

### Out of scope

- **Removing orphaned worktrees automatically**, from reconciliation or from `cleanup`.
  Cleanup's guards include branch containment against a base ref, decided per work item's
  repository; a directory with no row has no work item and the maintainer has not opted in.
  The anomaly and the path form make removal one command, which is the right amount of
  friction for deleting a directory nobody can vouch for.
- **Directories under the worktree root that are not the shape robot-army creates**, or that
  sit under a folder for a repository that is not configured. The root may be shared with the
  maintainer's own checkouts, and reporting those would train the habit of acknowledging
  `orphan_worktree` without reading it.
- **#21's half** — anomalies left pointing at rows `purge-simulated` deleted. Same shape,
  separate issue, separate change.
- **Reporting the orphans' disk usage in the pass summary.** Sizing directories on every
  60-second pass costs I/O for a number `worktree list` already shows on request.

### Key Entities

- **Orphaned worktree**: a directory robot-army would have created — `issue-<n>` under a
  configured repository's folder in the worktree root — that no work item claims. Derived on
  each pass from the disk and the work items; not stored anywhere but in the anomaly that
  reports it.
- **`orphan_worktree` anomaly**: names the path (its entity), whether a clone lists it and on
  which branch, and the removal command. Retracts itself.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a purge in which the maintainer accepts worktree removal, zero directories
  from the purged rows remain on disk other than those git refused, and each of those is named
  in the purge's output.
- **SC-002**: Every command `purge-simulated` or an `orphan_worktree` anomaly tells the
  maintainer to run can actually be run and succeeds on a clean worktree — none depends on a
  row that no longer exists.
- **SC-003**: On the machine in the report, the first reconciliation pass after the upgrade
  reports each of the three existing orphans, and `worktree list` shows them.
- **SC-004**: Each orphan can be removed, worktree and branch, with one robot-army command and
  no git command run by hand.
- **SC-005**: No directory the maintainer created by hand under the worktree root, outside the
  shape robot-army uses, is ever reported or offered for removal.

## Assumptions

- The only directory names robot-army creates under a repository's folder are `issue-<n>`
  (`prompt.worktree_dir`); that shape is what makes a directory "ours" without a row saying so.
- "Claimed" means a work item's recorded worktree path equals the directory's path. Paths are
  compared after resolving them, so a `~` or a trailing slash does not make a claimed directory
  look orphaned.
- The anomaly is raised as not-rehearsed: with no row there is nothing to say whether it was,
  and the established rule (issue #21, migration 014) is that a visible false positive is the
  recoverable mistake.
- A live worker inside an unclaimed directory is detected the way reconciliation already
  detects workers — through the session registry and `/proc` — not by a new mechanism.
