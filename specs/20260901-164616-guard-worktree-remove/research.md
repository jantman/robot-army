# Phase 0 Research: Refuse to Remove a Worktree While Its Session Is Open

**Feature**: `specs/20260901-164616-guard-worktree-remove` | **Date**: 2026-09-01

Every finding below was read out of the tree at `3c53217`, not recalled. Line numbers are from
that commit.

---

## R1 — The report's suggested fix would not have fixed the reported case

Issue #79 says "`reconcile.SESSION_BEARING_STATES` and `db.latest_session_for_item` already
provide everything needed". Both are the wrong tool, and each fails differently:

- **`SESSION_BEARING_STATES` is a set of *work item* states, not session states.**
  `reconcile.py:142` defines it as `frozenset({WorkItemState.DISPATCHING, WorkItemState.ACTIVE})`.
  Its own comment says it exists to answer "does this item legitimately have a session in flight".
  A guard written against it would ask about `item.state` — and in the reported incident the item
  was **`done`**, which is not in the set. The guard would have permitted the removal that the
  issue was filed about. This is why **FR-003 forbids the guard from consulting item state at
  all**.
- **`latest_session_for_item` sees one row.** `db.py:381` is `ORDER BY attempt DESC LIMIT 1`. An
  item that was restarted leaves an earlier attempt's row behind, and `reconcile`'s own docstring
  (`reconcile.py:170-176`) records that a superseded attempt's worker keeps running, reparented —
  that is exactly what the `orphan_session` anomaly is for. Asking only the newest row would miss
  a live worker of an older one. **FR-002.**

**Decision**: use `db.list_sessions_for_item` (`db.py:372`, `ORDER BY attempt`, every row) filtered
on session state, and ignore the work item's state entirely.

**Alternatives considered**: `latest_session_for_item` (misses superseded attempts);
`item.state in SESSION_BEARING_STATES` (misses the reported case outright).

---

## R2 — The definition of "live" already exists, in `cleanup.eligible`

`cleanup.py:80-86`:

```python
live = [
    session
    for session in db.list_sessions_for_item(conn, item.id)
    if session.state in (SessionState.STARTING, SessionState.RUNNING)
]
if live:
    return False, f"session {live[0].session_id} is still live"
```

This is precisely the question the manual path needs to ask, already asked correctly. FR-014 says
the two paths must share it rather than restate it.

**Decision**: lift the state set to a module constant `LIVE_SESSION_STATES` and the query to
`cleanup.live_sessions(conn, item_id) -> list[Session]`, in `cleanup.py`. `eligible` calls it;
`operations.worktree_remove` calls it.

**Why `cleanup.py` and not `db.py`**: `db.py` holds queries, and this is a *policy* — "what counts
as still running, for the purpose of refusing to delete things". `cleanup.py` already owns that
policy and the contract that documents it (`specs/004-concurrency-polish/contracts/cleanup.md:12`).
Import direction is safe: `cleanup` imports `db`, `repos`, `models`, `states` and nothing from
`operations`; `operations.py:45` already does `from robot_army import cleanup as cleanup_mod`.

**Alternatives considered**: a new `liveness.py` module (a third home for one predicate — Principle
I says no); duplicating the two-state tuple in `operations.py` (the drift FR-014 exists to prevent);
`db.live_sessions_for_item` (puts a policy decision in the query layer, and `db.py` has no other
opinion of that kind).

---

## R3 — The refactor has one fragile coupling that must not be disturbed

`cleanup.py:106` decides whether a refusal was the live-session refusal by **substring match on the
reason it just produced**:

```python
live_session = "still live" in reason
```

That is what routes the item to `SKIPPED` (reconsidered later) rather than to plain ineligibility.
Changing the wording of `cleanup.py:86` silently converts every live-session skip into an
un-reconsidered non-decision.

**Decision**: `eligible`'s returned string stays byte-for-byte `f"session {…} is still live"`. The
extraction changes how the list is *computed*, never what the sentence says. A regression test
asserts `cleanup` still records `skipped` for a live session.

Repairing the substring coupling itself is **out of scope**; it is pre-existing, and this feature
must not quietly change how the automatic path decides things (FR-013).

---

## R4 — A refusal today would leave no record at all

`operations.worktree_remove` (`operations.py:1449`) writes **no audit record of its own**. The only
records the command produces come from the boundary it calls: `git.remove_worktree` and
`git.delete_branch` (`boundaries/git.py:115`, `:133`). A guard that refuses before reaching git
therefore logs nothing, which FR-010 forbids and Principle III forbids independently.

Worse, the existing records never name the **work item**: `git.remove_worktree` carries
`target=<path>` and `detail={force, cwd}`. A reader asking "what happened when I removed item 21's
worktree" has to map a path back to an item by hand.

**Decision**: wrap the whole operation in `ctx.audit.action("worktree.remove", entity_type=
"work_item", entity_id=item_id, target=item.worktree_path, detail={"force": force})`
(`audit.py:206`). The yielded dict carries what was decided. This gives:

- an **intent flushed before** the destructive git call, which the Operating Constraints require of
  an irreversible action, and which today is satisfied only incidentally by the git boundary;
- one place where both the refusal and the override are recorded, in the shape `session.terminate`
  already uses for the same problem;
- the work item id on the record, closing a pre-existing gap at zero cost.

`worktree.*` is an existing namespace (`worktree.prepare`, `worktree.py`), so no new vocabulary is
invented.

**Alternatives considered**: a standalone event record on the refusal only (leaves the override —
the most destructive thing this command can do — recorded as an ordinary `force: true`, which is
also what a forced dirty-tree removal looks like, so FR-011 would be unmet); extending
`git.remove_worktree`'s detail (the boundary must not learn what a session is).

---

## R5 — A refusal is `outcome: "ok"`, not `outcome: "error"`

`docs/logging.md:63` fixes the vocabulary: `outcome` is `ok`, `error`, or `pending`. The precedent
for a guard firing is `cleanup.considered`, which records `outcome="ok"` with
`detail={"decision": "skipped", "reason": …}` (`cleanup.py:108-116`).

**Decision**: a refusal is `outcome="ok"` with `refused: true` and `refused_by: "live_session"` in
the detail. Nothing failed — the command was asked a question and answered it. `error` stays for a
boundary that broke, and `audit.action`'s exception branch (`audit.py:243`) keeps writing that on
its own.

---

## R6 — Liveness may inform the message; it must not gate the refusal

`procinfo.is_alive` (`procinfo.py:111-122`) degrades when `expected_start` is `None`:

```python
if actual is None:
    return False
if expected_start is None:
    return True
```

A recorded pid with no recorded start time therefore reads as *alive* whenever any process holds
that number — the exact degradation that let pid `1` through the termination guard three days ago
(`specs/20260831-184927-guard-terminate-pid/research.md` R6). And `sessions.py` treats `procStart`
as optional, so a real session row can legitimately carry a pid and no start time.

If liveness gated the refusal, every case where liveness cannot be established would fall through
to removal — which is the reported bug, only harder to reproduce.

**Decision**: the **session row** refuses. Liveness is reported to the operator as one of four
honest answers and never consulted for the decision:

| Recorded | Reported as |
|---|---|
| `pid` and `proc_start`, `is_alive` true | `pid N is running` |
| `pid` and `proc_start`, `is_alive` false | `pid N is no longer there` |
| `pid`, no `proc_start` | `pid N recorded, with no start time to identify it by` |
| no `pid` | `no process id recorded` |

`is_alive` is **never called without a `proc_start`**, so the degraded branch is unreachable from
here.

**Alternatives considered**: refuse only when a live process is confirmed (rejected above); scan the
session registry via `sessions.RegistryScan` (`sessions.py:82`) instead of `/proc` — more machinery
for a message, and the registry can be degraded, which would add a third kind of "don't know").

---

## R7 — Ordering: the guard must precede the confirmation prompt

`operations.py:1470-1477` prompts for the typed item id *before* removal, and the prompt says only
that uncommitted work will be discarded. FR-009 requires it to name the live session, so the guard
must be evaluated **before** the prompt is constructed, not before the removal.

**Decision**: single evaluation at the top, then three paths — live and not forced → refuse with no
prompt (FR-004: "the refusal is not a question"); live and forced → prompt naming the session; not
live → today's prompt, unchanged.

---

## R8 — Exit code: `EXIT_PRECONDITION`, and that is already distinguishing

`operations.py:69-72` gives `EXIT_OK=0`, `EXIT_FAILED=1`, `EXIT_USAGE=2`, `EXIT_PRECONDITION=3`.
Within this same function, git's refusal returns `EXIT_FAILED` and an unresolvable repository
returns `EXIT_PRECONDITION`. `cli.py:313-317` returns `result.code` unchanged and prints non-`OK`
results to stderr.

**Decision**: `EXIT_PRECONDITION`. A live session is a precondition that is not met, it matches the
sibling refusal in the same function, and it satisfies part of FR-006 for free — a script can tell
the two refusals apart from the exit status alone.

---

## R9 — The machine-readable output needs a discriminator, not a second reason field

`result.data["refused_reason"]` currently holds *git's* message (`operations.py:1491`). Adding a
second reason field would leave a reader guessing which one applies.

**Decision**: keep `refused_reason` as "why this command refused", filled by whichever guard
refused, and add `refused_by ∈ {"live_session", "git"}`, absent when nothing refused. Add
`live_session` — a small object naming the session, attempt, state, pid, liveness answer and
socket — present whenever an open session was found, whether it refused or was overridden, and
`forced_over_live_session: bool`.

No existing consumer breaks: `cli.py:466` is the **only** caller of `worktree_remove` in the tree
(no web route — `grep` over `src/robot_army/web/` finds no removal path), and `--json` renders
`data` verbatim.

---

## R10 — What the reattach line must be

`operations.py:717`, in `show`:

```python
if session.host_socket:
    result.say(f"       reattach: dtach -a {session.host_socket}")
```

FR-005 asks for "the same reattach line the item's detail view prints". It is conditional on
`host_socket` there and must be conditional here too — a simulated or partially-registered session
has none.

---

## R11 — Interruption analysis

- **Killed during the guard**: the intent is flushed; nothing else is written; nothing is removed.
  An intent with no outcome is the crash signature Principle IV asks for. The item is untouched, so
  every sweep that visited it still visits it.
- **Killed at the confirmation prompt** (forced path): same. `audit.action`'s `BaseException`
  branch (`audit.py:243`) records the `KeyboardInterrupt` as an `error` outcome and re-raises, so
  even an abandoned prompt is reconstructible.
- **Killed between the two removals** (forced path): unchanged from today, and already documented
  in `docs/state.md:422`.

This feature adds **no new interruption window**, because the path it adds writes nothing.

---

## R12 — Test doubles: proving nothing was removed

`boundaries/git.py:276` `SimulatedVersionControl` logs every intended operation to the audit log
and returns `RemovalResult(worktree_removed=True, …)` without touching disk. A unit test can
therefore assert the strongest available form of "nothing was removed": **no `git.remove_worktree`
record exists in the audit log at all**, rather than merely that a directory survived.

`tests/conftest.py:1319` `seed_session` inserts a row directly with `state`, `pid`, `host_socket`
and `dry_run`, and `db.next_attempt` gives it a real attempt number — so the multi-attempt case of
FR-002 is two calls, not a fixture.

`seed_session` does **not** write `proc_start` (it updates only `state`, `pid`, `exit_code`,
`signal`), which makes the third row of R6's table the default in unit tests and the first row
reachable by a direct `UPDATE`. Both are worth covering.

**Decision**: unit tests in a new `tests/unit/test_worktree_remove_guard.py` for every refusal,
message, prompt, record and payload assertion; one integration test added to the existing
`tests/integration/test_worktree_removal.py` (already `requires_git`) proving the real directory
and the real branch survive the real command; one regression test in `tests/unit/test_cleanup.py`
for R3.
