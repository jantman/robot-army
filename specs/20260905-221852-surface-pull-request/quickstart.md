# Quickstart: validating the pull-request surface

Everything below runs against a temporary database and a fake boundary. Nothing here needs a
GitHub token, and nothing here touches the maintainer's real state.

## Prerequisites

```bash
uv sync
```

## 1. The suite

```bash
uv run pytest
```

The whole suite must pass — the constitution's Development Workflow makes that the completion
gate, not a target. The tests this feature adds live in:

| File | Covers |
|---|---|
| `tests/unit/test_pull_requests.py` | the boundary read (C1): both routes, de-duplication, state normalisation, `TransportError` on failure rather than an empty list |
| `tests/unit/test_reconcile_pull_requests.py` | the refresh (C2, C3): the candidate rules, the write, the no-change path, the failure path, and the interruption path |
| `tests/unit/test_web_pages.py` | the three rendering states on every surface (C4) |
| `tests/unit/test_migrations.py` | migration 013 on a populated pre-013 database |

## 2. The three states, on one page

The point of the feature is that these three render differently. Build a work item in each state
and load its page:

```bash
uv run pytest tests/unit/test_web_pages.py -k pull_request -v
```

| Column value | The page must say |
|---|---|
| `NULL` | `not checked` |
| `'[]'` | `none` |
| `'[{"number":144,"url":"https://github.com/o/r/pull/144","state":"merged"}]'` | `#144 (merged)`, linked |

A test that only checks the third is not testing this feature. See
[contracts/pull-request-discovery.md](./contracts/pull-request-discovery.md) C4 for the exact
rendering each surface owes.

## 3. No page may need GitHub

The claim SC-004 makes is directly checkable: render every view with a boundary whose every
method raises, and no page may fail or hang.

```bash
uv run pytest tests/unit/test_web_pages.py -k unreachable -v
```

## 4. The refresh, end to end, without a network

```bash
uv run pytest tests/unit/test_reconcile_pull_requests.py -v
```

The cases that matter most are the ones that are not the happy path:

- a failed lookup leaves both columns exactly as they were and writes
  `reconcile.pull_requests_check` at `error`;
- a pass in which nothing changed writes no `work_item.pull_requests` record but does advance
  `pull_requests_at`;
- a simulated item and a branchless item cause no boundary call at all;
- a `done` item whose stored pull request is still `open` **is** re-checked; one whose pull
  requests are all `merged`/`closed` is not.

## 5. Against real GitHub, once, by hand

The GraphQL document is the one part a fake cannot vouch for. Confirm it against a repository
you can read — this is exactly the query R1 was verified with:

```bash
gh api graphql -f query='
query {
  repository(owner:"jantman", name:"robot-army") {
    issue(number: 71) {
      closedByPullRequestsReferences(first:20, includeClosedPrs:true) {
        nodes { number url state }
      }
    }
    pullRequests(headRefName:"robot-army/issue-71-a-card-naming-a-repository-at-the-end",
                 first:20, states:[OPEN,CLOSED,MERGED]) {
      nodes { number url state }
    }
  }
}'
```

Expected: both halves name pull request 142 with `"state":"MERGED"`. That single answer proves
both routes resolve, that merged pull requests survive `includeClosedPrs`, and that
de-duplication has real work to do.

## 6. Looking at it

```bash
uv run robot-army web --bind 127.0.0.1:8931
```

Then `/active`, `/interrupted` and `/item/<id>`. The daemon must have run at least one
reconcile pass for anything but `not checked` to appear; `POST /reconcile` from the interface
forces one.
