"""The parts of the two bounds that are decidable without a socket.

The behaviour of the bounds needs a real port and lives in
``tests/integration/test_web_connection_limits.py``. What is here is everything that does
not: that the bounds are wired to their constants at all, the exact bytes of the refusal, and
the admission counter's arithmetic — including the case that matters most, a slot released
after the handler raised.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from robot_army.web import server as server_mod
from robot_army.web.server import (
    MAX_CONCURRENT_CONNECTIONS,
    OVER_CAPACITY_RESPONSE,
    REQUEST_TIMEOUT_SECONDS,
    BoundedThreadingHTTPServer,
    Handler,
)


class FakeSocket:
    """Enough socket for the refusal path, which is all it touches."""

    def __init__(self, *, pending: bytes = b"", send_fails: bool = False) -> None:
        self.sent = b""
        self.blocking: bool | None = None
        self.pending = pending
        self.send_fails = send_fails
        self.shutdown_called = False
        self.closed = False

    def setblocking(self, flag: bool) -> None:
        self.blocking = flag

    def send(self, payload: bytes) -> int:
        if self.send_fails:
            raise BlockingIOError
        self.sent += payload
        return len(payload)

    def recv(self, _size: int) -> bytes:
        drained, self.pending = self.pending, b""
        return drained

    def shutdown(self, _how: int) -> None:
        self.shutdown_called = True

    def close(self) -> None:
        self.closed = True


class FakeServer(BoundedThreadingHTTPServer):
    """A bounded server with no socket bound, so the counter can be driven directly.

    ``BoundedThreadingHTTPServer.__init__`` would bind a port; this skips straight to the
    state the admission decision reads.
    """

    def __init__(self) -> None:
        self._capacity_lock = threading.Lock()
        self._in_flight = 0
        self._saturated = False
        self.refused_over_capacity = 0
        self.threads_started: list[Any] = []

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        self.threads_started.append(request)


class ServingServer(FakeServer):
    """A bounded server that runs the *real* ``process_request_thread``.

    ``FakeServer`` stubs that method out so admission can be tested without a handler;
    releasing the slot is the other half, so it needs the real one — with the two things
    ``ThreadingMixIn.process_request_thread`` calls stubbed instead.
    """

    def __init__(self, *, raises: BaseException | None = None) -> None:
        super().__init__()
        self.raises = raises
        self.finished: list[Any] = []
        self.shut_down: list[Any] = []
        self.errors: list[Any] = []

    process_request_thread = BoundedThreadingHTTPServer.process_request_thread

    def finish_request(self, request: Any, client_address: Any) -> None:
        if self.raises is not None:
            raise self.raises
        self.finished.append(request)

    def shutdown_request(self, request: Any) -> None:
        self.shut_down.append(request)

    def handle_error(self, request: Any, client_address: Any) -> None:
        self.errors.append(request)


# -- the bounds are actually wired ------------------------------------------


def test_the_handler_carries_the_timeout_constant():
    """FR-001. Without this attribute every ``rfile`` read blocks forever (RA-13).

    ``socketserver.StreamRequestHandler.setup`` calls ``settimeout`` only when ``timeout`` is
    not ``None``, and neither ``http.server`` nor this module used to set it.
    """
    assert Handler.timeout == REQUEST_TIMEOUT_SECONDS
    assert REQUEST_TIMEOUT_SECONDS == 15


def test_the_cap_is_a_single_constant_with_a_defensible_value():
    """FR-013. Above what a few browser tabs hold; far below any descriptor limit."""
    assert MAX_CONCURRENT_CONNECTIONS == 32


def test_neither_bound_became_configuration():
    """FR-012. A knob with one caller and no second use is complexity the constitution bans."""
    from robot_army.config import WebConfig

    fields = set(WebConfig.__dataclass_fields__)
    assert fields == {"bind", "port", "refresh_seconds"}


# -- the refusal bytes ------------------------------------------------------


def test_the_refusal_is_a_well_formed_503_that_closes():
    """Contract C2. A malformed refusal is worse than none: a keep-alive client mis-frames."""
    head, _, body = OVER_CAPACITY_RESPONSE.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")

    assert lines[0] == b"HTTP/1.1 503 Service Unavailable"
    headers = dict(line.split(b": ", 1) for line in lines[1:])
    assert headers[b"Connection"] == b"close"
    assert headers[b"Cache-Control"] == b"no-store"
    assert headers[b"Retry-After"] == b"1"
    assert headers[b"Content-Type"] == b"application/json; charset=utf-8"
    # Declared and actual must agree, or a keep-alive client mis-frames whatever follows.
    assert int(headers[b"Content-Length"]) == len(body)
    assert b'"ok": false' in body


# -- the admission counter --------------------------------------------------


def test_a_connection_under_the_cap_is_admitted_and_counted():
    server = FakeServer()
    sock = FakeSocket()

    server.process_request(sock, ("127.0.0.1", 1))

    assert server.threads_started == [sock]
    assert server._in_flight == 1
    assert server.refused_over_capacity == 0
    assert sock.sent == b""


def test_a_connection_at_the_cap_is_refused_without_a_thread(monkeypatch):
    """FR-004, FR-006. The refusal happens before ``super()`` starts anything."""
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 1)
    server = FakeServer()

    admitted = FakeSocket()
    server.process_request(admitted, ("127.0.0.1", 1))

    refused = FakeSocket(pending=b"GET / HTTP/1.1\r\n\r\n")
    server.process_request(refused, ("127.0.0.1", 2))

    assert server.threads_started == [admitted]
    assert server._in_flight == 1
    assert server.refused_over_capacity == 1
    assert refused.sent == OVER_CAPACITY_RESPONSE
    assert refused.blocking is False, "the accept loop must never block on a refusal"
    assert refused.pending == b"", "unread bytes at close turn the FIN into an RST"
    assert refused.shutdown_called and refused.closed


def test_a_refusal_whose_send_would_block_still_closes(monkeypatch):
    """Delivery is best-effort; releasing the descriptor is not."""
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 0)
    server = FakeServer()
    sock = FakeSocket(send_fails=True)

    server.process_request(sock, ("127.0.0.1", 1))

    assert sock.closed
    assert server.refused_over_capacity == 1


def test_a_slot_is_released_when_the_handler_raises():
    """FR-008. A failure that kept its slot would starve the server one connection at a time."""
    server = ServingServer(raises=RuntimeError("the handler failed"))
    server._in_flight = 1
    sock = FakeSocket()

    server.process_request_thread(sock, ("127.0.0.1", 1))

    assert server._in_flight == 0
    assert server.errors == [sock], "the failure must still be reported, only the slot freed"
    assert server.shut_down == [sock]


def test_a_slot_is_released_when_the_handler_returns():
    server = ServingServer()
    server._in_flight = 1
    sock = FakeSocket()

    server.process_request_thread(sock, ("127.0.0.1", 1))

    assert server._in_flight == 0
    assert server.finished == [sock]


# -- saturation reporting ---------------------------------------------------


def test_many_refusals_in_one_episode_report_once(monkeypatch, capsys):
    """FR-009. The message is bounded by episodes, not by refusals."""
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 0)
    server = FakeServer()

    for _ in range(5):
        server.process_request(FakeSocket(), ("127.0.0.1", 1))

    assert server.refused_over_capacity == 5
    assert capsys.readouterr().err.count("at capacity") == 1


def test_dropping_below_the_cap_re_arms_the_message(monkeypatch, capsys):
    """A second episode is a second thing worth knowing about."""
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 1)
    server = FakeServer()

    server.process_request(FakeSocket(), ("127.0.0.1", 1))
    server.process_request(FakeSocket(), ("127.0.0.1", 2))  # refused: episode one
    server._release_slot()  # the admitted connection ends
    server.process_request(FakeSocket(), ("127.0.0.1", 3))  # admitted, clears saturation
    server.process_request(FakeSocket(), ("127.0.0.1", 4))  # refused: episode two

    assert server.refused_over_capacity == 2
    assert capsys.readouterr().err.count("at capacity") == 2


def test_a_recycled_slot_does_not_re_arm_the_message(monkeypatch, capsys):
    """The reason the flag has hysteresis rather than clearing on any release.

    Under a sustained flood the count sits *at* the cap and oscillates by one as connections
    time out and new ones are admitted. A flag cleared by any release would re-arm on every
    recycled slot and print a line for each, which is one per connection in all but name.
    """
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 4)
    server = FakeServer()

    for i in range(4):
        server.process_request(FakeSocket(), ("127.0.0.1", i))
    server.process_request(FakeSocket(), ("127.0.0.1", 9))  # refused: the episode begins

    for i in range(10):  # ten connections recycle while the pressure never lets up
        server._release_slot()
        server.process_request(FakeSocket(), ("127.0.0.1", i))
        server.process_request(FakeSocket(), ("127.0.0.1", 99))  # refused again

    assert server.refused_over_capacity == 11
    assert capsys.readouterr().err.count("at capacity") == 1


def test_the_refusal_path_writes_no_audit_record(monkeypatch):
    """FR-011. The enumerated Principle III exception, held in place by a test.

    A record per refusal would open a SQLite connection and an audit file handle per refused
    connection — the exact pair this feature bounds — making the log the amplifier of the
    flood it documents. If someone later adds one, this fails and they have to change the
    plan's Constitution Check rather than change it by accident.
    """
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", 0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the refusal path built a Context")

    monkeypatch.setattr(server_mod.operations, "build_context", forbidden)

    server = FakeServer()
    server.process_request(FakeSocket(), ("127.0.0.1", 1))

    assert server.refused_over_capacity == 1


# -- connection endings are not errors --------------------------------------


@pytest.mark.parametrize(
    "exc", [TimeoutError(), ConnectionResetError(), BrokenPipeError()]
)
def test_a_connection_ending_prints_nothing(exc, capsys):
    """FR-003, research R8. Under a flood, one traceback per dropped connection is itself
    an amplifier — and a client hanging up is not this program failing."""
    server = FakeServer()
    try:
        raise exc
    except OSError:
        server.handle_error(FakeSocket(), ("127.0.0.1", 1))

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_a_real_failure_still_prints(capsys):
    """The ban on swallowed exceptions is about *our* failures, and this is one."""
    server = FakeServer()
    try:
        raise ValueError("a genuine bug")
    except ValueError:
        server.handle_error(FakeSocket(), ("127.0.0.1", 1))

    captured = capsys.readouterr()
    assert "ValueError" in captured.err + captured.out
