# Feature Specification: Times Are Read in the Local Timezone

**Feature Branch**: `010-local-timezone-display`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "The CLI and web UI should show all times in the system's local timezone, not UTC"

## User Scenarios & Testing *(mandatory)*

<!--
  One defect with two faces. Every timestamp this system stores is UTC, which is correct,
  and every timestamp it *shows* is that same UTC string handed to a human unchanged, which
  is not. The two stories below are the two interfaces; either can ship alone and each
  removes the arithmetic from the surface it covers. Story 3 is the boundary: it states what
  must NOT move, because the value of a UTC record is that it never shifts under you.
-->

### User Story 1 - Reading a time in the terminal without doing arithmetic (Priority: P1)

The maintainer runs `robot-army status` at half past nine in the evening and reads that
dispatch was paused at `2026-08-30T01:31:07Z`. Nothing about that string says "three minutes
ago". It says tomorrow. The maintainer knows the offset — it is their own machine — but they
have to apply it, in their head, every time, for every stamp on the screen, and the answer
changes twice a year. `show` prints six state transitions and a session's start and end;
`anomalies` prints a detection time; `log` prints a timestamp per record. Each is a small
subtraction, and the subtraction is being asked of the one person the tool exists to serve.

After this change the terminal prints those same instants in the machine's own timezone, with
the zone stated, so "was that just now or last night?" is answered by reading rather than by
computing.

**Why this priority**: The terminal is where the maintainer already is, and per the
constitution every capability is terminal-reachable — so it is the surface that carries every
timestamp the system has. Delivered alone it fixes the majority of the daily friction.

**Independent Test**: Run the read-only commands (`status`, `show`, `anomalies`, `log`,
`worktree list`, `doctor`) on a machine whose timezone is not UTC, against a database holding
known instants, and confirm every displayed time is the local rendering of the stored instant
and names its zone.

**Acceptance Scenarios**:

1. **Given** a work item whose stored `active_at` is `2026-08-30T01:31:07Z` and a machine in a
   zone four hours behind UTC, **When** the maintainer runs `show` for that item, **Then** the
   transition is displayed as the corresponding local time on 2026-08-29, labelled with its
   zone, and not as `2026-08-30T01:31:07Z`.
2. **Given** audit records spanning an evening, **When** the maintainer runs `log`, **Then**
   each record's time reads in local terms and the records remain in the same order.
3. **Given** the machine's timezone is UTC, **When** any command displays a time, **Then** the
   displayed value is the stored instant and still carries an explicit zone indication.
4. **Given** a stored timestamp that is absent, **When** it would be displayed, **Then** the
   existing absent-value marker appears and no error is produced.

---

### User Story 2 - Reading a time on the phone without doing arithmetic (Priority: P2)

The maintainer opens the web interface from their phone to see whether the session that has
been running all evening is stuck. The active view pairs each timestamp with a relative age —
"3h 12m ago" — which is readable, and an absolute UTC stamp beside it, which is not. The
page footer says when it was rendered, in UTC, which is the one line on the page whose whole
job is to tell the reader how fresh what they are looking at is. The pause banner says
dispatch has been paused since a UTC instant. On a phone, at a glance, in a hurry, the UTC
half of every pair is noise the reader must decode or ignore.

After this change the absolute half of each pair reads in the machine's timezone, and the
relative half is untouched — the pair still exists, because a relative age alone cannot be
cross-referenced against anything, and now both halves are legible without conversion.

**Why this priority**: The relative age already carries the phone-glance case, so the web
interface is less broken than the terminal — but it is the surface the maintainer reads
furthest from a computer, where mental arithmetic is least welcome.

**Independent Test**: Serve the interface on a machine in a non-UTC zone against a database
holding known instants, request every view, and confirm each absolute timestamp on each page
is the local rendering, labelled, with relative ages unchanged.

**Acceptance Scenarios**:

1. **Given** a running session started at a known UTC instant, **When** the active view is
   requested, **Then** the absolute time shown is that instant in the machine's zone, labelled,
   and the relative age beside it is unchanged from today's behaviour.
2. **Given** dispatch is paused, **When** any view is requested, **Then** the pause notice
   states the pause time in local terms.
3. **Given** any view, **When** it is rendered, **Then** the render-time footer states a local
   time with its zone.
4. **Given** the same instant, **When** it is shown by the terminal and by the web interface on
   the same machine, **Then** both display the same local wall-clock value.

---

### User Story 3 - The record and the scripts do not move (Priority: P3)

The maintainer searches an audit file for a specific instant, or feeds the machine-readable
output into another tool, or compares a stored timestamp against one GitHub reported. All of that works today precisely
because there is exactly one timestamp format in the system and it is UTC. A change that
"shows local times" by making the stored or machine-readable values local would trade a small
daily annoyance for a permanent class of ambiguity — offsets that shift under DST, records
that cannot be compared across a timezone change, a log that no longer means one thing.

After this change nothing in the database, the audit files, or any machine-readable response
differs by a single byte. The change lives entirely in the layer that turns a stored instant
into a line of text for a person.

**Why this priority**: It is a constraint on the other two stories rather than new capability,
but it is the constraint that decides whether they are safe. Principle III's standard is
reconstruction from the log alone; a log whose timestamps depend on where the reader stood
does not meet it.

**Independent Test**: Capture machine-readable output and audit file contents before and after
the change for identical inputs and confirm they are identical, including when the machine's
timezone is not UTC.

**Acceptance Scenarios**:

1. **Given** any command run with the machine-readable flag on a non-UTC machine, **When** its
   output is inspected, **Then** every timestamp field is UTC in the existing format.
2. **Given** any action that writes an audit record on a non-UTC machine, **When** the audit
   file is read, **Then** the record's timestamp is UTC in the existing format and the file is
   named by the UTC day as before.
3. **Given** a machine-readable response from the web interface, **When** it is inspected,
   **Then** its timestamps are UTC.
4. **Given** stored rows written before this change, **When** they are read afterwards,
   **Then** no migration, rewrite, or re-interpretation of stored values has occurred.

---

### Edge Cases

- **The machine's zone cannot be determined** — no zone configured, or a broken zone name.
  The interfaces must still render: fall back to UTC, say so via the zone label, and do not
  fail the command or the page.
- **Daylight-saving transitions.** Conversion runs one way, from a stored instant to local
  wall-clock, so every stored instant has exactly one local rendering even on the two days a
  year when a local wall-clock time is skipped or repeated. Displayed times across a
  transition may therefore appear to jump or to repeat; the explicit zone indication is what
  keeps them unambiguous.
- **A stored timestamp that does not parse.** Displayed verbatim rather than crashing the
  command or the page — a rendering layer must not be the thing that hides a corrupt row.
- **The reader is in a different timezone from the machine.** The phone travels; the daemon
  does not. Times are the machine's local times, matching what the terminal on that machine
  shows, and are labelled — so a reader elsewhere sees a labelled foreign time rather than a
  silently wrong local one.
- **The daemon and an interactive shell disagree about the zone.** A service process may have
  a different environment from a login shell. Both must resolve the same host zone, or the
  same instant would read two ways on one machine.
- **A local day spans two audit files.** Audit files are named by UTC day and stay that way,
  so an evening's activity can land in two files. Reading the log by day is a record-layer
  operation and is unchanged; only the timestamps printed from those records are converted.
- **Relative ages and duration inputs.** `--since 2h` and "3h 12m ago" describe elapsed time,
  not wall-clock, and are unaffected in both meaning and appearance.

## Requirements *(mandatory)*

### Functional Requirements

**Human-facing display**

- **FR-001**: Every timestamp the terminal interface presents in human-readable output MUST be
  rendered in the host machine's local timezone.
- **FR-002**: Every timestamp the web interface renders into a page MUST be rendered in the
  host machine's local timezone.
- **FR-003**: Every displayed timestamp MUST carry an explicit indication of the timezone it is
  expressed in, so that a displayed time can be reconciled with a stored UTC record without
  the reader knowing where the machine stands.
- **FR-004**: Displayed timestamps MUST use a single fixed-width format that sorts
  lexicographically in chronological order and states its components unambiguously, so that a
  column of times remains scannable and no displayed time can be misread as a different date.
- **FR-005**: The same instant MUST display as the same local wall-clock value in every
  human-facing surface; no surface may show UTC while another shows local time.
- **FR-006**: Relative ages MUST continue to be shown wherever they are shown today, with
  their current meaning and wording, alongside the absolute local time rather than in place
  of it.

**Determining the zone**

- **FR-007**: The timezone used MUST be the one the host operating system reports for the
  running process, honouring the standard environment-variable override that the operating
  system already defines.
- **FR-008**: No new configuration option for selecting a display timezone MUST be introduced;
  the host's own zone is the single source.
- **FR-009**: If the host's timezone cannot be determined, the interfaces MUST render times in
  UTC, indicate that as the zone, and MUST NOT fail the command or the page.

**The record does not move**

- **FR-010**: Stored timestamps MUST remain UTC in the existing documented format; no stored
  value may be rewritten, migrated, or re-interpreted by this change.
- **FR-011**: Audit records MUST continue to carry UTC timestamps, and audit files MUST
  continue to be partitioned by UTC day.
- **FR-012**: Machine-readable output — both the terminal's machine-readable flag and the web
  interface's machine-readable responses — MUST continue to carry UTC timestamps in the
  existing format.
- **FR-013**: Every comparison, ordering, age, staleness threshold, backoff window, and
  capacity decision MUST continue to be computed from the stored UTC instants; this change is
  presentational and MUST NOT alter any decision the system makes.
- **FR-014**: Arguments and filters that accept a time-related value MUST keep their existing
  meaning and accepted forms.

**Robustness**

- **FR-015**: A stored timestamp that cannot be interpreted MUST be displayed as it is stored,
  without raising an error or omitting the row.
- **FR-016**: An absent timestamp MUST continue to render as the existing absent-value marker.

**Accountability**

- **FR-017**: This change MUST NOT alter which actions are audited or what is recorded for
  them; converting a stored instant for display changes no state outside the process and is
  therefore not itself an auditable action.

**Documentation**

- **FR-018**: The project documentation that describes timestamp handling MUST state the split
  this feature establishes — stored and recorded in UTC, displayed to a person in the host's
  local zone — so that the next reader does not "fix" one half of it.

### Key Entities *(include if feature involves data)*

- **Stored instant**: A moment in time as the system records it — UTC, in one documented
  format, in the database and the audit files. Unchanged by this feature and the sole input to
  every decision the system makes.
- **Displayed timestamp**: The text a person reads for a stored instant. Newly expressed in the
  host's local zone and labelled with it. Exists only in human-facing output.
- **Host timezone**: The zone the operating system reports for the running process. Read, never
  configured by this system, and identical for the daemon, the terminal, and the web process on
  one machine.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a machine whose zone is not UTC, 100% of timestamps displayed by the terminal
  interface and the web interface read as local wall-clock times; zero display a raw UTC value.
- **SC-002**: 100% of displayed timestamps state the zone they are expressed in.
- **SC-003**: The maintainer can tell whether a displayed event happened today or yesterday by
  reading it, with no conversion step, for every timestamp either interface shows.
- **SC-004**: For identical inputs, machine-readable output is byte-identical before and after
  the change, on a machine in any timezone.
- **SC-005**: For identical actions, audit file contents and file names are byte-identical
  before and after the change, on a machine in any timezone.
- **SC-006**: Both interfaces render every view without error on a machine with no determinable
  timezone, and on a machine whose zone observes daylight saving, including for instants that
  fall on either side of a transition.
- **SC-007**: Given a time displayed by either interface and the audit log, the maintainer can
  identify the corresponding record unambiguously, with no case in which two distinct instants
  are displayed identically.
- **SC-008**: The same stored instant displays as the same wall-clock value in the terminal and
  in the web interface on the same machine, in 100% of cases checked.

## Assumptions

- **Machine-readable output stays UTC.** The terminal's machine-readable flag is documented as
  producing machine-readable output, and the web interface's machine-readable responses exist
  to be parsed. "Show times in local time" is read as a statement about what a person reads,
  not about what a program consumes. This keeps a single, stable, offset-free format for every
  consumer that compares or stores what it is given.
- **Conversion happens on the machine that runs the software**, not in the reader's browser.
  The terminal and the web interface then agree, which is the property that makes a time
  quotable between them. A reader whose phone is in another zone sees a labelled time from the
  machine, which is the honest rendering of "the system's local timezone" as asked.
- **The display format is an unambiguous, fixed-width, sortable rendering with a numeric zone
  offset** — the local analogue of what is displayed today, rather than a locale-dependent or
  prose format. This preserves the scannability of a column of times and keeps the change to
  the value rather than the shape.
- **No timezone configuration is added.** Principle I forbids a configuration knob with one
  caller and no second use in hand, and Principle II fixes the target at one user on one
  machine; the host's zone is therefore the only sensible source.
- **Relative ages are already correct** and are not part of this change beyond continuing to
  appear where they appear now.
- **Timestamps embedded in file names, spool files, and inter-process signals are record-layer
  values**, not display, and remain UTC.
- **No stored data is migrated.** Every existing row and every existing audit file is already
  in the format this feature keeps.
