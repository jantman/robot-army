# Feature Specification: Every verb that offers `--include-simulated` honours it

**Feature Branch**: `robot-army/issue-21-include-simulated-is-inert-on-anomalies`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue #21 — "`--include-simulated` is inert on `anomalies`, `repos` and `log` —
the flag claims an FR-056 guarantee it cannot enforce" (labels: `bug`, `robot-army`)

## Context

Below the `live` effect level the system rehearses the whole pipeline without touching anything
outward-facing, and the rows it writes while doing so are marked simulated. Milestone 001's FR-056
made excluding those rows the **default** for every listing and including them the explicit act;
`--include-simulated` is that explicit act, and its help text names FR-056 by number.

The flag is offered on six verbs. On three of them it does nothing at all: `anomalies`, `repos`
and `log` never receive the value the parser accepted, so both spellings of each command print
exactly the same rows. `status` is only half right — the work-item sections were corrected by
milestone 008, but the unacknowledged-anomalies block it prints underneath them is drawn
unfiltered, so `status --include-simulated` and `status` disagree about nothing while claiming to
differ. The web interface repeats the defect: its `/anomalies` and `/log` pages accept the same
toggle, thread it through their links, and then discard it, and the anomaly count in the site
chrome is unfiltered on every page.

The consequence is not cosmetic. A rehearsal is supposed to be incapable of raising anything a
reader mistakes for real, and the issue reports two `card_create_failing` anomalies, both belonging
to simulated cards, presented in the default view as outstanding real problems with no way to
exclude them. That is the same contradiction milestone 008 exists to eliminate — a listing
asserting a scope it does not have — surviving on the sibling verbs.

Not every verb can honour the flag, and one of them should stop offering it. A repository row is
written by onboarding, which inspects a real clone on disk and is never rehearsed, so a simulated
repository cannot exist and `repos --include-simulated` is a promise about an empty set. Silently
accepting a flag that does nothing is the defect; removing it where it cannot mean anything is a
fix, not a regression.

The issue's table lists `worktree list` as untested. It was measured during this work and is
correct: it filters its rows and reports how many it withheld. It is in scope only as a regression
test, so the guarantee cannot rot back.

### Secondary: anomalies that resolve themselves

The issue also reports that nothing ever retracts an anomaly. Since it was filed that has become
partly untrue — `orphan_session` anomalies are now re-checked and retracted by the reconciliation
pass — but `card_create_failing` still is not, even though its condition demonstrably resolves: the
card whose issue could not be created is later created successfully, and the report stays on the
list forever. The `anomalies` help text, "conditions detected but not resolvable", describes the
old world and is now wrong about two kinds.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A rehearsal raises no anomaly the maintainer mistakes for real (Priority: P1)

The maintainer has been running the daemon below `live`. Rehearsed work has raised anomalies. When
they run `robot-army anomalies`, or look at the anomaly block on `robot-army status`, or open the
`/anomalies` page, they see the conditions affecting real work and nothing else. Adding
`--include-simulated` — or flipping the site's toggle — shows the rehearsal's anomalies too,
marked as simulated, and says so.

**Why this priority**: This is the reported harm. A stale anomaly that belongs to a dry run is
presented as a live problem in the default view, which is the single thing FR-056 exists to
prevent.

**Independent Test**: Seed a database with anomalies raised against both real and simulated
entities, then run `anomalies`, `status` and the `/anomalies` page in both spellings and assert the
row sets differ by exactly the simulated rows.

**Acceptance Scenarios**:

1. **Given** three outstanding anomalies, two of them raised during rehearsed work, **When**
   `robot-army anomalies` runs without the flag, **Then** only the one real anomaly is listed and
   the output states that two simulated anomalies were withheld and names the flag that reveals
   them.
2. **Given** the same state, **When** `robot-army anomalies --include-simulated` runs, **Then** all
   three are listed and the two rehearsed ones are visibly marked as simulated.
3. **Given** the same state, **When** `robot-army status` runs without the flag, **Then** its
   unacknowledged-anomalies block counts and lists one anomaly, not three.
4. **Given** the same state, **When** `robot-army status --include-simulated` runs, **Then** its
   anomaly block lists all three, with the rehearsed ones marked.
5. **Given** the same state, **When** `--json` is used with either spelling, **Then** the
   machine-readable anomaly list contains exactly the rows the human-readable one did, and states
   how many were withheld.
6. **Given** the same state, **When** the `/anomalies` page is served with the toggle off, **Then**
   it shows the real anomaly only, and the anomaly count in the site chrome agrees with it on
   every page.
7. **Given** an anomaly raised against no particular entity — a registry-version warning, say —
   **When** either spelling runs, **Then** it is listed, because it belongs to the system rather
   than to any rehearsal.

---

### User Story 2 - The audit log can be read without the rehearsal's traffic (Priority: P1)

The maintainer is reconstructing what the system really did. `robot-army log` shows the records of
real actions; `robot-army log --include-simulated` shows the rehearsed ones alongside them, still
carrying the `[simulated]` marker they have always carried. The same holds for the `/log` page and
its toggle.

**Why this priority**: The issue measures 951 rehearsed records drowning the real ones in a
two-day window, on the one surface the constitution names as the reconstruction path. It is as
severe as US1 and independently fixable.

**Independent Test**: Write an audit file mixing real records with ones marked simulated or
dry-run, then read it in both spellings and assert the record sets differ by exactly the marked
records.

**Acceptance Scenarios**:

1. **Given** an audit file of 10 real and 40 rehearsed records, **When** `robot-army log` runs
   without the flag, **Then** 10 records are shown and the output states that 40 were withheld and
   names the flag.
2. **Given** the same file, **When** `robot-army log --include-simulated` runs, **Then** all 50 are
   shown and every rehearsed one still carries its `[simulated]` marker.
3. **Given** the same file and `--since`, `--item` or `--limit` also in force, **Then** the
   simulated filter composes with them rather than replacing them, and any withheld count reported
   is scoped to the same filters.
4. **Given** the same file, **When** the `/log` page is served with the toggle off, **Then** its
   page of records excludes the rehearsed ones, and turning the toggle on includes them.
5. **Given** an audit file whose last line is a partial write, **When** either spelling runs,
   **Then** the unparseable line is still skipped and counted as it is today, independently of the
   simulated filter.

---

### User Story 3 - No verb offers a filter it cannot apply (Priority: P2)

The maintainer reads `robot-army <verb> --help` and every `--include-simulated` it finds is one
that changes what the command prints. `repos` does not offer it, because a repository is never
rehearsed.

**Why this priority**: It is the issue's own framing — silently accepting a flag that does nothing
is the worse of the two failures — and it is what stops this defect from being re-introduced on the
next verb. It is second only because a reader is misled by it rather than harmed.

**Independent Test**: Enumerate the parser's verbs, and assert that every one advertising
`--include-simulated` reaches an operation that receives it.

**Acceptance Scenarios**:

1. **Given** the built command-line parser, **When** the verbs advertising
   `--include-simulated` are enumerated, **Then** every one of them passes the value through to the
   operation that produces its rows.
2. **Given** `robot-army repos --include-simulated`, **When** it runs, **Then** it is refused as a
   usage error naming the unrecognised option, rather than accepted and ignored.
3. **Given** `robot-army worktree list`, **When** it runs against a state holding both real and
   simulated worktrees, **Then** only the real ones are listed and the withheld count is stated —
   the behaviour it already has, now protected by a test.
4. **Given** the published guide and the command-line contract, **When** they are read, **Then**
   they name the same set of verbs the parser offers the flag on.

---

### User Story 4 - An anomaly whose condition has resolved leaves the list (Priority: P3)

A card that repeatedly failed to be filed raised an anomaly. The card is later filed successfully.
The maintainer's next look at `robot-army anomalies` does not show it, because the system re-checked
the condition and found it no longer holds — and `--all` shows it as *resolved*, distinct from one a
human dismissed.

**Why this priority**: It is the issue's own "secondary" heading, and it is a separate defect from
the flag: a list that is mostly stale teaches the habit of clearing it unread, which is how the
anomaly that mattered gets acknowledged along with the noise. It is last because the list is
readable without it once US1 lands.

**Independent Test**: Raise a `card_create_failing` anomaly for a card, drive that card to a state
where its issue exists, run one reconciliation pass, and assert the anomaly is recorded as resolved
and no longer appears in the default listing.

**Acceptance Scenarios**:

1. **Given** an outstanding `card_create_failing` anomaly for a card that has since been linked to
   an issue, **When** a reconciliation pass runs, **Then** the anomaly is recorded as resolved, the
   resolution is written to the audit log with the evidence for it, and the default listing no
   longer shows it.
2. **Given** the same anomaly for a card that is still failing, **When** a reconciliation pass
   runs, **Then** the anomaly is left outstanding.
3. **Given** the same anomaly for a card that no longer exists in the store at all, **When** a
   reconciliation pass runs, **Then** the anomaly is left outstanding, because "I could not check"
   must never be recorded as "it is fine".
4. **Given** an anomaly that has just been resolved, **When** a second reconciliation pass runs
   over the same state, **Then** nothing further is written and nothing further is logged.
5. **Given** the `anomalies` help text, **When** it is read, **Then** it no longer claims the
   conditions it lists cannot be resolved.

---

### Edge Cases

- **An anomaly about an entity that is neither real nor rehearsed.** Several kinds name no entity,
  or name one that is not a work item or a card — a registry version, a socket, a configuration
  section. These are properties of the machine, not of any rehearsal, and must remain visible in
  the default view. Withholding one would hide a real problem, which is the opposite of this
  feature's purpose.
- **Anomalies raised before this change.** Existing rows carry no record of whether the run that
  raised them was rehearsed. They must be treated as real and stay visible: showing a real anomaly
  that might be rehearsed is recoverable, hiding a rehearsed one that might be real is not.
- **A rehearsed run and a real run raising the same condition for the same entity.** The system
  suppresses duplicate outstanding anomalies, and the two must not collapse into one row that is
  shown or hidden by whichever arrived first.
- **The audit log's two markers.** Records carry a rehearsal marker under either of two names.
  Both mean "this did not really happen" and both must be filtered by the flag; the log has always
  rendered either as `[simulated]`.
- **Withheld counts under filters.** `log --since 1h --item 42` and `anomalies --since 1h` narrow
  what was matched. A withheld count must describe the rows those filters matched, not every
  simulated row in existence, or it reports a number the flag would not produce.
- **A page of the log that fills entirely with rehearsed records.** The paged reader must not
  return an empty page while older matching records remain; it applies the filter as it scans.
- **`worktree list` with only simulated worktrees on disk.** The listing is empty and must say so
  in the words that name the withheld rows, not in the words for a machine that has never had a
  worktree.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every command-line verb that advertises `--include-simulated` MUST pass the value to
  the operation that produces its rows, and that operation MUST use it to decide which rows it
  emits.
- **FR-002**: A verb that cannot honour `--include-simulated` MUST NOT advertise it. `repos` MUST
  stop offering it, and MUST reject it as a usage error.
- **FR-003**: The system MUST record, at the moment an anomaly is raised, whether the run that
  raised it was rehearsed, so that the distinction is a stored property of the anomaly rather than
  something a reader has to infer from the entity it names.
- **FR-004**: `anomalies` MUST exclude rehearsed anomalies by default and include them when
  `--include-simulated` is given, in both the rendered listing and the machine-readable payload.
- **FR-005**: The unacknowledged-anomalies block of `status` MUST be scoped by the same
  `--include-simulated` value as the rest of that command's output, in both the rendered listing
  and the machine-readable payload.
- **FR-006**: `log` MUST exclude records marked as rehearsed by default and include them when
  `--include-simulated` is given, composing with the `--since`, `--item` and `--limit` filters
  rather than replacing them.
- **FR-007**: Wherever a listing withholds rehearsed rows, it MUST say how many it withheld and
  name the option that reveals them, in the same words the corrected listings already use, and
  scoped to the same filters the visible rows were.
- **FR-008**: Rehearsed rows MUST remain visibly marked wherever they are shown.
- **FR-009**: An anomaly that names no entity, or names an entity that is not produced by rehearsed
  work, MUST be treated as real and remain visible in the default view.
- **FR-010**: Anomaly rows written before this change MUST be treated as real, and MUST NOT be
  hidden by the default view.
- **FR-011**: The web interface MUST apply its existing simulated toggle to the `/anomalies` and
  `/log` pages, and the anomaly count shown in the site chrome MUST agree with the scope the page
  was served with.
- **FR-012**: `worktree list` MUST continue to exclude rehearsed rows by default and report how
  many it withheld, and this MUST be covered by a test.
- **FR-013**: The system MUST re-check outstanding `card_create_failing` anomalies during
  reconciliation and record as resolved any whose card has since been linked to an issue. An
  anomaly whose condition cannot be re-established either way MUST be left outstanding.
- **FR-014**: A resolution MUST be written to the audit log at the time it occurs, carrying the
  evidence that the condition no longer holds, and a repeated pass over already-resolved state MUST
  write and log nothing.
- **FR-015**: A resolved anomaly MUST remain distinguishable from an acknowledged one wherever both
  are shown.
- **FR-016**: The `anomalies` help text MUST NOT describe the conditions it lists as unresolvable.
- **FR-017**: The published guide and the command-line contract MUST name the same set of verbs
  that offer the flag as the parser does, and MUST describe what each of them filters.

### Key Entities

- **Anomaly**: a condition the system detected and reported. Already carries its kind, the entity
  it concerns, when it was detected, whether a human acknowledged it, and whether the system
  re-checked and found it resolved. Gains a record of whether the run that raised it was rehearsed.
- **Audit record**: one line of the reconstruction path. Already carries a rehearsal marker under
  either of two names; gains no new field, only a reader that respects it.
- **Repository record**: written by onboarding against a real clone. Has no rehearsed form, which
  is why the flag leaves it.
- **Listing scope**: the pair of "which rows were shown" and "how many were withheld", which every
  corrected listing already states and which the corrected ones here must state in the same words.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every verb offering the flag, running it with and without the flag against a
  state holding rehearsed rows produces different output, and the difference is exactly the
  rehearsed rows.
- **SC-002**: No verb accepts `--include-simulated` and produces byte-identical output with and
  without it against a state that holds rehearsed rows of that verb's kind.
- **SC-003**: On a state whose only outstanding anomalies belong to rehearsed work, the default
  views report zero outstanding anomalies — on the command line, in the machine-readable payload,
  and in the site chrome on every page.
- **SC-004**: On the reported two-day window of 951 rehearsed records among real ones, the default
  log reader shows only the real ones, and the count it reports as withheld equals the number the
  flag then reveals.
- **SC-005**: A card whose creation failed past the anomaly threshold and was later filed
  successfully leaves the outstanding anomaly list within one reconciliation pass, without anyone
  acknowledging it, and appears under `--all` as resolved rather than as acknowledged.
- **SC-006**: The full test suite passes, and it contains a test that fails if any future verb
  advertises the flag without honouring it.

## Assumptions

- "Rehearsed" and "simulated" name the same thing throughout: a row or record produced at an
  effect level below `live`. The existing stored flag and the existing audit markers are the
  system's record of it and no new vocabulary is introduced.
- An anomaly's rehearsed-ness is a property of the run that raised it, not of the entity it names.
  Deriving it from the entity would require a different join per anomaly kind, would be undefined
  for the kinds that name no entity, and would silently change answer if the entity were later
  purged. Recording it at raise time matches how every other table in this system carries the
  distinction.
- Existing anomaly rows are not back-filled. There is no evidence in the row to back-fill from, and
  the safe reading of a missing value is "real".
- `repos` losing the flag is acceptable without a deprecation path: the project maintains no
  backward compatibility for outside consumers, and the flag it removes never did anything.
- The audit log's `simulated` and `dry_run` markers are treated as one condition by the reader, as
  the existing rendering already does.
- Retraction is added for `card_create_failing` only. Every other kind that is not already
  retracted has its own settling story, and guessing at one would be the failure mode the existing
  retraction was careful to avoid.
- The web interface's simulated toggle already exists, is already threaded through every link, and
  needs no new control — only for two pages to stop discarding the value they are handed.
