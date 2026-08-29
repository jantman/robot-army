"""One test per row of the outcomes table in contracts/detection.md.

The rows that matter most are the two failures that are *not* "there is no Spec Kit here":
scaffolding without commands, which is what a partially removed installation leaves behind,
and an unreadable directory, which is the whole of FR-005's promise that detection can never
convert a working dispatch into a failed one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.conftest import make_speckit_tree

from robot_army import speckit


def test_both_halves_present_in_skills_form(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", commands="skills")

    result = speckit.detect(root)

    assert result.detected is True
    assert result.form == "skills"
    assert set(result.commands) == set(speckit.LIFECYCLE)
    assert result.reason == "spec kit present (skills)"


def test_both_halves_present_in_commands_form(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", commands="commands")

    result = speckit.detect(root)

    assert result.detected is True
    assert result.form == "commands"


def test_mixed_forms_are_accepted_and_reported_as_mixed(tmp_path: Path) -> None:
    """A repository mid-migration between the two layouts genuinely has both."""
    root = make_speckit_tree(tmp_path / "repo", commands="mixed")

    result = speckit.detect(root)

    assert result.detected is True
    assert result.form == "mixed"


def test_scaffolding_without_commands_is_not_detected(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", commands=None)

    result = speckit.detect(root)

    assert result.detected is False
    assert result.scaffolding is True
    assert "lifecycle commands missing" in result.reason


def test_a_partial_command_set_names_which_are_missing(tmp_path: Path) -> None:
    """Naming them is the difference between a log line and a diagnosis."""
    root = make_speckit_tree(tmp_path / "repo", commands="partial")

    result = speckit.detect(root)

    assert result.detected is False
    assert "tasks" in result.reason
    assert "implement" in result.reason
    assert "specify" not in result.reason


def test_commands_without_scaffolding_is_not_detected(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", scaffolding=False, commands="skills")

    result = speckit.detect(root)

    assert result.detected is False
    assert result.scaffolding is False
    assert "no spec kit scaffolding" in result.reason


def test_scaffolding_directory_without_the_template_is_not_detected(tmp_path: Path) -> None:
    """``.specify/`` alone is what a half-removed installation leaves behind."""
    root = tmp_path / "repo"
    (root / ".specify").mkdir(parents=True)
    for name in speckit.LIFECYCLE:
        skill = root / ".claude" / "skills" / f"speckit-{name}" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("x", encoding="utf-8")

    assert speckit.detect(root).detected is False


def test_a_specify_that_is_a_file_is_not_detected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".specify").write_text("not a directory", encoding="utf-8")

    result = speckit.detect(root)

    assert result.detected is False
    assert "no spec kit scaffolding" in result.reason


def test_a_nonexistent_path_is_a_miss_not_an_error(tmp_path: Path) -> None:
    result = speckit.detect(tmp_path / "nowhere")

    assert result.detected is False
    assert result.reason


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unreadable_directory_is_a_miss_not_an_error(tmp_path: Path) -> None:
    """FR-005: nothing here may propagate an exception into a dispatch."""
    root = make_speckit_tree(tmp_path / "repo")
    os.chmod(root / ".specify", 0o000)
    try:
        result = speckit.detect(root)
    finally:
        os.chmod(root / ".specify", 0o755)  # noqa: S103 - restoring the fixture

    # Either outcome is acceptable — what is not acceptable is raising. The scaffolding
    # check itself may still succeed on some filesystems, so this asserts the contract
    # rather than a particular verdict.
    assert isinstance(result, speckit.Detection)
