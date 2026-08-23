# Implementation Plan: Minimum Daemon

**Branch**: `001-minimum-daemon` | **Date**: 2026-08-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-minimum-daemon/spec.md`

## Summary

A single-process, single-threaded Python daemon that polls GitHub for issues the maintainer has
labelled, prepares an isolated git worktree, launches a real interactive Claude Code session into
the already-running kitty instance through a `dtach` host, and tracks that session's fate through a
state machine in SQLite — reconciling on startup and on a timer so that terminal death, daemon
restart, and reboot all resolve to a correct, recoverable state rather than silent drift.

The technical approach is shaped by three things: the M0 spike's measured findings (which invalidate
several of the planning document's assumptions and are treated as ground truth here), the
constitution's insistence on stdlib-first simplicity and total auditability, and one deliberate
architectural departure — **session exits are reported through an atomic spool file rather than an
HTTP POST**, so the daemon opens no listening socket at all and an exit record survives the daemon
being down.

Full reasoning in [research.md](./research.md). Schema and state machines in
[data-model.md](./data-model.md). Interfaces in [contracts/](./contracts/). Validation in
[quickstart.md](./quickstart.md).

## Technical Context

**Language/Version**: Python 3.14 (3.14.7 present), managed with `uv` 0.12.3

**Primary Dependencies**: `httpx` (GitHub REST with conditional-request and rate-limit header
control). Dev only: `pytest`. Everything else is standard library: `sqlite3`, `tomllib`, `argparse`,
`fcntl`, `subprocess`, `json`, `hashlib`, `pathlib`, `uuid`.

**External binaries**: `git` 2.55.0, `dtach`, `kitty` 0.48.2 (`kitty @` control socket),
`systemctl --user`, the `claude` worker binary.

**Storage**: SQLite at `~/.local/state/robot-army/state.db`, WAL mode, `foreign_keys=ON`,
`synchronous=FULL`. Five tables. Hand-written SQL, no ORM (R2). Migrations via a `PRAGMA
user_version` ladder (R3).

**Testing**: `pytest`. Failure-path and interruption-path tests mandatory for the state machine,
persistence/recovery, and all external-input parsing (R20).

**Target Platform**: One Linux workstation, single user, graphical session running. Not portable and
not required to be.

**Project Type**: Single project — a CLI application with a long-running `run` subcommand.

**Performance Goals**: Dispatch-to-session under 2 minutes with no preparation steps (SC-001). Exit
detection within one 5-second tick. GitHub polling sustainable indefinitely at 60 s via ETag
conditional requests, which cost nothing against the rate limit when unchanged.

**Constraints**: No inbound network exposure — **no listening sockets whatsoever** in this
milestone. Every network call, subprocess, and socket probe bounded by an explicit timeout. Global
cap of 2 concurrent sessions by default, counting simulated ones. Sessions must be real interactive
terminal sessions in the running kitty instance; headless execution does not satisfy the
requirement.

**Scale/Scope**: ~300 local repositories inventoried, of which a handful are onboarded and polled.
Single-digit concurrent work items. 73 functional requirements across 6 user stories. Worktrees
measured at up to 499 MB each once a virtualenv exists, which makes disk a real constraint and
automatic cleanup a deliberate non-goal here.

## Constitution Check

*GATE: evaluated before Phase 0, re-evaluated after Phase 1 design. Both passes recorded.*

### I. Simplicity First (YAGNI & KISS)

| Check | Assessment |
|---|---|
| Speculative generality | **Pass with one justified item.** Five boundary interfaces are introduced. Each has exactly two implementations *in this milestone*, both mandated by FR-051–FR-058. See Complexity Tracking. |
| New dependencies justified | **Pass.** Exactly one runtime dependency (`httpx`) plus `pytest`. SQLAlchemy was evaluated and rejected in R2; PyGithub, pydantic, click, PyYAML, and `watchdog` were each evaluated and rejected in favour of stdlib. |
| Single process, obvious control flow | **Pass.** One process, one thread, one loop. No asyncio, no threads, no queues, no brokers. |
| Daemon justified against demonstrated need | **Pass.** The daemon is the product — the requirement is that work is picked up while the maintainer is away. |
| Fewer moving parts wins | **Pass, and improved during design.** R5 removed the HTTP server the planning document specified; the Isolated Checkout entity was collapsed into columns rather than a table. |

### II. Single-User, Local-First

| Check | Assessment |
|---|---|
| No multi-tenancy, auth, or roles | **Pass.** None built. The OS user is the trust boundary. |
| Local filesystem state at documented paths | **Pass.** R16 tabulates every path; `contracts/config.md` documents them. |
| No hosted database required | **Pass.** SQLite — this was the constitutional conflict raised and resolved during `/speckit-specify`. |
| Secrets from env or git-ignored files, never logged | **Pass.** A literal token in the config is a validation *error*. Redaction is applied at a single choke point (R14) and asserted by a test (quickstart scenario 8). |
| No public IP, proxy, or deployment infrastructure | **Pass, strengthened.** R5's spool-file design means **no listening socket exists at all**. |

### III. Total Accountability

**Pass, with four enumerated and justified gaps.** The constitution requires every permitted gap to
be named in the plan; an undocumented gap is a violation.

**What is logged** (JSONL, append-only, one record per line, secrets redacted, UTC timestamps —
R14):
- Every work item and session state transition, written **inside the same transaction** as the state
  change, so a crash cannot produce one without the other.
- Every outward-facing action as a **pair**: an `intent` record flushed *before* execution and an
  `outcome` record after, sharing an `action_id`. This satisfies the Operating Constraints rule that
  irreversible actions be logged before execution, which an append-only log cannot otherwise honour.
  The pairing is also the crash signature: an `intent` with no `outcome` means the process died
  mid-action.
- Every subprocess execution (git, hooks, kitty, dtach, systemctl) with argv, exit code, duration,
  and captured output on failure.
- Every GitHub **write**, individually.
- Every GitHub request **failure** and every **retry**, individually, with backoff decisions.
- Every eligibility rejection, with which condition failed (FR-009).
- Every anomaly detection, every dispatch confirmation, every effect-level decision at startup.
- Every simulated call, through the same log as a real one, marked as simulated.

**Enumerated gaps, per the Principle III exception clause:**

| Gap | Justification |
|---|---|
| Individual **successful, read-only** GitHub GETs are logged as one aggregate record per repository per poll cycle (status, ETag hit, rate-limit remaining, item counts) rather than one record per HTTP call | Cost disproportionate to risk. These change no state outside the process; at a 60 s poll the individual records would be pure volume. Every failure and every retry **is** logged individually, so the reconstruction standard still holds for anything that went wrong |
| Individual SQLite statements are not logged; the **state transition** they effect is | The transition is the meaningful unit for reconstruction, and the database is directly inspectable at any time. Logging each INSERT would flood the log without adding reconstruction power |
| Heartbeat file writes (every 5 s) are not logged | ~17,000 records per day of pure noise. The heartbeat file **is** the record, it is inspectable, and its staleness is the signal |
| Reads of `/proc` and the session registry during reconciliation are logged as an aggregate result per pass, not per file | Same disproportion argument. The *conclusions* — sessions found, orphans detected, states changed — are logged individually |
| **Actions taken by the Claude session itself** inside the worktree are not logged by this system | They occur outside this process entirely. This system records the dispatch, the session identity, and the transcript's location; the worker's own transcript is the record of what it did. Claiming otherwise would be dishonest about what our log covers |

**Silent failure**: forbidden throughout. Specifically encoded: a GitHub transport failure raises
rather than returning an empty result, because "no eligible work" and "I could not ask" are
different facts (`contracts/boundaries.md`); a malformed exit record is quarantined and raises an
anomaly rather than being deleted; an unknown session-registry version degrades to `/proc` **and**
raises an anomaly; a degraded terminate path logs that it was taken.

### IV. Interruption Tolerance

**Pass.**

- **Atomic writes**: every state change is a SQLite transaction with `synchronous=FULL`; the
  heartbeat and every exit record use write-fsync-rename.
- **Restartable**: reconciliation runs before any dispatch on every start; `data-model.md` carries a
  complete table of "interrupted at X → result on next start" for every persisted operation.
- **Idempotent**: `UNIQUE (source, source_id, dry_run)` makes re-polling a no-op rather than a second
  worktree; exit-record application is idempotent on `(session_id, event)`; the partial unique index
  on unacknowledged anomalies prevents duplicate rows from a 60-second reconcile loop.
- **Bounded network and subprocess calls**: explicit timeouts everywhere, bounded retries with
  backoff. M0 F15 (a hook that hangs forever rather than failing) is the reason every preparation
  step carries a timeout and kills its process **group**.
- **Precautions proportionate**: no consensus, no replication, no bespoke journalling. SQLite,
  rename, timeouts, and a reconciliation pass.

**The constitution's mandatory question — what happens if it is killed halfway through?** Answered
per-operation in `data-model.md`'s interruption table. The single most consequential answer: **if the
daemon is down when a session exits, the exit record survives in the spool and is applied on next
startup.** Under the planning document's HTTP-POST design that record would have been lost
permanently, silently downgrading a clean completion into a phantom that reconciliation could only
ever classify as `interrupted`. This is why R5 departs from the planning document.

### V. Public Code, Unsupported Project

**Pass.** No credentials or private hostnames committed — the config lives in `~/.config`, tokens
come from the environment, and the example config uses the maintainer's own already-public
repository names. No stable public API, no deprecation cycle, no packaging beyond a local
`pyproject.toml` entry point. Documentation is written for the author's future self: what it does,
how to run it, where the logs are, and what they mean.

### Operating Constraints & Development Workflow

| Check | Assessment |
|---|---|
| Single Linux machine, no portability constraint | **Pass** — `/proc`, `flock`, and systemd scopes are used freely and deliberately |
| Every capability reachable from the terminal | **Pass** — `contracts/cli.md` is the complete surface; there is no other interface in this milestone |
| Commands exit non-zero on failure | **Pass** — documented exit codes 0–4 |
| No GUI prerequisite | **Pass** — though kitty must be running, since a real terminal session *is* the product (planning §2, settled) |
| Plain text / SQLite persistence | **Pass** — SQLite plus TOML plus JSONL, all human-inspectable |
| Log location and format documented, review path documented | **Pass** — R14, R16, and `robot-army log` |
| Irreversible actions logged before execution and gated | **Pass** — intent/outcome pairing; `onboard` requires explicit confirmation; `worktree remove` refuses dirty trees; resume is never automatic |
| Unit tests for all new behaviour | **Pass** — R20 |
| Failure/interruption tests for persistence, state machines, external-input parsing | **Pass** — R20 names all three categories with specific cases |
| No coverage targets, test-first not required | **Pass** — none adopted |

### Post-Design Re-Evaluation

Re-run after Phase 1. **No new violations.** Three things changed *toward* the constitution during
design rather than away from it:

1. R5's spool-file decision eliminated the HTTP server, removing a listening socket, a server loop,
   a port-conflict failure mode, and a whole class of lost-record bugs.
2. The Isolated Checkout entity collapsed from a table into columns, and derived values (dirty,
   prunable, commits-ahead) became computed rather than stored — a stored copy would be wrong the
   moment the maintainer touched the directory.
3. Splitting `IssueSource` into reader and writer protocols made "polling is always real" structural
   rather than a rule to remember, and made a simulated reader impossible to select by accident.

The one item in Complexity Tracking was re-examined after the contracts were written and stands
unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/001-minimum-daemon/
├── plan.md                  # This file
├── spec.md                  # Feature specification
├── research.md              # Phase 0 — 20 decisions with rationale and alternatives
├── data-model.md            # Phase 1 — schema, state machines, interruption table
├── quickstart.md            # Phase 1 — setup and 9 validation scenarios
├── checklists/
│   └── requirements.md      # Spec quality checklist
├── contracts/               # Phase 1
│   ├── cli.md               # Command surface (also milestone 002's API surface)
│   ├── config.md            # config.toml schema
│   ├── exit-record.md       # Wrapper ↔ daemon spool contract
│   ├── boundaries.md        # The five effect-level seams
│   └── consumed-formats.md  # External formats we read but do not own
└── tasks.md                 # Phase 2 — created by /speckit-tasks, NOT by this command
```

### Source Code (repository root)

```text
pyproject.toml               # uv-managed; console_scripts entry point `robot-army`
README.md

src/robot_army/
├── __init__.py
├── __main__.py              # python -m robot_army
├── cli.py                   # argparse surface; thin — delegates to operations
├── operations.py            # every CLI verb as a callable; milestone 002 calls these too
├── daemon.py                # the loop, the multi-rate scheduler, signal handling, the lock
├── config.py                # TOML load + aggregate validation
├── effects.py               # EffectLevel enum and the boundary wiring table
├── db.py                    # connection, pragmas, transactions, dry_run default scope
├── migrations.py            # PRAGMA user_version ladder
├── models.py                # dataclasses for every entity
├── states.py                # state machines; the single legal-transition gate
├── audit.py                 # JSONL writer, intent/outcome pairing, redaction choke point
├── health.py                # heartbeat write, staleness check, webhook notify
├── poll.py                  # polling and eligibility evaluation
├── dispatch.py              # queue selection, launch, confirmation
├── worktree.py              # worktree lifecycle and preparation-step orchestration
├── reconcile.py             # the reconciliation pass, including the orphan sweep
├── sessions.py              # registry parsing, liveness, PID-reuse guard, classification
├── procinfo.py              # /proc reads: exe, cwd, starttime, cgroup
├── spool.py                 # exit-record draining, quarantine, idempotent application
├── prompt.py                # prompt composition from issue + .claude/robot-army.md
└── boundaries/
    ├── __init__.py
    ├── github.py            # GitHubReader, GitHubWriter, SimulatedIssueWriter
    ├── git.py               # GitVersionControl, SimulatedVersionControl
    ├── hooks.py             # SubprocessHookRunner, SimulatedHookRunner
    ├── dtach.py             # DtachHost, SimulatedSessionHost
    └── kitty.py             # KittyDisplay, SimulatedDisplay

share/
└── robot-army-session-wrapper.sh    # bash; seeded from docs/initial-planning/spike/

systemd/
├── robot-army-health.service        # oneshot: robot-army health --notify
└── robot-army-health.timer          # every 5 min — the actual dead-man's switch

tests/
├── conftest.py
├── fixtures/
│   ├── claude_sessions/     # registry files: valid, unknown version, truncated, PID-reuse
│   ├── claude_json/         # trust file: valid, missing key, malformed
│   ├── proc/                # synthetic /proc trees
│   └── exit_records/        # valid, truncated, unknown schema, unknown session
├── unit/                    # states, config, audit redaction, parsers, effects wiring
└── integration/             # real git in tmp repos; full loop with simulated boundaries
```

**Structure Decision**: Single project, `src/` layout. The only sub-package is `boundaries/`, which
groups exactly the five seams the effect levels act on — the grouping is not organisational tidiness,
it is the unit of substitution, and keeping it visible in the tree makes FR-053's guarantee legible
to a future reader. Everything else is a flat module namespace, because with roughly twenty modules a
deeper hierarchy costs navigation and buys nothing.

`operations.py` exists so that every CLI verb is a plain callable rather than logic embedded in
argument parsing. Milestone 002's HTTP API is then a second caller of the same functions rather than
a reimplementation — the single cheapest thing this milestone can do for the next one, at
essentially zero cost now.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Five boundary interfaces, each with a real and a simulated implementation, where Principle I forbids strategy interfaces with one caller | FR-051 through FR-058 require four graduated dry-run effect levels enforced **at the boundary, not at call sites**. Each interface therefore has two concrete implementations in this milestone — a present, concrete requirement, not anticipated generality | Scattered `if dry_run:` checks at call sites were explicitly evaluated and rejected by planning §2 and by FR-053: they drift as new code forgets the check, they cannot be tested, and the simulated path diverges from the real one — which is the exact failure the mode exists to prevent. A single global flag was also rejected: the four levels are independently useful and one boolean cannot express them |
| One runtime dependency (`httpx`) where the standard library is the default | FR-008 requires explicit timeouts, bounded retries, and rate-limit backoff on every call, and a 60 s poll is only sustainable via ETag conditional requests | `urllib.request` would mean hand-rolling connection pooling, timeout plumbing, and retry logic — more code added than removed. PyGithub was rejected in the other direction: it hides the response headers where ETags and rate-limit budget live, so the wrapper obstructs the requirement it would otherwise serve |

Nothing else in this design required justification against the constitution. Notably **not** in this
table, because they were removed rather than justified: an ORM, an HTTP server, a listening socket, a
migration framework, a config-schema library, a CLI framework, threads, and asyncio.
