# Specification Quality Checklist: Refuse to Remove a Worktree While Its Session Is Open

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-01
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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`

### Validation pass (2026-09-01)

- **Implementation detail**: the spec names session states (`starting`, `running`) and work item
  states (`done`, `abandoned`) as domain vocabulary rather than as code. These are the operator's
  own words — they appear in command output the operator reads — so they are treated as domain
  language, not leakage. No module, function, table, flag name or language construct appears.
- **Scope boundary**: four exclusions are stated explicitly in Assumptions (listing surface,
  stopping sessions, closing session rows, the automatic reclaim path's behaviour), and FR-012 /
  FR-013 pin the two things that must not change.
- **Testability**: every FR is observable from outside — counts of removals attempted, exit status,
  message content, prompt content, and action-record content. FR-014 is the one structural
  requirement; it is verifiable by the two paths agreeing on the same state set.
- **Clarifications**: none needed. The single genuinely debatable choice — whether process
  liveness gates the refusal or merely informs it — is resolved in Assumptions with its reasoning,
  in the direction the reported defect requires.
