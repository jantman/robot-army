---
description: "Task list for binding a stored poll ETag to the request that produced it"
---

# Tasks: A Stored ETag Is Only Replayed Against the Request That Produced It

**Input**: Design documents from `specs/20260910-053539-etag-bound-to-query/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/poll-request.md

**Tests**: required — the constitution requires unit tests for every changed unit of behaviour,
and failure/interruption tests for persistence and for code parsing external input.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 (label change is discovered), US2 (upgraded rows heal)

---

## Phase 1: Setup

No setup: no dependency, no new module, no configuration key.

---

## Phase 2: Foundational (blocking)

**Purpose**: the column, the model field and the protocol shape both stories stand on.

- [ ] T001 Add `SCHEMA_015_SQL` (`ALTER TABLE poll_state ADD COLUMN etag_request TEXT`) with a comment in the house style explaining the column, that NULL means *never recorded*, and why there is no backfill (research R4); add `_migration_015` and append it to `MIGRATIONS` in `src/robot_army/migrations.py`
- [ ] T002 [P] Add `etag_request: str | None = None` to `PollState` in `src/robot_army/models.py`, with a docstring line saying the ETag and its request are one fact
- [ ] T003 Write `etag_request` in `save_poll_state` (insert column list, values, and `ON CONFLICT … SET`) in `src/robot_army/db.py`
- [ ] T004 Change `IssueSourceReader.poll` to `poll(self, repo_key, etag, *, etag_request)` and add `request: str` to `PollResult`, documenting what `etag` and `request` mean for 304 / 200 / 200-without-header (data-model.md), in `src/robot_army/boundaries/__init__.py`
- [ ] T005 Update `FakeIssueReader.poll` to the new signature in `tests/conftest.py`: give it a `request` attribute (default a fixed line) that it returns as `PollResult.request`, and have it answer 304 only when the sent ETag equals `self.etag` **and** `etag_request == self.request` — mirroring the real boundary, so poll-level tests exercise the comparison — and record `(repo_key, etag, etag_request)` in `poll_calls`
- [ ] T006 [P] Migration tests in `tests/unit/test_migrations.py`: a v14 database with a populated `poll_state` row migrates to 15 with every value intact and `etag_request` NULL; a migration killed mid-015 (the existing `_explode` pattern) leaves `user_version` 14 and no `etag_request` column, then re-runs to `SCHEMA_VERSION`

**Checkpoint**: the schema holds the pair; the suite still imports.

---

## Phase 3: User Story 1 — Changing the label does not blind discovery (P1) 🎯 MVP

**Goal**: an ETag is sent only against the exact request that produced it; a label change is
discovered on the next poll.

**Independent Test**: poll, change `[github] label`, poll again — the second request carries no
`If-None-Match` and the new label's issues are discovered; the third is conditional again.

### Implementation

- [ ] T007 [US1] In `src/robot_army/boundaries/github.py` add a private `_issues_request(repo_key) -> tuple[str, dict]` returning the path and params that `poll` sends, and a function building the request line `path + "?" + urlencode(sorted(params.items()))` (contracts/poll-request.md); `poll` must send exactly the path and params the line was built from (FR-004)
- [ ] T008 [US1] In `GitHubReader.poll` (`src/robot_army/boundaries/github.py`): send `If-None-Match` only when `etag` is not None and `etag_request == line`; otherwise send none and classify the discard as `request_changed` / `request_unrecorded`; return `PollResult(request=line, …)`; on a 200 take the ETag from the response header only, never carrying the old one forward (research R5); on a 304 return the sent ETag (or None if none was sent); rewrite the docstring to say *why* a 304 is only healthy against the same request
- [ ] T009 [US1] Extend the `github.poll` audit detail in `src/robot_army/boundaries/github.py` with `etag_sent`, `etag_discarded`, and — only when discarded — `request` and (for `request_changed`) `etag_request`, per contracts/poll-request.md
- [ ] T010 [US1] In `poll.poll_repo` (`src/robot_army/poll.py`): pass `etag_request=state.etag_request`; save `etag=result.etag, etag_request=result.request` on success; on `TransportError` save `etag=state.etag, etag_request=state.etag_request` so the pair survives a failure (FR-006); update the module docstring's ETag paragraph to state the precondition that makes 304 healthy and point at issue #60

### Tests

- [ ] T011 [P] [US1] Update every `reader.poll(...)` call in `tests/unit/test_github.py` to the new signature, and add boundary tests: matching line sends `If-None-Match` and a 304 is healthy; a line differing only in `labels` sends none and records `etag_discarded == "request_changed"` with both lines; `etag_request=None` with an ETag sends none and records `request_unrecorded`; no stored ETag records `etag_sent: false, etag_discarded: null`; a 200 without an `ETag` header returns `etag is None`; the returned `request` equals the request actually sent (parsed from the `httpx.Request`) with sorted params; the line contains no token / `Authorization` value
- [ ] T012 [P] [US1] In `tests/unit/test_poll.py`: update `test_the_etag_is_persisted_and_replayed` to assert the pair is persisted and replayed; add an end-to-end label-change test driving `poll.poll_repo` with a real `GitHubReader` over `httpx.MockTransport` whose handler returns 304 for **any** `If-None-Match` (reproducing what GitHub was observed doing) and otherwise lists issues filtered by the `labels` param — poll under label A, `dataclasses.replace` the config to label B, poll again, assert the label-B issue became a work item and the second request had no `If-None-Match`, then a third poll is conditional; add a test that a transport failure leaves `(etag, etag_request)` unchanged
- [ ] T013 [P] [US1] Update any other test that constructs `PollResult` or calls `issue_reader.poll` directly (grep `tests/` for `PollResult(` and `.poll(`) to the new shape

**Checkpoint**: US1 is independently demonstrable.

---

## Phase 4: User Story 2 — The machine that already hit this heals on upgrade (P2)

**Goal**: a row with an ETag and no recorded request costs exactly one unconditional request.

**Independent Test**: seed `poll_state` with `etag` set and `etag_request` NULL, poll, and see an
unconditional request and a row holding the new pair.

- [ ] T014 [US2] In `tests/unit/test_poll.py` add a test seeding `PollState(repo_key="demo", etag='W/"old"')` (no request), polling, and asserting the reader was called with that ETag but `etag_request=None`, the fake answered 200 (no match), and the row afterwards holds the new ETag and a non-NULL `etag_request`; add a boundary-level assertion in `tests/unit/test_github.py` (covered by T011's `request_unrecorded` case — confirm, do not duplicate)
- [ ] T015 [US2] Confirm the Trello board poll writes `etag_request` NULL and is otherwise unchanged: extend the existing assertion in `tests/unit/test_intake_poll.py` (`state.etag is None`) with `state.etag_request is None`

**Checkpoint**: both stories pass independently.

---

## Phase 5: Polish & cross-cutting

- [ ] T016 [P] `docs/guide/state.md`: add a `### poll_state.etag_request — which request an ETag answered` section (what it holds, the replay rule, why NULL means never recorded and why there is no backfill, a `sqlite3` query to inspect it); adjust the later sentence describing `poll_state`'s columns as "fixed (`etag`, `last_status`, `backoff`)" so it stays true
- [ ] T017 [P] `docs/guide/audit-log.md`: add `## The issue #60 record` documenting the new `github.poll` detail keys and the worked example from contracts/poll-request.md
- [ ] T018 [P] `docs/guide/2-intake.md`: under "The label is the gate", one short paragraph saying changing `[github] label` costs one full listing per repository on the next poll and needs no manual step, and why (the stored ETag belongs to the old query)
- [ ] T019 Run `uv run pytest` and confirm the whole suite passes; run `uv run robot-army example-config --output /dev/stdout` is **not** needed (no config key changed) — confirm `tests/unit/test_example_config_drift.py` passes as part of the suite
- [ ] T020 Walk quickstart.md scenarios 1–5 against the test names actually written and fix any drift in quickstart.md

---

## Dependencies & execution order

- Phase 2 blocks everything. T001 → T003 (the column must exist for the upsert); T002 ∥ T001; T004 → T005.
- US1: T007 → T008 → T009 (same file, sequential); T010 depends on T003–T004; T011–T013 after T008–T010, parallel with each other (different files).
- US2 depends on US1's T010 (the poll must pass and save the pair) but not on its tests.
- Polish after both stories; T016–T018 in parallel.

## Parallel examples

```text
Phase 2:  T001 (migrations.py)  ∥  T002 (models.py)  ∥  T006 (test_migrations.py, after T001)
US1:      T011 (test_github.py) ∥  T012 (test_poll.py) ∥ T013 (other tests)
Polish:   T016 (state.md) ∥ T017 (audit-log.md) ∥ T018 (2-intake.md)
```

## Implementation strategy

MVP is Phase 2 + US1: it fixes every future label change. US2 adds no production code beyond
US1 — NULL-as-mismatch falls out of T008's comparison — and exists to prove the affected machine
heals, so it is delivered in the same change. Commit per phase: schema, boundary + poll, docs.
