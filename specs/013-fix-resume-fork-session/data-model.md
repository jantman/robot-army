# Phase 1 Data Model: `resume` That Actually Resumes

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

**No schema changes.** No new tables, no new columns, no migration. Every fact this feature
needs is already recorded; the defect is that one branch never read one of them. This document
describes the entities as they stand and the two behavioural rules being added.

---

## Entities as they already exist

### Work item (`work_items`)

Unchanged. Relevant states for this feature:

| State | Meaning here |
|---|---|
| `interrupted`, `awaiting_review` | The two states a resume may start from (see research R7). |
| `dispatching` | A launch is in flight and **dispatch owns settling the item**. |
| `active` | Confirmed running; the spool now owns the item's fate. |
| `failed` | Where a failed launch must land, at the moment of detection. |

Relevant columns: `state`, `failure_reason`, `dispatching_at`, `ended_at`, `worktree_path`,
`branch`.

### Session (`sessions`)

Unchanged. One row per attempt. Relevant columns: `session_id` (the id we chose), `attempt`,
`state`, `exit_code`, `signal`, `launch_argv`, `ended_at`.

A resume creates a **new row** with `attempt = next_attempt(item)`; the session it restores keeps
its own row untouched. This already matches how the worker behaves — research R2 confirmed the
forked conversation is written to a transcript under the new id while the original is preserved.

### Exit record (spool file)

Unchanged. Written by the wrapper, drained by the daemon. Its defining property for this feature
is one of *timing*: it may land before, during, or after the confirmation window. Landing early
is a normal condition, not an error.

### Launch command (`sessions.launch_argv`, composed by `build_launch_plan`)

Not a stored entity so much as a composed value, but it is the thing whose correctness this
feature redefines: **correct means "the worker binary accepts it"**, not "the list the code
intended to build". That redefinition is what FR-013–FR-016 exist to hold in place.

---

## Rule 1 — Launch shape (FR-001 – FR-004)

`build_launch_plan` composes `worker_argv`. The restoring form gains one token:

| Condition | Composed form |
|---|---|
| `resume_session_id is None` | `<binary> --session-id <new> …` — **byte-for-byte unchanged** (FR-004) |
| `resume_session_id is not None` | `<binary> --session-id <new> --resume <prior> --fork-session …` |

Invariants:

- The requested `--session-id` is honoured by the worker in both forms (research R2), so the
  session row's `session_id` continues to name the process that actually ran (FR-003).
- Flag order is unchanged apart from the appended token; `--fork-session` is positioned with
  `--resume`, which is the only flag it relates to.

## Rule 2 — Confirmation outcome (FR-005 – FR-010)

The `entry is None` branch of `dispatch_item` currently has one outcome. It gains a second,
selected by re-reading the session row that the daemon may have updated from another process:

| Session state when confirmation elapses | Session transition | Work item | Failure reason |
|---|---|---|---|
| `starting` / `running` (nothing recorded) | → `lost` | → `failed` | "was not confirmed within Ns…" (unchanged, FR-006) |
| `exited_error` (already recorded) | **none** — the recorded exit stands | → `failed` | names the exit status (FR-005, FR-007) |
| `exited_clean` (already recorded) | **none** | → `failed` | names the clean exit and that it preceded confirmation |
| `lost` (already recorded) | **none** | → `failed` | the recorded reason |

The read must happen inside the confirmation branch, not be carried from before the launch: the
whole point is that another process may have written since.

Every terminal case fails the item. `dispatching` has only two legal exits — `active` and
`failed` — so `classify_exit`'s `awaiting_review` and `interrupted` targets are unreachable
here, and they would be untrue anyway: a session that ended before it registered did not do
the work. See [contracts/confirmation-outcome.md](./contracts/confirmation-outcome.md).

### State-machine consequences

`SESSION_TRANSITIONS` is **not** modified. Adding `EXITED_* → LOST` would make the contradiction
legal rather than resolving it, and would overwrite a known exit status with "lost" (research
R3). The gate stays exactly as strict as it is.

Reachable session paths, for reference:

```
STARTING → RUNNING → EXITED_CLEAN | EXITED_ERROR | LOST
STARTING → LOST
```

There is no `STARTING → EXITED_*` edge; `spool.apply_record` inserts the `RUNNING` step itself
when an exit arrives with no prior start (`spool.py:198-208`). This is why the observed defect
showed `exited_error` for a session that never truly ran.

### Work item invariant (FR-008, FR-009)

> When `dispatch_item` returns, by any path including an exception, the work item is not in
> `dispatching`.

The age-based reaper stays as the backstop for launches that genuinely hang, and must not be the
mechanism that resolves a failure already detected (FR-009).

## Rule 3 — Launch-shape verification (FR-013 – FR-016)

Not persisted state. The verified set is defined by what `build_launch_plan` can produce:

```
{6 permission modes} × {restoring, non-restoring} × {model set, model unset} = 24 shapes
```

Each shape is probed against the real binary and must fail with its expected sentinel rather
than an argument rejection (research R5). The ceiling is the definition: the set is the shapes
the system composes, and it does not grow into a general facility for running real workers.
