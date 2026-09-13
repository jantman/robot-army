# Contract: Simulated Wording of the Destructive Verbs

`NOTE` below is the line

```
  (simulated: effect level is `<level>`; version control is real from `<real_from>`, so nothing on disk was touched)
```

with `<level>` the context's effect level and `<real_from>` from `effects.real_from("version_control")`.

## `worktree remove` (id, path, and through `purge-simulated`)

| Case | Boundary | Output | Exit | Data / record |
|---|---|---|---|---|
| W1 | real, removed, branch deleted | `removed worktree P` / `deleted branch B` | 0 | `simulated: false` |
| W2 | simulated, no directory, branch on record | `would remove worktree P` / `would delete branch B` / `NOTE` | 0 | `simulated: true`, `worktree_removed: true`, `branch_deleted: true` |
| W3 | simulated, no directory, no branch | `would remove worktree P` / `NOTE` | 0 | `simulated: true` |
| W4 | simulated, directory on disk | unchanged `directory_survived` refusal | 1 | `simulated: true`, `refused_by: directory_survived` |

The row's `worktree_path` is cleared in W1–W3 as today.

## `cleanup`

| Case | Boundary | Recorded state | Reason |
|---|---|---|---|
| W5 | simulated, no directory, contained branch | `done` | `worktree removal simulated; branch deletion simulated — <evidence>` |
| W6 | simulated, no directory, no branch on record | `done` | `worktree removal simulated; no branch on record` |
| W7 | any, reported removed, directory still on disk | `retained` | `SURVIVED_REASON`; branch not attempted |
| W8 | real | unchanged | unchanged (`worktree removed; branch removed — …`) |

Summary when any decision is simulated:
`N of M considered item(s) would have their worktree removed` / `NOTE`. Otherwise unchanged.

## `worktree prune`

| Case | Boundary | Output |
|---|---|---|
| W9 | simulated | `<repo>: not checked — pruning is simulated` per repository, then `NOTE` |
| — | real | unchanged (`<repo>: <git output or "nothing to prune">`) |

## The cross-verb rule

For `cancel`, `worktree remove`, `worktree prune` and `cleanup` under simulated boundaries: the
output contains "simulated", and every line containing any of `removed worktree`, `deleted branch`,
`worktree removed`, `branch removed`, `nothing to prune`, `had their worktree removed`,
`stopped session` also contains "simulated" or "would".
