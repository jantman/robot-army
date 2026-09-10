# Implementation Plan: A Stored ETag Is Only Replayed Against the Request That Produced It

**Branch**: `robot-army/issue-60-changing-github-label-silently-blinds` | **Date**: 2026-09-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/20260910-053539-etag-bound-to-query/spec.md`

## Summary

`poll_state` holds one ETag per repository and nothing about which request it answered, so a
change to `[github] label` leaves every repository replaying a validator captured under a
different query. GitHub answers 304, the poll reads that as "unchanged", and discovery is blind
for as long as the repository stays quiet — while every health surface reports normal.

The fix binds the validator to the request. `poll_state` gains `etag_request`: the complete
request line — path and sorted query parameters — that the stored ETag's response answered.
`GitHubReader.poll` builds that line from the same values it sends (one helper, so the request
sent and the request recorded cannot drift), and sends `If-None-Match` only when the stored
line is identical. A row with no recorded line (every row on an upgraded machine) does not
match, so the machine that hit this heals itself with one unconditional request per repository.
The `github.poll` record says whether the ETag was sent and, when one was discarded, why.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: none added. `urllib.parse.urlencode` (standard library, `quote` from
the same module is already imported by `boundaries/github.py`), `httpx` as today

**Storage**: SQLite, `poll_state` gains one nullable `TEXT` column via migration 015

**Testing**: `pytest` via `uv run pytest`. The boundary tests drive the real `GitHubReader`
through `httpx.MockTransport`; the poll tests use `FakeIssueReader`; migration tests use the
existing ladder helpers, including the killed-migration pattern

**Target Platform**: one Linux machine

**Project Type**: single Python package (CLI daemon plus local web interface)

**Performance Goals**: no change in steady state: an unchanged listing is still a 304 at zero
rate-limit cost. A label change costs one full listing per repository, once

**Constraints**: the recorded request must never contain a credential (it holds path and query
only; the token travels in a header); an ETag must never be stored paired with a request whose
response did not supply it

**Scale/Scope**: `boundaries/__init__.py` (protocol, `PollResult`), `boundaries/github.py`,
`poll.py`, `models.py`, `db.py`, `migrations.py`; `tests/conftest.py` fake; three guide pages

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design — see the second table.*

| Principle | Assessment |
|---|---|
| **I. Simplicity First** | One nullable column, one helper that builds the request, one string comparison. No new module, dependency, config key or type. The request is recorded as plain text rather than a hash, which is *fewer* moving parts (no hashing, no canonical-JSON step) and makes the row explain itself. Two further mechanisms the issue suggested — a startup check and a `poll --no-cache` flag — are rejected in research R6 and R7: the first duplicates what the per-poll comparison already does, and the second would be silently ignored on the daemon-delegated path. |
| **II. Single-User, Local-First** | Unchanged. State stays in the local SQLite file. |
| **III. Total Accountability** | **What this logs:** nothing new as an action — the poll is still one aggregate `github.poll` record per repository per cycle, the Principle III exception milestone 001 already enumerated and justified. That record gains `etag_sent` on every poll and, when a stored ETag was *not* sent, `etag_discarded` (`request_changed` or `request_unrecorded`) plus the request that was sent instead and the one the ETag belonged to. So the first-poll-after-change 200 is explained by the log rather than looking like an unexplained miss (FR-007, SC-004). The discard itself changes no state outside the process; its effect on the stored row is the ordinary `poll_state` save that already follows every poll. **No new unlogged action is introduced.** |
| **IV. Interruption Tolerance** | **If it is killed halfway:** the ETag and its request are written by the one `INSERT … ON CONFLICT` that already writes the ETag, inside the existing transaction — so the row holds either the old pair or the new pair, never a mix. Killed after the listing and before the save, the next poll repeats one request with the old pair, which is today's behaviour. Migration 015 is a single `ALTER TABLE … ADD COLUMN` run inside the ladder's transaction with `user_version` last; killed, it leaves version 14 and no column, and re-runs cleanly — tested with the existing killed-migration pattern. A failed poll saves the stored pair back unchanged (FR-006). No network call is added. |
| **V. Public Code, Unsupported Project** | The recorded request contains the repository path and query parameters only; the token is in the `Authorization` header, which is not part of it (tested). The `IssueSourceReader.poll` signature changes; there are no outside consumers to owe compatibility to. |
| **Operating Constraints** | The row is inspectable with `sqlite3` and reads as the request it was. Nothing outward-facing is added or enabled. |
| **Development Workflow** | Spec → plan → tasks → implement. Unit tests for the boundary (match, mismatch, unrecorded, ETag-less 200, credential absence, the audit detail), the poll loop (label change end to end, transport failure keeps the pair, upgrade healing), and the migration (fresh, preserved values, killed-and-re-run). |

**Verdict: PASS.** No violation, so Complexity Tracking is omitted.

## Project Structure

### Documentation (this feature)

```text
specs/20260910-053539-etag-bound-to-query/
├── plan.md               # this file
├── spec.md
├── research.md           # R1–R8
├── data-model.md         # poll_state.etag_request, PollState, PollResult
├── quickstart.md         # validation scenarios
├── contracts/
│   └── poll-request.md   # the recorded request line, the comparison, the audit detail
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks output
```

### Source Code (repository root)

```text
src/robot_army/
├── migrations.py         # SCHEMA_015_SQL: ALTER TABLE poll_state ADD COLUMN etag_request
├── models.py             # PollState.etag_request
├── db.py                 # save_poll_state writes it
├── boundaries/
│   ├── __init__.py       # IssueSourceReader.poll(repo_key, etag, *, etag_request);
│   │                     # PollResult.request
│   └── github.py         # _issues_request(); poll compares, sends or discards, records why;
│                         # a 200 no longer carries the old ETag forward
└── poll.py               # passes and saves the pair; the failure path keeps it

tests/
├── conftest.py           # FakeIssueReader honours the request line
└── unit/
    ├── test_github.py    # the boundary's comparison and audit detail
    ├── test_poll.py      # label change end to end; failure keeps the pair; upgrade heals
    └── test_migrations.py

docs/guide/
├── state.md              # poll_state.etag_request, and why it has no backfill
├── audit-log.md          # the issue #60 github.poll detail keys
└── 2-intake.md           # changing the label costs one full listing, and nothing else
```

**Structure Decision**: no new files in `src/`. The comparison lives in the boundary because the
boundary is the only code that knows the request it is about to send *and* writes the record
that must explain what it did (research R1).

## Phase 1 re-check (post-design)

| Question | Answer |
|---|---|
| Did the design add a dependency? | No. |
| A module, a table, a state file, a config key? | One column on an existing table. No config key, so `share/config.example.toml` needs no regeneration. |
| An abstraction with one implementation? | No. `_issues_request` is a private helper with two uses in one method — the request sent and the request recorded — and that pairing is the whole of FR-004. |
| Anything on by default that was not? | No. |
| Which documentation? | `state.md` (a table's shape), `audit-log.md` (a record's shape), `2-intake.md` (the label gate) — per `CLAUDE.md`'s table. `configuration.md` points to `1-setup.md`/`2-intake.md` for the label, and no key changes. |

**Verdict: PASS, unchanged.**
