"""Board ingestion: what a card *means*, and what it becomes.

**This is the only module that knows what a card means.** It never speaks HTTP — every
board call goes through the wired :class:`~robot_army.boundaries.CardSourceReader` and
:class:`~robot_army.boundaries.CardSourceWriter`, and every issue creation through the
existing ``IssueSourceWriter``. The counterpart split lives in
:mod:`robot_army.boundaries.trello`, which knows the API exists and nothing else.

The order of operations in here is the milestone's whole invariant (§11, R6, R7):

1. Consult the ``cards`` mapping row. If one exists, do nothing — and in particular do
   **not** read the board's comments, which is "don't parse comments as the authoritative
   source in normal operation" expressed as a call-site rule.
2. With no mapping, look for our own marker comment on the card and restore from it.
3. Only then create, in four separately resumable steps with the intent written first.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army import db, health
from robot_army.boundaries import BoardInfo, TransportError
from robot_army.cardstates import CardState, transition_card, utcnow
from robot_army.models import PollState

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config, TrelloConfig
    from robot_army.effects import Boundaries


# -- board preconditions (R10, R11, contracts/config.md) --------------------


@dataclass(frozen=True, slots=True)
class BoardCheck:
    """One precondition and its verdict.

    A structured per-check result rather than a bare boolean, because "the board is not
    usable" and "the label was renamed" call for completely different actions, and
    collapsing them into one bit is how a diagnostic becomes useless.

    ``informational`` marks a check that is **reported and never gated on** — the member
    list is the only one, and FR-004a is emphatic about why: who else may see the author's
    own private board is the author's decision, and a system that refused to run over it
    would be building the access policy Principle II forbids.
    """

    name: str
    ok: bool
    detail: str
    informational: bool = False


@dataclass(frozen=True, slots=True)
class BoardStatus:
    """The result of all five checks, plus whatever the board told us."""

    checks: tuple[BoardCheck, ...]
    info: BoardInfo | None = None
    label_id: str | None = None
    in_progress_list_id: str | None = None
    done_list_id: str | None = None

    @property
    def ok(self) -> bool:
        """Whether ingestion may proceed. Informational checks never decide this."""
        return all(check.ok for check in self.checks if not check.informational)

    @property
    def failures(self) -> list[BoardCheck]:
        return [c for c in self.checks if not c.ok and not c.informational]


def check_board(
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
) -> BoardStatus:
    """Verify the board is usable, once per process (R10, R11).

    Four gating checks and one informational one. Their failure disables **ingestion
    only** — dispatch of issues the author wrote themselves is unaffected, because an
    unrelated board misconfiguration must not take down the part of the system that has
    nothing to do with the board.

    The two that are easy to under-rate:

    * A **renamed label** produces zero matching cards, which is indistinguishable from an
      empty board. The system would sit there looking healthy and doing nothing, which is
      exactly what "silent failure is forbidden" is aimed at.
    * A **missing list** is worse: it is discovered halfway through a lifecycle, after the
      issue already exists.
    """
    trello = config.trello
    if trello is None:
        return BoardStatus(
            checks=(
                BoardCheck(
                    "board configured",
                    ok=False,
                    detail="no [trello] section — the board source is inert (FR-001)",
                ),
            )
        )

    reader = boundaries.card_reader
    if reader is None:
        # Configured but unwired means a wiring bug, not a board problem. Distinguishing
        # the two is the difference between fixing the config and reading the code.
        return BoardStatus(
            checks=(
                BoardCheck(
                    "board reachable",
                    ok=False,
                    detail="[trello] is configured but no board reader was wired",
                ),
            )
        )

    try:
        info = reader.board_info()
    except TransportError as exc:
        audit.error("trello.board.check", error=exc, entity_type="board", entity_id=trello.board_id)
        return BoardStatus(
            checks=(
                BoardCheck(
                    "board reachable",
                    ok=False,
                    detail=f"could not read board {trello.board_id}: {exc}",
                ),
            )
        )

    checks = [
        BoardCheck("board reachable", ok=True, detail=f"{trello.board_id} — {info.name!r}"),
        BoardCheck(
            "board is private",
            ok=info.permission_level == "private",
            detail=(
                f"permissionLevel={info.permission_level!r}"
                + ("" if info.permission_level == "private" else ", expected 'private'")
            ),
        ),
        BoardCheck(
            "board members",
            ok=True,
            detail=(
                f"{len(info.member_ids)} member(s): {', '.join(info.member_ids) or 'none'} "
                "— recorded, never gated on (FR-004a)"
            ),
            informational=True,
        ),
    ]
    checks.append(_present("tag", trello.label, info.labels, "label"))
    checks.append(_present("in-progress list", trello.in_progress_list, info.lists, "list"))
    checks.append(_present("done list", trello.done_list, info.lists, "list"))

    status = BoardStatus(
        checks=tuple(checks),
        info=info,
        label_id=info.labels.get(trello.label),
        in_progress_list_id=info.lists.get(trello.in_progress_list),
        done_list_id=info.lists.get(trello.done_list),
    )
    audit.record(
        "trello.board.check",
        outcome="ok" if status.ok else "error",
        entity_type="board",
        entity_id=trello.board_id,
        detail={
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "informational": c.informational}
                for c in status.checks
            ],
            "ingestion": "enabled" if status.ok else "disabled",
        },
    )
    return status


def _present(what: str, name: str, available: dict[str, str], kind: str) -> BoardCheck:
    """One existence check, with a message that names what to fix.

    Listing what the board *does* have is the difference between "the label is missing"
    and "the label is missing, and here are the six that exist, one of which you renamed".
    """
    found = name in available
    have = ", ".join(sorted(available)) or "none"
    return BoardCheck(
        f"{what} exists",
        ok=found,
        detail=(
            f"{name!r} found"
            if found
            else f"{name!r} is not a {kind} on this board (has: {have})"
        ),
    )


def board_disabled_anomaly(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    config: Config,
    status: BoardStatus,
) -> None:
    """Record that ingestion is off, and why, in the two places someone will look.

    The anomaly is what makes this visible without reading the log; the log line is what
    makes it reconstructable. Neither alone is enough at 2am.
    """
    trello: TrelloConfig | None = config.trello
    board_id = trello.board_id if trello else "(unconfigured)"
    failures = status.failures
    detail = {
        "board_id": board_id,
        "failed_checks": [{"name": c.name, "detail": c.detail} for c in failures],
        "consequence": (
            "board ingestion is disabled; polling and dispatch of issues you wrote "
            "yourself are unaffected"
        ),
    }
    with db.transaction(conn):
        db.raise_anomaly(
            conn,
            kind="board_precondition",
            entity_type="board",
            entity_id=board_id,
            detail=detail,
        )
    audit.record(
        "trello.board.check",
        outcome="error",
        entity_type="board",
        entity_id=board_id,
        detail=detail,
    )


# -- the board poll cycle (R13, R14, R15) -----------------------------------

#: Backoff ceiling for a board whose polls keep failing. The same shape and the same
#: ceiling ``poll.poll_repo`` uses, so a reader meets one policy rather than two.
MAX_BACKOFF_SECONDS = 900

#: Consecutive failures before an anomaly is raised. GitHub's threshold, reused verbatim:
#: a second convention would be a second thing to remember for no gain (R15).
FAILURE_ANOMALY_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class PollOutcome:
    """What one board cycle did. Mirrors ``poll.PollOutcome`` deliberately."""

    board_id: str
    found: int = 0
    created: int = 0
    evaluated: int = 0
    issues_created: int = 0
    held: int = 0
    dropped: int = 0
    recovered: int = 0
    failed: int = 0
    error: str | None = None
    skipped_reason: str | None = None
    #: The tagged cards this cycle actually read. Carried so the evaluation pass does not
    #: fetch the board a second time — one call per cycle is what R13's interval budget
    #: assumes, and two would double it for nothing.
    cards: tuple[Any, ...] = ()


def poll_board(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    status: BoardStatus,
    dry_run: bool,
) -> PollOutcome:
    """Read the board and track what is on it. **Writes nothing to either system.**

    Card *evaluation* — resolution, creation, the lifecycle — is the caller's next step;
    this function's whole job is to make the board's current contents observable in the
    database. Keeping the two apart is what makes the foundational checkpoint meaningful:
    at this point the system can see the board and has created nothing anywhere.

    A transport failure is recorded, backed off, and returned as a *failed poll* — never
    converted into "no cards found" (FR-009). That distinction is the reason the boundary
    raises rather than returning an empty list.
    """
    trello = config.trello
    reader = boundaries.card_reader
    if trello is None or reader is None:
        # Not an error and not a failure: there is nothing configured to poll. Returning a
        # skip rather than raising keeps "no board" distinct from "board broken".
        return PollOutcome(board_id="", skipped_reason="no board configured")
    if not status.ok or status.label_id is None:
        # The whole verdict, not just the label. A caller that passed a failed status would
        # otherwise ingest against a board whose lists are missing — and the point of
        # checking the lists at startup is that a missing one is discovered *before* an
        # issue exists rather than halfway through a lifecycle (R11). The daemon gates on
        # this too; the guard lives here as well so it cannot be forgotten by a new caller.
        return PollOutcome(
            board_id=trello.board_id, skipped_reason="board preconditions failed"
        )

    key = health.board_poll_key(trello.board_id)
    state = db.get_poll_state(conn, key)
    if state.backoff_until and state.backoff_until > utcnow():
        return PollOutcome(
            board_id=trello.board_id,
            skipped_reason=f"in backoff until {state.backoff_until}",
        )

    try:
        cards = reader.poll(trello.board_id, status.label_id)
    except TransportError as exc:
        return _record_board_failure(conn, audit, trello=trello, state=state, error=exc)

    created = 0
    for card in cards:
        with db.transaction(conn):
            row_id = db.insert_card(
                conn,
                board_id=card.board_id or trello.board_id,
                card_id=card.card_id,
                card_url=card.url,
                title=card.title,
                body=card.body,
                dry_run=dry_run,
                last_activity=card.last_activity,
                # Captured at first sighting, the only moment the card is guaranteed to be
                # where the author left it. Learning it later would record a list *we* put
                # it in as the place it came from (FR-029).
                origin_list_id=card.list_id,
            )
            if row_id is None:
                continue
            created += 1
            audit.record(
                "trello.evaluated",
                outcome="ok",
                entity_type="card",
                entity_id=card.card_id,
                target=card.card_id,
                detail={"title": card.title, "list_id": card.list_id, "first_seen": True},
                dry_run=dry_run,
            )

    with db.transaction(conn):
        db.save_poll_state(
            conn,
            PollState(
                repo_key=key,
                # Always NULL: Trello offers no usable conditional request on this
                # endpoint, which is why the interval is 300 seconds rather than 60 (R13).
                etag=None,
                last_polled_at=utcnow(),
                last_status=200,
                consecutive_failures=0,
                backoff_until=None,
            ),
        )

    # One aggregate record per cycle, which is the Principle III exception the plan
    # enumerates and justifies: the individual board reads change no state outside this
    # process, and logging each would bury the records that matter. Every failure and every
    # write is still recorded individually.
    audit.record(
        "trello.poll",
        outcome="ok",
        entity_type="board",
        entity_id=trello.board_id,
        detail={"tagged": len(cards), "newly_tracked": created},
        dry_run=dry_run,
    )
    return PollOutcome(
        board_id=trello.board_id, found=len(cards), created=created, cards=tuple(cards)
    )


def _record_board_failure(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    trello: TrelloConfig,
    state: PollState,
    error: Exception,
) -> PollOutcome:
    """Record a board failure with its cause, extend the backoff, and raise at threshold.

    The one thing this must never do is return an empty card list. "I could not ask" and
    "there is nothing there" produce identical downstream behaviour if they are conflated,
    and only one of them is a reason to do nothing (FR-009).
    """
    failures = state.consecutive_failures + 1
    backoff = min(2**failures, MAX_BACKOFF_SECONDS)
    with db.transaction(conn):
        db.save_poll_state(
            conn,
            PollState(
                repo_key=health.board_poll_key(trello.board_id),
                etag=None,
                last_polled_at=utcnow(),
                last_status=0,
                consecutive_failures=failures,
                backoff_until=_plus_seconds(backoff),
            ),
        )
        if failures >= FAILURE_ANOMALY_THRESHOLD:
            db.raise_anomaly(
                conn,
                kind="board_unreachable",
                entity_type="board",
                entity_id=trello.board_id,
                detail={
                    "consecutive_failures": failures,
                    "error": str(error),
                    "consequence": (
                        "no cards are being ingested. This is NOT an empty board — the "
                        "board could not be read at all"
                    ),
                },
            )
    audit.error(
        "trello.poll",
        error=error,
        entity_type="board",
        entity_id=trello.board_id,
        detail={"consecutive_failures": failures, "backoff_s": backoff},
    )
    return PollOutcome(board_id=trello.board_id, error=str(error))


def _plus_seconds(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def tracked_card_ids(
    conn: sqlite3.Connection, *, board_id: str, dry_run: bool
) -> dict[str, int]:
    """Card id → row id for every tracked card on this board, at this effect level."""
    rows = conn.execute(
        "SELECT id, card_id FROM cards WHERE board_id = ? AND dry_run = ?",
        (board_id, int(dry_run)),
    ).fetchall()
    return {row["card_id"]: row["id"] for row in rows}


# -- repository resolution (R8) ---------------------------------------------

#: A GitHub URL naming a repository, in any of the shapes a pasted link takes.
_URL_REF = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?(?=[/#?\s)\]]|$)"
)

#: A bare ``owner/name``. **This pattern is the reason R8 exists.** On its own it matches
#: ``src/robot_army``, ``docs/roadmap.md``, and any two-segment path in a pasted log — and
#: a card description is semi-untrusted text that may be pasted from one. It is never used
#: to *select* a repository, only to propose a candidate that the configured set must then
#: confirm.
_BARE_REF = re.compile(r"(?<![\w/.-])([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)(?![\w/.-])")

#: An absolute or ``~``-relative filesystem path, matched against configured clone paths.
_PATH_REF = re.compile(r"(?<!\S)(~?/[^\s'\"`,;)\]]+)")


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a card's text says about which repository it is for.

    ``resolvable`` means exactly one distinct configured repository survived. Zero and two
    are both unresolvable, and the ``reason`` distinguishes them — because "you named
    nothing I know" and "you named two" need different edits from the author (FR-012).
    """

    repo_key: str | None
    reason: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def resolvable(self) -> bool:
        return self.repo_key is not None


def resolve_repository(title: str, body: str, config: Config) -> Resolution:
    """Find the one configured repository a card names, or say why there isn't one.

    Three forms are scanned — a ``github.com/<owner>/<name>`` URL, a bare
    ``<owner>/<name>``, and a filesystem path — and **every candidate is filtered against
    the configured repositories before it counts**. That filter is the whole security
    argument: an unknown reference cannot select anything, so the worst case for a card
    full of pasted log output is ``needs_info``, which is the safe direction. Filing an
    issue in a repository named by a stray path fragment is the failure worth engineering
    against.

    Two references to the same repository are one reference: the set is deduplicated by
    resolved key before it is counted, so a card that pastes a URL *and* names the local
    path is resolvable rather than ambiguous.
    """
    text = f"{title}\n{body}"
    found: dict[str, str] = {}  # repo key → the text that produced it

    for owner, name in _URL_REF.findall(text):
        _offer(found, f"{owner}/{name}", config, f"github.com/{owner}/{name}")
    for owner, name in _BARE_REF.findall(text):
        _offer(found, f"{owner}/{name}", config, f"{owner}/{name}")
    for raw in _PATH_REF.findall(text):
        key = _key_for_path(raw, config)
        if key is not None:
            found.setdefault(key, raw)

    if len(found) == 1:
        key = next(iter(found))
        return Resolution(repo_key=key, candidates=(key,))
    if not found:
        return Resolution(
            repo_key=None,
            reason=(
                "no configured repository could be identified from this card. Name one by "
                "its GitHub URL, its owner/name, or its local path — configured: "
                f"{', '.join(sorted(config.repos)) or 'none'}"
            ),
        )
    keys = tuple(sorted(found))
    return Resolution(
        repo_key=None,
        reason=(
            f"this card names {len(keys)} configured repositories ({', '.join(keys)}); "
            "it must name exactly one"
        ),
        candidates=keys,
    )


def _offer(found: dict[str, str], candidate: str, config: Config, seen_as: str) -> None:
    """Accept a candidate only if it **is** a configured repository key, exactly.

    Exactly, and not by last segment. A ``demo`` suffix rule would let ``vendor/demo`` or
    ``demos/demo`` in a pasted log select the ``you/demo`` repository, which is precisely
    the accident R8 exists to prevent — and it would buy nothing, because a repository key
    is ``owner/name`` throughout this project (``[repos."you/example-repo"]``, and
    ``GitHubReader._repo_path`` splits on the slash to build an API path).
    """
    if candidate in config.repos:
        found.setdefault(candidate, seen_as)


def _key_for_path(raw: str, config: Config) -> str | None:
    """Map a filesystem path onto a configured repository, or ``None``.

    A path *inside* a configured clone counts as naming it: the author pasting
    ``/home/me/git/demo/src/thing.py`` plainly means ``demo``. The comparison is on
    resolved paths so that a symlinked or ``~``-relative spelling does not miss.
    """
    try:
        path = Path(raw).expanduser()
    except (OSError, ValueError):
        return None
    for key, repo in config.repos.items():
        try:
            if path == repo.path or repo.path in path.parents:
                return key
        except (OSError, ValueError):  # pragma: no cover - malformed path
            continue
    return None


# -- what a card becomes (FR-013, FR-014, FR-016) ---------------------------

#: The marker comment's fixed prefix. Written by us and read only by us, so its format is
#: ours to keep stable — and it is a **module constant** precisely so the writer and the
#: recovery reader cannot drift apart into two spellings of the same convention.
MARKER_PREFIX = "🤖 robot-army filed this card as "

#: How much of a card's description is carried into the issue body. A card is a note, not
#: a document, and an issue that is mostly quoted log output helps nobody.
MAX_BODY_CHARS = 8000


def marker_comment(issue_url: str) -> str:
    """The comment that links a card to its issue, and doubles as R7's recovery marker."""
    return f"{MARKER_PREFIX}{issue_url}"


def issue_url_from_marker(comment: str) -> str | None:
    """Read a marker back. Matched **by prefix**, never parsed.

    Prefix matching is the whole discipline: a parser would have opinions about the URL's
    shape, and a marker written by an older build would stop being readable exactly when
    it was needed — during a recovery, which is the one time this path runs at all.
    """
    text = comment.strip()
    if not text.startswith(MARKER_PREFIX):
        return None
    url = text[len(MARKER_PREFIX) :].strip()
    return url or None


def compose_issue(card_title: str, card_body: str, card_url: str) -> tuple[str, str]:
    """Turn a card into an issue title and body.

    **The card's text is data, never instruction** (FR-013). The description is carried as
    a quoted block and is never interpreted as configuration, command, or directive by
    anything in this system — and the quoting is what tells the *reader of the issue* the
    same thing. A card description is semi-untrusted text; an issue body that presented it
    as the system's own words would be inviting whoever reads it next to act on it.

    The body **always** contains the card's URL. R6's crash recovery matches on it, and
    FR-014 requires it independently — two reasons, so removing it breaks twice.
    """
    title = card_title.strip() or "(untitled card)"
    body = (card_body or "").strip()
    truncated = len(body) > MAX_BODY_CHARS
    if truncated:
        body = body[:MAX_BODY_CHARS]
    quoted = (
        "\n".join(f"> {line}" for line in body.splitlines()) if body else "> _(no description)_"
    )

    parts = [
        f"Filed by robot-army from a card on the intake board: {card_url}",
        "",
        "The card's description, quoted verbatim and **not** interpreted as instructions:",
        "",
        quoted,
    ]
    if truncated:
        parts += ["", f"_(the card's description was truncated at {MAX_BODY_CHARS} characters)_"]
    parts += [
        "",
        "---",
        "",
        "This issue is deliberately **unlabelled**. Nothing runs until you label it "
        "yourself — that is the human gate, and robot-army cannot apply the label.",
        "",
        f"Card: {card_url}",
    ]
    return title, "\n".join(parts)


# -- evaluation and creation (R6, R7, §11) ----------------------------------

#: Consecutive creation failures for one card before an anomaly is raised. Lower than the
#: board-poll threshold: a board that will not answer is usually a network blip, while an
#: issue that will not be created is usually a permission or configuration problem, and
#: those do not fix themselves.
CREATE_ANOMALY_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one card's evaluation did, for the caller's summary and for tests."""

    card_id: str
    action: str
    repo_key: str | None = None
    issue_number: int | None = None
    reason: str | None = None


def evaluate_card(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card_row_id: int,
    dry_run: bool,
    board_card: Any = None,
    forced: bool = False,
) -> Verdict:
    """Decide what one tracked card should become, and do it.

    **The order of the checks below is the milestone's central invariant** (§11, R7), and
    each step is placed where it is for a reason that a later edit could easily undo:

    1. A ``linked`` row means we are done. Nothing is read from the board — in particular
       the card's *comments* are not read, which is §11's "don't parse comments as the
       authoritative source in normal operation" expressed as a call-site rule.
    2. With no mapping at all, our own marker comment may still exist — the database-loss
       case (R7). Restoring from it comes **before** any creation, so a lost database
       cannot produce a second issue.
    3. Only then does resolution decide between ``needs_info`` and the creation sequence.
    """
    card = db.get_card_by_id(conn, card_row_id)
    if card is None:
        raise LookupError(f"no card with row id {card_row_id}")

    # (1) The mapping check, first and cheapest. A linked card is finished.
    if card.state is CardState.LINKED:
        if card.comment_posted_at is None:
            # The one loose end a linked row can have: killed between writing the mapping
            # and commenting on the card (R6, seam three).
            return _post_marker_comment(
                conn, boundaries=boundaries, audit=audit, card=card, dry_run=dry_run
            )
        return Verdict(card.card_id, "already_linked", card.repo_key, card.issue_number)

    if card.state is CardState.DROPPED:
        return Verdict(card.card_id, "dropped")

    # (2) An unfinished creation is resolved by R6's listing rather than re-created.
    if card.state is CardState.CREATING:
        return _resume_creation(
            conn, boundaries=boundaries, audit=audit, config=config, card=card, dry_run=dry_run
        )

    # (3) The database-loss path: no mapping, but our marker may be on the card.
    restored = _restore_from_marker(
        conn, boundaries=boundaries, audit=audit, config=config, card=card, dry_run=dry_run
    )
    if restored is not None:
        return restored

    # A held card is re-evaluated only when the author has actually touched it (FR-023),
    # and "touched" is the board's own activity stamp differing from our stored baseline.
    # Without this the system would re-resolve every held card every poll forever — and,
    # far worse, R9's trap would be live: **our own comment changes that stamp**, so a
    # baseline we never rebase makes every poll look like an edit the author made.
    if (
        card.state is CardState.NEEDS_INFO
        and not forced
        and board_card is not None
        and board_card.last_activity == card.last_activity
    ):
        return Verdict(card.card_id, "unchanged", reason=card.reason)

    resolution = resolve_repository(card.title, card.body, config)
    audit.record(
        "trello.evaluated",
        outcome="ok",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail={
            "resolvable": resolution.resolvable,
            "repo_key": resolution.repo_key,
            "candidates": list(resolution.candidates),
            "reason": resolution.reason,
        },
        dry_run=dry_run,
    )
    if not resolution.resolvable:
        return _hold_for_info(
            conn,
            boundaries=boundaries,
            audit=audit,
            card=card,
            reason=resolution.reason or "unresolvable",
            dry_run=dry_run,
            activity=getattr(board_card, "last_activity", None),
        )

    return _create_issue_for_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card=card,
        repo_key=resolution.repo_key,
        dry_run=dry_run,
    )


def _create_issue_for_card(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card: Any,
    repo_key: str,
    dry_run: bool,
) -> Verdict:
    """R6's four-step sequence, **each step in its own transaction**.

    1. Commit the ``creating`` intent with the resolved repository and a timestamp.
    2. Create the issue.
    3. Write the mapping and advance to ``linked``.
    4. Comment on the card, and record that we did.

    Separate transactions are the point, not an oversight: every seam between them is
    separately resumable, and a single transaction spanning the network call would either
    hold a write lock across it or roll back an issue that already exists. The dangerous
    window is between 2 and 3 — the issue exists and nothing local knows it — and the
    intent row plus the card URL in the issue body is what makes that window observable
    afterwards.
    """
    # (1) Intent. Committed before anything leaves the process.
    with db.transaction(conn):
        transition_card(
            conn,
            audit,
            card_row_id=card.id,
            target=CardState.CREATING,
            reason=f"resolved to {repo_key}",
            extra_columns={"repo_key": repo_key, "reason": None},
        )

    return _perform_creation(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        card=db.get_card_by_id(conn, card.id),
        dry_run=dry_run,
    )


def _perform_creation(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card: Any,
    dry_run: bool,
) -> Verdict:
    """Steps 2 through 4, entered with the intent row already committed."""
    repo_key = card.repo_key
    title, body = compose_issue(card.title, card.body, card.card_url)

    # (2) The issue itself. Failure leaves the row in `creating` with its reason, to be
    # retried on a later pass — the intent stands, and R6's recovery runs against it.
    try:
        issue = boundaries.issue_writer.create_issue(repo_key, title, body)
    except Exception as exc:  # noqa: BLE001 - every failure mode is retried identically
        return _record_create_failure(conn, audit, card=card, error=exc, dry_run=dry_run)

    # (3) The mapping. This is the row §11's invariant is about, and the unique index is
    # what makes a skipped check loud rather than duplicating.
    with db.transaction(conn):
        transition_card(
            conn,
            audit,
            card_row_id=card.id,
            target=CardState.LINKED,
            reason=f"issue {repo_key}#{issue.number} created",
            extra_columns={
                "issue_number": issue.number,
                "issue_url": issue.url,
                "reason": None,
                "create_failures": 0,
            },
        )

    # (4) The card comment, which is also R7's recovery marker.
    _post_marker_comment(
        conn,
        boundaries=boundaries,
        audit=audit,
        card=db.get_card_by_id(conn, card.id),
        dry_run=dry_run,
    )
    return Verdict(card.card_id, "created", repo_key, issue.number)


def _record_create_failure(
    conn: sqlite3.Connection,
    audit: AuditLog,
    *,
    card: Any,
    error: Exception,
    dry_run: bool,
) -> Verdict:
    """Leave the card in ``creating`` with a reason, and **never** comment on it (FR-019).

    A comment claiming an issue exists when one does not is worse than silence: the author
    would follow a link to nothing, and the card would look handled. The failure is
    visible in ``robot-army cards``, in the log, and — past the threshold — as an anomaly.
    """
    failures = (card.create_failures or 0) + 1
    reason = f"could not create the issue in {card.repo_key}: {error}"
    with db.transaction(conn):
        db.update_card_columns(conn, card.id, reason=reason, create_failures=failures)
        if failures >= CREATE_ANOMALY_THRESHOLD:
            db.raise_anomaly(
                conn,
                kind="card_create_failing",
                entity_type="card",
                entity_id=card.card_id,
                detail={
                    "card_url": card.card_url,
                    "repo_key": card.repo_key,
                    "attempts": failures,
                    "error": str(error),
                },
            )
    audit.error(
        "trello.issue.create",
        error=error,
        entity_type="card",
        entity_id=card.card_id,
        detail={"repo_key": card.repo_key, "attempts": failures},
        dry_run=dry_run,
    )
    return Verdict(card.card_id, "create_failed", card.repo_key, reason=reason)


def _post_marker_comment(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    card: Any,
    dry_run: bool,
) -> Verdict:
    """Step 4, also reachable on its own as the retry for an interrupted step 3→4.

    Checks the board for an existing marker **first**, so a retry cannot double-post. That
    read is the one place a linked card's comments are fetched, and it happens only when
    ``comment_posted_at`` is NULL — i.e. only when we know we may not have finished.
    """
    if card.comment_posted_at is not None:
        return Verdict(card.card_id, "already_linked", card.repo_key, card.issue_number)

    writer = boundaries.card_writer
    reader = boundaries.card_reader
    if writer is None or reader is None:  # pragma: no cover - guarded by the caller
        return Verdict(card.card_id, "no_board", card.repo_key, card.issue_number)

    try:
        existing = any(issue_url_from_marker(text) for text in reader.card_comments(card.card_id))
    except TransportError as exc:
        # Not knowing whether a marker exists is a reason to wait, not to post: posting
        # blind is how the one comment becomes two.
        audit.error(
            "trello.card.comment",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            detail={"stage": "checking for an existing marker"},
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "comment_deferred", card.repo_key, card.issue_number)

    if existing:
        with db.transaction(conn):
            db.update_card_columns(conn, card.id, comment_posted_at=utcnow())
        audit.record(
            "trello.recovered",
            outcome="ok",
            entity_type="card",
            entity_id=card.card_id,
            target=card.card_id,
            detail={"path": "marker already present", "issue_url": card.issue_url},
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "comment_already_present", card.repo_key, card.issue_number)

    try:
        result = writer.comment(card.card_id, marker_comment(card.issue_url or ""))
    except TransportError as exc:
        audit.error(
            "trello.card.comment",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "comment_deferred", card.repo_key, card.issue_number)

    with db.transaction(conn):
        _record_write(conn, card.id, result, comment_posted_at=utcnow())
    # "comment_posted", **not** "created". This function is reachable twice over: once as
    # step 4 of a creation, where the caller reports the creation itself, and once on its
    # own as the retry for a card whose comment was deferred. Reusing "created" for both
    # made that retry increment `issues_created` in the cycle report, so a pass that filed
    # no issue at all could claim to have filed one — a report that overstates an outward
    # action is worse than one that omits it.
    return Verdict(card.card_id, "comment_posted", card.repo_key, card.issue_number)


def _record_write(conn: sqlite3.Connection, card_row_id: int, result: Any, **columns: Any) -> None:
    """Record one of our own board writes, refreshing the activity baseline with it (R9).

    The baseline moves in the **same transaction** that records the write. Our comment just
    changed the card's ``dateLastActivity``, which is the rescan trigger — leave the two
    apart and the next poll sees an edit nobody made, re-evaluates, and does so forever.

    A writer that could not re-read returns ``None``, and then the baseline is deliberately
    left alone: one redundant re-evaluation is idempotent and posts no comment, which is a
    far smaller problem than recording a stamp the board does not have.
    """
    if getattr(result, "last_activity", None):
        columns["last_activity"] = result.last_activity
    db.update_card_columns(conn, card_row_id, **columns)


# -- holding a card that does not say enough (US2, FR-021, FR-022) ----------


def _hold_for_info(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    card: Any,
    reason: str,
    dry_run: bool,
    activity: str | None = None,
) -> Verdict:
    """Park an unresolvable card with a reason, and comment **at most once per reason**.

    FR-022's whole implementation is the comparison between ``reason`` and
    ``commented_reason``: comment when they differ, stay silent when they do not. A card
    held for weeks therefore accumulates one comment, not one per poll — and a card whose
    problem *changes* (from "names nothing" to "names two") gets a second comment saying
    so, which is the case a naive "have we commented?" flag would get wrong.
    """
    if card.state is not CardState.NEEDS_INFO:
        with db.transaction(conn):
            transition_card(
                conn,
                audit,
                card_row_id=card.id,
                target=CardState.NEEDS_INFO,
                reason=reason,
                extra_columns={"reason": reason},
            )
    else:
        with db.transaction(conn):
            db.update_card_columns(conn, card.id, reason=reason)

    audit.record(
        "trello.needs_info",
        outcome="ok",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail={"reason": reason, "commented_reason": card.commented_reason},
        dry_run=dry_run,
    )

    if card.commented_reason == reason:
        # Silent, and the baseline advances to what the board says now — so the edit that
        # brought us here is not seen again as a fresh one on the next pass.
        if activity and activity != card.last_activity:
            with db.transaction(conn):
                db.update_card_columns(conn, card.id, last_activity=activity)
        return Verdict(card.card_id, "held", reason=reason)

    writer = boundaries.card_writer
    if writer is None:  # pragma: no cover - guarded by the caller
        return Verdict(card.card_id, "held", reason=reason)
    try:
        result = writer.comment(card.card_id, _needs_info_comment(reason))
    except TransportError as exc:
        # The hold stands even if we could not say so out loud. `commented_reason` is
        # left unchanged, so the next pass tries again — and the card is still listed in
        # `robot-army cards` with its reason, which is where FR-047 says it must be.
        audit.error(
            "trello.card.comment",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "held", reason=reason)

    with db.transaction(conn):
        _record_write(conn, card.id, result, commented_reason=reason)
    return Verdict(card.card_id, "held_and_commented", reason=reason)


def _needs_info_comment(reason: str) -> str:
    return (
        "🤖 robot-army could not file an issue for this card yet.\n\n"
        f"{reason}\n\n"
        "Edit the card to say which repository this is for, and it will be picked up "
        "automatically on the next pass — no other action is needed."
    )


# -- recovery (US4, R6, R7) -------------------------------------------------


def _resume_creation(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card: Any,
    dry_run: bool,
) -> Verdict:
    """Resolve a row left in ``creating``, which means the intent outlived the process.

    R6's recovery: list issues in the target repository created since the intent
    timestamp, authored by us, and adopt one whose body contains this card's URL. If one
    is found the create already happened and is adopted; if not, it never happened and
    step 2 is retried.

    **Listing, never search.** GitHub's search index is eventually consistent by minutes,
    so an issue created two seconds before the crash may be invisible to it — which would
    produce precisely the duplicate this whole mechanism exists to prevent.
    """
    adopted = _find_orphaned_issue(
        boundaries=boundaries, audit=audit, config=config, card=card, dry_run=dry_run
    )
    if adopted is None:
        return _perform_creation(
            conn,
            boundaries=boundaries,
            audit=audit,
            config=config,
            card=card,
            dry_run=dry_run,
        )

    with db.transaction(conn):
        transition_card(
            conn,
            audit,
            card_row_id=card.id,
            target=CardState.LINKED,
            reason=f"adopted existing issue {card.repo_key}#{adopted.number}",
            extra_columns={
                "issue_number": adopted.number,
                "issue_url": adopted.url,
                "reason": None,
            },
        )
    audit.record(
        "trello.recovered",
        outcome="ok",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail={
            "path": "issue listing since intent_at",
            "repo_key": card.repo_key,
            "issue_number": adopted.number,
            "intent_at": card.intent_at,
        },
        dry_run=dry_run,
    )
    _post_marker_comment(
        conn,
        boundaries=boundaries,
        audit=audit,
        card=db.get_card_by_id(conn, card.id),
        dry_run=dry_run,
    )
    return Verdict(card.card_id, "adopted", card.repo_key, adopted.number)


def _find_orphaned_issue(
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card: Any,
    dry_run: bool,
) -> Any:
    """The issue this card's interrupted attempt may already have created, or ``None``."""
    if not card.repo_key or not card.intent_at:
        return None
    try:
        issues = boundaries.issue_reader.list_issues_since(
            card.repo_key, card.intent_at, author=config.github.author or None
        )
    except TransportError as exc:
        # Not knowing is a reason to wait, not to create: creating blind is exactly the
        # duplicate this path exists to prevent.
        audit.error(
            "trello.recovered",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            detail={"stage": "listing issues since intent_at"},
            dry_run=dry_run,
        )
        raise
    for issue in issues:
        if card.card_url and card.card_url in (issue.body or ""):
            return issue
    return None


def _restore_from_marker(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    card: Any,
    dry_run: bool,
) -> Verdict | None:
    """Rebuild a lost mapping from our own comment on the card (R7, FR-034).

    Reached **only** when no mapping row exists, which is the whole ordering rule: with a
    mapping present the board's comments are never read, and with it gone the marker
    restores it one card at a time — no bulk rebuild command to write, and none to keep
    working.

    Returns ``None`` when there is nothing to restore, so the caller falls through to
    ordinary evaluation.
    """
    reader = boundaries.card_reader
    if reader is None:  # pragma: no cover - guarded by the caller
        return None
    try:
        comments = reader.card_comments(card.card_id)
    except TransportError as exc:
        # Creating without having checked is how a lost database becomes a second issue.
        audit.error(
            "trello.recovered",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            detail={"stage": "reading card comments for a marker"},
            dry_run=dry_run,
        )
        raise
    for text in comments:
        url = issue_url_from_marker(text)
        if url is None:
            continue
        repo_key, number = _split_issue_url(url)
        if repo_key is None or number is None:
            continue
        with db.transaction(conn):
            transition_card(
                conn,
                audit,
                card_row_id=card.id,
                target=CardState.CREATING,
                reason="restoring a mapping from the card's marker comment",
                extra_columns={"repo_key": repo_key},
            )
            transition_card(
                conn,
                audit,
                card_row_id=card.id,
                target=CardState.LINKED,
                reason=f"marker comment names {repo_key}#{number}",
                extra_columns={
                    "issue_number": number,
                    "issue_url": url,
                    # The marker's existence *is* the evidence the comment was posted.
                    "comment_posted_at": utcnow(),
                    "reason": None,
                },
            )
        audit.record(
            "trello.recovered",
            outcome="ok",
            entity_type="card",
            entity_id=card.card_id,
            target=card.card_id,
            detail={"path": "marker comment", "repo_key": repo_key, "issue_number": number},
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "restored", repo_key, number)
    return None


_ISSUE_URL = re.compile(r"github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/issues/(\d+)")


def _split_issue_url(url: str) -> tuple[str | None, int | None]:
    """Pull ``owner/name`` and the number out of an issue URL we wrote ourselves."""
    matched = _ISSUE_URL.search(url)
    if not matched:
        return None, None
    return matched.group(1), int(matched.group(2))


# -- the cycle, end to end --------------------------------------------------

#: Verdict action → the ``PollOutcome`` counter it advances. A table rather than a chain
#: of ``if``s, so a new action that nobody counted shows up as a missing key here.
_VERDICT_COUNTER: dict[str, str] = {
    "created": "issues_created",
    "adopted": "recovered",
    "restored": "recovered",
    # Both of these are a deferred step 4 being finished, not an issue being filed.
    "comment_posted": "recovered",
    "comment_already_present": "recovered",
    "held": "held",
    "held_and_commented": "held",
    "dropped": "dropped",
    "create_failed": "failed",
}


def run_cycle(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    status: BoardStatus,
    dry_run: bool,
    forced: bool = False,
) -> PollOutcome:
    """One complete board pass: recover, poll, then evaluate every tracked card.

    Recovery runs at the **head** of every cycle, not only at startup (T073): an
    interruption is then resolved on the next pass rather than waiting for a restart that
    may be days away.

    ``forced`` re-evaluates every held card whether or not the author has touched it, which
    is what ``robot-army rescan`` asks for. Ordinary passes leave held cards alone until
    their activity stamp moves, because re-resolving unchanged text every five minutes
    forever is work that cannot produce a different answer.
    """
    # Recovery first, at the head of every cycle. A row left in ``creating`` may already
    # have an issue, and evaluating it as new before resolving that is exactly how the
    # duplicate §11 forbids gets created.
    recovered: dict[str, int] = {}
    if status.ok:
        recovered = recovery_sweep(
            conn, boundaries=boundaries, audit=audit, config=config, dry_run=dry_run
        )

    outcome = poll_board(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        status=status,
        dry_run=dry_run,
    )
    if outcome.error or outcome.skipped_reason:
        return outcome

    trello = config.trello
    assert trello is not None  # noqa: S101 - poll_board would have skipped otherwise
    counts: dict[str, int] = {}
    evaluated = 0

    present = {card.card_id: card for card in outcome.cards}
    dropped = _reconcile_board_contents(
        conn, audit=audit, config=config, present=present, dry_run=dry_run
    )

    for card in db.list_cards(conn, include_simulated=True, board_id=trello.board_id):
        if card.dry_run != dry_run:
            continue
        if card.state is CardState.DROPPED:
            continue
        try:
            verdict = evaluate_card(
                conn,
                boundaries=boundaries,
                audit=audit,
                config=config,
                card_row_id=card.id,
                dry_run=dry_run,
                board_card=present.get(card.card_id),
                forced=forced,
            )
        except TransportError as exc:
            # One card's board or issue failure must not stop the rest, exactly as one
            # repository's failure does not stop ``poll_all``.
            audit.error(
                "trello.evaluated",
                error=exc,
                entity_type="card",
                entity_id=card.card_id,
                dry_run=dry_run,
            )
            counts["failed"] = counts.get("failed", 0) + 1
            continue
        evaluated += 1
        counter = _VERDICT_COUNTER.get(verdict.action)
        if counter:
            counts[counter] = counts.get(counter, 0) + 1

    return PollOutcome(
        board_id=outcome.board_id,
        found=outcome.found,
        created=outcome.created,
        evaluated=evaluated,
        issues_created=counts.get("issues_created", 0),
        held=counts.get("held", 0),
        # Counted where cards are actually dropped, which is the reconcile pass above.
        # ``evaluate_card`` reports a "dropped" verdict too, but the loop below skips rows
        # already in that state before reaching it — so reading the count from `counts`
        # alone reported 0 however many cards had just left the board.
        dropped=dropped + counts.get("dropped", 0),
        recovered=counts.get("recovered", 0) + sum(recovered.values()),
        failed=counts.get("failed", 0),
    )


def _reconcile_board_contents(
    conn: sqlite3.Connection,
    *,
    audit: AuditLog,
    config: Config,
    present: dict[str, Any],
    dry_run: bool,
) -> int:
    """Notice cards that have left the board, and refresh the ones still on it.

    Returns the number of cards **transitioned to** ``dropped`` — not counting a linked
    card that was merely archived, which keeps its mapping and is a different event.

    ``poll`` returns only tagged, unarchived cards, so a card that has lost its tag, been
    archived, or been deleted is simply *absent* from the listing — which is the signal
    FR-025 acts on. A tracked card that no longer appears is dropped **unless it is
    linked**, in which case ``archived_at`` is recorded and the mapping is kept: dropping
    it would let a re-tagged card create a second issue.
    """
    trello = config.trello
    if trello is None:  # pragma: no cover - guarded by the caller
        return 0

    dropped = 0
    for row in db.list_cards(conn, include_simulated=True, board_id=trello.board_id):
        if row.dry_run != dry_run:
            continue
        card = present.get(row.card_id)
        if card is None:
            dropped += _leave_board(conn, audit, row=row, dry_run=dry_run)
            continue
        _refresh_tracked_card(conn, row=row, card=card)
    return dropped


def _leave_board(
    conn: sqlite3.Connection, audit: AuditLog, *, row: Any, dry_run: bool
) -> int:
    """A tracked card that is no longer tagged, or is archived or deleted (FR-025).

    Returns ``1`` when the card was actually dropped this pass, so the cycle can report it.
    A linked card that was archived returns ``0``: it keeps its mapping and is a different
    event, and counting it as dropped would say the mapping had gone when it had not.
    """
    if row.state is CardState.LINKED:
        if row.archived_at is None:
            with db.transaction(conn):
                db.update_card_columns(conn, row.id, archived_at=utcnow())
            audit.record(
                "trello.dropped",
                outcome="ok",
                entity_type="card",
                entity_id=row.card_id,
                target=row.card_id,
                detail={
                    "state": str(row.state),
                    "note": (
                        "mapping kept — dropping a linked card would let a re-tagged card "
                        "create a second issue"
                    ),
                },
                dry_run=dry_run,
            )
        return 0
    if row.state in (CardState.DROPPED, CardState.CREATING):
        # `creating` has no exit to `dropped` by design: an issue may already exist for it,
        # and R6's recovery must still run against the intent.
        return 0
    with db.transaction(conn):
        transition_card(
            conn,
            audit,
            card_row_id=row.id,
            target=CardState.DROPPED,
            reason="the card is no longer tagged, or was archived or deleted",
            extra_columns={"archived_at": utcnow()},
        )
    audit.record(
        "trello.dropped",
        outcome="ok",
        entity_type="card",
        entity_id=row.card_id,
        target=row.card_id,
        detail={"state": str(row.state)},
        dry_run=dry_run,
    )
    return 1


def _refresh_tracked_card(conn: sqlite3.Connection, *, row: Any, card: Any) -> None:
    """Carry the board's current title, body and list onto the tracked row.

    The activity baseline is **not** refreshed here, and that omission is the whole of
    FR-023: the difference between the stored baseline and the board's current stamp is
    what tells the next evaluation that the author edited the card. Overwriting it during
    a poll would erase the signal before anything could act on it.
    """
    changed: dict[str, Any] = {}
    if card.title != row.title:
        changed["title"] = card.title
    if card.body != row.body:
        changed["body"] = card.body
    if card.list_id and card.list_id != row.placed_list_id and row.origin_list_id is None:
        changed["origin_list_id"] = card.list_id
    if not changed:
        return
    with db.transaction(conn):
        db.update_card_columns(conn, row.id, **changed)


# -- the card's lifecycle on the board (US3, FR-027 through FR-031) ---------


def on_session_active(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    issue_number: int,
    dry_run: bool,
) -> Verdict | None:
    """Move the card to the in-progress list, once a session is **confirmed** (FR-027).

    Called from the point where a session is known to be running, not from where one is
    launched. M0 F16 measured the launch call's success as meaningless on its own, and a
    card that says "in progress" for a session that never started is exactly the lie this
    story exists to remove.
    """
    return _move_card_for_issue(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=repo_key,
        issue_number=issue_number,
        dry_run=dry_run,
        # The list is named, not resolved, so nothing reaches the board until a card is
        # known to exist. Python evaluates call arguments eagerly, so resolving it here
        # would fetch the board for every dispatched item — including the great majority
        # that never came from a card at all.
        which="in_progress",
        reason="a session is running for this card's issue",
        remember_origin=True,
    )


def on_issue_closed(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    issue_number: int,
    dry_run: bool,
) -> Verdict | None:
    """Move the card to the done list with an outcome comment (FR-028)."""
    return _move_card_for_issue(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=repo_key,
        issue_number=issue_number,
        dry_run=dry_run,
        which="done",
        reason=f"issue {repo_key}#{issue_number} is closed",
        comment=f"🤖 robot-army: issue {repo_key}#{issue_number} is closed.",
    )


def on_work_abandoned(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    issue_number: int,
    reason: str,
    dry_run: bool,
) -> Verdict | None:
    """Return the card to the list it came from, with a comment naming why (FR-029).

    Without this a card sits in the in-progress list claiming to be busy when nothing is,
    which is worse than never having moved it: the board would be confidently wrong rather
    than merely silent.
    """
    card = _card_for_issue(conn, config=config, repo_key=repo_key, issue_number=issue_number,
                           dry_run=dry_run)
    if card is None or card.origin_list_id is None:
        return None
    return _move_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        card=card,
        target=card.origin_list_id,
        reason=reason,
        comment=f"🤖 robot-army: work on this card stopped — {reason}. Returned to where it was.",
        dry_run=dry_run,
    )


def _list_id(config: Config, boundaries: Boundaries, which: str) -> str | None:
    """Resolve a configured lifecycle list name to its board id.

    The names were validated at startup (R11) and the ids resolved then; this re-reads them
    from the reader rather than threading the startup status through four call sites in
    three modules. The reader memoises ``board_info`` for the life of the process, so this
    is one board round trip per run rather than one per move — which is what R10 means by
    checking "once per process", and what this docstring used to claim without it being
    true. A board whose lists vanished mid-run is a condition the caller handles anyway.
    """
    trello = config.trello
    reader = boundaries.card_reader
    if trello is None or reader is None:
        return None
    name = trello.in_progress_list if which == "in_progress" else trello.done_list
    try:
        return reader.board_info().lists.get(name)
    except TransportError:
        return None


def _card_for_issue(
    conn: sqlite3.Connection,
    *,
    config: Config,
    repo_key: str,
    issue_number: int,
    dry_run: bool,
) -> Any:
    """The card an issue came from, or ``None`` if it did not come from one."""
    if config.trello is None:
        return None
    return db.find_card_by_issue(
        conn, repo_key=repo_key, issue_number=issue_number, dry_run=dry_run
    )


def _move_card_for_issue(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    issue_number: int,
    dry_run: bool,
    which: str,
    reason: str,
    comment: str | None = None,
    remember_origin: bool = False,
) -> Verdict | None:
    """Move the card an issue came from, if it came from one.

    **The order of these two lookups is a cost decision.** The card lookup is one indexed
    local query; resolving a list name is a board round trip. Both of these entry points
    sit in per-item hot paths — every dispatched item, every closed issue — and the great
    majority of those never came from a card, so the cheap local answer settles it first
    and the board is not touched at all.
    """
    card = _card_for_issue(
        conn, config=config, repo_key=repo_key, issue_number=issue_number, dry_run=dry_run
    )
    if card is None:
        return None
    target = _list_id(config, boundaries, which)
    if target is None:
        return None
    return _move_card(
        conn,
        boundaries=boundaries,
        audit=audit,
        card=card,
        target=target,
        reason=reason,
        comment=comment,
        dry_run=dry_run,
        remember_origin=remember_origin,
    )


def _move_card(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    card: Any,
    target: str,
    reason: str,
    comment: str | None,
    dry_run: bool,
    remember_origin: bool = False,
) -> Verdict:
    """Move one card, unless the author moved it first (FR-030, R12).

    **The freshness re-read is not optional.** The board is the author's own working
    surface, and a system that silently drags a card back to where it thinks it belongs is
    fighting its user — an argument the author will lose slowly and annoyingly. So the
    card's current list is read immediately before the move, and a list we did not put it
    in means we do not move it: we comment saying what we would have done, and leave it
    where the author put it.

    ``pending_move_to`` is written **before** the attempt, so an interrupted move of ours
    is not mistaken for a human one on the next pass: without it, killed after the move
    landed but before it was recorded, we would find the card somewhere we do not think we
    put it and conclude the author moved it.
    """
    reader = boundaries.card_reader
    writer = boundaries.card_writer
    if reader is None or writer is None:  # pragma: no cover - guarded by the caller
        return Verdict(card.card_id, "no_board")

    try:
        current = reader.get_card(card.card_id)
    except TransportError as exc:
        audit.error(
            "trello.card.move",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            detail={"stage": "reading the card's current list"},
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "move_deferred")
    if current is None:
        return Verdict(card.card_id, "card_gone")

    if current.list_id == target:
        # Already where we want it. Either we put it there, or the author did and agrees.
        # Either way there is nothing to do, and doing it anyway would be an API call and
        # a board event for no change.
        with db.transaction(conn):
            db.update_card_columns(
                conn, card.id, placed_list_id=target, pending_move_to=None
            )
        return Verdict(card.card_id, "move_unnecessary")

    if _moved_by_the_author(card, current):
        return _refuse_move(
            conn,
            boundaries=boundaries,
            audit=audit,
            card=card,
            current=current,
            target=target,
            reason=reason,
            dry_run=dry_run,
        )

    origin = card.origin_list_id
    if remember_origin and origin is None:
        origin = current.list_id

    # The intent, before the attempt. This is what makes an interrupted move ours.
    with db.transaction(conn):
        db.update_card_columns(
            conn, card.id, pending_move_to=target, **({"origin_list_id": origin} if origin else {})
        )

    audit.record(
        "trello.card.move",
        outcome="pending",
        kind="intent",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail={"from": current.list_id, "to": target, "reason": reason},
        dry_run=dry_run,
    )
    try:
        result = writer.move(card.card_id, target)
    except TransportError as exc:
        audit.error(
            "trello.card.move",
            error=exc,
            entity_type="card",
            entity_id=card.card_id,
            detail={"from": current.list_id, "to": target},
            dry_run=dry_run,
        )
        return Verdict(card.card_id, "move_deferred")

    with db.transaction(conn):
        _record_write(conn, card.id, result, placed_list_id=target, pending_move_to=None)
    audit.record(
        "trello.card.move",
        outcome="ok",
        kind="outcome",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail={"from": current.list_id, "to": target, "reason": reason},
        dry_run=dry_run,
    )

    if comment:
        try:
            commented = writer.comment(card.card_id, comment)
        except TransportError:
            # The move landed and is recorded. A missing outcome comment is cosmetic, and
            # failing the whole operation over it would be the wrong trade.
            return Verdict(card.card_id, "moved")
        with db.transaction(conn):
            _record_write(conn, card.id, commented)
    return Verdict(card.card_id, "moved")


def _moved_by_the_author(card: Any, current: Any) -> bool:
    """Is the card somewhere we did not put it?

    ``pending_move_to`` matching where the card actually is means **we** moved it and were
    killed before writing that down (R12) — not the author. Getting this backwards would
    make every interrupted move look like a human decision and freeze the card's lifecycle
    permanently.
    """
    if card.pending_move_to and current.list_id == card.pending_move_to:
        return False
    if card.placed_list_id is None:
        # We have never placed this card, so wherever it is, is where it started.
        return False
    return current.list_id != card.placed_list_id


def _refuse_move(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    card: Any,
    current: Any,
    target: str,
    reason: str,
    dry_run: bool,
) -> Verdict:
    """Say what would have been done, and do nothing (FR-030)."""
    detail = {
        "card_is_in": current.list_id,
        "we_last_placed_it_in": card.placed_list_id,
        "would_have_moved_to": target,
        "reason": reason,
    }
    audit.record(
        "trello.card.move_refused",
        outcome="ok",
        entity_type="card",
        entity_id=card.card_id,
        target=card.card_id,
        detail=detail,
        dry_run=dry_run,
    )
    writer = boundaries.card_writer
    body = (
        "🤖 robot-army did **not** move this card: you moved it since I last placed it, "
        "and the board is yours.\n\n"
        f"What I would have done: move it to the `{target}` list, because {reason}."
    )
    with db.transaction(conn):
        db.update_card_columns(conn, card.id, pending_move_to=None)
    if writer is None:  # pragma: no cover - guarded by the caller
        return Verdict(card.card_id, "move_refused")
    try:
        result = writer.comment(card.card_id, body)
    except TransportError:
        return Verdict(card.card_id, "move_refused")
    with db.transaction(conn):
        _record_write(conn, card.id, result)
    return Verdict(card.card_id, "move_refused")


# -- the recovery sweep (US4, T073, T074) -----------------------------------


def recovery_sweep(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    dry_run: bool,
) -> dict[str, int]:
    """Resolve what an interruption left behind, before anything new is evaluated.

    Run at daemon startup **and at the head of every board cycle**, so an interruption is
    resolved on the next pass rather than at the next restart — which may be days away, and
    a card sitting in ``creating`` for days is a card whose issue may already exist.

    Three conditions, each from data-model.md's interruption table:

    * A row in ``creating``: the intent outlived the process. R6's listing decides whether
      the issue exists.
    * A ``linked`` row with no ``comment_posted_at``: the mapping outlived the process.
      The comment is posted, after checking the board so a retry cannot double-post.
    * A ``linked`` row whose issue no longer resolves: deleted, transferred, or otherwise
      gone. The mapping is **kept** and an anomaly is raised (FR-037) — dropping it would
      let the card create a fresh issue, which is the failure §11 exists to prevent.
    """
    trello = config.trello
    if trello is None or boundaries.card_reader is None:
        return {}

    counts = {"resumed": 0, "commented": 0, "missing_issue": 0}
    rows = db.list_cards(conn, include_simulated=True, board_id=trello.board_id)
    for row in rows:
        if row.dry_run != dry_run:
            continue
        try:
            if row.state is CardState.CREATING:
                _resume_creation(
                    conn,
                    boundaries=boundaries,
                    audit=audit,
                    config=config,
                    card=row,
                    dry_run=dry_run,
                )
                counts["resumed"] += 1
            elif row.state is CardState.LINKED:
                if row.comment_posted_at is None:
                    _post_marker_comment(
                        conn,
                        boundaries=boundaries,
                        audit=audit,
                        card=row,
                        dry_run=dry_run,
                    )
                    counts["commented"] += 1
                # Skipped for simulated rows (FR-055), following the same rule
                # ``reconcile._resolve_closed_issues`` already applies. A simulated card's
                # issue number came from ``SimulatedIssueWriter`` and is a recognisable
                # fake, so asking the **real** reader about it is guaranteed to 404 — which
                # would file a ``card_issue_missing`` anomaly for every simulated card,
                # into the same operator-facing list as the real ones, and spend a GitHub
                # request to do it. That is the dry-run mode causing exactly the outward
                # effect it exists to avoid.
                #
                # This is a decision about *a simulated row*, not about which
                # implementation to call — the latter lives only in ``effects.py``.
                if not row.dry_run and _issue_has_vanished(
                    conn, boundaries=boundaries, audit=audit, card=row, dry_run=dry_run
                ):
                    counts["missing_issue"] += 1
        except TransportError as exc:
            # One card's recovery failing must not stop the rest, and must not be silent.
            audit.error(
                "trello.recovered",
                error=exc,
                entity_type="card",
                entity_id=row.card_id,
                dry_run=dry_run,
            )
    return counts


def _issue_has_vanished(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    card: Any,
    dry_run: bool,
) -> bool:
    """Has a linked card's issue been deleted, transferred, or otherwise lost (FR-037)?

    **The mapping is kept either way.** An anomaly names the card and the missing issue so
    a human can decide, because the alternatives are both wrong: dropping the mapping would
    let the next poll file a second issue, and creating one automatically would do it
    without anyone asking.

    ``TransportError`` propagates rather than counting as "vanished" — "I could not ask"
    and "it is gone" are different facts, and raising an anomaly on a network blip would
    train the reader to ignore them.
    """
    if not card.repo_key or card.issue_number is None:
        return False
    issue = boundaries.issue_reader.get_issue(card.repo_key, card.issue_number)
    if issue is not None:
        return False
    with db.transaction(conn):
        created = db.raise_anomaly(
            conn,
            kind="card_issue_missing",
            entity_type="card",
            entity_id=card.card_id,
            detail={
                "card_url": card.card_url,
                "issue": f"{card.repo_key}#{card.issue_number}",
                "issue_url": card.issue_url,
                "note": (
                    "the mapping is kept deliberately — dropping it would let this card "
                    "file a second issue on the next poll"
                ),
            },
        )
    if created:
        audit.record(
            "trello.recovered",
            outcome="error",
            entity_type="card",
            entity_id=card.card_id,
            target=card.card_id,
            detail={
                "path": "linked issue no longer resolves",
                "issue": f"{card.repo_key}#{card.issue_number}",
                "action": "mapping kept, anomaly raised",
            },
            dry_run=dry_run,
        )
    return True
