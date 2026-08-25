---

description: "Task list for Concurrency & Polish (milestone 004)"
---

# Tasks: Concurrency & Polish

**Input**: Design documents from `/specs/004-concurrency-polish/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included, and not optional here. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and *additional* failure-and-interruption tests for
persistence and recovery logic — which cleanup is. It also says test-first is **not** mandatory and
coverage targets **must not** be adopted, so test tasks sit beside the code they cover rather than
ahead of it. Write them in whichever order suits the work; the gate is that they exist, are
meaningful, and pass.

Two tests in this milestone are worth more than the rest, and both are in the group CI cannot run: the
one asserting the cap is not under-counted across the launch window (T021), and the one asserting no
branch with unpushed commits is ever deleted (T061). They guard the only two failures here that are
expensive rather than annoying.

**Organization**: By user story, in the priority order spec.md assigns, so each story is a shippable
increment. One maintainer, so `[P]` marks work that does not collide — not work that needs a second
person.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Touches files no other pending task touches; safe to interleave
- **[Story]**: US1–US6, mapping to spec.md's user stories
- Every task names its exact file path

## Path Conventions

Single project, as 001–003 established: `src/robot_army/`, `tests/unit/`, `tests/integration/`. New
code lands in `src/robot_army/capacity.py`, `ordering.py`, `cleanup.py`, `notifications.py`, and
`boundaries/notifier.py`.

---

## Phase 1: Setup

**Purpose**: The module skeletons and the one configuration section the ordering and cap work reads.
`[cleanup]` and `[notifications]` are deliberately **not** here — they belong to the stories that make
them mean something, so those stories stay independently droppable.

- [ ] T001 Create `src/robot_army/capacity.py`, `src/robot_army/ordering.py`, `src/robot_army/cleanup.py`, and `src/robot_army/notifications.py` as modules whose docstrings state the split the plan's Structure Decision makes — `capacity.py` observes the machine, `ordering.py` applies configuration, and only `ordering.py` depends on `capacity.py`
- [ ] T002 Add `DispatchConfig` (`order`, `default_repo_max_sessions`) as a frozen dataclass to `src/robot_army/config.py` per [contracts/config.md](contracts/config.md), defaulting to `order="oldest-first"` and `default_repo_max_sessions=1`, and hang it off `Config` as `config.dispatch`
- [ ] T003 Add `"dispatch"` to `_KNOWN_KEYS` in `src/robot_army/config.py` and parse the section in `parse()`, treating an unknown key in it as a **problem** rather than a warning — the same rule `[repos.*]` and `[trello]` already use, for the reason `config.py` states about typos silently disabling settings (depends on T002)
- [ ] T004 Add `[dispatch]` validation to `src/robot_army/config.py`: an `order` value outside the two known modes is a problem naming the key and listing the modes, and `default_repo_max_sessions` must be a positive integer (depends on T003)
- [ ] T005 [P] Extend `tests/unit/test_config.py` with cases for the `[dispatch]` defaults, an absent section leaving them at their defaults, an unknown mode refusing to load, a non-positive `default_repo_max_sessions` refusing to load, and a typo'd key in the section refusing to load

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two producers this whole milestone is built on — one that says how full the machine
is, one that says what order work sits in. Nothing acts on either yet.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Checkpoint**: at the end of this phase the system can state exactly how many sessions are running,
whose they are, and in what order the queue sits — and its dispatch behaviour is byte-for-byte
milestone 003's, because nothing consumes any of it.

### The registry gap that reads as free capacity

- [ ] T006 Add `directory_missing: bool` to `RegistryScan` in `src/robot_army/sessions.py` and set it in `scan()` when the registry directory does not exist or cannot be listed, distinguishing it from a directory that exists and is empty (R4) — this is the only failure mode that currently looks identical to an idle machine
- [ ] T007 [P] Add cases to `tests/unit/test_sessions.py` for a missing directory, an unreadable directory, and an empty-but-present directory, asserting the first two set `directory_missing` and the third does not

### Capacity

- [ ] T008 Add the `CapacitySnapshot` frozen dataclass to `src/robot_army/capacity.py` per [data-model.md](data-model.md) — `observable`, `degraded`, `total`, `ours: tuple[str, ...]`, `others: int`, `global_cap`, `per_repo`, `reason` — with `others` typed as an integer specifically so no control path can obtain a handle to a session the system did not start (R5, FR-006)
- [ ] T009 Implement `snapshot(conn, *, config, registry_dir=None, proc_root=None)` in `src/robot_army/capacity.py` following the seven steps in [contracts/dispatch-policy.md](contracts/dispatch-policy.md): scan, fall back to `/proc` when degraded **or** `directory_missing`, classify `ours` by `sessions.under_root`, and compute `total` as the **union by session id** of live registry entries and our own `starting`/`running` rows that match no entry (R3 — the launch window) (depends on T006, T008)
- [ ] T010 Add the unobservable path to `src/robot_army/capacity.py`: when the `/proc` fallback yields zero PIDs — impossible on a running machine, therefore an enumeration failure — return `observable=False` with a reason, write a `capacity.unobservable` audit record, and raise the de-duplicated `capacity_unobservable` anomaly via `db.raise_anomaly` (depends on T009)
- [ ] T011 Implement per-repository counting in `src/robot_army/capacity.py`: group live sessions by their work item's `repo_key`, counting simulated sessions exactly as live ones (FR-004), and leave out-of-band sessions unattributed because the author's own clone is not under the worktree root (depends on T009)
- [ ] T012 [P] Add `tests/unit/test_capacity.py` covering the union across the launch window (a `starting` row with no registry file still counts), the degraded `/proc` path, the unobservable path and its anomaly, simulated sessions counting toward both caps, and a recycled PID with a mismatched `proc_start` **not** counting
- [ ] T013 [P] Add a test to `tests/unit/test_capacity.py` asserting `CapacitySnapshot` exposes no PID, no process handle, and no session id for out-of-band sessions — the structural form of FR-006, so the guarantee is checked by the type rather than by reviewing every call site

### Order

- [ ] T014 Add the `HoldReason` StrEnum and the `QueueEntry` frozen dataclass to `src/robot_army/ordering.py` per [data-model.md](data-model.md), with the enum members declared in the precedence order [contracts/dispatch-policy.md](contracts/dispatch-policy.md) specifies so the precedence is readable in one place
- [ ] T015 Implement `plan(conn, *, config, capacity)` in `src/robot_army/ordering.py` as a pure function returning `QueueEntry` values for every `ready` item — reading via `db.list_work_items` (whose `ORDER BY id` stays as the stable input), assigning 1-based positions, and assigning the first applicable hold reason. This phase implements the `paused`, `capacity_unobservable`, and `global_cap` reasons only; `repo_cap` arrives with US2 and the ordering modes with US4 (depends on T014)
- [ ] T016 [P] Add `tests/unit/test_ordering.py` covering positions being 1-based and contiguous, the order being total and stable across repeated calls, and `plan()` performing no writes

---

## Phase 3: User Story 1 - My own work and the robot's work share one quota (Priority: P1) 🎯 MVP

**Goal**: the global cap counts every Claude session on the machine, not just the daemon's own, and
holds work rather than oversubscribing the author's subscription.

**Independent Test**: with the cap at two, start two Claude sessions by hand outside the daemon, make
one item eligible, and confirm nothing dispatches, the reason is visible from the terminal, and
dispatch happens on its own within one interval of closing one of them.

- [ ] T017 [US1] Rewrite `select_and_dispatch` in `src/robot_army/dispatch.py` to take one `capacity.snapshot` and walk `ordering.plan`, breaking on `paused`, `capacity_unobservable`, and `global_cap`, and re-snapshotting immediately before each individual dispatch so a batch cannot collectively exceed the cap and two overlapping passes cannot each see the same free slot (FR-009) (depends on T009, T015)
- [ ] T018 [US1] Retire `db.count_live_sessions` from `src/robot_army/db.py` and its call site, carrying its FR-055 reasoning — that simulated sessions count against the cap — into `capacity.snapshot`'s docstring, where it now lives (depends on T017)
- [ ] T019 [US1] Implement the hold record in `src/robot_army/dispatch.py` per R16: hold a `(total, others, global_cap, head_item_id)` signature in process memory, write `dispatch.at_capacity` only when it changes, and write `dispatch.hold_ended` with duration, passes spanned, and what freed it when the hold clears (depends on T017)
- [ ] T020 [US1] Add `operations.capacity(ctx)` to `src/robot_army/operations.py` and a `capacity` verb to `src/robot_army/cli.py` reporting total, cap, the ours/others split, per-repo counts, the `degraded` and `observable` flags, and the ordering mode in force — exiting non-zero when capacity is unobservable (FR-044, FR-045) (depends on T011)
- [ ] T021 [US1] Add `tests/integration/test_dispatch_capacity.py` driving `select_and_dispatch` against a fake registry holding out-of-band entries: nothing dispatches at capacity, one dispatch follows one entry disappearing, two items in one pass never exceed the cap, and — the case that matters most — a dispatch in flight with no registry file yet still occupies its slot (depends on T017)
- [ ] T022 [P] [US1] Add a test to `tests/integration/test_dispatch_capacity.py` asserting that when live sessions already exceed the cap (lowered cap, or several of the author's own), running work is left entirely alone and only new dispatch is withheld (FR-008)
- [ ] T023 [P] [US1] Add a test asserting no code path in `src/robot_army/` signals, terminates, resumes, or attaches to a PID sourced from anything other than the `sessions` table, to `tests/unit/test_capacity.py` — the call-site companion to T013's structural check (FR-006)

**Checkpoint**: the cap is honest about the machine. `robot-army capacity` answers the question, and
holding work is visible in the log without flooding it.

---

## Phase 4: User Story 2 - Two sessions never fight over one repository (Priority: P2)

**Goal**: a repository carries its own cap — one by default — so two sessions never share a clone's
ports, dev server, or submodule fetches.

**Independent Test**: label two issues in one repository with a global cap of two and a per-repo cap of
one; exactly one dispatches, the other is held with the repository named, and it dispatches when the
first finishes — without blocking a third item in a different repository.

- [ ] T024 [US2] Add the optional `max_sessions` key to `RepoConfig` and `_REPO_KEYS` in `src/robot_army/config.py`, falling back to `dispatch.default_repo_max_sessions` where unset, with a non-positive value refused as a problem (depends on T002)
- [ ] T025 [US2] Add `Config.effective_repo_cap(key)` to `src/robot_army/config.py` returning `min(repo max, daemon.max_concurrent_sessions)`, and record whether the value came from an explicit setting or a default so surfaces can distinguish "you chose 1" from "1 is what you get" (US2 AS4) (depends on T024)
- [ ] T026 [US2] Add the cross-field **warning** to `src/robot_army/config.py` when a repository's `max_sessions` exceeds `daemon.max_concurrent_sessions`, mirroring the existing `dispatching_max_age_seconds` cross-check — resolvable, so it warns and takes the minimum rather than refusing (R17) (depends on T025)
- [ ] T027 [US2] Add the `repo_cap` hold reason to `plan()` in `src/robot_army/ordering.py`, comparing `capacity.per_repo` against `effective_repo_cap`, with the detail string naming the repository and both numbers (depends on T015, T025)
- [ ] T028 [US2] Change the dispatch walk in `src/robot_army/dispatch.py` to `continue` past a per-item hold and `break` only on a global one — this single distinction is the whole of FR-012 and FR-020, so a repository at its cap blocks its own work and nothing else (depends on T017, T027)
- [ ] T029 [P] [US2] Extend `tests/unit/test_config.py` with cases for `max_sessions` defaulting, an explicit override, a non-positive value refusing to load, and the over-global warning taking the minimum
- [ ] T030 [P] [US2] Add cases to `tests/integration/test_dispatch_capacity.py`: two items in one repository yield one session; a third item in a different repository dispatches in the same pass; a simulated session occupies a per-repo slot (FR-004); and the hold reason is `repo_cap`, not `global_cap`

**Checkpoint**: repositories no longer collide, and a busy repository cannot stall the queue.

---

## Phase 5: User Story 3 - I can see what is waiting, where it is in line, and why (Priority: P3)

**Goal**: every held item shows its position and the specific reason it is not running, in both
interfaces, and the capacity summary distinguishes the daemon's sessions from the author's.

**Independent Test**: fill the machine to capacity with three items eligible and confirm both surfaces
show a stable ordering with positions, a per-item reason, and a capacity summary — and that the item
listed as next is the one the next dispatch selects.

- [ ] T031 [US3] Implement the full precedence in `plan()` in `src/robot_army/ordering.py` — `paused` above `capacity_unobservable` above `global_cap` above `repo_cap` above `not_onboarded` above `preparation_failed` — with exactly one reason reported per entry, and fold milestone 001's existing onboarding and trust block into `not_onboarded` so the surfaces speak one vocabulary (depends on T027)
- [ ] T032 [US3] Extend `operations.status` in `src/robot_army/operations.py` to render `ordering.plan` — position, hold reason, and detail per row — plus a one-line capacity summary carrying the ours/others split (depends on T020, T031)
- [ ] T033 [US3] Rewrite `queue_view` in `src/robot_army/web/pages.py` to render `ordering.plan` directly instead of deriving its own order, and delete the comment justifying `ORDER BY id` by asserting agreement with the dispatcher — the agreement becomes identity, so the comment stops being a claim that could go stale (depends on T031)
- [ ] T034 [US3] Add the capacity summary to the web chrome in `src/robot_army/web/pages.py` so it appears on every view, showing total, cap, the ours/others split, and the `degraded` flag when set (depends on T032)
- [ ] T035 [P] [US3] Extend `tests/unit/test_ordering.py` with the precedence cases: a paused system reports `paused` and not `global_cap` even when the machine is also full (US3 AS4), and an unobservable capacity outranks both caps
- [ ] T036 [P] [US3] Add a test to `tests/integration/test_dispatch_capacity.py` asserting the item `plan()` puts at position 1 is the item the next `select_and_dispatch` selects, repeated across a hundred iterations with nothing changing in between (SC-006)
- [ ] T037 [P] [US3] Extend `tests/unit/test_pages.py` with cases asserting the queue view renders positions and reasons, and that a held item's reason is distinguishable without consulting the log

**Checkpoint**: a hold is legible. The queue and the dispatcher cannot disagree, because they are one
function.

---

## Phase 6: User Story 4 - Work runs in the order I chose (Priority: P4)

**Goal**: the dispatch order is an explicit, configurable policy rather than an artefact of storage
order.

**Independent Test**: with items eligible in three repositories, run under each ordering mode and
confirm the dispatch order matches the mode's stated rule and that the mode in force is reported.

- [ ] T038 [US4] Add the optional `priority` key to `RepoConfig` and `_REPO_KEYS` in `src/robot_army/config.py`, defaulting to `0`, with a non-integer value refused as a problem (depends on T024)
- [ ] T039 [US4] Implement `order_key(item, repo, mode)` in `src/robot_army/ordering.py`: `(discovered_at, id)` for `oldest-first` and `(-priority, discovered_at, id)` for `repo-priority`, applied in Python because priority lives in TOML rather than in the database (R7) (depends on T038)
- [ ] T040 [US4] Apply `order_key` in `plan()` in `src/robot_army/ordering.py`, sorting the rows `db.list_work_items` returns rather than changing its SQL, so the database keeps one ordering and the policy stays in one place (depends on T039)
- [ ] T041 [US4] Report the ordering mode in force from `operations.capacity`, `operations.status`, and the web chrome in `src/robot_army/web/pages.py` (FR-017) (depends on T032, T040)
- [ ] T042 [P] [US4] Add cases to `tests/unit/test_ordering.py` for both modes: oldest-first ignoring repository, repo-priority draining a higher-priority repository first, equal priorities falling back to oldest-first, an unconfigured repository taking the default priority, and both keys producing a total order
- [ ] T043 [P] [US4] Add a case to `tests/unit/test_ordering.py` asserting that an item at the head which cannot be dispatched does not prevent later items from being considered in the same pass (FR-020), and a case asserting no aging or re-prioritisation occurs across repeated passes (FR-021)
- [ ] T044 [P] [US4] Extend `tests/unit/test_config.py` with `priority` defaulting to zero, an explicit value, and a non-integer refusing to load

**Checkpoint**: the order is stated rather than emergent, and changing it does not disturb work
already running.

---

## Phase 7: User Story 5 - Finished work stops eating the disk (Priority: P5)

**Goal**: when an item's issue closes, its worktree and branch are reclaimed — unless anything in
either exists nowhere else, in which case it is kept and the author is told why.

**Independent Test**: take one item to a closed issue with a clean worktree and pushed commits and
confirm both are gone; take a second with an uncommitted change and confirm both survive with the
reason recorded and visible.

### The unsafe default this story depends on

- [ ] T045 [US5] Change `VersionControl.commits_ahead` to return `int | None` in `src/robot_army/boundaries/__init__.py`, `boundaries/git.py`, and the simulated implementation, returning `None` where it currently returns `0` on a failed `rev-list` (R11) — the same value means "no information" to the resume-signal caller and "safe to delete" to cleanup, and that ambiguity is what makes the change necessary rather than cosmetic
- [ ] T046 [US5] Update `worktree.condition` in `src/robot_army/worktree.py` to map `None` to `0`, preserving today's resume-signal behaviour exactly (depends on T045)
- [ ] T047 [P] [US5] Add a case to `tests/unit/test_worktree.py` asserting a failing `rev-list` yields `None` from the boundary and `0` from `condition`, so the two readings stay apart

### Schema and configuration

- [ ] T048 [US5] Append `_migration_004` to `src/robot_army/migrations.py` adding the nullable `cleanup_state`, `cleanup_reason`, and `cleaned_at` columns to `work_items`, leaving earlier migrations untouched and letting `SCHEMA_VERSION` derive from the tuple length
- [ ] T049 [US5] Add the three columns to the `WorkItem` model in `src/robot_army/models.py` and cleanup accessors to `src/robot_army/db.py`, honouring the existing `include_simulated` scoping (depends on T048)
- [ ] T050 [US5] Add `CleanupConfig` (`on_issue_close`, default `false`) and the `"cleanup"` section to `_KNOWN_KEYS` and `parse()` in `src/robot_army/config.py`, defaulting to off because the Operating Constraints require irreversible actions to be unreachable by default (FR-022)
- [ ] T051 [P] [US5] Add a case to `tests/unit/test_migrations.py` asserting migration 004 runs on a 003-era database, that a killed migration leaves `user_version` unadvanced and re-runs cleanly, and that pre-existing rows read back with `cleanup_state` as `NULL`

### The two guards

- [ ] T052 [US5] Implement `eligible(item, *, config, capacity)` in `src/robot_army/cleanup.py` per [contracts/cleanup.md](contracts/cleanup.md): `done`, a `worktree_path`, `cleanup_state` of `NULL` or `skipped`, and no live session — recording `skipped` with its reason when a session is live (FR-027) (depends on T049, T050)
- [ ] T053 [US5] Implement the worktree guard in `src/robot_army/cleanup.py`, calling `vcs.remove_worktree(..., force=False)` and never passing `force`, treating git's refusal as the expected and useful outcome it is — writing `cleanup_state = retained` with git's own message and **not** attempting the branch half, because a dirty worktree means the branch may hold the only copy of something (FR-025) (depends on T052)
- [ ] T054 [US5] Implement the branch guard in `src/robot_army/cleanup.py`: fetch the base ref, then accept the branch only if `commits_ahead(clone, "<remote>/<base>", branch) == 0` or `commits_ahead(clone, "<remote>/<branch>", branch) == 0`, treating `None` as unproven. On success call `delete_branch(force=True)` — with a comment stating plainly that `force` here means a **stronger** guard than git's has already passed, not that a guard was skipped (R12) (depends on T045, T053)
- [ ] T055 [US5] Write the four outcomes — `done`, `branch_retained`, `retained`, `skipped` — to `work_items` in `src/robot_army/cleanup.py` with their reasons, and emit `cleanup.considered` and `cleanup.retained` audit records so "why is this 499 MB still here?" is answerable from the log alone (depends on T054)

### Wiring and surfaces

- [ ] T056 [US5] Add the `_cleanup_worktrees` pass to `src/robot_army/reconcile.py`, running immediately after `_resolve_closed_issues` in the same pass and only when `[cleanup] on_issue_close` is true — no new daemon job, because the existing pass already asks the exact question cleanup needs (R10) (depends on T055)
- [ ] T057 [US5] Add `operations.cleanup_now(ctx, item_id=None)` to `src/robot_army/operations.py` and a `cleanup` verb to `src/robot_army/cli.py`, running the same function under the same guards whether or not the automatic path is enabled (FR-029) (depends on T055)
- [ ] T058 [US5] Surface cleanup state in `operations.show`, `operations.worktree_list`, and the item view in `src/robot_army/web/pages.py`, so a retained worktree or branch is visible with its reason without reading the log (depends on T055)

### Tests

- [ ] T059 [P] [US5] Add `tests/unit/test_cleanup.py` covering eligibility: a `done` item with a worktree is eligible; a live session yields `skipped`; `retained` and `branch_retained` are not revisited automatically while `skipped` is
- [ ] T060 [P] [US5] Add cases to `tests/unit/test_cleanup.py` for the branch decision table — contained in the remote base, pushed and up to date, neither, and `commits_ahead` returning `None` — asserting deletion happens in the first two and the branch is retained in the last two
- [ ] T061 [US5] Add `tests/integration/test_cleanup.py` driving the pass against a repository fixture with four items: a dirty worktree, an unpushed branch, a live session, and an externally deleted directory — asserting zero removals that should have been kept, and that the unpushed commits still exist afterwards (SC-009) (depends on T056)
- [ ] T062 [P] [US5] Add interruption cases to `tests/integration/test_cleanup.py` per [data-model.md](data-model.md)'s table: killed between the two removals, killed after both but before the row is written, and killed during the containment fetch — each resolving on the next pass with the failure direction always "keep"
- [ ] T063 [P] [US5] Add a case to `tests/integration/test_cleanup.py` asserting that below the `live` effect level the removals are simulated with full arguments and nothing leaves the disk, and that at `local` and above they are real — cleanup follows worktree *creation*'s effect rule, not the board's (FR-039)

**Checkpoint**: disk is reclaimed for finished work, and nothing that exists only in a worktree or only
on a branch is ever destroyed.

---

## Phase 8: User Story 6 - The system tells me when something happens (Priority: P6)

**Goal**: a message arrives on the existing health channel when a run starts, finishes, fails, or a
card is waiting on the author.

**Independent Test**: enable notifications for failures only, force one failure and one success, and
confirm exactly one message is sent carrying enough to identify the item.

- [ ] T064 [US6] Extract the bounded-timeout JSON POST from `health.notify` in `src/robot_army/health.py` into a shared helper both the health signal and the notifier call, leaving `health.notify`'s behaviour unchanged so no existing test moves
- [ ] T065 [US6] Add the `NotificationEvent` dataclass and the `Notifier` protocol to `src/robot_army/boundaries/__init__.py` per [contracts/notifications.md](contracts/notifications.md), exporting both from `__all__`, with the event carrying identifiers and state names only so there is no field a secret could reach
- [ ] T066 [US6] Implement the real and simulated `Notifier` in `src/robot_army/boundaries/notifier.py`, the simulated one logging the call with its full arguments and returning a structurally valid result exactly as the other simulated writers do (depends on T064, T065)
- [ ] T067 [US6] Add `REAL_AT["notifier"] = frozenset({EffectLevel.LIVE})` and the wiring to `src/robot_army/effects.py`, and add the row to that module's docstring table so the table stays the single readable statement of the effect rules (FR-040) (depends on T066)
- [ ] T068 [US6] Add `NotificationsConfig` (`events`, default empty; `max_per_cycle`, default 5) and the `"notifications"` section to `_KNOWN_KEYS` and `parse()` in `src/robot_army/config.py`, with an unknown event kind refused as a problem and a non-empty `events` with no `health.webhook_url` raised as a warning (R17)
- [ ] T069 [US6] Implement `emit(...)` and the per-cycle bound in `src/robot_army/notifications.py`: at most `max_per_cycle` sends per tick, then one summary naming how many were suppressed and of which kinds, with `notify.send` and `notify.suppressed` audit records for both paths — the counter held in process memory, because it bounds one burst and a restart re-permitting a handful of messages is not worth a table (R15) (depends on T067, T068)
- [ ] T070 [US6] Add the four call sites — session confirmed in `src/robot_army/dispatch.py`, `awaiting_review`/`done` and `failed` in `src/robot_army/reconcile.py`, and `needs_info` in `src/robot_army/intake.py` — each **outside** the surrounding transaction, because an HTTP POST inside `BEGIN IMMEDIATE` would hold a write transaction open on a slow webhook (R14) (depends on T069)
- [ ] T071 [P] [US6] Add `tests/unit/test_notifications.py` covering: an unconfigured install making no outbound request; only configured kinds sending; a channel failure being recorded without failing, delaying, or retrying the triggering operation; and a send never occurring inside an open transaction
- [ ] T072 [P] [US6] Add cases to `tests/unit/test_notifications.py` for the per-cycle bound — a backlog of more than `max_per_cycle` events producing exactly that many messages plus one summary, with nothing dropped silently — and a case asserting no credential appears in any event, log record, or composed message across a run including an authentication failure (FR-037, SC-010)
- [ ] T073 [P] [US6] Extend `tests/unit/test_config.py` with the `[notifications]` defaults, an unknown event kind refusing to load, and the missing-webhook warning

**Checkpoint**: the author is told what they asked to be told, and nothing else changed.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T074 [P] Document the new configuration in `README.md` — the `[dispatch]`, `[cleanup]`, and `[notifications]` sections and the two new `[repos.*]` keys — and rewrite its "Cleaning up" section, which currently describes cleanup as entirely manual
- [ ] T075 [P] Add the new audit actions to `docs/logging.md` — `dispatch.hold_ended`, `capacity.unobservable`, `cleanup.considered`, `cleanup.retained`, `notify.send`, `notify.suppressed` — and document the `dispatch.at_capacity` summarisation rule (R16) alongside them, since the constitution requires a retention rule to be documented rather than improvised
- [ ] T076 [P] Add the three `work_items` cleanup columns and their values to `docs/state.md`, including that `worktree_path` and `branch` are deliberately retained after a removal
- [ ] T077 Update `docs/roadmap.md` to mark milestone 004 implemented, and record what running it actually taught — in particular whatever the global cap's value settled at, which §16 leaves open and only use answers
- [ ] T078 Run `ruff check` and `ruff format --check` across `src/` and `tests/`, and the full `pytest` suite; the constitution's gate is that the suite passes, not a coverage number
- [ ] T079 Walk [quickstart.md](quickstart.md) end to end on the real machine, including the scenarios CI cannot run — 2, 3, 4, and 5 need a live registry and real Claude processes; 9 and 10 need a real remote and a real worktree. These are the ones that would catch an under-counted cap or a wrongly deleted branch

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on US1 — the per-repo cap is a second condition on the walk US1 builds
- **US3 (Phase 5)**: depends on US2 — it surfaces the reasons US1 and US2 produce
- **US4 (Phase 6)**: depends on Foundational only; independent of US2 and US3, though its mode is
  nicer to read once US3's surfaces exist
- **US5 (Phase 7)**: depends on Foundational only — cleanup shares no code with the capacity work
- **US6 (Phase 8)**: depends on Foundational only
- **Polish (Phase 9)**: depends on every story that is being shipped

### Honest note on story independence

Milestones 001–003 produced mostly independent stories. This one does not, and pretending otherwise
would mislead. US1 → US2 → US3 is a genuine chain: each adds a condition or a surface to the same
dispatch walk, and dropping US1 leaves US2 with nothing to be a second condition *on*. US4, US5, and
US6 are genuinely independent of that chain and of each other — any of them can be built, shipped, or
dropped without touching the others.

### Parallel opportunities

- Setup: T005 alongside T002–T004
- Foundational: T007 and T012/T013 alongside the capacity work; T016 alongside T014/T015
- US1: T022 and T023 alongside T021
- US2: T029 and T030 in parallel once T027 lands
- US3: T035, T036, T037 in parallel once T031 lands
- US4: T042, T043, T044 in parallel once T039 lands
- US5: T047 alongside T045/T046; T059, T060, T062, T063 in parallel once the guards land
- US6: T071, T072, T073 in parallel once T069 lands
- Polish: T074, T075, T076 in parallel
- **Across stories**: once Foundational is done, US4, US5, and US6 can each proceed alongside the
  US1 → US2 → US3 chain

---

## Implementation Strategy

### MVP (User Story 1 only)

1. Phase 1 Setup
2. Phase 2 Foundational — the checkpoint here is worth stopping at: capacity and order are computed
   correctly and nothing consumes them, so dispatch behaviour is still exactly milestone 003's
3. Phase 3 US1
4. **Stop and validate** with quickstart scenarios 2, 3, and 4 on the real machine
5. This alone fixes the milestone's one actively bad behaviour — a cap that protects nothing on the
   machine where the author actually works

### Incremental delivery

Setup + Foundational → US1 (the cap is honest) → US2 (repositories stop colliding) → US3 (the hold is
legible) → US4 (the order is chosen) → US5 (disk is reclaimed) → US6 (the author is told).

Every step is shippable, and the last three can be reordered or dropped freely.

### If time runs short

Drop from the bottom. US6 is explicitly a stretch in the planning document and nothing depends on it.
US5 is the one with real deletion risk and no user waiting on it — leaving worktrees to accumulate is
annoying and reversible, which is the profile of a thing worth deferring. US4 is the one the planning
document itself says is not worth engineering up front.

Do **not** drop US3 while shipping US1 and US2. Holding work without saying why turns a visible queue
into an invisible stall, which is the silent no-op the planning document warns about, and it would be
worse than not holding at all.

---

## Notes

- `[P]` marks work that does not collide with another pending task's files, not work that needs a
  second person — there is one maintainer
- Every task names its file path; commit after each task or logical group
- Test-first is not mandatory and coverage targets are not adopted; the gate is that the tests exist,
  are meaningful, and pass
- Two tests are worth more than the rest and both need the real machine: T021 (the cap is never
  under-counted across the launch window) and T060/T061 (no branch with unpushed commits is ever
  deleted). Neither runs in CI
