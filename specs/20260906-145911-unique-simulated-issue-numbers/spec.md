# Feature Specification: Unique simulated issue numbers

**Feature Branch**: `robot-army/issue-22-simulated-issue-numbers-collide-and-the`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue #22 — "Simulated issue numbers collide and the retry scans linearly,
contradicting its own message" (labels: `bug`, `robot-army`)

## Context

At any effect level below `live`, filing a Trello card as a GitHub issue is simulated: nothing
reaches GitHub, and the system mints a recognisable fake issue number so the rest of the pipeline
rehearses the real path. That number is currently drawn from a counter that starts at zero in
every process, so every run mints the same sequence. The database refuses the duplicate mapping,
the card is left to be retried, and the retry draws the *next* number in the same sequence — which
is the next one already taken.

Observed on a real board: one card needed **eight passes over nineteen minutes** to be filed,
raised two `card_create_failing` anomalies on the way, and the reason recorded against it said
"The next pass retries with a fresh number", which is not what happens. The cost grows with the
number of simulated cards a repository already holds.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A rehearsal files each card on its first pass (Priority: P1)

The maintainer runs the daemon at a simulated effect level against a board that already holds
simulated cards for a repository. A new card arrives. It is filed on the first pass that reaches
it, whether the repository holds one prior simulated card or fifty, and whether or not the daemon
has been restarted since those cards were filed.

**Why this priority**: This is the defect. Everything else in the issue is a consequence of it.

**Independent Test**: Seed a database with N simulated cards already mapped for one repository,
run one intake pass over a fresh card, and assert the card reaches its linked state with a number
no other row holds — with N large enough that the old behaviour would need N failed passes.

**Acceptance Scenarios**:

1. **Given** a repository already holding simulated mappings for numbers 900001–900008, **When** a
   new card is filed in a freshly started process, **Then** the card is linked on that pass and its
   number collides with none of them.
2. **Given** two cards resolved to the same repository in one pass, **When** both are filed,
   **Then** each receives a distinct number and both are linked on that pass.
3. **Given** a card was filed in an earlier process, **When** the process is restarted and another
   card for the same repository is filed, **Then** the second card does not receive the first
   card's number.
4. **Given** the same board is rehearsed against two different repositories, **When** cards are
   filed for each, **Then** numbering for one repository is unaffected by the other's rows.

---

### User Story 2 - A card's number does not depend on unrelated simulated traffic (Priority: P2)

The number a card receives is decided by what numbers are already taken, not by how many other
simulated actions the process happened to perform first.

**Why this priority**: It is the second quirk named in the issue, and the reason two identical runs
can produce different numbers for the same card. It is a smaller defect than P1 but shares the same
fix site.

**Independent Test**: File a card in a process that performed simulated comments beforehand, and in
one that did not, and assert the card receives the same number in both.

**Acceptance Scenarios**:

1. **Given** a process that has recorded several simulated comments, **When** a card is filed,
   **Then** its issue number is the same as it would have been with no comments recorded.
2. **Given** simulated comments are recorded, **When** their would-be URLs are read from the log,
   **Then** each is still distinguishable from the others.

---

### User Story 3 - The recorded reason describes what will actually happen (Priority: P3)

When a mapping is nevertheless refused, the reason stored against the card and shown by
`robot-army cards` states what the system will actually do next, in terms a reader can check
against the log.

**Why this priority**: It cannot mislead anyone until a refusal happens, and after P1 refusals
should be rare. It still has to be true.

**Independent Test**: Force a mapping refusal and assert the recorded reason describes the actual
recovery behaviour and names the card holding the number.

**Acceptance Scenarios**:

1. **Given** a mapping is refused because another card holds the number, **When** the reason is
   recorded, **Then** it names the holding card and states accurately what the next pass does.
2. **Given** the reason text, **When** it is compared against the system's behaviour, **Then** no
   sentence in it claims a recovery strategy the system does not have.

---

### Edge Cases

- **A repository with no simulated rows yet**: the first card receives the first number above the
  recognisable base, not an arbitrary one.
- **Real and simulated rows in the same repository**: allocation considers only rows of the same
  kind, so a live issue number can never be handed to a simulated card and vice versa.
- **Gaps left by deleted cards**: a number no row holds is available regardless of whether higher
  numbers are in use; the system must not fail merely because the sequence is not contiguous.
- **A collision that survives allocation** (a row appears between the allocation and the write):
  the card is left to be retried with its reason recorded, exactly as today, and the next pass
  allocates afresh rather than incrementing.
- **A card already carrying a number** that is being retried after an earlier failure: it is filed
  under a newly allocated number rather than the one that failed.
- **Numbers that would exceed the recognisable simulated range**: the number stays recognisably
  simulated no matter how many rows exist.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A simulated issue number MUST be chosen so that, at the moment it is minted, no
  existing mapping for the same repository and the same simulated/real kind holds it.
- **FR-002**: Allocation MUST consult the recorded mappings rather than a per-process count, so
  that restarting the process cannot regenerate a number already in use.
- **FR-003**: Filing a card MUST NOT require more than one attempt because of number collision,
  whatever number of simulated cards the repository already holds.
- **FR-004**: Two cards filed in the same pass for the same repository MUST receive distinct
  numbers.
- **FR-005**: The number a card receives MUST NOT be influenced by how many simulated comments (or
  other simulated actions) the process performed beforehand.
- **FR-006**: A simulated issue number MUST remain unmistakable as simulated when read in a log,
  as it is today.
- **FR-007**: The reason recorded when a mapping is refused MUST describe the system's actual
  recovery behaviour and MUST continue to name the card already holding the number.
- **FR-008**: Minting a simulated issue MUST continue to be recorded in the audit log, marked
  simulated, carrying the number it would have returned.
- **FR-009**: Behaviour at the `live` effect level MUST be unchanged: real issue numbers come from
  GitHub and nothing in this change touches that path.
- **FR-010**: A refusal that still occurs MUST continue to leave the card retryable with its
  failure recorded, and MUST NOT abort the rest of the pass.

### Key Entities

- **Card mapping**: the recorded association of a board card with a repository and issue number,
  distinguished by whether it is simulated. Uniqueness of (repository, issue number, simulated) is
  the invariant this feature stops violating.
- **Simulated issue number**: a fake but structurally valid issue number, drawn from a high offset
  so it cannot be mistaken for a real one.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With any number of simulated cards already mapped for a repository, a new card is
  filed on the first pass that reaches it — one attempt, not N.
- **SC-002**: No `card_create_failing` anomaly is raised as a consequence of number collision in a
  rehearsal run.
- **SC-003**: Restarting the process and filing another card produces no repeated number.
- **SC-004**: The same card filed twice under identical recorded state receives the same number,
  regardless of unrelated simulated activity in the process.
- **SC-005**: Every sentence of the refusal reason is verifiable against the system's behaviour.
- **SC-006**: The full test suite passes, including tests that fail against the current behaviour.

## Assumptions

- Only one daemon process operates on the database at a time; the single-instance lock already
  guarantees this, so allocation does not need to defend against a concurrent writer beyond the
  existing uniqueness constraint and its retry path.
- The uniqueness constraint stays as the last line of defence. Allocation makes collisions
  unreachable in normal operation; it does not replace the constraint or the retry that follows it.
- Existing simulated rows in a live database are left as they are. Nothing renumbers them, and no
  migration of past rows is required.
- The high offset that marks a number as simulated does not change, so logs and existing tests that
  recognise simulated numbers by that offset keep working.
