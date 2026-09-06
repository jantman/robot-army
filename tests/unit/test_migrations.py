"""Migration ladder: fresh create, idempotent re-run, and interruption (T023)."""

from __future__ import annotations

import sqlite3

import pytest

from robot_army import db, migrations
from robot_army.cardstates import CardState
from robot_army.migrations import SCHEMA_VERSION, current_version, migrate
from robot_army.states import WorkItemState

EXPECTED_TABLES = {
    "repos",
    "work_items",
    "sessions",
    "anomalies",
    "poll_state",
    "dispatch_control",
    "cards",
    "repo_projects",
    "item_holds",
    "repo_holds",
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
                author="jantman",
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


# -- migration 003: cards (milestone 003) -----------------------------------


def _run_only_002(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 002-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    migrations._migration_001(conn)
    migrations._migration_002(conn)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()


def _insert_card(conn, **overrides):
    """A raw insert, bypassing ``db.insert_card``'s INSERT OR IGNORE.

    These tests are about what the *schema* refuses. Going through the accessor would
    exercise the accessor's own duplicate suppression instead, which is precisely the
    convention the indexes exist to make unnecessary.
    """
    values = {
        "board_id": "board-1",
        "card_id": "card-1",
        "card_url": "https://trello.com/c/card-1",
        "title": "a card",
        "body": "",
        "state": "discovered",
        "dry_run": 0,
        "repo_key": None,
        "issue_number": None,
        "first_seen_at": "2026-08-24T00:00:00Z",
        "updated_at": "2026-08-24T00:00:00Z",
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    return conn.execute(
        f"INSERT INTO cards ({columns}) VALUES ({placeholders})",  # noqa: S608
        tuple(values.values()),
    )


def test_migration_003_runs_on_a_002_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_002(conn)
    assert current_version(conn) == 2

    start, end = migrate(conn)
    assert (start, end) == (2, SCHEMA_VERSION)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables >= EXPECTED_TABLES
    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_cards_identity", "idx_cards_issue", "idx_cards_state"} <= indexes
    # 002's data survives: the migration adds a table, it does not rewrite anything.
    assert conn.execute("SELECT COUNT(*) AS n FROM dispatch_control").fetchone()["n"] == 1
    conn.close()


def test_a_killed_migration_003_leaves_user_version_at_two_and_re_runs(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")
    _run_only_002(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_003(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (migrations._migration_001, migrations._migration_002, _explode),
    )
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 2
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "cards" not in tables, "the partial migration must have rolled back"

    monkeypatch.undo()
    start, end = migrate(conn)
    assert (start, end) == (2, SCHEMA_VERSION)
    assert conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 0
    conn.close()


def test_one_card_maps_to_at_most_one_issue(tmp_path):
    """Half of the §11 invariant, enforced by ``idx_cards_identity``.

    This is the point of the table: a create path that skipped its mapping check does not
    produce a duplicate, it produces an ``IntegrityError`` — which is loud, and which is
    the difference between an invariant and a convention.
    """
    conn, _ = db.open_database(tmp_path / "state.db")
    _insert_card(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_card(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 1
    conn.close()


def test_one_issue_maps_to_at_most_one_card(tmp_path):
    """The other half, enforced by the partial ``idx_cards_issue``."""
    conn, _ = db.open_database(tmp_path / "state.db")
    _insert_card(conn, card_id="card-1", repo_key="me/demo", issue_number=7)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_card(conn, card_id="card-2", repo_key="me/demo", issue_number=7)
    conn.close()


def test_cards_without_an_issue_do_not_collide_with_each_other(tmp_path):
    """Why ``idx_cards_issue`` is partial. Every ``needs_info`` card has a NULL issue
    number, and a non-partial index would let the first one block all the rest."""
    conn, _ = db.open_database(tmp_path / "state.db")
    _insert_card(conn, card_id="card-1", repo_key="me/demo", issue_number=None)
    _insert_card(conn, card_id="card-2", repo_key="me/demo", issue_number=None)
    assert conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 2
    conn.close()


def test_a_simulated_row_does_not_occupy_the_live_rows_identity(tmp_path):
    """FR-041, in the schema. ``dry_run`` is part of both keys, so a ``no-remote`` run
    followed by a ``live`` run of the same card performs the real creation rather than
    colliding with its own rehearsal."""
    conn, _ = db.open_database(tmp_path / "state.db")
    _insert_card(conn, dry_run=1, repo_key="me/demo", issue_number=7)
    _insert_card(conn, dry_run=0, repo_key="me/demo", issue_number=7)
    assert conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"] == 2
    conn.close()


# -- migration 004 (milestone 004) ------------------------------------------


def _run_only_003(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 003-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    migrations._migration_001(conn)
    migrations._migration_002(conn)
    migrations._migration_003(conn)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()


def _seed_finished_item(conn: sqlite3.Connection) -> int:
    conn.execute(
        "INSERT INTO repos (repo_key, onboarded_at, fingerprint_approved_at) "
        "VALUES ('demo', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    cursor = conn.execute(
        """
        INSERT INTO work_items (source, source_id, source_url, repo_key, issue_number,
                                title, body, labels, state, dry_run, worktree_path,
                                discovered_at, updated_at)
        VALUES ('github', 'demo#1', 'u', 'demo', 1, 't', 'b', '[]', 'done', 0,
                '/w/demo/issue-1', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_migration_004_runs_on_a_003_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_003(conn)
    assert current_version(conn) == 3
    item_id = _seed_finished_item(conn)

    start, end = migrate(conn)
    assert (start, end) == (3, SCHEMA_VERSION)

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(work_items)")
    }
    assert {"cleanup_state", "cleanup_reason", "cleaned_at"} <= columns

    # A pre-existing row reads back as never considered, which is exactly what NULL means:
    # not "clean", not "retained", but "nobody has looked at this yet".
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
    assert row["cleanup_state"] is None
    assert row["cleanup_reason"] is None
    assert row["cleaned_at"] is None
    # And it is still a candidate, so enabling cleanup picks up work that finished before
    # the feature existed — no backfill command.
    assert [i.id for i in db.list_cleanup_candidates(conn)] == [item_id]
    conn.close()


def test_migration_004_does_not_touch_the_work_item_state_machine(tmp_path):
    """``done`` is terminal and stays terminal (R13). Whether the disk has been reclaimed is
    a different axis, and adding a ``cleaned`` state would make every existing query that
    treats ``done`` as terminal subtly wrong."""
    from robot_army.states import WORK_ITEM_TRANSITIONS, WorkItemState

    # No ``cleaned`` state was added, and ``done`` gained no way out of itself.
    assert "cleaned" not in {state.value for state in WorkItemState}
    assert [
        target for source, target in WORK_ITEM_TRANSITIONS if source is WorkItemState.DONE
    ] == []


def test_a_killed_migration_004_leaves_user_version_at_three_and_re_runs(
    tmp_path, monkeypatch
):
    conn = db.connect(tmp_path / "state.db")
    _run_only_003(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_004(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (
            migrations._migration_001,
            migrations._migration_002,
            migrations._migration_003,
            _explode,
        ),
    )
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 3
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert "cleanup_state" not in columns, "the partial migration must have rolled back"

    monkeypatch.undo()
    start, end = migrate(conn)
    assert (start, end) == (3, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert {"cleanup_state", "cleanup_reason", "cleaned_at"} <= columns
    conn.close()


def test_the_schema_version_derives_from_the_ladder_length(tmp_path):
    """Appending a migration is the whole act of adding one. A hand-maintained constant
    beside the tuple is a second thing to remember and a second thing to get wrong."""
    assert SCHEMA_VERSION == len(migrations.MIGRATIONS) == 14


# -- migration 005 (milestone 005, T019) ------------------------------------


def _run_only_004(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 004-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    migrations._migration_001(conn)
    migrations._migration_002(conn)
    migrations._migration_003(conn)
    migrations._migration_004(conn)
    conn.execute("PRAGMA user_version = 4")
    conn.commit()


NEW_REPO_COLUMNS = {"clone_path", "path_source", "verified_origin", "origin_verified_at"}


def test_migration_005_runs_on_a_004_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_004(conn)
    assert current_version(conn) == 4

    start, end = migrate(conn)

    assert (start, end) == (4, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(repos)")}
    assert columns >= NEW_REPO_COLUMNS
    conn.close()


def test_a_killed_migration_005_leaves_user_version_at_four_and_re_runs(tmp_path, monkeypatch):
    """The property data-model.md promises and research R12 says must be *tested* rather
    than assumed: ``ALTER TABLE ... ADD COLUMN`` on an existing column errors, so a
    partially applied set would make the re-run fail permanently. It does not, because the
    ladder wraps the whole sequence in one transaction and rolls back."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_004(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_005(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        (
            migrations._migration_001,
            migrations._migration_002,
            migrations._migration_003,
            migrations._migration_004,
            _explode,
        ),
    )
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 4
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(repos)")}
    assert not (NEW_REPO_COLUMNS & columns), (
        "no half-applied column set may be observable — the whole point of the transaction"
    )

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (4, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(repos)")}
    assert columns >= NEW_REPO_COLUMNS
    conn.close()


def test_a_pre_005_row_reads_back_with_a_null_clone_path_and_its_fingerprint_intact(tmp_path):
    """A NULL ``clone_path`` means *onboarded, location never verified* — not "onboarded at
    an unknown path". Nothing backfills it (research R6), and the row's existing approval
    is untouched by the migration."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_004(conn)
    conn.execute(
        "INSERT INTO repos (repo_key, onboarded_at, settings_fingerprint, "
        "fingerprint_approved_at, trust_verified_at) "
        "VALUES ('jantman/demo', '2026-01-01T00:00:00Z', '{\"a\": \"sha\"}', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()

    migrate(conn)

    record = db.get_repo(conn, "jantman/demo")
    assert record is not None
    assert record.clone_path is None
    assert record.path_source is None
    assert record.verified_origin is None
    assert record.origin_verified_at is None
    assert record.fingerprint == {"a": "sha"}, "the existing approval survives untouched"
    assert record.trust_verified_at == "2026-01-01T00:00:00Z"
    conn.close()


def _run_one(conn: sqlite3.Connection, number: int) -> None:
    """Run exactly the numbered migration, and nothing after it.

    The three ``adds_no_table_and_no_index`` tests below used to call ``migrate()``, which
    runs the *whole remaining ladder*. That asserted what their names say only for as long
    as the migration under test was the last rung: migration 008 adds an index, and it made
    all three fail at once while saying nothing about 005, 006 or 007. Running one rung
    keeps each test's claim its own.
    """
    conn.execute("BEGIN")
    migrations.MIGRATIONS[number - 1](conn)
    conn.commit()


def test_migration_005_adds_no_table_and_no_index(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_004(conn)
    before = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }

    _run_one(conn, 5)

    after = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }
    assert after == before
    conn.close()


# -- migration 006 (milestone 006, T012) ------------------------------------


def _run_only_005(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 005-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    for step in migrations.MIGRATIONS[:5]:
        step(conn)
    conn.execute("PRAGMA user_version = 5")
    conn.commit()


def test_migration_006_runs_on_a_005_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_005(conn)
    assert current_version(conn) == 5

    start, end = migrate(conn)

    assert (start, end) == (5, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
    assert "current_list_id" in columns
    conn.close()


def test_a_pre_006_card_reads_back_with_a_null_current_list_id(tmp_path):
    """Nothing backfills it. A pre-006 row is *tracked but not yet re-polled*, and the
    parked derivation treats NULL as not parked — milestone 003's behaviour, which is the
    safe direction for a value we do not have.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_005(conn)
    conn.execute(
        """
        INSERT INTO cards (board_id, card_id, card_url, title, body, state, dry_run,
                           origin_list_id, first_seen_at, updated_at)
        VALUES ('board-1', 'card-1', 'https://trello.example/c/card-1', 'Old', '',
                'needs_info', 0, 'list-inbox', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()

    migrate(conn)

    row = db.find_card(conn, board_id="board-1", card_id="card-1", dry_run=False)
    assert row is not None
    assert row.current_list_id is None
    # The columns it did have are untouched — a migration that adds must not disturb.
    assert row.origin_list_id == "list-inbox"
    assert row.state is CardState.NEEDS_INFO
    conn.close()


def test_a_killed_migration_006_leaves_user_version_at_five_and_re_runs(tmp_path, monkeypatch):
    """``ALTER TABLE ... ADD COLUMN`` on an existing column errors, so a half-applied
    migration would make the re-run fail permanently. It does not, because the ladder
    wraps the whole sequence in one transaction and rolls back.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_005(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_006(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:5], _explode))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 5
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
    assert "current_list_id" not in columns

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (5, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
    assert "current_list_id" in columns
    conn.close()


def test_migrate_is_idempotent_on_an_already_migrated_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    start, end = migrate(conn)

    assert (start, end) == (SCHEMA_VERSION, SCHEMA_VERSION)
    conn.close()


def test_migration_006_adds_no_table_and_no_index(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_005(conn)
    before = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }

    _run_one(conn, 6)

    after = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }
    assert after == before
    conn.close()


# -- migration 007 (milestone 007) ------------------------------------------


def _run_only_006(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 006-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    for step in migrations.MIGRATIONS[:6]:
        step(conn)
    conn.execute("PRAGMA user_version = 6")
    conn.commit()


def test_migration_007_runs_on_a_006_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_006(conn)
    assert current_version(conn) == 6

    start, end = migrate(conn)

    assert (start, end) == (6, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert {
        "speckit_baseline",
        "speckit_phase",
        "speckit_feature_dir",
        "speckit_phase_at",
    } <= columns
    conn.close()


def test_a_pre_007_item_reads_back_with_a_null_baseline(tmp_path):
    """NULL, not ``[]``. Without a baseline nothing can be attributed to this item, so it
    reports no phase at all — which is a different statement from "a Spec Kit worktree that
    had no features yet", and the two must not collapse into one value.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_006(conn)
    conn.execute(
        """
        INSERT INTO repos (repo_key, onboarded_at, fingerprint_approved_at)
        VALUES ('demo', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO work_items (source, source_id, source_url, repo_key, issue_number,
                                title, body, labels, state, dry_run, worktree_path,
                                discovered_at, updated_at)
        VALUES ('github', 'demo#1', 'https://github.example/1', 'demo', 1, 'Old', '', '[]',
                'active', 0, '/tmp/wt', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """
    )
    conn.commit()

    migrate(conn)

    row = db.get_work_item(conn, 1)
    assert row is not None
    assert row.speckit_baseline is None
    assert row.speckit_phase is None
    # The columns it did have are untouched — a migration that adds must not disturb.
    assert row.worktree_path == "/tmp/wt"
    assert row.state is WorkItemState.ACTIVE
    conn.close()


def test_a_killed_migration_007_leaves_user_version_at_six_and_re_runs(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")
    _run_only_006(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_007(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:6], _explode))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 6
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert "speckit_phase" not in columns

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (6, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert "speckit_phase" in columns
    conn.close()


def test_migration_007_adds_no_table_and_no_index(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_006(conn)
    before = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }

    _run_one(conn, 7)

    after = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }
    assert after == before
    conn.close()


# -- migration 008: the transcript question (issue #58) ----------------------


def _run_only_007(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 007-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    for step in migrations.MIGRATIONS[:7]:
        step(conn)
    conn.execute("PRAGMA user_version = 7")
    conn.commit()


def _seed_session(conn: sqlite3.Connection, session_id: str) -> None:
    """One session row, written the way a 007-era database already holds them."""
    conn.execute(
        "INSERT OR IGNORE INTO repos (repo_key, onboarded_at, fingerprint_approved_at) "
        "VALUES ('o/r', '2026-08-30T00:00:00Z', '2026-08-30T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO work_items (source, source_id, source_url, repo_key, issue_number, "
        "title, body, labels, state, dry_run, discovered_at, updated_at) "
        "VALUES ('github', ?, 'u', 'o/r', 1, 't', 'b', '[]', 'active', 0, '2026-08-30T00:00:00Z', "
        "'2026-08-30T00:00:00Z')",
        (session_id,),
    )
    item_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO sessions (work_item_id, session_id, attempt, state, dry_run, started_at) "
        "VALUES (?, ?, 1, 'running', 0, '2026-08-30T00:00:00Z')",
        (item_id, session_id),
    )


def test_migration_008_runs_on_a_007_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_007(conn)
    assert current_version(conn) == 7

    start, end = migrate(conn)

    assert (start, end) == (7, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "transcript_checked_at" in columns
    conn.close()


def test_migration_008_creates_the_partial_index_the_sweep_reads_through(tmp_path):
    """FR-010 is structural, not incidental: without this index the sweep's query scans
    every session ever dispatched, every 60 seconds, forever."""
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_sessions_transcript_open'"
    ).fetchone()

    assert row is not None
    assert "transcript_checked_at IS NULL" in row["sql"]
    conn.close()


def test_every_pre_008_session_is_backfilled_as_already_checked(tmp_path):
    """The one that matters. These sessions were already judged, correctly or not, by the
    old inline check. Left NULL, the first pass after the upgrade would re-ask the question
    about the entire history at once and report all of it."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_007(conn)
    _seed_session(conn, "old-session-1")
    _seed_session(conn, "old-session-2")

    migrate(conn)

    unchecked = conn.execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE transcript_checked_at IS NULL"
    ).fetchone()["n"]
    assert unchecked == 0
    stamps = [
        row["transcript_checked_at"]
        for row in conn.execute("SELECT transcript_checked_at FROM sessions ORDER BY id")
    ]
    # The app's own format, so ``_age_seconds`` could parse it if anything ever did.
    assert all(s.endswith("Z") and s[10] == "T" for s in stamps), stamps
    conn.close()


def test_a_session_created_after_008_starts_with_an_open_question(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)
    _seed_session(conn, "new-session")

    row = conn.execute(
        "SELECT transcript_checked_at FROM sessions WHERE session_id = 'new-session'"
    ).fetchone()

    assert row["transcript_checked_at"] is None


def test_a_killed_migration_008_leaves_user_version_at_seven_and_re_runs(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "state.db")
    _run_only_007(conn)

    def boom(_conn):
        raise sqlite3.OperationalError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:7], boom))
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)

    assert current_version(conn) == 7
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "transcript_checked_at" not in columns

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (7, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "transcript_checked_at" in columns
    conn.close()


# -- migration 009 (issue #48, T003) ----------------------------------------


def _run_only_008(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 008-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    for step in migrations.MIGRATIONS[:8]:
        step(conn)
    conn.execute("PRAGMA user_version = 8")
    conn.commit()


def test_migration_009_runs_on_an_008_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_008(conn)
    assert current_version(conn) == 8

    start, end = migrate(conn)

    assert (start, end) == (8, SCHEMA_VERSION)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert {"board_column", "board_position"} <= columns
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "repo_projects" in tables
    conn.close()


def test_migration_009_backfills_nothing(tmp_path):
    """The absence of a backfill is the design, not an omission.

    Every pre-009 row means *no board knowledge*, which is exactly what NULL says. An
    upgrade that wrote board facts would be changing dispatch order from information it
    does not have, and would do it before anyone had a chance to look.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_008(conn)
    conn.execute(
        "INSERT INTO repos (repo_key, onboarded_at, fingerprint_approved_at) "
        "VALUES ('jantman/demo', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO work_items (source, source_id, source_url, repo_key, issue_number, "
        "title, body, labels, state, dry_run, discovered_at, updated_at) "
        "VALUES ('github', 'jantman/demo#1', 'u', 'jantman/demo', 1, 't', 'b', '[]', "
        "?, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (str(WorkItemState.READY),),
    )
    conn.commit()

    migrate(conn)

    row = conn.execute(
        "SELECT board_column, board_position FROM work_items WHERE issue_number = 1"
    ).fetchone()
    assert row["board_column"] is None
    assert row["board_position"] is None
    assert conn.execute("SELECT COUNT(*) FROM repo_projects").fetchone()[0] == 0
    conn.close()


def test_migration_009_is_idempotent(tmp_path):
    """A second `migrate` applies nothing. `ALTER TABLE ADD COLUMN` is not idempotent on
    its own, so this is the assertion that the ladder guard actually holds."""
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    start, end = migrate(conn)

    assert (start, end) == (SCHEMA_VERSION, SCHEMA_VERSION)
    conn.close()


def test_an_interrupted_009_leaves_the_version_unadvanced(tmp_path, monkeypatch):
    """Killed mid-migration, nothing is half-applied and the whole step re-runs.

    The transaction is what makes this true, and it is worth pinning rather than
    assuming: a crash that advanced `user_version` past a partly built schema would be
    unrecoverable without hand-editing the database.
    """
    conn = db.connect(tmp_path / "state.db")
    _run_only_008(conn)

    def _boom(_conn):
        _conn.execute("ALTER TABLE work_items ADD COLUMN board_column TEXT")
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:8], _boom))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 8
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    assert "board_column" not in columns
    conn.close()


# -- migration 010: the hold tables (issue #117) -----------------------------


def test_migration_010_creates_both_hold_tables_with_no_backfill(tmp_path):
    """Two tables, and nothing to backfill: no hold existed before this migration, so an
    upgraded database is correct the instant the tables exist."""
    conn, (start, end) = db.open_database(tmp_path / "state.db")
    assert (start, end) == (0, SCHEMA_VERSION)
    assert current_version(conn) == SCHEMA_VERSION

    item_columns = {row["name"] for row in conn.execute("PRAGMA table_info(item_holds)")}
    assert item_columns == {"work_item_id", "held_at", "held_by"}
    repo_columns = {row["name"] for row in conn.execute("PRAGMA table_info(repo_holds)")}
    assert repo_columns == {"repo_key", "held_at", "held_by"}

    assert conn.execute("SELECT COUNT(*) FROM item_holds").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM repo_holds").fetchone()[0] == 0
    conn.close()


def test_migration_010_makes_the_target_the_primary_key(tmp_path):
    """FR-004's idempotence is a constraint, not a convention. A second hold on the same
    target must collide with itself rather than become a second row."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key="demo", settings_fingerprint=None, trust_verified=True)
    with db.transaction(conn):
        conn.execute("INSERT INTO repo_holds VALUES ('demo', 't', 'cli')")
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute("INSERT INTO repo_holds VALUES ('demo', 'later', 'web')")
    conn.close()


def test_migration_010_refuses_a_hold_on_a_repository_that_was_never_onboarded(tmp_path):
    """FR-006 at the schema level: the foreign key makes a typo impossible to store rather
    than merely unlikely. A hold on a repository the system does not watch would hold
    nothing and report nothing wrong, which looks exactly like a hold that works."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute("INSERT INTO repo_holds VALUES ('owner/typo', 't', 'cli')")
    conn.close()


def test_migration_010_holds_carry_no_nullable_provenance(tmp_path):
    """``held_by`` is NOT NULL here, unlike ``dispatch_control.paused_by`` which is
    nullable because it is *cleared* on resume. A hold has no cleared state: the row exists
    or it does not, so every row that exists can say what placed it."""
    conn, _ = db.open_database(tmp_path / "state.db")
    with db.transaction(conn):
        db.upsert_repo(conn, repo_key="demo", settings_fingerprint=None, trust_verified=True)
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute("INSERT INTO repo_holds VALUES ('demo', 't', NULL)")
    conn.close()


# -- migration 011: the recorded issue author (issue #119, RA-01) -------------


def _run_only_010(conn: sqlite3.Connection) -> None:
    """Bring a database to exactly the 010-era schema, as one in the field would be."""
    conn.execute("BEGIN")
    for step in migrations.MIGRATIONS[:10]:
        step(conn)
    conn.execute("PRAGMA user_version = 10")
    conn.commit()


def test_migration_011_adds_the_author_column_to_a_010_era_database(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_010(conn)
    assert current_version(conn) == 10
    assert "author" not in {
        row["name"] for row in conn.execute("PRAGMA table_info(work_items)")
    }

    start, end = migrate(conn)

    assert (start, end) == (10, SCHEMA_VERSION)
    assert "author" in {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    conn.close()


def test_migration_011_leaves_pre_existing_rows_with_a_null_author(tmp_path):
    """No backfill, and the contrast with migration 008 is the argument. 008 backfilled a
    fact it could derive; writing ``config.github.author`` here would be an unverified claim
    in the one column that exists to hold a verified one. ``NULL`` means *never recorded*,
    which is a state ``dispatch`` refuses rather than trusts."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_010(conn)
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO repos (repo_key, onboarded_at, fingerprint_approved_at, "
        "trust_verified_at) VALUES ('demo', 't', 't', 't')"
    )
    conn.execute(
        """
        INSERT INTO work_items
            (source, source_id, source_url, repo_key, issue_number, title, body, labels,
             state, dry_run, discovered_at, updated_at)
        VALUES ('github', 'demo#1', 'u', 'demo', 1, 't', 'b', '[]', 'ready', 0, 'x', 'x')
        """
    )
    conn.commit()

    migrate(conn)

    row = conn.execute("SELECT state, author FROM work_items WHERE source_id = 'demo#1'").fetchone()
    assert row["state"] == "ready", "the migration adds; it does not rewrite"
    assert row["author"] is None, "a pre-011 row's provenance cannot be established"
    conn.close()


def test_migration_011_is_idempotent(tmp_path):
    """Re-running the ladder against an up-to-date database applies nothing. SQLite has no
    ``ADD COLUMN IF NOT EXISTS``, so a second application would raise rather than pass
    quietly — which is exactly what makes this worth asserting."""
    conn, _ = db.open_database(tmp_path / "state.db")
    assert migrate(conn) == (SCHEMA_VERSION, SCHEMA_VERSION)
    conn.close()


def test_a_killed_migration_011_leaves_the_column_absent_and_re_runs(tmp_path, monkeypatch):
    """Interruption tolerance for the newest rung. ``user_version`` advances as the
    migration's last statement inside its transaction, so a crash rolls back the ALTER and
    the version together and the next start applies the whole thing."""
    conn = db.connect(tmp_path / "state.db")
    _run_only_010(conn)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_011(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:10], _explode))
    with pytest.raises(RuntimeError):
        migrate(conn)
    assert current_version(conn) == 10
    assert "author" not in {
        row["name"] for row in conn.execute("PRAGMA table_info(work_items)")
    }

    monkeypatch.undo()
    assert migrate(conn) == (10, SCHEMA_VERSION)
    assert "author" in {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
    conn.close()


# -- migration 012: anomalies that resolve themselves (issue #138) -----------


def test_migration_012_adds_resolved_at_and_leaves_existing_rows_open(tmp_path):
    """No backfill, and none is possible: every pre-migration row is genuinely unresolved
    until a pass re-checks it. The three anomalies live on the machine when this was written
    include one whose process was already gone, and it is the *feature* that clears it, not
    the migration."""
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO anomalies (kind, entity_type, entity_id, detail, detected_at) "
        "VALUES ('orphan_session', 'session', 's-1', '{\"pid\": 1}', '2026-09-05T00:00:00Z')"
    )
    conn.commit()

    rows = conn.execute("SELECT * FROM anomalies").fetchall()
    assert [r["resolved_at"] for r in rows] == [None]


def test_migration_012_rebuilds_the_partial_index_to_exclude_resolved_rows(tmp_path):
    """The half that is silent when it is wrong.

    The index is what stops a 60-second loop writing 1,440 rows a day for one condition. If
    a resolved row stayed inside it, the same condition could never be reported again — no
    error, no failing assertion, just an anomaly that never arrives.
    """
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'idx_anomalies_open'"
    ).fetchone()["sql"]
    assert "acknowledged_at IS NULL" in sql
    assert "resolved_at IS NULL" in sql

    def insert() -> None:
        conn.execute(
            "INSERT OR IGNORE INTO anomalies (kind, entity_type, entity_id, detail, "
            "detected_at) VALUES ('orphan_session', 'session', 's-1', '{}', 'now')"
        )

    insert()
    insert()
    assert conn.execute("SELECT count(*) c FROM anomalies").fetchone()["c"] == 1, (
        "an open anomaly must still be deduplicated"
    )

    conn.execute("UPDATE anomalies SET resolved_at = 'later'")
    insert()
    assert conn.execute("SELECT count(*) c FROM anomalies").fetchone()["c"] == 2, (
        "a resolved row must leave the index so the condition can recur"
    )


def test_migration_012_is_reached_from_an_older_database(tmp_path):
    """The upgrade path that actually exists: this machine's database was at 11."""
    conn = db.connect(tmp_path / "state.db")
    conn.execute("BEGIN")
    for index, migration in enumerate(migrations.MIGRATIONS[:11], start=1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()
    assert current_version(conn) == 11
    conn.execute(
        "INSERT INTO anomalies (kind, entity_type, entity_id, detail, detected_at) "
        "VALUES ('orphan_session', 'session', 's-old', '{}', '2026-09-05T00:00:00Z')"
    )
    conn.commit()

    start, end = migrate(conn)

    assert (start, end) == (11, SCHEMA_VERSION)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(anomalies)")}
    assert "resolved_at" in columns
    row = conn.execute("SELECT * FROM anomalies WHERE entity_id = 's-old'").fetchone()
    assert row["resolved_at"] is None, "an existing anomaly comes through open"


# -- migration 013 (issue #143) ---------------------------------------------


def test_migration_013_is_reached_from_a_012_era_database_with_rows_in_it(tmp_path):
    """The upgrade path that actually exists, with a populated table to come through it.

    Both columns are added to ``work_items``, which is the busiest table in the database, so
    the thing worth pinning is that an existing row survives readable rather than that the
    ``ALTER`` ran.
    """
    conn = db.connect(tmp_path / "state.db")
    conn.execute("BEGIN")
    for index, migration in enumerate(migrations.MIGRATIONS[:12], start=1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()
    assert current_version(conn) == 12
    conn.execute(
        "INSERT INTO repos (repo_key, onboarded_at, fingerprint_approved_at) "
        "VALUES ('jantman/demo', '2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO work_items (source, source_id, source_url, repo_key, issue_number, "
        "title, body, labels, state, dry_run, discovered_at, updated_at) "
        "VALUES ('github', 'jantman/demo#7', 'https://github.com/jantman/demo/issues/7', "
        "'jantman/demo', 7, 'a title', 'a body', '[]', 'done', 0, "
        "'2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z')"
    )
    conn.commit()

    start, end = migrate(conn)

    assert (start, end) == (12, SCHEMA_VERSION)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert {"pull_requests", "pull_requests_at"} <= columns
    row = conn.execute("SELECT * FROM work_items WHERE issue_number = 7").fetchone()
    assert row["title"] == "a title", "the existing row must come through untouched"
    assert row["pull_requests"] is None, (
        "nothing is backfilled: a pre-013 row has never been looked up, and NULL is what "
        "says so — '[]' would claim GitHub was asked and answered none"
    )
    assert row["pull_requests_at"] is None


def test_a_killed_migration_013_leaves_user_version_at_twelve_and_re_runs(
    tmp_path, monkeypatch
):
    """``ADD COLUMN`` on an existing column errors, so a half-applied pair would make every
    later run fail permanently. The ladder's transaction is what stops that, and the
    property is tested rather than assumed — as it is for 005."""
    conn = db.connect(tmp_path / "state.db")
    conn.execute("BEGIN")
    for index, migration in enumerate(migrations.MIGRATIONS[:12], start=1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_013(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(
        migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:12], _explode)
    )
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 12
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert not ({"pull_requests", "pull_requests_at"} & columns), (
        "no half-applied column may be observable"
    )

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (12, SCHEMA_VERSION)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert {"pull_requests", "pull_requests_at"} <= columns
    conn.close()


# -- migration 014 (issue #21) ----------------------------------------------


def _ladder_to(conn: sqlite3.Connection, version: int) -> None:
    """Bring a fresh connection up to exactly ``version``, the way the 012 and 013 cases do."""
    conn.execute("BEGIN")
    for index, migration in enumerate(migrations.MIGRATIONS[:version], start=1):
        migration(conn)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()
    assert current_version(conn) == version


def test_migration_014_is_reached_from_a_013_era_database_with_anomalies_in_it(tmp_path):
    """A pre-014 anomaly comes through readable, and reads back as **real**.

    The direction is the whole point and it is the opposite of 013's. There is no evidence in
    an existing row of which run raised it, so there is nothing to backfill from — and the
    safe reading is ``real``, because a real anomaly shown is a row the maintainer dismisses
    while a real anomaly hidden is a condition nobody ever sees.
    """
    conn = db.connect(tmp_path / "state.db")
    _ladder_to(conn, 13)
    conn.execute(
        "INSERT INTO anomalies (kind, entity_type, entity_id, detail, detected_at) "
        "VALUES ('card_create_failing', 'card', 'card-1', '{}', '2026-09-05T00:00:00Z')"
    )
    conn.commit()

    start, end = migrate(conn)

    assert (start, end) == (13, SCHEMA_VERSION)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(anomalies)")}
    assert "dry_run" in columns
    row = conn.execute("SELECT * FROM anomalies WHERE entity_id = 'card-1'").fetchone()
    assert row["kind"] == "card_create_failing", "the existing row must come through untouched"
    assert row["dry_run"] == 0, (
        "a pre-014 row must read as real: hiding a real anomaly is the one direction that "
        "cannot be recovered from"
    )
    conn.close()


def test_a_pre_014_anomaly_reads_back_through_the_model_as_not_simulated(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _ladder_to(conn, 13)
    conn.execute(
        "INSERT INTO anomalies (kind, entity_type, entity_id, detail, detected_at) "
        "VALUES ('orphan_session', 'session', 's-1', '{}', '2026-09-05T00:00:00Z')"
    )
    conn.commit()
    migrate(conn)

    listed = db.list_anomalies(conn)

    assert [a.entity_id for a in listed] == ["s-1"]
    assert listed[0].dry_run is False, "coerced to a bool, and the bool is False"
    conn.close()


def test_the_rebuilt_index_lets_a_rehearsed_and_a_real_anomaly_coexist(tmp_path):
    """The reason the index had to be rebuilt rather than left alone.

    Without ``dry_run`` in it, ``INSERT OR IGNORE`` keeps whichever of the two arrived first —
    so a real anomaly could be swallowed by a rehearsal and then be *invisible* in the default
    view, which is worse than the defect issue #21 reports.
    """
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    with db.transaction(conn):
        real = db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="card-1",
            detail={"attempts": 3},
        )
        rehearsed = db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="card-1",
            detail={"attempts": 3}, dry_run=True,
        )

    assert (real, rehearsed) == (True, True), "two different facts, two rows"
    assert len(db.list_anomalies(conn, include_simulated=True)) == 2
    assert [a.dry_run for a in db.list_anomalies(conn)] == [False]
    conn.close()


def test_the_rebuilt_index_still_refuses_a_second_open_anomaly_of_the_same_scope(tmp_path):
    """The duplicate suppression 001 added must survive the rebuild, on both sides of the flag.

    This is what stops a 60-second reconciliation loop writing 1,440 identical rows a day.
    """
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    for simulated in (False, True):
        with db.transaction(conn):
            first = db.raise_anomaly(
                conn, kind="orphan_session", entity_type="session", entity_id="s-1",
                detail={"pid": 1}, dry_run=simulated,
            )
            second = db.raise_anomaly(
                conn, kind="orphan_session", entity_type="session", entity_id="s-1",
                detail={"pid": 1}, dry_run=simulated,
            )
        assert (first, second) == (True, False), f"dry_run={simulated}"

    assert len(db.list_anomalies(conn, include_simulated=True)) == 2
    conn.close()


def test_the_rebuilt_index_still_separates_entity_less_anomalies_by_kind(tmp_path):
    """COALESCE is carried over from 012 and is still load-bearing.

    In SQLite two NULLs never compare equal, so indexing the bare columns would leave every
    anomaly with an unspecified entity colliding with nothing and duplicating on every pass.
    """
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    with db.transaction(conn):
        first = db.raise_anomaly(conn, kind="registry_version_unknown", detail={"v": 1})
        again = db.raise_anomaly(conn, kind="registry_version_unknown", detail={"v": 1})
        other = db.raise_anomaly(conn, kind="capacity_unobservable", detail={"why": "x"})

    assert (first, again, other) == (True, False, True)
    conn.close()


def test_resolving_a_rehearsed_anomaly_frees_the_slot_for_a_new_one(tmp_path):
    """Resolution has to lift a row out of the partial index on the rehearsed side too.

    A resolved row left *inside* the index would silently block that condition from ever being
    reported again — the failure migration 012 wrote its own index rebuild to avoid.
    """
    conn = db.connect(tmp_path / "state.db")
    migrate(conn)

    with db.transaction(conn):
        db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="card-1",
            detail={}, dry_run=True,
        )
    open_row = db.list_anomalies(conn, include_simulated=True)[0]
    with db.transaction(conn):
        assert db.resolve_anomaly(conn, open_row.id) is True

    with db.transaction(conn):
        again = db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="card-1",
            detail={}, dry_run=True,
        )

    assert again is True, "a genuinely new occurrence must be recordable after resolution"
    conn.close()


def test_a_killed_migration_014_leaves_user_version_at_thirteen_and_re_runs(
    tmp_path, monkeypatch
):
    """014 drops an index before recreating it, so a half-applied run is not merely untidy.

    A database left with the column added and no ``idx_anomalies_open`` would let the
    reconciliation loop duplicate anomalies forever, silently. The ladder's transaction is
    what prevents it, and the property is tested rather than assumed.
    """
    conn = db.connect(tmp_path / "state.db")
    _ladder_to(conn, 13)

    def _explode(connection: sqlite3.Connection) -> None:
        migrations._migration_014(connection)
        raise RuntimeError("killed mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS[:13], _explode))
    with pytest.raises(RuntimeError):
        migrate(conn)

    assert current_version(conn) == 13
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(anomalies)")}
    assert "dry_run" not in columns, "no half-applied column may be observable"
    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert "idx_anomalies_open" in indexes, (
        "the dropped index must be back: without it nothing suppresses duplicate anomalies"
    )

    monkeypatch.undo()
    start, end = migrate(conn)

    assert (start, end) == (13, SCHEMA_VERSION)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(anomalies)")}
    assert "dry_run" in columns
    conn.close()
