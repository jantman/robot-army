# Phase 0 — Research

Six findings. Every claim about existing code was read out of the source, and every claim about the
machine was measured on it after the first live retirement.

---

## R1 — Why the window survives, and why the last research was wrong

PR #140's R6 concluded: *"FR-014 needs no code. The window closing is a consequence of the process
ending."* It reasoned from the launch chain — `kitty → dtach → wrapper → claude` — and never read
the launch flags.

**Measured after the first live retirement**: both workers gone, both session rows `lost`, no
`dtach`, no wrapper, `/run/user/1000/robot-army/` empty, capacity back to `1 of 3` — and both tabs
still open.

`boundaries/kitty.py:222`:

```python
args = ["launch", "--type=tab", "--hold", "--cwd", cwd, "--title", title]
```

with the docstring stating the reason:

> `--hold` is always passed so a failed launch leaves a readable window instead of one that vanishes
> instantly (M0 F11) — that window is often the only evidence of what went wrong.

`--hold` keeps a kitty window open *after its command exits*. So a fully dead process tree leaves a
live tab, by design, and nothing anywhere closes one.

**The lesson worth carrying**: the chain was reasoned about and the flags were not. A one-line
`grep` for the launch arguments would have caught it before it reached the published guide, where
`docs/guide/5-outcome.md` currently tells the maintainer's future self that the tab closes with the
worker. That sentence is false until this ships and is corrected as part of it.

---

## R2 — The capability already exists and has never been called

`boundaries/kitty.py:269`:

```python
def close(self, handle: DisplayHandle) -> None:
    with self._audit.action("kitty.close_window", target=str(handle.window_id)):
        self._kitty(["close-window", "--match", f"id:{handle.window_id}"], ...)
```

It is on the `Display` protocol, implemented by `KittyDisplay` and by `SimulatedDisplay` (which
records `simulated=True` and pops the window from its in-memory map), and:

```
$ grep -rn "\.close_window(\|\.close(" src/robot_army/ | grep -v boundaries/kitty.py
(nothing)
```

**Zero callers.** This feature is the first, which means Principle III's before-and-after record
comes for free through the existing `audit.action` context.

**Decision**: reuse it unchanged.

---

## R3 — Identity: the marker, not the stored number

**Question**: FR-005 and FR-007 forbid closing a window the system cannot positively identify. What
establishes that?

Two candidates, and the difference matters.

**`sessions.window_id`** — recorded at dispatch (`dispatch.py:1168`), present on the machine now
(50 and 49 for items 45 and 54). **Rejected as the identity.** Kitty assigns window ids per kitty
process, monotonically from 1, and a kitty restart begins again at 1. A stored 50 can therefore name
a completely unrelated window — the maintainer's editor, a build log — and closing it would destroy
something the system never opened. This is the pid-reuse failure in different clothing, and this
codebase has been bitten by that twice already (FR-038's `proc_start` guard, and #79).

**The `ra_item` user variable** — `dispatch.py:719` passes `user_vars={"ra_item": str(item_id)}` to
`display.open`, which turns it into `--var ra_item=<id>` on the launch. `kitty @ ls` reports
`user_vars` per window, and `find_by_var` already reads them.

**Decision**: identity is `ra_item`. A window carrying it was opened by this system and names its
work item; a window without it is never touched, whatever it contains. The stored `window_id` is
used only as the handle to close a window the live listing has already identified — never to decide
*whether* to close one.

A useful side effect: `ra_item` names the **item**, not the attempt, so every attempt's window
carries the same value. FR-002 — close all of a completed item's windows, including superseded
attempts — falls out rather than needing its own rule.

---

## R4 — `find_by_var` is the wrong shape; one listing per pass is the right one

`find_by_var(key, value)` returns the **first** matching window or `None`. Two problems:

* FR-002 needs *every* window for an item, and looping `find_by_var` until it returns `None` hides
  an unbounded loop over subprocess calls behind a function whose name says "find one".
* It takes a value, so answering "which of my windows belong to finished items" means one
  `kitty @ ls` per candidate item per pass.

**Decision**: add `list_by_var(key) -> list[DisplayHandle]` to the `Display` protocol, returning
every window carrying that key with its value in `user_vars`. The sweep lists **once** per pass and
decides in memory.

`_windows()` already flattens `kitty @ ls`'s os-window → tab → window nesting, so both
implementations are a few lines each. `SimulatedDisplay` keeps an in-memory map and answers from it.

**Alternative rejected**: reading each window's `foreground_processes` to decide whether its command
had exited. `find_by_var`'s own docstring records why — `--hold` inserts a `kitten run-shell` layer
that repeats the whole command in its own argv, so the same string appears at several depths, and
M0 measured that producing a wrong conclusion during the spike. The session records answer the same
question exactly (R5).

---

## R5 — "Is anything still running for this item?" already has one definition

`cleanup.live_sessions(conn, item_id)` returns every session row for the item in `starting` or
`running`. Its docstring is explicit that it deliberately checks **every** row rather than the
latest attempt, because a superseded attempt keeps running reparented — and that it consults
neither the item's state nor the process table.

It was factored out by issue #79 with two callers (`cleanup.eligible` and
`operations.worktree_remove`) precisely so "live" would not acquire a second meaning.

**Decision**: this sweep is the third caller. `LIVE_SESSION_STATES` is an allow-list of open states
for the reason its own comment gives — a closed state added later must not silently start counting
as live — and inheriting it means the window rule cannot drift from the disk rule.

**Consequence for FR-004**: a `done` item whose worker survived a termination attempt still has a
`running` row, so its windows are not closed. The one case where a window might still have something
live in it is the one case the guard already covers.

---

## R6 — Cost, and why the database is asked first

The sweep runs every 60 seconds forever. Two costs to avoid:

* **A subprocess per pass on an idle machine.** `kitty @ ls` is cheap but not free, and reconciliation
  currently touches the display not at all.
* **An error per pass when kitty is not running.** `_ls()` raises `BoundaryError` on failure. A sweep
  that always calls it would log ~1,440 failures a day on a machine with no kitty — the exact
  disproportion Principle III's documented-gap clause exists to avoid, arrived at from the other
  direction.

**Decision**: build the candidate set from SQLite first — `done` items with no live session — and
return immediately when it is empty, before the display is touched at all. The terminal is consulted
only when there is genuinely something that might need closing.

**Corrected in review of PR #141: that is not sufficient, and on its own the gate never fires.**
`done` is terminal and rows are never deleted, so a completed item meets every database condition on
every future pass forever. The candidate set never empties again after the first item finishes, and
every cost above is incurred anyway. The fix is a process-memory set of items already answered for —
closed, or listed and found to have none, both of which are final because `done` has no outgoing
transition. Keyed on `(id, done_at)`, because SQLite reuses freed row ids and `purge_simulated`
frees them.

The lesson generalises past this feature: **a gate built out of monotonically-growing state is not a
gate.** Every condition here was about a fact that, once true, stays true.

This also makes the failure path rare and meaningful: a `BoundaryError` from the listing now means
"there was work to do and the terminal could not be reached", which is worth a record, rather than
"there is no kitty on this machine", which is not.

**Ordering in `reconcile()`**: after `_sweep_sockets`, with the other physical-residue sweeps, and
therefore after `_retire_finished_sessions` **and** `_sweep_stale_sessions`. Both halves matter: a
session retired this pass has had its row closed by then, so its item qualifies immediately and the
window goes in the same pass rather than the next.

---

## Decisions table

| # | Decision | Rejected alternative |
|---|---|---|
| R1 | Close windows explicitly; `--hold` and the launch path stay exactly as they are | Dropping `--hold`, which would restore the M0 F11 failure it was added to fix |
| R2 | Reuse the existing `close()` | Writing a second close path |
| R3 | Identity from the `ra_item` marker | The stored `window_id`, which kitty renumbers on restart |
| R4 | New `list_by_var(key)`; one listing per pass | Looping `find_by_var`; reading `foreground_processes` |
| R5 | Reuse `cleanup.live_sessions` | A fourth definition of "still running" |
| R6 | Database gate before the terminal is touched, **plus** a process-memory set of items already answered for — without the second half the gate never fires (corrected in review) | Listing windows unconditionally every pass; a persisted column, which costs a migration to save one listing per restart |
