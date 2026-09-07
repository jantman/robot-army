---

description: "Task list for: the session cap every surface shows is the one being enforced"
---

# Tasks: The session cap every surface shows is the one being enforced

**Input**: Design documents from `specs/20260906-185324-enforced-session-cap/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/enforced-cap.md](contracts/enforced-cap.md)

**Tests**: required. The constitution's Development Workflow says every new or changed unit
of behaviour ships with unit tests, and that code parsing external input carries failure-path
tests of its own — which is what reading a cap out of a file another process wrote is.

**Organization**: grouped by the user stories in spec.md. US1 and US2 are both P1 and share a
foundation; they are separate stories because they are separate claims — "the number is true"
and "the disagreement that produced it is not hidden" — and each is verifiable on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 from spec.md

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`.

---

## Phase 1: Setup

**Purpose**: nothing to install and nothing to scaffold — no new dependency, no new module,
no configuration key, no migration. This phase records that, and establishes the baseline the
"nothing else changed" claims are measured against.

- [X] T001 Run `uv run pytest` from the repository root and record that the suite is green before any change, so a later failure is attributable to this feature
- [X] T002 Read every call site of `capacity.snapshot` (`src/robot_army/dispatch.py` ×2, `src/robot_army/operations.py` ×2, `src/robot_army/web/server.py`, `src/robot_army/web/pages.py` ×2) and confirm against [research.md](research.md) R8 which are daemon paths that must keep passing no `enforced_cap` and which are read surfaces that must resolve one

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the daemon publishes what it enforces, and one function turns that into a cap.
Every user story below depends on this phase; none of it is visible on any surface yet.

⚠️ **Complete before starting Phase 3.**

- [X] T003 Add `max_concurrent_sessions: int | None = None` to `Heartbeat` in `src/robot_army/health.py`, as a first-class field beside `dispatch_paused` and `board`, with a docstring comment saying why it is first-class (the state guide documents this file's shape) and why it defaults to `None` (an older heartbeat still parses)
- [X] T004 Add the matching `max_concurrent_sessions: int | None = None` keyword to `write_heartbeat` in `src/robot_army/health.py`, passing it through to the dataclass
- [X] T005 Pass `max_concurrent_sessions=self.config.daemon.max_concurrent_sessions` from `Daemon._heartbeat` in `src/robot_army/daemon.py`, with a comment naming the invariant that makes the value trustworthy: the cap is fixed when the process starts and cannot change while it runs
- [X] T006 Add `published_cap(report, *, running) -> int | None` to `src/robot_army/health.py` implementing §1 of [contracts/enforced-cap.md](contracts/enforced-cap.md): `None` unless a daemon holds the lock and the heartbeat carries an `int` (rejecting `bool`) of at least 1. Its docstring must say why `running` is a parameter rather than a probe (`daemon` imports `health`; the reverse is a cycle) and why a stale heartbeat still counts (research.md R5)
- [X] T007 [P] Add tests to `tests/unit/test_health.py` walking every row of the decision table: no daemon; daemon with an unreadable heartbeat; daemon with a fresh heartbeat carrying a cap; the same stale; a heartbeat with no cap field; and each unusable value (`0`, `-1`, `"7"`, `True`, `None`, a float) rejected as *not published* rather than believed
- [X] T008 Add `enforced_cap: int | None = None` to `capacity.snapshot` in `src/robot_army/capacity.py`, resolving `global_cap` and the new `configured_cap` per [data-model.md](data-model.md), and apply the same resolution on the `_unobservable` path so an uncountable machine still reports the right limit
- [X] T009 Add the `configured_cap: int | None = None` field to `CapacitySnapshot` in `src/robot_army/capacity.py`, documented as *present only when it differs from the cap in force*, with the reason that presence is the disagreement so that no consumer compares two numbers and no two consumers disagree about the answer
- [X] T010 Add the `cap_disagreement` property to `CapacitySnapshot` in `src/robot_army/capacity.py`, returning the exact sentence in §5 of [contracts/enforced-cap.md](contracts/enforced-cap.md) or `None`, and extend `describe()` to carry it as a trailing clause. Comment why the sentence does not say which process is stale: both directions are reachable, the remedies are opposite, and it cannot tell
- [X] T011 Update `capacity.py`'s module docstring so its statement of what the module observes includes the cap in force, not only the count
- [X] T012 [P] Add tests to `tests/unit/test_capacity.py`: `enforced_cap` supplied and differing sets `global_cap` and `configured_cap`; supplied and equal leaves `configured_cap` `None`; omitted falls back to the configured cap with no disagreement; `at_capacity` follows the cap in force in both directions; `describe()` and `cap_disagreement` carry both numbers; and the unobservable path resolves the cap the same way

**Checkpoint**: the daemon publishes its cap and a snapshot can carry it — no surface reads it yet.

---

## Phase 3: User Story 1 - The fraction on the page is true (Priority: P1) 🎯 MVP

**Goal**: the web renders every capacity fraction against the cap the running daemon is
enforcing, in both directions of staleness.

**Independent Test**: run a daemon at one cap, render the chrome from a context configured for
another, and confirm the denominator and the "at capacity" styling are the daemon's.

- [X] T013 [US1] In `handle` in `src/robot_army/web/server.py`, take the health report and the lock probe **once** and pass them to `effective_level`, which already accepts both — replacing the reads it performs internally today (research.md R9)
- [X] T014 [US1] In the same place, resolve `health.published_cap(report, running=running)` and pass it as `enforced_cap` to the per-request `capacity_mod.snapshot`, with a comment saying why the resolution happens here: one reading of the daemon per page, handed to every consumer of it
- [X] T015 [US1] Add optional `report` and `running` parameters to `pages.chrome` in `src/robot_army/web/pages.py` so it uses the handler's reading instead of retaking it, defaulting to taking its own for a direct caller — matching what `effective_level` and `effect_mismatch` already do
- [X] T016 [US1] Confirm and, if needed, adjust that the two `capacity is None` fallbacks in `src/robot_army/web/pages.py` (`chrome` and the queue view) still resolve to the configured cap, and document that as the meaning of "no enforced cap supplied" rather than an oversight
- [X] T017 [P] [US1] Add tests to `tests/unit/test_web_views.py`: with a heartbeat naming a cap that differs from the app's configuration, the chrome payload's `global_cap` is the daemon's, the rendered pill shows that denominator, and the pill is not styled "at capacity" when the enforced cap leaves room
- [X] T018 [P] [US1] Add a test that the queue view's per-item reasons are planned against the enforced cap — with a daemon cap that leaves a free slot and a configured cap that does not, no item is held for capacity and the pill and the reasons agree

- [X] T036 [US1] **Added during implementation.** Clamp a repository's effective cap by the cap the *snapshot* is reporting against rather than by this process's configured one — `effective_repo_cap(key, ceiling=...)` in `src/robot_army/config.py`, passed `capacity.global_cap` from `ordering.repo_capacity` and `operations._repo_settings`. Found while writing T018: a per-repository limit is `min(repo, global)`, so a stale global cap could hold a row for `repo_cap` underneath a pill reading `1/3` — the same defect one level down. Behaviour is identical wherever no enforced cap is supplied, which is every dispatch path

**Checkpoint**: the issue's reproduction now reads `6/7`. Nothing yet says why it differs from the file.

---

## Phase 4: User Story 2 - A disagreement is announced, not silently absorbed (Priority: P1)

**Goal**: every web view that reports capacity states the disagreement that produced its
number, and no action is refused on account of one.

**Independent Test**: render with the two caps differing and confirm the sentence appears
naming both, and that a control still acts.

- [X] T019 [US2] Render `capacity["cap_disagreement"]` as a `banner warn` notice in `_chrome_bar` in `src/robot_army/web/html.py`, placed after the effect-level banner, with a comment placing it in the same family — conditions under which what you are reading does not mean what it appears to mean — and saying why this one warns rather than errors
- [X] T020 [US2] Add a `.banner.warn { border-color: var(--warn); }` rule beside `.banner.ok` in the stylesheet in `src/robot_army/web/html.py`
- [X] T021 [P] [US2] Add tests to `tests/unit/test_web_views.py`: the notice renders on every view when the caps differ, names both numbers, and is absent when they agree
- [X] T022 [P] [US2] Add tests for the three silent states of §4 of [contracts/enforced-cap.md](contracts/enforced-cap.md): no daemon running; a daemon holding the lock whose heartbeat cannot be read (which must render the existing unknown-level banner and **no** cap notice); and a heartbeat carrying no cap
- [X] T023 [P] [US2] Add a test to `tests/unit/test_web_effect_guard.py` that a cap disagreement refuses nothing — a POST that would be refused on an effect-level mismatch succeeds with only the caps differing

**Checkpoint**: the web half of the feature is complete and testable on its own.

---

## Phase 5: User Story 3 - The terminal and the web agree (Priority: P2)

**Goal**: `robot-army status` and `robot-army capacity` report against the same enforced cap,
so two surfaces read a second apart cannot print different denominators.

**Independent Test**: with a daemon whose cap differs from the file, take a terminal reading
and a web reading and compare the denominators.

- [X] T024 [US3] In `status` in `src/robot_army/operations.py`, add the lock probe beside the health report it already takes and pass the resolved `enforced_cap` into the snapshot it builds when no snapshot was handed in
- [X] T025 [US3] In `capacity` in `src/robot_army/operations.py`, take the health report and the lock probe, pass the resolved `enforced_cap` into its snapshot, and add the `cap          : ` line carrying `snap.cap_disagreement` when there is one
- [X] T026 [US3] Add `configured_cap` and `cap_disagreement` to the `capacity --json` document in `src/robot_army/operations.py`, keeping `global_cap` as the cap in force so an existing consumer is correct without changing
- [X] T027 [US3] Add the same two keys to `_capacity_dict` in `src/robot_army/operations.py`, which both `status --json` and the web chrome render from
- [X] T028 [P] [US3] Add tests to `tests/unit/test_capacity_cli.py` (or the existing home of the `capacity` command's tests): both directions of disagreement report the daemon's cap, the `cap` line appears only when they differ, and the three JSON keys carry the contracted values
- [X] T029 [P] [US3] Add a test that `status`'s capacity line carries the disagreement clause, and that its `--json` payload carries the same three keys as the web chrome's

**Checkpoint**: all three stories complete; every surface reports the same denominator.

---

## Phase 6: Polish & cross-cutting

- [X] T030 [P] Add an integration test to `tests/integration/test_dispatch_capacity.py` proving the daemon's own dispatch decisions are unchanged: it plans against its own configuration, reads no heartbeat to learn its cap, and dispatches exactly as it did before with a heartbeat present, absent, or naming a different cap
- [X] T031 [P] Document the heartbeat's new key in `docs/guide/state.md`, saying what it is for and that reading it answers "what cap is in force?" without access to the configuration file
- [X] T032 [P] Document in `docs/guide/3-selection.md` which cap the surfaces report and why the running daemon is the authority, including that raising the cap takes effect when the daemon restarts and not before
- [X] T033 [P] Document the new notice in `docs/guide/operating.md`: what it means, that it never refuses anything, and that the fix is to restart whichever of the two processes has been running since before the configuration changed
- [X] T034 Run `uv run pytest` and confirm the whole suite passes, including `tests/unit/test_example_config_drift.py` — no configuration key changed, so `share/config.example.toml` must be untouched
- [X] T035 Walk [quickstart.md](quickstart.md) §1 and §2 against a running daemon and web service, confirming the issue's `6/5` now reads `6/7` and that the reverse direction reports the daemon's cap — done against an **isolated** config, state directory and port rather than the live install, with a stand-in holding the real single-instance lock and writing real heartbeats. Every section walked: both directions, both surfaces agreeing as payloads, all three silent states, and the cap read straight out of `heartbeat.json`. One correction came out of it — `robot-army capacity` has no `--json` flag and never had one, so the contract and quickstart now name `status --json` as where that payload is read

---

## Phase 7: Review findings (added after implementation)

Raised by a `/code-review high` pass over the finished branch, run locally because the CI
review job went green having posted nothing — five attempts, each ending its turn waiting on
a backgrounded agent.

- [X] T037 **The launch gate was still on the stale cap.** `dispatch.check_launch_gate` runs in whichever process is about to launch, not in the daemon, so the web's *Resume* was gated against the web's own configuration: a header correctly reading `6/7` offered a button whose refusal said `6 of 5 sessions running`, and a web process with a *higher* cap would launch past the daemon's. Threaded `enforced_cap` through `check_launch_gate`, `dispatch_item` and `_dispatch_item` in `src/robot_army/dispatch.py`; resolved it in `web.require_dispatchable` and in `operations.resume`/`restart` via a new `operations._enforced_cap(ctx)`; the daemon passes nothing. Two tests in `tests/unit/test_launch_gate.py` and two in `tests/unit/test_web_effect_guard.py`, each verified to fail without the fix
- [X] T038 **`describe()` dropped the disagreement on the unobservable path**, which made `robot-army status` — whose only channel this is — silent about a stale cap in exactly the state where a wrong limit is least diagnosable. Fixed in `src/robot_army/capacity.py` with a test in `tests/unit/test_capacity.py`
- [X] T039 **The remedy was unactionable from a terminal.** The shared sentence will not say which process is behind, because on the web either can be — but a command read the file milliseconds ago, so the daemon necessarily is. `operations.FRESH_READER_REMEDY` adds that one line to `capacity` and `status` without the shared sentence growing a second wording
- [X] T040 Corrected the spec's false assumption ("the daemon is the sole enforcer"), FR-005, FR-007 and a new FR-011; added [research.md](research.md) R8a and the plan's post-implementation amendment; added §6 to [contracts/enforced-cap.md](contracts/enforced-cap.md); and fixed the over-broad "it never refuses anything" claim in `docs/guide/3-selection.md`, `docs/guide/operating.md`, `src/robot_army/web/html.py` and a test docstring

- [X] T041 **A dead daemon's heartbeat was trusted during a restart** (raised by the CI review on PR #158). `is_locked` proves only that *some* process holds the lock; `run_daemon` acquires it and then wires boundaries, checks preconditions and runs `startup` — seconds of network work — before its first beat, and nothing unlinks the old heartbeat. So lowering the cap 7→2 and restarting left every surface reporting 7 and the launch gate admitting sessions up to 7 against a daemon about to enforce 2. `health.published_cap` now takes `lock_holder` and returns `None` unless the heartbeat's own pid matches it. Four tests in `tests/unit/test_health.py`; the decision tables in [contracts/enforced-cap.md](contracts/enforced-cap.md) §1 and [data-model.md](data-model.md) gain the row

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: blocks every story. Nothing can read a cap that is not published, and nothing can carry one the snapshot has no field for.
- **US1 (Phase 3)**: needs Phase 2. Delivers the corrected number.
- **US2 (Phase 4)**: needs Phase 2 for the sentence and Phase 3 for the payload that carries it to the renderer.
- **US3 (Phase 5)**: needs Phase 2 only. Independent of US1 and US2 — a different set of call sites — and could be done first if the terminal mattered more.
- **Polish (Phase 6)**: after the stories it documents.

### Within the phases

- T003 → T004 → T005 (the field must exist before it is written, and be written before it is read).
- T006 is independent of T008–T010 and can be written alongside them.
- T008 → T009 → T010 (`cap_disagreement` reads `configured_cap`, which the resolution sets).
- T013 → T014 (the resolution needs the reading), T014 → T017/T018.
- T019 → T020 → T021/T022.

### Parallel opportunities

- T007 and T012 — different test files, different modules.
- T017, T018, T021, T022, T023 — tests, once their implementation task is in.
- T028 and T029 — different commands.
- T030–T033 — one integration test and three unrelated guide pages.

---

## Implementation Strategy

### MVP

Phases 1–3. The header stops lying, which is the issue. It is not shippable *alone* in good
conscience — a corrected number with no explanation is a page whose fraction disagrees with the
operator's editor for no visible reason — which is why US2 is also P1 and Phase 4 follows
immediately.

### Increments

1. Phase 2 → the daemon publishes its cap; `jq` on the heartbeat proves it.
2. Phase 3 → the web number is right.
3. Phase 4 → the web says why.
4. Phase 5 → the terminal matches, in both directions.
5. Phase 6 → documented, and proved by hand against a live daemon.

---

## Notes

- No configuration key changes, so `src/robot_army/exampleconfig.py` and `share/config.example.toml` are untouched by every task above.
- No database table, column or migration is involved.
- `src/robot_army/dispatch.py` is deliberately **not** in any task: the daemon plans against its own configuration, and T030 exists to prove it still does.
- Commit after each phase or logical group; each commit message says why, per the constitution.
