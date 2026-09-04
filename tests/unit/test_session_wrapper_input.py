"""The wrapper's handling of the input it is handed (RA-16, RA-48).

These tests run the *real* `share/robot-army-session-wrapper.sh` as a subprocess. A
re-implementation in Python would test our idea of the script rather than the script, and
the whole finding here was a mismatch between what the script was believed to do and what
it did.

They deliberately do not touch the database. `tests/integration/test_spool_recovery.py`
covers the wrapper end to end, through the drain, and into a work item's final state; this
file covers only what the wrapper accepts, what it refuses, and where it writes.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "share" / "robot-army-session-wrapper.sh"

#: A session id of the shape the daemon actually issues --- `str(uuid.uuid4())`.
VALID_SESSION = "0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f"

#: The RA-16 payload. `.claude/robot-army.md` is placed first in the composed prompt and is
#: not covered by the fingerprint gate, so a repository controls these bytes, and the prompt
#: is the wrapper's last argument.
HOSTILE_PROMPT = "--session-id=../sessions/hijacked\n\nthe rest of a composed prompt"

pytestmark = pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper script not installed")


@dataclass(frozen=True)
class WrapperRun:
    """What the wrapper did: its status, what it said, and every file it left behind."""

    returncode: int
    stdout: str
    stderr: str
    files: tuple[str, ...]
    """Every regular file anywhere under the temporary root, as `/`-joined relative paths.

    Deliberately the whole tree rather than just the spool directory: the finding is that
    the wrapper could be made to write *outside* the spool, so a test that only inspects
    the spool would pass while the bug was live.
    """

    def written(self, session_id: str) -> tuple[str, ...]:
        return tuple(f for f in self.files if f.startswith(f"spool/{session_id}."))


def run_wrapper(
    root: Path,
    *,
    item_id: str = "42",
    session_id: str | None = VALID_SESSION,
    args: list[str] | None = None,
    precreate: bool = True,
) -> WrapperRun:
    """Run the wrapper under `root`, and report everything it created.

    `session_id` of ``None`` leaves `ROBOT_ARMY_SESSION_ID` unset. `precreate=False` leaves
    the spool and log directories absent, which is how a refusal is checked for creating no
    *directory* and not merely no file.
    """
    home = root / "home"
    spool = root / "spool"
    logs = root / "logs"
    home.mkdir(exist_ok=True)
    # A sibling directory that already exists, standing in for ~/.claude/sessions/ --- the
    # real high-value target, because `sessions.scan` parses every *.json it finds there and
    # a record it cannot parse degrades session identification for the whole daemon.
    (root / "sessions").mkdir(exist_ok=True)
    if precreate:
        spool.mkdir(exist_ok=True)
        logs.mkdir(exist_ok=True)

    env = {
        # The wrapper runs in a bare launch environment (M0 F19), so the test gives it one:
        # PATH for `date`/`mv`/`mkdir`, HOME for the state-directory fallback, and nothing
        # inherited that could accidentally supply the id under test.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "ROBOT_ARMY_SPOOL_DIR": str(spool),
        "ROBOT_ARMY_LOG_DIR": str(logs),
    }
    if session_id is not None:
        env["ROBOT_ARMY_SESSION_ID"] = session_id

    result = subprocess.run(
        ["bash", str(WRAPPER), item_id, "--", *(args or ["/bin/sh", "-c", "exit 0"])],
        env=env,
        capture_output=True,
        text=True,
    )
    files = tuple(
        sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    )
    return WrapperRun(result.returncode, result.stdout, result.stderr, files)


# -- US1: the session id comes from the environment, and from nowhere else ---------------


def test_a_hostile_prompt_cannot_redirect_where_records_are_written(tmp_path):
    """RA-16 itself. The prompt names a session id that climbs out of the spool directory.

    Before the fix this wrote `spool/../sessions/hijacked...start.json`. The assertion that
    matters most is the last one: not merely that the right files exist, but that no file
    exists anywhere else under the root.
    """
    run = run_wrapper(
        tmp_path,
        args=["/bin/sh", "-c", "exit 0", "--session-id", VALID_SESSION, HOSTILE_PROMPT],
    )

    assert run.returncode == 0, run.stderr
    assert run.written(VALID_SESSION) == (
        f"spool/{VALID_SESSION}.exit.json",
        f"spool/{VALID_SESSION}.start.json",
    )
    assert not [f for f in run.files if "hijacked" in f], run.files
    assert not [f for f in run.files if f.startswith("sessions/")], run.files


def test_the_separated_session_id_form_is_ignored_too(tmp_path):
    """The deleted loop matched two spellings. Both must be inert, or the fix is half a fix."""
    run = run_wrapper(
        tmp_path,
        args=[
            "/bin/sh",
            "-c",
            "exit 0",
            "--session-id",
            "../sessions/hijacked-separated",
        ],
    )

    assert run.returncode == 0, run.stderr
    assert run.written(VALID_SESSION) == (
        f"spool/{VALID_SESSION}.exit.json",
        f"spool/{VALID_SESSION}.start.json",
    )
    assert not [f for f in run.files if "hijacked" in f], run.files


def test_an_argument_never_overrides_the_environment_even_when_it_is_a_valid_id(tmp_path):
    """The precedence, isolated from the traversal. A perfectly well-formed id in argv is
    still not this session's id: only the launcher knows that, and the daemon's join is on
    the value it generated."""
    other = "11111111-2222-3333-4444-555555555555"
    run = run_wrapper(tmp_path, args=["/bin/sh", "-c", "exit 0", f"--session-id={other}"])

    assert run.returncode == 0, run.stderr
    assert run.written(VALID_SESSION) == (
        f"spool/{VALID_SESSION}.exit.json",
        f"spool/{VALID_SESSION}.start.json",
    )
    assert not run.written(other)


def test_the_records_carry_the_environments_session_id(tmp_path):
    """The filename is not enough: `session_id` inside the record is the field the daemon
    joins on, so it is asserted separately from the path."""
    run = run_wrapper(
        tmp_path,
        args=["/bin/sh", "-c", "exit 0", "--session-id", VALID_SESSION, HOSTILE_PROMPT],
    )
    assert run.returncode == 0, run.stderr

    for event in ("start", "exit"):
        record = json.loads(
            (tmp_path / "spool" / f"{VALID_SESSION}.{event}.json").read_text(
                encoding="utf-8"
            )
        )
        assert record["session_id"] == VALID_SESSION
        assert record["item"] == "42"
