---

description: "Task list for closing a finished item's terminal tabs"
---

# Tasks: Close a finished item's terminal tabs

**Input**: Design documents from `/specs/20260905-145251-close-retired-tab/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/window-closing.md](./contracts/window-closing.md),
[quickstart.md](./quickstart.md)

**Tests**: Required, not optional — the constitution's Development Workflow makes unit tests
mandatory for every new or changed unit of behaviour. Here they carry more than usual weight:
User Story 2 is *entirely* a set of tests, because "must not close" is a property of the candidate
set rather than code of its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1, US2, US3 from [spec.md](./spec.md)
- Every task names the exact file it touches

## Path Conventions

Single project: `src/robot_army/`, `tests/`, `docs/guide/` at the repository root.

---

## Phase 1: Setup

- [ ] T001 Run `uv run pytest` and `uv run ruff check src/ tests/` and record both green before any edit, so a later failure is attributable to this feature rather than inherited

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the one new capability every story depends on. `find_by_var` returns the first match
only, so it cannot answer "every window belonging to a finished item" (research R4).

**⚠️ CRITICAL**: complete before Phase 3.

- [ ] T002 Add `list_by_var(self, key: str) -> list[DisplayHandle]` to the `Display` protocol in `src/robot_army/boundaries/__init__.py`, documented as "every window carrying this user variable, with its value readable from the returned handle". Leave `find_by_var` exactly as it is — it has its own caller and its own meaning
- [ ] T003 Implement `list_by_var` on `KittyDisplay` in `src/robot_army/boundaries/kitty.py` over the existing `_windows()` helper, building each `DisplayHandle` with `window_id`, `title` and the full `user_vars`, exactly as `find_by_var` already does for its single result
- [ ] T004 [P] Implement `list_by_var` on `SimulatedDisplay` in `src/robot_army/boundaries/kitty.py`, answering from its in-memory window map so a simulated run exercises the whole decision path against windows it opened itself (contract W7)
- [ ] T005 [P] Add `list_by_var` returning `[]` to `StubDisplay` in `tests/conftest.py`, so every existing test that wires a stub display through `reconcile()` keeps passing and demonstrably closes nothing
- [ ] T006 [P] Add cases to `tests/unit/test_effects.py` (or a new `tests/unit/test_display_listing.py`) proving `list_by_var` returns **every** matching window rather than the first, returns `[]` when none match, and never returns a window that lacks the key — the three properties the sweep's correctness rests on

---

## Phase 3: User Story 1 — A finished item leaves no tabs behind (Priority: P1) 🎯 MVP

**Goal**: a `done` item whose sessions have all ended loses every one of its terminal windows, on
the next reconciliation pass, with no action by the maintainer.

**Independent test**: seed a `done` item with an ended session and a marked window, run a pass, and
assert the window is gone and `windows_closed` is 1.

### The sweep

- [ ] T007 [US1] Add `windows_closed: int = 0` to `ReconcileResult` in `src/robot_army/reconcile.py` and to `summary()`, so it reaches the existing `reconcile.pass` record and the `robot-army reconcile` output (FR-015, contract W5)
- [ ] T008 [US1] Implement the candidate set in `_close_finished_windows(conn, *, boundaries, audit)` in `src/robot_army/reconcile.py` per contract W2: `done` items that have **at least one** session row and for which `cleanup.live_sessions(conn, item.id)` is empty. Return 0 immediately when the set is empty, **before the display is touched at all** — comment why (research R6: it is what keeps a machine with no kitty from logging ~1,440 listing failures a day, and what makes the failure that does get logged mean something)
- [ ] T009 [US1] Implement the identity rules in the same function per contract W3: for each handle from `boundaries.display.list_by_var("ra_item")`, skip a missing or non-integer marker, skip an id not in the candidate set, otherwise close. Comment that `sessions.window_id` is deliberately **not** consulted — kitty renumbers windows from 1 on restart, so a stored id can name a stranger's window, which is the pid-reuse failure this codebase already carries two guards against (research R3)
- [ ] T010 [US1] Implement the outcomes per contract W4 in the same function: count a close, treat a window that had already gone as success rather than failure, record `window_close_failed` for a close that raised and **continue to the next window**, and record a failed listing once for the pass. Catch `BoundaryError` on both paths — a reconciliation pass never raises for an operational condition
- [ ] T011 [US1] Wire `_close_finished_windows` into `reconcile()` in `src/robot_army/reconcile.py` after `_sweep_sockets`, counting into `result.windows_closed`, with a positioning comment in the style of the neighbouring sweeps: it sits with the other physical-residue sweeps, and after both `_retire_finished_sessions` and `_sweep_stale_sessions`, so a session retired earlier in this pass has its window closed in the **same** pass

### Tests

- [ ] T012 [US1] Create `tests/unit/test_window_closing.py` with the candidate-set cases from [quickstart.md](./quickstart.md) Scenario 1, using the real `SimulatedDisplay` rather than a stub — matching the precedent `make_boundaries` sets for `SimulatedSessionHost`, so the test exercises the production object. Cases: a `done` item with one ended session and one marked window is closed; an item still holding a `running` session is not; a `done` item with **no** session rows is not (contract W2's third condition)
- [ ] T013 [US1] Add the multiple-attempts case to `tests/unit/test_window_closing.py` (FR-002): an item resumed once, two ended sessions, two windows both carrying the same `ra_item` — assert **both** are closed, and that this needs no per-attempt logic because the marker names the item
- [ ] T014 [US1] Add the same-pass case to `tests/unit/test_window_closing.py`: drive the whole of `reconcile()` with a `done` item whose session was retired earlier in that pass, and assert the window is gone by the end of that pass rather than the next (contract W1)
- [ ] T015 [P] [US1] Add the counter case to `tests/unit/test_window_closing.py`: `reconcile()` reports `windows_closed` in its result and in the `reconcile.pass` audit record
- [ ] T016 [P] [US1] Add the audit case to `tests/unit/test_window_closing.py` (FR-012): every close leaves a `kitty.close_window` record naming the window, produced by the existing `audit.action` context rather than by anything new
- [ ] T017 [P] [US1] Add the failure-path cases from [quickstart.md](./quickstart.md) Scenario 4 to `tests/unit/test_window_closing.py`, each with its own display double: the listing raises (recorded once, pass completes, `windows_closed == 0`); one close raises while a second window still qualifies (the second is closed, the first is recorded and not counted); a window vanished between listing and close (**success**, not recorded as a failure)
- [ ] T018 [US1] Add the "never called" case to `tests/unit/test_window_closing.py`: with no candidate items, assert the display's `list_by_var` was **not invoked at all**. A test about a call that must not happen, and the whole of the cost argument in research R6 — easy to omit and the one that catches a refactor putting the listing first

---

## Phase 4: User Story 2 — Failed and abandoned work keeps its window (Priority: P1)

**Goal**: the behaviour `--hold` was introduced for survives this feature intact.

**This phase is tests and one verification, deliberately.** "Must not close" is a property of the
candidate set built in T008, not separate code — so there is nothing to implement, and the risk is
precisely that nothing pins it. A build that closed tabs correctly while also closing a failed
launch's window would be a regression rather than a partial success, which is why this is P1
alongside User Story 1 and not below it.

**Independent test**: seed a `failed` item and an `abandoned` item with marked windows, run ten
passes, and assert both windows survive.

- [ ] T019 [P] [US2] Add the state cases to `tests/unit/test_window_closing.py` (FR-003): a `failed` item and an `abandoned` item, each with an ended session and a marked window, survive **ten** passes. Ten and not one, because a build that closed them on the second pass would satisfy a single-pass assertion
- [ ] T020 [P] [US2] Add the unmarked-window case to `tests/unit/test_window_closing.py` (FR-008): a window with no `ra_item` at all is never closed, whatever its title or working directory. This stands in for every window the maintainer opened themselves, and the assertion is that the sweep does not act on it under any item state
- [ ] T021 [P] [US2] Add the unresolvable-marker cases to `tests/unit/test_window_closing.py` (FR-009): an `ra_item` naming an item id that does not exist, and an `ra_item` that is not an integer — neither is closed and neither raises
- [ ] T022 [P] [US2] Add the failed-launch case to `tests/unit/test_window_closing.py`: an item that never reached `done` because its launch failed keeps its window indefinitely — the M0 F11 case `--hold` exists for, asserted directly rather than inferred from the state rule
- [ ] T023 [US2] Add the retried-then-succeeded case to `tests/unit/test_window_closing.py`: an item that failed, was retried, and later reached `done` loses its earlier attempt's window too. This is the one place the feature deliberately narrows what `--hold` preserves (spec edge case), so it is asserted rather than left to be discovered
- [ ] T024 [US2] Confirm by reading `git diff` that `KittyDisplay.open` still passes `--hold` and is otherwise unchanged (FR-017), and that `close()` and `find_by_var` are untouched (contract W8)

---

## Phase 5: User Story 3 — Stopping a finished item's session by hand (Priority: P3)

**Goal**: the by-hand route converges on the same outcome as the automatic one.

**No code.** Because the rule is written about the item's state and its sessions rather than about
which command ended them, `cancel` making a session terminal is enough. The value of this phase is
proving that, and proving it stays true.

- [ ] T025 [P] [US3] Add the convergence case to `tests/unit/test_window_closing.py`: stop a `done` item's session with `operations.cancel`, run a pass, and assert its windows are closed exactly as if retirement had ended the session
- [ ] T026 [US3] Add the counterpart to `tests/unit/test_window_closing.py`: a `failed` item whose session was stopped by hand keeps its window — the route does not change the answer. Then confirm by `git diff` that `src/robot_army/operations.py` is unchanged by this feature

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T027 Correct the false sentence in `docs/guide/5-outcome.md`. The "The session's ending" section currently states the kitty tab "closes with it", which has never been true; replace it with what actually closes a tab and when — a `done` item with no live session, on the next pass — and say plainly that a `failed` or `abandoned` item keeps its window on purpose
- [ ] T028 [P] Update `docs/guide/audit-log.md`: `kitty.close_window` gains its first caller and should appear with what it means; add `windows_closed` to the documented `reconcile.pass` shape; and add a row to the deliberately-unlogged table for a window that simply does not qualify, with its justification (contract W6) — the file already carries that table and this is the constitution's required enumeration
- [ ] T029 [P] Check whether `docs/guide/operating.md` needs anything: it describes what to look at when something looks wrong, and "a window that stayed open" is now a signal rather than normal. Add a line only if it earns one — an unnecessary paragraph in the guide is its own defect
- [ ] T030 Confirm no configuration key changed: `config.py`'s `_KNOWN_KEYS` and `_REPO_KEYS` untouched, so `share/config.example.toml` needs no regeneration. `tests/unit/test_example_config_drift.py` passing is the proof
- [ ] T031 Confirm no schema change: `migrations.py` untouched and `SCHEMA_VERSION` still 12, so `docs/guide/state.md` needs no edit
- [ ] T032 Read `git diff` and confirm contract W8's untouched list: `cleanup.py` (a third caller of `live_sessions`, not an edit), `capacity.py`, `states.py`, `spool.py`, `db.py`, `operations.py`, and `KittyDisplay.open`'s `--hold`
- [ ] T033 Run `uv run pytest` and `uv run ruff check src/ tests/`. The full suite must pass, including `tests/unit/test_effects.py`'s grep-the-source assertion that `reconcile.py` never names the effect level — the simulated display is chosen by the wiring, so the new sweep must contain no branch on it (contract W7)
- [ ] T034 Take the next real item through to a merged pull request and confirm its tab closes on its own ([quickstart.md](./quickstart.md) Scenario 5). The two windows that prompted this feature were closed by hand before implementation began, so there is no historical evidence left to reproduce against and the check is necessarily forward-looking

---

## Dependencies

```
Phase 1 (T001)
    ↓
Phase 2 (T002 → T003, T004, T005, T006)   ← list_by_var; blocks everything
    ↓
Phase 3 (US1)  T007 → T008 → T009 → T010 → T011, then tests T012…T018
    ↓
    ├─────────────────┬─────────────────┐
    ↓                 ↓                 ↓
Phase 4 (US2)     Phase 5 (US3)    Phase 6 (polish)
 T019…T024         T025…T026        T027…T034
```

**Story independence is asymmetric here, and worth being honest about.** US2 and US3 are *tests of
US1's code*, not separable increments — US2 pins a property of the candidate set and US3 pins a
consequence of how the rule is phrased. Neither can be delivered before US1, and neither adds
behaviour. They are separate phases because each defends something a future change could break
silently, not because they ship independently.

**Within Phase 2**: T002 first (the protocol), then T003/T004/T005 in parallel (three different
implementations), then T006.

**Within Phase 3**: T007 → T008 → T009 → T010 → T011 are sequential — same function, each building
on the last. T012–T014 depend on T011. T015–T018 are parallel once T011 lands, though T012–T023 all
write to `tests/unit/test_window_closing.py`, so treat `[P]` there as "no logical dependency"
rather than "edit the file concurrently".

## Parallel execution examples

**Phase 2, after T002 lands — three implementations of one method, three files:**

```
T003  src/robot_army/boundaries/kitty.py   (KittyDisplay)
T004  src/robot_army/boundaries/kitty.py   (SimulatedDisplay — same file, sequential with T003)
T005  tests/conftest.py                    [P]
T006  tests/unit/test_display_listing.py   [P]
```

**Phase 6 — documentation and verification, genuinely independent:**

```
T027  docs/guide/5-outcome.md
T028  docs/guide/audit-log.md
T029  docs/guide/operating.md
T030  verification — config keys
T031  verification — schema
T032  verification — the untouched list
```

## Implementation strategy

**MVP is Phase 1 + Phase 2 + Phase 3 (User Story 1)** — 18 tasks, and it delivers the whole of the
request: a completed item's tabs close on their own.

**Phase 4 is not optional and is not polish.** It is P1 alongside US1 because the flag this feature
narrows exists to preserve the only evidence a failed launch leaves. Shipping US1 without US2's
tests would mean the constraint holds today by accident and could be lost by any later refactor of
the candidate set with the suite still green.

**Phase 5 is cheap and worth having**: two tests that pin a property currently true only because of
how the rule is worded. If someone later rewrites the rule in terms of "which command ended the
session", these fail and say why.

**Phase 6 includes a correction, not just documentation.** T027 fixes a sentence in the published
guide that has been false since the previous feature shipped.
