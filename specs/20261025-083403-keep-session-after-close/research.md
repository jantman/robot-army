# Phase 0 research

Eleven questions. All of them can be answered from the tree and the spec's clarifications
(1a, 2a, 3a), and no unknowns remain. R10 records how FR-008 is met, decided 2026-10-25.

---

## R1 — Where does a keep live, and in what shape?

**Decision**: a new table `item_keeps(work_item_id INTEGER PRIMARY KEY REFERENCES
work_items(id) ON DELETE CASCADE, kept_at TEXT NOT NULL, kept_by TEXT NOT NULL)`, created by
migration 016. It is accessed through `db.list_item_keeps`, `db.set_item_keep` and
`db.clear_item_keep`, and those mirror the hold accessors at `src/robot_army/db.py:1310-1376`.
A `Keep(kept_at, kept_by)` dataclass sits beside `Hold` (`src/robot_army/models.py:465`).

The hold table (`src/robot_army/migrations.py:432-462`) already answers every structural
question this one raises, and the answers carry over unchanged:

- The primary key makes "at most one per item" a constraint, so a repeated keep becomes a
  reported no-op.
- Presence *is* the fact: there is no expiry column, no level and no note (spec 2a).
- `ON DELETE CASCADE` stops a keep from outliving its item or reattaching to a recycled id.
  `purge_simulated` needs no change.
- There is no `dry_run` column. A keep applies to a simulated item for the same reason a hold
  does: a rehearsal must rehearse the real behaviour.

`_set_hold` and `_clear_hold` are generic over table and column (`db.py:1334-1376`). The keep
accessors reuse them only if the columns are renamed to match (`held_at`/`held_by`), and
they should not be: a row in `item_keeps` reading `held_at` is exactly the confusion between
the two concepts that FR-012 forbids. So the keep gets its own two small functions in the
same shape: `INSERT … ON CONFLICT DO NOTHING`, then read back the original `kept_at`.

**Alternatives**:

- **A `keep` column on `work_items`.** Rejected: it loses the provenance (when, from which
  surface), and `work_items` is the table every state transition writes, so a keep would
  become one more column that a transition could clobber.
- **A scope column on `item_holds`.** Rejected because of FR-012. A hold and a keep are
  different statements with different releases, and sharing a table makes "unhold" ambiguous.
- **No repository-scoped keep.** It is out of scope by clarification 1a, which removed the
  per-repository default.

---

## R2 — How does a keep stop retirement?

**Decision**: in `_retire_finished_sessions` (`src/robot_army/reconcile.py:1150`), right after
the `item.state is not WorkItemState.DONE` test at line 1200:
`if item.id in keeps: continue`. Here `keeps = db.list_item_keeps(conn)` is read once at the
top of the function. The skip writes nothing.

Placement matters. The skip comes **before** the liveness and idleness reads, so a kept
session costs no registry lookup. It also comes after the `done` test, so a keep on a
`dispatching`/`active` item changes nothing at all. That item was never a retirement
candidate, and the keep simply waits for `done`.

Silence follows the established rule. The function's docstring (lines 1184-1189) names
"not yet" silence as the feature's one Principle III gap, at about 1,440 records a day
otherwise. A kept session is "not yet" for an operator-chosen length of time, which makes the
argument stronger, not weaker. The fact that the session is kept is reconstructable from the
log: `keep.item` records it and `unkeep.item` ends it.

`_retire_signal` (line 1115) is **unchanged**. Releasing the keep returns the item to exactly
the decision table it would have met: a merged pull request retires the worker on the first
pass it is observed idle (SC-002), and otherwise the quiet period applies.

**Alternatives**:

- **A third signal in `_retire_signal`.** Rejected. That function answers "what authorises
  retirement", and a keep is the opposite question.
- **Suppressing retirement in `_retire_one`.** Rejected: it is too late, because the
  liveness and idle reads would already have run.

---

## R3 — How does a keep stop `orphan_session`, and only for itself?

**Decision**: three sites raise or keep `orphan_session`. Only one of them changes.

| Site | Reached by a kept done session? | Change |
|---|---|---|
| `reclaim_stale_session` alive branch, `reconcile.py:318-341` | **Yes.** `_sweep_stale_sessions` (line 1418) runs every open row through it. The item is `done`, which is not in `SESSION_BEARING_STATES` (line 241), and the worker is alive. | Inside the alive branch, before `raise_anomaly`: if the item is `done` and kept, return `"left"`. |
| `_sweep_superseded_sessions`, line 358 | Only for an *older* attempt's live worker | None. A superseded attempt is not the session the keep exists for, and that report is still true. |
| `_orphan_sweep`, line 1622 | No. Line 1678 skips any entry whose row is `running`, and a kept session's row stays `running`. | None. |

The test sits **inside** the alive branch on purpose. A kept session whose process has died
(the operator exited, or the machine rebooted) must still fall through to `reclaimed`.
Otherwise the row would hold a slot forever, and that is issue #28 again. It is limited to
`done` because `operations.abandon` (`src/robot_army/operations.py:3720`) also calls this
function. A live worker under an `abandoned` item is a real orphan whatever the operator said
earlier, and FR-006 covers `done` only.

`"left"` is reused rather than adding a fifth outcome. Its existing meaning, "legitimate;
leave it", is exactly the claim. No caller counts it (lines 1459-1462), so a new string
would have no reader.

**The window before the keep.** If the worker is busy when its item reaches `done`,
retirement declines, and the same pass's `_sweep_stale_sessions` raises `orphan_session`.
If the operator then places the keep, the anomaly stays open, because
`_resolve_orphan_anomalies` (line 1699) retracts only when the process is gone. So the
resolver gains one more retraction condition: an `orphan_session` whose
`detail.work_item_id` names a `done`, kept item, and whose session row is still open, is
resolved with the reason "kept by the operator". Pids and the start-time evidence are not
consulted for this branch. The anomaly's claim was "nobody accounts for this", and the keep
now accounts for it.

**Alternatives**:

- **Adding `done`-and-kept to `SESSION_BEARING_STATES`.** Rejected. That is a set of
  *states*, its docstring derives it from the dispatch path (lines 230-240), and other code
  reads it.
- **Leaving the pre-keep anomaly to be acknowledged by hand.** Rejected: it violates FR-006
  for exactly the case US3 describes.

---

## R4 — The tab: is any change needed?

**Decision**: none. `_close_finished_windows` (`reconcile.py:1969`) closes a `done` item's
windows only when `cleanup.live_sessions` is empty (line 2006), and a kept session's row stays
`running`, so the tab stays. When the operator exits the session, the exit record closes the
row, the next pass finds no live sessions, and the tab goes. That is the right outcome (spec
US1 scenario 3), and it happens whether or not the keep is still in place.

A test pins both halves, because "no change needed" is a claim that only holds while that
guard does.

---

## R5 — Cleanup: is any change needed?

**Decision**: one small change. The guard is already correct, and the change is to the
record volume.

**The guard.** `cleanup.eligible` (`src/robot_army/cleanup.py:124-148`) refuses while
`live_sessions` is non-empty, so a kept session's worktree and branch are never reclaimed
automatically. FR-002 holds with no change, and `cleanup <id>` and `worktree remove <id>`
already refuse in the same way (FR-011).

**The volume.** `clean_item` writes a `cleanup.considered`/`skipped` record every time it
declines (lines 165-182), and every pass re-offers a `skipped` item
(`db.list_cleanup_candidates`, `db.py:399-418`). That was tolerable when "still live" lasted
the minute between `done` and retirement. A keep can last days, which is the 1,440-records-a-
day shape C6 exists to refuse.

**Decision**: `list_cleanup_candidates` excludes an item that is kept **and** has an open
session row. Both automatic cleanup and the bare `robot-army cleanup` use that list, so the
"identical function under identical guards" property in `5-outcome.md` holds. `cleanup <id>`
does not use the list (`cleanup.py:549-553`), so it still reports "still live" (FR-011). The
exclusion needs both conditions. A keep left standing after its session ended protects
nothing (R7), and it must not block disk reclaim forever.

**Alternatives**:

- **No change, accepting the records.** Rejected because of C6.
- **Excluding every kept item.** Rejected: a forgotten keep would then silently retain
  499 MB.

---

## R6 — Capacity: how does "machine full" name kept sessions?

**Decision**: `CapacitySnapshot` (`src/robot_army/capacity.py:54`) gains
`kept_per_repo: dict[str, int]`, meaning live session rows under `done`, kept items by
repository, plus a `kept` property that sums it. `snapshot()` fills it with one joined query
(`db.count_kept_sessions_by_repo`) over the same open rows it already lists (line 284).

`components` stays exactly as it is, because its terms must sum to `total` (lines 111-127),
and a kept session is already counted inside `ours`. Kept sessions show as an annotation on
that term: `breakdown` renders `2 ours (1 kept), 0 other` when `kept > 0`, and the same text
as today otherwise. `breakdown` is the one phrase every surface prints: the global-cap hold
detail at `src/robot_army/ordering.py:383-386`, the web pill at
`src/robot_army/web/html.py:430`, and `status` through `operations.py:793`. FR-005's
"machine full" reason therefore names kept sessions on every surface with one change.

The per-repository cap reason (`ordering.py:395-403`) is the case that will actually bite on
privatepuppet. It appends `, N kept` when `kept_per_repo[repo] > 0`, so the reason names the
fix: release one.

**Alternatives**:

- **A `kept` component that is subtracted from `ours`.** Rejected: `ours` carries session ids
  and is relied on as "every session we started" (lines 60-66).
- **A separate line.** Rejected: every caller prints `breakdown` as one phrase.

---

## R7 — What happens to a keep after its session ends?

**Decision**: nothing. It stays until it is released (clarification 2a, FR-003). `keeps`
and the web listing show it with the item's state and "no live session". This is the
precedent `operations.list_holds` states (`operations.py:4617-4620`): "nothing sweeps a hold,
because clearing on a state transition is expiry under another name."

Such a keep is inert. It holds no slot (no open row), it does not block cleanup (R5), and it
does not affect retirement (there is nothing to retire). Its only cost is a row in a listing,
and showing it is what makes it visible rather than mysterious.

**Alternatives**: auto-clearing when the session ends. Rejected: it contradicts 2a, and it is
a sweep that writes state on a transition, which is the thing the hold design refused.

---

## R8 — Which items may be kept? (FR-010, the before-dispatch assumption)

**Decision**: refuse only when the item is terminal (`done` or `abandoned`) **and**
`cleanup.live_sessions` is empty, which means no session exists to keep and none can start.
The refusal names `claude --resume <session-id>` with the latest session's id when one
exists. Every other state is accepted:

- `ready` and `dispatching`: the keep applies once a session exists. This is the
  "I know at filing time" assumption.
- `active`: the normal case, placed before merging.
- `interrupted`, `awaiting_review` and `failed`: accepted, and it covers the resumed or
  retried session, because the keep is keyed on the item, not the session.
- `done` with a live session: the US3 case.

The refusal exits `EXIT_PRECONDITION`, matching other state refusals such as `abandon`'s
(`operations.py:3693-3700`). An unknown item reuses `_no_such_item`.

**Alternatives**: refusing every terminal item. Rejected: it removes US3.

---

## R9 — CLI and web surfaces

**Decision**: the surfaces mirror holds, under their own names.

- **CLI**: flat verbs `keep <id>` and `unkeep <id>`, plus `keeps`, matching the precedent at
  `src/robot_army/cli.py:344-362`. They are item-only, so there is no `--repo` and no
  mutually exclusive group. `keeps` joins `READ_COMMANDS` (line 36). `keep` and `unkeep`
  join the universal-flag list at line 393, so `--json` works.
- **Operations**: `keep_item`, `unkeep_item` and `list_keeps` in `operations.py`, each shaped
  like `hold_item`, `unhold_item` and `list_holds` (lines 4377-4470 and 4609): one
  transaction, an audit pair through `ctx.audit.action`, a redundant request reported rather
  than refused, and a release of nothing treated as a no-op that exits 0.
- **Web**: `POST /item/<id>/keep` and `/item/<id>/unkeep` go through a `_item_keep_action`
  shaped like `_item_hold_action` (`src/robot_army/web/server.py:1165-1208`). They are not
  effect-guarded and do not need the daemon, for the same reasons that docstring gives:
  placing a keep starts and signals nothing. Checks run inside `_perform`'s body.
  - **Where the control appears**: on the item page (`web/pages.py:1926`), and on each
    `/active` row (`pages.py:798`), because that is where the operator is standing before a
    merge.
  - **The listing** is a new "kept" section on `/active`. It lists **every** keep, including
    inert ones and ones on items not yet dispatched, and each row has a release control. A
    kept session under a `done` item is the one running session that no other page lists:
    `/active` filters on `state = active` (line 809). So without this section the session
    would be invisible on the web. That section is the FR-007 surface.

**Alternatives**: a separate `/keeps` page. Rejected: `/active` already answers "what is
running", and a kept session *is* running.

---

## R10 — FR-008: a retirement after release must be distinguishable

**Decision**: this is met by the log sequence, not by a new field on `session.retire`.
`unkeep.item` records the ids of the session rows that were open at release
(`detail.open_sessions`), and the next `session.retire` for one of those ids follows it.
Reconstruction from the log alone (Principle III's own standard) answers "was this worker
kept, and when was it let go" with a join on session id.

A field on `session.retire` would need the retirement path to know that a keep *used to*
exist. That means either a soft-deleted keep row (`released_at`, which needs a sweep to clear
it eventually) or a scan of the log, which no code does. Both add state to avoid a join.

**Decided 2026-10-25**: the log join (option a). FR-008 in the spec now reads "distinguishable
from the audit log". A field on the `session.retire` record would cost a `released_at` column
and a rule for when such a row is finally deleted, and was not taken.

---

## R11 — Pause and holds (FR-012)

**Decision**: no change, enforced by tests. `_retire_finished_sessions` never reads the pause
or either hold table today, and the plan adds a keep read without a hold or pause read. A unit
test retires a merged, idle `done` item while dispatch is paused and its item and repository
are both held, which pins clarification 3a.
