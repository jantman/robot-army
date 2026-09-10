# Research: A Stored ETag Is Only Replayed Against the Request That Produced It

## R1 — Where the comparison lives

**Decision**: in `GitHubReader.poll`. It receives the stored ETag *and* the stored request line,
builds the request it is about to send, and sends `If-None-Match` only if the two lines are
identical.

**Rationale**: the boundary is the only code that knows the request it sends, and it already
writes the `github.poll` record that FR-007 needs to carry the reason. Putting the comparison in
`poll.py` would need a second protocol method ("what request would you send?") whose answer must
agree with what `poll` then sends — two methods kept in step by hand, which is the drift FR-004
forbids.

**Alternatives**: comparing in `poll.py` via `issue_reader.poll_request(repo_key)` — rejected as
above. Having `poll.py` compare `config.github.label` to a stored label — rejected: fixes only the
observed parameter (the issue's option 1), and the trap reopens with the next parameter.

## R2 — What is recorded

**Decision**: the request line, as plain text:
`/repos/<percent-encoded owner>/<percent-encoded name>/issues?<urlencoded params, sorted by key>`.

**Rationale**: it is the request, so equality of the line is equality of the request — including
the path, which a repository-key encoding change would alter. Sorting makes it independent of
dict order. Plain text rather than a hash because the row is then self-explanatory to anyone
running `sqlite3` (Operating Constraints prefer human-inspectable data), and the audit record can
show *what* changed, not merely that something did. It is ~100 bytes per repository.

**Alternatives**: a SHA-256 of the canonical form — rejected: saves nothing that matters and
throws away the explanation. The label alone — rejected in R1.

## R3 — What is not recorded

**Decision**: headers (`Accept`, `X-GitHub-Api-Version`, `User-Agent`, `Authorization`) are not
part of the line.

**Rationale**: `Authorization` carries the token and must never be persisted (FR-008,
Principle V). The other three are client constants set once in `_http()`; changing one is a code
change shipped with a restart, and none of them is configurable. If one ever becomes a config
value it belongs in the line, and the docstring says so.

## R4 — Rows with an ETag and no recorded request

**Decision**: treated as not matching. No backfill in migration 015.

**Rationale**: on the machine that exhibited the defect nothing can say which label those ETags
answered — the verification round swapped it twice. Backfilling the line from the current
config would assert that every stored ETag answers today's request, which is *exactly the false
assumption this issue is about*, written into the one column whose purpose is to make it
checkable; it would re-create the blindness on the affected machine. Treating NULL as a mismatch
costs one unconditional request per repository, once. The same argument as migration 011 makes
about `work_items.author`: a NULL that means "never recorded" is honest; a backfilled guess is not.

## R5 — A 200 without an `ETag` header

**Decision**: store no ETag (`None`), rather than carrying the previously stored one forward as
today.

**Rationale**: a 200 means the stored ETag did not match (or was not sent). Carrying it forward
was already meaningless; with the request now recorded alongside it, carrying it forward would
pair it with a request whose response did not supply it (FR-005). GitHub always sends an ETag on
this endpoint, so in practice this changes nothing but the invariant.

## R6 — A startup check on the configured label

**Decision**: not built.

**Rationale**: the per-poll comparison runs on the first poll after every start, and compares the
whole request rather than one parameter of it. A startup check would be a second mechanism
performing a subset of the same comparison.

## R7 — `robot-army poll --no-cache`

**Decision**: not built.

**Rationale**: with a daemon running — the normal case — `poll` writes an option-less marker file
and the daemon polls every repository on its own terms (`operations.poll_now`); `--repo` is
already recorded-but-not-honoured on that path for this reason. The flag would therefore be
silently ignored where it would be used. Honouring it would need a second marker format for a
diagnostic whose known cause this feature removes. The issue listed it as a nice-to-have; that is
not a present need under Principle I.

## R8 — The protocol shape

**Decision**: `IssueSourceReader.poll(repo_key, etag, *, etag_request)` with `etag_request`
keyword-only and **required**; `PollResult` gains `request: str`, the line the result answered.

**Rationale**: required rather than defaulted so a caller cannot forget it — a forgotten argument
would silently cost a full listing every minute. Keyword-only so the two strings cannot be
swapped positionally. `PollResult.request` is what `poll.py` persists, so the value stored is the
value the boundary actually sent rather than one `poll.py` reconstructs.

**Alternatives**: a `CachedListing(etag, request)` value type passed in and returned — rejected:
`PollState` already holds the pair, and a type with no behaviour to carry the same two fields
between two functions is one more name for no protection a keyword-only argument does not give.
