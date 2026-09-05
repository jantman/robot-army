# Phase 0 — Research

Nine findings. Every measurement was taken on the machine while it was still in the condition #138
describes, before anything was changed. Where a number appears below it was read, not estimated.

---

## R1 — "done" already means "the issue was observed closed"

**Question**: FR-001 and FR-002 make retirement conditional on two facts — the item is `done`, and
its issue was observed closed. How is the second fact known on a later pass, or after a restart?

Three options were on the table: re-ask GitHub (an API call per candidate per pass), match the
`done` transition's recorded reason string (fragile), or add a column written by whoever makes the
transition (a migration).

**Measured**: `WorkItemState.DONE` is written in exactly one place in the whole source tree.

```
$ grep -rn "WorkItemState.DONE" src/robot_army/ | grep -v states.py
src/robot_army/reconcile.py:640:                target=WorkItemState.DONE,      # inside _resolve_closed_issues
src/robot_army/reconcile.py:984:        if item.worktree_path and item.state not in (...)   # a read
src/robot_army/cleanup.py:123:    if item.state is not WorkItemState.DONE:                 # a read
src/robot_army/db.py:346:    return _rows(conn.execute(sql, (str(WorkItemState.DONE),)), WorkItem)   # a read
```

The three legal edges into `DONE` — from `active`, `awaiting_review` and `interrupted` — all pass
through `_resolve_closed_issues`, whose only reason to act is `issue_reader.is_closed(...)` having
returned true.

**Decision**: the precondition is `item.state is DONE` and nothing else. No column, no API call, no
string matching.

**Consequence, and it must not be left implicit**: this is now a load-bearing invariant. A second
route to `DONE` added later would silently widen retirement's precondition to something the
maintainer never agreed to. So it is pinned by a test that greps the source for writers of
`WorkItemState.DONE` and asserts there is one — the same technique the codebase already uses to
keep `operations.cancel` the only place that picks a session host from stored state, and to keep
the effect level out of `reconcile.py`.

**Alternatives rejected**: a `done_via` column (a migration to record a fact the state already
carries); retiring only items that went `done` *in this same pass* (elegant, no storage, but a
worker still busy when its issue closed would then never be retired at all).

---

## R2 — The idle signal: the registry, not the transcript

**Question**: FR-003 requires "quiet" to be an observation about the worker, not an inference. What
observable answers it?

**Transcript mtime was tried first, and is wrong.** Both live sessions were measured:

| Session | Last record *inside* the transcript | File mtime | mtime ahead by |
|---|---|---|---|
| `037460ea…` (item 45) | `2026-09-05T15:45:04.297Z` | `16:14:27.623Z` | 29 min |
| `c2673ac8…` (item 54) | `2026-09-05T13:47:56.684Z` | `16:30:50.356Z` | 163 min |

The file is touched long after its last record is written, so mtime reports activity that did not
happen — in the dangerous direction, since it makes an idle session look busy and would defer
retirement indefinitely. Reading the last record itself is exact, but means seeking inside a 1–2 MB
JSONL file on every pass.

**The registry already answers it.** `~/.claude/sessions/<pid>.json`, which `sessions.scan()`
parses every pass anyway, carries both fields:

```json
{ "pid": 404232, "sessionId": "c2673ac8-…", "version": "2.1.259",
  "status": "idle", "statusUpdatedAt": 1788616076697, "updatedAt": 1788616076697 }
```

`statusUpdatedAt` of `1788616076697` is `2026-09-05T13:47:56.697Z` — **13 milliseconds** from that
session's last transcript record. For item 45 the two are 3 minutes apart, with `statusUpdatedAt`
the *earlier*, so the idle clock can start a few minutes early. Against a 1800-second threshold
that changes no decision, and it is recorded here rather than glossed.

Both finished sessions read `"status": "idle"`. `RegistryEntry` already parses `status`; only
`statusUpdatedAt` needs adding.

**Decision**: idle ⇔ `status == "idle"` **and** `now − statusUpdatedAt ≥ RETIRE_IDLE_SECONDS`.

**The comment this reverses.** `sessions.parse_entry` currently says `# status is displayed and
never used for control decisions.` That line must be rewritten, not deleted, and must say what
changed and why it is safe: the file is undocumented, so the decision is *gated on the version we
have seen* by the existing `KNOWN_VERSIONS` guard (`{(2, 1)}`; both live sessions report
`2.1.259`), and every unknown answer resolves to "do not retire". An unrecognised status, an absent
status, an absent timestamp, a missing entry, an unreadable registry: all six mean not idle. Being
wrong about the registry can therefore delay a retirement; it can never cause one.

**Alternatives rejected**: the per-session `messagingSocketPath` and the advertised `notify_idle`
peer feature — speaking an undocumented socket protocol to the worker is a far larger dependency
than reading a field from a file already being parsed. Transcript last-record parsing — exact, but
buys nothing over a field that is already there.

---

## R3 — Retirement adds no state transition

**Question**: what closes the row, and with what reason?

`reconcile.reclaim_stale_session` already answers "what is this open row?" for a row whose item is
not in `SESSION_BEARING_STATES`, and already has the three branches this needs: `left` (the item is
still running one), `reported` (the process is *alive*, so raise `orphan_session` and leave the row
open), and `reclaimed` (transition to `LOST`, slot released). It takes a `reason` string precisely
so the route is recorded.

**Decision**: retirement terminates the process, then calls `reclaim_stale_session` with a reason
naming retirement. No new transition code, no new session state, no new edge in
`SESSION_TRANSITIONS`.

The `reported` branch becomes retirement's own safety net, and it satisfies FR-007 exactly as
written: if the process survives the termination attempt, the row stays open, the slot stays held,
and the orphan anomaly is raised — "I tried and could not" is never recorded as "it is gone".

---

## R4 — Where the sweep goes in the pass

`reconcile()` runs its passes in a fixed order, each position already argued in a comment. The new
sweep goes **after `_resolve_closed_issues`** and **before `_cleanup_worktrees`**:

```
_resolve_closed_issues       # produces the `done` items
_retire_finished_sessions    # NEW
_cleanup_worktrees           # (if enabled) — now sees a closed row, not a live one
_sweep_stale_sessions        # #28 — reaches an already-closed row and returns "left"
_sweep_transcripts
_orphan_sweep
_resolve_orphan_anomalies    # NEW
```

Three halves, each load-bearing:

- **After `_resolve_closed_issues`**: that pass is what produces the `done` items this sweep acts
  on. Same pass means a merge takes effect within one tick rather than two.
- **Before `_cleanup_worktrees`**: cleanup's session guard is what records `skipped`. Retiring
  first means the worktree is reclaimed in the *same* pass, which is stronger than SC-004 asks for.
- **Before `_sweep_stale_sessions`**: this is what makes FR-009 free. #28's sweep reaches a row this
  one already closed, sees a state that is not `starting`/`running`, and returns `left`. The
  `orphan_session` that fires today is never reached — no suppression list, no special case, no
  "was this retired?" flag.

There is a second, independent reason the anomaly cannot fire: `scan` is captured once at the top
of the pass, but `RegistryEntry.alive()` re-reads `/proc` at call time. Even if the row were
somehow left open, the process is gone by then and `reclaim_stale_session` would take `reclaimed`
rather than `reported`. Belt and braces, and worth a test each.

`_resolve_orphan_anomalies` goes last among the detectors, after `_orphan_sweep`, so what it reports
describes the pass as it leaves things — the same argument `_sweep_transcripts` already carries.

---

## R5 — Terminating: an existing, confirmed path

`boundaries.dtach.SessionHost.terminate` already does everything FR-005, FR-007, FR-011 and FR-025
require, and its docstring records the incident behind each:

- logs `session.terminate` with pid, scope and expected start **before** signalling;
- refuses outright on an implausible pid, and states that zero signals were sent;
- returns `already_gone` when the pid is dead **or recycled** — `procinfo.is_alive` compares
  `/proc/<pid>/stat` field 22 against the recorded start time, so a stranger holding the number is
  never signalled;
- stops the systemd scope, then **observes** rather than trusting the exit status (issue #34:
  `systemctl --user stop` exits 0 in ~4 ms for an already-inactive unit while the process lives on),
  escalates to the process group, and confirms again;
- never returns `confirmed=True` without an observation.

**Decision**: retirement calls it, passing `expected_start=session.proc_start`. Nothing new is
built. The simulated-versus-real host discriminator is taken from the record exactly as
`operations.cancel` derives it — `dry_run and pid == 0 and proc_start is None` — which is FR-011,
and which `cancel`'s own comment explains at length is *not* the same question as `dry_run`.

**Alternative rejected**: asking the worker to exit gracefully so its wrapper writes a proper exit
record. There is no supported way to do it — it means either the undocumented messaging socket or
synthesising keystrokes into the terminal — and it would trade a confirmed mechanism for an
unconfirmed one to gain a record the daemon does not need.

---

## R6 — What ends the terminal window

The launch chain is `kitty @ launch → dtach -A <socket> → wrapper → claude`. The wrapper
deliberately does not `exec` the worker, so it is the worker's parent; when the worker exits the
wrapper returns, dtach's master exits, and kitty's window closes with the process it was hosting.
Terminating the systemd scope kills the whole cgroup, which is the same tree.

**Decision**: FR-014 needs no code. The window closing is a consequence of the process ending, and
the quickstart verifies it rather than the implementation asserting it.

---

## R7 — What happens if it is killed halfway (Principle IV)

Two windows, both real:

**A kill between the signal and the row transition.** The process is dead; the row is still open.
The next pass's `_sweep_stale_sessions` finds an open row whose item is `done` and whose process is
not alive, and takes the `reclaimed` branch. Self-healing within one tick, using code that already
exists. Nothing is lost but a minute of a slot.

**A late exit record.** The wrapper *may* still write one — unlikely, since it traps nothing and
SIGTERM ends bash outright, but it is a race and not a guarantee. Today that record would be
applied against a row already in `LOST`, `transition_session` would raise `IllegalTransition`,
`spool.drain`'s catch-all would log `spool.apply` as an error, and — because the file is unlinked
only after a successful commit — **the record would stay in the spool and be retried on every tick,
forever**.

This is a pre-existing hazard: `operations.cancel` writes `LOST` and has the same race today.
Retirement would make a rare path routine, so it is fixed here rather than inherited:
`spool.apply_record` gains a branch for an exit record whose session row is already terminal. It
records the late arrival, returns a verdict that lets the drain unlink the file, and does not
attempt the transition. That is FR-008's second half and the spec's late-exit-record edge case.

**Ordinary interruption** is covered by what already exists: every transition commits inside a
transaction with its own audit record, and reconciliation is re-entrant by construction.

---

## R8 — Anomaly resolution needs a column and an index rebuild

**Question**: FR-022 requires resolution to be distinguishable from a maintainer acknowledging by
hand. Can `acknowledged_at` be reused?

No. The two facts are different, and the CLI already exposes acknowledgement as a maintainer
action (`robot-army anomalies --acknowledge ID`, `--all`).

The subtlety is the index, which is easy to get wrong:

```sql
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''))
    WHERE acknowledged_at IS NULL;
```

That partial index is what stops a 60-second loop writing 1,440 identical rows a day. A resolved
row that stays *in* the index would block a genuinely new occurrence of the same condition from
ever being recorded again — the same trap acknowledgement already sidesteps.

**Decision**: migration 12 adds `anomalies.resolved_at TEXT`, drops the index, and recreates it with
`WHERE acknowledged_at IS NULL AND resolved_at IS NULL`. `db.list_anomalies` excludes resolved rows
under the same flag that excludes acknowledged ones, which is what makes the CLI and the web page
both correct with no change to either (`operations.anomalies`, `operations.status` and
`web/pages.py` are all callers of that one function).

**One column, not two.** No `resolved_reason`: Principle III makes the audit log the reconstruction
path, and `anomaly.resolved` carries the evidence — the kind, the entity, the pid and the start
time that no longer match. A second column would duplicate the log in a place nothing reads it from.

---

## R9 — The evidence needed to re-check an orphan is already in the row

`orphan_session` is raised in two places, and **both** already write `pid` and `proc_start` into
`detail`:

- `reconcile.reclaim_stale_session` — `pid`, `cwd`, `proc_start`, `work_item_id`, `work_item_state`
- `reconcile._orphan_sweep` — `pid`, `cwd`, `proc_start`, `session_id`

Confirmed against the live rows: anomalies 24, 25 and 26 all carry both. So re-checking is
`procinfo.is_alive(detail["pid"], detail["proc_start"])`, which answers False for a dead pid **and**
for a recycled one — FR-024 and the spec's fourth US3 scenario, for free, because identity is
already how that function works.

**Decision**: resolve only when `pid` is present in `detail` and `is_alive` is False. A row missing
`pid` is left alone forever rather than resolved on absent evidence. No row on the machine is in
that shape; the guard is there because "we could not check" must never read as "it is fine".

**Scope, held narrowly on purpose** (the clarification answer): only `orphan_session`. Not
`stale_socket`, not `prunable_worktree`, not `no_transcript` — each of those has its own settling
story, and widening the mechanism to kinds nobody asked about is exactly the speculative generality
Principle I forbids.

---

## Decisions table

| # | Decision | Rejected alternative |
|---|---|---|
| R1 | `done` is the whole precondition; pinned by a single-writer test | a `done_via` column; a second `is_closed` call; same-pass-only retirement |
| R2 | `status` + `statusUpdatedAt` from the registry | transcript mtime (measured wrong by 29 and 163 min); transcript last-record parsing; the messaging socket |
| R3 | Reuse `reclaim_stale_session` for the row transition | a bespoke transition; a new `retired` session state |
| R4 | After the closed-issue pass, before cleanup and before #28's sweep | anywhere after `_sweep_stale_sessions`, which would need a suppression flag for FR-009 |
| R5 | Reuse `SessionHost.terminate` | a graceful in-worker exit over an undocumented socket |
| R6 | The window closes because the process ends | asking kitty to close the window |
| R7 | Fix the late-exit-record path in `spool.apply_record` | inheriting a record that retries in the spool forever |
| R8 | `resolved_at` + a rebuilt partial index | reusing `acknowledged_at`; adding `resolved_reason` too |
| R9 | Re-check with `procinfo.is_alive(pid, proc_start)` from the row's own detail | re-running the whole detection; resolving rows with no pid |
