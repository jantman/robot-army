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

Issues can also start life as a card on a private Trello board, so I can capture a task from
my phone — see [the intake board](#the-intake-board). That path files an issue and stops:
labelling it is still mine to do.

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
- A state-changing request that a **browser** reports as coming from another site is refused
  with `403`. This is the one attack the model above does not already accept: a forged
  request needs no network path to the port at all, only my own browser — already inside the
  trust boundary — having some unrelated page open while the interface is running. It is not
  authentication; it identifies nobody, holds no state, and asks one question. Clients that
  send neither `Origin` nor `Sec-Fetch-Site`, `curl` included, are allowed through: they can
  reach the port directly anyway, which is the model above.
- **Reach it by address, not by name.** Any request whose `Host` is a hostname other than
  `localhost` is refused with `403`. Comparing `Origin` to `Host` is not enough on its own,
  because DNS rebinding lets an attacker control both: point `evil.test` at `127.0.0.1`, get
  my browser to load `http://evil.test:8420`, and every header agrees with every other while
  the request really lands here. Rebinding needs a *name*, so requiring an address closes it
  — and `[web] bind` already has to be an address for the same reason.

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

## The intake board

Optional, and **absent by default** — an installation with no `[trello]` section makes no
board request at all and behaves exactly as it did before this existed.

I put a card on a private Trello board from my phone, tag it, and it becomes a GitHub issue
in the repository the card names. That issue is **unlabelled**, so nothing runs: labelling it
is still the human gate, and the board cannot reach past it. The card then follows its issue —
into the in-progress list while a session runs, into the done list when the issue closes, and
back where it came from if the work is abandoned.

```toml
[trello]
board_id         = "5f3a..."        # required when the section is present
label            = "AI-task"        # the tag that marks a card as work
in_progress_list = "In Progress"
done_list        = "Done"
poll_seconds     = 300              # slower than GitHub's 60, deliberately

key_env          = "TRELLO_API_KEY"     # the NAME of a variable, never the value
token_env        = "TRELLO_API_TOKEN"   # or key_file / token_file, mode 0600
```

```bash
export TRELLO_API_KEY=...     # https://trello.com/power-ups/admin
export TRELLO_API_TOKEN=...
uv run robot-army doctor      # now checks the board too — all six must be green
uv run robot-army cards       # what is on the board and what became of it
```

`doctor` verifies at startup, and refuses to *ingest* — not to run — if any of these fail:

- The board is reachable and the credentials work.
- **The board is private.** A public board is not a person I chose, and board access is the
  only authorization this path has.
- The configured tag exists. A renamed label produces zero matching cards, which is
  indistinguishable from an empty board — the system would sit there looking healthy and
  doing nothing.
- Both lifecycle lists exist. A missing one is otherwise discovered halfway through a
  lifecycle, after the issue already exists.

The board's **member list is recorded and never gated on**. Who else may see my own private
board is my decision, and a second member can at most cause an unlabelled issue to be filed —
only I can cause one to run.

### When a card doesn't say enough

A card that names no configured repository, or names two, is **held** rather than guessed at.
It gets one comment saying what is missing, it appears in `robot-army cards` and on `/cards`
with its reason, and editing the card to name a repository resolves it on the next pass with
no further action.

```bash
uv run robot-army cards --state needs_info
uv run robot-army rescan <card-id>          # or --all-needs-info
```

A repository reference only counts if it is **already configured**. A card description is
often pasted from a log, and `src/robot_army` and `docs/roadmap.md` both look exactly like an
`owner/name`; filtering against `[repos.*]` means an unknown reference cannot select anything,
so the worst case is a held card rather than an issue filed somewhere I never named.

### One card, one issue

Enforced by two unique indexes rather than by code that has to remember, so a path that
skipped its check fails loudly instead of quietly duplicating. Creation is four steps with the
intent written first, and every seam between them is separately resumable — including the
dangerous one, where the issue exists and nothing local knows it yet.

If the database is lost entirely, each card's own comment names its issue, and the next poll
rebuilds the mapping from it rather than filing a second one. The one gap left open is a crash
between creating the issue and recording it *combined with* losing the database, and it is
written down in [docs/state.md](docs/state.md) rather than pretended away.

## How many sessions run at once

The cap counts **every** Claude session running as me, not just the ones the daemon
started. My own terminal sessions share the same subscription, so a cap that ignored them
protected nothing on the machine where I actually work.

```toml
[daemon]
max_concurrent_sessions = 2         # the whole machine, mine included

[dispatch]
order = "oldest-first"              # or "repo-priority"
default_repo_max_sessions = 1       # per repository, unless overridden below

[repos.example]
path = "~/GIT/example"
max_sessions = 2                    # optional; overrides the default above
priority = 10                       # optional; higher runs first under repo-priority
```

```bash
uv run robot-army capacity     # total, cap, mine vs. others, per repo, order in force
uv run robot-army status       # the same summary, plus the queue with positions
```

**Set the cap higher than feels right.** The first live `capacity` on this desktop reported
`2 of 2 sessions running, 0 ours, 2 other` — with the daemon having started nothing. Two
sessions of my own is my ordinary working state, so the default of 2 gives the daemon zero
slots and it never dispatches. The cap has to be my usual session count *plus* however many
robots I actually want; 3–4 is the realistic starting point.

Four things worth knowing:

- **One session per repository by default.** Two sessions in one clone share its ports, its
  dev server, and its submodule fetches. A repository that genuinely tolerates two says so.
- **A repository at its cap blocks its own work and nothing else.** A busy repository never
  stalls the queue; the next item in a different repository dispatches in the same pass.
- **When the count cannot be observed, nothing dispatches.** If the session registry is
  unreadable *and* `/proc` cannot be enumerated, the number of live sessions is unknown
  rather than zero — so work is held, an anomaly is raised, and `capacity` exits non-zero.
  A visible stall is a better failure than an invisible over-subscription. The middle case —
  registry unusable, `/proc` readable — counts via `/proc` and says `degraded`. Be aware that
  the `/proc` fallback matches on the binary *name*, so on this machine it also counts Claude
  Desktop: 11 processes where the registry says 2. The over-count is the safe direction, but
  in practice a degraded observation means "permanently at capacity". If the registry becomes
  unreliable, fix the registry.
- **Running work is never touched to reclaim capacity.** Lowering the cap under running
  sessions withholds new dispatch and leaves everything in flight alone.

`repo-priority` drains higher-priority repositories first and breaks ties oldest-first.
There is deliberately no aging: a low-priority repository can wait indefinitely while a
high-priority one keeps producing work, which is what choosing that mode means.

## Being told when something happens

Off by default — nothing is sent until I ask, and there is no second webhook to configure.
It reuses `[health] webhook_url`.

```toml
[health]
webhook_url = "https://ntfy.sh/my-private-topic"

[notifications]
events = ["failure", "needs_info"]  # dispatch | completion | failure | needs_info
max_per_cycle = 5
```

At most `max_per_cycle` messages per daemon tick, then one summary naming how many were
held back and of which kinds. The bound is per *burst* rather than per event, because a
backlog produces different items and per-item de-duplication would not bound it at all.
Every send is in the audit log whether or not it left the machine.

Messages carry identifiers and state names only. There is no field a credential could
reach, and a test asserts it across a run that includes an authentication failure.

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

| Level | Polls | Worktrees | Sessions | GitHub writes | Notifications |
|---|---|---|---|---|---|
| `plan` | real | no | no | no | no |
| `local` | real | **real** | no | no | no |
| `no-remote` | real | real | **real** | no | no |
| `live` | real | real | real | **real** | **real** |

Cleanup follows the *worktree* row rather than the GitHub one: simulated at `plan`, real at
`local` and above, because removing a worktree is a local effect. A notification leaves the
machine, so it follows the GitHub row.

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

A prepared worktree was measured at up to 499 MB, so disk is a real constraint — but
deleting work is irreversible, so automatic cleanup is **off by default** and stays off
until I turn it on.

```toml
[cleanup]
on_issue_close = false      # true: reclaim a finished item's worktree and branch
```

```bash
uv run robot-army worktree list             # size, branch, condition, cleanup state
uv run robot-army worktree remove <id>      # refuses if dirty — that refusal is the point
uv run robot-army worktree prune
uv run robot-army cleanup                   # every eligible item, under the same guards
uv run robot-army cleanup <id>              # one item, reconsidering a retained decision
```

With `on_issue_close = true`, an item whose issue has closed has its worktree and branch
reclaimed on the next reconciliation pass — provided nothing in either exists only there.
`robot-army cleanup` runs the identical function under the identical guards whether or not
the automatic path is enabled, so the manual route cannot drift from the automatic one.

**The two guards are different guards, and that is the whole design.**

- **The worktree**: git's own refusal, taken as-is. `git worktree remove` refuses on a dirty
  tree — *including merely untracked files* — and `--force` is never passed. A refused
  worktree is recorded as `retained` with git's own message, and the branch half is not
  attempted, because a dirty worktree means the branch may hold the only copy of something.
- **The branch**: my own containment check, because git's is the wrong one here. `git branch
  -d` accepts only a branch merged into the clone's current `HEAD`; the normal case is a PR
  merged on GitHub while my clone has a stale `main` checked out, so `-d` would refuse every
  time and `robot-army/*` branches would accumulate forever. Instead the base ref is
  fetched and the branch is deleted only if every commit on it is provably on the remote —
  contained in the published base, or pushed and up to date. If git cannot answer, that is
  "unproven", never "safe", and the branch is kept.

Four outcomes, all visible in `robot-army show <id>` and on the item's web page:

| `cleanup_state` | Worktree | Branch | Meaning |
|---|---|---|---|
| `done` | removed | removed | both guards passed |
| `branch_retained` | removed | kept | containment could not be proved |
| `retained` | kept | kept | git refused the worktree |
| `skipped` | kept | kept | a session was still live — reconsidered next pass |

`skipped` is the only one the automatic pass revisits: it means "not yet", where `retained`
means "we looked and decided no". `worktree_path` and `branch` are kept on the record even
after a successful removal, so "what was at this path?" stays answerable.

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
