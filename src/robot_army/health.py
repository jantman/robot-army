"""The liveness signal and its checker.

The essential insight (research.md R15): **a dead daemon cannot report its own death**, so
the checker must be a separate process. That makes the systemd user timer the actual
dead-man's switch, and the daemon's heartbeat merely the evidence it reads.

The heartbeat carries the *current activity*, not just a timestamp, so a long preparation
step is visible as work rather than looking like a hang (FR-063). That distinction is the
difference between "it is busy fetching a 400 MB repository" and "it is wedged".

**The heartbeat is not the only evidence, and on its own it is the slower half** (issue #52).
Its age can only say a daemon has *stopped beating*, and it cannot say that sooner than the
staleness threshold — which cannot be lowered, because a threshold near the tick interval
makes a busy daemon trip its own alarm. The lock says something the heartbeat never can and
says it at once: a released lock means the process is gone. So ``check`` takes both, and the
two catch different failures — a released lock is a death, a stale heartbeat under a *held*
lock is a wedge, and telling a reader which is the difference between "restart it" and "look
at it before you restart it".
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from robot_army.paths import atomic_write


class LockState(StrEnum):
    """What was observed of the daemon's single-instance lock, in three values.

    **It lives here rather than in ``daemon`` because the import only runs one way.**
    ``daemon`` imports this module, so a type ``check`` must name cannot be defined there —
    the same constraint ``published_cap`` already records. The placement is not merely
    tolerated: this is the checker's vocabulary for evidence it is handed, and ``daemon`` is
    one supplier of that evidence.

    **``UNKNOWN`` exists because this feature changed what a failed probe means.** Before
    issue #52 an unreadable lock collapsed into "not held" and cost a nuisance: a command
    acted directly, or a page said the daemon was not running. Now that same value would
    read as *the daemon has died*, exit non-zero and wake somebody. A permission error must
    not manufacture a death notice, so a probe that could not answer says so and the
    judgement falls back to the heartbeat alone.

    A caller that consulted nothing is a *fourth* case and is not spelled here — it passes no
    reading at all. "Nobody looked" and "we looked and could not see" produce different
    sentences, so they cannot share a value.
    """

    HELD = "held"
    UNHELD = "unheld"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LockReading:
    """One observation of the lock, taken at one instant by :func:`daemon.observe_lock`.

    State and holder travel together because they are read from one descriptor in one call.
    Asked separately — which two call sites did — they can come from either side of a daemon
    restart, and a page that has gone to the trouble of taking a single reading of the daemon
    would still have had two.

    ``holder`` is ``None`` unless the lock is held. An unheld lock file still contains the
    last holder's pid, and returning that as "the holder" would name a process that has
    exited. ``daemon.read_lock_holder`` keeps returning that text for the caller that wants
    exactly it — explaining which pid a refusal blames — which is a different question.

    ``path`` travels along because two of the sentences ``check`` writes name the lock file:
    "no process holds *this* — the daemon is gone", and "*this* could not be read, so what
    follows is the heartbeat alone". The judgement is handed a reading rather than a path, so
    a reading that did not know what it observed would force every caller to pass the path a
    second time for the message alone.
    """

    state: LockState
    path: Path
    holder: str | None = None

    @property
    def running(self) -> bool:
        """The bool every existing caller already wanted. ``UNKNOWN`` is not running."""
        return self.state is LockState.HELD


class HealthState(StrEnum):
    """The verdict: one member per distinct thing the reader should do next.

    ``check``'s docstring has always argued that "absent, unreadable, and stale are three
    different reasons and are reported as such". Issue #52 adds the two the lock makes
    visible — *died* and *hung* — on exactly that argument, because restarting a wedged
    daemon destroys the only evidence of why it wedged.

    ``STALE`` survives the addition rather than being renamed to ``HUNG``, because two paths
    genuinely reach a verdict with no usable lock evidence: a caller that passed no reading,
    and a reading that came back ``UNKNOWN``. Neither may claim to know whether a process is
    there.
    """

    OK = "ok"
    DIED = "died"
    HUNG = "hung"
    STARTING = "starting"
    NEVER_STARTED = "never_started"
    UNREADABLE = "unreadable"
    STALE = "stale"

    @property
    def label(self) -> str:
        """The word every surface prints for this verdict.

        Defined once because ``health``, ``status`` and the web chrome all print it, and the
        entire point of issue #52 is that those three cannot be allowed to describe one
        machine differently. ``ok`` stays lowercase so a healthy line is byte-for-byte what
        it has always been.
        """
        return "ok" if self is HealthState.OK else self.value.upper().replace("_", " ")


@dataclass(frozen=True, slots=True)
class Heartbeat:
    ts: str
    pid: int
    effect_level: str
    activity: str
    cycles: int
    dispatched: int = 0
    errors: int = 0
    #: FR-036. A first-class field rather than a member of ``extra`` because
    #: ``docs/state.md`` documents this file's shape for a human reading it at 2am, and a
    #: named field is what that reader will look for. It defaults to ``False``, so a
    #: heartbeat written by an older build still parses.
    dispatch_paused: bool = False
    #: Board health, or ``None`` on an installation with no ``[trello]`` section — which
    #: is not a degraded board but the absence of one, and the two must not read alike.
    #: A first-class field for the same reason ``dispatch_paused`` is: ``docs/state.md``
    #: documents this file's shape for a human reading it at 2am, and a named field is
    #: what that reader will look for. Defaults to ``None``, so an older heartbeat parses.
    board: dict[str, Any] | None = None
    #: The global session cap this daemon is enforcing (issue #30). A first-class field for
    #: the same reason ``dispatch_paused`` and ``board`` are — ``docs/state.md`` documents
    #: this file's shape for a human reading it at 2am, and a named field is what that
    #: reader will look for. It is here at all because **the daemon is the authority on the
    #: cap**: it reads the value once at startup and cannot change it while it runs, so a
    #: surface that reports a fraction against anything else is guessing at what the process
    #: doing the enforcing believes. Defaults to ``None``, so an older heartbeat parses.
    max_concurrent_sessions: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


def write_heartbeat(
    path: Path,
    *,
    effect_level: str,
    activity: str,
    cycles: int,
    dispatched: int = 0,
    errors: int = 0,
    dispatch_paused: bool = False,
    board: dict[str, Any] | None = None,
    max_concurrent_sessions: int | None = None,
    extra: dict[str, Any] | None = None,
) -> Heartbeat:
    """Write the heartbeat atomically.

    Write-fsync-rename, so a process killed mid-write never leaves a partial file
    observable to the checker — which would otherwise read as corruption and report a
    false alarm at the exact moment the daemon was healthy.
    """
    beat = Heartbeat(
        ts=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        pid=os.getpid(),
        effect_level=effect_level,
        activity=activity,
        cycles=cycles,
        dispatched=dispatched,
        errors=errors,
        dispatch_paused=dispatch_paused,
        board=board,
        max_concurrent_sessions=max_concurrent_sessions,
        extra=extra or {},
    )
    atomic_write(Path(path), beat.to_json(), mode=0o644)
    return beat


@dataclass(frozen=True, slots=True)
class HealthReport:
    """A verdict and the evidence it was reached on.

    ``state`` is required and has no default. Every default available would be a lie waiting
    to be told: ``OK`` would let a failing report claim health, and an eighth "unspecified"
    member would exist only to be the value nobody meant. It is cheap to insist on — the
    report is built in one function and a handful of tests, all of which know what they mean.

    ``lock`` records what the verdict was judged against, and ``None`` there means *no reading
    was supplied* rather than *no daemon holds it*. A consumer that could not tell those apart
    would be back to guessing, which is the whole of what issue #52 was.
    """

    healthy: bool
    reason: str
    state: HealthState
    lock: LockState | None = None
    age_seconds: float | None = None
    heartbeat: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "state": str(self.state),
            "lock": str(self.lock) if self.lock is not None else None,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "heartbeat": self.heartbeat,
        }


def check(
    path: Path,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
    lock: LockReading | None = None,
) -> HealthReport:
    """Judge the daemon from the heartbeat and, when the caller supplies one, the lock.

    Absent, unreadable, and stale are three different reasons and are reported as such:
    "never started" and "died an hour ago" call for different actions. Issue #52 added the
    two more the lock makes visible, on exactly that argument:

    * **died** — no process holds the lock. Instant and unambiguous, *whatever the
      heartbeat's age*: a heartbeat written a second ago says only that something was alive
      a second ago. This is the whole of the ~180-second detection floor, removed. Restart it.
    * **hung** — a process holds the lock and its heartbeat has stopped. Alive and wedged.
      Look at it *before* restarting it; a restart destroys the only evidence of why.
    * **starting** — a process holds the lock and has not published a beat of its own yet.
      ``run_daemon`` takes the lock and then wires boundaries, checks preconditions and runs
      ``startup`` — network work, seconds of it — before the first beat, and nothing unlinks
      the previous daemon's heartbeat. So "lock held, newest heartbeat from a dead pid" is an
      ordinary restart, not a wedge, and calling it one would send somebody to take a stack of
      a perfectly healthy process.

    **``lock`` is optional, and its absence is a supported case, not a degraded one.** A
    caller that has not looked gets exactly the verdicts and exactly the sentences this
    function produced before issue #52. The judgement never probes for itself: ``daemon``
    imports this module, so the reverse would be a cycle, and every caller either holds a
    reading already or takes one a line away — ``web.handle`` deliberately takes exactly one
    per request so that the halves of a page cannot describe different instants.

    **A verdict reached without the lock never reads like one reached with it.** An
    ``UNKNOWN`` reading — a probe that could not run — falls back to the heartbeat alone and
    says so in the sentence. Answering "died" on the strength of a permission error is the
    one failure mode worse than the slowness this feature removes.

    Two rules exist to keep an ordinary restart quiet, because a daily event that raises an
    alarm trains the reader to ignore the alarm. A **fresh** heartbeat is ``ok`` whoever wrote
    it, and a **stale** one under a lock held by a different pid is ``starting`` rather than
    ``hung``.

    ``now`` exists so the staleness boundary can be tested exactly. Timestamps are
    written to whole-second resolution, so a test that writes "sixty seconds ago" and
    reads immediately measures 60.4 seconds and cannot pin down the comparison.
    """
    path = Path(path)
    observed = lock.state if lock is not None else None

    def report(
        healthy: bool,
        verdict: HealthState,
        reason: str,
        *,
        age: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HealthReport:
        return HealthReport(healthy, reason, verdict, observed, age, payload)

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if lock is not None and lock.state is LockState.HELD:
            return report(
                False,
                HealthState.STARTING,
                f"a daemon holds {lock.path} but nothing has been written to {path} yet "
                "— it is starting, or it stopped before its first beat",
            )
        return report(
            False,
            HealthState.NEVER_STARTED,
            f"no heartbeat file at {path} — the daemon has never run{_alone(lock)}",
        )
    except OSError as exc:
        return report(
            False, HealthState.UNREADABLE, f"could not read {path}: {exc}{_lock_clause(lock)}"
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return report(
            False,
            HealthState.UNREADABLE,
            f"heartbeat at {path} is not valid JSON: {exc}{_lock_clause(lock)}",
        )
    if not isinstance(payload, dict) or "ts" not in payload:
        return report(
            False,
            HealthState.UNREADABLE,
            f"heartbeat at {path} has no timestamp{_lock_clause(lock)}",
        )

    try:
        stamp = datetime.strptime(str(payload["ts"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return report(
            False,
            HealthState.UNREADABLE,
            f"heartbeat timestamp is unparseable: {payload['ts']!r}{_lock_clause(lock)}",
        )

    age = ((now or datetime.now(UTC)) - stamp).total_seconds()

    # Above the freshness test on purpose. With the lock released the process is gone, and
    # the age of what it last wrote decides nothing — that ordering *is* the fix for #52.
    if lock is not None and lock.state is LockState.UNHELD:
        return report(
            False,
            HealthState.DIED,
            f"no process holds {lock.path} — the daemon is gone; its last heartbeat is "
            f"{int(age)}s old (pid {payload.get('pid')}, "
            f"last activity {payload.get('activity')!r})",
            age=age,
            payload=payload,
        )

    if age <= max_age_seconds:
        return report(
            True,
            HealthState.OK,
            f"heartbeat is {int(age)}s old (pid {payload.get('pid')}, "
            f"{payload.get('activity')}){_alone(lock)}",
            age=age,
            payload=payload,
        )

    if lock is not None and lock.state is LockState.HELD:
        # Compared as text because the lock file holds a line of it and the heartbeat holds
        # a number, and a pid unreadable from either side is a doubt rather than a match —
        # the comparison ``published_cap`` already makes, for the same restart window.
        holder = (lock.holder or "").strip()
        if holder and str(payload.get("pid")) == holder:
            return report(
                False,
                HealthState.HUNG,
                f"pid {holder} still holds {lock.path} but its heartbeat is {int(age)}s old, "
                f"past the {int(max_age_seconds)}s threshold — the daemon is wedged, not gone "
                f"(last activity {payload.get('activity')!r})",
                age=age,
                payload=payload,
            )
        # Two sentences rather than one with a placeholder in it: "belongs to pid 111,
        # which is not the holder" is a claim, and it may not be made when the holder is
        # precisely what could not be read.
        whose = (
            f"belongs to pid {payload.get('pid')}, which is not the holder"
            if holder
            else f"belongs to pid {payload.get('pid')} and cannot be matched to the holder, "
            "whose pid could not be read"
        )
        return report(
            False,
            HealthState.STARTING,
            f"a daemon holds {lock.path} but has not beaten yet; the newest heartbeat is "
            f"{int(age)}s old and {whose}",
            age=age,
            payload=payload,
        )

    return report(
        False,
        HealthState.STALE,
        f"heartbeat is {int(age)}s old, past the {int(max_age_seconds)}s threshold "
        f"(pid {payload.get('pid')}, last activity {payload.get('activity')!r})"
        f"{_alone(lock)}",
        age=age,
        payload=payload,
    )


def _alone(lock: LockReading | None) -> str:
    """The admission that a verdict was reached on half the evidence (FR-013).

    Appended to every sentence that judged the heartbeat by itself *because it had to*. A
    caller that supplied no reading gets nothing — it did not ask, and today's output is
    exactly what it has always been — but a probe that could not answer must never be
    mistaken for one that answered "nothing is there".
    """
    if lock is None or lock.state is not LockState.UNKNOWN:
        return ""
    return f" — {lock.path} could not be read, so this is the heartbeat's age alone"


def _lock_clause(lock: LockReading | None) -> str:
    """What the lock said, for the verdicts the lock does not decide.

    A corrupt heartbeat is neither a death nor a hang and must not be reported as either
    (FR-007). That is a reason to keep the *verdict* its own, not a reason to withhold a
    fact the reader is about to want — so the sentence carries both.
    """
    if lock is None:
        return ""
    if lock.state is LockState.HELD:
        return f" (a daemon holds {lock.path})"
    if lock.state is LockState.UNHELD:
        return f" (no process holds {lock.path})"
    return f" ({lock.path} could not be read)"


def published_cap(
    report: HealthReport, *, running: bool, lock_holder: str | None
) -> int | None:
    """The session cap the *running* daemon says it is enforcing, or ``None`` (issue #30).

    Issue #30 is a web header reading ``6/5`` — over capacity, apparently, so nothing can
    dispatch — while the truth was ``6/7`` with two slots free. The numerator is observed
    from the machine on every render; the denominator was whatever configuration that
    process loaded when it started, and no process rereads it. So the two halves of one
    fraction could be days apart in age with nothing saying so.

    Rereading the file more eagerly does not fix it, because **the file is not the
    authority**. The daemon is the only process that admits or withholds a dispatch, its cap
    is fixed when it starts, and a number reported against anything else is a guess about
    what some other process believes. So the cap travels on the heartbeat, and this is the
    one function that reads it.

    ``running`` and ``lock_holder`` are parameters rather than probes taken here, for a
    mechanical reason and a design one. ``daemon`` imports this module, so importing it back
    would be a cycle; and every caller has already probed the lock for the effect level, so
    taking a second one would let the two halves of one page answer differently across a
    daemon starting mid-request. Together they mean:

    * **No daemon holds the lock** (``running`` false). ``None``. Nothing is enforcing
      anything, and deferring to a heartbeat left by a dead process would be the same
      surprise in the other direction — the reasoning ``pages.effect_mismatch`` already
      records for the level.
    * **A daemon holds the lock, and wrote this heartbeat.** Its cap decides, *including
      from a stale heartbeat*. A daemon's cap, like its effect level, cannot change while it
      runs, so a stale heartbeat from the process currently holding the lock still names
      that process's cap correctly. Staleness means a tick is running long — which is when
      the machine is busy, which is exactly when the fraction is being read.
    * **A daemon holds the lock and somebody else wrote this heartbeat.** ``None``.

    **That third case is a real window, not a hypothetical**, and it is why the pid is
    compared at all rather than the lock being taken as proof. ``run_daemon`` acquires the
    lock and then wires boundaries, checks preconditions and runs ``startup`` — network work,
    seconds of it — before the first beat, and nothing unlinks the previous daemon's
    heartbeat. So for the length of a restart, ``is_locked`` is true while the newest
    heartbeat on disk belongs to the *dead* process. Lower the cap from 7 to 2 and restart,
    and without this check every surface would report 7 and, worse, the launch gate would
    admit sessions up to 7 against a daemon about to enforce 2. Both files carry their
    writer's pid, so the link is checkable; unreadable or mismatched, this fails to "use your
    own configuration", which is what it does for every other doubt.

    A value is believed only when it could have come from the loader: an ``int`` (``bool``
    is an ``int`` in Python and is not one here) of at least 1, which is the floor
    ``config`` itself enforces. Anything else — absent, a string, zero, negative — is *not
    published* rather than *a cap of zero*, because replacing a stale number with a
    nonsensical one is not an improvement.
    """
    if not running:
        return None
    beat = report.heartbeat
    if not beat:
        return None
    # The heartbeat must be the lock holder's own. Compared as text because the lock file
    # holds a line of it and the heartbeat holds a number, and a pid that cannot be read
    # from either side is a doubt rather than a match.
    holder = (lock_holder or "").strip()
    if not holder or str(beat.get("pid")) != holder:
        return None
    raw = beat.get("max_concurrent_sessions")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


def post_json(
    webhook_url: str, body: dict[str, Any], *, timeout: float = 10.0
) -> tuple[bool, str]:
    """A bounded-timeout JSON POST. Vendor-neutral, and that is all it is.

    This docstring used to claim that "a generic webhook covers ntfy and Pushover — both
    named in the planning document — without either becoming a dependency". **The Pushover
    half was wrong**, and issue #106 exists because of it: ntfy accepts an arbitrary JSON
    body, Pushover takes form-encoded parameters and rejects anything else. Pointing
    ``[health] webhook_url`` at Pushover produced a rejected request, not a notification.

    The instinct behind the claim survives intact — one transport module, one timeout
    convention, no vendor client library. What changed is that "one transport" now means
    this module's two bounded POST functions rather than this one. See ``post_form``.

    Extracted from ``notify`` in milestone 004 so the notifier shares it rather than
    growing a second HTTP client with a second timeout to keep correct (R14).
    """
    if not webhook_url:
        return False, "no webhook_url configured"
    import httpx

    try:
        response = httpx.post(webhook_url, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"webhook POST failed: {exc}"
    if response.status_code >= 400:
        return False, f"webhook returned HTTP {response.status_code}"
    return True, f"notified {webhook_url} (HTTP {response.status_code})"


def post_form(
    url: str, data: dict[str, str], *, timeout: float = 10.0
) -> tuple[bool, str]:
    """A bounded-timeout form-encoded POST — ``post_json``'s sibling, for Pushover (R4).

    Same shape, same explicit timeout, same absence of a retry loop, so there is still one
    convention to keep correct rather than two. Principle IV requires the timeout of every
    network call; a notification is also the one thing where a retry is least justified,
    because the state change it describes is already durably logged.

    **The caller's credentials belong in ``data``, never in ``url``.** The messages this
    function returns interpolate the URL, so keeping secrets out of it makes FR-007 a
    property of the signature rather than a rule every call site has to remember. Nothing
    from the response body is returned for the same reason: recording an upstream body
    verbatim is how a credential leaks the day the upstream starts echoing the request.
    """
    if not url:
        return False, "no url configured"
    import httpx

    try:
        response = httpx.post(url, data=data, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"POST to {url} failed: {exc}"
    if response.status_code >= 400:
        return False, f"{url} returned HTTP {response.status_code}"
    return True, f"notified {url} (HTTP {response.status_code})"


def alert_fields(report: HealthReport) -> tuple[str, str, dict[str, Any]]:
    """The stale-heartbeat alert, as the three arguments every channel takes.

    A pure composer: the body's shape is this function's, and the transport is somebody
    else's. It replaced ``notify``, which was this composer welded to a single transport
    and could therefore only ever reach one channel — the reason a Pushover-only
    installation would have been told about item failures and never about the daemon
    dying (research.md R2).

    The fields reproduce the body ``notify`` posted, so an existing webhook sees no change
    beyond ``state`` (issue #52) — which is here because the person reading this is not at a
    terminal, and "died" and "hung" ask them for different things: restart it, or look at it
    first because a restart destroys the evidence. The message is ``report.reason``, so that
    distinction reaches every channel and the ``health.notify`` audit record without a single
    call site changing. ``host`` and ``ts`` are added by
    :func:`robot_army.channels.webhook_body`, which is where they were always added for the
    notification path.
    """
    return (
        "robot-army health check failed",
        report.reason,
        {
            "healthy": report.healthy,
            "state": str(report.state),
            "age_seconds": report.age_seconds,
        },
    )


def board_signal(
    conn: Any,
    *,
    config: Any,
    ingesting: bool,
    failures: list[str] | None = None,
) -> dict[str, Any] | None:
    """The board's health, for the heartbeat and everything that renders it (FR-009).

    ``None`` when no board is configured. That is *not* a degraded board — it is the
    absence of one — and reporting the two alike would either invent a problem on every
    milestone-002 installation or hide a real one behind "not applicable".

    ``last_polled_at`` and ``consecutive_failures`` come from ``poll_state`` under the
    synthetic key R13 assigns, so a board that has stopped answering is visible as an age
    and a count rather than as silence. Silence is what FR-009 exists to forbid: "I could
    not ask" must never look like "nothing found".
    """
    from robot_army import db

    trello = getattr(config, "trello", None)
    if trello is None:
        return None
    state = db.get_poll_state(conn, board_poll_key(trello.board_id))
    return {
        "board_id": trello.board_id,
        "ingesting": ingesting,
        "failed_checks": list(failures or []),
        "last_polled_at": state.last_polled_at,
        "last_polled_age_seconds": _age(state.last_polled_at),
        "consecutive_failures": state.consecutive_failures,
        "backoff_until": state.backoff_until,
        "healthy": ingesting and state.consecutive_failures == 0,
    }


def board_poll_key(board_id: str) -> str:
    """The synthetic ``poll_state`` key for a board (R13).

    ``poll_state`` has no foreign key and no consumer that renders its rows as
    repositories, so a non-repository key is safe and a second identically shaped table is
    not needed. Defined here, next to the only other code that reads it, so the two
    spellings cannot drift.
    """
    return f"trello:board:{board_id}"


def _age(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int((datetime.now(UTC) - parsed).total_seconds())
