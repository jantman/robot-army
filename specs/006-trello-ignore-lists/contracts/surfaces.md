# Contract: The Gate, the Records, and the Surfaces

Where the exclusion happens, what it writes to the log, and what the author sees.

## The predicate

One definition, in `intake.py`:

```
_is_ignored(list_id: str | None, status: BoardStatus) -> bool
    return bool(list_id) and list_id in status.ignored_list_ids
```

A missing or empty `list_id` is **not** ignored. The safe direction for a value we do not have is
milestone 003's behaviour, and a card with no column is a shape Trello does not produce.

## The two guards

Two call sites, one predicate, two different questions.

### Guard 1 — `poll_board`: should this card become a row?

For each card returned by `poll()`, skip `db.insert_card` when `_is_ignored(card.list_id, status)`,
and count it.

This is what makes FR-006 true **structurally**: a card with no row cannot appear in
`robot-army cards` or the web listing, because both read rows. It also keeps the `cards` table
proportional to work rather than to the board.

**`outcome.cards` still carries every polled card, ignored ones included.** This is not an oversight
and a later edit must not "tidy" it: `_reconcile_board_contents` drops every tracked card absent from
that collection, and `dropped` is terminal — `CARD_TRANSITIONS` gives it no exit. Removing ignored
cards from `outcome.cards` would make parking a tracked card destroy it permanently and silently,
which is the exact failure User Story 2 exists to prevent.

### Guard 2 — `evaluate_card`: should this row be acted on?

A tracked card can enter an ignored column at any time. The gate returns a `Verdict(card_id,
"ignored", reason=<column name>)` without reading or writing the board.

**Its position is a contract, not a style choice.** Each neighbour is fixed by a different
requirement, and a reordering would silently break one of them:

| Order | Branch | Broken by moving the gate above it |
|---|---|---|
| 1 | `linked` → `already_linked` | FR-013 — a card with a recorded issue would stop being finished off; FR-015 would need a special case instead of being free |
| 2 | `dropped` → `dropped` | FR-012 — the ignore list would become a route back from a terminal state |
| 3 | `creating` → `_resume_creation` | the spec's mid-creation edge case — parking a card would cancel an issue creation whose intent is already recorded, and the one-issue invariant would become "one issue unless you dragged the card at the wrong moment" |
| **4** | **the ignored gate** | — |
| 5 | `_restore_from_marker` | nothing breaks, but an ignored card would cost a comments request per cycle. Deferring the restore cannot create a duplicate, because nothing downstream of the gate creates anything |
| 6 | the activity-baseline short circuit | — |
| 7 | `resolve_repository` | FR-005 — an ignored card would be recorded as awaiting clarification and commented on |

The tasks phase must produce one test per row of this table.

## Park and release

Detected in `_refresh_tracked_card`, where the stored `current_list_id` and the board's current one
are both in hand, and written **in the same transaction as the column update** — the discipline
`transition_card` already follows, so a crash cannot produce one without the other.

| Event | Condition | Record |
|---|---|---|
| park | old not ignored → new ignored, card not `linked` | `trello.parked` — `{"card_id", "list_id", "list_name", "from_list_id", "state"}` |
| release | old ignored → new not ignored | `trello.released` — `{"card_id", "list_id", "from_list_id", "state"}` |

One record per transition, never one per poll. A card that sits parked for a month produces one
`trello.parked` record, and the answer to "why did this card stop being evaluated?" is in the log
once, where it is findable.

No card **state** changes. Parked is derived — see [data-model.md](../data-model.md).

## The poll record

`trello.poll` gains one field:

```json
{"tagged": 140, "ignored": 100, "newly_tracked": 0}
```

`ignored` counts **every tagged card this cycle sitting in an ignored column** — counted once, in
`poll_board`, over the polled listing, whether or not the card is tracked. It is distinct from
`tagged` and `newly_tracked` because FR-021 requires the record to distinguish them, and because
"nothing is intake because you excluded everything" and "nothing is tagged" are different facts that
a single number would conflate.

**One counter, one place.** The `ignored` verdict `evaluate_card` returns for a tracked parked card
is deliberately **absent** from `_VERDICT_COUNTER`: counting it there as well would report a card
sitting in an ignored column twice in the same cycle, once as polled and once as evaluated, and the
poll record's whole job is to be reconstructible. The verdict exists to end the evaluation and to
name the reason in the listing, not to be tallied.

`PollOutcome.ignored` carries it to the caller alongside `found`, `created`, `held`, `dropped`.

**No per-card record for an ignored card.** This is the enumerated Principle III exception, argued in
[research.md](../research.md) R6 and stated in [plan.md](../plan.md): the read changes no state
outside the process, the record would say the same thing about the same card every five minutes
forever, and the aggregate plus the startup record's `ignored_lists` reconstructs the decision
completely.

## `robot-army cards`

A parked card is shown **as parked, and still as whatever else it is**. The two conditions are
independent and the listing must not collapse them — a card can be awaiting clarification *and*
parked, which is what the author produces by writing an ambiguous card and parking it because they
are not ready to disambiguate.

```
card      title                     state       repository        reason
9k2Lm...  Rework the poller         needs_info  —                 parked in 'Icebox' — awaiting clarification: no repository named
p4Xz9...  Bump the timeout          discovered  —                 parked in 'Blocked'
c7Qw1...  Fix the exit codes        needs_info  —                 no repository named
```

`_card_dict` gains `current_list_id`, `current_list_name`, `parked` (bool) and `parked_list`
(the column's name, or `null`), so the JSON view answers the same question as the table.

The derivation compares the stored **name** against `[trello] ignore_lists`, never an id against
a resolved set: the id map lives on the board, and this listing must not need it. That is why the
poll stores the column's name beside its id — see [data-model.md](../data-model.md).

**Cards that were never tracked do not appear**, because they have no row. That is FR-006, and it is
why `robot-army cards` is not the place to look for "what is being ignored" — `doctor` names the
columns, and the poll record counts the cards.

**No board request.** The listing reads `current_list_name` from the database and compares it against
the configuration, so it keeps working with the board unreachable. That constraint is what forced the
columns to exist at all, and what forced the *name* to be stored and not only the id.

## The web cards page

Same rule, same reason, and one specific trap to avoid: `web/pages.py` already renders the
`needs_info` state as **"held — the card does not say which repository"**, and `PollOutcome.held`
already counts that. The parked condition must render as **"parked"** and must never reuse "held",
or the page will say the same word for two unrelated things and the author will read one as the
other.

A parked card is excluded from the page's `needs_info` count of outstanding work (FR-006, FR-009): it
is not waiting on the author, it is where the author put it.

## `robot-army doctor`

No new code. The per-column `BoardCheck`s appear in the existing rendering, and the existing exit
code follows `BoardStatus.ok`. See [board-checks.md](board-checks.md) for the output.

## `robot-army rescan`

Unchanged in shape: `forced=True` still re-evaluates every card whose state invites it. It does
**not** override the ignored gate — a forced rescan of a parked card returns `ignored`. Rescan exists
to re-resolve a card the author has edited; it is not a way to act on a card the author has
deliberately excluded, and making it one would give the ignore list an exception nobody asked for.
