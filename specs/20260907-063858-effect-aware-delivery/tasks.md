# Tasks: The delivery block follows the effect level

**Input**: Design documents from `specs/20260907-063858-effect-aware-delivery/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/delivery-forms.md](contracts/delivery-forms.md),
[quickstart.md](quickstart.md)

**Tests**: required, not optional. The constitution's Development Workflow section demands unit
tests for every new or changed unit of behaviour, and in this feature the *behaviour is prose* —
`tests/unit/test_delivery_prompt.py` exists precisely because nothing else can hold a paragraph
to its reasons.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 (the two forms), US2 (the words), US3 (the preview)

---

## Phase 1: Foundational (blocks every user story)

These are the shape changes. Nothing else compiles or reads sensibly until they land, and they
are all in files that later tasks edit, so none of them is parallel with another.

- [X] T001 Add the `Delivery` frozen dataclass (`name`, `text`, `describe_name()`) to `src/robot_army/prompt.py`, per [data-model.md](data-model.md)
- [X] T002 Rename `DELIVERY` to `DELIVERY_PUSH` in `src/robot_army/prompt.py`, wrapping the existing text unchanged in a `Delivery(name="push", …)` — byte-for-byte, no reflow (SC-003)
- [X] T003 Add `DELIVERY_LOCAL` to `src/robot_army/prompt.py` with the local-only text from [contracts/delivery-forms.md](contracts/delivery-forms.md), and a module docstring paragraph explaining why there are now two forms and what the sentence about instructions above is for (research R5, R8)
- [X] T004 Make `delivery` a required keyword argument of `prompt.compose()` in `src/robot_army/prompt.py` and place `delivery.text` where `DELIVERY` was placed, with the docstring saying why it is not defaulted (research R4)
- [X] T005 Add `Boundaries.delivery: prompt.Delivery` and select it in `wire()` in `src/robot_army/effects.py`; document the level→form table beside `REAL_AT` and say why it is its own selection rather than a lookup of `issue_writer` (data-model.md)
- [X] T006 Add `"delivery"` to `Boundaries.describe()` in `src/robot_army/effects.py` so `daemon.start` records which form the daemon will hand every session

**Checkpoint**: `uv run pytest` fails only where a test still names `prompt.DELIVERY` or calls
`compose()` without a form — that failure list is the call-site inventory for Phase 2.

---

## Phase 2: User Story 1 — a rehearsal at a reduced level does not reach GitHub (P1) 🎯 MVP

**Goal**: a session dispatched below `live` is told to commit and stop; a session dispatched at
`live` is told exactly what it is told today.

**Independent test**: compose the prompt for one issue at `live` and at `no-remote` and diff the
delivery section; run a dispatch at `no-remote` and confirm `git ls-remote` shows no new branch.

- [X] T007 [US1] Pass `boundaries.delivery` to `prompt.compose()` in `build_launch_plan` in `src/robot_army/dispatch.py`
- [X] T008 [P] [US1] Parametrise the shared assertions in `tests/unit/test_delivery_prompt.py` over both forms — whose rules, the feature branch, "when there is work to deliver", no upward pointer at the branch, the mechanism rule, reading including live systems, no override granted, precedence asserted, enforcement disclaimed, no format placeholder
- [X] T009 [P] [US1] Add the `push`-only assertions to `tests/unit/test_delivery_prompt.py`: push to `origin`, open a pull request, "not a finished job", "arriving as commits and a pull request", "a limit on one thing", and that the text is unchanged from milestone 012's
- [X] T010 [P] [US1] Add the `local`-only assertions to `tests/unit/test_delivery_prompt.py`: commit and stop, no push, no pull request, no comment on the issue, nothing to any remote, where the work is read instead, the two named limits, and that it does not forbid committing
- [X] T011 [P] [US1] Add the test in `tests/unit/test_delivery_prompt.py` for the sentence about instructions above — present in `local`, absent in `push`, and scoped to outward writes (FR-006a, research R8)
- [X] T012 [P] [US1] Add size-budget tests in `tests/unit/test_delivery_prompt.py`: `push` under 1,800 characters, `local` under 2,200, each with its reason in the docstring
- [X] T013 [P] [US1] Test in `tests/unit/test_effects.py` that `wire()` selects `DELIVERY_PUSH` at `live` and `DELIVERY_LOCAL` at `plan`, `local` and `no-remote`, parametrised over the whole enum so a fifth level cannot be added without deciding
- [X] T014 [P] [US1] Test in `tests/unit/test_effects.py` that `describe()` carries `delivery` as `"push"`/`"local"`, and that `Boundaries` stays frozen
- [X] T015 [P] [US1] Test in `tests/unit/test_effects.py` that `prompt.py` and `dispatch.py` still name no effect level — i.e. that the existing allowlist test covers the new selection rather than being widened for it
- [X] T016 [US1] Update `tests/unit/test_speckit_prompt.py` and `tests/unit/test_speckit_dispatch_prompt.py` for the golden assembly now carrying a form, and add a case asserting the dispatch prompt's form follows the wired level
- [X] T017 [US1] Test in `tests/unit/test_prompt_fence.py` (or wherever determinism is held) that two composes at the same level differ only in the fence nonce, for both forms

**Checkpoint**: US1 is complete and testable on its own. The remaining stories change words and
one flag.

---

## Phase 3: User Story 2 — the words say what the ladder actually governs (P2)

**Goal**: a reader of the contract, the quickstart, the guide or the example configuration
learns what the level governs, what it asks, and that asking is not enforcing.

**Independent test**: read the four documents and check each states the limit of the guarantee.

- [X] T018 [P] [US2] Add the section to `specs/001-minimum-daemon/contracts/boundaries.md` saying the table governs these seams only, that a session launched through `SessionHost` runs as the same user with the same credentials and is outside them, and that the delivery form is the mitigation and not enforcement (FR-011)
- [X] T019 [P] [US2] Retitle quickstart scenario 3 in `specs/001-minimum-daemon/quickstart.md` and rewrite its expectation to say the check is on robot-army's own writes, adding the `git ls-remote` check for the session's (FR-012)
- [X] T020 [P] [US2] Rewrite the effect-level section of `docs/guide/1-setup.md`: what the ladder is enforced against, what a session below `live` is asked to do, that nothing stops one that ignores the request, and the row-by-row table updated to say so (FR-013)
- [X] T021 [P] [US2] Update `docs/guide/4-session.md` to describe both delivery forms, the level that selects each, the instruction-above case with this repository's own `[speckit] implement` as the example, and that `.claude/robot-army.md` still outranks everything else it says
- [X] T022 [US2] Reword the `effect_level` comment in `SECTIONS` in `src/robot_army/exampleconfig.py` so it describes what robot-army's own boundaries do rather than what the machine may touch (FR-014)
- [X] T023 [US2] Regenerate the committed example with `uv run robot-army example-config --output share/config.example.toml --force` (depends on T022; `tests/unit/test_example_config_drift.py` is the check)

**Checkpoint**: the documents and the code now say the same thing, and the drift test passes.

---

## Phase 4: User Story 3 — the preview is still the prompt (P3)

**Goal**: `robot-army prompt` composes the form for the level in effect, can be asked about
another level, and records which form it composed.

**Independent test**: preview one issue at two levels; diff; read the audit record.

- [X] T024 [US3] Pass `ctx.boundaries.delivery` to `prompt.compose()` in `prompt_preview` in `src/robot_army/operations.py`
- [X] T025 [US3] Add `"delivery": <form name>` to the `prompt.preview` audit detail in `src/robot_army/operations.py`, beside `instructions` and `speckit`, and to `result.data` with it (FR-010)
- [X] T026 [US3] Add `--effect-level` to the `prompt` parser in `src/robot_army/cli.py` and pass it to `operations.build_context` for the verbs that define it (FR-008a)
- [X] T027 [P] [US3] Test in `tests/unit/test_prompt_preview.py` that the preview composes the wired level's form, that the record carries `delivery`, and that a preview and a dispatch at the same level produce the same delivery section
- [X] T028 [P] [US3] Test in `tests/unit/test_cli*.py` (the file that already covers argument routing) that `prompt --effect-level no-remote` builds a context at that level and that the flag's absence leaves the configured level in force

**Checkpoint**: all three stories complete.

---

## Phase 5: Polish and cross-cutting

- [X] T029 Update `docs/guide/audit-log.md` for the two records that gained a field — `prompt.preview`'s `delivery`, and `daemon.start`'s `boundaries.delivery` — and say why neither carries prompt text
- [X] T030 Run `uv run pytest` and fix whatever the change surfaced; the suite must pass in full (SC-006)
- [X] T031 Run `uv run ruff check` and `uv run ruff format --check` (or this repository's configured equivalent) over the changed files
- [X] T032 Walk [quickstart.md](quickstart.md) end to end and correct it where reality disagrees; a quickstart that was never run is a claim, not a verification

---

## Dependencies

```text
Phase 1 (T001–T006)  ──┬── Phase 2 US1 (T007–T017)   ← MVP
                       ├── Phase 3 US2 (T018–T023)   (T022 → T023)
                       └── Phase 4 US3 (T024–T028)
                                   └── Phase 5 (T029–T032)
```

- Phase 1 blocks everything: every later task touches a name it creates.
- US2 depends on Phase 1 only in that its prose describes the behaviour Phase 1 introduces. Its
  document edits are otherwise independent of US1 and US3 and of each other.
- Within Phase 5, T030 is the gate; T029 and T031 may precede it in any order.

## Parallel opportunities

- T008–T015 are eight test tasks across two files with no shared edits; T008–T012 all touch
  `tests/unit/test_delivery_prompt.py` and should be written together in one pass rather than
  raced, so treat `[P]` there as "independent of the other files", not "eight editors".
- T018–T021 are four different documents and are genuinely parallel.

## Implementation strategy

Land Phase 1 and Phase 2 first: that is the MVP and it is the whole of the reported bug. Phase 3
is the half of the issue that no code can fix and must not be dropped for being prose — a reader
who still believes `no-remote` is enforced against the session has the same surprise waiting.
Phase 4 keeps the preview honest about the change the other two phases made.
