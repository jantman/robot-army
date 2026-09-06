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
    Hold,
    PollState,
    Repo,
    RepoProject,
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
    author: str,
    dry_run: bool,
) -> int | None:
    """Insert a ``discovered`` row, or return ``None`` if it already exists.

    The row is written *before* eligibility is evaluated, so an evaluation interrupted
    halfway is observable as a ``discovered`` row on the next start rather than as
    nothing at all (data-model.md's interruption table).

    ``author`` is required rather than defaulted, because a default is exactly the
    fabrication migration 011 exists to delete: the only correct value is the one the read
    that produced this row reported, and a caller that cannot supply it has no business
    creating the row.
    """
    now = utcnow()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO work_items
            (source, source_id, source_url, repo_key, issue_number, title, body,
             labels, author, state, dry_run, discovered_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            author,
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


def list_pull_request_candidates(conn: sqlite3.Connection) -> list[WorkItem]:
    """Work items whose pull-request answer can still change (issue #143).

    The whole candidate rule, in one query, because it is one question: *can this item's
    answer still change?* Doing it in SQL rather than by filtering a listing in Python is
    what keeps the cost proportional to the answer instead of to the history — the terminal
    half would otherwise rebuild every work item ever finished into a dataclass once a
    minute, to discard nearly all of them.

    Three clauses, and each is load-bearing:

    * **A live state** — ``active``, ``awaiting_review``, ``interrupted``. A pull request can
      appear or change at any moment.
    * **A stored pull request still open**, whatever the state. ``reconcile`` marks an item
      ``done`` the moment its issue closes, and an issue can be closed by hand while its
      pull request is open; without this the page would read ``open`` for ever.
    * **A stored *empty* set with a session still running.** An empty set is not settled the
      way ``merged`` is: the worker that has not opened a pull request yet may still open
      one. Bounded by the session, so it stops as soon as no process could produce one.

    Each clause runs out on its own, which is what lets this feature have no interval, no
    cap and no configuration key. ``pull_requests IS NOT NULL`` on the last two is what
    keeps "never looked up" from being backfilled: a terminal row from before migration 013
    is history, and reads as *not checked* because that is what it is.

    Simulated rows and rows without a branch are excluded here rather than skipped by the
    caller, because both are "there is no question to ask" rather than "the answer has not
    changed" — and asking GitHub about a simulated row is the outward effect dry-run exists
    to avoid.
    """
    cursor = conn.execute(
        """
        SELECT * FROM work_items
         WHERE dry_run = 0
           AND branch IS NOT NULL AND branch != ''
           AND (
                 state IN ('active', 'awaiting_review', 'interrupted')
              OR (pull_requests IS NOT NULL AND EXISTS (
                    SELECT 1 FROM json_each(work_items.pull_requests)
                     WHERE json_extract(json_each.value, '$.state') = 'open'))
              OR (pull_requests = '[]' AND EXISTS (
                    SELECT 1 FROM sessions
                     WHERE sessions.work_item_id = work_items.id
                       AND sessions.state IN ('starting', 'running')))
           )
         ORDER BY id
        """
    )
    return _rows(cursor, WorkItem)


def record_pull_requests(
    conn: sqlite3.Connection, item_id: int, *, found: str, at: str
) -> None:
    """Store one item's pull-request set and when it was confirmed (issue #143).

    Its own statement rather than ``update_work_item_columns`` for one reason:
    ``updated_at`` must not move. This runs every reconcile pass for every live item, and
    almost every run confirms an unchanged set — so routing it through the general updater
    would push ``updated_at`` forward once a minute for every item in the system, making a
    column that means "when this item last changed" mean "when the daemon last looked",
    and quietly falsifying every age derived from it.

    Both columns are written together, always. The unchanged case writes the identical
    text back, which keeps one path through the code and costs one row.
    """
    conn.execute(
        "UPDATE work_items SET pull_requests = ?, pull_requests_at = ? WHERE id = ?",
        (found, at, item_id),
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


def previous_session_for_item(
    conn: sqlite3.Connection, item_id: int, attempt: int
) -> Session | None:
    """The session this attempt replaces, or ``None`` when there is nothing before it.

    The ``attempt < ?`` bound is the entire reason this exists rather than a call to
    ``latest_session_for_item``. A session row is written **before** its process is
    launched, so by the time anything downstream asks what came earlier, this attempt's own
    row is already the latest one — and the honest-looking answer would be that the session
    supersedes itself.

    ``None`` is a real answer, not a failure: a rebuilt database or pruned history leaves an
    attempt with no recorded predecessor, and saying so beats inventing one.
    """
    row = conn.execute(
        "SELECT * FROM sessions WHERE work_item_id = ? AND attempt < ? ORDER BY attempt DESC "
        "LIMIT 1",
        (item_id, attempt),
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


def sessions_awaiting_transcript_check(conn: sqlite3.Connection) -> list[Session]:
    """Sessions whose transcript question is still open — ``transcript_checked_at IS NULL``.

    The whole population, with no state filter, no age filter and no ``dry_run`` filter.
    Deliberately so: the column *is* the population (issue #58 research R2). Every session
    is resolved exactly once and then leaves this set permanently, which is why no "recently
    ended" window is needed to keep it bounded — there is nothing left in it to bound.

    ``idx_sessions_transcript_open`` is what keeps this off a full-history scan (FR-010).
    The result is tiny either way; the scan would not have been.
    """
    return _rows(
        conn.execute(
            "SELECT * FROM sessions WHERE transcript_checked_at IS NULL ORDER BY id"
        ),
        Session,
    )


def mark_transcript_checked(conn: sqlite3.Connection, session_row_id: int) -> None:
    """Close a session's transcript question, for good.

    Never cleared afterwards and never re-opened: a transcript deleted after the fact, an
    acknowledged anomaly, or a resumed item do not re-ask it. A *new* session row is a new
    question. This is what makes one anomaly per session hold where the anomalies table's
    partial unique index cannot, since that index only dedupes unacknowledged rows.
    """
    conn.execute(
        "UPDATE sessions SET transcript_checked_at = ? WHERE id = ?",
        (utcnow(), session_row_id),
    )


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


def highest_simulated_issue_number(conn: sqlite3.Connection, *, repo_key: str) -> int | None:
    """The highest fake issue number already recorded for ``repo_key``, or ``None``.

    Read by ``SimulatedIssueWriter`` to decide the next one. The three columns here are
    exactly ``idx_cards_issue``'s three, and that is the whole point: a number derived from
    the same record the constraint is checked against cannot be refused by it. Deriving it
    from anything else — a per-process counter, which is what this replaces — makes every
    restart replay numbers the index already holds, so the card is refused, retried with the
    next number in the sequence, and refused again once per row that exists (issue #22).

    ``dry_run = 1`` rather than a parameter because the simulated writer is selected under
    exactly the condition that makes the rows it produces simulated: ``REAL_AT`` gives the
    real writer only at ``live``, and card rows carry ``dry_run=effect_level.is_simulated``.
    Filtering on it is what keeps the simulated numbering out of the live number space.

    ``MAX`` ignores ``NULL``, which is why rows with no issue yet — every ``needs_info``
    card, and every ``creating`` card before its issue exists — need no exclusion clause.
    """
    row = conn.execute(
        "SELECT MAX(issue_number) FROM cards WHERE repo_key = ? AND dry_run = 1",
        (repo_key,),
    ).fetchone()
    highest = row[0] if row else None
    return int(highest) if highest is not None else None


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


def find_card_on_any_board(
    conn: sqlite3.Connection, *, card_id: str, dry_run: bool
) -> Card | None:
    """One card by its Trello id, without needing to know which board it is on.

    ``find_card`` above takes a ``board_id`` because every caller in the intake path is already
    holding one. The anomaly resolver is not: it runs in reconciliation, which reaches this
    without reading the board at all — deliberately, so a retraction does not depend on Trello
    being reachable. ``idx_cards_identity`` is over ``(board_id, card_id, dry_run)`` and Trello
    card ids are globally unique, so the pair here identifies at most one row in practice.

    ``dry_run`` is part of the lookup rather than ignored: one card id can have both a real and
    a rehearsed row, and resolving a rehearsed anomaly against a real card's success would be a
    retraction of something that never happened.

    No ``include_simulated``: this is a lookup by identity, not a listing, and the caller states
    which of the two it means.
    """
    row = conn.execute(
        "SELECT * FROM cards WHERE card_id = ? AND dry_run = ?",
        (card_id, int(dry_run)),
    ).fetchone()
    return from_row(Card, row) if row else None


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
    dry_run: bool = False,
) -> bool:
    """Record an anomaly. Returns ``True`` if this was a new one.

    Re-detecting an unacknowledged anomaly updates nothing — the partial unique index
    absorbs it. That is what keeps a 60-second reconciliation loop from producing 1,440
    identical rows a day. ``dry_run`` is part of that index (migration 014), so a rehearsed
    run and a live one reporting the same condition for the same entity produce two rows
    rather than one — they are different facts, and collapsing them would let a rehearsal
    swallow a real report by arriving first.

    ``dry_run`` defaults to ``False`` rather than being required, and the asymmetry is the
    reason: a call site that forgets it raises a *visible* anomaly. The opposite default
    would make forgetting it hide a real condition, which is the failure this flag exists to
    prevent. It is a property of the run, so the caller supplies its subject's flag — see
    the table in the feature's research.md for which of the seventeen sites pass what.
    """
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO anomalies
            (kind, entity_type, entity_id, detail, detected_at, dry_run)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            entity_type,
            entity_id,
            json.dumps(detail, default=str),
            utcnow(),
            int(dry_run),
        ),
    )
    return bool(cursor.rowcount)


def _anomaly_scope(unacknowledged_only: bool) -> str:
    """The open/closed clause shared by the listing and its withheld companion.

    Extracted for the reason ``_work_item_filters`` was: the number of withheld simulated
    rows must equal *exactly* the rows ``--include-simulated`` would reveal, and two
    hand-written copies of this predicate would make that equality a claim maintained by
    hand rather than a fact about the construction.
    """
    return " AND acknowledged_at IS NULL AND resolved_at IS NULL" if unacknowledged_only else ""


def list_anomalies(
    conn: sqlite3.Connection,
    *,
    include_simulated: bool = False,
    unacknowledged_only: bool = True,
) -> list[Anomaly]:
    """Anomalies still needing attention, or every one ever recorded.

    A resolved row is excluded by the same flag that excludes an acknowledged one, and that
    is deliberately the only place the distinction is made: the CLI, ``status`` and the web
    page are three callers of this one function, so they all became correct about issue
    #138's self-resolving anomalies without any of them being edited. ``include_simulated``
    was added for the same reason and pays off the same way — issue #21's defect was that
    ``anomalies``, ``status`` and ``/anomalies`` each had to be fixed separately because none
    of them could ask this function the question.
    """
    sql = (
        "SELECT * FROM anomalies WHERE 1=1"  # noqa: S608
        + _scope(include_simulated)
        + _anomaly_scope(unacknowledged_only)
    )
    sql += " ORDER BY detected_at DESC, id DESC"
    return _rows(conn.execute(sql), Anomaly)


def list_simulated_anomalies(
    conn: sqlite3.Connection, *, unacknowledged_only: bool = True
) -> list[Anomaly]:
    """The anomalies a listing under these filters is *not* showing.

    Deliberately carries no ``include_simulated`` parameter — listing withheld rows *is* the
    simulated-only question, and ``include_simulated=False`` here would be nonsense. It is
    therefore not one of ``test_db_scope``'s listing accessors and must not be added to that
    list, exactly as ``count_simulated_work_items`` is not.

    **Rows rather than a ``COUNT(*)``, which is the one thing that separates it from its
    work-item counterpart.** ``anomalies --since`` is applied in Python by
    ``operations._within_window``, on purpose: ``detected_at`` is TEXT, so SQL would compare a
    malformed stamp lexicographically and drop the row with nothing anywhere in a position to
    notice (012 research R2). A count taken here could therefore name a number the flag would
    not then reveal. Handing back the rows lets the caller apply the identical window
    predicate to both populations, which makes the equality structural.
    """
    sql = (
        "SELECT * FROM anomalies WHERE dry_run = 1"  # noqa: S608
        + _anomaly_scope(unacknowledged_only)
    )
    sql += " ORDER BY detected_at DESC, id DESC"
    return _rows(conn.execute(sql), Anomaly)


def open_orphan_session_anomalies(conn: sqlite3.Connection) -> list[Anomaly]:
    """The population ``reconcile._resolve_orphan_anomalies`` re-checks (issue #138).

    Narrow on purpose. ``orphan_session`` is the one kind whose condition can be positively
    re-established as *false* — the process it names is gone — and every other kind has its
    own settling story that this mechanism has no business guessing at.
    """
    return _rows(
        conn.execute(
            "SELECT * FROM anomalies WHERE kind = 'orphan_session' "
            "AND acknowledged_at IS NULL AND resolved_at IS NULL ORDER BY id"
        ),
        Anomaly,
    )


def open_card_create_failing_anomalies(conn: sqlite3.Connection) -> list[Anomaly]:
    """The population ``reconcile._resolve_card_create_anomalies`` re-checks (issue #21).

    Narrow by construction, exactly like ``open_orphan_session_anomalies`` above and for the
    same reason. ``card_create_failing`` is the second kind whose truth can be positively
    re-established as *false* — the card it named has since been linked to an issue, so the
    creation it reported as failing has succeeded. Every other kind has its own settling story
    that this mechanism has no business guessing at.

    Rehearsed rows are included deliberately: this is not a listing, and an anomaly raised by a
    rehearsal is exactly as entitled to be retracted as a real one. Withholding retraction from
    it would leave the rehearsal's list going stale, which is the problem, not the scope.
    """
    return _rows(
        conn.execute(
            "SELECT * FROM anomalies WHERE kind = 'card_create_failing' "
            "AND acknowledged_at IS NULL AND resolved_at IS NULL ORDER BY id"
        ),
        Anomaly,
    )


def acknowledge_anomaly(conn: sqlite3.Connection, anomaly_id: int) -> bool:
    cursor = conn.execute(
        "UPDATE anomalies SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
        (utcnow(), anomaly_id),
    )
    return bool(cursor.rowcount)


def resolve_anomaly(conn: sqlite3.Connection, anomaly_id: int) -> bool:
    """Record that the condition no longer holds. ``True`` if this call changed anything.

    The ``resolved_at IS NULL`` guard is what makes a repeated pass a genuine no-op rather
    than a second write with the same effect, which is the same shape ``acknowledge_anomaly``
    uses and the reason both return a boolean.
    """
    cursor = conn.execute(
        "UPDATE anomalies SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
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


# -- project boards (issue #48) ---------------------------------------------


def get_repo_project(conn: sqlite3.Connection, repo_key: str) -> RepoProject:
    """This repository's board resolution. Never ``None``.

    A default-constructed row rather than ``None`` when absent, exactly as
    ``get_poll_state`` does, so no caller has to branch on existence to ask a question
    every caller asks. The default is *unresolved and never read*, which is the correct
    reading of a repository nothing has looked at yet.
    """
    row = conn.execute(
        "SELECT * FROM repo_projects WHERE repo_key = ?", (repo_key,)
    ).fetchone()
    return from_row(RepoProject, row) if row else RepoProject(repo_key=repo_key)


def list_repo_projects(conn: sqlite3.Connection) -> dict[str, RepoProject]:
    """Every board resolution, keyed by repository.

    Deliberately **not** given an ``include_simulated`` parameter, and the omission is the
    point rather than an oversight — see the note in ``tests/unit/test_db_scope.py``.
    ``repo_projects`` has no ``dry_run`` column and holds one row per repository: a
    simulated run and a live run of the same repository read the same board, because a
    board read makes no outward change and there is nothing to withhold.

    Returned whole because ``ordering.plan`` needs all of it once per plan rather than one
    query per queued item — the same reasoning as ``repos.resolved_all``.
    """
    return {
        row["repo_key"]: from_row(RepoProject, row)
        for row in conn.execute("SELECT * FROM repo_projects")
    }


def save_repo_project(conn: sqlite3.Connection, state: RepoProject) -> None:
    """Upsert one repository's board resolution.

    Every column is written on every save, including the NULLs. A partial update would
    let a stale ``project_title`` outlive the project it named, and the row is small
    enough that writing it whole is cheaper than reasoning about which half is current.
    """
    conn.execute(
        """
        INSERT INTO repo_projects
            (repo_key, project_id, project_number, project_title, project_url,
             project_source, column_name, column_source, resolved_at, unresolved_reason,
             last_read_at, last_error, consecutive_failures, backoff_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_key) DO UPDATE SET
            project_id           = excluded.project_id,
            project_number       = excluded.project_number,
            project_title        = excluded.project_title,
            project_url          = excluded.project_url,
            project_source       = excluded.project_source,
            column_name          = excluded.column_name,
            column_source        = excluded.column_source,
            resolved_at          = excluded.resolved_at,
            unresolved_reason    = excluded.unresolved_reason,
            last_read_at         = excluded.last_read_at,
            last_error           = excluded.last_error,
            consecutive_failures = excluded.consecutive_failures,
            backoff_until        = excluded.backoff_until
        """,
        (
            state.repo_key,
            state.project_id,
            state.project_number,
            state.project_title,
            state.project_url,
            state.project_source,
            state.column_name,
            state.column_source,
            state.resolved_at,
            state.unresolved_reason,
            state.last_read_at,
            state.last_error,
            state.consecutive_failures,
            state.backoff_until,
        ),
    )


def apply_board_facts(
    conn: sqlite3.Connection,
    repo_key: str,
    *,
    ranked: dict[int, int],
    elsewhere: dict[int, str],
    column_name: str,
) -> int:
    """Write one board snapshot over a repository's items. Returns rows touched.

    **Clearing is half the job.** Every item of the repository that the snapshot does not
    mention has its board facts set back to NULL, because a card removed from the board
    must stop being ranked and must stop being held — and an update that only wrote the
    items it saw would leave yesterday's answer in place for exactly the items whose
    answer changed.

    One clearing statement, then one statement per item the snapshot mentions. Not a
    single `CASE` expression, and the docstring said otherwise until review caught it:
    the counts here are tens, so the loop costs nothing measurable, and a `CASE` over a
    dict of issue numbers is harder to read than the thing it replaces. **The atomicity
    that matters is the caller's transaction, not this function's statement count** — the
    repository cannot be observed half in one snapshot and half in another because the
    whole call is inside one `BEGIN IMMEDIATE`. Do not read a per-statement guarantee
    into this that it does not provide.

    ``dry_run`` is not filtered. A simulated item occupies a queue position like any
    other, so it is ordered like any other; the board read that produced this made no
    outward change to withhold.
    """
    conn.execute(
        "UPDATE work_items SET board_column = NULL, board_position = NULL "
        "WHERE repo_key = ?",
        (repo_key,),
    )
    touched = 0
    for number, position in ranked.items():
        touched += conn.execute(
            "UPDATE work_items SET board_column = ?, board_position = ? "
            "WHERE repo_key = ? AND issue_number = ?",
            (column_name, position, repo_key, number),
        ).rowcount
    for number, other_column in elsewhere.items():
        touched += conn.execute(
            "UPDATE work_items SET board_column = ?, board_position = NULL "
            "WHERE repo_key = ? AND issue_number = ?",
            (other_column, repo_key, number),
        ).rowcount
    return touched


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


# -- dispatch holds (issue #117) --------------------------------------------
#
# Two tables rather than one with a scope column, so that "a hold never outlives what it
# holds" (FR-025) is a foreign key rather than a rule every future deletion site has to
# remember. See migration 010 for the full argument.
#
# Neither listing takes ``include_simulated``, and its absence is a decision rather than an
# oversight — see ``tests/unit/test_db_scope.py``. Neither table has a ``dry_run`` column,
# and holds apply to simulated items *by design*: a dry-run item occupies a queue slot, so
# a hold that skipped it would rehearse the wrong behaviour.


def list_item_holds(conn: sqlite3.Connection) -> dict[int, Hold]:
    """Every item hold in force, keyed by work item id.

    One scan, because ``ordering.plan`` calls this once per plan — on every dispatch tick
    *and* every web page render — and a query per queued item would multiply by the queue
    length on every page load.

    An empty table is not an error and never raises: no holds is the overwhelmingly common
    state.
    """
    return {
        int(row["work_item_id"]): Hold(held_at=row["held_at"], held_by=row["held_by"])
        for row in conn.execute("SELECT work_item_id, held_at, held_by FROM item_holds")
    }


def list_repo_holds(conn: sqlite3.Connection) -> dict[str, Hold]:
    """Every repository hold in force, keyed by repository key. Same shape, same reasons."""
    return {
        str(row["repo_key"]): Hold(held_at=row["held_at"], held_by=row["held_by"])
        for row in conn.execute("SELECT repo_key, held_at, held_by FROM repo_holds")
    }


def _set_hold(
    conn: sqlite3.Connection, *, table: str, column: str, target: Any, by: str
) -> tuple[Hold, bool]:
    """Place a hold, or report the one already in force. Returns ``(hold, newly_placed)``.

    ``ON CONFLICT DO NOTHING`` then read back, so holding something already held returns
    the **existing** row with its **original** ``held_at`` — never a refreshed one. This is
    the judgement ``set_dispatch_paused`` already makes and states: pausing twice is not a
    mistake, and the pause that is already in force, with its original timestamp, is the
    more useful answer than a fresh one (FR-004).

    ``table`` and ``column`` are module-private literals, never caller input, which is why
    the two interpolations below carry an S608 suppression rather than a rewrite — the same
    treatment ``_scope`` already gets.
    """
    placed = conn.execute(
        f"INSERT INTO {table} ({column}, held_at, held_by) VALUES (?, ?, ?) "  # noqa: S608
        f"ON CONFLICT({column}) DO NOTHING",
        (target, utcnow(), by),
    ).rowcount
    row = conn.execute(
        f"SELECT held_at, held_by FROM {table} WHERE {column} = ?",  # noqa: S608
        (target,),
    ).fetchone()
    return Hold(held_at=row["held_at"], held_by=row["held_by"]), bool(placed)


def _clear_hold(
    conn: sqlite3.Connection, *, table: str, column: str, target: Any
) -> Hold | None:
    """Release a hold, returning the one removed, or ``None`` if there was none.

    Returning what was removed is FR-005: the caller distinguishes "released a hold placed
    at *t*" from "there was nothing to release" without a second query, and reports the
    second as a no-op rather than a failure.
    """
    row = conn.execute(
        f"SELECT held_at, held_by FROM {table} WHERE {column} = ?",  # noqa: S608
        (target,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (target,))  # noqa: S608
    return Hold(held_at=row["held_at"], held_by=row["held_by"])


def set_item_hold(conn: sqlite3.Connection, item_id: int, *, by: str) -> tuple[Hold, bool]:
    """Hold one work item. Called inside ``db.transaction`` by its callers."""
    return _set_hold(conn, table="item_holds", column="work_item_id", target=item_id, by=by)


def clear_item_hold(conn: sqlite3.Connection, item_id: int) -> Hold | None:
    return _clear_hold(conn, table="item_holds", column="work_item_id", target=item_id)


def set_repo_hold(conn: sqlite3.Connection, repo_key: str, *, by: str) -> tuple[Hold, bool]:
    """Hold every work item in one repository, present and future (FR-012)."""
    return _set_hold(conn, table="repo_holds", column="repo_key", target=repo_key, by=by)


def clear_repo_hold(conn: sqlite3.Connection, repo_key: str) -> Hold | None:
    return _clear_hold(conn, table="repo_holds", column="repo_key", target=repo_key)


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
