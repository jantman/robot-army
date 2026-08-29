# Contract: `robot-army status` output when simulated rows are withheld

**Feature**: [../spec.md](../spec.md) | **Date**: 2026-08-29

Amends the `robot-army status` section of
[milestone 001's CLI contract](../../001-minimum-daemon/contracts/cli.md). Options, exit codes,
and section order are unchanged; this document fixes what the command is permitted to *say*.

## The invariant

> A single invocation of `robot-army status` MUST NOT print two statements that cannot both be
> true.

Concretely: it must never assert that no work items exist, or that none match, in an output that
also displays work items. Every rule below is a consequence of this one, and it is the property
the tests assert directly rather than by proxy.

## Section order (unchanged)

```
effect level : …
health       : …
dispatch     : …
capacity     : …
order        : …
database     : …

queue (N eligible) — in dispatch order:
<table>

counts by state:
  <state>  <n>

<item listing table>

unacknowledged anomalies (N):
```

## 1. Queue table

Adds a simulated marker. Columns and ordering are otherwise unchanged.

- The `item` column gains a `*` suffix for any row whose work item is simulated: `2*`.
- When at least one queue row is simulated, a blank line and the footnote
  `* = simulated (dry-run) row` are printed beneath the table.
- When no queue row is simulated, no footnote is printed and the table is byte-identical to
  today's.
- The queue continues to include simulated rows regardless of `--include-simulated`. That flag
  governs the counts and the listing; it does not and must not govern the queue, which has to
  name the item the next dispatch would actually select.

## 2. Counts by state

Let `W_counts` be the number of simulated work items in the database, ignoring `--state` and
`--repo` — matching the scope of the counts query itself, which has never honoured them.

| Condition | Output |
|---|---|
| Visible counts exist, `W_counts == 0` | `counts by state:` and the rows, exactly as today |
| Visible counts exist, `W_counts > 0` | The same, then one further line: `  N simulated rows withheld — pass --include-simulated to show them` |
| No visible counts, `W_counts == 0` | `no work items yet` — unchanged |
| No visible counts, `W_counts > 0` | `no work items (N simulated rows withheld — pass --include-simulated to show them)` |

The `yet` is dropped in the last case deliberately: it implies a system that has not started
producing work, which is the wrong implication when rows exist and are being withheld.

## 3. Item listing

Let `W_items` be the number of simulated work items matching the invocation's `--state` and
`--repo` filters.

| Condition | Output |
|---|---|
| Items shown, `W_items == 0` | The table, plus the existing `* = simulated (dry-run) row` footnote if any shown row is simulated |
| Items shown, `W_items > 0` | The table, then a blank line, then `N simulated rows withheld — pass --include-simulated to show them` |
| No items, `W_items == 0` | `no matching work items` — unchanged |
| No items, `W_items > 0` | `no matching work items (N simulated rows withheld — pass --include-simulated to show them)` |

`W_items` is scoped to the filters in force. Running `--repo owner/other` while every simulated
row belongs to a different repository reports `0` and prints nothing, because
`--include-simulated` would reveal nothing there.

## 4. With `--include-simulated`

`W_counts` and `W_items` are both zero by construction. No withheld line, no parenthetical, no
zero-count, and no other new output appears anywhere. Simulated rows appear in the counts and the
listing, marked as they already are.

## 5. Machine-readable payload

One key is added to the `status` payload. Nothing is renamed and nothing is removed.

```json
{
  "effect_level": "plan",
  "health": { "…": "…" },
  "counts": {},
  "items": [],
  "anomalies": [],
  "include_simulated": false,
  "dispatch_paused": false,
  "capacity": { "…": "…" },
  "queue": [ { "position": 1, "item_id": 1, "dry_run": true, "…": "…" } ],
  "withheld_simulated": { "counts": 4, "items": 4 }
}
```

- `withheld_simulated.counts` — `W_counts`, the number withheld from the `counts` object.
- `withheld_simulated.items` — `W_items`, the number withheld from the `items` array.
- The key is **always present**, with both values `0` when nothing was withheld, so a consumer
  never has to distinguish "nothing withheld" from "field not reported".
- `queue[].dry_run` already exists and is unchanged; the machine-readable side of the queue's
  simulated marking needed nothing.

## 6. Exit code

Unchanged in every case. Withholding rows is not an error and does not affect the exit status.

## 7. Sibling listings (P3)

The same distinction applies to two other commands that withhold simulated rows by default.
Neither displays a contradicting section, so neither is required to fix the invariant above —
but both currently report absence where there is withholding.

| Command | Today, when everything was withheld | Required |
|---|---|---|
| `robot-army cards` | `no cards tracked yet` | `no cards visible (N simulated rows withheld — pass --include-simulated to show them)` |
| `robot-army worktree list` | `no worktrees recorded` | `no worktrees visible (N simulated rows withheld — pass --include-simulated to show them)` |

When nothing is withheld, both keep their existing messages exactly. When rows *are* shown and
others were withheld, both print the same standalone line beneath the table that §3 specifies.
