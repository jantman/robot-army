---

description: "Task list for the lock-aware dead-man's switch"
---

# Tasks: The Dead-Man's Switch Reads the Lock as Well as the Heartbeat

**Input**: Design documents from `specs/20260909-093421-lock-aware-health/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/health-verdict.md](contracts/health-verdict.md),
[quickstart.md](quickstart.md)

**Tests**: included and **not optional here** — the constitution requires unit tests for every
new or changed unit of behaviour, and failure-path tests additionally for anything parsing
external input. The lock probe and the heartbeat parser are both of those.

**Organization**: by user story, in priority order. US1 alone is a complete fix for the reported
defect and is the MVP.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 from [spec.md](spec.md)

## Path Conventions

Single project: `src/robot_army/`, `tests/` at the repository root.

---

## Phase 1: Setup

**Purpose**: know the ground is level before moving anything.

- [X] T001 Record the baseline: `uv run pytest` is green on `robot-army/issue-52-the-dead-man-s-switch-has-a-180s` before any edit. Anything failing after this point is this feature's doing.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the vocabulary the verdict is written in, and the probe that supplies half of it.
Every user story depends on this phase; nothing here changes any surface's output on its own.

- [X] T002 [P] Add the `LockState` StrEnum — `HELD` / `UNHELD` / `UNKNOWN` — to `src/robot_army/health.py`, with a docstring saying why it lives here rather than in `daemon.py` (the import direction; research R1) and why `UNKNOWN` exists at all (a failed probe must not manufacture a death notice; research R2).
- [X] T003 [P] Add the `LockReading` frozen slotted dataclass — `state`, `holder`, `path`, and a `running` property — to `src/robot_army/health.py`, per [data-model.md](data-model.md). Docstring: the holder is `None` unless the lock is held, because an unheld lock file still names the process that exited.
- [X] T004 Add the `HealthState` StrEnum with its seven members and its `label` property to `src/robot_army/health.py`, per [contracts/health-verdict.md](contracts/health-verdict.md). The label is defined once here because three surfaces print it and the whole point is that they cannot differ.
- [X] T005 Extend `HealthReport` in `src/robot_army/health.py`: `state` required and third positional, `lock` keyword-defaulted to `None`, and `to_dict()` emitting both. Update the reports built by hand in `tests/unit/test_health.py` to name the state they mean.
- [X] T006 Add `observe_lock(path) -> LockReading` to `src/robot_army/daemon.py` — open read-only **without `O_CREAT`**, take `LOCK_SH | LOCK_NB`, read the holder from the same descriptor when the lock is held, map `ENOENT` to `UNHELD` and every other failure to `UNKNOWN` — and reimplement `is_locked` as `observe_lock(path).running`. Carry the shared-lock rationale and the 1,558-in-2,400 measurement onto the new function; leave `read_lock_holder` alone.
- [X] T007 [P] Probe tests in `tests/integration/test_single_instance.py`: a held lock reads `HELD` with the holder's pid; a free lock file reads `UNHELD` with no holder; an absent lock file reads `UNHELD` **and is still absent afterwards** (FR-012); an unopenable path reads `UNKNOWN`, patched rather than produced with `chmod 000` because a test that silently passes as root checks nothing (a directory does *not* work: Linux opens and flocks one happily). Confirm the existing `is_locked` tests, including the six-thread concurrency test, pass unchanged.

**Checkpoint**: the probe and the vocabulary exist; no output has changed anywhere.

---

## Phase 3: User Story 1 — a dead daemon is reported dead on the next check (Priority: P1) 🎯 MVP

**Goal**: with the lock released, the heartbeat's age stops deciding anything.

**Independent test**: write a heartbeat dated now, hold no lock, run `robot-army health`. It must
say the daemon is gone and exit 4, and give the same answer at 1s, 30s, 179s and 300s.

- [X] T008 [US1] Give `check` its `lock: LockReading | None = None` parameter in `src/robot_army/health.py` and implement the derivation rows that story 1 needs — row 4 (`died`, above the freshness test), row 5 (`ok`), and the existing absent/unreadable/stale rows carried over with their `state` and `lock` now set. Every returned report names its state; none of the existing sentences changes.
- [X] T009 [US1] In `operations.health_check` (`src/robot_army/operations.py`), observe the lock with `daemon_mod.observe_lock(ctx.layout.lock_path)`, pass it to `check`, and print `f"{report.state.label}: {report.reason}"` in place of the `"ok: "` / `"STALE: "` prefix. The exit code stays `EXIT_OK` / `EXIT_CHECK_FAILED`.
- [X] T010 [P] [US1] Tests in `tests/unit/test_health.py`: `died` at four heartbeat ages spanning the threshold, all with the same verdict and reason shape (SC-002); `ok` unchanged under a held lock with a fresh heartbeat, asserting the line is byte-for-byte today's (SC-004); a stale heartbeat under a held lock still failing (SC-003); `never_started` unchanged; and `health_check`'s exit code and first line for each.

**Checkpoint**: the reported defect is fixed and the switch is honest. Everything below sharpens
what it says.

---

## Phase 4: User Story 2 — "died" and "hung" are told apart (Priority: P2)

**Goal**: the failed check tells the reader whether to restart it or look at it first, in the
line, in the JSON, and in the alert.

**Independent test**: produce each of the six failing states and confirm each yields a distinct
`state` value, a sentence that says which, and an alert body carrying the same.

- [X] T011 [US2] Implement the remaining derivation rows in `src/robot_army/health.py` — `hung` (row 6, held with a stale beat from the holder), `starting` (rows 1 and 7, held with no beat of its own yet) — with the exact sentences from [contracts/health-verdict.md](contracts/health-verdict.md). The pid comparison is text-against-text, as `published_cap` already does it, and a holder that cannot be read is a doubt rather than a match.
- [X] T012 [US2] Implement the partial-evidence clauses in `src/robot_army/health.py`: the `UNKNOWN` suffix on both the healthy and the stale sentence (FR-013), and the lock clause appended to the `unreadable` sentences. A verdict reached without the lock must never read like one reached with it.
- [X] T013 [US2] Carry the state into the alert in `src/robot_army/health.py`'s `alert_fields` — `state` beside the existing `healthy` and `age_seconds`, title and message unchanged — so every configured channel and the `health.notify` audit record get the sharper reason with no work at the call site.
- [X] T014 [P] [US2] Tests in `tests/unit/test_health.py`: the full lock × heartbeat matrix from the contract, one case per row, asserting `state`, `healthy` and the distinguishing phrase; `hung` versus `starting` decided by the pid comparison in both directions; a **fresh** beat with a mismatched pid staying `ok` (SC-008); `to_dict` carrying `state` and `lock`, with `lock` `None` when no reading was supplied and `"unheld"` when one was (FR-009); `alert_fields` carrying the state.

**Checkpoint**: the switch says which failure it found, everywhere it speaks.

---

## Phase 5: User Story 3 — two surfaces looking at one machine give one answer (Priority: P3)

**Goal**: `status` and the web reach the verdict the same way `health` does, from a reading each
of them already takes.

**Independent test**: a fresh heartbeat with no lock held; none of the three surfaces reports the
daemon as healthy.

- [X] T015 [US3] In `operations.status` (`src/robot_army/operations.py`), observe the lock, pass it to `check`, and render `f"health       : {report.state.label} — {report.reason}"`. This is the only line `status` prints about the daemon, which is why it may not say `ok` beside a dead one.
- [X] T016 [US3] In `web.handle` (`src/robot_army/web/server.py`), replace the request's `is_locked` + `read_lock_holder` pair with one `observe_lock`, pass the reading to `check`, and feed `published_cap` from `lock.running` and `lock.holder`. One observation per request, which is what that function's existing comment already promises.
- [X] T017 [US3] Update the direct-caller fallbacks that take their own readings — `pages.chrome`, `pages.effect_mismatch` (`src/robot_army/web/pages.py`) and `effective_level` (`src/robot_army/web/server.py`) — to observe the lock and pass it to `check`, and add `state` to the chrome payload beside the `healthy` and `reason` it already publishes.
- [X] T018 [US3] In `src/robot_army/web/html.py`, render the running-daemon suffix from the report's label instead of the unconditional `— heartbeat STALE`, so a wedged daemon reads `HUNG` and a restarting one reads `STARTING`. The `DAEMON NOT RUNNING` banner is derived from `running` and does not change.
- [X] T019 [P] [US3] Tests: `status` reporting `DIED` rather than `ok` beside a dead daemon and carrying `state` in its `--json` `health` key, in `tests/unit/test_health.py` or the existing status tests; the chrome agreeing with `check` on one instant, and the banner unchanged for a running daemon, in `tests/unit/test_web_views.py`.

**Checkpoint**: the incident in the issue cannot be reproduced in either direction.

---

## Phase 6: Polish & cross-cutting

- [ ] T020 Update the "Noticing it has died" section of `docs/guide/operating.md`: what the switch consults now, the table of verdicts and what each one asks the reader to do, and the honest statement that the timer's five-minute cadence is a separate latency this does not change. Per `CLAUDE.md`'s table this is the page — health and recovery live on it.
- [ ] T021 Confirm nothing else in `docs/guide/` needs a change and say why in the commit: no config key moved, so `share/config.example.toml` needs no regeneration and `configuration.md` no new entry; no file shape changed, so `state.md` stands; no new audit action and no record shape change, so `audit-log.md` stands.
- [ ] T022 Run `uv run pytest` and the repository's lint/format as configured; walk [quickstart.md](quickstart.md) scenarios 1–8 and confirm each states what it claims.

---

## Dependencies

```
Phase 1 (T001)
   └─► Phase 2 foundational (T002…T007)
          ├─► US1 (T008 → T009, T010)          ← MVP, delivers the fix
          │      └─► US2 (T011, T012, T013 → T014)
          │             └─► US3 (T015, T016, T017, T018 → T019)
          └─► Phase 6 (T020…T022) after the stories it documents
```

- **T002–T005 before T008**: `check` cannot set a state that does not exist.
- **T006 before T009**: nothing to observe with until the probe exists.
- **US2 after US1** by file, not by logic: T011 and T012 edit the same function T008 does.
- **US3 after US2** for the same reason on the label: T015 and T018 render what T004 defines and
  T011 populates. US3 is otherwise independently droppable — it is the deliberate widening of the
  issue's scope, flagged as such in the spec and its checklist.

## Parallel opportunities

- T002 and T003 are separate definitions in one file; do them together and land them as one edit.
- T007 (integration, the probe) is independent of T010 and T014 (unit, the verdict) once T006 and
  T008 respectively are in.
- Within Phase 5, T015 (terminal) and T016–T018 (web) touch different files and can proceed in
  parallel once T011 is in; T019 covers both and comes last.

## Implementation strategy

**MVP is US1.** T001–T010 is the whole of the reported bug: a dead daemon reported dead on the
next check rather than one staleness threshold later. It is committable and shippable alone, and
if the rest were abandoned the switch would already be honest.

**US2 is the part worth getting right rather than getting done.** A message that said "died" of a
wedged process would send someone to restart the one thing whose stack was the only evidence
worth having.

**US3 is the smallest and the most easily deferred** — and leaving it out reproduces the reported
bug with the roles reversed, a `health` that knows and a `status` that does not.

Commits stay atomic and explain why: the vocabulary and the probe, the verdict, the sentences,
each surface, the documentation.
