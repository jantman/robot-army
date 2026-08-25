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
| `component` | `daemon`, `cli`, or `web` — which process wrote it |
| `kind` | `event`, `intent`, or `outcome` |
| `action` | Dotted name: `github.poll`, `git.fetch`, `hook.step`, `state.session`, … |
| `outcome` | `ok`, `error`, or `pending` (only on an `intent`) |
| `entity_type` / `entity_id` | What it happened to |
| `target` | A path, URL, or socket, where one is more useful than an id |
| `action_id` | Present on `intent`/`outcome` pairs, shared between them |
| `dry_run` / `simulated` | Present and `true` only when the record concerns simulated work |
| `detail` | Everything else, action-specific |

## The `web` component

Milestone 002 added a third writer. Records from the web interface carry
`"component": "web"`, which is what makes "which interface did this?" answerable from the
record alone rather than by inference from the timestamp.

```bash
jq -c 'select(.component == "web")' ~/.local/state/robot-army/logs/audit-*.jsonl
robot-army log --item 42          # both interfaces' records, interleaved in causal order
```

Every state-changing request produces an `intent`/`outcome` pair named for its route —
`web.resume`, `web.abandon`, `web.cancel`, `web.dispatch.pause`, `web.poll` — written
**before** the action runs, including before the checks that might refuse it. That ordering
is deliberate: it means a refused request, a crashed request, and a successful one all leave
a record, so **an error response with no corresponding record is impossible by construction**
rather than by discipline.

The operations underneath write their own records too, so a resume from the web reads as
`web.resume` (intent) → `state.work_item` → `worktree.prepare` → … → `web.resume` (outcome).
The two long actions, resume and restart, run on a worker thread and answer HTTP immediately;
what the operation actually returned lands later as `web.resume.result`.

`web.start` records the address and port the interface is actually listening on, on every
start. Under the exposure model in
[002's spec](../specs/002-web-ui/spec.md) the bind address *is* the access policy, so it is
the one fact that is never allowed to be silent.

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

## The `trello.*` actions

Milestone 003 adds one source and ten actions. Every one of them names the card id, and,
where one exists, the repository and issue — so a card's whole life is `grep`-able by its id.

| Action | When | Notable detail |
|---|---|---|
| `trello.board.check` | Startup, and `robot-army doctor` | Each precondition and its verdict, plus the board's **member list**, which is recorded and never gated on |
| `trello.poll` | Once per board cycle | How many cards carry the tag, and how many were newly tracked |
| `trello.evaluated` | Per card, per evaluation | Whether it resolved, to what, and the candidates it considered |
| `trello.needs_info` | A card is held | The reason, and the reason last written onto the card |
| `trello.issue.create` | An issue is filed from a card | Intent/outcome pair. The failure branch carries the attempt count |
| `trello.card.comment` | Any comment we write | Intent/outcome pair |
| `trello.card.move` | Any move we make | Intent/outcome pair, naming both lists |
| `trello.card.move_refused` | A move we declined | Where the card is, where we last put it, and what we *would* have done |
| `trello.recovered` | Any recovery path fires | Which path — issue listing, or marker comment — and what it found |
| `trello.dropped` | A card leaves the board | Whether the mapping was kept, and why |

A recovery that happened silently would be indistinguishable from nothing having gone wrong,
which is why `trello.recovered` exists at all: the *absence* of duplicates is not observable,
so the recovery that prevented one has to be.

### Credentials and this source

Trello's documented authentication is a **query string**. This project uses the header form
instead, and that is a security decision rather than a stylistic one: the redaction choke
point below is keyed on **field names**, so a secret embedded inside a string under a key
called `url` would sail straight through it. No `trello.*` record carries a full URL — method
and path only — and the boundary strips query strings and its own credential values from any
text it records, in case a remote quotes back what it was sent.

```bash
grep -c "$TRELLO_API_TOKEN" ~/.local/state/robot-army/log/*.jsonl   # expect 0, always
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
| Individual board **reads** made within a poll cycle — the freshness re-read before a move, and the comment fetch on the recovery path | Same reasoning as the GitHub one, and the same limit: they change no state outside the process, and the cycle *is* logged with what it evaluated and what it decided about each card. Every board **write** is an intent/outcome pair, without exception |
| **Read-only web requests** — every `GET` the interface serves | They change no state outside the process, so Principle III's own scope does not reach them. The exception is written down because the *volume* makes the omission visible: a page auto-refreshing every 10 seconds issues a `GET` every 10 seconds, and logging those would bury the record this project exists to keep readable. Nothing a `GET` does is unreconstructable — the data it read is in the database and in this log. **Every `POST` is logged**, without exception |

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
- A **board** transport failure raises for the same reason the GitHub one does, and is never
  turned into an empty card list. "No cards on the board" and "I could not read the board"
  produce identical behaviour if conflated, and only one of them is a reason to do nothing.
- A card whose issue could not be created stays in `creating` with its reason and a failure
  count, and — the part worth stating — **no comment is left on the card claiming an issue
  exists**. A link to nothing is worse than silence.
- A board precondition that fails disables ingestion only, with an anomaly naming which check
  failed. Dispatch of issues I wrote myself is untouched.

## Reading it from the phone

`GET /log` renders the same records with the same filters — `?item=`, `?since=`, `?outcome=`
— newest first, a bounded page at a time, with GitHub repositories, issues, and pull requests
turned into followable links. Only URLs that are already `https://github.com/…`, or targets
shaped like `owner/repo#123`, become links; anything else in a record stays as text, because
a record can carry an issue body and an arbitrary URL out of one must not become a link on a
page I trust.

Paging reads daily files newest-first and stops the moment the page is full, so it stays
bounded in time and memory against a log of any size rather than proportional to total
history.

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
