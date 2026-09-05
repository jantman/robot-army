"""``robot-army example-config``: streams, exit codes, and the atomic write.

Per contracts/cli.md. The property worth stating out loud is the first one: this command
must work when there is no configuration anywhere, because that is the situation it exists
to resolve.
"""

from __future__ import annotations

import json

import pytest

from robot_army.cli import main
from robot_army.exampleconfig import render
from robot_army.operations import EXIT_FAILED, EXIT_OK, EXIT_PRECONDITION, EXIT_USAGE


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No config, no state, nothing of the developer's machine reachable from a test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(home / "run"))
    return home


def test_it_works_with_no_config_file_anywhere(capsys):
    """The point of the whole command. Routed before ``load_config`` for this reason."""
    assert main(["example-config"]) == EXIT_OK
    assert "config file not found" not in capsys.readouterr().err


def test_stdout_carries_the_document_and_nothing_else(capsys):
    """``robot-army example-config > config.toml`` must produce a usable file."""
    assert main(["example-config"]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == render()
    assert captured.err == ""


def test_an_explicit_config_path_is_ignored_rather_than_read(capsys, tmp_path):
    """``--config`` is defined on the root parser but means nothing here.

    A pointer to a file that does not exist must not turn into a failure: nothing about
    this command reads a configuration.
    """
    assert main(["--config", str(tmp_path / "nope.toml"), "example-config"]) == EXIT_OK
    assert capsys.readouterr().out == render()


def test_output_writes_the_file_and_reports_on_stderr(tmp_path, capsys):
    """Nothing on stdout when writing to a file — the confirmation is not the document."""
    destination = tmp_path / "config.toml"
    assert main(["example-config", "--output", str(destination)]) == EXIT_OK
    captured = capsys.readouterr()
    assert destination.read_text(encoding="utf-8") == render()
    assert captured.out == ""
    assert str(destination) in captured.err


def test_an_existing_file_is_refused_and_left_alone(tmp_path, capsys):
    """Exit 3: the operation did not fail, a precondition was not met."""
    destination = tmp_path / "config.toml"
    destination.write_text("# mine, hand-written\n", encoding="utf-8")
    assert main(["example-config", "--output", str(destination)]) == EXIT_PRECONDITION
    assert destination.read_text(encoding="utf-8") == "# mine, hand-written\n"
    assert "--force" in capsys.readouterr().err


def test_force_replaces_an_existing_file(tmp_path):
    destination = tmp_path / "config.toml"
    destination.write_text("# mine\n", encoding="utf-8")
    assert main(["example-config", "--output", str(destination), "--force"]) == EXIT_OK
    assert destination.read_text(encoding="utf-8") == render()


def test_force_without_output_is_a_usage_error(capsys):
    """There is nothing to force when the destination is a stream."""
    assert main(["example-config", "--force"]) == EXIT_USAGE
    assert capsys.readouterr().err.strip().startswith("--force applies to --output")


def test_a_write_failure_is_reported_not_swallowed(tmp_path, capsys):
    """Silent failure is forbidden; an unwritable destination exits 1 and says why."""
    destination = tmp_path / "readonly" / "config.toml"
    destination.parent.mkdir()
    destination.parent.chmod(0o500)
    try:
        assert main(["example-config", "--output", str(destination)]) == EXIT_FAILED
        assert "could not write" in capsys.readouterr().err
    finally:
        destination.parent.chmod(0o700)


def test_no_partial_file_survives_a_render_failure(tmp_path, monkeypatch):
    """The document is built before the first byte is written, so an error truncates nothing.

    The destination is usually the file the daemon will not start without, and a half
    written config.toml is a daemon that refuses to start for a reason that looks like a
    syntax error nobody typed.
    """
    destination = tmp_path / "config.toml"
    destination.write_text("# mine\n", encoding="utf-8")
    monkeypatch.setattr(
        "robot_army.exampleconfig.render", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        main(["example-config", "--output", str(destination), "--force"])
    assert destination.read_text(encoding="utf-8") == "# mine\n"


def test_no_temporary_file_is_left_behind(tmp_path):
    """The write goes through a temp file beside the destination; it must not survive."""
    directory = tmp_path / "target"
    directory.mkdir()
    destination = directory / "config.toml"
    assert main(["example-config", "--output", str(destination)]) == EXIT_OK
    assert [p.name for p in directory.iterdir()] == ["config.toml"]


# -- the audit record (FR-026) ----------------------------------------------------------


def records(home):
    log_dir = home / ".local/state/robot-army/logs"
    return [
        json.loads(line)
        for path in sorted(log_dir.glob("audit-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_writing_a_file_is_recorded(tmp_path, isolated_home):
    """A state change outside the process leaves a record, with every field named."""
    destination = tmp_path / "config.toml"
    main(["example-config", "--output", str(destination)])
    written = [r for r in records(isolated_home) if r["action"] == "example_config.write"]
    assert len(written) == 1
    assert written[0]["outcome"] == "success"
    assert written[0]["target"] == str(destination)
    assert written[0]["detail"]["force"] is False
    assert written[0]["ts"].endswith("Z")


def test_a_refusal_is_recorded_as_a_failure(tmp_path, isolated_home):
    """ "I ran it and nothing changed" is exactly the question a log has to answer."""
    destination = tmp_path / "config.toml"
    destination.write_text("# mine\n", encoding="utf-8")
    main(["example-config", "--output", str(destination)])
    written = [r for r in records(isolated_home) if r["action"] == "example_config.write"]
    assert [r["outcome"] for r in written] == ["failure"]
    assert written[0]["detail"]["error"] == "file exists"


def test_the_recorded_target_is_absolute(tmp_path, isolated_home, monkeypatch):
    """A relative path in the log is only readable if you also know the working directory."""
    monkeypatch.chdir(tmp_path)
    main(["example-config", "--output", "config.toml"])
    written = [r for r in records(isolated_home) if r["action"] == "example_config.write"]
    assert written[0]["target"] == str(tmp_path / "config.toml")


def test_stdout_writes_no_record(isolated_home):
    """The documented Principle III exception: nothing outside the process changed."""
    main(["example-config"])
    assert not [r for r in records(isolated_home) if r["action"] == "example_config.write"]


def test_an_unwritable_audit_log_does_not_fail_the_write(tmp_path, isolated_home, capsys):
    """The config is the point; the log is not allowed to cost you it.

    Regression for a review finding on #137. ``_record`` caught ``OSError`` and immediately
    re-raised it, under a comment claiming the opposite. An unwritable state directory then
    turned a config that had already been written correctly — ``os.replace`` having run — into
    ``could not write <path>`` and a non-zero exit.
    """
    state = isolated_home / ".local/state"
    state.mkdir(parents=True)
    state.chmod(0o500)
    destination = tmp_path / "target" / "config.toml"
    destination.parent.mkdir()
    try:
        assert main(["example-config", "--output", str(destination)]) == EXIT_OK
        assert destination.read_text(encoding="utf-8") == render()
    finally:
        state.chmod(0o700)


def test_an_unwritable_audit_log_is_announced_rather_than_swallowed(
    tmp_path, isolated_home, capsys
):
    """Not failing is not the same as not saying. Silence here would be a Principle III gap."""
    state = isolated_home / ".local/state"
    state.mkdir(parents=True)
    state.chmod(0o500)
    destination = tmp_path / "target" / "config.toml"
    destination.parent.mkdir()
    try:
        main(["example-config", "--output", str(destination)])
        err = capsys.readouterr().err
        assert "audit log" in err
        assert "wrote" in err
    finally:
        state.chmod(0o700)


def test_the_refusal_message_survives_an_unwritable_audit_log(tmp_path, isolated_home, capsys):
    """The other half of the same bug: the failure-path record pre-empted the real message."""
    state = isolated_home / ".local/state"
    state.mkdir(parents=True)
    state.chmod(0o500)
    destination = tmp_path / "config.toml"
    destination.write_text("# mine\n", encoding="utf-8")
    try:
        assert main(["example-config", "--output", str(destination)]) == EXIT_PRECONDITION
        assert "--force" in capsys.readouterr().err
        assert destination.read_text(encoding="utf-8") == "# mine\n"
    finally:
        state.chmod(0o700)
