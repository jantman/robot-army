# Contract: pull-request discovery

Normative. Where this and the prose in `plan.md` differ, this wins.

## C1 — The boundary read

`IssueSourceReader.pull_requests_for(repo_key, issue_number, branch) -> list[PullRequest]`

**replaces** `open_pr_for_branch`, which loses its only caller (R5). It is a read, so it is real
at every effect level, exactly as the rest of the protocol is.

| | |
|---|---|
| Transport | one `POST /graphql` through the existing `GitHubReader._graphql` |
| Arguments | `owner`, `name`, `number` (the issue), `branch` (the head ref) |
| Failure | raises `TransportError`. **Never** returns `[]` to mean "I could not ask" |

The document:

```graphql
query($owner: String!, $name: String!, $number: Int!, $branch: String!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) {
        nodes { number url state }
      }
    }
    pullRequests(headRefName: $branch, first: 20, states: [OPEN, CLOSED, MERGED]) {
      nodes { number url state }
    }
  }
}
```

`includeClosedPrs: true` and the explicit `states:` list are both required and both tested: with
either omitted, a merged pull request — the case the maintainer most wants to see — disappears
from the answer.

**Result construction**, in this order and no other:

1. Take `issue.closedByPullRequestsReferences.nodes`, then `pullRequests.nodes`.
2. Drop any node missing `number` or `url`.
3. Lower-case `state`. A value that is not one of `open`, `merged`, `closed` is kept verbatim
   and rendered as-is — inventing a state would be worse than showing GitHub's.
4. De-duplicate on `number`, keeping the first occurrence (FR-003).
5. Sort by `number` ascending.

Sorting is not cosmetic: it is what makes "did this change?" a string comparison in C3.

`issue` coming back `null` alongside a GraphQL `errors` array is a failure and `_graphql`
already raises it (R2). `issue` being `null` with **no** errors — which GitHub does not do
today — is treated as "no linked pull requests" rather than as a failure, and the branch half
of the answer still stands.

## C2 — When the refresh runs

| | |
|---|---|
| Caller | `reconcile.reconcile()`, once per pass |
| Position | **before** `_resolve_closed_issues` |
| Function | `reconcile._refresh_pull_requests(conn, boundaries=…, audit=…)` |
| Counter | `ReconcileResult.pull_request_changes`, in `summary()` |

The position is load-bearing (R4). The ordinary ending is *pull request merges → issue closes →
item goes `done`*; refreshing first means the pass that retires an item has already recorded its
pull request as `merged`, instead of freezing it at `open`.

**Candidates**, evaluated per work item. Every rule but the last means *skip, silently, with no
network call and no record*:

| # | Condition | Outcome |
|---|---|---|
| 1 | `item.dry_run` | SKIP — FR-006; a simulated row must cause no outward-facing effect |
| 2 | `item.branch` is falsey | SKIP — FR-007; nothing was dispatched, so nothing can exist |
| 3 | state is `active`, `awaiting_review` or `interrupted` | **REFRESH** |
| 4 | any stored pull request has `state == "open"` | **REFRESH** — FR-008's second clause |
| 5 | otherwise | SKIP |

Rule 4 is why an item whose issue was closed by hand while its pull request stayed open does not
freeze at `open` forever, and why the rule terminates: once every stored pull request is
`merged` or `closed`, nothing can change and the item is never asked about again. Items that
reached a terminal state before migration 013 are **not** backfilled; their `pull_requests`
stays `NULL` and reads as "not checked" (R4).

**No per-pass cache**, unlike `_resolve_closed_issues`. It could never hit:
`idx_work_items_identity` is unique on `(source, source_id, dry_run)` and `source_id` is
`repo#issue`, so two non-simulated items cannot share an issue, and simulated ones are
skipped by rule 1. One lookup per candidate, and a cache that cannot hit is a claim no test
can back.

## C3 — Writing what was learned

On success, build the new list per C1 and serialise it with
`json.dumps(…, separators=(",", ":"))`.

**If the serialised text equals the stored `pull_requests` text**, write it back with a fresh
`pull_requests_at` and record nothing. This is the omission Principle III's exception path
covers, enumerated in `plan.md` and identical to `_observe_speckit`'s (R8).

**If it differs**, in one `db.transaction`:

1. `audit.record("work_item.pull_requests", outcome="ok", entity_type="work_item",
   entity_id=item.id, target=f"{repo_key}#{issue_number}", dry_run=item.dry_run, detail=…)`
   where `detail` carries `{"from": [<number>:<state>, …], "to": [<number>:<state>, …]}`.
2. `db.record_pull_requests(conn, item.id, found=<text>, at=utcnow())`.

`db.record_pull_requests` rather than `db.update_work_item_columns`, and the reason is
`updated_at`. This runs every pass for every live item and almost every run confirms an
unchanged set, so the general updater would push `updated_at` forward once a minute for every
item in the system — turning a column that means "when this item last changed" into "when the
daemon last looked", and falsifying every age derived from it. The unchanged case takes the
same statement and writes the identical text back, so there is one path rather than two.

Record first, then write, both inside the transaction — the order `speckit.record_phase` uses.
The audit log is an append-only file rather than a table, so a rollback cannot unwrite the
record: the order is chosen so the failure that *can* happen is a record for a change that did
not land, which the next pass corrects by writing the same change again. The other order risks
the opposite — a committed change with no record — which Principle III does not tolerate.

On `TransportError`:

- `audit.error("reconcile.pull_requests_check", error=exc, entity_type="work_item",
  entity_id=item.id)` — mirroring `reconcile.issue_closed_check` (FR-020);
- **neither column is written** (FR-011). The stored answer stands and its age keeps growing,
  which is the truth.

No other exception is caught. A `TransportError` is the only failure this knows how to be honest
about; anything else is a bug and must reach the pass's own handler rather than be swallowed.

## C4 — What the interface renders

`operations.pull_request_view(item) -> dict` is the single place the three states are decided,
and every surface reads its output:

```python
{
  "pull_requests": [{"number": 144, "url": "https://…/pull/144", "state": "merged"}, …],
  "pull_requests_at": "2026-09-05T22:31:04Z" | None,
  "pull_requests_known": True | False,   # False iff the column is NULL
}
```

`pull_requests_known` is the field FR-016 turns on. `False` with an empty list means *never
looked up*; `True` with an empty list means *looked up, none exist*. No surface may infer the
difference from the list alone.

| Surface | Renders |
|---|---|
| `/item/<id>` | a `pull request` row in the existing `<dl>`, immediately after `branch`: every known pull request as `#144 (merged)` linked through `github_link`, separated by `<br>`; `none` when known and empty; `not checked` in the `empty` class when not known. When known, a `class="meta"` line gives `pull_requests_at` through `when()` |
| `/active` | a `PR` column after `spec-kit`: the **last** pull request by number as a link labelled `#144`, plus ` +N` when more are known; `—` when known and empty; `?` in the `empty` class when not known (FR-015) |
| `/interrupted` and the two states' detail blocks | the signals `<dl>`'s `open PR` row becomes `pull requests`, rendered as `/active`'s cell is, with a `class="meta"` footnote reading `pull requests confirmed <age>` or `pull requests never checked` |
| `/queue` | **nothing**. `ready` rows have no branch, so the column would be empty on every row of every render (SC-007) |

The "last by number" choice is FR-015's *one of them*: pull requests are numbered in the order
they were opened, so the highest number is the most recent attempt — the one that represents the
item's current outcome when a first was closed and a second opened.

Every URL goes through `pages.github_link`; one that fails the check renders as plain text and
never as an href (FR-018, R7).

`View.data` carries the same three keys under the same names on every view that renders them
(FR-017), so the JSON and the HTML cannot drift.

## C5 — No page performs a GitHub request for this

`operations.remote_resume_signals` **stops calling** `open_pr_for_branch` and stops returning
`open_pull_request` (R5). Its live `issue_closed` check is untouched: that is a resume-decision
input, not pull-request display.

`operations.pull_request_view` reads the work-item row and nothing else — no network, no cache,
no clock beyond formatting an age. This is SC-004, and it is testable directly: render every
view against a boundary whose every method raises, and no page may fail.
