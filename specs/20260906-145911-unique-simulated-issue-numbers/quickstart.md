# Quickstart: proving simulated numbers no longer collide

**Feature**: [spec.md](spec.md) | **Contract**: [contracts/simulated-issue-number.md](contracts/simulated-issue-number.md)

Two ways to check this: the suite, which is the gate, and a rehearsal against a real board, which
is how the bug was found in the first place.

## Prerequisites

```bash
uv sync
```

## 1. The suite

```bash
uv run pytest
```

The whole suite must pass. The cases that speak to this feature:

| What it proves | Where |
|---|---|
| A repository already holding simulated rows files the next card in **one** attempt | `tests/integration/test_card_to_issue.py` |
| A fresh process does not reissue a number an earlier process used | `tests/unit/test_simulated_writers.py` |
| Two cards in one pass receive different numbers | `tests/unit/test_simulated_writers.py` |
| Simulated comments before a card do not move its number | `tests/unit/test_simulated_writers.py` |
| The allocation is empty-safe, gap-safe and per repository | `tests/unit/test_db.py` |
| A refused mapping still degrades to a retry rather than aborting the pass | `tests/integration/test_card_interruption.py` |
| The refusal message says what the next pass does | `tests/unit/test_card_invariant.py` |

A useful check that the P1 test would have failed before the fix: seed more simulated rows than
`CREATE_ANOMALY_THRESHOLD` and assert `create_failures` is `0` after the pass. Under the old
behaviour that card raises an anomaly on its way to being filed.

## 2. A rehearsal against a board

This is the reproduction from issue #22, run forwards.

```bash
uv run robot-army run --once --effect-level plan
uv run robot-army cards --include-simulated
```

Expected, for a repository that already holds simulated cards:

- every new card reaches `linked` on this single pass;
- no card shows a `reason` mentioning a refused mapping;
- `robot-army anomalies` lists no new `card_create_failing`.

Read the numbers out of the log to see the allocation:

```bash
jq -r 'select(.action == "github.issue.create" and .simulated == true)
       | [.ts, .entity_id, .detail.would_return.number] | @tsv' \
  ~/.local/state/robot-army/logs/audit-*.jsonl | tail -20
```

Each repository's numbers count upwards and none repeats, across restarts. Running
`robot-army run --once --effect-level plan` a second time and re-reading this shows the next
numbers continuing above the last, rather than starting again at 900001.

## 3. Resetting between rehearsals

Simulated rows are the input to the allocation, so clearing them starts the numbering over — which
is the intended way to get a clean rehearsal:

```bash
uv run robot-army purge-simulated
```

After that the next simulated card is 900001 again.
