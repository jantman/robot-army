"""Which candidate paths ``KittyDisplay.probe`` may speak to, and which it must not.

Socket discovery expands a glob, and a glob rooted anywhere another local user can write
returns names that user chose. The rule under test is that a name is not evidence: before
anything is run against a candidate it must be a socket, owned by this user, under
directories no stranger can rearrange. A refused candidate is never addressed at all —
not with the probe, and therefore not with a launch carrying the composed prompt.

Two cases here fail loudly if the implementation drifts, and both are deliberate:

* the **symlink** case fails if ``stat`` is used instead of ``lstat``. ``stat`` follows a
  link another user created to the genuine socket, reports *our* uid, and returns "owned
  by me" for a name the attacker controls.
* the **1777** case fails if world-writable directories are refused outright. ``/tmp`` is
  world-writable *and* sticky, which is exactly the property that stops a stranger
  swapping an entry — so the maintainer's shipped setup must keep working.

Real sockets and real directory modes, not mocked ``os.lstat``: a mocked inspection would
pass just as happily against a check that read the wrong field.
"""

from __future__ import annotations

import os
import socket
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from robot_army.audit import read_records
from robot_army.boundaries.kitty import KittyDisplay
from robot_army.subproc import Completed

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config
    from robot_army.paths import Layout


@pytest.fixture
def socket_at() -> Any:
    """Bind a real ``AF_UNIX`` socket and keep it open for the test's duration.

    Kept open because a closed socket's inode survives but a garbage-collected
    ``socket`` object unlinks nothing — holding the reference is what keeps the path a
    socket rather than a name that happened to be one.
    """
    held: list[socket.socket] = []

    def make(path: Path) -> Path:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        sock.listen(1)
        held.append(sock)
        return path

    yield make
    for sock in held:
        sock.close()


def display(config: Config, audit: AuditLog, pattern: str) -> KittyDisplay:
    """A display whose discovery looks only at ``pattern``."""
    return KittyDisplay(replace(config, terminal=replace(config.terminal, socket_glob=pattern)), audit)


def answering(*, ok_for: set[str] | None = None) -> Any:
    """A stand-in for ``subproc.run`` that records every target it was asked about.

    ``ok_for`` names the targets that answer; everything else exits non-zero, which is
    what a dead socket does. The recorded list is the assertion that matters: a refused
    candidate must not appear in it at all.
    """
    asked: list[str] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Completed:
        target = argv[argv.index("--to") + 1]
        asked.append(target)
        answers = ok_for is None or target in ok_for
        return Completed(
            argv=tuple(argv),
            returncode=0 if answers else 1,
            stdout="[]" if answers else "",
            stderr="",
            duration=0.001,
        )

    fake_run.asked = asked  # type: ignore[attr-defined]
    return fake_run


def unowned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every real file look like somebody else's, without a second account."""
    real = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real + 1)


def probe_records(layout: Layout) -> list[dict[str, Any]]:
    return [r for r, _ in read_records(layout.log_dir) if r and r["action"] == "kitty.probe"]


# -- the seam ---------------------------------------------------------------


def test_a_refused_candidate_is_never_run_against(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: refusal happens *before* anything is addressed to the candidate."""
    (tmp_path / "sock-b").write_text("not a socket")
    socket_at(tmp_path / "sock-a")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    found = display(config, audit, str(tmp_path / "sock-*")).probe()

    assert found == f"unix:{tmp_path}/sock-a"
    assert fake.asked == [f"unix:{tmp_path}/sock-a"]


def test_discovery_continues_past_a_refusal(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused candidate is skipped, not fatal — the next one still gets its chance."""
    (tmp_path / "sock-z").mkdir()
    socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    assert display(config, audit, str(tmp_path / "sock-*")).probe() == f"unix:{tmp_path}/sock-a"


def test_the_probe_record_carries_both_what_was_tried_and_what_was_refused(
    config: Config,
    audit: AuditLog,
    layout: Layout,
    tmp_path: Path,
    socket_at: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Principle III: one record, from which the whole discovery can be reconstructed."""
    (tmp_path / "sock-z").write_text("planted")
    socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    display(config, audit, str(tmp_path / "sock-*")).probe()
    audit.close()

    record = probe_records(layout)[-1]
    assert record["outcome"] == "ok"
    assert [t["socket"] for t in record["detail"]["tried"]] == [f"unix:{tmp_path}/sock-a"]
    assert [r["socket"] for r in record["detail"]["refused"]] == [f"unix:{tmp_path}/sock-z"]
    assert record["detail"]["refused"][0]["reason"]


def test_refusals_are_readable_after_a_failed_discovery(
    config: Config, audit: AuditLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing acceptable is a different fact from nothing at all, and both are held."""
    (tmp_path / "sock-z").write_text("planted")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    shown = display(config, audit, str(tmp_path / "sock-*"))
    assert shown.probe() is None
    assert [r["socket"] for r in shown.refusals] == [f"unix:{tmp_path}/sock-z"]

    empty = display(config, audit, str(tmp_path / "nothing-*"))
    assert empty.probe() is None
    assert empty.refusals == ()


# -- US1: it must be a socket, and it must be ours ---------------------------


def test_a_real_socket_owned_by_us_is_accepted(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    assert display(config, audit, str(tmp_path / "sock-*")).probe() == f"unix:{tmp_path}/sock-a"


@pytest.mark.parametrize("shape", ["file", "directory"])
def test_a_candidate_that_is_not_a_socket_is_refused(
    shape: str,
    config: Config,
    audit: AuditLog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cheapest impersonation: create *anything* with a name that sorts first."""
    if shape == "file":
        (tmp_path / "sock-a").write_text("hello")
    else:
        (tmp_path / "sock-a").mkdir()
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(tmp_path / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"] == "not a socket"


def test_a_symlink_to_the_genuine_socket_is_refused(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lstat``, not ``stat``.

    This is the case that decides which call the implementation makes. A stranger can
    create a *link* to my socket; following it reports my own uid and my own socket type
    for a name they control. Inspecting the link itself reports theirs, and a link is
    not a socket either way.
    """
    socket_at(tmp_path / "sock-a")
    (tmp_path / "sock-z").symlink_to(tmp_path / "sock-a")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(tmp_path / "sock-*"))

    assert shown.probe() == f"unix:{tmp_path}/sock-a"
    assert fake.asked == [f"unix:{tmp_path}/sock-a"]
    assert [(r["socket"], r["reason"]) for r in shown.refusals] == [
        (f"unix:{tmp_path}/sock-z", "not a socket")
    ]


def test_a_candidate_that_vanishes_before_inspection_is_refused(
    config: Config, audit: AuditLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable is never a pass. The glob is a snapshot; the file need not survive it."""
    monkeypatch.setattr("robot_army.boundaries.kitty.glob.glob", lambda _p: [str(tmp_path / "gone")])
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(tmp_path / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"].startswith("cannot be inspected")


def test_a_socket_owned_by_another_user_is_refused(
    config: Config,
    audit: AuditLog,
    tmp_path: Path,
    socket_at: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proved by moving the comparison's other side; the suite runs as one user."""
    socket_at(tmp_path / "sock-a")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)
    unowned(monkeypatch)

    shown = display(config, audit, str(tmp_path / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"] == f"owned by uid {os.stat(tmp_path / 'sock-a').st_uid}"


def test_the_impostor_that_sorts_first_receives_nothing(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RA-15, in one test.

    Candidates are tried in reverse lexicographic order, so a stranger picks a name that
    sorts ahead of ``mykitty-<pid>`` and is probed first. The rule is what makes the
    ordering stop mattering.
    """
    (tmp_path / "mykitty-zzzzzz").write_text("I am kitty, honest")
    socket_at(tmp_path / "mykitty-988978")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(tmp_path / "mykitty-*"))

    assert shown.probe() == f"unix:{tmp_path}/mykitty-988978"
    assert fake.asked == [f"unix:{tmp_path}/mykitty-988978"]
    assert [r["socket"] for r in shown.refusals] == [f"unix:{tmp_path}/mykitty-zzzzzz"]


# -- US2: and the directories above it must be nobody else's to rearrange ----


def test_an_owned_socket_in_a_private_directory_is_accepted(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "private"
    home.mkdir()
    home.chmod(0o700)
    socket_at(home / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    assert display(config, audit, str(home / "sock-*")).probe() == f"unix:{home}/sock-a"


def test_an_owned_socket_in_a_world_writable_directory_is_refused(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ours at the moment we looked is not ours at the moment we connect.

    Without the sticky bit anyone may unlink the entry and bind their own in its place,
    which turns the ownership check into a check with a window after it. The reason names
    the directory, because the directory is what has to change.
    """
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)  # chmod, not mkdir(mode=...): the umask would take the bits back
    socket_at(shared / "sock-a")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(shared / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"] == (
        f"directory {shared} is writable by others without the sticky bit"
    )


def test_an_owned_socket_in_a_sticky_world_writable_directory_is_accepted(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``/tmp`` shape, and the reason the shipped setup is not broken by its own fix.

    The sticky bit restricts unlinking and renaming to the entry's owner, which is exactly
    the property the previous test is missing. A rule that refused world-writable
    directories outright would refuse ``/tmp/mykitty-*`` — the maintainer's running
    configuration — and a security fix that stops the daemon starting is a fix that gets
    reverted.
    """
    sticky = tmp_path / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    socket_at(sticky / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    assert display(config, audit, str(sticky / "sock-*")).probe() == f"unix:{sticky}/sock-a"


def test_a_directory_owned_by_someone_else_is_refused(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An owner can always replace what is inside their own directory, whatever the mode."""
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    theirs.chmod(0o755)
    socket_at(theirs / "sock-a")
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)
    real_lstat, real_uid = os.lstat, os.getuid
    monkeypatch.setattr(
        os,
        "lstat",
        lambda p: _Owned(real_lstat(p), real_uid() + 1) if str(p) == str(theirs) else real_lstat(p),
    )

    shown = display(config, audit, str(theirs / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"] == f"directory {theirs} is owned by uid {real_uid() + 1}"


def test_a_deeply_nested_private_directory_is_still_accepted(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk ends at the root, so the root and everything under it must not refuse.

    Regression against a walk that refuses everything — which would pass every test above
    for entirely the wrong reason.
    """
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    socket_at(deep / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    assert display(config, audit, str(deep / "sock-*")).probe() == f"unix:{deep}/sock-a"


class _Owned:
    """One ``stat_result`` with a different ``st_uid``, to stand in for another user."""

    def __init__(self, info: os.stat_result, uid: int) -> None:
        self._info = info
        self.st_uid = uid

    def __getattr__(self, name: str) -> Any:
        return getattr(self._info, name)


# -- US4: the three failures are three different sentences -------------------


def require(config: Config, audit: AuditLog, pattern: str) -> str:
    """The message every launch failure and ``attach`` quotes."""
    from robot_army.boundaries import BoundaryError

    with pytest.raises(BoundaryError) as caught:
        display(config, audit, pattern)._require_socket()
    return str(caught.value)


def test_the_error_when_nothing_matched_is_unchanged(
    config: Config, audit: AuditLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    message = require(config, audit, str(tmp_path / "nothing-*"))
    assert message.endswith("is kitty running with allow_remote_control and listen_on set?")


def test_the_error_when_a_candidate_was_refused_says_so(
    config: Config, audit: AuditLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Nothing is running" and "something is answering for kitty" are opposite errands."""
    (tmp_path / "sock-z").write_text("planted")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    message = require(config, audit, str(tmp_path / "sock-*"))

    assert "1 candidate(s) were found and refused" in message
    assert f"unix:{tmp_path}/sock-z (not a socket)" in message


def test_the_error_when_a_candidate_answered_is_not_raised(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    assert display(config, audit, str(tmp_path / "sock-*"))._require_socket() == (
        f"unix:{tmp_path}/sock-a"
    )


def doctor_socket_check(config: Config, audit: AuditLog, conn: Any, pattern: str) -> str:
    from tests.conftest import make_boundaries

    from robot_army import operations
    from robot_army.effects import EffectLevel

    ctx = operations.Context(
        config=replace(config, terminal=replace(config.terminal, socket_glob=pattern)),
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )
    check = next(c for c in operations.doctor(ctx).data["checks"] if c["name"] == "terminal socket")
    return str(check["detail"])


def test_doctor_names_the_refusal_rather_than_reporting_an_absence(
    config: Config,
    audit: AuditLog,
    conn: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before RA-15 was fixed, `doctor` reported the impostor's socket as healthy.

    Reporting it as "nothing answered" instead would be the second-worst answer: the
    maintainer goes to restart a terminal that is already running.
    """
    (tmp_path / "sock-z").write_text("planted")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())

    detail = doctor_socket_check(config, audit, conn, str(tmp_path / "sock-*"))

    assert "were found and refused" in detail
    assert "not a socket" in detail


def test_doctor_is_unchanged_when_nothing_matched(
    config: Config,
    audit: AuditLog,
    conn: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    detail = doctor_socket_check(config, audit, conn, str(tmp_path / "nothing-*"))
    assert detail == f"nothing answered '{tmp_path}/nothing-*'"


def test_the_daemon_will_not_start_and_says_what_it_refused(
    config: Config,
    audit: AuditLog,
    conn: Any,
    layout: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The startup check makes the same distinction, against the real display.

    A daemon that refuses to start is the right outcome — the socket it would dispatch
    into is not the maintainer's — but "no socket" and "a socket I would not use" are
    different problems and get different words.
    """
    from tests.conftest import make_boundaries

    from robot_army.daemon import check_preconditions

    (tmp_path / "sock-z").write_text("planted")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    pattern = str(tmp_path / "sock-*")

    problems = check_preconditions(
        config=replace(config, terminal=replace(config.terminal, socket_glob=pattern)),
        layout=layout,
        boundaries=make_boundaries(audit, display=display(config, audit, pattern)),
        conn=conn,
    )

    problem = next(p for p in problems if "no terminal control socket answered" in p)
    assert "were found and refused" in problem
    assert "not a socket" in problem


def test_a_symlinked_directory_is_refused_by_name(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal says "symbolic link", not something untrue about its permissions.

    A symlink's own mode is 0777 on Linux, so the mode clause would refuse it anyway —
    with a reason that reads as though the maintainer had left a directory world-writable.
    A wrong explanation is worse than a terse one: it sends them to fix the wrong thing.
    """
    real = tmp_path / "real"
    real.mkdir()
    socket_at(real / "sock-a")
    (tmp_path / "linked").symlink_to(real)
    fake = answering()
    monkeypatch.setattr("robot_army.boundaries.kitty.run", fake)

    shown = display(config, audit, str(tmp_path / "linked" / "sock-*"))

    assert shown.probe() is None
    assert fake.asked == []
    assert shown.refusals[0]["reason"] == f"directory {tmp_path}/linked is a symbolic link"


# -- the cached socket is a cached path, not cached trust ---------------------


def test_a_cached_socket_that_stops_being_ours_is_refused(
    config: Config,
    audit: AuditLog,
    layout: Any,
    tmp_path: Path,
    socket_at: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking once at discovery would leave the finding half-closed.

    The sticky bit stops a stranger unlinking kitty's socket. It does not stop them
    claiming the path once kitty exits and frees it itself. A daemon that outlives a
    kitty restart would otherwise keep dispatching down a name it checked only while it
    was still ours — the whole composed prompt, to whoever got there first.
    """
    held = socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    shown = display(config, audit, str(tmp_path / "sock-*"))
    assert shown.probe() == f"unix:{tmp_path}/sock-a"

    # kitty exits, unlinking its socket; somebody else takes the vacant name.
    held.unlink()
    (tmp_path / "sock-a").write_text("mine now")

    assert shown.probe() is None
    assert shown.refusals == ({"socket": f"unix:{tmp_path}/sock-a", "reason": "not a socket"},)


def test_a_cached_socket_that_is_still_ours_is_not_rediscovered(
    config: Config, audit: AuditLog, tmp_path: Path, socket_at: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-checking is not re-globbing: a candidate that appears later is still ignored."""
    socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    shown = display(config, audit, str(tmp_path / "sock-*"))
    assert shown.probe() == f"unix:{tmp_path}/sock-a"

    socket_at(tmp_path / "sock-z")  # sorts first; would win a fresh discovery

    assert shown.probe() == f"unix:{tmp_path}/sock-a"


def test_a_refused_cache_keeps_failing_rather_than_silently_recovering(
    config: Config,
    audit: AuditLog,
    layout: Any,
    tmp_path: Path,
    socket_at: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loud, repeatable failure is the documented answer to a terminal that went away.

    Re-discovering here would hide a kitty that died and came back — and would hand the
    daemon whatever now sorts first, which is the attack.
    """
    held = socket_at(tmp_path / "sock-a")
    monkeypatch.setattr("robot_army.boundaries.kitty.run", answering())
    shown = display(config, audit, str(tmp_path / "sock-*"))
    shown.probe()
    held.unlink()
    socket_at(tmp_path / "sock-z")  # a perfectly good socket, which must NOT be adopted

    assert shown.probe() is None
    assert shown.probe() is None
    assert shown.refusals[0]["reason"].startswith("cannot be inspected")

    audit.close()
    record = probe_records(layout)[-1]
    assert record["detail"]["error"] == "the cached socket is no longer usable"
