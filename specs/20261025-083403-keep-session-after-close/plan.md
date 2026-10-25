# Implementation Plan: Keep a session open after its issue closes

**Branch**: `speckit/20261025-083403-keep-session-after-close` | **Date**: 2026-10-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20261025-083403-keep-session-after-close/spec.md`

## Summary

This plan adds a per-item **keep**: a durable statement that an item's session outlives its
issue. It is stored in a new `item_keeps` table (migration 016) that mirrors `item_holds`,
and it is placed and released with `keep`/`unkeep` (CLI) and `POST /item/<id>/keep|unkeep`
(web). The CLI lists keeps with `keeps`, and the web lists them in a new "kept" section on
`/active`.

Reconciliation reads the keep in four places, and each change is a guard rather than a new
mechanism:

1. `_retire_finished_sessions` skips a kept `done` item, silently (research R2).
2. `reclaim_stale_session` leaves a live, kept `done` session without raising
   `orphan_session`, and `_resolve_orphan_anomalies` retracts one raised before the keep
   (R3).
3. `db.list_cleanup_candidates` excludes a kept item with an open session, which stops a
   `skipped` record being written every pass. The cleanup guard itself already refuses (R5).
4. `capacity.snapshot` counts kept sessions by repository, and `breakdown` and the repo-cap
   reason name them (R6).

The tab needs no change: its existing open-row guard already keeps it (R4). Pause and holds
are untouched, and a test pins that (R11). A keep is never cleared by time or by a state
transition (R7).

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only. None added.

**Storage**: SQLite. One new table (`item_keeps`, migration 016), with no backfill.

**Testing**: pytest. The tests are listed [below](#tests).

**Target Platform**: one Linux machine

**Project Type**: single project: daemon, CLI and a read-mostly web interface

**Performance Goals**: no measurable change. Retirement reads the keep table once per pass.
`reclaim_stale_session` does one primary-key lookup per open row, which the cap bounds.
Capacity runs one more grouped query per snapshot.

**Constraints**:

- A kept session must keep counting against both caps.
- Behaviour for unkept items must be byte-identical (SC-003).
- No new configuration key.

**Scale/Scope**: one migration, about 6 db functions, 3 operations, 3 CLI verbs, 2 routes, 2
page sections, 4 reconcile guards, 1 capacity field, 5 guide pages.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1. Both evaluations pass with no
violations, so the Complexity Tracking table is omitted.*

### I. Simplicity First

**Pass.**

- No configuration knob. Clarification 1a removed the per-repository default.
- No new abstraction. The keep reuses the hold's shape (table, accessors, operation, route,
  control) as parallel code, because sharing the hold's generic helpers would require
  misnaming the columns (R1).
- No new reconcile pass. Every change is a guard inside an existing function.
- No new outcome string for `reclaim_stale_session`; the existing `"left"` is reused (R3).
- No new `/keeps` page; a section on `/active` instead (R9).
- The cleanup change is one extra `NOT EXISTS` clause, not a new code path (R5).

*Post-design*: still passes. The design added no module and no dependency.

### II. Single-User, Local-First

**Pass.** All state is local SQLite. The web control needs no auth beyond the existing
same-origin check, and nothing in this feature touches the network.

### III. Total Accountability

**What does this log?**

| Act | Record |
|---|---|
| Place a keep (CLI/web) | `keep.item` intent and outcome. The web adds its `_perform` record. |
| Refuse a keep (FR-010) | `keep.item` with `outcome: error` |
| Release a keep | `unkeep.item` with `open_sessions` |
| Retract a pre-keep `orphan_session` | `anomaly.resolved` with the keep reason |
| Retirement after release | the existing `session.retire` and `session.retired` |

**Named, justified omissions** (each is the existing C6 "not yet" shape):

1. **A retirement withheld because of a keep writes nothing.** A kept session can stand for
   days, and a record every pass would carry one bit that the `keep.item`/`unkeep.item` pair
   already establishes. This is the same exception `_retire_finished_sessions` already
   documents at `reconcile.py:1184-1189`.
2. **A kept, live item excluded from the cleanup candidates writes nothing.** Today it would
   write `cleanup.considered`/`skipped` every pass. The decision is re-derivable from the
   keep and session tables at any instant, and `cleanup <id>` still records it on demand.

Reconstruction stays intact. Whether an item was kept, when, by which surface, and which
sessions were open at release can all be answered from the log alone. FR-008's "retirement
after release is distinguishable" is met by joining `session.retire` to the preceding
`unkeep.item.open_sessions` (R10, decided 2026-10-25).

**What happens if it is killed halfway through?**

- **`keep` and `unkeep`**: each is one SQLite transaction inside `ctx.audit.action`, so a
  kill leaves either the row and both records, or the intent record with no row. A rerun is
  idempotent (a redundant keep is reported, a release of nothing is a no-op).
- **Reconciliation**: no new write sits between a check and an act. A kill after the keep is
  placed but before the next pass means that pass reads it. A kill during a pass that has
  already skipped a kept item changes nothing on disk.
- **Release racing a pass**: the pass either reads the keep (and skips) or does not (and
  applies the ordinary rules). Both are correct outcomes of the release.

### IV. Interruption Tolerance

**Pass.** Persistence goes through SQLite transactions. The keep table has no network calls
and no file writes, so it needs no timeouts. Durability across a restart or reboot is the
table's (FR-009). After a reboot the worker is gone, so `reclaim_stale_session`'s keep test,
which sits *inside* its alive branch, falls through to `reclaimed`, and the slot is not
leaked (R3).

### V. Public Code, Unsupported Project

**Pass.** Nothing machine-specific is committed. Examples use `owner/name`, not the private
repository's name in a context that would reveal anything beyond what the spec already
names. There is no migration shim and no compatibility path: migration 016 only adds.

### Operating Constraints

- **Terminal-reachable**: every web control has a CLI verb, and `keeps` is the observable.
- **Outward-facing or irreversible actions**: none added. A keep *withholds* an existing
  automatic act (ending a process, closing a tab, deleting a worktree). It never performs
  one. Releasing it hands the item back to rules that are already on by default (retirement)
  or already configured (cleanup).

### Development Workflow

The feature requires unit tests for every changed unit, and persistence tests with their
failure and interruption paths (list below). The full suite must be green before the feature
is complete.

## Project Structure

### Documentation (this feature)

```text
specs/20261025-083403-keep-session-after-close/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── cli.md
│   ├── web.md
│   ├── capacity.md
│   └── audit.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2, written by /speckit-tasks, not this command
```

### Source code (files touched)

```text
src/robot_army/
├── migrations.py        # SCHEMA_016_SQL, _migration_016, appended to MIGRATIONS
├── models.py            # Keep dataclass beside Hold
├── db.py                # keep accessors; count_kept_sessions_by_repo; list_cleanup_candidates exclusion
├── operations.py        # keep_item, unkeep_item, list_keeps
├── cli.py               # keep/unkeep/keeps parsers, READ_COMMANDS, universal-flag list, dispatch table
├── reconcile.py         # _retire_finished_sessions skip; reclaim_stale_session kept branch; _resolve_orphan_anomalies keep retraction
├── capacity.py          # kept_per_repo, kept, breakdown annotation
├── ordering.py          # repo-cap detail names kept sessions
└── web/
    ├── server.py        # _item_keep_action, two routes
    └── pages.py         # keep control; /active "kept" section; item page control

tests/unit/
├── test_migrations.py           # 016 shape, FK cascade
├── test_keeps.py                # NEW: db + operations + CLI
├── test_session_retirement.py   # keep skips; release retires; 3a pause/hold do not
├── test_window_closing.py       # kept tab stays; tab goes when session exits with keep in place
├── test_cleanup.py              # candidate exclusion; inert keep does not block; cleanup <id> unchanged
├── test_capacity_reporting.py   # breakdown annotation; byte-identical when none
├── test_holds_ordering.py       # repo-cap detail names kept (or a new ordering test)
└── test_web_actions.py          # POST keep/unkeep; refusal recorded; /active kept section

docs/guide/
├── 5-outcome.md         # "Keeping a session after the merge", tab and cleanup interplay
├── 3-selection.md       # capacity reasons name kept sessions; holds/pause still do not stop retirement
├── operating.md         # /active kept section, keep control, orphan_session not raised for kept
├── audit-log.md         # keep.item / unkeep.item, anomaly.resolved reason, the two silences
└── state.md             # item_keeps table
```

**Structure Decision**: the existing single-project layout. No new module. Every change lands
in the module that already owns the behaviour.

## Tests

The development workflow requires failure and interruption paths for persistence and the
state machine, and those are marked **(F)** below.

**`test_keeps.py`** (new)

- `set_item_keep` is idempotent and returns the original `kept_at`. `clear_item_keep`
  returns what it removed, or `None`.
- Deleting the item cascades to its keep (via `purge_simulated`).
- **(F)** An exception inside the `keep_item` transaction leaves no row and an error
  outcome record.
- **(F)** A keep placed against a database with no daemon running is honoured by the next
  reconcile pass.
- `keep` on an unknown item returns exit 1.
- `keep` on a `done`/`abandoned` item with no open row returns `EXIT_PRECONDITION`, with the
  resume line when a session existed.
- `keep` is accepted on `ready`, `active`, `failed`, and on `done` with a live session.
- A redundant keep is reported. `unkeep` of nothing is a no-op with exit 0.
- `unkeep` records `open_sessions`.
- The `keeps` listing shows inert, pending and live keeps with the right session wording.
- The empty listing says so in words.
- `--json` works on all three verbs.

**`test_session_retirement.py`**

- A kept, merged, idle `done` item is not retired, and **no record** is written for it.
- After `unkeep`, the next pass retires it with `signal: merged_pull_request`.
- A kept, unmerged item is not retired past `RETIRE_IDLE_SECONDS`.
- A keep on an `active` item does not change anything before `done`.
- **(3a)** With dispatch paused and the item and repository held, but no keep, the session is
  retired.

**`reclaim_stale_session` / sweeps** (in `test_session_retirement.py` or alongside the #28 tests)

- A kept `done` item with a live worker returns `"left"` and raises no anomaly.
- **(F)** A kept `done` item whose worker is dead returns `"reclaimed"`, so the slot is not
  leaked after a reboot.
- A kept `abandoned` item with a live worker is still `"reported"`.
- A pre-existing `orphan_session` for a now-kept `done` item is resolved with the keep reason.
- An `orphan_session` for an unkept item is untouched.

**`test_window_closing.py`**

- A kept `done` item with a live row keeps its window.
- Once the row closes (the session was exited) with the keep still in place, the window is
  closed.

**`test_cleanup.py`**

- A kept item with a live row is absent from `list_cleanup_candidates`, so no
  `cleanup.considered` record is written.
- A kept item with no live row is present, because an inert keep does not block reclaim.
- `cleanup <id>` on a kept, live item still records `skipped` "still live".

**`test_capacity_reporting.py`** and ordering tests

- The breakdown carries `(1 kept)`, and the components still sum to the total.
- With no kept sessions, the strings are identical to before.
- The repo-cap detail names `1 kept`.
- On the degraded path, the annotation is omitted.

**`test_web_actions.py`**

- POST keep and unkeep succeed and redirect, and the refusal is recorded through `_perform`.
- The cross-origin POST is refused.
- `/active` HTML and JSON list a kept `done` item.
- The item page shows the control.

**Unchanged and expected to stay green**: `tests/unit/test_done_single_writer.py`, because
no new writer of `done` is added. `test_migrations.py` compares against `SCHEMA_VERSION` symbolically, so it
needs only the new 016 shape test. Any CLI or doc-coverage test that enumerates verbs will need the
three new verbs documented, which the guide changes below cover.

## Documentation obligations

Per `CLAUDE.md`'s table:

| Page | Why |
|---|---|
| [`5-outcome.md`](../../docs/guide/5-outcome.md) | The session's ending gains an exception. The tab and cleanup sections each gain one sentence on a kept session. The "identical function under identical guards" paragraph notes the candidate exclusion. |
| [`3-selection.md`](../../docs/guide/3-selection.md) | Capacity reasons can say `kept`. The hold section's "never stops a running session" gains its pair: holds and pause do not keep one either, and a keep does. |
| [`operating.md`](../../docs/guide/operating.md) | The web interface gains the `/active` "kept" section and the keep control. The anomalies section notes that `orphan_session` is not raised for a kept session. |
| [`audit-log.md`](../../docs/guide/audit-log.md) | New actions and the two silences (contract `audit.md`). |
| [`state.md`](../../docs/guide/state.md) | The `item_keeps` table, beside `item_holds` at line 273. |

No configuration key changes, so `configuration.md` and `share/config.example.toml` are
untouched.

## Resolved decisions

- **FR-008 wording (research R10)**, decided 2026-10-25: a retirement after release is
  distinguishable *from the audit log*, by joining `session.retire` to the preceding
  `unkeep.item`'s `open_sessions`. No field on `session.retire` and no soft-deleted keep row.
  The spec's FR-008 is worded to match.
