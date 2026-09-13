# Feature Specification: Onboarding's Repository Lookup Leaves an Audit Record

**Feature Branch**: `robot-army/issue-64-onboarding-s-github-lookup-writes-no`

**Created**: 2026-09-12

**Status**: Draft

**Input**: GitHub issue jantman/robot-army#64 — "Onboarding's GitHub lookup writes no audit
record, so SC-009's stated verification always reports zero" (labels: bug, robot-army). Found
during the issue #1 verification round on 2026-08-30, running 005 quickstart scenario 9.

**Baseline**: written against `main` at 0285ba8. Verified in that tree rather than assumed:

- Onboarding's only source-system request is `GitHubReader.get_repo`
  (`boundaries/github.py:1063`): `GET /repos/{owner}/{name}` with `allow_404=True`, called from
  `repos.eligibility` (`repos.py:554`) before `repo.onboard` is written.
- `get_repo` writes no record on success or on a 404. `_request` writes `github.retry` for each
  retried attempt and `github.request` for an HTTP failure other than a 404, but it writes
  nothing when the request succeeds, and nothing final when retries are exhausted by
  connection errors.
- `robot-army log` prints `action [outcome] entity  {detail as JSON}` (`operations.py:5594`),
  without `target`, so the quickstart's `grep -c 'github.*"/repos/'` can match only a
  `github.*` record whose **detail** carries the path. No record does today, so the count is 0
  whatever onboarding does.
- `docs/guide/audit-log.md`'s "deliberately not logged" table exempts successful read-only
  GitHub GETs *because one aggregate record per repository per poll stands in for them*.
  Onboarding is not a poll and no aggregate covers it, so that exemption does not apply to
  this lookup. The gap is undocumented, which is what Principle III forbids.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every onboarding lookup is in the log (Priority: P1)

The maintainer onboards a repository, or tries to onboard one that does not exist, and then
reads the audit log to confirm what the system asked GitHub. Today the log shows the
`repo.onboard` result and nothing about the request that produced it, so SC-009 ("one lookup,
never an enumeration of the author's repositories") can only be confirmed by reading source.

After this change, each lookup leaves exactly one `github.get_repo` record naming the method,
the path it requested and the status GitHub answered with. The 005 quickstart's check for
scenario 9 counts one per attempt, as written.

**Why this priority**: It is the reported defect, and the only way SC-009 can be verified from
the log.

**Independent Test**: Onboard one existing repository and attempt one that does not exist, then
run `robot-army log --since 10m | grep -c 'github.*"/repos/'`. It prints 2.

**Acceptance Scenarios**:

1. **Given** a repository that exists, **When** it is looked up during onboarding, **Then**
   exactly one `github.get_repo` record is written, with outcome `ok`, the method `GET`, the
   path `/repos/{owner}/{name}`, and status 200.
2. **Given** a repository that does not exist, **When** it is looked up, **Then** exactly one
   `github.get_repo` record is written with outcome `ok` and status 404, saying the repository
   does not exist. A missing repository is a fact about the repository; the refusal belongs to
   `repo.onboard`, which already records it with `cause: no_such_repository`.
3. **Given** any lookup, **When** its record is printed by `robot-army log`, **Then** the line
   matches `github.*"/repos/`.

---

### User Story 2 - A lookup that fails is recorded as that lookup (Priority: P2)

A bad token or a dropped network means the lookup fails and onboarding refuses with
`source_unreachable`. The retries and HTTP failure are recorded already, under generic names,
but nothing records that *the repository lookup* failed, so a count of lookups would miss
exactly the attempts that went wrong.

After this change a failed lookup also writes one `github.get_repo` record, with outcome
`error`, the same method and path, the status if GitHub answered, and the error.

**Why this priority**: Without it, "one record per lookup" holds only when the lookup works,
and the count the quickstart relies on silently undercounts failures.

**Independent Test**: Make GitHub answer 401 for the lookup; attempt to onboard. One
`github.get_repo` record with outcome `error` and status 401 is written, and onboarding still
refuses with `source_unreachable`.

**Acceptance Scenarios**:

1. **Given** GitHub answers a non-404 error status, **When** the lookup runs, **Then** one
   `github.get_repo` record with outcome `error` carries the method, the path and that status,
   and the lookup still fails as before.
2. **Given** every attempt fails to connect, **When** retries are exhausted, **Then** one
   `github.get_repo` record with outcome `error` carries the method, the path and the error,
   with no status, and the lookup still fails as before.

### Edge Cases

- **Retried then succeeded**: the retries are recorded as `github.retry` as today, and the
  lookup writes a single `github.get_repo` for its final result. The record is per lookup, not
  per HTTP attempt.
- **Case-mismatched key**: the recorded path is the one requested, from the key as typed, so the
  record says what was asked; the canonical name is already reported by onboarding.
- **Credentials**: the record carries a path built from the repository key. No query string, no
  header, no token, and no full URL.
- **Simulated effect levels**: onboarding's lookup is a real read at every level, as today, and
  records the same way.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each repository lookup MUST write exactly one `github.get_repo` audit record,
  whatever its result.
- **FR-002**: The record MUST carry the repository as its entity, and in its detail the HTTP
  method, the request path (path only, no query string or host), and the status GitHub returned
  when it returned one.
- **FR-003**: A 200 and a 404 MUST both be recorded with outcome `ok`; the detail MUST say
  whether the repository exists.
- **FR-004**: A lookup that fails MUST be recorded with outcome `error` and the error, and MUST
  then fail exactly as it does today, so onboarding's `source_unreachable` refusal is unchanged.
- **FR-005**: The record MUST NOT contain a credential, a header, or a query string.
- **FR-006**: The existing `github.retry` and `github.request` records, and the number and shape
  of requests the lookup makes, MUST be unchanged.
- **FR-007**: The audit-log guide MUST document the new action, and its "deliberately not
  logged" table MUST no longer read as though this lookup were covered by the poll exemption.

### Key Entities

- **`github.get_repo` record**: one per lookup. Entity `repo:{owner}/{name}`; detail `method`,
  `path`, `status` (absent when no response arrived), `exists` on success, `error_type` and
  `error` on failure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The 005 quickstart scenario 9 check, run unmodified after N onboarding attempts
  that reach GitHub, prints N.
- **SC-002**: An implementation that enumerated the author's repositories instead would show in
  that same check as a path other than `/repos/{owner}/{name}`, so the check can now fail.
- **SC-003**: No audit record written by an onboarding attempt contains the token.
- **SC-004**: Every existing onboarding and GitHub boundary test passes unmodified.

## Assumptions

- One record per lookup, written after the result is known, rather than an intent/outcome pair:
  the lookup is a read that changes nothing outside the process, and the audit log already
  records reads as single records (`github.poll`, `github.project.read`).
- The action is named for the operation, `github.get_repo`, like `github.project.read` and
  `github.pull_requests.*`, rather than a generic `github.request` that would collide with the
  existing failure record.
- The other single-item reads that write no record on success — `get_issue`, used by `show`,
  `retry` and Trello intake, and `is_closed` during reconciliation — are out of scope. The issue
  names onboarding's lookup and its verification; those calls sit in cycles and commands whose
  own records account for them, and extending the change to them is a separate decision.
- The 005 quickstart is project history and is not edited: this change makes its check work as
  written.
