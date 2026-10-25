# Contract: keep on the web interface

## Routes

| Method | Path | Handler | `terminal=` |
|---|---|---|---|
| POST | `/item/<id>/keep` | `_item_keep_action(True)` | `keep` |
| POST | `/item/<id>/unkeep` | `_item_keep_action(False)` | `unkeep` |

They are shaped like `_item_hold_action` (`web/server.py:1165`):

- They go through `_perform`, so they pass the same-origin check and the intent record.
- `require_item` and the operation run **inside** the body, so a refusal is recorded.
- They are not effect-guarded and do not require the daemon.
- On success they redirect to the referring view, falling back to `/item/<id>`, with the
  message `kept` or `unkept`. Not `released`: that key is already the hold's banner, and
  sharing it would make a released keep read as a released hold.
- An FR-010 refusal renders the operation's message, including the resume line, with the
  terminal exit code, which is the existing `Refusal` path.

## Controls

A form button, `keep` or `release` (the second with class `primary`), labelled like the hold
control, rendered:

- on the item page, beside the action bar, for any item where `keep` would not be refused;
- on each `/active` row.

## `/active` — "kept" section

This section sits below the running table and is always present, with a stated empty state
(`Nothing is kept.`). It has one row per keep in force: item link, repository, issue, state,
session (the same three-way wording as `keeps`), kept since, by, and a release control.

The `/active` JSON gains `"keeps": [...]`, in the same row shape as `keeps --json`.

This is the only web surface that lists a session running under a `done` item, so it must
never filter out kept rows whose item is not `active`.

## Capacity pill

This needs no new element. The pill renders `capacity.breakdown`, which now carries
`(N kept)` (contract `capacity.md`).
