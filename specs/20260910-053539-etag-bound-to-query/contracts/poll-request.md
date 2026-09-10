# Contract: The Recorded Poll Request and the `github.poll` Detail

## The request line

Built by one helper in `boundaries/github.py`, used both to send the request and to record it:

```
/repos/{quote(owner, safe="")}/{quote(name, safe="")}/issues?{urlencode(sorted(params.items()))}
```

with `params` exactly the query sent. Today:

```
/repos/jantman/robot-army/issues?direction=desc&labels=robot-army&per_page=100&sort=updated&state=open
```

- Path and query only. **Never** a header, and so never the token.
- Parameters sorted by key, so the line does not depend on dict order.
- Relative to `[github] api_base`, which is not included: the ETag belongs to GitHub's resource,
  and `api_base` is the same host for every stored row.

## The comparison

```
send If-None-Match: <etag>   iff   etag is not None and etag_request == <line about to be sent>
```

## `github.poll` audit detail

Existing keys unchanged (`status`, `etag_hit`, `items`, `rate_limit_remaining`). Added:

| Key | When | Value |
|---|---|---|
| `etag_sent` | always | `true` if `If-None-Match` was sent |
| `etag_discarded` | always | `null`; or `"request_changed"` when a stored ETag existed with a different recorded request; or `"request_unrecorded"` when a stored ETag existed with none |
| `request` | only when `etag_discarded` is not `null` | the line sent |
| `etag_request` | only when `etag_discarded` is `"request_changed"` | the line the discarded ETag belonged to |

Example, the first poll after `[github] label` changed from `robot-army-verify` to `robot-army`:

```json
{"action":"github.poll","outcome":"ok","entity_type":"repo","entity_id":"jantman/robot-army",
 "detail":{"status":200,"etag_hit":false,"items":9,"rate_limit_remaining":4987,
  "etag_sent":false,"etag_discarded":"request_changed",
  "request":"/repos/jantman/robot-army/issues?direction=desc&labels=robot-army&per_page=100&sort=updated&state=open",
  "etag_request":"/repos/jantman/robot-army/issues?direction=desc&labels=robot-army-verify&per_page=100&sort=updated&state=open"}}
```
