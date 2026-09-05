"""Schema migrations as a ``PRAGMA user_version`` ladder (research.md R3).

Forward-only, no downgrades. Each migration runs inside a transaction and advances
``user_version`` as its last statement, so a process killed mid-migration leaves the
version unadvanced and the whole migration is re-run on the next start — which is the
interruption answer data-model.md records for this operation.

Adding a migration means appending to ``MIGRATIONS``. Never editing an existing one:
databases in the field have already run it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

SCHEMA_001_SQL = """
CREATE TABLE repos (
    repo_key                TEXT PRIMARY KEY,
    onboarded_at            TEXT NOT NULL,
    settings_fingerprint    TEXT,
    fingerprint_approved_at TEXT NOT NULL,
    trust_verified_at       TEXT
);

CREATE TABLE work_items (
    id              INTEGER PRIMARY KEY,
    source          TEXT    NOT NULL,
    source_id       TEXT    NOT NULL,
    source_url      TEXT    NOT NULL,
    repo_key        TEXT    NOT NULL REFERENCES repos(repo_key),
    issue_number    INTEGER NOT NULL,
    title           TEXT    NOT NULL,
    body            TEXT    NOT NULL,
    labels          TEXT    NOT NULL,
    state           TEXT    NOT NULL,
    dry_run         INTEGER NOT NULL,
    worktree_path   TEXT,
    branch          TEXT,
    prepare_output  TEXT,
    failure_reason  TEXT,
    blocked_reason  TEXT,
    discovered_at   TEXT    NOT NULL,
    ready_at        TEXT,
    dispatching_at  TEXT,
    active_at       TEXT,
    ended_at        TEXT,
    done_at         TEXT,
    updated_at      TEXT    NOT NULL
);

-- The idempotency guarantee (FR-072): re-polling an already-dispatched issue collides
-- on insert and becomes a no-op rather than a second worktree and a second session.
-- dry_run is part of the key deliberately, so a simulated run and a later live run of
-- the same issue can coexist, which is the normal workflow.
CREATE UNIQUE INDEX idx_work_items_identity ON work_items (source, source_id, dry_run);
CREATE INDEX idx_work_items_state ON work_items (state);
CREATE INDEX idx_work_items_dispatching ON work_items (state, dispatching_at);

CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY,
    work_item_id  INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    session_id    TEXT    NOT NULL UNIQUE,
    attempt       INTEGER NOT NULL,
    state         TEXT    NOT NULL,
    dry_run       INTEGER NOT NULL,
    pid           INTEGER,
    proc_start    TEXT,
    scope         TEXT,
    host_socket   TEXT,
    window_id     INTEGER,
    launch_argv   TEXT,
    exit_code     INTEGER,
    signal        INTEGER,
    started_at    TEXT    NOT NULL,
    confirmed_at  TEXT,
    ended_at      TEXT
);

CREATE INDEX idx_sessions_item ON sessions (work_item_id, attempt);
CREATE INDEX idx_sessions_state ON sessions (state);

CREATE TABLE anomalies (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    detail          TEXT NOT NULL,
    detected_at     TEXT NOT NULL,
    acknowledged_at TEXT
);

-- The partial index is what stops a 60-second reconciliation loop producing 1,440
-- identical rows a day for one orphan. Acknowledging a row lifts it out of the index,
-- which lets a genuinely new occurrence be recorded later.
--
-- COALESCE is load-bearing, not decoration: in SQLite two NULLs never compare equal, so
-- indexing the bare columns would leave every anomaly with an unspecified entity — a
-- registry-version warning, say — colliding with nothing and duplicating on every pass.
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''))
    WHERE acknowledged_at IS NULL;
CREATE INDEX idx_anomalies_ack ON anomalies (acknowledged_at);

CREATE TABLE poll_state (
    repo_key             TEXT PRIMARY KEY,
    etag                 TEXT,
    last_polled_at       TEXT,
    last_status          INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    backoff_until        TEXT
);
"""


def _statements(script: str) -> list[str]:
    """Split a schema script into individual statements.

    Needed because ``executescript()`` issues an implicit COMMIT before running, which
    would end the transaction ``migrate()`` opened and leave a half-built schema behind
    on a crash — exactly the interruption behaviour data-model.md promises against.

    Comments are stripped **before** the split, not after. Doing it the other way round
    made a semicolon inside an explanatory comment cut a statement in half, which
    milestone 003 discovered by writing one. The split remains safe only because these
    scripts contain no semicolons inside string literals or trigger bodies, and a future
    migration that does must not use this.
    """
    body = "\n".join(
        line for line in script.splitlines() if not line.strip().startswith("--")
    )
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


SCHEMA_002_SQL = """
-- Whether dispatch is currently suspended (milestone 002, FR-033 through FR-036).
--
-- One row, and the CHECK is what makes a second row impossible rather than merely
-- unlikely: this is a single-valued fact about the whole system, and "which of the two
-- pause rows is authoritative" is a question that must never be askable.
--
-- A table rather than a file or a config key because durability across restart and reboot
-- is the whole point (FR-035), and the database provides it atomically alongside the data
-- it governs — see research.md R6. `paused_by` records which front end set it, so "who
-- stopped dispatch" is answerable from state as well as from the log.
CREATE TABLE dispatch_control (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    paused      INTEGER NOT NULL DEFAULT 0,
    paused_at   TEXT,
    paused_by   TEXT
);

INSERT INTO dispatch_control (id, paused) VALUES (1, 0);
"""


SCHEMA_003_SQL = """
-- Cards on the intake board, and the mapping from each to the issue it produced
-- (milestone 003, data-model.md).
--
-- A table of its own rather than columns on `work_items`, and the reason is concrete
-- rather than aesthetic: `work_items.repo_key` is NOT NULL REFERENCES repos(repo_key) and
-- `issue_number` is NOT NULL, while a card awaiting clarification has neither by
-- definition. Accommodating it there would mean rebuilding the central table to weaken an
-- invariant every other row depends on (research.md R5). The mapping must also outlive any
-- work item: a card's issue may sit unlabelled for weeks, and may be refused at onboarding
-- and never become a work item at all.
--
-- `repo_key` is deliberately NOT a foreign key into `repos`. A card may name a repository
-- that is configured but not onboarded — such a card still gets an issue, because creating
-- an issue is not dispatching — and may name one that is configured and later removed. An
-- FK here would either forbid the row or delete the mapping, and deleting a mapping is how
-- a duplicate issue gets created.
CREATE TABLE cards (
    id                INTEGER PRIMARY KEY,
    board_id          TEXT    NOT NULL,
    card_id           TEXT    NOT NULL,
    card_url          TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    body              TEXT    NOT NULL,
    state             TEXT    NOT NULL,
    dry_run           INTEGER NOT NULL,
    repo_key          TEXT,
    issue_number      INTEGER,
    issue_url         TEXT,
    reason            TEXT,
    commented_reason  TEXT,
    last_activity     TEXT,
    origin_list_id    TEXT,
    placed_list_id    TEXT,
    pending_move_to   TEXT,
    comment_posted_at TEXT,
    intent_at         TEXT,
    create_failures   INTEGER NOT NULL DEFAULT 0,
    archived_at       TEXT,
    first_seen_at     TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

-- The §11 invariant, both halves, enforced by the schema rather than by a rule the create
-- path has to remember. A create that skipped its mapping check does not produce a
-- duplicate. It produces an IntegrityError, which is loud — the difference between an
-- invariant and a convention.
--
-- dry_run is part of both keys, exactly as it is for work_items and for the same reason: a
-- simulated run and a later live run of the same card must coexist, which is the normal
-- workflow, and it is what makes FR-041 true — a simulated row cannot occupy the live
-- row's slot and so cannot suppress the real creation.
CREATE UNIQUE INDEX idx_cards_identity ON cards (board_id, card_id, dry_run);

-- At most one card per issue. Partial, because rows that have no issue yet — every
-- needs_info card, and every creating card before its issue exists — must not collide with
-- each other on a shared NULL.
CREATE UNIQUE INDEX idx_cards_issue ON cards (repo_key, issue_number, dry_run)
    WHERE issue_number IS NOT NULL;

CREATE INDEX idx_cards_state ON cards (state);
"""


SCHEMA_004_SQL = """
-- What cleanup did to a finished item's disk, and why (milestone 004, R13).
--
-- Three nullable columns rather than a `cleanups` table: one row per item, one shot, no
-- lifecycle of its own, so a table would be a join for nothing.
--
-- `work_items.state` is deliberately untouched and WORK_ITEM_TRANSITIONS gains no entries.
-- `done` is terminal and means the *work* is finished; whether its disk has been reclaimed
-- is a different axis — the same separation §7 makes between work state and session state.
-- Adding a `cleaned` state would make every existing query that treats `done` as terminal
-- subtly wrong.
--
-- `worktree_path` and `branch` are never nulled after a removal. FR-024 requires the record
-- to retain what was removed, `_sweep_worktrees` already keys on the path being present,
-- and "what was at this path?" is exactly the question a retained-branch record must answer.
ALTER TABLE work_items ADD COLUMN cleanup_state  TEXT;
ALTER TABLE work_items ADD COLUMN cleanup_reason TEXT;
ALTER TABLE work_items ADD COLUMN cleaned_at     TEXT;
"""


SCHEMA_005_SQL = """
-- The location this repository was approved at, and how we arrived at it (milestone 005).
--
-- The **outcome** is recorded rather than the rule, and that is the whole design. A
-- derivation rule evaluated later can produce a different answer than the one a human
-- approved, and the wrong answer here is not an error — it is a real clone of a real
-- repository, which is why the five known collisions on the author's machine would fail
-- silently under any re-derive-on-demand design. Nothing re-derives after approval; a
-- clone that moves produces a refusal naming the recorded path.
--
-- Four nullable columns rather than a table: one row per repository, no lifecycle of its
-- own, so a table would be a join for nothing.
--
-- `verified_origin` holds the **normalised** host/owner/name and never a raw URL. A raw
-- remote URL may embed credentials, and storing one would put a secret into
-- `robot-army repos` output and every JSON view of it (FR-032). The normalised triple is
-- what the comparison uses, is stable across a clone being re-pointed between SSH and
-- HTTPS, and cannot carry a secret because normalisation strips userinfo first.
--
-- A NULL `clone_path` means *onboarded, location never verified* — a row predating this
-- migration — and not "onboarded at an unknown path". Nothing backfills it: writing a path
-- nobody approved into an approval record is the one thing this table exists not to do
-- (research R6).
ALTER TABLE repos ADD COLUMN clone_path         TEXT;
ALTER TABLE repos ADD COLUMN path_source        TEXT;   -- 'derived' | 'configured'
ALTER TABLE repos ADD COLUMN verified_origin    TEXT;   -- normalised host/owner/name
ALTER TABLE repos ADD COLUMN origin_verified_at TEXT;
"""


SCHEMA_006_SQL = """
-- Where the card is **now**, as the last poll saw it (milestone 006).
--
-- A fourth list id, and the three that already exist answer different questions, which is
-- why none of them could be reused:
--
--   origin_list_id   where the card was before we ever touched it — what FR-029 returns
--                    an abandoned card to. Overwriting it with the card's current
--                    position would return a card to wherever it last happened to be.
--   placed_list_id   where *we* last put it — what FR-030 compares against to detect a
--                    move the author made by hand.
--   pending_move_to  where we are in the middle of putting it, written before the move.
--   current_list_id  where it is now.
--
-- It exists so `robot-army cards` and the web listing can answer "is this card parked?"
-- with the board unreachable. Deriving that at render time would mean a board request
-- from a read-only listing command, which would then fail whenever the board is down.
--
-- NULL means *tracked before this migration and not yet re-polled*, never "in no column",
-- which Trello cannot produce. Nothing backfills it: the next poll writes it, and until
-- then the card is treated as not parked — milestone 003's behaviour, and the safe
-- direction for a value we do not have.
--
-- The **name** is stored beside the id because the two consumers cannot share one
-- representation. The intake gate runs inside the poll, where the board's id->name map is
-- in hand, and wants an id: an equality check that is duplicate-safe and survives a column
-- being renamed mid-run. `robot-army cards` and the web listing run where the board is
-- not available at all — they must answer "is this parked?" with the board down — and can
-- only compare against the *names* in `[trello] ignore_lists`. Both values are written by
-- the same statement from the same poll, so they cannot disagree.
ALTER TABLE cards ADD COLUMN current_list_id   TEXT;
ALTER TABLE cards ADD COLUMN current_list_name TEXT;
"""


SCHEMA_007_SQL = """
-- How far a Spec Kit run has got, and what makes that answerable (milestone 007).
--
-- speckit_baseline is the load-bearing one, and the trap it exists for is worth stating.
-- A fresh worktree of a repository that uses Spec Kit contains every feature it has ever
-- shipped: six directories here, each with a spec, a plan, and a tasks.md full of ticked
-- boxes. Deriving a phase from "what artifacts exist" would therefore report `implement`
-- the instant a worktree was created, on every item, forever. So the set of feature
-- directory names present *at creation* is recorded, and only a directory absent from it
-- counts as this item's work. /speckit-specify always creates a new one, so "not here
-- before" means "this session's feature" with no heuristics and no timestamps.
--
-- NULL means no baseline was recorded — a row predating this migration, or one prepared
-- before it shipped. It is not the same as '[]', which means a Spec Kit worktree that had
-- no features yet. A NULL baseline reports no phase at all and says why, rather than
-- deriving one late and silently classifying the session's own directory as pre-existing.
--
-- speckit_phase is a cache whose only job is transition detection: "did this change since
-- I last looked" is unanswerable without the previous value, and FR-014 wants one record
-- per transition rather than one per reconciliation cycle. The worktree stays the source
-- of truth and every cycle re-derives from it. The one place the column outlives its
-- source is after cleanup removes the worktree, where it becomes the item's last known
-- stage — which is history, correctly, and is why observation never clears it.
ALTER TABLE work_items ADD COLUMN speckit_baseline    TEXT;
ALTER TABLE work_items ADD COLUMN speckit_phase       TEXT;
ALTER TABLE work_items ADD COLUMN speckit_feature_dir TEXT;
ALTER TABLE work_items ADD COLUMN speckit_phase_at    TEXT;
"""


SCHEMA_008_SQL = """
-- Whether a session's transcript has been accounted for, and when (issue #58).
--
-- NULL is the whole design: it means "this session's transcript question is still open".
-- The check that answers it used to run inline in dispatch, one line after the session was
-- confirmed running -- before the worker had written anything -- so it fired on every
-- healthy dispatch. Moving it to reconciliation means the question outlives the pass that
-- asked it, and a column is what carries it across a restart.
--
-- Written once and never cleared. That is what makes "at most one anomaly per session,
-- ever" true where the anomalies table alone cannot: its partial unique index dedupes only
-- *unacknowledged* rows, so acknowledging a still-transcript-less session's anomaly would
-- otherwise let the next pass raise it again.
ALTER TABLE sessions ADD COLUMN transcript_checked_at TEXT;

-- The sweep's cost must follow open questions, not session history (FR-010). Without this
-- the result set is tiny but the scan is over every session ever dispatched, on a query
-- that runs every 60 seconds forever.
CREATE INDEX idx_sessions_transcript_open
    ON sessions (transcript_checked_at)
    WHERE transcript_checked_at IS NULL;

-- Every session that already exists has been judged, correctly or not, by the old inline
-- check. Leaving them NULL would make the first pass after this upgrade re-ask the
-- question about the entire history at once and report all of it. Inside migration 008's
-- transaction, so an interrupted upgrade re-runs the backfill whole.
UPDATE sessions SET transcript_checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    WHERE transcript_checked_at IS NULL;
"""

SCHEMA_009_SQL = """
-- Where the board puts this item, as the last successful board read saw it (issue #48).
--
-- Two columns rather than one, and the pair must stay a pair, because four states have to
-- remain distinguishable and collapsing any two of them is a real bug (research R9):
--
--   board_column NULL, repo never read   ->  no board knowledge. Nothing is gated and the
--                                            repository orders exactly as it always did.
--   board_column NULL, repo HAS been read ->  read, and this item is not on the board:
--                                            dispatchable, ordered after everything the
--                                            board ranked (FR-008).
--   board_column = the dispatch column   ->  board_position is its rank, 1-based.
--   board_column = anything else         ->  parked by the author. Held (FR-012), with
--                                            board_position NULL.
--
-- The distinction between the first two lives in repo_projects.last_read_at rather than
-- here, so "never read" is one fact about a repository instead of a fact repeated on
-- every one of its rows.
--
-- board_position is NULL for every item outside the dispatch column and must NEVER be
-- written as 0 to mean "unknown". boundaries/__init__.py records what that mistake cost
-- the last time it was made -- commits_ahead folding "could not determine" into 0 -- and
-- here it would silently promote every item of an unread board to the head of its queue.
ALTER TABLE work_items ADD COLUMN board_column   TEXT;
ALTER TABLE work_items ADD COLUMN board_position INTEGER;

-- Which project governs a repository, how that was decided, and how the last read went.
--
-- A table rather than columns on `repos`, because `repos` is an *approval* record:
-- migration 005 is emphatic that it stores what a human approved at a verified location
-- and that nothing re-derives after approval. This is the opposite -- discovered,
-- self-refreshing, and carrying its own failure state -- so putting it there would blur
-- the one record whose value is that it does not change on its own.
--
-- Not `poll_state` either. That table's columns are fixed (etag, last_status, backoff)
-- with nowhere to put a project id, a column name, or which of the two the author chose.
--
-- One row per repository, written by the poll and deleted by nothing. A repository whose
-- project is later unlinked keeps its row with resolved_at NULL and unresolved_reason
-- set, which is what lets `status` say *why* a board stopped governing rather than simply
-- going quiet.
--
-- last_read_at is the FR-014 gate. It records the last SUCCESSFUL read, never the last
-- attempt, so a failed read leaves the previous snapshot in force and visibly stale
-- rather than discarded. While it is NULL no item is ever held for its column, because
-- inventing a hold out of ignorance is worse than dispatching.
CREATE TABLE repo_projects (
    repo_key             TEXT PRIMARY KEY REFERENCES repos(repo_key),
    project_id           TEXT,
    project_number       INTEGER,
    project_title        TEXT,
    project_url          TEXT,
    project_source       TEXT,
    column_name          TEXT,
    column_source        TEXT,
    resolved_at          TEXT,
    unresolved_reason    TEXT,
    last_read_at         TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    backoff_until        TEXT
);
"""


SCHEMA_010_SQL = """
-- One work item the author has taken out of dispatch until they say otherwise (issue #117).
--
-- The work item id is the PRIMARY KEY, which does two jobs at once. It makes "at most one
-- hold per item" a constraint rather than a convention, so a repeated hold collides with
-- itself and becomes a reported no-op instead of a second row that would have to be
-- deduplicated on read (FR-004). And it is the whole row apart from the two provenance
-- columns -- a hold has no levels, no expiry and no note (FR-026) -- so presence *is* the
-- fact, and there is no state in which a hold exists but does not apply.
--
-- ON DELETE CASCADE is FR-025, and it is the reason this is two tables rather than one
-- table with a scope column. `PRAGMA foreign_keys` is ON -- db.py's docstring says the
-- schema relies on it and test_migrations asserts it -- so a hold cannot outlive the item
-- it holds and cannot reattach itself to a recycled id. A single polymorphic table could
-- not express that: its target would point at work_items for one scope and repos for the
-- other, and no foreign key says "one of these two depending on a sibling column". FR-025
-- would then be maintained by hand at every deletion site, and the site that forgot would
-- be the bug. `db.purge_simulated` is the only such site today and needs no change: the
-- cascade is the cleanup.
--
-- Simulated rows are covered with no special case, because they are work_items rows like
-- any other. A dry-run item occupies a queue slot, so it can be held; a hold that ignored
-- simulated work would rehearse the wrong behaviour. No outward request is made either way.
--
-- No backfill, and none is possible: no hold existed before this migration, so an upgraded
-- database is correct the instant the table exists.
CREATE TABLE item_holds (
    work_item_id INTEGER PRIMARY KEY REFERENCES work_items(id) ON DELETE CASCADE,
    held_at      TEXT NOT NULL,
    held_by      TEXT NOT NULL
);

-- Every work item in one repository, taken out of dispatch until released (issue #117).
--
-- Keyed on repos(repo_key), not on a bare string, and that is the point: FR-006 refuses a
-- hold on a repository that was never onboarded, and the foreign key makes a typo
-- impossible to store rather than merely unlikely. A hold on a repository the system does
-- not watch would hold nothing and report nothing wrong, which is the worst available
-- outcome -- it looks exactly like a hold that works.
--
-- A separate table rather than a column on `repos` for the reason migration 009 gave for
-- `repo_projects`: `repos` is an *approval* record, and migration 005 is emphatic that it
-- stores what a human approved at a verified location and that nothing re-derives after
-- approval. A hold is the opposite -- temporary, toggled often, and meaningless a week
-- later -- so putting it there would blur the one record whose value is that it does not
-- change on its own.
--
-- This shape is what makes FR-012 free. The hold is a fact about the repository rather
-- than about any item, so an item discovered tomorrow in a held repository is held on
-- arrival, with nothing to backfill and no event to hook.
--
-- held_by is NOT NULL here and in item_holds, unlike dispatch_control.paused_by, which is
-- nullable because it is cleared when dispatch resumes. A hold has no cleared state: the
-- row exists or it does not, so every row that exists was placed by something and can say
-- which.
CREATE TABLE repo_holds (
    repo_key TEXT PRIMARY KEY REFERENCES repos(repo_key) ON DELETE CASCADE,
    held_at  TEXT NOT NULL,
    held_by  TEXT NOT NULL
);
"""

SCHEMA_011_SQL = """
-- Who wrote the issue this item came from (issue #119, RA-01).
--
-- The author check is the control that stops "anyone can open an issue on a public
-- repository" becoming "anyone can run an agent in my checkout". It was enforced in exactly
-- one place -- poll.evaluate -- and `retry` returned an author-rejected item to the queue
-- without re-running it, because `dispatch.check_gates` takes a RepoConfig and cannot see
-- an issue at all. `dispatch` then *asserted* `author=config.github.author` into the Issue
-- it built, which made the code read as though a check had happened downstream.
--
-- This column is what lets that assertion become a comparison. The alternative was a second
-- HTTP read on the dispatch path, which today makes none and would have had to grow its own
-- timeout, retry and backoff to gain one; a nullable column costs one string compare and
-- behaves identically on a redispatch.
--
-- NULL means *never recorded* -- a row written before this migration -- and it is not "no
-- author" and not "unknown but probably fine". It is `this row's provenance cannot be
-- established`, which is a real and load-bearing state: a `ready` row from before this
-- migration may have reached `ready` through the very defect being fixed, and no query
-- answers which. So NULL refuses the dispatch and names `retry` as the recovery, which
-- re-reads the issue and writes this column for the first time. The upgrade heals itself
-- along the path this change hardens.
--
-- No backfill, deliberately, and the contrast with migration 008 is the whole argument.
-- 008 backfilled `sessions.transcript_checked_at` because it could derive the right answer:
-- those sessions really had been judged by the old inline check. Writing
-- `config.github.author` here would be the opposite -- an unverified claim in the one
-- column whose entire purpose is to hold a verified one. Migration 005 refuses the same
-- thing about clone paths, in the same words.
--
-- Nullable rather than NOT NULL DEFAULT '': a default would make every pre-migration row
-- indistinguishable from an issue written by an author whose login is the empty string, and
-- would silently give the two the same treatment. SQLite cannot add a NOT NULL column
-- without one anyway, so the choice is between a lie and a NULL that means something.
--
-- No index. Nothing queries by author; the only read is by primary key, on an item the
-- dispatcher is already holding.
ALTER TABLE work_items ADD COLUMN author TEXT;
"""


def _migration_001(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_001_SQL):
        conn.execute(statement)


def _migration_002(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_002_SQL):
        conn.execute(statement)


def _migration_003(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_003_SQL):
        conn.execute(statement)


def _migration_004(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_004_SQL):
        conn.execute(statement)


SCHEMA_012_SQL = """
-- An anomaly can stop being true without anyone reading it (issue #138).
--
-- `acknowledged_at` records a maintainer saying "I have seen this". That is the only way a
-- row has ever left the open list, which means a condition that resolved itself -- a worker
-- whose process is simply gone -- left its report behind forever. The cost is not the row.
-- It is that `robot-army anomalies` is read as a list of things needing attention, so a
-- list that is mostly stale teaches the habit of clearing it without reading it, which is
-- how the anomaly that mattered gets acknowledged along with the noise.
--
-- Deliberately NOT reused as `acknowledged_at`. "The system re-checked and this is no
-- longer true" and "a human looked at this" are different facts, and `--all` would be
-- unable to tell them apart. That distinction is the entire reason for a second column.
--
-- One column, not two: no `resolved_reason`. Principle III makes the audit log the
-- reconstruction path, and `anomaly.resolved` carries the evidence -- the kind, the entity,
-- and the pid and start time that no longer match. A reason column would duplicate the log
-- somewhere nothing reads it back from.
ALTER TABLE anomalies ADD COLUMN resolved_at TEXT;

-- **The index has to be rebuilt, and this is the part that is easy to get wrong.**
--
-- The partial index is what stops a 60-second reconciliation loop producing 1,440 identical
-- rows a day for one orphan. Acknowledging lifts a row out of it, which is what lets a
-- genuinely new occurrence be recorded later. Resolution has to do exactly the same, and a
-- resolved row left *inside* the index would silently block that condition from ever being
-- reported again -- a worker retired today would make the next orphan under the same
-- session id invisible. SQLite cannot alter an index in place, so it is dropped and rebuilt.
--
-- COALESCE is carried over unchanged and is still load-bearing: in SQLite two NULLs never
-- compare equal, so indexing the bare columns would leave every anomaly with an unspecified
-- entity colliding with nothing and duplicating on every pass.
DROP INDEX idx_anomalies_open;
CREATE UNIQUE INDEX idx_anomalies_open
    ON anomalies (kind, COALESCE(entity_type, ''), COALESCE(entity_id, ''))
    WHERE acknowledged_at IS NULL AND resolved_at IS NULL;
"""


def _migration_005(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_005_SQL):
        conn.execute(statement)


def _migration_006(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_006_SQL):
        conn.execute(statement)


def _migration_007(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_007_SQL):
        conn.execute(statement)


def _migration_008(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_008_SQL):
        conn.execute(statement)


def _migration_009(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_009_SQL):
        conn.execute(statement)


def _migration_010(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_010_SQL):
        conn.execute(statement)


def _migration_011(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_011_SQL):
        conn.execute(statement)


def _migration_012(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_012_SQL):
        conn.execute(statement)


#: Ordered ladder. Index + 1 is the ``user_version`` the migration produces.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migration_001,
    _migration_002,
    _migration_003,
    _migration_004,
    _migration_005,
    _migration_006,
    _migration_007,
    _migration_008,
    _migration_009,
    _migration_010,
    _migration_011,
    _migration_012,
)

SCHEMA_VERSION = len(MIGRATIONS)


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> tuple[int, int]:
    """Apply every outstanding migration. Returns ``(from_version, to_version)``.

    Idempotent: running it against an up-to-date database applies nothing and reports
    the same version twice.
    """
    start = current_version(conn)
    version = start
    for index, migration in enumerate(MIGRATIONS, start=1):
        if index <= version:
            continue
        # Explicit BEGIN because executescript() would otherwise commit implicitly and
        # a crash could leave a half-built schema with the version already advanced.
        conn.execute("BEGIN")
        try:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {index}")
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        version = index
    return start, version
