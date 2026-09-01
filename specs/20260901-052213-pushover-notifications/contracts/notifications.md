# Contract: Notifications — what this feature changes

An amendment to `specs/004-concurrency-polish/contracts/notifications.md`, not a replacement. Everything
that contract says still holds unless listed below.

## Unchanged

- **The four call sites**, each one line, each immediately after its transaction closes. They do not move.
  The R14 argument — an HTTP POST inside `BEGIN IMMEDIATE` holds a write transaction open for as long as a
  slow channel takes to answer — applies with more force to two channels, not less.
- **The four event kinds**: `dispatch`, `completion`, `failure`, `needs_info`. A closed set; an unknown
  kind is still refused at load.
- **Off by default.** `[notifications] events` is empty, so an unconfigured installation makes no outbound
  request at all (FR-033). Adding `[pushover]` without adding events sends nothing.
- **`REAL_AT["notifier"] = {LIVE}`.** Notification sends are simulated at every level below `live`.
- **`NotificationEvent`** gains no field. There is still no field a secret could reach (FR-037).
- **A send failure never fails, delays, or retries** the operation that triggered it (FR-035), and there
  is still no retry loop.
- **The accepted gap**: a notification never attempted because the process died between the transition and
  the send is not recorded as missed.

## Changed

### The channel is no longer singular

> *"No second HTTP client, no second URL knob: `[health] webhook_url` is the channel (R14)."*

Still one HTTP client — `httpx`, reached through `health.post_json` and the new `health.post_form`. Still
no second URL knob. But there are now zero, one, or two channels, built by `channels.build(config)`, and
every message goes to each of them.

### The Pushover claim was wrong

> *"A generic webhook covers ntfy and Pushover — both named in the planning document — without either
> becoming a dependency."*

ntfy accepts a JSON body. **Pushover does not** — it takes form-encoded parameters. Pointing
`webhook_url` at Pushover produces a rejected request, not a push notification. This is issue #106's root
cause, and the claim appears in three places that all need correcting: `health.post_json`'s docstring,
`tests/unit/test_health.py:173`'s docstring, and R14 in the milestone-004 contract.

The *instinct* behind R14 survives: one transport module, one timeout convention, no vendor client
library. What changes is that "one transport" now means one module with two bounded POST functions.

### `max_per_cycle` counts messages, not deliveries

Previously the two were the same number, so the distinction never had to be made. It does now: the cap
bounds a burst of *news*, not a burst of *packets* — which is exactly how R15 argued for it. An author
with two channels may see up to `2 × max_per_cycle` HTTP requests per tick, and that is correct.

The suppression summary goes out through the same fan-out and therefore reaches every channel.

### A message and a delivery are different records

| Action | Status |
|---|---|
| `notify.send` | unchanged — one **message** |
| `notify.channel` | **new** — one **delivery**: channel, outcome, reason on failure |
| `notify.suppressed` | unchanged |
| `notify.failed` | **removed**, superseded by `notify.channel` |

The record must be able to say "the webhook took it and Pushover did not". That is the whole reason
`notify.channel` exists, and why keeping `notify.failed` alongside it would be two ways to say one thing.

### The simulated path names its channels

`SimulatedNotifier` logs the composed body and returns `True`, as before, and now also records the names
of the channels that were configured. Below `live` there are no deliveries to record — only an intent, and
the intent is one message aimed at a known list.

## The health alert: a documented exception

The stale-heartbeat alert `robot-army health --notify` sends **is not part of this contract's boundary**
and never was. `operations.health_check` calls the transport directly and touches no boundary, so the
alert is sent for real at every effect level.

That stays true, and Pushover joins it on exactly those terms. `health --notify` takes no
`--effect-level` flag and resolves its level from `[daemon] effect_level`; gating the alert would silently
disable the dead-man's switch for anyone running their daemon at `local`. The effect level governs what
the *daemon* does autonomously — a human, or that human's systemd timer, running `health --notify` has
already made the decision the effect level exists to withhold.

The spec's original acceptance scenario assumed the opposite. The spec was corrected. **A test pins this
behaviour**, because it is exactly the kind of inconsistency a future reader would otherwise "fix".
