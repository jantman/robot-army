# Research: Surface the pull request in the web UI

Every finding below was checked against the code or against the live GitHub API before it was
written down. Where a query was run, the query and its answer are recorded, because the whole
design rests on two of them.

## R1 — Both routes the issue asks for are answerable in **one** GraphQL request

**Decision**: one `POST /graphql` per work item per refresh, asking the repository for both the
issue's linked pull requests and the pull requests whose head branch is the item's branch.

**Evidence**. Run against `jantman/robot-army` on 2026-09-05:

```graphql
query {
  repository(owner:"jantman", name:"robot-army") {
    issue(number: 71) {
      closedByPullRequestsReferences(first:10, includeClosedPrs:true) {
        nodes { number url state isDraft }
      }
    }
    pullRequests(headRefName:"robot-army/issue-71-a-card-naming-a-repository-at-the-end",
                 first:10, states:[OPEN,CLOSED,MERGED]) {
      nodes { number url state isDraft }
    }
  }
}
```

answered

```json
{"repository":{
  "issue":{"closedByPullRequestsReferences":{"nodes":[
     {"number":142,"url":"https://github.com/jantman/robot-army/pull/142","state":"MERGED","isDraft":false}]}},
  "pullRequests":{"nodes":[
     {"number":142,"url":"https://github.com/jantman/robot-army/pull/142","state":"MERGED","isDraft":false}]}}}
```

Three things this settles:

1. `closedByPullRequestsReferences` is the field GitHub's own "linked pull requests" section is
   built on, and it exists on the v4 schema this project already talks to. `includeClosedPrs:
   true` is required — without it a merged pull request vanishes from the answer, which is
   precisely the case the maintainer most wants to see.
2. `PullRequest.state` is the enum `OPEN | CLOSED | MERGED`. **Merged is a state, not a
   separate boolean**, so FR-004's three outcomes come free and no second field is read.
3. Both routes returned the same pull request, which is the ordinary case and the reason FR-003
   demands a set rather than two lists.

**Alternatives rejected**:

- *REST, two calls.* `GET /repos/{o}/{r}/pulls?head=owner:branch` answers the branch route
  (this is what `open_pr_for_branch` does today) but there is **no REST endpoint for the
  issue's linked pull requests** — the closest is the preview timeline API, which returns
  cross-references, a strictly broader and noisier relationship. Two calls, one of them wrong.
- *Text-scanning issue and pull-request bodies for `Closes #N`.* Reimplements a relationship
  GitHub already computes, and gets it wrong for pull requests linked from the UI without a
  keyword. The spec's Assumptions section rules this out explicitly.
- *`timelineItems(itemTypes: [CROSS_REFERENCED_EVENT])`.* Returns every pull request that so
  much as mentions the issue, including ones from unrelated work. Broader than "linked".

## R2 — A missing issue answers with data **and** errors, and `_graphql` already fails that correctly

```json
{"data":{"repository":{"issue":null}},
 "errors":[{"type":"NOT_FOUND","message":"Could not resolve to an Issue with the number of 99999."}]}
```

`GitHubReader._graphql` treats a response carrying both as a failure and raises `TransportError`
— the right answer here (FR-011: a failed lookup retains what is stored and does not advance
the last-confirmed time).

**But it records the failure under the action `github.project.partial`**, hard-coded, because
board reading was its only caller. A pull-request lookup logged as a *project* failure is an
audit record that answers the wrong question, which Principle III's reconstruction standard
does not tolerate.

**Decision**: `_graphql` grows one keyword-only parameter naming the operation, defaulting to
the existing `github.project.partial` so board reads are untouched. Two callers now exist; this
is not speculative generality, it is a name that stopped being a constant.

## R3 — Storage: two columns on `work_items`, following `speckit_baseline`'s precedent exactly

**Decision**: migration 013 adds `pull_requests TEXT` (a JSON array) and `pull_requests_at
TEXT` (a UTC timestamp) to `work_items`.

The three-state distinction FR-016 demands falls straight out of the column, with no extra
flag, and the codebase already reads this shape:

| `pull_requests` | Means |
|---|---|
| `NULL` | never looked up — the state `speckit_baseline`'s docstring calls "never recorded", explicitly *not* the same as `[]` |
| `'[]'` | looked up, GitHub reports none |
| `'[{...}]'` | these are the pull requests, as of `pull_requests_at` |

A JSON array in a TEXT column rather than a `pull_requests` table: there is exactly one
consumer, no query ever filters or joins on a pull request, and the whole value is read and
written as a unit. `speckit_baseline` and `labels` are both stored this way for the same
reason. A table would add a migration, four functions, and a join for nothing.

**Interruption (FR-012)**: the write is a single `UPDATE` of both columns inside
`db.transaction`, so a kill mid-refresh leaves each row wholly before or wholly after. The
existing `speckit.record_phase` writes its audit record and its columns in one transaction for
exactly this reason, and this copies it.

## R4 — Which items get re-checked, and why the rule needs no configuration key

**Decision**: a refresh candidate is a non-simulated work item that has a branch, and either

- is in `active`, `awaiting_review` or `interrupted` — states where a pull request can still
  appear or change — **or**
- has a stored pull request still recorded as `open`, whatever the item's state.

The second clause is not decoration. `reconcile._resolve_closed_issues` moves an item to `done`
the moment its issue closes, and an issue can be closed by hand while its pull request is still
open. Without the clause that item's page would say `open` forever. With it, the item is
re-checked until every pull request it has is `merged` or `closed` — at which point nothing can
change and the item stops costing anything, permanently. The rule terminates itself, which is
why it needs no interval, no cap, and no key.

**Ordering inside the pass matters.** The refresh runs *before* `_resolve_closed_issues` in
`reconcile()`. The ordinary successful ending is: pull request merges → issue closes → item
goes `done`. Refreshing first means the pass that retires the item has already recorded the
pull request as `merged`, so the common case never even reaches the second clause.

**Not backfilled**: work items that reached a terminal state before this feature existed are
never looked up. Their `pull_requests` stays `NULL` and their pages read "not checked", which
is true. The alternative — one GraphQL call per historical item in a single pass — spends
real rate-limit budget re-litigating history, and FR-016's third state exists precisely so
that "we have not looked" can be said out loud instead of guessed at.

**Cost**: one GraphQL point per candidate per 60-second pass, against a 5000/hour budget. The
existing `_resolve_closed_issues` already makes one REST call per candidate over the same
three states, so this is the same order of magnitude as the pass already costs.

## R5 — The signals block must stop calling GitHub, and this is what makes SC-004 true

`operations.remote_resume_signals` currently calls `open_pr_for_branch` **while a page is being
rendered**, cached in-process for 60 seconds. SC-004 says rendering a page must perform no
GitHub request on behalf of pull-request display, so this call has to go.

**Decision**: `remote_resume_signals` keeps its live `issue_closed` check — that is a
resume-decision input, not pull-request display, and SC-004 does not reach it — and drops
`open_pull_request` entirely. `resume_signals` gains a third source, read from the work item's
own row with no network and no cache, contributing `pull_requests` and `pull_requests_at`.

Two things improve rather than degrade:

- **There is now one source of truth.** Before this, `/interrupted` could have shown a live
  `open PR: yes` beside a stored set that disagreed with it. Now every surface renders the same
  stored answer.
- **Freshness is unchanged.** The in-process cache's window is 60 seconds and the reconcile
  interval defaults to 60 seconds (`config.py:177`), so the stored value is no staler than the
  cache it replaces.

`open_pr_for_branch` has exactly one caller (`operations.py:1127`) and that caller is this one.
It is **replaced**, not kept alongside — a boundary method with no callers is dead weight, and
Principle V says nothing here is owed backward compatibility.

## R6 — Where the interface shows it, and the one place it deliberately does not

| Surface | Shows | Why |
|---|---|---|
| `/item/<id>` | every known pull request, in the existing `<dl>`, beside `branch` | FR-013; the question "what did this produce" is asked here |
| `/active` | a new `PR` column | FR-014; active items have branches and a pull request appears mid-session |
| `/interrupted` | the signals block's `open PR` row, now reading the stored set with its own age | FR-014, FR-019; this is where the resume/abandon decision is made |
| `/queue` | **nothing** | SC-007. `ready` rows have never been dispatched and so have no branch; the column would be empty on every row of every render, forever |

`/queue`'s `dispatching` table is a genuine edge — a retry out of `awaiting_review` can carry a
branch — but the state lasts seconds and the item is one click away. An always-nearly-empty
column on the busiest page is the worse trade.

## R7 — Links leave this machine through `github_link`, and nothing else

`pages.github_link` is documented as "the one place an href may leave this machine, and only to
`github.com`". A pull-request URL arrives from the GitHub API rather than from a user, but it
is stored in the database and rendered into HTML, so it goes through the same gate as every
other outbound link (FR-018). A URL that fails the check renders as plain text, exactly as an
audit record's does.

## R8 — What is logged, and the one omission this plan enumerates

Principle III requires the plan to name what goes unlogged.

| Event | Record |
|---|---|
| The stored pull-request set changes — first discovery, a new pull request, or a state change | `work_item.pull_requests`, written **inside the same transaction as the column write**, with the item, the numbers, and the before/after states (FR-021) |
| A lookup fails | `reconcile.pull_requests_check` at `error`, with the item and the error, mirroring the existing `reconcile.issue_closed_check` (FR-020) |
| A GraphQL response carrying both data and errors | `github.pull_requests.partial`, via R2's new parameter |
| The HTTP request itself | already covered: `GitHubReader._request` logs every retry and every `>= 400` individually; successful reads are covered by the existing aggregate-logging exception |

**Enumerated omission**: *a refresh pass in which an item's pull-request set did not change
writes no record.* With a 60-second cycle and sessions that run for hours, the alternative is a
log in which nearly every line says a pull request did not change. Every transition is still
recorded with its time, and `pull_requests_at` on the row carries the last confirmation. This
is the identical omission `reconcile._observe_speckit` documents, for the identical reason, and
the pass summary carries a count.

## R9 — What happens if it is killed halfway through

The refresh is a loop over items; each item's write is its own transaction. A kill leaves every
item processed so far updated and every item after it untouched — a legitimate resting state,
indistinguishable from a pass that has not run yet, and repaired by the next pass 60 seconds
later. Nothing is staged, nothing is two-phase, and no item can hold a half-written set,
because the set is one column written once.

## R10 — Dependencies

None. `httpx` is already the transport, `_graphql` is already the GraphQL client, SQLite is
already the store, and every rendering helper this needs (`table`, `a`, `span`, `github_link`,
`when`, `human_age`) already exists in `web/pages.py` and `web/html.py`.
