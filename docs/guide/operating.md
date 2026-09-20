# Operating it

The page to open when something is wrong and the question is *what do I type*. Tables and
recipes; the reasoning behind any of it is on the narrative page that owns that stage, linked
from each section.

- [Recipes](#recipes) · [States](#states) · [Commands](#commands) ·
  [Where things live](#where-things-live) · [Reading the logs](#reading-the-logs) ·
  [Anomalies](#anomalies) · [Is it alive](#is-it-alive) · [The web interface](#the-web-interface)

## Recipes

### I want to start this item over

Throw the work away and work the issue again from what it says now.

```bash
uv run robot-army show <id>                  # what is there: commits, PR, uncommitted work
uv run robot-army cancel <id>                # only if a session is still running
uv run robot-army reset <id>                 # discard, re-read the issue, back to the queue
```

`reset` deletes the checkout **and its branch**, re-reads the issue from GitHub, re-checks
eligibility — author included — and leaves the item `ready`. The next dispatch builds a fresh
checkout and composes the prompt from the issue as just read. It refuses if a session is still
open, and it respects git's refusal over uncommitted or untracked work; `--force` overrides
that second one and asks for the item id typed out first.

Legal from `interrupted`, `awaiting_review` and `failed`. Also on the item's web page.

**Not what you want?** `restart` reuses the existing checkout and the stored issue text;
`retry` re-reads the issue but keeps the checkout; `abandon` gives up without deleting
anything.

### The PR is done but I want to merge it later

Nothing to do and nothing to configure: robot-army never merges a pull request and never
closes an issue. A session that pushed and opened a PR has done the whole job, and the item
waits for as long as I leave it.

```bash
uv run robot-army show <id>        # the PR, the branch, anything uncommitted
uv run robot-army attach <id>      # then exit the session — the item becomes awaiting_review
uv run robot-army capacity         # the slot is back
```

Exiting the worker is what frees its session slot; the worktree, the branch, the pull request
and the issue are all left exactly as they are. `cancel <id>` does the same thing to the
process and leaves the item `interrupted` instead. Both are equally safe for the work, which
by then is on the branch and on GitHub. Nothing reclaims the checkout meanwhile — cleanup
only considers `done` items, and even then it keeps a branch whose commits are not contained
in the base.

Merge whenever. Merging closes the issue, the next reconciliation pass moves the item to
`done`, retires any worker still alive under it, and hands the checkout to cleanup if that is
turned on.

**The one thing that makes parking a PR expensive is `wait_for_merge`.** Where it is in force,
any item that has been dispatched and has not reached `done` or `abandoned` holds that whole
repository, `awaiting_review` included — `status` says so by name. There are two ways out and
no third: leave `wait_for_merge` off for that repository, or `abandon <id>`, which is terminal,
touches nothing on GitHub and releases the repository, at the price of never resuming or
resetting that item. The distinction it draws, and why it is not `max_sessions`, is in
[what runs next](3-selection.md#working-a-repository-serially).

### This item is stuck

```bash
uv run robot-army show <id>       # state, history, blockers checked now, resume signals
uv run robot-army status          # what everything else is doing
uv run robot-army capacity        # is it waiting on a cap, a hold, or a pause
uv run robot-army holds           # every hold in force
```

`show`'s `blocked` line is re-checked at the moment you ask; `failure` is the sentence
recorded when it failed and never changes. The four readings of that line are in
[what runs next](3-selection.md#why-an-item-is-blocked-checked-now-rather-than-remembered).

Then: `resume` (new session, prior context), `restart` (new session, no context), `reset`
(throw it away and start again), or `abandon` (give up, checkout left alone).

### Something is running that should not be

```bash
uv run robot-army status                       # what robot-army thinks is running
uv run robot-army capacity                     # …against what the machine says
uv run robot-army attach <id>                  # look at it
uv run robot-army cancel <id>                  # stop that session and no other
uv run robot-army pause                        # stop dispatching anything new
```

`cancel` stops one item's process tree and leaves the item `interrupted` with its checkout
untouched. It never touches another session. If `status` shows nothing but a worker is alive,
that is an `orphan_session` anomaly — see [anomalies](#anomalies).

`pause` is durable and survives a restart; `unpause` lifts it. Polling, reconciliation and the
heartbeat all continue while paused.

### The daemon looks dead

```bash
uv run robot-army health                    # exit 0 healthy, 4 for every other verdict
systemctl --user status robot-army.service
uv run robot-army log --since 30m           # what it was doing last
```

Read the verdict, not the silence — the table is at [is it alive](#is-it-alive). `DIED` means
restart it. `HUNG` means look at the process **first**: restarting destroys the evidence. If
systemd gave up, `systemctl --user reset-failed robot-army.service` before starting it again.

### The disk is full

```bash
uv run robot-army worktree list              # size, branch, condition, cleanup state
uv run robot-army cleanup                    # every eligible finished item, under its guards
uv run robot-army worktree remove <id>       # one item's checkout and branch
uv run robot-army worktree remove <path>     # a checkout no work item claims
uv run robot-army worktree prune             # clear git's record of directories already gone
uv run robot-army purge-simulated            # rehearsal rows, and optionally their worktrees
```

A prepared worktree has been measured at up to 499 MB. Automatic cleanup is off until
configured; the guards and what they protect are in
[what happens after](5-outcome.md#cleaning-up).

### I want to know what actually happened

```bash
uv run robot-army log --item <id>            # one item's whole history
uv run robot-army log --since 10m
uv run robot-army log --follow
```

## States

What an item can be, and what you can do from there. The machine itself, every column, and
what survives a reboot are on the [state page](state.md).

| State | Means | You can | Becomes |
|---|---|---|---|
| `discovered` | found by the poller, not yet evaluated | wait; `poll` to force a pass | `ready`, `failed` |
| `ready` | eligible and queued, waiting for a slot | `hold`, `unhold`, `abandon` | `dispatching`, `abandoned` |
| `dispatching` | claimed; worktree and session being prepared | wait — it settles or ages out | `active`, `failed` |
| `active` | a session is running | `cancel`, `attach` | `awaiting_review`, `interrupted`, `failed`, `done` |
| `awaiting_review` | the session ended cleanly; work is waiting on me | `resume`, `restart`, `reset`, `abandon` | `done`, `dispatching`, `ready`, `abandoned` |
| `interrupted` | the session ended without finishing | `resume`, `restart`, `reset`, `abandon` | `dispatching`, `ready`, `done`, `abandoned` |
| `failed` | something refused it, or the session exited badly | `retry`, `reset`, `abandon` | `ready`, `abandoned` |
| `done` | finished — terminal | `cleanup`, `worktree remove` | — |
| `abandoned` | given up on — terminal | `worktree remove` | — |

**`resume` needs a previous session to restore**; without one only `restart` is offered.
**Terminal is terminal**: neither `done` nor `abandoned` can be returned to the queue, and
`reset` refuses both.

**A worker never ends itself.** It does the work, opens the pull request, and then sits at its
prompt waiting for someone to type. Nothing in robot-army types into it, so the item stays
`active` for as long as that process lives — a finished PR does not move it. What the item
becomes is decided by the process's exit status, written to the spool by the wrapper and
applied when the daemon drains it:

| The worker exits with… | Session | Item |
|---|---|---|
| `0` — in practice, because I attached and exited it | `exited_clean` | `awaiting_review` |
| `1`, `126` or `127` — it never really ran | `exited_error` | `failed` |
| a signal, which is what `cancel` sends | `exited_error` | `interrupted` |

So `awaiting_review` means one specific thing: **that session exited cleanly, which is to say
I ended it.** Not that the work is finished, not that a PR exists, and not that any amount of
time has passed. If notifications are configured, this is the transition that emits
`completion`.

## Commands

Every subcommand, and whether the web interface can reach it. **Web** means there is a control
on a page; **Terminal** means a shell on this machine is required.

### Work items

| Command | Does | Reach for it when | Refuses | Where |
|---|---|---|---|---|
| `show <id>` | one item's whole history, blockers re-checked now, resume signals | anything about one item | — | Both |
| `resume <id>` | new session restoring the prior context | the session died and its work is worth continuing | no previous session; the launch gate — cap, pause, holds | Both |
| `restart <id>` | new session, no prior context, same checkout | the transcript is gone or useless | not `interrupted`/`awaiting_review`; the launch gate | Both |
| `reset <id>` | **discard the checkout and branch, re-read the issue, back to the queue** | the work went the wrong way, or the issue has changed | not `interrupted`/`awaiting_review`/`failed`; an open session; git over uncommitted work; an issue that no longer passes the poller | Both |
| `reset <id> --force` | the same, past git's refusal | the checkout holds uncommitted work you do not want | asks for the item id typed out | Terminal |
| `retry <id>` | re-read the issue, re-check eligibility, back to the queue — **checkout kept** | a `failed` item whose blocker you have fixed | not `failed`; the blocker still holding; an ineligible issue | Both |
| `cancel <id>` | stop that item's session and only that one | something is running that should not be | no running session | Both |
| `abandon <id>` | mark it abandoned; nothing is deleted | giving up on the work | an `active` item — `cancel` first | Both |
| `attach <id>` | open a terminal window on a running session | you want to watch or type | no running session | Both |
| `hold` / `unhold` | take one item or a whole repository out of dispatch | not now, but not never | — | Both |
| `holds` | every hold in force, including ones holding nothing | "why is nothing dispatching?" | — | Terminal |
| `prompt <id>` | print the prompt a dispatch would compose | before trusting a dispatch | — | Terminal |

### The machine

| Command | Does | Reach for it when | Refuses | Where |
|---|---|---|---|---|
| `run` | the daemon, in the foreground | this is the product | another daemon holds the lock | Terminal |
| `serve` | the web interface, independently of the daemon | you want it on the phone | a globally routable bind address | Terminal |
| `status` | counts and listings by state, plus anomalies | first thing, always | — | Both |
| `capacity` | how full the machine is, whose sessions those are, the order in force | "why is nothing starting?" | — | Both |
| `health` | the liveness verdict; exit 4 for anything but `ok` | the daemon looks dead | — | Both |
| `doctor` | config, binaries, sockets, permissions, disk | something is wrong and you cannot say what | — | Terminal |
| `pause` / `unpause` | suspend or resume dispatch, durably | you want the machine to yourself | — | Both |
| `poll` | force an immediate poll | you just labelled an issue | — | Both |
| `reconcile` | force a reconciliation pass | the state looks out of date | — | Both |
| `drain` | drain the exit spool now | exits are not being applied | — | Terminal |
| `log` | read the audit JSONL — the reconstruction path | "what actually happened?" | — | Both |
| `anomalies` | conditions detected; most wait for `--acknowledge` | `status` says there are some | — | Both |

### Repositories, disk, and the board

| Command | Does | Reach for it when | Refuses | Where |
|---|---|---|---|---|
| `onboard <repo>` | the deliberate per-repository trust step | adding a repository, or after a fingerprint change | an unverified origin; a changed fingerprint without `--reapprove` | Terminal |
| `repos` | onboarding, fingerprint and trust status per repository | "why is nothing happening for this repo?" | — | Both |
| `worktree list` | every checkout with size, branch and condition | the disk is full | — | Terminal |
| `worktree remove <id>` | one item's checkout **and** its branch | reclaiming disk, or settling a `prunable_worktree` | an open session; git over uncommitted work; a removal already on record | Terminal |
| `worktree remove <path>` | a checkout no work item claims | an `orphan_worktree` anomaly | outside the worktree root; claimed by a row; a live worker | Terminal |
| `worktree prune` | clear git's record of worktrees whose directories are gone | after removing one by hand | — | Terminal |
| `cleanup [<id>]` | reclaim a finished item's checkout and branch, under the same guards | the disk is full | unfinished items; an uncontained branch | Terminal |
| `purge-simulated` | remove dry-run rows, and optionally their worktrees | after a rehearsal | — | Terminal |
| `cards` | tracked intake cards, their state and their reason | the board is not producing issues | — | Both |
| `rescan` | force re-evaluation of cards awaiting clarification | you just edited a card | — | Both |
| `example-config` | print a fully commented `config.toml` | setting up, or looking for a key | — | Terminal |

**Deliberately not on the web**: onboarding and re-approval, removing a checkout or a branch,
purging rehearsal rows, changing the concurrency limit, and anything that starts or stops the
daemon. Each is irreversible, or needs a fingerprint reviewed, or belongs to the process
manager.

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

XDG variables are honoured when set. Every table, what survives a reboot, the "interrupted at
X → result on next start" table, and what to back up are on the [state page](state.md).

## Reading the logs

```bash
uv run robot-army log --since 10m
uv run robot-army log --item 42
uv run robot-army log --follow
uv run robot-army log --include-simulated      # a rehearsal's records too
```

Every outward-facing action appears **twice**: an `intent` before it and an `outcome` after,
sharing an `action_id`. An intent with no outcome is the signature of a process killed
mid-action:

```bash
jq -r 'select(.action_id) | "\(.action_id) \(.kind)"' ~/.local/state/robot-army/logs/audit-*.jsonl \
  | sort | uniq -c | awk '$1 == 1'
```

Records carry which interface produced them — `daemon`, `cli` or `web` — and the same log is
readable at `/log`, filtered, newest first, with GitHub links already made.

**A rehearsal's records are withheld unless asked for**, and the reader always says how many
it withheld, so an empty list is never mistaken for an empty history. `log --follow` is scoped
too and says no count, because a tail has no end to count against.

Record format, every action name, the redaction rules, and how to reconstruct one item's whole
history are on the [audit log page](audit-log.md).

## Anomalies

```bash
uv run robot-army anomalies                      # outstanding
uv run robot-army anomalies --since 1h           # …in a window: 30s, 10m, 2h, 1d
uv run robot-army anomalies --all                # …including resolved and acknowledged
uv run robot-army anomalies --acknowledge <id>   # I looked
```

| Kind | Means | Settle it with |
|---|---|---|
| `orphan_session` | a live worker under the worktree root that no item claims | `attach`, then `cancel`; clears itself once the process is gone |
| `prunable_worktree` | an item's recorded checkout directory is gone | `worktree remove <id>`, or `cleanup <id>` for a finished item. **Acknowledging does not settle it** — the row never changes on its own |
| `orphan_worktree` | a checkout shaped like ours that no item claims | `worktree remove <path>`; clears itself once the directory is gone or a row claims it |
| `no_transcript` | the session left nothing resumable | `restart` it, never `resume` it. `doctor` shows whether `CLAUDE_CODE_*` reached the session host |
| `registry_unobservable` | the session registry could not be read, so **nothing was torn down** | fix the cause — `doctor` shows the path — and the next pass retracts it and reaches the conclusions it declined |
| `registry_version_unknown` | the worker's registry format changed; identification fell back to `/proc` | review the version |
| `card_create_failing` | an issue could not be created from a card | fix the cause; clears itself once the card reaches `linked` |
| `board_precondition` | the board failed a startup check, so board ingestion is off | fix the board, then acknowledge. Dispatch of issues you wrote yourself is unaffected |

Four kinds clear themselves — `orphan_session`, `card_create_failing`, `registry_unobservable`
and `orphan_worktree` — because their truth can be positively re-established as false. A
resolved anomaly shows under `--all` marked `resolved` rather than `acknowledged`: one is the
system re-checking, the other is me saying I looked. Everything else waits for
`--acknowledge`, because a list that is mostly stale teaches the habit of clearing it unread.

**A rehearsal's anomalies are withheld** from every view — the list, `status`, `/anomalies`
and the header count — with a line saying how many. Anything about the machine, the filesystem
or the network is real whatever the effect level, so those stay visible always. The two ways a
row leaves the list are on the
[state page](state.md#anomalies--two-different-ways-a-row-leaves-the-list-and-whether-it-was-a-rehearsal).

## Is it alive

A process cannot report its own death, so the checker is a separate process and the **timer**
is the dead-man's switch.

```bash
cp systemd/robot-army-health.* ~/.config/systemd/user/
cp -r systemd/robot-army.service.d ~/.config/systemd/user/     # not garnish — see below
systemctl --user daemon-reload
systemctl --user enable --now robot-army-health.timer
uv run robot-army health
```

| It says | What happened | What to do |
|---|---|---|
| `ok` | the lock is held and the heartbeat is fresh | nothing |
| `DIED` | **nothing holds the lock** — whatever the heartbeat's age | restart it |
| `HUNG` | the lock is held and its holder's heartbeat has stopped | look at the process **first**; restarting destroys the evidence |
| `STARTING` | the lock is held and that process has not beaten yet | look again shortly; a long one is worth investigating |
| `NEVER STARTED` | no lock, no heartbeat | it has never run here |
| `UNREADABLE` | the heartbeat is there and will not parse | look at the file |
| `STALE` | past the threshold with no usable lock reading | as `HUNG` or `DIED`; the line says the lock could not be consulted |

**What it catches is not "the daemon crashed".** The unit carries `Restart=on-failure` with
`RestartSec=10`, so a daemon that merely dies is back ten seconds later and the check
correctly goes on saying `ok`. The switch is for the ways it stays dead: systemd giving up,
a wedged process, `graphical-session.target` going away, or nobody having started it.

`robot-army.service.d/start-limit.conf` is what makes the first of those possible to detect at
all. With systemd's defaults a daemon that *cannot* start — a config file it will not load, a
database it cannot open — is retried every ten seconds for ever and nothing ever reports it
dead, because the lock keeps being retaken. Widening the window to five minutes means five
failures inside five minutes puts the unit in `failed`, the lock stays released, and the next
timer run says `DIED`. Starting it again then needs
`systemctl --user reset-failed robot-army.service` first.

The failure nothing here covers is the machine, or the user manager, wedging: the timer does
not fire either and the only symptom is silence. The one thing that could catch it is an
outside observer, rejected deliberately — an always-on network dependency for core
observability is a worse trade than a blind spot this size.

## The web interface

A second front end onto the same operations, so an interrupted item can be decided from a
phone without opening a terminal.

```bash
uv run robot-army run &        # the daemon
uv run robot-army serve        # the interface — http://127.0.0.1:8420 by default
```

**Two processes, started by hand, in either order.** The interface is deliberately separate
from the daemon: it starts, stops and survives on its own, so the audit log and the interrupted
list stay readable during exactly the incident that makes them worth reading.

```toml
[web]
bind = "127.0.0.1"      # the LAN address, or 0.0.0.0 for every interface
port = 8420
refresh_seconds = 10    # how often an open page re-fetches itself
```

Six views — active, queue, interrupted, one item, anomalies, and the log — carrying the
controls marked **Web** in the [command tables](#commands). Every one has a terminal
equivalent, verified by a test rather than by intention. Add `.json` to any path, or send
`Accept: application/json`, for the same facts as a payload:

```bash
curl -s localhost:8420/active.json | jq '.items[] | {id, repo_key, state, title}'
```

It is not a stable API; it is versioned by the commit that produced it. Nothing is fetched
from a third-party host, so every view works with the machine offline, renders on a phone in
one column, and works with scripting disabled.

### Read this part

**There is no authentication, and that is deliberate.** The operating-system user stops being
the trust boundary the moment this binds to anything but loopback — the network becomes the
boundary instead. **Anything that can reach that port has full control of robot-army**: it can
resume sessions, cancel them, reset work, abandon it, hold and release items and repositories,
and pause dispatch.

That is the accepted model, so these are the mitigations that matter:

| Mitigation | What it does |
|---|---|
| loopback by default | widening it is an explicit edit to the config |
| globally routable addresses refused | exit `3`; it will not start where the internet can reach it |
| the address is always announced | printed at startup and written as `web.start`, loudly when it is not loopback. The one fact about this design never allowed to be silent |
| cross-site requests refused | `403` for anything a **browser** reports as coming from another site — reads included, because answering cost real work. `curl` and the address bar are allowed through: they can reach the port directly anyway, which is the model above |
| hostnames refused | `403` for any `Host` but an address or `localhost`. DNS rebinding lets an attacker control `Origin` and `Host` together, and rebinding needs a *name* |
| framing refused | `X-Frame-Options: DENY` and `frame-ancestors 'none'`. A baited click inside a frame passes the same-origin check *honestly*, so the frame is refused rather than the click |
| bounded connections | a connection silent for 15 seconds is closed, and at most 32 are served at once. Without both, an open tab costs a thread, a socket, a SQLite connection and a file handle permanently — a `503` here means "too many connections", never a failure |
| bounded renders | one reading of the machine per response, one `git` observation per item per five seconds, at most 8 MB of audit log per `/log` page. A page costs what it looks like it costs |

From outside the house: connect the VPN and use the same LAN address. Nothing is published,
no tunnel is configured, and no port is forwarded.

### What the pages say about pull requests

A session's whole purpose is to open one, so the item page names every pull request the issue
has and `/active` carries a `PR` column. Two relationships qualify — a pull request opened from
the item's branch, and one GitHub reports as linked to the issue — and both together count
once.

| On the page | Means |
|---|---|
| `#144 (merged)` | that pull request, as of the confirmation time shown beside it |
| `none` | GitHub was asked, and there is no pull request |
| `not checked` / `?` | nobody has asked — never dispatched, simulated, or finished before this existed |

`none` and `not checked` are never rendered the same way. Answering "there is no pull request"
on the strength of never having looked is the one thing this must not do. None of it costs a
request while a page renders: reconciliation establishes the answer and stores it, so these
pages render with GitHub unreachable, showing the last answer and how old it is.

### Two things the pages will tell you about themselves

**The session count is against the cap the daemon is enforcing**, not the config this process
read at startup, because `serve` reads the file once. When the two disagree every view says so,
names both numbers, and says which is in force. It is a warning, not a refusal — nothing is
disabled — and the fix is to restart whichever process has been running since before the
configuration changed. With no daemon running the page falls back to its own cap.

**A refusal is shown, never hidden.** Resume, restart and reset obey the session cap, the pause
and the holds exactly as the terminal does, and say so on the page rather than appearing to
work and quietly doing nothing. There is no `--force` button: the answer to a refusal is the
control that lifts the condition, one press away on the same page. Reset from the web
therefore cannot discard uncommitted work — that needs `reset --force` from a terminal, where
the question is answered by typing the item id.

## Walking away from a confirmation prompt

| Command | The question |
|---|---|
| `onboard` | approve this repository for dispatch, recording its fingerprint |
| `cancel` | stop this session |
| `reset` | discard this checkout and branch, and requeue the item |
| `reset --force` | type the item id, to discard the tree's uncommitted work as well |
| `worktree remove --force` | type the item id, to discard the tree's uncommitted work |
| `worktree remove <path> --force` | type the directory name, for the same reason |
| `purge-simulated` | delete these rehearsal rows; then, separately, whether to remove their worktrees |

Ctrl-C at any of them, or running one where there is no stdin — a pipeline, a cron entry,
`< /dev/null` — stops the command. **Nothing it was about to do happens**, including at a
force prompt, where the expected answer is a typed id and an absent answer is not it. Each
says which of the two it was and exits accordingly:

```console
$ robot-army purge-simulated < /dev/null
Delete 4 simulated work item(s), 0 simulated session(s) and 17 simulated card(s)? [y/N]
no answer available: input ended before the prompt was answered
  → exit=4

$ robot-army worktree remove 21 --force        # then Ctrl-C
Type the item id (21) to force-remove /w/demo/issue-21 and discard its uncommitted work:
interrupted
  → exit=1
```

Every question is asked on **stderr**, so a `--json` run that was given up on still puts one
parseable document on stdout, and every one leaves a record under the command's own action
name. The shapes are on [the audit log page](audit-log.md#the-issue-23-records).

## Where the reasoning is

| For | Read |
|---|---|
| install, tokens, effect levels | [setting it up](1-setup.md) |
| the label gate, the intake board | [what gets picked up](2-intake.md) |
| caps, ordering, holds, pause, why an item is blocked | [what runs next](3-selection.md) |
| the composed prompt, Spec Kit, preview, attach | [what a session is told](4-session.md) |
| issue comments, notifications, the session's ending, cleanup | [what happens after](5-outcome.md) |
| every state, every column, reboots, backups | [state](state.md) |
| every config key | [configuration](configuration.md) |
| the record format and every action name | [the audit log](audit-log.md) |
