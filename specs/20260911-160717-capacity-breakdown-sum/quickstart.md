# Quickstart: verifying the breakdown sums

## Automated

```bash
uv run pytest tests/unit/test_capacity.py tests/unit/test_capacity_reporting.py \
  tests/unit/test_ordering.py tests/integration/test_dispatch_capacity.py
uv run pytest            # the whole suite
```

The sum invariant is asserted directly in `test_capacity.py` for each population: registry
sessions of ours and of the author's, simulated rows, real rows with no registry entry, and the
degraded `/proc` path.

## By hand

At effect level `local`, dispatch a couple of issues (simulated sessions), then:

```bash
uv run robot-army capacity
```

Expect a `simulated` line equal to the number of simulated sessions still open, and
`ours + others + simulated + in flight` equal to the first line's running count.
`uv run robot-army status` shows the same terms on one line, and the web chrome pill likewise.
