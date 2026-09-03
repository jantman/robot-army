"""The two surfaces for holds: the terminal verbs and the web routes (issue #117).

FR-007 requires the same effect from either, and the correspondence is checked by
enumeration in ``test_web_routing`` and ``test_cli_exit_codes`` rather than asserted here.
What this file covers is what each surface *says* and *refuses*, and the one rendering
decision that can quietly fail: a repository hold matching no queued item, which has no row
to attach to and would otherwise suppress every future item in that repository while the
page looked entirely normal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import config_dict, make_boundaries, monkey_token, seed_item
from tests.unit.test_cli_exit_codes import _to_toml, run_cli

from robot_army import db, operations
from robot_army.operations import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from robot_army.states import WorkItemState


@pytest.fixture
def config_file(tmp_path, repo_clone, layout) -> Path:
    monkey_token()
    path = tmp_path / "config.toml"
    path.write_text(
        _to_toml(config_dict(repo_clone, layout, tmp_path / "worktrees")), encoding="utf-8"
    )
    return path


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


# -- the argument contract (research R6) ------------------------------------


@pytest.mark.parametrize("verb", ["hold", "unhold"])
def test_naming_both_targets_is_a_usage_error(config_file, verb):
    """The target is *stated*, never inferred from its shape. A mutually exclusive group
    refuses this before anything is read, so the message is argparse's own."""
    with pytest.raises(SystemExit) as caught:
        run_cli([verb, "5", "--repo", "owner/name"], config_file)
    assert caught.value.code == EXIT_USAGE


@pytest.mark.parametrize("verb", ["hold", "unhold"])
def test_naming_no_target_is_a_usage_error(config_file, verb):
    with pytest.raises(SystemExit) as caught:
        run_cli([verb], config_file)
    assert caught.value.code == EXIT_USAGE


def test_a_non_integer_item_id_is_a_usage_error(config_file):
    """``owner/name`` in the positional slot must not be silently reinterpreted as a
    repository — which is the whole reason the two are separate arguments."""
    with pytest.raises(SystemExit) as caught:
        run_cli(["hold", "owner/name"], config_file)
    assert caught.value.code == EXIT_USAGE


# -- the verbs --------------------------------------------------------------


def test_holding_and_releasing_an_item_from_the_terminal(config_file, conn, capsys):
    item_id = seed_item(conn, state=str(WorkItemState.READY))

    assert run_cli(["hold", str(item_id)], config_file) == EXIT_OK
    assert "held" in capsys.readouterr().out
    assert set(db.list_item_holds(conn)) == {item_id}

    assert run_cli(["unhold", str(item_id)], config_file) == EXIT_OK
    assert db.list_item_holds(conn) == {}


def test_holding_and_releasing_a_repository_from_the_terminal(config_file, conn, capsys):
    seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))

    assert run_cli(["hold", "--repo", "demo"], config_file) == EXIT_OK
    assert set(db.list_repo_holds(conn)) == {"demo"}

    assert run_cli(["unhold", "--repo", "demo"], config_file) == EXIT_OK
    assert db.list_repo_holds(conn) == {}


def test_an_unknown_target_exits_one_and_names_what_was_not_found(config_file, conn, capsys):
    """Named, not merely refused — and on stderr, where every other failure goes."""
    seed_item(conn)
    assert run_cli(["hold", "999999"], config_file) == EXIT_FAILED
    assert "999999" in capsys.readouterr().err

    assert run_cli(["hold", "--repo", "owner/typo"], config_file) == EXIT_FAILED
    assert "owner/typo" in capsys.readouterr().err


def test_a_redundant_release_exits_zero(config_file, conn):
    """FR-005 from the terminal: releasing what is not held is a no-op, not a failure."""
    item_id = seed_item(conn)
    assert run_cli(["unhold", str(item_id)], config_file) == EXIT_OK


# -- the listing ------------------------------------------------------------


def test_holds_says_so_in_words_when_nothing_is_held(config_file, capsys):
    """US3 AS3. An empty table is not an answer — it is the same output a broken query
    would produce."""
    assert run_cli(["holds"], config_file) == EXIT_OK
    assert "nothing is held" in capsys.readouterr().out


def test_holds_lists_both_scopes_with_provenance(config_file, conn, capsys):
    item_id = seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))
    run_cli(["hold", str(item_id)], config_file)
    run_cli(["hold", "--repo", "demo"], config_file)
    capsys.readouterr()

    assert run_cli(["holds"], config_file) == EXIT_OK
    out = capsys.readouterr().out
    assert "held items (1)" in out
    assert "held repositories (1)" in out
    assert str(item_id) in out
    assert "demo" in out
    assert "cli" in out, "which surface placed it"


def test_holds_shows_a_repository_hold_that_is_holding_nothing(config_file, conn, capsys):
    """The failure mode this verb exists for. A hold with no queued item has nothing to
    attach to in any item-oriented view, and would otherwise be diagnosed as broken
    polling."""
    seed_item(conn, repo_key="demo", state=str(WorkItemState.DONE))
    run_cli(["hold", "--repo", "demo"], config_file)
    capsys.readouterr()

    run_cli(["holds"], config_file)
    out = capsys.readouterr().out

    assert "held repositories (1)" in out
    assert "queued" in out


def test_holds_shows_the_state_of_a_held_item_that_has_finished(config_file, conn, capsys):
    """Nothing sweeps a hold when its item finishes — clearing on a state transition is
    expiry under another name. Showing the state is what keeps it visible rather than
    mysterious."""
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    run_cli(["hold", str(item_id)], config_file)
    conn.execute(
        "UPDATE work_items SET state = ? WHERE id = ?", (str(WorkItemState.DONE), item_id)
    )
    conn.commit()
    capsys.readouterr()

    run_cli(["holds"], config_file)
    assert "done" in capsys.readouterr().out


def test_holds_renders_times_in_the_hosts_zone(config_file, conn, capsys, monkeypatch):
    """Milestone 010's rule for every terminal display site. A stored UTC stamp must never
    reach the author verbatim."""
    from robot_army import timefmt

    monkeypatch.setattr(timefmt, "local", lambda stamp: "LOCALISED" if stamp else None)
    item_id = seed_item(conn)
    run_cli(["hold", str(item_id)], config_file)
    capsys.readouterr()

    run_cli(["holds"], config_file)
    assert "LOCALISED" in capsys.readouterr().out


# -- the age summary --------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "-"), (5, "5s"), (300, "5m"), (7200, "2h"), (259200, "3d")],
)
def test_a_holds_age_is_reported_coarsely(seconds, expected):
    """FR-020's question is "did I set this and forget it". ``3d`` answers it; ``267413s``
    makes the reader do arithmetic first."""
    assert operations._held_for(seconds) == expected


# -- the web routes ---------------------------------------------------------


def test_holding_and_releasing_an_item_from_the_web(web, conn):
    item_id = seed_item(conn, state=str(WorkItemState.READY))

    response = web.post(f"/item/{item_id}/hold")
    assert response.status == 303
    assert set(db.list_item_holds(conn)) == {item_id}

    assert web.post(f"/item/{item_id}/unhold").status == 303
    assert db.list_item_holds(conn) == {}


def test_holding_and_releasing_a_repository_from_the_web(web, conn):
    seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))

    assert web.post("/repos/hold", form={"repo": "demo"}).status == 303
    assert set(db.list_repo_holds(conn)) == {"demo"}

    assert web.post("/repos/unhold", form={"repo": "demo"}).status == 303
    assert db.list_repo_holds(conn) == {}


def test_the_web_records_which_surface_placed_the_hold(web, conn):
    """``held_by`` is the provenance ``dispatch_control.paused_by`` already keeps: which
    front end did this, never which person."""
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    web.post(f"/item/{item_id}/hold")
    assert db.list_item_holds(conn)[item_id].held_by == "web"


def test_holding_an_unknown_item_from_the_web_is_a_404(web, conn):
    seed_item(conn)
    assert web.post("/item/999999/hold").status == 404


def test_holding_an_unknown_repository_from_the_web_is_a_404(web, conn):
    """The key is validated against the onboarding record before it reaches anything, so
    an unknown one is a refusal rather than a stored row."""
    seed_item(conn, repo_key="demo")
    assert web.post("/repos/hold", form={"repo": "owner/typo"}).status == 404
    assert web.post("/repos/hold", form={}).status == 404
    assert db.list_repo_holds(conn) == {}


def test_a_hold_from_the_web_is_in_force_for_the_terminal_immediately(web, conn, config_file):
    """FR-007: the same effect from either surface, with no restart and no cache."""
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    web.post(f"/item/{item_id}/hold")

    assert run_cli(["holds"], config_file) == EXIT_OK
    assert set(db.list_item_holds(conn)) == {item_id}


def test_the_redirect_carries_include_simulated_forward(web, conn):
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    response = web.post(f"/item/{item_id}/hold", form={"include_simulated": "1"})
    assert "include_simulated=1" in response.headers["Location"]


def test_every_hold_route_is_recorded_before_it_acts(web, conn, layout):
    """``_perform`` writes the intent record and flushes it before anything else runs, so a
    refusal, a crash and a success all leave a record."""
    from tests.unit.test_web_actions import web_records

    item_id = seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))
    web.post(f"/item/{item_id}/hold")
    web.post("/repos/hold", form={"repo": "demo"})

    assert web_records(layout, action="web.hold.item")
    assert web_records(layout, action="web.hold.repo")


def test_onboarding_is_still_terminal_only(web, conn):
    """``/repos/hold`` is two path segments and ``/repos/demo/onboard`` is three, so adding
    the former must not have opened the latter. Asserted here as well as in
    ``test_web_routing`` because this feature is the one that introduced ``/repos``."""
    seed_item(conn, repo_key="demo")
    assert web.post("/repos/demo/onboard").status == 404
    assert web.post("/repos/onboard").status == 404


# -- rendering --------------------------------------------------------------


def test_a_held_row_shows_its_reason_and_offers_release(web, conn):
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    web.post(f"/item/{item_id}/hold")

    rendered = web.get("/queue").text

    assert "held since" in rendered
    assert f"/item/{item_id}/unhold" in rendered
    assert f"/item/{item_id}/hold\"" not in rendered, "one control, not both"


def test_an_unheld_row_offers_hold(web, conn):
    item_id = seed_item(conn, state=str(WorkItemState.READY))
    assert f"/item/{item_id}/hold" in web.get("/queue").text


def test_a_held_row_is_never_omitted_or_moved(web, conn):
    """FR-014. A surface that silently omits held work is the failure the queue view
    exists to prevent."""
    first = seed_item(conn, issue_number=1, state=str(WorkItemState.READY))
    second = seed_item(conn, issue_number=2, state=str(WorkItemState.READY))
    web.post(f"/item/{first}/hold")

    rendered = web.get("/queue").text
    assert f"/item/{first}" in rendered
    assert f"/item/{second}" in rendered
    assert rendered.index(f"/item/{first}") < rendered.index(f"/item/{second}")


def test_the_queue_page_shows_a_held_repository_that_is_holding_nothing(web, conn):
    """FR-019, and the one place this feature can quietly fail. Without this notice a
    repository hold matching no queued item would suppress every future item in it while
    the page looked entirely normal."""
    seed_item(conn, repo_key="demo", state=str(WorkItemState.DONE))
    web.post("/repos/hold", form={"repo": "demo"})

    rendered = web.get("/queue").text

    assert "held repositories (1)" in rendered
    assert "/repos/unhold" in rendered


def test_the_held_repository_notice_counts_what_it_is_holding(web, conn):
    """"Holding nothing" and "holding three items" are very different facts."""
    for n in range(1, 4):
        seed_item(conn, repo_key="demo", issue_number=n, state=str(WorkItemState.READY))
    web.post("/repos/hold", form={"repo": "demo"})

    rendered = web.get("/queue").text
    assert "held repositories (1)" in rendered


def test_no_held_repository_notice_when_none_is_held(web, conn):
    seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))
    assert "held repositories" not in web.get("/queue").text


# -- the status summary line ------------------------------------------------


def test_status_says_nothing_about_holds_when_none_is_held(config_file, conn, capsys):
    """The common run must gain no noise. A permanent "no holds in force" line would cost
    exactly the discoverability it exists to buy."""
    seed_item(conn, state=str(WorkItemState.READY))
    run_cli(["status"], config_file)
    assert "holds" not in capsys.readouterr().out


def test_status_names_how_much_is_held_when_something_is(config_file, conn, capsys):
    """US3's discoverability: the author's habitual command mentions holds without their
    having to remember that holds exist."""
    item_id = seed_item(conn, repo_key="demo", state=str(WorkItemState.READY))
    run_cli(["hold", str(item_id)], config_file)
    run_cli(["hold", "--repo", "demo"], config_file)
    capsys.readouterr()

    run_cli(["status"], config_file)
    out = capsys.readouterr().out

    assert "holds        : 1 item and 1 repository held" in out
    assert "`robot-army holds` lists them" in out


def test_the_status_summary_pluralises_and_omits_the_empty_half(config_file, conn, capsys):
    for n in (1, 2):
        item = seed_item(conn, issue_number=n, state=str(WorkItemState.READY))
        run_cli(["hold", str(item)], config_file)
    capsys.readouterr()

    run_cli(["status"], config_file)
    out = capsys.readouterr().out

    assert "2 items held" in out
    assert "repositor" not in out.split("holds        :")[1].split("\n")[0]


def test_a_repository_hold_holding_nothing_still_reaches_status(config_file, conn, capsys):
    """The line is driven by the holds themselves, not by what is in the queue — otherwise
    the very hold most likely to be forgotten would be the one it stayed silent about."""
    seed_item(conn, repo_key="demo", state=str(WorkItemState.DONE))
    run_cli(["hold", "--repo", "demo"], config_file)
    capsys.readouterr()

    run_cli(["status"], config_file)
    assert "1 repository held" in capsys.readouterr().out

