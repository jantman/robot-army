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
    assert SCHEMA_VERSION == len(migrations.MIGRATIONS) == 7


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


def test_migration_005_adds_no_table_and_no_index(tmp_path):
    conn = db.connect(tmp_path / "state.db")
    _run_only_004(conn)
    before = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }

    migrate(conn)

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

    migrate(conn)

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

    migrate(conn)

    after = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")
    }
    assert after == before
    conn.close()
