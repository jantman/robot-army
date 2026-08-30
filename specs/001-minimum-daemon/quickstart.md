# Quickstart & Validation Guide: Minimum Daemon

How to run this and how to prove it works. The scenarios below are ordered so each builds on the
last, and they climb the effect levels — nothing touches GitHub until scenario 5.

Referenced detail lives in [contracts/cli.md](./contracts/cli.md),
[contracts/config.md](./contracts/config.md), and [data-model.md](./data-model.md) rather than being
repeated here.

## Prerequisites

Confirmed present on this machine: Python 3.14.7, SQLite 3.53.4, `uv` 0.12.3, `dtach`, `kitty`
0.48.2, `git` 2.55.0, `systemctl --user`.

Two things must be true that are easy to overlook:

1. **kitty must be running with a control socket.** `kitty.conf` needs `listen_on unix:/tmp/mykitty`
   and `allow_remote_control yes`. Kitty appends its PID, so the actual socket is
   `/tmp/mykitty-<pid>` — configure the **glob**, never a fixed path.
2. **The daemon is started by hand after graphical login** (planning §8). Not at boot, deliberately:
   a daemon started before login has no display environment and no kitty to launch into.

```bash
# Verify kitty's socket answers before anything else
kitty @ --to unix:$(ls -t /tmp/mykitty-* | head -1) ls >/dev/null && echo "kitty ok"
```

## Setup

```bash
cd ~/GIT/robot-army
uv sync                      # creates .venv from pyproject.toml
uv run robot-army --help
uv run pytest                # the suite must pass before anything below
```

Configuration:

```bash
mkdir -p ~/.config/robot-army
$EDITOR ~/.config/robot-army/config.toml     # see contracts/config.md for a complete example
export ROBOT_ARMY_GITHUB_TOKEN=ghp_...       # or use token_file, mode 0600
```

Then check the environment before trusting it:

```bash
uv run robot-army doctor
```

`doctor` is worth running first every time. It catches the failure that cost M0 the most time:
a kitty instance carrying `CLAUDE_CODE_CHILD_SESSION` in its environment silently disables
transcript saving, producing sessions that look perfect, exit 0, and can never be resumed.

---

## Scenario 1 — Nothing happens, visibly (effect level `plan`)

**Validates**: FR-052 (reads always real), FR-051, US4 scenarios 1–2, SC-008.

```bash
uv run robot-army run --dry-run --once
uv run robot-army status --include-simulated
uv run robot-army log --since 10m
```

**Expected**: real polling occurred and eligibility was really evaluated; every intended worktree
creation, session launch, and GitHub comment appears in the log as an intention; **zero** GitHub
writes, **zero** sessions, **zero** filesystem changes under the worktree root. Simulated rows are
absent from `status` without `--include-simulated` and visibly marked with it.

**Failure to watch for**: if `status` shows simulated rows *without* the flag, FR-056's default query
scope is not enforced in the persistence layer.

## Scenario 2 — Debug a repository's preparation steps (effect level `local`)

**Validates**: FR-013, FR-014, US4 scenario 3, SC-009, SC-013.

```bash
uv run robot-army run --effect-level local --once
uv run robot-army worktree list
```

**Expected**: a real worktree exists on a real branch with preparation steps really run; **no**
session launched, **no** GitHub writes. This is the loop for getting a repository's `post_create`
right without burning subscription quota.

**Then deliberately break it.** Point a repository's `post_create` at
`git submodule update --init --recursive` for a repo whose `.gitmodules` uses `git://` URLs, with
`timeout = 10`:

```bash
uv run robot-army run --effect-level local --once
uv run robot-army show <item-id>
```

**Expected**: the step is killed at 10 seconds, the item is `failed` with captured output, and it did
**not** sit in `dispatching` forever. This is M0 F15 reproduced deliberately — the hang is the
realistic case, not a contrived one.

## Scenario 3 — A real session, no GitHub writes (effect level `no-remote`)

**Validates**: FR-018, FR-020, FR-025, US1, SC-001, SC-002.

```bash
uv run robot-army run --effect-level no-remote
```

**Expected**: a kitty tab appears with a live Claude Code session in the worktree; the item reaches
`active` **only after** the registry entry carrying the generated `session_id` was observed; no
comment appears on the GitHub issue.

Confirm the confirmation actually happened rather than being assumed:

```bash
uv run robot-army show <item-id> | grep -E 'confirmed_at|session_id'
ls ~/.claude/sessions/
```

**The negative test that matters most.** Break the launch deliberately — set `socket_glob` to a
pattern matching a dead socket, or point a repo's path at a nonexistent directory:

**Expected**: the item lands in `failed`, **not** `active`. `kitty @ launch` returns 0 and a valid
window id even when nothing started (M0 F16, demonstrated three times). If a broken launch produces
an `active` item, FR-025 is not really implemented.

## Scenario 4 — Survive the terminal dying

**Validates**: FR-021, FR-037, FR-040, FR-043, FR-049, US3, SC-004, SC-005, SC-007.

With a session running:

```bash
# From a DIFFERENT terminal — never the one hosting your working session
kill <kitty-pid>
uv run robot-army reconcile
uv run robot-army status
```

**Expected**: the session is **still running**, the item is **still `active`**, and it is
reattachable:

```bash
dtach -a /run/user/1000/robot-army/<item>.sock
```

The reattached session should repaint fully.

Now restart the daemon while sessions run. **Expected**: no session dies (they live in disjoint
systemd scopes, M0 F18), and reconciliation completes before any new dispatch.

Now the orphan case — the subtle one:

```bash
kill -9 <wrapper-pid>          # kill the wrapper, NOT the worker
uv run robot-army reconcile
uv run robot-army anomalies
```

**Expected on this machine**: the worker does **not** survive. Killing the wrapper takes `claude`
with it, almost certainly via SIGHUP when dtach's pty goes away — observed during the issue #1
verification round, and reproduced against the mechanism in isolation while planning #33: a
`kill -9` of a dtach master takes its child with it, leaving the socket file behind because
SIGKILL gives dtach no chance to clean up.

So **this route no longer produces an orphan**, and the absence of an `orphan_session` anomaly
here is not evidence that FR-043 is missing. What must still hold is that the item does not stay
`active` against a dead session: check `robot-army show <item>` reports `interrupted` and the
session `lost`. Before issue #33 was fixed it did not, at any level below `live`.

The design this scenario was written against is still real — M0 F17, a worker that outlives its
wrapper — and `interrupted` still does **not** mean nothing is running. It is simply no longer
reachable by killing the wrapper on this machine. To exercise the orphan path deliberately, use
scenario 3 of
`specs/20260830-133818-reconcile-session-liveness/quickstart.md`, which produces a live worker
that no current attempt accounts for by resuming an item whose first worker is still alive.

## Scenario 5 — The real thing (effect level `live`)

**Validates**: FR-010, FR-033, FR-035, US1, US2, SC-001, SC-003.

```bash
uv run robot-army onboard <repo-key>     # deliberate, one-time, per repository
uv run robot-army run
```

Label an issue you authored. **Expected**: within ~2 minutes a session appears, a comment lands on
the issue, and `status` shows it `active`.

Then exercise each outcome:

| Action | Expected state |
|---|---|
| `/exit` in the session | `awaiting_review`, session `exited_clean` |
| Close the issue | `done` |
| `robot-army cancel <id>` | `interrupted`, signal recorded, **no other session affected** |
| Misconfigure `permission_mode` and dispatch | `failed`, stderr captured, not retried |

**Exit-detection latency**: up to one tick (5 s by default) after `/exit`, because the wrapper writes
a spool file the daemon drains rather than POSTing to it (see
[contracts/exit-record.md](./contracts/exit-record.md)). This is the deliberate tradeoff for exit
records that survive the daemon being down.

## Scenario 6 — Onboarding refuses what it should

**Validates**: FR-003, FR-004, SC-014.

```bash
uv run robot-army onboard <repo-with-committed-claude-settings>
```

**Expected**: the full contents of the committed settings are printed for review before any
approval. Then change them at the base branch tip and attempt a dispatch.

**Expected**: dispatch is **blocked** pending `onboard --reapprove`, which shows a diff. Also confirm
a repository whose trust dialog has never been accepted fails at dispatch with a clear message
rather than launching a session that hangs on an invisible modal (M0 E1.5).

## Scenario 7 — Notice the daemon dying

**Validates**: FR-063, FR-064, US6, SC-011.

```bash
uv run robot-army health && echo "healthy"
kill -9 <daemon-pid>
sleep 200
uv run robot-army health; echo "exit=$?"      # expect 4
```

Install the dead-man's switch — note that the **timer**, not the daemon, is the switch, because a
dead daemon cannot report its own death:

```bash
systemctl --user enable --now robot-army-health.timer
systemctl --user list-timers robot-army-health.timer
```

## Scenario 8 — Reconstruct history from the log alone

**Validates**: FR-059 through FR-062, SC-010.

```bash
uv run robot-army log --item <id>
```

**Expected**: for every outward-facing action, an `intent` record **before** it and an `outcome`
record after, sharing an `action_id`. You should be able to answer what happened, when, to what, and
with what result without re-running anything. An `intent` with no `outcome` is the signature of a
process killed mid-action — that pairing is the point.

Confirm the GitHub token appears **nowhere**:

```bash
grep -ri 'ghp_' ~/.local/state/robot-army/logs/ && echo "REDACTION FAILURE" || echo "clean"
```

## Scenario 9 — Clean up

```bash
uv run robot-army worktree list                 # note the sizes; M0 measured 499 MB for one
uv run robot-army worktree remove <item-id>     # refuses if dirty — that refusal is the feature
uv run robot-army purge-simulated
```

**Expected**: removal refuses on a worktree with uncommitted *or merely untracked* files, and removes
**both** the worktree and its branch when it does proceed. Confirm with
`git branch --list 'robot-army/*'` — worktree removal alone always leaves the branch behind, and
skipping the second step accumulates branches in every repository forever.
