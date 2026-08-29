"""Turning a stored instant into something a person can read.

Every timestamp this system records is UTC, in one format, and that is correct: a log
whose timestamps depend on where the reader stood cannot meet Principle III's
reconstruction standard. But until milestone 010 every timestamp this system *displayed*
was that same UTC string handed to a human unchanged, which asked the one person the tool
exists to serve to do arithmetic — on every stamp, on every screen, with an answer that
changes twice a year.

This module is the whole of the fix, and the boundary it sits on is the point:

* **Above it** — the database, the audit files, the heartbeat, ``Result.data``,
  ``View.data``, the chrome dict, every ``--json`` payload — everything stays UTC. Nothing
  here is called from any of them.
* **Below it** — ``Result.lines`` and the markup built by :mod:`robot_army.web.pages` and
  :mod:`robot_army.web.html` — everything reads local.

:func:`local` is display-only and nothing may compare, sort, store, or parse back what it
returns. Every comparison, ordering, age, staleness threshold, backoff window and capacity
decision in this package reads the stored UTC value instead.

That was audited when this module was written, and the result is worth writing down because
the check is cheap to repeat: ``health``, ``poll``, ``ordering``, ``capacity``, ``reconcile``
and ``dispatch`` contain **no call to this module at all** — the decision paths do not import
the display function, which is a stronger guarantee than any convention. ``pages`` calls it
twice and ``html`` twice, all four in markup; ``operations`` calls it eleven times, all of
them inside an f-string bound for ``Result.lines``. Ages are measured by ``pages.age_seconds``
and ``operations._age_seconds``, both of which parse the stored value, and every ``ORDER BY``
sorts a stored column. A rendered value also cannot be smuggled into a comparison by accident:
:func:`parse_stamp` refuses to read it back, so anything that tried would get ``None``.

**One thing is deliberately not converted**: the ``detail`` payload of an audit record,
which both front ends render as a quotation of what was written. It is free-form JSON, so
rewriting anything inside it that looked like a timestamp would need a heuristic, would
corrupt a field that merely resembled one, and would make the displayed record disagree with
the file it is quoting — which is the opposite of what the log is read for. Both interfaces
quote it verbatim, so they still agree with each other.

It lives at the top level of the package rather than inside ``web/`` because both front
ends need it and ``operations.py`` must not import from ``web/`` — ``web/`` is a front end
onto ``operations.py``, and this package's dependency arrow runs one way.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: The one timestamp format in the database, the audit log and the heartbeat.
STORED = "%Y-%m-%dT%H:%M:%SZ"

#: What a person reads. A space in place of ``STORED``'s ``T`` so that a rendered value can
#: never be mistaken for a record value, and a numeric offset on **every** stamp rather
#: than a zone stated once per surface. The offset is not decoration: at the autumn
#: daylight-saving fold two instants an hour apart render to the same wall clock, and it is
#: the only thing that tells them apart (research R8). A zone abbreviation would be shorter
#: and is not unique — ``CST`` and ``IST`` each name several zones — so it cannot do that job.
DISPLAYED = "%Y-%m-%d %H:%M:%S %:z"


def parse_stamp(stamp: str | None) -> datetime | None:
    """Read a stored instant. ``None`` for anything that is not one.

    Returns ``None`` rather than raising because every caller is either rendering or
    measuring an age, and neither has a useful response to an exception. A corrupt row must
    be visible to the reader, not fatal to the command that found it.
    """
    if not stamp:
        return None
    try:
        return datetime.strptime(str(stamp), STORED).replace(tzinfo=UTC)
    except ValueError:
        return None


def local(stamp: str | None) -> str | None:
    """The same instant in the host's timezone, labelled with its offset.

    ``2026-08-30T01:31:07Z`` becomes ``2026-08-29 21:31:07 -04:00`` in New York — note the
    *different calendar day*, which is the whole reason this exists.

    The host's zone is whatever the operating system reports: ``TZ`` if set, otherwise
    ``/etc/localtime``. ``astimezone()`` with no argument asks for exactly that, and asks
    per instant rather than per process, so a January stamp displayed in August carries
    January's offset. There is deliberately no configuration key — on a single-user machine
    a display-timezone setting has one correct value, which is the one the operating system
    already holds.

    **A zone that cannot be resolved needs no handling here.** The C library treats an
    unknown ``TZ`` as UTC, so an unresolvable zone renders at ``+00:00`` — which is the
    honest statement that the zone is unknown rather than a silent pretence, and is the
    fallback the specification asks for, obtained by writing nothing.

    Passes anything it cannot parse straight through. A stamp the database should not have
    contained must reach the screen as the value it is: hiding it behind a dash would be
    this module quietly covering for a corrupt row.
    """
    parsed = parse_stamp(stamp)
    if parsed is None:
        return stamp
    return parsed.astimezone().strftime(DISPLAYED)
