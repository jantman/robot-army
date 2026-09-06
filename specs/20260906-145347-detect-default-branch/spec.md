# Feature Specification: The base ref comes from the repository, not from a guess

**Feature Branch**: `robot-army/issue-150-onboarding-doesn-t-detect-proper`

**Created**: 2026-09-06

**Status**: Draft

**Input**: GitHub issue #150, filed after onboarding a real repository whose default branch
is not `main`.

Source: [#150 — Onboarding doesn't detect proper default
branch](https://github.com/jantman/robot-army/issues/150)

## What is actually happening

Onboarding `jantman/biweeklybudget` — a clone whose default branch is `master`, checked out
at `master`, with `origin/HEAD` pointing at `master` — prints:

```
repository   : jantman/biweeklybudget
clone path   : /home/jantman/GIT/biweeklybudget   (derived from [paths] repo_root)
verified     : github.com/jantman/biweeklybudget via origin
base ref     : main
trust        : accepted — /home/jantman/GIT/biweeklybudget is trusted

no committed .claude/settings*.json at the base ref
```

**Nothing looked at the repository.** The base ref is a configuration value with the
literal default `"main"`, and every other line on that screen was derived from the clone in
front of it: the path from `[paths] repo_root`, the identity from `origin`'s URL, trust from
the trust file. One line out of four is a guess, and it is the only line the reader has no
way of telling apart from the derived ones.

**The wrong answer is not confined to the screen.** The same value decides four things:

| Where | What it decides |
|---|---|
| onboarding | which ref the committed `.claude/settings*.json` are read and fingerprinted at |
| dispatch | the fingerprint re-check that gates every session |
| worktree creation | what the session's branch is created from, and what is fast-forwarded |
| cleanup, ordering, `worktrees` | whether a branch has landed, and how far ahead it is |

So on a `master` repository the line above is not cosmetic. `no committed
.claude/settings*.json at the base ref` is what "`main` does not exist" looks like — the
settings that *are* committed on `master` are neither shown nor fingerprinted, so the
maintainer approves a repository having been shown nothing, and the approval records a
fingerprint taken at a ref that is not there. Later, worktree creation asks git for a branch
off `main` and fails, or silently starts from whatever `main` resolves to if such a branch
happens to exist but is not the default.

**The information is already on disk and already free to read.** `git clone` writes
`refs/remotes/origin/HEAD` pointing at the remote's default branch, and every other line on
that screen was produced by reading the same clone. Nothing has to be fetched, and no new
credential, network call or record is required to answer the question correctly.

**The configuration key is still wanted, but as an override rather than as the answer.**
`[worker] base_branch` and `[repos.*] base_branch` exist so the maintainer can say "branch
work off `develop` in this repository even though its default is `main`". That is a real
need and it survives. What must stop is a *default* answering a question the repository can
answer for itself — and, because `[worker] base_branch = "main"` ships live in
`share/config.example.toml`, the answer today is very often an explicit `"main"` that the
maintainer copied rather than chose.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Onboarding a `master` repository shows `master` (Priority: P1)

The maintainer onboards a repository whose default branch is not `main`. The approval screen
names the repository's actual default branch as the base ref, and the committed
tool-permission settings shown below it are the ones committed on that branch — so the thing
approved is the thing that will be honoured.

**Why this priority**: It is the reported bug, and it is the one place where a wrong base ref
causes a human to approve a repository on the strength of a screen that showed them nothing.

**Independent Test**: Onboard a clone whose `origin/HEAD` points at `master` and read the
screen: the base ref line says `master`, and any `.claude/settings*.json` committed on
`master` is printed in full.

**Acceptance Scenarios**:

1. **Given** a clone whose default branch is `master` and no `base_branch` configured for
   it, **When** the maintainer runs `robot-army onboard`, **Then** the base ref line reads
   `master` and says where that answer came from.
2. **Given** that same clone with `.claude/settings.local.json` committed on `master`,
   **When** the maintainer runs `robot-army onboard`, **Then** the file's contents are
   printed under "committed tool-permission settings at the base ref" and the recorded
   fingerprint covers it.
3. **Given** a clone whose default branch is `main`, **When** the maintainer runs
   `robot-army onboard`, **Then** the base ref line reads `main` exactly as it does today.
4. **Given** a clone from which the default branch cannot be determined, **When** the
   maintainer runs `robot-army onboard`, **Then** the configured value is used, the screen
   says so, and onboarding is not refused.

---

### User Story 2 - The session is created from the branch that was approved (Priority: P1)

A dispatched session in a `master` repository gets a worktree branched from `master`, its
gate re-check reads `master`, and its work is compared against `master` for landing and
ahead-count. Every consumer of the base ref agrees with the screen the maintainer approved.

**Why this priority**: Fixing only the display would leave the more damaging half of the bug
in place — a repository that onboards cleanly and then fails, or starts work from the wrong
branch, at dispatch time.

**Independent Test**: Dispatch into a `master` repository and confirm the worktree's branch
was created from `master`, without touching any configuration.

**Acceptance Scenarios**:

1. **Given** an onboarded `master` repository with no `base_branch` configured, **When** a
   session is dispatched, **Then** the worktree branch is created from `master` and the
   fetch and fast-forward name `master`.
2. **Given** the same repository, **When** the dispatch gates re-compute the settings
   fingerprint, **Then** they read the same ref onboarding read, so an unchanged repository
   does not report a changed fingerprint.
3. **Given** the same repository, **When** `robot-army repos` and `robot-army worktrees` are
   run, **Then** the base ref they use is `master`.

---

### User Story 3 - A configured base branch still wins (Priority: P2)

The maintainer sets `base_branch` for a repository whose default branch is something else,
and that setting is obeyed everywhere, unchanged.

**Why this priority**: The override is the reason the key exists; detection is worthless if
it cannot be turned off for the one repository that needs a different answer.

**Independent Test**: Set `[repos."owner/name"] base_branch = "develop"` on a repository
whose default branch is `main` and confirm every surface says `develop`.

**Acceptance Scenarios**:

1. **Given** `[repos."owner/name"] base_branch = "develop"` and a clone whose default branch
   is `main`, **When** the maintainer runs `robot-army onboard owner/name`, **Then** the base
   ref is `develop` and the screen attributes it to the repository's configuration.
2. **Given** no per-repository setting and a clone whose default branch cannot be
   determined, **When** any surface resolves the base ref, **Then** it uses `[worker]
   base_branch`, or `main` if that is not set either.

---

### Edge Cases

- **`origin/HEAD` is absent.** A clone made with `--single-branch`, an old clone, or one
  where the remote's default was never recorded has no such ref. The system falls back to the
  configured value and says which answer it used; it does not refuse to onboard and does not
  invent a branch.
- **`origin/HEAD` is stale.** The remote's default has been renamed since the clone was made,
  so the local ref names a branch that no longer exists. This is indistinguishable from a
  correct answer without a network call; the resulting failure is the existing "that ref does
  not resolve" failure, which is already reported rather than swallowed.
- **The primary remote is not `origin`.** The clone's remote is already resolved when the
  repository's identity is verified; detection asks the same remote, not a hardcoded name.
- **The detected branch does not exist locally.** Detection names a remote-tracking branch;
  reading files at that ref and creating a worktree from it must work in a clone that has
  never checked the branch out.
- **The path is not a git repository at all**, or git fails. Detection produces no answer,
  the configured value is used, and the failure is recorded rather than swallowed.
- **The repository is not onboarded yet.** Detection happens against the clone path
  onboarding has already resolved, so it can answer before there is any record.
- **A repository onboarded before this change.** Its recorded fingerprint may have been taken
  at a ref that does not exist. It becomes a fingerprint mismatch, which the existing gate
  already reports with `onboard --reapprove` as the remedy.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST determine a repository's default branch by reading the clone,
  using the same remote its identity was verified against.
- **FR-002**: Detection MUST require no network call and no credential. Reading a local clone
  is the whole of it.
- **FR-003**: The base ref for a repository MUST be resolved by one rule, used by every
  surface: a `base_branch` explicitly configured for that repository wins; otherwise the
  detected default branch wins; otherwise the globally configured `base_branch` is used;
  otherwise `main`.
- **FR-004**: The onboarding screen MUST state where the base ref came from — detected from
  the clone, or configured — in the same style as the clone-path line it sits under.
- **FR-005**: The committed settings shown, and the fingerprint recorded, at onboarding MUST
  be read at the resolved base ref.
- **FR-006**: Dispatch's fingerprint gate, worktree creation, the landed check, the
  ahead-count and every listing MUST resolve the base ref by FR-003, so no two surfaces can
  disagree about it.
- **FR-007**: When detection produces no answer, the system MUST fall back to the configured
  value, continue, and make the fallback visible where the base ref is displayed.
- **FR-008**: Detection MUST be recorded as an action against the clone, consistent with
  every other read of a repository the system performs.
- **FR-009**: The audit record written when a repository is onboarded MUST carry the base ref
  that was approved and where it came from.
- **FR-010**: The example configuration MUST NOT ship a live `base_branch` value that would
  override detection, and the guide MUST explain the resolution order in FR-003.
- **FR-011**: A repository with no clone on disk, or whose clone is unreadable, MUST behave
  exactly as it does today — detection failing is not a new refusal.

### Key Entities

- **Base ref**: the branch new work is created from and compared against. Currently a
  configuration value with a default; after this change, a resolved answer with a stated
  provenance.
- **Detected default branch**: what the clone says its remote's default is. An observation,
  not a setting; not persisted, because re-reading it costs nothing and a stored copy could
  disagree with the clone it was copied from.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Onboarding a clone whose default branch is `master`, with no configuration
  changes, shows `master` as the base ref and prints the settings committed on `master`.
- **SC-002**: A session dispatched into that repository gets a worktree branched from
  `master`, with no configuration changes.
- **SC-003**: Every surface that names a base ref for the same repository names the same
  branch.
- **SC-004**: A repository with `base_branch` configured behaves exactly as it does today,
  detection notwithstanding.
- **SC-005**: A clone with no discoverable default branch onboards successfully, using the
  configured value, and says that is what it did.
- **SC-006**: The full test suite passes, including tests covering detection failure and a
  non-`origin` remote.

## Assumptions

- The clone is a real clone made by `git clone`, so `refs/remotes/<remote>/HEAD` is normally
  present. Its absence is handled, not assumed away.
- Nothing new is persisted. The base ref is resolved from configuration plus the clone each
  time it is needed, exactly as trust and the fingerprint already are.
- The detected value is not re-approved by itself: if the default branch is renamed later,
  the resulting fingerprint change is caught by the gate that already exists, and the remedy
  is the one already documented.
- `[worker] base_branch` and `[repos.*] base_branch` keep their names and their meaning as
  overrides. This changes what happens when neither is set, and demotes the global key
  beneath the repository's own answer.
- The maintainer's existing configuration may contain an explicit `[worker] base_branch =
  "main"` copied from the shipped example. The resolution order in FR-003 is chosen so that
  such a copy does not reinstate the bug.
