# Implementation Plan: The base ref comes from the repository, not from a guess

**Branch**: `robot-army/issue-150-onboarding-doesn-t-detect-proper` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260906-145347-detect-default-branch/spec.md`

## Summary

The base ref is the only line on the onboarding screen that nothing looked at the repository
to produce. It is `[worker] base_branch`, whose default is the literal `"main"`, so a clone
whose default branch is `master` is onboarded against a ref that does not exist: the settings
committed on `master` are neither shown nor fingerprinted, and the approval records a
fingerprint of nothing.

This adds one read and one resolver, and routes every existing base-ref call site through it.

- **One read**: `VersionControl.default_branch(clone_path, remote)` —
  `git symbolic-ref --quiet refs/remotes/<remote>/HEAD`, local, no network, no credential
  (R1). `SimulatedVersionControl` delegates to the real implementation, because the subject is
  the operator's real clone (R6).
- **One resolver**: `repos.base_ref(...)` returning the ref **and its provenance**, by the
  order `[repos."<key>"] base_branch` → detected → `[worker] base_branch` → `"main"` (R3).
- **Every call site** — onboarding, the dispatch gate, worktree creation, cleanup's landed
  check, and the three listings — asks the resolver instead of spelling
  `repo.base_branch or config.worker.base_branch` for the fifth time (R4).

Two supporting changes fall out of the ordering:

- The loader stops copying `worker.base_branch` into sections that never set it, so *not
  stated* is distinguishable from *set to the same value*. Every existing consumer already
  reads the empty string as "inherit" (R3).
- `[worker] base_branch` stops being rendered live in the example configuration. The shipped
  example is where the maintainer's explicit `"main"` came from in the first place, and a live
  key that outranks nothing is a lie about what decides (R3).

One thing is removed rather than resolved: the queue's wait-for-merge hold message stops
naming a branch, because `ordering.plan` is pure by contract and runs on every web page render
(R4).

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added. One method on an existing boundary protocol

**Storage**: SQLite at `~/.local/state/robot-army/state.db`. **No migration, no new column.**
The resolved value is computed from configuration plus the clone at each use, exactly as trust
and the settings fingerprint already are (R2)

**Testing**: `pytest`, `uv run pytest`. Detection needs a fixture clone with a real
`origin/HEAD` on a non-`main` branch — a `git clone` of a local `master` repository, which the
existing `git_repo` helper shape supports

**Target Platform**: one Linux machine, one user

**Project Type**: single-process daemon plus CLI plus a read-mostly web interface

**Performance Goals**: one extra local `git symbolic-ref` per resolution, on paths already
running several git commands. **No network call is added on any path**, and nothing is added
to `ordering.plan`, the one function on the per-render hot path

**Constraints**: onboarding must not become network-dependent; a clone that cannot answer must
onboard exactly as it does today; every git invocation stays timeout-bounded and audited

**Scale/Scope**: a handful of repositories, tens of work items

## Constitution Check

*GATE: passed before Phase 0 research; re-checked after Phase 1 design — see the bottom of
this file.*

**I. Simplicity First** — PASS. One boundary method, one resolver function, and the deletion
of four hand-spelled copies of the same fallback. The rejected alternative was a `repos` column
plus a migration plus a re-approval prompt for every existing repository (R2). No knob is
added: the two configuration keys that exist keep their names and gain no siblings.

**II. Single-User, Local-First** — PASS. Detection reads a local ref in a local clone. It works
with no network and no token, which is the property that rules out both the GitHub API and
`ls-remote` (R1).

**III. Total Accountability** — PASS. The detection subprocess is recorded as `git.subprocess`
with its argv, the same as every other local read on this boundary (R5). `repo.onboard`'s
detail gains `base_ref_source`, so the record answers *what branch was approved and what
decided it*, not just the first half. **No action is left unlogged.** Detection failing is not
swallowed: `symbolic-ref` exits 1 with no output when the ref is absent, that is the answer
rather than an error, and the fallback it causes is printed on the approval screen and carried
in the record.

**IV. Interruption Tolerance** — PASS. Nothing is written. Every path is a read of local git
state, bounded by the existing `QUICK_TIMEOUT`, and a kill mid-resolution leaves nothing to
recover: the next run resolves again from the same two inputs. Nothing is cached, so no cache
can survive a crash and disagree with the disk.

**V. Public Code, Unsupported Project** — PASS. No credential is read or written. The one
behaviour change to an existing key — `[worker] base_branch` losing to detection — is a
breaking change made deliberately for the single user, which this principle explicitly permits;
it is documented in `configuration.md` rather than shimmed.

**Development Workflow** — the two questions this constitution requires every plan to answer:

- **What does this log?** Every detection attempt, as `git.subprocess` with its argv and
  outcome. Every onboarding, as `repo.onboard` with `base_ref` and now `base_ref_source`.
  Nothing else is added, because nothing else happens.
- **What if it is killed halfway through?** Nothing is in flight. The longest-lived thing this
  feature creates is a local variable. A kill during onboarding is already handled by the
  existing `KeyboardInterrupt` path, which records `interrupted_at_prompt` and leaves no row.

## Design

### The read

```python
def default_branch(self, clone_path: str, remote: str) -> str | None
```

on `VersionControl`, implemented in `GitVersionControl` as
`git symbolic-ref --quiet refs/remotes/<remote>/HEAD` with `check=False`, returning the branch
name with `refs/remotes/<remote>/` stripped, or `None`. The full ref name is read rather than
`--short` so the prefix strip is exact rather than a guess about `/` in remote names (R1).

`None` means *the clone does not know*, and it is the only failure this read has: a missing ref
and an unreadable repository are the same answer to the caller, which is correct here — both
mean "fall back and say so". This is deliberately unlike `remote_branch_head`, whose three
answers must stay three because one of them authorises deleting a branch. Nothing irreversible
hangs off this one.

`SimulatedVersionControl.default_branch` delegates to `self._real` (R6).

### The resolver

```python
@dataclass(frozen=True, slots=True)
class BaseRef:
    ref: str
    source: str        # audit token: "repo_config" | "detected" | "worker_config" | "default"
    detail: str        # what a human reads: '[repos."x/y"] base_branch', "detected from origin/HEAD", …

def base_ref(config, key, vcs, clone_path, *, remote=None) -> BaseRef
```

in `repos.py`, next to `select_remote` and `verify`, which already take a `VersionControl` and
already read the clone. When `remote` is not supplied it is asked for with
`vcs.default_remote(clone_path)`; when there is no remote at all, detection is skipped and the
configured answer is used, which is the same shape `worktree.prepare` already uses for its
fetch.

Two fields rather than one string because both readers exist and want different things: the
audit record wants a token it can count, the approval screen wants a sentence (the same split
`Verification.cause` / `Verification.refusal` already makes).

### The call sites

| Site | Today | After |
|---|---|---|
| `operations.onboard` | `(section.base_branch if section else "") or config.worker.base_branch` | `repos.base_ref(..., remote=resolved.remote)`; prints the provenance; records `base_ref_source` |
| `dispatch.check_gates` | `repo.base_branch or config.worker.base_branch` | resolver |
| `worktree.prepare` | same expression | resolver, before the fetch; start-point preference unchanged |
| `cleanup` landed check | `config.base_branch_for(key)` | resolver |
| `operations.repos` listing | `repo.base_branch or config.worker.base_branch` | resolver |
| `operations.worktrees` listing | `config.base_branch_for(key)` | resolver |
| `operations._local_resume_signals` | `config.base_branch_for(key)` | resolver (the cache key already includes the base ref) |
| `ordering._hold_for` | `config.base_branch_for(key)` in a message | the branch name leaves the message (R4) |

`Config.base_branch_for` loses every caller and goes with them. Leaving a second, blind way to
answer the same question is how the two come to disagree — the reason `speckit_enabled_for`
returns its provenance rather than being recomputed at each site.

### The onboarding screen

```
base ref     : master   (detected from origin/HEAD)
base ref     : develop   ([repos."owner/name"] base_branch)
base ref     : main   ([worker] base_branch; origin/HEAD is not set)
base ref     : main   (the default; origin/HEAD is not set)
```

Same shape as the `clone path` line directly above it, which has read
`(derived from [paths] repo_root)` since milestone 005. The `--json` document gains
`base_ref_source` and `base_ref_detail` beside the existing `base_ref`.

## Project Structure

### Documentation (this feature)

```text
specs/20260906-145347-detect-default-branch/
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── base-ref.md      # Phase 1
├── checklists/
│   └── requirements.md
├── spec.md
└── tasks.md             # /speckit-tasks
```

### Source (repository root)

```text
src/robot_army/
├── boundaries/__init__.py   # VersionControl.default_branch on the protocol
├── boundaries/git.py        # the real read; the simulated delegation
├── repos.py                 # BaseRef and base_ref() — the one resolver
├── config.py                # stop inheriting base_branch at parse; drop base_branch_for
├── operations.py            # onboard screen + json + audit detail; two listings; resume signals
├── dispatch.py              # the fingerprint gate
├── worktree.py              # prepare
├── cleanup.py               # the landed check
├── ordering.py              # the hold message stops naming a branch
└── exampleconfig.py         # [worker] base_branch renders commented

tests/unit/
├── test_git_boundary.py     # detection: present, absent, non-origin remote, not a repo
├── test_repos.py            # the four-step precedence, and the provenance each step reports
├── test_onboard*.py         # the screen, the json, the audit detail, the fingerprint at master
├── test_config.py           # a section that omits base_branch no longer inherits
├── test_ordering.py         # the hold message no longer names a branch
└── test_example_config*.py  # regenerated example

tests/integration/
└── test_worktree.py         # a session in a master repository branches from master
```

**Structure Decision**: unchanged. This feature adds no module; it adds one function to
`repos.py`, one method to the version-control boundary, and subtracts a duplicated expression
from six call sites.

## Documentation obligations

Required by `CLAUDE.md`, decided by which pipeline stage each change touches:

| Change | Page |
|---|---|
| the onboarding screen's base-ref line and its provenance | `docs/guide/1-setup.md` |
| what decides the base ref; `[worker] base_branch` demoted to a fallback | `docs/guide/configuration.md` |
| `repo.onboard`'s `base_ref_source` | `docs/guide/audit-log.md` |
| the wait-for-merge hold message | `docs/guide/3-selection.md` |

And, because `[worker] base_branch` changes from live to commented:
`uv run robot-army example-config --output share/config.example.toml --force`, plus the fifth
row in
[`contracts/example-config.md`](../20260905-124257-docs-overhaul-example-config/contracts/example-config.md)'s
table of reasons and the one sentence in `CLAUDE.md` that summarises it.

## Complexity Tracking

No violations. Nothing here is justified as an exception; the design removes more expressions
than it adds functions.

## Post-design Constitution re-check

Re-read after Phase 1. Still PASS on all five principles. The design as detailed changed
nothing about the answers above: no state was added (II, IV), no action became unlogged (III),
and the one thing that grew — a resolver with a provenance field — replaced four copies of the
expression it generalises, so Principle I is better served after the change than before it.
