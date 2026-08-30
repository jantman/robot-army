# Feature Specification: A Stop That Is Confirmed, Not Assumed

**Feature Branch**: `issues/34`

**Created**: 2026-08-30

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#34 — "`cancel` reports a session stopped when it is still running: `systemctl stop` exit 0 is trusted as proof of death." Found during the issue #1 verification round at `no-remote`, running 001 quickstart scenario 5's cancel outcome against a real session.

## User Scenarios & Testing *(mandatory)*

<!--
  Three stories. Story 1 is the defect itself: a stop that is believed rather than checked.
  Story 2 is the report that stop produces, which today states as settled fact something
  nothing verified — separable, because a stop could be made reliable and still be described
  dishonestly. Story 3 generalises the lesson the project already learned once on the launch
  side, and is worth doing even if the first two were already done by hand.
-->

### User Story 1 - Cancelling a session actually ends it (Priority: P1)

The maintainer decides a running worker should stop — it has gone down a wrong path, it is
burning quota on work that is no longer wanted, or the machine is needed for something else.
They cancel the item, from the terminal or from the phone, and expect one of exactly two
outcomes: the worker is gone, or the system says plainly that it could not end it.

Today a third outcome is the common one. The primary stop path reports success without
having killed anything, that success is taken as proof, the stronger fallback that would have
actually signalled the process is skipped, the item is marked `interrupted`, and the worker
keeps running — scheduled, accumulating CPU, editing a worktree, spending subscription quota
— for as long as the machine stays up. Nothing later notices: the item is no longer in a
state any sweep visits, so the abandoned worker is never revisited.

After this change, a cancel that reports success has been verified: the process the system
was tracking is confirmed gone before anything is reported or any state is recorded. A cancel
that cannot achieve that says so and fails.

**Why this priority**: This is the defect. An unstoppable stop is worse than no stop at all,
because the maintainer moves on believing the machine is quiet. It leaves an autonomous agent
running unsupervised and unobserved, which is the precise failure the project exists to
prevent.

**Independent Test**: Cancel a real running session whose primary stop path is guaranteed to
report success without killing it, and confirm the worker process is gone afterwards — or, if
it survives every path, that the cancel reported failure and left the item's state untouched.

**Acceptance Scenarios**:

1. **Given** a running session, **When** the maintainer cancels it, **Then** the system
   verifies the tracked process is gone before it reports success or changes the item's state.
2. **Given** a running session whose primary stop path returns success without ending the
   process, **When** the maintainer cancels it, **Then** the system detects the process is
   still alive and escalates to the remaining stop paths rather than returning.
3. **Given** a running session that survives every available stop path within the bounded
   attempt, **When** the maintainer cancels it, **Then** the command fails with a non-zero
   exit, names what is still running and how to reach it, and leaves the work item in the
   state it was already in.
4. **Given** a session whose process has already exited before the cancel arrives, **When**
   the maintainer cancels it, **Then** the cancel succeeds, reports that there was nothing
   left to stop, and settles the item's state as it would after any confirmed stop.
5. **Given** a confirmed stop, **When** the item becomes `interrupted`, **Then** the session
   record stops describing that session as running.
6. **Given** a cancel issued from the web interface, **When** it completes, **Then** it obeys
   every rule above identically to the terminal command — the two surfaces share one
   behaviour.
7. **Given** an effect level below fully live, **When** a session is cancelled, **Then** the
   confirmation step behaves consistently with the simulation rather than sending a
   simulated stop down the failure branch.

---

### User Story 2 - The report describes what was confirmed, not what was tried (Priority: P1)

The maintainer reads one line of output and decides whether to walk away. That line must be
worth trusting.

Today it reads `stopped session <id> via systemd scope <scope>` — a completed action, stated
as fact, when all that happened was a command that exited zero. The durable record has the
same shape: a stop action recorded `ok` on the strength of an exit status.

After this change, the reported outcome and the durable record both distinguish what was
attempted from what was observed: which path was taken, whether the process was confirmed
gone, and — when confirmation was needed more than once — that escalation happened.

**Why this priority**: Fixing the mechanism without fixing the report leaves the maintainer
reading the same sentence and having to guess whether this build is the one that means it.
The record is also the only way to reconstruct, later, whether a given stop actually took
effect, which is what Principle III requires of it.

**Independent Test**: Cancel a session under each of the three shapes — clean stop, stop that
needed escalation, stop that failed entirely — and confirm from the printed line alone, and
from the durable record alone, which shape occurred.

**Acceptance Scenarios**:

1. **Given** a stop confirmed on the first path, **When** the result is reported, **Then** it
   states that the session was stopped and that its end was confirmed.
2. **Given** a stop that required escalation, **When** the result is reported, **Then** it
   names that the first path did not end the process and which path did.
3. **Given** a stop that failed, **When** the result is reported, **Then** it does not claim
   the session was stopped, and it names what remains alive.
4. **Given** any cancel, **When** the durable record is read afterwards without re-running
   anything, **Then** it answers which stop paths were attempted, what each returned, whether
   the process was observed gone, and how long confirmation took.
5. **Given** a stop path that reports success while the process survives, **When** it is
   recorded, **Then** the record shows both the reported success and the contradicting
   observation, rather than only the success.

---

### User Story 3 - Termination is confirmed the way launching already is (Priority: P3)

The project already knows this lesson in the other direction: a launch call returning success
is not evidence a session started, so dispatch confirms against an independent observation
before it will call an item `active`. Termination had no equivalent — the system confirmed
its launches and trusted its terminations.

The maintainer wants that asymmetry closed as a stated rule rather than as a one-off patch,
so the next outward-facing action whose exit status looks like proof is treated with the same
suspicion.

**Why this priority**: This is the durable value, but the system is safe once Stories 1 and 2
land. It is separable and can follow.

**Independent Test**: Read the project's own guidance and confirm the rule is written down
where the next such boundary call would be written, and that the termination path is covered
by a check that fails if confirmation is removed.

**Acceptance Scenarios**:

1. **Given** the project's documented guidance for boundary operations, **When** a maintainer
   adds an outward-facing operation whose effect is observable, **Then** the guidance states
   that the call's exit status is not evidence of its effect and that the effect must be
   confirmed independently.
2. **Given** the termination path, **When** its confirmation step is removed or bypassed,
   **Then** the project's own checks fail rather than passing quietly.

---

### Edge Cases

- What happens when the tracked process identifier has been reused by an unrelated process
  since the session started? The system must not confirm death against, or signal, a process
  that is not the session it launched.
- What happens when no process identifier was ever recorded for the session and the primary
  stop path reports success? There is nothing to confirm against; the outcome must be
  reported as unconfirmed rather than as success.
- What happens when the primary path ends the top of the tree but leaves a child alive?
  Confirmation is about the session the system tracks; what it can and cannot observe must be
  stated rather than implied.
- What happens when confirmation cannot complete promptly — the process is a zombie, or is
  stuck in uninterruptible sleep? The confirmation must be bounded in time and report a
  timeout as "not confirmed", never as success.
- What happens when the session ends on its own in the middle of the cancel? That is a
  success, not a failure, and must not produce a contradictory state change.
- What happens when a cancel fails? The item must remain in whatever state it held, so that
  the surviving worker is still visible to the sweeps that visit running items.
- What happens when the maintainer cancels the same item twice in a row? The second cancel
  must behave as the already-exited case rather than reporting a fresh stop.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST NOT treat the exit status of a stop command as evidence that
  the session's process ended. Every stop MUST be followed by an independent observation of
  whether the tracked process still exists.
- **FR-002**: When a stop path reports success but the tracked process is still observed
  alive, the system MUST continue to the remaining stop paths rather than returning, and MUST
  record that the reported success was contradicted.
- **FR-003**: Confirmation MUST be bounded in time. A confirmation that does not complete
  within its bound MUST be reported as "not confirmed", never as success.
- **FR-004**: The system MUST verify the identity of the process it observes and signals — a
  recycled process identifier MUST NOT be read as evidence of death, and MUST NOT be
  signalled as if it were the session.
- **FR-005**: A stop for which no confirmation is possible, because no process identifier was
  recorded, MUST be reported as unconfirmed rather than as a successful stop.
- **FR-006**: A cancel that cannot confirm the session ended MUST fail with a non-zero exit,
  MUST NOT change the work item's state, and MUST name what is still running and how the
  maintainer can reach or inspect it.
- **FR-007**: A cancel MUST change the work item to `interrupted` only after the session's end
  has been confirmed.
- **FR-008**: A confirmed stop MUST settle the session record so that the stopped session no
  longer reports itself as running.
- **FR-009**: A cancel of a session whose process is already gone MUST succeed, MUST say that
  there was nothing left to stop, and MUST settle the item and session records exactly as a
  confirmed stop does.
- **FR-010**: Reported output MUST distinguish a confirmed stop, a stop that required
  escalation, and a stop that could not be confirmed, and MUST NOT state as fact any effect
  the system did not observe.
- **FR-011**: The durable record MUST allow reconstruction, without re-running anything, of
  which stop paths were attempted, what each returned, whether the process was observed gone,
  how long confirmation took, and which outcome was reported to the maintainer.
- **FR-012**: The web interface and the terminal command MUST produce the same cancel
  behaviour, including the failure case — a cancel that fails MUST be visible as a failure in
  the web interface rather than reported as done.
- **FR-013**: Stopping a session MUST continue to stop that session's whole process tree and
  no other session's; confirmation MUST NOT be achieved by broadening what is killed.
- **FR-014**: At effect levels where sessions are simulated, the confirmation step MUST take
  the same branch the real path takes for a successful stop, so that simulated cancels do not
  diverge into the failure path.
- **FR-015**: The project's guidance for outward-facing boundary operations MUST state that a
  call's exit status is not evidence of its effect, and that an operation with an observable
  effect must confirm that effect independently.
- **FR-016**: The project MUST carry checks that fail if the confirmation step is removed from
  the termination path, including a check covering the specific case of a stop path that
  reports success while the process survives.

### Key Entities

- **Session**: A launched worker, tracked by the identity the system generated, its socket,
  its process identifier, and the isolation scope recorded when it was confirmed. Cancelling
  addresses a session; confirming a stop is an observation about the session's process.
- **Stop attempt**: One path tried against a session — the recorded scope, then the process
  group — with what it returned and what was observed afterwards.
- **Termination outcome**: The settled result of a cancel: confirmed stopped, stopped after
  escalation, already gone, or not confirmed. This is what is reported and what drives the
  state change.
- **Work item**: The unit of work the session belongs to. Its state changes only on a
  confirmed outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the reproduction from the issue — a real session whose recorded scope is
  already inactive, so the stop command exits zero having killed nothing — cancelling ends the
  worker process, verified by the process no longer existing afterwards.
- **SC-002**: Across the full set of cancel situations — clean stop, stop needing escalation,
  process already gone, process that survives everything — the reported outcome matches what
  actually happened in every case, with no case reporting a stop that did not occur.
- **SC-003**: No cancel that reports success leaves a worker process alive; and no work item
  is marked `interrupted` while the session it names is still running.
- **SC-004**: A cancel that cannot end the session exits non-zero and tells the maintainer
  what is still running, in both the terminal and the web interface.
- **SC-005**: For any cancel, the durable record alone answers what was attempted, what was
  observed, and what was reported, without re-running anything.
- **SC-006**: 001 quickstart scenario 5's cancel outcome passes against a real session in the
  verification round that found this defect.
- **SC-007**: A cancel completes — success or a reported failure — within a bounded, documented
  time rather than waiting indefinitely on a process that will not die.

## Assumptions

- The existing process-group fallback is correct as written; the defect is that it is
  unreachable whenever the primary path reports success. This work makes it reachable and
  adds confirmation around both paths — it does not redesign how killing is done.
- Observing whether a process still exists is cheap and local, so confirmation can be polled
  within a short bound without meaningfully slowing a cancel.
- "Confirmed gone" is defined against the process the system recorded for the session. A
  descendant that escaped the tracked tree is outside what the system can observe today, and
  is out of scope here.
- The two known gaps that would otherwise catch a survivor — reconciliation skipping
  simulated sessions, and the session sweep visiting only `active` items — remain open in
  their own issues. This spec does not rely on either as a backstop, which is why the failure
  case must refuse to change the item's state.
- Bounded escalation ends in a forceful signal, as the existing fallback already does. No new,
  more aggressive killing mechanism is introduced.
- The maintainer's cancels are always about a session on this machine, launched by this
  system; no remote or cross-machine termination is in scope.

## Dependencies

- The recorded isolation scope and process identifier captured for each session at
  confirmation time, which are the handles confirmation is performed against.
- The existing launch-confirmation behaviour, which this mirrors on the stopping side and
  which must remain unchanged.
- The existing work item and session state gates, which this work uses rather than bypasses:
  no state change may be forced past them to make a cancel look successful.
