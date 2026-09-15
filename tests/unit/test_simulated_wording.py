"""Below ``local`` the destructive verbs say "would", never "removed" (issue #70).

Found cleaning up after a verification round: ``worktree remove`` at ``plan`` printed
"removed worktree …" and "deleted branch …" over a worktree and branch that were both still
there. The audit log said ``[simulated]`` on both git records; the one surface the operator
reads first did not. ``cancel`` had it right all along — "stopped session … via a simulated
stop" — so the other verbs are held to its rule here: the output names the simulation, and
any line using the real path's words for a completed destruction says, in that same line,
that it was simulated.

The contexts here are at ``plan`` throughout, level and boundaries agreeing as ``wire``
makes them agree, because the note line names the level and a context at ``live`` holding a
simulated boundary would be describing a machine that cannot exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.conftest import make_boundaries, seed_item
from tests.unit.test_cancel import seed_running
from tests.unit.test_purge_worktrees import DeletingVcs

from robot_army import cleanup, db, operations
from robot_army.audit import read_records
from robot_army.boundaries.dtach import SimulatedSessionHost
from robot_army.boundaries.git import SimulatedVersionControl
from robot_army.effects import EffectLevel
from robot_army.operations import EXIT_FAILED, EXIT_OK, Context
from robot_army.states import WorkItemState

BRANCH = "robot-army/42-fix"
#: Not on disk: a simulated item's worktree was itself only simulated.
WORKTREE = "/w/demo/issue-42"
NOTE = (
    "  (simulated: effect level is `plan`; version control is real from `local`, "
    "so nothing on disk was touched)"
)

#: The real path's words for a destruction that happened. A simulated line may use one only
#: if it also says "simulated" — which is how ``cancel``'s sentence passes unchanged — or
#: puts it in the conditional, "would".
REAL_PHRASES = (
    "removed worktree",
    "deleted branch",
    "worktree removed",
    "branch removed",
    "nothing to prune",
    "had their worktree removed",
    "stopped session",
)


def at_plan(conn, audit, config, *, vcs: Any = None) -> Context:
    host = SimulatedSessionHost(audit)
    return Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(
            audit,
            level=EffectLevel.PLAN,
            vcs=vcs or SimulatedVersionControl(audit),
            host=host,
            simulated_host=host,
        ),
        effect_level=EffectLevel.PLAN,
    )


def item_with_worktree(conn, *, path: str = WORKTREE, branch: str | None = BRANCH) -> int:
    item_id = seed_item(conn, state=str(WorkItemState.DONE), dry_run=True)
    with db.transaction(conn):
        db.update_work_item_columns(conn, item_id, worktree_path=path, branch=branch)
    return item_id


def records(layout, action: str) -> list[dict[str, Any]]:
    return [
        record
        for record, _ in read_records(layout.log_dir)
        if record is not None and record["action"] == action
    ]


# -- worktree remove (contract W1 to W4) --------------------------------------


def test_a_simulated_removal_says_would_and_names_the_level(conn, audit, config, layout):
    """W2, and the issue's own reproduction: nothing on disk, so nothing survives to be
    refused, and the removal is the simulation's — accepted, and reported as one."""
    item_id = item_with_worktree(conn)

    result = operations.worktree_remove(at_plan(conn, audit, config), item_id)

    assert result.code == EXIT_OK, result.lines
    assert result.lines == [
        f"would remove worktree {WORKTREE}",
        f"would delete branch {BRANCH}",
        NOTE,
    ]
    assert result.data["simulated"] is True
    assert result.data["worktree_removed"] is True and result.data["branch_deleted"] is True
    outcome = records(layout, "worktree.remove")[-1]
    assert outcome["kind"] == "outcome"
    assert outcome["detail"]["simulated"] is True
    # Within the simulation that worktree is gone; every step of it is marked simulated, and
    # so is the record a finished item keeps of it (issue #113).
    after = db.get_work_item(conn, item_id)
    assert after.worktree_path == WORKTREE
    assert after.cleanup_state == "done"
    assert after.cleanup_reason == (
        "worktree removal simulated; branch deletion simulated — by `robot-army worktree remove`"
    )


def test_a_simulated_removal_with_no_branch_prints_no_branch_line(conn, audit, config):
    """W3."""
    item_id = item_with_worktree(conn, branch=None)

    result = operations.worktree_remove(at_plan(conn, audit, config), item_id)

    assert result.code == EXIT_OK, result.lines
    assert result.lines == [f"would remove worktree {WORKTREE}", NOTE]


def test_a_real_directory_at_plan_is_still_refused(conn, audit, config, tmp_path):
    """W4: #59's refusal is unchanged, and never reads as a "would"."""
    survivor = tmp_path / "worktrees" / "demo" / "issue-42"
    survivor.mkdir(parents=True)
    item_id = item_with_worktree(conn, path=str(survivor))

    result = operations.worktree_remove(at_plan(conn, audit, config), item_id)

    assert result.code == EXIT_FAILED
    assert result.data["refused_by"] == "directory_survived"
    assert result.data["simulated"] is True
    assert not any("would remove" in line or "removed worktree" in line for line in result.lines)
    assert survivor.is_dir()


def test_a_real_removal_reads_as_it_always_has(conn, audit, config, tmp_path):
    """W1: the real path's lines, and ``simulated: false`` rather than an absent key."""
    real = tmp_path / "worktrees" / "demo" / "issue-42"
    real.mkdir(parents=True)
    item_id = item_with_worktree(conn, path=str(real))
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=DeletingVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )

    result = operations.worktree_remove(ctx, item_id)

    assert result.code == EXIT_OK, result.lines
    assert result.lines == [f"removed worktree {real}", f"deleted branch {BRANCH}"]
    assert result.data["simulated"] is False
    assert not real.exists()


# -- cleanup (contract W5 to W7) ----------------------------------------------


def test_cleanup_at_plan_counts_what_would_have_been_removed(conn, audit, config):
    item_with_worktree(conn)

    result = operations.cleanup_now(at_plan(conn, audit, config))

    assert result.code == EXIT_OK, result.lines
    assert result.lines[-2:] == [
        "1 of 1 considered item(s) would have their worktree removed",
        NOTE,
    ]
    [decision] = result.data["decisions"]
    assert decision["simulated"] is True
    assert decision["state"] == cleanup.DONE
    assert decision["reason"].startswith("worktree removal simulated; branch deletion simulated")


def test_cleanup_at_plan_retains_a_real_directory(conn, audit, config, tmp_path):
    """W7 through the command: recorded ``retained``, not ``done``, and the directory is
    still there for ``cleanup <id>`` to reconsider once the level is real."""
    survivor = tmp_path / "worktrees" / "demo" / "issue-42"
    survivor.mkdir(parents=True)
    item_id = item_with_worktree(conn, path=str(survivor))

    result = operations.cleanup_now(at_plan(conn, audit, config))

    [decision] = result.data["decisions"]
    assert decision["state"] == cleanup.RETAINED
    assert decision["reason"] == cleanup.SURVIVED_REASON
    assert db.get_work_item(conn, item_id).cleanup_state == cleanup.RETAINED
    assert survivor.is_dir()


# -- worktree prune (contract W9) ---------------------------------------------


def test_prune_at_plan_says_it_did_not_look(conn, audit, config):
    seed_item(conn)

    result = operations.worktree_prune(at_plan(conn, audit, config))

    assert result.code == EXIT_OK, result.lines
    assert result.lines == ["demo: not checked — pruning is simulated", NOTE]
    assert result.data["simulated"] is True


# -- the rule all four hold to -------------------------------------------------


def _cancel(conn, audit, config) -> Any:
    item_id, _ = seed_running(conn, audit)
    return operations.cancel(at_plan(conn, audit, config), item_id, force=True)


def _remove(conn, audit, config) -> Any:
    return operations.worktree_remove(at_plan(conn, audit, config), item_with_worktree(conn))


def _prune(conn, audit, config) -> Any:
    seed_item(conn)
    return operations.worktree_prune(at_plan(conn, audit, config))


def _cleanup(conn, audit, config) -> Any:
    item_with_worktree(conn)
    return operations.cleanup_now(at_plan(conn, audit, config))


@pytest.mark.parametrize(
    "verb", [_cancel, _remove, _prune, _cleanup], ids=["cancel", "remove", "prune", "cleanup"]
)
def test_no_destructive_verb_reports_a_simulated_outcome_as_real(verb, conn, audit, config):
    """The issue's closing request. The phrase list is the real path's own vocabulary, so a
    verb reworded later — or a new one that copies a real sentence into its simulated branch —
    fails here the moment it does."""
    result = verb(conn, audit, config)

    assert result.code == EXIT_OK, result.lines
    assert "simulated" in "\n".join(result.lines)
    for line in result.lines:
        if any(phrase in line for phrase in REAL_PHRASES):
            # "would" is the honest form of the same words: cleanup's summary counts items
            # that "would have their worktree removed", which contains a real phrase.
            assert "simulated" in line or "would" in line, (
                f"reports a simulated outcome as real: {line!r}"
            )


def test_the_phrase_list_is_what_the_real_path_actually_says(conn, audit, config, tmp_path):
    """Guards the rule above against going vacuous: if the real wording drifted away from
    the list, every simulated output would pass it trivially."""
    real = tmp_path / "worktrees" / "demo" / "issue-42"
    real.mkdir(parents=True)
    item_id = item_with_worktree(conn, path=str(real))
    ctx = Context(
        config=config,
        conn=conn,
        audit=audit,
        boundaries=make_boundaries(audit, vcs=DeletingVcs(audit)),
        effect_level=EffectLevel.LIVE,
    )

    lines = operations.worktree_remove(ctx, item_id).lines

    assert any(phrase in line for line in lines for phrase in REAL_PHRASES)
    assert not any("simulated" in line for line in lines)
    assert Path(real).exists() is False
