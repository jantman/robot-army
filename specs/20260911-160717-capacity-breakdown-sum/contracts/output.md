# Contract: capacity breakdown output

Every shape below satisfies: the components shown sum to the total shown.

## `robot-army capacity` (terminal)

All four lines, always:

```text
capacity     : 6 of 7 sessions running
  ours       : 0
  others     : 2 (started outside this system)
  simulated  : 3 (rehearsal sessions; no process behind them)
  in flight  : 1 (dispatched, no registry entry yet — launching, or ended and not reconciled)
```

## `robot-army capacity --json` and `robot-army status --json`

`ours` and `others` unchanged; two keys added:

```json
{"total": 6, "ours": 0, "others": 2, "simulated": 3, "in_flight": 1}
```

## One-line forms

`status`'s capacity line (`CapacitySnapshot.describe()`), the web chrome pill, and a
`global_cap` hold detail name `simulated` and `in flight` only when non-zero:

```text
6/7 sessions, 0 ours, 2 other, 3 simulated, 1 in flight
6/7 sessions (0 ours, 2 other, 3 simulated, 1 in flight)
6 of 7 sessions running (0 ours, 2 other, 3 simulated, 1 in flight)
```

With both zero, each is byte-identical to its form before this change.

## `dispatch.at_capacity` audit record

`detail` gains `"simulated": <int>` and `"in_flight": <int>`.
