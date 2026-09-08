"""The one line in `show` that tells the maintainer to go and do something (issue #51).

Every other line of `show` reports; this one instructs, and until this module nothing in the
suite asserted anything about it at all. What it printed was decided by `if
session.host_socket:` — "we wrote a path down once" — which is true of every attempt that ever
ran, including the ones that are over.

The assertions here are built around the fact that makes this more than cosmetic: a socket is
named after the **work item**, not the session, so the path recorded by an attempt that ended
is the path the *next* attempt listens on. A liveness check alone would therefore keep offering
the ended attempt's line — and that command works. It attaches the reader to a live session
under a row that says `lost`. Several tests below deliberately mark the socket live while
asserting that a finished attempt stays silent; that combination is the point, not a
redundancy.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import StubSessionHost, make_boundaries, seed_item, seed_session

from robot_army import operations
from robot_army.boundaries import BoundaryError
from robot_army.states import SessionState

#: The path the issue's own report carries, and the shape `Layout.socket_for` produces.
SOCKET = "/run/user/1000/robot-army/12.sock"

TERMINAL = ["exited_clean", "exited_error", "lost"]
OPEN = ["starting", "running"]


class RaisingSessionHost(StubSessionHost):
    """A host whose probe cannot answer — the one branch a real machine reaches rarely.

    `DtachHost.is_alive` raises when the probe hangs past its timeout or the operating
    system refuses in an unexpected way. `show` calls nothing else that raises, so without
    the catch this stub exercises, one misbehaving socket would take down a read-only
    command.
    """

    message = f"dtach probe on {SOCKET} timed out"

    def is_alive(self, handle: Any) -> bool:
        raise BoundaryError(self.message)


@pytest.fixture
def host() -> StubSessionHost:
    return StubSessionHost()


@pytest.fixture
def ctx(config, conn, monkeypatch, host):
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log, conn: make_boundaries(log, level=level, host=host),
    )
    built = operations.build_context(config)
    yield built
    built.close()


def _attach_lines(ctx, item_id: int) -> list[str]:
    """Every line `show` prints about attaching, in the order it printed them."""
    result = operations.show(ctx, item_id)
    assert result.code == operations.EXIT_OK
    return [
        line
        for line in result.lines
        if "reattach" in line or "unverified" in line
    ]


def _rendered(ctx, item_id: int) -> str:
    return "\n".join(operations.show(ctx, item_id).lines)


# -- US1: a session that is over offers nowhere to go -----------------------


@pytest.mark.parametrize("state", TERMINAL)
def test_a_finished_attempt_offers_nothing_even_with_a_live_socket(ctx, conn, host, state):
    """FR-001. The live socket is the assertion, not the setup.

    It is what the item's *next* dispatch will be listening on, and it is why the guard is
    the row's own state rather than the path's liveness.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state=state, host_socket=SOCKET)
    host.alive.add(SOCKET)

    text = _rendered(ctx, item_id)

    assert "dtach" not in text
    assert _attach_lines(ctx, item_id) == []
    assert f"#1 {state}" in text, "the state line itself must be untouched"


def test_two_attempts_on_one_socket_offer_only_the_open_one(ctx, conn, host):
    """FR-001, and the half of issue #51 the report did not reach.

    Both rows record the same path because the path is the item's. Before this feature both
    carried the command, and the one under `lost` was the dangerous one: it works.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="lost", host_socket=SOCKET)
    seed_session(conn, item_id, state="running", host_socket=SOCKET)
    host.alive.add(SOCKET)

    lines = operations.show(ctx, item_id).lines
    attach = [i for i, line in enumerate(lines) if "dtach" in line]

    assert len(attach) == 1
    # Under attempt #2, not merely somewhere in the output: a regression that prints the
    # command under the wrong row would satisfy a bare count.
    row_2 = next(i for i, line in enumerate(lines) if line.startswith("  #2 "))
    row_1 = next(i for i, line in enumerate(lines) if line.startswith("  #1 "))
    assert row_1 < row_2 < attach[0]


@pytest.mark.parametrize("state", TERMINAL + OPEN)
def test_an_attempt_with_no_socket_says_nothing_and_invents_nothing(ctx, conn, state):
    """FR-005, unchanged from before this feature: nothing recorded, nothing printed."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state=state, host_socket=None)

    text = _rendered(ctx, item_id)

    assert _attach_lines(ctx, item_id) == []
    assert "dtach" not in text
    assert "/run/user" not in text


# -- US2: an open row says what is actually at the other end ----------------


def test_a_live_session_is_still_offered_byte_for_byte(ctx, conn, host):
    """FR-002. The regression guard for everything else here: the working case must not move."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running", host_socket=SOCKET)
    host.alive.add(SOCKET)

    assert _attach_lines(ctx, item_id) == [f"       reattach: dtach -a {SOCKET}"]


@pytest.mark.parametrize("state", OPEN)
def test_an_open_row_with_nothing_listening_says_so(ctx, conn, state):
    """FR-003. The record says this session is live; the socket says otherwise.

    Silence here would be the wrong answer, not a safe one: the reader would have a missing
    line to interpret rather than a contradiction to act on.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state=state, host_socket=SOCKET)

    assert _attach_lines(ctx, item_id) == [
        f"       reattach: not available — nothing is listening on {SOCKET}"
    ]
    assert "dtach" not in _rendered(ctx, item_id)


def test_a_rehearsals_row_is_judged_by_the_same_two_questions(ctx, conn, host):
    """A `dry_run` row names a socket no process ever bound, and needs no special case.

    Nothing here inspects the row to choose a host — that dispatch happens in exactly one
    place in this system and a test holds it there. The real host asked about a rehearsal's
    socket answers "nothing is listening", which is the true answer to the question asked.
    """
    item_id = seed_item(conn, state="active", dry_run=True)
    seed_session(conn, item_id, state="running", dry_run=True, host_socket=SOCKET)
    assert _attach_lines(ctx, item_id) == [
        f"       reattach: not available — nothing is listening on {SOCKET}"
    ]

    ended = seed_item(conn, issue_number=43, state="done", dry_run=True)
    seed_session(conn, ended, state="lost", dry_run=True, host_socket=SOCKET)
    host.alive.add(SOCKET)
    assert _attach_lines(ctx, ended) == []


# -- US3: a check that could not answer -------------------------------------


@pytest.fixture
def raising_host() -> RaisingSessionHost:
    return RaisingSessionHost()


@pytest.fixture
def raising_ctx(config, conn, monkeypatch, raising_host):
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log, conn: make_boundaries(log, level=level, host=raising_host),
    )
    built = operations.build_context(config)
    yield built
    built.close()


def test_an_unanswerable_probe_offers_the_command_with_the_caveat(raising_ctx, conn):
    """FR-004. Neither "dead" nor "alive" — the boundary's own words, unedited."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running", host_socket=SOCKET)

    assert _attach_lines(raising_ctx, item_id) == [
        f"       reattach: dtach -a {SOCKET}",
        f"                 unverified: {RaisingSessionHost.message}",
    ]


def test_a_raising_probe_degrades_one_line_and_not_the_command(raising_ctx, conn):
    """FR-009. `show` is how the maintainer looks at a machine that is misbehaving."""
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="lost", host_socket=SOCKET)
    seed_session(conn, item_id, state="running", host_socket=SOCKET)

    result = operations.show(raising_ctx, item_id)
    text = "\n".join(result.lines)

    assert result.code == operations.EXIT_OK
    for heading in ("state history:", "sessions (2 attempt(s)):", "resume-decision signals"):
        assert heading in text
    # The finished attempt never reaches the probe, so it cannot inherit the caveat.
    assert text.count("unverified:") == 1


# -- the payload this feature deliberately did not touch --------------------


def test_the_json_payload_keeps_exactly_the_keys_it_had(ctx, conn, host):
    """FR-008, and the guard on a decision made by *not* acting.

    `_session_dict` has two callers and the other one renders a web page per request, so the
    probe stays out of it. A later change that adds a key here changes the web's item view;
    this is where that gets noticed.
    """
    item_id = seed_item(conn, state="active")
    seed_session(conn, item_id, state="running", host_socket=SOCKET)
    host.alive.add(SOCKET)

    payload = operations.show(ctx, item_id).data["sessions"][0]

    assert sorted(payload) == [
        "attempt",
        "confirmed_at",
        "dry_run",
        "ended_at",
        "exit_code",
        "host_socket",
        "pid",
        "scope",
        "session_id",
        "signal",
        "started_at",
        "state",
        "window_id",
    ]
    assert payload["host_socket"] == SOCKET


def test_the_states_the_guard_reads_are_the_states_that_exist(ctx):
    """A new session state must be classified deliberately, not inherit "open" by default.

    `_reattach_lines` branches on terminal-versus-not, so a sixth state added later falls
    into the offering branch silently. This fails when that happens, which is the moment to
    decide which side it belongs on.
    """
    assert {s.value for s in SessionState} == set(TERMINAL) | set(OPEN)
