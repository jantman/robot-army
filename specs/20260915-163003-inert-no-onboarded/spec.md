# Feature Specification: Say so when nothing is onboarded

**Feature Branch**: `robot-army/issue-83-database-loss-un-onboards-every`

**Created**: 2026-09-15

**Status**: Draft

**Input**: GitHub issue #83 — "Database loss un-onboards every repository, and the resulting card
comment tells the operator to edit the card instead". Found during the issue #1 verification
round (2026-08-31), running 003 quickstart scenario 5's database-loss case (FR-034).

## Background

Losing the state database takes every onboarding record with it. The card→issue mappings come
back from the cards' marker comments (003 R7, FR-034 — that invariant held perfectly in the
verification round), but onboarding does not, and **must not**: onboarding is deliberate
consent, carrying the settings review and a recorded fingerprint approval, and re-granting it
from anything recovered would defeat the point of asking.

So the fault is not the missing recovery. It is that a system which has silently become
**inert** — no repository it may act in, so no card can resolve and no issue can dispatch —
reports itself as healthy and blames its input. `doctor` passes. Every card gets a comment
telling its author to edit the card, which cannot help: the card already named the right
repository, and there is nothing onboarded for it to match. In the round, five consecutive
cards were each held with that advice before the cause was spotted.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - `doctor` fails on an installation with nothing onboarded (Priority: P1)

After the database is lost (or before the first `onboard`), the operator runs `doctor` — the
command the guide says to run first, every time — and it tells them, as a failed check with a
non-zero exit, that no repository is onboarded, that nothing can be dispatched until one is, and
what command fixes it.

**Why this priority**: `doctor` is the operator's first diagnostic. A system that cannot do any
work at all and still passes every check is the root of the whole incident: nothing draws the
eye to the cause.

**Independent Test**: Run `doctor` against an empty onboarding table and confirm it exits with
the check-failed code and names the remedy; onboard one repository and confirm the check passes.

**Acceptance Scenarios**:

1. **Given** no repository is onboarded, **When** the operator runs `doctor`, **Then** a check
   is reported as failed, its detail says nothing can be dispatched and names
   `robot-army onboard`, and the command exits with the check-failed exit code.
2. **Given** at least one repository is onboarded, **When** the operator runs `doctor`, **Then**
   that check passes and reports how many repositories are onboarded.
3. **Given** no repository is onboarded, **When** the operator runs `doctor --json` (or reads
   its structured result), **Then** the check appears among `failures` by a stable name.

---

### User Story 2 - A held card says "nothing is onboarded" instead of "edit the card" (Priority: P1)

When a card cannot be resolved **because no repository is onboarded at all**, its comment and
its recorded reason say that first, and tell the operator to run `robot-army onboard` — not to
edit or add lines to the card. When at least one repository is onboarded and the card names
none of them, the existing author-facing advice is unchanged.

**Why this priority**: This is the misleading part. The operator follows the advice they are
given; advice that cannot work sends them to reword cards that were right the first time.

**Independent Test**: Resolve a card that correctly names a repository against an empty
onboarding table; confirm the reason and the posted comment lead with the onboarding problem,
name `robot-army onboard`, and do not tell the author to edit the card.

**Acceptance Scenarios**:

1. **Given** nothing is onboarded, **When** a card naming `jantman/robot-army` is evaluated,
   **Then** it is held, and its reason states that no repository is onboarded and names
   `robot-army onboard`.
2. **Given** nothing is onboarded, **When** that held card is commented on, **Then** the comment
   does not instruct the author to edit the card or add a `robot-army:` line, and says the card
   will be picked up once a repository is onboarded.
3. **Given** nothing is onboarded, **When** a card carrying a `robot-army:` line is evaluated,
   **Then** it gets the same onboarding-first reason and comment, not the "the line names
   something that is not onboarded" text.
4. **Given** one or more repositories are onboarded, **When** a card names none of them,
   **Then** the reason and comment are exactly what they are today.
5. **Given** a card was held and commented while nothing was onboarded, **When** the operator
   onboards the repository it names, **Then** the card resolves on the next pass with no edit.

---

### User Story 3 - The planning record says onboarding is lost with the database (Priority: P3)

The 003 data model's interruption table and its quickstart's database-loss scenario say plainly
that onboarding does not come back and must be redone before anything can be dispatched, so the
next reader of that row does not take "work items are also gone" as the only loss.

**Why this priority**: Documentation-only, but it is the record the verification round was read
against, and it understated the practical consequence.

**Independent Test**: Read the row and the scenario; both name the loss of onboarding and the
command that restores it.

**Acceptance Scenarios**:

1. **Given** the 003 data model's "database lost entirely" row, **When** it is read, **Then** it
   says onboarding does not come back and must be redone with `robot-army onboard` before
   anything is dispatched, and why it is not recovered.
2. **Given** the 003 quickstart's database-loss case, **When** it is read, **Then** its expected
   outcome includes every repository showing as not onboarded, `doctor` failing on it, and held
   cards naming `robot-army onboard`.

### Edge Cases

- **Fresh installation.** Before the first `onboard`, `doctor` now fails on this check. That is
  correct — a fresh installation is exactly as inert as one that lost its database — and the
  setup guide's "run `doctor` first" step must say to expect it.
- **Configured but not onboarded.** Repositories with `[repos.*]` sections still count as not
  onboarded, and the check fails. Configuration was never consent (milestone 005).
- **Onboarded record that no longer resolves.** An onboarding record that resolves to no usable
  repository (no section and no recorded path) is not something a card can match, so it does not
  count towards "something is onboarded" for card resolution; `doctor` counts what card
  resolution counts, so the two never disagree about whether the system is inert.
- **Comment already posted with the old text.** A card held under the old reason gets one new
  comment when the reason changes to the onboarding-first text; after that, the
  one-comment-per-distinct-reason rule keeps it quiet.
- **Some but not all repositories onboarded.** Out of scope for the new text: a card naming a
  repository that exists but is not onboarded, while others are, keeps the existing reason, which
  already lists what *is* onboarded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `doctor` MUST include a check that fails when no repository is onboarded, whose
  detail states that nothing can be dispatched and names `robot-army onboard`.
- **FR-002**: When at least one repository is onboarded, that check MUST pass and report the
  number of onboarded repositories.
- **FR-003**: The check's failure MUST make `doctor` exit with the existing check-failed exit
  code, and appear in its structured `failures` list, like every other failed check.
- **FR-004**: Card resolution MUST distinguish "no repository is onboarded" from "the card names
  no onboarded repository". When nothing is onboarded, the reason MUST lead with that fact and
  name `robot-army onboard`, whatever the card says — including when it carries a `robot-army:`
  line.
- **FR-005**: The held-card comment for the nothing-onboarded reason MUST NOT tell the author to
  edit the card or add a `robot-army:` line; it MUST say the card will be picked up automatically
  once a repository is onboarded.
- **FR-006**: The reason and comment for every other unresolvable card MUST be unchanged.
- **FR-007**: Nothing about onboarding recovery changes: the system MUST NOT re-onboard anything
  automatically after database loss.
- **FR-008**: The 003 data model's database-loss row and its quickstart's database-loss scenario
  MUST state that onboarding is lost, is deliberately not recovered, and must be redone before
  anything is dispatched.
- **FR-009**: The published guide pages for held cards (intake), setup's `doctor` step, and
  recovery MUST describe the new check and the new comment.

### Key Entities

- **Onboarded repository**: a repository the operator has consented to, recorded in the state
  database; the set of these is what card resolution matches against and what this feature
  counts.
- **Held card reason**: the one-line explanation recorded against a held card and quoted in its
  comment; one comment is posted per distinct reason.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Re-running the issue's reproduction (database moved aside, one pass, `doctor`),
  `doctor` exits non-zero and its output names `robot-army onboard` — where today it exits zero.
- **SC-002**: In the same state, zero held-card comments tell the author to edit the card; every
  one names `robot-army onboard`.
- **SC-003**: With at least one repository onboarded, the text of every existing held-card reason
  and comment is byte-for-byte unchanged.
- **SC-004**: After onboarding the repository a held card names, the card resolves on the next
  pass with no edit to the card.

## Assumptions

- The operator and the card author are the same person (single-user system), so a comment
  addressed to "the operator" on the card is read by the right person; the comment still goes on
  the card because that is where they are looking when they notice nothing happens.
- "Onboarded" means what card resolution already uses — the onboarded repositories that resolve
  to a usable configuration — so `doctor` and the card text agree by construction.
- The new `doctor` check performs no network call and touches nothing outside the process, so it
  adds no audit record; `doctor` itself is read-only today.
- The `/health` endpoint and web interface are out of scope; the issue asks for `doctor` and the
  card comment.
