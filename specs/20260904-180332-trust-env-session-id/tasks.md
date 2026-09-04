---

description: "Task list for: the session wrapper trusts only the identifiers its launcher gave it"
---

# Tasks: The session wrapper trusts only the identifiers its launcher gave it

**Input**: Design documents from `specs/20260904-180332-trust-env-session-id/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/session-wrapper.md, quickstart.md

**Tests**: Required, not optional. The constitution's Development Workflow section requires
unit tests for every new or changed unit of behaviour, and requires code parsing external
input to carry tests for its failure paths as well as its success paths. That is precisely
what this feature is, so every story below leads with its tests.

**Organization**: Grouped by user story. Note the honest caveat under Parallel Opportunities:
all three stories edit the same 140-line shell script, so they are sequential in practice.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3, mapping to the user stories in spec.md
- Exact file paths are given in every task

## Path Conventions

Single project. The wrapper is at `share/robot-army-session-wrapper.sh`; the daemon is under
`src/robot_army/`; tests are under `tests/unit/` and `tests/integration/`.

---

## Phase 1: Setup

**Purpose**: Establish a known-green starting point, so any later failure is attributable to
this feature rather than inherited.

- [X] T001 Run `uv run pytest -q` and record that the suite is green before any edit; if it is not, stop and report rather than building on a red baseline

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The ordering change that both P1 stories depend on. Neither the env-only
sourcing nor the shape checks can satisfy "creates no file or directory on refusal" while
`mkdir -p` still runs first.

**⚠️ CRITICAL**: No user story work begins until T002 is done.

- [X] T002 In `share/robot-army-session-wrapper.sh`, move the `mkdir -p "$SPOOL_DIR" "$LOG_DIR"` call and the `LOGFILE=` composition below the point where identifiers are resolved, leaving behaviour otherwise unchanged, so that later validation can precede every filesystem effect (research D3)

**Checkpoint**: The script still passes the existing suite; the preamble is now ordered so
validation has somewhere to go.

---

## Phase 3: User Story 1 — A repository cannot redirect where the wrapper writes (Priority: P1) 🎯 MVP

**Goal**: The session id comes from `ROBOT_ARMY_SESSION_ID` and from nothing else, so text
inside the composed prompt cannot name the session or steer where records land.

**Independent Test**: Run the wrapper with a valid id in the environment and a final,
prompt-shaped argument that names a different id containing `../` segments. Both records
appear in the spool directory under the environment's id, and nothing is created outside it.

### Tests for User Story 1

- [X] T003 [US1] Create `tests/unit/test_session_wrapper_input.py` with a helper that runs the real `share/robot-army-session-wrapper.sh` in a `tmp_path` spool/log/sessions layout and returns its exit status, stderr, and every file created anywhere under the temporary root
- [X] T004 [US1] In `tests/unit/test_session_wrapper_input.py`, assert that a final argument beginning `--session-id=../sessions/hijacked` — the RA-16 payload from quickstart.md step 1 — leaves both records in the spool directory named for the environment's id, and creates nothing in the sibling `sessions/` directory
- [X] T005 [US1] In `tests/unit/test_session_wrapper_input.py`, assert the same for the separated `--session-id <value>` form, since the deleted loop matched both spellings
- [X] T006 [US1] In `tests/unit/test_session_wrapper_input.py`, assert the written record's `session_id` field equals the environment's id, which is the join key the daemon depends on

### Implementation for User Story 1

- [X] T007 [US1] Delete the `--- Recover the session id from argv ---` loop from `share/robot-army-session-wrapper.sh` and set `SESSION_ID` from `ROBOT_ARMY_SESSION_ID` alone (research D1)
- [X] T008 [US1] Update the header comment and usage line in `share/robot-army-session-wrapper.sh`: `ROBOT_ARMY_SESSION_ID` is required, not a fallback for when argv lacks the id, and the stale "if it is not discoverable from argv" phrasing must go rather than be left describing behaviour that no longer exists
- [X] T009 [US1] Update the three wrapper tests in `tests/integration/test_spool_recovery.py` to pass the session id via `ROBOT_ARMY_SESSION_ID` and use real UUID-shaped ids instead of names like `wrapper-session`, so they exercise the path production uses
- [X] T010 [P] [US1] Add a guard test in `tests/unit/test_launch_shapes.py` asserting that `dispatch.build_launch_plan` puts `ROBOT_ARMY_SESSION_ID` in the launch environment and that its value equals the plan's `session_id` — the wrapper's only source of truth, which nothing else currently fails on if removed

**Checkpoint**: RA-16 is closed. The finding's own payload no longer redirects a write.

---

## Phase 4: User Story 2 — An implausible identifier is refused loudly, not used (Priority: P1)

**Goal**: Both identifiers that name a path are shape-checked before any path is built, so a
value the system did not issue cannot reach the filesystem by any future route either.

**Independent Test**: Invoke the wrapper with an empty, traversing, or wrong-shaped
identifier. Exit is non-zero, stderr names which identifier was refused, and no file or
directory exists anywhere under the temporary root.

### Tests for User Story 2

- [ ] T011 [US2] In `tests/unit/test_session_wrapper_input.py`, assert that an unset `ROBOT_ARMY_SESSION_ID` produces exit 2, a message naming the session id, no worker execution, and no file created
- [ ] T012 [US2] In `tests/unit/test_session_wrapper_input.py`, assert the same refusal for a session id that is `../../escape`, an empty string, and a plausible-but-wrong value such as `wrapper-session`
- [ ] T013 [US2] In `tests/unit/test_session_wrapper_input.py`, assert that a UUID followed by a newline and `../x` is refused, pinning the anchor behaviour measured in research D2 so a future rewrite cannot reintroduce a trailing-newline bypass
- [ ] T014 [US2] In `tests/unit/test_session_wrapper_input.py`, assert that a non-integer item id such as `../../evil` is refused with exit 2 before any file is created, including under the bad name
- [ ] T015 [US2] In `tests/unit/test_session_wrapper_input.py`, assert that no refusal creates the spool or log directories themselves, which is what T002's reordering exists to make true

### Implementation for User Story 2

- [ ] T016 [US2] In `share/robot-army-session-wrapper.sh`, validate `ITEM_ID` against `^[0-9]+$` immediately after it is read, refusing with a message naming it and exit 2 (research D3)
- [ ] T017 [US2] In `share/robot-army-session-wrapper.sh`, validate `SESSION_ID` against the canonical UUID pattern from data-model.md, refusing with a message naming it and exit 2 (research D2)
- [ ] T018 [US2] Confirm both checks sit above the `mkdir -p` moved in T002, and add a short comment in `share/robot-army-session-wrapper.sh` saying why the order matters, since the ordering is the property and a later edit could silently undo it

**Checkpoint**: The class of defect is closed, not merely the one known route into it.

---

## Phase 5: User Story 3 — Ordinary control characters do not quarantine a record (Priority: P2)

**Goal**: Any text reaching a record leaves it parseable by a strict reader and byte-exact on
the way back out.

**Independent Test**: Run the wrapper with an argument containing each control character and
read the records back with `json.loads(..., strict=True)`, comparing the decoded text to the
input.

### Tests for User Story 3

- [ ] T019 [US3] In `tests/unit/test_session_wrapper_input.py`, assert that an argument containing all 31 reachable control characters produces records that parse under `json.loads(..., strict=True)` and whose decoded `argv` element equals the input exactly — the full set rather than a sampled subset, per SC-003
- [ ] T020 [US3] In `tests/unit/test_session_wrapper_input.py`, assert that quotes, backslashes, newlines, carriage returns, tabs and multi-byte UTF-8 still round trip, so the widened escaping does not regress what already worked

### Implementation for User Story 3

- [ ] T021 [US3] Extend `jesc` in `share/robot-army-session-wrapper.sh` to map code points 1-8, 11, 12 and 14-31 to `\u00XX` using only `printf -v` and parameter expansion, placed after the existing backslash substitution so the escapes it introduces are not themselves escaped (research D4)
- [ ] T022 [US3] Add a brief comment in `share/robot-army-session-wrapper.sh` recording why the loop is unguarded and why 0 and 127 are excluded, so the next reader does not re-derive it or "optimize" in a locale-dependent bracket range

**Checkpoint**: RA-48 is closed. An issue body can no longer quarantine its own exit record.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T023 [P] Update `docs/security-analysis.md`: move the RA-16 row to **Resolved**, and add a dated resolution paragraph in the RA-15/RA-16 section stating what was done and why the argv fallback was deleted rather than validated, following the form the RA-15 resolution already uses
- [ ] T024 [P] Update `docs/security-analysis.md`: mark RA-48 resolved in the low-findings list, noting that it was fixed alongside RA-16 because it is the same function
- [ ] T025 Update the remediation list near the end of `docs/security-analysis.md` so item 6 ("delete the wrapper's argv scan") reflects that it is done
- [ ] T026 Walk `specs/20260904-180332-trust-env-session-id/quickstart.md` end to end by hand, including step 1 against the pre-fix script, to confirm the documented reproduction and the documented fix both behave as written
- [ ] T027 Run `uv run pytest -q` and confirm the full suite passes — the constitution's completion gate

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: no dependencies
- **Foundational (T002)**: depends on T001; blocks US1 and US2
- **US1 (T003-T010)**: depends on T002
- **US2 (T011-T018)**: depends on T002; independent of US1 in behaviour, but edits the same file
- **US3 (T019-T022)**: depends only on T001 — it touches `jesc`, which neither P1 story modifies
- **Polish (T023-T027)**: depends on the stories whose outcomes it documents

### Within Each Story

Tests first, then implementation. The tests are expected to fail before the implementation
task in the same phase lands; that failure is the evidence the test is testing something.

### Parallel Opportunities

Genuinely parallel: **T010** (a different file, `tests/unit/test_launch_shapes.py`), and
**T023/T024** (both in `docs/security-analysis.md`, so parallel with code work but not with
each other).

Everything else is sequential, and the reason is worth stating rather than dressing up:
US1, US2 and US3 all edit `share/robot-army-session-wrapper.sh`, and US1, US2 and US3's tests
all live in one new file. Marking them `[P]` would produce conflicts, not speed. US3 is the
one story that could be done first or last without disturbing the others, because `jesc` is
untouched by the identifier work.

---

## Implementation Strategy

### MVP

Phases 1-3 (T001-T010). That closes RA-16 — the finding this feature exists for — and is
independently valuable: the argv scan is gone and the daemon's id is authoritative, whether
or not the shape checks land.

### Incremental Delivery

1. Setup + Foundational → the script is ordered so validation can precede filesystem effects
2. US1 → RA-16 closed; **stop and validate** with quickstart.md step 1
3. US2 → the class closed, not just the route; validate with quickstart.md step 2
4. US3 → RA-48 closed; validate with quickstart.md step 3
5. Polish → the security analysis stops pointing readers at fixed findings

### Notes

- Commit per story rather than per task; each story is one coherent reason for a change,
  which is what the constitution asks a commit message to explain
- The three stories are one file, so run the full suite after each rather than only at the end
- Do not add a compatibility shim for the removed argv fallback — Principle V, and research D1
