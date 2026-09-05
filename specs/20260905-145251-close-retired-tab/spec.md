# Feature Specification: Close a finished item's terminal tabs

**Feature Branch**: `speckit/20260905-145251-close-retired-tab`

**Created**: 2026-09-05

**Status**: Draft

**Input**: User description: "close the tab once the kitty session is killed, and only on work that's
been successfully completed (not failed or abandoned)"

Follows [#138](https://github.com/jantman/robot-army/issues/138) and
[PR #140](https://github.com/jantman/robot-army/pull/140), which gave the successful path an ending
for the *process* and left the window behind.

## Why this exists

PR #140 made a finished item's worker stop: the process ends, the session record closes, the
capacity slot comes back. Its specification claimed one more thing — that the terminal window
"closes with it" — and asserted this needed no code, on the reasoning that the window hosts a chain
that exists only to run the worker.

**That reasoning was wrong, and it reached the published guide.** Measured on the machine after the
first live retirement: both workers gone, both session records `lost`, capacity back to `1 of 3`,
`robot-army anomalies` empty — and both terminal tabs still open, hosting a dead process tree with
no socket left behind. They stayed open until the maintainer closed them by hand.

The cause is a deliberate decision from an earlier milestone. Every session's window is launched
with a flag that **keeps the window open after its command exits**, so that a launch which fails
instantly leaves something readable rather than a window that vanishes before it can be seen. That
window is often the only evidence of what went wrong. Nothing anywhere in the system closes a
window; the capability to do so exists and has never had a caller.

So the tabs accumulate: one per completed item, forever, each one a dead session that looks alive
until you click into it.

**The flag's purpose must survive this feature.** A failed launch keeps its window. What changes is
that a window belonging to work which *succeeded* — the issue closed, the item finished — is
cleaned up, because there is nothing left in it to read that the transcript does not hold.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A finished item leaves no tabs behind (Priority: P1)

The maintainer merges a pull request. The issue closes, the item finishes, and its worker is
retired. Some time later they look at their terminal and there is nothing there that they have to
identify, read, and close.

Today every completed item leaves a tab open indefinitely. The first live retirement left two, and
nothing would ever have taken them down — they were closed by hand, which is the manual step this
feature removes.

**Why this priority**: It is the whole of the request, it is on the path every successful item
takes, and the cost accumulates without bound — one tab per completed item, each indistinguishable
at a glance from a session still doing something.

**Independent Test**: take an item to `done`, let its session end, run a reconciliation pass, and
confirm the tab is gone while a tab belonging to a `failed` item in the same terminal is untouched.

**Acceptance Scenarios**:

1. **Given** a `done` item whose sessions have all ended, **When** a reconciliation pass runs,
   **Then** every terminal window belonging to that item is closed.
2. **Given** the same item was resumed once, so two of its attempts have windows, **When** the pass
   runs, **Then** **both** windows are closed — a completed item leaves no tabs, whichever attempt
   they came from.
3. **Given** a `done` item whose latest session is still running — retirement has not fired yet, or
   its worker survived the attempt — **When** the pass runs, **Then** none of that item's windows
   is closed.
4. **Given** a window left behind by a retirement that happened before this feature existed —
   its session ended long ago and nothing closed it — **When** the first pass after the upgrade
   runs, **Then** it is closed with no action by the maintainer, without having been named
   anywhere.
5. **Given** a window closed by this feature, **When** the maintainer looks for what that session
   did, **Then** the transcript, the session record and the audit log are all intact and the
   session is still resumable.

---

### User Story 2 - Failed and abandoned work keeps its window (Priority: P1)

The maintainer comes back to a machine where something went wrong. The window is still there, still
holding whatever the launch printed before it died.

**Why this priority**: **Equal first, and not a lesser concern than User Story 1.** The flag that
holds these windows open exists because a window that vanished instantly destroyed the only
evidence of a failed launch. This feature narrows that behaviour, and narrowing it too far
reintroduces the exact problem it was added to fix. A build that closed tabs correctly but also
closed a failed launch's window would be a regression, not a partial success.

**Independent Test**: seed a `failed` item and an `abandoned` item, each with a window, run passes,
and confirm both windows survive indefinitely.

**Acceptance Scenarios**:

1. **Given** a `failed` item with a window, **When** any number of passes run, **Then** the window
   is never closed.
2. **Given** an `abandoned` item with a window, **When** any number of passes run, **Then** the
   window is never closed.
3. **Given** a launch that failed before the item ever reached `done`, **When** passes run, **Then**
   its window survives — this is the case the hold flag was introduced for.
4. **Given** a window the maintainer opened themselves, in a worktree or anywhere else, **When**
   passes run, **Then** it is never touched, whatever it contains.
5. **Given** a window whose recorded item cannot be resolved at all, **When** passes run, **Then**
   it is left alone: an unidentifiable window is never closed on a guess.

---

### User Story 3 - Stopping a finished item's session by hand ends the same way (Priority: P3)

The maintainer runs the command that stops one item's session, on an item that is already `done`.
They expect the same outcome as the automatic path, because the work is in the same state.

**Why this priority**: it is a consistency property rather than new behaviour, and it costs nothing
to hold: the rule is written about the *work*, not about which command ended the session, so both
routes converge without either being named. It is P3 because User Story 1 already delivers it.

**Independent Test**: stop a `done` item's session by hand, run a pass, and confirm the window is
closed — with no change to the stopping command itself.

**Acceptance Scenarios**:

1. **Given** a `done` item whose session was stopped by hand, **When** a pass runs, **Then** its
   windows are closed, exactly as if retirement had ended the session.
2. **Given** a `failed` item whose session was stopped by hand, **When** a pass runs, **Then** its
   window survives — the route does not change the answer, and neither does the item's state
   changing later for any reason other than reaching `done`.

---

### Edge Cases

- **The terminal is not running, or cannot be reached.** Nothing is closed, the condition is
  recorded, and the pass completes normally. Reconciliation never fails for an operational
  condition.
- **A window that vanished between being listed and being closed** — the maintainer closed it
  first. That is success, not an error, and must not be reported as a failure.
- **Closing one window fails.** Every other window is still considered. One terminal refusing does
  not abandon the sweep.
- **The terminal was restarted since the window was opened.** Window numbering restarts with it, so
  a *stored* window number can name a completely unrelated window — the same class of mistake as a
  reused process id, which this system has been bitten by before. Identity must therefore be
  established from something the system itself wrote onto the window, not from a stored number.
- **A window marked as belonging to an item that no longer exists** — simulated rows purged, a
  rebuilt database. Left alone.
- **An item that failed, was retried, and later succeeded.** Its failed attempt's window is closed
  along with the rest once the item reaches `done`. This is deliberate and slightly narrows what
  the hold flag preserves: the item finished, and the failure is still in the audit log and the
  transcript.
- **A window for a `done` item that has no session record at all** — a rebuilt database. There is
  nothing to establish that its session ended, so it is left alone.
- **A simulated item's window.** Closing is a real, outward-facing act on the maintainer's screen,
  so a simulated run must record what it would have closed rather than closing it.

## Requirements *(mandatory)*

### Functional Requirements

**What gets closed**

- **FR-001**: The system MUST close every terminal window belonging to a work item that is `done`
  and all of whose session records have ended.
- **FR-002**: The system MUST close **all** of a qualifying item's windows, including those opened
  by attempts that were superseded by a later resume or restart.
- **FR-003**: The system MUST NOT close any window belonging to an item in any state other than
  `done` — `failed` and `abandoned` in particular, which keep their windows indefinitely.
- **FR-004**: The system MUST NOT close any window belonging to an item that still has an open
  session record, even when that item is `done`.
- **FR-005**: The system MUST NOT close any window it cannot positively identify as one it opened
  for a known work item.
- **FR-006**: The rule MUST be expressed in terms of the work item's state and its sessions, not in
  terms of which command ended the session, so that the automatic and by-hand routes agree without
  either being special-cased.

**Identifying a window safely**

- **FR-007**: The system MUST establish a window's identity from a marker it wrote onto that window
  when it opened it, and MUST NOT close a window on the strength of a stored window number alone.
- **FR-008**: A window with no such marker MUST never be closed, whatever it appears to contain and
  wherever it appears to be running.
- **FR-009**: A window whose marker names a work item that cannot be resolved MUST be left alone.

**When it runs**

- **FR-010**: The check MUST run as part of the recurring reconciliation pass rather than only at
  the moment a session ends, so that a window is still cleaned up when the process that ended the
  session died before it could act, and so that windows left by earlier versions are picked up.
- **FR-011**: The check MUST be idempotent: a window already closed produces no second attempt and
  no repeated record.

**Failure and accountability**

- **FR-012**: Every window closed MUST be recorded, naming the window and the work item it belonged
  to, because closing a window removes something from the maintainer's screen.
- **FR-013**: A window that could not be listed or could not be closed MUST be recorded and MUST NOT
  stop the pass or prevent other windows from being considered.
- **FR-014**: A window that has already gone by the time it is closed MUST be treated as success and
  MUST NOT be reported as a failure.
- **FR-015**: The reconciliation pass's existing summary MUST report how many windows were closed.
- **FR-016**: Below the effect level at which outward-facing actions are real, the system MUST
  record what it would have closed and MUST NOT close anything.

**What must not change**

- **FR-017**: Windows MUST go on being opened such that a failed launch leaves a readable window.
  This feature narrows *which* windows are eventually closed; it does not change what happens when a
  launch fails.
- **FR-018**: Closing a window MUST NOT touch the session record, the work item, the worktree, the
  branch, or the transcript. It removes a view, not a record.
- **FR-019**: Retirement, cancellation, cleanup, and the capacity count MUST be unchanged by this
  feature.

### Key Entities

- **Terminal window**: a view onto a session, carrying a marker naming the work item it was opened
  for. Not persisted by this system beyond that marker and a recorded number; the terminal is the
  source of truth for which windows exist.
- **Work item**: the unit of work whose state decides whether its windows may be closed.
- **Session record**: consulted only to answer "is anything still running for this item?"

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An item taken from dispatch through to a merged pull request and a closed issue leaves
  zero terminal windows behind, with no action by the maintainer beyond merging.
- **SC-002**: Across ten items worked in sequence, the number of terminal windows at the end equals
  the number at the start.
- **SC-003**: A window left by a retirement that predates this feature is closed by the first
  reconciliation pass after it ships. The sweep reaches windows it never watched being created,
  which is what an event fired at the moment of retirement could not do.
- **SC-004**: A launch that fails leaves a window that survives indefinitely — verified across at
  least ten passes, because "it survived one pass" would be satisfied by a build that closes it on
  the second.
- **SC-005**: No window that the system did not open is ever closed, under any item state and any
  terminal contents.
- **SC-006**: A window closed by the system loses nothing recoverable: the transcript, the session
  record and the audit trail are all still readable afterwards.

## Assumptions

- **The rule is about the work, not the route.** Confirmed on clarification: any `done` item whose
  sessions have ended loses its windows, whether the session was ended by automatic retirement or by
  the maintainer running the stop command. This is why User Story 3 needs no change to that command
  — it falls out of the rule rather than being added to it.
- **All attempts, not just the retired one.** Confirmed on clarification. A completed item leaves no
  tabs at all.
- **A sweep rather than an event.** Confirmed on clarification, and it is what makes SC-003
  possible: a window left by an earlier retirement is past the moment a purely event-driven design
  would have acted, and would never be closed by one. It also closes the interruption gap — a
  process that dies between ending a session and closing its window would otherwise leak a window
  permanently. **Note**: the two windows that prompted this feature were closed by hand before
  implementation began, so they are no longer available as live evidence; the property is verified
  by seeding the same condition instead.
- **No grace period.** A window is closed as soon as its item qualifies. The window contains a dead
  session whose work is merged, and everything in it is in the transcript, so waiting would defer a
  cleanup without protecting anything. This is the assumption most worth revisiting if it turns out
  the windows are read after the fact more often than expected.
- **The marker the system already writes onto each window names the work item**, which is what makes
  FR-007 available without opening a new channel. It does not distinguish attempts, which is
  consistent with FR-002 wanting all of them anyway.
- **Determining whether anything is still running comes from the system's own session records**, not
  from inspecting what the terminal thinks is running inside the window. The hold behaviour inserts
  an extra layer into the window's process tree, and reading that tree has already produced a wrong
  conclusion once.
- **Nothing here changes the published guide's account of retirement except the sentence that was
  wrong.** `docs/guide/5-outcome.md` currently states the tab closes with the worker; that claim
  becomes true only when this ships, and until then it is inaccurate.
