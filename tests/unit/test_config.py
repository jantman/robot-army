"""Config validation: aggregate reporting, credential rejection, permissions (T024)."""

from __future__ import annotations

import pytest
from tests.conftest import config_dict, monkey_token

from robot_army.config import ConfigError, parse
from robot_army.effects import EffectLevel


def build(repo_clone, layout, tmp_path, **overrides):
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def test_a_valid_config_parses(repo_clone, layout, tmp_path):
    config = build(repo_clone, layout, tmp_path)
    assert config.daemon.effect_level is EffectLevel.LIVE
    assert config.github.author == "jantman"
    assert "demo" in config.repos


def test_every_problem_is_reported_at_once(repo_clone, layout, tmp_path):
    """Fixing one typo per restart is a poor experience at 2am, which is the audience
    the constitution names — so validation aggregates."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            daemon={"effect_level": "nope", "tick_seconds": 0, "max_concurrent_sessions": 0},
            github={"author": "", "token_env": "X", "token_file": "/nonexistent"},
            worker={"permission_mode": "wat"},
        )
    problems = caught.value.problems
    assert len(problems) >= 5, problems
    joined = "\n".join(problems)
    assert "effect_level" in joined
    assert "tick_seconds" in joined
    assert "author" in joined
    assert "permission_mode" in joined
    assert "exactly one of token_env or token_file" in joined


def test_a_literal_token_is_an_error_not_a_warning(repo_clone, layout, tmp_path):
    """The repository is public (Principle V). A config that "works" with a token in it
    is a config that will eventually be pasted somewhere."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            github={"token_env": "ghp_" + "a" * 36, "author": "jantman"},
        )
    assert any("literal credential" in p for p in caught.value.problems)


def test_token_file_must_be_mode_0600(repo_clone, layout, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            github={"token_env": None, "token_file": str(token_file)},
        )
    assert any("mode 0600" in p for p in caught.value.problems)

    token_file.chmod(0o600)
    config = build(
        repo_clone, layout, tmp_path, github={"token_env": None, "token_file": str(token_file)}
    )
    assert config.github.token_file == token_file


def test_unknown_key_inside_repos_is_an_error(repo_clone, layout, tmp_path):
    """A typo there silently disables a preparation step and produces a broken worktree,
    which is why it is an error where a top-level unknown key is only a warning."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={"demo": {"path": str(repo_clone), "post_creat": []}},
        )
    assert any("unknown key 'post_creat'" in p for p in caught.value.problems)


def test_unknown_top_level_key_is_only_a_warning(repo_clone, layout, tmp_path):
    """A config written for a later milestone must still start."""
    config = build(repo_clone, layout, tmp_path, daemon={"future_option": 1})
    assert any("future_option" in w for w in config.warnings)


def test_unknown_top_level_section_is_only_a_warning(repo_clone, layout, tmp_path):
    config = build(repo_clone, layout, tmp_path, milestone002={"port": 8080})
    assert any("milestone002" in w for w in config.warnings)


def test_author_cannot_be_disabled_or_blank(repo_clone, layout, tmp_path):
    """FR-007 calls this a security boundary; there is deliberately no "any author"."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, github={"author": "   "})
    assert any("security boundary" in p for p in caught.value.problems)


def test_intervals_must_not_be_shorter_than_the_tick(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, daemon={"tick_seconds": 30, "poll_seconds": 5})
    assert any("poll_seconds" in p for p in caught.value.problems)


def test_missing_repo_path_is_an_error(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, repos={"gone": {"path": str(tmp_path / "nope")}})
    assert any("does not exist" in p for p in caught.value.problems)


def test_non_git_repo_path_is_an_error(repo_clone, layout, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, repos={"plain": {"path": str(plain)}})
    assert any("not a git repository" in p for p in caught.value.problems)


def test_post_create_steps_parse_and_default_their_timeout(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        hooks={"default_timeout_seconds": 77},
        repos={
            "demo": {
                "path": str(repo_clone),
                "post_create": [{"run": "make setup"}, {"link": ".env", "timeout": 5}],
            }
        },
    )
    steps = config.repos["demo"].post_create
    assert [s.kind for s in steps] == ["run", "link"]
    assert steps[0].timeout == 77, "an unspecified timeout inherits the default"
    assert steps[1].timeout == 5


def test_a_step_must_set_exactly_one_form(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={"demo": {"path": str(repo_clone), "post_create": [{"run": "x", "link": "y"}]}},
        )
    assert any("exactly one of run, link, copy" in p for p in caught.value.problems)


def test_a_step_with_no_form_is_an_error(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={"demo": {"path": str(repo_clone), "post_create": [{"timeout": 5}]}},
        )
    assert any("exactly one of run, link, copy" in p for p in caught.value.problems)


def test_socket_glob_without_a_wildcard_warns(repo_clone, layout, tmp_path):
    """kitty appends its PID to listen_on, so a fixed path can only ever be stale."""
    config = build(repo_clone, layout, tmp_path, terminal={"socket_glob": "/tmp/mykitty"})
    assert any("no wildcard" in w for w in config.warnings)


def test_dispatching_max_age_shorter_than_preparation_warns(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        daemon={"dispatching_max_age_seconds": 10},
        repos={
            "demo": {
                "path": str(repo_clone),
                "post_create": [{"run": "sleep 1", "timeout": 300}],
            }
        },
    )
    assert any("dispatching_max_age_seconds" in w for w in config.warnings)


def test_per_repo_overrides_win_over_worker_defaults(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        worker={"permission_mode": "auto", "model": "base-model"},
        repos={
            "demo": {
                "path": str(repo_clone),
                "permission_mode": "acceptEdits",
                "base_branch": "trunk",
            }
        },
    )
    assert config.permission_mode_for("demo") == "acceptEdits"
    assert config.model_for("demo") == "base-model"
    assert config.base_branch_for("demo") == "trunk"


def test_missing_config_file_is_reported_clearly(tmp_path):
    from robot_army.config import load

    with pytest.raises(ConfigError) as caught:
        load(tmp_path / "absent.toml")
    assert any("not found" in p for p in caught.value.problems)


def test_token_is_read_from_the_environment_not_the_file(repo_clone, layout, tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOT_ARMY_TEST_TOKEN", "from-the-environment")
    config = build(repo_clone, layout, tmp_path)
    assert config.github.read_token() == "from-the-environment"


def test_an_empty_token_env_is_an_error_at_read_time(repo_clone, layout, tmp_path, monkeypatch):
    """"I could not get a token" must not silently become "I have an empty token"."""
    config = build(repo_clone, layout, tmp_path)
    monkeypatch.setenv("ROBOT_ARMY_TEST_TOKEN", "")
    with pytest.raises(ConfigError):
        config.github.read_token()


# -- [web] (milestone 002) --------------------------------------------------


def test_web_section_defaults_to_loopback(repo_clone, layout, tmp_path):
    """The shipped default must not be reachable from the network.

    Under FR-003 the bind address *is* the access policy, so an unconfigured install
    being loopback-only is a requirement rather than a convenience.
    """
    config = build(repo_clone, layout, tmp_path)
    assert config.web.bind == "127.0.0.1"
    assert config.web.port == 8420
    assert config.web.refresh_seconds == 10


def test_web_section_overrides_are_honoured(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        web={"bind": "192.168.1.20", "port": 9001, "refresh_seconds": 30},
    )
    assert config.web.bind == "192.168.1.20"
    assert config.web.port == 9001
    assert config.web.refresh_seconds == 30


def test_a_non_integer_web_port_is_rejected(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, web={"port": "8420"})
    assert any("[web] port must be an integer" in p for p in caught.value.problems)


def test_an_out_of_range_web_port_is_rejected(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, web={"port": 70000})
    assert any("65535" in p for p in caught.value.problems)


def test_an_unknown_web_key_is_a_warning_not_an_error(repo_clone, layout, tmp_path):
    """Top-level sections warn rather than fail, so a config written for a later
    milestone still starts."""
    config = build(repo_clone, layout, tmp_path, web={"tls": True})
    assert any("[web].tls" in w for w in config.warnings)


# -- [trello] (milestone 003) -----------------------------------------------


def trello_section(**overrides):
    """A minimally valid ``[trello]`` table, so each test overrides only what it tests."""
    base = {
        "board_id": "5f3a0000000000000000000a",
        "key_env": "TRELLO_TEST_KEY",
        "token_env": "TRELLO_TEST_TOKEN",
    }
    base.update(overrides)
    return base


def test_no_trello_section_leaves_the_source_inert(repo_clone, layout, tmp_path):
    """FR-001. ``None`` rather than a disabled-but-present config: an unconfigured
    installation has no section to read, so no board request can be constructed."""
    config = build(repo_clone, layout, tmp_path)
    assert config.trello is None


def test_trello_defaults_match_the_contract(repo_clone, layout, tmp_path):
    config = build(repo_clone, layout, tmp_path, trello=trello_section())
    assert config.trello is not None
    assert config.trello.board_id == "5f3a0000000000000000000a"
    assert config.trello.label == "AI-task"
    assert config.trello.in_progress_list == "In Progress"
    assert config.trello.done_list == "Done"
    # 300, not GitHub's 60: there is no conditional-request economy here (R13).
    assert config.trello.poll_seconds == 300
    assert config.trello.timeout_seconds == 20
    assert config.trello.max_retries == 4
    assert config.trello.api_base == "https://api.trello.com/1"


def test_trello_overrides_are_honoured(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        trello=trello_section(
            label="triage",
            in_progress_list="Doing",
            done_list="Shipped",
            poll_seconds=60,
            api_base="https://api.trello.example/1/",
        ),
    )
    assert config.trello.label == "triage"
    assert config.trello.in_progress_list == "Doing"
    assert config.trello.done_list == "Shipped"
    assert config.trello.poll_seconds == 60
    # The trailing slash is stripped, as it is for [github] api_base.
    assert config.trello.api_base == "https://api.trello.example/1"


def test_a_trello_section_without_a_board_id_is_rejected(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=trello_section(board_id=""))
    assert any("board_id is required" in p for p in caught.value.problems)


@pytest.mark.parametrize("what", ["key", "token"])
def test_exactly_one_credential_source_is_required(repo_clone, layout, tmp_path, what):
    """Neither is a board that cannot authenticate; both is an ambiguity about which
    one is in force, and silently preferring one would make the other a lie."""
    section = trello_section()
    section.pop(f"{what}_env")
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=section)
    assert any(f"exactly one of {what}_env or {what}_file" in p for p in caught.value.problems)

    both = trello_section(**{f"{what}_file": str(tmp_path / "secret")})
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=both)
    assert any(f"exactly one of {what}_env or {what}_file" in p for p in caught.value.problems)


def test_a_literal_credential_in_key_env_is_refused(repo_clone, layout, tmp_path):
    """The env *name* goes in the config, never the secret — the same rule, and the same
    message, as the [github] equivalents. The repository is public (Principle V)."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            trello=trello_section(key_env="ghp_" + "a" * 36),
        )
    assert any("literal credential" in p and "[trello]" in p for p in caught.value.problems)


def test_a_credential_file_must_exist(repo_clone, layout, tmp_path):
    section = trello_section(token_file=str(tmp_path / "absent"))
    section.pop("token_env")
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=section)
    assert any("token_file does not exist" in p for p in caught.value.problems)


def test_a_credential_file_must_be_mode_0600(repo_clone, layout, tmp_path):
    secret = tmp_path / "trello-token"
    secret.write_text("s3cret\n", encoding="utf-8")
    secret.chmod(0o644)
    section = trello_section(token_file=str(secret))
    section.pop("token_env")
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=section)
    assert any("must be mode 0600" in p for p in caught.value.problems)


def test_a_mode_0600_credential_file_is_accepted_and_read_lazily(repo_clone, layout, tmp_path):
    secret = tmp_path / "trello-token"
    secret.write_text("s3cret\n", encoding="utf-8")
    secret.chmod(0o600)
    section = trello_section(token_file=str(secret))
    section.pop("token_env")
    config = build(repo_clone, layout, tmp_path, trello=section)
    assert config.trello.token_file == secret
    # Resolved when needed, never stored in the config object itself.
    assert config.trello.read_token() == "s3cret"


def test_an_unknown_trello_key_is_an_error_not_a_warning(repo_clone, layout, tmp_path):
    """Unlike the top level. A typo in a board section that exists silently polls the
    wrong thing and looks healthy while doing it — the [repos.*] rule, for the same
    reason."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=trello_section(labl="AI-task"))
    assert any("unknown key 'labl'" in p for p in caught.value.problems)


def test_a_non_integer_trello_poll_interval_is_rejected(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, trello=trello_section(poll_seconds="300"))
    assert any("[trello] poll_seconds must be an integer" in p for p in caught.value.problems)


def test_reading_a_credential_from_an_empty_variable_is_an_error(repo_clone, layout, tmp_path):
    import os

    os.environ.pop("TRELLO_TEST_KEY", None)
    config = build(repo_clone, layout, tmp_path, trello=trello_section())
    with pytest.raises(ConfigError):
        config.trello.read_key()
