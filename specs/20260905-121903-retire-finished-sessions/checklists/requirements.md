# Specification Quality Checklist: Retire a finished item's session

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-05
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

Two judgements worth recording, because a later reader will otherwise think the checklist was
rubber-stamped.

**"No implementation details" and this project's vocabulary.** The spec names
`cleanup.on_issue_close`, `orphan_session`, `done`, `abandoned`, `failed`, and
`robot-army anomalies`. None of these is an implementation detail: each is a value or a command the
maintainer reads on a terminal or a web page, and every existing spec in `specs/` uses the same
vocabulary. No module, function, table or file name appears anywhere in the document, and that is
the line being held.

**Three clarifications were resolved before the spec was written**, not marked in it:

| Question | Resolution |
|---|---|
| What happens to a live worker under a terminal item? | Retire automatically for `done` **plus** a closed issue; never for `abandoned` or `failed` |
| Does a finished-but-alive session keep its capacity slot? | Yes — today's contract is preserved verbatim (FR-013) |
| Should resolved anomalies clear themselves? | `orphan_session` only, and only when the process it names is gone (FR-021, FR-023) |

One assumption is a decision rather than a default and is flagged as such in the spec: **no new
configuration key**. It is the first thing the plan phase should push back on if the reasoning does
not hold.
