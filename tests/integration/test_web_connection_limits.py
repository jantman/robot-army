"""The two socket-level bounds: a wait a silent client cannot outlast, and a connection cap.

Every other web test in this repository calls ``handle(app, request)`` as a pure function,
which is what makes the refusal cases cheap enough to write exhaustively. **Neither bound in
this module is reachable that way.** A timeout on a socket that was never handed to a handler,
and a refusal decided before ``ThreadingMixIn`` starts a thread, are both invisible to a test
that starts from a parsed ``Request`` — so this module binds a real port and speaks HTTP over
a raw socket, the same exception ``test_web_end_to_end.py`` makes for itself.

Both bounds are shrunk before the server is built — the cap always to two, the wait to
whichever value the test's own subject calls for. That keeps the module fast, and it is also a
live check that each is a single knob rather than a literal repeated at call sites: if either
were inlined anywhere, the override would not reach it and the test would hang or fail.

Two fixtures, because the two bounds interfere. ``bounded_server`` shortens the wait to a
fraction of a second, which is what the tests *about* the wait need. ``patient_server`` gives
it five seconds instead, which is what every test about the *cap* needs — those reach capacity
by holding connections open, and a held connection that quietly times out frees a slot and
turns the next refusal into an admission. That failure only appears on a loaded machine, which
is to say in CI and not here.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import operations
from robot_army.web import server as server_mod
from robot_army.web.server import WebApp, build_server

#: Long enough that a wait that is not bounded is unmistakable, short enough that a module
#: full of them costs a second. Every assertion below allows generous slack above it, because
#: the claim under test is "eventually, on its own", not "punctually".
TEST_TIMEOUT_SECONDS = 0.4

#: Two, so "at the cap" is reachable by opening two connections and holding them.
TEST_MAX_CONNECTIONS = 2

#: The bound used by the tests that are *not* about the bound. The cap tests need their held
#: connections to still be held when the next assertion runs, and a 0.4-second bound on a
#: loaded CI runner is a connection that quietly times out mid-test and turns a refusal into
#: an admission. Long enough to be effectively infinite for a test, short enough that a
#: mistake still ends rather than hangs.
HOLD_TIMEOUT_SECONDS = 5.0

#: The ceiling every "did it close?" assertion is made against. Far above the bound and far
#: below "forever", so a regression to the unbounded behaviour fails rather than hangs.
CLOSE_DEADLINE_SECONDS = 10.0


def stub_boundaries(monkeypatch) -> None:
    """The fakes every live-server test needs, and nothing about the bounds."""
    from tests.conftest import FakeIssueReader, StubDisplay, StubSessionHost

    reader, display, host = FakeIssueReader(), StubDisplay(), StubSessionHost()
    monkeypatch.setattr(
        operations,
        "wire",
        lambda level, cfg, log: make_boundaries(
            log, level=level, reader=reader, display=display, host=host
        ),
    )
    operations.clear_resume_signal_cache()


def shrink_bounds(monkeypatch, *, handler_timeout: float) -> None:
    """Put both bounds within a test's patience.

    ``Handler.timeout`` rather than ``REQUEST_TIMEOUT_SECONDS``: the attribute is what
    ``StreamRequestHandler.setup`` reads, and it is bound at class creation, so patching the
    constant would reach nothing. That the attribute *is* the constant by default is asserted
    in ``tests/unit/test_web_connection_limits.py``, where it belongs. The cap, by contrast,
    is read on every admission decision, so there the constant is the knob.
    """
    monkeypatch.setattr(server_mod.Handler, "timeout", handler_timeout)
    monkeypatch.setattr(server_mod, "MAX_CONCURRENT_CONNECTIONS", TEST_MAX_CONNECTIONS)


def run_server(config) -> Any:
    """A live server on an ephemeral port, serving on a daemon thread."""
    app = WebApp(config)
    server = build_server(app, bind="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.daemon = True
    thread.start()
    return server, thread


@pytest.fixture
def bounded_server(config, conn, monkeypatch):
    """A live server whose wait bound is short enough to sit and watch elapse.

    Yields ``(base_url, server)`` — the URL for ordinary requests, and the server itself for
    the tests that read ``refused_over_capacity`` off it.
    """
    stub_boundaries(monkeypatch)
    shrink_bounds(monkeypatch, handler_timeout=TEST_TIMEOUT_SECONDS)
    server, thread = run_server(config)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def patient_server(config, conn, monkeypatch):
    """The same server, but its wait bound will not expire under a test's own feet.

    Used by every test about the *cap*: those hold connections open to reach capacity, and a
    held connection that times out mid-test frees a slot and turns the next refusal into an
    admission — a flake that only appears on a loaded machine.
    """
    stub_boundaries(monkeypatch)
    shrink_bounds(monkeypatch, handler_timeout=HOLD_TIMEOUT_SECONDS)
    server, thread = run_server(config)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def connect(base: str) -> socket.socket:
    """A raw connection to the live server, closed by the caller."""
    port = int(base.rsplit(":", 1)[1])
    return socket.create_connection(("127.0.0.1", port), timeout=CLOSE_DEADLINE_SECONDS)


def wait_for_close(sock: socket.socket) -> float:
    """Seconds until the server closed the connection. Fails rather than hangs."""
    sock.settimeout(CLOSE_DEADLINE_SECONDS)
    start = time.monotonic()
    try:
        while sock.recv(4096):
            pass
    except TimeoutError:
        pytest.fail(
            f"the server held the connection for {CLOSE_DEADLINE_SECONDS}s — the bound is "
            "not in force"
        )
    return time.monotonic() - start


def read_until_close(sock: socket.socket) -> bytes:
    """Everything the server sent before closing."""
    sock.settimeout(CLOSE_DEADLINE_SECONDS)
    chunks = []
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except (TimeoutError, ConnectionResetError):
        pass
    return b"".join(chunks)


def get(base: str, path: str = "/") -> bytes:
    """One ordinary GET over its own connection, read to close."""
    sock = connect(base)
    try:
        host = base.removeprefix("http://")
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept: text/html\r\n"
            f"Connection: close\r\n\r\n".encode()
        )
        return read_until_close(sock)
    finally:
        sock.close()


# -- User story 1: the bound ------------------------------------------------


def test_a_connection_that_says_nothing_is_given_up_on(bounded_server):
    """Spec scenario 1, SC-001. The cheapest exploit there is: connect, then nothing."""
    base, _server = bounded_server
    sock = connect(base)
    try:
        elapsed = wait_for_close(sock)
    finally:
        sock.close()
    assert elapsed < CLOSE_DEADLINE_SECONDS


def test_a_partial_request_line_is_given_up_on(bounded_server):
    """Spec scenario 2. The classic slowloris: begin a request and stop."""
    base, _server = bounded_server
    sock = connect(base)
    try:
        sock.sendall(b"GET / HTTP/1.1\r\n")
        elapsed = wait_for_close(sock)
    finally:
        sock.close()
    assert elapsed < CLOSE_DEADLINE_SECONDS


def test_an_over_declared_body_is_given_up_on(bounded_server):
    """Spec scenario 3, FR-002.

    ``MAX_BODY_BYTES`` bounds the size, so a declared 4096 is accepted and read — and before
    this bound existed, ``rfile.read(4096)`` after one byte blocked for good.
    """
    base, _server = bounded_server
    host = base.removeprefix("http://")
    sock = connect(base)
    try:
        sock.sendall(
            f"POST /dispatch/pause HTTP/1.1\r\nHost: {host}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: 4096\r\n\r\nx".encode()
        )
        elapsed = wait_for_close(sock)
    finally:
        sock.close()
    assert elapsed < CLOSE_DEADLINE_SECONDS


def test_an_idle_keep_alive_connection_is_given_up_on(bounded_server):
    """Spec scenario 4. A *completed* request left open costs exactly as much as a stalled one."""
    base, _server = bounded_server
    host = base.removeprefix("http://")
    sock = connect(base)
    try:
        sock.sendall(f"GET /queue.json HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        sock.settimeout(CLOSE_DEADLINE_SECONDS)
        first = sock.recv(4096)
        assert first.startswith(b"HTTP/1.1 200"), first[:60]
        # The connection is now idle and kept alive. Nothing more is sent.
        elapsed = wait_for_close(sock)
    finally:
        sock.close()
    assert elapsed < CLOSE_DEADLINE_SECONDS


def test_a_request_inside_the_bound_is_served_unchanged(bounded_server, conn):
    """Spec scenario 5, FR-007. The bound changes how long silence is tolerated, nothing else."""
    base, _server = bounded_server
    seed_item(conn, state="ready", title="Still rendered")

    raw = get(base, "/queue")
    assert raw.startswith(b"HTTP/1.1 200"), raw[:80]
    assert b"Still rendered" in raw
    assert b"Cache-Control: no-store" in raw


def test_a_slow_handler_is_not_cut_off_by_the_bound(bounded_server, monkeypatch):
    """SC-004, spec edge case 1.

    The bound is on waiting for the *client*. A view that takes longer than the bound to
    compute must still return — this is the regression that would make the fix worse than the
    defect, because the interface's slowest pages are the ones an incident needs.
    """
    base, _server = bounded_server
    original = server_mod.handle

    def slow(app, request):
        time.sleep(TEST_TIMEOUT_SECONDS * 3)
        return original(app, request)

    monkeypatch.setattr(server_mod, "handle", slow)

    raw = get(base, "/queue.json")
    assert raw.startswith(b"HTTP/1.1 200"), raw[:80]


def test_a_timed_out_connection_says_nothing_on_stderr(bounded_server, capfd):
    """FR-003, contract C1. A silent client is not an incident and must not read like one."""
    base, _server = bounded_server
    capfd.readouterr()

    sock = connect(base)
    try:
        sock.sendall(b"GET / HTTP/1.1\r\n")
        wait_for_close(sock)
    finally:
        sock.close()

    captured = capfd.readouterr()
    assert captured.err == ""
    assert "Traceback" not in captured.out


# -- User story 2: the cap --------------------------------------------------


def hold(base: str, count: int) -> list[socket.socket]:
    """Open ``count`` connections and keep them, each having begun a request it never finishes."""
    held = []
    for _ in range(count):
        sock = connect(base)
        sock.sendall(b"GET / HTTP/1.1\r\n")
        held.append(sock)
    return held


def wait_for_in_flight(server: Any, expected: int) -> None:
    """Block until the accept loop has actually admitted them. Fails rather than hangs.

    ``connect`` returns as soon as the kernel completes the handshake, which is before
    ``accept`` has run — so a test that assumes the count moved with the connection is a
    flake waiting for a loaded machine.
    """
    deadline = time.monotonic() + CLOSE_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if server._in_flight >= expected:
            return
        time.sleep(0.005)
    pytest.fail(f"only {server._in_flight} of {expected} connections were admitted")


def test_a_connection_beyond_the_cap_is_refused_and_closed(patient_server):
    """Spec scenario 1, contract C2."""
    base, server = patient_server
    held = hold(base, TEST_MAX_CONNECTIONS)
    try:
        wait_for_in_flight(server, TEST_MAX_CONNECTIONS)

        refused = connect(base)
        try:
            raw = read_until_close(refused)
        finally:
            refused.close()
    finally:
        for sock in held:
            sock.close()

    assert raw.startswith(b"HTTP/1.1 503 Service Unavailable"), raw[:80]
    assert b"Connection: close" in raw
    assert b"too many connections" in raw
    assert server.refused_over_capacity == 1


def test_releasing_a_connection_frees_a_slot(patient_server):
    """Spec scenario 2, SC-002. The refusal is a "not now", and it has to mean it."""
    base, server = patient_server
    held = hold(base, TEST_MAX_CONNECTIONS)
    wait_for_in_flight(server, TEST_MAX_CONNECTIONS)

    for sock in held:
        sock.close()
    deadline = time.monotonic() + CLOSE_DEADLINE_SECONDS
    while server._in_flight > 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert server._in_flight == 0, "the released connections never gave their slots back"

    raw = get(base, "/queue.json")
    assert raw.startswith(b"HTTP/1.1 200"), raw[:80]


def test_a_refused_connection_opens_nothing(patient_server, monkeypatch):
    """Spec scenario 3, FR-006.

    The whole point of deciding at ``process_request``: a refused connection must not cost a
    SQLite connection and an audit file handle, or the refusal funds the attack.
    """
    base, server = patient_server
    contexts = []
    real = server_mod.operations.build_context
    monkeypatch.setattr(
        server_mod.operations,
        "build_context",
        lambda *a, **kw: (contexts.append(1), real(*a, **kw))[1],
    )

    held = hold(base, TEST_MAX_CONNECTIONS)
    try:
        wait_for_in_flight(server, TEST_MAX_CONNECTIONS)
        before = len(contexts)
        for _ in range(5):
            refused = connect(base)
            read_until_close(refused)
            refused.close()
        # Read the count *here*, not after the held connections are closed. Each of those is
        # parked mid-headers, and closing it delivers the EOF that ends the header block —
        # so the server then dispatches a header-less GET and builds a Context for it, which
        # has nothing to do with the refusals and would race this assertion.
        after = len(contexts)
        refusals = server.refused_over_capacity
    finally:
        for sock in held:
            sock.close()

    assert refusals == 5
    assert after == before


def test_an_over_large_body_is_still_refused_without_reading(bounded_server):
    """FR-014. The size refusal and the wait bound are different things; neither replaces the other."""
    base, _server = bounded_server
    host = base.removeprefix("http://")
    sock = connect(base)
    try:
        sock.sendall(
            f"POST /dispatch/pause HTTP/1.1\r\nHost: {host}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {server_mod.MAX_BODY_BYTES + 1}\r\n\r\n".encode()
        )
        raw = read_until_close(sock)
    finally:
        sock.close()

    assert raw.startswith(b"HTTP/1.1 413"), raw[:80]
    assert b"Connection: close" in raw
    assert b"request body too large" in raw


# -- User story 3: the record -----------------------------------------------


def test_the_stop_record_carries_the_refusal_count(config, conn, monkeypatch):
    """Spec scenario 2, FR-010, SC-006.

    The whole durable trace of a saturation episode is this one integer, so it is worth a
    test that runs the real ``serve`` — signal handler, shutdown path, stop record and all —
    rather than asserting on a dict built in isolation.
    """
    stub_boundaries(monkeypatch)
    # The patient bound: this test holds connections to reach capacity, and a held connection
    # timing out mid-test would turn one of its three refusals into an admission.
    shrink_bounds(monkeypatch, handler_timeout=HOLD_TIMEOUT_SECONDS)

    built: dict[str, Any] = {}
    real_build = server_mod.build_server

    def capture(app, *, bind, port):
        built["server"] = real_build(app, bind=bind, port=port)
        return built["server"]

    monkeypatch.setattr(server_mod, "build_server", capture)

    def drive() -> None:
        deadline = time.monotonic() + CLOSE_DEADLINE_SECONDS
        while "server" not in built and time.monotonic() < deadline:
            time.sleep(0.005)
        server = built["server"]
        base = f"http://127.0.0.1:{server.server_address[1]}"
        held = hold(base, TEST_MAX_CONNECTIONS)
        try:
            wait_for_in_flight(server, TEST_MAX_CONNECTIONS)
            for _ in range(3):
                refused = connect(base)
                read_until_close(refused)
                refused.close()
        finally:
            for sock in held:
                sock.close()
        os.kill(os.getpid(), signal.SIGTERM)

    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    try:
        assert server_mod.serve(config, bind="127.0.0.1", port=0) == 0
    finally:
        driver.join(timeout=CLOSE_DEADLINE_SECONDS)
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])

    stops = [
        json.loads(line)
        for path in sorted(config.layout.log_dir.glob("audit-*.jsonl"))
        for line in path.read_text().splitlines()
        if json.loads(line)["action"] == "web.stop"
    ]
    assert len(stops) == 1
    assert stops[0]["detail"]["refused_over_capacity"] == 3
    assert stops[0]["detail"]["reason"] == "signal"
