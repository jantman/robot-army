# Data Model: Give the Missing-Transcript Check Time to Be Right

One additive migration. No table is created, no column is dropped, no existing value is rewritten
except the one-time backfill described below.

## Migration 008

```sql
ALTER TABLE sessions ADD COLUMN transcript_checked_at TEXT;

-- FR-010: the sweep's cost must follow open questions, not session history. Without this the
-- result set is small but the scan is over every session ever dispatched, on a query that runs
-- every 60 seconds forever.
CREATE INDEX idx_sessions_transcript_open
    ON sessions (transcript_checked_at)
    WHERE transcript_checked_at IS NULL;

-- Every session that existed before this feature has already been judged, correctly or not, by
-- the old inline check. Leaving them NULL would make the first pass after the upgrade report the
-- entire history at once. Runs inside migration 008's transaction, so an interrupted upgrade
-- re-runs it whole.
UPDATE sessions SET transcript_checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    WHERE transcript_checked_at IS NULL;
```

`SCHEMA_VERSION` becomes 8.

## `sessions.transcript_checked_at`

| Property | Value |
|----------|-------|
| Type | `TEXT`, nullable, UTC `%Y-%m-%dT%H:%M:%SZ` |
| `NULL` | The transcript question is **open**: this session has not been judged |
| Timestamp | The question is **closed**, at that moment, and is never asked again |
| Written by | `reconcile._sweep_transcripts`, once per session, in the same transaction as any anomaly it raises |
| Written for | Every session it examines — found, missing, or exempt alike |
| Read by | `_sweep_transcripts` only |

Model change: `models.Session` gains `transcript_checked_at: str | None = None`. `from_row` selects
by declared field, so the column is invisible to the dataclass until the field exists.

### The question's lifecycle

```text
                         session row inserted
                                 │
                                 ▼
                    transcript_checked_at = NULL      ← the only durable state
                                 │
             ┌───────────────────┼────────────────────┐
             │                   │                    │
     pid is falsey        transcript found     grace elapsed, none found
   (never ran a process)          │                    │
             │                    │                    │
             ▼                    ▼                    ▼
   audit: transcript_skipped   audit:              raise no_transcript
                            transcript_found      + audit: transcript_missing
             │                    │                    │
             └───────────────────┬┴────────────────────┘
                                 ▼
                     transcript_checked_at = <now>
                          (terminal, forever)
```

Nothing moves a closed question back to open. A transcript deleted after the fact, an acknowledged
anomaly, a resumed item — none of them reopen it. A *new* session row is a new question.

## Decision inputs

Per session with `transcript_checked_at IS NULL`:

| Input | Source | Used for |
|-------|--------|----------|
| `pid` | `sessions.pid` | Exemption: falsey means no process ever ran, so nothing could have written a transcript |
| `confirmed_at`, `started_at` | `sessions` | The clock. `confirmed_at` when present, else `started_at` |
| transcript presence | `sessions.transcript_exists(session_id)` → `~/.claude/projects/**/<session_id>.jsonl` | The observation |
| `TRANSCRIPT_GRACE_SECONDS` | module constant, 300 | The threshold |
| `work_item_id`, `state`, `ended_at` | `sessions` | Detail recorded on the report |

Session **state is not an input**. A session that has ended is judged on the same terms as one still
running (FR-004); ending is not evidence either way, and waiting for it would leave a long-running
unresumable session unreported for hours.

## Anomaly record: `no_transcript`

Kind and entity are unchanged, so existing listings, filters, and exit-code behaviour keep working.

| Field | Value |
|-------|-------|
| `kind` | `no_transcript` (unchanged) |
| `entity_type` | `session` (unchanged) |
| `entity_id` | the session id (unchanged) |
| `detail.item_id` | the work item this session belongs to |
| `detail.waited_s` | seconds between the clock start and this judgement; `null` when the row could not be dated |
| `detail.session_state` | the session's state at judgement — running, exited, lost |
| `detail.ended_at` | when it ended, or absent while it is still running |
| `detail.note` | see below |

The note, replacing the one that asserts a cause (FR-009):

> no resumable transcript for this session after waiting {waited_s}s. Two causes are possible and
> this check cannot tell them apart: the worker never saved one — check `robot-army doctor` for
> `CLAUDE_CODE_*` in the session host's environment — or the session ended before it wrote one,
> which its exit record will show. Either way this session cannot be resumed; restart the item
> rather than resuming it.

## Audit records

One per session examined, written once, never repeated:

| Action | Outcome | Detail |
|--------|---------|--------|
| `session.transcript_found` | `ok` | `session_id`, `item_id`, `waited_s` |
| `session.transcript_missing` | `ok` | `session_id`, `item_id`, `waited_s`, `session_state`, `reported` |
| `session.transcript_skipped` | `ok` | `session_id`, `item_id`, `reason: "no process was ever recorded"` |

Plus two counters on `ReconcileResult`, carried by the existing per-pass `reconcile.pass` record:
`transcripts_checked` (sessions judged this pass) and `no_transcript` (of those, reported).

Together these satisfy FR-011: which sessions were examined, which lacked a transcript, and which
were reported, all reconstructable from the log alone.

## What is removed

`dispatch.py`'s inline block at `:1044` — the `transcript_exists` call, the `raise_anomaly`, and the
`not dry_run` guard on both. No dispatch-time anomaly replaces it.
