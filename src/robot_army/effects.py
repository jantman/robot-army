"""Effect levels, and the one table that selects real or simulated per boundary.

This module is the *only* place in ``src/robot_army/`` that knows an effect level exists.
Wiring happens once at startup and the resulting :class:`Boundaries` is passed down;
code downstream of that wiring has no access to the level and therefore cannot
accidentally branch on it. That is what makes FR-053's guarantee structural rather than
a rule someone has to remember.

A test (T147) greps the rest of the package for ``if dry_run:`` — finding one outside
this module would mean the requirement was not really implemented.

The table, from contracts/boundaries.md:

===================  ==========  ==========  ===========  ========
Boundary             plan        local       no-remote    live
===================  ==========  ==========  ===========  ========
IssueSource reads    real        real        real         real
IssueSource writes   simulated   simulated   simulated    real
CardSource reads     real        real        real         real
CardSource writes    simulated   simulated   simulated    real
VersionControl       simulated   real        real         real
HookRunner           simulated   real        real         real
SessionHost          simulated   simulated   real         real
Display              simulated   simulated   real         real
===================  ==========  ==========  ===========  ========

Reads are always real (FR-052) — a dry run that fakes its reads tells you nothing about
eligibility, which is the main thing you want to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from robot_army.boundaries import (
    CardSourceReader,
    CardSourceWriter,
    Display,
    HookRunner,
    IssueSourceReader,
    IssueSourceWriter,
    SessionHost,
    VersionControl,
)

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config


class EffectLevel(StrEnum):
    """Four graduated levels (FR-051). ``plan`` intends only; ``live`` does everything."""

    PLAN = "plan"
    LOCAL = "local"
    NO_REMOTE = "no-remote"
    LIVE = "live"

    @property
    def is_simulated(self) -> bool:
        """True for any level below ``live`` — i.e. rows created at it are ``dry_run``."""
        return self is not EffectLevel.LIVE


#: Boundary name → the set of levels at which its **real** implementation is selected.
#: Written as data rather than as branches so the test can assert the whole table.
REAL_AT: dict[str, frozenset[EffectLevel]] = {
    "issue_reader": frozenset(EffectLevel),  # FR-052: always real, at every level
    "issue_writer": frozenset({EffectLevel.LIVE}),
    "card_reader": frozenset(EffectLevel),  # FR-038: always real, at every level
    "card_writer": frozenset({EffectLevel.LIVE}),  # FR-039: no board write below live
    "version_control": frozenset({EffectLevel.LOCAL, EffectLevel.NO_REMOTE, EffectLevel.LIVE}),
    "hook_runner": frozenset({EffectLevel.LOCAL, EffectLevel.NO_REMOTE, EffectLevel.LIVE}),
    "session_host": frozenset({EffectLevel.NO_REMOTE, EffectLevel.LIVE}),
    "display": frozenset({EffectLevel.NO_REMOTE, EffectLevel.LIVE}),
}


def is_real(boundary: str, level: EffectLevel) -> bool:
    try:
        return level in REAL_AT[boundary]
    except KeyError:
        raise KeyError(f"unknown boundary {boundary!r}") from None


@dataclass(frozen=True, slots=True)
class Boundaries:
    """The wired set. Everything downstream takes this and never asks about the level."""

    level: EffectLevel
    issue_reader: IssueSourceReader
    issue_writer: IssueSourceWriter
    card_reader: CardSourceReader | None
    card_writer: CardSourceWriter | None
    version_control: VersionControl
    hook_runner: HookRunner
    session_host: SessionHost
    display: Display

    def describe(self) -> dict[str, str]:
        """Which implementation each boundary got, for the startup log (FR-057)."""
        return {
            # ``NoneType`` for the board pair on an installation with no ``[trello]``
            # section, which is exactly what FR-001 means by inert and is worth seeing in
            # the startup record rather than omitting.
            name: type(getattr(self, name)).__name__
            for name in (
                "issue_reader",
                "issue_writer",
                "card_reader",
                "card_writer",
                "version_control",
                "hook_runner",
                "session_host",
                "display",
            )
        }


def wire(level: EffectLevel, config: Config, audit: AuditLog) -> Boundaries:
    """Select one implementation per boundary. Called exactly once, at startup.

    Imports are local so that ``effects`` stays importable by ``config`` without a cycle,
    and so a test can import the table without dragging in httpx.
    """
    from robot_army.boundaries.dtach import DtachHost, SimulatedSessionHost
    from robot_army.boundaries.git import GitVersionControl, SimulatedVersionControl
    from robot_army.boundaries.github import (
        GitHubReader,
        GitHubWriter,
        SimulatedIssueWriter,
    )
    from robot_army.boundaries.hooks import SimulatedHookRunner, SubprocessHookRunner
    from robot_army.boundaries.kitty import KittyDisplay, SimulatedDisplay
    from robot_army.boundaries.trello import (
        SimulatedCardWriter,
        TrelloCardReader,
        TrelloCardWriter,
    )

    # There is no SimulatedIssueReader, deliberately: no level ever selects one, and its
    # absence means a bug that tries to fake reads fails to import.
    reader = GitHubReader(config, audit)

    # The board pair is ``None`` when no board is configured. This is the one boundary that
    # can be absent, and the absence is the requirement (FR-001): there is nothing to wire,
    # so nothing downstream can construct a request by accident.
    #
    # There is no SimulatedCardReader, for the same reason there is no SimulatedIssueReader:
    # no level selects one, so a bug that tries to fake board reads fails to import.
    card_reader: CardSourceReader | None = None
    card_writer: CardSourceWriter | None = None
    if config.trello is not None:
        card_reader = TrelloCardReader(config, audit)
        card_writer = (
            TrelloCardWriter(config, audit)
            if is_real("card_writer", level)
            else SimulatedCardWriter(audit)
        )

    writer: IssueSourceWriter = (
        GitHubWriter(config, audit)
        if is_real("issue_writer", level)
        else SimulatedIssueWriter(audit)
    )
    vcs: VersionControl = (
        GitVersionControl(audit)
        if is_real("version_control", level)
        else SimulatedVersionControl(audit)
    )
    hooks: HookRunner = (
        SubprocessHookRunner(audit)
        if is_real("hook_runner", level)
        else SimulatedHookRunner(audit)
    )
    host: SessionHost = (
        DtachHost(audit)
        if is_real("session_host", level)
        else SimulatedSessionHost(audit)
    )
    display: Display = (
        KittyDisplay(config, audit)
        if is_real("display", level)
        else SimulatedDisplay(audit)
    )

    return Boundaries(
        level=level,
        issue_reader=reader,
        issue_writer=writer,
        card_reader=card_reader,
        card_writer=card_writer,
        version_control=vcs,
        hook_runner=hooks,
        session_host=host,
        display=display,
    )
