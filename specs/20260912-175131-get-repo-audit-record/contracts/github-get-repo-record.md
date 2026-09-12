# Contract: the `github.get_repo` record

Every case the unit tests assert. "Records" counts `github.get_repo` records only; `github.retry`
and `github.request` are written exactly as before and are listed where they occur.

| Case | Records | `outcome` | `status` | Other detail | Also written | `get_repo` then |
|---|---|---|---|---|---|---|
| 200 | 1 | `ok` | 200 | `exists: true` | — | returns `RepoInfo(exists=True, …)` |
| 404 | 1 | `ok` | 404 | `exists: false` | — | returns `RepoInfo(exists=False)` |
| 401 (any non-retried ≥ 400) | 1 | `error` | 401 | `error_type: TransportError`, `error` | `github.request` | raises the same `TransportError` |
| 503 until retries run out | 1 | `error` | 503 | as above | `github.retry` × retries, `github.request` | raises |
| Connection error on every attempt | 1 | `error` | `null` | as above | `github.retry` × attempts | raises |
| 503 then 200 | 1 | `ok` | 200 | `exists: true` | `github.retry` × 1 | returns |

Every case: `method: "GET"`, `path: "/repos/{owner}/{name}"`, entity `repo:{key}`. The record
contains no token, no `?`, and no `https://`.

## As printed by `robot-army log`

```
… github.get_repo [ok] repo:jantman/demo  {"method": "GET", "path": "/repos/jantman/demo", "status": 200, "exists": true}
```

The line matches `grep 'github.*"/repos/'`, which is the 005 quickstart's scenario 9 check.
