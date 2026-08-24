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
"""

from __future__ import annotations

import ipaddress
import json
import queue
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
from robot_army import db, operations
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

TRUTHY = frozenset({"1", "true", "yes", "on"})


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
    def include_simulated(self) -> bool:
        """FR-019: excluded by default; including them is an explicit act."""
        return (self.first("include_simulated") or "").lower() in TRUTHY

    @property
    def referer(self) -> str | None:
        return self.headers.get("referer")


@dataclass(slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

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
            result = operation(ctx, item_id)
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
            "include_simulated": request.include_simulated,
        },
    ) as outcome:
        data = body(outcome)
        outcome.update({k: v for k, v in data.items() if k not in outcome})
    target = location + html_query(request, msg=message)
    return Redirect(location=target, data={"ok": True, "message": message, **data})


def html_query(request: Request, **extra: Any) -> str:
    parts = [f"{k}={v}" for k, v in extra.items() if v not in (None, "")]
    if request.include_simulated:
        parts.append("include_simulated=1")
    return ("?" + "&".join(parts)) if parts else ""


def _referring_view(request: Request, fallback: str) -> str:
    """Where a successful action returns to.

    The ``Referer`` is used only for its **path**, and only when its host matches the one
    the request came to. Trusting it whole would be an open redirect, and this interface
    has exactly one thing worth protecting — the fact that reaching it is reaching
    everything.
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
    return parsed.path


# -- route handlers: views --------------------------------------------------


def view_root(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> Redirect:
    return Redirect(location="/active" + html_query(request), data={"ok": True})


def view_active(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.active_view(ctx, include_simulated=request.include_simulated)


def view_queue(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.queue_view(ctx, include_simulated=request.include_simulated)


def view_interrupted(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.interrupted_view(ctx, include_simulated=request.include_simulated)


def view_anomalies(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.anomalies_view(ctx, include_simulated=request.include_simulated)


def view_item(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.item_view(ctx, params["id"], include_simulated=request.include_simulated)


def view_confirm(app: WebApp, ctx: Context, request: Request, params: dict[str, Any]) -> View:
    return pages.confirm_view(
        ctx, params["id"], params["action"], include_simulated=request.include_simulated
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
        include_simulated=request.include_simulated,
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


def _seg(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.strip("/").split("/") if part)


GET = frozenset({"GET", "HEAD"})
POST = frozenset({"POST"})

ROUTES: tuple[Route, ...] = (
    Route(GET, _seg("/"), view_root),
    Route(GET, _seg("/active"), view_active, terminal="status"),
    Route(GET, _seg("/queue"), view_queue, terminal="status"),
    Route(GET, _seg("/interrupted"), view_interrupted, terminal="status"),
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
        refresh_seconds=int(chrome.get("refresh_seconds") or 10),
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
        chrome = pages.chrome(ctx, include_simulated=request.include_simulated)
        try:
            outcome = route.handler(app, ctx, request, params)
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
                self._respond(
                    Response(
                        status=413,
                        body=b'{"ok": false, "reason": "request body too large"}',
                        content_type="application/json; charset=utf-8",
                        headers=dict(NO_STORE),
                    )
                )
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


def build_server(app: WebApp, *, bind: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((bind, port), handler)


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
    effective_port = int(port or config.web.port)

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

    print(f"robot-army: web interface on http://{address}:{actual_port}", file=sys.stderr)
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
            f"robot-army: {signal.Signals(signum).name} — finishing in-flight requests",
            file=sys.stderr,
        )
        # shutdown() must not run on the thread inside serve_forever, or it deadlocks.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        # Joins in-flight request threads. The dispatch worker is a daemon thread and is
        # deliberately **not** waited for: an item left mid-dispatch is reconciliation's
        # problem, the same path any interrupted dispatch already takes.
        server.server_close()
        closing = app.context()
        try:
            closing.audit.record("web.stop", outcome="ok", detail={"reason": "signal"})
        finally:
            closing.close()
    return EXIT_OK
