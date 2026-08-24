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

from robot_army import db
from robot_army.boundaries import Issue, PollResult, TransportError
from robot_army.models import PollState
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
    if repo_key not in config.repos:
        return Eligibility(
            False,
            f"repository {repo_key!r} has no [repos.{repo_key}] section",
            persist=False,
        )
    if not onboarded:
        return Eligibility(
            False,
            f"repository {repo_key!r} is configured but not onboarded — "
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
    """Poll every configured repository. One repository's failure does not stop the rest."""
    keys = [only_repo] if only_repo else sorted(config.repos)
    outcomes: list[PollOutcome] = []
    for repo_key in keys:
        if repo_key not in config.repos:
            audit.error(
                "poll.repo",
                error=f"no [repos.{repo_key}] section",
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
                    error=f"no [repos.{repo_key}] section in config",
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
