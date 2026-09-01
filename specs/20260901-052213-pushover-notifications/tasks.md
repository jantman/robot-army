---

description: "Task list for Pushover Notifications"
---

# Tasks: Pushover Notifications

**Input**: Design documents from `/specs/20260901-052213-pushover-notifications/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Test tasks are included and are **not optional**. The constitution's Development Workflow
section requires unit tests for every new or changed unit of behaviour, and additionally requires
failure-path tests for code parsing external input — which here is both the `[pushover]` config section
and the Pushover HTTP response. The same section states that test-first development is **not** mandatory,
so tests are listed alongside their implementation rather than ahead of it. Write them in whichever order
suits; the gate is that they exist, are meaningful, and pass.

**Organization**: Tasks are grouped by the four user stories in [spec.md](./spec.md) so each can be
implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story the task belongs to (US1..US4)
- Exact file paths are given in every task

## Path Conventions

Single Python package: `src/robot_army/` and `tests/` at the repository root. Boundaries live in
`src/robot_army/boundaries/`. This matches plan.md's Structure Decision; no new directory is created.

---

## Phase 1: Setup

**Purpose**: Establish the regression baseline this feature must not break. There is nothing to install —
no new runtime dependency is added (plan.md, Technical Context).

- [X] T001 Run `uv run pytest -q` and record the passing baseline; `tests/unit/test_notifications.py` and `tests/unit/test_health.py` are the two modules whose current assertions FR-016 and SC-003 require to keep passing unchanged

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The channel abstraction and the minimal config parsing that every user story below builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Behaviour must not change in this phase.** Every task here is either new-but-unreached code or a
refactor with identical output. T001's baseline must still be green at the checkpoint.

- [X] T002 [P] Add `PushoverConfig` frozen dataclass (`token_file: Path`, `user_key_file: Path`, `read_token()`, `read_user_key()`) to `src/robot_army/config.py` beside `TrelloConfig`; both readers strip trailing whitespace per data-model.md, and neither credential is retained on the instance
- [X] T003 Add `pushover: PushoverConfig | None` to the `Config` dataclass in `src/robot_army/config.py`, register `"pushover"` in `_SECTIONS`, `_STRICT_KEY_SECTIONS`, and `_KNOWN_KEYS` with `{"token_file", "user_key_file"}` (depends on T002)
- [X] T004 Add `_parse_pushover(section, problems) -> PushoverConfig | None` to `src/robot_army/config.py` handling only the happy path and the absent section (returns `None`); the four refusal rules are US3's work — wire it into `parse()` so `config.pushover` is populated (depends on T003)
- [X] T005 [P] Add `post_form(url, data, *, timeout=10.0) -> tuple[bool, str]` to `src/robot_army/health.py` beside `post_json`: form-encoded via `httpx.post(url, data=...)`, explicit timeout, no retry, same `(ok, message)` return shape — per contracts/channels.md and research.md R5
- [X] T006 Create `src/robot_army/channels.py` with the `Channel` protocol (`name`, `send(title, message, fields) -> tuple[bool, str]`), the shared `webhook_body(title, message, fields)` composer, and `WebhookChannel` wrapping `[health] webhook_url` and sending via `health.post_json` — per contracts/channels.md
- [X] T007 Add `build(config) -> tuple[Channel, ...]` to `src/robot_army/channels.py` returning the configured channels in stable order (webhook first) and `()` when none is configured, so no request is ever constructed for an unconfigured installation (depends on T004, T006)
- [X] T008 Establish the never-raises contract in `src/robot_army/channels.py`: every channel catches its own exceptions and returns `(False, reason)` rather than propagating, so a channel failure is never the caller's problem (FR-010) (depends on T006)
- [X] T009 Rewire `compose(event)` in `src/robot_army/boundaries/notifier.py` to call `channels.webhook_body` so the body the simulated notifier records and the body the real channel posts have one composer and cannot drift (depends on T006)
- [X] T010 [P] Add a `RecordingChannel` helper to `tests/conftest.py` alongside `RecordingNotifier`: captures `(title, message, fields)` per send and returns a configurable `(ok, message)`, so a test can assert what a channel was handed without a network
- [X] T011 Add unit tests to `tests/unit/test_notifications.py` pinning that `WebhookChannel` reproduces today's event body byte-for-byte, and to `tests/unit/test_health.py` that `build()` returns `()`, webhook-only, and pushover-only tuples for the four reachable configurations in contracts/config.md (depends on T007, T009)

**Checkpoint**: `uv run pytest -q` is still green with no assertion changed. `channels.py` exists and is
correct but nothing calls `build()` yet.

---

## Phase 3: User Story 1 - Be told on my phone when something happens (Priority: P1) 🎯 MVP

**Goal**: A Pushover-only installation receives all four notification event kinds as push notifications,
recorded in the audit log, and sends nothing below the `live` effect level.

**Independent Test**: Configure the two credential files and a non-empty `[notifications] events` with
**no** `[health] webhook_url` at all, run a cycle producing one of the chosen events, and confirm the
Pushover message is delivered and the send is recorded. Quickstart Scenarios 1, 3, and 4.

### Implementation for User Story 1

- [X] T012 [US1] Add `PushoverChannel` to `src/robot_army/channels.py`: `name = "pushover"`, form POST to `https://api.pushover.net/1/messages.json` via `health.post_form` with `token`, `user`, `title`, `message`, and `url` only when `fields["url"]` is present and non-empty (depends on T005, T006, T008)
- [X] T013 [US1] Read both credentials at send time in `PushoverChannel.send` in `src/robot_army/channels.py` via `PushoverConfig.read_token()` / `read_user_key()`, never at construction and never retained on the instance (FR-003) (depends on T002, T012)
- [X] T014 [US1] Truncate `title` to 250 and `message` to 1024 characters in `PushoverChannel.send` in `src/robot_army/channels.py` before sending, since Pushover rejects rather than truncates (research.md R4); the untruncated text stays in the audit record
- [X] T015 [US1] Record only the HTTP status and our own message on failure in `src/robot_army/channels.py` — never the Pushover response body, per contracts/channels.md and plan.md's Principle III section
- [X] T016 [US1] Register `PushoverChannel` in `channels.build()` in `src/robot_army/channels.py`, built when `config.pushover is not None` (depends on T007, T012)
- [X] T017 [US1] Replace `WebhookNotifier` with `MultiNotifier` in `src/robot_army/boundaries/notifier.py`: holds the channel tuple, adds `event_fields(event)` returning `{"kind", "item_id", "repo_key", "url"}`, iterates channels, returns `True` if any accepted and `False` with zero channels (depends on T009, T016)
- [X] T018 [US1] Emit one `notify.channel` audit record per delivery from `MultiNotifier.send` in `src/robot_army/boundaries/notifier.py` carrying channel name, kind, item id, outcome, and the reason on failure (FR-011); `notify.failed` is removed with `WebhookNotifier` rather than kept as a second way to say the same thing (depends on T017)
- [X] T019 [US1] Record the configured channel **names** in `SimulatedNotifier`'s record in `src/robot_army/boundaries/notifier.py` — one record for the one message, not one per channel, since below `live` there are no deliveries to record (research.md R9)
- [X] T020 [US1] Update `wire()` in `src/robot_army/effects.py` to build `channels.build(config)` and pass it to `MultiNotifier` at `live` or to `SimulatedNotifier` below it; `REAL_AT["notifier"]` is unchanged (depends on T017, T019)
- [X] T021 [US1] Add a three-line optional `describe_name()` lookup to `Boundaries.describe()` in `src/robot_army/effects.py` and implement it on both notifiers so the startup record reads `MultiNotifier(webhook, pushover)` rather than losing which channels are live (depends on T020)
- [X] T022 [US1] Reword the load warning in `src/robot_army/config.py` from "events are configured but `[health] webhook_url` is empty" to the channel-neutral form in contracts/config.md — webhook *or* Pushover satisfies it (FR-015); it stays a warning, not an error (depends on T004)

### Tests for User Story 1

- [X] T023 [P] [US1] Create `tests/unit/test_pushover.py` covering `PushoverChannel`'s form composition: exact parameter names, `url` present only when the field is, and the endpoint — with `httpx.post` monkeypatched as `tests/unit/test_health.py` already does
- [X] T024 [P] [US1] Add truncation tests to `tests/unit/test_pushover.py`: a 2000-character message and a 400-character title are sent truncated to 1024 and 250, and the audit record keeps the untruncated text
- [X] T025 [P] [US1] Add failure-path tests to `tests/unit/test_pushover.py`: a transport error and a 4xx each return `(False, reason)`, neither raises, and neither puts the response body in the record
- [X] T026 [US1] Add tests to `tests/unit/test_notifications.py` for a Pushover-only installation: each of the four kinds is delivered, and `events = []` produces **no** request at all rather than one built and skipped (US1 AS1, AS2)
- [X] T027 [US1] Add a test to `tests/unit/test_notifications.py` that below `live` nothing reaches Pushover and the simulated record carries the full composed body plus the configured channel names (US1 AS3)
- [X] T028 [P] [US1] Add tests to `tests/unit/test_effects.py` for `wire()` selecting `MultiNotifier` at `live` and `SimulatedNotifier` below it, and for `describe()` naming the configured channels
- [X] T029 [P] [US1] Add a test to `tests/unit/test_config.py` that a non-empty `events` with neither channel configured warns rather than errors, and that either channel alone clears the warning (US1 AS4)

**Checkpoint**: A Pushover-only installation is fully functional. This is the MVP — it delivers the whole
of issue #106's request.

---

## Phase 4: User Story 2 - Keep the webhook working, and run both at once (Priority: P2)

**Goal**: Both channels configured means both receive every message, with independent outcomes and no
coupling between their failures — and an existing webhook-only installation is untouched.

**Independent Test**: Configure both, emit one event, and confirm two deliveries and two per-channel
records for the one message. Then break one channel and confirm the other still delivers. Quickstart
Scenarios 5 and 6.

**Note on scope**: this story is deliberately test-heavy. The fan-out loop lands in T017 and the per-cycle
counter already counts events (`notifications.py:143`), so most of US2's value is a *guarantee* — and a
guarantee that is not pinned by a test is not a guarantee. Two small code tasks remain.

### Implementation for User Story 2

- [X] T030 [US2] Confirm and, if needed, correct `MultiNotifier.send` in `src/robot_army/boundaries/notifier.py` so one channel's failure never short-circuits the loop: every channel is attempted, every outcome recorded, and the return is "any succeeded" (FR-009, FR-010) (depends on T017)
- [X] T031 [US2] Verify in `src/robot_army/notifications.py` that `_CYCLE["sent"]` still increments once per **event** and not per delivery, and add the comment saying why — the cap bounds a burst of news, not of packets, so two channels must not halve it (FR-013, research.md R7)

### Tests for User Story 2

- [X] T032 [US2] Add a test to `tests/unit/test_notifications.py` that one event with both channels configured produces exactly one `notify.send` record and two `notify.channel` records with independent outcomes (US2 AS1)
- [X] T033 [US2] Add a test to `tests/unit/test_notifications.py` that with Pushover failing, the webhook still delivers, the Pushover failure is recorded, and `emit()` still returns without raising, delaying, or retrying (US2 AS2)
- [X] T034 [US2] Add a test to `tests/unit/test_notifications.py` that both channels failing produces two independent failure records and still does not fail the caller (spec Edge Cases)
- [X] T035 [US2] Add a test to `tests/unit/test_notifications.py` that a burst past `max_per_cycle` suppresses on message count with two channels configured, and that the single suppression summary reaches both (US2 AS4)
- [X] T036 [US2] Confirm `tests/unit/test_notifications.py`'s pre-existing webhook-only assertions pass **unchanged** — this is the FR-016 / SC-003 regression gate, and an assertion edited to accommodate the new code is the failure this task exists to catch (US2 AS3)

**Checkpoint**: Both channels work together and the existing installation is provably untouched.

---

## Phase 5: User Story 3 - Configure the credentials safely, and be told when I got it wrong (Priority: P2)

**Goal**: Every misconfiguration of the two credential keys is refused at load with a message naming the
offending key, and no credential can reach the log.

**Independent Test**: Run `robot-army doctor` against the four broken configurations in contracts/config.md
and confirm each is refused by name. Quickstart Scenarios 2 and 8.

### Implementation for User Story 3

- [X] T037 [US3] Extract the exists-and-mode-0600 half of `_trello_credential` into a shared `_secret_file(section_name, key, raw, problems)` in `src/robot_army/config.py` and switch `[trello]` to it, so the new section gains a caller rather than a copy (research.md R6)
- [X] T038 [US3] Add the both-or-neither rule to `_parse_pushover` in `src/robot_army/config.py`: one key alone is an **error** naming the missing key, not a warning, because a half-configured channel silently never fires (FR-004) (depends on T004)
- [X] T039 [US3] Apply `_secret_file` to both `[pushover]` keys in `src/robot_army/config.py` for the exists and mode-0600 rules, each message naming the key, the path, and the found mode (FR-005) (depends on T037, T038)
- [X] T040 [US3] Add a Pushover-only literal-credential pattern (`^[A-Za-z0-9]{30}$`) to `src/robot_army/config.py`, consulted **only** when scanning `[pushover]` and deliberately not added to the shared `_TOKEN_PATTERNS` — widening the shared tuple would create an error a `[github]` or `[trello]` author could not clear (research.md R6)
- [X] T041 [US3] Scan `[pushover]` values for a literal credential in `src/robot_army/config.py` using both `_looks_like_token` and the new pattern, erroring with the message in contracts/config.md (FR-006) (depends on T040)

### Tests for User Story 3

- [X] T042 [P] [US3] Add tests to `tests/unit/test_config.py` for the four refusals — missing key, missing file, mode 0644, inline literal credential — each asserting the offending key is named and that loading fails rather than warns (US3 AS2-AS4, SC-005)
- [X] T043 [P] [US3] Add a test to `tests/unit/test_config.py` that a valid section loads and that neither credential value appears in `repr(config)` (US3 AS1, FR-003)
- [X] T044 [P] [US3] Add a test to `tests/unit/test_config.py` that an unknown key in `[pushover]` is an error, since the section is in `_STRICT_KEY_SECTIONS`
- [X] T045 [US3] Add the credential-containment assertion to `tests/unit/test_pushover.py`: across a run including an authentication failure, neither credential appears in any audit record or any error string — the case where a token would otherwise ride along inside an error rather than in a field anyone chose to add (FR-007, SC-004, US3 AS5)
- [X] T046 [P] [US3] Add a test to `tests/unit/test_config.py` that a credential file deleted after load produces a recorded delivery failure naming the **path**, never the contents (spec Edge Cases)

**Checkpoint**: Every documented misconfiguration is refused by name, and no credential can reach a log.

---

## Phase 6: User Story 4 - Know when the daemon has gone quiet (Priority: P2)

**Goal**: The stale-heartbeat alert reaches every configured channel, so a Pushover-only installation is
told the daemon died — and it keeps being told regardless of the effect level.

**Independent Test**: With Pushover configured and no webhook, let the heartbeat go stale, run
`robot-army health --notify`, and confirm the alert arrives and is recorded. Quickstart Scenario 7.

### Implementation for User Story 4

- [X] T047 [US4] Add the pure composer `alert_fields(report) -> tuple[str, str, dict]` to `src/robot_army/health.py` returning the title, `report.reason`, and `{"healthy", "age_seconds"}` — the exact body `notify` builds today (data-model.md)
- [X] T048 [US4] Remove `health.notify` from `src/robot_army/health.py`; it was this composer welded to a single transport, and Principle V permits the outright replacement rather than an alias (depends on T047)
- [X] T049 [US4] Rewrite the notify branch of `health_check` in `src/robot_army/operations.py` to iterate `channels.build(ctx.config)`, sending `alert_fields(report)` to each (depends on T007, T047, T048)
- [X] T050 [US4] Emit one `health.notify` audit record **per channel** in `src/robot_army/operations.py`, each carrying `channel`, `reason`, the returned message, and the outcome (depends on T049)
- [X] T051 [US4] Report one output line per channel from `health_check` in `src/robot_army/operations.py`, and with zero channels report that nothing was sent without erroring — the one user-visible string this feature changes, and only in the nothing-configured case (FR-019) (depends on T049)

### Tests for User Story 4

- [X] T052 [US4] Update the two existing tests in `tests/unit/test_health.py` that call the removed `health.notify` — `test_notify_without_a_webhook_reports_that_rather_than_pretending` and `test_notify_posts_a_plain_json_body` — to go through the channel path, keeping their assertions about the body intact (depends on T048)
- [X] T053 [US4] Add a test to `tests/unit/test_health.py` that with Pushover configured and no webhook, a stale heartbeat delivers the alert to Pushover and records it (US4 AS1)
- [X] T054 [US4] Add a test to `tests/unit/test_health.py` that with both channels configured the alert reaches both and each outcome is recorded separately (US4 AS2)
- [X] T055 [US4] Add a test to `tests/unit/test_health.py` **pinning the effect-level exception**: with `[daemon] effect_level = "local"`, the alert is still sent for real on every channel. This is the task that stops a future reader from "fixing" the inconsistency — `health --notify` has no `--effect-level` flag and inherits `[daemon] effect_level`, so gating it would silently disable the dead-man's switch (US4 AS5, research.md R2)
- [X] T056 [P] [US4] Add a test to `tests/unit/test_health.py` that with no channel configured the command exits 4 and reports nothing was sent, without erroring (US4 AS4)

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: The documentation that makes the feature findable, and the correction of the false claim that
caused issue #106 in the first place.

- [X] T057 [P] Correct the false Pushover claim in `health.post_json`'s docstring in `src/robot_army/health.py` — ntfy accepts a JSON body, Pushover does not — and say what the module now does instead (research.md R1)
- [X] T058 [P] Correct the same claim in the docstring of `test_notify_posts_a_plain_json_body` in `tests/unit/test_health.py`
- [X] T059 [P] Correct R14's Pushover claim in `specs/004-concurrency-polish/contracts/notifications.md`, noting the instinct survived and only the factual premise was wrong
- [X] T060 [P] Add the `notify.channel` row to the audit-action table in `docs/logging.md` and, while in there, the `health.notify` row that has been missing since it was written
- [X] T061 [P] Rewrite the "Being told when something happens" section of `README.md`: the `[pushover]` keys, where to get the two credentials, that both channels may run at once, that the cap counts messages rather than deliveries, and that the health alert reaches every channel at any effect level
- [ ] T062 Run every scenario in `specs/20260901-052213-pushover-notifications/quickstart.md` against a real configuration, including the deliberate misconfigurations and the effect-level check in Scenario 7
  - **Partially done.** Scenarios 1 and 2 (the valid section, the four refusals, credential containment, `build()` channel selection) were run through the real TOML entry point and behave as specified. Scenarios 4, 5, 7 and 8 need a real Pushover application token, a real user key, and a phone to receive on — they are the author's to run. Their automated equivalents pass in `tests/unit/test_pushover.py`, `tests/unit/test_notifications.py` and `tests/unit/test_health.py`, which is a floor, not a substitute: only a real send proves the credentials and the endpoint agree.
- [X] T063 Run `uv run pytest -q` and confirm the full suite passes; implementation is not complete until it does (Constitution, Development Workflow)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational. No dependency on US2-US4
- **US2 (Phase 4)**: depends on Foundational **and on T017/T018 from US1** — the fan-out loop it validates is built there. This is the one genuine cross-story dependency, and it exists because US2's value is a guarantee about code US1 introduces
- **US3 (Phase 5)**: depends on Foundational only. Entirely `config.py` plus `test_config.py`, apart from T045
- **US4 (Phase 6)**: depends on Foundational only. Touches `health.py` and `operations.py`, which no other story touches
- **Polish (Phase 7)**: depends on the stories you intend to ship

### Within Each User Story

- Models and value objects before the services that use them
- `channels.py` before its callers
- Tests alongside implementation, not necessarily before it (the constitution does not mandate test-first)
- Each story is complete and its checkpoint green before moving to the next

### Parallel Opportunities

- **Phase 2**: T002 and T005 and T010 are three different files — fully parallel. T006 can start as soon as T005 lands
- **Phase 3**: the five test tasks T023, T024, T025, T028, T029 touch four different files and are parallel once their implementation lands; T023-T025 are the same new file and are parallel with each other only in the sense of being independently authorable
- **Phase 5**: T042, T043, T044, T046 are all `test_config.py` additions — independent assertions, parallel to author, one file to merge
- **US3 and US4 are fully parallel with each other**: `config.py` and `health.py`/`operations.py` do not overlap
- **Phase 7**: T057-T061 are five different files and are entirely parallel

### Parallel Example: US3 and US4 together

```bash
# After Foundational and US1 are complete, these two stories share no file:
Task: "T037-T046 — [pushover] validation in src/robot_army/config.py and tests/unit/test_config.py"
Task: "T047-T056 — the health alert in src/robot_army/health.py and src/robot_army/operations.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup — record the green baseline
2. Phase 2: Foundational — **blocks everything**; the checkpoint is "still green, nothing behaves differently"
3. Phase 3: User Story 1
4. **STOP and VALIDATE**: quickstart Scenarios 1, 3, 4. A Pushover-only installation now receives all four
   event kinds — which is the whole of issue #106 as written

### Incremental Delivery

1. Setup + Foundational → the channel abstraction exists and changes nothing
2. **+ US1 → the MVP.** Pushover works
3. **+ US2 →** both channels at once, proven not to interfere
4. **+ US3 →** every misconfiguration refused by name
5. **+ US4 →** the dead-man's switch reaches the phone
6. **+ Polish →** the docs, and the false claim that caused this issue is gone from all three places

US3 and US4 can swap order or run in parallel; neither touches the other's files.

---

## Notes

- **The riskiest task is T055.** It pins a deliberate inconsistency — the health alert ignores the effect
  level — that a future reader will otherwise be tempted to "fix", silently disabling the dead-man's
  switch for anyone running below `live`. Its docstring should say why, not just what.
- **T036 is a gate, not a chore.** If a pre-existing webhook-only assertion needs editing to accommodate
  the new code, FR-016 has been broken and the design has drifted. Editing the assertion is the failure
  mode it exists to catch.
- **T057-T059 look skippable and are not.** A false claim left in a docstring is how the next person
  re-derives the wrong answer; it is why this issue took as long to surface as it did.
- No database migration, no schema change, no new runtime dependency, no new command, no new effect level,
  and no change to when any notification is emitted.
- `[P]` means different files with no incomplete dependency. Commit after each task or logical group.
