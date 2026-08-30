# Contract: when a session slot is reclaimed, and what is recorded

**Feature**: [../spec.md](../spec.md) | **Model**: [../data-model.md](../data-model.md)

The single rule, applied at three call sites. This document fixes the decision so the three cannot
drift apart, and fixes what each writes so the log can be asserted against.

## C1 — The decision function

Input: one session row in `starting` or `running`, its work item, and an observation of the
machine (a registry scan plus a `/proc` root).

```
if item.state in (dispatching, active):        -> LEAVE      (legitimate, not stale)
elif a registry entry for session_id is alive: -> REPORT     (FR-005: do not close)
else:                                          -> RECLAIM    (transition to lost)
```

A simulated session (`dry_run = 1`, `pid = 0`) never has a registry entry, so it always takes the
RECLAIM branch. That is the reported case and it has no liveness ambiguity to resolve.

## C2 — The three call sites

| Site | When it runs | Which rows it considers |
|---|---|---|
| `operations.cancel` | after `session_host.terminate` succeeds, in the same transaction as the item's move to `interrupted` | the one session it just stopped |
| `operations.abandon` | in the same transaction as the item's move to `abandoned` | the item's latest session, if open |
| `reconcile` sweep | every pass, after the closed-issue pass and before the orphan sweep | every open session row in the database |

The command sites make FR-012's promise — the slot is released before the command returns, with or
without a daemon. The sweep makes the invariant true regardless of route, including for rows
already leaked before this feature existed (FR-004).

**Ordering within a reconciliation pass is load-bearing.** The sweep runs *after* the active-item
sweep and `_resolve_closed_issues`, so rows those passes already closed are not re-examined, and
items they moved out of `active` are seen in their settled state. It runs *before* `_orphan_sweep`,
which is left unchanged.

## C3 — What each outcome writes

### RECLAIM

| Record | Value |
|---|---|
| `sessions.state` | `lost` |
| `sessions.ended_at` | stamped by `_SESSION_STAMP_COLUMN`, not by the call site |
| audit action | `state.session`, written inside the same transaction by `transition_session` |
| audit `detail.reason` | names the route: cancellation, abandonment, or the sweep |
| audit `detail.from` / `to` | `running` or `starting` → `lost` |

The reason string must identify **which route** closed the row, because that is the difference
between "the maintainer stopped this" and "this was found stale later", and the log is the only
place that distinction survives.

### REPORT

| Record | Value |
|---|---|
| `sessions.state` | **unchanged** — still `starting` or `running` |
| anomaly `kind` | `orphan_session` (existing kind; no new kind is introduced) |
| anomaly `entity_type` / `entity_id` | `session` / the session id |
| anomaly `detail` | pid, cwd, the work item id and its state, and a note saying a live worker was found under an item that is no longer running one |

Raised at most once while unacknowledged — the partial unique index absorbs re-detection, which is
what keeps a 60-second pass from producing 1,440 rows a day.

### LEAVE

Nothing is written. A pass in which every open row is legitimate writes no record for this feature
at all, which is the same omission `_observe_speckit` already documents: the alternative is a log
whose lines almost all say nothing changed.

## C4 — The pass summary

`ReconcileResult` gains one counter, reported in `summary()` and therefore in the
`reconcile.pass` audit record and the CLI output:

| Field | Meaning |
|---|---|
| `reclaimed` | how many stale session rows this pass closed |

FR-009 exists because the issue's `checked 0` was the misleading part of the report: a pass that
did work must not read as a pass that examined nothing. Rows that took the REPORT branch are
counted by the existing `orphans` field, not by `reclaimed` — they were not reclaimed.

## C5 — Interaction with a late exit record

For a live session cancelled while running, the wrapper may still deliver an exit record after the
row is closed. Fixed behaviour, measured in
[research R3](../research.md#r3--closing-the-row-at-cancel-time-is-race-safe):

| | Outcome |
|---|---|
| `spool.apply_record` returns | `"duplicate"` |
| Session row | stays `lost`; the real exit status is **not** written |
| Work item | untouched — `apply_record` only moves an item in `active` |

Accepted, not fixed. The history keeps a weaker reason than the truth, and no state is wrong. The
alternative — allowing `lost → exited_error` — would make a contradiction legal.

## C6 — What must not change

- `SESSION_TRANSITIONS` gains no edge.
- `WORK_ITEM_TRANSITIONS` gains no edge; reclamation never moves an item (FR-011).
- `_orphan_sweep` is unchanged, including its `row.state is RUNNING` guard.
- `_resolve_closed_issues` is unchanged (research R6).
- `capacity.py` is unchanged; the count follows from the row's state.
- `purge-simulated` is unchanged and stays all-or-nothing. This feature removes the need to reach
  for it rather than making it finer-grained.
- No schema change, no migration, no new dependency, no new configuration knob, no new command.
