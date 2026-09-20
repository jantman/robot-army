"""HTML production: escaping, element helpers, the page chrome, and the two assets.

Two rules are structural rather than stylistic:

* **No value reaches the output without passing through** :func:`escape`. The element
  helpers below escape both text and attribute values; the only helper that emits a
  string verbatim is :class:`Markup`, which exists so already-built markup can be nested
  and is never constructed from anything derived from a request or a database row.
* **The stylesheet and the script are module constants** (R12), served from fixed routes.
  No request contributes to a filesystem path anywhere in this package, so path traversal
  is structurally impossible rather than defended against.

Nothing here fetches from a third-party host: SC-009 requires every view to work with the
machine offline, which rules out web fonts, CDN stylesheets, and icon sets. The only
external URLs any page emits are the ``github.com`` links the audit and item views build
from data already stored (FR-043).
"""

from __future__ import annotations

import hashlib
import html as _html
from collections.abc import Iterable
from typing import Any

from robot_army import timefmt
from robot_army.health import HealthState


class Markup(str):
    """A string that is already HTML and must not be escaped again.

    Deliberately a distinct type rather than a convention: :func:`escape` passes it
    through and escapes everything else, so "did I remember to escape this?" is answered
    by the type rather than by the author's memory.
    """

    __slots__ = ()


def escape(value: Any) -> str:
    """Escape anything for HTML text or an attribute value.

    ``quote=True`` because the same function is used for both positions, and an
    unescaped quote in an attribute is the whole vulnerability.
    """
    if isinstance(value, Markup):
        return str(value)
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def raw(text: str) -> Markup:
    """Mark a string this module built as already-escaped. Never call it on input."""
    return Markup(text)


def attrs(mapping: dict[str, Any] | None) -> str:
    """Render an attribute mapping. ``None`` and ``False`` values omit the attribute."""
    if not mapping:
        return ""
    parts: list[str] = []
    for name, value in mapping.items():
        if value is None or value is False:
            continue
        if value is True:
            parts.append(f" {escape(name)}")
        else:
            parts.append(f' {escape(name)}="{escape(value)}"')
    return "".join(parts)


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    """``class_`` → ``class``, ``data_refresh`` → ``data-refresh``.

    Python keywords and hyphenated attribute names are the two things a ``**kwargs``
    signature cannot express directly; this is the whole translation.
    """
    return {key.rstrip("_").replace("_", "-"): value for key, value in attributes.items()}


def tag(_name: str, *children: Any, **attributes: Any) -> Markup:
    """One element. The tag name is positional-by-convention so ``name=`` stays free
    for the HTML attribute of that name, which forms genuinely need."""
    body = "".join(escape(child) for child in children if child is not None)
    return Markup(f"<{_name}{attrs(_clean(attributes))}>{body}</{_name}>")


def void(_name: str, **attributes: Any) -> Markup:
    return Markup(f"<{_name}{attrs(_clean(attributes))}>")


def join(children: Iterable[Any]) -> Markup:
    return Markup("".join(escape(child) for child in children))


# -- convenience elements ---------------------------------------------------


def div(*children: Any, **attributes: Any) -> Markup:
    return tag("div", *children, **attributes)


def p(*children: Any, **attributes: Any) -> Markup:
    return tag("p", *children, **attributes)


def span(*children: Any, **attributes: Any) -> Markup:
    return tag("span", *children, **attributes)


def h(level: int, *children: Any, **attributes: Any) -> Markup:
    return tag(f"h{level}", *children, **attributes)


def a(href: str, *children: Any, **attributes: Any) -> Markup:
    return tag("a", *children, href=href, **attributes)


def ul(items: Iterable[Any], **attributes: Any) -> Markup:
    return tag("ul", join(tag("li", item) for item in items), **attributes)


def table(headers: list[str], rows: Iterable[Iterable[Any]], **attributes: Any) -> Markup:
    """A table inside its own scroll container.

    The container is not decoration: SC-013 forbids horizontal scrolling of the *page* at
    390 pixels, and a table of eight columns cannot honour that unless it scrolls inside
    something.
    """
    head = tag("tr", join(tag("th", header) for header in headers))
    body = join(tag("tr", join(tag("td", cell) for cell in row)) for row in rows)
    return div(
        tag("table", tag("thead", head), tag("tbody", body), **attributes),
        class_="scroll",
    )


def form(action: str, *children: Any, method: str = "post", **attributes: Any) -> Markup:
    return tag("form", *children, action=action, method=method, **attributes)


def button(label: str, **attributes: Any) -> Markup:
    return tag("button", label, type="submit", **attributes)


def hidden(name: str, value: Any) -> Markup:
    return void("input", type="hidden", name=name, value=value)


SIMULATED_MARK = Markup('<span class="sim" title="simulated (dry-run) row">simulated</span>')


def mark_simulated(simulated: Any) -> Markup:
    """FR-019: every simulated row carries a visible marker wherever it is shown."""
    return SIMULATED_MARK if simulated else Markup("")


# -- page chrome ------------------------------------------------------------

#: The label is what the reader sees; the path is what the reader never types. ``needs me``
#: covers the three states that page lists — interrupted, awaiting review and failed — where
#: ``interrupted`` named one of them and so gave no reason to click when the other two were
#: what was waiting (issue #182). The route stays ``/interrupted`` deliberately: renaming it
#: would rewrite every generated disclosure link, the route table and the terminal mapping to
#: buy a tidier URL on an interface where navigation is these six words.
NAV: tuple[tuple[str, str], ...] = (
    ("/active", "active"),
    ("/queue", "queue"),
    ("/interrupted", "needs me"),
    ("/cards", "cards"),
    ("/anomalies", "anomalies"),
    ("/log", "log"),
)

#: Banner keys the ``?msg=`` parameter may carry. A closed set, because the alternative —
#: rendering arbitrary text from the query string — is how a redirect becomes an injection
#: vector, and because a banner nobody wrote is a banner nobody can explain.
BANNERS: dict[str, tuple[str, str]] = {
    "resumed": ("ok", "Resume requested. The item moves to dispatching, then active."),
    "restarted": ("ok", "Restart requested. The item moves to dispatching, then active."),
    "abandoned": ("ok", "Item abandoned. Its worktree was left in place."),
    "cancelled": ("ok", "Session stopped. The item is interrupted and its worktree untouched."),
    "retried": ("ok", "Item moved back to the queue."),
    "reset": (
        "ok",
        "Work discarded and the item moved back to the queue. Its checkout and branch are "
        "gone; the issue was re-read and will be worked from scratch.",
    ),
    "attached": ("ok", "A terminal window was opened onto that session."),
    "acknowledged": ("ok", "Anomaly acknowledged."),
    "held": (
        "ok",
        "Held. Held work stays in the queue, in the position it would occupy anyway, and "
        "dispatches nothing until it is released. Nothing else is affected.",
    ),
    "released": (
        "ok",
        "Hold released. The work dispatches on the next tick, exactly as if it had never "
        "been held.",
    ),
    "paused": ("ok", "Dispatch paused. Eligible items accumulate in ready."),
    "unpaused": ("ok", "Dispatch resumed."),
    "polled": ("ok", "Poll requested. The result appears in the audit log."),
    "reconciled": ("ok", "Reconciliation requested. The result appears in the audit log."),
    "rescanned": (
        "ok",
        "Re-evaluation requested. Every card awaiting clarification is re-read on the "
        "daemon's next tick, and the result appears in the audit log.",
    ),
    "refused": ("error", "That action was refused. See the reason below."),
    "failed": ("error", "That action failed. See the audit log for what happened."),
}


def banner(key: str | None, reason: str | None = None) -> Markup:
    if not key:
        return Markup("")
    entry = BANNERS.get(key)
    if entry is None:
        return Markup("")
    level, text = entry
    children: list[Any] = [text]
    if reason:
        children.append(div(reason, class_="reason"))
    return div(*children, class_=f"banner {level}")


def _visibility_suffix(chrome: dict[str, Any]) -> str:
    """The query every internal link carries so a stated preference survives it (FR-003).

    Stated in both directions and never omitted: once the default varies by effect level,
    an absent parameter means "use the default" and can no longer stand in for false.

    **Unless there is nothing to carry.** The dead-end pages — 404, 405, the Host refusal,
    a schema mismatch — are rendered by ``server._bare``, which has no database context and
    so cannot resolve a level or a default. Its chrome omits the key entirely, and treating
    that absence as a stated ``0`` would put ``?include_simulated=0`` on every nav link of
    an error page: on a ``plan`` instance, one tap from a 404 would pin "hide everything"
    and land the reader on exactly the empty-looking page this milestone exists to remove.
    Nothing known, nothing stated, and the destination applies its own default.
    """
    stated = chrome.get("include_simulated")
    if stated is None:
        return ""
    return f"?include_simulated={'1' if stated else '0'}"


def _chrome_notices(chrome: dict[str, Any], *, running: bool, level: str) -> list[Any]:
    """The banners beneath the pills: conditions under which the page does not mean
    what it appears to mean.

    Split out of :func:`_chrome_bar` rather than inlined with it because the two answer
    different questions — the pills say what is true, these say what to distrust — and
    because one function carrying both had grown past what a reader can hold at once.
    Ordered by severity, and each one absent when it has nothing to say.
    """
    notices: list[Any] = []
    if not running:
        notices.append(
            div(
                "The daemon is not running. Everything below is the last recorded state, "
                "not a description of what is happening now. Controls that need the daemon "
                "will refuse.",
                class_="banner error",
            )
        )
    mismatch = chrome.get("effect_mismatch")
    if mismatch:
        notices.append(div(mismatch, class_="banner error"))
    # The same family, one step down in severity (issue #30). The cap the pill counts
    # against is the running daemon's, which can differ from the one this process read at
    # startup — and a number silently substituted is a number the reader cannot reconcile
    # with the file open in their editor.
    #
    # It **warns** rather than errors because nothing is wrong with the page and nothing is
    # refused: the daemon enforces its own cap whatever this process believes, so a
    # disagreement cannot make an action unsafe. Only the effect level, where acting at the
    # wrong one really does something in the world, earns the error colour and a refusal.
    #
    # Nothing is said when the daemon's cap could not be read at all — the notice is absent
    # rather than hedged — for the reason the block below gives about the level: that state
    # already has a banner saying more than this one could.
    cap_note = (chrome.get("capacity") or {}).get("cap_disagreement")
    if cap_note:
        notices.append(div(cap_note, class_="banner warn"))
    # The last member of the family the three above belong to: conditions under which what
    # you are reading does not mean what it appears to mean. This one is the broadest, since
    # it changes the meaning of *every* value on the page rather than one of them — an item
    # shown as `linked` against issue #900001 is linked to nothing at all.
    #
    # Nothing is said when the level cannot be read. That state already has a banner, two
    # lines above, which says more than this one could; a page carrying one account of a
    # situation beats a page carrying two.
    consequences = chrome.get("simulated_consequences") or []
    if consequences:
        # The prose around the list is derived too, not only the list. ``plan`` is the one
        # level at which *nothing* happened, and saying so is the strongest true statement
        # available — but repeating it at ``local``, where branches and commits are really
        # created, or at ``no-remote``, where a real session runs in a real terminal, would
        # be the "single message reused below live" that FR-013 forbids. Getting the list
        # right and leaving the sentences around it fixed is the same defect, quieter.
        total = bool(chrome.get("all_effects_simulated"))
        opening = (
            "nothing on this page really happened"
            if total
            else "parts of what these rows describe did not really happen"
        )
        tail = (
            "The rows are real rows describing work the daemon planned and did not carry "
            "out. Nothing here reached your repositories, GitHub, Trello, or a terminal."
            if total
            else "The rows are real rows. Everything listed above was skipped; anything "
            "not listed was really carried out."
        )
        notices.append(
            div(
                f"This instance is set up for testing, not real work. At effect level "
                f"{level}, {opening}:",
                ul(consequences),
                div(tail, class_="reason"),
                class_="banner error",
            )
        )
    return notices


def _chrome_bar(chrome: dict[str, Any]) -> Markup:
    """The facts FR-016 through FR-018 require on **every** view, not on a status page.

    One rule decides whether a pill is here at all, and issue #182 is what settled it:
    **the bar reads as "everything here is something to know"**. A pill that would only ever
    state the value you get unless you went out of your way to change it is not rendered —
    its absence says the same thing, more quietly, and the bar already teaches that
    convention through the pause pill and the three banners below.

    A *count* is the exception, and not an inconsistent one. ``0 need me`` and
    ``0 anomalies`` stay on screen because they answer a question the reader is asking; an
    ``effect level: live`` pill answers one nobody asked.
    """
    daemon = chrome.get("daemon") or {}
    running = bool(daemon.get("running"))
    suffix = _visibility_suffix(chrome)
    age = daemon.get("heartbeat_age_seconds")
    if running:
        state = f"daemon running (pid {daemon.get('pid') or '?'})"
        if not daemon.get("healthy"):
            # The verdict's own word, not "STALE" for every one of them (issue #52). A
            # daemon that holds the lock and has stopped beating is HUNG; one that has just
            # taken the lock and not beaten yet is STARTING, which is an ordinary restart
            # rather than a fault to go hunting.
            #
            # The word comes from the enum rather than being derived here, because the whole
            # point of the issue is that two surfaces may not describe one machine
            # differently — and a renderer that upper-cased the value itself would be a
            # second definition of the label waiting to drift from `robot-army health`'s.
            # A payload with no verdict at all falls back to ``stale``, which is the word
            # this line printed for every unhealthy daemon before the issue.
            state += f" — {HealthState(daemon.get('state') or 'stale').label}"
    else:
        state = "DAEMON NOT RUNNING"
    if age is not None:
        state += f", heartbeat {int(age)}s old"
    activity = daemon.get("activity")
    if running and activity:
        state += f", {activity}"

    # The level pill carries the alarm below ``live`` and **is not rendered at all** at
    # ``live`` (009 FR-016, FR-017, revised by issue #182).
    #
    # 009 argued the pill should be present and calm at ``live``: decorating the expected
    # state would train the operator to ignore the one place the level is shown. That
    # argument is about not *alarming* at ``live`` and it still holds — it is why the pill
    # below is plain when it appears. What it does not reach is whether a calm pill belongs
    # on screen at all, and the rest of this bar already answers that the other way. The
    # pause pill, the effect-mismatch banner, the cap-disagreement note and the
    # simulated-consequences banner are every one of them absent when there is nothing to
    # say, so an absent level pill reads as ``live`` by the convention the bar teaches.
    # A pill that only ever states the value you get unless you went out of your way is not
    # something to know; it is something to learn to skip.
    #
    # The condition is inequality with ``live`` rather than membership of the below-live
    # set, and that is the whole care in this line. ``unknown`` — a running daemon whose
    # level could not be read, and every page ``server._bare`` renders — keeps its pill.
    # "We could not tell" is not the default state; it is news.
    #
    # The word is in the text as well as in the colour, so a monochrome screenshot, a
    # colour-blind reader, and `curl | grep` all still carry the signal.
    level = str(chrome.get("effective_level") or chrome.get("effect_level") or "unknown")
    simulated = level != "live"
    pills: list[Any] = []
    if simulated:
        pills.append(
            span(f"effect level: {level} — simulated", class_="pill level simulated")
        )
    pills.append(
        span(state, class_="pill " + ("ok" if running and daemon.get("healthy") else "warn"))
    )
    # The capacity pill (milestone 004). On every view rather than on the queue alone,
    # because "why is nothing running?" is asked from wherever the author is looking, and
    # the answer — including whether the sessions filling the machine are the author's own —
    # is one line. It links to the queue, where the per-item reasons are.
    capacity = chrome.get("capacity") or {}
    if capacity:
        if not capacity.get("observable", True):
            pills.append(
                a(
                    "/queue" + suffix,
                    f"capacity UNOBSERVABLE — {capacity.get('reason')}",
                    class_="pill warn",
                )
            )
        else:
            total = int(capacity.get("total") or 0)
            cap = int(capacity.get("global_cap") or 0)
            # The snapshot's own phrase, which names the registry-blind terms when present
            # so the pill sums to its total (issue #61). The fallback is the older shape, for
            # a chrome dict built without it.
            breakdown = capacity.get("breakdown") or (
                f"{capacity.get('ours', 0)} ours, {capacity.get('others', 0)} other"
            )
            label = f"{total}/{cap} sessions ({breakdown})"
            if capacity.get("degraded"):
                label += " — degraded"
            pills.append(
                a(
                    "/queue" + suffix,
                    label,
                    class_="pill " + ("warn" if cap and total >= cap else "quiet"),
                )
            )
        pills.append(span(f"order: {capacity.get('order')}", class_="pill quiet"))

    # How much work is parked on the author (issue #182). The bar named the effect level, the
    # daemon, the capacity, the order and the anomalies, and said nothing whatever about work
    # items — so exiting a session sent the item to ``awaiting_review``, off ``/active``, and
    # no number anywhere noticed. The count is the anomaly pill's shape on purpose: a count on
    # every view, linking to the page that explains it, is already the pattern for this.
    #
    # Guarded on the key's **presence**, not its value. ``server._bare`` — 404, 405, schema
    # refusals — renders with no database and so counts nothing; omitting the key there and
    # skipping the pill here keeps "we did not count" distinct from "we counted nothing",
    # which is the same distinction ``_visibility_suffix`` above preserves for the toggle.
    # (It is deliberately *not* what ``anomaly_count`` does: ``_bare`` sets that to zero, and
    # an error page therefore prints "0 anomalies" having asked nobody.)
    if "waiting_count" in chrome:
        waiting = int(chrome.get("waiting_count") or 0)
        pills.append(
            a(
                "/interrupted" + suffix,
                f"{waiting} need{'s' if waiting == 1 else ''} me",
                class_="pill " + ("warn" if waiting else "quiet"),
            )
        )

    anomalies = int(chrome.get("anomaly_count") or 0)
    pills.append(
        a(
            "/anomalies" + suffix,
            f"{anomalies} anomal{'y' if anomalies == 1 else 'ies'}",
            class_="pill " + ("warn" if anomalies else "quiet"),
        )
    )
    if chrome.get("dispatch_paused"):
        # Converted *here* rather than in the chrome dict, which ``server._render`` merges
        # into the JSON body: that value is simultaneously a machine-readable field and
        # something a person reads, and only the second may be local (010 R3).
        since = timefmt.local(chrome.get("dispatch_paused_at")) or "unknown time"
        by = chrome.get("dispatch_paused_by") or "?"
        # A link, not a label: the pause is visible from every view, so the control that
        # lifts it has to be reachable from every view too.
        pills.append(
            a("/queue" + suffix, f"DISPATCH PAUSED since {since} (by {by})", class_="pill warn")
        )
    # Rendered when simulated rows are being **included**, and not when they are being
    # hidden (issue #182, revising 009 R9).
    #
    # R9's complaint was not that the override was missing but that "nothing on the page
    # suggests the parameter exists", and a label found only after the parameter has been
    # found is no answer to that. Sound then; `withheld_note` did not exist yet. It does
    # now, and it renders "N simulated rows hidden — show them", with the reveal link,
    # beneath any table that actually withheld rows, and nothing at all when the count is
    # zero. That is R9's discoverability offered exactly when there is something to
    # discover — so R9 is satisfied elsewhere rather than abandoned, and this pill
    # duplicated it whenever it mattered and was noise the rest of the time.
    #
    # Deleting it was conditional on a check, because if some view could withhold rows
    # without rendering a `withheld_note` then on that page this pill was the only route
    # back. Every view that filters by `include_simulated` — /active, /queue, /interrupted,
    # /cards, /anomalies and /log — discloses on the view itself; /item and the confirm
    # pages look up by identity and withhold nothing. Verified, hence the deletion.
    #
    # The polarity looks inverted next to the level pill above and is not. Below `live` the
    # default is to *include* simulated rows, so this pill is normally visible on a testing
    # instance — which is the same rule in both cases: the pill marks the surprising state,
    # and below `live` the page is full of rows describing things that did not happen.
    included = chrome.get("include_simulated")
    if included:
        # The key is absent entirely on the dead-end pages, which have no context to resolve
        # a default from — and a toggle that reports a state it had to guess is worse than no
        # toggle. Absent and false both render nothing, for different reasons.
        path = chrome.get("path") or "/active"
        pills.append(
            a(
                f"{path}?include_simulated=0",
                "simulated rows included",
                class_="pill quiet",
            )
        )

    return Markup(
        str(div(*pills, class_="chrome"))
        + "".join(
            str(n) for n in _chrome_notices(chrome, running=running, level=level)
        )
    )



def page(
    *,
    title: str,
    chrome: dict[str, Any],
    body: Any,
    path: str = "",
    message: str | None = None,
    reason: str | None = None,
    refresh_seconds: int = 10,
) -> str:
    """The whole document. One place, so every view carries the same chrome.

    ``data-refresh`` and ``data-path`` are read by ``app.js``; with scripting off the page
    is still correct, merely static until reloaded (R2).
    """
    # The nav carries the visibility preference too (009 FR-003). It is the most likely way
    # an operator leaves a page, and until 009 it was the one link on the page that dropped
    # their choice — hide the simulated rows, tap "cards", and they are back.
    suffix = _visibility_suffix(chrome)
    nav = join(
        a(href + suffix, label, class_="current" if path.startswith(href) else None)
        for href, label in NAV
    )
    rendered_at = timefmt.local(chrome.get("rendered_at", "")) or ""
    return (
        "<!DOCTYPE html>\n"
        + str(
            tag(
                "html",
                tag(
                    "head",
                    void("meta", charset="utf-8"),
                    void(
                        "meta",
                        name="viewport",
                        content="width=device-width, initial-scale=1",
                    ),
                    tag("title", f"robot-army — {title}"),
                    void("link", rel="stylesheet", href=asset_url("/static/app.css", APP_CSS)),
                    tag("script", src=asset_url("/static/app.js", APP_JS), defer=True),
                ),
                tag(
                    "body",
                    tag(
                        "header",
                        a("/active" + suffix, "robot-army", class_="brand"),
                        tag("nav", nav),
                    ),
                    tag(
                        "main",
                        _chrome_bar(chrome),
                        banner(message, reason),
                        div(body, id="content"),
                        id="main",
                        data_refresh=str(refresh_seconds),
                        data_path=path,
                    ),
                    tag(
                        "footer",
                        span(f"rendered {rendered_at}", class_="rendered"),
                        span("", id="age", class_="age"),
                    ),
                ),
                lang="en",
            )
        )
    )


def asset_url(path: str, content: str) -> str:
    """``/static/app.css?v=<hash of its content>``.

    The assets are cached for an hour, because a phone re-fetching a page every ten seconds
    must not re-download the stylesheet each time. Without this the other half of that trade
    would be an upgrade taking up to an hour to become visible — which bit during this
    milestone's own testing, on a browser that had loaded the page minutes earlier.

    Hashing the content into the URL removes the problem rather than shortening it: new
    bytes mean a new URL, so the stale cache entry is never consulted again. The route
    ignores the query string, so nothing about serving changes.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{path}?v={digest}"


# -- assets (R12: module constants, never read from disk) -------------------

APP_CSS = """
/* One column, large touch targets, no horizontal page scroll at 390px (FR-007, SC-013).
   No web font: SC-009 requires every view to work with the machine offline. */
:root {
  --bg: #14161a; --panel: #1d2027; --line: #2e323c; --text: #e7e9ee;
  --muted: #9aa1ae; --ok: #4caf82; --warn: #d98c3f; --error: #d9534f; --link: #7fb2ff;
  /* Two widths, because a paragraph and a table want different ones (issue #148). Until
     they were separated there was one, 60rem, and it was applied to both: right for the
     prose it was chosen for, and half a monitor for a nine-column table.

     --measure is that original width, kept and pointed at what it was for. --page is
     where the content area stops growing, set to exactly a full-size monitor — so on the
     1920-pixel screen in the issue nothing is narrowed by it at all, and on an ultrawide
     a row's first and last cells stay close enough to be read as one row.

     Lengths, not colours: the light-scheme block below swaps colours and must not repeat
     these, or a lit room would lay out differently from a dark one. */
  --measure: 60rem; --page: 120rem;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --line: #d9dde4; --text: #1b1e24;
    --muted: #5b626e; --ok: #1f7a52; --warn: #a35c12; --error: #b3312d; --link: #1a5fd0;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  overflow-x: hidden;
}
a { color: var(--link); }
header {
  display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline;
  padding: .75rem 1rem; background: var(--panel); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 2;
}
.brand { font-weight: 700; text-decoration: none; color: var(--text); }
nav { display: flex; flex-wrap: wrap; gap: .25rem; }
nav a {
  display: inline-block; padding: .6rem .8rem; min-height: 44px; line-height: 1.8;
  text-decoration: none; border-radius: 6px; color: var(--muted);
}
nav a.current { background: var(--line); color: var(--text); }
/* The content area is bounded by the page, and the prose inside it by the measure. One
   bound for both is what issue #148 reported: on a 1920-pixel window the /active table
   rendered at 928 pixels, with 480 of nothing on each side and every title wrapped over
   five or six lines.

   The second rule is a descendant selector on purpose. A child combinator would cap the
   five things that happen to sit directly under #content and miss the rest — the audit
   records, an item page's field list, and everything inside the wrapping div that the
   /queue repositories block puts around its own contents.

   Headings are not in the list: they are short, and one is a page title. Nor is .chrome,
   whose pills wrap, and which reads better on one line than on three. */
main { padding: 1rem; max-width: var(--page); margin: 0 auto; }
main p, main ul, main dl, main .banner, main .card, main .record, main .filters {
  max-width: var(--measure);
}
footer { padding: 1rem; color: var(--muted); font-size: .875rem; display: flex; gap: 1rem; }
h1 { font-size: 1.3rem; margin: 0 0 .75rem; }
h2 { font-size: 1.05rem; margin: 1.5rem 0 .5rem; }
.chrome { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .75rem; }
.pill {
  display: inline-block; padding: .3rem .6rem; border-radius: 999px;
  background: var(--panel); border: 1px solid var(--line); font-size: .875rem;
  text-decoration: none; color: var(--text);
}
.pill.warn { border-color: var(--warn); color: var(--warn); }
/* The level pill had no rule at all until 009, so the one thing on the page that said
   nothing here is real rendered in the same weight as `order: oldest-first`. The error
   colour rather than warn because warn is already spent on capacity and on a paused
   dispatch, and neither of those outranks "none of this happened". */
.pill.level.simulated {
  border-color: var(--error); color: var(--error); font-weight: 700;
}
/* There is no `.pill.level.live` rule because there is no such pill: since issue #182 the
   level pill is rendered only when the level is not `live`, so the calm variant this rule
   used to style has no elements left to style. */
.pill.ok { border-color: var(--ok); color: var(--ok); }
.pill.quiet { color: var(--muted); }
.banner {
  padding: .75rem 1rem; border-radius: 8px; margin: .5rem 0;
  border: 1px solid var(--line); background: var(--panel);
}
.banner.ok { border-color: var(--ok); }
.banner.warn { border-color: var(--warn); }
.banner.error { border-color: var(--error); color: var(--error); }
.banner .reason { color: var(--text); font-size: .9375rem; margin-top: .4rem; }
/* The same element on a card, where there is no banner to inherit from. Set apart
   from the signals above it and kept at body weight: this is the sentence the
   failed item is on this page to show, not an aside. */
.card .reason { color: var(--text); font-size: .9375rem; margin-top: .4rem;
  border-left: 2px solid var(--error); padding-left: .6rem; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: .75rem; margin: .5rem 0;
}
.card h3 { margin: 0 0 .4rem; font-size: 1rem; }
.meta { color: var(--muted); font-size: .875rem; }
.meta dt { font-weight: 600; color: var(--text); }
dl.kv {
  display: grid; grid-template-columns: max-content 1fr;
  gap: .15rem .75rem; margin: .4rem 0;
}
dl.kv dt { color: var(--muted); }
dl.kv dd { margin: 0; overflow-wrap: anywhere; }
/* Shrink-to-fit, so a table takes the width its content needs rather than the width it is
   given. Without this, widening the page above would stretch every table to fill it —
   including the two-column state-history table on an item page, which would put six
   characters at the left edge of a 1920-pixel window and eleven at the right.

   overflow-x is the half that was already here and must stay. max-width caps the container
   at the space available; on a phone that is 343 pixels, which a nine-column table does not
   fit into. Scrolling the table inside its own box rather than scrolling the page is
   SC-013, and it is why this div exists at all. */
.scroll {
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  width: fit-content; max-width: 100%;
}
table { border-collapse: collapse; width: 100%; font-size: .9375rem; }
th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; white-space: nowrap; }
/* .94em, not .85em: this is the one size that *compounds*, because it sits inside .meta
   and inside table cells. At .85em a branch name landed at 11.6px, which is exactly the
   "text requiring zoom to read" SC-013 rules out. */
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .94em; }
.sim {
  display: inline-block; padding: .1rem .4rem; border-radius: 4px;
  background: var(--warn); color: #10131a; font-size: .8125rem; font-weight: 700;
}
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .6rem; }
.actions form { margin: 0; }
button, .action {
  display: inline-block; min-height: 44px; padding: .6rem 1rem; border-radius: 8px;
  border: 1px solid var(--line); background: var(--panel); color: var(--text);
  font: inherit; text-decoration: none; cursor: pointer;
}
button.danger, .action.danger { border-color: var(--error); color: var(--error); }
button.primary { border-color: var(--ok); color: var(--ok); }
.empty { color: var(--muted); font-style: italic; }
.filters { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .5rem 0; }
.filters input, .filters select {
  min-height: 44px; padding: .4rem .6rem; border-radius: 8px;
  border: 1px solid var(--line); background: var(--panel); color: var(--text); font: inherit;
}
.record { border-bottom: 1px solid var(--line); padding: .5rem 0; }
.record .ts { color: var(--muted); font-size: .8125rem; }
.record .detail { overflow-wrap: anywhere; font-size: .875rem; }
.outcome-error { color: var(--error); }
.outcome-pending { color: var(--warn); }
"""

APP_JS = """
// The refresh loop (R2). It re-fetches the current URL and swaps #content, rather than
// re-implementing rendering in the browser — one renderer, two representations.
//
// With scripting disabled nothing here runs and every page is still correct, merely
// static until reloaded. That is the whole reason the pages render server-side.
(function () {
  var main = document.getElementById('main');
  if (!main) { return; }
  var seconds = parseInt(main.getAttribute('data-refresh') || '0', 10);
  var loadedAt = Date.now();

  function showAge() {
    var el = document.getElementById('age');
    if (!el) { return; }
    var age = Math.round((Date.now() - loadedAt) / 1000);
    el.textContent = age < 2 ? 'just now' : age + 's ago';
  }
  setInterval(showAge, 1000);
  showAge();

  if (!seconds || seconds < 1) { return; }

  function refresh() {
    // Same URL, so filters and ?include_simulated survive the refresh.
    fetch(window.location.href, { headers: { 'Accept': 'text/html' }, cache: 'no-store' })
      .then(function (response) {
        if (!response.ok) { throw new Error('HTTP ' + response.status); }
        return response.text();
      })
      .then(function (text) {
        var parsed = new DOMParser().parseFromString(text, 'text/html');
        var fresh = parsed.getElementById('content');
        var current = document.getElementById('content');
        if (!fresh || !current) { return; }
        // Never swap while a form is focused: the author is mid-decision.
        var active = document.activeElement;
        if (active && current.contains(active) && active.tagName !== 'BODY') { return; }
        current.innerHTML = fresh.innerHTML;
        var freshChrome = parsed.querySelector('main > .chrome');
        var chrome = document.querySelector('main > .chrome');
        if (freshChrome && chrome) { chrome.innerHTML = freshChrome.innerHTML; }
        loadedAt = Date.now();
        showAge();
      })
      .catch(function () {
        // A failed refresh must not leave the page claiming to be current. Saying so is
        // the point: the daemon dying while a page is open is an expected case.
        var el = document.getElementById('age');
        if (el) { el.textContent = 'refresh failed — this page may be stale'; }
      });
  }
  setInterval(refresh, seconds * 1000);
})();
"""
