"""The argparse surface. Thin by design — it parses, dispatches, and prints.

Every verb's logic lives in :mod:`robot_army.operations` as a plain callable, so
milestone 002's HTTP API can call the same functions rather than reimplementing them
(contracts/cli.md). Nothing here decides anything; it chooses between two renderings of a
``Result`` and returns its exit code.

Exit codes: ``0`` success, ``1`` operation failed, ``2`` usage error, ``3`` precondition
not met, ``4`` check failed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from robot_army import __version__, operations
from robot_army.cardstates import CardState
from robot_army.config import ConfigError
from robot_army.config import load as load_config
from robot_army.daemon import PreconditionFailed, run_daemon
from robot_army.effects import EffectLevel
from robot_army.operations import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_USAGE,
    Context,
    Result,
)
from robot_army.states import WorkItemState

READ_COMMANDS = frozenset(
    {"status", "show", "repos", "worktree", "log", "anomalies", "health", "doctor", "cards"}
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robot-army",
        description=(
            "Turn labelled GitHub issues into real interactive worker sessions. "
            "Every capability is reachable from here; there is no other interface."
        ),
    )
    parser.add_argument("--version", action="version", version=f"robot-army {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="config file (default: ~/.config/robot-army/config.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- run ---------------------------------------------------------------
    run = sub.add_parser("run", help="run the daemon in the foreground — this is the product")
    run.add_argument(
        "--effect-level",
        choices=[level.value for level in EffectLevel],
        default=None,
        help="override the configured effect level",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="alias for --effect-level plan",
    )
    run.add_argument("--once", action="store_true", help="run exactly one cycle and exit")

    # -- read commands -----------------------------------------------------
    status = sub.add_parser("status", help="counts and listings by state, plus anomalies")
    # `choices=` rather than free text, because the value reaches `WorkItemState(...)` and
    # an unrecognised one raised a bare ValueError straight out of `main()` — a raw
    # traceback where the exit-code table promises a usage error. argparse refuses it
    # before it can get that far, and lists the valid values while doing so.
    status.add_argument(
        "--state",
        default=None,
        choices=[state.value for state in WorkItemState],
        help="filter to one work item state",
    )
    status.add_argument("--repo", default=None, help="filter to one repository")

    sub.add_parser(
        "capacity",
        help="how full the machine is, whose sessions those are, and the order in force",
    )

    show = sub.add_parser("show", help="everything about one work item")
    show.add_argument("item_id", type=int)

    sub.add_parser("repos", help="onboarding, fingerprint, and trust status per repository")

    worktree = sub.add_parser("worktree", help="worktree listing and removal")
    worktree_sub = worktree.add_subparsers(dest="worktree_command", required=True)
    worktree_sub.add_parser("list", help="worktrees with size, branch, and condition")
    remove = worktree_sub.add_parser(
        "remove", help="remove BOTH the worktree and its branch (FR-016)"
    )
    remove.add_argument("item_id", type=int)
    remove.add_argument(
        "--force",
        action="store_true",
        help="override git's refusal on a dirty worktree; requires typed confirmation",
    )
    worktree_sub.add_parser("prune", help="clear git's record of worktrees whose dirs are gone")

    cleanup = sub.add_parser(
        "cleanup",
        help="reclaim finished work's worktree and branch, under the same two guards",
    )
    cleanup.add_argument(
        "item_id",
        type=int,
        nargs="?",
        default=None,
        help="one item to consider, reconsidering a retained decision; omit for every "
        "eligible item",
    )

    log = sub.add_parser("log", help="read the audit JSONL — the reconstruction path")
    log.add_argument("--since", default=None, metavar="DURATION", help="e.g. 30s, 10m, 2h, 1d")
    log.add_argument("--item", type=int, default=None, help="filter to one work item id")
    log.add_argument("--limit", type=int, default=None, help="show only the last N records")
    log.add_argument("--follow", action="store_true", help="tail the current day's file")

    anomalies = sub.add_parser("anomalies", help="conditions detected but not resolvable")
    anomalies.add_argument("--acknowledge", type=int, default=None, metavar="ID")
    anomalies.add_argument("--all", action="store_true", help="include acknowledged ones")
    # Spelled exactly as `log --since` above, because it is the same parser behind it —
    # "what went wrong in the last hour" is read off these two commands side by side.
    anomalies.add_argument(
        "--since", default=None, metavar="DURATION", help="e.g. 30s, 10m, 2h, 1d"
    )

    health = sub.add_parser("health", help="exit 4 if the heartbeat is stale or absent")
    health.add_argument(
        "--max-age", type=float, default=None, metavar="SECONDS", help="staleness threshold"
    )
    health.add_argument("--notify", action="store_true", help="POST to the configured webhook")

    sub.add_parser("doctor", help="check config, binaries, sockets, permissions, disk")

    # -- lifecycle ---------------------------------------------------------
    poll = sub.add_parser("poll", help="force an immediate poll")
    poll.add_argument("--repo", default=None)

    sub.add_parser("reconcile", help="force a reconciliation pass")
    sub.add_parser("drain", help="drain the exit spool now")

    cancel = sub.add_parser("cancel", help="stop one item's session and only that one")
    cancel.add_argument("item_id", type=int)
    cancel.add_argument("--force", action="store_true", help="skip the confirmation prompt")

    resume = sub.add_parser("resume", help="new session restoring the prior context")
    resume.add_argument("item_id", type=int)

    restart = sub.add_parser("restart", help="fresh session in the existing worktree")
    restart.add_argument("item_id", type=int)

    abandon = sub.add_parser("abandon", help="mark abandoned; does not remove the worktree")
    abandon.add_argument("item_id", type=int)

    retry = sub.add_parser("retry", help="move a failed item back to ready")
    retry.add_argument("item_id", type=int)

    onboard = sub.add_parser("onboard", help="the deliberate per-repository trust step")
    onboard.add_argument("repo_key")
    onboard.add_argument(
        "--reapprove", action="store_true", help="re-approve after a fingerprint change"
    )
    onboard.add_argument(
        "--yes",
        action="store_true",
        help="skip the prompt; refuses when committed settings are unapproved",
    )

    purge = sub.add_parser("purge-simulated", help="remove dry-run rows (FR-058)")
    purge.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    # -- milestone 002 -----------------------------------------------------
    serve = sub.add_parser(
        "serve", help="run the web interface in the foreground, independently of the daemon"
    )
    serve.add_argument(
        "--bind", default=None, metavar="ADDR", help="listening address (default: [web] bind)"
    )
    serve.add_argument(
        "--port", type=int, default=None, metavar="N", help="listening port (default: [web] port)"
    )
    serve.add_argument(
        "--effect-level",
        choices=[level.value for level in EffectLevel],
        default=None,
        help="override the configured effect level",
    )

    sub.add_parser(
        "pause",
        help="suspend dispatch durably; polling, reconciliation and the heartbeat continue",
    )
    sub.add_parser("unpause", help="resume dispatch")

    attach = sub.add_parser("attach", help="open a terminal window on a running session")
    attach.add_argument("item_id", type=int)

    # -- milestone 003 -----------------------------------------------------
    cards = sub.add_parser("cards", help="tracked intake cards, their state and their reason")
    cards.add_argument(
        "--state",
        default=None,
        choices=[state.value for state in CardState],
        help="filter to one card state",
    )

    rescan = sub.add_parser(
        "rescan", help="force re-evaluation of cards awaiting clarification (FR-024)"
    )
    rescan.add_argument(
        "card_id",
        nargs="?",
        default=None,
        help="the card to re-evaluate; omit with --all-needs-info",
    )
    rescan.add_argument(
        "--all-needs-info",
        action="store_true",
        help="re-evaluate every card awaiting clarification",
    )

    # -- universal flags ---------------------------------------------------
    for name, action in sub.choices.items():
        if name in READ_COMMANDS or name in (
            "poll",
            "reconcile",
            "rescan",
            "drain",
            "purge-simulated",
            "pause",
            "unpause",
            "attach",
            # `onboard` is here because 011 gave it a machine-readable mode to be correct
            # about: its prompt writes to stderr and its approval screen is suppressed so
            # that stdout carries one parseable document (FR-012). Without the flag that
            # was a contract describing a mode no command could enter. A prompting command
            # with `--json` is not novel — `purge-simulated` above has been one all along.
            "onboard",
        ):
            action.add_argument(
                "--json", action="store_true", help="machine-readable output on stdout"
            )
        if name in ("status", "worktree", "log", "anomalies", "repos", "cards"):
            action.add_argument(
                "--include-simulated",
                action="store_true",
                help="include dry-run rows; without it they are excluded (FR-056)",
            )
    # `worktree` puts --json / --include-simulated on the group, not the leaves, so the
    # subcommand parsers inherit nothing; add them where they are actually parsed.
    for leaf in ("list", "remove", "prune"):
        parser_leaf = worktree_sub.choices[leaf]
        parser_leaf.add_argument("--json", action="store_true")
        if leaf == "list":
            parser_leaf.add_argument("--include-simulated", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run(args)
    if args.command == "serve":
        # Handled here rather than in the table below because `serve` must never open the
        # database the way every other command does: the web process verifies the schema
        # version and refuses on a mismatch instead of migrating (research.md R11).
        return _serve(args)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        for warning in exc.warnings:
            print(f"  (warning) {warning}", file=sys.stderr)
        return EXIT_PRECONDITION

    ctx = operations.build_context(config)
    try:
        result = _dispatch(args, ctx)
    except PreconditionFailed as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PRECONDITION
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_FAILED
    finally:
        ctx.close()

    if result is None:
        return EXIT_OK
    as_json = bool(getattr(args, "json", False))
    # A machine-readable document goes to stdout whatever the exit code. That is what
    # `--json`'s own help text above already promises, and after 011 it is also what keeps
    # the document off the stream `onboard`'s prompt now writes to — a declined `--json`
    # run would otherwise put the question and the document on stderr together and neither
    # would parse (FR-012). Human-readable output keeps the split it has always had: an
    # outcome that failed belongs on stderr.
    stream = sys.stdout if as_json or result.code == EXIT_OK else sys.stderr
    text = result.render(as_json=as_json)
    if text:
        print(text, file=stream)
    return result.code


def _run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        for warning in exc.warnings:
            print(f"  (warning) {warning}", file=sys.stderr)
        return EXIT_PRECONDITION

    if args.dry_run and args.effect_level and args.effect_level != EffectLevel.PLAN.value:
        print(
            f"--dry-run is an alias for --effect-level plan and conflicts with "
            f"--effect-level {args.effect_level}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.dry_run:
        level = EffectLevel.PLAN
    elif args.effect_level:
        level = EffectLevel(args.effect_level)
    else:
        level = config.daemon.effect_level

    print(f"robot-army: effect level {level}", file=sys.stderr)
    for warning in config.warnings:
        print(f"robot-army: warning: {warning}", file=sys.stderr)

    try:
        return run_daemon(config=config, effect_level=level, once=args.once)
    except PreconditionFailed as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PRECONDITION


def _serve(args: argparse.Namespace) -> int:
    from robot_army.web.server import serve

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        for warning in exc.warnings:
            print(f"  (warning) {warning}", file=sys.stderr)
        return EXIT_PRECONDITION
    level = EffectLevel(args.effect_level) if args.effect_level else None
    try:
        return serve(config, bind=args.bind, port=args.port, effect_level=level)
    except KeyboardInterrupt:  # pragma: no cover - the signal handler normally wins
        return EXIT_OK


def _dispatch(args: argparse.Namespace, ctx: Context) -> Result | None:
    """Route one parsed command to its operation.

    A literal table rather than an if-chain: the mapping *is* the CLI surface, and
    reading it beside contracts/cli.md should be a one-to-one check.
    """
    include_simulated = bool(getattr(args, "include_simulated", False))

    table: dict[str, Callable[[], Result | None]] = {
        "status": lambda: operations.status(
            ctx, state=args.state, repo=args.repo, include_simulated=include_simulated
        ),
        "capacity": lambda: operations.capacity(ctx),
        "show": lambda: operations.show(ctx, args.item_id),
        "repos": lambda: operations.repos(ctx),
        "worktree": lambda: _worktree(args, ctx),
        "cleanup": lambda: operations.cleanup_now(ctx, args.item_id),
        "log": lambda: (
            _follow(ctx)
            if args.follow
            else operations.read_log(
                ctx, since=args.since, item_id=args.item, limit=args.limit
            )
        ),
        "anomalies": lambda: operations.anomalies(
            ctx, acknowledge=args.acknowledge, show_all=args.all, since=args.since
        ),
        "health": lambda: operations.health_check(
            ctx, max_age=args.max_age, do_notify=args.notify
        ),
        "doctor": lambda: operations.doctor(ctx),
        "poll": lambda: operations.poll_now(ctx, repo=args.repo),
        "reconcile": lambda: operations.reconcile_now(ctx),
        "drain": lambda: operations.drain_spool(ctx),
        "cancel": lambda: operations.cancel(ctx, args.item_id, force=args.force),
        "resume": lambda: operations.resume(ctx, args.item_id),
        "restart": lambda: operations.restart(ctx, args.item_id),
        "abandon": lambda: operations.abandon(ctx, args.item_id),
        "retry": lambda: operations.retry(ctx, args.item_id),
        "onboard": lambda: operations.onboard(
            ctx,
            args.repo_key,
            reapprove=args.reapprove,
            assume_yes=args.yes,
            # The approval screen goes out before the prompt blocks (011 FR-001) — but
            # never into a machine-readable run, whose stdout must parse as one document
            # (FR-012). `None` there is not "skip the screen": it is the pre-011 route,
            # where the lines reach `main` and `render(as_json=True)` drops them.
            out=None if bool(getattr(args, "json", False)) else sys.stdout,
        ),
        "purge-simulated": lambda: operations.purge_simulated(ctx, assume_yes=args.yes),
        "pause": lambda: operations.pause_dispatch(ctx, by="cli"),
        "unpause": lambda: operations.unpause_dispatch(ctx, by="cli"),
        "attach": lambda: operations.attach(ctx, args.item_id),
        "cards": lambda: operations.cards(
            ctx, state=args.state, include_simulated=include_simulated
        ),
        "rescan": lambda: _rescan(args, ctx),
    }

    handler = table.get(args.command)
    if handler is None:
        return Result(code=EXIT_USAGE, lines=[f"unknown command {args.command!r}"])
    return handler()


def _rescan(args: argparse.Namespace, ctx: Context) -> Result:
    """``rescan`` takes either a card id or ``--all-needs-info``, and needs exactly one.

    Checked here rather than by argparse because "neither" and "both" are different usage
    mistakes and deserve different messages — argparse's mutually-exclusive group would
    report one of them as the other.
    """
    if bool(args.card_id) == bool(args.all_needs_info):
        return Result(
            code=EXIT_USAGE,
            lines=[
                "usage: robot-army rescan <card-id> | robot-army rescan --all-needs-info"
                + (
                    "  (a card id and --all-needs-info together are ambiguous)"
                    if args.card_id
                    else "  (name a card, or ask for all of them)"
                )
            ],
        )
    return operations.rescan(ctx, args.card_id or "", all_needs_info=args.all_needs_info)


def _worktree(args: argparse.Namespace, ctx: Context) -> Result:
    if args.worktree_command == "list":
        return operations.worktree_list(
            ctx, include_simulated=bool(getattr(args, "include_simulated", False))
        )
    if args.worktree_command == "remove":
        return operations.worktree_remove(ctx, args.item_id, force=args.force)
    if args.worktree_command == "prune":
        return operations.worktree_prune(ctx)
    return Result(code=EXIT_USAGE, lines=["usage: robot-army worktree {list,remove,prune}"])


def _follow(ctx: Context) -> Result | None:
    try:
        for line in operations.follow_log(ctx):
            print(line, flush=True)
    except KeyboardInterrupt:
        pass
    return None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
