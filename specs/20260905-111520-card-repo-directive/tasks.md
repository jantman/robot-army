---

description: "Task list for: Naming the repository outright on a card"
---

# Tasks: Naming the repository outright on a card

**Input**: Design documents from `/specs/20260905-111520-card-repo-directive/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/repo-declaration.md](contracts/repo-declaration.md)

**Tests**: required. The constitution's Development Workflow section makes unit tests
mandatory for every new or changed unit of behaviour, and additionally requires failure-path
tests for code parsing external input — which is exactly what this feature is.

**Organization**: grouped by user story. Each story's phase is a complete, independently
testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: which user story the task serves (US1, US2, US3)

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/guide/`.

---

## Phase 1: Setup

- [X] T001 Confirm the baseline is green before touching anything: run `uv sync && uv run pytest` from the repository root and record that it passes, so any failure later in this feature is known to be this feature's

---

## Phase 2: Foundational (blocking — every user story needs these)

These three changes are what every story below is built on. Nothing in Phase 3 or later can
be tested until they exist.

- [X] T002 Add the `source: str = "scan"` field to the frozen `Resolution` dataclass in `src/robot_army/intake.py`, with a docstring line saying it names the *origin of the outcome* — `"declaration"` when the card's `robot-army:` lines produced it, resolved or held, and `"scan"` when the ordinary text scan did — and that it defaults so every existing construction site keeps meaning what it meant (data-model.md)
- [X] T003 Add the module-level `_DECLARATION` pattern to `src/robot_army/intake.py`, sited beside `_URL_REF`/`_BARE_REF`/`_PATH_REF`: `^[ \t]*robot-army[ \t]*:[ \t]*(\S+)[ \t]*$` compiled with `re.IGNORECASE | re.MULTILINE`, carrying a docstring that explains why each clause is what it is — both anchors are the whole of "and nothing else on the line", `[ \t]` rather than `\s` because `\s` matches `\n` and would straddle a line break under `MULTILINE`, and `\S+` because deciding *what* the reference is belongs to the recognisers that already exist (research R2)
- [X] T004 Add `_declared_references(text: str) -> list[str]` to `src/robot_army/intake.py`, which removes every backtick from the text and returns `_DECLARATION.findall` over it in source order without deduplicating, with a docstring stating why backticks are stripped: the documentation renders the line in code style, Trello renders markdown so the backticks vanish once the card is saved, and a backtick cannot occur in any of the three reference forms — so removing them is safe and is the difference between the line working and failing with no visible cause (research R2)

**Checkpoint**: `Resolution` carries a source, and a card's text can be turned into the list of
references its declaration lines give. Nothing consults it yet.

---

## Phase 3: User Story 1 — A card that names several repositories can still say which one it means (Priority: P1) 🎯 MVP

**Goal**: a card mentioning any number of onboarded repositories files its issue in the one
its `robot-army:` line names, with no other edit to the card.

**Independent test**: write a card naming two onboarded repositories, confirm it is held, add
the line, confirm the next evaluation files the issue in the named repository.

### Implementation

- [X] T005 [US1] Add `_resolve_declarations(references, onboarded) -> Resolution | None` to `src/robot_army/intake.py`: return `None` when there are no references, so the caller falls through to the text scan unchanged; otherwise resolve each reference through the existing `_URL_REF`/`_BARE_REF`/`_PATH_REF` recognisers and the existing `_offer`/`_key_for_path` onboarding filter, and return a `Resolution` with `source="declaration"` per the outcome table in [contracts/repo-declaration.md](contracts/repo-declaration.md) — one distinct key with every reference resolving is a resolution, and anything else is a hold (FR-006, FR-007, FR-008, FR-009)
- [X] T006 [US1] Wire the short-circuit into `resolve_repository` in `src/robot_army/intake.py`: read the onboarded set once as it does today, call `_declared_references` on `f"{title}\n{body}"`, and when `_resolve_declarations` returns a verdict, return it without running the three text scanners at all — with a docstring paragraph saying why it overrides rather than breaks a tie (an override that only applied when the system was already confused could not be tested by the author, who cannot see whether the system is confused until the card is held — research R4)
- [X] T007 [US1] Set `source="scan"` explicitly on every `Resolution` the text-scan path in `src/robot_army/intake.py` returns, rather than relying on the default alone, so the two paths are legible side by side (FR-010)

### Tests

- [X] T008 [P] [US1] In `tests/unit/test_repo_resolution.py`, using the existing `multi_config`/`resolve` fixtures, test that a card whose text names both onboarded repositories **and** carries `robot-army: jantman/demo` resolves to `jantman/demo` with `source == "declaration"` and `candidates == ("jantman/demo",)` (FR-006, US1 scenario 1)
- [X] T009 [P] [US1] In `tests/unit/test_repo_resolution.py`, test that a card naming exactly one onboarded repository in its text and a *different* one on its declaration line resolves to the declared one — the line decides even when there was no ambiguity to resolve (US1 scenario 3)
- [X] T010 [P] [US1] In `tests/unit/test_repo_resolution.py`, test that a card with no declaration resolves byte-for-byte as it does today: the existing adversarial cases keep passing, and a newly added assertion confirms `source == "scan"` on both a resolvable card and an unresolvable one (FR-010, SC-004)
- [X] T011 [P] [US1] In `tests/unit/test_repo_resolution.py`, test the declaration inside a pasted log: a body containing a realistic traceback plus a line reading `robot-army: someone/not-onboarded` resolves to nothing and is held, proving the onboarding filter still gates the new path (FR-005, SC-002)
- [X] T012 [P] [US1] In `tests/integration/test_card_to_issue.py`, extend the end-to-end walk with a card whose description names two onboarded repositories and carries a declaration, asserting the issue is filed in the declared repository and that the filed issue's body still contains the declaration line verbatim inside the quoted block (FR-015, US1 scenario 4)

**Checkpoint**: the reported problem is solved. A multi-repository card can be filed without
editing away the context that made it worth writing.

---

## Phase 4: User Story 2 — The line accepts the same three ways of naming a repository (Priority: P2)

**Goal**: whichever of the three spellings the author has to hand goes after `robot-army:`
and works.

**Independent test**: write the same multi-repository card three times, once with each
spelling, and confirm all three file the issue in the same repository.

**Note**: T005 already reuses the three recognisers, so this phase is mostly the tests that
pin that reuse down — plus the grammar tolerances, which have nowhere else to be tested.

### Tests

- [X] T013 [P] [US2] In `tests/unit/test_repo_resolution.py`, test all three reference spellings on the declaration line — `jantman/demo`, `https://github.com/jantman/demo`, and the clone's own path — each resolving to the same repository from an otherwise ambiguous card (FR-004, US2 scenarios 1–3)
- [X] T014 [P] [US2] In `tests/unit/test_repo_resolution.py`, test the URL variants the existing recogniser already accepts, arriving through the declaration: no scheme, a `www.` prefix, and a trailing `.git` (FR-004)
- [X] T015 [P] [US2] In `tests/unit/test_repo_resolution.py`, test that a path *inside* a clone (`<clone>/src/thing.py`) and a `~`-relative spelling of the clone both select it through the declaration, matching what `_key_for_path` already does for the text scan (FR-004)
- [X] T016 [P] [US2] In `tests/unit/test_repo_resolution.py`, test the grammar tolerances: mixed case (`Robot-Army:`), leading and trailing whitespace, whitespace around the colon, and the whole line wrapped in backticks — each still recognised (FR-002)
- [X] T017 [P] [US2] In `tests/unit/test_repo_resolution.py`, test the negatives from [contracts/repo-declaration.md](contracts/repo-declaration.md): `see robot-army: jantman/demo for context` is prose and resolves by the text scan with `source == "scan"`; `robot-army: jantman/demo (the new one)` is not a declaration; a bare `robot-army:` is treated as absent; and `robot-army jantman/demo` without a colon is not one either (FR-003)
- [X] T018 [P] [US2] In `tests/unit/test_repo_resolution.py`, test that a declaration naming a repository that is onboarded but has **no** `[repos.*]` section resolves — the `multi_config` fixture already provides one, and onboarding rather than configuration is what makes a repository selectable everywhere else (spec Edge Cases)

**Checkpoint**: no spelling that works elsewhere on the card fails on the line.

---

## Phase 5: User Story 3 — When the line does not work, the card says so specifically (Priority: P3)

**Goal**: a card held because of its declaration is told what its own line said and what was
wrong with it, and the durable record says how the decision was reached.

**Independent test**: write a card whose line names a non-onboarded repository, and confirm
the held reason and the card comment name the line's own text rather than saying no
repository was identified.

### Implementation

- [X] T019 [US3] Write the two held reasons in `_resolve_declarations` in `src/robot_army/intake.py`, matching [contracts/repo-declaration.md](contracts/repo-declaration.md) exactly: a reference that selected nothing quotes that reference and lists the onboarded repositories; disagreeing declarations say more than one line was given and name the repositories they selected (FR-011, FR-012)
- [X] T020 [US3] Extend `_needs_info_comment` in `src/robot_army/intake.py` so the instruction on the card describes the line — its shape and that it must be the whole line — while keeping the existing "no other action is needed" close, so a card held for any reason carries an instruction that is enough on its own (FR-013)
- [X] T021 [US3] Add `"source": resolution.source` to the `detail` of the `trello.evaluated` record in `evaluate_card` in `src/robot_army/intake.py` (FR-014, SC-005)

### Tests

- [X] T022 [P] [US3] In `tests/unit/test_repo_resolution.py`, test that a declaration naming a non-onboarded repository holds the card with a reason quoting that exact reference and listing the onboarded repositories, and does **not** fall back to the text scan even when the card's text names exactly one onboarded repository — the anti-fallback rule, which is the failure path this feature introduces (FR-009, FR-011, research R4)
- [X] T023 [P] [US3] In `tests/unit/test_repo_resolution.py`, test that two declarations naming two different onboarded repositories hold the card with the disagreement reason naming both, and that `candidates` carries both keys sorted (FR-007, FR-012)
- [X] T024 [P] [US3] In `tests/unit/test_repo_resolution.py`, test that two declarations naming the *same* repository by different spellings — `jantman/demo` and the clone path — resolve rather than being counted as two (FR-008, US3 scenario 3)
- [X] T025 [P] [US3] In `tests/unit/test_repo_resolution.py`, test the one-good-line-and-one-bad-one case: a card with a declaration selecting an onboarded repository and a second selecting nothing is **held**, with the reason naming the bad reference (spec Edge Cases, US3 scenario 1)
- [X] T026 [US3] In `tests/integration/test_card_needs_info.py`, alongside the existing held-card walks, test that a card held because of its declaration receives a card comment carrying that reason, and that a card whose declaration is then corrected resolves on the next poll with no further action — exercising the existing one-comment-per-distinct-reason machinery against the new reasons (FR-013, US3 scenario 4)
- [X] T027 [US3] In `tests/integration/test_card_to_issue.py`, which already asserts on the actions the audit log carries, test the `trello.evaluated` record's shape on three cards — one resolved by declaration, one held by declaration, one resolved by the text scan — asserting `detail["source"]` is `"declaration"`, `"declaration"`, and `"scan"` respectively (FR-014, SC-005)
- [X] T028 [P] [US3] In `tests/unit/test_ignored_lists.py`, test that a card carrying a valid declaration but parked in an ignored column is still returned as `ignored` and files nothing — a declaration says which repository, not whether to act (FR-016, spec Edge Cases)

**Checkpoint**: every way the line can fail produces a message that names what the author
actually wrote.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T029 [P] Update `docs/guide/2-intake.md`'s "When a card doesn't say enough" section: the declaration's shape, the three accepted spellings, that it overrides the rest of the card's text, and — the part worth the words — that a declaration naming something not onboarded holds the card rather than falling back, so the author never has to wonder whether a line was silently ignored (FR-017)
- [X] T030 [P] Update the `trello.evaluated` row in `docs/guide/audit-log.md` so it says the record now carries how the repository was chosen as well as which one, since a record's shape changed (CLAUDE.md's documentation rule)
- [X] T031 Confirm no configuration surface moved: `src/robot_army/config.py`'s `_KNOWN_KEYS` and `_REPO_KEYS` are untouched, and `uv run pytest tests/unit/test_example_config_drift.py` passes without regenerating `share/config.example.toml` (research R8)
- [X] T032 Run the full suite, `uv run pytest`, and confirm it passes — the constitution's bar for a feature being complete
- [X] T033 Walk [quickstart.md](quickstart.md)'s failure walks by hand or confirm each is covered by a test above, and fix the quickstart if any command or expected output has drifted from what the implementation actually does

---

## Dependencies & Execution Order

```text
Phase 1 (T001)
   ↓
Phase 2 — Foundational (T002 → T003 → T004)        BLOCKS everything below
   ↓
Phase 3 — US1  (T005 → T006 → T007, then T008–T012 in parallel)     🎯 MVP
   ↓
Phase 4 — US2  (T013–T018, all parallel; needs T005's reuse of the recognisers)
   ↓
Phase 5 — US3  (T019, T020, T021 — all in intake.py, so sequential; then T022–T028)
   ↓
Phase 6 — Polish (T029, T030 in parallel; T031–T033 after)
```

**Story independence**: US1 is deliverable alone and is the whole of the reported problem.
US2 adds no production code — T005 reuses the recognisers by construction, so US2 is the
tests that hold that reuse in place plus the grammar tolerances. US3 changes only the text
of reasons and the contents of one record; skipping it leaves a correct but unhelpful
system, which is why it is P3 and not P1.

**Sequential within a file**: T002–T007 and T019–T021 all edit `src/robot_army/intake.py` and
are therefore *not* parallel with each other, even where they are logically independent. The
`[P]` markers above are on test tasks in distinct files and on the two documentation pages.

## Parallel Execution Examples

```text
# After Phase 3's implementation lands (T005–T007):
T008, T009, T010, T011  — four cases in tests/unit/test_repo_resolution.py
T012                    — tests/integration/test_card_to_issue.py, a different file

# The whole of Phase 4:
T013, T014, T015, T016, T017, T018

# Phase 6's documentation:
T029  — docs/guide/2-intake.md
T030  — docs/guide/audit-log.md
```

## Implementation Strategy

**MVP is Phase 1 → Phase 2 → Phase 3.** That is nine tasks, and at the end of them the issue
that prompted this feature is closed: a card naming three onboarded repositories can name the
one it means and be filed.

Phases 4 and 5 are then worth doing in order, because the failure they cover is one the author
only meets *after* the MVP has taught them the line exists — a typo in a line they have
learned to trust is the worst moment to receive a message that says "name a repository".

---

## Implementation notes

Two things went differently from the plan, both recorded because they change what a later
reader should expect to find:

1. **The two existing held reasons were also edited.** T019 planned only the two *new*
   reasons, but a card held for naming nothing, or for naming two repositories in its text,
   is precisely the card the line was built for — and its message said nothing about the line.
   Both now end by naming it. `tests/integration/test_card_needs_info.py` had an assertion on
   the old comment wording (`"which repository"`); it now asserts on the shape of the line.

2. **`_key_for_reference` was not in the plan's file map.** T005 needed one more private
   helper than the plan named: resolving a reference in isolation is not the same call as
   scanning a whole card, because the URL and bare-slug patterns must not reach past the
   declaration's own line. It is four lines and it is what makes "the same three spellings"
   literally the same code.

Final state: `uv run pytest` — 3114 passed, 1 skipped (the pre-existing manual-board test).
`uv run ruff check src/ tests/` clean. No configuration surface touched, so
`share/config.example.toml` needed no regeneration.
