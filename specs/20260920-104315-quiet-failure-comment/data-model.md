# Phase 1 — data model

**No schema change.** No table, column, index or migration. This feature changes which
existing columns are rendered where, and what one derived view-field means.

## Stored columns this feature reads

| Column | Table | Written by | Meaning |
|---|---|---|---|
| `failure_reason` | `work_items` | `_fail` (`dispatch.py:1546`) on every dispatch failure | what went wrong, as recorded at the moment it went wrong. History, never re-checked. |
| `blocked_reason` | `work_items` | `_fail`, only when the failure was a refusal (`blocked=True`) | the same sentence, kept separately because a refusal is re-checkable and a crash is not |
| `worktree_path` | `work_items` | `worktree.prepare`'s caller, in the same transaction as `branch` | the isolated checkout. **Empty when the item never got one** — which is every gate refusal, because the gate runs before preparation. |

Nothing in this feature writes any of them.

## Derived view-field, redefined

`worktree_missing`, computed in `pages._signal_row` and carried into both the HTML and the
JSON form of the needs-me view.

| | Before | After |
|---|---|---|
| Definition | `not worktree_present` | a checkout path is recorded **and** it is not present |
| For an item that never had a checkout | `true` — "the checkout is missing" | `false` — nothing was created, so nothing is missing |
| For an item whose recorded checkout is gone | `true` | `true`, unchanged |
| For an item whose checkout is present | `false` | `false`, unchanged |

The underlying signal `worktree_present` (`operations.local_resume_signals`) is **not**
changed. It answers "did git find a worktree there?" and `false` is the right answer when
there is no there. The view-field is where the two facts are combined, because the view is
what makes a claim about them.

Consumers: `pages.py:1231` (producer), `pages.py:1317` (the banner), the JSON payload, and
tests. There are no others.

## Which reason the card shows

`failure_reason or blocked_reason`, falling back to a stated absence — the same expression
`/queue`'s blocked table already uses (`pages.py:937`). Deliberately not a new rule: two
surfaces disagreeing about which column is *the* reason is the defect this feature is
already fixing one instance of.

Rendered only for items in `failed`. `interrupted` and `awaiting_review` do not fail, carry
no reason, and gain no element.
