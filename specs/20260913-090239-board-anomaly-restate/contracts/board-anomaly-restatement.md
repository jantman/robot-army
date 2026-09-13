# Contract: what one board failure does to the anomalies table

`intake.board_disabled_anomaly(conn, audit, config=…, status=…)` with `status.ok` false. Every
case also writes the existing `trello.board.check` error record, unchanged (FR-007).

| # | State before | Effect on `anomalies` | `anomaly.restated` records |
|---|---|---|---|
| C1 | No open `board_precondition` row for this board | One new row (as today) | 0 |
| C2 | Open row, stored detail equal to the new detail | Nothing written; `detail` and `detected_at` unchanged | 0 |
| C3 | Open row, different failed check names | `detail` replaced; `detected_at` = now; same id | 1, with previous and new `failed_checks` and `previous_detected_at` |
| C4 | Open row, same names, a check's detail text differs | As C3 | 1 |
| C5 | Open row whose stored detail does not parse, or lacks `failed_checks` | As C3 | 1, `previous_failed_checks: null`, `previous_detail_unreadable: true` |
| C6 | Only an acknowledged or resolved row for this board | That row untouched; one new row (as today) | 0 |
| C7 | An open row for a *different* board | That row untouched; C1–C5 apply to this board's own row | per this board |

After any case there is at most one open `board_precondition` row per board (FR-001).

## The two database functions

- `db.open_anomaly(conn, *, kind, entity_type, entity_id, dry_run=False) -> Anomaly | None`: the
  one row the partial unique index allows, or `None`.
- `db.restate_anomaly(conn, anomaly_id, detail) -> bool`: rewrites `detail` and `detected_at`
  where the row is still unacknowledged and unresolved. `False` when it is not (a lost race with
  `--acknowledge`), in which case the caller writes no record.
