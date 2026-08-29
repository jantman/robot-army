"""The stale-artifact trap, and the baseline that closes it.

This is the file that earns the milestone. A fresh worktree of a repository that uses Spec
Kit carries every feature it has ever shipped — here, six directories each with a ticked-off
tasks.md. Without a baseline, every item would report ``implement`` the instant its worktree
existed, and the phase column would be worse than useless: confidently wrong on every row.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_speckit_tree

from robot_army import speckit

#: What a real checkout of this repository looks like: finished features, all the way down.
FINISHED = {
    f"{n:03d}-old-feature": ["spec.md", "plan.md", "tasks-done.md"] for n in range(1, 7)
}


def test_the_baseline_lists_what_was_there_at_creation(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features=FINISHED)

    assert speckit.baseline(root) == tuple(sorted(FINISHED))


def test_a_worktree_full_of_finished_features_reports_no_phase(tmp_path: Path) -> None:
    """The whole reason the baseline exists."""
    root = make_speckit_tree(tmp_path / "wt", features=FINISHED)
    base = speckit.baseline(root)

    assert speckit.observe(root, baseline=base) is None


def test_a_new_directory_after_the_baseline_is_this_item_s_work(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt", features=FINISHED)
    base = speckit.baseline(root)
    (root / "specs" / "007-new").mkdir()
    (root / "specs" / "007-new" / "spec.md").write_text("# spec\n", encoding="utf-8")

    phase = speckit.observe(root, baseline=base)

    assert phase == speckit.Phase(rung="specify", feature_dir="specs/007-new")


def test_work_inside_a_baseline_directory_is_not_attributed(tmp_path: Path) -> None:
    """Conservative and correct: nothing distinguishes that from the author's own earlier
    work in the same checkout, and claiming it would be a guess presented as a fact."""
    root = make_speckit_tree(tmp_path / "wt", features={"006-existing": ["spec.md"]})
    base = speckit.baseline(root)
    (root / "specs" / "006-existing" / "plan.md").write_text("# plan\n", encoding="utf-8")

    assert speckit.observe(root, baseline=base) is None


def test_an_empty_baseline_treats_every_directory_as_the_item_s(tmp_path: Path) -> None:
    """``()`` is a Spec Kit worktree with no features yet — not the same as no baseline."""
    root = make_speckit_tree(tmp_path / "wt")
    base = speckit.baseline(root)
    assert base == ()

    (root / "specs" / "001-first").mkdir(parents=True)
    (root / "specs" / "001-first" / "spec.md").write_text("# spec\n", encoding="utf-8")

    assert speckit.observe(root, baseline=base).feature_dir == "specs/001-first"


def test_a_baseline_of_a_worktree_with_no_specs_is_empty(tmp_path: Path) -> None:
    assert speckit.baseline(tmp_path / "nowhere") == ()


def test_two_new_directories_resolve_to_the_higher_rung(tmp_path: Path) -> None:
    """The 'two features in one worktree' case, resolved deterministically."""
    root = make_speckit_tree(tmp_path / "wt")
    base = speckit.baseline(root)
    make_speckit_tree(
        root,
        scaffolding=False,
        commands=None,
        features={"007-a": ["spec.md"], "008-b": ["spec.md", "plan.md"]},
    )

    phase = speckit.observe(root, baseline=base)

    assert phase == speckit.Phase(rung="plan", feature_dir="specs/008-b")


def test_the_same_two_directories_resolve_the_same_way_every_time(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "wt")
    base = speckit.baseline(root)
    make_speckit_tree(
        root,
        scaffolding=False,
        commands=None,
        features={"007-a": ["spec.md"], "008-b": ["spec.md"]},
    )

    first = speckit.observe(root, baseline=base)
    second = speckit.observe(root, baseline=base)

    assert first == second
