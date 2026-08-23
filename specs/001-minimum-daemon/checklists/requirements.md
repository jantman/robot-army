# Specification Quality Checklist: Minimum Daemon

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-23
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

Validation ran in a single pass; no items required a spec revision.

**One deliberate exception to "no implementation details".** The Assumptions section names SQLite.
This is not a technology preference leaking into the spec — it records the resolution of a conflict
between planning document §12 (which selects MariaDB) and constitution Principle II and the
Operating Constraints storage rule. The Governance section requires such a conflict to be raised
before the work rather than discovered afterward, so the resolution is recorded where the plan will
read it. No functional requirement names a storage technology; FR-071 and FR-073 state the
integrity and documentation obligations in technology-agnostic terms.

**Product-level named systems are domain, not implementation.** GitHub, the Claude Code worker, and
the author's terminal instance appear by name because they are settled product decisions in planning
§2 ("Settled decisions — not open for reconsideration"), not choices left to the plan. Mechanisms
that *are* the plan's to choose — the session-persistence layer, the exit-reporting transport, the
process-identity evidence — are stated as requirements on behaviour rather than by name.

**Constitution checks deferred to `/speckit-plan`, as designed.** Principle III's exception clause
requires the plan to enumerate any action that goes unlogged and why; FR-059 through FR-062 set the
obligation but the enumeration belongs in the plan. Likewise Principle I's justification of every
abstraction — the boundary interfaces FR-053 requires are the main thing the plan must defend, since
the constitution forbids strategy interfaces with one caller and this spec mandates four of them.
The counter-argument is already in planning §2: the dry-run effect levels are the second caller, and
they are a stated requirement rather than an anticipated need. The plan should make that argument
explicitly rather than assume it.

**Scale note for planning.** 73 functional requirements across six user stories is large for one
Spec Kit feature, and is the reason this was split into a roadmap rather than specified as one
document. If `/speckit-plan` or `/speckit-tasks` strains at this size, the natural further seam is
between user stories 1–2 (the dispatch loop) and 3–6 (recovery, effect levels, terminal interface,
health), which could become 001a and 001b without disturbing the roadmap's later entries.
