"""Saying out loud that something happened, at most a handful of times per tick.

Composes :class:`robot_army.boundaries.NotificationEvent` values and hands them to the
``Notifier`` boundary, which :mod:`robot_army.effects` wires real only at ``live``. Off by
default: ``[notifications] events`` is empty, so an unconfigured installation makes no
outbound request at all (FR-033).

Two decisions are worth keeping in view:

* **The bound is per cycle, not per event** (R15). Per-``(kind, item)`` de-duplication does
  not bound a backlog, because a backlog produces *different* items — the very case that
  would flood. At most ``max_per_cycle`` sends per daemon tick, then one summary naming how
  many were suppressed and of which kinds, so the bound is visible rather than silent. The
  counter lives in process memory: it exists to bound one burst, and a restart mid-burst
  re-permitting a handful of messages is not worth a table.
* **Every send happens outside the transaction that caused it** (R14). Hooking
  ``states.transition()`` would be structurally complete and impossible to forget, and was
  rejected because it runs inside ``BEGIN IMMEDIATE`` — an HTTP POST there holds a write
  transaction open for as long as a slow webhook takes to answer.

An event carries identifiers and state names only. There is no field a secret could reach
(FR-037).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from robot_army.boundaries import NotificationEvent

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config
    from robot_army.effects import Boundaries

#: Sends made in the current daemon tick, and which kinds were suppressed once the bound
#: was reached.
#:
#: Process memory on purpose (R15). It exists to bound one burst; a restart mid-burst
#: re-permitting a handful of messages is not a failure worth a table, and a durable counter
#: would be state to keep correct in exchange for a cosmetic guarantee.
_CYCLE: dict[str, object] = {"sent": 0, "suppressed": []}


def begin_cycle() -> None:
    """Reset the per-tick counter. Called once per daemon tick, before any work."""
    _CYCLE["sent"] = 0
    _CYCLE["suppressed"] = []


def end_cycle(*, boundaries: Boundaries, audit: AuditLog, config: Config) -> None:
    """Send the one summary a suppressed burst earns, if there was one.

    The difference between a bound and silent loss. Principle III forbids discarding
    records silently, and a channel that simply stops at five would leave the author with a
    quieter lie than no channel at all.
    """
    suppressed = list(_CYCLE.get("suppressed") or [])
    if not suppressed:
        return
    kinds: dict[str, int] = {}
    for kind in suppressed:
        kinds[str(kind)] = kinds.get(str(kind), 0) + 1
    breakdown = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    limit = config.notifications.max_per_cycle
    audit.record(
        "notify.suppressed",
        outcome="ok",
        detail={"count": len(suppressed), "kinds": kinds, "max_per_cycle": limit},
    )
    _CYCLE["suppressed"] = []
    _deliver(
        boundaries,
        audit,
        NotificationEvent(
            kind="summary",
            item_id=None,
            repo_key=None,
            title=f"robot-army: {len(suppressed)} further notification(s) suppressed",
            detail=f"this cycle reached its limit of {limit}: {breakdown}",
            url=None,
        ),
        suppressed=False,
    )


def emit(
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    kind: str,
    title: str,
    detail: str,
    item_id: int | None = None,
    repo_key: str | None = None,
    url: str | None = None,
) -> bool:
    """Say one thing, if the author asked to be told this kind of thing.

    Returns whether a send was attempted — never whether it succeeded, because the caller
    must not care. A channel failure is recorded and never fails, delays, or retries the
    operation that triggered it (FR-035): the state change already happened and is already
    in the log, and making a webhook's availability a precondition for finishing a
    reconciliation pass would be the tail wagging the dog.

    **Call this outside the transaction that caused it** (R14). An HTTP POST inside
    ``BEGIN IMMEDIATE`` holds a write transaction open for as long as a slow webhook takes
    to answer, and the four call sites are placed accordingly.
    """
    if not config.notifications.wants(kind):
        # The unconfigured installation, which is the default one: no outbound request is
        # constructed at all, not merely skipped at the last moment.
        return False

    event = NotificationEvent(
        kind=kind,
        item_id=item_id,
        repo_key=repo_key,
        title=title,
        detail=detail,
        url=url,
    )
    # Counted once per **event**, not once per delivery. Since issue #106 a message may go
    # to two channels, and counting packets would silently halve how many things the author
    # is told about — the cap bounds a burst of news, which is exactly how R15 argued for
    # it (FR-013).
    sent = int(_CYCLE.get("sent") or 0)
    if sent >= config.notifications.max_per_cycle:
        suppressed = list(_CYCLE.get("suppressed") or [])
        suppressed.append(kind)
        _CYCLE["suppressed"] = suppressed
        audit.record(
            "notify.send",
            outcome="ok",
            entity_type="work_item",
            entity_id=item_id,
            detail={"kind": kind, "suppressed": True, "title": title},
        )
        return False

    _CYCLE["sent"] = sent + 1
    _deliver(boundaries, audit, event, suppressed=False)
    return True


def _deliver(
    boundaries: Boundaries, audit: AuditLog, event: NotificationEvent, *, suppressed: bool
) -> None:
    """Hand the event to the boundary, recording the attempt either way.

    Every send is recorded whether or not the channel accepted it, so Principle III's
    reconstruction standard is met by the log rather than by the channel — which is the only
    place it *can* be met, since a webhook has no memory we can read.
    """
    ok = False
    try:
        ok = bool(boundaries.notifier.send(event))
    except Exception as exc:  # noqa: BLE001 - a channel failure is never the operation's
        audit.record(
            "notify.send",
            outcome="error",
            entity_type="work_item",
            entity_id=event.item_id,
            detail={"kind": event.kind, "suppressed": suppressed, "error": str(exc)},
        )
        return
    audit.record(
        "notify.send",
        outcome="ok" if ok else "error",
        entity_type="work_item",
        entity_id=event.item_id,
        detail={"kind": event.kind, "suppressed": suppressed, "delivered": ok},
    )
