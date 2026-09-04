# Research: Retry Re-Verifies the Author

Phase 0 for [plan.md](plan.md). Each entry is a decision that displaced a plausible
alternative; the ones that were never in doubt are not here.

---

## R1 — `retry` calls `poll.evaluate`, it does not reimplement it

**Decision.** `operations.retry` fetches the issue through
`boundaries.issue_reader.get_issue(repo_key, number)` and passes the result to
`poll.evaluate(issue, config=…, repo_key=…, onboarded=…)` — the same function the poller
calls, unchanged.

**Rationale.** FR-003 asks that the two callers can never disagree about what makes an
issue eligible. There is exactly one way to guarantee that and it is to share the code.
`evaluate` is already shaped for this: it is a pure function of an `Issue`, a `Config`, a
repository key and one boolean, touching no connection, no boundary and no clock. Nothing
about it assumes it is being called from a poll loop.

**Alternatives considered.**

- *A dedicated author-only check in `operations`.* This is the issue's "at minimum" option
  and it is the wrong minimum. It fixes the author condition and leaves the closed-issue
  and missing-label conditions bypassable by exactly the same route, so the next reader has
  to re-derive which of the four conditions `retry` honours. It also invites the reason
  string to be matched rather than the condition to be evaluated — see R2.
- *Moving `evaluate` into a new shared module.* It already lives in the module that owns
  the concept and has no import that `operations` cannot take: `operations` imports `poll`
  today. Relocating it would be a rename dressed as a design.

## R2 — The verdict is re-derived, never inferred from the stored `blocked_reason`

**Decision.** Nothing reads `blocked_reason` to decide anything. The refusal is produced by
re-running the evaluation against a fresh read.

**Rationale.** The issue offers "refuse outright when `blocked_reason` names the author
check" as a fallback. It is a string match on a message that exists to be read by a human,
which means the security boundary would be maintained by whoever next edits that f-string.
It is also wrong in both directions: an item whose author has since been corrected in
configuration would be refused forever, and an item that failed for an unrelated reason
after being smuggled to `ready` by today's bug would carry a `blocked_reason` naming
something else entirely and sail through (FR-007).

**Alternatives considered.** Storing a machine-readable rejection code alongside the reason
so the match is structural rather than textual. That is a new persisted concept whose only
consumer would be a check that a live re-read already answers better, and it would still be
answering "why was this blocked *then*" when the question is "is it blocked *now*".

## R3 — A read failure refuses; it never falls back to the stored copy

**Decision.** `BoundaryError` from `get_issue` and a `None` return are both refusals, with
distinct messages. Neither consults `item.title` / `item.body`.

**Rationale.** FR-006, and the module docstring in `boundaries/github.py` that the rest of
this codebase already obeys: "it did not happen" and "I could not ask" are different facts.
The stored copy is exactly the thing that cannot be trusted, so a fallback to it would be
the original defect with a network hiccup as its trigger — and it would be the failure mode
hardest to notice, because it looks like success.

`get_issue` returning `None` covers both "deleted" and "the token cannot see it"; those are
indistinguishable from outside and the refusal says so in those terms, following the
wording `operations.preview_prompt` already uses for the same ambiguity.

## R4 — The repository preconditions are checked before the read

**Decision.** `repos.resolve` and `dispatch.check_gates` run first, exactly where they run
today; the issue read happens only after they pass.

**Rationale.** An item whose repository has been un-onboarded, moved, or whose fingerprint
has drifted cannot dispatch whatever the issue says, so a network round trip spent on it
buys nothing and adds a failure mode to a path that already had a definite answer. This
also settles the spec's last edge case — one refusal is reported, the first one reached —
without a rule, because the function returns at the first refusal like every other
precondition in this file.

**Alternatives considered.** Reading first so that a refusal always carries fresh content
for the queue to display. Rejected: it makes the cheap, offline, always-available check
depend on the network one, and FR-009's content refresh is still reached on every path that
performs a read.

## R5 — The item's stored content is refreshed on both outcomes, in one place

**Decision.** Immediately after a successful read, `title`, `body`, `labels` and the new
`author` column are written via `db.update_work_item_columns`, before the verdict is
consulted. The refused path and the allowed path share that write.

**Rationale.** FR-009 requires it on both outcomes, and doing it once before the branch is
what makes that structural instead of two call sites that must be kept in step. It is also
the correct order for interruption (Principle IV): a process killed between the refresh and
the transition leaves an item still `failed`, with accurate content and its old reason — a
state the next retry corrects completely. Killed the other way round, a `ready` item would
carry stale content, which is the thing this feature exists to prevent.

`labels` is stored as the same JSON text the poller writes, through
`states.dumps_labels`, so there is one encoding rather than two.

## R6 — The author is persisted on the work item; dispatch does not re-read

**Decision.** Migration 011 adds `work_items.author TEXT`, written by
`db.insert_work_item` at discovery and refreshed by `retry`. `dispatch` compares that
column against `config.github.author` and refuses on mismatch, replacing
`author=config.github.author` in the `Issue` it constructs.

**Rationale.** FR-014 wants a real check where a fabricated value stands today. The two
ways to get one are to persist what was read or to read again at dispatch. Persisting wins
on every axis that matters here: no network call on the dispatch path (which currently has
none of its own and would gain a new timeout, retry and backoff story), no behaviour that
differs between a dispatch and a redispatch, and a check that costs one string comparison.
Under Principle I it is also the design with fewer moving parts — a nullable column against
an HTTP client in a new place.

Re-reading at dispatch is what a full fix for RA-04's poll-to-dispatch half would need. It
is deliberately not attempted here; the spec's Assumptions say so and FR-018 requires the
security analysis to say so too.

**Alternatives considered.** Deriving the author from `source_url`. It is not in there —
the URL names the repository and issue number, not who wrote it.

## R7 — A missing recorded author refuses the dispatch and names `retry` as the recovery

**Decision.** `author IS NULL` is a distinct refusal with its own message, not a pass and
not a crash (FR-015, FR-017).

**Rationale.** Rows written before migration 011 have no author. Trusting them would
reproduce the hole, because a `ready` row from before this change may have reached `ready`
through the bug being fixed here — which is precisely the provenance that cannot be
established after the fact. Refusing them without a recovery would strand them. Refusing
them *into* `retry`, which now re-reads and re-verifies and writes the author back, makes
the upgrade self-healing along the path this feature already hardens.

The realistic blast radius is a handful of in-flight items on one machine, and Principle V
is explicit that breaking changes are acceptable where they serve the single user.

**Alternatives considered.** Backfilling `author` with `config.github.author` in the
migration, the way migration 008 backfilled `transcript_checked_at`. Rejected, and the
contrast is instructive: 008 backfilled a fact it could derive correctly (those sessions
*had* been judged by the old inline check). Here the migration would be writing an
unverified claim into the column whose entire purpose is to record a verified one — the
same thing migration 005's comment refuses to do with clone paths.

## R8 — The author check is not behind `skip_gates`

**Decision.** The author comparison sits outside the `if not skip_gates:` block in
`_dispatch_item`, alongside the `repos.resolve` refusal rather than alongside
`check_gates`.

**Rationale.** `skip_gates` is a parameter no caller passes as `True` today, so this changes
no current behaviour — but a check whose whole documented character is "this cannot be
disabled" must not sit under a flag named *skip gates*. Placing it there would recreate the
class of defect being fixed, and would do it in the file where the next reader is most
likely to trust the surrounding structure.

Its placement also means `resume` and `restart` are covered: both reach the launch through
`dispatch_item`, which is the finding RA-05 observes about the concurrency cap and holds.
This change does not close RA-05, but it does not extend it either.

## R9 — Interface text describes the read, not just the outcome

**Decision.** The web `ActionSpec` description and the CLI `retry` help both say the issue
is re-read from GitHub and its eligibility re-checked.

**Rationale.** FR-011 and FR-012. Once R1 lands, today's sentence — "Refused, with the
reason, if the condition that blocked it still holds" — becomes technically true, which is
the trap: it would still fail to warn that a retry now makes a network call that can itself
fail, and it would still leave the maintainer with no way to tell the fixed build from the
broken one by reading the interface. The text names the mechanism because the mechanism is
what the maintainer is being asked to trust.

## R10 — Tests need one fixture change, and it is a defaulted parameter

**Decision.** `tests/conftest.py`'s `seed_item` gains `author: str = "jantman"`, matching
the `github.author` its own config fixture sets.

**Rationale.** Every existing dispatch test seeds through `seed_item`, and under FR-015 a
row with no author no longer dispatches. Defaulting to the configured author keeps those
tests testing what they were written to test, while a test of the refusal passes
`author="mallory"` explicitly and a test of FR-015 writes `NULL` directly.

`FakeIssueReader` needs nothing: it already implements `get_issue`, already records
`get_issue_calls`, and already carries `raise_on_get_issue` for the transport-failure path
— all added for the prompt-preview feature, which reads issues the same way.
