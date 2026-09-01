# Contract: Channels

One module, `src/robot_army/channels.py`, owning **where a message goes**. It exists because there have
always been two senders — the notifier boundary and `operations.health_check` — and only one of them went
through a boundary ([research.md](../research.md) R2). Without a shared module, "which channels are
configured" would be decided in two places.

## The protocol

```python
@runtime_checkable
class Channel(Protocol):
    name: str
    def send(self, title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]: ...
```

- **Never raises.** A channel failure is not the caller's problem (FR-010). Every exception is caught at
  the channel and returned as `(False, reason)`. `notifications._deliver`'s existing outer `try` stays as
  the backstop, not as the primary guard.
- **Never blocks indefinitely.** Explicit 10s timeout, no retry (Principle IV, R5).
- **Reads what it understands from `fields`, ignores the rest.** This is what lets one signature serve
  both a notification event and a health alert without either sender knowing about the other.
- The returned `str` is a human-readable line — `"notified https://hook (HTTP 200)"`,
  `"pushover returned HTTP 400"` — printed per channel by `robot-army health --notify` and recorded on
  failure.

## `build(config) -> tuple[Channel, ...]`

```python
def build(config: Config) -> tuple[Channel, ...]:
    channels: list[Channel] = []
    if config.health.webhook_url:
        channels.append(WebhookChannel(config.health.webhook_url))
    if config.pushover is not None:
        channels.append(PushoverChannel(config.pushover))
    return tuple(channels)
```

Stable order, webhook first. Returns `()` when nothing is configured — and an empty tuple means **no
request is constructed**, not "a request is built and skipped at the last moment". That distinction is
the one milestone 004 drew for `[notifications] events` and it is drawn again here.

## `WebhookChannel`

`name = "webhook"`. Wraps the URL from `[health] webhook_url`.

```python
def webhook_body(title, message, fields) -> dict[str, Any]:
    return {"title": title, "message": message, **fields,
            "host": os.uname().nodename,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
```

This is the **single** composer. `boundaries.notifier.compose(event)` becomes a thin call into it, so the
body the simulated notifier records and the body the real channel posts cannot drift — today they are
built by two functions that happen to agree.

Sends via the existing `health.post_json`. **The body is byte-for-byte what it is today** for both
senders, which is what makes FR-016 true rather than hoped for:

- event: `{title, message, kind, item_id, repo_key, url, host, ts}`
- health: `{title, message, healthy, age_seconds, host, ts}`

## `PushoverChannel`

`name = "pushover"`. Wraps a `PushoverConfig`.

```
POST https://api.pushover.net/1/messages.json      (application/x-www-form-urlencoded)
  token   = <token_file contents, stripped>
  user    = <user_key_file contents, stripped>
  title   = title[:250]
  message = message[:1024]
  url     = fields["url"]        # only when present and non-empty
```

Sends via a new `health.post_form`, the form-encoded sibling of `post_json`: same signature shape, same
10s default, same no-retry behaviour, same `tuple[bool, str]` return (R5).

**Credentials are read at send time**, not at load, and travel in the **form body** — never the URL, never
a header, never a log field. Because the token is not in the URL, the error strings that interpolate a URL
are safe by construction rather than by a rule someone has to remember (R4).

**Truncation, not rejection.** Pushover's limits are `message` 1024, `title` 250, `url` 512, and exceeding
them is a 4xx. We truncate before sending and keep the untruncated text in the audit record: a rejected
message tells the author nothing, a truncated one tells them most of it, and the log has the rest.

**The response body is never recorded.** Only the HTTP status and our own message. Recording an upstream
body verbatim is how a credential leaks the day the upstream starts echoing the request.

**Not sent, deliberately**: `priority`, `sound`, `device`, `expire`, `retry`, `html`, `timestamp`,
`url_title`, `attachment`. Each would be a knob with one caller (Principle I); each can be added the day
something concrete needs it.

## The two callers

### 1. `MultiNotifier` — the `notifier` boundary

Replaces `WebhookNotifier`. Wired at `live` only; `REAL_AT["notifier"]` is unchanged.

```python
class MultiNotifier:
    def send(self, event: NotificationEvent) -> bool:
        any_ok = False
        for channel in self._channels:
            ok, detail = channel.send(event.title, event.detail, event_fields(event))
            self._audit.record("notify.channel", outcome="ok" if ok else "error", ...)
            any_ok = any_ok or ok
        return any_ok
```

- One `notify.channel` record per delivery, whichever way it went.
- Returns `True` if **any** channel accepted; `False` with zero channels. The return feeds
  `notifications._deliver`'s existing `notify.send` record, which continues to describe the *message*.
- `event_fields(event)` is `{"kind", "item_id", "repo_key", "url"}` — the one place that mapping lives.

`SimulatedNotifier` keeps its shape and gains the configured channel **names** in its record (R9). One
record, not one per channel: below `live` nothing is sent, so there are no deliveries to record — only an
intent, and the intent is one message aimed at a known list.

### 2. `operations.health_check` — the stale-heartbeat alert

```python
if not report.healthy and do_notify:
    for channel in channels.build(ctx.config):
        ok, detail = channel.send(*health.alert_fields(report))
        result.say(f"{channel.name}: {detail}")
        ctx.audit.record("health.notify", outcome="ok" if ok else "error",
                         detail={"channel": channel.name, "reason": report.reason, "message": detail})
```

`health.alert_fields(report)` is the pure composer returning `(title, message, fields)`. `health.notify`
is removed — it was this composer welded to a single transport.

**This path is not effect-level gated, for either channel.** `robot-army health --notify` takes no
`--effect-level` flag and resolves its level from `[daemon] effect_level`, so gating it would silently
disable the dead-man's switch for anyone running their daemon at `local`. This is a deliberate, documented
exception; see R2 and FR-018. **A test must pin it**, because it is exactly the kind of inconsistency a
future reader would "fix".

With zero channels configured, the loop does not execute and the command reports that nothing was sent
(FR-019). The wording changes from `"no webhook_url configured"` to a channel-neutral line — the one
user-visible string this feature changes, and only in the case where nothing is configured.

## Startup record

`Boundaries.describe()` reports `type(x).__name__` per boundary (`effects.py:157`). A composite would read
`MultiNotifier`, losing which channels are live — the one fact a reader of that record wants. `describe()`
gains a three-line lookup for an optional `describe_name()`, and the notifier implements it:

```
MultiNotifier(webhook, pushover)
SimulatedNotifier(webhook, pushover)
MultiNotifier()                       # configured to notify with nowhere to notify
```
