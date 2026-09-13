# Feature Specification: A Board Anomaly Always Names the Board's Current Failure

**Feature Branch**: `robot-army/issue-73-board-precondition-anomalies-dedupe`

**Created**: 2026-09-13

**Status**: Draft

**Input**: GitHub issue #73: "board_precondition anomalies dedupe across different failed checks,
so a new board failure is hidden behind a resolved one".

## Background

When the Trello board fails one of its preconditions (it is public, the intake label is missing,
a column is missing, …), the daemon disables board ingestion and raises a `board_precondition`
anomaly naming which checks failed. While that anomaly is unacknowledged, a later failure on the
same board raises nothing: the one open row per board absorbs it, whatever it says.

Found during the issue #1 verification round: the board was made public (anomaly 19, "board is
private" failed), made private again, and then its intake label was renamed. Ingestion was
correctly disabled for the new reason, but `robot-army anomalies` went on showing only anomaly 19,
which said the board was public. It was not. The live cause appeared only after 19 was
acknowledged and the daemon restarted. For that window the list named a resolved cause and hid a
live one, and because both are the same kind on the same board, nothing on screen suggested
anything newer was being withheld.

The audit log was right throughout. The defect is confined to the surface an operator checks
first.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The open board anomaly says what is wrong now (Priority: P1)

As the operator, when board ingestion is off, I read `robot-army anomalies` (or `status`, or the
`/anomalies` page) and the board's anomaly names the checks that failed on the most recent check,
not the ones that failed the first time the board broke.

**Why this priority**: This is the whole defect. An anomaly that names a fixed problem sends me to
fix something that is not broken and leaves the real cause unreported.

**Independent Test**: Raise a board anomaly for one failed check, leave it unacknowledged, run the
board check again with a different check failing, and read the anomaly listing.

**Acceptance Scenarios**:

1. **Given** an unacknowledged board anomaly whose failed check is "board is private", **When** the
   board is next checked and the only failing check is "tag exists", **Then** the listing shows one
   board anomaly for that board, and it names "tag exists" with that check's detail and does not
   mention "board is private".
2. **Given** the same starting anomaly, **When** the next check fails *both* "board is private" and
   "tag exists", **Then** the one board anomaly names both.
3. **Given** a board anomaly whose failed checks have just been restated, **When** I list anomalies
   narrowed to a recent window (`--since`), **Then** the anomaly appears in that window, because its
   current reason was detected within it.

---

### User Story 2 - A board that stays broken stays quiet (Priority: P1)

As the operator, a board that is public for a week produces one anomaly, not one per check, and
re-checking it changes nothing.

**Why this priority**: This is what the existing deduplication protects. The fix must not trade
one bug for a flood of rows.

**Independent Test**: Run the board check repeatedly with the same failure and count anomaly rows
and audit records about anomalies.

**Acceptance Scenarios**:

1. **Given** an unacknowledged board anomaly, **When** the board is checked again and the same
   checks fail with the same details, **Then** there is still exactly one open board anomaly for
   that board, its contents and detection time are unchanged, and no record of a restatement is
   written.
2. **Given** a restated anomaly, **When** the reason changes again, **Then** there is still exactly
   one open board anomaly for that board.

---

### User Story 3 - A changed reason can be reconstructed (Priority: P2)

As the operator reconstructing what happened after the fact, the audit log tells me that the open
board anomaly's reason changed, from what, to what, and when the earlier reason had been detected.

**Why this priority**: Restating a row overwrites what it used to say. The constitution's
reconstruction standard requires that the overwritten reason survive somewhere, and the log is
where it lives.

**Independent Test**: Restate an anomaly and read the audit records it produced.

**Acceptance Scenarios**:

1. **Given** an unacknowledged board anomaly, **When** its reason is restated, **Then** exactly one
   audit record is written for that restatement, naming the anomaly, the board, the previous failed
   checks, the new failed checks and the previous detection time.

---

### Edge Cases

- **The earlier anomaly was acknowledged.** Behaviour is unchanged: the acknowledged row is never
  touched, and a new failure raises a new anomaly, as it does today.
- **The same checks fail but a detail changed** (for example, the list of labels the board *does*
  have, quoted in the "tag exists" detail, is different). What the anomaly says would otherwise be
  stale, so it is restated. Deduplication holds only while the anomaly's text would be identical.
- **The board passes its checks.** Out of scope. The open anomaly stays until acknowledged, as it
  does today; making `board_precondition` a self-resolving kind is a separate change (see
  Assumptions).
- **An open board anomaly whose stored detail cannot be read.** It is restated to the current
  failure, which is the correct text in every case, and the audit record says the previous detail
  was unreadable rather than inventing one.
- **Interrupted mid-restatement.** The row's new text and its new detection time are written
  together or not at all. A kill before the change is committed leaves the previous text, which the
  next check corrects.
- **An anomaly for a different board.** Never touched. The restatement applies only to the open
  anomaly for the board being checked.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: At most one unacknowledged, unresolved `board_precondition` anomaly MUST exist per
  board (per rehearsal flag, which for board anomalies is always "real").
- **FR-002**: When the board check fails and an open board anomaly for that board exists whose
  failed checks (names and details) differ from the current failures, the system MUST replace that
  anomaly's failed checks with the current ones.
- **FR-003**: When FR-002 restates an anomaly, its detection time MUST become the time of the
  restating check, so that ordering and time-window filters treat the current reason as new.
- **FR-004**: When the board check fails with failed checks identical to those the open anomaly
  already names, the system MUST write nothing to the anomaly and MUST NOT write a restatement
  record.
- **FR-005**: Every restatement MUST write one audit record before the change is considered done,
  carrying the anomaly id, the board, the previous failed checks, the new failed checks and the
  previous detection time.
- **FR-006**: An acknowledged or resolved board anomaly MUST NOT be modified. With no open anomaly
  for the board, a failure MUST raise a new one exactly as it does today.
- **FR-007**: The per-check `trello.board.check` audit record written when the board fails MUST be
  unchanged.
- **FR-008**: The operating guide MUST say that a board anomaly is restated rather than duplicated
  when its reason changes, and the audit-log guide MUST document the restatement record.

### Key Entities

- **Board anomaly**: one open row per board while ingestion is off, carrying the board, the failed
  checks (name and detail each), the consequence, and when its current reason was detected.
- **Restatement record**: an audit entry saying the open board anomaly's reason changed, and from
  what.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Replaying the issue's sequence (public → private → label renamed, no acknowledgement
  in between) leaves `robot-army anomalies` showing exactly one board anomaly, naming only the
  missing label, without acknowledging anything or restarting a second time.
- **SC-002**: Checking a board five times with an unchanged failure leaves exactly one board
  anomaly, unmodified, and zero restatement records.
- **SC-003**: For every restatement, the previous failed checks can be recovered from the audit log
  alone.
- **SC-004**: The full test suite passes.

## Assumptions

- The board preconditions are checked when the daemon starts, so "the next check" means the next
  daemon start. Checking the board periodically is not part of this change.
- Retracting a board anomaly when the board passes (making it a fifth self-resolving kind) is out of
  scope. The issue asks for the reason never to go stale while the board is still broken, not for
  the anomaly to clear itself, and a passing board is already visible as ingestion running.
- Of the two fixes the issue offers, restating in place is chosen, as its author prefers: one row
  per broken board matches how the operator thinks about it, and the stale reason, not the row
  count, is the defect. Keying on the failed check names instead would leave the stale row listed
  beside the new one.
- `doctor` reports board checks but raises no anomaly, and is unchanged.
- Board anomalies are always recorded as real regardless of effect level, because board reads are
  real at every level. That is unchanged.
