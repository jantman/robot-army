"""Attaching a terminal window to a running session (T066, research.md R10).

Both halves already existed and were measured in M0: ``DtachHost.attach_command`` returns
``dtach -a <socket>``, and ``KittyDisplay.open`` launches a tab running an argv. M0 also
confirmed that reattachment repaints fully and that two viewers may attach at once — so
there is deliberately **no** "is something already attached" check. FR-025's tolerance
requirement is satisfied by the host's measured capability rather than by logic here.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_boundaries, seed_item, seed_session

from robot_army import db, operations
from robot_army.boundaries import BoundaryError
from robot_army.states import SessionState, WorkItemState


@pytest.fixture
def ctx(config, conn, monkeypatch):
    from tests.conftest import StubDisplay, StubSessionHost

    display = StubDisplay()
    host = StubSessionHost()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log, conn: make_boundaries(log, level=level, display=display, host=host),
    )
    built = operations.build_context(config)
    yield built
    built.close()


def running(conn, **kwargs):
    item_id = seed_item(conn, state="active", **kwargs)
    seed_session(conn, item_id, state="running", host_socket="/tmp/ra-attach.sock")
    return item_id


def test_attaching_opens_a_window_running_the_host_attach_command(ctx, conn):
    item_id = running(conn)
    result = operations.attach(ctx, item_id)

    assert result.code == 0
    assert result.data["attached"] is True
    opened = ctx.boundaries.display.opened
    assert len(opened) == 1
    assert opened[0]["argv"] == ["dtach", "-a", "/tmp/ra-attach.sock"]
    assert str(item_id) in opened[0]["title"]


def test_attaching_changes_no_session_state(ctx, conn):
    """It opens a viewer. Consuming the session would be the opposite of the point."""
    item_id = running(conn)
    before = db.latest_session_for_item(conn, item_id)
    operations.attach(ctx, item_id)
    after = db.latest_session_for_item(conn, item_id)

    assert after == before
    assert after.state is SessionState.RUNNING
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_second_attach_also_succeeds(ctx, conn):
    """M0 measured multi-viewer tolerance. Refusing the second would be inventing a limit
    the host does not have."""
    item_id = running(conn)
    first = operations.attach(ctx, item_id)
    second = operations.attach(ctx, item_id)

    assert first.code == 0 and second.code == 0
    assert len(ctx.boundaries.display.opened) == 2
    assert first.data["window_id"] != second.data["window_id"]


@pytest.mark.parametrize("state", ["starting", "exited_clean", "exited_error", "lost"])
def test_a_non_running_session_is_refused_with_exit_3(ctx, conn, state):
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state=state)
    result = operations.attach(ctx, item_id)

    assert result.code == operations.EXIT_PRECONDITION
    assert result.data["attached"] is False
    assert ctx.boundaries.display.opened == []


def test_an_item_with_no_session_at_all_is_refused(ctx, conn):
    item_id = seed_item(conn, state="ready")
    result = operations.attach(ctx, item_id)
    assert result.code == operations.EXIT_PRECONDITION
    assert "no running session" in "\n".join(result.lines)


def test_a_missing_item_is_refused_rather_than_raising(ctx):
    result = operations.attach(ctx, 4242)
    assert result.code == operations.EXIT_FAILED
    assert "no work item with id 4242" in "\n".join(result.lines)


def test_a_boundary_failure_surfaces_as_a_refusal_not_an_exception(ctx, conn):
    """The author's next action is "start kitty", and the message has to say so."""
    item_id = running(conn)

    def refuse(*args, **kwargs):
        raise BoundaryError("kitty launch failed: no socket answered")

    ctx.boundaries.display.open = refuse
    result = operations.attach(ctx, item_id)

    assert result.code == operations.EXIT_FAILED
    assert result.data["attached"] is False
    assert "no terminal control socket answered" in "\n".join(result.lines)
    # Nothing about the session changed.
    assert db.latest_session_for_item(conn, item_id).state is SessionState.RUNNING
    assert db.get_work_item(conn, item_id).state is WorkItemState.ACTIVE


def test_a_session_with_no_recorded_socket_is_refused(ctx, conn):
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running", host_socket=None)
    result = operations.attach(ctx, item_id)
    assert result.code == operations.EXIT_PRECONDITION
    assert "no host socket" in "\n".join(result.lines)


# -- the web control --------------------------------------------------------


def test_the_web_offers_attach_only_for_a_running_session(web, conn):
    """FR-029: a control must not be offered where it is not valid."""
    active = running(conn, issue_number=1)
    assert "attach" in web.get_json(f"/item/{active}").json()["actions"]
    assert f"/item/{active}/attach" in web.get(f"/item/{active}").text

    idle = seed_item(conn, issue_number=2, state="interrupted")
    seed_session(conn, idle, state="lost")
    assert "attach" not in web.get_json(f"/item/{idle}").json()["actions"]
    assert f"/item/{idle}/attach" not in web.get(f"/item/{idle}").text


def test_the_web_control_needs_no_confirmation(web, conn):
    """It opens a window. Nothing stops, starts, or is discarded."""
    item_id = running(conn)
    body = web.get(f"/item/{item_id}").text
    assert f'action="/item/{item_id}/attach"' in body
    assert f"/item/{item_id}/confirm/attach" not in body


def test_the_web_control_opens_a_window_and_reports_it(web, conn):
    item_id = running(conn)
    response = web.post_json(f"/item/{item_id}/attach")
    assert response.status == 303
    assert response.json()["attached"] is True
    assert len(web.display.opened) == 1


def test_the_web_control_refuses_when_the_session_is_not_running(web, conn):
    item_id = seed_item(conn, state="interrupted")
    seed_session(conn, item_id, state="lost")
    response = web.post_json(f"/item/{item_id}/attach")
    assert response.status == 409
    assert web.display.opened == []
