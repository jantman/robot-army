# Phase 0 Research: Concurrency & Polish

Decisions taken before design, each with what was rejected. Referenced as R1..R17 from `plan.md`,
`data-model.md`, and the contracts.

Milestone 004 adds no external system and no new kind of work. Almost every decision here is
therefore about *where an existing observation is turned into a policy*, and the recurring failure
mode is two places computing the same answer and drifting apart. Several decisions below exist only
to make that structurally impossible.

---

## R1 — Capacity is one computed value, produced by one function

**Decision**: add `capacity.py` exporting `snapshot(conn, *, config, registry_dir, proc_root) ->
CapacitySnapshot`. `select_and_dispatch` gates on it, and the terminal and web surfaces render the
same object. `db.count_live_sessions` stops being the dispatch gate.

**Rationale**: SC-006 requires that the item the queue names as next is the item the next dispatch
selects. That is only honestly achievable if both read one function. Today `queue_view` carries a
comment justifying its ordering by asserting what `select_and_dispatch` happens to do — a correct
comment that becomes a lie the moment either side changes, which is exactly what this milestone
changes. One producer removes the class of bug rather than the instance.

**Alternatives considered**: keep `count_live_sessions` for dispatch and add a separate out-of-band
count for display (rejected — two sources of truth for one number, and the display one would be the
one nobody tests); compute capacity in `dispatch.py` and pass it to the web layer (rejected — the
web command runs as its own process and never enters `dispatch.py`).

---

## R2 — The global count comes from the machine, not from the database

**Decision**: the global cap counts every live `claude` process running as the operating-system user,
taken from the session registry — not the count of `starting`/`running` rows.

**Rationale**: `db.count_live_sessions` counts the daemon's own bookkeeping, which is precisely the
number that is blind to the author's own sessions. It was right for milestone 001, where the daemon
was the only actor being modelled. FR-001 changes the question from "how many did I start?" to "how
many are running?", and only the registry answers that.

Classification into ours and theirs reuses `sessions.under_root(entry.cwd, worktree_root)`, which
milestone 001 already uses for the orphan sweep. No second classification rule is invented; §10's
rule is that a session's working directory decides.

**Alternatives considered**: counting `pgrep` output (forbidden outright by FR-002 and by milestone
001's FR-039 — M0 recorded two real incidents from command-line matching); asking Claude Code for a
count (no such interface, and it would be a network dependency for a local fact).

---

## R3 — The launch window: a `starting` row has no registry file yet

**Decision**: the global count is the **union by session id** of (a) live registry entries and (b) the
system's own `starting`/`running` rows that no registry entry matches. Not the maximum of the two, and
not the registry alone.

**Rationale**: this is the sharpest correctness trap in the milestone. Between `kitty @ launch`
returning and the worker writing `~/.claude/sessions/<pid>.json`, a dispatch in flight is invisible to
the registry. A registry-only count would report a free slot to a second dispatch in the same tick, and
FR-009's guarantee — that a batch cannot collectively exceed the cap — would fail in exactly the case
it exists for.

The union is exact rather than approximate because the orchestrator *generates* the session id before
the process starts (M0 finding, §3), so the join key exists on both sides from the beginning. Rows with
no registry entry are counted whether they are launching or already dead-and-unreconciled; counting a
dead one briefly errs toward withholding dispatch, and the next reconciliation pass clears it.

**Alternatives considered**: `max(registry_count, db_count)` (rejected — wrong whenever the author has
sessions running *and* a dispatch is in flight, which is the busy case); a grace period during which
`starting` rows are assumed live (rejected — a timer standing in for a fact that is already known
exactly).

---

## R4 — Degraded observation withholds dispatch, and "missing" is not "empty"

**Decision**: `sessions.scan` gains `directory_missing: bool`. Capacity falls back to
`sessions.scan_via_proc` when the scan is degraded **or** the directory is missing. Capacity is
`observable=False` — and dispatch is withheld — only when the registry is unusable *and* the `/proc`
enumeration returns zero PIDs, which cannot happen on a running machine and therefore means the
enumeration itself failed.

**Rationale**: `reconcile.scan_registry` already degrades to `/proc` on an unknown registry version, and
that path is M0-verified. The hole it does not cover is a registry directory that has *moved*: the glob
finds nothing, no version is refused, no file is unreadable, and the result is indistinguishable from
"the machine is idle". That is the one failure that reads as free capacity, so it is the one that must
be distinguished at the source. `directory_missing` is a one-line distinction at the only place that
can make it.

FR-007's rule — never dispatch on an assumption of capacity — then falls out: an under-count is the
only capacity error that causes harm, so every unresolved doubt resolves to "hold". A visible stall is a
better failure than an invisible over-dispatch, and the stall is announced through a
`capacity_unobservable` anomaly, which the partial unique index already de-duplicates.

**Alternatives considered**: assuming zero out-of-band sessions when observation fails (rejected — that
is the harmful direction); refusing to start the daemon at all (rejected — polling, reconciliation, and
the health signal all still work, and stopping them would turn a degraded observation into a total
outage).

---

## R5 — The snapshot carries handles for our sessions and a number for everything else

**Decision**: `CapacitySnapshot.ours` is a tuple of session ids; `CapacitySnapshot.others` is an `int`.
Out-of-band PIDs are counted and never carried.

**Rationale**: FR-006 forbids terminating, signalling, resuming, or attaching to a session the system
did not start. A rule like that is kept by making the handle unavailable, not by remembering not to use
it. Since no control path can obtain a PID for an out-of-band session, no control path can act on one,
and the test for FR-006 becomes a type-level observation rather than an audit of every call site.

FR-003's requirement to report the two counts separately is satisfied by the count alone; the author
does not need identities for sessions they started themselves.

**Alternatives considered**: carrying full entries for both and relying on review (rejected — Principle
I's "fewest moving parts" does not mean fewest fields, and the field is the whole guard).

---

## R6 — Per-repo cap and priority are `[repos.*]` keys; the modes get a `[dispatch]` section

**Decision**:

```toml
[dispatch]
order = "oldest-first"            # or "repo-priority"
default_repo_max_sessions = 1
[repos.foo]
max_sessions = 2                  # optional, overrides the default
priority = 10                     # optional, higher runs first; default 0
```

Cleanup and notifications get their own sections (R10, R14) rather than being crowded in here.

**Rationale**: `[repos.*]` already carries per-repository policy — base branch, environment, hooks,
permission mode, model — so a cap and a priority belong there and nowhere else. Ordering is not a
property of a repository (R7), so it cannot go there; putting it in `[daemon]` would mix policy into a
section that is otherwise entirely about timing. A new section is cheaper to read than an overloaded
one.

Both new repo keys are added to `_REPO_KEYS`, where an unknown key is already an **error** rather than a
warning, for the reason `config.py` states: a typo silently disables a step.

**Alternatives considered**: storing priority in the `repos` table (rejected — it is configuration the
author edits, not state the system discovers, and R7 needs it in memory anyway); a single
`[concurrency]` section holding everything in the milestone (rejected — cleanup is not concurrency).

---

## R7 — Ordering is a sort key applied in Python, not in SQL

**Decision**: `ordering.py` exports `order_key(item, repo, mode) -> tuple`. `oldest-first` yields
`(discovered_at, id)`; `repo-priority` yields `(-priority, discovered_at, id)`. The sort happens after
`db.list_work_items` returns.

**Rationale**: repository priority lives in TOML, not in the database. Ordering by it in SQL would
require copying configuration into a table and keeping the copy fresh — a second source of truth for a
value the author edits by hand, to sort a list that is a handful of rows long. The `ORDER BY id` in
`db.list_work_items` stays exactly as it is and becomes the stable input to the sort rather than the
policy itself.

Tie-breaking by `(discovered_at, id)` in both modes gives FR-016's "ties broken oldest-first" for free
and makes the order total, which SC-006's hundred consecutive checks require.

**Alternatives considered**: `ORDER BY` with a `CASE` built from config (rejected — generated SQL for a
ten-row sort); a `priority` column synced from config at startup (rejected — the sync is the bug).

---

## R8 — One plan function feeds both the dispatcher and the queue view

**Decision**: `ordering.plan(conn, *, config, capacity) -> list[QueueEntry]` returns every eligible item
in dispatch order, each with its position and its hold reason. `select_and_dispatch` walks that list;
`queue_view` and `robot-army status` render it.

**Rationale**: this is R1's argument applied to order rather than to counts, and it is what makes SC-006
structural. It also fixes a live comment in `web/pages.py` that asserts the view's ordering matches the
dispatcher's — true today, and false the moment repo-priority ordering exists, unless the two are the
same code.

**Alternatives considered**: a `next_item()` the view calls repeatedly (rejected — O(n) capacity
snapshots for one page, and it cannot show positions past the first).

---

## R9 — Hold reasons are an enum with a precedence order

**Decision**: `HoldReason` in precedence order: `paused`, `capacity_unobservable`, `global_cap`,
`repo_cap`, `not_onboarded`, `preparation_failed`. The first that applies is the one reported.

**Rationale**: FR-013 requires the three main reasons to be distinguishable without reading the log, and
US3's fourth scenario requires the pause to win over a capacity reason — otherwise the author frees
capacity that changes nothing. Precedence has to be explicit and single-valued, because two reasons
displayed at once is how a surface stops being read.

`capacity_unobservable` ranks above the caps because when it applies, the cap numbers are not
trustworthy and showing them would be worse than showing nothing.

**Alternatives considered**: a free-text reason (rejected — untestable, and the existing
`blocked_reason` column already shows how a free-text field drifts into inconsistent phrasings).

---

## R10 — Cleanup runs inside reconciliation, off the `done` transition, not as a new job

**Decision**: a `_cleanup_worktrees` pass in `reconcile.py`, immediately after `_resolve_closed_issues`.
Configuration is `[cleanup] on_issue_close = true` (default `false`).

**Rationale**: `_resolve_closed_issues` already does the exact observation cleanup needs — it asks
whether the issue is closed and transitions the item to `done`. Adding a daemon job would re-ask the
same question on a different clock. Principle I's "single process, obvious control flow" points at the
existing pass, and Principle II's "irreversible actions MUST NOT be reachable by default" fixes the
default.

Running as a pass rather than as a side effect of the transition also means items that reached `done`
*before* cleanup was enabled are picked up on the next pass, with no backfill command.

**Alternatives considered**: cleanup inside `states.transition()` (rejected — it would put subprocess
calls and a `git fetch` inside a `BEGIN IMMEDIATE` transaction, which Principle IV's atomicity rule and
plain sense both forbid); an age-based sweep (rejected by the spec, and time says nothing about whether
work is finished).

---

## R11 — `commits_ahead` currently returns 0 on failure, and that is unsafe for a delete decision

**Decision**: change `VersionControl.commits_ahead` to return `int | None`, where `None` means "could
not determine". `worktree.condition` maps `None` to `0`, preserving today's behaviour for the resume
signals. Cleanup treats `None` as "retain the branch".

**Rationale**: the current implementation swallows a failed `rev-list` into `return 0`. For its one
existing caller — a resume-decision signal shown to a human — zero is a harmless "no information". For
a branch-deletion decision, zero means *"every commit on this branch is already contained elsewhere,
delete it"*, so a transient git failure would authorise destroying commits that exist nowhere else.
Same value, opposite meaning, and the difference is invisible at the call site.

This is the only change this milestone makes to an existing boundary signature, and it is made rather
than worked around because a second nearly identical method would leave the trap armed for the next
caller.

**Alternatives considered**: a new `is_contained(clone, haystack, needle) -> bool | None` (rejected —
`rev-list --count A..B` computed twice under two names); catching the failure at the cleanup call site
(rejected — the failure is not observable there; `0` and `0` are the same value).

---

## R12 — The branch guard is our own containment check, and `-D` is what follows a passed check

**Decision**: before deleting, fetch the base ref, then accept the branch as safe if
`commits_ahead(clone, "<remote>/<base>", branch) == 0` (contained in the published base) **or**
`commits_ahead(clone, "<remote>/<branch>", branch) == 0` (pushed and up to date). On success, delete
with `force=True`; on anything else — including `None` — retain the branch and record the reason. The
worktree removal never passes `force`.

**Rationale**: the two halves of cleanup need different guards, and assuming otherwise is how this goes
wrong.

For the **worktree**, git's refusal is free and exactly right: `git worktree remove` refuses on a dirty
tree, including merely untracked files, and `boundaries/git.py` already never defaults `force` on. FR-025
is satisfied by keeping that as-is.

For the **branch**, git's own guard is the wrong guard. `git branch -d` accepts only a branch merged into
the current `HEAD` of the clone, or into its upstream if one is set. The normal case here is a PR merged
on GitHub while the author's clone still has a stale `main` checked out and the robot branch has no
upstream — so `-d` refuses, every time, and the `robot-army/*` branches accumulate in every repository,
which is precisely the failure planning §6 warns about. Passing `-D` blindly is the opposite error.

So `force=True` here does not mean "skip the guard". It means "a stronger guard than git's has already
passed": every commit on the branch is provably contained in a ref that lives on the remote. That
distinction is worth stating loudly because the flag reads as danger everywhere else in this codebase.

The fetch is required or the containment check reads a stale remote-tracking ref and answers the wrong
question. It reuses `VersionControl.fetch`, whose timeout and retry policy are already bounded.

**Alternatives considered**: `-d` only, accepting the accumulation (rejected — it makes cleanup fail its
own purpose in the common case); `-D` unconditionally (rejected — silently destroys unpushed work, which
is the one outcome SC-009 counts); requiring an open PR to be merged (rejected — not all work produces a
PR, and it would make cleanup depend on a second GitHub query per item).

---

## R13 — Cleanup is a property of the item, not a state of it

**Decision**: three columns on `work_items` via migration **004** — `cleanup_state`, `cleanup_reason`,
`cleaned_at`. `work_items.state` is untouched and `WORK_ITEM_TRANSITIONS` gains no entries.
`worktree_path` and `branch` are **not** nulled after removal.

**Rationale**: `done` is terminal and means the work is finished; whether its disk has been reclaimed is
a different axis, exactly as §7's central insight separates work state from session state. Adding
`cleaned` as a work-item state would make every existing query that treats `done` as terminal subtly
wrong. Keeping the path and branch after removal is what FR-024 means by the record retaining what was
removed — and it is also what makes "why is this 499 MB still here?" answerable for the retained cases.

**Alternatives considered**: a `cleanups` table (rejected — one row per item, one-shot, no lifecycle of
its own, so a join for nothing); nulling `worktree_path` on success (rejected — destroys the record of
what was removed, and `_sweep_worktrees` already keys on the path being present).

---

## R14 — Notifications are a ninth boundary, sent from four named call sites

**Decision**: add a `Notifier` protocol with `send(event: NotificationEvent) -> bool`, real and
simulated, with `REAL_AT["notifier"] = {LIVE}`. The real implementation reuses the transport
`health.notify` already has, extracted so both share one bounded-timeout POST. Configuration is
`[notifications] events = ["dispatch", "completion", "failure", "needs_info"]`, empty by default, using
`[health] webhook_url`.

Events are emitted from four explicit call sites — session confirmed, item reaching `awaiting_review` or
`done`, item reaching `failed`, card entering `needs_info` — each immediately after its transaction
closes.

**Rationale**: FR-040 requires sends to be simulated below `live`, and `effects.py` is the only place in
the package permitted to know an effect level exists. That makes a boundary the only conforming shape;
calling `health.notify` directly from a service module would put an effect-level check back at a call
site, which is the pattern milestone 001 built `effects.py` to eliminate.

Hooking `states.transition()` instead was seriously considered and rejected: it is the single gate every
state write passes through, which is attractive, but it runs inside `BEGIN IMMEDIATE`, and an HTTP POST
holding a write transaction open is a deadlock waiting for a slow webhook. Four call sites outside the
transaction, each one line and each tested, is the honest trade.

**Alternatives considered**: a durable outbound queue with its own retry and persistence (rejected — the
spec's assumptions rule it out explicitly, and it is more machinery than a stretch feature is worth); a
second webhook URL for notifications (rejected — one channel already reaches the author, and Principle I
asks what the second knob removes).

---

## R15 — Volume is bounded per cycle, with a summary rather than silence

**Decision**: at most `max_per_cycle` sends (default 5) per daemon tick. Beyond that, one summary
notification naming how many were suppressed and of which kinds. In-process, not persisted.

**Rationale**: FR-036 needs a bound that survives a backlog. Per-(kind, item) de-duplication does not
provide one, because a backlog produces *different* items — the very case that would flood. A per-cycle
cap does, and the summary keeps the fact of the suppression visible, which is the difference between a
bound and silent loss. Every send is recorded in the audit log whether or not it was suppressed, so
Principle III's reconstruction standard is met by the log rather than by the channel.

Not persisting the counter is deliberate: it exists to bound one burst, and a restart mid-burst
re-permitting a handful of messages is not a failure worth a table.

**Alternatives considered**: a token bucket with a persisted state file (rejected — durable state for a
cosmetic bound); dropping silently past the cap (rejected — Principle III forbids discarding records
silently, and the author would be left with a channel that lies by omission).

---

## R16 — A capacity hold is logged on change, not on every pass

**Decision**: `dispatch.at_capacity` is written when the hold's signature changes — the counts, the cap,
or which item is at the head — and once more when the hold ends, carrying its duration and how many
passes it spanned. The signature is held in process memory.

**Rationale**: the current code writes one record per pass at capacity. At a five-second tick that is
17,280 identical records a day, which does not make the log more reconstructible — it makes it less, by
burying the records that carry information. FR-043 asks for exactly this and requires the rule be
documented rather than improvised.

This is a change of representation, not of content: the fact of the hold, its cause, its start, its end,
and its extent are all recorded. It is therefore a documented summarisation under Principle III's
retention clause, not an exception to it. The equivalent judgement already exists in this codebase —
`raise_anomaly`'s partial unique index absorbs re-detections for the same reason.

**Alternatives considered**: sampling every Nth pass (rejected — the boundaries are where the
information is, and sampling loses them); leaving it as-is (rejected — FR-043 exists because the
milestone makes holds routine rather than rare).

---

## R17 — Contradictory configuration warns; unresolvable configuration refuses to start

**Decision**: a per-repo `max_sessions` above `daemon.max_concurrent_sessions` is a **warning**, and the
effective limit is the lower of the two. An unknown `order` value, a non-integer `priority`, a
non-integer or non-positive `max_sessions`, and an unknown key in any new section are **errors** that
prevent startup.

**Rationale**: `config.parse` already draws this line, and the existing example is a good one — the
`dispatching_max_age_seconds` cross-check warns because the author may deliberately want a short leash.
The same test applies: if the system can resolve the contradiction to a defensible behaviour, warn and
proceed; if it would have to guess what the author meant, refuse. A cap above the global cap resolves
cleanly by taking the minimum. An unrecognised ordering mode does not resolve at all — silently falling
back to oldest-first would run the author's work in an order they did not choose and did not know about.

**Alternatives considered**: making the cap contradiction an error (rejected — it is a harmless
over-specification, often a leftover from lowering the global cap); making an unknown mode a warning
(rejected — FR-014 says unresolvable values must prevent startup, and this is the case it names).
