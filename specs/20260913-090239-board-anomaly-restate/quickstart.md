# Quickstart: validating the board anomaly restatement

## Automated

```bash
uv run pytest tests/unit/test_board_preconditions.py tests/unit/test_anomaly_resolution.py -q
uv run pytest            # the whole suite (SC-004)
```

The unit tests cover every case in [the contract](contracts/board-anomaly-restatement.md), plus the
issue's sequence (SC-001) and five unchanged failures (SC-002).

## By hand, against a real board (the issue's scenario)

Prerequisite: a configured `[trello]` board and a daemon you can restart.

1. Make the board public and start the daemon. `robot-army anomalies` shows one
   `board_precondition` anomaly naming `board is private`.
2. Make the board private again. Do **not** acknowledge.
3. Rename the intake label on the board, then restart the daemon.
4. `robot-army anomalies` shows the **same anomaly id**, now naming `tag exists` with the renamed
   label in its detail, and not naming `board is private`. `robot-army anomalies --since 5m`
   includes it.
5. `robot-army log --since 10m --json | grep anomaly.restated` shows one record naming that id,
   with `previous_failed_checks` naming `board is private`.
6. Restart again without changing anything. No new anomaly and no new `anomaly.restated` record.
7. Restore the label name.
