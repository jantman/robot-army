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
Notifier             simulated   simulated   simulated    real
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
    Notifier,
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


#: What each boundary *not* really doing something means, said to the person reading the
#: screen rather than to the person who wrote the wiring. Keyed by the same names as
#: :data:`REAL_AT`, so :func:`consequences` can derive which of these hold at a given level
#: instead of four hand-written paragraphs drifting out of step with the table above them.
#:
#: Only boundaries that are simulated *somewhere* appear. A boundary real at every level —
#: the two readers — has no consequence to state at any level, and a phrase that can never
#: render is dead data. ``test_effects`` asserts the two sets agree, so a boundary that
#: gains a simulated level without gaining a phrase fails the suite rather than rendering a
#: banner that quietly omits it.
#:
#: Order is the order they are read out in, worst-understood first: what the system would
#: have *done* on this machine, then what it would have done to the world outside it.
SIMULATED_CONSEQUENCES: dict[str, str] = {
    "session_host": "no session is really launched",
    "version_control": "no branch, commit or worktree is really created",
    "hook_runner": "no hook really runs",
    "display": "no terminal window really opens",
    "issue_writer": (
        "no issue or comment is really written, and the issue numbers shown are invented"
    ),
    "card_writer": "no card really moves on the board",
    "notifier": "no notification is really sent",
}


def consequences(level: EffectLevel) -> list[str]:
    """What is *not* really happening at ``level``, in operator terms.

    Derived from :data:`REAL_AT` rather than written out per level, so it cannot drift from
    what the boundaries actually do. Empty at ``live`` by construction — which is why "no
    banner at ``live``" falls out of the derivation instead of out of a branch someone has
    to remember not to delete.
    """
    return [
        phrase
        for name, phrase in SIMULATED_CONSEQUENCES.items()
        if level not in REAL_AT[name]
    ]


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
    # A notification leaves the machine, so it is an outward-facing write and follows the
    # board writer's rule rather than the session host's: real only at ``live`` (FR-040).
    "notifier": frozenset({EffectLevel.LIVE}),
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
    #: The one boundary with a second name, and the one selection that is **not** a
    #: function of the effect level (069 FR-011).
    #:
    #: The level answers "what should a *new* session use". It cannot answer "what owns
    #: this row", because a row created as simulated stays simulated for the whole of its
    #: life no matter what the configuration later becomes — and the ordinary go-live step
    #: is exactly that change. Dispatch at ``local``, raise the level, restart, cancel: the
    #: row still says ``pid = 0``, and ``getpgid(0)`` answers about the *caller*, so the
    #: real host would have signalled the daemon's own process group.
    #:
    #: ``operations.cancel`` is the only reader, asserted by a test. If a second appears,
    #: that is the moment to ask whether the selection belongs back in the table.
    simulated_session_host: SessionHost
    display: Display
    notifier: Notifier

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
                # Named here too: a boundary that can be selected later but is absent from
                # the startup record is a gap in the reconstruction (Principle III). A
                # reader would see ``DtachHost`` wired and have no way to know a second
                # host was standing by.
                "simulated_session_host",
                "display",
                "notifier",
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
    from robot_army.boundaries.notifier import SimulatedNotifier, WebhookNotifier
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
    # Constructed once and reused, so that at a simulated level ``session_host`` and
    # ``simulated_session_host`` are the *same object*. Two instances would not be
    # equivalent: ``SimulatedSessionHost`` carries an ``_alive`` set, and they would
    # diverge the moment one of them spawned something — after which ``is_alive`` would
    # answer differently depending on which field the caller reached for.
    simulated_host = SimulatedSessionHost(audit)
    host: SessionHost = DtachHost(audit) if is_real("session_host", level) else simulated_host
    display: Display = (
        KittyDisplay(config, audit)
        if is_real("display", level)
        else SimulatedDisplay(audit)
    )
    notifier: Notifier = (
        WebhookNotifier(config, audit)
        if is_real("notifier", level)
        else SimulatedNotifier(audit)
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
        simulated_session_host=simulated_host,
        display=display,
        notifier=notifier,
    )
