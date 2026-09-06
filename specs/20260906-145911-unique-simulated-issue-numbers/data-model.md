# Data Model: Unique simulated issue numbers

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

No table is added, altered or migrated. This document says what the allocation reads, what it
writes, and which existing constraint it is written to satisfy.

## What the allocation reads

`cards`, one indexed column, filtered on two:

| Column | Role in the allocation |
|---|---|
| `repo_key` | Numbering is per repository, because `idx_cards_issue` is. Two repositories rehearsed on the same board number independently. |
| `dry_run` | Always `1` for rows the simulated writer produces (`REAL_AT["issue_writer"] == {LIVE}`, and card rows carry `dry_run=effect_level.is_simulated`). Filtering on it keeps a simulated number out of the live number space and vice versa. |
| `issue_number` | The column the maximum is taken over. `NULL` for every card that has no issue yet — every `needs_info` card, and every `creating` card before its issue exists — and `MAX` ignores `NULL`, so those rows neither raise the allocation nor need excluding. |

Rows in any state count, including `archived` ones. An archived card still holds its number in
`idx_cards_issue`, so a number it holds is still taken.

## What it produces

```
allocated = max(SIMULATED_ISSUE_BASE, MAX(issue_number) or 0) + 1
```

- **A repository with no simulated cards**: `MAX` is `NULL`, so the result is
  `SIMULATED_ISSUE_BASE + 1` — 900001, exactly what the first card has always received.
- **A repository holding 900001–900008**: the result is 900009. One attempt, not eight.
- **A gapped sequence** (900001, 900004): the result is 900005. Gaps are not filled. Filling them
  would mean the number a card receives depends on which earlier card was deleted, and reusing a
  number that once meant something else makes a log harder to read, not easier.
- **A repository whose live rows carry ordinary numbers** (12, 47): unaffected, because those rows
  are `dry_run = 0` and outside the filter. The floor also means a live number could never be
  returned even if one were somehow in scope.

## The constraint this satisfies

```sql
CREATE UNIQUE INDEX idx_cards_issue ON cards (repo_key, issue_number, dry_run)
```

The allocation reads exactly the three columns the index is built on and returns a value no row
holds. That is the whole design: the number is derived from the same record the constraint is
checked against, so a refusal means something has changed underneath — not that the allocator was
guessing.

## Entity notes

- **Card mapping** — unchanged in shape. `issue_number`, `issue_url`, `repo_key` and
  `create_failures` are written exactly as they are today; only the value chosen for
  `issue_number` on a simulated run changes.
- **Simulated issue number** — still `> SIMULATED_ISSUE_BASE` (900,000), so everything that
  recognises a simulated number by that offset keeps working, in the code, in the tests and in the
  log.

## State transitions

None change. The four-step creation sequence — intent committed, issue minted, mapping written,
card commented — keeps its transaction boundaries. The allocation happens inside step 2, which is
the step that already had no transaction of its own because it was the network call.
