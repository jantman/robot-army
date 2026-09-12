# Research: Onboarding's Repository Lookup Leaves an Audit Record

## R1 — One record per lookup, after the result

**Decision**: `get_repo` writes a single `github.get_repo` record once the lookup has a result.
It is not an intent/outcome pair.

**Rationale**: The lookup is a read and changes nothing outside the process. The constitution's
"log before" rule is for irreversible or outward-facing actions, and the log's other reads
(`github.poll`, `github.project.read`) are single records. One line also keeps the quickstart's
count honest: an intent/outcome pair would print two matching lines per lookup.

**Alternatives considered**: `audit.action(...)` as `github.comment` uses. Rejected: it doubles
the lines per lookup and implies a state change.

## R2 — Record in `get_repo`, not in `_request`

**Decision**: The record is written by `get_repo`, named for the operation.

**Rationale**: `_request` serves every read, including the per-minute poll whose successful
requests are deliberately aggregated ("deliberately not logged", `audit-log.md`). A record in
`_request` would either break that exemption or need a flag saying which callers are exempt,
which is a knob with one caller. Only `get_repo` knows that a 404 is the answer "no such
repository" rather than a failure.

**Alternatives considered**: A generic `github.request` success record in `_request`. Rejected
for the reason above, and because the name is already taken by the failure record, so one name
would carry two meanings.

## R3 — A failed lookup is recorded, then re-raised unchanged

**Decision**: `get_repo` catches `TransportError`, writes `github.get_repo` with outcome
`error`, and re-raises the same exception.

**Rationale**: FR-001 is one record per lookup whatever the result. Without it, the lookups
that failed would be the ones missing from the count. Re-raising the same object keeps
`_resolve_for_onboarding`'s `source_unreachable` refusal byte-for-byte the same. The
existing `github.retry` and `github.request` records stay: they describe HTTP attempts, and this
record describes the lookup.

**Alternatives considered**: Recording only successes, and leaving failures to `github.request`.
Rejected: `github.request` carries its path in `target`, which `robot-army log` does not print,
so the quickstart's grep would still miss failed attempts. It also writes nothing when
connection retries are exhausted.

## R4 — The failed response's status reaches the record through `TransportError.status`

**Decision**: `TransportError` takes an optional keyword `status: int | None = None`. `_request`
passes it at the one place it raises for an HTTP response. The exhausted-retries raise leaves it
`None`, because no response arrived.

**Rationale**: The spec's acceptance scenario for a 401 needs the status on the lookup's own
record. Parsing it back out of the message would be fragile. The attribute is set for every HTTP
failure `_request` raises, not only this caller's, so it is data on the exception rather than a
special case. Every other raise site is unaffected by the default.

**Alternatives considered**: Omitting status from the failure record, since `github.request`
has it. Rejected: the record should be readable alone, which is Principle III's reconstruction
standard.

## R5 — What the record may carry

**Decision**: The detail has `method`, `path`, `status`, and `exists` on success, or
`error_type` and `error` on failure (message truncated to 400 characters). The entity is
`repo:{key}`. There is no `target`.

**Rationale**: `path` is the string `get_repo` builds from `_repo_path(repo_key)`, so it cannot
contain a host, a query string or a header, which covers FR-005 by construction. The error
message is the one `_request` already puts in exceptions: the method, the path and at most 400
characters of GitHub's body, which GitHub does not use to echo a token. It is also already in
the terminal output of a `source_unreachable` refusal. The path in `detail` is what makes the
quickstart's `github.*"/repos/` match, because `robot-army log` prints detail and not target.

## R6 — Documentation

**Decision**: A new "The issue #64 record" section in `docs/guide/audit-log.md`, and the
"deliberately not logged" row for successful GETs gains a sentence saying the exemption covers
reads inside a poll and that onboarding's lookup is recorded.

**Rationale**: `CLAUDE.md` maps a new audit action to `audit-log.md`. The exemption row is the
text a reader would take as licence for the gap, so it has to say where it stops.
