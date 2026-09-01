# Quickstart: Pushover Notifications

How to prove this works, end to end, without spending a real notification until you mean to. Every step
below is runnable against a real checkout.

## Prerequisites

1. A Pushover account, and one registered application at <https://pushover.net/apps/build> — the
   application gives you the **API token**, your dashboard shows your **user key**.
2. The Pushover app installed and logged in on a device.

## Setup

```bash
install -m 0600 /dev/null ~/.config/robot-army/pushover-token
install -m 0600 /dev/null ~/.config/robot-army/pushover-user
printf '%s' 'YOUR_APP_API_TOKEN' > ~/.config/robot-army/pushover-token
printf '%s' 'YOUR_USER_KEY'      > ~/.config/robot-army/pushover-user
```

`printf` rather than `echo`: no trailing newline. (The channel strips whitespace anyway — this is belt and
braces, and it makes the file's contents exactly the credential.)

Then in `~/.config/robot-army/config.toml`:

```toml
[pushover]
token_file    = "~/.config/robot-army/pushover-token"
user_key_file = "~/.config/robot-army/pushover-user"

[notifications]
events = ["failure", "needs_info"]   # dispatch | completion | failure | needs_info
max_per_cycle = 5
```

Note there is no `[health] webhook_url` here. That is the point of scenario 1: Pushover alone is a
complete channel.

## Scenario 1 — The configuration is accepted, and the credentials stay out of the config

```bash
uv run robot-army doctor
```

**Expect**: `config` reports loaded, with no problems. No warning about events having nowhere to go.

Then prove the credentials never entered the loaded configuration:

```bash
uv run python -c "
from robot_army.config import load
c = load()
assert c.pushover is not None
assert 'YOUR' not in repr(c), 'a credential reached the Config object'
print(c.pushover.token_file, c.pushover.user_key_file)
"
```

**Expect**: the two paths, and nothing else. Covers FR-003, US3 AS1.

## Scenario 2 — Each misconfiguration is refused, by name

Run each and read the message. Every one should fail at load, before anything is sent.

```bash
# (a) only one key set
# (b) a path that does not exist
# (c) chmod 0644 ~/.config/robot-army/pushover-token
# (d) token_file = "aBcDeFgHiJkLmNoPqRsTuVwXyZ1234"   <- a literal credential
uv run robot-army doctor
```

**Expect**, respectively: an error naming the missing key; an error naming the key and the path; an error
naming the key, the path, and the found mode; and an error saying the credential must come from a
mode-0600 file. Covers FR-004..FR-006, SC-005, US3 AS2-AS4.

Restore the good configuration before continuing.

## Scenario 3 — Nothing is sent below `live`

```bash
uv run robot-army run --effect-level local --once

jq -r 'select(.action == "notify.send" and .simulated == true)
       | {kind: .detail.kind, channels: .detail.channels}' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

**Expect**: the events that would have fired, each naming `["pushover"]` as the configured channel, and
**no** `notify.channel` records at all — nothing was delivered because nothing was sent. Covers FR-012,
US1 AS3.

Confirm your phone stayed quiet.

## Scenario 4 — A real notification arrives

Set `[daemon] effect_level = "live"` (or run at the default) and let a real event fire — the quickest is
to let an item reach `failed`.

```bash
uv run robot-army run --once

jq -r 'select(.action == "notify.channel")
       | "\(.detail.channel) \(.outcome) \(.detail.kind)"' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

**Expect**: `pushover ok failure`, and the notification on your phone within one daemon tick. Covers
FR-001, FR-008, FR-011, SC-001, US1 AS1.

## Scenario 5 — Both channels at once, and one of them broken

Add a webhook, and point Pushover at nothing that answers:

```toml
[health]
webhook_url = "https://ntfy.sh/my-private-topic"
```

Emit one event. Then:

```bash
jq -r 'select(.action == "notify.channel" or .action == "notify.send")
       | "\(.action) \(.detail.channel // "-") \(.outcome)"' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl | tail -5
```

**Expect**: **one** `notify.send` for the message, and **two** `notify.channel` records — one per channel,
with independent outcomes. Now break one channel (a bad webhook URL, or revoke the Pushover token) and
repeat: the healthy channel still delivers, the broken one records an error, and the daemon's pass neither
fails nor slows. Covers FR-009, FR-010, SC-006, US2 AS1-AS2.

## Scenario 6 — The existing installation is untouched

With **only** `[health] webhook_url` set and no `[pushover]` section:

```bash
uv run pytest tests/unit/test_notifications.py tests/unit/test_health.py -q
```

**Expect**: green, with the milestone-004 assertions unchanged. This is the regression gate for FR-016 and
SC-003 — the webhook body must still be byte-for-byte what it was.

## Scenario 7 — The dead-man's switch reaches the phone

```bash
uv run robot-army health --notify           # while the daemon is stopped and the heartbeat is stale
echo "exit: $?"
```

**Expect**: exit 4, one output line per configured channel, the alert on your phone, and one
`health.notify` record per channel:

```bash
jq -r 'select(.action == "health.notify") | "\(.detail.channel) \(.outcome)"' \
  ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

Covers FR-018, SC-008, US4 AS1-AS2.

**Then the part that is easy to get wrong.** Set `[daemon] effect_level = "local"` and run it again. The
alert **must still be delivered**, on both channels. The health alert is a human-invoked dead-man's switch
and is deliberately outside the effect-level system — gating it would silently disable the alert for
anyone running their daemon below `live`. Covers US4 AS5, and see
[contracts/notifications.md](./contracts/notifications.md).

With no channel configured at all, the command exits 4 and reports that nothing was sent, without
erroring. Covers FR-019, US4 AS4.

## Scenario 8 — No credential is anywhere in the log

The one assertion worth running after everything else, across a session that included a Pushover
authentication failure:

```bash
grep -c "$(cat ~/.config/robot-army/pushover-token)" \
  ~/.local/state/robot-army/logs/audit-*.jsonl
grep -c "$(cat ~/.config/robot-army/pushover-user)" \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

**Expect**: `0` for every file, both credentials. Covers FR-007, SC-004.

An automated version of this belongs in `tests/unit/test_pushover.py` — asserted across a run that
includes a 4xx, because that is the case where a credential would otherwise ride along inside an error
string rather than in a field anyone chose to add.

## Full suite

```bash
uv run pytest -q
```

Implementation is not complete until this passes (Constitution, Development Workflow).
