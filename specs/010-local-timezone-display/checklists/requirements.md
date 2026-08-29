# Specification Quality Checklist: Times Are Read in the Local Timezone

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-29
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

- Zero [NEEDS CLARIFICATION] markers. The three candidate ambiguities were all settled
  against evidence rather than guessed at, and each is recorded in Assumptions:
  - **Does machine-readable output convert?** No. The terminal flag is self-described as
    "machine-readable output on stdout", and the web interface's machine-readable responses
    exist to be parsed. Settled toward UTC.
  - **Server-side or reader-side conversion?** Server-side. The user asked for "the system's
    local timezone", and only server-side conversion makes the terminal and the web interface
    agree on the same machine.
  - **Is a timezone configuration option needed?** No. Constitution Principle I (no knob with
    one caller) and Principle II (one user, one machine).
- Constitution check: Principle III is preserved explicitly by FR-011 and FR-012 — the record
  stays UTC, so reconstruction from the log alone is unaffected. Principle I is honoured by
  FR-008's refusal of a configuration knob.
- Deliberately out of scope, stated in the spec rather than left silent: stored-value
  migration, relative-age wording, duration arguments, and UTC-day audit file partitioning.
