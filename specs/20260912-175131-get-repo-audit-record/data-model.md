# Data Model: Onboarding's Repository Lookup Leaves an Audit Record

No table, column or state file changes.

## `github.get_repo` audit record

One per `GitHubReader.get_repo` call. Written after the result is known (research R1).

| Field | Value |
|---|---|
| `action` | `github.get_repo` |
| `kind` | `event` (a single record, not an intent/outcome pair) |
| `outcome` | `ok` for a 200 or a 404; `error` when the lookup raised |
| `entity_type` / `entity_id` | `repo` / the repository key as passed |
| `detail.method` | `GET` |
| `detail.path` | `/repos/{owner}/{name}`: path only |
| `detail.status` | the HTTP status, or `null` when no response arrived |
| `detail.exists` | `true` / `false`; success only |
| `detail.error_type` | the exception's class name; failure only |
| `detail.error` | its message, at most 400 characters; failure only |

## `TransportError.status`

`int | None`, default `None`. Set by `GitHubReader._request` when it raises for an HTTP response
(status ≥ 400, other than an allowed 404 or a 304). `None` for exhausted connection retries and
at every other raise site.
