# Specification Quality Checklist: Every verb that offers `--include-simulated` honours it

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-06
**Feature**: [spec.md](../spec.md)

## Content Quality

- [X] No implementation details (languages, frameworks, APIs)
- [X] Focused on user value and business needs
- [X] Written for non-technical stakeholders
- [X] All mandatory sections completed

## Requirement Completeness

- [X] No [NEEDS CLARIFICATION] markers remain
- [X] Requirements are testable and unambiguous
- [X] Success criteria are measurable
- [X] Success criteria are technology-agnostic (no implementation details)
- [X] All acceptance scenarios are defined
- [X] Edge cases are identified
- [X] Scope is clearly bounded
- [X] Dependencies and assumptions identified

## Feature Readiness

- [X] All functional requirements have clear acceptance criteria
- [X] User scenarios cover primary flows
- [X] Feature meets measurable outcomes defined in Success Criteria
- [X] No implementation details leak into specification

## Notes

Two items needed a second pass and are recorded here because the reasoning is worth keeping:

- **"Written for non-technical stakeholders"** is met in the sense this project can meet it. The
  reader is the single maintainer, and the spec names the verbs, flags and output surfaces by the
  strings they actually print because those *are* the user-facing vocabulary here — not because
  they are implementation. It names no module, function, table or column.
- **Scope boundedness** was the item most at risk, because the issue offers two remedies ("give the
  tables a column" or "drop the flag") and a secondary defect. The spec resolves the choice per
  verb with the reason stated, and admits the secondary defect explicitly as US4 at P3 rather than
  leaving it ambiguous. `worktree list` is stated as already-correct and in scope for a regression
  test only, so the issue's "untested" row is closed rather than carried forward.

Deliberately *not* marked [NEEDS CLARIFICATION]: whether `repos` should gain a rehearsed form
rather than lose the flag. Onboarding inspects a real clone on disk and has no simulated path, so
there is no reasonable second reading, and inventing one would be the speculative generality the
constitution's Principle I forbids.
