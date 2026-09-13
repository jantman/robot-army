# Quickstart: A Simulated Removal Says It Was Simulated

## Automated

```bash
uv run pytest tests/unit/test_simulated_wording.py tests/unit/test_cleanup.py tests/unit/test_effects.py
uv run pytest            # the whole suite
```

The first file covers contract cases W1–W9 and the cross-verb rule.

## By hand, at `plan`

With `[daemon] effect_level = "plan"` and a simulated item carrying a worktree path that is not on
disk:

```bash
uv run robot-army worktree remove <id>
# would remove worktree …
# would delete branch …
#   (simulated: effect level is `plan`; version control is real from `local`, so nothing on disk was touched)

uv run robot-army worktree prune
# demo: not checked — pruning is simulated
#   (simulated: …)

uv run robot-army log --action worktree.remove   # outcome carries "simulated": true
```

At `local` or above the same commands print exactly what they printed before this change.
