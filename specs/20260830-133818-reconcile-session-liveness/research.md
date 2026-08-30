# Phase 0 Research — Liveness Is Checked Wherever the Session Is Real

**Feature**: `specs/20260830-133818-reconcile-session-liveness/`
**Baseline**: `main` at 15bf843 (includes #28 / PR #43 and #34).

Everything below was **measured against this checkout**, not inferred. Probes were throwaway
files under `tests/unit/` using the repository's own fixtures; they were deleted after
measurement and the tree was verified pristine (`git diff` empty, 1780 passed / 1 skipped).

Two findings — **R7** and **R8** — contradicted the specification as written. Both were taken
to the maintainer with their measurements; the spec has since been amended. Resolutions are
recorded at the end.

---

## R1 — `no-remote` is the only level where the record lies about the host

Read from the wired table (`effects.REAL_AT`) rather than from prose:

```
  plan       session_host_real=False  rows flagged dry_run=True
  local      session_host_real=False  rows flagged dry_run=True
  no-remote  session_host_real=True   rows flagged dry_run=True   <-- sole disagreement
  live       session_host_real=True   rows flagged dry_run=False
```

**Decision**: the skip must key on the session host, not on the level. Confirms FR-001.

---

## R2 — The record already contains the answer, and dispatch already trusts it

`SimulatedSessionHost.confirm_session` returns, measured:

```
  pid=0  proc_start=None  status='simulated'
  entry.alive()              -> False
  procinfo.is_alive(0, None) -> False
```

`dispatch.py:920` writes `pid=entry.pid` from whatever the **boundary** returned, so the stored
`pid` is `0` exactly when the host was simulated and a real pid when it was real — set through
the boundary, with no effect level consulted. `dispatch.py:918` already uses `if entry.pid` for
precisely this question when deciding whether to read a systemd scope.

**Decision**: use the absence of a process identifier as the discriminator. **No schema change,
no migration, no new column.** This satisfies FR-002 (the fact is on the record from creation)
and FR-008 (rows written before this change are already classified correctly, because the value
was always written this way).

**Alternatives considered**: a `simulated_host` column (rejected — a migration and a backfill to
store a fact the row already carries, against Principle I); re-deriving the level in
`reconcile` (rejected — forbidden, see R6).

---

## R3 — The bug, reproduced in-tree across all three record shapes

An `active` item, a `running` session, an empty registry and an empty `/proc` — dead by every
measure the system has:

```
  live       dry_run=0 pid=4321 -> item=interrupted  session=lost     checked=1 interrupted=1
  no-remote  dry_run=1 pid=4321 -> item=active       session=running  checked=1 interrupted=0
  plan/local dry_run=1 pid=0    -> item=active       session=running  checked=1 interrupted=0
```

Row 2 is the defect. Row 3 is the behaviour that must be preserved. Note `checked=1` in every
row, including the two that examined nothing — the misreporting FR-009 is about.

---

## R4 — The candidate produces all three required outcomes

Replacing the `session.dry_run` skip with a skip on the absent identifier, same probe:

```
  live       dry_run=0 pid=4321 -> item=interrupted  session=lost     interrupted=1
  no-remote  dry_run=1 pid=4321 -> item=interrupted  session=lost     interrupted=1   <-- fixed
  plan/local dry_run=1 pid=0    -> item=active       session=running  interrupted=0   <-- preserved
```

Confirms FR-005 and FR-006 are simultaneously satisfiable, and FR-013 (live unchanged).

---

## R5 — Full-suite impact: two failures, both fixture artifacts

With the candidate applied: **2 failed, 1778 passed, 1 skipped.** Both failures are in
`tests/unit/test_reconcile.py` and both trace to one helper:

```python
def active_item(conn, *, session_id="s-1", pid=4242, proc_start="777", **kwargs):
```

It builds `dry_run=True` rows carrying a **real-looking pid** — a shape the production dispatch
path never creates, because a simulated host always yields `pid=0`. The two tests therefore
assert against a record that could not exist.

Correcting the two call sites to `pid=0, proc_start=None` — the shape a simulated session
actually has — restores both: **24 passed** in that module, with no change to either
assertion.

**Decision**: the FR-055 guard test is kept and re-expressed truthfully, not deleted. A new case
must cover the `no-remote` shape (`dry_run=1` with a real pid), which nothing covers today —
which is why the defect shipped.

---

## R6 — The effect level is mechanically barred from `reconcile.py`

`tests/unit/test_effects.py::test_only_effects_py_knows_the_effect_level_exists` (T147) greps
every module outside a six-file allow-list for the identifier `EffectLevel`. `reconcile.py` is
not exempt. A sibling test permits a `dry_run` flag that *marks a record* while forbidding code
that *selects an implementation*, and names `reconcile.py` as a legitimate user of the former.

**Decision**: FR-003 is already enforced by a test, and the R2 approach satisfies it — the
discriminator is a column value, not a level.

---

## R7 — ⚠️ Contradicts User Story 3

US3 asserts that below `live` an orphaned worker is invisible, and that this feature restores it.
**Measured, that is false.** Pre-fix, at `no-remote`, with a live worker under the worktree root
whose session id matches no row:

```
  UNMATCHED ghost -> item=active  orphans=1     <-- already reported, pre-fix
```

The orphan anomaly is raised today. The fix changes the *item's* state, not the orphan:

```
  UNMATCHED ghost -> item=interrupted  orphans=1   (post-fix)
```

The guard's **actual** blind spot is different, and this feature does not close it. When a live
worker's session id matches a row that is still `running` but which the active-item sweep never
examines — because `db.latest_session_for_item` returns only the **newest attempt**, so an
earlier attempt's still-running session is never visited by anything:

```
  MATCHED old attempt -> item=active       orphans=0   (pre-fix)
  MATCHED old attempt -> item=interrupted  orphans=0   (post-fix — still unreported)
```

This is a genuine gap, distinct from #28 (whose sweep only visits sessions under items outside
`dispatching`/`active`; here the item *is* `active`). It is also distinct from this feature: no
change to the `dry_run` skip reaches it, because the sweep never loads that row at all.

**Decision required** — see "Open decisions" below. US3 cannot be planned as written.

---

## R8 — ⚠️ Quantifies the edge case the spec deferred

The spec lists the unreadable-registry hazard as out of scope, noting it is "inherited from
`live` unchanged". Measured, that inheritance is real and the exposure is newly reachable:

```
  live/missing_dir      -> interrupted=3  ['interrupted','interrupted','interrupted']   (pre-fix)
  live/empty_dir        -> interrupted=3  ['interrupted','interrupted','interrupted']   (pre-fix)
  no-remote/missing_dir -> interrupted=0  ['active','active','active']                  (pre-fix)
  no-remote/empty_dir   -> interrupted=0  ['active','active','active']                  (pre-fix)
```

Post-fix, all four rows read `interrupted=3`. So at `live` a vanished registry **already** marks
every active item interrupted today; at `no-remote` the `dry_run` skip has been masking it, and
this feature removes the mask.

Two further observations:

- `reconcile.py` never consults `scan.degraded` or `scan.directory_missing`, while `capacity.py`
  acts on both (`capacity.py:107-109`) — and `sessions.RegistryScan` documents
  `directory_missing` as distinguished precisely because one case means "the machine is idle"
  and the other means "this observation is worthless".
- The two are indistinguishable to reconciliation today: `missing_dir` and `empty_dir` produce
  identical outcomes at every level.

**Decision required** — see below. Note the tension: adding a guard would improve `live`
behaviour, which **FR-013 currently forbids**.

---

## R9 — The pass counters

`ReconcileResult` after #28 carries `checked, interrupted, dispatching_failed, closed_done,
reclaimed, orphans, stale_sockets, prunable, cleaned, retained, speckit_phase_changes`.
`checked` is incremented once per `active` item **before** any skip, so a pass that examined
nothing reports the same `checked` as one that examined everything (visible in R3).

**Decision**: add one counter for sessions skipped as never-real, incremented at the skip.
Satisfies FR-009 without renaming an existing figure, which would break `docs/logging.md` and
the JSON consumers.

---

## R10 — The combined candidate, measured

With the R2 discriminator **and** a sweep over each active item's non-current open sessions
(reusing `db.list_sessions_for_item`, which already exists — no new query):

```
  OLD-ALIVE -> item=interrupted  rows={'s-old':'running','s-new':'lost'}
               superseded=1 orphans=0 anomalies=[('orphan_session','s-old')]
  OLD-DEAD  -> item=active       rows={'s-old':'lost','s-new':'running'}
               superseded=1 orphans=0
  SIM-MULTI -> item=active       rows={'sim-old':'running','sim-new':'running'}
               superseded=0 skipped_never_real=1
```

All three required outcomes hold: the live superseded worker is reported and its row left open
(#28's under-count rule); the dead one is closed and its slot returned; simulated multi-attempt
rows are untouched. `orphans=0` in the first row is the no-double-report property — the anomaly
is raised at the new site and `_orphan_sweep`'s existing guard then declines it, exactly the
arrangement #28 chose.

**Full-suite impact of the combined change: `2 failed, 1778 passed, 1 skipped` — the same two
fixture artifacts from R5 and not one more.** The superseded sweep breaks nothing.

**Decision**: raise the anomaly at the new site and leave `_orphan_sweep` byte-identical, for
the reason #28 gave and which still holds: narrowing that guard would change cases neither
feature has characterised.

---

## Resolved decisions

| # | Question | Resolution |
|---|---|---|
| **D1** | US3 is false as written (R7). | **Redefine and fix here.** US3 is now the superseded-attempt gap; FR-011 is restated and FR-017–FR-019 added. Measured in R10; costs no additional test breakage. |
| **D2** | The fix extends `live`'s mass-interruption-on-vanished-registry to `no-remote` (R8). | **Ship matching `live`; file separately.** FR-013 is preserved, the two levels are made to behave alike, and the hazard is tracked as **#44**. Recorded as a deliberate acceptance in the spec's edge cases. |

Both were put to the maintainer with the measurements above rather than decided here.
