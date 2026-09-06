# Contract: allocating a simulated issue number

**Feature**: [spec.md](../spec.md) | **Plan**: [plan.md](../plan.md)

Governs `SimulatedIssueWriter` in `src/robot_army/boundaries/github.py` and the helper it calls in
`src/robot_army/db.py`. It is an internal contract; nothing here is a public API.

## The boundary signature does not change

```python
create_issue(repo_key: str, title: str, body: str) -> Issue
```

`GitHubWriter` and `SimulatedIssueWriter` remain interchangeable to every caller. A number is never
passed in: the real writer's number comes from GitHub, and the simulated writer's must come from
somewhere equivalent — the record of what it has already issued. This is why the allocation lives
inside the writer rather than at the call site.

## Construction

```python
SimulatedIssueWriter(audit: AuditLog, conn: sqlite3.Connection)
```

The connection is **required**. There is no default and no `None` branch: a caller that omits it
fails immediately rather than silently getting the per-process counter this feature exists to
remove. It must be the same connection the caller uses for card state — a second connection to the
same database would buy nothing and would add a WAL reader.

## Guarantees

1. **Unused when minted.** The returned `Issue.number` is held by no `cards` row with the same
   `repo_key` and `dry_run = 1` at the moment of the call.
2. **Recognisable.** `number > SIMULATED_ISSUE_BASE` always, for every repository and every count
   of existing rows.
3. **Monotonic per repository.** Successive calls for one repository return increasing numbers, so
   long as each is recorded. Numbers are not reused and gaps are not filled.
4. **Independent of unrelated simulated traffic.** The number depends only on what is recorded. Any
   number of `comment()` calls before it changes nothing.
5. **Per repository.** Allocation for `owner/a` is unaffected by rows for `owner/b`.
6. **Read-only.** The call performs a `SELECT` and writes one audit record. It reserves nothing,
   locks nothing, and leaves no state behind if the process dies immediately afterwards.

## Not guaranteed

- **Reservation.** An allocated number is not held. If the caller never records it, the next call
  returns the same number. This is correct: an unrecorded number is an unused number.
- **Freedom from a `TOCTOU` race.** Two processes allocating simultaneously could agree on a
  number. The single-instance lock is what makes that unreachable, and `idx_cards_issue` plus the
  existing retry is what makes it survivable if the assumption ever breaks.

## The audit record

Unchanged: one `github.issue.create` record per call, `simulated: true`, carrying the full title
and body and `detail.would_return.number`. No record is written for the allocation itself, which
changes no state outside the process.

## When the mapping is still refused

The `sqlite3.IntegrityError` guard in `intake._perform_creation` stays, and so does everything it
does: the card is left in `creating`, its `create_failures` is incremented, its `reason` is
recorded, the anomaly threshold still applies, and the rest of the pass still runs. What changes is
the sentence. The reason must:

- name the card already holding the number, as it does today;
- describe what the next pass actually does — allocate above the highest recorded number for that
  repository — and not claim a "fresh number" the old code never produced;
- remain one line a reader of `robot-army cards` can act on.

## Comment URLs

`comment()` keeps a counter of its own so that two simulated comments in one process yield
different `#issuecomment-simulated-N` fragments. That counter has no relationship to issue numbers,
constrains nothing, and is read by nothing.
