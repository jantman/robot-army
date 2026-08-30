# Quickstart: proving the slot comes back

**Feature**: [spec.md](./spec.md) | **Contract**: [contracts/slot-reclamation.md](./contracts/slot-reclamation.md)

Four scenarios. Scenario 1 is the issue's own reproduction and is the acceptance test for the
feature; scenarios 2–4 defend the three things that could quietly go wrong.

## Prerequisites

```bash
cd /home/jantman/worktrees/robot-army/issue-28
uv run pytest -q          # baseline: 1714 passed, 1 skipped before any change
```

Scenario 1 can also be run by hand against a real state directory; scenarios 2–4 are automated
only, because they need a controlled `/proc`.

---

## Scenario 1 — Cancel gives the slot back (User Story 1, the reported bug)

The shape of the reproduction, at `effect_level = "local"` with the shipped
`default_repo_max_sessions = 1`:

1. Dispatch two simulated items for the same repository. The first goes `active`; the second is
   held with reason `repo_cap`.
2. `robot-army cancel <first> --force`.
3. `robot-army capacity`.

**Before this feature** — the leak, measured in
[research R1](./research.md#r1--the-leak-reproduces-exactly-as-filed-on-both-reported-routes):

```
capacity : 1 of 5 sessions running
per repository:
  jantman/robot-army   1        <-- slot still held by the cancelled item
```

**Expected after**:

```
capacity : 0 of 5 sessions running
per repository:
  (none)
```

and the second item becomes dispatchable rather than sitting at `repo_cap`.

Then confirm the record is closed and still legible:

```bash
robot-army show <first>     # session shows lost, with an ended time
robot-army log --action state.session | tail -1   # reason names cancellation
```

**Also assert**: this must hold **with no daemon running**. That is the whole point of FR-012 —
the CLI-only rehearsal has nothing sweeping on a timer.

---

## Scenario 2 — Abandon gives the slot back (User Story 2)

Same setup; `robot-army abandon <first>` instead of `cancel`. Expected: identical capacity
outcome, and `robot-army show` reports the item `abandoned` with a `lost` session. Its worktree is
still in place — reclaiming a slot touches nothing on disk (FR-011).

---

## Scenario 3 — A live worker is never swept away (FR-005, the dangerous case)

The one scenario where a wrong implementation causes real harm rather than an inconvenience.

Set up: a **non-simulated** session, `running`, whose pid is alive in `/proc` with a matching
`proc_start`, whose registry entry has a cwd under the worktree root — under an item in
`interrupted`.

Run a reconciliation pass. **Expected**:

| Check | Expected |
|---|---|
| Session row | still `running` — **not** closed |
| `reclaimed` counter | `0` |
| Anomalies | one `orphan_session` for that session id |
| `capacity` total | still `1` — never lower than the number of live workers |

Run the pass a second time. **Expected**: still one anomaly, not two (FR-008).

Today this scenario produces `orphans: 0` and `ANOMALIES: []` — the condition is invisible
([research R4](./research.md#r4--a-live-worker-under-a-non-running-item-is-invisible-today)). The
new anomaly is the visible half of this feature.

---

## Scenario 4 — Slots already leaked are reclaimed, and nothing is discarded (User Story 3)

Start from a database in the state scenario 1 leaves behind **today**: an item in `interrupted`
with a `running`, `pid = 0` simulated session, plus other simulated items and tracked intake cards.

```bash
robot-army reconcile
```

**Expected**:

| Check | Expected |
|---|---|
| Pass summary | `reclaimed 1` — not `checked 0` (FR-009) |
| Session row | `lost`, with an ended time |
| `robot-army capacity` | the repository is below its cap |
| Work items | **all still present** |
| Tracked cards | **all still present** |
| Session history on the item | still shows the session |
| `robot-army resume <item>` | still works, restoring that session's context (FR-010) |
| A second `robot-army reconcile` | `reclaimed 0`, and no repeated audit record |

The last two rows are what separates this from the current escape hatch: today the only command
that clears the row is `purge-simulated`, which offers to delete every simulated work item and all
tracked cards. That command is unchanged; it simply stops being the only way out.

---

## Regression surface

The suite must stay green, and these existing modules are the ones this feature can break:

| Module | Why it is at risk |
|---|---|
| `tests/unit/test_capacity.py` | the counts this feature changes the inputs to |
| `tests/unit/test_web_actions.py` | cancel is reachable from the web interface and goes through the same `operations.cancel` |
| `tests/unit/test_states.py` | asserts the transition tables, which must not gain an edge (C6) |
| `tests/integration/test_dispatch_capacity.py` | per-repository capping end to end |
| `tests/unit/test_spool.py` | the late-exit-record no-op (C5) |
