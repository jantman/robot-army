# Contract: audit records

## New actions

All of them are written through `ctx.audit.action(...)`, so each one produces an intent and
an outcome record, the same as `hold.item`. `entity_type` is `work_item` and `entity_id` is
the item id.

| Action | `detail` at intent | Added at outcome |
|---|---|---|
| `keep.item` | `by`, `was_kept`, `state`, `live_session` (bool) | `kept_at`, `kept_by`, `placed` (bool) |
| `unkeep.item` | `by` | `released` (bool), `was_kept_at`, `open_sessions` (list of session ids open at release) |

An FR-010 refusal is recorded as `keep.item` with `outcome: error` and a `reason`. It writes
no row.

From the web, `_perform` writes its own `web` component intent record first, as it does for
`hold.item`.

## Changed records

- **`anomaly.resolved`** gains a second reason for `orphan_session`: `"the work item is done
  and kept by the operator, so this session is accounted for"`. That record carries
  `work_item_id` and has no pid re-check (research R3).

## Unchanged and deliberately unwritten

- **A retirement withheld because of a keep writes nothing.** This is the existing "not yet"
  silence (C6) at `reconcile.py:1184-1189`, extended to one more reason. Whether a session
  is kept can be reconstructed from the `keep.item`/`unkeep.item` pair.
- **A cleanup pass that excludes a kept, live item writes nothing.** It used to write a
  `cleanup.considered`/`skipped` record every pass (research R5). `cleanup <id>` still writes
  one.
- **`session.retire` is unchanged.** A retirement that follows a release is identified by
  joining it to the preceding `unkeep.item`'s `open_sessions` (research R10).
