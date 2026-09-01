"""Where a message goes.

This module exists because there have always been **two** senders and only one of them
went through a boundary (research.md R2). The four notification event kinds reach
``boundaries.notifier`` and are simulated below ``live``; the stale-heartbeat alert does
not and never did — ``operations.health_check`` calls the transport directly. Without a
shared module, "which channels are configured" would be decided in two places, and the day
they disagreed would be the day the author stopped being told something.

Two channels, one protocol, one builder:

* ``WebhookChannel`` — the generic JSON POST that has always been here, unchanged in what
  it puts on the wire.
* ``PushoverChannel`` — form-encoded, because Pushover does not accept an arbitrary JSON
  body. That is the whole of issue #106: ``health.post_json``'s docstring claimed a generic
  webhook covered Pushover, and it did not.

The single ``send(title, message, fields)`` signature is what lets one module serve both
senders. ``fields`` is the message's structured payload; each channel takes what it
understands and ignores the rest — the webhook splices the whole dict into its body, and
Pushover reads only ``url``. That is how both of today's wire bodies survive byte-for-byte
(FR-016) while a push notification still gets the one field it can use.

Every channel is bound by two rules, and both are contracts rather than habits:

* **A channel never raises.** A channel failure is not the caller's problem (FR-010): the
  state change it describes already happened and is already in the log. Exceptions are
  caught here and returned as ``(False, reason)``.
* **A channel never blocks indefinitely.** Explicit timeout, no retry (Principle IV).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from robot_army.config import Config, PushoverConfig

#: Pushover's message endpoint. Not configurable: there is one Pushover, and a knob whose
#: only job would be to be configured once and then forgotten is the one Principle I names.
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

#: Pushover's documented field limits. Exceeding one is a 4xx rather than a truncation, so
#: we truncate first: a rejected message tells the author nothing, a truncated one tells
#: them most of it, and the audit record keeps the rest.
PUSHOVER_MESSAGE_LIMIT = 1024
PUSHOVER_TITLE_LIMIT = 250


@runtime_checkable
class Channel(Protocol):
    """One place a message can go.

    ``send`` returns ``(delivered, detail)``. The bool is what ``Notifier.send`` needs; the
    string is the human-readable line ``robot-army health --notify`` prints per channel.
    Neither raises — see the module docstring.
    """

    name: str

    def send(self, title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]: ...


def webhook_body(title: str, message: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The generic webhook's wire body, and the one place a field could be added carelessly.

    **The single composer.** ``boundaries.notifier.compose`` calls into this rather than
    building the same dict a second way, so the body the simulated notifier records and the
    body the real channel posts cannot drift apart — before this, they were two functions
    that happened to agree.

    Every value comes from an identifier or a state name the system chose. Nothing is
    interpolated from a response, an exception, or a header, which is what makes FR-037 and
    FR-007 properties of this function rather than rules spread across the call sites.
    """
    return {
        "title": title,
        "message": message,
        **fields,
        "host": os.uname().nodename,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class WebhookChannel:
    """The generic JSON POST to ``[health] webhook_url``. Unchanged on the wire."""

    name = "webhook"

    def __init__(self, url: str) -> None:
        self._url = url

    def send(self, title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]:
        from robot_army import health

        try:
            return health.post_json(self._url, webhook_body(title, message, fields))
        except Exception as exc:  # noqa: BLE001 - a channel failure is never the caller's
            return False, f"webhook channel failed: {exc}"


class PushoverChannel:
    """A form-encoded POST to Pushover. The reason this module exists.

    Deliberately absent: ``priority``, ``sound``, ``device``, ``expire``, ``retry``,
    ``html``, ``timestamp``, ``url_title`` and ``attachment``. Each would be a
    configuration knob with exactly one caller and no second use in hand (Principle I), and
    each can be added the day something concrete needs it.
    """

    name = "pushover"

    def __init__(self, config: PushoverConfig) -> None:
        self._config = config

    def send(self, title: str, message: str, fields: dict[str, Any]) -> tuple[bool, str]:
        from robot_army import health

        try:
            # Read at the moment they are needed, never at construction and never retained
            # on the instance (FR-003). A file that vanished between load and now becomes a
            # recorded failure naming the *path*, never the contents.
            data = {
                "token": self._config.read_token(),
                "user": self._config.read_user_key(),
                "title": title[:PUSHOVER_TITLE_LIMIT],
                "message": message[:PUSHOVER_MESSAGE_LIMIT],
            }
            url = fields.get("url")
            if url:
                data["url"] = str(url)
            # The credentials travel in the body, not the URL. ``post_form``'s messages
            # interpolate the URL, so this is what makes them safe to log by construction
            # rather than by a rule someone has to remember (R4).
            return health.post_form(PUSHOVER_API_URL, data)
        except Exception as exc:  # noqa: BLE001 - a channel failure is never the caller's
            return False, f"pushover channel failed: {exc}"


def build(config: Config) -> tuple[Channel, ...]:
    """Every channel this configuration has, in a stable order.

    An empty tuple means **no request is constructed** — not "one is built and skipped at
    the last moment", which is the distinction milestone 004 drew for ``[notifications]
    events`` and the one that makes an unconfigured installation provably silent.

    Order is not semantically meaningful: neither channel depends on the other and one's
    failure never stops the other. It is fixed so the log and the tests read the same way
    every time.
    """
    channels: list[Channel] = []
    if config.health.webhook_url:
        channels.append(WebhookChannel(config.health.webhook_url))
    if config.pushover is not None:
        channels.append(PushoverChannel(config.pushover))
    return tuple(channels)


def names(channels: tuple[Channel, ...]) -> tuple[str, ...]:
    """The channel names, for audit records and the startup line."""
    return tuple(channel.name for channel in channels)
