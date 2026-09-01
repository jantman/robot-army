# Feature Specification: Pushover Notifications

**Feature Branch**: `20260901-052213-pushover-notifications`

**Created**: 2026-09-01

**Status**: Draft

**Input**: User description: "issue #106 on this repo" — *robot-army currently supports webhooks for notifications. Add optional support for Pushover as well. Following the convention of configuring file paths for secrets, add new configuration parameters for pushover api key and user key files.*

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Be told on my phone when something happens (Priority: P1)

The author wants notification events (`dispatch`, `completion`, `failure`, `needs_info`) delivered to
Pushover, so the alerts arrive as push notifications on a phone rather than as a generic JSON POST that
Pushover cannot accept. Today the only channel is a generic JSON webhook, and Pushover's API does not
accept that shape — so "point the webhook at Pushover" is not something the author can do. They add two
credential file paths to the configuration and the same events they already chose start arriving.

**Why this priority**: This is the whole of the request. Without it the feature does not exist, and with
it alone the author gets every notification the system already knows how to emit, on a device they carry.

**Independent Test**: Configure the two Pushover credential files and a non-empty `[notifications] events`
list with no webhook configured at all, run a cycle that produces one of the chosen events, and confirm a
Pushover message is delivered and the send is recorded in the audit log.

**Acceptance Scenarios**:

1. **Given** Pushover credentials are configured and `events` includes `failure`, **When** an item reaches
   `failed`, **Then** a Pushover message carrying the same title, detail, and identifiers as the webhook
   body is delivered, and one `notify.send` record with outcome `ok` is written.
2. **Given** Pushover credentials are configured and `events` is empty (the default), **When** the daemon
   runs a full cycle, **Then** no request is made to Pushover at all.
3. **Given** Pushover credentials are configured, **When** the daemon runs below the `live` effect level,
   **Then** no request reaches Pushover and the message that would have been sent is recorded in full as a
   simulated send.
4. **Given** no Pushover credentials and no webhook are configured, **When** the configuration is loaded
   with a non-empty `events` list, **Then** loading succeeds with a warning saying the chosen events have
   nowhere to go.

---

### User Story 2 - Keep the webhook working, and run both at once (Priority: P2)

The author who already has a webhook configured is not made to choose. Adding Pushover does not remove,
replace, or alter the existing webhook channel; with both configured, every message goes to both, and each
channel's failure is independent of the other's.

**Why this priority**: The existing channel is in use and documented. A change that silently redirected or
disabled it would break a working installation, and the request was to add support "as well", not instead.

**Independent Test**: Configure both a webhook URL and Pushover credentials, emit one event, and confirm
two deliveries occurred and two send records were written for the single event.

**Acceptance Scenarios**:

1. **Given** both a webhook URL and Pushover credentials are configured, **When** one notification event is
   emitted, **Then** the message is delivered to both channels and the audit log shows the outcome of each
   channel separately.
2. **Given** both channels are configured and Pushover is unreachable, **When** an event is emitted, **Then**
   the webhook delivery still succeeds, the Pushover failure is recorded, and the operation that triggered
   the notification is neither failed, delayed, nor retried.
3. **Given** only a webhook URL is configured, **When** events are emitted, **Then** behaviour is byte-for-byte
   what it was before this feature existed.
4. **Given** both channels are configured, **When** a burst exceeds `max_per_cycle`, **Then** the cap still
   counts *messages*, not deliveries, and the single suppression summary goes to both channels.

---

### User Story 3 - Configure the credentials safely and be told when I got it wrong (Priority: P2)

The author points two configuration keys at local files holding the Pushover application token and user
key. Loading the configuration validates those files the same way the existing credential files are
validated, and a mistake is an error at load time rather than a silent failure at 3am.

**Why this priority**: The credentials are the only new configuration surface, and a credential feature that
fails obscurely — or that leaks the credential into a world-readable log in a public repository — is worse
than no feature. Validation is cheap and belongs in the same slice as the keys themselves.

**Independent Test**: Run the configuration check against configurations that are (a) correct, (b) missing
one of the two files, (c) pointing at a file readable by other users, and (d) carrying a literal credential
inline, and confirm each is accepted or refused with a message naming the offending key.

**Acceptance Scenarios**:

1. **Given** both credential files exist and are readable only by their owner, **When** the configuration is
   loaded, **Then** it loads without error and the credential values are not present anywhere in the loaded
   configuration.
2. **Given** only one of the two credential keys is configured, **When** the configuration is loaded, **Then**
   loading fails with an error naming the missing key, because a half-configured channel cannot send.
3. **Given** a credential file that does not exist, or one whose permissions allow group or other access,
   **When** the configuration is loaded, **Then** loading fails with an error naming the key and the path.
4. **Given** a credential value written directly into the configuration file instead of a path, **When** the
   configuration is loaded, **Then** loading fails with an error saying the credential must come from a
   mode-0600 file.
5. **Given** any Pushover delivery, successful or failed, **When** its audit record is read back, **Then**
   neither the application token nor the user key appears in it, nor in any error text it carries.

---

### User Story 4 - Know when the daemon has gone quiet (Priority: P2)

The alert that matters most is the one saying the daemon itself has stopped: a stale or absent heartbeat.
That alert currently goes to the webhook only. An author whose only channel is Pushover would otherwise be
told about individual item failures and never about the process that reports them having died — the wrong
one to miss. A configured Pushover channel therefore carries the stale-heartbeat alert too.

**Why this priority**: It is the highest-value message the system sends, and a channel that cannot carry it
is a partial channel. It is P2 rather than P1 only because it is a separately deliverable slice: the health
alert is composed on a different path from the four notification event kinds, and User Story 1 is a complete,
useful feature without it.

**Independent Test**: Configure Pushover with no webhook, let the heartbeat go stale, request the health
alert, and confirm a Pushover message describing the stale heartbeat arrives and the attempt is recorded.

**Acceptance Scenarios**:

1. **Given** Pushover is configured and the heartbeat is stale, **When** the health alert is requested,
   **Then** a Pushover message naming the staleness reason is delivered and the attempt is recorded.
2. **Given** both a webhook and Pushover are configured and the heartbeat is stale, **When** the health alert
   is requested, **Then** both channels receive it and each outcome is recorded separately.
3. **Given** only a webhook is configured, **When** the health alert is requested, **Then** the behaviour and
   the reported result are what they were before this feature existed.
4. **Given** no channel at all is configured, **When** the health alert is requested, **Then** the command
   reports that nothing was sent, without erroring.
5. **Given** the health alert is requested at any effect level, **When** it is requested, **Then** it is sent
   for real on every configured channel, because the health alert is a human-invoked dead-man's switch and
   is deliberately outside the effect-level system today — Pushover joins it on exactly those terms.

---

### Edge Cases

- **The credential file is deleted or replaced between load and send.** The send fails, the failure is
  recorded with a reason that names the file rather than its contents, and the triggering operation is
  unaffected.
- **Pushover rejects the credentials** (revoked token, wrong user key). The rejection is recorded once per
  send as a channel failure. There is no retry loop and no escalation; the audit log is the record.
- **Pushover is slow or unreachable.** The send is bounded by an explicit timeout and gives up. A dead
  notification channel never holds a database transaction open, never delays a dispatch, and never stalls a
  daemon tick.
- **Pushover's message length limit is exceeded** by a long detail string. The message is delivered in a
  truncated form rather than being rejected outright, and the audit record retains the untruncated text.
- **The process dies between a state change and the send.** As today, the missed notification is not
  recorded as missed; the state change itself is fully logged. Adding a channel does not add a durable
  outbound queue.
- **Both channels fail for the same message.** Two independent failure records are written; the operation
  still succeeds.
- **The daemon is configured below `live` and the heartbeat goes stale.** The alert is still delivered on
  every configured channel. This is today's behaviour for the webhook and is preserved rather than changed;
  see the effect-level note on FR-018.
- **Pushover is the only configured channel and `events` is empty.** Notification events are silent, but the
  stale-heartbeat alert still reaches Pushover when requested: the health alert is not one of the four event
  kinds and is not gated by `events`.
- **Pushover credentials are configured but `events` is empty.** Nothing is sent, and this is not a warning —
  it is the documented default posture.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support Pushover as an optional delivery channel for the existing notification
  events, in addition to — never instead of — the existing webhook channel.
- **FR-002**: The system MUST accept configuration naming a local file path for the Pushover application
  token and a local file path for the Pushover user key, following the existing convention for
  credential files.
- **FR-003**: The system MUST read each credential from its file at the moment it is needed, and MUST NOT
  retain either credential in the loaded configuration.
- **FR-004**: The system MUST refuse to load a configuration in which exactly one of the two Pushover
  credential keys is set, naming the missing key.
- **FR-005**: The system MUST refuse to load a configuration whose Pushover credential file does not exist,
  or whose permissions grant access beyond its owner, naming the key and the path in each case.
- **FR-006**: The system MUST refuse to load a configuration carrying a literal Pushover credential inline
  rather than a file path, as it already does for the other credentials.
- **FR-007**: The system MUST NOT write either Pushover credential to any log record, error message, or
  notification body.
- **FR-008**: The system MUST send Pushover messages only when the Pushover channel is fully configured and
  the emitted event's kind is one the author selected; an unconfigured installation MUST make no request to
  Pushover.
- **FR-009**: When both channels are configured, the system MUST deliver each message to both, and MUST
  record the outcome of each delivery independently.
- **FR-010**: A Pushover delivery failure MUST NOT fail, delay, or retry the operation that triggered it,
  and MUST NOT prevent delivery on the other channel.
- **FR-011**: Every Pushover delivery attempt MUST be recorded in the audit log with its outcome, whether it
  succeeded or failed.
- **FR-012**: A Pushover send triggered by a notification event MUST be treated as an outward-facing write:
  real only at the `live` effect level, and simulated at every level below it, with the message that would
  have been sent recorded in full. The stale-heartbeat alert is the documented exception (FR-018).
- **FR-013**: The per-cycle notification bound MUST continue to count messages rather than deliveries, so
  adding a second channel does not halve the number of events the author is told about; the suppression
  summary MUST reach every configured channel.
- **FR-014**: The Pushover request MUST set an explicit timeout and MUST NOT retry beyond the transport's
  existing bounded policy.
- **FR-015**: The system MUST warn at configuration load when notification events are selected but no
  channel — neither webhook nor Pushover — is configured, replacing the current webhook-only warning.
- **FR-016**: An existing configuration with only a webhook URL MUST behave exactly as it does today, with no
  new required keys and no change to the webhook body.
- **FR-017**: The Pushover message MUST carry the same information the webhook body carries — title, detail,
  event kind, and the item and repository identifiers — expressed in the form Pushover accepts.
- **FR-018**: The system MUST deliver the stale-heartbeat health alert to every configured channel, so a
  configured Pushover channel receives it as well as, or instead of, the webhook. The alert MUST keep its
  current relationship to the effect level — it is sent for real whatever the level says — because it is a
  human-invoked dead-man's switch, and gating it would silently disable the alert for an author whose
  daemon runs below `live`.
- **FR-019**: When an author requests the health alert and no channel is configured, the system MUST report
  that nothing was sent, exactly as it does today, and MUST NOT treat the absence of a channel as an error.
- **FR-020**: The documented configuration reference MUST describe the new keys, how to obtain the two
  Pushover credentials, and the fact that both channels may run at once.

### Key Entities

- **Pushover channel configuration**: The pair of local file paths — application token file and user key
  file — that together make the channel usable. Either both are present or neither is; a single one is a
  configuration error. Holds paths, never credential values.
- **Notification event**: Unchanged. Carries an event kind, a title, a detail string, and optional item
  identifier, repository key, and URL. It gains no new field, because no field a credential could reach may
  exist on it.
- **Delivery attempt**: One message handed to one channel, with an outcome. A single message — a notification
  event or the stale-heartbeat health alert — now produces one delivery attempt per configured channel, and
  each is recorded separately.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Pushover configured and a webhook deliberately absent, an author receives a push
  notification on their device for each selected event kind, within one daemon tick of the event occurring.
- **SC-002**: Enabling Pushover requires adding no more than two configuration keys and creating two local
  credential files; no existing key needs to change.
- **SC-003**: An installation configured only with a webhook produces identical notification behaviour before
  and after this feature, demonstrated by the existing notification tests passing unchanged.
- **SC-004**: Across a full run that includes a Pushover authentication failure and a Pushover timeout,
  neither credential appears anywhere in the audit log, asserted by an automated check.
- **SC-005**: Every one of the four misconfigurations — missing key, missing file, over-permissive file,
  inline credential — is refused at load time with a message that names the offending key, and none of them
  can reach a send attempt.
- **SC-006**: A notification channel that is unreachable adds no measurable delay to a dispatch or
  reconciliation and never causes one to fail, verified with an unreachable channel configured.
- **SC-007**: 100% of Pushover delivery attempts, successful or failed, are reconstructable from the audit
  log alone without re-running anything.
- **SC-008**: An installation configured with Pushover and no webhook receives every message the system is
  capable of sending — all four event kinds and the stale-heartbeat alert — with no message kind reachable
  only by webhook.

## Assumptions

- **"As well" means alongside, not instead.** Pushover is a second peer channel. Both may be configured
  simultaneously and both then receive every message. Neither replaces nor disables the other.
- **File paths only, no environment-variable alternative.** The issue asks specifically for credential
  *files*, and the constitution accepts local files as a secret source. Adding `*_env` twins would be two
  configuration knobs with no caller in hand, which Principle I forbids.
- **The existing four event kinds are the whole scope.** No new event kind, no new notification call site,
  and no change to when notifications fire. This feature changes only where a message goes.
- **No Pushover-specific presentation controls.** Priority, sound, device targeting, message expiry, and
  retry-until-acknowledged are out of scope: no present, concrete need names them, and each would be a knob
  with one caller. They can be added later if a need appears.
- **The generic webhook stays generic.** The webhook channel's URL and body shape are untouched, and an
  installation configured only with a webhook is unaffected. This feature adds a channel; it does not
  refactor the existing one beyond what running two channels requires.
- **Channel parity is the rule, decided 2026-09-01.** Both of the webhook's current consumers — the four
  notification event kinds and the stale-heartbeat health alert — fan out to every configured channel. A
  channel that could carry item failures but not "the daemon is dead" would be the wrong half. This is the
  one open question from the specification round, answered in favour of parity.
- **The per-cycle cap keeps its current meaning.** `max_per_cycle` bounds messages the author is told about,
  not HTTP requests made, so configuring a second channel does not silently halve the effective cap.
- **Credentials are validated to the same standard as the existing ones**: the file must exist and must not
  be readable by group or other. The Trello and GitHub credential rules are the precedent being followed.
- **Pushover's service is treated as an unreliable dependency.** No feature behaviour depends on it being
  up, and its outage is a logged non-event.
- **A new outbound HTTP dependency is not introduced.** The project already makes bounded HTTP requests; this
  feature is expected to reuse that capability rather than add a Pushover client library. Whether that reuse
  is literal is a plan-phase decision, not a spec-phase one.

## Dependencies

- A Pushover account, one registered Pushover application (which yields the API token), and the account's
  user key. Obtaining these is manual and outside the system.
- The existing notification machinery: the four call sites, the event kinds, the per-cycle bound, the effect
  levels, and the audit log. This feature extends that machinery and does not replace it.
- Outbound network access from the machine to Pushover's API. Absent it, the channel logs failures and
  nothing else breaks.

## Out of Scope

- Any change to which events exist, when they are emitted, or when the health alert is triggered.
- Gating the stale-heartbeat alert behind `[notifications] events`, or giving it its own event kind.
- Pushover priority, sound, device targeting, expiry, or retry-until-acknowledged options.
- Receiving anything *from* Pushover: acknowledgements, replies, receipts, or two-way control.
- A durable outbound queue that replays notifications missed while the process was dead.
- Any third notification channel, or a general-purpose channel plugin system.
- Migration tooling for existing configurations; none is needed, since no existing key changes.
