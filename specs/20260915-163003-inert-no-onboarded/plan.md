# Implementation Plan: Say so when nothing is onboarded

**Branch**: `robot-army/issue-83-database-loss-un-onboards-every` | **Date**: 2026-09-15 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260915-163003-inert-no-onboarded/spec.md`

## Summary

An installation with no onboarded repository cannot do anything, and today it neither fails
`doctor` nor says so on the cards it holds — it tells each card's author to edit a card that was
already right (issue #83). Three small changes, no new state:

1. `doctor` gains an `onboarded repositories` check that fails when the set is empty and names
   `robot-army onboard`.
2. `resolve_repository` short-circuits on an empty onboarded set with a fixed reason, ahead of
   both the `robot-army:` line and the text scan, recorded with `source: "onboarding"`.
3. `_needs_info_comment` recognises that reason and posts a comment that tells nobody to edit
   the card.
4. The held-card activity gate in card evaluation lets a card held with that reason through once
   something is onboarded, so onboarding alone un-holds it (SC-004) — without which the comment's
   promise would be false, because the card itself never changes.

Plus the planning record (003 data model row, 003 quickstart scenario 5) and the guide pages
that describe `doctor`, held cards, and the audit record's `source` values.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library only; nothing added

**Storage**: SQLite state database — read only by this feature; no schema change

**Testing**: pytest (`uv run pytest`)

**Target Platform**: one Linux machine

**Project Type**: CLI + daemon

**Performance Goals**: n/a — one extra `len()` in `doctor`, one extra emptiness test per card
evaluation on a dict that is already built

**Constraints**: existing held-card reasons and comments must not change by a byte (SC-003)

**Scale/Scope**: two functions in `intake.py`, one in `operations.py`; docs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Verdict |
|---|---|
| I. Simplicity First | Pass. No new module, abstraction, config key or dependency. The reason is a module constant and the comment picks its text by comparing to it — no new field on `Resolution`, no new column. |
| II. Single-User, Local-First | Pass. Nothing multi-user; the comment addresses the operator on the card because the operator is the card author. |
| III. Total Accountability | Pass. **What this logs:** nothing new is logged, because nothing new happens outside the process. The resolution is already recorded as `trello.evaluated` (now with `source: "onboarding"` and the new reason), the hold as `trello.card.held`, and the comment by the existing `trello.card.comment` path. `doctor` stays read-only and unlogged, as it is today. |
| IV. Interruption Tolerance | Pass. **If killed halfway:** there is no new write. The card's `commented_reason` still records which reason was commented, so a kill between comment and record re-posts at most once, exactly as today. |
| V. Public Code, Unsupported | Pass. No credentials in any text; no compatibility shim for the old reason — a card held under it simply gets one fresh comment when the reason changes. |
| Operating Constraints | Pass, and this is the point: "Commands MUST exit non-zero on failure" — `doctor` on an inert installation now does. No outward-facing action is added. |
| Development Workflow | Unit tests for the `doctor` check (both ways), the resolution short-circuit (scan and declaration paths), the comment text, the unchanged texts, and the resolve-after-onboard path. |

Re-checked after Phase 1: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/20260915-163003-inert-no-onboarded/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── inert-installation.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
src/robot_army/
├── intake.py            # NOTHING_ONBOARDED reason; resolve_repository short-circuit; comment
└── operations.py        # doctor: `onboarded repositories` check

tests/unit/
├── test_nothing_onboarded.py   # new: resolution, comment, doctor, resolve-after-onboard
└── (existing tests unchanged — they are the SC-003 guard)

specs/003-trello-source/
├── data-model.md        # "database lost entirely" row
└── quickstart.md        # scenario 5, database-loss case

docs/guide/
├── 1-setup.md           # `doctor` fails until the first onboard
├── 2-intake.md          # held cards; `source` values; database loss
└── audit-log.md         # `trello.evaluated` `source: onboarding`
```

**Structure Decision**: single project; changes confined to the two modules that own the
behaviour and the docs that describe it.

## Complexity Tracking

No violations.
