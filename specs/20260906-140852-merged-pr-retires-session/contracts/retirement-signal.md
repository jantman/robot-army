# Contract: what authorises a retirement

Normative. Where this and the prose in `plan.md` differ, this wins.

This **amends** `specs/20260905-121903-retire-finished-sessions/contracts/session-retirement.md`.
Every clause of that contract stands except C2 rule 6 and the `session.retire` detail in C3.1,
both replaced below. C1, C4, C5, C6, C7 and C8 are unchanged and are not restated.

## D1 — The decision, in order (replaces C2)

Evaluated per session row. The **first** rule that matches decides, and every rule but the last
means *leave everything exactly as found* — no transition, no anomaly, no audit record (C6).

| # | Condition | Outcome |
|---|---|---|
| 1 | the work item is absent, or its state is not `done` | LEAVE |
| 2 | `session.pid` is falsey (`NULL` or `0` — no process was ever recorded) | LEAVE |
| 3 | no registry entry for `session.session_id` | LEAVE |
| 4 | the entry's process is not alive (`entry.alive()`) | LEAVE |
| 5 | `entry.idle_for()` is `None` — status is not `"idle"`, or `statusUpdatedAt` is absent, malformed, or in the future | LEAVE (FR-002) |
| 6 | `item.has_merged_pull_request` | **RETIRE**, signal `merged_pull_request` (FR-001) |
| 7 | `entry.idle_for() < RETIRE_IDLE_SECONDS` | LEAVE (FR-003) |
| 8 | otherwise | **RETIRE**, signal `quiet_period` |

Rules 1–5 are **unchanged**, and rule 5 keeping its position ahead of rule 6 is the whole of
FR-002: a merged pull request removes the *duration* requirement and never the *idleness* one.
Rules 3, 4 and 5 remain the "unknown is safe" rules — every way of failing to establish that
the worker is idle leaves it alone, so being wrong about the registry can delay a retirement
and can never cause one.

**No floor on rule 6.** Not "a small one"; zero. Research R4 has the arithmetic: against the
measured 47-second timeline any non-zero floor declines on the one pass that matters and
`_sweep_stale_sessions` raises the anomaly eight lines later, which is the defect.

`RETIRE_IDLE_SECONDS` keeps its value of 1800 and now applies to rule 7 only.

## D2 — `has_merged_pull_request`

| | |
|---|---|
| Where | `WorkItem`, a property in `models.py`, immediately after `pull_request_list` |
| True when | any element of `pull_request_list` has `state == "merged"` — an exact match. States are lower-cased at the boundary and an unrecognised one passes through, so anything GitHub adds later reads as *not merged* |
| False when | the column is `NULL`, `'[]'`, unparseable, not a list, or holds only `open` / `closed` entries |
| Network | none. It reads the stored column and nothing else (FR-004) |

"Never looked up" is false, not unknown, and that is deliberate: it is the direction that
delays a retirement. It matches `pull_request_list`'s own documented choice to answer `[]` for
both "none found" and "never asked".

## D3 — The signal is recorded before the signal is sent (replaces the detail in C3.1)

`session.retire` keeps its action, entity, position (**before** anything is terminated) and
every existing key — `item_id`, `session_id`, `pid`, `proc_start`, `idle_s` — and gains:

| Key | Values | Means |
|---|---|---|
| `signal` | `merged_pull_request` | the item has a merged pull request; the maintainer accepted the work |
| | `quiet_period` | no merged pull request, and the worker had been idle longer than `RETIRE_IDLE_SECONDS` |

FR-009: the log alone must distinguish the two. With one gate, `idle_s` implied the reason;
with two, an `idle_s` of 47 is either a merged pull request or a bug, and a reader should not
have to know which.

The settle reason passed to `reclaim_stale_session` names the same fact in prose, so a session
row read on its own says why its worker was ended.

## D4 — Where the decision lives

One module-level helper in `reconcile.py` taking the item and the established idle time and
returning the signal name or `None`. The gate in `_retire_finished_sessions` becomes:

1. `idle_s = entry.idle_for()`; `None` → LEAVE (rule 5, unchanged in meaning and position);
2. ask the helper; `None` → LEAVE (rules 6–7);
3. otherwise retire, passing the signal through to `_retire_one`.

A helper rather than a compound `if`, so D1 is one testable decision table rather than a
condition spread across a loop body. `_retire_one` gains one parameter and puts it in the
detail; nothing else about it changes.

## D5 — What must be true after a pass, and is the point of the whole feature

For an item that reaches `done` on pass *N* with a merged pull request and a worker idle for
any duration ≥ 0:

| | |
|---|---|
| the worker | ended on pass *N* |
| the session row | closed on pass *N*, with `ended_at` and a reason naming retirement |
| the capacity slot | released on pass *N*, globally and per repository |
| the terminal tab | closed on pass *N*, by the existing sweep and with no change to it |
| the worktree | no longer `skipped`; reclaimed on pass *N* if cleanup is enabled |
| `orphan_session` | **never raised** — not on pass *N*, not on any later pass, and not raised-then-resolved |

The last row is FR-011 and it is an assertion about a **full reconciliation pass**, not about
the sweep in isolation. It can only be tested by running `reconcile.reconcile` and asserting
that `result.orphans == 0` and that no `anomaly.raised` record exists for that session.

## D6 — Comments this contract obliges the change to correct

Both are shipped reasoning that this change falsifies. Leaving either in place would be worse
than having no comment, because each argues for the old behaviour (FR-012):

| Location | What it claims | What it must say |
|---|---|---|
| `reconcile.py:511` | the retire-before-sweep ordering makes "no anomaly for the ordinary successful path" free | the ordering makes the anomaly *unreachable once retirement acts*; acting on the pass the item goes `done` is what the merged-pull-request signal is for. Necessary, and now finally sufficient |
| `RETIRE_IDLE_SECONDS`, `reconcile.py:881`–893 | erring long is nearly free | true only where it still applies — the hand-closed path, whose only cost is a slot that comes back later. On the merged path the cost was an anomaly on every successful item, which is why that path no longer waits |

The guide carries the same correction: `docs/guide/5-outcome.md` states the 30-minute rule as
though it were the only one.
