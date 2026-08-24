---

description: "Task list for feature implementation"
---

# Tasks: Minimum Daemon

**Input**: Design documents from `/specs/001-minimum-daemon/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: **Required, not optional.** The constitution's Development Workflow section mandates unit
tests for every new or changed unit of behaviour, and additionally mandates failure- and
interruption-path tests for persistence/recovery logic, state machines, and code parsing external
input. SC-015 restates this. Test tasks are therefore first-class here.

**Test ordering — a deliberate deviation from the template.** The template says to write tests first
and watch them fail. The constitution explicitly says otherwise: *"test-first development is not
mandatory. The requirement is that the tests exist and are meaningful, not the order they were
written in."* Test tasks are therefore grouped at the end of each story rather than the start. Write
them in whichever order suits the work; the checkpoint is what enforces they exist.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task serves (US1–US6)
- Paths are repository-relative; the project is a single Python package under `src/`

---

## Phase 1: Setup

**Purpose**: Project initialization.

- [X] T001 Initialize the project in `pyproject.toml`: Python 3.14 requirement, `httpx` as the sole runtime dependency, `pytest` and `ruff` as dev dependencies, `[project.scripts] robot-army = "robot_army.cli:main"`, plus `[tool.pytest.ini_options]` and `[tool.ruff]` sections
- [X] T002 [P] Create the package skeleton: `src/robot_army/__init__.py`, `src/robot_army/__main__.py`, `src/robot_army/boundaries/__init__.py`
- [X] T003 [P] Create the test tree: `tests/conftest.py`, `tests/unit/`, `tests/integration/`, and fixture directories `tests/fixtures/{claude_sessions,claude_json,proc,exit_records}/`
- [X] T004 [P] Create `.gitignore` covering `.venv/`, `__pycache__/`, `*.db`, `.pytest_cache/`, `.ruff_cache/`
- [X] T005 Run `uv sync` and confirm both `uv run robot-army --help` and `uv run pytest` execute without error

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Infrastructure every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Note on scope**: this phase creates the boundary *protocols* and the effect-level wiring
*mechanism*, but only the real implementations are selected. The simulated implementations are
US4's work. See the build-order note in Implementation Strategy — you may want to pull US4 forward.

### Data and state

- [X] T006 [P] Define dataclasses for `WorkItem`, `Session`, `Repo`, `Anomaly`, and `PollState` in `src/robot_army/models.py` per data-model.md
- [X] T007 [P] Define `WorkItemState` and `SessionState` enums plus the legal-transition tables and a single `transition()` gate that rejects everything else, in `src/robot_army/states.py`
- [X] T008 Implement the connection factory in `src/robot_army/db.py`: `journal_mode=WAL`, `foreign_keys=ON`, `synchronous=FULL`, a transaction context manager, and a `row_factory` mapping rows to the T006 dataclasses
- [X] T009 Implement the `PRAGMA user_version` migration ladder in `src/robot_army/migrations.py`, with migration 001 creating all five tables and every index in data-model.md, including the partial unique index on unacknowledged anomalies
- [X] T010 Implement query accessors in `src/robot_army/db.py` whose signatures carry `include_simulated: bool = False`, so excluding `dry_run` rows is the structural default and including them is the explicit act (FR-056)

### Cross-cutting infrastructure

- [X] T011 [P] Implement the JSONL audit writer in `src/robot_army/audit.py`: daily file at `~/.local/state/robot-army/logs/audit-YYYY-MM-DD.jsonl`, append mode, flush per record, UTC timestamps
- [X] T012 Implement the redaction choke point in `src/robot_army/audit.py` so every record passes through it, keyed on field name (token, environment dumps)
- [X] T013 Implement `intent`/`outcome` record pairing with a shared `action_id` in `src/robot_army/audit.py`, so outward-facing actions are logged before execution (FR-060)
- [X] T014 Implement state-transition recording in `src/robot_army/states.py` that writes the audit record inside the same transaction as the state change
- [X] T015 [P] Implement TOML config loading in `src/robot_army/config.py` per contracts/config.md
- [X] T016 Implement aggregate config validation in `src/robot_army/config.py` that reports every problem at once, rejects a literal token as an error, and enforces mode 0600 on `token_file`
- [X] T017 [P] Implement `/proc` readers in `src/robot_army/procinfo.py` for `exe`, `cwd`, `stat` field 22 (`starttime`), and `cgroup`, treating `ProcessLookupError` and `FileNotFoundError` as "gone" rather than errors
- [X] T018 [P] Define the five boundary protocols in `src/robot_army/boundaries/__init__.py` per contracts/boundaries.md: `IssueSourceReader`, `IssueSourceWriter`, `VersionControl`, `HookRunner`, `SessionHost`, `Display`
- [X] T019 Implement the `EffectLevel` enum and the boundary wiring table in `src/robot_army/effects.py`, wired once at startup so no downstream code can access the level
- [X] T020 Implement the `fcntl.flock` single-instance lock in `src/robot_army/daemon.py`, writing the holder PID into `daemon.lock` and releasing on any exit (FR-070)
- [X] T021 Create the argparse skeleton in `src/robot_army/cli.py` and the callable-per-verb structure in `src/robot_army/operations.py`, with exit codes 0–4 per contracts/cli.md

### Foundational tests

- [X] T022 [P] Unit-test every legal and every illegal transition for both state machines in `tests/unit/test_states.py`
- [X] T023 [P] Unit-test migrations in `tests/unit/test_migrations.py`: fresh create, idempotent re-run, and a migration interrupted mid-transaction leaving `user_version` unadvanced
- [X] T024 [P] Unit-test config validation in `tests/unit/test_config.py`: aggregate multi-error reporting, literal-token rejection, `token_file` permission check, unknown key inside `[repos.*]` as an error
- [X] T025 [P] Unit-test audit redaction in `tests/unit/test_audit.py`, asserting a token value never reaches the file
- [X] T026 [P] Unit-test `procinfo` against `tests/fixtures/proc/` in `tests/unit/test_procinfo.py`, including a process that vanishes mid-read
- [X] T027 [P] Unit-test the `include_simulated` default scope in `tests/unit/test_db_scope.py`, asserting simulated rows are absent unless explicitly requested

**Checkpoint**: Schema, state machine, config, audit log, and CLI skeleton exist. User story work can begin.

---

## Phase 3: User Story 1 - Label an issue, get a working session (Priority: P1) 🎯 MVP

**Goal**: A labelled GitHub issue becomes a live Claude Code session in an isolated worktree, in the running kitty instance, confirmed to actually exist.

**Independent Test**: Label a real issue in a real repository; confirm a live session appears in the running kitty instance, in an isolated checkout on a new branch, with the issue content in its context, without touching a terminal.

### Onboarding and eligibility

- [X] T028 [P] [US1] Implement the GitHub reader in `src/robot_army/boundaries/github.py`: `poll` with `If-None-Match`, `get_issue`, `is_closed`, `list_owned_repos`, all with explicit connect/read timeouts
- [X] T029 [US1] Implement bounded exponential backoff with jitter in `src/robot_army/boundaries/github.py`, honouring `Retry-After` and `X-RateLimit-Reset`, and raising on transport failure rather than returning an empty result
- [X] T030 [US1] Persist and reuse ETags via the `poll_state` table in `src/robot_army/poll.py`, treating `304` as the healthy steady state
- [X] T031 [US1] Implement eligibility evaluation in `src/robot_army/poll.py`: author match, label present, repo onboarded, not already dispatched — recording which condition failed in `blocked_reason` (FR-007, FR-009)
- [X] T032 [US1] Implement work-item creation in `src/robot_army/poll.py`, writing the `discovered` row before evaluation and relying on `UNIQUE (source, source_id, dry_run)` for idempotency
- [X] T033 [P] [US1] Implement the trust check in `src/robot_army/dispatch.py` reading `~/.claude.json` → `projects[<primary clone path>].hasTrustDialogAccepted`, failing closed on a missing file or key
- [X] T034 [US1] Implement the committed-settings fingerprint in `src/robot_army/dispatch.py`: SHA-256 over `.claude/settings.json` and `.claude/settings.local.json` read via `git show <base-ref>:<path>`, compared against the approved value, blocking dispatch on any difference (FR-004)
- [X] T035 [US1] Implement the `onboard` operation in `src/robot_army/operations.py`: print the primary clone path, trust status, and the full contents of any committed settings at the base ref; require explicit confirmation; record approval and fingerprint. Support `--reapprove` with a diff

### Worktree preparation

- [X] T036 [P] [US1] Implement `GitVersionControl` in `src/robot_army/boundaries/git.py`: `fetch`, `add_worktree`, `remove_worktree` (never passing `--force` on its own), `delete_branch`, `list_worktrees` including `prunable`, `status_porcelain`, `commits_ahead`, `show_file_at_ref` — every subprocess call timeout-bounded
- [X] T037 [P] [US1] Implement `SubprocessHookRunner` in `src/robot_army/boundaries/hooks.py` with a per-step timeout that kills the process **group**, not just the direct child, and captures output on failure
- [X] T038 [US1] Implement the `link` and `copy` step forms in `src/robot_army/boundaries/hooks.py`, resolving sources from the primary clone, idempotently
- [X] T039 [US1] Implement branch and worktree naming in `src/robot_army/worktree.py` per R18: branch `robot-army/issue-<n>-<slug>`, worktree keyed on issue number only
- [X] T040 [US1] Implement worktree preparation orchestration in `src/robot_army/worktree.py`: fetch, create branch from base, add worktree, run steps in order, and fail the item with captured output on any timeout or non-zero exit — never launching into a partially prepared worktree (FR-014)
- [X] T041 [US1] Implement `env` injection including `"auto"` port allocation in `src/robot_army/worktree.py`

### Launch

- [X] T042 [P] [US1] Implement `KittyDisplay` socket discovery in `src/robot_army/boundaries/kitty.py`: glob the configured pattern, probe each candidate with `kitty @ --to <s> ls` under a short timeout, take whichever answers
- [X] T043 [US1] Implement `KittyDisplay.open` in `src/robot_army/boundaries/kitty.py` with `--type=tab`, `--hold`, `--cwd`, `--title`, `--var ra_item=<id>`, and explicit `--env` passing including `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`
- [X] T044 [P] [US1] Implement `KittyDisplay.find_by_var` and `send_text` in `src/robot_army/boundaries/kitty.py`, using `user_vars` for exact lookup and terminating `send_text` with `\r`
- [X] T045 [P] [US1] Implement `DtachHost` in `src/robot_army/boundaries/dtach.py` with the `-A <socket> <cmd>` form and **no `--` separator**, plus `is_alive` that probes the socket rather than trusting its existence, and the three measured capability flags
- [X] T046 [P] [US1] Implement prompt composition in `src/robot_army/prompt.py` from issue title, body, URL, and labels, prepending `.claude/robot-army.md` when the repository has one
- [X] T047 [US1] Implement launch-argument construction in `src/robot_army/dispatch.py` producing the verified chain from R19, setting both `-n/--name` and `--remote-control`, and never using `--bare`
- [X] T048 [US1] Implement pre-launch validation in `src/robot_army/dispatch.py` for generated paths and settings files, since the worker accepts some invalid values silently and exits 0 (FR-026)
- [X] T049 [US1] Generate the session UUID and write the `sessions` row **before** the process starts, in `src/robot_army/dispatch.py` (FR-020)
- [X] T050 [P] [US1] Install the session wrapper as `share/robot-army-session-wrapper.sh`, seeded from `docs/initial-planning/spike/ra-session-wrapper.sh`, emitting the `start` record atomically per contracts/exit-record.md and preserving the no-`exec` comment
- [X] T051 [US1] Implement session-registry parsing in `src/robot_army/sessions.py`: read `~/.claude/sessions/<pid>.json`, guard on `version`, join on `sessionId`, verify liveness with `pid` **and** `procStart` against `/proc`
- [X] T052 [US1] Implement dispatch confirmation in `src/robot_army/dispatch.py`: poll for the registry entry carrying our `session_id` within the configured window, and mark `active` only then — otherwise `failed` with launch argv and window state captured (FR-025)
- [X] T053 [US1] Implement the global concurrency cap in `src/robot_army/dispatch.py`, holding items in `ready` at capacity and counting simulated sessions (FR-028, FR-055)
- [X] T054 [P] [US1] Implement `GitHubWriter.comment` in `src/robot_army/boundaries/github.py` and post the dispatch and dispatch-failure comments from `src/robot_army/dispatch.py`
- [X] T055 [US1] Wire poll → evaluate → prepare → dispatch into the daemon loop in `src/robot_army/daemon.py` with the multi-rate scheduler from R6

### Tests for User Story 1

- [X] T056 [P] [US1] Unit-test eligibility in `tests/unit/test_poll.py`: each condition failing in isolation, and the author check being non-bypassable
- [X] T057 [P] [US1] Unit-test ETag handling and backoff in `tests/unit/test_github.py`, including that a transport failure raises rather than returning empty
- [X] T058 [P] [US1] Unit-test registry parsing in `tests/unit/test_sessions.py` against `tests/fixtures/claude_sessions/`: valid, unknown `version`, truncated, absent, and a `procStart` disagreeing with `/proc`
- [X] T059 [P] [US1] Unit-test trust and fingerprint checks in `tests/unit/test_trust.py` against `tests/fixtures/claude_json/`, including malformed and missing-key cases failing closed
- [X] T060 [P] [US1] Assert no code path ever opens a `<pid>.<hash>.key` file, in `tests/unit/test_sessions.py`
- [X] T061 [P] [US1] Integration-test worktree preparation against real temporary git repositories in `tests/integration/test_worktree.py`, including a hook that hangs and must be killed at its timeout
- [X] T062 [P] [US1] Integration-test that a hook failure leaves the item `failed` and launches no session, in `tests/integration/test_worktree.py`
- [X] T063 [P] [US1] Integration-test that an unconfirmed launch yields `failed`, never `active`, in `tests/integration/test_dispatch.py`
- [ ] T064 [US1] Run quickstart scenario 3 (`no-remote`) end to end, including its negative test with a deliberately broken launch

**Checkpoint**: US1 is fully functional — a labelled issue becomes a confirmed live session.

---

## Phase 4: User Story 2 - Know what happened when a session ends (Priority: P2)

**Goal**: Every session outcome is correctly classified — clean exit, configuration error, external kill, or a launch that never really started.

**Independent Test**: Drive one session to a clean exit, kill a second, misconfigure a third; confirm three different, correct states with the evidence recorded for each.

- [X] T065 [US2] Extend `share/robot-army-session-wrapper.sh` to emit the `exit` record atomically (write `.tmp`, fsync, rename) with `exit` and decoded `signal` fields, using only bash builtins plus `printf`, `date`, `mv`, `mkdir`
- [X] T066 [P] [US2] Implement spool draining in `src/robot_army/spool.py`: read, apply in a transaction, unlink only after commit
- [X] T067 [US2] Make spool application idempotent on `(session_id, event)` in `src/robot_army/spool.py`
- [X] T068 [US2] Implement malformed-record quarantine in `src/robot_army/spool.py`: move to `spool/exits/rejected/`, raise an anomaly, never silently delete; keep records whose `session_id` matches no row as `orphan_exit_record`
- [X] T069 [US2] Implement exit-code classification in `src/robot_army/states.py` per data-model.md: 0 → `awaiting_review`; 1/126/127 → `failed`; 128+N → `interrupted` with signal recorded; other non-zero → `failed`
- [X] T070 [US2] Implement the issue-closed check in `src/robot_army/reconcile.py` moving items to `done` regardless of session state, and skipping the check entirely for simulated items (FR-035, FR-055)
- [X] T071 [P] [US2] Detect and raise the `no_transcript` anomaly in `src/robot_army/sessions.py` when a session ran but no resumable transcript exists (M0 F19)
- [X] T072 [P] [US2] Detect and raise the `session_id_mismatch` anomaly in `src/robot_army/dispatch.py` when the registry entry carries a different id than requested
- [X] T073 [US2] Implement the `show` operation in `src/robot_army/operations.py`: state history with timestamps, every session attempt with exit code and signal, worktree path and branch
- [X] T074 [US2] Drain the spool at the top of every tick in `src/robot_army/daemon.py`, and on startup before reconciliation

### Tests for User Story 2

- [X] T075 [P] [US2] Unit-test exit-code classification for every row of the mapping table in `tests/unit/test_exit_classification.py`
- [X] T076 [P] [US2] Unit-test spool parsing against `tests/fixtures/exit_records/` in `tests/unit/test_spool.py`: valid, truncated, unknown `schema`, unknown `session_id`
- [X] T077 [P] [US2] Unit-test that applying the same exit record twice is a no-op, in `tests/unit/test_spool.py`
- [X] T078 [P] [US2] Unit-test that a record written while the daemon is down is applied on next startup, in `tests/integration/test_spool_recovery.py` — the case an HTTP POST would have lost
- [X] T079 [P] [US2] Unit-test that exit 0 with the issue still open produces no anomaly, in `tests/unit/test_exit_classification.py`

**Checkpoint**: Session outcomes are correctly classified and durable across daemon downtime.

---

## Phase 5: User Story 3 - Survive terminal death, daemon restart, and reboot (Priority: P3)

**Goal**: Recorded state always matches physical reality, survivable sessions survive and reattach, and nothing is silently lost or duplicated.

**Independent Test**: With sessions running, kill the terminal emulator, restart the daemon, then reboot — confirming at each stage that every work item's state matches reality.

- [X] T080 [US3] Capture the systemd scope from `/proc/<pid>/cgroup` at confirmation and store it as an opaque handle in `src/robot_army/dispatch.py` (M0 F18)
- [X] T081 [US3] Implement `SessionHost.terminate` in `src/robot_army/boundaries/dtach.py` via `systemctl --user stop <scope>`, falling back to signalling the process group and logging that the degraded path was taken
- [X] T082 [US3] Implement the reconciliation pass in `src/robot_army/reconcile.py`: for every `active` item check liveness by `pid` **and** `procStart`, marking `interrupted` where no live session and no exit exist (FR-038, FR-040)
- [X] T083 [US3] Implement the `dispatching` max-age sweep in `src/robot_army/reconcile.py`, failing items with whatever preparation output exists (FR-041)
- [X] T084 [US3] Implement the orphan sweep in `src/robot_army/reconcile.py`: live worker processes whose `cwd` is under the worktree root but which match no `active` row, raised as `orphan_session` (FR-043, M0 F17)
- [X] T085 [US3] Implement stale-socket and prunable-worktree detection in `src/robot_army/reconcile.py`, probing rather than trusting file existence
- [X] T086 [US3] Add the `/proc`-scan fallback path in `src/robot_army/sessions.py` for when the registry version is unrecognised, raising `registry_version_unknown` once and never matching on command lines (FR-039)
- [X] T087 [US3] Run reconciliation on startup before any dispatch, and on a timer, in `src/robot_army/daemon.py` (FR-037)
- [X] T088 [P] [US3] Implement the resume-decision signals in `src/robot_army/operations.py`: uncommitted changes, commits on branch, issue closed, open PR — all computed on demand, never stored (FR-048)
- [X] T089 [US3] Implement `resume` in `src/robot_army/operations.py` launching with `--resume <session-id>` as a new attempt, never automatically (FR-046, FR-047)
- [X] T090 [P] [US3] Implement `restart`, `abandon`, and `cancel` in `src/robot_army/operations.py`, with `cancel` stopping exactly one session's process tree
- [X] T091 [P] [US3] Implement anomaly recording and the `anomalies` operation in `src/robot_army/operations.py`, relying on the partial unique index so a 60-second loop cannot duplicate rows
- [X] T092 [US3] Ensure `SIGTERM`/`SIGINT` finish the current tick, release the lock, and never touch running sessions, in `src/robot_army/daemon.py` (FR-049)

### Tests for User Story 3

- [X] T093 [P] [US3] Unit-test reconciliation transitions in `tests/unit/test_reconcile.py`: active-with-no-session, dispatching-past-max-age, awaiting-review-with-closed-issue
- [X] T094 [P] [US3] Unit-test orphan detection in `tests/unit/test_reconcile.py` using a synthetic `/proc` tree with a worktree `cwd` and no matching row
- [X] T095 [P] [US3] Unit-test the PID-reuse guard in `tests/unit/test_sessions.py`: same PID, different `procStart`, must not be treated as alive
- [X] T096 [P] [US3] Unit-test that reboot-like state (all sessions gone, rows still `active`) reconciles to `interrupted` and raises no error-level record, in `tests/unit/test_reconcile.py`
- [X] T097 [P] [US3] Assert no code path shells out to `pgrep -f` or `pkill -f`, in `tests/unit/test_no_cmdline_matching.py`
- [ ] T098 [US3] Run quickstart scenario 4 end to end, including killing kitty, reattaching with `dtach -a`, and the wrapper-kill orphan case

**Checkpoint**: The system survives terminal death, daemon restart, and reboot with correct state.

---

## Phase 6: User Story 4 - Try it without consequences (Priority: P4)

**Goal**: Four graduated effect levels, enforced at the boundaries, with simulated work observably progressing through the same states by the same code path.

**Independent Test**: Run at each effect level against real repositories with eligible issues; confirm exactly the effects that level permits occurred and no others.

- [X] T099 [P] [US4] Implement `SimulatedIssueWriter` in `src/robot_army/boundaries/github.py`, logging the intended call with full arguments and returning a structurally valid fake handle
- [X] T100 [P] [US4] Implement `SimulatedVersionControl` in `src/robot_army/boundaries/git.py`
- [X] T101 [P] [US4] Implement `SimulatedHookRunner` in `src/robot_army/boundaries/hooks.py`
- [X] T102 [P] [US4] Implement `SimulatedSessionHost` in `src/robot_army/boundaries/dtach.py`
- [X] T103 [P] [US4] Implement `SimulatedDisplay` in `src/robot_army/boundaries/kitty.py`
- [X] T104 [US4] Complete the effect-level wiring table in `src/robot_army/effects.py` per contracts/boundaries.md, deliberately providing **no** simulated reader so faking reads is unrepresentable
- [X] T105 [US4] Propagate `dry_run` onto work item and session rows in `src/robot_army/dispatch.py` and `src/robot_army/poll.py`, denormalising onto sessions so session queries need no join
- [X] T106 [US4] Ensure simulated items are counted by the concurrency cap and reconciled like live ones, while the issue-closed check is skipped for them, in `src/robot_army/reconcile.py` and `src/robot_army/dispatch.py` (FR-055)
- [X] T107 [US4] Log the effect level at startup, include it in the heartbeat payload, and mark simulated rows visibly in every listing, in `src/robot_army/daemon.py` and `src/robot_army/operations.py` (FR-057)
- [X] T108 [US4] Add `--effect-level` and the `--dry-run` alias to `robot-army run` in `src/robot_army/cli.py`
- [X] T109 [P] [US4] Implement `purge-simulated` in `src/robot_army/operations.py`, removing only simulated rows and never touching worktrees on disk

### Tests for User Story 4

- [X] T110 [P] [US4] Unit-test the wiring table in `tests/unit/test_effects.py`: every level selects the expected implementation for every boundary, and reads are real at all four
- [X] T111 [P] [US4] Unit-test that simulated implementations return structurally valid handles rather than `None`, in `tests/unit/test_effects.py`
- [X] T112 [P] [US4] Integration-test that `plan` produces zero writes, zero sessions, and zero filesystem changes under the worktree root, in `tests/integration/test_effect_levels.py`
- [X] T113 [P] [US4] Integration-test that a simulated item still counts against the concurrency cap, in `tests/integration/test_effect_levels.py`
- [ ] T114 [US4] Run quickstart scenarios 1 and 2

**Checkpoint**: All four effect levels behave correctly and simulated work is fully observable.

---

## Phase 7: User Story 5 - Operate and inspect from a terminal (Priority: P5)

**Goal**: Every capability reachable from a shell, with honest exit codes and a readable audit trail.

**Independent Test**: Perform a full working day's operations — start, inspect, force poll, cancel, resume, read the log — using only a shell.

- [X] T115 [US5] Implement startup precondition checks in `src/robot_army/daemon.py`: terminal socket answers, state directory writable, database opens and migrates — exiting 3 with a clear reason (FR-067)
- [X] T116 [P] [US5] Implement `status` in `src/robot_army/operations.py` showing effect level, health, counts and listings by state, and unacknowledged anomalies, with `--state` and `--repo` filters
- [X] T117 [P] [US5] Implement `repos` in `src/robot_army/operations.py` showing onboarding, fingerprint, and trust status per repository
- [X] T118 [P] [US5] Implement `worktree list`, `worktree remove`, and `worktree prune` in `src/robot_army/operations.py`, with removal covering **both** worktree and branch and refusing on a dirty tree without `--force`
- [X] T119 [P] [US5] Implement `log` in `src/robot_army/operations.py` reading the audit JSONL with `--since`, `--item`, and `--follow`, skipping and counting unparseable trailing lines
- [X] T120 [P] [US5] Implement `poll` and `reconcile` operations in `src/robot_army/operations.py`, delegating to a running daemon or acting directly when none holds the lock
- [X] T121 [P] [US5] Implement `retry` in `src/robot_army/operations.py`, refusing with the reason when the blocking condition still holds
- [X] T122 [P] [US5] Implement `doctor` in `src/robot_army/operations.py` checking config, schema version, socket reachability, binary presence, permissions, disk space, and `CLAUDE_CODE_*` variables in the terminal daemon's environment (M0 F19)
- [X] T123 [US5] Add `--json` output to every read command in `src/robot_army/cli.py`
- [X] T124 [US5] Add `--include-simulated` to every listing command in `src/robot_army/cli.py`
- [X] T125 [US5] Add `--once` to `robot-army run` in `src/robot_army/cli.py`

### Tests for User Story 5

- [X] T126 [P] [US5] Unit-test exit codes for every command's failure paths in `tests/unit/test_cli_exit_codes.py`
- [X] T127 [P] [US5] Unit-test that `worktree remove` refuses on both uncommitted and merely untracked changes, in `tests/integration/test_worktree_removal.py`
- [X] T128 [P] [US5] Unit-test that a second daemon instance fails with exit 3 naming the holder, in `tests/integration/test_single_instance.py`
- [ ] T129 [US5] Run quickstart scenario 9

**Checkpoint**: The full CLI surface works and every capability is terminal-reachable.

---

## Phase 8: User Story 6 - Notice when the daemon has died (Priority: P6)

**Goal**: A dead or stalled daemon is detected without the maintainer noticing by accident.

**Independent Test**: Kill the daemon uncleanly; confirm staleness is detectable within the configured window from the terminal and through the notification channel.

- [X] T130 [P] [US6] Implement atomic heartbeat writing in `src/robot_army/health.py` with timestamp, PID, effect level, current activity, and cycle counters
- [X] T131 [US6] Write the heartbeat on every tick, including the current activity so a long preparation step is visible rather than looking like a hang, in `src/robot_army/daemon.py`
- [X] T132 [P] [US6] Implement the `health` command in `src/robot_army/operations.py`, exiting 4 when the heartbeat is stale or absent, with a configurable `--max-age`
- [X] T133 [P] [US6] Implement `--notify` webhook POST on failure in `src/robot_army/health.py`, with an explicit timeout
- [X] T134 [P] [US6] Create `systemd/robot-army-health.service` and `systemd/robot-army-health.timer` running the check every five minutes — the timer, not the daemon, is the dead-man's switch
- [X] T135 [US6] Surface all named anomaly kinds in `status` and `anomalies` output in `src/robot_army/operations.py` (FR-065)

### Tests for User Story 6

- [X] T136 [P] [US6] Unit-test staleness detection boundaries in `tests/unit/test_health.py`: fresh, exactly at threshold, stale, and absent heartbeat
- [X] T137 [P] [US6] Unit-test that a heartbeat write interrupted mid-way never leaves a partial file observable, in `tests/unit/test_health.py`
- [ ] T138 [US6] Run quickstart scenario 7, including installing and verifying the systemd timer

**Checkpoint**: Silent death — the failure mode the planning document singles out — is now detectable.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T139 [P] Write `README.md` for the author's future self: what it does, how to run it, where the logs are, and what they mean (Principle V)
- [X] T140 [P] Document the log location, record format, and review path in `docs/logging.md`
- [X] T141 [P] Document the state directory layout and what survives a reboot in `docs/state.md`
- [X] T142 Verify every enumerated Principle III logging gap in plan.md still matches the implementation, and update the plan if it drifted
- [X] T143 Run quickstart scenario 8 and confirm no token appears anywhere under the log directory
- [ ] T144 Run quickstart scenarios 5 and 6 end to end at `live` against a real repository
- [X] T145 Confirm the full `pytest` suite passes — implementation is not complete until it does (constitution, Governance)
- [X] T146 [P] Run `ruff` across `src/` and `tests/` and resolve findings
- [X] T147 Review `src/robot_army/` for any `if dry_run:` outside `effects.py`, which would mean FR-053 was not really implemented

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — **blocks all user stories**
- **US1 (Phase 3)**: Depends on Foundational
- **US2 (Phase 4)**: Depends on Foundational; needs US1's session rows and wrapper to be meaningful
- **US3 (Phase 5)**: Depends on Foundational; needs US1's sessions to reconcile and US2's exit records to distinguish "exited" from "lost"
- **US4 (Phase 6)**: Depends on Foundational and on the boundary implementations from US1–US3 existing to have counterparts
- **US5 (Phase 7)**: Depends on Foundational; each command depends on the story that produced its data
- **US6 (Phase 8)**: Depends on Foundational and on the daemon loop from US1
- **Polish (Phase 9)**: Depends on all desired stories

### Honest note on story independence

The template's ideal is fully independent stories. These are **not** fully independent, and pretending
otherwise would produce a misleading plan. US1 is a genuine standalone MVP. US2 and US3 are
observability and recovery layers over US1 — each is independently *testable* once US1 exists, but
neither delivers value without it. US4's simulated implementations need their real counterparts to
exist first. US5 and US6 are cross-cutting surfaces.

The seam that *is* real, and the one to use if this milestone proves too large: **US1+US2 form a
coherent deliverable** (dispatch and outcome tracking) and **US3–US6 form a second** (recovery,
effect levels, terminal surface, health). That is the 001a/001b split flagged in the requirements
checklist.

### Within Each Story

- Models before services, services before orchestration, orchestration before CLI surface
- Boundary implementations before the code that calls them
- Tests may be written in any order relative to implementation — the constitution does not mandate test-first, only that meaningful tests exist before the checkpoint

### Parallel Opportunities

- **Phase 1**: T002, T003, T004 together
- **Phase 2**: T006, T007, T017, T018 together; then T011, T015 together; then all of T022–T027 together
- **US1**: T028, T033, T036, T037, T042, T044, T045, T046, T050 are all different files; the T056–T063 test block runs entirely in parallel
- **US2**: T066, T071, T072 together; T075–T079 together
- **US3**: T088, T090, T091 together; T093–T097 together
- **US4**: T099–T103 are five different files and are the cleanest parallel block in the plan
- **US5**: T116–T122 are all separate operations; T126–T128 together
- **US6**: T130, T132, T133, T134 together

---

## Parallel Example: User Story 4

```bash
# The five simulated boundary implementations touch five different files
# with no dependencies between them:
Task: "Implement SimulatedIssueWriter in src/robot_army/boundaries/github.py"
Task: "Implement SimulatedVersionControl in src/robot_army/boundaries/git.py"
Task: "Implement SimulatedHookRunner in src/robot_army/boundaries/hooks.py"
Task: "Implement SimulatedSessionHost in src/robot_army/boundaries/dtach.py"
Task: "Implement SimulatedDisplay in src/robot_army/boundaries/kitty.py"
```

---

## Implementation Strategy

### Recommended build order for a single maintainer

The phases are ordered by *value*, which is correct for the specification. For actually building
this alone, one deviation is worth making deliberately:

**Pull T099–T104 (US4's simulated boundaries and wiring) forward, to immediately after US1's
boundary implementations exist.** The reason is practical rather than architectural: without them,
every US1 iteration runs against real GitHub, creates real worktrees, and burns real subscription
quota. With them, `--effect-level local` becomes the development loop. This is exactly the argument
the M0 spike wrapper makes for its own dry-run mode — testing a four-part launch chain with a real
worker makes every failure ambiguous.

This does not change the phase structure or what US4 delivers; it changes when you write five small
classes.

### MVP First (User Story 1 only)

1. Phase 1: Setup
2. Phase 2: Foundational — blocks everything
3. Phase 3: User Story 1
4. **STOP and VALIDATE**: quickstart scenario 3, including its negative test
5. At this point a labelled issue reliably becomes a confirmed live session

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → **MVP**: dispatch works and is confirmed
3. US2 → outcomes are classified and survive daemon downtime
4. US3 → safe to leave running unattended
5. US4 → safe to iterate on config and hooks
6. US5 → comfortable daily operation
7. US6 → safe to forget about

Steps 1–3 are the point at which this becomes genuinely useful. Step 4 is the point at which it
becomes trustworthy — before it, an unnoticed kitty crash silently strands work.

---

## Notes

- `[P]` means different files with no incomplete dependencies
- Every task names its file path; no task should require re-reading the design docs to locate its target
- Commit after each task or logical group, with messages explaining **why** (constitution, Development Workflow)
- Several tasks encode M0 findings that are counter-intuitive and easy to "fix" back into bugs — the
  no-`--`-separator in T045, the no-`exec` in T050, and the `--force`-never-by-default in T036 are
  the three most likely to be undone by a well-meaning later edit. Each carries a comment in the
  contracts explaining why; keep them

---

## Not run: scenarios requiring the maintainer's live environment

Six validation tasks are left unchecked because they cannot be performed by anyone but the
maintainer, on their machine, with their credentials. They are tracked in
**[issue #1](https://github.com/jantman/robot-army/issues/1)**, which batches them with any
later milestone's deferred verification into a single session rather than interrupting each
milestone. Milestone 002 has since added its two to the same issue, and its section is
cheapest to run immediately after scenarios 3 and 5 here, while live sessions still exist. Each would create real effects — real
sessions consuming real subscription quota, real comments on real issues, or changes to the
user's systemd configuration. They are listed here rather than quietly marked done.

| Task | Scenario | What it needs |
|---|---|---|
| T064 | 3 — a real session at `no-remote` | A live kitty session and the `claude` worker launching for real |
| T098 | 4 — surviving terminal death | Live sessions to kill, reattach, and orphan |
| T114 | 1 and 2 — `plan` and `local` | A real GitHub token and an onboarded repository |
| T129 | 9 — clean up | Real worktrees produced by earlier scenarios |
| T138 | 7 — the dead-man's switch | Installing and enabling a systemd user timer |
| T144 | 5 and 6 — `live` | Posting real comments on a real issue |

**What was verified in their place**, so the gap is bounded rather than open-ended:

- The daemon was run for real (`run --dry-run --once`) against a live config with a live
  GitHub token. It discovered the maintainer's actual kitty socket by probing, reconciled
  against the actual session registry (2 live sessions, `degraded: false`), recorded a 401
  as a bounded failure with backoff, wrote a heartbeat, and exited 0.
- **T143's check was performed against that run**: the token appears nowhere under the log
  directory.
- `doctor` was run for real and correctly failed on `CLAUDE_CODE_CHILD_SESSION` — the M0 F19
  hazard — present in this very environment.
- The systemd units pass `systemd-analyze verify --user`. Enabling the timer is left to the
  maintainer, since it changes their session configuration.
- Every scenario's *mechanism* has automated coverage: effect-level consequences
  (`tests/integration/test_effect_levels.py`), unconfirmed launches
  (`tests/integration/test_dispatch.py`), terminal-death reconciliation and the orphan sweep
  (`tests/unit/test_reconcile.py`), spool survival across daemon downtime
  (`tests/integration/test_spool_recovery.py`), removal refusals
  (`tests/integration/test_worktree_removal.py`), and staleness detection
  (`tests/unit/test_health.py`).
