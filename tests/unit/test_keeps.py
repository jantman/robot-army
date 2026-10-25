"""Keeping a session open after its issue closes: storage, operations and the terminal.

A keep is a separate statement from a hold and from a pause (FR-012). A hold governs entry
into dispatch; a keep governs the *end* of a session, and nothing in this file should ever
need to read a hold to pass.

The tests that carry the design rather than merely covering it:

* :func:`test_purging_simulated_rows_takes_their_keeps_with_them` proves the cascade. A keep
  must never outlive its item or reattach to a recycled id.
* :func:`test_list_kept_sessions_lists_only_the_open_latest_row_of_done_kept_items` pins
  what a "kept session" is for capacity: the open row of a ``done``, kept item's latest
  attempt. Each of the four exclusions is a different way to mis-name the machine as full.
"""

from __future__ import annotations

import json

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.models import Keep
from robot_army.states import WorkItemState


@pytest.fixture
def ctx(config, conn, monkeypatch):
    monkeypatch.setattr(
        operations, "wire", lambda level, cfg, log, conn: make_boundaries(log, level=level)
    )
    built = operations.build_context(config)
    yield built
    built.close()


def records(layout, action: str, *, kind: str = "outcome") -> list[dict]:
    """Audit records for one action, the ``outcome`` half by default (see test_holds)."""
    return [
        entry
        for path in sorted(layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if (entry := json.loads(line))["action"] == action and entry["kind"] in (kind, "event")
    ]


def open_session(
    conn, item_id: int, session_id: str, *, attempt: int = 1, state: str = "running"
) -> None:
    """One session row under ``item_id``, in ``state``."""
    with db.transaction(conn):
        db.insert_session(
            conn,
            work_item_id=item_id,
            session_id=session_id,
            attempt=attempt,
            dry_run=False,
            launch_argv=["true"],
        )
        conn.execute("UPDATE sessions SET state = ? WHERE session_id = ?", (state, session_id))


# -- the accessors ----------------------------------------------------------


def test_an_empty_table_lists_as_an_empty_dict(conn):
    assert db.list_item_keeps(conn) == {}


def test_placing_a_keep_returns_it_and_says_it_was_placed(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        keep, placed = db.set_item_keep(conn, item_id, by="cli")
    assert placed is True
    assert isinstance(keep, Keep)
    assert keep.kept_by == "cli"
    assert keep.kept_at
    assert db.list_item_keeps(conn) == {item_id: keep}
    assert db.is_kept(conn, item_id) is True


def test_keeping_something_already_kept_keeps_the_original_time_and_surface(conn):
    """A refreshed timestamp would silently rewrite when the decision was made."""
    item_id = seed_item(conn)
    with db.transaction(conn):
        first, _ = db.set_item_keep(conn, item_id, by="cli")
    conn.execute("UPDATE item_keeps SET kept_at = '2026-01-01T00:00:00Z'")
    with db.transaction(conn):
        second, placed = db.set_item_keep(conn, item_id, by="web")
    assert placed is False
    assert second == Keep(kept_at="2026-01-01T00:00:00Z", kept_by="cli")
    assert first.kept_by == "cli"
    assert len(db.list_item_keeps(conn)) == 1


def test_clearing_returns_what_it_removed_then_none(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        placed, _ = db.set_item_keep(conn, item_id, by="cli")
    with db.transaction(conn):
        assert db.clear_item_keep(conn, item_id) == placed
    with db.transaction(conn):
        assert db.clear_item_keep(conn, item_id) is None
    assert db.is_kept(conn, item_id) is False


def test_a_rolled_back_keep_leaves_no_trace(conn):
    item_id = seed_item(conn)
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.set_item_keep(conn, item_id, by="cli")
        raise RuntimeError("killed mid-change")
    assert db.list_item_keeps(conn) == {}


def test_a_rolled_back_release_leaves_the_keep_intact(conn):
    item_id = seed_item(conn)
    with db.transaction(conn):
        placed, _ = db.set_item_keep(conn, item_id, by="cli")
    with pytest.raises(RuntimeError), db.transaction(conn):
        db.clear_item_keep(conn, item_id)
        raise RuntimeError("killed mid-change")
    assert db.list_item_keeps(conn) == {item_id: placed}


def test_keeps_survive_a_reopened_database_with_identical_timestamps(layout):
    """FR-009: a keep is durable, and its clock does not reset on restart."""
    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn)
    with db.transaction(conn):
        keep, _ = db.set_item_keep(conn, item_id, by="cli")
    conn.close()

    reopened, _ = db.open_database(layout.db_path)
    assert db.list_item_keeps(reopened) == {item_id: keep}
    reopened.close()


def test_purging_simulated_rows_takes_their_keeps_with_them(conn):
    live = seed_item(conn, issue_number=1, dry_run=False)
    simulated = seed_item(conn, issue_number=2, dry_run=True)
    with db.transaction(conn):
        db.set_item_keep(conn, live, by="cli")
        db.set_item_keep(conn, simulated, by="cli")

    with db.transaction(conn):
        db.purge_simulated(conn)

    assert set(db.list_item_keeps(conn)) == {live}
    orphans = conn.execute(
        "SELECT COUNT(*) FROM item_keeps k "
        "LEFT JOIN work_items i ON i.id = k.work_item_id WHERE i.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_list_kept_sessions_lists_only_the_open_latest_row_of_done_kept_items(conn):
    done_kept = seed_item(conn, repo_key="demo", issue_number=1, state="done")
    done_kept_other_repo = seed_item(conn, repo_key="other", issue_number=2, state="done")
    active_kept = seed_item(conn, repo_key="demo", issue_number=3, state="active")
    done_unkept = seed_item(conn, repo_key="demo", issue_number=4, state="done")
    done_kept_exited = seed_item(conn, repo_key="demo", issue_number=5, state="done")
    done_kept_resumed = seed_item(conn, repo_key="demo", issue_number=6, state="done")

    open_session(conn, done_kept, "s-kept")
    open_session(conn, done_kept_other_repo, "s-other", state="starting")
    open_session(conn, active_kept, "s-active")
    open_session(conn, done_unkept, "s-unkept")
    open_session(conn, done_kept_exited, "s-exited", state="exited_clean")
    # Resumed while attempt 1's worker was still alive: two open rows, and only the
    # current attempt is the session the keep holds.
    open_session(conn, done_kept_resumed, "s-superseded", attempt=1)
    open_session(conn, done_kept_resumed, "s-current", attempt=2)
    with db.transaction(conn):
        for item_id in (
            done_kept,
            done_kept_other_repo,
            active_kept,
            done_kept_exited,
            done_kept_resumed,
        ):
            db.set_item_keep(conn, item_id, by="cli")

    assert sorted(db.list_kept_sessions(conn)) == [
        ("demo", "s-current"),
        ("demo", "s-kept"),
        ("other", "s-other"),
    ]


def test_list_kept_sessions_is_empty_when_nothing_is_kept(conn):
    item_id = seed_item(conn, state="done")
    open_session(conn, item_id, "s-1")
    assert db.list_kept_sessions(conn) == []


# -- the operations (US1) ---------------------------------------------------


@pytest.mark.parametrize("state", ["ready", "active", "failed"])
def test_keep_is_accepted_before_the_issue_closes_and_says_when_it_takes_effect(
    ctx, conn, state
):
    """Pre-dispatch, running and retried items all get a session later, so a keep on them
    is meaningful and waits for ``done`` (research R8)."""
    item_id = seed_item(conn, state=state)
    result = operations.keep_item(ctx, item_id)

    assert result.code == operations.EXIT_OK
    assert db.list_item_keeps(ctx.conn)[item_id].kept_by == "cli"
    text = "\n".join(result.lines)
    assert f"work item {item_id} (#42 demo) kept at " in result.lines[0]
    assert result.lines[0].endswith(" by cli")
    assert (
        "The keep takes effect when the issue closes: the session stays open instead of "
        "being retired." in text
    )
    assert f"robot-army unkeep {item_id}" in text


def test_keep_on_a_done_item_with_a_live_session_says_what_it_withholds(ctx, conn):
    item_id = seed_item(conn, state="done")
    open_session(conn, item_id, "s-live")
    result = operations.keep_item(ctx, item_id)

    assert result.code == operations.EXIT_OK
    assert db.is_kept(ctx.conn, item_id)
    text = "\n".join(result.lines)
    assert (
        "Its session will not be retired, its tab not closed and its checkout not reclaimed "
        "until released. It holds a session slot meanwhile." in text
    )
    assert "takes effect when the issue closes" not in text
    assert result.data == {
        "item_id": item_id,
        "kept": True,
        "kept_at": db.list_item_keeps(ctx.conn)[item_id].kept_at,
        "kept_by": "cli",
        "redundant": False,
        "state": "done",
        "live_session": True,
    }


def test_a_redundant_keep_is_a_reported_no_op_quoting_the_original_time(ctx, conn):
    item_id = seed_item(conn, state="active")
    first = operations.keep_item(ctx, item_id, by="cli")
    second = operations.keep_item(ctx, item_id, by="web")

    assert second.code == operations.EXIT_OK
    assert second.data["redundant"] is True
    assert second.data["kept_at"] == first.data["kept_at"]
    assert second.data["kept_by"] == "cli"
    assert "was already kept, since " in second.lines[0]
    assert second.lines[0].endswith(" by cli")
    assert len(db.list_item_keeps(ctx.conn)) == 1


def test_keeping_an_unknown_item_is_refused(ctx):
    result = operations.keep_item(ctx, 999999)
    assert result.code == operations.EXIT_FAILED
    assert "no work item with id 999999" in "\n".join(result.lines)


def test_releasing_an_unknown_item_is_refused(ctx):
    assert operations.unkeep_item(ctx, 999999).code == operations.EXIT_FAILED


def test_releasing_what_is_not_kept_is_a_no_op_not_a_failure(ctx, conn):
    item_id = seed_item(conn)
    result = operations.unkeep_item(ctx, item_id)
    assert result.code == operations.EXIT_OK
    assert result.lines == [f"work item {item_id} was not kept; nothing to release"]
    assert result.data == {
        "item_id": item_id,
        "kept": False,
        "released": False,
        "open_sessions": [],
    }


def test_releasing_a_kept_done_item_with_an_open_session_names_what_happens_next(
    ctx, conn, layout
):
    """FR-008: the release records which sessions were open at that moment, so a later
    ``session.retire`` for one of them can be joined back to the release from the log."""
    item_id = seed_item(conn, state="done")
    open_session(conn, item_id, "s-old", attempt=1, state="exited_clean")
    open_session(conn, item_id, "s-live", attempt=2)
    operations.keep_item(ctx, item_id)

    result = operations.unkeep_item(ctx, item_id)

    assert result.code == operations.EXIT_OK
    assert result.lines[0].startswith(f"work item {item_id} released; it was kept since ")
    assert result.lines[0].endswith(" by cli")
    assert result.lines[1] == (
        "Its session is retired on the next reconciliation pass that finds it idle."
    )
    assert result.data["open_sessions"] == ["s-live"]
    assert result.data["released"] is True
    assert not db.is_kept(ctx.conn, item_id)
    ctx.audit.close()

    released = records(layout, "unkeep.item")
    assert released[0]["detail"]["open_sessions"] == ["s-live"]
    assert released[0]["detail"]["released"] is True
    assert released[0]["detail"]["was_kept_at"]


def test_releasing_a_kept_item_that_is_not_done_omits_the_retirement_line(ctx, conn):
    item_id = seed_item(conn, state="active")
    open_session(conn, item_id, "s-live")
    operations.keep_item(ctx, item_id)
    result = operations.unkeep_item(ctx, item_id)
    assert len(result.lines) == 1
    assert result.data["open_sessions"] == ["s-live"]


def test_keep_and_release_are_written_to_the_audit_log(ctx, conn, layout):
    item_id = seed_item(conn, state="done")
    open_session(conn, item_id, "s-live")
    operations.keep_item(ctx, item_id, by="cli")
    operations.keep_item(ctx, item_id, by="web")
    ctx.audit.close()

    intents = records(layout, "keep.item", kind="intent")
    assert intents[0]["detail"] == {
        "by": "cli",
        "was_kept": False,
        "state": "done",
        "live_session": True,
    }
    assert intents[1]["detail"]["was_kept"] is True
    assert intents[0]["entity_type"] == "work_item"
    assert intents[0]["entity_id"] == item_id

    outcomes = records(layout, "keep.item")
    assert outcomes[0]["outcome"] == "ok"
    assert outcomes[0]["detail"]["placed"] is True
    assert outcomes[0]["detail"]["kept_by"] == "cli"
    assert outcomes[0]["detail"]["kept_at"]
    assert outcomes[1]["detail"]["placed"] is False, "the redundant attempt is recorded"


def test_a_keep_interrupted_inside_its_transaction_leaves_no_row_and_an_error_record(
    ctx, conn, layout, monkeypatch
):
    """(F) Interruption path. The intent record is written before the change, so a failure
    leaves the record that says one was attempted, and the transaction leaves no row."""
    item_id = seed_item(conn, state="active")
    real = db.set_item_keep

    def _explode(c, i, *, by):
        real(c, i, by=by)
        raise RuntimeError("killed mid-change")

    monkeypatch.setattr(db, "set_item_keep", _explode)
    with pytest.raises(RuntimeError):
        operations.keep_item(ctx, item_id)
    monkeypatch.undo()

    assert db.list_item_keeps(ctx.conn) == {}
    ctx.audit.close()
    assert records(layout, "keep.item")[0]["outcome"] == "error"


# A keep written while no daemon runs is honoured by the next ``reconcile`` call. That is the
# first case of ``test_session_retirement.py``'s keep tests, which places the keep directly
# against the database and only then runs a pass: nothing caches keeps, so there is no
# in-memory copy for a restart to invalidate.


def test_keep_and_unkeep_json_carry_the_contract_shapes(ctx, conn):
    item_id = seed_item(conn, state="ready")
    kept = operations.keep_item(ctx, item_id)
    assert set(kept.data) == {
        "item_id",
        "kept",
        "kept_at",
        "kept_by",
        "redundant",
        "state",
        "live_session",
    }
    assert kept.data["live_session"] is False
    assert json.loads(kept.render(as_json=True))["kept"] is True

    released = operations.unkeep_item(ctx, item_id)
    assert set(released.data) == {"item_id", "kept", "released", "open_sessions"}
    assert json.loads(released.render(as_json=True))["released"] is True


# -- the terminal -----------------------------------------------------------


@pytest.fixture
def config_file(tmp_path, repo_clone, layout):
    from tests.conftest import config_dict, monkey_token
    from tests.unit.test_cli_exit_codes import _to_toml

    monkey_token()
    path = tmp_path / "config.toml"
    path.write_text(
        _to_toml(config_dict(repo_clone, layout, tmp_path / "worktrees")), encoding="utf-8"
    )
    return path


def run_cli(args: list[str], config_file) -> int:
    from tests.unit.test_cli_exit_codes import run_cli as _run

    return _run(args, config_file)


def test_keep_and_unkeep_from_the_terminal(config_file, conn, capsys):
    item_id = seed_item(conn, state="active")

    assert run_cli(["keep", str(item_id)], config_file) == operations.EXIT_OK
    assert "kept at" in capsys.readouterr().out
    assert db.list_item_keeps(conn)[item_id].kept_by == "cli"

    assert run_cli(["unkeep", str(item_id)], config_file) == operations.EXIT_OK
    assert "released" in capsys.readouterr().out
    assert db.list_item_keeps(conn) == {}


def test_keep_on_an_unknown_item_exits_one_on_stderr(config_file, conn, capsys):
    seed_item(conn)
    assert run_cli(["keep", "999999"], config_file) == operations.EXIT_FAILED
    assert "no work item with id 999999" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["keep", "unkeep"])
def test_keep_verbs_take_json(config_file, conn, capsys, verb):
    item_id = seed_item(conn, state="active")
    assert run_cli([verb, str(item_id), "--json"], config_file) == operations.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["item_id"] == item_id
    assert data["kept"] is (verb == "keep")


def test_keep_verbs_take_no_repository_flag(config_file):
    """Item-only: unlike ``hold`` there is no repository-scoped keep (clarification 1a)."""
    with pytest.raises(SystemExit) as caught:
        run_cli(["keep", "--repo", "demo"], config_file)
    assert caught.value.code == operations.EXIT_USAGE


def test_a_keep_does_not_change_the_item_or_its_session(ctx, conn):
    """A keep starts, stops and signals nothing; it only withholds an automatic ending."""
    item_id = seed_item(conn, state=str(WorkItemState.DONE))
    open_session(conn, item_id, "s-live")
    before = db.latest_session_for_item(ctx.conn, item_id)

    operations.keep_item(ctx, item_id)

    assert db.get_work_item(ctx.conn, item_id).state is WorkItemState.DONE
    assert db.latest_session_for_item(ctx.conn, item_id) == before


# -- the listing (US2) ------------------------------------------------------


def test_keeps_says_so_in_words_when_nothing_is_kept(ctx):
    """An empty table is the same output a broken query would produce."""
    result = operations.list_keeps(ctx)
    assert result.code == operations.EXIT_OK
    assert result.lines == ["nothing is kept"]
    assert result.data == {"keeps": [], "count": 0}


def test_keeps_lists_live_pending_and_inert_keeps_with_their_session(ctx, conn):
    """Every keep in force, including inert ones: nothing sweeps a keep (research R7), so
    showing the item's state and that no session is left is what keeps it from being
    mysterious."""
    live = seed_item(conn, state="done", issue_number=1)
    pending = seed_item(conn, state="ready", issue_number=2)
    inert = seed_item(conn, state="done", issue_number=3)
    open_session(conn, live, "s-live")
    open_session(conn, inert, "s-ended", state="exited_clean")
    with db.transaction(conn):
        for item_id in (live, pending, inert):
            db.set_item_keep(conn, item_id, by="cli")
    with db.transaction(conn):
        db.clear_item_keep(conn, inert)
        db.set_item_keep(conn, inert, by="web")

    result = operations.list_keeps(ctx)

    assert result.code == operations.EXIT_OK
    header = next(line for line in result.lines if "item" in line and "session" in line)
    for column in ("item", "repo", "issue", "state", "session", "kept", "by"):
        assert column in header.split()
    text = "\n".join(result.lines)
    assert "live s-live" in text
    assert "none yet" in text
    assert "no live session" in text
    assert "web" in text
    assert "robot-army unkeep" in text

    rows = {row["item_id"]: row for row in result.data["keeps"]}
    assert result.data["count"] == 3
    assert rows[live]["open_session_id"] == "s-live"
    assert rows[pending]["open_session_id"] is None
    assert rows[inert]["open_session_id"] is None
    assert rows[inert]["kept_by"] == "web"
    assert set(rows[live]) == {
        "item_id",
        "repo_key",
        "issue_number",
        "state",
        "kept_at",
        "kept_by",
        "age_seconds",
        "open_session_id",
    }
    assert rows[live]["state"] == "done"
    assert rows[live]["repo_key"] == "demo"
    assert rows[live]["issue_number"] == 1
    assert isinstance(rows[live]["age_seconds"], int)


def test_keeps_from_the_terminal_takes_json(config_file, conn, capsys):
    item_id = seed_item(conn, state="active")
    run_cli(["keep", str(item_id)], config_file)
    capsys.readouterr()

    assert run_cli(["keeps", "--json"], config_file) == operations.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [row["item_id"] for row in data["keeps"]] == [item_id]

    assert run_cli(["keeps"], config_file) == operations.EXIT_OK
    assert "none yet" in capsys.readouterr().out


# -- refusing a keep with nothing to keep (US3, FR-010) ---------------------


@pytest.mark.parametrize("state", ["done"])
def test_a_finished_item_whose_session_ended_is_refused_with_the_resume_route(
    ctx, conn, layout, state
):
    """No session exists to keep and none can start, so a keep would be a promise nothing
    can honour. The transcript survives, so the refusal says how to get back to it — with
    the **latest** session, which is the one the author was last in."""
    item_id = seed_item(conn, state=state)
    open_session(conn, item_id, "s-first", attempt=1, state="exited_clean")
    open_session(conn, item_id, "s-latest", attempt=2, state="lost")

    result = operations.keep_item(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert result.lines == [
        f"work item {item_id} is {state} and has no running session; there is nothing to "
        "keep.",
        "Its transcript survives: claude --resume s-latest",
    ]
    assert db.list_item_keeps(ctx.conn) == {}
    ctx.audit.close()
    refused = records(layout, "keep.item")
    assert refused[0]["outcome"] == "error"
    assert "nothing to keep" in refused[0]["detail"]["reason"]


@pytest.mark.parametrize("live", [False, True])
def test_an_abandoned_item_is_refused_whatever_its_sessions(ctx, conn, layout, live):
    """``abandoned`` has no way back to ``done``, so a keep could never take effect; it is
    refused even with a worker still running, and the refusal names ``cancel`` rather than
    the resume route, because the session is not gone."""
    item_id = seed_item(conn, state="abandoned")
    open_session(conn, item_id, "s-1", attempt=1, state="running" if live else "lost")

    result = operations.keep_item(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert result.lines[0] == (
        f"work item {item_id} is abandoned and can never reach done, so a keep would never "
        "take effect. An abandoned item's session is already left running; "
        f"`robot-army cancel {item_id}` ends it."
    )
    assert ("Its transcript survives: claude --resume s-1" in result.lines) is not live
    assert db.list_item_keeps(ctx.conn) == {}
    ctx.audit.close()
    assert records(layout, "keep.item")[0]["outcome"] == "error"


def test_a_finished_item_that_never_had_a_session_is_refused_without_a_resume_line(
    ctx, conn
):
    item_id = seed_item(conn, state="done")
    result = operations.keep_item(ctx, item_id)
    assert result.code == operations.EXIT_PRECONDITION
    assert result.lines == [
        f"work item {item_id} is done and has no running session; there is nothing to keep."
    ]
    assert db.list_item_keeps(ctx.conn) == {}


def test_a_refused_keep_exits_three_from_the_terminal(config_file, conn, capsys):
    item_id = seed_item(conn, state="done")
    assert run_cli(["keep", str(item_id)], config_file) == operations.EXIT_PRECONDITION
    assert "nothing to keep" in capsys.readouterr().err


def test_a_keep_placed_after_the_merge_on_a_live_session_is_honoured(
    ctx, conn, config, layout, tmp_path
):
    """US3 scenario 1: the author decides after the fact, while the worker still runs. The
    keep is accepted and the next retirement pass leaves the session alone."""
    from pathlib import Path

    from tests.conftest import seed_session, write_proc, write_registry
    from tests.unit.test_session_retirement import KillingHost

    from robot_army import reconcile, sessions

    registry = tmp_path / "registry"
    registry.mkdir()
    proc = tmp_path / "proc"
    write_proc(proc, 1, starttime="1", exe="/usr/lib/systemd/systemd")
    item_id = seed_item(conn, state="done")
    seed_session(conn, item_id, state="running", session_id="s-live", pid=700, proc_start="700")
    cwd = Path(config.worktree_root) / "issue-700"
    cwd.mkdir(parents=True, exist_ok=True)
    write_registry(
        registry,
        pid=700,
        session_id="s-live",
        proc_start="700",
        cwd=str(cwd),
        status="idle",
        status_updated_at=0,
    )
    write_proc(proc, 700, starttime="700", cwd=str(cwd))

    assert operations.keep_item(ctx, item_id).code == operations.EXIT_OK

    host = KillingHost(proc)
    retired = reconcile._retire_finished_sessions(
        ctx.conn,
        boundaries=make_boundaries(ctx.audit, host=host),
        audit=ctx.audit,
        scan=sessions.scan(registry_dir=registry, proc_root=proc),
        proc_root=proc,
    )
    assert retired == 0
    assert host.calls == []
    assert db.latest_session_for_item(ctx.conn, item_id).state.value == "running"
