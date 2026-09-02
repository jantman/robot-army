# Phase 1 Data Model: Per-Repo Concurrency and Wait-for-Merge

**No schema change. No migration. No new table, column, or index.**

Everything the gate needs already exists in `work_items.state`. What follows describes the
in-memory shapes that change and the one derived set that is computed on read.

## Configuration

### `DispatchConfig` (extended)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `order` | `str` | `"oldest-first"` | unchanged |
| `default_repo_max_sessions` | `int` | `1` | unchanged — the issue's first item, already shipped |
| `wait_for_merge` | `bool` | `False` | **new.** The global default. Off, so no existing installation changes behaviour. |

Both new and existing keys live in `_STRICT_KEY_SECTIONS`' `dispatch` set, so `wait_for_merg`
is refused at load with the key named.

### `RepoConfig` (extended)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_sessions` | `int \| None` | `None` | unchanged; `None` inherits |
| `wait_for_merge` | `bool \| None` | `None` | **new.** `None` inherits `[dispatch] wait_for_merge`. The distinction is kept rather than resolved at parse time so a surface can say *which* setting is responsible — the same reason `max_sessions` and `speckit` keep theirs. |

`wait_for_merge` joins `_REPO_KEYS`, where an unknown key is already an error rather than a
warning.

`repos.resolve` carries the field through both of its construction branches. It is not what
the gate reads — the gate reads `Config.effective_wait_for_merge` — but a resolved
`RepoConfig` that silently dropped a field the author set would be a trap for the next
reader.

### `Config.effective_wait_for_merge(key) -> (bool, bool)`

`(value, explicit)`, shaped exactly like `effective_repo_cap` and for the same reason: the
second element is what lets a surface distinguish *you chose this* from *this is what you
get*. There is no `min()` counterpart here — a boolean has no coarser global ceiling to be
clamped against, because `[dispatch] wait_for_merge` **is** the global setting rather than a
separate machine-wide limit.

## Derived, never stored

### Unfinished work per repository

```
UNFINISHED_STATES = {dispatching, active, awaiting_review, interrupted, failed}
```

Computed once per `ordering.plan` call as `repo_key -> [WorkItem, ...]` from a single
`db.list_work_items(include_simulated=True, states=UNFINISHED_STATES)` scan.

The complement is the load-bearing half: `discovered` and `ready` are **excluded** because
they are pre-dispatch, and including `ready` would make a repository's queue hold itself
(R1). `done` and `abandoned` are excluded because they are terminal — and their exclusion is
the whole release mechanism.

Simulated rows are included, for the reason `capacity.snapshot` includes them in its own
count: a dry run exists to rehearse the real behaviour, and a gate that ignored simulated work
would rehearse the wrong thing. No outward request is made either way, so nothing about
dry-run isolation changes.

### `HoldReason` (extended)

Declaration order **is** the precedence. The new member is inserted, not appended:

| Rank | Reason | Scope |
|---|---|---|
| 1 | `paused` | whole queue |
| 2 | `capacity_unobservable` | whole queue |
| 3 | `global_cap` | whole queue |
| 4 | `repo_cap` | one repository |
| 5 | **`awaiting_merge`** | **one repository** |
| 6 | `not_onboarded` | one item |
| 7 | `preparation_failed` | one item |

Ranks 1–3 are `dispatch._GLOBAL_HOLDS` — they end the pass. Ranks 4–7 skip the item and leave
the rest of the queue moving. `awaiting_merge` is deliberately in the second group (R5, FR-007).

Its `detail` names the repository, the unfinished item's issue number, and that item's current
state, so the author can act without opening the log (SC-003). For example:

```
repository jantman/example: #41 is awaiting_review and not yet merged
```

### `FastForwardResult`

Returned by the new boundary method; never stored, written into the existing
`worktree.prepare` audit outcome.

| Field | Type | Meaning |
|---|---|---|
| `outcome` | `str` | one of `updated`, `already_current`, `skipped`, `failed` |
| `reason` | `str \| None` | why, for `skipped` and `failed`; `None` otherwise |
| `before` | `str \| None` | the branch's sha before, when it could be read |
| `after` | `str \| None` | the branch's sha after, when the update happened |

Four outcomes rather than a boolean, for the reason `VersionControl.remote_branch_head`'s
docstring gives about its own three answers: *declined, and here is why* and *did nothing*
are different facts, and the author needs the first when wondering why their clone is behind.

## State machine

**Unchanged.** `WORK_ITEM_TRANSITIONS` and `SESSION_TRANSITIONS` are untouched. This feature
adds no state and no transition; it only declines to start one, and the declining is not
persisted.
