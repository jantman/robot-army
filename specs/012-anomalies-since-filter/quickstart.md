# Quickstart: Validating the `--since` Window on `anomalies`

How to prove this feature works end to end, by hand and by suite. Details of the argument
grammar live in [contracts/cli-anomalies.md](./contracts/cli-anomalies.md); the filtering rule
lives in [data-model.md](./data-model.md).

## Prerequisites

- The repository checked out on this feature's branch, with `uv` available.
- `uv sync` has been run at least once.
- No daemon needs to be running: `anomalies` is a read command.

## 1. The automated check

```bash
uv run pytest tests/unit/test_anomalies_since.py -v   # the new behaviour
uv run pytest                                          # the whole suite must pass
uv run ruff check .
```

The full-suite run is the load-bearing one for two reasons: the constitution's Development
Workflow requires it before the feature is complete, and SC-003 says the existing anomaly
expectations must pass **unmodified** — if the unfiltered output changed, something in
`tests/unit/` and `tests/integration/` will say so without anyone having to look.

## 2. By hand, against a scratch database

Point the state directory somewhere disposable so nothing touches real state:

```bash
export XDG_STATE_HOME=$(mktemp -d)   # robot-army puts its state under $XDG_STATE_HOME/robot-army
```

Seed three anomalies with known detection times — 10 minutes, 3 hours and 2 days old — using
the same schema the daemon writes. A short Python snippet against `robot_army.db` is the
intended way; see `tests/unit/test_anomalies_since.py` for the exact calls, which are the ones
worth copying rather than reproducing here.

Then walk the scenarios:

```bash
uv run robot-army anomalies                # all three — the reflex reading, unchanged
uv run robot-army anomalies --since 1h     # only the 10-minute-old one
uv run robot-army anomalies --since 1d     # the 10-minute and 3-hour ones
uv run robot-army anomalies --since 5m     # none — and the message must say "in this window"
uv run robot-army anomalies --since 1h --json | jq '.anomalies | length'   # 1
```

**Expected**: the sets above, newest first, with the kinds trailer on every run. Read the
`--since 5m` message carefully — it must not say there are no outstanding anomalies, because
there are three.

## 3. The rejection path

```bash
uv run robot-army anomalies --since "2 weeks"; echo "exit=$?"
uv run robot-army anomalies --since abc;       echo "exit=$?"
uv run robot-army log --since "2 weeks";       echo "exit=$?"
```

**Expected**: `exit=2` on all three, and the first two print the *same* explanation the third
prints. Comparing them against `log` is the point of the exercise — FR-002 is a claim about
sameness, so validate it by looking at both.

## 4. The invariant worth checking deliberately

```bash
uv run robot-army anomalies --since 1h --acknowledge <id-of-the-2-day-old-one>
```

**Expected**: the acknowledgement succeeds and is reported, even though its anomaly is outside
the window; the listing that follows is still filtered to the hour. Then:

```bash
uv run robot-army anomalies --since 1h --acknowledge 99999; echo "exit=$?"
```

**Expected**: `exit=1` with the existing "no unacknowledged anomaly with id 99999" message —
`--since` does not change what `--acknowledge` does.

And the ordering guard from research.md R5:

```bash
uv run robot-army anomalies --since bogus --acknowledge <a-real-open-id>; echo "exit=$?"
uv run robot-army anomalies --all | grep <that-id>
```

**Expected**: `exit=2`, and the anomaly is **still unacknowledged** — a command that exits with
a usage error must not have marked anything.

## 5. Clean up

```bash
rm -rf "$XDG_STATE_HOME"
unset XDG_STATE_HOME
```
