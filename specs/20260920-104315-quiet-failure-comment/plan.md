# Implementation Plan: A failure comment that says nothing about my machine, and a needs-me card that says why

**Branch**: `speckit/20260920-104315-quiet-failure-comment` | **Date**: 2026-09-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260920-104315-quiet-failure-comment/spec.md`

## Summary

Two changes, in opposite directions, to what happens when a dispatch fails.

**Stop publishing the reason.** `failure_comment_body` loses its `reason` parameter and gains
`item_id`. The body becomes two labelled lines — host and work item — identical for every
cause. `_comment_failure` drops the reason too, so the five call sites stop passing it
onward. The reason is untouched everywhere it is already recorded: the `state.work_item`
audit record, the item's stored columns, and the notification.

**Start showing it.** `_interrupted_card` renders `failure_reason or blocked_reason` for
items in `failed`, and the missing-checkout banner becomes conditional on a checkout path
actually having been recorded. That second condition moves into `_signal_row`, redefining
`worktree_missing` so the JSON stops making the same false claim the HTML did.

No schema change, no new dependency, no new configuration key, no new module.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: standard library; the web interface's own `html.py` markup helpers.
None added.

**Storage**: SQLite (`work_items.failure_reason`, `.blocked_reason`, `.worktree_path`) — read
only; no migration.

**Testing**: pytest — `tests/unit/test_issue_comments.py`, `tests/unit/test_web_views.py`,
`tests/integration/test_dispatch.py`

**Target Platform**: one Linux machine

**Project Type**: single project — daemon plus CLI plus a read-mostly web interface

**Performance Goals**: none applicable. The card gains one string from a row already
fetched; the comment gets shorter.

**Constraints**: the rendered reason must pass through `html.escape`; the comment body must
be identical across all five failure paths.

**Scale/Scope**: two functions in `dispatch.py`, two in `web/pages.py`, two guide pages.

## Constitution Check

*GATE: passed before Phase 0, re-evaluated after Phase 1 design. No violations; the
Complexity Tracking table is therefore absent.*

### I. Simplicity First

**Pass.** Every available option was the smaller one.

- No new field: `worktree_missing` is **redefined** rather than joined by a second field
  that differs only in a case nobody wants the old answer for (research R6).
- No new rule for which reason wins: the card renders the expression `/queue` already
  renders (research R5).
- No truncation machinery, no expander, no disclosure control: every producer of a failure
  reason emits one bounded sentence, measured in research R3.
- No configuration knob for what the comment says. A key with one caller and one correct
  value is exactly what this principle forbids.
- The `reason` parameter is **removed** rather than kept and ignored, which is a subtraction.

### II. Single-User, Local-First

**Pass, and this feature serves it.** The failure reason moves from a surface the operator
does not control to three that are entirely local. Nothing gains a network dependency;
`/interrupted` renders with GitHub unreachable exactly as it does today, because the reason
is a stored column.

### III. Total Accountability

**What does this log?** Nothing new, and nothing less.

| Record | Before | After |
|---|---|---|
| `state.work_item`, `detail.reason` | full reason | unchanged |
| `github.comment`, `detail.body` | body including the fenced reason | body, which no longer contains the reason |
| Notification | full reason | unchanged |

**The one omission, named as this principle requires**: the `github.comment` record no
longer contains the failure reason. This is not a gap in the record. That record's
`entity_id` names the work item, and the `state.work_item` record written moments earlier in
the same dispatch carries the reason in full. Reconstruction from the log alone — what
happened, when, to what, with what result — is intact, and the reason is now recorded once
rather than twice.

Rendering the reason on the card adds no action and therefore no record. Reading is not an
action; the web interface already renders `show` on every request without writing, and the
reasoning for that is at `dispatch.check_gates`.

**What happens if it is killed halfway through?** Nothing changes. Neither half of this
feature writes state. The comment either posts or does not, and a comment that fails to post
is logged and ignored — unchanged, and the reason for it (GitHub's availability and a
session's fate are unrelated facts) is unaffected. The card is a pure function of rows
already committed.

### IV. Interruption Tolerance

**Pass, vacuously.** No new persistent write, no new network call. The one network call in
scope — the comment POST — keeps its existing timeout, its existing non-retry, and its
existing swallow-and-log handler.

### V. Public Code, Unsupported Project

**Pass, and this is the principle the feature exists to serve.** The change removes
operator-local detail — home-directory paths, settings filenames, repository configuration,
local command invocations — from a world-readable surface. The principle's letter is about
committed content; its rationale is about what publication costs, and a comment on a
repository the operator does not own is the harder case, because it cannot be taken back.

No backward-compatibility obligation applies to the comment's shape. Nothing consumes it but
a human.

### Development Workflow

Unit tests for every changed unit of behaviour. `_comment_failure` and `_interrupted_card`
are not persistence, state machines, or parsers, so the additional failure- and
interruption-path requirement does not attach; the failure paths that *produce* the reasons
are already covered in `tests/integration/test_dispatch.py` and stay covered. Full suite
green before the feature is complete.

## Project Structure

### Documentation (this feature)

```text
specs/20260920-104315-quiet-failure-comment/
├── spec.md
├── plan.md                      # this file
├── research.md                  # Phase 0
├── data-model.md                # Phase 1
├── quickstart.md                # Phase 1
├── contracts/
│   ├── failure-comment.md       # supersedes §3 of the 2026-08-30 issue-comment contract
│   └── needs-me-card.md
├── checklists/
│   └── requirements.md
└── tasks.md                     # Phase 2 — /speckit-tasks, not this command
```

### Source code

```text
src/robot_army/
├── dispatch.py                  # failure_comment_body, _comment_failure, 5 call sites
└── web/
    └── pages.py                 # _signal_row, _interrupted_card

tests/
├── unit/
│   ├── test_issue_comments.py   # the comment's shape and what it omits
│   └── test_web_views.py        # the card's reason, the banner's condition, HTML/JSON agreement
└── integration/
    └── test_dispatch.py         # the reason is absent from the comment, present in the record

docs/guide/
├── 5-outcome.md                 # "naming the host and the reason" is now false
└── operating.md                 # what the needs-me page tells you about a failed item
```

**Structure Decision**: the existing single-project layout. This feature adds no file to
`src/`; it edits four functions across two modules that already own the behaviour.

## Documentation obligations

Per `CLAUDE.md`, a change to behaviour updates the guide page for the pipeline stage it
affects. Two stages are affected, so two pages change.

| Page | Why |
|---|---|
| [`5-outcome.md`](../../docs/guide/5-outcome.md) | Line 41 reads "A failed attempt gets its own comment naming the host and the reason." The second half becomes false. The replacement states what the comment carries and, more usefully, *why* it carries so little and where the reason went. |
| [`operating.md`](../../docs/guide/operating.md) | The needs-me section describes what the three states' cards tell you. A failed card now explains itself, and the missing-checkout warning now means something narrower. |

No configuration key changes, so `configuration.md` and `share/config.example.toml` are
untouched and the drift test stays green. No audit action or record shape changes, so
`audit-log.md` is untouched — the `github.comment` record's *content* changes, but its shape
does not, and that page documents shapes.
