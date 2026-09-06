"""Every CLI verb as a plain callable.

This module exists so that no operation's logic lives inside argument parsing. Milestone
002's HTTP API is then a *second caller of the same functions* rather than a
reimplementation — the single cheapest thing this milestone can do for the next one, at
essentially zero cost now (plan.md, Project Structure).

Each operation returns a ``Result``: an exit code, a human-readable rendering, and a
machine-readable payload. The CLI chooses which to print; it makes no decisions of its own.
"""

from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from robot_army import audit as audit_mod
from robot_army import capacity as capacity_mod
from robot_army import (
    channels,
    control,
    db,
    dispatch,
    health,
    intake,
    poll,
    procinfo,
    prompt,
    reconcile,
    sessions,
    speckit,
    spool,
    timefmt,
    worktree,
)
from robot_army import cleanup as cleanup_mod
from robot_army import (
    daemon as daemon_mod,
)
from robot_army import ordering as ordering_mod
from robot_army import (
    repos as repos_mod,
)
from robot_army.audit import AuditLog
from robot_army.boundaries import BoundaryError, HostHandle, TransportError
from robot_army.cardstates import NEVER_PARKED, CardState
from robot_army.config import Config
from robot_army.effects import Boundaries, EffectLevel, wire
from robot_army.migrations import SCHEMA_VERSION
from robot_army.models import ANOMALY_KINDS, WorkItem
from robot_army.states import (
    TERMINAL_SESSION_STATES,
    SessionState,
    WorkItemState,
    dumps_labels,
    transition_session,
    transition_work_item,
)

# Exit codes, per contracts/cli.md.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PRECONDITION = 3
EXIT_CHECK_FAILED = 4


@dataclass(slots=True)
class Result:
    code: int = EXIT_OK
    lines: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def say(self, text: str = "") -> Result:
        self.lines.append(text)
        return self

    def flush_to(self, stream: TextIO | None) -> Result:
        """Write everything said so far to ``stream``, then **forget it**.

        The forgetting is the point, not an optimisation. An operation that shows the
        maintainer something and then asks them about it has to write before it blocks
        (011 FR-001) — but every path out of that question returns ``lines`` to the CLI,
        which prints them again. Clearing here is what makes "printed exactly once"
        (FR-006) a property of the code rather than a rule five exit paths have to
        remember. There is exactly one caller for that reason; a second flush point would
        quietly reintroduce the doubling.

        ``flush=True`` rather than trusting the buffer: the difference is invisible on a
        terminal and is the whole behaviour when output is redirected to a file or a pipe,
        which is where the maintainer is watching the run from another shell (FR-005).

        A ``None`` stream writes nothing and keeps the lines, which is both the pre-011
        behaviour every direct caller still gets and what ``--json`` passes, since a
        machine-readable document must carry no human-readable text (FR-012).
        """
        if stream is None:
            return self
        if self.lines:
            print("\n".join(self.lines), file=stream, flush=True)
        self.lines.clear()
        return self

    def render(self, *, as_json: bool) -> str:
        if as_json:
            return json.dumps(self.data, indent=2, default=str)
        return "\n".join(self.lines)


@dataclass(slots=True)
class Context:
    """Everything an operation needs, assembled once by the CLI."""

    config: Config
    conn: sqlite3.Connection
    audit: AuditLog
    boundaries: Boundaries
    effect_level: EffectLevel

    @property
    def layout(self) -> Any:
        return self.config.layout

    def close(self) -> None:
        self.conn.close()
        self.audit.close()


class SchemaMismatch(Exception):
    """The database is not at the version this code expects, and we must not migrate.

    Raised only by contexts built with ``migrate=False`` — the web process (R11). Two
    processes racing to run the same migration is a failure mode worth removing rather
    than surviving: the daemon owns the schema and the interface follows it.
    """

    def __init__(self, found: int, expected: int, path: Path) -> None:
        super().__init__(
            f"database at {path} is schema version {found}, but this code expects "
            f"{expected}. The daemon owns the schema — start or restart "
            "`robot-army run` to migrate, then start the interface again"
        )
        self.found = found
        self.expected = expected


def build_context(
    config: Config,
    *,
    effect_level: EffectLevel | None = None,
    component: str = "cli",
    migrate: bool = True,
) -> Context:
    """Assemble everything an operation needs.

    ``migrate=False`` is the web process's mode (R11): it opens the database read-write,
    verifies ``PRAGMA user_version``, and refuses rather than upgrading. Everything else
    migrates on open, as milestone 001 established.
    """
    layout = config.layout
    layout.ensure()
    level = effect_level or config.daemon.effect_level
    audit = AuditLog(layout.log_dir, component=component)
    if migrate:
        conn, _ = db.open_database(layout.db_path)
    else:
        conn = db.connect(layout.db_path)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error:
            conn.close()
            raise
        if version != SCHEMA_VERSION:
            conn.close()
            audit.close()
            raise SchemaMismatch(version, SCHEMA_VERSION, layout.db_path)
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=wire(level, config, audit, conn),
        effect_level=level,
    )


def _mark(item: Any) -> str:
    """Simulated rows are always visibly marked when shown (FR-057)."""
    return " [simulated]" if getattr(item, "dry_run", False) else ""


def _withheld_note(count: int) -> str:
    """What a listing says about the simulated rows it matched and did not show.

    One definition rather than four copies of the same user-facing sentence: research.md
    R3 rejected a helper parameterised by noun, flag and placement, and this is not that —
    it is the message itself, and four hand-written copies of it are how the count and the
    flag name drift apart.
    """
    return f"{count} simulated rows withheld — pass --include-simulated to show them"


def _table(rows: list[list[str]], headers: list[str]) -> Iterator[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    yield "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    yield "  ".join("-" * widths[i] for i in range(len(headers)))
    for row in rows:
        yield "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()


# -- status -----------------------------------------------------------------


def _say_queue(result: Result, queue: list[Any]) -> None:
    """The queue table: a position and one reason per row.

    One reason, not several: two shown at once is how a surface stops being read (R9).
    Extracted from ``status`` in milestone 008 — adding the withheld disclosure and the
    simulated marking pushed that function past the complexity ceiling, and the queue is
    the section with the least to say to the rest of it.
    """
    if not queue:
        return
    result.say(f"queue ({len(queue)} eligible) — in dispatch order:")
    rows = [
        [
            str(entry.position),
            # FR-057 applies here too, and did not used to: the queue includes simulated
            # rows by design, and showed them indistinguishable from real ones. A reader
            # who stops at the first table — the most likely reader — had nothing on
            # screen telling them what these were.
            str(entry.item.id) + ("*" if entry.item.dry_run else ""),
            entry.item.repo_key,
            f"#{entry.item.issue_number}",
            str(entry.hold) if entry.hold else "ready to dispatch",
            entry.detail[:64],
        ]
        for entry in queue
    ]
    for line in _table(rows, ["#", "item", "repo", "issue", "hold", "why"]):
        result.say(line)
    if any(entry.item.dry_run for entry in queue):
        result.say()
        result.say("* = simulated (dry-run) row")
    result.say()


def _say_holds_summary(result: Result, ctx: Context) -> None:
    """One line naming how much is held, and **only when something is** (issue #117).

    Conditional on purpose. A hold set and forgotten is the failure US3 is about, so
    ``status`` — the command the author actually types when wondering why nothing is
    running — has to mention holds. But a permanent "no holds in force" line would be
    noise on the overwhelmingly common runs where nothing is held, and noise is how a
    surface stops being read: it would cost exactly the discoverability it is here to buy.
    Saying something only when there is something to say gives both.

    The full listing is ``robot-army holds``, which can show what this line and the queue
    table structurally cannot — a repository hold matching no queued item, and a hold on an
    item that is no longer eligible.
    """
    items = len(db.list_item_holds(ctx.conn))
    repositories = len(db.list_repo_holds(ctx.conn))
    if not items and not repositories:
        return
    parts = []
    if items:
        parts.append(f"{items} item{'' if items == 1 else 's'}")
    if repositories:
        parts.append(f"{repositories} repositor{'y' if repositories == 1 else 'ies'}")
    result.say(f"holds        : {' and '.join(parts)} held — `robot-army holds` lists them")


def status(
    ctx: Context,
    *,
    state: str | None = None,
    repo: str | None = None,
    include_simulated: bool = False,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    capacity: Any = None,
) -> Result:
    """The default view: effect level, health, counts, the queue, listings, anomalies.

    Milestone 004 added the queue section and the capacity line. Both render
    ``ordering.plan`` and ``capacity.snapshot`` directly rather than deriving anything of
    their own, so what this prints as "next" is what the next dispatch will select (R8).
    """
    result = Result()
    report = health.check(
        ctx.layout.heartbeat_path, max_age_seconds=ctx.config.health.max_age_seconds
    )
    counts = db.count_work_items_by_state(ctx.conn, include_simulated=include_simulated)
    states = [WorkItemState(state)] if state else None
    items = db.list_work_items(
        ctx.conn, include_simulated=include_simulated, states=states, repo_key=repo
    )
    anomalies = db.list_anomalies(ctx.conn, include_simulated=include_simulated)
    control_state = db.get_dispatch_control(ctx.conn)
    # Handed in by a caller that has already taken one, or taken here for the terminal path
    # that has not (RA-14). The web renders several of these per page — ``/interrupted``
    # calls this twice, once per section — and each snapshot reads the session registry and
    # enumerates ``/proc``, so a page was paying for the same observation repeatedly.
    snap = capacity or capacity_mod.snapshot(
        ctx.conn, config=ctx.config, registry_dir=registry_dir, proc_root=proc_root
    )
    queue = ordering_mod.plan(ctx.conn, config=ctx.config, capacity=snap)

    # How many rows this invocation matched and did not show. Milestone 008: the queue
    # above includes simulated rows because they occupy capacity, while the counts and the
    # listing exclude them because FR-056 makes that the default. Both are right; printing
    # them side by side with nothing said about the gap is what produced a command that
    # denied the rows it had just listed.
    #
    # Two numbers rather than one, because the two sections ask different questions:
    # ``count_work_items_by_state`` has never honoured --state or --repo, and the listing
    # always has. One figure would be wrong in whichever section it did not belong to as
    # soon as a filter was in play.
    withheld_counts = 0 if include_simulated else db.count_simulated_work_items(ctx.conn)
    withheld_items = (
        0
        if include_simulated
        else db.count_simulated_work_items(ctx.conn, states=states, repo_key=repo)
    )
    # A third number, for the third section (issue #21). Milestone 008 corrected the counts
    # and the listing and left the anomaly block below them drawn from an unscoped query, so
    # `status --include-simulated` and `status` printed the same anomalies while claiming to
    # differ. No window applies here — `status` has never had one — so this is a plain length.
    withheld_anomalies = (
        0 if include_simulated else len(db.list_simulated_anomalies(ctx.conn))
    )

    result.data = {
        "effect_level": str(ctx.effect_level),
        "health": report.to_dict(),
        "counts": counts,
        "items": [_item_dict(i) for i in items],
        "anomalies": [_anomaly_dict(a) for a in anomalies],
        "include_simulated": include_simulated,
        "dispatch_paused": control_state.paused,
        "dispatch_paused_at": control_state.paused_at,
        "dispatch_paused_by": control_state.paused_by,
        "capacity": _capacity_dict(snap, ctx.config.dispatch.order),
        "queue": [_queue_dict(entry) for entry in queue],
        # Issue #48. Built from stored rows and configuration only — no network call —
        # which is what makes "why is this repository not ordered by its board?"
        # answerable with the board unreachable, and what keeps `status` free.
        "projects": _project_rows(ctx, queue),
        # Keyed to the two sections it explains, and always present: a consumer must never
        # have to tell "nothing was withheld" apart from "this build does not report it",
        # which is the absent-versus-zero ambiguity this milestone removes from the text.
        "withheld_simulated": {
            "counts": withheld_counts,
            "items": withheld_items,
            "anomalies": withheld_anomalies,
        },
    }

    result.say(f"effect level : {ctx.effect_level}")
    result.say(f"health       : {'ok' if report.healthy else 'STALE'} — {report.reason}")
    # FR-036: a system that is healthy and deliberately doing nothing must not read as a
    # system that is healthy and doing nothing for no reason.
    result.say(
        "dispatch     : "
        + (
            f"PAUSED since {timefmt.local(control_state.paused_at)} "
            f"(by {control_state.paused_by})"
            if control_state.paused
            else "running"
        )
    )
    result.say(f"capacity     : {snap.describe()}")
    result.say(f"order        : {ctx.config.dispatch.order}")
    _say_holds_summary(result, ctx)
    result.say(f"database     : {ctx.layout.db_path} (schema {SCHEMA_VERSION})")
    result.say()

    _say_queue(result, queue)
    _say_projects(result, result.data["projects"])
    if counts:
        result.say("counts by state:")
        for name in sorted(counts):
            result.say(f"  {name:<16} {counts[name]}")
        if withheld_counts:
            result.say(f"  {_withheld_note(withheld_counts)}")
    elif withheld_counts:
        # Not "no work items yet": the ``yet`` describes a system that has not started
        # producing work, which is the wrong thing to say when the rows exist and are
        # being withheld from this view on purpose.
        result.say(f"no work items ({_withheld_note(withheld_counts)})")
    else:
        result.say("no work items yet")
    result.say()

    if items:
        # The Spec Kit column appears only when something in this listing has a phase.
        # Always showing it would add a column that is empty on most rows of most
        # listings — FR-015's "show nothing" applied to the table as well as the cell.
        show_speckit = any(i.speckit_phase for i in items)
        rows = [
            [
                str(i.id),
                str(i.state) + ("*" if i.dry_run else ""),
                i.repo_key,
                f"#{i.issue_number}",
                (i.title[:48] + "…") if len(i.title) > 49 else i.title,
                *([i.speckit_phase or ""] if show_speckit else []),
                (i.failure_reason or i.blocked_reason or "")[:60],
            ]
            for i in items
        ]
        headers = ["id", "state", "repo", "issue", "title"]
        if show_speckit:
            headers.append("spec-kit")
        headers.append("reason")
        for line in _table(rows, headers):
            result.say(line)
        if any(i.dry_run for i in items):
            result.say()
            result.say("* = simulated (dry-run) row")
        # Whenever anything was withheld, not only when the listing came out empty: two
        # visible rows beneath a six-row queue is the same contradiction, only quieter.
        if withheld_items:
            result.say()
            result.say(_withheld_note(withheld_items))
    elif withheld_items:
        result.say(f"no matching work items ({_withheld_note(withheld_items)})")
    else:
        result.say("no matching work items")

    if anomalies or withheld_anomalies:
        # The header prints when rows were withheld even though none are visible, and that
        # case is the reported defect rather than an edge of it. Falling silent there says
        # "nothing was detected" about a machine holding two open anomalies, which is the
        # misreading this whole change exists to prevent.
        result.say()
        result.say(f"unacknowledged anomalies ({len(anomalies)}):")
        for anomaly in anomalies:
            result.say(
                f"  [{anomaly.id}]{'*' if anomaly.dry_run else ''} {anomaly.kind} "
                f"{anomaly.entity_type or ''}:{anomaly.entity_id or ''} "
                f"@ {timefmt.local(anomaly.detected_at)}"
            )
        if withheld_anomalies:
            result.say(f"  {_withheld_note(withheld_anomalies)}")
    return result


def capacity(
    ctx: Context,
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> Result:
    """How full the machine is, whose sessions those are, and in what order work sits.

    The terminal door onto the same snapshot the dispatcher gates on (FR-044). It is not a
    second computation of the numbers — it is the same function, which is the whole reason
    ``capacity.py`` exists as a module rather than as a few lines inside ``dispatch.py``.

    Exits non-zero when capacity cannot be observed (FR-045). That is not a failure of the
    command — the command worked — but the answer it produced is "I do not know how many
    sessions are running", and a script that treats that as "zero are running" is the
    precise mistake this milestone exists to make impossible. The same code ``health`` uses
    for a check that ran and did not pass.
    """
    result = Result()
    snap = capacity_mod.snapshot(
        ctx.conn,
        config=ctx.config,
        audit=ctx.audit,
        registry_dir=registry_dir,
        proc_root=proc_root,
    )
    order = ctx.config.dispatch.order

    settings = _repo_settings(ctx, snap)
    result.data = {
        "observable": snap.observable,
        "degraded": snap.degraded,
        "total": snap.total,
        "global_cap": snap.global_cap,
        "ours": len(snap.ours),
        "others": snap.others,
        # Unchanged in key and in meaning: the live-session count per repository. Anything
        # reading it today keeps reading exactly what it read before (milestone 047).
        "per_repo": dict(sorted(snap.per_repo.items())),
        "repos": settings,
        "wait_for_merge": ctx.config.dispatch.wait_for_merge,
        "order": order,
        "reason": snap.reason,
    }

    if not snap.observable:
        result.code = EXIT_CHECK_FAILED
        result.say(f"capacity     : UNOBSERVABLE — {snap.reason}")
        result.say("dispatch     : withheld until capacity can be observed again")
        result.say(f"order        : {order}")
        return result

    result.say(f"capacity     : {snap.total} of {snap.global_cap} sessions running")
    # FR-003. The split is the actionable half: "two of these are mine" tells the author to
    # close one of their own, and "two are the daemon's" tells them to wait.
    result.say(f"  ours       : {len(snap.ours)}")
    result.say(f"  others     : {snap.others} (started outside this system)")
    result.say(f"observable   : yes{' (degraded — counted via /proc)' if snap.degraded else ''}")
    if snap.degraded:
        result.say(
            "               session ids are unavailable on that path, so the total is a "
            "ceiling rather than a fact"
        )
    result.say(f"order        : {order}")
    result.say(
        f"wait-merge   : {'on' if ctx.config.dispatch.wait_for_merge else 'off'} globally"
    )
    result.say()
    if settings:
        result.say("per repository:")
        for row in settings:
            cap = f"{row['sessions']} of {row['cap']} sessions"
            source = "configured" if row["cap_explicit"] else "default"
            wait = "on" if row["wait_for_merge"] else "off"
            wait_source = "configured" if row["wait_explicit"] else "default"
            board = "on" if row["project_ordering"] else "off"
            board_source = "configured" if row["project_explicit"] else "default"
            result.say(
                f"  {row['repo_key']:<28} {cap:<18} {f'({source})':<13}"
                f"wait-for-merge: {wait:<3} ({wait_source})  "
                f"board-order: {board:<3} ({board_source})"
            )
    else:
        result.say("no repository is onboarded")
    return result


def _project_rows(ctx: Context, queue: list[Any]) -> list[dict[str, Any]]:
    """Every onboarded repository's board state, and why it is or is not governed.

    Shaped like :func:`_repo_settings` and for the same reason: each value travels with
    where it came from, so a surface can tell the author whether they chose it and
    therefore which file to edit.

    ``held_off_column`` is FR-030. A repository whose entire backlog is parked has ready
    items and dispatches none of them, and without a count that reads exactly like a
    repository with no work at all.
    """
    from robot_army import ordering as ordering_mod

    boards = db.list_repo_projects(ctx.conn)
    parked: dict[str, int] = {}
    for entry in queue:
        if entry.hold is ordering_mod.HoldReason.OFF_COLUMN:
            parked[entry.item.repo_key] = parked.get(entry.item.repo_key, 0) + 1

    rows: list[dict[str, Any]] = []
    for key in sorted(set(repos_mod.known(ctx.conn)) | set(boards)):
        enabled, explicit = ctx.config.effective_project_ordering(key)
        board = boards.get(key)
        rows.append(
            {
                "repo_key": key,
                "enabled": enabled,
                "enabled_explicit": explicit,
                "governs": bool(board and board.governs) and enabled,
                "project_number": getattr(board, "project_number", None),
                "project_title": getattr(board, "project_title", None),
                "project_url": getattr(board, "project_url", None),
                "project_source": getattr(board, "project_source", None),
                "column": getattr(board, "column_name", None),
                "column_source": getattr(board, "column_source", None),
                "last_read_at": getattr(board, "last_read_at", None),
                # `health._age` rather than a second parser: one spelling of "how old is
                # this timestamp" is enough, and the two would drift on the format.
                "last_read_age_seconds": health._age(
                    getattr(board, "last_read_at", None)
                ),
                "unresolved_reason": getattr(board, "unresolved_reason", None),
                "last_error": getattr(board, "last_error", None),
                "consecutive_failures": getattr(board, "consecutive_failures", 0) or 0,
                "held_off_column": parked.get(key, 0),
            }
        )
    return rows


def _say_projects(result: Result, rows: list[dict[str, Any]]) -> None:
    """One line per repository whose board state is worth stating.

    Repositories with board ordering enabled and no board are skipped: most installations
    have none, and a line saying so on every one of them would bury the rows that matter.
    A repository the author switched **off** is shown, because that is a choice they made
    and may have forgotten.
    """
    interesting = [
        row
        for row in rows
        if row["governs"]
        or row["unresolved_reason"]
        or row["last_error"]
        or row["enabled_explicit"]
    ]
    if not interesting:
        return
    result.say("project boards:")
    for row in interesting:
        if not row["enabled"]:
            # The source, not a guess at it. A repository reaches this line either by its
            # own setting or by the global one, and telling the author "configured" when
            # they inherited it sends them looking for a line that is not in their file —
            # which is the exact failure the explicit flag exists to prevent.
            source = "configured" if row["enabled_explicit"] else "default"
            result.say(f"  {row['repo_key']:<28} board ordering off ({source})")
            continue
        if row["governs"]:
            age = row["last_read_age_seconds"]
            stale = f", read {age}s ago" if age is not None else ""
            held = (
                f", {row['held_off_column']} held off-column"
                if row["held_off_column"]
                else ""
            )
            result.say(
                f"  {row['repo_key']:<28} #{row['project_number']} "
                f"{row['project_title']} -> {row['column']!r} "
                f"({row['project_source']}/{row['column_source']}{stale}{held})"
            )
        else:
            why = row["unresolved_reason"] or row["last_error"] or "never read"
            result.say(f"  {row['repo_key']:<28} not ordered by a board: {why}")
    result.say()


def _repo_settings(ctx: Context, snap: Any) -> list[dict[str, Any]]:
    """Every onboarded repository's two limits, and where each of them came from.

    Widened in milestone 047 from *repositories with a live session*, and the widening is
    the point: a repository holding its queue for a merge has **no** live session, so the
    old listing omitted exactly the repository the author had come to ask about. An
    onboarded repository with nothing running is now a row reading zero rather than an
    absence.

    ``repos.known`` rather than ``config.repos`` supplies the universe, because a
    repository needs no ``[repos.*]`` section to be onboarded and inherits both settings
    when it has none — and those are precisely the rows where "where did this come from?"
    is worth answering.
    """
    rows: list[dict[str, Any]] = []
    for key in sorted(set(repos_mod.known(ctx.conn)) | set(snap.per_repo)):
        cap, cap_explicit = ctx.config.effective_repo_cap(key)
        wait, wait_explicit = ctx.config.effective_wait_for_merge(key)
        ordering_on, ordering_explicit = ctx.config.effective_project_ordering(key)
        rows.append(
            {
                "repo_key": key,
                "sessions": snap.per_repo.get(key, 0),
                "cap": cap,
                "cap_explicit": cap_explicit,
                "wait_for_merge": wait,
                "wait_explicit": wait_explicit,
                "project_ordering": ordering_on,
                "project_explicit": ordering_explicit,
            }
        )
    return rows


def _capacity_dict(snap: Any, order: str) -> dict[str, Any]:
    """The capacity summary both the terminal and the web chrome render."""
    return {
        "observable": snap.observable,
        "degraded": snap.degraded,
        "total": snap.total,
        "global_cap": snap.global_cap,
        "ours": len(snap.ours),
        "others": snap.others,
        "per_repo": dict(sorted(snap.per_repo.items())),
        "order": order,
        "reason": snap.reason,
        "summary": snap.describe(),
    }


def _queue_dict(entry: Any) -> dict[str, Any]:
    return {
        "position": entry.position,
        "item_id": entry.item.id,
        "repo_key": entry.item.repo_key,
        "issue_number": entry.item.issue_number,
        "title": entry.item.title,
        "dry_run": entry.item.dry_run,
        "hold": str(entry.hold) if entry.hold else None,
        "detail": entry.detail,
    }


def _item_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "cleanup_state": item.cleanup_state,
        "cleanup_reason": item.cleanup_reason,
        "cleaned_at": item.cleaned_at,
        "source_id": item.source_id,
        "source_url": item.source_url,
        "repo_key": item.repo_key,
        "issue_number": item.issue_number,
        "title": item.title,
        "state": str(item.state),
        "dry_run": item.dry_run,
        "simulated": item.dry_run,
        "worktree_path": item.worktree_path,
        "branch": item.branch,
        "failure_reason": item.failure_reason,
        "blocked_reason": item.blocked_reason,
        "discovered_at": item.discovered_at,
        "updated_at": item.updated_at,
        # Milestone 007. ``None`` for everything that is not a Spec Kit run, which is most
        # of them — an absent phase is a resting state, not a fault, so it is reported as
        # nothing rather than as "unknown".
        "speckit_phase": item.speckit_phase,
        "speckit_feature_dir": item.speckit_feature_dir,
        "speckit_phase_at": item.speckit_phase_at,
        "speckit_baseline": item.speckit_baseline,
        # Issue #143, and here rather than in each view for the reason every other field is
        # here: the listings and the item page render the same rows through the same dict,
        # so a pull request added to one surface cannot go missing from another, and the
        # JSON payload carries what the HTML shows by construction.
        **pull_request_view(item),
    }


def _speckit_column(ctx: Context, key: str, path: Any) -> tuple[str, dict[str, Any]]:
    """Whether this clone uses Spec Kit, and whether the behaviour is on for it (FR-021).

    Four values, and the fourth is the point: ``?`` is *the clone could not be read*, which
    is a different statement from ``no``. A listing that answered "no" for a clone that has
    moved would be asserting something it has no evidence for.

    ``off`` means detected and suppressed. Milestone 007 switches this on by itself when a
    repository turns out to use Spec Kit, so the compensation is that the author can see
    which repositories that is *before* labelling anything — this column is that.
    """
    enabled, suppressed_by = ctx.config.speckit_enabled_for(key)
    try:
        readable = Path(path).is_dir()
    except OSError:
        readable = False
    if not readable:
        return "?", {"detected": None, "reason": "clone could not be read", "enabled": enabled}

    detection = speckit.detect(path)
    if not detection.detected:
        return "no", {
            "detected": False,
            "reason": detection.reason,
            "enabled": enabled,
        }
    cell = "yes" if enabled else "off"
    detail: dict[str, Any] = {
        "detected": True,
        "reason": detection.reason,
        "form": detection.form,
        "enabled": enabled,
    }
    if not enabled and suppressed_by:
        detail["suppressed_by"] = suppressed_by
    if enabled:
        # Which setting supplies each lifecycle instruction here (milestone 039, FR-027).
        # From the same ``speckit_commands_for`` call the audit record uses, so the listing
        # and the log cannot disagree about the same question.
        #
        # The **table** deliberately keeps its four values and gains no eighth column: the
        # cell answers "is this repository getting the block at all", which this milestone
        # did not change, and instructions are prose that no table cell can hold. Together
        # with the configuration file — where the maintainer wrote the text — the
        # provenance here is a complete offline answer (research R7).
        instructions = ctx.config.speckit_commands_for(key)
        if instructions:
            detail["instructions"] = {i.command: i.source for i in instructions}
    return cell, detail


def _speckit_lines(item: Any) -> list[str]:
    """The Spec Kit phase, or an explanation of why there is none (milestone 007).

    Nothing at all for an ordinary item, which is the common case and the requirement
    (FR-015): an empty or "unknown" phase on every non-Spec-Kit row would be a column that
    means nothing four times for every once it means something.

    The second branch is where the silence gets explained. An item whose worktree *is* a
    Spec Kit project but whose baseline is ``NULL`` will never report a phase, and that is
    correct — but "correct" and "mysterious" are the same thing from the outside, so the
    reason is stated here, at the point the question is actually asked, rather than logged
    once per reconciliation cycle for the life of the item.
    """
    if item.speckit_phase:
        since = f" (since {timefmt.local(item.speckit_phase_at)})" if item.speckit_phase_at else ""
        return [f"  spec-kit   : {item.speckit_phase} — {item.speckit_feature_dir}{since}"]
    if (
        item.speckit_baseline is None
        and item.worktree_path
        and speckit.detect(item.worktree_path).detected
    ):
        return [
            "  spec-kit   : detected, but no baseline was recorded for this worktree, "
            "so no phase is derived"
        ]
    return []


def _session_dict(session: Any) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "attempt": session.attempt,
        "state": str(session.state),
        "dry_run": session.dry_run,
        "pid": session.pid,
        "scope": session.scope,
        "host_socket": session.host_socket,
        "window_id": session.window_id,
        "exit_code": session.exit_code,
        "signal": session.signal,
        "started_at": session.started_at,
        "confirmed_at": session.confirmed_at,
        "ended_at": session.ended_at,
    }


def _anomaly_dict(anomaly: Any) -> dict[str, Any]:
    return {
        "id": anomaly.id,
        "kind": anomaly.kind,
        "entity_type": anomaly.entity_type,
        "entity_id": anomaly.entity_id,
        # An anomaly's detail is an open-ended dict written by whatever raised it, and it
        # is *stored* rather than only logged — so the audit log's redaction choke point
        # never saw it. Both front ends render this dict (the CLI to stdout, the web to a
        # page and a JSON payload), so it passes through the same choke point here, once,
        # rather than being filtered separately in each (FR-020).
        "detail": audit_mod.redact(anomaly.detail_obj),
        "detected_at": anomaly.detected_at,
        "acknowledged_at": anomaly.acknowledged_at,
        # Two different ways a row leaves the open list, kept apart deliberately (issue
        # #138). "A maintainer looked at this" and "the system re-checked and it is no
        # longer true" are not the same fact, and a reader of `--all` who cannot tell them
        # apart cannot tell which conditions were actually dealt with.
        "resolved_at": anomaly.resolved_at,
        # Issue #21. A payload that hid rows without letting a consumer identify the ones it
        # did show would have moved the ambiguity rather than removed it.
        "simulated": anomaly.dry_run,
    }


# -- show -------------------------------------------------------------------


#: The three keys :func:`resume_signals` merges in from the work item's own row (issue
#: #143). Everything else in that dict is computed for the call; these are read back from
#: storage, so ``show`` prints them separately rather than under a heading that says
#: nothing there is stored.
_STORED_SIGNAL_KEYS = frozenset(
    {"pull_requests", "pull_requests_at", "pull_requests_known"}
)


def _pull_request_line(item: Any) -> str:
    """One line naming this item's pull requests, for the terminal (issue #143).

    The three states the interface draws, in the words the terminal has room for. "not
    checked" and "none" must not collapse into one phrase here for the same reason they must
    not on the web page: one is an unasked question and the other is GitHub's answer.
    """
    if item.pull_requests is None:
        return "(not checked)"
    found = item.pull_request_list
    if not found:
        return f"(none, as of {timefmt.local(item.pull_requests_at)})"
    listed = "  ".join(
        f"#{pr.get('number')} {pr.get('state')} {pr.get('url')}" for pr in found
    )
    return f"{listed}  (as of {timefmt.local(item.pull_requests_at)})"


def show(ctx: Context, item_id: int) -> Result:
    """Everything about one work item, including the FR-048 resume-decision signals."""
    result = Result()
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])

    attempts = db.list_sessions_for_item(ctx.conn, item_id)
    signals = resume_signals(ctx, item)

    card = _card_for_item(ctx, item)
    result.data = {
        "item": _item_dict(item),
        "history": _history(item),
        "sessions": [_session_dict(s) for s in attempts],
        "resume_signals": signals,
        # FR-017 and FR-048: where a work item's issue came from a card, the card is shown
        # beside the issue wherever the issue is shown. ``None`` when it did not.
        "card": card,
    }

    result.say(f"work item {item.id}{_mark(item)}")
    result.say(f"  source     : {item.source_id}  {item.source_url}")
    if card:
        result.say(f"  card       : {card['card_id']}  {card['card_url']}")
    result.say(f"  repository : {item.repo_key}")
    result.say(f"  title      : {item.title}")
    result.say(f"  state      : {item.state}")
    result.say(f"  worktree   : {item.worktree_path or '(none)'}")
    result.say(f"  branch     : {item.branch or '(none)'}")
    result.say(f"  pull req.  : {_pull_request_line(item)}")
    if item.failure_reason:
        result.say(f"  failure    : {item.failure_reason}")
    if item.blocked_reason:
        result.say(f"  blocked    : {item.blocked_reason}")
    # A retained worktree or branch is visible here rather than only in the log, which is
    # what makes "why is this 499 MB still here?" answerable without reading anything.
    if item.cleanup_state:
        result.say(f"  cleanup    : {item.cleanup_state} — {item.cleanup_reason or ''}")
        result.say(f"  cleaned at : {timefmt.local(item.cleaned_at)}")
    for line in _speckit_lines(item):
        result.say(line)

    result.say()
    result.say("state history:")
    for when, what in _history(item):
        # The conversion belongs to this loop, not to ``_history`` — that helper also
        # feeds ``result.data["history"]``, which ``--json`` renders and which must stay
        # UTC (FR-012).
        result.say(f"  {timefmt.local(when)}  {what}")

    result.say()
    if attempts:
        result.say(f"sessions ({len(attempts)} attempt(s)):")
        for session in attempts:
            exit_text = "—" if session.exit_code is None else str(session.exit_code)
            signal_text = f" signal {session.signal}" if session.signal is not None else ""
            result.say(
                f"  #{session.attempt} {session.state}{'*' if session.dry_run else ''} "
                f"id={session.session_id} pid={session.pid or '—'} "
                f"exit={exit_text}{signal_text}"
            )
            result.say(
                f"       started {timefmt.local(session.started_at)} "
                f"ended {timefmt.local(session.ended_at) or '—'}"
            )
            if session.host_socket:
                result.say(f"       reattach: dtach -a {session.host_socket}")
    else:
        result.say("no session attempts yet")

    result.say()
    result.say("resume-decision signals (computed now, never stored):")
    for key, value in signals.items():
        # The pull-request keys are the one part of this dict that *is* stored, and they
        # are printed above under `pull req.` where that is not a contradiction. Leaving
        # them here would put a Python repr of a list of dicts in user-facing output,
        # beneath a heading asserting the opposite of the truth about them.
        if key in _STORED_SIGNAL_KEYS:
            continue
        result.say(f"  {key:<22} {value}")
    if item.prepare_output:
        result.say()
        result.say("preparation output:")
        for line in item.prepare_output.splitlines()[:40]:
            result.say(f"  {line}")
    return result


def _history(item: Any) -> list[tuple[str, str]]:
    stamps = [
        (item.discovered_at, "discovered"),
        (item.ready_at, "ready"),
        (item.dispatching_at, "dispatching"),
        (item.active_at, "active"),
        (item.ended_at, "session ended"),
        (item.done_at, "done"),
    ]
    return [(when, what) for when, what in stamps if when]


#: How long a GitHub-derived resume signal may be served from memory (research.md R9).
#: An interrupted view with five items auto-refreshing every 10 seconds would otherwise
#: make 1,800 GitHub calls an hour asking a question that cannot change as a result of
#: anything happening on this machine — competing directly with the polling budget FR-008
#: exists to protect.
REMOTE_SIGNAL_TTL_SECONDS = 60.0

#: How long a locally observed resume signal may be served from memory (RA-14).
#:
#: Deliberately *below* the default ``[web] refresh_seconds = 10``. That is the whole
#: choice: a page left open observes the worktree afresh on every refresh, so the maintainer
#: editing a file in the checkout still sees it on their next refresh — which is what
#: :func:`local_resume_signals` was recomputed on every call to guarantee. What five seconds
#: removes is the *burst*. ``/interrupted`` renders one card per interrupted and awaiting-
#: review item and each card costs several ``git`` subprocesses, so a caller issuing requests
#: in a loop was forking git at the rate it could send. Now it forks at most once per item
#: per five seconds however fast the requests arrive.
#:
#: A number *above* the refresh interval would buy nothing extra and would trade away the
#: freshness guarantee, which is why this one is pinned below it rather than to it.
LOCAL_SIGNAL_TTL_SECONDS = 5.0

#: (item id, branch) -> (computed_at monotonic, signals). Per-process, non-authoritative,
#: bounded by time and by the number of interrupted items, and lost on restart. Nothing is
#: persisted, so FR-013's prohibition on a *stored* copy is honoured as written.
_REMOTE_SIGNAL_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}
_REMOTE_SIGNAL_LOCK = threading.Lock()

#: (item id, repo key, worktree path, branch) -> (computed_at monotonic, signals).
#: The item's own fields, so an item whose worktree was reclaimed or whose branch was renamed
#: is a different key rather than a stale answer about somewhere else.
#:
#: **The base ref is deliberately not among them**, though ``worktree.condition`` is given it.
#: It used to be, back when it was a dictionary lookup; since issue #150 resolving it reads
#: the clone, and a key that costs a ``git`` fork to compute is a cache that cannot prevent
#: the ``git`` forks it exists to prevent — RA-14's "at most once per item per window" would
#: silently have become "always". It is resolved on a miss instead, where the observation is
#: being made anyway. The cost is that a base ref which changes mid-window is seen on the next
#: one, which is the same five seconds of staleness every other input to this observation
#: already accepts.
#:
#: A second dict rather than one shared with the remote half, because the two have different
#: lifetimes for different reasons — a minute for "has GitHub changed", five seconds for "has
#: the checkout changed" — and that split is the fact the two functions already encode.
_LOCAL_SIGNAL_CACHE: dict[
    tuple[int, str, str, str], tuple[float, dict[str, Any]]
] = {}
_LOCAL_SIGNAL_LOCK = threading.Lock()


def _monotonic() -> float:
    """Indirection so a test can move the clock without sleeping for a minute."""
    return time.monotonic()


def _purge_expired(
    cache: dict[Any, tuple[float, dict[str, Any]]], ttl: float, now: float
) -> None:
    """Drop entries older than ``ttl``. Called on insert, under the cache's own lock.

    This is the whole of the bound on both caches (RA-14). Each therefore holds at most the
    distinct items observed within one of its own TTL windows, which is itself bounded by the
    number of rows the views render. An LRU or a maximum size would be a second policy
    answering a question this one already answers.
    """
    stale = [key for key, (computed_at, _) in cache.items() if now - computed_at >= ttl]
    for key in stale:
        del cache[key]


def clear_resume_signal_cache() -> None:
    """Drop every cached signal, local and remote. For tests, and for a front end that wants
    a guaranteed-fresh read after acting on an item."""
    with _REMOTE_SIGNAL_LOCK:
        _REMOTE_SIGNAL_CACHE.clear()
    with _LOCAL_SIGNAL_LOCK:
        _LOCAL_SIGNAL_CACHE.clear()


def forget_resume_signals(item_id: int) -> None:
    """Drop every cached signal for one item, local and remote (RA-14 FR-010).

    Called after an action succeeds, so the page rendered next reflects the action rather than
    an observation made before it. Both halves, because both describe the item and an action
    can change either: abandoning an item can close its issue, resuming it can dirty its
    worktree.
    """
    with _REMOTE_SIGNAL_LOCK:
        for key in [k for k in _REMOTE_SIGNAL_CACHE if k[0] == item_id]:
            del _REMOTE_SIGNAL_CACHE[key]
    with _LOCAL_SIGNAL_LOCK:
        for key in [k for k in _LOCAL_SIGNAL_CACHE if k[0] == item_id]:
            del _LOCAL_SIGNAL_CACHE[key]


def local_resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """The three signals that come from local git, reused in-process for five seconds (RA-14).

    Volatile precisely because the maintainer may be in the worktree with an editor open —
    ``docs/state.md`` says these are computed on demand and never stored "because a stored
    copy would be wrong the moment I touched the directory", and that reasoning applies to
    these and not to the remote pair. It still does. What it argues for is that the value must
    not outlive the maintainer's next look at the page, and
    :data:`LOCAL_SIGNAL_TTL_SECONDS` is set below the default ``[web] refresh_seconds`` for
    exactly that: a page left open observes the checkout afresh on every refresh.

    What the window removes is the *burst*. ``/interrupted`` renders one card per interrupted
    and awaiting-review item and every card lands here, so before RA-14 a page on another site
    could fork ``git`` several times per item at whatever rate it could send requests. Now it
    forks at most once per item per window however fast they arrive.

    Every returned value carries ``local_signals_age_seconds``, so a reused answer is
    *visible* as reused rather than implied to be current — the same guarantee
    :func:`remote_resume_signals` gives its own half, kept as a second number because the two
    halves age on TTLs an order of magnitude apart and one number would misreport one of them.
    """
    signals: dict[str, Any] = {
        "worktree_present": False,
        "uncommitted_changes": None,
        "commits_on_branch": None,
        "local_signals_age_seconds": 0,
    }
    repo = repos_mod.resolve(ctx.conn, ctx.config, item.repo_key)
    if repo is None or not item.worktree_path or not item.branch:
        return signals
    # An item whose worktree was reclaimed or whose branch was renamed is a different key, so
    # it is observed afresh rather than served an answer about somewhere it was never made.
    # Every field here is free; see the note on the cache itself for why the base ref, which
    # is not, is resolved below the lookup rather than inside the key.
    key = (
        int(item.id),
        str(item.repo_key),
        str(item.worktree_path),
        str(item.branch),
    )
    now = _monotonic()
    with _LOCAL_SIGNAL_LOCK:
        cached = _LOCAL_SIGNAL_CACHE.get(key)
        if cached is not None and now - cached[0] < LOCAL_SIGNAL_TTL_SECONDS:
            return {**cached[1], "local_signals_age_seconds": int(now - cached[0])}

    base_ref = repos_mod.base_ref(
        ctx.config, item.repo_key, ctx.boundaries.version_control, repo.path
    ).ref
    try:
        condition = worktree.condition(
            ctx.boundaries.version_control,
            str(repo.path),
            item.worktree_path,
            item.branch,
            base_ref,
        )
        signals["worktree_present"] = condition.exists
        signals["uncommitted_changes"] = condition.dirty
        signals["commits_on_branch"] = condition.commits_ahead
    except BoundaryError as exc:
        # Not cached, for the reason ``remote_resume_signals`` does not cache a
        # ``TransportError``: "I could not look" is not an answer, and holding it for five
        # seconds would suppress the attempt that would have shown the checkout recovering.
        signals["worktree_error"] = str(exc)
        return signals

    with _LOCAL_SIGNAL_LOCK:
        _purge_expired(_LOCAL_SIGNAL_CACHE, LOCAL_SIGNAL_TTL_SECONDS, now)
        _LOCAL_SIGNAL_CACHE[key] = (now, dict(signals))
    return signals


def remote_resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """The signal that reaches GitHub, cached in-process for a minute (R9).

    Every returned value carries ``signals_age_seconds``, so a cached answer is *visible*
    as stale rather than implied to be current. ``0`` means it was computed just now.

    **It used to be two.** The pull-request half asked GitHub *while a page was rendering*,
    which issue #143 replaced with the set the reconcile pass stores — see
    :func:`pull_request_view`. That reads like a speed change and is really a correctness
    one: nothing stopped the live answer here disagreeing with what every other surface
    showed, and the freshness is identical anyway, since this cache's window and the
    reconcile interval are both sixty seconds.
    """
    empty: dict[str, Any] = {"issue_closed": None, "signals_age_seconds": 0}
    repo = repos_mod.resolve(ctx.conn, ctx.config, item.repo_key)
    if repo is None or not item.branch:
        return empty
    if item.dry_run:
        # FR-055: a simulated row must not cause outward-facing effects, and asking
        # GitHub about it would be exactly that.
        return empty

    key = (int(item.id), str(item.branch))
    now = _monotonic()
    with _REMOTE_SIGNAL_LOCK:
        cached = _REMOTE_SIGNAL_CACHE.get(key)
        if cached is not None and now - cached[0] < REMOTE_SIGNAL_TTL_SECONDS:
            return {**cached[1], "signals_age_seconds": int(now - cached[0])}

    fresh: dict[str, Any] = {"issue_closed": None}
    try:
        fresh["issue_closed"] = ctx.boundaries.issue_reader.is_closed(
            item.repo_key, item.issue_number
        )
    except TransportError as exc:
        # Not cached: "I could not ask" is not an answer, and caching it would suppress
        # the next attempt for a minute — the silent failure Principle III forbids.
        return {**fresh, "github_error": str(exc), "signals_age_seconds": 0}

    with _REMOTE_SIGNAL_LOCK:
        _purge_expired(_REMOTE_SIGNAL_CACHE, REMOTE_SIGNAL_TTL_SECONDS, now)
        _REMOTE_SIGNAL_CACHE[key] = (now, fresh)
    return {**fresh, "signals_age_seconds": 0}


def pull_request_view(item: Any) -> dict[str, Any]:
    """What this item's pull requests are, read from its own row (issue #143).

    No network, no cache, no clock: the reconcile pass established this and the interface
    only reads it. That is what lets every page render with GitHub unreachable, and it is
    the single place the three states are decided.

    ``pull_requests_known`` is the field that keeps them three. ``False`` with an empty list
    means *never looked up*; ``True`` with an empty list means *looked up, and there are
    none*. No caller may infer the difference from the list alone — that inference is
    exactly the confident "there is no pull request", earned by never having asked, that the
    whole feature exists to prevent.
    """
    return {
        "pull_requests": item.pull_request_list,
        "pull_requests_at": item.pull_requests_at,
        "pull_requests_known": item.pull_requests is not None,
    }


def resume_signals(ctx: Context, item: Any) -> dict[str, Any]:
    """Every signal behind the resume decision (FR-048, FR-013), from three sources.

    Split by cost; see R9 and the halves above. Nothing here is persisted by this function.
    The checkout pair and the ``issue_closed`` answer are each reused in-process on their own
    window — five seconds and a minute — and each carries its own age, so a value that was
    reused is visible as reused rather than implied to be current. The two ages stay separate
    because the two windows differ by an order of magnitude and one number would have to
    misreport one of them.

    The pull requests are the third source and behave unlike the other two: they are *stored*
    rather than fetched, so their age is the reconcile pass's last confirmation rather than a
    cache window, and it is carried as ``pull_requests_at`` rather than as a third seconds
    count.
    """
    return {
        **local_resume_signals(ctx, item),
        **remote_resume_signals(ctx, item),
        **pull_request_view(item),
    }


# -- prompt preview ---------------------------------------------------------

#: The four shapes the context note takes (contracts/cli.md). Written every time, including
#: the worktree case: the alternative is a reader having to know that *no note* means
#: *worktree*, which is a rule to remember rather than a fact on the screen.
#:
#: Keyed on its own name rather than on ``context_source``, because the clone splits in two
#: and the audit field does not. A reclaimed worktree and an issue that never had one both
#: read from the clone, but only one of them can be told "no worktree for this issue"
#: without lying — and this note exists precisely to stop a reader drawing the wrong
#: conclusion from a missing section, so a false parenthetical in it is worse than none.
_CONTEXT_NOTES = {
    "worktree": "context read from the worktree at {path}",
    "clone": "context read from the clone at {path} (no worktree for this issue)",
    "clone_reclaimed": (
        "context read from the clone at {path} — this issue's worktree at {worktree} is "
        "gone, so this is not necessarily what its session was sent"
    ),
    "none": (
        "no readable directory at {path} — repository instructions and spec kit guidance "
        "are omitted"
    ),
}


def _preview_refusal(
    ctx: Context,
    *,
    entity_id: str,
    cause: str,
    message: str,
    code: int,
    detail: dict[str, Any] | None = None,
) -> Result:
    """One refusal, recorded and returned.

    Every exit path of :func:`prompt_preview` writes exactly one ``prompt.preview`` record,
    which is what makes "every run leaves a log entry" a property of the code rather than a
    rule four returns have to remember (spec SC-005).

    The message goes in ``lines`` and the code is non-zero, so ``cli.main``'s existing
    routing puts it on stderr and leaves stdout empty. That is the whole of FR-014: there is
    no path that both fails and says something on stdout, because the routing is one
    expression in ``main`` rather than a branch here.

    ``detail`` carries **every field the caller had resolved by the time it refused**, and a
    refusal added below must pass its own. A reader reconstructing a run should not have to
    split ``entity_id`` on ``#`` to learn which repository was asked about: that string is an
    identifier, not a pair of fields, and for a malformed key it is not even a valid one.
    The single exception is the malformed-key refusal itself, which genuinely has no
    repository key to record — contracts/audit-records.md says so.
    """
    ctx.audit.record(
        "prompt.preview",
        outcome="error",
        entity_type="issue",
        entity_id=entity_id,
        detail={"refused": True, "cause": cause, **(detail or {})},
    )
    return Result(code=code, lines=[message], data={"refused": True, "cause": cause})


def prompt_preview(
    ctx: Context,
    repo_key: str,
    issue_number: int,
    *,
    notes: TextIO | None = None,
) -> Result:
    """The prompt a dispatch of this issue would compose, and nothing else.

    Read-only: no work item, branch, worktree or session is created, and nothing is written
    to the issue source. The only durable effect of a call is its own audit record.

    **This must not reimplement composition.** It resolves the same four arguments
    ``dispatch.build_launch_plan`` resolves and hands them to the same ``prompt.compose``.
    That is the only arrangement in which "the preview matches the dispatch" stays true as
    the prompt is edited underneath it — a second copy of the assembly would be a claim
    maintained by hand, and the first edit to ``compose`` would quietly falsify it.

    ``notes`` is where the context note is written **as it is resolved**, and it is
    deliberately not ``lines``: ``lines`` carries the prompt, and a note mixed into it would
    end up in the caller's redirected file (FR-003/FR-004). ``None`` writes the note nowhere
    but still records it in ``data``, so a non-CLI caller loses nothing.
    """
    result = Result()
    entity_id = f"{repo_key}#{issue_number}"

    owner, _, name = repo_key.partition("/")
    if not owner or not name or "/" in name or any(ch.isspace() for ch in repo_key):
        return _preview_refusal(
            ctx,
            entity_id=entity_id,
            cause="malformed_arguments",
            message=(
                f"{repo_key!r} is not a repository key — expected owner/name, as "
                f"`robot-army repos` prints it"
            ),
            code=EXIT_USAGE,
        )
    if issue_number <= 0:
        return _preview_refusal(
            ctx,
            entity_id=entity_id,
            cause="malformed_arguments",
            message=f"{issue_number} is not an issue number — expected a positive integer",
            code=EXIT_USAGE,
            # The key survived validation above, so it is a resolved field and belongs in
            # the record; the number is the value that was rejected, which is the one thing
            # a reader of this record wants to see.
            detail={"repo_key": repo_key, "issue_number": issue_number},
        )

    # Onboarding is the gate, inherited rather than reinvented: composing this prompt means
    # reading the repository's own files, which is the step onboarding exists to approve.
    repository = repos_mod.resolve(ctx.conn, ctx.config, repo_key)
    if repository is None:
        return _preview_refusal(
            ctx,
            entity_id=entity_id,
            cause="not_onboarded",
            message=(
                f"repository {repo_key!r} is not onboarded, so there is no approved clone "
                f"to read its instructions from — run `robot-army onboard {repo_key}` "
                f"first, or `robot-army repos` to see which repositories are known"
            ),
            code=EXIT_PRECONDITION,
            detail={"repo_key": repo_key, "issue_number": issue_number},
        )

    try:
        issue = ctx.boundaries.issue_reader.get_issue(repo_key, issue_number)
    except BoundaryError as exc:
        # Never a partial or invented prompt: a preview that guessed at the issue would be
        # worse than no preview, because it reads exactly like one that did not guess.
        return _preview_refusal(
            ctx,
            entity_id=entity_id,
            cause="issue_unavailable",
            message=f"could not read {entity_id}: {exc}",
            code=EXIT_FAILED,
            detail={"repo_key": repo_key, "issue_number": issue_number, "error": str(exc)},
        )
    if issue is None:
        return _preview_refusal(
            ctx,
            entity_id=entity_id,
            cause="issue_unavailable",
            message=f"{entity_id} does not exist, or this token cannot see it",
            code=EXIT_FAILED,
            detail={"repo_key": repo_key, "issue_number": issue_number},
        )

    # The live row only. A dry-run row has no worktree on disk — below ``live`` the version
    # control boundary is simulated — and its branch is the one derived below anyway, so
    # consulting it would add a branch that cannot change the answer (research R8).
    item = db.find_work_item(
        ctx.conn, source="github", source_id=entity_id, dry_run=False
    )

    branch = prompt.branch_name(ctx.config.worker.branch_prefix, issue_number, issue.title)
    branch_source = "derived"
    item_id: int | None = None
    if item is not None:
        item_id = item.id
        if item.branch:
            # The recorded branch, not a re-derivation: an issue retitled after dispatch
            # would otherwise be previewed against a branch that does not exist.
            branch = item.branch
            branch_source = "recorded"

    # The worktree first, because that is literally the directory a dispatch read from, and
    # it can carry a ``.claude/robot-army.md`` the clone does not. The clone is the stand-in
    # for an issue that has never been dispatched, and it is an approximation: it may sit on
    # another branch or carry uncommitted changes, which is why the source is always named
    # rather than left to be inferred (research R1, R2).
    #
    # Always a concrete path, never ``None``, including when nothing could be read from it:
    # the note and the record have to say *where* was looked at, or "this repository has no
    # instructions" is indistinguishable from "the wrong directory was read". Which of the
    # three happened is ``context_source``'s job, not the path's.
    recorded_worktree = item.worktree_path if item is not None else None
    if recorded_worktree and Path(recorded_worktree).is_dir():
        context_root = Path(recorded_worktree)
        context_source = "worktree"
    else:
        context_root = Path(repository.path)
        context_source = "clone" if context_root.is_dir() else "none"

    if context_source == "clone" and recorded_worktree:
        note_key = "clone_reclaimed"
    else:
        note_key = context_source
    note = _CONTEXT_NOTES[note_key].format(path=context_root, worktree=recorded_worktree)
    result.data["notes"] = [note]
    if notes is not None:
        # ``flush=True`` because the difference is invisible on a terminal and is the whole
        # behaviour when the two streams are being read apart from each other.
        print(note, file=notes, flush=True)

    if context_source == "none":
        instructions = None
        block = None
    else:
        instructions = prompt.read_instructions(context_root)
        # Dispatch's own function, not a copy of it: detection, per-repository suppression
        # and the configured command list must agree with a real dispatch, and sharing the
        # code is what makes that structural. ``item_id`` is ``None`` when the issue has no
        # row, which keys the record on the repository instead (research R3).
        block = dispatch.speckit_block(
            config=ctx.config,
            audit=ctx.audit,
            repo_key=repo_key,
            item_id=item_id,
            worktree_path=str(context_root),
        )

    text = prompt.compose(
        issue,
        repo_key=repo_key,
        branch=branch,
        instructions=instructions,
        speckit_block=block,
    )

    detail: dict[str, Any] = {
        "repo_key": repo_key,
        "issue_number": issue_number,
        "branch": branch,
        "branch_source": branch_source,
        "context_root": str(context_root),
        "context_source": context_source,
        "instructions": instructions is not None,
        "speckit": block is not None,
    }
    if item_id is not None:
        detail["item_id"] = item_id
    if recorded_worktree and context_source != "worktree":
        # The worktree this item was dispatched into, recorded because it is *not* what was
        # read. Without it the record says "clone" and a reader cannot tell a reclaimed
        # worktree from an issue that never had one — the same distinction the note draws.
        detail["recorded_worktree"] = recorded_worktree
    # Never the composed text, the issue body, or the contents of either optional section.
    # Dispatch does not record them either, and a log that described the rehearsal in more
    # detail than the performance would be the wrong asymmetry (research R4).
    ctx.audit.record(
        "prompt.preview",
        outcome="ok",
        entity_type="issue",
        entity_id=entity_id,
        detail=detail,
    )

    result.data.update(detail)
    result.data["prompt"] = text
    # The prompt is the *only* line, which is what makes stdout carry the prompt and
    # nothing else once ``cli.main`` prints it (FR-003).
    result.say(text)
    return result


# -- onboard ----------------------------------------------------------------


def _resolve_for_onboarding(ctx: Context, repo_key: str) -> repos_mod.Verification:
    """Run contracts/onboarding.md's resolution order and return what it found.

    A thin wrapper rather than inlined, so ``onboard`` reads as *resolve, then refuse or
    approve* and so that ``--reapprove`` and the ``repos`` verb can ask the same question
    without repeating the order. The order itself lives in ``repos``; the decision to
    refuse lives here, which is the module boundary plan.md draws.
    """
    # Step 1 and 2 first: whether this may be onboarded at all is settled before the path
    # is even derived (FR-024). Refusing "no clone at ..." for a repository the author
    # mistyped would send them to look for a directory rather than at the name they typed.
    try:
        permitted = repos_mod.eligibility(ctx.config, repo_key, ctx.boundaries.issue_reader)
    except TransportError as exc:
        # contracts/onboarding.md lists "the source system is unreachable" as a refusal of
        # step 2, not as a crash. Onboarding is the one command that must ask the source
        # system a question, and a bad token or a dropped network is the most ordinary way
        # for that to fail — it deserves the same named, recorded, non-zero exit every
        # other refusal gets rather than a traceback.
        return repos_mod.Verification(
            repo_key,
            None,
            "derived",
            cause="source_unreachable",
            refusal=(
                f"refusing: could not ask {ctx.config.github.api_base} about {repo_key}.\n"
                f"          {exc}\n"
                "          Check the token and the network, then try again."
            ),
        )
    if not permitted.ok:
        return permitted

    verification = repos_mod.verify(
        ctx.config, repo_key, ctx.boundaries.version_control
    )
    return replace(verification, owner_verdict=permitted.owner_verdict)


def _record_onboard_outcome(
    ctx: Context, repo_key: str, cause: str, verification: repos_mod.Verification
) -> None:
    """Record a non-zero onboarding exit that is not a verification refusal.

    Separate from :func:`_refuse_onboarding` because these two happen *after* the approval
    screen was printed and therefore print nothing new — but they are still results, and
    Principle III's reconstruction standard makes "the author saw the settings and said no"
    exactly as worth recording as "the clone was in the wrong place" (FR-031).
    """
    ctx.audit.record(
        "repo.onboard",
        outcome="error",
        entity_type="repo",
        entity_id=repo_key,
        detail={
            "refused": True,
            "cause": cause,
            "clone_path": str(verification.path) if verification.path else None,
            "path_source": verification.path_source,
            "verified_origin": str(verification.identity) if verification.identity else None,
        },
    )


def _refuse_onboarding(
    ctx: Context, repo_key: str, verification: repos_mod.Verification
) -> Result:
    """Print a refusal, record it, and exit 3.

    **Every** refusal comes through here, including the ones that happen before any
    prompt. That is the point: before milestone 005 a refusal was printed and forgotten,
    which under Principle III's reconstruction standard means the log cannot answer what
    the system did (research R11, FR-031).
    """
    ctx.audit.record(
        "repo.onboard",
        outcome="error",
        entity_type="repo",
        entity_id=repo_key,
        detail={
            "refused": True,
            "cause": verification.cause,
            "clone_path": str(verification.path) if verification.path else None,
            "path_source": verification.path_source,
            "remote": verification.remote,
            # The normalised identity, never the raw URL it came from (FR-032).
            "found_origin": str(verification.identity) if verification.identity else None,
            "owner_verdict": verification.owner_verdict,
        },
    )
    return Result(
        code=EXIT_PRECONDITION,
        lines=(verification.refusal or "refusing: onboarding failed").splitlines(),
        data={
            "repo_key": repo_key,
            "refused": True,
            "cause": verification.cause,
            "clone_path": str(verification.path) if verification.path else None,
            "path_source": verification.path_source,
        },
    )


def _ask(prompt: str) -> str:
    """Put a question to the maintainer on **stderr**, and read their answer.

    ``input(prompt)`` writes its prompt to stdout, which is where a ``--json`` run's
    document lives — so the question would land inside the JSON and stop it parsing
    (FR-012). stderr is where a question belongs regardless: it is not the command's
    output, and an interactive terminal shows both streams anyway.

    Only ``onboard`` uses this. ``cancel``, ``purge_simulated`` and ``worktree_remove``
    each ask a self-contained question with nothing composed above it, so none of them has
    a screen to protect; they keep ``input`` and their stdout prompts (FR-014).
    """
    print(prompt, end="", file=sys.stderr, flush=True)
    return input()


def _fingerprint_diff_lines(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    """The re-approval screen's diff between the approved fingerprint and this one."""
    lines = ["fingerprint diff against the approved version:"]
    paths = sorted(set(previous) | set(current))
    if not paths:
        # Neither side fingerprinted a file — a repository with no committed
        # ``.claude/settings*.json`` at either the approved or the current base ref. Say
        # so: without this line the heading stands alone over blank space and reads as a
        # diff that failed to render rather than one with nothing in it (issue #25).
        lines.append("  no settings files on either side; nothing to compare")
    for path in paths:
        before = previous.get(path, "(absent)")
        after = current.get(path, "(absent)")
        marker = " " if before == after else "*"
        lines.append(f"  {marker} {path}")
        lines.append(f"      approved: {before}")
        lines.append(f"      current : {after}")
    return lines


def _numbering_lines(numbering: speckit.Numbering) -> list[str]:
    """The advisory block about feature numbering, or nothing (issue #41).

    Called only for a repository Spec Kit detection said yes to, and only for a numbering
    that is not the collision-free one — so an empty list means the caller asked about a
    repository that is fine, and the screen is byte-identical to what it was before this
    existed.

    **"Nothing here prevents that" is load-bearing.** Issue #41 established that no check
    this system could perform would catch the collision: the competing number exists only as
    untracked files in a sibling worktree, invisible to every git query. A warning that left
    the reader expecting the daemon to catch it would be worse than no warning.

    The two ``scanned`` wordings differ in exactly one sentence, because "change this
    setting" and "add this setting" are different instructions to the person who has to
    carry them out.
    """
    if numbering.safe:
        return []

    if numbering.kind == "unknown":
        return [
            "spec kit: the feature numbering could not be determined",
            f"  {speckit.INIT_OPTIONS}: {numbering.reason}.",
            '  If it does not say "timestamp", two sessions running at once can claim the',
            '  same feature number. Set "feature_numbering": "timestamp" to be sure.',
            "",
        ]

    if numbering.value is None:
        opening = [
            f"  feature_numbering is not set in {speckit.INIT_OPTIONS}, and scanning is",
            "  the default.",
        ]
    else:
        opening = [
            f'  feature_numbering is "{numbering.value}" in {speckit.INIT_OPTIONS}.',
        ]
    return [
        "spec kit: this repository numbers feature directories by scanning",
        *opening,
        "  Two sessions running at once scan the same specs/ and cannot see each other's",
        "  worktrees, so both can claim the same number. Nothing here prevents that.",
        '  Set "feature_numbering": "timestamp" in that file to number by time instead.',
        "",
    ]


def onboard(
    ctx: Context,
    repo_key: str,
    *,
    reapprove: bool = False,
    assume_yes: bool = False,
    confirm: Any = _ask,
    trust_file: Path | None = None,
    out: TextIO | None = None,
) -> Result:
    """The deliberate per-repository trust step (FR-001).

    Prints the primary clone path, the worker's trust status, and the **full contents** of
    any committed settings *as they exist at the base branch tip* — because that is what a
    dispatched session will honour (FR-004, M0 F9), not whatever is in a working tree.

    ``out`` is where the approval screen is written **before** the prompt blocks (011
    FR-001). ``None`` means *do not write it here*, which is both the pre-011 behaviour —
    the whole screen reaching the caller in ``lines`` and being printed after the answer,
    which is issue #17 — and what a ``--json`` run passes, because a machine-readable
    document must carry no human-readable text (FR-012). The CLI supplies the stream; a
    caller that wants to read the screen rather than watch it arrive leaves it unset.
    """
    result = Result()

    # Resolution and verification come first, and their refusals are recorded (FR-031).
    # Before milestone 005 this function returned ``EXIT_USAGE`` for a missing section
    # *before* opening any audit action, so a refusal was printed and forgotten — a live
    # Principle III violation this milestone inherits and fixes rather than introduces
    # (research R11).
    resolved = _resolve_for_onboarding(ctx, repo_key)
    if resolved.refusal is not None:
        return _refuse_onboarding(ctx, repo_key, resolved)

    clone_path = resolved.path
    assert clone_path is not None  # noqa: S101 - guaranteed by the refusal above
    # Resolved rather than defaulted (issue #150). The remote is handed over rather than
    # re-chosen: ``resolved.remote`` is the one the identity check settled on two lines up,
    # and detection asking a different remote than identity did would be two answers about
    # the same repository.
    base = repos_mod.base_ref(
        ctx.config,
        repo_key,
        ctx.boundaries.version_control,
        clone_path,
        remote=resolved.remote,
    )
    base_ref = base.ref
    trusted, explanation = dispatch.is_trusted(clone_path, trust_file=trust_file)
    committed = dispatch.read_committed_settings(ctx.boundaries, str(clone_path), base_ref)
    fingerprint = dispatch.compute_fingerprint(ctx.boundaries, str(clone_path), base_ref)
    existing = db.get_repo(ctx.conn, repo_key)

    # Issue #41. Detection gates the read: a stray ``init-options.json`` in a directory with
    # no Spec Kit in it says nothing, and ``.specify/`` is not a rare enough name to carry
    # meaning alone. Read from the working tree rather than the base ref, unlike the
    # committed settings above — those are read at the ref because a session honours the
    # *committed* file, whereas numbering is a property of the repository that nothing here
    # honours at all, and is what ``speckit.detect`` two lines up already answers from.
    is_speckit = speckit.detect(clone_path).detected
    numbering = speckit.numbering(clone_path) if is_speckit else None

    result.data = {
        "repo_key": repo_key,
        "clone_path": str(clone_path),
        "path_source": resolved.path_source,
        "verified_origin": str(resolved.identity) if resolved.identity else None,
        "remote": resolved.remote,
        "owner_verdict": resolved.owner_verdict,
        "base_ref": base_ref,
        # What decided it, in both the countable form and the readable one. Onboarding is
        # the moment the base ref stops being a guess, so the document that records the
        # approval has to say which rung answered.
        "base_ref_source": base.source,
        "base_ref_detail": base.detail,
        "trusted": trusted,
        "trust_explanation": explanation,
        "committed_settings": committed,
        "fingerprint": fingerprint,
        "previously_onboarded": existing is not None,
        "previous_fingerprint": existing.fingerprint if existing else None,
        "previous_clone_path": existing.clone_path if existing else None,
        # ``null`` numbering means *not asked*, not "no answer": the repository is not a
        # Spec Kit project, so nothing read the file.
        "speckit": is_speckit,
        "speckit_numbering": numbering.kind if numbering else None,
        "speckit_numbering_value": numbering.value if numbering else None,
    }

    # These three lines come **first**, ahead of trust and the committed settings, because
    # they answer *which repository is about to be trusted* — and that must be settled
    # before anything about trust is read (FR-011, contracts/onboarding.md).
    result.say(f"repository   : {repo_key}")
    result.say(
        f"clone path   : {clone_path}   "
        f"({repos_mod.describe_source(resolved.path_source, repo_key)})"
    )
    result.say(f"verified     : {resolved.verified_line()}")
    if reapprove and existing is not None and existing.clone_path:
        recorded = existing.clone_path
        marker = "" if recorded == str(clone_path) else "   ** CHANGED **"
        result.say(f"recorded path: {recorded}{marker}")
    result.say(f"base ref     : {base_ref}   ({base.detail})")
    result.say(f"trust        : {'accepted' if trusted else 'NOT ACCEPTED'} — {explanation}")
    result.say()

    if committed:
        result.say("committed tool-permission settings at the base ref:")
        result.say(
            "  These are applied to a dispatched session WITHOUT asking. Read them."
        )
        for path, text in committed.items():
            result.say()
            result.say(f"  --- {path} ---")
            for line in text.splitlines():
                result.say(f"  {line}")
        result.say()
    else:
        result.say("no committed .claude/settings*.json at the base ref")
        result.say()

    if reapprove and existing is not None:
        result.lines.extend(_fingerprint_diff_lines(existing.fingerprint, fingerprint))
        result.say()

    # Last on the screen, and that position is fixed by two constraints at once. It must
    # reach the maintainer before the prompt, which the flush point below guarantees; and it
    # must not push the committed permission settings further from the top, because that
    # text is the thing on this screen that most needs reading (milestone 011).
    if numbering is not None:
        result.lines.extend(_numbering_lines(numbering))

    # THE flush point, and the only one. Everything above is the approval screen —
    # *which repository, where, verified how, trusted or not, and what it will honour
    # without asking* — and everything below is the outcome of deciding about it. Writing
    # here is what makes the screen arrive before the question rather than after the
    # answer (FR-001, issue #17); the clearing inside ``flush_to`` is what keeps any of
    # the five exits below from printing it a second time (FR-006). Adding a second call
    # anywhere in this function reintroduces the doubling.
    result.flush_to(out)

    if existing is not None and not reapprove and existing.fingerprint == fingerprint:
        result.say("already onboarded and the fingerprint is unchanged; nothing to do")
        return result

    if assume_yes and committed and (existing is None or existing.fingerprint != fingerprint):
        # --yes refuses to skip when committed settings are present and unapproved.
        # Skipping the prompt is a convenience; skipping the *review* is the hazard.
        _record_onboard_outcome(ctx, repo_key, "unapproved_committed_settings", resolved)
        # The splice below is still live, and carries the screen exactly once either way:
        # with a stream, ``flush_to`` already wrote it and emptied ``lines``, so this adds
        # only the refusal; with ``out=None`` nothing has been written yet and the screen
        # rides along to the caller as it always did. The same reading applies to the
        # abort return further down (FR-006).
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                *result.lines,
                "refusing --yes: this repository has committed tool-permission settings "
                "that have not been approved. Review the contents above and confirm "
                "interactively.",
            ],
            data=result.data,
        )

    if not assume_yes:
        try:
            answer = confirm(
                f"Approve {repo_key} for dispatch, recording this fingerprint? [y/N] "
            )
        except KeyboardInterrupt:
            # Ctrl-C used to propagate to ``cli.main``, which printed "interrupted" and
            # exited 1 — before this function had opened any audit action, so the log held
            # no trace that onboarding was attempted. contracts/onboarding.md requires
            # every non-zero exit to be recorded and Principle III requires the log alone
            # to answer what was attempted. The gap was safe only while nobody reached
            # this prompt informed enough to walk away from it; now that the screen
            # arrives first, giving up here is the expected second answer (FR-011).
            #
            # The code and the message are deliberately today's. What changes is the
            # record, and only the record.
            _record_onboard_outcome(ctx, repo_key, "interrupted_at_prompt", resolved)
            return Result(
                code=EXIT_FAILED, lines=[*result.lines, "interrupted"], data=result.data
            )
        except EOFError:
            # ``onboard some/repo < /dev/null``. Nothing caught this either, so it was a
            # traceback rather than a result. An absent answer is not an approval, so it
            # exits like the decline it effectively is — with its own cause, because
            # "input ran out" and "I said no" are different things to find in a log.
            _record_onboard_outcome(ctx, repo_key, "no_answer_available", resolved)
            return Result(
                code=EXIT_CHECK_FAILED,
                lines=[
                    *result.lines,
                    "no answer available: input ended before the prompt was answered",
                ],
                data=result.data,
            )
        if str(answer).strip().lower() not in ("y", "yes"):
            # Exit 4, distinct from the 3 every refusal uses (contracts/onboarding.md):
            # "I decided not to" and "the system would not let me" are different results,
            # and a script that retries on one must not retry on the other.
            _record_onboard_outcome(ctx, repo_key, "aborted_at_prompt", resolved)
            return Result(
                code=EXIT_CHECK_FAILED, lines=[*result.lines, "aborted"], data=result.data
            )

    with (
        ctx.audit.action(
            "repo.onboard",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "clone_path": str(clone_path),
                "path_source": resolved.path_source,
                "remote": resolved.remote,
                "verified_origin": str(resolved.identity) if resolved.identity else None,
                "owner_verdict": resolved.owner_verdict,
                "base_ref": base_ref,
                "base_ref_source": base.source,
                "fingerprint": fingerprint,
                "trusted": trusted,
                "reapprove": reapprove,
                # What the maintainer was looking at when they said yes (issue #41). The
                # verdict but not the value: this record answers *what was approved*, and
                # the verdict is that.
                "speckit": is_speckit,
                "speckit_numbering": numbering.kind if numbering else None,
            },
        ),
        db.transaction(ctx.conn),
    ):
        db.upsert_repo(
            ctx.conn,
            repo_key=repo_key,
            settings_fingerprint=fingerprint or None,
            trust_verified=trusted,
            clone_path=str(clone_path),
            path_source=resolved.path_source,
            verified_origin=str(resolved.identity) if resolved.identity else None,
        )

    result.say(f"onboarded {repo_key}")
    if not trusted:
        result.say(
            "NOTE: the trust dialog has not been accepted for this clone. Dispatch will "
            "still refuse until it is — open the repository in the worker once."
        )
    return result


# -- repos ------------------------------------------------------------------


def repos(ctx: Context, *, trust_file: Path | None = None) -> Result:
    """Where "why is nothing happening for this repo" gets answered.

    Listed by **onboarding record**, not by configuration section (FR-017). A section for
    a repository that was never onboarded is not a repository this system watches, and
    listing it as one is how "why is nothing happening for this repo" got asked in the
    first place. Such a section is still reported — at the end, as *not onboarded* —
    because silence about a section the author wrote would be its own confusion.
    """
    result = Result()
    rows: list[list[str]] = []
    payload: list[dict[str, Any]] = []
    resolved = repos_mod.resolved_all(ctx.conn, ctx.config)

    for key in repos_mod.known(ctx.conn):
        record = db.get_repo(ctx.conn, key)
        repo = resolved.get(key)
        if repo is None or repo.path is None:
            # Onboarded before migration 005, with nothing to say where. The row is real
            # and the location is not, and saying so is the whole content of the line.
            #
            # The third cell is the **path source** column, so it says what to do rather
            # than what is unknown — everything after it depends on a clone path we do not
            # have, and four question marks would describe the problem without naming the
            # one command that fixes it. It shouts for the same reason the not-onboarded
            # row below does: both are rows the author has to act on.
            rows.append([key, "(never recorded)", "NEEDS REAPPROVE", "?", "?", "?", "?"])
            payload.append(
                {
                    "repo_key": key,
                    "path": None,
                    "path_source": None,
                    "onboarded": True,
                    "onboarded_at": record.onboarded_at if record else None,
                    "note": "onboarded before the clone location was recorded — "
                    f"run `robot-army onboard {key} --reapprove`",
                }
            )
            continue

        trusted, explanation = dispatch.is_trusted(repo.path, trust_file=trust_file)
        base_ref = repos_mod.base_ref(
            ctx.config, key, ctx.boundaries.version_control, repo.path
        ).ref
        try:
            current = dispatch.compute_fingerprint(ctx.boundaries, str(repo.path), base_ref)
        except (BoundaryError, OSError):
            # ``OSError`` is the clone that is no longer there: git is invoked with the
            # clone as its working directory, and a missing directory fails before git
            # runs at all. Listing a moved clone is exactly when this verb is being read,
            # so it must produce a row saying so rather than a traceback (FR-021).
            current = {}
        approved = record.fingerprint if record else {}
        fingerprint_state = "matches" if current == approved else "CHANGED"
        source = record.path_source if record else None

        speckit_cell, speckit_detail = _speckit_column(ctx, key, repo.path)
        rows.append(
            [
                key,
                str(repo.path),
                source or "?",
                "yes" if trusted else "NO",
                fingerprint_state,
                str(len(repo.post_create)),
                speckit_cell,
            ]
        )
        payload.append(
            {
                "repo_key": key,
                "path": str(repo.path),
                "path_source": source,
                "onboarded": True,
                "onboarded_at": record.onboarded_at if record else None,
                "verified_origin": record.verified_origin if record else None,
                "origin_verified_at": record.origin_verified_at if record else None,
                "trusted": trusted,
                "trust_explanation": explanation,
                "fingerprint_state": fingerprint_state,
                "approved_fingerprint": approved,
                "current_fingerprint": current,
                "post_create_steps": len(repo.post_create),
                "speckit": speckit_detail,
            }
        )

    unonboarded = sorted(set(ctx.config.repos) - set(repos_mod.known(ctx.conn)))
    for key in unonboarded:
        section = ctx.config.repos[key]
        where = str(section.path) if section.path else "(derived)"
        rows.append([key, where, "NOT ONBOARDED", "-", "-", "-", "-"])
        payload.append(
            {
                "repo_key": key,
                "path": str(section.path) if section.path else None,
                "path_source": None,
                "onboarded": False,
                "note": f"has a [repos.\"{key}\"] section but was never onboarded — "
                f"run `robot-army onboard {key}`",
            }
        )

    result.data = {"repos": payload}
    if not rows:
        return result.say("no repositories are onboarded — run `robot-army onboard owner/name`")
    for line in _table(
        rows,
        ["repo", "clone path", "path source", "trusted", "fingerprint", "steps", "spec-kit"],
    ):
        result.say(line)
    return result


# -- worktree ---------------------------------------------------------------


def cleanup_now(ctx: Context, item_id: int | None = None) -> Result:
    """Reclaim finished work's disk, now, under exactly the automatic pass's guards.

    Runs whether or not ``[cleanup] on_issue_close`` is enabled (FR-029) — the setting
    governs *when* cleanup happens, not whether it is possible — and calls the same
    function ``reconcile._cleanup_worktrees`` calls, so the manual path cannot drift from
    the automatic one.

    Naming an item is also the act of *reconsidering* it: ``retained`` and
    ``branch_retained`` are decisions the automatic pass will not revisit, and this is what
    revisits them.
    """
    result = Result()
    try:
        decisions = cleanup_mod.sweep(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            item_id=item_id,
        )
    except LookupError as exc:
        return Result(code=EXIT_FAILED, lines=[str(exc)])

    result.data = {
        "decisions": [
            {
                "item_id": d.item_id,
                "state": d.state,
                "reason": d.reason,
                "worktree_removed": d.worktree_removed,
                "branch_deleted": d.branch_deleted,
            }
            for d in decisions
        ],
        "considered": len(decisions),
        "on_issue_close": ctx.config.cleanup.on_issue_close,
    }
    if not decisions:
        result.say("nothing eligible for cleanup")
        return result
    for decision in decisions:
        result.say(f"item {decision.item_id}: {decision.state} — {decision.reason}")
    reclaimed = sum(1 for d in decisions if d.reclaimed)
    result.say()
    result.say(f"{reclaimed} of {len(decisions)} considered item(s) had their worktree removed")
    return result


def worktree_list(ctx: Context, *, include_simulated: bool = False) -> Result:
    result = Result()
    rows: list[list[str]] = []
    payload: list[dict[str, Any]] = []
    # Counted from the rows rather than from SQL: which work items appear here is decided
    # partly in Python — those carrying a ``worktree_path`` — and a query that re-stated
    # that predicate would put the same rule in two places. Nothing is inspected on disk
    # for a withheld row; only counted.
    withheld = (
        0
        if include_simulated
        else sum(
            1
            for item in db.list_work_items(ctx.conn, include_simulated=True)
            if item.worktree_path and item.dry_run
        )
    )
    for item in db.list_work_items(ctx.conn, include_simulated=include_simulated):
        if not item.worktree_path:
            continue
        repo = repos_mod.resolve(ctx.conn, ctx.config, item.repo_key)
        if repo is None:
            condition = None
        else:
            base_ref = repos_mod.base_ref(
                ctx.config, item.repo_key, ctx.boundaries.version_control, repo.path
            ).ref
            try:
                condition = worktree.condition(
                    ctx.boundaries.version_control,
                    str(repo.path),
                    item.worktree_path,
                    item.branch or "",
                    base_ref,
                )
            except BoundaryError:
                condition = None
        size = worktree.directory_size(item.worktree_path)
        rows.append(
            [
                str(item.id) + ("*" if item.dry_run else ""),
                item.worktree_path,
                item.branch or "—",
                condition.label if condition else "unknown",
                worktree.human_size(size),
                item.cleanup_state or "—",
            ]
        )
        payload.append(
            {
                "item_id": item.id,
                "path": item.worktree_path,
                "branch": item.branch,
                "condition": condition.label if condition else "unknown",
                "dirty": condition.dirty if condition else None,
                "commits_ahead": condition.commits_ahead if condition else None,
                "size_bytes": size,
                "simulated": item.dry_run,
                "cleanup_state": item.cleanup_state,
                "cleanup_reason": item.cleanup_reason,
                "cleaned_at": item.cleaned_at,
            }
        )
    result.data = {"worktrees": payload}
    if not rows:
        if withheld:
            return result.say(f"no worktrees visible ({_withheld_note(withheld)})")
        return result.say("no worktrees recorded")
    for line in _table(rows, ["item", "path", "branch", "condition", "size", "cleanup"]):
        result.say(line)
    if withheld:
        result.say()
        result.say(_withheld_note(withheld))
    return result


@dataclass(frozen=True, slots=True)
class _LiveSession:
    """One open session row, described for a human who is about to delete its worktree.

    ``liveness`` is a **word, not a boolean**, because there are four honest answers and
    three of them are not "alive". Folding "cannot tell" into either "running" or "gone"
    would be the exact mistake this guard exists to prevent, and it is the mistake that
    let a pid of ``1`` through the termination guard in #69.
    """

    session_id: str
    attempt: int
    state: str
    pid: int | None
    liveness: str
    socket: str | None

    @property
    def process(self) -> str:
        if self.liveness == "unrecorded":
            return "no process id recorded"
        if self.liveness == "unidentified":
            return f"pid {self.pid} recorded, with no start time to identify it by"
        if self.liveness == "running":
            # "alive", not "running": the sentence this lands in already says the
            # *session* is running, and the same word twice reads as one fact repeated
            # rather than two facts checked.
            return f"pid {self.pid} is alive"
        return f"pid {self.pid} is no longer there"


def _describe_live_session(session: Any) -> _LiveSession:
    """Build the description above from a session row, consulting ``/proc`` at most once.

    ``procinfo.is_alive`` is **never** called without a recorded ``proc_start``: it
    documents its own degradation to a bare existence check in that case, which would
    report any process holding a recycled pid as this session. Session rows legitimately
    carry a pid and no start time — the registration treats it as optional and nothing
    backfills it — so the degraded branch is reachable and must be refused rather than
    believed.

    Whatever this answers changes **nothing** about the decision. The row is what refuses;
    this is what the operator is told.
    """
    if session.pid is None:
        liveness = "unrecorded"
    elif not session.proc_start:
        liveness = "unidentified"
    elif procinfo.is_alive(session.pid, session.proc_start):
        liveness = "running"
    else:
        liveness = "gone"
    return _LiveSession(
        session_id=session.session_id,
        attempt=session.attempt,
        state=str(session.state),
        pid=session.pid,
        liveness=liveness,
        socket=session.host_socket,
    )


def _refuse_for_live_session(
    result: Result,
    *,
    item_id: int,
    worktree_path: str,
    open_count: int,
    described: _LiveSession,
) -> str:
    """Fill in the refusal and return its reason, which the caller also records.

    The message has a job beyond saying no: the operator asked for this disk back, and
    every route onward from here is theirs to choose. So it says which session, how it
    looks from ``/proc``, what removing it now would cost, how to go and look at the
    worker, and the two ways forward — rather than leaving them to find ``show``.
    """
    result.code = EXIT_PRECONDITION
    result.data["refused_by"] = "live_session"
    result.data["refused_reason"] = reason = (
        f"session {described.session_id} (attempt {described.attempt}) is "
        f"{described.state} — {described.process}"
    )
    result.data["worktree_removed"] = False
    result.data["branch_deleted"] = False
    result.say(f"refused to remove {worktree_path}:")
    result.say(f"  {reason}")
    if open_count > 1:
        result.say(f"  ({open_count - 1} other open session(s) for this item)")
    result.say("  A worker may still be writing in there. Removing it now leaves that")
    result.say("  worker running in a deleted directory, and this command deletes the")
    result.say("  branch too, so there would be nothing left to recover the work from.")
    if described.socket:
        result.say(f"  look:   dtach -a {described.socket}")
    result.say(f"  then:   robot-army cancel {item_id}  (stop it), or")
    result.say(f"          robot-army worktree remove {item_id} --force  (remove anyway)")
    return reason


def worktree_remove(
    ctx: Context, item_id: int, *, force: bool = False, confirm: Any = input
) -> Result:
    """Remove **both** the worktree and its branch (FR-016), under three guards.

    Removal is two steps, and doing only the first accumulates ``robot-army/*`` branches
    in every repository forever.

    Two guards run here, and they answer different questions:

    1. **Is anything still running in there?** Ours, asked of the session rows (issue #79).
       Until #79 this was not asked at all, and the automatic reclaim path — which *did*
       ask it, and records ``skipped`` — was the conservative one. That was the wrong way
       round: ``cleanup`` runs unattended, while this is what someone reaches for when the
       disk is full, and it is the one that can override git.
    2. **Does the tree hold uncommitted or merely untracked work?** Git's, taken as-is and
       never overridden by default. That refusal is the feature, not an obstacle.

    A third question — **is the branch contained?** — is deliberately not asked here. This
    command deletes the branch it made along with the worktree; containment is
    ``cleanup``'s guard, on the path that reclaims work it did not start.

    The first guard is deliberately blind to two things. It never reads the work item's **state**:
    the reported case was a ``done`` item, because an issue can close while its worker
    types on, and terminal is precisely the state disk gets reclaimed from. And it never
    decides on **process liveness**: a row whose process cannot be seen is still a row
    nothing has closed, and refusing only on a *confirmed* live process would remove the
    worktree in every case where liveness could not be established.

    See ``specs/20260901-164616-guard-worktree-remove/contracts/worktree-removal.md``.
    """
    result = Result()
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if not item.worktree_path:
        return Result(code=EXIT_FAILED, lines=[f"work item {item_id} has no worktree"])
    result.data = {"item_id": item_id}
    vcs = ctx.boundaries.version_control
    with ctx.audit.action(
        "worktree.remove",
        entity_type="work_item",
        entity_id=item_id,
        target=item.worktree_path,
        detail={"force": force},
        dry_run=bool(item.dry_run),
    ) as outcome:
        outcome["refused"] = False
        outcome["worktree_removed"] = False
        outcome["branch_deleted"] = False
        outcome["forced_over_live_session"] = False
        result.data["forced_over_live_session"] = False

        repo = repos_mod.resolve(ctx.conn, ctx.config, item.repo_key)
        if repo is None:
            outcome["refused"] = True
            outcome["refused_by"] = "unresolved_repo"
            return Result(
                code=EXIT_PRECONDITION,
                lines=[
                    f"repository {item.repo_key!r} does not resolve to a clone any more"
                ],
            )

        # -- the session guard: is anything still running in there? (#79) --
        #
        # Asked of the **session rows**, never of the work item's state and never of the
        # process table. The reported case was a ``done`` item, so a guard keyed on state
        # would have permitted it; and a row whose process cannot be seen is still a row
        # nothing has closed, so deciding on liveness would remove the worktree in every
        # case where liveness could not be established — the same bug, harder to
        # reproduce.
        live = cleanup_mod.live_sessions(ctx.conn, item_id)
        described = _describe_live_session(live[0]) if live else None
        if described is not None:
            outcome["live_session"] = asdict(described)
            result.data["live_session"] = asdict(described)
        if described is not None and not force:
            reason = _refuse_for_live_session(
                result,
                item_id=item_id,
                worktree_path=item.worktree_path,
                open_count=len(live),
                described=described,
            )
            outcome["refused"] = True
            outcome["refused_by"] = "live_session"
            outcome["reason"] = reason
            return result

        if force:
            # Unchanged, word for word, when nothing is running: the common case must not
            # silently acquire different wording.
            prompt = (
                f"Type the item id ({item_id}) to force-remove {item.worktree_path} "
                "and discard its uncommitted work: "
            )
            if described is not None:
                # No *second* prompt. The override already demands the typed id, and a
                # question asked twice is answered reflexively; what changes is that the
                # operator is told what they are about to do before they answer at all.
                prompt = (
                    f"session {described.session_id} is {described.state} in "
                    f"{item.worktree_path} — {described.process}. Forcing leaves that "
                    f"worker running in a deleted directory. "
                ) + prompt
            answer = confirm(prompt)
            if str(answer).strip() != str(item_id):
                outcome["aborted"] = True
                return Result(code=EXIT_FAILED, lines=["aborted"])
            if described is not None:
                # The single most destructive thing this command can do, and ``force:
                # true`` alone does not say so — it is equally what forcing past a merely
                # dirty tree looks like. Those are not the same act.
                outcome["forced_over_live_session"] = True
                result.data["forced_over_live_session"] = True

        removal = vcs.remove_worktree(
            item.worktree_path, force=force, clone_path=str(repo.path)
        )
        branch_deleted = False
        if removal.worktree_removed and item.branch:
            branch_deleted = vcs.delete_branch(str(repo.path), item.branch, force=force)

        outcome["worktree_removed"] = removal.worktree_removed
        outcome["branch_deleted"] = branch_deleted

        result.data.update(
            {
                "item_id": item_id,
                "worktree_removed": removal.worktree_removed,
                "branch_deleted": branch_deleted,
                "refused_reason": removal.refused_reason,
            }
        )

        if not removal.worktree_removed:
            outcome["refused"] = True
            outcome["refused_by"] = "git"
            outcome["reason"] = removal.refused_reason
            result.data["refused_by"] = "git"
            result.code = EXIT_FAILED
            result.say(f"refused to remove {item.worktree_path}:")
            result.say(f"  {removal.refused_reason}")
            result.say(
                "  Git refuses to remove a worktree with uncommitted or untracked changes. "
                "That refusal is the guard; --force overrides it."
            )
            return result

        result.say(f"removed worktree {item.worktree_path}")
        if item.branch and branch_deleted:
            result.say(f"deleted branch {item.branch}")
        elif item.branch:
            result.code = EXIT_FAILED
            result.say(
                f"WARNING: removed the worktree but branch {item.branch} still exists. "
                "Removal is two steps; skipping the second accumulates robot-army/* branches"
            )
        with db.transaction(ctx.conn):
            db.update_work_item_columns(ctx.conn, item_id, worktree_path=None)
        return result


def worktree_prune(ctx: Context) -> Result:
    """Clear git's record of worktrees whose directories are gone."""
    result = Result()
    outputs: dict[str, str] = {}
    for key, repo in sorted(repos_mod.resolved_all(ctx.conn, ctx.config).items()):
        try:
            outputs[key] = ctx.boundaries.version_control.prune_worktrees(str(repo.path))
        except BoundaryError as exc:
            outputs[key] = f"error: {exc}"
            result.code = EXIT_FAILED
    result.data = {"pruned": outputs}
    for key, output in outputs.items():
        result.say(f"{key}: {output.strip() or 'nothing to prune'}")
    return result


# -- lifecycle verbs --------------------------------------------------------


def cancel(ctx: Context, item_id: int, *, force: bool = False, confirm: Any = input) -> Result:
    """Stop that item's running session and **only** that session (FR-050)."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    session = db.latest_session_for_item(ctx.conn, item_id)
    if session is None or session.state not in (SessionState.STARTING, SessionState.RUNNING):
        return Result(
            code=EXIT_FAILED,
            lines=[f"work item {item_id} has no running session to cancel"],
        )
    if not force:
        answer = confirm(f"Stop session {session.session_id} for item {item_id}? [y/N] ")
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=["aborted"])

    # Which host owns this session is a property of the *record*, not of the configured
    # effect level (069 FR-011/FR-012). A row created under simulation stays simulated for
    # the whole of its life, and the level may well have moved since: dispatch at ``local``
    # leaves a row with ``pid = 0`` that no worker will ever close, then raising
    # ``effect_level`` and restarting — one line in config.toml, the ordinary go-live step —
    # makes ``session_host`` real while the row is not. Routing that row to the real host
    # reaches ``killpg(getpgid(0), …)``, and ``getpgid(0)`` answers about the *caller*: the
    # daemon's own process group, or the operator's shell when the CLI is asking.
    #
    # The discriminator is NOT ``dry_run``, and that distinction is load-bearing.
    # ``EffectLevel.is_simulated`` is "not live", so rows created at ``no-remote`` are
    # ``dry_run`` too — while ``REAL_AT["session_host"]`` includes ``NO_REMOTE``, so those
    # rows have a **real process** behind a real pid. Routing them here would return
    # ``confirmed=True`` without signalling anything and mark the item ``interrupted``
    # while the worker ran on, unvisited by any sweep: issue #34 again, silently, from the
    # opposite direction. ``dry_run`` means "this row is a dry-run record", which is not the
    # same fact as "this row's host was simulated".
    #
    # What identifies a simulated *host* is the signature it writes and nothing else can:
    # ``SimulatedSessionHost.confirm_session`` returns ``pid=0, proc_start=None``
    # deliberately, so that nothing can mistake it for a real process. A real session at any
    # level records a real pid and (normally) a real start time.
    #
    # This is the only place in the system that picks an implementation from stored state.
    # A test asserts that it stays the only one.
    hosted_by_simulation = (
        session.dry_run and session.pid == 0 and session.proc_start is None
    )
    host = (
        ctx.boundaries.simulated_session_host
        if hosted_by_simulation
        else ctx.boundaries.session_host
    )
    handle = HostHandle(
        socket_path=session.host_socket or "",
        argv=(),
        simulated=hosted_by_simulation,
        pid=session.pid,
    )
    result = Result()
    try:
        # ``proc_start`` is not optional here. Without it the liveness check degrades to a
        # bare "does /proc/<pid> exist", which is the PID-reuse bug wearing a different hat
        # (FR-038): a recycled pid reads as a live session, and gets signalled.
        outcome = host.terminate(
            handle, session.scope, expected_start=session.proc_start
        )
    except BoundaryError as exc:
        return Result(code=EXIT_FAILED, lines=[f"could not stop the session: {exc}"])

    result.data = {
        "item_id": item_id,
        "session_id": session.session_id,
        "scope": session.scope,
        "confirmed": outcome.confirmed,
        "method": outcome.method,
        "escalated": outcome.escalated,
    }

    if outcome.refused_reason is not None:
        # The boundary declined to act, which is a third thing — neither "it stopped" nor
        # "I signalled it and it survived". Falling through to the branch below would print
        # "pid N is still running after signalling the process group", and every clause of
        # that is false here: nothing was signalled, and whether the pid is running was
        # never the question. Telling the maintainer their machine had just been signalled
        # when it had not is the specific harm issue #69 is about (069 S-K3).
        #
        # The row is malformed, so the message hands over the row: the next step is to look
        # at it, not to try the cancel again.
        result.data["refused"] = True
        result.data["refused_reason"] = outcome.refused_reason
        return Result(
            code=EXIT_FAILED,
            data=result.data,
            lines=[
                f"refused to stop session {session.session_id}: {outcome.refused_reason}. "
                f"nothing was signalled. item {item_id} is unchanged; "
                f"inspect its session row with: robot-army show {item_id}",
            ],
        )

    if not outcome.confirmed:
        # Nothing is settled, and that is the point. An item marked `interrupted` while its
        # worker is still running is visited by no sweep the system has — reconciliation
        # walks only `active` items — so it would run unsupervised until the machine was
        # rebooted, which is exactly what issue #34 observed. Leaving it `active` keeps it
        # in front of the machinery that will notice.
        attach = " ".join(host.attach_command(handle))
        return Result(
            code=EXIT_FAILED,
            data=result.data,
            lines=[
                f"could not confirm session {session.session_id} stopped: "
                f"pid {session.pid} is still running after "
                f"{'the systemd scope stop and ' if session.scope else ''}"
                "signalling the process group. "
                f"item {item_id} is unchanged. attach with: {attach}",
            ],
        )

    # The worktree is left untouched either way: cancelling is about the process, not about
    # the work.
    #
    # Re-read before settling. The daemon drains the exit spool in its own process while
    # this runs, so a worker killed by our own SIGTERM can record its ending before we get
    # here; forcing the transition then raises IllegalTransition and reports a perfectly
    # successful cancel as a failure. `dispatch.py` asks the same question at the
    # equivalent moment, for the same reason (milestone 013, 014 research R5).
    #
    # **These are two different questions and this used to conflate them** (issue #138).
    # "The session already recorded its own ending" is the exit-record race above. "The item
    # is not `active`" is something else entirely: a `done`, `abandoned` or `failed` item
    # whose worker is still running, which is precisely what a maintainer reaches for this
    # command to stop. Treating the second as the first meant cancel killed the process,
    # confirmed it gone, and then reported that it "had already recorded its own ending" —
    # while leaving the row open, so the slot stayed held and the orphan anomaly stayed
    # raised. Every clause of that message was false.
    settled = db.get_session(ctx.conn, session.session_id)
    already_ended = settled is not None and settled.state in TERMINAL_SESSION_STATES
    current = db.get_work_item(ctx.conn, item_id)
    if already_ended or current is None:
        result.data["settled_by"] = "exit record"
        return result.say(
            f"session {session.session_id} is gone; it had already recorded its own ending, "
            f"so item {item_id} was left as the exit record settled it"
        )

    # An `active` item becomes `interrupted`: its work stopped mid-flight and the state has
    # to say so. Any other state is left exactly where it is — a `done` item is still done
    # once its finished worker is closed down, and `interrupted` is not a legal destination
    # from most of them in any case.
    moves_to_interrupted = current.state is WorkItemState.ACTIVE
    with db.transaction(ctx.conn):
        transition_session(
            ctx.conn,
            ctx.audit,
            session_row_id=session.id,
            target=SessionState.LOST,
            reason=f"stopped by cancel ({outcome.method}); process confirmed gone",
        )
        if moves_to_interrupted:
            transition_work_item(
                ctx.conn,
                ctx.audit,
                item_id=item_id,
                target=WorkItemState.INTERRUPTED,
                reason=f"cancelled by the maintainer (session {session.session_id})",
            )

    result.data["item_state"] = str(
        WorkItemState.INTERRUPTED if moves_to_interrupted else current.state
    )
    tail = (
        f"item {item_id} is now interrupted and its worktree is untouched"
        if moves_to_interrupted
        else f"item {item_id} is left {current.state} and its worktree is untouched"
    )
    if outcome.method == "already_gone":
        return result.say(
            f"session {session.session_id} had already ended: nothing left to stop; {tail}"
        )
    if outcome.escalated:
        # The sentence that says this build caught the bug. Stating only "stopped via the
        # process group" would hide the fact that the scope claimed to have done it.
        return result.say(
            f"systemd scope {session.scope} reported success but the session was still "
            f"running; stopped session {session.session_id} by signalling the process "
            f"group; confirmed gone. {tail}"
        )
    # Name the path that actually did it. The scope is named only when there is one — a
    # method of ``systemd_scope`` with no recorded scope is a contradiction no real host
    # produces, and printing a scope of ``None`` would be its own small lie. A simulated
    # stop says so rather than borrowing the wording of a mechanism it never used.
    via = {
        "systemd_scope": f"systemd scope {session.scope}" if session.scope else "the systemd scope",
        "process_group_signal": "the process group",
        "simulated": "a simulated stop",
    }.get(outcome.method, outcome.method)
    return result.say(
        f"stopped session {session.session_id} via {via}; confirmed gone. {tail}"
    )


def _refusal_result(action: str, item_id: int, exc: dispatch.DispatchRefused) -> Result:
    """A launch the gate would not permit, rendered for the terminal (issue #120).

    ``EXIT_PRECONDITION``, never ``EXIT_FAILED``, and the difference is what the author
    acts on. ``EXIT_FAILED`` means the launch was attempted and the item now carries a
    failure reason to go and read; this means nothing was attempted and the item is
    untouched, so the reason on the screen is the whole story and there is nothing to
    repair. Sending the author to ``robot-army show`` here would send them to an empty
    field.

    Two lines: this surface's summary of *which* condition it was, then ``HoldReason``'s
    own detail string, unmodified. The second line is the same sentence ``/queue`` and
    ``robot-army status`` render for the same condition — one language across three
    surfaces (FR-008), because it is one string from one function.
    """
    return Result(
        code=EXIT_PRECONDITION,
        lines=[f"refusing to {action} item {item_id}: {_refusal_summary(exc)}", f"  {exc.detail}"],
        data={
            "item_id": item_id,
            "ok": False,
            "refused": True,
            "hold": str(exc.hold) if exc.hold is not None else None,
            "detail": exc.detail,
        },
    )


def _refusal_summary(exc: dispatch.DispatchRefused) -> str:
    """The short half of a refusal: which condition, in a phrase rather than an enum name.

    A claim failure has no ``HoldReason`` — no queueing word describes "somebody else has
    this item" — and the summary stays neutral about *why* the claim failed, because the
    detail line beneath it says which of the two happened: another dispatcher took it, or
    it was never in a state a session can start from. Asserting a race in the summary would
    contradict the detail every time the second case applied.
    """
    summaries = {
        ordering_mod.HoldReason.PAUSED: "dispatch is paused",
        ordering_mod.HoldReason.HELD: "held",
        ordering_mod.HoldReason.CAPACITY_UNOBSERVABLE: (
            "the number of running sessions could not be determined"
        ),
        ordering_mod.HoldReason.GLOBAL_CAP: "the machine is at its session limit",
        ordering_mod.HoldReason.REPO_CAP: "the repository is at its session limit",
    }
    if exc.hold is None:
        return "the item could not be claimed"
    return summaries.get(exc.hold, str(exc.hold))


def resume(
    ctx: Context,
    item_id: int,
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    force: bool = False,
    surface: str = "cli",
) -> Result:
    """Start a new session restoring the previous session's context (FR-047).

    **Never happens automatically** (FR-046). Resume is always a decision the maintainer
    makes with the FR-048 signals in front of them.

    Subject to the session cap, the dispatch pause and item/repo holds since issue #120 —
    ``dispatch_item`` applies them now, so this is not a check to keep in step, it is a
    refusal to render. ``force`` goes past the author's own policy and nothing else.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state not in (WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"work item {item_id} is {item.state}; resume requires "
                "'interrupted' or 'awaiting_review'"
            ],
        )
    previous = db.latest_session_for_item(ctx.conn, item_id)
    if previous is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} has no previous session to resume"],
        )

    try:
        ok = dispatch.dispatch_item(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            layout=ctx.layout,
            item_id=item_id,
            registry_dir=registry_dir,
            proc_root=proc_root,
            resume_session_id=previous.session_id,
            force=force,
            surface=surface,
        )
    except dispatch.DispatchRefused as exc:
        return _refusal_result("resume", item_id, exc)
    code = EXIT_OK if ok else EXIT_FAILED
    return Result(
        code=code,
        lines=[
            f"resumed item {item_id} from session {previous.session_id}"
            if ok
            else f"resume of item {item_id} failed; see `robot-army show {item_id}`"
        ],
        data={
            "item_id": item_id,
            "resumed_from": previous.session_id,
            "ok": ok,
            # The flag the author gave, **not** an assertion that anything was overridden.
            # Only the gate knows whether any condition actually applied, and it records
            # that in ``dispatch.forced`` — which is written only when one did.
            "force": force,
        },
    )


def restart(
    ctx: Context,
    item_id: int,
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
    force: bool = False,
    surface: str = "cli",
) -> Result:
    """A fresh session in the existing worktree, with no prior context.

    Subject to the same gate as ``resume`` (issue #120): starting an agent is starting an
    agent, and a cap that one of the two honoured would be no cap at all.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state not in (WorkItemState.INTERRUPTED, WorkItemState.AWAITING_REVIEW):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} is {item.state}; restart requires a rested item"],
        )
    try:
        ok = dispatch.dispatch_item(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            layout=ctx.layout,
            item_id=item_id,
            registry_dir=registry_dir,
            proc_root=proc_root,
            force=force,
            surface=surface,
        )
    except dispatch.DispatchRefused as exc:
        return _refusal_result("restart", item_id, exc)
    return Result(
        code=EXIT_OK if ok else EXIT_FAILED,
        lines=[
            f"restarted item {item_id}" if ok else f"restart of item {item_id} failed"
        ],
        data={"item_id": item_id, "ok": ok, "force": force},
    )


def abandon(
    ctx: Context,
    item_id: int,
    *,
    registry_dir: Path | None = None,
    proc_root: Path | None = None,
) -> Result:
    """Mark the item abandoned. Deliberately **not** destructive — the worktree stays."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    session = db.latest_session_for_item(ctx.conn, item_id)
    try:
        with db.transaction(ctx.conn):
            transition_work_item(
                ctx.conn,
                ctx.audit,
                item_id=item_id,
                target=WorkItemState.ABANDONED,
                reason="abandoned by the maintainer",
            )
            # An abandoned item is finished, so any session row still open under it is
            # holding a capacity slot for work that will never resume (#28). ``cancel``
            # closes its own row because it can confirm the process is gone first (#34);
            # ``abandon`` stops nothing, so it asks the rule instead — and a worker that
            # turns out to be alive is reported rather than closed, which here is the
            # likelier case, not the exotic one. The item moves first so the rule sees the
            # state that makes the row stale.
            if session is not None:
                reconcile.reclaim_stale_session(
                    ctx.conn,
                    ctx.audit,
                    session=session,
                    scan=sessions.scan(registry_dir=registry_dir, proc_root=proc_root),
                    proc_root=proc_root,
                    reason="the work item was abandoned",
                )
    except Exception as exc:  # noqa: BLE001 - an illegal transition is a usage error here
        return Result(code=EXIT_FAILED, lines=[str(exc)])
    # FR-029: a card must not sit in the in-progress list claiming to be busy when nothing
    # is. Best-effort — the item is abandoned either way, and a board that cannot be
    # written must not turn a successful abandon into a failure.
    try:
        intake.on_work_abandoned(
            ctx.conn,
            boundaries=ctx.boundaries,
            audit=ctx.audit,
            config=ctx.config,
            repo_key=item.repo_key,
            issue_number=item.issue_number,
            reason="the work item was abandoned",
            dry_run=bool(item.dry_run),
        )
    except Exception as exc:  # noqa: BLE001 - the item is abandoned; the board is cosmetic
        ctx.audit.error(
            "trello.card.move",
            error=exc,
            entity_type="work_item",
            entity_id=item_id,
            detail={"stage": "returning the card to its origin list"},
        )
    return Result(
        lines=[
            f"item {item_id} abandoned. Its worktree at "
            f"{item.worktree_path or '(none)'} was left in place — "
            f"`robot-army worktree remove {item_id}` removes it"
        ],
        data={"item_id": item_id},
    )


def retry(ctx: Context, item_id: int, *, trust_file: Path | None = None) -> Result:
    """Move a ``failed`` item back to ``ready``, refusing if anything still blocks it.

    Six checks, in the order of contracts/retry.md. The first four are local and free; only
    once they pass is the issue re-read from its source and put back through
    ``poll.evaluate``.

    That last step is the point of this function (issue #119, RA-01). ``check_gates`` takes
    a ``RepoConfig``, not a work item, so it cannot see an issue and never checked its
    author — which meant an item the poller had refused as *written by somebody else* came
    back to the queue on one press of a button whose confirmation promised the opposite.
    The author check is the control that stops "anyone may open an issue on a public
    repository" becoming "anyone may run an agent in the maintainer's checkout", and this
    was the one path around it.

    ``poll.evaluate`` is **called**, not reimplemented, and nothing here reads the stored
    ``blocked_reason`` to decide anything (research R1, R2). Matching the author check's
    wording in a stored message would put a security boundary at the mercy of whoever next
    edits an f-string, and would be wrong in both directions: an item whose configured
    author has since changed would be refused for ever, and an item that reached ``failed``
    for an unrelated reason after the old bug had already queued it would sail through.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    if item.state is not WorkItemState.FAILED:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"work item {item_id} is {item.state}; retry applies to failed items"],
        )
    source_id = f"{item.repo_key}#{item.issue_number}"
    repo = repos_mod.resolve(ctx.conn, ctx.config, item.repo_key)
    if repo is None:
        reason = (
            f"repository {item.repo_key!r} does not resolve to a clone any more — "
            f"run `robot-army onboard {item.repo_key} --reapprove`"
        )
        ctx.audit.record(
            "retry.blocked",
            outcome="error",
            entity_type="work_item",
            entity_id=item_id,
            target=source_id,
            detail={"repo_key": item.repo_key, "blocked": reason},
            dry_run=item.dry_run,
        )
        return Result(code=EXIT_PRECONDITION, lines=[reason])
    try:
        dispatch.check_gates(
            ctx.conn,
            boundaries=ctx.boundaries,
            config=ctx.config,
            repo=repo,
            trust_file=trust_file,
        )
    except dispatch.DispatchBlocked as exc:
        # Refused before the read, so no rate limit is spent on an item that could not
        # dispatch whatever the issue says (research R4). ``retry.blocked`` rather than
        # ``retry.evaluate`` is how the log distinguishes "we never asked GitHub" from
        # "we asked".
        ctx.audit.record(
            "retry.blocked",
            outcome="error",
            entity_type="work_item",
            entity_id=item_id,
            target=source_id,
            detail={"repo_key": item.repo_key, "blocked": str(exc)},
            dry_run=item.dry_run,
        )
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"refusing to retry item {item_id}: the blocking condition still holds.",
                f"  {exc}",
            ],
            data={"item_id": item_id, "blocked": str(exc)},
        )

    # -- the read (FR-001) -------------------------------------------------
    #
    # Never a fallback to ``item.title`` / ``item.body`` on failure. The stored copy is
    # precisely what cannot be trusted here, so falling back to it would be the original
    # defect with a network hiccup as its trigger — and it would be the failure mode
    # hardest to notice, because it looks exactly like success (FR-006, research R3).
    try:
        issue = ctx.boundaries.issue_reader.get_issue(item.repo_key, item.issue_number)
    except BoundaryError as exc:
        return _retry_unread(
            ctx, item, cause="issue_unreachable",
            message=f"could not read {source_id}: {exc}", error=str(exc),
        )
    if issue is None:
        return _retry_unread(
            ctx, item, cause="issue_absent",
            message=f"{source_id} does not exist, or this token cannot see it", error=None,
        )

    # -- the refresh (FR-009) ----------------------------------------------
    #
    # Before the verdict is consulted, and in its own transaction, so both outcomes get it
    # from one place rather than two call sites kept in step by hand. The order is chosen
    # for interruption (research R5): killed between this and the transition below, the
    # item is still ``failed`` with accurate content and its old reason, which the next
    # retry corrects completely. The other order would leave an item *in the queue*
    # carrying content nobody re-read, which is the thing this function exists to prevent.
    #
    # One dict rather than a call plus a hand-written list of what it wrote: the record's
    # ``refreshed`` field has to name exactly the columns this rewrote, and two copies would
    # drift the first time a fifth column joined. It is the set *written*, not a diff
    # against what was there — every one of these is taken from the read whether or not its
    # value moved, and "which of them differed" is a question the log cannot answer anyway
    # without also carrying the old values.
    refresh: dict[str, Any] = {
        "title": issue.title,
        "body": issue.body,
        "labels": dumps_labels(list(issue.labels)),
        "author": issue.author,
    }
    with db.transaction(ctx.conn):
        db.update_work_item_columns(ctx.conn, item_id, **refresh)

    # -- the verdict (FR-002) ----------------------------------------------
    #
    # ``onboarded=True`` is established, not assumed: ``check_gates`` above raises unless
    # ``db.get_repo`` returns a row, which is the same question ``poll`` asks.
    verdict = poll.evaluate(
        issue, config=ctx.config, repo_key=item.repo_key, onboarded=True
    )
    ctx.audit.record(
        "retry.evaluate",
        outcome="ok" if verdict.eligible else "error",
        entity_type="work_item",
        entity_id=item_id,
        target=source_id,
        detail={
            "repo_key": item.repo_key,
            "issue_number": item.issue_number,
            "eligible": verdict.eligible,
            "reason": verdict.reason,
            "author": issue.author,
            "refreshed": list(refresh),
        },
        dry_run=item.dry_run,
    )
    if not verdict.eligible:
        # The reason is written to the row as well as returned, so the queue describes why
        # the item is blocked *now* rather than why it was blocked before (FR-005).
        #
        # **Both** columns, which is not belt-and-braces. ``/queue`` renders
        # ``failure_reason or blocked_reason`` (``pages.web.pages._queue``), so writing only
        # the second leaves the page showing the *old* sentence beside a button that has
        # just refused for a different one — precisely the "the interface says something
        # other than what happened" failure this whole change exists to remove. ``_settle``
        # writes the pair for the same reason when the poller rejects an item, and these two
        # are the only writers of an eligibility verdict; they must not disagree.
        with db.transaction(ctx.conn):
            db.update_work_item_columns(
                ctx.conn,
                item_id,
                blocked_reason=verdict.reason,
                failure_reason=verdict.reason,
            )
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"refusing to retry item {item_id}: the issue is not eligible.",
                f"  {verdict.reason}",
            ],
            data={"item_id": item_id, "eligible": False, "reason": verdict.reason},
        )

    with db.transaction(ctx.conn):
        transition_work_item(
            ctx.conn,
            ctx.audit,
            item_id=item_id,
            target=WorkItemState.READY,
            reason="retried by the maintainer; the issue was re-read and re-evaluated",
            extra_columns={"failure_reason": None, "blocked_reason": None},
        )
    return Result(lines=[f"item {item_id} is ready again"], data={"item_id": item_id})


def _retry_unread(
    ctx: Context, item: WorkItem, *, cause: str, message: str, error: str | None
) -> Result:
    """A retry that could not read its issue. Refuses, and says which way it failed.

    "It did not happen" and "I could not ask" are different facts, and conflating them is
    the silent failure Principle III forbids — so the two causes are separate values rather
    than one "could not verify".
    """
    detail: dict[str, Any] = {
        "repo_key": item.repo_key,
        "issue_number": item.issue_number,
        "cause": cause,
    }
    if error is not None:
        detail["error"] = error
    ctx.audit.record(
        "retry.evaluate",
        outcome="error",
        entity_type="work_item",
        entity_id=item.id,
        target=f"{item.repo_key}#{item.issue_number}",
        detail=detail,
        dry_run=item.dry_run,
    )
    return Result(
        code=EXIT_FAILED,
        lines=[f"refusing to retry item {item.id}: {message}"],
        data={"item_id": item.id, "cause": cause, "error": error},
    )


# -- dispatch pause (milestone 002) -----------------------------------------


def _set_pause(ctx: Context, *, paused: bool, by: str) -> Result:
    """The body both verbs share. One transaction, one audit pair, no branching outside."""
    before = db.get_dispatch_control(ctx.conn)
    action = "dispatch.pause" if paused else "dispatch.unpause"
    with (
        ctx.audit.action(
            action,
            entity_type="dispatch_control",
            entity_id=1,
            detail={"by": by, "was_paused": before.paused},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        after = db.set_dispatch_paused(ctx.conn, paused=paused, by=by)
        outcome["paused"] = after.paused
        outcome["paused_at"] = after.paused_at
        outcome["paused_by"] = after.paused_by
        outcome["redundant"] = before.paused == paused

    result = Result(
        data={
            "paused": after.paused,
            "paused_at": after.paused_at,
            "paused_by": after.paused_by,
            "redundant": before.paused == paused,
        }
    )
    if before.paused == paused:
        # A redundant pause is a reported no-op, not an error (FR-033). Pausing twice is
        # not a mistake, and the *existing* pause with its original timestamp is the
        # useful answer.
        result.say(
            f"dispatch was already {'paused' if paused else 'running'}"
            + (
                f" — paused at {timefmt.local(after.paused_at)} by {after.paused_by}"
                if after.paused
                else ""
            )
        )
        return result
    if paused:
        result.say(f"dispatch paused at {timefmt.local(after.paused_at)} by {by}")
        result.say(
            "The daemon keeps polling, evaluating eligibility, reconciling, and "
            "heartbeating. It starts no new session; eligible items accumulate in ready."
        )
        result.say("This survives a daemon restart and a reboot. `robot-army unpause` clears it.")
    else:
        result.say("dispatch resumed; held items dispatch on the next tick")
    return result


def pause_dispatch(ctx: Context, *, by: str = "cli") -> Result:
    """Suspend dispatch durably (FR-033, FR-035).

    Works whether or not the daemon is running: it writes to the database, which the
    daemon reads before each dispatch decision. Pausing a stopped daemon is meaningful —
    it takes effect when it starts.
    """
    return _set_pause(ctx, paused=True, by=by)


def unpause_dispatch(ctx: Context, *, by: str = "cli") -> Result:
    return _set_pause(ctx, paused=False, by=by)


# -- dispatch holds (issue #117) --------------------------------------------
#
# The sibling of pause, and shaped like it deliberately: one transaction, one audit pair,
# and a redundant request reported rather than refused. What differs is scope — a pause
# stops the queue, a hold stops named work and leaves every other repository moving.


def hold_item(ctx: Context, item_id: int, *, by: str = "cli") -> Result:
    """Take one work item out of dispatch until it is released (FR-001, FR-009).

    Works whether or not the daemon is running, for the reason ``pause_dispatch`` does: it
    writes to the database, which the daemon reads before each dispatch decision, so
    holding against a stopped daemon takes effect when it starts (FR-022).

    **This never touches a session that is already running** (FR-010). A hold governs entry
    into dispatch; ``cancel`` is what stops a session, and the message says so rather than
    letting the author infer otherwise from silence.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return _no_such_item(ctx, item_id, action="hold.item")

    before = db.list_item_holds(ctx.conn).get(item_id)
    with (
        ctx.audit.action(
            "hold.item",
            entity_type="work_item",
            entity_id=item_id,
            detail={"by": by, "was_held": before is not None},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        hold, placed = db.set_item_hold(ctx.conn, item_id, by=by)
        outcome.update({"held_at": hold.held_at, "held_by": hold.held_by, "placed": placed})

    result = Result(
        data={
            "item_id": item_id,
            "held": True,
            "held_at": hold.held_at,
            "held_by": hold.held_by,
            "redundant": not placed,
        }
    )
    if not placed:
        # FR-004: a redundant hold is a reported no-op, and the hold *already in force* —
        # with its original timestamp — is the useful answer. ``set_dispatch_paused`` makes
        # the same judgement for the same reason.
        result.say(
            f"work item {item_id} was already held, since "
            f"{timefmt.local(hold.held_at)} by {hold.held_by}"
        )
        return result
    result.say(
        f"work item {item_id} (#{item.issue_number} {item.repo_key}) held at "
        f"{timefmt.local(hold.held_at)} by {by}"
    )
    result.say(
        "It stays in the queue in the position it would occupy anyway, and dispatches "
        "nothing until released. Every other item is unaffected."
    )
    result.say("Release it with `robot-army unhold " + str(item_id) + "`.")
    return result


def unhold_item(ctx: Context, item_id: int, *, by: str = "cli") -> Result:
    """Release one item's hold (FR-003). Releasing what is not held is a no-op (FR-005)."""
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return _no_such_item(ctx, item_id, action="unhold.item")

    with (
        ctx.audit.action(
            "unhold.item",
            entity_type="work_item",
            entity_id=item_id,
            detail={"by": by},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        removed = db.clear_item_hold(ctx.conn, item_id)
        outcome.update(
            {
                "released": removed is not None,
                "was_held_at": removed.held_at if removed else None,
            }
        )

    result = Result(data={"item_id": item_id, "held": False, "released": removed is not None})
    if removed is None:
        # Not a failure. "I already released that" and "that was never held" are the same
        # outcome to the author, and neither deserves a non-zero exit.
        result.say(f"work item {item_id} was not held; nothing to release")
        return result
    result.say(
        f"work item {item_id} released; it was held since {timefmt.local(removed.held_at)} "
        f"by {removed.held_by}"
    )
    return result


def hold_repo(ctx: Context, repo_key: str, *, by: str = "cli") -> Result:
    """Take every work item in one repository out of dispatch (FR-002).

    Present **and future** (FR-012): the hold is a fact about the repository rather than
    about any item, so an issue discovered tomorrow is held on arrival with nothing to
    backfill.

    The key is checked against ``repos.known`` rather than ``config.repos``, because a
    ``[repos.*]`` section for a repository that was never onboarded describes one the
    system does not watch — and a hold on such a repository would hold nothing while
    looking exactly like a hold that works.
    """
    if repo_key not in repos_mod.known(ctx.conn):
        return _no_such_repo(ctx, repo_key, action="hold.repo")

    before = db.list_repo_holds(ctx.conn).get(repo_key)
    with (
        ctx.audit.action(
            "hold.repo",
            entity_type="repo",
            entity_id=repo_key,
            detail={"by": by, "was_held": before is not None},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        hold, placed = db.set_repo_hold(ctx.conn, repo_key, by=by)
        outcome.update({"held_at": hold.held_at, "held_by": hold.held_by, "placed": placed})

    queued = _queued_in(ctx, repo_key)
    result = Result(
        data={
            "repo_key": repo_key,
            "held": True,
            "held_at": hold.held_at,
            "held_by": hold.held_by,
            "redundant": not placed,
            "queued_items": queued,
        }
    )
    if not placed:
        result.say(
            f"repository {repo_key} was already held, since "
            f"{timefmt.local(hold.held_at)} by {hold.held_by}"
        )
        return result
    result.say(
        f"repository {repo_key} held at {timefmt.local(hold.held_at)} by {by} "
        f"({queued} item{'' if queued == 1 else 's'} currently queued)"
    )
    result.say(
        "Work discovered in it later is held on arrival. Every other repository "
        "dispatches normally in the same pass."
    )
    result.say(f"Release it with `robot-army unhold --repo {repo_key}`.")
    return result


def unhold_repo(ctx: Context, repo_key: str, *, by: str = "cli") -> Result:
    """Release one repository's hold (FR-003)."""
    if repo_key not in repos_mod.known(ctx.conn):
        return _no_such_repo(ctx, repo_key, action="unhold.repo")

    with (
        ctx.audit.action(
            "unhold.repo",
            entity_type="repo",
            entity_id=repo_key,
            detail={"by": by},
        ) as outcome,
        db.transaction(ctx.conn),
    ):
        removed = db.clear_repo_hold(ctx.conn, repo_key)
        outcome.update(
            {
                "released": removed is not None,
                "was_held_at": removed.held_at if removed else None,
            }
        )

    result = Result(data={"repo_key": repo_key, "held": False, "released": removed is not None})
    if removed is None:
        result.say(f"repository {repo_key} was not held; nothing to release")
        return result
    result.say(
        f"repository {repo_key} released; it was held since "
        f"{timefmt.local(removed.held_at)} by {removed.held_by}"
    )
    return result


def _no_such_item(ctx: Context, item_id: int, *, action: str) -> Result:
    """Refuse, and record the refusal.

    An attempt that leaves no record is one nobody would ever notice, so the audit pair
    wraps the refusal rather than only the success (Principle III).
    """
    ctx.audit.record(
        action,
        outcome="error",
        entity_type="work_item",
        entity_id=item_id,
        detail={"refused": True, "cause": "no such work item"},
    )
    return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])


def _no_such_repo(ctx: Context, repo_key: str, *, action: str) -> Result:
    ctx.audit.record(
        action,
        outcome="error",
        entity_type="repo",
        entity_id=repo_key,
        detail={"refused": True, "cause": "repository not onboarded"},
    )
    # Action-agnostic, because both `hold` and `unhold` land here: "holding it would hold
    # nothing" reads as a non-sequitur on the release path, where the author is trying to
    # undo something rather than do it. What is wrong is the same either way — this is not
    # a repository the system watches — so that is what it says.
    return Result(
        code=EXIT_FAILED,
        lines=[
            f"repository {repo_key!r} is not onboarded, so it holds no work and can hold "
            f"none — run `robot-army repos` to see which repositories this system watches"
        ],
    )


def _queued_in(ctx: Context, repo_key: str) -> int:
    """How many of a repository's items are currently eligible. Reported when holding it,
    because "held, and it was holding nothing" and "held, and it stopped four items" are
    very different facts to the author."""
    row = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM work_items WHERE repo_key = ? AND state = ?",
        (repo_key, str(WorkItemState.READY)),
    ).fetchone()
    return int(row["n"])


def list_holds(ctx: Context) -> Result:
    """Every hold in force, item and repository (FR-018, FR-020).

    A separate verb rather than a section of ``status`` because it must show two things
    ``status``'s item-oriented sections structurally cannot:

    * **a repository hold matching no queued item** — the exact shape of a hold set and
      forgotten, which would otherwise be diagnosed as "polling is broken";
    * **a hold on an item that is no longer eligible** — held then finished, or held then
      abandoned. Those are left in place deliberately (nothing sweeps a hold, because
      clearing on a state transition is expiry under another name), and showing the item's
      state is what makes them visible rather than mysterious.

    With nothing held it says so in words. An empty table is not an answer to "what is
    held" — it is the same output a broken query would produce.
    """
    item_holds = db.list_item_holds(ctx.conn)
    repo_holds = db.list_repo_holds(ctx.conn)

    items: list[dict[str, Any]] = []
    for item_id, hold in sorted(item_holds.items()):
        item = db.get_work_item(ctx.conn, item_id)
        items.append(
            {
                "item_id": item_id,
                "repo_key": item.repo_key if item else None,
                "issue_number": item.issue_number if item else None,
                "state": str(item.state) if item else None,
                "held_at": hold.held_at,
                "held_by": hold.held_by,
                "age_seconds": health._age(hold.held_at),
            }
        )

    repositories: list[dict[str, Any]] = []
    for repo_key, hold in sorted(repo_holds.items()):
        repositories.append(
            {
                "repo_key": repo_key,
                "held_at": hold.held_at,
                "held_by": hold.held_by,
                "age_seconds": health._age(hold.held_at),
                "queued_items": _queued_in(ctx, repo_key),
            }
        )

    result = Result(data={"items": items, "repositories": repositories})
    if not items and not repositories:
        result.say("nothing is held")
        return result

    if items:
        result.say(f"held items ({len(items)}):")
        rows = [
            [
                str(row["item_id"]),
                f"#{row['issue_number']}" if row["issue_number"] else "-",
                row["repo_key"] or "-",
                row["state"] or "-",
                timefmt.local(row["held_at"]) or "-",
                row["held_by"],
                _held_for(row["age_seconds"]),
            ]
            for row in items
        ]
        for line in _table(
            rows, ["item", "issue", "repo", "state", "held at", "by", "for"]
        ):
            result.say("  " + line)
        result.say()

    if repositories:
        result.say(f"held repositories ({len(repositories)}):")
        rows = [
            [
                row["repo_key"],
                timefmt.local(row["held_at"]) or "-",
                row["held_by"],
                _held_for(row["age_seconds"]),
                str(row["queued_items"]),
            ]
            for row in repositories
        ]
        for line in _table(rows, ["repo", "held at", "by", "for", "queued"]):
            result.say("  " + line)
        result.say()

    result.say("Release with `robot-army unhold <item>` or `robot-army unhold --repo <key>`.")
    return result


def _held_for(age_seconds: int | None) -> str:
    """How long a hold has been in force, coarsely (FR-020).

    Coarse on purpose. The question this answers is "did I set this and forget it", and
    ``3d`` answers it while ``267413s`` makes the reader do arithmetic to find out.
    """
    if age_seconds is None:
        return "-"
    if age_seconds < 60:
        return f"{age_seconds}s"
    if age_seconds < 3600:
        return f"{age_seconds // 60}m"
    if age_seconds < 86400:
        return f"{age_seconds // 3600}h"
    return f"{age_seconds // 86400}d"


# -- attach (milestone 002) -------------------------------------------------


def attach(ctx: Context, item_id: int) -> Result:
    """Open a terminal window onto that item's running session (R10, FR-025).

    Changes **no** state and consumes no session: M0 measured that reattachment repaints
    fully and that more than one viewer is allowed, so there is deliberately no
    "is something already attached" check — FR-025's tolerance requirement is satisfied by
    the host's measured capability rather than by logic here.
    """
    item = db.get_work_item(ctx.conn, item_id)
    if item is None:
        return Result(code=EXIT_FAILED, lines=[f"no work item with id {item_id}"])
    session = db.latest_session_for_item(ctx.conn, item_id)
    if session is None or session.state is not SessionState.RUNNING:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                f"work item {item_id} has no running session to attach to"
                + (f" (latest session is {session.state})" if session else "")
            ],
            data={"item_id": item_id, "attached": False},
        )
    if not session.host_socket:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[f"session {session.session_id} has no host socket recorded"],
            data={"item_id": item_id, "attached": False},
        )

    handle = HostHandle(socket_path=session.host_socket, argv=(), pid=session.pid)
    argv = ctx.boundaries.session_host.attach_command(handle)
    title = f"robot-army #{item.issue_number} {item.repo_key} (item {item_id})"
    try:
        with ctx.audit.action(
            "web.attach" if ctx.audit.component == "web" else "session.attach",
            entity_type="work_item",
            entity_id=item_id,
            target=session.session_id,
            detail={"argv": argv, "socket": session.host_socket},
            dry_run=item.dry_run,
        ) as outcome:
            display = ctx.boundaries.display.open(
                item.worktree_path or str(ctx.config.worktree_root),
                argv,
                title,
                {"robot_army_item": str(item_id), "robot_army_session": session.session_id},
                {},
            )
            outcome["window_id"] = display.window_id
    except BoundaryError as exc:
        # A missing terminal socket is a visible refusal naming what is missing, not a
        # traceback: the maintainer's next action is "start kitty", and the message has to
        # say so.
        return Result(
            code=EXIT_FAILED,
            lines=[
                f"could not open a terminal window: {exc}",
                f"  no terminal control socket answered "
                f"{ctx.config.terminal.socket_glob!r}. Nothing about the session changed.",
            ],
            data={"item_id": item_id, "attached": False, "error": str(exc)},
        )
    return Result(
        lines=[
            f"opened a terminal window (id {display.window_id}) attached to session "
            f"{session.session_id}; the session is untouched and still running"
        ],
        data={
            "item_id": item_id,
            "attached": True,
            "session_id": session.session_id,
            "window_id": display.window_id,
        },
    )


# -- lock-aware verbs -------------------------------------------------------


def _request_job(ctx: Context, name: str, *, detail: dict[str, Any] | None = None) -> Result:
    """Ask the running daemon to run ``name`` on its next tick (research.md R5).

    The response is necessarily *"requested"* rather than *"here is what it found"*: the
    daemon reports the result into the audit log, which ``robot-army log`` and the web
    audit view then show. Saying so is the honest version of what 001's contract
    previously over-promised.
    """
    holder = daemon_mod.read_lock_holder(ctx.layout.lock_path)
    with ctx.audit.action(
        f"{name}.request",
        detail={"delegated_to_pid": holder, **(detail or {})},
    ) as outcome:
        created = control.request_job(ctx.layout, name)
        outcome["marker_created"] = created
    tick = ctx.config.daemon.tick_seconds
    lines = [
        f"requested a {name} from the running daemon (pid {holder}); it will run within "
        f"one tick ({tick}s)."
    ]
    if not created:
        lines.append(f"A {name} was already requested and is still pending — this changed nothing.")
    lines.append("What it finds is reported to the audit log: `robot-army log --since 1m`")
    return Result(
        lines=lines,
        data={
            "delegated": True,
            "daemon_pid": holder,
            "requested": name,
            "marker_created": created,
            "within_seconds": tick,
        },
    )


def poll_now(ctx: Context, *, repo: str | None = None) -> Result:
    """Force a poll. Delegates to a running daemon, or acts directly when none holds
    the lock — so the command works the same whether or not the daemon is up."""
    if daemon_mod.is_locked(ctx.layout.lock_path):
        # `repo` is deliberately not carried into the marker: the daemon's poll job polls
        # every configured repository, and inventing a per-repo marker would need a second
        # file format for a filter the daemon has no way to honour. It is recorded in the
        # audit detail so the request is still reconstructable.
        return _request_job(ctx, "poll", detail={"repo": repo})

    outcomes = poll.poll_all(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        dry_run=ctx.effect_level.is_simulated,
        only_repo=repo,
    )
    # asdict(), not vars(): these outcomes are slotted dataclasses and have no __dict__.
    # The web serves this payload directly, so a TypeError here would be a 500 on a
    # control FR-023 requires to work.
    result = Result(data={"delegated": False, "outcomes": [asdict(o) for o in outcomes]})
    for outcome in outcomes:
        if outcome.error:
            result.code = EXIT_FAILED
            result.say(f"{outcome.repo_key}: ERROR {outcome.error}")
        elif outcome.skipped_reason:
            result.say(f"{outcome.repo_key}: skipped ({outcome.skipped_reason})")
        else:
            result.say(
                f"{outcome.repo_key}: HTTP {outcome.status} "
                f"{'(unchanged)' if outcome.status == 304 else ''} "
                f"found={outcome.found} created={outcome.created} rejected={outcome.rejected}"
            )
    return result


def reconcile_now(ctx: Context, *, registry_dir: Path | None = None) -> Result:
    if daemon_mod.is_locked(ctx.layout.lock_path):
        return _request_job(ctx, "reconcile")
    outcome = reconcile.reconcile(
        ctx.conn,
        boundaries=ctx.boundaries,
        audit=ctx.audit,
        config=ctx.config,
        layout=ctx.layout,
        registry_dir=registry_dir,
    )
    result = Result(data={"delegated": False, **outcome.summary()})
    for key, value in outcome.summary().items():
        if key != "notes":
            result.say(f"{key:<20} {value}")
    return result


def drain_spool(ctx: Context) -> Result:
    outcome = spool.drain(
        ctx.conn,
        audit=ctx.audit,
        layout=ctx.layout,
        boundaries=ctx.boundaries,
        config=ctx.config,
    )
    return Result(
        lines=[
            f"applied={outcome.applied} duplicates={outcome.duplicates} "
            f"quarantined={outcome.quarantined} orphaned={outcome.orphaned}"
        ],
        data=asdict(outcome),
    )


def purge_simulated(ctx: Context, *, assume_yes: bool = False, confirm: Any = input) -> Result:
    """Remove ``dry_run`` rows and their sessions (FR-058). Never touches live rows.

    Does not remove worktrees those rows created — those are real directories on disk,
    and removing them is ``worktree remove``'s job.
    """
    counts = db.count_simulated(ctx.conn)
    if not any(counts.values()):
        return Result(lines=["no simulated rows to purge"], data={"purged": counts})
    if not assume_yes:
        answer = confirm(
            f"Delete {counts['work_items']} simulated work item(s), "
            f"{counts['sessions']} simulated session(s) and "
            f"{counts['cards']} simulated card(s)? [y/N] "
        )
        if str(answer).strip().lower() not in ("y", "yes"):
            return Result(code=EXIT_FAILED, lines=["aborted"])
    with ctx.audit.action("purge.simulated", detail=counts), db.transaction(ctx.conn):
        purged = db.purge_simulated(ctx.conn)
    return Result(
        lines=[
            f"purged {purged['work_items']} work item(s), {purged['sessions']} session(s) "
            f"and {purged['cards']} card(s)",
            "worktrees those rows created were NOT removed — use `worktree remove`",
        ],
        data={"purged": purged},
    )


# -- cards (milestone 003) --------------------------------------------------


def card_is_parked(card: Any, config: Any) -> bool:
    """Is this tracked card sitting in a column the author excluded? (data-model.md)

    **Derived, never stored.** A stored flag would go stale the moment ``ignore_lists`` is
    edited, and FR-011 requires that edit to take effect on the next poll with nothing else
    done — a derivation cannot be stale.

    Compared by *name*, against the configuration, so this makes **no board request** and
    keeps working with the board unreachable. That constraint is why the poll stores the
    column's name beside its id.

    A ``NULL`` name — a row tracked before milestone 006's migration and not yet re-polled
    — is not parked, which is milestone 003's behaviour and the safe direction for a value
    we do not have.
    """
    trello = getattr(config, "trello", None)
    if trello is None or not trello.ignore_lists or card.state in NEVER_PARKED:
        return False
    return bool(card.current_list_name) and card.current_list_name in trello.ignore_lists


def _card_dict(
    card: Any, *, work_item_id: int | None = None, parked: bool = False
) -> dict[str, Any]:
    return {
        "id": card.id,
        "card_id": card.card_id,
        "card_url": card.card_url,
        "title": card.title,
        "state": str(card.state),
        "dry_run": card.dry_run,
        "simulated": card.dry_run,
        "repo_key": card.repo_key,
        "issue_number": card.issue_number,
        "issue_url": card.issue_url,
        "source_id": card.source_id,
        "work_item_id": work_item_id,
        "reason": card.reason,
        "create_failures": card.create_failures,
        "placed_list_id": card.placed_list_id,
        "origin_list_id": card.origin_list_id,
        "current_list_id": card.current_list_id,
        "current_list_name": card.current_list_name,
        "parked": parked,
        "parked_list": card.current_list_name if parked else None,
        "archived_at": card.archived_at,
        "first_seen_at": card.first_seen_at,
        "updated_at": card.updated_at,
        "age_seconds": _age_seconds(card.updated_at),
    }


def _age_seconds(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        parsed = datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int((datetime.now(UTC) - parsed).total_seconds())


def _card_work_items(ctx: Context, cards: list[Any]) -> dict[int, int]:
    """Card row id → work item id, joined on ``(repo_key, issue_number)`` (R16).

    A join rather than a column on ``work_items``: the fact is already derivable from
    ``work_items.source_id``, and storing it again would create a second place for it to be
    wrong. One query for the whole set rather than one per card, because both front ends
    render listings and a per-row query is how a listing becomes slow quietly.
    """
    wanted = {c.source_id: c.id for c in cards if c.source_id}
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    rows = ctx.conn.execute(
        f"SELECT id, source_id FROM work_items WHERE source_id IN ({placeholders})",  # noqa: S608
        tuple(wanted),
    ).fetchall()
    return {wanted[row["source_id"]]: row["id"] for row in rows if row["source_id"] in wanted}


def _card_for_item(ctx: Context, item: Any) -> dict[str, Any] | None:
    """The card a work item's issue came from, or ``None`` (R16, FR-048).

    Derived by joining ``cards`` on ``(repo_key, issue_number)`` against the ``repo#number``
    in ``work_items.source_id``. **No column on ``work_items``**: the fact is already
    derivable, and storing it again would create a second place for it to be wrong.
    """
    if ctx.config.trello is None or not item.repo_key or item.issue_number is None:
        return None
    card = db.find_card_by_issue(
        ctx.conn,
        repo_key=item.repo_key,
        issue_number=item.issue_number,
        dry_run=bool(item.dry_run),
    )
    if card is None:
        return None
    # Always False in practice — a card attached to a work item is `linked`, and a linked
    # card is never parked (FR-013). Derived rather than hardcoded so the two paths cannot
    # disagree if that ever stops being true.
    return _card_dict(card, work_item_id=item.id, parked=card_is_parked(card, ctx.config))


def cards(
    ctx: Context,
    *,
    state: str | None = None,
    include_simulated: bool = False,
) -> Result:
    """List tracked cards with their state, resolved issue, and reason (FR-026, FR-047).

    Exits ``3`` when no board is configured, with a message saying so, rather than printing
    an empty table: an empty table would misrepresent "not configured" as "nothing to do",
    and those call for very different next actions.

    An empty list on a *configured* board exits ``0`` — an empty board is not a failure.
    """
    if ctx.config.trello is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                "no [trello] section is configured, so no board is being read.",
                f"Add one to {ctx.config.path} to enable the card source "
                "(specs/003-trello-source/contracts/config.md).",
            ],
            data={"configured": False, "cards": []},
        )

    states = [CardState(state)] if state else None
    rows = db.list_cards(ctx.conn, include_simulated=include_simulated, states=states)
    withheld = 0 if include_simulated else db.count_simulated_cards(ctx.conn, states=states)
    links = _card_work_items(ctx, rows)
    payload = [
        _card_dict(
            card,
            work_item_id=links.get(card.id),
            parked=card_is_parked(card, ctx.config),
        )
        for card in rows
    ]

    result = Result(
        data={
            "configured": True,
            "board_id": ctx.config.trello.board_id,
            "cards": payload,
            "include_simulated": include_simulated,
            # Always present, never absent, so a consumer never has to tell "nothing was
            # withheld" apart from "this build does not report it" -- the ambiguity 008
            # removed from ``status``'s payload and left standing in this one. The web
            # interface reads it; so does ``cards --json``.
            "withheld_simulated": withheld,
        }
    )
    if not payload:
        # "Nothing is tracked" and "everything tracked was withheld from you" are
        # different facts, and the second one used to be reported as the first.
        if withheld:
            result.say(f"no cards visible ({_withheld_note(withheld)})")
        else:
            result.say("no cards tracked yet")
        return result

    table_rows = [
        [
            row["card_id"],
            (row["title"][:40] + "…") if len(row["title"]) > 41 else row["title"],
            row["state"] + ("*" if row["simulated"] else ""),
            row["repo_key"] or "—",
            f"#{row['issue_number']}" if row["issue_number"] else "—",
            _human_age(row["age_seconds"]),
            # Alongside whatever else the card is, never instead of it: a card can be
            # awaiting clarification *and* parked, which is what writing an ambiguous card
            # and parking it produces.
            _card_reason(row)[:60],
        ]
        for row in payload
    ]
    for line in _table(
        table_rows, ["card", "title", "state", "repository", "issue", "in state", "reason"]
    ):
        result.say(line)
    if withheld:
        result.say()
        result.say(_withheld_note(withheld))
    return result


def _card_reason(row: dict[str, Any]) -> str:
    """The reason column: the parked note, the state's own reason, or both."""
    parts = []
    if row["parked"]:
        parts.append(f"parked in {row['parked_list']!r}")
    if row["reason"]:
        parts.append(row["reason"])
    return " — ".join(parts)


def _human_age(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def rescan(ctx: Context, card_id: str, *, all_needs_info: bool = False) -> Result:
    """Force re-evaluation of a card awaiting clarification (FR-024).

    A forced job request through the existing ``control.py`` marker, exactly as ``poll``
    and ``reconcile`` already are — the daemon drains it on its next tick. The response is
    therefore "requested", not "here is what it found", which is the honest thing to say
    about a cross-process request.

    The refusals are deliberate rather than permissive. Rescanning a *linked* card is
    meaningless, and silently doing nothing would be worse than saying so: the author would
    be left believing they had retried something.
    """
    if ctx.config.trello is None:
        return Result(
            code=EXIT_PRECONDITION,
            lines=["no [trello] section is configured, so there are no cards to rescan"],
        )

    if not all_needs_info:
        card = db.find_card(
            ctx.conn,
            board_id=ctx.config.trello.board_id,
            card_id=card_id,
            dry_run=ctx.effect_level.is_simulated,
        )
        if card is None:
            return Result(
                code=EXIT_FAILED,
                lines=[f"card {card_id!r} is not tracked — `robot-army cards` lists what is"],
            )
        if card.state is not CardState.NEEDS_INFO:
            return Result(
                code=EXIT_USAGE,
                lines=[
                    f"card {card_id} is {card.state}, not needs_info. Rescanning it would "
                    "mean nothing — only a card awaiting clarification has anything to "
                    "re-evaluate"
                ],
            )

    if not daemon_mod.is_locked(ctx.layout.lock_path):
        return Result(
            code=EXIT_PRECONDITION,
            lines=[
                "no daemon is running to service the request. Start `robot-army run`, or "
                "wait for its next board interval once it is up"
            ],
        )

    result = _request_job(
        ctx, "rescan", detail={"card_id": None if all_needs_info else card_id}
    )
    result.data["card_id"] = None if all_needs_info else card_id
    result.data["all_needs_info"] = all_needs_info
    return result


# -- durations --------------------------------------------------------------

# Its own section rather than a corner of the log reader's, because two commands now
# depend on it: `log --since` and `anomalies --since`. One parser, so "what counts as a
# duration" cannot have two answers that drift apart (012 FR-002).


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    """``30s``, ``10m``, ``2h``, ``1d``. Every rejection explains itself (FR-069).

    The amount is validated separately from the unit because a plausible-looking value
    like ``"10 fortnights"`` ends in a valid unit character, and letting ``int()`` fail
    on its own would surface "invalid literal for int()" — true, but not an explanation.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty duration; use e.g. 30s, 10m, 2h, 1d")
    unit = text[-1].lower()
    if unit not in _DURATION_UNITS:
        raise ValueError(f"unknown duration {text!r}; use e.g. 30s, 10m, 2h, 1d")
    amount = text[:-1].strip()
    if not amount.isdigit():
        raise ValueError(
            f"unknown duration {text!r}: {amount!r} is not a whole number of "
            f"{unit!r} units; use e.g. 30s, 10m, 2h, 1d"
        )
    return timedelta(seconds=int(amount) * _DURATION_UNITS[unit])


# -- anomalies --------------------------------------------------------------


def _within_window(detected_at: str, cutoff: datetime | None) -> bool:
    """Whether one anomaly's stored detection time falls inside the requested window.

    ``True`` when there is no window, and ``True`` again when the stamp cannot be read.
    The two cases are unrelated but the answer is the same, and deliberately so: a
    detected condition must not vanish from a listing because a filter could not judge it
    (012 FR-010). Silent omission is what Principle III forbids; showing a row the reader
    did not ask for is the only direction it is defensible to err in.

    This is also why the window is applied here rather than as ``WHERE detected_at >= ?``.
    The column is TEXT, so SQL would compare a malformed stamp lexicographically and drop
    it with nothing anywhere in a position to notice (012 research R2).
    """
    if cutoff is None:
        return True
    try:
        stamp = datetime.strptime(detected_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return True
    return stamp >= cutoff


def anomalies(
    ctx: Context,
    *,
    acknowledge: int | None = None,
    show_all: bool = False,
    since: str | None = None,
    include_simulated: bool = False,
) -> Result:
    # Parsed first — before the acknowledgement below, not merely before the listing. A
    # duration the parser refuses must not be the thing that irreversibly marks an anomaly
    # acknowledged, and every other malformed argument here is already refused by argparse
    # before any work happens; this puts `--since` on the same footing (012 research R5).
    #
    # `if since` rather than `is not None`, matching `read_log` exactly: an empty value
    # means "no window" in both commands. FR-002 is a claim about the two behaving the
    # same, which includes this edge.
    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.now(UTC) - parse_duration(since)
        except ValueError as exc:
            return Result(code=EXIT_USAGE, lines=[str(exc)])

    result = Result()
    if acknowledge is not None:
        with db.transaction(ctx.conn):
            changed = db.acknowledge_anomaly(ctx.conn, acknowledge)
        if not changed:
            return Result(
                code=EXIT_FAILED,
                lines=[f"no unacknowledged anomaly with id {acknowledge}"],
            )
        ctx.audit.record(
            "anomaly.acknowledge", outcome="ok", entity_type="anomaly", entity_id=acknowledge
        )
        result.say(f"acknowledged anomaly {acknowledge}")

    # Filtered before `result.data` is built, so the rendered lines and the `--json`
    # payload are drawn from one list and cannot disagree about the window (012 FR-008).
    rows = [
        anomaly
        for anomaly in db.list_anomalies(
            ctx.conn, unacknowledged_only=not show_all, include_simulated=include_simulated
        )
        if _within_window(anomaly.detected_at, cutoff)
    ]
    # The rows this invocation matched and did not show (issue #21). Counted from a listing
    # rather than from a `COUNT(*)`, and put through the *same* `_within_window` predicate the
    # visible rows went through — which is the whole reason `db.list_simulated_anomalies`
    # hands back rows. `--since` is applied here in Python on purpose (012 research R2), so a
    # number counted in SQL would name rows this window had already excluded, and the promise
    # that "withheld" equals "what the flag reveals" would be false exactly when a filter was
    # in play. Milestone 008 made that equality structural for work items; this is the same
    # construction on a second table.
    withheld = (
        0
        if include_simulated
        else sum(
            1
            for anomaly in db.list_simulated_anomalies(
                ctx.conn, unacknowledged_only=not show_all
            )
            if _within_window(anomaly.detected_at, cutoff)
        )
    )
    result.data = {
        "anomalies": [_anomaly_dict(a) for a in rows],
        "known_kinds": list(ANOMALY_KINDS),
        # Always present, including as 0: a consumer must never have to tell "nothing was
        # withheld" apart from "this build does not report it" (008's absent-versus-zero).
        "withheld_simulated": withheld,
    }
    if not rows:
        # Three empty listings that mean different things, and saying so is the whole reason
        # these filters are safe to add. "no outstanding anomalies" is an all-clear; a window
        # that matched nothing is not one, and a reader who reads it as one has been misled
        # by the tool into believing nothing is wrong (012 FR-009). A scope that withheld
        # everything is not one either, and that is issue #21's case exactly: the rows exist,
        # they belong to a rehearsal, and silence here would report an all-clear over them.
        if cutoff is not None:
            result.say(
                f"no anomalies detected in the last {since}"
                + (f" ({_withheld_note(withheld)})" if withheld else "")
            )
        else:
            result.say(
                "no outstanding anomalies"
                + (f" ({_withheld_note(withheld)})" if withheld else "")
            )
        result.say()
        result.say("kinds this system can raise: " + ", ".join(ANOMALY_KINDS))
        return result
    for anomaly in rows:
        # Only ever non-empty under `--all`, which is the only listing that includes a row
        # that has left the open list. Naming *which* way it left is the point: an anomaly
        # the system re-checked and found resolved is a different fact from one somebody
        # dismissed, and #138 is what happens when a stale list gets cleared unread.
        status = ""
        if anomaly.resolved_at:
            status = f"  resolved {timefmt.local(anomaly.resolved_at)}"
        elif anomaly.acknowledged_at:
            status = f"  acknowledged {timefmt.local(anomaly.acknowledged_at)}"
        result.say(
            f"[{anomaly.id}]{'*' if anomaly.dry_run else ''} {anomaly.kind}  "
            f"{anomaly.entity_type or '—'}:"
            f"{anomaly.entity_id or '—'}  detected {timefmt.local(anomaly.detected_at)}"
            f"{status}"
        )
        for key, value in anomaly.detail_obj.items():
            result.say(f"      {key}: {value}")
        result.say()
    # FR-057: a simulated row is marked wherever it is shown. The legend is printed only when
    # the listing actually contains one, the way `status` prints its own.
    if any(anomaly.dry_run for anomaly in rows):
        result.say("* = simulated (dry-run) row")
        result.say()
    # Whenever anything was withheld, not only when the listing came out empty — two visible
    # rows while three are held back is the same contradiction, only quieter (008).
    if withheld:
        result.say(_withheld_note(withheld))
        result.say()
    result.say("kinds this system can raise: " + ", ".join(ANOMALY_KINDS))
    return result


# -- log --------------------------------------------------------------------


#: A record either matches the active filters, fails them, or cannot be judged. The third
#: case is not the second: an unparseable timestamp means the record is *skipped and
#: counted*, which is what FR-044 requires readers to report rather than silently drop.
_MATCH = "match"
_REJECT = "reject"
_UNREADABLE = "unreadable"


def _judge_record(
    record: dict[str, Any],
    *,
    cutoff: datetime | None,
    item_id: int | None,
    outcome: str | None,
) -> str:
    """Apply the log filters to one record. One implementation, two readers.

    ``read_log`` scans forwards and ``read_log_page`` scans backwards; sharing this keeps
    "what does ``--item 42`` mean" from having two answers.
    """
    if cutoff is not None:
        try:
            stamp = datetime.strptime(record["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except (KeyError, ValueError):
            return _UNREADABLE
        if stamp < cutoff:
            return _REJECT
    if item_id is not None and str(record.get("entity_id")) != str(item_id):
        return _REJECT
    if outcome is not None and str(record.get("outcome")) != outcome:
        return _REJECT
    return _MATCH


def read_log(
    ctx: Context,
    *,
    since: str | None = None,
    item_id: int | None = None,
    limit: int | None = None,
    outcome: str | None = None,
) -> Result:
    """The FR-062 reconstruction path: what happened, when, to what, with what result.

    Unparseable lines are skipped **and counted**. R14 flushes per record, so an
    interrupted final write can leave a partial line; refusing to read the log because of
    it would be exactly the wrong trade.
    """
    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.now(UTC) - parse_duration(since)
        except ValueError as exc:
            return Result(code=EXIT_USAGE, lines=[str(exc)])

    records: list[dict[str, Any]] = []
    skipped = 0
    for record, _raw in audit_mod.read_records(ctx.layout.log_dir):
        if record is None:
            skipped += 1
            continue
        verdict = _judge_record(record, cutoff=cutoff, item_id=item_id, outcome=outcome)
        if verdict is _UNREADABLE:
            skipped += 1
        elif verdict is _MATCH:
            records.append(record)

    if limit:
        records = records[-limit:]

    result = Result(data={"records": records, "unparseable_lines": skipped})
    for record in records:
        result.say(_format_record(record))
    if skipped:
        result.say()
        result.say(f"({skipped} unparseable line(s) skipped)")
    return result


#: Default page size for the paged reader. Bounded by construction, per SC-014.
LOG_PAGE_SIZE = 100

#: How much of a daily file is read at a time when scanning it backwards (RA-14).
#:
#: Larger than any single audit record — the longest carry a prompt preview, still kilobytes —
#: so the path where a record spans a block boundary is exercised by a test rather than by
#: production. Small enough that a page filling from the newest few records reads one block and
#: stops, which is the common case and the one SC-014's two-second budget is measured against.
LOG_SCAN_BLOCK_BYTES = 65536

#: The most one page request will read across all daily files (RA-14).
#:
#: A day's file is a few megabytes, so 8 MiB spans the newest two or three days: every
#: unfiltered page and every recent-item filter fills long before it. It is reached only by a
#: filter matching nothing far back in history — the abuse case and the patient case — and both
#: are served correctly by stopping and offering the next page. Before this bound,
#: ``/log?item=999999`` read every ``audit-*.jsonl`` in the directory, whole, into memory.
LOG_SCAN_BUDGET_BYTES = 8 * 1024 * 1024


def _encode_cursor(file_name: str, offset: int) -> str:
    """An opaque cursor naming a file and a **byte position** within it.

    Opaque because its shape is ours to change, not a caller's to depend on — FR-009 says
    there is no stable API here — and it was changed (RA-14). It used to carry the count of
    matching records earlier pages had taken from the file, which had two problems. It was
    unstable under append: the daemon writes to today's file between the two requests of a
    page turn, and "the Nth match counting from the end" names a different record afterwards.
    And it cost an O(n) re-scan, because page *k* had to re-read and re-judge every record of
    pages 1..*k*-1 to skip them.

    ``offset`` is the position the next page scans **backwards** from: it covers ``[0,
    offset)``. Zero means the file is finished and the next page starts at the file before it.
    """
    raw = json.dumps({"f": file_name, "b": offset}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, int] | None:
    """``None`` for anything unreadable: a hand-edited cursor restarts from the newest
    page rather than erroring, because the page it names may legitimately no longer exist.

    A cursor issued before RA-14 carries ``n`` rather than ``b`` and is therefore unreadable
    — which is exactly the case above, and the reason no compatibility shim is needed. The
    only client affected is a browser tab held open across the upgrade, and it recovers by
    showing the newest page.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return str(payload["f"]), int(payload["b"])
    except (ValueError, KeyError, TypeError):
        return None


def _lines_backwards(
    handle: Any, end_offset: int, block: int
) -> Iterator[tuple[bytes, int]]:
    """Yield ``(line, start_offset)`` from ``[0, end_offset)``, newest line first.

    Blocks rather than ``mmap`` (RA-14). The newest daily file is being appended to by the
    daemon while it is read, and a mapping's length is fixed at map time; a normal file object
    read in blocks has no such edge. Blocks rather than ``read_text().splitlines()`` because
    that allocated the whole file plus a list of every line in it to return, at most, a
    thousand records from its end.

    The partial line at the front of each block is carried into the next read, so a record
    longer than one block is still yielded whole. ``start_offset`` is the position the line
    begins at, which is what the cursor stores when a page ends here.
    """
    position = end_offset
    carry = b""
    while position > 0:
        size = min(block, position)
        position -= size
        handle.seek(position)
        chunk = handle.read(size) + carry
        parts = chunk.split(b"\n")
        # The first part may be the tail of a line whose start is in the block below this
        # one — unless this *is* the bottom block, in which case it is a whole line.
        carry = parts.pop(0) if position > 0 else b""
        # Walk what remains newest-first. The offset of each line is derived by subtracting
        # the lengths of everything after it, so it stays exact across a carried line.
        tail = position + len(carry) + (1 if position > 0 else 0)
        offsets = []
        for part in parts:
            offsets.append(tail)
            tail += len(part) + 1
        for part, start in zip(reversed(parts), reversed(offsets), strict=True):
            if part.strip():
                yield part, start
    # No trailing flush of ``carry``: the loop only ends once ``position`` reaches zero, and
    # that final pass is the one branch that sets ``carry`` empty and emits its own first
    # part as a whole line. The file's first record leaves here from inside the loop.


@dataclass(slots=True)
class _FileScan:
    """What one backwards pass over one daily file produced.

    A named result rather than a five-tuple because ``hit_budget`` is the field a reader will
    most want to find, and it is the one a positional fifth element would hide.
    """

    records: list[dict[str, Any]]
    #: Where a following page resumes: it covers ``[0, next_offset)``. ``0`` means the file is
    #: exhausted and the next page starts at the file below it.
    next_offset: int
    #: Lines that could not be parsed or judged. Counted, never silently dropped.
    skipped: int
    #: Bytes of log consumed. Within one block of the bytes actually read from the file.
    read: int
    #: The budget ended this pass, rather than the page filling or the file running out.
    hit_budget: bool


def _scan_file_backwards(
    path: Path,
    *,
    judge: Callable[[dict[str, Any]], str],
    want: int,
    end_offset: int,
    budget: int,
    must_progress: bool,
) -> _FileScan:
    """Read one daily file newest-record-first over ``[0, end_offset)``.

    Pages are disjoint by construction — each covers a byte range strictly below the previous
    one — rather than by counting matches and re-skipping what earlier pages took, which is
    what this did before RA-14 and which cost an O(n) re-scan per page turn.

    Stops when the page is full, when the file runs out, or when ``budget`` bytes have been
    consumed. The budget is checked *before* consuming a line, so ``read`` never exceeds it —
    except under ``must_progress``, which forces the first line through even when it alone is
    larger than the whole budget. Without that, a page could return nothing with a cursor
    pointing where it already was, and "older records" would loop forever.
    """
    found: list[dict[str, Any]] = []
    skipped = 0
    read = 0
    try:
        handle = path.open("rb")
    except OSError:
        # A file that vanished between the glob and the read is not a reason to fail the
        # page; it is counted so the silence is not silent.
        return _FileScan([], 0, 1, 0, False)
    with handle:
        cursor = end_offset
        for raw, start in _lines_backwards(handle, end_offset, LOG_SCAN_BLOCK_BYTES):
            if read + len(raw) + 1 > budget and not (must_progress and read == 0):
                return _FileScan(found, cursor, skipped, read, True)
            read += len(raw) + 1
            cursor = start
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                skipped += 1
            else:
                verdict = judge(record)
                if verdict is _UNREADABLE:
                    skipped += 1
                elif verdict is _MATCH:
                    found.append(record)
            if len(found) >= want:
                return _FileScan(found, cursor, skipped, read, False)
    return _FileScan(found, 0, skipped, read, False)


def read_log_page(
    ctx: Context,
    *,
    since: str | None = None,
    item_id: int | None = None,
    outcome: str | None = None,
    cursor: str | None = None,
    limit: int = LOG_PAGE_SIZE,
) -> Result:
    """One bounded page of the audit log, newest first (research.md R14, FR-044).

    Daily files are read newest-first and each is scanned backwards, stopping the moment
    the page is full. SC-014 wants a bounded page against 100,000 records in under two
    seconds; reading one or two daily files satisfies any first page, and reading the whole
    history to show a hundred lines would grow without bound by construction.

    The cursor names ``(file, records already consumed from it)``, so paging across a file
    boundary produces disjoint pages rather than a repeated or a skipped record.
    """
    cutoff: datetime | None = None
    if since:
        try:
            cutoff = datetime.now(UTC) - parse_duration(since)
        except ValueError as exc:
            return Result(code=EXIT_USAGE, lines=[str(exc)])
    if outcome is not None and outcome not in ("ok", "error", "pending"):
        return Result(
            code=EXIT_USAGE,
            lines=[f"unknown outcome {outcome!r}; use ok, error, or pending"],
        )
    limit = max(1, min(int(limit), 1000))

    files = sorted(Path(ctx.layout.log_dir).glob("audit-*.jsonl"), reverse=True)
    start_index = 0
    # ``None`` means "from the end of the file", which is what a first page wants and what a
    # cursor whose offset was ``0`` wants of the *next* file down.
    start_offset: int | None = None
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            name, offset = decoded
            for index, path in enumerate(files):
                if path.name == name:
                    # An offset of zero means that file is finished, not that it should be
                    # read again — the difference between a page turn and an infinite loop.
                    start_index = index if offset else index + 1
                    start_offset = offset or None
                    break

    records: list[dict[str, Any]] = []
    skipped = 0
    scanned = 0
    truncated = False
    next_cursor: str | None = None
    judge = lambda record: _judge_record(  # noqa: E731 - one binding, read once, below
        record, cutoff=cutoff, item_id=item_id, outcome=outcome
    )
    for path in files[start_index:]:
        try:
            size = path.stat().st_size
        except OSError:
            # Vanished between the glob and the stat. Counted, for the same reason the read
            # failure inside the scan is counted: the silence must not be silent.
            skipped += 1
            start_offset = None
            continue
        # Clamped, always, because the offset arrives in a URL and is therefore client input.
        # Unclamped, an offset past the end of the file made ``_lines_backwards`` seek and
        # read past EOF in block-sized steps; those reads yield no lines, and the byte budget
        # only counts lines that *are* yielded, so it never fired. The cost was linear in the
        # offset rather than in the file, which defeats the ceiling this whole function
        # exists to keep — a minted cursor near ``2**63`` would have held the request thread,
        # and the SQLite connection and audit handle it carries, effectively forever.
        #
        # Clamping is also the honest reading of an honest cursor whose file has since been
        # rewritten smaller: the newest record it can still name is at the end of the file.
        end_offset = size if start_offset is None else max(0, min(start_offset, size))
        scan = _scan_file_backwards(
            path,
            judge=judge,
            want=limit - len(records),
            end_offset=end_offset,
            budget=LOG_SCAN_BUDGET_BYTES - scanned,
            # Progress is guaranteed once per *request*, not once per file: a later file must
            # not be allowed to force a line through on a budget the earlier ones spent.
            must_progress=scanned == 0,
        )
        records.extend(scan.records)
        skipped += scan.skipped
        scanned += scan.read
        start_offset = None
        if len(records) >= limit:
            next_cursor = _encode_cursor(path.name, scan.next_offset)
            break
        if scan.hit_budget:
            # The budget, not the page, ended this request (RA-14). Saying so matters: an
            # empty page that stopped early is not an empty history, and the audit view must
            # never be ambiguous about that. The cursor makes the stop a page boundary rather
            # than a dead end, so the older records stay reachable one bounded page at a time.
            truncated = True
            next_cursor = _encode_cursor(path.name, scan.next_offset)
            break

    filters = {"item": item_id, "since": since, "outcome": outcome}
    result = Result(
        data={
            "records": records,
            "filters": filters,
            "skipped_lines": skipped,
            "unparseable_lines": skipped,
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
            "page_size": limit,
            "truncated": truncated,
            "bytes_scanned": scanned,
        }
    )
    for record in records:
        result.say(_format_record(record))
    if truncated:
        result.say()
        result.say(
            f"(the scan stopped after {scanned} bytes without filling this page; there may "
            "be older matching records)"
        )
    if skipped:
        result.say()
        result.say(f"({skipped} unparseable line(s) skipped)")
    return result


def _format_record(record: dict[str, Any]) -> str:
    marker = {"intent": "→", "outcome": "←"}.get(str(record.get("kind")), " ")
    outcome = record.get("outcome", "")
    sim = " [simulated]" if record.get("simulated") or record.get("dry_run") else ""
    entity = ""
    if record.get("entity_type"):
        entity = f" {record['entity_type']}:{record.get('entity_id')}"
    detail = record.get("detail")
    tail = f"  {json.dumps(detail, default=str)}" if detail else ""
    stamp = timefmt.local(record.get("ts"))
    return f"{stamp} {marker} {record.get('action')} [{outcome}]{entity}{sim}{tail}"


def follow_log(ctx: Context) -> Iterator[str]:
    """Tail the current day's audit file. Used by ``log --follow``."""
    import time as _time

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    path = ctx.layout.log_dir / f"audit-{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open(encoding="utf-8") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                _time.sleep(0.5)
                continue
            try:
                yield _format_record(json.loads(line))
            except json.JSONDecodeError:
                yield f"(unparseable) {line.rstrip()}"


# -- health -----------------------------------------------------------------


def health_check(ctx: Context, *, max_age: float | None = None, do_notify: bool = False) -> Result:
    """Exits 0 if fresh, 4 if stale or absent. Intended to be run by a systemd timer —
    **this, not the daemon, is the dead-man's switch**."""
    threshold = (
        max_age
        if max_age is not None
        else float(ctx.config.health.max_age_seconds or 3 * ctx.config.daemon.reconcile_seconds)
    )
    report = health.check(ctx.layout.heartbeat_path, max_age_seconds=threshold)
    result = Result(
        code=EXIT_OK if report.healthy else EXIT_CHECK_FAILED,
        lines=[("ok: " if report.healthy else "STALE: ") + report.reason],
        data=report.to_dict(),
    )
    if not report.healthy and do_notify:
        # Every configured channel, not just the webhook (issue #106, FR-018).
        #
        # **This path is deliberately not effect-level gated**, and was not before this
        # feature either: ``health_check`` has never touched ``ctx.boundaries``. The reason
        # is worth stating where someone would otherwise "fix" it — ``health --notify``
        # takes no ``--effect-level`` flag and resolves its level from ``[daemon]
        # effect_level``, so routing it through the notifier boundary would silently
        # disable the dead-man's switch for anyone running their daemon at ``local``. The
        # effect level governs what the *daemon* does autonomously; a human, or that
        # human's systemd timer, running this command has already made the decision the
        # effect level exists to withhold (research.md R2).
        title, message_text, fields = health.alert_fields(report)
        outcomes: list[dict[str, Any]] = []
        for channel in channels.build(ctx.config):
            sent, detail = channel.send(title, message_text, fields)
            outcomes.append({"channel": channel.name, "sent": sent, "message": detail})
            result.say(f"{channel.name}: {detail}")
            ctx.audit.record(
                "health.notify",
                outcome="ok" if sent else "error",
                detail={
                    "channel": channel.name,
                    "reason": report.reason,
                    "message": detail,
                },
            )
        if not outcomes:
            # Nothing configured is not an error — it is an author who has not asked to be
            # told. Saying so beats a silence indistinguishable from a delivered alert.
            result.say("no notification channel configured")
        result.data["notified"] = any(o["sent"] for o in outcomes)
        result.data["notify_channels"] = outcomes
    return result


# -- doctor -----------------------------------------------------------------


def doctor(ctx: Context, *, trust_file: Path | None = None) -> Result:
    """Check and report, without changing anything.

    Worth running first every time. It catches the failure that cost M0 the most time: a
    kitty instance carrying ``CLAUDE_CODE_CHILD_SESSION`` in its environment silently
    disables transcript saving, producing sessions that look perfect, exit 0, and can
    never be resumed.
    """
    checks: list[tuple[str, bool, str]] = []

    checks.append(("config", True, f"loaded {ctx.config.path}"))
    for warning in ctx.config.warnings:
        checks.append(("config warning", True, warning))

    try:
        version = int(ctx.conn.execute("PRAGMA user_version").fetchone()[0])
        checks.append(
            (
                "database schema",
                version == SCHEMA_VERSION,
                f"user_version={version}, expected {SCHEMA_VERSION} ({ctx.layout.db_path})",
            )
        )
    except sqlite3.Error as exc:
        checks.append(("database schema", False, str(exc)))

    for name in (ctx.config.worker.binary, "dtach", "git", ctx.config.terminal.binary):
        found = shutil.which(name)
        checks.append((f"binary: {name}", found is not None, found or "NOT FOUND on PATH"))

    # Deliberately the REAL display, not the wired one. `doctor` reports on the machine,
    # and at a simulated effect level the wired probe would cheerfully answer with a
    # fake socket — which is the opposite of what a diagnostic command is for.
    from robot_army.boundaries.kitty import KittyDisplay, describe_refusals

    display = KittyDisplay(ctx.config, ctx.audit)
    socket = display.probe()
    checks.append(
        (
            "terminal socket",
            socket is not None,
            socket
            or (
                # "nothing answered" and "something answered and was refused" are
                # different machines to go and look at (RA-15). A diagnostic that
                # reported an impostor as an absence would hide the one fact the
                # maintainer most needs.
                f"nothing answered {ctx.config.terminal.socket_glob!r}"
                + describe_refusals(display.refusals)
            ),
        )
    )

    import os as _os

    writable = _os.access(ctx.layout.state_dir, _os.W_OK)
    checks.append(("state directory", writable, str(ctx.layout.state_dir)))

    usage = shutil.disk_usage(
        ctx.config.worktree_root if ctx.config.worktree_root.exists() else Path.home()
    )
    free_gb = usage.free / 1024**3
    checks.append(
        (
            "worktree disk space",
            free_gb > 2.0,
            f"{free_gb:.1f} GB free under {ctx.config.worktree_root} "
            "(M0 measured 499 MB for one prepared worktree)",
        )
    )

    # The M0 F19 check. These live in the *terminal daemon's* environment, which is what a
    # session inherits — not ours — so this is a best-effort read of our own as a proxy,
    # plus an explicit note about where to look properly.
    # Names only, never values: several of these carry session tokens, and `doctor`
    # output gets pasted into issues. The audit log's redaction choke point does not
    # cover stdout, so the discipline has to be repeated here.
    present = sorted(k for k in _os.environ if k.startswith("CLAUDE_CODE_"))
    checks.append(
        (
            "CLAUDE_CODE_* environment",
            "CLAUDE_CODE_CHILD_SESSION" not in present,
            (
                f"set in this process: {', '.join(present) or 'none'}. "
                "CLAUDE_CODE_CHILD_SESSION silently disables transcript saving, producing "
                "sessions that look perfect, exit 0, and can never be resumed. Sessions "
                "inherit the TERMINAL daemon's environment, not this one — check it with "
                "`tr '\\0' '\\n' < /proc/$(pidof kitty | cut -d' ' -f1)/environ "
                "| grep CLAUDE_CODE_`"
            ),
        )
    )

    for key, repo in sorted(repos_mod.resolved_all(ctx.conn, ctx.config).items()):
        record = db.get_repo(ctx.conn, key)
        trusted, explanation = dispatch.is_trusted(repo.path, trust_file=trust_file)
        checks.append(
            (
                f"repo: {key}",
                record is not None and trusted,
                f"onboarded={record is not None} trusted={trusted} — {explanation}",
            )
        )

    # The board's five checks (contracts/config.md), so it can be verified without
    # starting the daemon — which is the whole reason `doctor` performs them rather than
    # leaving them to startup. Absent entirely when no board is configured: an
    # unconfigured installation has nothing to check, and inventing a passing check for it
    # would say something about a board that does not exist.
    # The project board's checks (issue #48, contracts/config.md), for the reason the
    # board block below performs its own: the author should learn that their token cannot
    # read projects before they wait a poll interval watching an order fail to change.
    # Only for onboarded repositories — a board belongs to a repository, and an
    # installation with none has nothing to check.
    for key in repos_mod.known(ctx.conn):
        try:
            for check in poll.check_project(
                ctx.conn, boundaries=ctx.boundaries, config=ctx.config, repo_key=key
            ):
                checks.append((f"project: {check.name}", check.ok, check.detail))
        except Exception as exc:  # noqa: BLE001 - doctor reports, it never raises
            # `doctor` exists to report problems; one repository's unreachable board must
            # not stop it reporting on everything else.
            checks.append((f"project: {key}", False, f"could not be checked: {exc}"))

    if ctx.config.trello is not None:
        status = intake.check_board(
            boundaries=ctx.boundaries, audit=ctx.audit, config=ctx.config
        )
        for check in status.checks:
            checks.append((f"board: {check.name}", check.ok, check.detail))

    failures = [name for name, ok, _ in checks if not ok]
    result = Result(
        # 4, not 1: this is a *check* command, and the exit table reserves 4 for a check
        # that failed. `health` already uses it for the same reason.
        code=EXIT_OK if not failures else EXIT_CHECK_FAILED,
        data={
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
            "failures": failures,
            "effect_level": str(ctx.effect_level),
        },
    )
    for name, ok, detail in checks:
        result.say(f"[{'ok' if ok else 'FAIL'}] {name:<28} {detail}")
    result.say()
    result.say(
        "all checks passed" if not failures else f"{len(failures)} check(s) failed: {failures}"
    )
    return result


def sessions_snapshot(ctx: Context, *, registry_dir: Path | None = None) -> dict[str, Any]:
    """A read-only view of the live registry, used by ``status --json`` consumers."""
    scan = sessions.scan(registry_dir=registry_dir)
    return {
        "entries": [
            {
                "session_id": e.session_id,
                "pid": e.pid,
                "cwd": e.cwd,
                "status": e.status,
                "ours": sessions.under_root(e.cwd, ctx.config.worktree_root),
                "scope": procinfo.systemd_scope(e.pid) if e.pid else None,
            }
            for e in scan.entries
        ],
        **sessions.summarise(scan, ctx.config.worktree_root),
    }
