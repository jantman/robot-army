"""Every listing distinguishes absence from withholding (milestone 008, then issue #21).

``cards`` and ``worktree list`` came first. Neither displays a section that contradicts it,
so neither reproduced issue #13's visible self-contradiction. Both nonetheless reported
"nothing is tracked" and "nothing is recorded" while withholding rows, which leaves the
reader unable to tell the two situations apart — the same defect one step quieter.

``anomalies`` and ``status``'s anomaly block came later, and were worse: they did not
withhold at all. The flag was advertised, accepted, and had no effect, so a rehearsal's
anomalies were reported in the default view as outstanding real problems (issue #21). The
cases below hold both halves — that the rows are excluded, and that their absence is
declared — because either alone is a listing that lies.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.effects import EffectLevel

FLAG = "--include-simulated"
NOTE = "simulated rows withheld — pass --include-simulated to show them"


def listing_context(config, conn, audit):
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def add_card(conn, card_id, *, dry_run):
    with db.transaction(conn):
        db.insert_card(
            conn,
            board_id="board-1",
            card_id=card_id,
            card_url=f"https://trello.com/c/{card_id}",
            title="a card",
            body="",
            dry_run=dry_run,
        )


def add_worktree_item(conn, issue_number, *, dry_run, path="/tmp/wt"):
    item = seed_item(conn, issue_number=issue_number, dry_run=dry_run)
    with db.transaction(conn):
        db.update_work_item_columns(
            conn, item, worktree_path=f"{path}-{issue_number}", branch=f"b/{issue_number}"
        )
    return item


# -- cards ------------------------------------------------------------------


@pytest.fixture
def cards_ctx(board_config, conn, audit):
    return listing_context(board_config, conn, audit)


def test_cards_says_what_it_withheld_instead_of_claiming_nothing_is_tracked(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=True)
    add_card(conn, "card-2", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx).lines)

    assert "no cards tracked yet" not in text
    assert f"no cards visible (2 {NOTE})" in text


def test_cards_discloses_withheld_rows_beside_the_ones_it_shows(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=False)
    add_card(conn, "card-2", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx).lines)

    assert "card-1" in text
    assert "card-2" not in text, "the withheld row must actually be withheld"
    assert f"1 {NOTE}" in text


def test_cards_keeps_its_original_message_when_nothing_was_withheld(cards_ctx, conn):
    text = "\n".join(operations.cards(cards_ctx).lines)

    assert text.splitlines() == ["no cards tracked yet"]


def test_cards_discloses_nothing_when_simulated_rows_were_asked_for(cards_ctx, conn):
    add_card(conn, "card-1", dry_run=True)

    text = "\n".join(operations.cards(cards_ctx, include_simulated=True).lines)

    assert "withheld" not in text
    assert "card-1" in text


# -- worktree list ----------------------------------------------------------


@pytest.fixture
def wt_ctx(config, conn, audit):
    return listing_context(config, conn, audit)


def test_worktree_list_says_what_it_withheld_instead_of_claiming_nothing_recorded(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)
    add_worktree_item(conn, 27, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert "no worktrees recorded" not in text
    assert f"no worktrees visible (2 {NOTE})" in text


def test_worktree_list_discloses_withheld_rows_beside_the_ones_it_shows(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)
    add_worktree_item(conn, 31, dry_run=False)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert "/tmp/wt-31" in text
    # The issue #21 report listed `worktree list` as untested against a state that actually
    # had worktrees. It was already correct; this is the assertion that says so, and that
    # keeps it correct — the note alone would pass over a listing that showed both rows.
    assert "/tmp/wt-26" not in text, "the withheld row must actually be withheld"
    assert f"1 {NOTE}" in text


def test_worktree_list_keeps_its_original_message_when_nothing_was_withheld(wt_ctx, conn):
    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert text.splitlines() == ["no worktrees recorded"]


def test_a_simulated_item_with_no_worktree_was_never_withheld_from_this_listing(wt_ctx, conn):
    """It was never in the listing, so it cannot have been withheld from it — counting it
    would state a number that ``--include-simulated`` does not reveal here."""
    seed_item(conn, issue_number=26, dry_run=True)
    add_worktree_item(conn, 27, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx).lines)

    assert f"no worktrees visible (1 {NOTE})" in text


def test_worktree_list_discloses_nothing_when_simulated_rows_were_asked_for(wt_ctx, conn):
    add_worktree_item(conn, 26, dry_run=True)

    text = "\n".join(operations.worktree_list(wt_ctx, include_simulated=True).lines)

    assert "withheld" not in text
    assert "/tmp/wt-26" in text


# -- anomalies (issue #21) --------------------------------------------------


@pytest.fixture
def anomaly_ctx(config, conn, audit):
    return listing_context(config, conn, audit)


def add_anomaly(conn, entity_id, *, dry_run, kind="card_create_failing"):
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind=kind,
            entity_type="card",
            entity_id=entity_id,
            detail={"attempts": 3},
            dry_run=dry_run,
        )


def test_anomalies_says_what_it_withheld_instead_of_claiming_an_all_clear(anomaly_ctx, conn):
    """The reported defect, in the shape it was reported.

    Two `card_create_failing` anomalies, both belonging to dry-run cards, shown in the default
    view as outstanding real problems. Now they are withheld — and a bare "no outstanding
    anomalies" over two withheld rows would be the same lie in the other direction.
    """
    add_anomaly(conn, "card-1", dry_run=True)
    add_anomaly(conn, "card-2", dry_run=True)

    text = "\n".join(operations.anomalies(anomaly_ctx).lines)

    assert "card-1" not in text and "card-2" not in text
    assert f"no outstanding anomalies (2 {NOTE})" in text


def test_anomalies_discloses_withheld_rows_beside_the_ones_it_shows(anomaly_ctx, conn):
    add_anomaly(conn, "card-real", dry_run=False)
    add_anomaly(conn, "card-sim", dry_run=True)

    text = "\n".join(operations.anomalies(anomaly_ctx).lines)

    assert "card-real" in text
    assert "card-sim" not in text
    assert f"1 {NOTE}" in text


def test_anomalies_keeps_its_original_message_when_nothing_was_withheld(anomaly_ctx, conn):
    text = "\n".join(operations.anomalies(anomaly_ctx).lines)

    assert "no outstanding anomalies" in text
    assert "withheld" not in text


def test_anomalies_marks_the_rehearsed_rows_it_is_asked_to_show(anomaly_ctx, conn):
    """FR-057: shown means marked. An unmarked rehearsed row is the defect, revealed."""
    add_anomaly(conn, "card-real", dry_run=False)
    add_anomaly(conn, "card-sim", dry_run=True)

    text = "\n".join(operations.anomalies(anomaly_ctx, include_simulated=True).lines)

    assert "card-real" in text and "card-sim" in text
    assert "withheld" not in text
    assert "* = simulated (dry-run) row" in text
    marked = [line for line in text.splitlines() if line.startswith("[") and "]*" in line]
    assert len(marked) == 1, "exactly the rehearsed row carries the marker"


def test_anomalies_prints_no_legend_when_nothing_shown_is_rehearsed(anomaly_ctx, conn):
    add_anomaly(conn, "card-real", dry_run=False)

    text = "\n".join(operations.anomalies(anomaly_ctx, include_simulated=True).lines)

    assert "* = simulated" not in text


def test_a_window_that_matched_nothing_is_not_reported_as_a_withheld_scope(anomaly_ctx, conn):
    """Milestone 012's distinction has to survive a third empty case.

    "no anomalies detected in the last 1h" and "everything is being hidden from you" are
    different facts, and so is the pair of them together.
    """
    add_anomaly(conn, "card-sim", dry_run=True)

    text = "\n".join(operations.anomalies(anomaly_ctx, since="1h").lines)

    assert f"no anomalies detected in the last 1h (1 {NOTE})" in text


def test_the_withheld_count_is_scoped_to_the_window_the_listing_used(anomaly_ctx, conn):
    """The equality that makes the number safe: withheld must equal what the flag reveals.

    A count taken in SQL would ignore ``--since`` — which is applied in Python on purpose,
    because ``detected_at`` is TEXT and a malformed stamp compared lexicographically would be
    dropped silently (012 research R2). This is the case that would catch that mistake.
    """
    add_anomaly(conn, "card-old", dry_run=True)
    conn.execute(
        "UPDATE anomalies SET detected_at = '2020-01-01T00:00:00Z' WHERE entity_id = 'card-old'"
    )
    add_anomaly(conn, "card-new", dry_run=True)

    narrow = "\n".join(operations.anomalies(anomaly_ctx, since="1h").lines)
    wide = "\n".join(operations.anomalies(anomaly_ctx).lines)

    assert f"no anomalies detected in the last 1h (1 {NOTE})" in narrow, (
        "only the recent rehearsed row is inside the window"
    )
    assert f"no outstanding anomalies (2 {NOTE})" in wide


def test_the_withheld_count_widens_with_all_the_way_the_listing_does(anomaly_ctx, conn):
    add_anomaly(conn, "card-sim", dry_run=True)
    row = db.list_anomalies(conn, include_simulated=True)[0]
    with db.transaction(conn):
        db.acknowledge_anomaly(conn, row.id)

    default = "\n".join(operations.anomalies(anomaly_ctx).lines)
    everything = "\n".join(operations.anomalies(anomaly_ctx, show_all=True).lines)

    assert "withheld" not in default, "an acknowledged row is out of this listing's scope"
    assert f"no outstanding anomalies (1 {NOTE})" in everything


def test_an_anomaly_naming_no_entity_is_never_withheld(anomaly_ctx, conn):
    """FR-009: a fact about the machine is real whatever the effect level was.

    Withholding one would hide a real problem, which is the opposite of the point.
    """
    with db.transaction(conn):
        db.raise_anomaly(conn, kind="registry_version_unknown", detail={"versions": ["9.9"]})

    text = "\n".join(operations.anomalies(anomaly_ctx).lines)

    assert "registry_version_unknown" in text
    assert "withheld" not in text


def test_acknowledging_by_id_reaches_a_rehearsed_anomaly_without_the_flag(anomaly_ctx, conn):
    """An explicit id is already an explicit act — the rule ``db.get_work_item`` follows.

    Requiring the flag as well would make a rehearsed anomaly unacknowledgeable by the reader
    who just used the flag to find it.
    """
    add_anomaly(conn, "card-sim", dry_run=True)
    row = db.list_anomalies(conn, include_simulated=True)[0]

    result = operations.anomalies(anomaly_ctx, acknowledge=row.id)

    assert result.code == operations.EXIT_OK
    assert f"acknowledged anomaly {row.id}" in "\n".join(result.lines)
    assert db.list_anomalies(conn, include_simulated=True) == []


def test_the_payload_and_the_text_agree_about_the_scope(anomaly_ctx, conn):
    add_anomaly(conn, "card-real", dry_run=False)
    add_anomaly(conn, "card-sim", dry_run=True)

    result = operations.anomalies(anomaly_ctx)

    assert [a["entity_id"] for a in result.data["anomalies"]] == ["card-real"]
    assert result.data["withheld_simulated"] == 1
    assert result.data["anomalies"][0]["simulated"] is False


def test_the_payload_states_zero_rather_than_omitting_the_key(anomaly_ctx, conn):
    """A consumer must never have to tell "nothing withheld" from "this build does not say"."""
    add_anomaly(conn, "card-real", dry_run=False)

    result = operations.anomalies(anomaly_ctx)
    revealed = operations.anomalies(anomaly_ctx, include_simulated=True)

    assert result.data["withheld_simulated"] == 0
    assert revealed.data["withheld_simulated"] == 0


def test_the_payload_identifies_the_rehearsed_rows_it_does_show(anomaly_ctx, conn):
    add_anomaly(conn, "card-sim", dry_run=True)

    result = operations.anomalies(anomaly_ctx, include_simulated=True)

    assert result.data["anomalies"][0]["simulated"] is True
