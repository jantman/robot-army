"""T097: no code path identifies a process by matching its command line (FR-039).

Not a stylistic rule. M0 recorded two real incidents behind it: a ``pkill -f`` that killed
the invoking shell, and a ``pgrep -f`` that matched kitty's ``run-shell`` wrapper instead
of the intended process and produced a wrong conclusion. ``pgrep -f claude`` returned 18
matches of which 12 were the desktop application.

This is asserted by scanning the source, because the guarantee is about what the code
*cannot* do rather than about what one execution happens to do.

The scan walks the **AST** rather than the raw text. Comments are absent from an AST
entirely and docstrings are identifiable, so prose that *names* the prohibition — which is
exactly what should be kept — cannot trip it, while a string literal handed to a
subprocess call still can.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "robot_army"

#: Every spelling of "find a process by its command line" that could plausibly appear.
FORBIDDEN = (
    re.compile(r"\bpgrep\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\bkillall\b"),
    re.compile(r"cmdline"),
)


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Identity of every string node that is a docstring rather than a value."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def executable_text(path: Path) -> list[str]:
    """Every identifier and non-docstring string literal in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    pieces: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                pieces.append(node.value)
        elif isinstance(node, ast.Name):
            pieces.append(node.id)
        elif isinstance(node, ast.Attribute):
            pieces.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pieces.append(node.name)
    return pieces


def test_the_source_tree_was_actually_found():
    """A scanning test that silently scans nothing is worse than no test."""
    files = source_files()
    assert len(files) > 15, f"only found {len(files)} source files under {SRC}"


def test_the_scanner_would_actually_catch_a_violation(tmp_path):
    """Guards the guard: if ``executable_text`` stopped seeing call arguments, every
    other assertion in this file would pass vacuously."""
    offender = tmp_path / "bad.py"
    offender.write_text(
        '"""A docstring mentioning pgrep is fine."""\n'
        'def stop():\n'
        '    run(["pkill", "-f", "claude"])\n',
        encoding="utf-8",
    )
    pieces = executable_text(offender)
    assert any(re.search(r"\bpkill\b", piece) for piece in pieces)
    assert not any("A docstring mentioning" in piece for piece in pieces)


@pytest.mark.parametrize("pattern", FORBIDDEN, ids=lambda p: p.pattern)
def test_no_module_matches_processes_by_command_line(pattern):
    offenders: list[str] = []
    for path in source_files():
        for piece in executable_text(path):
            if pattern.search(piece):
                offenders.append(f"{path.relative_to(SRC)}: {piece!r}")
    assert not offenders, "command-line process matching found:\n" + "\n".join(offenders)


def test_no_module_shells_out_to_ps():
    offenders: list[str] = []
    for path in source_files():
        for piece in executable_text(path):
            if piece == "ps":
                offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"shelling out to ps: {offenders}"


def test_process_identity_comes_from_exe_and_starttime():
    """The permitted mechanism, asserted positively so the prohibition has a counterpart."""
    procinfo = (SRC / "procinfo.py").read_text(encoding="utf-8")
    assert "def exe(" in procinfo
    assert "def starttime(" in procinfo
    assert "def is_alive(" in procinfo


def test_the_prohibition_is_documented_where_a_reader_will_find_it():
    """The rule is counter-intuitive enough that a later edit could undo it in good
    faith. The comment explaining why is part of the guarantee."""
    procinfo = (SRC / "procinfo.py").read_text(encoding="utf-8")
    assert "FR-039" in procinfo
    assert "command line" in procinfo
