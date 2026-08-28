# Data Model: Trello Column Ignore List

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md)

Almost nothing here is new, which is the point. One configuration field, two database columns, two
fields on value types, and one condition that is computed rather than stored.

## Configuration

### `TrelloConfig.ignore_lists`

```
ignore_lists: tuple[str, ...] = ()
```

Board column names whose cards are not intake. Empty by default, which is what makes FR-002 true:
an installation that does not write the key resolves an empty ignored id set, and every comparison
against it is false.

A tuple rather than a set, and ordered as written, because the `doctor` output and the failure
messages read back in the author's own order — a set would reorder the report of a file the author
is looking at while reading it.

| Rule | Failure |
|---|---|
| Must be a list of strings | `[trello] ignore_lists must be a list of strings` — a `ConfigError` at load, matching the section's existing rule that a typo in `[trello]` is an error rather than a warning |
| Entries must be non-empty after nothing is stripped | `[trello] ignore_lists contains an empty column name` |
| Duplicates | Accepted, collapsed with `dict.fromkeys` — the same treatment `[notifications] events` already gives (FR-019a) |
| Unknown key | `ignore_lists` joins `_SECTION_KEYS["trello"]`, so writing it is not itself an "unknown key" error |
| Literal credential | The `[trello]` sweep, **extended** to look inside lists. It tested `isinstance(value, str)` and stopped, so this section's first list-valued key arrived as a hole in the choke point rather than a key it covered — caught in review, not by design |

Existence on the board is **not** checked here — the config loader does not make network calls.
That check is `check_board`'s, below.

## Persistence

### `cards.current_list_id` and `cards.current_list_name` — new, migration 006

```sql
ALTER TABLE cards ADD COLUMN current_list_id   TEXT;
ALTER TABLE cards ADD COLUMN current_list_name TEXT;
```

**Two columns, not the one this document originally specified.** The gap surfaced during
implementation and is recorded rather than smoothed over: the plan said "store the id", and the id
alone cannot answer the question the listing asks. The intake gate runs inside the poll, where the
board's `lists_by_id` map is in hand, and wants an **id** — an equality check that is duplicate-safe
and survives a rename mid-run. `robot-army cards` and the web listing run where the board is not
available at all, by design, and can only compare against the **names** in `[trello] ignore_lists`.
Neither representation serves both callers, and deriving one from the other needs exactly the board
mapping the listing does not have.

Both values are written by the same statement from the same poll, so they cannot disagree.

Where the card is **now**, as the last poll saw it. Nullable, and NULL means *tracked before this
migration and not yet re-polled* — not "in no column", which is impossible. Nothing backfills it:
the next poll writes it, and until then the card is treated as not parked, which is milestone 003's
behaviour and therefore the safe direction for a value we do not have.

The three list columns that already exist answer different questions, and the distinction is the
whole reason a fourth is needed rather than one of them being reused:

| Column | Question it answers | Written when |
|---|---|---|
| `origin_list_id` | Where was the card before we ever touched it? | first sighting only — what FR-029 returns an abandoned card to |
| `placed_list_id` | Where did *we* last put it? | after a successful move — what FR-030 compares against to detect a human move |
| `pending_move_to` | Where are we in the middle of putting it? | before a move is attempted, so our interrupted move is distinguishable from a human's |
| **`current_list_id`** | **Where is it now?** | **every poll, from the board's `idList`** |
| **`current_list_name`** | **What is that column called?** | **alongside the id, resolved through `lists_by_id`** |

**Written by** `intake._refresh_tracked_card`, inside the transaction it already opens for
title/body changes, and seeded by `db.insert_card` at first sighting so a newly tracked card is never
briefly unanswerable about where it is. **Read by** the parked derivation, the `robot-army cards`
listing, the web cards page, and the park/release records.

**Interruption**: a kill before the write leaves the previous value; the next poll overwrites it from
the board. Because parked is derived from this column rather than stored beside it, a stale value
produces one cycle of stale display and then self-corrects — there is no repair path to write,
because there is no second copy to disagree with it.

**Schema version**: `PRAGMA user_version` 5 → 6. Forward-only, appended to `MIGRATIONS`, never
editing an existing entry.

## Value types

### `BoardInfo.lists_by_id` — new field

```
lists_by_id: dict[str, str] = {}      # list id -> list name
```

The inverse of the existing `lists` (`name → id`), built from the same already-fetched
`GET /boards/{id}/lists` response. No additional request.

It exists because `lists` cannot answer FR-019b. Two board columns named "Icebox" collapse into one
`lists` entry, so one of them would silently stay intake. List ids are unique, so the inverted
mapping keeps both — the requirement is satisfied by the shape of the data rather than by a rule
somebody has to remember.

`lists` is unchanged and keeps its callers: the existence checks are name-membership questions, and
`name in info.lists` answers them correctly whether or not a name is duplicated.

### `BoardStatus.ignored_list_ids` — new field

```
ignored_list_ids: frozenset[str] = frozenset()
```

Resolved once per process by `check_board`, next to the `label_id`, `in_progress_list_id` and
`done_list_id` it already resolves:

```
ignored_list_ids = frozenset(
    list_id for list_id, name in info.lists_by_id.items() if name in trello.ignore_lists
)
```

Ids rather than names, so the per-card comparison is an equality check — R11's reasoning for the tag
filter, applied unchanged: it is cheap, and it survives a column being renamed mid-run rather than
half-matching.

Empty when nothing is configured, which makes the predicate below constantly false.

### `PollOutcome.ignored` — new field

```
ignored: int = 0
```

Tagged cards this cycle that were skipped for sitting in an ignored column. Distinct from `found`
(all tagged cards) and from `created` (newly tracked rows), because FR-021 requires the poll record
to distinguish them and because "nothing is intake because you excluded everything" and "nothing is
tagged" are different facts.

## The derived condition

**Parked** is computed, never stored:

```
# in the poll, where the board's id map is in hand:
ignored(card)  ==  card.list_id in status.ignored_list_ids

# in the listings, where the board is not available:
parked(card)   ==  card.state not in cardstates.NEVER_PARKED
                   and card.current_list_name in config.trello.ignore_lists
```

`NEVER_PARKED` — `{LINKED, CREATING, DROPPED}` — lives in `cardstates.py` because it has two
consumers that cannot otherwise share it: `intake` decides whether to **record** a park, `operations`
decides whether to **show** one, and `operations` imports `intake`. They were two separate literals
until they disagreed — the record side tested only `linked`, so a `creating` card moved into an
excluded column wrote a park record nothing would ever show, and then suppressed its own release once
it resumed to `linked`. One set now, asserted identical by a test.

No `CardState.PARKED`, no transition table entry, no boolean column. The reasons, in order of how
badly each alternative fails:

- **A state cannot retain a state.** FR-008 requires a parked card to keep the state and reason it
  had. `needs_info → parked → needs_info` would have to carry `reason` across two hops to
  reconstruct what the derived form never lost.
- **The common case is both at once.** A card that is awaiting clarification *and* parked is exactly
  what the author produces when they write an ambiguous card and park it because they are not ready
  to disambiguate. One enumeration cannot hold two independent conditions.
- **A cached boolean goes stale.** FR-011 requires removing a column from the configuration to take
  effect on the next poll with no other action. A stored flag would need a sweep to recompute it;
  a derivation cannot be wrong.

`cardstates.py` is therefore untouched — `CARD_TRANSITIONS` gains no entries, and its property that
every illegal transition can be enumerated in a test survives.

### Why `CREATING` is excluded from the derivation

A card whose issue creation is under way is never parked, even sitting in an ignored column. The
intent is recorded, R6's recovery must still run against it, and the spec's edge case is explicit:
being parked mid-creation does not cancel the creation. Excluding it here means the gate in
`evaluate_card` sits *after* the `creating` branch, so `_resume_creation` still runs — see
[contracts/surfaces.md](contracts/surfaces.md).

### Why `LINKED` is excluded

FR-013 and FR-015. A card with a recorded issue is past intake, so the ignore list does not apply to
it in either direction: its mapping survives, its session is untouched, and its lifecycle moves still
happen — including moves *into* an ignored column, which is what makes listing `in_progress_list` or
`done_list` as ignored a harmless no-op rather than a contradiction the loader has to reject.

## State transitions

**No card state transitions are added or changed.** The two transitions this milestone introduces are
of the `current_list_id` column across the ignored set, and they are events rather than states:

| Transition | Condition | Record |
|---|---|---|
| **park** | previous `current_list_id` not in `ignored_list_ids`, new one is, card not linked | `trello.parked` — card, column, previous column |
| **release** | previous `current_list_id` in `ignored_list_ids`, new one is not | `trello.released` — card, column it left |

Both are detected in `_refresh_tracked_card`, where the old and new values are both in hand, and both
records are written in the same transaction as the column update — the discipline `transition_card`
already follows, so a crash cannot produce one without the other.

A card that is untracked and stays untracked has no transition and no record; it is counted in
`PollOutcome.ignored` and nothing else. That is the enumerated Principle III exception, argued in
[research.md](research.md) R6.

## Entity relationships

Unchanged. No table is added, no foreign key is added, and no index changes — the ignored set is
configuration, not data, and it relates to cards only through a column value compared at read time.
