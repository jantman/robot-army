# Operating it

The web interface, where everything lives, how to read the logs, and what to do when
something looks wrong.

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
resume sessions, cancel them, abandon work, hold and release items and repositories, and
pause dispatch.

That is the accepted model, so the mitigations are the ones that matter:

- The default is loopback. Widening it is an explicit edit to the config.
- A **globally routable** bind address is refused outright, exit `3`. The interface will not
  start somewhere the internet can reach it.
- The effective address is printed at startup and written to the audit log as `web.start`, on
  every start, with a loud warning when it is not loopback. That is the one fact about this
  design that is never allowed to be silent.
- **Any** request that a **browser** reports as coming from another site is refused with
  `403` — reads as well as state changes. This is the one attack the model above does not
  already accept: it needs no network path to the port at all, only my own browser — already
  inside the trust boundary — having some unrelated page open while the interface is running.
  It is not authentication; it identifies nobody, holds no state, and asks one question.
  Clients that send neither `Origin` nor `Sec-Fetch-Site`, `curl` included, are allowed
  through: they can reach the port directly anyway, which is the model above. So is
  `Sec-Fetch-Site: none`, which is what a browser sends for the address bar and for a
  bookmark — the two ways I actually open it.

  Reads are checked *before* anything is done for them, which is the point: the response to
  `fetch(..., {mode:'no-cors'})` is opaque to the page that sent it, so the attack was never
  the reading. It was that answering cost this machine a `git` fork per card on
  `/interrupted`, a whole audit file per `/log`, and a `/proc` walk per page. A refused read
  now costs a string. A refused *write* still writes its audit pair first, because that pair
  is the only way a forged action would ever be noticed; a refused read does not, because
  writing one would open the SQLite connection and audit handle the refusal exists to avoid
  — so the count for the run goes into `web.stop` as `refused_cross_site` instead.
- **Reach it by address, not by name.** Any request whose `Host` is a hostname other than
  `localhost` is refused with `403`. Comparing `Origin` to `Host` is not enough on its own,
  because DNS rebinding lets an attacker control both: point `evil.test` at `127.0.0.1`, get
  my browser to load `http://evil.test:8420`, and every header agrees with every other while
  the request really lands here. Rebinding needs a *name*, so requiring an address closes it
  — and `[web] bind` already has to be an address for the same reason.
- **No page of it may be put in a frame.** Every response sends `X-Frame-Options: DENY` and a
  `Content-Security-Policy` beginning `frame-ancestors 'none'`, because framing walks straight
  past the same-origin check above: the form a baited click submits belongs to the framed page
  itself, so the browser reports `Sec-Fetch-Site: same-origin` and a matching `Origin`, and the
  check passes *honestly*. Nothing about the request distinguishes it, so the frame is refused
  rather than the click. The same policy adds `default-src 'self'`, `base-uri 'none'` and
  `form-action 'self'` — free here, because these pages load nothing external by design: no web
  font, no CDN, no icon set, no inline script or style. Every response also sends
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`, the latter so that
  following a `github.com` or `trello.com` link out of a view does not hand it this
  interface's address — `same-origin` and not `no-referrer`, because a refused control's
  page builds its "back to" link from the `Referer` of my own POST.
- **A connection cannot be held, and there cannot be many of them.** A connection that says
  nothing for 15 seconds is closed, and at most 32 are served at once; the 33rd is hung up on,
  with a `503` and `Connection: close` sent first where the socket takes it — delivery is
  best-effort on purpose, because the refusal is written from the accept loop and blocking
  there to satisfy a client that is not reading would be the denial of service itself.
  Both numbers are constants in
  `src/robot_army/web/server.py`, not config. This is not a rate limit and not about the
  network — a page in a browser tab I already have open can connect here and stay silent, and
  without the two bounds each of those connections costs a thread, a socket, a SQLite
  connection and an audit file handle, permanently. Descriptors run out long before memory
  does, and when they do the interface stops rendering at exactly the moment it is worth
  having. So a `503` from this interface means "too many connections", never a failure; the
  number of connections a run turned away is in that run's `web.stop` audit record, beside
  `refused_cross_site`, and reaching the cap prints one line to stderr per episode.
- **A page render is bounded work.** One reading of the machine per response, not one per
  section — `/queue` used to take two and could report two different counts of what is
  running on one page. One `git` observation per item per five seconds, with the age shown on
  the card, so a reused answer says so. At most 8 MB of audit log read per `/log` page, in
  64 KB blocks from the end of each daily file rather than whole files into memory; when a
  request stops at that ceiling the page says so and "older records" continues from where it
  stopped, so an empty page is never an empty history. All three numbers are constants in
  `src/robot_army/operations.py` and `src/robot_army/web/server.py`, not config. None of this
  is a rate limit — it is the difference between a page costing what it looks like it costs
  and costing whatever the caller asks for.

From outside the house I connect my existing VPN and use the same LAN address. Nothing is
published, no tunnel is configured, and no port is forwarded.

### What it can do

Six views — active, queue, interrupted, one item, anomalies, and the audit log — and the
controls for the decisions I actually make away from the desk: resume, restart, abandon,
cancel, retry, attach a terminal, acknowledge an anomaly, hold and release an item or a whole
repository, pause and resume dispatch, and force a poll or a reconciliation. Every one of
them has a terminal equivalent, verified by a test rather than by intention.

Resume and restart here obey the session cap, the pause and holds exactly as the terminal
does, and say so on the page rather than appearing to work and then quietly doing nothing.
There is no `--force` button: the answer to a refusal is the control that lifts the
condition, which is one press away on the same page and leaves the queue agreeing with the
button instead of overridden by it.

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

XDG variables are honoured when set. **Full detail — every table, what survives a reboot,
the "interrupted at X → result on next start" table, and what to back up — is on the
[state page](state.md).**

## Reading the logs

```bash
uv run robot-army log --since 10m
uv run robot-army log --item 42
uv run robot-army log --follow
```

Every outward-facing action appears **twice**: an `intent` record before it and an
`outcome` record after, sharing an `action_id`. An intent with no outcome is the signature
of a process killed mid-action:

```bash
jq -r 'select(.action_id) | "\(.action_id) \(.kind)"' ~/.local/state/robot-army/logs/audit-*.jsonl \
  | sort | uniq -c | awk '$1 == 1'
```

Records carry which interface produced them — `daemon`, `cli`, or `web` — and the same log is
readable from the browser at `/log`, filtered, newest first, with GitHub links already made.

**Record format, every action name, the redaction rules, and how to reconstruct one item's
whole history are on the [audit log page](audit-log.md).**

## When something looks wrong

```bash
uv run robot-army status               # counts, listings, outstanding anomalies
uv run robot-army show <item-id>       # one item's whole history and resume signals
uv run robot-army anomalies            # things detected but not resolvable
uv run robot-army anomalies --since 1h # …narrowed to a window: 30s, 10m, 2h, 1d
uv run robot-army repos                # why is nothing happening for this repo
uv run robot-army doctor               # environment and preconditions
```

**An `orphan_session` whose process is gone now clears itself.** It is the only kind that
does. Every other kind waits for `--acknowledge`, because `orphan_session` is the only one
whose truth can be positively re-established as *false* — the pid and start time it recorded
no longer name a live process. A resolved anomaly leaves the default listing and shows under
`--all` marked `resolved` rather than `acknowledged`, which are different facts: one is the
system re-checking, the other is me saying I looked. This exists because the list is read as
*things needing attention*, and a list that is mostly stale teaches the habit of clearing it
unread — which is how the one that mattered gets dismissed with the noise.

Anomalies worth understanding rather than dismissing:

- **`orphan_session`** — a live worker under the worktree root that no item claims.
  `interrupted` does *not* mean nothing is running: if the wrapper dies uncleanly the
  worker keeps going, reparented, while dtach tears down its socket.

  **It no longer fires on the ordinary successful path.** It used to, for every item:
  merging a PR closed the issue, the item went `done`, and the worker sat at its prompt
  forever. Retirement (see [what happens after](5-outcome.md#the-sessions-ending)) ends that
  worker before this sweep sees it. Seeing this anomaly for a `done` item now means
  retirement *tried and could not* — the process survived the termination, so the row stays
  open and the slot stays honestly subscribed.
- **`no_transcript`** — the session ran and left nothing resumable. Raised by
  reconciliation five minutes after the session was confirmed, not at dispatch: the worker
  writes its transcript when it starts processing, so asking any earlier reports every
  healthy session. Two causes, and the check cannot tell them apart — the worker never
  saved one (`robot-army doctor` shows whether `CLAUDE_CODE_*` is set in the session host's
  environment), or the session died before writing one (its exit record shows that).
  Either way that session cannot be resumed: `restart` it, do not `resume` it. Raised at
  most once per session.
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

**`resume` and `restart` pass the same gate the dispatcher does**, and did not until issue
#120. If the machine is at `max_concurrent_sessions`, if the repository is at its own limit,
if dispatch is paused, or if the item or its repository is held, they refuse — exit `3`, the
reason on stderr in the same words the queue uses, and the item untouched. Lift the
condition and press again; there is nothing to repair in between. To go past it anyway:

```bash
uv run robot-army resume <id> --force   # past the cap, the pause, and the holds
```

`--force` covers my own policy and nothing else. It cannot bypass the issue author check,
workspace trust, the committed settings fingerprint, onboarding, or the state machine, and
it has no configuration equivalent. Every condition it goes past is named in the log as
`dispatch.forced` — written only when something actually applied, so forcing an already
dispatchable item overrides nothing and records nothing. Note that this is a different
`--force` from `cancel --force`, which only skips a confirmation prompt.

The claim on an item is atomic, so a tap on my phone and a terminal command arriving in the
same second cannot both start a session: one wins, the other is told the item was claimed by
another dispatcher. One worktree, one branch, one agent.

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
