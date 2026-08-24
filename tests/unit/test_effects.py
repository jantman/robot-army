"""The effect-level wiring table (T110, T111).

The table in contracts/boundaries.md is asserted here **whole**, cell by cell, rather than
by spot-checking a few levels. It is the mechanism FR-053 rests on: if one cell is wrong,
a dry run performs a real effect, and nothing else in the system would notice.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from robot_army.boundaries import (
    Display,
    HookRunner,
    IssueSourceReader,
    IssueSourceWriter,
    SessionHost,
    VersionControl,
)
from robot_army.effects import REAL_AT, Boundaries, EffectLevel, is_real, wire

SRC = Path(__file__).resolve().parents[2] / "src" / "robot_army"

#: contracts/boundaries.md, transcribed. "real" or "simulated" per boundary per level.
EXPECTED = {
    EffectLevel.PLAN: {
        "issue_reader": "real",
        "issue_writer": "simulated",
        "version_control": "simulated",
        "hook_runner": "simulated",
        "session_host": "simulated",
        "display": "simulated",
    },
    EffectLevel.LOCAL: {
        "issue_reader": "real",
        "issue_writer": "simulated",
        "version_control": "real",
        "hook_runner": "real",
        "session_host": "simulated",
        "display": "simulated",
    },
    EffectLevel.NO_REMOTE: {
        "issue_reader": "real",
        "issue_writer": "simulated",
        "version_control": "real",
        "hook_runner": "real",
        "session_host": "real",
        "display": "real",
    },
    EffectLevel.LIVE: {
        "issue_reader": "real",
        "issue_writer": "real",
        "version_control": "real",
        "hook_runner": "real",
        "session_host": "real",
        "display": "real",
    },
}

REAL_CLASSES = {
    "issue_reader": "GitHubReader",
    "issue_writer": "GitHubWriter",
    "version_control": "GitVersionControl",
    "hook_runner": "SubprocessHookRunner",
    "session_host": "DtachHost",
    "display": "KittyDisplay",
}
SIMULATED_CLASSES = {
    "issue_writer": "SimulatedIssueWriter",
    "version_control": "SimulatedVersionControl",
    "hook_runner": "SimulatedHookRunner",
    "session_host": "SimulatedSessionHost",
    "display": "SimulatedDisplay",
}


@pytest.mark.parametrize("level", list(EffectLevel), ids=lambda level: level.value)
def test_every_cell_of_the_table(level, config, audit):
    wired = wire(level, config, audit)
    for boundary, expected in EXPECTED[level].items():
        actual = type(getattr(wired, boundary)).__name__
        want = REAL_CLASSES[boundary] if expected == "real" else SIMULATED_CLASSES[boundary]
        assert actual == want, f"{level}/{boundary}: expected {want}, wired {actual}"


@pytest.mark.parametrize("level", list(EffectLevel), ids=lambda level: level.value)
def test_reads_are_real_at_every_level(level, config, audit):
    """FR-052. A dry run that fakes its reads tells you nothing about eligibility, which
    is the main thing you want to check."""
    assert is_real("issue_reader", level)
    assert type(wire(level, config, audit).issue_reader).__name__ == "GitHubReader"


def test_there_is_no_simulated_issue_reader_anywhere():
    """Deliberate: its absence means a bug that tries to fake reads fails to *import*
    rather than quietly returning fixtures."""
    from robot_army.boundaries import github

    names = [name for name in dir(github) if "Simulated" in name]
    assert names == ["SimulatedIssueWriter"], names
    assert not hasattr(github, "SimulatedIssueReader")


def test_the_table_covers_exactly_the_six_boundaries():
    assert set(REAL_AT) == set(REAL_CLASSES)
    assert set(REAL_AT) == set(EXPECTED[EffectLevel.LIVE])


def test_an_unknown_boundary_name_raises_rather_than_defaulting():
    """Defaulting to "simulated" would silently disable an effect; defaulting to "real"
    would silently perform one. Neither is acceptable, so it raises."""
    with pytest.raises(KeyError):
        is_real("not_a_boundary", EffectLevel.PLAN)


def test_live_is_the_only_level_that_is_not_simulated():
    assert EffectLevel.LIVE.is_simulated is False
    for level in (EffectLevel.PLAN, EffectLevel.LOCAL, EffectLevel.NO_REMOTE):
        assert level.is_simulated is True


def test_plan_performs_no_writes_of_any_kind(config, audit):
    wired = wire(EffectLevel.PLAN, config, audit)
    for boundary in ("issue_writer", "version_control", "hook_runner", "session_host", "display"):
        assert type(getattr(wired, boundary)).__name__.startswith("Simulated")


def test_the_wired_set_describes_itself_for_the_startup_log(config, audit):
    """FR-057: the effect level must be stated loudly at startup, and knowing *which
    implementations* it selected is what makes that statement checkable."""
    described = wire(EffectLevel.LOCAL, config, audit).describe()
    assert described["version_control"] == "GitVersionControl"
    assert described["session_host"] == "SimulatedSessionHost"


# -- structural conformance --------------------------------------------------


@pytest.mark.parametrize("level", list(EffectLevel), ids=lambda level: level.value)
def test_every_wired_implementation_satisfies_its_protocol(level, config, audit):
    wired = wire(level, config, audit)
    assert isinstance(wired.issue_reader, IssueSourceReader)
    assert isinstance(wired.issue_writer, IssueSourceWriter)
    assert isinstance(wired.version_control, VersionControl)
    assert isinstance(wired.hook_runner, HookRunner)
    assert isinstance(wired.session_host, SessionHost)
    assert isinstance(wired.display, Display)


def test_real_and_simulated_pairs_have_the_same_method_surface(config, audit):
    """The simulated path must not be able to diverge from the real one by *omission* —
    a missing method would surface as an AttributeError only on the code path that
    happens to call it."""
    live = wire(EffectLevel.LIVE, config, audit)
    plan = wire(EffectLevel.PLAN, config, audit)

    def surface(obj: object) -> set[str]:
        return {
            name
            for name in dir(obj)
            if not name.startswith("_") and callable(getattr(obj, name, None))
        }

    for boundary in ("issue_writer", "version_control", "hook_runner", "session_host", "display"):
        real = surface(getattr(live, boundary))
        simulated = surface(getattr(plan, boundary))
        missing = real - simulated
        assert not missing, f"{boundary}: simulated implementation is missing {missing}"


@pytest.mark.parametrize(
    "boundary", ["issue_writer", "version_control", "hook_runner", "session_host", "display"]
)
def test_matching_methods_accept_the_same_arguments(boundary, config, audit):
    live = getattr(wire(EffectLevel.LIVE, config, audit), boundary)
    plan = getattr(wire(EffectLevel.PLAN, config, audit), boundary)
    for name in dir(live):
        if name.startswith("_"):
            continue
        real_attr = getattr(live, name, None)
        sim_attr = getattr(plan, name, None)
        if not callable(real_attr) or not callable(sim_attr):
            continue
        real_params = list(inspect.signature(real_attr).parameters)
        sim_params = list(inspect.signature(sim_attr).parameters)
        # The simulated side may absorb extras with **kwargs; it may not require more.
        required = [
            p
            for p in inspect.signature(sim_attr).parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        assert len(required) <= len(real_params), (
            f"{boundary}.{name}: simulated requires {sim_params}, real takes {real_params}"
        )


# -- returning structurally valid handles (T111) -----------------------------


def test_simulated_implementations_return_handles_not_none(config, audit, tmp_path):
    """Returning ``None`` or raising would let the simulated path diverge from the real
    one at exactly the point the requirement exists to prevent."""
    wired = wire(EffectLevel.PLAN, config, audit)

    url = wired.issue_writer.comment("owner/repo", 1, "body")
    assert isinstance(url, str) and url

    handle = wired.version_control.add_worktree("/clone", "/wt", "branch", "main")
    assert handle is not None
    assert handle.path == "/wt" and handle.branch == "branch"
    assert handle.simulated is True

    removal = wired.version_control.remove_worktree("/wt")
    assert removal is not None and removal.worktree_removed is True

    hook_result = wired.hook_runner.run([], "/wt", "/clone", {})
    assert hook_result is not None and hook_result.ok is True

    host_handle = wired.session_host.spawn("/wt", ["claude"], str(tmp_path / "s.sock"))
    assert host_handle is not None
    assert host_handle.socket_path == str(tmp_path / "s.sock")
    assert wired.session_host.is_alive(host_handle) is True

    entry = wired.session_host.confirm_session("abc", 1.0)
    assert entry is not None and entry.session_id == "abc"

    display_handle = wired.display.open("/wt", ["claude"], "title", {"ra_item": "1"}, {})
    assert display_handle is not None
    assert isinstance(display_handle.window_id, int)
    assert wired.display.is_open(display_handle) is True
    assert wired.display.find_by_var("ra_item", "1") == display_handle
    assert wired.display.probe() is not None


def test_simulated_lists_are_empty_lists_not_none(config, audit):
    wired = wire(EffectLevel.PLAN, config, audit)
    assert wired.version_control.list_worktrees("/clone") == []
    assert wired.version_control.commits_ahead("/clone", "main", "b") == 0
    assert isinstance(wired.version_control.prune_worktrees("/clone"), str)
    assert isinstance(wired.version_control.status_porcelain("/wt"), str)


def test_the_simulated_host_reports_the_same_capabilities_as_the_real_one():
    """Code that branches on ``survives_display_death`` must take the same branch under
    simulation, or the simulated path diverges at exactly the wrong place."""
    from robot_army.boundaries.dtach import DtachHost, SimulatedSessionHost

    assert SimulatedSessionHost.capabilities == DtachHost.capabilities


# -- the FR-053 structural guarantee ----------------------------------------


def test_only_effects_py_knows_the_effect_level_exists(config, audit):
    """T147, asserted mechanically. If ``EffectLevel`` were consulted downstream, a new
    code path could forget the check — which is the drift FR-053 exists to prevent."""
    allowed = {"effects.py", "config.py", "cli.py", "daemon.py", "operations.py"}
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bEffectLevel\b", text):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, (
        "these modules consult the effect level directly instead of receiving a wired "
        f"boundary set: {offenders}"
    )


def test_only_effects_py_selects_a_simulated_implementation():
    """The narrower half of the same guarantee, and the one that bites.

    A ``dry_run`` flag that *marks a record* or *skips a remote check* is FR-055 and is
    correct — ``audit.py`` and ``reconcile.py`` both do it deliberately. What must never
    appear outside the wiring is code choosing *which implementation to call*, so this
    asserts that no module except ``effects.py`` even names one.
    """
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "effects.py":
            continue
        for piece in _identifiers(path):
            if piece.startswith("Simulated"):
                # boundaries/*.py legitimately *defines* its own simulated class.
                if path.parent.name == "boundaries" and piece in _defined_names(path):
                    continue
                offenders.append(f"{path.relative_to(SRC)}: {piece}")
    assert not offenders, (
        "implementation selection leaked outside effects.py: " + str(offenders)
    )


def _identifiers(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.alias):
            names.append(node.name.split(".")[-1])
    return names


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def test_the_scanner_would_catch_a_leak(tmp_path):
    """Guards the guard."""
    leaky = tmp_path / "leaky.py"
    leaky.write_text("from x import SimulatedDisplay\nd = SimulatedDisplay()\n", encoding="utf-8")
    assert any(piece.startswith("Simulated") for piece in _identifiers(leaky))


def test_the_boundaries_dataclass_is_frozen():
    """Wiring happens once at startup; nothing may swap an implementation afterwards."""
    import dataclasses

    assert dataclasses.fields(Boundaries)
    params = Boundaries.__dataclass_params__
    assert params.frozen is True
