# Contract: Consumed External Formats

Formats this project **reads but does not own**. They can change or disappear in any upstream
release, so each entry records what is depended on, how the dependency is guarded, and what the
degraded path is.

The constitution requires failure-path tests for code parsing external input. Every format here
carries a fixture set covering: absent, empty, truncated, unknown version, and semantically wrong
content.

---

## `~/.claude/sessions/<pid>.json` — live session registry

**Status**: undocumented internal format. Load-bearing anyway, because M0 measured it as an exact
1:1 registry of live sessions with no stale entries in the happy path — which turns the
database↔process join from best-effort into exact.

**Fields consumed**

| Field | Use |
|---|---|
| `sessionId` | **The join key.** The daemon generated it via `--session-id`, so this is exact |
| `pid` | Liveness, cross-checked against `/proc` |
| `procStart` | **PID-reuse guard** (kernel start-time ticks). Never trust `pid` alone |
| `cwd` | Classifies orchestrator-owned vs the maintainer's own session |
| `status` | `busy` / `idle` / `shell` — displayed, never used for control decisions |
| `version` | **The guard.** Parsing is gated on a known-compatible set |

**Guard and degraded path**: on an unrecognised `version`, raise a `registry_version_unknown`
anomaly **once** and fall back to enumerating `/proc/*/exe` for the worker binary, classifying by
`/proc/<pid>/cwd`. Never crash — a worker upgrade must not take the daemon down.

**Absolute prohibition**: `<pid>.<hash>.key` files sit alongside these, mode 0600, and appear to be
session credentials. The daemon **must never open, read, copy, or log them.** This is worth a test
that asserts no code path opens a `.key` file.

**Unrelated but worth recording so nobody mistakes it for a liveness source**: `~/.claude/session-env/`
accumulates stale entries — M0 measured 285 directories against 2 live sessions.

## `~/.claude.json` — workspace trust

**Fields consumed**: `projects["<primary clone path>"].hasTrustDialogAccepted` (boolean).

**Why it matters**: trust is keyed on the **primary clone**, not the worktree (M0 E1.5). A worktree
of a trusted repo never prompts; a worktree of an untrusted one blocks on a modal dialog forever —
an invisible hang with no diagnostic. Checking at dispatch converts that into a clear failure.

**Degraded path**: a missing file or missing key is treated as **not trusted**, which fails the
dispatch. Failing closed is correct here: the cost of a false negative is a clear error message; the
cost of a false positive is a session hanging invisibly forever.

## `/proc/<pid>/stat`, `/proc/<pid>/exe`, `/proc/<pid>/cwd`, `/proc/<pid>/cgroup`

Stable Linux kernel interfaces; the only ones here not at upstream's whim.

| Path | Use |
|---|---|
| `stat` field 22 (`starttime`) | PID-reuse guard, compared against the registry's `procStart` |
| `exe` | Process identity in the fallback path — resolved symlink, matched against the worker binary |
| `cwd` | Classifies a process as orchestrator-owned (under the worktree root) or not |
| `cgroup` | Yields the systemd scope recorded at confirmation as the terminate handle |

**Prohibition, and it is not stylistic**: **never** identify a process by matching its command line
(FR-039). M0 recorded two real incidents — a `pkill -f` that killed the invoking shell, and a
`pgrep -f` that matched kitty's `run-shell` wrapper instead of the intended process and produced a
wrong conclusion. `pgrep -f claude` returned 18 matches of which 12 were the desktop application.

Reads must tolerate the process exiting mid-read: `/proc/<pid>/*` raises `ProcessLookupError` or
`FileNotFoundError` at any moment, and that means "gone", not "error".

## Repository-committed `.claude/settings.json` and `.claude/settings.local.json`

**Read via `git show <base-ref>:<path>`, never from a filesystem path.** What matters is what a
freshly created worktree will contain, which is the committed content at the base branch tip.

The daemon does **not** interpret these files. It hashes their bytes for the fingerprint check
(FR-004). Parsing them would mean tracking upstream's settings schema for no benefit — a change is a
change regardless of what it means.

**Why this exists**: M0 F9. The workspace-trust dialog also accepts whatever tool permissions a repo
has *committed*, and reports it plainly: *"This folder pre-approves 3 tool permissions … These will
apply without asking."* On a whitelisted repository the maintainer does not control, anyone with
commit access can pre-approve tools a dispatched session will honour silently. M0 measured the
exposure across 294 repositories as 1 committed `settings.local.json` and 4 committed
`.claude/settings.json` — small and checkable.

## `kitty @ ls` JSON

**Fields consumed**: `user_vars` (the `ra_item` key set at launch — an exact lookup), plus `id`,
`cwd`, `pid`, `title` for diagnosis.

**Not consumed**: `foreground_processes`. It is ambiguous — `--hold` inserts a `kitten run-shell`
layer that repeats the entire command in its own argv, so the same string appears at several depths.
M0 records this producing a wrong conclusion during the spike.

**Degraded path**: if no socket answers the probe, dispatch fails with a clear precondition error
(FR-067). Reconciliation continues, because the session registry and `/proc` are independent of
kitty being reachable — a session survives its display dying, and the daemon must still be able to
reason about it.

## Worker command-line surface

Consumed flags, all confirmed against the version M0 tested: `--session-id`, `--resume`,
`--remote-control`, `-n/--name`, `--permission-mode`, `--model`, `--add-dir`, `--settings`.

**Two silent-tolerance hazards to validate before launch rather than discover at runtime** (M0):
`--add-dir` pointing at a nonexistent path, and a malformed `--settings` file, **both exit 0 and
proceed**. Upstream documentation notes invalid settings are "silently ignored" in print mode,
implying interactive mode may show a blocking dialog instead — the same invisible-hang hazard as the
trust dialog. FR-026 requires validating generated paths and settings before launch.

**`--bare` must never be used.** It skips CLAUDE.md, hooks, skills, plugins, and MCP auto-discovery —
exactly the accumulated per-repository context that makes these repositories work well.

**Version drift**: `robot-army doctor` records the worker version it sees. A flag that disappears
should surface as a clear dispatch failure naming the flag, not as a mystery exit code.
