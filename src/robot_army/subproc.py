"""One bounded, audited way to run an external command.

Not in the plan's module list, and worth justifying: every boundary that shells out
(git, hooks, kitty, dtach, systemctl) has the same three obligations — an explicit
timeout, killing the process **group** rather than the direct child, and an audit record
carrying argv, exit code, duration and captured output on failure. Writing that five
times would guarantee four of them drift.

The process-group kill is the part that is easy to get wrong and expensive to get wrong.
M0 F15: a shell command that spawned ``git`` left the grandchild running when only the
direct child was killed, so the timeout appeared to work while the real work continued.
``start_new_session=True`` puts the child in its own process group and
``os.killpg`` takes the whole tree.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from robot_army.audit import AuditLog

#: How long to wait for a SIGTERM'd group to die before escalating to SIGKILL.
GRACE_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Completed:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined output, for the places that report a failure to a human."""
        parts = [p for p in (self.stdout.strip(), self.stderr.strip()) if p]
        return "\n".join(parts)


class SubprocessTimeout(Exception):
    def __init__(self, argv: list[str], timeout: float, output: str) -> None:
        super().__init__(f"timed out after {timeout}s: {' '.join(argv)}")
        self.argv = argv
        self.timeout = timeout
        self.output = output


def run(
    argv: list[str],
    *,
    timeout: float,
    audit: AuditLog | None = None,
    action: str = "subprocess",
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    shell: bool = False,
    check: bool = False,
    entity_type: str | None = None,
    entity_id: object = None,
) -> Completed:
    """Run a command with a hard timeout, killing its whole process group on expiry.

    Returns a :class:`Completed` even on non-zero exit; pass ``check=True`` to raise
    instead. A timeout always produces ``timed_out=True`` with whatever output was
    captured before the kill — a failure with no output is unactionable.
    """
    started = time.monotonic()
    full_env = {**os.environ, **env} if env else None
    popen_args: list[str] | str = " ".join(argv) if shell else argv

    proc = subprocess.Popen(  # noqa: S603 - argv is constructed, never user-interpolated
        popen_args,
        cwd=cwd,
        env=full_env,
        shell=shell,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,  # its own process group, so killpg reaches grandchildren
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stdout, stderr = _kill_group(proc)
    duration = time.monotonic() - started

    result = Completed(
        argv=tuple(argv),
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        duration=duration,
        timed_out=timed_out,
    )

    if audit is not None:
        detail: dict[str, object] = {
            "argv": list(argv),
            "cwd": cwd,
            "exit": result.returncode,
            "duration_s": round(duration, 3),
            "timed_out": timed_out,
        }
        if not result.ok:
            # Output is captured on failure only: logging it always would flood the log
            # with successful git plumbing output that adds nothing.
            detail["output"] = result.output[:8000]
        audit.record(
            action,
            outcome="ok" if result.ok else "error",
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )

    if timed_out and check:
        raise SubprocessTimeout(argv, timeout, result.output)
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr
        )
    return result


def _kill_group(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """SIGTERM the group, then SIGKILL what survives. Returns whatever was captured."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None

    if pgid is not None:
        with _ignore_gone():
            os.killpg(pgid, signal.SIGTERM)
        try:
            return proc.communicate(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with _ignore_gone():
                os.killpg(pgid, signal.SIGKILL)
    else:
        proc.kill()
    try:
        return proc.communicate(timeout=GRACE_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover - the group refused SIGKILL
        return "", ""


class _ignore_gone:  # noqa: N801 - a context manager used as a statement modifier
    """Swallow only ``ProcessLookupError``: the process exiting between our check and
    our signal is the expected race, not an error. Nothing else is swallowed."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, ProcessLookupError)
