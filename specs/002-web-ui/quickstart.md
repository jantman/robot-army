# Quickstart: Web UI & HTTP API

How to run the interface and how to prove it works. Every scenario below is runnable; each names the
requirement it validates so a failure points somewhere.

Prerequisites are 001's, unchanged: a config file, a token, kitty running with a control socket, and
at least one onboarded repository. `robot-army doctor` first, every time.

---

## Running it

```bash
cd ~/GIT/robot-army
uv sync
uv run pytest                       # the suite must pass

uv run robot-army serve             # loopback only, the shipped default
```

To reach it from the phone, name the machine's LAN address in the config:

```toml
[web]
bind = "192.168.1.20"   # or 0.0.0.0 for every interface
port = 8420
refresh_seconds = 10
```

```bash
uv run robot-army serve
# web interface on http://192.168.1.20:8420
# WARNING: this is not loopback. Anything that can reach this port has full control
#          of robot-army. There is no authentication by design (spec FR-003).
```

From outside the house, connect the VPN first and use the same address. Nothing is published, no
tunnel is configured, and no port is forwarded — that is deliberate.

Two processes, started by hand after graphical login, in either order:

```bash
uv run robot-army run &             # the daemon
uv run robot-army serve             # the interface
```

The interface works with the daemon stopped. That is the point of it being separate.

---

## Scenario 1 — The couch view (US1, FR-011 through FR-019)

With at least one session running and something queued:

```bash
curl -s localhost:8420/active.json | jq '.items[] | {id, repo_key, state, title}'
curl -s localhost:8420/queue.json  | jq '.counts'
```

**Expect**: active items with their session and elapsed time; the queue in dispatch order; blocked
items each carrying a specific reason. In a browser at 390 pixels wide, no horizontal scrolling and
no text needing zoom (SC-013).

**Effect level and liveness on every page**:

```bash
curl -s localhost:8420/active.json | jq '{effect_level, daemon, dispatch_paused, rendered_at}'
```

**Simulated rows are excluded by default** (FR-019). With `--effect-level plan` rows present:

```bash
curl -s localhost:8420/queue.json | jq '[.items[] | select(.simulated)] | length'   # 0
curl -s 'localhost:8420/queue.json?include_simulated=1' | jq '[.items[] | select(.simulated)] | length'
```

The second is non-zero and every such row is marked in the HTML.

---

## Scenario 2 — The daemon is down (FR-005, SC-010)

```bash
pkill -f 'robot-army run'
curl -s localhost:8420/interrupted.json | jq '.daemon'
```

**Expect**: `{"running": false, ...}`, the views still rendering their data, and the page saying
prominently that the daemon is not running. Then:

```bash
curl -s -X POST localhost:8420/poll -H 'Accept: application/json' | jq
```

**Expect**: this one succeeds and polls directly, because `poll` works without a daemon. But:

```bash
curl -s -X POST localhost:8420/item/1/resume -H 'Accept: application/json' | jq '.reason'
```

**Expect**: HTTP `503` and a reason naming that the daemon is not running — not a hang, not a
half-done dispatch, not a success that did nothing.

---

## Scenario 3 — Decide an interrupted item from the phone (US2, FR-013, FR-014)

Interrupt a session the way a reboot would:

```bash
kill -9 $(pgrep -f 'dtach -A .*robot-army' | head -1)   # or just reboot
uv run robot-army reconcile
curl -s localhost:8420/interrupted.json | jq '.items[] | {id, uncommitted_changes, commits_on_branch, issue_closed, open_pr, signals_age_seconds}'
```

**Expect**: all four signals present. Touch a file in the worktree and re-request: the local two
change immediately; the GitHub-derived two carry an age up to 60 seconds (R9).

Then resume it, through the confirmation the interface requires:

```bash
curl -s localhost:8420/item/42/confirm/resume | grep -o 'action="[^"]*"'
curl -s -i -X POST localhost:8420/item/42/resume | head -1     # HTTP/1.0 303 See Other
uv run robot-army show 42
```

**Expect**: `303` immediately — not a request that hangs for the length of a worktree preparation —
and the item in `dispatching`, then `active` once the session is confirmed.

---

## Scenario 4 — The view is stale when you tap (FR-027, SC-004)

The case a phone produces constantly: a page rendered minutes ago, acted on now.

```bash
# render the interrupted list, then change the item behind its back
uv run robot-army abandon 42
curl -s -X POST localhost:8420/item/42/resume -H 'Accept: application/json' | jq '{reason, code}'
```

**Expect**: HTTP `409`, `code: 3`, and a reason naming the item's actual state. Nothing happened.

The confirm page catches the same case before the tap:

```bash
curl -s localhost:8420/item/42/confirm/resume | grep -i 'no longer\|abandoned'
```

---

## Scenario 5 — Double tap (FR-028, SC-003)

```bash
for i in 1 2 3; do curl -s -X POST localhost:8420/item/43/resume -H 'Accept: application/json' & done; wait
sqlite3 ~/.local/state/robot-army/state.db \
  'SELECT COUNT(*) FROM sessions WHERE work_item_id = 43'
```

**Expect**: exactly one new session row. The losers report `409` with an illegal-transition reason.
The guard is `states.transition_work_item` under `BEGIN IMMEDIATE`, not anything in the web layer
(R7).

Reloading after a POST must also not re-post — every action answers `303`, so the browser's reload
re-issues a `GET`.

---

## Scenario 6 — Pause dispatch (US3, FR-033 through FR-037, SC-006, SC-007)

```bash
curl -s -X POST localhost:8420/dispatch/pause -H 'Accept: application/json' | jq
uv run robot-army status | grep -i pause
```

Label a fresh issue and wait out a full poll interval:

```bash
sleep 90
uv run robot-army status --state ready      # the item is here
sqlite3 ~/.local/state/robot-army/state.db 'SELECT COUNT(*) FROM sessions WHERE state IN ("starting","running")'
```

**Expect**: the item discovered, evaluated, and held in `ready`; no new session; polling and
reconciliation still running; the heartbeat carrying the pause:

```bash
jq '{effect_level, dispatch_paused, activity}' ~/.local/state/robot-army/heartbeat.json
```

**Durability** (SC-007) — restart the daemon and reboot if you want to be thorough:

```bash
pkill -f 'robot-army run'; uv run robot-army run &
uv run robot-army status | grep -i pause     # still paused
```

Then release it:

```bash
uv run robot-army unpause
sleep 10
uv run robot-army status --state active      # the held item dispatched
```

---

## Scenario 7 — Cancel exactly one session (FR-021, SC-008)

With two sessions running:

```bash
uv run robot-army status --state active --json | jq '[.items[].id]'
curl -s localhost:8420/item/44/confirm/cancel > /dev/null
curl -s -X POST localhost:8420/item/44/cancel -H 'Accept: application/json' | jq
uv run robot-army status --state active --json | jq '[.items[].id]'
```

**Expect**: item 44 `interrupted` with its worktree untouched, the other session still running and
still attachable.

---

## Scenario 8 — Force a poll that actually forces one (FR-023, R5)

```bash
curl -s -X POST localhost:8420/poll -H 'Accept: application/json' | jq
ls ~/.local/state/robot-army/requests/          # the marker, briefly
sleep 6
ls ~/.local/state/robot-army/requests/          # gone; the daemon consumed it
uv run robot-army log --since 1m | grep github.poll
```

**Expect**: the marker appears and is consumed within one tick, and the poll's result appears in the
audit log — which is where a forced job reports, since the response can only honestly say
"requested".

---

## Scenario 9 — Attach at the desk (US5, FR-025)

```bash
curl -s -X POST localhost:8420/item/45/attach -H 'Accept: application/json' | jq
```

**Expect**: a new kitty tab showing that session, fully repainted, session still running. Do it twice
— both viewers work. Then stop kitty and try again:

**Expect**: a visible refusal naming the missing terminal socket, and nothing about the session
changed.

---

## Scenario 10 — Effect-level disagreement (R4)

```bash
pkill -f 'robot-army run'
uv run robot-army run --effect-level plan &      # daemon simulating
uv run robot-army serve                          # config says live
curl -s -X POST localhost:8420/item/46/resume -H 'Accept: application/json' | jq '{reason, code}'
```

**Expect**: HTTP `409` naming both levels, the mismatch shown on every page, and no session launched.
Read views keep working throughout — this refuses actions, not inspection.

---

## Scenario 11 — Reconstruct what happened (US4, FR-042 through FR-044)

```bash
curl -s 'localhost:8420/log.json?item=42' | jq '.records[] | {ts, component, action, outcome}'
curl -s 'localhost:8420/log.json?since=1d&outcome=error' | jq '.records | length'
```

**Expect**: every action taken through the interface present with `"component": "web"`, as an
intent/outcome pair; GitHub repositories, issues, and PRs rendered as links in the HTML; a bounded
page with a cursor rather than the whole history.

**A truncated final line does not break it** (FR-044):

```bash
printf '{"ts":"2026-08-24T00:00:00Z","component":"web","act' >> \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
curl -s localhost:8420/log.json | jq '{count: (.records|length), skipped_lines}'
```

**Expect**: records still returned and `skipped_lines` at least 1.

---

## Scenario 12 — No secrets, no outside world (FR-020, SC-009, SC-012)

```bash
TOKEN=$(grep -o 'ghp_[A-Za-z0-9]*' ~/.config/robot-army/token 2>/dev/null || echo "$ROBOT_ARMY_GITHUB_TOKEN")
for path in / /active /queue /interrupted /anomalies /log /item/42; do
  curl -s "localhost:8420$path" | grep -c "$TOKEN"     # every one must be 0
done
```

**And nothing loads from outside**:

```bash
for path in / /active /queue /interrupted /log; do
  curl -s "localhost:8420$path" | grep -Eo '(src|href)="https?://[^"]*"' \
    | grep -v 'github\.com' || true                     # only issue and PR links may be external
done
```

**Expect**: no stylesheet, script, font, or image from any host. Pull the machine's network cable
and every view still renders — only the GitHub links themselves stop leading anywhere.

---

## Scenario 13 — A public bind address is refused (FR-004)

```bash
uv run robot-army serve --bind 8.8.8.8; echo "exit=$?"
```

**Expect**: exit `3`, a message naming the address as globally routable, and nothing listening.
Private ranges, the VPN range, loopback, and `0.0.0.0` all start — `0.0.0.0` with the warning.

---

## What "done" looks like

```bash
uv run robot-army doctor
uv run pytest
uv run ruff check src tests
```

Then the human round the constitution asks for, because CI cannot: a real phone, on the couch, with
the daemon running — see what is active, resume something interrupted, pause dispatch, and read back
in the audit log exactly what you just did.
