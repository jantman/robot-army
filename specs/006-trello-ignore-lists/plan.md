# Implementation Plan: Trello Column Ignore List

**Branch**: `006-trello-ignore-lists` | **Date**: 2026-08-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-trello-ignore-lists/spec.md`

## Summary

Intake changes from *"a tagged card"* to *"a tagged card in a column the author has not excluded"*.

One new configuration value, `[trello] ignore_lists`, holds column names. Their ids are resolved once
at startup into `BoardStatus.ignored_list_ids`, alongside the tag and lifecycle-column ids already
resolved there, and each configured name gets its own existence check so a rename is reported rather
than silently widening intake back to where it was.

The exclusion itself is two guards calling one predicate. `poll_board` does not *track* an untracked
card in an ignored column, which is what keeps it out of every listing; `evaluate_card` does not
*act* on a tracked one, positioned after its `linked` / `creating` / `dropped` branches so that a
card with a recorded issue is never affected in either direction.

The one structural change is the one the spec named. Milestone 003 records any tracked, unlinked card
absent from the poll listing as having *left the board*, and that outcome is terminal. Filtering
ignored cards out of the poll listing would therefore make parking a card destroy it permanently and
silently — so ignored cards stay in the listing, and "parked" becomes a **derived condition** rather
than a state: tracked, not linked, current column in the ignored set. Deriving it needs one thing the
database has never stored — where a card is *now*, as opposed to where it started or where we put it
— so `cards.current_list_id` is added in schema migration 006.

Everything else is subtraction. No new request is made, no new write is performed, no new state
exists, and an installation that sets nothing behaves exactly as it did in milestone 003.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: None added. `httpx` remains the sole runtime dependency, and this feature
adds no request — the ignored ids come out of the `GET /boards/{id}/lists` response `board_info()`
already makes, and a card's column arrives in the `idList` field `poll()` already asks for.

**Storage**: SQLite at the documented state path. One nullable column added to `cards` by forward-only
migration 006; `PRAGMA user_version` goes 5 → 6.

**Testing**: pytest. Unit tests are required for every changed unit of behaviour, and the
park/release transitions and the migration additionally need failure- and interruption-path tests
per the constitution's Development Workflow section.

**Target Platform**: Single Linux machine, single user.

**Project Type**: Single project — CLI plus daemon plus a read-only local web view.

**Performance Goals**: Unchanged. The board poll stays at one listing request per cycle; resolving
the ignored ids is a dict comprehension over a memoised response.

**Constraints**: The default must be behaviourally inert (FR-002). `robot-army cards` and the web
listing must keep working with the board unreachable, which is what forces the parked condition to be
answerable from local state.

**Scale/Scope**: One board, one author. Card counts in the hundreds. Roughly seven modules touched:
`config.py`, `boundaries/__init__.py`, `boundaries/trello.py`, `intake.py`, `migrations.py`, `db.py`
and `models.py`, plus `operations.py` and `web/pages.py` for the listings.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — see below.*

### I. Simplicity First (YAGNI & KISS) — **PASS**

- **No new dependency.** No new request, either.
- **No new abstraction.** No plugin point, no strategy interface, no registry. The exclusion is one
  predicate over a frozenset, called from two places for two stated reasons (research R1).
- **No new state machine.** `cardstates.py` is untouched: parked is derived, not stored, so
  `CARD_TRANSITIONS` gains no entries and the "illegal cases can be enumerated in a test" property
  survives intact (research R2).
- **Two additions carry a justification**, and both are recorded in Complexity Tracking below rather
  than waved through: one database column and one field on a value type.
- **The knob has a caller and a demonstrated need.** It is [issue #3](https://github.com/jantman/robot-army/issues/3),
  filed from using the thing.

### II. Single-User, Local-First — **PASS**

No account, role, or permission is introduced. No secret is added — the setting is a list of column
names, and the existing `_looks_like_token` sweep over `[trello]` values covers it for free. No new
network dependency: the feature makes the system do *less* over the network, never more. All state
stays in the existing SQLite file at the documented path.

### III. Total Accountability — **PASS, with one enumerated exception**

**What this logs** (the first question every plan must answer explicitly):

| Action | Record | When |
|---|---|---|
| Each configured ignored column's existence check | `trello.board.check` (existing; the checks are already serialised into its detail) | once per process, and on every `doctor` |
| Ignored cards skipped this cycle | `trello.poll` gains `ignored` beside `tagged` and `newly_tracked` | once per poll cycle |
| A tracked card enters an ignored column | `trello.parked`, naming the card and the column | once per transition |
| A tracked card leaves an ignored column | `trello.released`, naming the card and the column | once per transition |

**The enumerated exception**: an individual card seen in an ignored column and skipped is **not**
recorded per card per cycle. This extends the exception `poll_board` already takes and states, on
identical grounds — the read changes no state outside the process, and the record would say the same
thing about the same card every five minutes forever. A board with a hundred iceboxed cards would
otherwise emit ~28,800 identical records a day and bury the ones that matter. The reconstruction
standard is still met: `tagged`, `ignored`, and `newly_tracked` for the cycle, plus the ignored
column names in the startup record, answer what the system did, to what, and with what result
without re-reading the board.

Nothing is swallowed. The feature adds no `except` clause; a board failure still raises
`TransportError` and is still recorded, and "everything was ignored" is reported as a count that is
structurally distinct from "I could not ask" (`skipped_reason` / `error`).

### IV. Interruption Tolerance — **PASS**

**What happens if it is killed halfway through** (the second required question):

- **Mid-migration**: the existing ladder's guarantee. `PRAGMA user_version` is advanced as the last
  statement inside the transaction, so a kill leaves version 5 and migration 006 re-runs whole.
- **Mid-poll**: `current_list_id` is written inside the existing per-card `db.transaction` in
  `_refresh_tracked_card`. A kill leaves the previous value, and the next poll recomputes it from the
  board. The column is a cache of the board's own truth, so a stale value cannot be authoritative —
  and because parked is derived from it rather than stored, a stale value self-corrects rather than
  needing repair.
- **Killed between parking a card and recording it**: the record is written in the same transaction
  as the column change, matching `transition_card`'s existing discipline. Neither can exist without
  the other.
- **Configuration changed while the daemon is down**: nothing to recover. The ignored set is resolved
  at startup from the board and the config, so the next start simply resolves the new one.
- **No new network call**, therefore no new timeout or retry policy. The existing bounded backoff
  covers everything this touches.

### V. Public Code, Unsupported Project — **PASS**

No credential, personal datum, or private hostname enters the repository — column names in
`share/config.example.toml` are illustrative. No compatibility shim: migration 006 is forward-only
like its five predecessors, and there is no downgrade path because there is no outside consumer to
owe one to. Documentation is for the author's future self: `contracts/config.md` says what the key
does and `quickstart.md` says how to prove it works.

### Development Workflow — **PASS**

Unit tests for every changed behaviour, including the failure and interruption paths the constitution
requires for persistence and for code parsing external input: the migration, the config parser's
rejection of malformed values, the missing-column refusal, and the park/un-park round trip. The
tasks phase must produce at least one test asserting the property FR-002 states — that with
`ignore_lists` unset, behaviour is identical to milestone 003's.

## Project Structure

### Documentation (this feature)

```text
specs/006-trello-ignore-lists/
├── plan.md              # This file
├── research.md          # Phase 0 — seven decisions with alternatives
├── data-model.md        # Phase 1 — the column, the derived condition, the value types
├── quickstart.md        # Phase 1 — how to prove it works, including what CI cannot
├── contracts/
│   ├── config.md        # [trello] ignore_lists: shape, validation, messages
│   ├── board-checks.md  # BoardInfo/BoardStatus additions and the startup checks
│   └── surfaces.md      # the gate, the audit records, cards/web/doctor output
├── checklists/
│   └── requirements.md  # spec quality checklist (from /speckit-specify)
└── tasks.md             # NOT created by /speckit-plan
```

### Source Code (repository root)

Files this feature touches. No new module: every change lands where the thing it changes already
lives, which is the point of milestone 003 having built the seam.

```text
src/robot_army/
├── config.py                 # TrelloConfig.ignore_lists; parsing, validation, key allowlist
├── models.py                 # Card.current_list_id
├── migrations.py             # SCHEMA_006_SQL + _migration_006; user_version 5 -> 6
├── db.py                     # current_list_id through insert_card / row mapping
├── boundaries/
│   ├── __init__.py           # BoardInfo.lists_by_id
│   └── trello.py             # populate lists_by_id from the response already fetched
├── intake.py                 # THE FEATURE: _is_ignored, BoardStatus.ignored_list_ids,
│                             #   check_board checks, poll_board tracking guard,
│                             #   evaluate_card gate, park/release records in
│                             #   _refresh_tracked_card, PollOutcome.ignored
├── operations.py             # cards listing: parked column and reason
└── web/pages.py              # cards page: parked shown distinctly from "held"

tests/unit/
├── test_config.py                 # ignore_lists parsing, dedup, malformed rejection
├── test_migrations.py             # 5 -> 6, idempotency, interrupted re-run
├── test_board_preconditions.py    # per-column existence checks; refusal is ingestion-only
├── test_intake_poll.py            # untracked ignored cards are not tracked; counts
└── test_ignored_lists.py          # NEW: the gate, park/un-park round trip, linked
                                   #   immunity, duplicate board columns, inert default
```

**Structure Decision**: The existing single-project layout under `src/robot_army/` with unit tests in
`tests/unit/`. One new test module because the park/un-park round trip is a behaviour of its own
rather than an extension of any existing file's subject; every other change is an addition to the
test module that already owns that behaviour.

## Complexity Tracking

Two additions that Principle I requires a written justification for, plus the one thing that looks
like a violation and is not.

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| `cards.current_list_id` (one nullable column, migration 006) | FR-006, FR-009 and FR-023 all need to know whether a *tracked* card is currently in an ignored column, and `robot-army cards` must answer it with the board unreachable. The three list-id columns that exist answer different questions: first sighting, where we put it, where we are putting it. | Deriving at render time means a board request from a read-only listing command, which then fails when the board is down. Reusing `origin_list_id` corrupts what FR-029 returns an abandoned card to. Caching a boolean `parked` flag goes stale the moment the configuration changes, which FR-011 forbids. |
| `BoardInfo.lists_by_id` (one field, no request) | FR-019b: `lists` is `name → id`, so two columns of the same name collapse and one silently stays intake. Inverting to `id → name` preserves duplicates by construction, since ids are unique. | Changing `lists` to `name → tuple[id, ...]` ripples into both lifecycle-list resolutions, `_present()`, and every test that builds a `BoardInfo`, to express what an inverted dict expresses for free. Matching by name per card abandons R11's id-equality filter. |

**Not a violation, recorded because it looks like one**: the exclusion predicate is called from two
places. That is not duplicated policy — there is one `_is_ignored`, and the two call sites answer
genuinely different questions (*should this card become a row?* and *should this row be acted on?*).
`poll_board` already re-checks `status.ok` for exactly this reason, with the existing comment "the
guard lives here as well so it cannot be forgotten by a new caller".

**Deliberately not built**, so a later reader does not mistake absence for oversight: no `parked`
card state, no per-card override, no scheduling, no default ignore set, no notification for an
ignored card, and no fix for the pre-existing ambiguity of `in_progress_list` / `done_list` against
duplicate board column names (research R4 records it as a known limit).

## Post-Design Constitution Re-Check

Re-evaluated after `data-model.md` and `contracts/` were written. **No gate changed verdict.**

Three things the design work surfaced, none of which moved a gate:

1. **The word "held" was already taken**, twice — `web/pages.py` renders `needs_info` as "held", and
   `PollOutcome.held` counts it. A card can be awaiting clarification *and* parked at once, so one
   word could not carry both. The spec and every artifact here now say **parked**. Vocabulary
   discipline rather than a constitutional matter, but it is exactly the confusion milestone 003 went
   to trouble to avoid between *tag* and *label*.
2. **The `evaluate_card` gate's position is load-bearing**, not stylistic. Placed after the `creating`
   branch it satisfies the spec's mid-creation edge case; placed after `linked` it makes FR-015 true
   without a special case; placed before `_restore_from_marker` it costs an ignored card zero board
   requests. `contracts/surfaces.md` states the ordering as a contract so a later edit cannot reorder
   it by accident, and the tasks phase must produce a test per row of that table.
3. **The Principle III exception is narrower than 003's**, which is worth noting in its favour: 003
   omits a record per card read, while this omits a record only for cards it decides to do nothing
   about. Every card that becomes anything is still recorded individually.
