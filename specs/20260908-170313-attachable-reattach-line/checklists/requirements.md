# Specification Quality Checklist: A Reattach Line Only Where Reattaching Would Reach That Session

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-08
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

Two items were marked pass with a qualification rather than silently, because both are
places where this project's conventions and the generic checklist pull in different
directions.

**"No implementation details" and the Baseline paragraph.** The spec names files and line
numbers in its Baseline. That is deliberate and matches every other spec in `specs/`: the
report in issue #51 cited a line number that had already moved, and a bug spec that does not
say which tree it was read against invites the same drift again. The Baseline is evidence for
the claim "this is what the code does today", not a design.

**FR-006 names the boundary.** It constrains where the attachability answer comes from rather
than how it is computed, and it exists because the alternative — `show` deciding for itself
what "attachable" means, or picking a host implementation from a stored row — is a real and
tempting mistake this codebase has already ring-fenced once. The requirement is testable from
the outside: `show` and `attach` must agree.

**"Written for non-technical stakeholders"** is read here as the constitution reads it
(Principle V): the audience is the author's future self, and `dtach`, sockets and session
states are that reader's vocabulary.
