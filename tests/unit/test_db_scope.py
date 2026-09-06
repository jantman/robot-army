"""The FR-056 default query scope (T027).

Simulated rows are absent unless explicitly requested. This is enforced by the accessor
*signatures* rather than by convention — a convention would drift, a default argument
does not — so these tests exercise the accessors rather than the SQL.
"""

from __future__ import annotations

import inspect

import pytest
from tests.conftest import seed_item

from robot_army import db
from robot_army.states import WorkItemState

LISTING_ACCESSORS = [
    db.list_work_items,
    db.list_sessions,
    db.count_work_items_by_state,
    # Issue #21. `anomalies`, `status` and the web's /anomalies page were three surfaces that
    # each had to be fixed separately because none of them could ask this question here.
    db.list_anomalies,
]


@pytest.mark.parametrize("accessor", LISTING_ACCESSORS, ids=lambda f: f.__name__)
def test_every_listing_accessor_defaults_to_excluding_simulated(accessor):
    parameter = inspect.signature(accessor).parameters.get("include_simulated")
    assert parameter is not None, f"{accessor.__name__} must carry include_simulated"
    assert parameter.default is False
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (
        "it must be keyword-only, so including simulated rows is always an explicit act "
        "written out at the call site"
    )


def _seed_both(conn) -> tuple[int, int]:
    live = seed_item(conn, issue_number=1, dry_run=False)
    simulated = seed_item(conn, issue_number=2, dry_run=True)
    return live, simulated


def test_work_item_listings_exclude_simulated_by_default(conn):
    live, simulated = _seed_both(conn)
    assert [i.id for i in db.list_work_items(conn)] == [live]
    assert [i.id for i in db.list_work_items(conn, include_simulated=True)] == [live, simulated]


def test_counts_exclude_simulated_by_default(conn):
    _seed_both(conn)
    assert db.count_work_items_by_state(conn) == {"discovered": 1}
    assert db.count_work_items_by_state(conn, include_simulated=True) == {"discovered": 2}


def test_session_listings_exclude_simulated_by_default(conn):
    live, simulated = _seed_both(conn)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=live, session_id="s-live", attempt=1, dry_run=False)
        db.insert_session(conn, work_item_id=simulated, session_id="s-sim", attempt=1, dry_run=True)
    assert [s.session_id for s in db.list_sessions(conn)] == ["s-live"]
    assert [s.session_id for s in db.list_sessions(conn, include_simulated=True)] == [
        "s-live",
        "s-sim",
    ]


def test_fetching_by_id_needs_no_flag(conn):
    """An explicit id is already an explicit act; requiring a second one would be noise."""
    _, simulated = _seed_both(conn)
    item = db.get_work_item(conn, simulated)
    assert item is not None and item.dry_run is True


# The two cases that used to sit here — a simulated session occupying a slot, and an ended
# one not — moved to tests/unit/test_capacity.py in milestone 004, along with the counting
# itself. ``db.count_live_sessions`` was retired: it answered "how many did I start?", and
# the cap's question became "how many are running?". The requirement did not move; only the
# module that keeps it did.


def test_purge_removes_only_simulated_rows(conn):
    """FR-058, and it covers ``cards`` too since milestone 003: a simulated card row is a
    dry-run row like any other, and leaving it behind would make ``purge-simulated`` a
    verb that only mostly does what its name says."""
    live, simulated = _seed_both(conn)
    with db.transaction(conn):
        db.insert_session(conn, work_item_id=live, session_id="s-live", attempt=1, dry_run=False)
        db.insert_session(conn, work_item_id=simulated, session_id="s-sim", attempt=1, dry_run=True)
        for dry_run in (False, True):
            db.insert_card(
                conn,
                board_id="b1",
                card_id="c1",
                card_url="https://trello.com/c/c1",
                title="a card",
                body="",
                dry_run=dry_run,
            )

    with db.transaction(conn):
        purged = db.purge_simulated(conn)
    assert purged == {"work_items": 1, "sessions": 1, "cards": 1}
    assert [i.id for i in db.list_work_items(conn, include_simulated=True)] == [live]
    assert [s.session_id for s in db.list_sessions(conn, include_simulated=True)] == ["s-live"]
    remaining = db.list_cards(conn, include_simulated=True)
    assert [c.dry_run for c in remaining] == [False]


def test_state_filtering_composes_with_the_scope(conn):
    live, simulated = _seed_both(conn)
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id IN (?, ?)",
        (str(WorkItemState.READY), live, simulated),
    )
    assert [i.id for i in db.list_work_items(conn, states=[WorkItemState.READY])] == [live]
    assert [
        i.id
        for i in db.list_work_items(
            conn, states=[WorkItemState.READY], include_simulated=True
        )
    ] == [live, simulated]


def test_state_changes_cannot_bypass_the_transition_gate(conn):
    """``update_*_columns`` refuses ``state`` outright, so there is exactly one way in."""
    live, _ = _seed_both(conn)
    with pytest.raises(ValueError, match="transition_work_item"):
        db.update_work_item_columns(conn, live, state="active")
    with db.transaction(conn):
        row_id = db.insert_session(
            conn, work_item_id=live, session_id="s-x", attempt=1, dry_run=False
        )
    with pytest.raises(ValueError, match="transition_session"):
        db.update_session_columns(conn, row_id, state="running")


# ``count_simulated_work_items`` is deliberately absent from LISTING_ACCESSORS above. It
# carries no ``include_simulated`` parameter because counting withheld rows *is* the
# simulated-only question, and asking it for the other scope would be nonsense. Adding it
# to that list would fail the structural assertion for the right reason and the wrong
# purpose — milestone 008.


def test_counting_simulated_counts_only_simulated_rows(conn):
    _seed_both(conn)
    assert db.count_simulated_work_items(conn) == 1


def test_counting_simulated_is_zero_on_an_empty_table(conn):
    assert db.count_simulated_work_items(conn) == 0


def test_counting_simulated_honours_the_same_filters_as_the_listing(conn):
    live, simulated = _seed_both(conn)
    other = seed_item(conn, repo_key="other/repo", issue_number=3, dry_run=True)
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id IN (?, ?)",
        (str(WorkItemState.READY), live, simulated),
    )

    assert db.count_simulated_work_items(conn) == 2
    assert db.count_simulated_work_items(conn, states=[WorkItemState.READY]) == 1
    assert db.count_simulated_work_items(conn, repo_key="demo") == 1
    assert db.count_simulated_work_items(conn, repo_key="other/repo") == 1
    assert (
        db.count_simulated_work_items(
            conn, states=[WorkItemState.READY], repo_key="other/repo"
        )
        == 0
    )
    assert other  # the row exists; it is simply not READY


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"states": [WorkItemState.READY]},
        {"states": [WorkItemState.DISCOVERED]},
        {"repo_key": "demo"},
        {"repo_key": "other/repo"},
        {"repo_key": "nobody/nothing"},
        {"states": [WorkItemState.READY], "repo_key": "demo"},
    ],
)
def test_the_count_equals_what_including_simulated_would_reveal(conn, kwargs):
    """The invariant the whole of milestone 008 rests on.

    A withheld count that is merely *close* replaces an obvious contradiction with a
    subtler one, so the number is pinned to the definition that matters: exactly how many
    more rows the listing would show if the caller passed ``include_simulated``.
    """
    live, simulated = _seed_both(conn)
    seed_item(conn, repo_key="other/repo", issue_number=3, dry_run=True)
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id IN (?, ?)",
        (str(WorkItemState.READY), live, simulated),
    )

    revealed = len(db.list_work_items(conn, include_simulated=True, **kwargs)) - len(
        db.list_work_items(conn, **kwargs)
    )
    assert db.count_simulated_work_items(conn, **kwargs) == revealed


# ``list_repo_projects`` is deliberately absent from LISTING_ACCESSORS above, and the
# absence is a decision rather than an oversight (issue #48). ``repo_projects`` has no
# ``dry_run`` column and holds one row per repository: a simulated run and a live run of
# the same repository read the same board, because reading a board makes no outward change
# and there is therefore nothing to withhold. Giving it an ``include_simulated`` parameter
# would be a flag with no meaning, and a flag with no meaning is one a caller will
# eventually pass expecting it to do something.


def test_repo_projects_is_not_scoped_by_dry_run(conn):
    parameter = inspect.signature(db.list_repo_projects).parameters.get("include_simulated")
    assert parameter is None, (
        "repo_projects has no dry_run column; an include_simulated flag here would be "
        "inert, and an inert flag invites a caller to rely on it"
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(repo_projects)")}
    assert "dry_run" not in columns


# The hold listings are absent from LISTING_ACCESSORS for the same kind of reason, and the
# assertions live beside the rest of the hold tests in ``test_holds.py`` (issue #117).
# Neither ``item_holds`` nor ``repo_holds`` has a ``dry_run`` column, and holds apply to
# simulated items *by design*: a dry-run item occupies a queue slot, so a hold that skipped
# it would rehearse the wrong behaviour. What is asserted here is the schema half — that
# there is genuinely no column to scope by, so the exemption cannot quietly become wrong.


def test_hold_tables_have_no_dry_run_column(conn):
    for table in ("item_holds", "repo_holds"):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "dry_run" not in columns, table



def test_anomaly_listings_exclude_simulated_by_default(conn):
    """Issue #21: the flag was advertised on ``anomalies`` and could not be applied.

    The column did not exist, so both spellings of the command printed the same rows — and on
    the reporting machine both outstanding anomalies belonged to dry-run cards and were shown
    as real problems in the default view.
    """
    with db.transaction(conn):
        db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="real",
            detail={"attempts": 3},
        )
        db.raise_anomaly(
            conn, kind="card_create_failing", entity_type="card", entity_id="rehearsed",
            detail={"attempts": 3}, dry_run=True,
        )

    assert [a.entity_id for a in db.list_anomalies(conn)] == ["real"]
    assert {a.entity_id for a in db.list_anomalies(conn, include_simulated=True)} == {
        "real",
        "rehearsed",
    }


def test_the_withheld_listing_is_exactly_what_the_flag_reveals(conn):
    """The equality milestone 008 built ``_work_item_filters`` to guarantee, on a second table.

    ``list_simulated_anomalies`` and ``list_anomalies`` share ``_anomaly_scope``, so the rows
    withheld cannot drift from the rows the flag shows.
    """
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_session", entity_type="session",
                         entity_id="s-real", detail={})
        db.raise_anomaly(conn, kind="orphan_session", entity_type="session",
                         entity_id="s-sim", detail={}, dry_run=True)
        db.raise_anomaly(conn, kind="no_transcript", entity_type="session",
                         entity_id="s-sim2", detail={}, dry_run=True)

    visible = {a.id for a in db.list_anomalies(conn)}
    revealed = {a.id for a in db.list_anomalies(conn, include_simulated=True)}
    withheld = {a.id for a in db.list_simulated_anomalies(conn)}

    assert withheld == revealed - visible
    assert len(withheld) == 2


def test_the_withheld_listing_follows_the_acknowledged_scope_it_is_given(conn):
    """``--all`` widens both populations together, or the count describes a different set."""
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="orphan_session", entity_type="session",
                         entity_id="s-sim", detail={}, dry_run=True)
    row = db.list_anomalies(conn, include_simulated=True)[0]
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, row.id)

    assert db.list_simulated_anomalies(conn) == []
    assert len(db.list_simulated_anomalies(conn, unacknowledged_only=False)) == 1


def test_the_withheld_listing_carries_no_include_simulated_parameter():
    """Listing withheld rows *is* the simulated-only question.

    ``include_simulated=False`` here would be nonsense, which is why this accessor is kept out
    of ``LISTING_ACCESSORS`` above — the same argument ``count_simulated_work_items`` carries.
    """
    parameters = inspect.signature(db.list_simulated_anomalies).parameters
    assert "include_simulated" not in parameters


def test_an_anomaly_naming_no_entity_is_visible_by_default(conn):
    """FR-009. Several kinds are facts about the machine rather than about any rehearsal.

    Withholding one would hide a real problem, which is the opposite of what this flag is for.
    """
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="registry_version_unknown", detail={"versions": ["9.9"]})
        db.raise_anomaly(conn, kind="capacity_unobservable", detail={"reason": "no /proc"})

    assert {a.kind for a in db.list_anomalies(conn)} == {
        "registry_version_unknown",
        "capacity_unobservable",
    }
