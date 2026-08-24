# robot-army

A single-user daemon that turns labelled GitHub issues into real, interactive Claude Code
sessions in the terminal I already have open.

This is written for my future self. It is public so it can be read, not so it can be
adopted — there is no support, no stable API, and no packaging beyond a local
`pyproject.toml`. See [the constitution](.specify/memory/constitution.md).

## What it does

I label an issue I wrote. Within a couple of minutes, without touching a terminal:

1. The daemon polls GitHub, sees the label, and checks the issue is mine.
2. It creates an isolated git worktree on a new branch and runs that repository's
   preparation steps.
3. It launches a real interactive session into the running kitty instance, hosted by
   `dtach` so it survives the terminal dying.
4. It waits for proof the session actually started — a launch call returning success is
   not proof — and only then records the item as `active`.
5. When the session ends, a wrapper writes its exit status to a spool file the daemon
   drains. That file survives the daemon being down.

## Running it

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                  # the suite must pass

mkdir -p ~/.config/robot-army
$EDITOR ~/.config/robot-army/config.toml     # see specs/001-minimum-daemon/contracts/config.md
export ROBOT_ARMY_GITHUB_TOKEN=ghp_...       # or a mode-0600 token_file

uv run robot-army doctor       # run this first, every time
uv run robot-army onboard <repo-key>
uv run robot-army run
```

Two things are easy to overlook:

- **kitty must be running with a control socket.** `kitty.conf` needs
  `listen_on unix:/tmp/mykitty` and `allow_remote_control yes`. Kitty appends its PID, so
  configure the **glob**, never a fixed path.
- **Start the daemon by hand after graphical login**, not at boot. A daemon started before
  login has no display environment and no kitty to launch into.

`doctor` catches the failure that cost the most time during the spike: a kitty instance
carrying `CLAUDE_CODE_CHILD_SESSION` in its environment silently disables transcript
saving, producing sessions that look perfect, exit 0, and can never be resumed.

## Trying it without consequences

Four graduated effect levels, enforced at the boundaries rather than at call sites:

| Level | Polls | Worktrees | Sessions | GitHub writes |
|---|---|---|---|---|
| `plan` | real | no | no | no |
| `local` | real | **real** | no | no |
| `no-remote` | real | real | **real** | no |
| `live` | real | real | real | **real** |

Polling and eligibility are always real — a dry run that fakes its reads tells you nothing
about the main thing you want to check.

```bash
uv run robot-army run --dry-run --once          # plan
uv run robot-army run --effect-level local      # debug a repo's preparation steps
uv run robot-army status --include-simulated
uv run robot-army purge-simulated
```

Simulated rows are excluded from every listing unless you ask for them, and are visibly
marked when shown.

## Where things live

| Path | Contents |
|---|---|
| `~/.config/robot-army/config.toml` | configuration (never written by the daemon) |
| `~/.local/state/robot-army/state.db` | SQLite database |
| `~/.local/state/robot-army/logs/audit-*.jsonl` | the audit log |
| `~/.local/state/robot-army/logs/sessions/<item>.log` | per-session wrapper log |
| `~/.local/state/robot-army/spool/exits/` | exit records awaiting the daemon |
| `~/.local/state/robot-army/heartbeat.json` | liveness evidence |
| `~/.local/state/robot-army/daemon.lock` | single-instance lock |
| `/run/user/<uid>/robot-army/<item>.sock` | session host sockets |
| `~/worktrees/<repo>/issue-<n>/` | isolated checkouts |

XDG variables are honoured when set. Full detail in [docs/state.md](docs/state.md).

## Reading the logs

```bash
uv run robot-army log --since 10m
uv run robot-army log --item 42
uv run robot-army log --follow
```

Every outward-facing action appears **twice**: an `intent` record before it and an
`outcome` record after, sharing an `action_id`. An intent with no outcome is the signature
of a process killed mid-action. Format and conventions in
[docs/logging.md](docs/logging.md).

## When something looks wrong

```bash
uv run robot-army status              # counts, listings, outstanding anomalies
uv run robot-army show <item-id>      # one item's whole history and resume signals
uv run robot-army anomalies           # things detected but not resolvable
uv run robot-army repos               # why is nothing happening for this repo
uv run robot-army doctor              # environment and preconditions
```

Anomalies worth understanding rather than dismissing:

- **`orphan_session`** — a live worker under the worktree root that no item claims.
  `interrupted` does *not* mean nothing is running: if the wrapper dies uncleanly the
  worker keeps going, reparented, while dtach tears down its socket.
- **`no_transcript`** — the session ran but left nothing resumable. Almost always a
  `CLAUDE_CODE_*` variable in kitty's environment.
- **`registry_version_unknown`** — the worker's session-registry format changed. The
  daemon degraded to scanning `/proc` rather than crashing; identification is weaker until
  the version is reviewed.

## Recovering

Nothing resumes automatically — resume, abandon, and cancel are always mine to decide.

```bash
uv run robot-army show <id>       # uncommitted changes? commits on the branch? PR open?
uv run robot-army resume <id>     # new session, prior context restored
uv run robot-army restart <id>    # new session, no prior context
uv run robot-army cancel <id>     # stop that session's process tree and no other
uv run robot-army abandon <id>    # give up; the worktree is left alone
```

Reattach to a running session directly:

```bash
dtach -a /run/user/$(id -u)/robot-army/<item>.sock
```

## Noticing it has died

A dead daemon cannot report its own death, so the checker is a separate process and the
**timer**, not the daemon, is the dead-man's switch.

```bash
cp systemd/robot-army-health.* ~/.config/systemd/user/
systemctl --user enable --now robot-army-health.timer
uv run robot-army health          # exits 4 if the heartbeat is stale or absent
```

## Cleaning up

There is no automatic worktree removal — a prepared worktree was measured at up to 499 MB,
so disk is a real constraint, but deleting work automatically is worse.

```bash
uv run robot-army worktree list             # size, branch, condition
uv run robot-army worktree remove <id>      # refuses if dirty — that refusal is the point
uv run robot-army worktree prune
```

Removal is two steps: the worktree *and* its branch. Skipping the second accumulates
`robot-army/*` branches forever.

## Design notes

The reasoning lives in `specs/001-minimum-daemon/`: [research.md](specs/001-minimum-daemon/research.md)
records twenty decisions with their rejected alternatives,
[plan.md](specs/001-minimum-daemon/plan.md) carries the constitution check, and
[data-model.md](specs/001-minimum-daemon/data-model.md) has the state machines and the
"interrupted at X → result on next start" table.

Three implementation details are counter-intuitive enough to be worth naming here, because
each looks like a bug to a reader who does not know why:

- **`dtach` takes no `--` separator.** It rejects one outright. The wrapper needs its own.
- **The wrapper does not `exec` the worker.** `exec` would replace the shell and the exit
  code could never be captured, which is the wrapper's entire reason to exist.
- **`git worktree remove` is never given `--force` by default.** Git's refusal to remove a
  dirty worktree is the guard, not an obstacle.

## Licence

MIT.
