# Feature Specification: A Simulated Removal Says It Was Simulated

**Feature Branch**: `robot-army/issue-70-worktree-remove-reports-removed`

**Created**: 2026-09-13

**Status**: Draft

**Input**: GitHub issue #70: "worktree remove reports 'removed worktree' and 'deleted branch' at
plan, where it removed and deleted nothing".

## Background

Version control is simulated below the `local` effect level: at `plan`, every git operation is
recorded and nothing is done. That is correct, and the audit log says so — the `git.remove_worktree`
and `git.delete_branch` records carry `[simulated]`. The operator-facing output does not:

```
$ robot-army worktree remove 29
removed worktree /home/jantman/worktrees/robot-army/issue-66
deleted branch robot-army/issue-66-verification-round-014-termination
```

Neither happened. `worktree remove` exists to reclaim disk, and someone who runs it at `plan`, reads
"removed worktree", and moves on has reclaimed nothing and been told otherwise.

Since the issue was filed, issue #59 changed one half of this: a removal the boundary reports while
the directory is **still on disk** is now refused (`directory_survived`), which is honest. What is
left is the other half — a simulated item whose worktree was itself only simulated, so there is no
directory to survive — and the verbs beside it:

- `worktree remove <id>` still prints "removed worktree" / "deleted branch" there.
- `worktree prune` prints "nothing to prune" for every repository, an assertion about git's records
  that nothing checked.
- `cleanup` prints and *records* "worktree removed; branch removed", and counts the item as having
  had its worktree removed. Worse, it has no `directory_survived` guard at all: a real directory left
  by a `local` round, cleaned at `plan`, is recorded `done` — a decision the automatic pass never
  revisits — while it is still on disk.

`cancel` already words its simulated case honestly ("stopped session … via a simulated stop"), and
`status` marks simulated rows. This brings the removal verbs into line.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - `worktree remove` says "would" when nothing was removed (Priority: P1)

As the operator, when I run `robot-army worktree remove <id>` at an effect level where version
control is simulated, the output tells me the worktree and branch *would* have been removed, and
why they were not — the effect level, and the level at which version control becomes real.

**Why this priority**: This is the reported defect, on the command whose whole purpose is reclaiming
disk.

**Independent Test**: At `plan`, remove a simulated item's worktree and read the output.

**Acceptance Scenarios**:

1. **Given** effect level `plan` and a simulated item with a recorded worktree and branch, **When** I
   run `worktree remove <id>`, **Then** the output says it would remove that worktree and would delete
   that branch, names the simulation (level `plan`; version control real from `local`), and contains
   no line saying a worktree was removed or a branch deleted.
2. **Given** the same, **When** I ask for machine-readable output, or read the `worktree.remove`
   outcome record, **Then** it says the removal was simulated.
3. **Given** effect level `local` or above, **When** I remove a worktree, **Then** the output is exactly
   what it is today: "removed worktree …", "deleted branch …".

---

### User Story 2 - `cleanup` neither says nor records a removal that did not happen (Priority: P1)

As the operator, `robot-army cleanup` at a simulated level reports each decision as simulated, its
summary does not claim any worktree was removed, and a real directory still on disk is never recorded
as cleaned up.

**Why this priority**: `cleanup` shares the defect in its output, and adds a persistent one: a
recorded `done` hides a real directory from every later pass.

**Independent Test**: At `plan`, run `cleanup` over a finished simulated item with no directory, and
over a finished item whose directory is really on disk.

**Acceptance Scenarios**:

1. **Given** effect level `plan` and a finished simulated item whose worktree does not exist on disk,
   **When** cleanup considers it, **Then** its line and its recorded reason say the removal was
   simulated, and the summary says how many items *would* have had their worktree removed rather than
   how many did.
2. **Given** effect level `plan` and a finished item whose worktree is a real directory, **When**
   cleanup considers it, **Then** the item is recorded `retained` with a reason saying the removal was
   simulated and the directory is still there, the branch is not touched, and `cleanup <id>` at a
   real level reconsiders it.
3. **Given** a real level, **When** cleanup runs, **Then** its decisions, reasons and summary are
   unchanged.

---

### User Story 3 - `worktree prune` does not claim to have checked (Priority: P2)

As the operator, `worktree prune` at a simulated level says per repository that pruning was simulated,
rather than "nothing to prune".

**Why this priority**: Less harmful than a false removal — nothing is claimed destroyed — but it
asserts that git's records are clean when nothing looked.

**Independent Test**: At `plan`, run `worktree prune` with one onboarded repository.

**Acceptance Scenarios**:

1. **Given** effect level `plan`, **When** I run `worktree prune`, **Then** each repository's line
   says pruning was simulated and nothing was checked or pruned, and no line says "nothing to prune".

---

### User Story 4 - The destructive verbs agree (Priority: P2)

As the maintainer, one test holds `cancel`, `worktree remove`, `worktree prune` and `cleanup` to the
same rule, so the next verb added — or the next rewording — cannot quietly report a simulated
destruction as a real one.

**Independent Test**: Run each verb against a simulated version-control and session boundary and
check its output against the rule.

**Acceptance Scenarios**:

1. **Given** simulated boundaries, **When** each of the four verbs reports an outcome, **Then** its
   output names the simulation and contains none of the completed-outcome phrasings the real path
   uses.

### Edge Cases

- **The directory is really on disk at `plan`** (`worktree remove <id>`, `<path>`,
  `purge-simulated --remove-worktrees`). Already refused as `directory_survived` since #59, with a
  message saying version control is simulated. Unchanged.
- **Version control simulated, no branch on record.** Only the worktree line is printed, in the
  "would" form.
- **Machine-readable output with a real boundary.** Carries `simulated: false`, so a consumer need not
  infer realness from the key being absent.
- **A simulated removal of a simulated item clears the row's recorded worktree path**, as it does
  today: within the simulation, that worktree is gone, and the audit trail marks every step simulated.
- **Interrupted part-way.** Nothing new is persisted beyond an extra field on existing records and a
  different reason string; the existing interruption behaviour of each verb is unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When version control is simulated, `worktree remove` MUST NOT print that a worktree was
  removed or a branch deleted. It MUST print that it *would* remove the worktree and *would* delete
  the branch, and one line naming the simulation: the current effect level and the lowest level at
  which version control is real.
- **FR-002**: The "lowest level at which version control is real" MUST be derived from the table that
  decides which boundaries are real at which level, not written into the message.
- **FR-003**: Whether an operation was simulated MUST be taken from the version-control boundary that
  performed it — the same component that marks its audit records simulated — not inferred separately.
- **FR-004**: The `worktree.remove` outcome record and `worktree remove`'s machine-readable output
  MUST carry `simulated` (true or false).
- **FR-005**: When version control is simulated, `worktree prune` MUST NOT print "nothing to prune";
  it MUST say, per repository, that pruning was simulated and nothing was checked or pruned.
- **FR-006**: When version control is simulated, every cleanup decision that reports a removal or
  deletion MUST say in its reason that it was simulated; the reason is what is printed and what is
  recorded on the item.
- **FR-007**: When version control is simulated, `cleanup`'s summary MUST count items that *would*
  have had their worktree removed, not items that had it removed. Its machine-readable decisions MUST
  carry `simulated`.
- **FR-008**: When a removal is reported but the worktree directory is still on disk, cleanup MUST
  record the item `retained` with a reason saying so, and MUST NOT attempt the branch — the same rule
  `worktree remove` follows since #59.
- **FR-009**: When version control is real, the output, records and decisions of every verb above
  MUST be unchanged apart from the added `simulated: false` fields.
- **FR-010**: `cancel`'s wording is unchanged; it is the model the others follow.
- **FR-011**: A test MUST assert, for `cancel`, `worktree remove`, `worktree prune` and `cleanup`
  under simulated boundaries, that the output names the simulation and contains none of the real
  path's completed-outcome phrasings.
- **FR-012**: The outcome guide page MUST describe the simulated wording and the cleanup guard, and
  the audit-log guide MUST document the new `simulated` field on `worktree.remove`.

### Key Entities

- **Version-control boundary**: the component that performs git operations. It is either real or
  simulated for the whole process, and now says which.
- **Cleanup decision**: per item — state, reason, whether the worktree and branch went, and now
  whether that was simulated.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Replaying the issue's command at `plan` over a simulated item prints no line beginning
  "removed worktree" or "deleted branch", and names `plan` and `local`.
- **SC-002**: At `plan`, none of `worktree remove`, `worktree prune` or `cleanup` prints a
  completed-removal phrasing; all three name the simulation.
- **SC-003**: At `plan`, a real directory on disk is never recorded with cleanup state `done`.
- **SC-004**: At a real level, every existing test of these verbs' output passes unchanged.
- **SC-005**: The full test suite passes.

## Assumptions

- The wording follows the issue's suggestion and `cancel`'s precedent ("would remove worktree …",
  then a line naming the simulation); exact phrasing is a plan decision.
- The exit status of a simulated removal stays success: the command did everything the level allows,
  and the audit log records it. Making it non-zero would break scripts at `plan` for no gain.
- `purge-simulated` and `worktree remove <path>` only ever remove directories that are on disk, which
  at a simulated level are refused as `directory_survived`; they inherit FR-001 through the shared
  removal step but need no change of their own.
- A `retained` decision for a surviving directory (FR-008) is reconsidered by `cleanup <id>`, not by
  the automatic pass, as with every other `retained`. Retrying it automatically at `plan` would
  simulate the same removal every pass and change nothing.
