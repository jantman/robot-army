# Contract: Command-Line Interface

The only interface in this milestone (FR-066). Milestone 002's web UI is a second front end onto
these same operations, so every command here is a candidate for that API and should be implemented
as a function the CLI calls, not as logic living in the argument parser.

**Universal rules**
- Every command exits `0` on success and non-zero on failure, with the reason on stderr (FR-069).
- Exit codes: `0` success, `1` operation failed, `2` usage error, `3` precondition not met
  (daemon not running, lock held, config invalid), `4` check failed (used by `health`).
- `--json` on any read command emits machine-readable output on stdout; without it, output is a
  human-readable table.
- `--include-simulated` on any listing command includes `dry_run` rows. **Without it they are
  excluded** (FR-056). Simulated rows are always visibly marked when shown (FR-057).
- Read-only commands do not take the daemon lock and work while the daemon runs. Commands marked
  **[lock]** take it and fail with exit `3` if the daemon holds it.

---

## `robot-army run`

Runs the daemon in the foreground. This is the whole product.

| Option | Default | Meaning |
|---|---|---|
| `--effect-level {plan,local,no-remote,live}` | from config | Per R7 |
| `--dry-run` | — | Alias for `--effect-level plan` (planning §2 asks for this ergonomic) |
| `--config PATH` | `~/.config/robot-army/config.toml` | |
| `--once` | off | Run exactly one cycle and exit. For testing and for the quickstart |

Startup sequence, in order, all before any work is dispatched:
1. Acquire the single-instance lock, or exit `3` naming the holding PID (FR-070).
2. Load and validate config; on failure exit `3` with **every** problem listed, not just the first.
3. Check preconditions and exit `3` if unmet (FR-067): the terminal control socket answers a probe,
   the state directory is writable, the database opens and migrates.
4. Log the effect level loudly (FR-057).
5. **Reconcile** (FR-037).
6. Enter the loop.

`SIGTERM` and `SIGINT` finish the current tick, release the lock, and exit `0`. They never touch
running sessions (FR-049).

## `robot-army status`

The default view. Shows effect level, health, and counts and listings by state: active, ready,
dispatching, interrupted, awaiting review, blocked, plus unacknowledged anomalies.

| Option | Meaning |
|---|---|
| `--state STATE` | Filter to one work item state |
| `--repo KEY` | Filter to one repository |
| `--json` | Machine-readable |

## `robot-army show <item-id>`

Everything about one work item: source links, state history with timestamps, every session attempt
with exit codes and signals, worktree path and branch, and the FR-048 resume-decision signals —
uncommitted changes, commits on branch, issue open or closed, open pull request.

## `robot-army poll [--repo KEY]` **[lock-aware]**

Forces an immediate poll rather than waiting for the interval. If the daemon is running, signals it
to poll on its next tick; if not, polls directly. Reports what it found and what it rejected.

## `robot-army reconcile` **[lock-aware]**

Forces a reconciliation pass. Same delegation behaviour as `poll`.

## `robot-army cancel <item-id>`

Stops that item's running session and only that session (FR-050), via the recorded systemd scope.
Leaves the work item `interrupted` and the worktree untouched. `--force` skips the confirmation
prompt.

## `robot-army resume <item-id>`

Starts a new session that restores the previous session's context, using the recorded `session_id`
(FR-047). Requires the item to be `interrupted` or `awaiting_review`. **Never happens
automatically** (FR-046).

## `robot-army restart <item-id>`

Starts a fresh session in the existing worktree with no prior context. New `session_id`, new attempt
number.

## `robot-army abandon <item-id>`

Marks the item `abandoned`. Does not remove the worktree — that is `worktree remove`, deliberately
separate so abandoning is never destructive.

## `robot-army retry <item-id>`

Moves a `failed` item back to `ready`. Refuses, with the reason, if the blocking condition still
holds — a repository still untrusted, a fingerprint still unapproved.

## `robot-army onboard <repo-key>` **[lock]**

The deliberate per-repository trust step (FR-001). Prints the primary clone path, whether the
worker's trust dialog has been accepted for it, and the full contents of any committed
`.claude/settings.json` or `.claude/settings.local.json` **as they exist at the base branch tip** —
because that is what a dispatched session will honour (FR-004, M0 F9). Requires explicit
confirmation, then records the approval and the fingerprint.

| Option | Meaning |
|---|---|
| `--reapprove` | Re-approve after a fingerprint change; prints a diff of the committed settings against the approved version |
| `--yes` | Skip the prompt. Refuses to skip when committed settings are present and unapproved |

## `robot-army repos`

Lists configured repositories with onboarding status, fingerprint status, and trust status. This is
where "why is nothing happening for this repo" gets answered.

## `robot-army worktree list | remove <item-id> | prune`

`list` shows worktrees with size, branch, and condition (present, dirty, missing/prunable).

`remove` removes both the worktree and its branch, or reports clearly that it removed only one
(FR-016 — two steps, and doing only the first accumulates `robot-army/*` branches forever). It
**refuses** on a worktree with uncommitted or untracked changes; `--force` overrides and requires
typed confirmation. There is no automatic removal in this milestone.

`prune` clears git's record of worktrees whose directories are gone.

## `robot-army health [--notify]`

Reads `heartbeat.json` and exits `0` if fresh, `4` if stale or absent. `--notify` additionally POSTs
to the configured webhook on failure. Intended to be run by a systemd user timer — **this, not the
daemon, is the dead-man's switch** (R15).

| Option | Default | Meaning |
|---|---|---|
| `--max-age SECONDS` | 3× reconcile interval | Staleness threshold |

## `robot-army anomalies [--acknowledge ID]`

Lists unacknowledged anomalies with enough detail to act. Acknowledging one allows a genuinely new
occurrence of the same kind to be recorded later.

## `robot-army log [--since DURATION] [--item ID] [--follow]`

Reads the audit JSONL. This is the FR-062 reconstruction path: what happened, when, to what, with
what result. `--follow` tails.

## `robot-army purge-simulated` **[lock]**

Removes `dry_run` rows and their sessions (FR-058). Never touches live rows. Reports counts and
requires confirmation. Does not remove worktrees those rows created — those are real directories on
disk, and removing them is `worktree remove`'s job.

## `robot-army doctor`

Checks and reports, without changing anything: config validity, database schema version, terminal
socket reachability, `dtach` and `git` presence, worker binary presence, state directory
permissions, whether the terminal daemon's environment carries `CLAUDE_CODE_*` variables that would
silently degrade sessions (R11, M0 F19), and disk free space under the worktree root. Exits non-zero
if any check fails.
