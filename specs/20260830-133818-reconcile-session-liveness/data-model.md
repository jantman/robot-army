# Data Model — Liveness Is Checked Wherever the Session Is Real

**No schema change. `SCHEMA_VERSION` does not move. No migration, no backfill.**

Every column this feature reads is one the `sessions` table has carried since milestone 001.
What changes is which column reconciliation *asks*.

## The two columns, and why one of them is the wrong question

| Column | Written by | Means | Suitable as the liveness discriminator? |
|---|---|---|---|
| `dry_run` | `dispatch`, from `EffectLevel.is_simulated` | "the effect level was not `live`" | **No.** True at `no-remote`, where the session host is real |
| `pid` | `dispatch`, from `SessionHost.confirm_session().pid` | the process the host actually started, or `0` from the simulated host | **Yes.** Non-zero exactly when a real process existed |

The `pid` value arrives through the boundary rather than from a branch on the level, which is
what keeps the effect level inside `effects.py` where FR-053 and T147 require it. `dispatch.py`
already treats the same column as the same question when it decides whether to read a systemd
scope: `scope = procinfo.systemd_scope(entry.pid, ...) if entry.pid else None`.

Measured (research.md R2): `SimulatedSessionHost.confirm_session()` returns `pid=0`,
`proc_start=None`, `status='simulated'`; `procinfo.is_alive(0, None)` is `False`.

### Why old rows need no backfill

The value was always written this way. A row created at `plan` or `local` before this change
already carries `pid = 0`; a row created at `no-remote` or `live` already carries a real one. The
first pass after the change classifies every existing row correctly (FR-008), which is the whole
argument for using this column instead of adding one.

### The one shape that is neither

`pid` is `NULL` between `insert_session` (which does not set it) and confirmation (which does).
A row in that window belongs to an item in `dispatching`, not `active`, so the liveness sweep
never sees it — the item and the session are moved to `active`/`running` in the same transaction
that writes the pid. `NULL` and `0` are nonetheless treated alike: both mean "no process to
find", which is the truthful reading of each.

## The record shapes, and what the rule does with them

| Level | `dry_run` | `pid` | Liveness checked? | Outcome when the process is gone |
|---|---|---|---|---|
| `plan` | 1 | 0 | no | untouched; counted as `skipped_never_real` |
| `local` | 1 | 0 | no | untouched; counted as `skipped_never_real` |
| `no-remote` | 1 | real | **yes (changed)** | session `lost`, item `interrupted` |
| `live` | 0 | real | yes | session `lost`, item `interrupted` — unchanged |

## Session state transitions

**No edge is added to either transition table.** `running → lost` and `starting → lost` are
already legal, and are the only transitions this feature performs. Confirmed by #28, which
established the same for its own sweep.

| From | To | When |
|---|---|---|
| `running` / `starting` | `lost` | a real session with no live process and no exit record |
| `running` / `starting` | `lost` | a **superseded** attempt whose process is gone |
| `running` / `starting` | *(unchanged)* | a superseded attempt whose process is **alive** — reported, never closed |

The third row is the one that matters. Closing a record whose worker can be seen would report
fewer running sessions than exist, oversubscribing the quota the capacity cap protects. An
under-count is the only capacity error that causes real harm, so the rule declines to close and
raises an anomaly instead — the same judgement #28 reached for its own middle branch.

## Work item state transitions

| From | To | When |
|---|---|---|
| `active` | `interrupted` | its **current** attempt is a real session that is gone |

A superseded attempt never moves the item (FR-018): the item's state is decided by its current
attempt alone. Without that rule, a resumed item would be interrupted by the ghost of the
attempt the resume replaced.

## Anomalies

No new kind. `orphan_session` already means "a live worker nothing accounts for" and is already
in `ANOMALY_KINDS`. The partial unique index on open anomalies absorbs re-detection, so a 60-second
loop cannot turn one orphan into 1,440 rows a day.

New `detail` keys for the superseded case: `attempt` and `work_item_id`, so the record says
*which* attempt's ghost this is — the fact that distinguishes it from the cases #28 and
`_orphan_sweep` already report.

## Pass counters

`ReconcileResult` gains two fields and two `summary()` keys. Nothing existing is renamed, which
would break `docs/logging.md` and the JSON consumers.

| Counter | Incremented when |
|---|---|
| `skipped_never_real` | a session is skipped because it never had a process |
| `superseded` | a superseded open record is closed or reported |

`checked` keeps its current meaning — one per `active` item visited — so the pair
`checked` / `skipped_never_real` is what makes "examined everything" distinguishable from
"examined nothing", which is the misreading FR-009 exists to prevent.
