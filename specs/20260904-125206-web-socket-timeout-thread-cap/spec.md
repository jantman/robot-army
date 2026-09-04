# Feature Specification: Bounded waits and bounded concurrency for the web interface

**Feature Branch**: `speckit/20260904-125206-web-socket-timeout-thread-cap`

**Created**: 2026-09-04

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#123 — "RA-13: web server has no socket timeout and unbounded threads, each holding a DB connection" (label: bug). The web interface accepts keep-alive connections, never bounds how long it will wait for a client to say something, and places no ceiling on how many connections it will serve at once. Each served request opens its own database connection and audit-log file handle, so a client that opens connections and then goes quiet consumes the operator's file descriptors until the interface can no longer render.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The interface survives connections that go quiet (Priority: P1)

The operator has the web interface running. Something on the network — a page in another
browser tab, a stalled `curl`, a port scanner, a script — opens connections to the interface
and then sends nothing, or sends the beginning of a request and stops, or announces a body it
never finishes sending. The operator opens the interface and it renders normally. The quiet
connections are given up on and their resources returned.

**Why this priority**: This is the whole of the reported defect's cheapest exploit and the
only one an accident can trigger. A single wedged connection today is permanent; the
interface exists to be readable during an incident, and the fix that makes it readable is
worth more than any refinement of the fix.

**Independent Test**: Start the interface, open a connection, send a partial request or
nothing at all, and observe that the connection is closed on its own within the bounded wait
and that a normal request served afterward succeeds. Delivers the whole of the slowloris
defence without any other part of this feature.

**Acceptance Scenarios**:

1. **Given** the interface is running, **When** a client connects and sends no bytes at all,
   **Then** the interface closes the connection within the bounded wait and releases the
   resources it held.
2. **Given** the interface is running, **When** a client sends a partial request line and
   then stops, **Then** the interface closes the connection within the bounded wait.
3. **Given** the interface is running, **When** a client sends a complete request declaring a
   body of N bytes and then sends fewer than N bytes, **Then** the interface stops waiting
   within the bounded wait and closes the connection rather than holding it open.
4. **Given** a client has completed a request over a keep-alive connection, **When** it sends
   no follow-up request, **Then** the interface closes the idle connection within the bounded
   wait rather than holding it indefinitely.
5. **Given** the interface is running, **When** a request is served normally, **Then** the
   response is identical to what it was before this feature: bounded waiting changes only
   how long a silent client is tolerated, never the content of any response.

---

### User Story 2 - A flood is refused rather than absorbed (Priority: P2)

Something opens far more simultaneous connections than the operator would ever open — enough
that, one file descriptor and one database connection each, they would exhaust the process's
descriptors. Instead of accepting all of them, the interface serves a bounded number at once
and turns the rest away immediately with an explicit "not now" that closes the connection.
The operator's own request is served, or fails immediately and visibly, rather than hanging.

**Why this priority**: Bounded waiting alone leaves a determined client able to hold
connections by dribbling bytes just often enough to reset the wait. The cap is what makes the
resource ceiling a fact rather than a race. It ranks below P1 because P1 closes the accidental
and low-effort cases, which are the ones that actually occur.

**Independent Test**: With the cap set low for the test, open more simultaneous connections
than the cap and confirm that connections beyond the cap receive an immediate refusal that
closes the connection, while connections within the cap are served normally.

**Acceptance Scenarios**:

1. **Given** the interface is already serving its maximum number of simultaneous connections,
   **When** another connection is opened and sends a request, **Then** it receives an
   immediate, well-formed refusal indicating the service is temporarily unable to handle it,
   and the connection is closed.
2. **Given** the interface refused a connection because it was at capacity, **When** one of
   the in-flight connections finishes, **Then** a newly opened connection is served normally.
3. **Given** the interface is at capacity, **When** it refuses a connection, **Then** no
   database connection and no audit-log file handle are opened on behalf of the refused
   connection.
4. **Given** the number of simultaneous connections the interface will serve is capped at N,
   **When** any number of clients connect, **Then** the number of database connections and
   audit-log file handles the interface holds open at once never exceeds what N simultaneous
   requests require.

---

### User Story 3 - The operator can tell that it happened (Priority: P3)

After an episode in which the interface turned connections away, the operator can determine
from the durable record and the terminal that it happened and roughly how much, without
having been watching at the time.

**Why this priority**: A defence that acts silently is indistinguishable from a bug when the
operator later finds a page that failed to load. It ranks last because the interface is
correct without it, and because the record must not itself become the amplifier — the
accounting has to be cheap enough that a flood cannot turn it into the attack.

**Independent Test**: Drive the interface past its cap, stop it, and read back both the
terminal output and the durable record to confirm that saturation is reported and the number
of refused connections is recorded.

**Acceptance Scenarios**:

1. **Given** the interface has been running below capacity, **When** it first reaches
   capacity and refuses a connection, **Then** it reports on the terminal that it is at
   capacity and refusing connections.
2. **Given** the interface refused some number of connections during its run, **When** it is
   stopped, **Then** the durable record of the stop carries the total number of connections
   refused for capacity.
3. **Given** the interface refuses a large number of connections in quick succession,
   **When** the record is examined, **Then** the volume of records written is bounded by the
   number of saturation episodes rather than by the number of refused connections.

---

### Edge Cases

- **A slow but legitimate response.** A view that takes many seconds to compute — recomputing
  resume signals across several worktrees, reading whole audit files — MUST still complete.
  The bound is on waiting for the *client*, not on the interface's own work.
- **A large response to a slow reader.** A client that reads its response slowly but steadily
  MUST still receive it. Only silence past the bound ends the connection.
- **The interface's own auto-refresh.** The page refreshes itself on a timer; its connections
  MUST NOT be treated as an attack, and ordinary use with several browser tabs open MUST NOT
  reach the cap.
- **A request already in flight when capacity is reached.** Reaching the cap MUST NOT disturb
  connections already being served; the cap governs admission only.
- **A body declared but never sent, on a body larger than the accepted maximum.** The existing
  behaviour — refuse with "too large" and close rather than drain — MUST be preserved; the new
  bound applies to the wait, not to the size decision.
- **Shutdown while at capacity.** A signalled stop MUST still stop accepting and write its
  stop record, and the cap MUST NOT prevent shutdown from completing. It MUST NOT be read as
  requiring in-flight requests to be finished: they are served on daemon threads that are not
  joined and that die with the process, which is the existing and intended behaviour. What
  changes is that the bound in FR-001 now puts a ceiling on how long one of them can be held
  open by a client that has stopped talking.
- **The bound is reached during shutdown accounting.** The refusal path MUST NOT require any
  resource that the flood it is defending against has exhausted.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The web interface MUST bound how long it will wait for a client to send bytes on
  an accepted connection. When the bound elapses with nothing received, the interface MUST
  close the connection and release every resource held for it.
- **FR-002**: The bound in FR-001 MUST apply to every point at which the interface waits on a
  client: the first request line, the request headers, the request body, and the wait for a
  follow-up request on a kept-alive connection.
- **FR-003**: A connection ending — because the bound in FR-001 elapsed, or because the client
  disappeared — MUST NOT produce a traceback on the terminal and MUST NOT be reported as an
  error of the interface. Any other failure while serving a request MUST still be reported
  exactly as it is today; this is a narrowing to connection-lifecycle events, not a general
  silencing.
- **FR-004**: The web interface MUST cap the number of client connections it serves
  simultaneously at a fixed maximum.
- **FR-005**: A connection arriving when the interface is at the cap MUST receive an immediate,
  well-formed HTTP refusal carrying the "service unavailable" status and an instruction to
  close the connection, after which the interface MUST close it.
- **FR-006**: The refusal in FR-005 MUST NOT open a database connection, MUST NOT open an
  audit-log file handle, and MUST NOT start a new worker for the refused connection.
- **FR-007**: A connection admitted under the cap MUST be served exactly as it is served
  today; no response body, status, or header of an admitted request may change as a result of
  this feature.
- **FR-008**: Capacity MUST be released when a connection ends, by any means — normal
  completion, the bound in FR-001, client disconnect, or an unhandled failure while serving —
  so that a failure cannot permanently consume a slot.
- **FR-009**: The interface MUST print a message to the terminal when it begins a saturation
  episode, and MUST NOT print one per refused connection. It MUST NOT begin a new episode on
  a momentary dip below capacity — a single freed slot is not a recovery, and treating it as
  one reintroduces the per-connection message this requirement exists to prevent.
- **FR-010**: The interface MUST record the total number of connections refused for capacity
  in the durable record it writes when it stops.
- **FR-011**: Individual refusals MUST NOT be written to the durable record. This is a
  Principle III exception and MUST be enumerated and justified in the plan.
- **FR-012**: The bound and the cap MUST be fixed values in the source, not configuration.
  There is one operator and one deployment; a knob with no second caller is complexity the
  constitution forbids.
- **FR-013**: The chosen cap MUST be high enough that ordinary use — several browser tabs on
  the interface, with its auto-refresh running — does not reach it, and low enough that the
  descriptors held at the cap remain far below the process's descriptor limit. The chosen
  value and its reasoning MUST be recorded in the plan.
- **FR-014**: The existing refusal of over-large request bodies MUST continue to refuse
  without reading and to close the connection rather than draining it.
- **FR-015**: Both behaviours MUST be exercised by tests that bind a real socket, because
  neither is reachable by a test that calls the request handler as a function.

### Key Entities

- **Connection slot**: One unit of the interface's serving capacity, held from the moment a
  connection is admitted until the connection ends. The number of slots is the cap; the number
  held is what the refusal decision consults.
- **Refusal count**: A running total of connections turned away for capacity during one run of
  the interface, carried into the stop record. Not persisted across runs.
- **Saturation episode**: A period beginning when the interface reaches capacity and ending
  only once the pressure is comfortably gone — not the instant a single slot frees. One
  terminal message per episode, regardless of how many connections are refused within it.
  The asymmetry is deliberate and required: under sustained pressure the count sits *at* the
  cap and oscillates by one as slots recycle, so an episode that ended at the first release
  would restart on every recycled connection and produce a message per connection in all but
  name, which is exactly what FR-009 forbids. The implementation's threshold is half the cap;
  what this specification requires is only that the end of an episode means recovery rather
  than a momentary dip.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A connection that sends nothing is closed by the interface within the bounded
  wait, with no operator action.
- **SC-002**: With more silent connections open than the interface's cap, a request the
  operator makes afterwards either succeeds normally or is refused immediately; it never hangs.
- **SC-003**: The number of database connections and audit-log file handles the interface
  holds open at any moment is bounded by a fixed number regardless of how many clients connect.
- **SC-004**: The interface serves a request that takes longer than the bounded wait to
  compute, in full, unchanged.
- **SC-005**: Ordinary operator use — the interface open in several browser tabs with
  auto-refresh running — produces no refusals.
- **SC-006**: After a run in which connections were refused, the count of refusals is
  recoverable from the durable record alone, without re-running anything.
- **SC-007**: A flood of refused connections adds a number of durable records bounded by the
  number of saturation episodes, not by the number of refusals.
- **SC-008**: Every existing test of the web interface passes unchanged.
- **SC-009**: A run in which many connections are opened and dropped produces no tracebacks on
  the terminal, while a genuine failure inside a request still produces one.

## Assumptions

- The bounded wait is measured per socket operation, in the sense that any single stretch of
  client silence longer than the bound ends the connection. A client that sends a byte more
  often than the bound keeps its connection, which is why the cap in FR-004 is required and
  the bound alone is not sufficient.
- The cap governs simultaneous *connections*, not simultaneous requests, because a kept-alive
  connection holds a worker for its whole life and that worker is the resource being bounded.
- Refusing at capacity is preferable to queueing. There is no load to serve here; a queue
  would only convert a fast, honest refusal into a slow, ambiguous one.
- Reusing one database connection per worker instead of one per request — raised in the issue
  as something to consider — is **out of scope**. With the cap in place the number of
  simultaneous connections is already bounded, which is what made the per-request cost
  dangerous; a pooling layer would add moving parts to solve a problem the cap has closed.
- The interface remains unauthenticated and its bind address remains its access policy. This
  feature bounds resource consumption; it does not add authentication, rate limiting per
  client, or address-based refusal, none of which the reported defect requires.
- Existing precondition checks, startup output, shutdown behaviour, and the audit contract for
  requests that are actually served are unchanged.
