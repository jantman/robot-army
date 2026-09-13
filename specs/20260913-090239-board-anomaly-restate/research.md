# Research: A Board Anomaly Always Names the Board's Current Failure

## R1 — Restate in place, rather than key the row on which checks failed

**Decision**: When the board fails and an open `board_precondition` row exists for that board, its
detail is replaced with the current failures. The row's identity stays `(kind, board, dry_run)`.

**Rationale**: The issue's defect is a stale *reason*, not a row count. Keying on the failed check
names would raise a correct second row but leave the first listed beside it, still saying the board
is public, so the list would go on naming a resolved cause until someone acknowledged it. One row
per broken board is also how the operator thinks about it: "the board is broken, because …".

**Alternatives considered**: Adding the sorted failed check names to the dedup identity (the
issue's first suggestion). It needs either a new column in the partial unique index (a migration,
the third rebuild of that index) or a synthetic `entity_id`, which would stop the entity being the
board. Both cost more and still leave the stale row.

## R2 — What counts as "different"

**Decision**: The stored detail and the detail this failure would write are compared as parsed
JSON. Any difference restates. Equal means no write at all.

**Rationale**: FR-002 says names *and* details. A "tag exists" failure whose detail quotes the
labels the board does have is stale if that list changed, so comparing names alone would let the
text drift, which is the defect. The other two fields in the detail (`board_id`, `consequence`)
are constant for a board, so comparing the whole object costs nothing and cannot miss a field
added later. `check_board` produces checks in a fixed order, so list equality is order-stable.

**Alternatives considered**: Comparing the set of failed check names only. It is what the issue
names as the dedup key, but it keeps a stale detail line, as above.

## R3 — The detection time moves to the restating check

**Decision**: A restatement sets `detected_at` to now. The previous value goes into the audit
record.

**Rationale**: `anomalies --since`, `status` and `/anomalies` all order and filter by `detected_at`.
Left at the original time, a restated anomaly would be missing from `--since 10m` right after the
cause changed, and would sort beneath older rows, which is a quieter version of the same defect.
The row describes the current reason, and that reason was detected now. When the board first broke
is still recoverable from the log (`previous_detected_at`, and the first `trello.board.check`
error).

**Alternatives considered**: A new `restated_at` column, which is a migration plus teaching three
surfaces a second timestamp, for one kind. Rejected under Principle I. Keeping `detected_at`
unchanged was also rejected, for the reason above.

## R4 — Two narrow database functions; `raise_anomaly` is unchanged

**Decision**: `db.open_anomaly(conn, *, kind, entity_type, entity_id, dry_run=False)` returns the
open row or `None`. `db.restate_anomaly(conn, anomaly_id, detail)` rewrites `detail` and
`detected_at` only where the row is still open (`acknowledged_at IS NULL AND resolved_at IS NULL`)
and returns whether it changed anything, the same shape as `resolve_anomaly` and
`acknowledge_anomaly`. `intake.board_disabled_anomaly` is the only caller of either.

**Rationale**: `INSERT OR IGNORE` is correct for every other kind: their detail is evidence of the
first detection (a pid, a path, a card), and overwriting it on each pass would lose that evidence.
Turning `raise_anomaly` into an upsert would change seventeen call sites to fix one.

**Alternatives considered**: An `ON CONFLICT … DO UPDATE` inside `raise_anomaly` behind a flag. It
is a knob with one caller (Principle I), and SQLite's upsert against a *partial* unique index needs
the index's `WHERE` restated in the conflict target, a subtlety worth avoiding.

## R5 — The restatement is recorded as `anomaly.restated`

**Decision**: Written inside the same transaction as the update, after it succeeds, the way
`anomaly.resolved` is. Outcome `ok`, entity `anomaly/<id>`, detail: `kind`,
`anomaly_entity_id` (the board), `previous_failed_checks`, `failed_checks`,
`previous_detected_at`, `reason`. An unreadable stored detail gives `previous_failed_checks: null`
and `previous_detail_unreadable: true`.

**Rationale**: A restatement overwrites the only copy the database has of the old reason, so under
Principle III the log must hold it. A distinct action, rather than a flag on `trello.board.check`,
keeps "the anomaly changed" findable by grepping the log for one action name.

**Alternatives considered**: Relying on the per-pass `trello.board.check` records. They already
carry every failure, but not which anomaly row said what, or when that row's text changed.

## R6 — Interruption

**Decision**: Lookup, update and record share one `BEGIN IMMEDIATE` transaction.

**Rationale**: The lookup and the write cannot be split by a concurrent `anomalies --acknowledge`,
and the `still open` guard in `restate_anomaly` makes a lost race a no-op rather than a write to an
acknowledged row. Killed before commit, the row keeps its previous text and the next start restates
it. Killed after the record is appended but before commit, the log shows a restatement the database
did not keep, and the next start writes it again: one extra line, never a missing one, the same
trade `anomaly.resolved` makes.

## R7 — Out of scope, deliberately

- Retracting the anomaly when the board passes. A fifth self-resolving kind is a separate decision
  with its own docs (`state.md`'s "Four kinds resolve themselves").
- Checking the board periodically rather than at startup.
