# Feature Specification: Minimum Daemon

**Feature Branch**: `001-minimum-daemon` *(not created — no `before_specify` git hook is configured in this project)*

**Created**: 2026-08-23

**Status**: Draft

**Input**: User description: "The application described in `docs/initial-planning/robot-army-planning.md` and with additional supporting research in `docs/initial-planning/m0-spike-plan.md`. This is a large initial implementation, so you'll need to let me know if this is suitable for a single spec or if we should use a roadmap with multiple sub-specs."

**Scope note**: This is milestone 001 of the roadmap in [`docs/roadmap.md`](../../docs/roadmap.md), corresponding to
M1 in the planning document. Trello, the web UI, per-repo concurrency caps, priority modes,
out-of-band session accounting, and automatic worktree cleanup are explicitly out of scope and
belong to later specs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Label an issue, get a working session (Priority: P1)

The author files (or already has) a GitHub issue describing work they want done. They add the
`robot-army` label to it by hand. Within a minute or two, a Claude Code session appears as a tab in
the kitty instance already running on their desk, sitting in a freshly prepared isolated checkout of
that repository, already primed with the issue's content, with Remote Control active so the author
can also drive it from their phone. The author sits down at it, or picks it up later from the couch.

**Why this priority**: This is the entire product in one sentence. Every other story in this spec
exists to make this one trustworthy. Without it there is nothing; with only it, the author already
gets real value on days when nothing goes wrong.

**Independent Test**: Label a real issue in a real repository, and confirm a live Claude Code
session appears in the running kitty instance, in an isolated checkout on a new branch, with the
issue content in its context — without the author touching a terminal.

**Acceptance Scenarios**:

1. **Given** a configured repository and an open issue authored by the author that carries the
   `robot-army` label, **When** the daemon polls, **Then** an isolated checkout is prepared on a new
   branch from a freshly fetched base branch, a Claude Code session is launched into the running
   kitty instance with that checkout as its working directory, and the work item is recorded as
   `active` only after the session's existence has been independently confirmed.
2. **Given** an eligible issue in a repository that declares post-create preparation steps,
   **When** the daemon prepares the checkout, **Then** those steps run to completion inside the
   checkout before any session is launched.
3. **Given** an issue that carries the label but was authored by somebody other than the author,
   **When** the daemon polls, **Then** it is not dispatched and the reason is recorded.
4. **Given** an issue that has already been dispatched, **When** the daemon polls again, **Then** no
   second checkout, branch, or session is created for it.
5. **Given** a repository the author has not explicitly onboarded, **When** an eligible issue appears
   in it, **Then** no session is launched and the item is surfaced as blocked on onboarding rather
   than launching a session that would hang on an invisible trust prompt.
6. **Given** a repository whose committed tool-permission settings have changed since onboarding,
   **When** the daemon attempts to dispatch, **Then** dispatch is refused and re-review is requested.

---

### User Story 2 - Know what happened when a session ends (Priority: P2)

The author finishes a session with `/exit`, or the session dies, or it never really started. In
every case the system knows which of those happened, distinguishes "the human deliberately stopped"
from "something killed it" from "the launch was misconfigured", and records the outcome without the
author having to reconstruct it.

**Why this priority**: A dispatcher that cannot tell success from failure is worse than no
dispatcher, because it looks like it is working. The planning research found three distinct ways a
launch reports success while having done nothing — this story is the answer to those.

**Independent Test**: Drive one session to a clean exit, kill a second, and misconfigure a third, and
confirm the three land in three different, correct states with the evidence recorded for each.

**Acceptance Scenarios**:

1. **Given** an active session, **When** the author ends it deliberately and it exits with code zero,
   **Then** the work item becomes `awaiting_review` and the session is recorded as having exited
   cleanly.
2. **Given** a dispatch with an invalid worker configuration, **When** the worker exits with a
   configuration-error code, **Then** the work item becomes `failed` with the captured error output,
   and it is not retried unless configuration changes.
3. **Given** an active session, **When** it is killed by a signal, **Then** the work item becomes
   `interrupted`, the signal is recorded distinctly from an ordinary non-zero exit, and the item is
   offered for resume rather than treated as a failure.
4. **Given** a work item in `awaiting_review`, **When** its source issue is closed, **Then** the work
   item becomes `done` regardless of session state.
5. **Given** a launch that returns success but produces no running session, **When** the daemon's
   confirmation window elapses, **Then** the item does not become `active` and is recorded as a
   failed dispatch with the launch details.
6. **Given** a session that started and ran but for which no resumable transcript was ever recorded,
   **When** the daemon checks the session after launch, **Then** this is surfaced as an anomaly
   rather than left to be discovered when a resume later fails.

---

### User Story 3 - Survive the terminal dying, the daemon restarting, and the machine rebooting (Priority: P3)

The author closes the last kitty window, or restarts their graphical session, or reboots. Sessions
that can survive do survive and can be reattached to. Sessions that cannot survive are correctly
recognised as gone rather than lingering as phantom "active" rows. Work that was in flight is
recoverable, and no work is silently lost or silently duplicated.

**Why this priority**: The planning research identifies terminal death as far more likely than a
reboot, and silent state drift as the specific failure mode that kills systems of this kind. This
story is what makes the system safe to leave running unattended.

**Independent Test**: With sessions running, kill the terminal emulator, then restart the daemon,
then reboot — and confirm at each stage that every work item's recorded state matches physical
reality, and that survivable sessions were reattachable.

**Acceptance Scenarios**:

1. **Given** running sessions, **When** the terminal emulator hosting them dies, **Then** the
   sessions keep running, remain reattachable from a newly started terminal, and the work items stay
   `active` rather than being marked lost.
2. **Given** running sessions, **When** the daemon itself is stopped and restarted, **Then** no
   session is killed, and the daemon reconciles all existing state before dispatching any new work.
3. **Given** work items recorded as `active`, **When** reconciliation finds no live session behind
   them and no exit was ever reported, **Then** those items become `interrupted` and are surfaced
   for a human decision.
4. **Given** a live worker process whose working directory is one of the daemon's isolated
   checkouts but which matches no `active` work item, **When** reconciliation runs, **Then** it is
   reported loudly as an orphan rather than left running unaccounted for.
5. **Given** a work item stuck in `dispatching` past its maximum age, **When** reconciliation runs,
   **Then** it becomes `failed` with the preparation output captured.
6. **Given** interrupted work items, **When** the author reviews them, **Then** the system never
   resumes any of them automatically; resuming, abandoning, or restarting is always an explicit
   human action.
7. **Given** an interrupted item the author is deciding about, **When** they inspect it, **Then**
   they can see whether the checkout has uncommitted changes, whether the branch has commits,
   whether the issue is closed, and whether a pull request is open.
8. **Given** a reboot has occurred, **When** the daemon next starts, **Then** every previously active
   item is reconciled to `interrupted` without this being treated or reported as an error condition.

---

### User Story 4 - Try it without consequences (Priority: P4)

Before pointing the daemon at real repositories, and every time the author changes eligibility
rules or a repository's preparation steps, they want to see exactly what it would do — with a
choice of how far the simulation goes. Sometimes they want no side effects at all. Sometimes they
want real checkouts prepared so they can debug preparation steps, but no sessions launched.
Sometimes they want the whole thing running for real except that nothing is written back to GitHub.

**Why this priority**: The system's whole purpose is taking consequential actions on the author's
behalf. Being able to watch it decide without letting it act is what makes it safe to iterate on.
It ranks below the recovery stories only because it has no value until there is a real loop to
simulate.

**Independent Test**: Run the daemon at each effect level against real repositories with eligible
issues, and confirm that exactly the effects that level permits occurred and no others.

**Acceptance Scenarios**:

1. **Given** any effect level below fully live, **When** the daemon runs, **Then** polling and
   eligibility evaluation are performed for real against the real source, never simulated.
2. **Given** the most restricted effect level, **When** an eligible item is found, **Then** the
   intended checkout preparation, session launch, and source-system writes are each reported as
   intentions and none are performed.
3. **Given** an intermediate effect level that permits local work only, **When** an eligible item is
   found, **Then** the checkout and its preparation steps are really performed and no session is
   launched and nothing is written back to the source.
4. **Given** an intermediate effect level that permits sessions but not source writes, **When** an
   eligible item is dispatched, **Then** a real session runs and no comment, label change, or other
   modification reaches the source system.
5. **Given** simulated work items, **When** they are persisted, **Then** they are written to the same
   store as live items, marked as simulated, and observed advancing through the same states by the
   same code path.
6. **Given** a simulated item that caused a real session to be launched, **When** concurrency is
   evaluated or sessions are reconciled, **Then** it is counted and reconciled exactly as a live item
   is, because it consumed the same real resources.
7. **Given** a simulated item, **When** reconciliation asks whether the source item has been closed,
   **Then** that check is skipped, because there is no real source item to consult.
8. **Given** any effect level, **When** the daemon starts and while it runs, **Then** the active level
   is stated in the startup log, in the health signal, and in every listing of work items, and
   simulated items are visually distinguished rather than hidden.
9. **Given** an accumulation of simulated records, **When** the author chooses to clear them,
   **Then** a command removes them without touching live records.

---

### User Story 5 - Operate and inspect it entirely from a terminal (Priority: P5)

The author wants to start the daemon after logging in, ask it what it is doing, force a poll when
impatient, cancel a session, resume or abandon an interrupted item, and read the audit trail — all
from a shell, because until the web UI exists there is nothing else, and because the constitution
requires every capability to be reachable and observable from the terminal regardless.

**Why this priority**: Necessary for the system to be usable and debuggable at all, but it exposes
capabilities the earlier stories create rather than adding new ones.

**Independent Test**: Perform a full working day's operations — start, inspect, force poll, cancel,
resume, read the log — using only a shell, and confirm each command reports accurately and exits
non-zero on failure.

**Acceptance Scenarios**:

1. **Given** the daemon is not running, **When** the author starts it before the graphical
   environment is ready, **Then** it checks its preconditions, fails loudly with a clear reason, and
   exits non-zero rather than starting in a broken state.
2. **Given** a running daemon, **When** the author asks for status, **Then** they see the effect
   level, health, what is active, what is queued, what is interrupted, and what is blocked.
3. **Given** an active session, **When** the author cancels it from the terminal, **Then** exactly
   that session's process tree is stopped and no other session is affected.
4. **Given** an interrupted item, **When** the author chooses to resume it, **Then** a session is
   started that restores the prior session's context rather than starting from scratch.
5. **Given** any command, **When** it fails, **Then** it exits non-zero and explains why.
6. **Given** any completed activity, **When** the author reads the audit trail, **Then** they can
   determine what the system did, when, to what, and with what result, without re-running anything.

---

### User Story 6 - Notice when the daemon has died (Priority: P6)

The daemon is a long-running background process the author will forget about. If it dies, hangs, or
quietly stops polling, the author must find out from the system rather than by eventually noticing
that a labelled issue has been sitting untouched for three days.

**Why this priority**: The planning document calls this out as the specific failure mode that kills
systems of this class and explicitly refuses to treat it as a stretch goal. It is last only because
it is meaningless before there is a daemon to monitor.

**Independent Test**: Kill the daemon without a clean shutdown and confirm the staleness is
detectable within the configured window, both from the terminal and through the configured
notification channel.

**Acceptance Scenarios**:

1. **Given** a healthy running daemon, **When** it completes each polling and reconciliation cycle,
   **Then** it records a durable liveness signal including its effect level.
2. **Given** the daemon has died or stopped cycling, **When** the staleness threshold passes,
   **Then** the condition is detectable by a terminal command that exits non-zero, and is delivered
   to the configured external notification channel if one is set.
3. **Given** an anomaly the system can detect but not resolve — an orphaned session, an item stuck
   in dispatching, a session with no resumable transcript, a dispatch whose session identity did not
   match what was requested — **When** it occurs, **Then** it is surfaced as a named anomaly rather
   than swallowed or left implicit in the state.

---

### Edge Cases

- **A preparation step hangs forever rather than failing.** Observed in the research: a submodule
  fetch against a protocol GitHub disabled hangs indefinitely because the port is dropped rather
  than refused. Every preparation step must be bounded by a timeout, and exceeding it must fail the
  item visibly rather than wedging it.
- **The launch reports success but nothing started.** Observed three separate ways. Success of the
  launch call is never sufficient evidence that a session exists.
- **The session starts but is silently degraded.** Observed: an inherited environment marker
  disabled transcript recording, producing a session that looked perfect, exited zero, and could
  never be resumed.
- **The terminal that launched the session is not the environment the session inherits.** Sessions
  inherit the environment of the long-running terminal application, not the daemon's — so anything
  the session needs must be passed explicitly, and anything harmful the terminal carries must be
  neutralised.
- **The worker process outlives its supervising wrapper.** If the wrapper is killed uncleanly, the
  worker keeps running, reparented, while the system observes no session and no exit — meaning
  `interrupted` does not imply "nothing is running".
- **An issue is closed while its session is still live.** The work is done by the source's account
  but a process is still consuming resources; this must be reconciled, not ignored.
- **The label is removed after dispatch.** Eligibility is evaluated once at dispatch; later removal
  must not retroactively invalidate or duplicate an in-flight item.
- **A repository's isolated checkout contains uncommitted work.** It must never be removed
  automatically.
- **Two eligible issues arrive for the same repository at once.** Isolated checkouts and branches
  must not collide, even though per-repository concurrency policy is a later milestone.
- **The checkout directory is deleted out from under the system.** This is a detectable, distinct
  state and must be recognised rather than producing confusing downstream errors.
- **Repositories requiring several gigabytes of prepared dependencies.** Preparation is not free;
  a single prepared checkout was measured at roughly half a gigabyte.
- **The source system rate-limits or is unreachable.** Polling must back off with bounds and must
  never hang indefinitely, and the daemon must not treat a transport failure as "no eligible work".
- **The daemon is started twice.** A second instance must not race the first over the same state.
- **Identifying processes is ambiguous.** Matching by command line was found to be actively
  dangerous, producing both a wrong conclusion and an accidental kill during research; identity must
  come from stronger evidence, and process identifiers alone are insufficient because they are
  reused.

## Requirements *(mandatory)*

### Configuration & Onboarding

- **FR-001**: The system MUST operate only on repositories the author has explicitly onboarded.
  Discovery of a repository MUST NOT by itself make it eligible for dispatch.
- **FR-002**: Onboarding a repository MUST record the location of its primary local checkout, its
  base branch, its optional preparation steps with per-step timeouts, and its optional per-repository
  worker configuration overrides.
- **FR-003**: The system MUST verify at dispatch time that the repository's primary local checkout
  is already trusted by the worker, and MUST fail the item visibly if it is not, rather than
  launching a session that would block invisibly on a trust prompt.
- **FR-004**: The system MUST record, at onboarding, a fingerprint of any tool-permission settings
  the repository has committed to version control, and MUST re-check that fingerprint at dispatch.
  A change MUST block dispatch and require explicit human re-approval.
- **FR-005**: Configuration MUST be readable and editable as human-inspectable local files, and
  secrets MUST come from environment variables or git-ignored local files and MUST NOT appear in
  logs.

### Polling & Eligibility

- **FR-006**: The system MUST poll the configured GitHub repositories on a configurable interval for
  issues carrying the configured dispatch label.
- **FR-007**: An issue MUST be considered eligible only if all of the following hold: it is authored
  by the configured author, it carries the dispatch label, its repository is onboarded, and it has
  not already been dispatched. The author check is a security boundary and MUST NOT be configurable
  away.
- **FR-008**: Every polling cycle MUST bound its network calls with explicit timeouts and MUST back
  off with bounded retries on rate limiting or transport failure. A failed poll MUST be logged
  distinctly from a successful poll that found nothing.
- **FR-009**: The system MUST record, for each item it evaluated and rejected, which eligibility
  condition failed.
- **FR-010**: The transition from an eligible issue to a dispatched session MUST require no further
  human action beyond having applied the label; the label application is itself the human gate.

### Checkout Preparation

- **FR-011**: The system MUST prepare each work item in an isolated checkout derived from the
  repository's existing primary local checkout, and MUST NOT modify that primary checkout.
- **FR-012**: The isolated checkout MUST be created on a new branch, from the configured base branch,
  fetched immediately beforehand, using a deterministic naming convention derived from the work item.
- **FR-013**: Each configured preparation step MUST run to completion inside the prepared checkout
  before any session is launched, and MUST be bounded by a per-step timeout with a documented
  default.
- **FR-014**: A preparation step that times out or exits non-zero MUST place the work item in
  `failed` with its output captured. A session MUST NEVER be launched into a partially prepared
  checkout.
- **FR-015**: Preparation MUST support both running a command and placing a file (by link or copy)
  from the primary checkout into the isolated one.
- **FR-016**: The system MUST NOT automatically remove any isolated checkout in this milestone, and
  MUST NEVER remove one containing uncommitted or untracked changes without an explicit human
  decision. A manual removal command MUST be available, and it MUST remove both the checkout and its
  branch or clearly report that it did not.
- **FR-017**: The system MUST detect isolated checkouts whose directory no longer exists and surface
  them as a distinct recoverable condition.

### Dispatch

- **FR-018**: The system MUST launch each session as a real, interactive terminal session inside the
  terminal instance already running on the author's desk. Headless or background-only execution MUST
  NOT be used to satisfy this requirement.
- **FR-019**: The system MUST locate the running terminal instance by probing candidates rather than
  assuming a fixed address, MUST tolerate the author having restarted it, and MUST bound each probe
  so that a stale candidate cannot cause a hang.
- **FR-020**: The system MUST generate each session's identifier itself and persist it before the
  session process starts, so that the identifier is known even if the process dies immediately.
- **FR-021**: Sessions MUST be hosted such that they survive the death of the terminal displaying
  them, remain reattachable from a newly started terminal, and support more than one simultaneous
  viewer.
- **FR-022**: The system MUST pass explicitly into each session every environment value that session
  requires, MUST neutralise inherited environment values known to degrade the session, and MUST NOT
  assume the session inherits the daemon's own environment.
- **FR-023**: The system MUST seed each session with the issue's title, body, and label context, and
  MUST launch the worker in its normal context-loading mode rather than any reduced mode that would
  skip repository-level instructions, hooks, or tooling.
- **FR-024**: Each session MUST be named identifiably by source and work item identifier in every
  place the worker exposes a name, and MUST be tagged with a stable key that allows the system to
  find its terminal window again by exact lookup rather than by inspecting command lines.
- **FR-025**: The system MUST NOT mark a work item `active` on the strength of the launch call
  returning success. It MUST confirm by at least one independent observation that the session
  process exists and carries the identifier the system generated, within a bounded confirmation
  window, and MUST fail the dispatch if confirmation does not arrive.
- **FR-026**: The system MUST validate every generated path and generated configuration file before
  launch, because the worker is known to accept some invalid values silently and exit successfully.
- **FR-027**: A failed launch MUST leave diagnosable evidence rather than a window that disappears
  instantly.
- **FR-028**: The system MUST enforce a configurable maximum number of concurrently running sessions
  it owns, and MUST hold further eligible items in `ready` when at capacity. Counting the author's
  own unrelated sessions and per-repository limits are deferred to a later milestone.

### State & Outcome

- **FR-029**: The system MUST track work item state and session state as two separate related
  records, and MUST NOT infer work completion from session behaviour.
- **FR-030**: Work item state MUST be one of: `discovered`, `ready`, `dispatching`, `active`,
  `interrupted`, `awaiting_review`, `done`, `failed`, `abandoned`. (`needs_info` belongs to the
  Trello milestone and is not used here.)
- **FR-031**: Session state MUST be one of: `starting`, `running`, `exited_clean`, `exited_error`,
  `lost`.
- **FR-032**: The system MUST learn each session's exit status by being told, not by polling, and
  MUST distinguish an ordinary non-zero exit from a death by signal, recording the signal number
  separately.
- **FR-033**: Exit status MUST map to work item state as follows: a clean exit to `awaiting_review`;
  a worker-configuration error exit to `failed` with captured error output; a death by signal to
  `interrupted`; no exit ever reported to `interrupted` via reconciliation.
- **FR-034**: A clean exit with the source issue still open MUST be treated as a normal resting
  state, not an anomaly.
- **FR-035**: A work item whose source issue has been closed MUST become `done` regardless of its
  session state.
- **FR-036**: Every state transition MUST be durably recorded before it is acted upon, and MUST
  survive the process being killed at any point.

### Reconciliation & Recovery

- **FR-037**: On startup the system MUST reconcile all existing state before dispatching any new
  work, and MUST also reconcile on a recurring timer while running.
- **FR-038**: Reconciliation MUST, for every `active` item, determine whether its session is really
  alive using evidence stronger than a process identifier alone, since identifiers are reused.
- **FR-039**: Reconciliation MUST NEVER identify processes by matching against command lines.
- **FR-040**: Reconciliation MUST mark as `interrupted` any `active` item with no live session and no
  reported exit.
- **FR-041**: Reconciliation MUST mark as `failed` any item in `dispatching` older than a
  configurable maximum age, capturing whatever preparation output exists.
- **FR-042**: Reconciliation MUST resolve `awaiting_review` items whose source issue has closed to
  `done`.
- **FR-043**: Reconciliation MUST sweep for live worker processes whose working directory lies within
  the system's isolated checkouts but which match no `active` work item, and MUST report each as an
  orphan anomaly.
- **FR-044**: Reconciliation MUST prune its own stale connection endpoints and detect checkouts whose
  directories have been removed, probing rather than trusting the existence of any file.
- **FR-045**: A reboot MUST result in all previously active items being reconciled to `interrupted`,
  and this MUST NOT be reported as an error.
- **FR-046**: The system MUST NEVER resume an interrupted item automatically. Resume, abandon, and
  restart MUST each be an explicit human action.
- **FR-047**: Resuming an interrupted item MUST restore the prior session's context using the
  identifier the system generated at dispatch.
- **FR-048**: For each interrupted item, the system MUST be able to report whether its checkout has
  uncommitted changes, whether its branch has commits, whether its issue is closed, and whether a
  pull request is open, so the author can decide whether resuming is worthwhile.
- **FR-049**: Restarting or stopping the daemon MUST NOT kill any running session.
- **FR-050**: Stopping a single session MUST stop that session's entire process tree and no other
  session's.

### Effect Levels (Dry Run)

- **FR-051**: The system MUST support four graduated effect levels: intentions only; local
  preparation permitted; sessions permitted but no source writes; and fully live.
- **FR-052**: Polling and eligibility evaluation MUST be performed for real at every effect level.
- **FR-053**: Effect levels MUST be enforced at the boundary interfaces that touch the outside world
  — the source system, the worker, the session host, and version-control operations — and MUST NOT be
  enforced by conditional checks scattered through calling code. Any new outward-facing operation
  MUST go through such a boundary.
- **FR-054**: Simulated work items and sessions MUST be written to the same store as live ones,
  flagged as simulated, and MUST progress through the same states by the same code path.
- **FR-055**: The simulated flag MUST govern only outward-facing effects and reporting. It MUST NOT
  exempt an item from concurrency accounting or from session reconciliation, because a simulated item
  at the session-permitting level consumes real resources.
- **FR-056**: Queries MUST exclude simulated records by default; including them MUST be the explicit
  act. The default MUST be enforced structurally in the persistence layer rather than by remembering
  to filter at each call site.
- **FR-057**: The active effect level MUST be stated in the startup log, in the liveness signal, and
  in every listing of work items, and simulated items MUST be shown and visually distinguished rather
  than filtered out.
- **FR-058**: A command MUST exist to purge simulated records without affecting live records.

### Observability & Health

- **FR-059**: Every action that changes state outside the process — version-control operations,
  command execution, network requests, session launches, notifications — MUST be written to a
  durable, append-only, one-record-per-line structured log at the time it occurs, containing a UTC
  timestamp, originating component, action, target, parameters with secrets redacted, and outcome.
- **FR-060**: Irreversible or outward-facing actions MUST be logged before they are executed, not
  only after.
- **FR-061**: Silent failure MUST NOT occur. Every swallowed error, retry, and fallback MUST leave a
  record.
- **FR-062**: The log MUST be sufficient, alone and without re-running anything, to determine what
  the system did, when, to what, and with what result.
- **FR-063**: The system MUST record a durable liveness signal on each polling and reconciliation
  cycle, including its effect level.
- **FR-064**: Staleness of that liveness signal MUST be detectable by a terminal command that exits
  non-zero, and MUST be deliverable to an optional configured external notification channel.
- **FR-065**: The system MUST surface as named anomalies at minimum: an orphaned worker process, an
  item stuck in dispatching, a session that produced no resumable transcript, and a dispatch whose
  observed session identity did not match the one requested.

### Terminal Interface

- **FR-066**: Every capability of the system MUST be reachable and observable from the terminal. A
  graphical interface MUST NOT be a prerequisite for any function.
- **FR-067**: The daemon MUST verify its preconditions at startup — the terminal instance is
  reachable, the state store is usable, the configuration is valid — and MUST fail loudly and exit
  non-zero if started before its environment is ready.
- **FR-068**: Terminal commands MUST exist to: start and stop the daemon; show status including
  effect level, health, active, queued, interrupted, and blocked items; force an immediate poll;
  cancel a session; resume, abandon, or restart an interrupted item; remove an isolated checkout;
  purge simulated records; and read recent activity.
- **FR-069**: Every command MUST exit non-zero on failure and MUST explain the failure.
- **FR-070**: A second daemon instance MUST NOT run concurrently against the same state; an attempt
  MUST fail clearly.

### Data Integrity

- **FR-071**: Writes to persistent state MUST be atomic, such that a partially written state is never
  observable to a later run.
- **FR-072**: Restarting after an interruption at any point MUST NOT duplicate a work item, an
  isolated checkout, a branch, a session, or a source-system write.
- **FR-073**: The location and record format of all persistent state and logs MUST be documented.

### Key Entities

- **Work Item**: A unit of work sourced from a GitHub issue. Carries its source, source identifier,
  canonical URL, title, prompt body, target repository, isolated checkout path, branch, state,
  simulated flag, failure detail, and timestamps for each state transition.
- **Session**: A worker run attached to a work item. Carries the system-generated session
  identifier, process identity strong enough to survive identifier reuse, host and display handles,
  the opaque handle needed to stop its process tree, state, exit code, signal number, and start and
  end timestamps. A work item may have several sessions over its life through resume and restart.
- **Repository Configuration**: An onboarded repository. Carries its name, primary local checkout
  path, base branch, ordered preparation steps with timeouts, worker configuration overrides, the
  fingerprint of any committed tool-permission settings, and its onboarding approval record.
- **Isolated Checkout**: A prepared working directory for one work item. Carries its path, branch,
  source repository, creation time, preparation outcome, and current condition (present, dirty,
  missing).
- **Audit Record**: One durable line describing one outward-facing action or state transition, as
  specified in FR-059.
- **Anomaly**: A detected condition the system cannot resolve on its own, carrying its kind,
  the entity it concerns, when it was detected, and whether it has been acknowledged.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From applying the label, a session is running and usable within 2 minutes for a
  repository requiring no preparation steps, and within 10 minutes for one whose preparation steps
  complete inside their configured timeouts.
- **SC-002**: Across 20 consecutive dispatches, 100% of work items marked `active` have a verifiably
  live session behind them, and 0 are marked `active` on the strength of a launch call alone.
- **SC-003**: Every dispatch that does not succeed produces a work item in a non-active state and a
  log record naming the cause — 0 dispatches end in silence.
- **SC-004**: After the terminal emulator is killed, 100% of running sessions continue running and
  can be reattached to from a newly started terminal, and 0 work items are incorrectly marked lost.
- **SC-005**: After a daemon restart, 0 sessions are killed, and reconciliation completes before any
  new work is dispatched.
- **SC-006**: After a reboot or an unclean kill, one reconciliation pass brings 100% of work items to
  a state matching physical reality, with 0 duplicate work items, checkouts, branches, sessions, or
  source-system writes.
- **SC-007**: Every live worker process running in one of the system's isolated checkouts is either
  matched to an active work item or reported as an orphan within one reconciliation interval — 0 go
  unaccounted for.
- **SC-008**: At the intentions-only effect level, running against real repositories with eligible
  issues produces 0 source-system writes, 0 sessions, and 0 checkout modifications, while still
  reporting a complete and correct list of what it would have done.
- **SC-009**: At the local-preparation effect level, a repository's preparation steps can be
  debugged to a working state without a single session being launched.
- **SC-010**: The author can determine what the system did, when, to what, and with what result for
  any past work item, from the log alone, without re-running anything, in under 5 minutes.
- **SC-011**: A daemon that has died or stopped cycling is detectable within twice its configured
  cycle interval, both from the terminal and through the configured notification channel.
- **SC-012**: 100% of the operations the author needs during normal use are performable from a shell
  with no graphical interface present.
- **SC-013**: Preparation steps that hang are terminated at their configured timeout in 100% of
  cases, and 0 work items remain in `dispatching` past the configured maximum age after a
  reconciliation pass.
- **SC-014**: A repository whose committed tool-permission settings changed since onboarding is
  blocked from dispatch in 100% of cases until the author re-approves it.
- **SC-015**: The unit test suite passes, and the state machine, persistence and recovery logic, and
  external-input parsing each carry tests exercising their interruption and failure paths, not only
  their success paths.

## Assumptions

Defaults chosen where the planning documents left a value open or where a detail was unspecified.
Each is a starting value to be revised in use, not a finding.

- **Storage is SQLite at a documented local path**, not MariaDB as stated in planning §12. The
  planning choice conflicts with constitution Principle II and the Operating Constraints storage
  rule; the conflict was raised before work began and resolved in favour of the constitution. See
  [`docs/roadmap.md`](../../docs/roadmap.md).
- **Poll interval defaults to 60 seconds**, configurable, with bounded exponential backoff on rate
  limiting. Planning §4 left this open.
- **Branch naming defaults to a prefix, the issue number, and a short title slug**, e.g.
  `robot-army/issue-142-fix-the-thing`. Planning §6 left this open.
- **The global concurrency limit defaults to 2**, configurable. Planning §10 left the value open
  while settling the mechanism; the full policy including per-repository limits and accounting for
  the author's own unrelated sessions belongs to milestone 004.
- **The maximum age for `dispatching` defaults to 15 minutes**, configurable, chosen to exceed the
  sum of typical preparation timeouts with margin.
- **Isolated checkouts are never removed automatically in this milestone.** Planning §6 leaves the
  cleanup trigger undecided; manual removal is the safe default and the decision can be made with
  real usage data. Removal always covers both the checkout and its branch.
- **Simulated records are retained until explicitly purged**, not purged on startup. Planning §2
  left this open; retention preserves the inspection value that motivated the mode.
- **The health signal is a durable local heartbeat plus a terminal command that exits non-zero when
  stale, with an optional configured webhook** for external delivery. This satisfies the
  constitution's terminal-reachability rule without committing to a specific notification vendor;
  a generic webhook covers the services the planning document names.
- **The system writes a comment to the GitHub issue on dispatch and on dispatch failure.** These are
  the only source-system writes in this milestone, and they are what the effect levels suppress.
- **A single shared default preparation step** covers the common dependency-environment case, with
  per-repository overrides for the roughly fifteen repositories the research found to need bespoke
  handling. Repositories with no entry get no preparation steps.
- **The author's own repositories are enumerated from GitHub for the authenticated user**, and a
  configured list adds repositories the author does not own. Both still require explicit onboarding
  per FR-001.
- **The daemon is started manually after graphical login**, per planning §8. Automatic start at boot
  is not a requirement and is deliberately excluded, because it would place the daemon in an
  environment with no terminal instance to launch into.
- **The terminal control socket is protected by filesystem permissions for the operating-system
  user only.** Planning §16 leaves further hardening open; the operating-system user is the trust
  boundary per constitution Principle II.
- **The author is the only user**, on one Linux machine. No authentication, authorization, or
  multi-user concern is in scope.
- **Both external services and the worker are treated as available but unreliable** — every call is
  bounded by a timeout with bounded retries, and their unavailability degrades the system's activity
  rather than its correctness.

## Dependencies

- A running terminal instance on the author's desktop, started at graphical login, exposing a
  control interface the daemon can reach over a local socket.
- A session-persistence mechanism able to own a terminal session's controlling device independently
  of any viewer, so sessions survive terminal death and support reattachment and multiple viewers.
- The Claude Code worker, authenticated by the author's subscription. Orchestrated sessions share
  usage limits with the author's interactive sessions, which is why FR-028 exists.
- GitHub API access authenticated as the author.
- Existing local checkouts of the onboarded repositories, from which isolated checkouts are derived.
- The author's manual application of the dispatch label, which is the deliberate human gate and is
  not to be automated away.
