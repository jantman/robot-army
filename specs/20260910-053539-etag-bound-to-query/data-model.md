# Data Model: A Stored ETag Is Only Replayed Against the Request That Produced It

## `poll_state` (table) — one new column

| Column | Type | Meaning |
|---|---|---|
| `etag_request` | `TEXT`, nullable | The request line whose response supplied `etag` — see [contracts/poll-request.md](contracts/poll-request.md). `NULL` means *never recorded*: every row written before migration 015, and every Trello board row. |

Added by migration 015 (`ALTER TABLE poll_state ADD COLUMN etag_request TEXT`). No backfill
(research R4). Existing values in every other column are untouched.

**Invariant** (rows written after 015 by the GitHub poll): `etag`, when not `NULL`, was supplied
by a response to exactly `etag_request`. The two are written by the same statement and are only
ever meaningful together.

**Replay rule**: the stored `etag` is sent iff it is not `NULL` **and** `etag_request` is equal,
character for character, to the request about to be sent.

**Trello rows** (`trello:board:<id>`): `etag` and `etag_request` both stay `NULL`.

## `PollState` (dataclass, `models.py`)

Gains `etag_request: str | None = None`. The default keeps the Trello writers, which never set
it, correct without change.

## `PollResult` (dataclass, `boundaries/__init__.py`)

Gains `request: str` — the line this result answered. `etag` changes meaning slightly:

| Response | `etag` | `request` |
|---|---|---|
| 304 to a sent ETag | the sent ETag | the line sent |
| 200 with an `ETag` header | that header | the line sent |
| 200 with no `ETag` header | `None` (was: the old ETag carried forward) | the line sent |

## State transitions of a row's (etag, etag_request) pair

| Before | Event | After |
|---|---|---|
| (E, R) and current request = R | poll → 304 | (E, R) |
| (E, R) and current request = R | poll → 200 with E′ | (E′, R) |
| (E, R) and current request = R′ ≠ R | poll, ETag not sent → 200 with E′ | (E′, R′) |
| (E, NULL) — pre-015 | poll, ETag not sent → 200 with E′ | (E′, R′) |
| any (E, R) | transport failure | (E, R) unchanged |
| any | killed before save | unchanged; next poll repeats |
