# Phase 0 Research: Give the Missing-Transcript Check Time to Be Right

Every unknown the spec left open, resolved. Measurements are from this repository and from the
incident report in issue #58; nothing here is assumed where it could be read.

## R1 — How long should the grace period be?

**Decision**: `TRANSCRIPT_GRACE_SECONDS = 300` (5 minutes), a module constant in `reconcile.py`.

**Rationale**: One measurement exists. Issue #58 records session `23dc177e` confirmed at
18:03:51–52 with its transcript present by 18:04 — under eight seconds, on a warm cache, and the
issue explicitly warns that a single warm sample is not a bound. 300 seconds is roughly forty times
that observation, which absorbs a cold cache, a loaded machine, and a worker that spends a while
before its first write, while still surfacing a genuine failure inside SC-002's seven-minute budget
(300s grace + one 60s reconciliation interval = 360s worst case).

The cost asymmetry decides the direction: a grace period that is too long delays a report about a
session that is already unrecoverable; one that is too short recreates the exact defect being
fixed. Erring long is nearly free.

**Alternatives considered**:

- *60 seconds* — closer to the observation, but only 7× a single warm sample. Too close to the
  failure mode to be comfortable.
- *A configuration knob* — one caller, no second use in hand. Principle I forbids it; the value can
  simply change if it proves wrong.
- *Adaptive (learn from observed transcript latency)* — speculative generality of the purest kind,
  for a value that has never needed tuning.

## R2 — Where does "this session has not been judged yet" live?

**Decision**: a new nullable column, `sessions.transcript_checked_at`. `NULL` = open question; a
timestamp = answered, never asked again.

**Rationale**: Three requirements collapse into this one field.

- **FR-005 (one anomaly per session, ever)**: the anomalies table's partial unique index dedupes only
  *unacknowledged* rows. Acknowledge the anomaly for a still-transcript-less session and the next
  pass would raise it again — a slow-motion version of the reported bug. A column that says "asked
  and answered" is immune to acknowledgement.
- **FR-012 (survives interruption)**: an in-memory set of pending sessions is emptied by a restart,
  which would either lose the obligation or re-report. A committed column loses nothing.
- **FR-010 (bounded work)**: the open population is exactly the `NULL` rows.

**The simplification this buys**: the spec assumed the sweep would need an age window — "sessions
still open, or recently ended" — to keep the population bounded. It does not. Every session is
resolved exactly once and then leaves the population permanently, so the `NULL` set is
self-limiting with no window, no cutoff, and no second rule about how old is too old. One fewer
moving part, which is how Principle I resolves ties.

**Alternatives considered**:

- *Derive it from the anomalies table each pass* — no schema change, but re-globs the filesystem for
  every session forever, and cannot survive acknowledgement (above).
- *A separate `transcript_checks` table* — a table to hold one boolean per session, joined to the
  row that already exists. Rejected on Principle I.
- *Two columns (`transcript_seen_at` / `transcript_reported_at`)* — the distinction is already
  recoverable from the audit log and the anomalies table. One column, one question.

## R3 — Does the sweep need an index?

**Decision**: yes — `CREATE INDEX idx_sessions_transcript_open ON sessions (transcript_checked_at)
WHERE transcript_checked_at IS NULL`.

**Rationale**: FR-010 requires the work to be bounded by open questions, not by history. Without the
partial index the *result* is small but the *scan* is over every session ever dispatched, growing
without bound on a query that runs every 60 seconds forever. The partial index makes the requirement
structurally true rather than incidentally true, and a partial index over a set that is almost always
empty costs essentially nothing to maintain.

**Alternatives considered**: *No index* — the result set is tiny today and the table is small on one
machine. Rejected because the query's cost then grows with history on a permanent 60-second loop,
which is precisely the shape FR-010 was written against.

## R4 — When does the clock start?

**Decision**: `confirmed_at` when present, else `started_at`.

**Rationale**: `started_at` is written when the session row is inserted, which is *before* the
process launches. The worker cannot write a transcript until it is running, so measuring from
`started_at` charges the session for time it did not have — confirmation can take up to
`confirm_timeout_seconds` (45s by default), which would silently eat 15% of the grace period.
`confirmed_at` is the moment the process was observed running, which is the moment the clock should
honestly start. `started_at` is the fallback because it is `NOT NULL` and the column always answers.

An unparseable or absent timestamp reads as infinitely old through the existing `_age_seconds`,
which judges the session immediately and records `waited_s: null`. This follows the precedent set by
the `dispatching_timeout` sweep — a row we cannot date is one we cannot vouch for — and the honest
`null` in the record says the wait is unknown rather than inventing a number.

## R5 — What distinguishes a session that never ran from one that did?

**Decision**: `session.pid` is falsey — `NULL` or `0`.

**Rationale**: This is the correction issue #33 already made one sweep over in `reconcile.py`, being
applied to a second place that has the same bug. `dry_run` answers "was the effect level below
`live`", which is **true at `no-remote`**, where the session host is real, the process is real, and
the transcript is real. Keying the exemption on it is what made every rehearsal blind to this
detector — issue #58's third complaint. `pid` answers the question that actually matters: was there
ever a process that could have written anything? It is written from whatever the session host
returned on confirmation, and the simulated host returns `0` by construction, so `NULL` and `0` mean
the same thing and both are falsey.

Deriving it from the record rather than the effect level is also required mechanically:
`tests/unit/test_effects.py::test_only_effects_py_knows_the_effect_level_exists` greps every module
outside a small exempt set for `EffectLevel` and fails the suite if `reconcile.py` names it.

**Alternatives considered**: *Keep `dry_run`* — preserves the blindness the issue reports.
*Both* — `dry_run and not pid` is redundant: a row with a real pid is a real session whatever level
produced it, and that is the whole point.

## R6 — How do tests observe a transcript without touching the real home directory?

**Decision**: add `paths.claude_projects_dir()` — `~/.claude/projects`, mirroring the existing
`claude_registry_dir()` — make `sessions.transcript_exists` fall back to it when no `home=` is
given, and add an autouse conftest fixture pointing it at `tmp_path`.

**Rationale**: The existing `transcript_exists(session_id, home=...)` seam is fine for the two direct
unit tests that use it, but the reconciliation sweep calls it with no `home`, so without a seam a
suite run would read the maintainer's actual `~/.claude/projects`. That is the same class of
non-determinism `_no_real_session_registry` was written to remove in milestone 004, and it is fixed
the same way, in the same place, with the same shape. The `home=` parameter stays — it is the finer
seam and it already has callers.

**Alternatives considered**: *Monkeypatch `Path.home` per test* — leaks into everything else in the
process and hides which directory a test means. *Pass `home` down through `reconcile()`* — another
parameter threaded through a signature that already carries two test seams, to say something the
module-level seam says once.

## R7 — Where in the pass should the sweep run?

**Decision**: in `reconcile()`, after `_sweep_stale_sessions` and before `_orphan_sweep`.

**Rationale**: The check reads only the session row's timestamps, pid, and checked-at column, so it
is not order-sensitive for correctness. It is placed with the other session-focused sweeps, after
every pass that may have settled a session's state this tick, so that anything recorded alongside the
report describes the session as this pass has left it. `_orphan_sweep` and the socket and worktree
sweeps are untouched.

## R8 — What should the anomaly actually say?

**Decision**: name both possible causes, state that the check cannot distinguish them, record the
seconds waited, and give the instruction that is true either way — restart, do not resume.

**Rationale**: FR-009 exists because the current note asserts a cause. It sends the reader to hunt
`CLAUDE_CODE_*` variables in the terminal daemon's environment; on the machine where it fired that
environment was verifiably clean (`doctor` reported zero such variables), so the guidance led away
from the answer. The check observes an absence and genuinely cannot tell a suppressed transcript
from a session that died before writing one. Saying so, and naming the command that settles the
first possibility (`robot-army doctor`) and the record that settles the second (the session's exit
record), is guidance the maintainer can follow to an answer instead of to a dead end.
