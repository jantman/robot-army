# Phase 0 Research: Every verb that offers `--include-simulated` honours it

Everything below was settled against the code in this worktree at schema version 13. Nothing
here is a preference; each entry is a decision the implementation cannot be written without.

---

## R1 — Where an anomaly's rehearsed-ness comes from

**Decision**: a `dry_run` column on `anomalies`, `NOT NULL DEFAULT 0`, written by
`db.raise_anomaly` from a new keyword argument that each call site supplies.

**Rationale**: the alternative is to derive it at read time by joining the entity the anomaly
names, which is what the issue's own SQL demonstration does. That fails on three counts. It
needs a different join per `entity_type` — `work_item`, `session`, `card`, `repo`, `board`, and
`None` — so the "is this real?" question would have six answers maintained in one `CASE`
expression. It is undefined for the four kinds that name no entity or name the machine
(`capacity_unobservable`, `registry_version_unknown`, `malformed_exit_record`,
`orphan_exit_record`), and undefined is not a scope a default view can be built on. And it
changes its answer over time: `robot-army purge-simulated` deletes the simulated rows, at which
point every anomaly raised against one silently becomes "real" again — the exact class of
silent reversal Principle III forbids.

Recording it at raise time also matches how every other table in this system carries the
distinction, which is what `db.py`'s module docstring means by the default being *structural*.

**`NOT NULL DEFAULT 0` rather than nullable**, and the direction matters. Existing rows carry no
evidence of which run raised them and there is nothing to back-fill from. `0` reads as "real",
which keeps them visible (FR-010). This is the opposite choice from migrations 011 and 013,
which used nullable columns precisely so "never asked" stayed distinguishable — and the reason
is that here the two readings are not equally costly. Showing a real anomaly that might have
been rehearsed is a row the reader dismisses; hiding a rehearsed one that might have been real
is a condition nobody ever sees. Only one of those is recoverable.

**The keyword defaults to `False`** for the same asymmetry. A future call site that forgets to
pass it raises a visible anomaly, not an invisible one.

**Alternatives considered**: a `source_effect_level` string instead of a boolean, which would
record *which* level rehearsed it. Rejected under Principle I — the flag is a boolean, the
filter is a boolean, and no caller asks the finer question. `effects.py` already owns the level.

---

## R2 — Which call sites pass `dry_run=True`

All seventeen `db.raise_anomaly` call sites were read. They fall into two groups, and the split
is not "does it have a `dry_run` value in scope" but "is the condition it reports a fact about
rehearsed work or about the machine".

**Rehearsed when their subject is** (pass the row's own `dry_run`):

| Site | Kind | Value |
|---|---|---|
| `dispatch.py` `session_id_mismatch` | work item | `item.dry_run` |
| `reconcile.py` `_supersede` | `orphan_session` | `other.dry_run` (a sessions row) |
| `reconcile.py` orphan for a known session | `orphan_session` | `session.dry_run` |
| `reconcile.py` `dispatching_timeout` | work item | `item.dry_run` |
| `reconcile.py` `no_transcript` | session | `session.dry_run` |
| `reconcile.py` `config_missing_repo` | work item | `item.dry_run` |
| `reconcile.py` `prunable_worktree` | work item | `item.dry_run` |
| `intake.py` `card_create_failing` | card | `card.dry_run` |
| `intake.py` `card_issue_missing` | card | `card.dry_run` |

**Always real** (pass nothing; the default stands):

| Site | Kind | Why it is real whatever the level |
|---|---|---|
| `capacity.py` | `capacity_unobservable` | The count of live processes on this machine is unknown. Nothing was rehearsed about that, and dispatch is withheld while it holds. |
| `spool.py` | `malformed_exit_record` | A file on disk is corrupt. |
| `spool.py` | `orphan_exit_record` | An exit record names a session no row claims — so there is no row to ask. |
| `reconcile.py` | `registry_version_unknown` | The worker's registry format changed. A property of the installed worker. |
| `dispatch.py` gate | `clone_path_missing`, `clone_origin_changed` | The approved clone moved or became a different repository. The clone is real at every level; only what we *do* in it is rehearsed. |
| `intake.py` | `board_precondition`, `board_unreachable` | Board *reads* are real at every level — only writes are simulated — so both report a true fact about a real board. |

**The one genuinely undecidable site**: `reconcile._orphan_sweep`'s registry-scan branch raises
`orphan_session` for a live process under the worktree root that no row claims. There may be no
session row at all, so there is nothing to read a flag from. It stays real, and that is the
correct answer rather than a compromise: an unaccounted-for process is consuming a real slot on
a real machine whatever produced it, and the anomaly exists to say the machine is busier than
the database thinks.

---

## R3 — The partial unique index has to be rebuilt

**Decision**: `dry_run` joins the partial unique index, which is therefore dropped and recreated
exactly as migration 012 did.

```sql
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''), dry_run)
    WHERE acknowledged_at IS NULL AND resolved_at IS NULL;
```

**Rationale**: the index is what stops a 60-second loop writing 1,440 identical rows a day. Left
as it is, a rehearsed run and a real run reporting the same condition for the same entity
collide, and `INSERT OR IGNORE` silently keeps whichever arrived first — so a real anomaly could
be swallowed by a rehearsal that got there earlier, and would then be *invisible in the default
view*. That is strictly worse than the bug being fixed. The two are different facts about
different work and must be able to coexist, which is precisely what a fourth index column
expresses.

`COALESCE` is carried over unchanged and is still load-bearing: SQLite never compares two NULLs
equal, so indexing the bare columns leaves every entity-less anomaly colliding with nothing.

---

## R4 — How `anomalies` counts what it withheld

**Decision**: `db.list_anomalies` gains `include_simulated`, filtering in SQL via the existing
`_scope` helper. The withheld count comes from a second accessor,
`db.list_simulated_anomalies(conn, *, unacknowledged_only=...)`, returning the **rows** rather
than a count.

**Rationale**: `count_simulated_work_items` returns a number because every filter `status`
applies is a SQL filter. `anomalies` is not like that — `--since` is applied in Python by
`_within_window`, deliberately, because `detected_at` is TEXT and a malformed stamp compared
lexicographically in SQL would be dropped with nothing in a position to notice (milestone 012's
research R2). A `COUNT(*)` would therefore report a number the flag would not then reveal, which
is the exact equality milestone 008 built `_work_item_filters` to guarantee. Returning rows lets
the operation apply the identical `_within_window` predicate to both sets, so "withheld" and
"revealed" are the same population by construction.

The cost is fetching rows to count them, on a table that holds tens of rows on this machine and
is bounded by the partial index. That is the right trade against a number that can be wrong.

**Alternatives considered**: pushing the window into SQL so a `COUNT(*)` becomes exact. Rejected
— it would silently reintroduce the lexicographic-comparison bug 012 removed, in the one place
whose entire purpose is not silently dropping detected conditions.

---

## R5 — How `log` filters, and what "withheld" means for a bounded page

**Decision**: `_judge_record` gains `include_simulated`. A record whose `simulated` *or*
`dry_run` field is truthy is `_REJECT` when the flag is off. Both readers share it, as they
already share every other filter.

Two readers, two honest answers about the count:

- `read_log` scans every daily file, so it reports the true number withheld across the whole
  scan, scoped by whatever `--since` and `--item` were also in force.
- `read_log_page` stops the moment a page is full or its byte budget is spent. It reports the
  number withheld **from the records it scanned for this page**, and says so in those words.

**Rationale**: the alternative for the paged reader is to report nothing, or to report a
whole-history number the page did not measure. The first breaks FR-007 on the surface where the
951 rehearsed records were actually counted; the second states a figure the reader would
reasonably assume relates to the page in front of them. The scanned-region number is the only
one that is both useful and true, and the reader already lives with a bounded scan — the page
already prints "the scan stopped after N bytes" when it does.

**The rejection must happen inside the scan, not after it.** `read_log_page` fills a page by
scanning backwards until it has `limit` matches; if the simulated filter were applied to the
finished page, a page whose region is entirely rehearsed would come back empty while older
matching records remained. Putting it in `_judge_record` — where the other filters already are —
makes that impossible by construction.

---

## R6 — Why `repos` loses the flag rather than gaining a column

**Decision**: remove `repos` from the set of verbs the parser decorates. `robot-army repos
--include-simulated` becomes an argparse usage error, exit 2.

**Rationale**: `onboard` verifies a real clone on disk, computes a fingerprint from real `git`
output, and records the origin it actually found. There is no simulated onboarding path and
nothing in `effects.py` intercepts it, so the `repos` table cannot hold a rehearsed row. Adding
a `dry_run` column there would be a filter over a population that is empty by construction —
speculative generality with one caller and no second use, which Principle I names directly.

No deprecation path, per Principle V: this project maintains no backward compatibility for
outside consumers, and what is being removed never did anything.

**The web's site-wide toggle stays on `/repos`.** It is chrome carried across navigation — it
governs the nav links and the anomaly pill on every page — not a per-page filter, so suppressing
it on one page would make the reader's choice silently forgettable when they navigated through
it. The CLI flag and the web toggle are not the same control and are not required to have the
same surface.

---

## R7 — Making the guarantee testable rather than remembered

**Decision**: the set of verbs that carry the flag becomes a named constant in `cli.py`, and a
test drives every member of it end-to-end against a seeded state, asserting the two spellings
disagree.

**Rationale**: SC-006 asks for a test that fails if a future verb advertises the flag without
honouring it. Introspecting `_dispatch`'s lambdas to prove a value is threaded through is not
possible without reading source, and a test that reads source proves nothing about behaviour.
Driving each verb twice over a fixture holding rehearsed rows of every kind — a work item, a
card, an anomaly, a worktree, and audit records — proves the only thing that matters, and it
fails loudly the moment a seventh verb is decorated without being wired up.

The constant is what makes the set enumerable from the test without re-typing it, and makes the
parser's claim and the test's subject the same object rather than two lists that drift. This is
the same argument `_work_item_filters` carries for its own extraction.

---

## R8 — Where retraction of `card_create_failing` lives, and what re-establishes it as false

**Decision**: a `reconcile._resolve_card_create_anomalies` pass beside the existing
`_resolve_orphan_anomalies`, resolving an outstanding `card_create_failing` when the card it
names is in state `linked`.

**Rationale for the predicate**: `linked` is terminal, is reached only by the success path, and
that path writes `issue_number`, `issue_url` and `create_failures = 0` in the same transaction
as the transition. So "the card is linked" is a positively re-established falsehood of exactly
the condition the anomaly reported — the same standard `_resolve_orphan_anomalies` sets for
itself, and the reason it refuses to guess at any other kind.

**Rationale for the location**: reconciliation is the pass whose job is re-checking what has
settled, and it already hosts the sibling. Putting it in `intake` would tie retraction to a
board poll, so an anomaly would stay open whenever Trello was unreachable — a list going stale
for a reason unrelated to what it reports, which is the failure mode the whole retraction
mechanism exists to prevent. This check needs no network at all.

**Cards that cannot be found are left alone.** The anomaly's `entity_id` is the Trello card id;
the lookup is by `(card_id, dry_run)`, which `idx_cards_identity` makes unique in practice. A
purge, or a database restored without the card, produces no row — and "I could not check" must
never be written as "it is fine". Identical to how the orphan resolver treats an anomaly with no
recorded pid.

**Alternatives considered**: clearing the anomaly inline at the moment the card links. Rejected
— it would put anomaly bookkeeping inside the creation path, where an interruption between the
transition and the clear leaves the anomaly open with nothing to re-check it. The reconciliation
pass is idempotent and self-healing; the inline write is neither.

---

## R9 — What this feature logs, and what happens if it is killed halfway

Both questions the constitution's Development Workflow requires answered explicitly.

**Logged**: exactly one new outward-visible action, `anomaly.resolved` for the
`card_create_failing` kind — reusing the action name the orphan resolver already writes, with
the evidence in `detail`: the kind, the card, and the state that establishes the condition
false. `db.resolve_anomaly`'s `resolved_at IS NULL` guard means a repeated pass writes nothing
and therefore logs nothing (FR-014).

**Deliberately not logged**, and named here as Principle III requires: reading a listing. Every
change in US1–US3 is to *what a read returns*, and reads have never been logged in this system —
the audit log records actions that change state outside the process, and no command in this
feature does. The one write is the resolution above. The new `dry_run` value on an anomaly is
not a separate action either; it is a field of the raise, which is already logged by the caller
that raises it.

**If killed halfway**: the migration runs inside the migration runner's existing transaction, so
either version 14 exists with its rebuilt index or version 13 does. The resolution pass commits
one anomaly at a time under `db.transaction`, so a pass killed midway leaves the anomalies it
reached resolved and logged, and the rest outstanding for the next pass — the shape
`_resolve_orphan_anomalies` already uses, and idempotent by the `resolved_at IS NULL` guard.
Nothing in the listing changes carries state at all.

---

## R10 — Surfaces that must move together

Enumerated because the defect being fixed *is* a surface that was left behind:

| Surface | Change |
|---|---|
| `cli.py` | the named constant; `repos` drops out; `anomalies` and `log` receive the value |
| `operations.anomalies` | takes and applies `include_simulated`; reports withheld |
| `operations.status` | its anomaly block scoped by the value it already receives |
| `operations.read_log` / `read_log_page` | take and apply it; report withheld |
| `web/pages.chrome` | the anomaly pill counts within the scope the page was served |
| `web/pages.anomalies_view` | stops discarding the value it is handed |
| `web/pages.log_view` | stops discarding the value it is handed |
| `docs/guide/operating.md` | the anomalies and log sections; "not resolvable" is now wrong twice |
| `docs/guide/1-setup.md` | the list of verbs that state what they withheld |
| `docs/guide/audit-log.md` | the reader excludes rehearsed records by default |
| `docs/guide/state.md` | the new column and schema version 14 |
| `specs/001-minimum-daemon/contracts/cli.md` | the universal-rule line, amended as 008 amended it |

No configuration key is added or renamed, so `exampleconfig.py` and `share/config.example.toml`
are untouched — the drift test will confirm it.
