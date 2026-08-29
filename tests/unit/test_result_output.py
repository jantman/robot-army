"""``Result.flush_to``: the primitive milestone 011 is built on.

Four assertions, and the second and third are the ones that matter. *Flushed, not merely
written* is what separates a real fix from one that looks right on a terminal and loses the
screen under redirection. *Cleared* is what makes "printed exactly once" structural — the
five exit paths through ``onboard`` each splice ``result.lines`` into their return, so a
flush that left the lines in place would double every screen it wrote.
"""

from __future__ import annotations

import io
from pathlib import Path

from robot_army.operations import Result


def test_flush_to_writes_what_was_said():
    result = Result().say("first").say("second")
    stream = io.StringIO()

    result.flush_to(stream)

    assert stream.getvalue() == "first\nsecond\n"


def test_flush_to_actually_flushes_rather_than_buffering(tmp_path: Path):
    """FR-005. Read back through a *separate* handle, because the writer's own buffer
    would report the line as present whether or not it reached the file."""
    path = tmp_path / "screen.out"
    result = Result().say("clone path   : /somewhere")

    with path.open("w", encoding="utf-8") as handle:
        result.flush_to(handle)
        # Still inside the `with`: nothing has closed the file, so anything visible here
        # got there by the flush and not by cleanup.
        assert path.read_text(encoding="utf-8") == "clone path   : /somewhere\n"


def test_flush_to_forgets_what_it_wrote_so_render_cannot_repeat_it():
    """FR-006, and the reason this method clears rather than only writing."""
    result = Result().say("clone path   : /somewhere")
    stream = io.StringIO()

    result.flush_to(stream)

    assert result.lines == []
    assert result.render(as_json=False) == ""
    assert stream.getvalue().count("clone path") == 1


def test_a_second_flush_writes_only_what_was_said_since_the_first():
    """The outcome line follows the screen; it does not re-carry it."""
    result = Result().say("screen")
    stream = io.StringIO()
    result.flush_to(stream)

    result.say("outcome")
    result.flush_to(stream)

    assert stream.getvalue() == "screen\noutcome\n"


def test_a_none_stream_writes_nothing_and_keeps_the_lines():
    """The pre-011 behaviour every direct caller still gets, and what ``--json`` passes."""
    result = Result().say("screen")

    result.flush_to(None)

    assert result.lines == ["screen"]
    assert result.render(as_json=False) == "screen"


def test_flushing_nothing_writes_nothing():
    """A run with no lines yet must not emit a bare newline into a machine's output."""
    stream = io.StringIO()

    Result().flush_to(stream)

    assert stream.getvalue() == ""


def test_flush_to_returns_the_result_so_it_chains_like_say():
    result = Result().say("screen")
    assert result.flush_to(io.StringIO()) is result
