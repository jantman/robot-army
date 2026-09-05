"""Exit codes for every command's failure paths (T126).

contracts/cli.md fixes these: ``0`` success, ``1`` operation failed, ``2`` usage error,
``3`` precondition not met, ``4`` check failed. Every command must exit non-zero on
failure *and explain it* (FR-069), so these tests assert the code and the message.

``prompt``'s four codes are asserted in ``tests/unit/test_prompt_preview.py`` instead, not
here: that command needs a repository keyed ``owner/name`` — a key without an owner is one
of the failures it is *about* — and the fixture in this module deliberately uses the short
key every other command is content with. The assertions there are stricter than these, since
each also proves stdout stayed empty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import config_dict, make_boundaries, monkey_token, seed_item

from robot_army import operations
from robot_army.cli import build_parser, main
from robot_army.config import load as load_config
from robot_army.effects import EffectLevel
from robot_army.operations import (
    EXIT_CHECK_FAILED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_USAGE,
)
from robot_army.states import WorkItemState


@pytest.fixture
def config_file(tmp_path, repo_clone, layout) -> Path:
    """A real config file on disk, so the CLI's own loading path is exercised."""
    import tomllib  # noqa: F401 - documents that the file must be valid TOML

    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    path = tmp_path / "config.toml"
    path.write_text(_to_toml(raw), encoding="utf-8")
    return path


def _to_toml(data: dict) -> str:
    """A tiny writer — the daemon never writes TOML, so this belongs in tests only."""
    lines: list[str] = []

    def render(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(render(v) for v in value) + "]"
        return json.dumps(str(value))

    for section, body in data.items():
        if section == "repos":
            for key, repo in body.items():
                lines.append(f"[repos.{key}]")
                for name, value in repo.items():
                    if name == "post_create":
                        continue
                    lines.append(f"{name} = {render(value)}")
                lines.append("")
            continue
        lines.append(f"[{section}]")
        for name, value in body.items():
            if value is None:
                continue
            lines.append(f"{name} = {render(value)}")
        lines.append("")
    return "\n".join(lines)


def run_cli(args: list[str], config_file: Path) -> int:
    return main(["--config", str(config_file), *args])


# -- usage errors (exit 2) --------------------------------------------------


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == EXIT_USAGE


def test_an_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main(["nonsense"])
    assert caught.value.code == EXIT_USAGE


def test_a_bad_effect_level_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main(["run", "--effect-level", "wat"])
    assert caught.value.code == EXIT_USAGE


def test_dry_run_conflicting_with_an_effect_level_is_a_usage_error(config_file, capsys):
    code = run_cli(["run", "--dry-run", "--effect-level", "live"], config_file)
    assert code == EXIT_USAGE
    assert "conflicts with" in capsys.readouterr().err


def test_a_non_integer_item_id_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main(["show", "not-a-number"])
    assert caught.value.code == EXIT_USAGE


def test_worktree_without_a_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as caught:
        main(["worktree"])
    assert caught.value.code == EXIT_USAGE


# -- precondition failures (exit 3) -----------------------------------------


def test_a_missing_config_file_exits_three(tmp_path, capsys):
    code = main(["--config", str(tmp_path / "absent.toml"), "status"])
    assert code == EXIT_PRECONDITION
    assert "not found" in capsys.readouterr().err


def test_an_invalid_config_exits_three_listing_every_problem(tmp_path, capsys):
    bad = tmp_path / "config.toml"
    bad.write_text(
        '[daemon]\neffect_level = "nope"\ntick_seconds = 0\n[github]\nauthor = ""\n',
        encoding="utf-8",
    )
    code = main(["--config", str(bad), "status"])
    assert code == EXIT_PRECONDITION
    err = capsys.readouterr().err
    assert "effect_level" in err
    assert "tick_seconds" in err
    assert "author" in err, "every problem at once, not just the first"


def test_unparseable_toml_exits_three(tmp_path, capsys):
    bad = tmp_path / "config.toml"
    bad.write_text("[daemon\n", encoding="utf-8")
    assert main(["--config", str(bad), "status"]) == EXIT_PRECONDITION
    assert "TOML parse error" in capsys.readouterr().err


def test_onboarding_a_malformed_key_is_a_refusal_not_a_usage_error(config_file, capsys):
    """Changed by milestone 005. "No section" stopped being a reason to refuse — a
    repository needs no section — so what is left is the key not being ``owner/name``,
    which is a precondition the author can fix rather than a misuse of the command."""
    code = run_cli(["onboard", "not-a-repo"], config_file)
    assert code == EXIT_PRECONDITION
    assert "is not a repository key" in capsys.readouterr().err


# -- operation failures (exit 1) --------------------------------------------


def test_showing_a_missing_item_exits_one(config_file, capsys):
    code = run_cli(["show", "999"], config_file)
    assert code == EXIT_FAILED
    assert "no work item with id 999" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["cancel", "resume", "restart", "abandon", "retry"])
def test_lifecycle_verbs_on_a_missing_item_exit_one(command, config_file, capsys):
    code = run_cli([command, "999"], config_file)
    assert code == EXIT_FAILED
    assert "999" in capsys.readouterr().err


def test_acknowledging_a_missing_anomaly_exits_one(config_file, capsys):
    code = run_cli(["anomalies", "--acknowledge", "42"], config_file)
    assert code == EXIT_FAILED
    assert "no unacknowledged anomaly" in capsys.readouterr().err


def test_removing_a_worktree_for_a_missing_item_exits_one(config_file, capsys):
    assert run_cli(["worktree", "remove", "999"], config_file) == EXIT_FAILED


def test_an_unparseable_log_duration_is_a_usage_error(config_file, capsys):
    code = run_cli(["log", "--since", "10 fortnights"], config_file)
    assert code == EXIT_USAGE
    assert "unknown duration" in capsys.readouterr().err


# -- check failure (exit 4) -------------------------------------------------


def test_health_with_no_heartbeat_exits_four(config_file, capsys):
    """The dead-man's switch's own contract: 0 if fresh, 4 if stale or absent."""
    code = run_cli(["health"], config_file)
    assert code == EXIT_CHECK_FAILED
    assert "STALE" in capsys.readouterr().err


def test_health_with_a_fresh_heartbeat_exits_zero(config_file, layout, capsys):
    from robot_army import health

    health.write_heartbeat(
        layout.heartbeat_path, effect_level="live", activity="idle", cycles=1
    )
    assert run_cli(["health"], config_file) == EXIT_OK
    assert "ok:" in capsys.readouterr().out


def test_health_max_age_is_honoured(config_file, layout):
    from robot_army import health

    health.write_heartbeat(
        layout.heartbeat_path, effect_level="live", activity="idle", cycles=1
    )
    assert run_cli(["health", "--max-age", "0"], config_file) == EXIT_CHECK_FAILED


# -- success paths ----------------------------------------------------------


def test_status_on_an_empty_database_succeeds(config_file, capsys):
    assert run_cli(["status"], config_file) == EXIT_OK
    out = capsys.readouterr().out
    assert "effect level" in out
    assert "no work items yet" in out


def test_repos_lists_the_configured_repository(config_file, capsys):
    assert run_cli(["repos"], config_file) == EXIT_OK
    out = capsys.readouterr().out
    assert "demo" in out
    assert "NO" in out, "an un-onboarded repository must be visibly un-onboarded"


def test_anomalies_names_every_kind_it_can_raise(config_file, capsys):
    """FR-065, T135: all named kinds surfaced, not only the ones seen so far."""
    assert run_cli(["anomalies"], config_file) == EXIT_OK
    out = capsys.readouterr().out
    for kind in ("orphan_session", "no_transcript", "session_id_mismatch"):
        assert kind in out


def test_json_output_is_machine_readable(config_file, capsys):
    assert run_cli(["status", "--json"], config_file) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["effect_level"] == "live"
    assert "counts" in payload and "health" in payload


@pytest.mark.parametrize(
    "command",
    [["status"], ["repos"], ["anomalies"], ["log"], ["doctor"], ["worktree", "list"]],
)
def test_every_read_command_accepts_json(command, config_file):
    """T123: ``--json`` on every read command."""
    parser = build_parser()
    args = parser.parse_args([*command, "--json"])
    assert args.json is True


@pytest.mark.parametrize("command", [["status"], ["repos"], ["log"], ["worktree", "list"]])
def test_listing_commands_accept_include_simulated(command):
    """T124, FR-056: including simulated rows is the explicit act."""
    args = build_parser().parse_args([*command, "--include-simulated"])
    assert args.include_simulated is True


def test_run_accepts_once(config_file):
    """T125."""
    args = build_parser().parse_args(["run", "--once"])
    assert args.once is True


def test_simulated_rows_are_hidden_then_marked(config_file, layout, capsys):
    from robot_army import db

    conn, _ = db.open_database(layout.db_path)
    seed_item(conn, dry_run=True, state=str(WorkItemState.READY))
    conn.close()

    run_cli(["status"], config_file)
    assert "no matching work items" in capsys.readouterr().out

    run_cli(["status", "--include-simulated"], config_file)
    out = capsys.readouterr().out
    assert "ready*" in out
    assert "simulated" in out, "FR-057: simulated rows are always visibly marked"


def test_show_renders_an_items_history_and_resume_signals(config_file, layout, capsys):
    from robot_army import db

    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn)
    conn.close()

    assert run_cli(["show", str(item_id)], config_file) == EXIT_OK
    out = capsys.readouterr().out
    assert "state history" in out
    assert "resume-decision signals" in out
    assert "no session attempts yet" in out


def test_show_names_the_pull_requests_outside_the_never_stored_block(
    config_file, layout, capsys
):
    """Issue #143. The pull requests are read back from the row, so printing them under
    "computed now, never stored" would assert the opposite of the truth about them — and
    dumping the list into that block puts a Python repr in user-facing output."""
    from robot_army import db

    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn)
    with db.transaction(conn):
        db.record_pull_requests(
            conn,
            item_id,
            found='[{"number":144,"url":"https://github.com/x/demo/pull/144","state":"merged"}]',
            at="2026-09-05T22:00:00Z",
        )
    conn.close()

    assert run_cli(["show", str(item_id)], config_file) == EXIT_OK
    out = capsys.readouterr().out

    assert "#144 merged https://github.com/x/demo/pull/144" in out
    assert "pull_requests " not in out, (
        "the raw dict must not reach the signals block"
    )
    assert "{'number'" not in out, "no Python repr in user-facing output"


def test_show_distinguishes_an_unasked_question_from_an_empty_answer(
    config_file, layout, capsys
):
    from robot_army import db

    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn)
    conn.close()

    assert run_cli(["show", str(item_id)], config_file) == EXIT_OK
    assert "(not checked)" in capsys.readouterr().out


def test_the_exit_codes_are_the_documented_five():
    """A change here means contracts/cli.md changed, which should be deliberate."""
    assert (EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PRECONDITION, EXIT_CHECK_FAILED) == (
        0,
        1,
        2,
        3,
        4,
    )


def test_every_operation_returns_a_result_with_an_exit_code():
    """The CLI decides nothing; it renders a ``Result`` and returns its code."""
    import inspect

    verbs = [
        name
        for name, obj in vars(operations).items()
        if inspect.isfunction(obj)
        and not name.startswith("_")
        and obj.__module__ == operations.__name__
        and "ctx" in inspect.signature(obj).parameters
    ]
    assert len(verbs) >= 15, verbs
    for name in verbs:
        annotation = inspect.signature(getattr(operations, name)).return_annotation
        assert annotation in ("Result", "Result | None", "dict[str, Any]", "Iterator[str]"), (
            f"{name} returns {annotation}"
        )


def test_doctor_never_prints_environment_variable_values(config_file, capsys, monkeypatch):
    """`doctor` output gets pasted into issues, and several CLAUDE_CODE_* variables carry
    session tokens. The audit log's redaction choke point does not cover stdout, so this
    is asserted separately."""
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "f3f25f692d27e1a947153c920d60d923")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")

    run_cli(["doctor"], config_file)
    output = capsys.readouterr()
    combined = output.out + output.err

    assert "CLAUDE_CODE_MESSAGING_TOKEN" in combined, "the variable's presence is the signal"
    assert "f3f25f692d27e1a947153c920d60d923" not in combined, "its value must not appear"


def test_doctor_flags_the_transcript_killing_variable(config_file, capsys, monkeypatch):
    """M0 F19, the finding that cost the spike the most time."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    assert run_cli(["doctor"], config_file) != EXIT_OK
    combined = capsys.readouterr()
    assert "CLAUDE_CODE_CHILD_SESSION" in combined.out + combined.err


def test_doctor_probes_the_real_terminal_socket_not_the_wired_one(
    config_file, capsys, monkeypatch
):
    """`doctor` reports on the machine. At a simulated effect level the wired probe would
    answer with a fake socket, which is the opposite of what a diagnostic is for."""
    monkeypatch.delenv("CLAUDE_CODE_CHILD_SESSION", raising=False)
    run_cli(["doctor"], config_file)
    combined = capsys.readouterr()
    assert "simulated-kitty" not in combined.out + combined.err


# -- milestone 002 verbs ----------------------------------------------------


def test_pause_and_unpause_report_the_resulting_state(config_file, capsys):
    """Both work whether or not the daemon is running: they write to the database, which
    the daemon reads before each dispatch decision."""
    assert main(["--config", str(config_file), "pause"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "dispatch paused at" in out
    assert "survives a daemon restart" in out

    assert main(["--config", str(config_file), "unpause"]) == EXIT_OK
    assert "dispatch resumed" in capsys.readouterr().out


def test_pausing_twice_is_not_an_error(config_file, capsys):
    """FR-033: a redundant pause is a reported no-op. The existing pause is the answer."""
    main(["--config", str(config_file), "pause"])
    capsys.readouterr()
    assert main(["--config", str(config_file), "pause"]) == EXIT_OK
    assert "already paused" in capsys.readouterr().out


def test_status_shows_the_pause_after_the_verb_set_it(config_file, capsys):
    main(["--config", str(config_file), "pause"])
    capsys.readouterr()
    main(["--config", str(config_file), "status"])
    assert "PAUSED since" in capsys.readouterr().out


def test_attach_with_no_running_session_exits_three(config_file, layout, capsys):
    """contracts/cli-additions.md: exit 3 when the item has no session in ``running``."""
    from robot_army import db

    conn, _ = db.open_database(layout.db_path)
    item_id = seed_item(conn, state="interrupted")
    conn.close()

    assert main(["--config", str(config_file), "attach", str(item_id)]) == EXIT_PRECONDITION
    assert "no running session" in capsys.readouterr().err


def test_attach_on_a_missing_item_exits_one(config_file, capsys):
    assert main(["--config", str(config_file), "attach", "4242"]) == EXIT_FAILED
    assert "no work item with id 4242" in capsys.readouterr().err


def test_serve_refuses_a_globally_routable_bind_address(config_file, capsys):
    """FR-004, from the terminal: exit 3, and nothing listening."""
    assert (
        main(["--config", str(config_file), "serve", "--bind", "8.8.8.8"]) == EXIT_PRECONDITION
    )
    err = capsys.readouterr().err
    assert "globally routable" in err
    assert "no authentication by design" in err


def test_serve_reports_every_precondition_at_once(config_file, layout, capsys):
    """Fixing one per restart is a poor experience at 2am, which is the audience named."""
    from robot_army import db

    conn = db.connect(layout.db_path)
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    assert main(["--config", str(config_file), "serve", "--bind", "8.8.8.8"]) == EXIT_PRECONDITION
    err = capsys.readouterr().err
    assert "schema" in err
    assert "globally routable" in err


def test_serve_accepts_bind_and_port_overrides():
    args = build_parser().parse_args(["serve", "--bind", "192.168.1.20", "--port", "9001"])
    assert args.bind == "192.168.1.20"
    assert args.port == 9001


def test_pause_unpause_and_attach_accept_json():
    for command in (["pause"], ["unpause"], ["attach", "1"]):
        assert build_parser().parse_args([*command, "--json"]).json is True


def test_every_web_control_has_a_terminal_verb_here(config_file):
    """SC-011 from the other side: the CLI half of the enumeration in test_web_routing."""
    parser = build_parser()
    verbs = set(parser._subparsers._group_actions[0].choices)
    for verb in ("serve", "pause", "unpause", "attach", "resume", "restart", "abandon",
                 "cancel", "retry", "anomalies", "poll", "reconcile",
                 # issue #117. `holds` has no web control of its own — the queue page
                 # carries the repository notice — but it is the terminal answer to
                 # "what is held", which the Operating Constraints require to exist.
                 "hold", "unhold", "holds"):
        assert verb in verbs, verb


# -- `--state` filters reject what they cannot parse (milestone 003) --------


@pytest.mark.parametrize("verb", ["cards", "status"])
def test_an_invalid_state_filter_is_a_usage_error_not_a_traceback(config_file, verb, capsys):
    """The value reaches ``WorkItemState(...)`` / ``CardState(...)``, which raises a bare
    ``ValueError``. ``main()`` catches only ``PreconditionFailed`` and ``KeyboardInterrupt``,
    so an unrecognised state escaped it entirely and printed a raw Python traceback — where
    the exit-code table promises a usage error.

    argparse now refuses it before it can get that far, and lists the valid values.
    """
    with pytest.raises(SystemExit) as caught:
        run_cli([verb, "--state", "bogus"], config_file)
    assert caught.value.code == EXIT_USAGE
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("verb", "state"), [("cards", "needs_info"), ("status", "ready")]
)
def test_a_valid_state_filter_is_still_accepted(config_file, verb, state):
    """Guards the guard: a `choices=` list that omitted a real state would refuse valid
    usage, which is the other way to get this wrong."""
    assert run_cli([verb, "--state", state], config_file) in (EXIT_OK, EXIT_PRECONDITION)


# -- milestone 011: the machine-readable stream, and who prompts where ------


def test_json_output_reaches_stdout_even_when_the_command_failed(config_file, capsys):
    """011 FR-012. ``--json``'s help says "machine-readable output on stdout"; it used to
    mean that only when the exit code was 0, sending a failing run's document to stderr.

    That is now the stream `onboard`'s prompt writes to, so a declined `--json` run would
    have put the question and the document together and neither would have parsed."""
    assert run_cli(["show", "9999", "--json"], config_file) != EXIT_OK
    captured = capsys.readouterr()

    document = json.loads(captured.out)  # parses at all — that is the claim
    assert isinstance(document, dict)
    assert captured.err == "", "nothing human-readable alongside it"


def test_human_readable_failures_still_go_to_stderr(config_file, capsys):
    """The other half of the same change: only the machine-readable stream moved."""
    assert run_cli(["show", "9999"], config_file) != EXIT_OK
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "9999" in captured.err


def test_onboarding_asks_its_question_on_stderr(capsys, monkeypatch):
    """011 FR-012 again, at the source. ``input(prompt)`` writes the prompt to stdout,
    where a ``--json`` document lives. The question is not the command's output."""
    monkeypatch.setattr("builtins.input", lambda: "y")

    answer = operations._ask("Approve jantman/demo for dispatch? [y/N] ")
    captured = capsys.readouterr()

    assert answer == "y"
    assert captured.out == "", "nothing on the machine-readable stream"
    assert captured.err == "Approve jantman/demo for dispatch? [y/N] "


def test_onboard_prompts_through_that_helper_and_its_neighbours_do_not():
    """011 FR-014. Onboarding is the only command with a screen above its question, so it
    is the only one whose prompt moved. `cancel`, `purge-simulated` and
    `worktree remove --force` each ask something self-contained and are left alone —
    an asymmetry that is cheaper than changing three commands to fix one."""
    import inspect

    defaults = {
        name: inspect.signature(getattr(operations, name)).parameters["confirm"].default
        for name in ("onboard", "cancel", "purge_simulated", "worktree_remove")
    }

    assert defaults["onboard"] is operations._ask
    assert defaults["cancel"] is input
    assert defaults["purge_simulated"] is input
    assert defaults["worktree_remove"] is input


def ctx_over(conn, audit, config_file):
    """A context over the fixture database, for the operations that prompt."""
    return operations.Context(
        config=load_config(config_file),
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit),
        effect_level=EffectLevel.LIVE,
    )


def test_the_other_prompts_keep_their_wording_and_their_stdout_stream(
    conn, audit, config_file, capsys
):
    """FR-014 concretely. Nothing in the suite pinned these strings before, so nothing
    would have noticed if the stderr change had been applied across the board."""
    seed_item(conn, dry_run=True)
    asked: list[str] = []

    result = operations.purge_simulated(
        ctx_over(conn, audit, config_file),
        confirm=lambda prompt: asked.append(prompt) or "n",
    )
    captured = capsys.readouterr()

    assert asked and asked[0].startswith("Delete 1 simulated work item(s)")
    assert asked[0].endswith("? [y/N] ")
    assert result.code == EXIT_FAILED and result.lines == ["aborted"]
    assert captured.err == "", "this prompt did not move; only onboarding's did"


def test_onboard_accepts_json_because_011_describes_what_it_does_in_that_mode():
    """The gap review caught on #19. `cli._dispatch` suppressed onboarding's approval
    screen "in --json mode", `_ask` moved the prompt to stderr to keep that document
    parseable, and contracts/onboard-output.md described both — while `onboard` had no
    `--json` flag at all, so the branch was unreachable and the mode unenterable.

    Asserting the *rendered document* elsewhere could never have caught it: every such
    test used a command that already had the flag. This one asks the parser."""
    assert build_parser().parse_args(["onboard", "some/repo", "--json"]).json is True
    assert build_parser().parse_args(["onboard", "some/repo"]).json is False


@pytest.mark.parametrize(
    "command",
    [["onboard", "some/repo"], ["purge-simulated"], ["pause"], ["attach", "1"]],
)
def test_every_command_that_can_prompt_can_also_be_asked_for_json(command):
    """Guards the guard. A command that prompts and cannot produce a document is a
    command whose prompt stream nobody has had to think about."""
    assert build_parser().parse_args([*command, "--json"]).json is True


# -- issue #120: a refused launch is a precondition failure, not a failure ---


@pytest.mark.parametrize("command", ["resume", "restart"])
def test_a_gate_refusal_exits_three_not_one(command, config_file, conn, capsys, monkeypatch):
    """FR-014, and the distinction the author acts on.

    ``1`` means the launch was attempted and the item now carries a failure reason to go
    and read. ``3`` means nothing was attempted, the item is untouched, and the reason is
    already on the screen — so sending them to ``robot-army show`` would send them to an
    empty field.
    """
    from robot_army import dispatch, ordering

    item_id = seed_item(conn, state=str(WorkItemState.INTERRUPTED))
    _seed_ended_session(conn, item_id)

    def refuse(*_args, **_kwargs):
        raise dispatch.DispatchRefused(
            "dispatch is paused; lift it with `robot-army unpause`",
            hold=ordering.HoldReason.PAUSED,
        )

    monkeypatch.setattr(dispatch, "check_launch_gate", refuse)

    code = run_cli([command, str(item_id)], config_file)

    assert code == EXIT_PRECONDITION
    # stderr, not stdout: the CLI puts any outcome that did not succeed there, and a
    # refusal is an outcome that did not succeed.
    err = capsys.readouterr().err
    assert f"refusing to {command} item {item_id}" in err
    assert "dispatch is paused" in err
    assert "robot-army unpause" in err, "the detail the queue renders, verbatim"


def _seed_ended_session(conn, item_id: int) -> None:
    """``resume`` needs a previous session to restore; ``restart`` does not care."""
    from tests.conftest import seed_session

    seed_session(conn, item_id, state="exited_error", exit_code=130)


@pytest.mark.parametrize("command", ["resume", "restart"])
def test_force_is_off_unless_the_author_asks_for_it_by_name(
    command, config_file, conn, monkeypatch
):
    """FR-022. There is no configuration key that turns the gate off standing; the override
    is per invocation, and its absence must mean absent."""
    from robot_army import dispatch

    item_id = seed_item(conn, state=str(WorkItemState.INTERRUPTED))
    _seed_ended_session(conn, item_id)
    seen: list[bool] = []

    def record_force(*_args, force: bool = False, **_kwargs):
        seen.append(force)
        raise dispatch.DispatchRefused("stop here", hold=None)

    monkeypatch.setattr(dispatch, "check_launch_gate", record_force)

    run_cli([command, str(item_id)], config_file)
    run_cli([command, str(item_id), "--force"], config_file)

    assert seen == [False, True]


@pytest.mark.parametrize("command", ["resume", "restart"])
def test_the_force_flag_says_what_it_does_not_bypass(command):
    """``cancel --force`` already means "skip the confirmation prompt". One word doing two
    jobs across the CLI is only safe while each says which job it is doing."""
    parser = build_parser()
    action = next(
        a
        for a in parser._subparsers._group_actions[0].choices[command]._actions
        if a.dest == "force"
    )
    assert "Does not bypass" in action.help
    assert "author" in action.help
