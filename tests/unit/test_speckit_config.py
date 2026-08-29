"""`[speckit] enabled`, the per-repository override, and the provenance both callers need.

The provenance assertions are the point of most of these. Two places have to say *which*
setting suppressed a dispatch — the audit record and the repositories listing — and the only
way to keep them agreeing is for one function to answer both.
"""

from __future__ import annotations

from pathlib import Path
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
