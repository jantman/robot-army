"""Every verb that advertises ``--include-simulated`` actually filters on it (issue #21).

This file exists because the promise was broken once already, and nothing noticed. The flag
was decorated onto six verbs by a literal tuple inside the parser and honoured by three of
them; the help text named FR-056 by number on all six. `anomalies`, `repos` and `log` accepted
it and printed byte-identical output either way, which is worse than not offering it — a
reader who passed it believed they had excluded something.

The guard is behavioural, not structural. Introspecting the dispatch table to prove a value is
threaded through would be a test that reads source code, and a test that reads source code
proves nothing about what the command prints. So every member of
``cli.SIMULATED_SCOPED_COMMANDS`` is driven twice against one state holding rehearsed rows of
every kind, and the two runs must disagree. A seventh verb decorated without being wired up
fails here on the day it is added.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.conftest import make_boundaries, seed_item

from robot_army import cli, db, operations
from robot_army.effects import EffectLevel

FLAG = "--include-simulated"


@pytest.fixture
def ctx(board_config, conn, audit):
    """A context over the seeded database, with a board configured so ``cards`` has one.

    Commands are driven through ``cli.build_parser`` and ``cli._dispatch`` rather than through
    ``cli.main``, deliberately. Those two are exactly where issue #21 lived — the parser
    decorated a verb and the dispatch table did not pass the value on — and going through
    ``main`` would add a config file on disk without exercising a single line more of what is
    under test.
    """
    return operations.Context(
        config=board_config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def run(ctx, argv: list[str]) -> operations.Result:
    args = cli.build_parser().parse_args(argv)
    result = cli._dispatch(args, ctx)
    assert result is not None, f"{argv} produced no result to inspect"
    return result


def _seed_everything(conn, layout) -> None:
    """One rehearsed row and one real row of every kind the decorated verbs print.

    Deliberately paired: a state holding *only* rehearsed rows would let a verb pass by
    printing nothing at all in the default spelling, which is not the property under test.
    """
    # Work items — `status`. One of them carries a worktree, for `worktree list`.
    seed_item(conn, issue_number=1, dry_run=False, state="ready")
    seed_item(conn, issue_number=2, dry_run=True, state="ready")
    real_wt = seed_item(conn, issue_number=3, dry_run=False, state="active")
    sim_wt = seed_item(conn, issue_number=4, dry_run=True, state="active")
    with db.transaction(conn):
        for item, name in ((real_wt, "real"), (sim_wt, "sim")):
            db.update_work_item_columns(
                conn, item, worktree_path=f"/tmp/wt-{name}", branch=f"b/{name}"
            )

    # Cards — `cards`.
    with db.transaction(conn):
        for card_id, dry_run in (("card-real", False), ("card-sim", True)):
            db.insert_card(
                conn,
                board_id="board-1",
                card_id=card_id,
                card_url=f"https://trello.com/c/{card_id}",
                title="a card",
                body="",
                dry_run=dry_run,
            )

    # Anomalies — `anomalies`, and the block `status` prints beneath its listing.
    with db.transaction(conn):
        for entity, dry_run in (("anomaly-real", False), ("anomaly-sim", True)):
            db.raise_anomaly(
                conn,
                kind="card_create_failing",
                entity_type="card",
                entity_id=entity,
                detail={"attempts": 3},
                dry_run=dry_run,
            )

    # Audit records — `log`.
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8") as handle:
        for action, extra in (("record.real", {}), ("record.sim", {"simulated": True})):
            handle.write(
                json.dumps(
                    {
                        "ts": stamp,
                        "component": "daemon",
                        "kind": "event",
                        "action": action,
                        "outcome": "ok",
                        **extra,
                    }
                )
                + "\n"
            )


#: The argv each decorated verb is driven with. ``worktree`` is a group, so it needs its leaf
#: named; every other verb is its own bare command.
ARGV = {
    "status": ["status"],
    "cards": ["cards"],
    "anomalies": ["anomalies"],
    "log": ["log"],
    "worktree": ["worktree", "list"],
}


def test_the_argv_table_covers_every_decorated_verb() -> None:
    """If a verb joins the set, this test names it before the next one can pass vacuously."""
    assert set(ARGV) == set(cli.SIMULATED_SCOPED_COMMANDS)


@pytest.mark.parametrize("verb", sorted(cli.SIMULATED_SCOPED_COMMANDS))
def test_every_advertised_flag_changes_what_the_verb_prints(verb, ctx, conn, layout) -> None:
    """The promise, asserted as behaviour: the two spellings must disagree.

    This is the whole content of issue #21. A verb that accepts the flag and prints the same
    thing either way is a verb making a claim it cannot keep.
    """
    _seed_everything(conn, layout)
    argv = ARGV[verb]

    without = "\n".join(run(ctx, argv).lines)
    with_flag = "\n".join(run(ctx, [*argv, FLAG]).lines)

    assert without != with_flag, f"`robot-army {' '.join(argv)}` ignores {FLAG}"


@pytest.mark.parametrize("verb", sorted(cli.SIMULATED_SCOPED_COMMANDS))
def test_every_advertised_flag_discloses_what_it_withheld(verb, ctx, conn, layout) -> None:
    """FR-007: withholding silently is the quieter half of the same defect.

    A verb that filters correctly but says nothing about it leaves the reader unable to tell
    "nothing is wrong" from "you are not being shown it".
    """
    _seed_everything(conn, layout)

    data = run(ctx, ARGV[verb]).data
    stated = data["withheld_simulated"]

    # `status` reports one number per section it withholds from; the rest report one figure.
    counts = list(stated.values()) if isinstance(stated, dict) else [stated]
    assert any(count > 0 for count in counts), (
        f"`robot-army {verb}` withheld rows without saying so"
    )


@pytest.mark.parametrize("verb", sorted(cli.SIMULATED_SCOPED_COMMANDS))
def test_the_withheld_count_equals_what_the_flag_reveals(verb, ctx, conn, layout) -> None:
    """The equality that makes the number safe to print, on every verb at once.

    Milestone 008 made this structural for work items by extracting one filter predicate. It
    has to be earned again on each verb that joins the set, because the failure is silent: a
    number that is merely close looks exactly like a number that is right.
    """
    _seed_everything(conn, layout)

    hidden = run(ctx, ARGV[verb]).data
    shown = run(ctx, [*ARGV[verb], FLAG]).data

    stated = hidden["withheld_simulated"]
    if isinstance(stated, dict):
        # `status`'s three sections have three populations; the listing is the comparable one.
        assert len(shown["items"]) - len(hidden["items"]) == stated["items"]
        assert len(shown["anomalies"]) - len(hidden["anomalies"]) == stated["anomalies"]
        return
    key = {"cards": "cards", "anomalies": "anomalies", "log": "records",
           "worktree": "worktrees"}[verb]
    assert len(shown[key]) - len(hidden[key]) == stated


@pytest.mark.parametrize("verb", sorted(cli.SIMULATED_SCOPED_COMMANDS))
def test_every_advertised_flag_is_actually_advertised(verb) -> None:
    """The set and the parser are one object, so this cannot drift — but check the wiring."""
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    target = subparsers.choices[verb]
    if verb == "worktree":
        # The group puts it on the leaf, because the subcommand parsers inherit nothing.
        target = next(
            a for a in target._actions if hasattr(a, "choices") and a.choices
        ).choices["list"]
    options = {opt for action in target._actions for opt in action.option_strings}
    assert FLAG in options


# -- the verb that stopped advertising it -----------------------------------


def test_repos_refuses_the_option_rather_than_accepting_and_ignoring_it(capsys):
    """Silently accepting a flag that does nothing is the worse of the two failures.

    A `repos` row is written by onboarding, which inspects a real clone on disk and has no
    rehearsed path, so the table cannot hold a row this flag would hide. The honest answer is
    a usage error, not a filter over an empty population.
    """
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["repos", FLAG])

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --include-simulated" in capsys.readouterr().err


def test_repos_does_not_mention_the_option_in_its_help(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["repos", "--help"])

    assert FLAG not in capsys.readouterr().out


def test_repos_still_works_without_it(ctx) -> None:
    """Removing the option must not have removed the verb."""
    result = run(ctx, ["repos"])

    assert result.code == operations.EXIT_OK
    assert result.lines
