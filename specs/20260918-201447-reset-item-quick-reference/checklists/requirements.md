# Specification Quality Checklist: A first-class "start over", and an operating page you can scan

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-18
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

Two deviations from the strict letter of "no implementation details", both deliberate and both
kept because this is a single-maintainer project whose spec is read by the person who will build
it:

- The spec names `docs/guide/operating.md` and the sibling guide pages. The page is the
  deliverable, not an implementation of one, so naming it is naming the requirement.
- The spec names existing verbs (`retry`, `restart`, `resume`, `abandon`, `worktree remove`,
  `cancel`) and existing states. These are the product's own vocabulary and the user-facing
  surface the feature has to fit into; describing them obliquely would make the requirements
  less testable, not more neutral.

Both were checked against the "would a tester find this ambiguous?" bar rather than the
"does it mention a file?" bar, and pass.

Three open decisions were resolved as documented assumptions rather than as
[NEEDS CLARIFICATION] markers, because each has a defensible default and the issue left the
choice to the implementer: the verb's name (`reset`), whether `failed` is in scope (yes), and
whether terminal states become resettable (no — recorded in Out of Scope).
