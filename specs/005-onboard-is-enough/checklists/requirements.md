# Specification Quality Checklist: Onboarding Is Enough

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-26
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders — *see note 1*
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

**Note 1 — the stakeholder item is satisfied by the constitution's standard, not the template's.**
Constitution Principle V requires documentation "written for the author's future self", and there is
one maintainer and no non-technical stakeholder. The Requirements, Success Criteria and Out of Scope
sections are written without reference to code, and are the parts a reader must be able to check work
against. The **Scope note** deliberately names four existing accessor methods, because the argument
it makes — that this milestone finishes a fallback ladder that already exists rather than building a
new mechanism beside it — cannot be made without naming them, and that argument is what justifies the
milestone's size. Milestones 001–004 use the same latitude in their scope notes.

**Zero clarification markers.** Four decisions that could have been raised as questions were resolved
as documented assumptions instead, each with a stated reason:

| Decision | Resolution | Recorded in |
|---|---|---|
| One derivation candidate, or a search path? | One. A second root has no demonstrated user (Principle I). | Assumptions |
| Does a repository's own `post_create` replace or extend the shared default? | Replaces. The repositories needing their own steps need different steps, not extra ones. | Assumptions, FR-020 |
| Does a later configuration change to `path` take effect silently? | No — it blocks pending re-approval, mirroring the existing fingerprint flow. | FR-013, US3 AS3 |
| Is the onboarding allowlist a security boundary? | No, and FR-026 requires it be documented as a mistake guard. The issue-author check remains the boundary. | FR-026 |

**One story is explicitly droppable.** US7 (discovery listing) is the only story that adds a surface
rather than removing a step, and the spec states what must happen if it is dropped: the currently
uncalled listing method is deleted rather than left in place. That is the state issue #8 exists to
report, so leaving it would reproduce the defect this milestone resolves.

**Numbers are measured, not estimated.** SC-002's 220-of-252 threshold, the five wrong-location
repositories named in the scope note, and the 25 uncloned repositories were all read off the author's
machine on 2026-08-26. SC-002 sets the bar two below what was measured so a repository cloned or
renamed between now and implementation does not fail the criterion spuriously.

**Sequencing constraint carried into the spec.** The Dependencies & Follow-on section records that
issue #1's verification round should complete before implementation, and that 001's scenario 6 must
be re-run afterwards. This is a scheduling fact that would otherwise be lost between the plan and the
tracker.
