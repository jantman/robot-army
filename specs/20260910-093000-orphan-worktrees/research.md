# Research: No Worktree Is Left Where robot-army Cannot Reach It

Every decision below was taken against the tree at bd03aa3. "Rejected" means considered and
not built, with the reason.

## R1 — One removal core, three callers

**Decision**: extract the second half of `operations.worktree_remove` — ask git to remove the
worktree, check the disk, delete the branch, say what happened — into one private helper,
`_remove_checkout`. Three callers use it: the item-id form, the new path form, and
`purge_simulated`. Each caller keeps its *own* guard in front of it (R2, R9), because the three
have different evidence about what might be running in the directory.

**Rationale**: FR-003 and FR-012 say "the same two steps, the same refusals" three times. Three
copies of the step that has already been got wrong once (the branch half, FR-016) is how one of
them drifts.

**Rejected — purge calls `worktree_remove(item_id)` per row.** It would inherit the item-id
form's session guard verbatim, and that guard refuses every row from a `local` rehearsal: at
`local` the session host is simulated, so every session row is written with `pid = 0` and is
never closed by anything (the `cancel` comment at `operations.py:2760` describes this). The
purge would decline to remove exactly the worktrees it exists to remove. It would also emit one
unaggregated block of output per row.

## R2 — Purge's session guard ignores sessions no process ever stood behind

**Decision**: the purge refuses a worktree when the item has an open session row, **except**
rows carrying the simulated host's signature — `dry_run`, `pid == 0`, `proc_start is None`.
That signature is lifted out of `cancel` into a `Session.hosted_by_simulation` property, so
`cancel` and the purge read one definition.

**Rationale**: the #79 guard's argument is "a row whose process cannot be seen is still a row
nothing has closed", i.e. *absence of evidence is not evidence of absence*. A simulated-host row
is not absence of evidence: `SimulatedSessionHost.confirm_session` writes that signature by
construction so nothing can mistake it for a process, and `cancel` already routes on it. A
`no-remote` row has a real pid and stays guarded, which is the case #79 was about.

**Rejected — change `cleanup.live_sessions`.** That would widen the #79 guard for `cleanup`
and `worktree remove <id>` as well, neither of which this spec touches. It may be right; it is
a separate decision about a guard with its own history.

## R3 — The report follows the disk

**Decision**: after git reports a removal, `_remove_checkout` checks whether the directory is
still there. If it is, the removal is reported as refused (`refused_by: "directory_survived"`),
the branch half is not attempted, and a work item's recorded `worktree_path` is left alone.

**Rationale**: FR-006. `SimulatedVersionControl.remove_worktree` answers success without
touching the disk, which is correct for a worktree the simulation also pretended to create —
and wrong for a real directory left by a `local` or `no-remote` round and purged at the level
below, which is precisely the effect-level edge case in the spec. The check lives in the shared
core, so it corrects the item-id form too: today `worktree remove <id>` at that level would say
"removed worktree" and clear the row's path while the directory stays, which is this issue's
defect produced by a different route. Existing tests use paths that do not exist, so they are
unaffected by construction.

**Rejected — pick the real version-control boundary from the row's `dry_run`**, as `cancel`
does for the session host. `dry_run` does not say whether the *worktree* was real (it is true at
both `plan` and `local`), and a second place choosing an implementation from stored state is
exactly what `cancel`'s comment warns against. The disk is the evidence; ask it.

## R4 — What counts as "a worktree robot-army made"

**Decision**: a directory `<worktree_root>/<short>/issue-<n>` (`n` all digits), where `short`
is the last segment of an onboarded repository's key (`repos.resolved_all`). Nothing else under
the root is looked at.

**Rationale**: `prompt.worktree_dir` is the only thing that creates these paths, and that is the
only shape it creates. The default `worktree_root` is `~/worktrees` — a generic name the
maintainer may use for their own checkouts, which must never be reported (SC-005) or become
noise that trains `--acknowledge`.

**Rejected — every directory two levels down.** Reports the maintainer's own work.
**Rejected — only directories some clone lists as a worktree.** A half-created worktree (killed
between `mkdir` and `git worktree add`) holds disk and is exactly as unreachable; it is
reported, with "no clone lists it" in the detail, and path removal refuses it (R9).

## R5 — What "claimed" means

**Decision**: the directory's resolved path equals the resolved `worktree_path` of any work
item, in any state, real or simulated. A `done` item not yet cleaned up claims its directory.

**Rationale**: the claim is what makes a directory reachable through `worktree remove <id>` and
`cleanup`. An orphan is precisely a directory neither can reach. Resolving both sides means a
`~` or trailing slash in a stored path cannot make a claimed directory look orphaned.

## R6 — Git is consulted for the detail, never for the verdict

**Decision**: whether a directory is orphaned is decided from the disk and the database alone.
Each onboarded clone's `list_worktrees` is read once per pass (cached per clone, as
`_sweep_worktrees` already does) only to fill in `listed_by` and `branch`. A listing that fails
is recorded (`reconcile.list_worktrees`, as today) and the anomaly is still raised, with the
listing unknown.

**Rationale**: the edge case "a clone's list cannot be read". A failed read must not suppress a
report about disk, and it must not be what retracts one either (R8).

At the simulated version-control level, `list_worktrees` answers `[]` (deliberately, per its
docstring), so every orphan's detail says no clone lists it. That is the honest answer for that
level: nothing real can be removed there anyway, and path removal refusing is the right outcome.

## R7 — The anomaly's identity

**Decision**: `kind = "orphan_worktree"`, `entity_type = "worktree"`, `entity_id` = the resolved
path, `dry_run = False`.

**Rationale**: the path is the only identity an orphan has, and it is stable across passes, so
the partial unique index on `(kind, entity_type, entity_id, dry_run)` absorbs re-detection
(US2 scenario 2) without new code. `dry_run = False` because there is no row to take a run's
flag from, and migration 14's rule is that a visible false positive is the recoverable mistake.
`entity_type` is free text; only `"work_item"` is given special rendering (`web/pages.py:1963`,
`web/server.py:792`), so a new value renders as plain text everywhere.

## R8 — Retraction

**Decision**: a fourth self-resolving kind. `_resolve_orphan_worktree_anomalies` walks open
`orphan_worktree` anomalies and resolves one when its directory is no longer a directory, or a
work item now claims it. Each resolution writes `anomaly.resolved` with the evidence (`reason`:
`directory_gone` or `claimed_by_item` plus the item id), rehearsal flag copied as the existing
resolvers do.

**Rationale**: FR-009. Both conditions are positive observations of disk and database, neither
depends on git, so there is no "could not check" branch to get wrong. It runs after the raising
sweep in the same pass, and the two cannot fight: the sweep raises only for a directory that is
present and unclaimed now, the resolver resolves only one that is absent or claimed now.

## R9 — The path form's own guards

**Decision**: `worktree remove <path>` resolves the path, then refuses, in this order and
without asking anything:

1. not inside `worktree_root` → refuse (`refused_by: "outside_root"`);
2. claimed by a work item → refuse and name the item id (`"claimed"`);
3. not a directory → refuse, pointing at `worktree prune` for git's leftover record
   (`"not_a_directory"`);
4. no onboarded clone lists it as a worktree → refuse (`"not_a_worktree"`);
5. a live worker's working directory is inside it → refuse unless `--force`
   (`"live_worker"`).

Then `--force` asks for the directory's name to be typed (`issue-29`), and `_remove_checkout`
does the rest with git's dirty-tree refusal intact unless forced.

**Rationale**: there are no session rows for an orphan, so the item-id form's guard has nothing
to ask. The session registry and `/proc` are what reconciliation already uses to find workers by
working directory (`sessions.scan`, `RegistryEntry.alive`, which checks pid *and* start time).
Refusing a claimed path (2) is what stops the path form from being a way around #79's guard.
Refusing a path no clone lists (4) is what stops this command from becoming `rm -rf` for
directories under the root — robot-army only ever removes a worktree through git.

Typing the name rather than the whole path: it is the part the maintainer can read off the
refusal, and it is what the item id is to the item-id form — a token that proves they read which
one.

## R10 — Purge's two questions and one new flag

**Decision**:

| Invocation | Row question | Worktree question |
|---|---|---|
| `purge-simulated` | asked | asked (default no), only if any worktree exists |
| `purge-simulated --remove-worktrees` | asked | answered yes |
| `purge-simulated --yes` | answered yes | answered **no**, not asked |
| `purge-simulated --yes --remove-worktrees` | answered yes | answered yes |

Declining the row question ends the command before the second is asked, and removes nothing.
Both questions are asked before anything is done, so an interrupted second question has deleted
nothing. The `purge.simulated` intent's detail gains `worktrees` (the paths offered) and
`remove_worktrees` (the answer).

**Rationale**: FR-002. `--yes` means "skip the confirmation prompt" and a script already using it
deletes rows only; letting it start deleting directories would change what an existing
invocation does to disk. A second flag for the second question is a knob with a real second use
(unattended cleanup after a scripted rehearsal), not speculation.

## R11 — The `worktree remove` argument

**Decision**: one positional `TARGET`. All digits → item id, exactly as today. Anything else →
path, expanded (`~`) and resolved.

**Rationale**: every existing invocation keeps its meaning. A path that is all digits would be a
relative path to a directory named e.g. `42` in the current directory, which robot-army never
creates (R4); writing `./42` still reaches it.

**Rejected — a separate `worktree remove-path` subcommand.** The purge message and the anomaly
say "use `worktree remove`"; a second verb is a second thing to remember for one action.

## R12 — `worktree list`

**Decision**: after the claimed rows, list each orphan (R4/R5) with item `—`, its path, the
branch git reports (or `—`), condition `unclaimed`, and its size. JSON entries gain `claimed`
(`true` for rows, `false` for orphans); an orphan's `item_id` is `null`. Orphans are listed
regardless of `--include-simulated` — they have no row, so they are neither real nor simulated.

**Rationale**: FR-014 and SC-003. "no worktrees recorded" printed over three directories is the
report's opening evidence.

## Not built

- **Automatic removal of orphans** — see the spec's Out of scope.
- **Sizing orphans in the reconciliation pass** — `worktree list` sizes on request.
- **Walking repositories no longer onboarded** — their clone is not known, so their orphans
  could not be removed by path either. Named in the guide as a limit.
