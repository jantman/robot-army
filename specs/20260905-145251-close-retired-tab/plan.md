# Implementation Plan: Close a finished item's terminal tabs

**Branch**: `speckit/20260905-145251-close-retired-tab` | **Date**: 2026-09-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260905-145251-close-retired-tab/spec.md`

## Summary

PR #140 ended the worker and left the window. Its research asserted the tab would close as a
consequence of the process ending and that this needed no code; the first live retirement disproved
that in the most direct way available — two dead tabs, still open.

This adds `_close_finished_windows`, a reconciliation sweep that closes every kitty window belonging
to a `done` item with no live session. It is a sweep rather than a step of retirement because that
is what reaches the two windows already open, and what survives the daemon dying between the kill
and the close.

Four findings shaped it, and three of them removed work:

1. **`KittyDisplay.close()` already exists and has never had a caller.** Fully written, audited via
   `audit.action`, matching on window id. This feature is its first caller.
2. **`kitty @ launch` writes `ra_item=<item_id>` onto every window it opens.** That marker is better
   identity than the `window_id` stored on the session row, because kitty renumbers windows from 1
   when it restarts — a stored id can name a stranger's window, which is the pid-reuse class of bug
   this codebase has already been bitten by twice.
3. **`cleanup.live_sessions()` is already the shared "is anything running for this item?"
   definition**, factored out by issue #79 with two callers. This is the third, so "live" keeps one
   meaning across cleanup, `worktree remove` and window closing.
4. **`cancel` needs no change at all.** The spec's User Story 3 asks that a by-hand stop of a `done`
   item's session also close the tab. Because the rule is written about the item's state and its
   sessions rather than about which command ended them, `cancel` making a session terminal is enough
   — the next pass closes the window. US3 becomes a test, not code.

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added

**Storage**: none. **No schema change** — this feature reads state and acts on the terminal

**Testing**: `pytest`. `StubDisplay` in `tests/conftest.py` gains the one new method; the existing
`seed_item` / `seed_session` fixtures cover the rest

**Target Platform**: one Linux machine, one user, kitty as the terminal

**Project Type**: single-process daemon plus CLI plus a read-mostly web interface

**Performance Goals**: at most **one** `kitty @ ls` subprocess per reconciliation pass, and **zero**
when the database says there is nothing to close — which is the ordinary state of an idle machine

**Constraints**: reconciliation must never raise for an operational condition; a terminal that
cannot be reached must not spam the log every 60 seconds

**Scale/Scope**: a handful of windows, single-digit finished items per day

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design — see the bottom of this file.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | **Passes.** No new module, no schema change, no configuration key, no dependency. One new sweep, one new boundary method (`list_by_var`, because `find_by_var` returns only the first match and FR-002 wants all of them), one new counter. `cleanup.live_sessions` is reused rather than re-deriving "live", and `close()` is reused rather than written. |
| **II. Single-User, Local-First** | **Passes.** Everything is local: one subprocess to the terminal already running on this machine. No network call. |
| **III. Total Accountability** | **Passes, with one documented gap.** `KittyDisplay.close()` already logs through `audit.action`, so intent and outcome are both recorded for every window closed. The sweep adds `window.close_skipped` for a window it decided against **only when it decided against it for a reason worth knowing** — see the gap below. **The documented gap**: a window that simply does not qualify — its item is `failed`, or still has a live session — writes nothing. A pass runs every 60 seconds and the ordinary machine has several such windows permanently, so recording each one would write thousands of records a day carrying no news. The *decisions that act* are logged in full, and the condition is re-derivable at any instant from the item's state. Precedent: `_sweep_transcripts` and `_retire_finished_sessions` both write nothing for a deferral, for the same reason. |
| **IV. Interruption Tolerance** | **Passes, and this is the design's main argument.** A sweep is idempotent by construction: a window already closed is simply not in the next listing. A daemon killed between listing and closing loses nothing — the next pass sees the same window and closes it. This is precisely the gap an event-driven design would have left, and the reason the spec chose a sweep. No persistent state is written, so there is no partial write to be atomic about. |
| **V. Public Code, Unsupported Project** | **Passes.** No credential is read or logged. Window titles carry a session name and an item id, both already in the log. |
| **Operating Constraints** | **Passes, with the same judgement the previous feature made.** Closing a window is outward-facing in the sense that it changes what is on the maintainer's screen, so it is logged before execution — which `close()` already does. It is **not** in the class the constitution says must "require explicit configuration or confirmation": it deletes no user data, sends no external message, spends nothing, and mutates no remote system. Nothing is destroyed — the transcript, the session record, the worktree and the audit trail are all untouched, and only a *view* is removed. That is the same argument that kept retirement free of a configuration key, and it is restated here rather than inherited silently. |
| **Development Workflow** | **Passes.** Unit tests for the sweep's decision table, the identity guard, and every failure path — the terminal being unreachable, a window vanishing mid-sweep, and one close failing while others succeed. The two questions: **what does this log** — `kitty.close_window` (existing, intent and outcome) plus `window_close_failed`, and a `windows_closed` counter on `reconcile.pass`; **what happens if it is killed halfway** — nothing, by construction; see Principle IV above. |

**Result: PASS.** No violation to justify. One documented Principle III gap, enumerated above.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-145251-close-retired-tab/
├── plan.md              # This file
├── spec.md
├── research.md          # Phase 0 — six findings
├── data-model.md        # Phase 1 — no schema change; what is read and why
├── quickstart.md        # Phase 1 — including the two windows open right now
├── contracts/
│   └── window-closing.md
└── tasks.md             # /speckit-tasks output — not created here
```

### Source Code (repository root)

```text
src/robot_army/
├── reconcile.py            # + _close_finished_windows, + windows_closed counter,
│                           #   one new call placed after _sweep_sockets
├── boundaries/
│   ├── __init__.py         # Display protocol gains list_by_var
│   └── kitty.py            # KittyDisplay.list_by_var + SimulatedDisplay.list_by_var;
│                           #   close() and the --hold launch are UNCHANGED
└── cleanup.py              # UNCHANGED — live_sessions gains a third caller, not an edit

tests/
├── conftest.py                        # StubDisplay gains list_by_var
└── unit/test_window_closing.py        # new — the decision table and every failure path

docs/guide/
├── 5-outcome.md       # correct the sentence that is currently false, and say what closes a tab
└── audit-log.md       # kitty.close_window now has a caller; the windows_closed counter
```

**Structure Decision**: no new module. The sweep belongs in `reconcile.py` beside the other
physical-residue sweeps (`_sweep_sockets`, `_sweep_worktrees`), which do the same kind of job: look
at something outside the database, decide what is left over, and clean it up. `state.md` is **not**
in the list because there is no schema change, and `configuration.md` is not because there is no key.

## Key design decisions

Full reasoning in [research.md](./research.md); [contracts/window-closing.md](./contracts/window-closing.md)
is normative. The short version:

**The database is asked first, the terminal second.** The sweep builds its candidate set from
SQLite — `done` items with no live session — and if that set is empty it never touches the terminal
at all. On an idle machine this feature costs one indexed query per minute and zero subprocesses.
It also keeps the "kitty is not running" error rare: it can only be reached when there is genuinely
something to close, rather than 1,440 times a day on a machine with no kitty.

**Identity comes from the marker, never from the stored number.** `ra_item` is written by this
system onto every window it opens and by nothing else, so a window carrying it is ours and names
its item unambiguously. The `window_id` on the session row is *not* used to decide anything — kitty
renumbers from 1 on restart, so a stored 50 can name a stranger's window months later. The number
is used only as the handle to close the window the live listing just identified.

**`--hold` and the launch path are untouched.** FR-017 is satisfied by not editing them: a failed
launch's item never reaches `done`, so its window never qualifies. The behaviour is preserved by the
`done` gate rather than by a second rule that could drift from it.

**One `kitty @ ls` per pass, not one per item.** `find_by_var` returns the first match and would
need a call per candidate item; `list_by_var` returns every window carrying the key, so the sweep
lists once and decides in memory. That also makes FR-002 — all attempts of an item — fall out
naturally, since every attempt's window carries the same `ra_item`.

## Complexity Tracking

No constitutional violation requires justification. Two decisions sit close enough to a principle
that leaving them unargued would itself be the problem:

| Decision | Why | Simpler alternative rejected because |
|---|---|---|
| A new `list_by_var` on the `Display` protocol | `find_by_var` returns the first match only, and FR-002 needs every window for an item. Calling it once per candidate item would also mean one subprocess per item per pass | Looping `find_by_var` and closing the match until it returns `None` would work but hides an unbounded loop over subprocess calls, and misreads "find one" as "close them all one at a time" |
| A sweep rather than a step inside retirement | The two windows already open are past the moment an event would have fired, and a crash between the kill and the close would leak a window forever. Idempotence is free (SC-003, Principle IV) | An event at retirement is fewer moving parts, but it cannot satisfy SC-003 at all and reintroduces exactly the class of gap this feature exists to close |

## Post-design Constitution re-check

Re-read after Phase 1. **Still PASS**, and the design shrank on the way: no schema change, no
configuration key, no new module, no edit to `cleanup.py`, `capacity.py`, `states.py`,
`operations.py` or the launch path. The documented Principle III gap is a single named case, and
the interruption story is stronger than the previous feature's rather than weaker — a sweep that
writes no state has nothing to be interrupted halfway through.
