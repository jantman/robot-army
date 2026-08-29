"""SQLite persistence: connection, pragmas, transactions, and query accessors.

Hand-written SQL against one engine (research.md R2). Three pragmas are not optional:

* ``journal_mode=WAL`` lets read-only CLI commands run while the daemon holds a write
  connection, which is what makes ``robot-army status`` usable against a running daemon.
* ``foreign_keys=ON`` because SQLite defaults it *off* and this schema relies on them.
* ``synchronous=FULL`` because Principle IV assumes the machine loses power, and the
  throughput cost is irrelevant at this write volume.

**The accessor signatures are the FR-056 enforcement mechanism.** Every listing accessor
takes ``include_simulated: bool = False``, so excluding simulated rows is the structural
default and including them is an explicit act at the call site. A convention would drift;
a default argument does not.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from robot_army.cardstates import CardState
from robot_army.migrations import migrate
from robot_army.models import (
    Anomaly,
    Card,
    DispatchControl,
    PollState,
    Repo,
    Session,
    WorkItem,
    from_row,
)
from robot_army.states import SessionState, WorkItemState, utcnow


def connect(db_path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open the database with the pragmas this design depends on."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=timeout,
        isolation_level=None,  # we manage transactions explicitly; see transaction()
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def open_database(db_path: Path) -> tuple[sqlite3.Connection, tuple[int, int]]:
    """Connect and bring the schema up to date. Returns the connection and the versions."""
    conn = connect(db_path)
    versions = migrate(conn)
    return conn, versions


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One unit of atomic work.

    ``IMMEDIATE`` takes the write lock up front rather than on first write, so a
    concurrent writer fails fast at BEGIN instead of halfway through the body.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _scope(include_simulated: bool, *, alias: str = "") -> str:
    """The dry_run filter fragment. Empty only when simulated rows were asked for.

    Returns one of two literal strings and never interpolates caller input, which is why
    the concatenations that use it carry an S608 suppression rather than a rewrite.
    """
    if include_simulated:
        return ""
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}dry_run = 0"


def _rows(cursor: sqlite3.Cursor, cls: type[Any]) -> list[Any]:
    return [from_row(cls, row) for row in cursor.fetchall()]


# -- repos ------------------------------------------------------------------


def get_repo(conn: sqlite3.Connection, repo_key: str) -> Repo | None:
    row = conn.execute("SELECT * FROM repos WHERE repo_key = ?", (repo_key,)).fetchone()
    return from_row(Repo, row) if row else None


def list_repos(conn: sqlite3.Connection) -> list[Repo]:
    return _rows(conn.execute("SELECT * FROM repos ORDER BY repo_key"), Repo)


def upsert_repo(
    conn: sqlite3.Connection,
    *,
    repo_key: str,
    settings_fingerprint: dict[str, str] | None,
    trust_verified: bool,
    clone_path: str | None = None,
    path_source: str | None = None,
    verified_origin: str | None = None,
) -> None:
    """Record an onboarding approval. Re-approval updates the fingerprint timestamp.

    ``verified_origin`` must already be normalised (``repos.normalise_remote``). This
    function does not normalise it, deliberately: a helpful last-minute strip here would
    make it possible to pass a raw credentialed URL and have it appear to be handled,
    which is how the one rule FR-032 states would eventually be broken somewhere else.

    ``get_repo`` and ``list_repos`` need no change — both ``SELECT *``.
    """
    now = utcnow()
    fingerprint = json.dumps(settings_fingerprint, sort_keys=True) if settings_fingerprint else None
    conn.execute(
        """
        INSERT INTO repos (repo_key, onboarded_at, settings_fingerprint,
                           fingerprint_approved_at, trust_verified_at,
                           clone_path, path_source, verified_origin, origin_verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_key) DO UPDATE SET
            settings_fingerprint    = excluded.settings_fingerprint,
            fingerprint_approved_at = excluded.fingerprint_approved_at,
            trust_verified_at       = excluded.trust_verified_at,
            clone_path              = excluded.clone_path,
            path_source             = excluded.path_source,
            verified_origin         = excluded.verified_origin,
            origin_verified_at      = excluded.origin_verified_at
        """,
        (
            repo_key,
            now,
            fingerprint,
            now,
            now if trust_verified else None,
            clone_path,
            path_source,
            verified_origin,
            now if verified_origin else None,
        ),
    )


def touch_repo_trust(conn: sqlite3.Connection, repo_key: str) -> None:
    conn.execute("UPDATE repos SET trust_verified_at = ? WHERE repo_key = ?", (utcnow(), repo_key))


# -- work items -------------------------------------------------------------


def get_work_item(conn: sqlite3.Connection, item_id: int) -> WorkItem | None:
    """Fetch by primary key. No ``include_simulated``: an explicit id is already explicit."""
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
    return from_row(WorkItem, row) if row else None


def find_work_item(
    conn: sqlite3.Connection, *, source: str, source_id: str, dry_run: bool
) -> WorkItem | None:
    row = conn.execute(
        "SELECT * FROM work_items WHERE source = ? AND source_id = ? AND dry_run = ?",
        (source, source_id, int(dry_run)),
    ).fetchone()
    return from_row(WorkItem, row) if row else None


def _work_item_filters(
    states: Sequence[WorkItemState] | None, repo_key: str | None
) -> tuple[str, list[Any]]:
    """The ``states``/``repo_key`` clauses shared by the listing and its withheld count.

    Extracted rather than written twice because milestone 008 requires the number of
    withheld simulated rows to equal *exactly* the rows ``--include-simulated`` would
    reveal. Two hand-written copies of the same predicate would make that equality a claim
    maintained by hand; one construction makes it structural, the way ``_scope`` already
    does for ``dry_run``.
    """
    sql = ""
    params: list[Any] = []
    if states:
        placeholders = ",".join("?" * len(states))
        sql += f" AND state IN ({placeholders})"
        params.extend(str(s) for s in states)
    if repo_key:
        sql += " AND repo_key = ?"
        params.append(repo_key)
    return sql, params


def list_work_items(
    conn: sqlite3.Connection,
    *,
    include_simulated: bool = False,
    states: Sequence[WorkItemState] | None = None,
    repo_key: str | None = None,
    limit: int | None = None,
) -> list[WorkItem]:
    filters, params = _work_item_filters(states, repo_key)
    sql = (
        "SELECT * FROM work_items WHERE 1=1"  # noqa: S608
        + _scope(include_simulated)
        + filters
    )
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _rows(conn.execute(sql, params), WorkItem)


def count_simulated_work_items(
    conn: sqlite3.Connection,
    *,
    states: Sequence[WorkItemState] | None = None,
    repo_key: str | None = None,
) -> int:
    """How many simulated work items a listing under these filters is *not* showing.

    Deliberately carries no ``include_simulated`` parameter — counting withheld rows *is*
    the simulated-only question, and ``include_simulated=False`` here would be nonsense.
    It is therefore not one of ``test_db_scope``'s listing accessors, and must not be added
    to that list.

    A ``COUNT(*)`` rather than a second full fetch to subtract from: the caller wants a
    number, not the rows, and this one runs on every ``status`` render including the web's.
    """
    filters, params = _work_item_filters(states, repo_key)
    sql = "SELECT COUNT(*) AS n FROM work_items WHERE dry_run = 1" + filters  # noqa: S608
    return int(conn.execute(sql, params).fetchone()["n"])


def count_work_items_by_state(
    conn: sqlite3.Connection, *, include_simulated: bool = False
) -> dict[str, int]:
    sql = "SELECT state, COUNT(*) AS n FROM work_items WHERE 1=1" + _scope(  # noqa: S608
        include_simulated
    )
    sql += " GROUP BY state"
    return {row["state"]: row["n"] for row in conn.execute(sql)}


def insert_work_item(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    source_url: str,
    repo_key: str,
    issue_number: int,
    title: str,
    body: str,
    labels: str,
    dry_run: bool,
) -> int | None:
    """Insert a ``discovered`` row, or return ``None`` if it already exists.

    The row is written *before* eligibility is evaluated, so an evaluation interrupted
    halfway is observable as a ``discovered`` row on the next start rather than as
    nothing at all (data-model.md's interruption table).
    """
    now = utcnow()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO work_items
            (source, source_id, source_url, repo_key, issue_number, title, body,
             labels, state, dry_run, discovered_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            source_id,
            source_url,
            repo_key,
            issue_number,
            title,
            body,
            labels,
            str(WorkItemState.DISCOVERED),
            int(dry_run),
            now,
            now,
        ),
    )
    return cursor.lastrowid if cursor.rowcount else None


def update_work_item_columns(
    conn: sqlite3.Connection, item_id: int, **columns: Any
) -> None:
    """Update non-state columns. State changes go through ``states.transition_work_item``."""
    if not columns:
        return
    if "state" in columns:
        raise ValueError("state changes must go through states.transition_work_item()")
    columns["updated_at"] = utcnow()
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE work_items SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), item_id),
    )


def list_cleanup_candidates(
    conn: sqlite3.Connection, *, include_simulated: bool = False
) -> list[WorkItem]:
    """Finished items whose disk may still be reclaimable (milestone 004, R13).

    ``done``, a worktree path on record, and a ``cleanup_state`` the automatic pass would
    still reconsider — ``NULL`` (never looked) or ``skipped`` (looked, a session was live).
    ``retained`` and ``branch_retained`` are decisions rather than pending steps and are
    deliberately absent: only ``robot-army cleanup <id>`` revisits those.

    Scoped by ``dry_run`` like every other listing (FR-056). A simulated item's cleanup is
    simulated too, but *which* rows the caller sees stays the caller's explicit choice.
    """
    sql = (
        "SELECT * FROM work_items WHERE state = ? AND worktree_path IS NOT NULL "  # noqa: S608
        "AND (cleanup_state IS NULL OR cleanup_state = 'skipped')"
        + _scope(include_simulated)
        + " ORDER BY id"
    )
    return _rows(conn.execute(sql, (str(WorkItemState.DONE),)), WorkItem)


def record_cleanup(
    conn: sqlite3.Connection, item_id: int, *, state: str, reason: str | None
) -> None:
    """Write what cleanup decided, and when.

    ``worktree_path`` and ``branch`` are deliberately left alone even on success: FR-024
    requires the record to retain what was removed, ``_sweep_worktrees`` keys on the path
    being present, and "what was at this path?" is exactly the question a retained-branch
    record has to answer months later.
    """
    update_work_item_columns(
        conn,
        item_id,
        cleanup_state=state,
        cleanup_reason=reason,
        cleaned_at=utcnow(),
    )


# -- sessions ---------------------------------------------------------------


def get_session(conn: sqlite3.Connection, session_id: str) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return from_row(Session, row) if row else None


def get_session_by_row_id(conn: sqlite3.Connection, row_id: int) -> Session | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (row_id,)).fetchone()
    return from_row(Session, row) if row else None


def list_sessions_for_item(conn: sqlite3.Connection, item_id: int) -> list[Session]:
    return _rows(
        conn.execute(
            "SELECT * FROM sessions WHERE work_item_id = ? ORDER BY attempt", (item_id,)
        ),
        Session,
    )


def latest_session_for_item(conn: sqlite3.Connection, item_id: int) -> Session | None:
    row = conn.execute(
        "SELECT * FROM sessions WHERE work_item_id = ? ORDER BY attempt DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    return from_row(Session, row) if row else None


def list_sessions(
    conn: sqlite3.Connection,
    *,
    include_simulated: bool = False,
    states: Sequence[SessionState] | None = None,
) -> list[Session]:
    sql = "SELECT * FROM sessions WHERE 1=1" + _scope(include_simulated)  # noqa: S608
    params: list[Any] = []
    if states:
        placeholders = ",".join("?" * len(states))
        sql += f" AND state IN ({placeholders})"
        params.extend(str(s) for s in states)
    sql += " ORDER BY id"
    return _rows(conn.execute(sql, params), Session)


# ``count_live_sessions`` was retired in milestone 004. It counted the daemon's own
# bookkeeping, which is precisely the number that is blind to the author's own Claude
# sessions — right for milestone 001, where the daemon was the only actor being modelled,
# and wrong for a cap that is supposed to protect one subscription on one machine. The
# question changed from "how many did I start?" to "how many are running?", and only the
# session registry answers that.
#
# Its FR-055 reasoning — that simulated sessions occupy a slot, because they burn the same
# quota — did not go away with it. It lives in ``capacity.snapshot``, which counts them for
# the global cap and the per-repository cap alike.


def next_attempt(conn: sqlite3.Connection, item_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) AS n FROM sessions WHERE work_item_id = ?",
        (item_id,),
    ).fetchone()
    return int(row["n"]) + 1


def insert_session(
    conn: sqlite3.Connection,
    *,
    work_item_id: int,
    session_id: str,
    attempt: int,
    dry_run: bool,
    host_socket: str | None = None,
    launch_argv: list[str] | None = None,
) -> int:
    """Write the session row **before** the process starts (FR-020).

    This is what makes every launch failure recoverable: a process that dies before
    writing anything still has a row naming it, so reconciliation has something to
    reason about rather than a gap.
    """
    cursor = conn.execute(
        """
        INSERT INTO sessions
            (work_item_id, session_id, attempt, state, dry_run, host_socket,
             launch_argv, started_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work_item_id,
            session_id,
            attempt,
            str(SessionState.STARTING),
            int(dry_run),
            host_socket,
            json.dumps(launch_argv) if launch_argv else None,
            utcnow(),
        ),
    )
    return int(cursor.lastrowid or 0)


def update_session_columns(conn: sqlite3.Connection, row_id: int, **columns: Any) -> None:
    if not columns:
        return
    if "state" in columns:
        raise ValueError("state changes must go through states.transition_session()")
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE sessions SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), row_id),
    )


# -- cards (milestone 003) --------------------------------------------------


def get_card_by_id(conn: sqlite3.Connection, card_row_id: int) -> Card | None:
    """Fetch by primary key. No ``include_simulated``: an explicit id is already explicit."""
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_row_id,)).fetchone()
    return from_row(Card, row) if row else None


def find_card(
    conn: sqlite3.Connection, *, board_id: str, card_id: str, dry_run: bool
) -> Card | None:
    """The mapping lookup that every creation consults first (R7).

    Keyed exactly as ``idx_cards_identity`` is, so "is this card already tracked?" and
    "would this insert collide?" cannot answer differently.
    """
    row = conn.execute(
        "SELECT * FROM cards WHERE board_id = ? AND card_id = ? AND dry_run = ?",
        (board_id, card_id, int(dry_run)),
    ).fetchone()
    return from_row(Card, row) if row else None


def find_card_by_issue(
    conn: sqlite3.Connection, *, repo_key: str, issue_number: int, dry_run: bool
) -> Card | None:
    """The reverse-direction lookup FR-036 requires.

    Nothing in this milestone creates cards from issues, and this exists so that if
    anything ever tries, it finds the existing mapping rather than making a second one.
    """
    row = conn.execute(
        "SELECT * FROM cards WHERE repo_key = ? AND issue_number = ? AND dry_run = ?",
        (repo_key, issue_number, int(dry_run)),
    ).fetchone()
    return from_row(Card, row) if row else None


def _card_filters(
    states: Sequence[CardState] | None, board_id: str | None
) -> tuple[str, list[Any]]:
    """The ``states``/``board_id`` clauses shared by the card listing and its count.

    Extracted for the same reason as ``_work_item_filters``: the withheld count has to be
    the rows ``--include-simulated`` would reveal, and one construction makes that so.
    """
    sql = ""
    params: list[Any] = []
    if states:
        placeholders = ",".join("?" * len(states))
        sql += f" AND state IN ({placeholders})"
        params.extend(str(s) for s in states)
    if board_id:
        sql += " AND board_id = ?"
        params.append(board_id)
    return sql, params


def count_simulated_cards(
    conn: sqlite3.Connection,
    *,
    states: Sequence[CardState] | None = None,
    board_id: str | None = None,
) -> int:
    """How many simulated cards a listing under these filters is *not* showing.

    Like ``count_simulated_work_items``, it carries no ``include_simulated`` parameter and
    is not one of ``test_db_scope``'s listing accessors.
    """
    filters, params = _card_filters(states, board_id)
    sql = "SELECT COUNT(*) AS n FROM cards WHERE dry_run = 1" + filters  # noqa: S608
    return int(conn.execute(sql, params).fetchone()["n"])


def list_cards(
    conn: sqlite3.Connection,
    *,
    include_simulated: bool = False,
    states: Sequence[CardState] | None = None,
    board_id: str | None = None,
) -> list[Card]:
    filters, params = _card_filters(states, board_id)
    sql = (
        "SELECT * FROM cards WHERE 1=1"  # noqa: S608
        + _scope(include_simulated)
        + filters
    )
    sql += " ORDER BY id"
    return _rows(conn.execute(sql, params), Card)


def insert_card(
    conn: sqlite3.Connection,
    *,
    board_id: str,
    card_id: str,
    card_url: str,
    title: str,
    body: str,
    dry_run: bool,
    last_activity: str | None = None,
    origin_list_id: str | None = None,
    current_list_id: str | None = None,
    current_list_name: str | None = None,
) -> int | None:
    """Insert a ``discovered`` row, or return ``None`` if this card is already tracked.

    The row is written before the card is evaluated, so an evaluation interrupted halfway
    is observable as a ``discovered`` row on the next pass rather than as nothing at all —
    the same reasoning ``insert_work_item`` records, and the first line of data-model.md's
    interruption table.

    ``origin_list_id`` is captured here, at first sighting, because this is the only moment
    the card is guaranteed to be where the author left it. Learning it later would record
    a list *we* put it in as the place it came from.

    ``current_list_id`` starts as the same value and diverges from it the moment the card
    moves — the poll refreshes one and never the other (milestone 006). Seeding it here
    rather than leaving it NULL until the second poll means a card is never briefly
    unanswerable about where it is.
    """
    now = utcnow()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO cards
            (board_id, card_id, card_url, title, body, state, dry_run, last_activity,
             origin_list_id, current_list_id, current_list_name, first_seen_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            board_id,
            card_id,
            card_url,
            title,
            body,
            str(CardState.DISCOVERED),
            int(dry_run),
            last_activity,
            origin_list_id,
            current_list_id,
            current_list_name,
            now,
            now,
        ),
    )
    return cursor.lastrowid if cursor.rowcount else None


def update_card_columns(conn: sqlite3.Connection, card_row_id: int, **columns: Any) -> None:
    """Update non-state columns. State changes go through ``cardstates.transition_card``."""
    if not columns:
        return
    if "state" in columns:
        raise ValueError("state changes must go through cardstates.transition_card()")
    columns["updated_at"] = utcnow()
    assignments = ", ".join(f"{name} = ?" for name in columns)
    conn.execute(
        f"UPDATE cards SET {assignments} WHERE id = ?",  # noqa: S608 - names are ours
        (*columns.values(), card_row_id),
    )


# -- anomalies --------------------------------------------------------------


def raise_anomaly(
    conn: sqlite3.Connection,
    *,
    kind: str,
    detail: dict[str, Any],
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> bool:
    """Record an anomaly. Returns ``True`` if this was a new one.

    Re-detecting an unacknowledged anomaly updates nothing — the partial unique index
    absorbs it. That is what keeps a 60-second reconciliation loop from producing 1,440
    identical rows a day.
    """
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO anomalies (kind, entity_type, entity_id, detail, detected_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (kind, entity_type, entity_id, json.dumps(detail, default=str), utcnow()),
    )
    return bool(cursor.rowcount)


def list_anomalies(
    conn: sqlite3.Connection, *, unacknowledged_only: bool = True
) -> list[Anomaly]:
    sql = "SELECT * FROM anomalies"
    if unacknowledged_only:
        sql += " WHERE acknowledged_at IS NULL"
    sql += " ORDER BY detected_at DESC, id DESC"
    return _rows(conn.execute(sql), Anomaly)


def acknowledge_anomaly(conn: sqlite3.Connection, anomaly_id: int) -> bool:
    cursor = conn.execute(
        "UPDATE anomalies SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
        (utcnow(), anomaly_id),
    )
    return bool(cursor.rowcount)


# -- poll state -------------------------------------------------------------


def get_poll_state(conn: sqlite3.Connection, repo_key: str) -> PollState:
    row = conn.execute("SELECT * FROM poll_state WHERE repo_key = ?", (repo_key,)).fetchone()
    return from_row(PollState, row) if row else PollState(repo_key=repo_key)


def save_poll_state(conn: sqlite3.Connection, state: PollState) -> None:
    conn.execute(
        """
        INSERT INTO poll_state
            (repo_key, etag, last_polled_at, last_status, consecutive_failures, backoff_until)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_key) DO UPDATE SET
            etag                 = excluded.etag,
            last_polled_at       = excluded.last_polled_at,
            last_status          = excluded.last_status,
            consecutive_failures = excluded.consecutive_failures,
            backoff_until        = excluded.backoff_until
        """,
        (
            state.repo_key,
            state.etag,
            state.last_polled_at,
            state.last_status,
            state.consecutive_failures,
            state.backoff_until,
        ),
    )


# -- dispatch control (milestone 002) ---------------------------------------


def get_dispatch_control(conn: sqlite3.Connection) -> DispatchControl:
    """Read the pause state. Never raises on a missing row.

    Migration 002 inserts the row, so its absence would mean a hand-edited database. A
    default-constructed ``DispatchControl`` — not paused — is the honest reading of that:
    refusing to answer would stop the daemon dispatching, which is the failure mode with
    teeth. The daemon's schema-version precondition is what actually catches the
    hand-edited case.
    """
    row = conn.execute(
        "SELECT paused, paused_at, paused_by FROM dispatch_control WHERE id = 1"
    ).fetchone()
    return from_row(DispatchControl, row) if row else DispatchControl()


def set_dispatch_paused(
    conn: sqlite3.Connection, *, paused: bool, by: str
) -> DispatchControl:
    """Suspend or resume dispatch. Called inside ``db.transaction`` by its callers.

    Setting a state that already holds is a **no-op that returns the existing row**, not
    an error: pausing twice is not a mistake, and reporting the pause that is already in
    force — with its original timestamp — is more useful than reporting a fresh one. The
    audit record its caller writes still names the attempt.
    """
    current = get_dispatch_control(conn)
    if current.paused == paused:
        return current
    stamp = utcnow() if paused else None
    actor = by if paused else None
    conn.execute(
        """
        INSERT INTO dispatch_control (id, paused, paused_at, paused_by)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            paused    = excluded.paused,
            paused_at = excluded.paused_at,
            paused_by = excluded.paused_by
        """,
        (int(paused), stamp, actor),
    )
    return DispatchControl(paused=paused, paused_at=stamp, paused_by=actor)


# -- simulated-row purge (FR-058) -------------------------------------------


def purge_simulated(conn: sqlite3.Connection) -> dict[str, int]:
    """Delete every ``dry_run`` row. Never touches live rows, never touches disk.

    Worktrees those rows created are real directories; removing them is
    ``worktree remove``'s job, deliberately separate so purging is not destructive.
    """
    sessions = conn.execute("DELETE FROM sessions WHERE dry_run = 1").rowcount
    items = conn.execute("DELETE FROM work_items WHERE dry_run = 1").rowcount
    cards = conn.execute("DELETE FROM cards WHERE dry_run = 1").rowcount
    return {"work_items": items, "sessions": sessions, "cards": cards}


def count_simulated(conn: sqlite3.Connection) -> dict[str, int]:
    items = conn.execute("SELECT COUNT(*) AS n FROM work_items WHERE dry_run = 1").fetchone()["n"]
    sessions = conn.execute("SELECT COUNT(*) AS n FROM sessions WHERE dry_run = 1").fetchone()["n"]
    cards = conn.execute("SELECT COUNT(*) AS n FROM cards WHERE dry_run = 1").fetchone()["n"]
    return {"work_items": int(items), "sessions": int(sessions), "cards": int(cards)}
