"""The HTTP server: preconditions, the route table, request dispatch, and actions.

``http.server.ThreadingHTTPServer`` with a literal route table (R1). Threading because one
slow request — an interrupted view computing git status across several worktrees — must not
stall the page loading behind it, not because there is load to serve.

The shape that makes this testable (R15): :func:`handle` is a function from a parsed
:class:`Request` to a :class:`Response`, so routing, negotiation, rendering and every
refusal are unit-testable without binding a socket. Exactly one integration test binds a
real port, because the parts a pure-function test cannot reach — that the server binds,
that a browser-shaped request round-trips, that a ``303`` lands — are precisely the parts
that break silently.

Three properties here are requirements rather than implementation choices:

* **No filesystem path is ever derived from a request** (R12). ``SimpleHTTPRequestHandler``
  is not used and no directory is served; the two assets are module constants at fixed
  routes, so path traversal is structurally impossible rather than defended against.
* **Every mutating request is audited, intent first** (FR-038), and no error response is
  returned without a corresponding record (FR-039, FR-040). Both follow from every ``POST``
  passing through :func:`_perform`, which writes the intent before any check runs.
* **Every action calls an** ``operations.*`` **function** (FR-047). There is no action logic
  in this module; there are guards, and there is reporting of what the operation returned.
* **Every wait on a client is bounded, and so is the number of clients** (RA-13). Keep-alive
  without ``Handler.timeout`` means a connection that says nothing pins a thread forever, and
  an uncapped ``ThreadingHTTPServer`` means the number of those is whatever an attacker
  chooses — each one holding a socket and, once routed, a SQLite connection and an audit file
  handle. Descriptor exhaustion arrives long before memory does, and it stops the interface
  rendering at exactly the moment it is worth having. Removing either bound reopens that.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import queue
import re
import signal
import socket
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from robot_army import daemon as daemon_mod
from robot_army import db, dispatch, effects, health, operations
from robot_army import repos as repos_mod
from robot_army.config import Config
from robot_army.effects import EffectLevel
from robot_army.migrations import SCHEMA_VERSION
from robot_army.operations import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_USAGE,
    Context,
)
from robot_army.states import IllegalTransition
from robot_army.web import html, pages
from robot_army.web.pages import ITEM_ACTIONS, View

#: A request body larger than this is refused rather than read. The form bodies here are a
#: handful of bytes; anything bigger is a mistake or a probe, and reading an unbounded body
#: from a socket is the one denial-of-service this parser can trivially avoid.
MAX_BODY_BYTES = 64 * 1024

#: How long any single wait on a client may last (RA-13). ``MAX_BODY_BYTES`` bounds the
#: *size* of what a client may send; this bounds the *wait*, which is the other half and was
#: missing. Under HTTP/1.1 keep-alive a connection that says nothing would otherwise pin its
#: thread, its socket, and — once routed — a SQLite connection and an audit file handle,
#: forever. 15 sits above ``web.refresh_seconds`` (10 by default) so an idle-but-live browser
#: connection is left alone between refreshes, and far below anything a person would wait out.
REQUEST_TIMEOUT_SECONDS = 15

#: The most connections served at once (RA-13). The bound above is not sufficient on its own:
#: a client that sends one byte every fourteen seconds resets the wait and keeps its thread.
#: This is the ceiling that makes the resource cost a fact rather than a race — at saturation,
#: 32 sockets plus at most 32 SQLite connections plus at most 32 audit handles, under 100
#: descriptors against a typical ``RLIMIT_NOFILE`` of 1024. It is not 16, which the finding
#: suggested: a browser opens up to six connections per origin and this page refreshes itself
#: on a timer, so a handful of tabs can hold a dozen live connections between them, and a
#: refusal served to the operator's own page would be a worse bug than the one being fixed.
MAX_CONCURRENT_CONNECTIONS = 32

_OVER_CAPACITY_BODY = b'{"ok": false, "reason": "too many connections; try again"}'

#: The whole refusal, serialised once at import. Pre-built because it is written from the
#: accept loop's own thread while under attack: formatting a response per refused connection
#: is allocation on exactly the path that must stay cheap. ``Retry-After: 1`` is honest — a
#: slot frees the moment any in-flight connection ends. The body is JSON whatever the client
#: asked for, because at this point not one byte of the request has been read and ``Accept``
#: is not yet known.
OVER_CAPACITY_RESPONSE = b"\r\n".join(
    [
        b"HTTP/1.1 503 Service Unavailable",
        b"Content-Type: application/json; charset=utf-8",
        b"Content-Length: %d" % len(_OVER_CAPACITY_BODY),
        b"Connection: close",
        b"Cache-Control: no-store",
        b"Retry-After: 1",
        b"",
        _OVER_CAPACITY_BODY,
    ]
)

#: One read is enough to clear what a client sent before we refused it, and one read is all
#: the refusal path can afford. Sized like a request's headers, not like a body.
_DRAIN_BYTES = 64 * 1024

TRUTHY = frozenset({"1", "true", "yes", "on"})
#: Its twin, and the reason there is one: since 009 the visibility default varies by effect
#: level, so "the operator said no" and "the operator said nothing" are different facts and
#: an omitted parameter can no longer stand in for a false one.
FALSEY = frozenset({"0", "false", "no", "off"})


# -- request and response ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    form: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    wants_json: bool = False

    def first(self, name: str, default: str | None = None) -> str | None:
        values = self.form.get(name) or self.query.get(name)
        return values[0] if values else default

    @property
    def simulated_preference(self) -> bool | None:
        """What the operator *said* about simulated rows, or ``None`` if they said nothing.

        Three-valued rather than boolean (009 FR-002, FR-004). Until 009 the default was
        false at every effect level, so an absent parameter and an explicit ``0`` meant the
        same thing and one boolean sufficed. Now the default depends on the effect level —
        below ``live`` every row is a simulated row, so hiding them renders an empty page —
        and the two must be told apart: without that, an operator who deliberately hid the
        rows would have them reappear on their next click.

        An unrecognised value folds into "unstated" rather than into false, and never into a
        ``400``. This parameter is typed by hand from a phone, which is exactly the situation
        in which a typo should not produce an error page — and below ``live`` the forgiving
        direction is also the useful one: ``?include_simulated=treu`` shows the rows.
        """
        stated = (self.first("include_simulated") or "").lower()
        if stated in TRUTHY:
            return True
        if stated in FALSEY:
            return False
        return None

    @property
    def referer(self) -> str | None:
        return self.headers.get("referer")


#: On every response, without exception (RA-12). Attached in ``Response.__post_init__``
#: rather than at the call sites, because the call sites are not all in one place: two of
#: the five — the static assets and the ``413`` — never reach ``_render``, and a sixth added
#: later would not reach a list of them either. Being a response is the condition; carrying
#: these is the consequence. That also rules out folding them into :data:`NO_STORE`: it says
#: something different ("do not cache this"), the static assets deliberately do not carry it,
#: and a later response that wanted caching would have to choose between being cacheable and
#: being unframeable. The ``413`` does happen to spread ``NO_STORE`` today — but on purpose,
#: for its own reason, which is not a reason to make one constant mean two things.
SECURITY_HEADERS: dict[str, str] = {
    # The finding itself. A hostile page frames this interface at its shipped default
    # address, makes the frame transparent and baits a click over a real control; the form
    # that submits belongs to the framed document, so the browser reports
    # ``Sec-Fetch-Site: same-origin`` and a matching ``Origin`` — honestly.
    # ``check_same_origin`` passes, and it is right to. The question it answers is not the
    # one that distinguishes the two clicks, and no header on the request can be: the only
    # place to refuse is the frame.
    #
    # The three directives after it are free, and free only because of how austere these
    # pages are: ``html.page`` emits exactly two subresources, both served by this server
    # at fixed routes; there is no inline ``<script>``, no inline ``<style>``, no ``style=``
    # and no ``on*=`` anywhere in ``html.py``, so no ``'unsafe-inline'`` is needed; and
    # ``app.js`` fetches only ``window.location.href``, which ``connect-src`` inherits from
    # ``default-src``. The external URLs a page does emit — ``github.com`` and
    # ``trello.com``, the two systems this interface reads from — are anchors, and CSP does
    # not govern navigation by link. A page that grows a web font or a CDN script breaks
    # under this — deliberately, and a unit test says so before a browser does.
    #
    # ``default-src`` is also the second line under the escaping in ``html.py``, which is
    # currently the only thing stopping an injected ``<img onerror>`` from firing when the
    # refresh loop swaps ``innerHTML``.
    "Content-Security-Policy": (
        "frame-ancestors 'none'; default-src 'self'; base-uri 'none'; form-action 'self'"
    ),
    # The same instruction, for browsers older than ``frame-ancestors``. Where both are
    # understood the CSP wins; they say the same thing, so it does not matter which.
    "X-Frame-Options": "DENY",
    # Matters most on the ``.json`` responses and the two assets: a browser guessing a type
    # other than the one declared is the whole of that attack.
    "X-Content-Type-Options": "nosniff",
    # The audit and item views link out to ``github.com`` and ``trello.com``. Following one
    # must not tell the destination this interface's address, port, and the path being
    # looked at.
    #
    # ``same-origin``, not the stricter ``no-referrer`` the finding proposed, because
    # :func:`_referring_view` reads the ``Referer`` of our own POSTs and ``no-referrer``
    # suppresses it on those too, not only on the links out.
    #
    # The reachable difference is narrow, and worth stating exactly rather than
    # overstating: after a *successful* action there is none, because every control that
    # renders as a real form sits on the page its fallback already names — the holds and
    # the dispatch controls are on ``/queue``, ``attach`` is on the item — and every
    # confirm-gated verb POSTs from its confirmation page, whose referer this function
    # refuses on purpose. What does change is the chrome on a *refused* POST: its
    # visibility toggle is built from the referring view, so a control refused from
    # ``/queue`` offers a toggle back to ``/queue`` with the header and to ``/active``
    # without it. Small, but it lands on the error page, where being sent somewhere
    # unexpected is least welcome — and any confirm-free control added to a list view
    # later would widen it silently.
    #
    # ``same-origin`` withholds the referrer from exactly the destinations this header
    # exists to withhold it from, and from nothing else.
    "Referrer-Policy": "same-origin",
}


@dataclass(slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Security headers first, so a caller that sets one of these names deliberately wins
        # rather than being silently overwritten — nothing does today, and the ordering is
        # what says which way it would go.
        #
        # The comparison is case-insensitive because header names are, on the wire, while
        # this dict's keys are not: a caller passing ``x-frame-options`` would otherwise
        # collide with nothing, and ``_respond`` would write both, leaving a browser to
        # choose between two conflicting framing policies. Dropping ours when the caller
        # names it in any casing is what makes "one response, one value per header" true
        # rather than nearly true.
        stated = {name.lower() for name in self.headers}
        self.headers = {
            **{n: v for n, v in SECURITY_HEADERS.items() if n.lower() not in stated},
            **self.headers,
        }

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True, slots=True)
class Redirect:
    """A successful action. ``303`` so a reload re-issues a ``GET`` and never re-posts."""

    location: str
    data: dict[str, Any] = field(default_factory=dict)


class Refusal(Exception):
    """An action that will not happen, and why.

    ``code`` is the exit code the equivalent terminal command would have returned, from
    001's table. The HTTP status is necessarily coarse; ``code`` is the precise answer, and
    carrying it is what makes a refusal read identically whichever front end produced it.
    """

    def __init__(
        self, reason: str, *, status: int, code: int, extra: dict[str, Any] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.code = code
        self.extra = extra or {}


def _status_for(code: int) -> int:
    """Exit code to HTTP status. Deliberately coarse — ``code`` carries the precision."""
    if code == EXIT_USAGE:
        return 400
    return 409


# -- bind address (FR-004, R13) ---------------------------------------------


def validate_bind(address: str) -> tuple[str | None, str | None]:
    """Is this address permitted to listen on? Returns ``(problem, warning)``.

    Under FR-003 the bind address *is* the security policy, so this is the one fact about
    the design that must never be silent. A globally routable address is refused outright;
    anything that is not loopback warns, because anything able to reach the port has full
    control.

    ``0.0.0.0`` cannot be classified — it means every interface, including any the machine
    gains later — so it warns rather than refusing. Refusing it would push toward pinning
    an address a DHCP lease can change, trading a real ergonomic problem for a theoretical
    safety one.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return (
            f"[web] bind {address!r} is not an IP address. Use an address rather than a "
            "hostname, so what the interface becomes reachable from is unambiguous",
            None,
        )
    if parsed.is_global:
        return (
            f"[web] bind {address!r} is a globally routable address. This interface has no "
            "authentication by design (spec FR-003), so binding it where the internet can "
            "reach it would publish full control of robot-army. Refusing to start",
            None,
        )
    if parsed.is_loopback:
        return None, None
    if parsed.is_unspecified:
        return None, (
            f"binding {address} means every network interface on this machine, including "
            "any it gains later. Anything that can reach this port has FULL CONTROL of "
            "robot-army — there is no authentication, by design (spec FR-003)"
        )
    return None, (
        f"binding {address} is not loopback. Anything that can reach this port has FULL "
        "CONTROL of robot-army — there is no authentication, by design (spec FR-003)"
    )


def check_preconditions(
    config: Config, *, bind: str, port: int
) -> tuple[list[str], list[str]]:
    """Everything that must hold before the socket accepts anything (R11, FR-010).

    Returns ``(problems, warnings)``. **Every** problem is reported, not the first: fixing
    one per restart is a poor experience, which is the same reason config validation
    aggregates.
    """
    problems: list[str] = []
    warnings: list[str] = list(config.warnings)

    # 1. The database, opened but never migrated. The daemon owns the schema; two processes
    #    racing to run the same migration is a failure mode worth removing, not surviving.
    try:
        conn = db.connect(config.layout.db_path)
    except Exception as exc:  # noqa: BLE001 - any failure to open is one problem to report
        problems.append(f"database at {config.layout.db_path} is not usable: {exc}")
    else:
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != SCHEMA_VERSION:
                problems.append(
                    f"database schema is at version {version}, expected {SCHEMA_VERSION}. "
                    "The interface never migrates — start `robot-army run` to bring the "
                    "schema up to date, then start the interface again"
                )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"database at {config.layout.db_path} is not usable: {exc}")
        finally:
            conn.close()

    # 2. The bind address.
    problem, warning = validate_bind(bind)
    if problem:
        problems.append(problem)
    if warning:
        warnings.append(warning)

    # 3. The socket. Probed rather than held: the real bind happens moments later and is
    #    itself reported if it fails, so the only cost of the race is a duplicated message.
    if not problem:
        probe = socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((bind, port))
        except OSError as exc:
            problems.append(f"cannot bind {bind}:{port} — {exc}")
        finally:
            probe.close()

    return problems, warnings


# -- the application --------------------------------------------------------


class WebApp:
    """Everything a request needs that outlives the request.

    Deliberately small: the interface holds **no authoritative state** (FR-045), so this is
    the configuration, the effect level, and the one worker thread. Killing the process
    loses nothing.
    """

    def __init__(self, config: Config, *, effect_level: EffectLevel | None = None) -> None:
        self.config = config
        self.effect_level = effect_level or config.daemon.effect_level
        self._work: queue.Queue[tuple[str, int]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def context(self) -> Context:
        """One ``Context`` per request, closed when the request ends (R11).

        Per-request because ``sqlite3`` connections are not shareable across threads and
        ``ThreadingHTTPServer`` gives each request its own. They are cheap: a connect plus
        three pragmas. ``migrate=False`` because the web never migrates.
        """
        return operations.build_context(
            self.config,
            effect_level=self.effect_level,
            component="web",
            migrate=False,
        )

    # -- the worker thread (R3, T035) ------------------------------------

    def submit(self, action: str, item_id: int) -> None:
        """Hand a slow action to the single worker and return.

        ``dispatch.dispatch_item`` prepares a worktree and runs preparation hooks, bounded
        by the 15-minute ``dispatching`` max age. No phone holds an HTTP request that long,
        and a dropped connection must not be ambiguous with a failed action — so the
        response is immediate and the item's own state tells the story.

        A thread lost to a killed process is **reconciliation's** problem, not the server's:
        the daemon already owns "an item in dispatching with nothing behind it", and this is
        not a new way to produce that condition. That is why the worker needs no
        supervision, no restart, and no record of its own.
        """
        self._ensure_worker()
        self._work.put((action, item_id))

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_loop, name="robot-army-web-worker", daemon=True
            )
            self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            action, item_id = self._work.get()
            try:
                self._run_slow_action(action, item_id)
            except Exception:  # noqa: BLE001 - one bad action must not kill the worker
                traceback.print_exc(file=sys.stderr)
            finally:
                self._work.task_done()

    def _run_slow_action(self, action: str, item_id: int) -> None:
        """Run the operation and record what it actually returned (R7).

        The pre-check inside ``operations.resume`` is advisory; the transition inside
        ``dispatch_item`` is authoritative. Reporting the returned ``Result`` rather than
        assuming the pre-check held is the whole discipline here — and since the HTTP
        response has already gone, this record *is* the report.
        """
        ctx = self.context()
        try:
            operation = getattr(operations, action)
            # ``surface="web"`` because this is the *authoritative* gate check for a web
            # button press, and its ``dispatch.refused`` record would otherwise inherit the
            # operation's ``"cli"`` default — one press producing a refusal attributed to
            # "web" by the pre-check and a second attributed to "cli" by the worker, for
            # the same action. Principle III's standard is reconstruction, and a record
            # naming the wrong surface defeats it.
            #
            # Passed unconditionally because ``submit`` is reachable only from
            # ``_slow_item_action``, which is bound to ``resume`` and ``restart`` alone and
            # both accept it. A third slow action lacking the parameter would fail loudly
            # here on its first use rather than log the wrong surface quietly, which is the
            # right way round.
            result = operation(ctx, item_id, surface="web")
            ctx.audit.record(
                f"web.{action}.result",
                outcome="ok" if result.code == EXIT_OK else "error",
                entity_type="work_item",
                entity_id=item_id,
                detail={"exit_code": result.code, "message": " ".join(result.lines)},
            )
        except Exception as exc:
            ctx.audit.error(
                f"web.{action}.result",
                error=exc,
                entity_type="work_item",
                entity_id=item_id,
            )
            raise
        finally:
            ctx.close()


# -- guards -----------------------------------------------------------------


def require_daemon(ctx: Context, action: str) -> None:
    """FR-005: an action that needs the daemon fails with that reason, not a hang.

    Resume and restart launch a session whose exit only the daemon will notice: with no
    daemon running, the spool never drains and reconciliation never runs, so the session
    would be stranded in ``dispatching`` with nothing to resolve it.
    """
    if not daemon_mod.is_locked(ctx.layout.lock_path):
        raise Refusal(
            f"the daemon is not running, and {action} needs it: nothing would drain the "
            "exit spool or reconcile the session afterwards. Start `robot-army run` first",
            status=503,
            code=EXIT_PRECONDITION,
        )


def require_effect_agreement(ctx: Context, action: str) -> None:
    """R4: refuse to act when the daemon's live effect level disagrees with ours."""
    mismatch = pages.effect_mismatch(ctx)
    if mismatch:
        raise Refusal(mismatch, status=409, code=EXIT_PRECONDITION)


def require_dispatchable(ctx: Context, item_id: int, action: str) -> None:
    """Refuse a launch the cap, the pause or a hold would refuse (issue #120, FR-015).

    The *second* check of the same gate, and the reason for the duplication is this
    interface's shape rather than caution. ``_slow_item_action`` answers ``303``
    immediately and does the slow work on a worker, because preparing a worktree takes
    minutes and no phone holds a request that long — so a refusal discovered on the worker
    reaches the author only through the log, while the page shows an item that simply did
    not change. That is indistinguishable from nothing having happened, which is precisely
    the failure this feature exists to remove.

    So the gate runs here, where its refusal becomes a response the author reads on the
    page they are looking at, and again inside ``dispatch_item`` on the worker, where it is
    authoritative because minutes can have passed. The same discipline ``_run_slow_action``
    already states: the pre-check is advisory, the check at the launch decides.

    Never passes ``force``. The web has no override, and does not need one — *Unpause*,
    *Release hold* and the repository's own release are each one press away, and lifting
    the condition leaves the queue agreeing with the button instead of overridden by it.
    """
    # ``require_item`` rather than an early return. ``require_legal`` above already
    # refuses a missing item with a 404, so today this cannot be reached — but a silent
    # return here means the bypass becomes real the moment someone reorders the guards,
    # and "the item does not exist" is not a dispatchable state under any ordering.
    item = require_item(ctx, item_id)
    try:
        dispatch.check_launch_gate(
            ctx.conn,
            audit=ctx.audit,
            config=ctx.config,
            item=item,
            surface="web",
        )
    except dispatch.DispatchRefused as exc:
        raise Refusal(
            f"cannot {action} item {item_id}: {exc.detail}",
            status=409,
            code=EXIT_PRECONDITION,
            extra={"item_id": item_id, "hold": str(exc.hold) if exc.hold else None},
        ) from exc


def require_item(ctx: Context, item_id: int) -> Any:
    row = db.get_work_item(ctx.conn, item_id)
    if row is None:
        raise Refusal(
            f"no work item with id {item_id}", status=404, code=EXIT_FAILED,
            extra={"item_id": item_id},
        )
    return row


def require_legal(ctx: Context, item_id: int, action: str) -> Any:
    """FR-027: evaluate against state read **now**, not the state the page was rendered from.

    This is the same table that decided whether to offer the control (FR-029), asked again
    at submission. The offer and the refusal therefore cannot disagree — and the state
    machine under ``BEGIN IMMEDIATE`` remains the authority for anything that races us.
    """
    row = require_item(ctx, item_id)
    item = operations._item_dict(row)
    session = pages._session_for(ctx, item_id)
    has_previous = bool(db.list_sessions_for_item(ctx.conn, item_id))
    legal = pages.legal_actions(item, session, has_previous_session=has_previous)
    if action not in legal:
        raise Refusal(
            f"work item {item_id} is {item['state']}; {action} is not legal from that "
            f"state. Currently legal: {', '.join(legal) or 'nothing'}",
            status=409,
            code=EXIT_PRECONDITION,
            extra={"item_id": item_id, "state": item["state"], "legal_actions": legal},
        )
    return row


def _report(result: operations.Result, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn an operation's ``Result`` into a payload, refusing when it refused (R7).

    Never assumes the operation's own pre-check succeeded — reporting what it *returned* is
    the rule that keeps the web from claiming success the terminal would not have claimed.
    """
    if result.code != EXIT_OK:
        raise Refusal(
            "\n".join(result.lines) or "the operation failed",
            status=_status_for(result.code),
            code=result.code,
            extra={**(extra or {}), **result.data},
        )
    return {**(extra or {}), **result.data}


# -- which level is in force (009 FR-018) -----------------------------------

#: ``None`` means "a daemon is running whose level cannot be read", which is a real answer
#: rather than an absent one — so "not supplied" needs a sentinel of its own.
_UNSET = object()


#: ``EffectLevel`` declares its members least-to-most consequential, so the declaration order
#: *is* the ordering :func:`effective_level` needs and no second table has to be kept in step
#: with it. ``test_web_effect_guard`` pins the order so a future reordering of the enum cannot
#: silently invert the comparison.
_LEVEL_ORDER: tuple[EffectLevel, ...] = tuple(EffectLevel)


def effective_level(
    ctx: Context, report: Any = None, *, running: bool | None = None
) -> EffectLevel | None:
    """The one level that drives the non-live banner, the level pill, and the row default.

    Two levels can be in force at once and they can disagree: this interface's own
    (``ctx.effect_level``) and the running daemon's. :func:`effect_mismatch` exists because
    of that, and refuses mutations while they differ. But for the *display* question the two
    answer different halves — the rows on the page were written by the daemon at the daemon's
    level, while an action taken next would run at this interface's — so neither alone is the
    honest answer.

    **The more simulated of the two wins** (009 FR-018). That is the only rule under which the
    page cannot claim to be real about either half: an interface configured for ``live`` in
    front of a ``plan`` daemon would otherwise render a calm pill above a table of invented
    issue numbers, which is precisely the reading milestone 009 exists to remove.

    Returns ``None`` — meaning *unknown*, treated as most simulated — when a daemon holds the
    lock but no heartbeat can be read. The existing ``EFFECT LEVEL UNKNOWN`` banner already
    explains that state, so the caller renders no second banner for it and only the pill
    changes; one account of a situation beats two competing ones.
    """
    if report is None:
        report = health.check(
            ctx.layout.heartbeat_path, max_age_seconds=ctx.config.health.max_age_seconds
        )
    if running is None:
        running = daemon_mod.is_locked(ctx.layout.lock_path)
    ours = ctx.effect_level
    if not running:
        # Nothing to disagree with. Refusing to trust the configured level on the strength of
        # a heartbeat left by a dead process would be the same surprise in the other
        # direction — the reasoning ``effect_mismatch`` already records.
        return ours
    if not report.heartbeat:
        return None
    raw = report.heartbeat.get("effect_level")
    try:
        theirs = EffectLevel(str(raw))
    except ValueError:
        # A heartbeat naming a level this build does not have is not a level we can reason
        # about, and failing closed here matches what ``effect_mismatch`` does with one it
        # cannot read at all.
        return None
    return min(ours, theirs, key=_LEVEL_ORDER.index)



# -- what this request shows (009 FR-001, FR-002) ---------------------------


def include_simulated_for(
    request: Request, ctx: Context, *, level: EffectLevel | Any | None = _UNSET
) -> bool:
    """Does this request show simulated rows?

    The one place the two facts meet. ``Request`` is parsed from the wire before any database
    handle exists, so it cannot know the effect level; ``pages.*`` takes a boolean and should
    keep taking one, so the views stay pure functions of their arguments. The edge is where
    both are in hand.

    A stated preference wins outright. With nothing stated, the effective level decides: below
    ``live`` — or when it cannot be read — the simulated rows *are* the contents, so hiding
    them renders an empty page describing a system that has found no work, which is also
    exactly what a broken daemon looks like. At ``live`` they are leftovers from earlier
    testing and stay hidden, unchanged from 001's FR-019.

    The terminal keeps excluding them at every level. A flag is typed deliberately and stays
    visible in the scrollback; a phone has no other way to ask.

    ``level`` is accepted so the read path can resolve it once and hand it to both this and
    the chrome; ``None`` is a meaningful value there (a daemon whose level cannot be read),
    which is why the sentinel for "not supplied" is not ``None``.
    """
    stated = request.simulated_preference
    if stated is not None:
        return stated
    level = effective_level(ctx) if level is _UNSET else level
    return level is not EffectLevel.LIVE


# -- the action wrapper (FR-038, FR-039, FR-040) ----------------------------


def _perform(
    ctx: Context,
    request: Request,
    *,
    action: str,
    entity_type: str | None,
    entity_id: Any,
    location: str,
    message: str,
    body: Callable[[dict[str, Any]], dict[str, Any]],
) -> Redirect:
    """Audit, guard, act, redirect. **Every** ``POST`` goes through here.

    The intent record is written and flushed *before* anything else runs, including the
    checks — so a refusal, a crash, and a success all leave a record, and an error response
    with no corresponding record is impossible by construction rather than by discipline
    (FR-039, FR-040). A ``Refusal`` raised inside becomes the pair's ``error`` outcome and
    is re-raised for the caller to render.
    """
    with ctx.audit.action(
        f"web.{action}",
        entity_type=entity_type,
        entity_id=entity_id,
        detail={
            "route": request.path,
            "form": {k: v for k, v in request.form.items() if k != "include_simulated"},
            # Both halves. ``include_simulated`` is what the request was *served* with;
            # ``simulated_preference`` is what the operator *asked for*, or ``None`` if they
            # asked for nothing. Recording only the first would leave a record that cannot be
            # read back without knowing which effect level was in force at the time.
            "include_simulated": include_simulated_for(request, ctx),
            "simulated_preference": request.simulated_preference,
            "origin": request.headers.get("origin"),
            "sec_fetch_site": request.headers.get("sec-fetch-site"),
        },
    ) as outcome:
        # Inside the audit pair, so a forged request that is refused still leaves the
        # record that says one arrived — which is the only way it would ever be noticed.
        check_same_origin(request)
        data = body(outcome)
        outcome.update({k: v for k, v in data.items() if k not in outcome})
    target = location + html_query(request, ctx, msg=message)
    return Redirect(location=target, data={"ok": True, "message": message, **data})


def html_query(request: Request, ctx: Context, **extra: Any) -> str:
    """The query string a redirect carries forward.

    Always states ``include_simulated``, in both directions (009 FR-003). Omitting it when
    false was correct while false was also the default — omission and ``0`` meant the same
    thing. Now that the default varies by level, omission means "use the default" and can no
    longer stand in for ``0``: an operator who deliberately hid the rows would get them back
    on the ``303`` after their next action.

    Derived here rather than handed in because the action path reaches this from inside six
    handler bodies, and re-reading two small files to build one redirect target is cheaper
    than a keyword threaded through all of them.
    """
    parts = [f"{k}={v}" for k, v in extra.items() if v not in (None, "")]
    parts.append(f"include_simulated={'1' if include_simulated_for(request, ctx) else '0'}")
    return ("?" + "&".join(parts)) if parts else ""


def check_host(request: Request) -> None:
    """Refuse a request whose ``Host`` is a **name** rather than an address.

    This closes DNS rebinding, which otherwise walks straight through
    :func:`check_same_origin`. That check compares ``Origin`` against ``Host`` — and under
    rebinding an attacker controls both. They point ``evil.test`` at ``127.0.0.1``, the
    author's browser loads ``http://evil.test:8420``, and the browser then sends
    ``Host: evil.test:8420``, ``Origin: http://evil.test:8420`` and
    ``Sec-Fetch-Site: same-origin`` — all self-consistent, all satisfying a check that only
    compares the two to each other, while the request really reaches this server. That
    restores the whole attack, and adds reading every response to it.

    **Rebinding needs a name**, because the trick is a name whose resolution changes. So the
    rule is the form of the ``Host``, not an allowlist of addresses: an IP literal, or
    ``localhost``. That needs nothing plumbed through from the bind configuration, and it
    already matches how this interface is reached — ``[web] bind`` must itself be an address
    rather than a hostname, for the same reason: what the interface became reachable from
    must be unambiguous.

    Applied to **every** request, not only the mutating ones. Rebinding lets the attacker's
    page read what it fetches, and the audit log and issue titles are not theirs to read.

    An absent ``Host`` is allowed: HTTP/1.1 requires it and every browser sends it, so its
    absence means a hand-written client that could reach the port directly anyway.
    """
    host = request.headers.get("host")
    if not host:
        return
    hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0][1:]
    if hostname == "localhost":
        return
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        raise Refusal(
            f"refusing a request addressed to {hostname!r}. This interface answers on an "
            "address, not a name: a name that resolves here is how a page on another site "
            "makes your browser treat itself as same-origin (DNS rebinding). Reach it by "
            "its IP address, or by localhost",
            status=403,
            code=EXIT_PRECONDITION,
        ) from None


def check_same_origin(request: Request) -> None:
    """Refuse a state-changing request that a **browser** says came from another site.

    This closes a path the spec's exposure model does not actually cover. FR-003 reasons
    about *network* reachability — "anything that can reach the port has full control" — and
    accepts that. A cross-site request forgery needs no network path to a loopback-bound
    port at all: it needs only the author's own browser, already inside the trust boundary,
    to have some unrelated page open while the interface happens to be running. A zero-field
    form POST to ``/item/1/restart`` from any such page would otherwise just work.

    It is **not** authentication, which Principle II forbids building. It identifies nobody
    and authorises nobody; it asks one question — did a browser originate this from a page
    this server served? — and it holds no state, which is what R7 rules out.

    **Headers that are absent are allowed through, deliberately.** ``curl`` sends neither,
    and the quickstart drives every control with it; refusing those would break the
    documented terminal path to protect against a client that has no need of forgery — it
    can reach the port directly, which is the accepted model. Browsers always send
    ``Sec-Fetch-Site``, and always send ``Origin`` on a cross-origin POST, so the check
    covers exactly the gap and nothing else.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise Refusal(
            f"refusing a state-changing request the browser reports as {site!r}. This "
            "interface has no authentication by design, so a request forged by another "
            "page you have open is the one attack its exposure model does not already "
            "accept",
            status=403,
            code=EXIT_PRECONDITION,
        )
    origin = request.headers.get("origin")
    if origin:
        host = request.headers.get("host", "")
        if urlparse(origin).netloc != host:
            raise Refusal(
                f"refusing a state-changing request from origin {origin!r}, which is not "
                f"{host!r}",
                status=403,
                code=EXIT_PRECONDITION,
            )


def _referring_view(request: Request, fallback: str) -> str:
    """Where a successful action returns to.

    The ``Referer`` is used only for its **path**, and only when that path matches a route
    this server actually serves as a view. Trusting it whole would be an open redirect, and
    this interface has exactly one thing worth protecting — the fact that reaching it is
    reaching everything.

    **A confirmation page is never a destination.** Its whole purpose is to precede the
    action, and a browser sends it as the ``Referer`` of the very POST it submits — so
    returning it would send the author back to a page that re-validates an action they just
    completed, finds it no longer legal, and renders a ``409``. The action succeeded and the
    page said it failed, which is the worst thing this interface could do.
    """
    referer = request.referer
    if not referer:
        return fallback
    parsed = urlparse(referer)
    host = request.headers.get("host", "")
    if parsed.netloc and host and parsed.netloc != host:
        return fallback
    if not parsed.path.startswith("/"):
        return fallback
    # Match against the route table rather than pattern-matching the string: a referer
    # naming an asset, an unknown path, or a confirm page is not somewhere to land.
    route, _params, _allowed = match("GET", parsed.path.removesuffix(".json"))
    if route is None or route.handler in (view_confirm, view_root):
        return fallback
    return parsed.path


# -- route handlers: views --------------------------------------------------


def view_root(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
    return Redirect(location="/active" + html_query(request, ctx), data={"ok": True})


def view_active(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.active_view(ctx, include_simulated=params["include_simulated"])


def view_queue(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    # The chrome is handed in so the dispatch controls render from the same snapshot the
    # rest of the page does, rather than re-reading the pause a second time.
    return pages.queue_view(
        ctx,
        include_simulated=params["include_simulated"],
        chrome_payload=params.get("chrome"),
    )


def view_interrupted(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.interrupted_view(ctx, include_simulated=params["include_simulated"])


def view_cards(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.cards_view(ctx, include_simulated=params["include_simulated"])


def view_card_confirm(
    app: WebApp, ctx: Context, request: Request, params: dict[str, Any]
) -> View:
    return pages.card_confirm_view(
        ctx, params["card_id"], include_simulated=params["include_simulated"]
    )


def view_anomalies(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.anomalies_view(ctx, include_simulated=params["include_simulated"])


def view_item(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.item_view(ctx, params["id"], include_simulated=params["include_simulated"])


def view_confirm(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.confirm_view(
        ctx, params["id"], params["action"], include_simulated=params["include_simulated"]
    )


def view_log(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    raw_item = request.first("item")
    item_id: int | None = None
    if raw_item:
        try:
            item_id = int(raw_item)
        except ValueError:
            return pages.refusal_view(
                reason=f"item filter {raw_item!r} is not a work item id",
                status=400,
                code=EXIT_USAGE,
            )
    return pages.log_view(
        ctx,
        item_id=item_id,
        since=request.first("since") or None,
        outcome=request.first("outcome") or None,
        cursor=request.first("cursor") or None,
        include_simulated=params["include_simulated"],
    )


# -- route handlers: actions ------------------------------------------------


def _slow_item_action(action: str) -> Callable[..., Redirect]:
    """``resume`` and ``restart``: guarded here, run on the worker, ``303`` immediately."""

    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        item_id = params["id"]

        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            require_daemon(ctx, action)
            require_effect_agreement(ctx, action)
            require_legal(ctx, item_id, action)
            # Last of the four, because it is the only one that observes the machine — a
            # directory listing and a handful of /proc reads have no business running for a
            # request the three cheap guards above will refuse anyway.
            require_dispatchable(ctx, item_id, action)
            app.submit(action, item_id)
            outcome["handed_to_worker"] = True
            outcome["note"] = (
                "preparation can take minutes; the item's own state is the report, and "
                f"web.{action}.result carries what the operation returned"
            )
            return {"item_id": item_id, "accepted": True}

        return _perform(
            ctx,
            request,
            action=action,
            entity_type="work_item",
            entity_id=item_id,
            location=_referring_view(request, f"/item/{item_id}"),
            message="resumed" if action == "resume" else "restarted",
            body=body,
        )

    return handler


def _inline_item_action(
    action: str,
    *,
    message: str,
    run: Callable[[Context, int], operations.Result],
    needs_daemon: bool = False,
    effect_guarded: bool = True,
) -> Callable[..., Redirect]:
    """Everything else: a single transaction or a single call, run in the request thread."""

    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        item_id = params["id"]

        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            if needs_daemon:
                require_daemon(ctx, action)
            if effect_guarded:
                require_effect_agreement(ctx, action)
            require_legal(ctx, item_id, action)
            try:
                result = run(ctx, item_id)
            except IllegalTransition as exc:
                # The state machine is the arbiter, not this module (R7). A concurrent
                # terminal command or a second tap lands here, and the reason it gives is
                # the reason the author needs.
                raise Refusal(
                    str(exc), status=409, code=EXIT_PRECONDITION, extra={"item_id": item_id}
                ) from exc
            return _report(result, extra={"item_id": item_id})

        return _perform(
            ctx,
            request,
            action=action,
            entity_type="work_item",
            entity_id=item_id,
            location=_referring_view(request, f"/item/{item_id}"),
            message=message,
            body=body,
        )

    return handler


def action_acknowledge(
    app: WebApp, ctx: Context, request: Request, params: dict[str, Any]
) -> Redirect:
    anomaly_id = params["id"]

    def body(outcome: dict[str, Any]) -> dict[str, Any]:
        # Deliberately not effect-guarded: acknowledging touches no work and reaches
        # nothing outside this process. R4's guard exists to stop the interface acting on
        # work for a daemon running at another level; bookkeeping is not that.
        return _report(
            operations.anomalies(ctx, acknowledge=anomaly_id), extra={"anomaly_id": anomaly_id}
        )

    return _perform(
        ctx,
        request,
        action="anomaly.acknowledge",
        entity_type="anomaly",
        entity_id=anomaly_id,
        location=_referring_view(request, "/anomalies"),
        message="acknowledged",
        body=body,
    )


def _pause_action(paused: bool) -> Callable[..., Redirect]:
    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            # Deliberately **not** effect-guarded. Pausing is the mitigation for exactly
            # the condition R4's guard detects; refusing it during a mismatch would leave
            # the interface with no safe action at the moment one is most wanted. It
            # launches nothing and writes nothing outward.
            operation = operations.pause_dispatch if paused else operations.unpause_dispatch
            return _report(operation(ctx, by="web"))

        return _perform(
            ctx,
            request,
            action="dispatch.pause" if paused else "dispatch.unpause",
            entity_type="dispatch_control",
            entity_id=1,
            location=_referring_view(request, "/queue"),
            message="paused" if paused else "unpaused",
            body=body,
        )

    return handler


def _item_hold_action(holding: bool) -> Callable[..., Redirect]:
    """``POST /item/<id>/hold`` and its release (issue #117).

    Deliberately **not** effect-guarded, matching ``_pause_action``. The tempting
    asymmetry — guard the release because it can lead to a session starting — guards the
    wrong side of the causal chain: unholding starts nothing, it removes one row, after
    which the *dispatcher* decides whether to dispatch and applies the effect level itself
    at the moment it acts. Guarding here would attach a decision to the surface that
    displays a fact rather than to the code that acts on it.

    Not ``require_daemon`` either: a hold is meaningful against a stopped daemon, because
    it takes effect when it starts (FR-022). ``resume`` and ``restart`` need the daemon
    because something must drain the spool afterwards; nothing here launches anything.

    What checks there are run **inside** ``_perform``'s body, matching every other handler,
    so a refusal is recorded rather than silently returned.
    """

    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        item_id = params["id"]

        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            # Inside the body, not before it, because `_perform` writes and flushes the
            # intent record before it calls this — so a refusal for an unknown item leaves
            # a record saying one arrived, and passes through the same-origin check on the
            # way. Checking first would have made this the one POST whose refusals were
            # invisible, which is exactly what `_perform` exists to prevent. Every other
            # handler puts its checks here for the same reason.
            require_item(ctx, item_id)
            operation = operations.hold_item if holding else operations.unhold_item
            return _report(operation(ctx, item_id, by="web"))

        return _perform(
            ctx,
            request,
            action="hold.item" if holding else "unhold.item",
            entity_type="work_item",
            entity_id=item_id,
            location=_referring_view(request, "/queue"),
            message="held" if holding else "released",
            body=body,
        )

    return handler


def _repo_hold_action(holding: bool) -> Callable[..., Redirect]:
    """``POST /repos/hold`` and its release, taking the key from the **form body**.

    Never from the path. A repository key contains a slash, so a path parameter would mean
    two segments or an encoded one, and ``_bind`` matches on segment count — while the
    standing position beside ``_CARD_ID`` is that a route parameter reaching a page is one
    an attacker would like to control. A two-segment repository parameter would create
    exactly the shapes (``..``, encoded separators) that the strict card pattern forecloses.

    This is not a workaround: ``_job_action`` already reads ``request.first("repo")`` for
    ``POST /poll``. The value is validated against the onboarding record before it reaches
    anything, so an unknown key is a refusal rather than a stored row.
    """

    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        repo_key = request.first("repo") or ""

        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            # Validated inside the body for the reason above: a forged or mistyped `repo`
            # that is refused must still leave the record that says a request arrived.
            if repo_key not in repos_mod.known(ctx.conn):
                raise Refusal(
                    f"repository {repo_key!r} is not onboarded",
                    status=404,
                    code=EXIT_FAILED,
                    extra={"repo": repo_key},
                )
            operation = operations.hold_repo if holding else operations.unhold_repo
            return _report(operation(ctx, repo_key, by="web"), extra={"repo": repo_key})

        return _perform(
            ctx,
            request,
            action="hold.repo" if holding else "unhold.repo",
            entity_type="repo",
            entity_id=repo_key,
            location=_referring_view(request, "/queue"),
            message="held" if holding else "released",
            body=body,
        )

    return handler


def _job_action(name: str) -> Callable[..., Redirect]:
    def handler(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
        repo = request.first("repo") or None

        def body(outcome: dict[str, Any]) -> dict[str, Any]:
            require_effect_agreement(ctx, name)
            operation = operations.poll_now if name == "poll" else operations.reconcile_now
            result = operation(ctx, repo=repo) if name == "poll" else operation(ctx)
            return _report(result, extra={"repo": repo})

        return _perform(
            ctx,
            request,
            action=name,
            entity_type=None,
            entity_id=None,
            location=_referring_view(request, "/queue"),
            message="polled" if name == "poll" else "reconciled",
            body=body,
        )

    return handler


def action_rescan(
    app: WebApp, ctx: Context, request: Request, params: dict[str, Any]
) -> Redirect:
    """Force a re-evaluation of held cards (FR-024).

    Goes through ``operations.rescan`` rather than reimplementing the marker write, which
    is FR-047's rule and the reason the button and the verb cannot answer differently —
    including in their refusals, which this route does not re-derive.
    """
    card_id = params["card_id"]

    def body(outcome: dict[str, Any]) -> dict[str, Any]:
        require_effect_agreement(ctx, "rescan")
        return _report(operations.rescan(ctx, card_id), extra={"card_id": card_id})

    return _perform(
        ctx,
        request,
        action="rescan",
        entity_type="card",
        entity_id=card_id,
        location=_referring_view(request, "/cards"),
        message="rescanned",
        body=body,
    )


# -- the route table --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Route:
    methods: frozenset[str]
    segments: tuple[str, ...]
    handler: Callable[..., Any]
    #: The terminal command that does the same thing. FR-006 and SC-011 require every web
    #: control to have one, and naming it here is what lets a test verify it by enumeration
    #: rather than by reading both files and hoping.
    terminal: str | None = None


#: What a Trello card id looks like. Deliberately strict: a route parameter that reaches a
#: page is one an attacker would like to control, and the board only ever issues these.
_CARD_ID = re.compile(r"[A-Za-z0-9]{8,32}")


def _seg(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.strip("/").split("/") if part)


GET = frozenset({"GET", "HEAD"})
POST = frozenset({"POST"})

ROUTES: tuple[Route, ...] = (
    Route(GET, _seg("/"), view_root),
    Route(GET, _seg("/active"), view_active, terminal="status"),
    Route(GET, _seg("/queue"), view_queue, terminal="status"),
    Route(GET, _seg("/interrupted"), view_interrupted, terminal="status"),
    Route(GET, _seg("/cards"), view_cards, terminal="cards"),
    Route(GET, ("card", "<card_id>", "confirm", "rescan"), view_card_confirm, terminal="cards"),
    Route(POST, ("card", "<card_id>", "rescan"), action_rescan, terminal="rescan"),
    Route(GET, _seg("/anomalies"), view_anomalies, terminal="anomalies"),
    Route(GET, _seg("/log"), view_log, terminal="log"),
    Route(GET, ("item", "<id>"), view_item, terminal="show"),
    Route(GET, ("item", "<id>", "confirm", "<action>"), view_confirm, terminal="show"),
    Route(
        POST, ("item", "<id>", "resume"), _slow_item_action("resume"), terminal="resume"
    ),
    Route(
        POST, ("item", "<id>", "restart"), _slow_item_action("restart"), terminal="restart"
    ),
    # Every ``run`` resolves ``operations.*`` at call time rather than binding the function
    # object when this table is built. That is not indirection for its own sake: the table
    # is module-level, so an early binding would make the operation unpatchable and, more
    # importantly, would let this module hold a stale reference to a function it is
    # supposed to be a thin caller of.
    Route(
        POST,
        ("item", "<id>", "abandon"),
        _inline_item_action(
            "abandon",
            message="abandoned",
            run=lambda ctx, item_id: operations.abandon(ctx, item_id),
        ),
        terminal="abandon",
    ),
    Route(
        POST,
        ("item", "<id>", "cancel"),
        _inline_item_action(
            "cancel",
            message="cancelled",
            # force=True because the HTTP confirmation already happened; the terminal
            # prompt would have nothing to read from.
            run=lambda ctx, item_id: operations.cancel(ctx, item_id, force=True),
        ),
        terminal="cancel",
    ),
    Route(
        POST,
        ("item", "<id>", "retry"),
        _inline_item_action(
            "retry", message="retried", run=lambda ctx, item_id: operations.retry(ctx, item_id)
        ),
        terminal="retry",
    ),
    Route(
        POST,
        ("item", "<id>", "attach"),
        _inline_item_action(
            "attach", message="attached", run=lambda ctx, item_id: operations.attach(ctx, item_id)
        ),
        terminal="attach",
    ),
    Route(POST, ("anomalies", "<id>", "acknowledge"), action_acknowledge, terminal="anomalies"),
    Route(
        POST, ("item", "<id>", "hold"), _item_hold_action(True), terminal="hold"
    ),
    Route(
        POST, ("item", "<id>", "unhold"), _item_hold_action(False), terminal="unhold"
    ),
    Route(POST, _seg("/repos/hold"), _repo_hold_action(True), terminal="hold"),
    Route(POST, _seg("/repos/unhold"), _repo_hold_action(False), terminal="unhold"),
    Route(POST, _seg("/dispatch/pause"), _pause_action(True), terminal="pause"),
    Route(POST, _seg("/dispatch/unpause"), _pause_action(False), terminal="unpause"),
    Route(POST, _seg("/poll"), _job_action("poll"), terminal="poll"),
    Route(POST, _seg("/reconcile"), _job_action("reconcile"), terminal="reconcile"),
)

STATIC: dict[str, tuple[str, str]] = {
    "/static/app.css": ("text/css; charset=utf-8", html.APP_CSS),
    "/static/app.js": ("text/javascript; charset=utf-8", html.APP_JS),
}


def match(method: str, path: str) -> tuple[Route | None, dict[str, Any], list[str]]:
    """Find the route. Returns ``(route, params, methods_allowed_on_this_path)``.

    The third value is what makes a ``405`` able to name what the path *does* accept, which
    is the difference between a useful refusal and a bare status line.
    """
    segments = _seg(path)
    allowed: list[str] = []
    for route in ROUTES:
        params = _bind(route.segments, segments)
        if params is None:
            continue
        allowed.extend(sorted(route.methods - {"HEAD"}))
        if method in route.methods:
            return route, params, allowed
    return None, {}, sorted(set(allowed))


def _bind(pattern: tuple[str, ...], segments: tuple[str, ...]) -> dict[str, Any] | None:
    if len(pattern) != len(segments):
        return None
    params: dict[str, Any] = {}
    for expected, actual in zip(pattern, segments, strict=True):
        if expected == "<id>":
            try:
                params["id"] = int(actual)
            except ValueError:
                return None
        elif expected == "<card_id>":
            # A card id is an opaque board identifier, not a number of ours. Constrained to
            # what the board actually issues — 24 hex characters — so a path segment can
            # never carry a slash, a traversal, or markup into a page.
            if not _CARD_ID.fullmatch(actual):
                return None
            params["card_id"] = actual
        elif expected == "<action>":
            if actual not in ITEM_ACTIONS:
                return None
            params["action"] = actual
        elif expected != actual:
            return None
    return params


# -- request handling -------------------------------------------------------


#: Everything but the two assets. A cached page claiming to describe what is running now
#: is the failure this interface exists to avoid, so the header is set where the response
#: is built rather than at the wire — which also puts it inside R15's testable surface.
NO_STORE = {"Cache-Control": "no-store"}


def _render(view: View, chrome: dict[str, Any], request: Request) -> Response:
    if request.wants_json:
        payload = {**view.data, **chrome}
        return Response(
            status=view.status,
            body=json.dumps(payload, indent=2, default=str).encode("utf-8"),
            content_type="application/json; charset=utf-8",
            headers=dict(NO_STORE),
        )
    document = html.page(
        title=view.title,
        chrome=chrome,
        body=view.body,
        path=request.path,
        message=request.first("msg"),
        # `.get(key, default)`, not `or`: zero is a *meaningful* value here. `_bare` sets it
        # to 0 for dead-end pages — 404, 405, a schema mismatch — precisely so they do not
        # poll. Treating it as falsy made a page reporting a broken database re-fetch the
        # broken endpoint every ten seconds, forever.
        refresh_seconds=int(chrome.get("refresh_seconds", 10)),
    )
    return Response(status=view.status, body=document.encode("utf-8"), headers=dict(NO_STORE))


def _render_redirect(redirect: Redirect, request: Request) -> Response:
    """``303 See Other``, so a reload re-issues a ``GET`` and never re-posts (R7).

    The body carries the JSON payload when JSON was asked for, so a script gets the answer
    without following the redirect, and a browser gets a link it will never see.
    """
    if request.wants_json:
        body = json.dumps(redirect.data, indent=2, default=str).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = str(
            html.p(html.a(redirect.location, "continue"))
        ).encode("utf-8")
        content_type = "text/html; charset=utf-8"
    return Response(
        status=303,
        body=body,
        content_type=content_type,
        headers={"Location": redirect.location, **NO_STORE},
    )


def handle(app: WebApp, request: Request) -> Response:
    """One request in, one response out. No socket involved (R15)."""
    try:
        # Before routing, before the database, before anything reads the request: a
        # rebound Host means this request is not addressed to us in the sense the browser
        # believes, and its response is not the attacker's to read.
        check_host(request)
    except Refusal as refusal:
        return _bare(
            pages.refusal_view(
                reason=refusal.reason, status=refusal.status, code=refusal.code
            ),
            request,
        )
    if request.path in STATIC and request.method in GET:
        content_type, text = STATIC[request.path]
        return Response(
            body=text.encode("utf-8"),
            content_type=content_type,
            # An hour: long enough that a page refreshing every 10 seconds fetches the
            # asset once, short enough that an upgrade is picked up without a hard reload.
            headers={"Cache-Control": "public, max-age=3600"},
        )
    if request.path in STATIC:
        return _bare(
            pages.method_not_allowed_view(request.path, ["GET"]), request, {"Allow": "GET"}
        )

    route, params, allowed = match(request.method, request.path)
    if route is None:
        if allowed:
            return _bare(
                pages.method_not_allowed_view(request.path, allowed),
                request,
                {"Allow": ", ".join([*allowed, "HEAD"] if "GET" in allowed else allowed)},
            )
        return _bare(pages.not_found_view(f"There is no {request.path} here."), request)

    try:
        ctx = app.context()
    except operations.SchemaMismatch as exc:
        # The daemon migrated underneath us. A clear refusal beats a subtly wrong page.
        return _bare(
            pages.refusal_view(reason=str(exc), status=503, code=EXIT_PRECONDITION), request
        )

    try:
        # Resolved once, here, and handed to both the chrome and the handler. Deriving it
        # twice would read the heartbeat and the lock twice and could — across a daemon
        # starting mid-request — answer differently in the two halves of one page.
        level = effective_level(ctx)
        include_simulated = include_simulated_for(request, ctx, level=level)
        chrome = pages.chrome(
            ctx,
            include_simulated=include_simulated,
            simulated_preference=request.simulated_preference,
            # A GET-able path, because the chrome's visibility toggle links to it — and a
            # refused POST renders this same chrome. Pointing the toggle at
            # ``/item/5/abandon`` would offer the reader a link that answers 405, which is
            # reachable any time an action is illegal rather than only in some edge case.
            path=request.path if request.method in GET else _referring_view(request, "/active"),
            effective_level=str(level) if level else "unknown",
            simulated_consequences=effects.consequences(level) if level else [],
            all_effects_simulated=(
                level is not None
                and len(effects.consequences(level)) == len(effects.SIMULATED_CONSEQUENCES)
            ),
        )
        try:
            outcome = route.handler(
                app,
                ctx,
                request,
                {**params, "chrome": chrome, "include_simulated": include_simulated},
            )
        except Refusal as refusal:
            view = pages.refusal_view(
                reason=refusal.reason,
                status=refusal.status,
                code=refusal.code,
                extra=refusal.extra,
            )
            return _render(view, chrome, request)
        if isinstance(outcome, Redirect):
            return _render_redirect(outcome, request)
        return _render(outcome, chrome, request)
    finally:
        ctx.close()


def _bare(view: View, request: Request, headers: dict[str, str] | None = None) -> Response:
    """Render a view with no database behind it — 404, 405, and schema refusals.

    These must work when the database does not, which is exactly why they do not take a
    ``Context``: a 503 that cannot render is not a 503.
    """
    chrome = {
        "effect_level": "unknown",
        "daemon": {"running": False},
        "anomaly_count": 0,
        "rendered_at": pages.datetime.now(pages.UTC).strftime(pages.STAMP),
        "refresh_seconds": 0,
    }
    response = _render(view, chrome, request)
    response.headers.update(headers or {})
    return response


def parse_request(
    method: str, raw_path: str, headers: dict[str, str], body: bytes = b""
) -> Request:
    """Turn the wire into a :class:`Request`. The only place external input is parsed."""
    parsed = urlparse(raw_path)
    path = parsed.path or "/"
    wants_json = False
    if path.endswith(".json"):
        path = path[: -len(".json")] or "/"
        wants_json = True
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    accept = headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        wants_json = True
    form: dict[str, list[str]] = {}
    if body:
        form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return Request(
        method=method,
        path=path,
        query=parse_qs(parsed.query, keep_blank_values=True),
        form=form,
        headers=headers,
        wants_json=wants_json,
    )


class Handler(BaseHTTPRequestHandler):
    """The socket half. Everything decidable is decided in :func:`handle`."""

    server_version = "robot-army"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    #: The bound, and the reason keep-alive makes it mandatory rather than optional (RA-13).
    #: ``socketserver.StreamRequestHandler.setup`` turns this one attribute into
    #: ``settimeout()`` on the connection, which bounds *every* wait on a client: the request
    #: line, the headers, ``rfile.read(length)`` for a body, the gap before the next request
    #: on a kept-alive connection, and writes to a client that stops reading. Left at its
    #: inherited ``None`` — which is what ``http.server`` ships — each of those blocks
    #: forever, and a connection that says nothing pins a thread for the life of the process.
    #: ``handle_one_request`` already catches the resulting ``TimeoutError`` and closes the
    #: connection, so nothing else here has to know about it.
    timeout = REQUEST_TIMEOUT_SECONDS

    app: WebApp

    def log_message(self, fmt: str, *args: Any) -> None:
        # GET requests are the one enumerated Principle III exception (FR-041) and are not
        # audited; nor are they written to stderr, where an auto-refreshing page would
        # produce a line every 10 seconds and bury anything worth reading.
        return

    def _respond(self, response: Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _dispatch(self, method: str) -> None:
        headers = {key.lower(): value for key, value in self.headers.items()}
        body = b""
        if method == "POST":
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                # The body is never read, so its bytes are still queued on the socket.
                # Under HTTP/1.1 keep-alive the next read would parse them as the start of
                # the following request line, corrupting whatever the client sent next.
                # Closing is the honest end: the alternative is draining an arbitrary
                # number of bytes we already decided not to accept.
                self._respond(
                    Response(
                        status=413,
                        body=b'{"ok": false, "reason": "request body too large"}',
                        content_type="application/json; charset=utf-8",
                        headers={**NO_STORE, "Connection": "close"},
                    )
                )
                self.close_connection = True
                return
            body = self.rfile.read(length) if length > 0 else b""
        request = parse_request(method, self.path, headers, body)
        try:
            response = handle(self.app, request)
        except Exception as exc:  # noqa: BLE001 - never a bare traceback to the browser
            traceback.print_exc(file=sys.stderr)
            response = _bare(
                pages.refusal_view(
                    reason=f"the interface failed to handle this request: {exc}",
                    status=500,
                    code=EXIT_FAILED,
                ),
                request,
            )
        self._respond(response)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with a ceiling on how many connections it will serve (RA-13).

    The count is per **connection**, not per request, and that is the whole design decision.
    ``ThreadingMixIn.process_request`` starts one thread per accepted connection and
    ``BaseHTTPRequestHandler.handle`` loops on that one thread for the life of a keep-alive
    connection — so the thread, the socket, and everything the requests on it open are held
    for as long as the *connection* lives. Counting requests would leave an idle keep-alive
    connection uncounted while it still holds a thread, which is precisely the case that
    made the finding exploitable.

    Admission is decided in :meth:`process_request`, before ``super()`` starts a thread, so a
    refused connection never reaches a handler and therefore never calls ``WebApp.context()``
    — no SQLite connection, no ``AuditLog`` file handle, no worker.

    Refusals are counted, not audited. Writing an audit record per refusal would open the
    exact pair of descriptors this class exists to bound, making the log the amplifier of the
    flood it documents; the run's total rides out in the ``web.stop`` record instead. That is
    the enumerated Principle III exception in this feature's plan.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Guards the three fields below, and nothing else. Never held across a socket
        #: operation and never held across starting a thread.
        self._capacity_lock = threading.Lock()
        self._in_flight = 0
        #: ``True`` from the first refusal until the count next falls below the cap, so the
        #: terminal line is one per saturation episode rather than one per refused
        #: connection.
        self._saturated = False
        #: Connections turned away for capacity during this run. Read once, at shutdown.
        self.refused_over_capacity = 0

    def process_request(self, request: Any, client_address: Any) -> None:
        """Admit the connection, or refuse it — decided before any thread exists.

        The cap is read from the module rather than captured at construction so it is one
        constant with one meaning; the alternative is a copy that can disagree with it.
        """
        with self._capacity_lock:
            admitted = self._in_flight < MAX_CONCURRENT_CONNECTIONS
            if admitted:
                self._in_flight += 1
                announce = False
            else:
                self.refused_over_capacity += 1
                announce = not self._saturated
                self._saturated = True

        if not admitted:
            # Outside the lock: the refusal touches a socket, and no socket operation is
            # worth blocking every other connection's admission decision on.
            if announce:
                print(
                    f"robot-army: at capacity ({MAX_CONCURRENT_CONNECTIONS} connections); "
                    "refusing new connections",
                    file=sys.stderr,
                )
            self._refuse(request)
            return

        try:
            super().process_request(request, client_address)
        except BaseException:
            # The thread never started, so nothing will ever run the release in
            # ``process_request_thread``. Give the slot back here or lose it for good.
            self._release_slot()
            raise

    def _refuse(self, request: Any) -> None:
        """Answer ``503`` and close, without blocking and without opening anything.

        This runs on the accept loop's own thread, so a blocking write to a client that
        never reads would stall admission for every other client — the refusal would become
        the denial of service. Non-blocking makes it O(1): the response is 200-odd bytes and
        fits in any send buffer, and if it would block we drop it and close, which is the
        outcome that actually matters. Delivery is best-effort by design.

        The drain is not politeness. Closing a socket that still has unread bytes queued
        makes the kernel send an RST, and an RST can make the peer discard the response it
        has already buffered — so reading what the client sent is what keeps the close an
        ordinary FIN and the ``503`` readable.
        """
        try:
            # One suppress over all three, not one each: if ``setblocking`` fails the socket
            # is still blocking and has no timeout of its own — ``setup()`` never ran for it
            # — so a ``recv`` after it could block the accept loop for good. A failure at any
            # step must skip the rest, and only the close below is unconditional.
            with contextlib.suppress(OSError):
                request.setblocking(False)
                request.send(OVER_CAPACITY_RESPONSE)
                request.recv(_DRAIN_BYTES)
        finally:
            # Whatever the socket did or refused to do, the descriptor goes back. That is
            # the part of this path that is not best-effort.
            self.shutdown_request(request)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        """Serve the connection, and give the slot back however it ends.

        ``finally`` rather than a trailing statement because a slot lost to a failure is
        permanent: the server would starve one connection at a time, and the symptom —
        refusals rising over days — would look nothing like its cause.
        """
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_slot()

    def _release_slot(self) -> None:
        with self._capacity_lock:
            self._in_flight -= 1
            # Hysteresis, and the reason it is not simply ``< cap``: under a sustained flood
            # the count sits *at* the cap and oscillates by one as slots recycle, so a flag
            # cleared by any release would re-arm on every recycled connection and print a
            # line for each. An episode ends when the pressure is actually gone, which is
            # what half the cap stands for.
            if self._in_flight * 2 <= MAX_CONCURRENT_CONNECTIONS:
                self._saturated = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A connection ending is not an error; anything else still gets its traceback.

        ``ThreadingMixIn`` routes everything escaping a handler here, and the inherited
        implementation prints a full traceback. A client that times out, resets, or hangs up
        mid-response is not this program failing — and under the flood the cap exists to
        survive, one traceback per dropped connection is itself an amplifier, since stderr is
        usually the journal. The suppression is exactly three exception types, all of which
        mean "the connection ended". Principle III's ban on silent failure is about *our*
        failures, so every other exception prints exactly as it does today.
        """
        if isinstance(sys.exception(), TimeoutError | ConnectionResetError | BrokenPipeError):
            return
        super().handle_error(request, client_address)


def build_server(app: WebApp, *, bind: str, port: int) -> BoundedThreadingHTTPServer:
    """Bind the server, in the address family the address actually belongs to.

    ``TCPServer`` defaults to ``AF_INET``, so without this an IPv6 literal passed every
    precondition — ``validate_bind`` calls ``::1`` loopback and the probe socket already
    branches on the family — and then failed at the real bind with a ``gaierror``. Being
    told an address is fine and then refused it is worse than being refused it up front.
    """
    handler = type("BoundHandler", (Handler,), {"app": app})
    server_class: type[BoundedThreadingHTTPServer] = BoundedThreadingHTTPServer
    if ":" in bind:
        server_class = type(
            "BoundedThreadingHTTPServerV6",
            (BoundedThreadingHTTPServer,),
            {"address_family": socket.AF_INET6},
        )
    return server_class((bind, port), handler)


def _display_address(address: str, port: int) -> str:
    """``http://[::1]:8420`` — an IPv6 literal in a URL needs its brackets to be clickable."""
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}:{port}"


def serve(
    config: Config,
    *,
    bind: str | None = None,
    port: int | None = None,
    effect_level: EffectLevel | None = None,
) -> int:
    """Run the interface in the foreground. Exit ``3`` on any unmet precondition.

    Independent of the daemon: it starts, stops, and survives separately, which is what
    FR-005 requires — the audit log and the interrupted list stay readable during exactly
    the incident that makes them worth reading.
    """
    effective_bind = bind or config.web.bind
    # ``is not None`` rather than ``or``: port 0 is a real request — let the kernel choose —
    # and ``or`` silently answered it with the configured port instead.
    effective_port = int(port if port is not None else config.web.port)

    problems, warnings = check_preconditions(
        config, bind=effective_bind, port=effective_port
    )
    for warning in warnings:
        print(f"robot-army: warning: {warning}", file=sys.stderr)
    if problems:
        print("robot-army serve: startup preconditions not met:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_PRECONDITION

    app = WebApp(config, effect_level=effect_level)
    try:
        server = build_server(app, bind=effective_bind, port=effective_port)
    except OSError as exc:
        print(f"robot-army serve: cannot bind {effective_bind}:{effective_port} — {exc}",
              file=sys.stderr)
        return EXIT_PRECONDITION

    address, actual_port = server.server_address[0], server.server_address[1]
    ctx = app.context()
    try:
        # SC-015: the address and port actually listened on, in the log and on stderr, on
        # every start — including the ones that came from configuration rather than the
        # default. Under FR-003 this is the security policy, so it is never silent.
        ctx.audit.record(
            "web.start",
            outcome="ok",
            target=f"{address}:{actual_port}",
            detail={
                "bind": str(address),
                "port": actual_port,
                "effect_level": str(app.effect_level),
                "loopback": ipaddress.ip_address(str(address)).is_loopback,
                "refresh_seconds": config.web.refresh_seconds,
                "config": str(config.path),
            },
        )
    finally:
        ctx.close()

    print(
        f"robot-army: web interface on {_display_address(str(address), actual_port)}",
        file=sys.stderr,
    )
    print(f"robot-army: effect level {app.effect_level}", file=sys.stderr)
    if not ipaddress.ip_address(str(address)).is_loopback:
        print(
            "robot-army: WARNING: this is not loopback. Anything that can reach this port "
            "has full control of robot-army. There is no authentication, by design "
            "(spec FR-003).",
            file=sys.stderr,
        )

    stopping = threading.Event()

    def _stop(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        print(
            f"robot-army: {signal.Signals(signum).name} — closing the listening socket",
            file=sys.stderr,
        )
        # shutdown() must not run on the thread inside serve_forever, or it deadlocks.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        # Stops accepting and closes the listening socket. It does **not** join anything,
        # which this comment used to claim: ``_Threads.append`` drops daemon threads and
        # ``ThreadingHTTPServer`` sets ``daemon_threads = True``, so nothing is ever tracked
        # and the join is over an empty list. In-flight requests die with the process. That
        # is the intended trade — but before ``Handler.timeout`` existed there was no bound
        # on how long one could take, and now there is one.
        #
        # The dispatch worker is a daemon thread and is deliberately not waited for either:
        # an item left mid-dispatch is reconciliation's problem, the same path any
        # interrupted dispatch already takes.
        server.server_close()
        closing = app.context()
        try:
            # ``refused_over_capacity`` is the whole durable trace of the connection cap
            # (FR-010). Individual refusals are deliberately not recorded — a record per
            # refusal would open the SQLite connection and audit handle the cap exists to
            # bound, making the log amplify the flood it documents — so this integer is what
            # answers "did this run turn connections away, and how many" from the log alone.
            closing.audit.record(
                "web.stop",
                outcome="ok",
                detail={
                    "reason": "signal",
                    "refused_over_capacity": server.refused_over_capacity,
                },
            )
        finally:
            closing.close()
    return EXIT_OK
