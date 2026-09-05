# Specification Quality Checklist: Close a finished item's terminal tabs

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-05
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

Three judgements worth recording.

**Deliberately vaguer than the request.** The user's description named `kitty`, `--hold`,
`Display.close()`, `boundaries/kitty.py` and `window_id`. None of those appears in the spec, which
talks about "the terminal", "a window", "a marker the system wrote onto the window" and "keeps the
window open after its command exits". That is not evasion — the requirements are genuinely about
*which windows may be closed and on what evidence*, and every one of them would hold if the
terminal were swapped tomorrow. The named specifics belong in `research.md` and the plan, where the
existing `close()` with no callers and the `ra_item` marker will be cited directly.

**User Story 2 is P1, not a lower priority.** It would be natural to rank "keeps failed windows" as
a constraint on the real feature. It is ranked equal-first on purpose: the hold flag exists because
a vanishing window destroyed the only evidence of a failed launch, and a build that closed tabs
correctly while also closing a failed launch's window would be a regression rather than a partial
success. Ranking it below P1 would invite exactly that trade during implementation.

**Three clarifications were resolved before writing, not marked in the spec:**

| Question | Resolution |
|---|---|
| Event at retirement, or a recurring sweep? | **A sweep** — which is what makes SC-003 (the two windows already open) reachable at all, and closes the crash-between-kill-and-close gap |
| Does a by-hand stop of a `done` item's session close its tab? | **Yes** — the rule is written about the work, so both routes agree with no change to the stop command (FR-006, User Story 3) |
| Earlier attempts' windows? | **All of them** — a completed item leaves no tabs (FR-002) |

One assumption is a judgement rather than a default and is flagged in the spec: **no grace period**.
If the windows turn out to be read after the fact more often than expected, that is the first thing
to revisit.
