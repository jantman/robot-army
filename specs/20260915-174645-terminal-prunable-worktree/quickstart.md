# Quickstart: A hand-deleted worktree on a finished item is reported

Automated coverage: `uv run pytest tests/unit/test_terminal_prunable_worktree.py
tests/unit/test_worktree_remove_guard.py tests/integration/test_worktree_removal.py`.

The scenarios below are for a disposable environment — a scratch config, clone and worktree root —
with `[cleanup] on_issue_close = false` (the default). Contract references are to
[contracts/manual-removal-record.md](./contracts/manual-removal-record.md).

## 1. Scenario 10, item d — the reported case

Take a `done` item with a worktree. Then:

```bash
rm -rf "$WORKTREE_ROOT/demo/issue-<n>"
robot-army reconcile        # prunable_worktrees 1
robot-army anomalies        # prunable_worktree for the item, naming worktree remove and cleanup
```

**Expected**: one anomaly (M1, M3, M4). Running `reconcile` again adds none.

## 2. Settle it

```bash
robot-army worktree remove <id>
robot-army show <id>        # cleanup: done — worktree directory was already gone; …
robot-army anomalies --acknowledge <anomaly-id>
robot-army reconcile        # prunable_worktrees 0
```

**Expected**: exit 0; git's record and the branch gone; the path still on the row (M5, M10). The
acknowledged anomaly does not come back.

Repeat with `robot-army worktree prune` run **before** `worktree remove`: the same outcome, because
git's "not a working tree" over an absent directory is not a refusal (M10).

## 3. A manual removal leaves the record cleanup would

On another `done` item with a clean worktree and nothing running:

```bash
robot-army worktree remove <id>
robot-army show <id>        # worktree still named; cleanup: done — worktree removed; branch deleted — by `robot-army worktree remove`
robot-army reconcile        # prunable_worktrees 0
robot-army worktree remove <id>   # refused: already removed (exit 3)
```

**Expected**: M5–M7, M2, M12.

## 4. Nothing reported for cleanup's own removals

Set `on_issue_close = true`, close an issue, let a pass reclaim it, set it back and run
`reconcile` repeatedly. **Expected**: no `prunable_worktree` for that item (M2).
