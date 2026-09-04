"""The whole path, against a real git repository that really uses Spec Kit.

Real git rather than a fixture directory, because the trap this milestone exists for is
produced by ``git worktree add`` and nothing else: a fresh checkout of a repository that has
shipped six features contains all six, spec, plan and ticked-off tasks included. The unit
tests assert the rule; this asserts that the rule meets the thing it was written about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import make_boundaries, make_repo

from robot_army import db, reconcile, speckit, worktree
from robot_army.boundaries.hooks import SubprocessHookRunner
from robot_army.config import RepoConfig
from robot_army.states import WorkItemState, transition_work_item

pytestmark = pytest.mark.requires_git

#: A repository that uses Spec Kit and has shipped six features. Every one of them has a
#: ``tasks.md`` full of ticked boxes, which is what would make naive observation report
#: ``implement`` the instant a worktree existed.
def speckit_files() -> dict[str, str]:
    files = {
        ".specify/templates/spec-template.md": "# Feature Specification: [FEATURE NAME]\n",
        ".specify/memory/constitution.md": "# Constitution\n",
    }
    for name in ("specify", "plan", "tasks", "implement"):
        files[f".claude/skills/speckit-{name}/SKILL.md"] = f"# speckit-{name}\n"
    for n in range(1, 7):
        stem = f"specs/{n:03d}-shipped"
        files[f"{stem}/spec.md"] = "# spec\n"
        files[f"{stem}/plan.md"] = "# plan\n"
        files[f"{stem}/tasks.md"] = "- [X] T001 done\n"
    return files


def phase_records(layout) -> list[dict]:
    out = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("action") == "speckit.phase":
                out.append(json.loads(line))
    return out


def prepare_item(conn, audit, config, clone: Path):
    """Prepare a real worktree and put an ``active`` item in front of it."""
    boundaries = make_boundaries(audit, hooks=SubprocessHookRunner(audit))
    result = worktree.prepare(
        boundaries=boundaries,
        audit=audit,
        config=config,
        repo=RepoConfig(key="demo", path=clone, base_branch="main"),
        item_id=1,
        issue_number=42,
        title="Add a thing",
        dry_run=False,
    )
    assert result.ok, result.failure_reason

    with db.transaction(conn):
        db.upsert_repo(
            conn,
            repo_key="demo",
            settings_fingerprint={},
            trust_verified=True,
            clone_path=str(clone),
        )
        item_id = db.insert_work_item(
            conn,
            source="github",
            source_id="demo#42",
            source_url="https://github.example/42",
            repo_key="demo",
            issue_number=42,
            title="Add a thing",
            body="",
            labels="[]",
            author="jantman",
            dry_run=False,
        )
        db.update_work_item_columns(
            conn,
            item_id,
            worktree_path=result.worktree_path,
            branch=result.branch,
            speckit_baseline=json.dumps(list(result.speckit_baseline or ())),
        )
        for target in (
            WorkItemState.READY,
            WorkItemState.DISPATCHING,
            WorkItemState.ACTIVE,
        ):
            transition_work_item(
                conn, audit, item_id=item_id, target=target, reason="test"
            )
    return item_id, Path(result.worktree_path)


def observe(conn, audit) -> int:
    return reconcile._observe_speckit(conn, audit=audit)


def test_a_speckit_run_is_followed_from_specify_to_plan(
    conn, audit, config, layout, tmp_path
):
    clone = make_repo(tmp_path / "clones" / "demo", files=speckit_files())
    item_id, path = prepare_item(conn, audit, config, clone)

    # The baseline is what the checkout carried, and the six finished features are in it.
    baseline = json.loads(db.get_work_item(conn, item_id).speckit_baseline)
    assert baseline == [f"{n:03d}-shipped" for n in range(1, 7)]

    # Nothing has happened yet, and six finished features must not read as progress.
    assert observe(conn, audit) == 0
    assert db.get_work_item(conn, item_id).speckit_phase is None
    assert phase_records(layout) == []

    # The session writes a spec.
    (path / "specs" / "007-new").mkdir(parents=True)
    (path / "specs" / "007-new" / "spec.md").write_text("# spec\n", encoding="utf-8")

    assert observe(conn, audit) == 1
    item = db.get_work_item(conn, item_id)
    assert item.speckit_phase == "specify"
    assert item.speckit_feature_dir == "specs/007-new"
    assert len(phase_records(layout)) == 1

    # A second pass with nothing changed writes nothing — one line per transition, which
    # is the Principle III omission this milestone claims.
    assert observe(conn, audit) == 0
    assert len(phase_records(layout)) == 1

    # And then a plan.
    (path / "specs" / "007-new" / "plan.md").write_text("# plan\n", encoding="utf-8")

    assert observe(conn, audit) == 1
    assert db.get_work_item(conn, item_id).speckit_phase == "plan"
    records = phase_records(layout)
    assert len(records) == 2
    assert records[-1]["detail"]["from"] == "specify"
    assert records[-1]["detail"]["to"] == "plan"


def test_a_plain_repository_gets_no_baseline_and_no_phase(
    conn, audit, config, layout, repo_clone
):
    """A repository with no Spec Kit is untouched by all of this."""
    item_id, path = prepare_item(conn, audit, config, repo_clone)

    assert json.loads(db.get_work_item(conn, item_id).speckit_baseline) == []

    # Even a specs/ directory appearing is not enough: detection gates observation.
    (path / "specs" / "007-new").mkdir(parents=True)
    (path / "specs" / "007-new" / "spec.md").write_text("# spec\n", encoding="utf-8")

    assert observe(conn, audit) == 0
    assert db.get_work_item(conn, item_id).speckit_phase is None
    assert phase_records(layout) == []


def test_detection_answers_the_real_worktree(conn, audit, config, tmp_path):
    clone = make_repo(tmp_path / "clones" / "demo", files=speckit_files())
    _, path = prepare_item(conn, audit, config, clone)

    detection = speckit.detect(path)

    assert detection.detected is True
    assert detection.form == "skills"
