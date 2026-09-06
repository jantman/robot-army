# Feature Specification: The onboarding security review reads real committed settings at every effect level

**Feature Branch**: `robot-army/issue-20-onboard-cannot-see-committed-claude`

**Created**: 2026-09-06

**Status**: Draft

**Input**: jantman/robot-army issue #20 — "onboard cannot see committed `.claude/settings*.json`
below `local`, so the FR-003 review is blank and an empty fingerprint is approved".

## Context

`robot-army onboard` exists to put one thing in front of a human before a repository is ever
dispatched into: **the tool-permission settings that repository has committed, which a session
will honour without asking**. That screen is the control (001 FR-003); the hash recorded
alongside it is what later blocks a dispatch when those settings change (001 FR-004).

At `effect_level = "plan"` the version-control boundary is simulated, and its file-at-a-ref read
answers "no such file" for every path in every repository. So the review screen says *no committed
`.claude/settings*.json` at the base ref* whatever is actually committed, and the approval records
an empty set of hashes. The human approves a blank screen and the record asserts the repository
has nothing committed.

This contradicts a rule the project already holds: reads are real at every effect level (001
FR-052), because a dry run that fakes its reads tells you nothing about the thing you ran it to
check. The same boundary already honours that rule for the reads that verify *which repository is
being onboarded* — those were made real precisely so that a `plan`-level onboarding reaches the
same verdict a `live` one would. The read the security review depends on did not get the same
treatment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The settings review shows what is really committed (Priority: P1)

The operator runs `robot-army onboard <repo>` on a repository that has `.claude/settings.json`
committed at its base branch — a file that, say, registers a `SessionStart` hook running a script
from the repository. The screen prints that file in full, with its warning that these are applied
to a dispatched session without asking, and the operator decides with the file in front of them.
This happens at whatever effect level the installation is configured for, including `plan`.

**Why this priority**: it is the whole reason the command exists. Without it the operator is
approving an empty screen and does not know it, which is worse than having no review at all —
a blank screen reads as "this repository commits nothing", which is a positive claim.

**Independent Test**: onboard a repository with committed settings at `plan`, and compare the
screen and the recorded hashes against the same command at `live`. They must be identical.

**Acceptance Scenarios**:

1. **Given** a repository with `.claude/settings.json` and `.claude/settings.local.json` committed
   at its base branch, **When** the operator runs `onboard` at effect level `plan`, **Then** the
   full text of both files is printed for review before any approval is asked for.
2. **Given** the same repository, **When** the operator approves, **Then** the approval record
   holds a hash per committed file, matching what the same approval would record at `live`.
3. **Given** a repository with no committed settings, **When** the operator runs `onboard` at
   `plan`, **Then** the screen says so — and it says so because the files were looked for and were
   genuinely absent.
4. **Given** a repository whose base branch does not exist in the clone, **When** the operator runs
   `onboard` at any level, **Then** the command reports the absence the same way at every level and
   does not fail with an unhandled error.

---

### User Story 2 - A stale blank approval surfaces instead of standing (Priority: P1)

Every repository onboarded on this installation while the review was blank carries an approval
record asserting it has no committed settings. Once the read is real, the settings found at
dispatch no longer match that record, and the dispatch is blocked with a message naming the files
that "appeared" and pointing at `onboard --reapprove` — which now shows the real review.

**Why this priority**: the wrong approvals are already recorded, on this machine, today. A fix
that only stops new blank approvals would leave the existing ones silently trusted.

**Independent Test**: record an approval with an empty hash set against a repository that does have
committed settings, then attempt a dispatch, and confirm it is blocked with a message naming the
files and the remedy.

**Acceptance Scenarios**:

1. **Given** an approval record holding no hashes and a repository that has committed settings at
   its base branch, **When** a dispatch is attempted at any effect level, **Then** it is blocked,
   the block names the files as added, and the message points at `onboard --reapprove`.
2. **Given** that block, **When** the operator runs `onboard --reapprove`, **Then** the review shows
   the real settings and the difference against the previously approved (empty) set.

---

### User Story 3 - The simulation stops inventing answers about the clone that is really there (Priority: P2)

A question about the operator's existing primary clone — what remotes it has, where they point,
what is committed in it — has one true answer regardless of which effect level is being simulated.
The simulated boundary answers those truthfully. It keeps answering as-if only for artifacts the
simulation itself merely pretended to create: the worktree it did not make, the branch it did not
cut, the commit it did not push.

**Why this priority**: it is the rule that makes the P1 fix a fix rather than a patched instance.
Without it stated and enforced, the next read added to this boundary is one more coin toss.

**Independent Test**: for each read the simulated boundary answers truthfully, assert its answer
equals the real implementation's answer against the same clone; for each read it still fakes, the
subject of the question is something the simulation did not create, and that is recorded.

**Acceptance Scenarios**:

1. **Given** a clone with no configured remote, **When** a dispatch is planned at `plan`, **Then**
   the record says the fetch was skipped because the repository has no remote — the same thing the
   real path records — rather than naming a remote the clone does not have.
2. **Given** a clone whose only remote is not named `origin`, **When** the simulated boundary is
   asked which remote to use, **Then** it names that remote, as the real implementation does.
3. **Given** any read the simulated boundary delegates to the real implementation, **When** it is
   called, **Then** the audit record describes a real call rather than a simulated one, because the
   call really happened.

---

### Edge Cases

- **The base ref is missing from the clone** (never fetched, or a typo in `base_branch`). The read
  finds nothing, which is indistinguishable from "the file is not committed". This is the
  pre-existing behaviour of the real path and is not changed here; it is called out because a
  missing base ref now produces the same *blank* screen the bug produced, and the operator needs
  the base ref line already printed above it to tell the two apart.
- **A settings file that is not valid UTF-8.** The review prints text; undecodable bytes must not
  crash the approval screen.
- **The clone path is not a git repository at all.** Onboarding refuses earlier than this read, and
  the read must not become the thing that reports it — it answers rather than raising.
- **A settings file committed but empty (zero bytes).** It is present, so it is listed and hashed;
  an empty file is a fact about the repository, not an absence.
- **Reading is slower than inventing.** The read now runs a subprocess per settings path per
  onboarding and per dispatch gate, at every level, where before it ran none below `local`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Reading a file's committed content at a given ref MUST return the repository's real
  content at every effect level, including `plan`. The answer MUST NOT depend on the effect level.
- **FR-002**: The onboarding review screen MUST display the full text of each committed
  `.claude/settings*.json` found at the base ref at every effect level, and MUST state that none
  were found only when none were found.
- **FR-003**: The approval recorded by `onboard` MUST hold a hash per committed settings file
  actually present at the base ref, at every effect level. An empty set MUST be recorded only for a
  repository that genuinely commits none.
- **FR-004**: The dispatch settings-fingerprint gate MUST compare against the repository's real
  committed settings at every effect level, so that an approval recorded while the read was blank
  blocks dispatch rather than passing it.
- **FR-005**: A read the simulated boundary answers truthfully MUST be recorded as a real action in
  the audit log, not as a simulated one — it genuinely happened.
- **FR-006**: The simulated version-control boundary MUST answer truthfully every question whose
  subject exists independently of the simulation — the operator's primary clone and its contents,
  remotes, and refs. It MUST continue to answer as-if for questions about artifacts the simulation
  only pretended to create, and each such as-if answer MUST carry a written reason.
- **FR-007**: Which reads are answered truthfully and which are answered as-if MUST be asserted by
  the test suite, so that a read added to this boundary without a decision fails the suite rather
  than silently picking a side.
- **FR-008**: The guide MUST state that the onboarding security review is real at every effect
  level, and MUST NOT imply that rehearsing onboarding below `live` shows a reduced screen.

### Key Entities

- **Committed settings review**: the full text of each `.claude/settings*.json` as it exists at the
  base branch tip — what a freshly created worktree will contain, not what is in a working tree.
- **Approval record**: the per-repository row holding the approved clone location and the hash per
  committed settings file. Its meaning is "a human read exactly this and said yes".
- **Effect level**: the four graduated levels selecting real or simulated per boundary. Reads are
  real at every level.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Onboarding the same repository at `plan` and at `live` produces the same review text
  and the same recorded hashes — 100% identical, for a repository with committed settings and for
  one without.
- **SC-002**: The operator can no longer approve a repository whose committed settings were hidden
  from them: zero repositories with committed settings produce a "none found" review.
- **SC-003**: Every approval recorded while the review was blank is caught — a dispatch against
  such a record is blocked and names the files, rather than proceeding.
- **SC-004**: Quickstart scenario 6 (the onboarding review) can be walked at any effect level, so
  the review no longer has a level at which it cannot be verified.
- **SC-005**: The full test suite passes.

## Assumptions

- The read is cheap enough to make real at every level: it is a local `git` invocation against an
  object store on the same machine, already bounded by a timeout, running a small fixed number of
  times per onboarding and per dispatch gate. No caching is introduced.
- The two settings paths reviewed are unchanged by this work; this is about whether they are read,
  not which they are.
- Nothing backfills existing approval records. The correction path is the block-then-`--reapprove`
  flow that already exists, which is deliberate: an approval means a human read the file, and
  writing hashes into a record on the strength of a code change would forge that.
- Historical spec documents (001, 005 and their quickstarts) are project history and are not
  rewritten; where a verification gap they describe is closed by this work, it is the published
  guide that says so.
- The audit records for the newly real reads change shape — from one simulated record naming the
  boundary call to the real implementation's subprocess records. This is the same change
  `list_remotes` and `remote_url` already made and is not treated as a regression.
