# Feature Specification: Retire a finished item's session

**Feature Branch**: `speckit/20260905-121903-retire-finished-sessions`

**Created**: 2026-09-05

**Status**: Draft

**Input**: User description: "issue #138 on this repo; I've paused dispatch but otherwise left
everything as it was when the issue was created. Since then, issue #116 has been worked and now
shows the same problem."

Source: [#138 — Session cleanup](https://github.com/jantman/robot-army/issues/138)

## What was actually happening

The issue asks three questions — what is going on, what of it is intentional, and how to make the
normal path stop producing anomalies. The answers below are the premise this specification is
built on; they were established by reading the live state of the machine while it was still in the
condition #138 describes.

**A worker never ends itself.** It does the work, opens the pull request, and then sits at a
prompt waiting for someone to type. Nothing tells it to stop, and the exit record that closes a
session record is written only when the process ends. So the session record stays open for as long
as the process lives, which is indefinitely.

**Everything downstream of that is working exactly as designed.** Merging the pull request closes
the issue; the next reconciliation pass sees a closed issue and moves the item to `done`; a live
worker under a `done` item is precisely the condition `orphan_session` was built to report, and the
contract deliberately leaves the session record open so the capacity count never understates the
number of live workers. Each of those decisions is correct on its own. Together they mean **the
ordinary successful path terminates in an anomaly and a permanently held capacity slot.**

Three consequences, all of them measured on the machine rather than reasoned about:

| Observed | Detail |
|---|---|
| Two anomalies for two successful items | Items 45 (issue #116) and 54 (issue #136), both `done`, both with a live worker in their worktree |
| The machine is wedged at 3 of 3 sessions | Two of the three are those finished items. Dispatch would be blocked by capacity even with the pause lifted |
| Neither worktree can ever be reclaimed | 50 MB each. Cleanup's session guard sees a live record and records `skipped` — which means "not yet", and is reconsidered every pass, forever |

A fourth, smaller thing was found alongside: anomaly `[24]` names pid 498936, which no longer
exists. Anomalies are never cleared by the system, only acknowledged by hand, so a condition that
resolves itself leaves its report behind permanently.

The bug, then, is not in any of the parts. It is that **the lifecycle has no ending** — nothing in
the system is responsible for the moment when a session's work has been accepted and the session
itself should stop.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merging the pull request finishes the whole thing (Priority: P1)

The maintainer reviews a robot-army pull request on github.com, merges it, and deletes the branch.
The issue closes. From that point on the maintainer does nothing else and expects nothing else to
be required of them.

Today the item goes `done` and then three things are left behind: a worker still sitting at a
prompt, a terminal window that has to be closed by hand, and an anomaly that has to be
acknowledged by hand — and the capacity slot is held until both of those happen. After this
feature, the item going `done` because its issue closed is the point at which the session that did
the work is ended: the process stops, the record closes with an end time and a reason, the
terminal window goes away with it, the slot comes back, and no anomaly is raised for any of it.

**Why this priority**: It is the reported defect, it is on the path every successful item takes,
and its cost compounds — three successful items at the shipped cap of three are enough to stop the
daemon dispatching anything ever again, reporting only that the machine is full.

**Independent Test**: Take an item to `done` by closing its issue while its worker is alive and
quiet, run a reconciliation pass, and confirm the process is gone, the record is closed, the slot
is free, and `robot-army anomalies` is empty. Delivers the whole of #138's ask on its own.

**Acceptance Scenarios**:

1. **Given** an item in `done` whose issue is confirmed closed and whose worker has been quiet for
   longer than the quiet period, **When** a reconciliation pass runs, **Then** the worker is ended,
   its session record is closed with an end time and a reason naming retirement, and the slot it
   held is released from both the global and the per-repository count.
2. **Given** the same item, **When** the pass runs, **Then** no `orphan_session` anomaly is raised
   for that session, on that pass or any later one.
3. **Given** the same item, **When** the pass runs, **Then** the terminal window hosting that
   worker is gone, with no separate action required to close it.
4. **Given** the same item and `cleanup.on_issue_close` turned on, **When** the pass that retires
   the session is followed by the next cleanup consideration, **Then** the worktree is no longer
   `skipped` for a live session and is reclaimed under the existing two guards, unchanged.
5. **Given** an item in `done` whose worker has written to its transcript within the quiet period —
   the maintainer is attached and typing in it — **When** a reconciliation pass runs, **Then**
   nothing is ended, nothing is recorded as a decision, and the question is asked again next pass.
6. **Given** an item in `abandoned` or `failed` with a live worker, **When** a reconciliation pass
   runs, **Then** the worker is left alone and the existing `orphan_session` report for it stands.
7. **Given** an item in `done` reached by any route other than its issue being observed closed,
   **When** a reconciliation pass runs, **Then** the worker is left alone.

---

### User Story 2 - Ending a session by hand, whatever state its item is in (Priority: P2)

The automatic path deliberately covers only the one case where the work is provably accepted.
`abandoned` and `failed` items keep their workers, because those are the states where the work is
*not* finished and the transcript may be the reason the maintainer is about to attach. So the
maintainer needs a reliable way to end one of those on demand.

The command for stopping one item's session exists. It does not work here: it decides what to
record by asking whether the item is still `active`, and a terminal item is not, so it reports that
the session "had already recorded its own ending" and leaves the record open — for a process it
had just signalled and confirmed gone. The slot stays held, the anomaly stays raised, and the
message the maintainer reads is false.

**Why this priority**: Without it the states the automatic path deliberately excludes have no
route out at all, and the maintainer is told something untrue by a command that succeeded. It is
below P1 because a maintainer whose items all succeed is already unblocked.

**Independent Test**: Stop the session of an item in a terminal state and confirm the record closes,
the slot is released, and the message describes what actually happened. Testable without any part
of User Story 1.

**Acceptance Scenarios**:

1. **Given** an item in any state with a live session record, **When** the maintainer stops that
   item's session and the process is confirmed gone, **Then** the session record is closed with an
   end time and a reason, and the slot is released.
2. **Given** an item already in a terminal state, **When** its session is stopped, **Then** the item
   is left in the state it is in — stopping a finished item's session does not reopen or re-decide
   the item — and the message says the session was stopped, not that it had ended by itself.
3. **Given** a session whose process cannot be confirmed gone, **When** the maintainer stops it,
   **Then** the record is left open, the failure is reported, and nothing claims a slot was freed.
4. **Given** a session record whose recorded process id could not belong to a worker at all,
   **When** the maintainer stops it, **Then** nothing is signalled, the refusal says so in those
   words, and the record is handed over for inspection rather than settled.

---

### User Story 3 - An anomaly that has resolved itself stops being reported (Priority: P3)

`robot-army anomalies` is read as a list of things needing attention. An `orphan_session` naming a
process that no longer exists is not one of those, and clearing it by hand teaches the habit of
clearing the list without reading it — which is how the anomaly that mattered gets acknowledged
along with the noise.

**Why this priority**: It is hygiene on a list that User Stories 1 and 2 already stop filling. It
is scoped narrowly and deliberately: only `orphan_session`, and only the one condition that can be
positively re-established as false — the process it names is gone. Every other anomaly kind keeps
today's behaviour exactly.

**Independent Test**: Raise an `orphan_session` for a process, end that process by any means, run a
reconciliation pass, and confirm the anomaly is no longer listed and that the record says why.

**Acceptance Scenarios**:

1. **Given** an unacknowledged `orphan_session` naming a process that is no longer running,
   **When** a reconciliation pass runs, **Then** it is no longer listed as needing attention and a
   record states that the condition was re-checked and found resolved.
2. **Given** an unacknowledged `orphan_session` whose process is still running, **When** a pass
   runs, **Then** it stays listed and is not duplicated.
3. **Given** an unacknowledged anomaly of any other kind, **When** a pass runs, **Then** it is
   untouched.
4. **Given** an `orphan_session` whose recorded process id now belongs to a different, unrelated
   process, **When** a pass runs, **Then** it is treated as resolved only if the process identity —
   not merely the process id — fails to match the one recorded.

---

### Edge Cases

- **The maintainer is attached and mid-conversation when the issue closes.** The quiet period is
  the whole guard against ending a session someone is using. A worker that has produced output
  recently is not retired, and the decision is re-asked on the next pass rather than deferred once.
- **The worker ends itself between the decision and the signal.** The exit record is drained by the
  daemon in its own process while a pass is running. Retirement must settle on whatever the record
  says rather than forcing a transition, and must not report a failure for a session that ended
  cleanly a moment before it was asked to stop.
- **The process id has been reused.** A recorded process id alone is not identity. Nothing may be
  signalled unless the process it names is the same process the record describes.
- **The process survives the attempt.** The record stays open, the slot stays held, and the
  condition stays reported. "I tried and could not" is never recorded as "it is gone".
- **A simulated session.** A dry-run record has no process to end. Retirement closes the record
  without signalling anything, and never routes a simulated record through a real termination.
- **The issue is reopened after the session was retired.** Out of scope. The item's route back to
  work is the existing resume/restart path, which starts a session of its own.
- **The item is `done` but the check on whether the issue is closed cannot be made** — GitHub is
  unreachable. "I could not ask" is not "it is closed". Nothing is retired.
- **Two items' workers are retired in the same pass.** Each is decided, signalled and recorded
  independently; one failing has no effect on the other.
- **A worker whose work item cannot be resolved at all** — the existing worktree-root orphan sweep's
  population. Unchanged by this feature: nothing there is retired.
- **A late exit record.** The worker's wrapper may still manage to write one after the session
  record has been closed. Applying it would attempt a transition out of a state the machine calls
  terminal, and today that fails, is logged as an error, and leaves the file in the spool to be
  retried on every tick forever. A late record must settle quietly and leave nothing behind.

## Requirements *(mandatory)*

### Functional Requirements

**Retiring a finished session**

- **FR-001**: The system MUST end the worker of a work item that is in `done` **and** whose issue
  was observed closed, once that worker has been quiet for longer than a fixed quiet period.
- **FR-002**: The system MUST NOT retire a worker under an item in `abandoned` or `failed`, nor
  under an item that reached `done` by any route other than its issue being observed closed.
- **FR-003**: The system MUST treat "quiet" as an observation about the worker itself — that it has
  produced nothing for the duration of the quiet period — and not as an inference from the item's
  state or from elapsed time since dispatch.
- **FR-004**: A worker that is not yet quiet MUST be left exactly as found, with no decision
  recorded, so that the next pass asks again.
- **FR-005**: The system MUST verify the identity of the process it is about to end against the
  session record before signalling anything, and MUST NOT signal on a recorded process id alone.
- **FR-006**: On confirming the process is gone, the system MUST close the session record with an
  end time and a reason naming retirement, and MUST leave the work item in the state it is in.
- **FR-007**: If the process cannot be confirmed gone, the system MUST leave the session record
  open, MUST report the failure, and MUST NOT claim the slot was released.
- **FR-008**: Retirement MUST be idempotent: a session already retired, or one that recorded its
  own ending between the decision and the signal, MUST settle on that record without a second
  transition and without being reported as a failure.
- **FR-009**: The system MUST NOT raise `orphan_session` for a session it is retiring or has
  retired on this pass.
- **FR-010**: A session record for a simulated work item MUST be closed without anything being
  signalled, and MUST NOT be routed through the real termination path.
- **FR-011**: The system MUST choose whether a session was hosted by a real process or by
  simulation from the session record itself, never from the effect level in force at the time of
  retirement.

**What retirement releases**

- **FR-012**: A retired session's slot MUST be released from both the global concurrency count and
  the per-repository count as soon as its record is closed.
- **FR-013**: A session under a terminal item whose process is still alive MUST go on counting
  toward both caps until the process is actually gone. Retirement, not the item's state, is what
  frees a slot.
- **FR-014**: The terminal window hosting a retired worker MUST close as a consequence of the
  worker ending, with no separate action.
- **FR-015**: A retired session MUST leave its worktree eligible for cleanup under the two existing
  guards, which are unchanged by this feature.
- **FR-016**: The transcript of a retired session MUST remain readable and resumable afterwards.
  Retirement ends a process; it does not discard the record of what that process did.

**Ending a session by hand**

- **FR-017**: The maintainer MUST be able to end the session of a work item in any state, including
  a terminal one, and have the record settle correctly.
- **FR-018**: When a session under a terminal item is stopped by hand and confirmed gone, the
  system MUST close the session record and MUST leave the work item in its terminal state.
- **FR-019**: The system MUST NOT report that a session "had already recorded its own ending" when
  it had not. The message MUST describe what actually happened.
- **FR-020**: Stopping a session by hand MUST keep every existing guard: the implausible-pid
  refusal, the process-identity check, and the refusal to settle anything when the process
  survives.

**Anomaly hygiene**

- **FR-021**: An unacknowledged `orphan_session` anomaly MUST be resolved automatically once the
  process it names is confirmed no longer to be the process it described.
- **FR-022**: Resolution MUST be recorded — what was resolved, when, and on what evidence — and
  MUST be distinguishable from a maintainer acknowledging it by hand.
- **FR-023**: No anomaly kind other than `orphan_session` changes behaviour. Every other kind is
  still cleared only by acknowledgement.
- **FR-024**: An `orphan_session` whose process is still the process it described MUST stay listed
  and MUST NOT be duplicated.

**Accountability**

- **FR-025**: Every retirement MUST be logged before the signal is sent, because ending a process
  is irreversible from the system's side, and the outcome MUST be logged after.
- **FR-026**: A decision not to retire MUST be reconstructable from the log to the extent that it
  is a decision — a quiet-period deferral writes nothing (FR-004), and that silence is itself the
  documented exception, named here.
- **FR-027**: The reconciliation pass's existing summary MUST report how many sessions were
  retired and how many anomalies were resolved.

### Key Entities

- **Work item**: the unit of work, and the thing whose terminal state plus closed issue is the
  trigger. Unchanged in shape.
- **Session record**: the row that is open for as long as a worker runs and that holds a capacity
  slot. Gains a way of being closed that is neither an exit record nor a cancellation.
- **Anomaly**: a detected condition awaiting attention. Gains, for one kind only, a resolved state
  distinct from acknowledged.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A work item taken from dispatch through to a merged pull request and a closed issue
  leaves behind zero anomalies, zero held capacity slots, and zero open terminal windows, with no
  action by the maintainer beyond merging.
- **SC-002**: With the concurrency cap at three, at least ten items can be worked to completion in
  sequence without dispatch ever being blocked by finished work holding the cap. Today the third
  successful item wedges the machine permanently.
- **SC-003**: A worker the maintainer is using is never ended by the system: a session that is
  attached to and typed in at least once per quiet period survives every reconciliation pass after
  its issue closes, for an unlimited number of passes.
- **SC-004**: With cleanup enabled, a finished item's worktree is reclaimed within one cleanup
  consideration of its session being retired, rather than being reported `skipped` indefinitely.
- **SC-005**: `robot-army anomalies` reports only conditions that are true at the moment it is
  read, for the `orphan_session` kind.
- **SC-006**: Every command that reports stopping a session reports what actually happened, and no
  message asserts an ending the system did not observe.

## Assumptions

- **The automatic trigger is `done` plus a closed issue, and nothing else.** Confirmed on this
  feature's clarification: `abandoned` and `failed` keep their workers, because those are the
  states where the work is unfinished and the session may be the reason the maintainer is about to
  attach.
- **A finished-but-alive session keeps its slot.** Confirmed on clarification. The existing
  contract — that reporting fewer running sessions than exist oversubscribes the quota the cap
  protects — is preserved verbatim. This feature frees slots by ending processes, never by
  discounting live ones.
- **No new configuration key.** Retirement is what the system does with a session whose work has
  been accepted; there is no switch. This differs deliberately from `cleanup.on_issue_close`, which
  is off by default because it deletes work irreversibly. Retirement deletes nothing: the transcript
  survives and the session remains resumable, so the risk the cleanup switch guards against does not
  apply here. The constitution's rule against a knob with one caller and no second use in hand
  settles the rest. If the plan phase finds a reason to reverse this, it is the one assumption here
  worth revisiting first.
- **The quiet period is a fixed interval, not a configured one**, matching the existing
  no-transcript grace period, which is a constant for the same reason.
- **A worker's own output is an observable signal.** The system already reasons about whether a
  session has written a transcript; this feature assumes the same material can answer "has it
  written anything lately".
- **Ending the worker ends its terminal window.** The window hosts a chain that exists only to run
  the worker and report its exit, so the window closing is a consequence rather than a separate
  action to build.
- **The three anomalies currently on the machine are evidence, not state to migrate.** Nothing in
  this feature is required to clean up the two `orphan_session` rows for items 45 and 54 — User
  Story 3 will resolve them once their processes end, and User Story 2 is how they end.
- **Reopened issues are out of scope.** An item that goes back to work does so through the existing
  resume and restart paths, which start a session of their own.
