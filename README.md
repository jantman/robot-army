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

## The web interface

A second front end onto the same operations, so I can see what is running and decide an
interrupted item from my phone without opening a terminal.

```bash
uv run robot-army serve        # http://127.0.0.1:8420, the shipped default
```

**Two processes, started by hand after graphical login, in either order.** The interface is
deliberately separate from the daemon: it starts, stops, and survives on its own, so the
audit log and the interrupted list stay readable during exactly the incident that makes them
worth reading.

```bash
uv run robot-army run &        # the daemon
uv run robot-army serve        # the interface
```

To reach it from the phone, name the machine's LAN address:

```toml
[web]
bind = "127.0.0.1"      # the LAN address, or 0.0.0.0 for every interface
port = 8420
refresh_seconds = 10    # how often an open page re-fetches itself
```

### Read this part

**There is no authentication, and that is deliberate.** The operating-system user stops being
the trust boundary the moment this binds to anything but loopback — the network becomes the
boundary instead. **Anything that can reach that port has full control of robot-army**: it can
resume sessions, cancel them, abandon work, and pause dispatch.

That is the accepted model, so the mitigations are the ones that matter:

- The default is loopback. Widening it is an explicit edit to the config.
- A **globally routable** bind address is refused outright, exit `3`. The interface will not
  start somewhere the internet can reach it.
- The effective address is printed at startup and written to the audit log as `web.start`, on
  every start, with a loud warning when it is not loopback. That is the one fact about this
  design that is never allowed to be silent.

From outside the house I connect my existing VPN and use the same LAN address. Nothing is
published, no tunnel is configured, and no port is forwarded.

### What it can do

Six views — active, queue, interrupted, one item, anomalies, and the audit log — and the
controls for the decisions I actually make away from the desk: resume, restart, abandon,
cancel, retry, attach a terminal, acknowledge an anomaly, pause and resume dispatch, and force
a poll or a reconciliation. Every one of them has a terminal equivalent, verified by a test
rather than by intention.

Deliberately **not** there: repository onboarding and permission re-approval, removing a
checkout or its branch, purging simulated rows, changing the concurrency limit, and anything
that starts or stops the daemon. Each stays a terminal command.

Add `.json` to any path, or send `Accept: application/json`, for the same facts as a payload:

```bash
curl -s localhost:8420/active.json  | jq '.items[] | {id, repo_key, state, title}'
curl -s localhost:8420/queue.json   | jq '.counts'
curl -s 'localhost:8420/log.json?item=42&outcome=error' | jq '.records'
```

It is not a stable API. It is versioned by the commit that produced it.

Nothing is fetched from a third-party host — no web font, no CDN, no icon set — so every view
works with the machine offline. Every page renders on a phone in a single column, and works
with scripting disabled, merely static until reloaded.

## Pausing dispatch

```bash
uv run robot-army pause        # or the control on the queue view
uv run robot-army unpause
```

While paused the daemon still polls, evaluates eligibility, reconciles, and heartbeats. It
starts **no new session**, and eligible items accumulate in `ready` — nothing is rejected and
nothing is lost.

The pause is durable: it survives a daemon restart and a reboot, and is cleared only by
`unpause` or the web control. Never by time. It appears in `status`, in `heartbeat.json`, and
on every web view, because a system that is healthy and deliberately doing nothing must not
read as one that is healthy and doing nothing for no reason.

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
| `~/.local/state/robot-army/requests/` | markers asking the daemon to poll or reconcile now |
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

Records carry which interface produced them — `daemon`, `cli`, or `web` — and the same log is
readable from the browser at `/log`, filtered, newest first, with GitHub links already made.

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
