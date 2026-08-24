# The audit log

Where it is, what a record means, and how to answer "what happened?" from it alone.

## Location and rotation

```
~/.local/state/robot-army/logs/audit-YYYY-MM-DD.jsonl
```

JSON Lines: one record per line, opened in append mode, **flushed per record**. A record
still sitting in a buffer when the process dies is not a durable record, which is why the
flush is per line rather than per block.

Daily files, **never deleted automatically**. The constitution requires that any rotation
policy not discard records silently; nothing here discards anything. Prune by hand when
the directory gets large.

A partially written final line is possible — the process can die between the write and the
next flush. Readers skip unparseable lines and **count** them; `robot-army log` reports the
count at the end. Refusing to read the log because of one truncated line would be the wrong
trade.

## Reading it

```bash
robot-army log --since 10m         # 30s, 10m, 2h, 1d
robot-army log --item 42           # everything about one work item
robot-army log --follow            # tail the current day's file
robot-army log --json | jq .       # machine-readable
```

Or directly, since it is just JSONL:

```bash
jq -c 'select(.action | startswith("github"))' ~/.local/state/robot-army/logs/audit-*.jsonl
```

## Record shape

Every record carries what Principle III enumerates: when, which component, what action,
against what target, with what result.

```json
{
  "ts": "2026-08-23T14:07:11Z",
  "component": "daemon",
  "kind": "event",
  "action": "state.work_item",
  "outcome": "ok",
  "entity_type": "work_item",
  "entity_id": 42,
  "detail": {"from": "dispatching", "to": "active", "reason": "session confirmed present"}
}
```

| Field | Meaning |
|---|---|
| `ts` | UTC ISO 8601, `Z` suffix. Always. |
| `component` | `daemon` or `cli` — which process wrote it |
| `kind` | `event`, `intent`, or `outcome` |
| `action` | Dotted name: `github.poll`, `git.fetch`, `hook.step`, `state.session`, … |
| `outcome` | `ok`, `error`, or `pending` (only on an `intent`) |
| `entity_type` / `entity_id` | What it happened to |
| `target` | A path, URL, or socket, where one is more useful than an id |
| `action_id` | Present on `intent`/`outcome` pairs, shared between them |
| `dry_run` / `simulated` | Present and `true` only when the record concerns simulated work |
| `detail` | Everything else, action-specific |

## Intent and outcome

An append-only log cannot amend a record after the fact. The Operating Constraints require
irreversible or outward-facing actions to be logged *before* execution, so "log before" plus
"record the result" necessarily means **two records**:

```json
{"ts":"…:11Z","kind":"intent","action":"github.comment","outcome":"pending","action_id":"a1b2","target":"jantman/demo#42"}
{"ts":"…:12Z","kind":"outcome","action":"github.comment","outcome":"ok","action_id":"a1b2","detail":{"comment_url":"https://…"}}
```

The pairing is also the crash signature. **An `intent` with no matching `outcome` means the
process died mid-action.** Finding them:

```bash
jq -r 'select(.action_id) | "\(.action_id) \(.kind)"' ~/.local/state/robot-army/logs/audit-*.jsonl \
  | sort | uniq -c | awk '$1 == 1'
```

## Secrets

A single redaction choke point applies to every record on its way to the file, keyed on
field name. There is no path to the log that bypasses it. Field names containing `token`,
`secret`, `password`, `api_key`, `authorization`, `credential`, or `private_key` become
`<redacted>`; an `env` object has its secret-looking members redacted individually so the
rest stays legible.

Issue titles and bodies are **not** redacted. They are the prompt, and reconstruction needs
them.

```bash
grep -ri 'ghp_' ~/.local/state/robot-army/logs/ && echo "REDACTION FAILURE" || echo "clean"
```

## What is deliberately not logged

Principle III permits gaps only when they are named and justified in the feature plan. The
full argument is in [plan.md](../specs/001-minimum-daemon/plan.md); the list, so a reader of
the log knows what its silence means:

| Gap | Why |
|---|---|
| Individual **successful, read-only** GitHub GETs — one aggregate record per repository per poll instead | They change no state outside the process, and at a 60-second poll the individual records would be pure volume. Every **failure** and every **retry** is still logged individually |
| Individual SQLite statements — the **transition** they effect is logged instead | The transition is the meaningful unit for reconstruction, and the database is directly inspectable |
| Heartbeat writes, every 5 seconds | ~17,000 records a day of noise. The heartbeat file *is* the record, and its staleness is the signal |
| Individual `/proc` and registry reads during reconciliation — one aggregate per pass | Same disproportion. The *conclusions* — sessions found, orphans detected, states changed — are each logged individually |
| **Actions the session itself takes** inside the worktree | They happen outside this process entirely. This log records the dispatch, the session identity, and where the transcript lives; the worker's own transcript is the record of what it did. Claiming otherwise would be dishonest about what this log covers |

## Silent failure is forbidden

Every swallowed error, retry, and fallback leaves a record. Specifically:

- A GitHub transport failure **raises** rather than returning an empty result — "no eligible
  work" and "I could not ask" are different facts.
- A malformed exit record is quarantined to `spool/exits/rejected/` and raises an anomaly.
  It is never deleted.
- An unknown session-registry version degrades to `/proc` **and** raises an anomaly.
- A degraded terminate path (process group rather than systemd scope) logs that it was taken.
- A GitHub comment that fails does not change the work item's state, but the failure is
  recorded.

## Reconstructing an item's history

```bash
robot-army log --item 42
```

Reading top to bottom gives: discovered → eligibility verdict → worktree prepared (with each
preparation step's exit code and duration) → launch argv → confirmation or its absence →
exit code and signal → final state. Everything needed to answer what happened, when, to what,
and with what result, without re-running anything.

The database is the other half of the picture and is directly inspectable:

```bash
sqlite3 ~/.local/state/robot-army/state.db 'SELECT id, state, failure_reason FROM work_items'
```
