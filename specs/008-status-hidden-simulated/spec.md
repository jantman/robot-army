# Feature Specification: Status Never Contradicts Itself About Hidden Simulated Work

**Feature Branch**: `008-status-hidden-simulated`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "issue #13 on this repo" — `robot-army status` prints a populated queue and "no work items yet" in the same output

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reading `status` below `live` without being lied to (Priority: P1)

The maintainer runs the daemon at an effect level below `live` (`plan`, `dry-run`) to watch what
the system *would* do. Every work item the daemon creates in that mode is a simulated row. When
they run `robot-army status`, the queue section names four items in dispatch order — because the
queue deliberately counts simulated rows, since those rows occupy capacity — and then the very
next lines say `no work items yet` and `no matching work items`, because the counts and the
listing deliberately hide simulated rows.

Nothing underneath is broken. The rows are real rows, the database is correct, and both halves
are individually behaving as designed. But the command has printed two statements that cannot
both be true, and the maintainer's only options are to disbelieve the surface or to go read the
source. A status surface that visibly disagrees with itself is one you stop reading, which
costs far more than the four hidden rows.

After this change, the same invocation still hides the simulated rows by default — that default
is deliberate — but it *says so*: it reports how many rows it withheld and names the flag that
reveals them, so the populated queue and the empty listing become one coherent statement instead
of two contradictory ones.

**Why this priority**: This is the entire defect in the issue and the whole reason the feature
exists. Delivered alone it fully resolves the reported contradiction; nothing else in this spec
is required for it to be worth shipping.

**Independent Test**: Populate a database with only simulated work items in the `ready` state,
run `status` without `--include-simulated`, and confirm the output contains no statement of
absence that the same output's queue section contradicts, and that it names both the count of
withheld rows and the flag that shows them.

**Acceptance Scenarios**:

1. **Given** four simulated work items in `ready` and no real ones, **When** the maintainer runs
   `robot-army status`, **Then** the output shows the four-row queue and, in place of
   `no work items yet`, states that there are no visible work items and that four simulated rows
   were withheld, naming the flag that would show them.
2. **Given** the same database, **When** the maintainer runs
   `robot-army status --include-simulated`, **Then** all four rows appear in the counts and the
   listing, each marked as simulated, and no withheld-row notice is printed.
3. **Given** two real work items and four simulated ones, **When** the maintainer runs
   `robot-army status`, **Then** the counts and listing show the two real items *and* disclose
   that four further rows were withheld — the disclosure is not limited to the case where the
   listing is completely empty.
4. **Given** a database with no work items at all, simulated or real, **When** the maintainer
   runs `robot-army status`, **Then** the output states plainly that there are no work items and
   mentions no withheld rows.
5. **Given** simulated work items appear in the queue section, **When** the maintainer reads that
   section, **Then** each simulated row is visibly marked as simulated, using the same convention
   the item listing already uses.
6. **Given** the maintainer runs `robot-army status --state ready --repo owner/name`, **When**
   rows are withheld, **Then** the withheld count reflects only rows that those same filters
   would have matched, so the number the output states is the number the flag would reveal.

---

### User Story 2 - The machine-readable view agrees with the human one (Priority: P2)

The maintainer, or a script they wrote, consumes `robot-army status --json`. Today the JSON
payload has the same split as the text: a populated `queue` array beside empty `counts` and
`items`, with nothing in the document explaining the gap. A consumer that reports "0 work items"
from that payload while the queue array holds four entries reproduces exactly the contradiction
this feature removes from the text rendering.

**Why this priority**: The text fix is what the issue reports and what the maintainer reads
daily; the JSON is the second consumer of the same facts and would otherwise keep the defect
alive in a less visible place. Valuable, but not what makes the feature worth shipping.

**Independent Test**: With only simulated work items present, request the machine-readable
output without `--include-simulated` and confirm the payload states how many rows were withheld,
such that a consumer can distinguish "nothing exists" from "nothing is being shown to you".

**Acceptance Scenarios**:

1. **Given** four simulated work items and no real ones, **When** the maintainer requests
   machine-readable status output, **Then** the payload reports a withheld-row count of four
   alongside the existing counts, items, and queue fields.
2. **Given** the same database queried with simulated rows included, **When** the maintainer
   requests machine-readable output, **Then** the withheld-row count is zero.
3. **Given** any invocation, **When** the payload is read, **Then** the fields present before
   this change are still present and carry the same meaning as before.

---

### User Story 3 - Other listings that hide rows say so too (Priority: P3)

`robot-army cards` and `robot-army worktree list` hide simulated rows by the same default and
print `no cards tracked yet` / `no worktrees recorded` when everything they would have shown was
withheld. Unlike `status`, these commands show nothing alongside the claim, so they do not
contradict themselves within a single output — but they still tell the maintainer that nothing
exists when something does, and the maintainer has no way to tell the two situations apart.

**Why this priority**: The same class of defect, one step less severe because there is no
visible contradiction on screen, and entirely separable from the reported bug. Droppable without
affecting P1 or P2.

**Independent Test**: With only simulated cards (respectively worktree records) present, run each
command without the include flag and confirm it distinguishes "nothing exists" from "everything
was withheld".

**Acceptance Scenarios**:

1. **Given** only simulated cards exist, **When** the maintainer runs `robot-army cards`, **Then**
   the output states that rows were withheld and names the flag that shows them, instead of
   claiming no cards are tracked.
2. **Given** only simulated worktree records exist, **When** the maintainer runs
   `robot-army worktree list`, **Then** the output states that rows were withheld rather than
   claiming none are recorded.

---

### Edge Cases

- **Every row withheld, queue empty too**: simulated items exist but none are in a state the
  queue draws from. The listing must still disclose the withheld rows even though no visible
  section contradicts the claim of emptiness.
- **Filters exclude every withheld row**: the maintainer filters to a repo that has only real
  items while simulated items exist elsewhere. The withheld count must be zero for that
  invocation — reporting a number the flag would not reveal is a new contradiction, not a fix.
- **`--include-simulated` passed**: nothing is withheld, so no disclosure is printed and no
  empty parenthetical or zero-count noise appears.
- **Running at `live` with no simulated rows ever created**: output must be indistinguishable
  from today's, apart from the queue's simulated marking which has nothing to mark.
- **Simulated rows present but the maintainer never reaches the listing**: the queue section
  alone must be self-describing, so a reader who stops after the queue still knows those rows
  are simulated.
- **After `purge-simulated`**: withheld counts drop to zero and the disclosure disappears, with
  no stale count carried over.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A single invocation of `status` MUST NOT print two statements that cannot both be
  true. Specifically, it MUST NOT assert that no work items exist, or that none match, while the
  same output displays work items.
- **FR-002**: When `status` withholds simulated work items from its counts or its item listing,
  it MUST state that rows were withheld, MUST state how many, and MUST name the option that
  reveals them.
- **FR-003**: The withheld-row disclosure MUST appear whenever at least one row is withheld, not
  only when the visible listing is empty.
- **FR-004**: The withheld-row count MUST be computed under the same state and repository filters
  applied to the visible listing, so the number stated is the number the reveal option would
  surface.
- **FR-005**: When nothing is withheld — because none exist, or because the maintainer asked for
  them to be included — `status` MUST print no withheld-row disclosure at all.
- **FR-006**: When no work items exist at all, `status` MUST continue to say so plainly.
- **FR-007**: Simulated rows shown in the queue section MUST be visibly marked as simulated,
  consistent with the marking convention the item listing already uses.
- **FR-008**: The default MUST remain that simulated rows are excluded from counts and listings,
  and including them MUST remain an explicit opt-in. This feature changes what the command
  *says* about the rows it hides, not which rows it hides.
- **FR-009**: The queue section MUST continue to include simulated rows, because those rows
  occupy capacity and the queue must name the item the next dispatch would actually select.
- **FR-010**: The machine-readable status payload MUST carry the withheld-row count, so a
  consumer can distinguish "no work items exist" from "no work items are being shown".
- **FR-011**: Fields already present in the machine-readable status payload MUST retain their
  names and meanings; this feature adds to that payload and removes nothing from it.
- **FR-012**: The exit code of `status` MUST NOT change as a result of rows being withheld.
- **FR-013**: Other listings that withhold simulated rows by default — the card listing and the
  worktree listing — MUST likewise distinguish "nothing exists" from "everything was withheld".

### Key Entities

- **Work item**: a unit of queued work, carrying among other things a state, an owning
  repository, and a flag marking it as simulated rather than real.
- **Simulated row**: any persisted record created while the daemon was running below `live`.
  It counts against capacity and is real in the database, but is excluded from reporting by
  default and can be deleted wholesale by the purge operation.
- **Withheld-row count**: the number of records a given invocation matched but did not show,
  because they were simulated and simulated rows were not requested. Scoped to that
  invocation's filters.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every combination of effect level, filter, and simulated/real row mix, a single
  `status` invocation contains zero pairs of mutually contradictory statements. Verified by
  exercising the empty, all-simulated, mixed, and all-real cases.
- **SC-002**: A maintainer who sees a populated queue beside an empty listing can determine, from
  that one output alone and without running a second command or reading source, exactly how many
  rows were withheld and precisely how to see them.
- **SC-003**: 100% of simulated rows displayed anywhere in `status` output are visibly identified
  as simulated.
- **SC-004**: With no simulated rows present, `status` output is unchanged from its current form,
  so nothing about the everyday `live` view is disturbed.
- **SC-005**: A consumer of the machine-readable output can tell "nothing exists" apart from
  "everything was withheld" using only fields in a single response.
- **SC-006**: No invocation that withholds nothing prints a withheld-row notice, a zero count, or
  any other new noise.

## Assumptions

- The reported behavior is entirely a rendering defect. The queue's inclusion of simulated rows
  and the listing's exclusion of them are both deliberate and correct; only their unreconciled
  presentation is wrong. Neither behavior is changed by this feature.
- The purge operation remains the answer to simulated rows accumulating as history, so this
  feature does not touch retention, cleanup, or the lifetime of simulated records.
- The related web interface defect — that below `live` the interface renders as an empty system —
  is tracked separately as issue #14 and is out of scope here. The terminal and the interface are
  fixed independently; this spec constrains only what the terminal prints.
- "Withheld" is the correct framing rather than "hidden": the rows were matched and deliberately
  not shown, which is a fact about this invocation, not a property of the rows.
- The disclosure is intended for a maintainer reading a terminal, so it is one short line, not a
  warning banner, and it does not change the command's exit code or emit to standard error.
- Deriving the withheld count is cheap enough to be unconditional — the status command already
  runs several queries and one more comparison is not worth making conditional.
- No configuration option is added to turn the disclosure off. A surface that can be configured
  into lying is not a fix.
