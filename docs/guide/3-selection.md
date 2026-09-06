# ② What runs next

An eligible item does not necessarily start. Five things decide, and each has a different
answer to "why is nothing happening?":

| Reason | Scope | Lifted by |
|---|---|---|
| Dispatch is paused | everything | `unpause` |
| The item or its repository is held | named work | `unhold` |
| The machine or the repository is at its session cap | one repository, or all | a session ending, or a higher cap |
| `wait_for_merge` and the previous item has not landed | one repository | merging the pull request |
| The board has the card parked elsewhere | one item | moving the card back |

`robot-army status` and `robot-army capacity` both say which one is in force, by name.

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
wait_for_merge = false              # globally; see below
project_ordering = true             # globally; see below

[repos."jantman/example"]           # an override, not a registration
max_sessions = 2                    # optional; overrides the default above
priority = 10                       # optional; higher runs first under repo-priority
wait_for_merge = true               # optional; overrides the default above
project_ordering = false            # optional; overrides [dispatch] project_ordering
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
- **A changed cap takes effect when the daemon restarts, and not before.** The daemon reads
  the file once at startup and never rereads it, so the cap it is enforcing is whatever the
  file said the last time `systemctl --user restart robot-army.service` ran.

### Which cap you are being shown

Every surface reports the count against **the cap the running daemon is enforcing**, not
against the file the reporting process happens to have read. The daemon publishes it on its
heartbeat, and `status`, `capacity` and every web view take it from there.

This matters because the two can differ in both directions, and the number is the one used
to answer "why is nothing dispatching?":

- Raise the cap and restart the daemon only — the documented go-live procedure — and a
  long-running `robot-army serve` is still holding the old, lower number. It used to print
  `6/5`: full and then some, when the truth was `6/7` with two slots free (issue #30).
- Raise the cap and restart *nothing*, and a freshly-run `robot-army capacity` reads the new
  number out of the file while the daemon is still enforcing the old one. The fresh reading
  is the wrong one, and it looks the most trustworthy.

When the two disagree, the surface says so and names both:

```
SESSION CAP MISMATCH: the running daemon is enforcing a cap of 7, and this process is
configured for 5. The cap shown is the daemon's, because the daemon is what enforces it.
One of the two has been running since before the configuration changed — restart that one
and they will agree.
```

It never refuses anything — unlike an effect-level mismatch, which does. The daemon enforces
its own cap whatever any other process believes, so a disagreement cannot make an action
unsafe; it just means one of the two processes needs restarting. Which one, it cannot tell
you: neither knows when the other read its configuration.

With no daemon running, or with nothing readable from its heartbeat, each surface falls back
to its own configured cap and says nothing — there is then nothing to disagree with.

`repo-priority` drains higher-priority repositories first and breaks ties oldest-first.
There is deliberately no aging: a low-priority repository can wait indefinitely while a
high-priority one keeps producing work, which is what choosing that mode means.

## Working a repository serially

`max_sessions` and `wait_for_merge` answer two different questions and I kept mixing them
up until I wrote this down.

**`max_sessions` counts live sessions.** The moment a session exits, the slot is free and
the next issue in that repository starts — from a default branch that does *not* yet
contain the work that just finished, because that work is sitting in a pull request I have
not looked at. For a repository where every issue touches the same files, that is the
collision, and no session cap can express the wait, because there is no session to count.

**`wait_for_merge` waits for the work to land.** With it on, a repository dispatches
nothing while it has an *unfinished* item — one that has been dispatched at least once and
has not reached `done` or `abandoned`. Merging the pull request closes the issue, a closed
issue makes the item `done`, and the next issue goes out on the following pass. Merging is
entirely mine to do: this never opens, approves or merges anything.

```bash
uv run robot-army status     # the held item, and "#41 is awaiting_review and has not landed"
uv run robot-army capacity   # both limits per repository, and where each one came from
```

Three consequences worth knowing before turning it on:

- **A `failed` item holds the repository too.** It is unfinished work, whatever condition it
  is in. `robot-army retry <id>` or `robot-army abandon <id>` says which — and the hold names
  the item, so I am not left guessing what stopped. `retry` re-reads the issue and re-checks
  its eligibility, author included, so it refuses rather than clearing a hold it should not.
- **It holds one repository, never the queue.** Every other repository dispatches in the
  same pass, exactly as it does under `max_sessions`.
- **It fast-forwards my own clone.** Before creating the worktree, the clone's local default
  branch is advanced to the freshly fetched remote head — see below. Sessions have always
  branched from `origin/<base>`, so this is for *my* clone rather than for theirs.

### The clone fast-forward

Only for repositories with `wait_for_merge` in force, and only ever a fast-forward. It is
skipped, with the reason recorded, whenever the clone has uncommitted changes (untracked
files included), is on another branch or a detached `HEAD`, is mid-rebase, mid-merge,
mid-cherry-pick or mid-bisect, has no such remote, or holds commits on its default branch
that the remote does not. There is no `--force` anywhere in it and nothing is ever reset or
rebased. A skip never fails the dispatch — the worktree comes off the remote-tracking ref
either way.

```bash
uv run robot-army log --json | grep git.fast_forward   # outcome, reason, and the shas
```

## Ordering work from a project board

If a GitHub project is linked to a repository, **it decides the order that repository's
issues run in** — the card at the top of the ready column goes first. Drag a card, and the
next poll follows. Nothing to configure: one linked project with an obvious ready column
is found and used.

```bash
uv run robot-army status       # which board governs each repository, and why not
uv run robot-army doctor       # the token, the project, the column, the freshness
```

**A card parked in another column is held.** Moving an issue to `Backlog` is you saying
"not yet", and it stops dispatching until you move it back — with the reason on screen
naming the column it is in:

```
demo: #48 is in 'Backlog', not the dispatch column 'Ready' — move it there, or set
project_ordering = false for this repository
```

**A labelled issue that is not on the board at all still runs.** That is not an
oversight — an issue nobody put on the board is no signal either way, so it dispatches
after everything the board actually ranked. The board says what to do *first*, not what is
allowed.

Six things worth knowing:

- **The label is still the gate.** Being in the ready column is not enough, and the author
  check is untouched. The board narrows and orders; it never admits.
- **Only within a repository.** Board order never changes how repositories interleave —
  `order` and `priority` keep doing exactly what they did. The set of queue positions a
  repository holds is unchanged; only which of its issues sits at each one moves.
- **`Ready` or `Todo`, found automatically.** GitHub's Kanban template offers `Ready` and
  the simpler board offers `Todo`, so most boards need no configuration. A board with both,
  or with neither, is reported rather than guessed at — set `project_column`.
- **Two linked projects is an error, not a coin flip.** `status` names both and the
  repository keeps dispatching in its configured order until you set `project`.
- **A board that cannot be read never stalls anything.** The order from the last
  successful read stays in force and the queue says how old it is. If no board was ever
  read, nothing is held and nothing is reordered — an unread board grants no authority.
- **A view with its own sort is a trap, and `doctor` watches for it.** GitHub exposes one
  manual ordering per project and no per-view order, so a view sorted by `Priority` shows
  you an order this cannot reproduce. The check fires only when that sort field actually
  has values on cards in your dispatch column — the condition under which what you see and
  what runs can genuinely differ.

To name a board that is not linked, or override the column:

```toml
[repos."jantman/example"]
project = "https://github.com/users/jantman/projects/3"   # or just: 3
project_column = "Ready"
project_ordering = false                                  # or turn it off entirely
```

Turning it off restores the previous behaviour exactly: no board is contacted for that
repository, nothing is held, and the order is whatever `[dispatch] order` says.

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

## Holding specific work

```bash
robot-army hold 42                    # one item
robot-army hold --repo owner/name     # every item in a repository, present and future
robot-army unhold 42                  # and the two releases
robot-army unhold --repo owner/name
robot-army holds                      # everything held, with ages
```

Pause stops the queue. A hold stops **named work** and leaves everything else moving — which
is the case I actually hit: four items from a repository I do not care about this week sitting
in front of one I do. Holding the repository takes all four out and lets the fifth dispatch in
the same pass.

The target is stated, never guessed from its shape. An item id is an integer and a repository
key contains a slash, so one argument *could* be classified by looking at it — but a mistyped
key that happened to parse as something else would silently hold the wrong thing. Giving both
or neither is a usage error.

A held item **stays in the queue**, in the position it would occupy anyway, reported with a
`held` reason. It ranks directly below `paused`, because every reason beneath it — a full
machine, an unmerged pull request, a parked card, stale failure residue — would name a fix that
cannot work while I am the one holding it. When an item and its repository are both held, the
one reason says so, and says that releasing one leaves the other in force.

Three things it does **not** do, each on purpose:

- **It never expires.** A hold that lapsed on its own would silently start work I stopped.
- **It never stops a running session.** A hold governs entry into dispatch; `cancel` is what
  stops a session, and the two must not be confusable.
- **It never reorders anything.** Releasing puts an item back exactly where it was.

Holds are durable — they survive a restart and a reboot, and a hold placed while the daemon is
down is honoured on its first pass. They are also *runtime state*, deliberately not
configuration: `[repos.*].priority` with `order = "repo-priority"` is the standing preference I
edit in a file, and a hold is the temporary statement I make from whichever surface is to hand.

The queue view carries a repositories section listing everything with queued work **and**
everything held, each with its own hold or release control. Both halves matter. Without the
first there is nothing on the page that can *place* a repository hold; without the second, a
hold that currently matches no queued item is invisible — and a hold holding nothing looks
exactly like no hold at all, right up until it silently suppresses the next issue I file.

---

Next: [what a session is told](4-session.md).
