# Feature Specification: Refuse to Remove a Worktree While Its Session Is Open

**Feature Branch**: `20260901-164616-guard-worktree-remove`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "issue #79 on this repo" — *worktree remove deletes the worktree of a running session, leaving the worker in a deleted directory*

## Context

Manual worktree removal removes both the worktree directory and its branch. Its only guards are
that the item exists, that it has a worktree on record, that the repository still resolves, and
git's own refusal on a dirty or untracked tree. **Nothing asks whether a worker is still running
in that directory.**

Measured on 2026-08-31: removing the worktree of an item whose session was still running
succeeded silently. The worker process remained alive with its working directory reported as
`(deleted)`, and the branch was deleted in the same command, so there was nothing left to recover
its work from.

This needs no misuse to reach. It is the sequence the ordinary lifecycle produces:

1. An issue is dispatched and a session starts.
2. The issue is closed — by a person or by the session itself — and the work item goes `done`.
   **The session keeps running.** That is deliberate, and the `orphan_session` anomaly text says
   so in as many words.
3. The item is now terminal, so it reads as finished, and manual removal is the documented way to
   reclaim its disk. `purge-simulated` explicitly tells the operator to run it, and abandonment
   does too.
4. The worktree is removed out from under the live worker.

The automatic reclaim path already has this guard: it treats an item with a live session as
`skipped` — deliberately distinct from `retained`, so it is reconsidered on a later pass rather
than decided against. That guard is absent from the manual path, which is the more dangerous way
round. The automatic path is conservative by design and runs unattended; manual removal is what a
person reaches for when they want the space back *now*, and it is the one that overrides git.

The two consequences are unrecoverable, not merely untidy: writes from the live worker land in an
unlinked directory, and anything resolving a relative path or re-reading a file it wrote earlier
misbehaves strangely rather than failing cleanly.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Removal refuses while a session for the item is open (Priority: P1)

The operator asks to remove the worktree of an item that still has an open session — whether the
item itself is active, or terminal because its issue closed while the worker carried on. The
command refuses before anything is removed, says which session is open, and tells the operator how
to go and look at it. The worktree, the branch, and the recorded worktree path are all left exactly
as they were.

**Why this priority**: This is the entire report. Without it, one documented, routine command
destroys in-progress work and the branch that held it, with no warning and no recovery. Everything
else in this feature is refinement of the refusal.

**Independent Test**: Record an open session for a work item — including the terminal-item case
that the lifecycle actually produces — and attempt removal with the removal boundary instrumented.
The story passes when no removal and no branch deletion is attempted, the command exits non-zero,
and the item's recorded worktree path is unchanged.

**Acceptance Scenarios**:

1. **Given** a work item in a terminal state whose session row is still `running`, **When** the
   operator removes its worktree, **Then** the command refuses, removes nothing, deletes no branch,
   and exits non-zero.
2. **Given** a work item with a session in the `starting` state, **When** the operator removes its
   worktree, **Then** the command refuses on the same grounds — a session that has not reported
   itself running yet is not a session that is safely absent.
3. **Given** a work item with more than one session attempt where an *earlier* attempt's row is
   still open, **When** the operator removes its worktree, **Then** the command refuses; the guard
   asks whether *any* session for the item is open, not only the most recent attempt.
4. **Given** a refusal, **When** the operator reads the message, **Then** it names the session, the
   recorded process id, whether that process can still be seen, and — when a reattach socket is
   recorded — the same reattach line the item's detail view prints, so the operator can look at the
   worker before deciding anything.
5. **Given** a refusal, **When** the operator inspects the work item afterwards, **Then** its
   recorded worktree path, branch, state and cleanup record are all unchanged.
6. **Given** a work item whose sessions are all closed, **When** the operator removes its worktree,
   **Then** removal proceeds exactly as it does today, under git's existing refusal for a dirty or
   untracked tree.

---

### User Story 2 - The override is available, and it is honest (Priority: P2)

The operator has looked, has decided the worker is not doing anything worth keeping, and forces the
removal anyway. The forced path still demands the typed confirmation it demands today, and that
prompt says plainly that a live worker is running in the directory before the operator types
anything.

**Why this priority**: Refusing without an escape hatch turns a rescue into a dead end — a leaked
session row that nothing will ever close would make a worktree permanently unremovable. The
override must exist. It is P2 rather than P1 because the harm is already removed by Story 1, and
because an override the operator has to reach for deliberately is not the failure mode reported.

**Independent Test**: Force removal of an item with an open session and capture the confirmation
prompt. The story passes when the prompt names the live session before accepting input, when a
non-matching answer aborts with nothing removed, and when the matching answer proceeds.

**Acceptance Scenarios**:

1. **Given** an item with an open session, **When** the operator forces removal, **Then** the
   confirmation prompt states that a session is still open on that worktree and names it, in
   addition to what it already says about discarding uncommitted work.
2. **Given** that prompt, **When** the operator answers with anything other than the required
   confirmation, **Then** the command aborts and nothing is removed.
3. **Given** that prompt, **When** the operator answers correctly, **Then** removal proceeds and
   the worktree and branch are removed as they are today.
4. **Given** an item with an open session, **When** removal is attempted *without* the override,
   **Then** no confirmation prompt is shown at all — the refusal is not a question.
5. **Given** an item with **no** open session, **When** the operator forces removal, **Then** the
   confirmation prompt is worded as it is today and makes no claim about a live session.

---

### User Story 3 - The refusal and the override are both on the record (Priority: P3)

Every refusal, and every forced removal that overrode one, is written to the durable action record
with the session it concerned. A reader of the record alone can tell a refusal from a git refusal,
from an ordinary removal, and from a removal that knowingly went ahead over a live worker.

**Why this priority**: Required by the accountability principle, and practically necessary — a
forced removal over a live session is the single most destructive thing this command can do, and
if it later turns out to have destroyed something, the record is the only place that says it
happened and who was warned. P3 because the harm is already prevented by P1 and P2.

**Independent Test**: Trigger a refusal and a forced override, then read the action record alone.
The story passes when the record identifies which item and session each concerned and which of the
two outcomes occurred, without re-running anything.

**Acceptance Scenarios**:

1. **Given** a refusal, **When** the action record is read, **Then** it contains a record naming
   the work item, the session that caused the refusal, the reason, and the fact that nothing was
   removed.
2. **Given** a forced removal over an open session, **When** the action record is read, **Then** it
   records that the live-session guard was overridden and which session it concerned, distinct from
   an ordinary forced removal of a dirty tree.
3. **Given** either outcome, **When** the machine-readable output is read, **Then** it carries the
   refusal reason in a field distinguishable from git's own refusal reason, so the two are not
   conflated by anything reading the output.

---

### Edge Cases

- **The item is terminal.** This is the reachable case, not an exotic one. The guard must be
  independent of work item state: `done` and `abandoned` items are exactly the ones an operator
  reaches for, and they can carry live sessions by design.
- **The item is active with a running session.** Nothing guards this today either. The same refusal
  covers it; there is no reason for the two to behave differently.
- **An open session row whose process cannot be found.** A leaked row — a simulated session with no
  wrapper to close it, or a killed worker — still refuses, because the recorded row is what the
  capacity accounting believes and guessing from process absence is how a live worker gets missed.
  The message must say the process could not be seen, and the operator resolves it by letting the
  reclaim sweep close the row, by cancelling the item, or by overriding.
- **A session row with no recorded process id.** The row is still open; it still refuses. The
  message says the process id is unrecorded rather than presenting an absent pid as evidence of
  anything.
- **Removal of an item that has no worktree on record, or whose repository no longer resolves.**
  These existing refusals are unaffected and must keep their current wording and exit behaviour.
- **A dirty worktree that also has an open session.** The session guard is reached first and is
  reported on its own terms; the operator is not told about untracked files when the real answer is
  that a worker is still typing in there.
- **The automatic reclaim path.** Its guard already exists and already records `skipped`. This
  feature must not alter it, and must not make an item that the sweep would reconsider look decided.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Manual worktree removal MUST refuse when any session row for the work item is in an
  open state — the same set of states the automatic reclaim path treats as live, currently
  `starting` and `running`.
- **FR-002**: The guard MUST consider every session row recorded for the item, not only the latest
  attempt, so that an earlier attempt whose row was never closed is not silently ignored.
- **FR-003**: The guard MUST NOT consult the work item's state. An item in a terminal state with an
  open session MUST refuse exactly as an active one does.
- **FR-004**: A refusal MUST occur before any removal, any branch deletion, and any confirmation
  prompt, and MUST leave the worktree, the branch, the recorded worktree path, the work item state
  and the cleanup record untouched.
- **FR-005**: A refusal MUST exit non-zero and MUST name the session, the recorded process id (or
  say that none is recorded), whether that process can currently be seen, and the reattach line for
  the recorded socket when one exists — the same line the item's detail view already prints.
- **FR-006**: A refusal MUST be reported as its own outcome, distinguishable in both the terminal
  output and the machine-readable output from git's refusal on a dirty or untracked tree.
- **FR-007**: The override flag MUST still be able to remove a worktree with an open session, so
  that a row nothing will ever close cannot make a worktree permanently unremovable.
- **FR-008**: An override of the live-session guard MUST require the typed confirmation the
  override already demands; it MUST NOT introduce a second prompt, and MUST NOT be satisfied by the
  flag alone.
- **FR-009**: The confirmation prompt for an override MUST state that a session is still open on
  that worktree and name it, before any input is accepted. When no session is open, the prompt MUST
  be unchanged from today.
- **FR-010**: Every refusal MUST be written to the durable action record when it occurs, carrying
  the work item, the session that caused it, the reason, and the fact that nothing was removed.
- **FR-011**: A forced removal that overrode an open session MUST be recorded as such, distinct
  from a forced removal that only overrode git's dirty-tree refusal.
- **FR-012**: Removal of an item whose sessions are all closed MUST behave exactly as it does
  today — same guards, same git refusal, same branch deletion, same output.
- **FR-013**: The automatic reclaim path's existing live-session guard MUST be left as it is,
  including its `skipped` outcome and its reconsideration on later passes.
- **FR-014**: The definition of "a live session" MUST be shared between the manual and automatic
  paths rather than restated, so the two cannot drift into disagreeing about what is running.

### Key Entities

- **Work item**: the unit an operator names when removing a worktree. The fields that matter here
  are its recorded worktree path, its branch, and its state — the last of which the guard
  deliberately ignores.
- **Session record**: the orchestrator's memory of one launched session for an item. The fields
  that matter here are its state, its attempt number, its recorded process id and start time, and
  its recorded reattach socket.
- **Removal outcome**: what removal did or refused to do. Carries whether the worktree was removed,
  whether the branch was deleted, and — newly — whether removal refused because a session was open,
  as distinct from git's own refusal.
- **Action record**: the durable, append-only log from which any past action must be
  reconstructible without re-running it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every work item with an open session row, the number of worktrees removed and
  branches deleted by manual removal without the override is **zero**, in 100% of attempts, and
  regardless of the item's state.
- **SC-002**: No worker process can be left running with a deleted working directory by an
  unforced manual removal.
- **SC-003**: An operator who hits the refusal can, from its message alone, reach the running
  worker and look at it — without consulting the database, the process table, or any other command.
- **SC-004**: For 100% of refusals and of forced overrides, a reader of the action record alone can
  state which item and which session were involved and which of the two outcomes occurred.
- **SC-005**: No removal that succeeds today becomes a refusal: items whose sessions are all closed
  are removed at the same rate, by the same route, with the same output.
- **SC-006**: A worktree cannot become permanently unremovable by this guard — the override clears
  it in a single command in 100% of cases.

## Assumptions

- **The session row, not the process table, is what refuses.** An open row with a process that
  cannot be seen still blocks removal. The alternative — refusing only when a live process is
  confirmed — would remove the worktree whenever liveness could not be established, which is the
  failure the report is about, only harder to reproduce. Liveness is reported to the operator as
  information, never used as the gate.
- The reclaim sweep is the intended route for closing a session row with no live worker behind it,
  including a simulated one. This feature does not add a second way to close rows; it points the
  operator at the existing ones and offers the override as the last resort.
- The override's existing typed confirmation is adequate protection for the forced path. A second
  prompt for the live-session case would be answered as reflexively as the first and adds nothing.
- Surfacing the open-session fact in the worktree listing, so an operator never reaches for a
  guarded target in the first place, is **out of scope**. The refusal is what prevents the harm;
  advertising it ahead of time is a convenience with no second use in hand.
- Anything that stops a running session — cancellation, termination, the reclaim sweep — is out of
  scope. This feature refuses; it never stops a worker on the operator's behalf, because "reclaim
  some disk" is not consent to end a running job.
- The wording of the automatic reclaim path's `skipped` reason is assumed to be a suitable basis for
  the manual refusal's reason text, so the two paths describe the same condition the same way.
