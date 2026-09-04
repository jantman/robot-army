"""One test per write rule in data-model.md.

Two of these are the ones that would fail quietly in production rather than loudly in CI: a
worktree removed by cleanup must leave the last known stage standing, and a re-derivation of
an unchanged phase must write no second record. Both look like nothing happening, which is
exactly what makes them worth asserting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.conftest import make_speckit_tree

from robot_army import db, speckit
from robot_army.states import WorkItemState, transition_work_item


def phase_records(layout: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("action") == "speckit.phase":
                    out.append(record)
    return out


def seed(conn: Any, audit: Any, worktree: Path, *, baseline: object) -> Any:
    """An ``active`` item pointing at ``worktree``, with the given baseline column."""
    with db.transaction(conn):
        db.upsert_repo(
            conn,
            repo_key="demo",
            settings_fingerprint={},
            trust_verified=True,
            clone_path="/tmp/clone",
        )
        item_id = db.insert_work_item(
            conn,
            source="github",
            source_id="demo#1",
            source_url="https://github.example/1",
            repo_key="demo",
            issue_number=1,
            title="A task",
            body="",
            labels="[]",
            author="jantman",
            dry_run=False,
        )
    with db.transaction(conn):
        db.update_work_item_columns(
            conn,
            item_id,
            worktree_path=str(worktree),
            branch="robot-army/issue-1",
            speckit_baseline=baseline,
        )
        transition_work_item(
            conn, audit, item_id=item_id, target=WorkItemState.READY, reason="test"
        )
        transition_work_item(
            conn, audit, item_id=item_id, target=WorkItemState.DISPATCHING, reason="test"
        )
        transition_work_item(
            conn, audit, item_id=item_id, target=WorkItemState.ACTIVE, reason="test"
        )
    return db.get_work_item(conn, item_id)


def test_a_null_baseline_is_never_observed(conn, audit, layout, tmp_path) -> None:
    """Rule 1. Deriving a baseline now would classify the session's own directory as
    pre-existing — the same silence with none of the honesty."""
    worktree = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    item = seed(conn, audit, worktree, baseline=None)

    assert speckit.record_phase(conn, audit, item) is None
    assert db.get_work_item(conn, item.id).speckit_phase is None
    assert phase_records(layout) == []


def test_a_first_observation_writes_the_rung_and_one_record(conn, audit, layout, tmp_path):
    worktree = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    item = seed(conn, audit, worktree, baseline="[]")

    phase = speckit.record_phase(conn, audit, item)

    assert phase == speckit.Phase(rung="specify", feature_dir="specs/007-x")
    stored = db.get_work_item(conn, item.id)
    assert stored.speckit_phase == "specify"
    assert stored.speckit_feature_dir == "specs/007-x"
    assert stored.speckit_phase_at
    assert len(phase_records(layout)) == 1


def test_an_unchanged_phase_writes_nothing(conn, audit, layout, tmp_path) -> None:
    """Rule 5, and the Principle III omission: one line per transition, not per cycle."""
    worktree = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    item = seed(conn, audit, worktree, baseline="[]")
    speckit.record_phase(conn, audit, item)

    again = db.get_work_item(conn, item.id)
    assert speckit.record_phase(conn, audit, again) is None
    assert len(phase_records(layout)) == 1


def test_the_ladder_does_not_descend(conn, audit, layout, tmp_path) -> None:
    """Rule 2. Deleting an artifact must not read as progress in reverse."""
    worktree = make_speckit_tree(
        tmp_path / "wt", features={"007-x": ["spec.md", "plan.md"]}
    )
    item = seed(conn, audit, worktree, baseline="[]")
    speckit.record_phase(conn, audit, item)
    (worktree / "specs" / "007-x" / "plan.md").unlink()

    assert speckit.record_phase(conn, audit, db.get_work_item(conn, item.id)) is None
    assert db.get_work_item(conn, item.id).speckit_phase == "plan"


def test_advancing_writes_a_second_record_naming_both_rungs(conn, audit, layout, tmp_path):
    worktree = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    item = seed(conn, audit, worktree, baseline="[]")
    speckit.record_phase(conn, audit, item)
    (worktree / "specs" / "007-x" / "plan.md").write_text("# plan\n", encoding="utf-8")

    speckit.record_phase(conn, audit, db.get_work_item(conn, item.id))

    records = phase_records(layout)
    assert len(records) == 2
    assert records[-1]["detail"] == {
        "from": "specify",
        "to": "plan",
        "feature_dir": "specs/007-x",
    }


def test_a_new_feature_directory_is_recorded_with_both_names(conn, audit, layout, tmp_path):
    """Rule 3. It may look like a step backwards, and the record is what stops it reading
    as a bug."""
    worktree = make_speckit_tree(
        tmp_path / "wt", features={"007-x": ["spec.md", "plan.md"]}
    )
    item = seed(conn, audit, worktree, baseline="[]")
    speckit.record_phase(conn, audit, item)

    (worktree / "specs" / "008-y").mkdir()
    (worktree / "specs" / "008-y" / "spec.md").write_text("# spec\n", encoding="utf-8")
    # Only the new directory can win: the old one is already at a higher rung, so this
    # asserts the directory change rather than the tie-break.
    (worktree / "specs" / "007-x" / "plan.md").unlink()
    (worktree / "specs" / "007-x" / "spec.md").unlink()

    speckit.record_phase(conn, audit, db.get_work_item(conn, item.id))

    detail = phase_records(layout)[-1]["detail"]
    assert detail["to"] == "specify"
    assert detail["feature_dir"] == "specs/008-y"
    assert detail["previous_feature_dir"] == "specs/007-x"


def test_a_removed_worktree_leaves_the_recorded_phase_standing(conn, audit, layout, tmp_path):
    """Rule 4, and the reason it exists: milestone 004's cleanup removes worktrees under
    items that still exist, and the log has no way to restore what clearing would delete."""
    import shutil

    worktree = make_speckit_tree(
        tmp_path / "wt", features={"007-x": ["spec.md", "plan.md", "tasks-done.md"]}
    )
    item = seed(conn, audit, worktree, baseline="[]")
    speckit.record_phase(conn, audit, item)
    shutil.rmtree(worktree)

    assert speckit.record_phase(conn, audit, db.get_work_item(conn, item.id)) is None
    assert db.get_work_item(conn, item.id).speckit_phase == "implement"


def test_a_non_speckit_worktree_is_not_observed(conn, audit, layout, tmp_path) -> None:
    """Detection gates observation: ``specs/`` is not a rare enough directory name to mean
    Spec Kit on its own."""
    worktree = make_speckit_tree(
        tmp_path / "wt",
        scaffolding=False,
        commands=None,
        features={"007-x": ["spec.md"]},
    )
    item = seed(conn, audit, worktree, baseline="[]")

    assert speckit.record_phase(conn, audit, item) is None
    assert phase_records(layout) == []


def test_an_unparseable_baseline_is_treated_as_absent(conn, audit, layout, tmp_path) -> None:
    worktree = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    item = seed(conn, audit, worktree, baseline="{not json")

    assert speckit.record_phase(conn, audit, item) is None
    assert phase_records(layout) == []
