# Phase 0 Research: Minimum Daemon

**Feature**: [spec.md](./spec.md) | **Date**: 2026-08-23

Every decision below is recorded as Decision / Rationale / Alternatives. Where a decision departs
from the planning document, the departure is called out explicitly with its reason — the planning
document is prior art, not a contract.

Environment facts confirmed on this machine before deciding: Python 3.14.7, SQLite 3.53.4,
`uv` 0.12.3, `dtach`, `kitty` 0.48.2, `git` 2.55.0, `curl`, `systemctl`, `jq` all present;
`XDG_STATE_HOME` and `XDG_CONFIG_HOME` unset (defaults apply), `XDG_RUNTIME_DIR=/run/user/1000`;
`~/.claude/sessions/<pid>.json` registry and `~/.claude.json` both present with the shapes M0
recorded.

---

## R1 — Language and runtime

**Decision**: Python 3.14, project managed with `uv`, layout `src/robot_army/`, a `pyproject.toml`
declaring console entry point `robot-army`.

**Rationale**: The work is process supervision, filesystem manipulation, and REST polling — all
areas where Python's standard library is strong and where the code will be read more often than it
is run. The maintainer's own ecosystem is Python-dominant (M0 inventoried 39 repos with virtualenvs
and 47 with `tox.ini` out of 294), so this is the language they will debug fastest at 2am, which
Principle I's "one maintainer" rationale makes the deciding factor. `uv` is already installed and
removes the venv-management friction that would otherwise be the daily cost of choosing Python.

**Alternatives considered**:
- **Go** — a single static binary with no interpreter or virtualenv is genuinely attractive for a
  daemon, and its process/signal handling is excellent. Rejected because it optimises deployment,
  which is not a problem here (one machine, started by hand), at the cost of maintainability for
  this specific maintainer.
- **Bash** — the M0 spike is bash and it worked. Rejected: a state machine over SQLite with
  structured audit logging and JSON parsing in bash is how you get a program nobody can change.
  Bash remains correct for the session wrapper alone (see R5).

## R2 — Storage layer: stdlib `sqlite3`, no ORM

**Decision**: SQLite via the standard library `sqlite3` module, hand-written SQL, rows mapped to
dataclasses by an explicit `row_factory`. No SQLAlchemy, no Alembic.

**Rationale**: This was left open by the spec deliberately, to be argued here. The honest question
is what an ORM would remove for *this* schema. The answer is little: seven tables, no polymorphic
relationships, no identity map worth having, no cross-dialect query building (there is one dialect,
per the resolved constitutional conflict). `sqlite3` already supplies parameter binding,
transactions as a context manager, and `Row` access; mapping to dataclasses is roughly twenty lines
and makes the state machine's types explicit rather than dynamic. Alembic's migration machinery is
real value in a team with deployed environments and is close to none for one file on one machine
that a `PRAGMA user_version` ladder handles in about thirty lines (R3).

Set against that, SQLAlchemy is a substantial dependency with its own semantics — session lifecycle,
flush ordering, lazy-load surprises — that must be understood to debug the persistence layer under
interruption, which is exactly the layer Principle IV says must be most trustworthy. Under "the
standard library and already-present dependencies are the default", stdlib wins here.

Note this decision does not resurrect the MariaDB question. It closes it: hand-written SQL against
one engine is the thin persistence layer planning §12 asked for, and a hypothetical future move
stays a bounded rewrite of one module rather than a rewrite of the daemon.

**Alternatives considered**:
- **SQLAlchemy Core (no ORM)** — the closest call, and defensible. It buys typed schema definition
  and safer dynamic query construction. Rejected on volume: with no dynamic query construction to
  speak of, it removes less code than it adds concepts.
- **SQLAlchemy ORM + Alembic** — rejected for the reasons above.
- **Plain JSON or JSONL state files** — permitted by the constitution and simpler still, but gives
  up atomic multi-row transitions, which FR-036 and FR-072 need. SQLite is the constitution's own
  named answer for exactly this.

**Configuration required**: `PRAGMA journal_mode=WAL` (concurrent CLI reads while the daemon holds a
write connection), `PRAGMA foreign_keys=ON` (off by default in SQLite, and this schema relies on
them), `PRAGMA synchronous=FULL` (Principle IV — the machine loses power; the throughput cost is
irrelevant at this write volume).

## R3 — Schema migrations

**Decision**: An ordered list of migration functions, each bumping `PRAGMA user_version`, applied in
a transaction at daemon and CLI startup. Forward-only; no downgrades.

**Rationale**: `user_version` is a SQLite-native integer designed for precisely this. Forward-only
is honest for a single-user application where the rollback plan is restoring the file from backup.
Principle V explicitly frees the project from migration shims and backward compatibility.

**Alternatives considered**: Alembic (see R2). Recreate-and-reimport on every schema change —
rejected because in-flight work items and live sessions must survive an upgrade.

## R4 — GitHub access

**Decision**: `httpx` with a thin hand-written client module (`boundaries/github.py`) speaking the
REST API directly. Conditional requests using `ETag` / `If-None-Match` on the issue-listing poll.
Explicit connect and read timeouts on every call. Bounded exponential backoff with jitter, honouring
`Retry-After` and `X-RateLimit-Reset`.

**Rationale**: FR-008 makes bounded timeouts, bounded retries, and rate-limit backoff requirements
rather than niceties, and FR-006 asks for a 60-second poll. The single technique that makes a
60-second poll sustainable is the conditional request: an unchanged issue list returns `304` and
costs **zero** against the rate limit. A high-level client library abstracts response headers away,
which is where ETags and rate-limit budget live — so the wrapper library actively obstructs the
requirement. The surface actually needed is small: list issues by label, get an issue, create a
comment, list the authenticated user's repositories.

**Alternatives considered**:
- **PyGithub** — removes pagination and gives typed objects, but hides conditional-request control
  and the rate-limit headers. Rejected on the ETag argument above.
- **`requests`** — equivalent for our purposes and already installed system-wide. `httpx` chosen for
  its explicit, mandatory-by-construction timeout API, which suits a requirement that says every
  call MUST set a timeout.
- **stdlib `urllib.request`** — avoids the dependency but hand-rolls connection pooling, retries,
  and timeout plumbing. This is a case where one dependency removes more code than it adds.

## R5 — Session exit reporting: an atomic spool directory, not an HTTP POST

**Decision**: The session wrapper records each session's outcome by writing a single JSON file
atomically (`write` to `<name>.tmp`, `fsync`, `rename`) into `~/.local/state/robot-army/spool/exits/`.
The daemon drains that directory at the top of every loop tick, applies each record in a
transaction, and unlinks the file only after the transaction commits. **The daemon opens no listening
socket and runs no HTTP server in this milestone.**

**Rationale — this is a deliberate departure from planning §9**, which specifies that the wrapper
"POSTs the exit code back to the daemon's API". The departure is on Principle IV grounds and it is
not a small point: **an HTTP POST to a dead daemon is a lost exit record.** If the daemon is
restarting, upgrading, or crashed when a session ends, the POST fails — the M0 spike wrapper's own
code already reveals this, degrading to `echo WARNING` on curl failure — and the outcome is
permanently unrecoverable, downgrading a clean `awaiting_review` into a phantom that reconciliation
can only ever classify as `interrupted`. A spool file survives the daemon being down, survives a
reboot, and is replayed on next startup. It is also the constitution's own prescribed atomic-write
pattern, applied to the one piece of state that crosses a process boundary.

Secondary benefits that each independently reinforce the choice: no listening port satisfies the
planning document's "no inbound network exposure" principle absolutely rather than approximately;
no HTTP server means no server thread, no port allocation, no bind-conflict failure mode, and no
concurrency in a daemon the constitution wants single-threaded; and the wrapper keeps `curl` as an
optional convenience rather than a hard dependency in a bare launch environment.

**Cost, stated plainly**: exit detection is no longer instantaneous. It is bounded by the loop tick,
which R6 sets to 5 seconds — well inside human perception for "I typed `/exit` and the status
changed". FR-032's "told, not polled" is satisfied in the sense that matters: the daemon never
inspects processes or waits on children to discover an exit; the wrapper reports it. Draining a
directory of zero or one small files every five seconds is not polling in the expensive sense the
requirement guards against.

**Idempotency**: a crash between applying a record and unlinking it causes the record to be
reprocessed. Application is therefore written to be idempotent on `(session_id, exit_code)`, and
FR-072 requires this regardless.

**Alternatives considered**:
- **HTTP POST as specified** — rejected above.
- **Unix domain socket with the spool as fallback** — durable *and* instant, but reintroduces a
  server loop and a second code path for the same event, and the second path is the one that would
  rot from disuse. Deferred to milestone 002, which builds an HTTP API anyway and can add a wake-up
  notification cheaply at that point.
- **inotify on the spool directory** — instant and cheap, but Python has no stdlib inotify, so it
  means a dependency or `ctypes` syscall plumbing to save at most five seconds.

**The wrapper stays bash**, seeded from `docs/initial-planning/spike/ra-session-wrapper.sh`, which
already emits the right record shape. It must run in a bare launch environment where no virtualenv
is active, which is a strong argument against rewriting it in Python. Its no-`exec` comment must be
preserved verbatim — it is the reason the wrapper exists.

## R6 — Process and concurrency model

**Decision**: One process, one thread, one `while True` loop with a monotonic multi-rate scheduler.
A 5-second base tick; each periodic job declares its own interval and the loop runs those due.
Jobs: drain the exit spool (every tick), write the heartbeat (every tick), reconcile (default 60s),
poll GitHub (default 60s), dispatch from the ready queue (every tick). No `asyncio`, no threads, no
subprocess pool.

**Rationale**: Principle I names "obvious top-to-bottom control flow" as the default shape and
requires concurrency to be justified against demonstrated need. There is none: the workload is a
handful of HTTP calls and process checks per minute against a global session cap of 2. A single
loop is also what makes the audit log readable, since events appear in causal order with no
interleaving.

The multi-rate design matters and is worth stating: coupling exit-detection latency to the GitHub
poll interval would force a choice between prompt status updates and a sustainable rate-limit
budget. Separating tick from interval removes the tradeoff.

**Blocking risk and its bound**: a long operation on the tick thread stalls everything. This is
acceptable *because* every blocking operation is already required to be bounded — HTTP by FR-008
timeouts, preparation steps by FR-013 timeouts, terminal-socket probes by FR-019. Worktree creation
and preparation are the longest (minutes), and during them the daemon is legitimately busy. The
heartbeat records the current activity so a long-running step is visible rather than looking like a
hang (FR-063).

**Alternatives considered**: `asyncio` — rejected, no I/O concurrency need, and it complicates
subprocess and signal handling. A thread per session — rejected, sessions are not our children;
that is the entire reason the wrapper exists.

## R7 — Effect levels at the boundaries

**Decision**: Five boundary modules under `src/robot_army/boundaries/`, each exposing a concrete
real implementation and a simulated counterpart with the same surface. An `EffectLevel` enum maps to
a table selecting real or simulated per boundary; the daemon is wired once at startup and never
consults the level again.

| Boundary | `plan` | `local` | `no-remote` | `live` |
|---|---|---|---|---|
| `github` (writes) | simulated | simulated | simulated | real |
| `github` (reads/poll) | **real** | **real** | **real** | **real** |
| `git` (worktree, fetch, branch) | simulated | real | real | real |
| `hooks` (preparation steps) | simulated | real | real | real |
| `dtach` + `kitty` + `claude` (session) | simulated | simulated | real | real |

**Rationale**: FR-053 requires enforcement at the boundary rather than at call sites, and gives the
reason — scattered conditionals drift, cannot be tested, and let the simulated path diverge from the
real one. Splitting GitHub reads from GitHub writes is what makes FR-052 ("polling is always real")
structural rather than a rule someone must remember.

Wiring once at startup, rather than passing a level around, is what makes the guarantee hold: code
downstream of the wiring has no access to the level and therefore cannot accidentally branch on it.

**Constitutional tension, addressed**: Principle I forbids "strategy interfaces that have exactly
one caller and no second use in hand". Each of these has exactly two implementations *in this
milestone*, mandated by FR-051 through FR-058 — a present, concrete requirement, not an anticipated
one. The simulated implementations are not scaffolding; they are the feature. See the plan's
Complexity Tracking table.

**What the simulated implementations must do**: log the intended call with full arguments through
the audit log, and return a structurally valid fake handle so downstream code follows the identical
path. They must not silently return `None`, which would let the simulated path diverge at exactly
the place the requirement exists to prevent.

## R8 — Live session identity

**Decision**: Read `~/.claude/sessions/<pid>.json`. Guard on its `version` field against a known-
compatible set; on mismatch, log the anomaly once and degrade to the `/proc` method rather than
crashing. For each entry, confirm liveness by `/proc/<pid>` existing **and** field 22 (`starttime`)
of `/proc/<pid>/stat` matching the recorded `procStart`. Classify by `cwd`: under the worktree root
means orchestrator-owned, anything else means the maintainer's own session. Join orchestrator
entries to database rows on `sessionId`, which the daemon generated.

Fallback method: enumerate `/proc/*/exe` for the Claude binary path; classify by `/proc/<pid>/cwd`.
**Never** match on command lines, under any circumstance.

**Rationale**: Taken directly from M0's measured findings (E5.2, E5.3) — an exact 1:1 registry with
no stale entries in the happy path, giving an exact rather than best-effort join. The `procStart`
check is what makes it safe against PID reuse (FR-038). The command-line prohibition is FR-039, and
M0 records two real incidents behind it: a `pkill -f` that killed the invoking shell, and a
`pgrep -f` that matched a wrapper layer and produced a wrong conclusion.

**Hard rule to encode as a test**: the adjacent `<pid>.<hash>.key` files are mode 0600 and appear to
be session credentials. The daemon must never open, read, copy, or log them.

## R9 — Stopping a single session

**Decision**: At dispatch, after confirmation, read `/proc/<pid>/cgroup` and store the resulting
systemd scope name as an opaque handle. To stop that session and only that session, run
`systemctl --user stop <scope>`.

**Rationale**: M0 finding F18 — kitty places each launched window in its own `kitty-<pid>-<n>.scope`,
so stopping the scope kills exactly that session's process tree, satisfying FR-050. Recording the
name rather than computing it means a change in kitty's naming scheme degrades to a clear failure
instead of stopping the wrong thing.

**Fallback if no scope is found**: signal the wrapper's process group with `SIGTERM`, then `SIGKILL`
after a timeout, and log that the degraded path was taken. Do not silently do nothing.

## R10 — Confirming a dispatch actually happened

**Decision**: After launch returns, poll for up to a bounded confirmation window (default 45s,
configurable) for a registry entry whose `sessionId` equals the UUID the daemon generated. Only then
mark the item `active`. On timeout: `failed`, with the launch arguments, the terminal window state,
and any wrapper log captured. Launch with the terminal's hold-open behaviour enabled so a failed
window remains readable rather than vanishing.

**Rationale**: FR-025, resting on M0's F16 — the launch call returned success and a valid window id
three separate times when no session had started, with no diagnostic anywhere. The same check also
catches F19's silent degradation, where a session ran and exited zero but inherited an environment
marker that disabled transcript recording, making it permanently unresumable. That is why this check
is load-bearing rather than belt-and-braces: it is the only observation that distinguishes a healthy
session from a convincing imitation of one.

Corollary encoded as a requirement elsewhere: if the registry entry appears but carries a different
`sessionId` than requested, that is an anomaly (FR-065), not a success.

## R11 — Session launch environment

**Decision**: Pass every value the session needs explicitly via the terminal's `--env` mechanism.
Always pass `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`. Before launch, read the terminal daemon's own
environment, and if any `CLAUDE_CODE_*` variable that would degrade the session is present, log it
as a startup warning naming the variable.

**Rationale**: M0 finding F19 — sessions inherit the *terminal daemon's* environment, not the
daemon's, and a stray `CLAUDE_CODE_CHILD_SESSION=1` silently disabled transcript saving. FR-022
turns this into a requirement. The defensive variable is cheap; the detection turns a silent,
subtle, unresumable-session failure into a legible message at startup.

## R12 — Repository trust and committed-permission fingerprint

**Decision**: At dispatch, read `~/.claude.json` and require
`projects["<primary clone path>"].hasTrustDialogAccepted` to be true; if not, fail the item with a
message naming the path and the one-time action needed. Separately, compute a SHA-256 over the
contents of `.claude/settings.json` and `.claude/settings.local.json` **as they exist at the base
branch tip in git** (`git show <base-ref>:<path>`), not as they exist in a working tree, and compare
to the fingerprint recorded at onboarding. A mismatch, or a newly appearing file, blocks dispatch
pending explicit re-approval.

**Rationale**: FR-003 and FR-004. Trust is keyed on the primary clone, not the worktree (M0 E1.5),
so a worktree of an untrusted repo would block forever on an invisible modal — precisely the silent
hang the design exists to avoid. The fingerprint closes M0's F9 hole: the trust dialog also accepts
whatever tool permissions a repo has *committed*, so on a whitelisted repo the maintainer does not
control, anyone with commit access can pre-approve tools that a dispatched session honours silently.
Reading from the git object rather than the filesystem is the correct nuance — what matters is what
a freshly created worktree will contain, and M0 measured the exposure as small and checkable
(1 committed `settings.local.json` and 4 committed `.claude/settings.json` across 294 repos).

## R13 — Configuration

**Decision**: TOML, read with stdlib `tomllib`, at `~/.config/robot-army/config.toml`. Validated at
startup by explicit functions that raise a single aggregated error listing every problem found, not
just the first. Secrets (the GitHub token) read from environment variables, with a documented
fallback to a mode-0600 file path named in the config — never the token itself in the config file.

**Rationale**: `tomllib` is stdlib and read-only, which is all that is needed since the daemon never
writes its own config. TOML is human-inspectable and comment-friendly, satisfying the Operating
Constraints preference. Aggregated validation errors matter for a config with per-repository
sections: fixing one typo per restart cycle is a bad experience at 2am.

**Alternatives considered**: YAML (the planning document's sketch uses it) — rejected, it needs a
dependency and its type coercion surprises are a poor fit for a file that configures shell commands
and timeouts. JSON — no comments, and this file wants comments.

## R14 — Audit log

**Decision**: JSON Lines, one record per line, to `~/.local/state/robot-army/logs/audit-YYYY-MM-DD.jsonl`,
opened in append mode and flushed per record. Daily files, never deleted automatically. Outward-facing
actions emit **two** correlated records: an `intent` record written and flushed *before* the action,
and an `outcome` record after, sharing an `action_id`.

**Rationale**: Principle III requires a durable append-only record at the time the action occurs,
and the Operating Constraints require irreversible or outward-facing actions to be logged *before*
execution. An append-only log cannot mutate a record after the fact, so "log before" plus "record the
result" necessarily means two records. The pairing is also what makes a crashed action visible: an
`intent` with no `outcome` is exactly the signature of a process killed mid-action, which Principle IV
says must be detectable on the next run.

Daily files with no automatic deletion satisfy "any rotation policy MUST NOT discard records
silently" in the simplest possible way — nothing is discarded, and the maintainer prunes by hand.

**Redaction**: a single choke point applies redaction to every record, keyed on field name. The
GitHub token and anything from an environment dump are redacted. Issue and card bodies are logged in
full, since they are the prompt and reconstruction requires them.

## R15 — Health signal and dead-man's switch

**Decision**: Two parts.
1. The daemon writes `~/.local/state/robot-army/heartbeat.json` atomically on every tick, carrying
   UTC timestamp, PID, effect level, current activity, and cycle counters.
2. `robot-army health` reads it and exits non-zero if the timestamp is older than a configurable
   threshold (default 3× the reconcile interval). With `--notify`, a failing check also POSTs a
   plain JSON body to a configured webhook URL. A documented systemd **user timer** runs
   `robot-army health --notify` every five minutes.

**Rationale**: FR-063 and FR-064. The essential insight is that a dead daemon cannot report its own
death, so the checker must be a separate process — which makes the systemd timer the actual
dead-man's switch and the daemon's heartbeat merely the evidence it reads. This keeps the mechanism
local and dependency-free, satisfies the constitution's requirement that every capability be
reachable from the terminal, and stays vendor-neutral: a generic webhook covers ntfy and Pushover,
both named in planning §14, without either being a dependency.

Note the timer is a *checker*, not the daemon — planning §8's decision that the daemon is started
manually after graphical login is unaffected, because the checker needs no display environment.

**Alternatives considered**: an external uptime-monitoring service the daemon pings — rejected as an
always-on network dependency for core observability, against Principle II. Notifying from inside the
daemon only — rejected, it cannot detect its own death, which is the entire requirement.

## R16 — Filesystem layout

**Decision**:

| Path | Contents |
|---|---|
| `~/.config/robot-army/config.toml` | configuration |
| `~/.local/state/robot-army/state.db` | SQLite database |
| `~/.local/state/robot-army/logs/audit-*.jsonl` | audit log |
| `~/.local/state/robot-army/logs/sessions/<item>.log` | per-session wrapper log |
| `~/.local/state/robot-army/spool/exits/` | exit records awaiting the daemon |
| `~/.local/state/robot-army/heartbeat.json` | liveness evidence |
| `~/.local/state/robot-army/daemon.lock` | single-instance lock |
| `/run/user/1000/robot-army/<item>.sock` | session host sockets |
| `~/GIT-worktrees/<repo>/<slug>/` | isolated checkouts |

XDG variables are honoured when set, with these as the defaults.

**Rationale**: The split is deliberate and load-bearing. Sockets belong under `XDG_RUNTIME_DIR`
because it is tmpfs and cleared on reboot — a dead socket from a previous boot is noise
reconciliation would otherwise have to reason about, and letting the kernel delete them is free.
Everything that must survive a reboot goes under `~/.local/state`, which M0 chose for the same
reason. Worktrees stay at the planning document's `~/GIT-worktrees/` so they sit beside `~/GIT/`
where the maintainer will look for them, and stay out of any backup set aimed at `~/.local`, which
matters given M0's measurement of 499 MB per prepared worktree.

## R17 — Single instance

**Decision**: `fcntl.flock(LOCK_EX | LOCK_NB)` held on `daemon.lock` for the daemon's lifetime.
Failure to acquire exits non-zero naming the holding PID, which is written into the file.

**Rationale**: FR-070. `flock` is released automatically by the kernel when the process dies by any
means including `SIGKILL`, which a PID file alone cannot promise. Read-only CLI commands do not take
the lock; commands that mutate state the daemon owns take it or fail.

## R18 — Branch and worktree naming

**Decision**: Branch `robot-army/issue-<number>-<slug>`, worktree
`~/GIT-worktrees/<repo>/issue-<number>/`. The slug is derived from the issue title: lowercased,
non-alphanumerics collapsed to single hyphens, truncated to 40 characters at a hyphen boundary, and
omitted entirely if it reduces to empty.

**Rationale**: The spec's assumption, made concrete. Keying the worktree directory on the issue
number alone (not the slug) makes the path deterministic and stable if the issue is retitled, which
matters because the path is stored and reused across resume. Keeping the slug in the branch name is
what makes `git branch --list 'robot-army/*'` readable months later.

## R19 — Worker invocation

**Decision**: Launch through the chain M0 verified, with the corrected argument forms:

```
kitty @ --to <discovered-socket> launch --type=tab --hold \
  --cwd <worktree> --title "ra-<item>" --var ra_item=<item> \
  --env ROBOT_ARMY_ITEM=<item> --env CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 \
  -- dtach -A <socket> \
     robot-army-session-wrapper <item> -- \
     claude --session-id <uuid> -n "<name>" --remote-control "<name>" \
            --permission-mode <mode> "<prompt>"
```

Socket discovery by globbing the configured pattern and probing each candidate with
`kitty @ --to <s> ls`, taking whichever answers first.

**Rationale**: Every element is a measured M0 finding rather than a preference. `dtach` takes **no**
`--` separator and rejects one outright (F10); the wrapper needs its own `--`. `--to` is mandatory
because there is no `KITTY_LISTEN_ON` in a service environment. `--var` gives an exact reconciliation
key, where walking `foreground_processes` is ambiguous because `--hold` inserts a wrapper layer that
repeats the whole command in its own argv. `--hold` is what makes a failed launch diagnosable (F11).
Socket discovery by probe rather than a fixed path is required because kitty appends its PID to
`listen_on`, and probing is safe because a dead socket refuses in 14–25 ms.

Both name flags are set because they surface in different places and the auto-derived default is not
identifiable. `--bare` must never be used: it skips the per-repository context that makes these
repositories work well.

**Prompt seeding**: issue title, body, canonical URL, and label list, composed into a single prompt
argument. If the repository has a `.claude/robot-army.md`, its contents are prepended as
dispatch-specific instructions.

## R20 — Testing approach

**Decision**: `pytest`. Boundaries are tested through their simulated implementations plus
fakes injected at construction. Three areas get explicit failure-path and interruption-path tests,
per the constitution's Development Workflow section:
- **The state machine** — every transition, and every illegal transition rejected.
- **Persistence and recovery** — a database interrupted between write and commit; a spool record
  applied twice; a migration interrupted midway.
- **External-input parsing** — the session registry at an unknown `version`, truncated, absent,
  and containing an entry whose `procStart` disagrees with `/proc`; `~/.claude.json` malformed;
  exit records truncated or containing implausible values.

`/proc` and the registry are read through a small indirection so tests can supply fixture
directories rather than mocking the filesystem globally. Real `git` is used against temporary
repositories created in fixtures — git is fast, and mocking it would test the mock.

**Rationale**: The constitution requires unit tests for all new behaviour and additionally requires
failure and interruption tests for exactly these three categories. It explicitly forbids coverage
targets and does not mandate test-first, so the standard is meaningfulness, not ceremony.

---

## Unresolved

None. Every `NEEDS CLARIFICATION` from the Technical Context was resolved above; the spec carried no
`[NEEDS CLARIFICATION]` markers into this phase.

## Deviations from the planning document, collected

| Topic | Planning document | This plan | Reason |
|---|---|---|---|
| Storage engine | MariaDB (§12) | SQLite | Constitution Principle II; settled during `/speckit-specify` |
| Exit reporting | Wrapper POSTs to daemon HTTP API (§9) | Wrapper writes atomic spool file; daemon drains it | A POST to a down daemon loses the record permanently (Principle IV); also removes the listening socket entirely |
| Config format | YAML sketch (§6) | TOML | stdlib `tomllib`, no dependency |
| Persistence abstraction | "thin enough that SQLite is a drop-in" (§12) | Hand-written SQL, one engine | Direction reversed by the storage decision; thinness still honoured |
