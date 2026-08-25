# Implementation Plan: Concurrency & Polish

**Branch**: `004-concurrency-polish` | **Date**: 2026-08-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-concurrency-polish/spec.md`

## Summary

The daemon stops assuming it is the only thing running on the machine. The cap starts counting the
author's own Claude sessions, repositories get a cap and an order of their own, the queue says where
an item sits and why it is not moving, finished worktrees are reclaimed, and something is said out
loud when a run starts, finishes, or fails.

No new external system, no new kind of work, no new process. Two new modules, one migration, one new
boundary, three new configuration sections, and a change of representation for one audit record.

Five decisions shape everything else:

1. **Capacity and order are each computed by exactly one function, consumed by both the dispatcher
   and every surface** (R1, R8). SC-006 demands the queue's "next" be the dispatcher's "next". That is
   only honest if they are the same code. A comment in `web/pages.py` currently asserts this
   agreement; this milestone is what would make that comment false.
2. **The global count is the union by session id of live registry entries and our own un-registered
   rows** (R2, R3). The registry alone under-counts a dispatch in flight, because a `starting` session
   has no registry file yet — and an under-count is the one capacity error that causes harm.
3. **Every unresolved doubt about capacity resolves to "hold"** (R4). A visible stall beats an
   invisible over-dispatch, so `sessions.scan` learns to distinguish a *missing* registry directory
   from an empty one, which is the only failure that currently reads as free capacity.
4. **The worktree guard and the branch guard are different guards** (R12). Git refuses to remove a
   dirty worktree for free. Git's branch guard, by contrast, is the wrong guard — `-d` refuses the
   normal case here and would accumulate `robot-army/*` branches forever — so containment is checked
   explicitly against the remote, and `force` means "a stronger guard already passed".
5. **`commits_ahead` returns 0 when git fails, and that is safe for its current caller and unsafe for
   a delete decision** (R11). The signature becomes `int | None` rather than leaving the trap armed
   for the next caller.

## Technical Context

**Language/Version**: Python 3.14 (unchanged; `requires-python = ">=3.14"`)

**Primary Dependencies**: none added. `httpx` remains the sole runtime dependency; the notifier reuses
the bounded POST `health.notify` already performs (R14). Nothing in this milestone talks to a system
milestone 003 did not already talk to.

**Storage**: the existing SQLite database, plus migration **004** adding three nullable columns to
`work_items` (`cleanup_state`, `cleanup_reason`, `cleaned_at`). No new table (R13).

**Testing**: pytest. Unit tests for the capacity union across the launch window, the degraded and
unobservable paths, both ordering modes and their tie-breaks, hold-reason precedence, the branch
containment decision including its `None` case, config validation of every new key, and the per-cycle
notification bound. Integration tests drive `select_and_dispatch` against a fake registry with
out-of-band entries present, and drive cleanup against a repository fixture with a dirty worktree, an
unpushed branch, a live session, and an externally deleted directory. One test must be marked as
requiring a real registry and skipped in CI, for the reason the roadmap already records about CI's
ceiling.

**Target Platform**: the author's Linux desktop, unchanged.

**Project Type**: single project. Two new modules (`capacity.py`, `ordering.py`), one new boundary
(`Notifier`), one reconciliation pass, one migration, two CLI verbs, one changed web view.

**Performance Goals**: a capacity snapshot is a directory glob of a few files plus one `/proc/<pid>/stat`
read each — sub-millisecond, and it runs once per dispatch decision rather than once per tick. Cleanup
adds one `git fetch` and at most four short git invocations per item, and only for items whose issue has
just closed.

**Constraints**: an under-count of live sessions must be impossible by construction, not by care (R3,
R4); no control path may obtain a handle to a session the system did not start (R5); no branch may be
deleted whose commits are not provably on the remote (R12); no network call may occur inside a write
transaction (R14); the milestone-003 behaviour must be recoverable by configuration alone (FR-046).

**Scale/Scope**: one machine, a global cap in single figures, a few dozen repositories, a handful of
eligible items at a time. Every ordering and capacity computation is over a list short enough that its
cost is irrelevant and its correctness is everything.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 design. Re-check result at the bottom.*

### I. Simplicity First (YAGNI & KISS)

| Check | Result |
|---|---|
| New third-party dependencies justified by work removed | **Pass** — none added (R14) |
| No speculative generality | **Pass** — the `Notifier` seam has exactly two implementations, both required by FR-040, the same standing the existing eight have. Two ordering modes exist because FR-016 names two; there is no mode registry and no plugin point |
| Single process, plain files, obvious control flow the default | **Pass** — cleanup is a pass inside the existing reconciliation, not a new job (R10); notifications are four call sites, not a queue (R14, R15) |
| Fewest moving parts wins between two adequate designs | **Pass with two items tracked** — the `Notifier` boundary and the `capacity.py`/`ordering.py` split, both justified in Complexity Tracking |

The temptation worth naming is a scheduler. Caps, priorities, and a queue position invite one — a
component that owns the run queue, holds its own state, and decides. Nothing here needs it: the
"queue" is the set of `ready` rows the database already holds, the order is a sort key over that set
(R7), and the position is a list index (R8). No state is added to hold a queue, because there is no
queue object. FR-021's refusal of aging is the same judgement applied to the same temptation from a
different direction.

### II. Single-User, Local-First

| Check | Result |
|---|---|
| No authentication, authorization, accounts, or roles built | **Pass** — nothing here has a notion of who is acting; the operating-system user remains the boundary |
| State on the local filesystem, no hosted service required | **Pass** — same database. Capacity is read from `~/.claude/sessions` and `/proc`, both local; notifications are optional and their absence changes nothing |
| Secrets from environment or git-ignored files, never in logs | **Pass** — no new secret. FR-037's requirement that notifications carry none is met by composing them from item identifiers and state names only, with a test |
| No public IP, reverse proxy, or deployment infrastructure assumed | **Pass** — outbound HTTPS to one optional webhook, exactly as the health signal already does |

The interesting one is FR-006. Counting the author's own sessions means observing processes the
system does not own, which is close to a boundary worth being careful about. R5 settles it
structurally: the snapshot carries session ids for our own sessions and a bare integer for everyone
else's, so there is no handle to misuse. The system may notice the author's work. It may not touch
it.

### III. Total Accountability

| Check | Result |
|---|---|
| Every action that changes state outside the process logged when it occurs | **Pass** — worktree removal, branch deletion, and every notification send are `audit.action` intent/outcome pairs written before the call; the simulated implementations write the same records marked simulated |
| Records carry timestamp, component, action, target, params, outcome | **Pass** — the new actions are enumerated in data-model.md |
| No silent failure | **Pass** — a refused removal, a retained branch, an unobservable capacity, and a failed notification each produce a record naming the cause; none is converted into a benign-looking success |
| Documented exceptions enumerated | **One, below** |

**Enumerated Principle III item — the capacity hold record (R16).** `dispatch.at_capacity` is written
when the hold's signature changes and once when it ends, rather than once per pass. This is a
documented summarisation under the principle's retention clause, not an exception to it: the fact of
the hold, its cause, its counts, its start, its end, and how many passes it spanned are all recorded.
What is not written is 17,280 identical records a day, which would bury the records that carry
information rather than adding any. The same judgement is already embodied in `raise_anomaly`'s
partial unique index.

**A genuine gap, named as FR-035 requires.** A notification that is never attempted because the
process died between a state transition and the send is not recorded as a missed notification. The
state change itself is fully recorded, so nothing about what the system *did* is unreconstructable;
what is lost is the knowledge that the author was not told. Closing it would require a durable
outbound queue, which R14 and the spec both reject as disproportionate to a stretch feature.

### IV. Interruption Tolerance

| Check | Result |
|---|---|
| Atomic writes to persistent state | **Pass** — every mutation reuses `db.transaction` (`BEGIN IMMEDIATE`); migration 004 advances `user_version` as its last statement, as the ladder requires |
| Restartable, idempotent, incomplete work detected | **Pass** — data-model.md's interruption table has a row per kill point. Cleanup's two steps are separately recorded, so an interruption between them is visible as `branch_retained` and resolved on the next pass rather than being ambiguous |
| Explicit timeouts and bounded retries on every network call | **Pass** — the notifier reuses the health POST's explicit timeout; the cleanup fetch reuses `VersionControl.fetch`'s bounded policy. No new unbounded call |
| Precautions reasonable, not extreme | **Pass** — no queue, no journal, no watermark. The in-process state added (the hold signature, the per-cycle counter) is deliberately not durable, because losing it costs one extra record and a handful of extra messages |

The kill point worth naming: an interruption between `git worktree remove` succeeding and
`git branch -d` running leaves a removed worktree and a live branch. `cleanup_state` records the
partial outcome, and the next pass completes it. Critically, no network call and no subprocess runs
inside a transaction, so a kill can never leave a half-written row (R14).

### V. Public Code, Unsupported Project

| Check | Result |
|---|---|
| No credentials, personal data, private hostnames, or internal addresses committed | **Pass** — configuration examples use placeholder repository keys; the webhook URL is configuration, never committed |
| No stable public API, deprecation cycle, or migration shim maintained | **Pass** — `VersionControl.commits_ahead` changes signature outright (R11), with its one caller updated in the same change. No shim, no overload, no deprecation window |
| Documentation for the author's future self | **Pass** — quickstart.md is the runnable verification; the new configuration keys and the cleanup guards are documented in `contracts/config.md` and `contracts/cleanup.md` |
| No packaging or release pipeline built | **Pass** — nothing added |

### Operating Constraints

| Check | Result |
|---|---|
| Every capability reachable and observable from the terminal | **Pass** — `robot-army capacity` and `robot-army cleanup` are added, and `robot-army status` gains the capacity and queue summary. The web view is a second door onto both (FR-044) |
| Commands exit non-zero on failure | **Pass** — FR-045; the existing `Result` carries the code |
| Persistent data in plain text, structured line formats, or SQLite | **Pass** — three SQLite columns |
| Log location and record format documented, review path documented | **Pass** — no new log; the new actions join the existing JSONL and are readable with `robot-army log` |
| Irreversible or outward-facing actions logged before execution and not reachable by default | **Pass — and this is the milestone's sharpest constitutional constraint.** Worktree and branch removal are irreversible and outward-facing. Both are logged before the attempt (FR-028), and `[cleanup] on_issue_close` defaults to `false` (FR-022, R10). Notifications are outward-facing and default to sending nothing (FR-033) |

## Project Structure

### Documentation (this feature)

```text
specs/004-concurrency-polish/
├── plan.md              # This file
├── research.md          # Phase 0 output — R1..R17
├── data-model.md        # Phase 1 output — schema delta, entities, interruption table
├── quickstart.md        # Phase 1 output — runnable verification
├── contracts/
│   ├── dispatch-policy.md    # capacity snapshot, ordering, hold reasons
│   ├── cleanup.md            # the two guards, the two steps, the outcomes
│   ├── notifications.md      # the Notifier boundary and the event shape
│   └── config.md             # every new key, its default, and its validation
├── checklists/
│   └── requirements.md  # written by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/robot_army/
├── capacity.py            # NEW — CapacitySnapshot and snapshot() (R1..R5)
├── ordering.py            # NEW — order_key, plan(), QueueEntry, HoldReason (R7..R9)
├── notifications.py       # NEW — NotificationEvent, emit(), per-cycle bound (R14, R15)
├── cleanup.py             # NEW — the two guards and the two steps (R10, R12, R13)
├── sessions.py            # CHANGED — RegistryScan.directory_missing (R4)
├── dispatch.py            # CHANGED — gate on capacity, walk ordering.plan, R16 record
├── reconcile.py           # CHANGED — the cleanup pass after _resolve_closed_issues
├── config.py              # CHANGED — [dispatch], [cleanup], [notifications], two repo keys
├── migrations.py          # CHANGED — migration 004
├── db.py                  # CHANGED — cleanup column accessors; count_live_sessions retired
├── effects.py             # CHANGED — REAL_AT["notifier"], wiring
├── operations.py          # CHANGED — capacity(), cleanup_now(), status gains the summary
├── cli.py                 # CHANGED — `capacity`, `cleanup` verbs
├── worktree.py            # CHANGED — condition() maps commits_ahead None -> 0 (R11)
├── health.py              # CHANGED — POST transport extracted for reuse
├── boundaries/
│   ├── __init__.py        # CHANGED — Notifier protocol, commits_ahead -> int | None
│   ├── git.py             # CHANGED — commits_ahead returns None on failure
│   └── notifier.py        # NEW — real and simulated Notifier
└── web/pages.py           # CHANGED — queue_view renders ordering.plan; capacity in chrome

tests/
├── unit/                  # capacity, ordering, cleanup guards, config, notifications
└── integration/           # dispatch under caps; cleanup against a repo fixture
```

**Structure Decision**: unchanged from milestones 001–003 — a single flat package under
`src/robot_army/`, with boundaries in `boundaries/` and the web front end in `web/`. The four new
modules are peers of the existing service modules, not a new layer. `capacity.py` and `ordering.py`
are deliberately separate: one answers "how full is the machine?" and the other "what is next?", they
have different inputs (the machine versus the configuration), and only the second depends on the
first.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| A ninth boundary (`Notifier`) for one optional feature | FR-040 requires sends to be simulated below the `live` effect level, and `effects.py` is the only module permitted to know an effect level exists | Calling `health.notify` directly from four service modules would put an effect-level check back at a call site — the exact pattern milestone 001 built `effects.py` to make impossible, and the one a test (T147) greps for |
| Two new modules where one would do | `capacity.py` observes the machine; `ordering.py` applies configuration. Their tests need entirely different fixtures — a fake `/proc` and registry versus a config object — and only one direction of dependency exists | A single `dispatch_policy.py` would make every ordering test construct a fake process tree it does not care about, and would hide that the capacity half is reusable by surfaces that never order anything |
| Changing `VersionControl.commits_ahead`'s signature (R11) | Its `return 0` on failure means "no information" to its current caller and "safe to delete" to the new one. The same value with opposite meanings, invisible at the call site | Adding a second method computing the same `rev-list` under a different name leaves the trap armed for whoever calls the original next. Principle V explicitly permits breaking changes that serve the single user |

## Post-Design Constitution Re-Check

Re-run after Phase 1 produced data-model.md, the four contracts, and quickstart.md.

**No gate moved from pass to fail, and one design change was made to keep a gate passing.** The first
sketch of notifications hooked `states.transition()` — a single gate, structurally impossible to
forget, and exactly the shape this codebase prefers. Designing the interruption table (data-model.md)
showed it would place an HTTP POST inside `BEGIN IMMEDIATE`, so a slow webhook would hold a write
transaction open against Principle IV's atomicity rule and against plain sense. Four explicit call
sites outside the transaction replaced it (R14). This is worth recording because the rejected design
was the more elegant one; it was rejected on a constraint the elegance concealed.

Three items are worth restating after design rather than before:

- **Principle I held under the pressure it was most likely to fail.** Designing hold reasons (R9) and
  queue positions (R8) invited a queue object with its own state and its own lifecycle. The finished
  design adds no state at all for either: the order is a sort key, the position is a list index, and
  the hold reason is computed at the moment it is displayed. `ordering.plan()` is a pure function of
  the database, the configuration, and a capacity snapshot.
- **Principle III's enumerated item shrank rather than grew.** R16's summarisation is a change of
  representation with every fact preserved. The one genuine gap — a notification lost to a crash
  between transition and send — is named above and is the direct, accepted cost of refusing a durable
  outbound queue.
- **The Operating Constraints' irreversibility rule is what set two defaults.** Cleanup defaults to
  off and notifications default to sending nothing, not as caution but because the constitution
  requires irreversible and outward-facing actions to be unreachable by default. Both are one
  configuration line away, which is what FR-046 means by the milestone-003 behaviour being
  recoverable.

**Gate result: pass.** The three Complexity Tracking entries stand as justified; no fourth appeared
during design.
