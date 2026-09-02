# Phase 0 Research: Per-Repo Concurrency and Wait-for-Merge

No `NEEDS CLARIFICATION` markers survived the spec: the two decisions that would have become
markers were put to the author before the spec was written and are recorded in its
Assumptions. What follows resolves the remaining design questions against the existing code.

## R1 — The gate reads work items, not pull requests

**Decision**: A repository with wait-for-merge in force holds every item in it while that
repository has an **unfinished** item: `state NOT IN (discovered, ready, done, abandoned)`.

**Rationale**: The author chose this over a pull-request merge lookup. It costs no request,
it needs no new boundary method, no cache, and no staleness window — which matters
disproportionately because `ordering.plan` is called on every web page render, and a version
of this gate that asked GitHub anything would have had to be moved out of `plan` entirely, or
would have made rendering a page cost a rate-limited API call. The wait is the same wait: a
merged pull request that says *closes #N* closes the issue, `reconcile._resolve_closed_issues`
already notices that within a minute and transitions the item to `done`, and `done` is
terminal.

`discovered` and `ready` are excluded because they are pre-dispatch. Including `ready` would
deadlock a repository against its own queue — two ready items, each holding the other, and
neither ever dispatching. This is the only genuinely load-bearing detail of the predicate and
it gets its own test.

`failed` and `interrupted` are included. They are non-terminal work in a repository that asked
to run one thing at a time, and the author already has `retry` and `abandon` to say which. The
spec's Edge Cases records this as deliberate rather than incidental.

**Alternatives considered**:

- *Ask GitHub whether the branch's pull request is merged.* Rejected by the author. Would have
  required a new `PullRequest.merged` boundary read, a per-repo cache with a TTL, and a
  staleness display, and would have put a network call behind a page render.
- *Gate on `awaiting_review` only.* Rejected: it would let a `failed` item sit while the next
  issue dispatched over the top of it, which is exactly the collision the feature prevents.
- *Gate on the previous item by id.* Rejected: "previous" is ambiguous once items are
  retried, and the useful invariant is simply *one in flight per repository*, which needs no
  ordering at all.

## R2 — The gate belongs to `ordering`, not to `capacity`

**Decision**: `ordering.py` gains a `wait_for_merge` helper and a clause in `_hold_for`.
`CapacitySnapshot` is unchanged.

**Rationale**: `capacity.py`'s docstring states the split explicitly — the split is *by
input*: capacity's input is the machine, ordering's input is the configuration. Whether a
repository has an unfinished work item is neither; it is a fact about the database that only
becomes interesting once a policy is applied to it. Hanging an `unfinished_per_repo` dict off
`CapacitySnapshot` beside `per_repo` would look symmetrical and would be wrong: `per_repo`
counts *live sessions*, an observation of the machine, and the new dict would count *rows*,
which the observer has no business reading. The two would then be trivially confusable at
every call site.

**Alternatives considered**: adding it to the snapshot (above); a third module (rejected —
one function does not need a module, Principle I).

## R3 — Cost: one query per plan, not one per item

**Decision**: `ordering.plan` computes the set of repository keys with unfinished work **once**
per call, with a single `db.list_work_items(states=[...])` scan, and passes it into `_hold_for`
the way `resolved` is already passed in.

**Rationale**: `plan` already establishes this pattern in its own comment — `repos.resolved_all`
is resolved once "rather than per item: this function runs on every web page render, and one
query beats one per queued item." The gate follows it. The predicate scans a handful of rows
of a table already read once per plan.

**Alternatives considered**: a `COUNT(*)` per candidate repository (rejected: N queries where
one suffices, on a hot path); a dedicated indexed view (rejected: premature at this scale).

## R4 — Precedence: after `repo_cap`, before `not_onboarded`

**Decision**: `HoldReason` declaration order becomes `paused`, `capacity_unobservable`,
`global_cap`, `repo_cap`, `awaiting_merge`, `not_onboarded`, `preparation_failed`.

**Rationale**: `HoldReason`'s declaration order *is* the precedence, and its docstring
justifies each rank. `awaiting_merge` sits below `repo_cap` for the reason `repo_cap` sits
below `global_cap`: the coarser limit binds first, and when both apply, freeing a session slot
is the more immediate fact. It sits above `not_onboarded` and `preparation_failed` because
those are conditions of the *item* — true on an empty machine — while this is a condition of
the *queue*.

In practice the two rarely coincide: while a session is running, `repo_cap` holds and the item
is not yet unfinished-without-a-session; the moment the session exits, the cap frees and this
gate takes over. That handover is the behaviour worth a test.

**Alternatives considered**: above `repo_cap` (rejected: shows the author a merge to chase
when a session slot is what is actually missing); at the bottom (rejected: it is a queue
condition, and burying it under item conditions would report `preparation_failed` residue in
preference to the reason the queue is actually stopped).

## R5 — `awaiting_merge` is a per-item hold, and the hold record widens to match

**Decision**: `awaiting_merge` is **not** added to `dispatch._GLOBAL_HOLDS`. Instead,
`select_and_dispatch` records a hold when a pass ends having dispatched nothing *and* at least
one candidate was held, naming the first held entry.

**Rationale**: FR-007 requires per-repository isolation, and `_GLOBAL_HOLDS` means `break` —
one held repository would stop every other repository's dispatch in the same pass. So the
reason must be a `continue`. But then FR-015's record would never be written, because today
only global holds are recorded — which is also why `repo_cap`, an existing reason, has never
appeared in the log at all.

Widening the existing single-slot mechanism fixes both at once and adds no second recording
path: `_hold_signature` gains the head-held entry's reason and repository so that a change of
*which* condition is holding is news, and `_clear_hold`'s existing "the queue drained" call is
split so that "nothing was eligible" and "everything eligible was held" are distinguishable.
The de-duplication that makes this affordable is the enumerated Principle III gap in plan.md.

**Alternatives considered**: adding it to `_GLOBAL_HOLDS` (rejected: violates FR-007);
a second per-item hold table (rejected: state to keep correct for a fact recomputable on
demand, and a flood to suppress).

## R6 — Nothing is stored, so nothing is migrated

**Decision**: no schema change, no migration, no new column.

**Rationale**: the entire gate is a predicate over `work_items.state`, a column that has
existed since milestone 001 and whose every transition is already audited. This is what makes
the Interruption Tolerance answer trivial and what makes SC-004 — the setting taking effect on
the next pass with nothing to correct — true by construction rather than by care.

## R7 — `fast_forward` is one boundary method, not a sequence assembled by the caller

**Decision**: add `VersionControl.fast_forward(clone_path, remote, branch) -> FastForwardResult`
with four outcomes: `updated`, `already_current`, `skipped` (with a reason), `failed` (with the
git error). All refusal logic lives inside the boundary.

**Rationale**: the checks are git knowledge — `symbolic-ref` for the current branch,
`status --porcelain` for cleanliness, the presence of `MERGE_HEAD` / `CHERRY_PICK_HEAD` /
`REVERT_HEAD` / `rebase-merge` / `rebase-apply` / `BISECT_LOG` for an operation in progress, and
`merge-base --is-ancestor` for divergence — and `boundaries/git.py` is where git knowledge
lives. Assembling them in `worktree.prepare` would put six git invocations and their
interpretation into a module that currently expresses its git usage as five named verbs, and
would leave the simulated boundary with nothing coherent to simulate.

The four outcomes stay four for the reason `remote_branch_head`'s docstring gives about its own
three: collapsing "declined, and here is why" into "did nothing" loses precisely the fact the
author needs when they wonder why their clone is still behind.

**`--ff-only` is not sufficient on its own.** `git merge --ff-only` already refuses a divergent
history, but it does *not* refuse a dirty tree in every case, it does not tell the caller which
branch it was on, and its failure is a non-zero exit with prose. The explicit checks exist so
that the record says *dirty working tree* rather than *exit 128*, and so a refusal is an
expected outcome rather than an error. `--ff-only` is still passed, as the last line of
defence.

**Alternatives considered**: `git pull --ff-only` (rejected: it fetches again, and the fetch
has already happened one step earlier in `prepare`); `git update-ref` on the branch directly
(rejected: it would move the branch out from under a checked-out working tree, which is the
one thing that can actually destroy the author's uncommitted work).

## R8 — Where the fast-forward is called from, and for whom

**Decision**: `worktree.prepare`, immediately after the fetch and before `add_worktree`, and
only when `config.effective_wait_for_merge(repo.key)` is true. The outcome is written into the
existing `worktree.prepare` audit action's outcome dict. A `skipped` or `failed` result never
fails the item.

**Rationale**: `prepare` already has the clone path, the remote, the base ref, `config`, and an
open `audit.action` whose outcome dict is exactly where `fetch_skipped` is already recorded —
so this is one more key in a record that already exists, not a new record. Restricting it to
wait-for-merge repositories is the Operating Constraints' rule for actions that mutate
something the author owns: reachable only by explicit configuration.

FR-019 makes the failure non-fatal because the fast-forward is a convenience for the *author's*
clone; the *session* gets its merged code from `origin/<base>` regardless, which `prepare`
already prefers as the start point. Failing a work item because the author's clone was dirty
would be punishing the wrong thing.

**Alternatives considered**: doing it for every repository (rejected: touches clones that never
asked); a separate `robot-army sync` verb (rejected: YAGNI, and it would need to be remembered
and run); failing the item on a failed fast-forward (rejected by FR-019, above).

## R9 — Configuration shape mirrors the cap exactly

**Decision**: `[dispatch] wait_for_merge = false` as the global default and
`[repos."owner/name"] wait_for_merge = true|false` as the override, with `None` on
`RepoConfig` meaning *inherit*. `Config.effective_wait_for_merge(key) -> (value, explicit)`.

**Rationale**: `max_sessions` / `default_repo_max_sessions` already establishes this shape,
including the `None`-means-inherit convention that `RepoConfig.max_sessions` and
`RepoConfig.speckit` both use and both document with the same reason: keeping "unset" distinct
lets a surface say *which* setting is responsible. A second, differently-shaped mechanism for
the second setting on the same object would be gratuitous.

The name is `wait_for_merge` on both sides rather than `default_wait_for_merge` globally,
because unlike `default_repo_max_sessions` it is not a *number* being defaulted — it is the
same boolean at two scopes, and `[dispatch] default_wait_for_merge = true` reads as though it
configures a default that something else supplies. Both keys go into their sections'
strict-key sets, where a misspelling is an error rather than a silent no-op.

**Alternatives considered**: a single global-only switch (rejected: the issue asks for
per-repo); `serial = true` as one combined setting meaning *cap 1 and wait for merge*
(rejected: it conflates two independent knobs, and the cap already defaults to 1).
