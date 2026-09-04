# Specification Quality Checklist: One dispatch gate on every launch path

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-04
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

### Validation record

Two passes were run.

**Pass 1** found three problems, all now fixed:

1. *Implementation detail leaked into requirements.* An earlier draft of FR-016 named the
   claim as a conditional `UPDATE ... WHERE state IN (...)` returning a row count, and
   FR-021 named the parameter `force: bool = False`. Both are the issue's suggested
   implementation, not the requirement. They were restated as the observable property —
   exactly one concurrent attempt succeeds; the terminal offers an explicit override — and
   the code shape was left to `/speckit-plan`.
2. *An untestable success criterion.* "The cap is enforced everywhere" was replaced by
   SC-001, which counts surfaces and actions (four attempts, four refusals, zero sessions).
3. *Unbounded scope.* The queue reports nine hold reasons; the issue names three brakes. The
   spec now states in Assumptions exactly which five conditions move to the gate and why the
   remaining four do not, so "everything in `plan`" cannot be read into it.

**Pass 2** confirmed each checklist item:

- *No implementation details*: requirements name conditions, refusals, records and
  observable outcomes. Module, function and parameter names appear nowhere in Requirements
  or Success Criteria. Two names survive deliberately and are user-facing rather than
  internal: the terminal flag `--force` (FR-021, Assumptions) and the document path
  `docs/security-analysis.md` (FR-027), which is itself the deliverable.
- *Testable and unambiguous*: every FR is a MUST over a condition and an observable
  consequence. The three that could have been vague — precedence (FR-007), vocabulary
  (FR-008), and what an override may not reach (FR-024, FR-025) — are pinned to an existing
  behaviour or an enumerated list rather than to a judgement.
- *Measurable, technology-agnostic success criteria*: SC-001 through SC-008 count sessions,
  refusals, repetitions, and log fields. SC-005 states a repetition count (50) so "no race"
  is a measurement rather than an assertion. SC-009 is the constitution's own gate.
- *Acceptance scenarios*: four user stories, each independently testable and each with its
  own Given/When/Then set covering the success and refusal directions.
- *Edge cases*: eight, including the two most likely to be missed — that a refusal is not a
  failure (an item must not be marked failed for the machine being busy), and that the web
  interface answers before the slow work happens, so a late refusal would be invisible.
- *Scope bounded*: stated in Assumptions in both directions — which conditions move, which
  stay, and which checks the override may never reach.
- *Dependencies and assumptions*: eleven entries, including the two the constitution
  requires of every feature — what this logs, and what happens if it is killed halfway.
