# Phase 0 Research: Trello Column Ignore List

**Feature**: [spec.md](spec.md) | **Date**: 2026-08-27

Six questions the spec leaves to the plan, and one it names explicitly as structural. Each is
answered against the code that exists, not against a general principle — milestone 003 built the
board path and this milestone adds a value to it, so almost every question here is "where does the
existing seam already know this?" rather than "what should we build?".

---

## R1: Where the exclusion decision is made

**Decision**: Two call sites, one predicate. `intake.poll_board` skips *tracking* an untracked card
in an ignored column; `intake.evaluate_card` returns without acting on a *tracked* card currently in
one. Both call one helper, `_is_ignored(list_id, status)`, so there is one definition of ignored.

**Rationale**: The two sites answer different questions and neither subsumes the other.

`poll_board` is where a card first becomes a database row. Not tracking an ignored card is what makes
FR-006 true structurally: a card that has no row cannot be surfaced by `robot-army cards` or the web
listing, because both read rows. It also keeps the table proportional to work rather than to the
board — an icebox column with two hundred cards in it costs nothing.

`evaluate_card` is where a card that is *already* tracked gets acted on, and a tracked card can enter
an ignored column at any time. It is also the module's stated pattern for a gate that must not be
forgotten: `poll_board` already re-checks `status.ok` with the comment "the daemon gates on this too;
the guard lives here as well so it cannot be forgotten by a new caller."

**Placement inside `evaluate_card` is the load-bearing part**, and it is fixed by three requirements
at once. The check goes *after* the `linked`, `dropped` and `creating` branches and *before*
`_restore_from_marker`:

| Placed after | Because |
|---|---|
| `linked` | FR-013 — a card with a recorded issue is never ignored, in either direction. This is what makes FR-015 true for free: by the time the daemon puts a card in the in-progress or done column it is already `linked`, so listing either column as ignored is a no-op rather than a contradiction. |
| `creating` | The spec's edge case "moved into an ignored column while its issue is being created". The intent is recorded and R6's recovery must still run against it. Parking must not cancel a creation, or the one-issue invariant becomes "one issue unless you dragged the card at the wrong moment". |
| `dropped` | A dropped row is terminal and the ignore list is not a route back (FR-012). |

| Placed before | Because |
|---|---|
| `_restore_from_marker` | It reads the card's comments — a board request per card per cycle. An ignored card should cost nothing. Skipping it loses nothing, because the restore runs on the first cycle after the card is un-parked, and it is only ever a *duplicate-prevention* read: deferring it cannot create a duplicate, since nothing downstream of the skip creates anything. |
| the resolvability branch | FR-005 verbatim. |

**Alternatives considered**:

- *Filter inside the boundary, so `poll()` never returns ignored cards.* **Rejected, and this is the
  trap the whole feature turns on.** `intake._reconcile_board_contents` drops every tracked card that
  is absent from the poll listing, and `dropped` is terminal — `CARD_TRANSITIONS` gives it no exit.
  Filtering at the reader would therefore make parking a tracked card permanently destroy it, and
  un-parking would do nothing, forever, silently. That is precisely the failure User Story 2 exists
  to prevent. It also puts policy in the transport, which the boundaries contract forbids for the
  same reason it forbids the writer deciding whether a move is allowed.
- *One gate in `poll_cycle`'s evaluation loop instead of inside `evaluate_card`.* Rejected: it leaves
  `evaluate_card` — a public function with its own tests and its own callers — willing to act on an
  ignored card.

---

## R2: What "parked" is made of

**Decision**: Parked is a **derived condition**, not a state. No new `CardState`, no new transition,
no change to `CARD_TRANSITIONS`. A card is parked when it is tracked, not `linked`, and its current
column is in the ignored set.

**Rationale**: FR-008 requires a parked card to "retain the state and reason it had". A state cannot
retain another state. Modelling parked as a `CardState` would need `needs_info → parked → needs_info`
plus `discovered → parked → discovered`, and the `reason` column would have to be preserved across
both hops — reintroducing, as bookkeeping, exactly the information the derived form already has.
It would also make a card that is *both* awaiting clarification and parked unrepresentable, and that
is the common case: the author writes an ambiguous card and parks it precisely because they are not
ready to disambiguate it yet.

This keeps `cardstates.py` untouched, which matters beyond tidiness — its transition table is the
single gate every card change passes through, and the module's own docstring says the enumeration
exists so illegal cases can be listed in a test.

**Alternatives considered**:

- *A `parked` `CardState`.* Rejected above.
- *A boolean `parked` column.* Rejected: it is a cached derivation of `current_list_id ∈ ignored`
  that goes stale the moment the configuration changes, which FR-011 requires to take effect on the
  next poll with no other action. Storing the column and deriving the verdict cannot go stale.

---

## R3: How a surface knows a tracked card is parked

**Decision**: Add one nullable column, `cards.current_list_id`, refreshed from the board on every
poll by the existing `_refresh_tracked_card`. Schema migration 006.

**Rationale**: This is the one place the milestone genuinely adds persistent state, so it is worth
saying why the three columns already there do not answer the question. `origin_list_id` is where the
card was at first sighting, `placed_list_id` is where *we* last put it, and `pending_move_to` is where
we are in the middle of putting it. None of them is *where the card is now*, and the board's answer to
that has never been stored because until now nothing needed it.

`robot-army cards` and the web listing are local, read-only commands that must work with the board
unreachable. Deriving parkedness at render time would mean a board request from a listing command —
which would also make the listing fail when the board is down, for a question the daemon answered
five minutes ago.

The column pays for itself twice more: FR-023's park and release records are exactly the transitions
of this column across the ignored set, detectable without a second read; and FR-017's diagnostics can
name the column a card is actually sitting in.

**Interruption**: written inside the existing `db.update_card_columns` transaction in
`_refresh_tracked_card`. A process killed before the write leaves the previous value and the next
poll recomputes — the value is a cache of the board's truth, and the board is authoritative.

**Alternatives considered**:

- *Reuse `origin_list_id`.* Rejected outright: it is what FR-029 returns an abandoned card to, and
  overwriting it with the card's current position would return a card to wherever it last happened
  to be rather than where the author left it.
- *Overload `reason`.* Rejected: `reason` is compared against `commented_reason` to decide whether to
  comment on the card, so writing a park explanation into it would post a clarification comment about
  being parked — violating FR-004 and FR-010 in one move.

---

## R4: Duplicate column names on the board

**Decision**: Add `BoardInfo.lists_by_id: dict[str, str]` — **id → name**, the inverse of the
existing `lists` — populated from the same already-fetched response. The ignored id set is
`{lid for lid, name in lists_by_id.items() if name in ignore_lists}`.

**Rationale**: FR-019b requires excluding cards in *every* column of a configured name, and the
existing `BoardInfo.lists` is `name → id`, so two columns called "Icebox" collapse to one entry and
one of them silently stays intake. Inverting the mapping fixes it by construction rather than by a
rule: list ids are unique, so `id → name` loses nothing and preserves duplicates automatically. No
extra request — it is built from the response `lists` is already built from.

`lists` stays exactly as it is. The existence checks (FR-016, FR-017) are name-membership questions
and `name in info.lists` answers them correctly whether or not the name is duplicated.

**Noted and deliberately not fixed here**: `in_progress_list` and `done_list` resolve through
`lists` and therefore already pick an arbitrary one of two same-named columns. That is a pre-existing
milestone 003 behaviour, it is not made worse by this change, and fixing it means deciding what
moving a card to an ambiguous destination should mean — a different question with a different blast
radius. Recorded here so it is a known limit rather than a discovery.

**Alternatives considered**:

- *Change `lists` to `name → tuple[id, ...]`.* Rejected: it ripples into `in_progress_list_id`,
  `done_list_id`, `_present()`, and every test that constructs a `BoardInfo`, to express something an
  inverted dict expresses for free.
- *Match on name per card instead of resolving to ids.* Rejected: the per-card filter is an id
  equality check by design (R11 of milestone 003 — "immune to a label being renamed mid-run"), and
  reaching name-per-card would mean a lookup per card anyway.

---

## R5: Where the names are validated, and how strictly

**Decision**: Three layers, each already existing.

1. **Load** (`config.py`): `ignore_lists` must be a list of non-empty strings, or it is a
   `ConfigError`. `ignore_lists` joins `_SECTION_KEYS["trello"]`, so it is a recognised key rather
   than an "unknown key in `[trello]`" error. Duplicates are collapsed with `dict.fromkeys`, exactly
   as `[notifications] events` already does.
2. **Startup and `doctor`** (`intake.check_board`): one `_present()` check per configured name,
   appended after the tag and lifecycle-column checks. A missing one makes `BoardStatus.ok` false,
   which `poll_board` already gates on — so a failing check refuses **ingestion only**, and dispatch
   of the author's own issues is untouched (FR-018).
3. **Per poll**: nothing. The ids are resolved once into `BoardStatus.ignored_list_ids` and compared
   by equality thereafter.

**Rationale**: All three are the existing pattern, applied to one more value. `_present()` already
produces the message FR-017 asks for — it names what is missing and lists what the board does have,
which its docstring calls "the difference between 'the label is missing' and 'the label is missing,
and here are the six that exist, one of which you renamed'".

**Matching is exact, including case, and whitespace is not stripped** (FR-019). Same rule as `label`,
`in_progress_list` and `done_list`, and the reason to keep it identical is that a near-miss is
*reported*: a trailing space produces `'Icebox ' not found — the board has: Icebox, Doing, Done`,
which reads as the answer. A normalising match would silently accept a name the author did not write,
and there would then be two different matching rules inside one config section.

**Alternatives considered**:

- *Warn rather than refuse on a missing column.* Rejected. Every other check in this section refuses,
  and the failure mode here is the worst kind: intake silently widens back to milestone 003's, the
  icebox files issues, and nothing looks broken. A warning that widens what the system acts on is not
  a warning.
- *Case-insensitive matching.* Rejected as above.

---

## R6: What gets logged, and what deliberately does not

**Decision**:

| Event | Record | Cardinality |
|---|---|---|
| Poll cycle | `trello.poll` gains `ignored` alongside the existing `tagged` and `newly_tracked` | one per cycle (already exists) |
| Startup checks | `trello.board.check` already serialises every `BoardCheck`; the per-column checks appear there automatically, plus `ignored_lists` in the detail | one per process (already exists) |
| A tracked card enters an ignored column | `trello.parked`, naming the card and the column | one per transition |
| A tracked card leaves one | `trello.released`, naming the card and the column | one per transition |
| An untracked card seen in an ignored column | **nothing individually** | counted only |

**Rationale for the omission** (FR-024, and the Principle III exception the plan must enumerate):
`poll_board` already takes exactly this exception for its per-card reads, on the recorded grounds
that the reads change no state outside the process and that logging each would bury the records that
matter. An ignored card is the strongest possible case for it — the whole point is that **nothing
happens**, the same nothing, every five minutes, for as long as the card sits there. On a board with
a hundred iceboxed cards that is 28,800 identical records a day, and the constitution's standard is
reconstruction: `tagged=140, ignored=100, newly_tracked=0` plus the ignored column names in the
startup record reconstructs the decision completely.

The two transition records are not an exception to anything — they are once-per-event and they are
the answer to "why did this card stop being evaluated?", which is the question a silent hold would
otherwise leave unanswerable.

---

## R7: Vocabulary — "parked", not "held"

**Decision**: The implementation word is **parked**. `held` is not reused.

**Rationale**: `held` is already taken, twice, for something else. `web/pages.py` renders the
`needs_info` state as *"held — the card does not say which repository"*, and `PollOutcome.held`
counts cards in that state. A card can be awaiting clarification **and** in an ignored column
simultaneously, so one word cannot cover both without producing a listing that says "held" for two
unrelated reasons and a counter nobody can interpret. The spec was amended to match before this plan
was written, so there is one word in both documents.
