# Feature Specification: A Reattach Line Only Where Reattaching Would Reach That Session

**Feature Branch**: `robot-army/issue-51-show-offers-a-dtach-a-reattach-command`

**Created**: 2026-09-08

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#51 — "`show` offers a `dtach -a` reattach command for sessions that have ended and whose socket is gone" (labels: bug, robot-army). Found during the issue #1 verification round on 2026-08-30, running quickstart scenario 4.

**Baseline**: written against `main` at 047f8cd. Verified in that tree: `operations.show` prints the reattach line at `src/robot_army/operations.py:1041` under `if session.host_socket:` and nothing else; the issue's citation of line 702 is from an older tree. Two further facts this specification depends on were read there rather than assumed — `Layout.socket_for` names a socket after the **work item**, not the session (`src/robot_army/paths.py:150`), so every attempt on one item records the same path; and the session row is written before the host is spawned (`src/robot_army/dispatch.py:1155`), so a `starting` row whose socket does not exist yet is a normal, transient state and not a fault.

## User Scenarios & Testing *(mandatory)*

<!--
  Three stories, ordered by how wrong the output is today.

  Story 1 is the reported defect: a session that is over is offered as somewhere to go and
  look. It is also, unexpectedly, the story that carries the *worse* of the two errors —
  because sockets are named after the item, an ended attempt's recorded path is the path the
  next attempt will listen on, so the offered command can succeed and land the reader in a
  different session than the row it was printed under.

  Story 2 is the opposite error, which the same guard has always hidden: a row the record
  calls live whose socket answers nothing. The line printed there today is not wrong because
  the session ended — the record says it did not — it is wrong because there is nothing at
  the other end, and that contradiction is worth a sentence rather than silence.

  Story 3 is the honesty requirement on the check itself. A probe that cannot answer must not
  be read as an answer, in either direction.
-->

### User Story 1 - A session that is over offers nowhere to go (Priority: P1)

The maintainer is deciding what to do about an interrupted item. `robot-army show <id>` lists
its session attempts, and the reader's eye is on the attempt that ended badly:

```
sessions (1 attempt(s)):
  #1 lost* id=b2a7f229-df13-4332-805a-da996d3d6ab7 pid=177996 exit=—
       started 2026-08-30 17:10:44 -04:00 ended 2026-08-30 17:13:17 -04:00
       reattach: dtach -a /run/user/1000/robot-army/12.sock
```

The session is `lost`, it has an `ended` stamp, and its dtach master removed that socket when
it exited. The command offered cannot work. Worse, it is not inert: the socket is named after
item 12 rather than after this session, so once the item is dispatched again the same command
succeeds — and attaches the reader to a *different* session, under a row that says `lost` and
names an ending two days ago.

After this change the line is absent from a session in a terminal state, and the state on the
line above is the whole of what the output says about where that session went.

**Why this priority**: This is the reported defect, it is on the surface read while deciding
what to do about an interrupted item, and it is the one whose failure mode is a command that
appears to work.

**Independent Test**: Show an item whose only attempt is `exited_clean`, `exited_error` or
`lost` and which recorded a socket path. Confirm no reattach line appears, for each of the
three states, whether or not anything is listening on that path.

**Acceptance Scenarios**:

1. **Given** an attempt in a terminal state (`exited_clean`, `exited_error`, `lost`) with a
   recorded socket, **When** `show` renders it, **Then** no reattach line and no other
   attach instruction appears for that attempt.
2. **Given** that attempt's recorded socket path is *live* because the item was dispatched
   again and a new session is listening there, **When** `show` renders the ended attempt,
   **Then** it still offers no reattach line — the live socket belongs to the newer attempt,
   which has its own row.
3. **Given** an item with several attempts of which only the last is open, **When** `show`
   renders them, **Then** at most the open one carries a reattach line and every finished
   attempt carries none.
4. **Given** an attempt with no recorded socket at all, **When** `show` renders it, **Then**
   nothing is printed in place of the line and no path is invented — unchanged from today.

---

### User Story 2 - A session the record calls live, with nothing at the other end, says so (Priority: P2)

An item's session row says `running`. The machine rebooted, or the dtach master was killed,
and nothing has reconciled the row yet. The socket path is recorded and there is nothing
listening on it.

Today `show` prints the reattach command, and it fails at the shell with a message about the
socket. The row is one of the few places the system holds a contradiction it can see —
"the record says this is live" against "nothing answers where it lives" — and saying nothing
at all would leave the reader with a missing line to interpret.

After this change that attempt carries a line stating the fact: nothing is listening on the
recorded path. The reader learns in `show` what they would otherwise learn from a failed
command, and the state line above it is now visibly stale rather than merely wrong.

**Why this priority**: Real, and reached often after an unclean reboot — but the harm is a
command that fails loudly, not one that quietly does the wrong thing. It is separated from
story 1 because it is the case where the *absence* of a line would itself be misleading.

**Independent Test**: Show an item whose latest attempt is `running` with a recorded socket
that nothing is listening on. Confirm the output names the path and says nothing is listening,
and that it does not offer the command.

**Acceptance Scenarios**:

1. **Given** an attempt in a non-terminal state (`starting`, `running`) whose recorded socket
   answers, **When** `show` renders it, **Then** the reattach command appears exactly as it
   does today.
2. **Given** an attempt in a non-terminal state whose recorded socket does not answer, **When**
   `show` renders it, **Then** a line names that path and states that nothing is listening on
   it, and no runnable command is offered.
3. **Given** an attempt in a non-terminal state whose recorded socket path does not exist at
   all — a `starting` row whose host has not been spawned yet, or a rehearsal's row naming a
   socket that never existed — **When** `show` renders it, **Then** the output is the same as
   scenario 2 and asserts nothing about whether the socket ever existed.

---

### User Story 3 - A check that could not answer is not read as an answer (Priority: P3)

Deciding whether a socket is attachable means asking it. That question can fail to return an
answer — a socket that accepts a connection but never completes it, a permission error on the
path, an unexpected error from the operating system.

After this change, a check that could not conclude says so and still offers the command, on
the grounds that a working session must not be hidden from the maintainer because a probe
misbehaved. The caveat is on the same line, so the reader knows the offer is unverified.

**Why this priority**: Rare. It matters because both of the alternatives — treating an
unanswerable probe as "dead" and hiding the command, or as "alive" and offering it without a
word — replace one false statement with another, and this feature exists to stop the surface
making claims it has not checked.

**Independent Test**: Make the liveness check raise its boundary error for a non-terminal
attempt and confirm the command still appears, with the failure named on the line.

**Acceptance Scenarios**:

1. **Given** a non-terminal attempt whose liveness check raises, **When** `show` renders it,
   **Then** the reattach command appears with the reason the check could not answer, and
   `show` still exits `0` as it does for any successful render.
2. **Given** the same failure, **When** `show` renders the rest of the item, **Then** every
   other section is unaffected and no other attempt's line is changed.

---

### Edge Cases

- **A socket path is per item, not per session.** Two attempts on one item record identical
  paths. Whether a path answers therefore cannot, on its own, attribute the thing answering
  to a particular attempt. The session's own state is what makes the attribution: a finished
  session is not what is listening, whatever is.
- **Two open sessions on one item.** The record permits it — `worktree remove` counts open
  sessions and says so when there is more than one — and in that case the answering socket
  genuinely cannot be attributed to one of the two rows. Both non-terminal rows will carry the
  offer. This is accepted rather than solved: the record is already anomalous there, and the
  ambiguity belongs to the situation, not to this line.
- **A rehearsal's session.** A row recorded below the `live` effect level names a socket that
  no process ever listened on. It is treated as any other row: terminal, no line; open, a line
  saying nothing is listening. Nothing here inspects the row to decide which host it came
  from — the record of what the socket does now is the same question in both cases.
- **An item with no attempts** prints `no session attempts yet`, unchanged.
- **The `--json` payload** is unchanged. `host_socket` there is a recorded fact rather than an
  offer to act, so it carries none of the implication being removed from the terminal line.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `show` MUST NOT print a reattach command for a session attempt in a terminal
  state (`exited_clean`, `exited_error`, `lost`), whatever that attempt recorded as its socket
  and whatever is listening on that path now.
- **FR-002**: `show` MUST print the reattach command for an attempt in a non-terminal state
  (`starting`, `running`) whose recorded socket is confirmed attachable, in the form it prints
  today.
- **FR-003**: `show` MUST print, for an attempt in a non-terminal state whose recorded socket
  is confirmed *not* attachable, a line naming the recorded path and stating that nothing is
  listening on it, and MUST NOT offer a runnable command for that attempt.
- **FR-004**: Where the attachability check cannot reach a conclusion, `show` MUST print the
  command together with the reason the check could not answer, and MUST NOT report the socket
  as either attachable or dead.
- **FR-005**: An attempt with no recorded socket MUST produce no line of any kind, as today.
- **FR-006**: The attachability check MUST be the one the session host boundary already
  offers, so that what `show` reports and what `attach` will find are the same question asked
  once. `show` MUST NOT re-implement it, and MUST NOT select a host implementation from stored
  session state.
- **FR-007**: `show` MUST remain read-only: rendering an item MUST change no persistent state
  and MUST NOT alter, create, or remove any socket.
- **FR-008**: The `--json` payload of `show` MUST be unchanged by this feature, and the web
  item view — which renders from that payload — MUST be unaffected.
- **FR-009**: A failing attachability check MUST NOT fail the command: `show` MUST render the
  whole item and exit as it does today.
- **FR-010**: The guide page covering attaching to a session MUST state the rule the output
  now follows, so that an absent line is readable as a statement rather than as an omission.

### Key Entities

- **Session attempt**: one row of the `sessions` listing. Carries the state that decides
  whether it can be the thing behind a socket, and the socket path recorded when it was
  dispatched.
- **Recorded socket path**: where this item's host was told to listen. Named after the work
  item, so it is shared by every attempt on that item and outlives any one of them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every attach instruction `show` prints, on any item in any state, either reaches
  the session on the row it is printed under or carries a stated caveat. Verified by rendering
  an item in each session state against a socket that is present, absent, and stale.
- **SC-002**: Reproducing the issue's scenario — a `lost` attempt with an ended stamp and a
  removed socket — produces output containing no `dtach` command.
- **SC-003**: The same scenario after the item has been dispatched again, so that the recorded
  path is live under a new session, still produces no `dtach` command on the ended row.
- **SC-004**: `show` on an item with ten finished attempts completes in the time it takes
  today, within measurement noise: a finished attempt is not a question worth asking the
  operating system.
- **SC-005**: The full unit test suite passes, and the new behaviour is covered by tests for
  each of the three outcomes — offered, refused, and unverified.

## Assumptions

- The reader of `show` is the single maintainer at a terminal, deciding what to do about an
  item. The line exists to be typed, so "would this work?" is the right question for it to
  answer.
- A terminal session state is trustworthy for this purpose. It is written by the code that
  observed the ending, and a stale *open* state is the case story 2 covers; there is no
  corresponding "stale terminal" case, because nothing reopens a session row.
- Probing a unix socket is cheap enough to do while rendering one item: the boundary documents
  ~7 ms for a stale socket and it already carries its own timeout, so no new bound is needed.
- The reattach line's wording is otherwise the maintainer's existing habit and is not being
  redesigned; this feature changes when it appears, not what the command is.
