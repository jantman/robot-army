# Contract: the launch gate

The internal contract every launch path is held to. `contracts/cli.md` and `contracts/web.md`
describe how each surface renders it.

## `ordering.launch_holds`

```python
def launch_holds(
    item: WorkItem,
    *,
    config: Config,
    capacity: CapacitySnapshot,
    paused: bool,
    item_holds: dict[int, Hold],
    repo_holds: dict[str, Hold],
) -> list[tuple[HoldReason, str]]
```

**Pure.** No I/O of any kind — not the database, not the filesystem, not the network. Every
fact it needs arrives as an argument, which is what keeps `ordering` callable from a web page
render.

**Returns** every condition that applies, in `HoldReason` declaration order. Empty means the
launch is permitted by policy.

**Guarantees:**

1. Only `PAUSED`, `HELD`, `CAPACITY_UNOBSERVABLE`, `GLOBAL_CAP` and `REPO_CAP` may appear.
2. The order of the returned list is `HoldReason`'s declaration order, always.
3. `HELD` appears at most once even when both an item hold and a repository hold are in
   force; its detail names both, and says that releasing one leaves the other (FR-006).
4. The detail strings are the ones the queue view renders for the same condition — same
   function, so identity rather than agreement (FR-008).

**Relationship to `_hold_for`:** `_hold_for` calls this first and returns its first element if
there is one; otherwise it continues into `AWAITING_MERGE`, `NOT_ONBOARDED`, `OFF_COLUMN` and
`PREPARATION_FAILED` exactly as today. `ordering.plan`'s output is unchanged for every input
(SC-006).

## `dispatch.check_launch_gate`

```python
def check_launch_gate(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    item: WorkItem,
    force: bool = False,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> None
```

The impure half: it takes the fresh capacity snapshot and the three database reads that
`launch_holds` will not take for itself, then acts on the answer.

**Raises** `DispatchRefused` when a condition applies and `force` is not set. **Returns**
`None` otherwise.

**Guarantees:**

1. The capacity snapshot is taken *here*, on every call. No snapshot may be passed in and none
   is cached (FR-009).
2. On refusal: one `dispatch.refused` record with `outcome="error"`, carrying the item id, the
   first hold's reason and detail, and the calling surface. Nothing is written to the database
   (FR-010, FR-011, FR-013).
3. On override: one `dispatch.forced` record with `outcome="ok"`, carrying the item id and
   **every** applicable hold, then a normal return (FR-023).
4. When nothing applies, nothing is recorded. A permitted launch is the ordinary case and does
   not deserve a line.
5. It never transitions the item, never writes `failure_reason` or `blocked_reason`, never
   touches a worktree, and never posts an outward comment or notification.

## `dispatch.DispatchRefused`

```python
class DispatchRefused(Exception):
    hold: HoldReason | None   # None when the claim was lost rather than a policy refusal
    detail: str
```

**Not** a subclass of `DispatchBlocked`. Every existing `except DispatchBlocked` fails the
item, and a refusal must not (see research R3). Its `str()` is the sentence a surface prints.

## `states.claim_work_item`

```python
def claim_work_item(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    item_id: int,
    target: WorkItemState,
    reason: str,
    extra_columns: dict[str, object] | None = None,
) -> None
```

**Caller must already be inside `db.transaction`** — the same contract
`transition_work_item` states, and for the same reason: the state change and its audit record
commit or roll back together.

**Raises** `LookupError` when the item does not exist, `ClaimLost` when it exists in a state
from which `target` may not be entered.

**Guarantees:**

1. One `UPDATE ... WHERE id = ? AND state IN (...)`. Of any number of concurrent attempts on
   one item, exactly one updates a row (FR-016).
2. The legal sources are derived from `WORK_ITEM_TRANSITIONS`, never written out. An item
   already in `target` is therefore refused, because no state's self-transition is in the
   table (FR-018).
3. Stamp columns and the `state.work_item` audit record are identical to
   `transition_work_item`'s, so nothing downstream can tell which function moved the item
   (FR-019).
4. On `ClaimLost` nothing has been written (FR-017).
5. `transition_work_item` is **not** modified. Re-asserting a held state remains a no-op for
   reconciliation and spool replay (FR-020).

## Order of operations inside `_dispatch_item`

Fixed by this contract, because the order is the guarantee:

```
1. load the item                     LookupError if absent
2. resolve the repository            fails the item if unresolvable (unchanged)
3. check_launch_gate                 ← refuses here; nothing written yet
4. author check                      fails the item (unchanged, cannot be forced)
5. claim_work_item                   ← the atomic claim; ClaimLost → DispatchRefused
6. check_gates                       trust, fingerprint, location; fails the item (cannot be forced)
7. worktree, launch, confirm         unchanged
```

Steps 3 and 5 are the feature. Everything before step 5 writes nothing, which is what makes a
refusal free of consequence.

## What `force` does and does not reach

| Check | Forceable |
|---|---|
| Dispatch paused | **yes** |
| Item hold, repository hold | **yes** |
| Machine-wide session limit | **yes** |
| Per-repository session limit | **yes** |
| Capacity unobservable | **yes** |
| Repository no longer resolves to a clone | no |
| Issue author check | no (FR-024) |
| Onboarding, recorded location, workspace trust, settings fingerprint | no (FR-024) |
| The legal-transition table | no (FR-025) |
| The atomic claim | no (FR-025) |

The line is the author's own policy on one side and safety on the other. `force` moves the
author past decisions the author made; it moves nobody past decisions made about who may run
code in the author's checkout.

## Caller obligations

- **`select_and_dispatch`** passes no `force`. It catches `DispatchRefused`, records it, and
  ends the pass — the second snapshot can legitimately disagree with the planner's when a
  session starts outside the system between them (research R9).
- **`operations.resume` / `restart`** pass `force` through unchanged and render
  `DispatchRefused` as `EXIT_PRECONDITION` with the reason. They must not convert it into a
  generic failure.
- **The web** calls the gate a second time in the request thread, before handing to the
  worker, so the refusal is visible in the response (FR-015). It never passes `force`.
