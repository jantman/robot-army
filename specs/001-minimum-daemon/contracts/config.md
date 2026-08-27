# Contract: `config.toml`

Location: `~/.config/robot-army/config.toml` (honours `XDG_CONFIG_HOME`). Read-only to the daemon —
it never writes this file, which is what keeps it safe to hand-edit.

Per the split described in [data-model.md](../data-model.md): this file holds what the maintainer
**declares**. Onboarding approvals, fingerprints, and polling bookkeeping are observations and live
in the database, not here.

## Complete example

```toml
[daemon]
effect_level      = "live"          # plan | local | no-remote | live
tick_seconds      = 5
poll_seconds      = 60
reconcile_seconds = 60
dispatching_max_age_seconds = 900   # FR-041
confirm_timeout_seconds     = 45    # FR-025 dispatch confirmation window
max_concurrent_sessions     = 2     # FR-028

[paths]
worktree_root = "~/worktrees"
# state_dir and socket_dir default per R16; override only if you have a reason

[github]
author         = "jantman"          # FR-007 security boundary; see note below
label          = "robot-army"
token_env      = "ROBOT_ARMY_GITHUB_TOKEN"
# token_file   = "~/.config/robot-army/github-token"   # alternative, must be mode 0600
include_owned  = true               # any repo you own may be onboarded (see note below)
extra_repos    = ["someorg/theirrepo"]
timeout_seconds = 20
max_retries     = 4

[worker]
permission_mode = "auto"            # acceptEdits | auto | bypassPermissions | manual | dontAsk | plan
model           = ""                # empty = worker default
base_branch     = "main"
branch_prefix   = "robot-army"

[terminal]
socket_glob   = "/tmp/mykitty-*"    # pattern, never a fixed path — kitty appends its PID
probe_timeout_seconds = 2

[health]
max_age_seconds = 180
webhook_url     = ""                # empty disables; any service accepting a JSON POST

[hooks]
# The shared default, applied to any repo with no explicit post_create.
# M0 measured this as the dominant case: 39 of 294 repos, all virtualenv setup.
default_timeout_seconds = 300

# Added by milestone 002. All three are optional with the defaults shown.
[web]
bind            = "127.0.0.1"       # the LAN address, or 0.0.0.0, to reach it from a phone
port            = 8420
refresh_seconds = 10                # how often an open page re-fetches itself

# --- Repositories -----------------------------------------------------------
# A section here declares a repo. It still requires `robot-army onboard` before
# anything is dispatched (FR-001) — declaring is not approving.

[repos.equipment-status-board]
path        = "~/GIT/equipment-status-board"
base_branch = "main"
post_create = [
  { run = "make setup", timeout = 300 },
  { link = ".env" },                        # from the primary clone
]
env = { ESB_HOST_PORT = "auto", ESB_DEV_HOST_PORT = "auto" }

[repos.specfiles]
path = "~/GIT/specfiles"
post_create = [
  # M0 F15: this repo's .gitmodules uses git:// URLs, which GitHub disabled in
  # 2021. Port 9418 is now dropped rather than refused, so this HANGS rather
  # than failing. The timeout is the whole point of this entry.
  { run = "git submodule update --init --recursive", timeout = 120 },
]

[repos.electronics-projects]
path = "~/GIT/electronics-projects"
# no post_create: M0 F13 found most repos need nothing, because a dispatched
# session builds, tests, and lints — it does not run the production app.

[repos.privatepuppet]
path            = "~/GIT/privatepuppet"
permission_mode = "acceptEdits"     # per-repo override of [worker]
```

## Field rules

**`[daemon]`**
- `effect_level` MUST be one of the four values. Command line overrides it.
- `tick_seconds` bounds exit-detection latency (R5). Must be ≥ 1.
- `poll_seconds` and `reconcile_seconds` must each be ≥ `tick_seconds`.
- `dispatching_max_age_seconds` SHOULD exceed the sum of the longest repository's preparation
  timeouts with margin. Validation warns if it does not.
- `max_concurrent_sessions` must be ≥ 1. Counts simulated sessions too (FR-055) — they burn the
  same subscription quota.

**`[github]`**
- `author` is a **security boundary** (FR-007). There is deliberately no "any author" value and no
  way to disable the check. Validation rejects an empty value.
- Exactly one of `token_env` or `token_file` must be set. A literal token in this file is a
  validation error, not a warning — the repository is public (Principle V).
- `token_file` must be mode 0600 or startup fails.
- `extra_repos` are repositories the maintainer does not own. They still require onboarding, and the
  committed-permission fingerprint check (FR-004) matters most for exactly these.
- **Superseded by [milestone 005](../../005-onboard-is-enough/contracts/config.md).** `include_owned`
  and `extra_repos` were written here as though they governed *what is polled*, and enumerating the
  author's own repositories was the mechanism implied. Neither is true. Nothing enumerates — the
  ownership question is answered by one `GET /repos/{owner}/{name}` for the repository being named —
  and polling follows the **onboarding record**, not either key. What these two govern is what may be
  **onboarded**: `include_owned = true` permits any repository the author owns, `extra_repos` permits
  the specific repositories listed regardless of owner, and a repository permitted by neither is
  refused at onboarding with the setting named. This is a **mistake guard and not a security
  boundary** (005 FR-026) — the `author` check above is the boundary, and it remains undisableable.

**`[web]`** *(milestone 002)*
- `bind` is the interface's **access policy**, because it has no authentication by design
  ([002 FR-003](../../002-web-ui/spec.md)). Anything that can reach the port has full control.
  - A **globally routable** address is refused and `robot-army serve` exits `3`. This is the one
    validation in this file that is a safety property rather than a convenience.
  - `0.0.0.0` is permitted with a warning: it cannot be classified — it means every interface,
    including any the machine gains later — and refusing it would push toward pinning an address a
    DHCP lease can change.
  - Anything other than loopback prints a one-line warning naming the consequence. The effective
    address is also written to the audit log as `web.start`, on every start.
  - It must be an **address, not a hostname**, so what the interface became reachable from is
    unambiguous.
- `port` must be ≥ 1 and ≤ 65535. 8420 is arbitrary, above 1024 so no privilege is needed.
- `refresh_seconds` bounds how often an open page re-fetches itself. Must be ≥ 1.
- Validation of `bind` beyond its type happens at `serve` time rather than at load time, so that
  `robot-army status` never fails over a setting only the interface uses.

**`[terminal]`**
- `socket_glob` is a **pattern, not a path** — kitty appends its PID to `listen_on`, so no fixed
  path exists (M0). The daemon globs and probes each candidate, taking whichever answers.

**`[repos.<key>]`**
- `path` must exist and be a git repository at startup, or that repository is reported as
  misconfigured. Other repositories still work — one bad entry does not stop the daemon.
- `post_create` steps run in order, in the worktree, after branch creation.
  - `{ run = "...", timeout = N }` — shell command. `timeout` defaults to
    `hooks.default_timeout_seconds`. **Every step is bounded** (FR-013) — M0 F15 is why.
  - `{ link = "path" }` / `{ copy = "path" }` — from the primary clone into the worktree. Expressed
    as first-class forms rather than shell commands because they must be idempotent and readable.
  - A non-zero exit or a timeout fails the work item and **no session is launched** (FR-014).
- `env` values are injected into the session. `"auto"` means "allocate a free port" — the mechanism
  M0 E6.6 identified for per-worktree port assignment.

## Validation behaviour

Startup validates the whole file and reports **every** problem at once, then exits `3`. Fixing one
typo per restart is a poor experience at 2am, which is the audience the constitution names.

Unknown keys are a warning, not an error, so a config written for a later milestone still starts.
Unknown keys inside `[repos.<key>]` are an **error**, because a typo there silently disables a
preparation step and produces a broken worktree.
