# Phase 0 Research: Trello Source

Decisions taken before design, each with what was rejected. Referenced as R1..R16 from `plan.md`,
`data-model.md`, and the contracts.

---

## R1 — Trello is a sixth boundary, not a second `IssueSource`

**Decision**: add `CardSourceReader` and `CardSourceWriter` protocols alongside the existing five
seams, with their own row in the effect table. Do **not** widen `IssueSourceReader` into a generic
`WorkItemSource`.

**Rationale**: the roadmap poses this milestone as the test of whether 001's source seam was drawn in
the right place, and the honest answer is that it was — but not because Trello fits through it.
Trello and GitHub are not peers here. GitHub is where *dispatchable work* is read from; Trello is
where *intake* is read from, and its output is a GitHub issue. No caller ever holds one and could
just as well hold the other, which is the actual test for whether a shared protocol earns its
keep. A common `poll() -> [SourceItem]` could be written — both sides have an id, a title, a body,
and labels — but the two implementations would never be used polymorphically, and Principle I names
that case directly: a strategy interface with one caller and no second use in hand.

This mirrors the judgement `contracts/boundaries.md` already made for kitty and dtach: "Kitty and
dtach are *not* peers... Modelling them as interchangeable would force a lowest-common-denominator
interface." The same reasoning applies, and reaching the same conclusion twice from different
directions is mild evidence the rule is right.

**What *was* over-fitted, and is being fixed**: `IssueSourceWriter` has exactly one method,
`comment`, because commenting was the only write milestone 001 needed. Creating an issue is a second
write to the same system, so `create_issue` is added to that protocol — to the existing seam, not to
a new one, and with a simulated counterpart.

**Alternatives considered**: a generic `WorkItemSource` (rejected above); reaching Trello directly
from a service module with no boundary (rejected — every board write would then have to remember its
own effect-level check, which is precisely what FR-053 and `effects.py` exist to prevent).

---

## R2 — `httpx`, spoken directly, as with GitHub

**Decision**: talk to the Trello REST API over the `httpx` client the project already depends on. No
Trello SDK.

**Rationale**: `py-trello` and its peers are thin wrappers that would add a dependency, hide response
headers (the same objection research R4 in milestone 001 raised for GitHub), and pull in their own
transitive tree for what amounts to a dozen URL templates. The work removed is close to zero.
Principle I's test — "justified in the feature plan by the work they remove" — is not met.

**Alternatives considered**: `py-trello` (unmaintained relative to the API, wraps everything in
objects that fetch lazily, which is the opposite of what a bounded-timeout policy needs); `requests`
(a second HTTP client in one process for no reason).

---

## R3 — Credentials go in the `Authorization` header, never the query string

**Decision**: authenticate with `Authorization: OAuth oauth_consumer_key="<key>",
oauth_token="<token>"`.

**Rationale**: this is the single most dangerous difference between the two APIs. Trello's documented
and most common form is `?key=...&token=...` in the **query string**, and this project logs request
targets. A URL carrying credentials would put both secrets into the audit log, into any error
message that echoes the URL, and — once milestone 002's audit view renders it — onto a web page.
`audit.py`'s redaction is keyed on *field names*, so a secret embedded inside a URL string under a
key called `url` would sail straight through it. The header form removes the hazard at the source
rather than relying on a redaction rule to catch it.

Belt and braces: the boundary logs method and path only, never a full URL with a query string, and a
unit test asserts that neither the key nor the token appears anywhere in a log line produced by a
failing request.

**Alternatives considered**: query-string auth with a URL scrubber (rejected — a scrubber is a rule
someone has to remember to apply, and the failure is silent and permanent); extending `audit.py` to
pattern-match secrets in values (rejected — value-matching redaction produces false positives on
issue bodies and would still be a second line of defence for a problem with a first-line fix).

---

## R4 — Reads always real, every board write live-only

**Decision**: two new rows in `effects.REAL_AT` — `card_reader: every level`, `card_writer: {live}`.
`issue_writer` already reads `{live}`, and `create_issue` inherits that.

**Rationale**: FR-038 and FR-039 restate the existing rule; the point of putting it in the table is
that no calling code gets to decide. At `plan`, `local`, and `no-remote` a full board cycle reads
real cards, evaluates them for real, and writes nothing to either system, while the log records
every write that would have been made with its full arguments.

`SimulatedIssueWriter.create_issue` must return a **structurally valid** `Issue` — a plausible
number, a plausible URL — for the reason `contracts/boundaries.md` gives: returning `None` or raising
would let the simulated path diverge from the real one at exactly the point the requirement exists to
prevent. The fake number is drawn from a fixed high offset so it is recognisable in a log, and the
row it produces is `dry_run`, which is what keeps it out of listings and out of the live mapping.

---

## R5 — Cards live in their own table; `needs_info` is a card state

**Decision**: a `cards` table, keyed `(board_id, card_id, dry_run)`, carrying the card's own
lifecycle. Work items are untouched.

**Rationale**: this reverses the planning document's §7, which lists `needs_info` among the *work
item* states, and it is the largest single decision in this milestone. The reason is concrete rather
than aesthetic. `work_items.repo_key` is `NOT NULL REFERENCES repos(repo_key)` and
`work_items.issue_number` is `NOT NULL`. A card awaiting clarification has neither: the whole
definition of `needs_info` is that no repository could be identified, and the repository it
eventually names may not even be onboarded. There is no honest value to put in either column, and
SQLite cannot drop a `NOT NULL` in place — accommodating the row would mean rebuilding the central
table to weaken an invariant that every other row depends on.

The design that falls out is better than the one it replaces. Board ingestion cannot create a
dispatchable row *at all*, which turns the human gate from a rule the code follows into a shape the
schema enforces. And the card→issue mapping the §11 invariant needs has to outlive the work item
anyway: a card's issue may sit unlabelled for weeks with no work item in existence, and may still
have none after onboarding is refused.

**Alternatives considered**: making `repo_key` and `issue_number` nullable (rejected above); a
sentinel `repos` row for unresolved cards (rejected — a fake foreign key to make a state machine fit
is how a schema starts lying); storing cards as JSON in a config-adjacent file (rejected — Principle
IV wants the mapping written atomically alongside the data it governs, which is what the database
already provides).

---

## R6 — The creation sequence, and where it can be killed

**Decision**: four steps, with the intent row written first and every step separately resumable.

1. `INSERT` a `cards` row in state `creating`, carrying the resolved `repo_key` and the wall-clock
   time of the attempt. Committed before anything leaves the process.
2. `create_issue` at the GitHub boundary. The issue body carries the card's URL, which doubles as the
   marker searched in step 4's recovery.
3. `UPDATE` the row with the issue number and URL, state `linked`. This is the mapping.
4. `comment` on the card with the issue URL, then record `comment_posted_at`.

**Rationale**: the dangerous window is between 2 and 3 — the issue exists and nothing local knows it.
Recovery for a row found in `creating` at startup or on a later pass is: list issues in the target
repository created since the intent timestamp, authored by us, and look for the card's URL in the
body. If one is found, adopt it and advance to `linked`; if not, the create never happened and step 2
is retried.

**Why listing and not searching**: GitHub's search index is eventually consistent, by minutes in the
worst case. An issue created two seconds before the crash may not be findable by search, which would
produce exactly the duplicate the whole mechanism exists to prevent. `GET /repos/{owner}/{repo}/issues`
with `since` is immediately consistent and bounded by the intent timestamp.

A crash between 3 and 4 leaves a mapping with no card comment; the next pass sees
`comment_posted_at IS NULL` and posts it, checking first for an existing marker comment so a retry
cannot double-post.

**The residual gap, stated rather than papered over**: a crash between steps 2 and 3 *combined with*
loss of the database leaves an issue that nothing can find — no mapping, no intent row, and no card
comment. The next poll creates a second issue. This is a double failure, it is recoverable by hand
(the stray issue is visible in the repository and unlabelled, so it dispatches nothing), and closing
it would mean scanning every configured repository's recent issues before every creation. Recorded
here as the known limit rather than pretended away.

---

## R7 — Duplicate suppression is ordered, and the order matters

**Decision**: before creating anything, consult in this order — (1) the `cards` mapping row; (2) if
absent, the card's own comments for our marker; (3) for a row in `creating`, R6's issue listing.

**Rationale**: §11 is explicit that the marker comment is "a **recovery marker** for rebuilding state
after DB loss — not the primary key. Don't parse comments as the authoritative source in normal
operation." Ordering satisfies both halves: with a mapping present, nothing reads the board's
comments and the extra API call never happens; with the mapping gone, the marker restores it on the
next poll, one card at a time, with no bulk rebuild command to write or to keep working.

The marker is a fixed prefix followed by the issue URL, matched by prefix rather than parsed. It is
written by us and read only by us, so its format is ours to keep stable.

---

## R8 — Repository resolution accepts only references that already resolve

**Decision**: scan the card's title and description for three forms — a `github.com/<owner>/<name>`
URL, a bare `<owner>/<name>`, and a filesystem path — and keep only those that map to a key in
`config.repos`. Resolvable means the surviving set has exactly one member.

**Rationale**: the naive version of this is a security-adjacent bug. A bare `owner/name` pattern
matches `src/robot_army`, `docs/roadmap.md`, and any two-segment path in a pasted log — and a card
description is, by the planning document's own framing, semi-untrusted text that may be pasted from
a log. Filing an issue in a repository named by a stray path fragment is the failure mode worth
engineering against, and filtering the candidate set against configured repositories eliminates it
structurally: an unknown reference cannot select anything, so the worst case is `needs_info`, which
is the safe direction.

The same filter makes the rejection message specific and actionable, which FR-012 requires: "names
`someone/other`, which has no `[repos.*]` section" tells the author exactly what to fix.

**Two references to the same repository are one reference.** The set is deduplicated by resolved
repository key before it is counted, so a card that pastes a URL and also names the local path is
resolvable, not ambiguous.

**Alternatives considered**: asking a model to extract the repository (rejected — it puts a model
between the author's words and the issue, is unpredictable, needs network, and cannot be unit
tested); accepting any `owner/name` and validating against GitHub (rejected — turns every ambiguous
card into API calls and can still pick a real repository the author never meant).

---

## R9 — Our own writes must not look like the author editing the card

**Decision**: after any write to a card, re-read `dateLastActivity` from the response and store it as
the new baseline in the same transaction that records the write.

**Rationale**: this is a self-inflicted infinite loop waiting to happen. The rescan trigger is "the
card's last-activity timestamp changed" (FR-023), and *commenting on a card changes its
last-activity timestamp*. Without this rule, the `needs_info` comment posted in step one causes the
next poll to see an edit, rescan, find nothing new, and — with FR-022 suppressing the duplicate
comment — merely burn an evaluation every poll interval, for every unresolved card, forever. With a
future change that made the comment conditional differently, it would post again and become a true
loop.

Storing the post-write timestamp closes it, and a unit test asserts that a poll immediately following
one of our own writes triggers no re-evaluation.

---

## R10 — The board's privacy is checked, not assumed

**Decision**: at startup, `GET /1/boards/{id}?fields=name,prefs` and `GET /1/boards/{id}/members`.
Require `prefs.permissionLevel == "private"`; otherwise refuse to ingest, raise an anomaly, and leave
the rest of the daemon running. **Record** the member list; do not gate on it.

**Rationale**: the planning document states the assumption plainly — "the board is private and nobody
else can access it... there is no author check, so board access *is* authorization" — and adds
"Revisit if the board is ever shared." An assumption written in a document does not revisit itself.
Three cheap calls at startup turn it into a precondition, and sharing the board becomes a loud
failure instead of a silent widening of who can queue work onto the author's machine.

Refusing *ingestion only*, rather than refusing to start, is deliberate: an unrelated board
misconfiguration must not take down dispatch for issues the author wrote themselves.

**Requiring sole membership was considered and rejected — by the author, correcting this document.**
An earlier draft of R10 required the member list to contain only the authenticated member and refused
to ingest otherwise. That is not what the planning document asks for, and it substitutes the system's
judgement for the author's about who may see their own board. It is also disproportionate to what a
second member can actually do: put a card on the board, which becomes an *unlabelled* issue. The
human gate means only the author can turn that into a running session, so the exposure a second
member adds is unwanted issues — visible and reversible — not code execution. The private check
stays because a public board is a different thing entirely: it is not a person the author chose.

**Alternatives considered**: checking on every poll (rejected — extra calls a minute to detect a
change that happens approximately never; startup plus a documented restart is the right frequency); a
per-card author check (rejected for now — Trello's `idMemberCreator` is available and would be the
right tool if a shared board ever turns out to need one, but building it before that is speculative
generality, and the human gate already bounds the damage).

---

## R11 — Tag and list names are validated against the board at startup

**Decision**: fetch the board's labels and lists at startup; fail loudly if the configured tag or
either lifecycle list is missing.

**Rationale**: a renamed label produces zero matching cards, which is indistinguishable from an empty
board — the system would sit there looking healthy and doing nothing, which Principle III's "silent
failure is forbidden" is aimed squarely at. A missing list is worse: it is discovered halfway through
a lifecycle, after the issue exists.

Trello labels are objects with an id and a name, and cards carry label ids. Resolving the configured
name to an id once at startup also means the per-card filter is an id comparison rather than a string
match, which is both cheaper and immune to a label being renamed mid-run.

---

## R12 — A card is never moved from a list the author moved it to

**Decision**: record the list id we last placed the card in. Before any move, read the card's current
`idList`; if it differs, do not move — comment instead, saying what would have been done. Record a
`pending_move_to` before the move so an interrupted move is distinguishable from a human one.

**Rationale**: FR-030 in the spec, and the reason is that the board is the author's own working
surface. A system that silently drags a card back to where it thinks it belongs is fighting its user,
and the author will lose that argument slowly and annoyingly.

The `pending_move_to` field exists for a specific interruption: killed after the move landed but
before it was recorded, the next pass would see the card in a list we do not think we put it in and
conclude the author moved it. Recording the intent first makes "we moved it and did not finish
writing that down" distinguishable from "somebody else moved it".

---

## R13 — Board polling reuses `poll_state` with a synthetic key

**Decision**: store board poll bookkeeping in the existing `poll_state` table under the key
`trello:board:<board_id>`. No new table, no ETag.

**Rationale**: `poll_state` has no foreign key and no consumer outside `poll.py` and `db.py` — nothing
renders its rows as repositories — so a non-repository key is safe. Its columns are exactly what the
board poll needs: last polled, last status, consecutive failures, backoff.

The `etag` column stays `NULL`. Trello does not offer usable conditional requests on the endpoint we
need, so the ETag economy that makes a 60-second GitHub poll free does not exist here. That argues
for a longer default interval, not for a cleverer mechanism: the default board poll is **300
seconds**, against GitHub's 60. A card the author just wrote is not urgent — nothing dispatches from
it without a human labelling the issue afterwards anyway.

**Alternatives considered**: a `board_poll_state` table (rejected — a second table with identical
columns to satisfy a naming preference).

---

## R14 — The board poll is a job in the existing daemon

**Decision**: one more `Job` in `Daemon._build_jobs`, on its own interval, ordered after `poll` and
before `dispatch`.

**Rationale**: the daemon already runs independently scheduled jobs with per-job intervals; this is
the mechanism working as designed, not an extension of it. A separate process would need its own
lock, its own heartbeat, and its own answer to "what happens when only one of them is running" —
three new failure modes to gain nothing, and directly against Principle I.

Ordering after `poll` is a small nicety rather than a correctness requirement: an issue created by
the board job is picked up by the *next* GitHub poll regardless, and it cannot dispatch until the
author labels it, which will be much later than either.

One repository's failure already does not stop the rest (`poll_all`); the board job follows the same
rule — a board failure is recorded, backs off, and leaves GitHub polling untouched.

---

## R15 — Anomalies and the heartbeat carry board health

**Decision**: consecutive board poll failures raise an anomaly through the existing
`db.raise_anomaly` path after the same threshold GitHub uses; the heartbeat gains a board field.

**Rationale**: FR-009's requirement that "I could not ask" never be reported as "nothing found" is
already implemented for GitHub by raising `TransportError` out of the boundary and recording it. The
board boundary reuses `TransportError` verbatim rather than introducing a parallel exception, so
`poll.py`'s discipline extends without a second convention to remember.

The partial unique index on open anomalies already prevents a failing board from producing one row
per poll forever.

---

## R16 — Surfaces: one new listing, reusing what exists

**Decision**: `robot-army cards` lists tracked cards with state and reason; `robot-army rescan <card>`
forces a re-evaluation; a `/cards` view and a rescan button in the web interface; card links rendered
next to issue links wherever a work item is shown.

**Rationale**: FR-024, FR-026 and FR-049 between them require every capability to exist in the
terminal and the `needs_info` list to be visible in both interfaces. The rescan control is a forced
job request, which milestone 002 already built for `poll` and `reconcile` — the marker-file mechanism
in `control.py` takes another job name without modification, which is why this is a small change
rather than a new cross-process pathway.

The card link on a work item is a join, not a column: `cards` carries `repo_key` and `issue_number`,
and `work_items.source_id` is `<repo_key>#<number>`. Adding a `card_url` column to `work_items` would
denormalise a fact that is already derivable and create a second place for it to be wrong.
