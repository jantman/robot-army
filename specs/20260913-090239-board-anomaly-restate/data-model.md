# Data Model: A Board Anomaly Always Names the Board's Current Failure

No table, column, index or migration changes.

## `anomalies` row, kind `board_precondition`

| Field | Before | After |
|---|---|---|
| identity | `(kind, entity_type='board', entity_id=<board id>, dry_run=0)` while open | unchanged |
| `detail` | written once, at the first failure, never again | rewritten whenever the board's current failure differs from it |
| `detected_at` | when the board first failed | when the **current** reason was first detected |
| `acknowledged_at`, `resolved_at` | unchanged | unchanged; a row with either set is never rewritten |

`detail` shape is unchanged:

```json
{
  "board_id": "<board id>",
  "failed_checks": [{"name": "tag exists", "detail": "'AI-task' is not a label on this board (has: AI-task-RENAMED)"}],
  "consequence": "board ingestion is disabled; polling and dispatch of issues you wrote yourself are unaffected"
}
```

### Transitions of one open row

```text
(no open row) --board fails--> open [detail = D1]
open [D1] --board fails with D1--> open [D1]          (no write, no record)
open [D1] --board fails with D2--> open [D2]          (detail, detected_at rewritten; anomaly.restated)
open [*]  --acknowledged-------->  closed             (unchanged; next failure raises a new row)
```

## Audit record `anomaly.restated`

```json
{
  "action": "anomaly.restated",
  "outcome": "ok",
  "entity_type": "anomaly",
  "entity_id": "<anomaly id>",
  "detail": {
    "kind": "board_precondition",
    "anomaly_entity_id": "<board id>",
    "previous_failed_checks": [{"name": "board is private", "detail": "…"}],
    "failed_checks": [{"name": "tag exists", "detail": "…"}],
    "previous_detected_at": "2026-08-31T14:02:11Z",
    "reason": "…"
  }
}
```

`previous_failed_checks` is `null`, and `previous_detail_unreadable` is `true`, when the stored
detail did not parse as an object carrying `failed_checks`.
