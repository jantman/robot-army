# Contract: Board Information and Preconditions

Two value types gain one field each, and the startup check gains one row per configured ignored
column. The `CardSourceReader` / `CardSourceWriter` protocols are **unchanged** — no method is added,
removed, or given a new parameter, and no new request is made.

## `BoardInfo.lists_by_id`

```
BoardInfo:
    board_id, name, permission_level: str, member_ids: [str],
    labels:       {name -> id}
    lists:        {name -> id}
    lists_by_id:  {id -> name}        # new
```

Built in `TrelloCardReader.board_info()` from the same `GET /boards/{id}/lists` response `lists` is
built from, in the same pass. **No additional request**, and the memo that makes `board_info()`
once-per-process is untouched.

**Why the inverse exists.** `lists` is keyed by name, so two board columns called "Icebox" collapse
into one entry and one of them silently stays intake — which is FR-019b's failure. List ids are
unique, so `id → name` keeps both, and the requirement is satisfied by the shape of the data rather
than by a rule the resolution code has to remember.

**`lists` is unchanged and keeps every caller.** The existence checks are name-membership questions
and `name in info.lists` answers them correctly regardless of duplicates.

**Known limit, recorded rather than fixed**: `in_progress_list` and `done_list` resolve through
`lists`, so against two same-named columns they already pick an arbitrary one. That is milestone
003's behaviour, it is not made worse here, and fixing it means deciding what moving a card to an
ambiguous destination should mean — a different question with a different blast radius.

The simulated reader and every test fixture that builds a `BoardInfo` must populate `lists_by_id`
consistently with `lists`, or a test will pass against a board shape that cannot exist.

## `BoardStatus.ignored_list_ids`

```
BoardStatus:
    checks: (BoardCheck, ...)
    info: BoardInfo | None
    label_id, in_progress_list_id, done_list_id: str | None
    ignored_list_ids: frozenset[str]      # new, defaults to empty
```

Resolved once per process in `check_board`, beside the three ids it already resolves:

```
ignored_list_ids = frozenset(
    list_id for list_id, name in info.lists_by_id.items() if name in trello.ignore_lists
)
```

Ids, not names, for R11's reason applied unchanged: the per-card comparison becomes an equality check
that is cheap and that survives a column being renamed mid-run rather than half-matching.

Empty when nothing is configured, when `[trello]` is absent, and when `check_board` returns early on
an unreachable board — so every downstream comparison is false and the system behaves as milestone
003 does. That is FR-002 holding structurally rather than by a guard somebody has to write.

## The checks

`check_board` appends one `BoardCheck` per configured name, after the tag and lifecycle-column
checks, through the existing `_present()` helper:

| Check | `ok` when | Failure message |
|---|---|---|
| `ignored list exists` (once per configured name) | the name is a key of `info.lists` | names the missing column and lists the board's actual columns |

Order matters only for the report: the checks read in the order the author wrote them in the file,
which is why `ignore_lists` is a tuple rather than a set.

**Effect on `BoardStatus.ok`**: a failing check makes it false, exactly as a missing tag or lifecycle
column does. `poll_board` already gates on the whole verdict rather than on `label_id` alone, with
the recorded reasoning that "a caller that passed a failed status would otherwise ingest against a
board whose lists are missing". That gate now covers this failure with no modification.

**Blast radius**: ingestion only. Dispatch of issues the author wrote themselves is unaffected
(FR-018).

**Never a failure**: an *empty* `ignore_lists`. Zero checks are appended and the board section reports
exactly what it reported in milestone 003.

## The audit record

`trello.board.check` already serialises every `BoardCheck` into its detail, so the per-column checks
appear there with no code change. One field is added for the reconstruction standard, so the record
answers "which columns were being ignored?" without re-reading the configuration file:

```json
{
  "checks": [ ... , {"name": "ignored list exists", "ok": true, "detail": "'Icebox' found"} ],
  "ignored_lists": ["Icebox", "Blocked"],
  "ingestion": "enabled"
}
```

Written once per process and on every `doctor` run, which is the existing cadence.

## What does not change

- **`CardSourceReader`**: `board_info`, `poll`, `get_card`, `card_comments` — same signatures, same
  contract notes. In particular `poll` still returns **all** currently tagged, unarchived cards. It
  must **not** filter ignored ones: `_reconcile_board_contents` drops every tracked card absent from
  that listing and `dropped` is terminal, so filtering here would make parking a tracked card destroy
  it permanently and silently. Policy does not live in the transport — the same rule that keeps
  `move` from deciding whether moving is allowed.
- **`CardSourceWriter`**: untouched. This feature performs no write; it prevents them.
- **`effects.wire()`**: no new selection, no new row in the effect table. Nothing here is an effect —
  reads are already real at every level, and the feature adds no write to simulate.
- **`IssueSourceWriter`**: untouched.
