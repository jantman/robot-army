# Specification Quality Checklist: Liveness Is Checked Wherever the Session Is Real

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-30
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

- Validation ran in two iterations. The first pass flagged two leaks toward implementation:
  FR-003 described the fix in terms of where code lives, and an assumption named a stored
  sentinel value. Both were rewritten to state the constraint behaviourally — the effect level
  stays enforced where real and simulated behaviour is chosen, and the stored record already
  answers the question — without naming modules, columns, or values. Second pass clean.
- Zero clarification markers. The two candidates both had defensible defaults and were resolved
  in the Assumptions section instead: whether a dead-but-real session below `live` should raise a
  distinct anomaly (no — it takes the same path as `live`, per FR-005 and FR-013), and whether to
  absorb issue #28 (no — it is a gap in which sessions are swept, not whether they are checked).
- Scope boundary worth re-reading at plan time: the unreadable-registry hazard is listed as an
  edge case and explicitly *not* fixed here. Extending the sweep to `no-remote` widens that
  existing exposure. If the plan concludes that is not acceptable to ship, it belongs in a
  separate feature rather than expanding this one.
- **Third iteration, after #28 (PR #43) merged.** Re-validated against `main` at 15bf843 rather
  than against the PR description, which mattered: the merged tree confirms the `dry_run` skip
  is intact and that `_orphan_sweep`'s `running` guard was left byte-identical, so story 3
  survives the merge but for a narrower reason than first written. Revised: the Baseline note,
  story 3 (now explicitly the `active` half of a question #28's research declined to answer),
  FR-009 (written against the merged counter set, which now includes `reclaimed`), the #28 edge
  case and assumption (merged, not pending), and two new requirements — FR-015 forbidding a
  second liveness mechanism, FR-016 protecting the merged behaviour — plus SC-008 for the
  no-double-report interaction. Checklist re-run clean.
- Ordering is now a correctness property, not an implementation detail, and the plan should
  treat it as one: this feature must move a record off `running` earlier in the pass than the
  #28 sweep reads it. Reversing that order double-reports an orphaned worker or hides it.
- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
