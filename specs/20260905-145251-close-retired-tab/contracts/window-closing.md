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
    item.id
    for item in work items where state == DONE
    if item has at least one session row
    and cleanup.live_sessions(conn, item.id) == []
}
if not candidates:
    return 0          # the terminal is never touched
```

Three conditions, each carrying a requirement:

| Condition | Requirement | Why |
|---|---|---|
| `state == DONE` | FR-003 | `failed` and `abandoned` keep their windows indefinitely. This is also what preserves `--hold`'s purpose (FR-017) without a second rule |
| at least one session row | spec edge case | A `done` item that never had a session — a rebuilt database — offers no evidence its session ended. `live_sessions` returns `[]` for "all finished" *and* for "never had any", and only the first qualifies |
| `live_sessions(...) == []` | FR-004 | The shared definition from issue #79, reused rather than re-derived (R5). A worker that survived a termination attempt keeps a `running` row, and so keeps its windows |

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
(`kitty.close_window`). Nothing about that call is re-implemented.

| Outcome | Effect |
|---|---|
| closed | counted in `windows_closed` |
| the window had already gone | **success**, not a failure (FR-014). Not counted, not recorded as an error |
| the close failed | recorded as `window_close_failed` with the window id and item id; **the sweep continues to the next window** (FR-013); not counted |
| the listing itself failed | recorded once for the pass; the sweep returns 0; the pass completes normally |

A `BoundaryError` from either the listing or a close is caught. Reconciliation never raises for an
operational condition.

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
