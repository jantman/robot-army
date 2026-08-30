# Feature Specification: Liveness Is Checked Wherever the Session Is Real

**Feature Branch**: `issues/33`

**Created**: 2026-08-30

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#33 — "Reconciliation never checks session liveness below `live`: at `no-remote` a dead session stays `active` forever." Found during the issue #1 verification round, running 001 quickstart scenario 4 at `effect_level = "no-remote"`.

**Baseline**: written against `main` at 15bf843, which includes the merged fix for #28 (PR #43, `specs/015-reclaim-session-slots/`) and #34's confirmed stop. Verified in that tree: the `dry_run` skip this feature is about is intact, and `_orphan_sweep`'s `running` guard is unchanged. See Assumptions for what #28 settled and what it deliberately left here.

## User Scenarios & Testing *(mandatory)*

<!--
  Four stories. Story 1 is the defect: the safety sweep that notices a dead worker is off at
  the one level the quickstart tells people to rehearse in. Story 2 is the invariant the
  current skip was written to protect, separated out because the fix is only correct if
  rehearsals at the two lowest levels stay quiet. Story 3 was rewritten after Phase 0
  measurement disproved its first draft: the orphan case it originally claimed already works,
  and the real hole is a superseded attempt that no sweep ever visits (research.md R7). Story 4
  is the documentation the same test round found to be describing behaviour this machine no
  longer exhibits.
-->

### User Story 1 - A dead worker is noticed wherever the worker was real (Priority: P1)

The maintainer rehearses at the level the quickstart recommends for exactly this — a real
session on the machine, nothing written back to GitHub. A worker dies: the maintainer kills it,
it crashes, the machine reboots, the terminal that hosted it goes away and takes it with it.

Today nothing notices. The item stays `active` indefinitely, `status` reports a session against
a process identifier that belongs to nothing, the capacity slot the session held is never given
back, and no anomaly is raised. The system had every fact it needed — no socket, no registry
entry, no exit record — and reported a clean pass.

After this change, a session that was a real process is checked for liveness on every
reconciliation pass, whatever effect level created it. When it is gone and no exit was ever
reported, the item becomes `interrupted`, the session record is closed as lost, the slot is
returned, and the reason is in the log.

**Why this priority**: This is the defect, and it disables the project's core safety property
in the mode people are told to test in. An item that is permanently `active` against a dead
worker is a queue that silently stops moving and a status display that lies.

**Independent Test**: At `no-remote`, dispatch an item, kill the session by any means, run
reconciliation, and confirm the item is `interrupted`, the session is closed, and the capacity
slot is free.

**Acceptance Scenarios**:

1. **Given** an `active` item at `no-remote` whose session process has been killed, **When**
   reconciliation runs, **Then** the item is marked `interrupted`, the session record is
   closed as lost, and the reason names the missing evidence.
2. **Given** that same item, **When** reconciliation has run, **Then** the capacity accounting
   no longer counts its session, globally and for its repository, and the next eligible item
   for that repository can be dispatched.
3. **Given** an `active` item at `no-remote` whose session process is still alive, **When**
   reconciliation runs, **Then** nothing about the item or its session changes.
4. **Given** an `active` item whose session has already recorded an exit, **When**
   reconciliation runs, **Then** the recorded exit stands and the item is not marked
   interrupted on the strength of the process being gone.
5. **Given** the same kill at `live`, **When** reconciliation runs, **Then** the outcome is
   exactly what it was before this change.

---

### User Story 2 - Rehearsing without a real session stays quiet (Priority: P2)

The maintainer rehearses at `plan` or `local`, where no session is ever really launched. Those
items must move through the same states by the same code path as live ones, and must not be
torn down by a sweep looking for a process that was never going to exist.

**Why this priority**: This is the invariant the current skip exists to protect, and the fix is
wrong if it breaks it — every simulated item would be marked interrupted on the next pass.
Story 1 is not deliverable without it, but it is a separate observable outcome and deserves its
own test.

**Independent Test**: At `plan` and at `local`, dispatch items, leave them, run reconciliation
repeatedly, and confirm they stay `active` with their sessions untouched.

**Acceptance Scenarios**:

1. **Given** an `active` item at `plan` or `local`, **When** reconciliation runs any number of
   times, **Then** the item stays `active` and its session record is not closed.
2. **Given** such an item, **When** reconciliation runs, **Then** the pass record shows the
   session was skipped because it never had a process, distinguishably from a session that was
   checked and found alive.

---

### User Story 3 - A superseded attempt's worker is not left unwatched (Priority: P2)

An item is resumed or restarted, so it gets a second session. The first attempt's worker was
supposed to be gone — but if it survived, it is still running under the same checkout, editing
the same files, while everything the maintainer can see describes the *new* attempt.

Nothing looks at it. The liveness sweep examines only an item's most recent session, so an
earlier attempt's record is never loaded. The sweep for unclaimed live workers then passes over
the process, because that record still says `running` — and it stays `running` precisely because
nothing ever visits it. The two blind spots hold each other up.

After this change, every open session an item still owns is examined, not only its newest. A
superseded attempt whose worker is gone has its record closed and its capacity slot returned. A
superseded attempt whose worker is *alive* is reported as an orphan and its record deliberately
left open, because a count lower than the number of live workers is the one capacity error that
does real harm.

**Why this priority**: P2 rather than P3, because measurement moved it. This is not a
consequence of story 1 that disappears when story 1 lands — it is a second, independent hole
that survives it, and it hides exactly what the project exists to prevent: an autonomous agent
editing a repository with nothing watching it. It is below story 1 only because it needs a
resume or restart to occur, which is rarer than a session dying.

**Independent Test**: Give an `active` item two open sessions, leave the older one's process
alive, and confirm reconciliation reports it as an orphan while leaving the newer attempt's
handling unchanged.

**Acceptance Scenarios**:

1. **Given** an `active` item with an open session from a superseded attempt whose process is
   still alive, **When** reconciliation runs, **Then** an orphan anomaly names that session and
   its record is left open.
2. **Given** the same item, **When** reconciliation runs, **Then** the worker is reported once —
   not additionally by the sweep for unclaimed live workers.
3. **Given** an `active` item with an open session from a superseded attempt whose process is
   gone, **When** reconciliation runs, **Then** that record is closed as lost, its capacity slot
   is returned, and the item's state is decided by its current attempt alone.
4. **Given** an `active` item with several open sessions that never had a process, **When**
   reconciliation runs, **Then** none of them is closed or reported.
5. **Given** an `active` item with exactly one open session, **When** reconciliation runs,
   **Then** its handling is identical to what it is today.

---

### User Story 4 - The terminal-death rehearsal describes what actually happens (Priority: P3)

The maintainer follows the documented terminal-death scenario expecting the outcomes it states.
On this machine the documented outcome no longer occurs: killing the wrapper takes the worker
with it rather than leaving it reparented, so the orphan the scenario is designed to produce
does not appear, and a reader cannot tell a broken system from a stale document.

**Why this priority**: Documentation, not behaviour — but this document is the acceptance test
for stories 1 and 3, and an acceptance test that cannot distinguish success from failure is
worse than none.

**Independent Test**: Follow the scenario as written on this machine and confirm each stated
expectation is the one observed.

**Acceptance Scenarios**:

1. **Given** the terminal-death scenario as documented, **When** the maintainer follows it
   literally, **Then** every stated expectation matches what the system does.
2. **Given** the scenario's orphan case, **When** the maintainer follows it, **Then** it
   produces an orphan by a route that works against current behaviour, or states plainly that
   it cannot and says what to check instead.

---

### Edge Cases

- **A session recorded before this change.** Existing records were written with only the
  simulated flag to go on. They must be classified correctly on the first pass after the
  change, without the maintainer running a backfill or editing the store by hand.
- **The effect level changed between runs.** An item dispatched at `no-remote` is reconciled by
  a daemon later started at `local`, or the reverse. The decision must follow the fact recorded
  on that session when it was created, not the level the current process happens to be running
  at.
- **A recycled process identifier.** A dead session's identifier is reused by an unrelated
  process. Liveness must continue to rest on evidence stronger than the identifier alone, as it
  already does at `live`.
- **The registry cannot be read.** When the session registry directory is missing, unreadable,
  or its format unrecognised, the evidence liveness rests on is gone — and reconciliation reads
  its absence as death. Measured at this baseline: a vanished registry already marks *every*
  active item interrupted at `live`, and does not at `no-remote` only because the skip this
  feature removes was masking it. So the exposure is pre-existing rather than introduced, but it
  becomes reachable at a second level here. This is a **deliberate, recorded acceptance**: the
  two levels are made to behave alike, and the hazard itself is tracked as issue #44 rather
  than fixed by widening this feature. See research.md R8.
- **A session under an item that is no longer `active`.** This was issue #28 and is now fixed
  on `main`: such a record is reclaimed, and a live worker beneath it is reported as an orphan.
  The interaction runs one way and must stay that way — this feature moves a record off
  `running` *earlier* in the same pass, after which the #28 sweep correctly declines it as
  already settled. A change that reversed that order, or that closed the record without the
  liveness check, would either double-report the worker or hide it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Reconciliation MUST decide whether to check a session's liveness from whether
  that session had a real process, and MUST NOT decide it from whether the record is flagged
  simulated.
- **FR-002**: Each session record MUST carry, from the moment it is created, whether its host
  was real, so that reconciliation can make that decision from the record alone.
- **FR-003**: Reconciliation MUST NOT consult, derive, or branch on the effect level. As the
  existing effect-level requirement already demands, the level MUST continue to be enforced only
  where real and simulated behaviour is selected, and never by a condition in calling code.
- **FR-004**: For every `active` item whose session had a real process, reconciliation MUST
  determine whether that process is still alive, at every effect level, using evidence stronger
  than a process identifier alone.
- **FR-005**: An `active` item whose real session is neither alive nor holding a recorded exit
  MUST be marked `interrupted`, and its session MUST be closed as lost, identically to the way
  this already happens at the fully live level.
- **FR-006**: A session whose host was simulated MUST NOT be marked lost, and its item MUST NOT
  be marked `interrupted`, on the grounds that no process was found.
- **FR-007**: Closing a session as lost MUST release the capacity it held, both globally and
  for its repository, so the next eligible item can be dispatched.
- **FR-008**: Session records written before this change MUST be classified correctly by the
  first reconciliation pass that follows it, with no manual backfill or migration step required
  of the maintainer.
- **FR-009**: Each reconciliation pass MUST report how many sessions it skipped because they
  never had a process, as a figure distinct from the ones the pass already reports — the count
  it checked, the count it interrupted, and the count it reclaimed. A pass that examined nothing
  MUST NOT be reportable as a pass that examined everything, which is the exact misreading the
  originating issue's `checked: 2, interrupted: 0` invited.
- **FR-010**: Every liveness outcome — alive, dead, or skipped as never-real — MUST be
  reconstructable from the durable record alone, naming the session and the evidence the
  decision rested on.
- **FR-011**: Reconciliation MUST examine every open session record an item still owns, not
  only its most recent attempt.
- **FR-017**: An open session from a superseded attempt whose process is alive MUST be reported
  as an orphan and MUST NOT have its record closed, because reporting fewer running sessions
  than exist oversubscribes the quota the capacity cap protects.
- **FR-018**: An open session from a superseded attempt whose process is gone MUST have its
  record closed and its capacity slot returned, and MUST NOT influence the item's own state.
- **FR-019**: A worker reported as an orphan MUST be reported exactly once per pass, whichever
  part of reconciliation detects it.
- **FR-012**: Reported state MUST NOT show a session as running once reconciliation has
  concluded it is gone, in any interface that reports session state.
- **FR-013**: Behaviour at the fully live effect level MUST be unchanged by this feature.
- **FR-015**: The liveness determination MUST reuse the single established means of observing
  whether a recorded session is running, rather than introducing a second one alongside it.
- **FR-016**: The reclamation of sessions under items that have left `active`, delivered by the
  merged #28 work, MUST continue to behave exactly as it does today, and a worker it already
  reports MUST NOT be reported twice.
- **FR-014**: The documented terminal-death rehearsal MUST state the outcomes the system
  actually produces on the target machine, and its orphan case MUST either be reachable as
  written or say plainly that it is not and what to verify instead.

### Key Entities

- **Session record**: The system's account of one worker launch — which item it belongs to,
  what state it is in, what process it ran as, and, newly, whether that process was ever real.
- **Work item**: The unit of work whose state (`active`, `interrupted`, and the rest) is what
  the maintainer reads; the thing left stranded when a session's death goes unnoticed.
- **Effect level**: The graduated setting from intentions-only to fully live that selects, per
  boundary, whether an action is really performed. The session host is real at the top two
  levels and simulated at the lower two; the simulated flag on a record is set by the level as
  a whole and therefore does not answer the session-host question.
- **Capacity slot**: The global and per-repository allowance a running session occupies, and
  which an unnoticed death withholds indefinitely.
- **Anomaly**: The durable, acknowledgeable report of something the system found wrong but
  cannot itself resolve — here, a live worker that nothing claims.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At every effect level at which a session is a real process, a session ended by
  any means — clean exit, crash, kill, reboot — is reflected in the maintainer's view of the
  system within one reconciliation interval, in 100% of attempts.
- **SC-002**: After such a death is reconciled, the capacity figures return to what they were
  before that item was dispatched, and the next eligible item for the same repository is
  dispatched on the following pass rather than being held indefinitely.
- **SC-003**: Across at least ten consecutive reconciliation passes with items outstanding at
  the two levels that launch no real session, zero items are marked interrupted and zero
  sessions are closed.
- **SC-004**: No interface reports a session as running whose process does not exist, verified
  at all four effect levels.
- **SC-005**: A reader of a single reconciliation pass's record can tell, for every outstanding
  item, whether its session was checked and found alive, checked and found dead, or skipped
  because it never had a process — without re-running anything.
- **SC-006**: A live worker that no current attempt accounts for — including one belonging to a
  superseded attempt of an item that is still `active` — is reported as an anomaly within one
  reconciliation interval, once rather than once per pass, at every level at which sessions are
  real.
- **SC-007**: The documented terminal-death rehearsal, followed literally on the target
  machine, produces every outcome it states.
- **SC-008**: A single live orphaned worker produces exactly one open anomaly, whatever the
  state of the item above it, across repeated reconciliation passes.

## Assumptions

- The distinguishing fact is *whether this session's host was real*, which is a property of the
  session at the moment it was created. Recording it on the session, rather than re-deriving it
  during reconciliation, is what keeps the effect level confined to one module as FR-003 and the
  existing boundary design require.
- Existing records can be classified without a data migration: a session that never had a real
  host was already recorded with an absent process identifier, so the stored record already
  contains a truthful answer for rows written before this change.
- Issue #28 merged before this feature was planned, as PR #43. It was a gap in *which* sessions
  are swept; this one is a gap in *whether* a swept session is checked. Three things it settled
  that this feature now builds on rather than repeats: a session under an item outside
  `dispatching`/`active` is reclaimed; a live worker beneath such an item raises an orphan; and
  the pass reports what it reclaimed. Two things it deliberately left: the skip that makes this
  feature necessary, and the sweep guard that story 3 is about — its research declined to narrow
  that guard, on the grounds that deciding which cases it wrongly suppresses was a larger
  question than #28. This feature answers that question for the `active` case only.
- #28 established how an open session row's liveness is decided without consulting the simulated
  flag — it asks the observation the pass already took whether that session is running. This
  feature extends the same means to the `active` sweep rather than adding a second one, which is
  what FR-015 requires and what keeps one pass from holding two opinions about what is alive.
- No new effect level, and no change to which boundaries are real at which level, is required or
  intended.
- The reconciliation interval and the meaning of `interrupted` are unchanged: interrupted items
  are still never resumed automatically, and resuming, abandoning, or restarting remains an
  explicit human action.
- The target remains one maintainer on one Linux machine; there is no obligation to preserve
  behaviour for any outside consumer of the store or of the command output.
