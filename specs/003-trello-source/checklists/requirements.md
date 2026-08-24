# Specification Quality Checklist: Trello Source

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-24
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

Two items were rewritten during validation rather than passing on the first pass:

- **FR-042** originally stated a constraint on the specification document ("the specification MUST
  NOT claim dry-run coverage"), which is not a property of the system and therefore not testable. It
  now states the verification obligation on the system's invariant instead.
- **FR-044** originally said source-specific behaviour "MUST NOT be reintroduced as conditional
  branches", which describes a smell rather than a checkable condition. It now names what a reviewer
  must be able to observe.

Named external services (Trello, GitHub) are treated as domain vocabulary rather than implementation
detail: they are what the feature *is about*, not a technology choice made on the author's behalf.
The same applies to the secrets rule in FR-003, which restates a constitutional constraint the
project already holds.

No [NEEDS CLARIFICATION] markers were raised. Every gap in the feature description was closed from
the planning document (§4, §7, §11), the roadmap, and milestone 001's existing model, and each
resulting decision is recorded in the Assumptions section where it can be challenged.

### Amended during `/speckit-plan` (2026-08-24)

FR-017, FR-018, FR-020 and the work-item assumption were rewritten during planning, and a new FR-020a
was added. FR-020 as originally written placed `needs_info` among the work item states, following the
planning document's §7. Design found that `work_items.repo_key` is `NOT NULL REFERENCES repos(repo_key)`
and `issue_number` is `NOT NULL`, and that a card awaiting clarification has neither by definition —
it may name a repository nobody has heard of, or none at all. Implementing it as written would have
meant rebuilding the central table to weaken an invariant every other row depends on.

The conflict was raised before the work rather than discovered after it, which is what the
constitution's Governance section requires. Reasoning is in `research.md` R5 and in the plan's
Post-Design Constitution Re-Check. The checklist items above were re-verified against the amended
text and still pass.

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
