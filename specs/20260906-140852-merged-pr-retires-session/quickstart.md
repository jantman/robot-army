# Phase 1 — Quickstart: validating the change

Four scenarios. The first two are the feature; the third and fourth are the guards that keep it
from being an over-reach. All run against the unit suite — no GitHub call is involved, which is
the point of the signal being stored.

```bash
uv sync
uv run pytest tests/unit/test_session_retirement.py -q
uv run pytest                      # the whole suite must pass
```

## 1. The bug, reproduced and then fixed — a full pass

The one that would have caught this. It must run a **whole reconciliation pass**, not the sweep
alone, because the anomaly is raised by a different sweep eight lines later.

| Setup | |
|---|---|
| item | `done` |
| pull requests | `[{"number": 147, "url": "…", "state": "merged"}]` via `db.record_pull_requests` |
| worker | alive, `status: "idle"`, `statusUpdatedAt` **47 seconds** ago — the measured number from #149 |

Run `reconcile.reconcile(...)` once. Expect:

| | |
|---|---|
| `result.retired` | `1` |
| `result.orphans` | `0` |
| `result.anomalies_resolved` | `0` — nothing to resolve, because nothing was raised |
| the session row | `LOST`, with `ended_at` |
| `anomaly.raised` records | none for that session |
| the slot | free, by `capacity` |

Before the change this test fails on `result.retired == 1` and on `result.orphans == 0`, which
is the bug stated as an assertion.

## 2. The tab and the worktree follow, in the same pass

Same setup as scenario 1, plus a window carrying the item's marker and
`cleanup.on_issue_close` enabled. One pass. Expect the tab closed and the item's
`cleanup_state` no longer `skipped`. Neither rule is modified by this feature; this asserts
that both move earlier for free (research R6), which is the #81 half of the issue.

## 3. No merged pull request keeps the full 30 minutes

Three variants, each with a freshly-idle worker under a `done` item, each expecting **nothing
retired and nothing recorded**:

| Variant | `pull_requests` column |
|---|---|
| never looked up | `NULL` |
| looked up, none found | `'[]'` |
| open or closed-unmerged only | `[{"state": "open"}]`, `[{"state": "closed"}]` |

Then advance the same item's idle time past `RETIRE_IDLE_SECONDS` and expect it retired on the
unchanged rule, with `signal: quiet_period`. This is the guard that keeps User Story 2 true.

## 4. Idleness still gates the merged path

A `done` item with a merged pull request whose worker is **not** idle:

| Variant | Registry |
|---|---|
| busy | `status: "busy"` |
| status absent | no `status` |
| timestamp absent | `status: "idle"`, no `statusUpdatedAt` |
| timestamp in the future | a clock that disagrees with ours |

Each expects nothing terminated and nothing written, however old the timestamp is. This is
FR-002 and it is the property that keeps a worker from being ended mid-tool-call — a merged
pull request removes the duration requirement and never the idleness one.

## 5. The record says why

Retire once on each path and read `session.retire` from the audit log:

| Path | `detail.signal` | `detail.idle_s` |
|---|---|---|
| merged pull request, 47 seconds idle | `merged_pull_request` | `47` |
| no pull request, 31 minutes idle | `quiet_period` | `≥ 1800` |

FR-009: the log alone answers "why was this allowed", without the reader having to know that a
low `idle_s` implies a merge.

## On the real machine

No setup is required and nothing needs to be triggered by hand. The next item worked to a
merged pull request is the test: after merging, watch `robot-army reconcile` (or the daemon's
next pass) and expect the item to reach `done`, the session to be retired, the tab to close and
the anomaly list to stay empty — all within the same minute rather than the same half-hour.

The backlog already on the machine is covered by the same code with no migration: those items
are `done` with merged pull requests recorded, so the first pass after the change retires them.
