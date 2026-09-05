"""The generated example: complete, loadable, inert, reproducible, and credential-free.

These are the tests that make the example a thing that cannot rot rather than a thing
somebody has to remember. The property each one pins is named in its docstring, because
"the example config test failed" is otherwise a message that says nothing about what broke.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from robot_army import config as config_module
from robot_army.config import _KNOWN_KEYS, _REPO_KEYS, ConfigError, load
from robot_army.exampleconfig import (
    EXAMPLE_REPO_SECTION,
    SECTIONS,
    ExampleConfigError,
    KeySpec,
    SectionSpec,
    render,
)


def sections_of(text: str) -> dict[str, list[str]]:
    """Split the rendered document into ``{section name: its lines}``.

    Per section rather than over the whole file, so a key that exists only in the wrong
    section is a failure rather than a pass — which is exactly the mistake a hand-edit
    makes.
    """
    header = re.compile(r"^#?\s*\[(?P<name>[^\]]+)\]\s*$")
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        match = header.match(line)
        if match:
            current = found.setdefault(match.group("name"), [])
            continue
        if current is not None:
            current.append(line)
    return found


def keys_in(lines: list[str]) -> set[str]:
    """Key names in one section's lines, counting commented-out keys as present.

    A commented key still documents the key, and for several of them — the credential
    twins, the filesystem-validated ones, the environment-derived one — commented is the
    only form that leaves the file loadable.
    """
    pattern = re.compile(r"^(?:#\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    return {m.group(1) for line in lines if (m := pattern.match(line))}


@pytest.fixture(scope="module")
def rendered() -> str:
    return render()


@pytest.fixture(scope="module")
def parsed(rendered: str) -> dict[str, list[str]]:
    return sections_of(rendered)


# -- completeness (FR-011, FR-024) -----------------------------------------------------


@pytest.mark.parametrize("section", sorted(_KNOWN_KEYS))
def test_every_loader_section_is_present(parsed, section):
    """Every section the loader accepts appears in the example."""
    assert section in parsed, (
        f"[{section}] is accepted by the loader but is missing from the example config"
    )


@pytest.mark.parametrize(
    ("section", "key"),
    sorted((s, k) for s, keys in _KNOWN_KEYS.items() for k in keys),
)
def test_every_loader_key_is_documented(parsed, section, key):
    """Every key the loader accepts appears in *its own* section, with a comment."""
    lines = parsed[section]
    assert key in keys_in(lines), (
        f"[{section}] {key} is accepted by the loader but is not in the example config. "
        "Add a KeySpec for it in exampleconfig.SECTIONS."
    )
    for line in lines:
        if re.match(rf"^(?:#\s*)?{re.escape(key)}\s*=", line):
            assert "#" in line.split("=", 1)[1], f"[{section}] {key} has no comment"
            break


@pytest.mark.parametrize("key", sorted(_REPO_KEYS))
def test_every_repo_key_is_documented(parsed, key):
    """The per-repository section documents every key ``[repos.*]`` accepts."""
    assert key in keys_in(parsed[EXAMPLE_REPO_SECTION]), (
        f"[repos.*] {key} is accepted by the loader but is not in the example config"
    )


def test_no_section_documents_a_key_the_loader_rejects(parsed):
    """The check runs the other way too: a removed key must not linger as documentation."""
    for section, lines in parsed.items():
        expected = _REPO_KEYS if section == EXAMPLE_REPO_SECTION else _KNOWN_KEYS[section]
        assert keys_in(lines) <= set(expected), (
            f"[{section}] documents {sorted(keys_in(lines) - set(expected))}, which the "
            "loader does not accept"
        )


# -- loadability (FR-013) ---------------------------------------------------------------


@pytest.fixture
def loadable(tmp_path, monkeypatch, rendered) -> Path:
    """The example on disk, on a machine arranged the way the example describes.

    Creating ``~/GIT`` is not an edit to the file: ``[paths] repo_root`` is validated for
    existence at load, so the file describes a machine and the test builds that machine.
    """
    home = tmp_path / "home"
    (home / "GIT").mkdir(parents=True)
    (home / "worktrees").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    path = tmp_path / "config.toml"
    path.write_text(rendered, encoding="utf-8")
    return path


def test_the_example_loads_unmodified(loadable):
    """It parses and validates as generated — no edits, no problems."""
    try:
        loaded = load(loadable)
    except ConfigError as exc:  # pragma: no cover - the message is the point when it fires
        pytest.fail(f"the generated example does not load:\n{exc}")
    assert loaded.path == loadable


def test_the_example_loads_without_warnings(loadable):
    """Not merely no errors. A warning in a file we generate is a defect in the generator."""
    assert load(loadable).warnings == ()


# -- inertness (FR-015) -----------------------------------------------------------------


def test_copying_the_example_configures_nothing_outward_facing(loadable):
    """Copied verbatim it polls no board, notifies nobody, and deletes nothing.

    ``trello`` and ``pushover`` being ``None`` is the strong form: there is no object to
    make a request with, rather than a flag some later call site has to keep checking.
    """
    loaded = load(loadable)
    assert loaded.trello is None
    assert loaded.pushover is None
    assert loaded.notifications.events == ()
    assert loaded.cleanup.on_issue_close is False
    assert loaded.repos == {}


def test_the_web_interface_stays_on_loopback(loadable):
    """The bind address is the access policy; an example that widened it would be a hole."""
    assert load(loadable).web.bind == "127.0.0.1"


# -- reproducibility (FR-016, FR-020) ---------------------------------------------------


def test_render_ignores_the_environment(monkeypatch, rendered):
    """Same bytes under a different runtime dir, home, and user.

    The hazard is real rather than theoretical: ``[terminal] socket_glob``'s default is
    computed from ``$XDG_RUNTIME_DIR``, so a generator that reads it would produce a file
    carrying this machine's UID — and the drift test would then fail for everyone else.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/9999")
    monkeypatch.setenv("XDG_STATE_HOME", "/somewhere/else")
    monkeypatch.setenv("HOME", "/home/somebody-else")
    assert render() == rendered


def test_render_is_stable_across_calls(rendered):
    """No clock, no counter, no set iteration."""
    assert render() == rendered


def test_render_carries_no_timestamp(rendered):
    """A "generated on <date>" banner would break the drift test on the second day."""
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", rendered)


# -- no credentials (FR-014) ------------------------------------------------------------


def test_no_line_looks_like_a_credential(rendered):
    """Checked with the loader's own detectors, not a second regex written for the test.

    If this ever failed, the loader would refuse the file the generator just produced —
    the two rules would be in direct contradiction.
    """
    for number, line in enumerate(rendered.splitlines(), start=1):
        for value in re.findall(r'"([^"]*)"', line):
            assert not config_module._looks_like_token(value), (
                f"line {number} contains a GitHub- or Trello-shaped credential: {line}"
            )
            assert not config_module._looks_like_pushover_credential(value), (
                f"line {number} contains a Pushover-shaped credential: {line}"
            )


def test_credential_keys_name_a_variable_or_a_file(parsed):
    """``*_env`` holds the *name* of a variable; ``*_file`` holds a path. Never a value."""
    for section in ("github", "trello"):
        for line in parsed[section]:
            if re.match(r"^(?:#\s*)?\w*token_env\s*=|^(?:#\s*)?\w*key_env\s*=", line):
                value = line.split("=", 1)[1].split("#")[0].strip().strip('"')
                assert re.fullmatch(r"[A-Z][A-Z0-9_]*", value), (
                    f"[{section}] an *_env key must name an environment variable, got {value!r}"
                )


# -- the failure path (data-model.md) ---------------------------------------------------


def test_an_undocumented_key_is_refused(monkeypatch):
    """Add a key to the loader, forget the example, and the generator says so by name.

    This is the whole design in one test: completeness is enforced when the thing runs, not
    only when the suite runs.
    """
    monkeypatch.setitem(_KNOWN_KEYS, "web", _KNOWN_KEYS["web"] | {"tls_cert"})
    with pytest.raises(ExampleConfigError, match=r"\[web\] tls_cert"):
        render()


def test_a_key_the_loader_dropped_is_refused(monkeypatch):
    """And the other direction: documentation for a key that no longer exists."""
    monkeypatch.setitem(_KNOWN_KEYS, "web", _KNOWN_KEYS["web"] - {"port"})
    with pytest.raises(ExampleConfigError, match=r"\[web\] port .*does not"):
        render()


def test_an_undocumented_section_is_refused(monkeypatch):
    """A whole new section is caught the same way a single key is."""
    monkeypatch.setitem(_KNOWN_KEYS, "telemetry", {"enabled"})
    with pytest.raises(ExampleConfigError, match=r"\[telemetry\].*no SectionSpec"):
        render()


def test_a_commented_key_must_say_why():
    """A commented-out line with no reason is a line the reader cannot act on."""
    with pytest.raises(ExampleConfigError, match="must say why"):
        KeySpec("port", "8420", "the port", active=False)


def test_an_active_key_may_not_carry_a_reason():
    """The pairing is checked in both directions, so the field cannot become decorative."""
    with pytest.raises(ExampleConfigError, match="commented-out keys only"):
        KeySpec("port", "8420", "the port", why_commented="because")


def test_a_dead_section_may_not_hold_a_live_key():
    """A live key under a commented header is silently read into the section above it."""
    with pytest.raises(ExampleConfigError, match="not"):
        SectionSpec(
            name="trello",
            blurb=("x",),
            keys=(KeySpec("board_id", '"b"', "the board"),),
            active=False,
        )


def test_every_key_carries_a_non_empty_comment():
    """FR-012, checked on the specs rather than on the rendered text."""
    for section in SECTIONS:
        for key in section.keys:
            assert key.comment.strip(), f"[{section.name}] {key.name} has no comment"


def test_the_inert_sections_stay_inert():
    """Guards the intent, not just today's spelling.

    ``[trello]`` and ``[pushover]`` are commented out because their *absence* is what makes
    an unconfigured install make no outbound request. A future edit that switches either on
    to "make the example more complete" would be a real behaviour change for anyone who
    copies the file, so it fails here rather than in someone's Trello account.
    """
    inert = {s.name: s for s in SECTIONS if s.name in ("trello", "pushover")}
    assert set(inert) == {"trello", "pushover"}
    for name, section in inert.items():
        assert not section.active, f"[{name}] must stay commented out; see research R3"


def test_specs_are_immutable():
    """Frozen dataclasses, so nothing can mutate the tables at import time."""
    assert dataclasses.fields(KeySpec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        KeySpec("port", "8420", "the port").name = "other"  # type: ignore[misc]


def test_the_socket_dir_example_matches_where_sockets_actually_go(parsed):
    """Regression for a review finding on #137.

    ``socket_dir``'s illustrative value was copy-pasted from ``state_dir`` above it, so the
    example showed a state path for a key whose default comes from ``runtime_dir()`` — and
    contradicted the note on its own line. An illustrative value that illustrates the wrong
    thing is worse than none, because it is the line someone uncomments.
    """
    line = next(ln for ln in parsed["paths"] if ln.lstrip("# ").startswith("socket_dir"))
    value = line.split("=", 1)[1].split("#")[0].strip().strip('"')
    assert "/run/user/" in value, (
        f"socket_dir illustrates {value!r}; it defaults under $XDG_RUNTIME_DIR, "
        "not the state directory"
    )
    assert "state" not in value


def test_every_commented_key_illustrates_what_its_reason_describes(parsed):
    """The general form of the bug above: a value and its note must not contradict.

    Only the two path keys are checked mechanically — they are the ones whose reason names a
    specific directory, so the claim is checkable. Elsewhere the note is prose.
    """
    for key, expected in (("state_dir", "state"), ("socket_dir", "run/user")):
        line = next(ln for ln in parsed["paths"] if ln.lstrip("# ").startswith(key))
        assert expected in line, f"[paths] {key}'s example value contradicts its own comment"
