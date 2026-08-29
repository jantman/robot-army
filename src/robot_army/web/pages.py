"""One function per view: a payload dict, and the HTML rendering of that same dict.

Every view here is assembled from an ``operations.*`` call or a ``db.*`` accessor and is
returned *as the JSON representation too* (R2) — the HTML is a rendering of the same dict,
not a second assembly of the same facts. That is what makes ``curl | jq`` against a running
interface show exactly what the page shows.

Nothing in this module changes state. Actions live in :mod:`robot_army.web.server`, which
calls :mod:`robot_army.operations` for every one of them (FR-047).

**The only external URLs any page emits are ``github.com`` ones**, constructed from data
already stored — repository key, issue number, branch — with no additional source-system
call (FR-043). :func:`github_link` is the single place that decides an href may point off
this machine, and a test asserts nothing else does (SC-009).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from robot_army import capacity as capacity_mod
from robot_army import control, db, health, operations
from robot_army import daemon as daemon_mod
from robot_army import ordering as ordering_mod
from robot_army.cardstates import CardState
from robot_army.states import SessionState, WorkItemState, is_legal_work_item_transition
from robot_army.web import html
from robot_army.web.html import (
    Markup,
    a,
    button,
    div,
    form,
    h,
    join,
    mark_simulated,
    p,
    span,
    table,
    tag,
)


@dataclass(frozen=True, slots=True)
class View:
    """Both representations of one page, from one assembly.

    ``data`` is what ``.json`` returns; ``body`` is what goes inside the content container.
    A route produces exactly one of these, and the server decides which half to send.
    """

    title: str
    data: dict[str, Any]
    body: Markup
    status: int = 200


# -- time -------------------------------------------------------------------

STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.strptime(str(stamp), STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def age_seconds(stamp: str | None, *, now: datetime | None = None) -> int | None:
    parsed = _parse(stamp)
    if parsed is None:
        return None
    return int(((now or datetime.now(UTC)) - parsed).total_seconds())


def human_age(seconds: int | None) -> str:
    """Relative age, because that is what makes a stale signal obvious at a glance.

    Absolute UTC is displayed beside it everywhere — the record format is UTC throughout
    and "3h ago" alone cannot be cross-referenced against the log.
    """
    if seconds is None:
        return "—"
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def when(stamp: str | None) -> Markup:
    """Absolute UTC plus relative age, the pair the Assumptions section settles on."""
    if not stamp:
        return Markup("—")
    return span(f"{stamp} ({human_age(age_seconds(stamp))} ago)", class_="mono")


# -- links ------------------------------------------------------------------

GITHUB = "https://github.com/"
_REPO_ISSUE = re.compile(r"^([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(\d+)$")
_REPO_ONLY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def github_link(value: Any) -> str | None:
    """The one place an href may leave this machine, and only to ``github.com``.

    Accepts what audit records actually carry: an already-built ``https://github.com/…``
    URL, an ``owner/repo#123`` target, or a bare ``owner/repo`` key. Anything else returns
    ``None`` and is rendered as plain text — a record can contain an issue body, and an
    arbitrary URL out of one must never become a link.
    """
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(GITHUB):
        return value
    matched = _REPO_ISSUE.match(value)
    if matched:
        return f"{GITHUB}{matched.group(1)}/issues/{matched.group(2)}"
    if _REPO_ONLY.match(value):
        return f"{GITHUB}{value}"
    return None


def issue_link(item: dict[str, Any]) -> Markup:
    url = github_link(item.get("source_url")) or github_link(item.get("source_id"))
    label = f"#{item.get('issue_number')}"
    if url is None:
        return span(label)
    return a(url, label, rel="noreferrer noopener")


#: The intake board's own host. The **only** other external origin this interface emits,
#: added by milestone 003 for the same reason ``github.com`` was allowed in 002: the link
#: is constructed from data already stored, with no additional source-system call, and
#: :func:`card_link` is the single place that decides an href may point at it.
TRELLO = "https://trello.com/"


def card_link(card: dict[str, Any] | None, *, label: str | None = None) -> Markup:
    """The card an item came from, rendered beside its issue (FR-017, FR-048).

    ``None`` renders as an em dash rather than nothing, so "this item did not come from a
    card" is visibly answered rather than looking like a missing field.
    """
    if not card:
        return span("—")
    url = card.get("card_url") or ""
    text = label or f"card {card.get('card_id')}"
    if not isinstance(url, str) or not url.startswith(TRELLO):
        # A card URL that is not a Trello URL is not a link. Everything this system
        # renders here it wrote itself, so this should be unreachable — which is exactly
        # when a fallback earns its place.
        return span(text)
    return a(url, text, rel="noreferrer noopener")


def item_link(item: dict[str, Any], *, include_simulated: bool = False) -> Markup:
    return join(
        [
            a(f"/item/{item['id']}{_query(include_simulated)}", f"item {item['id']}"),
            " ",
            mark_simulated(item.get("simulated")),
        ]
    )


def _query(include_simulated: bool, **extra: Any) -> str:
    """The query string every generated link carries.

    ``include_simulated`` is always stated, in both directions (009 FR-003). See
    :func:`robot_army.web.server.html_query` for why omission can no longer stand in for
    false now that the default varies by effect level.
    """
    parts = [f"{k}={v}" for k, v in extra.items() if v not in (None, "")]
    parts.append(f"include_simulated={'1' if include_simulated else '0'}")
    return "?" + "&".join(parts)


# -- chrome (FR-016 through FR-019) -----------------------------------------


def chrome(
    ctx: operations.Context,
    *,
    include_simulated: bool = False,
    simulated_preference: bool | None = None,
    path: str = "",
    effective_level: str | None = None,
    simulated_consequences: list[str] | None = None,
) -> dict[str, Any]:
    """The facts every view carries. Assembled once per request.

    ``daemon.running`` false is the FR-005 case: read views render normally, the chrome
    says so prominently, and controls that need the daemon refuse.

    ``effective_level`` is computed here and **only** here (009 FR-018). The banner and the
    level pill both read it out of this payload rather than each deriving it, which is what
    makes "the two cannot disagree" structural rather than a matter of remembering.
    """
    report = health.check(
        ctx.layout.heartbeat_path, max_age_seconds=ctx.config.health.max_age_seconds
    )
    beat = report.heartbeat or {}
    running = daemon_mod.is_locked(ctx.layout.lock_path)
    # Defaulting to our own configured level keeps a direct caller — a test, or a future
    # second entry point — from silently rendering a page with no level at all.
    effective_level = effective_level or str(ctx.effect_level)
    pause = db.get_dispatch_control(ctx.conn)
    anomalies = db.list_anomalies(ctx.conn)

    return {
        "effect_level": str(ctx.effect_level),
        "daemon": {
            "running": running,
            "pid": beat.get("pid") or daemon_mod.read_lock_holder(ctx.layout.lock_path),
            "activity": beat.get("activity"),
            "heartbeat_age_seconds": (
                int(report.age_seconds) if report.age_seconds is not None else None
            ),
            "healthy": report.healthy,
            "reason": report.reason,
            "effect_level": beat.get("effect_level"),
        },
        "effect_mismatch": effect_mismatch(ctx, report, running=running),
        # Resolved by the server, which is where this process's effect level is decided
        # (FR-053 keeps the level enum out of every module that does not decide one). The
        # string "unknown" means a daemon is running whose level could not be read — a
        # consumer must be able to tell "we could not tell" from "we did not say".
        "effective_level": effective_level,
        # Resolved alongside the level and for the same reason: deriving them needs the level
        # enum, which FR-053 keeps out of every module that does not decide a level. Empty at
        # ``live``, which is what makes "no banner at live" a fact about the data rather than
        # a branch in the renderer.
        "simulated_consequences": list(simulated_consequences or []),
        "dispatch_paused": pause.paused,
        "dispatch_paused_at": pause.paused_at,
        "dispatch_paused_by": pause.paused_by,
        "anomaly_count": len(anomalies),
        # On every view rather than only on the queue: "why is nothing running?" is asked
        # from wherever the author happens to be looking, and the answer is one line.
        "capacity": operations._capacity_dict(
            capacity_mod.snapshot(ctx.conn, config=ctx.config), ctx.config.dispatch.order
        ),
        "include_simulated": include_simulated,
        # What the operator *said*, beside what the interface *decided*. A reader of the
        # payload can tell a deliberate choice from a default without knowing the level.
        "simulated_preference": simulated_preference,
        # The path is chrome because the visibility toggle is chrome: it has to send the
        # reader back to the view they are on, and the chrome bar is the only thing rendered
        # on every view.
        "path": path,
        "pending_job_requests": control.pending(ctx.layout),
        "rendered_at": datetime.now(UTC).strftime(STAMP),
        "refresh_seconds": ctx.config.web.refresh_seconds,
    }


def effect_mismatch(
    ctx: operations.Context, report: Any = None, *, running: bool | None = None
) -> str | None:
    """Does the daemon's live effect level disagree with ours? (research.md R4)

    The daemon can be started with ``--effect-level plan`` while the configuration file
    says ``live``. Nothing in 001 detects the divergence, because until now the only other
    actor was a terminal command someone was typing deliberately. A tap on a phone is not
    that — without this guard the interface would launch real sessions for a daemon the
    author believes is only planning.

    Three states, not two, and the middle one is the reason this reads the lock as well as
    the heartbeat:

    * **No daemon holds the lock.** There is nothing to disagree with and the configured
      level applies. Refusing on the strength of a heartbeat left by a dead process would
      be the same class of surprise in the other direction.
    * **A daemon holds the lock and a heartbeat exists** — fresh *or* stale. The levels are
      compared. Staleness is not ignorance here: a daemon's effect level is fixed when it
      starts and cannot change while it runs, and a starting daemon writes its heartbeat
      before its first tick. So a stale heartbeat from the process currently holding the
      lock still names that process's level correctly; what staleness means is that a tick
      is running long, which is exactly when a big clone is in progress and exactly when
      launching more work at the wrong level would matter most.
    * **A daemon holds the lock and no heartbeat can be read at all.** The level is
      genuinely unknown, and this fails closed rather than open.

    Treating stale as "no disagreement" made the guard fail open in the one state it exists
    for: a daemon alive at ``plan``, an interface configured for ``live``, a tick running
    longer than the staleness threshold, and every mutation waved through.
    """
    if report is None:
        report = health.check(
            ctx.layout.heartbeat_path, max_age_seconds=ctx.config.health.max_age_seconds
        )
    if running is None:
        running = daemon_mod.is_locked(ctx.layout.lock_path)
    if not running:
        return None

    if not report.heartbeat:
        return (
            "EFFECT LEVEL UNKNOWN: a daemon is running, but no heartbeat can be read, so "
            "there is no way to tell which effect level it is at. Actions that touch work "
            f"are refused rather than performed at this interface's {str(ctx.effect_level)!r} "
            f"on the chance it disagrees — {report.reason}"
        )

    theirs = report.heartbeat.get("effect_level")
    if not theirs or str(theirs) == str(ctx.effect_level):
        return None

    staleness = (
        ""
        if report.healthy
        else (
            f" Its heartbeat is {int(report.age_seconds or 0)}s old, which means a tick is "
            "running long — not that the level changed, because it cannot while the daemon "
            "runs."
        )
    )
    return (
        f"EFFECT LEVEL MISMATCH: the daemon is running at {theirs!r} but this interface is "
        f"configured for {str(ctx.effect_level)!r}. Actions that touch work are refused "
        f"until they agree — restart one of them at the level you meant.{staleness}"
    )


# -- what a control means ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One control: what it is called, what it needs, and when it is legal.

    Deriving the legality from the item's state in **one** table is what makes FR-029 —
    "a control MUST NOT be offered where it is not valid" — a property of one function
    rather than a rule scattered through templates. The same table answers the question
    again at submission time (FR-027), so the offer and the refusal cannot disagree.
    """

    name: str
    label: str
    confirm: bool
    needs_daemon: bool = False
    #: Whether an effect-level disagreement with the daemon blocks it (R4).
    effect_guarded: bool = True
    danger: bool = False
    description: str = ""
    item_states: tuple[WorkItemState, ...] = ()
    session_states: tuple[SessionState, ...] = ()
    #: Set when the item's own state machine decides, rather than a fixed list.
    legal_target: WorkItemState | None = None
    needs_previous_session: bool = False


ITEM_ACTIONS: dict[str, ActionSpec] = {
    "resume": ActionSpec(
        name="resume",
        label="resume",
        confirm=True,
        needs_daemon=True,
        description=(
            "Start a new session restoring the previous session's context, in the same "
            "checkout. Preparation can take minutes; the item moves to dispatching first."
        ),
        item_states=(WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW),
        needs_previous_session=True,
    ),
    "restart": ActionSpec(
        name="restart",
        label="restart",
        confirm=True,
        needs_daemon=True,
        description=(
            "Start a fresh session in the existing checkout with no prior context. The "
            "previous session's transcript is not carried over."
        ),
        item_states=(WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW),
    ),
    "abandon": ActionSpec(
        name="abandon",
        label="abandon",
        confirm=True,
        danger=True,
        description=(
            "Mark the item abandoned. Non-destructive: the checkout and branch are left "
            "exactly where they are, and removing them stays a terminal command."
        ),
        legal_target=WorkItemState.ABANDONED,
    ),
    "cancel": ActionSpec(
        name="cancel",
        label="cancel session",
        confirm=True,
        danger=True,
        description=(
            "Stop this item's session and only this one. The item becomes interrupted and "
            "its checkout is untouched. Other running sessions are unaffected."
        ),
        item_states=(WorkItemState.ACTIVE,),
        session_states=(SessionState.STARTING, SessionState.RUNNING),
    ),
    "retry": ActionSpec(
        name="retry",
        label="retry",
        confirm=True,
        description=(
            "Move a failed item back to the queue. Refused, with the reason, if the "
            "condition that blocked it still holds."
        ),
        item_states=(WorkItemState.FAILED,),
    ),
    "attach": ActionSpec(
        name="attach",
        label="attach terminal",
        confirm=False,
        description="Open a terminal window on the desktop showing this running session.",
        item_states=(WorkItemState.ACTIVE,),
        session_states=(SessionState.RUNNING,),
    ),
}


def legal_actions(
    item: dict[str, Any], session: dict[str, Any] | None, *, has_previous_session: bool
) -> list[str]:
    """Which controls this item's current state permits. FR-029's single source."""
    state = WorkItemState(item["state"])
    session_state = SessionState(session["state"]) if session else None
    legal: list[str] = []
    for spec in ITEM_ACTIONS.values():
        if spec.legal_target is not None:
            if not is_legal_work_item_transition(state, spec.legal_target):
                continue
        elif state not in spec.item_states:
            continue
        if spec.session_states and session_state not in spec.session_states:
            continue
        if spec.needs_previous_session and not has_previous_session:
            continue
        legal.append(spec.name)
    return legal


def action_control(item_id: int, name: str, *, include_simulated: bool = False) -> Markup:
    """Render one control. Confirmed actions are a **link to a page**, not a form (R8)."""
    spec = ITEM_ACTIONS[name]
    if spec.confirm:
        return a(
            f"/item/{item_id}/confirm/{name}{_query(include_simulated)}",
            spec.label,
            class_="action danger" if spec.danger else "action",
        )
    return form(
        f"/item/{item_id}/{name}",
        html.hidden("include_simulated", "1" if include_simulated else "0"),
        button(spec.label, class_="danger" if spec.danger else None),
    )


def action_bar(
    item_id: int, actions: list[str], *, include_simulated: bool = False
) -> Markup:
    if not actions:
        return p("No action is legal for this item in its current state.", class_="empty")
    return div(
        *[action_control(item_id, name, include_simulated=include_simulated) for name in actions],
        class_="actions",
    )


# -- dispatch controls (FR-023, FR-035, T046, T051) -------------------------


def dispatch_controls(chrome: dict[str, Any], *, include_simulated: bool = False) -> Markup:
    """Pause, unpause, force a poll, force a reconciliation.

    These are the four controls that act on the *system* rather than on one work item, so
    they live on the queue view — the page whose subject is what dispatch is doing. None of
    them needs a confirmation: none stops, starts, or discards work. Pausing is reversible
    and its whole purpose is caution.
    """
    paused = bool(chrome.get("dispatch_paused"))
    pending = chrome.get("pending_job_requests") or []

    def control(action: str, label: str, note: str, **attributes: Any) -> Markup:
        return form(
            action,
            html.hidden("include_simulated", "1" if include_simulated else "0"),
            button(label, **attributes),
            span(note, class_="meta"),
        )

    return div(
        h(2, "dispatch"),
        div(
            control(
                "/dispatch/unpause" if paused else "/dispatch/pause",
                "resume dispatch" if paused else "pause dispatch",
                "held items dispatch on the next tick"
                if paused
                else "polling, reconciliation and the heartbeat keep running",
                class_="primary" if paused else None,
            ),
            control(
                "/poll",
                "poll now",
                "requested; the daemon runs it within one tick"
                if "poll" in pending
                else "ask GitHub for new work now",
            ),
            control(
                "/reconcile",
                "reconcile now",
                "requested; the daemon runs it within one tick"
                if "reconcile" in pending
                else "make the picture match reality now",
            ),
            class_="actions",
        ),
        p(
            "With no daemon running, poll and reconcile are performed directly rather than "
            "requested — the controls work either way.",
            class_="meta",
        )
        if not (chrome.get("daemon") or {}).get("running")
        else Markup(""),
        class_="card",
    )


# -- shared row assembly ----------------------------------------------------


def _items(
    ctx: operations.Context, *, include_simulated: bool, state: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Rows through ``operations.status``, so field names match ``_item_dict`` exactly.

    Returns the rows **and** how many matching rows were withheld. The count comes from the
    same call, under the same filters, as the listing it accompanies — milestone 008's
    discipline, and the reason the number this interface prints is the number the link would
    actually reveal (009 FR-007) rather than merely close to it.
    """
    data = operations.status(ctx, state=state, include_simulated=include_simulated).data
    return data["items"], int(data["withheld_simulated"]["items"])


def _session_for(ctx: operations.Context, item_id: int) -> dict[str, Any] | None:
    session = db.latest_session_for_item(ctx.conn, item_id)
    return operations._session_dict(session) if session else None


def _empty(text: str) -> Markup:
    return p(text, class_="empty")


def _reveal(path: str, *, include_simulated: bool) -> Markup:
    """A link to this same view with the visibility preference flipped."""
    label = "hide them" if include_simulated else "show them"
    return a(path + _query(not include_simulated), label)


def withheld_note(
    count: int, *, path: str, include_simulated: bool, when_visible: bool = True
) -> Markup:
    """"N simulated rows hidden — show them", or nothing at all (009 FR-006, FR-009).

    Rendered beneath a table that is showing fewer rows than it matched. Absent entirely when
    the count is zero, so a page with nothing to disclose says nothing — the alternative is a
    permanent "0 rows hidden" that trains the reader to skip the line that matters.

    ``when_visible`` is the other half of the rule: a view discloses **once**. If it is showing
    rows, the disclosure sits beneath them; if it is showing none, :func:`_nothing` has already
    carried it in place of the empty text, and repeating it here would state the same count
    twice on one page.
    """
    if not count or not when_visible:
        return Markup("")
    plural = "row" if count == 1 else "rows"
    return p(
        f"{count} simulated {plural} hidden — ",
        _reveal(path, include_simulated=include_simulated),
        class_="withheld",
    )


def _nothing(text: str, count: int, *, path: str, include_simulated: bool) -> Markup:
    """The empty state, which must never claim absence while withholding (009 FR-008).

    "Nothing is ready." and "everything ready is being hidden from you" are different facts,
    and reporting the second as the first is the whole defect this milestone exists to
    remove — one notch quieter than the version the issue reported, but the same one.
    """
    if not count:
        return _empty(text)
    plural = "row" if count == 1 else "rows"
    return p(
        f"Nothing to show here. {count} simulated {plural} {'is' if count == 1 else 'are'} "
        "hidden — ",
        _reveal(path, include_simulated=include_simulated),
        class_="empty",
    )


# -- /active (FR-011) -------------------------------------------------------


def active_view(ctx: operations.Context, *, include_simulated: bool = False) -> View:
    """What is running, and for how long."""
    rows: list[dict[str, Any]] = []
    items, withheld = _items(
        ctx, include_simulated=include_simulated, state=str(WorkItemState.ACTIVE)
    )
    for item in items:
        session = _session_for(ctx, item["id"])
        started = (session or {}).get("started_at") or item.get("updated_at")
        rows.append(
            {
                **item,
                "item_id": item["id"],
                "session_id": (session or {}).get("session_id"),
                "session_state": (session or {}).get("state"),
                "started_at": started,
                "elapsed_seconds": age_seconds(started),
            }
        )

    body = join(
        [
            h(1, "active"),
            _nothing(
                "Nothing is running.", withheld, path="/active", include_simulated=include_simulated
            )
            if not rows
            else table(
                [
                    "item",
                    "repo",
                    "issue",
                    "title",
                    "session",
                    # Milestone 007. The reason this page exists is to answer "what is
                    # running" from a phone, and a session five minutes into specify and one
                    # three hours into implement are the same row without it.
                    "spec-kit",
                    "started",
                    "elapsed",
                    "checkout",
                ],
                [
                    [
                        item_link(row, include_simulated=include_simulated),
                        row["repo_key"],
                        issue_link(row),
                        row["title"],
                        span(row["session_state"] or "—"),
                        _speckit_badge(row),
                        when(row["started_at"]),
                        human_age(row["elapsed_seconds"]),
                        join(
                            [
                                span(row["worktree_path"] or "—", class_="mono"),
                                tag("br"),
                                span(row["branch"] or "—", class_="mono"),
                            ]
                        ),
                    ]
                    for row in rows
                ],
            ),
            withheld_note(
                withheld,
                path="/active",
                include_simulated=include_simulated,
                when_visible=bool(rows),
            ),
        ]
    )
    return View(
        title="active",
        data={"items": rows, "count": len(rows), "withheld_simulated": withheld},
        body=body,
    )


# -- /queue (FR-012, FR-013) ------------------------------------------------


def queue_view(
    ctx: operations.Context,
    *,
    include_simulated: bool = False,
    chrome_payload: dict[str, Any] | None = None,
) -> View:
    """Ready in dispatch order with each hold's reason, dispatching with its age, blocked
    with the reason.

    The order is not derived here. ``ordering.plan`` produces it and ``select_and_dispatch``
    walks the same function, so the position shown is the real one by identity rather than
    by agreement (R8). This view used to carry a comment asserting that its ``ORDER BY id``
    matched what the dispatcher happened to do — true when it was written, and false the
    moment an ordering mode existed. There is nothing left to assert.
    """
    all_items, withheld = _items(ctx, include_simulated=include_simulated)
    max_age = ctx.config.daemon.dispatching_max_age_seconds

    snap = capacity_mod.snapshot(ctx.conn, config=ctx.config)
    plan = ordering_mod.plan(ctx.conn, config=ctx.config, capacity=snap)
    by_id = {item["id"]: item for item in all_items}
    ready: list[dict[str, Any]] = [
        {
            **by_id.get(entry.item.id, {}),
            "id": entry.item.id,
            "position": entry.position,
            "hold": str(entry.hold) if entry.hold else None,
            "hold_detail": entry.detail,
        }
        for entry in plan
        # A simulated row is planned regardless, because it occupies a slot; whether it is
        # *shown* is the viewer's filter, exactly as it is everywhere else.
        if entry.item.id in by_id
    ]

    dispatching: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in all_items:
        state = item["state"]
        if state == str(WorkItemState.DISPATCHING):
            row = db.get_work_item(ctx.conn, item["id"])
            age = age_seconds(row.dispatching_at if row else None)
            dispatching.append(
                {
                    **item,
                    "dispatching_at": row.dispatching_at if row else None,
                    "age_seconds": age,
                    "max_age_seconds": max_age,
                    "overdue": age is not None and age > max_age,
                }
            )
        elif state == str(WorkItemState.FAILED) or item.get("blocked_reason"):
            blocked.append(
                {
                    **item,
                    "reason": item.get("failure_reason") or item.get("blocked_reason") or "",
                }
            )

    # The chrome is already assembled once per request; reuse it rather than re-reading
    # the same three facts to decide which control to render.
    current = chrome_payload if chrome_payload is not None else chrome(
        ctx, include_simulated=include_simulated
    )
    pause_note: list[Any] = []
    if current.get("dispatch_paused"):
        pause_note.append(
            div(
                "Dispatch is paused. Eligible items accumulate here and dispatch when it "
                "is resumed; nothing is being rejected or lost.",
                class_="banner error",
            )
        )

    body = join(
        [
            h(1, "queue"),
            *pause_note,
            dispatch_controls(current, include_simulated=include_simulated),
            h(2, f"ready ({len(ready)}) — in dispatch order"),
            _nothing(
                "Nothing is ready.",
                withheld if not (dispatching or blocked) else 0,
                path="/queue",
                include_simulated=include_simulated,
            )
            if not ready
            else table(
                ["#", "item", "repo", "issue", "title", "status", "ready since"],
                [
                    [
                        row["position"],
                        item_link(row, include_simulated=include_simulated),
                        row["repo_key"],
                        issue_link(row),
                        row["title"],
                        # One reason per row and no log required, which is all FR-013 asks
                        # for. Held rows are marked so a full queue does not read as a
                        # queue that is simply about to move.
                        span(row["hold_detail"], class_="outcome-error")
                        if row["hold"]
                        else span("next to dispatch" if row["position"] == 1 else "waiting"),
                        when(row["updated_at"]),
                    ]
                    for row in ready
                ],
            ),
            h(2, f"dispatching ({len(dispatching)})"),
            _empty("Nothing is being prepared.")
            if not dispatching
            else table(
                ["item", "repo", "issue", "title", "age", "limit"],
                [
                    [
                        item_link(row, include_simulated=include_simulated),
                        row["repo_key"],
                        issue_link(row),
                        row["title"],
                        span(
                            human_age(row["age_seconds"]),
                            class_="outcome-error" if row["overdue"] else None,
                        ),
                        f"{row['max_age_seconds']}s",
                    ]
                    for row in dispatching
                ],
            ),
            h(2, f"blocked ({len(blocked)})"),
            _empty("Nothing is blocked.")
            if not blocked
            else table(
                ["item", "state", "repo", "issue", "reason", ""],
                [
                    [
                        item_link(row, include_simulated=include_simulated),
                        row["state"],
                        row["repo_key"],
                        issue_link(row),
                        row["reason"],
                        action_bar(
                            row["id"],
                            [
                                name
                                for name in legal_actions(row, None, has_previous_session=False)
                                if name in ("retry", "abandon")
                            ],
                            include_simulated=include_simulated,
                        ),
                    ]
                    for row in blocked
                ],
            ),
            withheld_note(
                withheld,
                path="/queue",
                include_simulated=include_simulated,
                when_visible=bool(ready or dispatching or blocked),
            ),
        ]
    )
    return View(
        title="queue",
        data={
            "ready": ready,
            "dispatching": dispatching,
            "blocked": blocked,
            "counts": {
                "ready": len(ready),
                "dispatching": len(dispatching),
                "blocked": len(blocked),
            },
            "dispatching_max_age_seconds": max_age,
            "capacity": operations._capacity_dict(snap, ctx.config.dispatch.order),
            "withheld_simulated": withheld,
        },
        body=body,
    )


# -- /interrupted (FR-014) --------------------------------------------------


def _signal_row(ctx: operations.Context, item: dict[str, Any]) -> dict[str, Any]:
    row = db.get_work_item(ctx.conn, item["id"])
    signals = operations.resume_signals(ctx, row) if row else {}
    session = _session_for(ctx, item["id"])
    return {
        **item,
        "item_id": item["id"],
        "ended_at": row.ended_at if row else None,
        "last_session": session,
        "uncommitted_changes": signals.get("uncommitted_changes"),
        "commits_on_branch": signals.get("commits_on_branch"),
        "issue_closed": signals.get("issue_closed"),
        "open_pr": signals.get("open_pull_request"),
        "signals_age_seconds": signals.get("signals_age_seconds"),
        "worktree_missing": not signals.get("worktree_present", False),
        "worktree_error": signals.get("worktree_error"),
        "github_error": signals.get("github_error"),
    }


def _tri(value: Any) -> Markup:
    """``True`` / ``False`` / "not known", kept distinct. "I could not ask" is not "no"."""
    if value is None:
        return span("unknown", class_="empty")
    return span("yes" if value else "no")


def _signals_cell(row: dict[str, Any]) -> Markup:
    items: list[Any] = [
        tag("dt", "uncommitted"),
        tag("dd", _tri(row["uncommitted_changes"])),
        tag("dt", "commits"),
        tag("dd", "—" if row["commits_on_branch"] is None else str(row["commits_on_branch"])),
        tag("dt", "issue closed"),
        tag("dd", _tri(row["issue_closed"])),
        tag("dt", "open PR"),
        tag(
            "dd",
            a(row["open_pr"], "yes", rel="noreferrer noopener")
            if github_link(row["open_pr"])
            else _tri(bool(row["open_pr"]) if row["issue_closed"] is not None else None),
        ),
    ]
    age = row.get("signals_age_seconds")
    footnote: list[Any] = []
    if age is not None:
        # R9: the GitHub-derived pair may be up to a minute old, and a cached value must be
        # visible as such rather than implied to be current.
        footnote.append(
            span(
                "GitHub signals computed just now"
                if age == 0
                else f"GitHub signals {age}s old (cached)",
                class_="meta",
            )
        )
    if row.get("github_error"):
        footnote.append(span(f"GitHub unreachable: {row['github_error']}", class_="meta"))
    if row.get("worktree_error"):
        footnote.append(span(f"checkout unreadable: {row['worktree_error']}", class_="meta"))
    parts = [tag("dl", join(items), class_="kv"), *footnote]
    return join(parts)


def _interrupted_card(
    row: dict[str, Any], *, include_simulated: bool, has_previous_session: bool
) -> Markup:
    warnings: list[Any] = []
    if row["worktree_missing"]:
        warnings.append(
            div(
                "The isolated checkout is missing. Resuming will fail until it is restored; "
                "abandoning is the usual answer.",
                class_="banner error",
            )
        )
    return div(
        h(3, join([item_link(row, include_simulated=include_simulated), " — ", row["title"]])),
        p(
            join(
                [
                    row["repo_key"],
                    " ",
                    issue_link(row),
                    " · branch ",
                    span(row["branch"] or "—", class_="mono"),
                ]
            ),
            class_="meta",
        ),
        p(join(["ended ", when(row["ended_at"])]), class_="meta"),
        *warnings,
        _signals_cell(row),
        action_bar(
            row["id"],
            legal_actions(row, row.get("last_session"), has_previous_session=has_previous_session),
            include_simulated=include_simulated,
        ),
        class_="card",
    )


def interrupted_view(ctx: operations.Context, *, include_simulated: bool = False) -> View:
    """Interrupted items with the four FR-014 signals, plus what is awaiting review.

    ``awaiting_review`` is listed in its own section rather than left out: resume, restart
    and abandon are all legal there, and with no listing containing those items the only
    way to reach one would be to type its id into the address bar. A control that exists
    but cannot be navigated to is a gap, not a scope boundary.
    """
    interrupted_items, withheld_interrupted = _items(
        ctx, include_simulated=include_simulated, state=str(WorkItemState.INTERRUPTED)
    )
    interrupted = [_signal_row(ctx, item) for item in interrupted_items]
    awaiting_items, withheld_awaiting = _items(
        ctx, include_simulated=include_simulated, state=str(WorkItemState.AWAITING_REVIEW)
    )
    awaiting = [_signal_row(ctx, item) for item in awaiting_items]
    # The sum of the two state-filtered counts, which is exactly the set the reveal link
    # would surface for this view -- not the unfiltered total, which would name rows this
    # page would still not show (009 FR-007).
    withheld = withheld_interrupted + withheld_awaiting

    def cards(rows: list[dict[str, Any]]) -> Markup:
        return join(
            _interrupted_card(
                row,
                include_simulated=include_simulated,
                has_previous_session=row.get("last_session") is not None,
            )
            for row in rows
        )

    body = join(
        [
            h(1, "interrupted"),
            _nothing(
                "Nothing is interrupted.",
                withheld if not awaiting else 0,
                path="/interrupted",
                include_simulated=include_simulated,
            )
            if not interrupted
            else cards(interrupted),
            h(2, f"awaiting review ({len(awaiting)})"),
            p(
                "Sessions that exited cleanly. The same three decisions apply.",
                class_="meta",
            ),
            _empty("Nothing is awaiting review.") if not awaiting else cards(awaiting),
            withheld_note(
                withheld,
                path="/interrupted",
                include_simulated=include_simulated,
                when_visible=bool(interrupted or awaiting),
            ),
        ]
    )
    return View(
        title="interrupted",
        data={
            "items": interrupted,
            "awaiting_review": awaiting,
            "counts": {"interrupted": len(interrupted), "awaiting_review": len(awaiting)},
            "withheld_simulated": withheld,
        },
        body=body,
    )


# -- /anomalies (FR-017, FR-024) --------------------------------------------


def anomalies_view(ctx: operations.Context, *, include_simulated: bool = False) -> View:
    payload = operations.anomalies(ctx).data
    rows = payload["anomalies"]
    body = join(
        [
            h(1, f"anomalies ({len(rows)})"),
            p(
                "Conditions the system detected but cannot resolve on its own. "
                "Acknowledging one lifts it out of the outstanding count and lets a "
                "genuinely new occurrence be recorded later.",
                class_="meta",
            ),
            _empty("Nothing outstanding.")
            if not rows
            else join(
                div(
                    h(3, f"[{row['id']}] {row['kind']}"),
                    p(
                        f"{row['entity_type'] or '—'}:{row['entity_id'] or '—'} · detected ",
                        when(row["detected_at"]),
                        class_="meta",
                    ),
                    tag(
                        "dl",
                        join(
                            join([tag("dt", key), tag("dd", str(value))])
                            for key, value in (row.get("detail") or {}).items()
                        ),
                        class_="kv",
                    ),
                    div(
                        form(
                            f"/anomalies/{row['id']}/acknowledge",
                            html.hidden("include_simulated", "1" if include_simulated else "0"),
                            button("acknowledge"),
                        ),
                        class_="actions",
                    ),
                    class_="card",
                )
                for row in rows
            ),
            h(2, "kinds this system can raise"),
            html.ul(payload["known_kinds"]),
        ]
    )
    return View(
        title="anomalies",
        data={"anomalies": rows, "known_kinds": payload["known_kinds"], "count": len(rows)},
        body=body,
    )


# -- /cards (milestone 003, FR-026, FR-049) ---------------------------------

#: How a card's state reads to someone who did not write the state machine. The web is the
#: phone-shaped surface, and ``needs_info`` alone does not say what to do about it.
CARD_STATE_HELP: dict[str, str] = {
    "discovered": "seen, not yet evaluated",
    "needs_info": "held — the card does not say which repository",
    "creating": "filing an issue for it",
    "linked": "an issue exists for it",
    "dropped": "no longer tagged, archived, or deleted",
}


def cards_view(ctx: operations.Context, *, include_simulated: bool = False) -> View:
    """The card listing, mirroring ``robot-army cards`` (FR-026).

    Assembled from ``operations.cards`` rather than from ``db`` directly, which is
    milestone 002's FR-047 rule and the reason the two front ends cannot drift.
    """
    result = operations.cards(ctx, include_simulated=include_simulated)
    payload = result.data
    if not payload.get("configured"):
        return View(
            title="cards",
            data=payload,
            body=join(
                [
                    h(1, "cards"),
                    _empty(
                        "No intake board is configured, so no cards are being read. "
                        "Add a [trello] section to enable the card source."
                    ),
                ]
            ),
        )

    rows = payload["cards"]
    withheld = int(payload.get("withheld_simulated") or 0)
    # "Held" and "parked" are two different conditions and a card can be both at once —
    # awaiting clarification *and* sitting in a column the author excluded. `held` is the
    # state's own word, already used in CARD_STATE_HELP above; `parked` is milestone 006's.
    # A parked card is deliberately **not** counted as outstanding: it is not waiting on
    # the author, it is where the author put it (FR-006, FR-009).
    held = [
        row
        for row in rows
        if row["state"] == str(CardState.NEEDS_INFO) and not row.get("parked")
    ]
    parked = [row for row in rows if row.get("parked")]
    body = join(
        [
            h(1, f"cards ({len(rows)})"),
            p(
                "Cards on the intake board and what became of them. A card in "
                "needs_info is waiting for you to say which repository it is for — edit "
                "the card and it is picked up automatically, or rescan to force a look now.",
                class_="meta",
            ),
            p(
                f"{len(parked)} parked: tagged, but in a column you excluded from intake. "
                "Move one out and it is picked up on the next poll — nothing else to do.",
                class_="meta",
            )
            if parked
            else Markup(""),
            _nothing(
                "Nothing on the board yet.",
                withheld,
                path="/cards",
                include_simulated=include_simulated,
            )
            if not rows
            else join(
                [
                    h(2, f"awaiting clarification ({len(held)})") if held else Markup(""),
                    _cards_table(held, include_simulated=include_simulated, rescannable=True)
                    if held
                    else Markup(""),
                    h(2, "every tracked card"),
                    _cards_table(rows, include_simulated=include_simulated, rescannable=False),
                ]
            ),
            withheld_note(
                withheld,
                path="/cards",
                include_simulated=include_simulated,
                when_visible=bool(rows),
            ),
        ]
    )
    return View(
        title="cards",
        data={**payload, "needs_info": len(held), "parked": len(parked)},
        body=body,
    )


def _cards_table(
    rows: list[dict[str, Any]], *, include_simulated: bool, rescannable: bool
) -> Markup:
    return table(
        ["card", "title", "state", "repository", "issue", "reason", "in state", ""],
        [
            [
                card_link(row, label=row["card_id"]),
                join([row["title"], " ", mark_simulated(row["simulated"])]),
                span(
                    row["state"],
                    title=CARD_STATE_HELP.get(row["state"], ""),
                    class_="mono",
                ),
                row["repo_key"] or "—",
                _card_issue_cell(row, include_simulated=include_simulated),
                _card_reason_cell(row),
                human_age(row["age_seconds"]),
                rescan_control(row["card_id"], include_simulated=include_simulated)
                if rescannable
                else Markup(""),
            ]
            for row in rows
        ],
    )


def _card_reason_cell(row: dict[str, Any]) -> Markup:
    """Why the card is where it is — parked, its state's own reason, or both.

    Never "held" for a parked card: ``CARD_STATE_HELP`` already renders ``needs_info`` as
    "held", and one word for two unrelated conditions is how the author reads one as the
    other.
    """
    parts = []
    if row.get("parked"):
        parts.append(span(f"parked in {row['parked_list']!r}", class_="mono"))
    if row["reason"]:
        parts.append(span(row["reason"]))
    if not parts:
        return span("—")
    return join([parts[0]] if len(parts) == 1 else [parts[0], " — ", parts[1]])


def _card_issue_cell(row: dict[str, Any], *, include_simulated: bool = False) -> Markup:
    """The issue a card produced, and the work item it became, where each exists."""
    if row["issue_number"] is None:
        return span("—")
    url = github_link(row.get("issue_url")) or github_link(row.get("source_id"))
    issue = (
        a(url, f"#{row['issue_number']}", rel="noreferrer noopener")
        if url
        else span(f"#{row['issue_number']}")
    )
    if not row.get("work_item_id"):
        return issue
    return join(
        [
            issue,
            " · ",
            a(
                f"/item/{row['work_item_id']}{_query(include_simulated)}",
                f"item {row['work_item_id']}",
            ),
        ]
    )


def rescan_control(card_id: str, *, include_simulated: bool = False) -> Markup:
    """The one mutating control on this view. Confirm-then-post, like every other one."""
    return a(
        f"/card/{card_id}/confirm/rescan{_query(include_simulated)}",
        "rescan",
        class_="button",
    )


def card_confirm_view(
    ctx: operations.Context,
    card_id: str,
    *,
    include_simulated: bool = False,
    refusal: str | None = None,
) -> View:
    """The confirmation step, matching the pattern every other mutating route uses."""
    body = join(
        [
            h(1, f"rescan card {card_id}?"),
            p(
                "This asks the running daemon to re-read every card awaiting "
                "clarification on its next tick, this one included. It writes nothing to "
                "the board and creates nothing unless a card now names one repository.",
                class_="meta",
            ),
            html.banner("refused", refusal) if refusal else Markup(""),
            div(
                form(
                    f"/card/{card_id}/rescan",
                    html.hidden("include_simulated", "1" if include_simulated else "0"),
                    button("rescan"),
                ),
                a(f"/cards{_query(include_simulated)}", "cancel"),
                class_="actions",
            ),
        ]
    )
    return View(title=f"rescan {card_id}", data={"card_id": card_id, "action": "rescan"}, body=body)


# -- /item/<id> (FR-015, FR-029) --------------------------------------------


def _cleanup_cell(item: dict[str, Any]) -> Any:
    """What cleanup decided about this item's disk, in one cell.

    ``—`` means never considered, which is what ``NULL`` means and is not the same as
    "clean": every item predating the migration reads this way, and so does every item
    while ``[cleanup] on_issue_close`` is off.
    """
    state = item.get("cleanup_state")
    if not state:
        return span("— never considered", class_="quiet")
    reason = item.get("cleanup_reason") or ""
    label = f"{state} — {reason}" if reason else state
    return span(label, class_="outcome-error" if state != "done" else None)


def _speckit_cell(item: dict[str, Any]) -> Any:
    """How far a Spec Kit run has got, in one cell (milestone 007).

    ``—`` means there is nothing to say, which covers both "not a Spec Kit repository" and
    "a session that judged this issue did not warrant the lifecycle". The second is a
    correct outcome rather than a stall (FR-016), so it must not be rendered as a warning.
    """
    rung = item.get("speckit_phase")
    if not rung:
        return span("—", class_="quiet")
    directory = item.get("speckit_feature_dir") or ""
    return join([rung, " ", span(directory, class_="mono")])


def _speckit_badge(row: dict[str, Any]) -> Any:
    """The same fact, compressed for a listing row where most rows have nothing to say."""
    rung = row.get("speckit_phase")
    return span(rung, class_="mono") if rung else span("—", class_="quiet")


def item_view(
    ctx: operations.Context, item_id: int, *, include_simulated: bool = False
) -> View:
    result = operations.show(ctx, item_id)
    if result.code != operations.EXIT_OK:
        return not_found_view(f"No work item with id {item_id}.")

    payload = result.data
    item = payload["item"]
    sessions = payload["sessions"]
    session = sessions[-1] if sessions else None
    actions = legal_actions(item, session, has_previous_session=bool(sessions))
    payload = {**payload, "actions": actions, "latest_session": session}

    signals_block: list[Any] = []
    if item["state"] in (str(WorkItemState.INTERRUPTED), str(WorkItemState.AWAITING_REVIEW)):
        signals_block = [h(2, "resume-decision signals"), _signals_cell(_signal_row(ctx, item))]

    body = join(
        [
            h(
                1,
                join(
                    [
                        f"item {item['id']} — ",
                        item["title"],
                        " ",
                        mark_simulated(item["simulated"]),
                    ]
                ),
            ),
            tag(
                "dl",
                join(
                    [
                        tag("dt", "state"),
                        tag("dd", item["state"]),
                        tag("dt", "repository"),
                        tag("dd", item["repo_key"]),
                        tag("dt", "issue"),
                        tag("dd", issue_link(item)),
                        tag("dt", "card"),
                        tag("dd", card_link(payload.get("card"))),
                        tag("dt", "checkout"),
                        tag("dd", span(item["worktree_path"] or "—", class_="mono")),
                        tag("dt", "branch"),
                        tag("dd", span(item["branch"] or "—", class_="mono")),
                        tag("dt", "failure"),
                        tag("dd", item["failure_reason"] or "—"),
                        tag("dt", "blocked"),
                        tag("dd", item["blocked_reason"] or "—"),
                        # A retained worktree or branch, with the guard that kept it. The
                        # question this answers is asked long after the fact — "why is this
                        # 499 MB still here?" — so it belongs beside the path, not in a log.
                        tag("dt", "cleanup"),
                        tag(
                            "dd",
                            _cleanup_cell(item),
                        ),
                        tag("dt", "spec-kit"),
                        tag("dd", _speckit_cell(item)),
                    ]
                ),
                class_="kv",
            ),
            action_bar(item["id"], actions, include_simulated=include_simulated),
            *signals_block,
            h(2, "state history"),
            table(
                ["when", "what"],
                [[when(stamp), what] for stamp, what in payload["history"]],
            ),
            h(2, f"session attempts ({len(sessions)})"),
            _empty("No session has been attempted.")
            if not sessions
            else table(
                ["#", "state", "session id", "pid", "exit", "signal", "started", "ended"],
                [
                    [
                        row["attempt"],
                        join([row["state"], " ", mark_simulated(row["dry_run"])]),
                        span(row["session_id"], class_="mono"),
                        row["pid"] if row["pid"] is not None else "—",
                        row["exit_code"] if row["exit_code"] is not None else "—",
                        row["signal"] if row["signal"] is not None else "—",
                        when(row["started_at"]),
                        when(row["ended_at"]),
                    ]
                    for row in sessions
                ],
            ),
            h(2, "audit log for this item"),
            p(a(f"/log?item={item['id']}", "every record naming this item")),
        ]
    )
    return View(title=f"item {item['id']}", data=payload, body=body)


# -- /item/<id>/confirm/<action> (FR-026, FR-027, R8) -----------------------


def confirm_view(
    ctx: operations.Context,
    item_id: int,
    action: str,
    *,
    include_simulated: bool = False,
    refusal: str | None = None,
) -> View:
    """Name the item and the action, re-validated against **current** state.

    When the action is no longer legal this page says so and **renders no form at all** —
    which is where FR-027's re-validation becomes visible before the tap rather than
    arriving as a failure after it.
    """
    spec = ITEM_ACTIONS.get(action)
    if spec is None:
        return not_found_view(f"There is no {action!r} action.")
    row = db.get_work_item(ctx.conn, item_id)
    if row is None:
        return not_found_view(f"No work item with id {item_id}.")

    item = operations._item_dict(row)
    session = _session_for(ctx, item_id)
    sessions_exist = bool(db.list_sessions_for_item(ctx.conn, item_id))
    legal = legal_actions(item, session, has_previous_session=sessions_exist)
    is_legal = action in legal

    reason = refusal
    if not is_legal and reason is None:
        reason = (
            f"This item is {item['state']}, and {action} is not legal from that state. "
            "It changed after the page you came from was rendered."
        )

    data = {
        "item": item,
        "action": action,
        "legal": is_legal,
        "reason": reason,
        "legal_actions": legal,
    }

    if not is_legal:
        body = join(
            [
                h(1, f"{action} item {item_id}?"),
                div(reason, class_="banner error"),
                p(a(f"/item/{item_id}{_query(include_simulated)}", "open the item")),
                action_bar(item_id, legal, include_simulated=include_simulated),
            ]
        )
        return View(title=f"{action} item {item_id}", data=data, body=body, status=409)

    body = join(
        [
            h(1, f"{action} item {item_id}?"),
            div(
                p(join([item["title"], " ", mark_simulated(item["simulated"])])),
                p(
                    join(
                        [
                            item["repo_key"],
                            " ",
                            issue_link(item),
                            " · state ",
                            span(item["state"], class_="mono"),
                        ]
                    ),
                    class_="meta",
                ),
                p(spec.description),
                class_="card",
            ),
            form(
                f"/item/{item_id}/{action}",
                html.hidden("include_simulated", "1" if include_simulated else "0"),
                div(
                    button(
                        f"yes, {spec.label}",
                        class_="danger" if spec.danger else "primary",
                    ),
                    a(
                        f"/item/{item_id}{_query(include_simulated)}",
                        "no, go back",
                        class_="action",
                    ),
                    class_="actions",
                ),
            ),
        ]
    )
    return View(title=f"{action} item {item_id}", data=data, body=body)


# -- /log (FR-042, FR-043, FR-044) ------------------------------------------


def _record_target(record: dict[str, Any], *, include_simulated: bool = False) -> Markup:
    """The record's subject, as a link where it names something followable."""
    parts: list[Any] = []
    entity_type = record.get("entity_type")
    entity_id = record.get("entity_id")
    if entity_type == "work_item" and entity_id is not None:
        parts.append(a(f"/item/{entity_id}{_query(include_simulated)}", f"item {entity_id}"))
    elif entity_type:
        parts.append(span(f"{entity_type}:{entity_id}"))
    target = record.get("target")
    if target:
        url = github_link(target)
        parts.append(" ")
        parts.append(a(url, target, rel="noreferrer noopener") if url else span(target))
    return join(parts) if parts else Markup("—")


def _record_detail(detail: Any) -> Markup:
    """Detail, with GitHub URLs it already contains rendered as links (FR-043).

    Only values this module recognises as ``github.com`` become links; everything else is
    text. A record can carry an issue body, and an arbitrary URL out of one must never
    become a followable link on a page the author trusts.
    """
    if not isinstance(detail, dict) or not detail:
        return Markup("")
    parts: list[Any] = []
    for key, value in detail.items():
        url = github_link(value)
        parts.append(tag("dt", key))
        parts.append(
            tag(
                "dd",
                a(url, str(value), rel="noreferrer noopener")
                if url
                else span(json.dumps(value, default=str) if not isinstance(value, str) else value),
            )
        )
    return tag("dl", join(parts), class_="kv")


def log_view(
    ctx: operations.Context,
    *,
    item_id: int | None = None,
    since: str | None = None,
    outcome: str | None = None,
    cursor: str | None = None,
    include_simulated: bool = False,
) -> View:
    result = operations.read_log_page(
        ctx, item_id=item_id, since=since, outcome=outcome, cursor=cursor
    )
    if result.code != operations.EXIT_OK:
        return View(
            title="log",
            data={"error": "\n".join(result.lines)},
            body=join([h(1, "log"), div("\n".join(result.lines), class_="banner error")]),
            status=400,
        )

    payload = result.data
    records = payload["records"]
    filters = payload["filters"]

    # The active filter is always rendered (FR-042): a page that silently narrows what it
    # shows is worse than one that shows nothing.
    controls = div(
        html.void(
            "input",
            type="text",
            name="item",
            value=filters["item"] if filters["item"] is not None else "",
            placeholder="item id",
            inputmode="numeric",
        ),
        html.void(
            "input",
            type="text",
            name="since",
            value=filters["since"] or "",
            placeholder="since — 30s, 10m, 2h, 1d",
        ),
        tag(
            "select",
            join(
                tag("option", label, value=value, selected=(filters["outcome"] or "") == value)
                for value, label in (
                    ("", "any outcome"),
                    ("ok", "ok"),
                    ("error", "error"),
                    ("pending", "pending"),
                )
            ),
            name="outcome",
        ),
        html.hidden("include_simulated", "1" if include_simulated else "0"),
        button("filter"),
        a(f"/log{_query(include_simulated)}", "clear", class_="action"),
        class_="filters",
    )
    filter_form = form("/log", controls, method="get")

    active_filters = [f"{k}={v}" for k, v in filters.items() if v not in (None, "")]
    more = (
        p(
            a(
                "/log" + _query(include_simulated, cursor=payload["next_cursor"], **{
                    k: v for k, v in filters.items() if v not in (None, "")
                }),
                "older records →",
                class_="action",
            )
        )
        if payload["has_more"]
        else p("End of the record.", class_="empty")
    )

    body = join(
        [
            h(1, "audit log"),
            filter_form,
            p(
                "filters: " + (", ".join(active_filters) if active_filters else "none"),
                class_="meta",
            ),
            p(
                f"{payload['skipped_lines']} unparseable line(s) skipped. A partially "
                "written final line is expected — the process can die between the write "
                "and the flush.",
                class_="meta",
            )
            if payload["skipped_lines"]
            else Markup(""),
            _empty("No records match.")
            if not records
            else join(
                div(
                    div(
                        span(record.get("ts", "—"), class_="ts mono"),
                        " ",
                        span(record.get("component", "—"), class_="mono"),
                        " ",
                        tag("strong", record.get("action", "—")),
                        " ",
                        span(
                            record.get("outcome", "—"),
                            class_=f"outcome-{record.get('outcome')}",
                        ),
                        " ",
                        mark_simulated(record.get("simulated") or record.get("dry_run")),
                    ),
                    div(_record_target(record, include_simulated=include_simulated), class_="meta"),
                    div(_record_detail(record.get("detail")), class_="detail"),
                    class_="record",
                )
                for record in records
            ),
            more,
        ]
    )
    return View(title="log", data=payload, body=body)


# -- error pages ------------------------------------------------------------


def not_found_view(reason: str) -> View:
    """404 as a page, not a bare status line (contracts/http-api.md)."""
    return View(
        title="not found",
        data={"ok": False, "reason": reason, "code": operations.EXIT_FAILED},
        body=join([h(1, "not found"), p(reason), p(a("/active", "back to active"))]),
        status=404,
    )


def method_not_allowed_view(path: str, allowed: list[str]) -> View:
    reason = f"{path} accepts {', '.join(allowed)}. No GET on this interface changes state."
    return View(
        title="method not allowed",
        data={"ok": False, "reason": reason, "code": operations.EXIT_USAGE},
        body=join([h(1, "method not allowed"), p(reason), p(a("/active", "back to active"))]),
        status=405,
    )


def refusal_view(
    *, reason: str, status: int, code: int, extra: dict[str, Any] | None = None
) -> View:
    return View(
        title="refused",
        data={"ok": False, "reason": reason, "code": code, **(extra or {})},
        body=join(
            [h(1, "refused"), div(reason, class_="banner error"), p(a("/active", "back to active"))]
        ),
        status=status,
    )
