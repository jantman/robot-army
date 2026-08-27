# Data Model: Onboarding Is Enough

Phase 1 for [plan.md](plan.md). What changes on disk, what the resolved view looks like in memory,
and what each interruption point leaves behind.

## Migration 005

Four nullable columns on the existing `repos` table. No new table, no index, no data movement.

```sql
-- The location this repository was approved at, and how we arrived at it. Recorded rather
-- than re-derived, because a rule evaluated later can produce a different answer than the
-- one a human approved — and the whole point of the origin check is that the wrong answer
-- is a real clone of a real repository, not an error.
ALTER TABLE repos ADD COLUMN clone_path         TEXT;
ALTER TABLE repos ADD COLUMN path_source        TEXT;   -- 'derived' | 'configured'
ALTER TABLE repos ADD COLUMN verified_origin    TEXT;   -- normalised host/owner/name, never a raw URL
ALTER TABLE repos ADD COLUMN origin_verified_at TEXT;
```

`repos` after migration 005:

| Column | Added | Meaning |
|---|---|---|
| `repo_key` | 001 | `owner/name`, primary key |
| `onboarded_at` | 001 | first approval |
| `settings_fingerprint` | 001 | approved committed-settings hashes, `NULL` when there are none |
| `fingerprint_approved_at` | 001 | last approval of that fingerprint |
| `trust_verified_at` | 001 | when the worker trust dialog was last seen accepted |
| `clone_path` | **005** | absolute, symlinks resolved, as approved |
| `path_source` | **005** | `derived` or `configured` — so a surface can say which, per FR-011 |
| `verified_origin` | **005** | the **normalised** identity found there, never the raw URL |
| `origin_verified_at` | **005** | when that comparison last passed |

### Why `verified_origin` stores the normalised form

Storing the raw remote URL would put a possibly-credentialed string into the database and, from
there, into `robot-army repos` output and any JSON view. FR-032 forbids that. The normalised triple
is what the comparison actually uses, is stable across a clone being re-pointed between SSH and
HTTPS, and cannot carry a secret because normalisation strips userinfo before anything else.

### `NULL` clone_path

A row predating this migration. It means *onboarded, location never verified* — not "onboarded at an
unknown path". Dispatch refuses for such a repository and names `onboard --reapprove` as the
resolution. Nothing backfills it: writing a path nobody approved into an approval record is the one
thing this table exists not to do (research R6).

The author's `repos` table currently holds zero rows, so this strictness costs nothing today. It is
written as a rule rather than skipped because the rule is what a future reader needs.

## Entities

### ResolvedRepo

Not persisted. The in-memory answer to "everything a dispatch needs to know about this repository",
produced by `repos.resolve(conn, config, key)` and shaped exactly like today's `RepoConfig` so that
call sites do not change shape.

Precedence, highest first:

| Field | Onboarding record | `[repos.*]` section | Global default |
|---|---|---|---|
| `path` | **wins** | only used at onboarding, to resolve | — |
| `base_branch` | — | wins | `[worker] base_branch` |
| `post_create` | — | wins | `[hooks] post_create` *(new)* |
| `permission_mode` | — | wins | `[worker] permission_mode` |
| `model` | — | wins | `[worker] model` |
| `env` | — | wins | none |
| `max_sessions` | — | wins | `[dispatch] default_repo_max_sessions` |
| `priority` | — | wins | `0` |

`path` is the only field the record wins, and that asymmetry is the design. Every other field is a
policy the author can change at any time by editing a file; `path` decides **which repository is
acted upon**, so it is frozen at the moment a human approved it. A section whose `path` later
disagrees with the record does not silently win — it blocks dispatch pending re-approval (FR-013).

### Known set

`repos.known(conn)` — the `repo_key` column of the `repos` table. Replaces `sorted(config.repos)`
at every site that means "which repositories does this system watch".

A `[repos.*]` section is no longer evidence of anything except that overrides exist for a key. A
section for a repository that was never onboarded describes a repository the system does not watch,
and `robot-army repos` says so rather than listing it as known.

### Repository root

`[paths] repo_root`, one directory, defaulting to `~/GIT`. Validated at configuration load: absent or
not-a-directory is a configuration problem reported with the other configuration problems, not
discovered per repository at onboarding time (FR-001).

### Shared preparation steps

`[hooks] post_create`, the same array-of-tables shape `[repos.*] post_create` already takes, parsed
by the same code path and subject to the same per-step timeout ceiling. Feeds the same startup
timeout budget warning — for every repository that inherits them, not once (research R10).

## Resolution and verification

The sequence onboarding runs, in order, stopping at the first refusal. Each step's failure is a
distinct message; the full taxonomy is in [contracts/onboarding.md](contracts/onboarding.md).

```
repo_key
   ├─ eligibility          owned, or listed in extra_repos          → refuse: not permitted
   ├─ lookup               one source-system request                → refuse: no such repository
   │                       yields canonical name + ownership
   ├─ path                 section path, else <repo_root>/<name>    → path_source
   ├─ exists               directory present                        → refuse: no clone there
   ├─ primary clone        .git is a directory, not a file          → refuse: linked worktree
   ├─ not under worktree_root                                       → refuse: inside the worktree root
   ├─ remote               origin, else the sole remote             → refuse: no remote / ambiguous
   ├─ normalise            strip userinfo, strip .git, lowercase    → refuse: unparseable URL
   └─ compare              host, owner, name                        → refuse: WRONG REPOSITORY
                                                                        names both identities
```

Everything above is a read. Nothing is written until the author approves, and then one row is written
in one transaction.

## State transitions

`repos` rows have no lifecycle state machine and gain none. A row is present or absent; the columns
describe the last approval. The three transitions that exist:

| From | Event | To |
|---|---|---|
| absent | `onboard` approved | present, path recorded, origin verified |
| present | `onboard --reapprove` approved | present, path and origin re-recorded, timestamps advanced |
| present with `NULL` clone_path | `onboard --reapprove` approved | present, fully populated |

There is deliberately no "de-onboard" transition and none is added here. Removing a repository from
the watched set means deleting the row, and that remains a thing the author does directly.

## Interruption

Per the constitution's requirement that persistence and recovery logic carry failure-path tests.

| Killed at | On disk | Next run |
|---|---|---|
| Mid-migration | `user_version` still 4; some columns may exist | The ladder re-runs migration 005 whole. Because the ladder wraps each migration in one explicit transaction and rolls back on any exception, a partially applied set of `ALTER TABLE`s is not observable — this is the property that must be tested, not assumed |
| After eligibility, before lookup | Nothing | Re-resolves from the start |
| After lookup, before the prompt | Nothing | Re-resolves; the lookup is one more request |
| After the prompt, before commit | Nothing | Prompts again |
| After commit, before output | Row present and correct | Reports the fingerprint unchanged, does nothing, exits 0 |
| Dispatch re-verification, mid-check | Nothing; no worktree | Item stays dispatchable and is re-verified on a later pass |
| Dispatch re-verification passed, killed before `worktree.prepare` | Nothing; no worktree | Unchanged from today — this window already exists and 001 handles it |

There is no window in which a repository is half-onboarded, because the write is one row in one
transaction and every step before it is a read. That is the whole of the interruption story, and it
is short by design.

## What this milestone does not touch

- `work_items`, `sessions`, `cards`, `poll_state`, `anomalies`, `dispatch_control` — no column added,
  removed, or reinterpreted.
- `poll_state` is keyed by repository and needs no migration; a repository that becomes known between
  cycles simply has no prior state, which is already a case the code handles.
- The fingerprint and trust mechanisms are unchanged. This milestone adds a fourth thing onboarding
  verifies; it does not alter the three that exist.
