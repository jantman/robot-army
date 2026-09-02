# Contract: Reading a project board

**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R4, R7, R8, R12

Two methods on `IssueSourceReader`, implemented by `GitHubReader`. No new boundary and no
new seam — R12 records why. Reads are real at every effect level (FR-052), so there is no
simulated implementation, no `REAL_AT` entry, and no `Boundaries` field.

## Surface

```python
def resolve_project(
    self, repo_key: str, *, project: str | None, column: str | None
) -> ProjectResolution: ...

def read_board(
    self, repo_key: str, *, project_id: str, column_name: str
) -> BoardSnapshot: ...
```

`resolve_project` answers *which board and which column govern this repository*, honouring
configured values over discovery. `read_board` answers *where does everything sit*, against an
already-resolved board. Both raise `TransportError` and never return an empty result to mean a
failure — the module's standing rule.

In steady state the two travel in **one** HTTP request: once a project id is known, the caller
passes it and the reader issues a single GraphQL document carrying both the discovery half and
the board half (R4). The split into two methods is about what the caller asks for, not about
how many requests are made.

## The document

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
          content { ... on Issue { number state repository { nameWithOwner } } }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name optionId }
          }
        }
      }
    }
  }
}
```

Before any project is known, the `node($pid)` half is omitted and the document is discovery
alone. `orderBy` is passed explicitly even though it matches the observed default, because the
default is undocumented (R1).

## `_graphql()` — the guarantee that makes everything else safe

```python
def _graphql(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
    """POST one GraphQL document. Raises rather than returning a hollow result."""
```

**This helper is not optional and must not be bypassed.** `_request` raises only on HTTP
`>= 400`, and every GraphQL failure that matters here arrives as **HTTP 200 with an `errors`
array** (R7). Reading `payload["data"]` directly would turn a missing scope into an empty
board, which is indistinguishable from a board with nothing in it — a silent, plausible-looking
wrong answer, and the single most important thing in this feature to test.

| Response | `_graphql` does |
|---|---|
| 200, no `errors`, `data` present | returns `data` |
| 200, `errors` present, `data` null or the requested path null | raises `TransportError` naming `errors[0].type` and its message |
| 200, `errors` present **and** `data` usable | records `github.project.partial` and raises — a partly-believed board is worse than no board |
| 401 with a REST-style `{"message": "Bad credentials"}` body and no `errors` array | `_request` already raises; the message is passed through |
| Retryable status | `_request`'s existing bounded backoff applies unchanged |

Rate-limit headers **are** returned on GraphQL responses and are read with the existing
`_int_header` helper, so the board read reports its remaining budget the way `github.poll`
already does.

## Pagination

`first` is capped at 100 by the server (`first: 101` returns `EXCESSIVE_PAGINATION`). Pages are
followed through `pageInfo.hasNextPage`/`endCursor` with a hard bound of 20 pages — 2,000 items,
far beyond any board this is for — and exceeding it raises rather than silently truncating,
because a truncated board is a wrong order rather than a partial one. `POSITION ASC` is a total
order over the whole connection, so rank assignment survives being assembled across pages.

## Filtering

Applied while building the snapshot, in this order:

1. `item.type != "ISSUE"` — drops draft issues and pull requests. `REDACTED` items (content
   the token cannot see) are dropped here too, which is what stops `content.number` from being
   read off a null.
2. `content` null — belt to braces behind the above.
3. `content.repository.nameWithOwner != repo_key` — a project may span repositories; only this
   one's items take part in its order (FR-011).
4. `fieldValueByName` null — the item has no Status set. Treated as *on the board, in no
   column*, which is parked: held, naming "no status", not silently dispatchable.
5. Column comparison is case- and space-insensitive against the resolved column name.

Closed issues are not filtered here. They cannot reach the queue anyway — an issue that closes
becomes `done` through `reconcile` — and filtering them in the boundary would make the snapshot
disagree with the board for no benefit.

## What it records

| Action | Outcome detail |
|---|---|
| `github.project.discover` | repo, candidate projects, chosen project and column, whether each was discovered or configured, or the reason none resolved |
| `github.project.read` | repo, project, column, items in the column, items parked elsewhere, pages fetched, `rate_limit_remaining` |
| `github.project.partial` | the GraphQL `errors` entries, when a response carried both data and errors |
| `poll.board` | one record per repository per pass: read, skipped for backoff, or failed with the reason |
| `poll.board.fallback` | the snapshot went stale — which repository, how old the snapshot is, and that its order remains in force |

`github.project.*` are `record` calls rather than `action` intent/outcome pairs: they are
reads, and the intent/outcome bracket exists for calls that change something outside the
process. `poll.board` follows `github.poll`'s existing convention exactly.
