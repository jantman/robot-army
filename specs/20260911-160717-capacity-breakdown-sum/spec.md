# Feature Specification: Capacity breakdown that sums to its total

**Feature Branch**: `robot-army/issue-61-capacity-breakdown-does-not-sum-to-its`

**Created**: 2026-09-11

**Status**: Draft

**Input**: GitHub issue #61 — "capacity breakdown does not sum to its own total: sessions with no
registry entry are counted but never shown".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every counted session appears in the breakdown (Priority: P1)

The author runs `robot-army capacity` (or reads `status`, or glances at the web chrome) to learn
why nothing is dispatching. The total says `6 of 7`; the breakdown today says `0 ours, 2 other`.
Four sessions are counted towards the cap and shown nowhere. After this change every session in
the total is attributed to a named component, so the author can explain the number that decides
dispatch from the screen that reports it.

**Why this priority**: This is the defect. A total that cannot be reconciled with its own
breakdown is a number the author cannot act on.

**Independent Test**: Seed a machine with registry sessions of ours, registry sessions of the
author's, simulated session rows and a real session row with no registry entry; the rendered
components sum to the rendered total on every surface.

**Acceptance Scenarios**:

1. **Given** two out-of-band sessions and four session rows with no registry entry, **When** the
   author runs `capacity`, **Then** the output lists the four under a named component and the
   listed components sum to 6.
2. **Given** the same machine, **When** the author runs `status` or opens any web view, **Then**
   the one-line summary's components also sum to its total.
3. **Given** an idle machine or one with only registry-visible sessions, **When** any surface
   renders capacity, **Then** the output is no noisier than today — components that are zero are
   not added to the one-line summaries.

---

### User Story 2 - Simulated rows are told apart from real launches in flight (Priority: P2)

A simulated session row holding a slot is expected to persist (issue #28 exists because such
rows can be left behind indefinitely); a real dispatch with no registry entry yet is expected to
resolve within seconds. The author needs to see which of the two is filling the machine,
because the remedies differ — purge the rehearsal rows, or wait.

**Why this priority**: Showing a single "unaccounted" figure would fix the arithmetic but still
hide the distinction that matters when this bites.

**Independent Test**: Seed one simulated row and one real starting row, neither in the registry;
the breakdown reports one of each under different names.

**Acceptance Scenarios**:

1. **Given** simulated session rows occupying slots, **When** the author runs `capacity`, **Then**
   they are reported as simulated, separately from real sessions.
2. **Given** a real session row whose worker has not yet written a registry entry, **When** the
   author runs `capacity`, **Then** it is reported as in flight, not as simulated.

---

### User Story 3 - The record of a hold carries the same breakdown (Priority: P3)

When dispatch is held because the machine is full, the audit record and the queue's hold reason
describe the counts. These carry the same components, so a hold read back from the log can be
explained as fully as one read from the screen.

**Why this priority**: Secondary surfaces; the log and the hold reason are read after the fact.

**Independent Test**: Hold dispatch at the global cap with simulated rows present; the hold
detail and the `dispatch.at_capacity` record both name the simulated count.

**Acceptance Scenarios**:

1. **Given** the global cap is reached with rows absent from the registry, **When** an item is
   held, **Then** its hold detail names each non-zero component and those components sum to the
   stated total.

### Edge Cases

- **Degraded (`/proc`) observation**: session ids are unavailable, so no registry entry matches
  any row; every one of our live rows falls into the no-registry components while `/proc`'s
  processes (ours included) fall into "other". The components still sum to the total, and the
  existing "ceiling rather than a fact" warning remains the explanation for the over-count.
- **Unobservable machine**: no breakdown is shown at all, as today.
- **A simulated row not yet confirmed** (still starting, no pid recorded) is indistinguishable
  from a real launch in flight and is reported as in flight until confirmed — a seconds-long window.
- **A real row whose worker has exited** but which reconciliation has not closed yet also has no
  registry entry; it is reported under the in-flight component, whose wording must not claim it
  is certainly launching.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The capacity observation MUST expose, besides "ours" and "other", a count of
  simulated session rows with no registry entry and a count of real session rows with no
  registry entry.
- **FR-002**: For every observable snapshot, ours + other + simulated + in flight MUST equal the
  total. This is asserted by test, not by convention.
- **FR-003**: The `capacity` command's terminal output MUST list all four components, each on its
  own line, as it lists "ours" and "others" today.
- **FR-004**: The `capacity` command's and `status`'s structured (JSON) output MUST carry the two
  new counts alongside `ours` and `others`, which keep their existing keys and meanings.
- **FR-005**: The one-line summaries (`status`, the web chrome pill, a global-cap hold's detail)
  MUST include each new component when it is non-zero, and MAY omit it when zero.
- **FR-006**: The `dispatch.at_capacity` audit record MUST carry the two new counts.
- **FR-007**: The total, the cap, and every dispatch decision MUST be unchanged; this feature
  changes reporting only.
- **FR-008**: The snapshot MUST still carry no handle to a session the system did not start; the
  new counts describe the system's own rows and are bare integers.

### Key Entities

- **Capacity snapshot**: gains two counts — *simulated* (rows launched by the simulated host) and
  *in flight* (real rows with no registry entry: a launch not yet registered, or a session that
  ended and has not yet been reconciled).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On every surface that shows a capacity breakdown, the printed components sum to the
  printed total in 100% of observable cases exercised by the test suite.
- **SC-002**: The situation in the issue (6 running, 0 ours, 2 other) is reported with the missing
  4 attributed and split between simulated and in flight.
- **SC-003**: No dispatch decision changes: the existing capacity and ordering tests pass
  unmodified apart from assertions on rendered text.

## Assumptions

- "Simulated" means the signature the simulated host writes (a rehearsal row with pid 0 and no
  recorded start time) — the same test `purge-simulated` and `cancel` already use. A `no-remote`
  row is `dry_run` but has a real worker and is therefore not simulated.
- Adding keys to the JSON output and the audit record is acceptable; no outside consumer is
  supported (Constitution V).
- The hold signature that de-duplicates `dispatch.at_capacity` records is not widened: the total
  already moves whenever a slot is taken or freed.
