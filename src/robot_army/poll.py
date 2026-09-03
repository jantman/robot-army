"""Polling GitHub and deciding what is eligible.

Two things here carry more weight than their size suggests.

**ETags.** ``poll_state.etag`` is what makes a 60-second poll sustainable: an unchanged
listing returns ``304`` and costs *nothing* against the rate limit (R4). ``304`` is the
healthy steady state, not an error and not "nothing found".

**The author check is a security boundary** (FR-007). There is deliberately no "any
author" value and no way to disable it: the label is a trigger anyone with write access
to the repository could apply, and the author check is what stops that being a remote
code execution path into the maintainer's machine. It is checked here, in one place, and
a rejected item still gets a row so ``blocked_reason`` can explain why nothing happened
(FR-009).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from robot_army import db, repos
from robot_army.boundaries import Issue, PollResult, TransportError
from robot_army.models import PollState, RepoProject
from robot_army.states import WorkItemState, dumps_labels, transition_work_item, utcnow

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import Config
    from robot_army.effects import Boundaries

#: Backoff ceiling for a repository whose polls keep failing. Well under an hour, so a
#: transient outage does not silently stop work for the rest of the day.
MAX_BACKOFF_SECONDS = 900


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: str | None = None
    #: Whether a rejection deserves a persisted row.
    #:
    #: data-model.md draws this line deliberately: an issue the maintainer *deliberately
    #: labelled* in a repository we manage gets a row so ``blocked_reason`` can explain
    #: why nothing happened (FR-009). An issue that merely appeared in a listing without
    #: our label, or belongs to a repository that is not onboarded, gets an audit line
    #: and no row — otherwise the ones that matter would be buried, and the ``repo_key``
    #: foreign key would have nothing to point at.
    persist: bool = True


@dataclass(frozen=True, slots=True)
class PollOutcome:
    repo_key: str
    status: int
    found: int
    created: int
    rejected: int
    error: str | None = None
    skipped_reason: str | None = None


def evaluate(
    issue: Issue,
    *,
    config: Config,
    repo_key: str,
    onboarded: bool,
) -> Eligibility:
    """Every condition from FR-007, each reported individually when it fails.

    The conditions are independent; the order below is chosen so that the ones which
    decide *whether a row exists at all* are settled first. Only once we know this is a
    labelled issue in an onboarded repository does the author check — the security
    boundary, and the failure most worth a persisted explanation — get to speak.
    """
    if config.github.label not in issue.labels:
        return Eligibility(
            False,
            f"issue does not carry the {config.github.label!r} label "
            f"(has: {', '.join(issue.labels) or 'none'})",
            persist=False,
        )
    if not onboarded:
        # Onboarding, not configuration, is what makes a repository known (FR-015,
        # FR-016). Before milestone 005 this was two conditions and the first of them —
        # "has a [repos.*] section" — was the one that decided whether the repository
        # existed at all. A section is now an override and nothing more, so testing for
        # one here would refuse exactly the repositories this milestone exists to serve.
        return Eligibility(
            False,
            f"repository {repo_key!r} is not onboarded — "
            f"run `robot-army onboard {repo_key}`",
            persist=False,
        )
    if issue.author != config.github.author:
        return Eligibility(
            False,
            f"issue author {issue.author!r} is not the configured author "
            f"{config.github.author!r} (FR-007 security boundary; this cannot be disabled)",
        )
    if issue.state != "open":
        return Eligibility(False, f"issue is {issue.state}, not open")
    return Eligibility(True)


def _in_backoff(state: PollState) -> bool:
    return bool(state.backoff_until and state.backoff_until > utcnow())


def poll_repo(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    dry_run: bool,
) -> PollOutcome:
    """Poll one repository, create rows for what it found, and evaluate eligibility.

    A transport failure is recorded and re-raised to the caller as a *failed poll for
    this repository*, never converted into "found nothing" — see the module docstring in
    ``boundaries/github.py`` for why that distinction is load-bearing.
    """
    state = db.get_poll_state(conn, repo_key)
    if _in_backoff(state):
        return PollOutcome(
            repo_key=repo_key,
            status=state.last_status or 0,
            found=0,
            created=0,
            rejected=0,
            skipped_reason=f"in backoff until {state.backoff_until}",
        )

    try:
        result: PollResult = boundaries.issue_reader.poll(repo_key, state.etag)
    except TransportError as exc:
        failures = state.consecutive_failures + 1
        backoff = min(2**failures, MAX_BACKOFF_SECONDS)
        with db.transaction(conn):
            db.save_poll_state(
                conn,
                PollState(
                    repo_key=repo_key,
                    etag=state.etag,
                    last_polled_at=utcnow(),
                    last_status=0,
                    consecutive_failures=failures,
                    backoff_until=_plus_seconds(backoff),
                ),
            )
        audit.error(
            "poll.repo",
            error=exc,
            entity_type="repo",
            entity_id=repo_key,
            detail={"consecutive_failures": failures, "backoff_s": backoff},
        )
        return PollOutcome(
            repo_key=repo_key, status=0, found=0, created=0, rejected=0, error=str(exc)
        )

    created = 0
    rejected = 0
    onboarded = db.get_repo(conn, repo_key) is not None

    for issue in result.items:
        source_id = f"{repo_key}#{issue.number}"
        verdict = evaluate(issue, config=config, repo_key=repo_key, onboarded=onboarded)

        existing = db.find_work_item(
            conn, source="github", source_id=source_id, dry_run=dry_run
        )
        if existing is not None:
            # Idempotency (FR-072): re-polling an already-known issue is a no-op, not a
            # second worktree. The only thing worth doing is re-evaluating an item still
            # sitting in `discovered` because a previous evaluation was interrupted.
            if existing.state == WorkItemState.DISCOVERED:
                _settle(conn, audit, existing.id, verdict, dry_run=dry_run)
                rejected += 0 if verdict.eligible else 1
            continue

        if not verdict.eligible and not verdict.persist:
            # An issue that is simply not ours produces an audit line and no row.
            # Creating rows for every unlabelled issue in the repository would bury the
            # ones the maintainer deliberately labelled — and work_items.repo_key is a
            # foreign key into `repos`, which only has a row once onboarding happened.
            audit.record(
                "poll.rejected",
                outcome="ok",
                entity_type="issue",
                entity_id=source_id,
                detail={"reason": verdict.reason, "persisted": False},
                dry_run=dry_run,
            )
            rejected += 1
            continue

        with db.transaction(conn):
            item_id = db.insert_work_item(
                conn,
                source="github",
                source_id=source_id,
                source_url=issue.url,
                repo_key=repo_key,
                issue_number=issue.number,
                title=issue.title,
                body=issue.body,
                labels=dumps_labels(list(issue.labels)),
                dry_run=dry_run,
            )
            if item_id is None:
                continue
            created += 1
            audit.record(
                "poll.discovered",
                outcome="ok",
                entity_type="work_item",
                entity_id=item_id,
                target=source_id,
                detail={"title": issue.title, "labels": list(issue.labels)},
                dry_run=dry_run,
            )
            _settle(conn, audit, item_id, verdict, dry_run=dry_run, in_transaction=True)
        if not verdict.eligible:
            rejected += 1

    # After the per-issue loop and before the poll state is saved, deliberately (issue
    # #48): items discovered in *this* pass already have rows by now, so they get their
    # board facts immediately rather than spending a cycle misclassified as "not on the
    # board" and jumping the tail of their repository's queue.
    read_board(
        conn,
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo_key=repo_key,
        onboarded=onboarded,
    )

    with db.transaction(conn):
        db.save_poll_state(
            conn,
            PollState(
                repo_key=repo_key,
                etag=result.etag,
                last_polled_at=utcnow(),
                last_status=result.status,
                consecutive_failures=0,
                backoff_until=None,
            ),
        )

    return PollOutcome(
        repo_key=repo_key,
        status=result.status,
        found=len(result.items),
        created=created,
        rejected=rejected,
    )


@dataclass(frozen=True, slots=True)
class ProjectCheck:
    """One ``doctor`` finding about a repository's project board (issue #48)."""

    name: str
    ok: bool
    detail: str


def check_project(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    config: Config,
    repo_key: str,
) -> list[ProjectCheck]:
    """Everything about one repository's board that can be verified without dispatching.

    Performed by ``doctor`` rather than at startup for the reason the Trello board checks
    already are: the author should learn that their token cannot read projects *before*
    they wait a minute watching an order fail to change (FR-027).

    A repository with board ordering switched off returns exactly one passing check saying
    so, and naming whether that came from its own setting or the global one. Silence was
    the first design and is worse: "no board rows" would read identically to "this build
    has no board checks", and the author who turned it off six weeks ago is precisely the
    one who needs reminding. Nothing further is asked about such a repository, because
    every remaining answer would be about a board it has said it does not want.
    """
    enabled, explicit = config.effective_project_ordering(repo_key)
    if not enabled:
        source = "configured" if explicit else "default"
        return [
            ProjectCheck(
                f"{repo_key} ordering", True, f"board ordering off ({source})"
            )
        ]

    checks: list[ProjectCheck] = []
    access = boundaries.issue_reader.project_access()
    checks.append(ProjectCheck(f"{repo_key} token", access.ok, access.detail))
    if not access.ok:
        # No point asking further questions whose answers would all be the same refusal.
        return checks

    repo = config.repos.get(repo_key)
    try:
        resolution = boundaries.issue_reader.resolve_project(
            repo_key,
            project=repo.project if repo else None,
            column=repo.project_column if repo else None,
        )
    except TransportError as exc:
        checks.append(ProjectCheck(f"{repo_key} project", False, str(exc)))
        return checks

    if not resolution.resolved:
        checks.append(ProjectCheck(f"{repo_key} project", False, resolution.reason or ""))
        return checks
    checks.append(
        ProjectCheck(
            f"{repo_key} project",
            True,
            f"#{resolution.project_number} {resolution.project_title} "
            f"({resolution.project_source})",
        )
    )
    checks.append(
        ProjectCheck(
            f"{repo_key} column",
            True,
            f"{resolution.column_name!r} ({resolution.column_source})",
        )
    )

    try:
        conflicts = boundaries.issue_reader.view_sort_conflicts(
            repo_key,
            project_id=resolution.project_id,
            column_name=resolution.column_name,
        )
    except TransportError as exc:
        conflicts = ()
        checks.append(ProjectCheck(f"{repo_key} view sort", False, str(exc)))
    else:
        checks.append(
            ProjectCheck(
                f"{repo_key} view sort",
                not conflicts,
                "; ".join(conflicts)
                if conflicts
                else "no board view sorts the dispatch column",
            )
        )

    state = db.get_repo_project(conn, repo_key)
    if state.consecutive_failures:
        checks.append(
            ProjectCheck(
                f"{repo_key} freshness",
                False,
                f"last read failed ({state.last_error}); showing the snapshot from "
                f"{state.last_read_at or 'never'}",
            )
        )
    else:
        checks.append(
            ProjectCheck(
                f"{repo_key} freshness",
                True,
                f"last read {state.last_read_at or 'never'}",
            )
        )
    return checks


def read_board(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    repo_key: str,
    onboarded: bool,
) -> RepoProject:
    """Resolve and read one repository's project board, and store what it said (issue #48).

    Returns the state it stored, so a caller can report without a second read.

    **A failure here never stops dispatch** (FR-023, FR-025). The previous snapshot stays
    in force and becomes visibly stale, because an order the author arranged yesterday is
    a far better answer than no order at all — and a repository that stalled because
    GitHub was briefly unreachable would be a worse failure than the one being avoided.
    ``last_read_at`` therefore records the last **success**, never the last attempt.
    """
    state = db.get_repo_project(conn, repo_key)
    if not onboarded:
        # No row can be written: repo_projects references repos. A resolution for a
        # repository nobody onboarded is a row about nothing.
        return state
    enabled, _ = config.effective_project_ordering(repo_key)
    if not enabled:
        return state
    if state.backoff_until and state.backoff_until > utcnow():
        # Recorded rather than silent: "the board was not read this pass" is a fact about
        # the order being shown, and a reader of the log should not have to infer it from
        # an absence.
        audit.record(
            "poll.board",
            outcome="ok",
            entity_type="repo",
            entity_id=repo_key,
            detail={"skipped": f"in backoff until {state.backoff_until}"},
        )
        return state

    repo = config.repos.get(repo_key)
    try:
        resolution = boundaries.issue_reader.resolve_project(
            repo_key,
            project=repo.project if repo else None,
            column=repo.project_column if repo else None,
        )
        snapshot = (
            boundaries.issue_reader.read_board(
                repo_key,
                project_id=resolution.project_id,
                column_name=resolution.column_name,
            )
            if resolution.resolved
            else None
        )
    except TransportError as exc:
        return _board_failed(conn, audit, state, repo_key=repo_key, error=str(exc))

    now = utcnow()
    if snapshot is None:
        # Resolution failed but the request succeeded. Not a transport failure, so the
        # failure counter is *not* advanced — backing off would delay recovery from a
        # condition only the author can clear, and there is nothing to retry away.
        stored = RepoProject(
            repo_key=repo_key,
            unresolved_reason=resolution.reason,
            last_read_at=state.last_read_at,
            consecutive_failures=0,
        )
        with db.transaction(conn):
            db.save_repo_project(conn, stored)
        audit.record(
            "poll.board",
            outcome="error",
            entity_type="repo",
            entity_id=repo_key,
            detail={"unresolved": resolution.reason},
        )
        if state.last_read_at is not None:
            # NOT the same consequence as a transport failure, and the difference is the
            # whole point of recording it. `_board_failed` carries `resolved_at` forward,
            # so its repository stays governed by a stale snapshot. This branch does not:
            # without `resolved_at`, `RepoProject.governs` is false, the repository leaves
            # `ordering.plan`'s governed set, and **both** the board permutation and the
            # off-column holds are released. Saying "stays in force" here would describe
            # the other branch's behaviour, which is how a log stops being evidence.
            audit.record(
                "poll.board.fallback",
                outcome="ok",
                entity_type="repo",
                entity_id=repo_key,
                detail={
                    "snapshot_from": state.last_read_at,
                    "consequence": (
                        "the repository is now ungoverned; board order and off-column "
                        "holds are released until resolution succeeds again"
                    ),
                },
            )
        return stored

    stored = RepoProject(
        repo_key=repo_key,
        project_id=resolution.project_id,
        project_number=resolution.project_number,
        project_title=resolution.project_title,
        project_url=resolution.project_url,
        project_source=resolution.project_source,
        column_name=resolution.column_name,
        column_source=resolution.column_source,
        resolved_at=now,
        unresolved_reason=None,
        last_read_at=now,
        last_error=None,
        consecutive_failures=0,
        backoff_until=None,
    )
    # One transaction over both, so a process killed mid-write rolls back to the previous
    # snapshot whole rather than leaving half of one board beside half of another.
    with db.transaction(conn):
        db.apply_board_facts(
            conn,
            repo_key,
            ranked={entry.issue_number: entry.position for entry in snapshot.ranked},
            elsewhere=dict(snapshot.elsewhere),
            column_name=snapshot.column_name,
        )
        db.save_repo_project(conn, stored)
    audit.record(
        "poll.board",
        outcome="ok",
        entity_type="repo",
        entity_id=repo_key,
        detail={
            "project": resolution.project_number,
            "column": snapshot.column_name,
            "ranked": len(snapshot.ranked),
            "elsewhere": len(snapshot.elsewhere),
        },
    )
    return stored


def _board_failed(
    conn: sqlite3.Connection,
    audit: AuditLog,
    state: RepoProject,
    *,
    repo_key: str,
    error: str,
) -> RepoProject:
    """Record a failed board read and back off. The previous snapshot is left alone."""
    failures = state.consecutive_failures + 1
    backoff = min(2**failures, MAX_BACKOFF_SECONDS)
    stored = RepoProject(
        repo_key=repo_key,
        project_id=state.project_id,
        project_number=state.project_number,
        project_title=state.project_title,
        project_url=state.project_url,
        project_source=state.project_source,
        column_name=state.column_name,
        column_source=state.column_source,
        resolved_at=state.resolved_at,
        unresolved_reason=state.unresolved_reason,
        # Untouched. This is the whole of FR-025: the order the last successful read
        # produced stays in force, and its age is what makes the staleness visible.
        last_read_at=state.last_read_at,
        last_error=error,
        consecutive_failures=failures,
        backoff_until=_plus_seconds(backoff),
    )
    with db.transaction(conn):
        db.save_repo_project(conn, stored)
    audit.error(
        "poll.board",
        error=error,
        entity_type="repo",
        entity_id=repo_key,
        detail={"consecutive_failures": failures, "backoff_s": backoff},
    )
    if state.last_read_at is not None:
        audit.record(
            "poll.board.fallback",
            outcome="ok",
            entity_type="repo",
            entity_id=repo_key,
            detail={
                "snapshot_from": state.last_read_at,
                "consequence": "the last board read stays in force and is now stale",
            },
        )
    return stored


def _settle(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item_id: int,
    verdict: Eligibility,
    *,
    dry_run: bool,
    in_transaction: bool = False,
) -> bool:
    """Move a ``discovered`` row to ``ready`` or ``failed`` per the verdict."""

    def _apply() -> None:
        if verdict.eligible:
            transition_work_item(
                conn,
                audit,
                item_id=item_id,
                target=WorkItemState.READY,
                reason="all eligibility conditions passed",
                extra_columns={"blocked_reason": None},
            )
        else:
            transition_work_item(
                conn,
                audit,
                item_id=item_id,
                target=WorkItemState.FAILED,
                reason="eligibility rejected",
                extra_columns={
                    "blocked_reason": verdict.reason,
                    "failure_reason": verdict.reason,
                },
            )

    if in_transaction:
        _apply()
    else:
        with db.transaction(conn):
            _apply()
    return True


def _plus_seconds(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def poll_all(
    conn: sqlite3.Connection,
    *,
    boundaries: Boundaries,
    audit: AuditLog,
    config: Config,
    dry_run: bool,
    only_repo: str | None = None,
) -> list[PollOutcome]:
    """Poll every onboarded repository. One repository's failure does not stop the rest.

    The set comes from the database rather than from a ``Config`` loaded at process start,
    which has one consequence nothing asked for and everything gets: a repository onboarded
    **while the daemon is running** is polled on the next cycle, with no restart (research
    R7). ``poll_state`` is keyed by repository, so a key that appears between cycles simply
    has no prior state — a case this code already handled.
    """
    onboarded_keys = repos.known(conn)
    keys = [only_repo] if only_repo else onboarded_keys
    outcomes: list[PollOutcome] = []
    for repo_key in keys:
        if repo_key not in onboarded_keys:
            audit.error(
                "poll.repo",
                error=f"repository {repo_key!r} is not onboarded",
                entity_type="repo",
                entity_id=repo_key,
            )
            outcomes.append(
                PollOutcome(
                    repo_key=repo_key,
                    status=0,
                    found=0,
                    created=0,
                    rejected=0,
                    error=(
                        f"repository {repo_key!r} is not onboarded — "
                        f"run `robot-army onboard {repo_key}`"
                    ),
                )
            )
            continue
        outcomes.append(
            poll_repo(
                conn,
                boundaries=boundaries,
                audit=audit,
                config=config,
                repo_key=repo_key,
                dry_run=dry_run,
            )
        )
    return outcomes
