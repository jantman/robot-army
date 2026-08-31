# Feature Specification: Refuse to Signal an Unverified PID During Termination

**Feature Branch**: `20260831-184927-guard-terminate-pid`

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "issue #69 on this repo" — *terminate signals the recorded pid without validating it: a pid of 1 makes cancel run kill(-1) and wipe the user session*

## Context

Stopping a session signals the process group of the pid recorded on the session row. Nothing
between the recorded value and the signal asks whether that pid could plausibly be this
session's process. A recorded pid of `1` resolves to process group `1`, and signalling process
group `1` means "signal every process this user is allowed to signal" — measured on 2026-08-31,
this ended the maintainer's whole desktop session, took the orchestrator's own daemon and web
service down with it one second in, and then reported the cancel as a confirmed success, because
the only thing confirmation asks is whether the recorded pid is gone. It certainly was.

The recorded pid in that incident was placed by hand while staging a test scenario, so this is
not a defect reachable through ordinary operation *today*. It is worth fixing anyway, because the
signalling code holds no opinion at all about what a valid pid is: any route that lands a `0`, a
`1`, or a stranger's pid in that column — a partially written row, a migration, a simulated
session row reaching the real host after the effect level is raised, a future default — produces
an unrecoverable, unwarned wipe of everything the operating-system user owns. **FR-006 of the
original charter says the daemon never touches what it did not start.** This path can touch
everything.

Related but out of scope: **#67** (a recorded systemd scope that contains unrelated processes)
is the same blind spot on the other termination rung — confirming that the target died says
nothing about what else died with it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A cancel can never signal outside this session (Priority: P1)

The maintainer cancels a work item whose session row carries a pid that cannot belong to a
session this orchestrator started — nothing recorded, `0`, `1`, or any pid whose process group
resolves to `1`. The cancel stops before any signal is sent, says plainly which value it rejected
and why, and leaves the work item exactly as it found it.

**Why this priority**: This is the entire severity of the report. Without it, one command can end
every process the user owns, including the orchestrator itself, and then report success. Every
other part of this feature is refinement; this one removes the catastrophe.

**Independent Test**: Drive a cancel against a session row carrying each rejected pid value with
signal delivery instrumented. The story passes when zero signals are delivered, the command exits
non-zero, and the work item's state is unchanged.

**Acceptance Scenarios**:

1. **Given** a running session row whose recorded pid is `1`, **When** the maintainer cancels that
   item, **Then** no signal is sent to any process, the command reports that the recorded pid was
   rejected as impossible for a session process, and it exits non-zero.
2. **Given** a running session row whose recorded pid is `0`, **When** the maintainer cancels that
   item, **Then** no signal is sent to any process and the command reports the same rejection.
3. **Given** a running session row with no pid recorded and no systemd scope recorded, **When** the
   maintainer cancels that item, **Then** the command refuses without signalling and says there is
   nothing it can safely act on.
4. **Given** a session whose recorded pid is alive but whose process group resolves to `1`,
   **When** the maintainer cancels that item, **Then** the process group is not signalled and the
   command reports the rejected process group.
5. **Given** a session row with an ordinary, valid pid and matching recorded start time, **When**
   the maintainer cancels that item, **Then** termination proceeds exactly as it does today and the
   outcome is unchanged.

---

### User Story 2 - A signal is only ever sent to a positively identified process (Priority: P2)

Before any signal is delivered, the recorded pid is matched against the start time recorded for it
on the same row. A pid with no recorded start time has not been identified — it is a bare number —
and is refused rather than signalled on the strength of the number alone.

**Why this priority**: This is the principled version of Story 1's guard. Story 1 blocks the three
values known to be catastrophic; this blocks the whole class, including a recorded pid of `1` that
happens to carry a matching start time, and any row written without the identity the orchestrator
normally records. It reuses the identity check the system already applies for liveness.

**Independent Test**: Cancel a session row that has a live pid but an empty recorded start time,
with signal delivery instrumented. The story passes when nothing is signalled and the refusal names
the missing start time as the reason.

**Acceptance Scenarios**:

1. **Given** a running session row with a live pid and no recorded process start time, **When** the
   maintainer cancels that item, **Then** no signal is sent and the refusal names the missing start
   time.
2. **Given** a running session row whose recorded start time does not match the live process now
   holding that pid, **When** the maintainer cancels that item, **Then** no signal is sent to that
   process, because the orchestrator's process is gone and a stranger holds its number.
3. **Given** a session row whose recorded pid and start time match a live process, **When** the
   maintainer cancels that item, **Then** the signal is delivered as it is today.

---

### User Story 3 - The refusal is fully accounted for (Priority: P3)

Every refusal is written to the durable action record with the values that caused it, is visible
to the maintainer at the terminal, and is distinguishable in the record from both a successful stop
and a stop that could not be confirmed.

**Why this priority**: Required by the constitution's accountability principle, and practically
necessary: a refusal means a session row is malformed and a work item is stuck, and the maintainer
needs the record to say which row and which field without re-running anything. It is P3 only
because the harm is already removed by P1.

**Independent Test**: Trigger each refusal and read the action log alone. The story passes when the
log answers what was refused, on which session, on the strength of which recorded values, and that
nothing was signalled.

**Acceptance Scenarios**:

1. **Given** any refusal from Story 1 or Story 2, **When** the maintainer reads the action log,
   **Then** it contains a record naming the session, the rejected field and value, the reason, and
   that no signal was delivered.
2. **Given** any refusal, **When** the outcome is recorded, **Then** it is not recorded as a
   confirmed stop, and the work item is not moved to a terminated state.
3. **Given** any refusal, **When** the maintainer reads the terminal output, **Then** it names the
   session and the reason in one line, and the command's exit status is non-zero.

---

### User Story 4 - A simulated session is never handed to the real host (Priority: P3)

The maintainer runs the effect ladder as this project prescribes: dispatch at a simulated level,
raise the configured effect level, restart. A session row left behind by the simulated phase is
then cancelled. It is recognised as simulated from its own record and handled by the simulated
host, exactly as it would have been before the level was raised.

**Why this priority**: This closes the one route by which ordinary operation — no hand-edited
database, no bad migration — can put a pid of `0` in front of the real signalling path. P1 and P2
already make that route harmless; this makes it correct, so that cancelling a simulated session
stays a clean simulated stop rather than degrading into a refusal the maintainer has to interpret.

**Independent Test**: Dispatch at a simulated level, raise the configured level, cancel the
resulting item. The story passes when the cancel reports a confirmed simulated stop and the real
termination path is never entered.

**Acceptance Scenarios**:

1. **Given** a running session recorded as simulated, **When** the maintainer cancels it while the
   configured effect level would otherwise select the real session host, **Then** the simulated host
   handles the termination and the cancel reports a confirmed simulated stop.
2. **Given** the same session, **When** it is cancelled, **Then** the real termination path is never
   entered and no signal of any kind is delivered.
3. **Given** a session **not** recorded as simulated at a configured effect level that selects the
   real host, **When** the maintainer cancels it, **Then** the real host handles it as it does today.

---

### Edge Cases

- **A recorded pid of `1` that carries a matching recorded start time.** Identity validation alone
  would accept it, so the flat rejection of `0` and `1` must stand independently of the identity
  check rather than being replaced by it.
- **A recorded pid that is alive and identified but is not the orchestrator's to signal** (the user
  lacks permission). The refusal guards are not a permission check; a permission failure during
  signalling remains an unconfirmed stop and must not surface as an unhandled crash.
- **A simulated session row cancelled after the configured effect level is raised.** The row is
  routed to the simulated host on the strength of its own recorded simulated flag, so it never
  reaches the real termination path at all. The pid and identity guards would reject it even if it
  did — the two protections are deliberately redundant, because the report's whole lesson is that
  one layer of "this cannot happen" is not enough.
- **A session that already exited on its own.** This is not a refusal: the recorded pid is not
  present, the process is gone, and the existing "already gone" verdict is correct and must be kept
  distinct from a refusal in the record.
- **A recorded systemd scope with a bad pid on the same row.** The scope rung and the signal rung
  are separate; refusing to signal must not suppress or alter what the scope rung already does, nor
  turn an unconfirmed scope stop into a confirmed one.
- **The work item after a refusal.** It stays in whatever state it was in, so the sweeps that visit
  active items keep visiting it, rather than being parked in a state nothing revisits.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The termination path MUST refuse to send any signal when the recorded pid is absent,
  `0`, or `1`.
- **FR-002**: The termination path MUST refuse to send any signal when the process group resolved
  from the recorded pid is `1` or lower.
- **FR-003**: The termination path MUST NOT deliver a signal to a process that has not been
  positively identified as this session's; a recorded pid carrying no recorded process start time
  MUST be treated as unidentified and refused, not signalled on the strength of the pid alone.
- **FR-004**: A refusal MUST NOT be reported as a stop, a confirmed stop, or an "already gone"
  session; it MUST be a distinct outcome in both the action record and the terminal output.
- **FR-005**: A refusal MUST leave the work item and the session row in the state they were in
  before the cancel was attempted.
- **FR-006**: A cancel that ends in a refusal MUST exit non-zero and MUST tell the maintainer which
  session was involved, which recorded value was rejected, and why.
- **FR-007**: Every refusal MUST be written to the durable action record at the time it occurs,
  carrying the session identity, the rejected field and value, the reason, and the fact that no
  signal was delivered.
- **FR-008**: The guards MUST sit at the point where signals are delivered, not at the command
  layer, so that every present and future caller of termination inherits them.
- **FR-009**: Termination of a session with a valid recorded pid and a matching recorded start time
  MUST behave exactly as it does today — same rungs, same confirmation, same outcome.
- **FR-010**: The simulated session host MUST continue to terminate without observing or signalling
  anything, so that a simulated cancel does not take the refusal branch.
- **FR-011**: Termination of a session recorded as simulated MUST be handled by the simulated
  session host regardless of the configured effect level, so that raising the effect level cannot
  send a pre-existing simulated row down the real termination path.
- **FR-012**: The routing in FR-011 MUST be decided from the session record's own recorded
  simulated flag, not from the configuration in force at the moment of cancellation.
- **FR-013**: A simulated session terminated under FR-011 MUST produce the outcome it produces at a
  simulated effect level — a confirmed simulated stop, neither a refusal nor a real stop — so that
  cancelling a simulated session behaves identically before and after a go-live.

### Key Entities

- **Session record**: the orchestrator's memory of one launched session. The fields that matter
  here are the recorded process id, the recorded process start time that identifies it, the
  recorded systemd scope, the session state, and whether the session was simulated.
- **Termination outcome**: what termination *observed*, as distinct from what it was told. Carries
  whether the stop was confirmed, which rung achieved it, and — newly — whether termination refused
  to act and on what grounds.
- **Action record**: the durable, append-only log from which any past action must be reconstructible
  without re-running it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across every rejected recorded pid value (absent, `0`, `1`) and every process group
  that resolves to `1` or lower, the number of signals delivered to any process is **zero**.
- **SC-002**: A cancel against a malformed session row completes and reports its refusal in under
  one second, rather than spending the ten-second escalation wait that a delivered signal costs.
- **SC-003**: No cancel can end a process the orchestrator did not start; specifically, no cancel
  can end the maintainer's desktop session, shell, or the orchestrator's own daemon.
- **SC-004**: For 100% of refusals, a reader of the action record alone can state which session was
  involved, which recorded value was rejected, why, and that nothing was signalled — without
  re-running anything.
- **SC-005**: Cancels of well-formed sessions succeed at the same rate and by the same route as
  before the change; no previously working cancel becomes a refusal.
- **SC-006**: A refusal never leaves a work item in a state that no sweep revisits.
- **SC-007**: Cancelling a simulated session produces the same observable outcome before and after
  the configured effect level is raised, in 100% of cases, and delivers zero signals in both.

## Assumptions

- Any session row the orchestrator writes through ordinary operation records the process start time
  at the same moment it records the pid, so refusing rows that have a pid but no recorded start time
  rejects only rows that are already malformed. Rows that lose cancellability this way were never
  safely cancellable.
- Refusing is preferable to guessing. A malformed session row is an operator problem, and stopping
  with a clear message is a better outcome than any attempt to repair or infer the missing identity
  automatically.
- The existing confirmation behaviour is unchanged by this feature. Confirming that the recorded
  target died still says nothing about what else died with it; closing that gap is #67's work.
- The additional check suggested in the report — asserting that the pid is a descendant of the
  session's own worktree or process tree — is **out of scope** here. It overlaps #67 and is a larger
  change than the guard this issue needs.
- The systemd scope rung's behaviour is unchanged. This feature governs only what may be signalled.
- Reachability through the effect-level ladder (a simulated row cancelled after the level is raised)
  is treated as a real path, not a hypothetical, and is closed twice over: by the guards of FR-001
  through FR-003, and by the routing of FR-011.
- FR-011 makes session-host selection depend on the session record as well as the configured effect
  level, where every other boundary is selected by level alone. That is a deliberate departure from
  the existing "selection written as data, not branches" arrangement, chosen because a record that
  was created as simulated stays simulated for its whole life no matter what the configuration later
  becomes. The planning phase MUST justify it explicitly against the simplicity principle rather
  than let it pass unremarked.
