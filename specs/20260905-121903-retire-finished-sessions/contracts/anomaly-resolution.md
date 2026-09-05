# Contract: anomaly resolution

Normative. Scope is deliberately one anomaly kind.

## A1 — When it runs

| | |
|---|---|
| Caller | `reconcile.reconcile()`, once per pass, as `_resolve_orphan_anomalies` |
| Position | last among the detectors, **after** `_orphan_sweep` |
| Population | `kind = 'orphan_session' AND acknowledged_at IS NULL AND resolved_at IS NULL` |
| Bound | the number of open orphan anomalies. One `/proc/<pid>/stat` read each |

Running after `_orphan_sweep` means what it reports describes the pass as it leaves things — the
argument `_sweep_transcripts` already carries for its own position. The two cannot fight: the sweep
raises only for live processes and this resolves only dead ones.

## A2 — The decision

| # | Condition | Outcome |
|---|---|---|
| 1 | `detail` is not a JSON object, or has no `pid` | LEAVE, permanently. "We could not check" must never read as "it is fine" |
| 2 | `procinfo.is_alive(detail["pid"], detail.get("proc_start"))` is `True` | LEAVE — still true, and not duplicated (FR-024) |
| 3 | otherwise | **RESOLVE** |

Rule 2 carries FR-024's second half for free: `is_alive` compares `/proc/<pid>/stat` field 22
against the recorded start time, so a recycled pid — the number reused by an unrelated process —
answers `False` and the anomaly resolves. Identity, not the number, is what is being asked about.

A row whose `proc_start` is `NULL` degrades to a bare existence check, which is `is_alive`'s
documented behaviour and is unchanged here. No such row exists on the machine; both raisers write
`proc_start` (R9).

## A3 — Resolving

Inside one `db.transaction`:

1. `db.resolve_anomaly(conn, anomaly.id)` — stamps `resolved_at`, guarded by
   `WHERE resolved_at IS NULL` so a repeated pass changes nothing and returns `False`.
2. `audit.record("anomaly.resolved", ...)` carrying `anomaly_id`, `kind`, `entity_id`, `pid`,
   `proc_start` and `reason="the process named is no longer running"`.

Both commit together. This is where FR-022's "on what evidence" lives: the audit record is the
reconstruction path, which is why no `resolved_reason` column exists (R8).

## A4 — Visibility

`db.list_anomalies(unacknowledged_only=True)` filters on
`acknowledged_at IS NULL AND resolved_at IS NULL`. That single change makes all three consumers
correct with no edit to any of them: `operations.anomalies` (the CLI), `operations.status` and
`web/pages.py`.

`robot-army anomalies --all` still shows everything, and a resolved row must be **visibly
distinguishable** from an acknowledged one in that listing — they are different facts and the whole
reason `resolved_at` is not `acknowledged_at`.

## A5 — The index

Covered by [data-model.md](../data-model.md). Restated here because getting it wrong is silent: the
partial unique index must become
`WHERE acknowledged_at IS NULL AND resolved_at IS NULL`. A resolved row left inside the index would
block the same condition from ever being recorded again if it recurred.

A test must prove the recurrence: raise, resolve, raise again, and assert a **second row** exists.

## A6 — Scope

Only `orphan_session`. `stale_socket`, `prunable_worktree`, `no_transcript`, `dispatching_timeout`
and every other kind keep today's behaviour exactly: cleared by acknowledgement and nothing else.
This was the clarification answer, and widening the mechanism to kinds nobody asked about is the
speculative generality Principle I forbids. A test asserts a non-`orphan_session` anomaly whose
condition has passed is still listed.
