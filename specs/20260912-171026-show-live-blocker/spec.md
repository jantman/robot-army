# Feature Specification: `show` Reports What Blocks an Item Now, Not What Blocked It Then

**Feature Branch**: `robot-army/issue-63-show-reports-a-stale-blocked-reason`

**Created**: 2026-09-12

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#63 — "show reports a stale `blocked` reason after the
condition is resolved, while retry reports the real one" (labels: bug, robot-army). Found during
the issue #1 verification round on 2026-08-30, running 005 quickstart scenario 9.

**Baseline**: written against `main` at f5a4d72. Verified in that tree rather than assumed:

- `blocked_reason` is written in three places: `dispatch._fail(..., blocked=True)` when the
  author check or `check_gates` refuses a dispatch (`dispatch.py:1022`, `:1045`), `poll._settle`
  when discovery rejects an issue (`poll.py:718`), and `retry` when its re-read finds the issue
  ineligible (`operations.py:3674`). All three write `failure_reason` alongside it, and all three
  write it at the moment of the refusal. Nothing rewrites it afterwards except `retry`, which
  clears it on success.
- `show` prints both stored columns verbatim (`operations.py:1076-1079`), and the web item page
  renders the same two stored values (`web/pages.py:1807-1810`) from the same payload.
- `retry` decides with **live** local checks and never reads `blocked_reason` to decide anything
  (its docstring, `operations.py:3534`): the repository must resolve to a clone
  (`repos.resolve`), then `dispatch.check_gates` must pass — onboarded, the approved clone still
  where it was approved and still that repository, the workspace trusted, the committed
  permission settings unchanged. Only then does it spend a request re-reading the issue.
- `check_gates` is local — the database, the filesystem and `git` against the clone — but it is
  **not** read-only: two of its location refusals raise an anomaly row before raising
  (`dispatch._raise_location_anomaly`, `dispatch.py:478`).
- `show` already distinguishes computed from stored values: its resume-decision signals are
  printed under "computed now, never stored" (`operations.py:1117`).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - `show` names the condition that blocks a failed item now (Priority: P1)

An item failed because its approved clone had been moved. The maintainer restores the clone and
re-approves it, then asks `show` what is left to do. Today `show` repeats the stored sentence —
"the clone … is no longer at …. Restore it" — while `retry`, asked at the same moment, refuses
for a different, real reason: the workspace is not trusted. The maintainer is sent to do
something already done, and the one command meant to answer "what should I do about this item?"
disagrees with the one that acts on the answer.

After this change, for a failed item `show` runs the same local checks `retry` runs before its
network read, and reports that verdict as the current blocker, marked as checked now. Where it
differs from the reason recorded at the failure, it says so, so the state the maintainer most
wants to notice — "what I fixed is fixed; this is what is left" — is on the screen instead of
hidden behind the old sentence.

**Why this priority**: It is the reported defect. `show` is what an operator reads to decide on
a stuck item, and it is currently the command most likely to be wrong about exactly that.

**Independent Test**: Fail an item for a missing clone; restore the clone and leave the
workspace untrusted; run `show`. It reports the trust failure as the current blocker, says the
recorded reason no longer describes the item, and matches what `retry` then refuses with.

**Acceptance Scenarios**:

1. **Given** a failed item whose recorded reason is the missing clone, and the clone restored and
   re-approved but untrusted, **When** the maintainer runs `show`, **Then** the current blocker
   reported is the trust failure, word for word what `retry` would refuse with, and it is marked
   as checked now.
2. **Given** the same item, **When** `show` runs, **Then** it states that the item is now blocked
   by something other than the reason recorded when it failed, and still shows that recorded
   reason — as the record of why it failed, not as a present-tense claim.
3. **Given** a failed item whose recorded blocker still holds, **When** `show` runs, **Then** the
   current blocker is reported once, marked as checked now, with no claim that anything changed.
4. **Given** a failed item whose recorded blocker has cleared and nothing local blocks it any
   more, **When** `show` runs, **Then** it says nothing on this machine blocks the item now, that
   `retry` would still re-read the issue before returning it to the queue, and what the recorded
   reason was.

---

### User Story 2 - The web item page says the same thing (Priority: P2)

The web item page mirrors `show` — it is rendered from the same payload. After this change its
`blocked` field shows the current verdict, labelled as checked now, and the recorded reason stays
visible as history. The JSON form of `show` carries the current verdict as its own field beside
the stored columns, which keep their meaning.

**Why this priority**: The same defect on the other surface that reads the same item; fixing only
the terminal would leave the two disagreeing, which is the shape of the reported bug.

**Independent Test**: With the item from story 1, open its web page and fetch `show --json`; both
report the trust failure as the current blocker and the missing clone as the recorded reason.

**Acceptance Scenarios**:

1. **Given** the item from story 1, **When** its web page is opened, **Then** the `blocked` field
   shows the trust failure marked as checked now, and the recorded failure reason is still shown.
2. **Given** the same item, **When** `show --json` runs, **Then** the payload carries the current
   verdict in a field of its own, and the stored `failure_reason` and `blocked_reason` are
   unchanged in the payload.

### Edge Cases

- **The check cannot be completed** (a `git` call fails or times out, the clone is unreadable in a
  way the check does not classify): `show` says the current blocker could not be determined and
  why, and shows the recorded reason labelled as recorded. It never quietly falls back to the
  stored sentence as though it were current.
- **A reason only the issue can settle** — the author, the label, a closed issue — recorded by
  discovery or by `retry`: `show` makes no request, so it cannot confirm or clear it. The local
  verdict is reported for what it covers, and the recorded reason is shown as recorded, with the
  note that `retry` re-reads the issue.
- **Items not in `failed`**: the check is not run. `retry` does not apply to them, and a queued
  item's holds are already computed live by the queue plan. A stored `blocked_reason` on a
  non-failed row, if one exists, is shown labelled as recorded.
- **Repository no longer resolves to a clone**: reported as the current blocker with `retry`'s own
  wording, as `retry` does before it reaches the gates.
- **Simulated items**: the same rule; the check is local and a simulated item has a real
  repository.
- **Repeated renders**: the web page refreshes on a timer. Rendering must not write anything —
  no anomaly, no column, no audit record — however often it runs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: For an item in `failed`, `show` MUST compute the current blocker with the same local
  checks, in the same order and with the same wording, that `retry` performs before its network
  read: the repository resolves to a clone, then every check `check_gates` makes.
- **FR-002**: `show` and `retry` MUST obtain that verdict from one shared implementation, so the
  two cannot disagree about the same item at the same moment.
- **FR-003**: The current blocker MUST be presented as a present-tense verdict marked as checked
  now; the recorded `failure_reason` MUST continue to be shown, as the record of why the item
  failed.
- **FR-004**: When the current verdict differs from the stored `blocked_reason`, `show` MUST say
  the item is now blocked by something other than the reason recorded when it failed.
- **FR-005**: When no local check blocks a failed item, `show` MUST say so, and MUST say that
  `retry` still re-reads the issue before returning it to the queue.
- **FR-006**: When the check cannot be completed, `show` MUST say the current blocker could not be
  determined and why, and MUST NOT present the stored reason as current.
- **FR-007**: Computing the verdict for `show` MUST NOT write anything: no anomaly, no work-item
  column, no audit record. It MUST make no network request.
- **FR-008**: `retry`'s behaviour — its checks, its refusals, its audit records, and the anomalies
  its gate raises — MUST be unchanged.
- **FR-009**: The `show --json` payload MUST carry the current verdict as a field distinct from the
  stored columns, and the web item page MUST render that field for its `blocked` entry.
- **FR-010**: For items not in `failed`, `show` MUST NOT run the check; any stored
  `blocked_reason` it prints MUST be labelled as recorded rather than current.

### Key Entities

- **Current blocker**: the verdict of `retry`'s local checks for one failed item, computed when
  `show` renders and never stored. One of: blocked, with the reason; nothing local blocks it; or
  could not be determined, with the error.
- **Recorded reasons** (`failure_reason`, `blocked_reason`): unchanged columns, written at the
  moment of failure. History, and now presented as history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The incident's sequence — clone moved, item failed, clone restored and re-approved,
  workspace untrusted — ends with `show` and `retry` naming the same blocker, word for word.
- **SC-002**: In every case where the recorded blocker no longer holds, `show` tells the
  maintainer so without them having to run `retry` to find out.
- **SC-003**: Opening an item page or running `show` any number of times leaves the database and
  the audit log exactly as they were.
- **SC-004**: Every existing `retry` test passes unmodified.

## Assumptions

- Recompute rather than relabel, as the issue recommends: the current blocker is exactly the
  question `show` is asked, and the check is local and already exists.
- The check covers only what `retry` checks without the network. Issue-content eligibility (the
  author, the label, open/closed) needs a read, and a read per render of a page that refreshes on
  a timer is not a price this justifies; that part stays a recorded reason, labelled as such.
- The queue page's list of blocked and failed items keeps showing the recorded failure reason. It
  is a list of history per row; the per-item page and `show` are where the current verdict is
  asked for, and are what the issue names.
- No audit record for the check itself: `show` is an inspection and changes nothing, which is
  why FR-007 exists.
