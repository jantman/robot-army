"""The units this repository ships are installable, and the start limit is reachable.

Nothing here executes systemd. What it guards is the failure issue #53 is about: a claim
about the deployment that outlived the deployment, with nothing to notice.

Two claims are worth holding. A unit file nobody is told to copy is a unit file that does
not run, so every path under ``systemd/`` has to appear in the guide's install block. And
the start-limit drop-in only earns its place if its window is wide enough for the restarts
that have to fit inside it — widen ``RestartSec`` or narrow the interval and systemd goes
back to retrying a daemon that can never start, forever, which is exactly the state the
dead-man's switch is supposed to report.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / "systemd"
INSTALL_PAGE = REPO / "docs" / "guide" / "operating.md"

#: ``RestartSec`` in the daemon's own unit, which is not in this repository — it is
#: hand-written, because what it carries is this machine's business (R15). The number is
#: therefore stated rather than read, in the one place that depends on it: the drop-in's
#: window has to hold ``StartLimitBurst`` starts spaced this far apart, or systemd never
#: reaches the limit and never gives up.
DEPLOYED_RESTART_SEC = 10

#: ``5min``, ``300s``, ``5m``: the spellings systemd accepts that this project might use.
TIMESPAN = re.compile(r"^(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hours?)?$")
_SECONDS = {None: 1, "s": 1, "sec": 1, "second": 1, "seconds": 1, "m": 60, "min": 60,
            "minute": 60, "minutes": 60, "h": 3600, "hour": 3600, "hours": 3600}


def directives(unit: Path) -> dict[str, str]:
    """The unit's ``Key=value`` lines, flattened — comments and sections dropped.

    Flattened rather than parsed per-section because every key asked about below is unique
    across the file, and a section-aware reader would be more machinery than one dictionary
    lookup justifies.
    """
    found = {}
    for line in unit.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


def seconds(value: str) -> int:
    """A systemd time span in seconds, refusing anything this reader cannot be sure of."""
    match = TIMESPAN.match(value)
    assert match, f"cannot read {value!r} as a systemd time span"
    return int(match.group(1)) * _SECONDS[match.group(2)]


def test_every_shipped_unit_is_named_in_the_install_block():
    """A unit the guide never mentions is one that sits in the repository and never runs."""
    page = INSTALL_PAGE.read_text(encoding="utf-8")
    copied = re.findall(r"cp (?:-r )?systemd/(\S+)", page)
    assert copied, f"{INSTALL_PAGE.name} has no `cp systemd/...` line to check against"
    for path in sorted(UNITS.iterdir()):
        assert any(fnmatch(path.name, pattern) for pattern in copied), (
            f"systemd/{path.name} is shipped but {INSTALL_PAGE.name} never says to copy it"
        )


def test_the_start_limit_window_holds_the_restarts_it_has_to():
    """The whole point of the drop-in: with the defaults, systemd never gives up.

    ``StartLimitBurst`` starts arrive ``RestartSec`` apart, so they only fall inside one
    window if the window is longer than the gaps between them. systemd's default 10s window
    against a 10s ``RestartSec`` never fits two, which is why a daemon that cannot start was
    retried forever and never reported dead (issue #53).
    """
    dropin = UNITS / "robot-army.service.d" / "start-limit.conf"
    values = directives(dropin)
    burst = int(values["StartLimitBurst"])
    window = seconds(values["StartLimitIntervalSec"])
    needed = burst * DEPLOYED_RESTART_SEC
    assert window > needed, (
        f"StartLimitIntervalSec={values['StartLimitIntervalSec']} cannot hold {burst} starts "
        f"{DEPLOYED_RESTART_SEC}s apart ({needed}s); systemd would never stop retrying"
    )


def test_a_failing_health_check_does_not_stop_the_timer():
    """Exit 4 is the verdict the timer exists to produce; it must not fail the unit."""
    values = directives(UNITS / "robot-army-health.service")
    assert "4" in values["SuccessExitStatus"].split(), (
        "robot-army health exits 4 on every non-ok verdict; without it here the unit goes "
        "to failed and the timer stops running"
    )
