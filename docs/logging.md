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

## Recorded in UTC, displayed in local time

**The record is UTC and always will be.** Every `ts` above, every `*_at` column in the
database, the heartbeat, and every machine-readable response — `robot-army <command> --json`
and the web interface's JSON — carry `%Y-%m-%dT%H:%M:%SZ`. That is what makes a record
comparable against another record, against GitHub, and against itself a year later. A log
whose timestamps depended on where the reader stood could not meet the constitution's
reconstruction standard, which is why this half does not move.

**What you read is local.** Since milestone 010, every timestamp the CLI prints and every
timestamp the web interface renders is converted to this machine's timezone and labelled
with its offset — `2026-08-29 21:31:07 -04:00` for the record `2026-08-30T01:31:07Z`. Note
that these are the same instant on different calendar days; that is the point of the
feature, not a bug in it.

The two halves differ **on purpose**, and the difference is easy to mistake for an
oversight in either direction. Some notes for whoever reads this next:

* The conversion lives in `robot_army.timefmt`, which is the only producer of a displayed
  timestamp. It is display-only: nothing compares, sorts, stores, or parses back what it
  returns, and `timefmt.parse_stamp` deliberately refuses to read a displayed value so that
  a leak into a comparison fails loudly instead of quietly comparing two clocks.
* The zone is whatever the operating system reports — `TZ` if set, otherwise
  `/etc/localtime`. There is no configuration key and should not be one: on a single-user
  machine the setting would have exactly one correct value, which the OS already holds.
* An offset appears on **every** displayed stamp rather than once per page. At the autumn
  daylight-saving fold two instants an hour apart render to the same wall clock, and the
  offset is the only thing that tells them apart.
* Audit files stay partitioned by **UTC day**, so an evening's activity can land in two
  files. Reading by day is a record-layer operation and is unaffected.
* A record's `detail` payload is quoted verbatim in both interfaces, timestamps included.
  It is the record shown as the record; rewriting values inside free-form JSON would make
  the display disagree with the file it is quoting.

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

## The milestone 004 actions

Six new actions, and one changed one. Every one of them exists to answer a question that is
asked long after the fact: *why did nothing dispatch for twenty minutes?* and *why is this
499 MB still here?*

| Action | When | Notable detail |
|---|---|---|
| `dispatch.at_capacity` | **Changed** — when a hold's signature changes, not every pass | The counts, the cap, the ours/others split, and which item is at the head |
| `dispatch.hold_ended` | A capacity hold clears | How long it lasted, how many passes it spanned, and what freed it |
| `capacity.unobservable` | The registry and `/proc` both failed | Which failed, and that dispatch is being withheld as a result. Also raises a de-duplicated anomaly of the same kind |
| `cleanup.considered` | An item is evaluated for cleanup | The decision and the guard that made it — including "not eligible", so an item that was looked at and passed over is distinguishable from one nobody looked at |
| `cleanup.retained` | A guard refused a removal | Which guard, what it saw, and the worktree and branch it kept |
| `notify.send` | A notification is attempted | The kind, the item, and whether the per-cycle bound suppressed it. Written whether or not it left the machine |
| `notify.suppressed` | The per-cycle bound was reached | How many were held back, and of which kinds |

The existing `git.remove_worktree` and `git.delete_branch` gain a new caller and are
otherwise unchanged. `git.delete_branch` from cleanup always carries `force: true`, which
here means a *stronger* guard than git's has already passed — containment against the remote
was proved — rather than that a guard was skipped.

### The `dispatch.at_capacity` retention rule

A hold is recorded when its **signature** changes — `(total, others, cap, head item)` — and
once more when it ends. Not once per pass.

This is a documented summarisation under Principle III's retention clause, not an exception
to it. Everything about the hold is recorded: that it happened, what caused it, the counts
behind it, when it started, when it ended, and how many passes it spanned. What is *not*
written is 17,280 identical records a day at a five-second tick, which would not make the log
more reconstructible — it would make it less, by burying the records that carry information
under records that carry none. The same judgement already lives in this project as
`raise_anomaly`'s partial unique index.

The rule is written down here rather than improvised because the constitution requires a
retention policy to be documented, and because milestone 004 makes holds routine rather than
rare.

### Notifications carry no credentials

A `NotificationEvent` has exactly six fields — kind, item id, repository key, title, detail,
url — and every value is an identifier or a state name this system chose. There is no field
a secret could reach, which makes it a property of the shape rather than of the discipline of
whoever fills it in. A test asserts it across a run that includes an authentication failure,
because that is the case where a token would otherwise ride along inside an error string.

```bash
grep -c "$ROBOT_ARMY_GITHUB_TOKEN" ~/.local/state/robot-army/logs/*.jsonl   # expect 0
```

## The milestone 005 actions

One action changed, no new ones. `repo.onboard` was always an intent/outcome pair; what it
carries and *when it is written* both changed.

**The detail grew** to answer "which repository did I actually approve?" — the question the
whole milestone exists to make answerable:

| Field | Meaning |
|---|---|
| `clone_path` | the location approved, absolute and with symlinks resolved |
| `path_source` | `derived` (from `[paths] repo_root`) or `configured` (from a `[repos.*]` section) |
| `remote` | which remote was consulted — `origin`, or the sole remote when there is no `origin` |
| `verified_origin` | the **normalised** `host/owner/name` found there. Never a raw URL |
| `owner_verdict` | `owned`, `listed`, or the owner found, from the one repository lookup |

**Refusals are now written, and that is a bug fix rather than new behaviour.** Before this
milestone `onboard` returned for a missing `[repos.*]` section *before* opening any audit
action, so a refusal was printed and forgotten. Under Principle III's reconstruction standard
a refusal is a result, and one that leaves no trace is a gap. Every non-zero exit from
`onboard` now writes an outcome carrying a `cause`:

| `cause` | What was refused |
|---|---|
| `not_permitted` | neither owned-and-`include_owned` nor listed in `extra_repos` |
| `no_such_repository` | the source system has no such repository |
| `malformed_key` | not `owner/name` |
| `no_clone` | nothing at the resolved path |
| `linked_worktree` | the path is a linked worktree, not a primary clone |
| `inside_worktree_root` | the path is inside `[paths] worktree_root` |
| `no_remote` / `ambiguous_remote` | no remote, or several with none named `origin` |
| `unparseable_url` | the remote URL is not a repository URL |
| `wrong_repository` | a real clone of a **different** repository is there |
| `source_unreachable` | the source system could not be asked — a bad token, or no network |
| `unapproved_committed_settings` | `--yes` refused to skip an unreviewed settings change |
| `aborted_at_prompt` | I said no |
| `interrupted_at_prompt` | I pressed Ctrl-C at the prompt |
| `no_answer_available` | input ended before the prompt was answered |

```bash
jq -r 'select(.action == "repo.onboard" and .detail.refused) | "\(.entity_id) \(.detail.cause)"' \
  ~/.local/state/robot-army/logs/audit-*.jsonl
```

**The last two are milestone 011, and are the same bug fix one milestone later.** Onboarding
asked for approval before showing what was being approved (issue #17), so nobody reached that
prompt informed enough to walk away from it — and the two ways of walking away both exited
non-zero writing nothing. `Ctrl-C` propagated past `onboard` before its audit action opened;
end-of-input was an uncaught `EOFError` and a traceback. Now that the approval screen arrives
first, giving up after reading it is the expected second answer, so both are recorded like the
decline they resemble. Every terminating path through `onboard` now leaves exactly one outcome
record: an approval, or a refusal naming its cause.

### The dispatch-time re-verification writes only on failure

Before creating anything, dispatch re-checks that the recorded clone is still there, is still
a primary clone, and still has the same origin. A **pass** writes nothing.

That is a deliberate omission and it is not a gap. The worktree-creation record for the same
item follows milliseconds later on the same code path, so the question "did the clone still
check out?" is answered by the presence of the next record — which is exactly Principle III's
reconstruction standard, met by the log as a whole rather than by one line in it. Writing a
line per dispatch to say "yes, still fine" would add volume and no information.

A **failure** writes the existing `DispatchBlocked` path — the item goes `failed` with the
reason, naming the *recorded* path — and the two failures that mean *the machine changed
under an approval* also raise an anomaly: `clone_path_missing` and `clone_origin_changed`.
Those two are distinguished from an ordinary gate refusal on purpose. An untrusted clone is a
setup step I have not done yet; a clone that moved is a fact about the world I probably do
not know.

### No credential from a remote URL reaches this log

A git remote URL may embed credentials, and milestone 005 is the first time this codebase
reads one at all. Normalisation strips the `userinfo@` component before anything else, and
what is recorded, compared, and printed is the normalised triple — which cannot carry a
secret because it is three lowercase strings with no room for one. The refusal path is held
to the same rule, including the unparseable-URL refusal, which deliberately does **not** echo
the URL back even though that is the case where doing so would feel most helpful.

## The milestone 007 actions

Two new actions, both cheap, and one of them writes far less often than you would expect.

| Action | When | Notable detail |
|---|---|---|
| `speckit.detect` | Once per dispatch, before the prompt is composed | `detected`, the `reason` verbatim, the command `form` found (`skills`, `commands`, `mixed`), whether the guidance was `enabled`, and `suppressed_by` naming the setting when it was turned off |
| `speckit.phase` | A lifecycle phase **changes** | `from`, `to`, and the `feature_dir` it was read from — plus `previous_feature_dir` when the directory itself changed |

The `suppressed_by` field is the one worth knowing about. "This repository has no Spec Kit"
and "this repository has Spec Kit and I turned the guidance off" produce the same behaviour
and must not produce the same record, or a deliberately quiet repository reads as a broken
one.

`speckit.phase` carries no evidence from the session, because none is solicited. Spec Kit's
extension hooks are instructions an agent chooses to follow rather than callbacks, so a
report that never arrives is indistinguishable from a phase not yet reached; everything here
is read from files in the worktree instead. The argument, and the three conditions that would
make hooks worth revisiting, are in
[the 007 spec](../specs/007-speckit-extensions/spec.md#out-of-scope).

### Phase observation writes nothing when nothing changed

One record per **transition**, not per reconciliation pass — the same shape of rule as
`dispatch.at_capacity` above, and a documented summarisation rather than an exception.

With a 60-second reconciliation cycle and sessions that run for hours, a record per pass
would be some thousands of lines a day saying a phase did not change, burying the handful
that say it did. Everything about the progression is still recorded: every transition, with
its time, its rungs, and the directory it came from. The pass summary in `reconcile.pass`
carries `speckit_phase_changes`, so a cycle in which nothing moved is still accounted for.

An item that reports no phase at all is a legitimate resting state, not a fault — the prompt
leaves the judgement of whether an issue warrants the lifecycle to the session, and a session
that decides a typo fix does not need four phases produces exactly this. `robot-army show`
explains the one case where silence is otherwise mysterious: a Spec Kit worktree whose
baseline was never recorded, which can never report a phase and says so.

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
| A **notification never attempted** because the process died between a state transition and the send | The state change itself is fully recorded, so nothing the system *did* is unreconstructable. What is lost is the knowledge that I was not told. Closing it would need a durable outbound queue with its own retry and persistence, which is more machinery than an optional stretch feature is worth — the gap is named here rather than hidden |
| A **passing** dispatch-time clone re-verification | The worktree-creation record that follows on the same item milliseconds later already implies it passed, so a record here would be one line per dispatch answering a question the next line answers anyway. Every **failure** is logged, and the two that mean the machine changed under an approval also raise an anomaly. See the milestone 005 section above |
| A reconciliation pass in which **no lifecycle phase changed** | Same disproportion as the heartbeat: a 60-second cycle against work that moves every few hours. Every transition is recorded individually, and the pass summary counts the changes, so nothing about the progression is unreconstructable. See the milestone 007 section above |
| The individual **file reads** behind Spec Kit detection and phase observation | Four `stat` calls per dispatch and a handful per active item per cycle. They change no state outside the process, and the *decision* they support is logged — logging each read would bury it |
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
