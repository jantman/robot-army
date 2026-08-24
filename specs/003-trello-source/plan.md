# Implementation Plan: Trello Source

**Branch**: `003-trello-source` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-trello-source/spec.md`

## Summary

A tagged card on the author's private board becomes a GitHub issue, and the board then follows that
issue's life. The daemon gains one more job on its own interval; the schema gains one table; the
boundary layer gains one seam and one method.

Four decisions shape everything else:

1. **Trello is a sixth boundary, not a second `IssueSource`** (R1). The roadmap poses this milestone
   as the test of whether 001's source seam was drawn in the right place. It was — but not because
   Trello fits through it. GitHub is where dispatchable work is read; Trello is where intake is read,
   and its output is a GitHub issue. No caller ever holds one where it could equally hold the other,
   which is the actual test of whether a shared protocol earns its keep.
2. **`needs_info` lives on the card, not on the work item** (R5). This reverses planning §7 and is the
   largest decision here. `work_items.repo_key` is `NOT NULL REFERENCES repos(repo_key)` and
   `issue_number` is `NOT NULL`; a card awaiting clarification has neither, by definition. The spec
   was amended during planning rather than the central table being rebuilt to weaken an invariant
   every other row depends on.
3. **The §11 invariant is two unique indexes**, not a rule the create path follows. A create that
   skipped its mapping check raises `IntegrityError` rather than producing a duplicate.
4. **Creation is a four-step sequence with the intent written first** (R6), because the dangerous
   window — issue created, nothing local knows it — is exactly the one a crash lands in, and GitHub's
   search index is too slow to be the recovery mechanism.

## Technical Context

**Language/Version**: Python 3.14 (unchanged; `requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency and now speaks to a
second host (R2). Trello SDKs were evaluated and rejected: they remove close to no work, hide the
response headers a bounded-retry policy needs, and fail Principle I's "justified by the work they
remove" test.

**Storage**: the existing SQLite database, plus one new table (`cards`) added by migration **003**.
Board poll bookkeeping reuses `poll_state` under the synthetic key `trello:board:<board_id>` (R13);
that table has no foreign key and no consumer outside `poll.py` and `db.py`, so a non-repository key
is safe and a second identically shaped table is not needed.

**Testing**: pytest. Unit tests for repository resolution against adversarial card text, the card
state machine's illegal transitions, duplicate suppression order, the `dateLastActivity` self-write
trap, board precondition checks, credential redaction, and the simulated writers' structural
validity. Integration tests drive the four-step creation with a fake board and a fake issue writer,
killing the sequence at each step in turn. One test must be marked as requiring a real disposable
board and skipped in CI, for the reason the roadmap already records about CI's ceiling.

**Target Platform**: the author's Linux desktop, unchanged.

**Project Type**: single project. One new boundary module, one new service module, one migration, two
CLI verbs, one web view.

**Performance Goals**: a board poll is one API call for the card listing plus at most one per card
that needs a freshness re-read. At a 300-second interval and a board of a few dozen cards this is
nowhere near any published Trello rate limit (300 requests per 10 seconds per key).

**Constraints**: no credential may appear in any log line, error message, or served page — which for
this API means credentials must not travel in the query string at all (R3); no board write may occur
below the `live` effect level (FR-039); board ingestion failing must not stop dispatch of issues the
author wrote themselves.

**Scale/Scope**: one board, one label, a few cards a week. The invariant matters far more than the
throughput.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. Re-check result at the bottom.*

### I. Simplicity First (YAGNI & KISS)

| Check | Result |
|---|---|
| New third-party dependencies justified by work removed | **Pass** — none added (R2) |
| No speculative generality | **Pass** — the new seam has exactly two implementations, both required by FR-039 and FR-040, the same standing the existing five have. No registry, no plugin discovery, no configuration-driven selection |
| Single process, plain files, obvious control flow the default | **Pass** — one more job in the existing daemon loop (R14); no new process, no thread, no queue |
| Fewest moving parts wins between two adequate designs | **Pass with two items tracked** — the separate `cards` table and the four-step creation sequence, both justified below |

The generic-source temptation is the one worth naming. A `WorkItemSource` protocol covering both
GitHub and Trello *could* be written; it was rejected because its two implementations would never be
used polymorphically, which is precisely the "strategy interface with one caller and no second use in
hand" Principle I forbids. `contracts/boundaries.md` already reached the same conclusion about kitty
and dtach, from a different direction.

### II. Single-User, Local-First

| Check | Result |
|---|---|
| No authentication, authorization, accounts, or roles built | **Pass** — and the board's own privacy is *checked* rather than assumed (R10), which strengthens the existing boundary instead of adding a new one |
| State on the local filesystem, no hosted service required | **Pass** — same database; the board is a source, and the daemon runs and dispatches without it |
| Secrets from environment or git-ignored files, never in logs | **Pass, with a specific hazard closed** — Trello's documented auth is a query string, which would put both secrets into every logged URL and past the field-name redaction in `audit.py`. The header form is used instead (R3) |
| No public IP, reverse proxy, or deployment infrastructure assumed | **Pass** — outbound HTTPS only |

The trust boundary for this path is board access, as the planning document states. Principle II's
prohibition is on *building* authentication, and none is built; what R10 adds is a precondition check
that the assumption still holds, which is the "revisit if the board is ever shared" the planning
document asked for and could not perform on its own.

### III. Total Accountability

| Check | Result |
|---|---|
| Every outward-facing action logged when it occurs | **Pass** — every issue creation, card comment, and card move is an `audit.action` intent/outcome pair written before the call |
| Records carry timestamp, component, action, target, params, outcome | **Pass** — the ten new actions are listed in data-model.md |
| No silent failure | **Pass** — a board transport failure raises `TransportError` and is recorded; it is never converted into an empty card list, which is the distinction FR-009 exists for |
| Documented exceptions enumerated | **One, below** |

**Enumerated Principle III exception.** Individual board *read* calls made within a poll cycle — the
freshness re-read before a move, and the comment fetch on the recovery path — are not separately
audited. The cycle itself is, with what it evaluated and what it decided about each card. These reads
change no state outside the process, so the principle's scope does not reach them; the exception is
recorded because the reads are numerous enough that logging each would bury the records that matter.
Nothing they observe is unreconstructable: the decision each one fed is logged with its inputs.

### IV. Interruption Tolerance

| Check | Result |
|---|---|
| Atomic writes to persistent state | **Pass** — every mutation reuses `db.transaction` (`BEGIN IMMEDIATE`); migration 003 advances `user_version` as its last statement, as the ladder requires |
| Restartable, idempotent, incomplete work detected | **Pass** — data-model.md's interruption table has a row per kill point, each with its recovery and each with a test |
| Explicit timeouts and bounded retries on every network call | **Pass** — the board client mirrors the GitHub one: explicit connect and read timeouts, bounded exponential backoff with jitter, `Retry-After` honoured on `429` |
| Precautions reasonable, not extreme | **Pass** — an intent row and a bounded issue listing. No two-phase commit, no outbox, no distributed anything |

**What this logs**: every board read cycle with its verdict per card, and every write as an
intent/outcome pair. **What happens if it is killed halfway**: the four-step creation is resumable at
each of its three seams — an unfinished intent is resolved by listing issues created since the intent
timestamp and matching the card's URL in the body (R6), a missing card comment is posted on the next
pass after checking for an existing marker, and an unrecorded card move is identified by
`pending_move_to` rather than mistaken for a move by the author (R12).

The residual gap is stated rather than papered over: a crash between issue creation and mapping
*combined with* total loss of the database leaves an orphaned issue that the next poll will duplicate.
It is a double failure, the stray issue is unlabelled and therefore dispatches nothing, and closing it
would mean scanning every configured repository before every creation. Recorded in R6 and in the
Complexity Tracking table below.

### V. Public Code, Unsupported Project

| Check | Result |
|---|---|
| No credentials, personal data, or private addresses committed | **Pass** — board id is configuration; the quickstart uses placeholders |
| No stable public API maintained | **Pass** — two new routes on an interface FR-009 of milestone 002 already declares unstable |
| Documentation written for the author's future self | **Pass** — `quickstart.md`, plus updates to `docs/state.md`, `docs/logging.md`, and `README.md` |
| No packaging or release pipeline | **Pass** — two CLI verbs, no artifact |

### Operating Constraints

| Check | Result |
|---|---|
| Every capability reachable and observable from the terminal | **Pass** — `cards` and `rescan` land in the same change as the `/cards` view and its rescan button |
| Commands exit non-zero on failure | **Pass** — existing exit-code table; `contracts/surfaces.md` fixes the code for each new failure |
| Persistent data plain text or SQLite | **Pass** — one new table |
| Irreversible or outward-facing actions confirmed and logged before execution, and not reachable by default | **Pass** — issue creation is logged before execution and is reachable **only** through the explicit `[trello]` configuration, which is absent by default (FR-001, FR-006) |

**Gate result: PASS.** Two items carry justification in Complexity Tracking; neither is a violation
requiring redesign.

## Project Structure

### Documentation (this feature)

```text
specs/003-trello-source/
├── plan.md              # This file
├── research.md          # Phase 0 — R1..R16, every decision and what was rejected
├── data-model.md        # Phase 1 — the cards table, its state machine, interruption behaviour
├── quickstart.md        # Phase 1 — nine runnable validation scenarios
├── contracts/
│   ├── card-source.md   # The new boundary, the effect-table rows, create_issue
│   ├── config.md        # The [trello] section and its load-time and startup checks
│   └── surfaces.md      # New CLI verbs and web routes, and what is deliberately absent
├── checklists/
│   └── requirements.md  # From /speckit-specify
└── tasks.md             # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/robot_army/
├── boundaries/
│   ├── __init__.py          # + CardSourceReader / CardSourceWriter protocols, Card, BoardInfo
│   ├── trello.py            # new — TrelloCardReader, TrelloCardWriter, SimulatedCardWriter
│   └── github.py            # + create_issue on GitHubWriter and SimulatedIssueWriter
├── effects.py               # + two REAL_AT rows, + two wirings
├── intake.py                # new — the board job: evaluate, resolve, create, recover, lifecycle
├── cardstates.py            # new — the card state machine and its single transition gate
├── db.py                    # + cards accessors
├── migrations.py            # + _migration_003 (cards table and its two unique indexes)
├── models.py                # + Card row dataclass
├── daemon.py                # + the board job; + board preconditions at startup
├── health.py                # + board reachability on the heartbeat
├── operations.py            # + cards, rescan; + card link on show
├── config.py                # + [trello] section and its validation
├── cli.py                   # + cards, rescan
└── web/
    ├── server.py            # + GET /cards, POST /card/{id}/rescan
    └── pages.py             # + the cards view

tests/
├── unit/
│   ├── test_repo_resolution.py   # adversarial card text; pasted logs must resolve to nothing
│   ├── test_card_states.py       # legal and illegal transitions, enumerated
│   ├── test_card_dedup.py        # mapping-first ordering; comments read only when it is absent
│   ├── test_card_activity.py     # R9: our own comment must not trigger a rescan
│   ├── test_board_preconditions.py  # privacy, membership, label, lists
│   ├── test_trello_secrets.py    # no key or token in any record, including failures
│   └── test_simulated_writers.py # structural validity of create_issue and card writes
└── integration/
    ├── test_card_to_issue.py     # the happy path end to end against fakes
    ├── test_card_interruption.py # killed at each of the three seams; no duplicates
    └── test_card_lifecycle.py    # In Progress / Done / returned; the manual-move refusal

docs/
├── state.md                 # + the cards table and the synthetic poll_state key
├── logging.md               # + the ten trello.* actions, + the read exception
└── README.md                # + configuring the board
```

**Structure Decision**: two new small modules inside the existing package rather than a `trello`
sub-package. `boundaries/trello.py` is the only code that knows the API exists; `intake.py` is the
only code that knows what a card *means*. Splitting them further would create packages with one
module in them. `cardstates.py` is separate from `states.py` because the existing module's two
machines are about dispatchable work and this one is not, and merging them would put a state that can
never be dispatched into the same enumeration the dispatcher reads.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| A second entity table (`cards`) alongside `work_items` | A card awaiting clarification names no onboarded repository and no issue, and `work_items` requires both as `NOT NULL`. The mapping must also outlive any work item: a card's issue may sit unlabelled for weeks, and may be refused at onboarding and never become a work item at all | Making `repo_key` and `issue_number` nullable means rebuilding the central table — SQLite cannot drop `NOT NULL` in place — to weaken an invariant every other row depends on, so that one row shape can be represented. A sentinel `repos` row to satisfy the foreign key is a fake value inserted to make a state machine fit, which is how a schema starts lying |
| A four-step creation with an intent row and a listing-based recovery | The window between creating the issue and recording the mapping is where a crash produces the exact duplicate §11 exists to forbid, and it is unobservable afterwards without help. The intent row plus the card URL in the issue body makes it observable | "Create, then record, and accept the rare duplicate" is two lines shorter and abandons the milestone's central invariant. Recovering by GitHub *search* is simpler than listing but wrong: the search index lags by minutes, so an issue created seconds before the crash is invisible to it — producing precisely the duplicate the mechanism exists to prevent |

**The residual gap, tracked rather than closed**: crash-between-create-and-mapping *plus* total
database loss leaves an orphaned issue that the next poll duplicates. Closing it means scanning every
configured repository's recent issues before every creation — a cost paid on every card forever to
cover a double failure whose damage is one stray unlabelled issue that dispatches nothing. Recorded
in R6, and revisited only if it ever actually happens.

**Not listed as a violation**: the sixth boundary with two implementations. Each of the existing five
carries the same shape for the same reason, and `contracts/boundaries.md` already records why the
simulated implementations are the dry-run feature rather than scaffolding for a hypothetical future.

## Post-Design Constitution Re-Check

Re-run after the Phase 1 artifacts were written.

- **No new dependency appeared during design.** The board client, the resolution parser, and the
  recovery listing all landed in `httpx` plus the standard library. **Pass.**
- **The new table did not sprout policy.** It holds one card's facts and its lifecycle position; no
  settings, no per-board configuration, no second board. **Pass.**
- **The invariant moved from code into the schema during design**, which is the direction Principle I
  prefers: two unique indexes replace a rule the create path would otherwise have to remember.
  **Pass.**
- **One requirement was changed rather than implemented as written.** FR-020 placed `needs_info` on
  the work item, following planning §7; the schema makes that impossible without rebuilding
  `work_items`. The spec was amended, the reversal is recorded in its Assumptions section and in R5,
  and the resulting design makes the human gate structural — board activity cannot produce a
  dispatchable row at all. This is the Governance rule working: the conflict was raised before the
  work, not discovered after it. **Pass.**
- **The Principle III exception is exactly one** — individual board reads within a cycle — and is
  documented here and in `docs/logging.md`. **Pass.**
- **One hazard was found during design that the spec did not anticipate**: Trello's documented
  query-string authentication would defeat `audit.py`'s field-name redaction entirely, putting both
  secrets in every logged URL. The header form removes it at the source (R3). No requirement changed;
  the constraint is now explicit in the technical context. **Pass.**
- **A second hazard was found and closed**: commenting on a card changes its `dateLastActivity`, which
  is the rescan trigger — a self-sustaining re-evaluation loop that no requirement forbids because
  nobody thought of it. R9 refreshes the baseline from our own writes, with a test. **Pass.**

**Re-check result: PASS.** No violation requires redesign; the two tracked items and the one residual
gap stand as written.
