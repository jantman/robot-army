# Implementation Plan: Onboarding's Repository Lookup Leaves an Audit Record

**Branch**: `robot-army/issue-64-onboarding-s-github-lookup-writes-no` | **Date**: 2026-09-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260912-175131-get-repo-audit-record/spec.md`

## Summary

`GitHubReader.get_repo`, onboarding's one request to GitHub, writes no audit record, so the 005
quickstart's check for SC-009 counts 0 lookups whatever onboarding does. The check cannot fail,
which means it cannot pass either.

The fix is in `get_repo` alone. It writes one `github.get_repo` record per lookup, carrying the
method, the path, the status and whether the repository exists (R1, R2). A lookup that fails
writes the same record with outcome `error` and then re-raises unchanged (R3). The status of a
failed HTTP response reaches that record through a new optional `status` attribute on
`TransportError`, set by `_request` at the one place it raises for a response (R4). The
audit-log guide documents the action and narrows the poll exemption it was sheltering under (R6).

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added

**Storage**: none. The audit log gains one action; no table or state file changes.

**Testing**: `pytest` via `uv run pytest`. `tests/unit/test_github_boundary.py` already drives
`get_repo` through an `httpx.MockTransport` with the `audit` fixture. The new tests read the
records back with `audit.read_records`.

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: none. One JSON line per onboarding attempt.

**Constraints**:
- The request itself (method, path, count, retries) is unchanged (FR-006). The existing SC-009
  test is the proof.
- Path only, never a query string, header or host (FR-005). `get_repo` sends no query, and the
  recorded value is the path string it builds, not the URL `httpx` sends.
- `source_unreachable` refusals are unchanged (FR-004). The exception is re-raised, not wrapped.

**Scale/Scope**:
- Source: `boundaries/__init__.py` (`TransportError.status`), `boundaries/github.py`
  (`_request` sets it; `get_repo` records)
- Tests: `tests/unit/test_github_boundary.py` (200, 404, HTTP failure, connection failure,
  retried-then-succeeded, no credential); `tests/integration/test_onboard.py` (one record per
  attempt, formatted so the quickstart's grep matches)
- Docs: `docs/guide/audit-log.md` (the issue #64 record; the "deliberately not logged" row)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design. See the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One record call in one method, plus one optional exception attribute with a default. No module, dependency, table or config key. The alternative of recording in `_request` for every call would change the volume of every poll's reads to fix one method (R2). |
| **II. Single-User, Local-First** | Unchanged. |
| **III. Total Accountability** | **What this logs:** it closes a gap. Onboarding's lookup is a network request, and it was the one onboarding step with no record; the poll exemption for successful GETs never covered it, because no aggregate stands in for it (R6). Every lookup now writes `github.get_repo`, success or failure. **Deliberately unlogged:** nothing new. The other single-item reads (`get_issue`, `is_closed`) keep their status and are named in the spec as out of scope. The record carries no secret: the path is built from the repository key, and the error message `_request` builds holds the path and at most 400 characters of GitHub's response body, never a header (R5). |
| **IV. Interruption Tolerance** | **If it is killed halfway:** the lookup is a read. Killed before the record is written, the log lacks one line for a request that changed nothing, and onboarding had not yet written `repo.onboard` either, so the log is consistent: no result, no lookup. Timeouts and retries are `_request`'s, unchanged. |
| **V. Public Code, Unsupported Project** | No outside consumers. `TransportError` gains an attribute and loses nothing. |
| **Operating Constraints** | Visible through `robot-army log`. Nothing outward-facing is added. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests cover success, the 404 fact, both failure paths (HTTP status and exhausted connection retries), the retried-then-succeeded case, and the absence of the token. An integration test runs the quickstart's grep against real `onboard` attempts. The existing SC-009 test runs unmodified. |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260912-175131-get-repo-audit-record/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R6
├── data-model.md         # the github.get_repo record; TransportError.status
├── quickstart.md
├── contracts/
│   └── github-get-repo-record.md   # every case of the record
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/boundaries/
├── __init__.py   # TransportError(message, *, status=None)
└── github.py     # _request passes status= on an HTTP failure; get_repo records

tests/unit/test_github_boundary.py      # every contract case
tests/integration/test_onboard.py       # one record per attempt; the grep matches
docs/guide/audit-log.md                 # the issue #64 record; the exemption row
```

**Structure Decision**: no new module. The record lives in `get_repo`, which is the only place
that knows a response is an answer to "does this repository exist?".

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | None. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `TransportError.status` is data on an existing exception, and `_request` sets it for every HTTP failure, not only this caller's. |
| Anything on by default that was not? | No. |
| A new audit action or record shape? | Yes, `github.get_repo`. `audit-log.md` documents it, per `CLAUDE.md`'s table. |
| Which other guide pages? | None. `1-setup.md` describes onboarding's checks, not its records; the audit log page is where records are looked up. |

**Verdict: PASS, unchanged.**
