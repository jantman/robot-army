# Contract: closing a finished item's windows

Normative. Where this and the prose in `plan.md` differ, this wins.

## W1 — When it runs

| | |
|---|---|
| Caller | `reconcile.reconcile()`, once per pass, as `_close_finished_windows` |
| Position | after `_sweep_sockets`, with the other physical-residue sweeps |
| Population | windows carrying the `ra_item` user variable — **only** when the candidate set is non-empty |
| Bound | one `kitty @ ls` per pass at most; zero when nothing qualifies |

The position is after `_retire_finished_sessions` and after `_sweep_stale_sessions`, and both
matter: a session retired earlier in this pass has already had its row closed, so its item qualifies
immediately and its window goes in the **same** pass rather than the next.

## W2 — The candidate set, built from the database first

```
candidates = {
    item.id: item.done_at
    for item in work items where state == DONE
    if item has at least one session row
    and (item.id, item.done_at) not in _WINDOWS_SETTLED
    and cleanup.live_sessions(conn, item.id) == []
}
if not candidates:
    return 0          # the terminal is never touched
```

**The fourth condition was missing and the gate did not work without it** (found in review of
PR #141). `done` is terminal and rows are never deleted, so an item that has completed satisfies
the three database conditions on *every future pass forever* — long after its window went. The
candidate set would never empty again, so `if not candidates` fired exactly once in the life of an
installation: before the first item ever finished. Everything this gate was built to prevent —
a `kitty @ ls` per pass, ~1,440 listing failures a day on a machine with no kitty, a failure record
whose `candidates` grew without bound — would have happened anyway, while the docstring and this
contract both claimed otherwise.

`_WINDOWS_SETTLED` holds the items this *process* has answered for: closed, or looked for and found
none. Both are final, because `done` has no outgoing transition and so no dispatch can ever open a
settled item another window.

It is keyed on `(id, done_at)` rather than the id alone: SQLite reuses a freed `INTEGER PRIMARY
KEY`, and `purge_simulated` frees them, so a later item could otherwise inherit a settled id and
never have its window closed.

It lives in process memory rather than a column, following the precedent milestone 004 set for the
capacity hold and the notifier's cycle counter — losing it costs exactly **one** extra listing after
a restart, which is far less than a table costs to keep correct.

Four conditions, each carrying a requirement:

| Condition | Requirement | Why |
|---|---|---|
| `state == DONE` | FR-003 | `failed` and `abandoned` keep their windows indefinitely. This is also what preserves `--hold`'s purpose (FR-017) without a second rule |
| at least one session row | spec edge case | A `done` item that never had a session — a rebuilt database — offers no evidence its session ended. `live_sessions` returns `[]` for "all finished" *and* for "never had any", and only the first qualifies |
| `live_sessions(...) == []` | FR-004 | The shared definition from issue #79, reused rather than re-derived (R5). A worker that survived a termination attempt keeps a `running` row, and so keeps its windows |
| not already settled | FR-010's cost half | Without it the gate is dead after the first completion (above). Added in review of PR #141 |

The early return is not an optimisation. It is what keeps the terminal error path rare enough to be
meaningful (R6): a failure to list windows now means "there was work to do and the terminal could
not be reached", not "this machine has no kitty".

## W3 — Identity

For each handle returned by `display.list_by_var("ra_item")`:

| # | Condition | Outcome |
|---|---|---|
| 1 | `user_vars["ra_item"]` is absent or not an integer | LEAVE |
| 2 | that item id is not in `candidates` | LEAVE |
| 3 | otherwise | **CLOSE** (W4) |

Rule 1 covers FR-008 and FR-009 together: a window without the marker is not ours, and a marker that
does not name a resolvable item is not evidence. Rule 2 covers FR-003 and FR-004 — anything not in
the candidate set is left alone, whatever its state, with no second opinion sought.

**The stored `sessions.window_id` is not consulted anywhere in this contract** (FR-007). Kitty
renumbers windows from 1 when it restarts, so a stored number can name a stranger's window. The
`window_id` used to close is the one the live listing just reported for a window that carries our
marker.

## W4 — Closing

`display.close(handle)`, which already logs intent and outcome through `audit.action`
(`kitty.close_window`). It returns **whether a window was really closed**.

*That return value was added in review of PR #141.* This table promised "not counted" for a window
that had already gone, while the code counted every call that did not raise and the matching test
asserted the count — the contract stated a guarantee the implementation did not provide. The review
supposed it *could* not be provided, on the grounds that `kitty @ close-window` exits 0 for an id
that no longer matches. Measured: it exits **1**, with `No matching windows for expression: id:N`.
`_kitty` passes `check=False`, so the answer was in hand and being discarded. Returning it costs no
extra call to the terminal, so the row below is now true as originally written.

| Outcome | Effect |
|---|---|
| closed | counted in `windows_closed` |
| the window had already gone | **success**, not a failure (FR-014), and the item is still settled — there is nothing left to do. **Not counted**: this pass did not close it, and a `windows_closed` including windows somebody else closed would overstate the system's own work |
| the close failed | recorded as `window.close` with the window id and item id; **the sweep continues to the next window** (FR-013); not counted; **its item is not settled**, so it is retried next pass |
| the listing itself failed | recorded once for the pass; the sweep returns 0; the pass completes normally; **nothing is settled**, because the question was not answered |

A `BoundaryError` from either the listing or a close is caught. Reconciliation never raises for an
operational condition.

**Every candidate the listing answered for is settled**, including the ones that turned out to have
no window at all — that *is* the answer, and it cannot change. Only a candidate whose close failed
is held back.

## W5 — Counter

`ReconcileResult` gains `windows_closed: int`, surfaced by `summary()` and therefore by the existing
`reconcile.pass` record and `robot-army reconcile` (FR-015).

## W6 — What a non-closure writes

**Nothing**, for a window that simply does not qualify — the ordinary case, and the documented
Principle III gap. A pass runs every 60 seconds and a working machine has several such windows
permanently; recording each would write thousands of records a day carrying no news, and the
condition is re-derivable from the item's state at any instant. Precedent: `_sweep_transcripts` and
`_retire_finished_sessions` both write nothing for a deferral.

The exceptions — a close that failed, and a listing that failed — **are** recorded, because those
are the cases where the system tried to act and could not.

## W7 — Effect levels

Handled entirely by the existing wiring and **not** by any check in this code. `effects.REAL_AT`
makes `display` real only at `no-remote` and `live`; below that `SimulatedDisplay` is wired in and
its `close()` records `simulated=True` without touching a terminal. FR-016 therefore needs no
branch, and must not grow one — `reconcile.py` naming the effect level fails
`test_only_effects_py_knows_the_effect_level_exists`.

`SimulatedDisplay.list_by_var` answers from its own in-memory window map, so a simulated run
exercises the whole decision path against windows it "opened" itself.

## W8 — Untouched, and confirmed by reading the diff

- `KittyDisplay.open` — the `--hold` flag stays (FR-017)
- `KittyDisplay.close`, `find_by_var` — reused unchanged (R2, R4)
- `cleanup.py` — a third caller of `live_sessions`, not an edit
- `operations.cancel` — the rule is about the work, so it converges with no change
- `capacity.py`, `states.py`, `spool.py`, `migrations.py`, `db.py` — no schema change, no new query
- `sessions.window_id` — still recorded, still shown, read by nothing here
