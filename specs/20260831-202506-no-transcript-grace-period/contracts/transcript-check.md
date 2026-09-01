# Contract: The Transcript Check

The one decision this feature owns, stated so a test can assert it and a reader can audit it.

## Signature

```text
_sweep_transcripts(conn, *, audit) -> tuple[checked: int, reported: int]
```

Called once per reconciliation pass, after `_sweep_stale_sessions` and before `_orphan_sweep`.
Never raises for an operational condition — an unreadable projects directory reads as "no
transcript found", which is what `transcript_exists` already returns for `OSError`.

## Population

```sql
SELECT * FROM sessions WHERE transcript_checked_at IS NULL ORDER BY id
```

No state filter, no age filter, no `dry_run` filter. Every open question, and nothing else.

## Decision table

Evaluated per session, in this order. The first row that matches decides.

| # | Condition | Anomaly | Audit | `transcript_checked_at` |
|---|-----------|---------|-------|------------------------|
| 1 | `not session.pid` | none | `session.transcript_skipped` | set |
| 2 | `transcript_exists(session_id)` | none | `session.transcript_found` | set |
| 3 | `age < TRANSCRIPT_GRACE_SECONDS` | none | none | **left NULL** |
| 4 | otherwise | `no_transcript` | `session.transcript_missing` | set |

`age` = seconds since `confirmed_at or started_at`, via the module's existing `_age_seconds`;
unparseable or absent reads as infinite and therefore decides row 4 with `waited_s: null`.

Row 3 is the whole feature: a session younger than the grace period is left exactly as it was
found, to be asked again next pass. It is the only row that writes nothing.

Row 1 precedes row 2 deliberately. A simulated session cannot have a transcript, so testing for one
first would reach the filesystem to learn what the record already says.

## Invariants

- **C1** — Dispatch raises no `no_transcript` anomaly. Confirming a session writes nothing to the
  anomalies table.
- **C2** — At most one `no_transcript` anomaly exists per session id, for the life of the database,
  regardless of acknowledgement, restarts, or how many passes run.
- **C3** — No session is judged before `TRANSCRIPT_GRACE_SECONDS` have elapsed since its clock
  started.
- **C4** — A session whose transcript appears at any point before it is judged is never reported.
- **C5** — Judgement does not depend on session state: running and ended sessions are judged alike.
- **C6** — The exemption is read from `session.pid`. The effect level is not consulted, and
  `EffectLevel` is not named in `reconcile.py`.
- **C7** — The anomaly write and the `transcript_checked_at` write commit together or not at all.
- **C8** — A pass examines only rows with `transcript_checked_at IS NULL`, resolved through
  `idx_sessions_transcript_open`.
- **C9** — Every examined session leaves exactly one audit record, and every unexamined session
  leaves none.

## Worst-case latency

`TRANSCRIPT_GRACE_SECONDS` + `daemon.reconcile_seconds` = 300 + 60 = **360 seconds** from
confirmation to report, against SC-002's 7-minute bound.

## Unchanged by this contract

- `sessions.transcript_exists` — what it looks for and where.
- The `no_transcript` anomaly's `kind`, `entity_type`, and `entity_id`, so `robot-army anomalies`,
  `--since`, acknowledgement, and the non-zero exit on outstanding anomalies all keep working.
- Every other reconciliation sweep, and their inputs.
