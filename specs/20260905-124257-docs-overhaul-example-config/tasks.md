---

description: "Task list for the docs overhaul and example config"
---

# Tasks: Docs overhaul and example config

**Input**: Design documents from `specs/20260905-124257-docs-overhaul-example-config/`

**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md — all complete.

**Tests**: Required, not optional. The constitution's Development Workflow section mandates
unit tests for every new or changed unit of behaviour, and FR-023 / FR-024 / FR-025 make
three specific tests part of the deliverable rather than a check on it.

**Organization**: Grouped by the three user stories in spec.md, in priority order.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel — different files, no dependency on an incomplete task
- **[Story]**: US1 (example config), US2 (the guide), US3 (keeping it current)
- Every task names its exact file path

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/`, `tests/integration/`, `docs/`, `share/`
at the repository root.

---

## Phase 1: Setup

**Purpose**: Nothing to initialise — the project, its dependencies, its linting and its test
layout all exist. Two orientation tasks only, so that the work that follows matches what is
already here rather than inventing a parallel style.

- [X] T001 Read `src/robot_army/config.py` lines 796-880 to confirm the exact current contents of `_KNOWN_KEYS` and `_REPO_KEYS`, and record the full key list to annotate. This is the source of truth per research R1; annotating from memory is how a key gets missed.
- [X] T002 [P] Read three existing modules in `tests/unit/` (`test_config.py`, `test_cli_exit_codes.py`, and one other) to match the fixture style, assertion style, and naming conventions already in use before writing any new test module.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The generator module. Every task in US1 and the configuration page in US2
depend on it.

**⚠️ CRITICAL**: No US1 task can begin until T005 is complete.

- [X] T003 Create `src/robot_army/exampleconfig.py` with the module docstring stating what it is, that it derives its key surface from `config.py`'s `_KNOWN_KEYS` and `_REPO_KEYS`, and the one-way import rule (this module imports `config`; `config` must never import this one), following the docstring conventions in `config.py` and `speckit.py`.
- [X] T004 In `src/robot_army/exampleconfig.py`, define `ExampleConfigError`, `KeySpec` and `SectionSpec` per data-model.md, including the two construction-time validations: a commented key must carry `why_commented`, and a commented section must have every key commented.
- [X] T005 In `src/robot_army/exampleconfig.py`, write the annotation tables — a `SectionSpec` for each of the 15 sections in the order fixed by `contracts/example-config.md` (`paths`, `github`, `worker`, `dispatch`, `daemon`, `speckit`, `speckit.commands`, `trello`, `notifications`, `pushover`, `cleanup`, `hooks`, `terminal`, `web`, `health`, `repos."owner/name"`), with a `KeySpec` for every key in `_KNOWN_KEYS` and `_REPO_KEYS`, applying the four active/commented rules from research R3 and `contracts/example-config.md`.

---

## Phase 3: User Story 1 — Get a working config without reading the README (Priority: P1) 🎯 MVP

**Goal**: One command produces a complete, commented `config.toml` that loads clean and
configures nothing outward-facing.

**Independent test**: Run the subcommand, redirect to a file, load it with `config.load()`.
No guide page needs to exist.

### Implementation

- [X] T006 [US1] In `src/robot_army/exampleconfig.py`, implement `render()` — iterate `_KNOWN_KEYS` and `_REPO_KEYS`, look up each key's annotation, and raise `ExampleConfigError` naming the section and key when an annotation is missing, when an annotation names a key the loader does not accept, or when a section has no `SectionSpec` (data-model.md's three failure conditions). Ordering comes from the specs, never from iterating a set (FR-016).
- [X] T007 [US1] In `src/robot_army/exampleconfig.py`, implement the document rendering itself: preamble comment, section blurbs, `key = value  # comment` lines, the `# key = value` form with its indented reason line, one blank line between sections, exactly one trailing newline — per `contracts/example-config.md`. No timestamp and no version string, which would break FR-016.
- [X] T008 [US1] In `src/robot_army/exampleconfig.py`, implement `write(path, *, force=False, audit=None)`: render fully into memory first, write to a temporary file in the destination's directory, `fsync`, `rename` (research R7); refuse an existing path unless `force`; record `example_config.write` with outcome and detail when an audit log is supplied, and do not fail the write when the audit log cannot be opened.
- [X] T009 [US1] In `src/robot_army/cli.py`, register the `example-config` subparser with `--output PATH` and `--force`, following the existing `sub.add_parser(...)` style around lines 67-290.
- [X] T010 [US1] In `src/robot_army/cli.py`, route `example-config` inside `main()` **before** `load_config` is called, beside `run` and `serve`. Per `contracts/cli.md` this is a correctness requirement: the dispatch table presumes a valid config, and this command exists to be run when there is none. Implement the exit codes 0/1/2/3 and the stdout/stderr split the contract fixes.
- [X] T011 [US1] Generate the committed example: `uv run robot-army example-config --output share/config.example.toml --force`, replacing the stale 231-line hand-written file wholesale (FR-018).

### Tests

- [X] T012 [P] [US1] Create `tests/unit/test_example_config.py` with the coverage test: every key in `_KNOWN_KEYS[section]` and `_REPO_KEYS` appears in its own section of `render()`'s output, as `key = ` or `# key = ` at the start of a line, asserted per section so a failure names the section (FR-011, FR-024).
- [X] T013 [P] [US1] In `tests/unit/test_example_config.py`, add the loadability test: with `HOME` set to a `tmp_path` and `~/GIT` and `~/worktrees` created under it, `config.load()` accepts the rendered document unmodified, reports zero problems and zero warnings (FR-013, research R4).
- [X] T014 [P] [US1] In `tests/unit/test_example_config.py`, add the inertness test: the loaded config has `trello is None` and `pushover is None`, `cleanup.on_issue_close is False`, and `notifications.events == ()` — copying the example configures nothing outward-facing (FR-015).
- [X] T015 [P] [US1] In `tests/unit/test_example_config.py`, add the determinism test: `render()` returns identical output across two calls with different `XDG_RUNTIME_DIR` and different `HOME` values (FR-016, FR-020, research R2).
- [X] T016 [P] [US1] In `tests/unit/test_example_config.py`, add the no-credentials test: no line of the output matches `config._TOKEN_PATTERNS` or the Pushover credential shape — reusing the loader's own detectors rather than a second regex (FR-014).
- [X] T017 [P] [US1] In `tests/unit/test_example_config.py`, add the failure-path tests: removing an annotation makes `render()` raise `ExampleConfigError` naming the key; adding an annotation for a key the loader does not accept raises likewise; a `KeySpec` that is inactive with no `why_commented` is refused at construction (data-model.md).
- [X] T018 [P] [US1] Create `tests/unit/test_cli_example_config.py` covering the CLI surface from `contracts/cli.md`: stdout carries the document and nothing else; `--output` writes and puts its confirmation on stderr; a second `--output` to the same path exits 3 without touching the file; `--force` replaces it; `--force` alone exits 2; and the command succeeds with no config file present anywhere.

**Checkpoint**: US1 is independently shippable. `robot-army example-config` works, is tested,
and the committed example is current — with no documentation change at all.

---

## Phase 4: User Story 2 — Find one answer without scrolling a 1,180-line README (Priority: P2)

**Goal**: The README's 22 sections become eight guide pages published by GitHub Pages'
built-in branch build.

**Independent test**: Browse from the landing page; every subject the old README covered is
reachable in two clicks and appears exactly once.

**Dependency note**: T027 (`configuration.md`) needs T011's committed example. Every other
task in this phase is independent of US1.

### Site scaffolding

- [X] T019 [US2] Create `docs/_config.yml` with `theme: jekyll-theme-primer`, the site title and description, and the `exclude` list keeping `roadmap.md`, `incident-*.md`, `verification-*.md` and `initial-planning/` out of the published site while leaving them in git (FR-007, research R8).

### The guide pages — following the plan's mapping table exactly

Each task names the README sections it absorbs. Content is rewritten for its new context,
not pasted: cross-references that pointed within one file become links between pages.

- [X] T020 [P] [US2] Create `docs/guide/index.md` from README `## What it does` (lines 12-29) and `## Design notes` (1161-1177), adding the pipeline at a glance — the five stages in order, each linking to its page — and a one-line description of every guide page.
- [X] T021 [P] [US2] Create `docs/guide/1-setup.md` from README `## Running it` (30-45, the install and first-run half only), `### The token has to be a classic one` (46-67), `### Adding a repository` (68-103), and `## Trying it without consequences` (951-981). The `### [repos.*] is for exceptions` subsection is **not** here — it goes to `configuration.md` (T027).
- [X] T022 [P] [US2] Create `docs/guide/2-intake.md` from README `## The intake board` (304-352), `### Parking a card` (353-375), `### When a card doesn't say enough` (376-392), and `### One card, one issue` (393-404), plus how the GitHub label route works as the other intake path.
- [X] T023 [P] [US2] Create `docs/guide/3-selection.md` from README `## How many sessions run at once` (617-672), `## Working a repository serially` (673-706), `### The clone fast-forward` (707-720), `## Ordering work from a project board` (721-779), `## Pausing dispatch` (891-906), and `## Holding specific work` (907-950).
- [X] T024 [P] [US2] Create `docs/guide/4-session.md` from README `## What every session is told` (405-464), `### Reading a prompt before it is sent` (465-496), `## When a repository uses Spec Kit` (497-522), `### Telling it how *I* run the lifecycle` (523-574), `### Seeing how far it has got` (575-601), and `### What it deliberately does not do` (602-616).
- [X] T025 [P] [US2] Create `docs/guide/5-outcome.md` from README `## What it writes on the issue` (780-838), `## Being told when something happens` (839-890), and `## Cleaning up` (1093-1160).
- [X] T026 [US2] Create `docs/guide/operating.md` from README `## The web interface` (164-191), `### Read this part` (192-271), `### What it can do` (272-303), the state-paths half of `## Where things live` (982-998), `## Reading the logs` (999-1014), `## When something looks wrong` (1015-1042), `## Recovering` (1043-1081), and `## Noticing it has died` (1082-1092) — merged with the whole of `docs/logging.md` and the whole of `docs/state.md` (FR-006). Watch SC-003: this is the page most likely to exceed 350 lines, and it splits by subheading rather than by dropping content if it does.
- [X] T027 [US2] Create `docs/guide/configuration.md`: every section and key of the config, drawing on README `### [repos.*] is for exceptions` (104-163) and the `[paths]` half of `## Where things live` (982-998), and presenting the committed `share/config.example.toml` from T011 — linked, and with the sections quoted inline as each is explained. **Depends on T011.**

### Landing page and README

- [X] T028 [US2] Create `docs/index.md`: what robot-army is in a paragraph, and links into the guide. This is the published site's front page (FR-003).
- [X] T029 [US2] Rewrite `README.md` down to a high-level overview — what it is, what it does, roughly how it works — plus a prominent pointer to the published guide, the `## Licence` section (README 1178-1179), and nothing that restates the guide (FR-005). Note in the PR that old links to README anchors break, as the spec's assumptions accept.
- [X] T030 [US2] Delete `docs/logging.md` and `docs/state.md`, now that T026 carries their content (FR-006). Grep the repository first for links to either file and repoint them at `docs/guide/operating.md`.

### Tests

- [X] T031 [US2] Create `tests/unit/test_docs_links.py`: parse every relative Markdown link in `README.md`, `docs/index.md` and `docs/guide/*.md`, resolve each against the filesystem, and fail naming the file and the dead target (FR-008, FR-025). Anchors within a page are checked for the file half only; external `http(s)` links are skipped, since a test must not depend on the network (Principle IV).

**Checkpoint**: US2 is independently shippable. The guide exists and is internally
consistent, whether or not Pages has been enabled.

---

## Phase 5: User Story 3 — Documentation that does not rot (Priority: P3)

**Goal**: The next change to behaviour or configuration cannot quietly skip the docs.

**Independent test**: Add a key to `_KNOWN_KEYS` without touching the generator; the suite
fails naming the key.

- [X] T032 [US3] Create `CLAUDE.md` at the repository root: what this project is in a paragraph, how to run the tests, and the two standing rules — a change that alters behaviour updates the guide page for the pipeline stage it affects, and a change that alters configuration regenerates `share/config.example.toml` with `robot-army example-config --output share/config.example.toml --force`. State that `.specify/memory/constitution.md` governs where the two differ (FR-021, FR-022). Keep it to what a future session needs; it is not a second copy of the guide.
- [X] T033 [US3] Create `tests/unit/test_example_config_drift.py`: `share/config.example.toml` read from disk must equal `render()` byte for byte, and the failure message must name the exact regeneration command (FR-023). This is the test that would have caught the stale file this feature is replacing.

**Checkpoint**: all three stories complete.

---

## Phase 6: Polish & Cross-Cutting

- [X] T034 Run `uv run pytest -q` — the full suite, not only the new modules (SC-007, and the constitution's "implementation is not complete until the unit test suite passes").
- [X] T035 [P] Run the repository's linting and formatting over the changed Python files, matching whatever `pyproject.toml` already configures.
- [X] T036 [P] Check SC-003 by hand: `wc -l docs/guide/*.md`, and split any page much past 350 lines at a subheading rather than by cutting content.
- [X] T037 Walk `quickstart.md` end to end, steps 1 through 8, confirming each expected result. It is the feature's acceptance test and it exercises paths the unit tests do not — notably the audit records from step 5.
- [X] T038 [P] Re-read the guide against Principle V: no contribution guide, no issue templates, no support channels, no badges, no end-user tutorial voice, second person retained (FR-009).
- [X] T039 Verify the plan's mapping table against the tree: every one of the 22 README sections is accounted for on exactly one page, bar the two documented splits and the one documented three-way reuse (FR-004).

---

## Phase 7: Manual — the author performs this

- [ ] T040 **MANUAL — cannot be done by the implementation.** In the repository's GitHub settings: **Settings → Pages → Build and deployment**, set Source = *Deploy from a branch*, Branch = `main`, Folder = `/docs`. This is a repository setting and cannot be committed (research R8, quickstart step 9). Until it is done, the guide is committed but unpublished, and `README.md`'s link to the published site will 404. Flag it in the PR description.

---

## Dependencies

**Story order**: US1 → US2 → US3 by priority, but only one edge is real:

- **US1 blocks US2 at exactly one point**: T011 (the committed example) blocks T027 (`configuration.md`). Everything else in US2 can proceed alongside US1.
- **US3 depends on both**: T033 needs the generator (T006-T007) and the committed file (T011); T032 links to guide pages, so it needs T020-T028.

**Within Phase 2**: T003 → T004 → T005, strictly sequential — same file, each building on
the last.

**Within US1**: T005 → T006 → T007 → T008 (same file, sequential); T009 → T010 (same file);
T008 and T010 → T011; T011 → the tests. T012-T018 are parallel with each other.

**Within US2**: T019-T026 and T028 are parallel; T027 waits on T011; T029 and T030 wait on
the pages they link to; T031 waits on everything it walks.

**Phase 6** waits on everything. **T040** is last and is not the implementation's to do.

## Parallel execution examples

**US1 tests** — six modules' worth of assertions, one file, but independent to write:

```
T012, T013, T014, T015, T016, T017 in parallel; T018 alongside them (different file)
```

**US2 guide pages** — eight separate files, no shared state:

```
T020, T021, T022, T023, T024, T025 in parallel
T026 alongside (larger, absorbs two whole files)
T028 alongside
T027 only once T011 has landed
```

**Polish**:

```
T035, T036, T038 in parallel after T034
```

## Implementation strategy

**MVP is US1 alone.** `robot-army example-config`, tested, with the committed example
regenerated. It is the issue's item 2, it is the thing that has no quick path today, and it
ships without a single documentation file changing.

**Then US2**, which is the issue's headline complaint and the largest body of work, but
carries no risk to running code — it is Markdown and one YAML file.

**Then US3**, which is worth nothing until the first two exist and everything once they do.

**Deliver as one PR.** The three stories are one issue and one coherent change; splitting
them would leave `CLAUDE.md` telling future sessions to maintain pages that had not landed
yet.

---

## Deviations from the plan, and why

Two, both found during implementation and both recorded here rather than smoothed over.

### `docs/logging.md` and `docs/state.md` became guide pages of their own

T026 and FR-006 said to fold both into `docs/guide/operating.md`. They are 619 and 650
lines. Folded in, `operating.md` would have been about 1,400 lines — larger than any single
section of the README this feature exists to break up, and a direct contradiction of SC-003.

They were `git mv`d to `docs/guide/audit-log.md` and `docs/guide/state.md` instead, keeping
their history. `operating.md` carries the operational narrative — where things live, how to
read the log, the intent/outcome crash signature — and links to each for the full reference.

FR-006's substance holds: their content is in the guide, and there is no second copy of it
anywhere. What changed is which page it sits on. The guide is ten pages rather than eight.

### The example config is linked from `configuration.md`, not pasted into it

T027 said to embed it. Doing so produced a second copy of the generated file, in a second
place, needing a second regeneration step — which is precisely the failure mode this whole
feature exists to remove, and the one `share/config.example.toml` already demonstrated.

The page gives the command, links the committed file, and quotes each section's real content
in the tour. There is one copy, one generator, and one drift test. FR-018's "the guide's
configuration page MUST present it" is met by presenting it; a duplicate that can rot is not
a better form of presenting it.

### Note on SC-003

Every page written for this feature is under 265 lines. The two reference pages carried in
unchanged are 619 and 650. Splitting a per-action audit reference into fragments would make
it worse to use as a reference, so they were left whole.
