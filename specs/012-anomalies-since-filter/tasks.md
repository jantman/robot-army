---

description: "Task list for the --since window on `robot-army anomalies`"
---

# Tasks: A `--since` Window on `anomalies`

**Input**: Design documents from `/specs/012-anomalies-since-filter/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/cli-anomalies.md](./contracts/cli-anomalies.md), [quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** The constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour, and FR-012 enumerates the cases. Test
tasks below are therefore first-class, and the failure paths carry their own tasks because this
code parses external input.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story the task serves (US1, US2)
- Paths are repository-relative from the worktree root

## Path Conventions

Single Python package: `src/robot_army/`, tests in `tests/unit/` and `tests/integration/`,
documentation in `README.md` and `docs/`.

---

## Phase 1: Setup

**Purpose**: Establish the green baseline this change must not disturb. There is no project to
initialize, no dependency to add, and no tooling to configure — the plan adds none.

- [X] T001 Record the baseline by running `uv run pytest` and `uv run ruff check .` from the worktree root; note the passing test count, because SC-003 is a claim that this number only grows and no existing expectation changes

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Put the shared duration parser where two commands can honestly claim to share it.

**⚠️ Blocking**: T003 must land before the US1 filter is written, so the filter is authored
against the parser's final location rather than moved afterwards.

- [X] T002 In `src/robot_army/operations.py`, add a `# -- durations ------` section banner immediately above the existing `# -- anomalies ------` banner, and move the `_DURATION_UNITS` dict and the whole `parse_duration` function into it from under the `# -- log ------` banner — relocation only, with the body, name, signature and docstring unchanged
- [X] T003 Confirm the move changed nothing by running `uv run pytest tests/unit/test_time_record_unchanged.py tests/unit/test_web_log.py tests/unit/test_cli_local_time.py`; these import `parse_duration` by name or exercise `log --since`, so they are the direct evidence the relocation is inert

---

## Phase 3: User Story 1 — Reading only what went wrong recently (Priority: P1)

**Goal**: `robot-army anomalies --since 1h` lists the anomalies detected inside that window and
nothing older, parsing the duration exactly as `log --since` does.

**Independent Test**: Seed anomalies with detection times 10 minutes, 3 hours and 2 days old;
run `anomalies --since 1h`, `--since 1d` and bare; confirm the sets are one, two and three rows
and that `--since 1h --json` reports the same one row.

### Tests for User Story 1

- [X] T004 [P] [US1] Create `tests/unit/test_anomalies_since.py` with a helper that seeds anomalies at controlled detection times — insert via `db.raise_anomaly` then `UPDATE anomalies SET detected_at = ?` to place each row at a known instant, since `raise_anomaly` stamps `utcnow()` itself — using the `conn`, `audit`, `config` and `layout` fixtures from `tests/conftest.py` the way `tests/unit/test_cli_local_time.py` builds an `operations.Context`
- [X] T005 [US1] In `tests/unit/test_anomalies_since.py`, assert the window selects correctly: rows at 10 minutes, 3 hours and 2 days old give one row for `--since 1h`, two for `--since 1d`, and three for `--since 30d` (spec US1 scenarios 1 and 2)
- [X] T006 [US1] In `tests/unit/test_anomalies_since.py`, assert the boundary is inclusive: a row whose `detected_at` sits exactly on the cutoff instant is listed (research.md R3, spec Edge Cases)
- [X] T007 [US1] In `tests/unit/test_anomalies_since.py`, assert every malformed duration returns exit status `EXIT_USAGE` with no rows listed, and that the message is character-identical to what `operations.read_log` returns for the same input — cover `"2 weeks"`, `"1.5h"`, `"10 fortnights"`, `"abc"` and `""` (FR-002, FR-007, spec US1 scenario 6)
- [X] T008 [US1] In `tests/unit/test_anomalies_since.py`, assert `--since` composes with `--all`: with one acknowledged and one unacknowledged row both inside the window, `since` alone returns the unacknowledged one and `since` with `show_all=True` returns both, and neither returns a row outside the window (FR-005, spec US1 scenario 4)
- [X] T009 [US1] In `tests/unit/test_anomalies_since.py`, assert `result.data["anomalies"]` holds exactly the rows the rendered lines named, so the `--json` payload and the human output cannot disagree about the window (FR-008, spec US1 scenario 5)
- [X] T010 [US1] In `tests/unit/test_anomalies_since.py`, assert a row whose stored `detected_at` is unparseable is **listed** under any window rather than dropped, and that no exception escapes (FR-010, research.md R4)
- [X] T011 [US1] In `tests/unit/test_anomalies_since.py`, assert the ordering guard from research.md R5: `anomalies(ctx, since="bogus", acknowledge=<open id>)` returns `EXIT_USAGE` **and** leaves that anomaly still unacknowledged in the database, and that a valid `since` alongside `--acknowledge` still acknowledges, still writes its `anomaly.acknowledge` audit record, and filters the listing that follows (FR-006, FR-007, spec US1 scenario 7)

### Implementation for User Story 1

- [X] T012 [US1] In `src/robot_army/operations.py`, add a module-private helper next to `anomalies()` that judges one row against an optional cutoff — parse `detected_at` with `"%Y-%m-%dT%H:%M:%SZ"` as UTC and return whether it is at or after the cutoff, returning `True` (keep the row) when the parse raises, per the three-case table in [data-model.md](./data-model.md)
- [X] T013 [US1] In `src/robot_army/operations.py`, add `since: str | None = None` to `anomalies()` and parse it at the very top of the function — before the `acknowledge` branch — returning `Result(code=EXIT_USAGE, lines=[str(exc)])` on `ValueError`, mirroring `read_log`; comment why the parse precedes the acknowledgement (research.md R5)
- [X] T014 [US1] In `src/robot_army/operations.py`, apply the helper to the rows returned by `db.list_anomalies` before `result.data` is built, so the rendered lines and the `--json` payload are filtered from the same list; leave `db.list_anomalies` and its call site untouched (research.md R2)
- [X] T015 [US1] In `src/robot_army/cli.py`, register `--since` on the `anomalies` subparser (~line 132) with `default=None, metavar="DURATION", help="e.g. 30s, 10m, 2h, 1d"`, spelled the same as the `log` registration at ~line 125
- [X] T016 [US1] In `src/robot_army/cli.py`, pass `since=args.since` through the `"anomalies"` dispatch lambda (~line 391)
- [X] T017 [US1] Run `uv run pytest tests/unit/test_anomalies_since.py -v` and confirm every US1 test passes

**Checkpoint**: `--since` works end to end. This alone is the feature the issue asked for.

---

## Phase 4: User Story 2 — Trusting the unfiltered view (Priority: P1)

**Goal**: Adding the filter did not change what the reflex reading shows. Nothing here is new
behaviour; the tasks exist because "unchanged" is a claim that has to be checked, not assumed.

**Independent Test**: Run every pre-existing `anomalies` invocation against a fixed database and
confirm the output matches what it produced before this change.

- [X] T018 [P] [US2] In `tests/unit/test_anomalies_since.py`, assert the default is inert: `anomalies(ctx)` with no `since` returns every row `anomalies(ctx)` returned before the change, in the same `detected_at DESC, id DESC` order, with the kinds trailer still last (FR-004, spec US2 scenario 1)
- [X] T019 [P] [US2] In `tests/unit/test_anomalies_since.py`, assert the two empty listings are distinguishable: with no rows at all the message is still exactly `no outstanding anomalies`, while with rows present but none inside the window the message names the window and does **not** contain that string (FR-009, SC-004, spec US2 scenario 2)
- [X] T020 [US2] In `src/robot_army/operations.py`, implement that distinction in `anomalies()` — keep the existing `no outstanding anomalies` line for the unfiltered-empty case and emit a separate line naming the requested window when `since` emptied the listing; the kinds trailer prints in both cases as it does today (FR-009, research.md R6)
- [X] T021 [US2] Confirm the web surface is untouched by running `uv run pytest tests/unit/test_web_views.py tests/unit/test_web_actions.py tests/unit/test_web_routing.py tests/unit/test_status_withheld.py` — `web/pages.py::anomalies_view` calls `operations.anomalies(ctx)` with no `since`, so the default must carry it through unchanged (FR-004, research.md R7, spec US2 scenario 3)

**Checkpoint**: the filter is opt-in and provably cannot hide an anomaly from someone who did
not ask for a window.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T022 [P] In `README.md`, add `--since` to the "When something looks wrong" block (~line 533) beside `uv run robot-army anomalies`, showing the duration forms, so the documented CLI reference describes the flag (FR-011)
- [X] T023 [P] In `src/robot_army/cli.py`, confirm the `anomalies` subparser help text still reads correctly with the new argument and that `uv run robot-army anomalies --help` lists `--since` with its duration examples
- [X] T024 Run `uv run ruff check .` and fix anything it reports; the repository lints at line-length 100 with `C90` max-complexity 15, and `anomalies()` gains branches
- [X] T025 Run the full `uv run pytest` suite and confirm it passes with no existing expectation modified — the constitution's completion gate, and the evidence for SC-003
- [X] T026 Walk [quickstart.md](./quickstart.md) §2 through §4 by hand against a scratch `XDG_STATE_HOME`, in particular comparing the rejection messages of `anomalies --since "2 weeks"` and `log --since "2 weeks"` side by side, since FR-002 is a claim about sameness that only a side-by-side reading actually verifies
- [X] T027 Commit atomically with a message explaining **why** the filter was added — reading "what went wrong in the last hour" previously meant eyeballing timestamps — not merely what changed, per the constitution's Development Workflow

---

## Dependencies & Execution Order

```text
Phase 1 (T001)
    │
    ▼
Phase 2 (T002 → T003)          ← blocking: the parser must be in its final place first
    │
    ▼
Phase 3 — US1 (T004 … T017)    ← the feature; deliverable on its own
    │
    ▼
Phase 4 — US2 (T018 … T021)    ← the guard on US1; T020 is the only new source line here
    │
    ▼
Phase 5 (T022 … T027)
```

**Story dependencies**: US2 is not independent of US1 in the usual sense — it is the boundary
condition on US1 rather than a second increment. Its tests (T018, T019) are meaningful before
US1 lands too: run against the current code they pass, which is exactly what makes them a
regression baseline rather than new assertions written to fit new behaviour.

**Within Phase 3**: T004 must come first (every other test uses its seeding helper). T005–T011
are then independent of each other but all live in one file, so they are sequential in practice
rather than `[P]`. T012–T014 touch `operations.py` and must be sequential; T015–T016 touch
`cli.py` and can proceed in parallel with them.

## Parallel Execution Opportunities

- **T015 + T016** (`src/robot_army/cli.py`) can run alongside **T012–T014** (`src/robot_army/operations.py`) — different files, no shared state.
- **T018 + T019** are independent assertions and can be written in either order.
- **T022 + T023** touch different files and are independent.

Realistically this is a small change and the parallelism is marginal; it is recorded for
completeness, not because the critical path needs it.

## Implementation Strategy

**MVP is User Story 1 (Phases 1–3).** At the T017 checkpoint the issue is satisfied: `--since`
exists, parses like `log`, and filters correctly.

**Do not stop there.** Phase 4 is what keeps the filter from becoming a way to miss an anomaly,
and T020 — the distinguishable empty message — is a real source change, not a test. Shipping
Phase 3 without Phase 4 would leave a filtered empty listing indistinguishable from an all-clear,
which is the one outcome Story 2 exists to prevent.

**Phase 5 is not optional either**: T025 is the constitution's completion gate. The feature is
not done until the full suite passes.

## Task Summary

- **Total**: 27 tasks
- **US1**: 14 (T004–T017) — 8 test tasks, 5 implementation, 1 verification
- **US2**: 4 (T018–T021) — 2 test tasks, 1 implementation, 1 verification
- **Setup / Foundational / Polish**: 9 (T001–T003, T022–T027)
- **Source files touched**: 2 (`src/robot_army/operations.py`, `src/robot_army/cli.py`) plus `README.md`
- **New files**: 1 (`tests/unit/test_anomalies_since.py`)

---

## Implementation Notes

One thing the spec had wrong, found while writing T007 and corrected in `spec.md` and
`contracts/cli-anomalies.md` rather than papered over:

**An empty `--since` is not a rejection.** The spec's US1 scenario 6 listed "an empty value"
among the strings the command must refuse. `parse_duration` does raise on `""` — but
`read_log` guards its call with `if since:`, so `log --since ""` never reaches the parser and
means "no window". FR-002 asks for sameness, so `anomalies` guards identically. The spec now
says so, FR-002 states the requirement in both directions, and
`test_an_empty_since_means_no_window_here_because_it_does_for_the_log` pins the parity.

Two smaller notes:

- **T006 was moved off the command and onto the predicate.** Boundary inclusivity cannot be
  asserted through `anomalies()` at one-second resolution — the cutoff is derived from
  `datetime.now` a moment after the fixture's stamp is built, so the assertion would be a coin
  toss about which side of the second the two calls landed on. `_within_window` takes an
  explicit cutoff and is tested directly, which is the honest unit for this claim.
- **The baseline numbers**: 1625 passing before, 1654 after, 1 skipped in both. No existing
  test or expectation was modified — the evidence for SC-003.
