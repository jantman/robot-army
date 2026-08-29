# Research: Status Never Contradicts Itself About Hidden Simulated Work

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-08-29

Six decisions. None required an unknown to be resolved from outside the repository — the whole
feature is a disagreement between two functions that are both already correct, so the research
is design choice rather than investigation.

---

## R1 — How the withheld count is derived

**Decision**: add one counting accessor per affected table in `db.py` —
`count_simulated_work_items(conn, *, states=None, repo_key=None)` and
`count_simulated_cards(conn, *, states=None)` — each a single `COUNT(*)` over rows with
`dry_run = 1`, taking the same filter arguments as the listing accessor it mirrors. `status`
calls it only when `include_simulated` is false; when the caller asked for simulated rows,
nothing is withheld and the count is zero by construction rather than by query.

**Rationale**: the number has to be *exactly* the number of rows the reveal flag would surface,
or the fix substitutes a subtler contradiction for the obvious one. Tying the count to the same
`_scope`/filter construction as the listing is what makes that equality structural instead of a
claim maintained by hand — the same reasoning that put `include_simulated` in the accessor
signatures in the first place, and the same reasoning `ordering.plan` uses to guarantee the
queue names the item dispatch will actually select.

A counting query also costs a row count rather than a row fetch, which matters slightly for the
web, since `web/pages.py` calls `operations.status` on page renders.

**Alternatives considered**:

- *Fetch with `include_simulated=True` and subtract the visible length.* Correct, needs no new
  accessor, but fetches every simulated row's full record to learn only how many there are, and
  does it twice — once for the counts scope and once for the listing scope.
- *Fetch once with simulated rows included and partition in Python.* One query instead of two,
  but it re-implements the `dry_run` predicate at the call site. `db._scope` exists so that
  exactly one place in the codebase decides what "simulated" excludes; `tests/unit/test_db_scope.py`
  exists to keep it that way. Hand-rolling the filter in `operations.py` works against both.
- *Derive it from the queue.* Wrong set. The queue holds only `ready` items; the listing spans
  every state and honours `--state`/`--repo`. The queue can neither over- nor under-count
  reliably.

**Note on the structural scope test**: `tests/unit/test_db_scope.py` asserts that every listing
accessor carries a keyword-only `include_simulated` defaulting to `False`. The new accessors
deliberately do **not** take that parameter — counting withheld rows *is* the simulated-only
question, and a `count_simulated_work_items(include_simulated=False)` would be nonsense. They
must not be added to that test's `LISTING_ACCESSORS` list; the name is chosen so the scope is
unmistakable at every call site, and the test file gains a comment saying so.

---

## R2 — Two withheld numbers, not one

**Decision**: compute and report the withheld count twice, once per section, because the two
sections query different sets:

- The **counts by state** section calls `count_work_items_by_state` with no filters at all. Its
  withheld count is therefore every simulated work item in the database.
- The **item listing** calls `list_work_items` with `states=` and `repo_key=` from `--state` and
  `--repo`. Its withheld count must carry the same filters.

**Rationale**: this is not a detail — it is the difference between a truthful number and a new
lie. Running `status --repo owner/other` while four simulated items sit under a different
repository must report zero withheld for the listing, because passing `--include-simulated`
would reveal none of them there. Reporting one number for both sections would be wrong in one
of them whenever a filter is in play.

The asymmetry is inherited, not introduced: the counts section has never honoured `--state` or
`--repo`. Making it do so is a separate behaviour change with its own justification burden and
is deliberately out of scope; this feature describes the command as it is rather than quietly
altering it.

**Alternatives considered**: a single number computed unfiltered and used in both places —
rejected as described. Making the counts section honour the filters so one number serves both —
rejected as scope creep that changes what the command reports rather than what it says about
what it withholds.

---

## R3 — No shared abstraction across the three commands

**Decision**: `status`, `cards`, and `worktree list` each build their own disclosure line
inline. No `Withheld` dataclass, no `render_withheld()` helper, no mixin.

**Rationale**: Principle I. The three sites differ in the table they describe, in the filters
they honour, and — for `worktree list` — in the fact that the set is defined partly in Python
(items with a `worktree_path`) rather than entirely in SQL. A helper general enough to cover all
three would take the count, the noun, the flag name, and the placement as parameters, which is
longer than the three lines it replaces and harder to read at each call site. Three similar
lines of formatting are not duplication worth removing; they are three sentences that happen to
rhyme.

If a fourth listing ever needs this, the helper can be extracted then, with three examples in
hand instead of an imagined shape.

**Alternatives considered**: a `Result.withheld(n, noun, flag)` method on the shared `Result`
type. Tempting because `Result` is already the common vocabulary, but it puts rendering policy
into a transport object whose whole present virtue is that it holds lines and data and decides
nothing.

---

## R4 — Wording and placement

**Decision**: one parenthetical, appended to the existing absence message when the section is
empty, and printed as its own line beneath the table when it is not. The exact strings are fixed
in [contracts/status-output.md](contracts/status-output.md).

Empty:

```
no work items (4 simulated rows withheld — pass --include-simulated to show them)
```

Non-empty, beneath the table:

```
4 simulated rows withheld — pass --include-simulated to show them
```

**Rationale**: three properties decide this. It must name the count, because "some rows hidden"
leaves the reader exactly as unable to reconcile the output as before. It must name the flag,
because the maintainer should not have to reach for `--help` to resolve a contradiction the
command created. And it must be one line, because the audience is one person reading a terminal
and a warning banner over four hidden rows is its own kind of unreadable — the same judgement
that put one hold reason per queue row rather than several.

"Withheld" rather than "hidden" or "filtered": the rows were matched and deliberately not shown.
That is a fact about this invocation, not a property of the rows, and the word should say so.

The empty-case message changes from `no work items yet` to `no work items`. The `yet` implies a
system that has not started producing work, which is precisely the wrong implication when four
rows exist and are being withheld. When genuinely nothing exists the message keeps its original
`no work items yet` wording, so the everyday empty-database case is untouched.

**Alternatives considered**: emitting to standard error (rejected — this is normal output, not a
warning, and splitting it complicates piping); a `--explain` flag to opt into the disclosure
(rejected — an opt-in fix for a contradiction leaves the default output still contradictory);
suppressing the queue below `live` instead (rejected outright — it would break the queue's
guarantee that it names what dispatch will actually select, which is the one property the queue
exists to have).

---

## R5 — Marking simulated rows in the queue table

**Decision**: suffix the `item` column with `*` for simulated rows and print the existing
`* = simulated (dry-run) row` footnote beneath the queue table when any row carries it.

**Rationale**: this convention is already in the codebase twice — the item listing marks the
`state` column, `worktree list` marks the `item` column — and a third convention for the same
fact would itself be a small contradiction. Marking the item id matches `worktree list`, whose
table is the closer analogue: both are keyed by work item.

The module already has a `_mark()` helper returning ` [simulated]`, used by `show`. That form is
right for a single-record rendering with room to spell it out and wrong for a column in an
aligned table, where it would widen every row to accommodate the few. Both forms stay.

**Rationale for doing this at all**, since the issue does not ask for it: FR-057 requires
simulated rows to be visibly marked wherever they are shown, and the queue shows them unmarked
today. A reader who stops at the queue — the most likely reader, since it is the first table and
answers the most common question — currently has nothing on screen telling them those four rows
are simulated. Fixing the disclosure below while leaving the queue silent would resolve the
contradiction only for someone who reads to the end.

---

## R6 — Shape of the machine-readable field

**Decision**: add one key to the `status` payload:

```json
"withheld_simulated": { "counts": 4, "items": 4 }
```

Both values are zero when nothing was withheld, including when `include_simulated` is true. The
key is always present.

**Rationale**: the sub-keys are named for the payload sections they explain — `counts` and
`items` are existing top-level keys — so the relationship needs no documentation to be obvious,
and R2's two scopes stay visibly distinct rather than being flattened into one misleading total.
Always present rather than conditionally omitted, because a consumer testing
`payload.get("withheld_simulated")` must not have to distinguish "nothing withheld" from "an
older build that did not report it"; that is the same absent-versus-zero ambiguity the feature
exists to remove from the text.

The queue entries already carry `dry_run` in `_queue_dict`, so the machine-readable side of R5
needs no change — only the text rendering was missing it.

**Deliberately not done here**: `web/pages.py` calls `operations.status` and will receive this
field. Rendering it in the web interface belongs to issue #14, which is a larger problem — below
`live` the interface shows an empty system with a neutral pill, which is worse than a
contradiction because there is nothing on screen to notice. This feature puts the number where
#14 will be able to reach it and stops there.

**Alternatives considered**: two flat keys (`withheld_counts`, `withheld_items`) — equivalent,
slightly noisier at the top level, and it separates two numbers that are only meaningful as a
pair. A single scalar — rejected for R2's reasons.
