# Specification Quality Checklist: Only the maintainer's own terminal socket may receive a dispatch

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

- The spec says "owned by the user identity the process runs as", "restricts entry removal to the
  entry's owner", and "per-user runtime directory" rather than naming the system calls, the sticky
  bit, or `XDG_RUNTIME_DIR`. Those names belong in the plan; the requirement is about which
  candidates may be spoken to.
- The issue offered three fixes as alternatives ("any one of"). The spec takes all three, because
  they do different jobs and none subsumes the others: the ownership check is what protects the
  configuration the maintainer already has, moving the default is what protects a setup built from
  the documentation, and the load-time warning is what tells a maintainer with the old location
  why they are seeing it. This is recorded under Assumptions.
- One requirement in the spec is not in the issue's fix list: refusing a candidate whose directory
  lets other users remove entries (User Story 2). Without it the ownership check has a window
  after it in which the inspected file can be replaced. It costs the same inspection that is
  already being made.
- The "related exposure needing no attacker" — launch arguments visible through the process table
  — is scoped to documentation (User Story 5) rather than fixed. Closing it means changing how a
  session receives its prompt and environment, which is a different feature. The decision and its
  reason are in Assumptions so that a later reader does not mistake it for an oversight.
