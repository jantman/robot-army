"""Bind-address validation and the startup preconditions (T018).

Under FR-003 the interface has no authentication by design, so **the bind address is the
security policy**. That makes this the one piece of configuration whose validation is a
safety property rather than a convenience, and the one fact that must never be silent.
"""

from __future__ import annotations

import socket

import pytest
from tests.conftest import config_dict, monkey_token

from robot_army.config import parse
from robot_army.migrations import SCHEMA_VERSION
from robot_army.web.server import check_preconditions, validate_bind


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1111"])
def test_a_globally_routable_address_is_refused(address):
    """Binding where the internet can reach it would publish full control."""
    problem, warning = validate_bind(address)
    assert problem is not None
    assert "globally routable" in problem
    assert warning is None


@pytest.mark.parametrize("address", ["127.0.0.1", "127.0.0.53", "::1"])
def test_loopback_is_accepted_with_no_warning(address):
    problem, warning = validate_bind(address)
    assert problem is None
    assert warning is None, "loopback is the shipped default; warning about it is noise"


@pytest.mark.parametrize(
    "address",
    [
        "192.168.1.20",
        "10.0.0.5",
        "172.16.0.1",
        "100.64.1.2",  # the VPN range (R13)
        "169.254.1.1",
    ],
)
def test_private_and_vpn_addresses_are_accepted_with_a_warning(address):
    """Reachable from the LAN is the intended configuration — and it is announced."""
    problem, warning = validate_bind(address)
    assert problem is None
    assert warning is not None
    assert "FULL CONTROL" in warning


def test_the_unspecified_address_is_accepted_with_a_warning():
    """``0.0.0.0`` cannot be classified — it means every interface, including future ones.

    Refusing it would push toward pinning an address a DHCP lease can change, trading a
    real ergonomic problem for a theoretical safety one (R13).
    """
    problem, warning = validate_bind("0.0.0.0")  # noqa: S104 - the case under test
    assert problem is None
    assert warning is not None
    assert "every network interface" in warning


def test_a_hostname_is_refused_because_it_is_ambiguous():
    problem, _warning = validate_bind("localhost")
    assert problem is not None
    assert "not an IP address" in problem


def build(repo_clone, layout, tmp_path, **overrides):
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def test_preconditions_pass_on_a_healthy_setup(repo_clone, layout, tmp_path, conn):
    config = build(repo_clone, layout, tmp_path)
    problems, _warnings = check_preconditions(config, bind="127.0.0.1", port=0)
    assert problems == []


def test_a_schema_mismatch_refuses_to_serve_and_never_migrates(
    repo_clone, layout, tmp_path, conn
):
    """R11: the daemon owns the schema and the interface follows it.

    Two processes racing to run the same migration is a failure mode worth removing rather
    than surviving, and a version mismatch means the code was upgraded and the daemon has
    not been restarted — worth a clear refusal, not a subtly wrong page.
    """
    conn.execute("PRAGMA user_version = 1")
    config = build(repo_clone, layout, tmp_path)

    problems, _warnings = check_preconditions(config, bind="127.0.0.1", port=0)
    assert any("schema is at version 1" in p for p in problems)
    assert any(str(SCHEMA_VERSION) in p for p in problems)

    # And it did not migrate on the way past.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_every_problem_is_reported_not_only_the_first(repo_clone, layout, tmp_path, conn):
    """Fixing one per restart is a poor experience, which is why config validation
    aggregates and why this does too."""
    conn.execute("PRAGMA user_version = 0")
    config = build(repo_clone, layout, tmp_path)
    problems, _warnings = check_preconditions(config, bind="8.8.8.8", port=0)
    assert len(problems) >= 2
    joined = "\n".join(problems)
    assert "schema" in joined
    assert "globally routable" in joined


def test_a_port_already_in_use_is_reported(repo_clone, layout, tmp_path, conn):
    """"The listening port is already in use" must fail loudly, not silently not listen."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        config = build(repo_clone, layout, tmp_path)
        problems, _warnings = check_preconditions(config, bind="127.0.0.1", port=port)
        assert any(f"cannot bind 127.0.0.1:{port}" in p for p in problems)
    finally:
        holder.close()


def test_the_bind_warning_is_returned_so_the_caller_can_announce_it(
    repo_clone, layout, tmp_path, conn
):
    """SC-015: the effective address is announced on every start, including this one."""
    config = build(repo_clone, layout, tmp_path, web={"bind": "192.168.1.20"})
    problems, warnings = check_preconditions(config, bind=config.web.bind, port=0)
    # Binding a foreign address fails on this machine; the *warning* is what matters here.
    assert any("FULL CONTROL" in w for w in warnings)
    assert not any("globally routable" in p for p in problems)


# -- IPv6 --------------------------------------------------------------------


@pytest.mark.parametrize("address", ["::1", "::"])
def test_an_ipv6_address_that_validates_can_actually_be_bound(
    repo_clone, layout, tmp_path, conn, address
):
    """Validation and binding must agree about what is possible.

    ``TCPServer`` defaults to ``AF_INET``. Without an explicit family an IPv6 literal
    passed every precondition — ``validate_bind`` calls ``::1`` loopback and the probe
    socket already branched on the family — and then failed at the real bind with a
    ``gaierror``, reported as a precondition failure for an address just declared fine.
    """
    from robot_army.web.server import WebApp, build_server

    problem, _warning = validate_bind(address)
    assert problem is None

    config = build(repo_clone, layout, tmp_path)
    problems, _warnings = check_preconditions(config, bind=address, port=0)
    assert problems == [], problems

    server = build_server(WebApp(config), bind=address, port=0)
    try:
        assert server.address_family is socket.AF_INET6
        assert server.server_address[0] in (address, "::1", "::")
    finally:
        server.server_close()


def test_an_ipv4_address_still_binds_in_the_ipv4_family(repo_clone, layout, tmp_path, conn):
    from robot_army.web.server import WebApp, build_server

    config = build(repo_clone, layout, tmp_path)
    server = build_server(WebApp(config), bind="127.0.0.1", port=0)
    try:
        assert server.address_family is socket.AF_INET
    finally:
        server.server_close()


def test_an_ipv6_address_is_bracketed_when_it_is_printed(repo_clone, layout, tmp_path):
    """A URL with a bare IPv6 literal in it is not clickable and not pasteable."""
    from robot_army.web.server import _display_address

    assert _display_address("::1", 8420) == "http://[::1]:8420"
    assert _display_address("127.0.0.1", 8420) == "http://127.0.0.1:8420"
