---

description: "Task list for 010 — the CLI and web UI show times in the host's local timezone"
---

# Tasks: Times Are Read in the Local Timezone

**Input**: Design documents from `/specs/010-local-timezone-display/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/time-display.md](contracts/time-display.md),
[quickstart.md](quickstart.md)

**Tests**: **required, not optional.** The constitution's Development Workflow section states
that every new or changed unit of behaviour MUST ship with unit tests and that the full suite
MUST pass before the feature is complete. It also states that test-first is *not* mandatory, so
the tasks below place each test beside the change it covers rather than ahead of it.

**Tests carry unusual weight in this feature.** Research [R7](research.md) established that no
existing test asserts a UTC stamp in rendered output — so nothing in the current suite would
catch a display site left behind. The sixteen sites enumerated in
[contracts/time-display.md](contracts/time-display.md) are the definition of "every surface"
for FR-005 and SC-001, and the per-site tests are the only thing that makes that claim
checkable rather than aspirational.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Every task names the exact file it touches

## Path Conventions

Single Python package at the repository root: `src/robot_army/`, `tests/unit/`. One new source
file and three new test files; no new directory.

**A note on `[P]` in this feature.** All ten terminal sites live in one file, so US1's
implementation tasks are strictly sequential despite being independent in meaning. The web
splits across two files, so US2 has real parallelism. Marking US1's tasks `[P]` would be
convenient and wrong.

---

## Phase 1: Setup

**Purpose**: establish the baseline that this feature's central safety claim — "the record did
not move" — is measured against.

- [X] T001 Record the green baseline: run `uv run pytest` from the repository root and confirm the suite passes before any edit, so a later failure is attributable to this feature
- [X] T002 [P] Confirm [R7](research.md)'s sweep still holds by searching `tests/` for any assertion that a UTC stamp appears in rendered output (`Result.lines` or HTML body); if one has been added since, add it to [contracts/time-display.md](contracts/time-display.md) §3 before changing anything

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one conversion function and the ability to test it under a pinned zone. Both
user stories call `timefmt.local()`, so nothing else can start until this exists.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Add a timezone-pinning fixture to `tests/conftest.py` that sets `TZ`, calls `time.tzset()`, yields, then restores — restoring an *absent* `TZ` as distinct from an empty one, since `TZ=""` means UTC while unset means `/etc/localtime` ([R6](research.md))
- [X] T004 [P] Create `src/robot_army/timefmt.py` with `parse_stamp(stamp) -> datetime | None` and `local(stamp) -> str | None` per [contracts/time-display.md](contracts/time-display.md) §1, formatting with `%Y-%m-%d %H:%M:%S %:z` and using `datetime.astimezone()` with no argument
- [X] T005 Create `tests/unit/test_timefmt.py` covering every row of the contract's input/output table: the three zone conversions, the unresolvable-zone fallback to `+00:00`, `None`, `""`, a non-timestamp string returned verbatim, and a stamp missing its `Z` (depends on T003, T004)
- [X] T006 Add failure-path and edge tests to `tests/unit/test_timefmt.py`: assert `local()` never raises for any string input, assert a January stamp displayed under a summer clock carries January's offset, and assert the DST fold renders `2026-11-01T05:00:00Z` and `2026-11-01T06:00:00Z` as the same wall clock distinguished only by `-04:00` and `-05:00` (depends on T005)

**Checkpoint**: the conversion exists, is pinned by tests, and both user stories can now proceed
in parallel.

---

## Phase 3: User Story 1 — Reading a time in the terminal (Priority: P1) 🎯 MVP

**Goal**: every timestamp `robot-army` prints for a person reads in the host's zone, labelled.

**Independent Test**: run `TZ=America/New_York robot-army status`, `show`, `anomalies`, and
`log` against a database holding `2026-08-30T01:31:07Z` and confirm each prints
`2026-08-29 21:31:07 -04:00` — a different calendar day from the stored value, which is the
clearest possible evidence conversion happened. Quickstart scenario 1.

**All five implementation tasks touch `src/robot_army/operations.py` and are therefore
sequential.** Each converts only `Result.lines`; none may touch `Result.data`.

- [X] T007 [US1] Import `timefmt` in `src/robot_army/operations.py` and convert the two `status` sites — the `PAUSED since` line at line 296 (C1) and the anomaly line at line 364 (C2)
- [X] T008 [US1] Convert the four `show` sites in `src/robot_army/operations.py`: the Spec Kit `since` at line 549 (C3), `cleaned at` at line 639 (C4), the history loop at line 646 (C5), and the session row at line 659 (C6) — putting the conversion in the loop at 646, **not** in `_history()`, which also feeds `result.data["history"]` at line 614
- [X] T009 [US1] Convert the two `pause` sites in `src/robot_army/operations.py` at lines 1649 (C7) and 1656 (C8)
- [X] T010 [US1] Convert the `anomalies` site in `src/robot_army/operations.py` at line 2200 (C9)
- [X] T011 [US1] Convert `_format_record` in `src/robot_army/operations.py` at line 2473 (C10), which serves both `log` and `log --follow`; leave `read_log`'s `result.data` holding the raw records
- [X] T012 [US1] Create `tests/unit/test_cli_local_time.py` with one test per site C1 through C10, each seeding a known stored instant, running under the pinned-zone fixture, and asserting the local rendering appears in `Result.lines` and the raw `Z` form does not
- [X] T013 [US1] Add a test to `tests/unit/test_cli_local_time.py` asserting that a stored stamp of `"not a timestamp"` reaches `show`'s output verbatim and the command still exits zero (FR-015), and that an absent stamp still renders the existing absent-value marker (FR-016)

**Checkpoint**: the terminal is fully converted and independently testable. This is a shippable
MVP — the web is untouched and still correct, merely still in UTC.

---

## Phase 4: User Story 2 — Reading a time on the phone (Priority: P2)

**Goal**: every absolute timestamp the web interface renders reads in the host's zone,
labelled, with the relative age beside it unchanged.

**Independent Test**: serve the interface under a pinned zone against the same seeded instant,
request every view, and confirm no raw `…Z` stamp survives anywhere in the rendered HTML while
every JSON response still carries `…Z`. Quickstart scenarios 2 and 4.

- [X] T014 [US2] Delete the private `_parse` from `src/robot_army/web/pages.py` and point `age_seconds` at `timefmt.parse_stamp`, taking the number of stamp-parsing implementations in display code from two to one ([R4](research.md)); no behaviour changes and the existing web suite must still pass
- [X] T015 [US2] Convert `when()` in `src/robot_army/web/pages.py` at line 106 (W1) so it renders `2026-08-29 21:31:07 -04:00 (3h 12m ago)`, leaving `human_age` and `age_seconds` computing from the stored UTC value (FR-006); this one edit covers all seven of its call sites (depends on T014, same file)
- [X] T016 [US2] Convert the log view's record `ts` in `src/robot_army/web/pages.py` at line 1738 (W2), which is not routed through `when()` because a log record carries no relative age (depends on T015, same file)
- [X] T017 [P] [US2] Convert the two `src/robot_army/web/html.py` sites — the `DISPATCH PAUSED since` pill at line 302 (W3) and the `rendered …` footer at line 440 (W4) — reading from the chrome dict but converting **at render**, leaving the dict itself UTC ([R3](research.md)); parallel with T014–T016, different file
- [X] T018 [US2] Create `tests/unit/test_web_local_time.py` asserting, under the pinned-zone fixture, that each of W1's seven call sites plus W2, W3 and W4 renders the local form, and that no `T\d\d:\d\d:\d\dZ` pattern survives anywhere in the HTML of `/active`, `/queue`, `/interrupted`, `/anomalies` and `/log`
- [X] T019 [US2] Add the R3 trap test to `tests/unit/test_web_local_time.py`: request each view with `Accept: application/json` under a non-UTC zone and assert `rendered_at`, `dispatch_paused_at` and every row's `*_at` still end in `Z` — this is the specific regression T017 could introduce
- [X] T020 [P] [US2] Add a test to `tests/unit/test_web_local_time.py` confirming `app.js` is unchanged and still derives its footer age from `Date.now()` at load rather than parsing a rendered stamp, so the format change cannot have broken it

**Checkpoint**: both interfaces are converted and agree with each other (quickstart scenario 3,
SC-008).

---

## Phase 5: User Story 3 — The record and the scripts do not move (Priority: P3)

**Goal**: prove, rather than assert, that nothing outside the rendering layer changed.

**Independent Test**: run every command with `--json` under three very different zones and diff
the outputs; write audit records under a non-UTC zone and confirm the files and their names are
unchanged. Quickstart scenarios 4 and 5.

**These tasks are almost entirely tests, because the story is a constraint rather than a
capability.** They are last in priority and first in importance if anything goes wrong.

- [X] T021 [P] [US3] Create `tests/unit/test_time_record_unchanged.py` asserting that `status`, `show`, `cards`, `worktree list` and `anomalies` produce byte-identical `--json` payloads under `America/New_York`, `Asia/Kolkata` and `UTC` (SC-004)
- [X] T022 [US3] Add a test to `tests/unit/test_time_record_unchanged.py` that performs an audited action under `Asia/Kolkata` and asserts the written record's `ts` ends in `Z` and the file is still named for the **UTC** day, not the local one (SC-005, FR-011)
- [X] T023 [US3] Add a test to `tests/unit/test_time_record_unchanged.py` asserting no stored value is rewritten: read a row's `*_at` columns, render every view and command that displays them, re-read, and assert the stored values are unchanged (FR-010)
- [X] T024 [US3] Audit by inspection that no comparison, ordering, age, staleness threshold, backoff window or capacity decision reads a converted value — check `pages.age_seconds`, `pages.human_age`, `operations._age_seconds`, `health.check`, `poll`'s backoff, `ordering.plan`, `capacity.snapshot`, and every `ORDER BY` — and record the result in a comment in `src/robot_army/timefmt.py` naming `local()` as display-only (FR-013)
- [X] T025 [US3] Add a test to `tests/unit/test_time_record_unchanged.py` asserting `--since 30s`, `10m`, `2h` and `1d` keep their existing grammar and meaning under a non-UTC zone (FR-014)

**Checkpoint**: all three stories are independently functional and the safety claim is
evidenced.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T026 [P] Document the split in `docs/logging.md` beside line 59's definition of `ts`: the record is UTC always, what the CLI and web interface print is the host's local time, and the two differ on purpose (FR-018)
- [X] T027 [P] Add a `## 010 — Times are read where they are read` section to `docs/roadmap.md` with status and findings, and move the "Whatever survives contact with reality" parking lot to the 011 slot — the sixth time a milestone with a shape has displaced it
- [X] T028 Run every scenario in [quickstart.md](quickstart.md) end to end, including scenario 7's DST fold and scenario 8's corrupt stamp, and correct the document wherever the real output differs from what it predicts
- [X] T029 Run `uv run pytest` under `TZ=America/New_York`, `TZ=Asia/Kolkata` and `TZ=UTC` and confirm the full suite passes in all three — the suite passing in any zone is itself evidence that no decision depends on the display zone

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks both user stories**, because both call `timefmt.local()`
- **US1 (Phase 3)** and **US2 (Phase 4)**: both depend only on Phase 2, touch disjoint files, and can run fully in parallel
- **US3 (Phase 5)**: its tests are most meaningful once US1 and US2 have landed, since they guard against regressions those phases could introduce. T021 and T025 can be written earlier and will pass before the change as well as after — which is exactly what makes them good guards.
- **Polish (Phase 6)**: depends on everything

### User Story Dependencies

- **US1 (P1)**: independent. Ships alone as the MVP.
- **US2 (P2)**: independent of US1. The two share only `timefmt`, which Phase 2 delivers.
- **US3 (P3)**: not independent in value — it is the constraint on the other two — but its tasks are independently runnable and its tests pass both before and after the change, which is the property that makes them regression guards rather than acceptance tests.

### Within Each User Story

- US1: T007 → T008 → T009 → T010 → T011 are sequential (one file), then T012 and T013 (one file, sequential with each other)
- US2: T014 → T015 → T016 sequential in `pages.py`; T017 parallel in `html.py`; then T018 → T019 sequential, T020 parallel
- US3: T021 first, then T022 → T023 → T025 sequential in one file; T024 is inspection and parallel with all of them

### Parallel Opportunities

- **Phase 1**: T002 alongside T001
- **Phase 2**: T003 and T004 together — different files, no shared state
- **Across stories**: once Phase 2 is done, all of US1 and all of US2 proceed at the same time. This is the feature's one large parallel window, and it is genuine: `operations.py` and `web/` share no line.
- **Phase 4**: T017 (`html.py`) runs alongside T014–T016 (`pages.py`)
- **Phase 5**: T024 (inspection) alongside the test tasks
- **Phase 6**: T026 and T027 together — different documents

---

## Parallel Example: after Phase 2

```bash
# Two disjoint file sets. Neither touches the other.
Task: "US1 — convert the ten display sites in src/robot_army/operations.py (T007–T011)"
Task: "US2 — convert when() and the log ts in src/robot_army/web/pages.py (T014–T016)"
Task: "US2 — convert the pill and the footer in src/robot_army/web/html.py (T017)"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: baseline
2. Phase 2: `timefmt` and the zone fixture — **blocks everything**
3. Phase 3: the terminal
4. **STOP and VALIDATE**: quickstart scenario 1. The terminal reads local; the web still reads
   UTC and is not wrong, merely unconverted.

This is a real stopping point. The spec's US1 rationale is that the terminal carries every
timestamp the system has and is where the maintainer already is.

### Incremental Delivery

1. Setup + Foundational → the conversion exists
2. US1 → the terminal reads local → **MVP**
3. US2 → the phone reads local, and the two interfaces agree
4. US3 → the record is proven not to have moved
5. Polish → the split is written down so the next reader does not "fix" half of it

### Order to prefer if anything goes wrong

Run T021 (JSON parity) and T022 (audit parity) early even though they are Phase 5. They pass
before the change as well as after, cost nothing to write, and are the two tests that would
catch the only way this feature can do real harm — a local time reaching a machine-readable
surface.

---

## Notes

- `[P]` = different files, no dependencies. US1 has none of it and that is honest, not an
  oversight: ten sites, one file.
- Every implementation task converts output only. If a task finds itself editing something that
  populates `Result.data`, `View.data`, or the chrome dict, it is the wrong edit — see the
  boundary diagram in [data-model.md](data-model.md).
- Commit after each task or logical group; messages explain why, per the constitution.
- The existing suite is left untouched throughout. If a task requires changing an existing test,
  stop: either the change is wrong, or [R7](research.md)'s finding has gone stale and the
  contract needs updating first.
