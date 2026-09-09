"""The single-instance lock (T128, FR-070).

``flock`` is used rather than a PID file because the kernel releases it when the process
dies **by any means, including SIGKILL** — a promise a PID file cannot make, and the one
that matters after an unclean shutdown.
"""

from __future__ import annotations

import errno
import fcntl
import os
import subprocess
import sys
import textwrap

import pytest

from robot_army.daemon import (
    LockHeld,
    SingleInstanceLock,
    is_locked,
    observe_lock,
    read_lock_holder,
)
from robot_army.health import LockState


def test_the_lock_records_the_holding_pid(layout):
    with SingleInstanceLock(layout.lock_path):
        assert read_lock_holder(layout.lock_path) == str(os.getpid())


def test_a_second_instance_in_another_process_is_refused_naming_the_holder(layout, tmp_path):
    """Two locks in one process would both succeed — ``flock`` is per open file
    description, not per process pair — so the contention must be tested across a real
    process boundary or it is not tested at all."""
    ready = tmp_path / "ready"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import pathlib, sys, time
                sys.path.insert(0, {str(tmp_path.parents[3] / "src")!r})
                from robot_army.daemon import SingleInstanceLock
                with SingleInstanceLock(pathlib.Path({str(layout.lock_path)!r})):
                    pathlib.Path({str(ready)!r}).write_text("up")
                    time.sleep(30)
            """),
        ],
        env={**os.environ, "PYTHONPATH": str(_src_dir())},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(200):
            if ready.exists():
                break
            import time

            time.sleep(0.05)
        assert ready.exists(), "the holding process never acquired the lock"

        assert is_locked(layout.lock_path) is True
        with pytest.raises(LockHeld) as caught:
            SingleInstanceLock(layout.lock_path).acquire()
        assert str(holder.pid) in str(caught.value)
        assert "Only one instance" in str(caught.value)
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_the_lock_is_released_when_the_holder_is_sigkilled(layout, tmp_path):
    """The property a PID file cannot promise."""
    ready = tmp_path / "ready"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(f"""
                import pathlib, time
                from robot_army.daemon import SingleInstanceLock
                lock = SingleInstanceLock(pathlib.Path({str(layout.lock_path)!r}))
                lock.acquire()
                pathlib.Path({str(ready)!r}).write_text("up")
                time.sleep(30)
            """),
        ],
        env={**os.environ, "PYTHONPATH": str(_src_dir())},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    import time

    for _ in range(200):
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists(), holder.communicate()[1].decode()

    assert is_locked(layout.lock_path) is True
    holder.kill()
    holder.wait(timeout=10)

    for _ in range(100):
        if not is_locked(layout.lock_path):
            break
        time.sleep(0.05)
    assert is_locked(layout.lock_path) is False, "SIGKILL must free the lock"

    # And a fresh instance can take it.
    with SingleInstanceLock(layout.lock_path):
        assert read_lock_holder(layout.lock_path) == str(os.getpid())


def test_releasing_lets_another_acquire(layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    lock.release()
    second = SingleInstanceLock(layout.lock_path)
    second.acquire()
    second.release()


def test_releasing_twice_is_harmless(layout):
    lock = SingleInstanceLock(layout.lock_path)
    lock.acquire()
    lock.release()
    lock.release()


def test_is_locked_does_not_leave_the_lock_held(layout):
    """Read-only commands use this to decide whether to delegate; it must not block the
    daemon or leave a lock behind."""
    assert is_locked(layout.lock_path) is False
    assert is_locked(layout.lock_path) is False
    with SingleInstanceLock(layout.lock_path):
        assert is_locked(layout.lock_path) is True


def test_read_lock_holder_on_a_missing_file_is_none(tmp_path):
    assert read_lock_holder(tmp_path / "absent.lock") is None


def _src_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "src"


def test_concurrent_probes_do_not_see_each_other_as_a_running_daemon(layout):
    """``is_locked`` probes with a **shared** lock, and that is the whole point.

    A shared lock still conflicts with the daemon's ``LOCK_EX``, so a running daemon is
    detected exactly as before — but two concurrent probes no longer conflict with *each
    other*. With the exclusive probe this function used to take, each saw the other's
    transient hold and reported "a daemon is running": measured at 1,558 false positives
    in 2,400 probes across six threads, with no daemon running at all.

    Milestone 001 only ever probed from a single-threaded CLI, so the race had no way to
    occur. Milestone 002's web interface serves concurrent requests, and the very first
    page load with several in flight produced a page claiming the daemon was alive while
    it was dead — which is exactly what that milestone's SC-010 forbids.
    """
    import threading

    assert not is_locked(layout.lock_path)
    claims: list[bool] = []
    guard = threading.Lock()

    def probe() -> None:
        for _ in range(300):
            answer = is_locked(layout.lock_path)
            with guard:
                claims.append(answer)

    threads = [threading.Thread(target=probe) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claims) == 1800
    assert not any(claims), (
        f"{sum(claims)}/{len(claims)} concurrent probes invented a running daemon"
    )


def test_the_shared_probe_still_detects_a_real_holder(layout):
    """The fix must not have traded a false positive for a false negative."""
    with SingleInstanceLock(layout.lock_path):
        assert is_locked(layout.lock_path) is True
    assert is_locked(layout.lock_path) is False


# -- the three-valued observation (issue #52) --------------------------------
#
# ``health`` became a caller, and a caller that turns "not held" into "the daemon has died"
# and wakes somebody. What used to be one bool now has to distinguish *nothing is holding
# it* from *I could not look*, and must stop creating the file it was asked about.


def test_observe_lock_reads_the_holder_from_the_probe_itself(layout):
    """One open answers both questions, so the two answers describe one instant.

    Asked separately — as ``web.handle`` and ``_enforced_cap`` used to ask them — they can
    come from either side of a daemon restart, which is the window ``published_cap`` exists
    to close.
    """
    with SingleInstanceLock(layout.lock_path):
        reading = observe_lock(layout.lock_path)
    assert reading.state is LockState.HELD
    assert reading.running is True
    assert reading.holder == str(os.getpid())
    assert reading.path == layout.lock_path


def test_observe_lock_on_a_free_lock_file_names_no_holder(layout):
    """The file still contains the last holder's pid. Reporting it would name a process
    that has exited, which is the mistake this whole feature exists to stop making."""
    with SingleInstanceLock(layout.lock_path):
        pass
    assert layout.lock_path.read_text().strip() == str(os.getpid())

    reading = observe_lock(layout.lock_path)
    assert reading.state is LockState.UNHELD
    assert reading.running is False
    assert reading.holder is None


def test_observe_lock_on_an_absent_file_is_unheld_and_leaves_no_file(tmp_path):
    """Nothing holds a lock that does not exist — and asking must not create one.

    The probe used to open with ``O_CREAT``, so every ``robot-army health`` run left an
    empty file in the state directory as a side effect of reading it. FR-012.
    """
    absent = tmp_path / "nothing" / "daemon.lock"

    reading = observe_lock(absent)

    assert reading.state is LockState.UNHELD
    assert reading.holder is None
    assert not absent.exists()
    assert not absent.parent.exists()


def test_a_lock_that_cannot_be_opened_is_unknown_not_unheld(tmp_path, monkeypatch):
    """A permission error must not manufacture a death notice.

    Patched rather than produced with ``chmod 000`` because a test that silently passes as
    root is worse than no test: root opens the file regardless and the assertion would be
    checking nothing.
    """

    def refuse(*args: object, **kwargs: object) -> int:
        raise PermissionError(errno.EACCES, "nope")

    monkeypatch.setattr(os, "open", refuse)

    reading = observe_lock(tmp_path / "daemon.lock")

    assert reading.state is LockState.UNKNOWN
    assert reading.running is False
    assert reading.holder is None


def test_a_flock_failure_that_is_not_contention_is_unknown(layout, monkeypatch):
    """``EACCES``/``EAGAIN`` from ``flock`` is a holder; anything else is ignorance.

    ``ENOLCK`` — a filesystem that will not lock, a kernel table full — tells us nothing
    about whether a daemon is there, and the one thing this function may not do is answer
    as though it did.
    """
    layout.lock_path.parent.mkdir(parents=True, exist_ok=True)
    layout.lock_path.write_text("1234\n")

    def refuse(fd: int, operation: int) -> None:
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(fcntl, "flock", refuse)

    assert observe_lock(layout.lock_path).state is LockState.UNKNOWN


def test_is_locked_is_the_bool_half_and_answers_as_it_always_did(layout, monkeypatch):
    """Every existing caller wants a yes or no, and an unobservable lock is still ``False``
    to them — the answer this function gave before it had a third value to give."""
    assert is_locked(layout.lock_path) is False
    with SingleInstanceLock(layout.lock_path):
        assert is_locked(layout.lock_path) is True

    def refuse(*args: object, **kwargs: object) -> int:
        raise PermissionError(errno.EACCES, "nope")

    monkeypatch.setattr(os, "open", refuse)
    assert is_locked(layout.lock_path) is False
