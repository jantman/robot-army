# Implementation Plan: Pushover Notifications

**Branch**: `20260901-052213-pushover-notifications` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/20260901-052213-pushover-notifications/spec.md`

## Summary

robot-army's notification channel is a generic JSON webhook, and the reason it is generic is written
into `health.post_json`'s docstring: *"A generic webhook covers ntfy and Pushover — both named in the
planning document — without either becoming a dependency."* **That claim is wrong about Pushover**, which
takes form-encoded parameters and not an arbitrary JSON body ([R1](./research.md)). This feature corrects
it, and issue #106 exists because of it.

Four moves:

1. **One new module, `channels.py`, owning *where a message goes*.** A `Channel` protocol with a single
   method, two implementations — `WebhookChannel` and `PushoverChannel` — and a `build(config)` that
   returns the configured ones in a stable order. `fields` is a plain dict each channel reads what it
   understands from, which reproduces both of today's wire bodies exactly (R3).

2. **The fan-out has two callers because there have always been two senders.** The four notification
   event kinds go through the `notifier` boundary; the stale-heartbeat alert does not and never did —
   `operations.health_check` calls `health.notify` directly, touching no boundary (R2). Both now iterate
   the same channel tuple.

3. **A `[pushover]` section with two file paths**, absent by default, validated at load the way the
   existing credential files are: both-or-neither, exists, mode 0600, and no literal credential inline.
   The existing literal-credential guard cannot recognise a Pushover key at all, so it gains a pattern
   that is consulted only inside `[pushover]` (R6).

4. **One new audit action, `notify.channel`**, because a message and a delivery are now different things
   and Principle III needs both on the record (R8).

**The decision that shapes everything else**: the health alert stays outside the effect-level system, for
both channels. `robot-army health --notify` has no `--effect-level` flag and resolves its level from
`[daemon] effect_level`, so gating it would silently disable the dead-man's switch for anyone running
their daemon at `local`. The spec's original acceptance scenario assumed the opposite; **the spec was
corrected, not the code bent to fit it** (R2).

**No schema change, no migration, no new runtime dependency, no new command, no new boundary, no new
effect level, no change to the state machines, no change to when anything is emitted, and no change to
either webhook body.** Everything above was measured against this checkout; see [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.14 (`requires-python = ">=3.14"`).

**Primary Dependencies**: none added. `httpx` stays the sole runtime dependency (`pyproject.toml:13`);
`httpx.post(url, data=...)` form-encodes natively, so Pushover needs no client library (R4).

**Storage**: SQLite and the JSONL audit log — **both unchanged**. `SCHEMA_VERSION` does not move; no
table is read or written by this feature. The only new persistent state is two files the author creates
by hand, holding one credential each, which the process reads and never writes. See
[data-model.md](./data-model.md).

**Testing**: pytest. A new unit module `tests/unit/test_pushover.py` for the channel's own rules —
form composition, truncation, credential containment, transport and 4xx failure — with `httpx.post`
monkeypatched, as `tests/unit/test_health.py:173` already does. Cases added to `tests/unit/test_config.py`
(the four misconfigurations), `tests/unit/test_notifications.py` (fan-out, per-channel records, the cap),
`tests/unit/test_health.py` (the alert on each channel), and `tests/unit/test_effects.py` (wiring and the
startup record). No network, no `requires_git` marker, no worktree.

**Target Platform**: a single Linux machine with a shell — and a phone that is not on it, which is the
whole point of the feature.

**Project Type**: single Python package (`src/robot_army`) — CLI plus daemon plus a small web interface.

**Performance Goals**: none. At most one additional bounded HTTP POST per message, on paths that already
make one.

**Constraints**: every network call carries an explicit timeout (10s, matching `post_json`) and does not
retry (Principle IV, R5). A dead channel must add no measurable delay to a dispatch or a reconciliation
pass, and must never hold a database transaction open — the four call sites are already outside their
transactions and do not move (milestone 004 R14).

**Scale/Scope**: one user, two channels, at most `max_per_cycle` messages per daemon tick.

## Constitution Check

*GATE: evaluated before Phase 0, re-evaluated after Phase 1 design. Both passes below.*

### I. Simplicity First (YAGNI & KISS) — **PASS**

- **No new dependency.** `httpx` already present; Pushover's API is one form POST (R4). The alternative —
  a `python-pushover` style client — would add a dependency to save eight lines.
- **No new knobs beyond the two the issue asks for.** Priority, sound, device targeting, expiry, and
  retry-until-acknowledged are all explicitly out of scope. Each would be a configuration knob with
  exactly one caller and no second use in hand.
- **No `*_env` twins** for the two credential keys. `[github]` and `[trello]` have both forms because both
  were asked for; nothing asks here (R6).
- **The `Channel` protocol has two implementations and two callers.** This is the test Principle I sets —
  it forbids the abstraction with one caller and no second use in hand. Rejected alternatives are recorded
  in R3, including the two-boundary-fields shape that is otherwise idiomatic in this codebase.
- **One refactor that removes code rather than adding it**: `_trello_credential`'s exists-and-mode half
  becomes a shared `_secret_file`, so the new section gains a caller instead of a copy (R6).
- **One deletion**: `notify.failed` goes away, superseded by `notify.channel`. Two audit actions saying
  the same thing is worse than one.

### II. Single-User, Local-First — **PASS**

- Both credentials are read from local, mode-0600 files whose paths are configured — precisely the form
  the principle names. Neither is committed, neither is logged, neither is retained in the loaded
  `Config` object; each is read at the moment it is needed (FR-003), as `GitHubConfig.read_token` already
  does (`config.py:150`).
- No inbound exposure, no port, no listener. Pushover is outbound-only; receiving anything *from*
  Pushover is out of scope.
- Core function does not depend on it: with no `[pushover]` section nothing is built, nothing is sent,
  and no request is constructed.

### III. Total Accountability — **PASS, with one pre-existing gap restated and not widened**

- **What this logs**: every delivery attempt, on every channel, in both directions of outcome.
  `notify.channel` records the channel name, the event kind, the item, whether it succeeded, and on
  failure the reason. `health.notify` gains a `channel` field and becomes one record per channel.
  `notify.send` continues to record the message. `notify.suppressed` is untouched. Below `live`, the
  simulated notifier records the composed body *and* the channel names that were configured (R9), so the
  simulated path does not quietly say less than the real one.
- **Secrets**: neither credential can reach a record. The `NotificationEvent` gains no field (FR-007); the
  credentials travel in the form body, not the URL, so the error strings that interpolate a URL are safe
  by construction rather than by a rule (R4); and the upstream response body is never recorded, because
  recording an upstream body verbatim is how a credential leaks the day the upstream starts echoing it.
- **Reconstruction**: from the log alone, a reader can answer which message went to which channel, when,
  and with what result — including the case where one channel succeeded and the other failed.
- **Accepted gap, unchanged**: a notification never attempted because the process died between the state
  transition and the send is not recorded as missed. The transition itself is fully logged. This gap was
  named and justified in milestone 004's plan as the accepted cost of refusing a durable outbound queue;
  a second channel does not change that argument, and this feature does not widen the gap.
- **New, smaller instance of the same gap**: if the process dies *between* the two channel sends, the log
  shows one delivery and no second record. This is the same accepted gap at finer grain — the alternative
  is a durable outbound queue, which Principle I and milestone 004 both refuse.
- **Documentation**: `docs/logging.md:227` gains `notify.channel` and, while we are in there, the
  `health.notify` row that has been missing since it was written.

### IV. Interruption Tolerance — **PASS**

- **What happens if it is killed halfway through**: nothing is left inconsistent. No file is written, no
  row is touched, no transaction is open. A half-completed fan-out means one channel got the message and
  the other did not, and the log says exactly that up to the moment of death. There is nothing to recover
  and nothing to replay — deliberately, per the accepted gap above.
- Every new network call sets an explicit 10s timeout and does not retry (R5). No unbounded loop, no
  indefinite block.
- A channel failure never fails, delays, or retries the operation that triggered it (FR-010), and never
  prevents the other channel's delivery. Each channel's exceptions are caught at the channel and returned
  as `(False, reason)`; `notifications._deliver`'s existing outer guard stays as the backstop.
- Sends remain outside every transaction. The four call sites do not move.

### V. Public Code, Unsupported Project — **PASS**

- Nothing committed carries a credential; the config file holds paths, and a literal credential in it is
  a load error, not a warning.
- No backward-compatibility burden is accepted: `health.notify` and `notify.failed` are replaced outright
  rather than kept as aliases. Breaking changes are permitted when they serve the single user.
- Documentation is for the author's future self: README gains the two keys, where to get the two
  credentials, and the fact that both channels can run at once.

### Development Workflow — **PASS**

Unit tests ship with every new or changed unit of behaviour, and the code parsing external input — the
config section and the Pushover response — carries failure-path tests, not only success-path ones, as the
workflow section requires.

**Gate result: no violations. The Complexity Tracking table is therefore empty and has been removed.**

**Post-Phase-1 re-check**: the design in [data-model.md](./data-model.md) and
[contracts/](./contracts/) adds one module, one protocol with two implementations, one config section,
one audit action, and one three-line hook in `describe()`. It deletes one class, one function, and one
audit action. Nothing in the design changed the gate result; the `Channel` protocol was the only item that
needed Principle I argued rather than asserted, and R3 records the alternatives and why each was rejected.

## Project Structure

### Documentation (this feature)

```text
specs/20260901-052213-pushover-notifications/
├── plan.md              # This file
├── research.md          # Phase 0 — R1..R10, measured against the checkout
├── data-model.md        # Phase 1 — config, message, channel, records
├── quickstart.md        # Phase 1 — how to prove it works, end to end
├── contracts/
│   ├── channels.md      # The Channel protocol, both implementations, the fan-out
│   ├── config.md        # The [pushover] section and its validation rules
│   └── notifications.md # What changes in the milestone-004 contract, and what does not
├── checklists/
│   └── requirements.md  # Spec quality checklist (from /speckit-specify)
├── spec.md
└── tasks.md             # Phase 2 — created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
src/robot_army/
├── channels.py              # NEW — the Channel protocol, both channels, build(config)
├── health.py                # post_form added; post_json docstring corrected;
│                            #   notify() replaced by the pure alert_fields(report)
├── config.py                # [pushover] section, PushoverConfig, _secret_file extraction,
│                            #   the Pushover-only literal-credential pattern, reworded warning
├── effects.py               # wire() builds channels; describe() gains describe_name()
├── operations.py            # health_check fans out, one audit record per channel
└── boundaries/
    └── notifier.py          # MultiNotifier replaces WebhookNotifier;
                             #   SimulatedNotifier names its channels

tests/
├── conftest.py              # RecordingChannel helper
└── unit/
    ├── test_pushover.py     # NEW — the channel's own rules
    ├── test_notifications.py# fan-out, per-channel records, the cap counts messages
    ├── test_health.py       # the alert on each channel; the two corrected tests
    ├── test_config.py       # the four misconfigurations
    └── test_effects.py      # wiring and the startup record

docs/logging.md              # notify.channel, and the missing health.notify row
README.md                    # "Being told when something happens" — rewritten
specs/004-concurrency-polish/contracts/notifications.md  # R14's Pushover claim corrected
```

**Structure Decision**: unchanged. This is a single Python package with a flat module layout under
`src/robot_army/`, boundaries in `src/robot_army/boundaries/`, and tests split unit/integration. The one
new module sits at the package root beside `health.py` and `notifications.py` because it is not a boundary
— it is the thing two different senders share. No new directory, no new package, no new test category.

## Notes for `/speckit-tasks`

Suggested slicing, matching the spec's four user stories and their priorities:

1. **US1 (P1)** — `channels.py`, `PushoverChannel`, `post_form`, `[pushover]` config with full validation,
   `MultiNotifier`, `wire`. This is the milestone: with it alone, a Pushover-only installation receives
   all four event kinds.
2. **US2 (P2)** — the fan-out properties: both channels at once, independent outcomes, one channel's
   failure not touching the other, and the cap still counting messages. Mostly tests plus the
   `notify.channel` record.
3. **US3 (P2)** — the four load-time refusals and the credential-containment assertion. Splits cleanly
   because it is entirely `config.py` plus `test_config.py`.
4. **US4 (P2)** — `alert_fields`, `health_check`'s fan-out, the per-channel `health.notify` records, and
   the deliberate effect-level exception with a test that pins it.

Then documentation: README, `docs/logging.md`, and the three docstrings and one contract note carrying
the false Pushover claim (R1) — that last one is small, easy to skip, and the whole reason this issue
took as long to surface as it did.
