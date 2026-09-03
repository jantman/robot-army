# Implementation Plan: Holding Items and Repositories Out of Dispatch

**Branch**: `robot-army/issue-117-dispatch-holds` | **Date**: 2026-09-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260903-060639-dispatch-holds/spec.md`

## Summary

One migration, one dataclass, two read accessors and four writers, one enum member, one branch
in `_hold_for`, three CLI verbs, four web routes. No new module, no new dependency, no new
configuration key, and no network call anywhere in the feature.

The reason it is this small is that the queue already has the concept. `ordering.HoldReason` is
an enum whose declaration order **is** the precedence, `_hold_for` returns the first reason that
applies, and `select_and_dispatch`, `robot-army status`, and the web queue view all walk the
same `ordering.plan` output. Adding a reason therefore reaches the dispatcher and both surfaces
without touching any of them: `dispatch.py` is not edited by this feature at all.

Three placement decisions carry the design, and each displaced a plausible alternative
(research [R1](research.md), [R2](research.md), [R3](research.md)):

- **Two tables with real foreign keys**, not one polymorphic table with a `scope` column. `PRAGMA
  foreign_keys` is on, so `ON DELETE CASCADE` makes FR-025 — a hold never outliving what it
  holds — a database constraint rather than a rule every future deletion site has to remember.
  Making the target the primary key delivers FR-004's idempotence in the same stroke.
- **`held` ranks directly below `paused`**, above `capacity_unobservable`. Every reason below it
  would send the author at a fix that cannot work, because the item is held.
- **`held` is a per-item hold, never a global one.** `_GLOBAL_HOLDS` is unchanged. Holding a
  repository holds that repository's work and leaves every other repository dispatching in the
  same pass — which is the issue's actual scenario, and which a global hold would leave exactly
  as it found it.

Manual reordering is out of scope by the author's decision, recorded in the spec. Nothing here
stores an order, and `ordering.plan`'s sort is untouched.

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`)

**Primary Dependencies**: none added, and none used. `httpx` remains the only runtime
dependency and this feature does not reach it.

**Storage**: the existing SQLite database. **Migration 010** — two new tables, `item_holds` and
`repo_holds`. No column is added to any existing table and no backfill is needed: no holds
existed before, so an upgraded database is correct the moment the tables exist.

**Testing**: `pytest`, `tests/unit/`. `uv run pytest`, `uv run ruff check`.

**Target Platform**: one Linux machine with a shell. No network involvement whatsoever.

**Project Type**: single-process CLI daemon with a read-only-plus-controls web view.

**Performance Goals**: `ordering.plan` runs on every dispatch tick *and* every web page render,
so the budget is per-plan, not per-item. This feature adds exactly **two indexed scans per
plan** — one per hold table, each into a dict, both tables holding a handful of rows for one
author — resolved once for the whole plan in the same position as `resolved`, `unfinished`, and
`boards` (R5).

**Constraints**: `ordering.plan` is pure and stays pure — two more reads, no writes, no
filesystem, no network. `dispatch.py` is not modified. No configuration key is added, so
`share/config.example.toml` and `config.py` are untouched.

**Scale/Scope**: a handful of repositories, a queue of tens of items, one author, holds numbered
in single digits.

## Constitution Check

*GATE: passed before Phase 0, re-checked after Phase 1 — see the re-check at the end.*

### I. Simplicity First (YAGNI & KISS)

**Pass.** No new dependency, no new process, no new module, no new concurrency. Five
abstractions were available and all five were declined, each in research with the condition
under which it would become justified:

- **A polymorphic `dispatch_holds` table with a `scope` discriminator** — one table instead of
  two. Rejected in R1: it trades a constraint the database enforces for an invariant the code
  must remember at every deletion site, and the site that forgets leaves a hold attached to a
  recycled id.
- **A hold predicate or rule engine** — holding by label, age, source, or pattern. Rejected:
  the issue asks for two scopes, and a general predicate is machinery with one caller.
- **Hold expiry and the background sweeper it implies.** Rejected by FR-026 and R10: a hold
  that lapses on its own silently restarts work the author stopped.
- **A free-text note on each hold.** Rejected in R10: the audit record carries more context
  than a note would, and the listings carry the age. It would be a field with one writer and no
  reader.
- **A `held` column on `work_items` and on `repos`.** Rejected in R1: `repos` is an approval
  record that nothing re-derives after approval, and a policy column on `work_items` would put
  dispatch policy inside the item's own row where every reader of `WorkItem` would meet it.

The feature also adds **zero configuration**. That is not incidental — repository holds are
deliberately runtime state rather than a TOML key, because they are temporary and must be
settable from the web interface, which does not edit configuration.

### II. Single-User, Local-First

**Pass.** All state is in the existing SQLite database at the documented path. No accounts, no
authentication, no authorization: `held_by` records *which surface* placed the hold (`cli` or
`web`), exactly as `dispatch_control.paused_by` already does, and never a person. Nothing here
requires a network, a service, or any deployment infrastructure — it is the one feature in
recent memory with no outward call at all.

### III. Total Accountability

**Pass, with no gap to declare.**

- **What this logs.** Four actions, each written through `ctx.audit.action` with the intent
  record flushed before the change: `hold.item`, `hold.repo`, `unhold.item`, `unhold.repo`
  (and their `web.`-prefixed counterparts, which `_perform` writes for every `POST`). Each
  record carries the target, whether a hold was already in force, the resulting `held_at` and
  `held_by`, and whether the request was redundant. From the log alone, without re-running
  anything, "what was held, when, by which surface, and was it already held" is answerable —
  which is SC-008.
- **The effect of a hold** — a dispatch pass that skipped a held item — is recorded by the
  existing `_note_hold` / `_HOLD` signature mechanism in `dispatch.py`, unchanged. That
  mechanism is already a documented Principle III summarisation (a record when the hold's
  signature changes and once more when it ends, carrying duration and pass count, rather than
  17,280 identical records a day). Holds ride on it and introduce no new summarisation and no
  new gap.
- **Refusals are recorded too.** An unknown item or repository is refused inside `_perform`'s
  audit pair on the web side and inside the operation's own pair on the CLI side, so a rejected
  attempt leaves a record rather than vanishing.

### IV. Interruption Tolerance

**Pass.**

- **What happens if it is killed halfway.** Each hold change is one `db.transaction`
  (`BEGIN IMMEDIATE`), so FR-024 is SQLite's guarantee, not ours: the hold is wholly present or
  wholly absent. A process killed between the audit intent record and the commit leaves an
  intent with no outcome, which is the existing and intended reading of `audit.action` —
  "attempted, result unknown" — and the database says which it was.
- **Migration 010** advances `user_version` as its last statement inside one transaction, so an
  interrupted upgrade re-runs the whole migration on the next start.
- **A hold placed while the daemon is down** is honoured on its first dispatch pass (FR-022),
  because holds are read inside `plan` on every pass rather than cached at startup. There is no
  in-memory copy to go stale and nothing to invalidate across the two processes.
- **No network call exists in this feature**, so there is no timeout or retry bound to set.

### V. Public Code, Unsupported Project

**Pass.** No credentials, personal data, or hostnames are stored — a repository key and a
surface name. `docs/state.md` gains a section describing the two tables and how to inspect
them, as every other persisted table has. No packaging, no release pipeline, no compatibility
shim.

### Development Workflow

**Pass.** Unit tests ship with every new or changed unit of behaviour. Because holds are
persistence and state-machine-adjacent, the constitution additionally requires failure and
interruption paths, which the task list covers explicitly: the cascade under `purge_simulated`,
the redundant hold and redundant release, the unknown target, the interaction with each
existing hold reason, and survival across a reopened database.

**No entry is required in Complexity Tracking.** Nothing in this design is in tension with
Principle I or II.

## Project Structure

### Documentation (this feature)

```text
specs/20260903-060639-dispatch-holds/
├── plan.md              # This file
├── research.md          # Phase 0 — eleven placement decisions
├── data-model.md        # Phase 1 — migration 010, the Hold model, accessors
├── quickstart.md        # Phase 1 — end-to-end validation
├── contracts/
│   ├── dispatch-policy.md   # the gate, the precedence, selection, purity
│   ├── cli.md               # hold / unhold / holds, and status's summary line
│   └── web.md               # four routes, their guards, and terminal parity
├── checklists/
│   └── requirements.md  # written by /speckit-specify
├── spec.md
└── tasks.md             # /speckit-tasks output — NOT created by /speckit-plan
```

### Source code (repository root)

```text
src/robot_army/
├── migrations.py        # + SCHEMA_010_SQL, _migration_010, appended to MIGRATIONS
├── models.py            # + Hold dataclass
├── db.py                # + list_item_holds, list_repo_holds, set/clear × item/repo
├── ordering.py          # + HoldReason.HELD, holds read in plan, one branch in _hold_for
├── operations.py        # + hold/unhold/list_holds, status summary line
├── cli.py               # + hold, unhold, holds verbs; holds in READ_COMMANDS
└── web/
    ├── server.py        # + four routes and their handlers
    └── pages.py         # + held rendering, repo-hold notice on the queue page

tests/unit/
├── test_holds.py            # placing, releasing, idempotence, refusals, cascade
├── test_holds_ordering.py   # precedence against every existing reason, purity
└── test_holds_surfaces.py   # CLI verbs, web routes, parity, rendering

docs/state.md            # + the two tables, what they hold, how to inspect them
```

**Structure Decision**: unchanged. This is the existing single-package layout, and the feature
adds no file to `src/` — every change is an edit to a module that already exists. `dispatch.py`
is deliberately absent from that list: the dispatcher reads the plan, and a new reason in the
plan needs no change in the code that walks it.

## Design in one page

**The gate.** For a work item `I` in repository `R`:

> `I` is held with reason `held` if and only if a row exists in `item_holds` for `I`, or a row
> exists in `repo_holds` for `R`, or both.

**The precedence** gains one member and changes nothing else:

```
paused > held > capacity_unobservable > global_cap > repo_cap > awaiting_merge
       > not_onboarded > off_column > preparation_failed
```

**Selection** is unchanged. `held` is not in `_GLOBAL_HOLDS`, so a held item is skipped and the
pass considers every other repository's work (FR-011).

**Purity** is unchanged. `plan` gains two reads and still writes nothing.

**Reporting.** One reason per item (FR-015). When both an item hold and its repository's hold
apply, the single `held` reason's detail names both and says that releasing one leaves the
other in force (FR-017). A repository hold matching no queued item is surfaced separately — on
the queue page and in `robot-army holds` — so it can never suppress future work invisibly
(FR-018, FR-019).

## Phase 1 artifacts

- **[data-model.md](data-model.md)** — migration 010's SQL with its reasoning, the `Hold`
  dataclass, the six accessors, and what each guarantees.
- **[contracts/dispatch-policy.md](contracts/dispatch-policy.md)** — the gate, precedence,
  selection, and purity statements above, in the form the previous milestone's contract uses.
- **[contracts/cli.md](contracts/cli.md)** — the three verbs, their arguments, exit codes, and
  output.
- **[contracts/web.md](contracts/web.md)** — the four routes, their form fields, guards,
  redirects, and declared terminal verbs.
- **[quickstart.md](quickstart.md)** — the runnable validation walk, including the restart that
  proves FR-021.

## Constitution re-check after Phase 1

Re-evaluated against the completed design rather than the intention.

- **I. Simplicity First — still passes, and the design got smaller during Phase 1.** Two
  findings shrank it. `dispatch.py` needs no edit at all, because `select_and_dispatch` already
  handles per-item holds in both selection and logging (`first_held` / `_note_hold`), so the
  new reason inherits correct behaviour rather than being given it. And FR-025 needed no code
  after R1 chose foreign keys — `purge_simulated`, the only deletion path in the system today,
  is unchanged, with a test asserting the cascade rather than a cleanup call implementing it.
- **II. Single-User, Local-First — still passes.** Nothing added a network call, a service, or a
  second store.
- **III. Total Accountability — still passes, with no gap.** The four audit actions are listed
  above. One point re-checked deliberately: the summarisation that keeps a five-second tick from
  writing 17,280 hold records a day is pre-existing and documented, and this feature adds
  nothing to it.
- **IV. Interruption Tolerance — still passes.** One transaction per change; migration advances
  its version last; a hold placed with the daemon down is honoured on its first pass because
  nothing caches it.
- **V. Public Code — still passes.** `docs/state.md` is updated in the same change, which is the
  documentation obligation this feature actually carries.

## Complexity Tracking

Not applicable. The Constitution Check passed in both evaluations with no violation to justify.
