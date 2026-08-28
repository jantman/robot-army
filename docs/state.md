# State: where it lives and what survives a reboot

## The layout

XDG variables are honoured when set; these are the defaults.

| Path | Contents | Survives reboot |
|---|---|---|
| `~/.config/robot-army/config.toml` | Configuration. **Read-only to the daemon** | yes |
| `~/.local/state/robot-army/state.db` | SQLite: work items, sessions, repos, anomalies, poll state | yes |
| `~/.local/state/robot-army/logs/audit-*.jsonl` | The audit log | yes |
| `~/.local/state/robot-army/logs/sessions/<item>.log` | Per-session wrapper log | yes |
| `~/.local/state/robot-army/spool/exits/` | Exit records awaiting the daemon | **yes — deliberately** |
| `~/.local/state/robot-army/spool/exits/rejected/` | Quarantined malformed records | yes |
| `~/.local/state/robot-army/heartbeat.json` | Liveness evidence | yes (and immediately stale) |
| `~/.local/state/robot-army/daemon.lock` | Single-instance lock | file yes, **lock no** |
| `~/.local/state/robot-army/requests/{poll,reconcile}` | Empty markers asking the daemon to run a job now | yes — harmlessly |
| `/run/user/<uid>/robot-army/<item>.sock` | Session host sockets | **no — deliberately** |
| `~/worktrees/<repo>/issue-<n>/` | Isolated checkouts | yes |

Two entries in that table are load-bearing rather than arbitrary:

**Sockets live under `XDG_RUNTIME_DIR` because it is tmpfs and cleared on reboot.** A dead
socket from a previous boot is noise that reconciliation would otherwise have to reason
about; letting the kernel delete them is free. Sockets that survive *within* a boot because
a session died uncleanly are detected by probing — never by trusting that the file exists.

**Exit records survive on purpose.** The wrapper writes them as files rather than POSTing
them to the daemon, because a POST to a daemon that is down loses the record permanently —
and the daemon is legitimately down during restarts, upgrades, and reboots. A lost record
would silently downgrade a clean completion into a phantom that reconciliation could only
ever classify as `interrupted`. This is the single deliberate departure from the planning
document; the reasoning is [research.md R5](../specs/001-minimum-daemon/research.md).

**The lock file survives but the lock does not.** `flock` is released by the kernel when
the holding process dies by any means, including `SIGKILL`. A stale `daemon.lock` file
containing an old PID is normal and harmless.

**Job request markers survive on purpose, and cost at most one redundant job.** They are
empty files whose whole lifetime is one tick, written by `robot-army poll` / `reconcile` and
by the web controls when a daemon holds the lock, and unlinked by the daemon at the top of
its next tick. A marker left over from before a reboot causes one extra poll on the next
start — and a startup already polls and reconciles, so the cost is nil. Only `poll` and
`reconcile` are valid names; anything else in that directory is **left in place and reported
once**, because deleting something the system does not understand is worse than leaving it.

```bash
ls ~/.local/state/robot-army/requests/     # empty almost always; a marker lives ~5s
```

Why a file and not a signal: the daemon may be mid-tick, and a marker waits without needing
a handler in a loop whose entire design is "one thread, no interleaving". Signalling would
also mean reading a PID out of the lock file and trusting it, which is the
identify-the-process-by-weaker-evidence pattern this project has already been bitten by.

## The configuration / state split

`config.toml` holds what I **declare**. The database holds what the system **observed**.

A repository's base branch and preparation steps are configuration and never appear in the
database. Its onboarding approval, the fingerprint of its committed permissions, and when
its trust was last verified are observations and never appear in the config.

That split is what keeps the config hand-editable with no migration story, and keeps the
database free of values I would expect to change by editing a file.

**The clone path sits on the state side of that line, and it is the one exception worth
understanding.** Milestone 005 made onboarding sufficient: a repository's clone location is
derived from `[paths] repo_root`, verified against the clone's actual `origin`, and the
**outcome** is recorded in `repos.clone_path` at the moment I approve it. Nothing re-derives
it afterwards. That is deliberate — a rule evaluated later can produce a different answer
than the one I approved, and the wrong answer here is not an error message, it is a real
clone of a real repository. A `[repos.*]` `path` is still configuration, but after
onboarding it is an *input* to the next approval rather than the answer: if it disagrees
with the recorded path, dispatch blocks and names `onboard --reapprove` instead of quietly
switching to either one.

## The database

SQLite, WAL mode, `foreign_keys=ON`, `synchronous=FULL`. Seven tables. Directly inspectable:

```bash
sqlite3 ~/.local/state/robot-army/state.db '.tables'
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT id, state, repo_key, issue_number, failure_reason FROM work_items'
```

WAL mode is what lets `robot-army status` read while the daemon holds a write connection.
`synchronous=FULL` is because the machine loses power; the throughput cost is irrelevant at
this write volume.

Schema changes are a forward-only `PRAGMA user_version` ladder. Each migration runs in a
transaction and advances the version as its last statement, so a process killed mid-migration
leaves the version unadvanced and the whole migration re-runs on the next start.

```bash
sqlite3 ~/.local/state/robot-army/state.db 'PRAGMA user_version'
```

There are no downgrades. The rollback plan is restoring the file from backup.

### `repos` — what I approved, and where

One row per onboarded repository. **The row is what makes a repository known**: since
milestone 005 the polled set, the dispatchable set, and what `robot-army repos` lists all
come from this table, and a `[repos.*]` section with no row here is a set of overrides for a
repository the system does not watch.

| Column | Added | Meaning |
|---|---|---|
| `repo_key` | 001 | `owner/name`, primary key |
| `onboarded_at` | 001 | first approval |
| `settings_fingerprint` | 001 | approved committed-settings hashes; `NULL` means there were none |
| `fingerprint_approved_at` | 001 | last approval of that fingerprint |
| `trust_verified_at` | 001 | when the worker trust dialog was last seen accepted |
| `clone_path` | 005 | the location approved, absolute and with symlinks resolved |
| `path_source` | 005 | `derived` or `configured` — which answer produced `clone_path` |
| `verified_origin` | 005 | the **normalised** `host/owner/name` found there |
| `origin_verified_at` | 005 | when that comparison last passed |

Two things about these four are worth stating rather than inferring.

**`verified_origin` stores the normalised identity and never a raw URL.** A git remote URL
may embed credentials (`https://user:token@host/owner/name`), and this column is read back
into `robot-army repos` output and every JSON view of it. Normalisation strips the
`userinfo@` component before anything else, so the stored value cannot carry a secret. The
same rule applies to the audit log and to every refusal message.

**A `NULL` `clone_path` means *onboarded, location never verified*** — a row written before
migration 005 — and **not** "onboarded at an unknown path". Nothing backfills it. Writing a
path nobody approved into an approval record is the one thing this table exists not to do,
so dispatch refuses for such a repository and names `onboard --reapprove` as the fix. That
is one command per repository, and it is the honest price of never guessing.

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT repo_key, clone_path, path_source, verified_origin FROM repos'
```

### `dispatch_control` — is dispatch paused?

Added by migration 002. **One row, and the `CHECK (id = 1)` makes a second one impossible
rather than merely unlikely** — "which of the two pause rows is authoritative" is a question
that must never be askable.

| Column | Meaning |
|---|---|
| `paused` | `0` or `1`. Read by the daemon before **every** dispatch decision |
| `paused_at` | When it was set. `NULL` when not paused |
| `paused_by` | `web` or `cli` — so "who stopped dispatch" is answerable from state as well as from the log |

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db 'SELECT * FROM dispatch_control'
robot-army pause      # and `unpause`
robot-army status     # reports it, as does heartbeat.json and every web view
```

While paused the daemon still polls, evaluates eligibility, reconciles, and heartbeats. It
starts no new session, and eligible items **accumulate in `ready`** — nothing is rejected and
nothing is lost, so lifting the pause needs no unwinding.

It is in the database rather than in a config key or a marker file because durability across
restart and reboot is the entire point: a pause that lapses when the daemon restarts is worse
than no pause, because I would believe work was held when it was not. It is also an
*operational act* rather than something I declare, which is the line the section above draws.

### `cards` — the intake board, and the mapping to what each card became

Added by migration 003. A table of its own rather than columns on `work_items`, and the
reason is concrete: `work_items.repo_key` is `NOT NULL REFERENCES repos(repo_key)` and
`issue_number` is `NOT NULL`, while a card awaiting clarification has neither by definition.
Accommodating it there would have meant rebuilding the central table to weaken an invariant
every other row depends on. The mapping also has to *outlive* any work item — a card's issue
may sit unlabelled for weeks, and may be refused at onboarding and never become a work item
at all.

`repo_key` here is deliberately **not** a foreign key into `repos`. A card may name a
repository that is configured but not onboarded (such a card still gets an issue, because
creating one is not dispatching), and may name one that is configured and later removed. A
foreign key would either forbid the row or delete the mapping — and deleting a mapping is how
a duplicate issue gets created.

| Column | Meaning |
|---|---|
| `board_id`, `card_id` | The card's identity on the board |
| `state` | `discovered` → `needs_info` \| `creating` → `linked`, or `dropped`. See below |
| `repo_key`, `issue_number`, `issue_url` | The mapping, once one exists |
| `reason` | Why this card is held, or why creating its issue keeps failing |
| `commented_reason` | The last reason actually written **onto the card** |
| `last_activity` | The board's activity stamp, as the rescan baseline |
| `origin_list_id` | Where the card was before we ever touched it |
| `placed_list_id` | Where we last put it |
| `pending_move_to` | Where we were *about* to put it |
| `current_list_id`, `current_list_name` | Where it is **now**, refreshed every poll (migration 006) |
| `comment_posted_at` | When the marker comment was posted |
| `intent_at` | When creation was attempted — the bound on crash recovery |

**Four list columns, because they answer four different questions.** Three of them are about
the past — where the card started, where we put it, where we were about to put it — and only
`current_list_id` is about the present. Milestone 006 needed the present one: a tagged card in
a column named by `[trello] ignore_lists` is not intake, and `robot-army cards` has to be able
to say so with the board unreachable. `NULL` means *tracked before migration 006 and not yet
re-polled*, which is treated as not parked.

The **name** is stored beside the id because its two consumers cannot share one
representation. The intake gate runs inside the poll, where the board's id-to-name map is in
hand, and wants an id: an equality check that is duplicate-safe (Trello permits two columns of
the same name) and survives a rename mid-run. The listing commands run where the board is not
available at all and can only compare against the names in the configuration. Both values are
written by the same statement from the same poll, so they cannot disagree.

**The `§11` invariant is two unique indexes, not a rule the create path follows.**
`idx_cards_identity` is unique on `(board_id, card_id, dry_run)`, and the partial
`idx_cards_issue` is unique on `(repo_key, issue_number, dry_run)` where `issue_number IS NOT
NULL`. A create path that skipped its mapping check does not produce a duplicate; it produces
an `IntegrityError`, which is loud. That is the difference between an invariant and a
convention. `dry_run` is part of both keys, exactly as it is for `work_items`, so a simulated
run and a later live run of the same card coexist rather than the rehearsal suppressing the
real thing.

**`reason` versus `commented_reason`** is the whole of the one-comment rule: comment when
they differ, stay silent when they do not. A card held for weeks accumulates one comment, not
one per poll — and a card whose *problem* changes gets a second comment saying so, which a
simple "have we commented?" flag would get wrong.

**Three list columns, not one.** `origin_list_id` is what an abandoned card is returned to;
`placed_list_id` is what detects a move by me; `pending_move_to` is written *before* a move is
attempted, so a move of ours that was interrupted after it landed is not mistaken for one of
mine on the next pass. Getting that backwards would freeze a card's lifecycle at the first
interruption.

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT card_id, state, repo_key, issue_number, reason FROM cards'
robot-army cards                    # the same thing, readably
robot-army rescan --all-needs-info  # look again at everything held
```

### `work_items` cleanup columns — what happened to a finished item's disk

Added by migration 004. Three nullable columns rather than a table: one row per item, one
shot, no lifecycle of its own, so a table would have been a join for nothing.

| Column | Meaning |
|---|---|
| `cleanup_state` | `NULL` \| `done` \| `branch_retained` \| `retained` \| `skipped`. See the table below |
| `cleanup_reason` | Why — git's own refusal message, the containment evidence, or the session that was live |
| `cleaned_at` | When the decision was recorded |

| `cleanup_state` | Worktree | Branch | What the next pass does |
|---|---|---|---|
| `NULL` | — | — | Considers it, if eligible. Every pre-migration row reads this way, and so does every row while `[cleanup] on_issue_close` is false |
| `done` | removed | removed | Nothing |
| `branch_retained` | removed | kept | Nothing — a retained branch is a *decision*, not a pending step |
| `retained` | kept | kept | Nothing automatically; `robot-army cleanup <id>` reconsiders |
| `skipped` | kept | kept | Reconsiders it — a session was live, which means "not yet" |

`skipped` is the only non-`NULL` value the automatic pass revisits, and that is the entire
point of distinguishing it from `retained`: one means "not yet", the other means "we looked
and decided no".

**`work_items.state` is untouched and `WORK_ITEM_TRANSITIONS` gains no entries.** `done` is
terminal and means the *work* is finished; whether its disk has been reclaimed is a different
axis — the same separation this project already makes between work state and session state.
Adding a `cleaned` state would have made every existing query that treats `done` as terminal
subtly wrong.

**`worktree_path` and `branch` are never nulled**, not even after a successful removal. The
record has to retain what was removed: `_sweep_worktrees` keys on the path being present, and
"what was at this path?" is exactly the question a `branch_retained` row has to answer months
later.

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT id, state, cleanup_state, cleanup_reason FROM work_items WHERE cleanup_state IS NOT NULL'
```

### `work_items` spec-kit columns — how far a Spec Kit run has got

Added by migration 007. Four nullable columns, no table, no state machine: a phase is not a
state and `WORK_ITEM_TRANSITIONS` gains no entries, deliberately — nothing decides anything
on these (FR-016), they only display.

| Column | Meaning |
|---|---|
| `speckit_baseline` | JSON array of the feature directory names present in the worktree **when it was created**. `NULL` means none was recorded |
| `speckit_phase` | `NULL` \| `specify` \| `plan` \| `tasks` \| `implement` — the last rung derived |
| `speckit_feature_dir` | The worktree-relative directory it was read from, e.g. `specs/007-speckit-extensions` |
| `speckit_phase_at` | When the transition was recorded |

**`speckit_baseline` is the one that earns its place.** A fresh worktree of a repository that
uses Spec Kit contains every feature it has ever shipped — in this repository, six
directories, each with a spec, a plan, and a `tasks.md` full of ticked boxes. Deriving a
phase from "which artifacts exist" would therefore report `implement` the instant a worktree
was created, on every item, forever. Recording what was there at the start makes "a directory
that was not here before" mean "this session's feature", with no heuristics and no
timestamps, because `/speckit-specify` always creates a new one.

`NULL` and `[]` are different answers and must not be conflated. `[]` is a Spec Kit worktree
with no features yet, so every directory that appears belongs to this item. `NULL` is *no
baseline was recorded* — a row predating the migration, or a preparation that died before its
transaction committed — and such an item never reports a phase at all. `robot-army show` says
so in as many words rather than leaving the silence to be puzzled over.

**Observation never clears these columns.** A worktree removed by milestone 004's cleanup, a
deleted artifact, an unreadable directory: all of them leave the last recorded phase
standing. It is history at that point, and the log has no way to restore what clearing would
delete.

The phase is a **cache whose only job is transition detection** — "did this change since I
last looked" is unanswerable without the previous value. The worktree stays the source of
truth and every reconciliation pass re-derives from it, so a daemon that was down for hours
reports the right phase on the next pass without having watched a single intermediate step.

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  'SELECT id, state, speckit_phase, speckit_feature_dir FROM work_items WHERE speckit_phase IS NOT NULL'
```

### The board's poll bookkeeping lives in `poll_state`

Under the synthetic key `trello:board:<board_id>`. `poll_state` has no foreign key and no
consumer that renders its rows as repositories, so a non-repository key is safe and a second
identically shaped table would have been a table added to satisfy a naming preference.

The `etag` column stays `NULL` for that row. Trello offers no usable conditional request on
the endpoint the board poll needs, so the ETag economy that makes a 60-second GitHub poll free
does not exist here — which argues for a longer interval rather than a cleverer mechanism.
The board poll defaults to **300 seconds** against GitHub's 60.

```bash
sqlite3 -header -column ~/.local/state/robot-army/state.db \
  "SELECT * FROM poll_state WHERE repo_key LIKE 'trello:%'"
```

### The card creation sequence, and where it can be killed

Creating an issue from a card is four steps, each in its own transaction, because every seam
between them has to be separately resumable:

1. `INSERT` a `cards` row in `creating` with the resolved repository and `intent_at`.
2. Create the issue. Its body carries the card's URL.
3. `UPDATE` the row with the issue number and URL, state `linked`. **This is the mapping.**
4. Comment on the card with the issue URL, then record `comment_posted_at`.

The dangerous window is between 2 and 3: the issue exists and nothing local knows it. Recovery
for a row found in `creating` lists issues in the target repository created since `intent_at`,
authored by me, and looks for the card's URL in the body. Listing, **never search** — GitHub's
search index lags by minutes, so an issue created two seconds before a crash may be invisible
to it, producing exactly the duplicate the mechanism exists to prevent.

**The known limit, stated rather than papered over.** A crash between steps 2 and 3 *combined
with* total loss of the database leaves an issue nothing can find: no mapping, no intent row,
and no card comment. The next poll creates a second issue. This is a double failure, it is
recoverable by hand — the stray issue is visible in the repository and is unlabelled, so it
dispatches nothing — and closing it would mean scanning every configured repository's recent
issues before every creation, a cost paid on every card forever. If it ever actually happens,
revisit it then.

Database loss on its own is covered: each card carries a marker comment naming its issue, and
the next poll restores the mapping from it one card at a time. The marker is a **recovery
marker, not the primary key** — with a mapping row present the card's comments are never read
at all.

## What a reboot does

Every session is gone; every `active` row still says `active`. On the next start:

1. The spool drains first, so any session that exited cleanly before the reboot is recorded
   as `awaiting_review` rather than mistaken for one that vanished.
2. Reconciliation runs **before any dispatch**. For each remaining `active` item it finds no
   live process and no exit record, and moves it to `interrupted`.
3. Stale sockets are already gone, because `/run/user` is tmpfs.
4. Worktrees are still on disk, on their branches, with whatever the session had written.

None of this raises an error-level record. A reboot legitimately produces one `interrupted`
item per session that was running, and treating that as an error would train me to ignore
errors.

**A pause survives all of this.** If dispatch was paused before the reboot it is still paused
after it, and only `robot-army unpause` or the web control clears it — never time, never a
restart. That is the whole reason it lives in the database.

Nothing resumes automatically. `robot-army status` lists what is waiting; `robot-army show
<id>` reports whether the worktree has uncommitted changes, whether the branch has commits,
whether the issue is closed, and whether a PR is open — computed on demand, never stored,
because a stored copy would be wrong the moment I touched the directory.

## Interrupted at X → result on next start

The full table is in
[data-model.md](../specs/001-minimum-daemon/data-model.md#interruption-behaviour). The
summary:

| Interrupted at | Result |
|---|---|
| After the `discovered` insert, before evaluation | Row in `discovered`; re-evaluated on the next poll |
| During worktree creation or a preparation step | Item in `dispatching`; failed at max age with whatever output exists. The partial worktree is reported, never reused blindly |
| After the session row insert, before launch | Confirmation window elapses; session `lost`, item `failed` |
| After launch, before confirmation | Either the registry scan confirms it late, or the window elapses and the orphan sweep catches the live process |
| After the worker exits, before the spool drains | The file persists and is applied on the next tick or next startup |
| After applying a spool record, before unlinking it | Reapplied; application is idempotent |
| Mid-migration | The migration re-runs; `user_version` never advanced |
| Mid-audit-write | A partial final line is skipped and counted by readers |
| Mid-`set_dispatch_paused` | Rolled back; dispatch continues as before. The pause is never half-applied |
| After a card's intent row, before its issue exists | Row in `creating`; the listing finds nothing and step 2 is retried |
| After a card's issue, before its mapping | Row in `creating`, issue orphaned; the listing since `intent_at` finds it by the card URL in its body and adopts it |
| After a card's mapping, before its comment | Row `linked`, `comment_posted_at` NULL; the next pass checks the board for an existing marker, then posts |
| After a card move landed, before it was recorded | `pending_move_to` matches where the card actually is, which identifies the move as ours rather than mine |
| Between a card write and its `last_activity` refresh | One redundant re-evaluation, which is idempotent and posts no comment because `commented_reason` is unchanged |
| With the database lost entirely | Each card's marker comment restores its mapping on the next poll. The residual gap is the double failure above |
| After writing a request marker, before the daemon read it | The marker persists and is consumed on the next tick or the next start |
| After the daemon unlinked a marker, before running the job | The forced flag is lost and the job runs on its ordinary interval. Accepted: the cost is one interval, and unlinking *after* would risk running it twice |
| Web process killed mid-request, before the transaction committed | Rolled back. The browser sees a dropped connection and the item page tells the truth on reload |
| Between the worktree existing and its spec-kit baseline being recorded | Cannot happen: the baseline commits in the same transaction as `worktree_path` and `branch`, so a kill before it redoes the whole preparation |
| After a `speckit.phase` line was written, before its row committed | The next pass re-derives the same phase from the same files and records it again. A duplicated line at worst, never a change with no record of it |
| Mid-observation | Nothing is written until the derivation completes. The next pass re-reads the same worktree |
| Web process killed while a worker thread was dispatching | The item is left mid-dispatch and reconciliation resolves it — the same path any interrupted dispatch takes. This is not a new way to produce that condition |
| After a capacity snapshot, before the dispatch it authorised | Nothing written. The next pass takes a fresh snapshot; a snapshot is never stored, so it cannot be stale |
| Between two dispatches in one pass | Earlier items dispatched, later ones still `ready`. The next pass re-observes and re-plans — `select_and_dispatch` holds no cross-pass state |
| During a capacity hold, before its signature was recorded | No `dispatch.at_capacity` record for this hold. The next pass sees no remembered signature and writes one. Worst case is one extra record, never a missing hold |
| After `git worktree remove`, before the branch half | Worktree gone, branch present, `cleanup_state` unwritten. The next pass finds the directory already absent, treats git's "not a working tree" as a refusal about its *record* rather than about the contents, completes the branch half, and records the outcome |
| After both cleanup removals, before the row is written | Both gone, `cleanup_state` still `NULL`. The next pass re-attempts, both steps refuse harmlessly, and the row is written `done` |
| During the containment fetch | Nothing removed. Containment is unproven, so the branch is retained and the item is reconsidered. The failure direction is always *keep* |
| After a state transition, before its notification | State committed and logged; no message sent. The state change is fully reconstructible; the lost message is the named gap in [logging.md](logging.md) |
| Mid-notification, after the POST left | Possibly delivered, recorded as attempted with its outcome. **No retry** — a duplicate notification is noise, and a retry loop is a Principle IV violation |

## Disk

A prepared worktree was measured at up to **499 MB** once a virtualenv exists. Automatic
removal exists as of milestone 004 and is **off by default** — deleting work is irreversible,
and the Operating Constraints require irreversible actions to be unreachable until asked for.
`doctor` still warns when free space is low.

```bash
robot-army worktree list        # sizes, conditions, and cleanup state
robot-army cleanup              # every eligible item, under both guards
df -h ~/worktrees
```

With `[cleanup] on_issue_close = true`, an item whose issue has closed has its worktree and
branch reclaimed on the next reconciliation pass — unless anything in either exists nowhere
else, in which case it is kept and the reason is on the row. See the `cleanup_state` table
above and the README's "Cleaning up" section.

Worktrees live at `~/worktrees/` rather than under `~/.local/state` so they stay out of any
backup set aimed at `~/.local` — which matters at half a gigabyte each — and sit at a short
top-level path I will find without looking it up.

## Backing up

Worth keeping: `~/.config/robot-army/config.toml` and
`~/.local/state/robot-army/state.db`. The logs are worth keeping if the point is an audit
trail. The worktrees are reproducible from the branches, which live in the repositories.

Copy the database with SQLite rather than `cp`, so a concurrent write cannot produce a torn
file:

```bash
sqlite3 ~/.local/state/robot-army/state.db ".backup /tmp/robot-army-backup.db"
```
