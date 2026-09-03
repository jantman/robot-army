---

description: "Task list for holding items and repositories out of dispatch"
---

# Tasks: Holding Items and Repositories Out of Dispatch

**Input**: Design documents from `specs/20260903-060639-dispatch-holds/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included and **not optional**. The constitution's Development Workflow requires unit
tests for every new or changed unit of behaviour, and requires *additionally* that persistence
and recovery logic and state machines carry tests for their failure and interruption paths.
This feature is persistence plus a change to the queue's decision precedence, so both clauses
bite. Two tests in particular are load-bearing rather than routine: **T008**, which proves the
cascade that makes FR-025 a database guarantee instead of a promise, and **T011**, which pins
`held`'s position in the precedence against every reason it must outrank.

**Organization**: Grouped by the spec's four user stories.

- **[US1]** hold one item that is not worth doing yet (P1)
- **[US2]** hold a whole repository's work (P1)
- **[US3]** see every hold in force, including ones holding nothing (P2)
- **[US4]** holds outlive the daemon (P1)

The split that makes these independent: **US1 delivers item holds end to end** — schema,
operation, both surfaces — and is a complete, shippable increment. US2 adds the repository
scope on top of the same machinery. US3 adds discoverability. US4 adds no production code at
all and is a verification phase; that is stated plainly rather than padded, because persistence
is a property of the table US1 already created, and the honest work is proving it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)

## Path Conventions

Single project: `src/robot_army/`, `tests/unit/` at the repository root.

---

## Phase 1: Setup

**Purpose**: Nothing to initialise. The project, its dependencies, and its lint and test
configuration already exist, and this feature adds **no dependency and no configuration key**
(plan.md, Technical Context).

- [X] T001 Confirm the baseline is green before changing anything: `uv run pytest` and `uv run ruff check` both pass on the current branch

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two tables, the value type, and the six accessors every story depends on. **No
product behaviour changes in this phase** — after it the tables exist, are empty, and no
dispatch decision differs from today.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add `SCHEMA_010_SQL`, `_migration_010`, and the `MIGRATIONS` entry in `src/robot_army/migrations.py` — the `item_holds` and `repo_holds` tables exactly as data-model.md specifies, carrying the comments that record why the target is the primary key (FR-004 as a constraint), why `ON DELETE CASCADE` is present (FR-025), and why no backfill is possible
- [X] T003 [P] Test migration 010 in `tests/unit/test_migrations.py`: it applies, is idempotent, advances `user_version` to 10, creates both tables with the expected columns, backfills nothing, and an interrupted run re-applies whole
- [X] T004 Add the `Hold` dataclass (`held_at`, `held_by`, frozen, slots) in `src/robot_army/models.py`, with the docstring noting it carries no target because both accessors return `{target: Hold}` — and deliberately **no** `ROW_TYPES` entry, since neither query returns whole rows through the generic factory
- [X] T005 Add `list_item_holds`, `list_repo_holds`, `set_item_hold`, `clear_item_hold`, `set_repo_hold`, and `clear_repo_hold` in `src/robot_army/db.py` per data-model.md — setters use `INSERT ... ON CONFLICT DO NOTHING` then read back, returning `(Hold, bool)` so a redundant hold reports the **existing** row with its **original** `held_at`; clearers return the removed `Hold` or `None`; both readers return an empty dict rather than raising
- [X] T006 [P] Test the six accessors in `tests/unit/test_holds.py`: empty tables read as empty dicts, a setter is idempotent and never refreshes `held_at`, each setter's `bool` distinguishes placed from already-held, and each clearer distinguishes removed-what from nothing-to-remove
- [X] T007 [P] Record the `include_simulated` exemption in `tests/unit/test_db_scope.py` following the `list_repo_projects` precedent: neither hold table has a `dry_run` column, and holds apply to simulated items **by design** (contracts/dispatch-policy.md) — so an `include_simulated` parameter here would be a filter that must never fire
- [X] T008 [P] Test the cascade in `tests/unit/test_holds.py`: hold a simulated item, run `db.purge_simulated`, and assert no orphan row survives in `item_holds` — proving FR-025 without `purge_simulated` being modified. Assert the same for a deleted `repos` row and `repo_holds`

**Checkpoint**: the schema is in place and every dispatch decision is byte-identical to before.

---

## Phase 3: User Story 1 — Hold one item (Priority: P1) 🎯 MVP

**Goal**: The author holds one queued item from either surface. It stays visible in its
position, marked held, is skipped on every pass, and dispatches normally once released.

**Independent test**: Queue two eligible items, hold the first, confirm the second dispatches
and the first does not, then release the first and confirm it dispatches.

- [X] T009 [US1] Add `HELD = "held"` to `HoldReason` in `src/robot_army/ordering.py`, positioned **directly below `PAUSED` and above `CAPACITY_UNOBSERVABLE`**, and extend the enum's docstring with the rank's justification per contracts/dispatch-policy.md — every reason below it names a fix that cannot work, and `capacity_unobservable`'s "the numbers are untrustworthy" argument does not reach a reason that is not a number
- [X] T010 [US1] In `src/robot_army/ordering.py`, have `plan` read `db.list_item_holds(conn)` **once for the whole plan** — beside `resolved`, `unfinished`, and `boards`, for the reason that module already states — pass it to `_hold_for`, and add the item-hold branch immediately after the `paused` check, returning `HoldReason.HELD` with a detail naming when it was held and by which surface
- [X] T011 [P] [US1] Test the precedence in `tests/unit/test_holds_ordering.py`: a held item reports `held` and not `capacity_unobservable`, `global_cap`, `repo_cap`, `awaiting_merge`, `not_onboarded`, `off_column`, or `preparation_failed` when those conditions also apply — and reports `paused` rather than `held` when dispatch is paused
- [X] T012 [P] [US1] Test purity and position in `tests/unit/test_holds_ordering.py`: `plan` writes nothing when holds exist, positions stay contiguous and total with a held item in the middle (FR-014), and holding then releasing an item returns it to the identical position (FR-013)
- [X] T013 [P] [US1] Test that `held` is **not** in `dispatch._GLOBAL_HOLDS` in `tests/unit/test_holds_ordering.py`, and that a pass with a held item at the head still dispatches the item behind it (FR-011) — the issue's actual scenario, and the assertion that would fail if `held` were ever promoted to a global hold
- [X] T014 [US1] Add `hold_item` and `unhold_item` in `src/robot_army/operations.py`, following `_set_pause`'s shape: one `db.transaction`, one `ctx.audit.action` pair (`hold.item` / `unhold.item`) carrying the target, whether a hold was already in force, and the resulting `held_at`/`held_by`; a missing item is `EXIT_FAILED` with `no work item with id <n>`; a redundant hold and a redundant release are both exit `0` reported no-ops (FR-004, FR-005)
- [X] T015 [P] [US1] Test both operations in `tests/unit/test_holds.py`: exit codes per contracts/cli.md, the redundant-hold message quoting the original time, the no-op release, the refusal for an unknown id, and that each writes its audit record — including for the refusal
- [X] T016 [US1] Add the `hold` and `unhold` verbs in `src/robot_army/cli.py` with a positional item id, and wire them in `_dispatch`
- [X] T017 [P] [US1] Test the verbs in `tests/unit/test_holds_surfaces.py` (exit codes, output, local-time rendering via `timefmt.local`) and add `hold` and `unhold` to the enumeration in `tests/unit/test_cli_exit_codes.py::test_every_web_control_has_a_terminal_verb_here`
- [X] T018 [US1] Add `POST /item/<id>/hold` and `POST /item/<id>/unhold` to `ROUTES` in `src/robot_army/web/server.py`, each declaring `terminal="hold"` / `terminal="unhold"`, implemented through `_perform` with `require_item` — and **not** `require_effect_agreement` or `require_daemon`, for the reasons contracts/web.md records
- [X] T019 [P] [US1] Test the routes in `tests/unit/test_holds_surfaces.py`: `303` back to the referring view carrying `include_simulated` in both directions, `404` for an unknown item, the audit record written before the action, cross-origin still refused, and that `test_web_routing`'s automatic terminal-parity enumeration passes
- [X] T020 [US1] Render the hold on the queue page in `src/robot_army/web/pages.py` — no new rendering path is needed for the reason line, since `row["hold"]`/`row["hold_detail"]` already cover it — and add a per-row hold-or-release control with **no confirmation page**, per contracts/web.md
- [X] T021 [P] [US1] Test the rendering in `tests/unit/test_holds_surfaces.py`: a held row shows the reason and detail, shows release rather than hold, and a held row is never omitted or moved

**Checkpoint**: US1 is independently shippable. Quickstart steps 1–3 pass. One tap on the queue
page or one terminal verb takes an item out of dispatch and puts it back.

---

## Phase 4: User Story 2 — Hold a whole repository (Priority: P1)

**Goal**: The author holds a repository. Every item from it — present and future — is skipped
until released, and every other repository keeps dispatching in the same pass.

**Independent test**: Queue items in two repositories, hold one, confirm only the other
dispatches, then introduce a *new* item in the held repository and confirm it too is held with
no further action.

- [X] T022 [US2] In `src/robot_army/ordering.py`, have `plan` also read `db.list_repo_holds(conn)` once per plan, and extend the `held` branch to compose the three detail shapes in contracts/dispatch-policy.md — item only, repository only, and **both**, where the detail names both holds and states that releasing one leaves the other in force (FR-017)
- [X] T023 [P] [US2] Test the three detail shapes in `tests/unit/test_holds_ordering.py`, and specifically that releasing the item hold while the repository hold stands leaves the item held with the reason now naming only the repository — the failure FR-017 exists to prevent
- [X] T024 [P] [US2] Test repository scope isolation in `tests/unit/test_holds_ordering.py`: a held repository's items report `held` while another repository's items in the same plan report no hold and dispatch in the same pass (FR-011)
- [X] T025 [US2] Add `hold_repo` and `unhold_repo` in `src/robot_army/operations.py`, validating the key against `repos.known(ctx.conn)` — **not** `sorted(config.repos)`, since a `[repos.*]` section for a repository that was never onboarded describes one the system does not watch — refusing an unknown key with `EXIT_FAILED` and a message naming it (FR-006), with `hold.repo` / `unhold.repo` audit pairs
- [X] T026 [P] [US2] Test both operations in `tests/unit/test_holds.py`: the refusal for a repository that has a `[repos.*]` section but was never onboarded, the redundant hold keeping its original timestamp, and the no-op release
- [X] T027 [US2] Add the `--repo` flag to `hold` and `unhold` in `src/robot_army/cli.py`, mutually exclusive with the positional id and with exactly one required — both together or neither is a usage error (exit 2) raised by argparse before anything is read, per research R6
- [X] T028 [P] [US2] Test the argument contract in `tests/unit/test_holds_surfaces.py`: `hold 5 --repo owner/name` and bare `hold` both exit 2, and the target is never inferred from its shape
- [X] T029 [US2] Add `POST /repos/hold` and `POST /repos/unhold` to `ROUTES` in `src/robot_army/web/server.py`, taking the repository key from the **form body** (`request.first("repo")`) as `_job_action` already does for `POST /poll` — never as a path segment, per research R7 — validated before it reaches anything, refusing a missing or unknown key with `404`
- [X] T030 [P] [US2] Test the routes in `tests/unit/test_holds_surfaces.py`, and confirm `tests/unit/test_web_routing.py::test_the_deliberately_absent_controls_are_absent` still passes: `/repos/hold` is two segments and `/repos/demo/onboard` is three, so onboarding stays terminal-only
- [X] T031 [US2] Add the repository-hold notice and its control to the queue page in `src/robot_army/web/pages.py`, shaped like the existing `held_off_column` repository summary and for the same reason it exists — a repository with ready items dispatching none of them reads exactly like a repository with no work at all. The notice covers the zero-queued-items case in the same code path, which is where FR-019 lands; splitting it by whether the count is zero would be artificial
- [X] T032 [P] [US2] Test the notice in `tests/unit/test_holds_surfaces.py`, including a held repository with **no** queued items still appearing (FR-019)
- [X] T033 [P] [US2] Test arrival-time holding in `tests/unit/test_holds.py`: with a repository held, a newly inserted `ready` item in it is held on its first appearance in the plan, with nothing backfilled and no event hooked (FR-012)

**Checkpoint**: quickstart steps 4–6 pass. The issue's reported situation — four items from one
repository plus one individually low-priority item — is now two actions.

---

## Phase 5: User Story 3 — See every hold in force (Priority: P2)

**Goal**: The author can answer "what is held?" completely, including holds that currently hold
nothing, and is reminded that holds exist without having to remember them.

**Independent test**: Hold a repository with no queued items, confirm it is listed and
attributed, then confirm it still takes effect when an item in that repository appears.

- [X] T034 [US3] Add `list_holds` in `src/robot_army/operations.py`, composing both hold tables into rows that carry the target, `held_at`, `held_by`, the age (via `health._age`, not a second parser), each held item's **current state**, and each held repository's count of currently queued items
- [X] T035 [US3] Add the `holds` verb in `src/robot_army/cli.py`, wire it in `_dispatch`, add it to `READ_COMMANDS`, and have it say *nothing is held* plainly when there are none rather than printing an empty table (US3 AS3)
- [X] T036 [P] [US3] Test the verb in `tests/unit/test_holds_surfaces.py`: a repository hold matching no queued item is listed with a zero count, a hold on a `done` item is listed with that state (research R11), the empty case says so in words, and every timestamp renders through `timefmt.local`
- [X] T037 [P] [US3] Add `holds` to the enumeration in `tests/unit/test_cli_exit_codes.py::test_every_web_control_has_a_terminal_verb_here`
- [X] T038 [US3] Add the summary line to `robot-army status` in `src/robot_army/operations.py` — how many items and how many repositories are held, and that `robot-army holds` lists them — rendered **only when at least one hold is in force**, per contracts/cli.md
- [X] T039 [P] [US3] Test the line in `tests/unit/test_holds_surfaces.py`: present with counts when something is held, and **absent entirely** when nothing is, so the common run gains no noise

**Checkpoint**: quickstart step 7 passes. A hold set and forgotten is now discoverable rather
than diagnosable as "polling is broken".

---

## Phase 6: User Story 4 — Holds outlive the daemon (Priority: P1)

**Goal**: A hold survives a daemon restart and a reboot, with its placement time unchanged, and
one placed while the daemon is down is honoured on its first pass.

**Independent test**: Place holds, stop and restart the daemon, confirm the same holds are in
force with unchanged times and that nothing from the held set dispatched in between.

**This phase adds no production code**, and that is the honest position rather than a gap:
durability is a property of the table US1 created and of `plan` reading holds on every pass
rather than caching them at startup. What remains is proving it, which the constitution requires
independently of this feature — persistence and recovery logic must carry tests for its failure
and interruption paths.

- [X] T040 [P] [US4] Test survival across a reopened database in `tests/unit/test_holds.py`: place an item hold and a repository hold, close and reopen the connection through `db.open_database`, and assert both are still in force with byte-identical `held_at` and `held_by` (FR-021)
- [X] T041 [P] [US4] Test that a hold placed with no daemon running is honoured on the first plan afterwards in `tests/unit/test_holds.py` — nothing caches it, so the assertion is that the very first `plan` after the write reports `held` (FR-022)
- [X] T042 [P] [US4] Test atomicity in `tests/unit/test_holds.py`: a hold change rolled back inside `db.transaction` leaves the hold wholly absent, and one committed leaves it wholly present — never a row with a missing `held_at` or `held_by`, which the `NOT NULL` constraints also forbid (FR-024)

**Checkpoint**: quickstart step 9 passes, including the stronger case — hold with the daemon
stopped, start it, and confirm nothing from the held set dispatches before it notices.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T043 [P] Add a `## Holding specific work` section to `README.md` immediately after `## Pausing dispatch`, covering both scopes, that a hold never expires and never stops a running session, and that `[repos.*].priority` remains the *standing* preference a hold is deliberately not
- [X] T044 [P] Update the two web-control inventories in `README.md` (the lines near 176 and 207 that enumerate what the terminal and the web can do) to include holding and releasing
- [X] T045 [P] Add an `### item_holds` / `### repo_holds` section to `docs/state.md` alongside `### dispatch_control`, with the `sqlite3` one-liner for inspecting each, what the cascade guarantees, and the note that nothing sweeps a hold on finished work
- [X] T046 [P] Test that a hold does not touch a running session in `tests/unit/test_holds.py`: with a session `running` for item `N`, holding `N` leaves the session state and the item state unchanged (FR-010, SC-010)
- [X] T047 Run `uv run pytest` and `uv run ruff check`, then walk `quickstart.md` end to end against a real installation with two onboarded repositories — finishing on step 13, whose last line must be *nothing is held*

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001)** → everything
- **Foundational (T002–T008)** → blocks every story. The tables and accessors must exist first
- **US1 (T009–T021)** → depends on Foundational only. **This is the MVP**
- **US2 (T022–T033)** → depends on Foundational; touches lines US1 wrote in `ordering.py`,
  `operations.py`, `cli.py`, `server.py`, and `pages.py`, so it follows US1 in practice even
  though the repository scope is conceptually independent
- **US3 (T034–T039)** → depends on Foundational for the accessors and reads better after US2,
  since a listing with only one scope in it is half a feature
- **US4 (T040–T042)** → depends on Foundational only, and can be written the moment T005 lands.
  Pulling it forward is reasonable: it is the requirement the issue states twice
- **Polish (T043–T047)** → after the stories it documents

### Within each story

Ordering → operations → CLI → web → rendering, because each layer calls the one above it. The
`[P]` tests in each phase depend only on the task immediately above them.

### Parallel opportunities

- **Foundational**: T003, T006, T007, T008 all run in parallel once T002 and T005 land — four
  different test files or four independent tests in one
- **US1**: T011, T012, T013 are three independent tests against T010; T015, T017, T019, T021
  each follow their own implementation task
- **US2**: T023, T024 against T022; T026, T028, T030, T032, T033 likewise
- **US4**: T040, T041, T042 are entirely independent of one another and of every other phase
- **Polish**: T043, T044, T045, T046 touch four different files

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. T001 — confirm the baseline is green
2. T002–T008 — Foundational. **Behaviour is unchanged at this checkpoint**, which is the point:
   two tables land without altering a single dispatch decision
3. T009–T021 — US1
4. **STOP and VALIDATE**: quickstart steps 1–3

That is already a useful system: any single queued item can be taken out of dispatch and put
back, from the phone or the terminal, durably. The issue's five-item scenario would take five
actions instead of two — which is worse than the finished feature and much better than editing
TOML.

### Incremental delivery

1. Foundational → nothing changes
2. **US1** → one item can be held (MVP)
3. **US2** → a whole repository can be held, and future work in it arrives held
4. **US3** → every hold is discoverable, including the ones holding nothing
5. **US4** → durability is proven rather than assumed
6. Polish → the documentation the feature owes

### Risk notes

- **T011 is the test that pins the design.** `held`'s rank is the one decision here that is
  invisible in the code but immediately visible to the author, and the failure it prevents is a
  held item telling them to go free a session slot. Write it before the branch it tests feels
  finished
- **T008 is worth writing before it is needed.** `purge_simulated` is the only path in the
  system that deletes a work item, and the whole justification for two tables instead of one is
  that the cascade makes FR-025 free. If that test does not pass, R1's reasoning was wrong and
  the design should change rather than the test being weakened
- **T031 is the one place this feature can quietly fail.** A repository hold with no queued item
  has no row to attach to, and a page that renders nothing for it would suppress every future
  item in that repository while looking entirely normal. It is the same failure `held_off_column`
  was added to prevent, which is why it borrows that shape
- **T022's detail composition is easy to get subtly wrong.** The tempting shortcut — report the
  item hold and stop — passes every test except the one where the author releases it

---

## Deliberate non-tasks

Recorded so their absence reads as a decision rather than an oversight:

- **`dispatch.py` is not modified.** `select_and_dispatch` already skips per-item holds and
  already records the first one it saw through `_note_hold`, under the summarisation that keeps
  a five-second tick from writing 17,280 identical records a day. The new reason inherits all of
  it
- **`config.py` and `share/config.example.toml` are not modified.** Holds add no configuration,
  deliberately: they are temporary and must be settable from the web interface, which does not
  edit TOML (research R10)
- **`heartbeat.json` does not gain a hold count.** Pause is in it because pause stops
  *everything*, and a system that is healthy and deliberately idle must not read as one that is
  idle for no reason. A hold stops named work while the queue keeps moving, so `status`, the
  queue view, and `holds` are its surfaces. Adding it would be a field with one writer
- **Nothing sweeps a hold on a finished item.** Clearing on a state transition is expiry under
  another name, which FR-026 rules out, and it would mean every transition site had to know
  about holds. The `holds` listing shows the item's state instead (research R11)
- **No confirmation page for either action.** Holds are trivially reversible and outward-facing
  in no sense; confirm-then-act is for `cancel` and the destructive verbs

---

## Notes

- `[P]` tasks touch different files and depend on no incomplete task
- Every task names the file it changes; none is a research task in disguise
- Commit after each task or logical group; messages explain why, not what
- Stop at any checkpoint to validate a story independently
