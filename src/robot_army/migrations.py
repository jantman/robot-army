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


#: Ordered ladder. Index + 1 is the ``user_version`` the migration produces.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migration_001,
    _migration_002,
    _migration_003,
    _migration_004,
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
