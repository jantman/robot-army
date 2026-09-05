# Feature Specification: The session wrapper trusts only the identifiers its launcher gave it

**Feature Branch**: `robot-army/issue-126-ra-16-the-wrapper-recovers-session-id`

**Created**: 2026-09-04

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#126 — "RA-16: the wrapper recovers `--session-id` from the untrusted prompt" (label: bug). RA-16 in `docs/security-analysis.md`, severity Medium. Related low finding RA-48 (the wrapper's JSON escaping omits C0 control characters) is folded in because it is the same function, the same file, and the same class of "text from an issue reaches a file format that then rejects it".

## Context

The session wrapper is the small shell script that sits between the terminal host and the
worker process. Its whole reason to exist is to observe the worker's exit and write a
durable record of it, keyed by the session id, into a spool directory the daemon drains.

Today the wrapper works out which session it is by reading *every* argument it was handed
and taking the **last** thing that looks like a session id. The last argument it is handed
is the composed prompt, whose first bytes come from a file in the target repository that no
gate checks. So the prompt can name a session id, that name beats the one the daemon
supplied, and the resulting value is joined straight onto a directory path with no check on
its shape.

The consequences that follow are: records written to a directory of the attacker's choosing
rather than the spool; a directory the daemon itself reads (`~/.claude/sessions/`) that can
be filled with files it cannot parse, which persistently degrades session identification and
stops work reaching `active`; and, for the affected session, an exit that happened cleanly
being reported as lost because its record never landed where the drain looks.

Separately and without any attacker, an issue body containing an ordinary control character
— a vertical tab, a form feed — is copied verbatim into the record's JSON, which then fails
to parse. A session quarantines its own exit record purely by quoting the wrong text.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A repository cannot redirect where the wrapper writes (Priority: P1)

The maintainer dispatches work to a repository whose committed instruction file, or whose
generated content, contains text shaped like a session-id argument. The wrapper ignores it
entirely. The exit record for that session lands in the spool directory under the id the
daemon generated, the daemon drains it, and the work item settles on its real outcome.

**Why this priority**: This is the finding. Everything else here is hardening around it.
Without this, a single ungated file in any onboarded repository can stop the daemon
identifying sessions at all.

**Independent Test**: Run the wrapper by hand with a valid session id in its environment and
a prompt-shaped final argument that names a different session id, including one containing
`..` path segments. Assert that exactly one pair of records is written, that both are inside
the spool directory, that their names carry the environment's id, and that nothing was
created anywhere else.

**Acceptance Scenarios**:

1. **Given** the launcher supplied a session id in the environment, **When** a later
   argument names a different session id, **Then** the records are written under the
   environment's id and the argument's value is used for nothing.
2. **Given** a later argument names a session id containing `../` segments, **When** the
   wrapper writes its start and exit records, **Then** both files are inside the spool
   directory and no file is created outside it.
3. **Given** the launcher supplied a session id, **When** the worker exits, **Then** the
   record's `session_id` field is the launcher's id, so the daemon's join on it succeeds.

---

### User Story 2 - An implausible identifier is refused loudly, not used (Priority: P1)

Whatever the wrapper is told its session id and item id are, it checks their shape before
either one is used to build a path. A value that is not the shape the system issues is
refused: the wrapper stops, says why on its error output, and exits non-zero. It never
writes a record under an identifier it does not recognise.

**Why this priority**: The precedence fix removes today's known route to a bad identifier.
The shape check is what makes a *future* route harmless, and it is the difference between
"this particular hole is closed" and "this class of hole cannot open here".

**Independent Test**: Invoke the wrapper with an identifier that is empty, contains a path
separator, contains `..`, or is simply the wrong shape. Assert a non-zero exit, an
explanatory message on error output, and that no file was created in any directory.

**Acceptance Scenarios**:

1. **Given** no session id is supplied at all, **When** the wrapper runs, **Then** it
   refuses with an explanatory message and a non-zero exit, and writes no record.
2. **Given** a supplied session id that is not the shape the system issues, **When** the
   wrapper runs, **Then** it refuses in the same way, and the worker is never started.
3. **Given** an item id that is not the shape the system issues, **When** the wrapper runs,
   **Then** it refuses before opening any log file, so no file is created under the bad id.
4. **Given** a refusal, **When** the maintainer reads the error output, **Then** the message
   names which identifier was refused, so the cause is identifiable without reading the code.

---

### User Story 3 - Ordinary control characters do not quarantine a record (Priority: P2)

An issue body contains a control character — a vertical tab pasted from a document, a form
feed in a code sample. The session runs, the wrapper writes its records, and the daemon
parses them without complaint. The work item settles on its real outcome instead of being
reported as lost.

**Why this priority**: It needs no attacker and it is presently reachable by accident, but it
damages one session's record rather than the daemon's ability to identify any session.

**Independent Test**: Run the wrapper with an argument containing each control character in
turn and assert the resulting records parse under a strict JSON reader, and that the round
tripped text equals what went in.

**Acceptance Scenarios**:

1. **Given** an argument containing a control character other than newline, carriage return
   or tab, **When** the wrapper writes its records, **Then** a strict JSON reader parses
   them and the decoded text matches the original argument exactly.
2. **Given** an argument containing a quote, a backslash, a newline, a carriage return or a
   tab, **When** the wrapper writes its records, **Then** they still parse and still round
   trip, so the existing escaping is not regressed by the new escaping.

---

### Edge Cases

- **The launcher's id is absent.** The wrapper refuses and exits non-zero rather than
  guessing. No record is written, so the daemon's existing reconciliation reports the
  session as lost — an honest outcome, and the loud message on error output says why.
- **A refusal happens before the worker runs.** No exit record is produced, so the daemon
  never attributes an exit status to a worker that never started. The session is reported
  as lost by the existing reconciliation, and the message on error output — captured by the
  session host and visible in the held terminal window — says why.
- **Validation order.** The item id names the log file; the session id names the record
  files. Each must be checked before the path built from it is opened, so a bad value cannot
  create a file even in the act of being reported.
- **An identifier that is the right shape but the wrong session.** Out of scope: the wrapper
  cannot know this, and the daemon's own join is what catches it. The shape check is a
  containment measure, not an authentication one.
- **Text containing a NUL byte.** Cannot occur — the surrounding process boundary cannot
  carry one into an argument — and so needs no handling.
- **Existing records written by an older wrapper.** Unaffected: the record format is
  unchanged. Only the values that reach it and the escaping of its text change.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The wrapper MUST take the session id solely from the environment its launcher
  supplies. It MUST NOT derive, recover, or override the session id from any argument it was
  handed.
- **FR-002**: The wrapper MUST validate the session id's shape before using it to build any
  filesystem path, accepting only the shape the system issues for session ids.
- **FR-003**: The wrapper MUST validate the item id's shape before using it to build any
  filesystem path, accepting only the shape the system issues for item ids.
- **FR-004**: On a refused identifier the wrapper MUST NOT start the worker, MUST NOT create
  any file or directory, MUST write a message on its error output naming which identifier
  was refused, and MUST exit non-zero. Because no record is written, a refusal can never be
  read by the daemon as the worker's verdict on the task.
- **FR-005**: The wrapper MUST escape every character that a strict JSON reader forbids
  inside a string, so that any text reaching a record leaves it parseable.
- **FR-006**: Escaped text MUST round trip: what a strict reader decodes from a record MUST
  equal what the wrapper was given.
- **FR-007**: The record format MUST be unchanged — same fields, same schema number — so the
  daemon's existing drain, its duplicate handling, and records already on disk are
  unaffected.
- **FR-008**: The wrapper MUST continue to run in a bare launch environment, using only the
  minimal toolset it is already restricted to. No new external program may become a
  prerequisite for writing a record.
- **FR-009**: The launcher MUST supply the session id in the environment for every session it
  starts, including resumed and restarted ones, since that is now the only source.
- **FR-010**: The project's security analysis MUST record RA-16 and RA-48 as resolved,
  stating what was done and why, in the form the document already uses for resolved findings.

### Key Entities

- **Session id**: the identifier the daemon generates before a session starts and uses to
  join a launched session to its records. Supplied to the wrapper by its launcher.
- **Item id**: the work item's identifier, supplied by the launcher, which names the
  session's human-readable log file.
- **Exit record**: the durable file the wrapper writes describing how the session started
  and how it ended. Named by the session id and consumed by the daemon's drain.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a hostile prompt naming an alternative session id — including one using
  `..` to climb out of the spool directory — 100% of the wrapper's records are written
  inside the spool directory, under the launcher's id, and zero files are created anywhere
  else.
- **SC-002**: Every identifier that is not the shape the system issues is refused: the
  wrapper exits non-zero, explains itself, starts no worker, and creates no file.
- **SC-003**: Records containing any control character parse successfully under a strict
  reader, and the decoded text is byte-for-byte what was supplied — across all 32 control
  characters, not a sampled subset.
- **SC-004**: A normal session is unaffected: it starts, its exit status and any signal
  death are reported exactly as before, and the work item reaches the same final state it
  reaches today.
- **SC-005**: The full test suite passes, and the wrapper's behaviour under a hostile
  identifier and under control-character text is covered by tests that execute the real
  script, not a re-implementation of it.

## Assumptions

- **The argument fallback is removed rather than demoted.** The issue offers inverting
  precedence and keeping an argument fallback as an option. The launcher already supplies
  the id in the environment for every session it starts, so a fallback would be a second
  code path with no caller — which Principle I rules out. Callers that invoke the wrapper by
  hand, including the existing tests, supply the environment variable exactly as they
  already supply the spool and log directories.
- **Session ids are the identifiers the system actually issues.** The daemon generates them
  as standard 36-character UUIDs, so that is the shape accepted. The check is deliberately
  the canonical shape rather than a looser character-class test, because a looser test
  admits values that are not ids while offering no benefit.
- **Item ids are the identifiers the system actually issues.** They are database row
  integers, so the accepted shape is a plain non-negative integer. This is belt-and-braces
  today: nothing untrusted reaches it. It is included because it is the same class of defect
  one edit away.
- **Existing tests that invoke the wrapper with non-UUID session ids will be updated.** They
  pass ids like `wrapper-session` today. They will supply real-shaped ids in the
  environment, which also makes them exercise the path production uses.
- **Refusal is not written to the audit log.** The wrapper is a bare shell script that has no
  access to the audit log by design, and adding one would contradict its documented
  constraint of running in a minimal environment. The refusal is recorded on error output,
  which the session host captures; the daemon's existing reconciliation independently
  reports the session as lost. This gap is named here so the plan's Constitution Check can
  carry it as a documented exception under Principle III rather than an undocumented one.
- **No change to the fingerprint gate.** The issue notes that the file feeding the prompt's
  first bytes is ungated (RA-02). Widening that gate is a separate finding with separate
  trade-offs, and this feature closes RA-16 without depending on it.
