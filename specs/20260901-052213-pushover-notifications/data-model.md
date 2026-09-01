# Data Model: Pushover Notifications

**No database change.** `SCHEMA_VERSION` does not move. No table is read or written by this feature, and
no migration is needed. Everything below is in-memory configuration, one in-flight value object, and the
shape of what reaches the audit log.

---

## Persistent state

Exactly two new files, both created by hand by the author, both read and never written by the process:

| File | Holds | Required mode | Read when |
|---|---|---|---|
| `[pushover] token_file` | The Pushover application's API token | 0600 | Each send |
| `[pushover] user_key_file` | The account's user key | 0600 | Each send |

Read at the moment they are needed, never retained in the loaded `Config` (FR-003) — the same discipline
`GitHubConfig.read_token` follows (`config.py:150`). A file that vanishes between load and send produces a
recorded delivery failure naming the *path*, never the contents.

---

## `PushoverConfig`

New frozen dataclass in `config.py`, alongside `GitHubConfig` and `TrelloConfig`.

```python
@dataclass(frozen=True, slots=True)
class PushoverConfig:
    token_file: Path
    user_key_file: Path

    def read_token(self) -> str: ...
    def read_user_key(self) -> str: ...
```

- Both fields are non-optional. A `PushoverConfig` exists only when both were configured and both
  validated; there is no half-built form. This is what makes "a half-configured channel cannot send" a
  property of the type rather than a check somewhere downstream.
- Reachable as `config.pushover: PushoverConfig | None`. `None` means the section was absent — the
  unconfigured installation, and the same `None`-when-absent shape `config.trello` uses.
- Each reader strips trailing whitespace, because a credential file written with `echo` ends in a newline
  and a newline in a form parameter is a 4xx nobody enjoys diagnosing.

### Validation rules

Applied at load, aggregated with every other problem rather than raised on the first (the module's
established behaviour), each naming the offending key:

| Rule | Outcome when broken | Spec |
|---|---|---|
| Both keys set, or neither | Error: names the missing key | FR-004 |
| Each file exists | Error: names the key and the path | FR-005 |
| Each file is mode 0600 (`mode & 0o077 == 0`) | Error: names the key, the path, and the found mode | FR-005 |
| No value looks like a literal credential | Error: says it must come from a mode-0600 file | FR-006 |
| No unknown keys in the section | Error — `[pushover]` joins `_STRICT_KEY_SECTIONS` | — |

The literal-credential scan uses a Pushover-specific pattern (30 alphanumeric characters) consulted only
inside this section, not the shared `_TOKEN_PATTERNS` tuple — see [research.md](./research.md) R6 for why
widening the shared tuple would create an error an author could not clear.

`[pushover]` is added to `_SECTIONS`, `_STRICT_KEY_SECTIONS`, and `_KNOWN_KEYS` with
`{"token_file", "user_key_file"}`.

### One reworded warning

`config.py:794` currently warns when `[notifications] events` is set and `[health] webhook_url` is empty.
That condition becomes "no channel at all is configured" — webhook *or* Pushover satisfies it (FR-015).
It stays a warning, not an error: the intent is legible and the resolution is obvious.

---

## `NotificationEvent` — unchanged

No new field. This is a requirement, not an omission: the event carrying only identifiers and state names
is what makes FR-007 a property of one composer rather than a rule spread across four call sites. The
milestone-004 contract says there is no field a secret could reach, and that stays true.

---

## The message, as the channels see it

Not a class — three arguments, which is the whole point. Both senders produce the same shape and each
channel reads what it understands:

```python
Channel.send(title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]
```

| Sender | `title` | `message` | `fields` |
|---|---|---|---|
| Notification event | `event.title` | `event.detail` | `{"kind", "item_id", "repo_key", "url"}` |
| Stale-heartbeat alert | `"robot-army health check failed"` | `report.reason` | `{"healthy", "age_seconds"}` |

`fields` reproduces both of today's wire bodies **exactly** — the webhook splices the dict wholesale into
its JSON body, which is how the existing bodies survive byte-for-byte (FR-016). Pushover reads only
`url` and ignores the rest, which is right for a push notification.

The `tuple[bool, str]` return is `health.notify`'s existing shape: the `bool` is what `Notifier.send`
needs, the `str` is the human-readable line `robot-army health --notify` prints per channel.

---

## Channel identity

Each channel has a stable `name` — `"webhook"`, `"pushover"` — used in audit records, in the startup
record, and in `health --notify`'s output. These are the strings a future reader greps for, so they are
fixed here rather than derived from a class name that might be renamed.

`channels.build(config)` returns them in a stable order, webhook first. Order is not semantically
meaningful — neither channel depends on the other, and one's failure never stops the other — but a fixed
order makes the log and the test assertions stable.

| Configured | `build()` returns |
|---|---|
| Neither | `()` — nothing is sent, no request is constructed |
| Webhook only | `(WebhookChannel,)` — today's behaviour, unchanged |
| Pushover only | `(PushoverChannel,)` |
| Both | `(WebhookChannel, PushoverChannel)` |

---

## Audit records

| Action | Status | Fields |
|---|---|---|
| `notify.send` | unchanged | One **message**: `kind`, `suppressed`, `title`, `delivered` |
| `notify.channel` | **new** | One **delivery**: `channel`, `kind`, `item_id`, outcome, and `reason` on failure |
| `notify.suppressed` | unchanged | `count`, `kinds`, `max_per_cycle` |
| `notify.failed` | **removed** | Superseded by `notify.channel` |
| `health.notify` | gains `channel` | One record per channel: `channel`, `reason`, `message`, outcome |

A message and a delivery are now different things, and the record has to be able to say "the webhook took
it and Pushover did not". That is the entire reason `notify.channel` exists.

Neither credential appears in any of these, in any field, including `reason` — see plan.md's Principle III
section for why that is structural rather than a rule.

---

## What deliberately has no model

- **No delivery queue, no outbox, no retry table.** A notification missed because the process died is not
  recorded as missed; the state change itself is fully logged. This is milestone 004's accepted gap,
  restated in plan.md and not widened.
- **No per-channel enable/disable flags.** A channel is configured or it is not; that is the switch.
- **No receipts, acknowledgements, or anything inbound from Pushover.** Out of scope, and Principle II's
  no-inbound-exposure rule points the same way.
