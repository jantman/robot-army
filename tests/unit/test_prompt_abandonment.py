"""Giving up at a confirmation prompt is a recorded result, not a traceback (issue #23).

Four commands stop and ask the maintainer something before acting. Milestone 011 wrapped
`onboard`'s question in `try/except KeyboardInterrupt/EOFError` and left the other three,
so `robot-army purge-simulated < /dev/null` printed a Python traceback, `cancel` did the
same to the command that signals a running worker, and `worktree remove --force` — the one
that discards uncommitted work — left an audit outcome whose entire content was `EOFError`.

The fix is not three more `except` clauses. It is one guard the prompts pass through and
one decorator that turns what it raises back into a `Result`, so the fifth prompt someone
adds is guarded by the shape of the code rather than by their memory. The last two tests in
this file are what makes that claim checkable.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import db, operations
from robot_army.audit import read_records
from robot_army.effects import EffectLevel
from robot_army.operations import (
    EXIT_CHECK_FAILED,
    EXIT_FAILED,
    EXIT_OK,
    PromptAbandoned,
    Result,
)

EOF_LINE = "no answer available: input ended before the prompt was answered"

#: Both ways a question goes unanswered, and what each one is worth.
GAVE_UP = [
    (KeyboardInterrupt(), EXIT_FAILED, "interrupted", "interrupted_at_prompt"),
    (EOFError(), EXIT_CHECK_FAILED, EOF_LINE, "no_answer_available"),
]
GAVE_UP_IDS = ["ctrl-c", "eof"]


def raising(error: BaseException) -> Any:
    def confirm(_prompt: str) -> str:
        raise error

    return confirm


def ctx_over(conn, audit, config) -> operations.Context:
    return operations.Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def records(layout, action: str) -> list[dict[str, Any]]:
    return [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and record["action"] == action
    ]


# -- the guard itself --------------------------------------------------------


@pytest.mark.parametrize(("error", "code", "line", "cause"), GAVE_UP, ids=GAVE_UP_IDS)
def test_the_guard_records_the_cause_and_raises_the_result_it_stands_for(
    error, code, line, cause
):
    seen: list[str] = []

    with pytest.raises(PromptAbandoned) as raised:
        operations._answer_or_give_up(
            "Do the thing? [y/N] ",
            confirm=raising(error),
            record=seen.append,
            lines=["something said earlier"],
            data={"item_id": 7},
        )

    assert seen == [cause], "recorded exactly once, with the cause"
    result = raised.value.result
    assert result.code == code
    assert result.lines == ["something said earlier", line]
    assert result.data == {"item_id": 7}, "a --json run still has a document to render"


def test_the_two_causes_do_not_collapse_into_one_exit_code():
    """"I changed my mind" and "this ran where nobody was listening" are different things
    to find in a log, and different things for a shell script to branch on."""
    codes = set()
    for error, _, _, _ in GAVE_UP:
        with pytest.raises(PromptAbandoned) as raised:
            operations._answer_or_give_up(
                "?", confirm=raising(error), record=lambda _cause: None
            )
        codes.add(raised.value.result.code)

    assert len(codes) == 2


def test_an_answered_prompt_passes_straight_through_and_records_nothing():
    seen: list[str] = []

    answer = operations._answer_or_give_up(
        "Type the item id (7): ", confirm=lambda _: "7", record=seen.append
    )

    assert answer == "7"
    assert seen == [], "nothing happened, so nothing is recorded"


def test_the_guard_catches_nothing_else():
    """A failure inside the guarded work is not an abandoned prompt. Swallowing one here
    would be the silent failure Principle III forbids, dressed as a courtesy."""
    with pytest.raises(RuntimeError):
        operations._answer_or_give_up(
            "?", confirm=raising(RuntimeError("the terminal caught fire")),
            record=lambda _cause: pytest.fail("not an abandonment"),
        )


# -- purge-simulated, the one the issue reproduced ---------------------------


@pytest.mark.parametrize(("error", "code", "line", "cause"), GAVE_UP, ids=GAVE_UP_IDS)
def test_giving_up_at_the_purge_prompt_deletes_nothing(
    conn, audit, config, layout, error, code, line, cause
):
    """`robot-army purge-simulated < /dev/null`, verbatim from issue #23."""
    seed_item(conn, dry_run=True)
    before = db.count_simulated(conn)

    result = operations.purge_simulated(
        ctx_over(conn, audit, config), confirm=raising(error)
    )
    audit.close()

    assert result.code == code
    assert result.lines == [line]
    assert db.count_simulated(conn) == before, "no row was deleted"

    written = records(layout, "purge.simulated")
    assert len(written) == 1, "one record; the action wraps the delete, not the asking"
    assert written[0]["outcome"] == "error"
    assert written[0]["detail"]["abandoned"] is True
    assert written[0]["detail"]["cause"] == cause
    assert written[0]["detail"]["work_items"] == before["work_items"], (
        "the counts the question quoted, so the log says what was nearly deleted"
    )


def test_the_purge_prompt_is_only_asked_when_there_is_something_to_purge(
    conn, audit, config
):
    """No rows means no question, so there is nothing to give up on — checked because a
    guard that fires on an unasked question would be worse than the traceback."""
    result = operations.purge_simulated(
        ctx_over(conn, audit, config),
        confirm=raising(AssertionError("nothing to delete is not a question")),
    )

    assert result.code == EXIT_OK
    assert result.lines == ["no simulated rows to purge"]


def test_answering_the_purge_prompt_still_works_either_way(conn, audit, config):
    """FR-007. The guard sits in front of the answer, not in place of it."""
    seed_item(conn, dry_run=True)
    ctx = ctx_over(conn, audit, config)

    declined = operations.purge_simulated(ctx, confirm=lambda _: "n")
    assert declined.code == EXIT_FAILED and declined.lines == ["aborted"]
    assert db.count_simulated(conn)["work_items"] == 1

    approved = operations.purge_simulated(ctx, confirm=lambda _: "y")
    assert approved.code == EXIT_OK
    assert db.count_simulated(conn)["work_items"] == 0


# -- the claim that the next prompt inherits this ----------------------------


def test_a_new_prompt_is_guarded_by_wearing_the_decorator_and_nothing_else():
    """US3. The fifth prompt, written the way the four are: ask through the guard, wear
    the decorator, handle nothing. If this needed a `try` to pass, the trap issue #23
    describes would be re-armed."""

    @operations._guards_its_prompt
    def a_fifth_command(*, confirm: Any, record: Any) -> Result:
        answer = operations._answer_or_give_up(
            "Something irreversible? [y/N] ", confirm=confirm, record=record
        )
        return Result(lines=[f"did it: {answer}"])

    seen: list[str] = []
    given_up = a_fifth_command(confirm=raising(EOFError()), record=seen.append)
    answered = a_fifth_command(confirm=lambda _: "y", record=seen.append)

    assert given_up.code == EXIT_CHECK_FAILED and given_up.lines == [EOF_LINE]
    assert seen == ["no_answer_available"]
    assert answered.code == EXIT_OK and answered.lines == ["did it: y"]


def test_every_operation_that_prompts_wears_the_decorator():
    """The guard against the guard. A prompting operation is one with a `confirm`
    parameter; forgetting the decorator on the next one fails here rather than at a
    maintainer's terminal, months later, with a traceback."""
    prompting = {
        name: func
        for name, func in vars(operations).items()
        if callable(func)
        and not name.startswith("_")
        and getattr(func, "__module__", None) == operations.__name__
        and "confirm" in inspect.signature(func).parameters
    }

    assert set(prompting) == {"onboard", "cancel", "purge_simulated", "worktree_remove"}
    for name, func in prompting.items():
        assert getattr(func, "__wrapped__", None) is not None, (
            f"{name} asks a question and does not wear @_guards_its_prompt"
        )
        assert inspect.signature(func).parameters["confirm"].default is operations._ask, (
            f"{name} must ask on stderr, where a --json document is not"
        )


def test_no_call_site_handles_an_abandoned_prompt_itself():
    """SC-004, read off the source. Four separate `except EOFError` clauses is the state
    issue #23 found; one is the state it asks for. Anything that re-adds a second handler
    somewhere in `operations.py` fails here, whether or not it also works."""
    source = Path(operations.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and re.search(r"\b(KeyboardInterrupt|EOFError|PromptAbandoned)\b", ast.dump(node))
    ]
    lines = sorted(node.lineno for node in handlers)
    guarded = {
        operations._answer_or_give_up.__code__.co_firstlineno,
        operations._guards_its_prompt.__code__.co_firstlineno,
    }

    assert len(lines) == 3, f"expected two in the guard and one in the decorator: {lines}"
    for lineno in lines:
        owner = max(start for start in guarded if start < lineno)
        assert lineno - owner < 100, (
            f"an interrupt handler at operations.py:{lineno} is not inside the guard"
        )
