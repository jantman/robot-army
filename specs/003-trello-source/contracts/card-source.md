# Contract: The Card Source Boundary

A sixth seam, parallel to `IssueSource` rather than a second implementation of it. R1 gives the
reasoning: GitHub is where dispatchable work is read from, Trello is where intake is read from, and
no caller ever holds one where it could just as well hold the other.

## Effect table

Two new rows, and one existing row that now carries a second method.

| Boundary | `plan` | `local` | `no-remote` | `live` |
|---|---|---|---|---|
| `CardSourceReader` | real | real | real | real |
| `CardSourceWriter` | simulated | simulated | simulated | real |
| `IssueSourceWriter` (`comment`, **`create_issue`**) | simulated | simulated | simulated | real |

Reads are real at every level for the reason FR-052 already gives: a dry run that fakes its reads
tells you nothing about eligibility, which is the main thing you want to check. As with
`IssueSourceReader` there is **no** `SimulatedCardReader` — no level selects one, so a bug that tries
to fake board reads fails to import rather than quietly returning fixtures.

## Value types

```
Card:
    card_id, board_id, url, title, body,
    label_ids: [str], list_id: str, last_activity: str,
    closed: bool          # Trello's word for archived

BoardInfo:
    board_id, name, permission_level: str, member_ids: [str],
    labels: {name -> id}, lists: {name -> id}
```

`last_activity` is carried as the string the API returned, not a parsed datetime: it is used only for
equality against the stored baseline (R9), and parsing it would invite a timezone bug into a
comparison that does not need one.

## `CardSourceReader`

```
CardSourceReader:
    board_info() -> BoardInfo
    poll(board_id, label_id) -> [Card]
    get_card(card_id) -> Card | None
    card_comments(card_id) -> [str]
```

**Contract notes**

- `board_info` is what R10 and R11 check at startup: privacy, membership, and the existence of the
  configured label and lifecycle lists. It is one call plus a members call, made once per process.
- `poll` returns **all** currently tagged, unarchived cards, not a delta. There is no usable
  conditional-request economy here (R13), so the interval is 300 seconds by default rather than
  GitHub's 60.
- `card_comments` returns comment bodies newest-first, and exists only for R7's recovery path. It is
  **not** called when a mapping row exists, which is §11's "don't parse comments as the authoritative
  source in normal operation" expressed as a call-site rule with a test behind it.
- Every call sets explicit connect and read timeouts and retries with bounded exponential backoff and
  jitter, honouring `Retry-After` on a `429`.
- A transport failure raises `TransportError` — the same exception the GitHub boundary raises, reused
  rather than paralleled, so `poll.py`'s discipline extends without a second convention. It **must
  not** be caught and turned into an empty card list.
- Credentials travel in the `Authorization` header, never the query string, and no log line ever
  carries a full URL with a query string (R3).

## `CardSourceWriter`

```
CardSourceWriter:
    comment(card_id, body) -> comment_url
    move(card_id, list_id) -> None
```

**Implementations**: `TrelloCardWriter`, `SimulatedCardWriter`.

**Contract notes**

- Both calls return the card's refreshed `last_activity` to their caller alongside their result, so
  the baseline can be updated in the same transaction that records the write (R9). A writer that
  performed the write and left the caller to re-read would reopen the loop this rule closes.
- `move` does **not** decide whether moving is allowed. The check against `placed_list_id` (R12)
  belongs to the caller, because it is policy about the author's intent rather than a property of the
  transport.
- The simulated writer emits an audit record naming the call and its full arguments and returns a
  structurally valid result, per the rule the existing boundaries contract sets out.

## `IssueSourceWriter.create_issue`

```
IssueSourceWriter:
    comment(repo_key, number, body) -> comment_url
    create_issue(repo_key, title, body) -> Issue      # new
```

This is the one place milestone 001's seam was genuinely over-fitted: `IssueSourceWriter` had exactly
one method because commenting was the only write that milestone needed. Creating an issue is a second
write to the same system, so it goes on the existing protocol.

**Contract notes**

- The returned `Issue` is the created issue as GitHub reported it, including its number and URL. The
  mapping is written from that response, not from a request that was assumed to have worked.
- `create_issue` **never** applies the dispatch label (FR-015). The label is the human gate, and the
  writer has no parameter that could carry it — the gate is absent from the interface rather than
  defended by a rule.
- The body always contains the card's URL. R6's recovery depends on it, and FR-014 requires it
  independently.
- `SimulatedIssueWriter.create_issue` returns a structurally valid `Issue` with a recognisable
  high-offset fake number and a well-formed URL. Returning `None` or raising would let the simulated
  path diverge from the real one at the point the requirement exists to prevent.

## Wiring

`effects.wire()` gains two selections and one table extension. As with the existing five, selection is
a literal table in one function — no registry, no plugin discovery, no configuration-driven
implementation choice. Code downstream of the wiring has no access to the effect level, which is what
keeps the guarantee structural.

The greps that already assert this (`if dry_run:` appears nowhere outside `effects.py`) cover the new
code without modification.
