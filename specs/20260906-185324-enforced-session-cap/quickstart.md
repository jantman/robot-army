# Quickstart: proving the reported cap is the enforced cap

## Prerequisites

```bash
uv sync
```

A configured `robot-army`, a daemon that can be started and stopped, and a web service on
`localhost:8420`. `robot-army status` will say what is running.

## The whole suite

```bash
uv run pytest
```

Must pass before this feature is complete. The tests that matter here walk every row of the
decision table in [contracts/enforced-cap.md](contracts/enforced-cap.md): no daemon, no
readable heartbeat, a stale one, one with no cap, one with a garbled cap, agreement, and both
directions of disagreement.

## 1. The issue's own reproduction — reader stale, daemon fresh

This is the failure in issue #30. `6/5` before the fix, `6/7` after it.

```bash
# with max_concurrent_sessions = 5 in the config file:
systemctl --user start robot-army-web.service     # or: robot-army serve
systemctl --user start robot-army.service

# now raise it, and restart the daemon only — the documented go-live procedure
sed -i 's/^max_concurrent_sessions = 5/max_concurrent_sessions = 7/' ~/.config/robot-army/config.toml
systemctl --user restart robot-army.service

curl -s localhost:8420/active | grep -oE '[0-9]+/[0-9]+ sessions'
# expect: the denominator is 7, not 5

curl -s localhost:8420/active | grep -o 'SESSION CAP MISMATCH[^<]*'
# expect: one line naming both 7 and 5, saying the daemon's is in force
```

The number the daemon is enforcing wins, and the page says why it differs from the file the
web process read at startup.

## 2. The other direction — daemon stale, reader fresh

The same defect, and the one a fresh terminal command hides best.

```bash
# with the daemon running at 7, edit the file and restart nothing
sed -i 's/^max_concurrent_sessions = 7/max_concurrent_sessions = 9/' ~/.config/robot-army/config.toml

robot-army capacity
# expect: "... of 7 sessions running" — NOT 9 — and a `cap :` line naming both
```

Before this change the command printed 9: a cap nothing was enforcing, from a process that
looked maximally trustworthy for having just read the file.

## 3. Both surfaces agree

Run them within a second of each other, in either of the two states above:

```bash
robot-army capacity | grep '^capacity'
curl -s localhost:8420/active | grep -oE '[0-9]+/[0-9]+ sessions'

# and as payloads — `capacity` itself has no --json flag; `status` is where that lives
robot-army status --json      | jq '.capacity | {total, global_cap, configured_cap}'
curl -s localhost:8420/active.json | jq '.capacity | {total, global_cap, configured_cap}'
```

The denominators match. Before this change they could not be relied on to.

## 4. Nothing is refused, and nothing dispatches differently

The cap is a report, not a guard.

```bash
# with a disagreement in force, from §1 or §2:
robot-army status                # the queue and its reasons render normally
```

On the web, with the notice showing: the controls on `/queue` and `/item/<n>` are still
offered and still act. Only an effect-level mismatch refuses; a cap disagreement never does.

## 5. The silent states

Each of these must show the configured cap and **no** notice:

```bash
systemctl --user stop robot-army.service
curl -s localhost:8420/active | grep -c 'SESSION CAP MISMATCH'      # expect: 0

# a daemon holding the lock with an unreadable heartbeat: the existing
# "EFFECT LEVEL UNKNOWN" banner is the one account of that state, and there is no second one
mv ~/.local/state/robot-army/heartbeat.json /tmp/hb.json
curl -s localhost:8420/active | grep -c 'SESSION CAP MISMATCH'      # expect: 0
mv /tmp/hb.json ~/.local/state/robot-army/heartbeat.json
```

## 6. Reading the cap out of the heartbeat

The property that makes all of the above possible — the file alone answers what is being
enforced, with no access to the configuration:

```bash
jq .max_concurrent_sessions ~/.local/state/robot-army/heartbeat.json
```
