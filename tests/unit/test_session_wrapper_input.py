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


# -- US2: an identifier the system would never issue is refused, not used ----------------


def test_an_unset_session_id_is_refused(tmp_path):
    """The environment is now the only source, so an unset variable is the whole of the
    missing-id case. Refusing beats guessing: a record under a guessed id would be worse
    than no record, because the daemon would apply it to the wrong session."""
    run = run_wrapper(tmp_path, session_id=None, args=["/bin/sh", "-c", "touch ran"])

    assert run.returncode == 2
    assert "ROBOT_ARMY_SESSION_ID is not set" in run.stderr
    assert run.files == (), "a refusal writes nothing at all"


@pytest.mark.parametrize(
    "session_id",
    [
        pytest.param("../../escape", id="traversal"),
        pytest.param("", id="empty"),
        pytest.param("wrapper-session", id="readable-but-not-an-id"),
        pytest.param("0d5b1f3e9c2a4f1b8e776a1d2c3b4e5f", id="hex-but-undashed"),
        pytest.param("------------------------------------", id="thirty-six-dashes"),
        pytest.param(f"{VALID_SESSION}extra", id="valid-prefix-then-junk"),
    ],
)
def test_a_session_id_the_system_would_never_issue_is_refused(tmp_path, session_id):
    """The daemon issues `str(uuid.uuid4())`, so that is the shape accepted.

    `thirty-six-dashes` is here because it is exactly what the looser character class
    suggested in the issue would have let through --- not dangerous in itself, but the
    difference between a check that admits only real ids and one that merely counts
    characters.
    """
    run = run_wrapper(
        tmp_path, session_id=session_id, args=["/bin/sh", "-c", "touch ran"]
    )

    assert run.returncode == 2
    # Two phrasings, because bash cannot tell an empty variable from an unset one and the
    # message says which case it is. Both name the session id, which is what FR-004 asks of
    # them; neither is allowed to be silent about it.
    assert "session id" in run.stderr or "ROBOT_ARMY_SESSION_ID" in run.stderr
    assert run.files == ()


def test_a_trailing_newline_cannot_smuggle_a_path_past_the_check(tmp_path):
    """In regex dialects where `$` matches before a trailing newline, `<uuid>\\n../x` walks
    straight through a check its author believed anchored --- and the second line is what
    lands in the path. Bash anchors on the whole string; this pins that, because the
    property is invisible at the call site and a rewrite could lose it silently."""
    run = run_wrapper(
        tmp_path,
        session_id=f"{VALID_SESSION}\n../sessions/smuggled",
        args=["/bin/sh", "-c", "touch ran"],
    )

    assert run.returncode == 2
    assert run.files == ()


@pytest.mark.parametrize(
    "item_id",
    [
        pytest.param("../../evil", id="traversal"),
        pytest.param("42/../../evil", id="traversal-after-a-real-id"),
        pytest.param("not-a-number", id="not-an-integer"),
        pytest.param("42\n../evil", id="trailing-newline"),
    ],
)
def test_an_item_id_the_system_would_never_issue_is_refused(tmp_path, item_id):
    """The item id is a SQLite row id and nothing untrusted reaches it today. It is checked
    because it names a path, which is the same class of defect one edit away."""
    run = run_wrapper(tmp_path, item_id=item_id, args=["/bin/sh", "-c", "touch ran"])

    assert run.returncode == 2
    assert "item id" in run.stderr
    assert run.files == ()


def test_a_refusal_creates_no_directories_either(tmp_path):
    """Not merely no *file*. The script used to `mkdir -p` its spool and log directories
    before anything was checked, so a refusal still left a trail; the validation now sits
    above that. Asserted directly, because it is an ordering property and ordering is what
    a later edit silently breaks."""
    run = run_wrapper(
        tmp_path, session_id="../../escape", precreate=False, args=["/bin/sh", "-c", "true"]
    )

    assert run.returncode == 2
    assert not (tmp_path / "spool").exists()
    assert not (tmp_path / "logs").exists()


def test_a_refusal_never_runs_the_worker(tmp_path):
    """The command is refused, not merely unreported. `touch ran` would leave evidence if
    the payload ran, so its absence from the file listing is the assertion."""
    run = run_wrapper(
        tmp_path,
        session_id="../../escape",
        args=["/bin/sh", "-c", f"touch {tmp_path / 'ran'}"],
    )

    assert run.returncode == 2
    assert "ran" not in run.files


# -- US3: control characters do not quarantine a record (RA-48) -------------------------

#: Every control character an argument can actually carry. 0 is absent because a NUL cannot
#: cross the `execve` boundary into a bash string at all, so it is unreachable rather than
#: unhandled.
CONTROL_CHARACTERS = "".join(chr(i) for i in range(1, 32))


def read_record(root, session_id: str, event: str) -> dict:
    """Read a record the way the daemon does --- strictly.

    `strict=True` is Python's default and is the entire point of this test: it rejects raw
    control characters inside a string, which is how an ordinary vertical tab in an issue
    body used to quarantine that session's own exit record.
    """
    text = (root / "spool" / f"{session_id}.{event}.json").read_text(encoding="utf-8")
    return json.loads(text, strict=True)


def test_every_control_character_survives_the_record(tmp_path):
    """All 31, in one argument, rather than a sampled few --- the failure was
    character-specific, so a sample would have been the wrong instrument."""
    payload = f"issue body{CONTROL_CHARACTERS}end"
    run = run_wrapper(tmp_path, args=["/bin/sh", "-c", "exit 0", payload])
    assert run.returncode == 0, run.stderr

    for event in ("start", "exit"):
        record = read_record(tmp_path, VALID_SESSION, event)
        assert payload in record["argv"], (
            "the text must come back byte-for-byte, not merely parse"
        )


def test_the_escaping_already_in_place_is_not_regressed(tmp_path):
    """Quotes, backslashes, the three whitespace escapes and multi-byte UTF-8 all worked
    before. Widening the escaping must not cost any of them --- a doubled backslash or a
    mangled emoji would be a silent corruption of the record rather than a loud failure."""
    payload = 'a "quoted" back\\slash\nnewline\rreturn\ttab émoji → \U0001f680'
    run = run_wrapper(tmp_path, args=["/bin/sh", "-c", "exit 0", payload])
    assert run.returncode == 0, run.stderr

    assert payload in read_record(tmp_path, VALID_SESSION, "exit")["argv"]


def test_a_control_character_in_the_working_directory_is_escaped_too(tmp_path):
    """`cwd` goes through the same escaper as `argv`, and a directory name may legally
    contain a control character. Asserted separately because it is a different field, and a
    fix applied to one string and not the others would pass every test above."""
    odd = tmp_path / "work\x0bdir"
    odd.mkdir()
    result = subprocess.run(
        ["bash", str(WRAPPER), "42", "--", "/bin/sh", "-c", "exit 0"],
        cwd=odd,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "ROBOT_ARMY_SESSION_ID": VALID_SESSION,
            "ROBOT_ARMY_SPOOL_DIR": str(tmp_path / "spool"),
            "ROBOT_ARMY_LOG_DIR": str(tmp_path / "logs"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    assert read_record(tmp_path, VALID_SESSION, "exit")["cwd"] == str(odd)
