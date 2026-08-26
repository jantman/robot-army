# Implementation Plan: Onboarding Is Enough

**Branch**: `005-onboard-is-enough` | **Date**: 2026-08-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-onboard-is-enough/spec.md`

## Summary

Move the answer to "which repositories does this system know about" from the configuration file to
the onboarding record, so that `robot-army onboard <owner>/<name>` is the whole job for a repository
whose clone sits where the author's convention puts it.

The technical approach is a **resolution seam**, not a new subsystem. Three questions are asked of
`config.repos` across twenty-six call sites today, and each becomes a question with two sources:

| Question | Answered today by | Answered after 005 by |
|---|---|---|
| Which repositories are known? | the `[repos.*]` section keys | the `repos` table |
| Where is this repository's clone? | `RepoConfig.path`, required | the path recorded at onboarding |
| What are this repository's settings? | `RepoConfig`, or nothing | its section over the existing defaults |

The second question is the one with teeth. Deriving `<repo_root>/<name>` is right for 222 of the
author's 252 owned repositories and **wrong for five** — where the derived path holds a real clone of
a different repository. So derivation is paired with an origin check, the pair runs at onboarding
where a human is already reading an approval screen, and the *outcome* is recorded rather than the
rule. Nothing re-derives afterwards; a clone that moves produces a refusal naming the recorded path
instead of a worktree in a repository nobody named.

Two configuration additions finish an existing pattern rather than starting one: `[paths] repo_root`
and `[hooks] post_create`. `Config` already resolves four of seven per-repository settings against a
global default; these are the two that could not.

## Technical Context

**Language/Version**: Python 3.14.7 (`requires-python = ">=3.14"`)

**Primary Dependencies**: `httpx>=0.28`, unchanged. No new runtime dependency — URL comparison uses
`urllib.parse` from the standard library, and the one new source-system read reuses the existing
`GitHubReader` request path.

**Storage**: SQLite at `~/.local/state/robot-army/state.db`, currently `user_version = 4`. This
milestone adds migration 005, four nullable columns on `repos`, and no new table.

**Testing**: `pytest`, with the existing `requires_git` marker for cases that shell out to a real
`git`. Origin-comparison and path-derivation logic is pure and unit-testable without either.

**Target Platform**: One Linux workstation. `~/GIT` is the author's convention; the value is
configurable and has a default, not a hard-coded path.

**Project Type**: Single project — CLI plus a foreground daemon, as milestones 001–004 established.

**Performance Goals**: Onboarding one repository costs **one** additional source-system request
regardless of how many repositories the author owns (SC-009). Dispatch-time re-verification is two
local `git` invocations, both sub-100ms, against a step that already runs `fetch` and creates a
worktree.

**Constraints**: The polled set becomes a database read, so a repository onboarded while the daemon
runs is picked up on the next poll **without a restart** — a property the configuration-file version
could not have. No behaviour may change for a repository that has an explicit `path` (SC-008).

**Scale/Scope**: 252 owned repositories, of which ~227 are onboardable and ~25 will keep a
configuration section. Twenty-six call sites move from one source to two. One migration, four
columns, two configuration keys, one new git boundary method.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (YAGNI & KISS)

**Passes, with two entries in Complexity Tracking.**

- **One new module**, `src/robot_army/repos.py`, holding resolution. It is a consolidation of
  twenty-six existing call sites, not an abstraction placed ahead of a need — each of its two
  functions has a dozen callers on the day it lands. See Complexity Tracking for why it is not folded
  into `Config`.
- **One derivation rule, one candidate path.** No search path, no list of roots, no `<owner>/<name>`
  fallback. The author's nested grouping directories hold repositories they do not own and would not
  dispatch into; an override serves them. A search path would be a configuration knob with exactly
  one hypothetical user.
- **No new dependency.** URL normalisation is `urllib.parse` plus string handling.
- **The one candidate for removal is named in the spec**, not discovered later: US7's discovery
  listing is the only story that adds a surface rather than removing a step, and the spec states that
  dropping it means deleting `list_owned_repos()` rather than leaving it uncalled.

### II. Single-User, Local-First

**Passes.**

- No multi-tenancy, no accounts, no authorisation. The onboarding allowlist is explicitly **not** a
  security boundary (FR-026) and the plan must not let it drift into being described as one; the
  issue-author check remains the boundary and is untouched.
- All new state is four columns in the existing SQLite database at the documented path.
- **One new secret-handling surface**, and it is the reason FR-032 exists: a clone URL may embed
  credentials (`https://user:token@host/owner/name`). Every place a remote URL is recorded, compared,
  or displayed MUST strip userinfo first. This is new — no prior milestone read a git remote URL.

### III. Total Accountability

**Passes with zero enumerated gaps**, which is worth stating because 004 had one.

- Onboarding already writes a `repo.onboard` intent/outcome pair. It gains the resolved path, how it
  was resolved, and the origin comparison in its detail — no new record kind for the happy path.
- **Refusals are a real gap today and this milestone closes it.** `onboard` currently returns
  `EXIT_USAGE` for a missing section *before* any audit action is opened, so a refusal leaves no
  record at all. Every refusal path added here — and that early return, which is being replaced —
  writes an outcome naming its cause.
- Dispatch-time re-verification writes a record only when it **fails**, because success is already
  implied by the worktree creation record that follows it on the same item within milliseconds. This
  is not a gap under Principle III's reconstruction standard: the question "did the clone still check
  out?" is answered by the presence of the next record.
- No summarisation rule is needed. Unlike 004's capacity hold, nothing here fires per tick — the
  volume is one record per onboarding and one per dispatch failure.

### IV. Interruption Tolerance

**Passes.**

- Migration 005 joins the existing `PRAGMA user_version` ladder, which already sets its version as
  the last statement inside an explicit transaction. A kill mid-migration leaves the version
  unadvanced and the migration re-runs.
- Onboarding is one `db.transaction` writing one row. Killed before it, nothing happened and the
  command re-runs. Killed after it, the row exists and a re-run reports the fingerprint unchanged and
  does nothing. Idempotent in both directions.
- The verification steps are reads. There is no partial state they can leave.
- No new network call needs retry logic of its own: the ownership lookup goes through
  `GitHubReader._request`, which already carries the timeout and bounded backoff FR-008 requires.

### V. Public Code, Unsupported Project

**Passes.**

- No credential is committed, and FR-032 keeps one out of the log for the first time a git remote URL
  is read.
- Breaking changes are fine and there is one: a repository with a `[repos.*]` section that was never
  onboarded stops being polled. It was never dispatchable anyway — onboarding has always been the
  gate — so the change is that the system stops *pretending* to watch it.
- Documentation targets the author's future self. `README.md`, the configuration contract, and the
  shipped example all currently describe `include_owned` as "poll every repo you own", which is wrong
  in two ways and is corrected here.

### Operating Constraints

- Every new capability is reachable from the terminal, and every refusal exits non-zero with a
  message naming its cause and the override that resolves it.
- **The irreversible action this milestone is shaped around is one it prevents**, not one it adds:
  creating a worktree and a branch in a repository the author did not name. FR-028 and FR-029 are
  that guard, and SC-004 is how it is measured.
- Onboarding remains explicitly confirmed, one repository at a time.

## Project Structure

### Documentation (this feature)

```text
specs/005-onboard-is-enough/
├── plan.md                     # This file
├── spec.md                     # Feature specification
├── research.md                 # Phase 0 output — R1 through R12
├── data-model.md               # Phase 1 output — migration 005 and the resolved entities
├── quickstart.md               # Phase 1 output — validation scenarios
├── checklists/
│   └── requirements.md         # Spec quality checklist
├── contracts/
│   ├── config.md               # [paths] repo_root, [hooks] post_create, the two github keys
│   └── onboarding.md           # Resolution, verification, refusal taxonomy, recorded outcome
└── tasks.md                    # Phase 2 output — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/robot_army/
├── repos.py                    # NEW — resolution: known set, and settings for one repository
├── config.py                   # [paths] repo_root, [hooks] post_create, path becomes optional
├── migrations.py               # NEW migration 005 — four columns on repos
├── db.py                       # upsert_repo gains the resolved location; Repo model extended
├── models.py                   # Repo gains clone_path, path_source, verified_origin
├── operations.py               # onboard resolves + verifies + records; repos verb; discovery (US7)
├── dispatch.py                 # check_gates re-verifies the recorded path before a worktree
├── poll.py                     # poll_all walks the onboarded set
├── intake.py                   # card-to-repository matching uses resolved paths
├── worktree.py                 # preparation steps fall back to the shared default
└── boundaries/
    ├── git.py                  # NEW remote_url(); primary-clone check
    └── github.py               # repository lookup for ownership + canonical name

tests/
├── unit/
│   ├── test_repos.py           # NEW — derivation, normalisation, comparison, resolution
│   ├── test_config.py          # repo_root, shared post_create, optional path
│   ├── test_migrations.py      # migration 005, killed and re-run, pre-005 rows
│   └── test_worktree.py        # shared-default preparation steps
└── integration/
    ├── test_onboard.py         # NEW — the refusal taxonomy end to end, and the happy path
    └── test_dispatch.py        # the recorded path moving out from under a dispatch
```

**Structure Decision**: Single project, as 001–004 established. The one new module is
`src/robot_army/repos.py`, and its boundary is deliberate: **it answers questions, it does not
perform actions.** Derivation, URL normalisation, comparison, and the join of record-over-section-over-default
live there and are pure functions over `(conn, config)`. Onboarding's *decision* to record, dispatch's
*decision* to refuse, and every audit write stay at their existing call sites, so the seam adds a
place to look things up without adding a place where things happen.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A new `repos.py` resolution module rather than more methods on `Config` | Twenty-six call sites ask `config.repos` two questions that now have two sources — the file and the database. Putting the join anywhere else means every call site performs it. | `Config` is parsed from a file and deliberately has no database handle. It is constructed by `doctor`'s configuration check, by validation before any database exists, and by tests with no connection at all. Threading a connection into it would make the config object unusable in the three places that need it most. |
| Re-verifying the recorded path at dispatch, duplicating the check onboarding already made | The clone can move, be replaced, or be deleted between onboarding and a dispatch months later (US5). This is the check that turns that into a refusal rather than a worktree in the wrong repository. | Trusting the onboarding record alone was the first design. It fails exactly where the five known wrong-location repositories fail, but later and more quietly — the author would have approved a correct path and still ended up with a branch somewhere unexpected. The check is two local `git` calls on a step that already runs `fetch`. |

One smaller addition needs no justification table but is worth naming: `VersionControl` gains
`remote_url(clone_path, remote)`. `default_remote()` already exists and returns a remote *name*;
nothing in the codebase has ever needed a URL, because nothing before this compared a clone's
identity against anything.

## Post-Design Constitution Re-Check

Re-evaluated after research.md and the Phase 1 artifacts. **Still passes**, with three findings from
the design that were not visible at the first gate:

1. **The polled set moving to the database removes a restart, and that is a behaviour change nobody
   asked for.** Today `poll_all` reads a config loaded at process start, so a newly configured
   repository needs a daemon restart. Reading the onboarding record means a repository onboarded
   while the daemon runs is polled on the next cycle. This is strictly better and directly serves
   US1 AS2, but it is a change in observable behaviour and is recorded in research R7 rather than
   left to be noticed.

2. **`intake.py`'s card-to-repository matching is a fourth consumer that reads `RepoConfig.path`
   directly**, not just the key set — `_key_for_path` decides that a pasted filesystem path names a
   repository by comparing against every configured clone path. It must consult resolved paths after
   this change or Trello cards stop resolving for any repository without a section. This was not
   apparent from the spec and is why research R8 exists.

3. **The refusal-logging gap is pre-existing and larger than this milestone.** `onboard`'s early
   return for a missing section writes nothing to the audit log, which is a live Principle III
   violation today, not one this milestone introduces. It is fixed here because the code is being
   touched anyway, and the plan says so rather than quietly counting it as new work.

No new Principle III gap is enumerated, no new dependency is added, and the one genuinely droppable
story is still droppable — nothing in the design made US7 load-bearing.
