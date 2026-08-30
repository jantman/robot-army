# Quickstart — Validating Liveness Reconciliation

Five scenarios. Scenario 1 is the defect from issue #33. Scenario 2 is the invariant that must
survive the fix. Scenario 3 is the superseded-attempt gap Phase 0 found. Scenario 4 is the
capacity consequence. Scenario 5 re-derives 001's terminal-death rehearsal, which no longer
describes what this machine does.

Scenarios 1–4 are reproducible without a real worker; scenario 5 needs one.

## Prerequisites

```bash
uv sync
uv run pytest -q          # baseline: 1780 passed, 1 skipped at 15bf843
```

---

## Scenario 1 — A dead session at `no-remote` is noticed

**Validates**: FR-001, FR-004, FR-005, FR-013, US1. **Was**: the item stayed `active` forever.

Set `effect_level = "no-remote"`, dispatch an item, then kill its worker by any means and force
a pass:

```bash
uv run robot-army status                 # note the item is `active`, session `running*`
kill -9 <worker-pid>
uv run robot-army reconcile
uv run robot-army show <item>
```

**Expected**: item `interrupted`, session `lost`, and the pass summary reports
`interrupted: 1`. Before this change the same sequence reported `checked: 1, interrupted: 0` and
left the item `active` against a dead pid.

Repeat at `live` and confirm the outcome is **identical** — FR-013 requires that level to be
untouched.

---

## Scenario 2 — Rehearsing at `plan` and `local` stays quiet

**Validates**: FR-006, US2. **This is the regression that would matter most.**

At `plan`, then at `local`, dispatch an item and reconcile repeatedly:

```bash
uv run robot-army reconcile && uv run robot-army reconcile && uv run robot-army reconcile
uv run robot-army status
```

**Expected**: the item stays `active` across every pass, its session stays `running`, and each
pass reports `skipped_never_real: 1` — the figure that distinguishes "skipped" from "checked and
found alive".

**Failure mode to watch for**: if the discriminator were wrong, every simulated item would be
marked `interrupted` on the very next pass. That is the failure the original `dry_run` skip was
written to prevent, and the reason it was written the way it was.

---

## Scenario 3 — A superseded attempt's ghost is reported

**Validates**: FR-011, FR-017, FR-018, FR-019, US3. **Was**: reported by nothing at all.

Needs an item with two open sessions — resume or restart an item whose first worker survives:

```bash
uv run robot-army resume <item>          # creates attempt 2
# attempt 1's worker is still alive
uv run robot-army reconcile
uv run robot-army anomalies
```

**Expected**: one `orphan_session` anomaly naming attempt 1's session, with `attempt` and
`work_item_id` in its detail. Attempt 1's record is **still open** — deliberately, because the
slot really is taken. The item's own state follows attempt 2 alone.

Then reconcile again. **Expected**: still exactly one anomaly, not two — the open-anomaly index
absorbs re-detection, and `_orphan_sweep` declines the worker because C3 left its record
`running`.

If attempt 1's worker is instead **dead**: its record becomes `lost`, the pass reports
`superseded: 1`, and the item stays `active` on attempt 2. A resumed item must never be
interrupted by the ghost of the attempt the resume replaced.

---

## Scenario 4 — The capacity slot comes back

**Validates**: FR-007, SC-002.

With `default_repo_max_sessions = 1` (the shipped default) and a second item queued for the same
repository:

```bash
uv run robot-army capacity                # 1 of 1 — the dead session still counted
uv run robot-army reconcile
uv run robot-army capacity                # 0 of 1
uv run robot-army status                  # the held item is no longer `repo_cap`
```

**Expected**: the slot is released by the same pass that closed the record, and the next eligible
item dispatches on the following tick rather than being held forever.

---

## Scenario 5 — Re-derive 001's terminal-death rehearsal

**Validates**: FR-014, US4. **Needs a real worker at `no-remote` or `live`.**

001 quickstart scenario 4 says of the orphan case: *"The worker keeps running, reparented, while
dtach tears down its socket."* On this machine that is no longer what happens — killing the
wrapper took `claude` with it, almost certainly via SIGHUP when dtach's pty went away.

```bash
# From a DIFFERENT terminal than the session's
kill -9 <wrapper-pid>
ps -o pid,ppid,cmd -p <worker-pid>       # did the worker actually survive?
uv run robot-army reconcile
uv run robot-army anomalies
```

**Record what actually happens**, then update 001's scenario 4 to match. Two outcomes are
acceptable and they lead to different edits:

- **The worker survives**: the scenario stands as written; confirm the `orphan_session` anomaly
  appears and note that reconciliation now also reaches this at `no-remote`.
- **The worker dies with the wrapper** (observed during the #1 verification round): the scenario
  can no longer produce an orphan this way. Rewrite it to state that, and point the orphan case
  at scenario 3 above, which produces one by a route that works.

Either way the item must not be left `active` against a dead session — that outcome is scenario
1 and is independent of which way this goes.

---

## What CI cannot settle

Scenario 5 only. It needs a real terminal, a real dtach socket and a real worker, and no fake can
answer whether SIGHUP propagates on this machine — that is the assumption under test. Append the
result to issue #1's verification round.

Scenarios 1–4 are covered by `tests/unit/test_session_liveness.py` and
`tests/integration/test_reconcile_pass.py`; running the suite is not a substitute for walking
them once against a real database, but it is a substitute for walking them on every change.
