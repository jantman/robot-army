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

NAV: tuple[tuple[str, str], ...] = (
    ("/active", "active"),
    ("/queue", "queue"),
    ("/interrupted", "interrupted"),
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
    "attached": ("ok", "A terminal window was opened onto that session."),
    "acknowledged": ("ok", "Anomaly acknowledged."),
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


def _chrome_bar(chrome: dict[str, Any]) -> Markup:
    """The facts FR-016 through FR-018 require on **every** view, not on a status page."""
    daemon = chrome.get("daemon") or {}
    running = bool(daemon.get("running"))
    suffix = _visibility_suffix(chrome)
    age = daemon.get("heartbeat_age_seconds")
    if running:
        state = f"daemon running (pid {daemon.get('pid') or '?'})"
        if not daemon.get("healthy"):
            state += " — heartbeat STALE"
    else:
        state = "DAEMON NOT RUNNING"
    if age is not None:
        state += f", heartbeat {int(age)}s old"
    activity = daemon.get("activity")
    if running and activity:
        state += f", {activity}"

    # The level pill carries the alarm below ``live`` and nothing at all at ``live`` (009
    # FR-016, FR-017). The polarity is deliberate and settled: ``live`` is the state the
    # operator expects and the one the system is meant to run in, so decorating it would
    # train them to ignore the one place the level is shown. Every level below it is a
    # testing configuration, and that is the surprising state.
    #
    # The word is in the text as well as in the colour, so a monochrome screenshot, a
    # colour-blind reader, and `curl | grep` all still carry the signal.
    level = str(chrome.get("effective_level") or chrome.get("effect_level") or "unknown")
    simulated = level != "live"
    pills: list[Any] = [
        span(
            f"effect level: {level}" + (" — simulated" if simulated else ""),
            class_="pill level " + ("simulated" if simulated else "live"),
        ),
        span(state, class_="pill " + ("ok" if running and daemon.get("healthy") else "warn")),
    ]
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
            label = (
                f"{total}/{cap} sessions "
                f"({capacity.get('ours', 0)} ours, {capacity.get('others', 0)} other)"
            )
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

    anomalies = int(chrome.get("anomaly_count") or 0)
    pills.append(
        a(
            "/anomalies" + suffix,
            f"{anomalies} anomal{'y' if anomalies == 1 else 'ies'}",
            class_="pill " + ("warn" if anomalies else "quiet"),
        )
    )
    if chrome.get("dispatch_paused"):
        since = chrome.get("dispatch_paused_at") or "unknown time"
        by = chrome.get("dispatch_paused_by") or "?"
        # A link, not a label: the pause is visible from every view, so the control that
        # lifts it has to be reachable from every view too.
        pills.append(
            a("/queue" + suffix, f"DISPATCH PAUSED since {since} (by {by})", class_="pill warn")
        )
    # A link in both states, and present in both (009 R9). The issue this milestone answers
    # did not report that the override was missing — it reported that "nothing on the page
    # suggests the parameter exists". A label that appears only once the parameter has been
    # found is no answer to that, and below `live`, where rows are now shown by default,
    # nothing would otherwise point at the hidden view at all.
    included = chrome.get("include_simulated")
    if included is not None:
        # Absent on the dead-end pages, which have no context to resolve a default from —
        # and a toggle that reports a state it had to guess is worse than no toggle.
        path = chrome.get("path") or "/active"
        pills.append(
            a(
                f"{path}?include_simulated={'0' if included else '1'}",
                "simulated rows included" if included else "simulated rows hidden",
                class_="pill quiet",
            )
        )

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
    # The third member of the family the two above belong to: conditions under which what
    # you are reading does not mean what it appears to mean. This one is the broadest, since
    # it changes the meaning of *every* value on the page rather than one of them — an item
    # shown as `linked` against issue #900001 is linked to nothing at all.
    #
    # Nothing is said when the level cannot be read. That state already has a banner, two
    # lines above, which says more than this one could; a page carrying one account of a
    # situation beats a page carrying two.
    consequences = chrome.get("simulated_consequences") or []
    if consequences:
        notices.append(
            div(
                f"This instance is set up for testing, not real work. At effect level "
                f"{level}, nothing on this page happened:",
                ul(consequences),
                div(
                    "The rows are real rows describing actions that were planned and not "
                    "performed. Nothing here reached GitHub, Trello, or a terminal.",
                    class_="reason",
                ),
                class_="banner error",
            )
        )
    return Markup(str(div(*pills, class_="chrome")) + "".join(str(n) for n in notices))


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
    rendered_at = chrome.get("rendered_at", "")
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
main { padding: 1rem; max-width: 60rem; margin: 0 auto; }
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
.pill.level.live { color: var(--muted); }
.pill.ok { border-color: var(--ok); color: var(--ok); }
.pill.quiet { color: var(--muted); }
.banner {
  padding: .75rem 1rem; border-radius: 8px; margin: .5rem 0;
  border: 1px solid var(--line); background: var(--panel);
}
.banner.ok { border-color: var(--ok); }
.banner.error { border-color: var(--error); color: var(--error); }
.banner .reason { color: var(--text); font-size: .9375rem; margin-top: .4rem; }
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
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
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
