# Feature Specification: `resume` That Actually Resumes, and a Failure That Actually Fails

**Feature Branch**: `robot-army/issue-35-resume-has-never-worked`

**Created**: 2026-08-30

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#35 — "`resume` has never worked: `--session-id` with `--resume` is rejected by the CLI, and the failure wedges the item in `dispatching`." Found during the issue #1 verification round at T078, the phone round, on `no-remote`.

## User Scenarios & Testing *(mandatory)*

<!--
  Two stories, both P1, because the issue is two independent defects that happened to be
  discovered by one action. Story 1 makes resume work. Story 2 makes a launch that dies
  young report itself as dead — which is true of any session, resumed or not, and would
  still be worth fixing if Story 1 were already done. Story 3 is the guard against the
  same class of defect shipping again, and is separable from both.
-->

### User Story 1 - Resuming an interrupted item and having it run (Priority: P1)

An item was interrupted — the maintainer closed the laptop, the session was killed, the
machine restarted. The item sits in `interrupted` with a previous session that holds
everything the worker had figured out before it stopped. The maintainer, often from a phone,
taps "resume" and expects a new worker to pick up where the last one left off: same
worktree, same branch, the prior conversation restored, a fresh attempt recorded.

Today that never happens. The launch composes a command the worker binary rejects outright,
so the process exits within a second, before it has done anything at all. The maintainer sees
a terminal tab open, sees it immediately print that the terminal is gone, and sees the item
never leave `dispatching`. Nothing in the interface says the launch was refused.

After this change, resuming an interrupted item produces a running worker with the previous
session's context restored, under a session identity the system chose and can therefore
track.

**Why this priority**: `resume` is US2 of the 002 milestone and the primary action the web
interface exists to offer. It is the recovery path for every interruption, it is what
quickstart scenario 3 and T078 are built around, and it has never once worked.

**Independent Test**: Take an item in `interrupted` with a recorded previous session, resume
it against the real worker binary, and confirm the worker starts, stays up past the
confirmation window, carries the earlier session's context, and that the item reaches
`active`.

**Acceptance Scenarios**:

1. **Given** an item in `interrupted` whose most recent session ended and was recorded,
   **When** the maintainer resumes it, **Then** a worker process starts and remains running
   past the confirmation window, and the item transitions `dispatching` → `active`.
2. **Given** that resumed worker, **When** it starts, **Then** its conversation contains the
   previous session's context rather than beginning empty.
3. **Given** that resumed worker, **When** the launch is confirmed, **Then** the session the
   system tracks carries the identity the system asked for, so that the existing
   session-identity mismatch detection does not fire and later attach, terminate, and log
   operations address the right process.
4. **Given** a resumed launch, **When** the attempt is recorded, **Then** it is recorded as a
   new attempt against the same work item, distinct from the session it restored, and the
   record names which session it was restored from.
5. **Given** a fresh dispatch of an item that has no previous session, **When** it launches,
   **Then** its behaviour is unchanged from today in every respect — this change touches only
   the restoring case.
6. **Given** an item in a state other than `interrupted`, **When** a resume is requested,
   **Then** it is refused with the same explanation it gives today.

---

### User Story 2 - A launch that dies young is reported, not wedged (Priority: P1)

A worker can fail immediately: a rejected argument, a missing binary, a worktree that
vanished, a crash on startup. When that happens the maintainer needs the system to say so —
in the interface, in the log, and in the item's state — at the moment it is detected.

Today, when a worker exits fast enough to record its own exit *before* the confirmation
window elapses, the two paths collide. The confirmation path tries to declare the session
lost, the session has already declared itself exited, the state gate refuses the
contradiction, and the resulting error escapes the operation. The work item is never failed.
It stays in `dispatching` — a state that reads as "starting up, be patient" — until a
15-minute reaper eventually clears it. The failure was detected in under three seconds and
reported to nobody.

After this change, a session that has already reported its own exit is accepted as the
answer to "did this launch confirm?", the work item is failed at that moment with the reason
that actually applies, and no item is left in `dispatching` because an error escaped.

**Why this priority**: Equal to Story 1, because it is not specific to resume. Any session
that exits quickly hits it — the earlier broken-binary case (T064) escaped only because that
worker died before it could write an exit record. Silent failure is the one thing the
constitution names as forbidden, and this is silent failure with a 15-minute fuse.

**Independent Test**: Cause a launch whose worker exits non-zero immediately and records that
exit before the confirmation window closes; confirm the item ends in `failed`, with the
reason and the worker's exit status visible, well inside the confirmation window, and that
the reaper is never what resolves it.

**Acceptance Scenarios**:

1. **Given** a launch whose worker exits with a non-zero status and whose exit is recorded
   before the confirmation window elapses, **When** confirmation is evaluated, **Then** the
   already-recorded exit is used as the outcome, the session keeps the state it recorded for
   itself, and no illegal-transition error is raised.
2. **Given** the same launch, **When** the outcome is decided, **Then** the work item leaves
   `dispatching` for `failed`, and the failure reason names that the worker exited and with
   what status.
3. **Given** the same launch initiated from the web interface, **When** the confirmation
   result is reported, **Then** the interface shows the item as failed with that reason
   rather than showing it as still dispatching.
4. **Given** a launch whose worker never starts and never records anything, **When** the
   confirmation window elapses, **Then** the session is declared lost and the item failed,
   exactly as today — the existing unconfirmed path is preserved.
5. **Given** a launch whose worker exits cleanly and immediately, recording a zero exit before
   confirmation elapses, **When** confirmation is evaluated, **Then** the recorded clean exit
   is the outcome and the item is resolved by the ordinary end-of-session rules rather than
   being contradicted into a lost session.
6. **Given** any failure during a launch, **When** the operation returns, **Then** the work
   item is in a settled state, never `dispatching`. The dispatching age reaper remains a
   backstop for genuinely stuck launches and MUST NOT be the mechanism that resolves a
   failure the system already detected.
7. **Given** the failure, **When** the record is read afterwards, **Then** the log alone
   explains what was launched, that it exited, with what status, and what the item became —
   without re-running anything.

---

### User Story 3 - The same class of defect cannot ship again unnoticed (Priority: P3)

The rejected flag combination was invisible to every automated test, because the simulated
session host never executes the command it is handed. The composed command was asserted to be
the list the code intended to build, and it was — the list was simply one the real binary
refuses. Only handing it to the actual worker revealed it.

The maintainer wants a cheap, bounded check that closes that specific gap: the flag
combinations this system composes are exercised against the real worker binary, so a
combination the worker rejects is caught by running the suite on this machine rather than by a
manual phone round months later.

**Why this priority**: It prevents recurrence rather than fixing the outage, so it is behind
both fixes. It is also the story most at risk of growing: the value is in checking the small,
fixed set of launch shapes this system actually produces, not in building a general harness
that runs real workers.

**Independent Test**: With the worker binary present, run the check and confirm it rejects a
deliberately contradictory flag combination and accepts the combinations the system composes;
with the binary absent, confirm the check reports itself as not run rather than passing
silently or failing the suite.

**Acceptance Scenarios**:

1. **Given** the worker binary is available, **When** the check runs, **Then** every launch
   shape the system can compose — fresh and restoring, across the permission modes and the
   optional model selection — is confirmed acceptable to the real binary.
2. **Given** a launch shape the real binary rejects, **When** the check runs, **Then** it
   fails and names the rejected combination and the binary's own complaint.
3. **Given** the worker binary is not installed, **When** the check runs, **Then** it reports
   that it was skipped and why, and does not report success.
4. **Given** the check runs, **When** it exercises the binary, **Then** it does so without
   dispatching work, creating worktrees, or leaving a session behind.

---

### Edge Cases

- The previous session's context is gone (its transcript was pruned, or the worker no longer
  recognises the id) even though the record of it remains. The launch must fail visibly with
  a reason that names the missing context, not start an empty session that looks like a
  successful resume.
- The item has more than one previous session. Resume restores the most recent one, which is
  what the maintainer means by "keep going".
- The worker exits *during* the confirmation wait rather than before it starts — the exit
  record lands mid-window. The outcome is the same as Story 2 scenario 1: the recorded exit
  wins.
- The worker exits after confirmation succeeded. Unchanged: the ordinary end-of-session path
  owns that, and this change must not intercept it.
- A resumed worker starts under a session identity other than the one requested. That remains
  an anomaly, not a success, and the existing mismatch detection must still fire.
- An item already wedged in `dispatching` from before this change. It is resolved by the
  existing age-based backstop; no migration or repair command is introduced for it.
- Resume is requested twice in quick succession for the same item. The second request must not
  produce a second concurrent worker for one item.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a launch restores a previous session's context, the command the system
  composes MUST be one the worker binary accepts. It MUST combine the request for a specific
  session identity with the request to restore prior context in the form the worker documents
  for that combination, rather than a form the worker rejects outright.
- **FR-002**: A restoring launch MUST be recorded as a new attempt on the work item, and the
  record MUST name the session whose context it restored.
- **FR-003**: A restoring launch MUST run under the session identity the system chose, so that
  every subsequent operation that addresses the session — confirmation, attach, terminate,
  exit correlation, log correlation — addresses the session that was actually started.
- **FR-004**: A launch that does not restore prior context MUST compose exactly the command it
  composes today.
- **FR-005**: When the confirmation window elapses and the session has already recorded a
  terminal outcome for itself, the system MUST accept that recorded outcome as the answer and
  MUST NOT attempt to overwrite it with a "lost" outcome.
- **FR-006**: When the confirmation window elapses and the session has recorded nothing, the
  system MUST declare it lost and fail the work item, as it does today.
- **FR-007**: A work item whose launch failed MUST be moved out of `dispatching` at the moment
  the failure is detected, with a reason that states what failed and, when the worker exited,
  its exit status.
- **FR-008**: No launch failure may leave the work item in `dispatching` by way of an error
  escaping the operation. Every launch path MUST settle the item.
- **FR-009**: The age-based `dispatching` reaper MUST remain in place as a backstop for
  launches that genuinely hang, and MUST NOT be the mechanism by which a detected failure is
  resolved.
- **FR-010**: Both the confirmation outcome and the resulting work item state change MUST be
  recorded durably at the time they occur, with enough detail to reconstruct afterwards what
  was launched, whether it confirmed, whether it exited, and with what status.
- **FR-011**: A resume requested for an item that is not interrupted, or for an item with no
  previous session, MUST be refused with the explanation it gives today.
- **FR-012**: Where the interface reports the result of a resume, it MUST reflect the settled
  outcome — including immediate failure — rather than leaving the item shown as dispatching.
- **FR-013**: The project MUST include a check that exercises the launch shapes it composes
  today against the real worker binary. The set it covers is the set the system can actually
  produce — restoring and non-restoring, across the permission modes, with and without an
  explicit model selection — and no more; it MUST NOT grow into a general facility for running
  real workers.
- **FR-014**: That check MUST fail, naming the rejected shape and the binary's own complaint,
  when the binary refuses a shape the system composes.
- **FR-015**: When the worker binary is unavailable, that check MUST report itself as not run
  and say why. It MUST NOT report success, and it MUST NOT fail the suite on a machine where
  the binary is simply absent.
- **FR-016**: That check MUST exercise the binary without dispatching work, creating
  worktrees, or leaving a session behind.

### Key Entities

- **Work item**: the unit of work being dispatched. Carries the state the maintainer reads —
  `interrupted`, `dispatching`, `active`, `failed` — and must never rest in `dispatching`
  after a failure has been detected.
- **Session**: one attempt at running a worker for a work item. Carries its own identity, the
  attempt number, the command it was launched with, its lifecycle state, and its exit status.
  A session that has recorded its own ending owns that ending.
- **Launch command**: the exact invocation composed for one session, including whether it
  restores a previous session's context. Its correctness is defined by what the worker binary
  accepts, not by what the composing code intended.
- **Exit record**: the durable evidence a worker leaves when it stops. It may arrive before,
  during, or after the confirmation window, and its arrival before confirmation completes is a
  normal condition rather than an error.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Resuming an interrupted item from the web interface produces a running worker
  with the prior context restored, and the item reaches `active`, in 100% of attempts where
  the worktree and prior context are intact — against the current rate of zero.
- **SC-002**: A worker that fails at startup results in the item being shown as failed within
  the confirmation window (45 seconds by default), rather than after the 15-minute backstop —
  a reduction in time-to-truth of roughly twenty-fold.
- **SC-003**: Across the full set of launch-failure situations — rejected arguments, missing
  binary, missing worktree, immediate non-zero exit, immediate clean exit, nothing recorded at
  all — no work item is left in `dispatching` once the launch operation has returned.
- **SC-004**: For any failed launch, the durable record alone answers what was launched,
  whether it confirmed, whether it exited and with what status, and what the item became,
  without re-running anything.
- **SC-005**: The maintainer can carry out quickstart scenario 3 and the phone round's resume
  step end to end without reading source code or querying the database to find out what
  happened.
- **SC-006**: A deliberately contradictory launch shape is caught by running the project's own
  checks on this machine, rather than surviving to a manual verification round.

## Assumptions

- The worker binary's documented behaviour for this combination is stable enough to rely on:
  requesting a specific session identity while restoring prior context is supported when the
  launch is declared to be a new, forked session. This is the behaviour the existing code
  comment already describes as the intent.
- Restoring prior context under a freshly chosen session identity is what "resume" means here.
  Reusing the prior session's identity is explicitly not wanted: attempts are distinct, and
  the system tracks each one separately.
- The most recent previous session is the one to restore. No selection among older sessions is
  offered.
- Items already wedged in `dispatching` before this change are left to the existing age-based
  backstop. No repair command, migration, or manual unwedge action is added.
- The confirmation window default (45 seconds) and the dispatching backstop default (15
  minutes) are unchanged by this feature.
- The simulated session host keeps its present role: it stands in for a host at a simulated
  effect level and is not expected to execute launch commands. Closing the gap it leaves is
  the subject of Story 3, not a change to the simulator.
- The worker binary can be asked whether it accepts a combination of arguments cheaply and
  without doing the work those arguments describe — it validates its arguments before acting,
  and rejects an unacceptable combination promptly and with a message that names the problem.
  Story 3's check depends on that; without it the check would have to run real workers, which
  is explicitly out of scope.
- Whether the interface distinguishes "failed immediately" from other failures is not
  specified here; showing the settled state and its reason is sufficient.

## Dependencies

- The worker binary must be installed on the machine for Story 1 to be verifiable at all, and
  for Story 3's check to run rather than skip.
- The existing durable exit-record mechanism, the session state gate, and the session identity
  mismatch detection all remain in place; this feature changes how their interaction is
  resolved, not whether they exist.
