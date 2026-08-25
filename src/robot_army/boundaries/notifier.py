"""The ninth boundary: saying out loud that something happened.

Two implementations, both required by FR-040 — real at ``live``, simulated everywhere
below. The simulated one logs the call with its full arguments and returns a structurally
valid result, exactly as the other simulated writers do, so the simulated path cannot
quietly diverge from the real one.

The real one reuses ``health.post_json``: one bounded-timeout POST for the whole project,
one timeout to keep correct, and one channel — ``[health] webhook_url`` — rather than a
second URL knob whose only job would be to be configured separately and then forgotten
(R14).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from robot_army.boundaries import NotificationEvent

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config


def compose(event: NotificationEvent) -> dict[str, Any]:
    """The wire body, and the one place a field could be added carelessly.

    Every value here comes from an identifier or a state name the system chose. Nothing is
    interpolated from a response, an exception, or a header — which is what makes FR-037 a
    property of this function rather than a rule spread across four call sites.
    """
    return {
        "title": event.title,
        "message": event.detail,
        "kind": event.kind,
        "item_id": event.item_id,
        "repo_key": event.repo_key,
        "url": event.url,
        "host": os.uname().nodename,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class WebhookNotifier:
    """The real implementation. Never raises: a channel failure is not the operation's."""

    def __init__(self, config: Config, audit: AuditLog) -> None:
        self._config = config
        self._audit = audit

    def send(self, event: NotificationEvent) -> bool:
        from robot_army import health

        url = self._config.health.webhook_url
        if not url:
            # Configured to notify with nowhere to notify. The config loader already warned
            # about this at startup; repeating it as an error per event would be noise.
            return False
        ok, detail = health.post_json(url, compose(event))
        if not ok:
            self._audit.record(
                "notify.failed",
                outcome="error",
                entity_type="work_item",
                entity_id=event.item_id,
                detail={"kind": event.kind, "reason": detail},
            )
        return ok


class SimulatedNotifier:
    """Logs the call with its full arguments and returns success, as the other simulated
    writers do. Below ``live`` this is what "an outward-facing write" means."""

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit

    def send(self, event: NotificationEvent) -> bool:
        self._audit.record(
            "notify.send",
            outcome="ok",
            simulated=True,
            entity_type="work_item",
            entity_id=event.item_id,
            detail={"kind": event.kind, "body": compose(event)},
        )
        return True
