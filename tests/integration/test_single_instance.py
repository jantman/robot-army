"""The single-instance lock (T128, FR-070).

``flock`` is used rather than a PID file because the kernel releases it when the process
dies **by any means, including SIGKILL** — a promise a PID file cannot make, and the one
that matters after an unclean shutdown.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from robot_army.daemon import LockHeld, SingleInstanceLock, is_locked, read_lock_holder


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
