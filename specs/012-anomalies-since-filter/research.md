# Phase 0 Research: A `--since` Window on `anomalies`

No `NEEDS CLARIFICATION` markers reached this phase — the spec resolved its three open
questions by informed default. What follows are the design decisions the plan depends on, each
recorded with what was rejected and why, so a later reader can tell a choice from an accident.

---

## R1 — Reuse `operations.parse_duration` by direct call

**Decision**: `anomalies()` calls the existing `operations.parse_duration`. The
`_DURATION_UNITS` table and `parse_duration` move up in `operations.py`, out from under the
`# -- log ---` section banner and into a small `# -- durations ---` section placed above
`# -- anomalies ---`, so the section headers stop lying about who owns it. The function body,
its name, and its module do not change.

**Rationale**: FR-002 requires one parser, not two that agree. Both commands already live in
`operations.py`, so "one parser" costs a function call and a moved block. `parse_duration` is
imported by name at module level in `tests/unit/test_time_record_unchanged.py`, and moving a
definition within a module leaves that import valid.

**Alternatives considered**:

- *Copy the parsing into `anomalies()`* — rejected outright. Two implementations of "what is a
  valid duration" is precisely the divergence FR-002 exists to prevent.
- *Extract to a new `duration.py` module* — rejected under Principle I. Two callers in one file
  do not justify a module; the import graph would grow for no reader's benefit.
- *Leave `parse_duration` where it sits and just call it* — viable and even smaller, but the
  `# -- log ---` banner would then sit above a function two commands depend on. Moving the
  block is a pure relocation with no behaviour change, and it keeps the file honest.

---

## R2 — Filter in the operation, not in SQL

**Decision**: `db.list_anomalies` keeps its current signature. `anomalies()` fetches the rows
it fetches today and drops the ones outside the window before rendering.

**Rationale**: three reasons, in order of weight.

1. **FR-010 is not expressible in SQL here.** `detected_at` is a TEXT column. A
   `WHERE detected_at >= ?` string comparison against a malformed value silently excludes it —
   the exact silent omission Principle III forbids, arriving through a code path with nowhere
   to notice it. In Python the failed parse is a branch that can decide to *keep* the row.
2. **The set is small by construction.** The partial unique index
   `idx_anomalies_open` keeps one open row per `(kind, entity_type, entity_id)`, so the
   unacknowledged list is bounded by the number of distinct conditions the system can be in,
   not by uptime. There is no volume argument for pushing the predicate down.
3. **Fewer moving parts.** The SQL form needs the cutoff `datetime` rendered back into the
   stored `%Y-%m-%dT%H:%M:%SZ` string, which is a second format dependency to keep in step
   with `states.utcnow()`. The Python form parses the stored string once, in one direction.

**Alternatives considered**:

- *Add `detected_since: str | None` to `db.list_anomalies`* — rejected for (1) above. It also
  spreads the feature into the data-access layer for no gain.
- *Store a numeric epoch column and compare on it* — rejected. A schema change and a migration
  for a filter, against a constitution that prefers human-inspectable storage.

---

## R3 — The window is compared as instants, in UTC

**Decision**: parse each row's `detected_at` with `%Y-%m-%dT%H:%M:%SZ` into a timezone-aware
UTC `datetime` and compare against `datetime.now(UTC) - parse_duration(since)`. Boundary is
inclusive: `detected_at >= cutoff` is inside the window.

**Rationale**: this is the same shape `_judge_record` uses for `log --since`, including the
inclusive boundary, so "what does `--since 1h` mean" has one answer across both commands.
Milestone 010 made *display* local; comparison stays UTC because the stored instant is UTC and
a comparison that drifted with the machine's zone would be a bug, not a feature.
`tests/unit/test_time_record_unchanged.py` already guards that `parse_duration` is
zone-independent.

**Alternatives considered**:

- *Lexicographic string comparison on the fixed-width stamps* — correct in the happy case and
  genuinely tempting, but it is the SQL argument from R2 wearing Python clothes: a malformed
  stamp compares rather than raising, so the honesty branch never fires.
- *Exclusive boundary* — rejected for divergence from `log` with no reason behind it.

---

## R4 — An uninterpretable `detected_at` is kept, not dropped

**Decision**: if `strptime` cannot read a row's `detected_at`, the row stays in the listing
regardless of the window, and the rendered line shows what is stored.

**Rationale**: FR-010, and behind it Principle III's "silent failure is forbidden". The choice
is between showing a row the user did not ask for and hiding a detected, unresolved condition
because its timestamp is unreadable. Showing it is the only defensible direction — the reader
sees both the anomaly and the evidence that its timestamp is wrong.

**Note on scope**: this cannot happen through any code path this repository has —
`db.raise_anomaly` writes `states.utcnow()` and SQLite rows are not half-written, so unlike
the audit log's truncated final line there is no crash that produces one. It is a hand-edited
or externally corrupted database. It earns its branch anyway because the branch is two lines
and the alternative is a listing that lies; this is a guard, not the speculative generality
Principle I rules out.

**Alternatives considered**: *drop it*, rejected as above. *Raise*, rejected — refusing to show
any anomalies because one row is malformed is the same wrong trade the log reader already
declined to make.

---

## R5 — `--since` is validated before `--acknowledge` runs

**Decision**: parse the duration at the top of `anomalies()`, before the acknowledgement
branch. A malformed duration returns `EXIT_USAGE` having changed nothing.

**Rationale**: FR-007 requires the command to fail before listing anything, and the same
reasoning extends one step further back: a typo in a filter must not be the thing that
irreversibly marks an anomaly acknowledged. Every other malformed argument on this command is
already rejected by argparse before any work happens; validating here puts `--since` on the
same footing rather than inventing a case where a rejected command still mutated state. FR-006
is untouched by this — for every duration the parser accepts, `--acknowledge` behaves exactly
as it does today.

**Alternatives considered**: *acknowledge first, then validate* — rejected. It makes the one
irreversible thing this command does reachable from a command that exits with a usage error.

---

## R6 — A filtered-empty listing says so

**Decision**: when the window empties the listing, print a message naming the window rather
than reusing `no outstanding anomalies`. The trailing enumeration of raisable kinds is printed
in both cases, as today.

**Rationale**: FR-009 and SC-004. `no outstanding anomalies` is an all-clear; a filtered empty
result is not one, and a reader who conflates the two has been misled by the tool into
believing nothing is wrong. Reusing the string would make the filter a way to miss an anomaly —
the failure Story 2 exists to prevent.

**Alternatives considered**: *reuse the existing string* — rejected as above. *Print the
existing string plus a filter note* — equivalent in effect but wordier; one purpose-built line
is clearer.

---

## R7 — The web anomaly view is not touched

**Decision**: `web/pages.py::anomalies_view` keeps calling `operations.anomalies(ctx)` with no
`since`, so it sees the default and renders the unfiltered set. The chrome's anomaly count,
which reads `db.list_anomalies` directly, is likewise unchanged.

**Rationale**: the issue asks for the CLI. Story 2 scenario 3 asserts the web view is
unchanged, and the `since` parameter defaulting to `None` makes that true without a line of web
code. Adding a matching web filter now would be building for a need nobody has stated —
Principle I.

**Alternatives considered**: *add `?since=` to `/anomalies` for symmetry with `/log`* —
deferred, not rejected on merit. If the maintainer wants it, it is its own small feature and
the operation-layer parameter this plan adds is already the seam it would use.
