# Research: `show` Reports What Blocks an Item Now

Each entry: the decision, why, and what was rejected. Written against `main` at f5a4d72.

## R1 — Recompute, don't relabel

**Decision**: `show` computes the current blocker of a `failed` item when it renders.

**Rationale**: The issue prefers this and the tree agrees: the check is `retry`'s, it exists,
and it is local. "What blocks this item now?" is the question `show` is asked. A timestamp label
on a stored sentence tells the reader the answer might be stale. It does not give them the answer.

**Alternatives**: relabelling `blocked` as "recorded at <time>". Rejected as the weaker fix the
issue names. There is also no `failed_at` column to label it with (`models.WorkItem` has
`updated_at`, which moves for other reasons).

## R2 — One shared function, in `operations`, for `show` and `retry`

**Decision**: `operations._local_blocker(ctx, item, *, trust_file, raise_anomalies)` performs
exactly `retry`'s pre-read checks: `repos.resolve`, and if that yields a repository, then
`dispatch.check_gates`. It returns a small frozen dataclass, `LocalBlocker(reason, unresolved)`:
`reason` is `None` when nothing blocks the item, and `unresolved` says which step refused.
`retry` is rewritten to call it and to branch on the result into its two existing refusals, with
their existing lines, audit records and data unchanged.

**Rationale**: FR-002. Two call sites kept in step by hand is how `show` and `retry` came to
disagree. `retry` needs to know which step refused because its two refusals are worded and
recorded differently (the `repo is None` refusal has no "refusing to retry" header and no
`data`). The dataclass carries that without `retry` repeating the resolution.

**Alternatives**: a function in `dispatch`. Rejected because `repos.resolve` returning `None`
is `retry`'s wording, not a dispatch gate, and both callers are in `operations`. A function
that raises `DispatchBlocked` for the unresolved case was also rejected: it would merge the two
refusals `retry` distinguishes.

## R3 — The anomaly write is suppressed for `show`, not removed

**Decision**: `check_gates` gains a keyword-only `raise_anomalies: bool = True`, threaded through
`_check_recorded_location` to `_raise_location_anomaly`. With `False` the `DispatchBlocked` is
raised with the same message but no anomaly row is written. `show` passes `False`. `retry` and
dispatch keep the default.

**Rationale**: FR-007 and FR-008. `show` is an inspection, and the web item page renders it on
every request. The anomaly is `dispatch`'s and `retry`'s report that the machine changed under
an approval. That behaviour is theirs and stays, and `raise_anomaly` already de-duplicates, so
nothing else in the reporting changes. The default is the current behaviour, so no existing
caller changes.

**Alternatives**: catching the anomaly after the fact or rolling back a transaction. Rejected:
`_raise_location_anomaly` commits its own transaction, and undoing a write is not the same as
not making it. A separate read-only copy of the location checks was rejected as the "two copies"
failure R2 exists to avoid.

## R4 — What "could not be determined" catches

**Decision**: in `show` only, `BoundaryError` and `OSError` raised from `_local_blocker` are
caught and reported as `could not be checked now: <error>`. `retry` lets them propagate as it
does today.

**Rationale**: FR-006 and FR-008. Verified in the tree: the git calls on this path use
`check=False` and return `None` on failure (`show_file_at_ref`, `remote_url`), `select_remote`
catches `BoundaryError`, and `repos.base_ref` documents that it raises for no operational
condition. The realistic residue is a subprocess timeout or an unreadable path. `show` is
read-only and must not become a traceback on the machine that is already misbehaving, which is
the same reasoning `_reattach_lines` records for its probe. Catching wider than these two would
hide programming errors, which Principle III forbids.

## R5 — Only `failed` items are checked

**Decision**: the check runs for `state == failed` and for no other state. A stored
`blocked_reason` on any other row is printed labelled `(recorded, not re-checked)`.

**Rationale**: FR-010. `retry` applies only to `failed`, and the question the check answers is
"what would `retry` refuse for?". A queued item's holds are computed live by `ordering.plan`
already. `test_web_views` shows a `discovered` row can carry a `blocked_reason`, so the
non-failed branch is real and needs its label.

## R6 — Presentation

**Decision**: the `failure` line is unchanged: history, printed verbatim. The `blocked` line
becomes the current verdict, suffixed `(checked now)`. When the verdict names a blocker that
differs from a non-empty stored `blocked_reason`, the suffix is
`(checked now; not the reason recorded when it failed)`. When nothing blocks, the line says so
and names `retry` re-reading the issue. The stored `blocked_reason` gets a `recorded` line of its
own only when it is not already shown word for word on the `failure` line. Today's three writers
always write the pair identically, so that line is normally absent.

**Rationale**: FR-003 to FR-006. "Differs from the reason recorded" is the honest claim. "The
reason it failed has been resolved", which the issue suggests, is true only when the stored
reason came from an earlier gate than the current one. The gates stop at the first failure, and
an eligibility reason (author, label) cannot be checked without a read at all. The reader has
both sentences on adjacent lines and can see which one is current.

## R7 — No cache

**Decision**: the verdict is computed on every render, with no cache.

**Rationale**: One item per render, and only for failed items. The reader of a failed item's
page is typically fixing the condition and refreshing to see whether it cleared, so freshness is
the point. The RA-14 five-second cache exists for `/interrupted`, which renders a card per item.
It is not a precedent for a single-item view.

## R8 — The command records

**Decision**: the `git` calls the check makes are recorded by the boundary as `git.subprocess`,
like every other `git` call. The spec was narrowed so FR-007 and SC-003 say so.

**Rationale**: `subproc.run` writes a record whenever it is handed an audit log, and the
boundary always hands it one. That is Principle III's "command execution" clause, and `show`
already produces these records through `local_resume_signals`. Suppressing them for one caller
would be an undocumented gap in the log, which is exactly what the constitution forbids.
