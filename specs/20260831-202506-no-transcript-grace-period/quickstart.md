# Quickstart: Validating the Transcript Check

How to prove this feature works, in the order worth running. Details of the decision itself live in
[contracts/transcript-check.md](./contracts/transcript-check.md); the schema is in
[data-model.md](./data-model.md).

## Prerequisites

- The repository checked out, `uv` available.
- No live dispatch is required for any scenario below — that is SC-004, and scenario 4 is the proof.

## 1. The unit suite

```bash
uv run pytest tests/unit/test_transcript_check.py -v
uv run pytest              # the whole suite must pass (constitution: Development Workflow)
```

The new file covers the decision table row by row:

| Scenario | Expectation |
|----------|-------------|
| Transcript appears within the grace period | No anomaly, ever. `transcript_checked_at` set on the pass that finds it (C4) |
| Session younger than the grace period, no transcript | Nothing written; `transcript_checked_at` still `NULL`; asked again next pass (C3) |
| Grace elapsed, no transcript | Exactly one anomaly, `waited_s` recorded, column set (row 4) |
| The same session over ten further passes | Still exactly one anomaly (C2) |
| Anomaly acknowledged, transcript still missing, further passes | Still exactly one anomaly — the case the anomalies index alone cannot cover (C2) |
| `pid = 0` session at any age | No anomaly, no filesystem read, column set (C6) |
| `pid = 4242`, `dry_run = True` (a `no-remote` session) | Judged exactly like a live one (C6, and the fix for issue #58's third complaint) |
| Session ended, no transcript, grace elapsed | Reported (C5) |
| Session ended with a transcript | Not reported (C5) |
| Undateable `started_at`, no `confirmed_at` | Reported with `waited_s: null` |

Also assert **C1** where it belongs — in the dispatch tests: a full dispatch leaves the anomalies
table empty.

## 2. The migration

```bash
uv run pytest tests/unit/test_migrations.py -v
```

Assert: `SCHEMA_VERSION == 8`; `idx_sessions_transcript_open` exists; and a database seeded with
session rows *before* migrating comes out with every one of them backfilled — so the first pass
after an upgrade reports nothing about history.

## 3. The regression the issue asked for

Issue #58 closes with: *"A regression test should assert that a dispatch whose transcript appears
shortly afterwards raises **no** anomaly."* That is the first row of the table above, and it is the
test whose absence let this ship. It must exist by name.

## 4. End to end, without a live dispatch

This is SC-004 — the check the old code made impossible.

```bash
# 1. Dispatch at an effect level that launches a real session but touches nothing remote.
uv run robot-army status                   # note: no outstanding anomalies

# 2. Dispatch an item and immediately look. Nothing should be reported.
uv run robot-army anomalies                # expect: none. This is the reported bug's inverse.

# 3. Wait for the transcript to appear, then force a reconciliation pass.
ls ~/.claude/projects/**/<session-id>.jsonl
uv run robot-army reconcile
uv run robot-army anomalies                # still none (SC-001)
```

To see the *other* branch without breaking anything, point the projects seam at an empty directory
for one pass — the session's transcript then cannot be found, and after five minutes the anomaly
appears, exactly once, with `waited_s` recorded (SC-002, SC-003, SC-006).

## 5. Read what it says

```bash
uv run robot-army anomalies
```

The note must name both causes, say the check cannot tell them apart, and state that the session
must be restarted rather than resumed. If following it leads you to `robot-army doctor` and
`doctor` is clean, the note must have already told you that a clean environment does not close the
question (SC-005) — that is the failure of the old text.

## 6. The documentation

`README.md`'s `no_transcript` bullet must describe what now happens: raised by reconciliation after
a grace period, meaning the session left nothing resumable, cause not determined by the check
itself (FR-013).
