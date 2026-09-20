# Data Model: the waiting count

**Feature**: [spec.md](spec.md) | **Date**: 2026-09-20

**No schema change.** No table, column, index or migration. The feature reads the `work_items`
table through a function that already exists and renders the answer.

## The counted set

| Work item state | Counted? | Why |
|---|---|---|
| `discovered` | no | the machine's to move — intake decides next |
| `ready` | no | the machine's to move — dispatch decides next, and `/queue` says why it has not |
| `dispatching` | no | the machine's to move, right now |
| `active` | no | a session is running; `/active` says so |
| `awaiting_review` | **yes** | session exited cleanly; resume, restart and abandon are the operator's |
| `interrupted` | **yes** | session did not finish; the same three decisions are the operator's |
| `failed` | **yes** | `retry` and `reset` are routes out, and only the operator takes them |
| `done` | no | terminal |
| `abandoned` | no | terminal |

The rule the three share: **the work is parked and the machine will not move it without a
decision.** That is the pill's whole meaning, and it is why one number is right where three
would be three answers to one question.

`done` and `abandoned` are excluded not because they are uninteresting but because nothing is
waiting: there is no decision left to take.

## Derived value

**`waiting_count`** — a non-negative integer on the chrome payload.

- **Source**: `db.count_work_items_by_state(conn, include_simulated=...)`, summed over the
  three states above.
- **Scoping**: by the request's `include_simulated`, through the same `_scope` clause every
  listing uses. A simulated item counts when simulated rows are being shown and not when they
  are being hidden (FR-002).
- **Absence is meaningful**: the key is *omitted* from the chrome assembled by
  `server._bare`, which has no database. Absent means "not counted", which is not zero
  (R2). Present and zero means "counted, and there is nothing".
- **Written to**: the HTML chrome bar as one pill, and the JSON body of every view under the
  same name (FR-009).

## State transitions

None introduced. `WORK_ITEM_TRANSITIONS` is untouched; no item changes state because of
anything here.

Transitions that change the count are the existing ones, and they change it the way the
arithmetic implies:

| Transition | Effect on the count |
|---|---|
| `active → awaiting_review` | +1 — the reported case: the item leaves `/active` and the bar notices |
| `active → interrupted` | +1 |
| `active → failed`, `dispatching → failed`, `discovered → failed` | +1 |
| `interrupted → ready` (resume/restart/reset), `failed → ready` (retry/reset) | −1 |
| `awaiting_review → ready` (reset), `→ done`, `→ abandoned` | −1 |

## The page's own payload

`interrupted_view`'s `View.data` gains one section and two keys change shape:

| Key | Before | After |
|---|---|---|
| `items` | interrupted rows | unchanged |
| `awaiting_review` | awaiting-review rows | unchanged |
| `failed` | — | **new**: failed rows, same row shape |
| `counts` | `{interrupted, awaiting_review}` | gains `failed` |
| `withheld_simulated` | interrupted + awaiting | gains the failed section's withheld count |

The row shape is `_signal_row`'s, identical for all three sections: a failed item's checkout
may be gone, and the existing signal rendering already distinguishes "missing" from "could not
read" from "unknown", so it needs nothing new.

## Audit records

**None.** Nothing outside the process changes. See plan.md's Constitution Check, Principle III.
