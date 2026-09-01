"""`[speckit] enabled`, the per-repository override, and the provenance both callers need.

The provenance assertions are the point of most of these. Two places have to say *which*
setting suppressed a dispatch — the audit record and the repositories listing — and the only
way to keep them agreeing is for one function to answer both.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.conftest import config_dict, monkey_token

from robot_army.config import ConfigError, parse


def build(repo_clone: Path, layout: Any, tmp_path: Path, **overrides: Any) -> Any:
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def test_absent_section_means_enabled(repo_clone: Path, layout: Any, tmp_path: Path) -> None:
    """An installation that says nothing gets the behaviour — spec.md's Q3 decision."""
    config = build(repo_clone, layout, tmp_path)

    assert config.speckit.enabled is True
    assert config.speckit_enabled_for("demo") == (True, None)


def test_global_off_suppresses_and_names_itself(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(repo_clone, layout, tmp_path, speckit={"enabled": False})

    assert config.speckit_enabled_for("demo") == (False, "[speckit] enabled")


def test_per_repo_false_beats_global_true(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "speckit": False}},
    )

    enabled, why = config.speckit_enabled_for("demo")

    assert enabled is False
    assert why == '[repos."demo"] speckit'


def test_per_repo_true_beats_global_false(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """The override is an override in both directions, not merely an off switch."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"enabled": False},
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "speckit": True}},
    )

    enabled, why = config.speckit_enabled_for("demo")

    assert enabled is True
    assert why == '[repos."demo"] speckit'


def test_a_repository_with_no_section_inherits_the_global(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """Milestone 005: a repository needs no section at all, so this must answer for one."""
    config = build(repo_clone, layout, tmp_path, speckit={"enabled": False})

    assert config.speckit_enabled_for("jantman/never-mentioned") == (
        False,
        "[speckit] enabled",
    )


def test_unknown_key_in_the_section_is_an_error(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """Strict, like [trello] and [cleanup]: a typo that looks applied is worse than absent."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, speckit={"enabled": True, "enabeld": False})

    assert any("[speckit] unknown key" in problem for problem in caught.value.problems)


def test_non_boolean_enabled_is_an_error(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, speckit={"enabled": "yes"})

    assert any("must be true or false" in problem for problem in caught.value.problems)


def test_non_boolean_repo_override_is_an_error(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={"demo": {"path": str(repo_clone), "speckit": "no"}},
        )

    assert any("speckit must be true or false" in problem for problem in caught.value.problems)


def test_the_repositories_listing_carries_the_instruction_provenance(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-027 / SC-008: answerable offline, before anything is labelled.

    The table keeps its four values and gains no column — the cell answers "is this
    repository getting the block", which milestone 039 did not change. The provenance rides
    in the ``--json`` payload, from the same resolution call the audit record uses.
    """
    from tests.conftest import make_speckit_tree

    from robot_army import operations

    # Into the real clone fixture: config validates that a repository path is a git
    # repository, so a bare directory of Spec Kit files is refused before the listing runs.
    clone = make_speckit_tree(repo_clone)
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": "open a PR."}},
    )
    ctx = SimpleNamespace(config=config)

    cell, detail = operations._speckit_column(ctx, "demo", clone)

    assert cell == "yes"
    assert detail["instructions"] == {"implement": "[speckit.commands] implement"}


def test_a_suppressed_repository_lists_no_instructions(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """It will be told nothing, so listing what it would have been told would mislead."""
    from tests.conftest import make_speckit_tree

    from robot_army import operations

    clone = make_speckit_tree(repo_clone)
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"enabled": False, "commands": {"implement": "open a PR."}},
    )
    ctx = SimpleNamespace(config=config)

    cell, detail = operations._speckit_column(ctx, "demo", clone)

    assert cell == "off"
    assert "instructions" not in detail
