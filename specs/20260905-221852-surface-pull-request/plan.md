# Implementation Plan: Surface the pull request in the web UI

**Branch**: `robot-army/issue-143-link-pr-in-web-ui` | **Date**: 2026-09-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/20260905-221852-surface-pull-request/spec.md`

## Summary

A session's whole purpose is to open a pull request, and the web interface — the thing the
maintainer actually looks at — cannot name it. The only pull-request link on the entire site is
a bare `open PR: yes` inside the resume-decision block, computed live while the page renders,
visible on two states out of nine.

This closes that. One GraphQL request per work item per reconciliation pass asks GitHub for both
relationships the issue names — pull requests opened from the item's branch, and pull requests
GitHub reports as linked to the item's issue — merges them into one set, and stores it on the
work item. Every surface then reads the stored answer, so rendering a page costs no network call
at all.

Five research findings shaped the design, and four of them removed work:

1. **Both routes answer in one request** (R1). `closedByPullRequestsReferences(includeClosedPrs:
   true)` and `pullRequests(headRefName:)` are two fields of one `repository` query, verified
   live against issue #71 and pull request 142. There is no REST endpoint for the issue-linked
   half, so GraphQL is not a preference — it is the only thing that answers the question.
2. **`PullRequest.state` is `OPEN | MERGED | CLOSED`**, so FR-004's three outcomes cost nothing.
3. **The candidate rule terminates itself** (R4). Live states, plus any item whose stored pull
   request is still open. Once every pull request an item has is merged or closed, nothing can
   change and the item is never asked about again. No interval, no cap, no configuration key.
4. **Removing the render-time GitHub call improves correctness, not just speed** (R5).
   `remote_resume_signals` currently asks GitHub about a pull request while a page renders,
   which could disagree with what is stored. One source of truth is fewer moving parts *and*
   fewer wrong answers, and the freshness is identical because both windows are 60 seconds.
5. **`speckit_baseline` and `_observe_speckit` are the shape to copy** (R3, R8). A nullable JSON
   column whose `NULL` means "never looked", a write inside one transaction with its audit
   record, and an enumerated Principle III omission for cycles in which nothing changed. Three
   decisions already argued in this codebase, reused rather than re-litigated.

The feature is display-only by construction: it adds no state, no transition, and no consumer
that decides anything. `wait_for_merge` and `_resolve_closed_issues` are untouched.

## Technical Context

**Language/Version**: Python 3.11+, standard library first

**Primary Dependencies**: none added. `httpx` (already the GitHub transport), `sqlite3`,
`json` — all present

**Storage**: SQLite. Migration 013 adds `pull_requests TEXT` and `pull_requests_at TEXT` to
`work_items`

**Testing**: `pytest`, via `uv run pytest`

**Target Platform**: one Linux machine, one user

**Project Type**: single-process daemon plus a read-mostly local web interface

**Performance Goals**: one GraphQL point per refresh candidate per 60-second pass, against a
5000/hour budget. Zero GitHub requests while rendering any page (SC-004)

**Constraints**: no new configuration key; no outward-facing effect for a simulated item; every
outbound href through `pages.github_link`

**Scale/Scope**: two columns, one boundary method (replacing one), one reconcile pass, three
rendering surfaces

## Constitution Check

*Gate evaluated before Phase 0 and re-evaluated after Phase 1. Both passes below.*

### I. Simplicity First (YAGNI & KISS) — PASS

| Temptation | Rejected because |
|---|---|
| A `pull_requests` table | One consumer, no query ever filters or joins on a pull request, and the whole value is read and written as a unit. `labels` and `speckit_baseline` are JSON-in-TEXT for the same reason (R3) |
| A `pull_request_refresh_seconds` key | A knob with one caller and no second use in hand. The candidate rule bounds itself; there is nothing to tune (R4) |
| A cap on lookups per pass | Would only matter if the candidate set could grow without bound. It cannot: it is the live items plus items with an open pull request, and each of those resolves permanently (R4) |
| Keeping `open_pr_for_branch` alongside the new read | It would have zero callers. Principle V says nothing here is owed compatibility; it is replaced (R5) |
| Backfilling every historical `done` item | One GraphQL call per item of history, in one pass, to re-litigate work that finished. FR-016's third state exists so "we have not looked" can be said rather than guessed (R4) |

**Added, and justified**: one keyword-only parameter on `_graphql` naming the operation for its
audit record (R2). Not generality — the name simply stopped being a constant the moment a second
caller existed, and logging a pull-request failure as `github.project.partial` would answer the
wrong question.

**No new dependency.**

### II. Single-User, Local-First — PASS

No account, role, or permission is introduced. The GitHub token already configured for polling
answers these lookups; no new secret, no new configuration file, nothing new in a `[repos.*]
env`. All state is the existing SQLite database at its documented path. The interface remains
reachable only where it is already bound.

No credential can reach the audit log: the records carry item ids, pull-request numbers and
states, and a `github.com` URL the API returned — never a token, never a remote URL that could
embed one.

### III. Total Accountability — PASS, with one enumerated omission

Logged (R8, C3):

| Event | Action | Kind |
|---|---|---|
| The stored set changes | `work_item.pull_requests` | `ok`, inside the same transaction as the column write |
| A lookup fails | `reconcile.pull_requests_check` | `error`, with the item and the error |
| GraphQL returns data *and* errors | `github.pull_requests.partial` | `error`, before the read is failed |
| The HTTP request | already covered by `GitHubReader._request` — every retry and every `>= 400` individually |

**Enumerated omission, as the constitution's exception path requires**: *a refresh pass in which
an item's set did not change writes no record.* With a 60-second cycle and sessions that run for
hours, the alternative is a log in which nearly every line says a pull request did not change.
Every transition is still recorded with its time; `pull_requests_at` on the row carries the last
confirmation; and the pass summary carries a change count. This is the identical omission
`reconcile._observe_speckit` documents, for the identical reason.

Nothing is swallowed. Only `TransportError` is caught, and only to record it; any other
exception reaches the pass's own handler. An empty list is never returned to mean "I could not
ask" — that distinction is the whole of FR-016.

**No new outward-facing action.** Every GitHub call here is a read, and reads are real at every
effect level already. Nothing is written to GitHub, no notification is sent, and no simulated
item causes a request (C2 rule 1).

### IV. Interruption Tolerance — PASS

*What happens if it is killed halfway through?* (R9) The refresh is a loop; each item's write is
one `UPDATE` of both columns inside one `db.transaction`. A kill leaves every item processed so
far updated and every item after it untouched — indistinguishable from a pass that has not run
yet, and repaired 60 seconds later by the next pass. No item can hold a half-written set,
because the set is one column written once. Nothing is staged and nothing is two-phase.

Every network call goes through `GitHubReader._request`, which already carries an explicit
timeout and bounded backoff. This adds no unbounded loop and no indefinite block.

The columns are additive and nullable, so migration 013 cannot fail on a populated database and
a pre-013 row is a legitimate `NULL` rather than a broken one.

### V. Public Code, Unsupported Project — PASS

No credential, hostname, or personal datum is committed. `open_pr_for_branch` and
`remote_resume_signals`' `open_pull_request` key are removed outright rather than deprecated —
there are no outside consumers and none are owed. Documentation is the guide pages the change
touches, written for the author's future self.

### Development Workflow — PASS

Spec, plan, tasks, implement, with this Constitution Check. Unit tests are required for every
new or changed unit; the refresh is a state-writing pass reading external input, so it
additionally carries failure- and interruption-path tests (C3, quickstart §4). The full suite
must pass before this is complete.

### Post-Phase-1 re-evaluation

Design artefacts changed nothing above. The one thing worth re-recording: Phase 1 *removed* a
component rather than adding one — the render-time GitHub call in `remote_resume_signals` —
which moves Principle I in the right direction, and the queue page was deliberately left without
a column (SC-007) rather than made uniform for its own sake.

## Project Structure

### Documentation (this feature)

```text
specs/20260905-221852-surface-pull-request/
├── plan.md                              # this file
├── spec.md
├── research.md                          # R1–R10
├── data-model.md                        # migration 013 and the model field
├── quickstart.md
├── contracts/
│   └── pull-request-discovery.md        # C1–C5, normative
├── checklists/
│   └── requirements.md
└── tasks.md                             # /speckit-tasks, not this command
```

### Source code

```text
src/robot_army/
├── boundaries/
│   ├── __init__.py       # PullRequest state domain; IssueSourceReader.pull_requests_for
│   └── github.py         # the GraphQL document; _graphql gains an operation name (R2)
├── migrations.py         # _migration_013: two columns on work_items
├── models.py             # WorkItem.pull_requests, .pull_requests_at, .pull_request_list
├── reconcile.py          # _refresh_pull_requests, before _resolve_closed_issues
├── operations.py         # pull_request_view; remote_resume_signals loses its PR call
└── web/pages.py          # item_view, active_view, _signal_row/_signals_cell

tests/unit/
├── test_pull_requests.py            # the boundary read (C1)
├── test_reconcile_pull_requests.py  # the refresh, its failures and its interruption path
├── test_web_pages.py                # the three rendering states, on every surface
├── test_migrations.py               # 013 against a populated pre-013 database
└── test_resume_signals.py           # updated: no PR call while rendering

docs/guide/
├── operating.md          # the web interface, per CLAUDE.md's table
├── state.md              # a database table's shape changed
└── audit-log.md          # three new actions, and the enumerated omission
```

**Structure Decision**: the existing flat single-package layout. This feature adds no module —
every change lands in a file that already owns that concern, which is why the diff is wide and
shallow rather than a new subsystem.

## Documentation obligations

`CLAUDE.md` makes these non-optional, and each is a real change rather than a courtesy:

| File | Why |
|---|---|
| [`docs/guide/operating.md`](../../docs/guide/operating.md) | the web interface gained a column and a field, and the resume-decision signals changed source |
| [`docs/guide/state.md`](../../docs/guide/state.md) | `work_items` gained two columns, and their `NULL`-versus-`[]` distinction is exactly the kind of thing this page exists to record |
| [`docs/guide/audit-log.md`](../../docs/guide/audit-log.md) | `work_item.pull_requests`, `reconcile.pull_requests_check` and `github.pull_requests.partial`, plus the enumerated omission |

**No configuration key is added**, so `exampleconfig.py` and `share/config.example.toml` are
untouched and the drift test stays green without regeneration. That is worth stating rather than
assuming: it is the second of CLAUDE.md's two rot-prone obligations, and the answer here is that
it does not apply.

## Complexity Tracking

No Constitution Check violation. Nothing to justify.
