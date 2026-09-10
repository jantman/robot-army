# Quickstart: validating that a stored ETag is bound to its request

All scenarios run offline under `uv run pytest`; the last is an optional live check.

## 1. The boundary sends the ETag only for the same request

`tests/unit/test_github.py`: a stored ETag with a matching request line is sent and a 304 is the
healthy result; with a line differing only in `labels`, no `If-None-Match` is sent and the
`github.poll` detail says `request_changed`; with no line, `request_unrecorded`.

## 2. A label change is discovered on the next poll

`tests/unit/test_poll.py`: poll through a real `GitHubReader` over `httpx.MockTransport` whose
handler honours `If-None-Match` for *any* stored ETag (reproducing what GitHub was observed to
do). Poll under label A, change the config to label B, poll again: the second request carries no
`If-None-Match` and the label-B issues become work items. A third poll is conditional again.

## 3. An upgraded row heals itself

A `poll_state` row with an ETag and `etag_request` NULL: the next poll is unconditional and the
row afterwards holds both the new ETag and its request.

## 4. A failure keeps the pair

A transport failure after a stored (ETag, request) pair leaves both columns as they were.

## 5. The migration

`tests/unit/test_migrations.py`: a v14 database with a populated `poll_state` row migrates to 15
with every value intact and `etag_request` NULL; a migration killed mid-015 leaves version 14 and
no column, and re-runs.

## 6. Optional, live

```bash
uv run robot-army poll
sqlite3 ~/.local/state/robot-army/state.db 'SELECT repo_key, etag_request FROM poll_state'
```

The first poll after the upgrade is `HTTP 200` for each repository and `etag_request` is filled
in; the second is `HTTP 304 (unchanged)`.
