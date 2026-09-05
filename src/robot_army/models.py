"""Dataclasses for every persisted entity, per data-model.md.

These exist so the state machine's types are explicit rather than dynamic (research.md
R2). They are plain data — no behaviour, no persistence knowledge — and ``db.py``'s
row factory turns ``sqlite3.Row`` into them.

Note what is *not* here: Isolated Checkout, which collapsed into columns on
``WorkItem``, and Audit Record, which is the JSONL file rather than a table. Both
collapses are argued in data-model.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any, Self

from robot_army.cardstates import CardState
from robot_army.states import SessionState, WorkItemState


@dataclass(frozen=True, slots=True)
class Repo:
    """Onboarding state. A row exists only once the maintainer has onboarded (FR-001)."""

    repo_key: str
    onboarded_at: str
    fingerprint_approved_at: str
    settings_fingerprint: str | None = None
    trust_verified_at: str | None = None
    #: The clone location as approved, absolute and with symlinks resolved. ``None`` means
    #: *onboarded, location never verified* — a row written before migration 005 — and
    #: **not** "onboarded at an unknown path". The difference decides what dispatch does:
    #: the first is re-approvable with ``onboard --reapprove``, the second would invite a
    #: guess, and guessing which repository to act in is the failure milestone 005 exists
    #: to prevent (research R6).
    clone_path: str | None = None
    #: ``derived`` or ``configured`` — which of the two answers produced ``clone_path``,
    #: so a surface can tell the author which file to edit when it is wrong (FR-011).
    path_source: str | None = None
    #: The **normalised** ``host/owner/name`` found at ``clone_path``, never a raw remote
    #: URL: a raw URL may embed credentials and this column is read back into terminal
    #: output and JSON views (FR-032).
    verified_origin: str | None = None
    origin_verified_at: str | None = None

    @property
    def fingerprint(self) -> dict[str, str]:
        """The approved path → SHA-256 mapping. ``NULL`` means "no committed settings"."""
        if not self.settings_fingerprint:
            return {}
        return json.loads(self.settings_fingerprint)


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: int
    source: str
    source_id: str
    source_url: str
    repo_key: str
    issue_number: int
    title: str
    body: str
    labels: str
    state: WorkItemState
    dry_run: bool
    discovered_at: str
    updated_at: str
    #: Who wrote the issue, as the last read of it reported (issue #119). The fact
    #: ``dispatch`` compares against ``config.github.author`` instead of asserting it.
    #:
    #: ``None`` means *never recorded* — a row written before migration 011 — which is a
    #: different thing from "no author" and from "unknown but presumably fine". Such a row
    #: may have reached ``ready`` through the defect migration 011 exists to close, and
    #: nothing can tell after the fact, so it is refused rather than trusted. Nothing
    #: backfills it: ``retry`` re-reads the issue and writes it for the first time.
    author: str | None = None
    worktree_path: str | None = None
    branch: str | None = None
    prepare_output: str | None = None
    failure_reason: str | None = None
    blocked_reason: str | None = None
    ready_at: str | None = None
    dispatching_at: str | None = None
    active_at: str | None = None
    ended_at: str | None = None
    done_at: str | None = None
    #: What cleanup did to this item's disk, and why (milestone 004, R13). ``NULL`` means
    #: never considered — every pre-migration row, and every row while cleanup is disabled.
    #: A different axis from ``state``: ``done`` says the work finished, these say whether
    #: the 499 MB it left behind is still there.
    cleanup_state: str | None = None
    cleanup_reason: str | None = None
    cleaned_at: str | None = None
    #: The feature directories present in the worktree when it was created, as a JSON
    #: array (milestone 007). ``NULL`` means none was recorded, which is not ``"[]"``:
    #: without a baseline nothing can be attributed to this item, so no phase is derived
    #: at all and the reason is recorded once.
    speckit_baseline: str | None = None
    #: The last derived rung — ``specify``, ``plan``, ``tasks`` or ``implement`` — the
    #: directory it came from, and when it changed. Advisory: nothing decides anything on
    #: these (FR-016), and absence is a legitimate resting state rather than a fault.
    speckit_phase: str | None = None
    speckit_feature_dir: str | None = None
    speckit_phase_at: str | None = None
    #: Where this item's issue sits on its repository's project board, as the last
    #: successful board read saw it (issue #48). The pair must stay a pair: four states
    #: have to remain distinguishable and collapsing any two of them is a real bug.
    #:
    #: ``board_column`` NULL means either *no board has been read for this repository*
    #: or *read, and this item is not on the board* — and those are different. The
    #: difference lives in ``repo_projects.last_read_at``, because it is one fact about a
    #: repository rather than a fact repeated on every one of its rows.
    #:
    #: ``board_position`` is the 1-based rank inside the dispatch column and is NULL for
    #: everything outside it. It must never be written as 0 to mean "unknown": that would
    #: silently promote every item of an unread board to the head of its queue.
    board_column: str | None = None
    board_position: int | None = None

    @property
    def label_list(self) -> list[str]:
        return json.loads(self.labels)

    @property
    def cleanup_pending(self) -> bool:
        """Would the automatic pass reconsider this item?

        ``skipped`` is the only non-``NULL`` value it revisits, and that is the whole point
        of distinguishing it from ``retained``: one means "not yet", the other means "we
        looked and decided no".
        """
        return self.cleanup_state in (None, "skipped")


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    work_item_id: int
    session_id: str
    attempt: int
    state: SessionState
    dry_run: bool
    started_at: str
    pid: int | None = None
    proc_start: str | None = None
    scope: str | None = None
    host_socket: str | None = None
    window_id: int | None = None
    launch_argv: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    confirmed_at: str | None = None
    ended_at: str | None = None
    #: When this session's transcript question was answered, or ``NULL`` while it is still
    #: open. Written once by ``reconcile._sweep_transcripts`` and never cleared: a closed
    #: question stays closed, so no session can be reported twice however many passes run
    #: and whether or not its anomaly was acknowledged.
    transcript_checked_at: str | None = None

    @property
    def argv(self) -> list[str]:
        return json.loads(self.launch_argv) if self.launch_argv else []


@dataclass(frozen=True, slots=True)
class Card:
    """One card on the intake board, and the mapping to the issue it produced.

    Almost everything is optional because almost everything is *learned*: a card starts as
    a title and a body with no repository, no issue, and no position we put it in. The
    columns fill in as the lifecycle advances, and the ones that stay ``NULL`` are the
    honest record of a card that never got that far.

    Four list ids rather than one, and the distinction matters (data-model.md):
    ``origin_list_id`` is where the card was before we ever touched it and is what FR-029
    returns it to; ``placed_list_id`` is where we last put it and is what FR-030 compares
    against to detect a move by the author; ``pending_move_to`` is written *before* a move
    is attempted, so an interrupted move of ours is distinguishable from a human one (R12);
    and ``current_list_id`` is where the card is **now**, which none of the other three
    answers and which milestone 006 needs so a listing can say whether a card is parked
    without asking the board.
    """

    id: int
    board_id: str
    card_id: str
    card_url: str
    title: str
    body: str
    state: CardState
    dry_run: bool
    first_seen_at: str
    updated_at: str
    repo_key: str | None = None
    issue_number: int | None = None
    issue_url: str | None = None
    #: The current explanation. ``commented_reason`` is the last one actually written onto
    #: the card, and the comparison between the two is all of FR-022: comment when they
    #: differ, stay silent when they do not.
    reason: str | None = None
    commented_reason: str | None = None
    #: The rescan-trigger baseline, carried as the string the API returned. Refreshed from
    #: our *own* writes as well as the author's, which is what closes R9's loop.
    last_activity: str | None = None
    origin_list_id: str | None = None
    placed_list_id: str | None = None
    pending_move_to: str | None = None
    #: Refreshed from the board every poll. ``None`` means tracked before milestone 006's
    #: migration and not yet re-polled, which is treated as *not parked*.
    #:
    #: The name is carried beside the id because the listing commands must answer "is this
    #: parked?" with the board unreachable, and can only compare against the names in
    #: ``[trello] ignore_lists``. Written by the same statement, so they cannot disagree.
    current_list_id: str | None = None
    current_list_name: str | None = None
    comment_posted_at: str | None = None
    intent_at: str | None = None
    create_failures: int = 0
    archived_at: str | None = None

    @property
    def source_id(self) -> str | None:
        """The ``repo#number`` this card's issue is known by, once it has one.

        The join key onto ``work_items.source_id`` (R16), kept here so the two front ends
        cannot each invent their own spelling of it.
        """
        if self.repo_key is None or self.issue_number is None:
            return None
        return f"{self.repo_key}#{self.issue_number}"


@dataclass(frozen=True, slots=True)
class Anomaly:
    """A condition detected but not resolvable by the system (FR-065)."""

    id: int
    kind: str
    detail: str
    detected_at: str
    entity_type: str | None = None
    entity_id: str | None = None
    #: A maintainer said "I have seen this".
    acknowledged_at: str | None = None
    #: The system re-checked and the condition no longer holds (issue #138). A different
    #: fact from ``acknowledged_at``, kept in a different column so ``--all`` can tell an
    #: anomaly that resolved itself from one somebody dismissed.
    resolved_at: str | None = None

    @property
    def detail_obj(self) -> dict[str, Any]:
        return json.loads(self.detail)


@dataclass(frozen=True, slots=True)
class PollState:
    """Per-repository polling bookkeeping. High churn, kept out of ``repos``."""

    repo_key: str
    consecutive_failures: int = 0
    etag: str | None = None
    last_polled_at: str | None = None
    last_status: int | None = None
    backoff_until: str | None = None


@dataclass(frozen=True, slots=True)
class RepoProject:
    """Which project board governs a repository, and how the last read of it went.

    Shaped like :class:`PollState` and for the same reason: high-churn bookkeeping that
    has no business in ``repos``, which is an *approval* record that deliberately never
    re-derives itself.

    ``resolved_at`` and ``last_read_at`` answer different questions and both are needed.
    A project can be resolved and never yet read — the pass that resolved it failed, or
    has not run — and in that state nothing is ordered and nothing is held. Only
    ``last_read_at`` opens the gate (FR-014), and it records the last **success** rather
    than the last attempt, so a failed read leaves the previous snapshot in force and
    visibly stale instead of discarding it.

    ``unresolved_reason`` is non-NULL exactly when ``resolved_at`` is NULL and carries the
    sentence a surface shows, so "why is this repository not ordered by its board?" is
    answerable without the log and with the board unreachable.
    """

    repo_key: str
    project_id: str | None = None
    project_number: int | None = None
    project_title: str | None = None
    project_url: str | None = None
    #: ``'discovered'`` or ``'configured'`` — which is what lets a surface tell the author
    #: whether they chose this or inherited it, and therefore which file to edit.
    project_source: str | None = None
    column_name: str | None = None
    column_source: str | None = None
    resolved_at: str | None = None
    unresolved_reason: str | None = None
    last_read_at: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    backoff_until: str | None = None

    @property
    def governs(self) -> bool:
        """Whether this repository's order and holds come from its board.

        Both halves are required. A resolution with no successful read has nothing to
        order by, and a stale read still governs — that is FR-025, and it is why this
        asks for ``last_read_at`` rather than for freshness.
        """
        return self.resolved_at is not None and self.last_read_at is not None


@dataclass(frozen=True, slots=True)
class DispatchControl:
    """Whether dispatch is suspended, since when, and by which front end (FR-033).

    Single-valued for the whole system — the table it comes from is constrained to one
    row. Orthogonal to both state machines: it gates whether the dispatcher runs at all
    and changes no item's state, so items simply accumulate in ``ready`` (FR-034).
    """

    paused: bool = False
    paused_at: str | None = None
    paused_by: str | None = None


@dataclass(frozen=True, slots=True)
class Hold:
    """One deliberate statement that some work must not be dispatched (issue #117).

    Carries no target. The two accessors return ``{target: Hold}``, so the key *is* the
    target and the value is everything else — which is why one dataclass serves both
    ``item_holds`` and ``repo_holds`` despite their key columns differing.

    There is no level, no expiry, and no note (FR-026). Presence is the whole fact, so a
    ``Hold`` that exists always applies and the only question a reader ever asks of one is
    when it was placed and by which surface.

    Deliberately **not** in ``ROW_TYPES``. That mapping exists so ``db.py`` can pick a row
    factory per *table* for queries returning whole rows; both hold queries select two
    columns into a dict keyed by the target, so an entry there would be one nothing reads.
    """

    held_at: str
    held_by: str


#: Every anomaly kind the system can raise. Named here so ``status`` and ``anomalies``
#: can surface all of them (FR-065, T135) rather than only the ones seen so far.
ANOMALY_KINDS: tuple[str, ...] = (
    "orphan_session",
    "dispatching_timeout",
    "no_transcript",
    "session_id_mismatch",
    "registry_version_unknown",
    "config_missing_repo",
    "prunable_worktree",
    "malformed_exit_record",
    "orphan_exit_record",
    "stale_socket",
    # Milestone 003. Each names a board condition the system detected and cannot fix on
    # its own: a precondition that stopped ingestion, a board it could not reach, a
    # creation that keeps failing, and a mapping whose issue has vanished (FR-037).
    "board_precondition",
    "board_unreachable",
    "card_create_failing",
    "card_issue_missing",
    # Milestone 004. The registry could not be read and the /proc enumeration meant to
    # replace it returned nothing at all, so the count of live sessions is unknown rather
    # than zero. Dispatch is withheld while it holds (R4, FR-007), which makes it the one
    # anomaly here that stops work rather than merely describing it.
    "capacity_unobservable",
    # Milestone 005. The machine changed under an approval: the clone approved at
    # onboarding is no longer where it was, or a different repository is now there. Both
    # are distinct from an ordinary gate refusal, which means a precondition was never met
    # rather than that the world moved (FR-028, US5).
    "clone_path_missing",
    "clone_origin_changed",
)


def _coerce(value: Any, annotation: Any) -> Any:
    """Turn a SQLite column into the dataclass's declared type.

    Only ``bool`` (SQLite has no boolean type) and the three state enums need one.
    Everything else is already the right Python type.
    """
    if value is None:
        return None
    if annotation is bool or annotation == "bool":
        return bool(value)
    if annotation is WorkItemState or annotation == "WorkItemState":
        return WorkItemState(value)
    if annotation is SessionState or annotation == "SessionState":
        return SessionState(value)
    if annotation is CardState or annotation == "CardState":
        return CardState(value)
    return value


def from_row(cls: type[Any], row: Any) -> Any:
    """Build a dataclass from a mapping-like row, coercing declared types."""
    kwargs = {}
    for f in fields(cls):
        if f.name in row.keys():  # noqa: SIM118 - sqlite3.Row has no __contains__
            kwargs[f.name] = _coerce(row[f.name], f.type)
    return cls(**kwargs)


class _RowMixin:
    @classmethod
    def from_row(cls, row: Any) -> Self:
        return from_row(cls, row)


#: Table name → dataclass, used by db.py to pick a row factory per query.
ROW_TYPES: dict[str, type[Any]] = {
    "repos": Repo,
    "work_items": WorkItem,
    "sessions": Session,
    "anomalies": Anomaly,
    "poll_state": PollState,
    "cards": Card,
    "repo_projects": RepoProject,
}
