# Contract: Notifications

A ninth boundary, four call sites, one bound. Off by default.

## The boundary

```python
@runtime_checkable
class Notifier(Protocol):
    def send(self, event: NotificationEvent) -> bool: ...
```

`REAL_AT["notifier"] = frozenset({EffectLevel.LIVE})` — a notification is an outward-facing write, so
it is simulated at every level below `live` (FR-040). The simulated implementation logs the call with
its full arguments and returns `True`, exactly as the other simulated writers do, so the simulated path
cannot diverge from the real one.

The real implementation reuses the bounded POST `health.notify` already performs, extracted so both
share one explicit-timeout call. No second HTTP client, no second URL knob: `[health] webhook_url` is
the channel (R14).

> **Superseded in part by issue #106.** The premise of R14 was that "a generic webhook covers ntfy and
> Pushover without either becoming a dependency". **The Pushover half was factually wrong**: ntfy accepts
> an arbitrary JSON body, Pushover takes form-encoded parameters and rejects one, so pointing
> `webhook_url` at Pushover produced a rejected request rather than a notification.
>
> The *instinct* survives untouched — one HTTP client, one timeout convention, no vendor client library,
> and still no second URL knob. What changed is that the real implementation is now a fan-out over zero,
> one or two **channels** built by `robot_army.channels.build`, and `health.notify` was replaced by the
> pure composer `health.alert_fields` so the stale-heartbeat alert can reach them too. See
> `specs/20260901-052213-pushover-notifications/contracts/notifications.md`.

## Call sites

Four, each one line, each immediately **after** its transaction closes:

| Kind | Emitted from | On |
|---|---|---|
| `dispatch` | `dispatch.py` | A session is confirmed running |
| `completion` | `reconcile.py` | An item reaches `awaiting_review` or `done` |
| `failure` | `reconcile.py`, `dispatch.py` | An item reaches `failed` |
| `needs_info` | `intake.py` | A card enters `needs_info` |

Hooking `states.transition()` instead would be structurally complete and impossible to forget, and was
rejected for one reason: it runs inside `BEGIN IMMEDIATE`, so a slow webhook would hold a write
transaction open. Four explicit call sites outside the transaction is the honest trade (R14, and see
plan.md's post-design re-check).

## Configuration

```toml
[notifications]
events = []            # subset of dispatch | completion | failure | needs_info
max_per_cycle = 5
```

Empty by default, so nothing notifies and no outbound request is made until the author asks (FR-033).
With no channel configured — since issue #106 that means neither `[health] webhook_url` nor
`[pushover]` — `events` is a warning rather than an error: the intent is legible and the resolution is
obvious.

## The bound (R15)

At most `max_per_cycle` sends per daemon tick. Beyond that, one summary notification naming how many
were suppressed and of which kinds.

Per-(kind, item) de-duplication would **not** bound a backlog, because a backlog produces different
items — the very case that would flood. A per-cycle cap does. The counter is in process memory and is
not persisted: it exists to bound one burst, and a restart mid-burst re-permitting a handful of
messages is not worth a table.

Every send is recorded in the audit log whether or not it was suppressed (`notify.send`,
`notify.suppressed`), so the reconstruction standard is met by the log rather than by the channel.

## Failure and content

- A send failure is recorded and never fails, delays, or retries the operation that triggered it
  (FR-035). Retries are bounded by the transport's existing policy; there is no retry loop here.
- The event carries identifiers and state names only. There is no field a secret could reach, and a
  test asserts it across a run including an authentication failure (FR-037, SC-010).
- A notification never attempted because the process died between the transition and the send is not
  recorded as missed. The state change itself is fully logged; the gap is named in plan.md's Principle
  III section and is the accepted cost of refusing a durable outbound queue.
