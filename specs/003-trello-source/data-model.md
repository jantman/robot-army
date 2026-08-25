# Phase 1 Data Model: Trello Source

One new table, one new state machine, and no change to `work_items`. Migration **003**.

---

## The invariant is two unique indexes

§11 of the planning document asks for "one work item ⇒ at most one GitHub issue ⇒ at most one Trello
card", with the mapping table as source of truth. Both halves are enforced by the schema rather than
by code that has to remember:

| Half of the invariant | What enforces it |
|---|---|
| At most one issue per card | `idx_cards_identity` — one row per `(board_id, card_id, dry_run)`, and a row holds one `issue_number` |
| At most one card per issue | `idx_cards_issue` — unique on `(repo_key, issue_number, dry_run)` where `issue_number IS NOT NULL` |

A create path that skipped its mapping check does not produce a duplicate; it produces an
`IntegrityError`, which is loud. That is the difference between an invariant and a convention.

---

## `cards`

```sql
CREATE TABLE cards (
    id                INTEGER PRIMARY KEY,
    board_id          TEXT    NOT NULL,
    card_id           TEXT    NOT NULL,
    card_url          TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    body              TEXT    NOT NULL,
    state             TEXT    NOT NULL,
    dry_run           INTEGER NOT NULL,
    repo_key          TEXT,
    issue_number      INTEGER,
    issue_url         TEXT,
    reason            TEXT,
    commented_reason  TEXT,
    last_activity     TEXT,
    origin_list_id    TEXT,
    placed_list_id    TEXT,
    pending_move_to   TEXT,
    comment_posted_at TEXT,
    intent_at         TEXT,
    create_failures   INTEGER NOT NULL DEFAULT 0,
    archived_at       TEXT,
    first_seen_at     TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE UNIQUE INDEX idx_cards_identity ON cards (board_id, card_id, dry_run);
CREATE UNIQUE INDEX idx_cards_issue    ON cards (repo_key, issue_number, dry_run)
    WHERE issue_number IS NOT NULL;
CREATE INDEX        idx_cards_state    ON cards (state);
```

**`dry_run` is part of both identity keys**, exactly as it is for `work_items`, and for the same
reason: a simulated run and a later live run of the same card must be able to coexist, because that
is the normal workflow. It is also what makes FR-041 true — a simulated row cannot occupy the live
row's slot and so cannot suppress the real creation.

**`repo_key` is deliberately not a foreign key into `repos`.** A card may name a repository that is
configured but not onboarded, and the spec's edge cases say such a card still gets an issue —
creating an issue is not dispatching, and the onboarding block belongs at dispatch where the author
can act on it. A card may also name a repository that is configured and later removed. An FK here
would either forbid the row or delete the mapping, and deleting a mapping is how a duplicate issue
gets created.

**`origin_list_id` versus `placed_list_id` versus `pending_move_to`.** The first is where the card was
before we ever touched it, and is what FR-029 returns it to. The second is where we last put it, and
is what FR-030 compares against to detect a move by the author. The third is written *before* a move
is attempted, so that an interrupted move is distinguishable from a human one (R12).

**`reason` versus `commented_reason`.** `reason` is the current explanation, updated on every
evaluation. `commented_reason` is the last explanation actually written onto the card, and the
comparison between the two is FR-022's whole implementation: comment when they differ, stay silent
when they do not.

**`last_activity`** is the rescan trigger baseline, and R9 is the reason it is refreshed after our own
writes rather than only after the author's.

---

## Card lifecycle

| State | Meaning |
|---|---|
| `discovered` | Row exists, evaluation not yet settled. Observable only after an interrupted evaluation |
| `needs_info` | Tagged, but no single known repository could be identified. `reason` says which |
| `creating` | Intent to create an issue is recorded; the issue may or may not exist yet (R6) |
| `linked` | The issue exists and the mapping is recorded. Terminal for the mapping |
| `dropped` | The card lost its tag, was archived, or was deleted **before** it was linked |

| From | To | When |
|---|---|---|
| `discovered` | `needs_info` | Evaluation found zero or more than one known repository |
| `discovered` | `creating` | Evaluation found exactly one |
| `discovered` | `dropped` | Tag removed, archived, or deleted before evaluation settled |
| `needs_info` | `creating` | Re-evaluation resolved it |
| `needs_info` | `dropped` | Tag removed, archived, or deleted |
| `creating` | `linked` | The issue was created, or an existing one was adopted by recovery |
| `linked` | — | Terminal |

`creating` has **no** exit to `needs_info` or `dropped`, and that is deliberate in both directions. A
failed creation stays in `creating` with `reason` and an incremented `create_failures`, because the
intent stands and R6's recovery must still run against it; retreating to `needs_info` would discard
the intent timestamp that recovery depends on. And a card archived while in `creating` cannot be
dropped, because an issue may already exist for it.

`linked` is terminal even when the card is later archived or untagged: `archived_at` is recorded and
the mapping is kept. Dropping the mapping would let a re-tagged card create a second issue, which is
the exact failure §11 exists to prevent.

Transitions go through a single gate, as `states.transition_work_item` already does for the other two
machines, so the illegal cases can be enumerated in one test rather than inferred from the code.

---

## What is **not** added

- **No column on `work_items`.** A work item's card is found by joining `cards` on `(repo_key,
  issue_number)` against the `repo#number` in `work_items.source_id`. The fact is already derivable;
  storing it again creates a second place for it to be wrong (R16).
- **No new work item state.** `needs_info` lives on the card (R5). `work_items` is untouched by this
  migration, and milestone 001's FR-030 list stands as written.
- **No new poll-state table.** Board poll bookkeeping goes in `poll_state` under
  `trello:board:<board_id>` (R13).

---

## Interruption behaviour

Principle IV asks what happens if each write is killed halfway. Every row below is exercised by a
test.

| Killed at | Observable state | Recovery |
|---|---|---|
| Before the intent row is committed | Nothing exists | Next poll evaluates the card from scratch |
| After intent, before the issue is created | Row in `creating`, no issue | Listing finds nothing; step 2 is retried (R6) |
| After the issue is created, before the mapping is written | Row in `creating`, issue orphaned | Listing since `intent_at` finds the issue by the card URL in its body; the row advances to `linked` |
| After the mapping, before the card comment | Row `linked`, `comment_posted_at` NULL | Next pass checks for an existing marker comment, then posts |
| Mid-migration 003 | `user_version` unadvanced | The whole migration re-runs on the next start |
| After a card move landed, before it was recorded | `pending_move_to` set, card already there | The match between `pending_move_to` and the card's actual list identifies it as our move, not the author's (R12) |
| Between a card write and its `last_activity` refresh | Baseline is older than the card | One redundant re-evaluation, which is idempotent and posts no comment because `commented_reason` is unchanged |
| With the database lost entirely | No rows at all | Each card's marker comment restores its mapping on the next poll (R7); the gap is the double failure recorded in R6 |

---

## Config additions

A `[trello]` section, absent by default — an unconfigured installation makes no board request at all
(FR-001). Full shape in [`contracts/config.md`](contracts/config.md).

## Audit records

New actions, all through the existing `audit.action` intent/outcome pair with `component` unchanged
from whichever process is acting:

`trello.poll`, `trello.evaluated`, `trello.needs_info`, `trello.issue.create`, `trello.card.comment`,
`trello.card.move`, `trello.card.move_refused`, `trello.recovered`, `trello.dropped`,
`trello.board.check`.

Every one of them names the card id and, where one exists, the repository and issue. The redaction
choke point in `audit.py` is unchanged; R3 keeps credentials out of the record by never putting them
in a URL rather than by adding a rule.
