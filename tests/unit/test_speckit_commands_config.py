"""`[speckit.commands]`, `[repos.*].speckit_commands`, and what resolves from the pair.

Two things here are worth knowing before changing anything.

**The provenance is the point of most of these tests.** Two callers need to say *which*
setting supplied an instruction — the ``speckit.detect`` audit record and ``robot-army repos
--json`` — and the only thing keeping them agreeing is that one function answers both.
``test_speckit_config.py`` makes the same argument for the boolean gate; this is its
extension to four strings.

**Empty means different things in the two places, and that is deliberate.** Globally, an
empty instruction and an absent one are the same state, so an empty one says nothing and is
refused. In a repository section they are different states — absent inherits, empty
overrides with nothing — so empty is accepted there and is the only way to drop one
instruction without ``speckit = false`` removing the entire guidance block. Milestone 039,
research R5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.conftest import config_dict, monkey_token

from robot_army.config import MAX_INSTRUCTION_CHARS, ConfigError, parse


def build(repo_clone: Path, layout: Any, tmp_path: Path, **overrides: Any) -> Any:
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def problems(repo_clone: Path, layout: Any, tmp_path: Path, **overrides: Any) -> list[str]:
    """Parse expecting refusal, and return every problem rather than only the first."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, **overrides)
    return list(caught.value.problems)


# -- the global table --------------------------------------------------------


def test_absent_table_configures_nothing(repo_clone: Path, layout: Any, tmp_path: Path) -> None:
    """An installation that says nothing gets nothing — FR-004, and the whole of US3."""
    config = build(repo_clone, layout, tmp_path)

    assert config.speckit.commands == {}
    assert config.speckit_commands_for("demo") == ()


def test_global_instruction_resolves_with_its_provenance(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": "push the branch and open a PR."}},
    )

    assert config.speckit.commands == {"implement": "push the branch and open a PR."}
    resolved = config.speckit_commands_for("demo")

    assert len(resolved) == 1
    assert resolved[0].command == "implement"
    assert resolved[0].text == "push the branch and open a PR."
    assert resolved[0].source == "[speckit.commands] implement"


def test_all_four_commands_are_configurable(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={
            "commands": {
                "specify": "commit the spec.",
                "plan": "commit the plan.",
                "tasks": "commit the tasks.",
                "implement": "push and open a PR.",
            }
        },
    )

    assert [i.command for i in config.speckit_commands_for("demo")] == [
        "specify",
        "plan",
        "tasks",
        "implement",
    ]


def test_a_repository_with_no_section_gets_the_global_instructions(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-023. Milestone 005's rule: a section is for exceptions, not registration."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": "push it."}},
    )

    resolved = config.speckit_commands_for("never/mentioned")

    assert [(i.command, i.text) for i in resolved] == [("implement", "push it.")]


# -- US2: all four commands, ordered, independently omissible ------------------


def test_file_order_does_not_decide_render_order(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-011. Resolution sorts, so no consumer has to remember to."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={
            "commands": {
                "implement": "d",
                "tasks": "c",
                "plan": "b",
                "specify": "a",
            }
        },
    )

    assert [i.command for i in config.speckit_commands_for("demo")] == [
        "specify",
        "plan",
        "tasks",
        "implement",
    ]


def test_a_subset_resolves_to_exactly_that_subset(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"plan": "commit the plan.", "implement": "push it."}},
    )

    assert [i.command for i in config.speckit_commands_for("demo")] == ["plan", "implement"]


# -- US3: every malformed shape is refused, and refused out loud ---------------


def test_commands_must_be_a_table(repo_clone: Path, layout: Any, tmp_path: Path) -> None:
    found = problems(repo_clone, layout, tmp_path, speckit={"commands": "implement"})

    assert "[speckit] commands must be a table" in found


def test_an_unknown_command_name_is_refused(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """A typo here is an instruction that quietly never reaches a session."""
    found = problems(
        repo_clone, layout, tmp_path, speckit={"commands": {"implment": "push it."}}
    )

    assert any("unknown command 'implment'" in p for p in found)
    assert any("specify, plan, tasks, implement" in p for p in found)


def test_a_non_string_value_is_refused(repo_clone: Path, layout: Any, tmp_path: Path) -> None:
    found = problems(repo_clone, layout, tmp_path, speckit={"commands": {"implement": 42}})

    assert any("[speckit.commands] implement must be a string" in p for p in found)


def test_an_empty_global_instruction_is_refused(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """Research R5: globally, empty and absent are the same state, so empty is a mistake."""
    found = problems(repo_clone, layout, tmp_path, speckit={"commands": {"implement": ""}})

    assert any("[speckit.commands] implement is empty" in p for p in found)


def test_a_whitespace_only_global_instruction_is_refused(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    found = problems(
        repo_clone, layout, tmp_path, speckit={"commands": {"implement": "   \n  "}}
    )

    assert any("[speckit.commands] implement is empty" in p for p in found)


def test_an_over_long_instruction_is_refused_naming_both_numbers(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    too_long = "x" * (MAX_INSTRUCTION_CHARS + 1)
    found = problems(
        repo_clone, layout, tmp_path, speckit={"commands": {"implement": too_long}}
    )

    assert any(
        f"is {MAX_INSTRUCTION_CHARS + 1} characters; the limit is {MAX_INSTRUCTION_CHARS}" in p
        for p in found
    )


def test_an_instruction_at_exactly_the_limit_is_accepted(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """The boundary is inclusive, which is the half of it a test usually forgets."""
    at_limit = "x" * MAX_INSTRUCTION_CHARS
    config = build(repo_clone, layout, tmp_path, speckit={"commands": {"implement": at_limit}})

    assert config.speckit_commands_for("demo")[0].text == at_limit


def test_three_mistakes_are_reported_together(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-006. Aggregate reporting, not an abort at the first."""
    found = problems(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": 42, "specify": "", "plna": "typo"}},
    )
    mine = [p for p in found if "speckit" in p]

    assert len(mine) == 3


def test_a_mistyped_table_name_is_caught_by_the_existing_strict_check(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """No new machinery: `[speckit]` was already strict about unknown keys."""
    found = problems(repo_clone, layout, tmp_path, speckit={"command": {"implement": "x"}})

    assert any("[speckit] unknown key 'command'" in p for p in found)


# -- US4: one repository needs different instructions -------------------------


def section(repo_clone: Path, **extra: Any) -> dict[str, Any]:
    return {"demo": {"path": str(repo_clone), "base_branch": "main", **extra}}


def resolve(config: Any, key: str = "demo") -> dict[str, tuple[str, str]]:
    return {i.command: (i.text, i.source) for i in config.speckit_commands_for(key)}


def test_override_replaces_the_global_for_that_command_only(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"specify": "commit the spec.", "implement": "open a PR."}},
        repos=section(repo_clone, speckit_commands={"implement": "do not open a PR here."}),
    )

    assert resolve(config) == {
        "specify": ("commit the spec.", "[speckit.commands] specify"),
        "implement": (
            "do not open a PR here.",
            '[repos."demo".speckit_commands] implement',
        ),
    }


def test_an_override_with_no_global_still_applies(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    config = build(
        repo_clone,
        layout,
        tmp_path,
        repos=section(repo_clone, speckit_commands={"plan": "commit the plan."}),
    )

    assert resolve(config) == {
        "plan": ("commit the plan.", '[repos."demo".speckit_commands] plan')
    }


def test_an_empty_override_clears_the_global_here_and_nowhere_else(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-025, research R5 — the whole reason empty is legal in this table."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": "open a PR.", "plan": "commit the plan."}},
        repos=section(repo_clone, speckit_commands={"implement": ""}),
    )

    assert set(resolve(config)) == {"plan"}
    # Every other repository is untouched.
    assert set(resolve(config, "someone/else")) == {"implement", "plan"}


def test_an_empty_override_with_no_global_is_legal_and_inert(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """The last row of the resolution matrix: says "definitely nothing here", does nothing."""
    config = build(
        repo_clone, layout, tmp_path, repos=section(repo_clone, speckit_commands={"tasks": ""})
    )

    assert config.speckit_commands_for("demo") == ()


def test_a_repository_override_does_not_disturb_the_gate(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """`speckit` and `speckit_commands` are separate settings on the same section."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        repos=section(repo_clone, speckit=False, speckit_commands={"implement": "x"}),
    )

    assert config.speckit_enabled_for("demo") == (False, '[repos."demo"] speckit')
    assert resolve(config) == {"implement": ("x", '[repos."demo".speckit_commands] implement')}


def test_malformed_overrides_are_refused_naming_repository_and_command(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    """FR-028. Same checks as the global table, with the repository named."""
    found = problems(
        repo_clone,
        layout,
        tmp_path,
        repos=section(repo_clone, speckit_commands={"implement": 7, "plna": "typo"}),
    )

    assert any('[repos."demo".speckit_commands] implement must be a string' in p for p in found)
    assert any('[repos."demo".speckit_commands] unknown command' in p for p in found)


def test_speckit_commands_must_be_a_table_in_a_repository_section(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    found = problems(repo_clone, layout, tmp_path, repos=section(repo_clone, speckit_commands="x"))

    assert "[repos.demo] speckit_commands must be a table" in found


def test_a_mistyped_repository_key_is_caught_by_the_existing_strict_check(
    repo_clone: Path, layout: Any, tmp_path: Path
) -> None:
    found = problems(repo_clone, layout, tmp_path, repos=section(repo_clone, speckit_command={}))

    assert any("unknown key 'speckit_command'" in p for p in found)
