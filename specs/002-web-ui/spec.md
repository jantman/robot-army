# Feature Specification: Web UI & HTTP API

**Feature Branch**: `002-web-ui` *(not created — no `before_specify` git hook is configured in this project)*

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Milestone 2 on our roadmap"

**Scope note**: This is milestone 002 of the roadmap in [`docs/roadmap.md`](../../docs/roadmap.md), corresponding
to M2 in the planning document (§13, plus the audit-log view from §14 and the resume-decision
signals from §8). It is a **second front end onto operations milestone 001 already exposes**, not a
new set of capabilities — with one deliberate exception, pausing dispatch, which 001 does not have
and which is specified here for both interfaces. Trello, per-repo concurrency caps, priority modes,
out-of-band session accounting, and automatic worktree cleanup remain out of scope and belong to
milestones 003 and 004.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See what the daemon is doing, from the couch (Priority: P1)

The author is away from their desk. They open a page on their phone and, without typing a command
or opening a shell, see: which sessions are running and for how long, what is queued behind them,
what is interrupted and waiting for a decision, whether the daemon is actually alive, which effect
level it is running at, and anything it has flagged as an anomaly. Every work item links straight
out to the GitHub issue it came from.

**Why this priority**: This is the ergonomic point of the whole milestone and the reason the
planning document chose a web UI over a TUI. Milestone 001 made every one of these facts available,
but only to somebody sitting at a terminal. With only this story shipped — a read-only view — the
author already gets the thing they cannot get today, and nothing about the daemon's behaviour has
been put at risk to get it.

**Independent Test**: With sessions running, items queued, and at least one interrupted item, load
the interface on a phone-sized screen with no terminal available and confirm every one of those
facts is legible and correct without zooming or scrolling sideways.

**Acceptance Scenarios**:

1. **Given** running sessions, **When** the author opens the active view, **Then** each session shows
   its work item, repository, issue title, a link to the issue, its isolated checkout and branch,
   when it started, and how long it has been running.
2. **Given** eligible items held behind the concurrency limit, **When** the author opens the queue
   view, **Then** they see what is waiting, in the order it will be dispatched, and what is currently
   being prepared for dispatch.
3. **Given** items the system could not act on — a repository not onboarded, a permission
   fingerprint changed, a failed dispatch — **When** the author looks at the queue view, **Then**
   each is shown as blocked together with the specific reason it is blocked.
4. **Given** any view, **When** it is displayed, **Then** the current effect level and the freshness
   of the daemon's liveness signal are shown on it, not on a separate page.
5. **Given** the daemon has died or stopped cycling, **When** the author loads any view, **Then**
   the interface states prominently that the data may not reflect reality and how stale the liveness
   signal is, rather than presenting stale rows as current.
6. **Given** simulated work items exist, **When** any view is loaded, **Then** they are excluded by
   default, and including them is an explicit act that marks every simulated row visibly.
7. **Given** unacknowledged anomalies, **When** any view is loaded, **Then** their presence and count
   are visible without navigating to a dedicated page.
8. **Given** a view left open, **When** time passes, **Then** it refreshes itself on a bounded
   interval and displays how old the data on screen is.

---

### User Story 2 - Decide what to do with an interrupted item, away from the desk (Priority: P2)

A reboot, a crash, or a killed terminal has left work items interrupted. The author, on their phone,
looks at each one and sees the evidence they need to decide: does the checkout have uncommitted
changes, does the branch have commits, is the issue closed, is there an open pull request. Then they
resume it, restart it fresh, or abandon it — in one tap plus a confirmation, and never accidentally.

**Why this priority**: The planning document's resume policy is "never auto-resume, surface it and
let a human decide". That policy is only as good as the author's ability to actually make the
decision, and today that requires a terminal. This is the story that turns milestone 001's recovery
machinery into something the author uses on the day it matters instead of the day they next sit down.

**Independent Test**: Interrupt three sessions by different means, then from a phone alone resume
one, restart one, and abandon one, confirming each produced exactly the intended effect and the
other two items were untouched.

**Acceptance Scenarios**:

1. **Given** an interrupted item, **When** the author opens it, **Then** they see whether its
   checkout has uncommitted changes, whether its branch has commits, whether its issue is closed,
   and whether a pull request is open, each computed at the moment of viewing.
2. **Given** an interrupted item, **When** the author resumes it, **Then** a session is started that
   restores the prior session's context, and the item shows as active only once that session has
   been confirmed to exist.
3. **Given** an interrupted item, **When** the author restarts it, **Then** a fresh session with no
   prior context is started in the existing checkout as a new attempt.
4. **Given** an interrupted item, **When** the author abandons it, **Then** the item is marked
   abandoned and its checkout is left on disk untouched.
5. **Given** any of these actions, **When** the author selects it, **Then** the action is not
   performed until a confirmation step distinct from the initial tap is completed.
6. **Given** a view of an item that has since changed state — a session that has already been
   resumed from a terminal, or an issue that has since closed — **When** the author submits an action
   against the state they were shown, **Then** the action is refused with an explanation of what
   changed, and nothing is done.
7. **Given** a submitted action, **When** the same action is submitted again because of a double tap,
   a retried request, or a reloaded page, **Then** no second session, checkout, branch, or
   source-system write is produced.
8. **Given** an item the author is deciding about, **When** they want its history, **Then** they can
   see every session attempt it has had, with exit codes, signal numbers, and timestamps.

---

### User Story 3 - Take control when something is going wrong (Priority: P3)

Something is running that should not be, or nothing is running that should be. The author stops a
single runaway session without touching the others; pauses dispatch entirely so the daemon stops
starting new work while they investigate; forces an immediate poll or reconciliation rather than
waiting out the interval; and acknowledges an anomaly once they have dealt with it.

**Why this priority**: These are the levers that make the interface useful during an incident rather
than only during calm inspection. They rank below the recovery story because the situations that
need them are rarer than the daily interrupted-item decision, and because pausing dispatch is the
one genuinely new capability in this milestone rather than a second door onto an existing one.

**Independent Test**: With several sessions running, cancel exactly one and confirm the others
continue; pause dispatch and confirm an eligible item is held rather than dispatched; force a poll
and confirm it happened immediately; resume dispatch and confirm the held item is then dispatched.

**Acceptance Scenarios**:

1. **Given** several running sessions, **When** the author cancels one, **Then** that session's
   entire process tree stops, every other session keeps running, and the item becomes interrupted
   with its checkout untouched.
2. **Given** dispatch is running normally, **When** the author pauses it, **Then** no new session is
   started for any eligible item, polling and reconciliation continue, and eligible items accumulate
   in the queue rather than being rejected or lost.
3. **Given** dispatch is paused, **When** the daemon is restarted or the machine is rebooted,
   **Then** dispatch is still paused, because a pause that quietly lapses is worse than no pause.
4. **Given** dispatch is paused, **When** any view is loaded from either interface, **Then** the
   paused state is stated prominently, together with when it was paused.
5. **Given** dispatch is paused, **When** the author resumes it, **Then** queued eligible items begin
   dispatching again subject to the ordinary concurrency limit.
6. **Given** the author does not want to wait for the poll interval, **When** they force a poll or a
   reconciliation, **Then** it happens promptly and the interface reports what it found, including
   what it evaluated and rejected.
7. **Given** an unacknowledged anomaly, **When** the author acknowledges it, **Then** it stops being
   surfaced as outstanding while remaining in the record, and a genuinely new occurrence of the same
   kind is surfaced again later.
8. **Given** any control offered by the interface, **When** the author looks for it in the terminal,
   **Then** an equivalent terminal command exists, because a graphical interface is never a
   prerequisite for any capability.

---

### User Story 4 - Reconstruct what happened, with the links already made (Priority: P4)

Something happened yesterday and the author wants to know what. They open the audit view, filter to
the work item or the time window in question, and read the sequence of what the system did, when, to
what, and with what result — with GitHub repositories, issues, and pull requests rendered as links
they can follow rather than identifiers they have to reassemble by hand.

**Why this priority**: The record already exists and is already readable from the terminal; this
story adds convenience and reach rather than capability. It is above nothing only because the
planning document calls the clickable audit log out explicitly as part of this milestone, and
because reading a log on a phone through a shell is genuinely bad.

**Independent Test**: Pick a completed work item, and from the interface alone determine what
happened to it — every state transition, every outward-facing action, and the outcome of each —
without re-running anything and without opening a terminal.

**Acceptance Scenarios**:

1. **Given** past activity, **When** the author opens the audit view, **Then** they see records in
   reverse chronological order with timestamp, component, action, target, and outcome.
2. **Given** the audit view, **When** the author filters by work item, by time window, or by outcome,
   **Then** only matching records are shown and the active filter is visible.
3. **Given** a record concerning a GitHub issue, repository, or pull request, **When** it is
   displayed, **Then** those are rendered as links that open the corresponding page.
4. **Given** a record that was written by the web interface rather than by the daemon or a terminal
   command, **When** it is displayed, **Then** its originating component says so.
5. **Given** an audit file with a partially written final line, **When** the audit view reads it,
   **Then** it displays the records it could parse and reports the number it could not, rather than
   failing to display anything.
6. **Given** a large volume of records, **When** the audit view is opened, **Then** it presents a
   bounded, paged extent rather than attempting to render the entire history.

---

### User Story 5 - Sit down and take over a session (Priority: P5)

The author walks back to their desk, where the terminal is. From the interface they open a running
session in a terminal window on that desktop and take over driving it directly, rather than hunting
for which window it is in or reconstructing the command to attach.

**Why this priority**: Genuinely convenient and, per the planning research, cheap — session
reattachment was measured as working, repainting, and tolerating more than one simultaneous viewer.
It is last because it is the only story that requires the author to be physically at the machine,
which is precisely the situation the rest of this milestone exists to avoid, and because Remote
Control already gives the phone a way to drive a session.

**Independent Test**: With a session running and the terminal instance available, use the interface
to attach to it, and confirm a window appears showing that session's live state with the session
still running afterwards.

**Acceptance Scenarios**:

1. **Given** a running session and a reachable terminal instance, **When** the author attaches from
   the interface, **Then** a terminal window on the desktop shows that session, fully repainted, and
   the session continues running.
2. **Given** a running session already being viewed in a window, **When** the author attaches again,
   **Then** both viewers work rather than one displacing or killing the other.
3. **Given** no reachable terminal instance, **When** the author attempts to attach, **Then** the
   attempt fails visibly with the reason, and nothing about the session changes.
4. **Given** a session that is not running, **When** the author views it, **Then** no attach control
   is offered for it.

---

### Edge Cases

- **The view on screen is minutes old when the action is submitted.** Phones sleep, pages sit open,
  and the terminal is acting on the same state concurrently. Every action must be evaluated against
  state read at submission time, not against the state the page was rendered from.
- **The same action arrives twice.** Double taps, browser retries, and reloaded submissions are
  normal on a phone. A second arrival must not produce a second session, checkout, branch, or
  source-system write.
- **The daemon is not running when the interface is used.** Data is still readable, but describing it
  as current would be a lie, and any control that needs the daemon must fail with that reason rather
  than appearing to work.
- **The daemon dies while a page is open.** The next refresh must change what the page claims, not
  keep rendering the last good state indefinitely.
- **An action is submitted against an item that no longer exists** — purged simulated rows, or an
  identifier typed into the address bar by hand. It must produce a clear not-found response, not an
  unhandled error.
- **The interface is opened while the machine has no internet connection.** The phone is on the same
  local network and the daemon is local; every part of the interface that does not describe GitHub
  must work. Nothing may depend on fetching assets from an external service.
- **The listening port is already in use** when the interface starts. It must fail loudly and exit
  non-zero rather than silently not listening.
- **A request is interrupted halfway** by the phone losing signal or the server being killed. No
  partially applied state may be observable afterwards.
- **An action is taken on an item whose session ends during the request.** The outcome must be
  recorded accurately for what actually happened, not for what was intended.
- **The audit log is very large, or spans many daily files.** Reading it must be bounded in both time
  and memory rather than proportional to total history.
- **Simulated items are present.** They must be excluded by default and unmistakably marked when
  shown, exactly as in the terminal interface — a simulated row misread as live is the failure mode
  this rule exists to prevent.
- **Secrets appear in configuration and in some records.** Nothing served to the browser may contain
  a token, and redaction must not depend on the browser to enforce it.
- **The interface is reached from a device other than the author's phone**, because it is listening
  on a network interface. Whatever exposure model is chosen must make that either impossible or
  harmless.
- **A control is offered for an item in a state where it makes no sense** — resuming an active item,
  cancelling one that is not running. The interface must not offer it, and must still refuse it if
  submitted anyway.

## Requirements *(mandatory)*

### Interface & Access

- **FR-001**: The system MUST serve a browser-reachable interface over HTTP from the local machine,
  backed by an HTTP API that the interface itself consumes.
- **FR-002**: The listening address and port MUST be configurable, and MUST default to the loopback
  address. Serving on the local network per FR-003 MUST be an explicit configuration act, so that an
  unconfigured installation is not reachable by anything but the machine it runs on.
- **FR-003**: The interface MUST be servable on the author's local network, and MUST NOT implement
  any in-application access control. The trust boundary is the local network, extended to the
  author's phone when away from home by the author's existing virtual private network. No accounts,
  credentials, tokens, or roles are to be built, per constitution Principle II.
- **FR-004**: Because FR-003 places full control of the system behind nothing but network
  reachability, the interface MUST state the address and port it is actually listening on, loudly, at
  startup and in the audit log. It MUST NOT be reachable from a public address, and MUST NOT assume
  or configure a reverse proxy, tunnel, or port forward on the author's behalf.
- **FR-005**: The interface MUST be startable and stoppable independently of the daemon, and MUST
  keep serving its read-only views while the daemon is not running, so that history and interrupted
  items remain readable during exactly the incident that makes them worth reading. It MUST make the
  daemon's absence unmistakable rather than presenting stale data as current, and any control that
  requires a running daemon MUST fail with that reason rather than appearing to work.
- **FR-006**: Every capability the interface offers MUST have an equivalent terminal command, and the
  interface MUST NOT be a prerequisite for any capability of the system.
- **FR-007**: The interface MUST render usably on a phone-sized viewport: a single column, no
  horizontal scrolling, and touch targets large enough to hit without zooming.
- **FR-008**: The interface MUST NOT require any resource fetched from a third-party network service
  at load time. It MUST work with the machine offline and the phone on the local network only.
- **FR-009**: The HTTP API MUST NOT be treated as a stable public interface. No versioning,
  deprecation cycle, or compatibility shim is to be maintained for consumers outside this repository.
- **FR-010**: The interface MUST verify its preconditions at startup — its listening address is
  bindable, its configuration is valid, the state store is readable — and MUST fail loudly and exit
  non-zero if they are not met.

### Views

- **FR-011**: The interface MUST provide a view of active work items showing, for each: work item
  identifier, repository, issue number and title, a link to the issue, isolated checkout path,
  branch, session start time, elapsed running time, and current session state.
- **FR-012**: The interface MUST provide a view of queued work items showing what is ready, in the
  order it will be dispatched, and what is currently being prepared for dispatch.
- **FR-013**: The interface MUST show items that cannot proceed — repository not onboarded, committed
  permission fingerprint changed, dispatch failed — together with the specific blocking reason for
  each.
- **FR-014**: The interface MUST provide a view of interrupted items which, for each item, reports
  whether its checkout has uncommitted changes, whether its branch has commits, whether its issue is
  closed, and whether a pull request is open. These signals MUST be computed when the view is
  requested and MUST NOT be served from a stored copy.
- **FR-015**: The interface MUST provide a detail view for a single work item showing its source
  links, its full state history with timestamps, and every session attempt with exit code, signal
  number, and start and end times.
- **FR-016**: The interface MUST display the active effect level and the age of the daemon's liveness
  signal on every view.
- **FR-017**: The interface MUST surface unacknowledged anomalies and their count from every view,
  and MUST provide a view listing them with enough detail to act on.
- **FR-018**: Views MUST refresh on a bounded interval without the author acting, and MUST display
  how old the data being shown is.
- **FR-019**: Simulated records MUST be excluded from every view by default. Including them MUST be
  an explicit act, and every simulated record MUST be visibly marked wherever it is shown.
- **FR-020**: No response served by the interface may contain a credential, token, or other secret,
  whether in page content, API payloads, or error messages.

### Controls

- **FR-021**: The interface MUST offer, for items in the appropriate state: resume, restart, abandon,
  and cancel. Each MUST have the same effect and the same restrictions as its terminal equivalent.
- **FR-022**: The interface MUST offer a control to move a failed item back to the queue, which MUST
  refuse with the reason if the condition that blocked it still holds.
- **FR-023**: The interface MUST offer controls to force an immediate poll and an immediate
  reconciliation, and MUST report what each found, including what was evaluated and rejected.
- **FR-024**: The interface MUST offer a control to acknowledge an anomaly.
- **FR-025**: The interface MUST offer a control to attach a terminal window to a running session,
  MUST offer it only for sessions that are running, and MUST report the reason visibly if attaching
  fails. Attaching MUST NOT alter the session's state, and MUST tolerate the session already having
  a viewer.
- **FR-026**: Every action that stops, starts, or discards work MUST require a confirmation step
  distinct from the control that initiates it.
- **FR-027**: Every action MUST be evaluated against state read at the moment of submission, and MUST
  be refused with an explanation of what changed if the item is no longer in a state where the action
  is valid.
- **FR-028**: Submitting the same action more than once MUST NOT produce a second session, checkout,
  branch, or source-system write.
- **FR-029**: A control MUST NOT be offered for an item in a state where it is not valid, and
  MUST still be refused if submitted anyway.
- **FR-030**: The interface MUST NOT offer repository onboarding or re-approval of changed committed
  permissions. That decision requires reviewing settings content in full and remains a deliberate
  terminal-only step.
- **FR-031**: The interface MUST NOT offer removal of an isolated checkout or its branch. Abandoning
  an item MUST remain non-destructive.
- **FR-032**: Adjusting the concurrency limit is deferred to milestone 004 and MUST NOT be offered
  here.

### Pausing Dispatch

- **FR-033**: The system MUST support pausing dispatch: while paused, no new session is started for
  any work item, while polling, eligibility evaluation, reconciliation, and the liveness signal
  continue unchanged.
- **FR-034**: Eligible items arriving while dispatch is paused MUST accumulate as queued work, and
  MUST NOT be rejected, failed, or lost.
- **FR-035**: The paused state MUST be durable: it MUST survive a daemon restart and a reboot, and
  MUST be cleared only by an explicit human action.
- **FR-036**: The paused state, and when it was set, MUST be shown in every listing of work items in
  both the web and terminal interfaces, and MUST be included in the liveness signal.
- **FR-037**: Pausing and resuming dispatch MUST each be available as a terminal command as well as
  in the web interface, and each MUST be recorded in the audit log.

### Accountability

- **FR-038**: Every request that changes state MUST be recorded in the audit log before the action is
  executed and again with its outcome, following the existing record format.
- **FR-039**: Audit records originating from the web interface MUST identify it as their originating
  component, distinctly from the daemon and from terminal commands.
- **FR-040**: A request that fails MUST leave a record explaining why. An error response with no
  corresponding log record is a silent failure and MUST NOT occur.
- **FR-041**: Read-only requests are exempt from individual audit records, because they change no
  state outside the process. This exemption MUST be documented in the implementation plan as
  Principle III requires.
- **FR-042**: The interface MUST provide a view of the audit log with filtering by work item, by
  time window, and by outcome, presented newest first and bounded in extent.
- **FR-043**: The audit view MUST render GitHub repositories, issues, and pull requests as followable
  links.
- **FR-044**: The audit view MUST tolerate an unparseable final record, displaying what it could read
  and reporting the number of records it could not.

### Data Integrity & Interruption

- **FR-045**: The interface MUST hold no authoritative state of its own. Everything it displays MUST
  be derived from the existing state store, log, and live observation, so that killing it loses
  nothing.
- **FR-046**: A request interrupted at any point MUST NOT leave partially applied state observable to
  a later request.
- **FR-047**: Every action the interface performs MUST reuse the operations milestone 001 already
  implements rather than reimplementing their logic, so that the two front ends cannot diverge in
  behaviour.
- **FR-048**: Concurrent action from the terminal and the web interface against the same item MUST
  NOT corrupt state; one MUST succeed and the other MUST be refused with an explanation.
- **FR-049**: Reading a view MUST NOT block the daemon's own work, and MUST NOT require taking the
  daemon's single-instance lock.

### Key Entities

- **Dispatch Pause State**: Whether dispatch is currently suspended, when it was suspended, and
  through which interface. Durable, single-valued for the whole system, and read by the daemon before
  each dispatch decision.
- **Work Item, Session, Repository Configuration, Isolated Checkout, Audit Record, Anomaly**: All as
  defined in milestone 001. This milestone reads and acts on them; it introduces no new fields on
  them beyond what the pause state requires.
- **View Snapshot**: The set of facts rendered on a page together with the time they were read. Not
  persisted — it exists as a concept because every action must be re-validated against current state
  rather than against the snapshot it was initiated from.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From an unlocked phone, the author can determine what is running, what is queued, what
  is interrupted, and whether the daemon is alive, in under 15 seconds and without opening a terminal.
- **SC-002**: 100% of the decisions needed for an interrupted item — resume, restart, or abandon —
  can be made and executed from a phone, including seeing all four resume-decision signals, with no
  terminal involved.
- **SC-003**: Across 20 repeated or double submissions of the same action, 0 produce a second
  session, checkout, branch, or source-system write.
- **SC-004**: 100% of actions submitted against a work item whose state changed after the page was
  rendered are refused with an explanation, and 0 are applied to the wrong state.
- **SC-005**: Every action taken through the web interface is reconstructable from the audit log
  alone — what was done, by which interface, when, to what, and with what result — in 0 cases
  requiring the page to be re-opened or the action re-run.
- **SC-006**: With dispatch paused, 0 sessions are started across at least one full poll interval
  with eligible items present, and 100% of those items are still queued and dispatchable when
  dispatch is resumed.
- **SC-007**: A pause survives a daemon restart and a reboot in 100% of cases.
- **SC-008**: Cancelling one session from the interface stops exactly that session's process tree and
  affects 0 other running sessions.
- **SC-009**: With the machine's internet connection removed, every view except the content of
  external links loads and functions.
- **SC-010**: With the daemon stopped, the interface's read-only views still report the historical
  state and state the daemon's absence in 100% of loads, and 0 loads present stale data as current.
- **SC-011**: Every control present in the web interface has a terminal equivalent — 100%, verified
  by enumeration.
- **SC-012**: 0 responses served by the interface contain a credential or token, verified across all
  views and API responses including error paths.
- **SC-013**: On a 390-pixel-wide viewport, every view renders with 0 horizontal scrolling and no
  text requiring zoom to read.
- **SC-014**: The audit view returns a bounded page of records in under 2 seconds against a log of at
  least 100,000 records.
- **SC-015**: The address and port the interface is actually listening on appear in its startup
  output and in the audit log in 100% of starts, including the starts where they came from
  configuration rather than the default.
- **SC-016**: The unit test suite passes, and the request handling, action re-validation, and pause
  persistence each carry tests exercising their failure and interruption paths, not only their
  success paths.

## Assumptions

Defaults chosen where the planning documents left a detail open. Each is a starting point to be
revised in use, not a finding.

- **The interface is a second front end onto milestone 001's operations layer**, which was built for
  exactly this. No dispatch, reconciliation, or state-transition logic is reimplemented here; where
  an operation is missing — pausing dispatch — it is added to that layer and exposed through both
  interfaces at once.
- **Attaching means opening a terminal window on the author's desktop**, using the reattachment
  mechanism the research measured as working, repainting, and multi-viewer tolerant. Rendering a
  terminal inside the browser is deliberately not built.
- **Session transcripts are not displayed in the browser.** Sessions run with Remote Control enabled,
  so the author's phone already has a way to watch and drive a session; duplicating that would be
  scope with no beneficiary.
- **Views refresh by re-requesting on an interval**, not by a persistent push connection. The data
  changes on the order of a poll interval, and a polling refresh has fewer moving parts.
- **The refresh interval defaults to 10 seconds**, configurable, chosen to feel live without making
  a phone left open overnight a meaningful load.
- **The audit view pages from newest to oldest** with a bounded default page size, reading only the
  files needed to fill a page rather than the whole history.
- **Confirmation is a second explicit interaction** — a distinct confirm control naming the item and
  the action — rather than a typed phrase. Typed confirmations do not survive contact with a phone
  keyboard; the destructive operation that warranted one in milestone 001, removing a checkout, is
  deliberately not offered here.
- **Time is displayed as both absolute UTC and relative age.** The record format is UTC throughout,
  and relative age is what makes a stale liveness signal obvious at a glance.
- **No server-side session, cookie, or user state is kept.** There is one user and the interface is
  stateless between requests, which is also what makes killing it lossless.
- **The interface is started manually and separately from the daemon**, and after graphical login
  for the same reason the daemon is: attaching a session needs the desktop's terminal instance to
  exist. Running it as its own command is what makes FR-005 possible — the daemon can be stopped,
  upgraded, or dead while the interface is still serving history.
- **The interface binds to the author's local network address by configuration**, with loopback as
  the shipped default. Remote access is the author's existing virtual private network reaching that
  same local address; nothing is exposed publicly and no tunnel or proxy is configured by this
  system.
- **Anything that can reach the port has full control of the system.** That is the accepted
  consequence of FR-003 and the reason FR-004 requires the effective bind address to be stated
  loudly at startup — the one thing that must never be silent is which network the interface just
  became reachable from.
- **The author is the only user.** No accounts, roles, or per-user views exist. FR-003 is about
  network reachability, not identity, and deliberately builds no identity concept at all.
- **GitHub links are constructed from data already stored** — repository, issue number, branch — and
  no additional source-system calls are made to render a view, so a view stays fast and does not
  consume rate limit.
- **The pause state lives in the existing state store** alongside the data it governs, so that it is
  atomic and survives interruption by the same mechanism everything else does.

## Dependencies

- Milestone 001, complete: the state store, the audit log, the liveness signal, the boundary
  interfaces, and the operations layer that both front ends call.
- A browser on the author's phone and desktop. No specific browser, extension, or installed
  application is required.
- Local network reachability between the phone and the machine — directly when at home, and through
  the author's existing virtual private network when away. That network is a dependency of this
  milestone, not something it provides.
- For attaching only: the terminal instance running on the author's desktop and reachable over its
  control socket, exactly as milestone 001 requires for dispatch.
