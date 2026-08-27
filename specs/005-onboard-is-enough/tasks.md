---

description: "Task list for Onboarding Is Enough (milestone 005)"
---

# Tasks: Onboarding Is Enough

**Input**: Design documents from `/specs/005-onboard-is-enough/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional. The constitution's Development Workflow requires unit tests for
every new or changed unit of behaviour, and *additional* failure-and-interruption tests for
persistence and recovery logic — which a migration and an approval record both are. It also says
test-first is **not** mandatory and coverage targets **must not** be adopted, so test tasks sit beside
the code they cover rather than ahead of it. Write them in whichever order suits the work; the gate is
that they exist, are meaningful, and pass.

Three tests in this milestone are worth more than the rest, and all three are in the group CI cannot
fully run: the one asserting the five real wrong-location clones are refused (T041), the one asserting
a clone that moves after approval never produces a worktree (T057), and the one asserting no
credential from a clone URL reaches any record (T042). They guard the only failure here that is
expensive rather than annoying — a branch created in a repository the author never named.

**Organization**: By user story, in the priority order spec.md assigns. One maintainer, so `[P]` marks
work that does not collide — not work that needs a second person.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Touches files no other pending task touches; safe to interleave
- **[Story]**: US1–US7, mapping to spec.md's user stories
- Every task names its exact file path

## Path Conventions

Single project, as 001–004 established: `src/robot_army/`, `tests/unit/`, `tests/integration/`. The
one new module is `src/robot_army/repos.py`.

---

## Phase 1: Setup

**Purpose**: The new module's skeleton and the one configuration key every story reads.
`[hooks] post_create` is deliberately **not** here — it belongs to US4, so that story stays
independently droppable, following the rule 004 used for `[cleanup]` and `[notifications]`.

- [X] T001 Create `src/robot_army/repos.py` with a module docstring stating the boundary the plan's Structure Decision draws: it answers questions and performs no actions, so derivation, normalisation, comparison and the record-over-section-over-default join live here while every audit write and every decision to record stays at its existing call site
- [X] T002 Add `repo_root` to `[paths]` in `src/robot_army/config.py` per [contracts/config.md](contracts/config.md) — a single `Path`, defaulting to `~/GIT`, expanded like the other `[paths]` values, hung off `Config` alongside `worktree_root`
- [X] T003 Add `repo_root` to the `"paths"` entry of `_KNOWN_KEYS` in `src/robot_army/config.py` and validate it at load: absent, or present but not a directory, is a **problem** reported with every other configuration problem rather than discovered per repository at onboarding time (FR-001) (depends on T002)
- [X] T004 [P] Extend `tests/unit/test_config.py` with cases for the `repo_root` default, an explicit override, `~` expansion, a path that does not exist refusing to load, and a path that is a file rather than a directory refusing to load

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The pure logic, the persistence, and the two boundary reads every story is built on.
Nothing acts on any of it yet — no command changes behaviour until Phase 3.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement remote-URL normalisation in `src/robot_army/repos.py` per [research.md](research.md) R2: parse `git@host:owner/name.git`, `https://host/owner/name`, and `ssh://git@host/owner/name` into a `(host, owner, name)` triple, stripping any `userinfo@` component **first**, stripping a trailing `.git`, and lowercasing all three parts; anything that does not parse into exactly that shape returns no result rather than a partial one
- [X] T006 [P] Add `tests/unit/test_repos.py` cases for normalisation: all three URL forms with and without `.git`, equivalence across forms for one repository, case differences comparing equal, an unparseable URL yielding no result, and — the case FR-032 exists for — a URL embedding `user:token@` normalising with the credential absent from the result (depends on T005)
- [X] T007 Implement `derive_path(config, repo_key)` in `src/robot_army/repos.py`: exactly one candidate, `<repo_root>/<name>` where `name` is the second segment of the `owner/name` key. No search, no walk, no `<repo_root>/<owner>/<name>` fallback (FR-002) (depends on T002)
- [X] T008 [P] Add `tests/unit/test_repos.py` cases for derivation: the ordinary case, a key whose name contains a dot or a hyphen, a malformed key with no slash, and an assertion that exactly one path is produced and no filesystem access occurs (depends on T007)
- [X] T009 Add `remote_url(clone_path, remote)` to `VersionControl` in `src/robot_army/boundaries/git.py`, and to the `Boundaries` protocol, returning the configured URL or `None`; add the matching method to `SimulatedVersionControl` performing the **real** read, following that class's existing rule that cheap side-effect-free reads answer honestly, because "what repository is at this path" has one true answer at every effect level (research R3)
- [X] T010 [P] Add `tests/unit/test_git_boundary.py` cases for `remote_url`: a clone with `origin`, a clone with a single differently-named remote, a clone with no remotes returning `None`, and the simulated implementation returning the same answer as the real one
- [X] T011 Implement the remote-selection rule in `src/robot_army/repos.py`: prefer `origin`; fall back to the sole remote when exactly one exists and report which was used; refuse as ambiguous when several exist and none is `origin` — deliberately stricter than `default_remote()`, which may pick arbitrarily because it is choosing where to fetch rather than deciding identity (research R3) (depends on T009)
- [X] T012 Implement the primary-clone check in `src/robot_army/repos.py`: the resolved path is a primary clone when its `.git` is a **directory**, since a linked worktree's `.git` is a file holding a `gitdir:` pointer (research R4). A `stat`, not a subprocess
- [X] T013 [P] Add `tests/unit/test_repos.py` cases for the primary-clone check against a real fixture repository and a real linked worktree cut from it, marked `requires_git` (depends on T012)
- [X] T014 Add a single-repository lookup to `GitHubReader` in `src/robot_army/boundaries/github.py` returning existence, `owner.login`, canonical name, and default branch from one `GET /repos/{owner}/{name}`, routed through the existing `_request` so it inherits the timeout and bounded backoff FR-008 requires (research R5)
- [X] T015 [P] Add `tests/unit/test_github_boundary.py` cases for the lookup: an owned repository, a repository owned by someone else, a repository that does not exist, and an assertion that **one** request is issued — not a page walk (SC-009)
- [X] T016 Add migration 005 to `src/robot_army/migrations.py` per [data-model.md](data-model.md): four nullable columns on `repos` — `clone_path`, `path_source`, `verified_origin`, `origin_verified_at` — as a `SCHEMA_005_SQL` block with the comment explaining why the outcome is recorded rather than the rule, appended to the `MIGRATIONS` ladder
- [X] T017 Extend `Repo` in `src/robot_army/models.py` with the four new fields, all defaulting to `None`, and document on the dataclass that a `NULL` `clone_path` means *onboarded, location never verified* rather than "onboarded at an unknown path" (depends on T016)
- [X] T018 Extend `db.upsert_repo` in `src/robot_army/db.py` to write the four columns, keeping `get_repo` and `list_repos` unchanged since both `SELECT *` (depends on T017)
- [X] T019 [P] Add `tests/unit/test_migrations.py` cases for migration 005: it runs on a 004-era database, a killed migration leaves `user_version` unadvanced and re-runs cleanly with no half-applied column set observable, and pre-existing rows read back with `clone_path` as `NULL` and their fingerprint intact (depends on T016)
- [X] T020 Implement `known(conn)` in `src/robot_army/repos.py` returning the onboarded repository keys from the `repos` table — the replacement for `sorted(config.repos)` at every site meaning "which repositories does this system watch"
- [X] T021 Implement `resolve(conn, config, key)` in `src/robot_army/repos.py` returning a `RepoConfig`-shaped result built by the precedence table in [data-model.md](data-model.md): the record wins `path` and only `path`; the section wins every other field it sets; the existing global defaults fill the rest. Shaped identically to today's `RepoConfig` so call sites do not change shape (depends on T018, T020)
- [X] T022 [P] Add `tests/unit/test_repos.py` cases for `known` and `resolve`: an onboarded repository with no section resolving entirely from defaults, one with a section overriding each field in turn, the record's path winning over a section's, and an unonboarded key resolving to nothing (depends on T021)

**Checkpoint**: the resolved view exists and is tested. Nothing reads it yet.

---

## Phase 3: User Story 1 - Onboard, and nothing else (Priority: P1) 🎯 MVP

**Goal**: `robot-army onboard <owner>/<name>` makes a repository pollable and dispatchable with no
file edit, and the polled set comes from the onboarding record rather than the configuration file.

**Independent Test**: Onboard a repository with no `[repos.*]` section, label an issue in it, and watch
a session appear in a repository the configuration file does not mention.

**Note on shipping this alone**: US1 is not safely shippable without US2. The derivation rule is right
for 222 of 252 repositories and *wrong* for five, and the wrong five fail by doing real work in the
wrong place. Treat US1+US2 as the MVP; this phase is separated from the next for reviewability, not
because it is independently deployable.

- [X] T023 [US1] Make `path` optional in `[repos.*]` in `src/robot_army/config.py` — absent means derive, present means use as-is — keeping the existing "configured path does not exist" load-time problem for the explicit case (FR-003)
- [X] T024 [US1] Replace `onboard`'s early `EXIT_USAGE` return for a missing section in `src/robot_army/operations.py` with resolution: the section's `path` when one exists, otherwise `derive_path`, recording which in `path_source` (depends on T007, T023)
- [X] T025 [US1] Add the three resolution lines to `onboard`'s approval screen in `src/robot_army/operations.py` per [contracts/onboarding.md](contracts/onboarding.md) — clone path with `(derived from [paths] repo_root)` or `(configured in [repos."key"])`, and the `verified:` line — printed **before** the trust and committed-settings output, because which repository is about to be trusted must be settled before anything about trust is read (FR-011) (depends on T024)
- [X] T026 [US1] Extend the `repo.onboard` audit detail and the `db.upsert_repo` call in `src/robot_army/operations.py` to carry the resolved path, `path_source`, the remote consulted, the normalised comparison result, and the ownership verdict, inside the existing single transaction (depends on T018, T024)
- [X] T027 [US1] Change `poll_all` in `src/robot_army/poll.py` to walk `repos.known(conn)` instead of `sorted(config.repos)`, and change the per-repository eligibility check at `poll.py:85` to test onboarding rather than section presence (FR-015, FR-016) (depends on T020)
- [X] T028 [US1] Change `dispatch_item` in `src/robot_army/dispatch.py` to obtain its `RepoConfig` from `repos.resolve` rather than `config.repos.get`, keeping the existing failure when nothing resolves (depends on T021)
- [X] T029 [P] [US1] Change the `config.repos` reads in `src/robot_army/reconcile.py`, `src/robot_army/cleanup.py`, and `src/robot_army/ordering.py` to `repos.resolve` / `repos.known`, with no behaviour change intended at any of them (depends on T021)
- [X] T030 [US1] Change the nine `ctx.config.repos` reads in `src/robot_army/operations.py` — including the `repos` verb's listing — to the resolved view, and make that verb report a repository with a section but no onboarding record as **not onboarded** rather than listing it as known (FR-017) (depends on T021)
- [X] T031 [US1] Change `_key_for_path`, `_offer`, and the "no configured repository could be identified" message in `src/robot_army/intake.py` to use resolved clone paths and `repos.known`, and reword `configured:` to `onboarded:` — the consumer research R8 found, which reads clone *paths* and not only keys, and whose failure is a card held as `needs_info` telling the author to name a repository they already named (depends on T021)
- [X] T032 [P] [US1] Add `tests/integration/test_onboard.py` covering the headline path end to end: a repository with no section is onboarded, appears in `known`, is polled, and dispatches into a worktree cut from the derived clone
- [X] T033 [P] [US1] Add a case to `tests/integration/test_onboard.py` asserting that a repository onboarded **while a daemon is running** is polled on the next cycle with no restart, and that a repository appearing between cycles needs no special handling because `poll_state` is keyed by repository — the behaviour change research R7 records rather than assumes (depends on T027)
- [X] T034 [P] [US1] Add a case to `tests/unit/test_poll.py` asserting a repository with a `[repos.*]` section and no onboarding record is neither polled nor dispatchable, and that `robot-army repos` says so — the one intentional breaking change in this milestone (depends on T027)
- [X] T035 [P] [US1] Add cases to `tests/integration/test_card_to_issue.py` asserting a card resolves to an onboarded repository that has **no** section, both by GitHub URL and by a filesystem path inside its clone (depends on T031)

**Checkpoint**: onboarding is enough — for a repository whose derived path happens to be right.

---

## Phase 4: User Story 2 - A path that resolves to the wrong repository is refused (Priority: P2)

**Goal**: The derivation rule never silently points at a different repository.

**Independent Test**: Onboard each of the five known wrong-location repositories and confirm all five
are refused with the actual repository named. No dispatch, no session required.

- [X] T036 [US2] Implement the verification sequence in `src/robot_army/repos.py` per [contracts/onboarding.md](contracts/onboarding.md) steps 4–9: exists, primary clone, not inside `worktree_root`, remote selected, URL normalised, identity compared against the repository key and the configured API host — returning a typed refusal naming which step failed rather than a boolean (depends on T005, T011, T012)
- [X] T037 [US2] Wire the verification sequence into `onboard` in `src/robot_army/operations.py` so it runs before the approval screen and refuses with exit `3`, recording `verified_origin` and `origin_verified_at` on approval (depends on T026, T036)
- [X] T038 [US2] Write the refusal messages in `src/robot_army/operations.py` to match [contracts/onboarding.md](contracts/onboarding.md) exactly: each names the path, how it was arrived at, and the edit that fixes it, and the wrong-repository message names **both** identities. None may degrade to a generic "invalid configuration" (FR-009) (depends on T037)
- [X] T039 [US2] Make every refusal path in `onboard` write an audit outcome with its cause in `src/robot_army/operations.py`, including the pre-existing missing-section return that writes nothing today — a live Principle III violation this milestone inherits rather than introduces (research R11, FR-031) (depends on T038)
- [X] T040 [P] [US2] Add cases to `tests/integration/test_onboard.py` for each refusal cause distinctly — absent clone, linked worktree, path inside the worktree root, no remote, several remotes with no `origin`, unparseable URL, wrong repository — asserting exit `3`, a distinct message, and **an audit record for each** (depends on T039)
- [X] T041 [US2] Add `tests/integration/test_onboard.py::test_wrong_repository_at_derived_path` building real clones whose origin is a different repository, marked `requires_git`, reproducing the shape of all five known cases: a different owner, a different name under the same owner, and an unrelated upstream. Assert none is recorded and each names the found identity (depends on T036)
- [X] T042 [US2] Add a case to `tests/integration/test_onboard.py` asserting that a clone whose origin URL embeds `user:token@` is onboarded successfully with the credential appearing in **no** record, **no** message, and **no** terminal output, and that the same holds on the refusal path when the comparison fails (FR-032) (depends on T037)
- [X] T043 [P] [US2] Add a case to `tests/unit/test_repos.py` asserting a clone of the same `owner/name` on a different host is refused, since a same-named repository on another forge fails identically to a different repository (research R2)

**Checkpoint**: the five known collisions are refused. US1 is now safe to rely on.

---

## Phase 5: User Story 3 - The exceptions keep working (Priority: P3)

**Goal**: `[repos.*]` becomes a pure override, and nothing about an existing configuration changes.

**Independent Test**: Onboard a repository with an explicit unconventional `path`, confirm derivation
is never consulted, and confirm an existing full configuration behaves identically to before.

- [X] T044 [US3] Confirm and test in `src/robot_army/repos.py` that a configured `path` suppresses derivation entirely while still running the full verification sequence — a configured path can be wrong as easily as a derived one (FR-007) (depends on T036)
- [X] T045 [US3] Add the recorded-path-versus-configured-path check to `check_gates` in `src/robot_army/dispatch.py`: a `[repos.*] path` that disagrees with `clone_path` blocks dispatch pending `onboard --reapprove`, raising the existing `DispatchBlocked` (FR-013) (depends on T021)
- [X] T046 [US3] Make `onboard --reapprove` in `src/robot_army/operations.py` re-resolve and re-verify, showing the recorded path beside the newly resolved one alongside the fingerprint diff it already shows (depends on T037, T045)
- [X] T047 [P] [US3] Add cases to `tests/integration/test_onboard.py`: a configured path is used and reported as `configured`, a configured path pointing at the wrong repository is refused, and a repository with an explicit path and its own `post_create` behaves identically before and after this milestone (SC-008)
- [X] T048 [P] [US3] Add a case to `tests/integration/test_dispatch.py` asserting a `path` changed in the configuration after onboarding blocks dispatch naming both paths, rather than silently taking effect or silently losing (depends on T045)

**Checkpoint**: every existing configuration still works, and the file is now a list of exceptions.

---

## Phase 6: User Story 4 - Preparation steps have a default (Priority: P4)

**Goal**: A repository onboarded with no section runs the shared preparation steps rather than none.

**Independent Test**: Onboard two repositories, give one its own steps, and confirm the shared default
runs in the other and the override runs in the first.

- [X] T049 [US4] Add `post_create` to `[hooks]` in `src/robot_army/config.py` per [contracts/config.md](contracts/config.md), parsed by the existing `_parse_steps` so it inherits the same shape, the same per-step key validation, and `default_timeout_seconds` for a step that sets none
- [X] T050 [US4] Add `hooks.post_create` to the `"hooks"` entry of `_KNOWN_KEYS` in `src/robot_army/config.py` and make an unknown key inside a shared step a **problem**, matching the per-repository form (depends on T049)
- [X] T051 [US4] Make `resolve()` in `src/robot_army/repos.py` fall back to `config.hooks.post_create` when a repository defines none, as a **replacement** relationship — a repository's own steps replace the shared default and there is no way to request both (FR-020, research R10) (depends on T021, T049)
- [X] T052 [US4] Extend the startup timeout budget calculation in `src/robot_army/config.py` to count inherited shared steps for **every** repository that inherits them; counting them once under-reports for the majority of repositories after this milestone (FR-022) (depends on T049)
- [X] T053 [P] [US4] Add cases to `tests/unit/test_config.py` and `tests/unit/test_worktree.py`: shared steps running for a repository with no section, a repository's own steps replacing rather than appending, neither set producing no steps at all (today's behaviour preserved), an invalid shared step refusing to load, and the budget warning counting inherited steps per inheriting repository (depends on T051, T052)

**Checkpoint**: an onboarded repository with no section dispatches into a *prepared* worktree.

---

## Phase 7: User Story 5 - The clone moved (Priority: P5)

**Goal**: A clone that moves or is replaced after approval produces a refusal, never a worktree
somewhere unexpected.

**Independent Test**: Onboard a repository, rename its clone directory, attempt a dispatch, and confirm
a `failed` item, an anomaly, and no worktree anywhere.

- [X] T054 [US5] Add the fourth precondition to `check_gates` in `src/robot_army/dispatch.py` per [contracts/onboarding.md](contracts/onboarding.md): the recorded path still exists, is still a primary clone, and still normalises to the same repository — three local reads, no fetch, raising the existing `DispatchBlocked` so the failure path is existing code (FR-028, research R9) (depends on T012, T036)
- [X] T055 [US5] Make a `NULL` `clone_path` — a row predating migration 005 — block dispatch in `src/robot_army/dispatch.py` naming `onboard --reapprove`, with no backfill and no guess (FR-014, research R6) (depends on T054)
- [X] T056 [US5] Raise an anomaly from the path-missing and origin-changed branches in `src/robot_army/dispatch.py`, distinct from an ordinary gate refusal, since both mean the machine changed under an approval rather than that a precondition was never met (depends on T054)
- [X] T057 [US5] Add `tests/integration/test_dispatch.py::test_recorded_clone_moved` asserting that a clone renamed after approval fails the item naming the **recorded** path, raises an anomaly, creates **no** worktree anywhere, and specifically does **not** re-derive or find another directory matching the name (SC-005) (depends on T054)
- [X] T058 [P] [US5] Add a case to `tests/integration/test_dispatch.py` asserting that a *different* repository cloned into the recorded path is refused naming both identities — scenario 3's failure arriving months later, and the case a re-derivation design would silently get wrong (depends on T054)
- [X] T059 [P] [US5] Add a case to `tests/integration/test_dispatch.py` asserting `onboard --reapprove` after either failure re-resolves, re-verifies, and lets dispatch resume (depends on T046, T054)

**Checkpoint**: no worktree is ever created in a repository the work item did not name.

---

## Phase 8: User Story 6 - What may be onboarded at all (Priority: P6)

**Goal**: `include_owned` and `extra_repos` acquire the meaning their names imply, closing issue #8.

**Independent Test**: Attempt to onboard a repository the author neither owns nor listed; confirm the
refusal names the setting. No session, no dispatch.

- [X] T060 [US6] Implement the eligibility check in `src/robot_army/repos.py` using the single-repository lookup from T014: permitted when the author owns it and `include_owned` is true, or when it is listed in `extra_repos` (FR-023, FR-025) (depends on T014)
- [X] T061 [US6] Wire eligibility into `onboard` in `src/robot_army/operations.py` as step 1 of the resolution order, before the path is even derived, refusing with exit `3` and naming which setting would have permitted it (FR-024) (depends on T037, T060)
- [X] T062 [US6] Document in `src/robot_army/repos.py` and in the refusal message that this is a **mistake guard and not a security boundary** — the issue-author check remains the boundary and cannot be disabled — so the distinction survives contact with whoever reads this next (FR-026) (depends on T061)
- [X] T063 [P] [US6] Add cases to `tests/integration/test_onboard.py`: an owned repository permitted, an unowned unlisted repository refused naming `extra_repos`, an owned repository refused naming `include_owned` when it is false, a nonexistent repository refused distinctly from an unowned one, and an already-onboarded repository continuing to work after the setting that permitted it is removed (FR-027) (depends on T061)
- [X] T064 [P] [US6] Add a case to `tests/integration/test_onboard.py` asserting exactly **one** repository request per onboarding attempt and no page walk, since a fake with three repositories would otherwise pass an implementation that enumerates 252 (FR-025, SC-009) (depends on T060)

**Checkpoint**: issue #8's two keys do what they say.

---

## Phase 9: User Story 7 - See what could be onboarded (Priority: P7, droppable)

**Goal**: A listing of owned repositories not yet onboarded, so the author picks from a list rather
than remembering names.

**Independent Test**: Run the listing against a live account and confirm already-onboarded
repositories are marked rather than offered.

**This story is genuinely droppable and is the decision point for issue #8's dead-code half.** Decide
before starting it, and do T065–T066 **or** T067 — never neither.

> **Decided 2026-08-27: dropped.** T067 was done instead — `list_owned_repos` and its `Boundaries`
> declaration are gone, and so is `RepoRef`, whose only producer it was. T065 and T066 are therefore
> **not applicable** rather than outstanding. The reasoning is Principle I and the shape of this
> milestone: every other story removes a step, and this one would have added a surface whose job
> ("which of my repositories could I onboard?") is already served by typing the name.

- [~] T065 (not applicable — US7 dropped) [US7] Add `--onboardable` to the `repos` verb in `src/robot_army/operations.py` and `src/robot_army/cli.py`, listing repositories the author owns that are not yet onboarded, each with whether a clone was found at its derived location and whether that clone's origin matches — read-only, onboarding nothing (depends on T020, T036)
- [~] T066 (not applicable — US7 dropped) [P] [US7] Add cases to `tests/unit/test_operations.py` asserting already-onboarded repositories are marked rather than offered, and that the listing performs no write and creates no onboarding record (depends on T065)
- [X] T067 [US7] **If this story is dropped instead**: delete `list_owned_repos` from `src/robot_army/boundaries/github.py` and its declaration from `src/robot_army/boundaries/__init__.py`. An implemented method with no caller is the exact state issue #8 reports, and closing that issue while leaving one behind would be a poor joke

**Checkpoint**: `list_owned_repos` either has a caller or is gone. Not both, and not neither.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T068 [P] Add the four `repos` columns and their values to `docs/state.md`, including that `verified_origin` stores the normalised identity and never a raw URL, and that a `NULL` `clone_path` means *onboarded, location never verified*
- [X] T069 [P] Add the changed `repo.onboard` audit detail and the new refusal outcomes to `docs/logging.md`, and record the rule that the dispatch-time re-verification writes only on failure because success is implied by the worktree record that follows it milliseconds later on the same item
- [X] T070 [P] Correct the three places that describe `include_owned` as controlling polling, per [contracts/config.md](contracts/config.md): `share/config.example.toml`, `specs/001-minimum-daemon/contracts/config.md`, and `README.md` — the key never controlled polling, and polling is not what it should govern
- [X] T071 [P] Rewrite `README.md`'s setup section so onboarding is shown as sufficient and `[repos.*]` is presented as the exception mechanism, including the "a configuration file after this milestone" example from [contracts/config.md](contracts/config.md)
- [X] T072 Update `docs/roadmap.md`: mark 005 as this milestone rather than the "whatever survives contact with reality" parking lot, renumber that parking lot to 006, and record what running this actually taught — in particular whether the derivation rule held for anything beyond the 222 measured
- [X] T073 [P] Amend `specs/001-minimum-daemon/spec.md:576`, whose recorded decision says the author's own repositories are enumerated from GitHub; note that the second half of that sentence is what 005 implements and the first half is superseded because nothing needs to enumerate
- [X] T074 Close [issue #8](https://github.com/jantman/robot-army/issues/8) with a pointer to this milestone rather than a separate fix, and add a note to [issue #1](https://github.com/jantman/robot-army/issues/1) that milestone 001's scenario 6 must be re-run because this changes what onboarding refuses and what it prints
- [X] T075 Run `ruff check` and `ruff format --check` across `src/` and `tests/`, and the full `pytest` suite; the constitution's gate is that the suite passes, not a coverage number
  - `ruff check`: **clean**. `pytest`: **1232 passed, 1 skipped** (the skip is the pre-existing
    live-Trello case).
  - `ruff format --check`: **fails, and failed before this milestone too** — 81 of 107 files at
    the previous commit, 82 now. This repository has never been run through `ruff format`;
    `[tool.ruff.lint]` is configured and `[tool.ruff.format]` is not, so `ruff check` is the gate
    the project actually enforces. Reformatting 82 files is a separate decision and was **not**
    made here: it would bury this milestone's diff and change files it does not touch.
- [ ] T076 **Blocked on the live machine — cannot be done from here.** Walk [quickstart.md](quickstart.md) end to end on the real machine, including the three scenarios CI cannot run: 3 needs the five real wrong-location clones, 5 needs a clone to move out from under an approval, and 9's request count needs a real account with 252 repositories to be meaningful
- [ ] T077 **Blocked on the live machine — follows T076.** Delete the sandbox repositories and any throwaway clones created for quickstart scenarios 3, 5 and 11, and confirm no `robot-army/*` branch exists in any repository that was not a deliberate target — the negative check SC-004 asks for, performed once by hand at the end

> **T076 and T077 are outstanding and cannot be completed from a development session.** Both need
> the author's real machine: the five wrong-location clones, a real account with 252 repositories,
> and a clone physically moved out from under an approval. They are recorded in
> [issue #1](https://github.com/jantman/robot-army/issues/1) with the specific checks to run.
> Everything else in this milestone is done and the suite passes.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on US1 — it adds refusals to a resolution path US1 builds
- **US3 (Phase 5)**: depends on US2 — a configured path is verified by US2's sequence
- **US4 (Phase 6)**: depends on Foundational only; independent of US2, US3 and US5
- **US5 (Phase 7)**: depends on US2 — it re-runs US2's verification at a later moment
- **US6 (Phase 8)**: depends on Foundational only, though its refusal joins US2's taxonomy
- **US7 (Phase 9)**: depends on Foundational only
- **Polish (Phase 10)**: depends on every story being shipped

### Honest note on story independence

**US1 and US2 are not separable in practice, and pretending otherwise would mislead.** US1 alone
gives a derivation rule that is right 222 times and wrong five times, and the five fail by cutting a
worktree and a branch in a repository the author never named. The phases are split for reviewability
— one adds a capability, one adds its guard — but the MVP is both, and shipping US1 alone would be a
worse system than the one that exists today.

US3 and US5 are genuine extensions of US2 rather than independent stories: US3 applies its
verification to a configured path, US5 applies it at a later moment. US4, US6 and US7 are genuinely
independent of the US1→US2→US3/US5 chain and of each other — any of them can be built, shipped, or
dropped without touching the others.

### Parallel opportunities

- Setup: T004 alongside T002–T003
- Foundational: T006, T008, T010, T013, T015, T019 alongside the code they cover; the two boundary
  tasks (T009, T014) and the persistence tasks (T016–T018) touch disjoint files and can interleave
- US1: T032–T035 in parallel once T031 lands; T029 alongside T027/T028
- US2: T040, T042, T043 in parallel once T038 lands
- US3: T047 and T048 in parallel once T046 lands
- US5: T058 and T059 in parallel once T057 lands
- US6: T063 and T064 in parallel once T061 lands
- Polish: T068, T069, T070, T071, T073 all touch different files
- **Across stories**: once Foundational is done, US4, US6 and US7 can each proceed alongside the
  US1 → US2 chain

---

## Implementation Strategy

### MVP: User Stories 1 and 2 together

1. Complete Phase 1 (Setup) and Phase 2 (Foundational) — Foundational is the bulk of the work
2. Complete Phase 3 (US1) and Phase 4 (US2)
3. **STOP and VALIDATE**: quickstart scenarios 1, 3 and 4. Scenario 3 is the gate — if any of the
   five known collisions onboards successfully, stop and fix it before anything else
4. At this point onboarding is enough, and it is safe

### Incremental delivery after the MVP

1. Add US4 → a repository with no section dispatches into a *prepared* worktree. This is the story
   that makes the MVP pleasant rather than merely correct, and it is small
2. Add US3 → existing configurations are formally exceptions; validates SC-008
3. Add US5 → the machine changing under an approval becomes a refusal
4. Add US6 → issue #8's keys acquire their meaning
5. Add US7, or drop it and do T067

### Sequencing against the rest of the project

**Do not start Phase 1 until the verification round in
[issue #1](https://github.com/jantman/robot-army/issues/1) is complete.** That round verifies
milestones 001–004 as built; this milestone changes onboarding, the polled set, and the dispatch
gates underneath it. Running the round first means it verifies a system rather than a moving target,
and it will produce its own findings that may change this plan before any of it is written.
