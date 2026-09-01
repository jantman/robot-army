"""The ninth boundary: saying out loud that something happened.

Two implementations, both required by FR-040 — real at ``live``, simulated everywhere
below. The simulated one logs the call with its full arguments and returns a structurally
valid result, exactly as the other simulated writers do, so the simulated path cannot
quietly diverge from the real one.

The real one is a **fan-out**. Milestone 004 wired one webhook here and said so:

    "No second HTTP client, no second URL knob: ``[health] webhook_url`` is the channel."

Still one HTTP client and still no second URL knob — but since issue #106 there are zero,
one, or two *channels*, built by :func:`robot_army.channels.build`, and every message goes
to each of them. The reason is a plain factual correction: ``health.post_json``'s docstring
claimed a generic JSON webhook covered Pushover, and Pushover takes form-encoded parameters
(research.md R1).

Two consequences worth keeping in view:

* **A message and a delivery are different things.** ``notify.send`` records the message;
  ``notify.channel`` records each delivery. Without the second record the log could not say
  "the webhook took it and Pushover did not", which is exactly the case an author needs it
  to answer. ``notify.failed`` was folded into ``notify.channel`` rather than kept, because
  two audit actions saying one thing is worse than one.
* **One channel's failure is never another's.** The loop does not short-circuit, the return
  is "any channel accepted", and the caller is told nothing it could act on — a channel
  failure is not the operation's problem (FR-010).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from robot_army import channels as channels_mod
from robot_army.boundaries import NotificationEvent

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.channels import Channel


def event_fields(event: NotificationEvent) -> dict[str, Any]:
    """The structured half of a notification message.

    The one place this mapping lives. Every value is an identifier or a state name the
    system chose; nothing is interpolated from a response, an exception, or a header, which
    is what makes FR-037 a property of this function rather than a rule spread across four
    call sites.
    """
    return {
        "kind": event.kind,
        "item_id": event.item_id,
        "repo_key": event.repo_key,
        "url": event.url,
    }


def compose(event: NotificationEvent) -> dict[str, Any]:
    """The generic webhook's body for an event.

    A thin call into :func:`robot_army.channels.webhook_body` rather than a second way to
    build the same dict: before, the body the simulated notifier recorded and the body the
    real channel posted were two functions that happened to agree.
    """
    return channels_mod.webhook_body(event.title, event.detail, event_fields(event))


class MultiNotifier:
    """The real implementation: every configured channel, each recorded separately.

    Never raises — the channels do not either, and this loop is the second belt to their
    braces. With no channel configured it sends nothing and returns ``False``: the config
    loader already warned at startup, and repeating that as an error per event would be
    noise.
    """

    def __init__(self, channels: tuple[Channel, ...], audit: AuditLog) -> None:
        self._channels = channels
        self._audit = audit

    def describe_name(self) -> str:
        """Named for the startup record: ``MultiNotifier`` alone would hide the one fact a
        reader of that record wants, which is *which* channels are live (FR-057)."""
        return f"MultiNotifier({', '.join(channels_mod.names(self._channels))})"

    def send(self, event: NotificationEvent) -> bool:
        delivered = False
        for channel in self._channels:
            ok, detail = channel.send(event.title, event.detail, event_fields(event))
            self._audit.record(
                "notify.channel",
                outcome="ok" if ok else "error",
                entity_type="work_item",
                entity_id=event.item_id,
                detail={
                    "channel": channel.name,
                    "kind": event.kind,
                    # ``detail`` carries our own message, never the upstream response body.
                    "reason": detail,
                },
            )
            # Deliberately not `break` and deliberately not `and`: every channel is
            # attempted whatever the ones before it did (FR-009, FR-010).
            delivered = delivered or ok
        return delivered


class SimulatedNotifier:
    """Logs the call with its full arguments and returns success, as the other simulated
    writers do. Below ``live`` this is what "an outward-facing write" means.

    It records the *names* of the channels that were configured as well as the composed
    body. One record for the one message, not one per channel: below ``live`` there are no
    deliveries to record — only an intent, and the intent is one message aimed at a known
    list.
    """

    def __init__(self, audit: AuditLog, channels: tuple[Channel, ...] = ()) -> None:
        self._audit = audit
        self._channels = channels

    def describe_name(self) -> str:
        return f"SimulatedNotifier({', '.join(channels_mod.names(self._channels))})"

    def send(self, event: NotificationEvent) -> bool:
        self._audit.record(
            "notify.send",
            outcome="ok",
            simulated=True,
            entity_type="work_item",
            entity_id=event.item_id,
            detail={
                "kind": event.kind,
                "body": compose(event),
                "channels": list(channels_mod.names(self._channels)),
            },
        )
        return True
