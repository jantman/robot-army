# Phase 1 — Data model

**No schema change. No migration. No new column, index, table or state.** This section exists
to record that as a finding rather than as an omission, and to name the one thing that is read
and the one thing that is written.

## Read: `work_items.pull_requests`

Added by migration 013 for issue #143 and unchanged here. A JSON array of
`{number, url, state}` sorted by number, plus `pull_requests_at` recording when the set was
last **successfully** confirmed.

Three states, and they are deliberately not two:

| Column value | Means | This feature reads it as |
|---|---|---|
| `NULL` | never looked up — a pre-013 row, an item never dispatched, a simulated item | no merged pull request |
| `'[]'` | looked up; GitHub reports none | no merged pull request |
| a non-empty array | looked up; these are the answers | merged **iff** some element's `state` is `merged` |

Unparseable text, a payload that is not a list, and non-object elements all reach the same
place: `WorkItem.pull_request_list` filters them out element-wise and returns `[]`. So every
failure mode reads as "no merged pull request", which is the direction that delays a retirement
rather than causing one.

`state` is lower-cased at the boundary (`boundaries.PullRequest`), giving `open`, `merged`
or `closed` for the three states GitHub defines today; a state it adds later passes through
lower-cased rather than being mapped to a guess. The predicate is therefore an exact match on
`"merged"`, and an unrecognised state reads as *not merged* — the direction that delays a
retirement.

### New derived predicate

`WorkItem.has_merged_pull_request` — a property over `pull_request_list`, true when any element
has `state == "merged"`. Derived, not stored. See research R9 for why it lives in `models.py`
rather than in `reconcile.py`.

**Any merged pull request counts, not the newest one.** A retried item can carry a
closed-unmerged attempt alongside the merged one, and the merged one is still the maintainer's
acceptance of the work.

## Written: one key on an existing audit record

`session.retire`'s `detail` gains `signal`, valued `merged_pull_request` or `quiet_period`
(FR-009). `idle_s` stays and keeps its meaning on both paths — it is now "how long it had been
idle", not "the reason it was allowed".

Nothing else about the record changes: same action name, same entity, still written **before**
the signal.

## Unchanged, and asserted by test

| | |
|---|---|
| `migrations.py` | no migration. Schema version does not move |
| `states.py` | no state, no transition, no edge |
| `capacity.py` | a live process still counts; retirement is what ends the process |
| `cleanup.py` | both guards unchanged |
| `db.list_pull_request_candidates` | unchanged — it already refreshes a `done` item whose stored pull request is `open`, which is what makes a delayed merge work |
| `boundaries/` | no new call, no new method, no new normalisation |
| `config.py`, `exampleconfig.py`, `share/config.example.toml` | no key added, so neither CLAUDE.md configuration step is triggered |
