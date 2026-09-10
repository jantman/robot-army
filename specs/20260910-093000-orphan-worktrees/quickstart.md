# Quickstart: validating the orphan-worktree fix

All scenarios are covered by unit tests under `tests/unit/` (`uv run pytest`); these are the
same checks written as a human would run them on a machine with an onboarded repository.

## 1. Purge takes its worktrees with it (US1)

```bash
uv run robot-army run --effect-level local --once   # dispatch leaves a real worktree, dry_run row
uv run robot-army worktree list --include-simulated # note the path, marked *
uv run robot-army purge-simulated                   # lists the path; answer y, then y
ls ~/worktrees/<repo>/                              # the directory is gone
git -C <clone> branch --list 'robot-army/*'         # its branch is gone
uv run robot-army log --since 10m --include-simulated | grep worktree.remove
```

Repeat answering `y` then `n`: the rows go, the directory stays, and the output ends with the
exact `robot-army worktree remove <path>` line.

## 2. The report follows the disk (edge case)

Leave a worktree from a `local` round, then run `purge-simulated --yes --remove-worktrees`
under `--effect-level plan`. Expected: the path is reported as left on disk
(`directory_survived`), not as removed.

## 3. Orphans are reported and retract (US2)

```bash
mkdir -p ~/worktrees/<repo>/issue-9999
uv run robot-army reconcile        # orphan_worktrees 1
uv run robot-army anomalies        # orphan_worktree for that path
rmdir ~/worktrees/<repo>/issue-9999
uv run robot-army reconcile        # anomalies_resolved 1
mkdir -p ~/worktrees/<repo>/my-own-checkout ~/worktrees/unrelated/issue-1
uv run robot-army reconcile        # orphan_worktrees 0 — neither is robot-army's shape
```

## 4. Removal by path (US3)

```bash
uv run robot-army worktree list                          # orphan shown, condition unclaimed
uv run robot-army worktree remove ~/worktrees/<repo>/issue-29
uv run robot-army worktree remove ~/worktrees/<repo>/issue-59   # claimed → names the item id
uv run robot-army worktree remove /tmp                          # outside the root → refused
```
