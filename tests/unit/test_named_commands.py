"""Every ``robot-army …`` this codebase names has to be a command that exists (issue #50).

The paused queue used to tell the author to run ``robot-army resume``, which is a real verb
but the wrong one: ``resume`` takes an item id and *dispatches a session*, so following the
advice either failed for want of an argument or started work on some unrelated item. The
verb that lifts a pause is ``unpause``, and ``pause``'s own output already said so — two
messages about one state, disagreeing.

Nothing could have caught it. A command name inside a message is a string, so the type
checker sees nothing, and the message is only rendered when a paused system is asked what
it is waiting for. This file closes the half of that class that *is* mechanically
checkable: a message may still name the wrong real command, but it can no longer name a
command that does not exist, or pass it a flag it does not have.

Two scans, because they fail differently:

* the source text, so a message is checked wherever it is written rather than only where
  some test happens to render it — comments and docstrings included, since prose that
  names a command is read as an instruction by whoever reads it next;
* the hold reasons themselves, rendered and re-checked, so the path from "the queue
  produced this sentence" to "that is a real command" is closed end to end rather than
  inferred from the fact that both halves were looked at separately.

The check is the parser itself — :func:`cli.build_parser` — never a list of verbs kept
here. A copy of the verb list would be a second source of truth that goes stale in exactly
the direction that makes this test pass while the message is wrong.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest
from tests.conftest import seed_item
from tests.unit.test_ordering import snapshot

from robot_army import db, ordering
from robot_army.cli import build_parser

SRC = Path(__file__).resolve().parents[2] / "src" / "robot_army"

#: A command as the codebase writes one: inside backticks, ``robot-army`` first.
NAMED = re.compile(r"`robot-army ([^`]+)`")

#: What a subcommand looks like, and what nothing else in the corpus does. Everything a
#: message writes in that slot instead — an f-string placeholder, a ``<meta>`` word, the
#: fragments left by string concatenation — fails it, and is left unchecked: what a
#: positional argument holds is the caller's business, and only the shape is ours.
LITERAL_WORD = re.compile(r"[a-z][a-z-]*\Z")


# -- the checker ------------------------------------------------------------


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def problems(command: str) -> list[str]:
    """What is wrong with one ``robot-army`` invocation, as sentences; empty if nothing.

    Only the parts a message can get wrong without anyone noticing are checked — the verb,
    a nested subcommand, and each long option. Argument *values* are deliberately not:
    almost every one in the source is a placeholder, and demanding they parse would mean
    inventing values here to satisfy types that ``argparse`` already enforces at the door.
    """
    tokens: list[str] = []
    for token in command.split():
        if token[0] in ">|":
            # A shell example (`example-config > config.toml`). What follows the redirect
            # belongs to the shell, and this parser has no opinion about it.
            break
        tokens.append(token)
    if not tokens:
        return ["names no verb at all"]

    verb, *rest = tokens
    parser = build_parser()
    verbs = _subcommands(parser)
    if verb not in verbs:
        return [f"{verb!r} is not a robot-army verb"]
    parser = verbs[verb]

    found: list[str] = []
    nested = _subcommands(parser)
    if nested:
        for token in rest:
            if token.startswith("-"):
                continue
            if token in nested:
                parser = nested[token]
            elif LITERAL_WORD.match(token):
                found.append(f"`{verb}` has no subcommand {token!r}")
            break

    for token in rest:
        if not token.startswith("--"):
            continue
        option = token.split("=", 1)[0]
        if option not in _options(parser):
            found.append(f"`{verb}` has no option {option}")
    return found


def named_commands() -> list[tuple[str, int, str]]:
    """Every command the source names, as ``(file, line, command)``."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in NAMED.finditer(line):
                found.append((str(path.relative_to(SRC)), number, match.group(1)))
    return found


# -- the scan is real -------------------------------------------------------


def test_the_source_tree_was_actually_found():
    """A scanning test that silently scans nothing is worse than no test."""
    assert len(list(SRC.rglob("*.py"))) > 15, f"no source files under {SRC}"


def test_the_scan_finds_the_commands_the_codebase_names():
    """The corpus is the point: an empty one would pass every assertion below."""
    found = named_commands()
    assert len(found) > 20, f"only found {len(found)} named commands"
    assert {file for file, _line, _command in found} > {"ordering.py", "operations.py"}


@pytest.mark.parametrize(
    "command",
    [
        "unpasue",  # the verb does not exist
        "pause --forever",  # the verb does, the option does not
        "worktree destroy",  # the subcommand does not
    ],
)
def test_the_checker_would_catch_a_command_that_does_not_exist(command):
    """Guards the guard. Every assertion here rests on ``problems`` being able to say no."""
    assert problems(command), f"{command!r} was accepted"


@pytest.mark.parametrize(
    "command",
    [
        "unpause",
        "onboard {repo_key} --reapprove",
        "worktree prune",
        "log --since 1m",
        "unhold --repo <key>",
        "example-config > config.toml",
    ],
)
def test_the_checker_accepts_the_shapes_the_codebase_actually_writes(command):
    """The other half of guarding the guard: a checker that rejects everything is no
    cheaper to satisfy than one that accepts everything, and much more annoying."""
    assert problems(command) == []


# -- the guarantee ----------------------------------------------------------


def test_every_command_the_source_names_exists():
    offenders = [
        f"{file}:{line}: `robot-army {command}` — {'; '.join(faults)}"
        for file, line, command in named_commands()
        if (faults := problems(command))
    ]
    assert not offenders, "commands named that do not exist:\n" + "\n".join(offenders)


def test_every_command_a_hold_reason_names_exists(conn, config):
    """The end-to-end half: rendered hold details, not source text.

    A hold reason is the surface issue #50 was reported against, and it is the one place a
    command name is *composed* rather than written — ``not_onboarded`` interpolates a
    repository key into it. Rendering the reasons and re-checking what comes out is what
    makes the guarantee about the sentence the author reads.
    """
    item = db.get_work_item(conn, seed_item(conn))
    details = [
        detail
        for _reason, detail in ordering.launch_holds(
            item,
            config=config,
            capacity=snapshot(global_cap=9),
            paused=True,
            item_holds=None,
            repo_holds=None,
        )
    ]
    details += [
        detail
        for entry in ordering.plan(conn, config=config, capacity=snapshot(global_cap=9))
        if (detail := entry.detail)
    ]
    named = [match.group(1) for detail in details for match in NAMED.finditer(detail)]
    assert named, "no hold reason named a command; the assertion below proves nothing"
    offenders = [
        f"`robot-army {command}` — {'; '.join(faults)}"
        for command in named
        if (faults := problems(command))
    ]
    assert not offenders, "hold reasons naming commands that do not exist:\n" + "\n".join(
        offenders
    )


def test_the_paused_hold_reason_names_the_verb_that_lifts_a_pause(conn, config):
    """Issue #50 itself. ``unpause`` clears the pause; ``resume`` dispatches a session for
    one item, and is the wrong advice however real a verb it is."""
    item = db.get_work_item(conn, seed_item(conn))
    (reason, detail), *_ = ordering.launch_holds(
        item, config=config, capacity=snapshot(global_cap=9), paused=True
    )
    assert reason is ordering.HoldReason.PAUSED
    assert "`robot-army unpause`" in detail
    assert "robot-army resume" not in detail
