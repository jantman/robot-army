# Research: Parked Is Judged by the Daemon's Ignore List

Nothing in the Technical Context was unknown; these are the decisions the design turns on.

## R1 — Where the ignore list in force comes from

**Decision**: the daemon publishes the ignore list it loaded on every heartbeat, as a first-class
`ignore_lists` field beside `max_concurrent_sessions`. Every other surface judges parkedness against
the published list when it can be believed (R2), and against its own configuration otherwise.

**Rationale**: the defect is issue #30's. `robot-army web` reads its configuration once at startup,
and a web interface started before `Icebox` was configured holds an empty ignore list for its whole
life. The daemon is the process that parks cards — `intake` excludes them and records
`trello.parked` against the daemon's configuration — so the daemon's list is the true answer to "is
this card parked?", in the same sense that the daemon's cap is the true answer to "how many sessions
may run?". A daemon's configuration is fixed while it runs, so a published list cannot go stale
under a running daemon.

**Alternatives considered**:

- *Have the web interface reread the configuration file per request, or on mtime change.* Rejected
  for the reason issue #30 gave: the file is not the authority. After an edit that nobody has yet
  restarted the daemon for, the file names columns the daemon is still evaluating, and a surface
  reading the file would call those cards parked while the daemon is commenting on them.
- *Store a `parked` flag on the card row, written by the poll.* Rejected: `card_is_parked` is
  "derived, never stored" deliberately, and a flag written only when a card crosses a column would
  not change when `ignore_lists` is edited without the card moving.
- *Fix only the web.* Rejected by spec User Story 2: the terminal can disagree the other way (edited
  file, daemon not restarted), and the issue's second complaint is that the two surfaces disagree.

## R2 — When a published list is believed

**Decision**: `health.published_ignore_lists(report, *, running, lock_holder)` with exactly
`published_cap`'s conditions — a daemon holds the lock, the heartbeat's pid is the lock holder's,
stale heartbeats from that holder included — and a value shape check: a JSON list whose members are
all non-empty strings. An empty list is believed (the daemon is parking nothing). Anything else,
including a missing key from an older build and `null` from a daemon with no board, is *not
published*, returned as `None`.

The pid-match rule is factored into one private helper both functions call, so the restart-window
reasoning in `published_cap`'s docstring is enforced in one place rather than copied.

**Rationale**: every doubt fails to "use your own configuration", which is what the cap already
does. The shape check mirrors `published_cap`'s refusal to believe a value the loader could not have
produced; `config` refuses empty column names, so a heartbeat carrying one is not the daemon's.

**Alternatives considered**: returning `()` for "not published". Rejected: a daemon parking nothing
and a daemon that said nothing are different facts, and conflating them would make a daemon with no
`[trello]` section override a web interface that has one.

## R3 — How the list in force reaches `card_is_parked`

**Decision**: `card_is_parked(card, ignore_lists)` takes the column names rather than the whole
configuration. `operations` gains:

- `resolve_ignore_lists(config, published) -> tuple[in_force, configured_if_different]`, the one
  place that decides whether there is a disagreement, like `capacity._resolve_cap`. The surface's own
  list is `config.trello.ignore_lists`, or `()` without a `[trello]` section. Lists are compared as
  sets: order is not meaningful and `config` already treats a repeated name as one entry.
- `_published_ignore_lists(ctx)`, which takes its own reading of the lock and heartbeat, like
  `_enforced_cap`, for short-lived commands (`cards`, `show`).
- `cards(ctx, ..., published_ignore_lists=_OWN_READING)`. The default takes a reading; the web
  passes the request's single reading, which may legitimately be `None`, so the default is a sentinel
  rather than `None`.

**Rationale**: FR-005. `web.handle` already takes one reading of the daemon per request for the
effect level and the cap and hands it to everything on the page; the ignore list joins it through the
same `params` dictionary `capacity` travels in, so the two halves of a page cannot answer differently
across a daemon starting mid-request.

**Alternatives considered**: letting `cards` always take its own reading, on the web too. Rejected:
it would be a second observation of the heartbeat within one request, the thing issue #52 removed.

## R4 — The card on a work item

**Decision**: `_card_for_item` (used by `show`, and so by the web item page) resolves the list with
its own reading via `_published_ignore_lists(ctx)`, rather than having the request's reading threaded
through `show`.

**Rationale**: a card attached to a work item is `linked`, and `linked` is in `NEVER_PARKED`, so the
value is always `False` in practice; the existing comment there says it is derived rather than
hardcoded only so the two paths cannot disagree. Threading a parameter through `show` and
`item_view` for a value that cannot change the output adds more than it removes. The reading is only
taken when a `[trello]` section exists and a card was found.

## R5 — Saying so when the two disagree

**Decision**: one sentence, built once in `operations` and rendered verbatim by both surfaces, when
the list in force differs from the surface's own:

```
IGNORE LIST MISMATCH: the running daemon is parking cards in ['Icebox'], and this process is
configured for []. Parked cards are shown as the daemon decides, because the daemon is what parks
them. One of the two has been running since before the configuration changed — restart that one and
they will agree.
```

The terminal prints it after the table; the web renders it as a warning banner on `/cards` only. The
machine-readable payload carries `ignore_lists` (in force), `configured_ignore_lists` (non-`None`
only when it differs) and `ignore_list_disagreement` (the sentence or `None`).

**Rationale**: the cap's mismatch sentence (`CapacitySnapshot.cap_disagreement`) is the model,
including its refusal to say which process is stale: both directions are reachable and their remedies
are opposite. It is on `/cards` rather than in the chrome because it changes nothing any other page
shows.

## R6 — What this logs, and what happens if it is killed halfway

Nothing new changes state outside a process. The heartbeat gains a field and is still written
write-fsync-rename by `write_heartbeat`, so a partially written heartbeat is never observable; a
heartbeat from before this change has no `ignore_lists` key and is read as *not published* (R2). The
listings are read-only. No audit action is added or changed.
