"""Migration ladder: fresh create, idempotent re-run, and interruption (T023)."""

from __future__ import annotations

import sqlite3

import pytest

from robot_army import db, migrations
from robot_army.migrations import SCHEMA_VERSION, current_version, migrate

EXPECTED_TABLES = {
    "repos",
    "work_items",
    "sessions",
    "anomalies",
    "poll_state",
    "dispatch_control",
}


def test_fresh_database_creates_every_table_and_index(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    start, end = migrate(conn)
    assert (start, end) == (0, SCHEMA_VERSION)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables >= EXPECTED_TABLES

    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert "idx_work_items_identity" in indexes
    assert "idx_anomalies_open" in indexes
    conn.close()


def test_migrating_twice_is_a_no_op(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)
    start, end = migrate(conn)
    assert (start, end) == (SCHEMA_VERSION, SCHEMA_VERSION)
    conn.close()


def test_interrupted_migration_leaves_user_version_unadvanced(tmp_path, monkeypatch):
    """A crash mid-migration must re-run the whole migration next start.

    ``user_version`` advances as the migration's last statement inside its transaction,
    so a failure before commit rolls back both the schema and the version — which is what
    makes "each migration runs in a transaction" a recovery guarantee rather than a claim.
    """
    conn = db.connect(tmp_path / "state.db")

    def _explode(connection: sqlite3.Connection) -> None:
        # execute(), not executescript(): the latter implicitly commits, which is
        # precisely the trap migrations.py avoids.
        connection.execute("CREATE TABLE half_built (id INTEGER PRIMARY KEY)")
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (_explode,))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 0
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "half_built" not in tables, "the partial schema must have rolled back"
    conn.close()


def test_after_an_interrupted_migration_the_real_ladder_still_applies(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")

    def _explode(connection: sqlite3.Connection) -> None:
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (_explode,))
    with pytest.raises(RuntimeError):
        migrate(conn)
    monkeypatch.undo()

    start, end = migrate(conn)
    assert (start, end) == (0, SCHEMA_VERSION)
    conn.close()


def test_foreign_keys_and_wal_are_actually_on(tmp_path):
    """SQLite defaults foreign_keys *off*, and this schema relies on them."""
    conn = db.connect(tmp_path / "state.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    conn.close()


def test_work_item_identity_is_unique_per_dry_run_flag(tmp_path):
    """The uniqueness key is the idempotency guarantee (FR-072), and including
    ``dry_run`` is deliberate: a simulated run and a later live run coexist."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key="demo", settings_fingerprint=None, trust_verified=True)

    def insert(dry_run: bool) -> int | None:
        with db.transaction(conn):
            return db.insert_work_item(
                conn,
                source="github",
                source_id="demo#1",
                source_url="u",
                repo_key="demo",
                issue_number=1,
                title="t",
                body="b",
                labels="[]",
                dry_run=dry_run,
            )

    assert insert(False) is not None
    assert insert(False) is None, "re-polling the same issue must be a no-op"
    assert insert(True) is not None, "a simulated row may coexist with the live one"
    conn.close()


def test_unacknowledged_anomalies_cannot_duplicate(tmp_path):
    """The partial unique index is what stops a 60-second loop making 1,440 rows a day."""
    conn, _ = db.open_database(tmp_path / "state.db")
    for _ in range(5):
        with db.transaction(conn):
            db.raise_anomaly(
                conn,
                kind="orphan_session",
                entity_type="session",
                entity_id="s-1",
                detail={"pid": 1},
            )
    assert len(db.list_anomalies(conn)) == 1

    with db.transaction(conn):
        db.acknowledge_anomaly(conn, db.list_anomalies(conn)[0].id)
    with db.transaction(conn):
        created = db.raise_anomaly(
            conn,
            kind="orphan_session",
            entity_type="session",
            entity_id="s-1",
            detail={"pid": 1},
        )
    assert created, "acknowledging must allow a genuinely new occurrence to be recorded"
    assert len(db.list_anomalies(conn, unacknowledged_only=False)) == 2
    conn.close()


# -- migration 002: dispatch_control (milestone 002) ------------------------


def _run_only_001(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 001-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    migrations._migration_001(conn)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()


def test_migration_002_runs_on_a_001_era_database(tmp_path):
    """The upgrade path that actually exists: a database created by milestone 001."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_001(conn)
    assert current_version(conn) == 1

    start, end = migrate(conn)
    assert (start, end) == (1, SCHEMA_VERSION)

    row = conn.execute("SELECT paused, paused_at, paused_by FROM dispatch_control").fetchone()
    assert row["paused"] == 0
    assert row["paused_at"] is None
    assert row["paused_by"] is None
    # 001's data is untouched: the migration adds, it does not rewrite.
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables >= EXPECTED_TABLES
    conn.close()


def test_a_killed_migration_002_leaves_user_version_at_one_and_re_runs(tmp_path, monkeypatch):
    """Interruption tolerance for the ladder's second rung, not only its first.

    The whole migration re-runs on the next start, so a partially created table can never
    be observed by a later run — which is what makes the ladder a recovery guarantee.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_001(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_002(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (migrations._migration_001, _explode))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 1
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "dispatch_control" not in tables, "the partial migration must have rolled back"

    monkeypatch.undo()
    start, end = migrate(conn)
    assert (start, end) == (1, SCHEMA_VERSION)
    assert conn.execute("SELECT COUNT(*) AS n FROM dispatch_control").fetchone()["n"] == 1
    conn.close()


def test_the_single_row_check_rejects_a_second_dispatch_control_row(tmp_path):
    """"Which of the two pause rows is authoritative" must never be askable."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO dispatch_control (id, paused) VALUES (2, 1)")
    assert conn.execute("SELECT COUNT(*) AS n FROM dispatch_control").fetchone()["n"] == 1
    conn.close()


def test_setting_an_already_held_pause_state_returns_the_existing_row(tmp_path):
    """Pausing twice is not a mistake. The original timestamp is the useful answer."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with db.transaction(conn):
        first = db.set_dispatch_paused(conn, paused=True, by="cli")
    with db.transaction(conn):
        second = db.set_dispatch_paused(conn, paused=True, by="web")
    assert second == first
    assert second.paused_by == "cli", "the original actor is not overwritten"

    with db.transaction(conn):
        cleared = db.set_dispatch_paused(conn, paused=False, by="web")
    assert cleared.paused is False
    assert cleared.paused_at is None and cleared.paused_by is None
    conn.close()


def test_a_rolled_back_pause_leaves_dispatch_running(tmp_path):
    """Mid-``set_dispatch_paused`` interruption: the pause is never half-applied."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.set_dispatch_paused(conn, paused=True, by="web")
        raise RuntimeError("killed before commit")
    assert db.get_dispatch_control(conn).paused is False
    conn.close()
