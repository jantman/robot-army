# Research: The Label Is Re-checked Before Dispatch

## R1 — What the check compares, and against what

**Decision**: `config.github.label in <stored labels>`, using the row's `labels` column. The
comparison is the one `poll.evaluate` makes (`poll.py:82`), exact and case-sensitive.

**Rationale**:
- The issue proposes this, and it keeps `plan` free of I/O. `plan` runs on every dispatch tick
  and every web render, so a live read per queued item is out of the question.
- Using the same operator as discovery means an item is held exactly when discovery would have
  refused it.

**Alternatives considered**:
- *A shared helper such as `poll.carries_label(labels, label)`.* It would wrap one operator, and
  `ordering` would import `poll` for it. Both call sites are one readable line.
- *Re-reading the issue.* This is an I/O call inside `plan`, which the web interface would pay
  on every render.

## R2 — Where the reason sits, and which paths honour it

**Decision**: A new `HoldReason.NOT_LABELLED`, declared directly after `HELD`. It is computed in
`_hold_for` and is **not** added to `launch_holds`. `_hold_for` returns a launch hold first only
when it is `PAUSED` or `HELD`. It then applies the label check, and after that any remaining
launch hold (capacity), then the queue reasons as today.

**Rationale**:
- *Precedence.* In the incident the operator raised the cap and four items dispatched. With the
  reason below `global_cap`, the queue would have shown `global_cap` against all nine: an
  invitation to do exactly that. Paused and held stay above it because each stops the item
  whatever its label, and each is a more deliberate statement.
- *Declaration order is precedence* (the `HoldReason` docstring), so declaring it after `HELD`
  keeps that rule true. The docstring gains the justification alongside the others.
- *Not a launch hold.* `launch_holds` is shared with `dispatch.check_launch_gate`, which gates
  `resume` and `restart`. Those accept only `interrupted` and `awaiting_review` items
  (`operations.py:3336`, `:3408`): work already begun, which the spec leaves alone (FR-010).
  The only path that starts a `ready` item is `select_and_dispatch`, which walks `plan`
  (verified: the other `dispatch_item` callers are `resume` and `restart`). The hold in `plan`
  therefore covers every entry into a first session.

**Alternatives considered**:
- *Rank it with the item conditions, next to `not_onboarded`.* This is the natural place for a
  condition of the item, but it puts the cap in front of it: the reported failure.
- *Add it to `launch_holds`.* This refuses `resume` of an interrupted session because the
  configuration changed, a surprise the issue explicitly prefers to avoid. It also makes
  `--force` the only way through.

## R3 — Malformed stored labels

**Decision**: If the column does not decode to a list of strings, treat the item as not
carrying the label. The detail says the stored labels could not be read.

**Rationale**: The check exists to stop unwanted dispatch, so failing open would defeat it. A
malformed column can only come from a hand edit or corruption, so the hold is also the right
signal.

## R4 — Refreshing the stored labels

**Decision**: In `poll_repo`, when an issue in the listing matches an existing row whose state
is `ready`, compare the row's labels with the listing's as sets. If they differ, write the
listing's labels (in the listing's order) and record `poll.labels_refreshed` with `old` and
`new`, in one transaction. Only `ready` rows are refreshed.

**Rationale**:
- Without this, a hold can only be lifted by reverting the configuration. Every other path runs
  into the existing-row `continue` (`poll.py:193`).
- *Set comparison.* It stops GitHub's label order from producing a write and a record per poll.
  The steady state must stay silent (spec US2 AS2).
- *Only `ready`.* That is the population this feature governs. `discovered` rows are already
  re-evaluated in that branch, and `retry` refreshes `failed` rows from a live read. Rows past
  dispatch never consult their labels again.
- *Unconditional listing (#60).* After a label change the next listing is unconditional, so
  every issue carrying the new label reaches this branch on the first poll.

**Alternatives considered**:
- *Refresh title and body too, as `retry` does.* That widens this fix into content freshness,
  which nothing here needs.
- *Treat absence from the listing as removal.* This is only sound when the listing is complete
  and unconditional; the spec leaves it out of scope.

## R5 — What the log shows for a held pass

**Decision**: No new dispatch record. When every candidate is `not_labelled` (or the first
per-item hold seen is), `select_and_dispatch` already calls `_note_hold`, which writes
`dispatch.at_capacity` with `reason: "not_labelled"` and the detail. `_hold_signature` includes
`str(entry.hold)`, so a switch from `global_cap` to `not_labelled` is a new record and ends the
previous hold with `dispatch.hold_ended`.

**Rationale**: FR-009 asks for "the way any other stalled pass is today". This path already
records `repo_cap` and `awaiting_merge`. `_GLOBAL_HOLDS` is unchanged, because the new reason is
per-item.

## R6 — The startup warning

**Decision**: `daemon.warn_about_label(conn, audit, config) -> str | None` is called from
`Daemon.startup` directly after `warn_about_environment`, which is before the spool drain,
reconciliation and any dispatch. It lists `ready` items including simulated ones (the
population `plan` uses) and collects those `label_hold` would hold. If there are any, it
writes one `daemon.label_warning` record, `outcome="error"` as `daemon.config_warning` does,
with `label`, `count` and `item_ids`, and returns the one-line message.

**Rationale**:
- It reuses the predicate `plan` uses, so the warning and the queue cannot disagree.
- `outcome="error"` follows the two existing startup warnings, which is what `robot-army log`
  filters on to surface problems.
- Recording both populations together matches the queue, where simulated rows appear too.
