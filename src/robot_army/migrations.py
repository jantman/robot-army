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
    on a crash — exactly the interruption behaviour data-model.md promises against. The
    naive split is safe here only because these scripts contain no semicolons inside
    string literals or trigger bodies; a future migration that does must not use it.
    """
    statements: list[str] = []
    for chunk in script.split(";"):
        # Strip whole-line comments: a statement preceded by explanatory comments is
        # still a statement, and dropping the chunk would silently omit it.
        body = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            statements.append(body)
    return statements


def _migration_001(conn: sqlite3.Connection) -> None:
    for statement in _statements(SCHEMA_001_SQL):
        conn.execute(statement)


#: Ordered ladder. Index + 1 is the ``user_version`` the migration produces.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_001,)

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
