# Feature Specification: Reclaim capacity slots held by sessions that are no longer running

**Feature Branch**: `issues/28`

**Created**: 2026-08-30

**Status**: Draft

**Input**: User description: "issue #28 on this repo"

Source: [#28 — Cancelling a simulated session leaks its capacity slot permanently; reconcile never reclaims it](https://github.com/jantman/robot-army/issues/28)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cancelling an item gives its slot back (Priority: P1)

The maintainer is rehearsing a repository's setup at `effect_level = "local"` — the documented way
to get `post_create` right without spending subscription quota. Partway through they decide the
run is not worth finishing and cancel it. Cancel reports success and the item becomes
`interrupted`.

Today the session record for that item stays open forever. It keeps counting against both the
global session cap and the repository's cap, so with the shipped default of one session per
repository the next item for that repository never dispatches. The queue reports `repo_cap`, which
reads exactly like the cap working correctly, and nothing anywhere reports an error.

After this feature, stopping the session releases everything the session was holding.

**Why this priority**: This is the reported defect and the one that blocked a verification round.
It has no proportionate workaround: the only command that clears the record removes every
simulated work item and every tracked intake card along with it.

**Independent Test**: Dispatch two simulated items for one repository at the default cap of one,
cancel the first, and confirm the second dispatches. Fully testable on its own and it restores the
rehearsal workflow by itself.

**Acceptance Scenarios**:

1. **Given** a simulated item with an open session, **When** the maintainer cancels it, **Then**
   by the time the command returns the session record is closed with an end time and no longer
   counts toward the global cap or the repository's cap — with or without a running daemon.
2. **Given** that repository was at its cap only because of the cancelled item, **When** capacity
   is next consulted, **Then** the repository is reported below its cap and the next queued item
   for it becomes dispatchable.
3. **Given** a session was cancelled, **When** the maintainer inspects that work item, **Then**
   the session is shown as ended, with a recorded reason naming cancellation as the cause.
4. **Given** a cancelled item, **When** the maintainer resumes or restarts it, **Then** it
   dispatches normally and the prior session's context is still available to resume from.

---

### User Story 2 - Abandoning an item holds nothing (Priority: P2)

The maintainer abandons an item rather than cancelling it. The item reaches a terminal state and
is finished as far as the system is concerned, but its session record is still open and still
holding a slot.

**Why this priority**: The same leak by a second route, confirmed on the issue. It is separated
from P1 because a maintainer who only ever cancels is already unblocked by P1, and because
abandonment can be reached from several item states, not only from a cancelled one.

**Independent Test**: Abandon an item whose session record is open and confirm the slot is
released. Testable without touching the cancel path.

**Acceptance Scenarios**:

1. **Given** an item with an open session record, **When** the maintainer abandons it, **Then**
   by the time the command returns the session record is closed and its slot released.
2. **Given** an abandoned item, **When** capacity is next consulted, **Then** neither the global
   count nor the repository count includes it.

---

### User Story 3 - Slots already leaked are reclaimed without discarding work (Priority: P3)

A database that has been through the current behaviour already contains open session records under
items that finished long ago. The maintainer should get those slots back without deleting anything
they care about.

**Why this priority**: It is recovery for existing state rather than new behaviour, and P1 and P2
are worth shipping even if old records need one extra step. It matters because today the only way
to clear such a record is to discard every simulated work item and every tracked intake card, and
because the current workaround — raising the per-repository cap — leaves the machine
oversubscribed.

**Independent Test**: Start from a database holding an open session record under a finished item,
run reconciliation, and confirm the record is closed and every work item and intake card survives.

**Acceptance Scenarios**:

1. **Given** a database containing an open session record under an item that is not running,
   **When** reconciliation runs, **Then** that record is closed and the slot released.
2. **Given** the same database, **When** reconciliation completes, **Then** no work item, session
   history, or intake card has been deleted.
3. **Given** the same database, **When** reconciliation runs a second time, **Then** it closes
   nothing further and writes no repeated records for the same session.
4. **Given** reconciliation closed one or more such records, **When** its summary is read, **Then**
   the count of records it closed is visible rather than being reported as no work done.

---

### Edge Cases

- **A worker process is genuinely still alive under a finished item.** `interrupted` has never
  meant "nothing is running": a worker whose wrapper died keeps running, reparented. Closing that
  session's record would make the reported capacity lower than the number of live workers, which is
  the one direction of capacity error that causes real harm — it oversubscribes the maintainer's
  own quota while claiming to protect it. The record must stay open and the condition must be
  reported as an orphan, not silently reconciled away.
- **A simulated session has no process at all.** There is nothing to check for liveness and no exit
  report will ever arrive, so this is the unambiguous case and must always be reclaimed.
- **An item that is legitimately mid-flight.** An item being prepared for dispatch, or one actively
  running, is *supposed* to hold an open session record. Neither may be swept.
- **Cancel with no daemon running.** The rehearsal workflow is CLI-only, so nothing sweeps on a
  timer and the command itself has to be sufficient.
- **Cancel racing the ordinary exit path.** A real session's exit report may arrive at almost the
  same moment the record is closed for cancellation. The two must not fight, produce a contradictory
  history, or fail.
- **An item cancelled and then abandoned.** The record must be closed once, not closed twice or
  reported as an error the second time.
- **An item whose record was closed, then resumed.** Resuming restores context from the previous
  session; closing the record must not take that away.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST hold the invariant that no session record remains open while its work
  item is in a state that cannot have a session in flight. The only states that may carry an open
  session record are the two that describe a session being prepared or actively running.
- **FR-002**: When a session is stopped by cancellation, the system MUST close that session's
  record, stamping it with an end time and a reason identifying cancellation, and MUST NOT wait for
  an exit report that a stopped or simulated session will never produce.
- **FR-003**: When a work item is abandoned, the system MUST close any session record still open
  under it.
- **FR-004**: The system MUST reclaim slots held by session records that were left open before this
  feature existed, without requiring the maintainer to delete any work item, session history, or
  intake card.
- **FR-005**: The system MUST NOT close a session record whose worker process can be observed to be
  alive. Such a case MUST instead be reported through the existing orphan-detection path so it stays
  visible to the maintainer.
- **FR-006**: Capacity reporting MUST stop counting a closed session record immediately — the global
  count, the per-repository count, and the reason a queued item is being held MUST all reflect its
  release the next time they are consulted.
- **FR-007**: Every session record closed by this feature MUST be recorded in the audit log,
  naming the session, its work item, and why the record was closed.
- **FR-008**: Reclaiming MUST be idempotent: repeated passes over the same state MUST close nothing
  further and MUST NOT write repeated records for a session already closed.
- **FR-009**: Reconciliation's pass summary MUST report how many session records it closed, so that
  a pass which did work does not read as a pass which examined nothing.
- **FR-010**: A closed session record MUST remain visible in the work item's session history, and
  MUST remain usable as the context a resume restores from.
- **FR-011**: Reclaiming a slot MUST NOT alter the work item's own state, its worktree, its branch,
  or its intake card.
- **FR-012**: Cancellation and abandonment MUST release the slot before the command returns, so
  that the release is complete whether or not the daemon is running. Reconciliation MUST
  independently assert FR-001 as a backstop, so that a record left open by any other route — or
  already left open in an existing database — is still reclaimed.

### Key Entities

- **Work item**: One unit of work tracked from discovery to completion. Has a lifecycle state; only
  two of those states describe work with a session in flight.
- **Session**: One attempt at a work item. Has its own state, an optional process identity, a start
  time and an end time, and a flag marking it simulated. An open session — not yet ended — is what
  occupies a capacity slot.
- **Capacity snapshot**: A live observation of how many worker sessions are running in total and
  per repository, compared against the configured caps. Never stored, and required never to
  under-count.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the shipped default of one session per repository, cancelling a simulated item
  and then dispatching the next item for that repository succeeds, where today it is held forever.
- **SC-002**: After a reconciliation pass over any database, zero session records remain open under
  work items that are not preparing or running.
- **SC-003**: Recovering a leaked slot costs the maintainer zero work items and zero intake cards,
  down from all of them.
- **SC-004**: Recovering a leaked slot requires no configuration change; the reported workaround of
  raising the per-repository cap is no longer needed.
- **SC-005**: The reported number of running sessions is never lower than the number of worker
  processes actually alive, in every scenario this feature touches.
- **SC-006**: Every slot reclaimed can be accounted for from the log alone — which session, under
  which work item, at what time, and why — without re-running anything.
- **SC-007**: Scenario 2 of the 001 quickstart can be rehearsed end to end at `local`, including a
  cancellation partway through, without the queue silently stalling.

## Assumptions

- "Open session record" means a session that has been created and not yet ended — the states
  covering a session being prepared and a session running. "Closed" means ended, with an end time.
- The two work item states that may legitimately carry an open session record are the one covering
  dispatch preparation and the one covering an actively running session. Every other state — rested,
  failed, awaiting review, interrupted, done, abandoned — implies no session is in flight.
- A reclaimed record is marked as lost rather than as a clean or failed exit, because no exit status
  was ever observed. It stays in the item's session history rather than being deleted.
- Cancellation of a real session continues to rely on the ordinary exit report when one arrives;
  this feature closes the record when that report cannot or does not arrive, and the two paths agree
  rather than conflict.
- Liveness is judged by the mechanism the system already uses to decide whether a session is alive.
  No new detection method is introduced.
- Reconciliation continues to run on its existing schedule — every 60 seconds by default in the
  daemon — and on demand from the CLI. No new schedule and no new command are introduced.
- Making the escape hatch proportionate — purging a single simulated item rather than all of them —
  is out of scope. This feature removes the need to reach for it; the command's behaviour is
  unchanged.
- Changing the caps themselves, their defaults, or how capacity is counted is out of scope. Only
  which records are counted changes.
