# Phase 0 — Research

Nine findings. Every one was established by reading the shipped code or by arithmetic against
the timeline in issue #149; none is an assumption carried forward from the previous feature.

The headline: **this feature adds nothing.** No column, no migration, no state, no transition,
no boundary, no network call, no configuration key, no dependency. It reads a value that is
already stored, already fresh, and already parsed, and it uses that value to decide which of
two existing gates applies. Most of the research below is about confirming that each of those
"already"s is true rather than merely plausible.

---

## R1 — Is the pull request set fresh when retirement reads it?

**Yes, on both routes to `done`, and for two different reasons.**

`_refresh_pull_requests` runs first in the pass, ahead of `_resolve_closed_issues`, and the
comment at `reconcile.py:484` says the ordering is load-bearing for exactly this reason. Which
items it asks about is one SQL question — `db.list_pull_request_candidates` — with three
clauses:

| Clause | Covers |
|---|---|
| `state IN ('active','awaiting_review','interrupted')` | **The ordinary path.** On the pass where the merge is noticed the item is still `active`, so it is refreshed; `_resolve_closed_issues` moves it to `done` a few lines later; retirement reads `merged` written moments earlier by this same pass. |
| a stored pull request still `open`, **whatever the state** | **The delayed merge.** An issue closed by hand while its pull request is open leaves a `done` item that goes on being refreshed. When the maintainer merges later, the next pass records `merged` and retirement fires on that pass. |
| a stored empty set with a session still running | A worker that has not opened its pull request yet. Bounded by the session. |

So the merged signal is never stale in the direction that would delay a retirement, and the
second clause means the fix works for items that are *already* `done` when the merge happens —
which is precisely the backlog sitting on the machine right now.

**Staleness in the other direction is harmless.** If this pass's refresh failed (an unreachable
GitHub, recorded and moved on), the stored answer stands. A merged pull request never becomes
unmerged, so a stale `merged` is still true; a stale `open` merely falls back to the quiet
period. Both unknowns delay a retirement rather than cause one, which is the same asymmetry
`RegistryEntry.idle_for` is built on.

## R2 — Does reading it need any new parsing?

**No.** `WorkItem.pull_request_list` (`models.py:141`) already returns a list of dicts and
already collapses every failure mode to `[]`: a `NULL` column (never looked up), `"[]"` (looked
up, none found), text that will not parse, a payload that is not a list, and elements that are
not objects — it filters those element-wise, deliberately, because a column holding `[144]`
would otherwise raise inside a reconciliation pass and abort the whole pass.

FR-005 is therefore satisfied by the existing property with no defensive code of its own. The
predicate is one line over it: does any element's `state` equal `merged`?

`state` is normalised **at the boundary**: GitHub's enum is lower-cased there, giving
`open`, `merged` or `closed` for the three states it defines today, and anything GitHub adds
later passes through lower-cased rather than being mapped to a guess
(`test_every_state_is_lower_cased_and_an_unknown_one_is_passed_through`). So the predicate is
an exact match on `"merged"` and no case-folding is needed above the boundary — and a state
nobody has seen yet reads as *not merged*, which is the safe direction.

## R3 — What exactly changes in the gate?

Today, one condition covering two different questions:

```python
idle_s = entry.idle_for()
if idle_s is None or idle_s < RETIRE_IDLE_SECONDS:
    continue
```

`idle_for()` returning `None` means *we could not establish that this worker is idle* — the
status is not `idle`, the timestamp is missing, malformed, or in the future. `idle_s <
RETIRE_IDLE_SECONDS` means *it is idle, but not for long enough*. The two are conflated in one
`or` because today they have the same answer.

**Only the second is conditional.** The `None` branch keeps its meaning on both paths, which is
what preserves `idle_for`'s stated property — "being wrong about this registry can *delay* a
retirement; it can never cause one" — and what keeps a worker from being ended mid-tool-call.
The spec resolves this explicitly (FR-002) rather than leaving it to the diff.

## R4 — A floor on the merged path: the arithmetic rules it out

Issue #149 raises a 60-second floor as an option. Against the measured timeline it fails:

| | |
|---|---|
| 11:40:49 | worker goes idle |
| 11:41:36 | item → `done`; the pass that must retire it runs here |
| idle at that moment | **47 seconds** |

A floor of 60 seconds declines on that pass, `_sweep_stale_sessions` runs eight lines later and
raises `orphan_session` — which is the entire defect. The anomaly would be cleared on the
following pass instead of 29 minutes later, but FR-011 asks for it never to be *raised*, not
for it to be raised briefly. A floor low enough to be safe here (under 47 seconds) is not a
floor anyone would defend as a number; a floor high enough to be worth having reproduces the
bug. **Zero.**

The case the floor was meant to cover — the maintainer merging from the web interface while
still reading the session — is covered by the property retirement has had since it shipped and
which this feature does not weaken: nothing is destroyed. The transcript survives untouched and
`claude --resume <session-id>` brings the session back. The cost of being ended while reading
is a keystroke.

## R5 — What the audit record has to gain

`_retire_one` already writes `session.retire` **before** the signal, carrying `item_id`,
`session_id`, `pid`, `proc_start` and `idle_s`. With two authorising conditions, `idle_s` alone
no longer answers "why was this allowed" — an `idle_s` of 47 is either a merged pull request or
a bug, and the log should not require the reader to know which.

One key: `signal`, valued `merged_pull_request` or `quiet_period` (FR-009). `idle_s` stays and
keeps its meaning on both paths. No new audit action, no change to the settle reason's shape
beyond naming the same fact in prose.

## R6 — What follows for free

Both of the downstream effects the issue asks for are already gated on the session row being
closed, so retiring earlier moves both earlier with no change to either rule:

- **The tab.** `_close_finished_windows` (`reconcile.py:1532`) skips any `done` item for which
  `cleanup.live_sessions` is true, and runs at `reconcile.py:597` — after both retirement and
  the stale sweep. A row closed by retirement earlier in the pass is not live, so the tab is
  closed in the **same pass**. This is why #149 says fixing this fixes most of #81.
- **The worktree.** Cleanup's session guard records `skipped` for a live session, and `skipped`
  means "not yet" and is reconsidered every pass. `_cleanup_worktrees` runs at `reconcile.py:532`,
  after retirement, so the same pass reclaims it.
- **The slot.** Released by `reclaim_stale_session` closing the row, which retirement already
  calls. `capacity.py` is untouched: a live process still counts, and it is retirement that
  ends the process.

Nothing in this list needs a line of code. All three are consequences of the row closing 30
minutes earlier.

## R7 — What is killed halfway through?

Unchanged from the retirement feature, because this feature adds no write. The two windows and
their answers, restated so the plan carries them:

- **Killed between the decision and the signal.** The `session.retire` record is on disk (it is
  written first, deliberately), and nothing has changed. The next pass asks again.
- **Killed between the signal and the row transition.** A dead process under an open row, which
  `_sweep_stale_sessions` reclaims on the next pass — its existing purpose.
- **The worker ends itself in between.** `reclaim_stale_session` returns `left` and that is
  recorded as an ordinary outcome, not a failure (FR-008 of the earlier feature).

No network call is added, so no timeout or retry policy changes.

## R8 — Two pieces of shipped reasoning this change falsifies

Both must be corrected in the same commit as the code, because a comment that argues for the
old behaviour is worse than no comment once the behaviour moves (FR-012).

1. **`reconcile.py:511`** — "*Before* `_sweep_stale_sessions`, and this is what makes 'no
   anomaly for the ordinary successful path' free." The ordering is necessary but was never
   sufficient: it only helps when retirement acts in that pass, which on the ordinary path it
   could not. The corrected comment must say that the ordering is what makes the anomaly
   *unreachable once retirement acts*, and that acting on the pass the item goes `done` is what
   the merged-pull-request signal is for.
2. **`RETIRE_IDLE_SECONDS`'s comment (`reconcile.py:881`–893)** — "erring long is nearly free".
   True on the hand-closed path, where the only cost of waiting is a slot that comes back
   later. False on the merged path, where the cost is an anomaly on every successful item —
   the thing #138 was filed about. The comment must be re-scoped to the path it still describes
   and must say why the other path does not wait.

## R9 — Where the predicate lives

`WorkItem.has_merged_pull_request`, a property in `models.py` immediately after
`pull_request_list`, whose docstring carries the "never looked up reads as no" rule.

Considered and rejected: a private helper in `reconcile.py`. The question "does this item have a
merged pull request?" is a fact about the item derivable from its own column, and `models.py`
already holds exactly this shape of derived predicate — `cleanup_pending` is one, with the same
"one caller today" profile. Putting it in `reconcile.py` would separate it from the property it
reads and from the three-states-not-two docstring that explains what `[]` means.

This is not the speculative generality Principle I forbids: it is a name for a condition that
must be written down somewhere, and it removes no second implementation because there is no
second one. The alternative is not "less code", it is "the same code, further from its reason".

The decision itself — *which* of the two gates applies — stays in `reconcile.py` as a small
module-level helper returning the signal name or `None`, so the whole gate is one testable
decision table rather than a compound `if`.

## R10 — The test seam

`tests/unit/test_session_retirement.py` already has everything needed. Its `finished_item`
helper builds an item in `done` with a live, idle worker and takes `idle_ms`; the new cases add
a pull request set to that item via `db.record_pull_requests`, which is one call and needs no
GitHub double — the whole point of the signal being stored is that no boundary is involved.

The one gap worth naming: a full-pass test asserting `result.orphans == 0` already exists —
`test_the_orphan_sweep_does_not_report_a_worker_this_pass_retired` — but it builds its item
with the default `LONG_IDLE_MS`, so it exercises the quiet-period path and passes today.
**Nothing tests the freshly-idle item, which is the only shape the ordinary path produces.**
FR-011 asks for "never raised, on that pass or any later one", which only a full-pass
assertion can say, so the new case is that same test with a fresh idle time and a merged pull
request. It is the test that would have caught this bug, and its absence is exactly why the
bug shipped.

A full-pass test with a `done` item carrying a `merged` set makes no boundary call:
`list_pull_request_candidates`'s second clause looks for a stored **`open`** pull request, and
a merged one is not open, so the item is not a refresh candidate and the stored value stands.
