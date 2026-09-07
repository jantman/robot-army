# Feature Specification: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Feature Branch**: `robot-army/issue-44-reconciliation-reads-an-unobservable`

**Created**: 2026-09-07

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#44 — "Reconciliation reads an unobservable registry as proof every session is dead." Found while planning #33 (`specs/20260830-133818-reconcile-session-liveness/`, research.md R8) and split out deliberately, because fixing it changes `live` behaviour and #33 was scoped by FR-013 to leave `live` untouched.

**Baseline**: written against `main` at 60f2914, in which #33 has landed. Verified in that tree: the `dry_run` skip #33 replaced is gone and the active-item sweep now runs at every effect level, `reconcile.py` contains no reference to `scan.degraded` or `scan.directory_missing`, and `capacity.py` acts on both. The measurement in the issue was taken before #33 merged; the row it predicts — `no-remote` behaving exactly as `live` does — is now the shipped behaviour, and the mask that hid two of the four cases is gone.

## User Scenarios & Testing *(mandatory)*

<!--
  Four stories, and the order is the order of harm.

  Story 1 is the reported defect: a registry nobody can read is treated as proof that every
  session on the machine is dead, so every active item is interrupted in one pass.

  Story 2 is the same root cause reaching a second family of sweeps, found while reading the
  post-#33 tree. Those sweeps close session rows rather than interrupting items, which under-
  counts capacity — the one direction #28 established as genuinely harmful. It is separated
  because the harm is different in kind and is tested differently.

  Story 3 is the record. #33's plan enumerated the missing distinction between "observed dead"
  and "could not observe" as an accepted Principle III gap, on the explicit ground that closing
  it was tracked here. This is where it gets closed.

  Story 4 is the invariant the fix must not break. An idle machine and a vanished registry are
  byte-for-byte identical at the glob unless the distinction is respected, and a fix that
  declines to conclude on *both* would silently switch off the safety sweep #33 exists for.
-->

### User Story 1 - A registry nobody can read does not interrupt everything (Priority: P1)

The maintainer has three items running. Something moves the session registry out from under
the daemon: `XDG_RUNTIME_DIR` differs after a re-login, the directory has not been created yet
on a machine where the worker has not run since boot, a permission change makes it unlistable,
or it is simply gone.

Today reconciliation reads that as an empty machine. Within one pass every `active` item is
marked `interrupted` and every session record is closed as lost. Nothing on any surface says
the sweep was blind rather than informed. The maintainer is left resuming, by hand, items that
were never interrupted — while the workers they name are still editing files.

After this change, a pass that could not observe the registry declines to conclude that
anything died. Items stay as they are, session records stay open, and the next pass that *can*
see the registry reaches its conclusions normally.

**Why this priority**: This is the reported defect, and its blast radius is every running item
at once. It is recoverable — no worktree is touched and nothing is resumed automatically — but
it is silent and wholesale, which is the combination that costs the most to recover from.

**Independent Test**: Give reconciliation three `active` items with live-looking sessions and a
registry directory that does not exist. Confirm all three are still `active` afterwards, and
that the same items against an empty-but-present directory are still interrupted.

**Acceptance Scenarios**:

1. **Given** `active` items whose sessions look live and a registry directory that is absent,
   **When** reconciliation runs, **Then** no item is marked `interrupted` on the strength of
   that pass and no session record is closed as lost.
2. **Given** the same items and a registry directory that exists but cannot be listed, **When**
   reconciliation runs, **Then** the outcome is the same as scenario 1.
3. **Given** the same items and a scan that fell back to process enumeration because the
   registry version was unrecognised, **When** reconciliation runs, **Then** the outcome is the
   same as scenario 1.
4. **Given** the registry becomes readable again, **When** the next reconciliation pass runs,
   **Then** it reaches exactly the conclusions it would have reached had the blind pass never
   happened.
5. **Given** an `active` item with no session record at all, **When** reconciliation runs
   against an unobservable registry, **Then** it is still marked `interrupted` — that
   conclusion is drawn from the database and does not depend on the registry.

---

### User Story 2 - A blind pass does not hand back slots that are still taken (Priority: P1)

The same unobservable registry reaches two further sweeps, which do not interrupt items but do
close session records: the one that reclaims a record whose work item is no longer running
anything (#28), and the one that settles an attempt an item has already superseded (#33). Both
ask the registry whether the session is there, and both read "not there" out of an observation
that saw nothing at all.

Closing a record whose worker is alive reports fewer running sessions than exist. The cap is
then measured against a number lower than reality and dispatch oversubscribes the very
subscription quota the cap protects. #28 settled that an under-count is the only direction of
capacity error that does real harm; this is that error, arriving from the other side.

After this change, neither sweep closes a record on a pass that could not observe.

**Why this priority**: The harm is quieter than Story 1 but worse in kind — Story 1 leaves work
stalled and visible, this one spends money on a subscription the maintainer believed was
capped. It shares a root cause and a fix with Story 1, but it is a separate observable outcome
with separate tests.

**Independent Test**: Give reconciliation open session records whose work items are finished,
against an unobservable registry, and confirm the records are left open and the reported
capacity is unchanged. Repeat against an empty-but-present directory and confirm they are
reclaimed as they are today.

**Acceptance Scenarios**:

1. **Given** an open session record under a work item that is no longer running one and a
   registry that cannot be observed, **When** reconciliation runs, **Then** the record is left
   exactly as found and the slot it holds is still counted.
2. **Given** an `active` item with a superseded earlier attempt still open and a registry that
   cannot be observed, **When** reconciliation runs, **Then** the earlier attempt's record is
   left exactly as found.
3. **Given** either of those against an empty-but-present registry directory, **When**
   reconciliation runs, **Then** the record is reclaimed exactly as it is today.
4. **Given** a session record that never had a process, **When** reconciliation runs against an
   unobservable registry, **Then** it is settled exactly as it is today — a record with no
   process is not waiting on the registry to say anything about it.

---

### User Story 3 - A pass that could not see says so, once (Priority: P2)

The maintainer looks at `robot-army anomalies` and at the log, and wants to know why a pass
concluded nothing. Today the two conditions are indistinguishable after the fact: a pass across
a healthy idle machine and a pass across a registry that had moved write the same record, and
the mass interruption the second one caused reconstructs as a genuine finding.

After this change, a pass that could not observe records that fact in its own pass summary,
records how many liveness conclusions it withheld, and raises one anomaly so the condition
appears on the list of things needing attention. When a later pass can see the registry again,
that anomaly is retracted rather than left to go stale.

**Why this priority**: Stories 1 and 2 make the pass harmless; this makes it visible. Without
it the maintainer's queue quietly stops advancing with nothing anywhere saying why, which is a
different failure with the same cause. It is P2 rather than P1 only because it cannot corrupt
anything.

**Independent Test**: Run reconciliation against an unobservable registry, then read the pass
summary and the anomaly list. Run it repeatedly and confirm the anomaly count does not grow.
Restore the registry, run again, and confirm the anomaly is retracted.

**Acceptance Scenarios**:

1. **Given** a registry that cannot be observed, **When** reconciliation runs, **Then** its
   pass summary states that the registry was unobservable, why, and how many liveness
   conclusions were withheld.
2. **Given** the same, **When** reconciliation runs, **Then** exactly one anomaly is raised
   naming the condition.
3. **Given** the same, **When** reconciliation runs sixty more times, **Then** the number of
   open anomalies of that kind does not grow.
4. **Given** an open anomaly of that kind, **When** a later pass observes the registry
   successfully, **Then** the anomaly is retracted and no longer appears in the listing.
5. **Given** an observable registry, **When** reconciliation runs, **Then** the pass summary
   says the registry was observable and no anomaly of that kind is raised.

---

### User Story 4 - An idle machine is still an idle machine (Priority: P2)

The ordinary case is a registry directory that exists and is empty, because nothing is running.
Reconciliation must go on concluding from that exactly as it does today — that is the safety
sweep #33 exists for, and a fix that declined to conclude whenever it saw nothing would switch
it off permanently on any machine with no sessions.

**Why this priority**: This is the invariant the fix is measured against. The whole content of
the distinction being restored is that "absent" and "empty" are different; a fix that treats
them alike in the other direction is the same bug with the sign flipped.

**Independent Test**: With an empty-but-present registry directory, reproduce the behaviour the
#33 test suite asserts and confirm every outcome is byte-for-byte what it is today.

**Acceptance Scenarios**:

1. **Given** an `active` item whose session process is gone and an empty-but-present registry
   directory, **When** reconciliation runs, **Then** the item is marked `interrupted` and the
   session closed as lost, exactly as today.
2. **Given** an observable registry with entries, **When** reconciliation runs, **Then** every
   sweep behaves exactly as it does today — a session absent from a registry that *was* read is
   still absent.
3. **Given** any observable registry, **When** reconciliation runs, **Then** the pass summary
   carries the same counters it carries today, with the new ones reading as "nothing withheld".

---

### Edge Cases

- **The registry directory exists but cannot be listed.** Treated as unobservable, not as
  empty. The distinction the underlying scan draws already covers absent, not-a-directory, and
  unlistable as one condition, because all three mean the same thing to a reader: no usable
  registry here.
- **The scan fell back to process enumeration.** That path cannot recover session identifiers
  at all, so every database join finds nothing and produces the identical wholesale conclusion
  even though processes were seen. It counts as unobservable *for these sweeps* regardless of
  how many processes it found.
- **A recovery fallback is deliberately not attempted.** Capacity re-scans via process
  enumeration when the registry is unusable, because a count of processes answers its question.
  It does not answer this one: without session identifiers nothing can be matched to a record,
  so there is nothing to recover and declining to conclude is the whole of the remedy.
- **The registry is unobservable on some passes and not others.** Each pass decides for itself
  from its own observation. A blind pass withholds; the next readable pass concludes normally.
  Nothing is remembered between passes beyond the anomaly, which is retracted on the first pass
  that can see.
- **An `active` item with no session record at all.** Still interrupted. That conclusion comes
  from the database, not the registry, and withholding it would be a different bug.
- **A session record with no recorded process.** Unchanged. The existing rule — a session that
  never had a process has none to be alive — is decided from the record and does not consult
  the registry.
- **Sweeps that already fail safe.** The sweep that ends a finished worker and the sweep that
  reports unaccounted-for live processes both do nothing when the scan is empty, so a blind
  pass already produces no false conclusion from either. They must stay that way, and must not
  acquire a guard they do not need.
- **A blind pass and a genuinely idle machine both find nothing.** Only the flags distinguish
  them, which is exactly why the flags exist and why this feature is about consulting them
  rather than about counting entries.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Reconciliation MUST determine, once per pass, whether its registry observation
  was usable, and MUST derive that from the conditions the scan already reports — the registry
  directory being absent, not a directory, or unlistable; and the scan having fallen back to
  process enumeration.
- **FR-002**: When the observation was not usable, reconciliation MUST NOT mark any work item
  `interrupted` on the ground that its session was absent from the registry.
- **FR-003**: When the observation was not usable, reconciliation MUST NOT close any session
  record as lost on the ground that the session was absent from the registry. This covers the
  active-item sweep, the sweep that reclaims records outliving their work item, and the sweep
  that settles superseded attempts.
- **FR-004**: Conclusions that do not depend on the registry MUST be unaffected by an
  unusable observation — specifically, an `active` item with no session record at all, a
  session record with no recorded process, and every sweep that reads only the database.
- **FR-005**: When the observation was usable, every sweep MUST behave exactly as it does
  today. An empty-but-present registry directory is a usable observation.
- **FR-006**: Reconciliation MUST NOT attempt a process-enumeration fallback to recover from an
  unusable registry observation.
- **FR-007**: The pass summary record MUST state whether the registry observation was usable
  and, when it was not, why.
- **FR-008**: The pass summary record MUST report how many liveness conclusions were withheld
  because the observation was unusable, counted so that a pass which withheld nothing is
  distinguishable from a pass which had nothing to withhold.
- **FR-009**: A pass whose observation was unusable MUST raise an anomaly naming the condition,
  and repeated passes in the same condition MUST NOT accumulate additional open anomalies.
- **FR-010**: An open anomaly of that kind MUST be retracted by a later pass whose observation
  was usable, so it leaves the list of things needing attention without a maintainer acting on
  it.
- **FR-011**: The anomaly MUST carry enough detail to reconstruct the pass without re-running
  it: which condition made the observation unusable, and how many conclusions were withheld.
- **FR-012**: Behaviour MUST NOT depend on the effect level. Reconciliation does not consult
  it, and this feature does not introduce a first consultation; a rehearsed pass and a live one
  in the same registry condition withhold the same conclusions.
- **FR-013**: No configuration key is introduced. Declining to conclude from an observation
  that failed is not a policy the maintainer chooses between.

### Key Entities

- **Registry observation**: what one pass managed to read of the session registry, and whether
  that reading is worth drawing conclusions from. Already carries the two flags that answer the
  second question; this feature is about a consumer that ignores them.
- **Withheld conclusion**: a liveness judgement a sweep would have made and did not, because
  the observation it would have rested on saw nothing. Counted per pass and reported.
- **Unobservable-registry anomaly**: the standing report that reconciliation is running blind.
  Raised once, retracted by the first pass that can see.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With three running items and a registry directory that is absent, unlistable, or
  of an unrecognised version, a reconciliation pass interrupts zero of them. Measured today at
  three of three in every one of those conditions.
- **SC-002**: In the same conditions, a pass closes zero session records, so the capacity
  reported after the pass equals the capacity reported before it.
- **SC-003**: With an empty-but-present registry directory, every outcome is identical to the
  outcome before this change, across the whole existing test suite.
- **SC-004**: From the pass record alone, without re-running anything, a reader can tell a pass
  that observed an idle machine from a pass that observed nothing — a distinction that is
  impossible today.
- **SC-005**: A machine left in the unobservable condition for a day accumulates one open
  anomaly, not one per pass.
- **SC-006**: A registry that returns after any number of blind passes needs no maintainer
  action: the following pass concludes normally and the anomaly is gone from the listing.

## Assumptions

- **#33 has landed and its behaviour is the baseline.** The active-item sweep now runs at every
  effect level, so the four measured rows in the issue have collapsed to one behaviour. This
  feature changes that behaviour at every level, including `live` — which #33's FR-013 forbade
  it from doing and which is the whole reason this was split out.
- **The two flags the scan already reports are sufficient to answer "was this observation
  usable".** No new observation, no second scan, and no new field on the scan are needed. This
  is the assumption the fix is smallest under, and it is the one the existing capacity code
  already relies on.
- **The unusable conditions are treated as one.** An absent directory and a degraded fallback
  differ in cause and are reported distinctly in the record, but they lead to the same decision
  — do not conclude — so no requirement branches on which one occurred.
- **Withholding is per pass and stateless.** Nothing tracks how long the registry has been
  unobservable or escalates after N passes. The anomaly is the standing signal; a duration
  policy would be a knob with one caller and no second use in hand.
- **The two already-safe sweeps stay untouched.** Adding a guard to a sweep that cannot draw a
  false conclusion would be code with nothing to do, and would suggest to a later reader that
  it had something to do.
- **An item stalled by repeated blind passes is acceptable and is what the anomaly is for.**
  Withholding indefinitely is the safe direction: it delays noticing a genuinely dead session,
  which the next readable pass catches, rather than tearing down live work.
