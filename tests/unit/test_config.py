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


#: What a **real** Trello credential looks like. Written out per shape rather than as one
#: example, because the bug this guards against was precisely that the guard had never seen
#: a Trello-shaped secret: it listed only GitHub's prefixes, so a genuine 32-hex API key
#: pasted into `key_env` passed validation in silence.
#:
#: The test below used to paste a *GitHub*-shaped token into a Trello field, which
#: exercised the mechanism and proved nothing about the property. That is the more useful
#: half of the lesson, and the reason these are parametrised by shape.
REAL_TRELLO_CREDENTIALS = {
    "api key (32 hex)": "0123456789abcdef0123456789abcdef",
    "classic token (64 hex)": "f" * 64,
    "uppercase hex token": "A1B2C3D4" * 8,
    "ATTA-prefixed token": "ATTA" + "b" * 40,
}


@pytest.mark.parametrize("shape", sorted(REAL_TRELLO_CREDENTIALS))
def test_a_real_trello_credential_pasted_into_the_config_is_refused(
    repo_clone, layout, tmp_path, shape
):
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            trello=trello_section(key_env=REAL_TRELLO_CREDENTIALS[shape]),
        )
    assert any("literal credential" in p and "[trello]" in p for p in caught.value.problems)


@pytest.mark.parametrize("shape", sorted(REAL_TRELLO_CREDENTIALS))
def test_a_real_trello_credential_is_refused_in_the_token_field_too(
    repo_clone, layout, tmp_path, shape
):
    """Every string value in the section is checked, not only the four credential keys —
    so pasting a secret into the wrong key is caught as well as into the right one."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            trello=trello_section(token_env=REAL_TRELLO_CREDENTIALS[shape]),
        )
    assert any("literal credential" in p for p in caught.value.problems)


def test_a_board_id_is_not_mistaken_for_a_credential(repo_clone, layout, tmp_path):
    """Guards the guard against the other failure: a rule so broad it refuses valid
    config. A Trello board id is 24 hex characters and a short board link is 8, so neither
    collides with the 32- and 64-character patterns."""
    for board_id in ("5f3a0000000000000000000a", "abc12345", "A1B2C3D4E5F6A7B8C9D0E1F2"):
        config = build(repo_clone, layout, tmp_path, trello=trello_section(board_id=board_id))
        assert config.trello.board_id == board_id


def test_a_github_shaped_token_in_a_trello_field_is_still_refused(repo_clone, layout, tmp_path):
    """The original case. Kept, because widening the patterns must not narrow them."""
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


# -- [dispatch] (milestone 004) ---------------------------------------------


def test_dispatch_defaults_match_the_contract(repo_clone, layout, tmp_path):
    config = build(repo_clone, layout, tmp_path)
    assert config.dispatch.order == "oldest-first"
    assert config.dispatch.default_repo_max_sessions == 1


def test_an_absent_dispatch_section_leaves_the_defaults_alone(repo_clone, layout, tmp_path):
    """The no-op case FR-046 depends on: an installation that never heard of milestone
    004 keeps milestone 003's ordering without editing anything."""
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    assert "dispatch" not in raw
    monkey_token()
    config = parse(raw, tmp_path / "config.toml")
    assert config.dispatch.order == "oldest-first"
    assert config.dispatch.default_repo_max_sessions == 1


def test_dispatch_overrides_are_honoured(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        dispatch={"order": "repo-priority", "default_repo_max_sessions": 3},
    )
    assert config.dispatch.order == "repo-priority"
    assert config.dispatch.default_repo_max_sessions == 3


def test_an_unknown_ordering_mode_refuses_to_load(repo_clone, layout, tmp_path):
    """FR-014's named case. Falling back silently would run the author's work in an order
    they did not choose and would not know about."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, dispatch={"order": "newest-first"})
    joined = "\n".join(caught.value.problems)
    assert "[dispatch] order" in joined
    # The message lists what is valid, so the fix does not require reading the source.
    assert "oldest-first" in joined
    assert "repo-priority" in joined


@pytest.mark.parametrize("value", [0, -1])
def test_a_non_positive_default_repo_cap_refuses_to_load(repo_clone, layout, tmp_path, value):
    """Zero would disable every repository silently; negative is meaningless."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, dispatch={"default_repo_max_sessions": value})
    assert any("default_repo_max_sessions" in p for p in caught.value.problems)


def test_a_non_integer_default_repo_cap_refuses_to_load(repo_clone, layout, tmp_path):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, dispatch={"default_repo_max_sessions": "2"})
    assert any(
        "[dispatch] default_repo_max_sessions must be an integer" in p
        for p in caught.value.problems
    )


def test_a_typo_in_the_dispatch_section_is_an_error_not_a_warning(repo_clone, layout, tmp_path):
    """The [repos.*] and [trello] rule, for the same reason: a typo in a section that
    exists is a setting that quietly does nothing while looking applied."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, dispatch={"oder": "repo-priority"})
    assert any("[dispatch] unknown key 'oder'" in p for p in caught.value.problems)


# -- per-repository caps and priority (milestone 004) -----------------------


def test_a_repository_cap_defaults_to_the_dispatch_default(repo_clone, layout, tmp_path):
    config = build(repo_clone, layout, tmp_path)
    assert config.repos["demo"].max_sessions is None
    cap, explicit = config.effective_repo_cap("demo")
    assert (cap, explicit) == (1, False)


def test_an_explicit_repository_cap_is_reported_as_chosen(repo_clone, layout, tmp_path):
    """US2 AS4: "you chose 1" and "1 is what you get" are different answers, and the second
    one needs a pointer to the file the author would edit."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        daemon={"max_concurrent_sessions": 4},
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "max_sessions": 2}},
    )
    assert config.repos["demo"].max_sessions == 2
    assert config.effective_repo_cap("demo") == (2, True)


@pytest.mark.parametrize("value", [0, -1, "2", 1.5])
def test_a_bad_repository_cap_refuses_to_load(repo_clone, layout, tmp_path, value):
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={
                "demo": {"path": str(repo_clone), "base_branch": "main", "max_sessions": value}
            },
        )
    assert any("max_sessions must be a positive integer" in p for p in caught.value.problems)


def test_a_repository_cap_above_the_global_cap_warns_and_takes_the_minimum(
    repo_clone, layout, tmp_path
):
    """Resolvable, and usually a leftover from lowering the global cap — so it warns and
    proceeds, mirroring the existing dispatching_max_age_seconds cross-check rather than
    refusing to start over a harmless over-specification (R17)."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        daemon={"max_concurrent_sessions": 2},
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "max_sessions": 9}},
    )
    assert config.effective_repo_cap("demo") == (2, True)
    assert any("exceeds [daemon] max_concurrent_sessions" in w for w in config.warnings)


def test_a_repository_priority_defaults_to_zero(repo_clone, layout, tmp_path):
    """Equal priority everywhere makes repo-priority degrade to oldest-first, which is the
    harmless reading of a repository nobody has ranked."""
    assert build(repo_clone, layout, tmp_path).repos["demo"].priority == 0


def test_an_explicit_repository_priority_is_honoured(repo_clone, layout, tmp_path):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "priority": 10}},
    )
    assert config.repos["demo"].priority == 10


@pytest.mark.parametrize("value", ["10", 1.5])
def test_a_non_integer_repository_priority_refuses_to_load(repo_clone, layout, tmp_path, value):
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={"demo": {"path": str(repo_clone), "base_branch": "main", "priority": value}},
        )
    assert any("priority must be an integer" in p for p in caught.value.problems)


def test_a_typo_in_a_repo_section_is_still_an_error(repo_clone, layout, tmp_path):
    """The two new keys joined ``_REPO_KEYS``, where an unknown key was already an error.
    ``max_session`` silently capping nothing is exactly the failure that rule prevents."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            repos={
                "demo": {"path": str(repo_clone), "base_branch": "main", "max_session": 2}
            },
        )
    assert any("unknown key 'max_session'" in p for p in caught.value.problems)


# -- [paths] repo_root (milestone 005, T004) --------------------------------


def test_repo_root_defaults_to_the_conventional_location(repo_clone, layout, tmp_path, monkeypatch):
    """``~/GIT`` is the author's convention, and it is a *default* rather than a
    hard-coded path — the value is configurable and every test above overrides it."""
    home = tmp_path / "home"
    (home / "GIT").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkey_token()
    raw = config_dict(repo_clone, layout, tmp_path / "worktrees")
    del raw["paths"]["repo_root"]

    config = parse(raw, tmp_path / "config.toml")

    assert config.repo_root == home / "GIT"


def test_an_explicit_repo_root_overrides_the_default(repo_clone, layout, tmp_path):
    elsewhere = tmp_path / "code"
    elsewhere.mkdir()

    config = build(repo_clone, layout, tmp_path, paths={"repo_root": str(elsewhere)})

    assert config.repo_root == elsewhere


def test_repo_root_expands_a_tilde_like_every_other_path(repo_clone, layout, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "src").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    config = build(repo_clone, layout, tmp_path, paths={"repo_root": "~/src"})

    assert config.repo_root == home / "src"


def test_a_repo_root_that_does_not_exist_refuses_to_load(repo_clone, layout, tmp_path):
    """FR-001: reported here, with every other configuration problem, rather than
    discovered per repository at onboarding time. "Your root is missing" is one message,
    not 227 of them."""
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, paths={"repo_root": str(tmp_path / "absent")})

    assert any("repo_root does not exist" in p for p in caught.value.problems)


def test_a_repo_root_that_is_a_file_refuses_to_load(repo_clone, layout, tmp_path):
    not_a_directory = tmp_path / "file.txt"
    not_a_directory.write_text("x", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, paths={"repo_root": str(not_a_directory)})

    assert any("repo_root is not a directory" in p for p in caught.value.problems)


# -- [hooks] post_create, the shared preparation steps (milestone 005, T053) ----


def test_shared_steps_parse_with_the_same_shape_as_the_per_repository_form(
    repo_clone, layout, tmp_path
):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        hooks={"default_timeout_seconds": 10, "post_create": [{"run": "uv sync"}]},
    )

    assert len(config.hooks.post_create) == 1
    step = config.hooks.post_create[0]
    assert (step.kind, step.value) == ("run", "uv sync")
    assert step.timeout == 10, "default_timeout_seconds applies here exactly as it does there"


def test_an_invalid_shared_step_refuses_to_load(repo_clone, layout, tmp_path):
    """Same rule as ``[repos.*]``: a typo in a step that exists is a step that quietly does
    nothing, which is worse than one that is missing, because it looks applied."""
    with pytest.raises(ConfigError) as caught:
        build(
            repo_clone,
            layout,
            tmp_path,
            hooks={"post_create": [{"run": "uv sync", "timout": 30}]},
        )

    assert any("[hooks] post_create[0] unknown key 'timout'" in p for p in caught.value.problems)


def test_shared_steps_that_are_not_an_array_of_tables_refuse_to_load(
    repo_clone, layout, tmp_path
):
    with pytest.raises(ConfigError) as caught:
        build(repo_clone, layout, tmp_path, hooks={"post_create": "uv sync"})

    assert any("[hooks] post_create must be an array of tables" in p for p in caught.value.problems)


def test_the_startup_budget_counts_inherited_steps_for_every_inheriting_repository(
    repo_clone, layout, tmp_path
):
    """FR-022. Counting the shared set once would under-report for exactly the repositories
    that have no section — the majority after milestone 005."""
    config = build(
        repo_clone,
        layout,
        tmp_path,
        daemon={"dispatching_max_age_seconds": 30},
        hooks={"default_timeout_seconds": 10, "post_create": [{"run": "slow", "timeout": 60}]},
        repos={"jantman/inherits": {}},
    )

    assert any("does not exceed the longest repository's preparation timeouts (60s)" in w
               for w in config.warnings)


def test_a_repositorys_own_steps_are_what_the_budget_counts_for_it(
    repo_clone, layout, tmp_path
):
    config = build(
        repo_clone,
        layout,
        tmp_path,
        daemon={"dispatching_max_age_seconds": 30},
        hooks={"default_timeout_seconds": 10, "post_create": [{"run": "slow", "timeout": 60}]},
        repos={
            # Every repository in this config overrides, so nothing inherits the 60s set.
            "demo": {"path": str(repo_clone), "post_create": [{"run": "fast", "timeout": 5}]},
            "jantman/own": {"post_create": [{"run": "fast", "timeout": 5}]},
        },
    )

    # 5s each, from their own steps — not the 60s neither of them inherits.
    assert not any("preparation timeouts" in w for w in config.warnings)
