# Research: Unique simulated issue numbers

**Feature**: [spec.md](spec.md) | **Date**: 2026-09-06 | **Issue**: #22

Everything here is a decision the spec deliberately left open, resolved against the code as it
stands today.

## R1 — Where the number comes from

**Decision**: `create_issue` allocates `max(SIMULATED_ISSUE_BASE, MAX(issue_number)) + 1` over the
`cards` rows for that `repo_key` with `dry_run = 1`, computed on every call.

**Rationale**: The database is the only thing that knows which numbers are taken, and it is
exactly the thing the unique index consults when it refuses one. Deriving the number from the same
place the constraint is enforced makes a collision unreachable in normal operation rather than
merely rarer. The query reads one indexed column and returns one row; it runs once per card filed,
which is a scale of a few per minute at most.

**Alternatives considered**:

- *Re-draw on `IntegrityError`, inside intake.* The issue's second suggestion. It converts eight
  failed passes into eight failed attempts within one pass, which is faster but still a linear
  scan, and each attempt writes a `github.issue.create` record for a number that is never used —
  the log would describe eight creations for one card. It also asks intake to interpret a database
  error as "ask the boundary again", which is only true for the simulated writer.
- *Seed a per-process counter once at startup.* Cheaper, and wrong within the same process: a card
  filed after startup advances the recorded maximum, and a second card would have to keep its own
  count in step with rows written by a path that can also fail. The per-call query needs no such
  bookkeeping.
- *A random number in the simulated range.* Collisions become unlikely rather than impossible, and
  a log full of unordered fake numbers is harder to read than one that counts up.

## R2 — How the writer reaches the database

**Decision**: `SimulatedIssueWriter.__init__` takes the open `sqlite3.Connection` alongside the
audit log. `wire()` grows a `conn` parameter and passes it through.

**Rationale**: Both call sites already hold the connection before they wire — `run_daemon` opens it
at `daemon.py:719` and wires at `:722`; `operations.build_context` opens it before its `wire()` call
at `:191`. It is the same connection intake uses, so no second connection, no second WAL reader, and
no new threading constraint: `sqlite3` binds a connection to its creating thread, and this one was
already bound there.

The layering objection — a boundary reaching into the database — is worth stating and then
answering. `SimulatedIssueWriter` stands in for GitHub's *server-side allocator*, and an allocator's
whole job is to know what it has already issued. Where the real one keeps that in GitHub's database,
the simulated one keeps it in ours, because ours is where the simulated issues live. A simulated
writer that cannot see the record of its own past writes is the bug being fixed.

**Alternatives considered**:

- *Pass a `Callable[[str], int]` allocator.* An interface with one implementation and one caller,
  which Principle I names outright. It also hides where the number comes from at the one place a
  reader most wants to see it.
- *Let the writer open its own connection from the layout path.* A second connection to the same
  WAL database, for a query the existing one can serve.
- *Pass the number into `create_issue`.* Changes the shared boundary signature so that the real
  writer takes an argument GitHub alone decides — the exact divergence between simulated and real
  paths that `contracts/boundaries.md` exists to prevent.

## R3 — Whether the connection is optional

**Decision**: Required. No default, no `None` branch.

**Rationale**: An optional connection has exactly one failure mode, and it is this bug: a caller
that forgets it gets the per-process counter back, silently, and nothing fails until a rehearsal
stalls for nineteen minutes. Making it required means the compiler-equivalent — an immediate
`TypeError` — finds the omission. Six test call sites construct the writer today; each gets the
connection the test already has.

## R4 — The counter shared with `comment()`

**Decision**: `comment()` keeps a counter of its own, used only to make its fake
`#issuecomment-…` fragments distinguishable from one another. `create_issue` no longer has a
counter at all.

**Rationale**: FR-005. A comment URL fragment needs to be unique within a run and nothing more —
nothing reads it back, and no index constrains it. An issue number needs to be unique against the
record. Two requirements that different should not share one field; sharing is what makes the
number a card receives depend on unrelated traffic.

## R5 — What remains reachable behind the `IntegrityError` guard

**Decision**: The guard in `_perform_creation` stays. Its explanatory comment and the message in
`_mapping_conflict` are rewritten to describe what is now true.

**Rationale**: After R1 the guard is unreachable in ordinary operation — allocation and write happen
in the same process, under the single-instance lock, with no other writer in between. It stays
because the alternative to a caught `IntegrityError` is an escaped one, which aborts the pass and
strands the card exactly as the existing comment describes. A last line of defence that is never
reached is doing its job.

The message is the part that was actively false. "The next pass retries with a fresh number"
described a strategy the system did not have. What the next pass now does is allocate above the
highest number recorded for that repository, which is worth saying because it tells a reader why a
second failure would mean something quite different from the first.

## R6 — The `dry_run` dimension

**Decision**: The allocation query filters `dry_run = 1`.

**Rationale**: `REAL_AT["issue_writer"]` is `{LIVE}` (`effects.py:114`), and card rows are written
with `dry_run=self.effect_level.is_simulated` (`daemon.py:379`). So the simulated writer is selected
under exactly the condition that makes the rows it creates `dry_run = 1`. Filtering on it matches
`idx_cards_issue`'s three columns exactly, which is what makes "the number I allocated cannot be
refused" true rather than approximately true.

## R7 — Whether `work_items` numbers matter

**Decision**: No. The query reads `cards` only.

**Rationale**: `work_items` is unique on `(source, source_id, dry_run)`, not on the issue number, so
nothing there can refuse an allocation. Its rows come from polling GitHub, which cannot return an
issue numbered above 900,000 in any repository this system is pointed at. Widening the query to a
`MAX` across both tables would guard against nothing and would couple the allocator to a table it
has no relationship with.

## R8 — Documentation

**Decision**: A short paragraph in [`docs/guide/2-intake.md`](../../docs/guide/2-intake.md), in the
"One card, one issue" section.

**Rationale**: That section is where the two unique indexes and the four-step creation are
explained, and simulated numbering is the one case where the number in the index does not come from
GitHub. `state.md` describes the `cards` table and `idx_cards_issue` but neither changes shape, and
`configuration.md` is untouched because no key is added, removed or renamed — `_KNOWN_KEYS` and
`_REPO_KEYS` in `config.py` are not touched, so `exampleconfig.py` and `share/config.example.toml`
stay as they are.
