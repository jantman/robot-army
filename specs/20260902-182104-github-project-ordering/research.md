# Phase 0 Research: GitHub Project Board Ordering

**Feature**: [spec.md](spec.md) | **Date**: 2026-09-02

Every finding marked **verified** was tested live against `https://api.github.com/graphql`
with the author's own token and the author's own boards, not read out of documentation.
GitHub documents almost none of the ordering semantics this feature depends on, so the
distinction between *verified* and *documented* is load-bearing and is kept throughout.

---

## R1 — Is board order retrievable at all?

**Decision**: Yes. `ProjectV2.items(orderBy: {field: POSITION, direction: ASC})` returns the
project's items in the author's manual order, and a board column's order is that one list
filtered to the column.

**Verified**:

- `ProjectV2ItemOrderField` has exactly **one** value, `POSITION`, described in the schema as
  *"Order project v2 items by the their position in the project"*. Note **in the project**.
- There is **no** `position` or `rank` field on `ProjectV2Item`. Order is obtainable as a
  *sequence* and never as an integer. Consequence: "did the order change?" cannot be answered
  cheaply — it requires re-reading the list and comparing.
- The default `items` order equals `POSITION ASC`, and `POSITION DESC` is its exact reverse.
  The default is undocumented, so the query passes `orderBy` explicitly.
- `ProjectV2View` **is** in the public schema (contrary to older community reports) but has
  **no `items` connection**. A view cannot be asked what it shows. This is the hard limit that
  shapes R2.
- Position is **one global list spanning every column**: moving an item to the top with
  `updateProjectV2ItemPosition` placed it above items in a different column. Changing an
  item's Status does **not** move it in that list.

**Rationale**: this is exactly the shape the feature needs. The issue asks for order *within
one repository's column*, which is the global list filtered twice — to the repository and to
the column.

**Alternatives considered**: the REST Projects (classic) API — rejected, it addresses a
different, deprecated product the author's boards are not. Deriving order from a custom
numeric field the author maintains by hand — rejected as inventing a workflow to avoid
reading one that already exists.

---

## R2 — Manual position, or what a *view* displays?

**Decision**: order by manual position (`POSITION`). The configuration gains **no** concept of
a view.

This was investigated because it looked like a genuine conflict. Project #3's view 1 — the URL
the issue links — carries `sortBy: [Priority ASC]`, and a view's sort overrides manual
position on screen. If the author sees a Priority-sorted column and robot-army dispatches in
manual order, the feature dispatches in an order the author cannot see, which is precisely the
silent wrongness Principle III forbids.

**It is not a conflict, and the measurement is why**:

| Ready column, `POSITION ASC` | #48 | #1 | #41 | #20 | #22 | #21 | #23 | #30 | #32 | #44 |
|---|---|---|---|---|---|---|---|---|---|---|
| `Priority` field value | — | — | — | — | — | — | — | — | — | — |

Every card in the column has `Priority` unset, so the view's sort is inert and manual position
shows through unchanged. The author independently confirmed the on-screen order is
`48, 1, 41, 20, 22, 21, …` — identical to what the API returns.

**Rationale**: replicating GitHub's view semantics means implementing sort for every field
type (single-select by option index, iteration by start date, number, date, text), multi-field
sorts with per-field direction, and the view's own `filter` string — a sort engine with one
caller, re-verified against GitHub forever. That is the speculative generality Principle I
names, built to solve a divergence that does not currently exist.

**But it is not ignored**, because it can start existing the moment a Priority value is set.
`doctor` grows a check that reads the project's board views and reports a view whose sort field
**has a value on at least one card in the dispatch column** — the precise condition under which
the screen and the dispatch order can disagree. A sort that changes nothing produces no
warning, so the check does not cry wolf on the author's board as it stands today.

**Alternatives considered**: honouring a single-select sort only (order by option index, manual
position as tiebreak) — a reasonable middle, rejected because it buys nothing today and would
have to be either extended or explained the first time a non-single-select sort appeared.
Refusing to order a project whose view carries any sort — rejected as refusing to work on a
board that works fine.

---

## R3 — Does a drag in the browser actually write to `POSITION`?

**Decision**: yes. Treated as established, not assumed.

GitHub documents this nowhere, and no staff answer exists in the community threads that ask
([#8063](https://github.com/orgs/community/discussions/8063), 0 replies since 2021). The
structural argument is strong — one position per item, `updateProjectV2ItemPosition` takes only
`(projectId, itemId, afterId)`, and `ProjectV2View` stores no per-view item order, so a drag
has nowhere else to persist — but a structural argument is not evidence.

**The evidence**: `POSITION` order on project #3 is not monotonic in the time each card was
added to the project. #48 was added at `21:05:21`, last of the ten, and sits **first**; the
remaining nine interleave two distinct add-timestamps. Insertion order cannot produce that
sequence. Something reordered them, and the only thing that reorders them is the author
dragging cards.

**Consequence for the design**: none, other than that the feature is worth building. Had this
failed, `POSITION` would have been insertion order wearing a misleading name and the feature
would have had to be redesigned around an explicit priority field.

---

## R4 — Which query, and what does it cost?

**Decision**: **one HTTP request per repository per poll**, carrying discovery and the board
read in a single GraphQL document.

```graphql
query($owner:String!, $name:String!, $pid:ID!) {
  repository(owner:$owner, name:$name) {
    projectsV2(first: 20) { nodes { id number title url } }
  }
  node(id: $pid) {
    ... on ProjectV2 {
      field(name: "Status") {
        ... on ProjectV2SingleSelectField { id name options { id name } }
      }
      items(first: 100, orderBy: {field: POSITION, direction: ASC}) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          type
          content {
            ... on Issue { number state repository { nameWithOwner } }
          }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
        }
      }
    }
  }
}
```

Before a project has ever been resolved for a repository, the `node($pid)` half is omitted and
the document is discovery alone. Afterwards the two travel together, so every poll
re-verifies the link at no extra request.

**Verified costs**: a 100-item page of exactly this shape costs **1 point** with
`nodeCount: 100`. The budget is 5,000 points/hour. At the default 60-second poll that is 60
points per hour per repository — around 1% of budget per repository, which is what makes it
affordable to read the board on every pass and therefore what makes SC-002's one-poll-interval
promise honest.

`fieldValueByName` is used rather than `fieldValues(first: n)` deliberately: it is a plain
field and adds nothing to node count, where `fieldValues(first: 8)` would multiply it by eight.

**Why all items rather than the column**: `items(query: 'status:"Ready"')` filters server-side
and preserves `POSITION` order — verified, and it is the tempting query. It is **not** used,
because the feature needs two facts and that query answers one. FR-012 requires knowing which
*other* column an eligible item is parked in, and a filtered read cannot distinguish "in
Backlog" from "not on the board at all". One unfiltered read answers both.

**Pagination**: `first` is capped at 100 (`first: 101` returns `EXCESSIVE_PAGINATION`).
`pageInfo.hasNextPage`/`endCursor` are followed with a hard page bound, and order must survive
being assembled across pages — it does, because `POSITION ASC` is a total order over the whole
connection, not per page.

---

## R5 — Conditional requests, and why the poll interval does not change

**Decision**: none available; the interval stays at the daemon's existing poll interval.

**Verified**: GraphQL responses carry **no `ETag`, no `Last-Modified`, no `Cache-Control`**.
The 304 economy that makes the REST issue poll free does not exist here, and every board read
costs a point.

The Trello reader faced this exact situation and answered it with a longer interval
(`poll_seconds = 300`). That answer is **not** copied, and the difference is worth stating: a
Trello board poll returns up to 1,000 cards with no filter and no order, while this is one
1-point request. Spending 60 points an hour to keep SC-002's promise — a reorder taking effect
within one minute — is a better trade than saving 48 of them and making the author wait five.

`ProjectV2.updatedAt` was considered as a cheap change-detector. Rejected: it is untested
whether a pure reorder bumps it, and a change-detector that misses reorders would defeat the
one thing the feature exists to notice.

---

## R6 — Discovery, and what "the project for this repository" means

**Decision**: `repository.projectsV2` is the discovery query; exactly one candidate resolves,
anything else is reported rather than guessed.

**Verified**: `Repository.projectsV2` is described as *"List of projects linked to this
repository"* and is a **filter, not a superset** — for `jantman/robot-army` it returned 1
project, where `user(login:"jantman").projectsV2` returned 4. Critically, the one it returned
is a **user-owned** project at `github.com/users/jantman/projects/3`, which answers the spec's
open assumption: a user-owned board *is* discoverable through the repository, provided it is
linked. GitHub links automatically when an issue from the repository is added.

**Column selection**: `field(name: "Status")` returns the single-select options **in board
column order** — verified as `Backlog → Ready → In progress → In review → Done`. The recognised
dispatch column names are `Ready`, `Todo`, and `To do`, matched case- and space-insensitively.
GitHub's Kanban template offers exactly one of them (`Ready`) and the simpler template offers
exactly one (`Todo`), so the common cases resolve without configuration and a board offering
both or neither is reported as ambiguous rather than guessed at (FR-018).

**Explicit configuration** accepts a project number or a board URL, from which the owner type
(`/users/` vs `/orgs/`) and number are parsed, so an *unlinked* project — which discovery
cannot see by construction — is still usable.

---

## R7 — Authentication, and the one finding that constrains the author

**Decision**: a **classic** personal access token with `read:project` is required. Fine-grained
tokens are documented as unsupported for this feature and `doctor` says so before the daemon
ever needs it.

**Verified / documented**: there is an **organization-level** Projects permission for
fine-grained tokens and **no repository-level and no account-level one**. A fine-grained token
therefore *cannot* read a user-owned project — which is exactly what
`github.com/users/jantman/projects/3` is. This is not a limitation of this design; it is a gap
in GitHub's permission model, unanswered in
[community #156512](https://github.com/orgs/community/discussions/156512).

**Error shapes, both of which the client must handle**:

| Condition | Shape |
|---|---|
| Missing permission | **HTTP 200** with `errors[].type == "FORBIDDEN"` |
| Missing OAuth scope | **HTTP 200** with `errors[].type == "INSUFFICIENT_SCOPES"` |
| Bad or expired token | **HTTP 401** with a REST-style `{"message": "Bad credentials"}` body and no `errors` array |

The first two are the reason R8 exists. `x-oauth-scopes` **is** returned on GraphQL responses
(verified) and lists the granted scopes for a classic token; it is empty for a fine-grained
token, which is how `doctor` tells the author which kind they are holding.

---

## R8 — A GraphQL error is not an HTTP error, and `_request` cannot see the difference

**Decision**: a `GitHubReader._graphql()` helper that inspects the payload and raises
`TransportError` itself.

This is the sharpest hazard in the feature. `_request` raises only on status `>= 400`, and
every GraphQL failure that matters here — a missing scope, a forbidden project, a malformed
field — arrives as **HTTP 200 with an `errors` array**. Calling `_request` directly and reading
`payload["data"]` would turn every one of those into a silently empty board, which would in
turn look exactly like "the project has no items" and quietly reorder nothing while reporting
success. That is a Principle III violation with a plausible disguise, and it is the single
thing most worth a test.

`_graphql()` therefore: posts to `/graphql` relative to `api_base`, raises `TransportError`
naming `errors[0].type` and message when `errors` is present and `data` is null or the
requested path is null, and returns `data` otherwise. Partial errors (`data` present *and*
`errors` present) are recorded and treated as failure for the affected repository rather than
partially believed.

**GHES is out of scope and fails loudly rather than quietly**: on GitHub Enterprise the REST
base is `.../api/v3` while GraphQL lives at `.../api/graphql`, so `/graphql` relative to
`api_base` is wrong there. The target is one machine talking to github.com (Operating
Constraints); a GHES install gets a 404 from `_request`, which raises, and `doctor` reports the
project check as failed. Nothing is silently mis-ordered.

---

## R9 — Where the order is stored, and why it must be stored at all

**Decision**: two nullable columns on `work_items` (`board_column`, `board_position`) and one
new `repo_projects` table.

`ordering.plan` is pure, runs on **every web page render**, and does no I/O beyond reading the
database. That is a structural invariant of the codebase, not a preference — the dispatcher and
the web queue agree on the order by being the same function. A board read inside `plan` would
put an HTTP request on the render path and break it. So the board is read at poll time and the
result is stored.

Four states must stay distinguishable, and collapsing any pair of them is a real bug:

| State | Representation |
|---|---|
| No board has ever been read for this repository | `repo_projects.last_read_at IS NULL` |
| Board read; item is not on it | `board_column IS NULL` with a non-null `last_read_at` |
| Board read; item is in the dispatch column at rank *n* | `board_column = 'Ready'`, `board_position = n` |
| Board read; item is parked in another column | `board_column = 'Backlog'`, `board_position IS NULL` |

`boundaries/__init__.py` records the lesson this follows — `FastForwardResult`'s *"four
outcomes, and they must stay four"*, and `commits_ahead`'s account of what happened when a
read folded "could not determine" into `0`. Applied here: **unknown position must never become
position 0**, or an unread board would silently promote every item to the head of its
repository's queue.

`repo_projects` is a new table rather than columns on `repos` because `repos` is an *approval*
record — migration 005's comment is emphatic that it stores what a human approved — and a
discovered, self-refreshing, failure-tracking resolution is a different lifecycle. It is also
not `poll_state`, which has fixed columns with nowhere to put a project id, a column name, or
how each was decided.

**Alternatives considered**: a `project_items` side table keyed by `(repo_key, issue_number)` —
rejected, `work_items` already has exactly one row per issue with a unique index enforcing it,
and a side table would be a join on the render path to hold two values. Recomputing rank from a
cached raw board snapshot at plan time — rejected as parsing on the render path to avoid two
columns.

---

## R10 — How the order is applied without disturbing the global order

**Decision**: sort as today, then permute within each project-ordered repository.

FR-002 is unusually precise about what must not change: the **positions** a repository's items
occupy under the configured global mode stay exactly as they are; only **which** of that
repository's items sits at each of those positions changes. That is not a new sort key — a key
that mixed board rank with `discovered_at` would interleave repositories differently and
silently change `repo-priority`'s meaning.

So `plan` keeps its existing sort verbatim, then, for each repository with a board:

1. collect the indices its items occupy in the sorted list;
2. sort that repository's items by `(0, board_position)` when ranked and
   `(1, discovered_at, id)` when not — which is FR-008's "off-board items sort after everything
   the board ranked", and is total, so a render cannot shuffle;
3. write them back into the same indices.

The permutation is a pure function of the list and is the whole of the ordering change. Under
`oldest-first` and under `repo-priority` alike, the repository's slots are untouched.

---

## R11 — The new hold reason, and where it sits

**Decision**: `HoldReason.OFF_COLUMN`, placed between `NOT_ONBOARDED` and
`PREPARATION_FAILED`.

`HoldReason`'s declaration order **is** the precedence and its docstring argues each rank, so a
new member has to earn its place rather than be appended.

- **Below `not_onboarded`**: a repository that no longer resolves to a clone is broken in a way
  that blocks all of its work. Telling the author to move a card when the clone is missing
  points at the wrong fix.
- **Above `preparation_failed`**: both are conditions of the item, but parking a card is a
  deliberate and more recent statement by the author than residue from an attempt they have
  since stepped back from. "You parked this" is the current truth; the stale failure is history.
- **Below the queue-wide reasons**, following the rule the enum already states: reasons that
  would hold an item on a completely empty machine come last. This accepts that a parked item
  reports "machine full" while the machine is full — consistent with how `not_onboarded`
  already behaves, and changing that rule for one new member would make the precedence less
  readable, not more.

**The consequence worth naming**: gating makes every labelled issue parked in Backlog a `ready`
work item carrying a hold, so a large backlog becomes a large held section in the queue. FR-030
already requires that state to be distinguishable from having no work, and it is met with a
count in the ready section's heading and a field in `status` — not by hiding the rows, which
would trade noise for invisibility.

---

## R12 — Boundary shape

**Decision**: two methods on `IssueSourceReader`, implemented by `GitHubReader`. No new
boundary, no new seam.

`boundaries/__init__.py` states the test: a second seam is justified when no caller ever holds
one implementation where it could just as well hold the other — which is why `CardSource` is a
sixth seam and not a second `IssueSource`. Project reading fails that test in the direction of
*not* being a new seam: it is the same host, the same token, the same client, and the same
`_request` machinery, answering a question about the very issues `poll` already returns. It is
not a different source of work; it is the ordering the existing source imposes.

Reads are real at **every** effect level (FR-052), so no `REAL_AT` entry, no `Boundaries`
field, no `SIMULATED_CONSEQUENCES` phrase, and no simulated implementation. That last point is
guarded: `test_effects.py` asserts `github.py` contains exactly one `Simulated*` name, and this
feature adds none.

**Cost**: `tests/conftest.py`'s `FakeIssueReader` gains both methods and their call recorders,
and `test_effects.py`'s protocol conformance check keeps `GitHubReader` honest.
