# Research: Pushover Notifications

Every claim below was measured against this checkout on 2026-09-01, not recalled. Line numbers are
from the tree at `125d5e8`.

---

## R1 — The current design explicitly rejected Pushover, and the reason no longer holds

`health.post_json` (`src/robot_army/health.py:151`) carries this docstring:

> *"A generic webhook covers ntfy and Pushover — both named in the planning document — without either
> becoming a dependency."*

`tests/unit/test_health.py:173` repeats the claim in its own docstring, and
`specs/004-concurrency-polish/contracts/notifications.md` records the same decision as R14.

**The claim is false.** Pushover's message API accepts `application/x-www-form-urlencoded` parameters
(`token`, `user`, `message`, `title`, `url`) at `https://api.pushover.net/1/messages.json`. It does not
accept an arbitrary JSON document. `post_json` posts `json=body` and nothing else, so pointing
`webhook_url` at Pushover produces a rejected request, not a push notification. ntfy genuinely does
accept a JSON body; Pushover was assumed to work the same way and does not.

**Decision**: treat this as a factual correction, not a design reversal. The milestone-004 instinct —
one transport, one timeout, no vendor client library — survives intact. What changes is that "one
transport" now means one *module* owning both a bounded JSON POST and a bounded form POST, rather than
one function.

**Alternatives considered**: leave the webhook generic and tell the author to run a relay (an ntfy
instance, a shell script behind a local HTTP listener). Rejected: it makes a phone notification depend
on a second always-on process on a machine whose whole premise is that it might be asleep, and
Principle II's "no deployment infrastructure beyond a shell on this machine" cuts against it.

**Follow-through**: both docstrings and the contract note must be corrected as part of this work.
Leaving a false claim in a docstring is how the next person re-derives the wrong answer.

---

## R2 — There are two senders, not one, and only one of them goes through a boundary

This is the finding that shapes the whole design.

| Path | Composes body at | Sends via | Effect-level gated? |
|---|---|---|---|
| Four notification event kinds | `boundaries/notifier.py:compose` | `boundaries.notifier.send` | **Yes** — `REAL_AT["notifier"] = {LIVE}` |
| Stale-heartbeat alert | `health.notify` (`health.py:178`) | direct `health.post_json` | **No** — no boundary is involved |

`operations.health_check` (`operations.py:2859`) calls `health.notify(ctx.config.health.webhook_url,
report)` directly. It never touches `ctx.boundaries`.

**Decision**: keep the health alert outside the effect-level system, and let Pushover join it on
exactly those terms.

**Rationale**, and this is not a shortcut:

- `robot-army health --notify` takes no `--effect-level` flag (`cli.py:139-143`). It builds its context
  with `operations.build_context(config)` (`cli.py:292`), which resolves the level from
  `config.daemon.effect_level` (`operations.py:170`).
- So routing the alert through the notifier boundary would mean: an author who runs their daemon at
  `local` — a documented, supported posture — silently stops being told that their daemon is dead. The
  dead-man's switch would be disabled by a setting that has nothing to do with it.
- The effect level governs what the *daemon* does autonomously on the author's behalf. A human (or that
  human's systemd timer) running `health --notify` has already made the decision the effect level exists
  to withhold.

This contradicted the spec's original User Story 4 acceptance scenario 5, which assumed simulation
applied. **The spec was corrected**, not the code bent to fit it: AS5, FR-012, FR-018, and one edge case
now state the exception and why it exists.

**Alternatives considered**:

1. *Route both through the boundary.* Rejected for the reason above; it would be a silent regression of
   the single most important message the system sends.
2. *Gate only the new Pushover health send.* Rejected: two channels behaving differently on the same
   message is the kind of inconsistency that is discovered at 3am.
3. *Add `--effect-level` to `health`.* Rejected as scope the issue did not ask for, and it would not help
   the systemd timer, which is the actual caller.

---

## R3 — Where the fan-out lives: one channel module, two callers

R2 leaves two independent send paths that both need "every configured channel". The naive fix is two
fan-out loops, which is two places to get the channel list right.

**Decision**: a new module `src/robot_army/channels.py` that owns *where a message goes*, with one
protocol and one builder:

```python
class Channel(Protocol):
    name: str
    def send(self, title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]: ...

def build(config: Config) -> tuple[Channel, ...]: ...
```

`fields` is the message's structured payload. Each channel takes what it understands and ignores the
rest — the webhook splices the whole dict into its JSON body, Pushover reads only `url`. That single
signature reproduces **both** of today's bodies exactly:

- event: `fields = {"kind", "item_id", "repo_key", "url"}` → the body `compose` builds today
- health: `fields = {"healthy", "age_seconds"}` → the body `health.notify` builds today

The `tuple[bool, str]` return is `health.notify`'s existing shape, which is what gives `health --notify`
its human-readable per-channel line for free.

Two callers, and only two: the notifier boundary (events) and `operations.health_check` (the alert).

**Alternatives considered**:

1. *A second boundary field, `pushover_notifier: Notifier | None`, mirroring the `card_reader`/`card_writer`
   `None`-when-absent pattern.* Genuinely tempting — it is an established pattern in this codebase. Rejected
   because it does nothing for the health path (which has no boundaries) and because at a simulated level the
   two fields would either produce two simulated records for one event or lose the fact that Pushover was
   configured at all.
2. *Fan out inside `notifications._deliver` over a tuple of notifiers.* Rejected: it changes `Boundaries`
   from `notifier` to `notifiers`, touching `describe()`, `conftest.make_boundaries`, and every test that
   passes `notifier=`, and it still leaves the health path unserved.
3. *Give `WebhookNotifier` a Pushover sibling and have callers try both.* Rejected: the "which channels are
   configured" decision would be duplicated at each call site, which is the bug this module exists to prevent.

Is a tuple of channels speculative generality under Principle I? No. There are two concrete channels in
hand and two concrete callers. What is forbidden is the abstraction with one caller and no second use;
this has two of each.

---

## R4 — Pushover's API surface, and what of it we use

Measured against Pushover's published message API.

- **Endpoint**: `POST https://api.pushover.net/1/messages.json`, form-encoded.
- **Required**: `token` (the application's API token), `user` (the user or group key).
- **Used**: `message`, `title`, `url`.
- **Not used**: `priority`, `sound`, `device`, `expire`, `retry`, `html`, `timestamp`, `url_title`,
  `attachment`. Out of scope per the spec; each would be a knob with one caller (Principle I).
- **Limits**: `message` 1024 characters, `title` 250, `url` 512. Exceeding them is a 4xx, not a
  truncation, so we truncate before sending.
- **Success**: HTTP 200 with `{"status": 1, "request": "..."}`. **Failure**: 4xx with
  `{"status": 0, "errors": [...]}`.

**Decision on credentials in transit**: both credentials go in the **form body**, never the URL or a
header we log. This matters for FR-007: `post_json`'s existing error strings interpolate the URL
(`health.py:172`, `:174`), and the analogous form-POST errors will too. Because the token is not in the
URL, that interpolation is safe by construction rather than by a rule someone has to remember.

**Decision on the response body**: we record HTTP status and our own message, never the response body.
Pushover's `errors` array is descriptive text, but recording an upstream body verbatim is exactly the
habit that leaks a credential the day an upstream starts echoing the request.

**Decision on truncation**: truncate `message` to 1024 and `title` to 250 at the channel, and keep the
untruncated text in the audit record. A rejected message tells the author nothing; a truncated one tells
them most of it, and the log has the rest.

**No new dependency.** `httpx` is already the sole runtime dependency (`pyproject.toml:13`) and
`httpx.post(url, data=...)` form-encodes natively.

---

## R5 — Where the timeout lives

Principle IV requires an explicit timeout on every network call. Today there is exactly one such call
(`post_json`) and therefore exactly one timeout to keep correct — a property worth preserving.

**Decision**: add `post_form(url, data, *, timeout=10.0) -> tuple[bool, str]` beside `post_json` in
`health.py`, same signature shape, same default, same bounded no-retry behaviour. Two functions in one
module, sharing one convention, is still one place to look.

**Alternatives considered**: a shared `_post(url, *, json=None, data=None)` with two thin wrappers.
Rejected — the branch buys nothing over two eight-line functions and makes the call site harder to read.

**No retry loop.** Principle IV bounds retries; the existing channel has none, and a notification is the
one thing where a retry is least justified: the state change is already durably logged, and the point of
FR-010 is that the channel is never the operation's problem.

---

## R6 — Configuration shape and the credential guard

**Decision**: a new optional `[pushover]` section holding exactly two keys.

```toml
[pushover]
token_file = "~/.config/robot-army/pushover-token"      # the application API token
user_key_file = "~/.config/robot-army/pushover-user"    # the account's user key
```

Absent section → `config.pushover is None` → channel not built. This mirrors `[trello]` exactly
(`config.trello` is `None` when unconfigured, `effects.wire:220`), which is the established shape for
"an optional integration that must be completely inert when unconfigured".

**Naming**: `token_file` matches `[github] token_file` and `[trello] token_file`; Pushover's own API
parameter is `token`. `user_key_file` matches Pushover's `user` parameter and its dashboard label, and
avoids the ambiguity of calling both credentials "key". The issue's wording ("api key and user key") maps
to these two.

**Why a new section rather than keys under `[notifications]`**: `[notifications]` answers *what to say
and how often*; `[health] webhook_url` and `[pushover]` answer *where to say it*. Putting credentials in
`[notifications]` would also make the section's meaning depend on which of its keys were set.

**Why files only, no `*_env` twins**: the issue asks for files, and Principle I forbids the knob with no
caller. `[github]` and `[trello]` offer both because both were asked for; nothing asks here.

**Validation**, all at load, all reported together with every other problem:

1. Both keys present, or neither. One alone is an error naming the missing key — a half-configured
   channel cannot send, and a warning would produce a channel that silently never fires.
2. Each file exists.
3. Each file is mode 0600 (`mode & 0o077 == 0`), the rule `_trello_credential` already applies
   (`config.py:991`).
4. A literal credential inline is an error, as it is for `[github]` (`config.py:633`) and `[trello]`
   (`config.py:1070`).

**On the literal-credential scan**: `_TOKEN_PATTERNS` (`config.py:44`) cannot currently recognise a
Pushover credential — they are 30-character alphanumeric strings, matching none of the GitHub prefixes
or the 32/64-hex Trello shapes. Adding `^[A-Za-z0-9]{30}$` to the shared tuple would apply it to
`[github]` and `[trello]` too, where a legitimate 30-character alphanumeric label is improbable but
possible — and the failure mode there is an error the author *cannot* clear. So the 30-character rule is
a separate pattern consulted only when scanning `[pushover]`, whose only legitimate values are paths.
This is the same gap milestone 003 named for Trello: a guard that cannot match the credential it guards
proves nothing.

**Refactor, not addition**: `_trello_credential` already does exists-and-mode. The exists-and-mode half
is extracted into a shared `_secret_file(section, key, raw, problems)` used by both, so the new section
adds a caller rather than a copy.

---

## R7 — What `max_per_cycle` counts, and what breaks if we get it wrong

`notifications.emit` increments `_CYCLE["sent"]` once per **event**, before `_deliver`
(`notifications.py:143`). With one channel, messages and deliveries were the same number, so the
distinction never had to be made.

**Decision**: the counter keeps counting events. Adding a second channel must not halve how many things
the author is told about — the cap exists to bound a burst of *news*, not a burst of *packets*, and R15
of milestone 004 argues the cap in exactly those terms. The suppression summary goes out through the same
fan-out and therefore reaches every channel.

Consequence worth stating plainly: an author with two channels can see up to `2 × max_per_cycle` HTTP
requests per tick. That is correct and intended.

---

## R8 — Audit records, and the one existing gap this work exposes

Per-message and per-delivery are now different things, so the record has to say both.

| Action | Status | Meaning |
|---|---|---|
| `notify.send` | existing, unchanged | One *message*: its kind, its item, whether the bound suppressed it |
| `notify.channel` | **new** | One *delivery*: channel name, outcome, and the reason on failure |
| `notify.suppressed` | existing, unchanged | The bound was reached this cycle |
| `notify.failed` | existing | Superseded by `notify.channel`; removed rather than left as a second way to say the same thing |
| `health.notify` | existing, gains `channel` | One health-alert delivery, now one record per channel |

`docs/logging.md:227` documents `notify.send` and `notify.suppressed` and — an existing gap — documents
neither `notify.failed` nor `health.notify`. This work adds `notify.channel` and fills in `health.notify`
while it is in there.

**Principle III statement**: every delivery attempt on every channel produces a record, whether it
succeeded, failed, or was refused by the transport. Nothing is swallowed. The one accepted gap is the
existing one, unchanged and not widened: a notification never attempted because the process died between
the state transition and the send is not recorded as missed. The transition itself is fully logged. This
was named in milestone 004's plan as the accepted cost of refusing a durable outbound queue, and adding a
second channel does not change the argument.

---

## R9 — What the simulated path must show

`SimulatedNotifier` (`boundaries/notifier.py`) logs the composed body and returns `True`. With two
channels, a single simulated record can no longer say *where* the message would have gone.

**Decision**: the simulated notifier records the names of the channels that were configured, alongside
the one composed body. One record, not one per channel: below `live` nothing is sent, so there are no
deliveries to record — only an intent, and the intent is one message aimed at a known list.

`Boundaries.describe()` (`effects.py:157`) reports `type(x).__name__` per boundary for the startup record
(FR-057). With a composite notifier that would read `MultiNotifier`, losing which channels are live —
which is precisely the fact a reader of the startup record wants. `describe()` gains a three-line
`describe_name()` lookup so a boundary can name itself more precisely; the notifier is the only
implementer today.

---

## R10 — Blast radius, measured

Changed:

- `src/robot_army/channels.py` — **new**
- `src/robot_army/health.py` — `post_form` added; `post_json` docstring corrected; `notify` replaced by a
  pure `alert_fields(report)` composer
- `src/robot_army/boundaries/notifier.py` — `MultiNotifier` replaces `WebhookNotifier`; `compose` reuses
  the shared body builder; `SimulatedNotifier` names its channels
- `src/robot_army/effects.py` — `wire` builds channels; `describe()` gains the name hook
- `src/robot_army/config.py` — `[pushover]` section, `PushoverConfig`, `_secret_file` extraction, the
  Pushover-only literal-credential pattern, and the "no channel configured" warning
- `src/robot_army/operations.py` — `health_check` fans out and records one line per channel
- `docs/logging.md`, `README.md`, and `specs/004-concurrency-polish/contracts/notifications.md`'s R14 note

**Not** changed: the database schema (`SCHEMA_VERSION` does not move), the state machines, the four
notification call sites, when anything is emitted, the `NotificationEvent` shape, the webhook body for
either message, the CLI surface, and the web interface — which references neither notifier nor webhook.

No new runtime dependency. No migration. No new command. No new effect level.
