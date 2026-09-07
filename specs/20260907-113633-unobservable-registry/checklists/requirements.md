# Specification Quality Checklist: An Observation That Saw Nothing Is Not Evidence That Nothing Is Alive

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-07
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

Two judgements worth recording, because both were close.

**"Non-technical stakeholder" is read as this project's maintainer.** The constitution says
documentation is written for the author's future self, and the subject of this feature is a
daemon's internal reconciliation pass. The spec names the observable conditions — a directory
that is absent or unlistable, items left `active`, records left open, an anomaly on the list —
and avoids function names, field names and file paths, which is where the line sits for a
spec in this repository. The one place that line is deliberately walked up to is the Baseline
note, which cites the tree the measurements were taken against; that is evidence, not design.

**Scope was widened past the issue's own framing, on purpose.** The issue describes "the
liveness sweep"; User Story 2 adds two further sweeps found in the post-#33 tree that draw the
same false conclusion from the same flags and cause the capacity under-count #28 named as the
only harmful direction. Fixing one and leaving the others would ship a half-guarded pass. The
widening is stated in the story rather than left implicit, so a reader can disagree with it.

Nothing was marked [NEEDS CLARIFICATION]. The one genuinely open question the issue raised —
whether a blind pass should raise an anomaly of its own — is answered yes in User Story 3,
because #33's plan enumerated the missing record as an accepted Principle III gap *on the
explicit ground that closing it was tracked here*. Leaving it unanswered would leave that
acceptance outstanding with nowhere else to go.
