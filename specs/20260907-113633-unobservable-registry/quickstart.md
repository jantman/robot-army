# Quickstart: Validating the Observability Guard

**Feature**: `specs/20260907-113633-unobservable-registry/`

Everything here runs offline against synthetic registry and `/proc` trees, exactly as Phase 0's
measurements did. Nothing needs GitHub, a running daemon, or a real Claude session.

```bash
uv sync
uv run pytest                     # the whole suite must pass
```

---

## The one-line proof

The defect and the fix are both visible in a single number: how many of three running items a
pass interrupts when the registry directory is not there.

```bash
uv run pytest tests/unit/test_registry_observability.py -q
```

Before this feature that number is 3. After it, it is 0 — and it is still 3 when the directory
is present and empty, which is the half of the property that is easy to lose.

---

## Scenario 1 — A registry nobody can read does not interrupt everything (US1)

**Setup**: three `active` items, each owning one `running` session with a real process
identifier and a real `proc_start`; a registry directory that does not exist; an empty `/proc`.

**Run**: one `reconcile.reconcile` pass.

**Expect**:

- all three items still `active`, all three sessions still `running`
- `result.interrupted == 0`
- `result.liveness_withheld == 3`
- one `registry_unobservable` anomaly, its `detail.reason` naming the absent directory

**Then repeat with each of the other unusable conditions** and expect the same outcome:

| Condition | How to build it |
|---|---|
| directory absent | do not create it |
| directory unlistable | create it, `chmod 000` (restore the mode before the test ends) |
| unrecognised version | one file written with `version="9.9.9"` |
| partly unrecognised | one file on a known version, one on `"9.9.9"` |

The last row is the one that separates this rule from `capacity.py`'s
([contracts/observability-guard.md](./contracts/observability-guard.md) C2): a usable entry came
back, and the pass still declines.

## Scenario 2 — Registry-independent conclusions survive (US1, FR-004)

**Setup**: the same unobservable registry, plus

- an `active` item with **no session record at all**
- an `active` item whose session has **no process identifier** (`pid` `0`)

**Expect**: the first is `interrupted` — that conclusion comes from the database, not the
registry — and the second is counted in `skipped_never_real`, untouched. Neither is affected by
the guard, because every branch that reaches them returns before it.

## Scenario 3 — A blind pass does not hand back slots that are still taken (US2)

**Setup A — the stale-row sweep**: a `done` work item with one `running` session row still open,
and an unobservable registry.

**Expect**: `result.reclaimed == 0`, the row still `running`, `liveness_withheld` incremented.
With an empty-but-present directory instead, `result.reclaimed == 1` and the row `lost` —
today's behaviour, unchanged.

**Setup B — the superseded sweep**: an `active` item owning two open session rows, and an
unobservable registry.

**Expect**: `result.superseded == 0` and both rows still `running`. With an empty-but-present
directory, the earlier attempt is closed `lost` exactly as it is today.

**Setup C — capacity**: take a `capacity.snapshot` before and after a blind pass.

**Expect**: the same total. This is SC-002, and it is the point of the whole story — a row closed
while its worker lives is a slot the cap will hand out twice.

## Scenario 4 — A pass that could not see says so, once (US3)

**Run** the blind pass sixty times without changing anything.

**Expect**:

- exactly one open `registry_unobservable` anomaly, not sixty
- it appears in `uv run robot-army anomalies`, and the kind appears in that command's "kinds this
  system can raise" line
- every `reconcile.pass` record carries `registry_observable`-style detail: `directory_missing`
  true, and `liveness_withheld` equal to the number of rows in flight

**Then make the registry readable** and run one more pass.

**Expect**: the anomaly is resolved and gone from the listing with no maintainer action, and the
pass reaches whatever conclusions the now-visible registry supports.

## Scenario 5 — An idle machine is still an idle machine (US4)

**Setup**: an `active` item whose session process is gone, and a registry directory that exists
and is empty.

**Expect**: `interrupted`, session `lost`, `liveness_withheld == 0`, and no anomaly of this kind.
This is the safety sweep #33 exists for, and it must be untouched.

```bash
uv run pytest tests/unit/test_session_liveness.py tests/integration/test_reconcile_pass.py -q
```

Those two modules are the regression fence for it.

## Scenario 6 — Abandoning while blind (US2, R7)

**Setup**: an `active` item with an open session row; an unobservable registry.

**Run**: `uv run robot-army abandon <id>`.

**Expect**: the item is abandoned, the session row is **left open**, and the output says so and
why. A later reconciliation pass with a readable registry settles the row.

---

## The whole-suite check

```bash
uv run pytest
```

The suite is the contract for User Story 4: every existing assertion about reconciliation was
written against an empty-but-present registry directory, so any of them breaking means the guard
is firing where the observation was usable.
