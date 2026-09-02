# Contract: configuration

## `[dispatch]`

```toml
[dispatch]
order = "oldest-first"          # unchanged
default_repo_max_sessions = 1   # unchanged — the issue's first item, already shipped
wait_for_merge = false          # NEW; the global default
```

- Type: boolean. Anything else is a load-time problem naming the key and the value seen.
- Default: `false`. An installation that says nothing behaves exactly as it does today.
- Unknown keys in this section are already an **error**, not a warning; `wait_for_merge`
  joins the allowed set and a misspelling of it is refused with the key named.

## `[repos."owner/name"]`

```toml
[repos."jantman/example"]
max_sessions = 1          # unchanged
priority = 10             # unchanged
wait_for_merge = true     # NEW; overrides [dispatch] wait_for_merge for this repository
```

- Type: boolean. Absent means *inherit*, which is not the same as `false` and is reported
  differently.
- Unknown keys in a `[repos.*]` section are already an error; `wait_for_merge` joins
  `_REPO_KEYS`.

## Resolution

```
Config.effective_wait_for_merge(repo_key) -> (value: bool, explicit: bool)
```

| `[repos.x] wait_for_merge` | `[dispatch] wait_for_merge` | `value` | `explicit` |
|---|---|---|---|
| absent | absent | `False` | `False` |
| absent | `true` | `True` | `False` |
| absent | `false` | `False` | `False` |
| `true` | anything | `True` | `True` |
| `false` | `true` | `False` | `True` |

`explicit` exists so `robot-army capacity` can say whether the author chose the value or
inherited it, which is the difference between a setting they can find and one they would have
to discover. Same contract as `effective_repo_cap`'s second element.

Unlike `effective_repo_cap`, there is **no clamping**: `[dispatch] wait_for_merge` is the
global setting itself, not a machine-wide ceiling that a per-repository value could exceed.

## Reporting: `robot-army capacity`

The per-repository block widens from *repositories with a live session* to *every onboarded
repository*, because a repository with the gate in force and no live session is precisely the
one the author is asking about. Each line reports the sessions running, the effective cap,
whether that cap was chosen, and the effective wait-for-merge value with its source:

```
per repository:
  jantman/example                0 of 1 sessions (configured)   wait-for-merge: on (configured)
  jantman/other                  1 of 1 sessions (default)      wait-for-merge: off (default)
```

The JSON `data` payload gains, per repository, the effective cap, whether it was explicit, the
effective wait-for-merge value, and whether *it* was explicit. Existing keys keep their
meaning and their names; `per_repo` continues to be the live-session count so nothing reading
it today changes meaning.
