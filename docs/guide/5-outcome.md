# ④ What happens after

The session ends, or is confirmed running, and three things follow: a comment on the issue,
a notification if I asked for one, and — eventually, and only if I turned it on — the disk
back.

## What it writes on the issue

Two machines now run this, and a worktree path is not an address. So when a session is
confirmed, the issue is told where it is:

```markdown
🤖 robot-army dispatched a session for this issue.

- Host: `phoenix`
- Session: `ra-robot-army-38`
- Session id: `2f1c9c3e-6a54-4a0b-9f0d-4c2a1d8e77b1`
- Branch: `robot-army/issue-38-issue-comments-on-dispatch`
- Worktree: `/home/jantman/worktrees/robot-army/issue-38`
```

Both session handles are there because they are the two different things I search with: the
**name** is what appears in the tab title and the `/resume` picker, the **id** names the
transcript, the log records and the exit spool. The **branch** is the link to the pull
request — a PR for this work is opened from it — so an issue, its PR and its session logs
are one chain from either end.

Resume or restart an item and the next comment says so rather than repeating itself:

```markdown
🤖 robot-army reassigned this issue to a new session (attempt 2).
…
- Continues: `2f1c9c3e-…` (that session's context was restored)
```

A restart says `Supersedes:` instead, and says the new session starts without that
session's context — which is the difference between reading the earlier transcript for
context and reading it for facts that no longer apply. If no earlier session is on record
(a rebuilt database), it says that rather than naming one.

A failed attempt gets its own comment naming the host and the reason. Trust is granted per
machine, so "it works on the other one" is a real case and the host line is what makes it
visible.

Three rules hold throughout:

- **Nothing is posted before a session is confirmed running.** The comment is the last
  thing a dispatch does, after the check that a launch really started something — because
  `kitty @ launch` returns success either way.
- **Nothing is ever edited or deleted.** One comment per attempt, in order. That ordering
  is the record.
- **Below `live`, nothing reaches GitHub.** The body that *would* have been posted is in
  the log in full, which is how the wording gets checked without spending a real issue:

  ```bash
  uv run robot-army run --effect-level local --once
  jq -r 'select(.action == "github.comment" and .simulated == true) | .detail.body' \
    ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
  ```

A comment that fails to post is logged and otherwise ignored: GitHub being down is not a
reason to fail a session that is running. Nothing retries it, so a comment lost to a crash
between confirmation and the POST stays lost — `robot-army show <id>` still knows
everything the comment would have said.

## Being told when something happens

Off by default — nothing is sent until I ask. There are two channels, and either, both, or
neither may be configured.

```toml
[health]
webhook_url = "https://ntfy.sh/my-private-topic"   # a generic JSON POST

[pushover]                                          # a push notification on my phone
token_file    = "~/.config/robot-army/pushover-token"
user_key_file = "~/.config/robot-army/pushover-user"

[notifications]
events = ["failure", "needs_info"]  # dispatch | completion | failure | needs_info
max_per_cycle = 5
```

**Both channels get every message.** Adding Pushover does not replace the webhook, and one
channel failing never stops the other — each outcome is recorded separately, so the log can
say "the webhook took it and Pushover did not".

**Why Pushover needs its own channel rather than the webhook.** The webhook posts JSON.
ntfy accepts that; **Pushover does not** — it takes form-encoded parameters and rejects a
JSON body. `health.post_json`'s docstring claimed for two milestones that a generic webhook
covered both, and pointing `webhook_url` at Pushover produced a rejected request rather
than a notification. That is issue #106.

**Getting the two credentials.** The API token comes from an application registered at
<https://pushover.net/apps/build>; the user key is on the account dashboard. Each goes in
its own file, and each file must be mode 0600 — the same rule the GitHub and Trello
credential files follow. Both keys must be set or neither: a half-configured channel cannot
send, so it is a load error rather than a channel that silently never fires. A credential
written into `config.toml` instead of a file is also a load error, because this repository
is public.

At most `max_per_cycle` messages per daemon tick, then one summary naming how many were
held back and of which kinds. The bound is per *burst* rather than per event, because a
backlog produces different items and per-item de-duplication would not bound it at all. It
counts **messages, not deliveries**, so configuring a second channel does not halve how many
things I am told about. Every send is in the audit log whether or not it left the machine.

Messages carry identifiers and state names only. There is no field a credential could
reach, and a test asserts it across a run that includes an authentication failure.

**The stale-heartbeat alert goes to every configured channel too** — the one message that
matters most is the one saying the daemon itself has stopped, and a channel that could not
carry it would be the wrong half. Unlike the notifications above, that alert is *not* gated
by the effect level and never has been: `robot-army health --notify` takes no
`--effect-level` flag, so gating it would silently disable the dead-man's switch whenever
I am running the daemon at `local`.

## The session's ending

A worker never ends itself. It does the work, opens the pull request, and then sits at a
prompt waiting for someone to type — which is the whole point of these being *interactive*
sessions, and is also why the exit record that closes a session row never arrived.

Everything downstream of that was working exactly as designed, and the sum of it was a bug.
Merging the PR closes the issue; the next reconciliation pass moves the item to `done`; a
live worker under a `done` item is precisely what `orphan_session` reports; and the session
row is left open on purpose, because reporting fewer running sessions than exist would
oversubscribe the cap. So **the ordinary successful path ended in an anomaly and a capacity
slot held for as long as the machine stayed up.** Three successful items at
`max_concurrent_sessions = 3` were enough to stop dispatch permanently, reporting only that
the machine was full — and the worktrees could never be reclaimed either, because cleanup's
session guard kept recording `skipped`, which means "not yet". That is issue #138.

So the lifecycle has an ending now. An item that is `done` and whose worker is idle has
that worker stopped: the process ends, the row closes with a reason naming retirement, and
the slot comes back. Its terminal tab goes too, on the same pass — see [the tab](#the-tab)
below, which is a separate act and was originally, wrongly, assumed to be free.

### What says "now": the merge, or failing that the clock

**Two signals authorise it, and the merged pull request is the one that matters.** If I have
merged the PR I have said "yes, this is complete" in as many words, which is a stronger and
earlier statement than any inference from how long a process has been quiet. From that point
the worker has nothing left to do and its tab has nothing left in it I am about to read, so
it goes on the very pass the item reaches `done` — no floor, no further wait.

| The item is `done` and… | It is retired |
|---|---|
| it has a **merged** pull request | as soon as the worker is observed idle |
| it has none — the issue was closed by hand, or as not-planned | after the worker has been idle **30 minutes** |

The second row is unchanged and is the guard on the first. With no merge there is no
explicit acceptance of anything, the session may be exactly what I am about to attach to,
and idleness is the only evidence there is.

**This is a correction, and the shape of the mistake is worth keeping.** Retirement shipped
with the 30-minute clock as the *whole* gate, on the reasoning that erring long was nearly
free. That reasoning was measured against the wrong case. On the real path the worker goes
quiet, I merge within a few minutes, the issue closes, and the item goes `done` — so `done`
reliably arrives *inside* the quiet period and never after it. The gate declined every time,
`orphan_session` was raised by the sweep a few lines later, and for the next ~29 minutes
there was an anomaly on the list, a capacity slot held, a tab still open and a worktree
reported `skipped`. Of the four `session.retire` records in the log when this was found — at
idle 2477s, 11205s, 18049s and 35052s — **not one was an item that had finished normally.**
They were all backlog from before the feature existed. That is issue #149, and it is the
bounded version of the same bug #138 reported.

**No floor on the merged path, and that is arithmetic rather than taste.** A 60-second one
was considered. On the completion #149 was filed about the worker had been idle 47 seconds
when its item reached `done`, so a 60-second floor declines on exactly the pass that matters
and the anomaly is raised anyway. The case a floor would cover — merging from the web
interface while still reading the session — is already covered by the fact that retirement
destroys nothing: the transcript survives and `claude --resume` brings it back, so the cost
is a keystroke.

**What the merge does not remove is the idleness check.** A worker whose status is not
`idle`, or whose idleness cannot be established at all, is left alone however long ago
anything was merged. Merging is a statement about the *work*; whether the process is between
tool calls is a different question, and only the registry answers it. That is what keeps a
worker from being ended in the middle of a tool call, and it is why being wrong about the
registry can still only ever delay a retirement.

The audit record says which of the two fired: `session.retire` carries
`signal: merged_pull_request` or `signal: quiet_period`. With one gate the `idle_s` field
implied the reason; with two it does not, and a reader should not have to know that a low
`idle_s` means a merge.

**`done` is the whole precondition, and it means more than it looks like.** The only thing
in the system that writes that state is the pass that observed the issue closed, so `done`
*is* "the work was accepted". A test asserts that stays true, because a second route to
`done` would quietly widen which workers get stopped.

**`abandoned` and `failed` keep their workers.** Those are the states where the work is not
finished and the session may be exactly what I am about to attach to. `robot-army cancel
<id>` is the route out of those, and it now settles a terminal item's session correctly
rather than reporting an ending it did not observe.

### The tab

**The tab does not close by itself, and I assumed for one whole feature that it did.** The
retirement work above shipped with a sentence here claiming the kitty tab "closes with" the
worker, on the reasoning that the window hosts a chain — `kitty → dtach → wrapper → claude` —
that exists only to run it. The first live retirement disproved that in the most direct way
available: both workers gone, both records closed, both sockets gone, and both tabs still
sitting there. I closed them by hand.

The cause is one flag, passed on every launch since the very first milestone:

```
kitty @ launch --type=tab --hold --cwd … --title …
```

`--hold` keeps a window open **after its command exits**, so that a launch which fails
instantly leaves something readable instead of a window that vanishes before I can see it.
That window is often the only evidence of what went wrong, which is worth more than a tidy
terminal. The chain was reasoned about; the flags were not read.

So closing a tab is its own act, and it has its own rule:

> A tab is closed when its work item is `done` and **none** of its sessions is still open.

Three consequences worth knowing:

- **`failed` and `abandoned` keep their tabs, indefinitely.** That is the whole point of
  `--hold` and it survives untouched. The `done` gate is what preserves it — a failed launch
  never reaches `done`, so its window is never a candidate.
- **All of an item's tabs go, not just the last one.** A resumed or restarted item left a
  window per attempt, all marked with the same item id, and a finished item leaves none of
  them behind. The one place this narrows `--hold`: an item that failed, was retried, and
  then succeeded loses its failed attempt's tab too. The failure is still in the log and in
  the transcript.
- **It is a sweep, not a step of retirement.** Which means a tab left behind by a crash
  between the kill and the close is still cleaned up on the next pass, and so is one left by
  a version of this that predates the rule.
- **It goes on the pass the item finishes.** The rule above was right from the start and the
  tab still sat there for half an hour, because it waits on the session row and the row was
  waiting on a 30-minute clock the ordinary path never reached. Nothing in this rule changed
  to fix that: the tab moves earlier because the row closes earlier. That is issue #81,
  fixed as a consequence of #149 rather than on its own.

Which tab belongs to which item is decided by a marker the launch writes onto the window —
`ra_item=<id>` — and **never** by the window number recorded on the session row. Kitty
numbers windows per kitty process and starts again at 1 when it restarts, so a stored number
can name something else entirely a week later. A window without the marker is never touched,
whatever it contains.

**Idle is measured, not assumed.** The worker's own session registry entry carries a
`status` and the moment it last changed, and idleness is that pair and nothing else. A
status that is not exactly `idle`, an absent or malformed timestamp, a missing registry
entry, a registry that cannot be read — every one of them means *not retired*, and the
question is asked again next pass. Being wrong about that file can delay a retirement; it
cannot cause one. **A merged pull request does not change any of that**: it removes the
duration requirement, never the idleness one. Transcript file mtime was tried first and measured wrong: it ran 29 and
163 minutes ahead of the last record actually inside the file.

**Nothing is destroyed.** This is the difference between retirement and the cleanup below,
and it is why one is on by default and the other is not. The transcript survives untouched
and `claude --resume <session-id>` brings the session back, so a worker ended while I was
reading it costs a keystroke, not work. The worktree, the branch and the item's state are
not touched at all. There is no configuration key, because there is nothing to guard.

If the process survives the attempt, nothing is settled: the row stays open, the slot stays
held, and `orphan_session` is raised — "I tried and could not" is never recorded as "it is
gone".

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
uv run robot-army worktree remove <id>      # refuses if a session is live, or if dirty
uv run robot-army worktree prune
uv run robot-army cleanup                   # every eligible item, under the same guards
uv run robot-army cleanup <id>              # one item, reconsidering a retained decision
```

With `on_issue_close = true`, an item whose issue has closed has its worktree and branch
reclaimed on the next reconciliation pass — provided nothing in either exists only there.
`robot-army cleanup` runs the identical function under the identical guards whether or not
the automatic path is enabled, so the manual route cannot drift from the automatic one.

**The guards are different guards, and that is the whole design.**

- **The session**: is anything still running in there? Asked of the session rows — and of
  nothing else. Not of the work item's state, because the reachable case is a *finished*
  item: the issue closes, the item goes `done`, the worker types on, and terminal is
  exactly the state I reclaim disk from. Not of the process table either, because a row
  whose process I cannot see is still a row nothing has closed, and refusing only on a
  *confirmed* live process would remove the worktree in every case where liveness could not
  be established. `worktree remove` gained this guard in #79, after it deleted a running
  worker's directory and its branch in one command and reported success; `cleanup` had it
  from the start. That was the wrong way round — `cleanup` runs unattended and is
  conservative by design, while `worktree remove` is what I reach for when `/home` is at
  93%, and it is the one that can override git. `--force` still overrides it, and says so
  in the prompt before I type anything.
- **The worktree**: git's own refusal, taken as-is. `git worktree remove` refuses on a dirty
  tree — *including merely untracked files* — and `--force` is never passed. A refused
  worktree is recorded as `retained` with git's own message, and the branch half is not
  attempted, because a dirty worktree means the branch may hold the only copy of something.
- **The branch**: my own containment check, because git's is the wrong one here. `git branch
  -d` accepts only a branch merged into the clone's current `HEAD`; the normal case is a PR
  merged on GitHub while my clone has a stale `main` checked out, so `-d` would refuse every
  time and `robot-army/*` branches would accumulate forever. Instead the branch is deleted
  only if every commit on it is provably on the remote — contained in the published base,
  which is fetched first, or on the remote under its own name, which the **remote is asked
  about during the check**. A remote-tracking ref is not taken as the remote's answer: it is
  a cache of what the remote said last time, a fetch scoped to the base branch neither
  refreshes nor prunes it, and #105 measured a branch deleted on the remote going on proving
  itself "pushed and up to date" from that leftover until a `gc` on the remote made the loss
  permanent. If the remote cannot be asked, or git cannot answer, that is "unproven", never
  "safe", and the branch is kept.

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

---

Next: [operating it](operating.md).
