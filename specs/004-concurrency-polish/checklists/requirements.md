# Specification Quality Checklist: Concurrency & Polish

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-25
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

Validated in one pass; no spec edits were required to reach a clean result.

Judgements worth recording, since a reviewer may read some of these as failures:

- **Domain vocabulary is not implementation detail.** The spec names worktrees, branches, issues,
  cards, sessions, and the session registry. These are the system's subject matter, established in
  milestones 001 and 003, not technology choices — no language, library, command, schema, or wire
  format appears. FR-002 constrains *which observation is authoritative* and forbids command-line
  matching; that is a correctness requirement carried forward from 001, not a design instruction.
- **"Written for non-technical stakeholders" is read as "written for the author away from the
  code".** There is one maintainer and no non-technical audience. The test applied was whether each
  requirement can be judged without opening a source file, which it can.
- **Zero [NEEDS CLARIFICATION] markers is a deliberate outcome, not an oversight.** Four decisions
  the planning document leaves open were resolved here against its own stated evidence and against
  the constitution, and each is recorded in Assumptions with its reasoning: the cleanup trigger
  (issue close, opt-in), the per-repo default cap (one), the absence of aging, and reuse of the
  health channel for notifications. The one thing genuinely undeterminable — the global cap's
  numeric value — is treated as configuration rather than as a question, because only running the
  system answers it.
- **FR-036 and FR-043 require a documented rule rather than naming one.** Both are testable as
  written: the rule must exist, be documented, and bound the behaviour. Fixing the exact bound in the
  spec would be a planning decision made early with no evidence.
