# Specification Quality Checklist: The Dead-Man's Switch Reads the Lock as Well as the Heartbeat

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-09
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

Two items were judged on this project's terms rather than the generic wording, and the
judgement is recorded here so the next reader does not have to redo it.

**"No implementation details"** — the Baseline block names files, functions and line numbers,
and the Assumptions name `flock` and the import direction between two modules. This is
deliberate and matches every other spec in `specs/`: the audience is one maintainer, the report
is a bug report against code that exists, and the facts the spec depends on were read in the
tree rather than assumed. The *requirements* themselves (FR-001…FR-017) name no module, no
function and no signature — they state observable behaviour, and the plan is free to choose how.

**"Written for non-technical stakeholders"** — there is no non-technical stakeholder. Per the
constitution (Principle V) documentation is written for the author's future self. The standard
applied instead: a reader who has not seen this code can follow every scenario from the prose.

One scope decision worth flagging to the reader rather than burying: **User Story 3 widens the
issue.** The issue names `health`; story 3 also covers `status` and the web interface's account
of the daemon. It is included because the reported bug *is* two surfaces disagreeing, and fixing
only one of them recreates it with the roles reversed — and because those call sites already
take both readings. It is P3 and independently droppable if that judgement is wrong.
