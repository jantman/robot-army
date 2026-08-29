"""The ladder, including everything it has to survive reading.

``tasks.md`` is someone else's markdown, which the constitution's Development Workflow rule
treats as external input: the failure paths get tests, not only the happy one. The rung that
carries the most weight is ``implement``, because it is the only one not evidenced by a file
appearing — ``/speckit-implement`` writes no new file, it ticks tasks off.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_speckit_tree

from robot_army import speckit


def observe(root: Path, baseline: tuple[str, ...] = ()) -> speckit.Phase | None:
    return speckit.observe(root, baseline=baseline)


def test_a_spec_alone_is_the_specify_rung(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})

    phase = observe(root)

    assert phase == speckit.Phase(rung="specify", feature_dir="specs/007-x")


def test_a_plan_raises_it_to_plan(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md", "plan.md"]})

    assert observe(root).rung == "plan"


def test_a_tasks_file_raises_it_to_tasks(tmp_path: Path) -> None:
    root = make_speckit_tree(
        tmp_path / "wt", features={"007-x": ["spec.md", "plan.md", "tasks.md"]}
    )

    assert observe(root).rung == "tasks"


def test_a_ticked_task_raises_it_to_implement(tmp_path: Path) -> None:
    root = make_speckit_tree(
        tmp_path / "wt", features={"007-x": ["spec.md", "plan.md", "tasks-done.md"]}
    )

    assert observe(root).rung == "implement"


def test_only_unticked_boxes_stay_at_tasks(tmp_path: Path) -> None:
    """A tasks.md fresh from /speckit-tasks is all unticked; that is not implementation."""
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["tasks.md"]})

    assert observe(root).rung == "tasks"


def test_a_lowercase_x_counts(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": []})
    (root / "specs" / "007-x" / "tasks.md").write_text("- [x] T001 done\n", encoding="utf-8")

    assert observe(root).rung == "implement"


def test_a_higher_rung_wins_even_with_lower_artifacts_missing(tmp_path: Path) -> None:
    """The ladder is highest-wins, not a sequence that has to be complete."""
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["tasks-done.md"]})

    assert observe(root).rung == "implement"


def test_an_empty_feature_directory_yields_nothing(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": []})

    assert observe(root) is None


def test_an_unreadable_tasks_file_still_counts_as_tasks(tmp_path: Path) -> None:
    """Its existence is evidence; its contents merely fail to prove more."""
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    (root / "specs" / "007-x" / "tasks.md").write_bytes(b"\xff\xfe not utf-8 \x00")

    assert observe(root).rung == "tasks"


def test_no_specs_directory_yields_nothing(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt")

    assert observe(root) is None


def test_a_specs_path_that_is_a_file_yields_nothing(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt")
    (root / "specs").write_text("not a directory", encoding="utf-8")

    assert observe(root) is None


def test_a_missing_worktree_yields_nothing(tmp_path: Path) -> None:
    """The cleanup case: absence must be silence, never an exception."""
    assert speckit.observe(tmp_path / "gone", baseline=()) is None


def test_loose_files_under_specs_are_ignored(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features={"007-x": ["spec.md"]})
    (root / "specs" / "README.md").write_text("# specs\n", encoding="utf-8")

    assert observe(root).feature_dir == "specs/007-x"
