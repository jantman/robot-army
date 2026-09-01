# Quickstart: proving the refusal

**Feature**: `specs/20260901-164616-guard-worktree-remove` | **Date**: 2026-09-01

## Read this first

Unlike the previous guard feature, **this one is safe to stage by hand**. Reproducing it needs a
session row, not a signal: nothing here launches, signals or ends a process, and the failure being
prevented is a deletion that git performs. The one thing to avoid is running the `--force`
scenarios against a session you actually care about — the override does exactly what it says.

Every scenario is available from the suite; Scenario 5 is the by-hand version for anyone who wants
to see the message on a terminal.

## Prerequisites

```bash
cd /home/jantman/GIT/robot-army
uv sync            # or: pip install -e '.[dev]'
```

## Scenario 1 — The reported case: a terminal item with a running session (P1, SC-001, SC-002)

The load-bearing test. The item is `done`, which is what made the report reachable through ordinary
operation, and which is why the guard must not look at item state at all (research R1).

```bash
uv run pytest tests/unit/test_worktree_remove_guard.py -v -k reported
```

**Expected**: exit code `3` (`EXIT_PRECONDITION`); the audit log contains **no `git.remove_worktree`
record and no `git.delete_branch` record**; `work_items.worktree_path` and `branch` are unchanged.
The assertion is on the absent records, not merely on a surviving directory — proving the refusal
branch is reachable is not the same as proving the removal is unreachable.

## Scenario 2 — Every shape of "open" refuses (P1, W1–W3, FR-002, FR-003)

```bash
uv run pytest tests/unit/test_worktree_remove_guard.py -v -k refuses
```

**Expected**, one case each:

- session `running`, item `done` → refuses.
- session `starting`, item `done` → refuses. A session that has not reported itself running yet is
  not a session that is safely absent.
- session `running`, item `active` → refuses.
- **two attempts, the *earlier* one still open, the latest closed** → refuses. This is the case
  `db.latest_session_for_item` would miss and the reason the guard reads every row (R1).
- session `exited` / `lost` / `failed` → **does not** refuse; removal proceeds exactly as today.

## Scenario 3 — The message tells the operator what to do next (P1, SC-003, W8–W11)

```bash
uv run pytest tests/unit/test_worktree_remove_guard.py -v -k message
```

**Expected**: the rendered lines name the session id, its attempt, its state, and exactly one of the
four liveness answers in [contracts/worktree-removal.md](./contracts/worktree-removal.md) W9. With a
`host_socket` recorded, the `dtach -a <socket>` line appears verbatim; without one, no reattach line
is printed and nothing is invented in its place.

The `unidentified` case (a pid with no recorded `proc_start`) is the one that matters: it must not
render as `running`. `procinfo.is_alive` is never called for it — its documented degradation would
report any process holding that number as this session (R6).

## Scenario 4 — The override, and its honesty (P2, W13–W16)

```bash
uv run pytest tests/unit/test_worktree_remove_guard.py -v -k force
```

**Expected**:

- Without `--force`, the `confirm` callable is **never invoked**. The refusal is not a question.
- With `--force` and a live session, the prompt string names the session before any input is read.
- With `--force` and a live session, answering anything but the item id aborts with nothing removed.
- With `--force` and a live session, answering the item id removes the worktree and the branch.
- With `--force` and **no** live session, the prompt is byte-for-byte today's prompt.

## Scenario 5 — By hand, against a real worktree (integration, SC-001)

```bash
uv run pytest tests/integration/test_worktree_removal.py -v -k session
```

Or, on a scratch installation, with a work item that has a prepared worktree and no live session —
note the state database location from `robot-army doctor`:

```bash
DB=~/.local/state/robot-army/state.db
sqlite3 "$DB" "INSERT INTO sessions (work_item_id, session_id, attempt, state, dry_run, started_at, pid, host_socket)
               VALUES (<id>, 'staged-1', 99, 'running', 0, datetime('now'), 999999, '/tmp/staged.sock');"

uv run robot-army worktree remove <id>;  echo "exit: $?"
```

**Expected**: exit `3`; the message names session `staged-1` and reports `pid 999999 recorded,
with no start time to identify it by` — the row carries no `proc_start`, so the honest answer is
that the pid cannot be identified, **not** that it is alive or dead (W9, W10). The directory is
still present, and `git branch` in the clone still lists the branch. Then:

```bash
sqlite3 "$DB" "UPDATE sessions SET state = 'exited' WHERE session_id = 'staged-1';"
uv run robot-army worktree remove <id>;  echo "exit: $?"
```

**Expected**: removal proceeds as it always has.

## Scenario 6 — The record (P3, SC-004, W17–W21)

```bash
uv run pytest tests/unit/test_worktree_remove_guard.py -v -k record
uv run robot-army log --item <id>                 # on a real installation
```

**Expected**: an `intent`/`outcome` pair per attempt, carrying `entity_id` = the work item.
A refusal's outcome is `ok` with `refused: true`, `refused_by: "live_session"`, the reason, and the
`live_session` object. A forced override carries `forced_over_live_session: true` — which is what
distinguishes it from a forced removal of a merely dirty tree, since `force: true` does not (W20).

## Scenario 7 — The automatic path is untouched (P1, FR-013, W5, W22)

```bash
uv run pytest tests/unit/test_cleanup.py -v
```

**Expected**: unchanged. In particular a live session still records `skipped` — not `retained`, not
ineligible — which is what makes the item reconsidered on a later pass. The extraction in R2 must
not change `eligible`'s reason string, because `clean_item` routes on a substring of it (R3).

## Full suite

```bash
uv run pytest
```

The suite must pass before the feature is complete (Development Workflow).
