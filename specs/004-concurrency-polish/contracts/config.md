# Contract: Configuration

Every key this milestone adds, its default, and what happens when it is wrong. Validation follows
`config.parse`'s existing split (R17): resolvable contradictions **warn**, unresolvable ones are
**problems** that prevent startup.

## New sections

```toml
[dispatch]
order = "oldest-first"              # or "repo-priority"
default_repo_max_sessions = 1

[cleanup]
on_issue_close = false

[notifications]
events = []                         # dispatch | completion | failure | needs_info
max_per_cycle = 5
```

## New `[repos.*]` keys

```toml
[repos.example]
path = "~/GIT/example"
max_sessions = 2                    # optional; default [dispatch].default_repo_max_sessions
priority = 10                       # optional; default 0, higher runs first
```

Both join `_REPO_KEYS`, where an unknown key is already an **error** rather than a warning, for the
reason `config.py` states: a typo there silently disables a step.

## Defaults, and why they are these

| Key | Default | Reason |
|---|---|---|
| `dispatch.order` | `oldest-first` | The behaviour milestone 003 already has. FR-046 requires the previous behaviour to be recoverable by configuration; making it the default makes it the no-op |
| `dispatch.default_repo_max_sessions` | `1` | Planning §10: "probably 1, to avoid worktree and dev-server collisions". Every measured collision risk in §6 is per-clone |
| `repos.*.priority` | `0` | Equal priority means the mode degrades to oldest-first, which is the harmless reading |
| `cleanup.on_issue_close` | `false` | The Operating Constraints require irreversible actions to be unreachable by default |
| `notifications.events` | `[]` | Outward-facing; same rule |
| `notifications.max_per_cycle` | `5` | Enough that ordinary operation never hits it; small enough that a backlog cannot flood |
| `daemon.max_concurrent_sessions` | `2`, unchanged | §16 leaves the number open; only running the system answers it |

## Validation

| Condition | Outcome | Why |
|---|---|---|
| `order` is not a known mode | **Problem** | FR-014's named case. Falling back silently would run the author's work in an order they did not choose and did not know about |
| `priority` is not an integer | **Problem** | No defensible guess |
| `max_sessions` is not a positive integer | **Problem** | Zero would disable a repository silently; negative is meaningless |
| `notifications.events` contains an unknown kind | **Problem** | Silently ignoring it means an event the author asked for never arrives |
| Unknown key in `[dispatch]`, `[cleanup]`, or `[notifications]` | **Problem** | Same rule as `[repos.*]` and `[trello]`: a typo in a section that exists is a setting that quietly does nothing |
| `repos.*.max_sessions` > `daemon.max_concurrent_sessions` | **Warning**; effective cap is the lower | Resolvable and harmless — usually a leftover from lowering the global cap. Mirrors the existing `dispatching_max_age_seconds` cross-check, which warns for the same reason |
| `notifications.events` non-empty with no `health.webhook_url` | **Warning** | The intent is legible and the fix is obvious; refusing to start over a stretch feature would be disproportionate |

## Effective values

```
effective_repo_cap(key) = min(
    repos[key].max_sessions or dispatch.default_repo_max_sessions,
    daemon.max_concurrent_sessions,
)
```

Reported as a default rather than as an explicit setting where the author set nothing (US2 AS4), so
`robot-army capacity` distinguishes "you chose 1" from "1 is what you get".
