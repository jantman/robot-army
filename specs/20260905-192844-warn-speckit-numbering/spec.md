# Feature Specification: Warn at onboarding when Spec Kit numbers features by scanning

**Feature Branch**: `robot-army/issue-41-spec-numbers-collide-because-they-are`

**Created**: 2026-09-05

**Status**: Draft

**Input**: [Issue #41](https://github.com/jantman/robot-army/issues/41), "Spec numbers collide
because they are scanned per-worktree", and its **Human Decision**: "We should warn when onboarding
a repository that uses spec-kit but uses a feature numbering other than `timestamp`. We can consider
this to be an onboard-time-only concern; if a user ignores the warning, that's on them."

## Why this exists

This system dispatches one worktree per issue, and it tells every Spec Kit session to run
`/speckit-specify`. That command picks a feature directory by scanning `specs/` for the highest
number already used and adding one — a scan of the **current worktree only**.

Two concurrent sessions therefore see the same `specs/`, compute the same next number, and both
take it. Issue #41 records this happening twice in this repository: `012-anomalies-since-filter`
against `012-prompt-branch-pr-safety`, both merged before anyone noticed, and `014` claimed
simultaneously in two live worktrees.

The issue also establishes that **no check the daemon could perform at dispatch time would catch
it.** The competing number exists only as untracked files in a sibling worktree: not on any branch,
not in any ref, not in anything git can be asked about. Two sessions numbering in the same moment
collide however wide the search is made, because the losing session's claim is not yet written
anywhere when the winning session looks.

What *does* close the race is the repository's own configuration. Spec Kit supports
`"feature_numbering": "timestamp"` in `.specify/init-options.json`, which names directories
`YYYYMMDD-HHMMSS-<short-name>` and cannot collide between concurrent sessions by construction. This
repository set it, and the collisions stopped.

So the useful thing this system can do is not to prevent the collision — it cannot — but to **say
so once, at the one moment a human is already reading a screen about a repository and deciding
whether to trust it.** Onboarding is that moment. It is a deliberate, interactive step, run once per
repository, whose entire purpose is to put facts about a repository in front of the person
approving it.

**The warning does not block anything.** It is advice on a screen. A maintainer who reads it and
proceeds has made an informed choice about a low-impact problem — issue #41 is explicit that a
duplicate prefix breaks no tooling, because `.specify/feature.json` resolves by full path. What is
lost to a collision is that "spec 012" stops being an unambiguous name, and that is worth one
sentence at onboarding and no more than that.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Onboarding a scan-numbered Spec Kit repository says so (Priority: P1)

The maintainer runs `robot-army onboard jantman/some-repo` against a repository that has Spec Kit
installed and has never set `feature_numbering`. Before the approval prompt, the screen tells them
that this repository numbers features by scanning, that two concurrent sessions can therefore take
the same number, and what one-line edit fixes it.

They approve anyway, or they stop, fix the repository, and run onboarding again. Either way they
were told before they decided.

**Why this priority**: It is the whole of the Human Decision. Every other story in this document is
a case that must not produce a false warning.

**Independent Test**: onboard a repository whose clone has Spec Kit scaffolding, the four lifecycle
commands, and either no `.specify/init-options.json` or one whose `feature_numbering` is
`sequential`; confirm the warning appears on the screen ahead of the approval prompt, and that
answering `y` still onboards the repository normally.

**Acceptance Scenarios**:

1. **Given** a clone with Spec Kit installed and `"feature_numbering": "sequential"`, **When** the
   maintainer runs `onboard`, **Then** the approval screen carries a warning naming the configured
   numbering, the collision it permits, and the fix.
2. **Given** a clone with Spec Kit installed and **no** `feature_numbering` key at all, **When** the
   maintainer runs `onboard`, **Then** the same warning appears, because scanning is what Spec Kit
   does when nothing says otherwise.
3. **Given** the warning has been shown, **When** the maintainer answers `y`, **Then** the
   repository is onboarded exactly as it would have been without the warning — same record, same
   exit code.
4. **Given** the warning has been shown, **When** the maintainer answers `n`, **Then** onboarding
   aborts for the ordinary reason it aborts, and the warning has changed nothing about that path.

---

### User Story 2 - A repository that already numbers safely is not nagged (Priority: P1)

The maintainer onboards a repository that has set `"feature_numbering": "timestamp"`, or one that
does not use Spec Kit at all. The screen says nothing about numbering.

**Why this priority**: Equal in importance to the warning itself. A warning that appears on
repositories that are fine is a warning that gets skipped on the repository that is not, and the
approval screen is already long — it prints the full text of any committed permission settings,
which is the thing on that screen that most needs reading.

**Independent Test**: onboard a clone with `"feature_numbering": "timestamp"`, and separately a
clone with no `.specify/` directory at all; confirm the screen is byte-identical to what it is
today in both cases.

**Acceptance Scenarios**:

1. **Given** a clone with Spec Kit installed and `"feature_numbering": "timestamp"`, **When** the
   maintainer runs `onboard`, **Then** no numbering warning appears.
2. **Given** a clone with no Spec Kit scaffolding, **When** the maintainer runs `onboard`, **Then**
   no numbering warning appears, whatever else the clone contains — including a `specs/` directory
   or a stray `.specify/init-options.json`.
3. **Given** a clone with Spec Kit scaffolding but missing one or more of the four lifecycle
   commands — the repository this system would not send Spec Kit guidance to — **When** the
   maintainer runs `onboard`, **Then** no numbering warning appears, because the repository is not
   one whose numbering this system will ever cause to be exercised.

---

### User Story 3 - An unreadable numbering setting is reported as unreadable (Priority: P2)

The maintainer onboards a repository whose `.specify/init-options.json` is corrupt, is not an
object, or cannot be read. The screen says the numbering could not be determined, and says it
differently from the way it says "this is set to sequential".

**Why this priority**: Lower than the two above because it is rare, but it must not be silent and it
must not lie. Reporting "not timestamp" for a file that could not be parsed asserts something the
system does not know; reporting nothing hides a broken file in the one place a human is looking at
the repository.

**Independent Test**: onboard a clone whose `init-options.json` contains invalid JSON, and one whose
`init-options.json` is a JSON array; confirm each produces the could-not-determine wording rather
than the sequential wording, and that onboarding otherwise proceeds.

**Acceptance Scenarios**:

1. **Given** a Spec Kit clone whose `.specify/init-options.json` is not valid JSON, **When** the
   maintainer runs `onboard`, **Then** the screen says the numbering could not be read, names the
   file, and onboarding continues to the approval prompt.
2. **Given** a Spec Kit clone whose `.specify/init-options.json` holds a `feature_numbering` value
   that is not a string, **When** the maintainer runs `onboard`, **Then** the same could-not-read
   treatment applies rather than a warning quoting a nonsense value.
3. **Given** any of these, **When** onboarding proceeds, **Then** nothing about the run fails,
   exits non-zero, or is prevented on account of the unreadable file.

---

### User Story 4 - A `--json` run carries the finding without the prose (Priority: P3)

A script runs `robot-army onboard --json`. The machine-readable document reports what the numbering
is and whether it is the safe one, and carries no human-readable warning text.

**Why this priority**: The `--json` mode already exists and already carries every other fact the
screen states — the clone path, the trust verdict, the fingerprint. A fact visible only in prose
would be the one thing on that screen a script cannot see, for no reason.

**Independent Test**: run onboarding in JSON mode against each of the four cases above and confirm
the document distinguishes them, and that no warning sentence appears anywhere in it.

**Acceptance Scenarios**:

1. **Given** a scan-numbered Spec Kit clone, **When** onboarding runs in JSON mode, **Then** the
   document records that the repository uses Spec Kit and what its numbering is.
2. **Given** a non-Spec-Kit clone, **When** onboarding runs in JSON mode, **Then** the document says
   so, and does not report a numbering.

### Edge Cases

- **A repository that adopts Spec Kit after being onboarded.** Nothing warns, and that is the
  decision the issue records: this is an onboard-time-only concern. Re-running `onboard` — which the
  maintainer does anyway when committed settings change — is when the warning would next appear.
- **A repository that fixes its numbering after being warned.** Nothing needs to notice. The
  warning was advice; there is no stored state saying it was given, and nothing to clear.
- **Onboarding refuses before it reaches the screen** — no clone, wrong repository, ambiguous
  remote. No numbering is read and none is reported, because the refusal already means this
  repository is not the one being described.
- **`.specify/init-options.json` is present in a directory that is not a Spec Kit project.** Not
  read. Detection gates the read, for the same reason `speckit.record_phase` gates observation on
  detection: `specs/` and `.specify/` are not rare enough names to carry meaning on their own.
- **The clone's working tree differs from the base branch.** The numbering is read from the working
  tree, unlike the committed permission settings, which are read at the base ref. This is deliberate
  and is stated as an assumption below.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The onboarding approval screen MUST show a warning when the repository being onboarded
  is detected as a Spec Kit project **and** its configured feature numbering is anything other than
  `timestamp`.
- **FR-002**: "Anything other than `timestamp`" MUST include the case where no numbering is
  configured at all — no `.specify/init-options.json`, or one with no `feature_numbering` key —
  because scanning is what Spec Kit does by default and that is the case the issue was filed about.
- **FR-003**: The warning MUST state what the numbering currently is, that two concurrent sessions
  can take the same feature number under it, and the exact edit that fixes it.
- **FR-004**: The warning MUST NOT block onboarding, change its exit code, alter the approval
  prompt, or cause any refusal. It is advisory in every case.
- **FR-005**: No warning MUST be shown for a repository that is not detected as a Spec Kit project,
  whatever files it happens to contain.
- **FR-006**: No warning MUST be shown when the configured numbering is `timestamp`.
- **FR-007**: A `.specify/init-options.json` that cannot be read, cannot be parsed, is not a JSON
  object, or holds a non-string `feature_numbering` MUST produce a distinct "could not determine"
  message rather than either silence or the ordinary warning.
- **FR-008**: Reading the numbering MUST NOT raise, whatever the state of the filesystem. Every
  failure is one of the outcomes above.
- **FR-009**: The warning MUST appear before the approval prompt, on the same screen as the clone
  path, trust verdict, and committed settings — not after the answer.
- **FR-010**: The machine-readable (`--json`) onboarding document MUST carry the detection verdict
  and the numbering finding, and MUST NOT carry the human-readable warning text.
- **FR-011**: This system MUST NOT write to, repair, or upgrade the onboarded repository's Spec Kit
  configuration. It reads and reports.
- **FR-012**: Nothing about the finding is persisted. It is derived on each `onboard` run, exactly
  as Spec Kit detection already is, so that a repository which fixes its numbering needs no
  re-onboarding to stop being warned about.
- **FR-013**: The existing `repo.onboard` audit record MUST carry the numbering finding, so the log
  answers what the maintainer was shown at the moment they approved.

### Key Entities

- **Feature numbering**: how a repository's Spec Kit installation names new feature directories.
  Read from `.specify/init-options.json`. Three answers matter: `timestamp` (collision-free),
  anything else including absent (scanned, and therefore collision-prone), and unknown (the file
  exists but could not be understood).
- **Onboarding approval screen**: the existing per-repository screen printed before the approval
  prompt. This feature adds at most one short block to it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A maintainer onboarding a scan-numbered Spec Kit repository learns, before answering
  the approval prompt, that concurrent sessions can collide on a feature number — without consulting
  the guide, the issue, or the source.
- **SC-002**: A maintainer onboarding a timestamp-numbered or non-Spec-Kit repository sees a screen
  identical to today's, so nothing new competes with the committed-settings text they must read.
- **SC-003**: Every repository state — Spec Kit or not, each numbering value, absent file, corrupt
  file — produces exactly one of the three defined outcomes, with no crash and no non-zero exit
  attributable to this feature.
- **SC-004**: The audit log alone answers, for any past onboarding, whether the repository was a
  Spec Kit project and what its feature numbering was at that moment.

## Assumptions

- **The numbering is read from the clone's working tree**, not from the base ref. Committed
  permission settings are read at the base ref because a dispatched session honours the *committed*
  file; feature numbering is not honoured by this system at all — it is honoured by Spec Kit running
  inside a worktree, which is a checkout of the working tree's branch. Reading the working tree is
  also what existing Spec Kit detection does, and using two different sources for two facts about
  the same directory on the same screen would be its own confusion.
- **`.specify/init-options.json` at the repository root is the location.** It is where Spec Kit
  writes it and where `/speckit-specify` reads it.
- **`timestamp` is the only safe value**, per the Human Decision. Spec Kit's other documented value
  is `sequential`; an unrecognised value is treated as unsafe rather than as an error, because a
  value this system does not know is a value it cannot vouch for.
- **The deprecated `branch_numbering` key is not consulted.** Spec Kit's own documentation marks it
  as deprecated and slated for removal, and a repository still using it is by definition not set to
  `feature_numbering = "timestamp"`, so it earns the warning either way.
- **No configuration knob turns this warning off.** It appears on one screen, once per onboarding,
  and a knob to suppress a single advisory sentence would be a setting with one hypothetical user.

## Out of Scope

- Any dispatch-time check, prompt-time check, health check, or anomaly. The Human Decision fixes
  this at onboarding time, and the issue establishes that a dispatch-time check could not close the
  race in any case.
- Preventing, detecting, or repairing an actual collision — including renaming a colliding feature
  directory, or refusing to dispatch a second session into a repository with a spec in flight.
- Changing how this repository or any other numbers its own features. This repository already uses
  `timestamp`.
- Writing `feature_numbering` into an onboarded repository, offering to write it, or prompting to.
- Numbering feature directories by issue number (remedy 1 in the issue). The Human Decision did not
  choose it, and it would be a convention for this repository rather than behaviour of this system.
