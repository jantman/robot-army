---
description: "Task list for: a merged pull request retires the session"
---

# Tasks: A merged pull request retires the session

**Input**: Design documents in `specs/20260906-140852-merged-pr-retires-session/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/retirement-signal.md](./contracts/retirement-signal.md),
[quickstart.md](./quickstart.md)

**Tests**: Required, not optional. The constitution's Development Workflow section makes unit
tests mandatory for every new or changed unit of behaviour, and adds failure- and
interruption-path tests for state machines and for code parsing external input. Both apply
here: the gate is a decision table, and the pull request column is externally-sourced text.

**Organization**: by user story. US1 is the fix; US2 is the guard that keeps the fix from being
an over-reach. US2's behaviour is *preserved* rather than added, so its phase is mostly tests —
that is the correct shape, not an omission.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: `[US1]` or `[US2]`, mapping to spec.md's user stories

## Path conventions

Single project. `src/robot_army/`, `tests/unit/`, `docs/guide/` at the repository root.

---

## Phase 1: Setup

**Purpose**: establish the baseline this change is measured against.

- [ ] T001 Run `uv sync && uv run pytest` from the repository root and record that the suite is green **before** any edit. A pre-existing failure must be known now rather than attributed to this change later.
- [ ] T002 Re-read the two comments this feature falsifies so the corrections in T017/T018 replace what is actually there: the retire-before-sweep paragraph at `src/robot_army/reconcile.py:511` and the `RETIRE_IDLE_SECONDS` comment at `src/robot_army/reconcile.py:881`–893.

---

## Phase 2: Foundational (blocking prerequisite)

**Purpose**: the predicate both user stories read. US1 needs it true; US2 needs it false for
every other column shape. Nothing else blocks.

**⚠️ Both story phases depend on this.**

- [ ] T003 Add `WorkItem.has_merged_pull_request` to `src/robot_army/models.py` immediately after `pull_request_list`, per contract D2: true when any element of `pull_request_list` has `state == "merged"` — an exact match, because states are lower-cased at the boundary and an unrecognised one passes through. Docstring explains **why** (this codebase's convention), specifically: that `NULL`, `'[]'`, unparseable text, a non-list payload and non-object elements all read as *not merged* because that is the direction which delays a retirement rather than causing one; and that any merged pull request counts, not the newest, because a retried item carries a closed-unmerged attempt beside the merged one.

**Checkpoint**: the predicate exists and is readable from a `WorkItem`. Story work can begin.

---

## Phase 3: User Story 1 — merging finishes it, now rather than in half an hour (Priority: P1) 🎯 MVP

**Goal**: an item that reaches `done` with a merged pull request has its worker retired on that
same pass, whatever its idle duration — so the anomaly, the held slot, the open tab and the
`skipped` worktree never happen.

**Independent Test**: quickstart scenario 1. One full `reconcile.reconcile` pass over a `done`
item with a merged pull request and a worker idle **47 seconds**: `result.retired == 1`,
`result.orphans == 0`, no `anomaly.raised` record for that session, the row `LOST`, the slot
free.

### Tests for User Story 1 — written first and seen to fail

The first of these is the test that would have caught this bug. Every existing full-pass
retirement test builds its item with `LONG_IDLE_MS`, so all of them exercise the quiet-period
path and all of them pass today.

- [ ] T004 [US1] Add a full-pass test to `tests/unit/test_session_retirement.py`: a `done` item with `[{"number": 147, "url": …, "state": "merged"}]` written via `db.record_pull_requests`, and a live worker with `status="idle"` and `statusUpdatedAt` 47 seconds ago (the measured number from #149 — use it, and say so in the docstring). Run `reconcile.reconcile(...)` once and assert `result.retired == 1`, `result.orphans == 0`, `result.anomalies_resolved == 0`, the session row `LOST` with `ended_at`, and **no `anomaly.raised` record for that session id** in the audit log. Confirm it fails before Phase 2/3 source changes land — on `retired` and on `orphans` — because that failure *is* the bug.
- [ ] T005 [P] [US1] Add a sweep-level test to `tests/unit/test_session_retirement.py` for contract D1 rule 6: a `done` item, a merged pull request, `idle_ms` of a few seconds, expecting one retirement. The complement of the existing `test_a_worker_idle_for_less_than_the_threshold_is_left_alone`, which must keep passing unchanged.
- [ ] T006 [P] [US1] Add tests to `tests/unit/test_session_retirement.py` for contract D1 rule 5 **on the merged path** (FR-002, quickstart scenario 4): a `done` item with a merged pull request whose registry entry is `status="busy"`, has no status, has no `statusUpdatedAt`, and has a `statusUpdatedAt` in the future. Each expects nothing terminated and nothing written, however old the timestamp. Assert the host recorded no calls, not merely that the row survived.
- [ ] T007 [P] [US1] Add a test to `tests/unit/test_session_retirement.py` that a `done` item with a merged pull request but **no live process** (`pid` falsey, or no registry entry, or the entry not alive) is still left to `_sweep_stale_sessions` — rules 2–4 are unchanged and the merged signal does not reach past them.
- [ ] T008 [P] [US1] Add a test to `tests/unit/test_session_retirement.py` that an item in `active`, `awaiting_review`, `failed` or `abandoned` with a merged pull request and an idle worker is **not** retired (FR-007). The precondition is still `done` and this feature does not widen it.
- [ ] T009 [P] [US1] Add a test to `tests/unit/test_session_retirement.py` asserting `session.retire`'s detail carries `signal == "merged_pull_request"` alongside the existing `item_id`, `session_id`, `pid`, `proc_start` and `idle_s`, and that `idle_s` is the small real number rather than being clamped or omitted (FR-009, contract D3).
- [ ] T010 [P] [US1] Extend the existing full-pass tab/worktree test in `tests/unit/test_session_retirement.py` (or add its sibling) for quickstart scenario 2: the same freshly-idle merged item with `cleanup.on_issue_close` enabled and a window carrying the item's marker — the tab is closed and `cleanup_state` is no longer `skipped`, in the **same** pass. This is the #81 half and it must pass with no change to either rule.

### Implementation for User Story 1

- [ ] T011 [US1] Add `_retire_signal(item, idle_s)` to `src/robot_army/reconcile.py` returning `"merged_pull_request"`, `"quiet_period"` or `None`, implementing contract D1 rules 6–8 as one decision table. Docstring says why it is a helper rather than a compound `if` — the gate is a table and a table should be readable and testable as one — and why rule 6 has **no floor**, citing the 47-second measurement rather than asserting a preference.
- [ ] T012 [US1] Rewrite the gate in `_retire_finished_sessions` (`src/robot_army/reconcile.py:954`) as contract D1 requires: `idle_s = entry.idle_for()`; `None` → `continue` (rule 5, unchanged in meaning **and** in position); then `signal = _retire_signal(item, idle_s)`; `None` → `continue`. The comment on the `None` branch must keep saying that every unknown lands there, and must now also say that this is the branch a merged pull request does **not** bypass.
- [ ] T013 [US1] Thread `signal` through `_retire_one` in `src/robot_army/reconcile.py` into the `session.retire` detail, beside `idle_s`. Written **before** the termination, exactly as now — the ordering is Principle III and does not move.
- [ ] T014 [US1] Update `_retire_one`'s settle reason so a session row read on its own says which condition ended it: the merged case names the pull request rather than the idle seconds (contract D3's last paragraph).
- [ ] T015 [US1] Update `_retire_finished_sessions`'s docstring: the precondition is now `done` **plus one of two signals**, and the paragraph explaining that a non-retirement writes nothing stands unchanged and still applies to both paths.

**Checkpoint**: T004 passes. The bug is fixed and the ordinary successful path raises nothing.

---

## Phase 4: User Story 2 — an issue closed by hand keeps its half-hour (Priority: P2)

**Goal**: preserve today's behaviour exactly where there is no merged pull request. Nothing is
implemented here; the phase exists because "unchanged" has to be asserted or it is not a claim.

**Independent Test**: quickstart scenario 3. A freshly-idle worker under a `done` item with no
merged pull request is retired by no pass until `RETIRE_IDLE_SECONDS` has elapsed, and then is
retired on the unchanged rule.

- [ ] T016 [P] [US2] Add tests to `tests/unit/test_session_retirement.py` for contract D1 rule 7 across every non-merged column shape: `pull_requests` `NULL` (never looked up), `'[]'`, `[{"state": "open"}]`, `[{"state": "closed"}]`, and a mixed set of `open` + `closed`. Each with a freshly-idle worker, each expecting nothing retired and nothing written. Then advance past `RETIRE_IDLE_SECONDS` and assert the retirement happens with `signal == "quiet_period"`.
- [ ] T017 [P] [US2] Add tests to `tests/unit/test_reconcile_pull_requests.py` covering `has_merged_pull_request` over every shape the column can hold, beside that file's existing unreadable-column tests: `NULL`, `'[]'`, text that will not parse, a payload that is not a list, a list of non-objects (`[144]`), `open` only, `closed` only, `merged` alone, `merged` mixed with a closed-unmerged attempt, and an unrecognised lower-cased state such as `draft`. The last is the one that matters most: a state GitHub adds later must read as *not merged*.

**Checkpoint**: the hand-closed path is pinned. A future change that widens the merged rule
fails here rather than in production.

---

## Phase 5: Polish, documentation and the reasoning this change falsifies

**Purpose**: CLAUDE.md's rule 1 — a change to behaviour updates its guide page — and FR-012.

- [ ] T018 Correct the retire-before-sweep comment at `src/robot_army/reconcile.py:511` per contract D6: the ordering makes the anomaly *unreachable once retirement acts*, which was necessary but never sufficient; acting on the pass the item reaches `done` is what the merged-pull-request signal is for. Say plainly that the original claim was wrong and how it was found wrong, in this codebase's habit of recording the reasoning rather than only the conclusion.
- [ ] T019 Rewrite the `RETIRE_IDLE_SECONDS` comment at `src/robot_army/reconcile.py:881`–893 per contract D6: re-scope "erring long is nearly free" to the hand-closed path where it is still true, and state why the merged path does not wait — the cost there was an anomaly on every successful item, which is what #138 was filed about. Keep the constant at 1800 and keep the "if the value proves wrong, the value changes" note.
- [ ] T020 [P] Update `docs/guide/5-outcome.md`: the retirement section states the 30-minute rule as though it were the only one. It becomes two rules with the merge as the primary signal, including why no floor applies and the observation that the shipped gate had never once been crossed by an item finishing normally. The tab section needs no rule change but should say the tab now goes on the pass the item reaches `done`.
- [ ] T021 [P] Update `docs/guide/audit-log.md`: `session.retire`'s detail row gains `signal`, with both values and what each means, and a sentence saying `idle_s` no longer implies the reason.
- [ ] T022 Verify `docs/guide/configuration.md`, `src/robot_army/exampleconfig.py` and `share/config.example.toml` are untouched, and that `tests/unit/test_example_config_drift.py` still passes — no configuration key changes, so neither CLAUDE.md configuration step is triggered. A diff touching any of them means the design drifted.
- [ ] T023 Run `uv run pytest` in full from the repository root. Every existing retirement test must still pass unchanged — particularly `test_a_worker_idle_for_less_than_the_threshold_is_left_alone`, which now describes the hand-closed path and must be reachable only by items with no merged pull request. Fix any test whose fixture happens to carry a merged pull request it did not mean to.
- [ ] T024 Re-read the diff against [contract D5](./contracts/retirement-signal.md) and confirm every row of it is asserted by a test that exists: worker ended, row closed, slot released, tab closed, worktree eligible, and `orphan_session` **never raised**.

---

## Dependencies

```
Phase 1 (T001–T002)
  └── Phase 2 (T003) ── the predicate, blocking both stories
        ├── Phase 3 US1 (T004–T015) ── the fix
        └── Phase 4 US2 (T016–T017) ── the guard; needs T003, not T011–T015
              └── Phase 5 (T018–T024)
```

- **T004 before T011–T015.** It must be seen to fail first. It is the only artifact of this
  feature that demonstrates the bug rather than describing it.
- **T003 before everything in Phases 3 and 4.** Both stories read the predicate.
- **T011 before T012**; **T012 before T013–T015** (same function, sequential edits).
- **T018–T019 after T011–T015**, so the corrected comments describe code that exists.
- US1 and US2 are otherwise independent: US2's tests are written against T003 alone and pass
  today, which is what makes them a regression guard rather than a specification of new work.

## Parallel opportunities

- **T005–T010** — all in `tests/unit/test_session_retirement.py` but independent test functions;
  parallel in the sense of "any order", not "same file simultaneously".
- **T016 and T017** — different files, fully parallel.
- **T020 and T021** — different documentation files, fully parallel.

## Implementation strategy

**MVP is Phase 1 → Phase 2 → Phase 3.** T004 failing and then passing is the whole of issue
#149. Phase 4 adds no behaviour and Phase 5 adds no code; both are required for completeness —
Phase 4 because an unasserted "unchanged" is not a claim, and Phase 5 because CLAUDE.md and
FR-012 both require it, and because shipped reasoning that argues for behaviour the code no
longer has is worse than no comment at all.

Everything is one atomic-commit-per-step of a roughly twenty-line source change. If the diff to
`src/` grows past that, something has been added that the plan does not call for.
